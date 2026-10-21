"""Krok 2: kdo je najemce? Tri vrstvy od nejlevnejsi:

1) ARES (zdarma, bez AI): predmet cinnosti (CZ-NACE) a pravni forma protistrany.
   Protistrana s NACE 68 (pronajem nemovitosti) = pronajimatel -> verejny subjekt je NAJEMCE.
2) Pravidla nad textem PDF: kde stoji ICO/nazev verejneho subjektu a ktery popisek
   ("najemce" / "pronajimatel") za nim nasleduje ("... IČO 00006947, (dále jen „nájemce“)").
3) Lokalni LLM (krok 3) jen tam, kde se 1) a 2) neshodnou nebo nic nenajdou.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import unicodedata
from pathlib import Path

ARES_URL = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/{ico}"
USER_AGENT = "NajemniRadar-demo/0.2 (open data research)"

# Pravni formy verejneho sektoru (ciselnik CSU, nejcastejsi)
VEREJNE_FORMY = {"301", "302", "312", "325", "331", "332", "352", "353", "361", "362", "381", "382",
                 "391", "601", "641", "661", "801", "804", "805", "811", "906"}
# Typicti NAJEMCI verejnych prostor (obchod, gastro, sport, zdravotnictvi, kultura, sluzby)
NACE_NAJEMCI = ("47", "56", "86", "90", "91", "93", "96", "85")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ares(ico TEXT PRIMARY KEY, nazev TEXT, pravni_forma TEXT, nace TEXT,
  adresa TEXT, http INTEGER, cas TEXT);
CREATE TABLE IF NOT EXISTS role(id_verze TEXT PRIMARY KEY, role_ares TEXT, duvod TEXT,
  protistrana_ico TEXT, protistrana_nazev TEXT);
CREATE TABLE IF NOT EXISTS texty(id_verze TEXT PRIMARY KEY, souboru INTEGER, stran INTEGER,
  znaku INTEGER, sken INTEGER, role_text TEXT, duvod TEXT, cesta TEXT, chyba TEXT);
"""


# ---------------------------------------------------------------- 1) ARES --
def parse_ares(data: dict) -> dict:
    nace = [str(x) for x in (data.get("czNace") or [])]
    s = data.get("sidlo") or {}
    return {"nazev": data.get("obchodniJmeno") or "", "pravni_forma": str(data.get("pravniForma") or ""),
            "nace": nace, "adresa": s.get("textovaAdresa") or ""}


def role_z_ares(protistrany: list[dict]) -> tuple[str, str]:
    """protistrany = [{'nazev','pravni_forma','nace'}] -> (role verejneho subjektu, duvod)."""
    if not protistrany:
        return "nejasne", "protistrana neni v ARES"
    duvody, hlasy = [], []
    for p in protistrany:
        nace = p.get("nace") or []
        realitni = any(n.startswith("68") for n in nace)
        najemni = any(n.startswith(NACE_NAJEMCI) for n in nace)
        verejna = p.get("pravni_forma") in VEREJNE_FORMY
        if verejna:
            hlasy.append("verejny_verejny")
            duvody.append(f"{p['nazev'][:40]}: verejna pravni forma {p.get('pravni_forma')}")
        elif realitni and (not najemni or len(nace) <= 6):
            hlasy.append("verejny_najemce")
            duvody.append(f"{p['nazev'][:40]}: NACE 68 (pronajem nemovitosti)")
        elif najemni and not realitni:
            hlasy.append("verejny_pronajimatel")
            duvody.append(f"{p['nazev'][:40]}: NACE {','.join(n for n in nace if n.startswith(NACE_NAJEMCI))[:30]}")
        else:
            hlasy.append("nejasne")
            duvody.append(f"{p['nazev'][:40]}: NACE neurcuje roli")
    for r in ("verejny_najemce", "verejny_pronajimatel", "verejny_verejny"):
        if r in hlasy:
            return r, "; ".join(duvody)
    return "nejasne", "; ".join(duvody)


def _client(timeout: float = 30):
    import httpx
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True)


