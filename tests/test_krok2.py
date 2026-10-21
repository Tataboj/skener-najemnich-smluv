import json
from pathlib import Path

import pymupdf

from najemniradar import krok2, registr

TEXT_NAJEMCE = """SMLOUVA O NÁJMU PROSTOR SLOUŽÍCÍCH PODNIKÁNÍ
Smluvní strany:
Office Park Pankrác a.s., se sídlem Na Pankráci 1, Praha 4, IČO: 123 45 678,
zastoupená ..., (dále jen „pronajímatel“)
a
Ministerstvo pokusů, se sídlem Letenská 15, Praha 1, IČO: 00000001,
zastoupené ministrem, (dále jen „nájemce“)
Článek 1 Předmět nájmu: kancelářské prostory o výměře 2 400 m2 ...
"""

TEXT_PRONAJIMATEL = """NÁJEMNÍ SMLOUVA
Městská část Praha 2, IČO 00063715, se sídlem nám. Míru 20, jako pronajímatel
a
Galerie Vintage s.r.o., IČO 87654321, jako nájemce
"""


def test_role_z_ares():
    real = {"nazev": "Office Park a.s.", "pravni_forma": "121", "nace": ["68200", "41100"]}
    kavarna = {"nazev": "Kavarna s.r.o.", "pravni_forma": "112", "nace": ["56100", "47110"]}
    obec = {"nazev": "Obec Y", "pravni_forma": "801", "nace": []}
    assert krok2.role_z_ares([real])[0] == "verejny_najemce"
    assert krok2.role_z_ares([kavarna])[0] == "verejny_pronajimatel"
    assert krok2.role_z_ares([obec])[0] == "verejny_verejny"
    assert krok2.role_z_ares([])[0] == "nejasne"


def test_parse_ares():
    d = krok2.parse_ares({"obchodniJmeno": "X a.s.", "pravniForma": "121", "czNace": ["68200"],
                          "sidlo": {"textovaAdresa": "Praha 4"}})
    assert d == {"nazev": "X a.s.", "pravni_forma": "121", "nace": ["68200"], "adresa": "Praha 4"}


def test_role_z_textu():
    assert krok2.role_z_textu(TEXT_NAJEMCE, "00000001", "Ministerstvo pokusů")[0] == "verejny_najemce"
    assert krok2.role_z_textu(TEXT_PRONAJIMATEL, "00063715", "Městská část Praha 2")[0] == "verejny_pronajimatel"
    # ICO rozdelene mezerami u protistrany nesmi zmast
    assert krok2.role_z_textu(TEXT_NAJEMCE, "12345678", "Office Park")[0] == "verejny_pronajimatel"
    assert krok2.role_z_textu("bez stran", "99999999", "")[0] == "nejasne"


def _db(tmp_path: Path):
    con = registr.open_db(tmp_path / "db.sqlite")
    con.executescript(krok2.SCHEMA)
    strany = [{"nazev": "Office Park a.s.", "ico": "12345678", "adresa": "Praha 4", "prijemce": ""}]
    prilohy = [{"nazev": "smlouva.pdf", "odkaz": "https://example.invalid/smlouva.pdf"}]
    con.execute("INSERT INTO smlouvy (id_verze, id_smlouvy, predmet, subjekt_nazev, subjekt_ico, strany_json, "
                "prilohy_json, pocet_priloh, praha, typ, datum_uzavreni, hodnota_bez_dph) "
                "VALUES('1001','111','Smlouva o nájmu kanceláří','Ministerstvo pokusů','00000001',?,?,1,1,'smlouva','2021-03-01',5e6)",
                (json.dumps(strany), json.dumps(prilohy)))
    con.execute("INSERT INTO ares VALUES('12345678','Office Park a.s.','121',?, 'Praha 4', 200, 'x')",
                (json.dumps(["68200"]),))
    con.commit()
    return con


def test_krok2_cely_tok_offline(tmp_path: Path):
    con = _db(tmp_path)
    assert krok2.krok2_ares(con, praha=False, od_roku=None, ano=True) == 0
    assert con.execute("SELECT role_ares FROM role").fetchone()[0] == "verejny_najemce"
    # PDF uz "stazene" -> zadna sit
    (tmp_path / "pdf").mkdir()
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((40, 60), "Ministerstvo pokusu, ICO: 00000001, (dale jen najemce)", fontsize=10)
    page.insert_text((40, 80), "Office Park a.s., ICO 12345678, (dale jen pronajimatel)", fontsize=10)
    page.insert_text((40, 100), "Clanek 1 Predmet najmu: kancelarske prostory o vymere 2 400 m2 v budove "
                                "na Pankraci, doba najmu 5 let od 1. 4. 2021.", fontsize=8)
    doc.save(tmp_path / "pdf" / "1001_0.pdf")
    assert krok2.krok2_pdf(con, tmp_path, limit=10, role="verejny_najemce", praha=True, min_hodnota=0) == 0
    row = con.execute("SELECT stran, sken, role_text FROM texty").fetchone()
    assert row[0] == 1 and row[2] == "verejny_najemce"
