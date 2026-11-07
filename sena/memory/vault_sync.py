"""The automatic part of the vault: what Sena knows and does turns into Obsidian notes.
People, memory by section, skills, the task diary and the home page. Sena writes only between the block markers;
everything else in these notes is yours. Runs at startup, once an hour and at night; the diary updates after every task."""
import re
import threading
from datetime import date, datetime

from sena import config
from sena.core import events, store
from sena.core.tools import Failure
from sena.memory import facts, people, quality, rules, skills, task_log, vault

_lock = threading.RLock()   # sync_all holds the lock and calls write_day, which takes it again
USER_PART = "## Мои заметки\n\n"
OWNER_HINT = vault.HINT   # the first line of the automatic blocks of cards, memory and rules: edits inside them are overwritten


def owner_lines(path):
    """What the owner wrote in a card by hand: the lines after the sena:end marker — bullets and paragraphs, without headings,
    markers and the block's own hint (never his words, wherever a broken edit left it)."""
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    tail = text.rsplit(vault.END, 1)[1] if vault.END in text else ""   # after the last marker: a block written twice isn't his
    out = []
    for line in tail.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "<!--", "---", OWNER_HINT)):
            continue
        out.append(line[2:].strip() if line[:2] in ("- ", "* ") else line)
    return [l for l in out if l]


def _seen():
    return store.load_json(config.VAULT_SYNC_STATE, {"people": {}, "memory": {}})


def absorb_people():
    """The other direction: a line the owner added under a person's card becomes a fact (source vault);
    a line he removed retires the fact. Returns how many lines were taken in or retired."""
    state = _seen()
    seen, changed = state.setdefault("people", {}), 0
    known = {p["id"] for p in people.all_people()}
    for pid, path in _ids_in("Люди").items():
        if pid not in known:
            continue
        now = owner_lines(path)
        before = seen.get(pid, [])
        new = [l for l in now if l not in before]
        gone = [l for l in before if l not in now]
        if new:
            people.update(pid, facts=new, source="vault")
        for line in gone:
            people.retire_fact(pid, line, source="vault")
        changed += len(new) + len(gone)
        seen[pid] = now
    store.save_json(config.VAULT_SYNC_STATE, state, indent=1)
    return changed


def absorb_memory():
    """The same for memory sections: a line the owner wrote under Память/<section> goes into memory.md; a removed line leaves it.
    A line memory refuses (a relative date, a password) isn't taken as seen: it waits for his rewrite, and the reason stands in
    Sena's part of the note (refused) — it was dropped without a word, though the note says such a line goes into memory."""
    state = _seen()
    seen, refused, changed = state.setdefault("memory", {}), state.setdefault("refused", {}), 0
    for section in facts.SECTIONS:
        path = vault.root() / "Память" / f"{vault.clean_name(section)}.md"
        now = owner_lines(path)
        before, told, why = seen.get(section, []), refused.get(section, {}), {}
        known = {facts.plain(i) for s in facts.sections() if s["title"] == section for i in s["items"]}
        for line in now:
            if line in before or line in known:
                continue
            reply = told.get(line) or facts.remember(section, line)   # told once already: tried again only when he rewrites it
            if line in told or isinstance(reply, Failure):
                why[line] = str(reply)
            else:
                changed += 1
        for line in before:
            if line not in now and facts.drop_exact(section, line):
                changed += 1
        seen[section], refused[section] = [l for l in now if l not in why], why
    store.save_json(config.VAULT_SYNC_STATE, state, indent=1)
    return changed


def _today():
    return date.today().isoformat()


def _flat(text):
    return re.sub(r"\s+", " ", text).strip()


def _ids_in(folder):
    # sena_id -> file: used to find the card again even after the person was renamed
    found = {}
    for path in (vault.root() / folder).glob("*.md"):
        m = re.search(r'^sena_id:\s*"?([\w-]+)"?', path.read_text(encoding="utf-8", errors="replace")[:800], re.M)   # one odd card mustn't stop the listing
        if m:
            found[m.group(1)] = path
    return found


