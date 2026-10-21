"""Krok 1: stazeni otevrenych dat Registru smluv a vyber najemnich smluv na kancelare.

Zdroj: https://smlouvy.gov.cz/stranka/otevrena-data
- index.xml popisuje mesicni dumpy (odkaz, rok, mesic, velikost)
- dump obsahuje elementy <zaznam> s metadaty smluv a odkazy na prilohy (PDF)

Stahujeme pouze oficialne zverejnene soubory z index.xml (zadny crawling).
Ukladame jen smlouvy, kde je NEKTERA strana pravnicka osoba s ICO - fyzicke osoby vyrazujeme (GDPR).
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

INDEX_URL = "https://data.smlouvy.gov.cz/index.xml"
USER_AGENT = "NajemniRadar-demo/0.1 (open data research; kontakt v README)"

# --- filtr -----------------------------------------------------------------
# najem, najmu, najemni, pronajem, podnajem, pronajmu...
RE_NAJEM = re.compile(r"n[aá]j(em|m)", re.I)
RE_KANCELAR = re.compile(
    r"kancel[aá]ř|kancelar|nebytov|administrativn|office|prostor", re.I)
# veci, ktere nas nezajimaji (byty, pozemky, auta, technika...)
RE_VYLOUCIT = re.compile(
    r"\bbyt(u|y|ů|em|ech)?\b|(?<!ne)bytov|pozem|zahrad|vozid|automobil|kop[ií]rk|tisk[aá]r|"
    r"multifunk|server|licenc|software|hrob|parkovac[ií] m[ií]st|reklamn[ií] ploch|"
    r"pastv|les(n[ií])?\b|rybn|\bpole\b|zem[eě]d[eě]l|"
    # sum z realnych dat: akce, satly, ucebny, splatky, infrastruktura
    r"konferen|kr[aá]tkodob|spl[aá]tk|u[cč]ebn|kolektor|kon[ií]rn|t[eě]locvi|"
    r"\bakc[ei]\b|market|st[aá]nek|st[aá]nk|baz[eé]n", re.I)
RE_PRAHA = re.compile(r"\bPraha\b|\bPrague\b|\b1[0-9]{2}\s?[0-9]{2}\b")
RE_DODATEK = re.compile(r"dodat", re.I)
RE_UKONCENI = re.compile(r"ukon[cč]|skon[cč]en|v[yý]pov[eě]", re.I)


def typ_smlouvy(predmet: str) -> str:
    """ukonceni / dodatek / smlouva - dodatky a ukonceni nesou informaci o konci najmu."""
    if RE_UKONCENI.search(predmet):
        return "ukonceni"
    if RE_DODATEK.search(predmet):
        return "dodatek"
    return "smlouva"


def _ano(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "ano")


def je_kandidat(predmet: str) -> bool:
    """Najemni smlouva na kancelare / nebytove prostory (ne byty, pozemky, technika)."""
    if not predmet:
        return False
    return bool(RE_NAJEM.search(predmet) and RE_KANCELAR.search(predmet)
                and not RE_VYLOUCIT.search(predmet))


# --- XML pomocnici (dump ma namespace, porovnavame jen lokalni jmena) ------
def _ln(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: ET.Element, name: str) -> ET.Element | None:
    for c in el:
        if _ln(c.tag) == name:
            return c
    return None


def _children(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if _ln(c.tag) == name]


def _text(el: ET.Element | None, name: str | None = None) -> str:
    if el is None:
        return ""
    if name is not None:
        el = _child(el, name)
        if el is None:
            return ""
    return (el.text or "").strip()


def _strana(el: ET.Element) -> dict:
    return {
        "nazev": _text(el, "nazev"),
        "ico": _text(el, "ico"),
        "adresa": _text(el, "adresa"),
        "prijemce": _text(el, "prijemce"),
    }


@dataclass
class Zaznam:
    id_smlouvy: str
    id_verze: str
    odkaz: str
    cas_zverejneni: str
    datum_uzavreni: str
    cislo_smlouvy: str
    predmet: str
    hodnota_bez_dph: float | None
    hodnota_s_dph: float | None
    subjekt: dict
    strany: list[dict]
    prilohy: list[dict] = field(default_factory=list)
    platny: bool = True

    @property
    def praha(self) -> bool:
        txt = " ".join([self.predmet, self.subjekt.get("adresa", "")]
                       + [s.get("adresa", "") for s in self.strany])
        return bool(RE_PRAHA.search(txt))

    @property
    def praha_predmet(self) -> bool:
        """Silnejsi signal: Praha primo v predmetu smlouvy (adresa prostor)."""
        return bool(RE_PRAHA.search(self.predmet))

    @property
    def verejny_plati(self) -> int:
        """1 = protistrana je prijemce platby -> verejny subjekt je NAJEMCE (plati najem).
        0 = zadna protistrana neni prijemce (verejny subjekt spis pronajima)."""
        return int(any(_ano(s.get("prijemce", "")) for s in self.strany))

    @property
    def pravnicke_osoby(self) -> bool:
        """Vsechny protistrany maji ICO (= ne fyzicke osoby bez ICO)."""
        return bool(self.strany) and all(s.get("ico") for s in self.strany)


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", ".")) if s else None
    except ValueError:
        return None


def parse_zaznam(el: ET.Element) -> Zaznam:
    ident = _child(el, "identifikator")
    sml = _child(el, "smlouva")
    prilohy_el = _child(el, "prilohy")
    prilohy = []
    if prilohy_el is not None:
        for p in _children(prilohy_el, "priloha"):
            prilohy.append({"nazev": _text(p, "nazevSouboru"), "odkaz": _text(p, "odkaz")})
    subj = _child(sml, "subjekt") if sml is not None else None
    platny_txt = _text(el, "platnyZaznam")
    return Zaznam(
        id_smlouvy=_text(ident, "idSmlouvy"),
        id_verze=_text(ident, "idVerze"),
        odkaz=_text(el, "odkaz"),
        cas_zverejneni=_text(el, "casZverejneni"),
        datum_uzavreni=_text(sml, "datumUzavreni"),
        cislo_smlouvy=_text(sml, "cisloSmlouvy"),
        predmet=_text(sml, "predmet"),
        hodnota_bez_dph=_num(_text(sml, "hodnotaBezDph")),
        hodnota_s_dph=_num(_text(sml, "hodnotaVcetneDph")),
        subjekt=_strana(subj) if subj is not None else {},
        strany=[_strana(s) for s in _children(sml, "smluvniStrana")] if sml is not None else [],
        prilohy=prilohy,
        platny=(platny_txt in ("", "1", "true")),
    )


def iter_zaznamy(source) -> "iter[Zaznam]":
    """Streamove cteni dumpu (soubor muze mit stovky MB) - pamet zustava mala."""
    for event, el in ET.iterparse(source, events=("end",)):
        if _ln(el.tag) == "zaznam":
            yield parse_zaznam(el)
            el.clear()


# --- index ------------------------------------------------------------------
def parse_index(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    out = []
    for d in root.iter():
        if _ln(d.tag) != "dump":
            continue
        odkaz = _text(d, "odkaz")
        m = RE_DUMP_NAME.search(odkaz)
        out.append({
            "rok": int(_text(d, "rok") or 0),
            "mesic": int(_text(d, "mesic") or 0),
            "den": int(m.group(3)) if m and m.group(3) else 0,  # 0 = mesicni dump
            "odkaz": odkaz,
            "velikost": int(_text(d, "velikostDumpu") or 0),
            "dokonceny": _text(d, "dokoncenyMesic") in ("1", "true"),
        })
    out.sort(key=lambda x: (x["rok"], x["mesic"], x["den"]))
    return out


RE_DUMP_NAME = re.compile(r"dump_(\d{4})_(\d{2})(?:_(\d{2}))?\.xml")


def vyber_rozsah(dumpy: list[dict], od: str, do: str) -> list[dict]:
    """Mesicni dumpy v rozsahu RRRR-MM..RRRR-MM (vcetne)."""
    lo, hi = tuple(map(int, od.split("-"))), tuple(map(int, do.split("-")))
    return [d for d in dumpy if d["den"] == 0 and lo <= (d["rok"], d["mesic"]) <= hi]


def vyber_dumpy(dumpy: list[dict], mesicu: int) -> list[dict]:
    """Poslednich N mesicnich dumpu. Denni dumpy (dump_RRRR_MM_DD) jen pokud mesicni chybi."""
    mesicni = [d for d in dumpy if d["den"] == 0]
    if mesicni:
        return mesicni[-mesicu:]
    # jen denni dumpy -> vsechny dny z poslednich N kalendarnich mesicu
    mesice = sorted({(d["rok"], d["mesic"]) for d in dumpy})[-mesicu:]
    return [d for d in dumpy if (d["rok"], d["mesic"]) in set(mesice)]


# --- databaze ---------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS smlouvy(
  id_smlouvy TEXT, id_verze TEXT PRIMARY KEY, odkaz TEXT, cas_zverejneni TEXT,
  datum_uzavreni TEXT, cislo_smlouvy TEXT, predmet TEXT,
  hodnota_bez_dph REAL, hodnota_s_dph REAL,
  subjekt_nazev TEXT, subjekt_ico TEXT, subjekt_adresa TEXT,
  strany_json TEXT, prilohy_json TEXT, pocet_priloh INTEGER,
  praha INTEGER, dodatek INTEGER, dump TEXT,
  praha_predmet INTEGER, verejny_plati INTEGER, typ TEXT
);
CREATE TABLE IF NOT EXISTS dumpy(odkaz TEXT PRIMARY KEY, zpracovano TEXT, zaznamu INTEGER, kandidatu INTEGER);
"""


