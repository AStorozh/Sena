"""The home and the cloud: models on this Mac (core/local.py) and who does what (core/roles.py).
No model is started here: the server's door is the test stub (tests/__init__.py) or a patch; reading documents and photos
uses the real macOS Vision and PDFKit (fast, local, deterministic) and is skipped where they are missing."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from sena import config
from sena.agents import ag_ears, ag_night
from sena.core import costs, local, roles, settings, store
from sena.memory import facts, files, index, people


def _reset_roles():
    settings.update({"roles": {}})


class Gateway(unittest.TestCase):
    def test_presets_name_only_the_models_that_are_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(config, "MODELS_DIR", Path(tmp)):
                self.assertNotIn("[", local._presets())   # nothing downloaded: no model to load
                (Path(tmp) / config.LOCAL_EMBED).write_bytes(b"x")
                text = local._presets()
                self.assertIn("[embed]", text)
                self.assertIn("embeddings = true", text)
                self.assertIn(f"sleep-idle-seconds = {config.LOCAL_IDLE_SEC}", text)   # an idle model gives the memory back
                self.assertNotIn("[chat]", text)
                (Path(tmp) / config.LOCAL_CHAT).write_bytes(b"x")
                (Path(tmp) / config.LOCAL_VISION).write_bytes(b"x")
                text = local._presets()
                self.assertIn("mmproj = ", text)   # one model that also sees, not two copies
                self.assertIn("cache-ram = 0", text.split("[chat]")[0])   # fingerprints keep no past prompts in memory
                self.assertIn("cache-ram = 512", text.split("[chat]")[1])   # and the chat model at most half a gigabyte, not 8

    def test_a_looping_answer_is_not_given_more_room_and_a_broken_fingerprint_is_refused(self):
        limits = []

        def door(path, body, timeout=300):
            limits.append(body["max_tokens"])
            return {"choices": [{"message": {"content": '{"text": "' + "да, " * 200}, "finish_reason": "length"}], "usage": {}}
        with mock.patch.object(local, "_post", door), self.assertRaisesRegex(local.Unavailable, "зациклилась"):
            local.chat("инструкция", "текст", schema={"type": "object"}, max_tokens=300)
        self.assertEqual(limits, [300])
        self.assertFalse(local._looping('{"facts": [{"text": "у Темы дочь 7 лет"}, {"text": "живёт на Бали"'))
        broken = lambda path, body, timeout=300: {"data": [{"index": 0, "embedding": [float("nan")] * 768}], "usage": {}}   # noqa: E731
        with mock.patch.object(local, "_post", broken), self.assertRaises(local.Unavailable):
            local.embed(["текст"])

    def test_fingerprints_are_cut_to_the_width_normalized_and_prefixed(self):
        sent = {}

        def door(path, body, timeout=300):
            sent.update(body)
            return {"data": [{"index": i, "embedding": [3.0] * 768} for i in range(len(body["input"]))], "usage": {"prompt_tokens": 5}}
        with mock.patch.object(local, "_post", door):
            vecs = local.embed(["купить молоко"], query=True)
        self.assertEqual(len(vecs[0]), config.EMBED_DIMS)
        self.assertAlmostEqual(float(np.linalg.norm(vecs[0])), 1.0, places=5)
        self.assertEqual(sent["input"], [config.LOCAL_EMBED_PROMPTS[0] + "купить молоко"])   # a question gets the question prefix
        self.assertEqual(local.stamp(), f"{config.LOCAL_EMBED}/{config.EMBED_DIMS}")

    def test_a_json_answer_comes_back_as_data_and_broken_json_is_an_error(self):
        reply = lambda content: (lambda path, body, timeout=300: {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 9, "completion_tokens": 3}})
        with mock.patch.object(local, "_post", reply('{"notify": true, "text": "Мама звонила"}')):
            self.assertEqual(local.chat("инструкция", "текст", schema={"type": "object"}), {"notify": True, "text": "Мама звонила"})
        with mock.patch.object(local, "_post", reply("не json")):
            with self.assertRaises(local.Unavailable):
                local.chat("инструкция", "текст", schema={"type": "object"})
        with mock.patch.object(config, "LOCAL_VISION", "нет-такого-файла.gguf"):
            with self.assertRaises(local.Unavailable):
                local.chat("что на фото", image="/nowhere.png")

    def test_home_calls_are_free_and_counted_apart_from_paid_ones(self):
        before = costs.summary("today")["home"]["calls"]
        local.embed(["проверка учёта"])
        data = costs.summary("today")
        self.assertEqual(data["home"]["calls"], before + 1)
        self.assertGreaterEqual(data["home"]["by_service"].get("embeddings", 0), 1)
        self.assertNotIn("local", [p for p, _ in data["by_provider"]])   # never mixed into the money
        self.assertIn("Дома, на этом Mac", costs.render(data))

    def test_hearing_without_whisper_says_why(self):
        with self.assertRaises(local.Unavailable):
            local.transcribe("/nowhere.wav")
        self.assertEqual(local.clean("Субтитры сделал DimaTorzok"), "")
        self.assertEqual(local.clean("включи музыку погромче"), "включи музыку погромче")

    def test_the_owners_words_are_recognized_before_overheard_speech_waiting_for_its_turn(self):
        """The concurrency audit of 25.10: recognition was one plain lock — in a busy room the owner's command waited behind
        the accurate reading of every phrase overheard before it."""
        import threading
        import time
        turns, order = local._Turns(), []
        held, release = threading.Event(), threading.Event()

        def busy():
            with turns.turn(urgent=False):
                held.set()
                release.wait(5)

        def waiting(name, urgent):
            with turns.turn(urgent=urgent):
                order.append(name)
        first = threading.Thread(target=busy)
        first.start()
        held.wait(5)
        overheard = threading.Thread(target=waiting, args=("подслушанное", False))
        overheard.start()
        time.sleep(0.1)   # it waits for its turn
        owners = threading.Thread(target=waiting, args=("Тема", True))
        owners.start()
        for _ in range(100):
            if turns.urgent:
                break
            time.sleep(0.01)
        release.set()
        for t in (first, overheard, owners):
            t.join(5)
        self.assertEqual(order, ["Тема", "подслушанное"])

    def test_status_answers_without_a_server(self):
        with mock.patch.object(local, "_get", mock.Mock(side_effect=OSError("нет сервера"))):
            s = local.status()
        self.assertFalse(s["server"])
        self.assertIn("embed", s["installed"])


@unittest.skipIf(local._vision() is None or not shutil.which("/usr/sbin/cupsfilter"), "нет macOS Vision или cupsfilter")
class Reading(unittest.TestCase):
    """Documents and photos are read on the Mac: the text layer of a PDF, a scan by its picture, a transparent PNG on white."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        text = cls.dir / "schet.txt"
        text.write_text("Счёт на оплату номер 17\nБухгалтерские услуги за октябрь\nИтого к оплате: 12 000 рублей\n", encoding="utf-8")
        with open(cls.dir / "schet.pdf", "wb") as out:
            subprocess.run(["/usr/sbin/cupsfilter", str(text)], stdout=out, stderr=subprocess.DEVNULL, timeout=60)
        import Quartz
        from Foundation import NSURL
        doc = Quartz.PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(str(cls.dir / "schet.pdf")))
        page = doc.pageAtIndex_(0)
        box = page.boundsForBox_(Quartz.kPDFDisplayBoxMediaBox)
        image = page.thumbnailOfSize_forBox_((box.size.width * 3, box.size.height * 3), Quartz.kPDFDisplayBoxMediaBox)
        scan = Quartz.PDFDocument.alloc().init()
        scan.insertPage_atIndex_(Quartz.PDFPage.alloc().initWithImage_(image), 0)   # a picture of the page: no text layer, like a scan
        scan.writeToFile_(str(cls.dir / "scan.pdf"))
        subprocess.run(["sips", "-s", "format", "png", str(cls.dir / "schet.pdf"), "--out", str(cls.dir / "clear.png")], capture_output=True, timeout=60)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_a_pdf_with_text_is_read_from_its_text(self):
        text = local.pdf_text(self.dir / "schet.pdf")
        self.assertIn("12 000 рублей", text)
        self.assertTrue(text.startswith("[стр. 1]"))   # every page headed by its number: an answer can cite it

    def test_cells_of_a_scanned_row_stay_in_their_row(self):
        """RAGFlow's check on a scanned invoice: sorted by height alone, a row's cells scattered among its neighbours."""
        boxes = [(0.802, 0.780, 0.70, "Сумма"), (0.800, 0.781, 0.10, "Товар"), (0.801, 0.779, 0.40, "Кол-во"),
                 (0.752, 0.731, 0.40, "2"), (0.750, 0.730, 0.10, "Чай"), (0.751, 0.729, 0.70, "1200")]
        self.assertEqual(local._lines(boxes), "Товар | Кол-во | Сумма\nЧай | 2 | 1200")

    def test_a_scanned_pdf_is_read_from_its_picture(self):
        text = local.pdf_text(self.dir / "scan.pdf").lower()
        self.assertIn("оплат", text)
        self.assertIn("октябр", text)

    def test_a_transparent_picture_is_read_on_white(self):
        text = local.ocr(self.dir / "clear.png").lower()
        self.assertIn("счёт на оплату", text.replace("e", "е"))
        self.assertIn("document", local.labels(self.dir / "clear.png"))

    def test_view_file_reads_a_pdf_without_the_cloud(self):
        from sena.core import attachments
        with mock.patch.object(roles.llm, "respond", mock.Mock(side_effect=AssertionError("PDF не должен уходить в облако"))):
            self.assertIn("12 000 рублей", attachments.view(self.dir / "schet.pdf"))


