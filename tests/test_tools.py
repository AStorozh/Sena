"""Tool descriptions match their functions: the model can't call something that doesn't exist, or pass something extra."""
import inspect
import unittest
from unittest import mock

from sena import scheduler
from sena.agents import ag_ears, ag_judge, ag_mac, ag_night
from sena.agents.ag_sena import tools as ag_sena_tools
from sena.agents.ag_web import agent as ag_web
from sena.memory import facts, people, task_log

SERVICE_FIELDS = {"check", "status", "next"}   # these are stripped by brain.think before the function is called


def accepted(func):
    return set(inspect.signature(func).parameters)


def required_by_function(func):
    """Parameters without a default value: the function call will fail without them."""
    return {n for n, p in inspect.signature(func).parameters.items() if p.default is inspect.Parameter.empty}


class ToolContracts(unittest.TestCase):
    def check(self, specs, functions, owner):
        names = {s["name"] for s in specs}
        self.assertEqual(len(names), len(specs), f"{owner}: повторяющиеся имена инструментов")
        for spec in specs:
            name = spec["name"]
            self.assertIn(name, functions, f"{owner}: нет функции для инструмента {name}")
            params = set(spec["parameters"]["properties"]) - SERVICE_FIELDS
            self.assertLessEqual(params, accepted(functions[name]), f"{owner}.{name}: функция не принимает {params - accepted(functions[name])}")
            required = set(spec["parameters"].get("required", [])) - SERVICE_FIELDS
            self.assertLessEqual(required, params, f"{owner}.{name}: обязательное поле не описано")
            needed = required_by_function(functions[name]) - SERVICE_FIELDS
            self.assertLessEqual(needed, params, f"{owner}.{name}: функция требует {needed - params}, а в описании этого нет — модель вызовет без них")

    def test_coordinator(self):
        self.check(ag_sena_tools.TOOLS, ag_sena_tools.FUNCTIONS, "ag_sena")

    def test_web(self):
        functions = {"browser_code": ag_web.browser_code, "look": ag_web.look, "ask_tema": ag_web.ask_tema}
        self.check(ag_web.TOOLS, functions, "ag_web")

    def test_mac(self):
        self.check(ag_mac.TOOLS, {"run_command": ag_mac.run_command, "chrome_tabs": ag_mac.chrome_tabs, "media_key": ag_mac.media_key, "look": ag_mac.look, "click": ag_mac.click,
                                   "type_text": ag_mac.type_text, "press_key": ag_mac.press_key}, "ag_mac")

    def test_judge(self):
        # ag_judge supplies the task to set_outcome itself; the model only sees the tail of the real function's parameters
        tail = list(inspect.signature(task_log.set_outcome).parameters.values())[1:]   # with their defaults: "by" is not the model's
        stub = lambda **kw: None   # noqa: E731
        stub.__signature__ = inspect.Signature(tail)
        funcs = {**ag_judge.FUNCTIONS, "set_outcome": stub}
        self.check(ag_judge.TOOLS, funcs, "ag_judge")

    def test_night_prompt_describes_every_field_of_its_answer(self):
        # the night answers in one JSON (no tools): every field the code applies is explained to the model
        fields = set(ag_night.TIDY_SCHEMA["properties"]) | set(ag_night.TIDY_SCHEMA["properties"]["skills"]["items"]["properties"])
        for name in fields:
            self.assertIn(name, ag_night.TIDY)

    def test_live_voice(self):
        # Gemini Live calls these two; the conversation (Talk) is the first argument, the model fills in the rest
        import functools
        from sena.phone import voice
        self.check(voice.TOOLS, {name: functools.partial(func, None) for name, func in voice.FUNCTIONS.items()}, "voice")

    def test_people_and_facts_and_schedule(self):
        self.check(people.TOOLS, people.FUNCTIONS, "people")
        self.check(facts.TOOLS, facts.FUNCTIONS, "facts")
        self.check(scheduler.TOOLS, scheduler.FUNCTIONS, "scheduler")

    def test_ears_prompt_describes_every_field_the_code_reads(self):
        # the home model answers in JSON: every field of the schema is explained in the prompt, nothing else is asked for
        fields = set(ag_ears.SCHEMA["properties"]) | set(ag_ears.SCHEMA["properties"]["people"]["items"]["properties"]) \
            | set(ag_ears.SCHEMA["properties"]["merge"]["items"]["properties"])
        for name in fields:
            self.assertIn(name, ag_ears.ANALYZE)



