import io
from pathlib import Path

from najemniradar import registr

NS = "http://portal.gov.cz/rejstriky/ISRS/1.2/"

DUMP = f"""<?xml version="1.0" encoding="UTF-8"?>
<dump xmlns="{NS}">
 <mesic>9</mesic><rok>2026</rok>
 <zaznam>
  <identifikator><idSmlouvy>111</idSmlouvy><idVerze>1001</idVerze></identifikator>
  <odkaz>https://smlouvy.gov.cz/smlouva/1001</odkaz>
  <casZverejneni>2026-09-10T10:00:00</casZverejneni>
  <smlouva>
   <subjekt><nazev>Ministerstvo X</nazev><ico>00000001</ico><adresa>Letenska 15, 118 00 Praha 1</adresa></subjekt>
   <smluvniStrana><nazev>Office Park a.s.</nazev><ico>12345678</ico><adresa>Na Pankraci 1, Praha 4</adresa><prijemce>1</prijemce></smluvniStrana>
   <predmet>Smlouva o nájmu kancelářských prostor v budově Na Pankráci</predmet>
   <datumUzavreni>2026-09-01</datumUzavreni>
   <hodnotaBezDph>12500000</hodnotaBezDph>
  </smlouva>
  <prilohy><priloha><nazevSouboru>smlouva.pdf</nazevSouboru><odkaz>https://smlouvy.gov.cz/smlouva/soubor/1/smlouva.pdf</odkaz></priloha></prilohy>
  <platnyZaznam>1</platnyZaznam>
 </zaznam>
 <zaznam>
  <identifikator><idSmlouvy>112</idSmlouvy><idVerze>1002</idVerze></identifikator>
  <smlouva>
   <subjekt><nazev>Obec Y</nazev><ico>00000002</ico><adresa>Brno</adresa></subjekt>
   <smluvniStrana><nazev>Jan Novak</nazev><adresa>Brno</adresa></smluvniStrana>
   <predmet>Nájemní smlouva - byt 2+1</predmet>
  </smlouva>
  <platnyZaznam>1</platnyZaznam>
 </zaznam>
 <zaznam>
  <identifikator><idSmlouvy>113</idSmlouvy><idVerze>1003</idVerze></identifikator>
  <smlouva>
   <subjekt><nazev>Urad Z</nazev><ico>00000003</ico><adresa>Praha 2</adresa></subjekt>
   <smluvniStrana><nazev>Kopirky s.r.o.</nazev><ico>87654321</ico></smluvniStrana>
   <predmet>Pronájem multifunkčních kopírek do kanceláří</predmet>
  </smlouva>
  <platnyZaznam>1</platnyZaznam>
 </zaznam>
 <zaznam>
  <identifikator><idSmlouvy>111</idSmlouvy><idVerze>1000</idVerze></identifikator>
  <smlouva>
   <subjekt><nazev>Ministerstvo X</nazev><ico>00000001</ico><adresa>Praha 1</adresa></subjekt>
   <smluvniStrana><nazev>Office Park a.s.</nazev><ico>12345678</ico></smluvniStrana>
   <predmet>Smlouva o nájmu kancelářských prostor (stara verze)</predmet>
  </smlouva>
  <platnyZaznam>0</platnyZaznam>
 </zaznam>
</dump>"""

INDEX = f"""<?xml version="1.0" encoding="UTF-8"?>
<index xmlns="{NS}">
 <dump><mesic>9</mesic><rok>2026</rok><hashDumpu algoritmus="sha256">x</hashDumpu>
  <velikostDumpu>1000</velikostDumpu><casGenerovani>2026-10-01</casGenerovani>
  <dokoncenyMesic>1</dokoncenyMesic><odkaz>https://data.smlouvy.gov.cz/dump_2026_09.xml</odkaz></dump>
 <dump><mesic>10</mesic><rok>2026</rok><velikostDumpu>500</velikostDumpu>
  <dokoncenyMesic>0</dokoncenyMesic><odkaz>https://data.smlouvy.gov.cz/dump_2026_10.xml</odkaz></dump>
</index>"""


def test_filtr():
    assert registr.je_kandidat("Smlouva o nájmu kancelářských prostor")
    assert registr.je_kandidat("Dodatek č. 3 k nájemní smlouvě - nebytové prostory")
    assert registr.je_kandidat("Pronájem administrativních prostor, Praha 4")
    assert not registr.je_kandidat("Nájemní smlouva - byt 2+1")
    assert not registr.je_kandidat("Pronájem multifunkčních kopírek do kanceláří")
    assert not registr.je_kandidat("Pronájem pozemku")
    assert not registr.je_kandidat("Dodávka kancelářských potřeb")


