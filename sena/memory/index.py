"""The registry of everything Sena can recall, one SQLite file: facts and their archive, people, projects, rules, skills,
tasks, messages, overheard lines, notes (in chunks), files. Every record has a kind (its nature), a moment with the zone it was
written in, small attributes (meta: author, tier, facet...), and links to other records (links). A changed record of a kind
with history is kept in versions with the span it was true for; nothing is overwritten silently.
A record is added when it appears (put), not rebuilt on every message. The embedding is computed once, in a batch — on the
first search or at night; the same text is never embedded twice. Search is hybrid: meaning (cosine over embeddings, a float32
matrix in memory) plus words (FTS5 with trigrams — catches Russian word endings), merged via reciprocal rank fusion, with a
freshness bonus, time and attribute filters, and one hop over links. KINDS is the single table of kinds and their policy:
what gets a vector, what may be mixed into a reply unasked, what keeps history, which subtypes exist.
Inspired by: Letta/MemGPT (core in the prompt, the rest on demand), Generative Agents (relevance + recency), Zep/Graphiti
(facts with a validity span), hybrid BM25 + vector search."""
import bisect
import collections
import hashlib
import itertools
import json
import math
import re
import shutil
import sqlite3
import statistics
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from sena import config
from sena.core import events, moment, store, vectors

TEXT_MAX = 2000      # we don't store or embed anything longer: the excess is trimmed (notes go in by chunks)
CANDIDATES = 50      # how many top results each of the two searches takes before merging
RRF_K = 60           # the reciprocal rank fusion constant (Cormack et al., 2009)
PENDING_BATCH = 300  # how many embeddings to compute at a time before a search
MEMORY_SECTIONS = ("Обо мне", "Люди", "Проекты", "Предпочтения", "Планы", "Словарь")   # facets of a fact = sections of memory.md

class Meta:
    """A policy that depends on one attribute of the record (author, tier), readable by Python and by SQL alike: the search
    applies the kinds' policy before it ranks, not after (LlamaIndex filters its vector store first, then takes the best)."""

    def __init__(self, key, value, equal=True):
        self.key, self.value, self.equal = key, value, equal

    def __call__(self, meta):
        return ((meta or {}).get(self.key) == self.value) == self.equal

    def sql(self, alias):   # a record without attributes has meta '': json_extract would refuse it as malformed
        return f"json_extract(NULLIF({alias}.meta, ''), '$.{self.key}') {'IS' if self.equal else 'IS NOT'} ?", [self.value]


class AllOf:
    """Several attribute policies that must all hold, readable by Python and SQL alike (as Meta)."""

    def __init__(self, *rules):
        self.rules = rules

    def __call__(self, meta):
        return all(rule(meta) for rule in self.rules)

    def sql(self, alias):
        parts = [rule.sql(alias) for rule in self.rules]
        return "(" + " AND ".join(f"({cond})" for cond, _ in parts) + ")", [p for _, params in parts for p in params]


def _not(rule):
    # the policy that holds where rule doesn't: a kind's "foreign" is the reverse of what it may mix in unasked
    return lambda meta: not rule(meta)


_owner_note = Meta("author", "sena", equal=False)   # notes Sena writes herself are copies of memory and people: words only, never recalled unasked
_archived_fact = Meta("tier", "archive")   # core facts are already in every prompt: only the archive is worth recalling unasked
_rule_on = Meta("enabled", False, equal=False)   # a rule the owner switched off is still found, but no longer recalled into replies
# a message or a task that holds someone else's words: a voice that may not have been his ("outside"), or a line that came
# fenced or was written by a turn that had read foreign text ("read": her reply retelling a mail, his message forwarding one)
_own_line = AllOf(Meta("outside", True, equal=False), Meta("read", True, equal=False))
_unread = Meta("read", True, equal=False)
_made_by_sena = Meta("source", "gemini")   # a picture or a video Sena made: its record holds her own prompt; any other file holds someone's text

# kind -> policy. vector: gets an embedding; auto: may be mixed into a reply without being asked (recall.relevant);
# find: appears in a general search without naming the kind; history: changes go to versions; foreign: holds someone
# else's words (a file's text, overheard speech, a reply or a task that retold a page) — the task that reads it in find waits
# for the owner's "yes" before its dangerous tools, as after reading a site. Nothing foreign is ever auto: someone else's
# text isn't put in front of the model unasked (that is where an injection rides), so recalling the archive never marks the
# turn — the owner's own «да» and his coding requests are never held by context he didn't ask for. facets: closed list of
# subtypes or None. A value may be a function of the record's meta.
KINDS = {
    "человек":     dict(vector=True, auto=True, find=True, history=True, foreign=False, facets=None),
    "проект":      dict(vector=True, auto=True, find=True, history=True, foreign=False, facets=None),
    "организация": dict(vector=True, auto=True, find=True, history=True, foreign=False, facets=None),
    "место":       dict(vector=True, auto=True, find=True, history=True, foreign=False, facets=None),
    "факт":        dict(vector=True, auto=_archived_fact, find=True, history=True, foreign=False, facets=MEMORY_SECTIONS),
    "правило":     dict(vector=True, auto=_rule_on, find=True, history=True, foreign=False, facets=None),
    "навык":       dict(vector=True, auto=True, find=True, history=True, foreign=False, facets=None),
    "урок":        dict(vector=True, auto=False, find=True, history=True, foreign=False, facets=None),   # an agent's own lesson (memory/lessons.py)
    "цель":        dict(vector=True, auto=True, find=True, history=True, foreign=False, facets=None),   # the owner's long goal (memory/goals.py)
    "сообщение":   dict(vector=True, auto=_own_line, find=True, history=False, foreign=_not(_own_line), facets=("telegram", "app", "voice")),
    "услышано":    dict(vector=True, auto=False, find=False, history=False, foreign=True, facets=None),
    "задача":      dict(vector=True, auto=_unread, find=True, history=False, foreign=_not(_unread), facets=("владелец", "расписание", "служебная")),
    "заметка":     dict(vector=_owner_note, auto=_owner_note, find=_owner_note, history=False, foreign=False, facets=None),
    "файл":        dict(vector=True, auto=_made_by_sena, find=True, history=False, foreign=_not(_made_by_sena), facets=("image", "pdf", "doc", "sheet", "text", "other")),
}
FENCE = "<<<ВНЕШНИЕ ДАННЫЕ "   # the header core/safety.external puts over someone else's text (inside it, "<<<" can't be forged)


def fenced(text):
    """True when a stored text carries someone else's words framed by safety.external (a forwarded message, a tool's result)."""
    return FENCE in str(text or "")


# how two records relate: the closed list keeps links meaningful (no one invents a relation nobody filters by)
RELS = ("about", "mentions", "said_by", "made_by", "attached_to", "part_of", "derived_from")

_db = None
_lock = threading.RLock()
_rows = None         # ids of records that have an embedding (in the matrix's order)
_matrix = None       # their embeddings, normalized, float32
_row_of = {}         # id -> its row in the matrix
_masks = {}          # scope ("find", "auto", "all") -> which rows of the matrix the kinds' policy lets into that scope ("all": the live ones)
_dead = 0            # rows whose record changed or left since the matrix was loaded: masked out of every scope
_written = 0         # writes so far (put): what the "nothing left to embed" mark is checked against
_settled = None      # (model, _written) when nothing was left to embed: a search skips the scan of the whole registry then
_recent_queries = {}   # (model, query text) -> embedding: the same question gets searched several times in a row (tasks, archive), so we compute it once
names = {}           # kind -> {name (lower): ref}: people and entities whose names are looked for in every text (mention links)
_forms = None        # {word form: [(kind, ref, capital)]} built from names on first use; rebuilt when names change
_width = 1           # the longest registered name, in words
_ranking_ms = collections.deque(maxlen=21)   # the last rankings by meaning (the matrix product and the top): what a vector index would replace
# Exact search beats an approximate index while the matrix fits: full recall, nothing to maintain. The one seam is _vector_hits();
# past these numbers it is time to open it (sqlite-vec, exact KNN in C with int8 vectors). The nightly report watches them.
ANN_VECTORS = 300_000
ANN_SEARCH_MS = 50.0
LEGACY_STAMP = f"{config.MODEL_EMBED}/{config.EMBED_DIMS}"   # the model of every vector made before vectors were stamped: the cloud one


