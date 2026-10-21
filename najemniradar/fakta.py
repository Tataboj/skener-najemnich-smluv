"""Krok 4a: fakta pro demo - najmy jako RETEZCE dokumentu (smlouva -> dodatky -> ukonceni).

Kostra: metadata Registru smluv (navazanyZaznam = id puvodni smlouvy). Pro kazdy dokument dva nezavisle
extraktory (pravidla + lokalni LLM); "Najem dnes" = posledni platna hodnota v retezci + odkud je.

Kriticka kontrola misto slepe duvery:
  shoda     - pravidla i model dosly ke stejne hodnote (nejvyssi duvera)
  pravidla  - jen pravidla (citace je presny usek textu -> overitelna z podstaty)
  model     - jen model, citace overena v textu
  rozpor    - metody se lisi -> ukazeme obe, do benchmarku NEjde
Udaj je OVERENY (ok) jen kdyz citace existuje v textu a obsahuje prave tuto hodnotu; jinak "zkontrolovat".
Do benchmarku najemneho jdou jen overene hodnoty.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

from . import pravidla

# Priblizne polohy (centrum obvodu / mesta) - do doby, nez probehne geokodovani adres
PRAHA = {1: (50.0870, 14.4210), 2: (50.0755, 14.4300), 3: (50.0830, 14.4600), 4: (50.0420, 14.4400),
         5: (50.0700, 14.3950), 6: (50.1000, 14.3700), 7: (50.1010, 14.4370), 8: (50.1110, 14.4700),
         9: (50.1080, 14.5100), 10: (50.0700, 14.4900), 11: (50.0300, 14.5100), 12: (50.0050, 14.4250),
         13: (50.0500, 14.3300), 14: (50.1000, 14.5600), 15: (50.0450, 14.5400), 16: (49.9900, 14.3600),
         17: (50.0650, 14.3150), 18: (50.1350, 14.5050), 19: (50.1350, 14.5550), 20: (50.0950, 14.6050),
         21: (50.0700, 14.6550), 22: (50.0300, 14.6000)}
PSC_PRAHA = {"110": 1, "111": 1, "120": 2, "128": 2, "130": 3, "140": 4, "141": 4, "142": 4, "143": 12,
             "147": 4, "148": 4, "149": 11, "150": 5, "152": 5, "155": 13, "158": 5, "160": 6, "161": 6,
             "162": 6, "163": 6, "170": 7, "180": 8, "181": 8, "182": 8, "184": 8, "186": 8, "190": 9,
             "193": 9, "197": 14, "198": 14, "100": 10, "101": 10, "102": 15, "104": 10, "108": 10, "109": 15}
MESTA = {"brno": (49.1951, 16.6068), "ostrava": (49.8209, 18.2625), "plzen": (49.7384, 13.3736),
         "olomouc": (49.5938, 17.2509), "liberec": (50.7663, 15.0543), "ceske budejovice": (48.9745, 14.4743),
         "hradec kralove": (50.2092, 15.8328), "pardubice": (50.0343, 15.7812), "usti nad labem": (50.6607, 14.0323),
         "zlin": (49.2265, 17.6707), "jihlava": (49.3961, 15.5912), "karlovy vary": (50.2319, 12.8720),
         "turnov": (50.5874, 15.1569), "vyskov": (49.2775, 16.9990), "krnov": (50.0897, 17.7039),
         "slavkov u brna": (49.1533, 16.8765), "kladno": (50.1473, 14.1029), "opava": (49.9387, 17.9026),
         "frydek-mistek": (49.6833, 18.3500), "tabor": (49.4144, 14.6578), "pisek": (49.3088, 14.1475),
         "kolin": (50.0281, 15.2006), "mlada boleslav": (50.4114, 14.9032), "teplice": (50.6404, 13.8245),
         "most": (50.5030, 13.6362), "chomutov": (50.4605, 13.4178), "decin": (50.7821, 14.2148),
         "trebic": (49.2148, 15.8817), "prerov": (49.4554, 17.4510), "prostejov": (49.4719, 17.1118),
         "kromeriz": (49.2977, 17.3931), "uherske hradiste": (49.0698, 17.4597), "znojmo": (48.8555, 16.0488),
         "havirov": (49.7798, 18.4369), "karvina": (49.8540, 18.5418), "trinec": (49.6776, 18.6708),
         "cheb": (50.0796, 12.3739), "sokolov": (50.1813, 12.6401), "klatovy": (49.3955, 13.2950),
         "nachod": (50.4167, 16.1629), "trutnov": (50.5610, 15.9127), "jablonec nad nisou": (50.7243, 15.1711),
         "havlickuv brod": (49.6078, 15.5807), "vsetin": (49.3387, 17.9962), "breclav": (48.7590, 16.8820),
         "hodonin": (48.8489, 17.1324), "benesov": (49.7817, 14.6869), "beroun": (49.9638, 14.0720),
         "pribram": (49.6899, 14.0104), "melnik": (50.3505, 14.4741), "nymburk": (50.1861, 15.0417)}


NAZVY = {"plzen": "Plzeň", "usti nad labem": "Ústí nad Labem", "ceske budejovice": "České Budějovice",
         "hradec kralove": "Hradec Králové", "zlin": "Zlín", "decin": "Děčín", "trebic": "Třebíč",
         "prerov": "Přerov", "prostejov": "Prostějov", "kromeriz": "Kroměříž", "uherske hradiste": "Uherské Hradiště",
         "havirov": "Havířov", "karvina": "Karviná", "trinec": "Třinec", "nachod": "Náchod",
         "havlickuv brod": "Havlíčkův Brod", "vsetin": "Vsetín", "breclav": "Břeclav", "hodonin": "Hodonín",
         "benesov": "Benešov", "pribram": "Příbram", "melnik": "Mělník", "kolin": "Kolín", "pisek": "Písek",
         "tabor": "Tábor", "vyskov": "Vyškov", "frydek-mistek": "Frýdek-Místek", "slavkov u brna": "Slavkov u Brna",
         "jablonec nad nisou": "Jablonec nad Nisou", "mlada boleslav": "Mladá Boleslav"}


def lokalita(adresa: str | None) -> tuple[str | None, tuple[float, float] | None]:
    if not adresa:
        return None, None
    a = pravidla._fold(adresa)
    m = re.search(r"praha\s?-?\s?(\d{1,2})\b", a)
    if m and int(m.group(1)) in PRAHA:
        n = int(m.group(1))
        return f"Praha {n}", PRAHA[n]
    m = re.search(r"\b(1\d{2})\s?\d{2}\b", a)
    if m and m.group(1) in PSC_PRAHA:
        n = PSC_PRAHA[m.group(1)]
        return f"Praha {n}", PRAHA[n]
    if "praha" in a:
        return "Praha (obvod neuveden)", PRAHA[1]
    # mesto za PSC ma prednost ("RC Brno ..., 682 01 Vyskov" = Vyskov)
    m = re.search(r"\b\d{3} ?\d{2},? ([a-z][a-z]+(?:[ -]{1,3}(?:nad|pod|u|[a-z]+))*)", a)
    if m:
        for mesto, ll in MESTA.items():
            if m.group(1).startswith(mesto):
                return NAZVY.get(mesto, mesto.title()), ll
    for mesto, ll in MESTA.items():
        # i 6. pad ("v Krnove", "v Plzni" nechytne, "v Olomouci" ano)
        if re.search(r"\b" + re.escape(mesto) + r"(?:e|u|i|a|em)?\b", a):
            return NAZVY.get(mesto, mesto.title()), ll
    m = re.search(r"\b\d{3} ?\d{2},? ([A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][a-záčďéěíňóřšťúůýž]+(?:[ -]{1,3}(?:nad|pod|u|[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][a-záčďéěíňóřšťúůýž]+))*)",
                  adresa)
    if m and not re.match(r"(?:Praha|Česká|Ceska)", m.group(1)):
        return re.sub(r"\s*-\s*", "-", m.group(1)).strip(), None
    return None, None


def _jitter(idv: str, ll: tuple[float, float]) -> tuple[float, float]:
    h = hashlib.md5(idv.encode()).digest()
    return round(ll[0] + (h[0] - 128) / 128 * 0.006, 5), round(ll[1] + (h[1] - 128) / 128 * 0.009, 5)


def slouc(r_val, r_cit, l_val, l_cit, l_ok, tol=0.03):
    """-> (hodnota, zdroj, citace, alternativa)"""
    if isinstance(r_val, str) or isinstance(l_val, str):
        close = r_val == l_val
    else:
        close = r_val is not None and l_val is not None and abs(r_val - l_val) <= tol * max(abs(r_val), abs(l_val))
    if r_val is not None and l_val is not None:
        return (r_val, "shoda", r_cit, None) if close else (r_val, "rozpor", r_cit, l_val)
    if r_val is not None:
        return r_val, "pravidla", r_cit, None
    if l_val is not None and l_ok:
        return l_val, "model", l_cit, None
    if l_val is not None:
        return l_val, "model-neovereno", l_cit, None
    return None, None, None, None


def _iso(v) -> str | None:
    """Model obcas vrati '31.10.2034' misto ISO -> sjednotit; jine tvary zahodit."""
    if not isinstance(v, str):
        return None
    v = v.strip()
    if re.fullmatch(r"\d{4}-\d\d-\d\d", v):
        return v
    m = re.fullmatch(r"(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})", v)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return None
    return None


def _mesicu_do(konec: str | None, dnes: date) -> int | None:
    if not konec:
        return None
    y, m, d = map(int, konec.split("-"))
    return (y - dnes.year) * 12 + (m - dnes.month)



# ------------------------------------------------------------------ pomocne --
FO_FORMY = {"101", "102", "103", "104", "105", "106", "107", "108"}
RE_PRAVNICKA = re.compile(r"s\.\s?r\.\s?o|spol\.|a\.\s?s\.|akciov|z\.\s?s\.|v\.\s?o\.\s?s|k\.\s?s\.|druzstv|s\.\s?p\.|"
                          r"obec|mesto|kraj|republik|urad|ministerstv|univerzit|fond|nadac|ustav|sprav|podnik|"
                          r"skol|nemocnic|komora|svaz|spolek|cirkev|farnost|sdruzeni|institut|centrum|agentur|"
                          r"inspekc|policie|soud|zastupitel|gmbh|ltd|limited|b\.v\.|sa\b|se\b|o\.\s?p\.\s?s|z\.\s?u\.",
                          re.I)
SLUZBY = {
    "relokace": "Relokace / přejednání",
    "prejednani": "Přejednání podmínek",
    "kontrakce": "Optimalizace (zmenšení)",
    "expanze": "Expanze",
    "overit": "Ověřit pokračování nájmu",
    "monitoring": "Sledovat",
}


def je_fyzicka_osoba(nazev: str, ico: str | None, forma: str | None) -> bool:
    if forma:
        return forma in FO_FORMY
    return not RE_PRAVNICKA.search(pravidla._fold(nazev or "")) and len((nazev or "").split()) in (2, 3, 4)


def _mesice_mezi(a: date, iso: str) -> int:
    y, m, d = map(int, iso.split("-"))
    return (y - a.year) * 12 + (m - a.month) - (1 if d < a.day else 0)


def _minus_mesicu(iso: str, n: int) -> str:
    y, m, d = map(int, iso.split("-"))
    k = y * 12 + (m - 1) - n
    yy, mm = k // 12, k % 12 + 1
    import calendar
    return date(yy, mm, min(d, calendar.monthrange(yy, mm)[1])).isoformat()


def _pole(v=None, ok=False, zdroj=None, cit=None, misto=None, alt=None, pozn=None) -> dict:
    misto = misto or {}
    return {"v": v, "ok": bool(ok and v is not None), "zdroj": zdroj, "cit": cit, "str": misto.get("str"),
            "cl": misto.get("cl"), "alt": alt, "pozn": pozn}


RANGE = {"plocha": (5, 200_000), "kc_m2": (30, 800), "vypoved": (1, 60)}


def _slouc_pole(druh: str, kind: str, r_v, r_c, l_v, l_c, D: pravidla.Dokument, r_pozn=None) -> dict:
    """Pravidla vs. model -> jedno pole s priznakem 'ok' (overeno: citace v textu A obsahuje hodnotu)."""
    lo, hi = RANGE.get(druh, (None, None))
    if lo is not None:
        if r_v is not None and not (lo <= r_v <= hi):
            r_v = None
        if l_v is not None and not (isinstance(l_v, (int, float)) and lo <= l_v <= hi):
            l_v = None
    l_ok = l_v is not None and D.najdi(l_c) >= 0 and pravidla.hodnota_v_citaci(l_v, l_c, kind)
    if r_v is not None:
        if l_v is not None and l_ok:
            same = (r_v == l_v) if isinstance(r_v, str) else abs(r_v - l_v) <= 0.05 * max(abs(r_v), abs(l_v))
            if not same:
                return _pole(r_v, False, "rozpor", r_c, D.misto(r_c), alt=l_v,
                             pozn="Dvě místa ve smlouvě uvádějí různé hodnoty – zkontrolujte.")
            return _pole(r_v, True, "shoda", r_c, D.misto(r_c), pozn=r_pozn)
        return _pole(r_v, True, "pravidla", r_c, D.misto(r_c), pozn=r_pozn)
    if l_v is not None:
        if l_ok:
            return _pole(l_v, True, "model", l_c, D.misto(l_c))
        return _pole(l_v, False, "model-neovereno", l_c if D.najdi(l_c) >= 0 else None, D.misto(l_c),
                     pozn="Údaj navrhl jazykový model, ale citace ho neobsahuje – zkontrolujte ve smlouvě.")
    return _pole()


def fakta_dokumentu(text: str, ab: dict) -> dict:
    """Vsechna pole jednoho dokumentu (pravidla + model, overeni citaci, strana + clanek)."""
    D = pravidla.Dokument(text)
    rp = pravidla.extrahuj_dokument(D)
    ab = ab or {}
    for k in ("plocha_m2", "vypovedni_lhuta_mesicu"):
        if not isinstance(ab.get(k), (int, float)):
            ab[k] = None
    l_konec = _iso(ab.get("konec_najmu"))
    doba = rp["doba"]
    doba_zdroj = "pravidla" if doba else None
    if not doba and ab.get("doba_neurcita") is True:
        doba, doba_zdroj = "neurcita", "model"
    elif not doba and l_konec:
        doba, doba_zdroj = "urcita", "model"
    konec_pozn = None
    if rp["konec"] and not pravidla.hodnota_v_citaci(rp["konec"], rp["citace_doba"], "datum"):
        konec_pozn = "Dopočteno z délky nájmu uvedené ve smlouvě."
    konec = _slouc_pole("konec", "datum", rp["konec"], rp["citace_doba"],
                        None if doba == "neurcita" else l_konec, ab.get("citace_doba"), D, konec_pozn)
    kc_pozn = None
    if rp["kc_m2_zdroj"] and rp["kc_m2_zdroj"].startswith("dopocet"):
        druh = rp["kc_m2_zdroj"].split("(", 1)[1].split(" ")[0]
        kc_pozn = "Dopočteno: " + {"rocni": "roční", "pololetni": "pololetní", "ctvrtletni": "čtvrtletní",
                                    "mesicni": "měsíční"}.get(druh, druh) + " nájemné / plocha."
    if rp.get("mena") == "EUR":
        kc_pozn = (kc_pozn or "") + f" Ve smlouvě {rp['puvodni_m2_mesic']:.2f} EUR/m²/měs., přepočteno kurzem ČNB {pravidla.KURZ_EUR} Kč."
    l_kc = ab.get("_kc_m2_mesic")
    kc = _slouc_pole("kc_m2", "kc", rp["kc_m2_mesic"], rp["citace_najemne"],
                     l_kc if isinstance(l_kc, (int, float)) and ab.get("kc_m2_zdroj", "").startswith("smlouva") else None,
                     ab.get("citace_najemne"), D, kc_pozn.strip() if kc_pozn else None)
    out = {
        "doba": doba, "doba_zdroj": doba_zdroj,
        "doba_cit": rp["citace_doba"], "doba_misto": D.misto(rp["citace_doba"]) if rp["citace_doba"] else {},
        "konec": konec,
        "plocha": _slouc_pole("plocha", "plocha", rp["plocha_m2"], rp["citace_plocha"], ab.get("plocha_m2"),
                              ab.get("citace_plocha"), D),
        "kc_m2": kc,
        "vypoved": _slouc_pole("vypoved", "mesice", rp["vypoved_mesicu"], rp["citace_vypoved"],
                               ab.get("vypovedni_lhuta_mesicu"), ab.get("citace_vypoved"), D),
        "opce": _pole(rp["opce_lhuta_mesicu"] if rp["opce"] else None, True, "pravidla", rp["citace_opce"],
                      D.misto(rp["citace_opce"])) if rp["opce"] else _pole(),
        "opce_ano": bool(rp["opce"]),
        "indexace": _pole(True, True, "pravidla", rp["citace_indexace"], D.misto(rp["citace_indexace"]))
        if rp["indexace"] else _pole(),
        "adresa": _pole(rp["adresa"], True, "pravidla", rp["citace_adresa"], D.misto(rp["citace_adresa"]))
        if rp["adresa"] else _pole(),
        "adresa_model": ab.get("adresa_prostor") if ab.get("adresa_prostor") not in (None, "null", "Není uvedeno") else None,
        "mena": rp.get("mena") if kc["zdroj"] in ("pravidla", "shoda", "rozpor") else None,
        "ma_strany": D.ma_strany, "znaku": len(D.t),
        "zmenseni": rp["zmenseni"], "rozsireni": rp["rozsireni"],
        "cit_zmenseni": rp["citace_zmenseni"], "cit_rozsireni": rp["citace_rozsireni"],
    }
    return out


_V = "A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"
_m = "a-záčďéěíňóřšťúůýž"
RE_ULICE_CISLO = re.compile(rf"\b([{_V}][{_m}]+\.?(?: [{_V}{_m}][{_m}]*\.?){{0,2}}) (\d{{1,5}}(?:/\d{{1,4}}[a-z]?)?)\b")


CTVRTI = {"zizkov": 3, "vinohrady": 2, "karlin": 8, "liben": 8, "smichov": 5, "holesovice": 7, "letna": 7, "bubenec": 6,
          "dejvice": 6, "vokovice": 6, "brevnov": 6, "nusle": 4, "pankrac": 4, "michle": 4, "krc": 4, "podoli": 4, "vysehrad": 2,
          "vrsovice": 10, "strasnice": 10, "zabehlice": 10, "malesice": 10, "vysocany": 9, "prosek": 9, "chodov": 11,
          "haje": 11, "stodulky": 13, "repy": 17, "letnany": 18, "kbely": 19, "modrany": 12, "sterboholy": 15, "hostivar": 15,
          "male namesti": 1, "stare mesto": 1, "mala strana": 1, "hradcany": 1, "josefov": 1, "nove mesto": 1,
          "jinonice": 5, "radlice": 5, "kosire": 5, "zlicin": 17, "cakovice": 18, "troja": 7, "bohnice": 8, "kobylisy": 8}


def ctvrt_prahy(text: str | None) -> str | None:
    """Prazska ctvrt v textu (Zizkov, Karlin, ...) -> 'Praha N'."""
    if not text:
        return None
    f = pravidla._fold(text)
    for k, n in CTVRTI.items():
        if re.search(r"\b" + k + r"\w{0,3}\b", f):
            return f"Praha {n}"
    return None


def _ulice(a: str | None) -> str | None:
    """Nazev ulice (bez diakritiky) z adresy - pro porovnani se sidlem stran."""
    if not a:
        return None
    m = re.search(r"([a-z][a-z .]+?)\s+(?:c\.\s?p\.\s?)?\d", pravidla._fold(a))
    return m.group(1).strip().split()[-1] if m else None


def je_konkretni(a: str) -> bool:
    """Adresa, podle ktere mapa najde budovu: nazev ulice + cislo (ne 'RO Praha - mesto', ne 'Praha 2, 120 00 Strana')."""
    f = pravidla._fold(a)
    return bool(re.search(r"[a-z]{3,}\.?\s+(?:c\.\s?p\.\s?)?\d{1,5}(?:/\d{1,4}[a-z]?)?\b(?!\s?\d{2}\b)", f)) \
        and not re.search(r"\bstrana\s+\d|\bpraha\s+\d{1,2}\s*,\s*\d{3}", f[:40]) \
        and not re.search(r"vymer|ploch|patr|podlazi|\d\s?m2|\d\s?m²", f)


RE_SUM = re.compile(r"(?i)\b(?:smlouv\w*|dodat\w*|n[aá]j(?:em|m)\w*|pron[aá]j\w*|podn[aá]j\w*|n[aá]jemn\w*|prostor\w*|"
                    r"kancel[aá]ř\w*|nebyt\w*|slou[zž]\w*|podnik[aá]n\w*|č\.?|o|k|ke|na|pro|v|ve|ze|z|a|s|se|do|"
                    r"dlouh\.?|služ\w*|spojen\w*|výkon\w*|činnost\w*|zaměstnan\w*|pracovn\w*|budov\w*|areál\w*|"
                    r"česká|republika|AVISme|SoD|Sml|dohod\w*|účel\w*|pracovišt\w*|sekce|soukrom\w*|sektor\w*|"
                    r"administrativn\w*|pon[eě]\w*|rozšíř\w*|vymezen\w*|poměr\w*|kancelářsk\w*)\b")


def klicova_slova(predmet: str | None, mesto: str | None) -> str | None:
    """Jmeno mista z predmetu ('Najem prostor TV Zizkov' -> 'TV Zizkov'); None, kdyz zbyde jen obecny text."""
    if not predmet:
        return None
    t = re.sub(r"\([^)]*\)", " ", predmet)
    t = re.sub(r"\S*\d\S*", " ", t)  # kody, cisla smluv
    t = RE_SUM.sub(" ", t)
    t = re.sub(r"[-–_,.;:/\\]+", " ", t)
    slova = [w for w in t.split() if len(w) > 1]
    # od prvniho slova s velkym pismenem; konec u dvou malych slov za sebou ("vyuziti opce")
    while slova and not slova[0][0].isupper():
        slova.pop(0)
    for i in range(1, len(slova)):
        if slova[i][0].islower() and slova[i - 1][0].islower():
            slova = slova[:i - 1]
            break
    while slova and slova[-1][0].islower() and len(slova) > 1:
        slova.pop()
    if mesto:
        mf = set(pravidla._fold(mesto).split())
        slova = [w for w in slova if pravidla._fold(w)[:5] not in {x[:5] for x in mf}]
    if not slova or not any(w[0].isupper() for w in slova):
        return None
    return " ".join(slova[:5])


def adresa_z_predmetu(predmet: str | None, lok: str | None) -> str | None:
    """Ulice (+ cislo) uvedena v predmetu smlouvy; mesto doplnit z lokality."""
    if not predmet:
        return None
    mesto = None
    if lok:
        mesto = "Praha" if lok.startswith("Praha") else lok
    for m in RE_ULICE_CISLO.finditer(predmet):
        ulice = m.group(0)
        if re.search(r"(?i)dodatek|smlouv|č\.|cislo|článek|příloh|CES|AVIS|\bdne\b|^(?:Praha|MČ)\b", ulice) or \
                (lok and pravidla._fold(ulice).startswith(pravidla._fold(lok.split(" (")[0].split(" –")[0]))):
            continue
        tail = predmet[m.end():m.end() + 40]
        psc = re.match(r",? ?(\d{3} ?\d{2})? ?([%s][%s]+(?: [%s]?[%s]+)*)" % (_V, _m, _V, _m), tail)
        if psc and psc.group(2):
            return f"{ulice}, {(psc.group(1) + ' ') if psc.group(1) else ''}{psc.group(2)}".strip()
        return f"{ulice}, {mesto}" if mesto and mesto not in ulice else ulice
    if mesto and mesto != "Praha":
        m = re.search(re.escape(pravidla._fold(mesto)) + r"\s*,\s*([a-z][a-z]+(?:ova|ska|ho|ni|ich)\b)", pravidla._fold(predmet))
        if m:
            ulice = predmet[m.start(1):m.end(1)]
            return f"{ulice}, {mesto}"
    return None


def _datum_ukonceni(text: str, datum_doc: str | None) -> tuple[str | None, str | None]:
    """Datum skonceni z dohody o ukonceni: 'ke dni D' / 'k D' za slovem skoncit/ukoncit; ne starsi nez dokument - 1 rok."""
    D = pravidla.Dokument(text)
    t, f = D.t, D.f
    min_d = None
    if datum_doc:
        y, mo, d = map(int, datum_doc[:10].split("-"))
        min_d = date(y - 1, mo, min(d, 28)).isoformat()
    kand = []
    for m in re.finditer(r"(?:skonc|ukonc|zanik)\w*", f):
        okno = f[m.start(): m.start() + 220]
        for d in pravidla.RE_DATUM.finditer(okno):
            iso = pravidla._datum(d)
            if iso and (not min_d or iso >= min_d) and re.search(r"(?:ke? dni|\bk|\bke|dnem|datem|ke dni)\s?$",
                                                                  okno[max(0, d.start() - 9):d.start()]):
                kand.append((m.start(), iso, pravidla._cit(t, m.start(), m.start() + d.end(), 0)))
    if kand:
        pos, iso, cit = kand[0]
        return iso, cit
    return None, None


TYP_STITEK = {"smlouva": "SMLOUVA", "dodatek": "DODATEK", "ukonceni": "UKONČENÍ"}


def _kc(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ")


def cz_datum(iso: str | None) -> str:
    if not iso:
        return ""
    y, m, d = iso[:10].split("-")
    return f"{int(d)}. {int(m)}. {y}"


def _zmeny(pred: dict | None, ted: dict, doc: dict) -> list[str]:
    """Lidsky popis, co dokument meni (pro casovou osu)."""
    z = []
    if doc["typ"] == "ukonceni":
        z.append("ukončení nájmu" + (f" k {cz_datum(doc['konec_ukonceni'])}" if doc.get("konec_ukonceni") else ""))
    if ted.get("zmenseni") and doc["typ"] == "dodatek":
        z.append("zmenšení pronajatých prostor")
    if ted.get("rozsireni") and doc["typ"] == "dodatek":
        z.append("rozšíření pronajatých prostor")
    if doc.get("hodnota") is not None and doc["hodnota"] < 0:
        z.append(f"snížení hodnoty smlouvy o {_kc(-doc['hodnota'])} Kč")
    if pred is None:
        return z
    for k, nazev, fmt in (("konec", "konec nájmu", cz_datum), ("plocha", "plocha", lambda v: f"{_kc(v)} m²"),
                          ("kc_m2", "nájemné", lambda v: f"{v:.0f} Kč/m²/měs.")):
        a, b = pred.get(k, {}).get("v"), ted[k]["v"]
        if b is not None and ted[k]["ok"] and a is not None and a != b:
            if isinstance(b, float) and abs(a - b) <= 0.02 * max(a, b):
                continue
            z.append(f"{nazev}: {fmt(a)} → {fmt(b)}")
    if ted["doba"] == "neurcita" and pred.get("doba") == "urcita":
        z.append("změna na dobu neurčitou")
    return z


def sestav(con: sqlite3.Connection, text_dir: Path, dnes: date | None = None,
           text_str_dir: Path | None = None) -> list[dict]:
    """-> seznam NAJMU (retezcu dokumentu), kazdy s 'Najmem dnes', kritickym datem a doporucenou sluzbou."""
    dnes = dnes or date.today()
    con.row_factory = sqlite3.Row
    tab = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    tenant = [r["id_verze"] for r in con.execute(
        """SELECT s.id_verze FROM shortlist l JOIN smlouvy s USING(id_verze) JOIN texty t USING(id_verze)
           LEFT JOIN llm_role r USING(id_verze)
           WHERE t.sken=0 AND t.znaku>300 AND (t.role_text='verejny_najemce' OR r.role='najemce')""")]
    sml = {r["id_verze"]: dict(r) for r in con.execute("SELECT * FROM smlouvy")}
    meta = {r["id_verze"]: dict(r) for r in con.execute("SELECT * FROM meta WHERE chyba IS NULL OR chyba=''")} \
        if "meta" in tab else {}
    ab = {r["id_verze"]: json.loads(r["data"]) for r in con.execute("SELECT id_verze, data FROM abstrakt")} \
        if "abstrakt" in tab else {}
    ares = {r["ico"]: dict(r) for r in con.execute("SELECT * FROM ares")} if "ares" in tab else {}
    # id_smlouvy -> id_verze (posledni verze)
    ids2v: dict[str, str] = {}
    for v, r in sorted(sml.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
        if r.get("id_smlouvy"):
            ids2v[r["id_smlouvy"]] = v
    for v, r in meta.items():
        if r.get("id_smlouvy"):
            ids2v.setdefault(r["id_smlouvy"], v)

    def rodic(v: str) -> str | None:
        n = (meta.get(v) or {}).get("navazany")
        return n or None

    def koren(v: str) -> str:
        """Klic retezce = id_smlouvy nejstarsiho znameho predka (i kdyz neni v datech)."""
        seen = set()
        ids = (meta.get(v) or {}).get("id_smlouvy") or (sml.get(v) or {}).get("id_smlouvy") or v
        while v and v not in seen:
            seen.add(v)
            n = rodic(v)
            if not n:
                break
            ids = n
            v = ids2v.get(n)
        return ids

    retezce: dict[str, list[str]] = {}
    for v in set(meta) | set(tenant):
        retezce.setdefault(koren(v), []).append(v)
    tenant_koreny = {koren(v) for v in tenant}

    def text_of(v: str) -> str:
        for d in (text_str_dir, text_dir):
            if d and (Path(d) / f"{v}.txt").exists():
                return (Path(d) / f"{v}.txt").read_text(encoding="utf-8")
        return ""

    out = []
    for k in sorted(tenant_koreny):
        clen = [v for v in retezce.get(k, []) if v in sml]
        # jen dokumenty tehoz zverejnovatele (najemce)
        hlavni = next((v for v in clen if v in tenant), None)
        if not hlavni:
            continue
        zv = sml[hlavni]["subjekt_ico"]
        clen = [v for v in clen if sml[v]["subjekt_ico"] == zv]
        clen.sort(key=lambda v: ((sml[v]["datum_uzavreni"] or ""), {"smlouva": 0, "dodatek": 1, "ukonceni": 2}
                                 .get(sml[v]["typ"], 1), int(v) if v.isdigit() else 0))
        docs, pred = [], None
        for v in clen:
            s = sml[v]
            text = text_of(v)
            fd = fakta_dokumentu(text, dict(ab.get(v, {}))) if len(text) > 300 else None
            m = meta.get(v, {})
            hod = m.get("hodnota") if m.get("hodnota") is not None else (s["hodnota_bez_dph"] or s["hodnota_s_dph"])
            prilohy = json.loads(s["prilohy_json"] or "[]")
            typ = s["typ"]
            if typ == "smlouva" and m.get("navazany"):
                typ = "dodatek"  # v registru navazany na puvodni smlouvu -> fakticky dodatek
            doc = {"id": v, "typ": typ, "stitek": TYP_STITEK.get(typ, "DOKUMENT"),
                   "datum": s["datum_uzavreni"], "cislo": m.get("cislo_smlouvy") or s["cislo_smlouvy"],
                   "predmet": s["predmet"], "hodnota": hod, "odkaz": s["odkaz"],
                   "pdf": prilohy[0]["odkaz"] if len(prilohy) == 1 and prilohy[0].get("odkaz") else None,
                   "priloh": len(prilohy), "text": fd is not None, "sken": fd is None and bool(prilohy),
                   "v_shortlistu": v in tenant}
            if typ == "ukonceni":
                doc["konec_ukonceni"], doc["cit_ukonceni"] = _datum_ukonceni(text, s["datum_uzavreni"]) if text else (None, None)
                if text and doc["cit_ukonceni"]:
                    doc["misto_ukonceni"] = pravidla.Dokument(text).misto(doc["cit_ukonceni"])
            if fd:
                doc["zmeny"] = _zmeny(pred, fd, doc)
                if typ == "dodatek" and (fd["zmenseni"] or fd["rozsireni"]):
                    doc["zmena_cit"] = fd["cit_zmenseni"] or fd["cit_rozsireni"]
                pred = fd
            else:
                doc["zmeny"] = _zmeny(None, {}, doc) if typ == "ukonceni" or (hod or 0) < 0 else []
            doc["_f"] = fd
            docs.append(doc)
        out.append(_najem(k, docs, sml[hlavni], ares, dnes, meta))
    return out


def _dnes_pole(docs: list[dict], klic: str) -> tuple[dict, dict | None, dict | None]:
    """Najem dnes: nejnovejsi dokument s overenou hodnotou; jinak nejnovejsi neoverena. -> (pole, doc, puvodni)"""
    s_hodnotou = [(d, d["_f"][klic]) for d in docs if d["_f"] and d["_f"][klic]["v"] is not None]
    if not s_hodnotou:
        return _pole(), None, None
    ok = [x for x in s_hodnotou if x[1]["ok"]]
    d, p = (ok or s_hodnotou)[-1]
    first_d, first_p = (ok or s_hodnotou)[0]
    puv = None
    if first_d is not d and first_p["v"] != p["v"]:
        puv = {"v": first_p["v"], "doc": first_d["id"], "datum": first_d["datum"]}
    return dict(p), d, puv


def _najem(k: str, docs: list[dict], hl: dict, ares: dict, dnes: date, meta: dict) -> dict:
    dnes_iso = dnes.isoformat()
    pole = {}
    for klic in ("konec", "plocha", "kc_m2", "vypoved", "opce", "indexace", "adresa"):
        p, d, puv = _dnes_pole(docs, klic)
        p["doc"] = d["id"] if d else None
        p["doc_stitek"] = d["stitek"] if d else None
        p["doc_datum"] = d["datum"] if d else None
        p["doc_pdf"] = d["pdf"] if d else None
        p["doc_odkaz"] = d["odkaz"] if d else None
        p["puvodni"] = puv
        pole[klic] = p
    # doba: nejnovejsi dokument, ktery ji uvadi
    doba, doba_doc = None, None
    for d in docs:
        if d["_f"] and d["_f"]["doba"]:
            doba, doba_doc = d["_f"]["doba"], d
    if doba == "neurcita" and pole["konec"]["doc"] and doba_doc and pole["konec"]["doc_datum"] and \
            (pole["konec"]["doc_datum"] or "") > (doba_doc["datum"] or ""):
        doba = "urcita"  # pozdejsi dodatek stanovil konec
    if doba == "neurcita":
        pole["konec"] = {**_pole(), "doc": None, "puvodni": None}
    # ukonceni plati jen jako posledni dokument (po "ukonceni" s dalsimi dodatky jde o castecne ukonceni)
    ukon = [d for d in docs if d["typ"] == "ukonceni" and d is docs[-1] or
            (d["typ"] == "ukonceni" and all(x["typ"] == "ukonceni" or (x["datum"] or "") <= (d["datum"] or "")
                                            for x in docs))]
    ukonceno = None
    if ukon:
        d = ukon[-1]
        ukonceno = d.get("konec_ukonceni") or d["datum"]
    konec = pole["konec"]
    lhuty = [x for x in (pole["vypoved"]["v"] if pole["vypoved"]["ok"] else None,
                         pole["opce"]["v"] if pole["opce"]["ok"] else None) if x]
    lhuta = int(max(lhuty)) if lhuty else None
    kriticke, kriticke_txt, mes = None, None, None
    konec_pozn = None
    if ukonceno:
        mes0 = _mesice_mezi(dnes, ukonceno)
        stav = "skoncilo" if ukonceno <= dnes_iso else ("konci_24m" if mes0 <= 24 else "pozdeji")
        konec = {**_pole(ukonceno, True, "pravidla", ukon[-1].get("cit_ukonceni"), ukon[-1].get("misto_ukonceni"),
                         pozn=None if ukon[-1].get("konec_ukonceni") else "Datum dohody o ukončení."),
                 "doc": ukon[-1]["id"],
                 "doc_stitek": "UKONČENÍ", "doc_datum": ukon[-1]["datum"], "doc_pdf": ukon[-1]["pdf"],
                 "doc_odkaz": ukon[-1]["odkaz"], "puvodni": None}
        pole["konec"] = konec
        mes = _mesice_mezi(dnes, ukonceno)
    elif doba == "neurcita":
        stav = "neurcita"
        kriticke_txt = "kdykoli" + (f" · výpověď {lhuta} měs." if lhuta else "")
    elif konec["v"] and konec["ok"]:
        mes = _mesice_mezi(dnes, konec["v"])
        stav = "skoncilo" if konec["v"] < dnes_iso else ("konci_24m" if mes <= 24 else "pozdeji")
        pozdejsi = [d for d in docs if (d["datum"] or "") > konec["v"]]
        if stav == "skoncilo" and pozdejsi:
            # po datu konce vznikl dalsi dokument -> najem zrejme pokracuje (prodlouzeni nemusi byt v textu)
            stav = "k_overeni"
            konec_pozn = (f"Po tomto datu byl uzavřen další dokument ({pozdejsi[-1]['stitek'].lower()} "
                          f"z {cz_datum(pozdejsi[-1]['datum'])}) – nájem zřejmě pokračuje, ověřte konec.")
            konec["ok"] = False
    elif konec["v"]:
        stav = "k_overeni"
        mes = _mesice_mezi(dnes, konec["v"])
    else:
        stav = "nezname"
    if konec_pozn:
        konec["pozn"] = konec_pozn
    if konec["v"] and stav in ("konci_24m", "pozdeji", "k_overeni", "skoncilo") and not ukonceno:
        kriticke = _minus_mesicu(konec["v"], lhuta) if lhuta else konec["v"]
        kriticke_txt = None
    # doporucena sluzba
    zaporny = any((d["hodnota"] or 0) < 0 for d in docs if d["typ"] == "dodatek") or \
        any(d["_f"] and d["_f"]["zmenseni"] for d in docs if d["typ"] == "dodatek")
    rozsir = any(d["_f"] and d["_f"]["rozsireni"] for d in docs if d["typ"] == "dodatek")
    if stav == "konci_24m" or (kriticke and dnes_iso <= kriticke and _mesice_mezi(dnes, kriticke) <= 24):
        sluzba = "relokace"
    elif stav == "neurcita":
        sluzba = "prejednani"
    elif zaporny:
        sluzba = "kontrakce"
    elif rozsir:
        sluzba = "expanze"
    elif stav == "skoncilo":
        sluzba = "overit"
    elif stav == "k_overeni":
        sluzba = "overit"
    else:
        sluzba = "monitoring"
    # strany: najemce = zverejnovatel; pronajimatel = ostatni strany (fyzicke osoby anonymizovane)
    najemce_ico = (hl["subjekt_ico"] or "").strip()
    prona, seen = [], set()
    for d in docs:
        s_json = (meta.get(d["id"]) or {}).get("strany_json")
        for x in json.loads(s_json or "[]") or []:
            ico = (x.get("ico") or "").strip().replace(" ", "")
            nazev = (x.get("nazev") or "").strip()
            key = ico or pravidla._fold(nazev)
            if not nazev or ico == najemce_ico or key in seen:
                continue
            seen.add(key)
            a = ares.get(ico) or {}
            fo = je_fyzicka_osoba(nazev, ico, a.get("pravni_forma") or None)
            prona.append({"nazev": "Fyzická osoba (podnikatel)" if fo and ico else ("Fyzická osoba" if fo else
                          (a.get("nazev") or nazev)), "ico": ico or None, "fo": fo,
                          })
    if not prona:
        for d in docs:
            for x in json.loads(hl.get("strany_json") or "[]"):
                if (x.get("ico") or "").strip() != najemce_ico and x.get("nazev"):
                    ico = (x.get("ico") or "").strip() or None
                    fo = je_fyzicka_osoba(x["nazev"], ico, (ares.get(ico or "") or {}).get("pravni_forma") or None)
                    prona.append({"nazev": "Fyzická osoba" if fo else x["nazev"], "ico": ico, "fo": fo})
            break
    fo_jmena = []
    for d in docs:
        for x in json.loads(((meta.get(d["id"]) or {}).get("strany_json")) or (hl.get("strany_json") or "[]")):
            ico = (x.get("ico") or "").strip()
            if ico != najemce_ico and je_fyzicka_osoba(x.get("nazev", ""), ico, (ares.get(ico) or {}).get("pravni_forma") or None):
                fo_jmena.append(x.get("nazev", ""))
    # lokalita: predmet smlouvy (pise urad) -> adresa; adresa: predmet (ulice+cislo) -> text -> model -> hledani
    lok, ll = None, None
    for d in reversed(docs):
        lok, ll = lokalita(d["predmet"])
        if lok and not lok.startswith("Praha (obvod"):
            break

    def _shoda_mista(a: str | None, b: str | None) -> bool:
        if not a or not b or a.startswith("Praha (obvod") or b.startswith("Praha (obvod"):
            return True
        if a.startswith("Praha") and b.startswith("Praha"):
            return True  # postovni (Praha 10) vs. spravni (Praha 15) cislovani se lisi; mesto sedi
        return a == b


    def _pouzitelna(a: str | None) -> tuple[bool, str | None, tuple | None]:
        if not a or not je_konkretni(a):
            return False, None, None
        l2, ll2 = lokalita(a)
        if not _shoda_mista(lok, l2):
            return False, None, None  # jine mesto nez v predmetu smlouvy (casto sidlo pronajimatele)
        return True, l2, ll2

    adr, adr_zdroj, adr_ok, adr_dotaz = None, None, True, None
    kandidati = []
    for d in reversed(docs):
        a2 = adresa_z_predmetu(d["predmet"], lok)
        if a2 and re.search(r"\d", a2):
            kandidati.append((a2, "predmet", True))
    if pole["adresa"]["v"]:
        kandidati.append((pole["adresa"]["v"], "smlouva", True))
    for d in reversed(docs):
        if d["_f"] and d["_f"]["adresa_model"]:
            kandidati.append((d["_f"]["adresa_model"], "model", False))
    for i, (a2, zdroj, ok2) in enumerate(kandidati):
        pouz, l2, ll2 = _pouzitelna(a2)
        if pouz and zdroj == "predmet" and not l2:
            for a3, z3, ok3 in kandidati[i + 1:]:
                p3, l3, ll3 = _pouzitelna(a3)
                if p3 and l3 and _ulice(a3) == _ulice(a2):
                    a2, zdroj, ok2, l2, ll2 = a3, z3, ok3, l3, ll3
                    break
        if pouz:
            adr, adr_zdroj, adr_ok = a2, zdroj, ok2
            if l2 and (not lok or lok.startswith("Praha (obvod")):
                lok, ll = l2, ll2
            break
    if not adr:
        for d in reversed(docs):
            a2 = adresa_z_predmetu(d["predmet"], lok)  # ulice bez cisla ("Olomouc, Misakova")
            if a2:
                adr, adr_zdroj, adr_ok = a2, "predmet", True
                break
    if not lok:
        for a2, _, _ in kandidati:
            l2, ll2 = lokalita(a2)
            if l2:
                lok, ll = l2, ll2
                break
    if not lok and adr and adr_zdroj == "predmet" and "," in adr:
        m = adr.rsplit(",", 1)[1].strip()
        if m and m[0].isupper() and not re.search(r"\d", m):
            lok = m
    if not lok or lok.startswith("Praha (obvod"):
        for d in reversed(docs):
            l3 = ctvrt_prahy(d["predmet"]) or (ctvrt_prahy(adr) if adr else None)
            if l3 and (not lok or lok.startswith("Praha")):
                lok, ll = l3, PRAHA[int(l3.split()[1])]
                break
    if not adr:
        # zadna konkretni adresa: dotaz pro mapu z klicovych slov predmetu ("TV Zizkov") + mesta
        mesto = ("Praha" if lok and lok.startswith("Praha") else lok)
        klic = None
        for d in reversed(docs):
            klic = klicova_slova(d["predmet"], mesto)
            if klic:
                break
        if klic:
            for p_ in prona:  # "budova Gen" + pronajimatel "Gen Office Gallery a.s." -> nazev budovy
                if not p_["fo"] and pravidla._fold(klic.split()[0]) in pravidla._fold(p_["nazev"]):
                    klic = re.sub(r",?\s*(?:a\.\s?s\.|s\.\s?r\.\s?o\.|spol\..*)$", "", p_["nazev"]).strip()
                    break
        cp = next((m.group(1) for d in reversed(docs) for m in [re.search(r"č\.?\s?p\.\s?(\d{1,5})", d["predmet"] or "")] if m), None)
        if klic and not re.search(r"[a-záčďéěíňóřšťúůýž]{3,}", klic):
            klic = None  # jen zkratky ("OVSS IV") mapa nenajde -> hledat podle najemce
        if klic and cp:
            klic = f"{klic} {cp}"
        if klic and not mesto:
            klic = None  # bez mesta by mapa hledala naslepo
        if klic:
            adr_dotaz = f"{klic}, {mesto}" if mesto and pravidla._fold(mesto) not in pravidla._fold(klic) else klic
            adr_zdroj = "hledani"
        elif mesto:
            adr_dotaz, adr_zdroj = f"{hl['subjekt_nazev']}, {mesto}", "hledani-najemce"
    if lok and lok.startswith("Praha (obvod"):
        lok = "Praha – obvod neuveden"
    # anonymizace v citacich
    def anon(txt):
        if not txt:
            return txt
        for j in fo_jmena:
            for var in {j, j.replace("Ing. ", ""), " ".join(j.split()[-2:])}:
                if len(var) > 5:
                    txt = txt.replace(var, "[fyzická osoba]")
        return txt
    for p in pole.values():
        p["cit"] = anon(p.get("cit"))
    uplnost = sum(1 for kk in ("konec", "plocha", "kc_m2", "vypoved") if pole[kk]["ok"]) + (doba == "neurcita")
    hlavni_doc = docs[0]
    dok = []
    for d in docs:
        dd = {kk: vv for kk, vv in d.items() if kk not in ("_f", "cit_ukonceni")}
        dd["predmet"] = anon(dd["predmet"])
        if dd.get("zmena_cit"):
            dd["zmena_cit"] = anon(dd["zmena_cit"])
        dok.append(dd)
    return {
        "id": "N" + k, "koren": k, "predmet": anon(docs[-1]["predmet"] if docs[-1]["typ"] == "smlouva" else hlavni_doc["predmet"]),
        "najemce": hl["subjekt_nazev"], "najemce_ico": najemce_ico,
        "pronajimatel": prona[:4], "lokalita": lok, "lat": _jitter(k, ll)[0] if ll else None,
        "lon": _jitter(k, ll)[1] if ll else None, "adresa": anon(adr), "adresa_zdroj": adr_zdroj, "adresa_ok": adr_ok,
        "adresa_dotaz": anon(adr_dotaz),
        "adresa_cit": pole["adresa"].get("cit") if adr_zdroj == "smlouva" else None,
        "adresa_misto": {"str": pole["adresa"].get("str"), "cl": pole["adresa"].get("cl"),
                         "doc": pole["adresa"].get("doc"), "pdf": pole["adresa"].get("doc_pdf")} if adr_zdroj == "smlouva" else None,
        "doba": doba, "stav": stav, "mesicu_do_konce": mes, "kriticke": kriticke, "kriticke_txt": kriticke_txt,
        "lhuta": lhuta, "lhuta_druh": ("opce" if pole["opce"]["ok"] and pole["opce"]["v"] == lhuta else "vypoved") if lhuta else None,
        "sluzba": sluzba, "sluzba_txt": SLUZBY[sluzba],
        **{kk: pole[kk] for kk in ("konec", "plocha", "kc_m2", "vypoved", "opce", "indexace")},
        "opce_ano": any(d["_f"] and d["_f"]["opce_ano"] for d in docs),
        "mena": next((d["_f"]["mena"] for d in reversed(docs) if d["_f"] and d["_f"]["mena"]), None),
        "dokumenty": dok, "dokumentu": len(docs), "uplnost": uplnost,
        "chybi_puvodni": hlavni_doc["typ"] != "smlouva",
        "uzavreno": hlavni_doc["datum"],
    }


def statistiky(najmy: list[dict], meta: dict) -> dict:
    stavy, sluzby, over = {}, {}, {}
    for n in najmy:
        stavy[n["stav"]] = stavy.get(n["stav"], 0) + 1
        sluzby[n["sluzba"]] = sluzby.get(n["sluzba"], 0) + 1
        for k in ("konec", "plocha", "kc_m2", "vypoved"):
            o = over.setdefault(k, {"overeno": 0, "zkontrolovat": 0, "chybi": 0})
            o["overeno" if n[k]["ok"] else ("zkontrolovat" if n[k]["v"] is not None else "chybi")] += 1
    return {**meta, "najmu": len(najmy), "dokumentu": sum(n["dokumentu"] for n in najmy),
            "stavy": stavy, "sluzby": sluzby, "overeni": over}
