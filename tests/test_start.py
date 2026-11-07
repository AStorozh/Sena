"""Starting Sena (sena/app.py, run.sh, the launchd plist): the port first — it is what makes a core the only one; what the last
run left is settled before anything of this run starts; Telegram never holds the start; one run.sh at a time, no endless
restart loop when a restart can't help. Nothing real starts: the pieces are stand-ins, run.sh runs a stand-in Python."""
import os
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from sena import app, config
from sena.core import power
from sena.phone import server

ROOT = Path(__file__).resolve().parent.parent


class Exit(Exception):
    """os._exit, caught: the test goes on."""


class Main(unittest.TestCase):
    def run_main(self, poll=SystemExit(0), broken=()):
        """broken — the settling steps whose stand-ins raise, as a broken record the last run left makes them."""
        order = []

        def note(name, result=None):
            def stand_in(*a, **k):
                order.append(name)
                if name in broken:
                    raise RuntimeError(f"{name}: битая запись")
                return result
            return stand_in
        httpd = mock.Mock()
        patches = [mock.patch.object(app.config, "check", note("check")),
                   mock.patch.object(app.server, "make_server", note("bind", httpd)),
                   mock.patch.object(app.store, "write_text", lambda path, text: order.append("pid") if path == config.PID_FILE else None),
                   mock.patch.object(app, "_after_a_fall", note("after_a_fall")),
                   mock.patch.object(app.scheduler, "resume_cut", note("resume_cut", [{"text": "разбудить", "why": "old"}])),
                   mock.patch.object(app.work, "start_engine", note("work_engine")),
                   mock.patch.object(app.confirm, "unfinished", note("unfinished", [("k1", "письмо Ольге: акты за октябрь")])),
                   mock.patch.object(app.events, "log", lambda kind, text="", **k: order.append(text) if text == "Сена запущена" else None),
                   mock.patch.object(app.ask_owner, "set_sender", note("sender")),
                   mock.patch.object(app.server, "serve", note("serve")),
                   mock.patch.object(app.voice, "start", note("voice")),
                   mock.patch.object(app.convo, "start", note("convo")),
                   mock.patch.object(app.scheduler, "start", note("scheduler")),
                   mock.patch.object(app.safety, "background", lambda func, *a, what=None: order.append(f"фон: {what}")),
                   mock.patch.object(app.safety, "router", note("router")),
                   mock.patch.object(app.ag_sena, "notify_owner", note("telegram")),
                   mock.patch.object(app.threading, "Timer", mock.Mock()),
                   mock.patch.object(app.signal, "signal", note("signal")),
                   mock.patch.object(app.bot, "poll", side_effect=poll),
                   mock.patch.object(app, "_shut_down", note("shut_down")),
                   mock.patch.object(app.os, "_exit", side_effect=Exit),
                   mock.patch.object(app.traceback, "print_exc", lambda: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with self.assertRaises(Exit):
            app.main()
        return order, app.os._exit.call_args[0][0]

    def test_the_port_comes_first_and_the_last_run_is_settled_before_this_one_starts(self):
        """The audit of 25.10: an overdue one-off job fired twice (the scheduler started before resume_cut), a crash's recovery
        took the new run's tasks for cut ones, and a second core started Telegram and the syncs before it failed on the port."""
        order, code = self.run_main()
        self.assertEqual(order[:5], ["router", "check", "bind", "pid", "signal"])   # the pid file right after the port: no "off" with a start button meanwhile
        before = order.index
        self.assertLess(before("after_a_fall"), before("resume_cut"))
        self.assertLess(before("resume_cut"), before("Сена запущена"))
        self.assertLess(before("work_engine"), before("scheduler"))
        # round 2 (R9): the unfinished actions were read after the server and the schedule were up — an action of this run in
        # flight was told as the last run's
        self.assertLess(before("unfinished"), before("Сена запущена"))
        self.assertIn("фон: Незаконченные действия", order)
        for started in ("serve", "scheduler", "voice", "sender", "фон: Вход в аккаунт Телеграма", "фон: Указатель памяти", "фон: Расписание из правил"):
            if started in order:
                self.assertLess(before("Сена запущена"), before(started), started)
        self.assertNotIn("telegram", order)   # no word to the owner from the starting thread: Telegram may be out of reach
        self.assertIn("фон: Сообщение при запуске", order)   # the cut job is told from a thread of its own
        self.assertEqual((order[-1], code), ("shut_down", 0))

    def test_a_broken_record_the_last_run_left_does_not_stop_the_start(self):
        """Round 2 (R9): only the crash recovery was guarded; a malformed job or work record raised out of the start — exit 1,
        five quick falls, and Sena never came up."""
        order, code = self.run_main(broken=("after_a_fall", "resume_cut", "work_engine", "unfinished"))
        for step in ("after_a_fall", "resume_cut", "work_engine", "unfinished", "Сена запущена", "serve", "scheduler"):
            self.assertIn(step, order)
        self.assertEqual((order[-1], code), ("shut_down", 0))

    def test_shutting_down_flushes_the_voices_written_only_in_memory(self):
        """Round 2: a voice heard in the last minute (people.remember_voice) is only in memory until the next card write or
        VOICE_SAVE; a stop before either lost it. _shut_down must flush it after her own programs stop and before the databases close."""
        order = []
        with mock.patch.object(app.power, "set_stopping"), mock.patch.object(app, "_finish_running"), \
                mock.patch.object(app.programs, "stop_all", lambda: order.append("stop_all")), \
                mock.patch.object(app.people, "flush", lambda: order.append("flush")), \
                mock.patch.object(app.events, "checkpoint", lambda: order.append("checkpoint_events")), \
                mock.patch.object(app.recall.index, "checkpoint", lambda: order.append("checkpoint_index")), \
                mock.patch.object(type(app.config.PID_FILE), "unlink"):
            app._shut_down()
        self.assertEqual(order, ["stop_all", "flush", "checkpoint_events", "checkpoint_index"])

    def test_a_restart_asked_for_ends_with_3_and_a_crash_with_1(self):
        self.addCleanup(setattr, power, "_restart", False)
        power.request_restart()
        self.assertEqual(self.run_main()[1], 3)   # run.sh starts her again at once, on the new code
        power._restart = False
        order, code = self.run_main(poll=RuntimeError("сломалось"))
        self.assertEqual((order[-1], code), ("shut_down", 1))   # the cleanup runs after a crash too

    def test_without_the_keys_it_stops_before_the_port_and_asks_not_to_restart(self):
        with mock.patch.object(app.config, "check", side_effect=SystemExit("В .env не хватает: SENA_CLAUDE_BRAIN_KEY")), \
                mock.patch.object(app.server, "make_server", side_effect=AssertionError("порт не занимаем")), \
                mock.patch.object(app.safety, "router", lambda: None), \
                mock.patch.object(app.store, "write_text", side_effect=AssertionError("pid не пишем")), self.assertRaises(SystemExit) as stopped:
            app.main()
        self.assertEqual(stopped.exception.code, app.NO_RESTART)

    def test_stderr_is_left_to_its_own_masking_router(self):
        """Round 2 (S4): main set sys.stderr = sys.stdout — stderr is masked on its own since round 1, and the swap routed a
        traceback printed in a thread under a sandbox capture into that capture (core/sandbox.py captures through stdout)."""
        self.addCleanup(setattr, sys, "stderr", sys.stderr)   # whatever main does, the rest of the run keeps its stderr
        self.run_main()
        self.assertIsNot(sys.stderr, sys.stdout)

    def test_a_second_stop_during_the_cleanup_changes_nothing(self):
        """The concurrency audit (E9): a second «Остановить» aborted writing the cut tasks and stopping her programs half way."""
        self.addCleanup(setattr, power, "_stopping", False)
        with self.assertRaises(SystemExit):
            app._stop()
        self.assertTrue(power.is_stopping())
        self.assertIsNone(app._stop())   # no exception: the cleanup goes on


class RunsAndTheirEnds(unittest.TestCase):
    """What a run leaves in words: an agent's question, an update's verdict, the tasks a stop cut."""

    def test_an_agents_question_goes_into_the_conversation_as_it_is_asked(self):
        """Round 2 (J9, J18): the question was written only if his very next message went straight back to the agent — an
        answer after the timeout, or one the coordinator passed on, reached her with no context."""
        from sena.memory import history
        question = "Claude Code спрашивает: какой цвет у кнопки оплаты?"
        with mock.patch.object(app.api, "send_text") as sent:
            app._ask_owner_in_telegram(question, None)
        sent.assert_called_once()
        written = [m for m in history.messages(config.OWNER_ID) if m["content"] == question]
        self.assertEqual([m["role"] for m in written], ["assistant"])
        self.assertTrue(written[0].get("read"))   # an agent's words may retell a page: the registry keeps them as someone else's

    def test_only_an_update_applied_before_this_run_is_confirmed_by_it(self):
        """Round 2 (R8): the grace timer confirmed any update record — one applied during this very run, whose code hadn't run
        yet, was told «прижилось» and its record deleted, so its crash had nothing to roll back to."""
        pending = app.ag_code.PENDING_FILE
        self.addCleanup(pending.unlink, missing_ok=True)
        pending.write_text("aaa ab12 bbb\n", encoding="utf-8")
        with mock.patch.object(app.ag_code, "confirm_update", return_value=True) as confirmed:
            os.utime(pending, (app.STARTED + 5, app.STARTED + 5))   # applied while this run works: its restart is on the way
            self.assertFalse(app._update_took(app.STARTED))
            confirmed.assert_not_called()
            os.utime(pending, (app.STARTED - 60, app.STARTED - 60))   # applied before this run started: this run is its proof
            self.assertTrue(app._update_took(app.STARTED))
            pending.unlink()
            self.assertFalse(app._update_took(app.STARTED))
        self.assertEqual(confirmed.call_count, 1)

    def test_a_cut_long_work_turn_is_the_engines_and_a_restart_says_what_it_is(self):
        """Round 2 (R23, R25): a stop wrote «прервала: меня выключили. Скажи, если продолжить» for a long work's turn the engine
        resumes by itself, and the same words for a restart onto new code."""
        from sena.core import cancel, status
        from sena.memory import history, task_log
        now = time.time()
        status.tasks[515151] = {"id": "t-work-turn", "text": "Работа «Сайт»: ход 3", "start": now, "steps": [], "done": False, "answer": None, "work": "w1"}
        status.tasks[525252] = {"id": "t-his", "text": "найди билеты в Порту", "start": now, "steps": [], "done": False, "answer": None}
        for ident, tid in ((515151, "t-work-turn"), (525252, "t-his")):
            self.addCleanup(status.tasks.pop, ident, None)
            self.addCleanup(cancel.cancelled.discard, tid)
        self.addCleanup(setattr, power, "_restart", False)
        power.request_restart()
        with mock.patch.object(app, "STOP_WAIT", 0.2):
            app._finish_running()
        turn, his = task_log.get("t-work-turn"), task_log.get("t-his")
        self.assertTrue(turn["service"])
        self.assertEqual(his["answer"], "Прервано: перезапустилась на новый код")
        lines = [m["content"] for m in history.messages(config.OWNER_ID) if m["time"] >= now]   # only what this stop wrote
        self.assertIn("Задачу «найди билеты в Порту» прервала: перезапустилась на новый код. Скажи, если продолжить.", lines)
        self.assertFalse(any("Работа «Сайт»" in line for line in lines))
        self.assertFalse(any("меня выключили" in line for line in lines))


class Screen(unittest.TestCase):
    def test_the_screen_answers_for_her_while_she_is_off(self):
        """Round 2 (AN-7, J5): the screen alone left the voice port closed (the phone: «Mac не отвечает») and Telegram unread —
        «ты тут?» got no word. It opens the voice door, which refuses in words, and answers Telegram that she is off."""
        from sena.phone import __main__ as screen
        started = []
        httpd = mock.Mock()
        with mock.patch.object(screen.safety, "router"), mock.patch.object(screen.power, "set_off", lambda: started.append("off")), \
                mock.patch.object(screen.server, "make_server", lambda: httpd), \
                mock.patch.object(screen.safety, "background", lambda func, *a, what=None: started.append(what)), \
                mock.patch.object(screen.voice, "start", lambda: started.append("voice")), \
                mock.patch.object(screen.bot, "answer_while_off", lambda: started.append("telegram")), \
                mock.patch.object(screen.events, "log"), mock.patch("builtins.print"):
            screen.main()
            deadline = time.time() + 3
            while "telegram" not in started and time.time() < deadline:
                time.sleep(0.02)
        self.assertEqual(started[0], "off")   # switched off before anything can be asked of it
        self.assertIn("voice", started)
        self.assertIn("telegram", started)
        httpd.serve_forever.assert_called_once()


class Port(unittest.TestCase):
    def test_a_held_port_is_waited_for_a_little_then_refused(self):
        """desk core_start: the screen hands the port over and exits a moment later — the core waits for it, not exits."""
        holder = socket.socket()
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        holder.bind(("127.0.0.1", 0))   # this Mac only: a test server is no door for the café's network (round 2, security S2)
        holder.listen()
        port = holder.getsockname()[1]
        with mock.patch.object(config, "APP_PORT", port):
            started = time.time()
            with self.assertRaises(SystemExit):
                server.make_server(wait=0.6, host="127.0.0.1")
            self.assertGreaterEqual(time.time() - started, 0.5)
            threading.Timer(0.4, holder.close).start()   # the screen leaves
            httpd = server.make_server(wait=5, host="127.0.0.1")
            httpd.server_close()


class RunScript(unittest.TestCase):
    """run.sh in a throwaway folder with a stand-in Python that exits with the codes it is given. The stand-in answers run.sh's
    two questions to the code itself: where the data folder is (as config.DATA would) and a line to the owner in Telegram (written
    down in `told`, never sent). Git, where a test needs it, is a repository of the throwaway folder's own."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="sena-run-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        (self.folder / "run.sh").write_text((ROOT / "run.sh").read_text(encoding="utf-8"), encoding="utf-8")
        fake = self.folder / "python"
        fake.write_text("#!/bin/zsh\ncd \"$(dirname \"$0\")\"\n"
                        "if [ \"$1\" = -c ]; then\n"
                        "  case \"$2\" in\n"
                        "    *config.DATA*) echo \"${SENA_DATA:-data}\" ;;\n"
                        "    *telegram*) echo \"$3\" >> told ;;\n"
                        "  esac\n"
                        "  exit 0\n"
                        "fi\n"
                        "if [ \"$3\" = sena.phone ]; then echo SCREEN; exit 0; fi\n"
                        "n=$(( $(cat runs 2>/dev/null || echo 0) + 1 )); echo $n > runs\n"
                        "code=$(sed -n \"${n}p\" codes)\n"
                        "echo \"CORE $n $code\"\n"
                        "[ \"$code\" = stop ] && { touch \"${SENA_DATA:-data}/state/stop_requested\"; exit 0; }\n"
                        "exit ${code:-78}\n", encoding="utf-8")
        fake.chmod(0o755)
        # git never leaves the throwaway folder and never reads the owner's own settings (hooks, signing)
        self.git_env = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, "GIT_CEILING_DIRECTORIES": str(self.folder.parent),
                        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

    def run_script(self, codes, timeout=30, quick=False, data=None):
        (self.folder / "codes").write_text("\n".join(codes) + "\n", encoding="utf-8")
        env = {**self.git_env, "PYTHON": str(self.folder / "python"), "SENA_DATA": str(data or self.folder / "data")}
        if quick:   # the pauses between restarts pass at once: a sleep that barely sleeps comes first on the PATH
            (self.folder / "bin").mkdir(exist_ok=True)
            (self.folder / "bin" / "sleep").write_text("#!/bin/sh\nexec /bin/sleep 0.05\n", encoding="utf-8")
            (self.folder / "bin" / "sleep").chmod(0o755)
            env["PATH"] = f"{self.folder / 'bin'}:{env['PATH']}"
        return subprocess.run(["/bin/zsh", str(self.folder / "run.sh")], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)

    def log(self, data=None):
        return Path(data or self.folder / "data", "logs", "bot.log").read_text(encoding="utf-8")

    def told(self, wait=5.0):
        """What run.sh sent the owner (in the background: it may land a moment after run.sh is gone)."""
        path, deadline = self.folder / "told", time.time() + wait
        while not path.exists() and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.1)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def git(self, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args], cwd=self.folder,
                              env=self.git_env, capture_output=True, text=True, check=True).stdout.strip()

    def updated(self):
        """The throwaway folder as a repository right after an approved change to her own code: the update's commit on top of the
        one before it, and update_pending as ag_code.try_apply_change writes it ("<before> <id> <after>"), from before this run."""
        self.git("init", "-q")
        (self.folder / "app.txt").write_text("old\n", encoding="utf-8")
        (self.folder / "notes.txt").write_text("notes\n", encoding="utf-8")
        self.git("add", "app.txt", "notes.txt")
        self.git("commit", "-qm", "before")
        before = self.git("rev-parse", "HEAD")
        (self.folder / "app.txt").write_text("new\n", encoding="utf-8")
        (self.folder / "added.txt").write_text("added by the update\n", encoding="utf-8")
        self.git("add", "app.txt", "added.txt")
        self.git("commit", "-qm", "update")
        after = self.git("rev-parse", "HEAD")
        self.pending = self.folder / "data" / "state" / "update_pending"
        self.pending.parent.mkdir(parents=True, exist_ok=True)
        self.pending.write_text(f"{before} ab12 {after}\n", encoding="utf-8")
        past = time.time() - 30
        os.utime(self.pending, (past, past))
        return before, after

    def test_a_start_that_cannot_work_is_not_restarted_forever(self):
        """Missing keys or a port held by another Sena: a restart can't help (the audit: it looped every 3 seconds forever). The
        screen comes back, so the phone still has its server (round 2: «Включить» with a key missing left the phone nothing), and
        the owner hears why."""
        done = self.run_script([str(app.NO_RESTART)])
        self.assertEqual(done.returncode, 0)   # launchd's KeepAlive (only on failure) doesn't bring it back either
        self.assertEqual(self.log().count("CORE"), 1)
        self.assertIn("перезапускать не буду", self.log())
        self.assertTrue(self.log().rstrip().endswith("SCREEN"))
        self.assertFalse((self.folder / "data" / "state" / "core_starting").exists())
        self.assertEqual(done.stdout, "")   # not from a terminal: everything goes to bot.log only, no second log grows
        self.assertIn("Я не запустилась: CORE 1 78", self.told())   # the core's last line is the reason it gave

    def test_a_crash_is_restarted_and_a_stop_leaves_the_screen(self):
        done = self.run_script(["1", "stop"])
        self.assertEqual(done.returncode, 0)
        log = self.log()
        self.assertIn("CORE 1 1", log)
        self.assertIn("перезапуск через 3 с", log)
        self.assertIn("CORE 2 stop", log)
        self.assertIn("SCREEN", log)

    def test_a_core_that_falls_again_and_again_leaves_the_screen(self):
        """A core that can't live (an import error, a broken update) looped every 3 seconds forever: now the pause grows, and
        after five falls in a row the screen stays — the apps work and say Sena is off; «Запустить» tries again."""
        done = self.run_script(["1"] * 5, quick=True)
        self.assertEqual(done.returncode, 0)
        log = self.log()
        self.assertEqual(log.count("CORE"), 5)
        self.assertEqual([int(n) for n in re.findall(r"перезапуск через (\d+) с", log)], [3, 6, 12, 24])
        self.assertIn("упала 5 раз подряд", log)
        self.assertTrue(log.rstrip().endswith("SCREEN"))
        self.assertFalse((self.folder / "data" / "state" / "core_starting").exists())
        self.assertIn("падаю раз за разом", self.told())   # round 2: he was told nothing while away with only Telegram

    def test_a_fall_right_after_an_update_rolls_back_without_wiping_his_work_and_starts_the_old_code(self):
        """Round 2 (quality Q-25): `git reset --hard` took the whole tree back, the owner's uncommitted work with it. --keep moves
        only what the update changed; his edits elsewhere and his new files stay, and the old code is started."""
        before, _ = self.updated()
        (self.folder / "notes.txt").write_text("his work, not committed\n", encoding="utf-8")
        (self.folder / "draft.txt").write_text("a new file of his\n", encoding="utf-8")
        self.run_script(["1", "stop"], quick=True)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertEqual((self.folder / "app.txt").read_text(encoding="utf-8"), "old\n")
        self.assertFalse((self.folder / "added.txt").exists())
        self.assertEqual((self.folder / "notes.txt").read_text(encoding="utf-8"), "his work, not committed\n")
        self.assertTrue((self.folder / "draft.txt").exists())
        self.assertIn("откатилась на", self.log())
        self.assertIn("CORE 2 stop", self.log())   # the old code was started
        self.assertFalse(self.pending.exists())
        self.assertIn("откатилась на прежний код", self.told())

    def test_an_update_that_cannot_start_is_rolled_back_and_the_old_code_started(self):
        """Round 2 (R6): the new code needed a key .env lacks — exit 78 — and run.sh rolled back and then left, the old code never started."""
        before, _ = self.updated()
        self.run_script([str(app.NO_RESTART), "stop"], quick=True)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertIn("CORE 2 stop", self.log())

    def test_a_stop_or_a_restart_right_after_an_update_is_no_verdict_on_it(self):
        """Round 2 (Q-25, proven): «Остановить Сену» in the first two minutes was taken for a crash and the update rolled back."""
        _, after = self.updated()
        self.run_script(["3", "stop"], quick=True)
        self.assertEqual(self.git("rev-parse", "HEAD"), after)
        self.assertEqual((self.folder / "app.txt").read_text(encoding="utf-8"), "new\n")
        self.assertNotIn("откат", self.log())
        self.assertTrue(self.pending.exists())   # the next run gives the verdict
        self.assertEqual(self.told(wait=0.3), "")

    def test_a_rollback_never_wipes_what_nobody_committed_in_the_files_it_would_move(self):
        """The owner edits a file the update changed and Sena falls: the rollback refuses and says so, his edit stays."""
        _, after = self.updated()
        (self.folder / "app.txt").write_text("new\nhis line, not committed\n", encoding="utf-8")
        self.run_script(["1", "stop"], quick=True)
        self.assertEqual(self.git("rev-parse", "HEAD"), after)
        self.assertEqual((self.folder / "app.txt").read_text(encoding="utf-8"), "new\nhis line, not committed\n")
        self.assertIn("откатиться на", self.log())
        self.assertIn("откатиться я не смогла", self.told())
        self.assertFalse(self.pending.exists())   # told once, not on every next fall

    def test_run_sh_follows_the_data_folder_the_core_uses(self):
        """Round 2 (S1): run.sh had data/ written in, the core and the apps SENA_DATA: «Остановить» restarted the core instead
        (run.sh never saw the stop request), the start mark and the update record never met."""
        elsewhere = self.folder / "elsewhere"
        done = self.run_script(["stop"], data=elsewhere)
        self.assertEqual(done.returncode, 0)
        log = self.log(elsewhere)
        self.assertIn("CORE 1 stop", log)
        self.assertTrue(log.rstrip().endswith("SCREEN"))   # the stop request was found where the core wrote it
        self.assertTrue((elsewhere / "state" / "run.lock").exists())
        self.assertFalse((self.folder / "data").exists())

    def test_one_run_sh_at_a_time(self):
        """Two run.sh loops ran two cores (the audit: launchd's and the one «Запустить» started): the second waits for the first."""
        import fcntl
        (self.folder / "data" / "state").mkdir(parents=True)
        lock = open(self.folder / "data" / "state" / "run.lock", "a")
        fcntl.lockf(lock, fcntl.LOCK_EX)
        threading.Timer(1.0, lambda: (fcntl.lockf(lock, fcntl.LOCK_UN), lock.close())).start()   # the first one hands over
        started = time.time()
        self.run_script([str(app.NO_RESTART)])
        self.assertGreaterEqual(time.time() - started, 0.9)   # it waited for the lock
        self.assertIn("CORE 1", self.log())   # and then ran


class Plist(unittest.TestCase):
    def test_launchd_brings_run_sh_back_only_after_a_failure(self):
        with open(ROOT / "tools" / "launchd" / "com.sena.agent.plist", "rb") as f:
            job = plistlib.load(f)
        self.assertEqual(job["KeepAlive"], {"SuccessfulExit": False})   # a stop or a second run.sh exits 0: not started again on top
        self.assertTrue(job["RunAtLoad"])


if __name__ == "__main__":
    unittest.main()