def sync_people():
    vault.ensure()
    absorbed = absorb_people()   # first what the owner wrote by hand, then the cards
    existing = _ids_in("Люди")
    known, changed = set(), 0
    for p in people.all_people():
        names = [n for n in [p.get("name")] + p["aliases"] if n]
        if not names and not people.facts(p):
            continue   # a voice without a name and without facts doesn't need a card
        known.add(p["id"])
        try:
            changed += _card(p, names, existing.get(p["id"]))
        except (OSError, UnicodeError) as e:   # one odd card (not UTF-8, moved away meanwhile) doesn't stop the others
            events.error(f"Хранилище: карточка {names[0] if names else p['id']}", e)
    for pid, path in existing.items():   # the person was merged or deleted: the card moves to the archive instead of disappearing
        if pid not in known:
            target = vault.root() / "Архив" / f"Человек - {path.name}"
            if not target.exists():
                path.rename(target)
                changed += 1
    return changed + absorbed


def _card(p, names, current):
    # one person's card: found by its id even after a rename, its name kept free for another person, only Sena's block rewritten
    title = vault.clean_name(names[0]) if names else f"Голос {p['id'][:4]}"
    wanted = vault.root() / "Люди" / f"{title}.md"
    if current != wanted and wanted.exists() and wanted not in (current,):   # that name is taken by another person
        title = f"{title} ({p['id'][:4]})"
        wanted = vault.root() / "Люди" / f"{title}.md"
    if current and current != wanted and not wanted.exists():
        current.rename(wanted)
    known_facts = people.facts(p, exclude=("vault",))   # lines from the owner's own part of the card stay there, not repeated in the block
    lines = [OWNER_HINT]
    if p.get("name"):
        lines.append(f"**Имя:** {p['name']}" + (f" (также: {', '.join(p['aliases'])})" if p["aliases"] else ""))
    elif p["aliases"]:
        lines.append("**Известен как:** " + ", ".join(p["aliases"]))
    if p["relationship"]:
        lines.append(f"**Отношения с Темой:** {p['relationship']}")
    if known_facts:
        lines.append("**Что известно:**\n" + "\n".join(f"- {f}" for f in known_facts))
    if p["opinion"]:
        lines.append(f"**Моё мнение:** {p['opinion']}")
    voices = len(p["voices"])
    lines.append(f"Голос: {'запомнен, образцов ' + str(voices) if voices else 'не запомнен'}. "
                 f"Последний раз: {datetime.fromtimestamp(p['last_seen']):%d.%m.%Y}.")
    return vault.upsert(f"Люди/{title}", {"type": "person", "sena_id": p["id"], "aliases": names, "tags": ["человек"],
                                          "updated": _today()}, title, "\n\n".join(lines), USER_PART)


def sync_memory():
    vault.ensure()
    changed = absorb_memory()   # first the owner's own lines, then the sections
    refused = _seen().get("refused", {})
    for section in facts.sections():
        own = set(owner_lines(vault.root() / "Память" / f"{vault.clean_name(section['title'])}.md"))
        body = "\n".join(f"- {item}" for item in section["items"] if facts.plain(item) not in own) or "(пока пусто)"   # the owner's lines stay in his part, not repeated
        told = refused.get(section["title"], {})
        if told:
            body += "\n\nНе взяла в память (поправь строку ниже — возьму):\n" + "\n".join(f"- «{line}»: {why}" for line, why in told.items())
        block = (OWNER_HINT + " Автоматически из памяти Сены: это то, что она держит в голове постоянно. "
                 "Факты меняются через неё (в приложении: «База»); строка, дописанная ниже, тоже попадёт в память.\n\n" + body)
        changed += vault.upsert(f"Память/{vault.clean_name(section['title'])}", {"type": "memory", "tags": ["память"], "updated": _today()},
                                section["title"], block, USER_PART)
    return changed


def sync_rules():
    vault.ensure()
    lines = []
    for r in rules.all_rules():
        lines.append(f"- {r['text']}" + (" (разовое)" if r["once"] else "") + (f" — {r['note']}" if r.get("note") and r["note"] != "без расписания" else ""))
    block = (OWNER_HINT + " Автоматически из правил Темы: постоянные указания Сене и расписание из них. Меняются в приложении («Правила»).\n\n"
             + ("\n".join(lines) or "(пока пусто)"))
    return vault.upsert("Память/Правила", {"type": "memory", "tags": ["память", "правила"], "updated": _today()}, "Правила", block, USER_PART)


def sync_skills():
    vault.ensure()
    changed = 0
    for name in skills.names():
        text = skills.text(name)
        when, _, rest = text.partition("\n")
        code = ""
        if "\nКод (запускается без модели):\n" in "\n" + rest:
            rest, _, code = ("\n" + rest).partition("\nКод (запускается без модели):\n")
        block = f"**Когда применять:** {when}\n\n{rest.strip()}" + (f"\n\n```python\n{code.strip()}\n```" if code.strip() else "")
        changed += vault.upsert(f"Навыки/{name}", {"type": "skill", "tags": ["навык"], "updated": _today()}, name, block, USER_PART)
    return changed


