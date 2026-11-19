"""
Sběr nových inzerátů bytů a domů v Praze z Bazoš.cz a Sbazar.cz.
Výstup: docs/data.json (čte ho dashboard docs/index.html).
Volitelně posílá nové inzeráty do Telegramu (secrets TELEGRAM_TOKEN, TELEGRAM_CHAT_ID).
"""
import json, os, re, sys, time, random
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ------------------------------------------------------------------ NASTAVENÍ
DATA_FILE = Path("docs/data.json")
KEEP_DAYS = 30            # jak dlouho držet inzeráty v datech
MIN_PAGES = 3             # min. stránek na kategorii (kvůli TOPovaným inzerátům nahoře)
MAX_PAGES = 8             # max. stránek na jednu kategorii a běh (40 inzerátů/str. na Bazoši)

# Bazoš: jedno velké hledání kolem Prahy na každou kategorii, výsledky se pak filtrují podle PSČ.
BAZOS_AKCE = {"prodej": "prodam", "pronajem": "pronajmu"}
BAZOS_CENTER, BAZOS_RADIUS = "11000", "70"           # Praha + 70 km
BAZOS_SEARCHES = [                                  # (akce, typ = rubrika na Bazoši)
    ("prodej", "byt"), ("prodej", "dum"), ("pronajem", "byt"), ("pronajem", "dum"),
    ("prodej", "pozemek"), ("prodej", "chata"), ("prodej", "garaz"),
]
# Pozemky: pole, louky, lesy a jiná zemědělská půda Boba nezajímají (stavební / k bydlení se nechávají)
FIELD_RE = r"\bpol[eií]\b|\bpolí\b|orn[áéou]\w* půd|zemědělsk|\blouk|\bles\b|\blesy\b|lesní|lesů|pastvin|travní porost|\bttp\b"
BUILD_RE = r"stavební|k bydlení|pro bydlení|pozemky/bydlení|ke stavbě|rodinn"
POZEMEK_MIN = 1_000_000  # prodej pozemků až od této ceny
# Bob nechce: sruby, mobilheimy / mobilní a modulové domy, nemovitosti v zahraničí
UNWANTED_RE = r"\bsrub|karavan|mobilheim|mobil home|mobilní d[ůo]m|mobilní domek|modulov\w* d[ůo]m|maringotk|tiny ?house"
ABROAD_RE = (r"v zahraničí|chorvatsk[oua]\b|španělsk[oua]\b|\bitáli[ei]\b|bulharsk[oua]\b|řeck[oua]\b|\begypt|dubaj|emirát"
             r"|tureck[oua]\b|\bkypr|černá hora|černé hoře|thajsk[oua]\b|\bbali\b|albáni|portugalsk[oua]\b"
             r"|hurghad|sahl hasheesh|\bu moře|od moře|k moři|na pláži|costa |istri[ie]|dalmáci|slunečné pobřeží|chalkidik|tenerif|kanárs")
PRONAJEM_MIN_MIMO_PRAHU = 11_000   # pronájmy mimo Prahu až od této ceny (bez ceny se nechávají)
# Posílá se: Praha (PSČ 1xx xx) + okolí obcí podle prvních 3 číslic PSČ (pošt. obvod ≈ obec + ~10 km):
NEAR_PSC = {
    "250",  # Brandýs n. L., Úvaly, Čelákovice, Odolena Voda, Klecany, Zdiby
    "251",  # Říčany, Mnichovice, Strančice
    "252",  # Průhonice, Jesenice, Černošice, Rudná, Roztoky, Mníšek p. Brdy, Dobřichovice, Řevnice
    "253",  # Hostivice
    "256", "257",  # Benešov
    "263",  # Dobříš
    "266", "267",  # Beroun
    "272", "273",  # Kladno, Unhošť
    "274",  # Slaný
    "276",  # Mělník
    "277",  # Neratovice
    "278",  # Kralupy n. Vlt.
    "280", "281",  # Kolín
    "288", "289",  # Nymburk, Lysá n. L.
}
# Drahé nemovitosti: všechny rubriky (i komerční), Praha + celý Středočeský kraj (PSČ 25x–29x)
EXPENSIVE_MIN = 5_000_000
EXPENSIVE_RADIUS = "90"
SCHEME = "v3"             # změna schématu hledání -> první běh jen uloží (nic nepošle)

# Sbazar: vlož URL vyhledávání zkopírované z prohlížeče (Praha, byty/domy, prodej/pronájem).
# Klíč = (akce, typ). Prázdné URL se přeskočí.
SBAZAR_URLS = {
    ("prodej", "byt"): "",
    ("pronajem", "byt"): "",
    ("prodej", "dum"): "",
    ("pronajem", "dum"): "",
}

# Telegram: posílat jen inzeráty, které splní tyto podmínky (None = nefiltrovat)
NOTIFY = {
    "akce": None,          # "prodej" / "pronajem" / None
    "typ": None,           # "byt" / "dum" / None
    "max_cena": None,      # např. 8_000_000
    "min_m2": None,        # např. 50
    "dispozice": None,     # např. ["2+kk", "3+kk"]
}
WA_TEXT = {
    "pronajem": ("Dobrý den, jsem Robert Harman, realitní makléř z Prahy. Viděl jsem Váš inzerát na pronájem {co}.\n\n"
                 "Pokud byste nechtěl řešit desítky telefonátů a prohlídek, rád to vezmu za Vás: vyberu a prověřím "
                 "spolehlivého nájemníka, připravím smlouvu i předání {co2}. Vy jen podepíšete.\n\n"
                 "Kdyby Vás to zajímalo, stačí odpovědět. Pokud ne, nevadí, přeji rychlé pronajmutí. 🙂\n\n"
                 "Robert Harman\nDominius.cz"),
    "prodej": ("Dobrý den, jsem Robert Harman, realitní makléř z Prahy. Viděl jsem Váš inzerát na prodej {co}.\n\n"
               "Pokud byste nechtěl řešit desítky telefonátů a prohlídek, rád to vezmu za Vás: najdu a prověřím "
               "vážného kupce, zajistím prohlídky, kupní smlouvu i bezpečný převod. Vy jen podepíšete.\n\n"
               "Kdyby Vás to zajímalo, stačí odpovědět. Pokud ne, nevadí, přeji rychlý prodej. 🙂\n\n"
               "Robert Harman\nDominius.cz"),
}
MAX_SINGLE = 15         # kolik inzerátů poslat samostatně, zbytek přijde jako souhrn

# Filtr realitek: "rk_ven" = posílat soukromníky i nejisté (vyřadí jen jasné RK),
#                 "jen_soukromi" = posílat jen jasné soukromníky, None = posílat vše
SELLER_FILTER = "rk_ven"
MOTIVATED_WEEKDAY, MOTIVATED_HOUR_UTC = 0, 6   # pondělí od 7:00 (zima) / 8:00 (léto) – týdenní přehled motivovaných
DIGEST_HOUR_UTC = 16    # denní souhrn vyřazených (16 UTC = 18:00 v létě / 17:00 v zimě)
MAX_DETAILS = 100       # max. detailů inzerátů načtených za jeden běh (šetrnost k Bazoši)
RK_MIN_LISTINGS = 3     # od tolika různých inzerátů je prodávající podezřelý (❔ Možná RK)
RK_SURE_LISTINGS = 6    # od tolika různých inzerátů = realitka (vyřadit)

