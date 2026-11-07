"""Database of people around Tema. One person = one card: names (from chats and from speech),
voice embeddings, facts about them, Tema's relationship to them, Sena's opinion of them.
Both voice (who spoke) and conversations (who Tema is writing to) feed into it — if it's the same person, the cards get merged."""
import re
import threading
import time
import uuid

import numpy as np

from sena import config
from sena.core import events, local, moment, store, vectors
from sena.core.tools import Failure, string, strings, tool
from sena.memory import index

_lock = threading.RLock()   # reentrant: the cards' first load runs under it too, also from inside a writer's block
MATCH = 0.75   # voice similarity threshold for considering it the same person
MARGIN = 0.05  # the best candidate must be noticeably closer than the second one, otherwise we're not sure
MAX_VOICES = 8  # voice embeddings per person (so the file doesn't grow unbounded)
VOICE_SAVE = 60   # s: a known voice heard again refreshes its prints in memory at once, on disk at most this often

_people = None
_voices_due = False   # prints refreshed in memory and not written yet: the next write of the cards takes them along
_saved_at = 0.0       # when the cards were last written (time.monotonic)


def all_people():
    global _people
    if _people is None:
        with _lock:   # two callers at startup each loaded a list of their own, and a change made through one of them was lost
            if _people is None:
                _people = _load()
    return _people


def _load():
    cards, voices, changed = store.load_json(config.PEOPLE_FILE, []), store.load_json(config.VOICES_FILE, {}), False
    for p in cards:
        if "voices" in p:   # cards from before the fingerprints had a record of their own: moved there once
            if p["voices"]:
                voices.setdefault(p["id"], p["voices"])
            del p["voices"]
            changed = True
        if any(isinstance(f, str) for f in p["facts"]):   # cards from before facts carried time and source: converted once
            p["facts"] = [_fact(f, "chat", p.get("created", 0.0)) if isinstance(f, str) else f for f in p["facts"]]
            changed = True
    for p in cards:
        p["voices"] = voices.get(p["id"], [])
    changed = _drop_owner_prints(cards) or changed
    if changed:
        _write(cards, voices=True)
    return cards


def _write(cards, versioned=True, voices=False):
    """The cards go to their record without the voice fingerprints, which have one of their own, overwritten in place: eight
    prints a person made every version of the cards a megabyte, and each voice heard again wrote one. versioned=False — only
    when the time a voice was last heard changed: that is no change of the card worth its history. The prints go first: the
    move of old cards' prints wrote the cards without them before the prints' own record, and a failure between the two
    writes forgot every known voice."""
    global _voices_due
    if voices or _voices_due:
        store.save_json(config.VOICES_FILE, {p["id"]: p["voices"] for p in cards if p.get("voices")})
        _voices_due = False
    (store.save_versioned if versioned else store.save_json)(config.PEOPLE_FILE, [{k: v for k, v in p.items() if k != "voices"} for p in cards])


def _save(versioned=True, voices=False):
    global _saved_at
    _write(all_people(), versioned, voices)
    _saved_at = time.monotonic()


def _fact(text, source, when=None, sources=None):
    # one fact: what, when and where it came from (chat, heard, vault, app). A superseded fact gets valid_to instead of vanishing.
    # sources — the overheard lines it was learned from: forgetting them forgets the fact too (Graphiti's provenance)
    fact = {"text": text, "t": moment.now() if when is None else when, "tz": moment.offset(), "source": source, "valid_to": None}
    if sources:
        fact["from"] = sorted(set(sources))
    return fact


KEEP_WORDS = 0.8   # a new fact keeping this share of an old one's words, and saying more, is that fact told fuller


def _words(text):
    return set(re.findall(r"\w+", text.lower()))


def _fuller(new, old):
    # the new fact is the old one with more said ("работает в банке" -> "работает в банке аналитиком"): the old one retires
    was, now = _words(old), _words(new)
    return bool(was) and len(now) > len(was) and len(was & now) >= KEEP_WORDS * len(was)


def facts(p, all=False, exclude=()):
    """Texts of a person's facts: the current ones, or every fact ever recorded (all=True); exclude — sources to leave out."""
    return [f["text"] for f in p["facts"] if (all or f["valid_to"] is None) and f.get("source") not in exclude]


