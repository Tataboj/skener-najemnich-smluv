"""Pravidlovy extraktor klicovych podminek najmu (bez AI, s presnou citaci).

Pouceni z exportu 1d2246 (48 abstraktu z qwen3:4b):
  * 'konec None' je casto spravne - smlouva na dobu NEURCITOU -> rozhoduje vypovedni lhuta
  * plocha byva rozepsana po mistnostech -> hledat 'celkem / celkova vymera'
  * najemne casto v Kc/m2 za ROK (ne za mesic) -> jednotku je nutne cist z textu
Kazda hodnota nese citaci = presny usek textu, ze ktereho plyne (overitelna z podstaty).
"""
from __future__ import annotations

import re
from datetime import date

MESICE = {"ledna": 1, "unora": 2, "brezna": 3, "dubna": 4, "kvetna": 5, "cervna": 6, "cervence": 7,
          "srpna": 8, "zari": 9, "rijna": 10, "listopadu": 11, "prosince": 12}
CISLOVKY = {"jeden": 1, "jednoho": 1, "jedne": 1, "jednomesicni": 1, "dva": 2, "dvou": 2, "dvoumesicni": 2,
            "tri": 3, "trimesicni": 3, "ctyri": 4, "ctyr": 4, "pet": 5, "peti": 5, "sest": 6, "sesti": 6,
            "sestimesicni": 6, "devet": 9, "deviti": 9, "dvanact": 12, "dvanacti": 12}


