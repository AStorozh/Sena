"""Tema's rules: standing instructions in his own words — how to do tasks ("when I ask for a website, gather requirements first")
and what to do on a schedule ("wake me up every day at 8", "send news every morning at 9", "wake me tomorrow at 8").
Rules without a time always sit in every agent's prompt (procedural memory). The time and recurrence in a rule
get translated once into scheduler jobs tied to the rule — by code (core/when.py) when the time is clear, by the planner's model
(role "rules") only when it isn't; a rule with no time at all calls nothing. Delete the rule and the jobs go with it;
a one-off rule ("tomorrow at 8") disappears by itself once its job has fired. A rule can be switched off without
deleting it (toggle): the text stays, it temporarily leaves the prompt and the schedule; turning it back on restores it.
File data/memory/rules.json; a read-only mirror lives as the "Memory/Rules" note in the vault."""
import threading
import time
import uuid
from datetime import datetime

from sena import config, scheduler
from sena.core import confirm, costs, events, power, roles, safety, status, store, when
from sena.core.tools import string, tool
from sena.memory import index

KIND = "правило"
_lock = threading.Lock()
_planning = set()   # rules whose schedule is being made right now: a second planner of the same rule added its jobs twice
REPLAN_EVERY = 600   # s: rules whose schedule the planner's model couldn't make are tried again at most this often
_replanned = 0.0
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
REPEAT_RU = {"once": "разово", "daily": "каждый день", "weekdays": "по будням", "weekly": "каждую неделю"}
PLAN = """Ты планировщик Сены. Тема записал правило — постоянное указание своими словами; код не смог уверенно прочитать в нём время.
Если в правиле есть конкретное время или периодичность («каждый день в 8», «по будням», «завтра в 9», «каждое утро»), верни jobs:
text — что Сене сделать в это время, как поручение самой себе («Разбудить Тему: написать в Телеграм несколько сообщений подряд»,
«Прислать Теме сводку главных новостей»); at — ближайший срок в формате ДД.ММ.ГГГГ ЧЧ:ММ; repeat — once для разового,
daily, weekdays или weekly для повторов. Время словами: «утром» — 9:00, «днём» — 13:00, «вечером» — 20:00; ночью не планируй.
Несколько времён — несколько записей. Это условие («если…», «когда…»), а не расписание, или времени нет — jobs пустой.
note — одна короткая строка: что запланировано (с выбранным часом) или «без расписания»."""
PLAN_SCHEMA = {"type": "object", "properties": {
    "jobs": {"type": "array", "maxItems": 6, "items": {"type": "object", "properties": {
        "text": {"type": "string"}, "at": {"type": "string"}, "repeat": {"type": "string", "enum": ["once", "daily", "weekdays", "weekly"]}},
        "required": ["text", "at", "repeat"]}},
    "note": {"type": "string"}}, "required": ["jobs", "note"]}


def all_rules():
    return store.load_json(config.RULES_FILE, [])


def _save(rules):
    store.save_versioned(config.RULES_FILE, rules, indent=1)


def by_id(rule_id):
    return next((r for r in all_rules() if r["id"] == rule_id), None)


def _meta(rule):
    # a switched-off rule stays findable, but the registry no longer recalls it into replies (index.KINDS)
    return None if rule.get("enabled", True) else {"enabled": False}


def _index(rule):
    index.put(KIND, rule["id"], rule["text"], rule["created"], meta=_meta(rule))


def sync_index():
    return index.sync(KIND, [(r["id"], r["text"], r["created"], _meta(r)) for r in all_rules()])


def add(text):
    """New rule: record it right away; a time the code reads goes into the schedule at once (the answer can say what it
    became), one that needs the planner's model — in the background. A rule written by a task that had read someone else's
    text keeps that mark: its jobs start marked (scheduler.add), as a job scheduled from there does."""
    text = " ".join(text.split())
    if len(text) < 3:
        return "Слишком коротко: напиши правило словами"
    rule = {"id": uuid.uuid4().hex[:6], "text": text, "created": time.time(), "jobs": [], "once": False, "planned": False, "note": "", "enabled": True}
    outside = status.outside()
    if outside:
        rule["outside"] = str(outside)[:300]
    with _lock:
        rules = all_rules()
        rules.append(rule)
        _save(rules)
    _index(rule)
    events.log("rule", text, rule=rule["id"])
    _plan_soon(rule["id"])
    return by_id(rule["id"]) or rule   # if planning already finished, the rule comes back with its jobs attached


