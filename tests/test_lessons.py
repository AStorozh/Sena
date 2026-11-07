"""Each agent's own memory: lessons by agent and scope, drafts the judge keeps or lets go, confirmations and refutations,
no secrets, the right lessons at the start of a task, the nightly cap — and nothing deleted, only retired."""
import time
import unittest
from unittest import mock

from sena import config
from sena.agents import ag_judge, common
from sena.core import status, store
from sena.memory import index, lessons


class Lessons(unittest.TestCase):
    def setUp(self):
        for agent in lessons.AGENTS:
            store.clear(lessons._path(agent))
        index.drop(lessons.KIND)

    def test_the_same_lesson_in_other_words_is_confirmed_not_added(self):
        lessons.note("ag_web", "на hh.ru кнопка «Откликнуться» внизу карточки вакансии", "hh.ru")
        reply = lessons.note("ag_web", "на hh.ru кнопка «Откликнуться» находится внизу карточки вакансии", "hh.ru")
        self.assertIn("подтверждён ещё раз", reply)
        self.assertEqual(len([i for i in lessons.listing("ag_web") if i["scope"] == "hh.ru"]), 1)
        lessons.note("ag_web", "на hh.ru вход только по коду из СМС", "hh.ru")   # a different lesson stays apart
        self.assertEqual(len([i for i in lessons.listing("ag_web") if i["scope"] == "hh.ru"]), 2)

    def test_judge_lessons_are_active_and_repeats_confirm_them(self):
        self.assertEqual(lessons.note("ag_web", "Вход на hh.ru во вложенном окне: кнопка «Войти» в f1", "hh.ru"), "Урок записан")
        again = lessons.note("ag_web", "вход на hh.ru во вложенном окне — кнопка «войти» в f1", "HH.ru")
        self.assertIn("подтверждён", again)
        [item] = lessons.listing("ag_web")
        self.assertEqual((item["state"], item["confirmed"]), ("active", 2))
        self.assertIn(item["id"], index.refs(lessons.KIND))

    def test_an_agents_own_note_is_a_draft_until_the_judge_keeps_it(self):
        status.begin("найди вакансии на hh.ru")
        try:
            reply = lessons.note_function("ag_web")("Фильтр «удалённо» на hh.ru сбрасывается при смене города", "hh.ru")
            tid = status.current()["id"]
        finally:
            status.end("готово")
        self.assertIn("черновик", reply)
        [draft] = lessons.drafts("ag_web")
        self.assertEqual(draft["source"], tid)
        self.assertEqual(lessons.for_task("ag_web", "открой hh.ru"), "")   # a draft never reaches the agent
        self.assertNotIn(draft["id"], index.refs(lessons.KIND))
        lessons.keep("ag_web", draft["id"])
        self.assertIn("сбрасывается", lessons.for_task("ag_web", "открой hh.ru и поищи вакансии"))

    def test_refuted_more_than_confirmed_retires_but_stays_in_the_file(self):
        lessons.note("ag_mac", "Homebrew на этом Mac в /usr/local/bin")
        [item] = lessons.listing("ag_mac")
        lessons.refute("ag_mac", item["id"], "brew в /opt/homebrew")
        self.assertEqual(lessons.listing("ag_mac"), [])
        [kept] = lessons._load("ag_mac")
        self.assertEqual((kept["state"], kept["why"]), ("retired", "brew в /opt/homebrew"))
        self.assertNotIn(item["id"], index.refs(lessons.KIND))

    def test_no_secrets_no_strangers(self):
        from sena.core.tools import Failure
        self.assertIn("пароль", lessons.note("ag_web", "Пароль от hh.ru: Qwerty123456789"))
        self.assertIn("Нет такого агента", lessons.note("ag_x", "что-то полезное и длинное"))
        self.assertEqual(lessons._load("ag_web"), [])
        for refused in (lessons.note("ag_web", "Пароль от hh.ru: Qwerty123456789"), lessons.note("ag_web", "коротко"),
                        lessons.edit("ag_web", "nope", "достаточно длинный текст урока"), lessons.keep("ag_web", "nope")):
            self.assertIsInstance(refused, Failure)   # the apps show a refusal as an error, not as done

    def test_the_task_gets_scoped_lessons_first_and_the_experience_block(self):
        lessons.note("ag_web", "На avito.ru объявления грузятся при прокрутке: листай перед чтением", "avito.ru")
        lessons.note("ag_web", "Капчу Cloudflare решает только human_click по галочке")
        text = common.with_experience("найди на avito.ru велосипед", "ag_web")
        self.assertIn("Твои уроки", text)
        self.assertIn("(avito.ru) На avito.ru", text)
        self.assertNotIn("Твои уроки", common.with_experience("найди на avito.ru велосипед"))   # no agent named: no lessons

    def test_night_retires_old_drafts_and_the_weakest_past_the_cap(self):
        lessons.note("ag_mac", "Старый черновик, который никто не подтвердил", by="self")
        items = lessons._load("ag_mac")
        items[0]["t"] = time.time() - (lessons.DRAFT_DAYS + 1) * 86400
        lessons._save("ag_mac", items)
        with mock.patch.object(lessons, "MAX_ACTIVE", 1):
            lessons.note("ag_mac", "Первый урок про терминал и zsh")
            lessons.note("ag_mac", "Второй урок про громкость через osascript")
            lessons.note("ag_mac", "Второй урок про громкость через osascript")   # confirmed twice: the stronger one stays
            self.assertEqual(lessons.tidy(), 2)
        [left] = lessons.listing("ag_mac")
        self.assertIn("громкость", left["text"])
        self.assertEqual(len(lessons._load("ag_mac")), 3)   # nothing deleted

    def test_the_judge_sees_drafts_of_this_task_and_the_lessons_given(self):
        lessons.note("ag_web", "Урок, который агент получил на hh.ru", "hh.ru")
        items = lessons._load("ag_web")
        lessons.note("ag_web", "Черновик этой задачи про hh.ru и вложенные окна", "hh.ru", source="t1", by="self")
        entry = {"id": "t1", "text": "открой hh.ru", "steps": [{"who": "ag_web", "text": "смотрю"}]}
        seen = ag_judge._lessons_seen(entry, ["ag_web: browser_code {...} -> ok"])
        self.assertIn("Черновики уроков", seen)
        self.assertIn("Черновик этой задачи", seen)
        self.assertIn(items[0]["text"], seen)

    def test_a_remark_made_during_the_redo_is_reviewed_with_the_redo(self):
        """crewAI's training triplet: the first try, the owner's words and the redo go to one review; the remark alone isn't
        reviewed before the redo exists, and the first try is graded by the remark itself."""
        from sena.memory import task_log
        first = task_log.save({"id": "a1", "text": "включи последнее видео Мармока", "start": 1.0, "steps": [], "answer": "Включила «Как я строил дом»"})
        status.begin("не то, я просил последнее")
        try:
            with mock.patch.object(ag_judge, "after_task") as reviewed:
                self.assertIn("Комментарий записан", ag_judge.comment(first["id"], "это не последнее, а первое в списке"))
            reviewed.assert_not_called()
            self.assertEqual(status.current()["redo_of"], "a1")
            self.assertEqual(task_log.get("a1")["outcome"]["verdict"], "done_with_remarks")
            redo = task_log.save({**status.current(), "answer": "Включила самое новое по дате"})
        finally:
            status.end("ok")
        seen = {}
        with mock.patch.object(ag_judge.brain, "run", lambda *a, **k: seen.update(text=a[3])):
            ag_judge.review(redo, ["ag_web: открыла канал, сортировка по новым"])
        self.assertIn("Это переделка после замечания Темы", seen["text"])
        self.assertIn("Включила «Как я строил дом»", seen["text"])
        self.assertIn("это не последнее, а первое в списке", seen["text"])
        self.assertIsInstance(ag_judge.comment("нет-такой", "x"), ag_judge.Failure)

    def test_a_review_is_a_service_task_of_its_own(self):
        """The review ran with no task: missing from "Processes", its steps journaled under nothing (README: a service job is a
        status.begin(..., service=True) task)."""
        from sena.memory import task_log
        entry = task_log.save({"id": "r1", "text": "найди билеты", "start": 1.0, "steps": [], "answer": "нашла"})
        seen = {}

        def run(*a, **k):
            seen.update(task=dict(status.current() or {}))
        with mock.patch.object(ag_judge.brain, "run", run):
            ag_judge.review(entry, [])
        self.assertTrue(seen["task"]["service"])
        self.assertIn("Разбор задачи: найди билеты", seen["task"]["text"])
        self.assertIsNone(status.current())   # ended with the review

    def test_rebuild_after_the_registry_was_set_aside(self):
        lessons.note("ag_code", "В проекте KSclay тесты запускаются pnpm test", "ksclay")
        index.drop(lessons.KIND)
        lessons.sync_index()
        self.assertEqual(len(index.refs(lessons.KIND)), 1)
        self.assertTrue(store.exists(config.MEMORY_DIR / "agents" / "ag_code.json"))   # a record in the one store


if __name__ == "__main__":
    unittest.main()
