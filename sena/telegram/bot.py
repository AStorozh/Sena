"""Sena's Telegram bot: receives the owner's messages (text, voice, photo, files, forwards), passes them to the coordinator, replies.
Each message is a separate task in its own thread, so several can be given at once. An album of several photos is one task, and
so is a comment with the forwards it came with (Telegram sends «forward with a comment» as messages a moment apart).
While Sena is off, the screen answers him that she is off (answer_while_off) and leaves his messages for the core."""
import re
import threading
import time

from sena import config
from sena.agents.ag_sena import agent as ag_sena
from sena.agents.ag_sena import tools as sena_tools
from sena.core import attachments, confirm, events, llm, moment, roles, safety, settings, status
from sena.core.safety import external
from sena.core.tools import Failure
from sena.google import media as gemini
from sena.memory import people
from sena.telegram import api
from sena.world import uptime

ALBUM_WAIT = 1.5   # how long to wait for the rest of the album's photos
JOIN_WAIT = 1.0    # s: his comment and the forwards it came with arrive this close together, and become one task
LATE = 120         # s: a message older than this was written while she didn't answer (off, fallen, the Mac without network)
LONG = 30          # s: an answer that took longer comes as a message of its own — an edit of «Приняла» far up the chat makes no sound
STEP_SHOWN = 300   # characters of one progress step: a long one (a transcript) would push the steps out of the message
_albums = {}
_albums_lock = threading.Lock()
_joining = {}      # chat -> [the messages one task takes, when the last of them came]: the forwards of a comment join it
SPOKEN = ("voice", "audio", "video_note")   # things that get transcribed from speech


def _person(user):
    name = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p) or "без имени"
    return name + (f" (@{user['username']})" if user.get("username") else "")


def _from_origin(origin):
    # who a message came from, by Telegram's MessageOrigin (a forward, a reply to a message of another chat)
    kind = origin.get("type")
    if kind == "user":
        return _person(origin.get("sender_user", {}))
    if kind == "hidden_user":
        return origin.get("sender_user_name", "скрытого отправителя")
    if kind == "chat":
        return "чата «" + origin.get("sender_chat", {}).get("title", "") + "»"
    if kind == "channel":
        return "канала «" + origin.get("chat", {}).get("title", "") + "»"
    return "неизвестного отправителя"


def _forwarded_from(message):
    # who a forwarded message is from; None if it isn't forwarded
    if message.get("forward_origin"):
        return _from_origin(message["forward_origin"])
    if message.get("forward_from"):
        return _person(message["forward_from"])
    if message.get("forward_sender_name"):
        return message["forward_sender_name"]
    if message.get("forward_from_chat"):
        return "чата «" + message["forward_from_chat"].get("title", "") + "»"
    return None


def _reply_note(message):
    """Which message the owner is replying to — without it "forward them this" makes no sense: (the note, the source of someone
    else's text in it, or None). Sena's words and his own are quoted as they are; a message he had forwarded, or one of another
    chat, is someone else's text: fenced, and the task is marked as with a forward."""
    original, other = message.get("reply_to_message"), message.get("external_reply")
    if not original and not other:
        return "", None
    quote = (message.get("quote") or {}).get("text")   # the part of it he quoted, when he did
    if other:   # a message of another chat: Telegram gives where it is from, and the words only as the quote
        source = f"сообщение из другого чата от {_from_origin(other.get('origin') or {})}"
        return f"[Ответ на {source}]\n{external((quote or 'вложение')[:300], source)}", source
    snippet = (quote or original.get("text") or original.get("caption") or "вложение")[:300]
    forwarded, sender = _forwarded_from(original), original.get("from") or {}
    if not forwarded and sender.get("is_bot"):
        return f"[Ответ на сообщение Сены: «{snippet}»]", None
    if not forwarded and sender.get("id") == config.OWNER_ID:
        return f"[Ответ на сообщение Темы: «{snippet}»]", None
    source = f"пересланное сообщение от {forwarded}" if forwarded else f"сообщение от {_person(sender)}"
    return f"[Ответ на {source}]\n{external(snippet, source)}", source


