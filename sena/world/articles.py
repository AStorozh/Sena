"""Reading a web article in full: the main text of the page without menus, ads and comments (trafilatura), with its title, site
and date. A Google News link leads through Google's own page: the address of the article itself is asked from Google first.
The text is someone else's: the caller marks it as external data."""
import json
import os
import re
import tempfile
import threading
import time
import zipfile
from urllib.parse import quote, unquote, urlparse

from sena.core import attachments, local
from sena.world import net

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
           "Accept-Language": "en-US,en;q=0.9,ru;q=0.8"}   # news sites turn away a bare client: the page is asked for as a browser asks
MIN_TEXT = 300        # shorter is a subscription stub, a consent page or a page built by scripts, not the article
MIN_WHOLE = 80        # a page read whole (research): a status card or a price line is short and still an answer
CACHE_SECONDS = 3600
SKIP_HOURS = 6        # a site that refused us isn't asked again for this long (blocked on this network, a paywall, bot protection)
BUSY_MINUTES = 30     # a site that said "too many requests" (429) is left alone this long
# only a site's own refusal closes the whole site for a while; one missing page, a server error or no network say nothing
# about the site's other pages (crawl4ai's study: any failure, a 404 included, used to close a site for six hours)
CLOSED = {401: SKIP_HOURS * 3600, 403: SKIP_HOURS * 3600, 451: SKIP_HOURS * 3600, 429: BUSY_MINUTES * 60, 503: BUSY_MINUTES * 60}
PDF_PAGES = 40        # pages of a PDF found by a link that are read
GOOGLE = re.compile(r"^https://news\.google\.com/(?:rss/)?articles/([\w-]+)")
LEFTOVERS = re.compile(r"\b(?:share|facebook|twitter|linkedin|pinterest|copy url|e-?mail)\b", re.I)   # a page's buttons, not an article's words
_refused = {}         # host -> until when it isn't asked
_resolved = {}        # Google News id -> the article's own address
_lock = threading.Lock()


class Unreadable(net.WorldError):
    """The text can't be read: closed by a subscription, the site doesn't let us in, or the page is built by scripts."""


def host(url):
    return (urlparse(url).hostname or "").removeprefix("www.")


def readable(url):
    """False for a site that refused us recently: no point waiting for it again."""
    with _lock:
        until = _refused.get(host(url), 0)
    return time.time() >= until


def _close(url, status):
    # the site's own refusal leaves it alone for a while: half an hour after "too many requests" or "unavailable", hours after
    # "forbidden"; anything else — a timeout, no network, one missing page — closes nothing
    wait = CLOSED.get(status)
    if wait:
        with _lock:
            _refused[host(url)] = time.time() + wait


def _get(url, who, ttl=CACHE_SECONDS, **kw):
    """GET a page (a refusal closes the site: _close). ttl=0 — read it fresh (a watched page)."""
    seen = {}

    def explain(reply):
        seen["status"] = reply.status_code   # the message itself stays the usual one
    try:
        return net.get(url, headers=HEADERS, timeout=20, who=who, ttl=ttl, error=Unreadable, explain=explain, public=True, **kw)
    except Unreadable:
        _close(url, seen.get("status"))
        raise