RK_WORDS = [
    r"realitn\w* kancel", r"makl[eé]ř", r"provize", r"zprostředk", r"exkluzivně", r"exkluzivn\w* (nabíd|nabíz|prodej|zastoupen|smlouv)",
    r"naše společnost", r"naše kancelář",     r"id nabídky", r"číslo nabídky", r"kód nabídky", r"\bev\. ?č", r"\bev\. ?číslo",
    r"poplatek za (podnájem|zprostředkování|služby)", r"\w*realit\w*\.(cz|com|eu)",
    r"číslo zakázky", r"id zakázky", r"evidenční číslo", r"kontaktujte (makléře|naši)",
    r"rezervační (poplatek|smlouv)", r"právní servis", r"financování zajistíme", r"hypoteční poradenství",
    r"výhradn\w* (zastoupen|smlouv|prodej|nabíd|zprostředk)", r"v zastoupení (majitel|vlastník)",
    r"re/?max", r"century ?21", r"m ?& ?m reality", r"m ?& ?m\b", r"maxima reality", r"svoboda ?& ?williams",
    r"engel ?& ?v[oö]lkers", r"lexxus", r"bidli", r"realitymix", r"sting", r"mm reality",
    r"reality\.cz", r"realit[ay]\b.*s\.r\.o", r"s\.r\.o\.", r"\bs\.? ?r\.? ?o\b", r"\bspol\.", r"a\.s\.", r"properties", r"real estate",
]
# Slova v TEXTU, která samy o sobě inzerát nevyřadí – pošle se jako ❔ nejistý (Bob 14. 11.: raději víc inzerátů).
# Silné znaky (výhradní zastoupení, ev. č., ID nabídky, názvy RK, weby realitek, s.r.o.) dál vyřazují.
RK_TEXT_SOFT = [r"provize", r"makl", r"zprostředk", r"realitn", r"exkluziv", r"naše společnost", r"naše kancelář",
                r"kontaktujte", r"rezervační", r"právní servis", r"financování", r"hypoteční", r"poplatek za"]

# Konkrétní realitky / makléři, které chceš vždy vyřadit (stačí část jména, malá písmena)
RK_SELLERS = [
    "ladislav burian", "realbrána", "realbrana",
    "ideální nájemce", "dumrealit", "jan paschke", "paschke", "hvb real estate", "finpos", "aleš doubek", "makers reality", "gepard",
    "vlasta marklová", "žalmánek", "váš konzultant realit", "realityspolu", "reality spolu", "petráčková",
    "next reality", "karel zajac", "zoom", "bohemian estates", "broker consulting", "pavelková", "jiří beneš", "petr schejbal", "vladimír kremel", "vladimir kremel", "jaroslav čermák", "jaroslav cermak", "femida", "martin petlach", "adam horák", "adam horak", "marek bijok", "karel budín", "karel budin", "martin kršňák", "martin krsnak", "luboš rezek", "lubos rezek", "luboš řezek", "jana barčuková", "jana barcukova", "tomáš tvoroch", "tomas tvoroch", "tomáš tvaroh", "rk evropa", "bohumír kocián", "bohumir kocian", "lukáš kuchař", "lukas kuchar", "dyrma", "martin lubas",
]

# Bazoš ID prodávajícího (idphone) nahlášených realitek – chytí je i pod jiným jménem
RK_SELLER_IDS = {"19659032", "19127202", "19056743", "19006547", "19623771", "21100492", "19135889", "19165354", "19117937", "19208053", "19151196", "19194521", "19057417", "19058815", "19135727", "19180520", "19093549", "19135726", "19204461", "19115807"}   # Pavelková, Jiří Beneš, TM (2×), Petr Schejbal, Vladimír Kremel, Jaroslav Čermák, Femida, Martin Petlach, Adam Horák, Marek Bijok, Karel Budín („Martina“), Martin Kršňák, Luboš Rezek, Jana Barčuková, Tomáš Tvoroch (RK Evropa), Bohumír Kocián, Lukáš Kuchař, Oleksandr Dyrma, Martin Lubas
# Krátká jména RK – musí se shodovat s celým jménem prodávajícího (v textu se nehledají)
RK_SELLERS_EXACT = {"tm"}

# Inzeráty, kde majitel výslovně nechce RK – vyřadit (nemá smysl volat)
_RK = r"(?<!\w)r\.?\s?k\b\.?"         # RK, R.K, R.K., R. K.
NO_RK_WORDS = [
    r"(" + _RK + r"|realitk\w*|realitní\w* kancelář\w*|makléř\w*)[^\n]{0,25}?(nevolat|nevolejte|nekontaktovat|nekontaktujte|neozývat|neozývejte|neodpovídám|nepište|ne,? děkuji|děkuji,? ne)",
    r"(nevolat|nevolejte|nekontaktovat|nekontaktujte)[^.\n]{0,15}(" + _RK + r"|realitk\w*|realitní\w* kancelář\w*|makléř\w*)",
    r"(nemám|nemáme) zájem o (" + _RK + r"|realitk\w*|služby (" + _RK + r"|realitk\w*|makléř\w*))",
    r"bez zájmu o (" + _RK + r"|realitk\w*)", r"spolupráci s (" + _RK + r"|realitk\w*) (nechci|nepožaduji|odmítám)",
]

# Slabé znaky RK: samy o sobě inzerát nevyřadí, jen ho označí jako ❔ nejistý
RK_WEAK = [r"exkluziv", r"\brk\b", r"nabízíme (vám )?(k )?(prodeji|pronájmu|podnájmu)", r"nabízíme vám"]

PRIVATE_WORDS = [r"bez rk", r"nejsem rk", r"ne ?rk", r"přímo od majitele", r"bez proviz\w*", r"provize se neplatí", r"bez poplatku rk", r"od majitele", r"jsem majitel", r"soukrom[áý] osoba", r"bez realitky",
                 r"bez zprostředk\w*", r"bez realitní\w* kancelář\w*", r"žádn\w* proviz\w*", r"neplatí\w* (žádnou )?proviz\w*",
                 r"nejsem (realitk\w*|makléř\w*)", r"přímému zájemci", r"bez makléř\w*", r"pouze přím\w* zájem\w*"]
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept-Language": "cs-CZ,cs;q=0.9",
}
S = requests.Session()
S.headers.update(HEADERS)
NOW = datetime.now(timezone.utc)


def log(*a):
    print(*a, flush=True)


START = time.time()
MAX_RUNTIME = 6 * 60      # po 6 minutách přestat stahovat a uložit, co máme


class OutOfTime(Exception):
    pass


def get(url, params=None):
    if time.time() - START > MAX_RUNTIME:
        raise OutOfTime("vyčerpán časový limit běhu")
    time.sleep(random.uniform(1.5, 3.0))   # šetrně k serverům
    r = S.get(url, params=params, timeout=(10, 20))
    r.raise_for_status()
    return r.text


def parse_price(text):
    if not text:
        return None
    t = text.replace("\xa0", " ")
    num = r"(\d{1,3}(?:[ .]\d{3})+|\d{4,})"
    m = re.search(num + r"\s*(?:Kč|,-|CZK)", t, re.I) or re.search(num, t)
    if not m:
        return None
    try:
        v = int(re.sub(r"\D", "", m.group(1)))
        return v if v >= 1000 else None
    except ValueError:
        return None


def parse_layout(text):
    m = re.search(r"\b(\d)\s?\+\s?(kk|1)\b", text, re.I)
    return f"{m.group(1)}+{m.group(2).lower()}" if m else None


def parse_area(text):
    for m in re.finditer(r"(\d{2,4}(?:[.,]\d{1,2})?)\s?(?:m2|m²|m\^2|metr|㎡)", text, re.I):
        v = float(m.group(1).replace(",", "."))
        if 12 <= v <= 1500:
            return round(v)
    return None


def parse_district(text):
    m = re.search(r"Praha[\s-]*(\d{1,2})\b", text, re.I)
    return f"Praha {int(m.group(1))}" if m else ("Praha" if re.search(r"praha", text, re.I) else None)


def enrich(item):
    blob = f"{item.get('title','')} {item.get('desc','')}"
    item.setdefault("layout", parse_layout(blob))
    item.setdefault("area", parse_area(blob))
    if not item.get("district"):
        item["district"] = parse_district(f"{item.get('locality','')} {blob}")
    if item.get("price") and item.get("area"):
        item["ppm2"] = round(item["price"] / item["area"])
    return item


# ------------------------------------------------------------------ BAZOŠ
TYP_CO = {"byt": "bytu", "dum": "domu", "pozemek": "pozemku", "chata": "chaty", "garaz": "garáže"}
TYP_LABEL = {"pozemek": "Pozemek", "chata": "Chata/chalupa", "garaz": "Garáž", "jine": "Jiná nemovitost"}


def guess_typ(*texts):
    """Typ podle slova, které se v titulku (pak v popisu) objeví nejdřív."""
    rules = (("garaz", r"garáž|garaz|parkovací stání"), ("chata", r"chat[auy]\b|chalup|rekreační"),
             ("byt", r"\bbyt|\d\s?\+\s?(kk|1)\b|garsoni|ateliér"), ("dum", r"\bd[ůo]m\b|\bdomu\b|\bvil[ay]\b|rodinn"),
             ("pozemek", r"pozem|parcel|zahrad"))
    for t in texts:
        hits = [(m.start(), typ) for typ, rx in rules for m in [re.search(rx, t.lower())] if m]
        if hits:
            return min(hits)[1]
    return "jine"