def _late_note(message):
    """An old message answered only now (she was off or fell, the Mac had no network): when he wrote it — «через 10 минут» and
    «сейчас» in it are counted from then, and what is no longer current he hears about, instead of a stale answer as if fresh."""
    sent = message.get("date")
    if not isinstance(sent, (int, float)) or time.time() - sent < LATE:
        return ""
    ago = time.time() - sent
    when = moment.local(sent, moment.owner_offset)
    day = "" if when.date() == moment.local(time.time(), moment.owner_offset).date() else f"{when:%d.%m} "
    late = f"{round(ago / 3600)} ч" if ago >= 5400 else f"{max(2, round(ago / 60))} мин"
    return (f"[Тема написал это в {day}{when:%H:%M} — {late} назад, а отвечаю я только сейчас (была выключена или без связи): "
            "«сейчас» и «через …» в нём считай от того времени, а что устарело — скажи ему]")


def _file_of(message):
    # (file_id, name) of an attachment we know how to save; None if there's no attachment
    if message.get("photo"):
        return message["photo"][-1]["file_id"], "photo.jpg"   # the last photo in the list is the largest
    if message.get("document"):
        doc = message["document"]
        return doc["file_id"], doc.get("file_name") or "document"
    return None


def read_message(message, show):
    """One message from the owner -> (text for Sena, parts for the model: images, PDFs, file text, the sources of someone
    else's text in it). The task doesn't exist yet while the message is read: the sources go to ag_sena.ask, which marks
    the task foreign as soon as it begins — and then the terminal and the code agents wait for his "yes"."""
    lines, parts, foreign = [], [], []
    body = message.get("text") or message.get("caption") or ""
    forwarded = _forwarded_from(message)
    if forwarded:
        # someone else's words: Sena doesn't carry out their requests or commands
        body = external(body, f"пересланное сообщение от {forwarded}") if body else f"[Пересланное сообщение от {forwarded}]"
        foreign.append(f"пересланное сообщение от {forwarded}")
    reply, source = _reply_note(message)
    if reply:
        lines.append(reply)
    if source:
        foreign.append(source)

    for field in SPOKEN:
        if field in message:
            show("Расшифровываю " + ("голосовое" if field == "voice" else "аудио"))
            media = message[field]
            ext = {"audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/x-m4a": "m4a", "video/mp4": "mp4", "audio/wav": "wav"}.get(
                media.get("mime_type"), "mp4" if field == "video_note" else "ogg")   # a round video carries no type: it is an mp4
            # at home first (roles.hear): the owner's voice stays on the Mac; the name matters only if the cloud has to step in
            audio = api.download(media["file_id"])
            spoken = roles.hear(audio, media.get("file_name") or f"{field}.{ext}", cloud=gemini.transcribe)
            if field != "voice":
                lines.append(f"[Аудио{' «' + media['file_name'] + '»' if media.get('file_name') else ''}, расшифровка: {external(spoken, 'речь из аудио')}]")
                foreign.append("речь из аудио")
            elif forwarded:   # someone else's voice message: their words are data to look at, never his command
                show("Расшифровала пересланное голосовое")
                body += "\n" + external(spoken, f"голосовое от {forwarded}")
            else:   # his own voice: Sena learns it, so a spoken command near the phone can be told from a guest's
                safety.background(people.learn_owner_voice, audio, what="Голос Темы")
                show(f"Ты сказал: {spoken}")
                body = (body + "\n" if body else "") + spoken
    if body:
        lines.append(body)

    found = _file_of(message)
    if found:
        file_id, name = found
        show("Скачиваю файл")
        path = attachments.save(api.download(file_id), name, caption=body)   # the message it came with is the file's context
        lines.append(attachments.note(path))
        show("Читаю файл")
        parts += attachments.parts(path)
        foreign.append(f"файл «{name}»")   # what a file says is someone else's text, as a page or a mail is

    if message.get("contact"):
        c = message["contact"]
        lines.append(f"[Контакт: {c.get('first_name', '')} {c.get('last_name', '')}, телефон {c.get('phone_number', '')}]".replace("  ", " "))
    if message.get("location"):
        loc = message["location"]
        lines.append(f"[Геопозиция: {loc['latitude']}, {loc['longitude']}]")
    return "\n".join(lines), parts, foreign


