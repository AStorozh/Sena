"""Overheard speech around Tema: the raw lines (data/journal/convo_log.jsonl, append-only, kept for good by the owner's decision)
and their records in the registry (kind "услышано"). A line is searchable only when asked for — by the kind, by the person who
said it, by the period — never mixed into a reply unasked (see index.KINDS). The only way out is forget(): the owner's explicit
command, line by line into the journal of events. The sound itself is never stored anywhere."""
import hashlib
import json
import re
import threading

from sena import config
from sena.core import events, moment, store
from sena.memory import index, people

KIND = "услышано"
_lock = threading.RLock()   # one writer at a time into the raw log: the phone's lines and forget() must not overlap; reentrant for the migration
PLACE = re.compile(r"l\d{1,9}")   # how a line without an id was named once: by its place in the log ("l12"); a content id is "l" + ten hex digits
_migrated = False


def append(entry):
    """A recognized line from the phone: into the raw log, then into the registry."""
    with _lock:
        store.append_jsonl(config.EARS_LOG, entry)
    register(entry)


def register(entry):
    when = moment.parse(entry.get("time"))
    index.put(KIND, entry["id"], entry["text"], when, entry.get("tz", ""), {"pid": entry.get("pid", ""), "who": entry.get("who", "")})
    if people.by_id(entry.get("pid", "")):
        index.link(KIND, entry["id"], "человек", entry["pid"], "said_by", when, keep=False)


def assign(entries, voice, pid):
    """The review named an unknown voice (gN): its lines in this conversation now belong to the person. The raw log is append-only,
    so the correction is a line of its own ({"fix": id, "pid": ...}), applied whenever the log is read."""
    name = (people.by_id(pid) or {}).get("name") or pid
    with _lock:
        for e in entries:
            if e.get("pid") == voice:
                store.append_jsonl(config.EARS_LOG, {"fix": e["id"], "pid": pid, "who": name})
                e["pid"], e["who"] = pid, name
    for e in entries:
        if e.get("pid") == pid:
            register(e)


def moved(drop, keep):
    """Two cards turned out to be one person (people.merge): the lines said under the dropped card belong to the kept one —
    a correction line each, so "forget what he said" reaches them too. Returns how many."""
    name = (people.by_id(keep) or {}).get("name") or keep
    with _lock:
        mine = [e for e in entries() if e.get("pid") == drop]
        for e in mine:
            store.append_jsonl(config.EARS_LOG, {"fix": e["id"], "pid": keep, "who": name})
            e["pid"], e["who"] = keep, name
    for e in mine:
        register(e)
    return len(mine)


def _legacy_id(e):
    # a line from before ids were kept is named by what it holds, not by where it lies: forgetting a line before it must not
    # hand its name, and the corrections made under that name, to the next one
    return "l" + hashlib.md5(f"{e.get('time')}|{e.get('text')}".encode()).hexdigest()[:10]


def _parsed():
    # [(place in the log, entry)] — the place counts every line, as the old names did; a line cut off by a crash is skipped
    out = []
    for n, line in enumerate(store.read_text(config.EARS_LOG).splitlines()):
        try:
            out.append((n, json.loads(line)))
        except ValueError:
            continue
    return out


