"""Shared code for world data sources (weather, exchange rates, flights, maps, news): a request with a clear error and a cache.
We don't hit free APIs needlessly: a response can be kept for its ttl (minutes for news, a day for reference data), in a cache
bounded by count and by size."""
import ipaddress
import json
import random
import socket
import sys
import threading
import time
from urllib.parse import urljoin, urlparse

import requests

from sena.core import events

_cache = {}   # (url, params) -> (when received, its ttl, body size, response), oldest first
_lock = threading.Lock()
CACHE_MOST = 300             # responses kept at most
CACHE_BYTES = 50_000_000     # and their bodies together; a single body over a tenth of this isn't kept
ONLINE = "https://www.apple.com/library/test/success.html"   # answers whenever this Mac is on the internet


class WorldError(Exception):
    """Couldn't get world data: the source didn't respond or refused, or the request doesn't fit. The text can be shown to Tema as is."""


SECRET_KEYS = ("key", "apikey", "api_key", "token", "access_token")


def _shown(params):
    return {k: ("***" if str(k).lower() in SECRET_KEYS else v) for k, v in (params or {}).items()} or None


def why_not(e, timeout=None):
    """A failed connection in words for the owner: the name not found, the site's certificate refused, no answer in time, the
    connection refused or dropped — what to do differs (Firecrawl names each, a bare class name said nothing)."""
    text = str(e).lower()
    if INSIDE.lower() in text:
        return INSIDE + " — не открываю"
    if isinstance(e, requests.Timeout) or "timed out" in text:
        return f"не ответил{f' за {timeout} с' if timeout else ' вовремя'}"
    if isinstance(e, requests.exceptions.SSLError) or "certificate" in text or "ssl" in text:
        return "сертификат сайта не принят (защищённое соединение не установилось)"
    if "name or service not known" in text or "nodename nor servname" in text or "getaddrinfo" in text or "name resolution" in text:
        return "адрес не найден (такого сайта нет или нет интернета)"
    if "refused" in text:
        return "соединение отклонено (сайт не принимает подключения)"
    if "reset" in text or "aborted" in text or "remotedisconnected" in text:
        return "соединение оборвалось"
    return f"нет связи ({type(e).__name__})"


def unanswered(e):
    """The request's error says no answer came (time ran out, the connection broke), not a refusal: the other side may still
    have done the work, so a retry must say it is the same request."""
    return isinstance(e.__context__, requests.RequestException) and not isinstance(e.__context__, requests.exceptions.SSLError)


def _direct():
    # every request dials the site itself, never a proxy (config drops the proxy variables: direct internet access). requests
    # also reads the Mac's system proxy settings — a VPN app's 127.0.0.1:7890 — and through one a public-only fetch dials
    # inside this Mac and is refused, and the site watch tells the owner his site is down. Named per request (a fresh dict:
    # requests fills in the one it gets); a redirect requests follows itself reads the system settings again, so public-only
    # requests follow theirs here, hop by hop
    return {"http": None, "https": None, "all": None}


def send(method, url, body=None, headers=None, timeout=60, who="источник данных", error=WorldError, explain=None, redirects=True, form=None,
         public=False):
    """A request with a JSON body (POST, PUT, PATCH, DELETE) and a clear error. The body itself isn't logged: it can be personal
    (the log keeps the url, source and status). redirects=False — don't follow redirects; form — an already encoded form body instead of JSON.
    public=True — the address came from someone's text (a mail's unsubscribe link): only a public address is dialled, no redirects."""
    if public:
        why = private_host(url)
        if why:
            events.log("fetch", url, source=who, method=method, failed=True, error="private")
            raise error(f"Не открываю: {why}")
    try:
        payload = {"data": form} if form is not None else {"json": body}
        with public_only(public):
            reply = requests.request(method, url, headers=headers, timeout=timeout, allow_redirects=redirects and not public, proxies=_direct(), **payload)
    except requests.RequestException as e:
        events.log("fetch", url, source=who, method=method, failed=True, error=type(e).__name__)
        raise error(f"{who}: {why_not(e, timeout)}")
    ok = 200 <= reply.status_code < 300   # creation returns 201, deletion and some edits return 204
    events.log("fetch", url, source=who, method=method, status=reply.status_code, failed=None if ok else True)
    if not ok:
        raise error((explain(reply) if explain else None) or f"{who} ответил {reply.status_code}")
    return reply


def post(url, body, headers=None, timeout=60, who="источник данных", error=WorldError, explain=None):
    return send("POST", url, body, headers, timeout, who, error, explain)


def as_json(reply, who="источник данных", error=WorldError):
    """Parse the response's JSON; garbage instead of JSON (a placeholder, a consent page) becomes a clear error, not a crash."""
    try:
        return reply.json()
    except ValueError:
        raise error(f"{who} ответил не JSON (похоже, заглушка или сбой на их стороне)")


HOPS = 5   # redirects a public-only fetch follows, each one checked
MOST_BYTES = 25_000_000   # a page or a file read for the model: a bigger body is refused, never read whole into memory
NEVER_HOSTS = {"localhost", "metadata", "metadata.google.internal"}
_NAT64, _V4COMPAT, _6TO4 = (ipaddress.ip_network(n) for n in ("64:ff9b::/96", "::/96", "2002::/16"))