def _understood(message):
    return any(k in message for k in ("text", "caption", "photo", "document", "contact", "location", *SPOKEN))


class Progress:
    """The temporary message that shows how the work goes. Its edits go out from a thread of their own: a step never waits on
    Telegram (up to 10 s on a bad network), and quick steps come together into one edit a second instead of one each."""
    EVERY = 1.0   # seconds between two edits at least

    def __init__(self, message_id):
        self.id, self.steps = message_id, ["Приняла"]
        self.lock, self.wake, self.over = threading.Lock(), threading.Event(), False
        threading.Thread(target=self._run, daemon=True).start()

    def show(self, text):
        text = str(text)
        self.steps.append(text if len(text) <= STEP_SHOWN else text[:STEP_SHOWN].rstrip() + "…")
        self.wake.set()

    def _run(self):
        while self.wake.wait():
            self.wake.clear()
            with self.lock:
                if self.over:
                    return
                api.edit_text(self.id, "\n".join(self.steps[-12:]) + "...", quick=True)   # task progress shouldn't wait minutes on Telegram
            time.sleep(self.EVERY)

    def close(self):
        """Before the answer takes the message's place: an edit of steps already on its way finishes first, none comes after."""
        with self.lock:
            self.over = True
        self.wake.set()


def _joined(messages, seen):
    """The messages that joined this task (dispatch) since `seen` of them were read; [] once none came for JOIN_WAIT — then
    the task takes no more. The wait is the moment Telegram takes to bring the forwards after the comment."""
    chat = messages[0]["chat"]["id"]
    while True:
        with _albums_lock:
            if len(messages) > seen:
                return messages[seen:]
            entry = _joining.get(chat)
            if not entry or entry[0] is not messages:   # a newer message of his took the chat (or this task never took forwards)
                return []
            left = JOIN_WAIT - (time.time() - entry[1])
            if left <= 0:
                del _joining[chat]
                return []
        time.sleep(min(left, 0.1))


def _close_joining(messages):
    # the task is answering: a forward that comes now is a message of its own (a failed task must not swallow it unread)
    with _albums_lock:
        entry = _joining.get(messages[0]["chat"]["id"])
        if entry and entry[0] is messages:
            del _joining[messages[0]["chat"]["id"]]


def _answer(wait, answer, markup, took):
    """The answer in the place of «Приняла» when it came quickly; after a long task as a message of its own — Telegram makes no
    sound for an edit, and a job of an hour ended silently far up the chat. The progress message then makes way."""
    if wait and took < LONG and (api.edit_text(wait["message_id"], answer, markup=markup) or {}).get("ok"):
        events.log("message_out", answer, channel="telegram")
        return
    sent = api.send_text(answer, markup)   # the temporary message wasn't made, can't be edited, or the task was long
    if wait and took >= LONG and (sent or {}).get("ok") and not (api.delete_message(wait["message_id"]) or {}).get("ok"):
        api.edit_text(wait["message_id"], "Готово, ответ ниже", quick=True)


