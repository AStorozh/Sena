"""Watching a page for the owner's goal: the same text costs nothing, a change is judged for the goal and told with its own
words quoted, a page that can't be read is never "changed", and a told change waits for the owner's waking hours."""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from sena import config
from sena.core import store
from sena.world import articles, net, pages


class Pages(unittest.TestCase):
    def setUp(self):
        store.clear(config.PAGE_WATCH_FILE)
        self.text = {"t": "Вакансии отдела\nФронтенд-разработчик, React\nДизайнер интерфейсов"}
        self.reads = []

        def read(url, limit=None, whole=False, fresh=False):
            self.reads.append(fresh)
            if isinstance(self.text["t"], Exception):
                raise self.text["t"]
            return {"url": url, "title": "Вакансии ACME", "site": "acme.example", "date": "", "text": self.text["t"]}
        self.enterContext(mock.patch.object(articles, "read", read))
        self.asked = []
        self.verdict = {"meaningful": True, "summary": "появилась вакансия Python-разработчика", "before": "", "after": "Python-разработчик, Django"}
        self.enterContext(mock.patch.object(pages.llm, "respond", lambda **kw: self.asked.append(kw["input"]) or SimpleNamespace(
            output_text=json.dumps(self.verdict, ensure_ascii=False), output=[], id="r")))
        self.enterContext(mock.patch.object(pages.settings, "awake", lambda now=None: True))
        self.told = []

    def due(self):
        items = store.load_json(config.PAGE_WATCH_FILE, [])
        for w in items:
            w["next"] = 0
        store.save_json(config.PAGE_WATCH_FILE, items)

    def test_a_change_that_matters_is_told_with_its_words_and_the_same_page_costs_nothing(self):
        self.assertIn("Слежу", pages.page_watch("https://acme.example/jobs", "появится вакансия Python"))
        self.assertTrue(self.reads[0])   # read fresh, not from the cache
        self.due()
        pages.check(self.told.append)
        self.assertEqual((self.told, self.asked), ([], []))   # nothing changed: no model call
        self.text["t"] += "\nPython-разработчик, Django"
        self.due()
        pages.check(self.told.append)
        self.assertEqual(len(self.told), 1)
        self.assertIn("появилась вакансия Python", self.told[0])
        self.assertIn("стало: «Python-разработчик, Django»", self.told[0])
        self.assertIn("+Python-разработчик, Django", self.asked[0])   # the model reads the change, fenced as someone else's text
        self.assertIn("ВНЕШНИЕ ДАННЫЕ", self.asked[0])

    def test_an_invented_quote_or_a_trifle_is_not_told(self):
        pages.page_watch("https://acme.example/jobs", "появится вакансия Python")
        self.text["t"] = self.text["t"].replace("React", "React, удалённо")
        self.verdict = {"meaningful": True, "summary": "появилась вакансия Python", "before": "", "after": "Python-разработчик"}   # not in the change
        self.due()
        pages.check(self.told.append)
        self.verdict = {"meaningful": False, "summary": "мелкая правка", "before": "", "after": ""}
        self.text["t"] += "\nОбновлено вчера"
        self.due()
        pages.check(self.told.append)
        self.assertEqual(self.told, [])

    def test_a_page_that_cant_be_read_is_never_changed_and_is_told_once(self):
        pages.page_watch("https://acme.example/jobs", "появится вакансия Python")
        self.text["t"] = net.WorldError("acme.example ответил 503")
        for _ in range(pages.TOLD_AFTER + 1):
            self.due()
            pages.check(self.told.append)
        self.assertEqual(len(self.told), 1)
        self.assertIn("не могу прочитать", self.told[0])
        self.assertEqual(self.asked, [])

    def test_a_change_found_at_night_waits_for_the_morning(self):
        pages.page_watch("https://acme.example/jobs", "появится вакансия Python")
        self.text["t"] += "\nPython-разработчик, Django"
        self.due()
        with mock.patch.object(pages.settings, "awake", lambda now=None: False):
            pages.check(self.told.append)
        self.assertEqual(self.told, [])
        pages.check(self.told.append)   # morning: told now, without reading the page again
        self.assertEqual(len(self.told), 1)

    def test_watches_are_listed_and_removed_and_a_bad_request_is_refused(self):
        self.assertIsInstance(pages.page_watch("acme.example/jobs", "что-то"), pages.Failure)
        self.assertIsInstance(pages.page_watch("https://acme.example/jobs", ""), pages.Failure)
        wid = pages.page_watch("https://acme.example/jobs", "цена ниже 50 000").split("[")[1].split("]")[0]
        self.assertIn("цена ниже 50 000", pages.page_watches())
        with mock.patch.object(pages.events, "log") as log:
            self.assertIn("Больше не слежу", pages.page_unwatch(f" {wid} "))   # the id as the model copies it, with a space
        self.assertIn(wid, log.call_args.args[1])
        self.assertEqual(pages.page_watches(), "За страницами не слежу")

    def test_a_watch_added_or_removed_during_a_pass_stays_so(self):
        """A slow pass saved the list it had loaded: a watch the owner added meanwhile was lost, one he removed came back."""
        import threading
        pages.page_watch("https://acme.example/jobs", "появится вакансия Python")
        gone = pages.page_watch("https://acme.example/old", "старое").split("[")[1].split("]")[0]
        self.due()
        gate, inside = threading.Event(), threading.Event()
        quick = articles.read

        def slow(url, **kw):
            if url == "https://acme.example/jobs" and not gate.is_set():
                inside.set()
                gate.wait(5)
            return quick(url, **kw)
        with mock.patch.object(articles, "read", slow):
            t = threading.Thread(target=pages.check, args=(self.told.append,))
            t.start()
            self.assertTrue(inside.wait(5))
            pages.page_watch("https://acme.example/new", "цена ниже 50 000")   # the owner adds one while the pass reads
            pages.page_unwatch(gone)                                             # and removes another
            gate.set()
            t.join(5)
        self.assertEqual(sorted(w["url"] for w in store.load_json(config.PAGE_WATCH_FILE, [])), ["https://acme.example/jobs", "https://acme.example/new"])

    def test_a_page_that_cant_be_read_is_told_in_waking_hours_too(self):
        pages.page_watch("https://acme.example/jobs", "появится вакансия Python")
        self.text["t"] = net.WorldError("acme.example ответил 503")
        with mock.patch.object(pages.settings, "awake", lambda now=None: False):
            for _ in range(pages.TOLD_AFTER):
                self.due()
                pages.check(self.told.append)
        self.assertEqual(self.told, [])   # night: it waits
        pages.check(self.told.append)
        self.assertEqual(len(self.told), 1)
        self.assertIn("не могу прочитать", self.told[0])

    def test_the_judges_prompt_names_every_field_of_its_answer(self):
        """prompts P24: the schema isn't strict and the prompt never said «meaningful» — an answer with another name for it was
        never told."""
        for name in pages.SCHEMA["properties"]:
            self.assertIn(name, pages.JUDGE)


if __name__ == "__main__":
    unittest.main()
