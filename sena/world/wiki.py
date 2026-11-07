"""Wikipedia: a quick reference on a person, company, place, or concept. The official Wikimedia API, no key needed, in any language.
First the article title is searched, then its summary is fetched. If nothing is found in the requested language, it tries English."""
import math
import re
from datetime import date
from urllib.parse import quote

from sena.core.safety import external
from sena.core.tools import clamp, string, tool
from sena.world import net

SEARCH = "https://{}.wikipedia.org/w/api.php"
SUMMARY = "https://{}.wikipedia.org/api/rest_v1/page/summary/{}"
THIS_DAY = "https://api.wikimedia.org/feed/v1/wikipedia/{}/onthisday/{}/{:02d}/{:02d}"
DAY_KINDS = ("selected", "events", "births", "deaths", "holidays")
HEADERS = {"User-Agent": "SenaAssistant/1.0 (personal assistant; https://github.com/AStorozh/Sena)", "Accept": "application/json"}   # Wikimedia asks clients to identify themselves
CACHE_SECONDS = 3600
EXTRACT = 1500   # characters of the summary: enough for a reference without bloating the response
LANG = re.compile(r"[a-z]{2,3}")   # the language ends up in the site's address: nothing but a language code is let through


class WikiError(net.WorldError):
    pass


def _get(url, params=None):
    return net.as_json(net.get(url, params, HEADERS, ttl=CACHE_SECONDS, who="Википедия", error=WikiError), "Википедия", WikiError)


def _found(lang, query):
    """Article titles matching the query, best match first."""
    data = _get(SEARCH.format(lang), {"action": "query", "list": "search", "srsearch": query, "srlimit": 5, "format": "json", "formatversion": 2})
    return [row["title"] for row in data.get("query", {}).get("search", [])]


def _fits(query, title, extract):
    """Wikipedia's search always returns the "closest" match: for a person with no article it'll bring back someone else's. An article is considered a fit if it contains at least half the query's words."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) >= 3]
    text = f"{title} {extract}".lower()
    return not words or sum(w[:5] in text for w in words) >= math.ceil(len(words) / 2)


def wiki(query, lang="ru"):
    """Wikipedia reference: who or what this is, what it's known for. lang: ru, en, id and other language codes."""
    query, lang = str(query or "").strip(), str(lang or "ru").strip().lower()
    if not query:
        raise WikiError("Что искать в Википедии?")
    if not LANG.fullmatch(lang):
        raise WikiError("Язык пишется кодом: ru, en, id, de")
    near = []
    for code in dict.fromkeys([lang, "en"]):   # the requested language first, then English
        titles = _found(code, query)
        if not titles:
            continue
        page = _get(SUMMARY.format(code, quote(titles[0].replace(" ", "_"), safe="")))
        extract = (page.get("extract") or "").strip()
        if not _fits(query, page.get("title", titles[0]), extract):
            near += titles[:3]
            continue
        others = [t for t in titles[1:4] if t != page.get("title")]
        if page.get("type") == "disambiguation" or not extract:   # a disambiguation page: show the options
            return external(f"«{query}» в Википедии ({code}) может значить: {', '.join(titles[:5])}. Уточни запрос.", "Википедия")
        link = (page.get("content_urls") or {}).get("desktop", {}).get("page", "")
        text = f"{page.get('title', titles[0])}" + (f" — {page['description']}" if page.get("description") else "") + f"\n{extract[:EXTRACT]}" + ("…" if len(extract) > EXTRACT else "")
        return external(text + (f"\n{link}" if link else "") + (f"\nПохожие статьи: {', '.join(others)}" if others else ""), "Википедия")
    if near:
        return external(f"Статьи точно про «{query}» в Википедии нет. Ближайшие по словам, но это не то же самое: {', '.join(dict.fromkeys(near))}.", "Википедия")
    return f"В Википедии ничего не нашлось по «{query}»."


def _day(value):
    """A day from "19.10", "10-19" or "2026-10-19"; empty — today. Returns (month, day)."""
    value = str(value or "").strip()
    if not value:
        today = date.today()
        return today.month, today.day
    match, dotted = re.fullmatch(r"(?:\d{4}-)?(\d{1,2})-(\d{1,2})", value), re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.\d{4})?", value)
    month, day = (int(match.group(1)), int(match.group(2))) if match else (int(dotted.group(2)), int(dotted.group(1))) if dotted else (0, 0)
    try:
        date(2024, month, day)   # a leap year: February 29 does occur
    except ValueError:
        raise WikiError("Дата пишется как 19.10 (день и месяц) или 10-19")
    return month, day


def on_this_day(day="", kind="selected", lang="ru", limit=8):
    """What happened on this day: highlights (selected), events, births, deaths or holidays. day — 19.10 or 10-19, empty — today."""
    month, number = _day(day)
    kind, lang = kind if kind in DAY_KINDS else "selected", str(lang or "ru").strip().lower()
    if not LANG.fullmatch(lang):
        raise WikiError("Язык пишется кодом: ru, en, de")
    limit = clamp(limit, 8, 1, 15)
    items = _get(THIS_DAY.format(lang, kind, month, number)).get(kind) or []
    if not items:
        return f"На {number:02d}.{month:02d} в Википедии ({lang}) ничего не нашлось."
    items = sorted((i for i in items if i.get("text")), key=lambda i: i.get("year") or 0)
    step = max(1, len(items) // limit)   # there are many events: sample evenly across all of history, not just the oldest ones
    chosen = items[::step][:limit]
    return external(f"{number:02d}.{month:02d} в истории ({kind}):\n" + "\n".join(f"{i.get('year', '')}: {i['text']}" for i in chosen), "Википедия")


TOOLS = [
    tool("wiki", "Справка из Википедии: кто такой человек, что за компания, место, понятие, термин. Бесплатно, быстро. Язык по умолчанию русский, "
                 "если статьи нет, смотрит английскую. Свежие события и мелочи про малоизвестных людей в Википедии могут отсутствовать.",
         {"query": string("Что или кого искать"), "lang": string("Код языка: ru, en, id, de; по умолчанию ru")}, ["query"]),
    tool("on_this_day", "Что было в этот день в истории: главные события, рождения, смерти, праздники (Википедия). day — 19.10 или 10-19, пусто — сегодня.",
         {"day": string("День и месяц: 19.10; пусто — сегодня"), "kind": {"type": "string", "enum": list(DAY_KINDS), "description": "selected (главное, по умолчанию), events, births, deaths, holidays"},
          "lang": string("Код языка, по умолчанию ru"), "limit": {"type": "integer", "description": "Сколько записей, от 1 до 15, по умолчанию 8"}}),
]
FUNCTIONS = {"wiki": wiki, "on_this_day": on_this_day}
