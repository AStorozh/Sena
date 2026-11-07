"""Long work: a job too big for one go — a site from requirements to deploy, the whole mailbox put in order, a book read
through, a research — pursued turn after turn until it is done and proven, needs the owner, or runs out of its budget.

A work has a goal in the owner's words, a done-when (how to check it is done), limits, a plan, and a checkpoint: the
report of the last turn (done with facts / in progress / in the way / decisions / next), so every turn starts with a fresh
mind and the whole story. One turn is a full coordinator turn (30 steps, every tool and agent, the same confirmations).
After it a strict judge (the fast model) decides: done — only on a concrete fact that meets done-when; continue; wait (for
something that runs on its own); or the owner is needed. The model may claim done or ask the owner, never pause or drop the
work: pause, resume and stop are the owner's. The owner hears only what matters: done, a question, the budget spent, a
failure — not every turn. A turn cut by a restart is resumed with a note to check what it managed before repeating it.
A work for one of the owner's projects keeps that project in Luma, his own app: the project's status follows the work and
every plan step is a card of it with the step's status (luma_sync).

Designed after Hermes Agent (/goal: completion contract, judge verdicts done/blocked/continue/wait, "done needs the
deliverable to exist") and OpenClaw (session goals: owner-only pause and resume, budget, restart recovery), both MIT."""
import json
import threading
import time
import uuid
from datetime import datetime, timedelta

from sena import config
from sena.core import cancel, confirm, costs, events, llm, moment, power, safety, settings, status, store
from sena.core.tools import Failure, clamp, string, tool
from sena.world import luma

ACTIVE, WAITING, PAUSED, LIMITED, DONE, STOPPED = "active", "waiting", "paused", "limited", "done", "stopped"
JUDGE_BROKEN = 3   # turns in a row the judge didn't answer before the owner is told once
ASK_HOLD = 36 * 3600   # s: what a work prepared waits this long for the owner's "yes" when the work asks him (Dify's 36 hours)
WAIT_REMIND = 86400    # s a work waits for the owner before he is reminded once
WAIT_PAUSE = 7 * 86400   # and before it is paused
STATUSES = {ACTIVE: "в работе", WAITING: "ждёт Тему", PAUSED: "на паузе", LIMITED: "кончился бюджет", DONE: "сделана", STOPPED: "остановлена"}
VERDICTS = {"done": "готово", "continue": "дальше", "wait": "ждём", "need_owner": "нужен Тема"}   # the judge's word in the log he reads
MAX_TURNS, BUDGET = 12, 3.0        # a work's default limits unless the owner names his own
MOST_TURNS, MOST_BUDGET = 200, 100.0
AT_ONCE = 2                        # works stepping at the same time; the rest wait their turn
FAILED_TURNS = 3                   # turns in a row that broke (no network, no money) before the work pauses and says so
IDLE_REPLAN, IDLE_ASK = 2, 3       # turns in a row without a result: a new plan is asked for / the owner is asked what is in the way
                                   # (crewAI re-plans after two failed steps; reading and searching are not progress, its observer says)
CHECKPOINT = 6000                  # characters of a turn's report kept as the checkpoint
STEP_STATES = ("todo", "doing", "done", "skipped")
# the work's project in Luma: Luma has no "blocked", a work that waits is a project on hold; a step dropped from the plan
# leaves its card cancelled, never deleted (the owner may have written on it)
PROJECT_STATUS = {ACTIVE: "active", WAITING: "on_hold", PAUSED: "on_hold", LIMITED: "on_hold", DONE: "done", STOPPED: "dropped"}
CARD_STATUS = {"todo": "todo", "doing": "doing", "done": "done", "skipped": "cancelled"}
CARD_TITLE = 200                   # Luma's longest title
LUMA_BATCH = 20                    # cards per call (Luma's limit)
LUMA_RETRY = 300                   # s before a project Luma didn't take is sent again; a closed Luma is waited for, never opened
LUMA_FAILS = 5                     # failures in a row before the work stops keeping its project and the owner is told

_lock = threading.Lock()
_running = set()                   # ids of works with a turn going right now (this process)
_runner = None                     # runs one turn: runner(work) -> (answer, task id, outcome ok|cut|failed|day); the coordinator (agent.work_turn)
_notify = None                     # tells the owner: notify(text)
_syncing = set()                   # ids of works whose project is being sent to Luma right now


def _all():
    # every record as stored, the unreadable ones too: what is saved back keeps them as they were
    items = store.load_json(config.WORKS_FILE, [])
    return items if isinstance(items, list) else []


# what the engine reads in every record, and of which kind
FIELDS = {"id": str, "title": str, "status": str, "turns": int, "max_turns": int, "budget": (int, float), "created": (int, float), "tasks": list}


def _readable(w):
    """A record the engine can step and show. One edited by hand or left by another version (a field gone or of another kind)
    is passed over — named once, at start (resume_after_restart) — instead of breaking every work, the coordinator's context of
    every message and the apps' panel with it."""
    if not isinstance(w, dict) or not all(isinstance(w.get(k), kind) for k, kind in FIELDS.items()) or w["status"] not in STATUSES:
        return False
    plan, kept, p = w.get("plan") or [], [w.get("log") or [], w.get("notes") or []], w.get("luma")
    return (isinstance(plan, list) and all(isinstance(s, dict) and isinstance(s.get("text"), str) and s.get("status") in STEP_STATES for s in plan)
            and all(isinstance(rows, list) and all(isinstance(r, dict) and "t" in r and "text" in r for r in rows) for rows in kept)
            and (p is None or (isinstance(p, dict) and isinstance(p.get("project"), str))))


def _works():
    return [w for w in _all() if _readable(w)]


def _save(items):
    store.save_versioned(config.WORKS_FILE, items, indent=1)


def _find(items, id):
    return next((w for w in items if _readable(w) and w["id"] == id), None)


def _clean(text, most=2000):
    return " ".join(safety.visible(text or "").split())[:most]


def get(id):
    return _find(_all(), id)


def all_works():
    return _works()


def _change(id, apply):
    """Change one work under the lock: apply(work) edits it and returns the reply; None — no such work."""
    with _lock:
        items = _all()
        w = _find(items, id)
        if not w:
            return None
        reply = apply(w)
        w["updated"] = moment.now()
        _save(items)
    return reply


def _log(w, text):
    w["log"] = (w.get("log", []) + [{"t": moment.now(), "text": _clean(text, 400)}])[-100:]


