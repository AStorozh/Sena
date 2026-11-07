"""Telegram Bot API: sending messages and files to the owner, receiving messages, downloading voice notes."""
import html
import os
import re
import time

import requests

from sena import config
from sena.core import events, settings

_URL = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"
_session = requests.Session()  # one connection to Telegram — faster


def safe(error):
    """An error's text without the bot's token: requests puts the whole address, /bot<token>/…, into its messages, and a print
    goes to bot.log as it is in every process (the journal masks it on its own)."""
    return str(error).replace(config.TELEGRAM_TOKEN, "<токен>") if config.TELEGRAM_TOKEN else str(error)


def post(method, data, retries=10, timeout=60):
    # the connection sometimes drops: we retry a few times so the reply isn't lost; on 429 we wait as long as Telegram asks.
    # A send that never got through is one line in the journal, not one per try
    error = None
    for attempt in range(retries):
        try:
            reply = _session.post(f"{_URL}/{method}", json=data, timeout=timeout).json()
        except Exception as e:
            error = e
            print("Нет связи с Телеграмом, повтор:", safe(e))
            if attempt + 1 < retries:
                time.sleep(5)
            continue
        wait = (reply.get("parameters") or {}).get("retry_after") if not reply.get("ok") else None
        if wait and attempt + 1 < retries:
            time.sleep(min(int(wait), 30))
            continue
        return reply
    if error is not None:
        events.error(f"Связь с Телеграмом ({method})", error)
    return None


LIMIT = 4000   # a single Telegram message holds at most 4096 characters


def _chunks(text):
    # long text goes out as several messages, split on line breaks where possible
    text = text.strip() or "(пусто)"
    parts = []
    while len(text) > LIMIT:
        cut = text.rfind("\n", 0, LIMIT)
        cut = cut if cut > LIMIT // 2 else LIMIT
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return parts + [text]


def buttons(held):
    """Buttons under an answer for what waits for a "yes": [(id, name)] -> Telegram's inline keyboard (or None)."""
    if not held:
        return None
    return {"inline_keyboard": [[{"text": f"Да: {name[:40]}", "callback_data": f"yes:{sid}"}, {"text": "Нет", "callback_data": f"no:{sid}"}]
                                for sid, name in held]}


# Telegram's servers open a link in a message to draw its preview: a link with data in it (planted by a mail or a page
# Sena read) would leak the data without anyone tapping it — so no previews in what the bot writes (promptfoo's data
# exfiltration check: a link with data in the URL)
NO_PREVIEW = {"link_preview_options": {"is_disabled": True}}

# the Markdown the model writes (the apps draw it): in Telegram it showed raw, «**Итог**»
CODE_BLOCK = re.compile(r"```[^\n`]*\n?(.*?)```", re.S)
INLINE_CODE = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")   # not __x__: it is how names like __init__ are written
HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t#]*$", re.M)
BULLET = re.compile(r"^([ \t]*)[-*+][ \t]+", re.M)


def _written_out(text):
    # a link as «text (address)»: the owner sees where it leads — a planted address hides behind no word
    return LINK.sub(r"\1 (\2)", text)


def rich(text):
    """Markdown -> Telegram's HTML: **bold** and headings in bold, `code` and ``` blocks as code, list marks as bullets, links
    written out. Everything else is escaped, so the only tags are these, each one closed."""
    out = []
    for n, piece in enumerate(CODE_BLOCK.split(text)):   # odd pieces are the insides of code blocks
        if n % 2:
            out.append(f"<pre>{html.escape(piece.strip(chr(10)), quote=False)}</pre>")
            continue
        for k, span in enumerate(INLINE_CODE.split(piece)):   # odd spans are inline code
            if k % 2:
                out.append(f"<code>{html.escape(span, quote=False)}</code>")
                continue
            span = html.escape(_written_out(span), quote=False)
            span = HEADING.sub(lambda m: "<b>" + m.group(1).replace("**", "") + "</b>", span)
            span = BOLD.sub(r"<b>\1</b>", span)
            out.append(BULLET.sub(r"\1• ", span))
    return "".join(out)


