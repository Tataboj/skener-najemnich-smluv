"""v0.6.0: platne zneni (retezce), overeni citaci, strana + clanek, kriticke datum, doporucena sluzba."""
import json
import sqlite3
from datetime import date
from pathlib import Path

from najemniradar import fakta, pravidla


def test_ctvrtletni_najemne():
    r = pravidla.extrahuj("Předmět nájmu o celkové výměře 100 m2. Nájemné bude hrazeno čtvrtletně ve výši 45 000,- Kč bez DPH.")
    assert r["kc_m2_mesic"] == 150.0 and "ctvrtletni" in r["kc_m2_zdroj"]


def test_opce_a_indexace():
    t = ("Nájemce je oprávněn uplatnit opční právo tak, že pronajímateli písemně oznámí nejpozději 9 měsíců před uplynutím "
         "doby nájmu, že má zájem o prodloužení. Nájemné bude každoročně upraveno o míru inflace.")
    r = pravidla.extrahuj(t)
    assert r["opce"] and r["opce_lhuta_mesicu"] == 9
    assert r["indexace"]


def test_adresa_prostor_ne_sidlo():
    t = ("Pronajímatel: ABC s.r.o., se sídlem Dlouhá 1, 110 00 Praha 1. Předmětem nájmu jsou prostory v budově "
         "na adrese Sokolovská 394/17, 186 00 Praha 8.")
    assert pravidla.extrahuj(t)["adresa"].startswith("Sokolovská 394/17")


def test_strana_a_clanek():
    d = pravidla.Dokument("[[STRANA 1]]\nČlánek I.\nÚvod\n[[STRANA 2]]\nČlánek IV.\nDoba nájmu\nNájem se sjednává na dobu určitou do 30. 11. 2023.")
    m = d.misto("na dobu určitou do 30. 11. 2023")
    assert m == {"str": 2, "cl": "IV", "nalezeno": True}


def test_hodnota_musi_byt_v_citaci():
    assert pravidla.hodnota_v_citaci("2023-11-30", "do 30. 11. 2023", "datum")
    assert not pravidla.hodnota_v_citaci("2029-11-30", "do 30. 11. 2023", "datum")
    assert pravidla.hodnota_v_citaci(6, "výpovědní doba činí šest měsíců", "mesice")
    assert not pravidla.hodnota_v_citaci(1000.0, "výpovědní doba činí šest měsíců", "plocha")
    assert pravidla.hodnota_v_citaci(1511.0, "o výměře 1 511 m2", "plocha")


def test_model_bez_overene_citace_je_varovani():
    D = pravidla.Dokument("Výpovědní doba činí tři měsíce.")
    p = fakta._slouc_pole("vypoved", "mesice", None, None, 12, "Výpovědní doba činí tři měsíce.", D)
    assert p["v"] == 12 and not p["ok"]
    p = fakta._slouc_pole("vypoved", "mesice", 3, "Výpovědní doba činí tři měsíce", 3, "Výpovědní doba činí tři měsíce.", D)
    assert p["ok"] and p["zdroj"] == "shoda"