def inner(address):
    """Whether an address isn't on the public internet — itself or the IPv4 inside an IPv6 transition form (mapped, NAT64, 6to4).
    "Not global" rather than a list of private ranges: Tailscale's 100.64/10, the cloud metadata 169.254.169.254, the
    benchmark and documentation ranges are all caught (crawl4ai's egress_broker.py, Apache-2.0)."""
    try:
        ip = ipaddress.ip_address(str(address).split("%")[0])
    except ValueError:
        return True
    forms = [ip]
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            forms.append(ip.ipv4_mapped)
        elif ip in _NAT64 or ip in _V4COMPAT:
            forms.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        elif ip in _6TO4:
            forms.append(ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF))
    return any(not form.is_global for form in forms)


def private_host(url):
    """Why an address must not be fetched for the model: it points inside this Mac, the home network or the owner's private
    network (Tailscale) — a page or a mail naming such a link would make Sena read her own services. None — a public address."""
    host = urlparse(url).hostname
    if not host:
        return "в адресе нет сайта"
    if host.lower().rstrip(".") in NEVER_HOSTS:
        return f"адрес внутри этого Mac или сети ({host})"
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (socket.gaierror, UnicodeError):
        return None   # an unknown name: the request itself will fail with a clear error
    if any(inner(a) for a in addresses):
        return f"адрес внутри этого Mac или домашней сети ({host})"
    return None


INSIDE = "адрес внутри этого Mac или сети"
_pinned = threading.local()   # .on — what this thread dials now must be a public address


class Inside(ConnectionRefusedError):
    """A public-only request tried to connect inside this Mac or the home network."""


def _audit(event, args):
    # every socket a thread opens while its public-only request runs: the address actually dialled is checked — after the name
    # was resolved for the last time, so a name that answers "outside" to the check and "inside" to the dial (DNS rebinding)
    # never gets a request through (Flowise's httpSecurity resolves, checks and pins for the same reason). Inert elsewhere.
    if event == "socket.connect" and getattr(_pinned, "on", False):
        address = args[1]
        if not isinstance(address, tuple) or inner(address[0]):
            raise Inside(f"{INSIDE} ({address[0] if isinstance(address, tuple) else address})")


sys.addaudithook(_audit)


class public_only:
    """While inside: this thread connects to public addresses only (nested uses keep the outer state)."""

    def __init__(self, on=True):
        self.on = on

    def __enter__(self):
        self.was = getattr(_pinned, "on", False)
        _pinned.on = self.was or self.on

    def __exit__(self, *exc):
        _pinned.on = self.was


def _peer(reply):
    """The address the answer actually came from (urllib3 2.x, 1.x, the connection), or None when it can't be seen."""
    for get in (lambda: reply.raw._fp.fp.raw._sock, lambda: reply.raw._original_response.fp.raw._sock, lambda: reply.raw._connection.sock):
        try:
            sock = get()
            peer = sock.getpeername()[0] if sock is not None else None
        except Exception:
            continue
        if isinstance(peer, str):
            return peer
    return None


def _guard(reply, who, error):
    """The last answer of a public-only fetch, before its body is read. The name is resolved again when the request dials: a name
    with a short life can point outside when checked and inside when dialled (DNS rebinding), so the address actually connected
    to is checked, and an unseen one counts as inside. The body is read up to MOST_BYTES (crawl4ai's PDF download)."""
    peer = _peer(reply)
    if peer is None or inner(peer):
        reply.close()
        events.log("fetch", reply.url, source=who, failed=True, error="private peer" if peer else "unseen peer")
        raise error("Не открываю: ответ пришёл не с публичного адреса" if peer else "Не открываю: не видно, с какого адреса пришёл ответ")
    body = reply.raw.read(MOST_BYTES + 1, decode_content=True)
    reply.close()
    if len(body) > MOST_BYTES:
        raise error(f"{who}: ответ больше {MOST_BYTES // 1_000_000} МБ, не читаю")
    reply._content = body   # what .text and .content give from now on
    return reply


PERSONAL = ("authorization", "proxy-authorization", "cookie")   # headers meant for one site only


def _public_get(url, params, headers, timeout, who, error, follow=True):
    # every hop of the redirect chain is checked, not only the first address (pydantic-ai's SSRF guard); every connection by the
    # address it dials (_audit), and the last answer by the address it came from, too. follow=False — the redirect is the answer
    with public_only():
        for _ in range(HOPS + 1):
            why = private_host(url)
            if why:
                events.log("fetch", url, source=who, failed=True, error="private")
                raise error(f"Не открываю: {why}")
            reply = requests.get(url, params=params, headers=headers, timeout=timeout, allow_redirects=False, stream=True, proxies=_direct())
            if getattr(reply, "is_redirect", False) is not True or not follow:
                return _guard(reply, who, error)
            reply.close()
            target = urljoin(url, reply.headers.get("location", ""))
            if urlparse(target).hostname != urlparse(url).hostname:   # a key or a cookie doesn't follow to another site (requests does the same)
                headers = {k: v for k, v in (headers or {}).items() if k.lower() not in PERSONAL}
            url, params = target, None
    raise error(f"{who}: слишком много переадресаций")