def handle(messages):
    first = messages[0]
    if first["chat"]["id"] != config.OWNER_ID:
        return
    if not any(_understood(m) for m in messages):
        api.send_text("Такое вложение (видео, стикер или другое) я пока не открываю. Пришли текстом, голосом, фото или файлом.")
        return
    began = time.time()
    # a temporary message: it shows what's happening as the work proceeds, and we replace it with the answer at the end
    wait = (api.send_text("Приняла") or {}).get("result")
    progress = Progress(wait["message_id"]) if wait else None
    show = progress.show if progress else (lambda text: None)
    status.local.show = show
    try:
        read, parts, foreign = [], [], []
        done, batch = 0, list(messages)
        while batch:   # what came at first, then the forwards that joined it meanwhile
            done += len(batch)
            for message in sorted(batch, key=lambda m: m["message_id"]):
                text, more, sources = read_message(message, show)
                if text:
                    read.append((message["message_id"], text))
                parts += more
                foreign += sources
            batch = _joined(messages, done)
        texts = [text for _, text in sorted(read)]
        late = _late_note(min(messages, key=lambda m: m["message_id"]))
        events.log("message_in", "\n".join(texts), source="telegram", files=len(parts) or None, late=bool(late) or None)
        answer = ag_sena.ask(first["chat"]["id"], "\n".join(([late] if late else []) + texts), attachments=parts or None, foreign=foreign)
    except api.FileNotAvailable as e:
        answer = f"Не получилось получить файл: {e}."
        events.error("Файл из Телеграма", e)
    except Exception as e:
        events.error("Сообщение из Телеграма", e)   # with its trace: one channel for errors, not a print beside it
        answer = f"Что-то сломалось, пока отвечала: {llm.explain(e)}.{uptime.outage_note(e)} Повтори, пожалуйста; подробности в журнале событий."
    finally:
        _close_joining(messages)
    if progress:
        progress.close()
    markup = api.buttons(confirm.prepared_in(getattr(status.local, "turn", None)))   # what this answer asks a "yes" for gets buttons
    _answer(wait, answer, markup, time.time() - began)
    if settings.get("voice_reply") and any("voice" in m for m in messages):   # a voice message gets a voice reply too, a long one in parts
        safety.background(lambda: api.send_audio(roles.speak(answer, settings.get("voice"), gemini.speech)), what="Голосовой ответ")


_album_last = {}   # when the last part of the album arrived


def _flush_album(group_id):
    with _albums_lock:
        if time.time() - _album_last.get(group_id, 0) < ALBUM_WAIT - 0.1:
            return   # more parts are still coming in: the last one's timer will fire
        messages = _albums.pop(group_id, [])
        _album_last.pop(group_id, None)
    if messages:
        safety.background(handle, messages, what="Альбом в Телеграме")


def dispatch(message):
    """A new message from the owner: it goes to work right away. Album parts are collected together first; a comment and the
    forwards that come with it a moment apart (in either order) join one task — two tasks, one with his words and no forward
    in sight, one with the forward and no question, answered neither right."""
    group_id = message.get("media_group_id")
    if not group_id:
        chat, text = (message.get("chat") or {}).get("id"), message.get("text") or ""
        if not _understood(message) or (ag_sena.STOP.match(text) and len(text) < 40):   # «стоп» stops at once: it waits for no forward
            safety.background(handle, [message], what="Сообщение в Телеграме")
            return
        with _albums_lock:
            entry = _joining.get(chat)
            if entry and time.time() - entry[1] < JOIN_WAIT and any(_forwarded_from(m) for m in [*entry[0], message]):
                entry[0].append(message)   # the task that took the comment (or the forward) reads it too
                entry[1] = time.time()
                return
            messages = [message]
            _joining[chat] = [messages, time.time()]
        safety.background(handle, messages, what="Сообщение в Телеграме")
        return
    with _albums_lock:
        _albums.setdefault(group_id, []).append(message)
        _album_last[group_id] = time.time()
    timer = threading.Timer(ALBUM_WAIT, _flush_album, args=(group_id,))   # the album is sent once its parts stop arriving
    timer.daemon = True   # Sena's exit doesn't wait for it
    timer.start()


