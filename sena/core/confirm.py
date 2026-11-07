"""Confirmations: anything irreversible or sensitive (a message to someone, a mail, a change on a site, a secret shown or granted)
is prepared first and executed only after the owner's "yes" — on the following turn of the conversation, never the same one,
or by his own click on the control panel. One place for every such step; the action itself is a closure that runs only here."""
import hashlib
import re
import threading
import time
import uuid

from sena.core import events, status
from sena.core.cancel import Stopped, finish_task, new_task
from sena.core.tools import Failure

TTL = 900   # a prepared action waits for a "yes" for a quarter of an hour
pending = {}   # id -> {"name", "text", "turn", "time", "run", "tid", "ttl", "later"}
_lock = threading.Lock()
finished_hook = None   # who keeps the task of a pressed action in the task journal: fn(task) (set by the agents' layer)


def _alive(p):
    return time.time() - p["time"] <= p.get("ttl", TTL)


def hold(name, text, run, ttl=None):
    """Prepare an action; returns its short id for the owner's confirmation. ttl — seconds it may wait instead of TTL; such an
    action may be confirmed on any later turn (a report he reads in an hour), still only by his own "yes" or his click."""
    sid = uuid.uuid4().hex[:4]
    with _lock:
        for old in [k for k, p in pending.items() if not _alive(p)]:
            del pending[old]   # anything not confirmed within the allotted time is no longer needed
        pending[sid] = {"name": name, "text": text, "turn": getattr(status.local, "turn", 0), "time": time.time(), "run": run,
                        "tid": (status.current() or {}).get("id"), **({"ttl": ttl, "later": True} if ttl else {})}
    return sid


def keep(tid, seconds):
    """What the task tid prepared waits as long as its question to the owner does, and his "yes" may come on any later turn:
    a long work asks and he answers in an hour (Dify keeps a human-input form 36 hours). Returns [(id, name)], for the buttons."""
    with _lock:
        ids = [k for k, p in pending.items() if tid and p.get("tid") == tid and _alive(p)]
        for k in ids:
            pending[k].update(ttl=time.time() - pending[k]["time"] + seconds, later=True)
        return [(k, pending[k]["name"]) for k in ids]


# a failure that surely reached nobody (the name not found, the connection refused, the service said no or "wait N seconds"),
# and one that may have (no answer in time, a connection dropped midway, the server's own error)
NOT_DELIVERED = re.compile(r"адрес не найден|соединение отклонено|сертификат|нет связи|connection ?refused|failed to resolve|nodename nor servname|"
                           r"name or service not known|flood ?wait|a wait of \d+ seconds|too many requests|ответил 4\d\d|сена выключена|"
                           r"notsignedin|вход в google|oauth-клиента", re.I)   # Google not signed in: nothing was sent, «да» after the sign-in
MAYBE_DELIVERED = re.compile(r"не ответил|оборвалось|timed? ?out|reset|aborted|ответил 5\d\d|неизвестн|могло выполниться", re.I)
TRIES = 3   # presses of «Да» one prepared action gets when it keeps failing before it reached anyone


def _not_delivered(failure):
    return bool(NOT_DELIVERED.search(f"{type(failure).__name__}: {failure}")) and not MAYBE_DELIVERED.search(str(failure))


def _again(sid, p, failure):
    """A failed action that surely went nowhere waits under its id once more: «Да» repeats it, instead of "there is no such
    action any more" (Flowise resumes a run after an error by retrying the step that failed). One that may have gone isn't."""
    if not sid or not _not_delivered(failure) or p.get("tries", 0) + 1 >= TRIES:
        return None
    # his «да» may come on any later turn: after a press (a thread of no conversation, turn 0) it was "a turn passed without
    # confirmation", and the promise "«Да» ещё раз" couldn't be kept by words
    turn = getattr(status.local, "turn", 0) or p["turn"]
    with _lock:
        pending[sid] = {**p, "time": time.time(), "turn": turn, "later": True, "tries": p.get("tries", 0) + 1}
    return Failure(f"Не получилось: {str(failure)[:300]}. Ничего не ушло; действие ждёт [{sid}]: «Да» ещё раз — повторю.")


