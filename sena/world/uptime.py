"""Whether a site and the services Sena depends on are alive. check_website: does the address open, how long it takes, where redirects lead, and when the certificate expires.
service_status: status pages for Anthropic, OpenAI, GitHub, Cloudflare, Vercel (Statuspage JSON, no key needed). watch: a background check that notifies Tema
if a site goes down, comes back, or its certificate is about to expire. outage_note: a note appended to "failed to respond", saying what the model's status page reports.
Only public addresses are checked: Sena never probes the local network or internal IP addresses."""
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import certifi

from sena import config
from sena.core import events, safety, store
from sena.core.tools import string, tool
from sena.world import net

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SenaMonitor/1.0)"}
SERVICES = {"anthropic": ("Anthropic (Claude)", "https://status.claude.com"), "openai": ("OpenAI", "https://status.openai.com"),
            "github": ("GitHub", "https://www.githubstatus.com"), "cloudflare": ("Cloudflare", "https://www.cloudflarestatus.com"),
            "vercel": ("Vercel", "https://www.vercel-status.com")}
LEVELS = {"none": "всё работает", "minor": "небольшие сбои", "major": "серьёзные сбои", "critical": "критический сбой", "maintenance": "плановые работы"}
PARTS = {"degraded_performance": "тормозит", "partial_outage": "частичный сбой", "major_outage": "не работает", "under_maintenance": "обслуживание"}
HOPS = 5          # redirects to follow
CERT_WARN = 14    # how many days before certificate expiry to notify Tema
DOWN_AFTER = 2    # consecutive failed checks after which a site counts as down (a single network hiccup doesn't raise an alarm)
DAY = 86400       # seconds in a day: the certificate check and reminder follow this rhythm
STATE = config.SITE_WATCH_FILE


class UptimeError(net.WorldError):
    pass


def _public(url):
    """The address must point to the internet: otherwise Sena could be used to probe the home network and services on this Mac.
    The rule is net's, for every address from someone's text; the dial itself is checked again (net.probe(public=True))."""
    why = net.private_host(url)
    if why:
        raise UptimeError(f"{why}: такие не проверяю, только публичные сайты")


def _normalize(url):
    url = str(url or config.SITE_PUBLIC or "").strip()
    if not url:
        raise UptimeError("Какой сайт проверить? Адрес вида example.com")
    url = url if "://" in url else "https://" + url
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UptimeError("Нужен адрес сайта: https://example.com")
    return url


def _open(url):
    """Follow redirects manually, checking each address. Returns (chain [(address, code)], seconds, failure reason)."""
    chain, started = [], time.time()
    for _ in range(HOPS + 1):
        _public(url)
        reply, _, why = net.probe(url, HEADERS, who="проверка сайта", public=True)
        if reply is None:
            return chain, time.time() - started, why
        chain.append((url, reply.status_code))
        location = reply.headers.get("Location")
        if reply.status_code in (301, 302, 303, 307, 308) and location:
            url = urljoin(url, location)
            if urlparse(url).scheme not in ("http", "https"):
                return chain, time.time() - started, "перенаправление на странный адрес"
            continue
        return chain, time.time() - started, ""
    return chain, time.time() - started, f"больше {HOPS} перенаправлений подряд"


