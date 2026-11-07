"""The live voice: her name opens a conversation, the microphone is passed on by speech, the brain answers through Live,
an unsure name lets no sound leave the Mac, her voice isn't fed back to her, the old way answers when Live is out of reach,
the stream's door is the app's; and the Live client itself speaks Google's protocol and records every reply's price."""
import base64
import json
import os
import queue
import socket
import threading
import time
import unittest
from unittest import mock

import numpy as np
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect
from websockets.sync.server import serve

from sena import config
from sena.core import costs, limits, power, store
from sena.google import media
from sena.memory import people
from sena.phone import voice

LOUD = (np.sin(np.arange(voice.FRAME) / 3) * 8000).astype(np.int16).tobytes()   # a frame as loud as speech
QUIET = bytes(voice.FRAME * 2)


class FakeDetector:
    scores = {}   # frame -> how sure the detector is that it holds the name

    def __init__(self):
        self.models = [True]

    def score(self, frame):
        return self.scores.get(frame, 0.0)


def name(score):
    # a loud frame of its own that the detector scores as the name
    frame = os.urandom(2) + LOUD[2:]
    FakeDetector.scores[frame] = score
    return frame


class FakeLive:
    """Gemini Live as the conversation sees it: what was sent, and the events the test puts in its inbox."""
    made = []
    fail = None

    def __init__(self, instructions, tools, waiting=(), purpose=""):
        self.instructions, self.tools, self.waiting = instructions, tools, waiting
        self.sent, self.pauses, self.answers, self.notes, self.told = [], 0, [], [], []
        self.inbox = queue.Queue()
        self.usd, self.closed = 0.0, False
        FakeLive.made.append(self)

    def open(self, timeout=10):
        if FakeLive.fail:
            raise media.MediaError(FakeLive.fail)
        return self

    def send(self, pcm):
        self.sent.append(pcm)

    def note(self, text):
        self.notes.append(text)

    def tell(self, text):
        self.told.append(text)

    def pause(self):
        self.pauses += 1

    def answer(self, call_id, name, result, quiet=False):
        self.answers.append((call_id, name, result))

    def receive(self, timeout=0.5):
        try:
            return [self.inbox.get(timeout=0.02)]
        except queue.Empty:
            return []

    def close(self):
        self.closed = True


class NoWarm:
    # no conversation is opened ahead in these tests: each one opens its own (Warm has its own test)
    def join(self):
        pass

    def leave(self):
        pass

    def take(self):
        return None


def until(check, timeout=3.0):
    deadline = time.time() + timeout
    while not check():
        if time.time() > deadline:
            raise AssertionError("не дождались")
        time.sleep(0.01)