def _run(p, sid=None):
    """Carry out a confirmed action with its trace in the journal: "started" before, "done" after, under the task that
    prepared it. A restart between the two leaves only "started": the next turn knows the outcome is unknown and checks
    before repeating — a message is never sent twice blindly (LangGraph's first-write-wins writes, the idea of an effects log)."""
    # this action, not its words: the same message sent twice in a day has two starts and two ends — with one key between them,
    # a fall in the middle of the second looked finished (the first one's "done") and the owner never heard of it
    key = hashlib.sha1(f"{sid}\n{p['time']}\n{p['name']}\n{p['text']}".encode()).hexdigest()[:12]
    events.log("effect", f"{p['name']}: {p['text']}"[:500], key=key, state="start", tid=p.get("tid"))
    try:
        result = p["run"]()
    except Exception as e:   # it failed and said so: a known outcome, not "interrupted by a shutdown" after the next start (n8n)
        events.log("effect", f"{p['name']}: не удалось — {str(e)[:300]}", key=key, state="failed", tid=p.get("tid"))
        kept = _again(sid, p, e)
        if kept is None:
            raise
        return kept
    failed = isinstance(result, Failure)
    events.log("effect", f"{p['name']}: {'не удалось — ' if failed else ''}{str(result)[:300]}", key=key, state="failed" if failed else "done", tid=p.get("tid"))
    return (_again(sid, p, result) or result) if failed else result


YES = re.compile(r"^\W*(да|ага|угу|ок|окей|хорошо|давай|можно|конечно|верно|подтверждаю|согласен|согласна|отправляй|отправь|шли|пиши|"
                 r"применяй|примени|публикуй|делай|жми|го|yes|yep|ok|okay|sure|go)\b", re.I)
NOT_YES = re.compile(r"\b(не|нет|стоп|подожди|погоди|отмена|отмени|но|позже|потом|no|don't|stop|wait|but|later)\b", re.I)


def _not_owners_yes():
    """Why this turn's words can't confirm, or None. The model writes confirm_send itself, so its "the owner said yes" is checked
    here (Microsoft Agent Framework accepts an approval only as the user's own act): his own words this turn — not a background
    turn, not someone else's voice — saying yes in their first sentence, before any foreign text was read this turn."""
    said = getattr(status.local, "said", None)
    if not said:
        return "в этом ходе нет слов Темы (фоновая задача или чужой голос): подтвердить может только он — кнопкой «Да» или своим «да»"
    if status.outside():
        return "в этом ходе уже прочитан чужой текст: пусть Тема подтвердит кнопкой «Да» под вопросом"
    first = re.split(r"[.!?;\n]", said.strip(), maxsplit=1)[0]
    if not YES.match(said) or NOT_YES.search(first):
        return "Тема не сказал на это прямого «да»: спроси ещё раз одной фразой"
    return None


def confirm(sid):
    """The owner said "yes" in the conversation: (done, text, item). Only on the turn right after the question (or any later
    turn for what a long work asked him about), and only on his own words that say yes."""
    with _lock:
        p = pending.get(sid)
        if not p or not _alive(p):
            return False, "Такого сообщения нет: истекло время или Сена перезапускалась — подготовь заново", None
        turn = getattr(status.local, "turn", 0)
        if turn <= p["turn"]:
            return False, "Нельзя подтвердить в том же ходе: сначала спроси Тему и дождись его ответа", None
        if turn > p["turn"] + 1 and not p.get("later"):
            del pending[sid]
            return False, "Прошёл ход без подтверждения: это уже не ответ на тот вопрос. Подготовь заново и спроси ещё раз", None
        why = _not_owners_yes()
        if why:
            p["turn"] = turn   # asked again now: his next own "да", or the button, confirms it
            return False, f"Не подтверждено: {why}.", p
        del pending[sid]
    return True, _run(p, sid), p


