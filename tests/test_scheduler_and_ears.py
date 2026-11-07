"""Scheduler and background hearing: logic only, no models or network."""
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

from sena import scheduler
from sena.core import local
from sena.phone import convo, speech


class Scheduler(unittest.TestCase):
    def test_rejects_bad_input(self):
        self.assertIn("Не поняла время", scheduler.schedule("x", "завтра"))
        self.assertIn("repeat", scheduler.schedule("x", "01.01.2099 10:00", "иногда"))
        self.assertIn("уже прошло", scheduler.schedule("x", "01.01.2000 10:00"))

    def test_schedule_and_cancel(self):
        out = scheduler.schedule("позвонить", "01.01.2099 10:00", "daily")
        job_id = out.split("[")[1].split("]")[0]
        self.assertIn("позвонить", scheduler.summary())
        self.assertEqual(scheduler.unschedule(job_id), "Удалено")
        self.assertEqual(scheduler.unschedule(job_id), "Нет такого задания")

    def test_weekdays_skip_weekend(self):
        friday = datetime(2026, 10, 16, 9, 0)   # Friday
        self.assertEqual(scheduler._next(friday, "weekdays").weekday(), 0)   # Monday
        self.assertEqual(scheduler._next(friday, "weekly"), friday + timedelta(days=7))

    def test_due_job_fires_once_and_repeating_moves_forward(self):
        past = (datetime.now() - timedelta(minutes=5)).strftime(scheduler.FORMAT)
        once, daily = [{"id": "o", "text": "раз", "at": past, "repeat": "once"}], [{"id": "d", "text": "каждый день", "at": past, "repeat": "daily"}]
        scheduler.store.save_json(scheduler.config.JOBS_FILE, once + daily)
        due = scheduler._due()
        self.assertEqual({j["id"] for j in due}, {"o", "d"})
        left = scheduler.store.load_json(scheduler.config.JOBS_FILE, [])
        self.assertEqual([j["id"] for j in left], ["o", "d"])   # the one-off waits, marked fired, until it has run
        self.assertTrue(left[0]["fired"])
        self.assertGreater(datetime.strptime(left[1]["at"], scheduler.FORMAT), datetime.now())
        self.assertEqual(scheduler._due(), [])   # fired once, not again
        scheduler._finished(due[0], "напомнила")
        self.assertEqual([j["id"] for j in scheduler.store.load_json(scheduler.config.JOBS_FILE, [])], ["d"])

    def test_a_one_off_job_cut_by_a_restart_runs_again_and_an_old_one_is_told(self):
        scheduler.store.save_json(scheduler.config.JOBS_FILE, [
            {"id": "n", "text": "свежее", "at": "01.01.2026 10:00", "repeat": "once", "fired": time.time() - 60},
            {"id": "s", "text": "давнее", "at": "01.01.2026 10:00", "repeat": "once", "fired": time.time() - 7 * 3600}])
        told = scheduler.resume_cut()
        self.assertEqual([j["id"] for j in told], ["s"])
        [left] = scheduler.store.load_json(scheduler.config.JOBS_FILE, [])
        self.assertTrue(left["cut"])
        self.assertEqual([j["id"] for j in scheduler._due()], ["n"])   # due again, and its run hears it was cut

    def test_a_slept_through_digest_waits_for_its_next_time_and_a_reminder_runs_late(self):
        """n8n's misfire rule: after a night of sleep every missed job ran at once, a morning digest at noon among them."""
        three_hours_ago = (datetime.now() - timedelta(hours=3)).strftime(scheduler.FORMAT)
        scheduler.store.save_json(scheduler.config.JOBS_FILE, [
            {"id": "dg", "text": "утренняя сводка", "at": three_hours_ago, "repeat": "daily", "late": "skip"},
            {"id": "pill", "text": "напомнить про таблетку", "at": three_hours_ago, "repeat": "daily"}])
        self.assertEqual([j["id"] for j in scheduler._due()], ["pill"])   # the reminder still matters
        digest = next(j for j in scheduler.store.load_json(scheduler.config.JOBS_FILE, []) if j["id"] == "dg")
        self.assertGreater(datetime.strptime(digest["at"], scheduler.FORMAT), datetime.now())   # tomorrow, as usual
        job, _ = scheduler.add("сводка", (datetime.now() + timedelta(hours=1)).strftime(scheduler.FORMAT), "daily", late="skip")
        self.assertEqual(job["late"], "skip")

    def test_no_more_than_two_scheduled_jobs_run_at_once(self):
        import threading as th
        running, most, release = [0], [0], th.Event()

        def job(text, at, j):
            running[0] += 1
            most[0] = max(most[0], running[0])
            release.wait(2)
            running[0] -= 1
            return "ok"
        threads = [th.Thread(target=scheduler._run_job, args=(job, {"id": f"j{i}", "text": "t", "at": "", "repeat": "daily"})) for i in range(4)]
        for t in threads:
            t.start()
        time.sleep(0.3)
        self.assertEqual(most[0], scheduler.JOBS_AT_ONCE)
        release.set()
        for t in threads:
            t.join()

    def test_a_record_it_cant_read_never_stops_the_start(self):
        """review R9: resume_cut runs before Sena is up; a cut count written by hand raised out of the start, and run.sh
        restarted her into the same fall five times."""
        fired = time.time() - 60
        scheduler.store.save_json(scheduler.config.JOBS_FILE, [
            {"id": "hand", "text": "полить цветы", "at": "01.01.2026 10:00", "repeat": "once", "fired": fired, "cuts": "много"},
            {"id": "bare", "fired": fired},   # no text, no time: the loop drops it, with a word to the journal
            "не задание"])
        self.assertEqual(scheduler.resume_cut(), [])
        jobs = {j["id"]: j for j in scheduler.store.load_json(scheduler.config.JOBS_FILE, []) if isinstance(j, dict)}
        self.assertEqual((jobs["hand"]["cuts"], jobs["hand"]["cut"]), (1, True))   # resumed as cut once
        self.assertEqual(jobs["bare"], {"id": "bare", "fired": fired})   # left as it was
        before = len(scheduler.store.versions(scheduler.config.JOBS_FILE))
        scheduler.store.save_json(scheduler.config.JOBS_FILE, [{"id": "n", "text": "позже", "at": "01.01.2099 10:00", "repeat": "once"}])
        scheduler.resume_cut()   # nothing cut: no new version of the schedule at every start
        self.assertEqual(len(scheduler.store.versions(scheduler.config.JOBS_FILE)), before)

    def test_a_job_cut_again_and_again_is_not_run_a_third_time(self):
        """n8n's crash guard: a one-off job that fells Sena would fire on every restart (run.sh restarts her in seconds)."""
        scheduler.store.save_json(scheduler.config.JOBS_FILE, [{"id": "p", "text": "ядовитое", "at": "01.01.2026 10:00", "repeat": "once"}])
        for _ in range(scheduler.MOST_CUTS + 1):
            jobs = scheduler.store.load_json(scheduler.config.JOBS_FILE, [])
            if not jobs:
                break
            jobs[0]["fired"] = time.time() - 60   # it fired, and Sena fell with it
            scheduler.store.save_json(scheduler.config.JOBS_FILE, jobs)
            told = scheduler.resume_cut()
        self.assertEqual([(j["id"], j["why"]) for j in told], [("p", "again")])
        self.assertEqual(scheduler.store.load_json(scheduler.config.JOBS_FILE, []), [])

    def test_a_repeating_job_remembers_its_last_answer_and_tells_failures_sparingly(self):
        past = (datetime.now() - timedelta(minutes=5)).strftime(scheduler.FORMAT)
        scheduler.store.save_json(scheduler.config.JOBS_FILE, [{"id": "w", "text": "сводка новостей", "at": past, "repeat": "daily"}])
        [job] = scheduler._due()
        seen = []
        scheduler._run_job(lambda text, at, j: seen.append(j) or "Главное сегодня: саммит.", job)
        [stored] = scheduler.store.load_json(scheduler.config.JOBS_FILE, [])
        self.assertEqual(stored["last"]["text"], "Главное сегодня: саммит.")
        said = []

        def boom(text, at, j):
            raise ConnectionError("нет сети")
        for _ in range(4):
            scheduler._run_job(boom, stored, said.append)
        self.assertEqual(len(said), 2)   # the first and the third failure in a row, not every one
        self.assertIn("уже 3 раза подряд", said[1])
        for _ in range(6):
            scheduler._run_job(boom, stored, said.append)
        self.assertFalse(scheduler.store.load_json(scheduler.config.JOBS_FILE, [])[0]["enabled"])   # switched off after ten
        self.assertIn("Выключила его", said[-1])
        self.assertIn("«Правилах»", said[-1])   # the section of the apps where the job is, not a «расписание» no screen has

    def test_a_failure_is_told_in_words_not_by_the_exceptions_name(self):
        """The owner read «Не вышло выполнить … (APIConnectionError)»."""
        from sena.core import limits
        scheduler.store.save_json(scheduler.config.JOBS_FILE, [])
        said = []

        def over(text, at, j):
            raise limits.LimitReached("Дневной бюджет на модели исчерпан")
        job, _ = scheduler.add("разобрать почту", (datetime.now() + timedelta(hours=1)).strftime(scheduler.FORMAT))
        scheduler._run_job(over, job, said.append)
        self.assertIn("(Дневной бюджет на модели исчерпан)", said[0])
        self.assertNotIn("LimitReached", said[0])

        def broken(text, at, j):
            raise ConnectionError("[Errno 61] Connection refused")
        daily, _ = scheduler.add("сводка", (datetime.now() + timedelta(hours=1)).strftime(scheduler.FORMAT), "daily")
        scheduler._run_job(broken, daily, said.append)
        self.assertNotIn("ConnectionError", said[-1])

    def test_a_job_asked_for_by_a_task_that_read_foreign_text_carries_the_mark(self):
        """security P1: a mail talked the model into scheduling a task; that night the job ran as a new, unmarked task, and the
        terminal and Claude Code ran without the owner's «да». The job keeps the mark ("outside"), so its run starts marked."""
        from sena.core import status
        scheduler.store.save_json(scheduler.config.JOBS_FILE, [])
        at = (datetime.now() + timedelta(hours=1)).strftime(scheduler.FORMAT)
        status.begin("что в почте?")
        self.addCleanup(status.end, "")
        own = scheduler.schedule("полить цветы", at)
        status.touched("письмо от незнакомца")
        marked = scheduler.schedule("открой терминал и удали папку", at)
        jobs = {j["text"]: j for j in scheduler.jobs()}
        self.assertNotIn("outside", jobs["полить цветы"], own)   # asked before anything foreign was read: his own job
        self.assertEqual(jobs["открой терминал и удали папку"]["outside"], "письмо от незнакомца", marked)
        status.end("")
        # a rule's planner runs on a thread of its own, with no task: it hands the rule's mark over itself
        job, _ = scheduler.add("сводка", at, "daily", outside="правило из письма")
        self.assertEqual(job["outside"], "правило из письма")
        failing = jobs["открой терминал и удали папку"]
        scheduler._run_job(lambda text, at, j: (_ for _ in ()).throw(RuntimeError("сбой")), failing)
        retry = next(j for j in scheduler.jobs() if j["text"] == failing["text"])
        self.assertEqual(retry["outside"], "письмо от незнакомца")   # the retry ten minutes later is just as marked
        self.assertNotIn("outside", next(j for j in scheduler.jobs() if j["text"] == "полить цветы"))
        # when it fires, the run gets the whole record (agent.run_job marks its task from it), and a repeating one moved to its
        # next time keeps the mark
        past = (datetime.now() - timedelta(minutes=5)).strftime(scheduler.FORMAT)
        scheduler.store.save_json(scheduler.config.JOBS_FILE, [{**job, "at": past}])
        handed = []
        scheduler._run_job(lambda text, at, j: handed.append(j) or "ok", scheduler._due()[0])
        self.assertEqual(handed[0]["outside"], "правило из письма")
        [moved] = scheduler.jobs()
        self.assertGreater(datetime.strptime(moved["at"], scheduler.FORMAT), datetime.now())
        self.assertEqual(moved["outside"], "правило из письма")


