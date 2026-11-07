"""Panels: screens the server describes and the apps (Android, Mac) only draw — so a new thing Sena can do reaches both
apps without code in them. A panel is blocks plus actions; the app sends the action back with what the owner typed or chose,
the server does it (through desk.act, so it is one line in the event journal) and returns the panel as it is now.

The contract both apps render (GET desk/api/panels, GET desk/api/panel?id=…, POST desk/api/panel_act):
  panel  = {"id", "title", "subtitle", "blocks": [block]}
  block  = {"type": "stats", "items": [{"label", "value", "tone"?}]}
         | {"type": "text", "text", "tone"?}                         tone: "muted" | "good" | "warn" | "bad"
         | {"type": "section", "title"}
         | {"type": "items", "items": [item], "empty": text}
         | {"type": "form", "id", "title", "fields": [field], "submit": action}
         | {"type": "switch", "label", "value": bool, "action": action}       the app sends values {"value": bool}
         | {"type": "buttons", "items": [action]}
  item   = {"id", "title", "subtitle"?, "meta"?, "progress"? (0–100), "tone"?, "actions": [action]}
  field  = {"name", "label", "kind": "text" | "multiline" | "number" | "choice", "value"?, "options"? [{"value", "label"}], "placeholder"?}
  action = {"id", "label", "args": {...}, "confirm"? (a question before doing it), "danger"? bool,
            "input"? {"label", "kind": "text" | "multiline" | "number", "value"?, "optional"? bool}}
            input: the app asks for one value first; optional — an empty answer is a real answer too
  A "value" may be a number (a number field's default): the apps show it as text.
  POST body: {"panel", "action", "args", "values": {field name or "value": what the owner gave}} -> {"result", "panel"},
  or {"error", "panel"} when the action refused (a Failure): the apps show why and keep what the owner typed."""
import math
import threading
import time

from sena import config, scheduler
from sena.agents import checkup, programs
from sena.agents.ag_sena import tools as sena_tools
from sena.agents.ag_sena import work
from sena.core import confirm, offsite, power, safety, settings, store
from sena.core.tools import Failure
from sena.memory import goals, lessons
from sena.phone.api import ApiError
from sena.world import mcp

AGENT_NAMES = {"ag_sena": "Сена", "ag_web": "Браузер", "ag_mac": "Компьютер", "ag_code": "Код"}


def _action(name, label, confirm_text=None, danger=False, input=None, **args):
    a = {"id": name, "label": label, "args": args}   # args may carry an "id" of their own (the goal's, the lesson's)
    if confirm_text:
        a["confirm"] = confirm_text
    if danger:
        a["danger"] = True
    if input:
        a["input"] = input
    return a


def _when(t):
    return time.strftime("%d.%m.%Y", time.localtime(t)) if t else ""


def _number(values, key, default, label):
    """A number the owner typed into a form or an input (a comma works as a point); empty — the default; not a number — a clear refusal, not a crash."""
    raw = str(values.get(key) if values.get(key) is not None else "").strip().replace(",", ".")
    if not raw:
        return default
    try:
        number = float(raw)
    except ValueError:
        number = math.nan
    if not math.isfinite(number):
        raise ApiError(400, f"«{label}» — здесь нужно число")
    return number


