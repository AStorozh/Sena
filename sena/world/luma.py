"""Luma: Tema's personal app on this Mac (tasks, projects, calendar, habits, time, expenses, notes, boards).
Sena works with it directly, like any AI agent: Luma has a tools API (list — GET /api/v1/tools, call — POST /api/v1/tools/<name>).
Sena doesn't copy the tools: she takes the list and schemas straight from Luma itself. So a new tool in Luma becomes available immediately, and a group
the owner disabled in Luma's developer panel disappears here too. Sena opens Luma herself if it's closed."""
import json
import re
import subprocess
import threading
import time
import uuid
from difflib import get_close_matches
from urllib.parse import urlparse

from sena import config
from sena.core import events, safety, status
from sena.core.tools import Failure, string, tool
from sena.world import net

SOURCE = "Luma"
START_WAIT = 60        # how many seconds to wait for the launched app's server
CALL_TIMEOUT = 60      # a Luma tool responds within fractions of a second; the slowest case is an expense in a foreign currency (rate fetched from the internet)
RESULT_LIMIT = 10000   # characters of the response for the model; a longer one (a long note, tasks with their notes) goes whole to a file
TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")   # the name ends up in the request path: "../keys" and the like won't pass this
UNANSWERED = 50        # writes without an answer whose key is kept for a retry
_opening = threading.Lock()   # several calls at once shouldn't open the app twice
_unanswered = {}       # (task, tool, input) of a call that got no answer -> the Idempotency-Key it went with: Luma does its retry once


class LumaError(net.WorldError):
    """Luma is unreachable or refused the request. The text can be shown to Tema as is."""


def _url():
    # Luma takes another port when 3110 is busy and writes the one it took to its pid file; an address set in .env wins
    if config.LUMA_PORT_FILE:
        try:
            port = int(json.loads(config.LUMA_PORT_FILE.read_text(encoding="utf-8"))["port"])
            if 0 < port < 65536:
                return f"http://127.0.0.1:{port}"
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return config.LUMA_URL


def _base():
    url = _url().rstrip("/")
    if urlparse(url).hostname not in ("127.0.0.1", "localhost", "::1"):
        raise LumaError("LUMA_URL должен вести на этот же Mac (127.0.0.1): у Luma нет защиты для чужих адресов")
    return url


def _headers():
    # with the key, Luma knows who's calling: its AI action log shows it's Sena, and the developer panel's toggles apply fully
    return {"Authorization": "Bearer " + config.LUMA_KEY} if config.LUMA_KEY else None


def _explain(reply):
    # Luma errors arrive as {"error": {"code", "message"}}; its own wording beats a generic "responded 400" — it says which field to fix
    try:
        error = reply.json()["error"]
        text = f"Luma отказала ({error['code']}): {error['message']}"
    except (ValueError, KeyError, TypeError):
        return None
    if error["code"] == "unauthorized":
        text += ". Проверь LUMA_KEY в .env: ключ выпускается в Luma, «Настройки → Панель разработчика → Ключи»"
    return text


def is_up():
    url = _base() + "/api/v1/health"   # a bad address is a configuration error, not "Luma is closed", so this check sits outside the try
    try:
        net.get(url, timeout=3, who=SOURCE, error=LumaError)
        return True
    except LumaError:
        return False


def start():
    """Launch the Luma app if it's closed, and wait for its server."""
    with _opening:
        if is_up():
            return
        app = config.LUMA_APP
        if not app.exists():
            raise LumaError(f"Luma не запущена, а программы нет по пути {app}: путь задаётся в LUMA_APP (.env)")
        # ELECTRON_RUN_AS_NODE turns Luma into a plain Node process: no window and no server
        env = safety.clean_env(("ELECTRON_RUN_AS_NODE",))
        events.log("app", "открываю программу Luma", source=SOURCE, app=str(app))   # a program started, not a request: "fetch" is HTTP
        done = subprocess.run(["open", "-g", str(app)], env=env, capture_output=True, text=True, timeout=30)   # -g: don't bring the window to the front over whatever Tema is doing
        if done.returncode:   # macOS refused to open it (damaged, not allowed): no point waiting a minute for its server
            raise LumaError(f"Luma не открылась: {(done.stderr or done.stdout or '').strip()[:300] or f'open вернул {done.returncode}'}")
        deadline = time.time() + START_WAIT
        while time.time() < deadline:
            time.sleep(2)
            if is_up():
                return
        raise LumaError(f"Открыла Luma, но её сервер не ответил за {START_WAIT} с: посмотри, нет ли в ней окна с ошибкой")