def open_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    cols = [r[1] for r in con.execute("PRAGMA table_info(smlouvy)")]
    if cols and "typ" not in cols:  # stara verze schematu -> zalozit znovu
        con.executescript("DROP TABLE smlouvy; DROP TABLE IF EXISTS dumpy;")
    con.executescript(SCHEMA)
    return con


def uloz(con: sqlite3.Connection, z: Zaznam, dump: str) -> None:
    con.execute(
        "INSERT OR REPLACE INTO smlouvy VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (z.id_smlouvy, z.id_verze, z.odkaz, z.cas_zverejneni, z.datum_uzavreni,
         z.cislo_smlouvy, z.predmet, z.hodnota_bez_dph, z.hodnota_s_dph,
         z.subjekt.get("nazev", ""), z.subjekt.get("ico", ""), z.subjekt.get("adresa", ""),
         json.dumps(z.strany, ensure_ascii=False), json.dumps(z.prilohy, ensure_ascii=False),
         len(z.prilohy), int(z.praha), int(bool(RE_DODATEK.search(z.predmet))), dump,
         int(z.praha_predmet), z.verejny_plati, typ_smlouvy(z.predmet)))


def zpracuj_dump(con: sqlite3.Connection, path: Path, dump_name: str) -> tuple[int, int]:
    n = k = 0
    with open(path, "rb") as f:
        for z in iter_zaznamy(f):
            n += 1
            if not z.platny or not je_kandidat(z.predmet) or not z.pravnicke_osoby:
                continue
            uloz(con, z, dump_name)
            k += 1
    con.execute("INSERT OR REPLACE INTO dumpy VALUES(?,?,?,?)",
                (dump_name, time.strftime("%Y-%m-%d %H:%M"), n, k))
    con.commit()
    return n, k