# ---------- goals ----------
def goals_panel():
    items = goals.all_goals()
    active = [g for g in items if g["status"] == goals.ACTIVE]
    rows = []
    for g in active:
        left = [s["text"] for s in g["steps"] if not s["done"]]
        acts = [_action("goal_note", "Как идёт", id=g["id"], input={"label": "Как идёт? Своими словами", "kind": "multiline"}),
                _action("goal_step_add", "Новый шаг", id=g["id"], input={"label": "Шаг", "kind": "text"})]
        if left:
            acts.insert(0, _action("goal_step_done", f"Сделано: {left[0][:40]}", id=g["id"], step=left[0]))
        acts += [_action("goal_close", "Достигнута", id=g["id"], status=goals.DONE),
                 _action("goal_close", "Оставить", id=g["id"], status=goals.DROPPED, confirm_text=f"Оставить цель «{g['title']}»? Она сохранится в списке закрытых", danger=True)]
        rows.append({"id": g["id"], "title": g["title"], "progress": goals.progress(g), "actions": acts,
                     "subtitle": f"ход {goals.progress_line(g)}" + (f" · {g['log'][-1]['text']}" if g["log"] else ""),
                     "meta": " · ".join(x for x in (f"срок {g['deadline']}" if g["deadline"] else "", f"проверка {_when(g['next_check'])}") if x)})
    closed = [g for g in items if g["status"] != goals.ACTIVE]
    return {"id": "goals", "title": "Цели", "subtitle": "то, к чему Тема идёт неделями: Сена напоминает следующий шаг и спрашивает, как идёт",
            "blocks": [
                {"type": "stats", "items": [{"label": "в работе", "value": str(len(active))},
                                            {"label": "достигнуто", "value": str(sum(g["status"] == goals.DONE for g in items)), "tone": "good"}]},
                {"type": "items", "items": rows, "empty": "Целей пока нет: добавь первую ниже или скажи Сене «моя цель — …»"},
                {"type": "form", "id": "goal_add", "title": "Новая цель", "submit": _action("goal_add", "Добавить"),
                 "fields": [{"name": "title", "label": "Цель", "kind": "text", "placeholder": "Английский до B2"},
                            {"name": "why", "label": "Зачем", "kind": "text"},
                            {"name": "deadline", "label": "Срок", "kind": "text", "placeholder": "01.05.2027 или «к маю»"},
                            {"name": "steps", "label": "Первые шаги, по строке на шаг", "kind": "multiline"},
                            {"name": "check_every_days", "label": "Спрашивать раз в, дней", "kind": "number", "value": goals.CHECK_DAYS}]},
                *([{"type": "section", "title": "Закрытые"},
                   {"type": "items", "items": [{"id": g["id"], "title": g["title"], "tone": "muted",
                                                "subtitle": "достигнута" if g["status"] == goals.DONE else "оставлена",
                                                "actions": [_action("goal_close", "Вернуть в работу", id=g["id"], status=goals.ACTIVE)]} for g in closed[-20:]],
                    "empty": ""}] if closed else []),
            ]}


def _goal_add(args, values):
    steps = [line.strip() for line in str(values.get("steps") or "").splitlines() if line.strip()]
    days = _number(values, "check_every_days", goals.CHECK_DAYS, "Спрашивать раз в, дней")
    return goals.add(values.get("title", ""), values.get("why", ""), values.get("deadline", ""), steps, int(days))


# ---------- long works ----------
TONES = {work.ACTIVE: None, work.WAITING: "warn", work.PAUSED: "muted", work.LIMITED: "warn", work.DONE: "good", work.STOPPED: "muted"}


