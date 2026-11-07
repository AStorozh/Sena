"""The agent asks Tema for help (captcha, account login, SMS code) and waits for an answer.
The question goes out through the communication channel that set_sender registered (Telegram), and deliver hands his answer
to the agent that asked. Every question has its own slot: two agents asking at once each get their own answer (one shared
slot let the second question replace the first, and the answer to the first was lost — the A2A study). With one question
waiting, his next message is its answer; with several, the answer names its question ("[ab12] …"), or the coordinator, who
sees them all, passes it on (answer_agent) — the caller owns the human, as in A2A. An answer the coordinator passes on in
its own words comes marked as such: it can't stand for his "yes" to an irreversible button (ag_web/guard.approve).
The owner's stop reaches a task while it waits for him."""
import re
import threading
import time
import uuid

from sena.core import cancel, events, roles, status
from sena.core.tools import Failure

_send = None
_asks = {}   # id -> {"id", "question", "image", "time", "who", "answer", "own", "done"}: the questions waiting for Tema now
_lock = threading.Lock()
NAMED = re.compile(r"^\s*\[?(?:вопрос\s*)?([0-9a-f]{4})\]?\s*[:,.—-]?\s+", re.I)   # "[ab12] ответ", "ab12: ответ"
OWN = "Тема ответил: "                        # his own words, as he wrote or said them
PASSED = "Тема ответил (передала Сена): "     # the coordinator's retelling of his answer


PLAIN = """Перепиши вопрос агента к Теме так, чтобы его понял человек без технического опыта.
Одно-два коротких предложения: что случилось и что нужно от Темы (выбрать, ответить, сделать). Без команд, флагов, путей, имён
инструментов и режимов; незнакомое слово — простым сравнением. Смысл и варианты ответа не меняй, ничего не добавляй от себя.
Ответ — только сам вопрос."""


def plain(question):
    """An agent's question as Tema can read it: Claude Code asked him about «--permission-mode bypassPermissions», and he
    understood nothing. A failed retelling keeps the question as it was: he is asked either way."""
    try:
        text = str(roles.ask("plain", PLAIN, question, purpose="вопрос Теме простыми словами", max_tokens=300) or "").strip()
    except Exception as e:
        events.error("Вопрос простыми словами", e)
        return question
    return text or question


def set_sender(func):
    global _send
    _send = func   # func(question, image_jpeg)


def ask(question, image=None, timeout=180):
    if not _send:
        return Failure("Спросить Тему сейчас нельзя: канала связи с ним нет")
    question = plain(question)   # retold before it is shown anywhere: the app's card and the message read the same plain words
    item = {"id": uuid.uuid4().hex[:4], "question": question, "image": image, "time": time.time(), "who": getattr(status.local, "who", None),
            "answer": None, "own": True, "done": threading.Event()}
    with _lock:
        others = len(_asks)
        _asks[item["id"]] = item
    try:
        # another question is already waiting: this one says which it is, so his answer can name it
        _send(f"[вопрос {item['id']}] {question}" if others else question, image)
        end = time.monotonic() + timeout
        while not item["done"].wait(min(1.0, max(0.0, end - time.monotonic()))):
            cancel.check_stop()   # stopped by the owner: the task doesn't keep waiting for an answer nobody needs
            if time.monotonic() >= end:
                return Failure(f"Тема не ответил за {max(1, timeout // 60)} мин")   # no answer is no answer, not a tool that worked (AG2)
        return (OWN if item["own"] else PASSED) + item["answer"]
    finally:
        with _lock:
            _asks.pop(item["id"], None)


def waiting():
    """The questions waiting for Tema, oldest first: [{"id", "question", "who", "has_image", "time"}]."""
    with _lock:
        items = sorted(_asks.values(), key=lambda q: q["time"])
    return [{"id": q["id"], "question": q["question"], "who": q["who"], "has_image": q["image"] is not None, "time": q["time"]} for q in items]


def pending():
    """The question the console shows (the oldest one), without the image — only whether there is one; how many wait in all."""
    items = waiting()
    return {**items[0], "count": len(items)} if items else None


def image(qid=None):
    """The picture of a waiting question (the oldest when no id is named), or None."""
    with _lock:
        items = sorted(_asks.values(), key=lambda q: q["time"])
        found = _asks.get(qid) if qid else (items[0] if items else None)
        return found["image"] if found else None


def deliver(text, qid=None, own=True):
    """Tema's message as the answer to a waiting question: the one named (qid, or "[ab12]" at its start), or the only one
    waiting. Returns that question ({"id", "question", "who"}), or None — nothing to answer, or several wait and it doesn't say
    which (the coordinator decides then). own=False — the coordinator's words for his answer, not his own."""
    with _lock:
        if not qid and len(_asks) > 1:
            named = NAMED.match(text)
            if named and named.group(1).lower() in _asks:
                qid, text = named.group(1).lower(), text[named.end():].strip() or text
        item = _asks.get(qid) if qid else (next(iter(_asks.values())) if len(_asks) == 1 else None)
        if not item or item["done"].is_set():
            return None
        item["answer"], item["own"] = text, own
        item["done"].set()
    return {"id": item["id"], "question": item["question"], "who": item["who"]}
