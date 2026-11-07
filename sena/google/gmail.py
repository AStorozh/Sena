"""Gmail: search, read, organize (labels, archive, trash), download attachments, send — and the whole mailbox at once:
a census of who writes how much (code reads thousands of headers, the model sees one table), actions on everything a search
finds (prepared with the count and examples, done after the owner's "yes" in batches of a thousand), Gmail filters so new
mail sorts itself, and unsubscribing the standard way (a one-click link or a mail, RFC 8058). The model decides by groups,
the code does the thousands (smolagents' and OpenClaw's lesson: bulk work doesn't fit into tool-calling steps).
Mail can't be deleted permanently: only trashed (it's kept there for 30 days). Mail text is someone else's data."""
import base64
import html
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from urllib.parse import parse_qs, unquote, urlparse

from sena import config
from sena.core import attachments, cancel, events, status, store
from sena.core.safety import external, html_text, spill
from sena.core.tools import Failure, clamp, string, strings, tool
from sena.google.auth import GoogleError, call

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
SYSTEM_LABELS = {"INBOX", "UNREAD", "STARRED", "IMPORTANT", "SPAM", "TRASH", "SENT", "DRAFT"}
_labels = None   # label name (lowercase) -> id


def _decode(value):
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def _headers(payload):
    return {h["name"].lower(): h["value"] for h in payload.get("headers", [])}


def _walk(part):
    yield part
    for sub in part.get("parts", []):
        yield from _walk(sub)


def _b64(data):
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _text(payload):
    # readable mail text: text/plain, otherwise text/html with markup stripped
    plain, markup = [], []
    for part in _walk(payload):
        data = part.get("body", {}).get("data")
        if not data or part.get("filename"):
            continue
        charset = re.search(r"charset=\"?([\w-]+)", _headers(part).get("content-type", ""), re.I)
        try:
            text = _b64(data).decode(charset.group(1) if charset else "utf-8", errors="replace")
        except LookupError:
            text = _b64(data).decode("utf-8", errors="replace")
        kind = part.get("mimeType", "")
        if kind == "text/plain":
            plain.append(text)
        elif kind == "text/html":
            markup.append(text)
    if plain:
        return "\n".join(plain).strip()
    if markup:   # what the owner sees of it: no comments, no hidden blocks a model alone would read
        return html_text("\n".join(markup))
    return ""


def _attachments(payload):
    return [(p["filename"], p["body"]["attachmentId"], p["body"].get("size", 0))
            for p in _walk(payload) if p.get("filename") and p.get("body", {}).get("attachmentId")]


def _line(msg):
    h = _headers(msg.get("payload", {}))
    flags = ("не прочитано " if "UNREAD" in msg.get("labelIds", []) else "") + ("со звездой " if "STARRED" in msg.get("labelIds", []) else "")
    return (f"[{msg['id']}] {h.get('date', '')[:25]} | от {_decode(h.get('from', ''))[:60]} | {flags}"
            f"{_decode(h.get('subject', '(без темы)'))[:90]} — {html.unescape(msg.get('snippet', ''))[:120]}")


def search(query="", limit=10):
    """Mail matching a Gmail search string (is:unread, from:..., newer_than:2d, has:attachment, label:...)."""
    params = {"maxResults": clamp(limit, 10, 1, 25)}
    if query:
        params["q"] = query
    found = call("GET", f"{BASE}/messages", params=params).get("messages", [])
    if not found:
        return "Писем по этому запросу нет"

    def meta(m):
        return call("GET", f"{BASE}/messages/{m['id']}", params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(meta, found))
    return external("\n".join(_line(m) for m in rows), "почта: список писем")


def read(id):
    """The full message: headers, text, list of attachments."""
    msg = call("GET", f"{BASE}/messages/{id}", params={"format": "full"})
    payload = msg.get("payload", {})
    h = _headers(payload)
    files = [f"{name} ({size // 1024 or 1} КБ)" for name, _, size in _attachments(payload)]
    body = (f"От: {_decode(h.get('from', ''))}\nКому: {_decode(h.get('to', ''))}\nКопия: {_decode(h.get('cc', ''))}\n"
            f"Дата: {h.get('date', '')}\nТема: {_decode(h.get('subject', ''))}\n"
            f"Метки: {', '.join(msg.get('labelIds', []))}\nВложения: {', '.join(files) or 'нет'}\n\n"
            f"{spill(_text(payload) or html.unescape(msg.get('snippet', '')), 6000, 'письмо')}")
    return external(body, "почта: письмо")