def _cert(host, port=443):
    """(days until expiry, issuer) or (None, reason) if the certificate failed verification or there's no connection."""
    try:
        context = ssl.create_default_context(cafile=certifi.where())   # the same trusted CAs requests uses: Python on Mac doesn't know its own
        with net.public_only(), socket.create_connection((host, port), timeout=10) as raw, context.wrap_socket(raw, server_hostname=host) as tls:
            cert = tls.getpeercert()   # the name is resolved once more here: the address dialled is checked (net's audit hook)
    except ssl.SSLCertVerificationError as e:
        events.log("fetch", f"tls://{host}", source="проверка сертификата", failed=True, error="SSLCertVerificationError")
        return None, f"сертификат не прошёл проверку ({e.verify_message})"
    except net.Inside:
        events.log("fetch", f"tls://{host}", source="проверка сертификата", failed=True, error="private")
        return None, f"{net.INSIDE} — не проверяю"
    except (OSError, ssl.SSLError) as e:
        events.log("fetch", f"tls://{host}", source="проверка сертификата", failed=True, error=type(e).__name__)
        return None, f"не удалось связаться по HTTPS ({type(e).__name__})"
    events.log("fetch", f"tls://{host}", source="проверка сертификата", status=200)
    left = (ssl.cert_time_to_seconds(cert["notAfter"]) - time.time()) / 86400
    issuer = dict(x[0] for x in cert.get("issuer", ())).get("organizationName", "")
    return left, issuer


def check_website(url=""):
    """Check a site: does it open, the response code and time, where redirects lead, how many days its certificate is valid for."""
    # the addresses it led to and the certificate's issuer come from the site itself: someone else's text (Microsoft Agent
    # Framework treats every tool's output as untrusted unless declared otherwise)
    return safety.external(site_report(url), f"проверка сайта {urlparse(_normalize(url)).hostname}")


def site_report(url=""):
    """The site check as plain lines, for Sena's own code (the system check reads its verdict)."""
    url = _normalize(url)
    host = urlparse(url).hostname
    chain, seconds, why = _open(url)
    if why or not chain:
        verdict = f"НЕ открывается: {why or 'нет ответа'}"
    else:
        code = chain[-1][1]
        verdict = f"открывается, код {code}, ответ за {seconds:.1f} с".replace(".", ",") if code < 400 else f"отвечает ошибкой {code} (за {seconds:.1f} с)".replace(".", ",")
    lines = [f"{host}: {verdict}"]
    if len(chain) > 1:
        lines.append("Перенаправления: " + " → ".join(f"{u} ({c})" for u, c in chain))
    last = urlparse(chain[-1][0] if chain else url)   # the certificate is checked on the address we ended up at (http often leads to https)
    if last.scheme == "https":
        left, note = _cert(last.hostname)
        if left is None:
            lines.append(f"Сертификат: {note}")
        else:
            lines.append(f"Сертификат: действует ещё {int(left)} дн." + (" (скоро истечёт)" if left <= CERT_WARN else "") + (f", выдан {note}" if note else ""))
    return "\n".join(lines)


# ---------- service status pages ----------
def _status(key, timeout=10):
    name, base = SERVICES[key]
    data = net.as_json(net.get(base + "/api/v2/summary.json", None, HEADERS, timeout=timeout, ttl=60, who=f"статус {name}", error=UptimeError), f"статус {name}", UptimeError)
    level = (data.get("status") or {}).get("indicator", "none")
    bad = [c for c in data.get("components", []) if c.get("status") not in ("operational", None) and not c.get("group")]
    incidents = [i for i in data.get("incidents", []) if i.get("status") != "resolved"]
    return {"name": name, "level": level, "bad": bad, "incidents": incidents}


def _line(info):
    text = f"{info['name']}: {LEVELS.get(info['level'], info['level'])}"
    bad = info["bad"]
    if bad:
        text += f" ({'; '.join(f'{c['name']}: {PARTS.get(c['status'], c['status'])}' for c in bad[:4])}" + (f"; всего с проблемами {len(bad)}" if len(bad) > 4 else "") + ")"
    if info["incidents"]:
        text += ". Происшествия: " + "; ".join(f"{i.get('name')} ({i.get('status')})" for i in info["incidents"][:3])
    return text


def service_status(service=""):
    """Are Anthropic (Claude), OpenAI, GitHub, Cloudflare, Vercel up, according to their status pages. Without a name — all of them."""
    keys = [str(service).strip().lower()] if str(service or "").strip() else list(SERVICES)
    unknown = [k for k in keys if k not in SERVICES]
    if unknown:
        raise UptimeError(f"Такого сервиса нет в списке: {unknown[0]}. Есть: {', '.join(SERVICES)}")

    def one(key):
        try:
            return _line(_status(key))
        except UptimeError as e:
            return f"{SERVICES[key][0]}: страница статуса не ответила ({e})"
    with ThreadPoolExecutor(max_workers=len(keys)) as pool:
        return safety.external("\n".join(pool.map(one, keys)), "страницы статуса сервисов")   # incident titles are their words


