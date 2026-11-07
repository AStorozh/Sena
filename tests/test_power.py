"""Switching Sena off: the screen without the core spends nothing and starts nothing whatever is pressed; a stop finishes or
writes down the running tasks and stops her own programs — and only hers (the owner's Claude Code and Chrome are left alone);
a git lock left by a killed command goes only when it is surely stale."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from sena import app, config, scheduler
from sena.agents import ag_code, programs
from sena.agents.ag_web import browser
from sena.core import cancel, limits, llm, local, power, seatbelt, status
from sena.google import media
from sena.memory import history, task_log
from sena.phone import desk, server


NESTED = "внутри песочницы ps не запускается"   # the tests of Sena's copy run inside one: this runs in the ordinary suite

class Off(unittest.TestCase):
    """The screen without the core: power.set_off() — and every door that spends or starts a program is shut."""
    def setUp(self):
        power.set_off()
        self.addCleanup(setattr, power, "_off", False)

    def test_no_paid_call_goes_out(self):
        with self.assertRaises(limits.LimitReached) as said:
            limits.budget_check()
        self.assertIn("Сена выключена", str(said.exception))
        with mock.patch.object(llm, "_api", side_effect=AssertionError("в облако не ходим")):
            for call in (lambda: llm.respond(purpose="тест", model=config.MODEL_FAST, input="привет"), lambda: media.speech("привет"),
                         lambda: llm.embeddings(["привет"]), lambda: media.transcribe(b"ogg")):
                with self.assertRaises(limits.LimitReached):
                    call()

    def test_no_home_program_starts(self):
        with mock.patch.object(local.subprocess, "Popen", side_effect=AssertionError("сервер моделей не запускаем")):
            with self.assertRaises(local.Unavailable):
                local._ensure()
        for call in (lambda: local.transcribe("x.wav"), lambda: local.speak("привет"), lambda: local.voice_print("x.wav")):
            with self.assertRaises(local.Unavailable):
                call()

    def test_no_agent_program_starts(self):
        with mock.patch.object(ag_code.subprocess, "Popen", side_effect=AssertionError("Claude Code не запускаем")):
            self.assertIn("Сена выключена", ag_code._claude(config.ROOT, "задача", "тест")["text"])
        with self.assertRaises(RuntimeError):
            browser.context()

    def test_the_remote_refuses_what_needs_her_and_does_the_rest(self):
        self.assertIn("Сена выключена", desk.act("tidy", {})["error"])
        self.assertNotIn("error", desk.act("settings", {}))

    def test_the_server_refuses_a_message_and_answers_the_rest(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        conn = HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
        self.addCleanup(conn.close)
        conn.request("POST", f"/ask?t={config.APP_TOKEN}", body=json.dumps({"text": "привет"}).encode())
        answer = conn.getresponse()
        self.assertEqual(answer.status, 409)
        self.assertIn("Сена выключена", json.loads(answer.read())["error"])
        conn.request("GET", f"/desk/api/history?t={config.APP_TOKEN}")   # reading goes on: the history, memory, journal stay at hand
        self.assertEqual(conn.getresponse().status, 200)


class Stopping(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, power, "_stopping", False)

    def test_the_scheduler_starts_nothing_once_stopping(self):
        power.set_stopping()
        job = mock.Mock()
        done = threading.Event()
        thread = threading.Thread(target=lambda: (scheduler._loop(job, job, job), done.set()), daemon=True)
        thread.start()
        self.assertTrue(done.wait(2))   # the loop returned at once
        job.assert_not_called()

    def test_no_home_program_starts_once_stopping(self):
        """The concurrency audit of 25.10: a task still running after her programs were stopped started the model server again."""
        power.set_stopping()
        with mock.patch.object(local.subprocess, "Popen", side_effect=AssertionError("сервер моделей не запускаем")):
            with self.assertRaises(local.Unavailable):
                local._ensure()
        with self.assertRaises(local.Unavailable):
            local.transcribe("x.wav")

    def test_after_a_fall_the_tasks_it_cut_are_written_down_and_told_once(self):
        """n8n marks runs a crash left unfinished: a graceful stop wrote cut tasks down, a fall left them nowhere. The audit of
        25.10: the last start was looked for among the last 40 "start" events, and Telegram's reconnects are "start" too."""
        from sena import app
        from sena.core import events
        from sena.memory import task_log
        events.log("start", "Сена запущена")
        events.log("task_start", "найди билеты в Порту", tid="fall1")
        events.log("task_start", "найди билеты в Порту", tid="fall1", direct=True)   # one task logged twice is one task
        events.log("task_start", "который час", tid="done1")
        events.log("task_end", "13:05", tid="done1")
        events.log("task_start", "уборка", tid="svc1", service=True)
        for _ in range(45):
            events.log("start", "связь с Телеграмом вернулась")
        told = []
        with mock.patch.object(app, "_tell", told.append):
            cut = app._after_a_fall()
        self.assertEqual([e["tid"] for e in cut], ["fall1"])   # not the finished one, not the service one
        self.assertEqual(task_log.get("fall1")["answer"], "Прервано: Сена упала")
        self.assertEqual(len(told), 1)
        self.assertIn("найди билеты в Порту", told[0])
        events.log("start", "Сена запущена")
        events.log("task_start", "переведи письмо", tid="fall2")
        events.log("stop", "Сена останавливается")   # a stop that began and was cut half way is no clean stop
        with mock.patch.object(app, "_tell", told.append):
            self.assertEqual([e["tid"] for e in app._after_a_fall()], ["fall2"])
            events.log("start", "Сена запущена")
            events.log("task_start", "открой сайт", tid="fall3")
            events.log("stop", "Сена остановлена")
            self.assertEqual(app._after_a_fall(), [])   # a stop to the end: its tasks were written down by the stop itself

    def test_a_stuck_task_is_written_down_as_cut_off_and_the_conversation_told(self):
        stuck = {"id": "t-stuck", "text": "найди билеты", "start": time.time(), "steps": [], "done": False, "answer": None, "service": False}
        status.tasks[424242] = stuck
        self.addCleanup(status.tasks.pop, 424242, None)
        self.addCleanup(cancel.cancelled.discard, "t-stuck")   # the stop flag it set doesn't outlive the test
        with mock.patch.object(app, "STOP_WAIT", 0.3):
            app._finish_running()
        self.assertNotIn(424242, status.tasks)
        entry = next(e for e in task_log.items if e["id"] == "t-stuck")
        self.assertIn("Прервано", entry["answer"])
        last = history.messages(config.OWNER_ID)[-1]
        self.assertEqual(last["role"], "assistant")
        self.assertIn("найди билеты", json.dumps(last, ensure_ascii=False))

    def test_a_task_that_ends_in_time_is_not_written_twice(self):
        quick = {"id": "t-quick", "text": "который час", "start": time.time(), "steps": [], "done": False, "answer": None, "service": False}
        status.tasks[434343] = quick
        self.addCleanup(cancel.cancelled.discard, "t-quick")
        threading.Timer(0.2, lambda: status.tasks.pop(434343, None)).start()   # it finishes on its own after the stop signal
        app._finish_running()
        self.assertFalse(any(e["id"] == "t-quick" for e in task_log.items))