def _catalogue():
    start()
    reply = net.get(_base() + "/api/v1/tools", headers=_headers(), timeout=20, who=SOURCE, error=LumaError, explain=_explain)
    rows = net.as_json(reply, SOURCE, LumaError)
    if not isinstance(rows, list) or not all(isinstance(r, dict) and r.get("name") for r in rows):
        raise LumaError("Luma прислала список инструментов в незнакомом виде")
    return rows


NEEDED = ("context", "tasks", "planning", "calendar")   # the groups Sena's routing counts on


def check():
    """For the system check: (state, words). True — Luma answers, lets Sena's key in and has the groups she counts on;
    False — what to fix; None — Luma isn't running (it is opened when needed, not by a check). A bare /health said "fine"
    with a revoked key or the tools switched off."""
    if not is_up():
        return None, "не запущена"
    try:
        rows = _catalogue()
    except LumaError as e:
        return False, str(e)
    off = [g for g in NEEDED if g not in {r.get("group") for r in rows}]
    if off:
        return False, f"в Luma выключены группы инструментов: {', '.join(off)} (Luma → Панель разработчика)"
    return True, f"отвечает, инструментов {len(rows)}"


def _short(row):
    # the description's first sentence: it shows what the tool is for; the rest is in luma_tools(name)
    first = re.split(r"(?<=[.!?])\s+(?=[A-ZА-Я`])", (row.get("description") or "").strip(), maxsplit=1)[0]   # "e.g. "..."" isn't the end of a sentence
    return first if len(first) <= 220 else first[:217] + "..."


def _compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def luma_tools(about=""):
    """What Luma can do: groups and tools; a group gives a short line per tool, a tool's name gives all its fields."""
    rows = _catalogue()
    groups = {}
    for row in rows:
        groups.setdefault(row.get("group") or "other", []).append(row)
    about = (about or "").strip().lower()
    if not about:
        lines = [f"{group} ({len(items)}): " + ", ".join(r["name"] for r in items) for group, items in groups.items()]
        return ("Инструменты Luma по группам (что видно с этим доступом; выключенные в Luma группы не показаны):\n" + "\n".join(lines)
                + "\nКоротко про каждый — luma_tools(группа), все поля одного — luma_tools(имя инструмента).")
    if about in groups:
        return "\n".join(f"{r['name']} [{'чтение' if r.get('scope') == 'read' else 'запись'}] — {_short(r)}" for r in groups[about])
    for row in rows:
        if row["name"] == about:
            parts = [f"{about} ({'только читает' if row.get('scope') == 'read' else 'меняет данные'}): {row.get('description') or ''}",
                     "input (JSON Schema): " + _compact(row.get("inputSchema") or {})]
            if row.get("examples"):
                parts.append("Примеры input: " + _compact(row["examples"]))
            return safety.cut("\n".join(parts), RESULT_LIMIT)
    near = get_close_matches(about, [r["name"] for r in rows] + list(groups), n=4)
    raise LumaError(f"В Luma нет группы или инструмента «{about}»." + (f" Похожие: {', '.join(near)}." if near else " Весь список — luma_tools без параметра."))


def _name(tool):
    name = str(tool or "").strip()
    if not TOOL_NAME.match(name):
        raise LumaError(f"«{name}» не имя инструмента Luma: оно из строчных латинских букв и подчёркиваний, например get_today. Список — luma_tools")
    return name


def _arguments(value):
    if value is None or value == "":
        return {}
    if not isinstance(value, dict):
        raise LumaError("input должен быть JSON-объектом, например {\"date\": \"tomorrow\"}")
    return value