def test_parse_dump_a_ulozeni(tmp_path: Path):
    z = list(registr.iter_zaznamy(io.BytesIO(DUMP.encode())))
    assert len(z) == 4
    assert z[0].predmet.startswith("Smlouva o nájmu")
    assert z[0].praha and z[0].pravnicke_osoby
    assert z[0].prilohy[0]["nazev"] == "smlouva.pdf"
    assert z[0].hodnota_bez_dph == 12500000
    assert not z[1].pravnicke_osoby  # fyzicka osoba bez ICO
    assert z[3].platny is False

    p = tmp_path / "dump.xml"
    p.write_text(DUMP, encoding="utf-8")
    con = registr.open_db(tmp_path / "db.sqlite")
    n, k = registr.zpracuj_dump(con, p, "dump.xml")
    assert (n, k) == (4, 1)  # jen platna smlouva na kancelare s pravnickymi osobami
    assert registr.souhrn(con, tmp_path) == 0
    assert (tmp_path / "kandidati.csv").exists()


def test_index():
    d = registr.parse_index(INDEX.encode())
    assert [x["mesic"] for x in d] == [9, 10]
    assert d[0]["dokonceny"] and not d[1]["dokonceny"]
    assert d[1]["odkaz"].endswith("dump_2026_10.xml")


def test_vyber_mesicnich_dumpu():
    idx = f"""<?xml version="1.0"?><index xmlns="{NS}">
    """ + "".join(
        f"<dump><rok>2026</rok><mesic>{m}</mesic><velikostDumpu>1</velikostDumpu>"
        f"<odkaz>https://data.smlouvy.gov.cz/{n}</odkaz></dump>"
        for m, n in [(8, "dump_2026_08.xml"), (9, "dump_2026_09.xml"), (10, "dump_2026_10.xml"),
                     (10, "dump_2026_10_14.xml"), (10, "dump_2026_10_15.xml"), (10, "dump_2026_10_16.xml")]
    ) + "</index>"
    d = registr.parse_index(idx.encode())
    v = registr.vyber_dumpy(d, 3)
    assert [x["odkaz"].rsplit("/", 1)[-1] for x in v] == ["dump_2026_08.xml", "dump_2026_09.xml", "dump_2026_10.xml"]
    # bez mesicnich -> denni
    only_daily = [x for x in d if x["den"]]
    assert len(registr.vyber_dumpy(only_daily, 2)) == 3  # vsechny dny rijna


def test_sum_a_typy():
    assert not registr.je_kandidat("Pronájem konferenčních prostor 30 osob + služby")
    assert not registr.je_kandidat("NÁJEMNÍ SMLOUVA - krátkodobý pronájem nebytových prostor - konírna")
    assert not registr.je_kandidat("Smlouva o nájmu nebytových prostor - učebna")
    assert not registr.je_kandidat("Splátková dohoda věřitele s dlužníkem pro dluhy spojené s nájmem nebytového prostoru")
    assert registr.je_kandidat("Pronájem nebytových prostor kanceláře - v budově Přerov 2027-2030")
    assert registr.typ_smlouvy("Dohoda o ukončení Smlouvy o nájmu prostoru sloužícího podnikání") == "ukonceni"
    assert registr.typ_smlouvy("Dodatek č. 4 ke smlouvě o nájmu - úprava doby nájmu") == "dodatek"
    assert registr.typ_smlouvy("Smlouva o nájmu prostor sloužících k podnikání") == "smlouva"


def test_kdo_plati():
    z = list(registr.iter_zaznamy(io.BytesIO(DUMP.encode())))
    assert z[0].verejny_plati == 1      # protistrana prijima platbu -> ministerstvo je najemce
    assert z[0].praha_predmet is False  # Praha jen v adrese, ne v predmetu


def test_rozsah():
    d = [{"rok": r, "mesic": m, "den": 0, "odkaz": f"dump_{r}_{m:02d}.xml", "velikost": 1, "dokonceny": True}
         for r in (2017, 2018, 2019) for m in range(1, 13)]
    v = registr.vyber_rozsah(d, "2018-01", "2018-12")
    assert len(v) == 12 and v[0]["rok"] == 2018


def test_stahni_opakuje(monkeypatch, tmp_path):
    import httpx
    volani = {"n": 0}

    def fake(url, cil):
        volani["n"] += 1
        if volani["n"] < 3:
            raise httpx.ConnectError("WinError 10054")
        cil.write_text("ok")
        return cil

    monkeypatch.setattr(registr, "_stahni1", fake)
    monkeypatch.setattr(registr.time, "sleep", lambda s: None)
    assert registr.stahni("https://x", tmp_path / "a.xml").read_text() == "ok"
    assert volani["n"] == 3
