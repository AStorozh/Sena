"""Money accounting: every paid call is recorded in the cost journal, and reports are computed from it.

How it's built so nothing gets lost:
- a paid call is recorded right where it is made, and only there: `core/llm.py` (Claude and OpenAI), `google/media.py` (Gemini),
  `world/maps_*` (maps), `world/hireflow.py`; Claude Code's subscription runs (`agents/ag_code.py`) and the home models
  (`core/local.py`) are recorded too, at no cost — so it can't be bypassed (checked by tests/test_structure.py);
- the journal records not dollars but what we're paying for: tokens, seconds, requests, and the price at the time
  of recording. If a rate changes or turns out to be inaccurate, `recost()` recalculates all entries using the new price table;
- a call with no known price is not counted as zero: it's recorded as "unpriced" and shown in the report;
- an accounting failure doesn't bring down the work: the error is printed to the log, and the task continues.

Journal: data/journal/costs/YYYY-MM.jsonl, one file per month. Who spent it — the agent (`who`), the task (`tid`, the same
id as in the task journal) and the reason (`purpose`) are recorded with every call. The Claude Code subscription doesn't
charge money, but its calls are still visible in the report."""
import json
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from sena import config
from sena.core import status, store
from sena.core.tools import string, tool

# ---------- rates ----------
PRICES_AS_OF = "2026-10-15"
# OpenAI, Standard, dollars per 1M tokens: (input, cached input, output, (input, cache, output) when input exceeds long_over tokens).
# Source: developers.openai.com/api/docs/pricing. The model name in the response comes with a date snapshot ("gpt-5.4-mini-2026-03-17"):
# the longest matching entry is used.
OPENAI_TEXT = {
    "gpt-6-astra": (10.0, 1.0, 50.0, (20.0, 2.0, 75.0)),
    "gpt-6.1-sol": (2.0, 0.1, 10.0, (4.0, 0.2, 15.0)),
    "gpt-6-sol": (2.0, 0.2, 10.0, (4.0, 0.4, 15.0)),
    "gpt-6-luna": (0.1, 0.01, 0.5, (0.2, 0.02, 0.75)),
    "gpt-5.6-sol": (4.0, 0.4, 20.0, (8.0, 0.8, 30.0)),
    "gpt-5.6-terra": (2.0, 0.2, 12.0, (4.0, 0.4, 18.0)),
    "gpt-5.6-luna": (0.2, 0.02, 1.2, (0.4, 0.04, 1.8)),
    "gpt-5.5": (5.0, 0.5, 30.0, (10.0, 1.0, 45.0)),
    "gpt-5.4": (2.5, 0.25, 15.0, (5.0, 0.5, 22.5)),
    "gpt-5.4-mini": (0.75, 0.075, 4.5, None),
    "gpt-5.4-nano": (0.2, 0.02, 1.25, None),
    "gpt-5.2": (1.75, 0.175, 14.0, None),
    "gpt-5.1": (1.25, 0.125, 10.0, None),
    "gpt-5": (1.25, 0.125, 10.0, None),
    "gpt-5-mini": (0.25, 0.025, 2.0, None),
    "gpt-5-nano": (0.05, 0.005, 0.4, None),
    "gpt-4.1": (2.0, 0.5, 8.0, None),
    "gpt-4.1-mini": (0.4, 0.1, 1.6, None),
    "gpt-4o": (2.5, 1.25, 10.0, None),
    "gpt-4o-mini": (0.15, 0.075, 0.6, None),
}
# cache writes are billed for newer models (gpt-5.4 and earlier don't have them): dollars per 1M tokens, twice as expensive for long input
OPENAI_CACHE_WRITE = {"gpt-6-astra": 12.5, "gpt-6.1-sol": 2.5, "gpt-6-sol": 2.5, "gpt-6-luna": 0.125,
                      "gpt-5.6-sol": 5.0, "gpt-5.6-terra": 2.5, "gpt-5.6-luna": 0.25}
LONG_CONTEXT_OVER = 272_000        # longer input — the whole request is billed at the "long" rates
TIER_FACTOR = {"default": 1.0, "auto": 1.0, "flex": 0.5, "batch": 0.5, "priority": 2.0}   # priority = Fast mode, twice the price
WEB_SEARCH_PER_CALL = 10.0 / 1000  # built-in search: 10 dollars per 1000 calls; the found text is already counted in input tokens
EMBEDDINGS = {"text-embedding-3-small": 0.02, "text-embedding-3-large": 0.13, "text-embedding-ada-002": 0.10}   # per 1M tokens
# Claude API, dollars per 1M tokens: (input, cache read, output). A 5-minute cache write is 1.25x the input price. Search — same as OpenAI, 10 dollars per 1000.
# Source: Anthropic's model reference, 2026-10-06. The model name in the response is matched by the longest match.
CLAUDE_TEXT = {"claude-opus-5-5": (4.0, 0.20, 20.0), "claude-sonnet-5-5": (2.0, 0.20, 10.0), "claude-haiku-5-5": (0.10, 0.01, 0.50),
               "claude-fable-5-1": (10.0, 0.25, 50.0),
               # a request can fall back to these on failure (fallbacks): cache read here is taken as 0.1x input, the reference doesn't list a price
               "claude-opus-4-8": (5.0, 0.5, 25.0), "claude-opus-5": (5.0, 0.5, 25.0), "claude-sonnet-5": (2.0, 0.2, 10.0)}
