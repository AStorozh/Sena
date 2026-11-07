"""What can be shown to the model, what the terminal never runs, and background tasks. Model-written code runs in core/sandbox.py."""
import os
import re
from html.parser import HTMLParser
import sys
import threading
import unicodedata
import uuid
from pathlib import Path

from sena import config
from sena.core import events, status, store

# What the agent's terminal never executes, no matter how it's asked. The first line of defense is the prohibition in the
# instructions, but text from a page, email or chat can talk the model into it. This is the second line, not a sandbox: a
# clever phrasing can still get around it, which is why the keys are also stripped from the environment (clean_env), and
# anything irreversible still requires Tema's confirmation.
FORBIDDEN = (
    (r"(^|[\s/\'\"=:~])\.env(?!\.example)\b|\.session\b|google_token|google_client|browser_profile|data/state", "файлы с ключами и входами Сены"),
    (r"Library/Keychains|\bsecurity\s+(find|dump|export|import|add|delete|unlock|set)-|Application Support/Google/Chrome|Login Data",
     "связка ключей и пароли браузера"),
    (r"\.ssh\b|id_rsa|id_ed25519|\.aws\b|\.netrc|\.gnupg", "ключи доступа к серверам"),
    (r"\bsudo\b|\bsu\s+-|\bdoas\b", "права администратора"),
    (r"\brm\s+-\w*[rR]\w*[fF]|\brm\s+-\w*[fF]\w*[rR]|\brm\s+--recursive|\bmkfs|\bdiskutil\s+(erase|reformat|partition)|\b(shutdown|reboot|halt)\b",
     "необратимое удаление, выключение"),
    (r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da)?sh\b|\b(nc|ncat|netcat)\b", "запуск скачанного скрипта, сырой обмен по сети"),
)


CLAUDE_API_PREFIXES = ("SENA_CLAUDE_", "ANTHROPIC_")


PARTS = re.compile(r"&&|\|\||[;|&\n]|\$\(|`")


def _deletes(command):
    """Whether a command deletes recursively, however it is spelled: rm with -r/-R/--recursive anywhere among its flags
    (rm -r -f, rm -fr, rm --force --recursive, rm -r dir), find … -delete or -exec rm, xargs rm. Every part of a compound
    command is checked (gemini-cli's policy engine parses each part; the regex alone let these through)."""
    for part in PARTS.split(command):
        words = part.split()
        while words and (words[0] in ("sudo", "env", "command", "builtin", "nohup", "time", "nice") or "=" in words[0]):
            words = words[1:]   # wrappers and VAR=value before the real command
        if not words:
            continue
        name = words[0].rsplit("/", 1)[-1]
        flags = [w for w in words[1:] if w.startswith("-")]
        if name == "rm" and any(f in ("-r", "-R", "--recursive") or (not f.startswith("--") and ("r" in f[1:] or "R" in f[1:])) for f in flags):
            return True
        if name == "find" and ("-delete" in words or ("-exec" in words and any(w.rsplit("/", 1)[-1] == "rm" for w in words))):
            return True
        if name == "xargs" and any(w.rsplit("/", 1)[-1] == "rm" for w in words[1:]):
            return True
    return False


def forbidden_command(command):
    """Refusal text for the model if the command touches secrets or is irreversible; None — it's fine to run."""
    reasons = [why for pattern, why in FORBIDDEN if re.search(pattern, command, re.I)]
    if not reasons and _deletes(command):
        reasons = ["необратимое удаление папок (вместо этого — в корзину: mv … ~/.Trash/)"]
    if reasons:
        events.log("tool", command, blocked=True, reason=reasons[0], private=True)
        return (f"Команда заблокирована защитой Сены: {reasons[0]}. Не пытайся обойти другой записью. "
                "Если это правда нужно Теме, остановись и скажи, что сделать это он должен сам.")


# Keys and logins wherever they lie, relative to the home folder: the terminal's sandbox hides them from every command
# (core/seatbelt.py) and no file tool reads or sends them (secret_path) — one list, so the two can't drift apart
SECRET_PLACES = (".ssh", ".aws", ".gnupg", ".config/gh", ".netrc", "Library/Keychains", "Library/Application Support/Google/Chrome",
                 ".claude/.credentials.json", ".codex/auth.json")
