"""News from a long checked list of outlets (news_sources.json next to this file, about 160 open feeds): world and politics,
in Russian, tech, finance, sport, culture, and local news of the owner's place (that country's outlets plus Google News for the city).
Code prepares, Sena decides: no extra model stands between the feeds and her.
- only outlets with an editorial standard are on the list; each has a tier (1 — news agencies, public broadcasters, papers of record;
  2 — solid national and specialist press; 3 — local and smaller outlets) and an owner: outlets of one owner count as one voice;
  state media of countries without a free press (kind "state") give the official position and never make a story strong alone;
  fact checkers (kind "factcheck") add no weight, they flag a story they have checked; an organisation's own channel (kind
  "primary": central banks, the UN, OpenAI) counts like an outlet of its tier for now;
- the same story across outlets and languages is merged by meaning (the home fingerprint model; without it — by shared words);
- a story ranks by how many independent owners carry it and how reliable they are, then by feed position and freshness;
- the strongest stories come with the opening of their article from the most reliable open outlet; a doubtful one (a single
  outlet, only state media, words of anonymous sourcing) comes with a longer passage and is marked, and Sena — the brain, who
  reads all this anyway to retell it — keeps what matters, drops the rest and checks the doubtful before telling;
- a background watch (watch(), from the scheduler) gathers every feed ahead, so a digest takes seconds, and writes first when
  a strong new story on a topic the owner watches appears.
The headlines and passages are someone else's text: the answer is marked as external data."""
import html
import json
import math
import re
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import numpy as np
from lxml import etree

from sena import config
from sena.core import events, local, settings, store
from sena.core.safety import external
from sena.core.tools import Failure, clamp, string, tool
from sena.world import articles, net

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SenaNews/1.0)"}
CACHE_SECONDS = 600
SOURCES = json.loads(Path(__file__).with_name("news_sources.json").read_text(encoding="utf-8"))
TOPICS_RU = {"world": "мир и политика", "russia": "по-русски", "tech": "ИТ", "finance": "финансы и крипта", "sport": "спорт",
             "culture": "культура", "local": "местные", "all": "все темы"}
WEIGHT = {1: 3, 2: 2, 3: 1}   # what one owner adds to a story, by the best tier of its outlets there
MERGE = 0.80          # two headlines are one story at this closeness of home fingerprints (EmbeddingGemma; measured on a day of real
                      # feeds: at 0.85 a story of 10 outlets fell apart into three, at 0.80 stories of 10–15 outlets stayed whole and apart)
SEARCH_FLOOR = 0.40   # below this closeness to the query nothing matches by meaning
SEARCH_SPAN = 0.20    # a search keeps headlines this close to its best match (the closeness itself varies by query, the order holds:
                      # one headline worded like the query in its own language stood 0.63, the same story in English 0.49–0.53)
SEARCH_ENOUGH = 12    # with fewer finds in our outlets the search asks Google News too
FACTCHECK_HOURS = 168  # fact checks come days after a story: they are looked at over a week
LEAD_CHARS = 450      # the opening of a story's article that Sena gets
DOUBT_CHARS = 1200    # a doubtful story's longer passage: enough for Sena to check it without opening the page
TEXT_HOURS = 6        # an article read once is kept this long
WATCH_MINUTES = 30    # the background watch gathers every feed this often
SNAPSHOT_HOURS = 48   # what a gathering keeps: two days of news
FRESH_MINUTES = 35    # a digest takes the watch's gathering while it is this fresh, and asks the feeds itself otherwise
USED_DAYS = 3         # the watch works while news were asked for within these days, or a topic is watched
READ_AHEAD = 24       # how many of the strongest stories the watch reads ahead
ALERT_OWNERS = 3      # independent owners a story needs before Sena writes first about it (2 for local news)
ALERT_DAY_CAP = 8     # at most this many such messages a day
ALERT_TOPIC_HOURS = 2  # one watched topic speaks at most once in this many hours
WATCH_MATCH = 0.42    # a headline this close to a watched topic (home fingerprints) is about it: the calibration's relevant headlines
                      # stood 0.43–0.68 to their query, the unrelated ones under 0.41; Sena herself drops a miss before telling
ENGLISH = ("en-US", "US", "US:en")
EDITIONS = {   # Google News edition of a country: the language and region its local news are found in
    "VN": ("vi", "VN", "VN:vi"), "ID": ("id", "ID", "ID:id"), "TH": ("th", "TH", "TH:th"), "MY": ("en-MY", "MY", "MY:en"),
    "SG": ("en-SG", "SG", "SG:en"), "PH": ("en-PH", "PH", "PH:en"), "IN": ("en-IN", "IN", "IN:en"), "JP": ("ja", "JP", "JP:ja"),
    "KR": ("ko", "KR", "KR:ko"), "TW": ("zh-TW", "TW", "TW:zh-Hant"), "HK": ("zh-HK", "HK", "HK:zh-Hant"), "RU": ("ru", "RU", "RU:ru"),
    "UA": ("uk", "UA", "UA:uk"), "TR": ("tr", "TR", "TR:tr"), "AE": ("en", "AE", "AE:en"), "IL": ("he", "IL", "IL:he"),
    "DE": ("de", "DE", "DE:de"), "FR": ("fr", "FR", "FR:fr"), "ES": ("es", "ES", "ES:es"), "IT": ("it", "IT", "IT:it"),
    "PT": ("pt-PT", "PT", "PT:pt-150"), "NL": ("nl", "NL", "NL:nl"), "PL": ("pl", "PL", "PL:pl"), "CZ": ("cs", "CZ", "CZ:cs"),
    "RS": ("sr", "RS", "RS:sr"), "GB": ("en-GB", "GB", "GB:en"), "IE": ("en-IE", "IE", "IE:en"), "US": ENGLISH,
    "CA": ("en-CA", "CA", "CA:en"), "AU": ("en-AU", "AU", "AU:en"), "NZ": ("en-NZ", "NZ", "NZ:en"), "MX": ("es-419", "MX", "MX:es-419"),
    "AR": ("es-419", "AR", "AR:es-419"), "BR": ("pt-BR", "BR", "BR:pt-419"), "ZA": ("en-ZA", "ZA", "ZA:en"), "NG": ("en-NG", "NG", "NG:en"),
}
NOT_NEWS_LINK = re.compile(r"/(opinions?|commentisfree|comment|voices|columns?|columnists?|editorials?|letters|blogs?|podcasts?|videos?|"
                           r"quiz(zes)?|crosswords?|puzzles|horoscopes?|deals|coupons|sponsored|partner-content|shopping|reviews?)/", re.I)
