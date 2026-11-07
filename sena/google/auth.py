"""Google login (OAuth for a desktop app) and token-authenticated requests.
Uses only requests: the large Google libraries aren't needed. The token lives in data/state/google_token.json."""
import base64
import hashlib
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from sena import config
from sena.core import events, status, store
from sena.core.tools import Failure, tool
from sena.world import net

# what's allowed: mail (read, modify, send), Sheets (read and write), Drive (read-only), YouTube (own channel: videos, comments) and its analytics
YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.settings.basic",   # filters, so new mail sorts itself (asked at the next sign-in)
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
    YOUTUBE_SCOPE,
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
HOW_TO_SIGN_IN = ("Вход в Google нужно обновить: вызови google_signin — на Маке откроется окно входа, Тема нажмёт «Разрешить» "
                  "(вручную то же самое: python -m sena.google)")
_lock = threading.Lock()
_signing = threading.Event()   # the sign-in window is open and waits for "Allow"


class NotSignedIn(Exception):
    pass


class GoogleError(Exception):
    pass


def _client():
    data = store.load_json(config.GOOGLE_CLIENT_FILE, None)
    if not data:
        raise NotSignedIn(f"Нет файла OAuth-клиента {config.GOOGLE_CLIENT_FILE.name} в data/state/")
    return data["installed"]


def _token():
    return store.load_json(config.GOOGLE_TOKEN_FILE, None)


def _save_token(token):
    store.save_json(config.GOOGLE_TOKEN_FILE, token, indent=1)   # file locked down to the owner only: it holds the key to the mailbox


def state():
    """'ok' — logged in, 'none' — never logged in, 'expired' — token revoked or stale."""
    token = _token()
    if not token or not token.get("refresh_token"):
        return "none"
    return "expired" if token.get("expired") else "ok"


def mark_notified():
    """The owner should be told about the stale login once: True means this is the first time."""
    with _lock:
        token = _token()
        if not token or token.get("notified"):
            return False
        _save_token({**token, "notified": True})
        return True