def retire_fact(pid, text, source=None):
    """A fact no longer holds: it stays in the card with the moment it stopped being true. source — only a fact that came from there."""
    with _lock:
        p = by_id(pid)
        if not p:
            return Failure("Человек не найден")
        gone = [f for f in p["facts"] if f["valid_to"] is None and f["text"] == text and (source is None or f.get("source") == source)]
        for f in gone:
            f["valid_to"] = moment.now()
        if gone:
            _save()
    if gone:
        sync_index()
    return "Убрано" if gone else Failure("Такого факта нет")


def sync_index():
    """Facts about people in the memory index: "Name: fact"; names and aliases become the registry's mention dictionary."""
    rows, known = [], {}
    for p in all_people():
        name = p.get("name") or (p["aliases"][0] if p["aliases"] else p["id"])
        rows.append((p["id"], card(p), p["created"]))   # the person as a record: what links point at and what shows up as "related"
        rows += [(f"{p['id']}:{index.line_ref(f['text'])}", f"{name}: {f['text']}", f["t"] or p["last_seen"]) for f in p["facts"] if f["valid_to"] is None]
        for n in [p.get("name")] + p["aliases"]:
            if n:
                known[n] = p["id"]
    index.register_names("человек", known)
    return index.sync("человек", rows)


def card(p):
    """One line about a person: names, relationship, Sena's opinion. Not how many facts are known: every new fact changed the
    card's record, and each change was a version of it and a new embedding."""
    names = " / ".join(n for n in [p.get("name")] + p["aliases"] if n) or f"#{p['id']}"
    parts = [names]
    if p["relationship"]:
        parts.append(f"отношения: {p['relationship']}")
    if p["opinion"]:
        parts.append(f"мнение: {p['opinion']}")
    return " — ".join(parts)


def new_person(name=None, alias=None, voices=None):
    person = {
        "id": uuid.uuid4().hex[:8],
        "name": name,                          # what Tema calls them
        "aliases": [a for a in [alias] if a],  # variants: name in chat, name used by voice
        "voices": [v for v in voices or [] if not owner_like(v)][-MAX_VOICES:],  # voice embeddings, never the owner's own
        "facts": [],                           # what's known about the person
        "relationship": "",                    # how Tema relates to them
        "opinion": "",                         # Sena's opinion of them
        "created": time.time(),
        "last_seen": time.time(),
    }
    with _lock:
        all_people().append(person)
        _save(voices=bool(person["voices"]))
    sync_index()   # the new name goes into the mention dictionary right away
    return person


def by_id(pid):
    return next((p for p in all_people() if p["id"] == pid), None)


def title(pid):
    """A person as the owner tells them apart: the name (or another name) and how he relates to them, «Ольга Петрова (коллега)»."""
    p = by_id(pid)
    if not p:
        return pid
    name = p.get("name") or (p["aliases"][0] if p["aliases"] else f"#{pid}")
    return name + (f" ({p['relationship']})" if p["relationship"] else "")


def rename_sources(names):
    """Overheard lines got new names (heard.py: a line without an id is named by what it holds, no longer by its place in the
    log): the facts learned from them follow, so forgetting those lines still forgets the facts. names — {old: new}."""
    changed = False
    with _lock:
        for p in all_people():
            for f in p["facts"]:
                if any(s in names for s in f.get("from", [])):
                    f["from"] = sorted({names.get(s, s) for s in f["from"]})
                    changed = True
        if changed:
            _save(versioned=False)   # the same facts under the same words: no change of the card worth a version
    return changed


def match(embedding, groups):
    # groups: [(who, [embeddings])]. We compare against each person's average voice (more reliable than against their best sample).
    # The answer names someone only if the similarity is above the threshold and noticeably closer than the second candidate.
    size = len(embedding)   # embeddings of a different length (the voice model changed) aren't compared
    scores = []
    for key, voices in groups:
        same = [v for v in voices if len(v) == size]
        if same:
            scores.append((vectors.cosine(embedding, np.mean(same, axis=0)), key))
    scores.sort(reverse=True)
    if not scores:
        return None, 0.0
    best, key = scores[0]
    second = scores[1][0] if len(scores) > 1 else 0.0
    return (key, best) if best >= MATCH and best - second >= MARGIN else (None, best)


