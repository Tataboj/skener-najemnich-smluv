import json
import zipfile
from pathlib import Path

from najemniradar import export, krok2, llm, registr, vyber


def test_export_jen_cteni(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    con = registr.open_db(data / "najemniradar.sqlite")
    for s in (krok2.SCHEMA, vyber.SCHEMA, llm.SCHEMA):
        con.executescript(s)
    strany = json.dumps([{"nazev": "Office Park a.s.", "ico": "12345678"}])
    for idv, typ in (("1", "smlouva"), ("2", "dodatek"), ("3", "smlouva")):
        ico = "00000001" if idv != "3" else "99999999"
        con.execute("INSERT INTO smlouvy (id_verze, subjekt_ico, strany_json, typ, predmet) VALUES(?,?,?,?,'x')",
                    (idv, ico, strany, typ))
    (data / "text").mkdir()
    (data / "text" / "1.txt").write_text("smlouva", encoding="utf-8")
    con.execute("INSERT INTO shortlist VALUES('1', 9, '[]')")
    con.execute("INSERT INTO texty VALUES('1',1,1,7,0,'verejny_najemce','',?, '')", (str(data / "text" / "1.txt"),))
    con.execute("INSERT INTO ares VALUES('12345678','Office Park','121','[\"68200\"]','',200,'x')")
    con.commit()
    con.close()
    before = (data / "najemniradar.sqlite").read_bytes()
    z = export.export(data)
    assert (data / "najemniradar.sqlite").read_bytes() == before  # zdroj nezmenen
    with zipfile.ZipFile(z) as f:
        names = f.namelist()
        m = json.loads(f.read("manifest.json"))
    assert "export.sqlite" in names and "text/1.txt" in names and "souhrn.txt" in names
    assert m["pocty_radku"]["smlouvy"] == 2      # shortlist + dodatek tehoz zverejnovatele, ne cizi
    assert m["pocty_radku"]["ares"] == 1