def near(psc, price):
    return bool(psc) and (psc.startswith("1") or psc[:3] in NEAR_PSC)


def expensive(psc, price):
    return bool(psc) and bool(re.match(r"1|2[5-9]", psc)) and (price or 0) >= EXPENSIVE_MIN


def bazos_page(akce, typ, offset, params, accept):
    """Vrátí (inzeráty prošlé filtrem accept(psč, cena), počet inzerátů na stránce celkem)."""
    path = f"https://reality.bazos.cz/{BAZOS_AKCE[akce]}/" + (f"{typ}/" if typ else "")
    if offset:
        path += f"{offset}/"
    try:
        html = get(path, params)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return [], 0       # za poslední stránkou výsledků
        raise
    soup = BeautifulSoup(html, "lxml")
    out, raw = [], 0
    for box in soup.select("div.inzeraty"):
        a = box.select_one("h2.nadpis a, .nadpis a")
        if not a:
            continue
        href = a.get("href", "")
        m = re.search(r"/inzerat/(\d+)/", href)
        if not m:
            continue
        raw += 1
        url = href if href.startswith("http") else "https://reality.bazos.cz" + href
        loc_el = box.select_one(".inzeratylok")
        loc = loc_el.get_text(" ", strip=True) if loc_el else ""
        psc = re.search(r"\b(\d{3})\s?(\d{2})\b", loc)
        psc = f"{psc.group(1)} {psc.group(2)}" if psc else None
        price_el = box.select_one(".inzeratycena")
        price = parse_price(price_el.get_text(" ", strip=True) if price_el else "")
        if not accept(psc, price):
            continue
        title = a.get_text(strip=True)
        if re.match(r"\s*(hled[aá]m|koup[ií]m|popt[aá]v\w*|sh[aá]n[ií]m)(e|me)?\b", title, re.I):
            continue                              # poptávka, ne nabídka
        full = box.get_text(" ", strip=True)
        d = re.search(r"\[(\d{1,2})\.(\d{1,2})\.\s?(\d{4})\]", full)
        posted = f"{d.group(3)}-{int(d.group(2)):02d}-{int(d.group(1)):02d}" if d else None
        img = box.select_one("img")
        desc_el = box.select_one(".popis")
        desc = (desc_el.get_text(" ", strip=True) if desc_el else "")[:400]
        locality = loc.replace(psc or "", "").strip()
        typ_i = typ or guess_typ(title, desc)
        blob = f"{title} {desc}".lower()
        if typ_i == "pozemek" and re.search(FIELD_RE, blob) and not re.search(BUILD_RE, blob):
            continue                              # pole / les / louka – nezajímá
        if re.search(UNWANTED_RE, blob) or re.search(ABROAD_RE, blob):
            continue                              # srub / mobilheim / zahraničí – nezajímá
        if akce == "pronajem" and not (psc or "").startswith("1") and price and price < PRONAJEM_MIN_MIMO_PRAHU:
            continue                              # levný pronájem mimo Prahu – nezajímá
        if typ_i == "pozemek" and akce == "prodej" and price and price < POZEMEK_MIN:
            continue                              # levné pozemky nezajímají (bez ceny se nechávají)
        out.append(enrich({
            "key": f"bazos:{m.group(1)}",
            "source": "bazos",
            "url": url,
            "title": title,
            "desc": desc,
            "price": price,
            "price_text": price_el.get_text(" ", strip=True) if price_el else "",
            "locality": locality,
            "psc": psc,
            "img": img.get("src") if img else None,
            "posted": posted,
            "akce": akce,
            "typ": typ_i,
            "district": None if (psc or "").startswith("1") else (locality or None),
            "search": f"{akce}/{typ or 'vse'}",
        }))
    return out, raw


def bazos_detail(item):
    """Načte detail inzerátu: celý popis a jméno / ID prodávajícího."""
    try:
        html = get(item["url"])
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        log(f"[bazos] detail {item['key']}: CHYBA {e}")
        return
    if not Path("docs/probe/bazos_detail.html").exists():
        Path("docs/probe").mkdir(parents=True, exist_ok=True)
        Path("docs/probe/bazos_detail.html").write_text(html, "utf-8")
    d = soup.select_one(".popisdetail")
    if d:
        item["desc_full"] = d.get_text(" ", strip=True)[:3000]
    for td in soup.find_all("td"):
        if td.get_text(strip=True).rstrip(":").lower() == "jméno":
            nxt = td.find_next("td")
            if nxt:
                item["seller"] = nxt.get_text(" ", strip=True)[:80]
            break
    for a in soup.select('a[href*="idphone"], a[href*="idmail"], a[href*="hodnoceni"]'):
        m = re.search(r"id(?:phone|mail)=(\w+)", a.get("href", ""))
        if m:
            item["seller_id"] = m.group(1)
            break
    try:
        bazos_phone(item, soup, html)
    except Exception as e:
        log(f"[bazos] telefon {item['key']}: CHYBA {e}")


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}", re.I)
PHONE_RE = re.compile(r"(?:\+?420[\s.]?)?([67]\d{2})[\s.]?(\d{3})[\s.]?(\d{3})\b")


def _phone_from(text):
    m = PHONE_RE.search(text or "")
    return f"+420{m.group(1)}{m.group(2)}{m.group(3)}" if m else None


def bazos_phone(item, soup, html):
    """Telefon jen pokud je přímo na stránce (odkaz tel:). Bazoš číslo přes
    ad-phone.php nevydává, proto se na něj už nedotazujeme (jen bereme idphone)."""
    for a in soup.select('a[href^="tel:"]'):
        ph = _phone_from(a.get("href"))
        if ph:
            item["phone"] = ph
            return
    m = re.search(r"odeslatrequest\(\s*'[^']*phone[^']*'\s*,\s*'([^']*)'", html)
    if m and not item.get("seller_id"):
        m2 = re.search(r"idphone=(\w+)", m.group(1))
        if m2:
            item["seller_id"] = m2.group(1)


RK_KNOWN, RK_PHONES = set(), set()
# „Jan Novák - Praha“ je místo, ne firma
PLACE_RE = (r"(praha|praha[\s-]*\d+|praha[\s-]*(západ|východ|zapad|vychod)|brno|kladno|beroun|benešov|mělník|kolín|nymburk|říčany"
            r"|neratovice|kralupy.*|brandýs.*|slaný|úvaly|čelákovice|roztoky|hostivice|dobříš|lysá.*|okolí prahy)")
# z čeho si hlídač pamatuje prodávajícího jako RK (ne z „dříve vyřazen“ – to by se samo řetězilo, ne ze slova v textu)
STRONG_RK_REASONS = ("na seznamu RK", "nahlášená RK", "jméno s firmou", "jméno firmy", "jméno prodávajícího", "telefon patří")


def _norm_phone(ph):
    d = re.sub(r"\D", "", str(ph))
    return d[-9:] if len(d) >= 9 else d


def _norm_title(t):
    return re.sub(r"[^a-z0-9á-ž]+", " ", (t or "").lower()).strip()


def classify(item, seller_counts):
    """Jako _classify; navíc „RK nevolat" už inzerát nevyřadí (Bob 16. 11.) – přijde s varováním ⚠️."""
    st, why = _classify(item, seller_counts)
    text = " ".join(str(item.get(k) or "") for k in ("title", "desc", "desc_full")).lower()
    if st != "rk" and any(re.search(w, text) for w in NO_RK_WORDS):
        return "nejiste", "majitel nechce RK"
    return st, why


