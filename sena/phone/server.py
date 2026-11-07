"""Server for the Android app and the Mac app: the API (/desk/api/... and the few direct paths below), accepts tasks and voice.
The password (APP_TOKEN) is required on every path; until it's set in .env, the server lets no one in."""
import hmac
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from sena import config
from sena.agents.ag_sena import agent as ag_sena
from sena.core import attachments, events, llm, moment, power, roles, safety, settings, status
from sena.google import media as gemini
from sena.memory import facts, people, quality
from sena.phone import data, desk
from sena.phone.api import ApiError, field, number
from sena.world import uptime

DESK_PREFIX = "/desk/api/"   # everything else the apps read and do goes through desk.get/desk.act
CORE_ONLY = {"/ask": "отвечать", "/voice": "распознавать голос", "/speak": "говорить"}   # what only Sena at work does: with her off (the screen) or stopping it is refused
tasks = {}  # app task id -> {"steps": [...], "answer": ..., "done": ..., "thread", "tid", "failed": only if it crashed}
_tasks_lock = threading.Lock()
MAX_FINISHED = 20   # how many finished tasks we keep in memory
MAX_BODY = 50 * 1024 * 1024   # audio from the phone can be large, but not gigabytes
SIZE_ONLY = {"/voice", "/memory", "/speak"}   # only the size of these requests' bodies goes into the event log: they're personal (speech, memory)
POSTS = {"/ask", "/memory", "/voice", "/speak", "/desk/upload"}   # the direct paths an app posts to (the rest is desk actions)
DENIED_EVERY = 60   # access denials are logged no more than once a minute per address: password guessing won't flood the log
_denied = {}   # address -> (when last logged, how many denials were skipped)
_denied_lock = threading.Lock()
# password guessing: LOCK_AFTER wrong passwords from one address within LOCK_WINDOW seconds lock that address out for LOCK_FOR seconds, its password isn't checked.
# The Mac itself never gets locked out: the app polls the server every few seconds, and a forgotten old password in it would lock itself out.
LOCK_AFTER, LOCK_WINDOW, LOCK_FOR = 10, 600, 900
LOCKED = "адрес закрыт за подбор пароля"   # the one refusal the apps are told as it is: any other is "нет доступа"
LOOPBACK = ("127.0.0.1", "::1")
_wrong = {}          # address -> times of wrong passwords
_locked_until = {}   # address -> until when it's locked


def is_locked(ip):
    if ip in LOOPBACK:
        return False
    with _denied_lock:
        return _locked_until.get(ip, 0) > time.time()


def wrong_password(ip):
    """Record a wrong password; on the LOCK_AFTER-th one, the address gets locked out."""
    if ip in LOOPBACK:
        return
    now = time.time()
    with _denied_lock:
        if len(_wrong) > 500:
            _wrong.clear()
        for old in [a for a, until in _locked_until.items() if until <= now]:
            del _locked_until[old]
        recent = [t for t in _wrong.get(ip, []) if now - t < LOCK_WINDOW] + [now]
        _wrong[ip] = recent
        locked = len(recent) >= LOCK_AFTER
        if locked:
            _locked_until[ip] = now + LOCK_FOR
            del _wrong[ip]
    if locked:
        events.log("app", "адрес закрыт за подбор пароля", source="сеть", ip=ip, seconds=LOCK_FOR, failed=True)


def given(headers, query):
    """The password a request carries: the Authorization header ("Bearer …" — it never lands in an address, a browser's
    history or a log on the way) or, as the apps send it today, the t parameter."""
    scheme, _, value = (headers.get("Authorization") or "").partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return query.get("t", "")


def access(ip, given, zone=""):
    """None — let in; otherwise why not, in words. The same door for the app API and the live voice stream (voice.py):
    the password (APP_TOKEN) compared without a timing leak, an address that guesses passwords is locked out for a while."""
    if not config.APP_TOKEN:   # no password set: an empty password mustn't match an empty request
        return "пароль APP_TOKEN не задан в .env"
    if is_locked(ip):   # a locked-out address's password isn't checked: even a correct one won't help until time passes
        return LOCKED
    if hmac.compare_digest(given.encode(), config.APP_TOKEN.encode()):
        if moment.set_owner_offset(zone, phone=ip not in LOOPBACK):   # the phone and the Mac app say which time zone the owner is in; the phone travels with him
            events.log("app", f"часовой пояс владельца: {moment.owner_offset}", source="сеть")
        return None
    if given:   # only a password that was given and is wrong counts as guessing; a request with no password isn't an attempt
        wrong_password(ip)
    return "неверный пароль" if given else "нет пароля"


