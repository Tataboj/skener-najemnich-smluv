import base64
import json
from pathlib import Path

import pymupdf

from najemniradar import export, krok2, metadata, registr, vyber


def _pdf_bytes(texty: list[str]) -> bytes:
    doc = pymupdf.open()
    for t in texty:
        doc.new_page().insert_text((40, 60), t, fontsize=10)
    return doc.tobytes()


def _xml(idv: str, ids: str, nav: str, hod: str, pdf: bytes) -> bytes:
    b64 = base64.b64encode(pdf).decode()
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<zaznam xmlns="http://portal.gov.cz/rejstriky/ISRS/1.2/"><data>
 <identifikator><idSmlouvy>{ids}</idSmlouvy><idVerze>{idv}</idVerze></identifikator>
 <smlouva><subjekt><nazev>GFR</nazev><ico>72080043</ico></subjekt>
  <smluvniStrana><nazev>Pronajimatel s.r.o.</nazev><ico>73006955</ico></smluvniStrana>
  <predmet>Dodatek c. 2</predmet><datumUzavreni>2023-03-14</datumUzavreni>
  <cisloSmlouvy>SML:18/3201/0004/02D</cisloSmlouvy><hodnotaBezDph>{hod}</hodnotaBezDph>
  <navazanyZaznam>{nav}</navazanyZaznam></smlouva>
 <prilohy><priloha><data>{b64}</data><hash algoritmus="sha256">abc</hash><nazevSouboru>d.pdf</nazevSouboru></priloha></prilohy>
 <platnyZaznam>1</platnyZaznam></data></zaznam>""".encode()


def test_parse_meta_xml():
    m, soubory = metadata.parse_meta_xml(_xml("23780769", "22169909", "6746275", "-138046.5", _pdf_bytes(["a"])))
    assert m["navazany"] == "6746275" and m["hodnota"] == -138046.5 and m["cislo_smlouvy"].startswith("SML:18")
    assert m["strany"][0]["ico"] == "73006955"
    assert soubory[0][0] == "d.pdf" and soubory[0][1][:4] == b"%PDF"


def test_krok5_offline(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    con = registr.open_db(data / "najemniradar.sqlite")
    con.executescript(krok2.SCHEMA)
    con.executescript(vyber.SCHEMA)
    strany = json.dumps([{"nazev": "Pronajimatel s.r.o.", "ico": "73006955"}])
    for idv, ids, typ in (("23780769", "22169909", "dodatek"), ("7285887", "6746275", "smlouva")):
        con.execute("INSERT INTO smlouvy (id_verze, id_smlouvy, subjekt_ico, strany_json, typ, predmet) VALUES(?,?,?,?,?,'x')",
                    (idv, ids, "72080043", strany, typ))
    con.execute("INSERT INTO shortlist VALUES('23780769', 8, '[]')")
    con.commit()
    xmls = {
        "23780769": _xml("23780769", "22169909", "6746275", "-138046.5", _pdf_bytes(["Dodatek str 1", "str 2"])),
        "7285887": _xml("7285887", "6746275", "", "9573774", _pdf_bytes(["Smlouva na dobu urcitou do 30. 11. 2023"])),
    }
    monkeypatch.setattr(metadata, "_stahni_meta", lambda c, idv, pauza: (idv, 200, xmls[idv], ""))
    assert metadata.krok5(con, data, vlaken=1, pauza=0) == 0
    assert {r[0] for r in con.execute("SELECT id_verze FROM meta")} == {"23780769", "7285887"}
    t = (data / "text_str" / "23780769.txt").read_text(encoding="utf-8")
    assert "[[STRANA 1]]" in t and "[[STRANA 2]]" in t
    z = export.export(data)
    import zipfile
    with zipfile.ZipFile(z) as f:
        names = f.namelist()
        m = json.loads(f.read("manifest.json"))
    assert "text_str/23780769.txt" in names and m["pocty_radku"]["meta"] == 2