def _classify(item, seller_counts):
    """Vrací 'rk', 'soukromy' nebo 'nejiste' + důvod."""
    if item.get("source") == "ulovdomov":
        if item.get("ud_external"):
            return "rk", "převzato z realitního portálu"
        if item.get("ud_private") is False:
            return "rk", "UlovDomov: není soukromý majitel"
    text = " ".join(str(item.get(k) or "") for k in ("title", "desc", "desc_full")).lower()
    seller = re.sub(r"\s+", " ", (item.get("seller") or "")).strip().lower()
    for name in RK_SELLERS:
        pat = r"(?<!\w)" + re.escape(name) + r"(?!\w)"
        if re.search(pat, seller) or re.search(pat, text):
            return "rk", f"na seznamu RK ({name})"
    if seller.strip() in RK_SELLERS_EXACT:
        return "rk", f"na seznamu RK ({item.get('seller')})"
    if item.get("seller_id") and str(item["seller_id"]) in RK_SELLER_IDS:
        return "rk", f"nahlášená RK (ID {item['seller_id']})"
    if re.search(r"(?<!\w)(rk|r\.k\.?|realitka|reality|infolinka)(?!\w)", seller):
        return "rk", f"jméno firmy ({item.get('seller')})"
    _after = re.split(r"\s[-–|]\s*", item.get("seller") or "", maxsplit=1)
    if len(_after) == 2 and _after[1].strip() and not re.fullmatch(PLACE_RE, _after[1].strip().lower()):
        return "rk", f"jméno s firmou ({item.get('seller')})"
    if re.search(r"\w(real|reality|estate|invest)\b|,", seller) or \
            re.search(r"\bREAL|\bReal(?:it|\b|[A-ZÁ-Ž])", item.get("seller") or ""):
        return "rk", f"jméno firmy ({item.get('seller')})"
    for w in RK_WORDS:
        if re.search(w, seller):
            return "rk", f"jméno prodávajícího ({item.get('seller')})"
    if seller and len(seller.split()) >= 2 and seller in RK_KNOWN:
        return "rk", f"dříve vyřazen jako RK ({item.get('seller')})"
    if item.get("phone") and _norm_phone(item["phone"]) in RK_PHONES:
        return "rk", "telefon patří realitce"
    if item.get("phone") and seller_counts.get("tel:" + _norm_phone(item["phone"]), 0) >= RK_MIN_LISTINGS:
        return "rk", "stejný telefon u více inzerátů"
    sid = item.get("seller_id") or seller
    if sid and seller_counts.get(sid, 0) >= RK_SURE_LISTINGS:
        return "rk", f"{seller_counts[sid]} inzerátů od stejného prodávajícího"
    if sid and seller_counts.get(sid, 0) >= RK_MIN_LISTINGS:   # 3–5 inzerátů: může být majitel více bytů
        return "nejiste", f"má {seller_counts[sid]} různé inzeráty – možná RK"
    raw_text = " ".join(str(item.get(k) or "") for k in ("title", "desc", "desc_full"))
    m = re.search(r"\bR\.?\s?K\.?\s+[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][a-záčďéěíňóřšťúůýž]+\s+[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][a-záčďéěíňóřšťúůýž]+", raw_text)
    if m:                                        # „RK Karel Budín" = makléř se podepsal
        return "rk", f"text inzerátu ({m.group(0)})"
    priv = [w for w in PRIVATE_WORDS if re.search(w, text)]
    clean = text
    for w in PRIVATE_WORDS:                      # "bez RK" nesmí spustit RK
        clean = re.sub(w, " ", clean)
    hits = [w for w in RK_WORDS if re.search(w, clean)]
    if hits and not priv:
        strong = [w for w in hits if not any(re.match(sw, w) for sw in RK_TEXT_SOFT)]
        label = re.sub(r"\\b|\\", "", (strong or hits)[0])
        if strong:
            return "rk", "text inzerátu (" + label + ")"
        return "nejiste", "jen slovo v textu (" + label + ") – možná RK"
    weak = [w for w in RK_WEAK if re.search(w, clean)]
    if weak and not priv:
        return "nejiste", "slabý znak RK v textu"
    if priv and not hits:
        return "soukromy", "v textu uvádí soukromou nabídku"
    if item.get("seller") and not hits:
        return "soukromy", "jméno fyzické osoby, žádné znaky RK"
    return "nejiste", "nedostatek informací"


def scrape_bazos(known):
    found, seen = [], set()
    near_params = {"hlokalita": BAZOS_CENTER, "humkreis": BAZOS_RADIUS}
    exp_params = {"hlokalita": BAZOS_CENTER, "humkreis": EXPENSIVE_RADIUS, "cenaod": str(EXPENSIVE_MIN)}
    searches = [(akce, typ, near_params, near, f"{akce}/{typ}") for akce, typ in BAZOS_SEARCHES]
    searches.append(("prodej", None, exp_params, expensive, f"prodej/vše od {EXPENSIVE_MIN // 1_000_000} mil."))
    for akce, typ, params, accept, label in searches:
        idle = 0
        for p in range(MAX_PAGES):
            try:
                items, raw = bazos_page(akce, typ, p * 40, params, accept)
            except OutOfTime:
                log("[bazos] vyčerpán čas běhu, zbytek příště")
                return found
            except Exception as e:
                log(f"[bazos] {label} str.{p}: CHYBA {e}")
                break
            items = [i for i in items if i["key"] not in seen]
            seen.update(i["key"] for i in items)
            found += items
            fresh = [i for i in items if i["key"] not in known]
            log(f"[bazos] {label} str.{p}: {raw} na stránce, v oblasti {len(items)}, nových {len(fresh)}")
            idle = 0 if fresh else idle + 1
            # nahoře bývají TOPované (staré) inzeráty, nové mohou být až na 2.–3. stránce:
            # projít aspoň MIN_PAGES stránek a skončit až po 2 stránkách bez nového
            if not raw or (p + 1 >= MIN_PAGES and idle >= 2):
                break
    return found