def ares_fetch(con: sqlite3.Connection, icos: list[str], pauza: float = 0.15) -> None:
    hotove = {r[0] for r in con.execute("SELECT ico FROM ares")}
    todo = [i for i in dict.fromkeys(icos) if i and i not in hotove]
    print(f"ARES: {len(todo)} novych ICO (v mezipameti {len(hotove)}), odhad {len(todo)*pauza/60:.0f} min")
    with _client() as c:
        for n, ico in enumerate(todo, 1):
            for pokus in range(4):
                try:
                    r = c.get(ARES_URL.format(ico=ico.zfill(8)))
                except Exception as e:  # noqa: BLE001
                    print(f"  {ico}: {e}")
                    time.sleep(5 * (pokus + 1))
                    continue
                if r.status_code == 429:
                    time.sleep(30 * (pokus + 1))
                    continue
                break
            else:
                continue
            d = parse_ares(r.json()) if r.status_code == 200 else {"nazev": "", "pravni_forma": "", "nace": [], "adresa": ""}
            con.execute("INSERT OR REPLACE INTO ares VALUES(?,?,?,?,?,?,?)",
                        (ico, d["nazev"], d["pravni_forma"], json.dumps(d["nace"]), d["adresa"],
                         r.status_code, time.strftime("%Y-%m-%d %H:%M")))
            if n % 50 == 0:
                con.commit()
                print(f"  ARES {n}/{len(todo)}", end="\r")
            time.sleep(pauza)
    con.commit()
    print()


def vyber_smluv(con: sqlite3.Connection, praha: bool, od_roku: str | None) -> list[tuple]:
    w = ["typ IN ('smlouva','dodatek','ukonceni')"]
    if praha:
        w.append("praha=1")
    if od_roku:
        w.append(f"substr(datum_uzavreni,1,4) >= '{int(od_roku)}'")
    return con.execute(f"SELECT id_verze, strany_json FROM smlouvy WHERE {' AND '.join(w)}").fetchall()


def krok2_ares(con: sqlite3.Connection, praha: bool, od_roku: str | None, ano: bool) -> int:
    con.executescript(SCHEMA)
    rows = vyber_smluv(con, praha, od_roku)
    icos = [s.get("ico", "").strip() for _, sj in rows for s in json.loads(sj)]
    print(f"Smluv ve vyberu: {len(rows)}, unikatnich ICO protistran: {len(set(icos))}")
    if not ano and input("Spustit dotazy do ARES? [a/N] ").strip().lower() not in ("a", "ano", "y"):
        return 0
    ares_fetch(con, icos)
    cache = {r[0]: {"nazev": r[1], "pravni_forma": r[2], "nace": json.loads(r[3] or "[]")}
             for r in con.execute("SELECT ico, nazev, pravni_forma, nace FROM ares WHERE http=200")}
    for idv, sj in rows:
        strany = json.loads(sj)
        prot = [cache[s["ico"].strip()] for s in strany if s.get("ico", "").strip() in cache]
        role, duvod = role_z_ares(prot)
        hlavni = strany[0] if strany else {}
        con.execute("INSERT OR REPLACE INTO role VALUES(?,?,?,?,?)",
                    (idv, role, duvod, hlavni.get("ico", ""), hlavni.get("nazev", "")))
    con.commit()
    return souhrn2(con)


# ---------------------------------------------------- 2) PDF + pravidla --
def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"\s+", " ", "".join(c for c in s if not unicodedata.combining(c))).lower()


RE_LBL_PRONAJ = re.compile(r"\b(pod)?pronajimatel|\bpodnajimatel")
RE_LBL_NAJEMCE = re.compile(r"\b(pod)?najemc|\bpodnajemnik")


# Prisna pravidla (v0.3): jen jednoznacne formulace za ICO/nazvem subjektu.
#   "IČO: 00006947, ... (dále jen „nájemce“)"   /   "... jako pronajímatel"
_ROLE = r"(?P<role>(?:pod)?pronajimatel|(?:pod)?najemc[e]?|(?:pod)?najemnik)"
RE_DALE_JEN = re.compile(r"(?:dale\s+(?:jen|jako|take\s+jen|take)|jako)\W{0,6}" + _ROLE)


def _role_slovo(slovo: str) -> str:
    return "verejny_pronajimatel" if "pronajimatel" in slovo else "verejny_najemce"


def role_z_textu(text: str, ico: str, nazev: str, okno_znaku: int = 450) -> tuple[str, str]:
    """Najde vyskyt ICO (nebo nazvu) verejneho subjektu v uvodu smlouvy a PRVNI nasledujici
    jednoznacnou formulaci role ("dale jen najemce" / "jako pronajimatel"). Jinak 'nejasne'."""
    t = fold(text[:15000])  # smluvni strany jsou na zacatku
    pozice = []
    if ico:
        ico = ico.strip().zfill(8)
        pozice += [m.end() for m in re.finditer(r"\s?".join(ico), t)]
    if not pozice and nazev:
        slova = [w for w in fold(nazev).split() if len(w) > 3][:3]
        if slova:
            pozice += [m.end() for m in re.finditer(re.escape(" ".join(slova)), t)]
    if not pozice:
        return "nejasne", "subjekt v textu nenalezen (ICO ani nazev)"
    for p in pozice[:3]:
        okno = t[p:p + okno_znaku]
        m = RE_DALE_JEN.search(okno)
        if m:
            return _role_slovo(m.group("role")), f"'{okno[max(0, m.start()-30):m.end()+3]}'"
    return "nejasne", "za subjektem neni jednoznacna formulace role"


