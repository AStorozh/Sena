"""Quality scoring and the weekly review: verdicts and repeated errors are tallied from the log, the review goes to Tema and into a note."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from sena import config
from sena.agents import ag_judge
from sena.core import brain, costs, events
from sena.memory import quality, task_log, vault
from sena.telegram import api


class Quality(unittest.TestCase):
    def setUp(self):
        # a journal of its own: the repeated errors other tests log would push this test's out of the top places
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for name, value in (("JOURNAL_DIR", Path(folder.name)), ("EVENTS_DB", Path(folder.name) / "events.db")):
            patch = mock.patch.object(config, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        events._reset()
        costs._reset()
        self.addCleanup(events._reset)
        self.addCleanup(costs._reset)

    def test_summary_counts_verdicts_agents_and_repeated_errors(self):
        now = time.time()
        a = task_log.save({"text": "качество: включить музыку", "start": now - 60, "steps": [{"who": "ag_web", "text": "Готово", "time": now - 30}], "answer": "играет"})
        b = task_log.save({"text": "качество: купить билет", "start": now - 50, "steps": [{"who": "ag_web", "text": "Готово", "time": now - 20}], "answer": "не купила"})
        task_log.set_outcome(a["id"], "done", "играет, проверено", cause="агент")   # nothing went wrong: no cause kept
        task_log.set_outcome(b["id"], "failed", "нужна оплата картой", cause="поручение")
        task_log.add_comment(b["id"], "спроси меня сразу")
        for _ in range(2):
            events.log("error", "Инструмент browser_code: TimeoutError", place="Инструмент browser_code", error="TimeoutError")
        events.log("recipe", "ошибка", name="youtube-play", failed=True)
        s = quality.summary(7)
        self.assertGreaterEqual(s["verdicts"].get("done", 0), 1)
        self.assertGreaterEqual(s["verdicts"].get("failed", 0), 1)
        self.assertGreaterEqual(s["by_agent"]["ag_web"].get("failed", 0), 1)
        self.assertTrue(any(p == "Инструмент browser_code" and n >= 2 for p, n in s["repeats"]))
        self.assertEqual(s["recipes"]["youtube-play"]["провал"], 1)
        text = quality.render(7)
        self.assertIn("не вышло", text)
        self.assertIn("купить билет", text)
        self.assertIn("спроси меня сразу", text)
        self.assertIn("youtube-play", text)
        self.assertNotIn("cause", task_log.get(a["id"])["outcome"])
        self.assertGreaterEqual(s["causes"].get("поручение", 0), 1)   # whose doing a failure was, counted for the weekly review
        self.assertIn("Почему не вышло (по разбору): поручение", text)

    def test_summary_counts_tema_ratings(self):
        now = time.time()
        a = task_log.save({"text": "качество: оценка а", "start": now - 60, "steps": [], "answer": "готово"})
        b = task_log.save({"text": "качество: оценка б", "start": now - 50, "steps": [], "answer": "готово"})
        task_log.set_rating(a["id"], 5)
        task_log.set_rating(b["id"], 3)
        s = quality.summary(7)
        self.assertEqual(s["ratings"].get(5, 0), 1)
        self.assertEqual(s["ratings"].get(3, 0), 1)
        self.assertEqual(s["rating_avg"], 4.0)
        self.assertIn("средняя оценка 4.0/5", quality.render(7))

    def test_costs_count_the_same_days_as_the_tasks(self):
        """«За 30 дней» counted the costs of the calendar month: on the 3rd, three days of spending; a day was midnight's costs
        beside 24 hours of tasks."""
        from datetime import date
        for days, span in ((30, 29), (7, 6), (1, 0)):
            with mock.patch.object(quality.costs, "summary", return_value={"total": 1.5}) as spent:
                self.assertEqual(quality.summary(days)["usd"], 1.5)
            first, last = (date.fromisoformat(d) for d in spent.call_args.args[0].split(".."))
            self.assertEqual(((last - first).days, last), (span, date.today()))

    def test_weekly_review_goes_to_owner_and_vault(self):
        task_log.save({"text": "качество: разбор недели", "start": time.time() - 10, "steps": [], "answer": "ок"})
        sent = []
        with mock.patch.object(brain, "run", return_value="Неделя прошла ровно, повторяющихся ошибок нет."), mock.patch.object(api, "send_text", sent.append), \
                mock.patch.object(ag_judge.programs, "start_exam", return_value="экзамен запущен") as exam:   # never a real exam process from a test
            answer = ag_judge.weekly()
        exam.assert_called_once()
        self.assertIn("ровно", answer)
        self.assertTrue(sent and sent[0].startswith("Разбор недели"))
        notes = [n for n in vault.notes("Дневник") if n["title"].startswith("Неделя ")]
        self.assertTrue(notes)
        self.assertIn("ровно", vault.read(notes[0]["path"]))
        self.assertTrue(any(e["text"] == "Недельный разбор качества" and e.get("service") for e in task_log.items))


if __name__ == "__main__":
    unittest.main()
