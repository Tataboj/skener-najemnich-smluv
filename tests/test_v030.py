import json
from pathlib import Path

from najemniradar import krok2, llm, registr, vyber


def test_skore_ministerstvo_vs_mestska_cast():
    s1, d1 = vyber.skoruj("Nájem kancelářských prostor pro složky MO", "Česká republika - Ministerstvo obrany",
                          "325", 1.0e9, [["68200"]])
    s2, _ = vyber.skoruj("Smlouva o nájmu prostoru sloužícího podnikání - kavárna", "Městská část Praha 2",
                         "811", 300000, [["56100", "68200"]])
    s3, _ = vyber.skoruj("Podnájem prostor a zajištění služeb – koncert", "Ministerstvo zdravotnictví",
                         "325", 50000, [[]])
    assert s1 >= 8 and any("ministerstvo" in x for x in d1)
    assert s2 < 0
    assert s3 < 4   # koncert neni kancelar


def test_prisne_pravidlo_ignoruje_rekapitulaci():
    t = "Ministerstvo X, IČO 00000001, se sídlem Praha. Dne 16. 11. 1993 byla mezi pronajímatelem a ..."
    assert krok2.role_z_textu(t, "00000001", "Ministerstvo X")[0] == "nejasne"
    t2 = "Ministerstvo X, IČO 00000001, se sídlem Praha, jako nájemce na straně druhé"
    assert krok2.role_z_textu(t2, "00000001", "Ministerstvo X")[0] == "verejny_najemce"


SMLOUVA = """SMLOUVA O NÁJMU
Office Park a.s., IČO 12345678, (dále jen „pronajímatel“)
Ministerstvo X, IČO 00000001, (dále jen „nájemce“)

Čl. 1 Předmět nájmu
Pronajímatel přenechává nájemci kancelářské prostory o výměře 2 400 m2 ve 3. podlaží budovy Na Pankráci 1, Praha 4.

Čl. 2 Nájemné
Nájemné činí 380 Kč za m2 a měsíc, tj. 10 944 000 Kč ročně bez DPH. Nájemné bude každoročně zvyšováno o míru inflace.

Čl. 3 Doba nájmu
Nájem se sjednává na dobu určitou 5 let ode dne 1. 7. 2022, tj. do 30. 6. 2027.

Čl. 4 Výpověď
Výpovědní lhůta činí 6 měsíců.

Čl. 5 Ostatní
Tato smlouva je vyhotovena ve čtyřech stejnopisech. Smluvní strany prohlašují, že si smlouvu přečetly.
"""


def test_vyber_kontextu_obsahuje_klicove_casti():
    k = llm.vyber_kontext(SMLOUVA, max_znaku=900)
    assert "380 Kč" in k and "2 400 m2" in k and "30. 6. 2027" in k


def test_overeni_citaci_a_dopocet():
    d = {"plocha_m2": 2400, "najemne_rocne_kc": 10944000, "najemne_kc_m2_mesic": None,
         "citace_plocha": "kancelářské prostory o výměře 2 400 m2",
         "citace_najemne": "Nájemné činí 999 Kč za m2",  # vymyslena -> neoverena
         "citace_doba": None, "citace_vypoved": "Výpovědní lhůta činí 6 měsíců."}
    z = llm.zpracuj_abstrakt(d, SMLOUVA)
    assert z["_kc_m2_mesic"] == 380.0
    assert z["_overeni"] == {"citace_plocha": True, "citace_najemne": False, "citace_vypoved": True}


