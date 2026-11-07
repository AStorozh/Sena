"""What the app screens show and the desk shares: running and finished tasks with their cost, notes, people, search across
everything, the disk the data takes. Gathers data from memory and the log, doesn't compute or store anything itself. Weather,
exchange rates, and news don't go to the screen: that's data for Sena, not for the owner."""
import os
import time

from sena import config
from sena.core import costs, status
from sena.memory import people, recall, task_log, vault
from sena.phone.api import ApiError

STARTED = time.time()   # when the screen came up (the bot itself logs the start event)


def active_tasks():
    """Currently running tasks with their cost. id — the thread number (used to stop the task), tid — the task's id in the logs and expenses,
    service — a maintenance task (cleanup, triage): it doesn't count as experience; work — a long work's turn: service too, but
    his «Стоп» stops it and pauses the work (ag_sena tools.stop_all), so the apps count it as his."""
    shown = ("text", "start", "steps", "done", "answer", "service", "next", "work", "using", "failed_by")
    rows = [{**{k: t[k] for k in shown if k in t}, "id": str(ident), "tid": t.get("id")} for ident, t in list(status.tasks.items())]
    spent = _spent(rows, "tid")
    return [{**t, "cost": spent.get(t["tid"], 0)} for t in rows]


def _spent(rows, key):
    """{task id: dollars} for these tasks, reading the cost journal from the oldest one's start only. No tasks — no reading at
    all: with nothing running, every overview (each app polls it every few seconds) walked the whole cost history, all months."""
    if not rows:
        return {}
    return costs.by_task([t[key] for t in rows], since=min(t.get("start") or 0 for t in rows))


def note(path):
    try:
        text = vault.read(path)
    except ValueError:
        raise ApiError(400, "Недопустимый путь заметки")
    return {"path": path, "text": text} if text is not None else {"error": f"Заметки «{path}» нет"}


def write_note(path, text, mode):
    """Writing a note from the app: a path outside storage or in a reserved folder gets a 400, not a server crash."""
    try:
        return vault.write_note(path, text, mode)
    except ValueError:
        raise ApiError(400, "Недопустимый путь заметки")


def write_note_result(path, text, mode):
    """Same, but a ready-made response for the app: a substantive rejection (note already exists, too long, no such note for append/replace) —
    {"error"}, not a green "result" with the same text."""
    result = write_note(path, text, mode)
    return {"result": result} if result.startswith(("Создана", "Дописано", "Заменен")) else {"error": result}


def delete_note_result(path):
    """Delete a note from the app: a ready-made response, same shape as write_note_result."""
    try:
        result = vault.delete_note(path)
    except ValueError:
        raise ApiError(400, "Недопустимый путь заметки")
    return {"result": result} if result.endswith("удалена") else {"error": result}


def people_rows():
    rows = []
    for p in sorted(people.all_people(), key=lambda x: -x["last_seen"]):
        rows.append({"id": p["id"], "name": p.get("name") or "", "aliases": p["aliases"], "relationship": p["relationship"],
                     "facts": people.facts(p), "opinion": p["opinion"], "voice": bool(p["voices"]), "last_seen": p["last_seen"]})
    return rows


def finished_tasks(limit=10):
    rows = task_log.recent(limit)
    spent = _spent(rows, "id")
    return [{**t, "cost": spent.get(t["id"], 0)} for t in rows]


def task(task_id):
    """One task for the page: from the log (with its cost, read from its start) or from the currently running ones."""
    for t in task_log.items:
        if t["id"] == task_id:
            return {**t, "done": True, "cost": _spent([t], "id").get(task_id, 0)}
    for ident, t in list(status.tasks.items()):
        if str(ident) == str(task_id) or t.get("id") == task_id:
            spent = costs.by_task([t["id"]], since=t.get("start")).get(t["id"], 0) if t.get("id") else 0
            return {**{k: t[k] for k in ("id", "text", "start", "steps", "next") if k in t}, "thread": str(ident), "done": False, "answer": None, "comments": [],
                    "cost": spent, "service": bool(t.get("service"))}
    return {"error": "Такой задачи нет"}


def search(q):
    """Search across everything through the registry — the same find the model uses: notes, tasks, messages, facts, people, files,
    with what each is linked to. Chunks of one note merge into one row."""
    out, seen = [], set()
    for r in recall.find_rows(q, k=40, with_events=False).get("rows", []):
        item = {"kind": r["kind"], "ref": r["ref"], "text": r["text"], "time": r["time"], "tz": r.get("tz", ""), "meta": r.get("meta") or {},
                "links": r.get("links", []), "score": round(r["score"], 4)}
        if r["kind"] == vault.KIND:
            path = r["ref"].rsplit("#", 1)[0]   # "<path>#<chunk>": a note's own name may hold a "#" too
            if path in seen:
                continue
            seen.add(path)
            item.update(path=path, title=path.rsplit("/", 1)[-1][:-3], text=r["text"].split(": ", 1)[-1])
        out.append(item)
    return out


def _size(path):
    path = str(path)
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def probe(func, default=None):
    """The result of func(), or default if it failed: one part of the overview shouldn't bring down the whole response."""
    try:
        return func()
    except Exception:
        return default


def state():
    """How much disk the data takes, by part (the Mac's settings screen). The rest of the system's state lives in the desk's
    overview: what was here besides — today's counts, the index, the integrations — no screen read (25.10)."""
    return {"now": time.time(),
            "disk": {"события": _size(config.EVENTS_DB), "индекс поиска": _size(config.INDEX_DB), "записи": _size(config.RECORDS_DB), "скачанное": _size(config.DOWNLOADS_DIR), "сделанное": _size(config.GENERATED_DIR),
                     "присланное": _size(config.INBOX_DIR), "заметки": _size(config.VAULT_DIR), "копии": _size(config.BACKUPS_DIR)}}
