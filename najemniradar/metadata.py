"""Krok 5 (faze A dema v4): metadata XML z Registru smluv jako kostra dat.

Co dela:
  1) vybere dokumenty: shortlist + souvisejici smlouvy (stejny zverejnovatel + spolecna protistrana)
  2) pro kazdy stahne metadata XML (smlouvy.gov.cz/smlouva/<ID>/xml/registr_smluv_smlouva_<ID>.xml)
     -> navazanyZaznam (vazba dodatku na puvodni smlouvu), cisloSmlouvy, hodnota, prilohy + otisk (hash)
     -> XML obsahuje i samotne prilohy (base64) -> ulozi se, bez dalsiho stahovani
  3) dotahne puvodni smlouvy, na ktere dodatky odkazuji (pokud jsou v nasi databazi)
  4) precte text vsech dokumentu S CISLY STRAN ("[[STRANA n]]") pro citace "str. X"
Stahovani: 2 vlakna, pauza, opakovani; uz stazene se preskakuje (lze kdykoli prerusit a spustit znovu).
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

XML_URL = "https://smlouvy.gov.cz/smlouva/{id}/xml/registr_smluv_smlouva_{id}.xml"
USER_AGENT = "NajemniRadar-demo/0.5 (open data research)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(id_verze TEXT PRIMARY KEY, id_smlouvy TEXT, navazany TEXT, cislo_smlouvy TEXT,
  hodnota REAL, datum_uzavreni TEXT, predmet TEXT, subjekt_ico TEXT, strany_json TEXT, prilohy_json TEXT,
  http INTEGER, chyba TEXT, cas TEXT);
CREATE TABLE IF NOT EXISTS text_str(id_verze TEXT PRIMARY KEY, cesta TEXT, souboru INTEGER, stran INTEGER,
  znaku INTEGER, sken INTEGER);
"""


def _ln(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(el, *path):
    for name in path:
        if el is None:
            return None
        el = next((c for c in el if _ln(c.tag) == name), None)
    return el


def _t(el, *path) -> str:
    e = _find(el, *path)
    return (e.text or "").strip() if e is not None else ""


def parse_meta_xml(xml_bytes: bytes) -> tuple[dict, list[tuple[str, bytes, str]]]:
    """-> (metadata, [(nazev_souboru, obsah, hash)])"""
    root = ET.fromstring(xml_bytes)
    data = _find(root, "data")
    if data is None:
        data = root
    sml = _find(data, "smlouva")
    strany = []
    for s in (c for c in (sml if sml is not None else []) if _ln(c.tag) == "smluvniStrana"):
        strany.append({"nazev": _t(s, "nazev"), "ico": _t(s, "ico"), "adresa": _t(s, "adresa")})
    prilohy, soubory = [], []
    pr = _find(data, "prilohy")
    for p in (c for c in (pr if pr is not None else []) if _ln(c.tag) == "priloha"):
        nazev = _t(p, "nazevSouboru")
        h = _t(p, "hash")
        prilohy.append({"nazev": nazev, "hash": h})
        b64 = _t(p, "data")
        if b64:
            try:
                soubory.append((nazev, base64.b64decode(b64), h))
            except ValueError:
                pass
    hod = _t(sml, "hodnotaBezDph") or _t(sml, "hodnotaVcetneDph")
    meta = {
        "id_smlouvy": _t(data, "identifikator", "idSmlouvy"), "id_verze": _t(data, "identifikator", "idVerze"),
        "navazany": _t(sml, "navazanyZaznam"), "cislo_smlouvy": _t(sml, "cisloSmlouvy"),
        "hodnota": float(hod.replace(",", ".")) if re.fullmatch(r"-?\d+([.,]\d+)?", hod or "") else None,
        "datum_uzavreni": _t(sml, "datumUzavreni"), "predmet": _t(sml, "predmet"),
        "subjekt_ico": _t(sml, "subjekt", "ico"), "strany": strany, "prilohy": prilohy,
    }
    return meta, soubory


def vyber_dokumentu(con: sqlite3.Connection) -> list[str]:
    """Shortlist + souvisejici smlouvy (stejny zverejnovatel + aspon jedna spolecna protistrana)."""
    sl = {r[0] for r in con.execute("SELECT id_verze FROM shortlist")}
    rows = con.execute("SELECT id_verze, subjekt_ico, strany_json FROM smlouvy").fetchall()
    info = {i: (s, {x.get("ico", "").strip() for x in json.loads(sj or "[]") if x.get("ico")}) for i, s, sj in rows}
    klice = {(info[i][0], ic) for i in sl if i in info for ic in info[i][1]}
    rel = {i for i, (s, ii) in info.items() if any((s, ic) in klice for ic in ii)}
    return sorted(sl | rel, key=lambda x: int(x) if x.isdigit() else 0)


def _client():
    import httpx
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True)


