"""Sena's note vault in Obsidian format: a plain folder of markdown files (by default ~/Desktop/Obsidian_sena).
Sena has her own vault; she doesn't touch your other working vaults.

The ownership rule that makes edits from both sides safe: only Sena writes between the <!-- sena:begin --> and
<!-- sena:end --> markers (automatically, it gets updated); everything else in the file is yours and stays untouched.
Free-form notes (knowledge, projects, ideas) are written by Sena with the note_* tools; you edit them as usual."""
import json
import re
from datetime import date

from sena import config
from sena.core import store
from sena.core.safety import cut, external
from sena.core.tools import clamp, string, tool
from sena.memory import index

BEGIN, END = "<!-- sena:begin -->", "<!-- sena:end -->"
HINT = "Этот блок обновляет Сена — свои заметки пиши ниже."   # how an automatic block starts: edits inside it are overwritten
FOLDERS = ["Дневник", "Люди", "Проекты", "Организации", "Места", "Знания", "Идеи", "Входящие", "Память", "Навыки", "Архив", "Вложения"]
ENTITIES = {"Проекты": "проект", "Организации": "организация", "Места": "место"}   # a note in these folders is also an entity other records link to
MAX_NOTE = 100_000
_FRONT = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
_BAD = re.compile(r'[\\/:*?"<>|#^\[\]]+')


def root():
    return config.VAULT_DIR


def ensure():
    """Create the vault: folders, Obsidian settings (daily notes in the "Дневник" folder, attachments in "Вложения")."""
    for folder in FOLDERS:
        (root() / folder).mkdir(parents=True, exist_ok=True)
    settings = root() / ".obsidian"
    settings.mkdir(exist_ok=True)
    for name, data in {
        "app.json": {"newFileLocation": "folder", "newFileFolderPath": "Входящие", "attachmentFolderPath": "Вложения",
                     "alwaysUpdateLinks": True},
        "daily-notes.json": {"folder": "Дневник", "format": "YYYY-MM-DD"},
        "core-plugins.json": ["file-explorer", "global-search", "switcher", "graph", "backlink", "outgoing-link", "tag-pane",
                              "properties", "page-preview", "daily-notes", "templates", "outline", "word-count", "file-recovery"],
    }.items():
        if not (settings / name).exists():
            store.save_json(settings / name, data, indent=1)
    return root()


def clean_name(name, limit=80):
    return (_BAD.sub(" ", name).strip(" .") or "без названия")[:limit].strip()


def _safe(path):
    """Note path inside the vault (.md by default); escaping the vault and service folders are forbidden."""
    raw = [p for p in re.split(r"[\\/]+", str(path).strip()) if p and p != "."]
    if not raw or any(p.startswith(".") for p in raw):   # "..", ".obsidian" and other service names
        raise ValueError(f"Недопустимый путь: {path}")
    parts = [clean_name(p) for p in raw]
    if not parts[-1].lower().endswith(".md"):
        parts[-1] += ".md"
    return root().joinpath(*parts)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(path, text, mode=0o644)   # these are your notes, not secrets: read by Obsidian and other programs


def _meta_value(value):
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(json.dumps(str(v), ensure_ascii=False) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False) if isinstance(value, str) else str(value)


def frontmatter(meta):
    return "---\n" + "\n".join(f"{k}: {_meta_value(v)}" for k, v in meta.items()) + "\n---\n"


def set_meta(text, key, value):
    """Update a single property in the note's frontmatter (other properties, including yours, are left untouched)."""
    line = f"{key}: {_meta_value(value)}"
    m = _FRONT.match(text)
    if not m:
        return frontmatter({key: value}) + text
    body = m.group(1)
    if re.search(rf"^{re.escape(key)}:.*$", body, re.M):
        body = re.sub(rf"^{re.escape(key)}:.*$", lambda _: line, body, flags=re.M)
    else:
        body += "\n" + line
    return f"---\n{body}\n---\n" + text[m.end():]


