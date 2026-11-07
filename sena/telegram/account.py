"""The owner's own Telegram account (Telethon): read chats, draft and send messages to people.
Not to be confused with telegram/bot.py: the bot is the owner's communication channel with Sena, while here Sena acts on the owner's own behalf."""
import asyncio
import functools
import os
import re
import subprocess
import tempfile
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor

from sena import config
from sena.core import cancel, events, sandbox, status
from sena.core.safety import external
from sena.core.tools import Failure, clamp

# Telegram lives in its own thread: this way several tasks can use it at once without interfering with each other
_thread = ThreadPoolExecutor(max_workers=1)
client = None        # set only once connected and signed in: a client that never connected is no account
SIGN_IN_WAIT = 300   # s the QR page waits to be scanned
SHOW_WAIT = 20       # s sign_in waits for the QR page to open (or the sign-in to end) before it says what happened
RETRY = 60           # s between tries to connect a saved session that didn't connect (no network when Sena started)
DIALOGS_MOST = 300   # chats a name is looked for among, the newest first: every hundred more is one more request to Telegram
HOW_TO_SIGN_IN = ("Аккаунт Телеграма не подключён: вызови telegram_signin — на Маке откроется страница с QR-кодом, Тема отсканирует "
                  "его Телеграмом на телефоне (Настройки → Устройства → «Подключить устройство»). Код подтверждения не нужен.")
_signing = threading.Event()   # the QR page is open and waits to be scanned
_tried = 0.0                   # when the account last tried to connect


def _need():
    # a clear answer instead of "'NoneType' has no attribute ..." when the account isn't connected; a saved session that didn't
    # connect is tried again first, at most once a RETRY (runs on the account's own thread, as everything that calls it)
    if client is None and os.path.exists(f"{config.TELEGRAM_SESSION}.session") and time.time() - _tried > RETRY:
        _connect()
    if client is None:
        raise RuntimeError(HOW_TO_SIGN_IN)


