"""Krok 2b: uzky vyber (shortlist) kancelarskych najmu, kde verejny subjekt PLATI.

Poucka z mereni v0.2: NACE 68 u protistrany ma presnost ~54 % (kavarny a restaurace maji 68 v zivnosti).
Hlavni signal je proto KDO SMLOUVU ZVEREJNIL:
  - ministerstva, urady, agentury, fondy, pojistovny, soudy  -> kancelare si vetsinou NAJIMAJI
  - obce, mestske casti, skoly, divadla, dopravni podnik, posta -> prostory vetsinou PRONAJIMAJI
Skore je transparentni soucet duvodu (kazdy duvod se uklada -> lze vysvetlit na pohovoru).
"""
from __future__ import annotations

import json
import re
import sqlite3

from .krok2 import NACE_NAJEMCI, SCHEMA as SCHEMA2, ares_fetch, fold

SCHEMA = """
CREATE TABLE IF NOT EXISTS shortlist(id_verze TEXT PRIMARY KEY, skore INTEGER, duvody TEXT);
"""

# --- zverejnovatel (subjekt) ---
RE_SUBJ_NAJEMCE = re.compile(
    r"ministerstv|urad\b|uradu\b|agentur|fond\b|fondu\b|inspekc|pojistov|soud\b|soudu\b|zastupitelstv|"
    r"ceska republika|institut|centrum pro regionalni|sprava uprchlick|komise|rada pro|"
    r"cesky statisticky|katastr|celni|financni|policie|hasicsk|archiv|knihovna|akademie ved|"
    r"technologicka agentura|grantova agentura|operator ict|narodni agentura")
RE_SUBJ_PRONAJ = re.compile(
    r"mestska cast|hlavni mesto praha|^mesto |^obec |statutarni mesto|kraj$|krajsky urad|"
    r"zakladni skol|materska skol|gymnazi|stredni|skola\b|divadl|muze|galeri|zoologick|"
    r"dopravni podnik|ceska posta|sprava zeleznic|technicka sprava|nemocnic|poliklinik|"
    r"sprava majetku|trade centre praha|kulturni|sportovni|lesy ceske|povodi|vystaviste")
FORMY_NAJEMCE = {"325", "361", "362", "381", "382", "661", "352", "353"}  # OSS, verejnopr. instituce, fondy, v.v.i.
FORMY_PRONAJ = {"801", "804", "811"}                                       # obec, kraj, mestska cast

# --- predmet ---
RE_KANCELAR = re.compile(r"kancelar|administrativ|office|pro potreby|pro ucely (uradu|pracoviste)|sidl")
RE_SUM = re.compile(
    r"koncert|predstaven|workshop|catering|strech|kiosk|kvetin|kavarn|restaur|bufet|automat|reklam|"
    r"\bwc\b|bankomat|\batm\b|telocvic|sportov|hal[auy]\b|ucebn|posudk|rekonstrukc|uklid|"
    r"parkovac|garaz|sklad|depo|antena|vysilac|kolektor|jidelna|stravov|lekarn|ordinac")


def skoruj(predmet: str, subjekt_nazev: str, subjekt_forma: str, hodnota: float | None,
           protistrany_nace: list[list[str]]) -> tuple[int, list[str]]:
    p, sn = fold(predmet), fold(subjekt_nazev)
    s, d = 0, []

    def add(b: int, why: str):
        nonlocal s
        s += b
        d.append(f"{'+' if b > 0 else ''}{b} {why}")

    if RE_SUBJ_NAJEMCE.search(sn):
        add(3, "zverejnovatel typicky najemce (urad/ministerstvo/fond...)")
    if RE_SUBJ_PRONAJ.search(sn):
        add(-3, "zverejnovatel typicky pronajimatel (obec/MC/skola/podnik...)")
    if subjekt_forma in FORMY_NAJEMCE:
        add(2, f"pravni forma {subjekt_forma} (stat. slozka/instituce)")
    if subjekt_forma in FORMY_PRONAJ:
        add(-2, f"pravni forma {subjekt_forma} (obec/kraj/MC)")
    if RE_KANCELAR.search(p):
        add(2, "predmet: kancelare/administrativa")
    if RE_SUM.search(p):
        add(-3, "predmet: akce/gastro/technika/sklad...")
    if hodnota and hodnota >= 500_000:
        add(1, "hodnota >= 0,5 mil. Kc")
    if hodnota and hodnota >= 5_000_000:
        add(1, "hodnota >= 5 mil. Kc")
    for nace in protistrany_nace:
        if any(n.startswith("68") for n in nace) and not any(n.startswith(NACE_NAJEMCI) for n in nace):
            add(1, "protistrana: ciste realitni NACE 68")
        elif any(n.startswith(("56", "47")) for n in nace):
            add(-1, "protistrana: gastro/obchod")
    return s, d


