"""Panels the server describes and the apps only draw: every panel follows the contract both renderers know, every action
named in a panel exists, and an action comes back with its answer and the panel as it is now — through the desk API."""
import json
import time
import unittest

from sena import config, scheduler
from sena.core import confirm, status, store
from sena.memory import goals, lessons
from sena.phone import desk, panels
from sena.phone.api import ApiError

BLOCKS = {"stats", "text", "section", "items", "form", "switch", "buttons"}
FIELDS = {"text", "multiline", "number", "choice"}


def actions_of(panel):
    for b in panel["blocks"]:
        if b["type"] == "items":
            for item in b["items"]:
                yield from item.get("actions", [])
        elif b["type"] == "form":
            yield b["submit"]
        elif b["type"] == "switch":
            yield b["action"]
        elif b["type"] == "buttons":
            yield from b["items"]


class Contract(unittest.TestCase):
    def setUp(self):
        store.clear(config.GOALS_FILE)
        for agent in lessons.AGENTS:   # each test starts with no lessons left by others
            store.clear(lessons._path(agent))
        goals.add("Английский до B2", "для работы", "01.05.2027", ["курс", "клуб"])
        lessons.note("ag_web", "На hh.ru вход во вложенном окне", "hh.ru", by="self")

    def test_every_panel_speaks_the_contract(self):
        seen = 0
        for pid in panels.PANELS:
            panel = panels.panel(pid)
            self.assertEqual(panel["id"], pid)
            self.assertNotIn("null", json.dumps(panel))   # absent keys, never nulls: both apps read "not set" the same way
            for block in panel["blocks"]:
                self.assertIn(block["type"], BLOCKS, pid)
                if block["type"] == "form":
                    self.assertTrue(all(f["kind"] in FIELDS for f in block["fields"]), pid)
            for action in actions_of(panel):
                seen += 1
                self.assertIn(action["id"], panels.ACTIONS[pid], f"{pid}: кнопка «{action['label']}» ведёт к несуществующему действию")
                if "input" in action:
                    self.assertIn(action["input"]["kind"], FIELDS - {"choice"})
        self.assertGreater(seen, 10, "панели без действий: проверка выше ничего не проверила")

    def test_the_exam_panel_says_when_its_result_is_stale(self):
        import time
        from unittest import mock
        run = {"t": time.time() - 60, "passed": 9, "total": 10, "usd": 0.9, "version": "abc1234", "cases": [], "verdict": ""}
        self.addCleanup(store.save_json, config.EXAM_FILE, store.load_json(config.EXAM_FILE, {"runs": []}))
        store.save_json(config.EXAM_FILE, {"runs": [run]})
        with mock.patch.object(config, "version", return_value="def5678"):
            texts = [b["text"] for b in panels.panel("exam")["blocks"] if b["type"] == "text"]
        self.assertTrue(any("изменился код (abc1234 → def5678)" in t for t in texts))
        with mock.patch.object(config, "version", return_value="abc1234"):
            texts = [b["text"] for b in panels.panel("exam")["blocks"] if b["type"] == "text"]
        self.assertFalse(any("изменился код" in t for t in texts))

    def test_a_goal_moves_from_the_panel_and_the_panel_comes_back_fresh(self):
        gid = goals.all_goals()[0]["id"]
        out = desk.act("panel_act", {"panel": "goals", "action": "goal_step_done", "args": {"id": gid, "step": "курс"}})
        self.assertIn("50%", out["result"])
        item = next(i for b in out["panel"]["blocks"] if b["type"] == "items" for i in b["items"] if i["id"] == gid)
        self.assertEqual(item["progress"], 50)
        added = desk.act("panel_act", {"panel": "goals", "action": "goal_add", "values": {"title": "Полумарафон", "steps": "бег 3 раза\nдлинная в субботу", "check_every_days": "5"}})
        self.assertIn("через 5 дн", added["result"])
        self.assertEqual(len(goals.all_goals()[-1]["steps"]), 2)

    def test_the_owner_keeps_corrects_and_retires_agents_lessons(self):
        draft = lessons.drafts("ag_web")[0]
        desk.act("panel_act", {"panel": "lessons", "action": "lesson_keep", "args": {"agent": "ag_web", "id": draft["id"]}})
        desk.act("panel_act", {"panel": "lessons", "action": "lesson_edit", "args": {"agent": "ag_web", "id": draft["id"]}, "values": {"value": "Вход на hh.ru — во вложенном окне f1"}})
        [kept] = lessons.listing("ag_web")
        self.assertEqual((kept["state"], kept["by"]), ("active", "owner"))
        with self.assertRaisesRegex(ApiError, "Нет такого агента"):
            desk.act("panel_act", {"panel": "lessons", "action": "lesson_keep", "args": {"agent": "ag_x", "id": "1"}})
        with self.assertRaisesRegex(ApiError, "Такого действия на этой панели нет"):
            desk.act("panel_act", {"panel": "goals", "action": "lesson_keep", "args": {}})   # an action of another panel is refused

    def test_a_goal_is_closed_and_brought_back_from_the_panel(self):
        gid = goals.all_goals()[0]["id"]
        desk.act("panel_act", {"panel": "goals", "action": "goal_close", "args": {"id": gid, "status": goals.DONE}})
        self.assertEqual(goals.all_goals()[0]["status"], goals.DONE)
        desk.act("panel_act", {"panel": "goals", "action": "goal_close", "args": {"id": gid, "status": goals.ACTIVE}})
        self.assertEqual(goals.all_goals()[0]["status"], goals.ACTIVE)

    def test_a_refused_action_is_an_error_and_the_panel_comes_back(self):
        out = desk.act("panel_act", {"panel": "mcp", "action": "mcp_add", "values": {"name": "BAD NAME!", "title": "x", "command": "uvx x@1"}})
        self.assertIn("латиницей", out["error"])   # was a green "done", and the app wiped the five fields typed
        self.assertNotIn("result", out)
        self.assertEqual(out["panel"]["id"], "mcp")

    def test_a_typed_number_that_is_not_one_is_a_clear_refusal(self):
        for body in ({"panel": "goals", "action": "goal_add", "values": {"title": "Цель", "check_every_days": "раз в неделю"}},
                     {"panel": "works", "action": "work_budget", "args": {"id": "w1"}, "values": {"value": "много"}}):
            with self.assertRaisesRegex(ApiError, "нужно число"):   # was a 500 from float()
                desk.act("panel_act", body)
        added = desk.act("panel_act", {"panel": "goals", "action": "goal_add", "values": {"title": "Ещё цель", "check_every_days": "3,0"}})
        self.assertIn("через 3 дн", added["result"])   # a comma is a decimal point too

    def test_number_defaults_and_inputs_that_may_stay_empty(self):
        from sena.world import mcp
        form = next(b for b in panels.panel("goals")["blocks"] if b["type"] == "form")
        days = next(f for f in form["fields"] if f["name"] == "check_every_days")
        self.assertEqual(days["value"], goals.CHECK_DAYS)   # a number: the apps show it as text (the contract says so)
        self.assertIn("Сервер «time» добавлен", str(mcp.add("time", "Время", "uvx mcp-server-time@2026.10.19")))
        self.addCleanup(mcp.remove, "time")
        read = next(a for a in actions_of(panels.panel("mcp")) if a["id"] == "mcp_read")
        self.assertTrue(read["input"]["optional"])   # emptying the list is the only way to make every tool ask again

    def test_the_exam_limit_is_set_from_its_panel(self):
        from sena.core import settings
        self.addCleanup(settings.update, {"exam_budget_usd": settings.get("exam_budget_usd")})
        out = desk.act("panel_act", {"panel": "exam", "action": "exam_budget", "values": {"value": "2,5"}})
        self.assertIn("не дороже 2.5 $", out["result"])   # no app had a field for it before
        self.assertEqual(settings.get("exam_budget_usd"), 2.5)
        self.assertIn("error", desk.act("panel_act", {"panel": "exam", "action": "exam_budget", "values": {"value": "500"}}))   # out of the range: refused
        self.assertEqual(settings.get("exam_budget_usd"), 2.5)

    def test_backup_from_the_panel_runs_once_at_a_time(self):
        import threading
        from unittest import mock
        from sena.agents import ag_night
        gate = threading.Event()
        self.addCleanup(gate.set)
        made = []
        with mock.patch.object(ag_night, "backup_memory", lambda force=False: made.append(force) or gate.wait(5)):
            first = desk.act("panel_act", {"panel": "backups", "action": "backup_now"})
            second = desk.act("panel_act", {"panel": "backups", "action": "backup_now"})
            gate.set()
            for _ in range(100):   # the run is over before the next test
                if "Копия памяти" not in scheduler.busy():
                    break
                time.sleep(0.02)
        self.assertIn("Копия делается", first["result"])
        self.assertIn("уже делается", second["error"])   # a double press used to start two copies at once
        self.assertEqual(made, [True])   # asked for now: made even when the night's copy of today exists

    def test_the_exam_waits_for_sena_to_be_on(self):
        from unittest import mock
        from sena.core import power
        with mock.patch.object(power, "is_off", return_value=True):
            panel = panels.panel("exam")
            out = desk.act("panel_act", {"panel": "exam", "action": "exam_run"})
        self.assertNotIn("exam_run", [a["id"] for a in actions_of(panel)])   # no start button she would refuse anyway
        self.assertTrue(any(b["type"] == "text" and "выключена" in b["text"] for b in panel["blocks"]))
        self.assertIn("выключена", out["error"])   # a refusal, not a green «done» with the refusal's words
        self.assertNotIn("result", out)

    def test_what_waits_for_a_yes_is_answered_from_the_panel(self):
        status.new_turn()
        sid = confirm.hold("Мама", "опоздаю", lambda: "отправлено")
        self.assertEqual(desk.get("panels", {})["items"][0]["badge"], 1)
        out = desk.act("panel_act", {"panel": "confirm", "action": "confirm_yes", "args": {"id": sid}})
        self.assertEqual(out["result"], "отправлено")
        self.assertEqual(out["panel"]["blocks"][0]["items"], [])


if __name__ == "__main__":
    unittest.main()
