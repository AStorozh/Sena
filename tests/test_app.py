"""The app screen: what both apps read and do over the server — tasks, notes, schedule, alerts, people, skills, search, the
log and state (the desk paths /desk/api/*, plus the few direct ones the apps call); also checks that text reports (e.g.
weather) are still assembled from structured data, not broken on display. Data sources are replaced with stubs; the server
is real, on a free port."""
import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest import mock
from urllib.parse import quote

from sena import config, scheduler
from sena.memory import skills, vault
from sena.phone import server
from sena.world import alerts, finance, weather

WEATHER = {"place": "Муи-Не, Вьетнам", "now": {"temp": 27.2, "feels": 31.4, "code": 3, "text": "пасмурно", "wind": 2.75, "humidity": 79},
           "days": [{"date": "2026-10-15", "label": "Сегодня", "min": 25.7, "max": 31.3, "code": 55, "text": "морось", "rain": 12, "rain_chance": 100, "wind": 6, "uv": 9}]}


class App(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def get(self, path):
        conn = HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=10)
        conn.request("GET", path + ("&" if "?" in path else "?") + "t=" + config.APP_TOKEN)
        answer = conn.getresponse()
        return answer.status, json.loads(answer.read())

    def post(self, path, body):
        conn = HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=10)
        conn.request("POST", path + "?t=" + config.APP_TOKEN, body=json.dumps(body).encode())
        answer = conn.getresponse()
        return answer.status, json.loads(answer.read())

    def test_tasks_limit_and_one_task_with_cost(self):
        from sena.core import status
        from sena.memory import task_log
        status.begin("проверка страницы задачи")
        task_id = status.current()["id"]
        task_log.save(status.end("готово"))
        _, d = self.get("/desk/api/tasks?limit=1")
        self.assertEqual(len(d["finished"]), 1)
        _, t = self.get("/tasks/one?id=" + task_id)   # Android's task page reads it directly
        self.assertEqual((t["text"], t["done"], t["cost"]), ("проверка страницы задачи", True, 0))
        _, missing = self.get("/tasks/one?id=nope")
        self.assertIn("error", missing)

    def test_no_cost_history_is_read_for_no_tasks_and_a_task_page_reads_from_its_start(self):
        """Round 2 (performance P1): with nothing running, every overview — each app polls it every few seconds — walked every
        month of the cost journal (since=None); a finished task's page did the same for one task."""
        from sena.memory import task_log
        from sena.phone import data
        calls = []
        entry = task_log.save({"text": "страница задачи", "start": 1234.0, "steps": [], "answer": "ок"})
        with mock.patch.object(data.costs, "by_task", lambda ids, since=None: calls.append((list(ids), since)) or {}):
            data.active_tasks()
            data.finished_tasks(1)
            data.task(entry["id"])
        self.assertFalse([c for c in calls if not c[0]])   # never for no tasks
        self.assertIn(([entry["id"]], 1234.0), calls)    # the page reads from the task's own start

    def test_a_long_works_turn_is_marked_for_the_apps(self):
        """A long work's turn is a service task, yet «Стоп» stops it and pauses the work: the apps need the mark to count it as his."""
        from sena.core import status
        from sena.phone import data
        ident = 616161
        status.tasks[ident] = {"id": "t-turn", "text": "Работа «Сайт»: ход 2", "start": 1.0, "steps": [], "service": True, "work": "w7"}
        self.addCleanup(status.tasks.pop, ident, None)
        with mock.patch.object(data.costs, "by_task", lambda ids, since=None: {}):
            row = next(r for r in data.active_tasks() if r["tid"] == "t-turn")
        self.assertEqual((row["work"], row["service"], row["id"]), ("w7", True, str(ident)))

    def test_notes_list_search_read_write(self):
        vault.ensure()
        vault.write_note("Идеи/Проверка приложения", "# Проверка\n\n- первый пункт\n- второй", "create")
        _, listing = self.get("/desk/api/notes")
        row = next(r for r in listing["notes"] if r["path"] == "Идеи/Проверка приложения.md")
        self.assertEqual(row["preview"], "- первый пункт - второй")
        _, found = self.get("/desk/api/notes?q=" + quote("пункт"))
        self.assertEqual(found["notes"][0]["title"], "Проверка приложения")
        _, note = self.get("/notes/read?path=" + quote("Идеи/Проверка приложения.md"))
        self.assertIn("первый пункт", note["text"])
        _, r = self.post("/desk/api/note_write", {"path": "Идеи/Проверка приложения.md", "text": "дописано с телефона", "mode": "append"})
        self.assertIn("Дописано", r["result"])
        self.assertIn("дописано с телефона", vault.read("Идеи/Проверка приложения.md"))
        _, missing = self.get("/notes/read?path=" + quote("Нет.md"))
        self.assertIn("error", missing)

    def test_jobs_and_alerts_roundtrip(self):
        self.assertIn("Запланировано", scheduler.schedule("полить цветы", "01.01.2099 10:00", "daily"))
        _, listing = self.get("/desk/api/rules")
        job = next(j for j in listing["jobs"] if j["text"] == "полить цветы")
        _, r = self.post("/desk/api/job_remove", {"id": job["id"]})
        self.assertEqual(r["result"], "Удалено")
        self.assertNotIn(job["id"], [j["id"] for j in scheduler.jobs()])
        with mock.patch.object(finance, "price_of", return_value=80000.0):
            alerts.add("crypto", "BTC", "below", 70000)
        _, listing = self.get("/desk/api/rules")
        self.assertEqual(listing["alerts"][-1]["symbol"], "BTC")
        _, r = self.post("/desk/api/alert_remove", {"id": listing["alerts"][-1]["id"]})
        self.assertEqual(r["result"], "Удалён")

    def test_people_skills_costs_and_settings(self):
        skills.write_skill("app-check", "когда проверяем приложение", "открыть экран")
        _, rows = self.get("/desk/api/skills")
        row = next(r for r in rows if r["name"] == "app-check")
        self.assertEqual((row["when"], row["has_code"]), ("когда проверяем приложение", False))
        _, people = self.get("/desk/api/people")
        self.assertIsInstance(people, list)
        _, report = self.get("/desk/api/costs?period=today")
        self.assertIn("total", report["summary"])
        from sena.core import settings
        self.assertIn("отвечает подробно", settings.set_style(verbosity="medium"))
        self.assertEqual(settings.get("verbosity"), "medium")
        self.assertIn("Не из списка", settings.set_style(voice="робот"))
        self.assertEqual(settings.get("voice"), config.VOICE)
        _, answer = self.post("/desk/api/settings", {"voice": settings.HOME_VOICE, "nonsense": 1, "effort": "космос"})
        prefs = answer["settings"]
        self.assertEqual((prefs["voice"], prefs["effort"]), (settings.HOME_VOICE, "low"))
        self.assertNotIn("nonsense", prefs)
        settings.set_style(verbosity="low", voice=config.VOICE)

    def test_search_journal_and_state(self):
        from sena.core import events
        vault.ensure()
        vault.write_note("Знания/Поиск по всему", "Гибридный поиск находит заметку по словам и смыслу", "create")
        from sena.memory import task_log
        task_log.save({"text": "проверить гибридный поиск задач", "start": 1.0, "steps": [], "answer": "нашёл"})
        _, found = self.get("/search?q=" + quote("гибридный поиск"))
        kinds = {r["kind"] for r in found}
        self.assertIn("заметка", kinds)
        self.assertIn("задача", kinds)
        self.assertEqual(next(r for r in found if r["kind"] == "заметка")["title"], "Поиск по всему")
        events.log("tool", "привет 1", name="hello", args={"x": 1}, who="ag_web")
        _, rows = self.get("/desk/api/events?period=today&kind=tool&limit=5")
        self.assertEqual((rows[-1]["name"], rows[-1]["args"], rows[-1]["who"]), ("hello", {"x": 1}, "ag_web"))
        _, state = self.get("/desk/api/state")   # the Mac's settings screen reads only how much disk each part takes
        self.assertEqual(set(state), {"now", "disk"})
        self.assertGreater(state["disk"]["события"], 0)

    def test_a_note_with_a_hash_in_its_name_is_found_whole(self):
        """The registry's ref is "<path>#<chunk>": a note named «C# и .NET» lost the rest of its name in the search."""
        from sena.phone import data
        row = {"kind": vault.KIND, "ref": "Знания/C# и .NET.md#0", "text": "C# и .NET: заметка про язык", "time": 1.0, "score": 0.5}
        with mock.patch.object(data.recall, "find_rows", return_value={"rows": [row, {**row, "ref": "Знания/C# и .NET.md#1"}]}):
            found = data.search("язык")
        self.assertEqual([(r["path"], r["title"]) for r in found], [("Знания/C# и .NET.md", "C# и .NET")])   # its chunks are one row

    def test_weather_text_still_renders_from_structured_data(self):
        with mock.patch.object(weather, "lookup", return_value=WEATHER):
            text = weather.weather("Mui Ne")
        self.assertIn("Муи-Не, Вьетнам. Сейчас 27°C (ощущается как 31), пасмурно, ветер 3 м/с, влажность 79%.", text)
        self.assertIn("Сегодня (15.10): 26…31°C, морось, осадки 12 мм (вероятность 100%), ветер до 6 м/с, УФ 9", text)


if __name__ == "__main__":
    unittest.main()
