"""Krok 3: lokalni LLM (Ollama) - role + abstrakt najemni smlouvy s citacemi.

Navrh pro slabe CPU (notebook, bez GPU):
  * role: model cte jen uvod smlouvy (smluvni strany, ~2 500 znaku) a jen tam, kde pravidla nevi
  * abstrakt: "mini-RAG uvnitr smlouvy" - text se rozdeli na odstavce, vyberou se jen relevantni
    (strany, predmet/plocha, najemne, doba, vypoved, opce, indexace) -> model cte ~1-2 strany, ne 10
  * strukturovany vystup: Ollama 'format' = JSON schema -> zadne parsovani volneho textu
  * kontrola pravdivosti: kazda citace se overi, ze OPRAVDU je v textu smlouvy; jinak 'neovereno'
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from .krok2 import fold

OLLAMA = "http://localhost:11434"
MODEL = "qwen3:4b-instruct"

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_role(id_verze TEXT PRIMARY KEY, role TEXT, je_kancelar INTEGER,
  citace TEXT, overeno INTEGER, sekund REAL, model TEXT);
CREATE TABLE IF NOT EXISTS abstrakt(id_verze TEXT PRIMARY KEY, data TEXT, overeno_citaci INTEGER,
  citaci INTEGER, najemce TEXT, pronajimatel TEXT, adresa TEXT, plocha_m2 REAL, kc_m2_mesic REAL,
  najemne_rocne REAL, zacatek TEXT, konec TEXT, vypoved_mesicu REAL, sekund REAL, model TEXT,
  kc_m2_zdroj TEXT);
"""


# ------------------------------------------------------------ Ollama --
def ollama_chat(system: str, user: str, schema: dict, model: str = MODEL, num_ctx: int = 6144) -> dict:
    import httpx
    body = {"model": model, "stream": False, "format": schema,
            "options": {"temperature": 0, "num_ctx": num_ctx},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=900)
    if r.status_code >= 400:  # starsi Ollama neumi slozitejsi schema -> obecny JSON rezim + schema v zadani
        body["format"] = "json"
        body["messages"][0]["content"] += "\nOdpovez POUZE JSON objektem podle schematu: " + json.dumps(schema)
        r = httpx.post(f"{OLLAMA}/api/chat", json=body, timeout=900)
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])


def ollama_ok(model: str) -> str | None:
    """None = vse OK, jinak text chyby pro uzivatele."""
    import httpx
    try:
        tags = httpx.get(f"{OLLAMA}/api/tags", timeout=5).json()
    except Exception:  # noqa: BLE001
        return "Ollama nebezi. Spust aplikaci Ollama (nebo 'ollama serve') a zkus znovu."
    names = [m.get("name", "") for m in tags.get("models", [])]
    if not any(n == model or n.startswith(model + ":") for n in names):
        return f"Model {model} neni stazeny. Spust: ollama pull {model}   (mas: {', '.join(names)})"
    return None


# ------------------------------------------------- overeni citaci --
def citace_v_textu(citace: str, text_fold: str) -> bool:
    """Citace musi byt v textu smlouvy (tolerance: diakritika, mezery, prvnich 60 znaku)."""
    c = fold(citace or "").strip(" .,\"'„“")
    if len(c) < 6:
        return False
    c = re.sub(r"\s+", " ", c)[:60]
    return c in text_fold


# ----------------------------------------------------------- role --
ROLE_SCHEMA = {
    "type": "object",
    "properties": {
        "role_verejneho_subjektu": {"type": "string", "enum": ["najemce", "pronajimatel", "jine", "nejasne"]},
        "je_kancelar": {"type": "boolean"},
        "citace": {"type": "string"},
    },
    "required": ["role_verejneho_subjektu", "je_kancelar", "citace"],
}
ROLE_SYSTEM = (
    "Jsi pravni analytik. Cti uvod ceske najemni smlouvy. Urci, jakou roli v ni ma uvedeny "
    "verejny subjekt: 'najemce' (platí nájem, užívá prostory), 'pronajimatel' (prostory pronajímá), "
    "'jine' (nejde o nájem prostor), nebo 'nejasne'. je_kancelar = true, pokud jde o kancelářské "
    "nebo administrativní prostory. Do 'citace' zkopíruj DOSLOVA krátký úsek textu (max 120 znaků), "
    "ze kterého role plyne. Nic si nevymýšlej."
)