def _db(tmp: Path) -> sqlite3.Connection:
    con = sqlite3.connect(tmp / "t.sqlite")
    con.executescript("""
      CREATE TABLE smlouvy(id_smlouvy, id_verze, odkaz, cas_zverejneni, datum_uzavreni, cislo_smlouvy, predmet, hodnota_bez_dph,
        hodnota_s_dph, subjekt_nazev, subjekt_ico, subjekt_adresa, strany_json, prilohy_json, pocet_priloh, praha, dodatek, dump,
        praha_predmet, verejny_plati, typ);
      CREATE TABLE shortlist(id_verze, skore, duvody);
      CREATE TABLE texty(id_verze, souboru, stran, znaku, sken, role_text, duvod, cesta, chyba);
      CREATE TABLE llm_role(id_verze, role, je_kancelar, citace, overeno, sekund, model);
      CREATE TABLE meta(id_verze, id_smlouvy, navazany, cislo_smlouvy, hodnota, datum_uzavreni, predmet, subjekt_ico, strany_json,
        prilohy_json, http, chyba, cas);
      CREATE TABLE ares(ico, nazev, pravni_forma, nace, adresa, http, cas);""")
    strany = json.dumps([{"nazev": "Jan Novák", "ico": "11111111"}])
    rows = [("100", "1", "2018-11-06", "smlouva", 9_000_000, None, "Nájem kanceláří Praha 8"),
            ("200", "2", "2023-03-14", "smlouva", -138_046, "100", "Dodatek č. 2 – snížení rozsahu")]
    for ids, idv, dat, typ, hod, nav, pred in rows:
        con.execute("INSERT INTO smlouvy(id_smlouvy,id_verze,odkaz,datum_uzavreni,predmet,hodnota_bez_dph,subjekt_nazev,subjekt_ico,"
                    "strany_json,prilohy_json,typ) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (ids, idv, f"https://smlouvy.gov.cz/smlouva/{idv}", dat, pred, hod, "Úřad X", "72080043", strany, "[]", typ))
        con.execute("INSERT INTO meta(id_verze,id_smlouvy,navazany,hodnota,strany_json) VALUES (?,?,?,?,?)", (idv, ids, nav, hod, strany))
    con.execute("INSERT INTO shortlist VALUES ('1', 9, '')")
    con.execute("INSERT INTO texty(id_verze, znaku, sken, role_text) VALUES ('1', 5000, 0, 'verejny_najemce')")
    con.execute("INSERT INTO ares(ico, nazev, pravni_forma) VALUES ('11111111', 'Jan Novák', '101')")
    con.commit()
    (tmp / "ts").mkdir()
    pad = " Ostatní ustanovení smlouvy." * 20
    (tmp / "ts" / "1.txt").write_text(
        "[[STRANA 1]]\nČlánek II.\nNájem se sjednává na dobu určitou do 31. 12. 2027. Výpovědní doba činí šest měsíců. "
        "Předmět nájmu o celkové výměře 1 511 m2. Pronajímatel Jan Novák." + pad, encoding="utf-8")
    (tmp / "ts" / "2.txt").write_text("[[STRANA 1]]\nDodatek č. 2. Smluvní strany se dohodly na snížení rozsahu předmětu nájmu "
                                     "o místnosti 319 a 321. Předmět nájmu o celkové výměře 1 076 m2." + pad, encoding="utf-8")
    return con


def test_retezec_najem_dnes_kriticke_sluzba_anonymizace(tmp_path: Path):
    con = _db(tmp_path)
    najmy = fakta.sestav(con, tmp_path / "text", date(2026, 10, 21), tmp_path / "ts")
    assert len(najmy) == 1
    n = najmy[0]
    assert [d["stitek"] for d in n["dokumenty"]] == ["SMLOUVA", "DODATEK"]   # navazany -> dodatek
    assert n["plocha"]["v"] == 1076.0 and n["plocha"]["doc"] == "2"          # platne zneni z dodatku
    assert n["plocha"]["puvodni"]["v"] == 1511.0
    assert n["konec"]["v"] == "2027-12-31" and n["konec"]["ok"]
    assert n["kriticke"] == "2027-06-30"                                     # konec - 6 mesicu vypovedi
    assert n["stav"] == "konci_24m" and n["sluzba"] == "relokace"
    assert n["pronajimatel"][0]["fo"] and "Novák" not in json.dumps(n, ensure_ascii=False)


def test_adresa_konkretni_a_hledani():
    assert fakta.je_konkretni("Kodaňská 1441/46, 100 10 Praha 10")
    assert not fakta.je_konkretni("RO Praha - město")
    assert not fakta.je_konkretni("2. patře Budovy o celkové výměře 75,4 m2")
    assert fakta.klicova_slova("710300 - SoD 00.03-V-9 - Nájem prostor TV Žižkov", "Praha") == "TV Žižkov"
    assert fakta.klicova_slova("Dodatek č.1 ke smlouvě o nájmu nebytových prostor", "Praha") is None
    assert fakta.ctvrt_prahy("Nájem prostor TV Žižkov") == "Praha 3"
    assert fakta.lokalita("RC Brno Poštovská SE, Brněnská 509/11a, 682 01 Vyškov")[0] == "Vyškov"
    assert fakta.adresa_z_predmetu("Pronájem kanceláří pracoviště Olomouc,Mišákova", "Olomouc") == "Mišákova, Olomouc"
