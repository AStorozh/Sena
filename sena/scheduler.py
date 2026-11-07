"""Scheduling: Sena wakes herself up at the right time.
- Tema's jobs ("every day at 8:00 ...", "remind me tomorrow at 10 ...") — data/state/jobs.json;
- once an hour during the day — a background check: is there anything important to tell Tema;
- every few minutes — the watch round (price alerts, the site, news and pages; named "Сигналы по цене" in the app);
- once a day at night — cleaning up memory and skills; once a week — the review; long goals' check-ins and long works' turns.
What exactly to do when something is due is decided by whoever called start: on_job, on_heartbeat, on_tidy, on_watch, on_weekly,
on_goal, on_work. Every part of the half-minute pass is a stage of its own: one that fails doesn't stop the others."""
import threading
import time
import uuid
from datetime import datetime, timedelta

from sena import config
from sena.core import events, llm, power, safety, settings, status, store
from sena.core.tools import Failure, string, tool
from sena.memory import goals

FORMAT = "%d.%m.%Y %H:%M"
REPEATS = ["once", "daily", "weekdays", "weekly"]
# how often the check and price alerts run, active hours, the cleanup hour, the weekly review's day and hour — all in settings
# (settings: heartbeat_every_min, active_from/to, watch_every_min, tidy_hour, weekly_day/weekly_hour); the app edits them in "Rules"
_lock = threading.Lock()


def add(text, at, repeat="once", late="run", outside=None):
    """A new job: (job, None) or (None, why it didn't work). late — what a repeating job does with a time the Mac slept through:
    "run" — late (a reminder still matters), "skip" — wait for the next time (yesterday's digest at noon is noise).
    outside — the foreign text read by the task that asked for the job (by default the calling task's own mark, status.outside()):
    the job keeps it as "outside" and its every run starts marked (agents/ag_sena/agent.run_job), so a mail that talks the model
    into scheduling something can't have it run at night as an unmarked task, past the owner's "yes" (README, «Защита»)."""
    try:
        when = datetime.strptime(str(at).strip(), FORMAT)
    except ValueError:
        return None, f"Не поняла время «{at}», нужно ДД.ММ.ГГГГ ЧЧ:ММ"
    if repeat not in REPEATS:
        return None, f"repeat — одно из: {', '.join(REPEATS)}"
    if repeat == "once" and when < datetime.now():
        return None, "Это время уже прошло"
    while repeat != "once" and when <= datetime.now():   # a repeating job starts from the next occurrence, not right now
        when = _next(when, repeat)
    outside = outside or status.outside()
    with _lock:
        jobs = store.load_json(config.JOBS_FILE, [])
        job = {"id": uuid.uuid4().hex[:6], "text": text, "at": when.strftime(FORMAT), "repeat": repeat, **({"late": "skip"} if late == "skip" else {}),
               **({"outside": str(outside)[:300]} if outside else {})}
        jobs.append(job)
        store.save_versioned(config.JOBS_FILE, jobs, indent=1)
    return job, None


def schedule(text, at, repeat="once", late="run"):
    job, error = add(text, at, repeat, late)
    return Failure(error) if error else f"Запланировано [{job['id']}]: {job['at']} ({repeat}) — {text}"


def unschedule(id):
    with _lock:
        jobs = store.load_json(config.JOBS_FILE, [])
        kept = [j for j in jobs if j.get("id") != id]
        if len(kept) == len(jobs):
            return Failure("Нет такого задания")
        store.save_versioned(config.JOBS_FILE, kept, indent=1)
    return "Удалено"


def jobs():
    """All jobs as data (for the screen)."""
    return store.load_json(config.JOBS_FILE, [])


def toggle(id, enabled):
    """Turn a job off without deleting it, or back on: a disabled one doesn't fire. When re-enabling a repeating job whose
    time has passed, we move it to the next occurrence (otherwise it would fire right away for every missed one)."""
    with _lock:
        items = store.load_json(config.JOBS_FILE, [])
        job = next((j for j in items if j["id"] == id), None)
        if job is None:
            return Failure("Нет такого задания")
        job["enabled"] = bool(enabled)
        if enabled and job.get("repeat", "once") != "once":
            try:
                when = datetime.strptime(job["at"], FORMAT)
                while when <= datetime.now():
                    when = _next(when, job["repeat"])
                job["at"] = when.strftime(FORMAT)
            except (KeyError, ValueError, TypeError):
                pass
        store.save_versioned(config.JOBS_FILE, items, indent=1)
        return job


