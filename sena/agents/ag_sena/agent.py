"""ag_sena — the coordinator, Sena herself. Receives Tema's message (Telegram, the app, voice), thinks,
hands work out to agents as tools, and replies. This is also where the background turns live: scheduled jobs and checks."""
import hashlib
import re
import threading
import time
from datetime import datetime, timedelta

from sena import config, scheduler
from sena.agents import ag_judge, ag_mac, common
from sena.agents.ag_sena import prompt, toolbox, tools, work
from sena.agents.ag_web import agent as ag_web
from sena.core import ask_owner, brain, cancel, confirm, costs, events, llm, local, moment, power, roles, safety, secrets, settings, status, store
from sena.google import auth as google_auth
from sena.google import gmail
from sena.memory import facts, history, lessons, people, personality, recall, skills, task_log, vault_sync
from sena.telegram import account, api
from sena.world import luma, uptime

FAST_EFFORT = "none"   # voice commands — no reasoning, for an instant reply
# "stop" without the model — only the bare word (optionally with "all"/"please"). "Stop the timer" or "cancel the meeting" are
# ordinary requests: the model handles those, not the global stop. A bare «отмена» is a "no" to the question just asked
# ("Отправить письмо?"), not a stop of every running task: the model reads it too
STOP = re.compile(r"^\s*(стоп|останови(сь)?|хватит|stop)([\s,]+(всё|все|это|их|его|задачу|задачи|работу|сейчас|уже|пожалуйста|please|all|it|now))*\s*[.!]*\s*$", re.I)
# a reminder job needs no model: «Напомнить Теме про созвон», «напомни мне, что завтра выезд», «напоминание: оплатить интернет».
# «Напомни Оле про отчёт» is no reminder for him (a message to her): the model reads it
REMINDER = re.compile(r"^\s*(?:напоминание\s*:|(?:напомни(?:ть)?|напоминай)(?:\s+(?:мне|себе|теме|тёме))?(?=[\s,:—-]+(?:о|об|обо|про|что|чтобы|\w+(?:ть|ти|чь))\b))[\s,:—-]*", re.I)
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
NO = re.compile(r"^\W*(нет|не надо|не нужно|не отправляй|не делай|отмена|отмени|no|nope)\b", re.I)
ANSWER_MOST = 6   # words: a short answer with no question in it answers the agent that waits for him («синий», «код 4471», «вошёл»)
REPLY_TO = re.compile(r"^\[Ответ на сообщение Сены: «(.*?)»\]\s*", re.S)   # his Telegram reply to one of her messages (telegram/bot.py)
HELD_WAIT = 12 * 3600   # s: what a background turn prepared waits for his «да» this long (a night's question is read in the morning)


def _persona():
    # what stays the same from message to message (the cached prefix): rules of work, the catalog of tool groups, personality.
    # People and skills come with each message (_about), so a new voice nearby doesn't change the prompt
    return (prompt.SYSTEM + "\n\n# Инструменты по группам (open_tools)\n" + toolbox.catalog()
            + "\n\n# Кто ты сейчас (твоя личность)\n" + personality.read())


def _instructions():
    return common.instructions(_persona(), with_skills=False)


def _hint(what, find, text):
    # a piece of context the reply can do without: the registry busy or broken costs the hint, not the whole reply
    try:
        return find(text) or ""
    except Exception as e:
        events.error(what, e)
        return ""


def _about(text):
    """Context for one message: the people it is about and the skills close to it."""
    lines = ""
    around = _hint("Люди вокруг Темы", people.relevant, text)
    if around:
        lines += "\nЛюди вокруг Темы (названные в сообщении и недавние; остальных найдёт find):\n" + around
    known = _hint("Похожие навыки", skills.relevant, text)
    if known:
        lines += "\nНавыки, похожие на эту просьбу (полный рецепт — read_skill; остальные найдёт find):\n" + known
    own = _hint("Уроки координатора", lambda t: lessons.for_task("ag_sena", t), text)
    if own:
        lines += "\nТвои уроки из прошлых задач (проверены разбором):\n" + own
    return lines


def now_line():
    # the date, the hour and where the owner is: every turn of hers starts with it (the live voice too)
    now = datetime.now()
    line = f"Сейчас {now:%d.%m.%Y %H:%M}, {WEEKDAYS[now.weekday()]}, часовой пояс Mac {time.strftime('%Z')}."
    if moment.owner_offset and moment.owner_offset != moment.offset():   # the owner is away: his clock differs from the Mac's
        line += f" Тема сейчас в поясе {moment.owner_offset}, у него {moment.local(time.time(), moment.owner_offset):%H:%M}."
    return line


