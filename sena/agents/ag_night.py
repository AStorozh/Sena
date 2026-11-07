"""ag_night — nightly cleanup: once a day cleans memory and skills of duplicates and stale entries."""
import hashlib
import json
import shutil
import sqlite3
import threading
import time
import zipfile
from datetime import datetime

from sena import config
from sena.agents import common
from sena.core import brain, cancel, costs, events, offsite, roles, secrets, status, store
from sena.memory import facts, files, history, index, lessons, recall, rules, skills, task_log, vault_sync

TIDY = """Ты ночная уборка памяти Сены. Ниже — вся память о Теме (ядро) и все навыки. Ответь одним JSON по схеме: только изменения,
и в каждом — строки ядра дословно, как в нём (дату в начале строки можно не писать). Текст ядра целиком не переписывай.
merge — строки, которые говорят одно и то же: old — эти строки, new — одна строка вместо них; все числа, даты и названия из old
  остаются в new (иначе склейка не примется). В начале new — самая новая дата из old.
supersede — строка, которой противоречит более новая (по дате в начале строки: переехал, сменил работу, план отменён):
  old — устаревшая строка, why — чем сменилась, одной фразой. Она уйдёт в архив с пометкой, до какого дня была верна.
archive — прошедшие планы, разовое и редкое (детали старых задач, мелкие факты, давние события): эти строки уйдут из ядра в
  архив, Сена найдёт их поиском. Ядро идёт в каждый запрос, поэтому короткое (до ~50 строк): только то, что нужно почти каждый день.
Факты, которые могут быть верны вместе («любит чай» и «любит кофе»), не трогай. Сомневаешься — не трогай.
skills — только навыки, которые надо переписать: одинаковые по смыслу склей в один (name, when — одна строка «когда применять», recipe — рецепт);
code — только Python-рецепт браузера: функция run(page, ...) из раздела «Код (запускается без модели)». Команды терминала и прочее — в recipe.
Код не трогаешь — пустая строка; склеиваешь два навыка с таким кодом — полный код более надёжного.
delete_skills — имена навыков, которые убрать: склеенные в другой или явно неверные. Хорошие не трогай.
Всё и так чисто — все списки пустые."""
_LINES = {"type": "array", "items": {"type": "string"}}
TIDY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["merge", "supersede", "archive", "skills", "delete_skills"],
               "properties": {"merge": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["old", "new"],
                                                                   "properties": {"old": _LINES, "new": {"type": "string"}}}},
                              "supersede": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["old", "why"],
                                                                       "properties": {"old": {"type": "string"}, "why": {"type": "string"}}}},
                              "archive": _LINES,
                              "skills": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["name", "when", "recipe", "code"],
                                                                    "properties": {"name": {"type": "string"}, "when": {"type": "string"},
                                                                                   "recipe": {"type": "string"}, "code": {"type": "string"}}}},
                              "delete_skills": _LINES}}


KEEP_BACKUPS_DAYS = 14   # copies older than this are dropped — only the copies, never an original
EVENTS_COPIES = 3        # the event journal is by far the heaviest file: a full copy once a week, the three newest kept
EVENTS_EVERY_DAYS = 7
INDEX_COPIES = 3         # the registry, every night, the three newest kept: its versions are the only history of what records said
TIDY_STATE = config.STATE_DIR / "tidy_state.json"   # fingerprint of memory and skills after the last cleanup
COPIED = (("memory.md", config.MEMORY_FILE), ("archive.md", config.ARCHIVE_FILE), ("personality.md", config.PERSONALITY_FILE),
          ("settings.json", config.SETTINGS_FILE), ("secrets.enc", config.SECRETS_FILE))   # the secrets file, never its key; records — one database copy
COUNTED = ("*_events.db", "*_index.db")   # copies kept by their number, not by their age
_backup_lock = threading.Lock()   # the night's copy and one asked for from the app never run at once (two copies, two uploads)


def _history_files():
    # every <file>.history.jsonl: the past of people, rules, settings, memory, skills
    out = []
    for folder in (config.MEMORY_DIR, config.STATE_DIR, config.SKILLS_DIR):
        out += sorted(folder.glob("*.history.jsonl")) if folder.is_dir() else []
    return out