NOT_NEWS_TITLE = re.compile(r"^(opinion|letters?|quiz|crossword|wordle|connections|horoscope|watch|listen|podcast)\b|"
                            r"\b(deals?|discount|% off|promo code|sponsored)\b|^(мнение|колонка|тест|реклама)\b", re.I)
RUMOR = re.compile(r"\b(?:reportedly|rumou?r(?:s|ed)?|allegedly|unconfirmed|leaked|sources? (?:say|says|said|told)|"   # the words of anonymous
                   r"according to (?:sources|people|a person|reports)|(?:people|person) familiar|anonymous (?:source|official)s?)\b|"   # sourcing
                   r"по данным источник\w*|по информации источник\w*|источник\w* (?:сообщ|рассказ|утвержд)\w*|сообщают сми|якобы|"
                   r"\bслух\w*|утечк\w*|неподтвержд\w*", re.I)
_vectors_cache = {}   # headline -> home fingerprint
_texts = {}           # article link -> (when read, the article)
_snapshot = {}        # "all" or "local:<country>:<place>" -> the watch's last gathering {"t", "items", "ok", "failed"}
_topic_vectors = {}   # watched topic -> its home fingerprint
_watch_lock = threading.Lock()
_state_lock = threading.Lock()
_vectors_lock = threading.Lock()


# ---------- feeds ----------
def _tag(el):
    return el.tag.split("}")[-1] if isinstance(el.tag, str) else ""


def _text(entry, *names):
    for child in entry:
        if _tag(child) in names and (child.text or "").strip():
            return child.text.strip()
    return ""


def _when(raw):
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fix_clock(items):
    """A feed that writes its local time as GMT puts its fresh news in the future: its whole clock moves back by whole hours
    (the offset of its zone, at most 14); a single item dated further ahead just becomes "now"."""
    now = datetime.now(timezone.utc)
    ahead = max((i["time"] - now for i in items if i["time"]), default=timedelta(0))
    shift = timedelta(hours=math.ceil(ahead.total_seconds() / 3600)) if timedelta(minutes=10) < ahead <= timedelta(hours=14) else timedelta(0)
    for i in items:
        if i["time"]:
            i["time"] = min(i["time"] - shift, now)
    return items


def _root(xml_bytes):
    try:
        return ET.fromstring(xml_bytes)
    except ET.ParseError:   # a feed with a stray character: the forgiving parser reads what it can
        root = etree.fromstring(xml_bytes, parser=etree.XMLParser(recover=True))
        if root is None:
            raise
        return root


def parse(xml_bytes):
    """RSS 2.0, Atom and RDF -> [{"title", "link", "time", "summary", "outlet", "outlet_url"}] (outlet — who wrote it, in Google News)."""
    items = []
    for entry in (e for e in _root(xml_bytes).iter() if _tag(e) in ("item", "entry")):
        link = _text(entry, "link")
        if not link:   # in Atom the link is an attribute
            link = next((c.get("href", "") for c in entry if _tag(c) == "link" and c.get("href")), "")
        summary = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", _text(entry, "description", "summary", "content", "encoded"))))
        outlet = next((c for c in entry if _tag(c) == "source"), None)
        items.append({"title": html.unescape(_text(entry, "title")), "link": link.strip(),
                      "time": _when(_text(entry, "pubDate", "published", "updated", "date")), "summary": summary.strip()[:300],
                      "outlet": (outlet.text or "").strip() if outlet is not None else "", "outlet_url": outlet.get("url", "") if outlet is not None else ""})
    return _fix_clock([i for i in items if i["title"]])


def _hacker_news(count=15):
    """Hacker News top stories via the official API (Firebase): the site's own feeds return a refusal."""
    base = "https://hacker-news.firebaseio.com/v0"

    def get(path):
        return net.as_json(net.get(f"{base}/{path}", timeout=15, who="Hacker News", ttl=CACHE_SECONDS), "Hacker News")

    ids = get("topstories.json")[:count]
    with ThreadPoolExecutor(max_workers=8) as pool:
        stories = list(pool.map(lambda i: get(f"item/{i}.json"), ids))
    return [{"title": s["title"], "link": s.get("url") or f"https://news.ycombinator.com/item?id={s['id']}",
             "time": datetime.fromtimestamp(s["time"], timezone.utc), "summary": f"{s.get('score', 0)} очков, {s.get('descendants', 0)} комментариев",
             "outlet": "", "outlet_url": ""}
            for s in stories if s and s.get("title") and not s.get("dead") and not s.get("deleted")]