class ServiceFields(unittest.TestCase):
    def test_no_tool_has_a_real_parameter_the_brain_takes_for_its_own(self):
        """The audit of 25.10: the brain takes check, status and next off every call (the model's note on its last step, the
        caption for the app, its guess at the next step), so a real parameter of that name never reached its tool — a goal was
        never closed, a comment never moderated. Such a name may carry only one of the shared service fields (core/tools.py);
        the browser and computer agents use them on purpose."""
        from sena.core import tools as core

        def service(spec):
            return spec in (core.CHECK, core.LOOK_STATUS, core.NEXT) or str(spec.get("description", "")).startswith("Что сейчас делаешь, для Темы")
        tables = {"ag_sena": ag_sena_tools.TOOLS, "ag_web": ag_web.TOOLS, "ag_mac": ag_mac.TOOLS, "ag_judge": ag_judge.TOOLS}
        wrong = [f"{owner}.{spec['name']}({name})" for owner, specs in tables.items() for spec in specs
                 for name, field in (spec.get("parameters") or {}).get("properties", {}).items() if name in SERVICE_FIELDS and not service(field)]
        self.assertEqual(wrong, [], "переименуй параметр: мозг забирает его у каждого вызова")


class Toolbox(unittest.TestCase):
    """The coordinator's tools: about thirty always at hand, the rest in groups the brain opens when it needs them."""

    def test_every_tool_is_at_hand_or_in_exactly_one_group(self):
        from sena.agents.ag_sena import toolbox
        names = [t["name"] for t in ag_sena_tools.TOOLS]
        grouped = [n for g in toolbox.GROUPS.values() for n in g["tools"]]
        self.assertEqual(len(grouped), len(set(grouped)), "инструмент в двух группах")
        self.assertEqual(set(grouped) - set(names), set(), "в группе инструмент, которого нет")
        self.assertEqual(set(toolbox.CORE) - set(names), set(), "под рукой инструмент, которого нет")
        self.assertEqual(set(names) - set(grouped) - set(toolbox.CORE), set(), "инструмент ни под рукой, ни в группе: модель его не найдёт")

    def test_open_tools_names_the_tools_and_brings_the_rules(self):
        from sena.agents.ag_sena import toolbox
        opened = toolbox.open_tools(["finance", "нет-такой"])
        self.assertIn("crypto_price", opened["open"])
        self.assertIn("советов", opened["text"])   # the group's rules come with its tools
        self.assertIn("Таких групп нет: нет-такой", opened["text"])
        specs = ag_sena_tools.for_brain()
        deferred = {t["name"] for t in specs if t.get("defer")}
        self.assertIn("crypto_price", deferred)
        self.assertNotIn("remember", deferred)
        self.assertIn("finance:", toolbox.catalog())

    def test_every_tool_has_a_step_for_the_owner_and_no_dead_caption(self):
        """A tool without a caption showed the owner no step at all; a caption without a tool is a leftover."""
        names = {t["name"] for t in ag_sena_tools.TOOLS}
        self.assertEqual(set(ag_sena_tools.CAPTIONS) - names, set(), "подпись инструмента, которого нет")
        quiet = {"open_tools", "claude_task"}   # opening a group is no step of the work; claude_task is captioned by describe()
        self.assertEqual(names - set(ag_sena_tools.CAPTIONS) - quiet, set(), "инструмент без подписи шага")
        self.assertNotIn("check", ag_sena_tools.TOOLS[[t["name"] for t in ag_sena_tools.TOOLS].index("claude_task")]["parameters"]["properties"])


class SendFile(unittest.TestCase):
    def test_sena_s_own_data_keys_and_logins_never_go_to_telegram(self):
        """send_file sent the Telegram session to Telegram's servers while view_file refused to even open it."""
        import os
        from unittest import mock
        from sena import config
        sent = []
        public = config.INBOX_DIR / "договор.pdf"
        public.write_bytes(b"%PDF")
        self.addCleanup(public.unlink)
        with mock.patch.object(ag_sena_tools.api, "send_file", lambda path: sent.append(path) or "Файл отправлен"):
            for path in (config.STATE_DIR / "tema.session", config.DATA / "memory" / "memory.md", os.path.expanduser("~/.ssh/id_ed25519")):
                self.assertIsInstance(ag_sena_tools.send_file(str(path)), ag_sena_tools.Failure, path)
            self.assertEqual(ag_sena_tools.send_file(str(public)), "Файл отправлен")
        self.assertEqual(sent, [str(public)])