def denied(ip, path, reason):
    """Log a refusal: the address and the reason, never the password; no more than once a minute per address."""
    now = time.time()
    with _denied_lock:
        last, skipped = _denied.get(ip, (0, 0))
        if now - last < DENIED_EVERY:
            _denied[ip] = (last, skipped + 1)
            return
        if len(_denied) > 500:
            _denied.clear()
        _denied[ip] = (now, 0)
    events.log("app", "доступ отклонён", source="сеть", ip=ip, path=path, reason=reason, skipped=skipped or None, failed=True)


def make_server(wait=0, host="0.0.0.0"):
    """The server on the app's port. Won't start without a password; a taken port gets a clear message. wait — seconds to keep
    trying while the port is still held: the screen hands it over to a starting core (desk core_start) and exits a moment later.
    host — every interface (the phone comes over the network); a test listens on this Mac only."""
    config.check_token()
    deadline = time.time() + wait
    while True:
        try:
            return ThreadingHTTPServer((host, config.APP_PORT), Handler)
        except OSError as e:   # the port is taken: Sena is already running, or a separate screen is up (run_app_local)
            if time.time() >= deadline:
                raise SystemExit(f"Порт {config.APP_PORT} занят ({e}): Сена уже запущена или поднят отдельный экран. Останови их и запусти снова.")
            time.sleep(0.5)


def serve(httpd):
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"Сервер приложения: порт {config.APP_PORT}")


def run_task(task_id, text, files=(), agent=None):
    task = tasks[task_id]
    task["thread"] = str(threading.get_ident())   # the app's Stop names this very task (desk stop_task)
    status.local.show = task["steps"].append  # task statuses go into its list of steps
    try:
        parts, foreign = [], []
        for f in files:   # attachments from the remote: a note in the chat history, the content goes to the model
            text = attachments.note(f) + ("\n" + text if text else "")
            parts += attachments.parts(f)
            foreign.append(f"файл «{attachments.shown_name(f)}»")   # what a file says is someone else's text: the task is marked once it begins
        events.log("message_in", text, source="app", files=len(files) or None, agent=agent)
        if agent and agent != "ag_sena":   # straight to the agent, bypassing Sena (it sees attachments as path notes); a file marks it too
            task["answer"] = ag_sena.ask_agent(config.OWNER_ID, agent, text, foreign=foreign)
        else:
            task["answer"] = ag_sena.ask(config.OWNER_ID, text, attachments=parts or None, foreign=foreign)
    except Exception as e:
        task["answer"] = f"Что-то сломалось, пока отвечала: {llm.explain(e)}.{uptime.outage_note(e)} Повтори, пожалуйста; подробности в журнале событий."
        task["failed"] = True   # this answer isn't in the chat history (Sena herself didn't record it): the app will show it separately
        events.error("Задача из приложения", e)
    finally:   # a task always ends: otherwise the app's chat would wait for an answer forever
        if task["answer"] is None:
            task["answer"] = "Задача прервана, ответа нет. Подробности в журнале событий."
            task["failed"] = True
        events.log("message_out", task["answer"], channel="app")
        task["done"] = True


def task_view(task_id):
    """An app task as the app polls it — steps, answer, done — with its thread and its id in the journal (tid, once the task
    has begun): the app's Stop names exactly this task."""
    with _tasks_lock:
        task = tasks.get(task_id)
        if task is None:
            return {"error": "нет задачи"}
        # only while it runs is its thread its own: a finished task's thread number may be another task's by now
        live = None if task.get("done") or task.get("tid") else status.tasks.get(int(task.get("thread") or 0))
        if live:
            task["tid"] = live["id"]
        return {**task, "steps": list(task["steps"])}   # a copy: the task's thread goes on adding to it while this is sent


