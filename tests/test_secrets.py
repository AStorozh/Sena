"""The keeper of secrets: encrypted at rest, values never in the model's text or the journal, tickets for agents, confirmations for the owner."""
import json
import sqlite3
import threading
import time
import unittest
from unittest import mock

from sena import config
from sena.core import confirm, events, sandbox, secrets, status
from sena.core.tools import Failure
from sena.memory import facts
from sena.telegram import account


class Keeper(unittest.TestCase):
    def setUp(self):
        secrets._reset()
        config.SECRETS_FILE.unlink(missing_ok=True)
        config.SECRETS_KEY.unlink(missing_ok=True)
        confirm.pending.clear()

    def test_values_are_kept_encrypted_and_history_is_never_destroyed(self):
        self.assertIn("Имя секрета", secrets.put("", "hunter2hunter"))
        self.assertIn("Слишком коротко", secrets.put("почта", "short"))
        self.assertEqual(secrets.put("почта", "hunter2hunter", note="пароль от Gmail", allow=["ag_mac"]), "Секрет «почта» сохранён")
        raw = config.SECRETS_FILE.read_bytes()
        self.assertNotIn(b"hunter2hunter", raw)   # nothing readable on disk
        self.assertEqual(oct(config.SECRETS_KEY.stat().st_mode)[-3:], "600")
        self.assertEqual(secrets.put("почта", "hunter3hunter3", allow=["ag_mac", "ag_web"]), "Секрет «почта» обновлён")
        secrets._reset()   # reread from disk: the key and the file agree
        row = secrets.names()[0]
        self.assertEqual((row["name"], row["note"], row["allow"], row["changed"]), ("почта", "пароль от Gmail", ["ag_mac", "ag_web"], 1))
        self.assertNotIn("value", row)
        self.assertEqual(secrets.remove("почта"), "Секрет «почта» убран")
        self.assertEqual(secrets.names(), [])
        self.assertTrue(any(k.startswith("почта@") for k in secrets._load()["gone"]))   # taken out of use, not destroyed
        self.assertIn("нет", secrets.remove("почта"))

    def test_agents_get_tickets_not_values_and_every_access_is_journaled(self):
        secrets.put("сервер", "very-secret-pass", allow=["ag_mac"])
        self.assertIsNone(secrets.use("сервер", "ag_web", "войти"))   # not allowed: refused and journaled
        token = secrets.use("сервер", "ag_mac", "перезапустить цикл")
        self.assertEqual(secrets.resolve(token), "very-secret-pass")
        self.assertIsNone(secrets.resolve("nope"))
        self.assertIn("ag_web", secrets.grant("сервер", "ag_web"))
        self.assertTrue(secrets.allowed("сервер", "ag_web"))
        rows = events.search("all", kind="secret")
        self.assertGreaterEqual(len(rows), 4)
        self.assertFalse(any("very-secret-pass" in json.dumps(r, ensure_ascii=False) for r in rows))

    def test_a_value_is_wiped_out_of_the_journal_output_and_errors(self):
        secrets.put("ключ", "zx9!KeepMeHidden", allow=[])
        events.log("tool", "вывод: zx9!KeepMeHidden и ещё", name="run_command", args={"command": "echo zx9!KeepMeHidden"})
        db = sqlite3.connect(config.EVENTS_DB)
        try:
            stored = db.execute("SELECT text, data FROM events ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            db.close()
        self.assertNotIn("zx9!KeepMeHidden", stored[0] + stored[1])
        self.assertIn("[секрет:ключ]", stored[0])
        self.assertIn("[секрет:ключ]", sandbox.run("print('pw=zx9!KeepMeHidden')", {}, "тест"))   # printed by an agent's code: masked
        self.assertEqual(secrets.mask("без секретов"), "без секретов")

    def test_a_value_hidden_in_a_link_or_reversed_is_masked_too(self):
        """promptfoo's leak forms: a password with # or + inside a link is URL-encoded; a reversed one is the same secret."""
        secrets.put("вайфай", "Fox+Den#2026", allow=[])
        self.assertIn("[секрет:вайфай]", secrets.mask("https://x.example/?p=Fox%2BDen%232026"))
        self.assertIn("[секрет:вайфай]", secrets.mask("6202#neD+xoF"))

    def test_unticking_the_last_agent_takes_the_access_away(self):
        secrets.put("сервер", "very-secret-pass", allow=["ag_mac"])
        secrets.put("сервер", "", note="только заметка")   # allow not given: the agents stay
        self.assertTrue(secrets.allowed("сервер", "ag_mac"))
        secrets.put("сервер", "", allow=[])   # the app sent an empty list: no agent may use it now
        self.assertFalse(secrets.allowed("сервер", "ag_mac"))
        self.assertIsInstance(secrets.put("сервер", "short"), Failure)   # a refusal is a failure, not "saved"

    def test_sena_keys_are_masked_even_when_the_store_cant_be_opened(self):
        secrets.put("почта", "hunter2hunter")
        config.SECRETS_FILE.write_bytes(b"broken" * 10)   # damaged on disk
        secrets._reset()
        with mock.patch.object(config, "secret_values", return_value=[("sk-own-key-123456", "OPENAI_API_KEY")]):
            self.assertEqual(secrets.mask("ключ sk-own-key-123456"), "ключ [секрет:OPENAI_API_KEY]")
        config.SECRETS_FILE.unlink()
        secrets._reset()

    def test_a_value_escaped_inside_json_is_masked_too(self):
        secrets.put("сайт", 'pa"ss\\word-2026')
        line = json.dumps({"command": 'login pa"ss\\word-2026'})
        self.assertNotIn("word-2026", secrets.mask(line))

    def test_reveal_only_through_a_confirmed_path_and_never_when_marked_so(self):
        secrets.put("банк", "bank-pass-123", reveal="never")
        self.assertIsNone(secrets.reveal("банк", "пульт"))
        secrets.put("банк", "", reveal="confirm")   # only the rule changes, the value stays
        self.assertEqual(secrets.reveal("банк", "пульт"), "bank-pass-123")
        self.assertTrue(events.search("all", "показан владельцу", kind="secret"))

    def test_memory_refuses_passwords(self):
        self.assertIn("в память не пишу", facts.remember("Обо мне", "пароль от почты: hunter2hunter"))
        self.assertIn("в память не пишу", facts.remember("Обо мне", "api key для сайта abcdef123456"))
        self.assertIn("Запомнила", facts.remember("Обо мне", "любит пароли придумывать сам"))   # the word without a value is just a sentence

    def test_telegram_code_has_no_doors_to_the_process(self):
        self.assertIn("недоступен", account.telegram_code("import os\nprint(os.environ)"))
        self.assertIn("недоступно", account.telegram_code("print(open('/etc/hosts').read())"))
        self.assertIn("2026", account.telegram_code("import datetime\nprint(datetime.date(2026, 1, 1).year)"))


class Confirmations(unittest.TestCase):
    def setUp(self):
        confirm.pending.clear()
        status.local.turn = 1

    def test_only_on_the_next_turn_or_by_the_owners_click(self):
        done = []
        sid = confirm.hold("тест", "текст", lambda: done.append(1) or "сделано")
        self.assertEqual(confirm.confirm(sid)[1], "Нельзя подтвердить в том же ходе: сначала спроси Тему и дождись его ответа")
        status.local.turn, status.local.said = 2, "да"
        ok, text, item = confirm.confirm(sid)
        self.assertEqual((ok, text, item["name"], done), (True, "сделано", "тест", [1]))
        self.assertFalse(confirm.confirm(sid)[0])   # gone after use
        sid2 = confirm.hold("второе", "текст", lambda: "ок")
        status.local.turn = 5
        self.assertIn("Прошёл ход", confirm.confirm(sid2)[1])
        sid3 = confirm.hold("третье", "текст", lambda: "с пульта")
        self.assertEqual([w["id"] for w in confirm.waiting()], [sid3])
        self.assertEqual(confirm.confirm_now(sid3), "с пульта")
        self.assertEqual(confirm.cancel(sid3), "Такой отправки уже нет")


class Earlier(unittest.TestCase):
    def setUp(self):
        secrets._reset()
        config.SECRETS_FILE.unlink(missing_ok=True)
        config.SECRETS_KEY.unlink(missing_ok=True)

    def test_an_earlier_or_removed_value_is_masked_too(self):
        """The n8n study: old values stayed in the keeper's history and on its removed shelf, but were never wiped from texts."""
        secrets.put("почта", "first-Pass-2025")
        secrets.put("почта", "second-Pass-2026")
        secrets.put("старый", "gone-Value-777")
        secrets.remove("старый")
        masked = secrets.mask("пароли: first-Pass-2025, second-Pass-2026, gone-Value-777")
        self.assertNotIn("first-Pass-2025", masked)
        self.assertNotIn("gone-Value-777", masked)
        self.assertIn("[секрет:почта]", masked)
        self.assertIn("[секрет:старый]", masked)


class Cards(unittest.TestCase):
    """LangChain's PII rule on this Mac: an unknown card number went to the correspondence, the search and the journal whole."""
    CARD = "4276160000000001"   # passes Luhn's check

    def test_a_card_number_is_closed_and_other_long_numbers_are_left_alone(self):
        grouped = " ".join(self.CARD[i:i + 4] for i in range(0, 16, 4))
        self.assertEqual(secrets.cards(f"моя карта {grouped}, оплати"), "моя карта [карта …0001], оплати")
        self.assertEqual(secrets.cards("Мир 2200123456789019"), "Мир [карта …9019]")
        self.assertIn("[карта …0005]", secrets.cards("amex 3782 822463 10005"))
        for text in ("номер 4111-1111-1111-1112", "штрихкод 4607001771234", "телефон +7 916 123 45 67", "сумма 4276160000000001,00",
                     "2024 2025 2026 2027", "заказ 1234567812345678"):
            self.assertEqual(secrets.cards(text), text)   # no check digit, another length or start, a sum, years
        self.assertEqual(secrets.mask(f"платёж с {self.CARD}"), "платёж с [карта …0001]")   # the journal and tool outputs too

    def test_the_coordinator_never_keeps_a_card_number_in_the_correspondence(self):
        from sena.agents.ag_sena import agent
        from sena.memory import history
        from tests.test_core import FakeModel, response
        from sena.core import llm
        was = llm.respond
        self.addCleanup(setattr, llm, "respond", was)
        llm.respond = FakeModel(response(text="Номер карты я в переписке не держу: положи её в «Секреты»."), response(text="нет"))
        agent.ask(301, f"запомни мою карту {self.CARD}")
        said = [m["content"] for m in history.messages(301) if m["role"] == "user"]
        self.assertIn("[карта …0001]", said[-1])
        self.assertNotIn(self.CARD, json.dumps(history.messages(301), ensure_ascii=False))


class Sites(unittest.TestCase):
    """Skyvern ties a credential to its site: a page that talks the agent into typing a password elsewhere gets nothing."""
    def setUp(self):
        secrets._reset()
        config.SECRETS_FILE.unlink(missing_ok=True)
        config.SECRETS_KEY.unlink(missing_ok=True)

    def test_a_password_bound_to_its_sites_is_typed_only_there(self):
        secrets.put("hh", "Very-Long-Pass-77", allow=["ag_web"], sites=["https://www.hh.ru/login", "mail.example"])
        self.assertEqual(secrets.names()[0]["sites"], ["hh.ru", "mail.example"])
        self.assertIsNone(secrets.off_site("hh", "https://spb.hh.ru/applicant"))   # a subdomain is its own site
        self.assertIn("только на hh.ru", secrets.off_site("hh", "https://hh.ru.evil.example/login"))
        self.assertEqual(secrets.name_of("Very-Long-Pass-77"), "hh")
        secrets.put("hh", "", note="без сайтов")   # sites not given: kept as they are
        self.assertEqual(secrets.names()[0]["sites"], ["hh.ru", "mail.example"])
        secrets.put("hh", "", sites=[])   # cleared: any site again
        self.assertIsNone(secrets.off_site("hh", "https://anything.example"))

    def test_the_browser_refuses_where_the_text_goes_in(self):
        from sena.agents.ag_web import guard, human
        secrets.put("hh", "Very-Long-Pass-77", allow=["ag_web"], sites=["hh.ru"])

        class Field:   # a locator: the page and the element's own document
            def __init__(self, url):
                self.page, self.url, self.typed = type("P", (), {"url": url})(), url, []

            def evaluate(self, script, timeout=None):
                return self.url

            def fill(self, value, **kw):
                self.typed.append(value)
        guard._wrap(Field, "fill", lambda self, value=None, *a, **k: guard._no_secret_elsewhere(self, value), with_self=True)   # as install() wraps Locator.fill
        good, bad = Field("https://hh.ru/account/login"), Field("https://evil.example/login")
        good.fill("Very-Long-Pass-77")
        with self.assertRaises(PermissionError):
            bad.fill("Very-Long-Pass-77")
        bad.fill("обычный текст поиска")   # an ordinary text goes anywhere
        self.assertEqual((good.typed, bad.typed), (["Very-Long-Pass-77"], ["обычный текст поиска"]))
        with self.assertRaises(PermissionError):
            human.human_type(type("Page", (), {"url": "https://evil.example/"})(), 3, "Very-Long-Pass-77")


class KeeperRound2(unittest.TestCase):
    """The audits of 26.10: a shorter password cut a longer one's value, an emptied note came back, a Keychain that couldn't be
    read was asked for every journal line, and the key went to `security` on its command line."""

    def setUp(self):
        secrets._reset()
        config.SECRETS_FILE.unlink(missing_ok=True)
        config.SECRETS_KEY.unlink(missing_ok=True)
        self.addCleanup(secrets._reset)

    def test_a_password_that_starts_another_one_leaves_nothing_of_it(self):
        secrets.put("почта", "Tema2024pass")
        secrets.put("банк", "Tema2024pass!Bank")
        masked = secrets.mask("пароль банка: Tema2024pass!Bank, почта: Tema2024pass")
        self.assertEqual(masked, "пароль банка: [секрет:банк], почта: [секрет:почта]")
        self.assertNotIn("!Bank", masked)

    def test_an_emptied_note_is_cleared_and_a_missing_one_kept(self):
        secrets.put("сервер", "very-secret-pass", note="ключ от VPS")
        secrets.put("сервер", "", note=None)   # the field wasn't sent: kept
        self.assertEqual(secrets.names()[0]["note"], "ключ от VPS")
        secrets.put("сервер", "", note="")   # the owner emptied it in the app
        self.assertEqual(secrets.names()[0]["note"], "")
        secrets.put("сервер", "", allow=[])   # nothing about the note at all: still empty, not the old one back
        self.assertEqual(secrets.names()[0]["note"], "")

    def test_a_keychain_that_cant_be_read_is_asked_once_not_for_every_line(self):
        from sena.core import keychain
        secrets.put("почта", "hunter2hunter")   # the store exists; its key moved to the Keychain long ago
        key = config.SECRETS_KEY.read_bytes()
        config.SECRETS_KEY.unlink()
        self.addCleanup(lambda: config.SECRETS_KEY.write_bytes(key))
        secrets._reset()
        refusing = mock.Mock(side_effect=keychain.Unavailable("Связка ключей не открылась (код 51)"))
        with mock.patch.object(keychain, "enabled", return_value=True), mock.patch.object(keychain, "get", refusing), \
                mock.patch.object(keychain, "put", mock.Mock(return_value=True)) as put:
            for n in range(10):
                events.log("tool", f"строка журнала номер {n} с длинным текстом", args={"command": "echo длинная команда"})
            print("строка вывода, которую маскирует охрана вывода")
            self.assertEqual(refusing.call_count, 1)   # once, not per journal line, field and print
            with self.assertRaises(keychain.Unavailable):
                secrets.names()   # what the owner asks for himself tries again and says why it can't
            self.assertEqual(refusing.call_count, 2)
            put.assert_not_called()   # and no new key over the store, as before
        self.assertTrue(events.search("all", "Хранилище секретов не открылось", kind="error"))

    def test_the_key_goes_to_security_on_its_input_not_its_command_line(self):
        import shlex
        import subprocess
        from sena.core import keychain
        stored, calls = {}, []

        def run(argv, input=None, **kw):   # what `security` does with it, never the real one
            calls.append((list(argv), input))
            if argv[1:] == ["-i"]:
                words = shlex.split(input)   # its interactive mode reads a line as a shell would
                stored[words[words.index("-s") + 1]] = words[words.index("-w") + 1]
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[1] == "find-generic-password":
                service = argv[argv.index("-s") + 1]
                return subprocess.CompletedProcess(argv, 0 if service in stored else 44, stored.get(service, ""), "")
            stored[argv[argv.index("-s") + 1]] = argv[argv.index("-w") + 1]   # the old way, by the command line
            return subprocess.CompletedProcess(argv, 0, "", "")
        value = "ab" * 32
        with mock.patch.object(config, "KEYCHAIN", True), mock.patch.object(keychain.subprocess, "run", run):
            self.assertTrue(keychain.put(keychain.SECRETS, value))
        self.assertEqual(stored, {keychain.SECRETS: value})
        self.assertFalse(any(value in " ".join(argv) for argv, _ in calls))   # never in a process's arguments (ps)
        self.assertIn(value, calls[0][1])

        def deaf(argv, input=None, **kw):   # an interactive mode that took nothing: the old way still keeps the key
            return subprocess.CompletedProcess(argv, 0, "", "") if argv[1:] == ["-i"] else run(argv, input, **kw)
        stored.clear()
        with mock.patch.object(config, "KEYCHAIN", True), mock.patch.object(keychain.subprocess, "run", deaf):
            self.assertTrue(keychain.put(keychain.COPIES, value))
        self.assertEqual(stored, {keychain.COPIES: value})
        self.assertTrue(events.search("all", "security -i", kind="error"))


class PressesAndNo(unittest.TestCase):
    """The audits of 26.10: an errand confirmed by a button ran in no task (not in «Задачи», out of «Стоп»'s reach, no money
    brake); a press that reached nobody promised "«Да» ещё раз" that words couldn't give; a typed «нет» couldn't drop anything."""

    def setUp(self):
        confirm.pending.clear()
        status.local.turn, status.local.said = 1, None
        self.addCleanup(setattr, status.local, "said", None)

    def press(self, sid):
        out = {}
        worker = threading.Thread(target=lambda: out.update(result=confirm.confirm_now(sid)))   # as Telegram and the apps press: a thread of its own
        worker.start()
        return worker, out

    def test_a_pressed_errand_is_a_task_of_its_own_that_stop_reaches_and_costs_count_for(self):
        from sena.core import cancel, costs
        seen, finished = {}, []

        def errand():
            seen.update(task=dict(status.current() or {}), listed=[t["text"] for t in status.tasks.values()])
            costs.record("anthropic", "messages", "claude-haiku-5-5", {"input": 100_000})
            seen["spent"] = costs.task_spent(status.current()["id"])
            for _ in range(250):
                cancel.check_stop()
                time.sleep(0.02)
            return "сделано"
        sid = confirm.hold("поручение компьютеру", "почисти загрузки", errand)
        with mock.patch.object(confirm, "finished_hook", finished.append):
            worker, out = self.press(sid)
            for _ in range(200):
                if seen:
                    break
                time.sleep(0.01)
            self.assertTrue(seen["task"]["text"].startswith("По кнопке «Да»: поручение компьютеру"))
            self.assertIn(seen["task"]["text"], seen["listed"])   # in «Задачи» while it runs
            self.assertGreater(seen["spent"], 0)   # its costs are its own: the task's money brake sees them
            self.assertEqual(cancel.stop_task(seen["task"]["id"]), "Задача остановлена")   # «Стоп» from the apps reaches it
            worker.join(5)
        self.assertEqual(out["result"], "Остановлено по просьбе Темы")
        self.assertEqual([t["text"] for t in finished], [seen["task"]["text"]])   # handed to the task journal
        self.assertFalse(any(t["id"] == seen["task"]["id"] for t in status.tasks.values()))

    def test_a_press_that_reached_nobody_waits_for_his_typed_yes_too(self):
        tries = []

        def send():
            tries.append(1)
            if len(tries) == 1:
                raise ConnectionError("Connection refused")
            return "Отправлено"
        sid = confirm.hold("сообщение маме", "задержусь", send)
        worker, out = self.press(sid)
        worker.join(5)
        self.assertIn("Ничего не ушло", out["result"])
        status.local.turn, status.local.said = 9, "да"   # a later message of his, not the very next turn
        ok, text, _ = confirm.confirm(sid)
        self.assertEqual((ok, text, len(tries)), (True, "Отправлено", 2))

    def test_google_not_signed_in_reached_nobody(self):
        from sena.google import auth
        self.assertTrue(confirm._not_delivered(auth.NotSignedIn(auth.HOW_TO_SIGN_IN)))
        self.assertTrue(confirm._not_delivered(Failure("Фильтр не создан: у входа в Google нет права на настройки почты. " + auth.HOW_TO_SIGN_IN)))

    def test_a_typed_no_drops_the_action_only_on_his_own_words(self):
        sid = confirm.hold("сообщение маме", "задержусь", lambda: "Отправлено")
        status.local.turn = 2
        self.assertIsInstance(confirm.decline(sid), Failure)   # a background turn or someone else's voice: no
        self.assertIn(sid, [w["id"] for w in confirm.waiting()])
        status.local.said = "нет, не надо"
        self.assertEqual(confirm.decline(sid), "Отменено: сообщение маме")
        self.assertEqual(confirm.waiting(), [])
        self.assertIsInstance(confirm.decline(sid), Failure)


if __name__ == "__main__":
    unittest.main()