class LiveVoice(unittest.TestCase):
    def setUp(self):
        FakeLive.made, FakeLive.fail = [], None
        self.asked, self.overheard = [], []
        self.heard_text = "Сена, какая погода завтра"
        patches = [mock.patch.object(voice.speech, "Detector", FakeDetector), mock.patch.object(voice.gemini, "Live", FakeLive),
                   mock.patch.object(voice, "warm", NoWarm()), mock.patch.object(voice, "FOLLOW_UP", 0.3), mock.patch.object(voice, "ECHO_TAIL", 0.05),
                   mock.patch.object(voice, "ECHO_GUESS", 0.05), mock.patch.object(voice, "LATE_QUIET", 0.2),
                   mock.patch.object(voice.convo, "stranger", lambda wav, teach=False: None),
                   mock.patch.object(voice.convo, "overheard", lambda wav, text: (self.overheard.append(text), os.unlink(wav))),
                   mock.patch.object(voice.local, "transcribe", lambda wav, best=True, **kw: self.heard_text),
                   mock.patch.object(voice.api, "send_text", lambda text, *a, **k: None),   # a late answer never leaves for Telegram from a test
                   mock.patch.object(voice.ag_sena, "ask", self.ask),
                   mock.patch.object(voice.roles, "speak", lambda text, voice=None, cloud=None, links=False: b"mp3"),   # her voice, without the cloud
                   mock.patch.object(voice, "_voice_pcm", lambda mp3: b"\x02\x00" * 2400)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.sent = []
        self.ear = voice.Ear(self.sent.append, "test")
        # a conversation a test left open is closed before the stand-ins go: its thread doesn't go on into the next tests
        self.addCleanup(lambda: self.ear.talk and self.ear.talk.end("конец теста"))

    def ask(self, chat_id, text, fast=False, outside=None, own=True):
        self.asked.append((chat_id, text, fast, outside))
        return "Завтра солнечно, плюс двадцать девять."

    def feed(self, *frames):
        self.ear.feed(b"".join(frames))

    def events(self, kind):
        return [m for m in self.sent if isinstance(m, dict) and m.get("type") == kind]

    def opened(self):
        until(lambda: FakeLive.made and self.ear.talk and self.ear.talk.mode == "live")
        return FakeLive.made[-1]

    def test_the_name_opens_a_conversation_and_the_brain_answers_through_live(self):
        self.feed(*[LOUD] * 5, name(0.99))
        self.assertEqual(len(self.events("wake")), 1)   # the app hears it at once, before Live has even opened
        live = self.opened()
        self.assertEqual(len(live.sent), 6)               # what was said just before the name went with it
        self.assertIn("Сейчас", live.notes[0])            # the time, as a quiet note: the conversation may have been opened long before
        self.assertIn("ask_sena", [t["name"] for t in live.tools])
        self.feed(*[LOUD] * 10, *[QUIET] * 20)
        self.assertEqual(len(live.sent), 6 + 10 + 14)     # speech and a 1.2 s tail; then a pause instead of quiet
        self.assertEqual(live.pauses, 1)
        live.inbox.put(("heard", "Сена, какая погода завтра"))
        live.inbox.put(("call", {"id": "c1", "name": "ask_sena", "args": {"request": "какая погода завтра"}}))
        until(lambda: live.answers)
        self.assertEqual(self.asked, [(config.OWNER_ID, "какая погода завтра", True, None)])
        self.assertEqual(live.answers, [("c1", "ask_sena", "Завтра солнечно, плюс двадцать девять.")])   # said at once (INTERRUPT)
        self.assertEqual([m["on"] for m in self.events("thinking")], [True, False])
        voice_bytes = b"\x01\x00" * 2400   # 0.1 s of her voice
        live.inbox.put(("audio", voice_bytes))
        live.inbox.put(("said", "Завтра солнечно, плюс двадцать девять."))
        live.inbox.put(("turn", True))
        until(lambda: voice_bytes in self.sent)
        self.assertNotIn(b"\x02\x00" * 2400, self.sent)   # Live said it: no second voice
        until(lambda: self.events("end"), timeout=3)      # the quiet after her answer closes the conversation
        self.assertEqual(self.events("end")[0]["reason"], "тишина")
        self.assertTrue(live.closed)
        self.feed(QUIET)
        self.assertIsNone(self.ear.talk)

    def test_a_brain_answer_cut_by_a_noise_is_said_again(self):
        """03.11.2026 18:33: «Музыка на паузе…» — one word, then silence: Live took a noise for him and cut its own speech."""
        said = []
        with mock.patch.object(voice.roles, "speak", lambda text, voice=None, cloud=None, links=False: said.append(text) or b"mp3"), \
                mock.patch.object(voice, "_voice_pcm", lambda mp3: b"\x02\x00" * 2400):
            self.feed(name(0.99))
            live = self.opened()
            live.inbox.put(("call", {"id": "c1", "name": "ask_sena", "args": {"request": "какая погода завтра"}}))
            until(lambda: live.answers)
            live.inbox.put(("audio", b"\x01\x00" * 2400))
            live.inbox.put(("interrupted", True))
            live.inbox.put(("turn", True))
            until(lambda: said)
        self.assertEqual(said, ["Завтра солнечно, плюс двадцать девять."])

    def test_live_asking_the_same_again_gets_the_same_answer_and_a_said_answer_is_not_repeated(self):
        """03.11.2026 18:48: «Что в чате 4 по 07?» once — the chat showed it twice: Live asked the brain again 6 s later."""
        said = []
        with mock.patch.object(voice.roles, "speak", lambda text, voice=None, cloud=None, links=False: said.append(text) or b"mp3"), \
                mock.patch.object(voice, "_voice_pcm", lambda mp3: b"\x02\x00" * 2400):
            self.feed(name(0.99))
            live = self.opened()
            live.inbox.put(("call", {"id": "c1", "name": "ask_sena", "args": {"request": "что в чате четыре по ноль семь"}}))
            until(lambda: live.answers)
            live.inbox.put(("audio", b"\x01\x00" * 2400))
            live.inbox.put(("said", "Завтра солнечно, плюс двадцать девять."))   # Live said all of it
            live.inbox.put(("turn", True))
            live.inbox.put(("call", {"id": "c2", "name": "ask_sena", "args": {"request": "что в чате четыре по ноль семь"}}))
            until(lambda: len(live.answers) == 2)
            time.sleep(0.3)
        self.assertEqual(len(self.asked), 1)   # the brain answered once
        self.assertEqual(live.answers[1], ("c2", "ask_sena", "Завтра солнечно, плюс двадцать девять."))
        self.assertEqual(said, [])   # Live said it whole: her own voice doesn't say it again

    def test_while_she_speaks_the_microphone_is_held_and_her_name_cuts_in(self):
        self.feed(name(0.99))
        live = self.opened()
        live.inbox.put(("audio", b"\x01\x00" * 48000))   # 2 s of her voice
        until(lambda: self.ear.talk.speaking_until > time.time() + 1)
        before = len(live.sent)
        self.feed(*[LOUD] * 5)                            # her own voice from the speaker
        self.assertEqual(len(live.sent), before)
        self.feed(name(0.99), LOUD)                       # one frame like her name may be her own word: not enough
        self.assertEqual(self.events("flush"), [])
        self.feed(name(0.99), name(0.99))                 # "Sena" over her voice
        self.assertEqual(len(self.events("flush")), 1)
        self.assertEqual(len(live.sent), before + 5)      # the name and the moment before it go on to Live

    def test_an_unsure_name_lets_nothing_out_until_whisper_reads_it(self):
        self.heard_text = "Давай завтра созвонимся после обеда"
        self.feed(name(0.7), *[LOUD] * 13)
        until(lambda: self.overheard)
        self.assertEqual(self.overheard, ["Давай завтра созвонимся после обеда"])   # someone else's words: back to the background hearing
        self.assertEqual(FakeLive.made[0].sent, [])
        self.assertEqual(self.events("wake") + self.events("end"), [])
        self.heard_text = "Смена, какая погода"   # his own voice, as whisper writes it: a mishearing of the name still counts
        self.feed(QUIET, name(0.7), *[LOUD] * 13)
        live = self.opened()
        until(lambda: self.events("wake"))
        until(lambda: len(live.sent) >= 14)

    def test_a_sure_detector_is_trusted_at_once(self):
        with mock.patch.object(voice.local, "transcribe", side_effect=AssertionError("уверенному детектору whisper не нужен")):
            self.feed(name(0.99))
            live = self.opened()
            voice_bytes = b"\x01\x00" * 2400
            live.inbox.put(("audio", voice_bytes))
            until(lambda: voice_bytes in self.sent)   # her voice isn't held back for a check
            self.feed(*[LOUD] * 15)
        self.assertEqual(self.events("end"), [])

    def test_with_echo_cancelled_in_the_app_the_owner_cuts_in_by_voice(self):
        self.ear.command(json.dumps({"type": "duplex", "on": True}))
        self.feed(name(0.99))
        live = self.opened()
        live.inbox.put(("audio", b"\x01\x00" * 48000))   # 2 s of her voice
        until(lambda: self.ear.talk.speaking_until > time.time() + 1)
        before = len(live.sent)
        quiet_echo = (np.sin(np.arange(voice.FRAME) / 3) * 300).astype(np.int16).tobytes()   # what is left of her voice after the echo canceller
        self.feed(*[quiet_echo] * 5)
        self.assertEqual(len(live.sent), before)
        self.feed(LOUD)                                  # the owner speaks over her: Live hears it and stops by itself
        self.assertGreater(len(live.sent), before)
        self.assertEqual(self.events("flush"), [])       # not cut by the server: Live decides, then says "interrupted"

    def test_the_app_saying_her_voice_has_played_opens_the_microphone_at_once(self):
        self.feed(name(0.99))
        live = self.opened()
        live.inbox.put(("audio", b"\x01\x00" * 48000))
        until(lambda: self.ear.talk.speaking_until > time.time() + 1)
        before = len(live.sent)
        self.ear.command(json.dumps({"type": "played"}))   # e.g. the owner turned the sound down: it is over sooner than estimated
        time.sleep(0.1)
        self.feed(LOUD)
        self.assertEqual(len(live.sent), before + 1)       # the gate is open: the frame goes on

    def test_an_answer_live_gave_up_on_is_still_said(self):
        release = threading.Event()
        def slow(chat_id, text, fast=False, outside=None, own=True):
            release.wait(3)
            return "Почта: два новых письма."
        with mock.patch.object(voice.ag_sena, "ask", slow):
            self.feed(name(0.99))
            live = self.opened()
            live.inbox.put(("call", {"id": "c9", "name": "ask_sena", "args": {"request": "что на почте"}}))
            until(lambda: self.events("thinking"))
            live.inbox.put(("cancel", ["c9"]))           # the owner said something meanwhile: Live drops the question
            time.sleep(0.1)
            release.set()
            self.feed(*[QUIET] * 5)                      # the owner is quiet: now she says it
            until(lambda: [e for e in self.events("said") if e["text"] == "Почта: два новых письма."])
        self.assertEqual(live.answers, [])

    def test_a_late_answer_is_checked_again_right_before_it_is_said(self):
        """crewAI's reply queue: a late answer the owner has since asked for again and heard, or one from a talk that moved on,
        is not said."""
        from types import SimpleNamespace
        cls = next(c for c in vars(voice).values() if isinstance(c, type) and hasattr(c, "still_wanted"))
        now = time.time()
        talk = SimpleNamespace(answered=[(now - 5, voice._words("что у меня на почте"))])
        self.assertFalse(cls.still_wanted(talk, "что на почте у меня", now - 30, now - 2))   # asked again after it and answered
        self.assertTrue(cls.still_wanted(talk, "какая погода завтра", now - 30, now - 2))
        self.assertFalse(cls.still_wanted(talk, "какая погода завтра", now - 400, now - voice.LATE_STALE - 1))   # the talk moved on

    def test_a_half_heard_phrase_with_a_mishearing_of_the_name_opens_a_conversation(self):
        self.heard_text = "Смена, напомни про хлеб"
        self.ear.read([LOUD] * 10, peak=0.0)             # the detector heard nothing: "Smena" in talk around is just a word
        self.assertEqual(FakeLive.made, [])
        self.assertEqual(self.overheard, ["Смена, напомни про хлеб"])
        self.ear.read([LOUD] * 10, peak=0.4)             # it half-heard the name: a call
        self.opened()
        self.assertEqual(len(self.events("wake")), 1)

    def test_an_unsure_name_that_the_detector_grows_sure_of_a_moment_later_sends_at_once(self):
        with mock.patch.object(voice.local, "transcribe", lambda wav, best=True, **kw: time.sleep(5) or ""):   # whisper is slow
            self.feed(*[LOUD] * 3, name(0.6), name(0.99))
            live = self.opened()
            until(lambda: self.events("wake"))
            until(lambda: len(live.sent) >= 5)   # the sound goes without waiting for whisper
            self.feed(*[LOUD] * 15)
        self.assertEqual(len(self.events("wake")), 1)
        self.assertEqual(len(live.sent), 20)

    def test_a_phrase_is_overheard_and_a_name_whisper_caught_opens_a_conversation(self):
        self.heard_text = "мы завтра едем в Хойан"
        self.feed(*[LOUD] * 10, *[QUIET] * 12)
        until(lambda: self.overheard)
        self.assertEqual(self.overheard, ["мы завтра едем в Хойан"])
        self.assertEqual(FakeLive.made, [])
        self.heard_text = "Сена, напомни купить хлеб"   # the detector missed the name, whisper read it
        self.feed(*[LOUD] * 10, *[QUIET] * 12)
        live = self.opened()
        self.assertEqual(len(self.events("wake")), 1)
        until(lambda: live.pauses == 1)                  # the phrase is over: Live answers at once
        self.assertGreaterEqual(len(live.sent), 10)

    def test_when_live_is_out_of_reach_the_old_way_answers(self):
        FakeLive.fail = "нет связи"
        self.heard_text = "Сена, который час"
        pcm = b"\x00\x01" * 4800
        with mock.patch.object(voice.convo, "command", lambda wav, text: {"command": True, "text": "который час", "say": "Семь утра."}), \
                mock.patch.object(voice.roles, "speak", lambda text, voice_name, cloud=None: b"mp3"), mock.patch.object(voice, "_voice_pcm", lambda mp3: pcm):
            self.feed(name(0.99))
            until(lambda: self.ear.talk.mode == "classic")
            self.feed(*[LOUD] * 5, *[QUIET] * 12)
            until(lambda: self.events("end"))
        self.assertEqual(self.events("said")[0]["text"], "Семь утра.")
        self.assertEqual(b"".join(m for m in self.sent if isinstance(m, bytes)), pcm)
        self.assertEqual(self.events("end")[0]["reason"], "ответила без живого голоса")   # the apps show it: words, not jargon

    def test_goodbye_closes_after_her_last_word(self):
        self.feed(name(0.99))
        live = self.opened()
        live.inbox.put(("audio", b"\x01\x00" * 2400))
        live.inbox.put(("call", {"id": "c2", "name": "finish", "args": {}}))
        live.inbox.put(("turn", True))
        until(lambda: self.events("end"))
        self.assertEqual(self.events("end")[0]["reason"], "попрощались")

    def test_the_owner_stops_it_by_hand_and_a_stopping_sena_does_not_start(self):
        self.feed(name(0.99))
        live = self.opened()
        self.ear.command(json.dumps({"type": "stop"}))
        self.assertEqual(self.events("end")[0]["reason"], "остановлено в приложении")
        self.assertTrue(live.closed)
        with mock.patch.object(power, "_stopping", True):
            self.feed(QUIET, name(0.99))
            self.assertTrue(self.ear.talk is None or self.ear.talk.over)

    def test_the_talk_button_needs_no_name_overhears_nothing_and_hangs_up(self):
        hung = []
        class Send(list):
            def __call__(self, message):
                self.append(message)

            def close(self):
                hung.append(True)
        self.sent = Send()
        with mock.patch.object(voice.local, "transcribe", side_effect=AssertionError("кнопке whisper не нужен")):
            self.ear = voice.Ear(self.sent, "phone", button=True)
            self.ear.start([], sure=True, how="кнопка", pressed=True)
            self.assertEqual(self.events("wake"), [{"type": "wake"}])
            live = self.opened()
            self.feed(*[LOUD] * 15)
            self.assertEqual(len(live.sent), 15)          # straight to Live: no name to wait for
            voice_bytes = b"\x01\x00" * 2400
            live.inbox.put(("audio", voice_bytes))
            until(lambda: voice_bytes in self.sent)        # her voice isn't held: there is no name to read
            live.inbox.put(("call", {"id": "c3", "name": "finish", "args": {}}))
            live.inbox.put(("turn", True))
            until(lambda: hung)
        self.assertEqual(self.events("end")[0]["reason"], "попрощались")
        self.feed(*[LOUD] * 10, *[QUIET] * 12)               # after it nothing is listened to
        time.sleep(0.2)
        self.assertEqual(self.overheard, [])
        self.assertEqual(len(FakeLive.made), 1)

    def test_a_turn_too_short_to_tell_keeps_the_last_voice_check(self):
        checks = []
        with mock.patch.object(voice.convo, "stranger", lambda wav, teach=False: checks.append(wav) or "голос не похож на голос Темы"):
            self.feed(*[LOUD] * 12, name(0.99))
            live = self.opened()
            live.inbox.put(("call", {"id": "c1", "name": "ask_sena", "args": {"request": "открой терминал"}}))
            until(lambda: live.answers)
            live.inbox.put(("turn", True))
            until(lambda: self.ear.talk.turn_frames == [])
            live.inbox.put(("call", {"id": "c2", "name": "ask_sena", "args": {"request": "да"}}))
            until(lambda: len(live.answers) == 2)
        self.assertEqual(len(checks), 1)
        self.assertEqual([a[3] for a in self.asked], ["голос не похож на голос Темы"] * 2)

    def test_a_bare_yes_is_short_however_much_quiet_goes_with_it(self):
        """Round 2 (J10): the turn's sound holds the moment before the speech and a 1.2 s quiet tail — a bare «да» counted as two
        seconds: always "his own words", and checked afresh on a print of a third of a second of speech."""
        self.assertAlmostEqual(voice._spoken([QUIET] * 4 + [LOUD] * 4 + [QUIET] * 15), 4 * voice.STEP)
        self.assertEqual(voice._spoken([QUIET] * 20), 0.0)
        checks = []

        def ask(chat_id, text, fast=False, outside=None, own=True):
            self.asked.append((text, outside, own))
            return "Хорошо."
        with mock.patch.object(voice.ag_sena, "ask", ask), \
                mock.patch.object(voice.convo, "stranger", lambda wav, teach=False: checks.append(wav) or "голос не похож на голос Темы"):
            self.feed(*[LOUD] * 15, name(0.99))
            live = self.opened()
            live.inbox.put(("call", {"id": "c1", "name": "ask_sena", "args": {"request": "напиши маме, что я задержусь"}}))
            until(lambda: live.answers)
            live.inbox.put(("turn", True))
            until(lambda: self.ear.talk.turn_frames == [])
            self.ear.talk.turn_frames = [QUIET] * 4 + [LOUD] * 4 + [QUIET] * 15   # «да», with what goes to Live around it
            live.inbox.put(("call", {"id": "c2", "name": "ask_sena", "args": {"request": "да"}}))
            until(lambda: len(live.answers) == 2)
        self.assertEqual(len(checks), 1)   # the short turn keeps the first check
        self.assertEqual([a[2] for a in self.asked], [True, False])   # and it can't say "yes" for him by itself

    def test_too_little_clean_speech_keeps_the_last_check(self):
        """convo.stranger tells a print too short to judge from a stranger's voice: the turn keeps what was known."""
        verdicts = iter([None, voice.convo.TOO_SHORT])
        with mock.patch.object(voice.convo, "stranger", lambda wav, teach=False: next(verdicts)):
            self.feed(*[LOUD] * 15, name(0.99))
            live = self.opened()
            live.inbox.put(("call", {"id": "c1", "name": "ask_sena", "args": {"request": "какая погода"}}))
            until(lambda: live.answers)
            live.inbox.put(("turn", True))
            until(lambda: self.ear.talk.turn_frames == [])
            self.ear.talk.turn_frames = [LOUD] * 15   # long enough to be checked again, but the print came out too short
            live.inbox.put(("call", {"id": "c2", "name": "ask_sena", "args": {"request": "а завтра"}}))
            until(lambda: len(live.answers) == 2)
        self.assertEqual([a[3] for a in self.asked], [None, None])

    def test_the_talk_buttons_first_turn_teaches_his_voice_and_an_unlearned_voice_is_told_once(self):
        """Round 2 (J11): his voice prints were lost, and nothing a voice turn said could be his again — nothing taught it back
        but Telegram's voice messages, and nothing told him."""
        taught, told = [], []
        with mock.patch.object(voice.convo, "stranger", lambda wav, teach=False: taught.append(teach) or voice.convo.NOT_LEARNED), \
                mock.patch.object(voice.convo, "tell_unlearned", lambda: told.append(True)):
            self.ear = voice.Ear(self.sent.append, "phone", button=True)
            self.ear.start([], sure=True, how="кнопка", pressed=True)
            live = self.opened()
            self.feed(*[LOUD] * 15, *[QUIET] * 3)
            live.inbox.put(("call", {"id": "c1", "name": "ask_sena", "args": {"request": "что на почте"}}))
            until(lambda: live.answers)
            live.inbox.put(("turn", True))
            until(lambda: self.ear.talk.turn_frames == [])
            until(lambda: time.time() > self.ear.talk.speaking_until + self.ear.talk.tail())   # her answer has played: the microphone is open again
            self.feed(*[LOUD] * 15)
            live.inbox.put(("call", {"id": "c2", "name": "ask_sena", "args": {"request": "а вчера что пришло"}}))
            until(lambda: len(live.answers) == 2)
        self.assertEqual(taught, [True, False])   # only the turn right after his press
        self.assertTrue(told)

    def test_small_talk_live_answers_itself_is_in_the_chat_and_a_brain_turn_is_not_twice(self):
        """«Сена, привет, как дела?» — Live answered by itself, and the chat never showed it: only the brain wrote turns down."""
        kept = []
        with mock.patch.object(voice.history, "spoken", lambda chat, heard, said, outside=None: kept.append((heard, said))):
            self.feed(name(0.99))
            live = self.opened()
            live.inbox.put(("heard", "привет, как дела?"))
            live.inbox.put(("said", "Привет! Всё отлично."))
            live.inbox.put(("turn", True))
            until(lambda: kept)
            live.inbox.put(("heard", "что на почте"))
            live.inbox.put(("call", {"id": "c1", "name": "ask_sena", "args": {"request": "что на почте"}}))
            until(lambda: live.answers)
            live.inbox.put(("turn", True))
            time.sleep(0.3)
        self.assertEqual(kept, [("привет, как дела?", "Привет! Всё отлично.")])   # the brain's turns it wrote itself

    def test_a_long_job_answer_is_said_even_after_his_long_silence(self):
        """02.11.2026 17:41: «включи Red Hot Chili Peppers» — the job took 33 s, he waited quietly, and the conversation closed
        "for silence" the very second the answer came. 18:19: handed to Live, the answer came 8–23 s late, and he gave up."""
        release = threading.Event()

        def slow(chat_id, text, fast=False, outside=None, own=True):
            release.wait(3)
            return "Музыка играет."
        with mock.patch.object(voice.ag_sena, "ask", slow), mock.patch.object(voice, "LIVE_WAIT", 0.3):
            self.feed(name(0.99))
            live = self.opened()
            live.inbox.put(("call", {"id": "c3", "name": "ask_sena", "args": {"request": "включи музыку"}}))
            until(lambda: self.events("thinking"))
            time.sleep(0.6)   # longer than FOLLOW_UP: he keeps quiet while she works
            self.assertEqual(self.events("end"), [])
            release.set()
            until(lambda: [e for e in self.events("said") if e["text"] == "Музыка играет."])   # Live stayed silent: her TTS voice said it
            self.assertEqual(self.events("end"), [])   # not closed before she said it
        self.assertTrue(any(m == b"\x02\x00" * 2400 for m in self.sent))

    def late(self, answer, gone=False):
        """A long job asked by voice; he ends the conversation while the brain works. gone — the app hangs up too."""
        release, sent = threading.Event(), []

        def slow(chat_id, text, fast=False, outside=None, own=True):
            release.wait(3)
            return answer
        with mock.patch.object(voice.ag_sena, "ask", slow), mock.patch.object(voice.api, "send_text", sent.append), \
                mock.patch.object(voice.roles, "speak", lambda text, voice=None, cloud=None, links=False: b"mp3"), \
                mock.patch.object(voice, "_voice_pcm", lambda mp3: b"\0" * voice.VOICE_RATE):
            self.feed(name(0.99))
            live = self.opened()
            live.inbox.put(("call", {"id": "c5", "name": "ask_sena", "args": {"request": "выложи лендинг"}}))
            until(lambda: self.events("thinking"))
            self.ear.command(json.dumps({"type": "stop"}))
            if gone:
                self.ear.close()
            release.set()
            until(lambda: sent or [e for e in self.events("said") if e["text"] == answer])
        self.assertEqual(live.answers, [])
        return sent

    def test_an_answer_that_outlived_the_conversation_is_said_aloud(self):
        """Round 2 (J32): a long job asked by voice — the conversation ended (his «стоп», the ten minutes) and the answer was
        dropped. Then it went to Telegram as text: asked by voice, it is answered by voice now, on the same microphone."""
        sent = self.late("Готово: лендинг выложен.")
        self.assertEqual(sent, [])
        self.assertTrue(any(isinstance(m, bytes) for m in self.sent))   # her voice went to the app

    def test_an_answer_whose_app_is_gone_goes_to_telegram(self):
        sent = self.late("Готово: лендинг выложен.", gone=True)
        self.assertIn("выложи лендинг", sent[0])
        self.assertIn("Готово: лендинг выложен.", sent[0])

    def test_an_answer_live_gave_up_on_and_never_said_is_said_after(self):
        release = threading.Event()

        def slow(chat_id, text, fast=False, outside=None, own=True):
            release.wait(3)
            return "Почта: два новых письма."
        with mock.patch.object(voice.ag_sena, "ask", slow), \
                mock.patch.object(voice.roles, "speak", lambda text, voice=None, cloud=None, links=False: b"mp3"), \
                mock.patch.object(voice, "_voice_pcm", lambda mp3: b"\0" * voice.VOICE_RATE):
            self.feed(name(0.99))
            live = self.opened()
            live.inbox.put(("call", {"id": "c9", "name": "ask_sena", "args": {"request": "что на почте"}}))
            until(lambda: self.events("thinking"))
            live.inbox.put(("cancel", ["c9"]))
            time.sleep(0.1)
            self.feed(*[LOUD] * 5)   # he goes on talking: no quiet for her to say it in
            release.set()
            until(lambda: self.ear.talk.late)
            self.ear.command(json.dumps({"type": "stop"}))
            until(lambda: [e for e in self.events("said") if e["text"] == "Почта: два новых письма."])
        self.assertEqual(live.told, [])


class VoiceCheck(unittest.TestCase):
    """convo.stranger: whose voice a command is in — too short to tell, not learned, learning from his own press, his, not his."""

    def setUp(self):
        store.clear(config.OWNER_VOICE_FILE)
        self.addCleanup(store.clear, config.OWNER_VOICE_FILE)
        self.wav = os.path.join(config.DATA, "voice-check.wav")
        open(self.wav, "wb").close()
        self.addCleanup(os.unlink, self.wav)

    def check(self, vec, seconds, teach=False, someone=None):
        with mock.patch.object(voice.convo.local, "voice_print", lambda wav: (vec, seconds)), \
                mock.patch.object(people, "identify_voice", lambda v: (someone, 0.9 if someone else 0.0)), \
                mock.patch.object(people, "all_people", lambda: []):   # other tests' people never stand beside him
            return voice.convo.stranger(self.wav, teach=teach)

    def test_short_unlearned_taught_his_and_not_his(self):
        self.assertEqual(self.check([1.0, 0.0, 0.0], 0.8), voice.convo.TOO_SHORT)
        self.assertEqual(self.check([1.0, 0.0, 0.0], 3.0), voice.convo.NOT_LEARNED)   # a voice turn without his button teaches nothing
        self.assertEqual(self.check([1.0, 0.0, 0.0], 3.0, teach=True, someone={"id": "p1"}), voice.convo.NOT_LEARNED)   # a known guest's
        self.assertEqual(self.check([1.0, 0.0, 0.0], 3.0, teach=True), voice.convo.LEARNING)   # his press: learned from this turn on
        self.assertIsNone(self.check([0.99, 0.02, 0.0], 3.0))
        self.assertEqual(self.check([0.0, 1.0, 0.0], 3.0, teach=True), "голос не похож на голос Темы")   # not his: not taught
        self.assertEqual(len(store.load_json(config.OWNER_VOICE_FILE, [])), 2)   # his press, and his recognised turn: learned on this microphone too

    def test_his_own_words_around_the_house_are_his_not_a_strangers(self):
        """The background hearing labelled his speech "unknown g1", and the analysis gave his voice a stranger's card."""
        people.remember_owner_voice([1.0, 0.0, 0.0])
        with mock.patch.object(people, "all_people", lambda: []):
            self.assertEqual(voice.convo.who_is([0.99, 0.02, 0.0], 3.0), (people.OWNER, "Тема"))
            self.assertNotEqual(voice.convo.who_is([0.0, 0.0, 1.0], 3.0)[0], people.OWNER)

    def test_an_unlearned_voice_is_told_once(self):
        told = []
        with mock.patch.object(voice.convo, "_told_unlearned", False), \
                mock.patch.object(voice.convo.safety, "background", lambda func, *a, what=None: told.append(a[0])):
            voice.convo.tell_unlearned()
            voice.convo.tell_unlearned()
        self.assertEqual(len(told), 1)
        self.assertIn("2–3 голосовых", told[0])


class OpenedAhead(unittest.TestCase):
    """A conversation is kept opened ahead while a microphone is connected: taken by the name, renewed, closed when no one listens."""

    def setUp(self):
        self.made = []
        self.fail = 0
        for p in (mock.patch.object(voice, "open_live", self.open_live), mock.patch.object(voice, "WARM_FRESH", 0.3),
                  mock.patch.object(voice, "WARM_RETRY", (0.05, 0.1))):
            p.start()
            self.addCleanup(p.stop)
        self.warm = voice.Warm()

    def open_live(self, timeout=voice.OPEN_WAIT):
        if self.fail:
            self.fail -= 1
            raise media.MediaError("нет связи")
        live = FakeLive("", [])
        self.made.append(live)
        return live

    def test_taken_renewed_and_closed_when_no_one_listens(self):
        self.warm.join()
        until(lambda: self.warm.live is not None)
        first = self.warm.take()
        self.assertIs(first, self.made[0])
        until(lambda: self.warm.live is not None and self.warm.live is not first)   # the next one is ready for the next time
        second = self.warm.live
        until(lambda: self.warm.live is not second, timeout=3)   # renewed before it gets old
        self.assertTrue(second.closed)
        self.warm.leave()
        until(lambda: not self.warm.thread.is_alive())
        self.assertTrue(all(live.closed for live in self.made if live is not first))
        self.assertIsNone(self.warm.live)

    def test_a_failed_opening_is_tried_again_and_a_stale_one_is_never_given(self):
        self.fail = 2
        self.warm.join()
        until(lambda: self.warm.live is not None)
        self.assertEqual(len(self.made), 1)
        self.warm.at -= 10   # as if it waited too long
        self.assertIsNone(self.warm.take())
        self.assertTrue(self.made[0].closed)
        self.warm.leave()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StreamDoor(unittest.TestCase):
    def setUp(self):
        FakeLive.made, FakeLive.fail = [], None
        self.port = _free_port()
        patches = [mock.patch.object(config, "VOICE_PORT", self.port), mock.patch.object(voice.speech, "Detector", FakeDetector),
                   mock.patch.object(voice.gemini, "Live", FakeLive), mock.patch.object(voice, "warm", NoWarm())]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.listener = voice.start(host="127.0.0.1")   # this Mac only: a test server is no door for the café's network
        self.addCleanup(self.listener.shutdown)

    def url(self, token=config.APP_TOKEN, path="/ear"):
        return f"ws://127.0.0.1:{self.port}{path}?t={token}&device=test"

    def test_the_door_is_the_apps_password_and_the_hearing_switch(self):
        with self.assertRaises(InvalidStatus) as denied:
            connect(self.url("неверный"))
        self.assertEqual(denied.exception.response.status_code, 403)
        with self.assertRaises(InvalidStatus) as missing:
            connect(self.url(path="/other"))
        self.assertEqual(missing.exception.response.status_code, 404)
        with mock.patch.object(voice.settings, "get", lambda key: False if key == "use_hearing" else None):
            with self.assertRaises(InvalidStatus) as deaf:
                connect(self.url())
        self.assertEqual(deaf.exception.response.status_code, 409)
        with mock.patch.object(voice.settings, "get", lambda key: False if key == "use_hearing" else None):   # the button isn't background hearing
            with connect(self.url() + "&mode=talk") as phone:
                self.assertEqual([json.loads(phone.recv(timeout=3))["type"] for _ in range(2)], ["ready", "wake"])
        with connect(self.url()) as app:
            self.assertEqual(json.loads(app.recv(timeout=3)), {"type": "ready"})   # heard: the app needs no ping to know it
            app.send(name(0.99))
            self.assertEqual(json.loads(app.recv(timeout=3)), {"type": "wake"})

    def test_switched_off_the_door_says_so(self):
        """Round 2 (AN-7): the screen opened no voice port, and the phone said «Mac не отвечает по этому адресу» while Sena was
        simply off. The screen opens the door now, and it answers in words."""
        with mock.patch.object(power, "_off", True):
            with self.assertRaises(InvalidStatus) as off:
                connect(self.url())
        self.assertEqual(off.exception.response.status_code, 409)
        self.assertIn("выключена", off.exception.response.body.decode())

    def test_the_password_may_come_in_a_header_and_a_lockout_is_told_as_such(self):
        """The address with ?t= lands in logs on the way (finding 16 of 25.10); the apps tell a lockout ("wait") from a wrong
        password ("retype it") by the refusal's text, the same as the app server's."""
        from types import SimpleNamespace
        from sena.phone import server
        with connect(f"ws://127.0.0.1:{self.port}/ear?device=test", additional_headers={"Authorization": f"Bearer {config.APP_TOKEN}"}) as app:
            self.assertEqual(json.loads(app.recv(timeout=3)), {"type": "ready"})
        ip = "10.9.8.5"
        self.addCleanup(server._locked_until.pop, ip, None)
        for _ in range(server.LOCK_AFTER):
            server.wrong_password(ip)
        door = SimpleNamespace(remote_address=(ip, 5000), respond=lambda code, text: (code, text))
        self.assertEqual(voice._admit(door, SimpleNamespace(path=f"/ear?t={config.APP_TOKEN}", headers={})), (403, "адрес закрыт за подбор пароля\n"))
        self.addCleanup(server._wrong.pop, "10.9.8.4", None)
        wrong = SimpleNamespace(remote_address=("10.9.8.4", 5000), respond=lambda code, text: (code, text))
        self.assertEqual(voice._admit(wrong, SimpleNamespace(path="/ear?t=nope", headers={})), (403, "нет доступа\n"))


class LiveClient(unittest.TestCase):
    """media.Live against a fake Google on this Mac: the setup it sends, what it reads, what it answers, what it costs."""

    USAGE = {"promptTokenCount": 1120, "responseTokenCount": 48, "thoughtsTokenCount": 101,
             "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 708}, {"modality": "AUDIO", "tokenCount": 327}],
             "responseTokensDetails": [{"modality": "AUDIO", "tokenCount": 25}]}

    def setUp(self):
        costs._reset()
        self.port = _free_port()
        self.seen = []
        for p in (mock.patch.object(media, "LIVE_URL", f"ws://127.0.0.1:{self.port}/live"), mock.patch.object(config, "GEMINI_API_KEY", "test-key")):
            p.start()
            self.addCleanup(p.stop)

    def google(self, behave):
        listener = serve(behave, "127.0.0.1", self.port)
        threading.Thread(target=listener.serve_forever, daemon=True).start()
        self.addCleanup(listener.shutdown)

    def test_setup_events_answers_and_the_price_of_each_reply(self):
        def behave(connection):
            self.seen.append(connection.request.headers["x-goog-api-key"])
            self.seen.append(json.loads(connection.recv())["setup"])
            connection.send(json.dumps({"setupComplete": {}}))
            self.seen.append(json.loads(connection.recv()))
            audio = base64.b64encode(b"\x01\x02\x03\x04").decode()
            connection.send(json.dumps({"serverContent": {"inputTranscription": {"text": "какая погода"}}}))
            connection.send(json.dumps({"serverContent": {"modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000", "data": audio}}]},
                                                          "outputTranscription": {"text": "Секунду."}}}))
            connection.send(json.dumps({"toolCall": {"functionCalls": [{"id": "c1", "name": "ask_sena", "args": {"request": "какая погода"}}]}}))
            connection.send(json.dumps({"serverContent": {"turnComplete": True}, "usageMetadata": self.USAGE}))
            self.seen.append(json.loads(connection.recv()))
        self.google(behave)
        from sena.phone import voice as live_voice
        live = media.Live("инструкция", live_voice.TOOLS, waiting=("ask_sena",)).open()
        live.send(b"\x00\x00")
        got = []
        deadline = time.time() + 3
        while ("turn", True) not in got and time.time() < deadline:
            got += live.receive(timeout=0.5)
        live.answer("c1", "ask_sena", "Солнечно")
        until(lambda: len(self.seen) == 4)
        live.close()
        key, setup, audio, reply = self.seen
        self.assertEqual(key, "test-key")
        self.assertEqual(setup["model"], f"models/{config.MODEL_LIVE}")
        self.assertEqual(setup["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"], config.VOICE)
        declared = {d["name"]: d for d in setup["tools"][0]["functionDeclarations"]}
        self.assertEqual(declared["ask_sena"]["behavior"], "NON_BLOCKING")   # the conversation goes on while the brain thinks
        self.assertNotIn("behavior", declared["finish"])
        self.assertEqual(audio["realtimeInput"]["audio"]["mimeType"], "audio/pcm;rate=16000")
        self.assertEqual(reply["toolResponse"]["functionResponses"][0],
                         {"id": "c1", "name": "ask_sena", "response": {"result": "Солнечно"}, "scheduling": "INTERRUPT"})
        self.assertEqual(got, [("heard", "какая погода"), ("audio", b"\x01\x02\x03\x04"), ("said", "Секунду."),
                               ("call", {"id": "c1", "name": "ask_sena", "args": {"request": "какая погода"}}), ("turn", True)])
        expected = ((1120 - 327) * 0.75 + 327 * 3.0 + (48 - 25 + 101) * 4.5 + 25 * 12.0) / 1e6
        self.assertAlmostEqual(live.usd, expected)
        row = costs.recent("today")[0]
        self.assertEqual((row["provider"], row["service"], row["purpose"]), ("gemini", "live", "живой голос"))
        self.assertEqual(row["units"], {"input": 793, "input_audio": 327, "audio": 25, "text": 23, "thought": 101})

    def test_a_refusal_comes_with_googles_reason(self):
        def behave(connection):
            connection.recv()
            connection.close(1008, "API key not valid. Please pass a valid API key.")
        self.google(behave)
        with self.assertRaises(media.MediaError) as refused:
            media.Live("инструкция", []).open()
        self.assertIn("API key not valid", str(refused.exception))

    def test_switched_off_it_never_connects(self):
        with mock.patch.object(power, "_off", True), mock.patch.object(media, "connect", side_effect=AssertionError("не должна звонить")):
            with self.assertRaises(limits.LimitReached):
                media.Live("инструкция", []).open()


if __name__ == "__main__":
    unittest.main()