def llm_role(text: str, subjekt: str, ico: str, model: str = MODEL) -> dict:
    user = f"Verejny subjekt: {subjekt} (ICO {ico})\n\nUVOD SMLOUVY:\n{text[:2500]}"
    return ollama_chat(ROLE_SYSTEM, user, ROLE_SCHEMA, model, num_ctx=3072)


# ------------------------------------------- mini-RAG uvnitr smlouvy --
TEMATA = {
    "strany": r"pronajimatel|najemce|ico|se sidlem|zastoupen",
    "predmet": r"predmet|\bm2\b|m²|metr|podlazi|budov|ulic|parc\.|c\.\s?p\.|katastr|plocha|vymer",
    "najemne": r"najemne|kc|korun|eur|\bmesicne|rocne|za m|cena|platb",
    "doba": r"doba|dobu|urcit|neurcit|do dne|ode dne|let\b|roku|skonc|ucinnost",
    "vypoved": r"vypov|vypovedni|odstoup|ukoncen",
    "opce": r"opc|prodlouz|obnov",
    "indexace": r"inflac|index|valoriz|cpi|spotrebitelskych cen|zvys",
}


def vyber_kontext(text: str, max_znaku: int = 4500) -> str:
    """Rozdeli smlouvu na odstavce, oboduje je podle temat a vrati nejrelevantnejsi v puvodnim poradi.
    Uvod (smluvni strany) se bere vzdy."""
    odst = [o.strip() for o in re.split(r"\n\s*\n|\n(?=\s*(?:čl\.|cl\.|článek|clanek|\d+\.\d*\s))", text, flags=re.I)
            if o.strip()]
    if not odst:
        return text[:max_znaku]
    skore = []
    for i, o in enumerate(odst):
        f = fold(o)
        s = sum(min(3, len(re.findall(rx, f))) for rx in TEMATA.values())
        s += 2 * sum(1 for rx in TEMATA.values() if re.search(rx, f))  # pestrost temat
        skore.append((s, i))
    uvod = text[:1500]
    vybrane, delka = set(), len(uvod)
    for s, i in sorted(skore, reverse=True):
        if s == 0:
            break
        if delka + len(odst[i]) > max_znaku:
            continue
        vybrane.add(i)
        delka += len(odst[i])
    return uvod + "\n[...]\n" + "\n[...]\n".join(odst[i][:1500] for i in sorted(vybrane))


