"""The live voice. The apps stream the microphone here (WebSocket on config.VOICE_PORT, path /ear) and get Sena's voice back.
Until her name is said nothing leaves the Mac: the stream is cut into phrases for the background hearing (convo.py), and the
name detector (speech.py) listens to every 80 ms. The name opens a conversation with Gemini Live (google/media.Live): she answers
in about a second, in her voice. Live only talks: whatever needs knowing, memory or doing it hands to the brain (ag_sena, the
fast voice path) with ask_sena and says the brain's answer at once; silent past LIVE_WAIT, the answer is said in her TTS voice. The conversation closes by itself after a quiet pause or
when the owner says goodbye. While she speaks the microphone isn't passed on (her own voice from the speaker would cut her off);
her name said over her voice stops her and starts a new turn. A conversation is kept opened ahead (Warm) while a microphone
is connected, so her first answer doesn't wait for Google to connect. When Live can't be reached, the old way answers the phrase:
the accurate whisper, the brain, Gemini TTS (convo.command).

A stream opened with mode=talk is the phone's "talk" button: the conversation starts at once without the name, nothing of it is
overheard, and the server hangs up when the conversation ends.

The stream. App -> server: binary — the microphone, 16 kHz 16-bit mono PCM, pieces of any size (100 ms is good); an app that
saves its battery may stop sending in a long quiet, but sends at least HANGOVER of quiet after speech and everything while a
conversation is on. Text — {"type": "stop"}: the owner ended the conversation by hand. Server -> app: binary — her voice,
24 kHz 16-bit mono PCM, played in the order it comes; text — {"type": "ready"} the stream is accepted (sent first: the
app knows it is heard without a ping, which URLSession holds back behind unread messages); "wake" a conversation began; "heard"/"said" {text}
what was heard and said in this turn so far; "thinking" {on} the brain is at work; "flush" — stop her voice at once and drop
what is queued; "end" {reason} — the conversation is over."""
import collections
import json
import math
import os
import re
import subprocess
import tempfile
import threading
import time
import weakref
import wave
from urllib.parse import parse_qs, urlparse

import numpy as np
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

from sena import config
from sena.agents.ag_sena import agent as ag_sena
from sena.agents.ag_sena import prompt
from sena.core import events, local, power, roles, safety, settings
from sena.core.tools import string, tool
from sena.google import media as gemini
from sena.memory import history
from sena.phone import convo, server, speech
from sena.telegram import api

RATE = 16000            # the microphone: 16 kHz, 16-bit, mono
FRAME = 1280            # samples in one step of the name detector
STEP = FRAME / RATE     # 80 ms
VOICE_RATE = 48000      # bytes a second of her voice: 24 kHz, 16-bit, mono
SPEECH_DB = -38         # louder than this counts as speech (the level the apps cut phrases by)
CLOSE_AFTER = 0.9       # s of quiet after speech: the phrase is over
MIN_SPEECH = 0.5        # s: less is a click or a sigh, not speech
MAX_PHRASE = 20         # s: a longer phrase is cut anyway
PRE_ROLL = 1.5          # s of sound before the name that go into the conversation with it
WAKE_MAYBE = 0.5        # the detector is unsure: the conversation opens, but the sound goes out only once whisper reads the name
CONFIRM_AFTER = 1.0     # s of sound after an unsure name before whisper reads it (on less it reads nothing)
ONSET = 0.3             # s before the speech resumes, passed with it: the first sound of a phrase isn't clipped
EARS = weakref.WeakSet()   # the apps' microphones connected now (state)
HANGOVER = 1.2          # s of quiet still passed on after speech: Live hears the phrase end on its own
FOLLOW_UP = 10          # s after her answer the owner may go on without the name; then the conversation closes
TALK_MAX = 600          # s: no conversation lasts longer (each reply re-reads it whole, the price grows with it)
ECHO_TAIL = 0.4         # s: after her voice the room still rings; the microphone waits that long
ECHO_GUESS = 0.8        # s more when the app doesn't say when her voice has really played: the estimate runs ahead of the speaker
BARGE_DB = -30          # an app that cancels her echo: while she speaks, only what is louder than this goes on — the owner cutting in
BARGE_FRAMES = 2        # an app that doesn't: her name heard in this many frames in a row stops her (one frame may be her own voice)
CUT_WORDS = 2           # fewer words than this heard while Live left a brain's answer unfinished: not Tema cutting in, it is said again
SAID_SHARE = 0.5        # a brain's answer counts as said when Live's own transcript has at least this share of its words
REPEAT = 30             # s: Live asking the brain the same question again within this gets the answer already made
NEAR_PEAK = 0.3         # a phrase the detector half-heard (this sure) may start with a mishearing of the name, as whisper writes it
LATE_QUIET = 1.0        # s of the owner's quiet before she says a brain's answer that came after Live had given up on the question
IDLE_WAIT = 5           # s an answer said in her TTS voice waits for Live to finish a phrase it is saying
LIVE_WAIT = 2.5         # s for Live to start saying a brain's answer (it starts in 0.6 s); then her TTS voice says it (5–6 s more)
LATE_STALE = 120        # s after which such an answer is no longer said: the talk has moved on (crewAI's reply queue re-checks before speaking)
OWN_SPEECH = 1.0        # s of his speech in a turn: a shorter one keeps the last voice check and can't say "yes" for him
OPEN_WAIT = 5           # s to wait for Live to open before the old way answers
WARM_FRESH = 300        # s: a conversation opened ahead is renewed this often (Google closes a connection after ten minutes)
WARM_RETRY = (60, 600)  # s: Live didn't open ahead — try again after a minute, then up to every ten minutes
CHECK_EVERY = 2         # s: how often the stream checks that hearing is still on and Sena isn't stopping

