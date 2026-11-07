"""Recall: search across the whole archive — memory and its archive, conversations, tasks with remarks, people, notes.
The idea comes from Letta: only the memory core is always in context, everything else is fetched on demand.
This module also reconciles the index (memory/index.py) with its sources: at startup, at night and from the desk's button."""
from datetime import datetime
from pathlib import Path

from sena import config
from sena.core import costs, events
from sena.core.safety import external
from sena.core.tools import Failure, clamp, string, strings, tool
from sena.memory import facts, files, goals, heard, index, lessons, people, rules, skills, task_log, vault

NOISE = 0.35   # below this cosine, a record that matched no word is noise


def _label(row):
    when = datetime.fromtimestamp(row["time"]).strftime("%d.%m.%Y")
    kind, ref, meta = row["kind"], row["ref"], row.get("meta") or {}
    if kind == "сообщение":   # a voice that may not have been his is never shown as his words
        return f"{meta.get('author', 'сообщение')}{' (голос не его)' if meta.get('outside') else ''} {when}"
    if kind == "задача":
        return f"задача {ref} {when}"   # the id: for add_task_comment, tag and the task's own journal
    if kind == "заметка":
        return f"заметка {ref.rsplit('#', 1)[0]}"
    if kind == "правило" and meta.get("enabled") is False:
        return "правило (выключено)"
    if kind == "файл":
        return f"файл {ref}"   # the path under data/: for view_file, send_file and tag
    if kind == "факт" and meta.get("tier") == "archive":
        return "архив памяти"
    if kind == "факт":
        return "память"
    return kind


def _foreign(rows):
    # someone else's words among the finds (a file's text, a task's answer retelling a page, Sena's lines retelling a mail,
    # overheard speech — index.KINDS): the task that read them waits for the owner's "yes" before its dangerous tools
    return any(index.policy(r["kind"], r.get("meta")).foreign for r in rows)


# journal events that, with no task to vouch for them, may carry someone else's words: a tool's result, a model's answer about
# it, a message sent on from the hourly check, a page or an MCP server's reply
TASKLESS = ("tool", "model", "message_out", "mcp", "browser", "fetch", "read", "heard")


def _journal_foreign(rows):
    """Whether journal events hold someone else's words: a fenced text, a voice that wasn't his, an event of a task that had
    read foreign text, or one of the kinds above outside any task. A journal line of his own request or of her own steps is
    no one else's: every line counting as foreign held his coding and computer tasks after any search of the journal."""
    tids = {e["tid"] for e in rows if e.get("tid")}
    marked = {t for t in tids if events.search("all", kind="outside", tid=t, limit=1)}
    return any(index.fenced(e.get("text")) or e.get("stranger") or (e["tid"] in marked if e.get("tid") else e.get("kind") in TASKLESS)
               for e in rows)


def _whole(ref):
    # a long message lies in the registry in pieces ("<chat>:<id>#n"): its ref as a whole
    return ref.split("#", 1)[0]


def relevant(text, k=3, min_score=0.5, skip=(), seen=(), marks=None):
    # what from the archive is close to the message: mixed into the context automatically, without a "recall" request
    # (memory-core lines aren't taken — they're already in the prompt; the policy of each kind decides, see index.KINDS).
    # skip — kinds another block of the same context shows already: their places go to the next candidates (Haystack's
    # study: the same past task came twice into one message, once as a similar task and once from the archive)
    # seen — refs the model has in front of it already (the messages of the conversation's window).
    # The lines are data, never instructions: framed as such. Nothing that holds someone else's words is auto (index.KINDS),
    # so this never marks the turn by itself; should a policy ever let one through, it marks the running task —
    # marks — a list the source is added to then, for a caller whose task starts after its context is built
    rows, taken = [], set()
    for r in index.search(text, k=k + 4, min_cosine=min_score, half_life_days=90, scope="auto"):
        whole = (r["kind"], _whole(r["ref"]) if r["kind"] == "сообщение" else r["ref"])
        if r["kind"] not in skip and whole[1] not in seen and whole not in taken:
            rows.append(r)
            taken.add(whole)
    rows = rows[:k]
    if not rows:
        return ""
    foreign = _foreign(rows)
    if foreign and marks is not None:
        marks.append("архив памяти: чужой текст")
    return external("\n".join(f"- [{_label(r)}] {index.window(r['text'], text, 300)}" for r in rows), "архив памяти", own=not foreign)