# ------------------------------------------------------------------ SBAZAR
def _walk_json(obj):
    """Najde v libovolném JSONu objekty, které vypadají jako inzerát."""
    if isinstance(obj, dict):
        keys = set(obj)
        if "id" in keys and ("name" in keys or "title" in keys) and ("price" in keys or "seo_name" in keys):
            yield obj
        for v in obj.values():
            yield from _walk_json(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_json(v)


def sbazar_parse(html, akce, typ):
    soup = BeautifulSoup(html, "lxml")
    out = {}

    # 1) vložená JSON data stránky (Next.js / state / ld+json)
    for sc in soup.find_all("script"):
        txt = sc.string or ""
        if not txt or ("price" not in txt and "Offer" not in txt):
            continue
        txt = txt.strip()
        cand = None
        try:
            cand = json.loads(txt)
        except Exception:
            m = re.search(r"=\s*(\{.*\})\s*;?\s*$", txt, re.S)
            if m:
                try:
                    cand = json.loads(m.group(1))
                except Exception:
                    pass
        if cand is None:
            continue
        for o in _walk_json(cand):
            iid = str(o.get("id"))
            title = o.get("name") or o.get("title") or ""
            price = o.get("price")
            if isinstance(price, dict):
                price = price.get("value") or price.get("amount")
            loc = o.get("locality") or {}
            loc_txt = " ".join(str(v) for v in loc.values()) if isinstance(loc, dict) else str(loc)
            seo = o.get("seo_name") or ""
            user = (o.get("user") or {}).get("user_service", {}).get("shop_url") if isinstance(o.get("user"), dict) else None
            url = o.get("url") or (f"https://www.sbazar.cz/{user}/detail/{iid}-{seo}" if user and seo else None)
            img = None
            imgs = o.get("images") or []
            if imgs and isinstance(imgs, list) and isinstance(imgs[0], dict):
                img = imgs[0].get("url")
                if img and img.startswith("//"):
                    img = "https:" + img
            if not url:
                continue
            out[iid] = {"key": f"sbazar:{iid}", "url": url, "title": title,
                        "desc": (o.get("description") or "")[:400],
                        "price": int(price) if isinstance(price, (int, float)) and price > 999 else None,
                        "locality": loc_txt, "img": img,
                        "posted": (o.get("create_date") or o.get("created") or "")[:10] or None}

    # 2) záloha: odkazy na detail inzerátu přímo z HTML
    if not out:
        for a in soup.select('a[href*="/detail/"]'):
            href = a.get("href", "")
            m = re.search(r"/detail/(\d+)", href)
            if not m or m.group(1) in out:
                continue
            card = a
            for _ in range(4):           # vyšplhat na kartu inzerátu
                if card.parent and len(card.parent.get_text(" ", strip=True)) < 600:
                    card = card.parent
            text = card.get_text(" ", strip=True)
            img = card.select_one("img")
            out[m.group(1)] = {
                "key": f"sbazar:{m.group(1)}",
                "url": href if href.startswith("http") else "https://www.sbazar.cz" + href,
                "title": a.get_text(" ", strip=True) or text[:120],
                "desc": text[:400], "price": parse_price(text), "locality": text,
                "img": (img.get("src") or img.get("data-src")) if img else None, "posted": None,
            }

    items = []
    for v in out.values():
        v.update({"source": "sbazar", "akce": akce, "typ": typ, "psc": None})
        if v.get("img") and v["img"].startswith("//"):
            v["img"] = "https:" + v["img"]
        items.append(enrich(v))
    return items


def scrape_sbazar(known):
    found = []
    for (akce, typ), url in SBAZAR_URLS.items():
        if not url:
            continue
        try:
            items = sbazar_parse(get(url), akce, typ)
            log(f"[sbazar] {akce}/{typ}: {len(items)} inzerátů, nových {sum(i['key'] not in known for i in items)}")
            if not items:
                log("[sbazar] 0 inzerátů – nejspíš se změnila struktura stránky, pošli log Claudovi")
            found += items
        except Exception as e:
            log(f"[sbazar] {akce}/{typ}: CHYBA {e}")
    return found


# ------------------------------------------------------------------ ULOVDOMOV
UD_ENABLED = False      # UlovDomov vypnutý (skoro samé realitky); True = zapnout
UD_FIND = "https://ud.api.ulovdomov.cz/v1/offer/find"
UD_CONTACT = "https://www.ulovdomov.cz/fe-api/offer/contact-info/{}"
UD_PRAHA = {"northEast": {"lat": 50.178, "lng": 14.707}, "southWest": {"lat": 49.942, "lng": 14.224}}
UD_TYPES = {("pronajem", "byt"): ("rent", "flat"), ("prodej", "byt"): ("sale", "flat"),
            ("pronajem", "dum"): ("rent", "house"), ("prodej", "dum"): ("sale", "house")}
UD_PAGES = 3
UD_LAYOUT = {"onePlusKk": "1+kk", "onePlusOne": "1+1", "twoPlusKk": "2+kk", "twoPlusOne": "2+1",
             "threePlusKk": "3+kk", "threePlusOne": "3+1", "fourPlusKk": "4+kk", "fourPlusOne": "4+1",
             "fivePlusKk": "5+kk", "fivePlusOne": "5+1"}


def _ud_offers(obj):
    """Najde v odpovědi seznam inzerátů (slovníky s id a absoluteUrl/seo)."""
    if isinstance(obj, list) and obj and isinstance(obj[0], dict) and "id" in obj[0] \
            and ("absoluteUrl" in obj[0] or "seo" in obj[0]):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            r = _ud_offers(v)
            if r:
                return r
    if isinstance(obj, list):
        for v in obj:
            r = _ud_offers(v)
            if r:
                return r
    return []


def _ud_item(o, akce, typ):
    village = ((o.get("village") or {}).get("title") or "")
    part = ((o.get("villagePart") or {}).get("title") or "")
    street = ((o.get("street") or {}).get("title") or "")
    price = (o.get("rentalPrice") or o.get("price") or {})
    price = price.get("value") if isinstance(price, dict) else price
    url = o.get("absoluteUrl") or f"https://www.ulovdomov.cz/inzerat/{o.get('seo', '')}/{o['id']}"
    photo = (o.get("photos") or [{}])[0].get("path") if o.get("photos") else None
    item = {
        "key": f"ulovdomov:{o['id']}", "source": "ulovdomov", "ud_id": o["id"], "url": url,
        "title": o.get("title") or "", "desc": (o.get("description") or "")[:400],
        "price": int(price) if isinstance(price, (int, float)) and price > 999 else None,
        "price_text": o.get("priceNote") or "", "locality": ", ".join(x for x in (street, part, village) if x),
        "img": photo, "posted": (o.get("published") or "")[:10] or None, "akce": akce, "typ": typ,
        "no_commission": o.get("isNoCommission"), "psc": None,
        "layout": UD_LAYOUT.get(o.get("disposition")), "area": o.get("area"),
    }
    m = re.search(r"Praha[\s-]*(\d{1,2})", f"{part} {village}")
    if m:
        item["district"] = f"Praha {int(m.group(1))}"
    return enrich(item)


def scrape_ulovdomov(known):
    found = []
    for (akce, typ), (ot, pt) in UD_TYPES.items():
        for page in range(1, UD_PAGES + 1):
            try:
                if time.time() - START > MAX_RUNTIME:
                    raise OutOfTime("vyčerpán časový limit běhu")
                time.sleep(random.uniform(1.5, 3.0))
                r = S.post(UD_FIND, params={"page": page, "perPage": 20, "sorting": "latest"},
                           json={"bounds": UD_PRAHA, "offerType": ot, "propertyType": pt}, timeout=(10, 20))
                if page == 1 and akce == "pronajem" and typ == "byt":
                    Path("docs/probe").mkdir(parents=True, exist_ok=True)
                    Path("docs/probe/ud_find.json").write_text(r.text[:200000], "utf-8")
                r.raise_for_status()
                offers = _ud_offers(r.json())
            except Exception as e:
                log(f"[ulovdomov] {akce}/{typ} str.{page}: CHYBA {e}")
                break
            items = [_ud_item(o, akce, typ) for o in offers]
            items = [i for i in items if "praha" in i["locality"].lower()]
            found += items
            fresh = [i for i in items if i["key"] not in known]
            log(f"[ulovdomov] {akce}/{typ} str.{page}: {len(offers)} inzerátů, v Praze {len(items)}, nových {len(fresh)}")
            if not offers or not fresh:
                break
    return found


def ulovdomov_contact(item):
    """Kontakt u inzerátu: soukromý majitel / firma / převzatý inzerát z jiného webu."""
    try:
        time.sleep(random.uniform(1.0, 2.0))
        r = S.get(UD_CONTACT.format(item["ud_id"]), timeout=(10, 20), headers={"Accept": "application/json"})
        if not Path("docs/probe/ud_contact.json").exists():
            Path("docs/probe").mkdir(parents=True, exist_ok=True)
            Path("docs/probe/ud_contact.json").write_text(r.text[:20000], "utf-8")
        d = r.json()
        d = d.get("data", d) if isinstance(d, dict) else {}
    except Exception as e:
        log(f"[ulovdomov] kontakt {item['key']}: CHYBA {e}")
        return
    name = " ".join(x for x in (d.get("first_name"), d.get("last_name")) if x)
    item["seller"] = (f"{name} ({d['company_name']})" if name and d.get("company_name")
                      else d.get("company_name") or name or None)
    item["phone"] = d.get("phone") or None
    item["ud_private"] = d.get("is_private_landlord")
    item["ud_external"] = bool(d.get("external_url"))


# ------------------------------------------------------------------ TELEGRAM
def matches_notify(i):
    st = i.get("seller_type", "nejiste")
    if SELLER_FILTER == "rk_ven" and st == "rk": return False
    if SELLER_FILTER == "jen_soukromi" and st != "soukromy": return False
    if i.get("akce") == "pronajem" and i.get("typ") == "garaz": return False   # pronájem garáží Bob nechce
    f = NOTIFY
    if f["akce"] and i["akce"] != f["akce"]: return False
    if f["typ"] and i["typ"] != f["typ"]: return False
    if f["max_cena"] and (not i.get("price") or i["price"] > f["max_cena"]): return False
    if f["min_m2"] and (not i.get("area") or i["area"] < f["min_m2"]): return False
    if f["dispozice"] and i.get("layout") not in f["dispozice"]: return False
    return True


def _fmt(i):
    price = f"{i['price']:,} Kč".replace(",", " ") if i.get("price") else (i.get("price_text") or "cena neuvedena")
    if i.get("price") and i["akce"] == "pronajem":
        price += "/měs."
    meta = " | ".join(str(x) for x in [
        "Prodej" if i["akce"] == "prodej" else "Pronájem", TYP_LABEL.get(i.get("typ")),
        i.get("layout"), f"{i['area']} m²" if i.get("area") else None,
        f"{i['ppm2']:,} Kč/m²".replace(",", " ") if i.get("ppm2") else None,
        i.get("district"), {"bazos": "Bazoš", "ulovdomov": "UlovDomov"}.get(i["source"], i["source"]),
        "bez provize" if i.get("no_commission") else None] if x)
    return price, meta


def _send(tok, chat, text, preview=True, html=False):
    # TELEGRAM_CHAT_ID může obsahovat víc ID oddělených čárkou -> pošle se všem
    for cid in [c.strip() for c in str(chat).split(",") if c.strip()]:
        try:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                              json={"chat_id": cid, "text": text, "disable_web_page_preview": not preview,
                                    **({"parse_mode": "HTML"} if html else {})},
                              timeout=15)
            if not r.ok:
                log(f"[telegram] {cid}: {r.status_code} {r.text[:120]}")
        except Exception as e:
            log("[telegram]", e)
        time.sleep(1.1)            # limit Telegramu ~1 zpráva/s do jednoho chatu