def _migrate():
    """Once per process: corrections and learned facts that still name a line without an id by its place in the log ("l12")
    move to the line's content id. A place drifts as soon as forget() trims a line before it, and the correction then landed
    on someone else's words — Ольга's lines lost her, and a stranger's became hers for the next «забудь, что говорила Ольга».
    The newer correction of a line still wins; a log the old forget() had trimmed already can't be put right, only stopped."""
    global _migrated
    with _lock:
        if _migrated:
            return
        rows = _parsed()
        places = {f"l{n}": _legacy_id(e) for n, e in rows if "fix" not in e and e.get("text") and "id" not in e}
        by_place = [e for _, e in rows if "fix" in e and PLACE.fullmatch(str(e["fix"]))]
        by_id = {e["fix"] for _, e in rows if "fix" in e and not PLACE.fullmatch(str(e["fix"]))}
        if places:
            people.rename_sources(places)   # the facts learned from such lines name them the same way
        moved = {}
        for fix in by_place:   # in log order: the last correction of a place wins, as when the log is read
            target = places.get(fix["fix"])
            if target and target not in by_id:   # a correction by content id already overrides any by place
                moved[target] = {"fix": target, "pid": fix.get("pid", ""), "who": fix.get("who", "")}
        for entry in moved.values():   # appended first, the old ones trimmed after: a crash in between leaves both, never neither
            store.append_jsonl(config.EARS_LOG, entry)
        if by_place:
            store.trim_jsonl(config.EARS_LOG, lambda e: not ("fix" in e and PLACE.fullmatch(str(e["fix"]))))
            events.log("cleanup", f"подслушанное: поправок по месту строки переведено на её содержание: {len(moved)}", moved=len(moved))
        _migrated = True


def _read():
    # the lines with corrections applied (by the line's id: a line from before ids were kept is named by what it holds)
    _migrate()
    rows, fixes = [], {}
    for _, e in _parsed():
        if "fix" in e:
            fixes[e["fix"]] = e
        elif e.get("text"):
            e.setdefault("id", _legacy_id(e))
            rows.append(e)
    for e in rows:
        fix = fixes.get(e["id"])
        if fix:
            e["pid"], e["who"] = fix["pid"], fix.get("who", e.get("who", ""))
    return rows


def entries():
    """Every line of the raw log, oldest first, with corrections applied. Lines from before ids were kept get one from their content."""
    return _read()


def sync_index():
    """Registry rows from the raw log (startup, night, after the registry was set aside as broken)."""
    rows = entries()
    report = index.sync(KIND, [(e["id"], e["text"], moment.parse(e.get("time")), {"pid": e.get("pid", ""), "who": e.get("who", "")}) for e in rows])
    for e in rows:
        if people.by_id(e.get("pid", "")):
            index.link(KIND, e["id"], "человек", e["pid"], "said_by", moment.parse(e.get("time")), keep=False)
    return report


def forget(person="", since=None, until=None):
    """The owner's command: remove overheard lines of a person and/or a period from the raw log and the registry. Returns the count.
    Nothing else ever deletes these lines."""
    pid, found = None, []
    if person:
        found = [person] if people.by_id(person) else index.matches("человек", person)
        if len(found) > 1:   # two Olgas: "the first" lost the other's words or kept them — the owner says whose
            return f"Под «{person}» подходят: {', '.join(people.title(r) for r in found[:8])}. Ничего не забыла: назови, чьё именно"
        if not found:
            return f"Не знаю человека «{person}»"
        pid = found[0]
    if not person and since is None and until is None:
        return "Назови человека или период: всё подряд не забываю"

    def doomed(e):   # judged on the corrected lines: whose words they are now, after the review named the voices
        when = moment.parse(e.get("time"))
        return (not pid or e.get("pid") == pid) and (since is None or when >= since) and (until is None or when <= until)

    with _lock:   # nothing is appended between the reading and the trimming
        gone = [e for e in _read() if doomed(e)]
        ids = {e["id"] for e in gone}
        # the raw lines go by id, each with its corrections: a raw line keeps the pid it was heard with, so judging the raw
        # lines kept a line the review had given her and took one it had taken from her for someone else
        store.trim_jsonl(config.EARS_LOG, lambda e: (e["fix"] if "fix" in e else e.get("id") or _legacy_id(e)) not in ids)
    index.purge(KIND, ids)   # the lines leave the registry for good, not into its history
    learned = people.forget_sources(ids)   # and what was learned only from them
    events.log("cleanup", f"забыто реплик подслушанного: {len(gone)}, фактов из них: {learned}", person=pid or None, since=since, until=until)
    return f"Забыто реплик: {len(gone)}" + (f", и фактов о людях, выведенных только из них: {learned}" if learned else "")