def _finish(answer):
    # the task is over: record it in the experience log and hand it off for review (ag_judge)
    ident = threading.get_ident()
    task = status.end(answer)
    cancel.finish_task(task["id"] if task else ident)
    if task:
        _keep(task)


def _keep(task):
    """A finished task into the experience log and the day's note, and to the review (ag_judge) when it is worth one. What his
    press of «Да» ran is a task of its own and comes here too (core/confirm.finished_hook)."""
    answer = task.get("answer")
    if task.get("work"):   # a long work's turn: the work's own judge reviews it (work.py), and it isn't one of his requests
        task["service"] = True   # so it stays out of the recent tasks his remarks are matched to
    entry = task_log.save(task)
    safety.background(vault_sync.write_day, what="Дневник в хранилище")
    safety.background(task_log.name, entry["id"], what="Название задачи")
    # a review costs money: needed when agents did something, a call failed (raised or declared it: core/tools.Failure), or
    # there are remarks; "what time is it" or an exchange rate isn't worth reviewing
    if task.get("work") or not settings.get("judge"):   # the review can be turned off from the control panel
        return
    worth = (any(s["who"].split("/")[0] != "ag_sena" for s in task["steps"]) or task.get("failures") or task.get("redo_of")
             or str(answer).startswith(("Не вышло", "Не получилось", "Остановилась")) or entry["comments"] or sampled(entry["id"]))
    previous = answered_before(entry)
    if previous:   # his message may be a remark on that answer: read it first, so a redo is reviewed beside its first try
        safety.background(_follow_up, entry, task, previous, worth, what="Разбор задачи")
    elif worth:
        ag_judge.after_task(entry, task.get("trace"), {"code": task.get("code"), "image": task.get("image")})


confirm.finished_hook = _keep   # the task a press of «Да» runs in (core/confirm.confirm_now) is kept like any other


FOLLOW_SIGNAL = 1800   # s: his next message in the same chat this soon after an answer may be a remark on it


def answered_before(entry):
    """The answer he saw just before writing this message (his own words in the same chat, nothing remarked on either), or None."""
    if not entry.get("chat") or entry.get("redo_of"):   # someone else's voice, a schedule; or the remark is written down already
        return None
    before = [e for e in task_log.items if e.get("chat") == entry["chat"] and e is not entry and e["end"] <= entry["start"]]
    last = max(before, key=lambda e: e["end"], default=None)
    return last if last and entry["start"] - last["end"] < FOLLOW_SIGNAL and not last["comments"] else None


def _follow_up(entry, task, previous, worth):
    """His message right after an answer, read as a remark on it: "не то", "я же просил" are feedback on that task even when the
    coordinator didn't write it down (Langfuse's managed evaluator "User Disagreement"). Then this task is reviewed as the redo of
    that one — the first try, his words and the second try seen together, as when the remark is written down (ag_judge.comment)."""
    said = task["text"][:1500]
    try:
        verdict = roles.ask("feedback", prompt.FEEDBACK, f"Ответ Сены на «{previous['text'][:300]}»:\n{previous['answer'][:1500]}\n\n"
                            f"Следующее сообщение Темы:\n{said}", prompt.FEEDBACK_SCHEMA, purpose="несогласие Темы с ответом", max_tokens=200)
    except Exception as e:   # no model to read it now: the task is reviewed as it would be anyway
        events.error("Несогласие Темы с ответом", e)
        verdict = None
    if isinstance(verdict, dict) and verdict.get("disagrees") is True:
        remark = f"{said[:300]} (понято по его следующему сообщению: {str(verdict.get('reason', ''))[:150]})"
        task_log.add_comment(previous["id"], remark)
        if not previous.get("outcome"):   # his remark is the first try's grade already, as when it is written down
            task_log.set_outcome(previous["id"], "done_with_remarks", remark)
        task_log.redo_of(entry["id"], previous["id"])
        worth = True
    if worth:
        ag_judge.after_task(entry, task.get("trace"), {"code": task.get("code"), "image": task.get("image")})


SAMPLE = 0.2   # the share of tasks reviewed whatever they look like: quality is measured on all of them, not only on those that look worth it


