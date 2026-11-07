"""The Mac app (mac/) and Sena's server speak the same language: every view, action and route the app calls exists on the
server, the replies carry the fields its screens read in the shape they read them (a person's facts are text), the journal
and search kinds it names are the server's, the numbers it keeps a copy of match, and every text it shows has both translations."""
import re
import unittest
from pathlib import Path

from sena import config
from sena.core import events
from sena.memory import index, people, task_log
from sena.phone import desk, panels, server

MAC = Path(__file__).resolve().parent.parent / "mac"
SOURCES = {f.name: f.read_text(encoding="utf-8") for f in (MAC / "Sources").rglob("*.swift")}
ALL = "\n".join(SOURCES.values())
SERVER = (Path(__file__).resolve().parent.parent / "sena" / "phone" / "server.py").read_text(encoding="utf-8")
SCHEDULER = (Path(__file__).resolve().parent.parent / "sena" / "scheduler.py").read_text(encoding="utf-8")


def dictionary(name, text):
    """The keys of a Swift dictionary literal `let NAME = ["key": …, …]` (one level, string keys)."""
    body = re.search(rf"let {name} = \[(.*?)\]\n", text, re.S)
    assert body, f"нет словаря {name}"
    return set(re.findall(r'"([^"]+)":', body.group(1)))


def has(node, path):
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return True