def _copy_index(stamp):
    """The registry as one consistent file (VACUUM INTO, its WAL tail included), read-only: a damaged registry is not copied —
    the good copies from the nights before stay, the newest of them is what it comes back from. The path, or None."""
    if not config.INDEX_DB.exists():
        return None
    target = config.BACKUPS_DIR / f"{stamp}_index.db"
    db = sqlite3.connect(f"file:{config.INDEX_DB}?mode=ro", uri=True, timeout=30)
    try:
        try:
            verdict = db.execute("PRAGMA quick_check").fetchone()[0]
        except sqlite3.DatabaseError as e:   # damaged past what the check can read («disk image is malformed»): a verdict too
            verdict = str(e)
        if verdict != "ok":
            events.log("error", f"указатель памяти повреждён, в копию не взят: {verdict[:200]}", place="ночная копия")
            return None
        db.execute("VACUUM INTO ?", (str(target),))
    finally:
        db.close()
    for old in sorted(config.BACKUPS_DIR.glob("*_index.db"))[:-INDEX_COPIES]:
        old.unlink()
    return target


def backup_memory(force=False):
    """Copy of everything that can't be recomputed: memory, people, rules, tasks, correspondence, overheard speech, the records
    of files and links, settings, the owner's secrets (the file, not the key), every history file, skills and expenses as archives,
    the registry with its versions; the event journal once a week. Copies older than two weeks are dropped — the originals never
    are. The night makes one a day (a retry after a failed night doesn't copy again); force — the owner asked for one now."""
    with _backup_lock:
        stamp = f"{datetime.now():%Y-%m-%d_%H%M%S}"
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        if not force and any(p.is_file() and p.stat().st_mtime >= today for p in config.BACKUPS_DIR.iterdir()):
            return None   # a copy already exists for today (a retry after a cleanup failure)
        copied = 0
        for name, path in COPIED:
            if path.exists():
                shutil.copy(path, config.BACKUPS_DIR / f"{stamp}_{name}")
                copied += 1
        store.snapshot(config.BACKUPS_DIR / f"{stamp}_sena.db")   # people, rules, tasks, conversations, overheard speech, files, links: one consistent moment
        copied += 1
        archives = [shutil.make_archive(str(config.BACKUPS_DIR / f"{stamp}_skills"), "zip", config.SKILLS_DIR)]
        costs_dir = config.JOURNAL_DIR / "costs"
        if costs_dir.is_dir():
            archives.append(shutil.make_archive(str(config.BACKUPS_DIR / f"{stamp}_costs"), "zip", costs_dir))
        history = _history_files()
        if history:
            target = config.BACKUPS_DIR / f"{stamp}_history.zip"
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
                for path in history:
                    z.write(path, arcname=str(path.relative_to(config.DATA)))
            archives.append(str(target))
        try:
            registry = _copy_index(stamp)
            if registry:
                archives.append(str(registry))
        except sqlite3.DatabaseError as e:   # the registry can't be read whole: the rest of the copy still goes
            events.error("Копия указателя памяти", e)
        copies = sorted(config.BACKUPS_DIR.glob("*_events.db"))
        if config.EVENTS_DB.exists() and (not copies or time.time() - copies[-1].stat().st_mtime > EVENTS_EVERY_DAYS * 86400):
            target = config.BACKUPS_DIR / f"{stamp}_events.db"
            with sqlite3.connect(config.EVENTS_DB) as db:   # a full copy, including the WAL tail
                db.execute("VACUUM INTO ?", (str(target),))
            db.close()
            archives.append(str(target))
            for old in sorted(config.BACKUPS_DIR.glob("*_events.db"))[:-EVENTS_COPIES]:
                old.unlink()
        border = time.time() - KEEP_BACKUPS_DAYS * 86400
        for old in config.BACKUPS_DIR.iterdir():
            if old.is_file() and not any(old.match(pattern) for pattern in COUNTED) and old.stat().st_mtime < border:
                old.unlink()
        events.log("backup", f"копия {stamp}: файлов {copied}, архивов {len(archives)}", archives=[a.rsplit('/', 1)[-1] for a in archives],
                   asked=force or None)
        try:   # the same set, encrypted, leaves the Mac (iCloud Drive): with the secrets' key inside, opened only by the recovery key
            offsite.make(sorted(config.BACKUPS_DIR.glob(f"{stamp}_*")), {"secrets.key": secrets.key_for_copy()} if config.SECRETS_FILE.exists() else None)
        except Exception as e:
            events.error("Копия вне Mac", e)
        return copied


