"""Krok 4b: webove demo - jedna HTML stranka s vlozenymi daty (otevre se v prohlizeci, nic se neinstaluje)."""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from . import __version__, fakta, pravidla

TEMPLATE = Path(__file__).with_name("demo_template.html")
POPIS = "Komu končí nájem kanceláří, kolik platí za m² a kdy může vypovědět. Nájmy úřadů z Registru smluv, každý údaj s citací."


def data_dema(db: Path, text_dir: Path, text_str_dir: Path | None = None, dnes: date | None = None) -> dict:
    dnes = dnes or date.today()
    con = sqlite3.connect(db)
    najmy = fakta.sestav(con, text_dir, dnes, text_str_dir)
    con.row_factory = None
    q = lambda s: con.execute(s).fetchone()[0]  # noqa: E731
    tab = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    meta = {
        "verze": __version__, "dnes": dnes.isoformat(),
        "zaznamu": q("SELECT COALESCE(SUM(zaznamu),0) FROM dumpy") if "dumpy" in tab else None,
        "kandidatu": q("SELECT COALESCE(SUM(kandidatu),0) FROM dumpy") if "dumpy" in tab else None,
        "mesicu": q("SELECT COUNT(*) FROM dumpy") if "dumpy" in tab else None,
        "od": q("SELECT MIN(substr(odkaz, -11, 7)) FROM dumpy") if "dumpy" in tab else None,
        "shortlist": q("SELECT COUNT(*) FROM shortlist"),
        "s_textem": q("SELECT COUNT(*) FROM shortlist JOIN texty USING(id_verze) WHERE sken=0 AND znaku>300"),
        "metadat": q("SELECT COUNT(*) FROM meta WHERE chyba IS NULL OR chyba=''") if "meta" in tab else 0,
        "navazanych": q("SELECT COUNT(*) FROM meta WHERE navazany IS NOT NULL AND navazany!=''") if "meta" in tab else 0,
        "abstraktu": q("SELECT COUNT(*) FROM abstrakt") if "abstrakt" in tab else 0,
        "sek_abstrakt": q("SELECT ROUND(AVG(sekund)) FROM abstrakt") if "abstrakt" in tab else None,
        "model": "qwen3:4b-instruct (Ollama) na notebooku bez GPU",
        "kurz_eur": pravidla.KURZ_EUR, "kurz_datum": pravidla.KURZ_DATUM,
    }
    con.close()
    return {"meta": fakta.statistiky(najmy, meta), "najmy": najmy}


def vytvor(db: Path, text_dir: Path, out: Path, text_str_dir: Path | None = None, dnes: date | None = None,
           fragment: bool = False, og_image: str | None = None) -> Path:
    """fragment=True: bez <!doctype>/<head> (pro publikaci, kde se kostra doplni); jinak samostatny soubor."""
    data = data_dema(db, text_dir, text_str_dir, dnes)
    js = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", js)
    if not fragment:
        hlava, telo = html.split("</style>", 1)  # <title>, fonty a styly patri do <head>
        html = ('<!doctype html>\n<html lang="cs">\n<head>\n<meta charset="utf-8">\n'
                '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
                f'<meta name="description" content="{POPIS}">\n'
                '<meta property="og:title" content="Skener nájemních smluv">\n'
                f'<meta property="og:description" content="{POPIS}">\n'
                + (f'<meta property="og:image" content="{og_image}">\n<meta name="twitter:card" content="summary_large_image">\n'
                   if og_image else "")
                + '<style>[hidden]{display:none!important}body{margin:0}</style>\n'
                + hlava + "</style>\n</head>\n<body>" + telo + "\n</body>\n</html>\n")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Demo: {out.resolve()}  ({data['meta']['najmu']} nájmů, {data['meta']['dokumentu']} dokumentů)")
    return out
