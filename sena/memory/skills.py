"""Skills: proven working methods, one file per skill in data/memory/skills/.
<name>.md — the first line is "when to apply", followed by the recipe in words. Only the "name — when" list goes into the prompt;
the agent reads the full recipe itself via read_skill when the skill is needed. This way the prompt doesn't bloat.
<name>.py (optional) — the same recipe as code: a run(page, ...) function for the browser agent.
Such code is executed by ag_web without the model reasoning about it (the fast path, see agents/ag_web/recipes.py).
Skills aren't written by an agent mid-task, but by the post-task review (ag_judge) and the nightly cleanup (ag_night)."""
import ast
import re
import threading

from sena import config
from sena.core import moment, safety, sandbox, status, store
from sena.core.tools import string, tool
from sena.memory import index

_lock = threading.Lock()


def _slug(name):
    return re.sub(r"[^\w-]+", "-", name.strip().lower()).strip("-")


def ref(name):
    """The skill's reference in the registry: its file name without the extension."""
    return _slug(name)


def _path(name):
    return config.SKILLS_DIR / (_slug(name) + ".md")


def _code_path(name):
    return _path(name).with_suffix(".py")


def names():
    return sorted(p.stem for p in config.SKILLS_DIR.glob("*.md"))


_catalog = {"key": None, "rows": []}   # the skill list is read from disk only when the files have changed


def catalog():
    """Skills as data (for the screen and the prompt): [{name, when, has_code}]."""
    key = tuple((p.name, p.stat().st_mtime_ns) for p in sorted(config.SKILLS_DIR.glob("*")) if p.is_file())
    if key != _catalog["key"]:
        _catalog["rows"] = [{"name": n, "when": _path(n).read_text(encoding="utf-8").split("\n", 1)[0].strip(), "has_code": _code_path(n).exists()}
                            for n in names()]
        _catalog["key"] = key
    return list(_catalog["rows"])


def listing():
    # list for the prompt: name — when to apply (and a mark if code exists)
    return "\n".join(f"- {s['name']} — {s['when']}" + (" [есть код]" if s["has_code"] else "") for s in catalog())


KIND = "навык"


def relevant(text, k=5):
    """Skills close to a request (by meaning and words): the coordinator gets these with each message, not the whole list."""
    known = {s["name"]: s for s in catalog()}
    rows = index.search(text, kinds=[KIND], k=k, min_cosine=0.35, scope="all")
    picked = [known[r["ref"]] for r in rows if r["ref"] in known]
    return "\n".join(f"- {s['name']} — {s['when']}" + (" [есть код]" if s["has_code"] else "") for s in picked)


def sync_index():
    """Skills in the registry: name, when to apply and the recipe (kind навык), dated by the file."""
    rows = [(n, f"{n}: {_path(n).read_text(encoding='utf-8')}", _path(n).stat().st_mtime) for n in names()]
    return index.sync(KIND, rows)


def _indexed(name):
    # a written or fixed skill is findable at once, not after the night's reconciliation (one place for what Sena can recall);
    # a busy registry is told in the journal and catches up at the reconciliation
    index.try_put(KIND, _slug(name), f"{_slug(name)}: {_path(name).read_text(encoding='utf-8')}", _path(name).stat().st_mtime)


TWIN = 0.85   # a new skill this close in meaning to an existing one is the same class of task: it grows there, not twice
_warned = set()   # (review or task, name) told about its twin once: the second identical call in it is a deliberate new skill


def _turn():
    # the review or task a call belongs to: the task its costs are billed to (a review's), else the running one — one review's
    # warning switched the check off for every later review of that name
    return getattr(status.local, "tid", None) or (status.current() or {}).get("id")


def _twin(name, when, recipe):
    """The existing skill a new one repeats, or None. Told once per review: a different task that only looks alike is saved
    on the second call (addyosmani's agent-skills lints the catalog for duplicates; the judge was told "don't make a new one",
    and nothing held it). A registry that can't answer allows."""
    slug = _slug(name)
    if (_turn(), slug) in _warned:
        return None
    try:
        rows = index.search(f"{slug}: {when}\n{recipe}", kinds=[KIND], k=3, scope="all")
    except Exception:
        return None
    known = set(names())
    top = max((r for r in rows if r["ref"] != slug and r["ref"] in known), key=lambda r: r.get("cosine", 0), default=None)
    if top and top.get("cosine", 0) >= TWIN:
        _warned.add((_turn(), slug))
        return top["ref"]
    return None


