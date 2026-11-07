"""The phone app (android/) and Sena's server speak the same language: every endpoint and action the app calls exists
on the server, responses have the fields it reads in the shapes it reads them (people's facts as texts, a task's two numbers,
the costs of a task by its words, a refusal's body), the ports and the list of integrations match, and the signing key and
package name stayed the same (so the new version installs over the old one)."""
import importlib.util
import json
import re
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from sena import config
from sena.phone import desk

ANDROID = Path(__file__).resolve().parent.parent / "android"
APP = ANDROID / "app" / "src" / "main"
CLIENT = (APP / "java" / "com" / "tema" / "ia" / "data" / "HttpSenaApi.kt").read_text(encoding="utf-8")
SERVER = (Path(__file__).resolve().parent.parent / "sena" / "phone" / "server.py").read_text(encoding="utf-8")


class AndroidContract(unittest.TestCase):
    def test_desk_views_the_app_reads_exist(self):
        names = set(re.findall(r'(?:get|request)\("desk/api/([a-z_]+)"', CLIENT))
        self.assertTrue(names, "приложение ничего не читает?")
        self.assertLessEqual(names, set(desk.GET), "приложение зовёт чтение, которого нет на сервере")

    def test_desk_actions_the_app_calls_exist(self):
        names = set(re.findall(r'(?:\bact|factAction)\(\s*"([a-z_]+)"', CLIENT))   # a call may break its line after "("
        self.assertTrue({"fact_add", "fact_edit", "fact_delete", "stop_task", "secret_put"} <= names, "регулярка не видит действий приложения")
        names |= {f"{kind}_{verb}" for kind in ("rule", "job", "alert") for verb in ("toggle", "remove")}   # names are assembled in actionOf
        names |= set(re.findall(r'post\("desk/api/([a-z_]+)"', CLIENT))
        self.assertLessEqual(names, set(desk.ACTIONS), "приложение зовёт действие, которого нет на сервере")
        self.assertNotIn('post("desk/api/secrets"', CLIENT)   # the list of secrets is a plain read: no journal line every 30 s

    def test_plain_routes_exist(self):
        routes = re.findall(r'(?:get|post)\("((?!desk/)[a-z/]+)"', CLIENT)
        self.assertTrue(routes, "регулярка не видит прямых путей приложения")   # a loop over nothing proves nothing
        for route in routes:
            self.assertIn(f'"/{route}"', SERVER, f"на сервере нет маршрута /{route}")

    def test_overview_has_what_the_home_screen_reads(self):
        o = desk.overview()
        for path in (("core", "alive"), ("question",), ("pending_sends",), ("active",), ("budget", "limit"), ("budget", "spent"),
                     ("today", "errors"), ("today", "tasks"), ("accounts", "keys")):
            node = o
            for key in path:
                self.assertIn(key, node, "в обзоре нет поля " + ".".join(path))
                node = node[key]

    def test_tasks_rules_memory_config_have_what_the_screens_read(self):
        tasks = desk.tasks({})
        self.assertTrue({"now", "active", "finished"} <= set(tasks))
        self.assertTrue({"rules", "jobs", "alerts"} <= set(desk.rules_view()))
        self.assertIn("sections", desk.memory_view())
        config = desk.config_view()
        for key in ("settings", "options", "ranges", "process", "core"):
            self.assertIn(key, config)
        self.assertIn("budget_usd_day", config["ranges"])
        self.assertIn("git", config["process"])
        for key in ("verbosity", "effort", "budget_usd_day", "heartbeat", "tidy", "weekly", "voice_reply"):
            self.assertIn(key, config["settings"], "приложение показывает настройку, которой нет на сервере: " + key)
        self.assertTrue({"folders", "notes"} <= set(desk.notes_view({})))

    def test_integrations_row_fields(self):
        row = next(iter(desk.integrations_view().values()))
        for key in ("flag", "enabled", "uses_today", "last_used", "errors_today", "last_error", "usd_today", "restart"):
            self.assertIn(key, row)

    def test_ports_are_the_servers(self):
        address = (APP / "java" / "com" / "tema" / "ia" / "data" / "Address.kt").read_text(encoding="utf-8")
        self.assertEqual(int(re.search(r"DEFAULT_PORT = (\d+)", address).group(1)), config.APP_PORT, "приложение стучится не в тот порт")
        self.assertEqual(int(re.search(r"VOICE_PORT = (\d+)", address).group(1)), config.VOICE_PORT, "живой голос приложения идёт не в тот порт")

    # ---------- shapes the app reads (Parse.kt): a key that exists with another shape broke it before ----------

    def test_people_facts_are_texts_of_the_facts_still_true(self):
        from sena.memory import people
        p = people.new_person(name="Контракт Андроида")
        self.addCleanup(people.delete, p["id"])
        people.update(p["id"], facts=["живёт в Порту", "работает в банке"])
        people.update(p["id"], retire=["работает в банке"])
        row = next(r for r in desk.people_view() if r["id"] == p["id"])
        self.assertTrue(all(isinstance(f, str) for f in row["facts"]), "факты людей должны приходить текстом: приложение читает строки")
        self.assertEqual(row["facts"], ["живёт в Порту"])   # what no longer holds isn't shown as true

    def test_a_running_task_names_its_thread_and_its_entry(self):
        from sena.core import status
        from sena.phone import data
        status.begin("контракт: задача идёт")
        try:
            tid, thread = status.current()["id"], str(threading.get_ident())
            row = next(t for t in data.active_tasks() if t.get("tid") == tid)
            self.assertEqual(row["id"], thread)   # a list row: "id" is the thread (what stops it), "tid" its entry
            page = data.task(tid)                 # the app opens a task by its entry
            self.assertEqual((page["id"], page["thread"], page["done"]), (tid, thread, False))
        finally:
            task = status.end("ответ")
        from sena.memory import task_log
        with mock.patch.object(task_log, "items", [{**task, "comments": []}]):   # as agent._finish writes it: the same id
            self.assertTrue(data.task(tid)["done"], "закончившаяся задача находится по тому же номеру, что и шедшая")

    def test_a_chat_task_names_its_thread_for_stop(self):
        from sena.phone import server
        with mock.patch.dict(server.tasks, {"t-app": {"steps": ["Принято"], "answer": None, "done": False, "thread": "123"}}):
            view = server.task_view("t-app")
        self.assertEqual((view["steps"], view["done"], view["thread"]), (["Принято"], False, "123"))

    def test_stop_by_entry_is_taken_or_refused_with_400(self):
        from sena.phone.api import ApiError
        try:
            out = desk.act("stop_task", {"id": "ab12cd34"})   # the app tries the task's entry first
        except ApiError as e:
            self.assertEqual(e.code, 400, "отказ в номере задачи должен быть 400: по нему приложение переходит на номер потока")
        else:
            self.assertIn("result", out)

    def test_unknown_action_is_404(self):
        from sena.phone.api import ApiError
        with self.assertRaises(ApiError) as caught:   # the app tells an older server by this (one fact at a time, else the whole list)
            desk.act("no_such_action_ever", {})
        self.assertEqual(caught.exception.code, 404)

    def test_top_tasks_carry_their_words(self):
        import tempfile
        from sena.core import costs, status
        journal = tempfile.TemporaryDirectory()   # a journal of its own: other tests count today's spending
        self.addCleanup(journal.cleanup)
        patch = mock.patch.object(config, "JOURNAL_DIR", Path(journal.name))
        patch.start()
        self.addCleanup(patch.stop)
        costs._reset()
        self.addCleanup(costs._reset)
        status.begin("Проверь билеты в Лиссабон")
        try:
            tid = status.current()["id"]
            costs.record("anthropic", "messages", config.MODEL_FAST, {"input": 1000, "output": 100})
        finally:
            status.end("")
        rows = dict(costs.summary("today")["top_tasks"])
        self.assertIn(tid, rows)
        self.assertEqual(rows[tid]["task"], "Проверь билеты в Лиссабон")   # the app shows the words, keyed by the id
        self.assertTrue({"usd", "calls"} <= set(rows[tid]))

    def test_a_refused_secret_is_an_error_not_a_saved_one(self):
        out = desk.act("secret_put", {"name": "мой пароль", "value": "12345678", "allow": []})   # a space in the name: refused
        self.assertIn("error", out, "отказ хранителя должен прийти ошибкой, иначе приложение скажет «сохранён»")
        self.assertNotIn("result", out)

    def test_refusals_are_json_with_the_reason_the_app_shows(self):
        from sena.phone import server
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            def get(token):
                conn = HTTPConnection("127.0.0.1", httpd.server_port, timeout=10)
                try:
                    conn.request("GET", "/desk/api/overview?t=" + quote(token))
                    answer = conn.getresponse()
                    return answer.status, json.loads(answer.read())
                finally:
                    conn.close()
            code, body = get("wrong-password")
            self.assertEqual(code, 403)
            self.assertIsInstance(body.get("error"), str)   # the app reads "error"; anything but "подбор" is a wrong password
            self.assertNotIn("подбор", body["error"])
            with mock.patch.object(server, "is_locked", return_value=True):
                code, body = get(config.APP_TOKEN)
            self.assertEqual(code, 403)
            self.assertIn("подбор", body["error"], "закрытый адрес должен узнать, что он закрыт: иначе приложение пишет «пароль не подошёл»")
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_the_strings_tool_keeps_plurals_and_comments(self):
        spec = importlib.util.spec_from_file_location("add_strings", ANDROID / "tools" / "add_strings.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        xml = ('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n    <string name="a">А</string>\n'
               '    <plurals name="p">\n        <item quantity="one">%d штука</item>\n    </plurals>\n    <!-- голос -->\n</resources>\n')
        out = tool.upsert(tool.upsert(xml, "a", tool.esc("Б")), "b", tool.esc("Тема's"))
        self.assertIn('<string name="a">Б</string>', out)
        self.assertIn('<string name="b">Тема\\\'s</string>', out)
        self.assertIn('<plurals name="p">', out)
        self.assertIn("<!-- голос -->", out)
        self.assertEqual(out.count('name="a"'), 1)

    def test_integration_list_matches_the_server(self):
        text = (APP / "java" / "com" / "tema" / "ia" / "data" / "IntegrationCatalog.kt").read_text(encoding="utf-8")
        ids = re.findall(r'Spec\("([a-z_]+)"', text)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), set(desk.INTEGRATIONS), "список интеграций в приложении и на сервере разошёлся")

    def test_integration_keys_are_real_keys(self):
        text = (APP / "java" / "com" / "tema" / "ia" / "data" / "IntegrationCatalog.kt").read_text(encoding="utf-8")
        known = set(desk.KEYS)
        for ids in re.findall(r'listOf\(([^)]*)\)', text):
            for key in re.findall(r'"([A-Z_]+)"', ids):
                self.assertIn(key, known, f"ключ {key} из каталога приложения сервер не знает")

    def test_package_and_signing_key_stay_the_same(self):
        gradle = (ANDROID / "app" / "build.gradle.kts").read_text(encoding="utf-8")
        self.assertIn('applicationId = "com.tema.ia"', gradle)            # the same package as before: the new version installs over the old one
        self.assertIn('keyAlias = "ia"', gradle)
        if not (ANDROID / "debug.keystore").is_file():   # a public copy has no project key: both builds use the standard debug key
            self.assertIn('getByName("debug")', gradle)

    def test_server_serves_the_new_file_name(self):
        from sena import config
        self.assertEqual(config.APK_FILE.name, "sena.apk")
        self.assertIn('"/sena.apk"', SERVER)
        self.assertIn('"/ia.apk"', SERVER)   # the old link keeps working

    def test_no_secrets_in_the_android_sources(self):
        checked = 0
        for f in ANDROID.rglob("*"):
            if f.suffix in (".kt", ".xml", ".kts", ".md", ".properties") and "build" not in f.parts:
                text = f.read_text(encoding="utf-8", errors="ignore")
                self.assertNotRegex(text, r"ghp_[A-Za-z0-9]{20}|github_pat_|sk-[A-Za-z0-9]{20}|APP_TOKEN\s*=\s*\S{8}", str(f))
                checked += 1
        self.assertGreater(checked, 50, "исходники приложения не найдены: проверять было нечего")