def notify(new_items):
    tok, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return
    items = [x for x in new_items if matches_notify(x)]
    items.sort(key=lambda x: (x["akce"], x["typ"]))
    single, rest = items[:MAX_SINGLE], items[MAX_SINGLE:]
    for i in single:                       # každý inzerát zvlášť, s náhledem fotky
        price, meta = _fmt(i)
        who = {"soukromy": "👤 Soukromník", "nejiste": "❔ Nejisté (RK nepotvrzena)"}.get(i.get("seller_type"), "")
        if i.get("seller_reason") == "majitel nechce RK":
            who = "⚠️ Majitel v inzerátu píše, že nechce realitky"
        m2 = re.match(r"má (\d+) různé inzeráty", i.get("seller_reason") or "")
        if m2:
            who = f"❔ Možná RK – má na Bazoši {m2.group(1)} různé inzeráty"
        m = re.match(r"jen slovo v textu \((.*)\)", i.get("seller_reason") or "")
        if m:
            word = re.sub(r"[\\\[\]()|?*+.]|w\*", "", re.sub(r"\[(.)[^\]]*\]", r"\1", m.group(1))).split()[0]
            who = f"❔ Možná RK – v textu „{word}…“"
        if i.get("seller"):
            who += f" – {i['seller']}"
        from html import escape as E
        links = [f'<a href="{E(i["url"])}">Otevřít inzerát</a>']
        if i.get("phone"):
            digits = re.sub(r"\D", "", i["phone"])
            digits = "420" + digits if len(digits) == 9 else digits
            co = TYP_CO.get(i.get("typ"), "nemovitosti")
            msg = WA_TEXT.get(i.get("akce"), WA_TEXT["pronajem"]).format(co=co, co2=co)
            links.append(f'<a href="{E("https://wa.me/" + digits + "?text=" + requests.utils.quote(msg))}">📲 WhatsApp</a>')
        mail = EMAIL_RE.search(" ".join(str(i.get(k) or "") for k in ("desc_full", "desc", "title")))
        tel = f"📞 {E(i['phone'])}\n" if i.get("phone") else ""
        if mail:                           # Telegram mailto: odkazy nebere – e-mail jako text (klikací)
            co = TYP_CO.get(i.get("typ"), "nemovitosti")
            body = WA_TEXT.get(i.get("akce"), WA_TEXT["pronajem"]).format(co=co, co2=co)
            tel += f"✉️ {E(mail.group(0))}\n📝 Text e-mailu (klepnutím zkopírujete):\n<code>{E(body)}</code>\n"
        _send(tok, chat, f"🏠 <b>{E(i['title'])}</b>\n{E(price)}\n{E(meta)}\n{E(who)}\n{tel}" + " · ".join(links),
              preview=True, html=True)
    if rest:                               # zbytek v souhrnných zprávách
        lines = [f"📋 Dalších {len(rest)} nových inzerátů:"]
        for i in rest:
            price, meta = _fmt(i)
            lines.append(f"\n• {i['title'][:80]}\n  {price} | {meta}\n  {i['url']}")
        chunk = ""
        for ln in lines:
            if len(chunk) + len(ln) > 3800:
                _send(tok, chat, chunk, preview=False)
                chunk = ""
            chunk += ln + "\n"
        if chunk:
            _send(tok, chat, chunk, preview=False)
    skipped = sum(1 for x in new_items if x.get("seller_type") == "rk")
    log(f"[telegram] odesláno {len(items)} inzerátů, vyřazeno realitek: {skipped}")


def daily_rk_digest(store):
    """Jednou denně pošle přehled vyřazených inzerátů, ať vidíš, jestli filtr nevyhodil soukromníka."""
    tok, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (tok and chat) or NOW.hour != DIGEST_HOUR_UTC or NOW.minute >= 30:
        return
    since = (NOW - timedelta(hours=24)).isoformat()
    rk = [v for v in store.values() if v.get("seller_type") == "rk" and v["first_seen"] >= since]
    if not rk:
        return
    lines = [f"🚫 Vyřazeno filtrem za 24 h: {len(rk)}\nZkontroluj, jestli tu není soukromník:"]
    for v in rk:
        lines.append(f"\n• {v['title'][:70]}\n  {v.get('seller') or '?'} | důvod: {v.get('seller_reason','')}\n  {v['url']}")
    chunk = ""
    for ln in lines:
        if len(chunk) + len(ln) > 3800:
            _send(tok, chat, chunk, preview=False); chunk = ""
        chunk += ln + "\n"
    if chunk:
        _send(tok, chat, chunk, preview=False)


def motivated_digest(store, data):
    """Jednou týdně (pondělí ráno) přehled soukromníků, kterým se nedaří prodat/pronajmout.
    Jen ověření soukromníci (seller_type == "soukromy", známé jméno, < 3 různé inzeráty) – žádné realitky ani ❔."""
    tok, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    week = NOW.strftime("%G-W%V")
    if not (tok and chat) or NOW.weekday() != MOTIVATED_WEEKDAY or NOW.hour < MOTIVATED_HOUR_UTC \
            or data.get("motivated_week") == week:
        return
    titles, groups = {}, {}
    for v in store.values():
        sid = v.get("seller_id") or (v.get("seller") or "").lower()
        name = re.sub(r"\s+", " ", (v.get("seller") or "")).strip().lower()
        if name:
            titles.setdefault("n:" + name, set()).add(_norm_title(v.get("title")))
        if sid:
            titles.setdefault(sid, set()).add(_norm_title(v.get("title")))
            groups.setdefault((sid, _norm_title(v.get("title")), v.get("akce")), []).append(v)
    cands = []
    for v in store.values():
        sid = v.get("seller_id") or (v.get("seller") or "").lower()
        if v.get("source") != "bazos" or v.get("seller_type") != "soukromy" or not v.get("seller") or v.get("dup_of"):
            continue
        name = "n:" + re.sub(r"\s+", " ", v["seller"]).strip().lower()
        if max(len(titles.get(sid, ())), len(titles.get(name, ()))) >= RK_MIN_LISTINGS \
                or (NOW - datetime.fromisoformat(v["last_seen"])).days > 2 \
                or (v.get("akce") == "prodej" and re.match(r"\s*pronaj|\s*pronáj", v["title"], re.I)):
            continue                                   # možná RK / už neviditelné
        if classify(v, {k: len(t) for k, t in titles.items()})[0] != "soukromy":
            continue                                   # podle dnešních pravidel (seznam RK, ID…) není jistý soukromník
        why, score = [], 0.0
        if v.get("price_prev") and v.get("price") and v["price"] < v["price_prev"]:
            pct = 100 * (v["price_prev"] - v["price"]) / v["price_prev"]
            why.append(f"cena −{pct:.0f} %"); score += 2 + pct / 3
        group = groups.get((sid, _norm_title(v.get("title")), v.get("akce")), [])
        first = min(x["first_seen"] for x in group) if group else v["first_seen"]
        days = (NOW - datetime.fromisoformat(first)).days
        if not v.get("first_load") and days >= 14:
            why.append(f"{days} dní v inzerci"); score += days / 7
        if len(group) >= 2:
            why.append(f"{len(group)}× obnoven"); score += len(group)
        if why:
            cands.append((score, why, v))
    data["motivated_week"] = week
    if not cands:
        log("[motivovaní] nic k poslání"); return
    from html import escape as E
    lines = ["🎯 <b>Majitelé, kterým se nedaří</b> (jen soukromníci, týdenní přehled)"]
    for akce, label, n in (("prodej", "PRODEJ", 7), ("pronajem", "PRONÁJEM", 5)):
        top = sorted([c for c in cands if c[2].get("akce") == akce], key=lambda c: -c[0])[:n]
        if not top:
            continue
        lines.append(f"\n<b>{label}</b>")
        for _, why, v in top:
            price, meta = _fmt(v)
            lines.append(f"• <a href=\"{E(v['url'])}\">{E(v['title'][:60])}</a>\n  {E(price)} | {E(v.get('district') or '')} | "
                         f"👤 {E(v.get('seller') or '')}\n  ⚠️ {E(', '.join(why))}")
    _send(tok, chat, "\n".join(lines), preview=False, html=True)
    log(f"[motivovaní] odesláno, kandidátů {len(cands)}")