TOOLS = [tool("ask_sena", "Передать слова Темы мозгу Сены: он знает, помнит и делает дела. Вернёт готовый ответ — его нужно произнести.",
              {"request": string("Слова Темы дословно и полностью, без обращения по имени")}, ["request"]),
         tool("finish", "Разговор окончен: Тема попрощался или говорит не с тобой")]


def level(frame):
    """How loud a frame is, in dB of full scale (silence is -120)."""
    samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
    rms = math.sqrt(float(np.mean(samples * samples))) if len(samples) else 0.0
    return 20 * math.log10(rms / 32768) if rms > 0 else -120.0


def _spoken(frames):
    """Seconds of speech in a turn's sound: from its first loud frame to its last. The moment before it and the quiet tail that
    go to Live with it (ONSET, HANGOVER) are no speech: counted with them, a bare «да» was two seconds and always "long"."""
    loud = [i for i, frame in enumerate(frames) if level(frame) > SPEECH_DB]
    return (loud[-1] - loud[0] + 1) * STEP if loud else 0.0


def _words(text):
    return {w for w in re.findall(r"\w{3,}", str(text).lower())}


def _close(a, b):
    # two questions are the same question when most of their words are shared
    return bool(a and b) and len(a & b) / len(a | b) >= 0.6


def _wav(frames):
    # frames of the microphone -> a temporary 16 kHz WAV for whisper and the voice print; the caller deletes it
    out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(frames))
    out.close()
    return out.name


def _voice_pcm(mp3):
    # her voice from the old way (an mp3) -> the same 24 kHz PCM the live voice sends
    done = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-f", "s16le", "-ar", "24000", "-ac", "1", "pipe:1"],
                          input=mp3, capture_output=True, timeout=60)
    return done.stdout


def _reading(text):
    """What whisper made of a phrase an unsure detector woke her with: "yes" — the name or a mishearing of it ("Smena", "Siena":
    the owner's own voice is written so) among the first words (noise before it gets written as words too); "no" — clearly other
    words, four or more; "unclear" — too little to tell, and then the detector is believed."""
    words = re.findall(r"[a-zа-яё]+", text.lower())
    if speech.WAKE.match(text) or any(speech.near_name(" ".join(words[i:])) for i in range(min(5, len(words)))):
        return "yes"
    return "no" if len(words) >= 4 else "unclear"


def hearing_on():
    """Whether the microphone may be listened to now: Sena at work and hearing on in the integrations."""
    return not (power.is_off() or power.is_stopping()) and settings.get("use_hearing")


def state():
    """What her voice is doing now, for her character by the notch: "speaking", "listening" (a conversation is open and she
    is not speaking) or None."""
    for ear in list(EARS):
        talk = ear.talk
        if talk is not None and not talk.over:
            return "speaking" if time.time() < talk.speaking_until or talk.saying else "listening"
    return None