# --- stahovani --------------------------------------------------------------
def _client():
    import httpx
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True)


def stahni(url: str, cil: Path, pokusu: int = 6) -> Path:
    """Stahne soubor; pri vypadku spojeni (server obcas spojeni utne) to zkusi znovu s pauzou."""
    import httpx
    for pokus in range(1, pokusu + 1):
        try:
            return _stahni1(url, cil)
        except (httpx.TransportError, httpx.HTTPStatusError) as e:
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
                raise
            pauza = min(300, 15 * 2 ** (pokus - 1))
            print(f"\n  ! {cil.name}: {type(e).__name__} ({str(e)[:80]}) - pokus {pokus}/{pokusu}, "
                  f"cekam {pauza} s")
            time.sleep(pauza)
    raise RuntimeError(f"{cil.name}: stazeni selhalo ani po {pokusu} pokusech")


def _stahni1(url: str, cil: Path) -> Path:
    """Stahne soubor streamem (s prubehem). Pokud uz existuje se stejnou velikosti, preskoci."""
    cil.parent.mkdir(parents=True, exist_ok=True)
    tmp = cil.with_suffix(cil.suffix + ".part")
    with _client() as c, c.stream("GET", url) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        if cil.exists() and total and cil.stat().st_size == total:
            print(f"  (uz stazeno) {cil.name}")
            return cil
        done, last = 0, 0.0
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if time.time() - last > 2:
                    pct = f"{100*done/total:5.1f} %" if total else ""
                    print(f"  {cil.name}: {done/1e6:8.1f} MB {pct}", end="\r")
                    last = time.time()
    tmp.replace(cil)
    print(f"  {cil.name}: {done/1e6:8.1f} MB  hotovo        ")
    return cil


