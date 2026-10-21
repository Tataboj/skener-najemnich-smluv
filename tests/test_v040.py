import json
import zipfile
from datetime import date
from pathlib import Path

from najemniradar import demo, fakta, krok2, pravidla

DOC = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
       '<w:body><w:p><w:r><w:t>Nájem se sjednává na dobu určitou do 30. 6. 2027.</w:t></w:r></w:p>'
       '<w:p><w:r><w:t>Výpovědní lhůta činí 3 měsíce.</w:t></w:r></w:p></w:body></w:document>')


def test_docx_text(tmp_path: Path):
    p = tmp_path / "a.docx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml", DOC)
    t, n = krok2.docx_text(p)
    assert "30. 6. 2027" in t and "Výpovědní" in t and n == 1


def test_pravidla_zakladni():
    t = ("Pronajímatel přenechává nájemci kancelářské prostory o celkové výměře 1.229,65 m2. "
         "Nájemné činí 2.470,91 Kč/m2/rok bez DPH. Nájem se uzavírá na dobu určitou, a to do 30. 4. 2033. "
         "Výpovědní lhůta činí šest měsíců.")
    r = pravidla.extrahuj(t)
    assert r["plocha_m2"] == 1229.65
    assert r["kc_m2_mesic"] == round(2470.91 / 12, 1)
    assert r["doba"] == "urcita" and r["konec"] == "2033-04-30"
    assert r["vypoved_mesicu"] == 6
    assert "do 30. 4. 2033" in r["citace_doba"]


def test_pravidla_mesicu_od():
    r = pravidla.extrahuj("Smlouva se uzavírá na dobu určitou o dalších 62 měsíců počínaje dnem 1. 11. 2019.")
    assert r["konec"] == "2024-12-31"


def test_pravidla_neurcita_mimo_kontext():
    r = pravidla.extrahuj("Smluvní strany souhlasí se zveřejněním na dobu neurčitou pro účely informování veřejnosti.")
    assert r["doba"] is None


def test_slouceni():
    assert fakta.slouc(100.0, "c", 101.0, "d", True)[1] == "shoda"
    assert fakta.slouc(100.0, "c", 200.0, "d", True)[1] == "rozpor"
    assert fakta.slouc(None, None, 200.0, "d", False)[1] == "model-neovereno"
    assert fakta.lokalita("Na Pankráci 1, 140 00 Praha 4")[0] == "Praha 4"
    assert fakta.lokalita("Poděbradova 2842/1, 301 00 Plzeň")[0] == "Plzeň"


def test_eur():
    r = pravidla.extrahuj("Nájemné: Kancelářské prostory – 7,30 EUR/m2 měsíčně, ostatní prostory 6,48 EUR/m2.")
    assert r["mena"] == "EUR" and r["puvodni_m2_mesic"] == 7.3
    assert r["kc_m2_mesic"] == round(7.3 * pravidla.KURZ_EUR, 1)


def test_iso_a_plocha():
    assert fakta._iso("31.10.2034") == "2034-10-31"
    assert fakta._iso("2034-10-31") == "2034-10-31"
    assert fakta._iso("null") is None