MARKET_FILE = Path("docs/market.json")   # denní souhrn trhu – drží se natrvalo (malý), z něj měsíční „Tržní radar“
MARKET_REPORT_HOUR_UTC = 7               # 1. den v měsíci od 8:00 (zima)
LAYOUTS = ["1+kk", "1+1", "2+kk", "2+1", "3+kk", "3+1"]


def _area(v):
    d = v.get("district") or ""
    return d if d.startswith("Praha ") else ("Praha" if d == "Praha" else d or "?")


def market_update(store):
    """Doplní do market.json každý celý den (z data.json, které drží 30 dní) – nabídkové ceny Kč/m² nových inzerátů.
    Klíč „akce|typ|dispozice|oblast“ → [Kč/m² …, počet, z toho soukromníci]. Přepočítatelné, takže nevadí ztracený zápis."""
    mk = json.loads(MARKET_FILE.read_text("utf-8")) if MARKET_FILE.exists() else {"days": {}}
    today = NOW.date().isoformat()
    days = {}
    for v in store.values():
        if v.get("source") != "bazos" or v.get("first_load") or v.get("dup_of") or v.get("typ") not in ("byt", "dum"):
            continue
        day = v["first_seen"][:10]
        if day >= today or day in mk["days"]:
            continue
        lay = v.get("layout") if v.get("typ") == "byt" and v.get("layout") in LAYOUTS else ("4+" if v.get("typ") == "byt" and v.get("layout") else "-")
        key = f"{v['akce']}|{v['typ']}|{lay}|{_area(v)}"
        rec = days.setdefault(day, {}).setdefault(key, [[], 0, 0])
        rec[1] += 1
        rec[2] += v.get("seller_type") != "rk"
        ppm2 = v.get("ppm2")
        lo, hi = (20_000, 450_000) if v["akce"] == "prodej" else (80, 1_500)
        if ppm2 and lo <= ppm2 <= hi:
            rec[0].append(ppm2)
    oldest = min((v["first_seen"][:10] for v in store.values()), default=today)
    for day, recs in days.items():
        if day > oldest:                       # první (neúplný) den dat vynechat
            mk["days"][day] = recs
    if days:
        MARKET_FILE.write_text(json.dumps(mk, ensure_ascii=False, separators=(",", ":")), "utf-8")
    return mk


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def market_report(mk, data):
    """1. den v měsíci: „📊 Tržní radar“ za minulý měsíc vs. předminulý (nabídkové ceny z Bazoše)."""
    tok, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    first = NOW.date().replace(day=1)
    prev_end = first - timedelta(days=1)
    month = prev_end.strftime("%Y-%m")
    if not (tok and chat) or NOW.day != 1 or NOW.hour < MARKET_REPORT_HOUR_UTC or data.get("market_month") == month:
        return
    prev2 = (prev_end.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

    def agg(m, akce, typ, layout=None, area=None):
        ps, n, npriv = [], 0, 0
        for day, recs in mk["days"].items():
            if not day.startswith(m):
                continue
            for key, (p, c, cp) in recs.items():
                a, t, lay, ar = key.split("|")
                if a != akce or t != typ or (layout and lay != layout):
                    continue
                if area == "PRAHA" and not re.fullmatch(r"Praha( \d+)?", ar):
                    continue
                if area and area != "PRAHA" and ar != area:
                    continue
                ps += p; n += c; npriv += cp
        return _median(ps), n, npriv

    days_in = sum(1 for d in mk["days"] if d.startswith(month))
    if days_in < 14:
        log(f"[radar] za {month} jen {days_in} dní dat – neposílám"); data["market_month"] = month; return

    def fmt(akce, med, med0):
        if not med:
            return "–"
        val = f"{med / 1000:.0f} tis. Kč/m²" if akce == "prodej" else f"{med:.0f} Kč/m²/měs."
        if med0:
            ch = 100 * (med - med0) / med0
            val += f" ({'↑' if ch > 0 else '↓' if ch < 0 else '→'}{abs(ch):.0f} %)"
        return val

    from html import escape as E
    names = ["leden", "únor", "březen", "duben", "květen", "červen", "červenec", "srpen", "září", "říjen", "listopad", "prosinec"]
    out = [f"📊 <b>Tržní radar – {names[prev_end.month - 1]} {prev_end.year}</b>",
           "<i>nabídkové ceny nových inzerátů na Bazoši, v závorce změna proti minulému měsíci</i>"]
    for akce, label in (("prodej", "PRODEJ BYTŮ"), ("pronajem", "PRONÁJEM BYTŮ")):
        med, n, npriv = agg(month, akce, "byt", area="PRAHA")
        med0, _, _ = agg(prev2, akce, "byt", area="PRAHA")
        out.append(f"\n<b>{label} – Praha</b>: {fmt(akce, med, med0)}, nových {n} (soukromníci {npriv})")
        rows = []
        for lay in LAYOUTS:
            m1, c1, _ = agg(month, akce, "byt", lay, "PRAHA")
            if c1 >= 5:
                rows.append(f"{lay} {fmt(akce, m1, agg(prev2, akce, 'byt', lay, 'PRAHA')[0])}")
        if rows:
            out.append("  " + " · ".join(rows))
        areas = {}
        for day, recs in mk["days"].items():
            if day.startswith(month):
                for key, (p, c, cp) in recs.items():
                    a, t, lay, ar = key.split("|")
                    if a == akce and t == "byt":
                        areas[ar] = areas.get(ar, 0) + c
        top = [a for a, c in sorted(areas.items(), key=lambda x: -x[1]) if c >= 5][:12]
        for ar in sorted(top, key=lambda a: (not re.fullmatch(r"Praha( \d+)?", a), int((re.findall(r"\d+", a) or [99])[0]), a)):
            m1, c1, cp1 = agg(month, akce, "byt", area=ar)
            out.append(f"• {E(ar)}: {fmt(akce, m1, agg(prev2, akce, 'byt', area=ar)[0])}, nových {c1}")
    _, c1, cp1 = agg(month, "prodej", "dum")
    _, c0, _ = agg(prev2, "prodej", "dum")
    out.append(f"\n<b>PRODEJ DOMŮ</b> (Praha + okolí): nových {c1} (soukromníci {cp1})" + (f", minulý měsíc {c0}" if c0 else ""))
    _send(tok, chat, "\n".join(out)[:4000], preview=False, html=True)
    data["market_month"] = month
    log(f"[radar] odeslán za {month}")


# ------------------------------------------------------------------ MAIN

# ------------------------------------------------------------------ PRŮZKUM NOVÝCH ZDROJŮ (dočasné)
PROBE_URLS = {}


def probe():
    """Stáhne JavaScript UlovDomova, ve kterém je adresa jejich datového rozhraní."""
    out = Path("docs/probe")
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*"):
        f.unlink()
    try:
        h = S.get("https://www.ulovdomov.cz/pronajem/bytu/praha", timeout=(10, 20)).text
        js = re.findall(r'/_next/static/chunks/[^"]+\.js', h)
        for i, path in enumerate(js):
            r = S.get("https://www.ulovdomov.cz" + path, timeout=(10, 20))
            (out / f"ud_js_{i}.txt").write_text(path + "\n" + r.text, "utf-8")
            time.sleep(1)
        log(f"[probe] ulovdomov js: {len(js)} souborů")
    except Exception as e:
        log(f"[probe] CHYBA {e}")


def find_repost(i, store):
    """Stejný prodávající znovu vložil stejný inzerát (nové ID na Bazoši) – už jsme ho posílali."""
    sid = i.get("seller_id") or (i.get("seller") or "").lower()
    if not sid:
        return None
    t = _norm_title(i.get("title"))
    for k, v in store.items():
        if k != i["key"] and (v.get("seller_id") or (v.get("seller") or "").lower()) == sid \
                and _norm_title(v.get("title")) == t and v.get("akce") == i.get("akce"):
            return k
    return None


def find_duplicate(i, store):
    """Stejný byt z jiného webu: shoda typu, dispozice, plochy ±1 m², ceny ±2 % a čtvrti (nebo telefonu)."""
    for k, v in store.items():
        if k == i["key"] or v.get("source") == i["source"] or v.get("akce") != i["akce"] or v.get("typ") != i["typ"]:
            continue
        if i.get("phone") and v.get("phone") and _norm_phone(i["phone"]) == _norm_phone(v["phone"]) \
                and i.get("layout") == v.get("layout"):
            return k
        if not (i.get("price") and v.get("price")) or abs(i["price"] - v["price"]) > 0.02 * v["price"]:
            continue
        if not (i.get("area") and v.get("area")) or abs(i["area"] - v["area"]) > 1:
            continue
        if i.get("layout") and v.get("layout") and i["layout"] != v["layout"]:
            continue
        if i.get("district") and v.get("district") and i["district"] != v["district"]:
            continue
        return k
    return None


def main():
    data = {"listings": {}}
    if DATA_FILE.exists():
        data = json.loads(DATA_FILE.read_text("utf-8"))
    store = data["listings"]
    first_run = not store
    known = set(store)

    found = scrape_bazos(known) + scrape_sbazar(known)
    ud_first = not any(v["source"] == "ulovdomov" for v in store.values())
    if UD_ENABLED:
        try:
            found += scrape_ulovdomov(known)
        except Exception as e:
            log("[ulovdomov] CHYBA", e)

    # paměť realitek: jména (aspoň 2 slova) a telefony, které už byly jednou vyřazené jako RK
    for v in store.values():
        # pamatovat si jen jasné RK – ne jednorázový nález slova v textu (mohl to být soukromník)
        if v.get("seller_type") == "rk" and (v.get("seller_reason") or "").startswith(STRONG_RK_REASONS):
            if v.get("seller") and len(v["seller"].split()) >= 2:
                RK_KNOWN.add(re.sub(r"\s+", " ", v["seller"]).strip().lower())
            if v.get("phone"):
                RK_PHONES.add(_norm_phone(v["phone"]))

    # detail (jméno prodávajícího, celý popis) u nových inzerátů z Bazoše
    # + u starších, kde se detail minule nestihl načíst ("pending")
    # nová lokalita: při prvním načtení jen uložit (bez detailů a bez posílání)
    new_scheme = data.get("scheme") != SCHEME
    for i in found:
        if new_scheme and i["source"] == "bazos" and i["key"] not in known:
            i["first_load"] = True
    data["scheme"] = SCHEME
    pending = [dict(v) for v in store.values() if v.get("pending")]
    seen_keys = set()
    todo = []
    for i in pending + [i for i in found if i["key"] not in known and i["source"] in ("bazos", "ulovdomov")
                        and not i.get("first_load")]:
        if i["key"] not in seen_keys:
            seen_keys.add(i["key"]); todo.append(i)
    if not first_run:
        for i in todo[:MAX_DETAILS]:
            if i["source"] == "bazos":
                bazos_detail(i)
            elif not (i["source"] == "ulovdomov" and ud_first and i["key"] not in store):
                ulovdomov_contact(i)
        log(f"[bazos] načteno detailů: {min(len(todo), MAX_DETAILS)}, čeká na příští běh: {max(0, len(todo) - MAX_DETAILS)}")
    # kolik RŮZNÝCH inzerátů má prodávající (stejný inzerát vložený znovu = 1)
    _seen_titles = {}
    for v in list(store.values()) + [i for i in found if i["key"] not in store]:
        t = _norm_title(v.get("title"))
        sid = v.get("seller_id") or (v.get("seller") or "").lower()
        if sid:
            _seen_titles.setdefault(sid, set()).add(t)
        if v.get("phone"):
            _seen_titles.setdefault("tel:" + _norm_phone(v["phone"]), set()).add(t)
    seller_counts = {k: len(ts) for k, ts in _seen_titles.items()}

    pool = dict(store)                           # pro hledání duplicit i v rámci jednoho běhu

    def settle(i):
        """Zařadí inzerát; bez jména prodávajícího ho nechá čekat (max. 3 běhy)."""
        no_detail = (i["source"] == "bazos" and not i.get("seller")) or \
                    (i["source"] == "ulovdomov" and i.get("ud_private") is None and not i.get("ud_external"))
        if i.get("first_load"):
            i["seller_type"], i["seller_reason"], i["silent"], i["pending"] = "nejiste", "první načtení (nové hledání)", True, False
            return
        if i["source"] == "ulovdomov" and ud_first and i["key"] not in store:
            i["seller_type"], i["seller_reason"], i["silent"], i["pending"] = "nejiste", "první načtení zdroje", True, False
            return
        if no_detail and not first_run:
            i["tries"] = i.get("tries", 0) + 1
            if i["tries"] < 3:
                i["pending"] = True
                return
            i["seller_type"], i["seller_reason"], i["silent"] = "nejiste", "nepodařilo se načíst prodávajícího", True
        else:
            i["seller_type"], i["seller_reason"] = classify(i, seller_counts)
        i["pending"] = False
        i.pop("desc_full", None)
        dup = find_duplicate(i, pool) or find_repost(i, pool)
        if dup:
            i["dup_of"], i["silent"] = dup, True
        pool[i["key"]] = i

    ready = []                                   # dřívější "pending", které se teď dořešily
    by_key = {i["key"]: i for i in todo}
    for v in pending:
        i = by_key.get(v["key"], v)
        settle(i)
        store[i["key"]] = {**store[i["key"]], **i}
        if not i.get("pending"):
            ready.append(store[i["key"]])
    for i in found:
        if i["key"] not in known:
            i.update(by_key.get(i["key"], {}))
            settle(i)
    now_iso = NOW.isoformat(timespec="seconds")
    new = []
    for i in found:
        if i["key"] in store:
            old = store[i["key"]]
            if i.get("price") and old.get("price") and i["price"] != old["price"]:
                i["price_prev"] = old["price"]
            i["first_seen"] = old["first_seen"]
            i["last_seen"] = now_iso
            store[i["key"]] = {**old, **{k: v for k, v in i.items() if v is not None}}
        else:
            i["first_seen"] = i["last_seen"] = now_iso
            store[i["key"]] = i
            new.append(i)

    cutoff = (NOW - timedelta(days=KEEP_DAYS)).isoformat()
    for k in [k for k, v in store.items() if v["last_seen"] < cutoff]:
        del store[k]

    if not first_run:
        try:
            motivated_digest(store, data)
        except Exception as e:
            log("[motivovaní] CHYBA", e)
        try:
            market_report(market_update(store), data)
        except Exception as e:
            log("[radar] CHYBA", e)
    data["updated"] = now_iso
    DATA_FILE.parent.mkdir(exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), "utf-8")
    log(f"Hotovo: nových {len(new)}, celkem {len(store)}")

    if not first_run:          # při prvním běhu nespamovat
        notify([i for i in new + ready if not i.get("pending") and not i.get("silent")])
    if not found:
        sys.exit("Nenačten žádný inzerát – zkontroluj log (blokace nebo změna webu).")