def robots(host: str) -> str:
    try:
        with _client() as c:
            r = c.get(f"https://{host}/robots.txt")
            return r.text if r.status_code == 200 else f"(HTTP {r.status_code})"
    except Exception as e:  # noqa: BLE001
        return f"(chyba: {e})"


def krok1(mesicu: int, data_dir: Path, ano: bool, ponechat: bool,
          od: str | None = None, do: str | None = None) -> int:
    data_dir.mkdir(parents=True, exist_ok=True)
    print("== robots.txt (pro transparentnost) ==")
    for host in ("data.smlouvy.gov.cz", "smlouvy.gov.cz"):
        print(f"--- {host}\n{robots(host).strip()[:600]}\n")

    print("== index dumpu ==")
    with _client() as c:
        r = c.get(INDEX_URL)
        r.raise_for_status()
    dumpy = parse_index(r.content)
    if not dumpy:
        print("Index je prazdny nebo ma jiny format - posli mi prvnich 40 radku:")
        print(r.text[:3000])
        return 1
    n_mes = sum(1 for d in dumpy if d["den"] == 0)
    print(f"Index: {len(dumpy)} souboru ({n_mes} mesicnich, {len(dumpy)-n_mes} dennich), "
          f"od {dumpy[0]['odkaz'].rsplit('/',1)[-1]} do {dumpy[-1]['odkaz'].rsplit('/',1)[-1]}")
    vyber = vyber_rozsah(dumpy, od, do or "9999-12") if od else vyber_dumpy(dumpy, mesicu)
    celkem = sum(d["velikost"] for d in vyber)
    for d in vyber:
        den = f"-{d['den']:02d}" if d["den"] else "   "
        print(f"  {d['rok']}-{d['mesic']:02d}{den}  {d['velikost']/1e6:8.1f} MB  "
              f"{'hotovy' if d['dokonceny'] else 'rozpracovany'}  {d['odkaz']}")
    print(f"Celkem ke stazeni: {celkem/1e9:.2f} GB")
    if not ano:
        if input("Pokracovat? [a/N] ").strip().lower() not in ("a", "ano", "y", "yes"):
            print("Zruseno.")
            return 0

    con = open_db(data_dir / "najemniradar.sqlite")
    hotove = {r[0] for r in con.execute("SELECT odkaz FROM dumpy")}
    for i, d in enumerate(vyber, 1):
        name = d["odkaz"].rsplit("/", 1)[-1]
        if name in hotove and d["dokonceny"]:
            print(f"== [{i}/{len(vyber)}] {name} == uz zpracovano, preskakuji")
            continue
        print(f"== [{i}/{len(vyber)}] {name} ==")
        try:
            path = stahni(d["odkaz"], data_dir / "dumps" / name)
        except Exception as e:  # noqa: BLE001 - jeden mesic nesmi shodit cely beh
            print(f"  !! {name} preskakuji ({e}); dalsi spusteni ho dozene")
            continue
        n, k = zpracuj_dump(con, path, name)
        print(f"  zaznamu: {n:,}   kandidatu (najem kancelari/nebyt. prostor): {k}")
        if not ponechat:
            path.unlink(missing_ok=True)
    return souhrn(con, data_dir)


