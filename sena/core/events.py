"""Event journal: everything the system does, one entry per action, forever.
Tema's messages and replies (Telegram, the app, voice), the start and end of tasks, every step, every model call
(what went out and what came back, including Claude Code and images with video), every tool (arguments, result, time),
every request to data sources, the agent's self-assessments, errors with a trace, scheduled jobs, the judge's verdicts and
Tema's remarks, service agents, messages to people on Tema's behalf, actions from the app.
Stored in SQLite data/journal/events.db (WAL, append-only, never cleaned up) with a full-text FTS5 index:
search by words, kind, task and time — instant even across hundreds of thousands of entries. The context (who's working,
which task, which turn) is filled in automatically from status. Seen in the apps' journal (phone/desk.py); from the command
line: python -m sena.core.events [period] [words]."""
import json
import re
import sqlite3
import threading
import time
import traceback
from collections import Counter
from datetime import datetime

from sena import config
from sena.core import costs, status, store

TEXT_LIMIT = 4000   # long text is trimmed: the meaning stays, the database doesn't bloat; a note at the end says how much there was
LINES_LIMIT = 150   # how many lines to show in the report
# every kind of event the journal records, with its word for the owner: the one table the apps take their labels from
KINDS = {
    "start": "старт", "message_in": "сообщение", "message_out": "ответ", "file_out": "файл", "task_start": "задача",
    "task_end": "конец задачи", "step": "шаг", "model": "модель", "media": "медиа", "tool": "инструмент", "check": "проверка",
    "stop": "стоп", "error": "ошибка", "job": "задание", "heartbeat": "фон", "watch": "слежка", "verdict": "оценка",
    "rating": "отметка Темы", "comment": "замечание", "app": "приложение", "transcribe": "речь→текст", "speech": "озвучка",
    "embeddings": "отпечатки", "fetch": "запрос", "download": "скачивание", "sync": "сверка", "recipe": "рецепт",
    "heard": "услышано", "telegram_held": "в Телеграм не ушло", "voice": "голос", "person": "человек", "personality": "характер", "alert": "сигнал",
    "history": "переписка", "google": "google", "browser": "браузер", "cleanup": "чистка", "backup": "копия", "rule": "правило",
    "update": "обновление", "review": "разбор", "work": "долгая работа", "retry": "повтор", "effect": "действие", "mcp": "MCP",
    "secret": "секрет", "attention": "внимание", "code": "код", "exam": "экзамен", "goal": "цель", "lesson": "урок",
    "outside": "чужой текст", "read": "чтение",
}
_db = None
_lock = threading.Lock()
errors = []    # what failed to record (for checks)
mask_hook = None   # the keeper of secrets subscribes here (core/secrets.py): known values never reach the journal


def _connect():
    # the connection is kept only once its tables are made: a half-made one would be reused by every later log until a restart
    global _db
    if _db is None:
        db = sqlite3.connect(config.EVENTS_DB, check_same_thread=False)
        try:
            _prepare(db)
        except BaseException:
            db.close()
            raise
        _db = db
    return _db