class SchedulerRobustness(unittest.TestCase):
    def test_tidy_runs_after_the_machine_slept_through_the_night(self):
        today = datetime(2026, 10, 16, 15, 0).strftime("%Y-%m-%d")
        hour = 4   # cleanup hour from settings; passed explicitly so the test doesn't depend on the default
        self.assertTrue(scheduler.tidy_due(datetime(2026, 10, 16, 15, 0), {}, hour))                  # woke up during the day, hasn't cleaned up today yet
        self.assertTrue(scheduler.tidy_due(datetime(2026, 10, 16, 4, 0), {"tidy": "2026-10-15"}, hour))
        self.assertFalse(scheduler.tidy_due(datetime(2026, 10, 16, 15, 0), {"tidy": today}, hour))   # already cleaned up
        self.assertFalse(scheduler.tidy_due(datetime(2026, 10, 16, 2, 0), {"tidy": "2026-10-15"}, hour))   # too early at night, before the due time

    def test_one_broken_job_does_not_block_the_others(self):
        past = (datetime.now() - timedelta(minutes=5)).strftime(scheduler.FORMAT)
        scheduler.store.save_json(scheduler.config.JOBS_FILE, [
            {"id": "bad", "text": "сломано", "at": "вчера вечером", "repeat": "once"},
            {"id": "monthly", "text": "раз в месяц", "at": past, "repeat": "monthly"},
            {"id": "old", "text": "без повтора, как писали раньше", "at": past},
            "не задание",
            {"id": "ok", "text": "работает", "at": past, "repeat": "once"}])
        self.assertEqual([j["id"] for j in scheduler._due()], ["old", "ok"])   # a record without "repeat" is a one-off
        self.assertEqual([j["id"] for j in scheduler.store.load_json(scheduler.config.JOBS_FILE, [])], ["old", "ok"])   # the broken ones were removed
        self.assertIn("без повтора", scheduler.summary())

    def tick(self, **handlers):
        """One pass of the loop with every background job due; returns the names of the background jobs it started."""
        spawned = []
        prefs = {**scheduler.settings.DEFAULTS, "active_from": 0, "active_to": 24, "tidy": False, "weekly": False}
        scheduler.store.save_json(scheduler.config.JOBS_STATE_FILE, {})
        with mock.patch.object(scheduler.settings, "load", return_value=prefs), \
                mock.patch.object(scheduler, "_spawn", lambda name, *a, **k: spawned.append(name)), mock.patch.object(scheduler.events, "error"):
            scheduler._tick(lambda *a: None, lambda: None, lambda: None, on_watch=lambda: None, **handlers)
        return spawned

    def test_a_failing_stage_does_not_stop_the_later_ones(self):
        """A single broken record (a job, a goal) used to stop the heartbeat, every watch, the cleanup and the review for good."""
        def broken(*args):
            raise KeyError("repeat")
        with mock.patch.object(scheduler, "_due", broken), mock.patch.object(scheduler.goals, "due", broken):
            self.assertEqual(self.tick(on_goal=lambda g: None), ["Фоновая проверка", "Сигналы по цене"])
        scheduler.store.save_json(scheduler.config.JOBS_FILE, [])
        goals = [{"id": "g1", "title": "сломанная"}, {"id": "g2", "title": "живая"}]
        started = []
        with mock.patch.object(scheduler.goals, "due", return_value=goals), \
                mock.patch.object(scheduler.goals, "checked", lambda id: broken() if id == "g1" else None), \
                mock.patch.object(scheduler.safety, "background", lambda func, *args, what=None: started.append(args)):
            self.assertEqual(self.tick(on_goal=lambda g: None), ["Фоновая проверка", "Сигналы по цене"])
        self.assertEqual(started, [(goals[1],)])   # the goal that can't be marked doesn't keep the other one waiting

    def test_a_goal_that_cant_be_marked_is_not_tried_at_every_pass(self):
        """review R11: a goal whose check couldn't be written (or whose mark didn't stick) was due again 30 s later: an error
        a pass, ~2900 a day, or a paid check-in every half minute."""
        goals = [{"id": "gw", "title": "не пишется"}, {"id": "gs", "title": "не держит отметку"}]
        marks, started = [], []

        def checked(id):
            marks.append(id)
            if id == "gw":
                raise OSError("database is locked")
        with mock.patch.object(scheduler.goals, "due", return_value=goals), mock.patch.object(scheduler.goals, "checked", checked), \
                mock.patch.object(scheduler.safety, "background", lambda func, *args, what=None: started.append(args[0]["id"])), \
                mock.patch.dict(scheduler._goal_next, clear=True):
            for _ in range(3):
                self.tick(on_goal=lambda g: None)
            self.assertEqual((marks, started), (["gw", "gs"], ["gs"]))   # each tried once, not at every pass
            self.assertAlmostEqual(scheduler._goal_next["gw"] - time.time(), scheduler.GOAL_RETRY, delta=60)   # the failed mark: again in an hour
            self.assertGreater(scheduler._goal_next["gs"] - time.time(), 23 * 3600)   # a check-in that started: not before a day
            scheduler._goal_next["gw"] = time.time() - 1   # the hour has passed
            self.tick(on_goal=lambda g: None)
            self.assertEqual((marks, started), (["gw", "gs", "gw"], ["gs"]))

    def test_the_long_works_pass_runs_off_the_loop(self):
        """work.tick tells the owner inline: with Telegram away it used to hold the scheduler itself for minutes."""
        import threading as th
        release, passes = th.Event(), []

        def slow():
            passes.append(1)
            release.wait(5)
        began = time.time()
        self.assertEqual(self.tick(on_work=slow), ["Фоновая проверка", "Сигналы по цене"])
        self.tick(on_work=slow)   # the first pass still runs: no second one on top of it
        self.assertLess(time.time() - began, 2)
        time.sleep(0.1)
        self.assertEqual(passes, [1])
        release.set()
        self.assertTrue(scheduler._work_pass.acquire(timeout=2))   # the pass ended and let go
        scheduler._work_pass.release()

    def test_a_job_fired_by_this_run_is_not_taken_for_one_cut_by_a_restart(self):
        """The start race: the loop's first pass started an overdue one-off, then resume_cut took it for cut and it ran twice."""
        scheduler.store.save_json(scheduler.config.JOBS_FILE, [{"id": "j1", "text": "разобрать почту", "at": "25.10.2026 09:00", "repeat": "once"}])
        with mock.patch.object(scheduler, "_started", time.time() - 1):   # the loop is running
            self.assertEqual([j["id"] for j in scheduler._due()], ["j1"])
            self.assertEqual(scheduler.resume_cut(), [])
            [job] = scheduler.jobs()
            self.assertNotIn("cut", job)
            self.assertEqual(scheduler._due(), [])   # not started a second time

    def test_nothing_starts_while_a_restart_for_new_code_waits(self):
        """The apply of her own code change waits for the running tasks before the restart: a job started meanwhile was cut by it."""
        passes = []
        with mock.patch.object(scheduler.power, "is_restarting", return_value=True), mock.patch.object(scheduler, "_tick", lambda *a: passes.append(1)), \
                mock.patch.object(scheduler.power, "is_stopping", side_effect=[False, False, True]), mock.patch.object(scheduler.time, "sleep"):
            scheduler._loop(lambda *a: None, lambda: None, lambda: None)
        self.assertEqual(passes, [])

    def test_a_hung_run_gives_its_place_up(self):
        import threading as th
        release, started, ended, logged = th.Event(), [], [], []

        def hung(text, at, j):
            started.append(j["id"])
            release.wait(5)
            return "ok"
        with mock.patch.object(scheduler, "RUN_MOST", 0.2), mock.patch.object(scheduler.events, "log", lambda kind, text, **kw: logged.append(text)), \
                mock.patch.object(scheduler, "_finished", lambda job, answer=None, failed=False: ended.append(job["id"])):
            threads = [th.Thread(target=scheduler._run_job, args=(hung, {"id": f"h{i}", "text": "t", "at": "", "repeat": "daily"})) for i in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(3)
            self.assertEqual(sorted(started), ["h0", "h1", "h2"])   # the third didn't wait for the two hung ones
            release.set()
            for _ in range(100):   # the hung runs go on by themselves and finish as usual
                if len(ended) == 3:
                    break
                time.sleep(0.02)
        self.assertEqual(sorted(ended), ["h0", "h1", "h2"])
        self.assertTrue(any("идёт дольше" in text for text in logged))


class Hearing(unittest.TestCase):
    def test_name_is_caught_in_all_forms(self):
        for phrase in ["Сена, сколько времени", "эй, Сена", "Сенка включи музыку", "слушай Сеночка", "hey Sena what time", "Сена!", "тена, привет"]:
            self.assertTrue(speech.WAKE.match(phrase), phrase)

    def test_other_words_are_not_the_name(self):
        for phrase in ["Сенат собрался вчера", "сейчас пойдём", "цена какая", "Лена, ты где", "стена белая"]:
            self.assertFalse(speech.WAKE.match(phrase), phrase)

    def test_command_text_after_name(self):
        m = speech.WAKE.match("Сена, сколько времени")
        self.assertEqual("Сена, сколько времени"[m.end():].strip(), "сколько времени")

    def test_clean_drops_whisper_inventions(self):
        for junk in ["Субтитры сделал DimaTorzok", "[музыка]", "СПОКОЙНАЯ МУЗЫКА", "так так так", "да"]:
            self.assertEqual(local.clean(junk), "", junk)
        self.assertEqual(local.clean("включи музыку погромче"), "включи музыку погромче")

    def test_worth_analyzing(self):
        small = [{"text": "ага да ну ладно"}]
        real = [{"text": "завтра встречаюсь с Серканом обсудить новый отчёт по продажам"}]
        self.assertFalse(convo.worth_analyzing(small))
        self.assertTrue(convo.worth_analyzing(real))


if __name__ == "__main__":
    unittest.main()
