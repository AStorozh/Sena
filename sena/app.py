"""Starting Sena: assembles all the pieces together. To run: ./run.sh or python -m sena."""
import os
import signal
import sys
import threading
import time
import traceback

from sena import config, scheduler

from sena.agents import ag_code, ag_judge, ag_night, programs
from sena.agents.ag_sena import agent as ag_sena
from sena.agents.ag_sena import work
from sena.core import ask_owner, cancel, confirm, events, power, safety, settings, status, store
from sena.phone import convo, server, voice
from sena.memory import goals, history, people, recall, rules, task_log, vault_sync
from sena.telegram import account, api, bot
from sena.world import alerts, news, pages, uptime

NO_RESTART = 78   # the exit run.sh doesn't restart on: the keys are missing or another Sena holds the port — a restart can't help
PORT_WAIT = 10    # seconds the port may still be held by the screen that is handing it over (desk core_start)
STARTED = time.time()   # this run's start: an update record older than it was applied before this run (_update_took)


def _ask_owner_in_telegram(text, image):
    # the agent asks for help: a screenshot and a question to Tema in Telegram, his reply is intercepted by ask_owner.deliver.
    # The question goes into the conversation as it is asked: an answer he gives after it timed out, or one the coordinator
    # passes on, has it in sight (it was written only when his very next message went straight back to the agent). It is an
    # agent's text, which may retell a page or a program: the registry keeps it as someone else's words
    api.notify(text, image=image)   # the app shows the question too; Telegram only if he lets her write there
    try:
        history.reply(config.OWNER_ID, text, read="вопрос агента")
    except Exception as e:   # the question went out: the agent waits for his answer all the same
        events.error("Вопрос агента в переписку", e)


STOP_WAIT = 8   # seconds the running tasks get to finish on their own when Sena is stopped


def _stop(*_):
    # "Stop" from the apps or the terminal: no new work from here on, and a normal exit through main's cleanup. A second
    # Stop while it runs changes nothing: it mustn't cut the writing down of tasks and the stopping of programs half way
    if power.is_stopping():
        return
    power.set_stopping()
    raise SystemExit(0)


def _tell(text):
    # a word to the owner while starting: Telegram may be unreachable (ten tries of a minute each), the start doesn't wait on it
    safety.background(ag_sena.notify_owner, text, what="Сообщение при запуске")


def _finish_running():
    """The running tasks are asked to stop and get a few seconds; whatever still runs goes to the task journal as cut off, and
    the owner's conversation gets a line about it: nothing he asked for vanishes without a trace. A long work's turn is the
    engine's, not his request: the work resumes it by itself (work.resume_after_restart), he isn't asked to."""
    cancel.stop_all(services=True)
    deadline = time.time() + STOP_WAIT
    while status.tasks and time.time() < deadline:
        time.sleep(0.2)
    why = "перезапустилась на новый код" if power.exit_code() == 3 else "меня выключили"   # 3: a restart for a change to her own code
    for ident, task in list(status.tasks.items()):
        status.tasks.pop(ident, None)
        task.update(done=True, end=time.time(), answer=f"Прервано: {why}", **({"service": True} if task.get("work") else {}))
        try:
            task_log.save(task)
            if not task.get("service"):
                history.reply(config.OWNER_ID, f"Задачу «{task['text'][:100]}» прервала: {why}. Скажи, если продолжить.")
        except Exception as e:
            events.error("Остановка: запись прерванной задачи", e)
        events.log("task_end", f"прервано: {why}", tid=task["id"])