ATTENTION = config.STATE_DIR / "attention.json"   # which nightly warnings the owner has already been told about


def _tell(text):
    # a word of the night to the owner: without a sound (it waits for the morning) and into the correspondence, so his reply to
    # it («почини») is answered knowing what it was about
    from sena.telegram import api   # the outgoing channel sits below the agents (tests/test_structure.OUTGOING)
    api.notify(text, quiet=True)
    history.reply(config.OWNER_ID, text)


def _tell_once(what, now_true, text):
    """A nightly warning reaches the owner once, when it first becomes true — not every night after."""
    told = store.load_json(ATTENTION, {})
    if now_true and not told.get(what):
        _tell(text)
        events.log("attention", text, what=what)
    if bool(told.get(what)) != bool(now_true):
        store.save_json(ATTENTION, {**told, what: bool(now_true)}, indent=1)


def _count(path, sql):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [db.execute(q).fetchone()[0] for q in sql]
    finally:
        db.close()


def check_integrity():
    """Night check: the databases answer PRAGMA integrity_check, and the newest copies of the event journal, the records and the
    registry open read-only and count their rows — a copy that was never opened isn't a backup. Returns {name: verdict}."""
    report = {}
    try:
        report["sena.db"] = store.integrity()   # the records: people, rules, tasks, conversations — the one store that is not rebuilt from anything
        for name, path in (("events.db", config.EVENTS_DB), ("index.db", config.INDEX_DB)):
            if path.exists():
                report[name] = _count(path, ["PRAGMA integrity_check"])[0]
        copies = sorted(config.BACKUPS_DIR.glob("*_events.db"))
        if copies:
            report["copy"] = f"{copies[-1].name}: событий {_count(copies[-1], ['SELECT COUNT(*) FROM events'])[0]}"
        records = sorted(config.BACKUPS_DIR.glob("*_sena.db"))
        if records:   # the newest copy of the records opens and counts what it holds
            docs, lines = _count(records[-1], ["SELECT COUNT(*) FROM docs", "SELECT COUNT(*) FROM lines"])
            report["records_copy"] = f"{records[-1].name}: записей {docs}, строк {lines}"
        registry = sorted(config.BACKUPS_DIR.glob("*_index.db"))
        if registry:
            items, versions = _count(registry[-1], ["SELECT COUNT(*) FROM items", "SELECT COUNT(*) FROM versions"])
            report["index_copy"] = f"{registry[-1].name}: записей {items}, версий {versions}"
    except sqlite3.DatabaseError as e:
        report["error"] = str(e)
    bad = {k: v for k, v in report.items() if k not in ("copy", "records_copy", "index_copy") and v != "ok"}
    if bad:
        events.log("error", f"целостность баз: {bad}", place="ночная проверка")
    else:
        events.log("backup", "проверка целостности: " + (", ".join(f"{k}: {v}" for k, v in report.items()) or "нечего проверять"))
    return report


def think(text):
    """The one model call of the night: what to keep in the memory core, what to archive, which skills to merge."""
    status.show("Убираю память и навыки", who="ag_night")
    standing = rules.listing()   # a standing rule ("не трогай навык такой-то") reaches her only here, not in checkup's or _tell's code path
    instructions = TIDY + (f"\nПравила Темы, их не нарушай:\n{standing}" if standing else "")
    # through the brain's door: a passing failure (an overloaded model, no network for a minute) is waited out or answered by
    # the neighbouring model, as in any task, instead of costing the night its tidy
    reply = brain.respond("medium", purpose="ночная уборка памяти", model=config.MODEL_MAIN, reasoning={"effort": "medium"}, instructions=instructions,
                          input=text, text={"format": {"type": "json_schema", "name": "tidy", "schema": TIDY_SCHEMA}})
    return json.loads(reply.output_text)


