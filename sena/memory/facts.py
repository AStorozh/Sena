"""Long-term memory about Tema: a text file with sections. Plain text was chosen over a database so the memory can be read and edited by hand."""
import re
import threading
import time
from datetime import datetime

from sena import config
from sena.core import events, safety, status, store
from sena.core.tools import Failure, string, tool
from sena.memory import index

SECTIONS = list(index.MEMORY_SECTIONS)  # working methods are kept in skills (skills.py)
_lock = threading.Lock()


SIMILAR = 0.9   # a fact this similar in the same section, keeping almost all words of the old one, counts as an update, not a new fact
KEEP_WORDS = 0.8


def read():
    return store.read_text(config.MEMORY_FILE)


STAMP = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})(?: (\d{2}):(\d{2}))?")   # "24.10.2026 14:03:" (older lines: date only)


def line_time(line):
    """Moment of a memory line from the stamp at its start, read in the Mac's zone; no stamp means 0 (long ago)."""
    m = STAMP.match(line)
    try:
        return time.mktime(datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), int(m.group(4) or 0), int(m.group(5) or 0)).timetuple()) if m else 0.0
    except ValueError:
        return 0.0


KIND = "факт"


def sync_index():
    """Memory in the registry, both tiers in one reconciliation of the kind: a core line -> "section: line" (tier core, facet =
    section), an archive line as it is (tier archive). Called after any edit to either file. Two passes over one kind took each
    other's tier away: the archive was unfindable, and after every restart the core was missing for remember's update check."""
    rows = {}
    for line in _archived():
        rows[index.line_ref(line)] = (line, line_time(line), {"tier": "archive"})
    for s in sections():
        for i in s["items"]:   # a line in both files is the core's
            text = f"{s['title']}: {i}"
            rows[index.line_ref(text)] = (text, line_time(i), {"tier": "core", "facet": s["title"] if s["title"] in index.MEMORY_SECTIONS else None})
    return index.sync(KIND, [(ref, *row) for ref, row in rows.items()])


def plain(line):
    """The fact without its leading stamp."""
    return re.sub(r"^\d{2}\.\d{2}\.\d{4}(?: \d{2}:\d{2})?:\s*", "", line).strip()


def _words(line):
    # words and numbers of the fact without the leading date: "number 3" and "number 4" are different facts
    return set(re.findall(r"\w+", plain(line).lower()))


def drop_exact(section, text):
    """Remove the one line of a section whose text (without the stamp) is exactly text. True if something was removed."""
    with _lock:
        lines = read().splitlines()
        current, hit = None, None
        for i, line in enumerate(lines):
            if line.startswith("## "):
                current = line[3:]
            elif current == section and line.startswith("- ") and plain(line[2:]) == text.strip():
                hit = i
                break
        if hit is None:
            return False
        del lines[hit]
        store.write_versioned(config.MEMORY_FILE, "\n".join(lines).strip() + "\n")
    sync_index()
    return True


def _similar(section, fact):
    # an old fact in the same section that the new one essentially updates: close in meaning and containing almost all its words
    # ("Marshall headphones" -> "Marshall headphones, blue"). Different facts sharing one word ("meeting on Tuesday" / "on Thursday")
    # both stay: contradictions are resolved by the nightly cleanup, not a blind replace
    try:
        for r in index.search(f"{section}: {fact}", kinds=[KIND], k=3, min_cosine=SIMILAR, meta={"tier": "core"}, scope="all"):
            if not r["text"].startswith(f"{section}: "):
                continue
            old = r["text"][len(section) + 2:]
            was, now = _words(old), _words(fact)
            if was and len(was & now) >= KEEP_WORDS * len(was):
                return old
    except Exception as e:
        events.error("Поиск похожего факта", e)
    return None