def _after_a_fall():
    """At start: if the last run of Sena ended without stopping (a crash, a power cut), the owner's tasks it had started and
    never ended are written down as cut and he hears of them once — a graceful stop does this itself (_finish_running); a fall
    left them nowhere (n8n marks the runs a crash left unfinished when it starts again). Runs before this run logs its own
    start or starts anything: the last "Сена запущена" in the journal is the previous run's, and every open task is its."""
    _, since = events.tally("start", "text", ["Сена запущена"])   # exactly that line: Telegram's reconnects are "start" events too
    if since is None:
        return []
    if events.tally("stop", "text", ["Сена остановлена"], since=since)[0]:
        return []   # it stopped to the end: the cut tasks are written down already ("останавливается" alone is a stop that was cut)
    ended = {e["tid"] for e in events.search("all", kind="task_end", since=since)}
    cut = []
    for e in events.search("all", kind="task_start", since=since):
        if e["tid"] and e["tid"] not in ended and not e.get("service"):
            ended.add(e["tid"])   # a task logged twice is one task
            cut.append(e)
    for e in cut:
        try:
            task_log.save({"id": e["tid"], "text": e["text"], "start": e["t"], "end": time.time(), "steps": [], "answer": "Прервано: Сена упала"})
        except Exception as error:
            events.error("После падения: запись прерванной задачи", error)
        events.log("task_end", "прервано: Сена упала", tid=e["tid"])
    if cut:
        names = "; ".join(f"«{e['text'][:80]}»" for e in cut[:5])
        _tell(f"Я перезапустилась после падения: не закончила {names}. Скажи, какие продолжить.")
    return cut


def _tell_unfinished(left):
    """After a fall: confirmed actions that started and never finished are named to the owner once; Sena doesn't repeat them
    blindly (a message could have gone out already)."""
    ag_sena.notify_owner("Перед выключением начала, но не успела подтвердить: " + "; ".join(text for _, text in left[:5])
                         + ". Проверь, дошло ли, — сама не повторяю.")
    confirm.told([key for key, _ in left])


def _bind():
    """The app's port before anything else: holding it is what makes this the one core. A second one (another run.sh, a start
    from a terminal) stops here, before it opens the Telegram session or writes the notes and the index; so does a start
    without the keys."""
    try:
        config.check()
        return server.make_server(wait=PORT_WAIT)
    except SystemExit as e:   # a restart can't help: run.sh stops trying, the other Sena (or the screen) serves the apps
        print(e, flush=True)   # the last line of the core's output: run.sh tells the owner this reason
        raise SystemExit(NO_RESTART)


def _settle(what, func, *args):
    """One step of settling the last run: its failure is written down and the start goes on (one broken record — a job, a long
    work — stopped Sena from starting at all: exit 1, five quick falls, only the screen left)."""
    try:
        return func(*args)
    except Exception as e:
        events.error(f"Запуск: {what}", e)
        return None


def _settle_last_run():
    """What the last run left is settled before anything of this run starts — so a task, a job or an action of this run is never
    taken for the last one's: its cut tasks are written down, its cut one-off jobs resumed or dropped, its cut long-work turns
    marked, its confirmed actions that never finished named."""
    _settle("после падения", _after_a_fall)
    for job in _settle("прерванные задания", scheduler.resume_cut) or []:   # cut long ago, or cut again and again: not run blindly now
        _tell(f"Задание «{job['text'][:120]}» " + (f"{job['cuts']} раза обрывалось вместе со мной — похоже, оно меня и роняет; не запускаю."
                                                   if job.get("why") == "again" else "прервалось на выключении и уже устарело — не повторяю.")
              + " Поставить заново?")
    _settle("долгие работы", work.start_engine, ag_sena.work_turn, ag_sena.notify_owner)   # a turn cut by the last stop is marked: the next one checks first
    left = _settle("незаконченные действия", confirm.unfinished)   # read now: an action of this run in flight is not the last run's
    if left:
        safety.background(_tell_unfinished, left, what="Незаконченные действия")


def _update_took(started):
    """GRACE seconds after the start: the change to her own code applied before this run has taken (ag_code.confirm_update). One
    applied during this run isn't this run's to confirm: its restart is on the way, and its own run decides whether it took."""
    try:
        if ag_code.PENDING_FILE.stat().st_mtime >= started:
            return False
    except FileNotFoundError:
        return False
    return ag_code.confirm_update(ag_sena.notify_owner)