def spent(w):
    """Dollars the work's turns cost so far (the cost journal, by their task ids)."""
    return round(sum(costs.by_task(w.get("tasks", []), since=w["created"] - 60).values()), 4) if w.get("tasks") else 0.0


def plan_line(w):
    steps = w.get("plan", [])
    done = sum(s["status"] in ("done", "skipped") for s in steps)
    return f"{done} из {len(steps)}" if steps else "плана нет"


def progress(w):
    steps = w.get("plan", [])
    return 100 if w["status"] == DONE else round(100 * sum(s["status"] in ("done", "skipped") for s in steps) / len(steps)) if steps else 0


# ---------- the model's tools ----------
def start(title, goal, done_when, plan=None, limits="", budget_usd=None, max_turns=None, project=""):
    """A new long work; its first turn starts within half a minute, in the background. project — the owner's project in Luma
    the work is for: the work keeps it there (luma_sync). Started by a task that had read someone else's text (a mail, a page),
    the work keeps that mark: each of its turns starts marked (turn_began), or a mail could have a work run the terminal or
    Claude Code with no "yes" of his (the audit's "laundering" through a work)."""
    title, goal, done_when, project = _clean(title, 120), _clean(goal, 3000), _clean(done_when, 1000), _clean(project, CARD_TITLE)
    if len(title) < 3 or len(goal) < 10:
        return Failure("Назови работу коротко и опиши цель словами Темы")
    if len(done_when) < 5:
        return Failure("Нужно условие готовности: проверяемый факт, по которому видно, что работа сделана (адрес сайта открывается, тесты прошли, "
                       "писем во «Входящих» меньше 50)")
    now, outside = moment.now(), status.outside()
    w = {"id": uuid.uuid4().hex[:6], "title": title, "goal": goal, "done_when": done_when, "limits": _clean(limits, 1000),
         "plan": [{"text": _clean(s, 300), "status": "todo"} for s in (plan or []) if str(s).strip()][:50],
         "status": ACTIVE, "turns": 0, "max_turns": clamp(max_turns or MAX_TURNS, MAX_TURNS, 1, MOST_TURNS),
         "budget": max(0.1, min(float(budget_usd or BUDGET), MOST_BUDGET)), "checkpoint": "", "notes": [], "log": [], "tasks": [],
         "claim": None, "question": "", "next_at": now, "running": None, "fails": 0, "created": now, "updated": now, "tz": moment.offset(now),
         **({"outside": outside} if outside else {}),
         **({"luma": {"project": project, "id": None, "cards": {}, "shown": None, "fails": 0, "next_try": 0}} if project else {})}
    with _lock:
        items = _all()
        items.append(w)
        _save(items)
    events.log("work", f"новая долгая работа: {title}", id=w["id"], budget=w["budget"], turns=w["max_turns"], project=project or None)
    luma_said = ""
    if project:
        luma_said = (f"Проект «{project}» в Luma работа ведёт сама: статус и шаги плана карточками" +
                     (" — когда Тема включит Luma в настройках интеграций: сейчас она выключена. " if not settings.get("use_luma") else
                      "; закрытую Luma сама не открывает — заведёт проект, когда Тема её откроет. "))
    return (f"Работа заведена [{w['id']}] «{title}»: первый ход начнётся в фоне через несколько секунд; дальше ходы идут сами, "
            f"пока цель не будет проверена по условию готовности. Бюджет: {w['budget']} $ и до {w['max_turns']} ходов. " + luma_said
            + (f"В этой задаче был чужой текст ({outside}): поручения компьютеру и Claude Code в ходах работы будут ждать «да» Темы. " if outside else "")
            + "Скажи Теме коротко: что будешь делать, план, бюджет; напишешь, когда будет готово или понадобится он.")


def set_plan(id, steps):
    """The whole plan at once (the old one is replaced): [{"text", "status", "fact"}], at most one step in progress. A step
    done or skipped stays: a new plan that drops one is refused with its name (MetaGPT keeps the finished prefix of a plan;
    a re-plan here erased finished steps and progress fell from 67 % to 0), and a done step keeps its fact (CAMEL hands a
    finished part's result to the parts after it — a path found in turn 1 was gone by turn 3)."""
    rows = []
    for s in steps or []:
        text = _clean(s.get("text") if isinstance(s, dict) else s, 300)
        state = (s.get("status") if isinstance(s, dict) else "todo") or "todo"
        fact = _clean(s.get("fact"), 300) if isinstance(s, dict) else ""
        if text:
            rows.append({"text": text, "status": state if state in STEP_STATES else "todo", **({"fact": fact} if fact else {})})
    doing = [r for r in rows if r["status"] == "doing"]
    for r in doing[1:]:
        r["status"] = "todo"   # one step at a time is "in progress"; the others wait
    if not rows:
        return Failure("План пуст: нужен хотя бы один шаг")

    def apply(w):
        old = {s["text"]: s for s in w.get("plan", [])}
        new = {r["text"] for r in rows}
        lost = [t for t, s in old.items() if s["status"] in ("done", "skipped") and t not in new]
        if lost:
            return Failure("План не принят: в нём нет уже сделанных шагов — " + "; ".join(f"«{t[:80]}»" for t in lost[:5])
                           + ". Оставь их как есть (со статусом done или skipped) и пришли план целиком.")
        for r in rows:   # a finished step keeps its fact when the plan is resent without it
            if r["text"] in old and old[r["text"]].get("fact") and not r.get("fact"):
                r["fact"] = old[r["text"]]["fact"]
        w["plan"] = rows[:50]
        return f"План работы «{w['title']}»: {plan_line(w)} сделано"
    return _change(id, apply) or Failure(f"Работы {id} нет")


def claim_done(id, evidence):
    """The model says the work is done; the judge checks the claim against done-when after the turn."""
    evidence = _clean(evidence, 2000)
    if len(evidence) < 5:
        return Failure("Нужен факт, который доказывает готовность: адрес, путь к файлу, вывод проверки, число")

    def apply(w):
        w["claim"] = evidence
        return "Принято: после хода проверю готовность по условию. Закончи ход итогом."
    return _change(id, apply) or Failure(f"Работы {id} нет")


def need_owner(id, question):
    """The model can't go on without the owner: the question goes to him after the turn, the work waits for his answer."""
    question = _clean(question, 1000)
    if len(question) < 5:
        return Failure("Сформулируй один вопрос Теме: что именно от него нужно")

    def apply(w):
        w["question"] = question
        return "Вопрос уйдёт Теме после этого хода; работа подождёт его ответа. Закончи ход итогом."
    return _change(id, apply) or Failure(f"Работы {id} нет")