def works_panel():
    items = work.all_works()
    current = [w for w in items if w["status"] not in (work.DONE, work.STOPPED)]
    rows = []
    for w in current:
        acts = [_action("work_note", "Указание", id=w["id"], input={"label": "Что учесть в следующих ходах", "kind": "multiline"})]
        if w["status"] == work.ACTIVE:
            acts.append(_action("work_pause", "Пауза", id=w["id"]))
        else:
            acts.append(_action("work_resume", "Продолжить", id=w["id"], input={"label": "Ответ или указание (можно пусто)", "kind": "multiline", "optional": True}))
        if w["status"] == work.LIMITED:
            acts.append(_action("work_budget", "Добавить бюджет", id=w["id"], input={"label": "Сколько ещё можно потратить, $", "kind": "number", "value": work.BUDGET}))
        acts.append(_action("work_stop", "Остановить", id=w["id"], danger=True, confirm_text=f"Остановить работу «{w['title']}»? Сделанное останется"))
        last = w["log"][-1]["text"] if w.get("log") else ""
        rows.append({"id": w["id"], "title": w["title"], "progress": work.progress(w), "tone": TONES.get(w["status"]), "actions": acts,
                     "subtitle": f"{work.STATUSES[w['status']]} · ход {w['turns']} из {w['max_turns']} · план {work.plan_line(w)}"
                                 + (f" · {w['question']}" if w["status"] == work.WAITING and w.get("question") else f" · {last}" if last else ""),
                     "meta": f"потрачено {work.spent(w)} из {w['budget']} $ · с {_when(w['created'])}"})
    closed = [w for w in items if w["status"] in (work.DONE, work.STOPPED)]
    return {"id": "works", "title": "Долгие работы", "subtitle": "большие дела, которые Сена ведёт сама ход за ходом, пока не сделает и не проверит",
            "blocks": [
                {"type": "stats", "items": [{"label": "в работе", "value": str(sum(w["status"] == work.ACTIVE for w in items))},
                                            {"label": "ждут тебя", "value": str(sum(w["status"] == work.WAITING for w in items)), "tone": "warn"},
                                            {"label": "сделано", "value": str(sum(w["status"] == work.DONE for w in items)), "tone": "good"}]},
                {"type": "items", "items": rows, "empty": "Долгих работ нет: поручи Сене большое дело — «сделай сайт…», «разбери всю почту…»"},
                *([{"type": "section", "title": "Закрытые"},
                   {"type": "items", "items": [{"id": w["id"], "title": w["title"], "tone": "muted",
                                                "subtitle": f"{work.STATUSES[w['status']]} · ходов {w['turns']} · потрачено {work.spent(w)} $",
                                                "actions": [_action("work_resume", "Продолжить", id=w["id"], input={"label": "Что ещё нужно", "kind": "multiline"})]}
                                               for w in closed[-20:]], "empty": ""}] if closed else []),
            ]}


# ---------- the agents' own memory ----------
def lessons_panel():
    blocks = [{"type": "text", "tone": "muted",
               "text": "Короткие уроки каждого агента: где на сайте кнопка, какая команда работает на этом Mac, чем в проекте запускаются тесты. "
                       "Пишет судья после задач; черновики агентов ждут его подтверждения. Исправленное здесь — главнее."}]
    for agent, name in AGENT_NAMES.items():
        items = lessons.listing(agent)
        rows = []
        for i in sorted(items, key=lambda i: (i["state"] != lessons.DRAFT, -i["confirmed"])):
            acts = [_action("lesson_edit", "Исправить", agent=agent, id=i["id"], input={"label": "Урок", "kind": "multiline", "value": i["text"]}),
                    _action("lesson_retire", "Убрать", agent=agent, id=i["id"], confirm_text="Убрать урок? Он уйдёт в архив агента", danger=True)]
            if i["state"] == lessons.DRAFT:
                acts.insert(0, _action("lesson_keep", "Оставить", agent=agent, id=i["id"]))
            by = {"judge": "разбор", "self": "сам агент", "owner": "Тема"}.get(i.get("by"), i.get("by", ""))
            rows.append({"id": i["id"], "title": i["text"], "tone": "warn" if i["state"] == lessons.DRAFT else None, "actions": acts,
                         "subtitle": " · ".join(x for x in (i["scope"], "черновик" if i["state"] == lessons.DRAFT else f"подтверждён {i['confirmed']} раз", by) if x)})
        blocks += [{"type": "section", "title": f"{name} ({len(items)})"}, {"type": "items", "items": rows, "empty": "Уроков пока нет"}]
    blocks.append({"type": "form", "id": "lesson_add", "title": "Научить агента", "submit": _action("lesson_add", "Записать"),
                   "fields": [{"name": "agent", "label": "Кому", "kind": "choice", "value": "ag_web",
                               "options": [{"value": a, "label": n} for a, n in AGENT_NAMES.items()]},
                              {"name": "text", "label": "Урок одной фразой", "kind": "multiline"},
                              {"name": "scope", "label": "Сайт, программа или проект (необязательно)", "kind": "text"}]})
    drafts = sum(len(lessons.drafts(a)) for a in AGENT_NAMES)
    return {"id": "lessons", "title": "Память агентов", "subtitle": f"черновиков ждут разбора: {drafts}" if drafts else "уроки, проверенные разбором", "blocks": blocks}


def _agent(args, values=None):
    agent = (values or {}).get("agent") or args.get("agent")
    if agent not in lessons.AGENTS:
        raise ApiError(400, "Нет такого агента")
    return agent