# ---------- reconciling the index with its sources ----------
def sync_all():
    """Full reconciliation: memory and its archive, people, rules, skills, tasks, conversations, notes and entities; then mention
    links and embeddings for new records. The writing modules update the index themselves — reconciliation catches gaps and
    manual edits (memory, notes in Obsidian) and restores everything after the registry was set aside as broken. A source that
    fails is told in the journal and doesn't keep the ones after it out (people go before the texts that mention them)."""
    from sena.memory import history   # conversation history lives next door; imported here so memory tests don't drag it in at startup
    steps = [("память", facts.sync_index), ("люди", people.sync_index), ("правила", rules.sync_index), ("навыки", skills.sync_index),
             ("задачи", task_log.sync_index), ("заметки", vault.sync_index), ("файлы", files.sync_index), ("услышано", heard.sync_index),
             ("уроки", lessons.sync_index), ("цели", goals.sync_index), ("переписка", history.sync_index),
             ("связи", lambda: {"added": index.rebuild_links(), "removed": 0}),
             ("упоминания", lambda: {"added": index.relink_mentions(), "removed": 0})]   # names are registered now: every record gets its mention links
    report = []
    for name, step in steps:
        try:
            r = step()
            report.append(f"{name}: +{r['added']}/-{r['removed']}")
        except Exception as e:
            events.error(f"Сверка указателя памяти: {name}", e)
            report.append(f"{name}: ошибка")
    done = 0
    try:
        while True:
            n, _ = index.embed_pending(index.PENDING_BATCH)
            done += n
            if n < index.PENDING_BATCH:
                break
    except Exception as e:   # no embedding model right now: the queue waits for the next search or night
        events.error("Сверка указателя памяти: отпечатки", e)
    text = ", ".join(report) + f"; отпечатков посчитано: {done}"
    events.log("sync", "указатель памяти: " + text)
    return text


# ---------- find: the one door into everything ----------
MOST_QUERIES = 4   # phrasings of one question searched together: "how many / list all" needs several, one wording misses items
MOST_ROWS = 40     # the most finds the model may ask for at once (a count over a year of trips)


def _title(kind, ref):
    # a record as the owner names it, to tell two of one name apart
    if kind == "человек":
        return people.title(ref)
    return str(ref).rsplit("/", 1)[-1].removesuffix(".md")


def named(kind, name):
    """(ref, None) for the one record a name means; (None, why) when it names several — «Ольга» for two Olgas was taken as the
    first, and forget, find and tag acted on her; (None, None) when the name is unknown."""
    found = index.matches(kind, name)
    if len(found) == 1:
        return found[0], None
    if found:
        return None, f"Под «{name}» подходят: {', '.join(_title(kind, r) for r in found[:8])} — спроси Тему, кого (что) именно, и назови полнее"
    return None, None


def _linked(person, project, task):
    # (the record a search keeps to, or None; why it can't, or None)
    if person:
        pid, why = named("человек", person)
        if not pid:
            known = sorted({p.get("name") or (p["aliases"] or [""])[0] for p in people.all_people()} - {""})
            return None, why or f"Не знаю человека «{person}»; люди: {', '.join(known[:40]) or 'никого'}"
        return ("человек", pid), None
    if project:
        ref, why = named("проект", project)
        if not ref:
            return None, why or f"Не знаю проекта «{project}»; проекты: {', '.join(sorted(index.names.get('проект', {}))) or 'нет'}"
        return ("проект", ref), None
    return (("задача", task) if task else None), None


def find_rows(query="", period="all", kinds=None, person="", project="", task="", history=False, k=8, with_events=True, also=()):
    """Everything about a question as data: {"title", "rows" (registry records with links), "events", "error"}.
    period — as in costs.span; kinds — registry kinds; person/project — only what is linked to them; task — a task id;
    also — other phrasings of the same question: each is searched, the finds are merged, and a record found by several
    phrasings ranks higher (crewAI's memory tool takes a list of queries: items are described differently across talks)."""
    kinds = [k_ for k_ in (kinds or []) if k_]
    unknown = [k_ for k_ in kinds if k_ not in index.KINDS]
    if unknown:
        return {"error": f"Нет таких видов: {', '.join(unknown)}. Виды: {', '.join(index.KINDS)}"}
    try:
        since, until, title = costs.span(period or "all")
    except ValueError as e:
        return {"error": str(e)}
    linked, why = _linked(person, project, task)
    if why:
        return {"error": why}
    scope = "all" if kinds or linked else "find"   # named a kind or a person: nothing is held back
    history = history or bool(person)   # about a person, what used to be true counts too: "где раньше жила Ольга" (Graphiti)
    queries = list(dict.fromkeys(q.strip() for q in [query, *(also or ())] if q and q.strip()))[:MOST_QUERIES]
    if queries:
        merged = {}
        for q in queries:
            for r in index.search(q, kinds=kinds or None, k=k * 3, half_life_days=180, since=since, until=until, scope=scope, history=history, linked_to=linked):
                if not (r["words"] or r["cosine"] >= NOISE or r.get("valid_to")):   # no word matched and far off in meaning: noise, not a find
                    continue
                key = (r["kind"], _whole(r["ref"]) if r["kind"] == "сообщение" else r["ref"], r.get("valid_to"))   # one message, though found by two of its pieces
                if key in merged:
                    merged[key]["score"] += r["score"]
                else:
                    merged[key] = r
        rows = sorted(merged.values(), key=lambda r: -r["score"])[:k]
    else:   # a long message lies in pieces of one moment: it is one find, not one per piece taking the places of the others
        rows, taken = [], set()
        for r in index.browse(kinds=kinds or None, since=since, until=until, linked_to=linked, k=k * 3, scope=scope):
            key = (r["kind"], _whole(r["ref"]) if r["kind"] == "сообщение" else r["ref"])
            if key not in taken and len(rows) < k:
                taken.add(key)
                rows.append(r)
    index.expand(rows, per_row=3)
    for r in rows:   # a piece of a long message stands for the message: its own ref, not the piece's
        if r["kind"] == "сообщение":
            r["ref"] = _whole(r["ref"])
    out = {"title": title, "rows": rows, "events": []}
    if with_events and (queries or task) and not (person or project):   # the journal knows no people: "only about Olga" stays only about her
        seen = {}
        for q in queries or [""]:
            for e in _journal(period or "all", q, task):
                seen[(e["t"], e["kind"], e["text"])] = e
        out["events"] = sorted(seen.values(), key=lambda e: e["t"])[-(JOURNAL + 2 * max(0, len(queries) - 1)):]
    return out