def sources(topic, country=""):
    """The outlets of a topic; for local news — every outlet of that country, whatever its topic, but fact checkers."""
    if topic == "local":
        return [s for s in SOURCES if s["country"] == country.upper() and s.get("kind") != "factcheck"]
    topics = [t for t in TOPICS_RU if t not in ("local", "all")] if topic == "all" else [topic]
    return [s for s in SOURCES if s["topic"] in topics]


def _own(item, source, position):
    # position in the feed: editors put what matters higher
    item.update(source=source["name"], group=source["group"], tier=source["tier"], kind=source.get("kind", "news"),
                text=source.get("text", True), position=position, topic=source.get("topic", ""))
    return item


def _fetch(source):
    items = _hacker_news() if source["url"] == "hn:top" else parse(net.get(source["url"], headers=HEADERS, timeout=15, who=source["name"], ttl=CACHE_SECONDS).content)
    return [_own(i, source, position) for position, i in enumerate(items)]


def _known(name):
    """An outlet named by Google News, found on our list by its name; else None."""
    key = re.sub(r"^the |[^\w ]", "", (name or "").lower()).strip()
    return next((s for s in SOURCES if re.sub(r"^the |[^\w ]", "", s["name"].lower()).strip() == key), None)


def _google(path, params):
    """Google News items as their outlets' own: a known outlet keeps its tier and owner, an unknown one is tier 3, its own owner."""
    items = parse(net.get(f"https://news.google.com/rss/{path}", params, HEADERS, timeout=15, who="Google News", ttl=CACHE_SECONDS).content)
    for i in items:
        if i["outlet"] and i["title"].endswith(" - " + i["outlet"]):   # Google adds the outlet to the headline
            i["title"] = i["title"][:-len(i["outlet"]) - 3]
        if i["outlet"].startswith("http"):   # some outlets are named by their address only
            i["outlet"] = articles.host(i["outlet"])
        i["summary"] = ""   # Google's description repeats the headline
    return [_own(i, _known(i["outlet"]) or {"name": i["outlet"] or "Google News", "group": articles.host(i["outlet_url"]) or i["outlet"] or "google", "tier": 3},
                 position) for position, i in enumerate(items)]


def _google_place(place, country):
    """Local news of a place: Google's local section in the country's own language, and fresh English mentions."""
    hl, gl, ceid = EDITIONS.get(country.upper(), ENGLISH)
    items = _google(f"headlines/section/geo/{quote(place)}", {"hl": hl, "gl": gl, "ceid": ceid})
    if (hl, gl) != ENGLISH[:2]:
        items += _google("search", {"q": f"{place} when:2d", "hl": ENGLISH[0], "gl": ENGLISH[1], "ceid": ENGLISH[2]})
    return items


def _google_search(query, hours):
    hl, gl, ceid = EDITIONS["RU"] if re.search("[а-яё]", query, re.I) else ENGLISH
    return _google("search", {"q": f"{query} when:{max(1, min(7, round(hours / 24)))}d", "hl": hl, "gl": gl, "ceid": ceid})


def collect(topic, hours, country="", place="", query=""):
    """(fresh news items, the feeds that answered, the feeds that didn't — by address, "google" for Google News). Fact checks of the
    last week come along: they flag a story, not make one. With a query — only Google News' search for it (the wider net when our
    outlets found little)."""
    if query:
        jobs = [("google", lambda: _google_search(query, hours))]
    else:
        jobs = [(s["url"], lambda s=s: _fetch(s)) for s in sources(topic, country)]
        if topic != "local":
            jobs += [(s["url"], lambda s=s: _fetch(s)) for s in SOURCES if s.get("kind") == "factcheck"]
        elif place:
            jobs.append(("google", lambda: _google_place(place, country)))

    def run(job):
        key, load = job
        try:
            return key, load(), None
        except (net.WorldError, ET.ParseError, etree.XMLSyntaxError, ValueError, KeyError, TypeError) as e:
            return key, [], type(e).__name__

    now = time.time()
    items, failed, seen = [], [], set()
    with ThreadPoolExecutor(max_workers=24) as pool:
        for key, got, error in pool.map(run, jobs):
            if error:
                failed.append(key)
                continue
            for i in got:
                span = FACTCHECK_HOURS if i["kind"] == "factcheck" else hours
                if not i["time"] or i["time"].timestamp() < now - span * 3600 or i["link"] in seen:
                    continue
                if NOT_NEWS_LINK.search(i["link"]) or NOT_NEWS_TITLE.search(i["title"]):   # columns, podcasts, quizzes, sales
                    continue
                seen.add(i["link"])
                items.append(i)
    return items, {key for key, _ in jobs} - set(failed), sorted(set(failed))


# ---------- one story across outlets ----------
_WORD = re.compile(r"[\w]{4,}", re.U)


def _words(title):
    # word stems (first 5 letters): "refinery" and "refineries", "Зеленский" and "Зеленского" count as the same word
    return {w.lower()[:5] for w in _WORD.findall(title)}


def _alike(words, other):
    shared = len(words & other)
    return shared >= 3 and shared / max(1, min(len(words), len(other))) >= 0.5