# ---------- the exam of the mind ----------
def _exam_stale(run):
    """What changed since an exam run, in words, or "": the code it measured, or the personality and skills it was given."""
    said = []
    if run.get("version") and config.version() and run["version"] != config.version():
        said.append(f"изменился код ({run['version']} → {config.version()})")
    if any(p.exists() and p.stat().st_mtime > run["t"] for p in [config.PERSONALITY_FILE, *sorted(config.SKILLS_DIR.glob("*.md"))]):
        said.append("изменились личность или навыки")
    return " и ".join(said)


def exam_panel():
    runs = store.load_json(config.EXAM_FILE, {"runs": []})["runs"]
    last = runs[-1] if runs else None
    blocks = []
    if last:
        groups = {}
        for c in last["cases"]:
            if c["passed"] is not None:
                done, total = groups.get(c["group"], (0, 0))
                groups[c["group"]] = (done + bool(c["passed"]), total + 1)
        blocks += [{"type": "stats", "items": [{"label": "сдано", "value": f"{last['passed']} из {last['total']}", "tone": "good" if last["passed"] == last["total"] else None},
                                               {"label": "стоил", "value": f"{last['usd']:.2f} $"}, {"label": "когда", "value": _when(last["t"])}]},
                   {"type": "items", "items": [{"id": g, "title": g, "subtitle": f"{d} из {t}", "progress": round(100 * d / t),
                                                "tone": None if d == t else "warn", "actions": []} for g, (d, t) in groups.items()], "empty": ""}]
        failed = [c for c in last["cases"] if c["passed"] is False]
        if failed:
            blocks += [{"type": "section", "title": "Не сдано"},
                       {"type": "items", "items": [{"id": c["id"], "title": c["turns"][0]["say"][:120] if c["turns"] else c["id"], "subtitle": "; ".join(c["problems"])[:300],
                                                    "tone": "bad", "actions": []} for c in failed], "empty": ""}]
        if last.get("verdict"):
            blocks.append({"type": "text", "text": last["verdict"], "tone": "muted"})
        stale = _exam_stale(last)
        if stale:   # a score of yesterday's code is not today's (Flowise flags an evaluation whose flow or data changed since)
            blocks.append({"type": "text", "text": f"Результат устарел: с этого экзамена {stale}. Запусти экзамен заново.", "tone": "warn"})
        if len(runs) > 1:
            blocks += [{"type": "section", "title": "Прошлые прогоны"},
                       {"type": "items", "items": [{"id": str(r["t"]), "title": f"{r['passed']} из {r['total']} · {r['usd']:.2f} $", "subtitle": f"{_when(r['t'])} · {r.get('why', '')}",
                                                    "actions": []} for r in reversed(runs[-6:-1])], "empty": ""}]
    else:
        blocks.append({"type": "text", "text": "Экзамена ещё не было. Настоящие просьбы и атаки чужим текстом: понимает ли Сена и выбирает ли верные действия.", "tone": "muted"})
    budget = settings.get("exam_budget_usd")
    running, off = programs.exam_running(), power.is_off()   # running: whoever started it, the weekly run too
    limit = _action("exam_budget", f"Предел: {budget:g} $", input={"label": "Один прогон экзамена не дороже, $", "kind": "number", "value": budget})
    if off and not running:   # the exam calls the paid brain: it waits for her to be on (programs.start_exam refuses it meanwhile)
        blocks.append({"type": "text", "tone": "muted", "text": "Сена выключена: экзамен можно запустить после «Запустить Сену»."})
    blocks += [{"type": "switch", "label": "Раз в неделю, после недельного разбора", "value": bool(settings.get("exam")), "action": _action("exam_weekly", "Раз в неделю")},
               {"type": "buttons", "items": ([] if running or off else [_action("exam_run", "Запустить экзамен", confirm_text=f"Экзамен на настоящей модели: около 1 $, не больше {budget:g} $. Запустить?")])
                                            + [limit]}]
    return {"id": "exam", "title": "Экзамен ума", "subtitle": "идёт сейчас: результат через несколько минут" if running else "проверка на настоящих просьбах; провал после самоизменений откатывает их",
            "blocks": blocks}


