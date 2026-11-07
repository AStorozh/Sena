"""Luma: Sena calls its tools directly over HTTP, gets the tool list from Luma itself, opens the app if it's closed, and never reaches past this Mac."""
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from sena import config
from sena.agents.ag_sena import tools
from sena.core import settings, status
from sena.core.tools import Failure
from sena.world import luma

OPENED = subprocess.CompletedProcess(["open"], 0, stdout="", stderr="")

CATALOGUE = [
    {"name": "get_today", "group": "context", "scope": "read", "description": "Returns everything about one day in a single call. Call it first.",
     "inputSchema": {"type": "object", "properties": {"date": {"type": "string"}}}, "examples": [{}, {"date": "tomorrow"}]},
    {"name": "task_add", "group": "tasks", "scope": "write", "description": "Creates one or more tasks (1-20 per call). Use it when the user states something to DO.",
     "inputSchema": {"type": "object", "properties": {"items": {"type": "array"}}, "required": ["items"]}, "examples": [{"items": [{"text": "Позвонить Диме"}]}]},
    {"name": "task_move", "group": "tasks", "scope": "write", "description": "Moves cards to another stage, e.g. \"В работе\". The project is needed.", "inputSchema": {"type": "object"}},
    {"name": "habit_log", "group": "tracking", "scope": "write", "description": "Records a habit for a day.", "inputSchema": {"type": "object"}},
]


class FakeLuma(BaseHTTPRequestHandler):
    """Stands in for the real Luma: /health, /tools and /tools/<name>. Records what came in and replies with what was set."""
    seen = []
    keys = []          # the Idempotency-Key of each POST
    reply = {}
    code = 200
    catalogue = CATALOGUE
    replay = False     # answer as a replayed write
    slow = 0           # seconds the next POST waits before answering

    def log_message(self, *args):
        pass

    def answer(self, code, data):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        if FakeLuma.replay:
            self.send_header("Idempotent-Replay", "true")
        try:
            self.end_headers()
            self.wfile.write(raw)
        except BrokenPipeError:   # the client stopped waiting (the timeout test)
            pass

    def do_GET(self):
        if self.path.endswith("/health"):
            return self.answer(200, {"ok": True})
        FakeLuma.seen.append(("GET", self.path, self.headers.get("Authorization"), None))
        self.answer(FakeLuma.code, FakeLuma.catalogue if FakeLuma.code == 200 else FakeLuma.reply)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        FakeLuma.seen.append(("POST", self.path, self.headers.get("Authorization"), body))
        FakeLuma.keys.append(self.headers.get("Idempotency-Key"))
        if FakeLuma.slow:
            wait, FakeLuma.slow = FakeLuma.slow, 0
            time.sleep(wait)
        self.answer(FakeLuma.code, FakeLuma.reply)