class AgentsRoundTwo(unittest.TestCase):
    """Round 2 of the agents layer: her own long outputs go to him as a file; a step's caption says what the confirmation does."""

    def test_her_own_long_output_goes_to_him_as_a_file_but_nothing_else_of_her_data(self):
        """R17: «пришли этот список файлом» answered «личные данные Сены» for data/spill, where collected lists and long
        outputs are kept for him."""
        from unittest import mock
        from sena import config
        from sena.core import safety
        sent = []
        listing = safety.spill("строка списка\n" * 2000, 100, "данные агента")
        path = listing.split("Весь текст сохранён: ")[1].split(" — ")[0]
        self.addCleanup(lambda: __import__("os").unlink(path))
        beside = config.SPILL_DIR / ".." / "memory" / "memory.md"   # a way out of the folder is still her data
        with mock.patch.object(ag_sena_tools.api, "send_file", lambda p: sent.append(p) or "Файл отправлен"):
            self.assertEqual(ag_sena_tools.send_file(path), "Файл отправлен")
            self.assertIsInstance(ag_sena_tools.send_file(str(beside)), ag_sena_tools.Failure)
        self.assertEqual(sent, [path])

    def test_a_confirmation_step_says_what_it_does(self):
        """P7: «Отправляю» was shown while she applied a code change, showed a secret or ran an agent after foreign text."""
        from sena.core import confirm
        sid = confirm.hold("поручение компьютеру", "открой Загрузки", lambda: "ок")
        self.addCleanup(confirm.pending.pop, sid, None)
        self.assertEqual(ag_sena_tools.describe("confirm_send", {"id": sid}), "Делаю, что ты подтвердил: поручение компьютеру")
        self.assertEqual(ag_sena_tools.describe("confirm_send", {"id": "nope"}), "Делаю, что ты подтвердил")

    def test_every_json_answer_of_the_agents_names_its_fields_in_its_prompt(self):
        """P24: the night and the ears were checked; the other narrow jobs of the agents were not."""
        from sena.agents import sources
        from sena.agents.ag_sena import prompt
        from sena.agents.ag_web import recipes
        pairs = {"feedback": (prompt.FEEDBACK, prompt.FEEDBACK_SCHEMA), "heartbeat": (prompt.HEARTBEAT, prompt.HEARTBEAT_SCHEMA),
                 "router": (recipes.ROUTER, recipes.SCHEMA), "sources": (sources.EXTRACT, sources.FACTS)}
        for job, (text, schema) in pairs.items():
            fields = set(schema["properties"])
            for nested in schema["properties"].values():
                fields |= set((nested.get("items") or {}).get("properties", {}))
            self.assertTrue(fields, job)
            for name in fields:
                self.assertIn(name, text, f"{job}: поле {name} не названо в подсказке")


if __name__ == "__main__":
    unittest.main()


class ChromeTabs(unittest.TestCase):
    """03.11.2026: three tabs of one clip, and nothing could close them — AppleScript to Chrome from the sandbox is refused,
    and clicks without «Универсальный доступ» are dropped by the system without a word."""

    def test_an_address_opens_in_the_tab_of_its_site_not_a_new_one(self):
        from unittest import mock
        ran = []

        def script(*args):
            ran.append(args)
            return "1.1  http://127.0.0.1:8000/  Логотипы\n1.2  https://www.youtube.com/watch?v=a  Клип"
        with mock.patch.object(ag_mac, "_tabs_script", script):
            ag_mac.chrome_tabs("open", "https://youtube.com/watch?v=b")
            ag_mac.chrome_tabs("open", "https://example.com")
        self.assertEqual(ran[1], ("open", "https://youtube.com/watch?v=b", "1", "2"))   # the YouTube tab, reused
        self.assertEqual(ran[3], ("open", "https://example.com", "0", "0"))           # no tab of that site: a new one
        self.assertIsInstance(ag_mac.chrome_tabs("close", tab="три"), ag_mac.Failure)

    def test_without_the_accessibility_permission_a_click_says_so(self):
        from unittest import mock
        with mock.patch.object(ag_mac, "_input_allowed", lambda: False):
            self.assertEqual(ag_mac._cliclick(["c:1,1"]), ag_mac.NO_INPUT)


class MediaKey(unittest.TestCase):
    """Pausing a video by the Mac's media key, not by clicks on the page (03.11.2026: minutes of clicks, and another video opened)."""

    def test_who_plays_is_read_from_power_assertions(self):
        from sena.agents import ag_mac
        out = ('   pid 1320(Yandex): [0x1] 00:03:11 NoIdleSleepAssertion named: "Playing audio"\n'
               '   pid 603(coreaudiod): [0x2] 00:00:05 PreventUserIdleSystemSleep named: "com.apple.audio.x"\n')
        with mock.patch.object(ag_mac.subprocess, "run", lambda *a, **k: mock.Mock(stdout=out)):
            self.assertEqual(ag_mac.playing(), ["Yandex"])

    def test_unknown_key_and_no_access(self):
        from sena.agents import ag_mac
        self.assertIsInstance(ag_mac.media_key("louder"), ag_mac.Failure)
        with mock.patch.object(ag_mac, "_input_allowed", lambda: False):
            self.assertEqual(ag_mac.media_key("play_pause"), ag_mac.NO_INPUT)