def identify_voice(embedding):
    # find a person by their voice embedding; return (person, similarity) or (None, best_match)
    pid, score = match(embedding, [(p["id"], p["voices"]) for p in all_people()])
    return (by_id(pid) if pid else None), score


OWNER = "owner"
OWNER_VOICES = 16   # the owner speaks to Sena every day: more samples, a steadier average
OWNER_MIN_SEC = 2   # shorter speech gives an unreliable fingerprint


def _owner_prints():
    return store.load_json(config.OWNER_VOICE_FILE, [])


def owner_like(embedding, prints=None):
    """Whether a print is the owner's own voice, judged against his average alone. Such a print never goes onto someone
    else's card: the background hearing once made a "stranger" of his own voice (0.97 alike), and from then on every spoken
    command of his lost to that card by a hair and counted as someone else's — accepted one time, refused the next."""
    same = [v for v in (_owner_prints() if prints is None else prints) if len(v) == len(embedding)]
    return bool(same) and vectors.cosine(embedding, np.mean(same, axis=0)) >= MATCH


def _drop_owner_prints(cards):
    # prints of the owner's voice that ended up on other people's cards: removed (True if any were)
    prints = _owner_prints()
    changed = False
    for p in cards:
        kept = [v for v in p.get("voices") or [] if not owner_like(v, prints)]
        if len(kept) != len(p.get("voices") or []):
            p["voices"], changed = kept, True
            events.log("person", f"с карточки {p.get('name') or p['id']} убран голос Темы", pid=p["id"])
    return changed


def remember_owner_voice(embedding):
    """A voice message the owner recorded himself (Telegram, the app), or a live turn already recognised as his: his voice is
    learned without any setup step, and keeps up with the microphone he actually talks into."""
    with _lock:
        voices = _owner_prints()
        store.save_json(config.OWNER_VOICE_FILE, ([v for v in voices if len(v) == len(embedding)] + [embedding])[-OWNER_VOICES:])
        if _people is not None and _drop_owner_prints(_people):
            _save(versioned=False, voices=True)


def learn_owner_voice(audio):
    """The owner's own voice message (bytes): keep its fingerprint. Run in the background; a short one teaches nothing."""
    vec, seconds = local.owner_print(audio)
    if seconds >= OWNER_MIN_SEC:
        remember_owner_voice(vec)


def voice_of(embedding):
    """Whose voice: OWNER, a person's id, or None (unknown, or too close between two to say), and the similarity."""
    return match(embedding, [(OWNER, _owner_prints())] + [(p["id"], p["voices"]) for p in all_people()])


def is_owner(embedding):
    """True — the owner speaks; False — someone else, or too unclear to say; None — his voice isn't learned yet."""
    if not _owner_prints():
        return None
    return voice_of(embedding)[0] == OWNER


def remember_voice(pid, embedding):
    """A known voice heard again: its prints are refreshed in memory at once (recognition reads them there) and written at
    most once in VOICE_SAVE, or with whatever write of the cards comes first — every recognised phrase rewrote every person's
    prints and the cards, a few gigabytes of writes an hour of talk nearby. Lost on a crash: a minute of samples of voices
    known anyway."""
    global _voices_due
    if owner_like(embedding):   # his own voice never becomes someone else's
        return
    with _lock:
        p = by_id(pid)
        if p:
            p["voices"] = ([v for v in p["voices"] if len(v) == len(embedding)] + [embedding])[-MAX_VOICES:]   # entries of a different length are dropped
            p["last_seen"] = time.time()
            _voices_due = True
            if time.monotonic() - _saved_at >= VOICE_SAVE:
                _save(versioned=False)
            events.log("person", f"голос запомнен: {p.get('name') or pid}", pid=pid)


def flush():
    """Write what is refreshed in memory only (the prints of voices heard in the last minute): before the core stops."""
    with _lock:
        if _voices_due and _people is not None:
            _save(versioned=False)