def _load_labels():
    global _labels
    items = call("GET", f"{BASE}/labels").get("labels", [])
    _labels = {l["name"].lower(): l["id"] for l in items}
    _labels.update({name.lower(): name for name in SYSTEM_LABELS})
    return _labels


def _label_ids(names, create=False):
    known = _labels or _load_labels()
    ids = []
    for name in names:
        key = name.strip().lower()
        if key not in known:
            known = _load_labels()   # the label may have been created in Gmail itself after startup
        if key not in known:
            if not create:
                raise GoogleError(f"Метки «{name}» нет. Есть: {', '.join(sorted(known))}")
            created = call("POST", f"{BASE}/labels", json={"name": name.strip()})
            known[key] = created["id"]
        ids.append(known[key])
    return ids


def labels():
    """Labels and how many are unread in the inbox."""
    items = call("GET", f"{BASE}/labels").get("labels", [])
    inbox = call("GET", f"{BASE}/labels/INBOX")
    user = sorted(l["name"] for l in items if l.get("type") == "user")
    return (f"Входящие: всего {inbox.get('messagesTotal', 0)}, непрочитанных {inbox.get('messagesUnread', 0)}.\n"
            f"Свои метки: {', '.join(user) or 'нет'}")


def modify(ids, add_labels=None, remove_labels=None):
    """Change message labels. Archive — remove INBOX; read — remove UNREAD; starred — add STARRED."""
    ids = [ids] if isinstance(ids, str) else list(ids)
    add, remove = add_labels or [], remove_labels or []
    if not ids or not (add or remove):
        return "Нечего менять: укажи письма и метки"
    body = {"ids": ids, "addLabelIds": _label_ids(add, create=True), "removeLabelIds": _label_ids(remove)}
    call("POST", f"{BASE}/messages/batchModify", json=body)
    return f"Готово: писем {len(ids)}; добавлено {add or '—'}, убрано {remove or '—'}"


def trash(ids, restore=False):
    """Move to trash (or back). Messages are never deleted permanently: Gmail's trash keeps them for 30 days."""
    ids = [ids] if isinstance(ids, str) else list(ids)
    for i in ids:
        call("POST", f"{BASE}/messages/{i}/{'untrash' if restore else 'trash'}")
    return f"{'Возвращено из корзины' if restore else 'В корзине'}: писем {len(ids)}"


def attachment(message_id, filename):
    """Save a message attachment to data/downloads; returns the path (it can be sent to the owner via send_file)."""
    msg = call("GET", f"{BASE}/messages/{message_id}", params={"format": "full"})
    for name, att_id, _ in _attachments(msg.get("payload", {})):
        if name.lower() == filename.lower():
            data = call("GET", f"{BASE}/messages/{message_id}/attachments/{att_id}")["data"]
            target = config.DOWNLOADS_DIR / f"mail-{message_id[:8]}-{store.safe_name(name)}"
            target.write_bytes(_b64(data))
            attachments.register(target, f"вложение «{name}» из письма {message_id}", "gmail")
            return str(target)
    have = [n for n, _, _ in _attachments(msg.get("payload", {}))]
    return f"Вложения «{filename}» нет. Есть: {', '.join(have) or 'нет вложений'}"


def send(to, subject, body, cc=None, reply_to_id=None):
    """Send a message (after the owner's confirmation). reply_to_id — a reply within the same thread."""
    mail = EmailMessage()
    mail["To"] = to
    if cc:
        mail["Cc"] = cc
    payload = {}
    if reply_to_id:
        original = call("GET", f"{BASE}/messages/{reply_to_id}", params={"format": "metadata", "metadataHeaders": ["Message-ID", "References", "Subject"]})
        h = _headers(original.get("payload", {}))
        if h.get("message-id"):
            mail["In-Reply-To"] = h["message-id"]
            mail["References"] = (h.get("references", "") + " " + h["message-id"]).strip()
        payload["threadId"] = original.get("threadId")
        if not subject:
            subject = _decode(h.get("subject", ""))
        if subject and not subject.lower().startswith(("re:", "ответ:")):
            subject = "Re: " + subject
    mail["Subject"] = subject
    mail.set_content(body)
    payload["raw"] = base64.urlsafe_b64encode(mail.as_bytes()).decode()
    sent = call("POST", f"{BASE}/messages/send", json=payload)
    events.log("message_out", body, channel="gmail", to=to, cc=cc, subject=subject, mail_id=sent.get("id"))
    return f"Письмо отправлено: {to}, «{subject}» [{sent.get('id', '')}]"