def _insert(lines, section, line):
    # line to the end of the section (created if the section doesn't exist)
    header = f"## {section}"
    if header not in lines:
        lines += ["", header]
    i = lines.index(header) + 1
    while i < len(lines) and not lines[i].startswith("## "):
        i += 1
    while i > 0 and lines[i - 1] == "":
        i -= 1
    lines.insert(i, line)


SECRET_LIKE = re.compile(r"(парол|password|пин[- ]?код|\bpin\b|код доступа|api[- ]?key|токен|token|секрет)", re.I)
LONG_NUMBER = re.compile(r"(?:\d[ -]?){12,}")   # a card, account or document number


def secret_like(text):
    """A password, code or key written next to its value, or a long card/document number: never stored in plain text
    (memory sits in every prompt; people's cards in every search)."""
    return bool(LONG_NUMBER.search(text)) or (bool(SECRET_LIKE.search(text)) and any(len(t) >= 6 and re.search(r"[\d!@#$%^&*?_]", t) for t in text.split()))


# A relative date in a long-kept fact goes stale the next day ("в пятницу стоматолог" — which Friday?): written as a date
RELATIVE = re.compile(r"(?<!\w)(сегодня|завтра|послезавтра|вчера|позавчера|на (этой|следующей|прошлой) неделе|в (этот|следующий|прошлый) раз|"
                      r"в (понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)|через \d+ (дн|недел|месяц))(?!\w)", re.I)


def _hits(lines, text):
    # memory lines whose text without the stamp is exactly this text, in any section (Letta: a replace names one line verbatim)
    want = plain(text).lower()
    return [l for l in lines if l.startswith("- ") and plain(l[2:]).lower() == want]


def remember(section, fact, instead_of=""):
    """Write a long fact; instead_of — the memory line it replaces, word for word (a move, a new job, a cancelled plan): the old
    line leaves the core for the archive, marked until when it held — never two contradicting lines side by side
    (mem0's supersede, Letta's memory_replace that must find exactly one line)."""
    fact = " ".join(safety.visible(fact).split())
    if secret_like(fact):   # a password in memory would sit in every prompt in plain text
        return Failure("Пароли, коды и ключи в память не пишу: их место — раздел «Секреты» в приложении (там они зашифрованы и выдаются по правилам)")
    if section != "Словарь" and RELATIVE.search(plain(fact)):
        return Failure(f"Не записала: в факте относительная дата («{RELATIVE.search(plain(fact)).group(0)}»). Перепиши с датой числами (дд.мм.гггг) и запиши снова.")
    if section != "Словарь" and not re.match(r"^\d{2}\.\d{2}\.\d{4}", fact):
        fact = f"{datetime.now():%d.%m.%Y %H:%M}: {fact}"   # stamp: on contradiction the newer one wins
    old = None if instead_of else _similar(section, fact)
    with _lock:
        lines = read().splitlines()
        if instead_of:
            at = _at(lines, instead_of)
            if at is None:
                hits = _hits(lines, instead_of)
                near = [l[2:] for l in lines if l.startswith("- ") and len(_words(instead_of) & _words(l[2:])) >= 2][:3]
                return Failure("Не заменила: " + ("такой строки в памяти нет" if not hits else f"подходит строк: {len(hits)}")
                               + (". Похожие: " + " | ".join(near) if near else "") + ". Назови старую строку дословно или запиши без instead_of.")
            if plain(lines[at][2:]) == plain(fact):   # the same words (an edit saved unchanged, or only the section moved): no archive line
                return _moved(lines, at, section)
            replaced = lines.pop(at)[2:]
            # the replaced fact stays findable, marked until when it held; the archive is written first, so a crash between
            # the two writes leaves the line in both files, never in neither
            _append_archive([f"{replaced} (так было до {datetime.now():%d.%m.%Y}; сменилось: {plain(fact)})"])
        elif old and f"- {old}" in lines:
            lines.remove(f"- {old}")   # update: the old version of the fact is removed, the new one with the new date stays
        _insert(lines, section, f"- {fact}")
        store.write_versioned(config.MEMORY_FILE, "\n".join(lines).strip() + "\n")
    sync_index()
    task = status.current()
    if task:   # the fact came out of this task: the link can't be recomputed from memory.md, so it is kept
        index.link(KIND, index.line_ref(f"{section}: {fact}"), "задача", task["id"], "derived_from")
    if instead_of:
        return f"Заменила в «{section}»: {fact} (прежнее ушло в архив: {plain(replaced)})"
    return f"Обновила в «{section}»: {fact} (было: {old})" if old else f"Запомнила в «{section}»: {fact}"