def plain(text):
    """The same without any markup, for when Telegram can't read the HTML: no raw asterisks either."""
    text = INLINE_CODE.sub(r"\1", CODE_BLOCK.sub(lambda m: m.group(1).strip("\n"), text))
    text = HEADING.sub(lambda m: m.group(1).replace("**", ""), _written_out(text))
    return BULLET.sub(r"\1• ", BOLD.sub(r"\1", text))


def _unreadable(reply):
    # Telegram refused the HTML ("can't parse entities"): the same words go again as plain text
    return bool(reply) and not reply.get("ok") and "entit" in str(reply.get("description", "")).lower()


def _post_text(method, data, text, **kw):
    # a text with its markup as Telegram's HTML, as plain text if Telegram can't read it
    reply = post(method, {**data, "text": rich(text), "parse_mode": "HTML"}, **kw)
    return post(method, {**data, "text": plain(text)}, **kw) if _unreadable(reply) else reply


def _message(part, markup=None, quiet=False):
    # one message of at most LIMIT characters; logging it is the caller's: an answer is one line in the journal, whatever its parts
    data = {"chat_id": config.OWNER_ID, **NO_PREVIEW, **({"disable_notification": True} if quiet else {})}
    if markup:
        data["reply_markup"] = markup
    return _post_text("sendMessage", data, part)


def send_text(text, markup=None, quiet=False):
    """Send text to the owner; returns Telegram's response for the first message. markup — buttons under the last part;
    quiet — without a sound (a night report waits for the morning instead of waking him)."""
    first = None
    parts = _chunks(text)
    for n, part in enumerate(parts):
        reply = _message(part, markup if n == len(parts) - 1 else None, quiet)
        first = first or reply
    events.log("message_out", text, channel="telegram", failed=not (first or {}).get("ok") or None)
    return first


def notify(text, markup=None, quiet=False, image=None):
    """A message Sena sends on her own initiative — a notice, a reminder, a report, an agent's question, a late voice answer.
    The one door for all of them, shut while the owner has turned such messages off (settings telegram_notify): each path used
    to check the flag itself, and those that forgot wrote to him after he had said not to. What he asks for himself (a reply
    to his message in the bot, «пришли мне в Телеграм») goes by send_text. Telegram's reply, or None — not sent."""
    if not settings.get("telegram_notify"):
        events.log("telegram_held", "в Телеграм не отправлено: Тема запретил писать туда самой", chars=len(text))
        return None
    if image:
        return send_photo(image, text)
    return send_text(text, **({"markup": markup} if markup else {}), **({"quiet": True} if quiet else {}))


def _upload(method, field, file, caption="", timeout=300, retries=3):
    """A file to the owner in one request; Telegram's reply as a dict. A dropped connection is tried again and a 429 waits as
    Telegram asks, as in post; a timeout isn't repeated (the file is too big for this network). With no connection at all the
    reply says so ({"ok": False}) instead of an exception in the middle of a task."""
    data = {"chat_id": config.OWNER_ID, **({"caption": caption[:1000]} if caption else {})}
    name = file[0] if isinstance(file, tuple) else getattr(file, "name", None)
    reply, error = None, None
    for attempt in range(retries):
        if hasattr(file, "seek"):
            file.seek(0)   # a retry sends the file from its start
        try:
            reply = _session.post(f"{_URL}/{method}", data=data, files={field: file}, timeout=timeout).json()
        except requests.Timeout as e:
            reply, error = None, e
            break
        except Exception as e:
            reply, error = None, e
            if attempt + 1 < retries:
                time.sleep(5)
            continue
        wait = (reply.get("parameters") or {}).get("retry_after") if not reply.get("ok") else None
        if not (wait and attempt + 1 < retries):
            break
        time.sleep(min(int(wait), 30))
    if reply is None:
        events.error(f"Связь с Телеграмом ({method})", error)
        reply = {"ok": False, "description": f"нет связи с Телеграмом ({type(error).__name__})"}
    events.log("file_out", caption, channel="telegram", method=method, name=name, failed=not reply.get("ok") or None)
    return reply


def send_photo(image, caption):
    _upload("sendPhoto", "photo", ("screen.jpg", image), caption, timeout=60)