CLAUDE_CACHE_WRITE = 1.25
CLAUDE_CACHE_WRITE_HOUR = 2.0   # an hour-long cache write (cache_write_1h — a part of cache_write): 2x the input price
# a request whose prompt (fresh input, cache read and write together) passes the threshold is billed whole at the higher card
# (the Claude docs: Haiku 5.5 is $0.10 / $0.50 up to 100K prompt tokens and $0.50 / $2.50 above; LiteLLM prices the same way)
CLAUDE_LONG = {"claude-haiku-5-5": (100_000, (0.50, 0.05, 2.50))}
# OpenAI's voice and hearing — Sena no longer calls them (the voice is Gemini's, hearing is at home), but the journal keeps their
# old rows and recost() prices those again. Speech recognition: (input audio, input text, output) per 1M tokens; audio is derived
# from the official estimate "about 0.006 dollars per minute" (verified: 33 audio tokens per 3.4 seconds).
TRANSCRIBE = {"gpt-4o-transcribe": (6.0, 2.5, 10.0), "gpt-4o-mini-transcribe": (3.0, 1.25, 5.0)}
TRANSCRIBE_PER_MINUTE = 0.006      # if the API returned a duration instead of tokens
SPEECH_PER_MINUTE = 0.015          # gpt-4o-mini-tts voice: the API doesn't return audio tokens, so it's billed by duration
SPEECH_TEXT_PER_M = 0.60
# Gemini, dollars per 1M tokens: (input, text and thinking output, image output, video output). Source: ai.google.dev/gemini-api/docs/pricing
GEMINI = {
    "gemini-nano-banana-2.1": (1.5, 7.5, 30.0, None),
    "gemini-3.1-flash-lite-image": (0.25, 1.5, 30.0, None),
    "gemini-3-pro-image": (2.0, 12.0, 120.0, None),
    "gemini-omni-1.1-flash": (1.5, 9.0, None, 17.5),
}
GEMINI_VIDEO_TOKENS_PER_SECOND = 5792   # 720p; Google doesn't publish 1080p, there we take tokens from the response
# Gemini voice and hearing, dollars per 1M tokens: (from date, input, output). Speech is 25 audio tokens a second; the 3.8 TTS rates double on 01.01.2027.
GEMINI_AUDIO = {"gemini-3.8-flash-tts": [("2026-09-23", 0.50, 9.0), ("2027-01-01", 1.00, 18.0)],
                "gemini-3.8-flash-lite-tts": [("2026-09-23", 0.50, 6.0), ("2027-01-01", 1.00, 12.0)],
                "gemini-3.5-transcribe": [("2026-01-01", 2.00, 12.0)]}
# Gemini Live (the live voice), dollars per 1M tokens: (input text, input sound, output text and thinking, output sound).
# Each reply re-reads the whole conversation so far, her own voice included: the input grows turn by turn, so a conversation is kept short.
GEMINI_LIVE = {"gemini-3.8-live": (0.75, 3.00, 4.50, 12.00)}
# TomTom: free requests per month; the price above the limit here is an estimate, based on the "pay as you grow" plan
TOMTOM_FREE_MONTH = {"search": 2500, "routing": 20000}
TOMTOM_OVER_PER_1000 = 0.50

_lock = threading.Lock()
# month file -> its entries as read so far. Only this month and the last one stay in memory: every month ever read stayed parsed
# for the life of the process (hundreds of MB once months fill up with the home models' rows); an older one is read when asked for
_months = {}
_read = {}     # month file -> (its inode, how many of its bytes are read): what another process appends is read at the next look
_tasks = {}    # month file -> {task id: dollars}, kept for every month read: the cost of tasks without their rows
errors = []    # what failed to record (for checks and tests)


def _dir():
    return config.JOURNAL_DIR / "costs"


# ---------- recording ----------
def _file(moment):
    return _dir() / f"{datetime.fromtimestamp(moment):%Y-%m}.jsonl"


