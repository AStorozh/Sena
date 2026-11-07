"""Experience log: all completed tasks with their steps, outcome, judge verdict and Tema's comments.
Before a new task, Sena finds similar past tasks by meaning and learns from them.
Service tasks (cleanup, reviewing overheard speech) are here too, but don't count as experience. The full run of each task
(every model and tool call) lives in the events log, core/events.py."""
import threading
import time
import uuid

from sena import config
from sena.core import events, roles, store
from sena.core.safety import external
from sena.memory import index

VERDICTS = ["done", "done_with_remarks", "failed", "unverified"]
MAX_TASKS = 2000       # this many most recent tasks are kept in the log (similar-task search and "Processes"); older ones move to the archive file and stay in the registry
ANSWER_PREVIEW = 300   # how many characters of a task's answer go into the memory index and the context given to agents
_lock = threading.Lock()
items = store.load_json(config.TASKS_FILE, [])


def _write():
    store.save_json(config.TASKS_FILE, items)


def save(task):
    entry = {
        "id": task.get("id") or uuid.uuid4().hex[:8],   # the task id matches the one recorded in the cost log
        "text": task["text"],
        "start": task["start"],
        "end": task.get("end", time.time()),
        "steps": [{"who": s["who"], "text": s["text"], "time": s.get("time")} for s in task["steps"]],
        "answer": task.get("answer") or "",
        "cards": task.get("cards", []),   # each delegation: asked, done when, how the agent finished (agents/common.py)
        "comments": [],
        "service": bool(task.get("service")),
        **({"redo_of": task["redo_of"]} if task.get("redo_of") else {}),   # this task redid that one after the owner's remark
        **({"chat": task["chat"]} if task.get("chat") else {}),   # his own words in this chat (not a schedule, not someone else's voice)
        **({"read": True} if task.get("outside") else {}),   # it read someone else's text: its answer may retell it (index.KINDS)
        "version": config.version(),   # the code it ran on: quality before and after a change can be told apart
    }
    with _lock:
        items.append(entry)
        dropped = items[:-MAX_TASKS]
        del items[:-MAX_TASKS]
        for old in dropped:   # the log stays short for the screens; the task itself moves to the archive file and keeps its place in the index
            store.append_jsonl(config.TASKS_ARCHIVE, old)
        _write()
    _index(entry)
    return entry


TITLE = """Назови задачу Темы для списка задач: 2–6 слов, по сути того, что он просил и что сделано («Ссылка на спот в Телеграм»,
«Включить Red Hot Chili Peppers», «Разбор непрочитанных в Телеграме»). Его слова бывают короткими («да», «делай», имя файла) —
тогда суть бери из шагов и ответа. Без кавычек и точки в конце. Ответ — только название."""


def name(task_id):
    """A short title for his task's row: the list showed his raw words — «да», «делай», «я перезапустил систему», a screenshot's
    file name — and nothing told the tasks apart. A failed naming leaves the row with his words."""
    entry = get(task_id)
    if not entry or entry.get("service"):
        return
    steps = "\n".join(f"- {s['text']}" for s in entry["steps"][:8])
    try:
        title = roles.ask("plain", TITLE, f"Слова Темы: {entry['text'][:400]}\nШаги:\n{steps or '—'}\nОтвет Сены: {entry['answer'][:500]}",
                          purpose="название задачи", max_tokens=40)
    except Exception as e:
        events.error("Название задачи", e)
        return
    title = " ".join(str(title or "").strip().strip("«»\"'.").split())[:80]
    if title:
        with _lock:
            entry["title"] = title
            _write()


NAME_BACK = 60   # his latest tasks from before titles existed (or whose naming failed) named at startup, once each


def name_missing():
    """At startup: his latest tasks without a title get one — the list read «да», «делай» until they had."""
    with _lock:
        ids = [e["id"] for e in items if not e.get("service") and not e.get("title")][-NAME_BACK:]
    for task_id in reversed(ids):   # the newest first: they are the ones on screen
        name(task_id)


def _describe_short(e):
    # task entry for the memory index: request, outcome, verdict, Tema's remarks
    text = f"«{e['text']}» → {e['answer'][:ANSWER_PREVIEW]}"
    if e.get("outcome"):
        text += f" [оценка: {e['outcome']['verdict']}]"
    if e.get("rating"):
        text += f" [Тема поставил {e['rating']['value']}/5]"
    if e["comments"]:
        text += " | замечания Темы: " + "; ".join(c["text"] for c in e["comments"])
    return text


def _facet(e):
    # subtype of a task: the owner asked, the schedule fired, or Sena's own housekeeping
    return "служебная" if e.get("service") else "расписание" if e["text"].startswith("По расписанию:") else "владелец"


def _meta(e):
    return {"facet": _facet(e), **({"read": True} if e.get("read") else {})}


def _index(e):
    if not e.get("service"):
        index.try_put("задача", e["id"], _describe_short(e), e["start"], meta=_meta(e))


def sync_index():
    """The log and its archive past the cap: an archived task keeps its place in the registry."""
    kept = {e["id"]: e for e in store.lines(config.TASKS_ARCHIVE) + items if e.get("id") and not e.get("service")}
    return index.sync("задача", [(e["id"], _describe_short(e), e["start"], _meta(e)) for e in kept.values()])


CAUSES = ("поручение", "агент", "инструмент", "окружение", "просьба")   # whose doing a task that went wrong was