ABSTRAKT_SCHEMA = {
    "type": "object",
    "properties": {
        "najemce": {"type": "string"}, "pronajimatel": {"type": "string"},
        "adresa_prostor": {"type": "string"},
        "plocha_m2": {"type": ["number", "null"]},
        "najemne_rocne_kc": {"type": ["number", "null"]},
        "najemne_mesicne_kc": {"type": ["number", "null"]},
        "najemne_kc_m2_mesic": {"type": ["number", "null"]},
        "zacatek_najmu": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "konec_najmu": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "doba_neurcita": {"type": "boolean"},
        "vypovedni_lhuta_mesicu": {"type": ["number", "null"]},
        "opce_prodlouzeni": {"type": ["string", "null"]},
        "indexace": {"type": ["string", "null"]},
        "ucel_uzivani": {"type": ["string", "null"]},
        "citace_plocha": {"type": ["string", "null"]},
        "citace_najemne": {"type": ["string", "null"]},
        "citace_doba": {"type": ["string", "null"]},
        "citace_vypoved": {"type": ["string", "null"]},
    },
    "required": ["najemce", "pronajimatel", "adresa_prostor", "plocha_m2", "najemne_rocne_kc",
                 "najemne_mesicne_kc", "najemne_kc_m2_mesic", "zacatek_najmu", "konec_najmu", "doba_neurcita",
                 "vypovedni_lhuta_mesicu", "opce_prodlouzeni", "indexace", "ucel_uzivani",
                 "citace_plocha", "citace_najemne", "citace_doba", "citace_vypoved"],
}
ABSTRAKT_SYSTEM = (
    "Jsi pravni analytik realitni poradenske firmy. Z vyňatků české nájemní smlouvy vytvoř "
    "'lease abstract'. Pravidla: 1) Uváděj JEN to, co ve smlouvě skutečně stojí; co tam není, dej null. "
    "2) Částky v Kč bez DPH, pokud je to rozlišeno. 3) Data ve formátu YYYY-MM-DD; pokud je doba "
    "'na X let ode dne D', dopočítej konec. 4) Do polí citace_* zkopíruj DOSLOVA krátký úsek textu "
    "(max 150 znaků), ze kterého údaj plyne. 5) Nic si nevymýšlej - chybný údaj je horší než null."
)


def _num(x) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _mesicu(zac: str | None, kon: str | None) -> float | None:
    try:
        y1, m1 = int(zac[:4]), int(zac[5:7])
        y2, m2 = int(kon[:4]), int(kon[5:7])
        n = (y2 - y1) * 12 + (m2 - m1) + 1
        return float(n) if n > 0 else None
    except (TypeError, ValueError):
        return None


def zpracuj_abstrakt(d: dict, text: str, hodnota_smlouvy: float | None = None) -> dict:
    """Dopocty + overeni citaci proti textu smlouvy.
    Kc/m2/mesic: 1) primo ze smlouvy, 2) dopocet z rocniho/mesicniho najemneho a plochy,
    3) ODHAD z hodnoty smlouvy v registru / pocet mesicu / plocha (oznaceno jako odhad)."""
    tf = re.sub(r"\s+", " ", fold(text))
    cit = {k: d.get(k) for k in ("citace_plocha", "citace_najemne", "citace_doba", "citace_vypoved") if d.get(k)}
    over = {k: citace_v_textu(v, tf) for k, v in cit.items()}
    plocha = _num(d.get("plocha_m2"))
    rocne, mesicne = _num(d.get("najemne_rocne_kc")), _num(d.get("najemne_mesicne_kc"))
    if rocne is None and mesicne:
        rocne = mesicne * 12
    m2, zdroj = _num(d.get("najemne_kc_m2_mesic")), "smlouva"
    if m2 is None and plocha and rocne:
        m2, zdroj = round(rocne / 12 / plocha, 1), "dopocet"
    if m2 is None and plocha and hodnota_smlouvy:
        mes = _mesicu(_datum(d.get("zacatek_najmu")), _datum(d.get("konec_najmu")))
        if mes:
            m2, zdroj = round(hodnota_smlouvy / mes / plocha, 1), "odhad z hodnoty smlouvy"
    # mimo realny rozsah kancelari = pravdepodobne chyba jednotek -> zahodit
    if m2 is not None and not (20 <= m2 <= 2000):
        m2 = None
    return {**d, "_najemne_rocne": rocne, "_kc_m2_mesic": m2, "_kc_m2_zdroj": zdroj if m2 else None,
            "_overeni": over}


def _datum(s) -> str | None:
    return s if isinstance(s, str) and re.fullmatch(r"(19|20)\d\d-\d\d-\d\d", s) else None