RETRY_PAUSE = 1.0   # seconds (±25 %) before the one more try of a GET
RETRY_STATUSES = (502, 503, 504)
TRANSIENT = ("reset", "aborted", "remotedisconnected", "connection broken", "temporary failure in name resolution")


def _again(reply=None, failure=None):
    """Seconds to wait before one more try of a GET, or None: a connection dropped on the way, a gateway busy for a moment, a 429
    that asks for a short wait itself. Never a timeout (the wait was long already) and never a refusal by name or certificate:
    those don't change in a second (LangChain retries idempotent calls only; a POST is never sent twice)."""
    pause = RETRY_PAUSE * random.uniform(0.75, 1.25)
    if failure is not None:
        quick = isinstance(failure, requests.ConnectionError) and not isinstance(failure, (requests.exceptions.SSLError, requests.Timeout))
        return pause if quick and any(w in str(failure).lower() for w in TRANSIENT) else None
    if reply.status_code in RETRY_STATUSES:
        return pause
    wait = str((getattr(reply, "headers", None) or {}).get("retry-after") or "").strip()
    return float(wait) if reply.status_code == 429 and wait.isdigit() and int(wait) <= 5 else None


def get(url, params=None, headers=None, timeout=20, who="источник данных", error=WorldError, ttl=0, explain=None, redirects=True, public=False):
    """GET with a clear error. error — which exception to raise, ttl — how many seconds to remember the response,
    explain(reply) — a custom error message for a non-200 status (if it returns nothing, the message is "<who> responded <code>").
    redirects=False — don't follow redirects (requests carrying an access key: the key must not leak to a foreign address).
    public=True — the address came from the model or someone's text: only public sites, through every redirect (none with
    redirects=False; a key or a cookie never follows to another site)."""
    key = (url, json.dumps(params, sort_keys=True, default=str)) if ttl else None
    if key:
        with _lock:
            hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            events.log("fetch", url, source=who, params=_shown(params), cached=True)
            return hit[3]
    for attempt in (0, 1):
        try:
            if public:
                reply = _public_get(url, params, headers, timeout, who, error, follow=redirects)
            else:
                reply = requests.get(url, params=params, headers=headers, timeout=timeout, proxies=_direct(), **({} if redirects else {"allow_redirects": False}))
        except requests.RequestException as e:
            wait = _again(failure=e) if not attempt else None
            events.log("fetch", url, source=who, params=_shown(params), failed=True, error=type(e).__name__, retry=True if wait is not None else None)
            if wait is None:
                raise error(f"{who}: {why_not(e, timeout)}")
            time.sleep(wait)
            continue
        wait = _again(reply) if not attempt and reply.status_code != 200 else None
        events.log("fetch", url, source=who, params=_shown(params), status=reply.status_code, failed=reply.status_code != 200 or None,
                   retry=True if wait is not None else None)
        if wait is None:
            break
        time.sleep(wait)
    if reply.status_code != 200:
        raise error((explain(reply) if explain else None) or f"{who} ответил {reply.status_code}")
    if key:
        _keep(key, reply, ttl)
    return reply


def _keep(key, reply, ttl):
    """Remember a response: first out go the ones past their own ttl, then the oldest while there are more than CACHE_MOST or
    their bodies pass CACHE_BYTES (a long ttl of one caller no longer outlives the cap, a short one no longer empties the rest)."""
    try:
        size = len(reply.content or b"")
    except (AttributeError, TypeError):
        size = 0
    if size > CACHE_BYTES // 10:
        return
    now = time.time()
    with _lock:
        for old in [k for k, (t, keep, _, _) in _cache.items() if now - t >= keep]:
            del _cache[old]
        _cache.pop(key, None)
        _cache[key] = (now, ttl, size, reply)
        total = sum(entry[2] for entry in _cache.values())
        while len(_cache) > CACHE_MOST or total > CACHE_BYTES:
            total -= _cache.pop(next(iter(_cache)))[2]


def probe(url, headers=None, timeout=15, who="проверка адреса", public=False):
    """A single request that never raises on a bad response (checking whether an address is alive): (response or None, seconds, failure reason in words).
    Doesn't follow redirects: the caller checking the address looks at those itself. Only the status and headers are read, never
    the body. public=True — the address came from someone's text: what is dialled must be a public address (DNS rebinding)."""
    started = time.time()
    try:
        with public_only(public):
            reply = requests.get(url, headers=headers, timeout=timeout, allow_redirects=False, stream=True, proxies=_direct())
        reply.close()
    except requests.RequestException as e:
        events.log("fetch", url, source=who, failed=True, error=type(e).__name__)
        return None, time.time() - started, why_not(e, timeout)
    events.log("fetch", url, source=who, status=reply.status_code, failed=reply.status_code >= 400 or None)
    return reply, time.time() - started, ""