def _moved(lines, at, section):
    # the caller holds _lock: a line named instead of itself — saved unchanged it stays as it is, with a new section it moves
    # there with its own date; an archive line «X (так было до …; сменилось: X)» was noise in the archive and the registry
    line, was = lines[at], _section_of(lines, at)
    if was == section:
        return f"Обновила в «{section}»: без изменений — {line[2:]}"
    del lines[at]
    _insert(lines, section, line)
    store.write_versioned(config.MEMORY_FILE, "\n".join(lines).strip() + "\n")
    sync_index()
    return f"Заменила в «{section}»: {line[2:]} (перенесла из «{was}»)"


def _section_of(lines, at):
    return next((lines[i][3:] for i in range(at, -1, -1) if lines[i].startswith("## ")), None)


def _append_archive(lines):
    # the caller holds _lock. A line keeps the date it was written with (the fact's own moment, as find shows it); a line
    # without one is dated now
    added = "".join(f"- {line}\n" if STAMP.match(line) else f"- {datetime.now():%d.%m.%Y %H:%M}: {line}\n" for line in lines)
    if added:
        store.write_text(config.ARCHIVE_FILE, store.read_text(config.ARCHIVE_FILE) + added)


def _archived():
    return [l[2:] for l in store.read_text(config.ARCHIVE_FILE).splitlines() if l.startswith("- ")]


def _without(line, fact):
    # an archive line with a forgotten fact taken out, in the forms remember and the night write: None — the line is that fact
    # (as it is, or its replaced copy «fact (так было до …)»); the line it replaced «X (так было до …; сменилось: fact)» keeps
    # X and its date without the fact; any other line comes back as it was
    low, fact = plain(line).lower(), plain(fact).lower()
    if low == fact or low.startswith(fact + " (так было до "):
        return None
    tail = f"; сменилось: {fact})"
    return line[:-len(tail)] + ")" if line.lower().endswith(tail) else line


def forget(text):
    """Delete one memory line: the whole line word for word, or a part of it that only this line has. Several lines fit —
    nothing is deleted, they are shown (Letta: an edit names one line; a broad word must not take its neighbours with it).
    The same fact leaves the archive too, and the registry's past of both: find brought a forgotten fact back from there."""
    text = text.strip().lower()
    if len(text) < 3:
        return Failure("Слишком коротко: назови часть строки, которую нужно удалить (от трёх знаков)")
    with _lock:
        lines = read().splitlines()
        whole = text.removeprefix("- ").strip()   # the line named with its stamp tells it from a twin with another date
        found = ([l for l in lines if l.startswith("- ") and l[2:].strip().lower() == whole] or _hits(lines, text)
                 or [l for l in lines if l.startswith("- ") and text in l.lower()])
        if not found:
            return Failure(f"Не нашла в памяти: {text}")
        if len(set(found)) > 1:   # copies of one and the same line are one line: they go together
            return Failure(f"Под «{text}» подходит строк: {len(found)} — ничего не удалила. Назови нужную строку целиком (можно по очереди):\n"
                           + "\n".join(found[:8]))
        refs = [index.line_ref(f"{_section_of(lines, i)}: {l[2:]}") for i, l in enumerate(lines) if l in found]
        archived, kept = store.read_text(config.ARCHIVE_FILE).splitlines(), []
        for line in archived:
            if not line.startswith("- "):
                kept.append(line)
                continue
            left = _without(line[2:], found[0][2:])
            if left != line[2:]:
                refs.append(index.line_ref(line[2:]))   # its old words leave the registry and its past
            if left is not None:
                kept.append(f"- {left}")
        # the registry first, its versions too: purged after the files, a crash between would let the next reconciliation drop
        # the lines into versions — kept against the owner's word (people.forget_sources does the same)
        index.purge(KIND, refs)
        if kept != archived:
            store.write_text(config.ARCHIVE_FILE, "".join(f"{l}\n" for l in kept))
        store.write_versioned(config.MEMORY_FILE, "\n".join(l for l in lines if l not in found).strip() + "\n")
    sync_index()
    return f"Удалила: {found[0][2:]} (из памяти и её архива; в переписке эти слова остались)"