def refused(name, why, text):
    """A change to a skill that didn't take (the exam rolled it back): kept beside the skill so the next review sees it and
    doesn't make it again (addyosmani's agent-skills keeps a ledger of rejected changes)."""
    with _lock:
        rows = store.load_json(USAGE_FILE, {})
        row = rows.setdefault(_slug(name), {"created": moment.now()})
        row["refused"] = (row.get("refused", []) + [{"t": moment.now(), "why": why, "text": safety.visible(str(text))[:200]}])[-5:]
        store.save_json(USAGE_FILE, rows, indent=1)


def refusals(name):
    """What earlier changes to this skill didn't take, for the review that is about to change it: text or ""."""
    items = store.load_json(USAGE_FILE, {}).get(_slug(name), {}).get("refused", [])
    return "\n".join(f"- {moment.local(i['t']).strftime('%d.%m')}: {i['why']}: «{i['text']}»" for i in items)


USAGE_FILE = config.STATE_DIR / "skill_usage.json"   # per skill: when made, how often read, run as code, fixed; the last time
STALE_DAYS = 90   # a skill nobody read or ran this long goes to the archive at night (skills/archive), never deleted


FAILS_IN_ROW = 2   # a code recipe that failed this many times in a row is no longer offered to the fast path, until it is fixed


def used(name, what):
    """Count a skill's life: viewed (read_skill), ran (its code did the task without the model), failed (its code ran and
    didn't), patched (written or fixed). A failed run counts as a failure, not as life (Anthropic's skill evaluations measure
    "chosen" and "worked" apart): two in a row take the recipe off the fast path, and a fix gives it its chance back."""
    with _lock:
        rows = store.load_json(USAGE_FILE, {})
        row = rows.setdefault(_slug(name), {"created": moment.now()})
        row[what] = row.get(what, 0) + 1
        if what in ("ran", "patched"):
            row["fails_in_row"] = 0
        elif what == "failed":
            row["fails_in_row"] = row.get("fails_in_row", 0) + 1
        row["last"] = moment.now()
        store.save_json(USAGE_FILE, rows, indent=1)


def broken(name):
    """Whether a code recipe failed FAILS_IN_ROW times in a row and hasn't been fixed since."""
    return store.load_json(USAGE_FILE, {}).get(_slug(name), {}).get("fails_in_row", 0) >= FAILS_IN_ROW


def archive_stale(now=None):
    """Skills unread and unrun for STALE_DAYS move to skills/archive: the list stays short and relevant, nothing is lost —
    moving a file back restores it (Hermes Agent's curator: stale, then archived, never deleted). Returns the names moved."""
    now = now or moment.now()
    rows, moved = store.load_json(USAGE_FILE, {}), []
    folder = config.SKILLS_DIR / "archive"
    for name in names():
        row = rows.get(_slug(name))   # the count is kept by the slug: a hand-made «Mac-Volume.md» was "seen" anew every night
        if not row:   # a skill from before the counting began: its clock starts now
            used(name, "seen")
            continue
        if now - row.get("last", row.get("created", now)) < STALE_DAYS * 86400:
            continue
        folder.mkdir(exist_ok=True)
        for suffix in (".md", ".py"):
            source = config.SKILLS_DIR / f"{name}{suffix}"   # the file as it is named on disk
            if source.exists():
                source.rename(folder / source.name)
        for r in {name, _slug(name)}:   # out of the registry now, not at the night's reconciliation
            index.drop(KIND, r)
        moved.append(name)
    return moved


def text(name):
    """A skill's whole text (and its code) as it is, read without counting as use: the night's tidy, the notes' sync and the
    review read every skill, and counting that made every skill look used today, so none ever went to the archive
    (skillreaper's rule: a skill is used when it fires in a task, not when it is read)."""
    path = _path(name)
    if not path.exists():
        return f"Навыка «{name}» нет. Есть: {', '.join(names())}"
    whole = path.read_text(encoding="utf-8")
    code = _code_path(name)
    if code.exists():
        whole += "\nКод (запускается без модели):\n" + code.read_text(encoding="utf-8")
    return whole


