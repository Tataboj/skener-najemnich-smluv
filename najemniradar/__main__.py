"""CLI: python -m najemniradar <prikaz>"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="najemniradar")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("version")
    k1 = sub.add_parser("krok1", help="stahni dumpy Registru smluv a vyber najmy kancelari")
    k1.add_argument("--mesicu", type=int, default=6)
    k1.add_argument("--od", help="RRRR-MM, napr. 2018-01 (mesicni dumpy od)")
    k1.add_argument("--do", help="RRRR-MM, napr. 2023-12 (mesicni dumpy do)")
    k1.add_argument("--ano", action="store_true", help="nepta se na potvrzeni")
    k1.add_argument("--ponechat", action="store_true", help="nemaze stazene dumpy")
    sub.add_parser("souhrn", help="vypise souhrn z databaze")
    k2 = sub.add_parser("krok2", help="kdo je najemce: ARES + PDF + pravidla")
    k2.add_argument("faze", choices=["ares", "pdf", "souhrn"])
    k2.add_argument("--praha", action="store_true", help="jen smlouvy s prazskou adresou")
    k2.add_argument("--od-roku", help="jen smlouvy uzavrene od roku (napr. 2018)")
    k2.add_argument("--role", default="verejny_najemce", help="pro faze pdf (verejny_najemce/nejasne/...)")
    k2.add_argument("--limit", type=int, default=50, help="pro faze pdf: max. pocet smluv")
    k2.add_argument("--min-hodnota", type=float, default=0, help="pro faze pdf: min. hodnota smlouvy v Kc")
    k2.add_argument("--ano", action="store_true")
    kb = sub.add_parser("krok2b", help="uzky vyber kancelarskych najmu, kde stat plati")
    kb.add_argument("faze", choices=["vyber", "pdf", "prepocitej", "znovu"])
    kb.add_argument("--limit", type=int, default=300)
    kb.add_argument("--min-skore", type=int, default=4)
    kb.add_argument("--praha", action="store_true")
    kb.add_argument("--ano", action="store_true")
    k3 = sub.add_parser("krok3", help="lokalni LLM: role + abstrakt smlouvy")
    k3.add_argument("faze", choices=["role", "abstrakt", "vse", "souhrn", "ukaz"])
    k3.add_argument("id", nargs="?", help="pro 'ukaz': id_verze smlouvy")
    k3.add_argument("--limit", type=int, default=80, help="max. pocet abstraktu")
    k3.add_argument("--limit-role", type=int, default=120, help="max. pocet LLM rozhodnuti o roli")
    k3.add_argument("--model", default="qwen3:4b-instruct")
    kn = sub.add_parser("noc", help="vse najednou bez dotazu: vyber -> PDF -> LLM role -> LLM abstrakt")
    kn.add_argument("--limit", type=int, default=300)
    kn.add_argument("--praha", action="store_true")
    kn.add_argument("--model", default="qwen3:4b-instruct")
    sub.add_parser("export", help="balicek dat pro vyvoj (jen cteni, lze spustit soubezne s davkou)")
    sub.add_parser("demo", help="vytvori webove demo data/demo/skener-najemnich-smluv.html")
    k5 = sub.add_parser("krok5", help="metadata XML z registru + prilohy + texty s cisly stran")
    k5.add_argument("--vlaken", type=int, default=2)
    a = ap.parse_args(argv)

    data = Path("data")
    if a.cmd == "version":
        print(f"NajemniRadar {__version__}")
        return 0
    if a.cmd == "demo":
        from .demo import vytvor
        vytvor(data / "najemniradar.sqlite", data / "text", data / "demo" / "skener-najemnich-smluv.html",
               data / "text_str")
        return 0
    if a.cmd == "krok5":
        from . import krok2, metadata, registr, vyber
        con = registr.open_db(data / "najemniradar.sqlite")
        con.executescript(krok2.SCHEMA)
        con.executescript(vyber.SCHEMA)
        return metadata.krok5(con, data, a.vlaken)
    if a.cmd == "export":
        from .export import export
        export(data)
        return 0
    from . import registr
    if a.cmd == "krok1":
        return registr.krok1(a.mesicu, data, a.ano, a.ponechat, a.od, a.do)
    if a.cmd == "krok2":
        from . import krok2
        con = registr.open_db(data / "najemniradar.sqlite")
        if a.faze == "ares":
            return krok2.krok2_ares(con, a.praha, a.od_roku, a.ano)
        if a.faze == "pdf":
            return krok2.krok2_pdf(con, data, a.limit, a.role, a.praha, a.min_hodnota)
        return krok2.souhrn2(con)
    if a.cmd in ("krok2b", "krok3", "noc"):
        from . import krok2, llm, vyber
        con = registr.open_db(data / "najemniradar.sqlite")
        con.executescript(krok2.SCHEMA)
        con.executescript(vyber.SCHEMA)
        if a.cmd == "krok2b":
            if a.faze == "vyber":
                return vyber.vyber(con, a.limit, a.min_skore, a.praha, a.ano)
            if a.faze == "pdf":
                return vyber.pdf_shortlist(con, data, a.limit)
            if a.faze == "znovu":
                n = vyber.znovu_bez_textu(con)
                print(f"K opakovani: {n} smluv bez textu (Word / chyba serveru)")
                return vyber.pdf_shortlist(con, data, a.limit)
            print("prepocitano:", krok2.prepocitej_role_textu(con))
            return 0
        if a.cmd == "krok3":
            if a.faze == "souhrn":
                return llm.souhrn3(con)
            if a.faze == "ukaz":
                return llm.ukaz(con, a.id or "")
            return llm.krok3(con, a.faze, a.limit, a.model, a.limit_role)
        t0 = __import__("time").time()
        chyba = llm.ollama_ok(a.model)
        if chyba:
            print(chyba)
            return 1
        vyber.vyber(con, a.limit, 4, a.praha, True)
        krok2.prepocitej_role_textu(con)
        vyber.pdf_shortlist(con, data, a.limit)
        r = llm.krok3(con, "vse", a.limit, a.model)
        print(f"NOC hotova za {(__import__('time').time()-t0)/60:.0f} min")
        return r
    if a.cmd == "souhrn":
        return registr.souhrn(registr.open_db(data / "najemniradar.sqlite"), data)
    return 1


if __name__ == "__main__":
    sys.exit(main())