def resolve(url):
    """A Google News link -> the address of the article itself (Google keeps it behind its own page); any other link as is."""
    found = GOOGLE.match(url or "")
    if not found:
        return url
    gid = found.group(1)
    if gid in _resolved:
        return _resolved[gid]
    if not readable(url):
        raise Unreadable("Google News просил подождать: адрес статьи спрошу позже")
    page = _get(f"https://news.google.com/articles/{gid}", "Google News").text
    sig, ts = re.search(r'data-n-a-sg="([^"]+)"', page), re.search(r'data-n-a-ts="([^"]+)"', page)
    if not sig or not ts:
        raise Unreadable("Google News не дал адрес статьи")
    ask = ["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
                          "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0], gid, int(ts.group(1)), sig.group(1)]
    form = "f.req=" + quote(json.dumps([[["Fbv4je", json.dumps(ask, separators=(",", ":")), None, "generic"]]], separators=(",", ":")))
    seen = {}
    try:
        reply = net.send("POST", "https://news.google.com/_/DotsSplashUi/data/batchexecute", form=form, timeout=15, who="Google News", error=Unreadable,
                         headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                         explain=lambda r: seen.update(status=r.status_code))
    except Unreadable:   # a blip says nothing about Google's other links: only its own refusal closes it for a while
        _close(url, seen.get("status"))
        raise
    try:
        target = json.loads(json.loads(reply.text.split("\n\n")[1])[0][2])[1]
    except (IndexError, TypeError, ValueError):
        raise Unreadable("Google News не дал адрес статьи")
    if not str(target).startswith("http"):
        raise Unreadable("Google News не дал адрес статьи")
    with _lock:
        if len(_resolved) > 5000:
            _resolved.clear()
        _resolved[gid] = target
    return target


def _type(page):
    # the reply's Content-Type as "type/subtype; charset=…", lower case; "" when there is none
    value = getattr(getattr(page, "headers", None), "get", lambda name: "")("content-type")
    return value.lower() if isinstance(value, str) else ""


def _body(page):
    # the page for trafilatura: its bytes when the server named no charset — requests would read Russian as Latin-1 then, and
    # trafilatura finds the page's own <meta charset> (Firecrawl's check: UTF-8 and cp1251 pages came out as mojibake)
    return page.content if isinstance(page.content, bytes) and "charset=" not in _type(page) else page.text


GOOGLE_DOC = re.compile(r"^https://docs\.google\.com/(document|spreadsheets|presentation)/d/([\w-]{20,})(?:/[^?#]*)?(?:[?#].*)?$")
GID = re.compile(r"[?#&]gid=(\d+)")   # the sheet a link points at, wherever among its parameters


def _export(url):
    # a public Google document, sheet or slides link reads as its export, not as the editor's page of scripts (Firecrawl
    # rewrites them the same way); a private one asks to sign in and stays unreadable, as it should
    found = GOOGLE_DOC.match(url or "")
    if not found:
        return url
    (kind, doc), gid = found.groups(), GID.search(url)
    if kind == "document":
        return f"https://docs.google.com/document/d/{doc}/export?format=txt"
    if kind == "spreadsheets":
        return f"https://docs.google.com/spreadsheets/d/{doc}/export?format=csv" + (f"&gid={gid.group(1)}" if gid else "")
    return f"https://docs.google.com/presentation/d/{doc}/export/txt"


def read(url, limit=12000, whole=False, fresh=False):
    """{"url", "title", "site", "date", "text"} of an article; Unreadable when there is no text to read. whole — research and
    long reading: tables kept, and a short card or a page of links read whole instead of refused (the question's filter drops
    the menus later). Plain text, JSON, PDF and office files behind a link are read as what they are, not taken for a page."""
    url = resolve(url)
    if not readable(url):
        raise Unreadable(f"{host(url)} недавно не отдал текст (подписка или сайт не пускает)")
    page = _get(_export(url), host(url), ttl=0 if fresh else CACHE_SECONDS)
    data = page.content if isinstance(page.content, bytes) else b""
    kind = _type(page).split(";")[0].strip()
    if data[:5] == b"%PDF-":   # a link to a paper or a report: its text, read on the Mac, not its bytes taken for a page
        return _pdf(url, data, limit)
    if data[:4] in (b"PK\x03\x04", b"\xd0\xcf\x11\xe0"):   # a Word, Excel or other office file
        return _document(url, data, limit)
    if kind in ("text/plain", "text/csv", "text/markdown") or kind.endswith("json"):
        return _plain(url, data or page.text.encode(), kind, limit)
    html = _body(page)
    import trafilatura   # a tenth of a second to load: on the first article read, not at every start of the core and the screen
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=whole, deduplicate=True) or ""
    if len(LEFTOVERS.findall(text[:400])) >= 2:   # the page's byline and share buttons came first: the fuller mode starts at the prose
        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=whole, deduplicate=True, favor_recall=True) or text
    if whole and len(text) < MIN_TEXT:   # a status page or a card is short, a page of links has no "article": read it whole
        text = max(text, trafilatura.html2txt(html) or "", key=len)
    if len(text) < (MIN_WHOLE if whole else MIN_TEXT):   # one short page says nothing about the site: it isn't marked as refusing
        raise Unreadable(f"{host(url)}: текста статьи нет (закрыт подпиской или собирается скриптами)")
    meta = trafilatura.extract_metadata(html, default_url=url)
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + " […]"
    return {"url": url, "title": getattr(meta, "title", None) or "", "site": getattr(meta, "sitename", None) or host(url),
            "date": getattr(meta, "date", None) or "", "text": text}



def _name(url, default):
    return unquote(urlparse(url).path.rsplit("/", 1)[-1]) or default


def _cut(text, limit):
    return text[:limit].rsplit(" ", 1)[0] + " […]" if len(text) > limit else text


def _plain(url, data, kind, limit):
    # plain text, CSV or JSON behind a link: as it is (an empty "article" before), JSON laid out to be read
    text = attachments.decode(data)
    if kind.endswith("json"):
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=1)
        except ValueError:
            pass
    if not text.strip():
        raise Unreadable(f"{host(url)}: файл пустой")
    return {"url": url, "title": _name(url, host(url)), "site": host(url), "date": "", "text": _cut(text, limit)}


def _document(url, data, limit):
    # an office file behind a link: read the way a file the owner sends is read (core/attachments.py)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "file.bin")
        with open(path, "wb") as f:
            f.write(data)
        suffix = ".doc"
        if data[:4] == b"PK\x03\x04":
            try:
                with zipfile.ZipFile(path) as z:
                    names = set(z.namelist())
            except zipfile.BadZipFile:
                names = set()
            suffix = ".docx" if "word/document.xml" in names else ".xlsx" if "xl/workbook.xml" in names else ""
        if not suffix:
            raise Unreadable(f"{host(url)}: архив или файл, который не читается как текст")
        os.rename(path, path + suffix)
        try:
            text = attachments.text_of(path + suffix) or ""
        except Exception as e:
            raise Unreadable(f"{host(url)}: документ не прочитался ({e})")
    if not text.strip():
        raise Unreadable(f"{host(url)}: в документе нет текста")
    return {"url": url, "title": _name(url, host(url)), "site": host(url), "date": "", "text": _cut(text, limit)}


def _pdf(url, data, limit):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "page.pdf")
        with open(path, "wb") as f:
            f.write(data)
        try:
            text = local.pdf_text(path, max_pages=PDF_PAGES, purpose="статья в PDF")
        except local.Unavailable as e:
            raise Unreadable(f"{host(url)}: PDF не прочитался ({e})")
    if len(text) < MIN_TEXT:
        raise Unreadable(f"{host(url)}: в PDF нет текста")
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1]).removesuffix(".pdf") or host(url)
    return {"url": url, "title": name, "site": host(url), "date": "", "text": text[:limit].rsplit(" ", 1)[0] + " […]" if len(text) > limit else text}