def _line(item):
    # the headline alone: it names the event as well as with the summary (the clusters of a day's feeds came out the same),
    # and the home model fingerprints headlines nearly twice as fast
    return item["title"].strip()


def _vectors(texts):
    """Home fingerprints of headlines; None when the home model isn't on this Mac (then stories are merged by shared words).
    Always the home model, never the cloud: MERGE was measured on it, and hundreds of headlines are free to fingerprint at home."""
    if not texts or not local.installed()["embed"]:
        return None
    with _vectors_lock:   # the watch and a digest fingerprint at the same time: one's start-over can't pull a vector from under the other
        have = {t: _vectors_cache[t] for t in texts if t in _vectors_cache}
    todo = list(dict.fromkeys(t for t in texts if t not in have))
    try:
        for k in range(0, len(todo), 64):
            have.update(zip(todo[k:k + 64], local.embed(todo[k:k + 64], purpose="новости: склейка сюжетов")))
    except local.Unavailable as e:
        events.error("Склейка новостей по смыслу не вышла, склеиваю по словам", e)
        return None
    with _vectors_lock:
        if len(_vectors_cache) > 30000:   # a month of headlines: the cache starts over
            _vectors_cache.clear()
        _vectors_cache.update(have)
    return np.stack([have[t] for t in texts])


def cluster(items, vectors=None):
    """The same story across outlets -> one group {"items": [...]}: by meaning when fingerprints are given, else by shared words."""
    groups = []
    if vectors is not None:
        sums = np.zeros((len(items), vectors.shape[1]), dtype=np.float32)
        units = np.zeros_like(sums)
    for j in sorted(range(len(items)), key=lambda j: items[j]["time"], reverse=True):
        words = _words(items[j]["title"])
        if vectors is not None:
            sims = units[:len(groups)] @ vectors[j] if groups else np.zeros(0)
            hit = int(np.argmax(sims)) if len(sims) and sims.max() >= MERGE else None
        else:
            hit = next((k for k, g in enumerate(groups) if _alike(words, g["words"])), None)
        if hit is None:
            groups.append({"items": [], "words": set()})
            hit = len(groups) - 1
        groups[hit]["items"].append(items[j])
        groups[hit]["words"] |= words
        if vectors is not None:
            sums[hit] += vectors[j]
            units[hit] = sums[hit] / (float(np.linalg.norm(sums[hit])) or 1.0)
    return groups


def _voices(group):
    """{owner: weight} of the outlets carrying a story: one owner is one voice; state media weigh little, fact checkers nothing."""
    out = {}
    for i in group["items"]:
        if i["kind"] != "factcheck":
            out[i["group"]] = max(out.get(i["group"], 0), 0.5 if i["kind"] == "state" else WEIGHT[i["tier"]])
    return out


def _lead(group):
    """The story's most reliable report."""
    return min((i for i in group["items"] if i["kind"] != "factcheck"), key=lambda i: (i["tier"], i["kind"] == "state", i["position"]))


def rank(groups, now=None):
    """Stories by weight: independent owners and their reliability first, then a high place in a feed and freshness.
    A fact check alone isn't a story."""
    now = now or time.time()
    for g in groups:
        g["newest"] = max(i["time"] for i in g["items"])
        top = max(0, 5 - min(i["position"] for i in g["items"])) * 0.8
        g["score"] = sum(_voices(g).values()) * 10 + top - max(0.0, (now - g["newest"].timestamp()) / 3600) * 0.3
    return sorted((g for g in groups if _voices(g)), key=lambda g: -g["score"])


def _diverse(groups, limit, per_source=2):
    """No more than per_source stories led by one owner (otherwise whoever publishes most often takes over); the rest fill in at the end."""
    taken, rest, count = [], [], {}
    for g in groups:
        key = _lead(g)["group"]
        if count.get(key, 0) < per_source:
            count[key] = count.get(key, 0) + 1
            taken.append(g)
        else:
            rest.append(g)
    return (taken + rest)[:limit]


def _independent(group):
    """The outlets carrying a story that speak for themselves: one name per owner, most reliable first; no state media, no fact checkers."""
    names = {}
    for i in sorted(group["items"], key=lambda i: (i["tier"], i["position"])):
        if i["kind"] not in ("state", "factcheck"):
            names.setdefault(i["group"], i["source"])
    return list(names.values())


def _status(group):
    """Who carries the story, in words: independent outlets (one per owner), state media, fact checkers."""
    state = [i["source"] for i in group["items"] if i["kind"] == "state"]
    checks = [i["source"] for i in group["items"] if i["kind"] == "factcheck"]
    free = _independent(group)
    line = (f"{_n(len(free), 'независимое издание', 'независимых издания', 'независимых изданий')}: {', '.join(free[:4])}" + (" и др." if len(free) > 4 else "")
            if len(free) > 1 else f"пока одно издание: {free[0]}" if free else "только государственные СМИ")
    if state and free:
        line += f"; гос. СМИ: {', '.join(dict.fromkeys(state))}"
    if checks:
        line += f"; есть проверка фактов: {', '.join(dict.fromkeys(checks))}"
    return line