def control(id, action, note="", budget_usd=None, max_turns=None):
    """What the owner asked about a work: pause, resume (with his answer or more budget), stop, or a note for the next turns."""
    note = _clean(note, 1500)

    def apply(w):
        if action == "note":
            if not note:
                return Failure("Пустая заметка")
            w["notes"] = (w.get("notes", []) + [{"t": moment.now(), "text": note}])[-30:]
            _log(w, f"Тема: {note}")
            return f"Записала в работу «{w['title']}»: следующие ходы это учтут"
        if action == "pause":
            if w["status"] in (DONE, STOPPED):
                return Failure(f"Работа уже {STATUSES[w['status']]}")
            w["status"] = PAUSED
            _log(w, "пауза по слову Темы")
            return f"Работа «{w['title']}» на паузе (ход, если идёт, доработает)"
        if action == "stop":
            w["status"] = STOPPED
            _log(w, "остановлена по слову Темы")
            if w.get("running") and w.get("tasks"):
                cancel.stop_task(w["tasks"][-1])   # the turn going now stops too, not only the turns after it (one still starting: turn_began)
            return f"Работа «{w['title']}» остановлена; всё сделанное остаётся"
        if action == "resume":
            if w["status"] == DONE and not note:
                return Failure("Работа уже сделана: чтобы продолжить, скажи, что ещё нужно (note)")
            budget = round(min(spent(w) + float(budget_usd), MOST_BUDGET), 2) if budget_usd else w["budget"]   # "ещё N долларов" on top of what's spent
            if spent(w) >= budget:   # resuming would only stop it again at once
                return Failure(f"Бюджет работы «{w['title']}» исчерпан ({spent(w)} из {w['budget']} $): спроси Тему, сколько ещё можно потратить, и передай budget_usd")
            w["budget"] = budget
            if max_turns:
                w["max_turns"] = min(w["turns"] + clamp(max_turns, MAX_TURNS, 1, MOST_TURNS), MOST_TURNS)
            elif w["turns"] >= w["max_turns"]:   # "go on" when the turns ran out: as many turns again as a new work gets
                w["max_turns"] = min(w["turns"] + MAX_TURNS, MOST_TURNS)
            if note:
                w["notes"] = (w.get("notes", []) + [{"t": moment.now(), "text": note}])[-30:]
            if w["status"] in (DONE, STOPPED):   # it is done again only on new proof: the old "done" fact answered the old request (A2A)
                w["claim"] = None
            w.update(status=ACTIVE, question="", next_at=moment.now(), fails=0, idle=0, waiting_since=None, reminded=False)   # the owner's answer gives the work a fresh start
            _log(w, "снова в работе" + (f": {note}" if note else ""))
            return f"Работа «{w['title']}» снова в работе: ход начнётся через несколько секунд"
        return Failure("action — pause, resume, stop или note")
    return _change(id, apply) or Failure(f"Работы {id} нет. Есть: " + (", ".join(f"[{w['id']}] {w['title']}" for w in _works()) or "ни одной"))


def control_by_model(id, action, note="", budget_usd=None, max_turns=None):
    """work_control as the model calls it: only on the owner's own words this turn. A background turn of the work itself could
    raise its own budget (3 → 90 $ in the audit): pause, resume, stop, more money or turns and notes are his; the apps' buttons
    are his too and call control directly."""
    if not getattr(status.local, "said", None):
        return Failure("work_control — только по слову Темы: в фоновом ходе работа сама себе бюджет, ходы и указания не меняет")
    return control(id, action, note, budget_usd, max_turns)


def answered(tid, text):
    """The owner pressed «Да» or «Нет» on what a work's turn prepared (the buttons under its question in Telegram, the apps'
    «Ждут «да»»): the press is his answer. A work waiting for him resumes with it as his note; one still going gets the note
    for its next turn. The press used to reach no work: it stayed «ждёт тебя», was reminded of a day later and paused after
    a week. tid — the task that prepared the action (confirm's item "tid"), text — what he pressed and how it went. Returns
    the reply, or None when no work's turn prepared it — tools.pressed may call it for every press."""
    w = next((w for w in _works() if tid and tid in w["tasks"] and w["status"] in (WAITING, ACTIVE)), None)
    if w is None:
        return None
    reply = control(w["id"], "resume", text) if w["status"] == WAITING else None
    if reply is None or isinstance(reply, Failure):   # going on already, or it can't go on (its money is spent): his answer waits in its notes
        control(w["id"], "note", text)
    return reply or f"Записала в работу «{w['title']}»"


def listing(state="current"):
    items = [w for w in _works() if state == "all" or (state == "current" and w["status"] not in (DONE, STOPPED)) or w["status"] == state]
    if not items:
        return "Долгих работ нет"
    return "\n".join(line(w) for w in items)


def _luma_line(w):
    # what is true about the work's project in Luma: kept, waiting for the switch, or given up after Luma refused again and again
    p = w.get("luma")
    if not p:
        return ""
    if p.get("off"):
        return f", проект «{p['project']}» в Luma больше не веду (Luma раз за разом не принимала)"
    if not settings.get("use_luma"):
        return f", проект «{p['project']}» в Luma не веду, пока Luma выключена в настройках"
    return f", проект в Luma «{p['project']}»"


def line(w):
    return (f"- [{w['id']}] «{w['title']}» — {STATUSES[w['status']]}, ход {w['turns']} из {w['max_turns']}, план {plan_line(w)}, "
            f"потрачено {spent(w)} из {w['budget']} $" + _luma_line(w)
            + (f"; ждёт ответа: {w['question']}" if w["status"] == WAITING and w.get("question") else ""))


def _limit_text(w):
    """Which of the work's limits ran out, and what the owner says to go on."""
    if spent(w) >= w["budget"]:
        what, ask = f"кончился бюджет (потрачено {spent(w)} из {w['budget']} $, ходов {w['turns']})", "скажи, сколько ещё можно потратить"
    else:
        what, ask = f"кончились ходы ({w['turns']} из {w['max_turns']}, потрачено {spent(w)} $)", "скажи «продолжай» или сколько ещё ходов"
    return f"Работа «{w['title']}»: {what}. Сделано по плану {plan_line(w)}. Продолжить — {ask}."


def context():
    """For the coordinator's every turn: the works not finished, and what each waits for."""
    items = [w for w in _works() if w["status"] not in (DONE, STOPPED)]
    return "\n".join(line(w) for w in items)