def upsert(path, meta, title, block, tail=""):
    """Create a note or update only the block between the markers. Anything outside the block is left alone.
    Returns True if the file changed."""
    target = _safe(path)
    block = f"{BEGIN}\n{block.strip()}\n{END}"
    old = target.read_text(encoding="utf-8") if target.exists() else None
    if old is None:
        new = frontmatter({**meta, "sena": "auto"}) + f"# {title}\n\n{block}\n" + (f"\n{tail}\n" if tail else "")
    elif BEGIN in old and END in old:   # the owner's part is what follows the last end marker: a block written twice heals here
        new = old.split(BEGIN, 1)[0] + block + old.rsplit(END, 1)[1]
        for key, value in meta.items():
            if key in ("aliases", "updated"):
                new = set_meta(new, key, value)
    else:   # a note you created without the block: append the block at the end
        new = old.rstrip() + f"\n\n{block}\n"
    if new == old:
        return False
    _write(target, new)
    return True


def read_note(path):
    target = _safe(path)
    if not target.is_file():
        return f"Заметки «{path}» нет"
    return external(cut(target.read_text(encoding="utf-8", errors="replace"), 12000), f"заметка {target.relative_to(root())}", own=True)


def write_note(path, text, mode="create"):
    """create — a new note (error if one already exists), append — add to the end, replace — overwrite.
    In automatic notes the block between the markers is never changed: only what comes after it is appended to or replaced."""
    result = _write_note(path, text, mode)
    if result.startswith(("Создана", "Дописано", "Заменен")):
        sync_index()   # one index reconciliation per write
    return result


def _write_note(path, text, mode):
    if len(text) > MAX_NOTE:
        return f"Слишком длинно ({len(text)} знаков, максимум {MAX_NOTE}): раздели на несколько заметок"
    ensure()
    target = _safe(path)
    shown = target.relative_to(root())
    if mode == "create":
        if target.exists():
            return f"Заметка «{shown}» уже есть: допиши в неё (append) или замени (replace)"
        body = text if _FRONT.match(text) else frontmatter({"created": date.today().isoformat(), "source": "sena"}) + text
        _write(target, body.rstrip() + "\n")
        return f"Создана заметка «{shown}»"
    if not target.exists():
        return f"Заметки «{shown}» нет: сначала создай (create)"
    old = target.read_text(encoding="utf-8")
    if mode == "append":
        _write(target, old.rstrip() + "\n\n" + text.strip() + "\n")
        return f"Дописано в «{shown}»"
    if mode == "replace":
        if BEGIN in old and END in old:
            cut_at = old.index(END) + len(END)
            _write(target, old[:cut_at] + "\n\n" + _owner_part(text, old[:cut_at]) + "\n")
            return f"Заменена часть «{shown}» после автоматического блока"
        _write(target, text.rstrip() + "\n")
        return f"Заменена заметка «{shown}»"
    return "mode — одно из: create, append, replace"


def _owner_part(text, sena):
    """What of a text written over a note with Sena's block is the owner's. The Mac sends only his part; the phone sends the
    whole file back, and with a marker line deleted by hand (it looks like junk) the end marker's absence took his lines away
    with the block, or both markers' absence made Sena's block and its hint his own lines — the sync then took them for facts
    he wrote. sena — the note's part up to its end marker: a line of it (the title, the block, the markers) is never his."""
    if END in text:
        return _FRONT.sub("", text.rsplit(END, 1)[1].strip(), count=1).strip()
    hers = {line.strip() for line in sena.splitlines() if line.strip()} | {BEGIN, END}
    body = _FRONT.sub("", text.strip(), count=1)
    return "\n".join(line for line in body.splitlines() if line.strip() not in hers).strip()


def _notes(folder=""):
    base = root()
    if folder:
        parts = [p for p in re.split(r"[\\/]+", str(folder).strip()) if p and p != "."]
        if any(p.startswith(".") for p in parts):   # ".." and service folders: never escape the vault, an absolute path is read as nested
            return []
        base = root().joinpath(*parts)
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*.md") if not any(part.startswith(".") for part in p.relative_to(root()).parts))


KIND = "заметка"   # one kind for all notes; who maintains the note (the owner or Sena herself) is the record's author attribute
CHUNK = 800   # a note enters the index in paragraph-sized chunks, so the matching paragraph is found rather than the whole note


