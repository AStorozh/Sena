"""Event journal: every action is recorded tied to a task, background errors aren't lost, search and reporting work."""
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sena import config
from sena.core import brain, costs, events, llm, safety, status
from sena.memory import task_log
from sena.core.tools import tool



class RunIds(unittest.TestCase):
    def test_every_event_of_a_run_carries_its_id(self):
        """Microsoft Agent Framework: two browser runs of one task mixed together in the journal; now each has its own id."""
        from sena.core import brain, status
        seen = []

        def model(**kw):
            seen.append(events.log("step", "шаг")["run"])
            return SimpleNamespace(output=[], output_text="готово", id="r")
        with mock.patch.object(brain.llm, "respond", model):
            brain.run("m", "low", "", "задача", [], {}, name="ag_web")
            brain.run("m", "low", "", "задача", [], {}, name="ag_web")
        self.assertEqual(len(set(seen)), 2)
        self.assertIsNone(getattr(status.local, "run", None))   # back to no run outside them

class Events(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        for name, value in (("JOURNAL_DIR", Path(self.dir.name)), ("EVENTS_DB", Path(self.dir.name) / "events.db")):
            patch = mock.patch.object(config, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        events._reset()
        costs._reset()
        self.addCleanup(events._reset)

    def test_old_journal_gets_keys_column_and_private_entries_keep_only_sizes(self):
        import sqlite3
        db = sqlite3.connect(config.EVENTS_DB)
        db.executescript("""
            CREATE TABLE events (id INTEGER PRIMARY KEY, t REAL NOT NULL, kind TEXT NOT NULL, who TEXT, tid TEXT, turn INTEGER, text TEXT NOT NULL, data TEXT NOT NULL);
            CREATE VIRTUAL TABLE events_fts USING fts5(text, data, content='events', content_rowid='id', tokenize='trigram');
            CREATE TRIGGER events_ai AFTER INSERT ON events BEGIN INSERT INTO events_fts(rowid, text, data) VALUES (new.id, new.text, new.data); END;
            INSERT INTO events(t, kind, text, data) VALUES (strftime('%s', 'now'), 'tool', 'старая запись про комод', '{}');
        """)
        db.commit()
        db.close()
        events.log("tool", "новая запись", name="read_chat", args={"name": "Мама"})
        events.log("model", "кто-то рядом говорил о деньгах", input="длинная чужая речь", private=True)
        self.assertEqual(events.errors, [])
        self.assertEqual([e["text"] for e in events.search(query="комод")], ["старая запись про комод"])
        self.assertEqual([e["text"] for e in events.search(query="read_chat")], ["новая запись"])   # short fields are searched by words
        secret = events.search(kind="model")[0]
        self.assertNotIn("деньгах", secret["text"])
        self.assertIn("приватно", secret["input"])

    def test_task_steps_carry_task_id_and_worker(self):
        status.begin("проверка журнала")
        tid = status.current()["id"]
        status.show("ищу комод", who="ag_web")
        status.end("готово")
        rows = events.search("today", tid=tid)
        self.assertEqual([r["kind"] for r in rows], ["task_start", "step", "task_end"])
        self.assertEqual(rows[1]["who"], "ag_web")
        self.assertEqual(rows[2]["text"], "готово")
        self.assertIn("seconds", rows[2])
        self.assertEqual(events.errors, [])

    def test_model_call_and_tool_call_are_logged_by_brain(self):
        call = SimpleNamespace(type="function_call", name="hello", arguments='{"x": 1, "check": "прошлый шаг удался", "status": "здороваюсь"}', call_id="c1")
        first = SimpleNamespace(id="r1", output=[call], output_text="", usage=SimpleNamespace(input_tokens=10, output_tokens=5))
        second = SimpleNamespace(id="r2", output=[SimpleNamespace(type="message")], output_text="готово", usage=None)
        with mock.patch.object(llm, "_api") as api:
            api.return_value.responses.create.side_effect = [first, second]
            with mock.patch.object(costs, "openai_response"):
                answer = brain.run("gpt-test", "low", "инструкция", "привет", [tool("hello", "привет", {"x": {"type": "integer"}})], {"hello": lambda x: f"привет {x}"}, name="ag_web")
        self.assertEqual(answer, "готово")
        tools = events.search("today", kind="tool")
        self.assertEqual((tools[0]["name"], tools[0]["args"], tools[0]["text"], tools[0]["who"]), ("hello", {"x": 1}, "привет 1", "ag_web"))
        self.assertEqual(events.search("today", kind="check")[0]["text"], "прошлый шаг удался")
        models = events.search("today", kind="model")
        self.assertEqual(len(models), 2)
        self.assertEqual((models[0]["model"], models[0]["input"], models[0]["calls"][0]["name"], models[0]["tokens"]),
                         ("gpt-test", "привет", "hello", {"input": 10, "output": 5}))
        self.assertEqual(models[1]["input"], "ответы инструментов: 1")   # tool answers are already recorded by tool events

    def test_background_error_is_logged_with_trace(self):
        safety.background(lambda: 1 / 0, what="деление")
        for _ in range(50):
            if events.search("today", kind="error"):
                break
            time.sleep(0.05)
        row = events.search("today", kind="error")[0]
        self.assertEqual((row["place"], row["error"]), ("деление", "ZeroDivisionError"))
        self.assertIn("ZeroDivisionError", row["trace"])

    def test_verdict_and_comment_follow_the_task(self):
        entry = task_log.save({"id": "ab12cd34", "text": "проверка", "start": time.time(), "steps": [], "answer": "ок"})
        task_log.set_outcome(entry["id"], "done", "факт подтверждён")
        task_log.add_comment(entry["id"], "в следующий раз быстрее")
        rows = events.search("today", tid="ab12cd34")
        self.assertEqual([(r["kind"], r["text"]) for r in rows], [("verdict", "факт подтверждён"), ("comment", "в следующий раз быстрее")])
        self.assertEqual(rows[0]["verdict"], "done")

    def test_long_text_is_cut_and_report_counts_kinds(self):
        events.log("model", "x" * 10_000, model="gpt-test", purpose="проверка")
        events.log("message_in", "Сена, сколько времени?", source="voice")
        row = events.search("today", kind="model")[0]
        self.assertTrue(row["text"].endswith("[обрезано, всего 10000 знаков]"))
        text = events.report("today", "сколько")
        self.assertIn("Всего 1: message_in 1", text)
        self.assertIn("message_in: Сена, сколько времени?", text)
        self.assertIn("Ничего не найдено", events.report("yesterday"))
        self.assertIn("period —", events.report("позавчера"))

    def test_world_fetch_is_logged_with_status(self):
        from sena.world import net
        with mock.patch.object(net.requests, "get", return_value=SimpleNamespace(status_code=200)):
            net.get("https://example.test/weather", {"q": "Mui Ne"}, who="погода", ttl=60)
            net.get("https://example.test/weather", {"q": "Mui Ne"}, who="погода", ttl=60)   # the second time from cache
        rows = events.search("today", kind="fetch")
        self.assertEqual([(r["source"], r.get("status"), r.get("cached")) for r in rows], [("погода", 200, None), ("погода", None, True)])

    def test_a_secret_with_quotes_or_backslashes_is_masked_in_the_details_too(self):
        """The audit of 25.10: the details were masked after packing, where a value with " or \\ is written escaped — the text
        column was clean and the details held the password."""
        import json
        import sqlite3
        from sena.core import secrets
        value = 'Tr0ub"le\\pass9'
        with mock.patch.object(secrets, "values", return_value=[(value, "bank")]):
            events.log("model", f"пароль {value}", input=[{"role": "user", "content": f"мой пароль {value}"}], args={"text": value})
        db = sqlite3.connect(config.EVENTS_DB)
        self.addCleanup(db.close)
        text, keys, data = db.execute("SELECT text, keys, data FROM events WHERE kind = 'model'").fetchone()
        self.assertNotIn("Tr0ub", text + keys + data)
        details = json.loads(data)
        self.assertEqual((details["args"]["text"], details["input"][0]["content"]), ("[секрет:bank]", "мой пароль [секрет:bank]"))

    def test_every_kind_the_code_logs_has_its_word_in_the_table(self):
        """The audit of 25.10: seven kinds were logged but missing from KINDS, and the apps showed raw English codes."""
        import re
        found = set()
        for path in (config.ROOT / "sena").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            found |= set(re.findall(r"\bevents\.log\(\s*\"([a-z_]+)\"", text))
            if path.name in ("events.py", "status.py"):
                found |= set(re.findall(r"(?<![\w.])log\(\s*\"([a-z_]+)\"", text))
        self.assertGreater(len(found), 40)
        self.assertEqual(found - set(events.KINDS), set())
        self.assertTrue(all(isinstance(word, str) and word for word in events.KINDS.values()))

    def test_the_last_use_is_found_once_and_then_only_among_newer_events(self):
        """The Mac's audit of 25.10: "when was it last used" walked back through every event of its kind each time it was asked
        (a tool never used: the whole journal), every few seconds from the Integrations screen."""
        for n in range(50):
            events.log("tool", "шаг", name=f"t{n}")
        events.log("tool", "погода", name="weather")
        uses, last = events.tally("tool", "name", ["weather"])
        self.assertEqual(uses, 1)
        self.assertIsNotNone(last)
        self.assertEqual(events.tally("tool", "name", ["luma"]), (0, None))   # never used
        events.log("tool", "Luma", name="luma")
        uses, later = events.tally("tool", "name", ["luma"])
        self.assertEqual(uses, 1)
        self.assertIsNotNone(later)
        asked = []
        db = events._connect()
        with mock.patch.object(events, "_db", mock.Mock(wraps=db)) as spy:
            spy.execute.side_effect = lambda sql, params=(): asked.append(sql) or db.execute(sql, params)
            self.assertEqual(events.tally("tool", "name", {"weather"})[1], last)   # a set of values is the same question
        self.assertIn("e.t > ?", asked[0])   # only what came after the previous look was read

    def test_a_journal_that_failed_to_open_is_not_kept_half_made(self):
        import sqlite3
        with mock.patch.object(events, "_prepare", side_effect=sqlite3.OperationalError("disk I/O error")):
            self.assertIsNone(events.log("step", "раз"))
        self.assertIsNone(events._db)
        self.assertTrue(events.log("step", "два"))   # the next one opens it properly
        self.assertEqual([r["text"] for r in events.search("today")], ["два"])

    def test_journal_survives_restart_and_searches_by_words(self):
        events.log("app", "/settings", source="app", body='{"voice": "coral"}')
        events._reset()   # as if after the bot restarted
        events.log("app", "/jobs", source="app")
        self.assertEqual([r["text"] for r in events.search("today")], ["/settings", "/jobs"])
        self.assertEqual([r["text"] for r in events.search("today", "coral")], ["/settings"])   # words are searched in the details too
        self.assertEqual(events.search("today", limit=1)[0]["text"], "/jobs")


class CutAndWho(unittest.TestCase):
    """The audits of 26.10: a secret cut at the 4000-character limit was no longer masked (its head stayed in the journal), and
    the journal by actor scanned the whole table while every log waited on its lock."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        for name, value in (("JOURNAL_DIR", Path(self.dir.name)), ("EVENTS_DB", Path(self.dir.name) / "events.db")):
            patch = mock.patch.object(config, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        events._reset()
        self.addCleanup(events._reset)

    def test_a_secret_on_the_limit_is_masked_before_the_cut(self):
        from sena.core import secrets
        secret = "Zq7-unique-secret-value-2026"
        with mock.patch.object(config, "secret_values", return_value=[(secret, "SENA_SITE_KEY")]):
            text = "x" * (events.TEXT_LIMIT - 10) + secret + " и дальше"
            events.log("tool", text, name="run_command", args={"command": text})
            row = events.search("today")[-1]
        self.assertNotIn(secret[:10], row["text"])
        self.assertNotIn(secret[:10], row["args"]["command"])
        self.assertIn("обрезано", row["text"])
        secrets._reset()

    def test_the_journal_by_actor_uses_its_own_index(self):
        events.log("step", "шаг", who="ag_code/KSclay")
        db = events._connect()
        where, params = events._where("all", who="ag_code")
        plan = " ".join(str(r) for r in db.execute("EXPLAIN QUERY PLAN SELECT e.id FROM events e WHERE " + " AND ".join(where), params))
        self.assertIn("events_who", plan)
        self.assertEqual([r["who"] for r in events.search("all", who="ag_code")], ["ag_code/KSclay"])


if __name__ == "__main__":
    unittest.main()