def needs_ann():
    """True when the registry has outgrown brute-force vectors in memory (the nightly report says so, the code doesn't switch by
    itself): too many vectors, or a ranking that is slow as a rule over a registry big enough for that to be the matrix's fault.
    One search's whole time, the query's embedding included, made a tiny registry look slow."""
    s = stats()
    return s["vectors"] > ANN_VECTORS or (s["search_ms"] > ANN_SEARCH_MS and s["vectors"] > ANN_VECTORS // 10)


def policy(kind, meta=None):
    """The resolved policy of one record: SimpleNamespace(vector, auto, find, history, foreign)."""
    spec = KINDS.get(kind)
    if spec is None:
        raise ValueError(f"Неизвестный вид записи: {kind!r}; виды: {', '.join(KINDS)}")
    meta = meta or {}
    return SimpleNamespace(**{k: (v(meta) if callable(v) else v) for k, v in spec.items() if k != "facets"})


def _check(kind, meta):
    spec = KINDS.get(kind)
    if spec is None:
        raise ValueError(f"Неизвестный вид записи: {kind!r}; виды: {', '.join(KINDS)}")
    facet = (meta or {}).get("facet")
    if facet and spec["facets"] and facet not in spec["facets"]:
        raise ValueError(f"У вида {kind!r} нет подтипа {facet!r}; есть: {', '.join(spec['facets'])}")


def _corrupt(e):
    # only a damaged file is set aside: "database is locked" (another writer), a full disk or a folder that can't be opened are
    # DatabaseErrors too, and taking them for damage threw a sound registry and its versions away under the other writer's feet
    code = getattr(e, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xFF in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)
    return not isinstance(e, sqlite3.OperationalError)


def _move_aside():
    # the damaged registry is kept beside, never deleted; its WAL tail must not land on the new one
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for suffix in ("", "-wal", "-shm"):
        broken = config.INDEX_DB.with_name(config.INDEX_DB.name + suffix)
        if broken.exists():
            broken.replace(broken.with_name(f"index.broken-{stamp}.db{suffix}"))
    return f"index.broken-{stamp}.db"


def _connect():
    global _db
    if _db is None:
        try:
            _db = _open()
        except sqlite3.DatabaseError as e:
            if not _corrupt(e):
                raise
            # the file is damaged: set it aside, start fresh — the startup's reconciliation (recall.sync_all, which opens it
            # first) repopulates it from the sources
            events.error(f"Указатель памяти повреждён, отложен как {_move_aside()}", e)
            try:
                _db = _open()
            except sqlite3.DatabaseError as again:
                events.error("Указатель памяти не открылся и после сброса", again)
                raise
    return _db


def set_aside(why, good_copy=None):
    """A registry that opens but fails its integrity check (the night finds it) is put aside the same way, and replaced by the
    last good copy when there is one (its versions come back), else started empty. The caller reconciles it from the sources
    right after (recall.sync_all): the registry is derived data, the files are the truth."""
    with _lock:
        _reset()
        kept = _move_aside()
        if good_copy:
            shutil.copyfile(good_copy, config.INDEX_DB)
    events.error(f"Указатель памяти не прошёл проверку ({why[:200]}): отложен как {kept}" + (f", взята копия {Path(good_copy).name}" if good_copy else ""),
                 RuntimeError(why[:200]))
    return kept


RENAMED = {   # kinds of the first registry: author and tier became attributes, not kinds
    "Тема": ("сообщение", {"author": "Тема"}), "Сена": ("сообщение", {"author": "Сена"}),
    "память": ("факт", {"tier": "core"}), "архив памяти": ("факт", {"tier": "archive"}),
    "заметка-авто": ("заметка", {"author": "sena"}),
}


def _open():
    db = sqlite3.connect(config.INDEX_DB, timeout=30, check_same_thread=False)   # another writer waits its turn, as in the store
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")   # a power cut may lose the last fraction of a second, never corrupt the file
        ghosts = not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = 'versions_ad'").fetchone()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, ref TEXT NOT NULL, time REAL NOT NULL,
                text TEXT NOT NULL, h TEXT NOT NULL, vec BLOB, tz TEXT NOT NULL DEFAULT '', meta TEXT NOT NULL DEFAULT '',
                vmodel TEXT NOT NULL DEFAULT '', UNIQUE(kind, ref));
            CREATE INDEX IF NOT EXISTS items_kind ON items(kind);
            CREATE INDEX IF NOT EXISTS items_h ON items(h);
            CREATE INDEX IF NOT EXISTS items_time ON items(time);
            CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(text, content='items', content_rowid='id', tokenize='trigram');
            CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
                INSERT INTO items_fts(rowid, text) VALUES (new.id, new.text); END;
            CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
                INSERT INTO items_fts(items_fts, rowid, text) VALUES ('delete', old.id, old.text); END;
            CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE OF text ON items BEGIN
                INSERT INTO items_fts(items_fts, rowid, text) VALUES ('delete', old.id, old.text);
                INSERT INTO items_fts(rowid, text) VALUES (new.id, new.text); END;
            CREATE TABLE IF NOT EXISTS versions (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, ref TEXT NOT NULL, text TEXT NOT NULL, time REAL NOT NULL,
                tz TEXT NOT NULL DEFAULT '', meta TEXT NOT NULL DEFAULT '', valid_from REAL NOT NULL, valid_to REAL NOT NULL, why TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS versions_ref ON versions(kind, ref);
            CREATE VIRTUAL TABLE IF NOT EXISTS versions_fts USING fts5(text, content='versions', content_rowid='id', tokenize='trigram');
            CREATE TRIGGER IF NOT EXISTS versions_ai AFTER INSERT ON versions BEGIN
                INSERT INTO versions_fts(rowid, text) VALUES (new.id, new.text); END;
            CREATE TRIGGER IF NOT EXISTS versions_ad AFTER DELETE ON versions BEGIN
                INSERT INTO versions_fts(versions_fts, rowid, text) VALUES ('delete', old.id, old.text); END;
            CREATE TABLE IF NOT EXISTS links (
                src_kind TEXT NOT NULL, src_ref TEXT NOT NULL, dst_kind TEXT NOT NULL, dst_ref TEXT NOT NULL, rel TEXT NOT NULL,
                time REAL NOT NULL, tz TEXT NOT NULL DEFAULT '', valid_to REAL,
                PRIMARY KEY (src_kind, src_ref, dst_kind, dst_ref, rel));
            CREATE INDEX IF NOT EXISTS links_dst ON links(dst_kind, dst_ref);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(items)")}
        for name in ("tz", "meta", "vmodel"):   # the first registry had none of these
            if name not in columns:
                db.execute(f"ALTER TABLE items ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        if "vmodel" not in columns:   # vectors from before the model was recorded were all made in the cloud (OpenAI) — stamped so, never as home ones
            db.execute("UPDATE items SET vmodel = ? WHERE vec IS NOT NULL", (LEGACY_STAMP,))
        # what is left to embed is counted by the model stamp alone (a record without a vector has '' there): an index, not a
        # scan of the whole registry on every overview of the apps and before the first search after any write
        db.execute("CREATE INDEX IF NOT EXISTS items_vmodel ON items(vmodel)")
        if ghosts:   # versions removed before they left their word index too: forgotten words found later versions by their rowids
            db.execute("INSERT INTO versions_fts(versions_fts) VALUES ('rebuild')")
        for old, (kind, meta) in RENAMED.items():   # only where something is left to rename: a plain open writes nothing
            if db.execute("SELECT 1 FROM items WHERE kind = ? LIMIT 1", (old,)).fetchone():
                db.execute("UPDATE items SET kind = ?, meta = ? WHERE kind = ?", (kind, _meta_text(meta), old))
        legacy = db.execute("SELECT id, vec FROM items WHERE vec IS NOT NULL AND length(vec) = ?", (config.EMBED_DIMS * 4,)).fetchall()
        if legacy:   # float32 blobs of the first registry: half the disk as float16, same cosine
            db.executemany("UPDATE items SET vec = ? WHERE id = ?", [(_pack(np.frombuffer(v, dtype=np.float32)), i) for i, v in legacy])
        db.commit()
    except sqlite3.DatabaseError:
        db.close()
        raise
    old = config.STATE_DIR / "embeddings.db"   # the previous embedding cache: the index will recompute them cheaply
    if old.exists():
        old.unlink()
        events.log("cleanup", str(old))
    return db


def checkpoint():
    """Before shutdown: flush the WAL tail into the main file so a copy of the database is complete."""
    with _lock:
        if _db is not None:
            _db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _reset():
    # for tests: close the database and forget the matrix
    global _db, _forms, _settled
    with _lock:
        if _db is not None:
            _db.close()
        _db = None
        _forget_matrix()
        _recent_queries.clear()
        _ranking_ms.clear()
        names.clear()
        _forms, _settled = None, None


def _clean(text):
    return " ".join(str(text).split())[:TEXT_MAX]


def _hash(text):
    return hashlib.md5(text.encode()).hexdigest()


def _meta_text(meta):
    return json.dumps({k: v for k, v in (meta or {}).items() if v not in (None, "", [])}, ensure_ascii=False, sort_keys=True) if meta else ""


def _meta_of(text):
    try:
        return json.loads(text) if text else {}
    except ValueError:
        return {}


def _current():
    """The embedding model and width every vector is stamped with: a vector from another model never meets a query from this one."""
    return vectors.stamp()


def _pack(vec):
    return np.asarray(vec, dtype=np.float16).tobytes()


def _unpack(blob):
    return np.frombuffer(blob, dtype=np.float16).astype(np.float32)


def _forget_matrix():
    global _rows, _matrix, _row_of, _masks, _dead
    _rows = _matrix = None
    _row_of, _masks, _dead = {}, {}, 0


def _scopes(kind, meta_text):
    # (find, auto) of one record, for the masks the vector search filters by
    p = policy(kind, _meta_of(meta_text))
    return bool(p.find), bool(p.auto)


def _retire(ids):
    # vectors that no longer hold (the record's text changed, it left): their rows stay in the matrix, masked out of every
    # scope — reloading the whole matrix for one changed record cost 0.4 s at 100k records, and a verdict or a remark changed
    # one on most turns; once a fifth of the rows are dead, the next search loads it afresh
    global _dead
    for i in ids:
        n = _row_of.pop(i, None)
        if n is not None:
            for m in _masks.values():
                m[n] = False
            _dead += 1
    if _rows is not None and _dead > max(100, len(_rows) // 5):
        _forget_matrix()


def _rescope(item_id, kind, meta_text):
    # the record's attributes moved it between scopes (a fact into the archive, a rule switched off): its masks follow in place
    n = _row_of.get(item_id)
    if n is not None:
        _masks["find"][n], _masks["auto"][n] = _scopes(kind, meta_text)


# ---------- writing ----------
def put(kind, ref, text, when=None, tz="", meta=None, known=None, exact=False):
    """Add or update a record of kind kind with reference ref (task id, note path, line hash). meta — small attributes
    (author, tier, facet, pid...). The embedding isn't computed here: a search or the nightly reconciliation will fill it in later.
    A changed record of a kind with history keeps its previous text in versions. known — {text hash: embedding} taken before a
    batch of writes (vectors_of): a note whose paragraphs moved keeps their embeddings, though their rows are overwritten in turn.
    exact — the time is a marker compared as is (a note's file time): kept exactly, not within the usual second."""
    _check(kind, meta)
    text = _clean(text)
    if not text:
        return drop(kind, ref)
    ref, h, when = str(ref), _hash(text), when or moment.now()
    tz, meta_text = tz or moment.offset(when), _meta_text(meta)
    global _written
    with _lock:
        db = _connect()
        row = db.execute("SELECT id, h, time, text, tz, meta FROM items WHERE kind = ? AND ref = ?", (kind, ref)).fetchone()
        if row and row[1] == h:
            if abs(row[2] - when) > (0 if exact else 1) or row[5] != meta_text:   # same text, new timestamp or attributes: the embedding stays
                db.execute("UPDATE items SET time = ?, tz = ?, meta = ? WHERE id = ?", (when, tz, meta_text, row[0]))
                db.commit()
                _written += 1   # new attributes may give it a vector it had no right to (a note Sena's no longer)
                if _scopes(kind, row[5]) != _scopes(kind, meta_text):   # a fact moved to the archive: the search masks follow
                    _rescope(row[0], kind, meta_text)
            return row[0]
        same = db.execute("SELECT vec FROM items WHERE h = ? AND vec IS NOT NULL AND vmodel = ? LIMIT 1", (h, _current())).fetchone()   # the same text was already embedded by this model
        if same is None and known and h in known:
            same = (known[h],)
        vec, vmodel = (same[0], _current()) if same else (None, "")
        if row:
            if policy(kind, _meta_of(row[5])).history:
                _remember_version(db, kind, ref, row[3], row[2], row[4], row[5], when, "changed")
            db.execute("UPDATE items SET time = ?, text = ?, h = ?, vec = ?, vmodel = ?, tz = ?, meta = ? WHERE id = ?", (when, text, h, vec, vmodel, tz, meta_text, row[0]))
            item_id = row[0]
            _retire([item_id])   # its old embedding no longer holds
        else:
            item_id = db.execute("INSERT INTO items(kind, ref, time, text, h, vec, vmodel, tz, meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                 (kind, ref, when, text, h, vec, vmodel, tz, meta_text)).lastrowid
        db.commit()
        _written += 1
        if vec is not None:   # an embedding of the same text, borrowed: in the matrix at once
            _append_matrix([item_id])
        _link_mentions(db, kind, ref, text, when, tz, renew=bool(row))
        return item_id


def try_put(kind, ref, text, when=None, tz="", meta=None):
    """put on the owner's path (his message, Sena's answer, a task, a skill as it is written): the registry is derived from
    the records, so a write it can't take now (locked, broken, a full disk) is told in the journal and left to the
    reconciliation (recall.sync_all) — his turn goes on. Returns the record's id, or None."""
    try:
        return put(kind, ref, text, when, tz, meta)
    except (sqlite3.Error, OSError) as e:
        events.error(f"Указатель памяти: запись «{kind}» подождёт сверки", e)
        return None


def _remember_version(db, kind, ref, text, when, tz, meta_text, until, why):
    db.execute("INSERT INTO versions(kind, ref, text, time, tz, meta, valid_from, valid_to, why) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (kind, ref, text, when, tz, meta_text, when, until, why))


def drop(kind, ref=None, prefix=None):
    """Remove records of kind kind: one (ref), by reference prefix (prefix), or all of them.
    A kind with history keeps the removed text in versions; links of the removed records are closed, not deleted — though
    mention links are computed afresh by relink_mentions, and a kept link opens again with rebuild_links (links.jsonl)."""
    _check(kind, None)
    with _lock:
        db = _connect()
        if ref is not None:
            where, params = "kind = ? AND ref = ?", (kind, str(ref))
        elif prefix is not None:
            where, params = "kind = ? AND substr(ref, 1, ?) = ?", (kind, len(prefix), prefix)
        else:
            where, params = "kind = ?", (kind,)
        gone = db.execute(f"SELECT id, ref, text, time, tz, meta FROM items WHERE {where}", params).fetchall()
        now = moment.now()
        for _, r, text, when, tz, meta_text in gone:
            if policy(kind, _meta_of(meta_text)).history:
                _remember_version(db, kind, r, text, when, tz, meta_text, now, "removed")
            db.execute("UPDATE links SET valid_to = ? WHERE valid_to IS NULL AND ((src_kind = ? AND src_ref = ?) OR (dst_kind = ? AND dst_ref = ?))",
                       (now, kind, r, kind, r))
        n = db.execute(f"DELETE FROM items WHERE {where}", params).rowcount
        db.commit()
        _retire([g[0] for g in gone])
        return n


def purge(kind, refs):
    """Remove records for good — no copy kept in versions, links deleted: the owner asked to forget them (overheard speech
    and what was learned from it). Every other removal keeps the past (drop)."""
    refs = [str(r) for r in refs]
    if not refs:
        return 0
    _check(kind, None)
    with _lock:
        db = _connect()
        n, ids = 0, []
        before = db.execute("PRAGMA secure_delete").fetchone()[0]
        db.execute("PRAGMA secure_delete = 1")   # the forgotten text is overwritten on disk, not left in free pages
        try:
            for i in range(0, len(refs), 500):
                part = refs[i:i + 500]
                marks = ",".join("?" * len(part))
                ids += [r for (r,) in db.execute(f"SELECT id FROM items WHERE kind = ? AND ref IN ({marks})", (kind, *part))]
                n += db.execute(f"DELETE FROM items WHERE kind = ? AND ref IN ({marks})", (kind, *part)).rowcount
                db.execute(f"DELETE FROM versions WHERE kind = ? AND ref IN ({marks})", (kind, *part))
                db.execute(f"DELETE FROM links WHERE (src_kind = ? AND src_ref IN ({marks})) OR (dst_kind = ? AND dst_ref IN ({marks}))",
                           (kind, *part, kind, *part))
            db.commit()
        finally:
            db.execute(f"PRAGMA secure_delete = {int(before)}")
        _retire(ids)
        return n


def vectors_of(kind, prefix):
    """{text hash: embedding} of the records of kind whose ref starts with prefix, by the current model — to keep before rewriting them."""
    with _lock:
        rows = _connect().execute("SELECT h, vec FROM items WHERE kind = ? AND ref LIKE ? ESCAPE '\\' AND vec IS NOT NULL AND vmodel = ?",
                                  (kind, prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", _current())).fetchall()
    return dict(rows)


def refs(kind):
    """References of kind kind and their timestamps: {ref: time}."""
    with _lock:
        return dict(_connect().execute("SELECT ref, time FROM items WHERE kind = ?", (kind,)).fetchall())


def sync(kind, rows):
    """Bring the records of kind kind in line with rows = [(ref, text, time) or (ref, text, time, meta)]: remove extras,
    write new and changed ones. For lines without their own id (memory, facts about people), ref is the text hash (line_ref)."""
    rows = [(str(r[0]), _clean(r[1]), r[2], r[3] if len(r) > 3 else None) for r in rows if _clean(r[1])]
    wanted = {ref for ref, _, _, _ in rows}
    with _lock:
        db = _connect()
        have = {ref: (h, meta_text) for ref, h, meta_text in db.execute("SELECT ref, h, meta FROM items WHERE kind = ?", (kind,)).fetchall()}
        gone = [ref for ref in have if ref not in wanted]
        for ref in gone:
            drop(kind, ref)
        changed = 0
        for ref, text, when, meta in rows:
            if have.get(ref) != (_hash(text), _meta_text(meta)):
                put(kind, ref, text, when, meta=meta)
                changed += 1
        db.commit()
    return {"added": changed, "removed": len(gone)}


SEPARATORS = (r"\n", r"(?<=[.!?…])\s+", r" ")   # a text too long for one record is cut by line, then sentence, then space


def pieces(text, size, level=0):
    """A long text in pieces of at most size characters (notes by paragraph, long messages): Haystack's recursive splitter —
    a long list no longer loses everything past the registry's limit, item 55 is found as item 5 is; a piece still too long
    goes down to the next separator, the last resort is a plain cut."""
    if len(text) <= size:
        return [text]
    if level == len(SEPARATORS):
        return [text[i:i + size] for i in range(0, len(text), size)]
    joiner, parts, cur = "\n" if level == 0 else " ", [], ""
    for bit in re.split(SEPARATORS[level], text):
        bit = bit.strip()
        if not bit:
            continue
        if cur and len(cur) + len(bit) + 1 > size:
            parts.append(cur)
            cur = ""
        cur = f"{cur}{joiner}{bit}" if cur else bit
    if cur:
        parts.append(cur)
    return [q for p in parts for q in pieces(p, size, level + 1)]


def line_ref(text):
    """Reference for a line without an id: a hash of its text (memory, archive, facts about people)."""
    return _hash(_clean(text))


# ---------- links ----------
def link(src_kind, src_ref, dst_kind, dst_ref, rel, when=None, tz="", keep=True):
    """Relate two records. keep — write the link to links.jsonl too (anything that can't be recomputed from the sources:
    the owner's own tags); mention links are derived from text and live only in the table. Returns True for a new link."""
    if rel not in RELS:
        raise ValueError(f"Неизвестная связь: {rel!r}; есть: {', '.join(RELS)}")
    _check(src_kind, None)
    _check(dst_kind, None)
    when = when or moment.now()
    tz = tz or moment.offset(when)
    with _lock:
        db = _connect()
        cur = db.execute("INSERT OR IGNORE INTO links(src_kind, src_ref, dst_kind, dst_ref, rel, time, tz) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (src_kind, str(src_ref), dst_kind, str(dst_ref), rel, when, tz))
        if not cur.rowcount:   # already there: a closed link opens again
            db.execute("UPDATE links SET valid_to = NULL WHERE src_kind = ? AND src_ref = ? AND dst_kind = ? AND dst_ref = ? AND rel = ? AND valid_to IS NOT NULL",
                       (src_kind, str(src_ref), dst_kind, str(dst_ref), rel))
        db.commit()
        new = bool(cur.rowcount)
    # kept even when a text had made the same link already: the owner's word holds after the text no longer says it
    if keep and (new or (src_kind, str(src_ref), dst_kind, str(dst_ref), rel) not in kept_links()):
        store.append_jsonl(config.LINKS_FILE, {"t": when, "tz": tz, "src": [src_kind, str(src_ref)], "dst": [dst_kind, str(dst_ref)], "rel": rel})
    return new


def unlink(src_kind, src_ref, dst_kind, dst_ref, rel):
    """A link no longer holds: it stays with the moment it was closed."""
    with _lock:
        db = _connect()
        n = db.execute("UPDATE links SET valid_to = ? WHERE src_kind = ? AND src_ref = ? AND dst_kind = ? AND dst_ref = ? AND rel = ? AND valid_to IS NULL",
                       (moment.now(), src_kind, str(src_ref), dst_kind, str(dst_ref), rel)).rowcount
        db.commit()
        return n


def links_of(kind, ref, rel=None, closed=False):
    """Links touching a record, both directions: [{"kind", "ref", "rel", "out" (True: this record is the source), "time", "tz"}]."""
    sql = ("SELECT src_kind, src_ref, dst_kind, dst_ref, rel, time, tz, valid_to FROM links "
           "WHERE ((src_kind = ? AND src_ref = ?) OR (dst_kind = ? AND dst_ref = ?))")
    params = [kind, str(ref), kind, str(ref)]
    if rel:
        sql += " AND rel = ?"
        params.append(rel)
    if not closed:
        sql += " AND valid_to IS NULL"
    with _lock:
        rows = _connect().execute(sql + " ORDER BY time", params).fetchall()
    out = []
    for sk, sr, dk, dr, r, when, tz, valid_to in rows:
        mine = (sk, sr) == (kind, str(ref))
        out.append({"kind": dk if mine else sk, "ref": dr if mine else sr, "rel": r, "out": mine, "time": when, "tz": tz, "valid_to": valid_to})
    return out


def anchors(kind, ref):
    """Where a record's links hang: the record itself, and for a chunk of a note also the note's first chunk (the note's links live there)."""
    ref = str(ref)
    out = [ref]
    if kind == "заметка" and "#" in ref and not ref.endswith("#0"):
        out.append(ref.rsplit("#", 1)[0] + "#0")
    return out


def related(kind, ref, limit=8):
    """Records one hop away, as search rows (text included), nearest in time first."""
    with _lock:
        db = _connect()
        out, seen = [], set()
        for anchor in anchors(kind, ref):
            for l in links_of(kind, anchor):
                if (l["kind"], l["ref"]) in seen:
                    continue
                seen.add((l["kind"], l["ref"]))
                row = db.execute("SELECT kind, ref, time, tz, text, meta FROM items WHERE kind = ? AND ref = ?", (l["kind"], l["ref"])).fetchone()
                if row:
                    out.append({"kind": row[0], "ref": row[1], "time": row[2], "tz": row[3], "text": row[4], "meta": _meta_of(row[5]), "rel": l["rel"], "out": l["out"]})
    out.sort(key=lambda r: -r["time"])
    return out[:limit]


def expand(rows, per_row=4):
    """Attach to search rows what they are linked to: row["links"] = [{"kind", "ref", "rel", "text", "meta"}] — the
    attributes too, so a reader can tell a linked record that holds someone else's words (KINDS "foreign")."""
    for r in rows:
        r["links"] = [{"kind": x["kind"], "ref": x["ref"], "rel": x["rel"], "text": x["text"][:200], "meta": x["meta"]}
                      for x in related(r["kind"], r["ref"], per_row)]
    return rows


def beside(kind, ref, when, before):
    """The record of the same kind and conversation ("<chat>:<id>") right before or after a moment: the question a reply answered,
    the reply a question got — a hit is read with its neighbour (LlamaIndex's previous/next postprocessor). Archived messages
    keep their records, so this works for them too."""
    chat = str(ref).split(":", 1)[0] + ":"
    with _lock:   # of a long message in pieces, its first ("<chat>:<id>", the shortest ref)
        row = _connect().execute(f"SELECT text, meta FROM items WHERE kind = ? AND substr(ref, 1, ?) = ? AND time {'<' if before else '>'} ? "
                                 f"ORDER BY time {'DESC' if before else 'ASC'}, length(ref) LIMIT 1", (kind, len(chat), chat, float(when))).fetchone()
    return {"text": row[0], "meta": _meta_of(row[1])} if row else None


def rebuild_links():
    """The links table from links.jsonl (after the registry was set aside as broken); mention links come back with relink_mentions()."""
    n = 0
    for e in _read_jsonl(config.LINKS_FILE):
        try:
            if link(e["src"][0], e["src"][1], e["dst"][0], e["dst"][1], e["rel"], e.get("t"), e.get("tz", ""), keep=False):
                n += 1
        except (KeyError, ValueError, TypeError):
            continue
    return n


def kept_links():
    """Every link kept in links.jsonl — what no text can say again (the owner's tags, what a task derived): a set of
    (src_kind, src_ref, dst_kind, dst_ref, rel), for a source that recomputes its own links and must leave these alone."""
    out = set()
    for e in _read_jsonl(config.LINKS_FILE):
        try:
            out.add((*map(str, e["src"]), *map(str, e["dst"]), e["rel"]))
        except (KeyError, TypeError):
            continue
    return out


def repoint(kind, old, new):
    """Two records turned out to be one (two cards of one person): the kept links of the old one — the owner's tags, what a
    task derived — hold for the new one too, in links.jsonl as well. Mention and said_by links are recomputed by their owners."""
    n, old, new = 0, str(old), str(new)
    for e in _read_jsonl(config.LINKS_FILE):
        try:
            ends = [tuple(e["src"]), tuple(e["dst"])]
            if (kind, old) not in ends:
                continue
            (sk, sr), (dk, dr) = [(kind, new) if end == (kind, old) else end for end in ends]
            if (sk, sr) != (dk, dr) and link(sk, sr, dk, dr, e["rel"], e.get("t"), e.get("tz", "")):
                n += 1
        except (KeyError, ValueError, TypeError):
            continue
    return n


def _read_jsonl(path):
    return store.lines(path)


# ---------- names and mentions ----------
# A name is found in a text only as one of its case forms ("Игорь", "Игорю", "Игорем"), never as the start of another word
# ("Игорный", "левый", "лента" for Лена). Names that are also ordinary words count only written with a capital letter: "Лев звонил",
# not "лев в зоопарке"; the project "Сайт", not every "сайт". Explicit [[links]], frontmatter and the tag tool don't pass through this.
WORD_NAMES = {"лев", "вера", "надежда", "любовь", "роза", "слава", "мира", "света", "воля", "майя", "марта", "соня", "поля", "тома", "гена",
              "костя", "марк", "роман", "женя", "тема", "сена", "сайт", "проект", "дом", "банк", "почта", "сервер", "работа", "офис", "город",
              "страна", "школа", "клиент", "компания", "магазин", "мир", "дело", "план", "отчет", "встреча", "задача", "память", "письмо"}
IRREGULAR = {"лев": "льв", "павел": "павл", "петр": "петр"}   # a vowel that drops out when the name declines: Лев -> Льва, Павел -> Павлу
MENTION_MIN = 3   # a shorter name ("Ян") is known when asked by name, but never looked for in texts: it would match stray syllables
_WORD = re.compile(r"[^\W_][\w-]*")


def _norm(word):
    # one spelling for matching: lower case, е for ё (people type "Артем" for "Артём"), no stray hyphens
    return word.lower().replace("ё", "е").strip("-_")


def _word_forms(w):
    # the case forms one word of a name may take in a Russian text (over-generation is harmless: "Ивану" exists, "Иваными" matches nothing)
    out = {w}
    if "-" in w:
        head, _, tail = w.rpartition("-")   # Нью-Йорк -> Нью-Йорка
        return {head + "-" + t for t in _word_forms(tail)}
    if len(w) < 3 or not re.fullmatch(r"[а-я]+", w):
        return out   # Latin names, codes, initials: as written
    if w in IRREGULAR:
        return out | {IRREGULAR[w] + e for e in ("а", "у", "ом", "е")}
    base, last = w[:-1], w[-1]
    if last == "а":
        out |= {base + e for e in ("ы", "и", "е", "у", "ой", "ей", "ою")}      # Ольга, Анна, Москва
    elif last == "я":
        out |= {base + e for e in ("и", "е", "ю", "ей", "ею")}                 # Катя, Илья, Мария
    elif last == "ь":
        out |= {base + e for e in ("я", "ю", "ем", "е", "и", "ью")}            # Игорь, Любовь
    elif last == "й":
        out |= {base + e for e in ("я", "ю", "ем", "е", "и")}                  # Сергей, Дмитрий
    elif last not in "оеиуыэю":                                                # Иван, Сбербанк, Петров (Отто and Порту don't decline)
        out |= {w + e for e in ("а", "у", "ом", "ем", "е", "ым")}
    return out


def _forms_of(name):
    # every spelling of a whole name: the forms of each word, combined ("Анна Петрова" -> "анной петровой")
    words = [_norm(m.group()) for m in _WORD.finditer(name)]
    return {" ".join(c) for c in itertools.product(*(_word_forms(w) for w in words))} if words else set()


def _strict(kind, name):
    # must the name be capitalised in the text to count? A word that is also a name, and a short plain title ("Дом", "Банк"): yes;
    # people's names and distinctive titles (five letters and up, Latin letters, digits, several words): in any spelling
    name = " ".join(_norm(m.group()) for m in _WORD.finditer(name))
    if " " in name:
        return False
    if name in WORD_NAMES:
        return True
    return kind != "человек" and len(name) < 5 and not re.search(r"[a-z0-9]", name)


def _table():
    global _forms, _width
    if _forms is None:
        _forms, _width = {}, 1
        for kind, mapping in names.items():
            for name, ref in mapping.items():
                if len(name) < MENTION_MIN:
                    continue
                strict = _strict(kind, name)
                for form in _forms_of(name):
                    _forms.setdefault(form, []).append((kind, ref, strict))
                _width = max(_width, name.count(" ") + 1)
    return _forms, _width


def register_names(kind, mapping):
    """Names to look for in every text: {name: ref} for people, projects, organizations, places. Replaces the kind's set;
    records that already mention a newly registered name — or a name that now names another record (two cards merged) — get
    their link right away (found by words, no full pass)."""
    global _forms
    with _lock:
        fresh = {str(n).strip().lower(): str(r) for n, r in mapping.items() if n and str(n).strip()}
        new = [n for n, r in fresh.items() if names.get(kind, {}).get(n) != r and len(n) >= MENTION_MIN]
        names[kind] = fresh
        _forms = None
        if new:
            db = _connect()
            seen = set()
            for n in new:
                first = n.split()[0] if n.split() else n
                for form in sorted(_word_forms(_norm(first)), key=len):   # records containing any form of the first word; mentions() then decides
                    try:
                        rows = db.execute("SELECT i.id, i.kind, i.ref, i.text, i.time, i.tz FROM items_fts f JOIN items i ON i.id = f.rowid "
                                          "WHERE items_fts MATCH ? LIMIT 500", (" OR ".join(f'"{s}"' for s in _spellings(form)),)).fetchall()
                    except sqlite3.OperationalError:
                        continue
                    for item_id, k, ref, text, when, tz in rows:
                        if item_id not in seen:
                            seen.add(item_id)
                            _link_mentions(db, k, ref, text, when, tz)


def matches(kind, name, whole=False):
    """Every record of kind kind a name may mean, in any case form ("Ольге", "кс клэй"): [ref], the closest tier only — the
    whole name ("Ольга Петрова", "Ольге Петровой") before a first name alone ("Ольга" for the cards «Ольга Петрова» and
    «Ольга Иванова»: both). whole — the whole name only (is there a card of exactly this name). Empty when unknown."""
    wanted = " ".join(_norm(m.group()) for m in _WORD.finditer(str(name or "")))
    if not wanted:
        return []
    with _lock:
        table, _ = _table()
        exact, partial = [], []
        for k, ref, _ in table.get(wanted, ()):
            if k == kind:
                exact.append(ref)
        for k, ref, _ in table.get(wanted.split()[0], ()) if " " in wanted else ():   # "Серкан Коратес" when only "Серкан" is on the card
            if k == kind:
                partial.append(ref)
        for n, ref in names.get(kind, {}).items():
            words = [_norm(m.group()) for m in _WORD.finditer(n)]
            if " ".join(words) == wanted:   # a name too short to look for in texts is still known by itself
                exact.append(ref)
            elif " " not in wanted and len(wanted) >= MENTION_MIN and len(words) > 1 and wanted in _word_forms(words[0]):
                partial.append(ref)   # "Серкану" for the card "Серкан Коратес"
    return list(dict.fromkeys(exact or ([] if whole else partial)))


def lookup(kind, name):
    """The one record of kind kind a name means -> ref; None when it is unknown or names several (two Olgas: an action taken
    on "the first" forgot, found or tagged the wrong one — matches() lists them for the question «которую?»)."""
    found = matches(kind, name)
    return found[0] if len(found) == 1 else None


def mentions(text):
    """Who and what the text mentions, by name in any of its case forms: [(kind, ref)]."""
    with _lock:
        table, width = _table()
        if not table:
            return []
        words = [(m.group(), _norm(m.group())) for m in _WORD.finditer(text)]
        found = {}
        for i, (raw, _) in enumerate(words):
            for n in range(min(width, len(words) - i), 0, -1):
                for kind, ref, strict in table.get(" ".join(w for _, w in words[i:i + n]), ()):
                    if not strict or raw[:1].isupper():
                        found[(kind, ref)] = True
        return list(found)


def _link_mentions(db, kind, ref, text, when, tz, renew=False):
    # a record's mention links from its text: one closed when the record left opens again when it comes back. renew — the
    # text changed: the names it no longer has stop being its links
    ref = str(ref)
    found = {(mk, mr) for mk, mr in mentions(text) if (mk, mr) != (kind, ref)}
    for mk, mr in found:
        db.execute("INSERT INTO links(src_kind, src_ref, dst_kind, dst_ref, rel, time, tz) VALUES (?, ?, ?, ?, 'mentions', ?, ?) "
                   "ON CONFLICT(src_kind, src_ref, dst_kind, dst_ref, rel) DO UPDATE SET valid_to = NULL", (kind, ref, mk, mr, when, tz))
    if renew:
        for dk, dr in db.execute("SELECT dst_kind, dst_ref FROM links WHERE src_kind = ? AND src_ref = ? AND rel = 'mentions' AND valid_to IS NULL",
                                 (kind, ref)).fetchall():
            if (dk, dr) not in found:
                db.execute("UPDATE links SET valid_to = ? WHERE src_kind = ? AND src_ref = ? AND dst_kind = ? AND dst_ref = ? AND rel = 'mentions'",
                           (moment.now(), kind, ref, dk, dr))
    db.commit()


RELINK_CHUNK = 1000   # records relinked under one hold of the lock: a message written meanwhile waits for a chunk, not for the registry
RELINK_DAYS = 7       # with the same names every mention is still recomputed this often: a rule of matching may have changed


def _names_digest():
    # what mention links are computed from besides the texts: the names and the rules for them
    return hashlib.md5(json.dumps([sorted((k, sorted(v.items())) for k, v in names.items()), MENTION_MIN, sorted(WORD_NAMES),
                                   sorted(IRREGULAR.items())], ensure_ascii=False).encode()).hexdigest()


def relink_mentions(force=False):
    """Mention links for every record, computed afresh — when the names (or the rules for them) changed since the last pass, the
    table was rebuilt, or RELINK_DAYS went by: they are derived from the texts and the names, so the old ones go first — a name
    that was renamed or a match the rules no longer make. With the same names a pass made the same links again: every start and
    every night held the registry's lock for seconds on a big registry, and the owner's first message waited behind it. Done in
    chunks, the lock let go between them (put and register_names keep the links of what changes meanwhile). Returns how many
    records were relinked: 0 — nothing had changed."""
    digest = _names_digest()
    with _lock:
        db = _connect()
        last = dict(db.execute("SELECT key, value FROM meta WHERE key IN ('mentions', 'mentions_at')").fetchall())
        if not force and last.get("mentions") == digest and time.time() - float(last.get("mentions_at") or 0) < RELINK_DAYS * 86400:
            return 0
        ids = [i for (i,) in db.execute("SELECT id FROM items ORDER BY id")]
    done = 0
    for n in range(0, len(ids), RELINK_CHUNK):
        part = ids[n:n + RELINK_CHUNK]
        with _lock:
            db = _connect()
            for kind, ref, text, when, tz in db.execute(f"SELECT kind, ref, text, time, tz FROM items WHERE id IN ({','.join('?' * len(part))})", part).fetchall():
                db.execute("DELETE FROM links WHERE rel = 'mentions' AND src_kind = ? AND src_ref = ?", (kind, ref))
                _link_mentions(db, kind, ref, text, when, tz)
                done += 1
    with _lock:
        db = _connect()   # mentions of records that are gone, closed or not, go too: the pass made them anew from what is there
        db.execute("DELETE FROM links WHERE rel = 'mentions' AND NOT EXISTS (SELECT 1 FROM items i WHERE i.kind = links.src_kind AND i.ref = links.src_ref)")
        db.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", [("mentions", digest), ("mentions_at", str(time.time()))])
        db.commit()
    return done


# ---------- embeddings ----------
def _pending_sql(model):
    # records to embed with this model — without a vector, or with one from another model (recomputed alongside, never thrown
    # away first) — under the kinds' vector policy, chosen in SQL: Sena's own notes never get a vector, and a LIMIT taken before
    # the policy held only them once 600 came first, so nothing new was embedded again. A record without a vector has the
    # stamp '' (put, embed_pending and _load_matrix write the two together), so "another stamp than this model's" says both,
    # as two ranges of the stamp's index
    cond, params = _policy_sql("vector")
    return " AND ".join(["(i.vmodel < ? OR i.vmodel > ?)", *cond]), [model, model, *params]


def embed_pending(limit=None, query=None):
    """Compute embeddings for records that lack one, and for the query (a question is fingerprinted as a question).
    Returns (how many were computed, the query's embedding or None). Once nothing is left for this model, the scan of the
    whole registry is skipped until the next write (it ran before every search: 30 ms at 100k records, four searches a message)."""
    global _settled
    model = _current()
    where, params = _pending_sql(model)
    with _lock:
        mark = None if _settled == (model, _written) else (model, _written)
        rows = _connect().execute(f"SELECT id, text, h FROM items i WHERE {where} ORDER BY id" + (f" LIMIT {int(limit)}" if limit else ""), params).fetchall() if mark else []
    vecs = vectors.embed([text for _, text, _ in rows]) if rows else []   # the model is called outside the lock
    with _lock:
        if rows:
            db = _connect()
            # only write if the record's text hasn't changed while the request was in flight (otherwise a stale embedding would stick to new text)
            db.executemany("UPDATE items SET vec = ?, vmodel = ? WHERE id = ? AND h = ?", [(_pack(v), model, i, h) for (i, _, h), v in zip(rows, vecs)])
            db.commit()
            _append_matrix([i for i, _, _ in rows])
        if mark and (not limit or len(rows) < int(limit)) and _written == mark[1]:   # all that was left, and nothing written since
            _settled = mark
    asked = np.asarray(vectors.embed([query], query=True)[0], dtype=np.float32) if query else None
    return len(rows), asked


def _set_matrix(rows, matrix, flags):
    # the matrix, its ids and the scope masks change together; flags — (find, auto) of each row
    global _rows, _matrix, _row_of, _masks, _dead
    _rows, _matrix, _dead = rows, matrix, 0
    _row_of = {i: n for n, i in enumerate(rows)}
    _masks = {"find": np.array([f for f, _ in flags], dtype=bool), "auto": np.array([a for _, a in flags], dtype=bool),
              "all": np.ones(len(rows), dtype=bool)}


def _append_matrix(ids):
    # new embeddings join the in-memory matrix in place; if some of them are in it already, it's simpler to load it afresh
    global _matrix
    if _matrix is None or _rows is None:
        return
    if any(i in _row_of for i in ids):
        _forget_matrix()
        return
    fresh = _connect().execute(f"SELECT id, vec, kind, meta FROM items WHERE id IN ({','.join('?' * len(ids))}) AND vec IS NOT NULL AND vmodel = ? ORDER BY id",
                               [*ids, _current()]).fetchall()
    size = config.EMBED_DIMS * 2
    if any(len(v) != size for _, v, _, _ in fresh):
        _forget_matrix()
        return
    if not fresh:
        return
    block = np.array([_unpack(v) for _, v, _, _ in fresh])
    norms = np.linalg.norm(block, axis=1)
    norms[norms == 0] = 1
    flags = [_scopes(k, m) for _, _, k, m in fresh]
    _matrix = np.vstack([_matrix, block / norms[:, None]]) if len(_rows) else block / norms[:, None]
    for i, _, _, _ in fresh:
        _row_of[i] = len(_rows)
        _rows.append(i)
    _masks["find"] = np.concatenate([_masks["find"], np.array([f for f, _ in flags], dtype=bool)])
    _masks["auto"] = np.concatenate([_masks["auto"], np.array([a for _, a in flags], dtype=bool)])
    _masks["all"] = np.concatenate([_masks["all"], np.ones(len(fresh), dtype=bool)])


def _load_matrix():
    global _written
    if _matrix is not None:
        return
    db = _connect()
    rows = db.execute("SELECT id, vec, kind, meta FROM items WHERE vec IS NOT NULL AND vmodel = ? ORDER BY id", (_current(),)).fetchall()   # only this model's vectors meet this model's queries
    size = config.EMBED_DIMS * 2   # float16 on disk; a vector of another width can't be from this model: it goes back to the queue
    good = [r for r in rows if len(r[1]) == size]
    if len(good) < len(rows):
        db.execute("UPDATE items SET vec = NULL, vmodel = '' WHERE vec IS NOT NULL AND length(vec) != ?", (size,))
        db.commit()
        _written += 1   # back in the queue
    if not good:
        _set_matrix([], np.zeros((0, 1), dtype=np.float32), [])
        return
    # float32 in memory: float16 arithmetic is slow on the CPU, the file is what we halve. One buffer and one copy, normalised in
    # place: a list of per-row arrays, their stack and the divided copy came to 2.5 times the matrix at its peak
    matrix = np.frombuffer(b"".join(v for _, v, _, _ in good), dtype=np.float16).reshape(len(good), config.EMBED_DIMS).astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0] = 1
    matrix /= norms[:, None]
    _set_matrix([i for i, _, _, _ in good], matrix, [_scopes(k, m) for _, _, k, m in good])


# ---------- search ----------
# Russian inflections, longest first: the words channel looks for the stem, so "доллара" and "долларом" both find "доллар"
# (trigram FTS matches a phrase as a substring). Only while four letters remain: a three-letter stem would match half the texts.
ENDINGS = sorted(("иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими", "ях", "ах", "ов", "ев", "ей", "ой", "ый", "ий", "ая", "яя", "ое",
                  "ее", "ые", "ие", "ом", "ем", "ам", "ям", "ую", "юю", "ью", "а", "я", "ы", "и", "у", "ю", "е", "о", "й", "ь"), key=len, reverse=True)


def _stem_word(w):
    if not re.fullmatch(r"[а-яё]+", w):
        return w   # Latin words and codes: as written
    for e in ENDINGS:
        if w.endswith(e) and len(w) - len(e) >= 4:
            return w[:-len(e)]
    return w


# words that carry no subject: in a question they would match half the registry ("какой", "когда", "мне") and push the real
# answer down; measured on the paraphrase set (sena/home/bench.py) — without them the first answer is right far more often
STOP_WORDS = set("""что как какой какая какое какие каком какую когда где куда откуда кто кого кому чем чём сколько почему зачем
мне меня мой моя моё мое мои моих мою нам нас наш наша наше наши вам вас ваш его её ее их она они оно он ты вы мы себя свой своя свои
это эта этот эти тот там тут здесь есть был была было были будет буду быть для про при без над под или если чтобы тоже очень можно
нужно надо все всё весь вся уже ещё еще нет так вот the and for with what when where who how""".split())


def stems(text):
    """The words of a text as search compares them: stems of Russian words, Latin words as written, no stop words, three
    letters and more — "детей" and "дети" meet (for scoring pieces of a page the same way the registry is searched)."""
    return [_stem_word(w) for w in re.findall(r"\w+", str(text).lower()) if len(w) >= 3 and w not in STOP_WORDS]


def window(text, query, size):
    """The part of a long text where the query's words are — about size characters, cut at words, "…" where it was cut — or
    its start when no word of the query is in it. A hotel named near the end of a long reply is shown, not the reply's first
    lines (Haystack's sentence window: the match with what is around it)."""
    text = str(text or "")
    if len(text) <= size:
        return text
    low = text.lower()
    hits = sorted(m.start() for stem in set(stems(query)) for m in re.finditer(re.escape(stem), low))
    if not hits:
        return text[:size]
    lead = size // 5   # a little of what comes before the first word found
    best, most = 0, -1
    for at in hits:
        start = max(0, min(at - lead, len(text) - size))
        count = bisect.bisect_right(hits, start + size) - bisect.bisect_left(hits, start)
        if count > most:
            best, most = start, count
    end = min(len(text), best + size)
    piece = text[best:end]
    if best > 0:
        piece = "…" + piece.split(" ", 1)[-1] if " " in piece[:40] else "…" + piece
    if end < len(text):
        piece = piece.rsplit(" ", 1)[0] + "…" if " " in piece[-40:] else piece + "…"
    return piece


def _spellings(word):
    # е and ё are one letter to the owner ("Артем" for "Артём", "елка"): the words channel looks for either spelling
    spots = [i for i, ch in enumerate(word) if ch in "её"]
    if not spots or len(spots) > 3:
        return [word]
    out = []
    for letters in itertools.product("её", repeat=len(spots)):
        chars = list(word)
        for i, ch in zip(spots, letters):
            chars[i] = ch
        out.append("".join(chars))
    return out


def _fts_query(query, match):
    words = stems(query)   # trigrams: words shorter than three letters aren't searched
    if not words:
        return None
    return (" AND " if match == "all" else " OR ").join("(" + " OR ".join(f'"{s}"' for s in _spellings(w)) + ")" for w in dict.fromkeys(words))


def _where(kinds, since, until, meta, alias="i"):
    sql, params = [], []
    if kinds:
        sql.append(f"{alias}.kind IN ({','.join('?' * len(kinds))})")
        params += list(kinds)
    if since:
        sql.append(f"{alias}.time >= ?")
        params.append(float(since))
    if until:
        sql.append(f"{alias}.time <= ?")
        params.append(float(until))
    for key, value in (meta or {}).items():
        sql.append(f"json_extract(NULLIF({alias}.meta, ''), ?) = ?")
        params += [f"$.{key}", value]
    return sql, params


def _policy_sql(scope, alias="i"):
    """The kinds' policy for a scope as SQL conditions: what a search scope may return is chosen before ranking, so the hidden
    kinds (overheard speech, Sena's own notes) never take the places of real finds; "vector" — what gets an embedding.
    "all" — no policy."""
    if scope not in ("find", "auto", "vector"):
        return [], []
    sql, params = [], []
    for kind, spec in KINDS.items():
        rule = spec[scope]
        if rule is True:
            continue
        if rule is False:
            sql.append(f"{alias}.kind != ?")
            params.append(kind)
        else:
            cond, extra = rule.sql(alias)
            sql.append(f"({alias}.kind != ? OR {cond})")
            params += [kind, *extra]
    return sql, params


def _allowed(kinds, since, until, meta, linked_to=None):
    # the ids a search may return under the filters; None — no filter at all.
    # linked_to = (kind, ref): that record itself, the records linked to it (either direction) and, for a person, their own facts
    sql, params = _where(kinds, since, until, meta)
    if not sql and not linked_to:
        return None
    ids = {i for (i,) in _connect().execute("SELECT i.id FROM items i" + (" WHERE " + " AND ".join(sql) if sql else ""), params)}
    if linked_to:
        kind, ref = linked_to[0], str(linked_to[1])
        near = {i for (i,) in _connect().execute(
            "SELECT i.id FROM items i JOIN links l ON (l.src_kind = i.kind AND l.src_ref = i.ref AND l.dst_kind = ? AND l.dst_ref = ?) "
            "OR (l.dst_kind = i.kind AND l.dst_ref = i.ref AND l.src_kind = ? AND l.src_ref = ?) WHERE l.valid_to IS NULL "
            "UNION SELECT id FROM items WHERE kind = ? AND (ref = ? OR substr(ref, 1, ?) = ?)",
            (kind, ref, kind, ref, kind, ref, len(ref) + 1, ref + ":"))}
        ids &= near
    return ids


def browse(kinds=None, since=None, until=None, meta=None, linked_to=None, k=20, scope="all"):
    """Records under the filters without a query, newest first — "everything about Olga in March". scope — as in search: a general
    look at a month doesn't bring overheard speech."""
    hide, hide_params = _policy_sql(scope)
    with _lock:
        allowed = _allowed(kinds, since, until, meta, linked_to)
        if allowed is None:
            rows = _connect().execute("SELECT kind, ref, time, tz, meta, text FROM items i" + (" WHERE " + " AND ".join(hide) if hide else "")
                                      + " ORDER BY time DESC LIMIT ?", (*hide_params, int(k))).fetchall()
        else:
            ids = list(allowed)
            rows = []
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                rows += _connect().execute(f"SELECT kind, ref, time, tz, meta, text FROM items i WHERE id IN ({','.join('?' * len(part))})"
                                           + "".join(f" AND {h}" for h in hide), (*part, *hide_params)).fetchall()
            rows.sort(key=lambda r: -r[2])
            rows = rows[:int(k)]
    return [{"score": 0.0, "cosine": 0.0, "words": False, "kind": kind, "ref": ref, "time": when, "tz": tz, "meta": _meta_of(m), "text": text}
            for kind, ref, when, tz, m, text in rows]


def _linked_sql(linked_to, alias="i"):
    """Records linked to (kind, ref) — and a person's own facts — as one SQL condition. For earlier versions: their record may
    be gone and its links closed, and what it was linked to still counts."""
    kind, ref = linked_to[0], str(linked_to[1])
    return (f"(({alias}.kind = ? AND ({alias}.ref = ? OR substr({alias}.ref, 1, ?) = ?)) OR EXISTS (SELECT 1 FROM links l WHERE "
            f"(l.src_kind = {alias}.kind AND l.src_ref = {alias}.ref AND l.dst_kind = ? AND l.dst_ref = ?) OR "
            f"(l.dst_kind = {alias}.kind AND l.dst_ref = {alias}.ref AND l.src_kind = ? AND l.src_ref = ?)))",
            [kind, ref, len(ref) + 1, ref + ":", kind, ref, kind, ref])


def _text_hits(query, match, kinds, since, until, meta, table="items", fts="items_fts", limit=CANDIDATES, scope="all", linked_to=None):
    q = _fts_query(query, match)
    if not q:
        return []
    sql, params = _where(kinds, since, until, meta)
    hide, hide_params = _policy_sql(scope)
    if linked_to:   # chosen in SQL, before the best are taken: a person's records past the registry's top words were missed
        cond, extra = _linked_sql(linked_to)
        hide, hide_params = hide + [cond], hide_params + extra
    if table == "items" and _small(kinds):   # a few hundred skills or rules: their own texts, not every match in the registry
        rows = _connect().execute("SELECT i.id, i.text FROM items i WHERE " + " AND ".join(sql + hide), params + hide_params).fetchall()
        return _scored(query, match, rows)[:int(limit)]
    where = " AND ".join([f"{fts} MATCH ?"] + sql + hide)
    try:
        return [i for (i,) in _connect().execute(f"SELECT i.id FROM {fts} f JOIN {table} i ON i.id = f.rowid WHERE {where} ORDER BY f.rank LIMIT {int(limit)}",
                                                 [q] + params + hide_params)]
    except sqlite3.OperationalError:   # quotes or special characters in the query
        return []


SMALL_KIND = 1000   # records of the asked kinds up to which the words channel reads their own texts: FTS ranks every match in the
                    # registry before the kind is chosen, so a search of a few hundred skills paid for every matching message (P4, 26.10)


def _small(kinds):
    if not kinds:
        return False
    marks = ",".join("?" * len(kinds))
    return _connect().execute(f"SELECT COUNT(*) FROM items WHERE kind IN ({marks})", list(kinds)).fetchone()[0] <= SMALL_KIND


def _scored(query, match, rows):
    """Ids of rows [(id, text)] holding the query's words, best first — as the trigram index finds them (a stem inside the text,
    е or ё) and ranked by BM25 over these rows alone (Robertson's k1 = 1.2, b = 0.75, as FTS5's bm25())."""
    words = [_spellings(w) for w in dict.fromkeys(stems(query))]
    if not words or not rows:
        return []
    texts = [(item_id, text.lower()) for item_id, text in rows]
    tf = [[sum(low.count(s) for s in spelled) for spelled in words] for _, low in texts]
    df = [sum(1 for counts in tf if counts[j]) for j in range(len(words))]
    n, avg = len(texts), sum(len(low) for _, low in texts) / len(texts) or 1
    idf = [math.log((n - d + 0.5) / (d + 0.5) + 1) for d in df]
    scored = []
    for (item_id, low), counts in zip(texts, tf):
        if not (all(counts) if match == "all" else any(counts)):
            continue
        norm = 1.2 * (0.25 + 0.75 * len(low) / avg)
        scored.append((sum(w * c * 2.2 / (c + norm) for w, c in zip(idf, counts) if c), item_id))
    scored.sort(key=lambda s: (-s[0], -s[1]))
    return [item_id for _, item_id in scored]


def _vector_hits(q, allowed, scope="all", also=()):
    """(id -> cosine for the finds and for also, best ids by meaning) among the records the filters and the scope allow. The
    scores of the rest are masked before the best are taken — the global best filtered afterwards lost what was asked for as
    memory grew (a filter covering 0.3% of 300k records kept 1 of 50 wanted candidates; masked first, all 50 for +3 ms). The
    cosines are read only where needed, not built for every row. One seam for a future vector index (sqlite-vec)."""
    _load_matrix()
    if not _rows or q.shape[0] != _matrix.shape[1]:
        return {}, []
    started = time.perf_counter()
    try:
        q = q / (np.linalg.norm(q) or 1)
        scores = _matrix @ q
        mask = _masks.get(scope, _masks["all"])   # every scope leaves out the rows whose record changed or left
        if allowed is not None:
            keep = np.zeros(len(_rows), dtype=bool)
            keep[[_row_of[i] for i in allowed if i in _row_of]] = True
            mask = mask & keep
        ranked = np.where(mask, scores, -np.inf)
        want = min(int(mask.sum()), CANDIDATES)
        if want <= 0:
            return {i: float(scores[_row_of[i]]) for i in also if i in _row_of}, []
        top = np.argpartition(-ranked, want - 1)[:want]
        top = top[np.argsort(-ranked[top])]
        order = [_rows[int(i)] for i in top]
        cosines = {i: float(scores[_row_of[i]]) for i in (*order, *also) if i in _row_of}
        return cosines, order
    finally:
        _ranking_ms.append((time.perf_counter() - started) * 1000)


def search(query, kinds=None, k=8, match="any", text_only=False, min_cosine=0.0, half_life_days=0,
           since=None, until=None, meta=None, scope="find", history=False, linked_to=None):
    """Best records for the query: [{"score", "cosine", "words" (matched by words), "kind", "ref", "time", "tz", "meta", "text"}].
    kinds — which kinds to search; match — all: every word, any: at least one; text_only — skip meaning (no embedding call);
    min_cosine — cut off anything too far in meaning; half_life_days — the freshness bonus halves over this many days (0 — no bonus);
    since/until — a span of moments; meta — attributes that must match ({"tier": "core"});
    scope — "find": records the policy lets into a general search, "auto": only what may be mixed in unasked, "all": no policy filter;
    history — also search the earlier versions of records (words only; such rows carry "valid_to")."""
    query = _clean(query)
    if not query:
        return []
    q = None
    if not text_only:
        try:   # embeddings for new records and the query — one call to the embedding model (core/roles.py), outside the lock
            key = (_current(), query)   # a query embedded by the other model (the role moved home or to the cloud) is not this one's
            with _lock:
                q = _recent_queries.get(key)
            if q is not None:
                embed_pending(PENDING_BATCH)
            else:
                _, q = embed_pending(PENDING_BATCH, query)
                if q is not None:
                    with _lock:   # two searches at once mutated it unguarded: "dictionary changed size" lost a computed vector
                        _recent_queries[key] = q
                        while len(_recent_queries) > 16:
                            del _recent_queries[next(iter(_recent_queries))]
        except Exception as e:
            events.error("Указатель памяти: отпечаток не посчитан, ищу только по словам", e)
    with _lock:
        try:   # both channels choose among what the filters and the scope allow, then rank (filter first, rank second)
            allowed = _allowed(kinds, since, until, meta, linked_to)
            by_words = [i for i in _text_hits(query, match, kinds, since, until, meta, scope=scope, linked_to=linked_to)
                        if allowed is None or i in allowed]
            cosines, by_meaning = _vector_hits(q, allowed, scope, by_words) if q is not None else ({}, [])
            old = _text_hits(query, match, kinds, since, until, meta, "versions", "versions_fts", scope=scope, linked_to=linked_to) if history else []
        except sqlite3.DatabaseError as e:   # the database broke mid-flight: return an empty search, not a failed task
            events.error("Указатель памяти", e)
            return []
        worded = set(by_words)
        fused = {}
        for ranked in (by_meaning, by_words):   # equal weights: a lighter words channel raised R@1 on paraphrases (68 -> 74 %), but a
            for rank, item_id in enumerate(ranked):   # record found by its exact name alone could then never rise above the 50 meaning finds
                fused[item_id] = fused.get(item_id, 0.0) + 1.0 / (RRF_K + rank + 1)
        found = {}
        ids = list(fused)
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            for item_id, kind, ref, when, tz, meta_text, text in _connect().execute(f"SELECT id, kind, ref, time, tz, meta, text FROM items WHERE id IN ({','.join('?' * len(part))})", part):
                found[item_id] = (kind, ref, when, tz, _meta_of(meta_text), text)
        versions_found = {}
        if old:
            for vid, kind, ref, when, tz, meta_text, text, valid_to in _connect().execute(
                    f"SELECT id, kind, ref, time, tz, meta, text, valid_to FROM versions WHERE id IN ({','.join('?' * len(old))})", old):
                versions_found[vid] = (kind, ref, when, tz, _meta_of(meta_text), text, valid_to)
    now = time.time()

    def fresh(when):   # a fresh record can weigh up to twice as much
        return 1 + math.exp(-max(0.0, now - when) / 86400 * math.log(2) / half_life_days) if half_life_days else 1.0
    out = []
    for item_id, score in fused.items():
        if item_id not in found:
            continue
        kind, ref, when, tz, meta_d, text = found[item_id]
        pol = policy(kind, meta_d)
        if scope == "find" and not pol.find or scope == "auto" and not pol.auto:
            continue
        cosine = cosines.get(item_id, 0.0)
        if min_cosine and cosine < min_cosine:
            continue
        out.append({"score": score * fresh(when), "cosine": cosine, "words": item_id in worded, "kind": kind, "ref": ref, "time": when, "tz": tz, "meta": meta_d, "text": text})
    for rank, vid in enumerate(old):   # earlier wordings compete on the same terms as a record found by words
        if vid in versions_found:
            kind, ref, when, tz, meta_d, text, valid_to = versions_found[vid]
            out.append({"score": fresh(when) / (RRF_K + rank + 1), "cosine": 0.0, "words": True, "kind": kind, "ref": ref, "time": when, "tz": tz,
                        "meta": meta_d, "text": text, "valid_to": valid_to})
    out.sort(key=lambda r: -r["score"])
    return out[:k]


def count(kind=None, meta=None):
    with _lock:
        db = _connect()
        sql, params = _where([kind] if kind else None, None, None, meta)
        return db.execute("SELECT COUNT(*) FROM items i" + (" WHERE " + " AND ".join(sql) if sql else ""), params).fetchone()[0]


def counts():
    """How many records of each kind: {kind: count} (for the dashboard)."""
    with _lock:
        return dict(_connect().execute("SELECT kind, COUNT(*) FROM items GROUP BY kind").fetchall())


def pending():
    """Records still to embed with the current model: without a vector, or with one from another model (counted, not read)."""
    where, params = _pending_sql(_current())
    with _lock:
        if _settled == (_current(), _written):
            return 0
        return _connect().execute(f"SELECT COUNT(*) FROM items i WHERE {where}", params).fetchone()[0]


def stats():
    """Size and speed of the registry, for the analytics screen and the nightly report."""
    with _lock:
        db = _connect()
        vectors_n = db.execute("SELECT COUNT(*) FROM items WHERE vmodel = ?", (_current(),)).fetchone()[0]   # the stamp's index alone
        stale_n = db.execute("SELECT COUNT(*) FROM items WHERE vmodel > '' AND (vmodel < ? OR vmodel > ?)", (_current(), _current())).fetchone()[0]
        versions_n = db.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
        links_n = db.execute("SELECT COUNT(*) FROM links WHERE valid_to IS NULL").fetchone()[0]
        size = config.INDEX_DB.stat().st_size if config.INDEX_DB.exists() else 0
        ranking = round(statistics.median(_ranking_ms), 1) if _ranking_ms else 0.0   # the ranking alone, the median of the last ones
    return {"items": counts(), "vectors": vectors_n, "stale": stale_n, "model": _current(), "versions": versions_n, "links": links_n, "pending": pending(),
            "file_mb": round(size / 1e6, 1), "matrix_mb": round(vectors_n * config.EMBED_DIMS * 4 / 1e6, 1), "search_ms": ranking}