def update(pid, clear=(), **fields):
    # update the card: name, relationship, opinion, add aliases/facts. An empty field value changes nothing (that's how the model calls it);
    # clear — which of name/relationship/opinion to clear (requested by the app when the owner erased a field); "aliases" in it —
    # the aliases given are the whole list now (the app sends the field as he left it), otherwise they are only added
    with _lock:
        p = by_id(pid)
        if not p:
            return Failure("Человек не найден")
        for key in ("name", "relationship", "opinion"):
            if fields.get(key):
                p[key] = fields[key]
            elif key in clear:
                p[key] = ""
        if "aliases" in clear:
            p["aliases"] = []
        for a in fields.get("aliases", []) or []:
            if a and a not in p["aliases"] and a != p.get("name"):
                p["aliases"].append(a)
        sources = fields.get("sources") or None
        retired = []
        # what no longer holds goes first: the apps send a shortened fact as a new one plus the old one retired, and the old,
        # fuller one counted as "already known", so the new one was never added and the old one then retired — the fact was gone
        for text in fields.get("retire", []) or []:   # the owner or the model said a fact no longer holds
            want = " ".join(str(text).lower().split())
            hit = [f for f in p["facts"] if f["valid_to"] is None and " ".join(f["text"].lower().split()) == want]
            for f in hit:
                f["valid_to"] = moment.now()
                retired.append(f["text"])
        for fact in fields.get("facts", []):
            fact = " ".join(str(fact).split())
            if not fact:
                continue
            current = [f for f in p["facts"] if f["valid_to"] is None]
            same = next((f for f in current if f["text"].lower() == fact.lower() or _fuller(f["text"], fact)), None)
            if same:   # already known (as is, or said fuller): only the new evidence is added
                if sources:
                    same["from"] = sorted(set(same.get("from", [])) | set(sources))
                continue
            for f in current:
                if _fuller(fact, f["text"]):   # mem0's merge: the barer old fact retires, nothing is deleted
                    f["valid_to"] = moment.now()
                    retired.append(f["text"])
            p["facts"].append(_fact(fact, fields.get("source", "chat"), sources=sources))   # no cap: an old fact is never pushed out by a new one
        p["last_seen"] = time.time()
        _save()
    sync_index()
    return f"Обновлено: {p.get('name') or p['id']}" + (f"; больше не верно: {'; '.join(retired)}" if retired else "")


def forget_sources(line_ids):
    """The owner had overheard lines forgotten: facts learned only from them go too — from the cards, the registry and the
    cards' earlier versions; facts with other evidence just lose these lines. Returns how many facts went."""
    line_ids, gone = set(line_ids), []
    with _lock:
        for p in all_people():
            kept = []
            for f in p["facts"]:
                sources = set(f.get("from", []))
                if sources and sources <= line_ids:
                    gone.append((p, f))
                    continue
                if sources & line_ids:
                    f["from"] = sorted(sources - line_ids)
                kept.append(f)
            p["facts"] = kept
        if gone:   # the registry first: a crash before the cards were saved left the forgotten facts for the next sync to drop into versions
            index.purge("человек", [f"{p['id']}:{index.line_ref(f['text'])}" for p, f in gone])
            _save()
            store.scrub(config.PEOPLE_FILE, [f["text"] for _, f in gone])
    if gone:
        sync_index()
        events.log("cleanup", f"забыто фактов о людях из забытых реплик: {len(gone)}", facts=len(gone))
    return len(gone)


def delete(pid):
    # remove a person's card entirely (the phone app's own action: Tema deleting a card he doesn't want, not something Sena does herself)
    with _lock:
        p = by_id(pid)
        if not p:
            return Failure("Человек не найден")
        all_people().remove(p)
        _save(voices=True)
    index.drop("человек", prefix=f"{pid}:")
    index.drop("человек", pid)   # the card row too; a history kind, so both stay in versions
    return f"Удалено: {p.get('name') or pid}"


def merge(keep, drop):
    # merge two cards (id) into one: e.g. voice "Vasya" and chat "Vasya" are the same person. keep is the one that stays
    with _lock:
        keep, drop = by_id(keep), by_id(drop)
        if not keep or not drop or keep is drop:
            return Failure("Не найдено")
        keep["voices"] = (keep["voices"] + drop["voices"])[-MAX_VOICES:]
        for a in [drop.get("name")] + drop["aliases"]:
            if a and a not in keep["aliases"] and a != keep.get("name"):
                keep["aliases"].append(a)
        known = facts(keep, all=True)
        keep["facts"] += [f for f in drop["facts"] if f["text"] not in known]   # the merged card keeps the other's history too
        keep["relationship"] = keep["relationship"] or drop["relationship"]
        keep["opinion"] = keep["opinion"] or drop["opinion"]
        all_people().remove(drop)
        _save(voices=True)
    sync_index()
    # what was said and tagged under the dropped card is the kept one's now: the overheard lines (else "forget what he said"
    # missed them) and the owner's links to it (else they pointed at a card that no longer exists)
    from sena.memory import heard   # heard builds on people: imported here, where the two meet
    heard.moved(drop["id"], keep["id"])
    index.repoint("человек", drop["id"], keep["id"])
    return f"Склеено в {keep.get('name') or keep['id']}"