def write_day(day=None):
    """Diary for the day: what tasks there were, who did them, how they ended."""
    with _lock:
        return _write_day(day)


def _write_day(day=None):
    vault.ensure()
    day = day or date.today()
    rows = [e for e in task_log.items if datetime.fromtimestamp(e["start"]).date() == day]
    if not rows:
        return 0
    counts = {}
    lines = []
    for e in sorted(rows, key=lambda x: x["start"]):
        verdict = quality.VERDICT_RU.get((e.get("outcome") or {}).get("verdict"), "без оценки")
        counts[verdict] = counts.get(verdict, 0) + 1
        who = ", ".join(dict.fromkeys(s["who"] for s in e["steps"] if s["who"] != "ag_sena"))
        lines.append(f"- {datetime.fromtimestamp(e['start']):%H:%M} «{_flat(e['text'])[:140]}»"
                     f"{' (' + who + ')' if who else ''} → {_flat(e['answer'])[:200]} [{verdict}]"
                     + "".join(f"\n  - замечание Темы: {c['text']}" for c in e["comments"]))
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    changed = vault.upsert(f"Дневник/{day.isoformat()}", {"type": "daily", "date": day.isoformat(), "tags": ["дневник"], "updated": _today()},
                           day.isoformat(), f"**Задач: {len(rows)}** ({summary})\n\n" + "\n".join(lines), USER_PART)
    events.log("sync", f"дневник {day.isoformat()}: {'записан' if changed else 'без изменений'} ({len(rows)} задач)", source="vault")
    return changed


def write_home():
    vault.ensure()
    block = ("Хранилище Сены. Автоматически здесь: **Люди**, **Память**, **Навыки**, **Дневник**; "
             "свободные заметки Сена ведёт в **Проекты**, **Знания**, **Идеи**, **Входящие**.\n\n"
             "- Люди: карточка на человека (факты, отношения, голос)\n"
             "- Память: то, что Сена держит в голове постоянно\n"
             "- Навыки: рабочие способы, которым она научилась, и их код\n"
             "- Дневник: задачи по дням с оценкой результата\n"
             "- Проекты, Знания, Идеи: записи Сены по твоим просьбам и из её работы\n\n"
             f"Людей: {len(people.all_people())}, навыков: {len(skills.names())}, задач в журнале: {len(task_log.items)}. "
             f"Обновлено: {datetime.now():%d.%m.%Y %H:%M}.")
    return vault.upsert("Сена", {"type": "home", "tags": ["сена"], "updated": _today()}, "Сена", block, USER_PART)


_synced_sign = None   # which files the last reconciliation was based on


def sync_if_changed(days=2):
    """Reconcile the vault, but only if memory, people, skills or rules changed since last time (hourly, from the background check)."""
    global _synced_sign
    files = [config.MEMORY_FILE, config.PEOPLE_FILE, config.RULES_FILE, *sorted(config.SKILLS_DIR.glob("*"))]
    for folder in ("Люди", "Память"):   # the owner's own edits in the cards count as changes too: they flow back
        files += sorted((vault.root() / folder).glob("*.md")) if (vault.root() / folder).is_dir() else []
    sign = tuple((str(p), store.stamp(p)) for p in files)   # people and rules are records: the store tells their change as for a file
    if sign == _synced_sign:
        return "без изменений"
    _synced_sign = sign
    return sync_all(days)


def sync_all(days=2):
    """Everything automatic in one pass. Returns a short summary; a failure in one part doesn't block the rest."""
    with _lock:
        report = []
        steps = [("люди", sync_people), ("память", sync_memory), ("правила", sync_rules), ("навыки", sync_skills), ("главная", write_home)]
        steps += [(f"дневник {i}", lambda i=i: write_day(date.fromordinal(date.today().toordinal() - i))) for i in range(days)]
        for name, step in steps:
            try:
                n = step()
                if n:
                    report.append(f"{name}: {int(n)}")
            except Exception as e:
                report.append(f"{name}: ошибка {e}")
                events.error(f"Хранилище: {name}", e)
        events.log("sync", ", ".join(report) or "без изменений", source="vault")
        return ", ".join(report) or "без изменений"