def _start(httpd):
    _settle_last_run()
    events.log("start", "Сена запущена")   # every task from here on is this run's: the next start's _after_a_fall looks from here
    if settings.get("use_telegram_account"):   # the integration can be turned off from the console; enabled, it connects on the next startup
        safety.background(account.start, what="Вход в аккаунт Телеграма")   # in the background: waiting for the login code doesn't hold up the bot or the screen
    else:
        events.log("start", "аккаунт Телеграма выключен в настройках интеграций")
    ask_owner.set_sender(_ask_owner_in_telegram)
    safety.background(vault_sync.sync_all, what="Хранилище заметок")   # at startup: people, memory, skills, diary
    safety.background(recall.sync_all, what="Указатель памяти")        # at startup: catch up on what was missed, finish computing fingerprints
    safety.background(rules.replan, what="Расписание из правил")
    safety.background(task_log.name_missing, what="Названия задач")   # his tasks from before they had names      # rules whose time needed the model and found none (Sena off, no cloud)
    server.serve(httpd)                   # the phone app: the same assistant, the same memory and history
    voice.start()                         # the live voice: the apps' microphones, her answer in a second
    convo.start()                         # background hearing: reviewing conversations that have gone quiet
    scheduler.start(on_job=ag_sena.run_job, on_heartbeat=ag_sena.heartbeat, on_tidy=ag_night.tidy,
                    # news and pages first, each in its own thread (they don't wait on the rest); the price alerts and the site watch
                    # inline, in the watch's one thread: two overlapping rounds of alerts.check would tell the owner the same alert twice
                    on_watch=lambda: (safety.background(news.watch, ag_sena.news_alert, what="Слежка за новостями"),
                                      safety.background(pages.check, ag_sena.notify_owner, what="Слежка за страницами"),
                                      alerts.check(ag_sena.notify_owner), uptime.watch(ag_sena.notify_owner)),
                    on_weekly=ag_judge.weekly, notify=ag_sena.notify_owner,
                    # a long goal's check-in: a task like any scheduled one; a goal set by a task that had read someone else's
                    # text keeps that mark into its check-in, as a scheduled job's record does
                    on_goal=lambda g: ag_sena.run_job(goals.check_text(g), job={"outside": g["outside"]} if g.get("outside") else None),
                    on_work=work.tick)   # long works: their due turns
    update_check = threading.Timer(ag_code.GRACE, _update_took, args=(STARTED,))   # the update to her own code has taken
    update_check.daemon = True
    update_check.start()
    print("Бот запущен")


def _shut_down():
    power.set_stopping()
    events.log("stop", "Сена останавливается")
    _finish_running()
    programs.stop_all()   # the home model server, the agent's Chrome, her Claude Code, the exam: nothing of hers keeps running
    people.flush()   # the last minute of voice prints, written only in memory otherwise (memory/people.py)
    events.log("stop", "Сена остановлена")
    config.PID_FILE.unlink(missing_ok=True)   # last: until here the apps see her as still stopping, not off
    for db in (events, recall.index):   # flush the WAL tails into the files: the database copies stay complete
        try:
            db.checkpoint()
        except Exception as e:
            print("checkpoint:", e)


def main():
    safety.router()   # every print and traceback is masked from here on, stdout and stderr each (safety.guard_output): no key reaches bot.log
    httpd = _bind()
    store.write_text(config.PID_FILE, str(os.getpid()))   # the apps see the core alive from now on, not "off" with a start button
    config.STARTING_FILE.unlink(missing_ok=True)   # "starting up": the port is held, the handover is over
    config.STOP_FILE.unlink(missing_ok=True)       # an old stop request shouldn't fire on this run
    signal.signal(signal.SIGTERM, _stop)   # "Stop" from the apps: running tasks are finished or written down, her programs stop, the databases close
    code = 1
    try:
        _start(httpd)
        bot.poll()
    except SystemExit:   # the stop signal
        code = power.exit_code()   # 3 — a restart was asked for (her own code changed): run.sh starts her again at once
    except BaseException:
        traceback.print_exc()
    try:
        _shut_down()
    except BaseException:
        traceback.print_exc()
    sys.stdout.flush()
    os._exit(code)   # not the usual exit: it would wait for every worker still in a model call or the browser, the ports open, the pid file gone