def souhrn(con: sqlite3.Connection, data_dir: Path) -> int:
    q = lambda s, *a: con.execute(s, a).fetchone()[0]  # noqa: E731
    print("\n================ SOUHRN (posli mi tuto cast) ================")
    print(f"kandidatu celkem:                 {q('SELECT COUNT(*) FROM smlouvy')}")
    print(f"  Praha (jakakoli adresa):        {q('SELECT COUNT(*) FROM smlouvy WHERE praha=1')}")
    print(f"  Praha v predmetu:               {q('SELECT COUNT(*) FROM smlouvy WHERE praha_predmet=1')}")
    print(f"  priznak 'prijemce' (nespolehl.):{q('SELECT COUNT(*) FROM smlouvy WHERE verejny_plati=1')}")
    print(f"  ...a zaroven Praha:             {q('SELECT COUNT(*) FROM smlouvy WHERE verejny_plati=1 AND praha=1')}")
    print("  podle typu:", dict(con.execute("SELECT typ, COUNT(*) FROM smlouvy GROUP BY typ")))
    print("  podle roku uzavreni:", dict(con.execute(
        "SELECT substr(datum_uzavreni,1,4) r, COUNT(*) FROM smlouvy GROUP BY r ORDER BY r")))
    # kontrola, jake hodnoty ma priznak 'prijemce' v realnych datech
    vals: dict[str, int] = {}
    for (sj,) in con.execute("SELECT strany_json FROM smlouvy"):
        for st in json.loads(sj):
            v = repr(st.get("prijemce", ""))
            vals[v] = vals.get(v, 0) + 1
    print("  hodnoty 'prijemce' u stran:", vals)

    print("\n-- 25 nahodnych: verejny subjekt PLATI najem, Praha --")
    for p, sn, t in con.execute(
            "SELECT predmet, subjekt_nazev, typ FROM smlouvy WHERE verejny_plati=1 AND praha=1 "
            "ORDER BY RANDOM() LIMIT 25"):
        print(f"  - [{t}] {sn[:35]} | {p[:110]}".replace("\n", " "))
    print("\n-- nejvetsi hodnoty: verejny subjekt PLATI --")
    for p, h, sn in con.execute(
            "SELECT predmet, COALESCE(hodnota_bez_dph, hodnota_s_dph) h, subjekt_nazev FROM smlouvy "
            "WHERE verejny_plati=1 AND h IS NOT NULL ORDER BY h DESC LIMIT 10"):
        print(f"  {h:>15,.0f} Kc | {sn[:40]} | {p[:70]}")
    out = data_dir / "kandidati.csv"
    import csv
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        cur = con.execute("SELECT id_verze, datum_uzavreni, typ, verejny_plati, praha, praha_predmet, "
                          "subjekt_nazev, predmet, hodnota_bez_dph, hodnota_s_dph, pocet_priloh, odkaz "
                          "FROM smlouvy ORDER BY verejny_plati DESC, praha DESC, datum_uzavreni DESC")
        w.writerow([c[0] for c in cur.description])
        w.writerows(cur)
    print(f"\nCSV pro kontrolu: {out}")
    print("=============================================================")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(krok1(6, Path("data"), False, False))