def send_media(path, caption=""):
    """An image goes as a photo, a video as a video (otherwise, if that fails or it's too big, as a file)."""
    path = os.path.expanduser(str(path))
    suffix = os.path.splitext(path)[1].lower()
    method, field = (("sendVideo", "video") if suffix in (".mp4", ".mov", ".webm") else
                     ("sendPhoto", "photo") if suffix in (".png", ".jpg", ".jpeg", ".webp") and os.path.getsize(path) < 9_000_000 else   # Telegram accepts photos up to 10 MB, we leave a margin
                     ("sendDocument", "document"))
    with open(path, "rb") as f:
        reply = _upload(method, field, f, caption)
    if not reply.get("ok") and method != "sendDocument":
        with open(path, "rb") as f:   # the photo or video wasn't accepted: send it as a file
            reply = _upload("sendDocument", "document", f, caption)
    return "Отправлено в Телеграм" if reply.get("ok") else f"Не отправилось: {reply.get('description', reply)}"


def send_audio(audio, caption=""):
    """A voice reply (mp3) to the owner: as a voice message, the way he sent his (it came as a music file «ответ.mp3»); as an
    audio file when Telegram won't take it as one (he may have voice messages closed to bots)."""
    reply = _upload("sendVoice", "voice", ("ответ.mp3", audio), caption, timeout=60)
    if not reply.get("ok") and not str(reply.get("description", "")).startswith("нет связи"):
        reply = _upload("sendAudio", "audio", ("ответ.mp3", audio), caption, timeout=60)
    return reply


def send_file(path):
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return f"Файл не найден: {path}"
    with open(path, "rb") as f:
        reply = _upload("sendDocument", "document", f)
    return "Файл отправлен" if reply.get("ok") else f"Ошибка: {reply}"


def edit_text(message_id, text, quick=False, markup=None):
    """Replace a message's text; whatever doesn't fit the limit goes out as follow-up messages. quick — task progress: one fast
    attempt, plain text, and never a follow-up (a long step posted another copy of itself with every edit, left in the chat).
    markup — buttons under the answer (on the message itself if it fits, else on the last follow-up)."""
    head, *rest = _chunks(text)
    data = {"chat_id": config.OWNER_ID, "message_id": message_id, **NO_PREVIEW}
    if quick:
        return post("editMessageText", {**data, "text": head}, retries=1, timeout=10)
    if markup and not rest:
        data["reply_markup"] = markup
    reply = _post_text("editMessageText", data, head)
    for n, part in enumerate(rest):   # the follow-ups are this answer's: whoever edits logs it once, whole
        _message(part, markup if n == len(rest) - 1 else None)
    return reply


def delete_message(message_id):
    """Remove a message of Sena's (the progress of a long task, once its answer came below as a message of its own)."""
    return post("deleteMessage", {"chat_id": config.OWNER_ID, "message_id": message_id}, retries=2, timeout=15)


def answer_button(callback_id, text=""):
    """Tell Telegram the button was heard (the little notice on the owner's screen)."""
    return post("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:190]}, retries=2, timeout=15)


def drop_buttons(message_id):
    return post("editMessageReplyMarkup", {"chat_id": config.OWNER_ID, "message_id": message_id, "reply_markup": {"inline_keyboard": []}}, retries=2, timeout=15)


class FileNotAvailable(Exception):
    pass


def download(file_id):
    reply = _session.get(f"{_URL}/getFile", params={"file_id": file_id}, timeout=60).json()
    if not reply.get("ok"):
        # the bot can only download files up to 20 MB: what he can do instead is said with it
        raise FileNotAvailable("файл слишком большой (бот скачивает до 20 МБ): перетащи его в чат приложения на Mac или пришли ссылкой"
                               if "too big" in reply.get("description", "") else reply.get("description", "Телеграм не отдал файл"))
    return _session.get(f"https://api.telegram.org/file/bot{config.TELEGRAM_TOKEN}/{reply['result']['file_path']}", timeout=300).content


def updates(offset):
    # long polling: Telegram holds the request for up to 30 seconds until a message arrives
    return _session.get(f"{_URL}/getUpdates", params={"offset": offset, "timeout": 30}, timeout=60).json()["result"]