def _prepare(db):
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")   # a power failure might lose the last fraction of a second, but won't corrupt the database
    db.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY, t REAL NOT NULL, kind TEXT NOT NULL, who TEXT, tid TEXT, turn INTEGER,
            text TEXT NOT NULL, keys TEXT NOT NULL DEFAULT '', data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS events_t ON events(t);
        CREATE INDEX IF NOT EXISTS events_kind ON events(kind, t);
        CREATE INDEX IF NOT EXISTS events_tid ON events(tid);
        CREATE INDEX IF NOT EXISTS events_who ON events(who, id);
        CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(text, keys, content='events', content_rowid='id', tokenize='trigram');
        CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
            INSERT INTO events_fts(rowid, text, keys) VALUES (new.id, new.text, new.keys); END;
    """)
    if "keys" not in {r[1] for r in db.execute("PRAGMA table_info(events)")}:   # old-style journal: the index was built over all details
        db.executescript("""
            ALTER TABLE events ADD COLUMN keys TEXT NOT NULL DEFAULT '';
            DROP TRIGGER IF EXISTS events_ai;
            DROP TABLE IF EXISTS events_fts;
            CREATE VIRTUAL TABLE events_fts USING fts5(text, keys, content='events', content_rowid='id', tokenize='trigram');
            CREATE TRIGGER events_ai AFTER INSERT ON events BEGIN
                INSERT INTO events_fts(rowid, text, keys) VALUES (new.id, new.text, new.keys); END;
            INSERT INTO events_fts(events_fts) VALUES ('rebuild');
        """)


def _reset():
    global _db
    with _lock:
        if _db is not None:
            _db.close()
        _db = None
        errors.clear()
        _last.clear()


def checkpoint():
    """Before shutting down: flush the WAL tail into the main file, so the journal's copy is complete."""
    with _lock:
        if _db is not None:
            _db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


MARGIN = 4000   # what is masked past TEXT_LIMIT before the cut: a secret (in any of its forms) that starts before the cut ends inside it


def _cut(value):
    # text and nested structures within reasonable limits; bytes — only their size. A string is masked before it is cut: a key
    # starting a few characters before the limit was cut first, and the masking, which looks for the whole value, left its head
    if isinstance(value, str):
        total = len(value)
        if mask_hook and total >= 8:   # a known secret is never shorter than 8; what lies past the margin is cut away anyway
            value = mask_hook(value[:TEXT_LIMIT + MARGIN])
        return value if total <= TEXT_LIMIT and len(value) <= TEXT_LIMIT else value[:TEXT_LIMIT] + f"… [обрезано, всего {total} знаков]"
    if isinstance(value, bytes):
        return f"(данные, {len(value)} байт)"
    if isinstance(value, dict):
        return {str(k): _cut(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_cut(v) for v in value[:200]]
    return value


def _size(value):
    # instead of private content — only its size
    if isinstance(value, str):
        return f"(приватно, {len(value)} знаков)"
    if isinstance(value, (list, dict)):
        return f"(приватно, {len(value)} частей)"
    return value


def log(kind, text="", **fields):
    """Record an event: kind — what happened (see KINDS), text — the gist in words, fields — details.
    who and tid are taken from the current thread (status); they can also be passed explicitly. private=True — content isn't
    stored, only sizes (someone else's speech around Tema). Never raises for the caller."""
    try:
        task = status.current() or {}
        who = fields.pop("who", None) or getattr(status.local, "who", None)
        tid = fields.pop("tid", None) or task.get("id")
        private = fields.pop("private", False)
        turn = getattr(status.local, "turn", None)
        text = text if isinstance(text, str) else str(text)
        if private:
            text = _size(text)
            fields = {k: (_size(v) if k in ("input", "text", "calls", "args") else v) for k, v in fields.items()}
        fields.setdefault("run", getattr(status.local, "run", None))   # which run of an agent: two browser runs of one task stay apart
        # the keeper of secrets wipes known values out of everything the journal records (_cut): each string on its own, before
        # packing — inside JSON a value with " or \ is written escaped, and the packed text no longer held it as it is
        data = {k: _cut(v) for k, v in fields.items() if v is not None}
        text = _cut(text)
        keys = " ".join(str(v) for k, v in data.items() if isinstance(v, str) and len(v) <= 80 and k not in ("trace", "run"))   # short fields go into the word search
        entry = {"t": time.time(), "kind": kind, "who": who, "tid": tid, "turn": turn, "text": text, **data}
        packed = json.dumps(data, ensure_ascii=False, default=str)
        if mask_hook:   # and once more over what was packed: a value only str() turned into text (default=str)
            packed = mask_hook(packed)
        with _lock:
            db = _connect()
            db.execute("INSERT INTO events(t, kind, who, tid, turn, text, keys, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (entry["t"], kind, who, tid, turn, entry["text"], keys, packed))
            db.commit()
        return entry
    except Exception as e:
        errors.append(f"{kind}: {e}")
        print("Журнал событий:", kind, e)


def error(where, exc):
    """An error with a trace: where it happened and what exactly. Recorded wherever the error was caught."""
    return log("error", f"{where}: {exc}", place=where, error=type(exc).__name__, trace=traceback.format_exc())


store.broken_hook = lambda text: log("error", text, place="данные", error="повреждённый файл")


# ---------- reading ----------
def _fts(query):
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) >= 3]   # trigrams: words shorter than three letters aren't searched
    return " AND ".join(f'"{w}"' for w in dict.fromkeys(words)) if words else None


