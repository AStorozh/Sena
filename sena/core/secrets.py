"""The keeper of the owner's secrets: passwords and codes he wants Sena to hold and use. Stored encrypted (AES-GCM) in
data/state/secrets.enc with a key that lives only on this Mac, in its Keychain (core/keychain.py; without it — data/state/secrets.key,
0600); nightly backups copy the file, never the key; the encrypted copy off the Mac (core/offsite.py) carries both. The model never sees a value: an agent asks for a ticket (use), and the tool that needs the secret resolves it in
its own code; the owner gets a value back only through a confirmation (the app behind its lock, or his "yes" in the chat);
every access is a line in the event journal — name, who, why, never the value; and mask() wipes a value out of any text
that is logged, printed or returned to the model. Values are entered only in the app: a password typed into the chat is
already in the conversation, the registry and the model's hands before any tool runs."""
import base64
import json
import os
import re
import threading
import time
import uuid
from urllib.parse import quote, urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sena import config
from sena.core import events, keychain, moment, store
from sena.core.tools import Failure

MIN_LEN = 8          # shorter values aren't accepted: they couldn't be masked without wiping ordinary words
TICKET_TTL = 900     # an agent's ticket is good for a quarter of an hour
NAME = re.compile(r"^[\w.\-]{2,40}$")
_lock = threading.RLock()
_data = None         # the decrypted store, loaded once
_tickets = {}        # token -> {"name", "agent", "until"}


_key_bytes = None   # read once: masking runs on every journal line
RETRY = 60          # seconds the masking leaves a store that couldn't be opened alone before asking the Keychain again
_failed = None      # when the last failed opening of the store may be tried again by the masking (time.monotonic)


def _key():
    """The key to the store: in the Mac's Keychain (core/keychain.py), so a copied data folder holds no key. A key file from
    before is moved there once and removed only after the Keychain gives the same key back. Without the Keychain — the file.
    A new key is made only while there is nothing to open with the old one: a Keychain that can't be read raises
    (keychain.Unavailable), and a store without its key is refused, never re-keyed."""
    global _key_bytes
    if _key_bytes:
        return _key_bytes
    path = config.SECRETS_KEY
    if keychain.enabled():
        found = keychain.get(keychain.SECRETS)
        if found:
            _key_bytes = bytes.fromhex(found)
            return _key_bytes
        if not path.exists() and config.SECRETS_FILE.exists():
            raise RuntimeError("Ключа секретов нет ни в Связке ключей, ни в файле, а хранилище есть: новый ключ сделал бы его "
                               "нечитаемым. Ключ есть в копии вне Mac: python -m sena.core.offsite restore")
        key = path.read_bytes() if path.exists() else os.urandom(32)
        if keychain.put(keychain.SECRETS, key.hex()) and keychain.get(keychain.SECRETS) == key.hex():
            path.unlink(missing_ok=True)   # the Keychain holds it now: no second copy of the key next to the data
            _key_bytes = key
            return key
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            store.write_bytes(path, key, mode=0o600)
        _key_bytes = key
        return key
    if not path.exists():
        if config.SECRETS_FILE.exists():
            raise RuntimeError("Файла ключа секретов нет, а хранилище есть: новый ключ сделал бы его нечитаемым")
        path.parent.mkdir(parents=True, exist_ok=True)
        store.write_bytes(path, os.urandom(32), mode=0o600)
    _key_bytes = path.read_bytes()
    return _key_bytes


def key_for_copy():
    """The store's key, for the encrypted off-site copy only (core/offsite.py): without it the copied secrets can't be opened."""
    return _key()


def _load():
    """The store, opened once. A store that can't be opened (the Keychain locked or asking, the key lost, a damaged file) raises
    for whoever asked, and is marked: the masking — every journal line and every print asks it — leaves it alone for RETRY,
    instead of a `security` run (and a Keychain prompt) per line, every logging thread waiting behind this lock."""
    global _data, _failed
    if _data is None:
        try:
            if config.SECRETS_FILE.exists():
                blob = config.SECRETS_FILE.read_bytes()
                _data = json.loads(AESGCM(_key()).decrypt(blob[:12], blob[12:], None).decode("utf-8"))
            else:
                _data = {"items": {}, "gone": {}}
        except Exception as e:
            first = _failed is None
            _failed = time.monotonic() + RETRY
            if first:   # said once per spell of failures; the journal's own masking finds the mark already set
                events.error("Хранилище секретов не открылось", e)
            raise
        _failed = None
    return _data


def _save():
    nonce = os.urandom(12)
    blob = nonce + AESGCM(_key()).encrypt(nonce, json.dumps(_data, ensure_ascii=False).encode("utf-8"), None)
    store.write_bytes(config.SECRETS_FILE, blob, mode=0o600)