def read_skill(name):
    if _path(name).exists():
        used(name, "viewed")   # an agent or the coordinator read it for a task: that is use
    return text(name)


def _run_function(source):
    # the run(page, ...) function from the recipe's code, or None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "run" and node.args.args and node.args.args[0].arg == "page":
            return node
    return None


WHEN_MAX, RECIPE_MAX = 400, 6000   # a skill rides in the context whole once read: a long one is a log, not a recipe (Hermes' linter);
                                   # "when" holds what it does, when to use it and when not (Anthropic's skill descriptions)
KEEP = 0.6                         # a rewrite must keep this share of the old recipe's words — the hard-won parts don't vanish


def _words(text):
    return set(re.findall(r"\w{3,}", text.lower()))


CODE_KEEP = 0.5   # a rewritten recipe's code keeps at least this share of the old one
# what breaks a replayed recipe the next day: an element's number from this page's listing, an id a framework generates anew on
# every load, a selector of one or two characters (Skyvern's validators on generated scripts)
FRAGILE = ((re.compile(r"\b(?:click|fill|human_click|human_type)\(\s*page\s*,\s*['\"]?f?\d+(?:\.\d+)?['\"]?\s*[,)]"), "номера элементов из списка меняются от загрузки к загрузке"),
           (re.compile(r"data-sena-id"), "служебные номера Сены на странице живут одну загрузку"),
           (re.compile(r"#(?:ember|react-select-|mui-|radix-|headlessui-|:r)[\w:-]*\d"), "такие id сайт создаёт заново при каждой загрузке"),
           (re.compile(r"get_by_text\(\s*['\"].{1,2}['\"]"), "текст из одной-двух букв найдёт что угодно"))


def _fragile(code):
    """Why a recipe's code would break when replayed on another day; None — it looks steady."""
    return next((why for pattern, why in FRAGILE if pattern.search(code)), None)


def write_skill(name, when, recipe, code=None, by="model"):
    """Create or completely rewrite a skill. code: None — leave the code alone, "" — remove it, text — replace it.
    by="owner" — the owner rewrote it in the apps: his word wins over the guards that keep the judge's and the night's rewrites
    from losing what was learned (a twin, a recipe or code that drops most of the old one); the old text still goes to the
    skill's versions. The model's tool never passes it (the brain refuses fields the tool doesn't describe)."""
    owner = by == "owner"
    when, recipe = safety.visible(when).strip(), safety.visible(recipe).strip()
    if not _slug(name) or not when or not recipe:
        return "Не сохранено: нужны имя (буквы, цифры, дефис), строка «когда применять» и рецепт."
    if len(when) > WHEN_MAX or len(recipe) > RECIPE_MAX:
        return f"Не сохранено: навык слишком длинный («когда» до {WHEN_MAX} знаков, рецепт до {RECIPE_MAX}). Оставь суть и точные команды, без истории случаев."
    twin = None if owner or _path(name).exists() else _twin(name, when, recipe)
    if twin:
        return (f"Не сохранено: похоже на навык «{twin}». Прочитай его (read_skill) и дополни (patch_skill). "
                "Если это другой класс задач — повтори этот вызов: второй раз сохраню.")
    if code:
        try:
            ast.parse(code)
        except SyntaxError as e:
            return f"Не сохранено: ошибка в коде: {e}. Исправь и повтори."
        if _run_function(code) is None:
            return "Не сохранено: в коде нужна функция run(page, ...) — первый параметр page."
        refused = sandbox.check(code)
        if refused:   # a recipe runs in the same sandbox as the agent's code: what won't run there isn't kept
            return f"Не сохранено: {refused}. Перепиши код без этого."
        fragile = _fragile(code)
        if fragile:
            return f"Не сохранено: {fragile}. Находи элементы по роли и тексту (page.get_by_role, get_by_text), как увидит человек."
    with _lock:   # the old text is read where it is replaced: two reviews rewriting one skill at once compared against one past
        old = _path(name).read_text(encoding="utf-8").split("\n", 1)[-1] if _path(name).exists() else ""
        was = _words(old)
        if not owner and was and len(was & _words(recipe)) < KEEP * len(was):   # the night merge and the judge rewrite whole skills: what was learned stays
            return ("Не сохранено: новый рецепт теряет большую часть прежнего. Поправь точечно (patch_skill) "
                    "или перепиши, сохранив прежние рабочие шаги и ловушки.")
        old_code = _code_path(name).read_text(encoding="utf-8") if code and _code_path(name).exists() else ""
        if not owner and len(old_code) > 200 and len(code.strip()) < len(old_code) * CODE_KEEP:   # Skyvern's reviewer: a fix adds, it doesn't gut
            return "Не сохранено: новый код вдвое короче прежнего — так теряются проверенные ветки. Исправь точечно, сохранив прежнее."
        store.write_versioned(_path(name), when + "\n" + recipe + "\n")
        if code:
            store.write_versioned(_code_path(name), code.strip() + "\n")
        elif code == "":
            store.remove_versioned(_code_path(name))
    used(name, "patched")
    _indexed(name)
    return f"Навык «{name}» сохранён" + (" вместе с кодом" if code else "")