def _exam_run(args, values):
    return programs.start_exam("из приложения")   # refused while Sena is off or another run is on; stopped with her


def _exam_budget(args, values):
    """The exam's own spending limit (settings.RANGES): no app had a field for it."""
    kept, rejected = settings.apply({"exam_budget_usd": _number(values, "value", settings.get("exam_budget_usd"), "Один прогон экзамена не дороже, $")})
    if rejected:
        low, high = settings.RANGES["exam_budget_usd"]
        return Failure(f"Предел экзамена — от {low:g} до {high:g} $")
    return f"Экзамен не дороже {kept['exam_budget_usd']:g} $"


# ---------- what waits for the owner's "yes" ----------
PRESS_WAIT = 8   # s: a press waits this long for the action's own answer; a longer one (an agent's errand, Claude Code) goes on behind it


def press(sid, yes):
    """The owner's «Да» / «Отмена» from an app (this panel and the desk's buttons): the action runs beside the request, so one
    longer than the apps' request timeout isn't cut off and shown as an error. A quick one answers with its own result; a long
    one says it goes on, and its result reaches the conversation (sena_tools.pressed writes it there)."""
    name = (confirm.pending.get(sid) or {}).get("name", "")
    out, done = [], threading.Event()

    def work():
        try:
            out.append(sena_tools.pressed(sid, yes))
        except Exception as e:
            out.append(Failure(f"Не получилось: {str(e)[:200]}"))
            raise   # and the journal keeps it (safety.background)
        finally:
            done.set()
    safety.background(work, what="Подтверждённое действие")
    if not done.wait(PRESS_WAIT):
        return f"Делаю: {name or 'действие'} — это дольше обычного, результат придёт в переписку"
    return out[0]


def confirm_panel():
    rows = [{"id": w["id"], "title": w["name"], "subtitle": w["text"][:300], "meta": time.strftime("%H:%M", time.localtime(w["time"])),
             "actions": [_action("confirm_yes", "Да", id=w["id"]), _action("confirm_no", "Отмена", id=w["id"], danger=True)]} for w in confirm.waiting()]
    return {"id": "confirm", "title": "Ждут «да»", "subtitle": "отправки, правки и поручения, которые Сена подготовила: нажатие — это и есть твоё «да»",
            "blocks": [{"type": "items", "items": rows, "empty": "Ничего не ждёт подтверждения"}]}


# ---------- copies: on this Mac every night, and encrypted off it ----------
def backups_panel():
    local = sorted(p for p in config.BACKUPS_DIR.glob("*_sena.db")) if config.BACKUPS_DIR.is_dir() else []
    away = offsite.status()
    blocks = [{"type": "stats", "items": [
        {"label": "копия на Mac", "value": _when(local[-1].stat().st_mtime) if local else "нет", "tone": None if local else "warn"},
        {"label": "копий вне Mac", "value": str(away["count"]), "tone": None if away["count"] else "warn"},
        {"label": "последняя", "value": f"{away['kb']} КБ" if away["count"] else "—"}]}]
    if not away["available"]:
        blocks.append({"type": "text", "tone": "warn", "text": "iCloud Drive на этом Mac выключен: копия вне Mac не делается. Включи iCloud Drive в настройках системы."})
    else:
        blocks.append({"type": "text", "tone": "muted", "text": f"Каждую ночь зашифрованная копия уходит в {away['folder']} (хранятся {offsite.KEEP} последних)."})
    blocks += [{"type": "section", "title": "Если Mac потерян"},
               {"type": "text", "text": "Открывает копию только ключ восстановления. Сохрани его один раз в своём менеджере паролей: "
                                        "в Терминале на этом Mac — python -m sena.core.offsite key. Развернуть копию на новом Mac: "
                                        "python -m sena.core.offsite restore <файл копии> <папка> --key <ключ>."},
               {"type": "buttons", "items": [_action("backup_now", "Сделать копию сейчас")]}]
    return {"id": "backups", "title": "Копии", "subtitle": "память, люди, переписка и секреты — на этом Mac и зашифрованно вне его", "blocks": blocks}