class Luma(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeLuma)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        FakeLuma.seen, FakeLuma.keys = [], []
        FakeLuma.code, FakeLuma.replay, FakeLuma.slow = 200, False, 0
        FakeLuma.catalogue = CATALOGUE
        luma._unanswered.clear()
        FakeLuma.reply = {"ok": True, "result": {"created": [{"id": "abc", "title": "Позвонить Диме"}], "message": "Created 1 task."}}
        # the real Luma app is never launched from the tests under any failure: the path leads nowhere until a test supplies its own
        for patch in (mock.patch.object(config, "LUMA_URL", self.url), mock.patch.object(config, "LUMA_KEY", ""),
                      mock.patch.object(config, "LUMA_APP", Path("/нет/такой/Luma.app")), mock.patch.object(config, "LUMA_PORT_FILE", None)):
            patch.start()
            self.addCleanup(patch.stop)

    # ---------- calling a tool ----------

    def test_call_goes_to_the_tool_and_the_answer_is_fenced_but_the_owners_own(self):
        status.begin("тест: дело в Luma")
        self.addCleanup(status.end, "")
        task = status.current()
        text = luma.luma("task_add", {"items": [{"text": "Позвонить Диме завтра в 15:00"}]})
        self.assertEqual(FakeLuma.seen, [("POST", "/api/v1/tools/task_add", None, {"items": [{"text": "Позвонить Диме завтра в 15:00"}]})])
        self.assertIn('"title":"Позвонить Диме"', text)   # the answer is compact: no indentation, to avoid wasting tokens
        self.assertIn("ВНЕШНИЕ ДАННЫЕ", text)   # Luma's data can contain foreign text: the model doesn't execute commands from there
        self.assertIsNone(task.get("outside"))   # but it is the owner's own app: the next coding job doesn't wait for a "yes"

    def test_input_may_be_missing(self):
        luma.luma("get_today")
        luma.luma("get_today", "")
        self.assertEqual([seen[3] for seen in FakeLuma.seen], [{}, {}])

    def test_every_call_has_its_own_key_and_an_unanswered_one_is_retried_with_the_same(self):
        luma.luma("task_add", {"items": [{"title": "Купить хлеб"}]})
        luma.luma("task_add", {"items": [{"title": "Купить хлеб"}]})
        self.assertNotEqual(FakeLuma.keys[0], FakeLuma.keys[1])   # two answered calls are two calls
        FakeLuma.slow = 1
        with mock.patch.object(luma, "CALL_TIMEOUT", 0.3), self.assertRaises(luma.LumaError):
            luma.luma("expense_add", {"items": [{"amount": 5}]})   # no answer in time: Luma may still have saved it
        luma.luma("expense_add", {"items": [{"amount": 5}]})   # the model's retry of exactly that call
        self.assertEqual(FakeLuma.keys[2], FakeLuma.keys[3])
        luma.luma("expense_add", {"items": [{"amount": 5}]})   # answered before: a new one is a new expense
        self.assertNotEqual(FakeLuma.keys[3], FakeLuma.keys[4])

    def test_a_replayed_answer_is_said_to_be_a_repeat(self):
        FakeLuma.replay = True
        self.assertIn("повтор", luma.luma("task_add", {"items": [{"title": "x"}]}))

    def test_a_batch_where_everything_was_refused_is_a_failure(self):
        FakeLuma.reply = {"ok": True, "result": {"updated": [], "failed": [{"index": 0, "error": "No tasks match \"Тест\""}],
                                                 "message": "Updated 0 tasks. 1 failed"}}
        self.assertIsInstance(luma.luma("task_update", {"items": [{"task": "Тест", "status": "done"}]}), Failure)
        with self.assertRaises(luma.LumaError) as caught:
            luma.call("task_update", {"items": [{"task": "Тест", "status": "done"}]})
        self.assertIn("No tasks match", str(caught.exception))
        FakeLuma.reply = {"ok": True, "result": {"updated": [{"id": "t1"}], "failed": [{"index": 1, "error": "No tasks match"}], "message": ""}}
        text = luma.luma("task_update", {"items": [{"task": "t1", "status": "done"}, {"task": "Тест", "status": "done"}]})
        self.assertNotIsInstance(text, Failure)
        self.assertIn("Не всё прошло", text)
        self.assertEqual(luma.call("task_update", {"items": [{"task": "t1"}, {"task": "Тест"}]})["updated"], [{"id": "t1"}])

    def test_sena_s_own_code_can_read_refusals_item_by_item(self):
        """A work's sync moving a card the owner deleted: the whole batch is that one refusal — his decision about the card, which
        the raise turned into a failed sync (five of them switched the project's sync off)."""
        FakeLuma.reply = {"ok": True, "result": {"updated": [], "failed": [{"index": 0, "item": {"task": "c1"}, "error": "No tasks match"}]}}
        made = luma.call("task_update", {"items": [{"task": "c1", "status": "done"}]}, per_item=True)
        self.assertEqual(made["failed"][0]["error"], "No tasks match")
        with self.assertRaises(luma.LumaError):
            luma.call("task_update", {"items": [{"task": "c1", "status": "done"}]})   # as data it is the caller's to ask for

    def test_the_port_luma_took_is_read_from_its_file(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            dead = s.getsockname()[1]
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: [f.unlink() for f in folder.iterdir()] and folder.rmdir())
        pid = folder / "server.pid"
        pid.write_text(json.dumps({"pid": 1, "port": self.httpd.server_port, "started": 0}))
        with mock.patch.object(config, "LUMA_URL", f"http://127.0.0.1:{dead}"), mock.patch.object(config, "LUMA_PORT_FILE", pid):
            luma.luma("get_today")   # 3110 was busy: Luma took another port and said so in its file
            pid.write_text("не json")
            self.assertFalse(luma.is_up())   # a broken file: back to the address from .env

    def test_input_that_is_not_an_object_is_refused_before_sending(self):
        FakeLuma.seen = []
        for bad in ([1, 2], "не json", "[1]", 5):
            with self.assertRaises(luma.LumaError):
                luma.luma("get_today", bad)
        self.assertEqual(FakeLuma.seen, [])

    def test_key_is_sent_only_when_set(self):
        luma.luma("get_today")
        luma.luma_tools()
        with mock.patch.object(config, "LUMA_KEY", "luma_abc"):
            luma.luma("get_today")
            luma.luma_tools()
        self.assertEqual([seen[2] for seen in FakeLuma.seen], [None, None, "Bearer luma_abc", "Bearer luma_abc"])

    def test_tool_name_cannot_reach_other_addresses(self):
        # "../keys" through the tool address would reach Luma's key list: the name is accepted only as a bare name
        for bad in ("../keys", "a/b", "", "Task_Add", "get_today?x=1", "get today", None):
            with self.assertRaises(luma.LumaError):
                luma.luma(bad)
        self.assertEqual(FakeLuma.seen, [])

    def test_empty_result_is_said_plainly(self):
        FakeLuma.reply = {"ok": True, "result": None}
        self.assertIn("Готово", luma.luma("trash_restore", {"item": "x"}))

    def test_long_answer_goes_whole_to_a_file(self):
        FakeLuma.reply = {"ok": True, "result": {"text": "я" * 30000 + "КОНЕЦ"}}
        text = luma.luma("note_read", {"note": "x"})
        self.assertLess(len(text), luma.RESULT_LIMIT + 800)
        path = next(word for word in text.split() if word.endswith(".txt"))
        self.assertIn("я" * 30000 + "КОНЕЦ", Path(path.strip("«»()[],.:;")).read_text(encoding="utf-8"))   # nothing lost from the middle

    def test_refusal_is_explained_in_lumas_words(self):
        FakeLuma.code = 403
        FakeLuma.reply = {"error": {"code": "tool_group_disabled", "message": "The \"finance\" tools are switched off", "details": None}}
        with self.assertRaises(luma.LumaError) as caught:
            luma.luma("expense_add", {"items": []})
        self.assertIn("tool_group_disabled", str(caught.exception))

    def test_validation_error_keeps_the_fields_luma_names(self):
        FakeLuma.code = 400
        FakeLuma.reply = {"error": {"code": "validation", "message": "Invalid input for task_add:\n- items.0: Unrecognized key: \"titel\"", "details": []}}
        with self.assertRaises(luma.LumaError) as caught:
            luma.luma("task_add", {"items": [{"titel": "x"}]})
        self.assertIn("titel", str(caught.exception))   # the model will fix the field based on this text

    def test_wrong_key_points_to_the_env_file(self):
        FakeLuma.code = 401
        FakeLuma.reply = {"error": {"code": "unauthorized", "message": "Ключ API недействителен", "details": None}}
        with self.assertRaises(luma.LumaError) as caught:
            luma.luma("get_today")
        self.assertIn("LUMA_KEY", str(caught.exception))

    def test_odd_reply_is_an_error_not_a_crash(self):
        FakeLuma.reply = {"что-то": "другое"}
        with self.assertRaises(luma.LumaError):
            luma.luma("get_today")

    def test_only_this_mac_is_allowed(self):
        for address in ("http://192.168.1.5:3110", "http://evil.example"):
            with mock.patch.object(config, "LUMA_URL", address):
                with self.assertRaises(luma.LumaError):
                    luma.luma("get_today")
                with self.assertRaises(luma.LumaError):
                    luma.luma_tools()

    # ---------- what Luma can do: the list comes from Luma itself ----------

    def test_overview_lists_groups_and_tool_names(self):
        text = luma.luma_tools()
        self.assertIn("tasks (2): task_add, task_move", text)
        self.assertIn("tracking (1): habit_log", text)
        self.assertEqual(FakeLuma.seen, [("GET", "/api/v1/tools", None, None)])

    def test_group_gives_one_short_line_per_tool(self):
        text = luma.luma_tools("TASKS")
        self.assertIn("task_add [запись] — Creates one or more tasks (1-20 per call).", text)
        self.assertNotIn("Use it when", text)   # beyond the first sentence is in the tool's own description
        self.assertIn('Moves cards to another stage, e.g. "В работе".', text)   # "e.g." isn't the end of a sentence
        self.assertNotIn("The project is needed", text)
        self.assertNotIn("habit_log", text)

    def test_tool_gives_description_schema_and_examples(self):
        text = luma.luma_tools("task_add")
        self.assertIn("Use it when the user states something to DO", text)
        self.assertIn('"required":["items"]', text)
        self.assertIn('"text":"Позвонить Диме"', text)

    def test_unknown_name_offers_the_nearest(self):
        with self.assertRaises(luma.LumaError) as caught:
            luma.luma_tools("taks")
        self.assertIn("tasks", str(caught.exception))

    def test_odd_catalogue_is_an_error(self):
        FakeLuma.catalogue = {"не": "список"}
        with self.assertRaises(luma.LumaError):
            luma.luma_tools()

    def test_the_system_check_sees_the_key_and_the_groups(self):
        """A bare /health said "Luma отвечает" with a revoked key or the tools switched off."""
        state, said = luma.check()
        self.assertFalse(state)
        self.assertIn("planning, calendar", said)   # the fake's catalogue has no planning and calendar groups
        FakeLuma.catalogue = CATALOGUE + [{"name": "project_add", "group": "planning"}, {"name": "event_add", "group": "calendar"}]
        self.assertEqual(luma.check()[0], True)
        FakeLuma.code = 401
        FakeLuma.reply = {"error": {"code": "unauthorized", "message": "Ключ API недействителен", "details": None}}
        state, said = luma.check()
        self.assertFalse(state)
        self.assertIn("LUMA_KEY", said)
        with mock.patch.object(luma, "is_up", return_value=False):
            self.assertEqual(luma.check(), (None, "не запущена"))

    # ---------- launching the app ----------

    def test_closed_luma_is_opened_without_electron_flag_and_without_sena_keys(self):
        app = Path(tempfile.mkdtemp())
        self.addCleanup(app.rmdir)
        logged = []
        with mock.patch.object(config, "LUMA_APP", app), mock.patch.object(luma, "is_up", side_effect=[False, False, True]), \
                mock.patch.object(luma.time, "sleep"), mock.patch.object(luma.subprocess, "run", return_value=OPENED) as run, \
                mock.patch.dict(os.environ, {"ELECTRON_RUN_AS_NODE": "1", "OPENAI_API_KEY": "sk-x"}), \
                mock.patch.object(luma.events, "log", lambda kind, text, **data: logged.append((kind, text))):
            luma.start()
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["open", "-g", str(app)])
        self.assertNotIn("ELECTRON_RUN_AS_NODE", run.call_args.kwargs["env"])
        self.assertNotIn("OPENAI_API_KEY", run.call_args.kwargs["env"])
        self.assertEqual(logged, [("app", "открываю программу Luma")])   # a program started, not a request ("fetch" is HTTP)

    def test_call_opens_a_closed_luma_first(self):
        with mock.patch.object(luma, "start") as start:
            luma.luma("get_today")
            luma.luma_tools()
        self.assertEqual(start.call_count, 2)

    def test_running_luma_is_not_opened_again(self):
        with mock.patch.object(luma.subprocess, "run") as run:
            luma.start()
        run.assert_not_called()

    def test_missing_app_is_said_plainly(self):
        with mock.patch.object(luma, "is_up", return_value=False), mock.patch.object(config, "LUMA_APP", Path("/нет/такой/Luma.app")), \
                self.assertRaises(luma.LumaError) as caught:
            luma.start()
        self.assertIn("LUMA_APP", str(caught.exception))

    def test_server_that_never_wakes_gives_up(self):
        app = Path(tempfile.mkdtemp())
        self.addCleanup(app.rmdir)
        with mock.patch.object(config, "LUMA_APP", app), mock.patch.object(luma, "is_up", return_value=False), \
                mock.patch.object(luma.subprocess, "run", return_value=OPENED), mock.patch.object(luma, "START_WAIT", 0), self.assertRaises(luma.LumaError):
            luma.start()

    def test_an_app_macos_refused_to_open_is_said_at_once(self):
        app = Path(tempfile.mkdtemp())
        self.addCleanup(app.rmdir)
        refused = subprocess.CompletedProcess(["open"], 1, stdout="", stderr="LSOpenURLsWithRole() failed with error -10810")
        with mock.patch.object(config, "LUMA_APP", app), mock.patch.object(luma, "is_up", return_value=False), \
                mock.patch.object(luma.subprocess, "run", return_value=refused), mock.patch.object(luma.time, "sleep") as slept, \
                self.assertRaises(luma.LumaError) as caught:
            luma.start()
        self.assertIn("-10810", str(caught.exception))
        slept.assert_not_called()   # no minute of waiting for a server that can't come

    def test_down_server_is_not_up(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        with mock.patch.object(config, "LUMA_URL", f"http://127.0.0.1:{port}"):
            self.assertFalse(luma.is_up())

    # ---------- switch ----------

    def test_switch_hides_both_tools(self):
        before = settings.load()
        self.addCleanup(settings.update, {k: before[k] for k in settings.DEFAULTS})
        names = lambda: {t["name"] for t in tools.available()}   # noqa: E731
        self.assertLessEqual({"luma", "luma_tools"}, names())
        settings.update({"use_luma": False})
        self.assertFalse({"luma", "luma_tools"} & names())
        self.assertEqual(tools.integration_of("luma"), "use_luma")
        self.assertEqual(tools.integration_of("luma_tools"), "use_luma")

    def test_the_owners_goals_are_senas_not_lumas(self):
        """The tool's own line listed «цели» among Luma's records, while goals live in Sena (goal_add): Luma's only on his word."""
        described = next(t for t in luma.TOOLS if t["name"] == "luma")["description"]
        self.assertNotIn("заметки, цели", described)
        self.assertIn("Цели Темы ведёт Сена", described)

    def test_old_advisor_tool_is_gone(self):
        # the advisor chat for keys is closed, and its AI was a second, paid one: Sena calls the tools herself
        self.assertNotIn("luma_task", {t["name"] for t in tools.TOOLS})


if __name__ == "__main__":
    unittest.main()


class CodingJobCards(unittest.TestCase):
    """«взаимосвязь с luma» (02.11.2026): what Sena did in Tema's projects never showed up on their boards in Luma."""

    def setUp(self):
        from unittest import mock
        from sena.agents import ag_code
        from sena.core import settings
        self.ag_code, self.calls = ag_code, []

        def call(tool, input=None, per_item=False):
            self.calls.append((tool, input))
            if tool == "project_list":
                return {"items": [{"id": "p1", "title": "Sena"}, {"id": "p2", "title": "KS Trading"}]}
            if tool == "task_add":
                return {"created": [{"id": "c1", "index": 0}]}
            return {"updated": [{"id": "c1"}]}
        for p in (mock.patch.object(ag_code.luma, "is_up", lambda: True), mock.patch.object(ag_code.luma, "call", call),
                  mock.patch.object(settings, "get", lambda key, default=None: True if key == "use_luma" else default)):
            p.start()
            self.addCleanup(p.stop)

    def test_a_job_in_a_project_luma_has_is_a_card_in_work_then_done_or_back(self):
        card = self.ag_code._card("ks trading", "Починить выгрузку остатков\nподробности")
        self.assertEqual(card, "c1")
        self.assertEqual(self.calls[1], ("task_add", {"items": [{"title": "Починить выгрузку остатков", "project": "p2", "notes": "Починить выгрузку остатков\nподробности"}]}))
        self.assertEqual(self.calls[2], ("task_update", {"items": [{"task": "c1", "status": "doing"}]}))
        self.ag_code._card_moved(card, "done", "Починить выгрузку", "готово")
        self.assertEqual(self.calls[-1][1]["items"][0]["status"], "done")

    def test_a_project_luma_doesnt_have_gets_no_card(self):
        self.assertIsNone(self.ag_code._card("logo", "Нарисовать персонажей"))
        self.assertEqual([c[0] for c in self.calls], ["project_list"])