# ---------- one turn ----------
BRIEF = """Это ход {n} из {total} долгой работы [{id}] «{title}». Тема сейчас ничего не писал: работу ведёшь ты сама.
Цель (слова Темы): {goal}
Готово, когда: {done_when}
Границы: {limits}
План:
{plan}
Итог прошлого хода:
{checkpoint}{missing}{notes}{restart}{replan}
Потрачено {spent} $ из {budget} $.

Сделай следующие конкретные шаги к цели — сколько успеешь за этот ход: делай, а не описывай намерения. Уже сделанное не
повторяй (см. итог), неясное сначала проверь. Держись границ. Необратимое (сообщения людям, письма, оплата, удаление,
публикация) — как всегда, только с подтверждением Темы: подготовь и спроси через work_need, указав id подготовленного.
Ответ «Ещё не поручено [id]» (computer_task, claude_task) — не ошибка: так же спроси через work_need, с этим id и одной
фразой, что и зачем поручаешь.
План веди через work_plan: весь список целиком, один шаг «doing». Вся цель выполнена и проверена по условию готовности —
work_done с фактом-доказательством. Без Темы дальше нельзя (ответ, решение, вход, деньги, подтверждение) — work_need
с одним вопросом. Ответ хода — рабочая запись для следующего хода, Тема её не видит. Коротко, по разделам:
Сделано (с фактами: пути, адреса, числа, итоги проверок) / В работе / Мешает / Решения / Дальше."""

REPLAN = ("\nДва хода подряд без результата (только чтение, поиск или неисправленная ошибка): прежний путь не работает. "
          "Сначала назови себе причину, потом составь новый план иначе (work_plan) и сделай его первый шаг; сделанное не повторяй.")
# what the judge of the last turn chose for the next one (CAMEL's recovery strategies, checked by code: only these four count)
ADVICE = {"retry": "Прошлый ход упёрся во временную ошибку: повтори тот же шаг.",
          "replan": "Похоже, прежний путь не работает: назови себе причину, составь новый план иначе (work_plan) и сделай его первый шаг.",
          "split": "Шаг слишком большой: разбей его в плане на мелкие проверяемые шаги и сделай первый.",
          "other_way": "Этот путь не годится: возьми другой инструмент, источник или агента."}
CUT = ("\nПрошлый ход {by}: что он успел сделать, могло остаться, а могло и нет. Сначала проверь "
       "состояние (файлы, страницы, отправленное), потом продолжай; ничего не повторяй вслепую.")
CUT_BY = {"owner": "остановил Тема", "restart": "прервал перезапуск Сены"}   # who cut the last turn (cut_by): the next one is told


def brief(w):
    """What a turn of the work is told."""
    marks = {"todo": " ", "doing": "→", "done": "✓", "skipped": "–"}
    plan = "\n".join(f"{i}. [{marks[s['status']]}] {s['text']}" + (f" — {s['fact']}" if s.get("fact") else "")
                     for i, s in enumerate(w.get("plan", []), 1)) or "плана ещё нет: составь его (work_plan)"
    notes = "".join(f"\n- {moment.local(n['t']):%d.%m %H:%M}: {n['text']}" for n in w.get("notes", [])[-10:])
    return BRIEF.format(n=w["turns"], total=w["max_turns"], id=w["id"], title=w["title"], goal=w["goal"], done_when=w["done_when"],
                        limits=w.get("limits") or "не названы", plan=plan, checkpoint=w.get("checkpoint") or "это первый ход",
                        missing=("\nПроверка не нашла доказательств для: " + "; ".join(w["missing"])) if w.get("missing") else "",
                        notes=("\nЧто Тема сказал по этой работе:" + notes) if notes else "",
                        restart=(CUT.format(by=CUT_BY.get(w.get("cut_by"), CUT_BY["restart"])) + "".join(f"\n- {e}" for e in _effects(w.get("cut_tid")))
                                 + _cut_steps(w.get("cut_tid"))) if w.get("interrupted") else "",
                        replan=_advice(w), spent=spent(w), budget=w["budget"])


CUT_STEPS = 15   # tool calls of a cut turn the next one is shown


def _cut_steps(tid):
    # what the cut turn did, from the journal, beyond its confirmed effects: files written, a build already run (Microsoft
    # Agent Framework replays a workflow from its last checkpoint; here the next turn at least sees the steps). Someone else's
    # text is not repeated, only its size
    rows = events.search("all", kind="tool", tid=tid, limit=CUT_STEPS) if tid else []
    lines = []
    for r in rows:
        text = str(r.get("text") or "")
        shown = f"(чужой текст, {len(text)} знаков)" if "ВНЕШНИЕ ДАННЫЕ" in text else " ".join(text.split())[:160]
        lines.append(f"\n- {r.get('name') or 'шаг'}: {shown}")
    return ("\nЧто прерванный ход успел вызвать:" + "".join(lines)) if lines else ""


def _advice(w):
    # the judge's diagnosis of the last turn and its chosen fix reach this one (CAMEL: the reason was lost and every idle turn
    # got the same generic line); with no advice, two idle turns still ask for a new plan
    advice = w.get("advice") or {}
    if advice.get("kind") in ADVICE:
        return f"\nПроверка прошлого хода: {advice.get('why', '')[:300].rstrip('. ')}. {ADVICE[advice['kind']]}"
    return REPLAN if w.get("idle", 0) >= IDLE_REPLAN else ""