class Handler(BaseHTTPRequestHandler):
    timeout = 60   # a client that goes silent mid-request doesn't hold the thread forever

    def log_message(self, *args):
        pass  # don't clutter the log with every request

    def send(self, body, content_type="application/json", code=200, filename=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if filename:  # so the browser downloads the file with a proper name
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def deny(self, reason):
        denied(self.client_address[0], self.safe_path(), reason)
        self.send({"error": LOCKED if reason == LOCKED else "нет доступа"}, code=403)   # a lockout is told as such: the app says to wait, not to retype

    def allowed(self):
        reason = access(self.client_address[0], given(self.headers, self.parse()[1]), self.headers.get("X-Sena-Zone", ""))
        if reason:
            self.deny(reason)
            return False
        return True

    def body(self):
        if not hasattr(self, "_body"):   # the body is read from the socket once
            try:
                size = int(self.headers.get("Content-Length", 0))
            except ValueError:
                raise ApiError(400, "Длина запроса указана неверно")
            if size < 0:
                raise ApiError(400, "Длина запроса указана неверно")
            if size > MAX_BODY:
                raise ApiError(413, "Слишком большое тело запроса")
            self._body = self.rfile.read(size)
        return self._body

    def json_body(self, kind=dict):
        """The request body as JSON of the given kind (object or list); an empty body becomes an empty object. Invalid — 400."""
        raw = self.body()
        if not raw.strip():
            return kind()
        try:
            value = json.loads(raw)
        except ValueError:
            raise ApiError(400, "В теле запроса не JSON")
        if not isinstance(value, kind):
            raise ApiError(400, "Тело запроса должно быть " + ("объектом JSON" if kind is dict else "списком JSON"))
        return value

    def reply_error(self, code, text):
        try:
            self.send({"error": text}, code=code)
        except OSError:
            pass   # the client already left

    def guarded(self, route):
        try:
            route()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass   # the app closed the connection without waiting for the response: not a server failure
        except ApiError as e:   # a client error: a clear text and code, not logged
            self.reply_error(e.code, e.text)
        except Exception as e:   # our own failure: details go to the log, the client gets no internal paths
            print("Сервер приложения:", self.path.split("?")[0], e)
            events.error("Сервер приложения " + self.path.split("?")[0], e)
            self.reply_error(500, f"Внутренняя ошибка ({type(e).__name__}): подробности в журнале событий")

    def do_GET(self):
        self.guarded(self.get)

    def do_POST(self):
        self.guarded(self.post)

    def safe_path(self):
        try:
            return urlparse(self.path).path
        except ValueError:
            return "?"   # a malformed address: logged as-is, unparsed

    def parse(self):
        try:
            url = urlparse(self.path)
            return url, {k: v[0] for k, v in parse_qs(url.query).items()}
        except ValueError:   # a malformed address ("http://[bad"): respond 400 before the password check, without logging an error
            raise ApiError(400, "Некорректный адрес запроса")

    def get(self):
        url, q = self.parse()
        if url.path == "/favicon.ico":
            return self.send(b"", "image/x-icon", code=204)   # a browser visiting /apk asks for it on its own; there's no icon to give
        if not self.allowed():
            return
        path = url.path
        if path.startswith(DESK_PREFIX):
            result = desk.get(path[len(DESK_PREFIX):], q)
            if isinstance(result, tuple):   # the file itself: (bytes, type)
                self.send(result[0], result[1])
            else:
                self.send(result)
        elif path == "/task":
            self.send(task_view(q.get("id", "")))        # an /ask task: the app polls its steps and answer
        elif path == "/tasks/one":
            self.send(data.task(q.get("id", "")))       # one task from the journal or the running ones (Android's task page)
        elif path == "/search":
            self.send(data.search(q.get("q", "")))       # search across everything: notes, tasks, chat history, memory, people
        elif path == "/notes/read":
            self.send(data.note(q.get("path", "")))
        elif path == "/quality":
            self.send(quality.summary(number(q, "days", 7, 1, 365)))   # the quality score
        elif path in ("/apk", "/sena.apk", "/ia.apk"):   # ia.apk — the old name: old links still work
            # download the phone app over Wi-Fi (Telegram won't carry files >50 MB)
            if not config.APK_FILE.is_file():
                raise ApiError(404, "Приложения для телефона на сервере нет")
            self.send(config.APK_FILE.read_bytes(), "application/vnd.android.package-archive", filename="sena.apk")
        else:
            raise ApiError(404, "не найдено")

    def post(self):
        if not self.allowed():
            return
        path = self.parse()[0].path
        if path in CORE_ONLY and (power.is_off() or power.is_stopping()):
            raise ApiError(409, power.refusal(CORE_ONLY[path]) if power.is_off() else "Сена выключается: подожди, пока она снова запустится")
        if path.startswith(DESK_PREFIX):   # an action from the remote: desk.act logs it itself
            return self.send(desk.act(path[len(DESK_PREFIX):], self.json_body()))
        if path not in POSTS:   # nothing is read or logged for an unknown path: an old app's body may be personal
            raise ApiError(404, "не найдено")
        if path == "/desk/upload":   # a file from the remote (dragged into the chat): goes to data/inbox, then to Sena as an attachment to /ask
            name = self.parse()[1].get("name", "файл")
            raw = self.body()
            saved = attachments.save(raw, name, source="app")
            events.log("app", "файл из приложения", source="desk", name=name, bytes=len(raw))
            return self.send({"path": str(saved), "name": attachments.shown_name(saved), "kind": attachments.kind(saved), "note": attachments.note(saved)})
        if path != "/ask":   # a message to the owner is logged as message_in in run_task
            raw = self.body()
            shown = f"(приватно, {len(raw)} байт)" if path in SIZE_ONLY else raw.decode("utf-8", "replace")
            events.log("app", path, source="app", body=shown)
        if path == "/ask":
            # the task runs in the background, the app polls its status every half second via /task
            d = self.json_body()
            text = field(d, "text", str, "").strip()
            files = [desk.allowed_path(f) for f in field(d, "files", list, [])]   # attachments only from Sena's folders
            agent = field(d, "agent", str, None)
            if not text and not files:
                raise ApiError(400, "Нужен текст сообщения или файл")
            task_id = uuid.uuid4().hex
            with _tasks_lock:   # two /ask calls in a row (chat and microphone) mustn't corrupt the dict for each other
                for old in [i for i, t in list(tasks.items()) if t["done"]][:-MAX_FINISHED]:
                    del tasks[old]   # long-finished ones aren't needed: the app already fetched the answer
                tasks[task_id] = {"steps": [], "answer": None, "done": False}
            threading.Thread(target=run_task, args=(task_id, text, files, agent), daemon=True).start()   # a stop writes it down if cut off (app.py)
            self.send({"id": task_id})
        elif path == "/memory":
            parts = self.json_body(list)   # database edits from the app: a list of sections {title, items}
            for part in parts:
                if not (isinstance(part, dict) and isinstance(part.get("title"), str) and isinstance(part.get("items"), list)
                        and all(isinstance(i, str) for i in part["items"])):
                    raise ApiError(400, "Память — список разделов вида {title: строка, items: [строки]}")
            facts.write(parts)
            self.send({"ok": True})
        elif path == "/voice":
            raw = self.body()
            if not raw:
                raise ApiError(400, "Запись пустая")
            kind = self.headers.get("Content-Type", "")   # voice -> text
            ext = "wav" if "wav" in kind else "m4a" if "mp4" in kind else "webm"
            self.send({"text": roles.hear(raw, f"voice.{ext}", cloud=gemini.transcribe)})   # at home first; the cloud only if the home hearing fails
            safety.background(people.learn_owner_voice, raw, what="Голос Темы")   # the app is his, behind its lock: his voice
        elif path == "/speak":   # her answer to a dictated message, said aloud in the app: the same voice as everywhere
            text = field(self.json_body(), "text", str, "").strip()
            if not text:
                raise ApiError(400, "Нечего сказать")
            self.send(roles.speak(text, settings.get("voice"), gemini.speech), "audio/mpeg")
