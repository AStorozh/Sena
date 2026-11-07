"""Countries: a reference (capital, currency, languages, time zones, phone code, which side traffic drives on) from Wikidata, and economy (GDP, inflation, unemployment,
population, exchange rate) from the World Bank's open data. Both sources need no key. The country can be named in any language or by its two-letter code (ID, VN)."""
import re
from concurrent.futures import ThreadPoolExecutor

from sena.core.safety import external
from sena.core.tools import string, tool
from sena.world import finance, net

WIKIDATA = "https://www.wikidata.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"
BANK = "https://api.worldbank.org/v2/country/{}/indicator/{}"
HEADERS = {"User-Agent": "Sena/1.0 (personal assistant; https://github.com/AStorozh/Sena)", "Accept": "application/json"}
CACHE_SECONDS = 86400   # country reference data hardly ever changes
ISO2 = re.compile(r"[A-Za-z]{2}")
INDICATORS = [("NY.GDP.PCAP.CD", "ВВП на человека", "$"), ("NY.GDP.MKTP.CD", "ВВП", "$"), ("NY.GDP.MKTP.KD.ZG", "рост ВВП", "%"), ("FP.CPI.TOTL.ZG", "инфляция", "%"),
              ("SL.UEM.TOTL.ZS", "безработица", "%"), ("SP.POP.TOTL", "население", ""), ("PA.NUS.FCRF", "курс к доллару (местных денег за 1 $)", "")]
QUERY = """SELECT ?cLabel ?en ?iso ?capLabel ?curLabel ?curCode ?langLabel ?tzLabel ?code ?driveLabel WHERE {{
  {anchor}
  ?c wdt:P297 ?iso.
  OPTIONAL {{ ?c rdfs:label ?en FILTER(LANG(?en) = "en") }}
  OPTIONAL {{ ?c wdt:P36 ?cap }}
  OPTIONAL {{ ?c wdt:P38 ?cur. OPTIONAL {{ ?cur wdt:P498 ?curCode }} }}
  OPTIONAL {{ ?c wdt:P37 ?lang }}
  OPTIONAL {{ ?c wdt:P421 ?tz }}
  OPTIONAL {{ ?c wdt:P474 ?code }}
  OPTIONAL {{ ?c wdt:P1622 ?drive }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "ru,en". }}
}}"""


class CountryError(net.WorldError):
    pass


def _json(url, params=None, who="Викиданные"):
    return net.as_json(net.get(url, params, HEADERS, ttl=CACHE_SECONDS, who=who, error=CountryError), who, CountryError)


def _country(name):
    """Country data from Wikidata: a dict with the iso code, name, capital, currency, languages, zones, phone code."""
    name = str(name or "").strip()
    if not name:
        raise CountryError("Какая страна?")
    if ISO2.fullmatch(name):
        anchor = f'?c wdt:P297 "{name.upper()}".'
    else:
        found = _json(WIKIDATA, {"action": "wbsearchentities", "search": name, "language": "ru", "uselang": "ru", "type": "item", "limit": 8, "format": "json"}).get("search") or []
        if not found:
            raise CountryError(f"Страну «{name}» не нашла. Назови точнее или кодом: ID, VN, TH.")
        anchor = "VALUES ?c { " + " ".join(f"wd:{x['id']}" for x in found if re.fullmatch(r"Q\d+", x["id"])) + " }"   # of the search results, only the ones with a country code survive
    rows = _json(SPARQL, {"query": QUERY.format(anchor=anchor), "format": "json"})["results"]["bindings"]
    if not rows:
        raise CountryError(f"«{name}» не похоже на страну (у неё нет кода страны ISO). Назови страну.")
    first = rows[0]["iso"]["value"]
    rows = [r for r in rows if r["iso"]["value"] == first]   # if several countries matched, take the first one found
    collect = lambda key: sorted({r[key]["value"] for r in rows if key in r})
    zones = [z for z in collect("tzLabel") if re.fullmatch(r"[A-Za-z_]+/[A-Za-z_/\-]+|UTC[+−-]?[\d:]*", z)]
    currency = [f"{r['curLabel']['value']} ({r['curCode']['value']})" if "curCode" in r else r["curLabel"]["value"] for r in rows if "curLabel" in r]
    return {"iso": first, "name": rows[0]["cLabel"]["value"], "en": next((r["en"]["value"] for r in rows if "en" in r), rows[0]["cLabel"]["value"]), "capital": collect("capLabel"), "currency": list(dict.fromkeys(currency)), "languages": collect("langLabel"),
            "zones": zones or collect("tzLabel")[:3], "code": collect("code"), "drive": collect("driveLabel")}