JUDGE = """Ты строгий судья долгой работы ассистентки Сены. Тебе дают цель Темы, условие готовности, границы, план, отчёт
последнего хода, что показали её инструменты в этом ходе и, если есть, заявление «готово» с фактом.
Доказательства — только выводы инструментов и сделанные действия; слова отчёта и заявление — не доказательство, пока их не
подтверждает вывод инструмента. Выведи из цели конкретные требования и для каждого найди доказательство. Сначала reason, потом
verdict — одно из четырёх:
done — цель выполнена целиком: условие готовности выполнено, и есть конкретный факт-доказательство (адрес, путь к файлу,
  вывод проверки, число), а не слова «готово» или «всё работает». Результат должен существовать на деле. Объясняет, почему
  сделать нельзя, — это не done. Нарушена граница — не done.
need_owner — дальше нельзя без Темы: нужен его ответ, решение, вход, деньги, подтверждение, или цель недостижима как
  поставлена. В reason — что именно от него нужно; ошибку сервиса цитируй дословно и приписывай только тому сервису, что
  назван в отчёте.
wait — работа ждёт того, что идёт само (сборка, выкладка, ответ человека, письмо, время): новый ход сейчас — пустая трата.
  wait_minutes — когда проверить снова (10–1440). Не выбирай wait только потому, что работы ещё много.
continue — не готово, и следующий шаг понятен. Это ответ по умолчанию, когда сомневаешься.
progress — что дал этот ход: result — есть новый результат по плану (файл, страница, отправленное подготовлено, проверка прошла);
  explored — только читала, искала, смотрела (список файлов, открыла страницу, поиск) без нужного результата; none — ошибка не
  исправлена или ничего не сделано.
missing — требования, для которых доказательства нет (они пойдут в следующий ход); для done — пусто.
recovery — если ход не дал результата, что делать в следующем: retry — временная ошибка (сеть, сбой сервиса), тот же шаг ещё раз;
  replan — путь не работает, нужен другой план; split — шаг слишком большой, разбить на мелкие; other_way — нужен другой
  инструмент, источник или агент. Результат есть — none.
message — только для done и need_owner: сообщение Теме от Сены, 1–3 короткие фразы: что сделано и где смотреть результат,
  или что от него нужно. Иначе пусто. Ответ — только JSON по схеме."""
VERDICT = {"type": "object", "properties": {   # the reason before the verdict: judges that decide first explain afterwards (pydantic-ai)
    "reason": {"type": "string"}, "verdict": {"type": "string", "enum": ["done", "continue", "wait", "need_owner"]},
    "progress": {"type": "string", "enum": ["result", "explored", "none"]},
    "recovery": {"type": "string", "enum": ["none", "retry", "replan", "split", "other_way"]},
    "missing": {"type": "array", "items": {"type": "string"}}, "wait_minutes": {"type": "integer"}, "message": {"type": "string"}},
    "required": ["reason", "verdict", "progress", "recovery", "missing", "wait_minutes", "message"]}
EVIDENCE_ROWS = 30   # tool results of a turn the judge reads


def _verdict(data):
    """The judge's answer in the shape the bookkeeping reads. The schema isn't strict: a field left out is filled in, and a field
    of the wrong kind is made right — a word instead of a list was walked letter by letter, a wait in words raised inside the
    bookkeeping, and the turn's checkpoint and the word to the owner were lost."""
    progress, recovery, missing = data.get("progress"), data.get("recovery"), data.get("missing")
    missing = missing if isinstance(missing, list) else [missing] if missing else []
    return {"verdict": data["verdict"], "reason": str(data.get("reason") or ""),
            "progress": progress if progress in VERDICT["properties"]["progress"]["enum"] else "result",
            "recovery": recovery if recovery in VERDICT["properties"]["recovery"]["enum"] else "none",
            "missing": [str(m) for m in missing if m], "wait_minutes": clamp(data.get("wait_minutes"), 0, 0, 1440),
            "message": str(data.get("message") or "")}


def judge(w, answer, tid=None):
    """The verdict on a turn: {"verdict", "reason", "wait_minutes", "message"}. A broken judge never stops the work: continue."""
    plan = "\n".join(f"- [{s['status']}] {s['text']}" for s in w.get("plan", [])) or "нет"
    shown = [f"- {r.get('name') or r['kind']}: {r['text'][:300]}" for r in events.search("all", kind="tool", tid=tid, limit=EVIDENCE_ROWS)] if tid else []
    done = confirm.effects(tid)
    text = (f"# Цель\n{w['goal']}\n\n# Готово, когда\n{w['done_when']}\n\n# Границы\n{w.get('limits') or 'не названы'}\n\n# План\n{plan}\n\n"
            f"# Отчёт хода {w['turns']}\n{str(answer)[-4000:]}"
            + "\n\n# Что показали инструменты в этом ходе (доказательства)\n" + ("\n".join(shown) or "ничего")
            + ("\n\n# Сделанные действия\n" + "\n".join(f"- {d}" for d in done) if done else "")
            + (f"\n\n# Заявлено «готово», факт\n{w['claim']}" if w.get("claim") else "")
            + ("\n\n# Что Тема сказал по этой работе (новое важнее цели)\n" + "\n".join(f"- {moment.local(n['t']):%d.%m %H:%M}: {n['text']}" for n in w["notes"][-10:])
               if w.get("notes") else "")
            + (f"\n\n# Вопрос Теме от работы\n{w['question']}" if w.get("question") else "") + f"\n\n# Сейчас\n{moment.local(time.time()):%d.%m.%Y %H:%M}")
    try:
        with costs.label(f"проверка работы: {w['title']}", tid):   # billed to the turn, so the work's budget counts it
            reply = llm.respond(purpose="проверка долгой работы", model=config.MODEL_FAST, reasoning={"effort": "low"}, instructions=JUDGE,
                                input=text, text={"format": {"type": "json_schema", "name": "verdict", "schema": VERDICT, "strict": False}})
        data = json.loads(reply.output_text)
        if isinstance(data, dict) and data.get("verdict") in VERDICTS:
            return _verdict(data)
    except Exception as e:
        events.error("Проверка долгой работы", e)
    # a judge that didn't answer says nothing about progress: neither a result nor idleness (Dify's study — this used to count as
    # a result and reset the idle count, so a stuck work looked busy)
    return {"verdict": "continue", "reason": "проверка не ответила, продолжаю", "progress": "unknown", "missing": [], "wait_minutes": 0,
            "message": "", "broken": True}


def _second_opinion(w, answer, tid, first):
    """A verdict that ends the work or stops it for the owner, when the turn itself didn't claim done or ask him, is asked again:
    two of three votes decide, stopping early when two agree (MetaGPT's vote; one fast call alone could end a work)."""
    unasked = (first["verdict"] == "done" and not w.get("claim")) or (first["verdict"] == "need_owner" and not w.get("question"))
    if not unasked:
        return first
    votes = [first]
    for _ in range(2):
        votes.append(judge(w, answer, tid))
        same = [v for v in votes if v["verdict"] == first["verdict"] and not v.get("broken")]
        if len(same) >= 2:
            return first
        if len([v for v in votes if v["verdict"] != first["verdict"] or v.get("broken")]) >= 2:
            break
    other = next((v for v in votes[1:] if not v.get("broken")), None)
    events.log("work", f"проверка не подтвердила «{first['verdict']}» вторым мнением", id=w["id"])
    return other if other and other["verdict"] != first["verdict"] else {**first, "verdict": "continue", "message": ""}