def _backup_now(args, values):
    from sena.agents import ag_night   # late: a panel doesn't load the night's agent until it is asked to copy
    if not scheduler.run_now("Копия памяти", lambda: ag_night.backup_memory(force=True)):   # the Mac's "Резервная копия" too: a double press makes one copy
        return Failure("Копия уже делается: дождись конца")
    return "Копия делается: через минуту она будет здесь"


# ---------- the system check: every part tried for real ----------
TONE = {checkup.OK: "good", checkup.WARN: "warn", checkup.FAIL: "bad", checkup.OFF: "muted", checkup.WAIT: "muted"}


def checkup_panel():
    report = checkup.last()
    rows = {r["id"]: r for r in report.get("results", [])}
    now = checkup.running()
    seen = [rows[c.id] for c in checkup.CHECKS if c.id in rows]
    blocks = [{"type": "stats", "items": [
        {"label": "работает", "value": str(sum(r["status"] == checkup.OK for r in seen)), "tone": "good"},
        {"label": "с оговоркой", "value": str(sum(r["status"] == checkup.WARN for r in seen)), "tone": "warn" if any(r["status"] == checkup.WARN for r in seen) else None},
        {"label": "не работает", "value": str(sum(r["status"] == checkup.FAIL for r in seen)), "tone": "bad" if any(r["status"] == checkup.FAIL for r in seen) else None},
        {"label": "проверено", "value": _when_time(report.get("t")) or "ещё нет"}]}]
    if now:
        blocks.append({"type": "text", "tone": "muted", "text": f"Идёт проверка: готово {now['done']} из {now['total']}. Результаты появляются здесь сами."})
    else:
        blocks.append({"type": "buttons", "items": [_action("check_all", "Проверить всё")]})
    if power.is_off():
        blocks.append({"type": "text", "tone": "muted", "text": "Сена выключена: проверяется только бесплатное и то, что не запускает программ. "
                                                                  "Домашние модели, слух, голос, Claude Code и балансы — после «Запустить Сену»."})
    for group in checkup.GROUPS:
        items = []
        for c in (c for c in checkup.CHECKS if c.group == group):
            r = rows.get(c.id)
            subtitle = (r["summary"] + (f" · Что сделать: {r['fix']}" if r["fix"] and r["status"] in (checkup.FAIL, checkup.WARN) else "")) if r else "ещё не проверялось"
            items.append({"id": c.id, "title": c.title, "subtitle": subtitle, "tone": TONE.get(r["status"]) if r else "muted",
                          "meta": f"{checkup.WORDS[r['status']]} · {_when_time(r['t'])}" if r else None,
                          "actions": [] if now else [_action("check_one", "Проверить", id=c.id)]})
        blocks += [{"type": "section", "title": group}, {"type": "items", "items": items, "empty": ""}]
    return {"id": "checkup", "title": "Проверка системы", "subtitle": "каждая часть пробуется настоящим действием: бесплатно, а где нужен платный вызов — дешевле сотой цента",
            "blocks": blocks}


def _when_time(t):
    return time.strftime("%d.%m %H:%M", time.localtime(t)) if t else ""


def _check(part):
    """Start a check the way any background job starts (seen in «Процессы»; a double press runs it once) and give it a few
    seconds: a quick one answers at once, a long one keeps filling the panel."""
    done = threading.Event()

    def work():
        try:
            checkup.run(part)
        except RuntimeError:   # another check started a moment earlier: its results fill the panel
            pass
        finally:
            done.set()
    if checkup.running() or not scheduler.run_now("Проверка системы", work):
        return "Проверка уже идёт: результаты появляются на этой странице"
    return "Проверка закончена" if done.wait(8) else "Проверка идёт: результаты появятся здесь сами, обычно за полминуты"