class Programs(unittest.TestCase):
    def process(self, *args):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", *args])
        self.addCleanup(lambda: (p.kill(), p.wait()))
        return p

    def test_every_program_is_stopped_even_if_one_fails(self):
        with mock.patch.object(programs.local, "stop", side_effect=RuntimeError("не ответил")), \
                mock.patch.object(programs.browser, "stop", return_value=True), \
                mock.patch.object(programs.ag_code, "stop_children", return_value=2), \
                mock.patch.object(programs.ag_code, "clear_stale_lock", return_value=False):
            self.assertEqual(programs.stop_all(), ["Chrome помощника", "Claude Code"])

    @unittest.skipUnless(seatbelt.available(), NESTED)
    def test_senas_claude_code_run_is_stopped_and_nothing_else(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))   # removed after the test
        fake = folder / "claude"
        fake.write_text("#!/bin/sh\nsleep 30\n")
        fake.chmod(0o755)
        other = self.process()   # a program that isn't hers, its pid on her list
        with mock.patch.dict(os.environ, {"PATH": f"{folder}:{os.environ['PATH']}"}):
            run = threading.Thread(target=ag_code._claude, args=(folder, "задача", "тест"), daemon=True)
            run.start()
            for _ in range(50):
                if ag_code._children:
                    break
                time.sleep(0.05)
        ag_code.store.save_json(ag_code.CHILDREN_FILE, sorted(ag_code._children) + [other.pid])
        self.assertEqual(ag_code.stop_children(), 1)
        run.join(5)
        self.assertFalse(run.is_alive())   # the run ended with its process
        self.assertIsNone(other.poll())    # the other program lives on
        self.assertFalse(ag_code.CHILDREN_FILE.exists())

    def test_the_agents_chrome_is_found_by_its_own_profile(self):
        mine, someone = self.process(f"--user-data-dir={config.BROWSER_PROFILE}"), self.process("--user-data-dir=/tmp/the-owners-chrome")
        time.sleep(0.2)
        self.assertTrue(browser.stop())
        mine.wait(5)
        self.assertIsNone(someone.poll())

    def test_a_git_lock_goes_only_when_old_and_no_git_runs(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / ".git").mkdir()
        lock = root / ".git" / "index.lock"
        lock.write_text("")
        with mock.patch.object(ag_code.config, "ROOT", root):
            self.assertFalse(ag_code.clear_stale_lock())   # fresh: someone may be working
            old = time.time() - ag_code.LOCK_AGE - 5
            os.utime(lock, (old, old))
            with mock.patch.object(ag_code.subprocess, "run", return_value=mock.Mock(returncode=0)):
                self.assertFalse(ag_code.clear_stale_lock())   # a git is running somewhere: not ours to touch
            with mock.patch.object(ag_code.subprocess, "run", return_value=mock.Mock(returncode=1)):
                self.assertTrue(ag_code.clear_stale_lock())
        self.assertFalse(lock.exists())

    def test_a_process_that_shares_the_data_never_stops_another_living_senas_server(self):
        """The audit of 25.10: an exam or a script exiting with the real data folder stopped the running core's home models."""
        def stand_in(*args, group=False):
            p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", *args], start_new_session=group)
            self.addCleanup(lambda: (p.kill(), p.wait()))
            return p
        owner, server = stand_in("-m", "sena"), stand_in(group=True)   # a living Sena (python … -m sena) and its server's group
        with mock.patch.object(local, "SERVER", "Python"):   # the stand-in server passes the "it is a llama-server" check
            local.PID_FILE.write_text(json.dumps({"pid": server.pid, "owner": owner.pid}))
            self.assertFalse(local.stop())
            self.assertIsNone(server.poll())   # the living core's server keeps running
            self.assertTrue(local.PID_FILE.exists())
            owner.kill()
            owner.wait(5)
            self.assertTrue(local.stop())   # its owner is gone: now it is a leftover, and it goes
        server.wait(5)
        self.assertFalse(local.PID_FILE.exists())

    def test_only_a_python_running_senas_modules_counts_as_a_living_sena(self):
        """The audit of 25.10: any process whose command line said "sena" (an editor or a terminal in the project folder) and a
        check that got no answer counted as a living owner — its model server was then never stopped."""
        editor, core, exam = self.process(str(config.ROOT / "sena" / "app.py")), self.process("-m", "sena"), self.process("-m", "sena.exam", "run")
        self.assertFalse(local._lives(editor.pid))
        self.assertTrue(local._lives(core.pid))
        self.assertTrue(local._lives(exam.pid))
        with mock.patch.object(local.subprocess, "run", side_effect=subprocess.TimeoutExpired("ps", 3)):
            self.assertFalse(local._lives(core.pid))

    def test_a_pid_file_naming_another_program_stops_nothing(self):
        other = self.process()
        local.PID_FILE.write_text(json.dumps({"pid": other.pid}))
        self.assertFalse(local.stop())
        self.assertIsNone(other.poll())
        self.assertFalse(local.PID_FILE.exists())


class Restarting(unittest.TestCase):
    def test_a_restart_asked_for_is_known_until_the_core_goes(self):
        """The audit of 26.10: while a restart onto new code waited for the running tasks, the scheduler and the long works kept
        starting new ones, so the wait ran its full two minutes and then cut them."""
        self.addCleanup(setattr, power, "_restart", False)
        self.assertFalse(power.is_restarting())
        power.request_restart()
        self.assertTrue(power.is_restarting())
        self.assertEqual(power.exit_code(), 3)

    def test_the_refusal_while_off_speaks_of_her_in_one_person(self):
        for what in ("отвечать", "платные вызовы", "Claude Code"):
            said = power.refusal(what)
            self.assertTrue(said.startswith(f"Сена выключена: {what} — только когда она работает"), said)
            self.assertNotIn("не делаю", said)


if __name__ == "__main__":
    unittest.main()
