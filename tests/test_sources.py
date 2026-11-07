"""Reading sources for a research: pages read whole in parallel, only the pieces about the question go to the model, a fact
without its quote in the page is dropped, and a follow-up round adds to the same research."""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from sena.agents import sources as research
from sena.core import costs, status, store

PAGE = ("Вступление о погоде и рекламе. " * 60 + "Исследование показало, что дети, которые читают перед сном, засыпают на 20 минут быстрее. "
        + "Ещё немного рекламы. " * 60)


class Sources(unittest.TestCase):
    def setUp(self):
        status.begin("исследование")

    def tearDown(self):
        status.end("")

    def test_facts_need_their_quote_in_the_page(self):
        def read(url, limit=None, whole=False):
            if "bad" in url:
                raise research.articles.Unreadable("закрыто подпиской")
            return {"url": url, "title": "Сон и чтение", "site": "x", "date": "2026-10-01", "text": PAGE}

        def respond(**kw):
            self.assertIn("засыпают", kw["input"])
            self.assertLess(len(kw["input"]), len(PAGE))   # only the pieces about the question reach the model
            return SimpleNamespace(output_text=json.dumps({"facts": [
                {"fact": "чтение перед сном ускоряет засыпание", "quote": "дети, которые читают перед сном, засыпают на 20 минут быстрее", "date": ""},
                {"fact": "выдуманный факт", "quote": "этого предложения на странице нет совсем вообще", "date": ""}]}), output=[], id="r")
        with mock.patch.object(research.articles, "read", read), mock.patch.object(research.llm, "respond", respond):
            out = research.read_sources("как чтение перед сном влияет на засыпание детей", ["https://a.example/1", "https://bad.example/2"], research="t1")
            self.assertIn("фактов с цитатами 1", out)   # the invented one had no quote in the page
            self.assertIn("без дословной цитаты отброшено: 1", out)   # and the answer says so, not only the count of what stayed
            self.assertIn("расходятся — назови разброс", out)
            self.assertIn("не открылись: 1", out)
            self.assertIn("засыпают на 20 минут быстрее", out)
            again = research.read_sources("как чтение перед сном влияет на засыпание детей", ["https://a.example/1"], research="t1")
        self.assertIn("прочитано страниц 0", again)   # the follow-up round reads only what it hasn't
        self.assertEqual(len(store.lines(research.FACTS_DIR / "t1.jsonl")), 2)

    def test_every_page_is_read_for_the_task_that_asked(self):
        """The pages were read in pool threads with no task: thirty extractions billed to none, the per-task brake blind to them."""
        billed = []

        def respond(**kw):
            billed.append(costs._context()["tid"])
            return SimpleNamespace(output_text='{"facts": []}', output=[], id="r")
        read = lambda url, limit=None, whole=False: {"url": url, "title": "т", "site": "x", "date": "", "text": PAGE}   # noqa: E731
        with mock.patch.object(research.articles, "read", read), mock.patch.object(research.llm, "respond", respond):
            research.read_sources("как чтение перед сном влияет на засыпание детей", ["https://a.example/1", "https://b.example/2"], research="t-bill")
        self.assertEqual(billed, [status.current()["id"]] * 2)

    def test_a_failed_page_is_read_again_and_a_broken_answer_is_not_nothing(self):
        """Firecrawl's check on this Mac: a page that timed out took its "already read" place for good, and a broken model
        answer read as "nothing on the page"; a quote with "-" for "–" or "е" for "ё" was thrown out."""
        tries = {"n": 0}
        page = PAGE.replace("20 минут", "20 минут – и ещё ёлки")

        def read(url, limit=None, whole=False):
            tries["n"] += 1
            if tries["n"] == 1:
                raise TimeoutError("timed out")
            return {"url": url, "title": "Сон", "site": "x", "date": "", "text": page}
        answers = iter([SimpleNamespace(output_text="{не json", output=[], id="r"),
                        SimpleNamespace(output_text=json.dumps({"facts": [{"fact": "быстрее", "quote": "засыпают на 20 минут - и еще елки", "date": ""}]}),
                                        output=[], id="r")])
        with mock.patch.object(research.articles, "read", read), mock.patch.object(research.llm, "respond", lambda **kw: next(answers)):
            first = research.read_sources("чтение перед сном и засыпание детей", ["https://a.example/1"], research="t3")
            self.assertIn("не открылись: 1", first)
            second = research.read_sources("чтение перед сном и засыпание детей", ["https://a.example/1"], research="t3")
            self.assertIn("прочитано страниц 1", second)   # tried again, not "already read"
            self.assertIn("не открылись: 1", second)   # the broken answer is a failure too
            third = research.read_sources("чтение перед сном и засыпание детей", ["https://a.example/1"], research="t3")
        self.assertIn("фактов с цитатами 1", third)   # the quote with plain typography still counts

    def test_a_page_read_through_a_redirect_is_not_read_again(self):
        reads = []

        def read(url, limit=None, whole=False):
            reads.append(url)
            return {"url": "https://site.example/article", "title": "Сон", "site": "site.example", "date": "", "text": PAGE}
        with mock.patch.object(research.articles, "read", read), \
                mock.patch.object(research.llm, "respond", lambda **kw: SimpleNamespace(output_text='{"facts": []}', output=[], id="r")):
            research.read_sources("чтение перед сном", ["https://news.example/rss/1?oc=5"], research="t2")
            again = research.read_sources("чтение перед сном", ["https://news.example/rss/1", "https://www.site.example/article/?utm_source=tg"], research="t2")
        self.assertEqual(reads, ["https://news.example/rss/1?oc=5"])   # the link as asked and the page it led to are both known
        self.assertIn("прочитано страниц 0", again)

    def test_the_relevance_filter_keeps_only_pieces_about_the_question(self):
        kept = research.relevant(PAGE, "как чтение перед сном влияет на засыпание")
        self.assertTrue(kept)
        self.assertTrue(all("засыпают" in p or "читают" in p for p in kept))
        self.assertEqual(research.relevant("совсем про другое " * 100, "квантовая физика"), [])

    def test_other_word_forms_and_another_language_still_reach_the_reader(self):
        """crawl4ai's demo on Sena's own filter: a page answering in other forms of the words was dropped unread; a Russian
        question shares no word with an English page — there the meaning decides, and without fingerprints the page's start."""
        page = "Реклама и меню сайта.\n" * 120 + "Португалия облагает фрилансера налогом по ставке 25 % от дохода.\n" + "Подвал сайта.\n" * 120
        kept = research.relevant(page, "налоги фрилансеров в Португалии")
        self.assertTrue(kept and all("налогом" in p for p in kept))
        english = "Cookie banner and menu.\n" * 120 + "Freelancers in Portugal pay 25% income tax.\n" + "Footer links.\n" * 120

        def embed(texts, query=False):   # the home model's fingerprints: the tax line means what the question means
            return [[1.0, 0.0] if query or "tax" in t else [0.0, 1.0] for t in texts]
        with mock.patch.object(research.vectors, "embed", embed):
            kept = research.relevant(english, "налоги фрилансеров в Португалии")
        self.assertTrue(kept and all("income tax" in p for p in kept))
        with mock.patch.object(research.vectors, "embed", side_effect=RuntimeError("домашняя модель выключена")):
            self.assertEqual(research.relevant(english, "налоги фрилансеров в Португалии"), research._pieces(english)[:2])   # not dropped unread


if __name__ == "__main__":
    unittest.main()