def _is_auto(text):
    m = _FRONT.match(text)
    return bool(m and re.search(r"^sena:\s*\"?auto\"?\s*$", m.group(1), re.M))


def _front_list(text, key):
    # a frontmatter property as a list: key: ["a", "b"], key: [a, b] or key: a
    m = _FRONT.match(text)
    line = re.search(rf"^{key}:\s*(.*)$", m.group(1), re.M) if m else None
    if not line:
        return []
    value = line.group(1).strip()
    if value.startswith("["):
        return [v.strip().strip('"\'') for v in value.strip("[]").split(",") if v.strip().strip('"\'')]
    return [value.strip('"\'')] if value else []


def _aliases(text):
    # other names of an entity from its frontmatter: aliases: ["KSclay", "кс клэй"]
    return _front_list(text, "aliases")


LINK_KEYS = (("people", "человек"), ("project", "проект"), ("organization", "организация"), ("place", "место"))


def _link_note(rel, text, kept=()):
    """What the note is about: [[Имя]] links in the text and people/project/organization/place in the frontmatter
    become links from the note (its first chunk) to the person or entity — Obsidian's own idea of a link, kept in the registry.
    A link taken out of the text closes; the owner's own tags (kept — index.kept_links) stay whatever the text says."""
    wanted = {(None, n.strip()) for n in re.findall(r"\[\[([^\]|#]+)", text)}
    for key, kind in LINK_KEYS:
        wanted |= {(kind, n) for n in _front_list(text, key)}
    kinds, made, head = [kind for _, kind in LINK_KEYS], set(), f"{rel}#0"
    for kind, name in wanted:
        for k in ([kind] if kind else kinds):
            ref = index.lookup(k, name)
            if ref and ref != rel:   # an entity note mentioning itself is not a link
                index.link(KIND, head, k, ref, "about", keep=False)   # recomputed from the note text on every sync
                made.add((k, ref))
                break
    for l in index.links_of(KIND, head, rel="about"):
        if l["out"] and (l["kind"], l["ref"]) not in made and (KIND, head, l["kind"], l["ref"], "about") not in kept:
            index.unlink(KIND, head, l["kind"], l["ref"], "about")
    kind, _, ref = _entity(rel)
    if kind and ref != rel:   # a note in a project's folder is a part of the project
        index.link(KIND, head, kind, ref, "part_of", keep=False)


HEADING = re.compile(r"^(#{1,6})\s+(.+)$")


def _chunks(text):
    """A note in chunks of about CHUNK characters. A heading always starts a new chunk, and every chunk after the first in its
    section is headed by its section's path: a paragraph added above moves nothing below the next heading, and «1200 евро» is
    found as a price because it sits under «Цены» (Flowise splits by headings; RAGFlow repeats the heading path on each piece)."""
    body = _FRONT.sub("", text, count=1)
    out, cur, path = [], "", []
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para:
            continue
        heading = HEADING.match(para.splitlines()[0])
        if heading:
            if cur:
                out.append(cur)
                cur = ""
            level = len(heading.group(1))
            path = [h for h in path if h[0] < level] + [(level, heading.group(2).strip()[:80])]
        for piece in index.pieces(para, CHUNK):
            if cur and len(cur) + len(piece) > CHUNK:
                out.append(cur)
                cur = ""
            if not cur and path and not heading:
                cur = "[" + " > ".join(h for _, h in path) + "]"
            cur = (cur + "\n" + piece).strip()
            heading = None
    if cur:
        out.append(cur)
    return out or [""]


def _entity(rel):
    """(kind, name, ref) of the project, organization or place a note stands for or belongs to; (None, None, None) for any
    other note. "Проекты/KSclay.md" is the project KSclay, and so is the folder "Проекты/KSclay/": the notes in it are the
    project's parts, never projects of their own — taken for projects, «План» and «Требования» were mentioned by every message."""
    parts = rel.split("/")
    kind = ENTITIES.get(parts[0]) if len(parts) > 1 else None
    if not kind:
        return None, None, None
    name = parts[1].removesuffix(".md") if len(parts) == 2 else parts[1]
    return kind, name, f"{parts[0]}/{name}.md"