def due_within(hours=2):
    """The texts of jobs scheduled for the next few hours (for the background check)."""
    now = datetime.now()
    out = []
    for j in jobs():
        try:
            when = datetime.strptime(j["at"], FORMAT)
        except (KeyError, ValueError, TypeError):
            continue
        if j.get("enabled", True) and now <= when <= now + timedelta(hours=hours):
            out.append(f"{j['at']} — {j['text']}")
    return out


def summary():
    # for Sena's context: what's scheduled
    # something the owner disabled still sits in the schedule but won't fire: the model should know it's not active
    return "\n".join(f"- [{j.get('id')}] {j.get('at')} ({j.get('repeat', 'once')}) — {j.get('text')}" + ("" if j.get("enabled", True) else " (ВЫКЛЮЧЕНО, не сработает)")
                     for j in jobs() if isinstance(j, dict))


def _next(when, repeat):
    step = timedelta(days=7 if repeat == "weekly" else 1)
    when += step
    while repeat == "weekdays" and when.weekday() >= 5:
        when += step
    return when


def tidy_due(now, state, hour=None):
    # cleanup once a day, not before the hour set in settings; the Mac sleeps at night, so "it's time" means "it's time and it wasn't done today"
    hour = settings.get("tidy_hour") if hour is None else hour
    return now.hour >= hour and state.get("tidy") != now.strftime("%Y-%m-%d") and state.get("tidy_retry", 0) <= time.time()