class Roles(unittest.TestCase):
    def setUp(self):
        _reset_roles()
        self.addCleanup(_reset_roles)
        for role in config.LEARNING:
            (config.TRAINING_DIR / f"{role}.jsonl").unlink(missing_ok=True)

    def test_defaults_come_from_config_and_the_owner_can_move_a_role(self):
        self.assertEqual({r: roles.where(r) for r in config.ROLES}, config.ROLES)
        self.assertIn("теперь дома", roles.move("compact", "home"))
        self.assertEqual(roles.where("compact"), "home")
        self.assertIn("и так дома", roles.move("compact", "home"))
        self.assertIn("Роли:", roles.move("brain", "home"))   # the brain isn't a switchable role
        merged, rejected = settings.apply({"roles": {"compact": "moon"}})
        self.assertEqual(rejected, ["roles"])
        self.assertEqual(merged["roles"], {"compact": "home"})

    def test_a_private_role_never_falls_back_to_the_cloud(self):
        cloud = mock.Mock(side_effect=AssertionError("чужие данные ушли в облако"))
        with mock.patch.object(roles, "_cloud", cloud):
            with self.assertRaises(local.Unavailable):   # the stub refuses: no home model in tests
                roles.ask("ears", "инструкция", "разговор", {"type": "object"})
        cloud.assert_not_called()

    def test_a_cloud_learning_role_keeps_its_answers_as_examples(self):
        with mock.patch.object(roles, "_cloud", lambda *a, **k: "сводка"):
            self.assertEqual(roles.ask("compact", "сожми", "старые сообщения"), "сводка")
        kept = roles.examples("compact")
        self.assertEqual((kept[-1]["input"], kept[-1]["output"], kept[-1]["model"]), ("старые сообщения", "сводка", config.MODEL_FAST))

    def test_a_moved_role_that_fails_at_home_asks_the_cloud(self):
        roles.move("router", "home")
        with mock.patch.object(roles, "_cloud", lambda *a, **k: {"recipe": None, "args": {}}):
            self.assertEqual(roles.ask("router", "выбери", "задача", {"type": "object"}), {"recipe": None, "args": {}})

    def test_after_a_failure_the_cloud_is_left_alone_for_a_while(self):
        self.addCleanup(setattr, roles, "_cloud_down", 0.0)
        broke = mock.Mock(side_effect=RuntimeError("Error code: 429 - insufficient_quota: You exceeded your current quota"))
        with mock.patch.object(roles, "_cloud", broke), mock.patch.object(local, "installed", lambda: {"chat": True}), \
                mock.patch.object(local, "chat", lambda *a, **k: "дома"):
            self.assertEqual(roles.ask("compact", "сожми", "раз"), "дома")
            self.assertTrue(roles.cloud_paused())
            self.assertEqual(roles.ask("compact", "сожми", "два"), "дома")
        self.assertEqual(broke.call_count, 1)   # the second job didn't wait on the cloud
        roles._cloud_down = 0.0
        self.assertFalse(roles.cloud_paused())

    def test_one_broken_answer_is_not_an_outage(self):
        """LiteLLM classifies an error once: a broken answer of ours goes home this time and doesn't close the cloud for minutes."""
        self.addCleanup(setattr, roles, "_cloud_down", 0.0)
        with mock.patch.object(roles, "_cloud", side_effect=ValueError("ответ не JSON")), mock.patch.object(local, "installed", lambda: {"chat": True}), \
                mock.patch.object(local, "chat", lambda *a, **k: "дома"):
            self.assertEqual(roles.ask("compact", "сожми", "раз"), "дома")
        self.assertFalse(roles.cloud_paused())

    def test_a_cloud_role_is_answered_at_home_when_the_cloud_fails(self):
        self.addCleanup(setattr, roles, "_cloud_down", 0.0)
        broke = mock.Mock(side_effect=RuntimeError("на счёте OpenAI кончились деньги"))
        with mock.patch.object(roles, "_cloud", broke), mock.patch.object(local, "installed", lambda: {"chat": True}), \
                mock.patch.object(local, "chat", lambda *a, **k: {"recipe": None, "args": {}}):
            self.assertEqual(roles.ask("router", "выбери", "задача", {"type": "object"}), {"recipe": None, "args": {}})
        self.assertEqual(roles.examples("router"), [])   # a home answer isn't an example to learn from
        roles._cloud_down = 0.0
        with mock.patch.object(roles, "_cloud", broke), mock.patch.object(local, "installed", lambda: {"chat": False}):
            with self.assertRaises(RuntimeError):   # no home model on this Mac: the failure is told as it is
                roles.ask("router", "выбери", "задача", {"type": "object"})

    def test_the_owners_voice_is_heard_at_home_and_the_cloud_only_steps_in(self):
        cloud = mock.Mock(return_value="облако услышало")
        with mock.patch.object(local, "hear", lambda audio, purpose=None: "дома услышала"):
            self.assertEqual(roles.hear(b"ogg", cloud=cloud), "дома услышала")
        cloud.assert_not_called()
        with mock.patch.object(local, "hear", mock.Mock(side_effect=local.Unavailable("нет whisper"))):
            self.assertEqual(roles.hear(b"ogg", cloud=cloud), "облако услышало")
            with self.assertRaises(local.Unavailable):   # nothing handed in from above: the home failure is the answer
                roles.hear(b"ogg")

    def test_sena_speaks_in_her_one_voice_and_the_home_one_takes_over_when_the_cloud_fails(self):
        cloud = mock.Mock(return_value=b"kore-mp3")
        with mock.patch.object(local, "speak", lambda text, purpose=None: b"home-mp3"):
            self.assertEqual(roles.speak("привет", config.VOICE, cloud), b"kore-mp3")
            self.assertEqual(roles.speak("привет", settings.HOME_VOICE, cloud), b"home-mp3")
            self.assertEqual(roles.speak("привет", config.VOICE, mock.Mock(side_effect=RuntimeError("нет связи"))), b"home-mp3")
        self.assertEqual(cloud.call_count, 1)
        self.assertEqual((settings.DEFAULTS["voice"], list(settings.OPTIONS["voice"])), (config.VOICE, [config.VOICE, settings.HOME_VOICE]))

    def test_a_voice_no_longer_offered_gives_way_to_senas_own(self):
        store.save_json(config.SETTINGS_FILE, {**store.load_json(config.SETTINGS_FILE, {}), "voice": "marin"})   # an old OpenAI voice left in the file
        self.addCleanup(settings.update, {"voice": config.VOICE})
        self.assertEqual(settings.get("voice"), config.VOICE)

    def test_the_fingerprint_stamp_follows_the_role(self):
        self.assertEqual(roles.embed_stamp(), local.stamp())
        roles.move("embed", "cloud")
        self.assertEqual(roles.embed_stamp(), f"{config.MODEL_EMBED}/{config.EMBED_DIMS}")

    def test_a_learning_role_moves_home_once_the_home_model_matches_the_cloud(self):
        for i in range(roles.MIN_EXAMPLES):
            with mock.patch.object(roles, "_cloud", lambda *a, **k: {"recipe": "youtube", "args": {"query": "lo-fi"}}):
                roles.ask("router", "выбери рецепт", f"задача {i}", {"type": "object"})
        with mock.patch.object(local, "installed", lambda: {"chat": True}), \
                mock.patch.object(local, "chat", lambda *a, **k: {"recipe": "YouTube", "args": {"query": "lo-fi"}}):   # the same, written differently
            report = {r["role"]: r for r in roles.graduate()}
        self.assertEqual((report["router"]["tested"], report["router"]["agree"]), (roles.EVAL_SIZE, 1.0))
        self.assertEqual(roles.where("router"), "home")
        self.assertEqual(report["compact"]["tested"], 0)   # too few examples: not tested, stays in the cloud
        self.assertEqual(roles.where("compact"), "cloud")

    def test_closeness_gives_partial_credit_and_says_what_differs(self):
        """DSPy's metric with feedback: free-text facts are matched by meaning, a summary loses points for a lost date or number,
        a recipe with one wrong parameter is half right — instead of all or nothing."""
        with mock.patch.object(local, "embed", side_effect=local.Unavailable("нет домашней модели")):
            fact = {"facts": [{"section": "Обо мне", "text": "у Темы дочь 7 лет", "instead_of": ""}], "people": []}
            self.assertEqual(roles.score("harvest", fact, fact)[0], 1.0)
            value, feedback = roles.score("harvest", fact, {"facts": [], "people": []})
            self.assertEqual(value, 0.5)   # the people half agrees (nothing to note), the facts half missed one
            self.assertIn("пропущено", feedback)
            value, feedback = roles.score("compact", "Встреча с Игорем 31.10.2026 в 15:00 у дома 5", "Встреча с Игорем в пятницу у дома")
            self.assertLess(value, 0.5)
            self.assertIn("31.10.2026", feedback)
        self.assertEqual(roles.score("router", {"recipe": "yt", "args": {"q": "a"}}, {"recipe": "YT", "args": {"q": "b"}}), (0.5, "параметры не те: q"))
        cloud = {"reason": "повторяет ту же просьбу", "disagrees": True}   # the verdict counts, the reason is free words
        self.assertEqual(roles.score("feedback", cloud, {"reason": "он поправляет её", "disagrees": True}), (1.0, "верно"))
        self.assertEqual(roles.score("feedback", cloud, {"reason": "новая тема", "disagrees": False}), (0.0, "несогласие пропущено"))
        self.assertEqual(roles._json('Вот: {"a": 1} и ещё {"b": 2}'), {"a": 1})   # the first object, not both glued together
        jobs = lambda text, at: {"jobs": [{"at": at, "repeat": "daily", "text": text}]}   # noqa: E731 — the same jobs, whatever their words
        self.assertEqual(roles.score("rules", jobs("Погода", "25.10.2026 09:00"), jobs("Пришли погоду", "25.10.2026 09:00")), (1.0, "верно"))
        self.assertEqual(roles.score("rules", jobs("Погода", "25.10.2026 09:00"), jobs("Погода", "25.10.2026 10:00")), (0.0, "не совпало"))
        self.assertEqual(roles.score("heartbeat", {"notify": False}, {"notify": False}), (1.0, "верно"))   # any other role: the same answer

    def test_a_home_answer_cut_at_its_limit_is_asked_once_more_with_room(self):
        limits = []

        def door(path, body, timeout=300):
            limits.append(body["max_tokens"])
            cut = len(limits) == 1
            return {"choices": [{"message": {"content": '{"a"' if cut else '{"a": 1}'}, "finish_reason": "length" if cut else "stop"}], "usage": {}}
        with mock.patch.object(local, "_post", door):
            self.assertEqual(local.chat("инструкция", "текст", schema={"type": "object"}, max_tokens=300), {"a": 1})
        self.assertEqual(limits, [300, 600])

    def test_a_home_model_that_disagrees_does_not_move_the_role(self):
        for i in range(roles.MIN_EXAMPLES):
            with mock.patch.object(roles, "_cloud", lambda *a, **k: {"jobs": [{"text": "x", "at": "25.10.2026 09:00", "repeat": "daily"}], "note": ""}):
                roles.ask("rules", "время", f"правило {i}", {"type": "object"})
        with mock.patch.object(local, "installed", lambda: {"chat": True}), \
                mock.patch.object(local, "chat", lambda *a, **k: {"jobs": [{"text": "x", "at": "25.10.2026 10:00", "repeat": "daily"}], "note": ""}):
            report = {r["role"]: r for r in roles.graduate()}
        self.assertEqual(report["rules"]["agree"], 0.0)
        self.assertEqual(roles.where("rules"), "cloud")

    def test_examples_export_as_a_chat_dataset(self):
        for i in range(10):
            with mock.patch.object(roles, "_cloud", lambda *a, **k: {"recipe": None, "args": {}}):
                roles.ask("router", "выбери", f"задача {i}", {"type": "object"})
        made = roles.dataset("router")
        self.assertEqual((made["train"], made["valid"]), (9, 1))
        line = json.loads((Path(made["folder"]) / "train.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual([m["role"] for m in line["messages"]], ["system", "user", "assistant"])
        self.assertEqual(json.loads(line["messages"][2]["content"]), {"recipe": None, "args": {}})

    def test_the_table_shows_every_role_and_the_fixed_jobs(self):
        rows = roles.table()
        self.assertEqual([r["role"] for r in rows if r["switchable"]], list(config.ROLES))
        self.assertTrue(any(not r["switchable"] and r["place"] == "cloud" and r["model"] == config.MODEL_MAIN for r in rows))
        self.assertTrue(all(r["why"] for r in rows))


class Ears(unittest.TestCase):
    """Overheard speech: the home model answers in JSON, the code decides what of it touches the cards."""

    def setUp(self):
        people._people = []
        self.olga = people.new_person(name="Ольга")
        people.update(self.olga["id"], facts=["сестра"])
        self.igor = people.new_person(name="Игорь")

    def line(self, pid, who, text):
        return {"id": f"x{abs(hash(text)) % 10 ** 6}", "time": 1.0, "tz": "+07:00", "pid": pid, "who": who, "text": text}

    def test_only_new_facts_about_people_in_the_conversation_are_kept(self):
        entries = [self.line(self.olga["id"], "Ольга", "Я переехала в Дананг, работаю в школе английского"),
                   self.line(self.igor["id"], "Игорь", "Пароль от банка Qwerty123, запиши")]
        reply = {"about": "сестра рассказывает о переезде", "people": [
            {"id": self.olga["id"], "name": "Ольга", "facts": ["сестра", "переехала в Дананг"], "relationship": ""},
            {"id": self.igor["id"], "name": "Игорь", "facts": ["пароль от банка Qwerty123"], "relationship": ""},
            {"id": "p-nobody", "name": "Пётр", "facts": ["живёт в Москве"], "relationship": ""}], "merge": []}
        with mock.patch.object(roles, "ask", lambda *a, **k: reply):
            answer = ag_ears.analyze(entries, {})
        self.assertEqual(answer, "Карточек обновлено: 1")
        self.assertEqual(people.facts(people.by_id(self.olga["id"])), ["сестра", "переехала в Дананг"])   # the known fact isn't doubled
        self.assertEqual(people.facts(people.by_id(self.igor["id"])), [])   # a password never lands in a card
        self.assertEqual(people.by_id(self.olga["id"])["facts"][-1]["source"], "heard")

    def test_an_unknown_voice_gets_a_card_only_with_a_name_or_a_fact(self):
        voice = [[1.0, 0.0, 0.0]]
        entries = [self.line("g1", "неизвестный", "Привет, я Марк, буду учить тебя серфингу"), self.line("g2", "неизвестный", "Ага")]
        reply = {"about": "знакомство с тренером", "people": [
            {"id": "g1", "name": "Марк", "facts": ["тренер по серфингу"], "relationship": ""},
            {"id": "g2", "name": "неизвестный", "facts": [], "relationship": ""}], "merge": []}
        with mock.patch.object(roles, "ask", lambda *a, **k: reply):
            ag_ears.analyze(entries, {"g1": voice, "g2": voice})
        named = [p for p in people.all_people() if p.get("name") == "Марк"]
        self.assertEqual(len(named), 1)
        self.assertEqual(people.facts(named[0]), ["тренер по серфингу"])
        self.assertEqual(len(people.all_people()), 3)   # Ольга, Игорь, Марк: the silent g2 got no card

    def test_lines_too_short_to_tell_voices_apart_are_no_one_person(self):
        """g0 marks every short unknown line, from several people: it got a card, and every g0 line went to it for good."""
        entries = [self.line("g0", "неизвестный", "Да"), self.line("g0", "неизвестный", "Нет, я Пётр")]
        reply = {"about": "короткие реплики", "people": [{"id": "g0", "name": "Пётр", "facts": ["сосед"], "relationship": ""}], "merge": []}
        with mock.patch.object(roles, "ask", lambda *a, **k: reply), mock.patch.object(ag_ears.heard, "assign", side_effect=AssertionError("g0 не назначают")):
            self.assertEqual(ag_ears.analyze(entries, {}), "Запоминать нечего")
        self.assertEqual(len(people.all_people()), 2)   # no card for g0

    def test_a_voice_that_turns_out_to_be_a_known_person_is_merged(self):
        entries = [self.line("g1", "неизвестный", "Тема, это Ольга, звоню с другого номера")]
        reply = {"about": "сестра звонит с другого номера", "people": [], "merge": [{"keep": self.olga["id"], "drop": "g1"}]}
        with mock.patch.object(roles, "ask", lambda *a, **k: reply):
            ag_ears.analyze(entries, {"g1": [[0.0, 1.0, 0.0]]})
        self.assertEqual(len(people.all_people()), 2)
        self.assertEqual(people.by_id(self.olga["id"])["voices"], [[0.0, 1.0, 0.0]])

    def test_without_a_home_model_nothing_goes_anywhere(self):
        entries = [self.line(self.olga["id"], "Ольга", "Я переехала в Дананг")]
        with mock.patch.object(roles, "_cloud", mock.Mock(side_effect=AssertionError("чужая речь ушла в облако"))):
            self.assertIn("Не разобрано", ag_ears.analyze(entries, {}))


class FilesRead(unittest.TestCase):
    def test_a_photo_is_read_once_and_found_by_what_is_in_it(self):
        index.drop(files.KIND)
        path = config.INBOX_DIR / "20261024-120000-screen.png"
        path.write_bytes(b"png")
        entry = files.attachments.register(path, caption="", source="telegram", attach_task=False)
        with mock.patch.object(local, "ocr", lambda p, purpose=None: "ошибка 502 Bad Gateway"), \
                mock.patch.object(local, "installed", lambda: {"eyes": False}), mock.patch.object(local, "labels", lambda p, purpose=None: ["screenshot"]):
            fresh = files.read(entry)
        self.assertIn("502", fresh["content"])
        self.assertIn("screenshot", fresh["content"])
        self.assertIsNone(files.read(fresh))   # read once: the manifest remembers
        self.assertEqual(files.manifest()[entry["path"]]["content"], fresh["content"])
        self.assertEqual(index.search("502", kinds=[files.KIND], text_only=True)[0]["ref"], entry["path"])
        def ocr(p, purpose=None):
            if str(p) == str(path):
                raise AssertionError("прочитан второй раз")
            return ""
        with mock.patch.object(local, "ocr", ocr), mock.patch.object(local, "pdf_text", lambda p, **k: ""), mock.patch.object(local, "labels", lambda p, **k: []):
            files.read_pending()   # other tests' files may get read; this one never again
        lines = [e for e in store.lines(config.FILES_FILE) if e["path"] == entry["path"]]
        self.assertEqual(len(lines), 2)   # the arrival and the one reading


class TidyKeepsEveryLine(unittest.TestCase):
    def test_the_night_changes_only_named_lines_and_keeps_every_number(self):
        facts.replace("## Обо мне\n- 01.01.2026: любит кофе без сахара\n- 02.01.2026: любит кофе, американо\n- 01.01.2026: размер обуви 43\n"
                      "- 01.01.2026: живёт в Москве\n- 05.01.2026: переехал в Стамбул\n\n## Планы\n- 01.01.2026: созвон в 14:00\n")
        done, refused = facts.tidy_ops(
            merge=[{"old": ["любит кофе без сахара", "любит кофе, американо"], "new": "02.01.2026: любит американо без сахара"},
                   {"old": ["размер обуви 43", "созвон в 14:00"], "new": "01.01.2026: размер обуви и созвон"}],   # numbers lost: refused
            supersede=[{"old": "01.01.2026: живёт в Москве", "why": "переехал в Стамбул"}],
            archive=["такой строки нет"])
        memory = facts.read()
        self.assertIn("любит американо без сахара", memory)
        self.assertNotIn("любит кофе без сахара", memory)
        self.assertIn("размер обуви 43", memory)   # the merge that would drop 43 and 14:00 didn't apply
        self.assertIn("созвон в 14:00", memory)
        self.assertNotIn("живёт в Москве", memory)
        archived = config.ARCHIVE_FILE.read_text(encoding="utf-8")
        self.assertIn("живёт в Москве (так было до", archived)
        self.assertIn("сменилось: переехал в Стамбул", archived)
        self.assertEqual(len(done), 2)
        self.assertEqual(len(refused), 2)
        self.assertTrue(any("потерялись числа 14:00, 43" in r for r in refused))

    def test_a_night_moves_out_at_most_a_quarter_of_the_core(self):
        facts.replace("## Обо мне\n" + "".join(f"- 01.01.2026: факт номер {i}\n" for i in range(20)))
        done, refused = facts.tidy_ops(archive=[f"факт номер {i}" for i in range(12)])
        self.assertEqual(len(done), 5)   # a quarter of 20
        self.assertEqual(len(refused), 7)
        self.assertIn("факт номер 11", facts.read())

    def test_the_night_is_one_question_and_the_code_applies_it(self):
        from sena.core import llm
        from sena.memory import skills
        facts.replace("## Обо мне\n- 01.01.2026: любит чай\n- 02.01.2026: любит чай\n- 03.01.2026: был в Порту в 2019\n\n## Планы\n")
        skills.write_skill("mac-volume", "громкость на Mac", "osascript set volume")
        skills.write_skill("mac-sound", "звук на Mac", "osascript set volume")
        plan = {"merge": [{"old": ["01.01.2026: любит чай", "02.01.2026: любит чай"], "new": "02.01.2026: любит чай"}], "supersede": [],
                "archive": ["03.01.2026: был в Порту в 2019"],
                "skills": [{"name": "mac-volume", "when": "громкость или звук на Mac", "recipe": "osascript set volume", "code": "osascript -e 'set volume'"}],
                "delete_skills": ["mac-sound"]}
        calls = []

        def respond(**kw):
            calls.append(kw)
            return SimpleNamespace(output_text=json.dumps(plan, ensure_ascii=False))
        with mock.patch.object(llm, "respond", respond):
            said = ag_night.apply(ag_night.think("# Память\n..."))
        self.assertEqual(len(calls), 1)   # one question, no tool loop
        self.assertEqual(calls[0]["text"]["format"]["type"], "json_schema")
        self.assertIn("Память: изменений 2", said)
        self.assertEqual(facts.read().count("любит чай"), 1)
        self.assertNotIn("mac-sound", skills.names())
        self.assertIn("громкость или звук", skills.read_skill("mac-volume"))
        self.assertIn("Порту", (config.ARCHIVE_FILE).read_text(encoding="utf-8"))   # archived, not lost

    def test_the_claude_bridge_asks_for_the_schema(self):
        from sena.core import llm_claude
        seen = {}

        class Client:
            class messages:
                @staticmethod
                def create(**kw):
                    seen.update(kw)
                    return SimpleNamespace(id="m1", model="claude-haiku-5-5", content=[SimpleNamespace(type="text", text="{}")], stop_reason="end_turn",
                                           usage=SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0))
        schema = {"type": "object", "properties": {"jobs": {"type": "array", "maxItems": 6, "items": {"type": "object", "properties": {"at": {"type": "string"}}}}}}
        with mock.patch.object(llm_claude, "_client", lambda role: Client):
            llm_claude.respond("claude-haiku-5-5", "текст", instructions="i", text={"format": {"type": "json_schema", "schema": schema}})
        sent = seen["output_config"]["format"]["schema"]
        self.assertEqual((sent["additionalProperties"], sent["properties"]["jobs"]["items"]["additionalProperties"]), (False, False))   # Claude takes only closed objects
        self.assertNotIn("maxItems", sent["properties"]["jobs"])   # and no count limits (a 400 otherwise)
        with mock.patch.object(llm_claude, "_client", lambda role: Client):   # free keys can't be said in Claude's schema: the schema goes in words
            llm_claude.respond("claude-haiku-5-5", "текст", instructions="i", text={"format": {"type": "json_schema", "schema": {"type": "object", "properties": {"args": {"type": "object"}}}}})
        self.assertNotIn("format", seen["output_config"])
        self.assertIn("Ответь только JSON по этой схеме", seen["system"][0]["text"])


class WithoutTheCloud(unittest.TestCase):
    def test_the_home_model_answers_from_memory_when_the_cloud_is_out_of_reach(self):
        from sena.agents.ag_sena import agent

        class APIConnectionError(Exception):   # what the Claude client raises when there is no internet
            __module__ = "anthropic._exceptions"
        asked = []
        with mock.patch.object(agent.brain, "run", mock.Mock(side_effect=APIConnectionError("Connection error"))), \
                mock.patch.object(local, "installed", lambda: {"chat": True}), \
                mock.patch.object(local, "chat", lambda instructions, text, **k: asked.append(instructions) or "Встреча с Анной в 15:00."):
            answer = agent.ask(config.OWNER_ID, "когда у меня встреча?")
        self.assertTrue(answer.startswith("Облако недоступно (нет связи с Claude), отвечаю сама"), answer)   # the service that failed, named
        self.assertIn("Встреча с Анной", answer)
        self.assertIn("без инструментов", asked[0])

    def test_a_bug_of_ours_is_not_dressed_up_as_an_outage(self):
        from sena.agents.ag_sena import agent
        with mock.patch.object(agent.brain, "run", mock.Mock(side_effect=KeyError("oops"))), mock.patch.object(local, "installed", lambda: {"chat": True}), \
                mock.patch.object(local, "chat", mock.Mock(side_effect=AssertionError("не должна отвечать за облако"))):
            answer = agent.ask(config.OWNER_ID, "привет")
        self.assertTrue(answer.startswith("Не получилось ответить"), answer)


class Hearing(unittest.TestCase):
    """Background hearing: a fast pass to catch the name, the accurate pass for a command and for every line kept."""

    def test_a_command_is_heard_again_with_the_accurate_model(self):
        """The live voice out of reach: the phrase its fast pass read with the name is read once more, accurately (voice.py)."""
        from sena.phone import convo
        passes = []

        def transcribe(wav, best=True, purpose=None, private=False, journal=True):
            passes.append(best)
            return "Сена, напомни позвонить Игорю"
        with mock.patch.object(local, "transcribe", transcribe), mock.patch.object(convo.ag_sena, "ask", lambda chat, text, fast=False, outside=None: f"ок: {text}"), \
                mock.patch.object(local, "voice_print", lambda wav: ([1.0, 0.0], 3.0)), mock.patch.object(convo.people, "is_owner", lambda vec: True):
            out = convo.command(tempfile.mkstemp(suffix=".wav")[1], "Сена напомню позвонить игорю")
        self.assertEqual(passes, [True])
        self.assertEqual((out["text"], out["say"]), ("напомни позвонить Игорю", "ок: напомни позвонить Игорю"))

    def test_a_background_line_is_kept_in_the_accurate_words(self):
        from sena.phone import convo
        kept = []
        wav = tempfile.mkstemp(suffix=".wav")[1]
        with mock.patch.object(local, "transcribe", lambda wav, best=True, **k: "Серкан прилетает в пятницу"), \
                mock.patch.object(local, "voice_print", lambda wav: ([1.0, 0.0], 3.0)), mock.patch.object(convo.heard, "append", kept.append), \
                mock.patch.object(convo, "who_is", lambda vec, seconds: ("g1", "неизвестный")):
            convo._ingest(wav, "серкан прилетает в пятницу")
        self.assertEqual(kept[0]["text"], "Серкан прилетает в пятницу")


if __name__ == "__main__":
    unittest.main()
