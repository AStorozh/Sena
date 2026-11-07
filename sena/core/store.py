"""Reading and writing Sena's data. Writes are atomic: nothing is ever half-written, even if the process crashes midway.
Records — what Sena builds up and nobody edits by hand (config.RECORDS: people, rules, tasks, conversations, overheard speech,
files, links, jobs, alerts, the agents' lessons) — live in one SQLite database, data/state/sena.db; their path is only their
name there. Everything else (memory, personality, skills, settings, notes) stays a file. Both kinds keep their past: a versioned
write puts the previous content aside first (the versions table, or <file>.history.jsonl), so a change never destroys what was
there — only supersedes it. Files that existed before the database are taken in once and set aside as <name>.moved-<time>."""
import difflib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from sena import config
from sena.core import moment

broken_hook = None   # who to notify about a corrupted file (the event journal subscribes at startup)
_db = None
_db_lock = threading.RLock()


# ---------- the records database ----------
def _record_key(path):
    """The record's name in the database (its path under data/), or None if this path is an ordinary file."""
    path = Path(os.path.abspath(path))
    for record in config.RECORDS:
        record = Path(os.path.abspath(record))
        if path == record or record in path.parents:
            return str(path.relative_to(Path(os.path.abspath(config.DATA))))
    return None


def _connect():
    global _db
    with _db_lock:
        if _db is None:
            _db = _open()
        return _db


def _open():
    target = Path(config.RECORDS_DB)
    target.parent.mkdir(parents=True, exist_ok=True)
    fresh = not target.exists()
    db = sqlite3.connect(target, timeout=30, check_same_thread=False, isolation_level=None)
    try:
        _ready(db)
    except sqlite3.OperationalError:   # locked, the disk full, no permission: the database itself may be whole — never "recovered"
        db.close()
        raise
    except sqlite3.DatabaseError as e:   # the file is no database or a broken one
        db = _recover(target, e, db)
    else:
        if fresh:
            os.chmod(target, 0o600)   # people, conversations, overheard speech: closed to other users of the Mac
    _take_in_files(db)
    return db


def _ready(db):
    # the shared connection's settings, and a first read that proves the file is a whole database
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA secure_delete=ON")   # what is deleted (the owner's "forget") is overwritten, not left readable in free pages
    _schema(db)
    db.execute("SELECT count(*) FROM docs").fetchone()


def _recover(target, error, failed):
    """The database can't be read: it goes aside with its write-ahead journal as a pair (never deleted), and work starts from
    the newest nightly copy that is whole, older ones after it, or from an empty one when none is. The pair moves before the
    failed handle closes — closing it deletes the journal, the broken database's last pages — and nothing of it stays beside the
    copy: SQLite would replay it onto the copy ("mispairing a database and its journal": the audit of 25.10 got a copy that
    failed its integrity check). The aside name is never taken twice: a damaged copy recovered from in the same second once
    went aside over the original, the one file with that day's pages."""
    aside = _aside(target, "-wal", "-shm")
    for part in ("", "-wal", "-shm"):
        if Path(f"{target}{part}").exists():
            os.replace(f"{target}{part}", f"{aside}{part}")
    failed.close()
    db, taken, damaged = None, None, []
    for copy in sorted(Path(config.BACKUPS_DIR).glob("*_sena.db"), reverse=True):
        shutil.copy(copy, target)
        db = _whole(target)
        if db is not None:
            taken = copy
            break
        damaged.append(copy.name)
        for part in ("", "-wal", "-shm"):   # the working copy of a damaged one: the nightly file itself stays where it is
            Path(f"{target}{part}").unlink(missing_ok=True)
    if db is None:
        db = sqlite3.connect(target, timeout=30, check_same_thread=False, isolation_level=None)
        try:
            _ready(db)
        except BaseException:
            db.close()
            raise
    os.chmod(target, 0o600)
    text = (f"База записей повреждена ({error}); отложена как {aside.name}, " + (f"взята ночная копия {taken.name}" if taken else "начинаю с пустой")
            + (f" (не открылись копии: {', '.join(damaged)})" if damaged else ""))
    if broken_hook:
        broken_hook(text)
    else:
        print(text)
    return db


def _aside(path, *parts):
    # a name to set a broken file aside under (with its -wal and -shm, for a database) that no earlier one took
    stamp, n = time.strftime("%Y%m%d-%H%M%S"), 1
    aside = path.with_name(f"{path.name}.broken-{stamp}")
    while any(Path(f"{aside}{part}").exists() for part in ("", *parts)):
        n += 1
        aside = path.with_name(f"{path.name}.broken-{stamp}.{n}")
    return aside