def _plan_soon(rule_id):
    # the code's reading of the time at once; the planner's model, if the code can't, in the background
    if plan(rule_id, model=False) is None:
        safety.background(plan, rule_id, what="Расписание из правила")


def remove(id):
    """Delete a rule together with its jobs in the schedule."""
    with _lock:
        rules = all_rules()
        rule = next((r for r in rules if r["id"] == id), None)
        if not rule:
            return "Такого правила нет"
        rules.remove(rule)
        _save(rules)
    for job_id in rule["jobs"]:
        scheduler.unschedule(job_id)
    index.drop(KIND, id)
    events.log("rule", f"удалено: {rule['text']}", rule=id)
    return f"Правило удалено: {rule['text']}"


def toggle(rule_id, enabled):
    """Turn a rule off without losing its text: it leaves the agents' memory, and timed jobs are pulled from the schedule.
    Turn it on and it's back; if it had a time, the scheduler lays the jobs out again (from the current date)."""
    rule = by_id(rule_id)
    if not rule:
        return "Такого правила нет"
    if rule.get("enabled", True) == enabled:
        return rule
    if not enabled:
        for job_id in rule["jobs"]:
            scheduler.unschedule(job_id)
        kept = _update(rule_id, enabled=False, jobs=[])
    else:
        _update(rule_id, enabled=True, planned=False)
        _plan_soon(rule_id)
        kept = by_id(rule_id) or rule   # the background task may have already run (like add) — return the fresh state
    fresh = by_id(rule_id)
    if fresh:   # off: no longer recalled into replies; on: recalled again
        _index(fresh)
    events.log("rule", f"{'включено' if enabled else 'выключено'}: {rule['text']}", rule=rule_id)
    return kept


def _update(rule_id, **fields):
    with _lock:
        rules = all_rules()
        for r in rules:
            if r["id"] == rule_id:
                r.update(fields)
                _save(rules)
                return r


def plan(rule_id, model=True):
    """Read the time in a rule and put jobs in the schedule, tied to the rule: code first (core/when.py), the planner's model
    only when the code isn't sure; a rule without any time calls nothing. model=False — only what the code reads: None, with
    nothing changed, when the time needs the model. A planner that didn't answer (Sena off, the cloud out) leaves the rule
    waiting (planned False): out of the prompt, planned again by replan() — it was kept as a standing rule for good, and
    «напоминай про отчёт каждую пятницу после обеда» never fired."""
    with _lock:
        if rule_id in _planning:
            return None
        _planning.add(rule_id)
    try:
        return _plan(rule_id, model)
    finally:
        with _lock:
            _planning.discard(rule_id)


def _when_ru(job):
    return f"{job['at']}, {REPEAT_RU.get(job['repeat'], job['repeat'])}"


def _plan(rule_id, model):
    rule = by_id(rule_id)
    if not rule:
        return None
    now = datetime.now()
    found = when.parse(rule["text"], now)
    if found == [] and not model:
        return None
    made, errors = [], []

    def schedule(text, at, repeat="once"):
        job, error = scheduler.add(text, at, repeat, outside=rule.get("outside"))
        if job:
            made.append(job)
        else:
            errors.append(error)

    if found is None:
        note = "без расписания"
    elif found:
        job_text = when.rest(rule["text"])
        for j in found:
            schedule(job_text, j["at"], j["repeat"])
        note = "запланировано: " + "; ".join(_when_ru(j) for j in found)
    else:
        try:
            with costs.label(f"правило: {rule['text'][:60]}"):
                reply = roles.ask("rules", PLAN + f"\nСейчас {now:%d.%m.%Y %H:%M}, {WEEKDAYS[now.weekday()]}.", rule["text"], PLAN_SCHEMA,
                                  purpose="время в правиле", max_tokens=400)
        except Exception as e:
            events.error("Расписание из правила", e)
            note = ("Сена выключена — расписание составлю, когда включишь" if power.is_off()
                    else "время ещё не разобрано: модель не ответила, попробую позже")
            _update(rule_id, planned=False, note=note)
            return note
        for j in (reply.get("jobs") or [])[:6] if isinstance(reply, dict) else []:
            if isinstance(j, dict):
                schedule(j.get("text") or when.rest(rule["text"]), j.get("at", ""), j.get("repeat") or "once")
        note = (reply.get("note") if isinstance(reply, dict) else "") or ("запланировано" if made else "без расписания")
    if errors and not made:
        note = f"{note}; не вышло: {errors[0]}"
    kept = _update(rule_id, jobs=[j["id"] for j in made], once=bool(made) and all(j["repeat"] == "once" for j in made), planned=True, note=note[:200])
    if kept is None or not kept.get("enabled", True):   # the rule was deleted or disabled while the scheduler was running: its jobs shouldn't exist either
        for j in made:
            scheduler.unschedule(j["id"])
        return note
    events.log("rule", f"расписание из правила: {note}", rule=rule_id, jobs=[j["id"] for j in made])
    return note


