"""Search quality, measured: a small life-like registry and the questions the owner really asks. Each question names the record
that answers it; the answer has to be among the first five that recall.find_rows returns (recall@5). Runs on the test embeddings
(words, not meaning), so it measures the pipeline — grammar of the question, words, links, time, kinds, history — not the model.
Misses are listed in the failure message; a new kind of question goes in here before it goes in the prompt."""
import unittest

from sena import config
from sena.home.bench import TOP, golden_fill, golden_questions, hit
from sena.memory import index, recall
from tests.test_index import _wipe

REQUIRED = 1.0   # share of questions answered in the top five: all of them. The test embeddings hash words with a stable hash
                 # (tests/__init__.py, crc32), so a run is the same every time: one miss is a regression, not noise


class SearchQuality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _wipe()
        cls.ids = golden_fill()

    def test_the_answer_is_among_the_first_five(self):
        misses = []
        questions = golden_questions(self.ids)
        for question, args, answers in questions:
            data = recall.find_rows(question, with_events=False, **args)
            rows = data.get("rows", [])
            if data.get("error") or not hit(rows, answers):
                misses.append(f"{question or '(без вопроса)'} {args or ''} -> " + (data.get("error") or ", ".join(f"{r['kind']} {r['ref']}" for r in rows[:TOP]) or "пусто"))
        share = 1 - len(misses) / len(questions)
        self.assertGreaterEqual(share, REQUIRED, f"recall@{TOP} = {share:.2f}; мимо:\n  " + "\n  ".join(misses))

    def test_the_time_filter_does_not_invent_answers(self):
        self.assertEqual(recall.find_rows("комод", period=self.ids["empty_month"], with_events=False)["rows"], [])   # nothing happened that month

    def test_the_set_covers_every_kind_and_every_filter(self):
        questions = golden_questions(self.ids)
        asked = {a[0] for _, _, answers in questions for a in answers}
        self.assertEqual(asked, set(index.KINDS) - {"организация"})   # no organization in the fixture yet: add one with its question when the first appears
        filters = {k for _, args, _ in questions for k in args}
        self.assertEqual(filters, {"person", "project", "kinds", "period", "history"})
        self.assertEqual(config.EMBED_DIMS, 512)   # the fixture's vectors come from the test embedder of this width (tests/__init__.py)


if __name__ == "__main__":
    unittest.main()