class MacContract(unittest.TestCase):
    def test_views_the_app_reads_exist(self):
        names = set(re.findall(r'backend\.get\("([a-z_]+)"', ALL)) | set(re.findall(r'"/desk/api/([a-z_]+)', ALL))
        self.assertTrue(names, "приложение ничего не читает?")
        self.assertLessEqual(names, set(desk.GET), "приложение зовёт чтение, которого нет на сервере")

    def test_actions_the_app_calls_exist(self):
        names = set(re.findall(r'backend\.act\("([a-z_]+)"', ALL))
        names |= set(re.findall(r'runAction\([^,]+,\s*"([a-z_]+)"', ALL))                 # model.runAction(what, name, …)
        names |= set(re.findall(r'\b(?:run|act)\(L\("[^"]*"\),\s*"([a-z_]+)"', ALL))      # SystemView.run, RulesView.act
        names |= set(re.findall(r'indexAction\("([a-z_]+)"', ALL))
        slow = re.search(r"slowActions: Set<String> = \[(.*?)\]", SOURCES["API.swift"], re.S).group(1)
        names |= set(re.findall(r'"([a-z_]+)"', slow))                                     # a stale name there would never be slow
        self.assertTrue(len(names) > 20)
        self.assertLessEqual(names, set(desk.ACTIONS), "приложение зовёт действие, которого нет на сервере")

    def test_plain_routes_exist(self):
        routes = set(re.findall(r'backend\.api\("(/[a-z/_]+)', ALL)) | set(re.findall(r'post\("(/[a-z/_]+)', ALL))
        self.assertTrue({"/ask", "/search", "/voice", "/desk/upload"} <= routes)
        for route in routes:
            self.assertIn(f'"{route}"', SERVER, f"на сервере нет маршрута {route}")

    def test_people_facts_are_the_current_ones_as_text(self):
        p = people.new_person(name="Проверка Контракта")
        self.addCleanup(people.delete, p["id"])   # a named card would become a note in the vault other tests count
        people.update(p["id"], facts=["любит чай"])
        people.update(p["id"], facts=["любит чай с мятой"])   # the first one retires and stays in the card's history only
        row = next(r for r in desk.people_view() if r["id"] == p["id"])
        self.assertEqual(row["facts"], ["любит чай с мятой"])   # the Mac reads facts[].str: records instead of text were empty cards
        for key, kind in (("name", str), ("aliases", list), ("relationship", str), ("opinion", str), ("voices", int), ("created", float), ("last_seen", float)):
            self.assertIsInstance(row[key], kind, key)

    def test_overview_has_what_the_screens_read(self):
        o = desk.overview()
        read = {tuple(re.findall(r'\["([a-z_]+)"\]', m)) for m in re.findall(r'overview((?:\["[a-z_]+"\])+)', ALL)}
        read |= {("core", "alive"), ("core", "started"), ("core", "this_pid"), ("core", "is_core"), ("core", "pid"), ("process", "git"), ("process", "rss"),
                 ("process", "threads"), ("process", "load"), ("process", "python"), ("process", "platform"), ("accounts", "telegram_bot"), ("accounts", "telegram_account"),
                 ("accounts", "google"), ("accounts", "youtube"), ("accounts", "browser"), ("accounts", "keys"), ("today", "tasks"), ("today", "usd"),
                 ("scheduler", "jobs_total"), ("scheduler", "state"), ("scheduler", "busy"), ("index", "items"), ("index", "pending")}   # read through o[…], sc[…], ix[…]
        for path in read:
            self.assertTrue(has(o, path), "в обзоре нет поля " + ".".join(path))

    def test_config_memory_and_integrations_have_what_the_screens_read(self):
        c = desk.config_view()
        for path in (("models", "main"), ("models", "fast"), ("models", "voice"), ("models", "transcribe"), ("models", "tts"), ("models", "embed"),
                     ("models", "embed_place"), ("models", "embed_dims"), ("paths",), ("required",), ("app_port",), ("prices_as_of",), ("scheduler", "max_attempts"),
                     ("memory", "history_recent"), ("memory", "history_max"), ("memory", "tasks_max"), ("memory", "reflect_every"), ("memory", "backup_days"),
                     ("headless",), ("owner_id",), ("options",), ("ranges", "budget_usd_day")):
            self.assertTrue(has(c, path), "в настройках нет поля " + ".".join(path))
        self.assertEqual(c["models"]["embed_place"], config.ROLES["embed"])   # the model counting now, not a cloud one nobody uses
        m = desk.memory_view()
        for key in ("items", "pending", "size", "stats", "by_kind", "embed", "embed_place", "ann_threshold"):
            self.assertIn(key, m["index"])
        row = next(iter(desk.integrations_view().values()))
        for key in ("flag", "enabled", "uses_today", "last_used", "errors_today", "last_error", "usd_today", "needed", "ready"):
            self.assertIn(key, row)
        self.assertTrue({"items", "agents"} <= set(desk.get("secrets", {})))
        self.assertTrue(all({"id", "title", "badge"} <= set(i) for i in panels.listing()["items"]))   # the menu's counter on «Сена ведёт»

    def test_changes_have_what_the_self_development_tab_reads(self):
        from unittest import mock
        from sena.agents import ag_code
        ag_code._record({"id": "mac1", "branch": "sena/auto-mac1", "base": "abc", "task": "проверка", "created": 1.0, "tests": True, "diff": "", "report": ""})
        self.addCleanup(lambda: ag_code.CHANGES_FILE.unlink(missing_ok=True))
        with mock.patch.object(desk, "_git", return_value=""):
            view = desk.changes_view()
        row = next(c for c in view["changes"] if c["id"] == "mac1")
        for key in ("task", "branch", "base", "created", "tests", "diff", "report", "refused", "notes"):   # refused, notes — ag_code.verdict
            self.assertIn(key, row)
        self.assertTrue({"pending", "good", "branches", "head", "dirty"} <= set(view))

    def test_journal_and_search_kinds_are_the_servers(self):
        kinds = dictionary("KIND_RU", SOURCES["Model.swift"])
        self.assertLessEqual(set(events.KINDS), kinds, "в журнале есть виды, которых приложение не знает: покажет код вместо слова")
        found = {k for k, policy in index.KINDS.items() if policy["find"] is not False}   # what a general search can return
        self.assertLessEqual(found, dictionary("SEARCH_KIND_RU", SOURCES["ContentView.swift"]))
        go = re.search(r"func go\(_ r: JSON\) \{(.*?)\n    \}", SOURCES["ContentView.swift"], re.S).group(1)
        routed = set(re.findall(r'case ([^:]+):', go))
        routed = {k for line in routed for k in re.findall(r'"([^"]+)"', line)}
        self.assertLessEqual(found - {"сообщение"}, routed, "найденное этого вида ведёт не туда (сообщения — в переписку, это ветка default)")
        self.assertLessEqual(routed, set(index.KINDS), "приложение ведёт вид, которого в указателе нет")

    def test_copies_of_server_values_match(self):
        self.assertEqual(int(re.search(r"let PORT = (\d+)", SOURCES["API.swift"]).group(1)), config.APP_PORT)
        self.assertEqual(int(re.search(r"let VOICE_PORT = (\d+)", SOURCES["Ears.swift"]).group(1)), config.VOICE_PORT)
        factors = re.search(r"maxUpload = ([\d *]+)", SOURCES["API.swift"]).group(1).split("*")
        size = 1
        for f in factors:
            size *= int(f)
        self.assertEqual(size, server.MAX_BODY)
        folders = re.search(r"static let folders = \[([^\]]*)\]", SOURCES["FilesView.swift"]).group(1)
        self.assertEqual(re.findall(r'"([^"]+)"', folders), list(desk.FILE_DIRS))
        spawned = set(re.findall(r'_spawn\("([^"]+)"', SCHEDULER))
        self.assertLessEqual(set(re.findall(r'rawName: "([^"]+)"', SOURCES["RulesViews.swift"])), spawned, "фоновое дело называется на сервере иначе: «идёт сейчас» не загорится")

    def test_every_text_has_both_translations_and_none_is_left_over(self):
        line = re.compile(r'^"((?:[^"\\]|\\.)*)"\s*=\s*"((?:[^"\\]|\\.)*)";\s*$')
        used = set(task_log.CAUSES)   # shown through L(cause): the server's words for whose doing a failure was
        for text in SOURCES.values():
            used |= set(re.findall(r'\bL\(\s*"((?:[^"\\]|\\.)*)"\s*\)', text))
            used |= set(re.findall(r'\bplural\([^,]+,\s*"((?:[^"\\]|\\.)*)"', text))   # the "one" form is translated, the rest is English's "s"
        spec = re.compile(r"%(?:\d+\$)?[-+ 0#]*\d*(?:\.\d+)?(?:ll|l|h)?[@dfsuixXeEgGc]")
        for lang in ("ru", "en"):
            table = {}
            for row in (MAC / "Resources" / f"{lang}.lproj" / "Localizable.strings").read_text(encoding="utf-8").splitlines():
                m = line.match(row)
                if m:
                    table[m.group(1)] = m.group(2)
            missing = sorted(used - set(table))
            self.assertEqual(missing, [], f"{lang}: нет перевода — английское приложение покажет русский текст")
            self.assertEqual(sorted(set(table) - used), [], f"{lang}: строки, которые приложение больше не показывает")
            for key, value in table.items():
                self.assertEqual(spec.findall(key), spec.findall(value), f"{lang}: «{key}» — подстановки в переводе не те")