def sync_index():
    """Index over notes: text chunks with the file's path and timestamp; only changed ones are reread.
    The file is the source of truth: edits made in Obsidian are picked up via mtime. A note in an entity folder
    (projects, organizations, places) is also an entity record, and its name and aliases join the mention dictionary."""
    had = {}   # note -> the numbers of its chunks in the registry
    for ref, when in index.refs(KIND).items():
        note, _, n = ref.rpartition("#")   # the chunk's number is after the last "#": a note's own name may have one
        had.setdefault(note, {})[n] = when
    seen, changed, entities, fresh_notes = set(), 0, {}, []
    for path in _notes():
        rel = str(path.relative_to(root()))
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:   # deleted while the vault was walked
            continue
        seen.add(rel)
        kind, name, ref = _entity(rel)
        text = None
        if kind:   # entity notes are few: read every time, so the mention dictionary and the entity records are always whole
            text = path.read_text(encoding="utf-8", errors="replace")
            e = entities.setdefault((kind, ref), {"name": name, "aliases": [], "summary": "", "parts": [], "time": 0.0})
            e["time"] = max(e["time"], mtime)
            if rel == ref:   # the entity's own note: its other names and first lines
                e["aliases"], e["summary"] = _aliases(text), _chunks(text)[0][:300]
            else:
                e["parts"].append(path.stem)
        if had.get(rel, {}).get("0") == mtime:
            continue
        text = path.read_text(encoding="utf-8", errors="replace") if text is None else text   # a non-UTF-8 note shouldn't crash the whole listing
        meta = {"author": "sena" if _is_auto(text) else "owner", "facet": rel.split("/", 1)[0] if "/" in rel else None}
        chunks = _chunks(text)
        known = index.vectors_of(KIND, f"{rel}#")   # its paragraphs' embeddings, kept while their rows are rewritten in turn
        for i in range(1, len(chunks)):   # identical paragraphs keep their own embeddings
            index.put(KIND, f"{rel}#{i}", f"{path.stem}: {chunks[i]}", mtime, meta=meta, known=known)
        for n in had.get(rel, {}):
            if n.isdigit() and int(n) >= len(chunks):
                index.drop(KIND, f"{rel}#{n}")   # leftover chunks from a note that got shorter
        # the first chunk last: its time marks the note as read, so a write cut off midway is read again next time. Exactly the
        # file's time: put keeps an unchanged text's old time within a second, and a note written twice in one second was then
        # read again on every sync until its next write
        index.put(KIND, f"{rel}#0", f"{path.stem}: {chunks[0]}", mtime, meta=meta, known=known, exact=True)
        fresh_notes.append((rel, text))
        changed += 1
    gone = set(had) - seen
    for rel in gone:
        index.drop(KIND, prefix=f"{rel}#")
    names = {kind: {} for kind in ENTITIES.values()}
    for (kind, ref), e in entities.items():
        about = e["summary"] or "заметки: " + ", ".join(sorted(e["parts"]))   # a folder without a note of its own: what is in it
        index.put(kind, ref, e["name"] + (f" ({', '.join(e['aliases'])})" if e["aliases"] else "") + f": {about}", e["time"], meta={"aliases": e["aliases"]})
        for n in [e["name"], *e["aliases"]]:
            names[kind][n] = ref
    for kind in ENTITIES.values():   # an entity whose notes are all gone leaves — and so does a deeper note once taken for one
        for ref in index.refs(kind):
            if (kind, ref) not in entities:
                index.drop(kind, ref)
        index.register_names(kind, names[kind])
    kept = index.kept_links() if fresh_notes else set()
    for rel, text in fresh_notes:   # after the names are registered: a note may link to an entity that appeared in this same pass
        _link_note(rel, text, kept)
    return {"added": changed, "removed": len(gone)}