def _where(period, kind="", tid="", since=None, until=None, who=""):
    if since is None and until is None:
        since, until, _ = costs.span(period)
    where, params = [], []
    if since is not None:
        where.append("e.t >= ?")
        params.append(since)
    if until is not None:
        where.append("e.t < ?")
        params.append(until)
    if kind:
        where.append("e.kind = ?")
        params.append(kind)
    if tid:
        where.append("e.tid = ?")
        params.append(tid)
    if who:   # the performer and its sub-threads "ag_web/…": a string range rather than LIKE (agent codes contain "_")
        where.append("(e.who = ? OR (e.who >= ? AND e.who < ?))")
        params += [who, who + "/", who + "0"]
    return where, params


def count(period="today", kind="", since=None, until=None):
    where, params = _where(period, kind, since=since, until=until)
    with _lock:
        return _connect().execute("SELECT COUNT(*) FROM events e" + (" WHERE " + " AND ".join(where) if where else ""), params).fetchone()[0]


def counts(period="today", since=None, until=None, query="", tid="", who=""):
    """How many events of each kind during the period: {kind: count} (console). Same filters as search, except kind."""
    where, params = _where(period, tid=tid, since=since, until=until, who=who)
    fts = _fts(query)
    if query.strip() and not fts:
        return {}
    if fts:
        where.append("e.id IN (SELECT rowid FROM events_fts WHERE events_fts MATCH ?)")
        params.append(fts)
    with _lock:
        try:
            rows = _connect().execute("SELECT kind, COUNT(*) FROM events e" + (" WHERE " + " AND ".join(where) if where else "") + " GROUP BY kind", params).fetchall()
        except sqlite3.OperationalError:   # special characters in the query
            return {}
    return dict(rows)


def search(period="today", query="", kind="", tid="", limit=None, since=None, until=None, who=""):
    """Events for the period (or from since to until), oldest first; query — words that must all appear; kind — the kind;
    tid — the task; who — the performer (filtered before limit, not after); limit — only this many most recent."""
    where, params = _where(period, kind, tid, since, until, who)
    fts = _fts(query)
    if query.strip() and not fts:
        return []
    if fts:
        where.append("e.id IN (SELECT rowid FROM events_fts WHERE events_fts MATCH ?)")
        params.append(fts)
    sql = "SELECT e.t, e.kind, e.who, e.tid, e.turn, e.text, e.data FROM events e" + (" WHERE " + " AND ".join(where) if where else "")
    sql = f"SELECT * FROM ({sql} ORDER BY e.id DESC LIMIT {int(limit)}) ORDER BY t" if limit else sql + " ORDER BY e.id"
    with _lock:
        try:
            rows = _connect().execute(sql, params).fetchall()
        except sqlite3.OperationalError:   # special characters in the query
            return []
    return [{"t": t, "kind": k, "who": who, "tid": tid_, "turn": turn, "text": text, **json.loads(data)} for t, k, who, tid_, turn, text, data in rows]


_FIELD = re.compile(r"^\w+$")
# (kind, field, values) -> (the moment looked up to, the last such event's time or None). The journal only grows, so a later
# look reads only what came after (and the last minute again, for an event that waited for the lock): the walk back through
# every event of a kind — a tool never used — took a third of a second at 250 000 events, every few seconds from the apps
_last = {}