# what the owner's own programs read as they work (git, npm, pip, docker, kubectl, gcloud) — so a command in the sandbox may —
# but never the model nor a file sent anywhere: other services' tokens, his messages, other browsers' cookies and passwords
TOOL_SECRETS = (".git-credentials", ".npmrc", ".pypirc", ".docker/config.json", ".kube/config", ".config/gcloud", "Library/Messages",
                "Library/Cookies", "Library/Safari", "Library/Containers/com.apple.Safari", "Library/Application Support/Firefox",
                "Library/Application Support/BraveSoftware", "Library/Application Support/Microsoft Edge",
                "Library/Application Support/Arc", "Library/Application Support/Yandex")


def secret_places():
    """SECRET_PLACES as real paths on this Mac (the sandbox's hidden list)."""
    return [os.path.realpath(Path.home() / p) for p in SECRET_PLACES]


def forbidden_path(path, to_owner=False):
    """Why a file must not leave through an agent (a site upload, a page it reads, a file sent); None — it may. Sena's own data
    stays with her — her data folder and her notes vault: only what the owner sent, what was downloaded and what she made can go
    out (inbox, downloads, generated). to_owner — the file goes to the owner himself (his own chat): a whole output she showed
    him in parts (spill) and her notes may too."""
    real = os.path.realpath(os.path.expanduser(str(path)))
    shown = (config.SPILL_DIR, config.VAULT_DIR) if to_owner else ()
    public = [os.path.realpath(d) for d in (config.INBOX_DIR, config.DOWNLOADS_DIR, config.GENERATED_DIR, *shown)]
    if _inside(real, os.path.realpath(config.DATA)) and not any(_inside(real, d) for d in public):
        return "личные данные Сены (память, журналы, настройки)"
    if not to_owner and _inside(real, os.path.realpath(config.VAULT_DIR)):
        return "заметки Сены (хранилище Obsidian)"
    return secret_path(real)


def secret_path(path):
    """Why a file is never read into the model nor sent anywhere: keys, logins, the keychain, server keys, other services'
    tokens, his messages and browsers' stores — wherever they are; everything the sandbox hides is here too. None — it may be read."""
    real = os.path.realpath(os.path.expanduser(str(path)))
    if _inside(real, os.path.realpath(config.ROOT)) and os.path.basename(real).startswith(".env") and not real.endswith(".example"):
        return "файлы с ключами Сены"
    for pattern, why in FORBIDDEN[:3]:
        if re.search(pattern, real, re.I):
            return why
    if any(_inside(real, p) for p in secret_places()):
        return "ключи и входы (их не видит и терминал)"
    if any(_inside(real, os.path.realpath(Path.home() / p)) for p in TOOL_SECRETS):
        return "ключи других сервисов, переписка и пароли браузеров"
    return None


def _inside(path, folder):
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


# a variable named like a key, or holding a value shaped like one, is someone's secret whoever set it: the owner's shell brings
# GITHUB_TOKEN, AWS_SECRET_ACCESS_KEY or HF_TOKEN that no list of Sena's own keys knows (E2B passes only what is named)
SECRET_PARTS = {"KEY", "KEYS", "APIKEY", "TOKEN", "TOKENS", "SECRET", "SECRETS", "PASSWORD", "PASSWD", "PASS", "PAT",
                "CREDENTIAL", "CREDENTIALS", "COOKIE", "COOKIES"}
SECRET_VALUE = re.compile(r"^(sk-|sk_live_|ghp_|gho_|ghs_|github_pat_|glpat-|xox[abpr]-|AKIA[0-9A-Z]{12}|AIza[\w-]{30}|hf_[A-Za-z0-9]{20})")


def secret_like(name, value=""):
    """Does this environment variable look like a secret, by its name's parts or its value's shape?"""
    return bool(SECRET_PARTS & set(re.split(r"[_\W]+", name.upper()))) or bool(SECRET_VALUE.match(str(value)))


def degenerate(text, before="", short_ok=False):
    """Why a model's text can't stand in for what it replaces, or None: it goes round in circles (the same lines, the same run of
    words again and again), it is empty, or — when it replaces a summary — a fraction of the one before. Mastra checks every
    observation and reflection the same way; a looped or "." summary here replaced a good one and lost its details."""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not short_ok and len(" ".join(lines)) < 40:
        return "пустой или почти пустой ответ"
    if before and len(str(text)) < len(str(before)) / 4:
        return "в разы короче прежнего"
    if len(lines) >= 10 and (len(lines) - len(set(lines))) / len(lines) > 0.5:
        return "строки повторяются"
    words = str(text or "").split()
    if len(words) >= 60:
        runs = [tuple(words[i:i + 5]) for i in range(len(words) - 4)]
        if (len(runs) - len(set(runs))) / len(runs) > 0.5:
            return "текст ходит по кругу"
    return None