def _in_thread(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return _thread.submit(func, *args, **kwargs).result()
    return wrapper


def _connect():
    # log into the owner's account from its session (data/state/tema.session); the client is kept only when it connected and is
    # signed in — Telethon gives up after its own retries with no network, and a half-made client looked "connected" for good
    global client, _tried
    _tried = time.time()
    asyncio.set_event_loop(asyncio.new_event_loop())
    from telethon.sync import TelegramClient
    tg = TelegramClient(str(config.TELEGRAM_SESSION), config.TG_API_ID, config.TG_API_HASH)
    try:
        tg.connect()
        signed = tg.is_user_authorized()
    except Exception as e:   # no network, Telegram not answering: not connected now, the next call tries again
        events.error("Вход в аккаунт Телеграма", e)
        try:
            tg.disconnect()
        except Exception:
            pass
        return
    if not signed:   # no one to ask for a code in the background: the owner signs in by QR (sign_in) or by hand
        tg.disconnect()
        events.log("start", "аккаунт Телеграма не подключён: Сена откроет вход по QR-коду (telegram_signin)", failed=True)
        return
    client = tg
    events.log("start", "вход в аккаунт Телеграма выполнен")


start = _in_thread(_connect)


def saved():
    """Whether a sign-in is kept on this Mac (connected now, or its session file waits to connect): the hourly check and the
    tools try it, not only an account that connected when Sena started."""
    return client is not None or os.path.exists(f"{config.TELEGRAM_SESSION}.session")


@_in_thread
def connected():
    """Whether the account works now: a kept sign-in that didn't connect is tried again first (at most once a RETRY)."""
    try:
        _need()
    except RuntimeError:
        return False
    return True


def find_chat(name):
    # runs on the account's thread (its callers do); the chat named exactly comes before one whose name only contains it
    # («Мама» before «Мама Лена»), among the DIALOGS_MOST newest: the whole list was walked, page after page, for every name
    _need()
    wanted, near = name.lower().strip(), None
    for d in client.iter_dialogs(limit=DIALOGS_MOST):
        title = (d.name or "").lower()
        if title == wanted:
            return d
        if near is None and wanted in title:
            near = d
    return near


@_in_thread
def chat(name):
    """The chat a name finds now: (its name, its peer) — what a message prepared for the owner's "yes" goes to — or None."""
    dialog = find_chat(name)
    return (dialog.name, dialog.input_entity) if dialog else None


@_in_thread
def list_chats(limit=30):
    _need()
    lines = [f"{d.name} (непрочитано: {d.unread_count})" for d in client.iter_dialogs(limit=limit)]
    return external("\n".join(lines), "список чатов Телеграма")


@_in_thread
def unread(limit=40):
    """Chats with unread messages, as data: [(name, how many unread)] — for the hourly check, which reads only those."""
    _need()
    return [(d.name, d.unread_count) for d in client.iter_dialogs(limit=limit) if d.unread_count and d.name]


@_in_thread
def read_chat(name, limit=20):
    _need()
    dialog = find_chat(name)
    if not dialog:
        return Failure(f"Чат «{name}» не найден")
    lines = []
    for m in reversed(client.get_messages(dialog, limit=limit)):
        sender = "Тема" if m.out else getattr(m.sender, "first_name", None) or dialog.name
        lines.append(f"[{m.date:%d.%m %H:%M}] {sender}: {m.text or '(не текст)'}")
    return external(f"Чат «{dialog.name}»:\n" + "\n".join(lines), "переписка в Телеграме")


@_in_thread
def send_message(name, text, peer=None):
    # peer — the chat as it was found when the message was prepared (chat): it goes there and nowhere else, even when the name
    # now matches another chat first (a newer group, «Мама Лена» beside «Мама»)
    _need()
    if peer is None:
        dialog = find_chat(name)
        if not dialog:
            return Failure(f"Чат «{name}» не найден")
        name, peer = dialog.name, dialog.input_entity
    client.send_message(peer, text)
    events.log("message_out", text, channel="telegram_account", to=name)
    return f"Отправлено в чат «{name}»: {text}"


FORBIDDEN = re.compile(r"\b(send_message|send_file|forward_messages|delete_messages|edit_message|delete_dialog|send_read_acknowledge|"
                       r"reply|respond|forward_to|delete|edit|click|pin|pin_message|kick_participant|edit_admin|edit_permissions|"
                       r"functions|Request|getattr|setattr|__getattribute__|exec|eval|"
                       r"session|api_hash|api_id|log_out|disconnect|takeout|download_media|download_file|download_profile_photo|iter_download|upload_file)\b")
# the session and the app's keys are the account itself; files go to disk only through Sena's own tools


def _who(entity):
    return " ".join(x for x in (getattr(entity, "first_name", None), getattr(entity, "last_name", None)) if x) or getattr(entity, "title", None) or ""


def _message(m, chat=""):
    return {"id": m.id, "chat": chat, "date": m.date.astimezone().strftime("%d.%m.%Y %H:%M") if m.date else "", "out": bool(m.out),
            "sender": "Тема" if m.out else _who(getattr(m, "sender", None)), "text": m.message or "", "reply_to": m.reply_to_msg_id}


def _chat(name):
    _need()
    dialog = find_chat(name)
    if not dialog:
        raise LookupError(f"чат «{name}» не найден")
    return dialog


# The code's only way to the account: functions that read and return plain data. A Telethon client or message in the code
# would carry methods that write (edit_2fa, edit_folder, unpin_message, archive, mark_read — a list of forbidden words never
# catches them all, smolagents' study found three), so the code never gets one.
def _signed(func):
    # every reading function says plainly that the account isn't connected, instead of "'NoneType' has no attribute"
    def call(*args, **kwargs):
        _need()
        return func(*args, **kwargs)
    return call


def _messages(chat, limit=50, search=None, before=None):
    dialog = _chat(chat)   # once: looked up again for every message, a read of 50 walked the chat list 51 times
    return [_message(m, dialog.name) for m in client.get_messages(dialog, limit=clamp(limit, 50, 1, 1000), search=search, offset_date=before)]


TG = {"dialogs": lambda limit=100: [{"name": d.name, "unread": d.unread_count, "user": d.is_user, "group": d.is_group, "channel": d.is_channel}
                                    for d in client.get_dialogs(limit=clamp(limit, 100, 1, 500))],
      "messages": _messages,
      "search_all": lambda query, limit=50: [_message(m, _who(getattr(m, "chat", None))) for m in client.get_messages(None, limit=clamp(limit, 50, 1, 300), search=query)],
      "participants": lambda chat, limit=200: [{"name": _who(u), "username": getattr(u, "username", None) or "", "id": u.id}
                                               for u in client.get_participants(_chat(chat), limit=clamp(limit, 200, 1, 2000))],
      "me": lambda: {"name": _who(client.get_me()), "username": getattr(client.get_me(), "username", "")}}


def telegram_code(code):
    # Python with read-only access to the owner's account: tg.dialogs(), tg.messages(chat, limit, search, before),
    # tg.search_all(query), tg.participants(chat), tg.me() — plain data, no client. Anything that goes out to people goes through
    # send_message with the owner's confirmation. It runs on Telegram's own thread, so the caller's stop is handed in
    mine = cancel.current_task()
    return _run_code(code, lambda: mine in cancel.cancelled)


@_in_thread
def _run_code(code, stop):
    hit = FORBIDDEN.search(code)
    if hit:
        return Failure(f"Отказ: «{hit.group(0)}» в коде нельзя — только чтение и поиск; отправка, пересылка, удаление и правки идут через send_message с подтверждением Темы")
    modules = ("datetime", "re", "json", "math", "collections")
    refused = sandbox.check(code, modules)
    if refused:   # the sandbox's own refusals come first: they say what to rewrite
        return Failure(f"Код не запущен: {refused}. Перепиши без этого.")
    env = {"tg": types.SimpleNamespace(**{name: _signed(f) for name, f in TG.items()}), **{name: sandbox.module(name) for name in ("datetime", "re", "json")}}
    result = sandbox.run(code, env, "вывод кода с Телеграмом", modules, stop=stop)   # the process holds Sena's keys: no os, no open
    return Failure(HOW_TO_SIGN_IN) if client is None and "telegram_signin" in result else result


def _qr_page(path, url, expires):
    # the code inside the page itself (no picture for the browser to keep stale), written whole; the page reloads every 2 s
    import segno
    left = max(0, int(expires.timestamp() - time.time()))
    html = f"""<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="2"><title>Вход Сены в Телеграм</title>
<body style="font-family:-apple-system,Helvetica;text-align:center;background:#fff;color:#111;margin-top:24px">
<h2>Отсканируй телефоном</h2><p>Телеграм на телефоне → Настройки → Устройства → «Подключить устройство»</p>
<img alt="QR" src="{segno.make(url, error='m').svg_data_uri(scale=10, border=4)}" style="width:380px;height:380px">
<p>Этот код живёт ещё {left} с, потом страница сама покажет новый</p></body>"""
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(path + ".tmp", path)


async def _qr_flow(show=True, opened=None):
    """Sign in by QR code: the owner scans it with Telegram on his phone. True — signed in; the reason in words otherwise.
    opened — an event set once the page is open on the Mac."""
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    tg = TelegramClient(str(config.TELEGRAM_SESSION), config.TG_API_ID, config.TG_API_HASH)
    await tg.connect()
    folder = tempfile.mkdtemp(prefix="sena-tg-")
    page = os.path.join(folder, "telegram_qr.html")
    try:
        if await tg.is_user_authorized():
            return True
        qr = await tg.qr_login()
        shown, deadline = False, time.time() + SIGN_IN_WAIT
        while time.time() < deadline:
            _qr_page(page, qr.url, qr.expires)
            if show and not shown:
                subprocess.run(["open", page])
                if opened is not None:
                    opened.set()
            shown = True
            while time.time() < qr.expires.timestamp() - 4:   # a fresh code a few seconds before this one dies
                try:
                    await qr.wait(timeout=2)
                    return True
                except asyncio.TimeoutError:
                    _qr_page(page, qr.url, qr.expires)
                except SessionPasswordNeededError:
                    return "у аккаунта облачный пароль: Тема вводит его сам в Терминале — python -m sena.telegram.account"
            await qr.recreate()
        return f"QR-код не отсканировали за {SIGN_IN_WAIT // 60} минут"
    finally:
        await tg.disconnect()
        for f in (page, page + ".tmp"):
            if os.path.exists(f):
                os.unlink(f)   # the page held a login token: it doesn't stay on disk
        os.rmdir(folder)


def _sign_in_run(opened=None, outcome=None):
    # outcome — gets "done": True (signed in) or the reason in words, for sign_in, which waits a little to say what happened
    outcome = {} if outcome is None else outcome
    _signing.set()
    try:
        done = asyncio.run(_qr_flow(opened=opened))   # its own event loop: the account's thread keeps its own
        outcome["done"] = done
        if done is True:
            events.log("start", "вход в аккаунт Телеграма по QR-коду выполнен")
            start()   # the session is there now: the account connects without a restart
        else:
            events.log("start", f"вход в аккаунт Телеграма не завершён: {done}", failed=True)
    except Exception as e:
        outcome["done"] = f"нет связи с Телеграмом ({type(e).__name__})"
        events.error("Вход в Телеграм по QR-коду", e)
    finally:
        _signing.clear()


def sign_in():
    """Sena's tool: the owner's Telegram sign-in by QR code — a page with the code opens on the Mac, the owner scans it with
    Telegram on his phone; no login code to type. The scan itself is always his. It says what really happened: a kept
    sign-in may connect without any page, and with no network no page opens at all."""
    if client is not None:
        return "Аккаунт Телеграма подключён."
    if _signing.is_set():
        return "Страница с QR-кодом уже открыта на Маке: отсканируй её Телеграмом на телефоне."
    if not getattr(status.local, "said", None):   # a scheduled job, a long work's turn, a voice that isn't his: nobody asked at
        # the Mac, and the page holds a login token on a screen nobody watches
        return Failure("Страницу с QR-кодом открываю только по просьбе самого Темы, а в этом ходе его слов нет. Скажи ему, что нужен "
                       "вход в Телеграм: пусть напишет «войди в Телеграм».")
    opened, outcome = threading.Event(), {}
    worker = threading.Thread(target=_sign_in_run, args=(opened, outcome), daemon=True)
    worker.start()
    end = time.monotonic() + SHOW_WAIT
    while not opened.is_set() and worker.is_alive() and time.monotonic() < end:
        opened.wait(0.2)
    if opened.is_set() or worker.is_alive():
        return ("Открыла на Маке страницу с QR-кодом. В Телеграме на телефоне: Настройки → Устройства → «Подключить устройство» — "
                "и наведи камеру на код. Код подтверждения не нужен." if opened.is_set() else
                "Готовлю страницу с QR-кодом: через несколько секунд она откроется на Маке. Отсканируй её Телеграмом на телефоне "
                "(Настройки → Устройства → «Подключить устройство»).")
    if outcome.get("done") is True:
        return "Подключила сохранённый вход в Телеграм: QR-код не понадобился."
    return Failure(f"Вход в Телеграм не начался: {outcome.get('done') or 'подробности в журнале'}. Попробуй позже.")


if __name__ == "__main__":   # python -m sena.telegram.account [qr] — log into the owner's account: by QR code, or phone and code
    import asyncio as _asyncio
    import sys as _sys
    if len(_sys.argv) > 1 and _sys.argv[1] == "qr":
        print({True: "Вход выполнен, сессия сохранена"}.get(_asyncio.run(_qr_flow()), "Вход не выполнен"))
    else:
        from telethon.sync import TelegramClient as _Client
        _asyncio.set_event_loop(_asyncio.new_event_loop())
        with _Client(str(config.TELEGRAM_SESSION), config.TG_API_ID, config.TG_API_HASH) as _c:
            _c.start()
            print("Вход выполнен, сессия сохранена:", config.TELEGRAM_SESSION)
