"""Sena's behavior settings. Style (voice, reply length, reasoning depth) isn't changed by hand — Tema asks for it in
conversation ("answer shorter", "think deeper", "speak with the home voice"), Sena records it with the set_style tool and
uses it from then on (both in the app and in Telegram). Background behavior (checking, cleanup, review, active hours) is
toggled by Tema himself from the console on the Mac.
Not to be confused with config.py: that one holds paths and keys."""
import threading
from datetime import datetime

from sena import config
from sena.core import store
from sena.core.tools import string, tool

DEFAULTS = {
    "voice": config.VOICE,  # the voice of replies: Sena's one voice (Gemini, picked by the owner by ear)
    "verbosity": "low",    # reply length: low — brief, medium — more detailed
    "effort": "low",       # the coordinator's reasoning: low — fast, medium — deeper
    # background (console on the Mac)
    "heartbeat": True,            # background check: chats, mail, upcoming events
    "heartbeat_every_min": 60,    # how often
    "active_from": 9,             # active hours: from 9:00
    "active_to": 23,              # until 22:59 (don't disturb at night)
    "watch": True,                # price alerts without the model
    "watch_every_min": 5,         # how often to check prices
    "tidy": True,                 # nightly cleanup of memory and backups
    "tidy_hour": 4,               # not before this hour
    "weekly": True,               # weekly quality review
    "weekly_day": 6,              # which day of the week: 0 — Monday … 6 — Sunday
    "weekly_hour": 20,            # and from which hour
    "exam": True,                 # with the weekly review: the exam of her mind on the real model (sena/exam), about 1-2 dollars
    "exam_budget_usd": 3,         # one exam run never spends more than this
    "judge": True,                # review after tasks with agents (learning from experience)
    "reflect": True,              # reflections on her own personality
    "voice_reply": True,          # reply with voice to a voice message in Telegram too
    "budget_usd_day": 0,          # daily budget for paid calls, in dollars; 0 — no limit
    "harvest": True,              # long facts the owner mentions in passing are kept from the conversation leaving the window
    "telegram_notify": True,      # Sena's own, unprompted messages to the Telegram bot (night check, reminders, background notices);
                                   # off — she writes nowhere but the app; a reply to what he writes there isn't affected
    "task_budget_usd": 5,         # one task never spends more than this: past it the brain sums up and stops (a long work has its own budget)
    # integrations (the "Integrations" section in the app): the model doesn't see a disabled one, its tool disappears from its list
    "use_telegram_account": True,  # Tema's chats in Telegram (connected when the core starts)
    "use_browser": True,           # ag_web: Chrome
    "use_claude": True,            # ag_code: Claude Code
    "use_google": True,            # mail, Sheets, Drive
    "use_gemini": True,            # images and video
    "use_maps": True,              # TomTom and Google Maps
    "use_travel": True,            # tickets
    "use_luma": True,              # Luma, the owner's app: his to-dos and projects, calendar, habits, time, expenses, notes
    "use_mcp": True,               # the owner's MCP servers (each one only after his approval in the app)
    "use_site": True,              # portfolio site: texts, application links, analytics
    "use_github": True,            # GitHub: repositories, issues, pull requests, profile
    "use_hireflow": True,          # Hire-flow: job search on hh.ru, job evaluation, letters, texts
    "use_youtube": True,           # YouTube: own channel (videos, comments, stats)
    "use_hearing": True,           # background hearing from the phone (whisper on the Mac)
    "roles": {},                   # roles moved away from where config.ROLES puts them: {"compact": "home"} (core/roles.py)
}
HOME_VOICE = "home"   # the voice on this Mac (Piper's Irina or macOS Milena): the reply text doesn't leave the house to be spoken
OPTIONS = {
    "voice": {config.VOICE: "голос Сены", HOME_VOICE: "домашний голос"},
    "verbosity": {"low": "кратко", "medium": "подробно"},
    "effort": {"low": "быстро", "medium": "глубже"},
}
RANGES = {"heartbeat_every_min": (15, 240), "active_from": (0, 23), "active_to": (1, 24), "tidy_hour": (0, 23), "watch_every_min": (1, 60),
          "weekly_day": (0, 6), "weekly_hour": (0, 23), "budget_usd_day": (0, 1000), "exam_budget_usd": (0.5, 20),
          "task_budget_usd": (0, 100)}   # 0 — no brake on one task (the day's budget still holds)
DOLLARS = ("budget_usd_day", "exam_budget_usd", "task_budget_usd")
WHOLE = {k for k in RANGES if k not in DOLLARS}   # hours, days and minutes are whole numbers (the scheduler can't digest 20.5); a dollar budget can be any number
FLAGS = ("heartbeat", "watch", "tidy", "weekly", "exam", "judge", "reflect", "voice_reply", "harvest", "telegram_notify",
         "use_telegram_account", "use_browser", "use_claude", "use_google", "use_gemini", "use_maps", "use_travel", "use_luma", "use_mcp", "use_site", "use_github", "use_hireflow", "use_youtube", "use_hearing")