def test_krok3_s_falesnou_ollamou(tmp_path: Path, monkeypatch):
    con = registr.open_db(tmp_path / "db.sqlite")
    con.executescript(krok2.SCHEMA)
    con.executescript(vyber.SCHEMA)
    con.executescript(llm.SCHEMA)
    con.execute("INSERT INTO smlouvy (id_verze, subjekt_nazev, subjekt_ico, predmet) VALUES('1','Ministerstvo X','00000001','najem')")
    con.execute("INSERT INTO smlouvy (id_verze, subjekt_nazev, subjekt_ico, predmet) VALUES('2','Úřad Y','00000002','najem')")
    for idv, role in (("1", "verejny_najemce"), ("2", "nejasne")):
        p = tmp_path / f"{idv}.txt"
        p.write_text(SMLOUVA, encoding="utf-8")
        con.execute("INSERT INTO texty VALUES(?,1,2,?,0,?,'',?, '')", (idv, len(SMLOUVA), role, str(p)))
        con.execute("INSERT INTO shortlist VALUES(?,8,'[]')", (idv,))
    con.commit()

    def fake_chat(system, user, schema, model=None, num_ctx=0):
        if "role_verejneho_subjektu" in schema["properties"]:
            return {"role_verejneho_subjektu": "najemce", "je_kancelar": True,
                    "citace": "Ministerstvo X, IČO 00000001, (dále jen „nájemce“)"}
        return {"najemce": "Ministerstvo X", "pronajimatel": "Office Park a.s.", "adresa_prostor": "Na Pankráci 1, Praha 4",
                "plocha_m2": 2400, "najemne_rocne_kc": 10944000, "najemne_kc_m2_mesic": 380,
                "zacatek_najmu": "2022-07-01", "konec_najmu": "2027-06-30", "doba_neurcita": False,
                "vypovedni_lhuta_mesicu": 6, "opce_prodlouzeni": None, "indexace": "inflace",
                "ucel_uzivani": "kancelare", "citace_plocha": "o výměře 2 400 m2",
                "citace_najemne": "Nájemné činí 380 Kč za m2 a měsíc", "citace_doba": "do 30. 6. 2027",
                "citace_vypoved": "Výpovědní lhůta činí 6 měsíců"}

    monkeypatch.setattr(llm, "ollama_chat", fake_chat)
    monkeypatch.setattr(llm, "ollama_ok", lambda m: None)
    assert llm.krok3(con, "vse", 10, "test") == 0
    assert con.execute("SELECT role, overeno FROM llm_role").fetchone() == ("najemce", 1)
    rows = con.execute("SELECT id_verze, konec, kc_m2_mesic, overeno_citaci, citaci, kc_m2_zdroj FROM abstrakt ORDER BY 1").fetchall()
    assert rows == [("1", "2027-06-30", 380.0, 4, 4, "smlouva"), ("2", "2027-06-30", 380.0, 4, 4, "smlouva")]
    assert llm.ukaz(con, "1") == 0


def test_vyber_shortlist_offline(tmp_path: Path):
    con = registr.open_db(tmp_path / "db.sqlite")
    con.executescript(krok2.SCHEMA)
    strany = json.dumps([{"nazev": "Office Park a.s.", "ico": "12345678"}])
    con.execute("INSERT INTO smlouvy (id_verze, predmet, subjekt_nazev, subjekt_ico, hodnota_bez_dph, strany_json, typ, praha) "
                "VALUES('1','Nájem kancelářských prostor','Ministerstvo X','00000001',5e7,?,'smlouva',1)", (strany,))
    con.execute("INSERT INTO smlouvy (id_verze, predmet, subjekt_nazev, subjekt_ico, hodnota_bez_dph, strany_json, typ, praha) "
                "VALUES('2','Pronájem prostor - kavárna','Městská část Praha 2','00063715',2e5,?,'smlouva',1)", (strany,))
    for ico, forma, nace in (("00000001", "325", []), ("00063715", "811", []), ("12345678", "121", ["68200"])):
        con.execute("INSERT INTO ares VALUES(?,?,?,?,'',200,'x')", (ico, "x", forma, json.dumps(nace)))
    con.commit()
    assert vyber.vyber(con, 10, 4, True, True) == 0
    assert [r[0] for r in con.execute("SELECT id_verze FROM shortlist")] == ["1"]


def test_odhad_kc_m2_z_hodnoty_smlouvy():
    d = {"plocha_m2": 1000, "najemne_rocne_kc": None, "najemne_mesicne_kc": None, "najemne_kc_m2_mesic": None,
         "zacatek_najmu": "2022-01-01", "konec_najmu": "2026-12-31"}
    z = llm.zpracuj_abstrakt(d, "x", hodnota_smlouvy=24_000_000)   # 60 mesicu
    assert z["_kc_m2_mesic"] == 400.0 and z["_kc_m2_zdroj"] == "odhad z hodnoty smlouvy"
    d2 = {**d, "najemne_mesicne_kc": 350_000}
    z2 = llm.zpracuj_abstrakt(d2, "x")
    assert z2["_kc_m2_mesic"] == 350.0 and z2["_kc_m2_zdroj"] == "dopocet"