def confirm_now(sid):
    """The owner's own click (a button in Telegram or the apps) is the "yes" itself: the turn rule doesn't apply. Outside a
    task the action runs as a task of its own on the presser's thread: a coding job or the computer agent behind a button ran
    in none — not in «Задачи», out of «Стоп»'s reach, past the task's money brake, its costs under no task."""
    with _lock:
        p = pending.pop(sid, None)
    if not p:
        return "Такой отправки уже нет"
    events.log("tool", f"подтверждено с пульта: {p['name']}", name="confirm", args={"id": sid})
    if status.current():   # pressed inside a task already: the action is that task's
        return _run(p, sid)
    new_task()
    status.begin(f"По кнопке «Да»: {p['name']}")
    task = status.current()
    task["prepared_by"] = p.get("tid")   # the task that asked: its work, its review
    answer = "Ошибка"
    try:
        result = _run(p, sid)
        answer = str(result)
        return result
    except Stopped as e:   # the owner's «Стоп» reached it: his own choice, not a failure
        answer = str(e)
        return answer
    except Exception as e:
        answer = f"Не удалось: {str(e)[:300]}"
        raise
    finally:
        done = status.end(answer)
        finish_task(task["id"])
        if done and finished_hook:
            try:
                finished_hook(done)
            except Exception as e:
                events.error("Задача по кнопке «Да»", e)


def cancel(sid):
    with _lock:
        p = pending.pop(sid, None)
    if not p:
        return "Такой отправки уже нет"
    events.log("tool", f"отменено с пульта: {p['name']}", name="cancel", args={"id": sid})
    return f"Отменено: {p['name']}"


def decline(sid):
    """The owner said "no" in the conversation («нет», «не надо», «отмена»): the prepared action is dropped, and with it its
    buttons and cards — before, only a press could, and a stray tap on an old «Да» still sent it. Only on his own words this turn:
    a page or a mail read in a turn can't take back what he is about to confirm."""
    if not getattr(status.local, "said", None):
        return Failure("в этом ходе нет слов Темы: отменить подготовленное может только он — словом или кнопкой «Нет»")
    with _lock:
        p = pending.pop(sid, None)
    if not p:
        return Failure("Такого действия уже нет: истекло время, его сделали или отменили")
    events.log("tool", f"отменено словом Темы: {p['name']}", name="decline", args={"id": sid})
    return f"Отменено: {p['name']}"


def effects(tid):
    """What a task really did to the world through confirmations: ["сделано: …" | "начато, исход неизвестен: …"]."""
    rows = events.search("all", kind="effect", tid=tid, limit=60) if tid else []
    done = {r.get("key") for r in rows if r.get("state") == "done"}
    failed = {r.get("key") for r in rows if r.get("state") == "failed"}
    return [("сделано: " if r.get("key") in done else "не удалось: " if r.get("key") in failed else "начато, исход неизвестен: ") + r["text"]
            for r in rows if r.get("state") == "start"]


def unfinished(hours=24):
    """Confirmed actions that started but never finished (Sena fell in between) and the owner hasn't heard of yet: [text]."""
    rows = events.search(since=time.time() - hours * 3600, kind="effect", limit=500)
    closed = {r.get("key") for r in rows if r.get("state") in ("done", "told", "failed")}
    out, seen = [], set()
    for r in rows:
        if r.get("state") == "start" and r.get("key") not in closed and r.get("key") not in seen:
            seen.add(r.get("key"))
            out.append((r.get("key"), r["text"]))
    return out


def told(keys):
    """The owner has been told about these unfinished actions: not again after the next restart."""
    for key in keys:
        events.log("effect", "Теме сказано: исход неизвестен", key=key, state="told")


def prepared_in(turn):
    """What was prepared on this turn of the conversation (its answer carries the question): [(id, name)] for the buttons under it."""
    with _lock:
        return [(k, p["name"]) for k, p in pending.items() if p["turn"] == turn and _alive(p)]


def waiting():
    """What is waiting for a "yes" right now (for the control panel). turn — the conversation's turn it was asked on, later —
    it may be answered on any later turn (what is "the question just asked" and what waits for hours)."""
    with _lock:
        return [{"id": k, "name": p["name"], "text": p["text"], "time": p["time"], "turn": p["turn"], "later": bool(p.get("later"))}
                for k, p in list(pending.items()) if _alive(p)]