def _tidy(url):
    """A link without the tracking tail (utm_…)."""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(query=urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_")])))


def _n(count, one, few, many):
    """A number with its word in the right form: 1 лента, 3 ленты, 63 ленты, 11 лент."""
    rest = abs(count) % 100
    return f"{count} {many if 11 <= rest <= 14 else one if rest % 10 == 1 else few if 2 <= rest % 10 <= 4 else many}"


def _ago(dt):
    minutes = int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
    return f"{minutes} мин назад" if minutes < 90 else f"{minutes // 60} ч назад"


# ---------- a story as Sena gets it ----------
def _readers(group):
    """Whom to read the story from: the most reliable outlets whose text is open, not closed sites; a direct link before one
    through Google News (that costs Google two requests, and it soon asks to wait)."""
    return sorted((i for i in group["items"] if i["text"] and i["kind"] != "factcheck" and i["link"].startswith("http") and articles.readable(i["link"])),
                  key=lambda i: (bool(articles.GOOGLE.match(i["link"])), i["tier"], i["kind"] == "state", i["position"]))


def _article(item):
    """An outlet's article, read once in TEXT_HOURS (the watch reads the strongest ahead)."""
    hit = _texts.get(item["link"])
    if hit and time.time() - hit[0] < TEXT_HOURS * 3600:
        return hit[1]
    found = articles.read(item["link"], limit=DOUBT_CHARS * 2)
    if len(_texts) > 3000:
        _texts.clear()
    _texts[item["link"]] = (time.time(), found)
    return found


def _opening(text, limit):
    """The beginning of an article, cut at the end of a sentence within limit."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > limit // 2 else cut.rsplit(" ", 1)[0] + "…"


def prepare(group):
    """{"title", "status", "doubts", "lead", "ago", "url"} of a story: the headline of its most reliable report, who carries it,
    the opening of the most reliable open article (or the feed's summary when none could be read). A doubtful story — one outlet,
    only state media, or words of anonymous sourcing while fewer than three carry it — gets a longer passage, so Sena can check
    it without opening the page."""
    lead = _lead(group)
    article = None
    for item in _readers(group)[:2]:
        try:
            article = _article(item)
            break
        except net.WorldError:
            continue
    text = article["text"] if article else lead["summary"]
    free = _independent(group)
    # words of anonymous sourcing matter while few carry the story; three independent owners outweigh a phrase in one text
    rumors = list(dict.fromkeys(m.group(0).lower() for m in RUMOR.finditer(f"{lead['title']} {text[:DOUBT_CHARS]}"))) if len(free) < 3 else []
    doubts = (["пока одно издание" if free else "только государственные СМИ"] if len(free) < 2 else []) \
        + ([f"в тексте: {', '.join('«' + w + '»' for w in rumors[:4])}"] if rumors else []) \
        + ([] if article else ["статью прочитать не удалось, есть только анонс"])
    doubtful = len(free) < 2 or bool(rumors)
    return {"title": lead["title"], "status": _status(group), "doubts": doubts, "ago": _ago(max(i["time"] for i in group["items"])),
            "lead": _opening(text, DOUBT_CHARS if doubtful else LEAD_CHARS), "url": _tidy(article["url"] if article else lead["link"])}


def _story_text(story):
    status = story["status"][:1].upper() + story["status"][1:]
    return (f"{story['title']}\n   {status}, {story['ago']}" + (f"\n   Сомнительно: {'; '.join(story['doubts'])}" if story["doubts"] else "")
            + (f"\n   {story['lead']}" if story["lead"] else "") + f"\n   {story['url']}")


# ---------- what was asked for, what is watched ----------
def _state():
    """The watch's memory: watched topics, stories already told, today's count, when news were last asked for and for which place."""
    s = store.load_json(config.NEWS_WATCH_FILE, {}) or {}
    return {"watches": s.get("watches", []), "told": s.get("told", {}), "day": s.get("day", ""), "count": s.get("count", 0),
            "used": s.get("used", 0), "place": s.get("place", ""), "country": s.get("country", "")}


def _remember(**changes):
    with _state_lock:
        state = _state()
        state.update(changes)
        store.save_json(config.NEWS_WATCH_FILE, state)


def _key(topic, country="", place=""):
    return f"local:{country.upper()}:{place.strip().lower()}" if topic == "local" else "all"


def _broken(snap):
    # a gathering made while the network was down or failing: more feeds failed than answered — it says nothing about the news
    return len(snap["ok"]) < len(snap["failed"])


def _gather(topic, hours, country="", place=""):
    """(items, feeds that answered, feeds that didn't): the watch's gathering while it is fresh, whole and covers the request,
    otherwise straight from the feeds."""
    snap = _snapshot.get(_key(topic, country, place))
    if not snap or _broken(snap) or hours > SNAPSHOT_HOURS or time.time() - snap["t"] > FRESH_MINUTES * 60:
        return collect(topic, hours, country, place)
    feeds = {s["url"] for s in sources(topic, country)} | ({"google"} if topic == "local" else {s["url"] for s in SOURCES if s.get("kind") == "factcheck"})
    now = time.time()
    items = [i for i in snap["items"] if (topic in ("all", "local") or i["topic"] == topic or i["kind"] == "factcheck")
             and i["time"].timestamp() >= now - (FACTCHECK_HOURS if i["kind"] == "factcheck" else hours) * 3600]
    return items, feeds & snap["ok"], sorted(feeds & set(snap["failed"]))


def _names(feeds):
    name = {s["url"]: s["name"] for s in SOURCES}
    return sorted({name.get(f, "Google News") for f in feeds})


def _has(text, word):
    # a long word by its stem (the first 5 letters: «пошлины» finds «пошлинах»), a short one whole
    return (word[:5] in text) if len(word) >= 5 else re.search(rf"\b{re.escape(word)}\b", text) is not None


def _matching(items, vectors, query):
    """Items about the query: all its words in the headline or summary, or close by meaning in any language."""
    words = re.findall(r"\w{2,}", query.lower())
    keep = {j for j, i in enumerate(items) if words and all(_has((i["title"] + " " + i["summary"]).lower(), w) for w in words)}
    if vectors is not None:
        try:
            sims = vectors @ local.embed([query], query=True, purpose="новости: поиск")[0]
            best = float(sims.max()) if len(sims) else 0.0
            if best >= SEARCH_FLOOR:
                keep |= {int(j) for j in np.argsort(-sims)[:60] if sims[j] >= best - SEARCH_SPAN}
        except local.Unavailable as e:
            events.error("Поиск новостей по смыслу не вышел, ищу по словам", e)
    keep = sorted(keep)
    return [items[j] for j in keep], (vectors[keep] if vectors is not None and keep else None)


def stories(topic="world", hours=24, limit=8, place="", country="", query=""):
    """The strongest stories of the last hours, prepared for Sena to choose from: ([story], {"outlets", "items", "failed"}).
    A few more than asked for come: Sena drops the noise and the doubtful that don't hold up."""
    if topic not in TOPICS_RU:
        raise net.WorldError(f"topic — одно из: {', '.join(TOPICS_RU)}")
    place, country = (place or "").strip(), (country or "").strip().upper()
    if topic == "local" and not country:
        raise net.WorldError("Для местных новостей нужна страна (country: VN, ID, TH, RU…) и лучше город (place)")
    hours, limit = clamp(hours, 24, 1, 168), clamp(limit, 8, 1, 15)
    _remember(used=time.time(), **({"place": place, "country": country} if topic == "local" and place else {}))
    items, ok, failed = _gather(topic, hours, country, place)
    vectors = _vectors([_line(i) for i in items])
    if query:
        items, vectors = _matching(items, vectors, query)
        if len(items) < SEARCH_ENOUGH:   # our outlets found little: Google News widens the net (its outlets are unknown, tier 3)
            known = {i["link"] for i in items}
            extra, google_ok, google_failed = collect(topic, hours, query=query)
            extra = [i for i in extra if i["link"] not in known]
            if extra:
                more = _vectors([_line(i) for i in extra])
                vectors = more if not items else np.vstack([vectors, more]) if vectors is not None and more is not None else None
            items, ok, failed = items + extra, ok | google_ok, failed + google_failed
    groups = _diverse(rank(cluster(items, vectors)), min(15, limit + 4))
    with ThreadPoolExecutor(max_workers=8) as pool:
        prepared = list(pool.map(prepare, groups))
    return prepared, {"outlets": len(ok), "items": len(items), "failed": _names(failed)}


SILENT_NAMED = 8   # feeds that didn't answer named by name; the rest are a count


def _silent(failed):
    """The feeds that didn't answer: a few names and how many more, not a hundred and sixty of them."""
    more = len(failed) - SILENT_NAMED
    return ", ".join(failed[:SILENT_NAMED]) + (f" и ещё {_n(more, 'лента', 'ленты', 'лент')}" if more > 0 else "")


def _render(rows, info, what):
    if not info["outlets"] and info["failed"]:   # nothing answered: that is no network, not "no news"
        return Failure(f"{what}: не ответила ни одна из {_n(len(info['failed']), 'ленты', 'лент', 'лент')} — похоже, нет связи. Попробуй позже.")
    if not rows:
        return f"{what}: ничего не нашла" + (f" (не ответили: {_silent(info['failed'])})" if info["failed"] else "")
    lines = [f"{what}: {_n(len(rows), 'самый сильный сюжет', 'самых сильных сюжета', 'самых сильных сюжетов')} из "
             f"{_n(info['outlets'], 'ленты', 'лент', 'лент')} (сильнее тот, о ком пишут больше независимых изданий, и тем надёжнее, чем они надёжнее)."]
    lines += [f"{n}. {_story_text(r)}" for n, r in enumerate(rows, 1)]
    if info["failed"]:
        lines.append(f"Не ответили: {_silent(info['failed'])}.")
    return external("\n".join(lines), "новостные ленты")


# ---------- the background watch ----------
def _take(topic, country="", place=""):
    """A fresh gathering of two days into the snapshot, its headlines fingerprinted at home."""
    items, ok, failed = collect(topic, SNAPSHOT_HOURS, country, place)
    _vectors([_line(i) for i in items])
    _snapshot[_key(topic, country, place)] = snap = {"t": time.time(), "items": items, "ok": ok, "failed": failed}
    return snap


def _about(group, topic):
    """Is a story about a watched topic: all its words in one headline, or a headline close to it by meaning (home fingerprints)."""
    words = re.findall(r"\w{2,}", topic.lower())
    if words and any(all(_has(i["title"].lower(), w) for w in words) for i in group["items"]):
        return True
    if not local.installed()["embed"]:
        return False
    try:
        if topic not in _topic_vectors:
            _topic_vectors[topic] = local.embed([topic], query=True, purpose="новости: слежка")[0]
    except local.Unavailable:
        return False
    vectors = [_vectors_cache.get(_line(i)) for i in group["items"]]
    return any(float(v @ _topic_vectors[topic]) >= WATCH_MATCH for v in vectors if v is not None)


def _strong(group):
    """Strong enough to write first about: ALERT_OWNERS independent owners with one of tier 2 or better (local news: two owners)."""
    free = [i for i in group["items"] if i["kind"] not in ("state", "factcheck")]
    return bool(free) and len({i["group"] for i in free}) >= (2 if group.get("local") else ALERT_OWNERS) \
        and (group.get("local") or min(i["tier"] for i in free) <= 2)


def _alert_text(topic, story):
    return (f"Слежка за новостями: по теме «{topic}», за которой Тема просил следить, появился сильный новый сюжет.\n"
            + external(_story_text(story), "новостные ленты")
            + "\nЕсли это правда новое и важное по теме — коротко перескажи Теме по-русски, со ссылкой. "
              "Если не по теме, мелочь или ты уже об этом говорила — ответь OK.")


def _alerts(groups):
    """The messages to send about new strong stories on watched topics: each story once, a topic at most once in
    ALERT_TOPIC_HOURS, no more than ALERT_DAY_CAP a day. Marked as told before they go, so a failure doesn't repeat them;
    the articles are read after, outside the lock (a digest or a new watch doesn't wait on someone's slow site)."""
    chosen = []
    with _state_lock:
        state = _state()
        now, day = time.time(), datetime.now().strftime("%Y-%m-%d")
        if state["day"] != day:
            state["day"], state["count"] = day, 0
        state["told"] = {link: t for link, t in state["told"].items() if now - t < SNAPSHOT_HOURS * 3600 * 2}
        for watch_ in state["watches"]:
            if state["count"] >= ALERT_DAY_CAP or now - watch_.get("spoke", 0) < ALERT_TOPIC_HOURS * 3600:
                continue
            for g in groups:   # the strongest first
                if not _strong(g) or any(i["link"] in state["told"] for i in g["items"]) \
                        or min(i["time"] for i in g["items"]).timestamp() < watch_["added"] - 3600 or not _about(g, watch_["topic"]):
                    continue   # weak, already told, begun before the watch was set, or not about the topic
                chosen.append((watch_["topic"], g))
                state["told"].update({i["link"]: now for i in g["items"]})
                watch_["spoke"], state["count"] = now, state["count"] + 1
                break
        store.save_json(config.NEWS_WATCH_FILE, state)
    return [(topic, prepare(g)) for topic, g in chosen]


def watch(alert):
    """The background watch: the scheduler calls it every few minutes, it works every WATCH_MINUTES while news are in use (asked
    for within USED_DAYS, or a topic watched). It gathers two days of every feed and of the owner's last place, fingerprints the
    headlines at home and reads the strongest stories ahead — then a digest takes seconds. A strong new story on a watched topic,
    in the owner's waking hours, goes to alert(text): Sena decides whether it's worth telling and tells it. Costs nothing by itself."""
    state = _state()
    if not state["watches"] and time.time() - state["used"] > USED_DAYS * 86400:
        return
    last = _snapshot.get("all")
    if (last and not _broken(last) and time.time() - last["t"] < WATCH_MINUTES * 60) or not _watch_lock.acquire(blocking=False):
        return   # gathered lately (one made while the network was down is gathered again at the next round)
    try:
        started, groups = time.time(), []
        takes = [("all", "", "")] + ([("local", state["country"], state["place"])] if state["place"] and state["country"] else [])
        for topic, country, place in takes:
            snap = _take(topic, country, place)
            items = [i for i in snap["items"] if i["kind"] == "factcheck" or i["time"].timestamp() >= time.time() - 24 * 3600]
            ranked = rank(cluster(items, _vectors([_line(i) for i in items])))
            for g in ranked:
                g["local"] = topic == "local"
            with ThreadPoolExecutor(max_workers=8) as pool:   # read ahead: a digest finds these articles already read
                list(pool.map(prepare, ranked[:READ_AHEAD if topic == "all" else READ_AHEAD // 3]))
            groups += ranked
        events.log("watch", f"новости: собрала {sum(len(_snapshot[_key(*t)]['items']) for t in takes)} заголовков, "
                            f"прочитала вперёд сильнейшие сюжеты за {round(time.time() - started)} с", watches=len(state["watches"]))
        told = _alerts(sorted(groups, key=lambda g: -g["score"])) if state["watches"] and settings.awake() else []
    finally:
        _watch_lock.release()
    for topic, story in told:
        events.log("watch", f"новости: по теме «{topic}» сильный сюжет — передаю Сене", topic=topic, url=story["url"])
        alert(_alert_text(topic, story))


# ---------- tools ----------
def news_digest(topic="world", hours=24, limit=8, place="", country=""):
    """The strongest stories of a topic over the last hours, for Sena to choose from and retell."""
    rows, info = stories(topic, hours, limit, place, country)
    where = f", {place}" if topic == "local" and place else ""
    return _render(rows, info, f"Новости за {clamp(hours, 24, 1, 168)} ч, {TOPICS_RU[topic]}{where}")


def news_search(query, hours=48, limit=8, topic="all"):
    """What the news says about a word or a subject, in any language."""
    if not str(query or "").strip():
        raise net.WorldError("Пустой запрос")
    rows, info = stories(topic if topic != "local" else "all", hours, limit, query=str(query).strip())
    return _render(rows, info, f"Новости по запросу «{query}» за {clamp(hours, 48, 1, 168)} ч")


def news_read(link):
    """The whole text of an article."""
    try:
        a = articles.read(str(link or "").strip())
    except net.WorldError as e:
        return f"Статью прочитать не удалось: {e}. Возьми другую ссылку на этот сюжет или найди его через news_search."
    head = " · ".join(x for x in (a["site"], a["date"]) if x)
    return external(f"{a['title']}\n{head}\n{a['url']}\n\n{a['text']}", a["site"] or "статья")


def news_watch(topic):
    """Watch a topic: Sena writes first when a strong new story about it appears."""
    topic = re.sub(r"\s+", " ", str(topic or "")).strip()
    if not topic:
        raise net.WorldError("За чем следить? Тема словами: «выборы в США», «Дананг», «биткоин»")
    with _state_lock:
        state = _state()
        if any(w["topic"].lower() == topic.lower() for w in state["watches"]):
            return f"За «{topic}» уже слежу"
        item = {"id": uuid.uuid4().hex[:6], "topic": topic, "added": time.time()}
        state["watches"].append(item)
        store.save_json(config.NEWS_WATCH_FILE, state)
    events.log("watch", f"новости: слежу за «{topic}»", topic=topic)
    s = settings.load()
    return (f"Слежу [{item['id']}]: «{topic}». Напишу первой, когда появится новый сильный сюжет — о нём пишут хотя бы "
            f"{ALERT_OWNERS} независимых издания (о местном — два); проверяю раз в {WATCH_MINUTES} минут, с {s['active_from']} до {s['active_to']} ч.")


def news_watches():
    """The topics being watched."""
    state = _state()
    return "\n".join(f"[{w['id']}] {w['topic']} (с {datetime.fromtimestamp(w['added']).strftime('%d.%m.%Y')})" for w in state["watches"]) \
        or "Ни за чем не слежу"


def news_unwatch(id):
    """Stop watching a topic."""
    with _state_lock:
        state = _state()
        kept = [w for w in state["watches"] if w["id"] != str(id).strip()]
        if len(kept) == len(state["watches"]):
            return Failure("Нет такой слежки")
        state["watches"] = kept
        store.save_json(config.NEWS_WATCH_FILE, state)
    events.log("watch", f"новости: больше не слежу [{id}]")
    return "Больше не слежу"


TOOLS = [
    tool("news_digest", "Новости за последние часы из ~160 проверенных изданий (агентства, общественные вещатели, солидная пресса разных стран, "
                        "русскоязычные независимые и российские, спорт, культура, местные): самые сильные сюжеты — о ком пишут больше независимых "
                        "изданий; одинаковые склеены по смыслу на любом языке. У каждого — кто пишет, начало статьи, ссылка; сомнительные помечены "
                        "и идут с длинным отрывком. Это сырьё: выбери и перескажи сама.",
         {"topic": {**string("world — мир и политика, russia — по-русски, tech — ИТ, finance — финансы и крипта, sport — спорт, culture — культура, "
                             "local — местные новости места, all — всё; по умолчанию world"), "enum": list(TOPICS_RU)},
          "hours": {"type": "integer", "description": "За сколько часов, по умолчанию 24, до 168"},
          "limit": {"type": "integer", "description": "Сколько сюжетов нужно Теме, по умолчанию 8, до 15 (придёт на несколько больше — на выбор)"},
          "place": string("Только для local: город или область, где сейчас Тема, как его пишут местные (Da Nang, Денпасар)"),
          "country": string("Только для local: код страны из двух букв (VN, ID, TH, RU)")}),
    tool("news_search", "Что пишут новости о человеке, стране, компании, событии — на любом языке: русский запрос найдёт и английские статьи. "
                        "Ищет в ~160 проверенных изданиях (мало находок — и в Google News), склеивает одинаковые сюжеты; сырьё, как у news_digest.",
         {"query": string("О чём искать: имя, событие, тема"),
          "hours": {"type": "integer", "description": "За сколько часов, по умолчанию 48, до 168"},
          "limit": {"type": "integer", "description": "Сколько сюжетов, по умолчанию 8, до 15"},
          "topic": {**string("Где искать, по умолчанию all"), "enum": [t for t in TOPICS_RU if t != "local"]}}, ["query"]),
    tool("news_read", "Полный текст статьи по ссылке (из новостей или любой другой): без меню, рекламы и комментариев. Для подробностей "
                      "и проверки сомнительного сюжета. Закрытые подпиской сайты не читаются — тогда другая ссылка на тот же сюжет.",
         {"link": string("Адрес статьи")}, ["link"]),
    tool("news_watch", "Следить за темой в новостях: Сена сама напишет Теме, когда о ней появится новый сильный сюжет (пишут несколько "
                       "независимых изданий). Проверка раз в полчаса, ночью молчит. Бесплатно: деньги — только когда есть что сказать.",
         {"topic": string("Тема словами, лучше 2–4 слова: «выборы в США», «искусственный интеллект», «Дананг»")}, ["topic"]),
    tool("news_watches", "За какими темами в новостях Сена следит (с номерами).", {}),
    tool("news_unwatch", "Перестать следить за темой в новостях.", {"id": string("Номер слежки из news_watches")}, ["id"]),
]
FUNCTIONS = {"news_digest": news_digest, "news_search": news_search, "news_read": news_read,
             "news_watch": news_watch, "news_watches": news_watches, "news_unwatch": news_unwatch}
