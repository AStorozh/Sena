"""Ambient listening: phrases of speech from the apps' live stream (voice.py) -> a command or an overheard conversation.
Everything stays on the Mac. voice.py cuts the phrases and finds the name; here: 1) a phrase with the name, when the live
voice is out of reach, is recognized again with the accurate model (the owner's words matter) and carried out as a command;
2) a phrase without it — the accurate pass runs in the background, voice fingerprint -> who spoke, lines accumulate into a
conversation; 3) ag_ears analyzes the finished conversation with the home model."""
import os
import re
import threading
import time
import uuid

from sena import config
from sena.agents import ag_ears
from sena.agents.ag_sena import agent as ag_sena
from sena.core import events, local, moment, safety
from sena.memory import heard, people
from sena.phone import speech

IDLE_GAP = 180      # a conversation is considered finished after 3 minutes of silence
MIN_VOICE_SEC = 2   # we don't remember a voice from a shorter chunk of speech: the fingerprint comes out unreliable
CHECK_SEC = 1.5     # s of clean speech a check of the owner's voice needs: a shorter print misses him about as often as not
TOO_SHORT = "слишком коротко, чтобы узнать голос"
NOT_LEARNED = "голос Темы ещё не выучен (выучится по его голосовым)"
LEARNING = "голос Темы выучивается с этого хода: сам ход ещё не в счёт"
ACCURATE_MOST = 20  # accurate passes waiting at once: past it a phrase keeps its fast text (a room talks faster than the Mac reads)
UNLEARNED = ("Я пока не узнаю твой голос (его отпечатков у меня нет), поэтому голосом нельзя подтвердить отправку или поручить "
             "работу на компьютере. Пришли мне 2–3 голосовых в Телеграм или поговори со мной кнопкой «поговорить» в телефоне — выучу сама.")
_lock = threading.Lock()
_accurate = 0       # accurate passes waiting now
_outran = False     # the last phrase found the queue full
_told_unlearned = False

buffer = []          # current lines of the unfinished conversation
strangers = {}       # unfamiliar voices in this conversation: "gN" -> [fingerprints]. They enter the people database only after analysis
last_time = 0

# words that never carry meaning: if a conversation consists only of these, there's nothing to analyze
FILLER = set("ага угу да нет ну ок окей так вот это то а и но я ты мы он она они вы что как же ли бы не "
             "мм хм эм э ой ах ох эх блин ладно короче типа слышь давай всё все щас сейчас там тут "
             "ok okay yeah yes no yep nope um uh hmm oh ah so like well the a i you it and but".split())


def command(wav, text):
    """A phrase with the name is a command: the same sound once more with the accurate model ("remind me to call Igor" must come
    out right), whose voice it is, and the fast brain's answer to speak. The caller deletes the wav."""
    try:
        accurate = local.transcribe(wav, best=True, purpose="голосовая команда")
        if accurate:
            text = accurate
    except local.Unavailable as e:
        events.error("Точное распознавание команды", e)
    m = speech.WAKE.match(text)   # no match: the name came out garbled, the whole phrase is the command
    outside = stranger(wav)
    if outside == NOT_LEARNED:
        tell_unlearned()
    said = text[m.end():].strip() if m else text.strip()
    if not said:
        return {"command": True, "text": text, "say": "Да?"}
    events.log("message_in", said, source="voice", stranger=outside or None)
    try:
        answer = ag_sena.ask(config.OWNER_ID, said, fast=True, outside=outside)   # using the fast model
    except Exception as e:
        answer = "Что-то сломалось, посмотри журнал."
        events.error("Голосовая команда", e)
    events.log("message_out", answer, channel="voice")
    return {"command": True, "text": said, "say": answer}


def overheard(wav, text):
    """Speech without the name: in the background, the accurate words and whose voice, into the conversation (the wav is deleted).
    While ACCURATE_MOST phrases already wait for the accurate pass, a new one keeps its fast text: the queue no longer grows
    without end (a TV, two people talking — a thread and a wav per phrase, and the lines further and further behind)."""
    global _accurate, _outran
    with _lock:
        accurate = _accurate < ACCURATE_MOST
        if accurate:
            _accurate += 1
        first, _outran = not accurate and not _outran, not accurate
    if first:   # once a spell: the room outran the accurate pass
        events.log("voice", f"слух: в очереди точного распознавания {ACCURATE_MOST} фраз — следующие остаются с быстрым текстом")
    safety.background(_ingest, wav, text, accurate, what="Разбор речи")


