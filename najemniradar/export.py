"""Export pro vyvoj mimo tvuj PC (jen pro cteni - lze spustit, i kdyz bezi davka v jinem okne).

Obsah ZIPu:
  manifest.json  - verze aplikace, cas, ID exportu, pocty radku, stav databaze (zda se prave meni)
  souhrn.txt     - souhrn kroku 3 v okamziku exportu
  export.sqlite  - shortlist + vsechny smlouvy tychz zverejnovatelu (kvuli dodatkum) + texty/role/abstrakty
  text/*.txt     - texty smluv ze shortlistu
Nazev: NR_export_<verze>_<RRRRMMDD-HHMM>_<id>.zip  -> kazdy export je jednoznacne dohledatelny.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sqlite3
import time
import zipfile
from pathlib import Path

from . import __version__

TABULKY_CELE = ["shortlist", "texty", "llm_role", "abstrakt", "dumpy", "meta", "text_str"]


def _tabulky(con: sqlite3.Connection, schema: str = "main") -> set[str]:
    return {r[0] for r in con.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table'")}


def export(data_dir: Path) -> Path:
    src = data_dir / "najemniradar.sqlite"
    if not src.exists():
        raise SystemExit(f"Databaze {src} neexistuje.")
    out_dir = data_dir / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "_export.sqlite"
    tmp.unlink(missing_ok=True)

    mtime = src.stat().st_mtime
    vek_min = (time.time() - mtime) / 60
    con = sqlite3.connect(str(tmp), uri=True)  # uri=True: ATTACH umi 'file:///C:/...?mode=ro' (jen cteni)
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("ATTACH DATABASE ? AS src", (src.resolve().as_uri() + "?mode=ro",))
    have = _tabulky(con, "src")
    for t in TABULKY_CELE:
        if t in have:
            con.execute(f"CREATE TABLE {t} AS SELECT * FROM src.{t}")
    # smlouvy: shortlist + vsechny kandidaty tychz zverejnovatelu (dodatky, ukonceni, nove smlouvy)
    meta_cond = " OR id_verze IN (SELECT id_verze FROM src.meta)" if "meta" in have else ""
    con.execute(f"""CREATE TABLE smlouvy AS SELECT * FROM src.smlouvy WHERE subjekt_ico IN
                   (SELECT s.subjekt_ico FROM src.shortlist l JOIN src.smlouvy s USING(id_verze)){meta_cond}""")
    if "role" in have:
        con.execute("CREATE TABLE role AS SELECT * FROM src.role WHERE id_verze IN (SELECT id_verze FROM smlouvy)")
    if "ares" in have:
        con.execute("""CREATE TABLE ares AS SELECT * FROM src.ares WHERE ico IN
                       (SELECT subjekt_ico FROM smlouvy) OR ico IN
                       (SELECT json_extract(j.value, '$.ico') FROM smlouvy, json_each(smlouvy.strany_json) j)""")
    con.commit()
    pocty = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in sorted(_tabulky(con))}
    modely = sorted({r[0] for t in ("llm_role", "abstrakt") if t in pocty
                     for r in con.execute(f"SELECT DISTINCT model FROM {t}")})
    texty = [Path(r[0]) for r in con.execute("SELECT cesta FROM texty") if r[0]] if "texty" in pocty else []
    texty_str = [Path(r[0]) for r in con.execute("SELECT cesta FROM text_str") if r[0]] if "text_str" in pocty else []
    # smlouvy: doplnit puvodni smlouvy dohledane v kroku 5 (nemusi byt mezi smlouvami techz zverejnovatelu)
    con.execute("DETACH DATABASE src")
    con.close()

    # souhrn z exportovane kopie
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        from .llm import souhrn3
        c2 = sqlite3.connect(tmp)
        souhrn3(c2)
        c2.close()

    cas = time.strftime("%Y%m%d-%H%M")
    eid = hashlib.sha1(f"{cas}{json.dumps(pocty)}{mtime}".encode()).hexdigest()[:6]
    manifest = {
        "export_id": eid, "verze_aplikace": __version__, "cas_exportu": time.strftime("%Y-%m-%d %H:%M:%S"),
        "databaze_naposledy_zmenena": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)),
        "databaze_se_prave_meni": vek_min < 5,
        "poznamka": ("Databaze se zmenila pred mene nez 5 min - nejspis bezi davka; export je PRUBEZNY."
                     if vek_min < 5 else "Databaze v klidu; export je UPLNY."),
        "pocty_radku": pocty, "modely": modely, "textu": len(texty),
    }
    zip_path = out_dir / f"NR_export_v{__version__}_{cas}_{eid}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        z.writestr("souhrn.txt", buf.getvalue())
        z.write(tmp, "export.sqlite")
        for p in texty:
            if p.exists():
                z.write(p, f"text/{p.name}")
        for p in texty_str:
            if p.exists():
                z.write(p, f"text_str/{p.name}")
    tmp.unlink(missing_ok=True)
    mb = zip_path.stat().st_size / 1e6
    print(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"\nEXPORT: {zip_path.resolve()}  ({mb:.1f} MB)\nTento soubor mi posli.")
    return zip_path