def merge(ours_path):
    """Sloučí naše data s daty, která mezitím uložil jiný běh (nic se neztratí ani nepošle dvakrát)."""
    ours = json.loads(Path(ours_path).read_text("utf-8"))
    theirs = json.loads(DATA_FILE.read_text("utf-8")) if DATA_FILE.exists() else {"listings": {}}
    L = theirs.setdefault("listings", {})
    for k, v in ours.get("listings", {}).items():
        if k not in L:
            L[k] = v
        else:
            a, b = L[k], v
            newer = b if b.get("last_seen", "") > a.get("last_seen", "") else a
            newer["first_seen"] = min(a.get("first_seen", ""), b.get("first_seen", "")) or newer.get("first_seen")
            L[k] = newer
    theirs["scheme"] = ours.get("scheme") or theirs.get("scheme")
    theirs["market_month"] = max(ours.get("market_month") or "", theirs.get("market_month") or "") or None
    theirs["motivated_week"] = max(ours.get("motivated_week") or "", theirs.get("motivated_week") or "") or None
    theirs["updated"] = max(theirs.get("updated", ""), ours.get("updated", ""))
    DATA_FILE.write_text(json.dumps(theirs, ensure_ascii=False, separators=(",", ":")), "utf-8")
    log(f"[merge] sloučeno, celkem {len(L)}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--merge":
        merge(sys.argv[2])
    else:
        main()