_lock = threading.Lock()


def _stahni_meta(c, idv: str, pauza: float) -> tuple[str, int, bytes | None, str]:
    import httpx
    err = "opakovane selhani"
    for k in range(4):
        try:
            r = c.get(XML_URL.format(id=idv))
            if r.status_code >= 500 or r.status_code == 429:
                time.sleep(5 * (k + 1))
                continue
            time.sleep(pauza)
            return idv, r.status_code, (r.content if r.status_code == 200 else None), ""
        except httpx.TransportError as e:
            err = str(e)[:150]
            time.sleep(5 * (k + 1))
    return idv, 0, None, err


def uloz_meta(con: sqlite3.Connection, idv: str, http: int, xml: bytes | None, chyba: str, doc_dir: Path) -> dict | None:
    meta = None
    if xml:
        try:
            meta, soubory = parse_meta_xml(xml)
        except ET.ParseError as e:
            chyba = f"XML: {e}"
        else:
            for i, (nazev, obsah, h) in enumerate(soubory):
                ext = nazev.rsplit(".", 1)[-1].lower() if "." in nazev else "bin"
                cil = doc_dir / f"{idv}_{i}.{ext}"
                if not cil.exists():
                    cil.write_bytes(obsah)
    con.execute("INSERT OR REPLACE INTO meta VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        idv, (meta or {}).get("id_smlouvy"), (meta or {}).get("navazany"), (meta or {}).get("cislo_smlouvy"),
        (meta or {}).get("hodnota"), (meta or {}).get("datum_uzavreni"), (meta or {}).get("predmet"),
        (meta or {}).get("subjekt_ico"), json.dumps((meta or {}).get("strany", []), ensure_ascii=False),
        json.dumps((meta or {}).get("prilohy", []), ensure_ascii=False), http, chyba, time.strftime("%Y-%m-%d %H:%M")))
    return meta


def text_se_stranami(paths: list[Path]) -> tuple[str, int]:
    """Text vsech priloh s oddelovaci stran '[[STRANA n]]' (cislovani pres vsechny prilohy)."""
    casti, n = [], 0
    for p in paths:
        ext = p.suffix.lower()
        try:
            if ext == ".pdf":
                import pymupdf
                with pymupdf.open(p) as doc:
                    for page in doc:
                        n += 1
                        casti.append(f"\n[[STRANA {n}]]\n{page.get_text()}")
            elif ext == ".docx":
                from .krok2 import docx_text
                t, _ = docx_text(p)
                n += 1
                casti.append(f"\n[[STRANA {n}]]\n{t}")
        except Exception:  # noqa: BLE001 - poskozeny soubor nesmi shodit davku
            continue
    return "".join(casti), n