def _get_s_opakovanim(c, url: str, pokusu: int = 3):
    """Registr obcas vrati 500 nebo utne spojeni -> zkusit znovu s pauzou."""
    import httpx
    for k in range(pokusu):
        try:
            r = c.get(url)
            if r.status_code >= 500 and k < pokusu - 1:
                time.sleep(5 * (k + 1))
                continue
            r.raise_for_status()
            return r
        except httpx.TransportError:
            if k == pokusu - 1:
                raise
            time.sleep(5 * (k + 1))
    r.raise_for_status()
    return r


def docx_text(path: Path) -> tuple[str, int]:
    """Text z Wordu (.docx) bez dalsich knihoven: word/document.xml, odstavce oddelene radky.
    Pocet 'stran' se odhaduje z delky textu (~2500 znaku/strana)."""
    import zipfile
    from xml.etree import ElementTree as ET
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    odst = ["".join(t.text or "" for t in p.iter(W + "t")) for p in root.iter(W + "p")]
    text = "\n".join(o for o in odst if o.strip())
    return text, max(1, len(text) // 2500)


def pdf_text(path: Path) -> tuple[str, int]:
    import pymupdf
    with pymupdf.open(path) as doc:
        return "\n".join(p.get_text() for p in doc), doc.page_count


def zpracuj_pdf(c, con: sqlite3.Connection, row: tuple, pdf_dir: Path, txt_dir: Path,
                pauza: float = 1.0) -> tuple:
    """Stahne PDF prilohy jedne smlouvy, vytahne text, urci roli pravidlem a ulozi do 'texty'."""
    idv, pj, ico, nazev = row
    texty, stran, chyba = [], 0, ""
    prilohy = [p for p in json.loads(pj) if p.get("odkaz")]
    for i, p in enumerate(prilohy[:4]):
        nazev_s = p.get("nazev", "").lower()
        ext = "docx" if nazev_s.endswith(".docx") else "pdf" if (nazev_s.endswith(".pdf") or not nazev_s) else None
        if ext is None:  # .doc (stary Word), obrazky... zatim ne
            continue
        cil = pdf_dir / f"{idv}_{i}.{ext}"
        try:
            if not cil.exists():
                r = _get_s_opakovanim(c, p["odkaz"])
                cil.write_bytes(r.content)
                time.sleep(pauza)
            t, ps = docx_text(cil) if ext == "docx" else pdf_text(cil)
            texty.append(t)
            stran += ps
        except Exception as e:  # noqa: BLE001
            chyba = str(e)[:200]
    text = "\n\n".join(texty)
    znaku = len(text.strip())
    sken = int(stran > 0 and znaku / stran < 200)
    rt, duvod = role_z_textu(text, ico, nazev) if not sken else ("nejasne", "sken bez textu")
    cesta = txt_dir / f"{idv}.txt"
    cesta.write_text(text, encoding="utf-8")
    con.execute("INSERT OR REPLACE INTO texty VALUES(?,?,?,?,?,?,?,?,?)",
                (idv, len(texty), stran, znaku, sken, rt, duvod, str(cesta), chyba))
    con.commit()
    return idv, stran, sken, rt


def prepocitej_role_textu(con: sqlite3.Connection) -> int:
    """Znovu urci roli z uz stazenych textu (po zmene pravidel), bez stahovani."""
    n = 0
    for idv, cesta, ico, nazev in con.execute(
            "SELECT t.id_verze, t.cesta, s.subjekt_ico, s.subjekt_nazev FROM texty t "
            "JOIN smlouvy s USING(id_verze) WHERE t.sken=0").fetchall():
        try:
            text = Path(cesta).read_text(encoding="utf-8")
        except OSError:
            continue
        rt, d = role_z_textu(text, ico, nazev)
        con.execute("UPDATE texty SET role_text=?, duvod=? WHERE id_verze=?", (rt, d, idv))
        n += 1
    con.commit()
    return n


def krok2_pdf(con: sqlite3.Connection, data_dir: Path, limit: int, role: str, praha: bool,
              min_hodnota: float, pauza: float = 1.0) -> int:
    con.executescript(SCHEMA)
    w = ["r.role_ares = ?", "s.pocet_priloh > 0", "t.id_verze IS NULL"]
    args: list = [role]
    if praha:
        w.append("s.praha = 1")
    if min_hodnota:
        w.append("COALESCE(s.hodnota_bez_dph, s.hodnota_s_dph, 0) >= ?")
        args.append(min_hodnota)
    rows = con.execute(
        f"""SELECT s.id_verze, s.prilohy_json, s.subjekt_ico, s.subjekt_nazev
            FROM smlouvy s JOIN role r USING(id_verze) LEFT JOIN texty t USING(id_verze)
            WHERE {' AND '.join(w)}
            ORDER BY COALESCE(s.hodnota_bez_dph, s.hodnota_s_dph, 0) DESC LIMIT ?""",
        (*args, limit)).fetchall()
    print(f"PDF ke zpracovani: {len(rows)} smluv (role={role}, praha={praha}, min. hodnota={min_hodnota:,.0f})")
    pdf_dir, txt_dir = data_dir / "pdf", data_dir / "text"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    txt_dir.mkdir(parents=True, exist_ok=True)
    with _client(60) as c:
        for n, row in enumerate(rows, 1):
            idv, stran, sken, rt = zpracuj_pdf(c, con, row, pdf_dir, txt_dir, pauza)
            print(f"  [{n}/{len(rows)}] {idv}: {stran} str., {'SKEN' if sken else 'text'}, {rt}   ", end="\r")
    print()
    return souhrn2(con)


# --------------------------------------------------------------- souhrn --
def souhrn2(con: sqlite3.Connection) -> int:
    con.executescript(SCHEMA)
    print("\n================ SOUHRN KROK 2 (posli mi tuto cast) ================")
    print("Role podle ARES:", dict(con.execute("SELECT role_ares, COUNT(*) FROM role GROUP BY role_ares")))
    print("  ...z toho Praha:", dict(con.execute(
        "SELECT r.role_ares, COUNT(*) FROM role r JOIN smlouvy s USING(id_verze) WHERE s.praha=1 GROUP BY 1")))
    print("ARES dotazu:", dict(con.execute("SELECT http, COUNT(*) FROM ares GROUP BY http")))
    print("\n-- nejcastejsi PRONAJIMATELE verejneho sektoru (protistrana s NACE 68) --")
    for naz, n, h in con.execute(
            """SELECT r.protistrana_nazev, COUNT(*), SUM(COALESCE(s.hodnota_bez_dph, s.hodnota_s_dph, 0))
               FROM role r JOIN smlouvy s USING(id_verze) WHERE r.role_ares='verejny_najemce'
               GROUP BY r.protistrana_ico ORDER BY 3 DESC LIMIT 15"""):
        print(f"  {n:4d} smluv | {h:>15,.0f} Kc | {naz[:60]}")
    print("\n-- 15 nahodnych: verejny subjekt = NAJEMCE (podle ARES) --")
    for sn, p, d in con.execute(
            """SELECT s.subjekt_nazev, s.predmet, r.duvod FROM role r JOIN smlouvy s USING(id_verze)
               WHERE r.role_ares='verejny_najemce' ORDER BY RANDOM() LIMIT 15"""):
        print(f"  - {sn[:35]} | {p[:70]} | {d[:60]}".replace("\n", " "))
    n_t = con.execute("SELECT COUNT(*) FROM texty").fetchone()[0]
    if n_t:
        skenu = con.execute("SELECT SUM(sken) FROM texty").fetchone()[0]
        chyb = con.execute("SELECT COUNT(*) FROM texty WHERE chyba<>''").fetchone()[0]
        print(f"\nPDF zpracovano: {n_t}, skenu: {skenu}, chyb: {chyb}")
        print("Shoda ARES vs. text:")
        for ra, rt, n in con.execute(
                """SELECT r.role_ares, t.role_text, COUNT(*) FROM texty t JOIN role r USING(id_verze)
                   GROUP BY 1, 2 ORDER BY 3 DESC"""):
            print(f"  ARES={ra:22s} text={rt:22s} {n}")
        print("\n-- 8 ukazek role z textu (ke kontrole) --")
        for idv, rt, d in con.execute("SELECT id_verze, role_text, duvod FROM texty ORDER BY RANDOM() LIMIT 8"):
            print(f"  {idv}: {rt} | {d[:120]}")
    print("====================================================================")
    return 0