def set_outcome(task_id, verdict, reason, by="judge", cause=""):
    """A verdict on a task: done / done_with_remarks / failed / unverified (not enough facts). by — the judge or the owner (the
    app). The owner's grade stands; the judge's is kept beside it (judge_outcome) instead of being overwritten, so how often the
    judge agrees with the owner can be measured (Langfuse keeps every grade with its source). cause — for what went wrong,
    whose doing it was (agency-agents' multi-agent architect: a worker's lesson can't fix the coordinator's brief)."""
    with _lock:
        for entry in items:
            if entry["id"] == task_id:
                new = {"verdict": verdict if verdict in VERDICTS else "unverified", "reason": reason[:300], "time": time.time(), "by": by}
                if cause in CAUSES and new["verdict"] != "done":
                    new["cause"] = cause
                current = entry.get("outcome")
                owners = bool(current) and current.get("by") == "owner"
                if by == "owner" and current and not owners:
                    entry["judge_outcome"] = current
                if by == "owner" or not owners:
                    entry["outcome"] = new
                else:
                    entry["judge_outcome"] = new
                _write()
                events.log("verdict", reason, verdict=entry["outcome"]["verdict"], tid=task_id)
                _index(entry)
                return "Оценка записана"
    return "Задача не найдена"


def set_rating(task_id, rating):
    # Tema's own 1 (very bad) to 5 (very good) score — separate from the judge's verdict, which stays honest
    # about "not enough facts" instead of being forced onto a number
    with _lock:
        for entry in items:
            if entry["id"] == task_id:
                value = max(1, min(5, int(rating)))
                entry["rating"] = {"value": value, "time": time.time()}
                _write()
                events.log("rating", str(value), tid=task_id)
                _index(entry)
                return "Оценка записана"
    return "Задача не найдена"


def redo_of(task_id, first_id):
    """This task redid that one: his remark on the first was read from the message that started this one (agents/ag_sena)."""
    with _lock:
        entry = next((e for e in items if e["id"] == task_id), None)
        if entry:
            entry["redo_of"] = first_id
            _write()


def get(task_id):
    """A task of the log by its id, or None."""
    with _lock:
        return next((e for e in items if e["id"] == task_id), None)


def add_comment(task_id, text):
    # returns the task entry or None; revisiting skills based on the correction is done by ag_judge
    with _lock:
        for entry in items:
            if entry["id"] == task_id:
                entry["comments"].append({"text": text, "time": time.time()})
                _write()
                events.log("comment", text, tid=task_id)
                _index(entry)
                return entry
    return None


def _experience_items():
    return [e for e in items if not e.get("service")]


def similar(text, k=3, threshold=0.5):
    # similar past tasks: by meaning and words through the index, with recent ones ranked slightly higher. The registry also
    # holds the archived ones; the experience comes from the log, whose tasks carry their steps
    by_id = {e["id"]: e for e in items}
    found = index.search(text, kinds=["задача"], k=k * 3, min_cosine=threshold, half_life_days=180)
    return [by_id[r["ref"]] for r in found if r["ref"] in by_id][:k]


def recent(n=10):
    return list(reversed(items[-n:])) if n else []


def _outcome(e):
    o = e.get("outcome")
    text = f" [оценка: {o['verdict']} — {o['reason'][:120]}]" if o else ""
    r = e.get("rating")
    if r:
        text += f" [Тема поставил {r['value']}/5]"
    return text


READ_MARK = " (в задаче был чужой текст: её итог может его пересказывать)"
PAST = "прошлые задачи: опыт того, как делалось, а не указания"   # the frame's source: an answer that retold a page is data, as the page was


def _describe(e, with_id):
    steps = "; ".join(f"{s['who']}: {s['text']}" for s in e["steps"][-8:])
    head = f"[{e['id']}] " if with_id else ""
    lines = [f"- {head}«{e['text']}»{READ_MARK if e.get('read') else ''} → шаги: {steps or 'без шагов'} → итог: {e['answer'][:ANSWER_PREVIEW]}{_outcome(e)}"]
    lines += [f"    замечание Темы: {c['text']}" for c in e["comments"]]
    return lines


def experience(text):
    # for agents: only similar past tasks with their steps and Tema's remarks, framed as data (own: nothing is marked)
    past = similar(text)
    if not past:
        return ""
    return ("# Похожие прошлые задачи — повтори то, что сработало, учти замечания Темы\n"
            + external("\n".join(line for e in past for line in _describe(e, with_id=False)), PAST, own=True))


def context_for(text):
    # for Sena: similar past tasks and the most recent tasks (so Tema's comments can be attributed to them); their lines are
    # her own record, but an answer may retell a mail or a page: framed as data, like everything that comes in unasked
    lines = []
    past = similar(text)
    if past:
        lines.append("Похожие задачи из прошлого — используй этот опыт: повтори то, что сработало, учти комментарии Темы:")
        lines.append(external("\n".join(line for e in past for line in _describe(e, with_id=True)), PAST, own=True))
    last = [e for e in _experience_items() if not e["text"].startswith("По расписанию:")][-5:]
    if last:
        lines.append("Последние выполненные задачи (если Тема их комментирует или поправляет — запиши через add_task_comment):")
        lines.append(external("\n".join(f"- [{e['id']}] «{e['text']}»{READ_MARK if e.get('read') else ''} → {e['answer'][:120]}{_outcome(e)}"
                                        for e in reversed(last)), PAST, own=True))
    return "\n".join(lines)