def krok5(con: sqlite3.Connection, data_dir: Path, vlaken: int = 2, pauza: float = 0.5) -> int:
    con.executescript(SCHEMA)
    doc_dir, txt_dir = data_dir / "doc", data_dir / "text_str"
    doc_dir.mkdir(parents=True, exist_ok=True)
    txt_dir.mkdir(parents=True, exist_ok=True)
    hotove = {r[0] for r in con.execute("SELECT id_verze FROM meta WHERE http=200")}
    ids = vyber_dokumentu(con)
    todo = [i for i in ids if i not in hotove]
    print(f"Dokumentu: {len(ids)} (shortlist + souvisejici), ke stazeni metadat: {len(todo)}, "
          f"odhad {len(todo) * (pauza + 0.8) / vlaken / 60:.0f} min")

    def davka(seznam: list[str], popis: str):
        n = 0
        with _client() as c, ThreadPoolExecutor(vlaken) as ex:
            futs = [ex.submit(_stahni_meta, c, i, pauza) for i in seznam]
            for f in as_completed(futs):
                idv, http, xml, chyba = f.result()
                uloz_meta(con, idv, http, xml, chyba, doc_dir)
                n += 1
                if n % 10 == 0 or n == len(seznam):
                    con.commit()
                    print(f"  {popis} {n}/{len(seznam)}", end="\r")
        con.commit()
        print()

    if todo:
        davka(todo, "metadata")
    # puvodni smlouvy, na ktere odkazuji dodatky (navazanyZaznam = idSmlouvy)
    mame = {r[0] for r in con.execute("SELECT id_smlouvy FROM meta WHERE id_smlouvy IS NOT NULL")}
    chybi = {r[0] for r in con.execute("SELECT DISTINCT navazany FROM meta WHERE navazany<>''")} - mame
    puvodni = []
    for ids_ in chybi:
        r = con.execute("SELECT id_verze FROM smlouvy WHERE id_smlouvy=? ORDER BY CAST(id_verze AS INTEGER) DESC",
                        (ids_,)).fetchone()
        if r and r[0] not in hotove:
            puvodni.append(r[0])
    print(f"Puvodni smlouvy k dodatkum: chybi {len(chybi)}, v nasi databazi dohledano {len(puvodni)}")
    if puvodni:
        davka(puvodni, "puvodni smlouvy")

    # text se stranami: prilohy z XML (data/doc) nebo drive stazene PDF (data/pdf)
    pdf_dir = data_dir / "pdf"
    hotove_t = {r[0] for r in con.execute("SELECT id_verze FROM text_str")}
    vsechny = [r[0] for r in con.execute("SELECT id_verze FROM meta WHERE http=200")]
    n = 0
    for idv in vsechny:
        if idv in hotove_t:
            continue
        paths = sorted(doc_dir.glob(f"{idv}_*")) or sorted(pdf_dir.glob(f"{idv}_*"))
        paths = [p for p in paths if p.suffix.lower() in (".pdf", ".docx")]
        text, stran = text_se_stranami(paths)
        znaku = len(re.sub(r"\[\[STRANA \d+\]\]", "", text).strip())
        sken = int(stran > 0 and znaku / stran < 200)
        cesta = txt_dir / f"{idv}.txt"
        cesta.write_text(text, encoding="utf-8")
        con.execute("INSERT OR REPLACE INTO text_str VALUES(?,?,?,?,?,?)",
                    (idv, str(cesta), len(paths), stran, znaku, sken))
        n += 1
        if n % 25 == 0:
            con.commit()
            print(f"  texty se stranami {n}", end="\r")
    con.commit()
    return souhrn5(con)


def souhrn5(con: sqlite3.Connection) -> int:
    con.executescript(SCHEMA)
    q = lambda s: con.execute(s).fetchone()[0]  # noqa: E731
    print("\n================ SOUHRN KROK 5 (posli mi tuto cast) ================")
    print("metadata http:", dict(con.execute("SELECT http, COUNT(*) FROM meta GROUP BY http")))
    s_vazbou = q("SELECT COUNT(*) FROM meta WHERE navazany<>''")
    print(f"dokumentu s vazbou na puvodni smlouvu (navazanyZaznam): {s_vazbou}")
    print(f"vazba vede na smlouvu, kterou mame: {q('''SELECT COUNT(*) FROM meta a WHERE a.navazany<>'' AND EXISTS
          (SELECT 1 FROM meta b WHERE b.id_smlouvy=a.navazany)''')}")
    print(f"zaporna hodnota (zmenseni): {q('SELECT COUNT(*) FROM meta WHERE hodnota<0')}")
    print(f"texty se stranami: {q('SELECT COUNT(*) FROM text_str')}, skenu: {q('SELECT COALESCE(SUM(sken),0) FROM text_str')}, "
          f"bez souboru: {q('SELECT COUNT(*) FROM text_str WHERE souboru=0')}")
    print("chyby:", [r for r in con.execute("SELECT id_verze, http, chyba FROM meta WHERE http<>200 LIMIT 5")])
    print("====================================================================")
    return 0