# ---------- the owner's MCP servers ----------
def mcp_panel():
    now = mcp.status()
    rows = []
    for name, spec in mcp.specs().items():
        approved, on = mcp.approved(name), spec.get("enabled", True)
        running = now.get(name) or {}
        state = ("работает" if running.get("running") else "не запущен") if approved and on else "выключен" if not on else "ждёт твоего «разрешить»"
        warn = mcp.unpinned(spec)
        acts = [] if approved else [_action("mcp_approve", "Разрешить запуск", id=name, danger=True,
                                            confirm_text=f"Запустить чужую программу: {mcp.command_line(spec)}? Она работает в песочнице, без данных и ключей "
                                                         "Сены, но всё равно это чужой код: разреши, только если знаешь, откуда он.")]
        acts += [_action("mcp_read", "Без вопроса", id=name, input={"label": "Инструменты, которые можно без твоего «да» (через пробел)", "kind": "text",
                                                                   "value": " ".join(spec.get("read", [])), "optional": True}),   # empty: every tool asks again
                 _action("mcp_offline", "С интернетом" if spec.get("offline") else "Без интернета", id=name, on=not spec.get("offline")),
                 _action("mcp_switch", "Выключить" if on else "Включить", id=name, on=not on),
                 _action("mcp_remove", "Убрать", id=name, danger=True, confirm_text=f"Убрать сервер «{spec.get('title') or name}»?")]
        tools = running.get("tools") or {}
        said_read = sorted(t for t, d in tools.items() if (d.get("annotations") or {}).get("readOnlyHint"))
        rows.append({"id": name, "title": spec.get("title") or name, "tone": "warn" if warn or not approved else None, "actions": acts,
                     "subtitle": mcp.command_line(spec) + (f" · {warn}" if warn else ""),
                     "meta": " · ".join(x for x in (state, f"протокол {running['era']}" if running.get("era") else "", f"инструментов {len(tools)}" if tools else "",
                                                    "без вопроса: " + (", ".join(spec.get("read", [])) or "ничего"),
                                                    "без интернета" if spec.get("offline") else "",
                                                    f"сам говорит, что только читает: {', '.join(said_read)}" if said_read else "") if x)})
    return {"id": "mcp", "title": "MCP-серверы", "subtitle": "программы, которые дают Сене инструменты других приложений: запускаются только с твоего разрешения",
            "blocks": [
                {"type": "text", "tone": "muted",
                 "text": "Сервер MCP — чужая программа. Сена запускает её в песочнице: без своих данных, ключей и кода. Всё, что он пишет, Сена считает "
                         "чужим текстом. Действия сервера, кроме тех, что ты пометишь «без вопроса», она делает только после твоего «да»."},
                {"type": "items", "items": rows, "empty": "Серверов пока нет. Добавь первый ниже: имя, название и команду запуска из описания сервера."},
                {"type": "form", "id": "mcp_add", "title": "Добавить сервер", "submit": _action("mcp_add", "Добавить"),
                 "fields": [{"name": "name", "label": "Имя латиницей", "kind": "text", "placeholder": "time"},
                            {"name": "title", "label": "Название для тебя и Сены", "kind": "text", "placeholder": "Время и часовые пояса"},
                            {"name": "command", "label": "Команда запуска (с версией пакета)", "kind": "text", "placeholder": "uvx mcp-server-time@2026.10.19"},
                            {"name": "read", "label": "Без вопроса (имена инструментов через пробел, можно пусто)", "kind": "text"},
                            {"name": "timeout", "label": "Ждать ответа, секунд", "kind": "number", "value": mcp.CALL}]},
            ]}


PANELS = {"confirm": confirm_panel, "works": works_panel, "goals": goals_panel, "lessons": lessons_panel, "exam": exam_panel, "backups": backups_panel,
          "checkup": checkup_panel, "mcp": mcp_panel}