class Ear:
    """One app's microphone, frame by frame: the name detector, phrases for the background hearing, the live conversation."""

    def __init__(self, send, device="app", button=False):
        EARS.add(self)
        self.send, self.device = send, device
        self.button = button                                     # the "talk" button: one conversation, no name, nothing overheard
        self.rest = b""                                          # bytes short of a whole frame
        self.ring = collections.deque(maxlen=round(PRE_ROLL / STEP))   # the last moments: what was said just before the name
        self.phrase, self.voiced, self.quiet = [], 0, 0         # the phrase being cut for the background hearing
        self.detector = speech.Detector()                        # its own: the detector keeps the stream's state
        self.talk = None
        self.duplex = False                                      # the app cancels her echo itself: the microphone may stay open while she speaks
        self.reports_played = False                              # the app says when her voice has really played to the end
        self.peak = 0.0                                          # how sure the detector was anywhere in the phrase being cut
        self.deaf_until = 0.0                                    # her voice may still be playing: it isn't overheard speech
        self.checked = time.time()
        self.closed = False                                      # the app has gone: nothing more can be said to it
        self.lock = threading.Lock()                             # the stream and a phrase read in the background both open conversations
        if not button:   # a microphone that waits for her name keeps a conversation opened ahead; the button takes one if there is
            warm.join()

    def feed(self, data):
        data = self.rest + data
        whole = len(data) - len(data) % (FRAME * 2)
        self.rest = data[whole:]
        for i in range(0, whole, FRAME * 2):
            self.frame(data[i:i + FRAME * 2])

    def allowed(self):
        """Checked every CHECK_EVERY seconds by the stream: hearing turned off or Sena stopping closes it."""
        if time.time() - self.checked < CHECK_EVERY:
            return True
        self.checked = time.time()
        return hearing_on() or (self.button and not (power.is_off() or power.is_stopping()))

    def frame(self, frame):
        db = level(frame)
        loud = db > SPEECH_DB
        try:
            score = self.detector.score(frame)
        except Exception as e:   # the detector broke: the name is still caught by whisper's text
            events.error("Детектор имени", e)
            self.detector.models, score = [], 0.0
        self.ring.append(frame)
        talk = self.talk
        if talk and talk.over:   # the conversation ended: the background hearing goes on from a fresh phrase
            self.talk, self.deaf_until = None, talk.speaking_until + talk.tail()
            talk = None
        if talk:
            talk.hear(frame, loud, score, db)
        elif self.button:   # the button's conversation is over: the server is hanging up, nothing is listened to
            return
        elif score >= WAKE_MAYBE:
            self.start(list(self.ring), sure=score >= speech.WAKE_SCORE, how=f"детектор {score:.2f}")
        elif time.time() > self.deaf_until:
            self.collect(frame, loud, score)

    def collect(self, frame, loud, score=0.0):
        # the background hearing: a phrase starts with speech (and a moment before it) and ends with a pause
        if not self.phrase:
            if not loud:
                return
            self.phrase = list(self.ring)[-round(ONSET / STEP):]
            self.peak = 0.0
        else:
            self.phrase.append(frame)
        self.peak = max(self.peak, score)
        self.voiced, self.quiet = (self.voiced + 1, 0) if loud else (self.voiced, self.quiet + 1)
        if self.quiet * STEP >= CLOSE_AFTER or len(self.phrase) * STEP >= MAX_PHRASE:
            frames, voiced, peak = self.phrase, self.voiced, self.peak
            self.phrase, self.voiced, self.quiet, self.peak = [], 0, 0, 0.0
            if voiced * STEP >= MIN_SPEECH:
                safety.background(self.read, frames, peak, what="Слух: фраза")

    def read(self, frames, peak=0.0):
        """A finished phrase with no conversation on: whisper looks for the name in its text (the detector may have missed it).
        With the name it is a request — a conversation opens with this phrase; otherwise it is overheard speech. A mishearing
        of the name counts only if the detector half-heard it too: otherwise "Smena" or "tsena" in talk around would wake her."""
        wav = _wav(frames)
        try:
            text = local.transcribe(wav, best=False, private=True, journal=False)   # fast: only to hear whether the name is there
        except Exception as e:
            events.error("Распознавание речи на Mac", e)
            os.unlink(wav)
            return
        if speech.WAKE.match(text) or (peak >= NEAR_PEAK and speech.near_name(text)):
            os.unlink(wav)
            self.start(frames + self.phrase, sure=True, how="имя в тексте", spoken=True)
        elif len(text) < 3:
            os.unlink(wav)
        else:
            convo.overheard(wav, text)

    def start(self, frames, sure, how, spoken=False, pressed=False):
        """Open a conversation with what was just said. sure — the name is certain; spoken — the phrase is already over;
        pressed — the owner pressed "talk": no name and nothing to check."""
        with self.lock:
            if (self.talk and not self.talk.over) or power.is_off() or power.is_stopping():
                return
            self.phrase, self.voiced, self.quiet = [], 0, 0   # the phrase belongs to the conversation now
            self.talk = Talk(self, frames, sure, how, spoken, pressed)

    def command(self, text):
        try:
            message = json.loads(text)
        except ValueError:
            return
        kind = message.get("type") if isinstance(message, dict) else None
        if kind == "stop" and self.talk:
            self.send({"type": "flush"})
            self.talk.end("остановлено в приложении")
        elif kind == "played":   # her voice has really played to the end in the app
            self.reports_played = True
            if self.talk:
                self.talk.played()
        elif kind == "duplex":   # the app cancels her echo now (or no longer): the microphone may stay open while she speaks
            self.duplex = bool(message.get("on"))
            events.log("voice", "приложение гасит эхо её голоса" if self.duplex else "приложение больше не гасит эхо", device=self.device)

    def say(self, text):
        """Say an answer to this microphone outside a conversation (the brain answered after the conversation had closed):
        the same voice as the old way, shown on the shelf as said. False — the app is gone or her voice didn't come together."""
        if self.closed:
            return False
        try:
            pcm = _voice_pcm(roles.speak(text, settings.get("voice"), gemini.speech))
        except Exception as e:
            events.error("Живой голос: поздний ответ голосом", e)
            return False
        if self.closed or not pcm:
            return False
        self.send({"type": "said", "text": text})
        self.deaf_until = time.time() + len(pcm) / VOICE_RATE + ECHO_TAIL + ECHO_GUESS   # her own voice from the speaker isn't overheard speech
        for i in range(0, len(pcm), VOICE_RATE // 5):
            self.send(pcm[i:i + VOICE_RATE // 5])
        events.log("message_out", text, channel="voice", late=True)
        return True

    def close(self):
        self.closed = True
        if not self.button:
            warm.leave()
        if self.talk:
            self.talk.end("приложение отключилось")


class Talk:
    """One conversation in the live voice. Live opens in the background; until it is open and the name is certain the sound
    waits here. Then the microphone is passed on by speech (with a moment before it and a short tail after), her voice goes
    to the app, questions go to the brain; the conversation closes itself.
    A sure detector is trusted at once. An unsure one sends nothing out until whisper reads the start of the phrase: the name
    or its mishearing — the conversation goes on; clearly other words — it was someone else's talk, dropped without a sound.
    While she speaks: an app that cancels her echo passes on what is louder than her echo (the owner cutting in, Live stops
    by itself); an app that doesn't passes nothing but her name, heard twice in a row.
    mode: "opening" — Live is being connected; "live" — the conversation is on; "classic" — Live is out of reach, the old way
    collects the phrase; "answering" — the old way is answering it."""

    def __init__(self, ear, frames, sure, how, spoken=False, pressed=False):
        self.ear, self.how, self.sure = ear, how, sure
        self.started = time.time()
        self.mode, self.live = "opening", None
        self.pending = list(frames)           # sound waiting for Live to open and for the name to be certain
        self.trigger = len(frames)            # where the unsure name was heard: whisper reads CONFIRM_AFTER after it
        self.check_frames = list(frames)      # what whisper reads
        self.checking = sure or spoken or pressed   # a sure detector, a phrase whisper already read, the button: nothing to read
        self.announced = False                # the app was told the conversation began
        self.over = False
        self.lock = threading.Lock()
        self.speaking_until = 0.0             # when her voice, as sent to the app, has played to the end
        self.since_loud = round(HANGOVER / STEP) if spoken else 0   # frames since the owner's last speech, by the stream's own clock
        self.last_voice = time.time()         # the last speech of either side: the quiet after it closes the conversation
        self.paused = False                   # the microphone went quiet and Live was told so
        self.quiet = 0                        # quiet frames in a row (the old way waits for the end of the phrase)
        self.name_run = 0                     # frames in a row with her name while she speaks (an app without echo cancelling)
        self.generating = False               # Live is sending a turn right now
        self.dropping = False                 # she was cut off: the rest of that turn isn't played
        self.finishing = False                # she said goodbye: the conversation closes after her last word
        self.turn_frames = list(frames)       # the owner's sound in this turn: whose voice asked the brain
        self.outside, self.voice_checked = None, False
        self.calls, self.cancelled = set(), set()
        self.late = []                        # the brain's answers to questions Live gave up on: said when she is free
        self.answered = []                    # (when asked, the words) of the questions answered in time: a late one asked again is not repeated
        self.heard, self.said, self.turns = "", "", 0
        self.brained = False                  # the brain was asked in this turn: it writes the turn into the chat itself
        self.saying = 0                       # the brain's answers being turned into her TTS voice and said: the conversation waits for them
        self.owed = []                        # [handed at, answer, links] answers handed to Live that it hasn't started saying
        self.answering = False                # Live is saying a brain's answer: that turn is in the chat already (the brain wrote it)
        self.answer_now = None                # (answer, links, where in "said" it began) Live is saying now: checked against what it said
        self.replies = []                     # (when, the question's words, the answer): Live asking the brain the same again gets this
        if sure or spoken or pressed:
            self.began()
        threading.Thread(target=self.run, daemon=True).start()

    # ---- the microphone (the stream's thread) ----
    def hear(self, frame, loud, score, db=-120.0):
        now = time.time()
        with self.lock:
            if self.over or self.mode == "answering":
                return
            if not self.checking:   # the start of the phrase, for whisper to read the name
                self.check_frames.append(frame)
                if len(self.check_frames) - self.trigger >= CONFIRM_AFTER / STEP:
                    self.checking = True
                    safety.background(self.confirm, list(self.check_frames), what="Живой голос: проверка имени")
            if self.mode == "classic":   # the old way takes the phrase whole: it waits for the pause after it
                self.pending.append(frame)
                self.quiet = 0 if loud else self.quiet + 1
                if self.quiet * STEP >= CLOSE_AFTER or len(self.pending) * STEP >= MAX_PHRASE:
                    self.mode = "answering"
                    safety.background(self.answer_classic, list(self.pending), what="Голосовая команда")
                return
            if self.mode == "opening" or not self.sure:
                self.pending.append(frame)
                self.since_loud = 0 if loud else self.since_loud + 1
                if not self.sure and score >= speech.WAKE_SCORE:   # a moment later the detector is sure: trusted, the sound goes
                    self.sure = self.checking = True
                    if self.mode == "live":
                        self.flow()
                    safety.background(self.began, what="Живой голос: начало")
                return
            if now < self.speaking_until + self.tail():   # her voice is playing in the app
                # her echo is cancelled there: only a voice louder than what is left of it — the owner cutting in; but a brain's answer
                # (what he asked for) is stopped only by her name, like without echo cancelling: a cough or a click must not cut it
                if self.ear.duplex and not (self.answering or self.saying):
                    if db < BARGE_DB:
                        return
                else:   # the microphone hears her: only her name, twice in a row, stops her (once may be her own word)
                    self.name_run = self.name_run + 1 if score >= speech.WAKE_SCORE else 0
                    if self.name_run < BARGE_FRAMES:
                        return
                    self.name_run = 0
                    self.cut_in(now)
            self.pass_on(frame, loud, now)

    def tail(self):
        # how long after the estimated end of her voice the microphone still waits: an app that says when it has really
        # played needs only the room's ring; otherwise the estimate runs ahead of the speaker
        return ECHO_TAIL if self.ear.reports_played else ECHO_TAIL + ECHO_GUESS

    def played(self):
        # the app has played all her voice it got: the gate opens after the room's ring, not after a guess
        self.speaking_until = min(self.speaking_until, time.time())

    def pass_on(self, frame, loud, now):
        # under the lock: speech, a moment before it and a short tail after it go to Live; a long quiet is a pause.
        # The tail is counted in frames, not by the wall clock: a phone on a weak network delivers the sound in bursts
        if loud:
            self.last_voice = now
        self.since_loud = 0 if loud else self.since_loud + 1
        if self.since_loud * STEP < HANGOVER:
            if self.paused:   # speech resumes: the moment before it too, or the first sound is clipped
                self.paused = False
                for earlier in list(self.ear.ring)[-round(ONSET / STEP) - 1:-1]:
                    self.send_live(earlier)
            self.send_live(frame)
        elif not self.paused:
            self.paused = True
            self.call_live(lambda live: live.pause())

    def send_live(self, frame):
        self.turn_frames.append(frame)
        self.call_live(lambda live: live.send(frame))

    def call_live(self, action):
        try:
            action(self.live)
        except gemini.MediaError as e:
            events.error("Живой голос", e)
            threading.Thread(target=self.end, args=("связь с Google оборвалась",), daemon=True).start()   # not under our lock

    def cut_in(self, now):
        self.ear.send({"type": "flush"})
        self.speaking_until = 0.0
        self.dropping = self.generating   # the rest of the cut-off turn may still come: it isn't played
        self.paused = True                # the name and the moment before it go to Live with this frame
        self.since_loud = 0
        self.last_voice = now
        events.log("voice", "перебили по имени", device=self.ear.device)

    # ---- the name ----
    def confirm(self, frames):
        """An unsure detector: whisper reads the start of the phrase. The name or its mishearing — the conversation goes on
        (the sound that waited goes out); clearly other words — someone else's talk: dropped without a sound, overheard speech
        it is; too little to tell — dropped too. If the detector grew sure meanwhile, it has decided already."""
        wav = _wav(frames)
        try:
            text = local.transcribe(wav, best=False, private=True, journal=False)
        except Exception as e:
            events.error("Живой голос: проверка имени", e)
            text = ""
        with self.lock:
            decided = self.sure
            if not decided and _reading(text) != "no":   # only clearly other words turn her away: the owner's voice is read badly
                self.sure = True
                if self.mode == "live":
                    self.flow()
                keep = True
            else:
                keep = decided
        if keep:
            os.unlink(wav)
            self.began()
            return
        self.reject(wav, text)

    def reject(self, wav, text):
        # the name wasn't there: the conversation is dropped without a sound, the phrase is overheard speech
        if self.end("не ко мне", quiet=not self.announced):
            events.log("voice", "не ко мне: имени в начале фразы нет", device=self.ear.device, how=self.how)
        if wav and len(text) >= 3:
            convo.overheard(wav, text)
        elif wav:
            os.unlink(wav)

    def began(self):
        with self.lock:
            if self.announced:
                return
            self.announced = True
        self.ear.send({"type": "wake"})
        events.log("voice", "разговор начат", device=self.ear.device, how=self.how)

    def flow(self):
        # under the lock: Live is open and the name is certain — what waited goes out at once, then the stream goes live
        for frame in self.pending:
            self.send_live(frame)
        self.pending = []
        self.last_voice = time.time()
        if self.since_loud * STEP >= HANGOVER:   # the phrase is already over: Live answers now, without waiting for more sound
            self.paused = True
            self.call_live(lambda live: live.pause())

    def play(self, value):
        # her voice to the app; when it will have played to the end there
        now = time.time()
        self.ear.send(value)
        self.speaking_until = max(self.speaking_until, now) + len(value) / VOICE_RATE
        self.last_voice = now

    # ---- Live (its own thread) ----
    def run(self):
        try:
            live = warm.take()
            if live is None:
                live = open_live(OPEN_WAIT)
            live.note(prompt.VOICE_NOW.format(now=ag_sena.now_line()))
        except Exception as e:   # no key, no network, no money: the old way answers this phrase
            events.error("Живой голос не открылся, отвечаю без него", e)
            with self.lock:
                if self.mode == "opening":
                    self.mode = "classic"
            return
        with self.lock:
            if self.over:   # the name wasn't confirmed while Live was opening
                live.close()
                return
            self.live, self.mode = live, "live"
            if self.sure:
                self.flow()
        try:
            while not self.over:
                for kind, value in live.receive(timeout=0.25):
                    self.on(kind, value)
                self.tick()
        except Exception as e:
            events.error("Живой голос", e)
            self.end("сбой живого голоса")
        finally:
            live.close()

    def on(self, kind, value):
        if kind == "audio":
            if self.dropping:
                return
            if self.owed:   # she has started saying the answer she was handed
                _, answer, links = self.owed.pop(0)
                self.answering, self.answer_now = True, (answer, links, len(self.said))
            self.generating = True
            self.play(value)
        elif kind in ("heard", "said"):
            setattr(self, kind, getattr(self, kind) + value)
            self.ear.send({"type": kind, "text": getattr(self, kind).strip()})
        elif kind == "turn":
            self.turn_over()
        elif kind == "interrupted":
            events.log("voice", "Live прервал свою речь", device=self.ear.device, answer=bool(self.answering))
            self.ear.send({"type": "flush"})
            self.speaking_until = 0.0
            self.generating = self.dropping = False
        elif kind == "call":
            self.called(value)
        elif kind == "cancel":   # Live gave up on a question (the owner spoke meanwhile): its answer will still be said
            self.cancelled.update(value)
            events.log("voice", "Live отменил вопрос мозгу: ответ скажу, когда она освободится", device=self.ear.device)
        elif kind == "end":
            self.end(value)

    def turn_over(self):
        # her turn is over: what was heard and said goes into the journal, the next turn starts afresh. A turn Live answered
        # by itself (a greeting, small talk, a question back) goes into the chat too: what was said aloud is never missing there
        heard, said = self.heard.strip(), self.said.strip()
        if heard:
            events.log("message_in", heard, source="voice", live=True, stranger=self.outside or None)
        if said:
            events.log("message_out", said, channel="voice", live=True)
        mine = not (self.brained or self.answering)
        self.answering, now_saying = False, self.answer_now
        self.answer_now = None
        if now_saying and self.unsaid(now_saying, said, heard):
            events.log("voice", "Live не договорил ответ мозга: говорю его своим голосом", device=self.ear.device)
            self.saying += 1
            safety.background(self.say, now_saying[0], now_saying[1], True, what="Живой голос: ответ заново")
        if (heard or said) and mine:
            with self.lock:
                frames = list(self.turn_frames)
            safety.background(self.keep_turn, heard, said, frames, what="Живой голос: реплика в переписку")
        self.heard, self.said, self.brained = "", "", False
        self.turns += 1
        self.generating = self.dropping = False
        with self.lock:
            self.turn_frames = []

    def unsaid(self, now_saying, said, heard):
        """Live began a brain's answer and didn't finish it: what it said of it (its own transcript) covers less than half its
        words, and it wasn't Tema who cut in (he said next to nothing). 03.11.2026: one word, then silence — twice in an evening:
        a noise taken for his voice, and Live jumping to ask the brain the same again. Not when another answer is on its way."""
        answer, _, mark = now_saying
        want, got = _words(roles.spoken(answer)), _words(said[mark:])
        return (bool(want) and len(want & got) < SAID_SHARE * len(want) and len(heard.split()) < CUT_WORDS
                and not (self.owed or self.calls))

    def called(self, call):
        name, args = call.get("name"), call.get("args") or {}
        request = str(args.get("request") or "")
        with self.lock:
            same = next((a for t, w, a in reversed(self.replies) if time.time() - t < REPEAT and _close(w, _words(request))), None)
            if name == "ask_sena" and same is not None:   # Live asks the brain what it just asked: the answer is not made twice
                self.owed.append([time.time(), same, bool(roles.SAY_LINK.search(request))])
        if name == "ask_sena" and same is not None:
            self.brained = True
            events.log("voice", "Live спросил мозг о том же ещё раз: отдаю тот же ответ", device=self.ear.device, request=request[:120])
            self.call_live(lambda live: live.answer(call["id"], "ask_sena", roles.spoken(same, links=bool(roles.SAY_LINK.search(request)))))
        elif name == "ask_sena":
            self.brained = True
            with self.lock:   # whose voice asked: the sound of this turn, taken now — the turn may end before the check
                frames = list(self.turn_frames)
            self.calls.add(call["id"])
            self.ear.send({"type": "thinking", "on": True})
            safety.background(self.ask_sena, str(args.get("request") or ""), call["id"], frames, what="Живой голос: вопрос мозгу")
        elif name == "finish":
            self.finish()

    def ask_sena(self, request, call_id=None, frames=()):
        """The brain answers (the fast voice path: the same memory, tools and chat history) and she says the answer herself,
        right away. Handed to Live to say, it came 8–23 s late or not at all (02.11.2026: he waited, gave up and stopped her):
        Live gets it silently, to know what was said. An answer that comes after the conversation ended is said to the
        microphone all the same (deliver_late)."""
        asked = time.time()
        try:
            if request.strip():
                # a turn under a second keeps the last voice check: fine for "and tomorrow?", not for a "yes" that sends a message
                answer = ag_sena.ask(config.OWNER_ID, request, fast=True, outside=self.whose_voice(frames), own=_spoken(frames) >= OWN_SPEECH)
            else:
                answer = "Не расслышала, повтори."
        except Exception as e:
            events.error("Живой голос: ответ мозга", e)
            answer = "Что-то сломалось, посмотри журнал."
        # the answer counts as being said before the call stops counting as open: the quiet he kept while she worked must not
        # close the conversation in the moment between (17:41, 02.11.2026 — closed "for silence" the second the answer came)
        with self.lock:
            over = self.over or self.live is None
            cancelled = call_id in self.cancelled
            if not over:
                if cancelled:   # Live gave up on the question (he spoke on), but he asked it: said when he is quiet
                    self.late.append((request, answer, asked, time.time()))
                else:
                    self.answered.append((asked, _words(request)))
                    self.replies.append((time.time(), _words(request), answer))
                    self.owed.append([time.time(), answer, bool(roles.SAY_LINK.search(request))])
                self.last_voice = time.time()
            self.calls.discard(call_id)
        self.ear.send({"type": "thinking", "on": bool(self.calls)})
        if over:
            self.deliver_late(request, answer)
        elif not cancelled:   # Live says it now; a link is read out only when he asked to hear it
            said = roles.spoken(answer, links=bool(roles.SAY_LINK.search(request)))
            self.call_live(lambda live: live.answer(call_id, "ask_sena", said))

    def say(self, answer, links=False, counted=False):
        """Her brain's answer in her voice, at once: after the phrase Live may be saying, as one more piece of her speech.
        counted — ask_sena already counted it in saying."""
        if not counted:
            self.saying += 1
        try:
            pcm = _voice_pcm(roles.speak(answer, settings.get("voice"), gemini.speech, links=links))
            until = time.time() + IDLE_WAIT
            while self.generating and time.time() < until:
                time.sleep(0.05)
            self.ear.send({"type": "said", "text": answer})
            for i in range(0, len(pcm), VOICE_RATE // 5):   # in pieces of 0.2 s: the app starts playing at once
                self.play(pcm[i:i + VOICE_RATE // 5])
            events.log("message_out", answer, channel="voice", live=True)
        except Exception as e:   # her voice didn't come together: the answer is in the chat, Live is told to say it
            events.error("Живой голос: ответ голосом", e)
            self.call_live(lambda live: live.tell(prompt.VOICE_LATE.format(request="", answer=roles.spoken(answer, links=links))))
        finally:
            self.saying -= 1

    def keep_turn(self, heard, said, frames):
        history.spoken(config.OWNER_ID, heard, said, outside=self.whose_voice(frames) if heard and frames else None)

    def deliver_late(self, request, answer):
        """The conversation is over and he didn't hear this answer: asked by voice, it is answered by voice. A new conversation
        on the same microphone says it when she is free; otherwise she says it to the microphone straight away; only when the
        app is gone (or her voice failed) does Telegram bring it as text (the chat's history has it already). Nothing while she
        is going down: the stop writes down what it cut."""
        if not (request.strip() and answer.strip()) or power.is_stopping():
            return
        now = self.ear.talk
        if now is not None and now is not self and not now.over and now.live is not None:
            with now.lock:
                now.late.append((request, answer, time.time(), time.time()))
            return
        safety.background(self.say_late, request, answer, what="Живой голос: поздний ответ")

    def say_late(self, request, answer):
        if not self.ear.say(answer):   # the chat has it anyway; Telegram only if he lets her write there
            api.notify(f"Ответ на то, что ты спросил голосом («{request[:120]}»):\n{answer}")

    def still_wanted(self, request, asked, ready):
        """A late answer is said only if it still answers something: not after the talk moved on, not when the owner asked the
        same again and already heard the answer (crewAI's reply queue validates a delivery right before speaking it)."""
        why = ("разговор ушёл дальше" if time.time() - ready > LATE_STALE else
               "Тема спросил снова и уже услышал ответ" if any(t > asked and _close(w, _words(request)) for t, w in self.answered) else "")
        if why:
            events.log("voice", f"поздний ответ не сказан: {why}", request=request[:120])
        return not why

    def finish(self):
        self.finishing = True

    def whose_voice(self, frames):
        """Why the request may not be the owner's own (convo.stranger): from the sound of the turn; a turn too short to tell
        ("yes", "and tomorrow?") keeps the last answer, and with no answer yet it is checked on what there is. The first turn
        after his own press of "talk" in his locked app teaches his voice (convo.stranger, teach)."""
        if _spoken(frames) >= OWN_SPEECH or not self.voice_checked:
            wav = _wav(frames or self.pending)
            try:
                verdict = convo.stranger(wav, teach=self.ear.button and self.turns == 0)
            finally:
                os.unlink(wav)
            if verdict != convo.TOO_SHORT or not self.voice_checked:   # too little clean speech: the last check stands
                self.outside = verdict
            self.voice_checked = True
            if verdict == convo.NOT_LEARNED:
                convo.tell_unlearned()
        return self.outside

    def tick(self):
        now = time.time()
        played = now > self.speaking_until + self.tail()
        if self.late and played and not self.generating and self.since_loud * STEP >= LATE_QUIET:
            request, answer, asked, ready = self.late.pop(0)
            if self.still_wanted(request, asked, ready):
                self.saying += 1
                safety.background(self.say, answer, False, True, what="Живой голос: поздний ответ")
        if self.owed and not self.generating and now - self.owed[0][0] > LIVE_WAIT:   # Live stayed silent: her TTS voice says it
            _, answer, links = self.owed.pop(0)
            events.log("voice", "Live не начал говорить ответ: говорю сама", device=self.ear.device)
            self.saying += 1
            safety.background(self.say, answer, links, True, what="Живой голос: ответ голосом")
        if self.finishing and played and not self.generating and not (self.calls or self.late or self.saying or self.owed or self.answer_now):   # what he asked for is answered first
            self.end("попрощались")
        elif played and not (self.calls or self.late or self.saying or self.owed or self.answer_now or self.generating) and now - max(self.last_voice, self.speaking_until) > FOLLOW_UP:
            self.end("тишина")
        elif now - self.started > TALK_MAX:
            self.end("долгий разговор")
        elif power.is_stopping():
            self.end("Сена выключается")

    # ---- the old way: whisper, the brain, Gemini TTS ----
    def answer_classic(self, frames):
        wav = _wav(frames)
        try:
            text = local.transcribe(wav, best=False, private=True, journal=False)
            reading = _reading(text)
            if self.over:   # whisper's first reading of the name already dropped it
                return
            if not self.sure and reading == "no":   # an unsure detector: clearly other words turn her away (as in confirm)
                self.reject(None, "")
                return
            self.sure = True
            self.began()
            self.ear.send({"type": "thinking", "on": True})
            said = convo.command(wav, text)["say"]
            self.ear.send({"type": "thinking", "on": False})
            self.ear.send({"type": "said", "text": said})
            pcm = _voice_pcm(roles.speak(said, settings.get("voice"), gemini.speech))
            self.speaking_until = time.time() + len(pcm) / VOICE_RATE
            for i in range(0, len(pcm), VOICE_RATE // 5):   # in pieces of 0.2 s: the app starts playing at once
                self.ear.send(pcm[i:i + VOICE_RATE // 5])
            time.sleep(max(0.0, self.speaking_until - time.time()) + self.tail())   # the microphone waits until she has finished
        except Exception as e:
            events.error("Голосовая команда", e)
        finally:
            if os.path.exists(wav):
                os.unlink(wav)
        self.end("ответила без живого голоса")

    def end(self, reason, quiet=False):
        """Close the conversation (once; True if this call closed it). quiet — the app never heard it begin. Answers Live gave
        up on and never got to say go to Telegram, unless he asked again and heard it."""
        with self.lock:
            if self.over:
                return False
            self.over = True
            late, self.late = self.late, []
            owed, self.owed = self.owed, []
        if self.live is not None:
            self.live.close()
        for request, answer, asked, _ in late:
            if not any(t > asked and _close(w, _words(request)) for t, w in self.answered):
                self.deliver_late(request, answer)
        for _, answer, _ in owed:   # handed to Live and never begun (his «стоп», a broken link): said to the microphone now
            self.deliver_late("…", answer)
        if quiet:
            return True
        self.ear.send({"type": "end", "reason": reason})
        if self.ear.button:   # the button's one conversation: the server hangs up
            getattr(self.ear.send, "close", lambda: None)()
        events.log("voice", "разговор окончен", device=self.ear.device, reason=reason, how=self.how, turns=self.turns,
                   seconds=round(time.time() - self.started, 1), usd=round(self.live.usd, 5) if self.live is not None else 0.0)
        return True


FUNCTIONS = {"ask_sena": Talk.ask_sena, "finish": Talk.finish}


def open_live(timeout=OPEN_WAIT):
    return gemini.Live(prompt.VOICE_LIVE, TOOLS, waiting=("ask_sena",)).open(timeout)


class Warm:
    """A conversation opened ahead of time: her name is answered without waiting for Google to connect (about a second sooner).
    One for all the apps, kept only while some microphone is connected. Unused it costs nothing — Google counts only what is
    heard and said — and it is renewed every WARM_FRESH seconds, well before Google's ten-minute limit on a connection."""

    def __init__(self):
        self.live, self.at, self.users = None, 0.0, 0
        self.lock = threading.Lock()
        self.changed = threading.Event()
        self.thread = None

    def join(self):
        with self.lock:
            self.users += 1
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self.keep, daemon=True)
                self.thread.start()
        self.changed.set()

    def leave(self):
        with self.lock:
            self.users = max(0, self.users - 1)
        self.changed.set()

    def take(self):
        """The conversation opened ahead, if there is a fresh one (then a new one is opened for the next time)."""
        with self.lock:
            live, at, self.live = self.live, self.at, None
        self.changed.set()
        if live is not None and time.time() - at < WARM_FRESH:
            return live
        if live is not None:
            live.close()
        return None

    def keep(self):
        retry, failed = 0.0, 0
        while True:
            with self.lock:
                closed = self.live is not None and any(kind == "end" for kind, _ in self.live.receive(timeout=0))
                if closed:   # Google closed it while it waited
                    self.live.close()
                    self.live = None
                users, live, at = self.users, self.live, self.at
            if users <= 0 or not hearing_on():   # no microphone, or no listening: nothing is kept open
                with self.lock:
                    stale, self.live = self.live, None
                if stale is not None:
                    stale.close()
                if users <= 0:
                    return
            elif (live is None or time.time() - at >= WARM_FRESH) and time.time() >= retry:
                try:
                    fresh = open_live()
                except Exception as e:   # no network or no money: the conversation opens when the name is said, or the old way answers
                    if not failed:
                        events.error("Живой голос: не открылся заранее", e)
                    failed += 1
                    retry = time.time() + min(WARM_RETRY[0] * 2 ** (failed - 1), WARM_RETRY[1])
                else:
                    failed, retry = 0, 0.0
                    with self.lock:
                        old, self.live, self.at = self.live, fresh, time.time()
                    if old is not None:
                        old.close()
            with self.lock:   # sleep until it's time to renew or to try again; a taken one or a microphone wakes it sooner
                due = self.at + WARM_FRESH if self.live is not None else (retry or time.time() + 5)
            self.changed.wait(timeout=min(5.0, max(0.05, due - time.time())))
            self.changed.clear()


warm = Warm()


# ---------- the server ----------
class Link:
    """What goes back to the app: her voice (bytes) and events (dicts, as JSON). Several threads write: one at a time."""

    def __init__(self, connection):
        self.connection, self.lock = connection, threading.Lock()

    def __call__(self, message):
        with self.lock:
            try:
                self.connection.send(message if isinstance(message, bytes) else json.dumps(message, ensure_ascii=False))
            except (ConnectionClosed, OSError):
                pass   # the app has gone: the stream's own thread sees it and closes the conversation

    def close(self):
        threading.Thread(target=self.connection.close, args=(1000, "разговор окончен"), daemon=True).start()   # not on Live's own thread


def _query(path):
    return {k: v[0] for k, v in parse_qs(urlparse(path).query).items()}


def _admit(connection, request):
    # the same door as the app API: the password, the lockout for guessing; switched off or without hearing — no stream
    ip, path = connection.remote_address[0], urlparse(request.path).path
    if path != "/ear":
        return connection.respond(404, "не найдено\n")
    reason = server.access(ip, server.given(request.headers, _query(request.path)), request.headers.get("X-Sena-Zone", ""))
    if reason:
        server.denied(ip, path, reason)
        return connection.respond(403, (server.LOCKED if reason == server.LOCKED else "нет доступа") + "\n")
    if power.is_off() or power.is_stopping():
        return connection.respond(409, power.refusal("слушать") + "\n")
    if not settings.get("use_hearing") and _query(request.path).get("mode") != "talk":   # the button isn't background hearing
        return connection.respond(409, "слух выключен в интеграциях\n")
    return None


def _serve(connection):
    query = _query(connection.request.path)
    device = query.get("device", "app")[:20]
    ear = Ear(Link(connection), device, button=query.get("mode") == "talk")
    ear.send({"type": "ready"})
    if ear.button:
        ear.start([], sure=True, how="кнопка", pressed=True)
    events.log("app", "живой голос: микрофон подключён", source=device)
    try:
        for message in connection:
            if isinstance(message, bytes):
                ear.feed(message)
            else:
                ear.command(message)
            if not ear.allowed():   # hearing turned off or Sena stopping: the app will knock again and be told why
                connection.close(1000, "слух выключен")
                break
    except ConnectionClosed:
        pass
    except Exception as e:
        events.error("Живой голос", e)
    finally:
        ear.close()
        events.log("app", "живой голос: микрофон отключён", source=device)


def start(host="0.0.0.0"):
    """The live voice beside the app server. Switched off (the screen) it listens too, only to refuse in words: Sena doesn't
    listen then. host — every interface (the phone streams over the network); a test listens on this Mac only."""
    try:
        listener = serve(_serve, host, config.VOICE_PORT, process_request=_admit, compression=None, max_size=2 ** 20)
    except OSError as e:
        events.error(f"Живой голос: порт {config.VOICE_PORT} занят", e)
        print(f"Живой голос: порт {config.VOICE_PORT} занят ({e})")
        return None
    threading.Thread(target=listener.serve_forever, daemon=True).start()
    print(f"Живой голос: порт {config.VOICE_PORT}")
    return listener
