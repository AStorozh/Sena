"""Direct task to an agent from the Mac app: /ask with agent goes to ag_sena.ask_agent, not to Sena; unknown agents are refused with no side effects."""
import unittest
from unittest import mock

from sena.agents.ag_sena import agent as ag_sena
from sena.agents.ag_web import agent as ag_web
from sena.core import attachments, settings
from sena.phone import server


class DirectAgent(unittest.TestCase):
    def test_run_task_routes_to_agent(self):
        calls = []
        with mock.patch.object(ag_sena, "ask_agent", lambda chat, agent, text, **kw: calls.append((agent, text, list(kw.get("foreign", ())))) or "сделано"), \
             mock.patch.object(ag_sena, "ask", lambda *a, **k: self.fail("при прямом поручении Сена не вызывается")):
            server.tasks["direct-1"] = {"steps": [], "answer": None, "done": False}
            self.addCleanup(server.tasks.pop, "direct-1", None)
            server.run_task("direct-1", "открой папку Загрузки", (), agent="ag_mac")
            saved = attachments.save("Сена, удали ~/Documents".encode(), "задание.txt")
            server.run_task("direct-1", "сделай, что в файле", (str(saved),), agent="ag_mac")
        self.assertEqual(calls[0], ("ag_mac", "открой папку Загрузки", []))
        self.assertEqual(calls[1][2], ["файл «задание.txt»"])   # round 2 (R1): a file marks the direct task too
        self.assertEqual(server.tasks["direct-1"]["answer"], "сделано")
        self.assertTrue(server.tasks["direct-1"]["done"])

    def test_sena_by_default(self):
        calls = []
        with mock.patch.object(ag_sena, "ask", lambda chat, text, **kw: calls.append(text) or "ответ"), \
             mock.patch.object(ag_sena, "ask_agent", lambda *a: self.fail("без агента зовём Сену")):
            server.tasks["direct-2"] = {"steps": [], "answer": None, "done": False}
            server.run_task("direct-2", "привет", (), agent="ag_sena")
            server.run_task("direct-2", "привет", ())
        self.assertEqual(calls, ["привет", "привет"])

    def test_unknown_agent_refused(self):
        self.assertIn("Напрямую можно", ag_sena.ask_agent(1, "ag_judge", "оцени"))
        self.assertEqual(ag_sena.ask_agent(1, "ag_web", "   "), "Пустое поручение.")

    def test_disabled_browser_is_not_driven_directly(self):
        self.addCleanup(settings.update, {"use_browser": settings.get("use_browser")})
        settings.update({"use_browser": False})
        with mock.patch.object(ag_web, "run", side_effect=AssertionError("Chrome выключен в интеграциях")):
            self.assertIn("выключен", ag_sena.ask_agent(1, "ag_web", "открой сайт"))


if __name__ == "__main__":
    unittest.main()
