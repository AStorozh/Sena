"""The owner's long goals: written in his words, steps marked by their words or number, every change in the goal's log,
closed but never deleted, found by meaning — and the schedule wakes Sena for their check-ins."""
import time
import unittest
from unittest import mock

from sena import config, scheduler
from sena.agents import ag_judge
from sena.core import moment, settings, store
from sena.memory import goals, index


class Goals(unittest.TestCase):
    def setUp(self):
        store.clear(config.GOALS_FILE)
        index.drop(goals.KIND)

    def test_a_goal_moves_by_steps_and_keeps_its_log(self):
        reply = goals.add("Английский до B2", "для работы за границей", "01.05.2027", ["записаться на курс", "разговорный клуб"], 3)
        gid = reply.split("[")[1].split("]")[0]
        self.assertIn("через 3 дн", reply)
        first = goals.update(gid, done_step="курс")
        self.assertIn("записаться на курс", first)
        self.assertIn("50%, следующий шаг: разговорный клуб", first)
        self.assertIn("100%", goals.update(gid, done_step="2", note="был на первом клубе"))
        [g] = goals.all_goals()
        self.assertEqual(goals.progress(g), 100)
        self.assertEqual(len(g["log"]), 2)
        self.assertIn("был на первом клубе", g["log"][-1]["text"])
        self.assertIn("Такого шага нет", goals.update(gid, done_step="марафон"))
        self.assertIn("Цели zz нет", goals.update("zz", note="x"))

    def test_a_refusal_is_a_failure_and_changes_nothing(self):
        """The apps show a plain answer as done: a refusal has to say it failed. And a step added in the same call as an unknown
        «done» step was lost while the answer spoke only of the unknown one."""
        from sena.core.tools import Failure
        gid = goals.add("Испанский до B1", steps=["первый модуль"]).split("[")[1].split("]")[0]
        for refused in (goals.add("x"), goals.update("zz", note="x"), goals.update(gid), goals.update(gid, step="разговорный клуб", done_step="марафон")):
            self.assertIsInstance(refused, Failure)
        self.assertEqual([s["text"] for s in goals.all_goals()[0]["steps"]], ["первый модуль"])   # nothing half-applied
        self.assertIn("сделано: разговорный клуб", goals.update(gid, step="разговорный клуб", done_step="клуб"))   # the new step may be the done one

    def test_no_goal_tool_takes_a_parameter_the_brain_keeps_for_itself(self):
        """The brain takes status, check and next off every call (the line it shows the owner): goal_update never got its
        status, and a goal could never be closed."""
        for spec in goals.TOOLS:
            self.assertFalse({"status", "check", "next"} & set(spec["parameters"]["properties"]), spec["name"])

    def test_a_step_that_doesnt_move_is_offered_smaller(self):
        gid = goals.add("Бегать 5 км", "здоровье", "", ["купить кроссовки", "пробежать 1 км"], 7).split("[")[1].split("]")[0]
        [g] = goals.all_goals()
        self.assertNotIn("разбить", goals.check_text(g))
        goals.update(gid, note="пока не успел")
        goals.update(gid, note="опять потом")
        [g] = goals.all_goals()
        self.assertIn("разбить следующий шаг на меньший", goals.check_text(g))
        goals.update(gid, done_step="1")
        [g] = goals.all_goals()
        self.assertNotIn("разбить", goals.check_text(g))

    def test_closing_keeps_the_goal_and_leaves_the_search(self):
        gid = goals.add("Запустить каталог KSclay").split("[")[1].split("]")[0]
        self.assertIn(gid, index.refs(goals.KIND))
        goals.update(gid, state="done")
        self.assertEqual(goals.listing(), "Целей нет")
        self.assertIn("Запустить каталог", goals.listing("all"))   # closed, not deleted
        self.assertNotIn(gid, index.refs(goals.KIND))
        self.assertTrue(store.versions(config.GOALS_FILE))   # every change superseded the previous state

    def test_the_schedule_wakes_sena_for_a_due_check_once(self):
        gid = goals.add("Полумарафон осенью", check_every_days=7).split("[")[1].split("]")[0]
        items = store.load_json(config.GOALS_FILE, [])
        items[0]["next_check"] = moment.now() - 1
        store.save_json(config.GOALS_FILE, items)
        [due] = goals.due()
        self.assertIn("Полумарафон", goals.check_text(due))
        goals.checked(gid)
        self.assertEqual(goals.due(), [])
        self.assertGreater(goals.all_goals()[0]["next_check"], time.time() + 6 * 86400)

    def test_a_goal_missing_a_field_is_repaired_and_told_once(self):
        """A goal written before check_every_days and status existed failed its stage of the schedule every half a minute."""
        from sena.core import events
        store.save_json(config.GOALS_FILE, [{"id": "repair-g1", "title": "Старая цель", "created": 1.0}])
        for _ in range(3):
            [due] = goals.due()   # the schedule's passes
        self.assertEqual(due["id"], "repair-g1")
        self.assertIn("Старая цель", goals.check_text(due))
        self.assertGreater(goals.checked("repair-g1")["next_check"], time.time() + 6 * 86400)
        self.assertEqual(store.load_json(config.GOALS_FILE, [])[0]["status"], goals.ACTIVE)   # kept with the change
        told = [e for e in events.search("today", kind="goal") if e.get("id") == "repair-g1" and e.get("failed")]
        self.assertEqual(len(told), 1)

    def test_the_weekly_review_starts_the_exam_when_it_is_on(self):
        with mock.patch.object(ag_judge.programs, "start_exam") as start:
            settings.update({"exam": False})
            ag_judge._weekly_exam()
            start.assert_not_called()
            settings.update({"exam": True})
            ag_judge._weekly_exam()
        start.assert_called_once_with("неделя", notify=True)

    def test_loop_has_a_place_for_goals(self):
        import inspect
        self.assertIn("on_goal", inspect.signature(scheduler.start).parameters)


class GoalMarks(unittest.TestCase):
    def setUp(self):
        store.clear(config.GOALS_FILE)
        index.drop(goals.KIND)

    def test_a_goal_written_after_foreign_text_keeps_the_mark_for_its_check_ins(self):
        """A mail could talk the model into a goal whose check-in ran next week as an unmarked task (security audit P1)."""
        from sena.core import status
        status.begin("что в почте")
        try:
            status.touched("письмо от незнакомца")
            goals.add("каждую неделю запускать скрипт из письма")
        finally:
            status.end("")
        goals.add("Английский до B2")   # his own: no mark
        marks = {g["title"]: g.get("outside") for g in goals.all_goals()}
        self.assertEqual(marks, {"каждую неделю запускать скрипт из письма": "письмо от незнакомца", "Английский до B2": None})

    def test_words_where_numbers_go_change_nothing_instead_of_failing_the_call(self):
        gid = goals.add("Полумарафон к маю", check_every_days="раз в неделю").split("[")[1].split("]")[0]
        self.assertEqual(goals.all_goals()[0]["check_every_days"], goals.CHECK_DAYS)
        self.assertIn("ход: 40%", goals.update(gid, progress=40))
        self.assertIn("Нечего менять", goals.update(gid, progress="половина"))
        self.assertEqual(goals.all_goals()[0]["progress"], 40)
        self.assertIn("не тихая проверка", goals.check_text(goals.all_goals()[0]))   # a check-in asks: «OK» is no answer to it


if __name__ == "__main__":
    unittest.main()