def _line(p):
    # one person for the model, without voice embeddings (they're large)
    names = " / ".join([n for n in [p.get("name")] + p["aliases"] if n]) or f"#{p['id']}"
    parts = [f"[{p['id']}] {names}"]
    if p["relationship"]:
        parts.append(f"отношения: {p['relationship']}")
    if facts(p):
        parts.append("; ".join(facts(p)[-4:]))
    if p["opinion"]:
        parts.append(f"моё мнение: {p['opinion']}")
    return " — ".join(parts)


def summary(limit=30):
    # brief overview of the most recently seen people
    return "\n".join(_line(p) for p in sorted(all_people(), key=lambda x: -x["last_seen"])[:limit])


def relevant(text, recent=5):
    """The people a message is about — named in it, in any case form — and the few seen most recently (whose voice or chat
    just came up). The brain gets these with each message instead of the whole address book, which also keeps its cached
    prompt unchanged when someone new speaks nearby."""
    named = [ref for kind, ref in index.mentions(text) if kind == "человек"]
    latest = [p["id"] for p in sorted(all_people(), key=lambda x: -x["last_seen"])[:recent]]
    found = [by_id(pid) for pid in dict.fromkeys(named + latest)]
    return "\n".join(_line(p) for p in found if p)[:4000] if found else ""


# ---------- tools for the model ----------
def name_person(id="", name="", aliases=None):
    """Name a card, or start one: without an id (the owner told about someone Sena has no card for) a new card is made —
    unless a card of exactly this name exists, then that one is named (cards were made only from voices and the apps, so
    «Маша — моя сестра» had nowhere to go but a memory line). A first name alone never joins a fuller card: «Маша Петрова»
    is no reason to rename the card «Маша»."""
    if not str(name).strip():
        return Failure("Назови человека: name")
    if not id:
        found = index.matches("человек", name, whole=True)
        if len(found) > 1:
            return Failure(f"Карточек «{name}» несколько: {', '.join(f'[{r}] {title(r)}' for r in found[:8])} — назови id нужной")
        if found:
            id = found[0]
        else:
            p = new_person(name=" ".join(str(name).split()))
            if aliases:
                update(p["id"], aliases=aliases)
            return f"Заведена карточка [{p['id']}]: {title(p['id'])} — факты и отношение Темы запиши через note_person"
    return update(id, name=name, aliases=aliases or [])


def note_person(id, facts=None, relationship=None, opinion=None, retire=None):
    return update(id, facts=facts or [], relationship=relationship, opinion=opinion, retire=retire or [])


TOOLS = [
    tool("name_person", "Назвать человека по id из «Люди вокруг Темы» (когда Тема сказал, кто это) или уточнить его имя/варианты имени. "
                        "Без id — завести карточку человеку, которого Тема назвал, а карточки нет («Маша — моя сестра»); потом note_person",
         {"id": string("id карточки; пусто — новая карточка"), "name": string(), "aliases": strings("как ещё зовут: имя в чате, варианты")}, ["name"]),
    tool("note_person", "Записать в карточку человека факт о нём, отношение Темы к нему или своё мнение о нём. Факт сменился "
                        "(переехал, сменил работу, расстались) — новый в facts, прежний в retire: он останется в истории, но перестанет быть текущим",
         {"id": string(), "facts": strings("самодостаточные строки: имена вместо «он», даты числами"), "relationship": string("как Тема к нему относится"),
          "opinion": string("твоё личное мнение о человеке"), "retire": strings("факты карточки, которые больше не верны, дословно")}, ["id"]),
    tool("merge_people", "Склеить две карточки людей в одну, если это один человек (например голос и чат). keep — какую оставить",
         {"keep": string(), "drop": string()}, ["keep", "drop"]),
]
FUNCTIONS = {"name_person": name_person, "note_person": note_person, "merge_people": merge}
