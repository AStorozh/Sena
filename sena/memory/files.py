"""Files in the registry. Every file Sena received, downloaded or made has a line in data/journal/files.jsonl (core/attachments.register):
what it is, the message or prompt it came with, which task it belongs to. From that line the file becomes a record of kind "файл"
with links to its task and to the people and projects it mentions — so "the picture we drew for the site" is found by its context,
not by a lucky word in the file name. What a photo or a PDF holds is read on the Mac (core/local.py: the text of a document or a scan,
the text in a screenshot, what a photo shows) once, in the background, and kept in the manifest as a new line for the same file —
so "the screenshot with the 502 error" or "the photo of the cat" is found by what is in it. The manifest is the source of truth;
the registry rows are rebuilt from it (sync_index)."""
from pathlib import Path

from sena import config
from sena.core import attachments, events, local, moment, safety
from sena.memory import index, task_log

KIND = "файл"
EXCERPT = 600   # how much of a readable file's text goes into the record (text, Word, spreadsheets — read locally, never through the model)
CONTENT = 1500  # how much of what was read from a photo or a PDF is kept and goes into the record
READ_KINDS = ("image", "pdf")   # read in the background on the Mac
READ_LIMIT = 50 * 1024 * 1024   # bigger files aren't read for the registry
AUTO_READ = True   # a new file is read right after it arrives (tests turn this off and call read() themselves)
MADE = ("gemini", "browser", "gmail", "drive")   # sources where the file is the task's result, not the owner's attachment


def _path(entry):
    return Path(entry["path"]) if entry["path"].startswith("/") else config.DATA / entry["path"]


READABLE = ("text", "doc", "sheet")   # read on the spot, never through the model


def _excerpt(entry):
    # what a readable file says, from the manifest once it was read there (sync_index keeps it): the reconciliation parsed
    # every document again at every startup and night
    if "excerpt" in entry:
        return entry["excerpt"]
    path = _path(entry)
    if entry.get("kind") not in READABLE or not path.is_file():
        return ""
    try:
        body = attachments.text_of(path) or ""
    except Exception:
        body = ""
    return " ".join(body.split())[:EXCERPT]


def _text(entry):
    text = entry["name"]
    if entry.get("caption") and entry["caption"] != entry["name"]:
        text += f": {entry['caption']}"
    if entry.get("task") and entry["task"] not in text:
        text += f" — задача: {entry['task']}"
    excerpt = _excerpt(entry)
    if excerpt:
        text += " — " + excerpt
    if entry.get("content"):
        text += " — " + entry["content"][:CONTENT]
    return text


def _meta(entry):
    return {"facet": entry.get("kind") or "other", "source": entry.get("source", ""), "tid": entry.get("tid", "")}


def _link_task(entry):
    if entry.get("tid"):
        index.link(KIND, entry["path"], "задача", entry["tid"], "made_by" if entry.get("source") in MADE else "attached_to", entry["t"], keep=False)


def on_registered(entry):
    index.put(KIND, entry["path"], _text(entry), entry["t"], entry.get("tz", ""), _meta(entry))
    _link_task(entry)
    if AUTO_READ and entry.get("kind") in READ_KINDS:
        safety.background(read, entry, what="Чтение файла на Mac")


attachments.registered_hook = on_registered


def _content(path, what):
    # a PDF: its text (scans recognized by their picture); a photo: the text in it and what it shows — the home model's two
    # sentences if it has eyes, the system's labels otherwise
    if what == "pdf":
        return local.pdf_text(path, max_pages=20, purpose="файл в реестр")
    parts = []
    text = local.ocr(path, purpose="файл в реестр")
    if text.strip():
        parts.append("текст: " + text)
    seen = ""
    if local.installed()["eyes"]:
        try:
            seen = local.describe(path, purpose="файл в реестр")
        except local.Unavailable:
            seen = ""
    if not seen:
        found = local.labels(path, purpose="файл в реестр")
        seen = ("на снимке: " + ", ".join(found)) if found else ""
    if seen:
        parts.append(seen)
    return " ".join(parts)


def read(entry):
    """Read what a photo or a PDF holds, once, and keep it: a new manifest line for the same file, the record updated.
    Returns the updated entry, or None when there is nothing to read or it couldn't be read."""
    path = _path(entry)
    if entry.get("kind") not in READ_KINDS or entry.get("content") is not None or not path.is_file() or path.stat().st_size > READ_LIMIT:
        return None
    try:
        content = " ".join(_content(path, entry["kind"]).split())[:CONTENT]
    except local.Unavailable as e:
        events.error(f"Чтение файла «{entry.get('name')}»", e)
        content = ""
    fresh = {**entry, "content": content, "read": moment.now()}   # an empty content is kept too: tried once, not retried every night
    attachments.write(fresh)
    index.put(KIND, entry["path"], _text(fresh), entry["t"], entry.get("tz", ""), _meta(fresh))
    return fresh


def read_pending(limit=200):
    """Photos and PDFs that were never read (from before reading existed, or the Mac was busy): read them now, oldest first."""
    done = 0
    for entry in sorted(manifest().values(), key=lambda e: e.get("t", 0)):
        if done >= limit:
            break
        if read(entry):
            done += 1
    return done


manifest = attachments.manifest   # one reader of files.jsonl: core/attachments.py


def sync_index():
    """Registry rows from the manifest; files on disk that have no record yet get one (dated by the file); tasks whose answer
    names a file are linked to it. Called at startup, at night and after the registry was set aside as broken."""
    known = manifest()
    picked = 0
    for folder, source in ((config.INBOX_DIR, "telegram"), (config.DOWNLOADS_DIR, "browser"), (config.GENERATED_DIR, "gemini")):
        for path in sorted(folder.iterdir()) if folder.is_dir() else []:
            if path.is_file() and not path.name.startswith((".", "tmp")) and str(path.relative_to(config.DATA)) not in known:
                entry = attachments.register(path, source=source, when=path.stat().st_mtime, attach_task=False)   # the hook writes the row
                if entry:
                    known[entry["path"]] = entry
                    picked += 1
    present = []
    for e in known.values():
        if not _path(e).is_file():
            continue
        if "excerpt" not in e and e.get("kind") in READABLE:   # read once, kept in the manifest for every later reconciliation
            e = {**e, "excerpt": _excerpt(e)}
            attachments.write(e)
        present.append(e)
    report = index.sync(KIND, [(e["path"], _text(e), e["t"], _meta(e)) for e in present])
    for e in present:
        _link_task(e)
    # a task whose answer names a file it made: only the results of tasks, and names too distinctive to be a coincidence —
    # an owner's attachment or a «photo.jpg» named in some answer isn't what that task made
    by_name = {Path(e["path"]).name: e["path"] for e in present if e.get("source") in MADE and len(Path(e["path"]).name) >= 12}
    for t in task_log.items:
        answer = t.get("answer") or ""
        for name, p in by_name.items():
            if name in answer:
                index.link(KIND, p, "задача", t["id"], "made_by", t["start"], keep=False)
    return {"added": report["added"] + picked, "removed": report["removed"]}