JOURNAL = 6   # events of the journal one phrasing brings


def _journal(period, query, task):
    """The journal's events for one phrasing: its words as the registry compares them (no stop words, stems), all of them;
    when no event has them all, those with most of them. The journal itself wants every word of the query: «какой отель я
    нашла в Порту» found nothing, «отель Порту» found the event."""
    words = list(dict.fromkeys(index.stems(query)))
    if not words:   # a task's whole run, or a question of stop words only (which would match anything)
        return events.search(period, "", tid=task, limit=JOURNAL) if task or not query.strip() else []
    found = events.search(period, " ".join(words), tid=task, limit=JOURNAL)
    if found or len(words) < 2:
        return found
    hits = {}
    for w in words:
        for e in events.search(period, w, tid=task, limit=50):
            hits.setdefault((e["t"], e["kind"], e["text"]), [e, 0])[1] += 1
    need = max(2, (len(words) + 1) // 2)   # most of the words, never a single common one
    best = sorted((v for v in hits.values() if v[1] >= need), key=lambda v: (-v[1], -v[0]["t"]))[:JOURNAL]
    return [e for e, _ in best]


def _when(row):
    return f"{datetime.fromtimestamp(row['time']):%d.%m.%Y %H:%M}"


def find(query="", period="all", kinds=None, person="", project="", task="", history=False, also=None, limit=8):
    """The model's tool: the same, in words. also and limit are for counting and listing questions."""
    limit = clamp(limit or 8, 8, 1, MOST_ROWS)
    data = find_rows(query, period, kinds, person, project, task, history, k=limit, also=also or ())
    if data.get("error"):
        return data["error"]
    lines, size = [], 400 if limit <= 8 else 250   # a long list keeps each find shorter, so the whole list still fits
    words = " ".join([str(query or ""), *map(str, also or ())])   # each record is shown where these words are in it
    # someone else's words among what is shown mark the task: a file's text, a retold page, the journal's tool results
    foreign = _foreign(data["rows"]) or _journal_foreign(data["events"])
    for r in data["rows"]:
        line = f"- [{_label(r)} · {_when(r)}] {index.window(r['text'], words, size)}"
        if r.get("valid_to"):
            line += f" (было так до {datetime.fromtimestamp(r['valid_to']):%d.%m.%Y})"
        if r["kind"] == "сообщение" and not r.get("valid_to"):   # a line of a conversation with its other half: the reply, or what it answered
            asked = (r.get("meta") or {}).get("author") == "Сена"
            mate = index.beside("сообщение", r["ref"], r["time"], before=asked)
            if mate and mate["meta"].get("author") == ("Тема" if asked else "Сена"):
                line += f"\n    {'в ответ на' if asked else 'ответ'}: {index.window(mate['text'], words, 250)}"
                foreign = foreign or index.policy("сообщение", mate["meta"]).foreign
        if r.get("links"):
            line += "\n    связано: " + "; ".join(f"{l['kind']} «{l['text'][:80]}»" for l in r["links"])
            foreign = foreign or _foreign(r["links"])
        lines.append(line)
    if data["events"]:
        lines.append("Журнал событий:")
        lines += [f"- {datetime.fromtimestamp(e['t']):%d.%m %H:%M} {e['kind']}: {e['text'][:200]}" for e in data["events"]]
    if not lines:
        return f"Ничего не нашла ({data['title']})" if index.count() else "Архив пуст"
    return f"Нашла ({data['title']}):\n" + external("\n".join(lines), "память, заметки и журнал", own=not foreign)   # found text is data, never instructions


# ---------- tag: the owner says what something is about ----------
def _target(what):
    # what the owner points at: a file (path under data/ or absolute), a note (path in the vault), a task (id) -> (kind, ref)
    what = (what or "").strip()
    if not what:
        return None
    path = Path(what).expanduser()
    try:
        rel = str(path.resolve().relative_to(config.DATA.resolve()))
    except ValueError:
        rel = what
    if (config.DATA / rel).is_file() or rel in files.manifest():
        return ("файл", rel)
    note = what if what.endswith(".md") else what + ".md"
    if (vault.root() / note).is_file():
        return ("заметка", f"{note}#0")
    if any(e["id"] == what for e in task_log.items):
        return ("задача", what)
    return None


def tag(what, person="", project="", organization="", place=""):
    """The model's tool: relate a file, a note or a task to a person, project, organization or place the owner named."""
    target = _target(what)
    if not target:
        return Failure(f"Не нашла, что отмечать: «{what}». Нужен путь к файлу (из find или view_file), путь заметки (Проекты/X.md) или id задачи")
    done, missing, unclear = [], [], []
    for kind, name in (("человек", person), ("проект", project), ("организация", organization), ("место", place)):
        if not name:
            continue
        ref, why = named(kind, name)
        if why:   # two of that name: which one is the owner's to say
            unclear.append(why)
            continue
        if not ref:
            missing.append(f"{kind} «{name}»")
            continue
        index.link(target[0], target[1], kind, ref, "about")   # the owner's own tag can't be recomputed from anything: kept in links.jsonl
        done.append(f"{kind} {name}")
    if not done and not missing and not unclear:
        return "Назови, к кому или к чему это относится: person, project, organization или place"
    text = f"Отметила: {target[0]} {target[1].rsplit('#', 1)[0]} — {', '.join(done)}" if done else "Ничего не отметила"
    if missing:
        text += f". Не знаю: {', '.join(missing)} (карточку человека заведи через name_person без id, проект — заметкой в Проекты/)"
    if unclear:
        text += ". " + "; ".join(unclear)
    return text


TAG = tool("tag", "Отметить, к кому или к чему относится файл, заметка или задача — когда Тема так сказал («это для проекта X», «это от Ольги»). "
                  "Связь остаётся навсегда, по ней потом ищет find.",
           {"what": string("путь к файлу (как показал find или view_file), путь заметки (Проекты/X.md) или id задачи"),
            "person": string("имя человека"), "project": string("название проекта"), "organization": string("название организации"),
            "place": string("название места")}, ["what"])

FIND = tool("find",
            "Найти в памяти всё по вопросу: факты, людей, переписку, прошлые задачи с замечаниями Темы, заметки, навыки, файлы и журнал "
            "событий — по смыслу и словам, с отбором по времени, человеку, проекту или задаче; у каждой находки видно, с чем она связана. "
            "Для «что я говорил про…», «когда…», «как мы делали…», «где файл…», «что было в марте», «что ты делала вчера». "
            "history — как запись выглядела раньше. Услышанное вокруг Темы попадает только если назвать вид «услышано» или человека. "
            "Не отвечай «не помню», пока не поискала. «Сколько раз», «все», «перечисли», «как часто»: одна формулировка находит не всё — "
            "дай also (2–3 других слова для того же) и limit побольше, перечисли найденное по одному и только потом считай.",
            {"query": string("что ищем, своими словами; можно пусто, если задан человек, проект, задача или период"),
             "also": strings("другие формулировки того же вопроса (до трёх) — словами, которые могли стоять в самой записи, а не в вопросе: "
                             "«поездка в Порту», «летали в Португалию», «билеты Лиссабон»"),
             "limit": {"type": "integer", "description": f"сколько находок вернуть: по умолчанию 8, для подсчёта и списков до {MOST_ROWS}"},
             "period": string("today, yesterday, week, month, last_month, all, ГГГГ-ММ, ГГГГ-ММ-ДД или ГГГГ-ММ-ДД..ГГГГ-ММ-ДД; по умолчанию all"),
             "kinds": strings("какие виды записей смотреть: " + ", ".join(index.KINDS)),
             "person": string("имя человека: только то, что о нём и с ним связано"),
             "project": string("название проекта из заметок Obsidian (Проекты/): только связанное с ним; проекты из Luma здесь не ищутся"),
             "task": string("id задачи: её ход по журналу и всё, что к ней относится"),
             "history": {"type": "boolean", "description": "искать и в прежних версиях записей — что было раньше"}},
            ["query"])
TOOLS = [FIND, TAG]
FUNCTIONS = {"find": find, "tag": tag}