def country_info(country):
    """Country reference: capital, currency, languages, time zones, phone code, which side traffic drives on."""
    c = _country(country)
    lines = [f"{c['name']} ({c['iso']})"]
    for label, key in (("Столица", "capital"), ("Валюта", "currency"), ("Языки", "languages"), ("Телефонный код", "code"), ("Движение", "drive")):
        if c[key]:
            lines.append(f"{label}: {', '.join(c[key][:6])}")
    if c["zones"]:
        lines.append("Часовые пояса: " + ", ".join(c["zones"][:8]))
    return external("\n".join(lines), "Викиданные")


def _latest(iso, code):
    """The two most recent known values of the indicator: [(year, value), ...], most recent first."""
    data = _json(BANK.format(iso, code), {"format": "json", "mrv": 6}, "Всемирный банк")
    rows = data[1] if isinstance(data, list) and len(data) > 1 and data[1] else []
    return [(r["date"], r["value"]) for r in rows if r.get("value") is not None][:2]


def country_economy(country):
    """Country economy from World Bank data: GDP, growth, inflation, unemployment, population, exchange rate. Each indicator has its own year (the latest published)."""
    c = _country(country)
    with ThreadPoolExecutor(max_workers=len(INDICATORS)) as pool:
        results = list(pool.map(lambda i: _safe(c["iso"], i[0]), INDICATORS))
    errors = [r for r in results if isinstance(r, CountryError)]
    if len(errors) == len(results):   # the bank is down: that is not "no data" for every indicator
        raise CountryError(f"Всемирный банк сейчас не отвечает ({errors[0]}). Попробуй позже.")
    lines = []
    for (code, label, unit), values in zip(INDICATORS, results):
        if not values or isinstance(values, CountryError):
            lines.append(f"{label}: " + ("не прочиталось" if isinstance(values, CountryError) else "нет данных"))
            continue
        (year, now), before = values[0], values[1] if len(values) > 1 else None
        shown = f"{finance.big(now)} $" if code == "NY.GDP.MKTP.CD" else finance.big(now) if code == "SP.POP.TOTL" else f"{finance.fmt(now, 1)}{'%' if unit == '%' else ' ' + unit if unit else ''}"
        lines.append(f"{label}: {shown} ({year})" + (f", годом раньше {finance.fmt(before[1], 1)}" if before and unit == "%" else ""))
    return external(f"{c['name']}, данные Всемирного банка (последний опубликованный год по каждому показателю):\n" + "\n".join(lines), "Всемирный банк")


def _safe(iso, code):
    # the indicator's values, or its error: an indicator that didn't load is told apart from one with no data
    try:
        return _latest(iso, code)
    except CountryError as e:
        return e


TOOLS = [
    tool("country_info", "Справка о стране: столица, валюта, языки, часовые пояса, телефонный код, с какой стороны движение. Страна на любом языке или кодом (ID, VN, TH). Для поездок и звонков.",
         {"country": string("Страна: Индонезия, Vietnam, TH")}, ["country"]),
    tool("country_economy", "Экономика страны (Всемирный банк): ВВП, ВВП на человека, рост, инфляция, безработица, население, курс местной валюты к доллару. Данные годовые, свежие за прошлый год.",
         {"country": string("Страна: Индонезия, Vietnam, TH")}, ["country"]),
]
FUNCTIONS = {"country_info": country_info, "country_economy": country_economy}