def _post(name, arguments):
    """One call of a Luma tool: (result, replayed). A call that got no answer (time ran out, the connection dropped) may still
    have been done, so its key is kept: when exactly the same call comes again in the same task, Luma answers it from the
    first one instead of doing it twice (Idempotency-Key, kept by Luma for 10 minutes). Any other call gets a fresh key."""
    same = ((status.current() or {}).get("id"), name, _compact(arguments))
    key = _unanswered.pop(same, None) or uuid.uuid4().hex
    try:
        reply = net.post(f"{_base()}/api/v1/tools/{name}", arguments, {**(_headers() or {}), "Idempotency-Key": key}, timeout=CALL_TIMEOUT,
                         who=SOURCE, error=LumaError, explain=_explain)
    except LumaError as e:
        if net.unanswered(e):
            _unanswered[same] = key
            while len(_unanswered) > UNANSWERED:
                _unanswered.pop(next(iter(_unanswered)))
        raise
    try:
        result = reply.json()["result"]
    except (ValueError, KeyError, TypeError):
        raise LumaError("Luma ответила не так, как ждали (нет поля result)")
    return result, reply.headers.get("Idempotent-Replay") == "true"


def refused(result):
    """Items of a batch call Luma refused ([{index, item, error}]) and whether it did anything else: a batch answers 200 even
    when every item failed."""
    failed = result.get("failed") if isinstance(result, dict) else None
    if not isinstance(failed, list) or not failed:
        return [], True
    return failed, any(isinstance(v, list) and v for k, v in result.items() if k != "failed")


def call(tool, input=None, per_item=False):
    """A Luma tool for Sena's own code (a work keeping its project there): the result as data. Luma is not opened from here —
    the caller checks is_up() first. A batch where every item was refused raises with Luma's reason; per_item — the caller
    reads the refusals item by item itself ("failed": [{index, item, error}]): a card the owner deleted is his decision about
    that card, not a broken sync (the raise turned it into five failures and the whole project's sync off)."""
    result, _ = _post(_name(tool), _arguments(input))
    failed, done = refused(result)
    if failed and not done and not per_item:
        raise LumaError("Luma не приняла: " + "; ".join(str(f.get("error", ""))[:200] for f in failed[:3]))
    return result


def luma(tool, input=None):
    """Call a Luma tool and return its response."""
    name, arguments = _name(tool), _arguments(input)
    start()
    result, replayed = _post(name, arguments)
    text = "Готово: Luma ничего не вернула." if result is None else _compact(result)
    # fenced: Luma's data can hold other people's text (imported from spreadsheets, notes). But it is the owner's own app — like
    # his notes, it doesn't make the task "foreign" (that would hold every coding job after a look at his to-dos)
    shown = safety.external(safety.spill(text, RESULT_LIMIT, name="luma"), SOURCE, own=True)
    failed, done = refused(result)
    if failed and not done:
        return Failure("Luma ничего не сделала: все пункты отклонены (см. failed) — поправь их и повтори.\n" + shown)
    if failed:
        return f"Не всё прошло: {len(failed)} пункт(ов) Luma отклонила (см. failed), остальное сделано.\n" + shown
    if replayed:
        return "Это повтор прошлого вызова: Luma сделала его тогда, второй раз не делала.\n" + shown
    return shown


TOOLS = [tool("luma",
              "Вызвать инструмент Luma — личного приложения Темы: дела и проекты, календарь и события, привычки, учёт времени и таймер, траты и доходы, заметки, доски. "
              "Цели Темы ведёт Сена (группа goals); цели в Luma — только если он сам назвал Luma. "
              "День целиком — get_today; имена проектов, привычек и групп — get_structure; поиск по слову — search. Остальные инструменты и их поля — luma_tools. "
              "Ошибка Luma говорит, какое поле поправить: поправь и повтори.",
              {"tool": string("Имя инструмента Luma, например get_today, task_add, habit_log, expense_add"),
               "input": {"type": "object", "description": "Поля инструмента, например {\"items\":[{\"text\":\"Позвонить Диме завтра 15:00\"}]}; у get_today можно не передавать"}},
              ["tool"]),
         tool("luma_tools",
              "Что умеет Luma. Без параметра — группы и названия инструментов; группа (context, tasks, planning, calendar, tracking, time, finance, nutrition, "
              "training, notes, board, inbox, settings, trash) — коротко про каждый; "
              "имя инструмента — все его поля и примеры. Заглядывай, когда не уверена, как называется инструмент или что в нём передавать.",
              {"about": string("Группа или имя инструмента; пусто — обзор")})]
FUNCTIONS = {"luma": luma, "luma_tools": luma_tools}