# --------------------------------------------------------- davky --
def krok3(con: sqlite3.Connection, faze: str, limit: int, model: str, limit_role: int | None = None) -> int:
    cols = [r[1] for r in con.execute("PRAGMA table_info(abstrakt)")]
    if cols and "kc_m2_zdroj" not in cols:  # v0.3.0 -> v0.3.1: abstrakty se udelaji znovu
        con.execute("DROP TABLE abstrakt")
    con.executescript(SCHEMA)
    chyba = ollama_ok(model)
    if chyba:
        print(chyba)
        return 1
    if faze in ("role", "vse"):
        rows = con.execute(
            """SELECT t.id_verze, t.cesta, s.subjekt_nazev, s.subjekt_ico FROM shortlist l
               JOIN texty t USING(id_verze) JOIN smlouvy s USING(id_verze)
               LEFT JOIN llm_role r USING(id_verze)
               WHERE t.sken=0 AND t.znaku>300 AND t.role_text='nejasne' AND r.id_verze IS NULL
               ORDER BY l.skore DESC LIMIT ?""", (limit_role or limit,)).fetchall()
        print(f"LLM role: {len(rows)} smluv, kde pravidla nerozhodla (model {model})")
        for n, (idv, cesta, sn, ico) in enumerate(rows, 1):
            text = Path(cesta).read_text(encoding="utf-8")
            t0 = time.time()
            try:
                d = llm_role(text, sn, ico, model)
            except Exception as e:  # noqa: BLE001
                print(f"  [{n}] {idv}: chyba {e}")
                continue
            dt = time.time() - t0
            ok = citace_v_textu(d.get("citace", ""), re.sub(r"\s+", " ", fold(text)))
            con.execute("INSERT OR REPLACE INTO llm_role VALUES(?,?,?,?,?,?,?)",
                        (idv, d["role_verejneho_subjektu"], int(d["je_kancelar"]), d.get("citace", ""),
                         int(ok), round(dt, 1), model))
            con.commit()
            print(f"  [{n}/{len(rows)}] {idv}: {d['role_verejneho_subjektu']:12s} kancelar={d['je_kancelar']!s:5s} "
                  f"citace={'OK' if ok else '??'}  {dt:4.0f} s")
    if faze in ("abstrakt", "vse"):
        rows = con.execute(
            """SELECT t.id_verze, t.cesta, (SELECT COALESCE(hodnota_bez_dph, hodnota_s_dph) FROM smlouvy s
                  WHERE s.id_verze=t.id_verze) FROM shortlist l JOIN texty t USING(id_verze)
               LEFT JOIN llm_role r USING(id_verze) LEFT JOIN abstrakt a USING(id_verze)
               WHERE t.sken=0 AND t.znaku>300 AND a.id_verze IS NULL
                 AND (t.role_text='verejny_najemce' OR r.role='najemce')
               ORDER BY l.skore DESC LIMIT ?""", (limit,)).fetchall()
        print(f"LLM abstrakt: {len(rows)} potvrzenych najmu (model {model})")
        for n, (idv, cesta, hodnota) in enumerate(rows, 1):
            text = Path(cesta).read_text(encoding="utf-8")
            t0 = time.time()
            try:
                d = ollama_chat(ABSTRAKT_SYSTEM, "VYNATKY ZE SMLOUVY:\n" + vyber_kontext(text),
                                ABSTRAKT_SCHEMA, model, num_ctx=4096)
            except Exception as e:  # noqa: BLE001
                print(f"  [{n}] {idv}: chyba {e}")
                continue
            dt = time.time() - t0
            z = zpracuj_abstrakt(d, text, hodnota)
            over = z["_overeni"]
            con.execute("INSERT OR REPLACE INTO abstrakt VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (idv, json.dumps(z, ensure_ascii=False), sum(over.values()), len(over),
                         d.get("najemce"), d.get("pronajimatel"), d.get("adresa_prostor"),
                         _num(d.get("plocha_m2")), z["_kc_m2_mesic"], z["_najemne_rocne"],
                         _datum(d.get("zacatek_najmu")), _datum(d.get("konec_najmu")),
                         _num(d.get("vypovedni_lhuta_mesicu")), round(dt, 1), model, z["_kc_m2_zdroj"]))
            con.commit()
            print(f"  [{n}/{len(rows)}] {idv}: {str(d.get('plocha_m2')):>8} m2, {z['_kc_m2_mesic']} Kc/m2 "
                  f"({z['_kc_m2_zdroj']}), konec {d.get('konec_najmu')}, "
                  f"citace {sum(over.values())}/{len(over)} OK, {dt:4.0f} s")
    return souhrn3(con)