NUMBER = re.compile(r"\d+(?:[.,:]\d+)*")
OUT_SHARE, CORE_LINES = 0.25, 60   # a night moves out at most a quarter of the core — more only to bring it down to this size


def _at(lines, text, taken=()):
    # the core line this text names: the whole line with its stamp, or its text without the stamp; None — none, or several
    # different ones (exact duplicates are one line in effect: any of them will do). taken — lines already used
    text = str(text).strip().removeprefix("- ").strip()
    for same in (lambda l: l[2:].strip().lower() == text.lower(), lambda l: plain(l[2:]).lower() == plain(text).lower()):
        found = [i for i, l in enumerate(lines) if l.startswith("- ") and i not in taken and same(l)]
        if found:
            return found[0] if len({lines[i] for i in found}) == 1 else None
    return None


def tidy_ops(merge=(), supersede=(), archive=()):
    """The night's changes to the core as named operations, each checked before it applies — never a rewritten text, where a
    changed number or a dropped line would pass unseen (Letta: edits, not rewrites; OpenClaw's dreaming: decisions, not
    prose, and a cap on how much may leave at once; mem0: supersede and merge, nothing destructive).
    merge — lines that say one thing become one: every old line must exist, and every number in them must survive;
    supersede — a line a newer one contradicts leaves for the archive, marked until when it held;
    archive — a rarely needed line leaves the core for the archive, where find still finds it.
    Edits made while the model was thinking stay: the operations apply to the memory as it is now. Returns (done, refused)."""
    done, refused, out = [], [], []
    with _lock:
        lines = read().splitlines()
        core = sum(l.startswith("- ") for l in lines)
        most_out = max(3, int(core * OUT_SHARE), core - CORE_LINES)
        gone = set()
        for op in merge or []:
            olds, new = [str(o) for o in op.get("old") or []], " ".join(safety.visible(op.get("new") or "").split())
            at = []
            for o in olds:   # one line per name, so two identical duplicates are two lines
                at.append(_at(lines, o, taken=gone | set(at)))
            numbers = {n for o in olds for n in NUMBER.findall(plain(o))}
            lost = sorted(numbers - set(NUMBER.findall(new)))
            if len(olds) < 2 or None in at or set(at) & gone or not new or lost or secret_like(new):
                refused.append(f"склейка {olds[:2]}: " + (f"потерялись числа {', '.join(lost)}" if lost else "строки не найдены дословно или пустая новая"))
                continue
            lines[at[0]] = "- " + new
            gone.update(at[1:])
            done.append(f"склеила {len(olds)} строки: {plain(new)}")
        for kind, items in (("supersede", supersede or []), ("archive", archive or [])):
            for op in items:
                old = op.get("old", "") if isinstance(op, dict) else str(op)
                i = _at(lines, old, taken=gone)
                if i is None:
                    refused.append(f"{'замена' if kind == 'supersede' else 'архив'}: строки «{old[:60]}» нет дословно")
                    continue
                if len(out) >= most_out:
                    refused.append(f"архив: «{old[:60]}» — за ночь больше не убираю ({most_out} строк)")
                    continue
                line, text = lines[i][2:], plain(lines[i][2:])
                why = " ".join(str(op.get("why") or "").split())[:200] if isinstance(op, dict) else ""
                out.append(f"{line} (так было до {datetime.now():%d.%m.%Y}" + (f"; сменилось: {why})" if why else ")") if kind == "supersede" else line)
                gone.add(i)
                done.append(("устарело: " if kind == "supersede" else "в архив: ") + text)
        if gone:
            lines = [l for i, l in enumerate(lines) if i not in gone]
        if out:   # the archive first: a crash between the writes keeps a moved line in both files, never in neither
            _append_archive(out)
        if done:
            store.write_versioned(config.MEMORY_FILE, "\n".join(lines).strip() + "\n")
    if done:
        sync_index()
    return done, refused


