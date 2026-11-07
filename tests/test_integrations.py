"""Integrations: an integration disabled from the remote disappears from the model's tools, the server reports its state and toggles the flag,
usage counters are computed from real journal events, and the app's list matches the server's list."""
import re
import unittest
from pathlib import Path
from unittest import mock

from sena.agents.ag_sena import tools
from sena.core import events, settings
from sena.memory import vault_sync
from sena.phone import desk

SWIFT = Path(__file__).resolve().parent.parent / "mac" / "Sources" / "Views" / "IntegrationsView.swift"


class Integrations(unittest.TestCase):
    def setUp(self):
        before = settings.load()
        self.addCleanup(settings.update, {k: before[k] for k in settings.DEFAULTS})
        desk._tallies.clear()   # the counters are reused for desk.TALLY_FRESH seconds: these tests count this moment's

    def fresh(self):
        desk._tallies.clear()
        return desk.integrations_view()

    def test_disabled_integration_hides_tools(self):
        names = {t["name"] for t in tools.available()}
        self.assertIn("browser_task", names)
        self.assertIn("mail_search", names)
        settings.update({"use_browser": False, "use_google": False})
        names = {t["name"] for t in tools.available()}
        self.assertNotIn("browser_task", names)
        self.assertNotIn("mail_search", names)
        self.assertIn("computer_task", names)   # everything else stays in place
        self.assertEqual(tools.integration_of("sheet_read"), "use_google")
        self.assertIsNone(tools.integration_of("remember"))

    def test_view_and_toggle(self):
        view = desk.integrations_view()
        self.assertEqual(set(view), set(desk.INTEGRATIONS))
        for row in view.values():
            for key in ("enabled", "uses_today", "last_used", "errors_today", "usd_today"):
                self.assertIn(key, row)
        self.assertIsNone(view["openai"]["enabled"])   # part of Sena herself: cannot be turned off
        self.assertTrue(view["browser"]["enabled"])
        self.assertIn("выключена", desk.act("integration", {"id": "browser", "on": False})["result"])
        self.assertFalse(desk.integrations_view()["browser"]["enabled"])
        self.assertFalse(settings.get("use_browser"))
        self.assertIn("error", desk.act("integration", {"id": "openai", "on": False}))
        self.assertIn("error", desk.act("integration", {"id": "nope", "on": True}))

    def test_hearing_switch_shows_in_the_view(self):
        # the /listen route itself is checked over HTTP in test_server
        settings.update({"use_hearing": False})
        self.assertEqual(desk.integrations_view()["hearing"]["enabled"], False)
        settings.update({"use_hearing": True})
        self.assertEqual(desk.integrations_view()["hearing"]["enabled"], True)

    def test_uses_and_last_use_are_counted_from_the_journal(self):
        def row(name):
            return self.fresh()[name]
        before = {n: row(n) for n in ("obsidian", "memory", "browser", "maps", "sources")}
        events.log("sync", "указатель памяти: 3 записи")   # an unrelated sync: counted as memory, not Obsidian
        events.log("sync", "дневник 2026-10-19: записан (2 задач)", source="vault")
        events.log("tool", "страница открыта", name="browser_task", args={})
        events.log("tool", "другой инструмент", name="remember", args={})
        events.log("fetch", "https://api.tomtom.com/x", source="TomTom")
        events.log("fetch", "https://api.open-meteo.com/x", source="Open-Meteo")
        after = {n: row(n) for n in before}
        self.assertEqual(after["obsidian"]["uses_today"] - before["obsidian"]["uses_today"], 1)   # the counter used to not match any event
        self.assertEqual(after["memory"]["uses_today"] - before["memory"]["uses_today"], 2)
        self.assertEqual(after["browser"]["uses_today"] - before["browser"]["uses_today"], 1)
        self.assertEqual(after["maps"]["uses_today"] - before["maps"]["uses_today"], 1)
        self.assertEqual(after["sources"]["uses_today"] - before["sources"]["uses_today"], 1)
        self.assertGreater(after["obsidian"]["last_used"], 0)

    def test_view_reads_counters_not_event_bodies(self):
        # the app requests this data every five seconds: event details (which hold traces and model text) must not be read
        with mock.patch.object(events, "search", side_effect=AssertionError("подробности событий не нужны")):
            view = desk.integrations_view()
        self.assertEqual(set(view), set(desk.INTEGRATIONS))

    def test_errors_are_matched_by_words_and_counted_without_traces(self):
        events.error("Chrome упал", RuntimeError("страница не открылась"))
        row = desk.integrations_view()["browser"]
        self.assertGreaterEqual(row["errors_today"], 1)
        self.assertIn("Chrome упал", row["last_error"])

    def test_vault_sync_marks_its_events_as_obsidian(self):
        before = desk.integrations_view()["obsidian"]["uses_today"]
        vault_sync.sync_all(days=1)
        self.assertGreater(self.fresh()["obsidian"]["uses_today"], before)

    def test_app_list_matches_server_list(self):
        # the app keeps its own list of integrations and keys: if it drifts from the server, a card would stay empty or show a false red dot
        text = SWIFT.read_text(encoding="utf-8")
        ids = set(re.findall(r'Integration\(id: "([a-z_]+)"', text))
        self.assertEqual(ids, set(desk.INTEGRATIONS))
        tiles = {t[4:] for t in re.findall(r'Tile\(id: "(svc:[a-z_]+)"', text)}
        self.assertEqual(tiles, ids)   # every integration has a tile, and every tile has an integration
        keys = set()
        for group in re.findall(r"keys: \[([^\]]*)\]", text):
            keys |= set(re.findall(r'"([A-Z_]+)"', group))
        self.assertLessEqual(keys, set(desk.KEYS))   # a key the server doesn't check would forever show as "not in .env"


if __name__ == "__main__":
    unittest.main()