def _whole(target):
    """The database at target opened with the shared connection's settings when it is whole (it opens and SQLite's quick check
    finds its pages fine), otherwise None. A lock or a full disk is no verdict on the copy: it raises."""
    db = sqlite3.connect(target, timeout=30, check_same_thread=False, isolation_level=None)
    try:
        _ready(db)
        if db.execute("PRAGMA quick_check").fetchone()[0] == "ok":
            return db
    except sqlite3.OperationalError:
        db.close()
        raise
    except sqlite3.DatabaseError:
        pass
    db.close()
    return None


def _schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS docs (key TEXT PRIMARY KEY, text TEXT NOT NULL, t REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS versions (id INTEGER PRIMARY KEY, key TEXT NOT NULL, t REAL NOT NULL, tz TEXT, text TEXT);
        CREATE INDEX IF NOT EXISTS versions_key ON versions(key, id);
        CREATE TABLE IF NOT EXISTS lines (id INTEGER PRIMARY KEY, key TEXT NOT NULL, line TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS lines_key ON lines(key, id);
        CREATE TABLE IF NOT EXISTS moved (key TEXT PRIMARY KEY, t REAL NOT NULL, source TEXT NOT NULL);""")
    return db


def _take_in_files(db):
    """Once: records that still lie as files (written before the database existed) go into it, with their history;
    the files are set aside next to themselves, not deleted."""
    found = []
    for record in config.RECORDS:
        record = Path(record)
        if record.is_dir():
            found += [p for p in sorted(record.iterdir()) if p.is_file() and p.suffix in (".json", ".jsonl") and not p.name.endswith(".history.jsonl")]
        elif record.is_file():
            found.append(record)
    if not found:
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    db.execute("BEGIN IMMEDIATE")
    try:
        for path in found:
            key = _record_key(path)
            text = path.read_text(encoding="utf-8", errors="replace")
            if path.suffix == ".jsonl":
                db.executemany("INSERT INTO lines (key, line) VALUES (?, ?)", [(key, line) for line in text.splitlines() if line.strip()])
            elif db.execute("SELECT 1 FROM docs WHERE key = ?", (key,)).fetchone():
                db.execute("INSERT INTO versions (key, t, tz, text) VALUES (?, ?, ?, ?)", (key, path.stat().st_mtime, moment.offset(), text))
            else:
                db.execute("INSERT INTO docs (key, text, t) VALUES (?, ?, ?)", (key, text, path.stat().st_mtime))
            past = history_path(path)
            if past.is_file():
                for entry in _parse_lines(past.read_text(encoding="utf-8", errors="replace")):
                    old = json.dumps(entry["data"], ensure_ascii=False) if "data" in entry else entry.get("text")
                    db.execute("INSERT INTO versions (key, t, tz, text) VALUES (?, ?, ?, ?)", (key, entry.get("t", 0), entry.get("tz", ""), old))
            db.execute("INSERT OR REPLACE INTO moved (key, t, source) VALUES (?, ?, ?)", (key, time.time(), str(path)))
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    for path in found:   # only after the database has them all
        os.replace(path, path.with_name(f"{path.name}.moved-{stamp}"))
        past = history_path(path)
        if past.is_file():
            os.replace(past, past.with_name(f"{past.name}.moved-{stamp}"))
    print(f"Записи перенесены в базу {Path(config.RECORDS_DB).name}: {len(found)} файлов (сами файлы отложены рядом)")


def _parse_lines(text):
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue   # a line cut off by a crash: skipped, the rest is intact
    return out


def _doc(key):
    row = _connect().execute("SELECT text FROM docs WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _lines_of(key):
    return [line for (line,) in _connect().execute("SELECT line FROM lines WHERE key = ? ORDER BY id", (key,))]


def _apart():
    """A connection of its own for a long read (the nightly copy, the integrity check): in WAL it sees one moment of the
    database while the shared connection goes on reading and writing — nothing waits for the whole copy."""
    if _db is None:
        _connect()   # opened (taken in, recovered if broken) before anything reads it apart
    return sqlite3.connect(config.RECORDS_DB, timeout=30)


def snapshot(target):
    """A consistent copy of every record in one file (nightly backups, the exam's throwaway world)."""
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    db = _apart()
    try:
        db.execute("VACUUM INTO ?", (str(target),))
    finally:
        db.close()
    return target


def integrity():
    """"ok", or what SQLite found wrong with the records database."""
    db = _apart()
    try:
        return db.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        db.close()


def close():
    """Let go of the database (tests swap the data folder; the nightly copy is taken from a closed handle too)."""
    global _db
    with _db_lock:
        if _db is not None:
            _db.close()
            _db = None


def load_json(path, default):
    """The contents of a JSON file or record, or default. A broken file (hand-edited, disk failure) doesn't crash the startup:
    it's set aside next to it under the name <name>.broken, and work continues with empty data."""
    key = _record_key(path)
    if key:
        with _db_lock:
            text = _doc(key)
            if text is None:
                return default
            try:
                return json.loads(text)
            except ValueError as e:   # never expected: the database only takes what save_json wrote
                broken = e
                _keep_previous(path, None)   # its text goes to the versions once, and the record leaves: the next read is quiet
                _connect().execute("DELETE FROM docs WHERE key = ?", (key,))
        if broken_hook:
            broken_hook(f"Запись {key} не читается ({broken}): работа с пустыми данными, прежний текст сохранён в истории")
        return default
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        aside = _aside(path)   # earlier broken copies aren't overwritten, not even within a second
        os.replace(path, aside)
        text = f"Файл {path.name} повреждён ({e}); отложен как {aside.name}, работа с пустыми данными"
        if broken_hook:   # the journal has it; the output only when there is no journal yet
            broken_hook(text)
        else:
            print(text)
        return default


def write_text(path, text, mode=None):
    """Write the whole file via a temporary file. mode — permissions; without it, the file is closed to everyone but the owner.
    A file only Sena writes (her memory, its archive, personality, skills) is noted as hers, whichever write it came by."""
    key = _record_key(path)
    if key:
        with _db_lock:
            _connect().execute("INSERT INTO docs (key, text, t) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET text = excluded.text, t = excluded.t",
                               (key, text, moment.now()))
        return
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")   # a hidden temporary file
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    _known(path, text)


def save_json(path, data, indent=None):
    write_text(path, json.dumps(data, ensure_ascii=False, indent=indent))


def write_bytes(path, data, mode=None):
    """Write a binary file via a temporary file (the same atomicity as write_text)."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---------- versioned files: a change supersedes the old content, never destroys it ----------
def history_path(path):
    path = Path(path)
    return path.with_name(path.name + ".history.jsonl")


def _keep_previous(path, new_text):
    """The current content of path goes to its history before new_text replaces it.
    Returns False when nothing changes (identical content), so callers can skip the write too."""
    key = _record_key(path)
    if key:
        with _db_lock:
            old = _doc(key)
            if old == new_text:
                return False
            if old is not None:
                _connect().execute("INSERT INTO versions (key, t, tz, text) VALUES (?, ?, ?, ?)", (key, moment.now(), moment.offset(), old))
        return True
    path = Path(path)
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == new_text:
        return False
    if old is not None:
        entry = {"t": moment.now(), "tz": moment.offset()}
        try:
            entry["data"] = json.loads(old)   # JSON files keep their structure in the history
        except ValueError:
            entry["text"] = old
        append_jsonl(history_path(path), entry)
    return True


def write_versioned(path, text):
    with _db_lock:   # the version and the write in one step: two writers of one record or file can't lose a version between them
        if _keep_previous(path, text):
            write_text(path, text)


def save_versioned(path, data, indent=None):
    write_versioned(path, json.dumps(data, ensure_ascii=False, indent=indent))


def remove_versioned(path):
    """Delete a versioned file: its last content stays in the history."""
    key = _record_key(path)
    if key:
        with _db_lock:
            _keep_previous(path, None)
            _connect().execute("DELETE FROM docs WHERE key = ?", (key,))
        return
    path = Path(path)
    with _db_lock:
        if path.exists():
            _keep_previous(path, None)
            path.unlink()
            _known(path, None)


# ---------- what Sena alone writes: memory, personality, skills ----------
KNOWN_DIR = config.STATE_DIR / "known"   # a copy of what Sena last wrote into each such file
_known_lock = threading.Lock()


def _watched(path):
    """The name of a file that shapes how Sena thinks and that only she writes (her memory and its archive — both recalled into
    her answers by themselves — her personality, skills), or None."""
    path = Path(path)
    try:
        rel = path.relative_to(config.MEMORY_DIR)
    except ValueError:
        return None
    if rel.parts in (("memory.md",), ("archive.md",), ("personality.md",)) or (len(rel.parts) == 2 and rel.parts[0] == "skills" and path.suffix in (".md", ".py")):
        return str(rel)
    return None


def _known(path, text):
    rel = _watched(path)
    if not rel:
        return
    copy = KNOWN_DIR / rel
    with _known_lock:
        if text is None:
            copy.unlink(missing_ok=True)
        else:
            copy.parent.mkdir(parents=True, exist_ok=True)
            write_text(copy, text)


def outside_changes():
    """Files of her memory, personality and skills whose text isn't what Sena last wrote there: [(name, a short diff)]. A coding
    run works unsandboxed and could plant a line she would then follow (clawsec watches an agent's own instruction files). Each
    is told once: her own last version goes into the file's history, and what is there now becomes the known state. The first
    look only learns what is there, and so does the first look at one of her fixed files watched only since (archive.md since
    25.10.2026); a skill nobody here wrote is told — that is how a planted one looks."""
    fixed = [config.MEMORY_DIR / name for name in ("memory.md", "archive.md", "personality.md")]
    files = [*fixed, *sorted((config.MEMORY_DIR / "skills").glob("*"))]
    out = []
    with _known_lock:
        first = not (KNOWN_DIR / ".baseline").exists()
        for f in files:
            rel = _watched(f)
            if not rel or not f.is_file():
                continue
            now, copy = f.read_text(encoding="utf-8", errors="replace"), KNOWN_DIR / rel
            was = copy.read_text(encoding="utf-8") if copy.exists() else None
            if was == now:
                continue
            if not first and not (was is None and f in fixed):
                diff = [line for line in difflib.unified_diff((was or "").splitlines(), now.splitlines(), lineterm="", n=0)
                        if line[:1] in "+-" and not line.startswith(("+++", "---"))]
                out.append((rel, "\n".join(diff[:12]) + ("\n…" if len(diff) > 12 else "")))
                if was is not None:
                    append_jsonl(history_path(f), {"t": moment.now(), "tz": moment.offset(), "text": was, "why": "до правки не Сеной"})
            copy.parent.mkdir(parents=True, exist_ok=True)
            write_text(copy, now)
        if first:
            KNOWN_DIR.mkdir(parents=True, exist_ok=True)
            (KNOWN_DIR / ".baseline").write_text(str(moment.now()), encoding="utf-8")
    return out


def scrub(path, texts, mark="[забыто]"):
    """The owner asked to forget: these texts leave the earlier versions of a versioned file too (as JSON strings and as
    they are), so "forget" doesn't leave the words in the file's history. Returns how many versions changed."""
    forms = sorted({f for t in texts if t for f in (t, json.dumps(t, ensure_ascii=False)[1:-1])}, key=len, reverse=True)
    if not forms:
        return 0
    key = _record_key(path)
    if key:
        changed = 0
        with _db_lock:
            db = _connect()
            for vid, text in db.execute("SELECT id, text FROM versions WHERE key = ?", (key,)).fetchall():
                new = text
                for f in forms:
                    new = new.replace(f, mark) if new else new
                if new != text:
                    db.execute("UPDATE versions SET text = ? WHERE id = ?", (new, vid))
                    changed += 1
        return changed
    hist = history_path(path)
    if not hist.exists():
        return 0
    old = hist.read_text(encoding="utf-8")
    new = old
    for f in forms:
        new = new.replace(f, mark)
    if new != old:
        write_text(hist, new)
    return int(new != old)


def versions(path):
    """All earlier contents of a versioned file, oldest first: [{"t", "tz", "data" | "text"}]. For tests and a look by hand:
    Sena herself reads only the newest."""
    key = _record_key(path)
    if key:
        rows = []
        with _db_lock:
            found = _connect().execute("SELECT t, tz, text FROM versions WHERE key = ? ORDER BY id", (key,)).fetchall()
        for t, tz, text in found:
            entry = {"t": t, "tz": tz or ""}
            try:
                entry["data"] = json.loads(text)
            except (TypeError, ValueError):
                entry["text"] = text
            rows.append(entry)
        return rows
    hist = history_path(path)
    if not hist.exists():
        return []
    rows = []
    for line in hist.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue   # a line cut off by a crash: skipped, the rest is intact
    return rows


def read_text(path):
    key = _record_key(path)
    if key:
        with _db_lock:
            text = _doc(key)
            if text is None:
                found = _lines_of(key)
                text = "\n".join(found) + "\n" if found else ""
        return text
    path = Path(path)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def lines(path):
    """The entries of a line-based journal (a record or a JSONL file), oldest first; a torn line is skipped."""
    key = _record_key(path)
    if key:
        with _db_lock:
            return _parse_lines("\n".join(_lines_of(key)))
    path = Path(path)
    return _parse_lines(path.read_text(encoding="utf-8")) if path.exists() else []


def exists(path):
    key = _record_key(path)
    if key:
        with _db_lock:
            db = _connect()
            return bool(db.execute("SELECT 1 FROM docs WHERE key = ?", (key,)).fetchone() or db.execute("SELECT 1 FROM lines WHERE key = ? LIMIT 1", (key,)).fetchone())
    return Path(path).exists()


def stamp(path):
    """Something that changes whenever the record or file changes (for callers that cache what they read)."""
    key = _record_key(path)
    if key:
        with _db_lock:
            db = _connect()
            return (db.execute("SELECT count(*), max(id) FROM lines WHERE key = ?", (key,)).fetchone(),
                    db.execute("SELECT t, length(text) FROM docs WHERE key = ?", (key,)).fetchone())
    try:
        st = Path(path).stat()
        return st.st_size, st.st_mtime_ns
    except FileNotFoundError:
        return None


def clear(path):
    """Empty a record or delete a file, for tests (a versioned record keeps its history). The owner's "forget" never comes here:
    overheard speech goes through heard.forget, the conversation into its archive."""
    key = _record_key(path)
    if key:
        with _db_lock:
            db = _connect()
            db.execute("DELETE FROM docs WHERE key = ?", (key,))
            db.execute("DELETE FROM lines WHERE key = ?", (key,))
        return
    Path(path).unlink(missing_ok=True)


def append_jsonl(path, entry):
    """Append a JSON line to a line-based journal. If the previous entry was cut off without a trailing newline (an abrupt
    shutdown), the new one won't get glued onto it."""
    key = _record_key(path)
    if key:
        with _db_lock:
            _connect().execute("INSERT INTO lines (key, line) VALUES (?, ?)", (key, json.dumps(entry, ensure_ascii=False)))
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as f:
        torn = False
        if f.tell() > 0:
            f.seek(-1, 2)
            torn = f.read(1) != b"\n"
        f.write((b"\n" if torn else b"") + (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))


def trim_jsonl(path, keep):
    """Keep only the line-based (JSONL) entries for which keep(entry) is true. Returns how many were removed."""
    key = _record_key(path)
    if key:
        with _db_lock:
            db = _connect()
            doomed = []
            for row_id, line in db.execute("SELECT id, line FROM lines WHERE key = ?", (key,)).fetchall():
                try:
                    if not keep(json.loads(line)):
                        doomed.append((row_id,))
                except (ValueError, KeyError, TypeError):
                    continue   # leave an unparseable line alone: cleanup shouldn't lose what it didn't understand
            db.executemany("DELETE FROM lines WHERE id = ?", doomed)
        return len(doomed)
    path = Path(path)
    if not path.exists():
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = []
    for line in lines:
        try:
            if keep(json.loads(line)):
                kept.append(line)
        except (ValueError, KeyError, TypeError):
            kept.append(line)   # leave an unparseable line alone: cleanup shouldn't lose what it didn't understand
    if len(kept) < len(lines):
        write_text(path, "\n".join(kept) + ("\n" if kept else ""))
    return len(lines) - len(kept)


def safe_name(name, default="file"):
    """A filename without dangerous characters: spaces and anything else become "_", leading dots are stripped."""
    return re.sub(r"[^\w.\-]+", "_", name).strip("._") or default


def stamped(folder, name):
    """The path for a new file in the folder: the save time plus a safe name, so identical names don't overwrite each other."""
    stem, dot, ext = safe_name(name).rpartition(".")
    base = (stem if dot else ext)[:100] + (dot + ext[:12] if dot else "")   # a very long name shouldn't break writing the file
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = Path(folder) / f"{stamp}-{base}"
    n = 1
    while path.exists():   # two files with the same name in the same second don't overwrite each other
        n += 1
        path = Path(folder) / f"{stamp}.{n}-{base}"   # the counter goes into the timestamp: the name after it (used to label the file) doesn't change
    return path