if __name__ == "__main__":
    unittest.main()


class AgentMoods(unittest.TestCase):
    """The characters by the notch: the face and the thought come from what the agent is really doing (desk._moods)."""

    def setUp(self):
        self.items = task_log.items[:]
        task_log.items[:] = []

    def tearDown(self):
        task_log.items[:] = self.items

    def test_the_bubble_is_never_empty(self):
        self.assertEqual(desk._moods([], False, False)["ag_web"], ("sleepy", "stop"))   # the core is off
        self.assertEqual(desk._moods([], True, False)["ag_web"], ("sleepy", "pause"))   # free for long
        import time
        task_log.items.append({"steps": [{"who": "ag_web", "text": "Ищу", "time": time.time() - 120}], "end": time.time() - 100, "cards": []})
        self.assertEqual(desk._moods([], True, False)["ag_web"], ("calm", "pause"))   # free, lately busy
        for face, thought in desk._moods([], True, False).values():
            self.assertTrue(thought)

    def test_a_running_tool_shows_and_between_tools_it_thinks(self):
        import time
        now = time.time()
        active = [{"steps": [{"who": "ag_sena", "text": "Компьютер взял задачу", "tool": "computer_task", "time": now - 30},
                             {"who": "ag_mac", "text": "Ставлю на паузу", "tool": "media_key", "time": now - 10},
                             {"who": "ag_code", "text": "Работаю в проекте Sena", "time": now - 10}],
                   "using": {"ag_sena": "computer_task"}}]
        moods = desk._moods(active, True, False)
        self.assertEqual(moods["ag_sena"], ("tense", "send"))   # handed over long ago and waits
        self.assertEqual(moods["ag_mac"], ("calm", "dots"))     # its tool answered: it thinks
        self.assertEqual(moods["ag_code"], ("calm", "code"))    # Claude Code works without the brain's tools
        active[0]["using"]["ag_mac"] = "wait"
        self.assertEqual(desk._moods(active, True, False)["ag_mac"], ("dizzy", "spinner"))
        active[0]["using"]["ag_mac"] = "click"
        active[0]["failed_by"] = {"ag_mac": 3}
        self.assertEqual(desk._moods(active, True, False)["ag_mac"], ("angry", "tap"))
        active[0]["steps"].insert(0, {"who": "ag_web", "text": "Ищу", "tool": "news_search", "time": now})
        active[0]["using"]["ag_web"] = "news_search"
        self.assertEqual(desk._moods(active, True, False)["ag_web"], ("surprise", "search"))   # a task just given

    def test_results_ratings_voice_and_waiting(self):
        import time
        task_log.items.append({"steps": [], "end": time.time(), "cards": [{"agent": "ag_web", "status": "failed"}]})
        task_log.items.append({"steps": [{"who": "ag_mac", "text": "x", "time": time.time()}], "end": time.time(), "cards": [],
                               "rating": {"value": 5, "time": time.time()}})
        moods = desk._moods([], True, False, "speaking")
        self.assertEqual(moods["ag_web"], ("sad", "cross"))
        self.assertEqual(moods["ag_mac"], ("wow", "check"))
        self.assertEqual(moods["ag_sena"], ("happy", "horn"))
        self.assertEqual(desk._moods([], True, False, "listening")["ag_sena"], ("surprise", "listen"))
        self.assertEqual(desk._moods([], True, True)["ag_sena"], ("surprise", "question"))   # waiting for Tema's answer

    def test_every_mapped_tool_exists(self):
        tools = set()
        for f in Path("sena").rglob("*.py"):
            tools |= set(re.findall(r'tool\("([a-z_]+)"', f.read_text(encoding="utf-8")))
        self.assertEqual(set(desk.THOUGHT_OF) - tools - {"wait"}, set())
