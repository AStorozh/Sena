"""The owner's long goals: not one message and not one job, but something he moves towards for weeks — "English to B2 by May",
"save for a car by spring", "run a half marathon" (a project with tasks lives in Luma). A goal has why, a deadline, steps, a
log of how it goes and the next check. Sena keeps it: the schedule wakes her at the next check (scheduler.py, on_goal), she
asks and nudges, and what he tells her goes back into the goal. A goal is a record in the one store; the registry finds it by
meaning, so a message about the same thing brings it up without being asked (kind "цель")."""
import hashlib
import json
import threading
import uuid
from datetime import datetime

from sena import config
from sena.core import events, moment, status, store
from sena.core.tools import Failure, clamp, string, strings, tool
from sena.memory import index

KIND = "цель"
ACTIVE, DONE, DROPPED = "active", "done", "dropped"
CHECK_DAYS = 7      # how often Sena checks in on a goal unless the owner asked otherwise
LOG_KEPT = 200      # check-ins kept inside the goal (the rest stays in its history)
_lock = threading.Lock()
_told = set()   # goals already told to have missed a field: once, not at every half-minute pass of the schedule


def _all():
    return [_whole(g) for g in store.load_json(config.GOALS_FILE, []) if isinstance(g, dict)]


def _whole(g):
    # a goal from an older version, or edited by hand, may lack a field or carry a wrong one: the default stands in (and is kept
    # with the goal's next change), told once — the schedule asked about it every half a minute and failed on it every time
    fixed = []
    if not g.get("id"):   # named by what it holds, so the same goal keeps one id until it is saved
        g["id"] = hashlib.md5(json.dumps(g, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:6]
        fixed.append("id")
    for key, default in (("title", "цель без названия"), ("why", ""), ("deadline", ""), ("steps", []), ("log", []), ("progress", None), ("tz", "")):
        if key not in g or (key == "title" and not g[key]):
            g[key] = list(default) if isinstance(default, list) else default
            fixed.append(key)
    if g.get("status") not in (ACTIVE, DONE, DROPPED):
        g["status"] = ACTIVE
        fixed.append("status")
    every = clamp(g.get("check_every_days"), None, 1, 90)
    if every is None:
        fixed.append("check_every_days")
    g["check_every_days"] = every or CHECK_DAYS
    if not isinstance(g.get("created"), (int, float)):
        g["created"] = moment.parse(g.get("created")) or moment.now()
        fixed.append("created")
    if not isinstance(g.get("next_check"), (int, float)):
        g["next_check"] = 0.0   # due now: the check sets the next one
        fixed.append("next_check")
    if fixed and g["id"] not in _told:
        _told.add(g["id"])
        events.log("goal", f"цель «{g['title']}»: не было полей {', '.join(fixed)}, взяты обычные", id=g["id"], failed=True)
    return g


def _save(items):
    store.save_versioned(config.GOALS_FILE, items, indent=1)


def _text(g):
    steps = "; ".join(("✓ " if s["done"] else "") + s["text"] for s in g["steps"])
    return f"{g['title']}. {g['why']} " + (f"Срок: {g['deadline']}. " if g["deadline"] else "") + (f"Шаги: {steps}" if steps else "")


def _index(g):
    if g["status"] == ACTIVE:
        index.put(KIND, g["id"], _text(g), g["created"], g.get("tz", ""), {"status": g["status"]})
    else:
        index.drop(KIND, g["id"])


def progress(g):
    """How far along: the owner's own number if he gave one, otherwise the share of steps done."""
    if g.get("progress") is not None:
        return g["progress"]
    return round(100 * sum(s["done"] for s in g["steps"]) / len(g["steps"])) if g["steps"] else 0


def _deadline(text):
    text = (text or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.strptime(text, fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
    return text   # "к маю", "в этом году": kept in his words


def add(title, why="", deadline="", steps=None, check_every_days=CHECK_DAYS):
    """A new goal. Written by a task that had read someone else's text, it keeps that mark ("outside"): its check-ins start
    marked (app.py on_goal -> run_job), as a job scheduled from there does — a mail can't plant a goal that drives the terminal."""
    title = " ".join(str(title).split())[:200]
    if len(title) < 3:
        return Failure("Назови цель одной фразой")
    now, every = moment.now(), clamp(check_every_days or CHECK_DAYS, CHECK_DAYS, 1, 90)
    g = {"id": uuid.uuid4().hex[:6], "title": title, "why": " ".join(str(why).split())[:500], "deadline": _deadline(deadline),
         "steps": [{"text": " ".join(str(s).split())[:200], "done": False, "t": now} for s in (steps or []) if str(s).strip()],
         "progress": None, "status": ACTIVE, "check_every_days": every, "next_check": now + every * 86400, "log": [], "created": now,
         "tz": moment.offset(now)}
    outside = status.outside()
    if outside:
        g["outside"] = str(outside)[:300]
    with _lock:
        items = _all()
        items.append(g)
        _save(items)
    _index(g)
    events.log("goal", f"новая цель: {title}", id=g["id"], deadline=g["deadline"] or None)
    return f"Цель записана [{g['id']}]: {title}. Проверю, как идёт, через {g['check_every_days']} дн."


def update(id, step="", done_step="", note="", progress=None, state="", deadline="", check_every_days=None):
    """Change a goal: add a step, mark a step done (by its words or number), log how it goes, set progress, deadline, check
    frequency, or close it (state: done / dropped). Every change is a line in the goal's log. The parameter isn't "status":
    the brain takes that name off every call for the line it shows the owner, so a goal could never be closed."""
    with _lock:
        items = _all()
        g = next((x for x in items if x["id"] == id), None)
        if not g:
            return Failure(f"Цели {id} нет. Есть: " + (", ".join(f"[{x['id']}] {x['title']}" for x in items if x["status"] == ACTIVE) or "ни одной"))
        changes, now = [], moment.now()
        new = {"text": " ".join(str(step).split())[:200], "done": False, "t": now} if step else None
        target = _step({"steps": g["steps"] + ([new] if new else [])}, done_step) if done_step else None
        if done_step and target is None:   # checked before anything changes: a step added in the same call isn't lost unsaid
            return Failure("Такого шага нет, цель не изменена. Шаги: " + "; ".join(f"{n}. {s['text']}" for n, s in enumerate(g["steps"], 1)))
        if new:
            g["steps"].append(new)
            changes.append(f"шаг: {step}")
        if target:
            target["done"], target["done_t"] = True, now
            changes.append(f"сделано: {target['text']}")
        if progress is not None and clamp(progress, None, 0, 100) is not None:   # "половина" isn't a number: no change, not a crash
            g["progress"] = clamp(progress, None, 0, 100)
            changes.append(f"ход: {g['progress']}%")
        if deadline:
            g["deadline"] = _deadline(deadline)
            changes.append(f"срок: {g['deadline']}")
        if check_every_days and clamp(check_every_days, None, 1, 90) is not None:
            g["check_every_days"] = clamp(check_every_days, None, 1, 90)
            g["next_check"] = now + g["check_every_days"] * 86400
            changes.append(f"проверка раз в {g['check_every_days']} дн.")
        if state in (ACTIVE, DONE, DROPPED) and state != g["status"]:
            g["status"] = state
            changes.append({"done": "цель достигнута", "dropped": "цель оставлена", "active": "цель снова в работе"}[state])
        if note:
            changes.append(" ".join(str(note).split())[:500])
        if not changes:
            return Failure("Нечего менять: назови шаг, отметку, заметку, ход, срок или state")
        g["log"] = (g["log"] + [{"t": now, "text": "; ".join(changes)}])[-LOG_KEPT:]
        _save(items)
    _index(g)
    events.log("goal", f"цель «{g['title']}»: {'; '.join(changes)}", id=id)
    return f"Цель «{g['title']}»: {'; '.join(changes)}. Ход {progress_line(g)}"


def _step(g, which):
    which = str(which).strip()
    if which.isdigit() and 1 <= int(which) <= len(g["steps"]):
        return g["steps"][int(which) - 1]
    lowered = which.lower()
    return next((s for s in g["steps"] if lowered in s["text"].lower() or s["text"].lower() in lowered), None)


def progress_line(g):
    left = [s["text"] for s in g["steps"] if not s["done"]]
    return f"{progress(g)}%" + (f", следующий шаг: {left[0]}" if left else "")


def listing(state=ACTIVE):
    items = [g for g in _all() if state in ("all", g["status"])]
    if not items:
        return "Целей нет" if state == ACTIVE else "Таких целей нет"
    return "\n".join(f"- [{g['id']}] {g['title']} — {progress_line(g)}" + (f"; срок {g['deadline']}" if g["deadline"] else "")
                     + (f"; последнее: {g['log'][-1]['text']}" if g["log"] else "") for g in items)


def all_goals():
    return _all()


def due(now=None):
    """Active goals whose next check has come (the schedule asks every half a minute)."""
    now = now or moment.now()
    return [g for g in _all() if g["status"] == ACTIVE and g.get("next_check", 0) <= now]


def checked(id):
    """The check-in happened: the next one in check_every_days."""
    with _lock:
        items = _all()
        g = next((x for x in items if x["id"] == id), None)
        if g:
            g["next_check"] = moment.now() + g["check_every_days"] * 86400
            _save(items)
    return g


def check_text(g):
    """What Sena is told when a goal's check comes: everything she needs to write to the owner in one or two sentences."""
    left = [s["text"] for s in g["steps"] if not s["done"]]
    days = ""
    if g["deadline"]:
        try:
            days = f" До срока {(datetime.strptime(g['deadline'], '%d.%m.%Y') - datetime.now()).days + 1} дн."
        except ValueError:
            days = f" Срок: {g['deadline']}."
    return (f"Пора проверить долгую цель Темы [{g['id']}] «{g['title']}» ({g['why'] or 'зачем — не сказано'}). Ход {progress(g)}%.{days}"
            + (f" Следующий шаг: {left[0]}." if left else " Все шаги сделаны — спроси, достигнута ли цель.")
            + (f" Последняя запись: {g['log'][-1]['text']}." if g["log"] else "")
            + (" Две последние записи без сделанного шага: предложи разбить следующий шаг на меньший, а не повторять его; "
               "если Тема снова «потом» — предложи проверять реже (check_every_days)." if _stuck(g) else "")
            + " Это не тихая проверка: «OK» здесь не ответ, это задание само просит спросить. Напиши Теме одной-двумя фразами: "
              "напомни следующий шаг и спроси, как идёт. Его ответ потом запиши в goal_update.")


def _stuck(g):
    # the last two entries moved nothing (agency-agents' growth mentor: a step that doesn't move is too big, not forgotten)
    return len(g["log"]) >= 2 and not any(w in e["text"] for e in g["log"][-2:] for w in ("сделано:", "ход:", "цель достигнута"))


def sync_index():
    rows = [(g["id"], _text(g), g["created"], {"status": g["status"]}) for g in _all() if g["status"] == ACTIVE]
    return index.sync(KIND, rows)


TOOLS = [
    tool("goal_add", "Записать долгую цель Темы (то, к чему он идёт неделями: выучить, накопить, пробежать; проект с делами — в Luma): зачем, срок, шаги",
         {"title": string("цель одной фразой, его словами"), "why": string("зачем она ему"), "deadline": string("срок: ДД.ММ.ГГГГ или его словами"),
          "steps": strings("первые шаги"), "check_every_days": {"type": "integer", "description": "как часто спрашивать о ходе, в днях (по умолчанию 7)"}},
         ["title"]),
    tool("goal_update", "Изменить цель: новый шаг, отметить шаг сделанным (его словами или номером), записать, как идёт, ход в процентах, срок, "
                        "как часто проверять, или закрыть (state: done — достигнута, dropped — оставлена)",
         {"id": string(), "step": string("новый шаг"), "done_step": string("какой шаг сделан"), "note": string("как идёт, его словами"),
          "progress": {"type": "integer", "description": "ход 0–100, если Тема назвал"}, "state": {**string(), "enum": [ACTIVE, DONE, DROPPED]},
          "deadline": string("новый срок"), "check_every_days": {"type": "integer", "description": "как часто проверять, в днях"}},
         ["id"]),
    tool("goal_list", "Долгие цели Темы с ходом, сроком и последней записью", {"state": {**string(), "enum": [ACTIVE, DONE, DROPPED, "all"]}}),
]
FUNCTIONS = {"goal_add": add, "goal_update": update, "goal_list": listing}