def apply(plan):
    """Carry out the night's answer: the core through checked operations (facts.tidy_ops), skills rewritten or retired (their text stays in history)."""
    said = []
    done, refused = facts.tidy_ops(plan.get("merge"), plan.get("supersede"), plan.get("archive"))
    if done or refused:
        said.append(f"Память: изменений {len(done)}" + (f", не принято {len(refused)}" if refused else ""))
        events.log("cleanup", "уборка памяти: " + "; ".join(done + [f"не принято — {r}" for r in refused]), done=len(done), refused=len(refused) or None)
    for s in plan.get("skills") or []:
        code = s.get("code") or ""
        code = code if "def run(" in code else None   # only a browser recipe is code; anything else stays in the words, the skill's code untouched
        said.append(skills.write_skill(s.get("name", ""), s.get("when", ""), s.get("recipe", ""), code))
    for name in plan.get("delete_skills") or []:
        said.append(skills.delete_skill(name))
    return "; ".join(said) or "Память и навыки и так чистые"


def _memory():
    # the one question to the model, when memory or skills changed since the last night; the code applies the answer with its
    # guarantees. Under the learning lock: a post-task review may be rewriting a skill right now
    with common.learning_lock:
        all_skills = "\n\n".join(f"### {n}\n{skills.text(n)}" for n in skills.names()) or "нет"
        text = f"# Память\n{facts.read()}\n\n# Навыки\n{all_skills}"
        if store.load_json(TIDY_STATE, {}).get("digest") == hashlib.md5(text.encode()).hexdigest():   # unchanged since the last cleanup: no model
            return "Память и навыки с прошлой уборки не менялись; копии и чистка сделаны"
        answer = apply(think(text))
        after = f"# Память\n{facts.read()}\n\n# Навыки\n" + ("\n\n".join(f"### {n}\n{skills.text(n)}" for n in skills.names()) or "нет")
        store.save_json(TIDY_STATE, {"digest": hashlib.md5(after.encode()).hexdigest(), "time": time.time()}, indent=1)
    return answer


def _databases():
    checked = check_integrity()
    status.show("Копия сделана, базы проверены: " + (", ".join(f"{k} {v}" for k, v in checked.items() if k != "copy") or "нечего проверять"), who="ag_night")
    broken = checked.get("index.db") not in (None, "ok")
    if not broken:
        _tell_once("index", False, "")
        return ""
    # the registry is derived from the files: the damaged one is set aside (kept), the last good night copy takes its place (the
    # copy skipped damaged files, _copy_index), and the reconciliation brings it up to now. The owner hears it once
    copies = sorted(config.BACKUPS_DIR.glob("*_index.db"))
    index.set_aside(checked["index.db"], copies[-1] if copies else None)
    done = recall.sync_all()
    _tell_once("index", True, "Указатель памяти был повреждён: отложила его, взяла ночную копию и досчитала из записей — поиск по памяти "
                              f"снова в порядке ({done}). Испорченный файл лежит рядом как index.broken-….db.")
    return f"Указатель памяти повреждён и восстановлен: {checked['index.db'][:200]}"


def _rules():
    done = rules.tidy()   # one-off rules whose triggers already fired (the rules file keeps them in its history)
    status.show(f"Исполненных разовых правил убрано: {done}", who="ag_night")
    return ""


def _files():
    read = files.read_pending(200)   # photos and PDFs not read yet: their text and what they show, read at home
    return f"Прочитано файлов на Mac: {read}" if read else ""


def _lessons():
    with common.learning_lock:
        retired = lessons.tidy()   # agents' drafts the judge never kept, and the weakest past the cap
    return f"Уроков агентов убрано в архив: {retired}" if retired else ""


def _stale_skills():
    with common.learning_lock:
        stale = skills.archive_stale()   # skills nobody read or ran for months: to skills/archive, never deleted
    if not stale:
        return ""
    events.log("cleanup", f"навыки в архив: {', '.join(stale)}", skills=len(stale))
    return f"Навыков в архив (давно не нужны): {', '.join(stale)}"