def vyber(con: sqlite3.Connection, limit: int, min_skore: int, praha: bool, ano: bool) -> int:
    con.executescript(SCHEMA2)
    con.executescript(SCHEMA)
    w = "typ IN ('smlouva','dodatek','ukonceni')" + (" AND praha=1" if praha else "")
    rows = con.execute(f"""SELECT id_verze, predmet, subjekt_nazev, subjekt_ico,
                           COALESCE(hodnota_bez_dph, hodnota_s_dph), strany_json FROM smlouvy WHERE {w}""").fetchall()
    subj_icos = sorted({r[3].strip() for r in rows if r[3]})
    print(f"Smluv: {len(rows)}, zverejnovatelu (ICO) pro ARES: {len(subj_icos)}")
    if ano or input("Doplnit pravni formu zverejnovatelu z ARES? [a/N] ").strip().lower() in ("a", "ano", "y"):
        ares_fetch(con, subj_icos)
    ares = {r[0]: (r[1], json.loads(r[2] or "[]")) for r in
            con.execute("SELECT ico, pravni_forma, nace FROM ares WHERE http=200")}
    con.execute("DELETE FROM shortlist")
    vysl = []
    for idv, predmet, sn, sico, hod, sj in rows:
        forma = ares.get((sico or "").strip(), ("", []))[0]
        nace = [ares[s["ico"].strip()][1] for s in json.loads(sj) if s.get("ico", "").strip() in ares]
        sk, d = skoruj(predmet or "", sn or "", forma, hod, nace)
        if sk >= min_skore:
            vysl.append((sk, hod or 0, idv, d))
    vysl.sort(key=lambda x: (-x[0], -x[1]))
    for sk, _, idv, d in vysl[:limit]:
        con.execute("INSERT INTO shortlist VALUES(?,?,?)", (idv, sk, json.dumps(d, ensure_ascii=False)))
    con.commit()
    print(f"Shortlist: {min(limit, len(vysl))} smluv (ze {len(vysl)} se skore >= {min_skore})")
    print("\n-- top 20 shortlistu --")
    for sk, sn, p, h in con.execute(
            """SELECT l.skore, s.subjekt_nazev, s.predmet, COALESCE(s.hodnota_bez_dph, s.hodnota_s_dph)
               FROM shortlist l JOIN smlouvy s USING(id_verze) ORDER BY l.skore DESC,
               COALESCE(s.hodnota_bez_dph, s.hodnota_s_dph, 0) DESC LIMIT 20"""):
        print(f"  {sk:2d} | {(h or 0):>13,.0f} Kc | {sn[:32]} | {p[:60]}".replace("\n", " "))
    return 0


def pdf_shortlist(con: sqlite3.Connection, data_dir, limit: int, pauza: float = 1.0) -> int:
    """Stahne PDF a urci roli pravidlem pro smlouvy ze shortlistu (jen dosud nezpracovane)."""
    from .krok2 import _client, zpracuj_pdf
    rows = con.execute(
        """SELECT s.id_verze, s.prilohy_json, s.subjekt_ico, s.subjekt_nazev
           FROM shortlist l JOIN smlouvy s USING(id_verze) LEFT JOIN texty t USING(id_verze)
           WHERE s.pocet_priloh > 0 AND t.id_verze IS NULL
           ORDER BY l.skore DESC, COALESCE(s.hodnota_bez_dph, s.hodnota_s_dph, 0) DESC LIMIT ?""",
        (limit,)).fetchall()
    print(f"PDF ke stazeni (shortlist): {len(rows)}  (~{len(rows)*2/60:.0f} min)")
    pdf_dir, txt_dir = data_dir / "pdf", data_dir / "text"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    txt_dir.mkdir(parents=True, exist_ok=True)
    with _client(60) as c:
        for n, row in enumerate(rows, 1):
            idv, stran, sken, rt = zpracuj_pdf(c, con, row, pdf_dir, txt_dir, pauza)
            print(f"  [{n}/{len(rows)}] {idv}: {stran} str., {'SKEN' if sken else 'text'}, {rt}       ", end="\r")
    print()
    print("Role z pravidel (shortlist):", dict(con.execute(
        "SELECT role_text, COUNT(*) FROM shortlist JOIN texty USING(id_verze) GROUP BY 1")))
    return 0


def znovu_bez_textu(con: sqlite3.Connection) -> int:
    """Smaze zaznamy 'texty' u smluv ze shortlistu bez pouzitelneho textu (chyba serveru, Word),
    aby je 'krok2b pdf' zkusil znovu (v0.4: umi .docx a opakuje pri chybe 500)."""
    n = con.execute("""DELETE FROM texty WHERE id_verze IN (SELECT id_verze FROM shortlist)
                       AND NOT (sken=0 AND znaku>300)""").rowcount
    con.commit()
    return n