def _function(name):
    """One function of the app's client: from its declaration to the next one (the shapes the app sends are checked there)."""
    m = re.search(rf"override (?:suspend )?fun {name}\(.*?(?=\n    override |\n    // ----------|\n    companion object)", CLIENT, re.S)
    assert m, f"в клиенте нет функции {name}"
    return m.group(0)


class AndroidSendsWhatTheServerReads(unittest.TestCase):
    """Round 2: what the app sends, as the server takes it (the password in a header, the question's id, every other name,
    only the new facts, slim lists answered with "same")."""

    def test_the_password_goes_in_the_header_and_the_server_takes_it(self):
        self.assertIn('header("Authorization", "Bearer $token")', CLIENT)
        voice = (APP / "java" / "com" / "tema" / "ia" / "data" / "LiveVoice.kt").read_text(encoding="utf-8")
        self.assertIn("headers.forEach", voice)   # the live voice's stream carries the same header
        lines = [line for line in CLIENT.splitlines() if 'addQueryParameter("t"' in line]
        self.assertTrue(lines, "регулярка не видит запасного пути пароля")
        for line in lines:
            self.assertIn("!inHeader", line, "пароль в адресе — только когда заголовок его не унесёт")
        from sena.phone import server
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            conn = HTTPConnection("127.0.0.1", httpd.server_port, timeout=10)
            conn.request("GET", "/desk/api/overview", headers={"Authorization": "Bearer " + config.APP_TOKEN})
            answer = conn.getresponse()
            self.assertEqual(answer.status, 200, "сервер не принял пароль из заголовка")
            self.assertIn("core", json.loads(answer.read()))
            conn.close()
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_the_answer_names_its_question_and_reaches_it(self):
        self.assertIn('put("id", id)', _function("answer"))
        from sena.core import ask_owner
        before = ask_owner._send
        ask_owner.set_sender(lambda q, img: None)
        self.addCleanup(ask_owner.set_sender, before)
        got = {}
        threads = [threading.Thread(target=lambda k=k: got.__setitem__(k, ask_owner.ask(f"вопрос {k}", timeout=10)), daemon=True) for k in ("first", "second")]
        for t in threads:
            t.start()
            for _ in range(100):
                if len(ask_owner.waiting()) == threads.index(t) + 1:
                    break
                threading.Event().wait(0.02)
        second = next(q for q in ask_owner.waiting() if q["question"] == "вопрос second")
        self.assertIn("result", desk.act("answer", {"text": "это второму", "id": second["id"]}))
        threads[1].join(5)
        self.assertEqual(got.get("second"), "Тема ответил: это второму")   # not the oldest question
        self.assertEqual([q["question"] for q in ask_owner.waiting()], ["вопрос first"])
        desk.act("answer", {"text": "и первому"})
        threads[0].join(5)

    def test_new_people_go_with_every_other_name_and_cards_with_only_their_new_facts(self):
        self.assertIn('putJsonArray("aliases")', _function("addPerson"))
        model = (APP / "java" / "com" / "tema" / "ia" / "ui" / "screens" / "memory" / "MemoryViewModel.kt").read_text(encoding="utf-8")
        self.assertIn("factChanges(person.facts, facts)", model)   # facts: only the new ones, retire: what he took out or changed

    def test_lists_are_asked_slim_and_answered_with_same_when_unchanged(self):
        tasks, history = _function("tasks"), _function("history")
        self.assertIn('"slim" to "1"', tasks)
        self.assertIn("sameAs(", tasks)
        self.assertIn("sameAs(", history)
        self.assertIn('"fields" to "sections"', _function("facts"))
        self.assertIn('"fields" to "index"', _function("registry"))
        self.assertIn('"limit" to "1"', _function("costsReport"))


if __name__ == "__main__":
    unittest.main()