def clean_env(extra=()):
    """The environment for programs Sena runs (terminal, Claude Code): without her keys and without anything shaped like one.
    If text from elsewhere talks the agent into running printenv, it gets no key, token or password — hers or the owner's."""
    hidden = set(config.SECRET_KEYS) | set(extra)
    # Claude API keys for Sena herself (SENA_CLAUDE_<role>_KEY) and any ANTHROPIC_*: code is written only through the
    # subscription, so Claude Code and the terminal never get them, whatever key name might show up later
    return {k: v for k, v in os.environ.items() if k not in hidden and not k.startswith(CLAUDE_API_PREFIXES) and not secret_like(k, v)}


# Characters that show as nothing but the model reads them: zero-width ones, bidirectional overrides, the Unicode "tag" letters
# (the channel for instructions hidden in a page or a mail, "ASCII smuggling"), and terminal colour codes that models copy
# into what they write. Taken out of foreign text and of what Sena learns, before the model sees it. The joiners (U+200C,
# U+200D) stay: scripts and emoji need them. From Hermes Agent (tools/ansi_strip.py, tools/threat_patterns.py, MIT).
INVISIBLE = re.compile("[\u200b\u200e\u200f\u2060\u2062-\u2064\ufeff\u202a-\u202e\u2066-\u2069\U000e0000-\U000e007f]")
ANSI = re.compile(r"\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def visible(text):
    """The text without invisible characters and terminal codes: what a person sees is what the model gets."""
    return ANSI.sub("", INVISIBLE.sub("", str(text)))


HTML_COMMENT = re.compile(r"<!--.*?(?:-->|$)", re.S)   # shown to no reader of a page or an issue, read by a model
HIDING = re.compile(r"display:none|visibility:hidden|font-size:0(?:px|pt|em|rem|%)?(?:;|!|$)|opacity:0(?:\.0+)?(?:;|!|$)|max-height:0(?:px)?(?:;|!|$)")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
BREAKS = {"br", "p", "div", "tr", "li", "table", "ul", "ol", "blockquote", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}


class _Shown(HTMLParser):
    # the text a reader of the markup sees: no scripts, styles or head, no comments, nothing hidden by an attribute or a style
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack = [], []

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            if tag == "br" and not any(h for _, h in self.stack):
                self.out.append("\n")
            return
        found = {k.lower(): (v or "") for k, v in attrs}
        hidden = tag in ("script", "style", "head", "template") or "hidden" in found or bool(HIDING.search(re.sub(r"\s+", "", found.get("style", "").lower())))
        if tag in BREAKS and not hidden and not any(h for _, h in self.stack):
            self.out.append("\n")
        self.stack.append((tag, hidden))

    def handle_endtag(self, tag):
        names = [t for t, _ in self.stack]
        if tag in VOID or tag not in names:
            return
        shown = not any(h for _, h in self.stack)
        del self.stack[len(names) - 1 - names[::-1].index(tag):]   # tolerant of tags left open inside
        if shown and tag in BREAKS:
            self.out.append("\n")

    def handle_data(self, data):
        if not any(h for _, h in self.stack):
            self.out.append(data)


def _script(ch):
    name = unicodedata.name(ch, "")
    return next((w for w in ("LATIN", "CYRILLIC", "GREEK", "ARMENIAN") if name.startswith(w)), None)


def look_alike(address):
    """Why an address may imitate another one: letters of two alphabets in one name (a Cyrillic «о» in gmail.com), or a domain
    written in punycode (xn--) — so it reads as a familiar one; None — it reads as what it is (awesome-copilot's homoglyph checks)."""
    for part in re.split(r"[@.\-_+]", str(address or "")):
        scripts = {_script(ch) for ch in part if ch.isalpha()} - {None}
        if len(scripts) > 1:
            return f"в «{part}» смешаны алфавиты"
    if "xn--" in str(address or "").lower():
        return "адрес записан в другой кодировке (xn--): он может выглядеть как знакомый"
    return None


def html_text(markup):
    """What a person reading the markup sees, as text: a mail can carry text for the model alone in comments and hidden
    elements, never shown to the owner (claude-code-action's sanitizer and the awesome-claude-code study)."""
    reader = _Shown()
    try:
        reader.feed(str(markup))
        reader.close()
    except Exception:   # a markup the parser gives up on: the plainest reading, without comments
        return re.sub(r"<[^>]+>", "", HTML_COMMENT.sub("", str(markup)))
    return re.sub(r"\n\s*\n+", "\n\n", "".join(reader.out)).strip()


def external(text, source, own=False):
    # tag someone else's text so the model doesn't mistake it for a command. A random id — the end marker can't be forged.
    # The task now carries foreign text (status.touched); own — Sena's own memory and the owner's notes, which don't count
    if not own:
        status.touched(source)
    tag = uuid.uuid4().hex[:6]
    text = visible(text).replace("<<<", "‹‹‹")
    return f"<<<ВНЕШНИЕ ДАННЫЕ {tag} | {source}>>>\n{text}\n<<<КОНЕЦ ВНЕШНИХ ДАННЫХ {tag}>>>"


def cut(text, limit=5000):
    # long output is trimmed in the middle: the start often has the error itself, the end has the result
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n...[вырезано {len(text) - limit} символов из {len(text)}]...\n" + text[-half:]


def spill(text, limit=5000, name="вывод", how=None, folder=None):
    """Long output isn't cut away: the whole of it goes to a file, the model gets the start and the end and where the rest is,
    and reads it page by page with view_file — instead of asking the service again (the call may already have done its
    work). Hermes Agent keeps every large result this way (tools/tool_result_storage.py, MIT).
    how — for a reader without view_file (the terminal and browser agents): what it can do instead, {path} standing for the
    file; a hint that names a tool the reader doesn't have sends it in circles (SWE-agent: every message names only the
    actions available). folder — where the whole text goes when the reader must open it itself (outside Sena's data)."""
    text = str(text)
    if len(text) <= limit:
        return text
    path = store.stamped(Path(folder) if folder else config.SPILL_DIR, f"{name}.txt")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(path, text)
    except OSError as e:   # no room on the disk: the old way, cut in the middle
        events.error("Длинный вывод в файл", e)
        return cut(text, limit)
    half = limit // 2
    note = how.replace("{path}", str(path)) if how else f"Весь текст сохранён: {path} — читай его частями через view_file(path, part=2, 3…), а не запрашивай то же заново"
    return text[:half] + f"\n...[здесь ещё {len(text) - limit} знаков из {len(text)}. {note}]...\n" + text[-half:]


class _StdoutRouter:
    """sys.stdout (or sys.stderr) that masks every known secret and writes into the current thread's buffer if one is set,
    as usual otherwise. A plain output swap affects the whole process: two simultaneous code runs (browser and Telegram)
    would interfere with each other, and the bot's messages would go missing from the log afterward."""
    def __init__(self, base):
        self.base = base
        self.local = threading.local()

    def write(self, text):
        if not getattr(self.local, "masking", False):   # a line printed while masking (the keeper's own trouble) goes as it is
            self.local.masking = True
            try:
                from sena.core import secrets   # late: secrets is built on store and events, which this module must not need at import
                text = secrets.mask(text)   # a secret never reaches bot.log or an agent's output
            except Exception:   # the keeper can't be asked (still loading, the process going down): the line still goes out
                pass
            finally:
                self.local.masking = False
        return (getattr(self.local, "buffer", None) or self.base).write(text)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def __getattr__(self, name):   # flush, encoding, isatty and the rest — from the real output
        return getattr(self.base, name)


def guard_output():
    """Every print and every traceback of the process — stdout and stderr, which run.sh writes into bot.log — goes out with
    the owner's secrets and Sena's keys masked. Installed when this module is imported, so in every Sena process from its
    start: before, only the first run of model code installed it, stderr never (a dropped connection printed the Telegram
    address with the bot's token in it)."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is not None and not isinstance(stream, _StdoutRouter):
            setattr(sys, name, _StdoutRouter(stream))


def router():
    """The process's stdout as a per-thread router (core/sandbox.py captures an agent's print through it)."""
    guard_output()
    return sys.stdout


guard_output()


def background(func, *args, what=None):
    """Run in the background. An error is logged and brings down neither the bot nor the caller."""
    def run():
        try:
            func(*args)
        except Exception as e:
            print(f"{what or func.__name__}: не удалось:", e)
            events.error(what or func.__name__, e)
    threading.Thread(target=run, daemon=True).start()