def sampled(task_id):
    """A fixed fifth of the tasks, chosen by the task's id (the same task is always in or out, the 10 % set lies inside the
    20 % one): the review sees tasks the "worth it" rule skips — Sena answering alone (Langfuse's deterministic sampling)."""
    digest = hashlib.sha256(f"sena:review-sample:v1\0{task_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2 ** 64 < SAMPLE


def _stop_everything(chat_id, text):
    # "stop" — immediately, no model: every task of his that is running now, and a long work's turn (Sena's own, but his stop
    # pauses it); other service work goes on: it is no task of his
    me = cancel.current_task()
    stopping = [t for ident, t in list(status.tasks.items()) if ident != me and (not t.get("service") or t.get("work"))]
    tools.stop_all()
    history.ask(chat_id, text, "")
    answer = (f"Остановила ({len(stopping)})." + (" Долгая работа на паузе: продолжу, когда скажешь «продолжай»." if any(t.get("work") for t in stopping) else "")
              if stopping else "Останавливать нечего.")
    history.reply(chat_id, answer)
    return answer


def _handed(chat_id, text, question):
    """His message went straight to the agent that waits for it: his answer stays in the correspondence like any message (the
    question is written there when it is asked, app._ask_owner_in_telegram), and the reply names who got it."""
    to, who = common.agent_word(question.get("who"), 1), common.agent_word(question.get("who"))
    history.ask(chat_id, text, "")
    answer = f"Передала {to} как ответ на его вопрос. Если это было новое поручение — повтори его, когда {who} закончит."
    history.reply(chat_id, answer)
    return answer


def _answer_for_agent(text, foreign=(), attachments=None):
    """(question id or None, the answer, whether it surely is one) when his message by itself answers an agent that waits for
    him: a Telegram reply to that very question, an answer naming it ("[ab12] …"), or a short answer with no question in it.
    Otherwise None, and the coordinator decides (it sees the questions and has answer_agent): «какая погода завтра?» while
    Claude Code waits on «Какой цвет кнопки?» is a new request, not the colour. Someone else's text or a file in the message is
    never passed on as his words."""
    asked = ask_owner.waiting()
    if not asked or foreign or attachments:
        return None
    reply = REPLY_TO.match(text)
    if reply:   # the quote is the question (or the part of it he quoted)
        quoted = reply.group(1).strip()
        question = next((q for q in asked if quoted and (q["question"][:80].strip() in quoted or quoted[:80] in q["question"])), None)
        answer = text[reply.end():].strip()
        return (question["id"], answer, True) if question and answer else None
    named = ask_owner.NAMED.match(text)
    if named:
        qid = named.group(1).lower()
        return (qid, text[named.end():].strip() or text, True) if qid in {q["id"] for q in asked} else None
    if len(text.split()) <= ANSWER_MOST and "?" not in text:
        return None, text, False
    return None


def _asked_for_yes():
    """What his message may be the «да» to: a prepared action asked about lately that is answered on the next turn only. A long
    work's or a code change's hold (kept for a day and more, confirm.keep) doesn't close the way to an agent that asks him now."""
    return [w for w in confirm.waiting() if not w.get("later")]


def _bare_answer(text):
    # «да», «ок, отправляй», «нет, не надо»: his answer to her own question, nothing in it to look up in the archive
    return len(text.split()) <= FOLLOW_UP and bool(confirm.YES.match(text) or NO.match(text))


def ask(chat_id, text, fast=False, attachments=None, outside=None, own=True, foreign=()):
    """Tema's message -> Sena's reply. fast — for voice commands ("Sena, ..."): the fast model, an almost instant reply.
    attachments — parts for the model (photos, PDFs, file text): visible only on this turn, the correspondence keeps a note about the file instead.
    outside — why this message may not be his own words (a voice command not in his voice): the task starts as if it had read foreign text.
    own=False — his words, but not checked to be his (a voice turn too short to tell): they can't confirm a prepared action.
    foreign — what someone else's text inside his own message came from (a forwarded message, a file attached in the app): the task
    is marked as having read it, while his own words still count as his."""
    cancel.new_task()   # first: a stop pressed while this task is still starting is kept for it (core/cancel.py)
    if not text.strip():
        return "Не расслышала, повтори, пожалуйста."
    mine = own and not outside   # his own words in his own voice: only they stop everything at once or answer an agent directly
    if mine and STOP.match(text) and len(text) < 40:
        return _stop_everything(chat_id, text)
    text = secrets.cards(text)   # a card number never lands in the correspondence, the search, an agent or the cloud model (prompt: «Секреты»)
    # an agent waits for his answer: what answers it by itself goes straight to it — unless it may be the «да» to what he was
    # asked just before, then the coordinator, who sees both, decides whose answer it is (answer_agent or confirm_send)
    found = _answer_for_agent(text, foreign, attachments) if mine else None
    handed = bool(found) and (found[2] or not _asked_for_yes()) and ask_owner.deliver(found[1], found[0])
    if handed:
        return _handed(chat_id, text, handed)
    asked = ask_owner.waiting()   # several agents are waiting and his words don't say which: the coordinator sees them all
    status.new_turn(text if mine else None)   # someone else's voice, or one too short to know, can't say "yes" for him
    marks = []   # someone else's words among the recalled lines: the task is marked once it begins below
    context = _context(chat_id, text, fast, asked, marks)
    turn = history.ask(chat_id, text, context, outside)   # only the latest messages go to the model, the full history is kept for the app
    if attachments:   # before the day's context; the message keeps the labels the correspondence gave it (a date, «не голос Темы»)
        turn[-2] = {"role": "user", "content": [{"type": "input_text", "text": turn[-2]["content"]}, *attachments]}
    status.begin(text)
    for source in [*foreign, *marks]:   # someone else's text came inside his message or the recalled context: the task has read it
        status.touched(source)
    if outside:
        status.touched(outside)
    else:   # his own words: his next message here may be read as a remark on this answer (answered_before)
        status.current()["chat"] = chat_id
    answer = "Ошибка"
    try:
        if cancel.is_cancelled():   # stopped while it was still starting: no model is asked
            answer = "Остановлено по просьбе Темы."
        else:
            prefs = settings.load()  # reasoning effort and reply length — from the app settings
            model = config.MODEL_VOICE if fast else config.MODEL_MAIN
            effort = FAST_EFFORT if fast else prefs["effort"]
            answer = brain.run(model, effort, _instructions(), turn, tools.for_brain(), tools.FUNCTIONS,
                               describe=tools.describe, name="ag_sena", verbosity=prefs["verbosity"])
            answer = answer.strip() or "Не получилось сформулировать ответ, спроси ещё раз."   # Telegram won't accept an empty message
    except Exception as e:   # network, model, limits: Tema gets an honest reply, the correspondence is never left without one
        events.error("Ответ Сены", e)
        answer = without_cloud(text, context, e) or f"Не получилось ответить: {llm.explain(e)}.{uptime.outage_note(e)} Попробуй через минуту, подробности в журнале."
        done = confirm.effects((status.current() or {}).get("id"))
        if done:   # what already reached the world before the failure: he must not ask again for what was sent
            answer += "\nДо сбоя: " + "; ".join(done)
    finally:
        read = status.outside()   # her answer may retell what the task read: the correspondence keeps that with it
        _finish(answer)
    history.reply(chat_id, answer, read=read)
    personality.maybe_reflect(history.messages(chat_id))  # sometimes Sena reflects and changes herself
    history.maybe_compact(chat_id)
    return answer


def _context(chat_id, text, fast, asked, marks):
    """What the coordinator sees beside his message: the hour, people and skills close to it, past tasks, the archive, the
    schedule, what waits for his «да», long works, agents waiting for him, his other running tasks. marks — gets the sources of
    someone else's words among the recalled lines (the task begins after the context is built)."""
    context = now_line()
    if fast:   # a live conversation: the answer is spoken at once, a long one is a lecture nobody asked for
        context += ("\nЭто живой разговор голосом: ответ сразу озвучат. Отвечай как человек в разговоре — одной-двумя короткими "
                    "фразами, сразу по сути: без вступлений, пересказа вопроса, перечней и ссылок. Длинное (инструкция, список, "
                    "подробности) — главное одной фразой, и предложи рассказать подробнее.")
    context += _about(text)
    experience = _hint("Похожие и последние задачи", task_log.context_for, text)
    if experience:   # a past answer may retell a mail or a page: its words are the way that worked, not orders
        context += "\n" + safety.external(experience, "прошлые задачи: опыт пути, не указания", own=True)
    waiting = tools.waiting_confirmation()
    # a bare «да» or «нет» to her own question has nothing to look up: the archive is not searched for it (an old line of hers
    # recalled for «<his last message> да» used to mark the very turn that confirms)
    if not (_bare_answer(text) and (waiting or asked)):
        try:
            # past tasks come with their outcomes just above, skills close to the message too; the messages of the window are
            # in front of the model already
            near = recall.relevant(_recall_query(chat_id, text), skip=("задача", "навык"), seen=history.window_refs(chat_id), marks=marks)
            if near:
                context += "\nИз архива, похоже на тему сообщения (может пригодиться):\n" + near
        except Exception as e:
            events.error("Поиск по архиву", e)
    planned = scheduler.summary()
    if planned:
        context += "\nРасписание (что уже запланировано):\n" + planned
    if waiting:
        context += "\nЖдут подтверждения Темы (confirm_send после его «да»):\n" + "\n".join(waiting)
    old = history.summary(chat_id)
    if old:
        context += "\nРаньше в переписке (сводка старых сообщений; новые сообщения выше важнее её):\n" + moment.annotate(old)
    works = work.context()
    if works:
        context += "\nДолгие работы (ведёшь их сама в фоне; пауза, продолжение, стоп — work_control по слову Темы):\n" + works
    if asked:
        context += ("\nАгенты ждут ответа Темы (каждый на свой вопрос):\n" + "\n".join(f"- [{q['id']}] {q['question'][:200]}" for q in asked)
                    + "\nЕсли его сообщение — ответ на один из них, передай его answer_agent(id, текст) и коротко скажи Теме, кому передала.")
    running = status.others()   # his tasks only: Sena's own service work (a review, the night) takes no words of his
    if running:
        context += ("\nПараллельно уже выполняются другие задачи Темы:\n- " + "\n- ".join(running) +
                    "\nНовое сообщение может относиться к одной из них или быть новой задачей — пойми по смыслу. Уточнение к идущей («пропусти рекламу», "
                    "«не тот, второй») — tell_task с её id; «стоп» — stop_all.")
    return context


FOLLOW_UP = 4   # words or fewer: a follow-up ("а когда?", "а сколько?") that names nothing to look for on its own


def _recall_query(chat_id, text):
    """What the automatic recall looks for: the message, and for a short follow-up the owner's previous message with it — "а
    когда?" alone finds nothing (LlamaIndex's chat memory searches with the last messages, not the latest one only)."""
    if len(text.split()) > FOLLOW_UP:
        return text
    earlier = [m["content"] for m in history.messages(chat_id) if m.get("role") == "user" and isinstance(m.get("content"), str)]
    return f"{earlier[-1]} {text}" if earlier else text


def without_cloud(text, context, error):
    """The cloud brain is out of reach (no internet, no money, overloaded, the daily budget is spent): the home model answers
    from memory instead of a bare error — no tools, no internet, and it says so. None if the failure isn't the cloud's or
    there is no home model."""
    why = llm.explain(error)
    if not llm.outage(error) or not local.installed()["chat"]:   # a bug of ours is not an outage: don't pretend
        return None
    try:
        found = recall.find_rows(text, k=6, with_events=False).get("rows", [])
        notes = "\n".join(f"- {r['text'][:300]}" for r in found) or "ничего"
        reply = local.chat(prompt.OFFLINE.format(why=why), f"{context}\n\n# Память о Теме\n{facts.read()}\n\n# Нашлось в памяти по сообщению\n{notes}"
                                                          f"\n\n# Сообщение Темы\n{text}", max_tokens=500, purpose="ответ без облака")
    except Exception as e:
        events.error("Ответ без облака", e)
        return None
    if not reply:
        return None
    events.log("message_out", reply, channel="home", local=True)
    return f"Облако недоступно ({why}), отвечаю сама, по памяти:\n{reply}"


def _background(instruction, model, effort, tool_specs, functions, quiet=False):
    # a background turn with no message from Tema (a scheduled job or a check). The reply goes to Tema in Telegram and into the history.
    context = f"{now_line()}\n{instruction}" + _about(instruction)
    planned = scheduler.summary()
    if planned:
        context += "\nРасписание:\n" + planned
    status.join_turn()   # what it prepares for the owner's "yes" is answered by his next message
    turn = history.background_turn(config.OWNER_ID, context)
    # it sums up before the scheduler gives its place to the next jobs (scheduler.RUN_MOST), instead of running on unseen
    answer = brain.run(model, effort, _instructions(), turn, tool_specs, functions,
                       describe=tools.describe, name="ag_sena", max_seconds=scheduler.RUN_MOST)
    # what it prepared for his "yes" (a message to someone the job is about) waits for him with its buttons, and his «да» on any
    # later turn counts: nobody was there to answer the very next one
    held = confirm.keep((status.current() or {}).get("id"), HELD_WAIT)
    said = answer.strip()
    plain_ok = said.strip(".").upper() in ("OK", "ОК")
    if not held and (not said or (quiet and plain_ok)):
        return answer   # don't send Tema an empty reply or a plain "all fine"
    if held and (not said or plain_ok):   # it said nothing of what waits for him: the names say it
        said = "Подготовила, ждёт твоего «да»: " + "; ".join(name for _, name in held)
    notify_owner(said, held)
    return answer


UNATTENDED = ("Тема сейчас не рядом: никто не уточнит и не подтвердит по ходу, доведи до результата тем, что есть. Ответ — сам результат, "
              "а не план; спрашивай Тему, только если задание само просит спросить. Сообщение или письмо человеку не пропускай: подготовь его "
              "и спроси Тему одной фразой — кнопки «Да / Нет» придут к нему вместе с ответом. Неизменное и уже известные препятствия не "
              "пересказывай. Задание — проверка, сообщать нечего и спрашивать не просят — ответь ровно OK.")


def _reminder(text, late_hours=0):
    """A reminder in her own words, without the model: «Напомнить Теме про созвон в 10:00» → «Напоминаю про созвон в 10:00.»,
    «напомни, что завтра выезд» → «Напоминаю, что завтра выезд.», «напомни позвонить маме» → «Напоминаю: позвонить маме.»
    A late one says it is late."""
    rest = REMINDER.sub("", text, count=1).strip().rstrip(".!")
    joint = " " if re.match(r"(?:о|об|обо|про)\s", rest, re.I) else ", " if re.match(r"(?:что|чтобы)\s", rest, re.I) else ": "
    return f"Напоминаю{joint}{rest}" + (f" (с опозданием на {late_hours} ч: Mac спал)" if late_hours else "") + "."


def run_job(text, at=None, job=None):
    # a scheduled job fired: a full-fledged task (visible in "Processes", gets reviewed). at — when it was scheduled for;
    # job — its record: what it answered last time (a repeating job continues from there instead of repeating itself)
    cancel.new_task()
    status.begin(f"По расписанию: {text}")
    if job and job.get("outside"):   # asked for by a task that had read someone else's text: its every run is as marked as that one
        status.touched(job["outside"])
    answer, late_hours = "Ошибка", 0
    instruction = f"Сработало задание по расписанию: «{text}». Выполни его сейчас. Напоминание — напомни Теме коротко. {UNATTENDED}"
    last = (job or {}).get("last")
    if last:   # Hermes Agent's continuity for recurring jobs: the previous meaningful answer, as data
        instruction += (f"\nПрошлый раз по этому заданию ({datetime.fromtimestamp(last['t']):%d.%m %H:%M}) ты ответила: «{last['text']}». "
                        "Не повторяй уже сказанное, продолжай с того места.")
    if (job or {}).get("cut"):
        instruction += "\nПрошлый запуск этого задания прервал перезапуск Сены: сначала проверь, что он успел сделать, и не повторяй это вслепую."
    if at:
        try:
            late = datetime.now() - datetime.strptime(at, "%d.%m.%Y %H:%M")
            if late > timedelta(hours=1):
                late_hours = int(late.total_seconds() // 3600)
                instruction += (f" Назначено было на {at}, опоздание {late_hours} ч (Mac спал). Не делай вид, что вовремя: если смысл потерян "
                                "(разбудить, напомнить к сроку) — скажи Теме, что пропустила, и предложи, что делать.")
        except ValueError as e:
            events.error("Задание по расписанию: разбор времени", e)
    try:
        if REMINDER.match(text):   # a reminder needs no model: her own words go out at once, without a paid call
            answer = _reminder(text, late_hours)
            if settings.get("telegram_notify") and not (api.send_text(answer) or {}).get("ok"):   # Telegram never took it: the scheduler tries again in ten minutes
                answer = "Напоминание не ушло: Телеграм не ответил"
                raise RuntimeError("Телеграм не принял напоминание: нет связи")
            history.reply(config.OWNER_ID, answer)
        else:
            answer = _background(instruction, config.MODEL_MAIN, settings.get("effort"), tools.for_brain(), tools.FUNCTIONS, quiet=True)
    finally:
        _finish(answer)
    return answer


WORK_TOOLS = ("work_plan", "work_done", "work_need", "work_list")   # a work's turn has its own tools at hand


def work_turn(w):
    """One turn of a long work (agents/ag_sena/work.py): the coordinator with every tool, a fresh context built from the work's
    record, nothing sent to the owner — the engine tells him what matters. Returns (the turn's report, the task id, how the turn
    ended: ok, cut — stopped by the owner or Sena going down, failed — the cloud or the money, day — the day's budget is spent)."""
    cancel.new_task()
    # Sena's own work, not a request of his: a fall or a stop in the middle doesn't ask him to resume it (app._after_a_fall reads
    # the journal's mark), the engine resumes it itself; his «стоп» still reaches it (tools.stop_all)
    status.begin(f"Работа «{w['title']}»: ход {w['turns']}", service=True)
    status.current()["work"] = w["id"]
    tid = status.current()["id"]
    work.turn_began(w["id"], tid)
    answer, outcome = "Ошибка", "ok"
    try:
        if w.get("outside"):   # the work was asked for by a task that had read someone else's text: every turn of it is marked
            status.touched(w["outside"])
        # an earlier turn read someone else's text, and its checkpoint carries what it read into this turn's brief: this turn
        # is as marked as that one, or a page read by turn N would steer turn N+1 past the "yes" (README, «Защита»)
        earlier = next((r["text"] for t in w.get("tasks", []) if t != tid for r in events.search("all", kind="outside", tid=t, limit=1)), None)
        if earlier:
            status.touched(f"прошлый ход работы: {earlier}")
        context = f"{now_line()}\n{work.brief(w)}" + _about(w["goal"])
        waiting = tools.waiting_confirmation()
        if waiting:
            context += "\nЖдут подтверждения Темы (confirm_send после его «да»):\n" + "\n".join(waiting)
        status.join_turn()
        turn = history.background_turn(config.OWNER_ID, context)
        answer = brain.run(config.MODEL_MAIN, settings.get("effort"), _instructions(), turn, tools.for_brain(WORK_TOOLS), tools.FUNCTIONS,
                           describe=tools.describe, name="ag_sena").strip() or "Ход закончился без отчёта."
        if cancel.is_cancelled() or power.is_stopping():
            outcome = "cut"
    except Exception as e:   # the cloud, the money, the day's budget: the engine decides what it means for the work
        events.error(f"Ход работы «{w['title']}»", e)
        limit = type(e).__name__ == "LimitReached"
        answer = str(e) if limit else f"Не получилось ответить: {llm.explain(e)}"
        outcome = "cut" if power.is_off() or power.is_stopping() else "day" if limit else "failed"
    finally:
        _finish(answer)
    return answer, tid, outcome


def notify_owner(text, held=(), quiet=False, read=None):
    # a message with no question from Tema (e.g. a price alert): to Telegram and into the correspondence, so Sena remembers what
    # she said and his reply to it has its context. held — [(id, name)] of prepared actions it asks a "yes" for: they come with
    # their «Да / Нет» buttons; quiet — without a sound (what can wait for the morning); read — the foreign text it retells
    api.notify(text, api.buttons(list(held)) if held else None, quiet=quiet)
    history.reply(config.OWNER_ID, text, read=read)


def news_alert(text):
    # the news watch found a strong new story on a topic Tema watches: Sena decides whether it is worth telling and tells it
    # in her own words; "OK" — nothing is sent
    cancel.new_task()
    status.begin("Новость по теме, за которой слежу", service=True)
    status.touched("новостные ленты")   # the headlines were fenced before this task existed: the mark is set here
    answer = "Ошибка"
    try:
        answer = _background(f"{text}\n{UNATTENDED}", config.MODEL_MAIN, settings.get("effort"), tools.for_brain(), tools.FUNCTIONS, quiet=True)
    finally:
        _finish(answer)


_heartbeat_sign = None   # what decides whether there's a reason to call the model: the list of chats with unread messages and upcoming events
TOLD_FILE = config.STATE_DIR / "heartbeat_told.json"   # what the hourly check told the owner: the model sees it and doesn't tell
TOLD_KEPT, TOLD_MOST = 86400, 10                       # the same mail twice — after a restart too (it lived in memory only)


def _told():
    now = time.time()
    return [(t, m) for t, m in store.load_json(TOLD_FILE, []) or [] if now - t < TOLD_KEPT][-TOLD_MOST:]


CHATS_READ = 8   # how many chats with unread messages the hourly check reads, the most recent first
UNREAD_READ = 10  # and how many of the last messages in each


def _gather(prefs, login):
    # what the hourly check looks at, collected by code: chats with unread messages (only those are opened), unread mail,
    # what is due within two hours. Other people's words — they go only to the home model (role "heartbeat")
    lines = []
    if prefs["use_telegram_account"] and account.saved():   # a kept sign-in that didn't connect at the start connects here
        try:
            for name, count in account.unread(40)[:CHATS_READ]:
                lines.append(f"Чат «{name}» (непрочитано {count}):\n{account.read_chat(name, limit=min(count, UNREAD_READ))}")
        except RuntimeError:   # not connected this hour either (no network, the sign-in withdrawn): the next hour tries again
            pass
        except Exception as e:
            events.error("Фоновая проверка: чаты", e)
    if login == "ok":
        try:
            mail = gmail.search("is:unread newer_than:1d", limit=10)
            if not mail.startswith("Писем по этому запросу нет"):
                lines.append(f"Почта, непрочитанное:\n{mail}")
        except Exception as e:
            events.error("Фоновая проверка: почта", e)
    soon = scheduler.due_within(hours=2) + (_luma_soon(2) if prefs.get("use_luma") else [])
    if soon:
        lines.append("Дела на ближайшие два часа:\n" + "\n".join(soon))
    return lines


def _luma_soon(hours):
    """Luma's events and timed tasks of today in the next hours: meetings live there (event_add), not in the schedule. Only when
    Luma is up — the hourly check never opens it — and any failure just leaves them out."""
    try:
        day = luma.call("get_today", {}) if luma.is_up() else None
    except Exception as e:
        events.error("Фоновая проверка: Luma", e)
        return []
    if not isinstance(day, dict):
        return []
    now = datetime.now()
    timed = [(e.get("time"), e.get("title")) for e in day.get("events") or [] if isinstance(e, dict)]
    timed += [(t.get("dueTime"), t.get("title")) for t in (day.get("plan") or {}).get("tasks") or []
              if isinstance(t, dict) and t.get("due") in (None, day.get("date"))]   # an overdue task's time is another day's
    soon = []
    for at, title in timed:
        try:
            when = datetime.combine(now.date(), datetime.strptime(str(at), "%H:%M").time())
        except ValueError:   # an all-day event, a task without a time
            continue
        if now <= when <= now + timedelta(hours=hours):
            soon.append(f"{when:%d.%m.%Y %H:%M} — {title} (Luma)")
    return soon


def heartbeat():
    """Once an hour: is there anything to tell Tema right now? The code reads what changed; the home model decides; nothing is
    sent unless it said so. The model is called only when the picture changed since the last check."""
    global _heartbeat_sign
    vault_sync.sync_if_changed()   # once an hour: people, memory, skills, rules — into notes, if they changed
    prefs = settings.load()   # a Telegram account or Google integration turned off means the check doesn't even read it, not just hide it from the model
    login = google_auth.state() if prefs["use_google"] else "off"
    if login == "expired" and google_auth.mark_notified():   # into the correspondence too: his «войди» is answered knowing why
        notify_owner("Вход в Google устарел или отозван, почта и таблицы сейчас недоступны. Войти заново: скажи мне «войди в Google» — на Маке откроется окно входа.")
    found = _gather(prefs, login)
    sign = hashlib.md5("|".join(found).encode()).hexdigest()
    if not found or sign == _heartbeat_sign:
        _heartbeat_sign = sign
        events.log("heartbeat", "без изменений, модель не звала" if found else "непрочитанного и близких дел нет")
        return
    told = [f"- {datetime.fromtimestamp(t):%H:%M} {m}" for t, m in _told()]
    text = (f"{now_line()}\n\n# Память о Теме\n{facts.read()}\n\n# Сейчас\n" + "\n\n".join(found)
            + ("\n\n# Уже сообщила Теме за сутки (то же самое не повторяй)\n" + "\n".join(told) if told else ""))
    try:
        with costs.label("фоновая проверка"):
            reply = roles.ask("heartbeat", prompt.HEARTBEAT, text, prompt.HEARTBEAT_SCHEMA, purpose="фоновая проверка", max_tokens=300)
    except Exception as e:   # no home model (and the role isn't moved to the cloud): the check waits, other people's messages stay home
        events.error("Фоновая проверка", e)
        return   # the picture stays unseen: the next hour looks at it again (n8n moves its cursor only with a finished run)
    _heartbeat_sign = sign
    message = (reply.get("text") or "").strip() if isinstance(reply, dict) else ""
    if isinstance(reply, dict) and reply.get("notify") and message:
        notify_owner(message, read="фоновая проверка")   # it retells other people's chats and mail
        store.save_json(TOLD_FILE, [*_told(), (time.time(), message[:300])][-TOLD_MOST:])
    events.log("heartbeat", message if message and reply.get("notify") else "сообщать нечего", notified=bool(message and reply.get("notify")))


DIRECT = {"ag_web": "браузер", "ag_mac": "компьютер"}   # who can be given tasks directly from the app, bypassing Sena


def ask_agent(chat_id, agent, text, foreign=()):
    """A task given directly to an agent from the app: no coordinator, but still treated as an ordinary task — in the correspondence, the experience log, and the review.
    The task is tagged with the agent's name, so Sena later understands this was done without her. foreign — as in ask()."""
    if agent not in DIRECT:
        return f"Напрямую можно поручать только {', '.join(DIRECT)}"
    if not text.strip():
        return "Пустое поручение."
    if agent == "ag_web" and not settings.get("use_browser"):   # Chrome disabled in the integrations doesn't open even for a direct task
        return "Браузер выключен в интеграциях: включи его там, чтобы поручать ему задачи."
    run = {"ag_web": ag_web.run, "ag_mac": ag_mac.run}[agent]
    text = secrets.cards(text)
    cancel.new_task()
    status.new_turn()
    history.ask(chat_id, f"[{agent}] {text}", "")
    status.begin(f"[{agent}] {text}")   # its task_start in the journal carries the agent's name in the text
    for source in foreign:   # a file attached in the app: someone else's text, as in ask()
        status.touched(source)
    answer = "Ошибка"
    try:
        answer = str(run(text)).strip() or "Агент ничего не ответил."
    except Exception as e:
        events.error(f"Прямое поручение {agent}", e)
        answer = f"Не вышло: {llm.explain(e)}"
    finally:   # the task always ends: one left in status.tasks would hold back an update and a restart for good
        read = status.outside()
        _finish(answer)
    history.reply(chat_id, answer, read=read)
    return answer