def _reset():
    # for tests: forget the loaded store, its key, a failure to open it and the tickets
    global _data, _key_bytes, _failed
    with _lock:
        _data = None
        _key_bytes = None
        _failed = None
        _tickets.clear()


def names():
    """What is kept, without the values: [{name, note, allow, sites, reveal, t, tz, changed}]."""
    with _lock:
        items = _load()["items"]
        return [{"name": n, "note": r.get("note", ""), "allow": list(r.get("allow", [])), "sites": list(r.get("sites", [])),
                 "reveal": r.get("reveal", "confirm"), "t": r["t"], "tz": r.get("tz", ""), "changed": len(r.get("history", []))}
                for n, r in sorted(items.items())]


def _site(text):
    # "https://www.hh.ru/login" -> "hh.ru": the host a password belongs to, its subdomains included
    host = urlsplit(text if "//" in text else "//" + text).hostname or ""
    return host.lower().removeprefix("www.").rstrip(".")


def off_site(name, host):
    """Why the secret must not be typed on this site, or None. A password bound to its sites is typed only there and on their
    subdomains: a page that talks the agent into entering it elsewhere gets nothing (Skyvern ties a credential to its site)."""
    with _lock:
        record = _load()["items"].get(name)
    sites = (record or {}).get("sites") or []
    host = _site(host or "")
    if not sites or any(host == s or host.endswith("." + s) for s in sites):
        return None
    events.log("secret", f"не на своём сайте: {name} → {host or 'без адреса'}", name=name, host=host, granted=False)
    return f"Секрет «{name}» вводится только на {', '.join(sites)}, а эта страница — {host or 'без адреса'}: не ввожу"


def name_of(value):
    """The name of the owner's secret with exactly this value, or None: tells where a text being typed belongs."""
    with _lock:
        return next((n for n, r in _load()["items"].items() if value and r.get("value") == value), None)


def put(name, value, note=None, allow=None, reveal="confirm", sites=None):
    """Keep a secret (new or changed). value may be empty when only the note, the agents allowed, the sites or the reveal rule
    change. note, allow — the agents that may use it, sites — the hosts a password may be typed on: None keeps them as they
    are, a value replaces them ("" — no note, [] — no agent / any site). A refusal is a Failure: the app shows it instead of "saved"."""
    name = (name or "").strip()
    if not NAME.match(name):
        return Failure("Имя секрета: 2–40 знаков — буквы, цифры, точка, дефис, подчёркивание")
    value = (value or "").strip()
    if reveal not in ("confirm", "never"):
        return Failure("reveal — confirm (показывать владельцу после подтверждения) или never")
    with _lock:
        items = _load()["items"]
        old = items.get(name)
        if not value and not old:
            return Failure(f"Значение секрета «{name}» не задано")
        if value and len(value) < MIN_LEN:
            return Failure(f"Слишком коротко: от {MIN_LEN} знаков, иначе его не спрятать в текстах")
        now, tz = moment.now(), moment.offset()
        record = dict(old) if old else {"t": now, "tz": tz, "history": []}
        if value and (not old or old["value"] != value):
            if old:
                record["history"] = record.get("history", []) + [{"value": old["value"], "t": old["t"], "tz": old.get("tz", "")}]
            record.update(value=value, t=now, tz=tz)
        record.update(note=(str(note) if note is not None else record.get("note", ""))[:200], reveal=reveal,
                      allow=sorted({a for a in allow if a}) if allow is not None else record.get("allow", []))
        if sites is not None:
            record["sites"] = sorted({h for h in map(_site, sites) if h})
        items[name] = record
        _save()
    events.log("secret", f"секрет {'обновлён' if old else 'записан'}: {name}", name=name, allow=record["allow"], sites=record.get("sites") or None)
    return f"Секрет «{name}» {'обновлён' if old else 'сохранён'}"


def remove(name):
    """Take a secret out of use: it moves to the "gone" shelf (nothing is ever destroyed), agents can't use it anymore."""
    with _lock:
        data = _load()
        record = data["items"].pop(name, None)
        if not record:
            return Failure(f"Секрета «{name}» нет")
        data["gone"][f"{name}@{moment.now():.0f}"] = record
        _save()
    events.log("secret", f"секрет убран: {name}", name=name)
    return f"Секрет «{name}» убран"


def allowed(name, agent):
    with _lock:
        record = _load()["items"].get(name)
        return bool(record and agent in record.get("allow", []))


def grant(name, agent):
    """The owner allowed an agent to use the secret (after his confirmation)."""
    with _lock:
        record = _load()["items"].get(name)
        if not record:
            return Failure(f"Секрета «{name}» нет")
        record["allow"] = sorted(set(record.get("allow", [])) | {agent})
        _save()
    events.log("secret", f"доступ разрешён: {name} → {agent}", name=name, agent=agent)
    return f"Агент {agent} теперь может использовать «{name}»"