def login(timeout=300, open_browser=True):
    """One-time: a Google window, "Allow", the token gets saved. Returns the list of granted scopes."""
    client = _client()
    verifier = secrets.token_urlsafe(64)   # PKCE: the code can't be intercepted and reused
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state_value = secrets.token_urlsafe(16)
    got = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            if "code" in query and query.get("state", [""])[0] == state_value:
                got["code"] = query["code"][0]
            elif "error" in query:
                got["error"] = query["error"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in got   # the sign-in is opened from the chat as often as from the terminal: the page names neither
            self.wfile.write(("<h3>Готово. Это окно можно закрыть.</h3>" if ok else
                              "<h3>Вход не удался. Окно можно закрыть и попробовать ещё раз: скажи Сене «войди в Google».</h3>").encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = client["auth_uri"] + "?" + urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent", "code_challenge": challenge, "code_challenge_method": "S256",
        "state": state_value,
    })
    print("Открываю окно входа Google. Если оно не открылось, перейди по адресу:\n" + url)
    if open_browser:
        webbrowser.open(url)
    deadline = time.time() + timeout
    server.timeout = 2
    while not got and time.time() < deadline:
        server.handle_request()
    server.server_close()
    if "code" not in got:
        raise GoogleError("Вход не завершён: " + got.get("error", "время вышло"))
    r = requests.post(client["token_uri"], timeout=30, data={
        "code": got["code"], "client_id": client["client_id"], "client_secret": client["client_secret"],
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    if r.status_code != 200:
        raise GoogleError(f"Google не выдал токен: {r.text[:300]}")
    data = r.json()
    if not data.get("refresh_token"):
        raise GoogleError("Google не выдал долгий токен. Отзови доступ приложения Sena в настройках аккаунта Google и войди заново.")
    _keep_login(data)
    events.log("google", "вход в Google выполнен", scopes=data.get("scope", "").split())
    return data.get("scope", "").split()


def _keep_login(data):
    # under the refresh's lock: a refresh running at this moment holds the old token and, told invalid_grant, would write it
    # back as expired over the login just made
    with _lock:
        _save_token({"refresh_token": data["refresh_token"], "access_token": data["access_token"],
                     "expires_at": time.time() + data["expires_in"] - 60, "scope": data.get("scope", "")})   # no expired or notified: the login is fresh


def access_token(force=False):
    with _lock:
        token = _token()
        if not token or not token.get("refresh_token"):
            raise NotSignedIn(HOW_TO_SIGN_IN)
        if token.get("expired"):
            raise NotSignedIn("Вход в Google отозван или устарел. " + HOW_TO_SIGN_IN)
        if not force and token["expires_at"] > time.time():
            return token["access_token"]
        client = _client()
        try:
            r = requests.post(client["token_uri"], timeout=30, data={
                "client_id": client["client_id"], "client_secret": client["client_secret"],
                "refresh_token": token["refresh_token"], "grant_type": "refresh_token"})
        except requests.RequestException as e:
            raise GoogleError(f"Google: токен не обновился — {net.why_not(e, 30)}")
        if r.status_code != 200:
            try:
                error = r.json().get("error")
            except (ValueError, AttributeError):   # a proxy's or a gateway's page instead of Google's JSON
                error = None
            if error == "invalid_grant":
                _save_token({**token, "expired": True})
                events.log("google", "вход в Google отозван или устарел", failed=True)
                raise NotSignedIn("Вход в Google отозван или устарел. " + HOW_TO_SIGN_IN)
            raise GoogleError(f"Не удалось обновить токен Google: {r.text[:300]}")
        data = r.json()
        _save_token({**token, "access_token": data["access_token"], "expires_at": time.time() + data["expires_in"] - 60})
        events.log("google", "токен Google обновлён")
        return data["access_token"]


RETRY_WAITS = (1, 2, 4)   # s: a passing "too many requests" or server error from Google is asked again — only a request safe to repeat


def _retry_after(r):
    try:
        return float((getattr(r, "headers", None) or {}).get("retry-after") or 0) or None
    except (AttributeError, TypeError, ValueError):
        return None


def _request(method, url, timeout, idempotent=None, **kwargs):
    # the token may have expired early: on a 401 we refresh it and retry the request once. A 429, a 5xx or a dropped connection
    # is asked again after a pause when repeating changes nothing (reading; marking or trashing the same mail) — never a send
    # or a filter being created (Dify and n8n retry only what is safe to repeat)
    headers = kwargs.pop("headers", {})
    again = (method == "GET") if idempotent is None else idempotent
    for wait in (*(RETRY_WAITS if again else ()), None):
        try:
            for attempt in (1, 2):
                r = requests.request(method, url, headers={"Authorization": f"Bearer {access_token(force=attempt == 2)}", **headers},
                                     timeout=timeout, **kwargs)
                if r.status_code != 401:
                    break
        except (requests.ConnectionError, requests.Timeout) as e:
            if wait is None:   # the callers count a GoogleError (a bulk action says "N of M"): a raw one would slip past them
                events.log("fetch", url, source="Google", method=method, failed=True, error=type(e).__name__)
                raise GoogleError(f"Google: {net.why_not(e, timeout)}")
            time.sleep(wait)
            continue
        if wait is None or not (r.status_code == 429 or r.status_code >= 500):
            break
        time.sleep(min(_retry_after(r) or wait, 30))
    events.log("fetch", url, source="Google", method=method, status=r.status_code, failed=r.status_code >= 400 or None)
    if r.status_code >= 400:
        try:
            message = r.json()["error"]["message"]
        except Exception:
            message = r.text[:300]
        raise GoogleError(f"Google {r.status_code}: {message}")
    return r


def call(method, url, idempotent=None, **kwargs):
    """A token-authenticated request to the Google API. Returns JSON (or {} for an empty response); an API error raises GoogleError.
    idempotent — the request is safe to repeat (a GET is, unless said otherwise): a passing failure is asked again."""
    r = _request(method, url, 60, idempotent, **kwargs)
    return r.json() if r.content else {}


def call_raw(method, url, **kwargs):
    """Same, but returns the response bytes (for downloading files)."""
    return _request(method, url, 120, **kwargs).content


def missing_scopes():
    """The rights in SCOPES the saved login didn't grant: a login made before a right was added (mail filters, YouTube) lacks it,
    and only a new sign-in asks for it."""
    granted = set(str((_token() or {}).get("scope") or "").split())
    return [s for s in SCOPES if s not in granted]


def sign_in():
    """Sena's tool: the Google sign-in window opens on the Mac, the owner only presses "Allow" — she restores mail, Sheets
    and Drive herself instead of sending him to the terminal. The consent itself is always his, and the window opens only in
    a turn with his own words (not from a scheduled job at night). A login that lacks a right asked for later counts as one to
    renew: "the login is there" would send the owner round in a circle."""
    missing = missing_scopes()
    if state() == "ok" and not missing:
        return "Вход в Google есть, почта, Таблицы и Диск работают."
    if _signing.is_set():
        return "Окно входа Google уже открыто на Маке: нажми в нём «Разрешить»."
    try:
        _client()
    except NotSignedIn as e:
        return f"{e}. Его скачивают один раз в Google Cloud Console (OAuth client, Desktop app)."
    if not getattr(status.local, "said", None):   # a scheduled job, a long work's turn, a voice that isn't his: nobody asked at the Mac,
        # and a window popping up while he's away would hold the consent page on his screen
        return Failure("Окно входа Google открываю только по просьбе самого Темы, а в этом ходе его слов нет. Скажи ему, что нужен вход "
                       "в Google: пусть напишет «войди в Google».")

    def run():
        _signing.set()
        try:
            scopes = login()
            events.log("google", "вход в Google обновлён", scopes=len(scopes))
        except Exception as e:
            events.error("Вход в Google", e)
        finally:
            _signing.clear()
    threading.Thread(target=run, daemon=True).start()
    if state() == "ok":
        return ("Открыла на Маке окно входа Google: у нынешнего входа нет части прав, которые добавились позже "
                f"({', '.join(s.rsplit('/', 1)[-1] for s in missing)}). Нажми в окне «Разрешить» — и они заработают.")
    return "Открыла на Маке окно входа Google: нажми в нём «Разрешить» — почта, Таблицы и Диск снова заработают."


TOOLS = [tool("google_signin", "Открыть на Маке окно входа в Google, когда вход устарел, его нет или у него нет нужного права (почта, Таблицы, Диск "
                               "или YouTube ответили, что нужно войти или не хватает прав). Тема нажмёт в окне «Разрешить» — больше ему ничего делать "
                               "не нужно, не отправляй его в Терминал.")]
FUNCTIONS = {"google_signin": sign_in}
