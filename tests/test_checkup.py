"""The system check: every part answers works / with a caveat / doesn't — why and what to do. The checks run together, each in
its own time, and a hung or broken one spoils nothing; switched off nothing is spent and no program starts; a paid check costs
less than a hundredth of a cent; the night tells a new failure once, quietly, and not a blip; the panel and the tool show it all."""
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from sena import config
from sena.agents import checkup
from sena.core import costs, llm, llm_claude, local, power, settings, store
from sena.phone import desk, panels
from sena.world import uptime


def fake(id, func, group="Сена", timeout=checkup.TIMEOUT):
    return checkup.Check(id, group, f"Часть {id}", func, timeout)


def answers(status, summary="", fix=""):
    return lambda spend: (status, summary, fix)


class Fakes(unittest.TestCase):
    """The runner on stand-in checks: nothing real is tried."""
    def setUp(self):
        store.clear(checkup.REPORT)

    def use(self, *checks):
        for name, value in (("CHECKS", list(checks)), ("BY_ID", {c.id: c for c in checks})):
            patcher = mock.patch.object(checkup, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class Running(Fakes):
    def test_the_checks_run_together_and_a_hung_or_broken_one_spoils_nothing(self):
        release, seen = threading.Event(), {}
        self.addCleanup(release.set)

        def slow(spend):
            time.sleep(0.3)
            seen["saved"] = [r["id"] for r in checkup.last().get("results", [])]   # what the panel already shows
            return checkup.OK, "медленно, но работает", ""

        def broken(spend):
            raise ValueError("диск не читается")

        def hung(spend):
            release.wait(5)
            return checkup.OK, "", ""
        self.use(fake("fast", answers(checkup.OK, "работает")), fake("slow", slow), fake("broken", broken), fake("hung", hung, timeout=0.6))
        started = time.time()
        results = checkup.run()
        self.assertLess(time.time() - started, 3)   # together, and the hung one isn't waited for past its own time (5 s)
        self.assertEqual([r["id"] for r in results], ["fast", "slow", "broken", "hung"])
        by = {r["id"]: r for r in results}
        self.assertEqual(by["fast"]["status"], checkup.OK)
        self.assertIn("fast", seen["saved"])   # the quick result was on the panel while the slow one still ran
        self.assertEqual((by["broken"]["status"], by["broken"]["summary"]), (checkup.FAIL, "диск не читается"))
        self.assertTrue(by["broken"]["fix"])
        self.assertEqual(by["hung"]["status"], checkup.FAIL)
        self.assertIn("не ответило", by["hung"]["summary"])
        self.assertEqual({r["id"] for r in checkup.last()["results"]}, {"fast", "slow", "broken", "hung"})
        self.assertIsNone(checkup.running())

    def test_a_second_check_does_not_start_while_one_runs(self):
        go = threading.Event()
        self.addCleanup(go.set)

        def waits(spend):
            go.wait(5)
            return checkup.OK, "работает", ""
        self.use(fake("wait", waits))
        first = threading.Thread(target=checkup.run)
        first.start()
        for _ in range(200):
            if checkup.running():
                break
            time.sleep(0.01)
        self.assertEqual(checkup.running()["total"], 1)
        with self.assertRaises(RuntimeError):
            checkup.run()
        self.assertIn("уже идёт", checkup.system_check())
        self.assertIn("уже идёт", panels._check(""))
        panel = panels.checkup_panel()
        self.assertIn("Идёт проверка: готово 0 из 1", str(panel))
        self.assertFalse([a for b in panel["blocks"] if b["type"] in ("items", "buttons") for i in b["items"] for a in i.get("actions", [i])
                          if a.get("id", "").startswith("check")])   # no button starts a second one
        go.set()
        first.join(5)
        self.assertIsNone(checkup.running())

    def test_the_tool_tells_what_is_broken_first_and_what_to_do(self):
        self.use(fake("a", answers(checkup.OK, "работает")), fake("b", answers(checkup.FAIL, "не отвечает", "Перезапусти")),
                 fake("c", answers(checkup.WARN, "медленно", "Проверь Wi-Fi")), fake("d", answers(checkup.OFF, "не подключено")))
        answer = checkup.system_check()
        self.assertTrue(answer.startswith("Проверено частей: 4. Работает 1, с оговоркой 1, не работает 1."), answer)
        self.assertLess(answer.index("Не работает:"), answer.index("С оговоркой:"))
        self.assertIn("- Часть b: не отвечает. Что сделать: Перезапусти", answer)
        self.assertIn("Выключено или не подключено: Часть d (не подключено)", answer)
        self.assertIn("Такой части в проверке нет", checkup.system_check("холодильник"))

    def test_a_paid_check_is_billed_to_the_task_that_asked(self):
        from sena.core import status
        billed = []
        self.use(fake("a", lambda spend: billed.append(costs._context()["tid"]) or (checkup.OK, "работает", "")))
        status.begin("проверь систему")
        try:
            checkup.system_check()
            self.assertEqual(billed, [status.current()["id"]])   # the check's thread works for the asking task
        finally:
            status.end("")


class Night(Fakes):
    def test_a_new_failure_is_told_once_quietly_then_its_fix(self):
        state = {"b": checkup.FAIL}
        self.use(fake("a", answers(checkup.OK, "работает")), fake("b", lambda spend: (state["b"], "не отвечает", "Перезапусти")))
        sent = []
        with mock.patch.object(checkup, "RECHECK_PAUSE", 0), mock.patch.object(checkup.api, "send_text", side_effect=lambda text, quiet=False: sent.append((text, quiet))):
            first = checkup.night()
            self.assertIn("перестало работать", first)
            self.assertIn("- Часть b: не отвечает. Что сделать: Перезапусти", first)
            self.assertEqual(sent, [(first, True)])   # one message, without a sound: it waits for the morning
            self.assertEqual(checkup.night(), "")    # still broken: not told again
            state["b"] = checkup.OK
            fixed = checkup.night()
        self.assertIn("снова работает — Часть b", fixed)
        self.assertEqual(len(sent), 2)

    def test_a_blip_is_tried_again_and_not_told(self):
        tries = []

        def flaky(spend):
            tries.append(spend)
            return (checkup.FAIL, "нет связи", "Проверь интернет") if len(tries) == 1 else (checkup.OK, "работает", "")
        self.use(fake("flaky", flaky))
        with mock.patch.object(checkup, "RECHECK_PAUSE", 0), mock.patch.object(checkup.api, "send_text") as send:
            self.assertEqual(checkup.night(), "")
        send.assert_not_called()
        self.assertEqual(len(tries), 2)
        self.assertEqual(checkup.last()["results"][0]["status"], checkup.OK)


class Panel(Fakes):
    def test_one_part_is_checked_by_its_button_and_the_panel_comes_back_fresh(self):
        self.use(fake("a", answers(checkup.OK, "работает")), fake("b", answers(checkup.FAIL, "не отвечает", "Перезапусти"), group="Связь"))
        out = desk.act("panel_act", {"panel": "checkup", "action": "check_one", "args": {"id": "b"}})
        self.assertEqual(out["result"], "Проверка закончена")
        items = {i["id"]: i for b in out["panel"]["blocks"] if b["type"] == "items" for i in b["items"]}
        self.assertEqual(items["b"]["tone"], "bad")
        self.assertIn("не отвечает · Что сделать: Перезапусти", items["b"]["subtitle"])
        self.assertEqual(items["a"]["subtitle"], "ещё не проверялось")   # only the part asked for was tried
        self.assertEqual([a["id"] for a in items["a"]["actions"]], ["check_one"])
        badge = next(i["badge"] for i in panels.listing()["items"] if i["id"] == "checkup")
        self.assertEqual(badge, 1)   # the menu shows how many parts don't work
        everything = desk.act("panel_act", {"panel": "checkup", "action": "check_all", "args": {}})
        stats = next(b for b in everything["panel"]["blocks"] if b["type"] == "stats")["items"]
        self.assertEqual([s["value"] for s in stats[:3]], ["1", "0", "1"])


class Parts(unittest.TestCase):
    """The real list: every part has its place, and the owner's words find it."""
    def test_every_part_has_its_place(self):
        self.assertEqual(len({c.id for c in checkup.CHECKS}), len(checkup.CHECKS))
        self.assertEqual({c.group for c in checkup.CHECKS}, set(checkup.GROUPS))
        self.assertEqual(set(checkup.BY_ID), {c.id for c in checkup.CHECKS})

    def test_a_part_is_named_by_id_group_or_words(self):
        def ids(part):
            return [c.id for c in checkup.pick(part)]
        self.assertEqual(len(ids("")), len(checkup.CHECKS))
        self.assertEqual(ids("всё"), ids(""))
        self.assertEqual(ids("google"), ["google"])
        self.assertEqual(set(ids("телеграм")), {"telegram_bot", "telegram_account"})
        self.assertEqual(ids("работает ли мозг"), ["claude"])
        self.assertEqual(set(ids("помощники")), {"browser", "computer", "code"})
        self.assertEqual(ids("холодильник"), [])

    def test_the_parts_at_home_are_tried_for_real(self):
        store.clear(checkup.REPORT)
        here = ("core", "disk", "databases", "backups", "keys", "budget", "errors", "secrets", "exam", "listening", "reading", "vault")
        for r in checkup._run([checkup.BY_ID[i] for i in here], spend=False):
            self.assertIn(r["status"], checkup.WORDS, r)
            self.assertTrue(r["summary"], r)
            if r["status"] in (checkup.FAIL, checkup.WARN):
                self.assertTrue(r["fix"], r)   # what doesn't work always comes with what to do


class SwitchedOff(unittest.TestCase):
    """The screen without the core: a check spends nothing, starts no program, and says what waits for Sena."""
    def setUp(self):
        power.set_off()
        self.addCleanup(setattr, power, "_off", False)
        store.clear(checkup.REPORT)

    def test_nothing_is_spent_and_no_program_starts(self):
        never = mock.Mock(side_effect=AssertionError("выключенная Сена не тратит и не запускает программ"))
        ready = dict.fromkeys(local.installed(), True)
        with mock.patch.dict(config.CLAUDE_KEYS, {"brain": "test-key", "agents": "test-key-2"}), mock.patch.object(config, "OPENAI_API_KEY", "test-key"), \
                mock.patch.object(llm_claude, "ping", return_value=[config.MODEL_MAIN, config.MODEL_FAST, config.MODEL_VOICE, llm_claude.CHEAPEST]), \
                mock.patch.object(llm, "ping", return_value=[]), \
                mock.patch.object(llm, "claude_probe", never), mock.patch.object(llm, "embeddings", never), mock.patch.object(llm, "respond", never), \
                mock.patch.object(local, "installed", return_value=ready), mock.patch.object(local, "embed", never), mock.patch.object(local, "chat", never), \
                mock.patch.object(local, "transcribe", never), mock.patch.object(local, "speak", never), mock.patch.object(checkup.subprocess, "run", never), \
                mock.patch.object(checkup, "_flag", return_value=True):
            parts = ("core", "claude", "openai", "home_models", "hearing", "voice", "code")
            results = {r["id"]: r for r in checkup._run([checkup.BY_ID[i] for i in parts], spend=True)}
        never.assert_not_called()
        self.assertEqual(results["core"]["status"], checkup.OFF)
        self.assertEqual(results["claude"]["status"], checkup.OK, results["claude"])
        self.assertIn("баланс проверю, когда Сена включена", results["claude"]["summary"])
        self.assertEqual(results["openai"]["status"], checkup.OFF)   # nothing runs on OpenAI any more
        self.assertIn("не используется", results["openai"]["summary"])
        for part in ("home_models", "hearing", "voice", "code"):
            self.assertEqual(results[part]["status"], checkup.WAIT, part)
        self.assertIn("Сена выключена: проверяется только бесплатное", str(panels.checkup_panel()))


class Paid(unittest.TestCase):
    """Where only a paid call can tell (an empty balance shows on nothing else), it costs less than a hundredth of a cent."""
    def test_the_claude_balance_is_seen_for_less_than_a_hundredth_of_a_cent(self):
        usage = SimpleNamespace(input_tokens=8, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0, server_tool_use=None)
        message = SimpleNamespace(id="msg_1", model=llm_claude.CHEAPEST, content=[SimpleNamespace(type="text", text="1")], stop_reason="max_tokens", usage=usage)
        asked = []
        listed = [SimpleNamespace(id=m) for m in (config.MODEL_MAIN, config.MODEL_FAST, config.MODEL_VOICE, llm_claude.CHEAPEST)]
        client = SimpleNamespace(models=SimpleNamespace(list=lambda limit: listed), messages=SimpleNamespace(create=lambda **kw: asked.append(kw) or message))
        before = costs.spent_today()
        with mock.patch.dict(config.CLAUDE_KEYS, {"brain": "test-key", "agents": "test-key-2"}), mock.patch.object(llm_claude, "_client", return_value=client):
            status, summary, fix = checkup._claude(True)
        self.assertEqual(status, checkup.OK, summary)
        self.assertEqual([(a["model"], a["max_tokens"]) for a in asked], [(llm_claude.CHEAPEST, 1)] * 2)   # the brain's key and the helpers'
        spent = costs.spent_today() - before
        self.assertGreater(spent, 0)                    # paid calls, and they're in the cost journal
        self.assertLess(spent / len(asked), 0.0001)     # each less than a hundredth of a cent
        with mock.patch.dict(config.CLAUDE_KEYS, {"brain": "test-key", "agents": ""}), mock.patch.object(llm_claude, "_client", return_value=client):
            status, summary, fix = checkup._claude(False)
        self.assertEqual(status, checkup.WARN)          # helpers on the brain's key: works, but their spending isn't seen apart
        self.assertIn("SENA_CLAUDE_AGENTS_KEY", fix)

    def test_an_empty_balance_reads_as_what_to_do(self):
        class Billing(Exception):
            __module__ = "anthropic._exceptions"

        class Quota(Exception):
            __module__ = "openai._exceptions"
        with mock.patch.dict(config.CLAUDE_KEYS, {"brain": "test-key", "agents": "test-key-2"}), mock.patch.object(config, "MODEL_FAST", "gpt-6-luna"), \
                mock.patch.object(llm_claude, "ping", return_value=[config.MODEL_MAIN, config.MODEL_VOICE]), \
                mock.patch.object(llm, "claude_probe", side_effect=Billing("Your credit balance is too low to access the Anthropic API")), \
                mock.patch.object(config, "OPENAI_API_KEY", "test-key"), mock.patch.object(llm, "ping", return_value=[]), \
                mock.patch.object(llm, "embeddings", side_effect=Quota("Error code: 429 - insufficient_quota")):
            rows = {r["id"]: r for r in checkup._run([checkup.BY_ID["claude"], checkup.BY_ID["openai"]], spend=True)}
        self.assertEqual((rows["claude"]["status"], rows["claude"]["summary"]), (checkup.FAIL, "на счёте Claude кончились деньги"))
        self.assertIn("console.anthropic.com", rows["claude"]["fix"])
        self.assertEqual((rows["openai"]["status"], rows["openai"]["summary"]), (checkup.FAIL, "на счёте OpenAI кончились деньги"))
        self.assertIn("platform.openai.com", rows["openai"]["fix"])


class Verdicts(unittest.TestCase):
    """A part's own words: what the service answered becomes works / with a caveat / doesn't."""
    def test_the_daily_limit(self):
        self.addCleanup(settings.update, {"budget_usd_day": 0})
        settings.update({"budget_usd_day": 1.0})
        for spent, status in ((0.5, checkup.OK), (0.85, checkup.WARN), (1.2, checkup.FAIL)):
            with mock.patch.object(costs, "spent_today", return_value=spent):
                self.assertEqual(checkup._budget(True)[0], status, spent)

    def test_errors_of_the_day_are_counted_by_place(self):
        rows = [{"text": "Слух, проверка тишины: x", "place": "Слух, проверка тишины"}] * 2 + [{"text": "Фоновая проверка: чаты: y", "place": "Фоновая проверка: чаты"},
                                                                                         {"text": "Проверка системы: Интернет: z", "place": "Проверка системы: Интернет"}]
        with mock.patch.object(checkup.events, "search", return_value=rows):
            status, summary, fix = checkup._errors(True)
        self.assertEqual(status, checkup.WARN)
        self.assertEqual(summary, "ошибок 3; чаще всего — Слух, проверка тишины (2), Фоновая проверка (1)")   # a failed check is its own line already
        with mock.patch.object(checkup.events, "search", return_value=[]):
            self.assertEqual(checkup._errors(True)[0], checkup.OK)

    def test_a_bot_with_a_webhook_does_not_hear_the_owner(self):
        replies = {"getMe": {"ok": True, "result": {"username": "sena_test_bot"}}, "getWebhookInfo": {"ok": True, "result": {"url": "https://example.com/hook"}}}
        with mock.patch.object(config, "TELEGRAM_TOKEN", "123:abc"), mock.patch.object(checkup.api, "post", side_effect=lambda method, data, **kw: replies[method]):
            status, summary, fix = checkup._telegram_bot(True)
        self.assertEqual(status, checkup.FAIL)
        self.assertIn("вебхук", summary)
        replies["getWebhookInfo"] = {"ok": True, "result": {"url": ""}}
        with mock.patch.object(config, "TELEGRAM_TOKEN", "123:abc"), mock.patch.object(checkup.api, "post", side_effect=lambda method, data, **kw: replies[method]):
            self.assertEqual(checkup._telegram_bot(True), (checkup.OK, "бот @sena_test_bot на связи", ""))

    def test_the_website_verdict_reads_the_uptime_words(self):
        page = [("https://site.example/", 200)]
        cases = (((page, 0.4, ""), (90, "R11"), checkup.OK), ((page, 0.4, ""), (5, "R11"), checkup.WARN),
                 (([("https://site.example/", 503)], 0.4, ""), (90, "R11"), checkup.FAIL), (([], 15.0, "не ответил за 15 с"), (None, "нет связи"), checkup.FAIL))
        for opened, cert, status in cases:
            with mock.patch.object(config, "SITE_PUBLIC", "site.example"), mock.patch.object(uptime, "_open", return_value=opened), \
                    mock.patch.object(uptime, "_cert", return_value=cert):
                self.assertEqual(checkup._website(True)[0], status, (opened, cert))


class Advice(unittest.TestCase):
    """Round 2: the advice sent him to the terminal for what Sena does herself (her QR code, her Google window) and to «пульт»,
    a section no app has; a kept Telegram sign-in that didn't connect at the start was never tried again; a lost Google sign-in
    was grey «off»; his voice, lost in the incident, was nowhere said to be unlearned."""

    def test_no_advice_names_the_terminal_for_a_sign_in_or_a_section_that_isnt_there(self):
        import inspect
        source = inspect.getsource(checkup)
        self.assertNotIn("пульт", source.lower())
        session = f"{config.TELEGRAM_SESSION}.session"
        with mock.patch.object(checkup, "_flag", return_value=True), mock.patch.object(power, "is_off", return_value=False):
            self.assertEqual(checkup._telegram_account(False)[2], checkup.SIGN_IN_TELEGRAM)   # no session at all
            open(session, "w").close()
            self.addCleanup(__import__("os").unlink, session)
            with mock.patch.object(checkup.account, "connected", return_value=False) as tried:
                status, summary, fix = checkup._telegram_account(False)
            tried.assert_called_once()   # the kept sign-in is tried now, not only looked at
            self.assertEqual((status, fix), (checkup.FAIL, checkup.SIGN_IN_TELEGRAM))
            with mock.patch.object(checkup.account, "connected", return_value=True):
                self.assertEqual(checkup._telegram_account(False)[0], checkup.OK)
            client = config.STATE_DIR / "google-client-probe.json"
            client.write_text("{}")
            self.addCleanup(client.unlink, missing_ok=True)
            for state in ("none", "expired"):
                with mock.patch.object(checkup.auth, "state", return_value=state), mock.patch.object(config, "GOOGLE_CLIENT_FILE", client):
                    status, summary, fix = checkup._google(False)
                self.assertEqual((status, fix), (checkup.FAIL, checkup.SIGN_IN_GOOGLE), state)   # set up and lost: a failure, told once
            with mock.patch.object(checkup.auth, "state", return_value="none"), mock.patch.object(config, "GOOGLE_CLIENT_FILE", client.with_name("none.json")):
                self.assertEqual(checkup._google(False)[0], checkup.OFF)   # never set up: off
        for words in (checkup.SIGN_IN_TELEGRAM, checkup.SIGN_IN_GOOGLE):
            self.assertNotIn("python -m", words)
            self.assertNotIn("перезапуст", words)

    def test_an_unlearned_voice_of_his_is_said(self):
        wake = config.STATE_DIR / "wake-probe.onnx"
        wake.write_bytes(b"x")
        self.addCleanup(wake.unlink, missing_ok=True)
        with mock.patch.object(checkup.importlib.util, "find_spec", return_value=object()), mock.patch.object(config, "WAKE_MODEL", wake), \
                mock.patch.object(config, "OWNER_VOICE_FILE", config.STATE_DIR / "no-voice-yet.json"):
            status, summary, fix = checkup._listening(False)
        self.assertEqual(status, checkup.WARN)
        self.assertIn("голос Темы ещё не выучен", summary)
        self.assertIn("голосовых", fix)


if __name__ == "__main__":
    unittest.main()