def _fold(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def normalizuj(text: str) -> tuple[str, str]:
    """-> (text s jednou mezerou, tentyz text bez diakritiky a malymi pismeny). Delky jsou shodne,
    takze pozice z hledani v 'f' plati i v 't' (citace s diakritikou)."""
    t = re.sub(r"\s+", " ", text)
    f = _fold(t)
    if len(f) != len(t):  # vzacne znaky, ktere se rozpadnou -> nahradni zarovnani
        t = "".join(c if len(_fold(c)) == 1 else " " for c in t)
        f = _fold(t)
    return t, f


def cislo(s: str) -> float | None:
    """'1.229,65' / '24 074,2' / '1 315' / '13.984' (tisice) -> float."""
    s = s.strip().replace(" ", " ")
    s = re.sub(r"(?<=\d) (?=\d{3}\b)", "", s)  # mezery jako oddelovac tisicu
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", s):
        s = s.replace(".", "")
    s = s.replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


RE_NUM = r"(\d{1,3}(?:[ .]\d{3})*(?:,\d+)?|\d+(?:,\d+)?)"
RE_DATUM = re.compile(r"(\d{1,2})\s?\.\s?(\d{1,2})\s?\.\s?((?:19|20)\d\d)|(\d{1,2})\.\s?(" +
                      "|".join(MESICE) + r")\s((?:19|20)\d\d)")


def _datum(m: re.Match) -> str | None:
    try:
        if m.group(1):
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            d, mo, y = int(m.group(4)), MESICE[m.group(5)], int(m.group(6))
        return date(y, mo, d).isoformat()
    except (ValueError, KeyError):
        return None


def _cit(t: str, a: int, b: int, pad: int = 25) -> str:
    return t[max(0, a - pad):min(len(t), b + pad)].strip()


# ------------------------------------------------------------- doba --
RE_NEURCITA = re.compile(r"na dobu neurcitou")
RE_URCITA = re.compile(r"na dobu urcitou")
RE_KONTEXT_DOBA = re.compile(r"naj|uzavir|sjednav|doba|dobu|smlouva se|plat")
RE_MESICU_OD = re.compile(r"(\d+)\s?(?:\(\w+\) )?mesic\w*[^.;]{0,40}?(?:pocinaje|ode dne|od)\s")
RE_LET = re.compile(r"(?:na dobu urcitou )?(?:v delce |na dobu |na )?(\d+|" + "|".join(CISLOVKY) +
                    r"|deset|patnact)\s?(?:\(\w+\) )?(let|roku|roky)")


def doba(t: str, f: str) -> dict:
    """Doba najmu: neurcita / urcita + konec (pripadne dopocet 'na X let ode dne D')."""
    out: dict = {"doba": None, "konec": None, "citace_doba": None}
    hits = []
    for rx, druh in ((RE_URCITA, "urcita"), (RE_NEURCITA, "neurcita")):
        for m in rx.finditer(f):
            hits.append((m.start(), druh, m))
    if not hits:
        return out
    hits.sort(key=lambda x: x[0])
    # jen zminky v kontextu najmu (ne "na dobu neurcitou pro ucely zverejneni...")
    ok = [h for h in hits if RE_KONTEXT_DOBA.search(f[max(0, h[0] - 140):h[0]])]
    if not ok:
        return out
    pos, druh, m = ok[0]
    okno = f[m.start(): m.start() + 260]
    out["doba"] = druh
    out["citace_doba"] = _cit(t, m.start(), m.start() + min(len(okno), 160), 0)
    if druh == "urcita":
        dates = [(x.start(), _datum(x)) for x in RE_DATUM.finditer(okno)]
        dates = [d for d in dates if d[1]]
        # "do D" ma prednost; jinak posledni datum v okne; jinak "X let ode dne D"
        do = [d for p, d in dates if re.search(r"(?:do|az do|do dne)\s?$", okno[max(0, p - 8):p])]
        if do:
            # dodatek "se prodluzuje do D" casto cituje i puvodni konec -> nejpozdejsi datum
            out["konec"] = max(do) if re.search(r"prodlu", okno) else do[0]
        else:
            mm = RE_MESICU_OD.search(okno)
            ml = RE_LET.search(okno)
            if mm and dates:
                n = int(mm.group(1))
                y, mo, d = map(int, dates[0][1].split("-"))
                k = (y * 12 + mo - 1) + n
                try:
                    out["konec"] = date(k // 12, k % 12 + 1, 1).fromordinal(date(k // 12, k % 12 + 1, 1).toordinal() - 1).isoformat()
                except ValueError:
                    pass
                return out
            if ml and dates:
                n = ml.group(1)
                n = int(n) if n.isdigit() else CISLOVKY.get(n, {"deset": 10, "patnact": 15}.get(n))
                y, mo, d = map(int, dates[0][1].split("-"))
                try:
                    out["konec"] = date(y + n, mo, d).fromordinal(date(y + n, mo, d).toordinal() - 1).isoformat()
                except ValueError:
                    pass
            elif len(dates) >= 2:
                out["konec"] = max(d for _, d in dates)
    return out


# ------------------------------------------------------- vypoved --
RE_VYPOVED = re.compile(r"vypovedni (?:lhut|dob)\w*[^.;]{0,80}?(\d+|" + "|".join(CISLOVKY) +
                        r")\s?(?:\(\w+\) )?(?:-?ti |-?mi )?(mesic\w*|let|rok\w*)")
RE_VYPOVED2 = re.compile(r"(\d+|" + "|".join(CISLOVKY) + r")\s?(?:\(\w+\) )?-?(?:mesicni|mesicnich) vypovedni")


def vypoved(t: str, f: str) -> dict:
    for rx in (RE_VYPOVED, RE_VYPOVED2):
        m = rx.search(f)
        if m:
            n = m.group(1)
            n = int(n) if n.isdigit() else CISLOVKY.get(n)
            if n is None:
                continue
            if rx is RE_VYPOVED and m.group(2).startswith(("let", "rok")):
                n *= 12
            if 0 < n <= 60:
                return {"vypoved_mesicu": n, "citace_vypoved": _cit(t, m.start(), m.end(), 0)}
    return {"vypoved_mesicu": None, "citace_vypoved": None}


# --------------------------------------------------------- plocha --
RE_M2 = r"\s?(?:m2|m²|m 2|metr\w* ctverecn\w*)"
RE_PLOCHA_CELKEM = re.compile(
    r"(?:celkov\w* (?:vymer\w*|ploch\w*|podlahov\w* ploch\w*)|vymer\w* celkem|celkem)[^.;]{0,60}?" + RE_NUM + RE_M2)
RE_PLOCHA_VYMERA = re.compile(r"(?:o (?:celkove )?vymere|o vymere celkem|v rozsahu|o celkove plose|o plose)\s"
                              r"(?:cca |priblizne |celkem )?" + RE_NUM + RE_M2)


def plocha(t: str, f: str) -> dict:
    kand = []
    for rx, vaha in ((RE_PLOCHA_CELKEM, 2), (RE_PLOCHA_VYMERA, 1)):
        for m in rx.finditer(f):
            v = cislo(m.group(1))
            if v and 5 <= v <= 200_000:
                w = vaha - (2 if re.search(r"budov|objekt|areal|pozemk", f[m.start():m.start() + 60]) else 0)
                kand.append((w, v, m))
    if not kand:
        return {"plocha_m2": None, "citace_plocha": None}
    # nejvyssi vaha, pak nejvetsi hodnota (celkova plocha >= dilci)
    vaha, v, m = max(kand, key=lambda x: (x[0], x[1]))
    return {"plocha_m2": v, "citace_plocha": _cit(t, m.start(), m.end(), 0)}


# -------------------------------------------------------- najemne --
# Kurz CNB pro prepocet EUR -> Kc (u kancelari na soukromem trhu je EUR standard)
KURZ_EUR = 24.325
KURZ_DATUM = "2026-10-20"
_MENA = r"(kc|eur|€)"
RE_KC_M2 = re.compile(RE_NUM + r"\s?(?:,-\s?)?" + _MENA + r"\s?(?:bez dph\s?)?(?:/|za|za 1|za jeden)\s?(?:1\s?)?(?:m2|m²|m 2)"
                      r"\s?(?:/|a|za|ročně|rocne|mesicne|za kalendarni)?\s?(rok|mesic|rocne|mesicne|kalendarni rok|kalendarni mesic)?")
RE_ROCNI = re.compile(r"(?:rocni|celkov\w* rocni) najemn\w*[^.;]{0,70}?" + RE_NUM + r"\s?(?:,-\s?)?" + _MENA)
RE_MESICNI = re.compile(r"mesicni najemn\w*[^.;]{0,70}?" + RE_NUM + r"\s?(?:,-\s?)?" + _MENA)
RE_CTVRTLETNI = re.compile(r"(?:ctvrtletni najemn\w*[^.;]{0,70}?|najemn\w* (?:bude |je )?(?:hrazeno |placeno |splatne )?"
                           r"ctvrtletne[^.;]{0,40}?)" + RE_NUM + r"\s?(?:,-\s?)?" + _MENA)
RE_POLOLETNI = re.compile(r"(?:pololetni najemn\w*[^.;]{0,70}?|najemn\w* (?:bude |je )?(?:hrazeno |placeno |splatne )?"
                          r"pololetne[^.;]{0,40}?)" + RE_NUM + r"\s?(?:,-\s?)?" + _MENA)


def _kurz(mena: str) -> float:
    return KURZ_EUR if mena in ("eur", "€") else 1.0


def najemne(t: str, f: str, plocha_m2: float | None) -> dict:
    """Kc/m2/mesic (EUR prepocteno kurzem CNB); 'mena' a 'puvodni' nesou hodnotu, jak stoji ve smlouve."""
    out = {"kc_m2_mesic": None, "kc_m2_zdroj": None, "citace_najemne": None, "mena": None, "puvodni_m2_mesic": None}
    sazby = list(RE_KC_M2.finditer(f))
    sazby.sort(key=lambda m: 0 if re.search(r"kancel|administr", f[max(0, m.start() - 120):m.end() + 40]) else 1)
    for m in sazby:
        if re.search(r"sklad|parkov|garaz|stani|archiv|reklam", f[m.end():m.end() + 40]):
            continue
        v = cislo(m.group(1))
        mena = m.group(2)
        jedn = (m.group(3) or "") + f[m.end(): m.end() + 25]
        if not v:
            continue
        if "rok" in jedn or "rocne" in jedn:
            mes = v / 12
        elif "mesic" in jedn:
            mes = v
        else:  # bez jednotky: >900 Kc (>40 EUR) za m2 mesicne je u kancelari nerealne -> rocni sazba
            mes = v if v * _kurz(mena) < 900 else v / 12
        kc = mes * _kurz(mena)
        if 20 <= kc <= 1500:
            out.update(kc_m2_mesic=round(kc, 1), kc_m2_zdroj="smlouva (sazba za m2)", mena="EUR" if _kurz(mena) > 1 else "Kc",
                       puvodni_m2_mesic=round(mes, 2), citace_najemne=_cit(t, m.start(), min(len(t), m.end() + 20), 0))
            return out
    if plocha_m2:
        for rx, k in ((RE_ROCNI, 12), (RE_POLOLETNI, 6), (RE_CTVRTLETNI, 3), (RE_MESICNI, 1)):
            m = rx.search(f)
            if m:
                v = cislo(m.group(1))
                if v:
                    mes = v / k / plocha_m2
                    kc = mes * _kurz(m.group(2))
                    if 20 <= kc <= 1500:
                        out.update(kc_m2_mesic=round(kc, 1), mena="EUR" if _kurz(m.group(2)) > 1 else "Kc",
                                   puvodni_m2_mesic=round(mes, 2),
                                   kc_m2_zdroj=f"dopocet ({ {12: 'rocni', 6: 'pololetni', 3: 'ctvrtletni', 1: 'mesicni'}[k]} najemne / plocha)",
                                   citace_najemne=_cit(t, m.start(), m.end(), 0))
                        return out
    return out


# ---------------------------------------------- opce (prodlouzeni) --
RE_OPCE = re.compile(r"\bopc(?:e|i|emi|ni\w*)\b|pravo (?:na )?prodlouz\w*|opravnen\w* (?:jednostranne )?prodlouzit|"
                     r"(?:muze|lze) (?:byt )?(?:dobu najmu |najem )?prodlouz\w*")
RE_LHUTA_PRED = re.compile(r"nejpozdeji (\d+|" + "|".join(CISLOVKY) + r")\s?(?:\(\w+\) )?(mesic\w*|tydn\w*|dn\w*)"
                           r" (?:pred|predem pred) (?:uplynutim|ukoncenim|skoncenim|koncem|datem|sjednan|zanikem)")


def opce(t: str, f: str) -> dict:
    """Moznost prodlouzeni najmu + do kdy je nutne ji uplatnit ('nejpozdeji 9 mesicu pred uplynutim')."""
    out = {"opce": None, "opce_lhuta_mesicu": None, "citace_opce": None}
    # 1) lhuta 'nejpozdeji X pred uplynutim' v kontextu prodlouzeni
    for lh in RE_LHUTA_PRED.finditer(f):
        ctx = f[max(0, lh.start() - 250): lh.end() + 300]
        if re.search(r"prodlou|dalsi (?:dob|obdob)|\bopc|obnov", ctx) and not re.search(r"vypoved", f[max(0, lh.start() - 80):lh.start()]):
            n = lh.group(1)
            n = int(n) if n.isdigit() else CISLOVKY.get(n)
            if not n:
                continue
            g = lh.group(2)
            mes = n if g.startswith("mesic") else (max(1, round(n / 4.3)) if g.startswith("tyd") else max(1, round(n / 30)))
            return {"opce": True, "opce_lhuta_mesicu": mes, "citace_opce": _cit(t, max(0, lh.start() - 90), lh.end() + 60, 0)}
    # 2) jen zminka o opci / pravu prodlouzit
    for m in RE_OPCE.finditer(f):
        if re.search(r"neni opravnen|nema pravo|bez moznosti", f[max(0, m.start() - 40):m.end() + 20]):
            continue
        return {"opce": True, "opce_lhuta_mesicu": None, "citace_opce": _cit(t, m.start(), min(len(t), m.start() + 160), 0)}
    return out


# ------------------------------------------- zvysovani najemneho --
RE_INDEX = re.compile(r"inflac\w*|index\w* (?:rustu )?spotrebitelskych cen|miru inflace|valoriz\w*|indexac\w*|"
                      r"(?:zvys|navys)\w* (?:najemne\w* )?o (\d+(?:,\d+)?) ?%")


def indexace(t: str, f: str) -> dict:
    for m in RE_INDEX.finditer(f):
        ctx = f[max(0, m.start() - 160): m.end() + 60]
        if re.search(r"najemn|uhrad|cen", ctx) and not re.search(r"nebude (?:zvys|navys|valoriz|upravov)|nezvys|nepouzij|nemen", ctx):
            return {"indexace": True, "citace_indexace": _cit(t, max(0, m.start() - 110), m.end() + 50, 0)}
    return {"indexace": None, "citace_indexace": None}


# --------------------------------------------- adresa prostor --
_VEL = "A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"
_MAL = "a-záčďéěíňóřšťúůýž"
RE_ADRESA = re.compile(rf"([{_VEL}][{_MAL}]+\.?(?: (?:[{_VEL}{_MAL}][{_MAL}]*\.?|\d{{1,2}}\.)){{0,3}}) (?:č\. ?p\. ?)?"
                       rf"(\d{{1,5}}(?:/\d{{1,4}}[a-zA-Z]?)?),? ?(\d{{3}} ?\d{{2}}),? ?"
                       rf"([{_VEL}][{_MAL}]+(?:(?: ?[-–] ?| )(?:\d{{1,2}}\b|[{_VEL}][{_MAL}]+|nad|pod|u)){{0,4}})")
RE_ADR_ANO = re.compile(r"budov|objekt|nemovitost|adresni mist|na adrese|prostor|predmet\w* najmu|v ulici|areal")
RE_ADR_NE = re.compile(r"sidl|bytem|misto podnikani|katastr|pracovist|dorucov|zastoupen|ico|dic|bankov|uct|smlouv|\bc\. \d")


def adresa(t: str, f: str) -> dict:
    """Adresa pronajatych prostor (ne sidlo stran): adresa s PSC v kontextu budovy / predmetu najmu."""
    for m in RE_ADRESA.finditer(t):
        pred = f[max(0, m.start() - 160): m.start()]
        veta = re.split(r"[.;]\s", pred[-110:])[-1]  # jen tataz veta (predchozi veta muze byt o sidle strany)
        if RE_ADR_ANO.search(pred) and not RE_ADR_NE.search(veta) and not RE_ADR_NE.search(f[m.start():m.end()]):
            adr = re.sub(r"(?:\s+(?:Nájemce|Pronajímatel|Smlouva|Článek|Budova|Předmět|IČO?|DIČ|Tel|Zastoupen\w*|Česká|Obsah|Směrnice|E-mail|Zapsan\w*|Účel|Pokud|Denní|Nájemné|Strana|Předmětem|Smluvní))+$", "",
                         m.group(0)).strip(" ,")
            return {"adresa": adr, "citace_adresa": _cit(t, max(0, m.start() - 80), m.end(), 0)}
    return {"adresa": None, "citace_adresa": None}


# --------------------------------------- strany a clanky (citace) --
RE_STRANA = re.compile(r"\[\[STRANA (\d+)\]\]")
RE_CLANEK = re.compile(r"(?:^|[\s(])(?:clanek|cl\.)\s?([ivxlc]{1,6}|\d{1,2})\.?(?=[\s.,)]|$)")


RE_RIMSKE = re.compile(r"x{0,3}(?:ix|iv|v?i{0,3})")


class Dokument:
    """Text s cisly stran: normalizovany text t / f + zacatky stran -> citace 'str. X, cl. Y'."""

    def __init__(self, text: str):
        casti = RE_STRANA.split(text)
        stranky: list[tuple[int, str]] = []
        if len(casti) == 1:
            stranky = [(1, text)]
        else:
            if casti[0].strip():
                stranky.append((1, casti[0]))
            for i in range(1, len(casti), 2):
                stranky.append((int(casti[i]), casti[i + 1]))
        ts, fs, self.zacatky, self.cisla, pos = [], [], [], [], 0
        for n, txt in stranky:
            tt, ff = normalizuj(txt)
            tt, ff = tt.strip(), ff.strip()
            self.zacatky.append(pos)
            self.cisla.append(n)
            ts.append(tt)
            fs.append(ff)
            pos += len(tt) + 1
        self.t, self.f = " ".join(ts), " ".join(fs)
        self.ma_strany = len(casti) > 1

    def strana(self, pos: int) -> int | None:
        if not self.ma_strany or pos < 0:
            return None
        import bisect
        return self.cisla[max(0, bisect.bisect_right(self.zacatky, pos) - 1)]

    def clanek(self, pos: int) -> str | None:
        """Nejblizsi 'Clanek IV' / 'cl. 5' nad citaci (rimske cislice jen platne a v originale velkymi)."""
        last = None
        for m in RE_CLANEK.finditer(self.f, max(0, pos - 6000), pos + 1):
            g = m.group(1)
            if g.isdigit():
                if 0 < int(g) <= 40:
                    last = g
            elif RE_RIMSKE.fullmatch(g) and self.t[m.start(1):m.end(1)].isupper():
                last = g.upper()
        return last

    def najdi(self, citace: str | None) -> int:
        """Pozice citace v textu (presne, jinak bez diakritiky podle prvnich 50 znaku); -1 = nenalezeno."""
        if not citace:
            return -1
        c = re.sub(r"\s+", " ", citace).strip()
        i = self.t.find(c)
        if i >= 0:
            return i
        cf = _fold(c)[:50].strip()
        return self.f.find(cf) if len(cf) >= 12 else -1

    def misto(self, citace: str | None) -> dict:
        i = self.najdi(citace)
        return {"str": self.strana(i), "cl": self.clanek(i) if i >= 0 else None, "nalezeno": i >= 0}


# ------------------------------------ hodnota musi byt v citaci --
def _cisla_v(s: str) -> list[float]:
    out = []
    for m in re.finditer(r"(?<![\d,.])(\d{1,3}(?:[ .]\d{3})+(?:,\d+)?|\d+(?:,\d+)?)(?!\d)", _fold(re.sub(r"\s+", " ", s))):
        v = cislo(m.group(1))
        if v is not None:
            out.append(v)
    return out


def hodnota_v_citaci(hodnota, citace: str | None, druh: str) -> bool:
    """Udaj je 'overeny' jen tehdy, kdyz citace skutecne obsahuje tuto hodnotu (ne jen nejaky text)."""
    if hodnota is None or not citace:
        return False
    f = _fold(re.sub(r"\s+", " ", citace))
    if druh == "datum":
        for m in RE_DATUM.finditer(f):
            if _datum(m) == hodnota:
                return True
        return False
    if druh == "mesice":
        n = int(round(float(hodnota)))
        slova = [k for k, v in CISLOVKY.items() if v == n]
        return bool(re.search(r"(?<!\d)" + str(n) + r"(?!\d)", f)) or any(re.search(r"\b" + w + r"\b", f) for w in slova)
    cisla = _cisla_v(citace)
    nasobky = (1,) if druh == "plocha" else (1, 12, 3, 6, 1 / KURZ_EUR, 12 / KURZ_EUR)
    return any(abs(c - hodnota * k) <= 0.011 * max(1.0, abs(hodnota * k)) for c in cisla for k in nasobky)


RE_ZMENSENI = re.compile(r"snizeni (?:rozsahu|vymery|plochy|predmetu)|zmenseni (?:rozsahu|predmetu|plochy)|"
                         r"vyjim\w* z predmetu|vynim\w* z predmetu|cast\w* predmetu najmu (?:se )?vrac")
RE_ROZSIRENI = re.compile(r"rozsireni (?:predmetu|plochy|najmu|pronajat\w*|rozsahu (?:pronajat|najat|ploch|prostor))|navyseni (?:plochy|vymery|rozsahu|predmetu)|"
                          r"predmet najmu se rozsiruje|rozsiruje (?:se )?o")


def extrahuj_dokument(d: "Dokument") -> dict:
    t, f = d.t, d.f
    def _hledej(rx):
        for m in rx.finditer(f):
            if not re.search(r"v pripade|pokud|v pomeru|lze|muze|opravnen", f[max(0, m.start() - 50):m.start()]):
                return m
        return None
    zm = _hledej(RE_ZMENSENI)
    rz = _hledej(RE_ROZSIRENI)
    flags = {"zmenseni": bool(zm), "citace_zmenseni": _cit(t, zm.start(), zm.end(), 80) if zm else None,
             "rozsireni": bool(rz), "citace_rozsireni": _cit(t, rz.start(), rz.end(), 80) if rz else None}
    dd = doba(t, f)
    p = plocha(t, f)
    return {**dd, **vypoved(t, f), **p, **najemne(t, f, p["plocha_m2"]), **opce(t, f), **indexace(t, f), **adresa(t, f),
            **flags}


def extrahuj(text: str) -> dict:
    return extrahuj_dokument(Dokument(text))