def turn_began(id, tid):
    """The turn's task is written down the moment it starts: if Sena falls mid-turn, what it spent still counts against the
    budget, and its journal is found by the next turn. Called on the turn's own thread before its model does anything: a work
    started after someone else's text marks the turn the same way, and a turn starting on a work the owner stopped a moment
    ago is stopped at once (his stop reached only the turn before it)."""
    seen = {}

    def apply(w):
        if tid not in w["tasks"]:
            w["tasks"].append(tid)
        seen.update(outside=w.get("outside"), stopped=w["status"] == STOPPED)
    _change(id, apply)
    if seen.get("outside"):
        status.touched(f"{seen['outside']} (в задаче, что завела эту работу)")
    if seen.get("stopped"):
        cancel.stop_task(tid)


_effects = confirm.effects   # what the turn really did to the world: done, or started with an unknown outcome


def _tomorrow():
    now = datetime.now()
    return (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0).timestamp()


def _turn(w):
    """Run one turn of the work and decide what next. Runs in its own thread; w is the record as the turn began."""
    try:
        answer, tid, outcome = _runner(w)
    except Exception as e:   # the runner itself broke: counted like a failed turn
        events.error(f"Ход работы «{w['title']}»", e)
        answer, tid, outcome = f"Ошибка: {e}", None, "failed"
    cut, failed, day = outcome == "cut", outcome == "failed", outcome == "day"
    verdict = None if cut or failed or day else judge(get(w["id"]) or w, answer, tid)   # the record holds the claim or question of this turn
    if verdict and verdict["verdict"] in ("done", "need_owner"):
        verdict = _second_opinion(get(w["id"]) or w, answer, tid, verdict)

    held = []

    def apply(cur):
        cur["running"], cur["interrupted"] = None, cut
        if cut:
            cur["cut_tid"] = tid
        if tid and tid not in cur["tasks"]:
            cur["tasks"].append(tid)
        if cut:   # the checkpoint of the last whole turn stays; the next turn checks what this one managed
            cur["cut_by"] = "restart" if power.is_stopping() else "owner"
            if power.is_stopping():
                _log(cur, f"ход {cur['turns']} прервало выключение Сены")
            elif cur["status"] == ACTIVE:
                cur["status"] = PAUSED   # the owner said "stop": the work waits for his "продолжай"
                _log(cur, f"ход {cur['turns']} остановлен Темой, работа на паузе")
            else:   # he stopped or paused this very work: its status says so already
                _log(cur, f"ход {cur['turns']} остановлен Темой, работа {STATUSES[cur['status']]}")
            return None
        if day:
            cur["next_at"], cur["turns"] = _tomorrow(), max(0, cur["turns"] - 1)   # the turn didn't happen: it isn't counted
            _log(cur, "дневной бюджет исчерпан, продолжу завтра")
            return ("tomorrow", f"Работа «{cur['title']}» продолжится завтра: {str(answer)[:200]}")
        if failed:
            cur["fails"] = cur.get("fails", 0) + 1
            _log(cur, f"ход {cur['turns']} не удался: {str(answer)[:200]}")
            if cur["fails"] >= FAILED_TURNS and cur["status"] == ACTIVE:
                cur["status"] = PAUSED
                return ("paused", f"Работа «{cur['title']}» на паузе: {FAILED_TURNS} хода подряд не удались ({str(answer)[:200]}). "
                                  "Скажи «продолжай», когда можно.")
            cur["next_at"] = moment.now() + 120 * cur["fails"]
            return None
        cur["fails"] = 0
        cur["checkpoint"] = str(answer)[-CHECKPOINT:]
        cur["missing"] = [_clean(m, 300) for m in verdict.get("missing") or []][:10]
        _log(cur, f"ход {cur['turns']}: {VERDICTS[verdict['verdict']]} — {verdict['reason']}")
        message = _clean(verdict.get("message"), 1500)
        if cur["status"] != ACTIVE:   # the owner paused or stopped it while the turn ran: his word wins
            return None
        if verdict["verdict"] == "done":
            cur["status"], cur["claim"] = DONE, cur.get("claim") or verdict["reason"]
            return ("done", f"Работа «{cur['title']}» сделана. " + (message or verdict["reason"]))
        if verdict["verdict"] == "need_owner" or cur.get("question"):
            cur["status"], cur["waiting_since"], cur["reminded"] = WAITING, moment.now(), False
            cur["question"] = cur.get("question") or verdict["reason"]
            held[:] = confirm.keep(tid, ASK_HOLD)   # what the turn prepared waits with the question, and comes with its buttons
            return ("waiting", f"Работа «{cur['title']}» ждёт тебя: " + (message or cur["question"]))
        cur["claim"] = None   # a claim the judge didn't accept is spent: the next turn proves it again
        cur["judge_broken"] = cur.get("judge_broken", 0) + 1 if verdict.get("broken") else 0
        if cur["judge_broken"] == JUDGE_BROKEN:   # said once: the work goes on, but nobody is checking it
            _notify and _notify(f"Работа «{cur['title']}»: проверка ходов {JUDGE_BROKEN} раза подряд не ответила — продолжаю, но без проверки "
                                "результата; загляни в журнал, если важно.")
        if verdict["verdict"] == "continue":   # waiting for a build or a reply is no idleness; reading around without a result is
            progress = verdict.get("progress", "result")
            # a result takes one idle turn off, not all of them (Magentic-One's stall counter decays): one trivial result between
            # idle turns no longer hides a work that is stuck — it asks the owner instead of going on for eighteen turns
            cur["idle"] = cur.get("idle", 0) if progress == "unknown" else max(0, cur.get("idle", 0) - 1) if progress == "result" else cur.get("idle", 0) + 1
            kind = verdict.get("recovery")   # only a fix the judge named; without one, two idle turns still ask for a new plan
            cur["advice"] = {"kind": kind, "why": _clean(verdict.get("reason"), 300)} if kind in ADVICE and progress not in ("result", "unknown") else None
            if cur["idle"] >= IDLE_ASK:
                cur["status"], cur["question"] = WAITING, f"{IDLE_ASK} хода подряд без результата: {verdict['reason']} Что мешает и как быть?"
                cur["waiting_since"], cur["reminded"] = moment.now(), False
                return ("waiting", f"Работа «{cur['title']}» буксует: {IDLE_ASK} хода подряд без результата ({verdict['reason']}). "
                                   "Что мешает или как лучше сделать?")
        if verdict["verdict"] == "wait":
            cur["next_at"] = moment.now() + max(10, verdict.get("wait_minutes") or 30) * 60
        else:
            cur["next_at"] = moment.now()
        if cur["turns"] >= cur["max_turns"] or spent(cur) >= cur["budget"]:
            cur["status"] = LIMITED
            return ("limited", _limit_text(cur))
        return None
    try:
        told = _change(w["id"], apply)
    finally:   # whatever broke above, the work is not left marked as running: it would never get a turn again
        with _lock:
            _running.discard(w["id"])
    if told:
        events.log("work", told[1], id=w["id"], status=told[0])
        if _notify:
            _notify(told[1], held) if held else _notify(told[1])


