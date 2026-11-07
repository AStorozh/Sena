"""Code recipes: storage in skills, the ag_web fast path, evidence for the judge."""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from sena.agents import ag_judge
from sena.agents.ag_web import agent as ag_web
from sena.agents.ag_web import browser, recipes
from sena.core import llm, status
from sena.memory import skills, task_log

CODE = '''def run(page, query, count=1):
    """Включить ролик по запросу."""
    assert query, "пустой запрос"
    return f"играет {query} x{count}"
'''


def router_says(**answer):
    return lambda **kw: SimpleNamespace(output_text=json.dumps(answer, ensure_ascii=False))


class SkillCode(unittest.TestCase):
    def tearDown(self):
        skills.delete_skill("test-play")

    def test_code_is_validated_and_listed(self):
        self.assertIn("Не сохранено", skills.write_skill("test-play", "когда", "рецепт", code="def run(:"))
        self.assertIn("run(page", skills.write_skill("test-play", "когда", "рецепт", code="def go(page): pass"))
        self.assertIn("с кодом", skills.write_skill("test-play", "Включить ролик", "шаги", code=CODE))
        (recipe,) = [r for r in skills.code_recipes() if r["name"] == "test-play"]
        self.assertEqual(recipe["required"], ["query"])
        self.assertEqual(recipe["params"], ["query", "count"])
        self.assertEqual(recipe["signature"], "run(page, query, count=1)")
        self.assertIn("[есть код]", skills.listing())
        self.assertIn("Код (запускается без модели)", skills.read_skill("test-play"))

    def test_a_recipe_failing_twice_leaves_the_fast_path_until_fixed(self):
        """A failed run is a failure, not a sign of life: two in a row and the router no longer sees it; a fix brings it back."""
        skills.write_skill("test-play", "Включить ролик", "шаги", code=CODE)
        listed = lambda: [r["name"] for r in skills.code_recipes() if r["name"] == "test-play"]   # noqa: E731
        skills.used("test-play", "failed")
        self.assertEqual(listed(), ["test-play"])
        skills.used("test-play", "failed")
        self.assertEqual(listed(), [])
        skills.patch_skill("test-play", "шаги", "шаги получше")
        self.assertEqual(listed(), ["test-play"])

    def test_fragile_or_gutted_code_is_not_kept(self):
        """Skyvern's validators: element numbers and generated ids break a replay the next day; a rewrite that halves the code
        loses its tested branches."""
        self.assertIn("номера элементов", skills.write_skill("test-play", "когда", "шаги", code="def run(page, query):\n    click(page, 5)\n"))
        self.assertIn("заново при каждой загрузке", skills.write_skill("test-play", "когда", "шаги", code="def run(page):\n    page.click('#react-select-3-input')\n"))
        big = CODE + "".join(f"    # проверенная ветка {i}: что делать, если страница другая\n" for i in range(12))
        self.assertIn("с кодом", skills.write_skill("test-play", "Включить ролик", "шаги", code=big))
        self.assertIn("вдвое короче", skills.write_skill("test-play", "Включить ролик", "шаги", code="def run(page, query):\n    return query\n"))

    def test_rewrite_keeps_code_unless_cleared(self):
        skills.write_skill("test-play", "когда", "v1", code=CODE)
        skills.write_skill("test-play", "когда", "v2")   # code not given — it stays
        self.assertTrue([r for r in skills.code_recipes() if r["name"] == "test-play"])
        skills.write_skill("test-play", "когда", "v3", code="")   # empty string — remove it
        self.assertFalse([r for r in skills.code_recipes() if r["name"] == "test-play"])