def patch_skill(name, old, new):
    """A targeted fix inside a skill: the old text, word for word and found exactly once, becomes the new one — the rest of the
    recipe stays as it was (Hermes Agent's skill patch: only the changed text goes through the model)."""
    path = _path(name)
    old, new = str(old), safety.visible(new)
    with _lock:   # read and written under one lock: two reviews patching one skill at once kept only the second fix
        if not path.exists():
            return f"Навыка «{name}» нет. Есть: {', '.join(names())}"
        text = path.read_text(encoding="utf-8")
        found = text.count(old) if old.strip() else 0
        if found != 1:
            return ("Не исправлено: " + ("такого текста в навыке нет" if not found else f"этот текст встречается {found} раза") +
                    ". Прочитай навык (read_skill) и возьми кусок дословно, чтобы он был один.")
        fixed = text.replace(old, new)
        if len(fixed) > WHEN_MAX + RECIPE_MAX:
            return "Не исправлено: навык стал бы слишком длинным"
        store.write_versioned(path, fixed)
    used(name, "patched")
    _indexed(name)
    return f"Навык «{name}» исправлен"


def delete_skill(name):
    path = _path(name)
    with _lock:
        if not path.exists():
            return f"Навыка «{name}» нет"
        store.remove_versioned(path)   # the last text stays in the skill's history file
        store.remove_versioned(_code_path(name))
    try:
        index.drop(KIND, _slug(name))
    except Exception:
        pass
    return f"Навык «{name}» удалён"


def code_recipes():
    # skills that have code and still work: [{"name", "when", "signature", "doc", "params", "required", "source"}]
    out = []
    for name in names():
        path = _code_path(name)
        if not path.exists() or broken(name):   # failing again and again: the agent solves it, the review fixes the recipe
            continue
        source = path.read_text(encoding="utf-8")
        fn = _run_function(source)
        if fn is None:
            continue
        params = [a.arg for a in fn.args.args[1:]]
        required = params[:len(params) - len(fn.args.defaults)]
        out.append({
            "name": name,
            "when": _path(name).read_text(encoding="utf-8").split("\n", 1)[0].strip(),
            "signature": f"run({ast.unparse(fn.args)})",
            "doc": ast.get_docstring(fn) or "",
            "params": params,
            "required": required,
            "source": source,
        })
    return out


READ_TOOL = tool("read_skill", "Прочитать полный рецепт навыка из списка «Навыки» перед похожей задачей", {"name": string()}, ["name"])
WRITE_TOOL = tool("write_skill", "Создать или полностью переписать навык",
                  {"name": string("короткое имя латиницей через дефис, например mac-volume"),
                   "when": string("одна строка без шагов: что делает и для каких просьб, словами Темы; «даже если …» — где агент решил бы, "
                                  "что справится без навыка; «не для …» — где похожая задача не подходит"),
                   "recipe": string("рецепт: несколько коротких строк с точными командами"),
                   "code": string("необязательно, для браузерных задач: Python-функция run(page, ...) — проверенный код, "
                                  "который выполнится без модели. Пустая строка убирает старый код, не указан — код остаётся")},
                  ["name", "when", "recipe"])
PATCH_TOOL = tool("patch_skill", "Точечно исправить навык: кусок рецепта дословно (он должен встречаться один раз) заменить новым. Лучше полной перезаписи",
                  {"name": string(), "old": string("кусок из навыка дословно"), "new": string("чем заменить")}, ["name", "old", "new"])