def souhrn3(con: sqlite3.Connection) -> int:
    con.executescript(SCHEMA)
    q = lambda s: con.execute(s).fetchone()[0]  # noqa: E731
    print("\n================ SOUHRN KROK 3 (posli mi tuto cast) ================")
    print(f"shortlist: {q('SELECT COUNT(*) FROM shortlist')}, s textem: "
          f"{q('SELECT COUNT(*) FROM shortlist JOIN texty USING(id_verze) WHERE sken=0')}, "
          f"skenu: {q('SELECT COUNT(*) FROM shortlist JOIN texty USING(id_verze) WHERE sken=1')}")
    print("role z pravidel (shortlist):", dict(con.execute(
        "SELECT role_text, COUNT(*) FROM shortlist JOIN texty USING(id_verze) GROUP BY 1")))
    print("role z LLM:", dict(con.execute("SELECT role, COUNT(*) FROM llm_role GROUP BY 1")),
          f"| citace overena: {q('SELECT COALESCE(SUM(overeno),0) FROM llm_role')}/{q('SELECT COUNT(*) FROM llm_role')}",
          f"| prumer {q('SELECT ROUND(COALESCE(AVG(sekund),0)) FROM llm_role')} s")
    print(f"abstraktu: {q('SELECT COUNT(*) FROM abstrakt')}, s plochou: {q('SELECT COUNT(plocha_m2) FROM abstrakt')}, "
          f"s Kc/m2: {q('SELECT COUNT(kc_m2_mesic) FROM abstrakt')}, s koncem najmu: {q('SELECT COUNT(konec) FROM abstrakt')}, "
          f"citace {q('SELECT COALESCE(SUM(overeno_citaci),0) FROM abstrakt')}/{q('SELECT COALESCE(SUM(citaci),0) FROM abstrakt')} overeno, "
          f"prumer {q('SELECT ROUND(COALESCE(AVG(sekund),0)) FROM abstrakt')} s")
    print("\n-- abstrakty (nejdriv ty, kterym nejdriv konci najem) --")
    for najc, adr, m2, kcm2, konec, ov, ci in con.execute(
            """SELECT najemce, adresa, plocha_m2, kc_m2_mesic, konec, overeno_citaci, citaci FROM abstrakt
               ORDER BY konec IS NULL, konec LIMIT 20"""):
        print(f"  {str(konec):10s} | {str(m2 or ''):>7} m2 | {str(kcm2 or ''):>6} Kc/m2 | "
              f"{(najc or '')[:28]:28s} | {(adr or '')[:35]:35s} | citace {ov}/{ci}")
    print("====================================================================")
    return 0


def ukaz(con: sqlite3.Connection, idv: str) -> int:
    """Vypise abstrakt jedne smlouvy vcetne citaci a overeni (pro kontrolu kvality)."""
    con.executescript(SCHEMA)
    r = con.execute("SELECT data FROM abstrakt WHERE id_verze=?", (idv,)).fetchone()
    s = con.execute("SELECT subjekt_nazev, predmet, COALESCE(hodnota_bez_dph, hodnota_s_dph), odkaz "
                    "FROM smlouvy WHERE id_verze=?", (idv,)).fetchone()
    if s:
        print(f"{s[0]} | {s[1]}\nhodnota v registru: {s[2]} Kc | {s[3]}")
    print(json.dumps(json.loads(r[0]), ensure_ascii=False, indent=1) if r else "abstrakt neexistuje")
    return 0