def tally(kind, field=None, want=(), since=None):
    """(how many events of kind since since, the time of the last one ever) — without reading the details.
    field/want — count only events where field (text or a detail field) is in want."""
    cond, params = "e.kind = ?", [kind]
    if field:
        if not _FIELD.match(field):
            raise ValueError(f"недопустимое поле: {field}")
        column = "e.text" if field == "text" else f"json_extract(e.data, '$.{field}')"
        cond += f" AND {column} IN ({','.join('?' * len(want))})"
        params += list(want)
    key = (kind, field, tuple(sorted(map(str, want))))
    with _lock:
        db = _connect()
        looked, last = _last.get(key, (None, None))
        now = time.time()
        newer = (" AND e.t > ?", [looked - 60]) if looked is not None else ("", [])
        row = db.execute(f"SELECT e.t FROM events e WHERE {cond}{newer[0]} ORDER BY e.t DESC LIMIT 1", params + newer[1]).fetchone()
        last = max(row[0], last or 0) if row else last
        _last[key] = (now, last)
        number = db.execute(f"SELECT COUNT(*) FROM events e WHERE {cond}" + (" AND e.t >= ?" if since is not None else ""),
                            params + ([since] if since is not None else [])).fetchone()[0]
    return number, last


def brief(period="today", kind="", limit=100):
    """Only the time and text of recent events, without details or traces; oldest first."""
    where, params = _where(period, kind)
    sql = "SELECT e.t, e.text FROM events e" + (" WHERE " + " AND ".join(where) if where else "")
    with _lock:
        rows = _connect().execute(f"SELECT * FROM ({sql} ORDER BY e.id DESC LIMIT {int(limit)}) ORDER BY 1", params).fetchall()
    return [{"t": t, "text": text} for t, text in rows]


def _line(r):
    when = datetime.fromtimestamp(r["t"]).strftime("%d.%m %H:%M:%S")
    who = r.get("who") or "система"
    text = (r.get("text") or "").replace("\n", " ")[:220]
    if r["kind"] == "tool":
        args = json.dumps(r.get("args", {}), ensure_ascii=False)[:160]
        text = f"{r.get('name')}({args}) → {text}" + (f" [{r['seconds']} с]" if r.get("seconds") else "")
    elif r["kind"] == "model":
        calls = ", ".join(c.get("name", "?") for c in r.get("calls", []))
        text = f"{r.get('model')}: {text}" + (f" → инструменты: {calls}" if calls else "")
    elif r["kind"] == "error":
        text = f"{text} ({r.get('error')})"
    elif r["kind"] == "verdict":
        text = f"{r.get('verdict')} — {text}"
    tid = f" #{r['tid']}" if r.get("tid") else ""
    return f"{when} {who}{tid} {r['kind']}: {text}"


def report(period="today", query="", kind="", tid=""):
    """A report in words for the command line: how much of what there was (counted by the database), and the most recent
    lines — the period's rows are no longer all read to show the last LINES_LIMIT of them ("all" read the whole journal)."""
    try:
        _, _, title = costs.span(period)
    except ValueError as e:
        return str(e)
    head = f"Журнал событий: {title}" + (f", слова «{query}»" if query else "") + (f", вид {kind}" if kind else "") + (f", задача {tid}" if tid else "")
    found = Counter({k: n for k, n in counts(period, query=query, tid=tid).items() if not kind or k == kind})
    total = sum(found.values())
    if not total:
        return head + ". Ничего не найдено."
    rows = search(period, query, kind, tid, limit=LINES_LIMIT)
    lines = [head + f". Всего {total}: " + ", ".join(f"{k} {n}" for k, n in found.most_common()) + "."]
    if total > len(rows):
        lines.append(f"Показаны последние {len(rows)}; уточни слова или вид, чтобы увидеть остальное.")
    lines += [_line(r) for r in rows]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    print(report(sys.argv[1] if len(sys.argv) > 1 else "today", " ".join(sys.argv[2:])))