def last_weekly(now, day=None, hour=None):
    # the most recent weekly-review slot (day of week and hour from settings) that has already arrived: its date is the review's key
    day = settings.get("weekly_day") if day is None else day
    hour = settings.get("weekly_hour") if hour is None else hour
    last = (now - timedelta(days=(now.weekday() - day) % 7)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return last if now >= last else last - timedelta(days=7)


def weekly_due(now, state):
    # once a week after the scheduled day and hour; a missed one (the Mac was asleep) is caught up on the first wake-up
    return state.get("weekly") != last_weekly(now).strftime("%Y-%m-%d") and state.get("weekly_retry", 0) <= time.time()


RESUME_HOURS = 6     # a one-off job cut by a restart is run again within this long, with a note to check first; older ones are told
MOST_CUTS = 2        # a job cut this many times with Sena is not run again: it may be what brings her down (n8n's crash guard)
LATE_GRACE = 2 * 3600   # s: a repeating "skip" job this late is passed by, not run
JOBS_AT_ONCE = 2     # scheduled jobs that run at the same time; the rest wait their turn (the owner's own messages never do)
_job_slots = threading.BoundedSemaphore(JOBS_AT_ONCE)
RUN_MOST = 30 * 60   # s a scheduled run keeps its place: one hung longer lets the next jobs start (it goes on by itself)
FAILS_TOLD = (1, 3)  # a repeating job's failures in a row the owner hears about: the first and the third, not every one
FAILS_OFF = 10       # in a row, and the job is switched off with a word to the owner (OpenClaw's auto-disable)
_started = 0.0       # when this run's loop began: a job it fired itself is running now, not one a restart cut (resume_cut)


def _when(j):
    # the time of a job the scheduler can run, or None: a record with an id, a text, a time it reads and a known repeat
    # (one written without "repeat" — by hand or by an older version — is a one-off, as toggle reads it)
    try:
        if not j.get("id") or not isinstance(j.get("text"), str) or j.get("repeat", "once") not in REPEATS:
            return None
        return datetime.strptime(j["at"], FORMAT)
    except (AttributeError, KeyError, ValueError, TypeError):
        return None


def _due():
    # collect jobs whose time has come; move repeating ones to their next occurrence (don't let missed ones pile up).
    # A one-off job isn't removed when it fires but marked "fired": removed after it ran, so a restart in between doesn't lose it
    now, due, dropped = datetime.now(), [], False
    with _lock:
        kept = []
        for j in store.load_json(config.JOBS_FILE, []):
            when = _when(j)
            if when is None:   # one broken entry shouldn't stop the rest
                events.log("job", "непонятное задание удалено (нет времени, текста или повтора)", job=j, failed=True)
                dropped = True
                continue
            j = {**j, "repeat": j.get("repeat", "once")}
            if when > now or not j.get("enabled", True) or j.get("fired"):   # a disabled one stays in place but doesn't fire
                kept.append(j)
                continue
            dropped = True
            if j["repeat"] != "once" and j.get("late") == "skip" and (now - when).total_seconds() > LATE_GRACE:
                while when <= now:   # slept through: this time is passed by, the next one comes as usual (n8n's misfire rule)
                    when = _next(when, j["repeat"])
                kept.append({**j, "at": when.strftime(FORMAT)})
                events.log("job", f"пропущено, Mac спал: {j['text'][:100]}", job=j["id"], late=True)
                continue
            due.append(j)
            if j["repeat"] != "once":
                while when <= now:
                    when = _next(when, j["repeat"])
                kept.append({**j, "at": when.strftime(FORMAT)})
            else:
                kept.append({**j, "fired": time.time()})
        if due or dropped:
            store.save_versioned(config.JOBS_FILE, kept, indent=1)
    return due


def _finished(job, answer=None, failed=False):
    """A job ran: a one-off one leaves the schedule; a repeating one keeps its last answer (the next run continues from it,
    without repeating what it said) and its run of failures."""
    with _lock:
        items = store.load_json(config.JOBS_FILE, [])
        for j in items:
            if j.get("id") != job["id"]:
                continue
            if j.get("repeat", "once") == "once" and not failed:
                items.remove(j)
            elif failed:
                j["fails"] = j.get("fails", 0) + 1
            else:
                j["fails"] = 0
                if answer:
                    j["last"] = {"t": time.time(), "text": " ".join(str(answer).split())[:1000]}
            break
        store.save_versioned(config.JOBS_FILE, items, indent=1)
    return next((j for j in items if j.get("id") == job["id"]), None)


def resume_cut():
    """At start: a one-off job that fired but never finished was cut by a restart. Recent ones run again (the run is told
    to check what the cut one managed); older ones, and one cut MOST_CUTS times already (it may itself be what fells Sena —
    run.sh restarts her in seconds, and it would fire again every time), are dropped and returned for the owner to hear about,
    each with "why": "old" or "again". A job this run's loop fired itself is running, not cut: called after start, it's left alone.
    It runs before Sena is up: a record it can't read (no text or time, a count written by hand) is left to the loop, which
    drops it with a word to the journal — here it would stop the start itself, and run.sh would restart into the same fall."""
    told = []
    with _lock:
        items = store.load_json(config.JOBS_FILE, [])
        cut = [j for j in items if isinstance(j, dict) and _when(j) is not None and isinstance(j.get("fired"), (int, float))
               and not (_started and j["fired"] >= _started)]
        for j in cut:
            j["cuts"] = (j["cuts"] if isinstance(j.get("cuts"), int) else 0) + 1
            if j["cuts"] > MOST_CUTS:
                items.remove(j)
                told.append({**j, "why": "again"})
            elif time.time() - j["fired"] < RESUME_HOURS * 3600:
                j.pop("fired")
                j["cut"] = True
                j["at"] = datetime.now().strftime(FORMAT)
            else:
                items.remove(j)
                told.append({**j, "why": "old"})
        if cut:   # nothing cut: no new version of the schedule at every start
            store.save_versioned(config.JOBS_FILE, items, indent=1)
    return told


_busy = set()   # which background jobs are running right now: a second run of the same job won't start
_busy_lock = threading.Lock()   # checking "is it running" and marking "it's started" as one action: two clicks in a row won't start a job twice


def _mark(key, value):
    with _lock:
        state = store.load_json(config.JOBS_STATE_FILE, {})
        state[key] = value
        store.save_json(config.JOBS_STATE_FILE, state, indent=1)


MAX_ATTEMPTS = 3   # cleanup and review retry after an hour on failure, but not forever


def _spawn(name, func, done=None, retry=None):
    # one background job at a time (False — already running); done() — mark it done after success; retry — (key, seconds until retry) on error.
    # After MAX_ATTEMPTS failures the job is considered done until next time, so it doesn't hammer the model all day
    with _busy_lock:
        if name in _busy:
            return False
        _busy.add(name)

    def go():
        try:
            func()
            if done:
                done()
            if retry:
                _mark(retry[0] + "_attempts", 0)
        except Exception as e:
            events.error(name, e)
            if retry:
                state = store.load_json(config.JOBS_STATE_FILE, {})
                attempts = state.get(retry[0] + "_attempts", 0) + 1
                _mark(retry[0] + "_attempts", attempts)
                if attempts >= MAX_ATTEMPTS and done:
                    done()
                    _mark(retry[0] + "_attempts", 0)
                else:
                    _mark(retry[0], time.time() + retry[1])
        finally:
            _busy.discard(name)
    safety.background(go, what=name)
    return True


def busy():
    """Which background jobs are running right now (for the console)."""
    with _busy_lock:
        return sorted(_busy)


def run_now(name, func):
    """Run a job by hand (from the app) the same way as on schedule: marked as "running now". False — this job is already running."""
    return _spawn(name, func)


def _run_job(on_job, job, notify=None):
    # a job that failed (network, model): a one-time job is put back on the schedule 10 minutes later (up to three times), Tema is told once;
    # a repeating one tells him on its first and third failure in a row, and after FAILS_OFF it is switched off.
    # At most JOBS_AT_ONCE run together: a Mac that wakes after a night doesn't start every missed job at once (n8n's limit);
    # a run that hangs past RUN_MOST gives its place up, so two hung runs don't hold back every later job
    with _job_slots:
        done = threading.Event()

        def run():
            try:
                _run_job_now(on_job, job, notify)
            finally:
                done.set()
        safety.background(run, what="Задание по расписанию")
        if not done.wait(RUN_MOST):
            events.log("job", f"идёт дольше {RUN_MOST // 60} мин, следующие задания его не ждут: {job['text'][:100]}", job=job["id"], failed=True)


def _run_job_now(on_job, job, notify=None):
    try:
        answer = on_job(job["text"], job["at"], job)
    except Exception as e:
        events.error("Задание по расписанию", e)
        why = llm.explain(e)   # the owner reads words, not an exception's class name («APIConnectionError»)
        attempts = job.get("attempts", 0)
        again = job["repeat"] == "once" and attempts < 3
        if job["repeat"] == "once":
            with _lock:
                jobs = [j for j in store.load_json(config.JOBS_FILE, []) if j.get("id") != job["id"]]
                if again:
                    jobs.append({**{k: v for k, v in job.items() if k not in ("fired", "cut")},
                                 "at": (datetime.now() + timedelta(minutes=10)).strftime(FORMAT), "attempts": attempts + 1})
                store.save_versioned(config.JOBS_FILE, jobs, indent=1)
            if notify and (attempts == 0 or not again):
                notify(f"Не вышло выполнить «{job['text']}» ({why}). " + ("Повторю через 10 минут." if again else "Разовое больше не повторяю."))
            return
        current = _finished(job, failed=True) or job
        fails = current.get("fails", 1)
        if fails >= FAILS_OFF:
            toggle(job["id"], False)
        if notify and (fails in FAILS_TOLD or fails >= FAILS_OFF):
            notify(f"Не вышло выполнить «{job['text']}» ({why})" + (f", уже {fails} раза подряд" if fails > 1 else "") + ". "
                   + ("Выключила его: включишь снова в приложении, в «Правилах», когда поправим." if fails >= FAILS_OFF
                      else "Регулярное — в следующий раз по расписанию."))
        return
    _finished(job, answer)


def _stage(name, func, *args):
    # one part of the half-minute pass: its failure is written down and the other parts still run (a single broken record
    # used to stop the heartbeat, every watch, the cleanup and the weekly review, every half minute for good)
    try:
        func(*args)
    except Exception as e:
        events.error(f"Расписание: {name}", e)


def _start_due(on_job, notify):
    for j in _due():
        events.log("job", j["text"], job=j)
        safety.background(_run_job, on_job, j, notify, what="Задание по расписанию")


GOAL_RETRY = 3600   # s: a goal whose check-in couldn't be marked is tried again this much later, not at every half-minute pass
_goal_next = {}     # goal id -> the earliest time this run starts its check-in again


def _start_goal(on_goal, g):
    now = time.time()
    if now < _goal_next.get(g["id"], 0):
        return   # tried a moment ago and still due: its mark couldn't be written, or didn't stick (two goals under one id)
    _goal_next[g["id"]] = now + GOAL_RETRY
    goals.checked(g["id"])   # marked first, so a failure doesn't repeat it every half minute
    _goal_next[g["id"]] = now + 86400   # a check-in started is due again in a day at the soonest (check_every_days >= 1)
    events.log("goal", f"проверка цели «{g['title']}»", id=g["id"])
    safety.background(on_goal, g, what="Проверка цели")


_work_pass = threading.Lock()   # one pass over the long works at a time, off the loop: a slow word to the owner doesn't hold it up


def _start_works(on_work):
    if not _work_pass.acquire(blocking=False):
        return   # the last pass is still telling the owner something (Telegram slow or away)

    def run():
        try:
            on_work()
        finally:
            _work_pass.release()
    safety.background(run, what="Долгие работы")


def _heartbeat(state, now, s, on_heartbeat):
    if s["heartbeat"] and settings.awake(now) and time.time() - state.get("heartbeat", 0) >= s["heartbeat_every_min"] * 60:
        _mark("heartbeat", time.time())
        _spawn("Фоновая проверка", on_heartbeat)


def _watch(state, now, s, on_watch):
    if on_watch and s["watch"] and time.time() - state.get("watch", 0) >= s["watch_every_min"] * 60:
        _mark("watch", time.time())
        _spawn("Сигналы по цене", on_watch)


def _tidy(state, now, s, on_tidy):
    if s["tidy"] and tidy_due(now, state):   # the "done" mark is set after success (keyed by the run date); on failure — retry in an hour
        day = now.strftime("%Y-%m-%d")
        _spawn("Ночная уборка", on_tidy, done=lambda: _mark("tidy", day), retry=("tidy_retry", 3600))


def _weekly(state, now, s, on_weekly):
    if on_weekly and s["weekly"] and weekly_due(now, state):
        week = last_weekly(now).strftime("%Y-%m-%d")
        _spawn("Недельный разбор", on_weekly, done=lambda: _mark("weekly", week), retry=("weekly_retry", 3600))


def _background(on_heartbeat, on_tidy, on_watch, on_weekly):
    state, now, s = store.load_json(config.JOBS_STATE_FILE, {}), datetime.now(), settings.load()   # turned on and off from the console
    _stage("фоновая проверка", _heartbeat, state, now, s, on_heartbeat)
    _stage("слежка", _watch, state, now, s, on_watch)
    _stage("ночная уборка", _tidy, state, now, s, on_tidy)
    _stage("недельный разбор", _weekly, state, now, s, on_weekly)


def _goals(on_goal):
    for g in goals.due():
        _stage("цель", _start_goal, on_goal, g)   # one goal that can't be marked doesn't keep the others waiting


def _tick(on_job, on_heartbeat, on_tidy, on_watch=None, on_weekly=None, notify=None, on_goal=None, on_work=None):
    """One pass of the loop: the due jobs, the goals, the long works, each background job — every one a stage of its own."""
    _stage("задания", _start_due, on_job, notify)
    if on_goal:
        _stage("цели", _goals, on_goal)
    if on_work:   # long works: the turns that are due start in their own threads
        _stage("долгие работы", _start_works, on_work)
    _stage("фоновые дела", _background, on_heartbeat, on_tidy, on_watch, on_weekly)


def _loop(*handlers):
    while not power.is_stopping():   # Sena is going down: no job, check or cleanup starts any more
        if not power.is_restarting():   # a restart for new code waits for the running tasks: nothing new starts meanwhile
            _tick(*handlers)
        time.sleep(30)


def start(on_job, on_heartbeat, on_tidy, on_watch=None, on_weekly=None, notify=None, on_goal=None, on_work=None):
    global _started
    _started = time.time()
    threading.Thread(target=_loop, args=(on_job, on_heartbeat, on_tidy, on_watch, on_weekly, notify, on_goal, on_work), daemon=True).start()


TOOLS = [
    tool("schedule", "Запланировать дело: в указанное время ты сама получишь эту задачу и выполнишь её (напомнить, проверить, сделать)",
         {"text": string("Что сделать в это время, например «Напомнить про созвон с Серканом в 10:00»"),
          "at": string("Когда: ДД.ММ.ГГГГ ЧЧ:ММ"),
          "repeat": {**string("once — один раз, daily — каждый день, weekdays — по будням, weekly — раз в неделю"), "enum": REPEATS},
          "late": {**string("если Mac спал во время повторяющегося дела: run — выполнить с опозданием (напоминания, по умолчанию); "
                            "skip — пропустить до следующего раза (сводки, проверки: вчерашнее в обед не нужно)"), "enum": ["run", "skip"]}},
         ["text", "at", "repeat"]),
    tool("unschedule", "Отменить запланированное дело по id из «Расписание»", {"id": string()}, ["id"]),
]
FUNCTIONS = {"schedule": schedule, "unschedule": unschedule}