# ---------- the whole mailbox ----------
PAGE = 500          # ids per list page (Gmail's maximum)
MOST = 10000        # messages one census or one bulk action covers at most
CHUNK = 1000        # ids per batchModify (Gmail's limit)
ACTIONS = {"archive": ("в архив", [], ["INBOX"]), "read": ("отметить прочитанными", [], ["UNREAD"]), "trash": ("в корзину", None, None),
           "label": ("поставить метку", "label", []), "unlabel": ("снять метку", [], "label")}


def ids(query, most=MOST):
    """Ids of every message a Gmail search finds, page by page, up to most."""
    found, token = [], None
    while len(found) < most:
        cancel.check_stop()
        params = {"q": query, "maxResults": PAGE, **({"pageToken": token} if token else {})}
        page = call("GET", f"{BASE}/messages", params=params)
        found += [m["id"] for m in page.get("messages", [])]
        token = page.get("nextPageToken")
        if not token:
            break
    return found[:most]


HEADERS = ["From", "Subject", "Date", "List-Unsubscribe", "List-Unsubscribe-Post"]


def _metas(message_ids):
    def meta(i):
        try:
            return call("GET", f"{BASE}/messages/{i}", params={"format": "metadata", "metadataHeaders": HEADERS})
        except GoogleError:
            return None
    out = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for n, m in enumerate(pool.map(meta, message_ids), 1):
            if m:
                out.append(m)
            if n % 500 == 0:
                status.show(f"Смотрю почту: {n} из {len(message_ids)}")
    return out


def unsubscribe_way(headers):
    """How a sender lets one unsubscribe: ("click", url) — the one-click link (RFC 8058), ("mail", address, subject),
    ("link", url) — a page to open, or None."""
    raw = headers.get("list-unsubscribe", "")
    links = re.findall(r"<([^>]+)>", raw) or [p.strip() for p in raw.split(",") if p.strip()]
    https = next((l for l in links if l.lower().startswith("https://")), None)
    if https and "one-click" in headers.get("list-unsubscribe-post", "").lower():
        return ("click", https)
    mailto = next((l for l in links if l.lower().startswith("mailto:")), None)
    if mailto:
        parsed = urlparse(mailto)
        subject = (parse_qs(parsed.query).get("subject") or ["unsubscribe"])[0]
        return ("mail", unquote(parsed.path), subject)
    return ("link", https) if https else None


def census(query="in:inbox", limit=2000):
    """Who writes how much: every message the search finds (up to limit), grouped by sender — how many, unread, the newest,
    an example subject, whether one can unsubscribe. The model sees the table, not the thousands of mails."""
    found = ids(query, clamp(limit or 2000, 2000, 1, MOST))
    if not found:
        return "Писем по этому запросу нет"
    senders = defaultdict(lambda: {"n": 0, "unread": 0, "last": 0.0, "subject": "", "name": "", "unsub": None})
    metas = _metas(found)
    for m in metas:
        h = _headers(m.get("payload", {}))
        name, address = parseaddr(_decode(h.get("from", "")))
        s = senders[(address or name or "?").lower()]
        s["n"] += 1
        s["unread"] += "UNREAD" in m.get("labelIds", [])
        try:
            when = parsedate_to_datetime(h.get("date", "")).timestamp()
        except (TypeError, ValueError):
            when = 0.0
        if when >= s["last"]:
            s.update(last=when, subject=_decode(h.get("subject", ""))[:70], name=name[:40])
        s["unsub"] = s["unsub"] or unsubscribe_way(h)
    ways = {"click": "отписка в один клик", "mail": "отписка письмом", "link": "отписка на сайте"}
    rows = sorted(senders.items(), key=lambda kv: -kv[1]["n"])
    lines = [f"{i}. {s['name'] or a} <{a}> — {s['n']} писем" + (f" (не прочитано {s['unread']})" if s["unread"] else "")
             + (f", последнее {time.strftime('%d.%m.%Y', time.localtime(s['last']))}" if s["last"] else "")
             + f", «{s['subject']}»" + (f", {ways[s['unsub'][0]]}" if s["unsub"] else "")
             for i, (a, s) in enumerate(rows[:60], 1)]
    missed = len(found) - len(metas)   # headers Gmail didn't give (its per-second limit): counted, not dropped in silence
    head = (f"Просмотрено писем: {len(metas)} по запросу «{query}»" + (" (это предел за раз)" if len(found) >= MOST else "")
            + (f"; ещё {missed} не прочитались (Gmail просил подождать) — повтори перепись позже, чтобы учесть всех" if missed else "")
            + f"; отправителей: {len(senders)}. Самые частые:")
    return external(head + "\n" + "\n".join(lines), "почта: перепись отправителей")