def tick():
    """Called every half a minute by the scheduler: start the turns that are due (a few works at a time). None while Sena is
    going down, or while a restart onto her own new code waits for the running tasks (ag_code._restart asks for it first): a
    turn started then only kept that wait going and was cut at its end."""
    if power.is_stopping() or power.is_off() or power.exit_code() == 3 or _runner is None:
        return 0
    started, changed, told = [], False, []
    with _lock:
        items = _all()
        works = [w for w in items if _readable(w)]   # the same records: what changes in them is saved with the rest
        for w in works:   # a work waiting for the owner: reminded once after a day, paused after a week (n8n's waits time out)
            if w["status"] != WAITING:
                continue
            if not w.get("waiting_since"):
                w["waiting_since"], changed = moment.now(), True
            since = w["waiting_since"]
            if moment.now() - since > WAIT_PAUSE:
                w["status"], changed = PAUSED, True
                _log(w, "неделю ждала ответа Темы: на паузе")
                told.append(f"Работа «{w['title']}» неделю ждала твоего ответа — поставила на паузу. Вопрос был: {w.get('question', '')[:300]}")
            elif moment.now() - since > WAIT_REMIND and not w.get("reminded"):
                w["reminded"], changed = True, True
                told.append(f"Работа «{w['title']}» всё ещё ждёт тебя: {w.get('question', '')[:300]}")
        for w in works:
            if len(_running) >= AT_ONCE:
                break
            if w["status"] != ACTIVE or w["id"] in _running or w.get("next_at", 0) > moment.now():
                continue
            if w["turns"] >= w["max_turns"] or spent(w) >= w["budget"]:   # the limits were changed by hand: no turn past them, and he hears why
                w["status"], changed = LIMITED, True
                told.append(_limit_text(w))
                continue
            w.update(turns=w["turns"] + 1, running=moment.now(), question="")
            _running.add(w["id"])
            started.append(dict(w))
            changed = True
        if changed:
            _save(items)
    for text in told:
        _notify and _notify(text)
    for w in started:
        safety.background(_turn, w, what=f"Долгая работа «{w['title']}»")
    for w in works:
        if _luma_due(w) and not _syncing:   # one project at a time: a sync takes a few quick calls
            _syncing.add(w["id"])
            safety.background(luma_sync, w["id"], what=f"Проект работы «{w['title']}» в Luma")
            break
    return len(started)


# ---------- the work's project in Luma ----------
def _luma_wanted(w):
    """What Luma should show for the work: the project's status and each card's status by its title."""
    p = w["luma"]
    cards = {s["text"][:CARD_TITLE]: CARD_STATUS[s["status"]] for s in w.get("plan", [])}
    for title, card in (p.get("cards") or {}).items():
        if card and title not in cards:   # a step dropped from the plan
            cards[title] = "cancelled"
    return {"status": PROJECT_STATUS[w["status"]], "cards": cards}


def _luma_due(w):
    # the owner's «Luma off» switch holds the sync too, as it hides the Luma tools: it catches up when he turns it on again
    p = w.get("luma")
    return (bool(p) and not p.get("off") and bool(settings.get("use_luma")) and p.get("next_try", 0) <= moment.now()
            and p.get("shown") != _luma_wanted(w))


def _batches(items):
    return [items[i:i + LUMA_BATCH] for i in range(0, len(items), LUMA_BATCH)]


def luma_sync(id):
    """Send the work's project to Luma: made once (project_add returns an open project of that name instead of a second one),
    then only what changed — its status, a card per new step, a card's status. Luma that is closed is not opened (the owner
    may have closed it on purpose): the sync waits for it. A card the owner deleted in Luma is left alone. Background only."""
    try:
        w = get(id)
        if not w or not _luma_due(w):
            return None
        p, want = w["luma"], _luma_wanted(w)
        shown = dict(p.get("shown") or {"status": None, "cards": {}})
        project, cards, now = p.get("id"), dict(p.get("cards") or {}), dict(shown["cards"])
        try:
            if not luma.is_up():
                return _luma_later(id, None)
            if not project:
                made = luma.call("project_add", {"items": [{"title": p["project"], "description": f"{w['goal']}\nГотово, когда: {w['done_when']}"[:2000],
                                                            "status": want["status"]}]})
                project, shown["status"] = made["created"][0]["id"], made["created"][0].get("status")
            if shown["status"] != want["status"]:
                luma.call("project_update", {"items": [{"project": project, "status": want["status"]}]})
            # cards: a refusal is about that one card, read item by item (per_item) — Luma answers a deleted card as one refused
            # item, and a batch of only that card raised, failed five times and switched the project's whole sync off
            new = [t for t in want["cards"] if t not in cards]
            for chunk in _batches(new):
                made = luma.call("task_add", {"items": [{"title": t, "project": project} for t in chunk]}, per_item=True)
                for row in made.get("created", []):
                    cards[chunk[row["index"]]], now[chunk[row["index"]]] = row["id"], row.get("status", "todo")
                for row in made.get("failed", []):   # refused: not tried again, the journal says why
                    cards[chunk[row["index"]]] = None
                    _card_refused(id, chunk[row["index"]], row)
            moves = [(t, s) for t, s in want["cards"].items() if cards.get(t) and now.get(t) != s]
            for chunk in _batches(moves):
                made = luma.call("task_update", {"items": [{"task": cards[t], "status": s} for t, s in chunk]}, per_item=True)
                refused = {row["index"]: row for row in made.get("failed", [])}
                for i, (t, s) in enumerate(chunk):
                    if i in refused:   # the owner deleted the card in Luma: it stays deleted
                        cards[t] = None
                        _card_refused(id, t, refused[i])
                    else:
                        now[t] = s
        except (luma.LumaError, KeyError, IndexError, TypeError, AttributeError) as e:   # Luma refused, or answered in an odd shape
            return _luma_later(id, e)
        sent = {"status": want["status"], "cards": {t: (now.get(t) if cards.get(t) else s) for t, s in want["cards"].items()}}

        def apply(cur):
            cur["luma"].update(id=project, cards=cards, shown=sent, fails=0, next_try=0)
        _change(id, apply)
        events.log("work", f"проект в Luma: {p['project']} — {want['status']}, карточек {sum(1 for c in cards.values() if c)}", id=id)
        return sent
    finally:
        with _lock:
            _syncing.discard(id)