_lock = threading.Lock()


def load():
    current = {**DEFAULTS, **store.load_json(config.SETTINGS_FILE, {})}
    for key, choices in OPTIONS.items():   # a value no longer offered (the old OpenAI voices) gives way to the default
        if current.get(key) not in choices:
            current[key] = DEFAULTS[key]
    return current


def get(key):
    return load()[key]


def awake(now=None):
    """Whether it is the owner's waking hours (active_from..active_to on the Mac's clock): what Sena tells on her own waits for them."""
    s = load()
    return s["active_from"] <= (now or datetime.now()).hour < s["active_to"]


def _valid(key, value):
    if key == "roles":   # where switchable jobs work: only known roles, only home or cloud
        return isinstance(value, dict) and all(k in config.ROLES and v in (config.HOME, config.CLOUD) for k, v in value.items())
    if key in OPTIONS:
        return isinstance(value, str) and value in OPTIONS[key]
    if key in RANGES:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not RANGES[key][0] <= value <= RANGES[key][1]:
            return False
        return key not in WHOLE or float(value).is_integer()
    return key in FLAGS and isinstance(value, bool)


def apply(changes):
    """Records what's valid. Returns (the settings as saved, the keys that were dropped: unknown, out of range,
    wrong type, and active hours where "from" is not less than "to")."""
    with _lock:
        current = load()
        accepted = {k: (int(v) if k in WHOLE else v) for k, v in changes.items() if _valid(k, v)}   # 20.0 from the phone screen is just 20
        rejected = [k for k in changes if k not in accepted]
        merged = {**current, **accepted}
        if merged["active_from"] >= merged["active_to"]:   # the active-hours window must not collapse
            for key in ("active_from", "active_to"):
                if accepted.pop(key, None) is not None:
                    rejected.append(key)
            merged = {**current, **accepted}
        store.save_versioned(config.SETTINGS_FILE, merged, indent=1)
        return merged, rejected


def update(changes):
    """Accepts only known keys with valid values; silently drops the rest."""
    return apply(changes)[0]


def describe():
    """How Sena is configured right now, in words (for the screen and for herself)."""
    s = load()
    return f"отвечает {OPTIONS['verbosity'][s['verbosity']]}, думает {OPTIONS['effort'][s['effort']]}, голос {OPTIONS['voice'][s['voice']]}"


def set_style(verbosity=None, effort=None, voice=None):
    """Sena's tool: record Tema's request about reply length, reasoning depth, or voice."""
    wanted = {k: v for k, v in {"verbosity": verbosity, "effort": effort, "voice": voice}.items() if v}
    if not wanted:
        return "Нечего менять: укажи verbosity, effort или voice"
    bad = [f"{k}={v}" for k, v in wanted.items() if v not in OPTIONS[k]]
    if bad:
        return "Не из списка: " + ", ".join(bad) + ". " + "; ".join(f"{k}: {', '.join(o)}" for k, o in OPTIONS.items())
    update(wanted)
    return "Запомнила, теперь " + describe()


def set_telegram_notify(on):
    """Sena's tool: Tema asked her to stop (or let her resume) writing to the Telegram bot on her own — the night check,
    reminders, background notices. Direct replies to what he writes there aren't affected, only the unprompted ones."""
    update({"telegram_notify": bool(on)})
    return "Больше не пишу в Телеграм-бот сама — только в приложении." if not on else "Пишу в Телеграм-бот сама, как раньше."


TOOLS = [tool("set_style", "Тема попросил впредь отвечать короче или подробнее, думать быстрее или глубже, или сменить голос — запиши это себе: "
                           "настройка действует на все следующие ответы, пока он не попросит иначе. Разовое «расскажи подробнее» — не сюда: "
                           "просто ответь развёрнуто.",
              {"verbosity": {**string("длина ответов"), "enum": list(OPTIONS["verbosity"])},
               "effort": {**string("глубина мышления"), "enum": list(OPTIONS["effort"])},
               "voice": {**string("голос озвучки"), "enum": list(OPTIONS["voice"])}}),
         tool("set_telegram_notify", "Тема попросил не присылать ему в Telegram-бот ничего по своей инициативе (ночную проверку, "
                                      "напоминания, фоновые уведомления) — или наоборот разрешил снова. Не про ответ на его сообщение в боте.",
              {"on": {"type": "boolean", "description": "писать в Telegram-бот самостоятельно"}}, required=["on"])]
FUNCTIONS = {"set_style": set_style, "set_telegram_notify": set_telegram_notify}