def outage_note(error):
    """A note appended to "failed to respond": what the status page of the model that failed is saying. Never raises, and waits no more than five seconds."""
    try:
        module, name = type(error).__module__, type(error).__name__
        key = "anthropic" if module.startswith("anthropic") or name == "ClaudeError" else "openai" if module.startswith("openai") else ""
        if not key:
            return ""
        info = _status(key, timeout=5)
        if info["level"] == "none" and not info["incidents"]:
            return f" По странице статуса у {info['name']} сбоев нет: дело не в них."
        return f" Страница статуса: {_line(info)}."
    except Exception:
        return ""


# ---------- background check ----------
def watch(notify, state_file=None):
    """Called repeatedly from the scheduler: does the site open? is the certificate not about to expire? Notifies Tema only when
    something has changed. Never raises: the watches started after it in the same round still start."""
    try:
        _watch(notify, state_file)
    except Exception as e:
        events.error("Слежка за сайтом", e)


def _watch(notify, state_file):
    url = str(config.SITE_PUBLIC or "").strip()
    if not url:
        return
    path = state_file or STATE
    state, now, host = store.load_json(path, {}), time.time(), urlparse(_normalize(url)).hostname
    try:
        chain, _, why = _open(_normalize(url))
    except UptimeError as e:
        chain, why = [], str(e)
    alive = bool(chain) and not why and chain[-1][1] < 400
    if not chain and net.probe(net.ONLINE, who="проверка сайта: есть ли интернет")[0] is None:
        return   # the Mac itself is offline (a plane, a café): that says nothing about the site
    if alive:
        if state.get("down_since"):
            minutes = int((now - state["down_since"]) / 60)
            text = f"Сайт {host} снова открывается (не работал около {minutes} мин)."
            notify(text)
            events.log("watch", text)
        state["fails"], state["down_since"] = 0, None
    else:
        state["fails"] = state.get("fails", 0) + 1
        reason = why or f"отвечает ошибкой {chain[-1][1]}"
        if state["fails"] >= DOWN_AFTER and not state.get("down_since"):
            state["down_since"] = now
            text = f"Сайт {host} не открывается: {reason}. Слежу, напишу, когда вернётся."
            notify(text)
            events.log("watch", text, error=True)
    if now - state.get("cert_checked", 0) > DAY:   # the certificate is checked once a day
        state["cert_checked"] = now
        left, note = _cert(host)
        problem = f"Сертификат сайта {host}: {note}." if left is None else f"Сертификат сайта {host} истекает через {int(left)} дн." if left <= CERT_WARN else ""
        if problem and now - state.get("cert_alert", 0) > 3 * DAY:   # don't remind more often than once every three days
            state["cert_alert"] = now
            notify(problem)
            events.log("watch", problem, error=left is None)
    store.save_json(path, state, indent=1)


TOOLS = [
    tool("check_website", "Проверить сайт: открывается ли, код и время ответа, перенаправления, сколько дней действует сертификат HTTPS. Без адреса проверяет сайт-портфолио Темы. Только публичные сайты.",
         {"url": string("Адрес: example.com; без него сайт-портфолио")}),
    tool("service_status", "Работают ли сервисы, от которых зависит Сена и Тема, по их страницам статуса: anthropic (Claude), openai, github, cloudflare, vercel. Без названия — все сразу. "
                           "Зови, когда что-то не отвечает или тормозит и непонятно, чья это вина.",
         {"service": string("anthropic, openai, github, cloudflare или vercel; пусто — все")}),
]
FUNCTIONS = {"check_website": check_website, "service_status": service_status}