ACTIONS = {
    "goals": {
        "goal_add": _goal_add,
        "goal_step_done": lambda a, v: goals.update(a["id"], done_step=a.get("step", "")),
        "goal_step_add": lambda a, v: goals.update(a["id"], step=str(v.get("value", ""))),
        "goal_note": lambda a, v: goals.update(a["id"], note=str(v.get("value", ""))),
        "goal_close": lambda a, v: goals.update(a["id"], state=a.get("status", "")),   # the panel's own arg stays "status": both apps send it
    },
    "works": {
        "work_note": lambda a, v: work.control(a["id"], "note", str(v.get("value", ""))),
        "work_pause": lambda a, v: work.control(a["id"], "pause"),
        "work_resume": lambda a, v: work.control(a["id"], "resume", str(v.get("value", "") or "")),
        "work_budget": lambda a, v: work.control(a["id"], "resume", budget_usd=_number(v, "value", work.BUDGET, "Сколько ещё можно потратить, $")),
        "work_stop": lambda a, v: work.control(a["id"], "stop"),
    },
    "lessons": {
        "lesson_add": lambda a, v: lessons.note(_agent(a, v), v.get("text", ""), v.get("scope", ""), by="owner"),
        "lesson_keep": lambda a, v: lessons.keep(_agent(a), a.get("id", "")),
        "lesson_edit": lambda a, v: lessons.edit(_agent(a), a.get("id", ""), str(v.get("value", ""))),
        "lesson_retire": lambda a, v: lessons.retire(_agent(a), a.get("id", "")),
    },
    "exam": {
        "exam_run": _exam_run,
        "exam_budget": _exam_budget,
        "exam_weekly": lambda a, v: "Раз в неделю: " + ("включено" if settings.update({"exam": bool(v.get("value"))})["exam"] else "выключено"),
    },
    "confirm": {
        "confirm_yes": lambda a, v: press(a.get("id", ""), True),
        "confirm_no": lambda a, v: press(a.get("id", ""), False),
    },
    "backups": {"backup_now": _backup_now},
    "checkup": {"check_all": lambda a, v: _check(""), "check_one": lambda a, v: _check(a.get("id", ""))},
    "mcp": {
        "mcp_add": lambda a, v: mcp.add(v.get("name", ""), v.get("title", ""), v.get("command", ""), v.get("read", ""), v.get("timeout") or mcp.CALL),
        "mcp_approve": lambda a, v: mcp.approve(a.get("id", "")),
        "mcp_read": lambda a, v: mcp.set_read(a.get("id", ""), str(v.get("value", ""))),
        "mcp_switch": lambda a, v: mcp.switch(a.get("id", ""), bool(a.get("on"))),
        "mcp_offline": lambda a, v: mcp.set_offline(a.get("id", ""), bool(a.get("on"))),
        "mcp_remove": lambda a, v: mcp.remove(a.get("id", "")),
    },
}


TITLES = {"confirm": "Ждут «да»", "works": "Долгие работы", "goals": "Цели", "lessons": "Память агентов", "exam": "Экзамен ума", "backups": "Копии", "checkup": "Проверка системы",
          "mcp": "MCP-серверы"}


def listing():
    """The panels for the apps' menu, with what needs attention."""
    drafts = sum(len(lessons.drafts(a)) for a in AGENT_NAMES)
    badges = {"confirm": len(confirm.waiting()), "lessons": drafts, "goals": len(goals.due()),
              "works": sum(w["status"] in (work.WAITING, work.LIMITED) for w in work.all_works()),
              "checkup": sum(r["status"] == checkup.FAIL for r in checkup.last().get("results", [])),
              "mcp": sum(not mcp.approved(n) for n in mcp.specs())}
    return {"items": [{"id": pid, "title": TITLES[pid], "badge": badges.get(pid, 0)} for pid in PANELS]}


def _clean(value):
    # no nulls on the wire: an absent key is what both apps understand as "not set"
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def panel(pid):
    func = PANELS.get(pid)
    if not func:
        raise ApiError(404, "Такой панели нет")
    return _clean(func())


def act(body):
    """One action from a panel: (what it said, the panel as it is now)."""
    pid, name = body.get("panel"), body.get("action")
    handler = ACTIONS.get(pid, {}).get(name)
    if not handler:
        raise ApiError(400, "Такого действия на этой панели нет")
    args = body.get("args") if isinstance(body.get("args"), dict) else {}
    values = body.get("values") if isinstance(body.get("values"), dict) else {}
    result = handler(args, values)
    if isinstance(result, Failure):   # refused (a bad name, no such goal): not a green "done" — the apps keep what the owner typed
        return {"error": str(result), "panel": panel(pid)}
    return {"result": str(result), "panel": panel(pid)}