def sections():
    # memory by section: [{"title": section, "items": [lines]}]
    result = []
    for line in read().splitlines():
        if line.startswith("## "):
            result.append({"title": line[3:], "items": []})
        elif line.startswith("- ") and result:
            result[-1]["items"].append(line[2:])
    return result


def write(parts):
    # save all memory by section (edits coming from the app)
    with _lock:
        text = "\n".join(f"## {s['title']}\n" + "".join(f"- {i}\n" for i in s["items"]) for s in parts)
        store.write_versioned(config.MEMORY_FILE, _dated(text, read()).strip() + "\n")
    sync_index()


def _dated(text, before):
    # a line typed by hand in an editor of the whole memory gets the date remember gives (it read as written in 1970, and on
    # a contradiction the newer line couldn't win); lines that were there, stamped ones and the dictionary stay as they are
    known, out, section = set(before.splitlines()), [], None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:]
        elif line.startswith("- ") and line not in known and section != "Словарь" and not STAMP.match(line[2:]):
            line = f"- {datetime.now():%d.%m.%Y %H:%M}: {line[2:].strip()}"
        out.append(line)
    return "\n".join(out)


def _with_sections(text):
    section = None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:]
        elif line.startswith("- "):
            yield section, line


def _carry(snapshot, current, new):
    # the cleanup reasoned over a snapshot of memory; lines Sena added or removed in the meantime carry over into the new text
    was = {l for _, l in _with_sections(snapshot)}
    now = [(s, l) for s, l in _with_sections(current)]
    removed = was - {l for _, l in now}
    lines = [l for l in new.splitlines() if not (l.startswith("- ") and l in removed)]
    first = next((l[3:] for l in lines if l.startswith("## ")), SECTIONS[0])
    for section, line in now:
        if line in was or line in lines:
            continue
        _insert(lines, section or first, line)   # a line without a section (manual edit) goes to the first section, not under "## None"
    return "\n".join(lines)


def replace(text, snapshot=None):
    # replace all of memory (the Mac's editor). snapshot is the text the owner saw: edits Sena made meanwhile aren't lost
    with _lock:
        before = read()
        if snapshot is not None and snapshot != before:
            text = _carry(snapshot, before, text)
        store.write_versioned(config.MEMORY_FILE, _dated(text, before).strip() + "\n")
    sync_index()


TOOLS = [
    tool("remember", "Записать в память важный долгий факт о Теме одной короткой самодостаточной строкой: имена вместо «он», даты числами, "
                     "числа и названия как сказано; перемена — вместе с прежним («переехал из Москвы в Стамбул»). Факт сменил прежний — instead_of",
         {"section": {**string("Раздел памяти"), "enum": SECTIONS}, "fact": string("Факт одной короткой строкой"),
          "instead_of": string("строка памяти, которую этот факт отменяет (переезд, новая работа, отменённый план), дословно без даты; пусто — новый факт")},
         ["section", "fact"]),
    tool("forget", "Удалить из памяти одну строку: целиком или по части, которая есть только в ней", {"text": string("Строка или её часть")}, ["text"]),
]
FUNCTIONS = {"remember": remember, "forget": forget}