def _find(query, folder, limit):
    # None — empty query; otherwise [(weight, path, text snippet)], best first: all words, via the FTS index
    if not query.strip():
        return None
    sync_index()
    rows = index.search(query, kinds=[KIND], k=200, match="all", text_only=True, scope="all")
    found, seen = [], set()
    for r in rows:
        rel = r["ref"].rsplit("#", 1)[0]
        if folder and not rel.startswith(folder.rstrip("/") + "/"):
            continue
        if rel in seen:
            continue
        seen.add(rel)
        found.append((r["score"], rel, r["text"].split(": ", 1)[-1][:200]))
    return found[:clamp(limit, FOUND, 1, FOUND_MOST)]


FOUND, FOUND_MOST = 50, 100   # notes one search of the screen shows (it showed ten, silently, and counted them as the total)


def find(query, folder="", limit=FOUND):
    """Search results as data (for the screen): [{path, title, folder, snippet}]."""
    return [{"path": p, "title": p.rsplit("/", 1)[-1][:-3], "folder": p.rsplit("/", 1)[0] if "/" in p else "", "snippet": s}
            for _, p, s in (_find(query, folder, limit) or [])]


def _preview(path, limit=160):
    # the first lines of text without frontmatter, heading, service markers or the automatic block's hint, for the card in the
    # grid view (with the hint every person's and memory note read the same)
    text = _FRONT.sub("", path.read_text(encoding="utf-8", errors="replace"), count=1)
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith(("#", "<!--", HINT))]
    return re.sub(r"[*`\[\]]", "", " ".join(lines))[:limit]


def notes(folder=""):
    """Notes as data (for the screen): [{path, title, folder, updated, preview}], newest first."""
    out = [{"path": str(p.relative_to(root())), "title": p.stem, "folder": "" if p.parent == root() else str(p.parent.relative_to(root())),   # «v1.2» keeps its dot
            "updated": p.stat().st_mtime, "preview": _preview(p)} for p in _notes(folder)]
    return sorted(out, key=lambda n: -n["updated"])


def read(path):
    """The note's text as is (for the screen); None if it doesn't exist."""
    target = _safe(path)
    return target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None


def delete_note(path):
    """Delete a note file (the phone app's own action). Index entries of both kinds are dropped, since a note
    can be a plain one or one of Sena's own "auto" notes."""
    target = _safe(path)
    if not target.is_file():
        return f"Заметки «{path}» нет"
    rel = str(target.relative_to(root()))
    target.unlink()
    index.drop(KIND, prefix=f"{rel}#")
    sync_index()   # the entity it stood for or was a part of follows: a project with notes left in its folder stays
    return f"Заметка «{rel}» удалена"


def listing(folder=""):
    notes = _notes(folder)
    if not notes:
        return "Заметок нет" + (f" в «{folder}»" if folder else "")
    by_folder = {}
    for path in notes:
        by_folder.setdefault(str(path.parent.relative_to(root())), []).append(path.stem)
    return external("\n".join(f"{f}/ ({len(names)}): " + ", ".join(names[:30]) + (" …" if len(names) > 30 else "")
                              for f, names in sorted(by_folder.items())), "заметки хранилища", own=True)


TOOLS = [
    tool("note_read", "Прочитать заметку Obsidian (твои справки) по пути, например Проекты/KSclay.md. Заметки самого Темы — в Luma: luma note_find, note_read.",
         {"path": string()}, ["path"]),
    tool("note_list", "Список заметок хранилища по папкам (можно одной папки).", {"folder": string("Папка, необязательно")}),
    tool("note_write", "Записать заметку в хранилище Obsidian: это твои справки и материалы, а не записи Темы (его заметки, идеи и мысли — в Luma: note_add, inbox_add; сюда — только если он сам сказал «в Obsidian»). "
                       "Папки: Проекты (по файлу на проект), Знания (справки и выводы с источниками), Идеи и Входящие (твоё, сырое). mode: create — новая (по умолчанию), append — дописать в конец, replace — заменить, только по прямой просьбе Темы: заметка может быть его. "
                       "Ссылайся на людей и проекты через [[Имя]].",
         {"path": string("Например Знания/Налоги на маркетплейсах.md"), "text": string("Текст заметки в markdown"),
          "mode": {**string(), "enum": ["create", "append", "replace"]}}, ["path", "text"]),
]
FUNCTIONS = {"note_read": read_note, "note_list": listing, "note_write": write_note}