def _load(path):
    """A month's entries: read once, and from then on only what was appended to the file since — by this process or another
    (the weekly exam, a script record into the same journal), so the day's budget counts every process's spending, not only
    what this one spent since it started. A file written anew (recost) is read again from its start, and so is an older month
    whose rows left memory (_forget_old). The month's cost by task is summed as its lines are read. Called under _lock."""
    try:
        st = path.stat()
    except FileNotFoundError:
        _months[path], _read[path], _tasks[path] = [], (None, 0, 0), {}
        return _months[path]
    inode, done, _ = _read.get(path, (None, 0, 0))
    if path not in _months or inode != st.st_ino or st.st_size < done:
        _months[path], _tasks[path], done = [], {}, 0
    if st.st_size > done:
        with open(path, "rb") as f:
            f.seek(done)
            tail = f.read(st.st_size - done)
        whole = tail[:tail.rfind(b"\n") + 1]   # a line still being written is read whole at the next look
        tasks = _tasks.setdefault(path, {})
        for line in whole.decode("utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue   # a truncated line from an abrupt shutdown shouldn't break the reports
            _months[path].append(row)
            if row.get("tid") and row.get("usd") is not None:
                tasks[row["tid"]] = tasks.get(row["tid"], 0.0) + row["usd"]
        done += len(whole)
    _read[path] = (st.st_ino, done, st.st_size)
    return _months[path]


def _recent(path):
    # this month's and last month's files stay parsed; an older one is read again when a report asks for it
    first = (date.today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    return path.stem >= first


def _forget_old():
    # the rows of older months leave memory (their task totals and read marks stay: by_task needs no rows). Called under _lock
    for path in [p for p in _months if not _recent(p)]:
        del _months[path]


def _task_totals(path):
    """{task id: dollars} of one month file: from memory while the file is as it was last read, otherwise read anew. Under _lock."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return {}
    inode, _, size = _read.get(path, (None, 0, 0))
    if path not in _tasks or inode != st.st_ino or st.st_size != size:
        _load(path)
    return _tasks.get(path, {})


def _append(entry):
    with _lock:   # read back at the next look, together with whatever another process appended meanwhile
        store.append_jsonl(_file(entry["t"]), entry)


@contextmanager
def label(text, tid=None):
    """For the duration of the block, calls are recorded under this reason (and task): post-task review, nightly cleanup, background check."""
    before = (getattr(status.local, "label", None), getattr(status.local, "tid", None))
    status.local.label, status.local.tid = text, tid or before[1]
    try:
        yield
    finally:
        status.local.label, status.local.tid = before


def _context():
    task = status.current() or {}
    return {"who": getattr(status.local, "who", None), "tid": getattr(status.local, "tid", None) or task.get("id"),
            "task": (getattr(status.local, "label", None) or task.get("text") or "")[:100]}


_by_task = {}   # task id -> dollars spent in this process: the brain's per-task brake reads it every step without the journal


def task_spent(tid):
    """Dollars a task has spent so far (this process; a task lives in one)."""
    with _lock:
        return _by_task.get(tid, 0.0)


def record(provider, service, model="", units=None, tier="default", purpose=None, note=None, moment=None, unknown=False):
    """Record a paid call. Returns the cost in dollars (None — no price). Never raises.
    unknown — the call happened, but there's nothing to tell its cost from: it's recorded as "unpriced" rather than zero."""
    try:
        units = {k: v for k, v in (units or {}).items() if v}
        moment = moment or time.time()
        usd, estimate = (None, False) if unknown else price(provider, service, model, units, tier, moment)
        entry = {"t": moment, "provider": provider, "service": service, "model": model, "units": units,
                 "tier": tier, "usd": usd, "estimate": estimate, **_context()}
        if unknown:
            entry["unknown"] = True
        if purpose:
            entry["purpose"] = purpose
        if note:
            entry["note"] = note
        _append(entry)
        spent = usd if usd is not None else _dearest(provider, service, units)
        if spent and entry.get("tid"):
            with _lock:
                _by_task[entry["tid"]] = _by_task.get(entry["tid"], 0.0) + spent
        return usd
    except Exception as e:   # accounting must not bring down the work
        errors.append(repr(e))
        print("Учёт расходов: не записано:", e)
        return None


def _dearest(provider, service, units):
    """For the per-task brake only: a model call with no known price at the dearest known rate of its kind — a call that can't be
    priced must not be free for the limit (SWE-agent refuses to run without a price). The journal still says "unpriced"."""
    table = {("anthropic", "messages"): CLAUDE_TEXT, ("openai", "responses"): OPENAI_TEXT}.get((provider, service))
    if not table:
        return 0.0
    rates = list(table.values())
    return (units.get("input", 0) * max(r[0] for r in rates) + units.get("output", 0) * max(r[2] for r in rates)) / 1e6


# ---------- price by units ----------
def _match(table, model):
    keys = [k for k in table if model.startswith(k)]
    return table[max(keys, key=len)] if keys else None


def _gemini_audio(model, u, tier, moment):
    # Sena's voice and the backup hearing: the rate of the day of the call
    periods = _match(GEMINI_AUDIO, model)
    if not periods:
        return None, False
    day = datetime.fromtimestamp(moment or time.time()).strftime("%Y-%m-%d")
    _, inp, out = ([p for p in periods if p[0] <= day] or periods[:1])[-1]
    return (u.get("input", 0) * inp + u.get("output", 0) * out) / 1e6, False


def _gemini_live(model, u, tier, moment):
    p = _match(GEMINI_LIVE, model)
    if not p:
        return None, False
    text_in, audio_in, text_out, audio_out = p
    return (u.get("input", 0) * text_in + u.get("input_audio", 0) * audio_in + (u.get("text", 0) + u.get("thought", 0)) * text_out
            + u.get("audio", 0) * audio_out) / 1e6, False


def _openai_text(model, u, tier, moment):
    p = _match(OPENAI_TEXT, model)
    if not p:
        return None, False
    long = bool(p[3]) and u.get("input", 0) > LONG_CONTEXT_OVER
    inp, cached, out = p[3] if long else p[:3]
    write_price = _match(OPENAI_CACHE_WRITE, model)
    write = u.get("cache_write", 0)
    write_rate = (write_price * (inp / p[0]) if write_price else inp)   # no separate write price: treated as regular input
    fresh = max(0, u.get("input", 0) - u.get("cached", 0) - write)
    tokens = (fresh * inp + u.get("cached", 0) * (cached if cached is not None else inp) + write * write_rate
              + u.get("output", 0) * out) / 1e6
    return tokens * TIER_FACTOR.get(tier, 1.0) + u.get("web_search", 0) * WEB_SEARCH_PER_CALL, False


def _claude_text(model, u, tier, moment):
    p = _match(CLAUDE_TEXT, model)
    if not p:
        return None, False
    long = _match(CLAUDE_LONG, model)
    if long and u.get("input", 0) + u.get("cache_read", 0) + u.get("cache_write", 0) > long[0]:
        p = long[1]
    inp, read, out = p
    usd = (u.get("input", 0) * inp + u.get("cache_read", 0) * read + u.get("cache_write", 0) * inp * CLAUDE_CACHE_WRITE
           + u.get("cache_write_1h", 0) * inp * (CLAUDE_CACHE_WRITE_HOUR - CLAUDE_CACHE_WRITE) + u.get("output", 0) * out) / 1e6
    return usd + u.get("web_search", 0) * WEB_SEARCH_PER_CALL, False


def _embeddings(model, u, tier, moment):
    p = _match(EMBEDDINGS, model)
    return (u.get("tokens", 0) * p / 1e6, False) if p is not None else (None, False)


def _openai_transcribe(model, u, tier, moment):   # old rows: OpenAI no longer hears for Sena
    if u.get("seconds"):
        return u["seconds"] / 60 * TRANSCRIBE_PER_MINUTE, True
    p = _match(TRANSCRIBE, model)
    if not p:
        return None, False
    return (u.get("audio", 0) * p[0] + u.get("text", 0) * p[1] + u.get("output", 0) * p[2]) / 1e6, True


def _openai_speech(model, u, tier, moment):   # old rows: OpenAI no longer speaks for Sena
    return u.get("seconds", 0) / 60 * SPEECH_PER_MINUTE + u.get("chars", 0) / 4 * SPEECH_TEXT_PER_M / 1e6, True


def _gemini_media(model, u, tier, moment):
    # images and video: tokens by kind; video by its seconds when the reply had no video tokens (an estimate)
    p = _match(GEMINI, model)
    if not p:
        return None, False
    inp, text, image, video = p
    if (u.get("image") and image is None) or (u.get("video") and video is None):
        return None, False
    video_tokens = u.get("video") or int(u.get("video_seconds", 0) * GEMINI_VIDEO_TOKENS_PER_SECOND)
    usd = ((u.get("input", 0) + u.get("tool", 0)) * inp + (u.get("text", 0) + u.get("thought", 0)) * text
           + u.get("image", 0) * (image or 0) + video_tokens * (video or 0)) / 1e6
    return usd, bool(u.get("video_seconds") and not u.get("video")) or bool(u.get("video") and video is not None)


def _hireflow(model, u, tier, moment):
    return u.get("usd", 0.0), True   # the AI inside Hire-flow names its own cost in the response; this is its own estimate


# tomtom: a free monthly limit (usage above it is counted in the report); claude-code: the subscription, no money charged;
# local: a model on this Mac (core/local.py) — no money, and nothing left the house
FREE = ("tomtom", "claude-code", "local")
# (provider, service) -> how its units are priced; (provider, None) — every other service of that provider
PRICERS = {("gemini", "speech"): _gemini_audio, ("gemini", "transcribe"): _gemini_audio, ("gemini", "live"): _gemini_live,
           ("openai", "responses"): _openai_text, ("anthropic", "messages"): _claude_text, ("openai", "embeddings"): _embeddings,
           ("openai", "transcribe"): _openai_transcribe, ("openai", "speech"): _openai_speech,
           ("gemini", None): _gemini_media, ("hireflow", None): _hireflow}


def price(provider, service, model, units, tier="default", moment=None):
    """(dollars or None, whether it's an estimate). Based on units and the moment of the call (a rate can change on a date),
    so it also works for recalculating old entries."""
    if provider in FREE:
        return 0.0, False
    pricer = PRICERS.get((provider, service)) or PRICERS.get((provider, None))
    return pricer(model, units, tier, moment) if pricer else (None, False)


# ---------- what the callers record ----------
def _num(obj, path, default=0):
    for part in path.split("."):
        obj = obj.get(part) if isinstance(obj, dict) else getattr(obj, part, None)
        if obj is None:
            return default
    return obj


def openai_response(model, response, purpose=None):
    """Responses API reply: tokens (with cache), built-in search calls, tier."""
    usage = getattr(response, "usage", None)
    web = sum(1 for o in getattr(response, "output", None) or [] if getattr(o, "type", "") == "web_search_call")
    units = {"input": _num(usage, "input_tokens"), "cached": _num(usage, "input_tokens_details.cached_tokens"),
             "cache_write": _num(usage, "input_tokens_details.cache_write_tokens"), "output": _num(usage, "output_tokens"), "web_search": web}
    return record("openai", "responses", getattr(response, "model", None) or model, units,
                  tier=getattr(response, "service_tier", None) or "default", purpose=purpose,
                  note=None if usage is not None else "ответ без данных об использовании", unknown=usage is None)


def claude_response(response, purpose=None):
    """Claude reply (translated by llm_claude): tokens without cache, cache read and write, output, built-in searches — for every
    request it took (a turn paused by the built-in search is several requests, each billed)."""
    calls = getattr(response, "calls", None) or [(getattr(response, "model", None) or "", getattr(response, "raw_usage", None))]
    total = None
    for model, usage in calls:
        units = {"input": _num(usage, "input_tokens"), "cache_read": _num(usage, "cache_read_input_tokens"),
                 "cache_write": _num(usage, "cache_creation_input_tokens"), "cache_write_1h": _num(usage, "cache_creation.ephemeral_1h_input_tokens"),
                 "output": _num(usage, "output_tokens"),
                 "web_search": _num(usage, "server_tool_use.web_search_requests")}
        usd = record("anthropic", "messages", model or "", units, purpose=purpose,
                     note=None if usage is not None else "ответ без данных об использовании", unknown=usage is None)
        total = usd if total is None else total + (usd or 0)
    return total


def gemini_audio(model, response, service, purpose=None):
    """Gemini voice ("speech") or hearing ("transcribe"): input and output tokens from the reply."""
    usage = response.get("usage") or {}
    return record("gemini", service, model, {"input": usage.get("total_input_tokens", 0), "output": usage.get("total_output_tokens", 0)},
                  tier=response.get("service_tier") or "standard", purpose=purpose,
                  note=None if usage else "ответ без данных об использовании", unknown=not usage)


def gemini_live(model, usage, purpose=None):
    """One reply of the live voice (its usageMetadata): what it re-read (text, sound) and what it gave (sound, text, thinking)."""
    def by_kind(key):
        return {d.get("modality"): d.get("tokenCount", 0) for d in usage.get(key) or []}
    heard, said = by_kind("promptTokensDetails").get("AUDIO", 0), by_kind("responseTokensDetails").get("AUDIO", 0)
    units = {"input": usage.get("promptTokenCount", 0) - heard, "input_audio": heard, "audio": said,
             "text": usage.get("responseTokenCount", 0) - said, "thought": usage.get("thoughtsTokenCount", 0)}
    return record("gemini", "live", model, units, tier="standard", purpose=purpose)


def openai_embeddings(model, response, purpose=None):
    usage = getattr(response, "usage", None)
    return record("openai", "embeddings", getattr(response, "model", None) or model,
                  {"tokens": _num(usage, "total_tokens") or _num(usage, "prompt_tokens")}, purpose=purpose)


def gemini(model, response, kind, purpose=None, video_seconds=None):
    """Gemini reply (interactions): tokens by kind, images and video priced separately."""
    usage = response.get("usage") or {}
    by_mode = {m.get("modality"): m.get("tokens", 0) for m in usage.get("output_tokens_by_modality") or []}
    total_out = usage.get("total_output_tokens", 0)
    units = {"input": usage.get("total_input_tokens", 0), "tool": usage.get("total_tool_use_tokens", 0),
             "thought": usage.get("total_thought_tokens", 0)}
    if kind == "video":
        # verified in practice: of 17,798 output tokens, 17,376 are tagged as video, the remaining 422 are untagged. How Google
        # counts those is unknown, so everything that isn't text is billed at the video rate: a bit more, but not less than the truth (an estimate)
        units["text"] = by_mode.get("text", 0)
        units["video"] = max(0, total_out - units["text"])
    else:
        units["image"] = by_mode.get("image", 0)
        units["text"] = max(0, total_out - units["image"])
    if kind == "video" and not units["video"] and video_seconds:
        units["video_seconds"] = video_seconds   # a reply with no video tokens: bill by seconds instead
    return record("gemini", "video" if kind == "video" else "image", model, units,
                  tier=response.get("service_tier") or "standard", purpose=purpose,
                  note=None if usage else "ответ без данных об использовании", unknown=not usage and not video_seconds)


def request(provider, service, count=1, purpose=None):
    """A request to a service with a free limit or a per-call charge (maps)."""
    return record(provider, service, units={"requests": count}, purpose=purpose)


def hireflow(usd, purpose=None):
    """AI spend inside Hire-flow (letters, job evaluation): Hire-flow names the amount itself in its reply, Sena records it in the journal."""
    return record("hireflow", "agent-api", units={"usd": round(float(usd), 6)}, purpose=purpose)


def subscription(provider, seconds, purpose=None, turns=None):
    # a run on a subscription costs no money: it is counted (calls, seconds, the model's turns) to see how the quota goes
    return record(provider, "subscription", units={"calls": 1, "seconds": round(seconds, 1), **({"turns": int(turns)} if turns else {})}, purpose=purpose)


# ---------- reports ----------
def _all_rows(since=None, until=None):
    with _lock:
        paths = sorted(_dir().glob("*.jsonl")) if _dir().exists() else []
        if since is not None:   # don't read months earlier than the start of the period
            first = datetime.fromtimestamp(since).strftime("%Y-%m")
            paths = [p for p in paths if p.stem >= first]
        rows = [r for p in paths for r in _load(p)]
        _forget_old()
    return [r for r in rows if (since is None or r["t"] >= since) and (until is None or r["t"] < until)]


def span(period):
    """(since, until, label) for the period: today, yesterday, week, month, last_month, all, YYYY-MM, YYYY-MM-DD
    or the range YYYY-MM-DD..YYYY-MM-DD (both dates inclusive — that's how the Mac app asks, where the owner picks the dates)."""
    today = date.today()
    start = lambda d: datetime(d.year, d.month, d.day).timestamp()   # noqa: E731
    if period == "today":
        return start(today), None, f"сегодня, {today:%d.%m.%Y}"
    if period == "yesterday":
        d = today - timedelta(days=1)
        return start(d), start(today), f"вчера, {d:%d.%m.%Y}"
    if period == "week":
        d = today - timedelta(days=6)
        return start(d), None, f"7 дней, с {d:%d.%m}"
    if period == "month":
        return start(today.replace(day=1)), None, f"{today:%m.%Y}, с начала месяца"
    if period == "last_month":
        end = today.replace(day=1)
        first = (end - timedelta(days=1)).replace(day=1)
        return start(first), start(end), f"{first:%m.%Y}"
    if period == "all":
        return None, None, "всё время"
    try:
        if ".." in period:
            a, b = (date.fromisoformat(x) for x in period.split("..", 1))
            if b < a:
                a, b = b, a
            return start(a), start(b + timedelta(days=1)), f"{a:%d.%m.%Y}" if a == b else f"{a:%d.%m.%Y} — {b:%d.%m.%Y}"
        if len(period) == 7:
            first = date.fromisoformat(period + "-01")
            nxt = (first + timedelta(days=32)).replace(day=1)
            return start(first), start(nxt), f"{first:%m.%Y}"
        d = date.fromisoformat(period)
        return start(d), start(d + timedelta(days=1)), f"{d:%d.%m.%Y}"
    except ValueError:
        raise ValueError("period — today, yesterday, week, month, last_month, all, ГГГГ-ММ, ГГГГ-ММ-ДД или ГГГГ-ММ-ДД..ГГГГ-ММ-ДД")


def _source(row):
    return row.get("who") or row.get("purpose") or "система"


def _model(row):
    # in responses the model name comes with a date snapshot ("gpt-5.4-2026-03-05"): in the report it's a single model
    name = row.get("model") or row["service"]
    keys = [k for table in (OPENAI_TEXT, EMBEDDINGS, TRANSCRIBE, GEMINI) for k in table if name.startswith(k)]
    return max(keys, key=len) if keys else name


def summary(period="today"):
    """Report data for the period: total, by service, model, performer, task, day, unpriced, limits, subscription."""
    since, until, title = span(period)
    every = _all_rows(since, until)
    home = [r for r in every if r["provider"] == "local"]   # the home models: free, counted apart so the report shows what stayed in the house
    rows = [r for r in every if r["provider"] != "local"]
    priced = [r for r in rows if r["usd"] is not None and r["provider"] != "claude-code"]

    def group(key, items=priced, n=None):
        acc = {}
        for r in items:
            g = acc.setdefault(key(r), {"usd": 0.0, "calls": 0})
            g["usd"] += r["usd"]
            g["calls"] += 1
        ordered = sorted(acc.items(), key=lambda kv: -kv[1]["usd"])
        return ordered[:n] if n else ordered

    tasks = {}
    for r in priced:
        if r.get("tid"):
            t = tasks.setdefault(r["tid"], {"usd": 0.0, "task": r.get("task", ""), "calls": 0})
            t["usd"] += r["usd"]
            t["calls"] += 1
    unpriced = {}
    for r in rows:
        if r["usd"] is None:
            k = f"{r['provider']}/{_model(r)}"
            unpriced[k] = unpriced.get(k, 0) + 1
    month_rows = _all_rows(*span("month")[:2])
    free = {}
    for r in month_rows:
        if r["provider"] == "tomtom":
            free[r["service"]] = free.get(r["service"], 0) + r["units"].get("requests", 0)
    claude = [r for r in rows if r["provider"] == "claude-code"]
    by_service = {}
    for r in home:
        by_service[r["service"]] = by_service.get(r["service"], 0) + 1
    return {
        "period": period, "title": title, "total": sum(r["usd"] for r in priced), "calls": len(rows),
        "home": {"calls": len(home), "by_service": by_service, "seconds": round(sum(r["units"].get("seconds", 0) for r in home), 1)},
        "estimated": any(r.get("estimate") for r in priced),
        "by_provider": group(lambda r: r["provider"]), "by_model": group(_model),
        "by_source": group(_source), "by_purpose": group(lambda r: r.get("purpose") or "задачи Темы"), "by_day": sorted(group(lambda r: datetime.fromtimestamp(r["t"]).strftime("%Y-%m-%d")), key=lambda kv: kv[0]),
        "top_tasks": sorted(tasks.items(), key=lambda kv: -kv[1]["usd"])[:5], "unpriced": unpriced,
        "tomtom_month": {s: {"used": n, "free": TOMTOM_FREE_MONTH.get(s)} for s, n in free.items()},
        "claude": {"calls": len(claude), "seconds": sum(r["units"].get("seconds", 0) for r in claude)},
    }


def money(x):
    if x == 0:
        return "$0"
    return f"${x:.6f}" if x < 0.0001 else f"${x:.4f}" if x < 0.01 else f"${x:.2f}"


def render(data, detail=True):
    out = [f"Расходы, {data['title']}: {money(data['total'])}" + (" (часть — оценка по длительности звука)" if data["estimated"] else "")
           + f", вызовов {data['calls']}."]
    if data["by_provider"]:
        out.append("По сервисам: " + ", ".join(f"{k} {money(v['usd'])} ({v['calls']})" for k, v in data["by_provider"]) + ".")
    if detail and data["by_model"]:
        out.append("По моделям: " + ", ".join(f"{k} {money(v['usd'])}" for k, v in data["by_model"][:6]) + ".")
    if detail and data["by_source"]:
        out.append("Кто тратил: " + ", ".join(f"{k} {money(v['usd'])}" for k, v in data["by_source"][:6]) + ".")
    if detail and data["by_purpose"]:
        out.append("На что: " + ", ".join(f"{k} {money(v['usd'])}" for k, v in data["by_purpose"][:6]) + ".")
    if detail and len(data["by_day"]) > 1:
        out.append("По дням: " + ", ".join(f"{d[5:]} {money(v['usd'])}" for d, v in data["by_day"][-10:]) + ".")
    if data["top_tasks"]:
        out.append("Дороже всего: " + "; ".join(f"«{t['task'][:50]}» {money(t['usd'])}" for _, t in data["top_tasks"]) + ".")
    if data["unpriced"]:
        out.append("Без цены (не включены в итог, добавь тариф в costs.py): " + ", ".join(f"{k} — {n}" for k, n in data["unpriced"].items()) + ".")
    for service, q in data["tomtom_month"].items():
        free = q["free"]
        over = f", сверх лимита {q['used'] - free} (оценка {money((q['used'] - free) / 1000 * TOMTOM_OVER_PER_1000)})" if free and q["used"] > free else ""
        out.append(f"TomTom {service}: {q['used']} запросов в этом месяце из {free or '?'} бесплатных{over}.")
    if data["claude"]["calls"]:
        out.append(f"Claude Code по подписке: {data['claude']['calls']} вызовов, {data['claude']['seconds'] / 60:.0f} мин (деньги не списываются).")
    if data.get("home", {}).get("calls"):
        names = {"embeddings": "отпечатки", "chat": "суждения", "transcribe": "слух", "read": "чтение", "speech": "голос"}
        parts = ", ".join(f"{names.get(k, k)} {n}" for k, n in sorted(data["home"]["by_service"].items(), key=lambda kv: -kv[1]))
        out.append(f"Дома, на этом Mac: {data['home']['calls']} вызовов ({parts}) — бесплатно, наружу ничего не ушло.")
    if errors:
        out.append(f"Внимание: не удалось записать вызовов в журнал: {len(errors)} (последняя ошибка: {errors[-1][:80]}). Итог занижен.")
    out.append(f"Тарифы на {PRICES_AS_OF}; бесплатные источники (погода, курсы, новости, почта) не считаются.")
    return "\n".join(out)


def report(period="today", detail=True):
    return render(summary(period), detail)


def by_task(task_ids, since=None):
    """Cost of tasks by their id (for the "Processes" screen): {id: dollars}. since — no task started before this time, so its
    costs lie in that month or later. Taken from each month's totals by task, not its rows: with no task running the apps'
    overview walked every cost row ever written, every two seconds (the audit of 26.10)."""
    wanted = {t for t in task_ids if t}
    if not wanted:
        return {}
    totals = {}
    with _lock:
        paths = sorted(_dir().glob("*.jsonl")) if _dir().exists() else []
        if since is not None:
            first = datetime.fromtimestamp(since).strftime("%Y-%m")
            paths = [p for p in paths if p.stem >= first]
        for path in paths:
            month = _task_totals(path)
            for tid in wanted & month.keys():
                totals[tid] = totals.get(tid, 0.0) + month[tid]
        _forget_old()
    return totals


def spent_today():
    """How much was spent today (dollars), excluding the Claude subscription: for the daily budget and the console's side panel.
    A call no rate prices counts at the dearest rate of its kind, as for the per-task brake: a model missing from the table
    (a new one, a server-side fallback) must not be free for the day's budget."""
    since, until, _ = span("today")
    return sum(r["usd"] if r.get("usd") is not None else _dearest(r["provider"], r["service"], r.get("units") or {})
               for r in _all_rows(since, until) if r["provider"] not in ("claude-code", "local"))


def recent(period="today", limit=200):
    """Most recent cost journal entries for the period, newest first (console): time, service, model, units, price, purpose, task."""
    since, until, _ = span(period)
    rows = _all_rows(since, until)
    return list(reversed(rows[-int(limit):]))


def recost():
    """Recalculate all entries using the current price table (a rate changed or was inaccurate). Returns how many entries changed."""
    changed = 0
    with _lock:
        for path in sorted(_dir().glob("*.jsonl")) if _dir().exists() else []:
            rows = _load(path)
            touched = False
            for r in rows:
                if r.get("unknown"):
                    continue   # the cost is unknown from the call itself (a reply with no data): there's nothing to recalculate from
                usd, estimate = price(r["provider"], r["service"], r.get("model", ""), r.get("units", {}), r.get("tier", "default"), r.get("t"))
                if usd != r["usd"] or estimate != r.get("estimate", False):
                    r["usd"], r["estimate"], touched = usd, estimate, True
                    changed += 1
            if touched:
                rows = _load(path)   # what another process appended meanwhile goes into the new file too (priced as it was)
                store.write_text(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        _forget_old()
    return changed


def _reset():
    # for tests: forget the entries in memory (the data folder may have changed)
    with _lock:
        _months.clear()
        _read.clear()
        _tasks.clear()
        errors.clear()


TOOLS = [tool("costs_report", "Сколько потрачено денег системой Сена: по сервисам, моделям, исполнителям и самым дорогим задачам. "
                              "Цифры в долларах; в рубли или донги переведи через exchange_rate.",
              {"period": {**string("today — сегодня, yesterday — вчера, week — 7 дней, month — с начала месяца, last_month — прошлый месяц, "
                                   "all — всё время, ГГГГ-ММ, ГГГГ-ММ-ДД или диапазон ГГГГ-ММ-ДД..ГГГГ-ММ-ДД; по умолчанию today"),
                          }, "detail": {"type": "boolean", "description": "false — только итог и сервисы, true (по умолчанию) — подробно"}})]
FUNCTIONS = {"costs_report": report}


if __name__ == "__main__":
    import sys
    print(report(sys.argv[1] if len(sys.argv) > 1 else "month"))