def bulk_plan(query, action, label=""):
    """What a bulk action would touch: (ids, the words for the owner's question) — nothing is changed here."""
    if action not in ACTIONS:
        raise GoogleError(f"action — одно из: {', '.join(ACTIONS)}")
    if action in ("label", "unlabel") and not label.strip():
        raise GoogleError("назови метку")
    found = ids(query)
    if not found:
        return [], f"По запросу «{query}» писем нет"
    sample = [_decode(_headers(m.get("payload", {})).get("subject", "")) for m in _metas(found[:5])]
    title = ACTIONS[action][0] + (f" «{label}»" if label else "")
    more = " (это предел за раз, остальное — следующим заходом)" if len(found) >= MOST else ""
    return found, f"{title}: {len(found)} писем по запросу «{query}»{more}. Например: " + "; ".join(f"«{s[:60]}»" for s in sample if s)


def bulk_apply(message_ids, action, label=""):
    """Do it, in Gmail's batches: labels and archive by batchModify, the trash message by message (kept there 30 days)."""
    failed, why = [], ""   # one batch failing doesn't hide what the others did: the owner hears "N of M" (n8n, Dify)

    def trash(i):
        try:
            call("POST", f"{BASE}/messages/{i}/trash", idempotent=True)
            return None
        except GoogleError as e:
            return i, str(e)
    if action == "trash":
        with ThreadPoolExecutor(max_workers=8) as pool:
            for miss in filter(None, pool.map(trash, message_ids)):
                failed.append(miss[0])
                why = why or miss[1]
    else:
        _, add, remove = ACTIONS[action]
        add = _label_ids([label], create=True) if add == "label" else _label_ids(add)
        remove = _label_ids([label]) if remove == "label" else _label_ids(remove)
        for i in range(0, len(message_ids), CHUNK):
            part = message_ids[i:i + CHUNK]
            try:
                call("POST", f"{BASE}/messages/batchModify", idempotent=True, json={"ids": part, "addLabelIds": add, "removeLabelIds": remove})
            except GoogleError as e:
                failed += part
                why = why or str(e)
    done = len(message_ids) - len(failed)
    events.log("google", f"почта: {ACTIONS[action][0]} — {done} из {len(message_ids)} писем", action=action, count=done,
               failed=len(failed) or None, label=label or None)
    head = f"Готово: {ACTIONS[action][0]}{f' «{label}»' if label else ''} — {done} из {len(message_ids)} писем"
    if not failed:
        return f"Готово: {ACTIONS[action][0]}{f' «{label}»' if label else ''} — {len(message_ids)} писем"
    text = f"{head}; не вышло {len(failed)} ({why[:200]}). Повтори тот же запрос — сделанное он уже не захватит, если действие меняет выборку."
    return Failure(text) if not done else text


FILTER_ACTIONS = {"archive": ({}, {"removeLabelIds": ["INBOX"]}), "read": ({}, {"removeLabelIds": ["UNREAD"]}),
                  "trash": ({}, {"addLabelIds": ["TRASH"]}), "label": ({}, {})}