def _graduate():
    lines = []
    for g in roles.graduate():   # learning jobs: does the home model answer as the cloud one by now?
        lines.append(f"Домашняя модель на «{roles.TITLES[g['role']]}»: " + (f"близость к облаку {g['agree']:.0%} на {g['tested']} примерах" if g["tested"] else f"примеров {g['examples']} из {roles.MIN_EXAMPLES}")
                     + (f" — {g['moved']}" if g.get("moved") else ""))
    return "\n".join(lines)


def _registry():
    size = index.stats()   # how big the registry is tonight and whether the vector seam should open (index.ANN_*)
    _tell_once("ann", index.needs_ann(), "Память выросла до порога: поиску по смыслу пора включить быстрый указатель (sqlite-vec). "
                                          "Это правка кода: скажи, когда сделать.")
    events.log("sync", f"реестр: записей {sum(size['items'].values())}, векторов {size['vectors']}, версий {size['versions']}, "
                       f"связей {size['links']}, файл {size['file_mb']} МБ, матрица {size['matrix_mb']} МБ, поиск {size['search_ms']} мс",
               **{k: v for k, v in size.items() if k != "items"}, attention=index.needs_ann() or None)
    return ""


def _outside_changes():
    changed = store.outside_changes()   # her memory, personality or skills edited by someone else: told once, with what changed
    if not changed:
        return ""
    _tell("Мои файлы изменил кто-то, кроме меня:\n" + "\n".join(f"{name}:\n{diff}" for name, diff in changed[:5])
          + "\nЕсли это ты — всё в порядке. Если нет — скажи: прежний текст сохранён в истории файла.")
    events.log("attention", f"файлы изменены не Сеной: {', '.join(n for n, _ in changed)}", files=[n for n, _ in changed])
    return f"Изменены не Сеной: {', '.join(n for n, _ in changed)}"


def _system():
    from sena.agents import checkup   # late: the check itself uses this module's integrity check
    return checkup.night() or "Проверка системы: без перемен"   # the whole system, tried for real: what broke reaches the owner once


NIGHT = (("копия", backup_memory), ("базы", _databases), ("правила", _rules), ("память и навыки", _memory),
         ("хранилище", lambda: f"Хранилище: {vault_sync.sync_all()}"), ("указатель", lambda: f"Указатель: {recall.sync_all()}"),
         ("файлы", _files), ("уроки", _lessons), ("навыки", _stale_skills), ("домашние модели", _graduate), ("реестр", _registry),
         ("чужие правки", _outside_changes), ("проверка системы", _system))


NIGHT_DONE = config.STATE_DIR / "night_done.json"   # the steps tonight's night has done: a retry an hour later runs only the rest


def tidy():
    # a service task: visible in "Processes" and in the log, not counted as experience for other tasks. Every step on its own:
    # one that fails is written down and the rest still run (one failed cloud call used to skip eight jobs, the system check among
    # them — the night Claude is down is the night it should say so). A failed step makes the night fail at its end, so the
    # scheduler tries again in an hour (scheduler._spawn): the memory and skills used to wait a whole day after a failed cloud call
    cancel.new_task()
    status.begin("Ночная уборка памяти и навыков", service=True)
    said, failed = [], []
    day = datetime.now().strftime("%Y-%m-%d")
    record = store.load_json(NIGHT_DONE, {}) or {}
    done = set(record.get("steps", [])) if record.get("day") == day else set()
    try:
        with costs.label("ночная уборка"):
            for name, step in NIGHT:
                cancel.check_stop()
                if name in done:   # done on an earlier try tonight
                    continue
                try:
                    line = step()
                except cancel.Stopped:
                    raise
                except Exception as e:
                    events.error(f"Ночная уборка: {name}", e)
                    line = f"{name}: не удалось — {str(e)[:200]}"
                    failed.append(name)
                else:
                    done.add(name)
                    store.save_json(NIGHT_DONE, {"day": day, "steps": sorted(done)})
                if isinstance(line, str) and line:
                    said.append(line)
        print("Ночная уборка памяти закончена;", "; ".join(said))
    finally:
        task = status.end("\n".join(said) or "Ночная уборка: нечего сообщить")
        if task:
            task_log.save(task)
    if failed:
        raise RuntimeError(f"ночная уборка не доделала: {', '.join(failed)} — повторю через час")