def pressed(query):
    """The owner pressed «Да» or «Нет» under an answer: his own click is the "yes" itself, like a button on the control panel.
    The press is always answered: done, cancelled, gone, or not done and why; the buttons stay while the action still waits
    (one that surely reached no one waits for another «Да»)."""
    if (query.get("from") or {}).get("id") != config.OWNER_ID:
        return api.answer_button(query["id"], "Это не твоя кнопка")
    kind, _, sid = (query.get("data") or "").partition(":")
    if kind not in ("yes", "no") or not sid:
        return api.answer_button(query["id"])
    events.log("app", f"кнопка в Телеграме: {'да' if kind == 'yes' else 'нет'}", source="telegram", id=sid)
    message = query.get("message") or {}
    known = sid in confirm.pending
    try:
        result = sena_tools.pressed(sid, kind == "yes", (message.get("chat") or {}).get("id"))
    except Exception as e:   # the action itself broke: said in words all the same
        events.error("Кнопка в Телеграме", e)
        result = Failure(f"Не вышло: {api.safe(e)[:300]}")
    api.answer_button(query["id"], "Этого уже нет" if not known else "Не вышло" if isinstance(result, Failure) else
                      "Готово" if kind == "yes" else "Отменено")
    if message.get("message_id") and sid not in confirm.pending:
        api.drop_buttons(message["message_id"])
    # what the press prepared anew (an apply refused for now waits under a new id) comes with its own «Да / Нет»
    waiting = {w["id"]: w["name"] for w in confirm.waiting()}
    held = [(k, waiting[k]) for k in dict.fromkeys(re.findall(r"\[([0-9a-f]{4})\]", str(result))) if k != sid and k in waiting]
    api.send_text(str(result), api.buttons(held))


def poll():
    offset, pause, down = 0, 3, False
    while True:
        try:
            for update in api.updates(offset):
                offset = update["update_id"] + 1
                if "message" in update:
                    dispatch(update["message"])
                elif "callback_query" in update:
                    safety.background(pressed, update["callback_query"], what="Кнопка в Телеграме")
            if down:
                events.log("start", "связь с Телеграмом вернулась")
                down, pause = False, 3
        except Exception as e:   # no connection: one log entry for the outage and a pause that grows to a minute, instead of thousands of lines an hour
            print("Ошибка:", api.safe(e))
            if not down:
                events.error("Опрос Телеграма", e)
                down = True
            time.sleep(pause)
            pause = min(pause * 2, 60)


OFF = ("Я сейчас выключена. Включи меня кнопкой «Запустить Сену» в приложении на Mac или телефоне — и я отвечу на то, "
       "что ты написал.")
OFF_EVERY = 20     # s between two looks at Telegram while she is off
OFF_QUIET = 1800   # s: one «я выключена» in half an hour, however many messages he sends


def off_round(seen, told):
    """One look at Telegram from the screen (Sena off): his new message gets OFF at most once in OFF_QUIET, a press of a button
    a word that she is off. Nothing is confirmed (no offset): the core, once started, answers every message, with the time it
    was written (_late_note). seen — the update ids already looked at; returns when he was last told."""
    for update in api.updates(0):
        if update["update_id"] in seen:
            continue
        seen.add(update["update_id"])
        message, query = update.get("message") or {}, update.get("callback_query")
        if (message.get("chat") or {}).get("id") == config.OWNER_ID and time.time() - told >= OFF_QUIET:
            api.send_text(OFF)
            told = time.time()
        if query:
            api.answer_button(query["id"], "Сена выключена")
    return told


def answer_while_off():
    """The screen's Telegram (python -m sena.phone): while Sena is off his messages got no word at all. Runs for the screen's life."""
    if not (config.TELEGRAM_TOKEN and config.OWNER_ID):
        return
    seen, told, down = set(), 0.0, False
    while True:
        try:
            told, down = off_round(seen, told), False
        except Exception as e:   # no connection: one line for the outage
            if not down:
                events.error("Телеграм, пока Сена выключена", e)
            down = True
        time.sleep(OFF_EVERY)