def filter_plan(sender="", query="", action="archive", label=""):
    """A Gmail filter for new mail: (the request body, the words for the owner) — created only after his "yes"."""
    criteria = {k: v for k, v in (("from", sender.strip()), ("query", query.strip())) if v}
    if not criteria:
        raise GoogleError("нужен отправитель или запрос, к каким письмам применять фильтр")
    if action not in FILTER_ACTIONS or (action == "label" and not label.strip()):
        raise GoogleError(f"action — одно из: {', '.join(FILTER_ACTIONS)}; для label нужна метка")
    body = {"criteria": criteria, "action": dict(FILTER_ACTIONS[action][1])}
    if action == "label":
        body["action"] = {"addLabelIds": _label_ids([label], create=True)}
    said = {"archive": "сразу в архив", "read": "сразу прочитанными", "trash": "сразу в корзину", "label": f"с меткой «{label}»"}[action]
    return body, f"новые письма {', '.join(f'{k}: {v}' for k, v in criteria.items())} — {said}"


def filter_apply(body):
    try:
        made = call("POST", f"{BASE}/settings/filters", json=body)
    except GoogleError as e:
        if "insufficient" in str(e).lower() or "403" in str(e):
            from sena.google import auth   # same layer: the sign-in words live there
            return Failure("Фильтр не создан: у входа в Google нет права на настройки почты. " + auth.HOW_TO_SIGN_IN)
        raise
    events.log("google", "почта: фильтр создан", filter=made.get("id"), criteria=body.get("criteria"))
    return f"Фильтр создан [{made.get('id', '')}]"


def unsubscribe_plan(sender):
    """How to unsubscribe from a sender, from the newest mail of theirs: (way, the words for the owner) or (None, why not)."""
    found = call("GET", f"{BASE}/messages", params={"q": f"from:{sender}", "maxResults": 1}).get("messages", [])
    if not found:
        return None, f"Писем от «{sender}» нет"
    metas = _metas([found[0]["id"]])
    if not metas:
        return None, f"Письмо от «{sender}» не прочиталось"
    way = unsubscribe_way(_headers(metas[0].get("payload", {})))
    if not way:
        return None, f"У писем «{sender}» нет стандартной отписки: только по ссылке внутри письма"
    if way[0] == "link":
        return None, f"Отписка — только на их странице: {way[1]} (открыть может агент браузера, с твоего «да»)"
    return way, (f"отписаться от «{sender}» в один клик" if way[0] == "click" else f"отписаться от «{sender}» письмом на {way[1]}")


TOOLS = [
    tool("mail_search", "Найти письма в Gmail. query — поисковая строка Gmail: is:unread, from:имя, newer_than:2d, has:attachment, subject:слово, label:метка; без query — последние.",
         {"query": string("Поисковая строка Gmail"), "limit": {"type": "integer", "description": "Сколько писем, по умолчанию 10, максимум 25"}}),
    tool("mail_read", "Прочитать письмо целиком по id из mail_search: заголовки, текст, список вложений.", {"id": string()}, ["id"]),
    tool("mail_labels", "Метки почты и число непрочитанных во входящих."),
    tool("mail_modify", "Изменить метки писем: архив — убрать INBOX; прочитано — убрать UNREAD; непрочитано — добавить UNREAD; со звездой — добавить STARRED. Нужной своей метки нет — создастся.",
         {"ids": strings("id писем"), "add_labels": strings("добавить метки"), "remove_labels": strings("убрать метки")}, ["ids"]),
    tool("mail_trash", "Переместить письма в корзину (навсегда не удаляется, корзина хранит 30 дней) или вернуть из неё.",
         {"ids": strings("id писем"), "restore": {"type": "boolean", "description": "true — вернуть из корзины"}}, ["ids"]),
    tool("mail_attachment", "Сохранить вложение письма на компьютер, вернёт путь (файл Теме — send_file).",
         {"message_id": string(), "filename": string("Имя вложения из mail_read")}, ["message_id", "filename"]),
    tool("mail_census", "Перепись почты: все письма по запросу (до 10 000), сгруппированные по отправителям — сколько, не прочитано, последнее, "
                        "пример темы, можно ли отписаться. Начало любой большой уборки ящика.",
         {"query": string("поисковая строка Gmail, по умолчанию in:inbox; например category:promotions older_than:1y"),
          "limit": {"type": "integer", "description": "сколько писем просмотреть, по умолчанию 2000"}}),
]
FUNCTIONS = {"mail_search": search, "mail_read": read, "mail_labels": labels, "mail_modify": modify,
             "mail_trash": trash, "mail_attachment": attachment, "mail_census": census}