def use(name, agent, purpose=""):
    """An agent asks for a secret: a ticket (opaque token) when the agent is allowed, otherwise None. Logged either way."""
    with _lock:
        record = _load()["items"].get(name)
        ok = bool(record and agent in record.get("allow", []))
        token = uuid.uuid4().hex if ok else None
        if ok:
            _tickets[token] = {"name": name, "agent": agent, "until": moment.now() + TICKET_TTL}
    events.log("secret", f"{'выдан' if ok else 'отказано'}: {name} → {agent}" + (f" ({purpose[:120]})" if purpose else ""),
               name=name, agent=agent, granted=ok)
    return token


def resolve(token):
    """The value behind a ticket — for the code of a tool, never for the model's text. None when the ticket is unknown or stale."""
    with _lock:
        ticket = _tickets.get(token)
        if not ticket or ticket["until"] < moment.now():
            _tickets.pop(token, None)
            return None
        record = _load()["items"].get(ticket["name"])
        return record["value"] if record else None


def reveal(name, how):
    """The value for the owner himself — only from a confirmed path (how — "pult" or "чат"); the journal says it happened."""
    with _lock:
        record = _load()["items"].get(name)
    if not record:
        return None
    if record.get("reveal") == "never":
        return None
    events.log("secret", f"показан владельцу ({how}): {name}", name=name)
    return record["value"]


def values():
    # the owner's secrets and Sena's own keys; each also in the encoded forms a page or a script would print it in
    # the earlier values of a secret and the removed ones are masked too: an old password is often still the password somewhere
    # (the n8n study found them kept but never wiped from texts). Sena's own keys are masked even when the store can't be read.
    # Longest first: a password that is the start of another one ("Tema2024pass", "Tema2024pass!Bank") would otherwise cut the
    # longer one's value and leave its tail ("[секрет:почта]!Bank") in every text
    known = list(config.secret_values())
    if _data is not None or _failed is None or time.monotonic() >= _failed:   # a store that just failed to open isn't asked per line
        try:
            with _lock:
                data = _load()
                shelves = [(n, r) for n, r in data["items"].items()] + [(n.split("@", 1)[0], r) for n, r in data.get("gone", {}).items()]
                known += [(v, n) for n, r in shelves for v in [r.get("value", ""), *(h.get("value", "") for h in r.get("history", []))] if len(v) >= MIN_LEN]
        except Exception:   # no key, a damaged file: the owner's secrets can't be known, the keys above still are
            pass
    return sorted(((form, name) for value, name in known for form in _forms(value)), key=lambda pair: -len(pair[0]))


def _forms(value):
    # the value as written, and as a page, a link or a script would print it: base64, base64url, hex, URL-encoded (a password
    # with # or + in a link), reversed (promptfoo's leak checks look for the same forms), and escaped inside JSON (a password
    # with " or \ in a journal line's details)
    raw = value.encode()
    return dict.fromkeys([value, base64.b64encode(raw).decode().rstrip("="), base64.urlsafe_b64encode(raw).decode().rstrip("="), raw.hex(),
                          quote(value, safe=""), value[::-1], json.dumps(value, ensure_ascii=False)[1:-1], json.dumps(value)[1:-1]])


CARD = re.compile(r"(?<![\d.,])\d(?:[ -]?\d){12,18}(?![\d.,]?\d)")


def _luhn(digits):
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def _card(digits):
    # the lengths and first digits of real card numbers (Visa, Mastercard, Mir, UnionPay, JCB, Amex): a product code, a phone
    # or an order number of another length or start is left alone
    n, two, four = len(digits), digits[:2], digits[:4]
    if n == 15:
        return two in ("34", "37")
    if n not in (16, 19):
        return False
    return (digits[0] == "4" or "2200" <= four <= "2204" or two == "62"
            or n == 16 and ("51" <= two <= "55" or "2221" <= four <= "2720" or "3528" <= four <= "3589"))


def cards(text):
    """A bank card number in a text becomes [карта …1234] (its length, first digits and Luhn's check digit say it is one —
    LangChain's PII redaction): it doesn't reach the correspondence, the search, the journal or the cloud model."""
    if not isinstance(text, str) or len(text) < 15:
        return text

    def hide(m):
        digits = re.sub(r"\D", "", m.group(0))
        return f"[карта …{digits[-4:]}]" if _card(digits) and _luhn(digits) else m.group(0)
    return CARD.sub(hide, text)


def mask(text):
    """Any known value in a text becomes [секрет:name], a card number [карта …1234]. Cheap enough to run on every logged line."""
    if not isinstance(text, str) or not text:
        return text
    text = cards(text)
    try:
        known = values()
    except Exception:   # never block logging over the masking itself
        return text
    for value, name in known:
        if value in text:
            text = text.replace(value, f"[секрет:{name}]")
    return text


events.mask_hook = mask   # the journal wipes values out of everything it records