def _card_refused(id, title, row):
    events.log("work", f"Luma не приняла карточку «{title[:80]}»: {str(row.get('error', ''))[:200]} — больше её не трогаю", id=id)


def _luma_later(id, error):
    """Luma is closed (error None) or refused: try again in a while; after LUMA_FAILS refusals in a row the work stops keeping
    the project and the owner hears why once."""
    said = []

    def apply(cur):
        p = cur["luma"]
        p["next_try"] = moment.now() + LUMA_RETRY
        if error is not None:
            p["fails"] = p.get("fails", 0) + 1
            if p["fails"] >= LUMA_FAILS:
                p["off"] = True
                said.append(f"Работа «{cur['title']}»: не получается вести проект «{p['project']}» в Luma ({error}) — дальше веду работу без неё.")
    _change(id, apply)
    if error is not None:
        events.error("Проект работы в Luma", error)
    for text in said:
        _notify and _notify(text)
    return None


def resume_after_restart():
    """At start: a turn that was going when Sena stopped is marked interrupted — the next one checks before repeating. A record
    the engine can't read is named in the journal once and passed over: it never stops the start, nor the other works."""
    with _lock:
        items = _all()
        unreadable = [w.get("id", "?") if isinstance(w, dict) else type(w).__name__ for w in items if not _readable(w)]
        cut = [w for w in items if _readable(w) and w.get("running")]
        for w in cut:
            w.update(running=None, interrupted=True, cut_by="restart", next_at=moment.now(), cut_tid=w["tasks"][-1] if w["tasks"] else None)
            _log(w, f"ход {w['turns']} прервал перезапуск")
        if cut:
            _save(items)
    if unreadable:
        events.error("Долгие работы при запуске", ValueError(f"не читаются записи {', '.join(map(str, unreadable[:10]))}: их ходы не идут, "
                                                              "остальные работы идут как обычно"))
    for w in cut:
        events.log("work", f"работа «{w['title']}»: ход прервал перезапуск, продолжу с проверки", id=w["id"])
    return len(cut)


def start_engine(runner, notify):
    """Wire the engine: who runs a turn and who tells the owner (app.py). Wired first: a stale "running" mark becomes
    "interrupted" right after (no turn can start before the scheduler, which app.py starts later), and if that raised, the
    engine would stay unwired for the whole run."""
    global _runner, _notify
    _runner, _notify = runner, notify
    return resume_after_restart()


STEP = {"type": "object", "properties": {"text": string(), "status": {**string(), "enum": list(STEP_STATES)},
                                         "fact": string("для сделанного шага — факт результата: путь, адрес, число (его увидят следующие ходы)")},
        "required": ["text", "status"]}
TOOLS = [
    tool("work_start", "Завести долгую работу: большое дело, которое не уложится в один заход (сайт от требований до выкладки, разобрать "
                       "всю почту, прочитать книгу, большое исследование, проект на несколько часов или дней). Дальше ты сама получаешь её ходы "
                       "в фоне, пока она не сделана и не проверена, не нужен Тема или не кончился бюджет.",
         {"title": string("коротко, 2–5 слов"), "goal": string("цель словами Темы, полностью: что нужно и зачем"),
          "done_when": string("проверяемое условие готовности: по какому факту видно, что сделано"),
          "plan": {"type": "array", "items": {"type": "string"}, "description": "этапы по порядку, у каждого — признак «сделано»; последний даёт сам результат"},
          "limits": string("границы: что нельзя трогать или делать, сроки"),
          "budget_usd": {"type": "number", "description": f"сколько можно потратить на ходы, $ (по умолчанию {BUDGET}; Тема назвал — его число)"},
          "max_turns": {"type": "integer", "description": f"сколько ходов можно (по умолчанию {MAX_TURNS})"},
          "project": string("проект Темы в Luma, для которого эта работа (название из project_list или новое): работа сама заведёт его там, "
                            "будет вести его статус и шаги плана карточками. Пусто — работа не для проекта Темы (почта, книга, исследование)")},
         ["title", "goal", "done_when"]),
    tool("work_plan", "План долгой работы целиком (заменяет прежний): шаги по порядку со статусом todo, doing (один), done, skipped. "
                      "У каждого шага — проверяемый признак «сделано»; шаги, которые могут не выйти по отдельности, — отдельно; без шагов "
                      "«подумать», «проверить», «оформить»; последний шаг даёт сам результат.",
         {"id": string(), "steps": {"type": "array", "items": STEP}}, ["id", "steps"]),
    tool("work_done", "Долгая работа выполнена целиком и проверена по условию готовности: факт-доказательство. После хода это проверит судья.",
         {"id": string(), "evidence": string("факт: адрес, путь к файлу, вывод проверки, число")}, ["id", "evidence"]),
    tool("work_need", "Дальше по долгой работе нельзя без Темы (ответ, решение, вход, деньги, подтверждение подготовленного): один вопрос ему. "
                      "Работа подождёт его ответа.", {"id": string(), "question": string("вопрос Теме, одной-двумя фразами")}, ["id", "question"]),
    tool("work_control", "Только по слову Темы: поставить долгую работу на паузу, продолжить (с его ответом, добавленным бюджетом или ходами), "
                         "остановить, или записать его указание для следующих ходов (note).",
         {"id": string(), "action": {**string(), "enum": ["pause", "resume", "stop", "note"]}, "note": string("его ответ или указание"),
          "budget_usd": {"type": "number", "description": "сколько ещё можно потратить, $"}, "max_turns": {"type": "integer", "description": "сколько ещё ходов"}},
         ["id", "action"]),
    tool("work_list", "Долгие работы: статус, ход, план, расход, чего ждут", {"state": {**string(), "enum": ["current", "all", *STATUSES]}}),
]
FUNCTIONS = {"work_start": start, "work_plan": set_plan, "work_done": claim_done, "work_need": need_owner, "work_control": control_by_model,
             "work_list": listing}