def tell_unlearned():
    """A voice turn couldn't be his because his voice isn't learned: he hears why and how to fix it, once in a run (his prints
    were lost once, and every voice turn was a stranger's without a word)."""
    global _told_unlearned
    with _lock:
        told, _told_unlearned = _told_unlearned, True
    if not told:
        safety.background(ag_sena.notify_owner, UNLEARNED, what="Голос Темы не выучен")


def stranger(wav, teach=False):
    """None — the command is in the owner's own voice; otherwise why it can't count as his (then the task is treated
    like someone else's text: the terminal and code agents wait for his "yes"). TOO_SHORT — too little clean speech to tell.
    teach — his own press of "talk" in his locked app began this turn: his voice is learned from it (as from his voice
    messages and the Mac's recorder), unless it is a known other person's or plainly not his."""
    try:
        vec, seconds = local.voice_print(wav)
    except Exception as e:
        events.error("Голос команды", e)
        return "голос команды не разобран"
    if seconds < CHECK_SEC:
        return TOO_SHORT
    owner = people.is_owner(vec)
    if teach and owner is not False and seconds >= people.OWNER_MIN_SEC and people.identify_voice(vec)[0] is None:
        people.remember_owner_voice(vec)
        if owner is None:   # his first sample is this very turn: the next ones are checked against it, this one isn't his yet
            return LEARNING
    elif owner and seconds >= people.OWNER_MIN_SEC:   # recognised: the print joins his, so his voice is learned on this very microphone too
        people.remember_owner_voice(vec)
    if owner is None:
        return NOT_LEARNED
    return None if owner else "голос не похож на голос Темы"


def who_is(vec, seconds):
    # the owner, a known person from the database, otherwise an unfamiliar voice in this conversation (gN)
    if people.owner_like(vec):   # his own words around the house are his, never a stranger who gets a card
        return people.OWNER, "Тема"
    person, _ = people.identify_voice(vec)
    if person:
        if seconds >= MIN_VOICE_SEC:
            people.remember_voice(person["id"], vec)   # reinforce the fingerprint
        return person["id"], person.get("name") or person["id"]
    with _lock:
        key, _ = people.match(vec, list(strangers.items()))
        if not key:
            if seconds < MIN_VOICE_SEC:
                return "g0", "неизвестный"     # too short to distinguish voices
            key = f"g{len(strangers) + 1}"
            strangers[key] = []
        if seconds >= MIN_VOICE_SEC:
            strangers[key] = (strangers[key] + [vec])[-people.MAX_VOICES:]
    return key, "неизвестный"


def _ingest(wav, text, accurate=True):
    global last_time, _accurate
    try:
        try:   # the line kept for good deserves the accurate model; the fast text stays only if it can't run
            if accurate:
                text = local.transcribe(wav, best=True, private=True, journal=False) or text
        except local.Unavailable:
            pass
        finally:
            if accurate:
                with _lock:
                    _accurate = max(0, _accurate - 1)
        vec, seconds = local.voice_print(wav)
    finally:
        os.unlink(wav)
    flush_idle()                          # the previous conversation ended — analyze it
    pid, who = who_is(vec, seconds)
    entry = {"id": uuid.uuid4().hex[:8], "time": moment.now(), "tz": moment.offset(), "pid": pid, "who": who, "text": text}
    events.log("heard", "", speaker=who, pid=pid, chars=len(text), seconds=round(seconds, 1))   # the actual words of someone else's speech go only into the raw log and the registry
    with _lock:
        buffer.append(entry)
        last_time = time.time()
    heard.append(entry)   # the raw log stays on the Mac only; the registry finds the line when asked for it


def worth_analyzing(entries):
    # a free check before involving the model: is there any meaning at all in the conversation
    words = re.findall(r"[а-яёa-z]+", " ".join(e["text"] for e in entries).lower())
    return len([w for w in words if w not in FILLER]) >= 6


def _flush():
    # called under _lock: take the finished conversation and analyze it in the background
    global buffer, strangers
    entries, voices = buffer, strangers
    buffer, strangers = [], {}
    if entries and worth_analyzing(entries):
        safety.background(ag_ears.analyze, entries, voices, what="Разбор разговора")


def flush_idle():
    # if the conversation has been quiet for a while — analyze it
    with _lock:
        if buffer and time.time() - last_time > IDLE_GAP:
            _flush()


def _watch():
    # check once a minute whether the conversation has gone quiet
    while True:
        time.sleep(60)
        try:
            flush_idle()
        except Exception as e:
            events.error("Слух, проверка тишины", e)


def start():
    threading.Thread(target=_watch, daemon=True).start()