def tidy():
    """One-off rules whose jobs have already fired remove themselves (once a day and before the list is shown); rules still
    waiting for their schedule are planned again (replan)."""
    alive = {j["id"] for j in scheduler.jobs()}
    with _lock:
        rules = all_rules()
        gone = [r for r in rules if r["once"] and r["jobs"] and not any(j in alive for j in r["jobs"])]
        if gone:
            _save([r for r in rules if r not in gone])
    for r in gone:
        index.drop(KIND, r["id"])
        events.log("rule", f"разовое правило исполнено и убрано: {r['text']}", rule=r["id"])
    replan()
    return len(gone)


def replan():
    """Plan again, in the background, the rules whose planner's model didn't answer (Sena was off, the cloud out): at the core's
    start, at night and when the rules are shown — never while Sena is off, at most every REPLAN_EVERY. Returns how many."""
    global _replanned
    if power.is_off() or time.monotonic() - _replanned < REPLAN_EVERY:
        return 0
    waiting = [r["id"] for r in all_rules() if not r.get("planned", True) and r.get("enabled", True)]
    if not waiting:
        return 0
    _replanned = time.monotonic()
    safety.background(lambda: [plan(rule_id) for rule_id in waiting], what="Расписание из правил")
    return len(waiting)


def listing():
    """Standing rules for the agents' prompt. Timed rules live in the schedule — they have nothing to do in the prompt, and
    neither do rules still waiting for their schedule (they aren't standing ones). Disabled ones (Tema unchecked the box in the
    app) don't make it into the prompt, but their text isn't lost."""
    return "\n".join(f"- {r['text']}" for r in all_rules()
                     if r.get("enabled", True) and r.get("planned", True) and not r["once"] and not r["jobs"])


def rows():
    """Rules with their jobs as data (for the screen)."""
    tidy()
    jobs = {j["id"]: j for j in scheduler.jobs()}
    return [{**r, "jobs": [jobs[j] for j in r["jobs"] if j in jobs]} for r in reversed(all_rules())]


TOOLS = [
    tool("add_rule", "Записать постоянное правило Темы его словами: как делать такие-то задачи или что делать регулярно "
                     "(«когда прошу сайт — сначала собери требования», «буди меня каждый день в 8»). Время из правила попадёт в расписание само. "
                     "Это только текст в твоей собственной подсказке — его не увидит ночная проверка, напоминания и другой код, что работает "
                     "сам, без твоего хода. Если для просьбы уже есть настоящая настройка со своим инструментом (set_style, set_telegram_notify "
                     "и подобные) — используй её, а не это: она действует везде, а не только пока ты думаешь.",
         {"text": string("правило одной-двумя фразами")}, ["text"]),
    tool("remove_rule", "Удалить правило Темы по id из списка «Правила Темы» (вместе с его расписанием)", {"id": string()}, ["id"]),
]


def _told(made):
    # what add_rule answers: the time the rule became (the owner hears «каждый день в 09:00», not «через минуту появится»)
    if isinstance(made, str):
        return made
    if not made.get("planned") and not made.get("note"):
        return f"Правило записано [{made['id']}]. Время в нём разбираю: через минуту появится в расписании; не появилось — скажи Теме"
    return f"Правило записано [{made['id']}]: {made['note']}"


def add_rule(text):
    """The model's tool. In a task that had read someone else's text the rule waits for the owner's "yes": it would stand in
    every prompt as his own word, or run on the schedule — a mail that talks the model into a rule must not plant one."""
    source = status.outside()
    if source:
        shown = " ".join(str(text).split())[:120]
        sid = confirm.hold(f"записать правило «{shown[:80]}»", shown, lambda: _told(add(text)))
        return (f"Ещё не записано [{sid}]: в этой задаче был чужой текст ({source}). Спроси Тему, записать ли правило «{shown}»; "
                f"запишешь через confirm_send({sid}) только после его «да»")
    return _told(add(text))


FUNCTIONS = {"add_rule": add_rule, "remove_rule": remove}