class FastPath(unittest.TestCase):
    def setUp(self):
        skills.write_skill("test-play", "Включить ролик на YouTube", "шаги", code=CODE)
        self.original = llm.respond
        self.patches = [
            mock.patch.object(browser, "in_browser", lambda func, *a, **k: func(*a)),
            mock.patch.object(browser, "get_page", lambda: "PAGE"),
            mock.patch("sena.agents.ag_web.toolkit.helpers", lambda page: {"page": page}),
        ]
        for p in self.patches:
            p.start()
        status.begin("тест")

    def tearDown(self):
        status.end("")
        for p in self.patches:
            p.stop()
        llm.respond = self.original
        skills.delete_skill("test-play")

    def test_recipe_runs_without_agent(self):
        llm.respond = router_says(recipe="test-play", args={"query": "Death Stranding"})
        answer, note = recipes.try_fast("включи Death Stranding")
        self.assertIsNone(note)
        self.assertIn("играет Death Stranding", answer)

    def test_router_says_null_or_invents_parameter(self):
        llm.respond = router_says(recipe=None, args={})
        self.assertEqual(recipes.try_fast("открой почту"), (None, None))
        llm.respond = router_says(recipe="test-play", args={"query": "x", "volume": 9})   # no such parameter exists
        self.assertEqual(recipes.try_fast("включи x"), (None, None))
        llm.respond = router_says(recipe="test-play", args={})   # required parameter missing
        self.assertEqual(recipes.try_fast("включи что-нибудь"), (None, None))
        # the router's own way to say "the task has no such parameter": null or empty is missing, not a value to play
        for missing in (None, ""):
            llm.respond = router_says(recipe="test-play", args={"query": missing})
            self.assertEqual(recipes.try_fast("включи что-нибудь на ютубе"), (None, None))

    def test_a_recipe_that_returns_nothing_is_not_a_success(self):
        """Firecrawl's rule: an empty result reads like "the page had no data" and hides that the recipe broke."""
        skills.write_skill("test-play", "Включить ролик на YouTube", "шаги",
                           code='def run(page, query):\n    """Включить ролик."""\n    assert query\n    return None\n')
        llm.respond = router_says(recipe="test-play", args={"query": "x"})
        answer, note = recipes.try_fast("включи x")
        self.assertIsNone(answer)
        self.assertIn("ничего не вернул", note)

    def test_broken_recipe_gives_note_for_agent(self):
        skills.write_skill("test-play", "Включить ролик на YouTube", "шаги",
                           code='def run(page, query):\n    """Включить ролик."""\n    assert query != "сломано", "плеер не найден"\n    return f"играет {query}"\n')
        llm.respond = router_says(recipe="test-play", args={"query": "сломано"})
        answer, note = recipes.try_fast("включи сломано")
        self.assertIsNone(answer)
        self.assertIn("не сработал", note)
        self.assertIn("плеер не найден", note)

    def test_agent_gets_note_and_task_is_not_lost(self):
        seen = {}

        def fake_run(model, effort, instructions, task, *a, **kw):
            seen["task"] = task
            return "сделал сам"

        with mock.patch.object(recipes, "try_fast", lambda task: (None, "Быстрый путь не сработал: X")), \
             mock.patch.object(ag_web.brain, "run", fake_run):
            self.assertEqual(ag_web.run("включи музыку"), "сделал сам")
        self.assertTrue(seen["task"].startswith("включи музыку"))
        self.assertIn("Быстрый путь не сработал: X", seen["task"])

    def test_fast_answer_skips_agent(self):
        with mock.patch.object(recipes, "try_fast", lambda task: ("Готово по рецепту", None)), \
             mock.patch.object(ag_web.brain, "run", side_effect=AssertionError("агент не должен запускаться")):
            self.assertEqual(ag_web.run("включи музыку"), "Готово по рецепту")


class Evidence(unittest.TestCase):
    def test_judge_receives_browser_code_and_final_frame(self):
        captured = {}
        entry = task_log.save({"text": "найди цену", "start": 1.0, "steps": [], "answer": "1200 руб"})
        evidence = {"code": [{"code": "print(page.title())", "failed": False}, {"code": "page.click('x')", "failed": True}], "image": b"\xff\xd8jpeg"}
        with mock.patch.object(ag_judge.brain, "run", lambda *a, **kw: captured.update(input=a[3])):
            ag_judge.review(entry, ["ag_web: browser_code ..."], evidence)
        content = captured["input"][0]["content"]
        self.assertEqual([c["type"] for c in content], ["input_text", "input_image"])
        self.assertIn("## вызов 1", content[0]["text"])
        self.assertIn("## вызов 2 — ОШИБКА", content[0]["text"])
        self.assertIn("print(page.title())", content[0]["text"])

    def test_evidence_is_kept_on_task_but_not_shown_in_app(self):
        status.begin("задача")
        try:
            status.record_code("print(1)", failed=False)
            status.attach_image(b"jpeg")
            (task,) = [t for t in status.tasks.values() if t["text"] == "задача"]
            self.assertEqual(task["code"][0]["code"], "print(1)")
            self.assertEqual(task["image"], b"jpeg")
            from sena.phone import data
            shown = data.active_tasks()   # what the apps' task lists show of the running ones
            json.dumps(shown)   # image bytes don't end up in the JSON
            self.assertTrue(all("image" not in t and "code" not in t for t in shown))
        finally:
            status.end("")


if __name__ == "__main__":
    unittest.main()
