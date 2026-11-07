"""API for the Mac app (mac/): a full technical overview of and control over Sena. Process and core, tasks with steps and events, the log
with filters, a live log tail, expenses by entry, memory, people, skills, notes, rules, the schedule, self-code edits,
behavior settings, actions (stop, run a background task, restart). Gathers data and carries out actions,
stores nothing itself. The phone screen (data.py) shows the same thing more simply; the remote shows everything, with details.
Client errors (missing field, wrong type, foreign path) are an ApiError with code 400/403/404; a substantive rejection ("can't apply this edit")
comes back as an ordinary {"error": ...} response. The remote can also run from a separate process (screen only, no bot): then it sees the core via the pid file."""
import hashlib
import json
import os
import platform
import re
import resource
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime

from sena import config, scheduler
from sena.agents import ag_code, ag_judge, ag_night
from sena.agents.ag_sena import agent as ag_sena
from sena.agents.ag_sena import tools as sena_tools
from sena.google import youtube
from sena.core import ask_owner, attachments, cancel, costs, events, limits, local, power, roles, secrets, settings, status, store
from sena.core.tools import Failure
from sena.memory import facts, heard, history, index, people, personality, recall, rules, skills, task_log, vault, vault_sync
from sena.phone import data, panels, voice
from sena.phone.api import ApiError, field, number, valid_period
from sena.world import alerts

AGENTS = [("ag_sena", "Сена", "координатор"), ("ag_web", "Браузер", "сайты и покупки"), ("ag_mac", "Компьютер", "файлы и программы"),
          ("ag_code", "Код", "проекты и свой код"), ("ag_judge", "Разбор", "после задач"), ("ag_night", "Уборка", "ночью"), ("ag_ears", "Слух", "фоном")]
KEYS = ("TELEGRAM_TOKEN", "TG_OWNER_ID", "TG_API_ID", "TG_API_HASH", "OPENAI_API_KEY", "APP_TOKEN", "GEMINI_API_KEY", "TOMTOM_KEY",
        "TRAVELPAYOUTS_TOKEN", "GOOGLE_MAPS_KEY", "SENA_CLAUDE_BRAIN_KEY", "SENA_CLAUDE_AGENTS_KEY", "LUMA_KEY", "SENA_SITE_KEY", "SENA_GITHUB_TOKEN", "HIREFLOW_KEY")
LOG_FILE = config.LOGS_DIR / "bot.log"
FILE_DIRS = {"присланное": config.INBOX_DIR, "скачанное": config.DOWNLOADS_DIR, "сделанное": config.GENERATED_DIR}   # what the "Files" section shows
MIME = {"pdf": "application/pdf", "text": "text/plain; charset=utf-8"}
LOG_TAIL = 64 * 1024   # how many bytes of the log to show on first open
FILE_LIMIT = 25 * 1024 * 1024   # we don't hand anything larger to the app: the file is read into memory whole
# what's allowed to be "opened" from the app: only things a viewer opens rather than runs (images, macro-free documents, audio,
# video). Everything else (scripts, programs, installers, link bookmarks, html with code, files with the execute bit) only gets "show in Finder":
# files from "Downloads" can be mail or website attachments, and can't be trusted
OPENABLE = {".png", ".jpg", ".jpeg", ".gif", ".heic", ".webp", ".tif", ".tiff", ".bmp", ".pdf", ".txt", ".md", ".rtf", ".csv", ".json", ".log",
            ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pages", ".numbers", ".key", ".epub",
            ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}

_git_cache = {}   # (args) -> (time, reply): we ask git no more than once a minute, since the overview is polled every couple of seconds
_probe = data.probe
_PATH = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "/usr/bin:/bin")   # where her programs live (ffmpeg, whisper, cliclick, claude)


def _git(*args):
    now = time.time()
    hit = _git_cache.get(args)
    if hit and now - hit[0] < 60:
        return hit[1]
    # GIT_OPTIONAL_LOCKS=0: reading the state doesn't take the index lock and doesn't interfere with the owner's commit or Sena's self-edit;
    # if git isn't found or doesn't respond, we don't break the overview, just an empty string (like the other optional data via _probe)
    out = _probe(lambda: subprocess.run(["git", *args], cwd=config.ROOT, capture_output=True, text=True, timeout=5,
                                         env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).stdout.strip(), "")
    _git_cache[args] = (now, out)
    return out


def _dirty():
    # changes to tracked files: new (untracked) ones don't block applying an edit, so they're not in the banner either
    return _git("status", "--porcelain", "--untracked-files=no")


_seen_core = {}   # pid -> (time, is it the core): ps is asked about a number at most once a minute, the overview comes every 2 s


def _is_core(pid, cached=True):
    """The process with this number is Sena's core (python … -m sena), not whatever took over the number of a pid file a crashed
    run left behind — another program, this Mac app (Sena.app) or a screen without the core (-m sena.phone).
    cached=False — ask ps now: before a signal the answer must be about this very moment."""
    if pid == os.getpid():
        return True   # this very process wrote it: the core serving the apps itself
    hit = _seen_core.get(pid)
    if cached and hit and time.time() - hit[0] < 60:
        return hit[1]
    try:
        words = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=3).stdout.split()
    except Exception:
        return True   # couldn't check: a live core isn't called dead, and a stop isn't blocked
    verdict = any(w == "-m" and words[i + 1:i + 2] == ["sena"] for i, w in enumerate(words))
    _seen_core[pid] = (time.time(), verdict)
    return verdict


def core():
    """Whether the core (bot) is alive: app.main writes the pid. is_core — the remote is served by the bot itself, not a separate screen process."""
    pid = _probe(lambda: int(config.PID_FILE.read_text().strip()), 0) or 0
    alive = False
    if pid:
        try:
            os.kill(pid, 0)
            alive = _is_core(pid)
        except OSError:
            alive = False
    started = _probe(lambda: config.PID_FILE.stat().st_mtime) if alive else None   # the file may have disappeared while the core was exiting
    return {"pid": pid or None, "alive": alive, "is_core": alive and pid == os.getpid(), "this_pid": os.getpid(), "started": started}


def process():
    """This process: memory, threads, load, versions, git."""
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"rss": rss if platform.system() == "Darwin" else rss * 1024, "threads": threading.active_count(),
            "load": _probe(os.getloadavg, (0, 0, 0)), "python": platform.python_version(), "platform": platform.platform(),
            "git": {"commit": _git("rev-parse", "--short", "HEAD"), "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
                    "dirty": bool(_dirty()), "last": _git("log", "-1", "--format=%s")}}


_active = data.active_tasks   # running tasks with their cost (one shared place for the phone and the remote)


# The agents' characters by the notch (mac NotchPanel, Character.swift): the face says how the agent is, the thought bubble what it is
# doing right now, and the bubble is never empty. While a tool runs, its thought shows; between tools the agent thinks ("dots").
# Each thought stands for one kind of work, so Tema can tell at a glance what is going on.
THOUGHTS = {
    "search": "find find_files mail_search news_search wiki research_search arxiv_search book_search place_search hireflow_search "
              "stackoverflow hacker_news product_lookup word_info health_research flight_prices flight_ideas route travel_advice on_this_day "
              "country_info world_time look",
    "read": "mail_read read_long read_sources news_read news_digest site_read github_read github_overview note_read read_chat view_file "
            "sheet_read sheet_info mail_attachment read_skill list_chats hireflow_read hireflow_overview youtube_comments",
    "memory": "remember note_person name_person merge_people keep_lesson lesson refute_lesson note_to_self note_write note_list tag "
              "goal_list goal_update write_skill",
    "code": "claude_task browser_code",
    "send": "browser_task computer_task tell_task answer_agent ask_sena",
    "horn": "send_message send_telegram mail_send confirm_send",
    "question": "ask_tema",
    "judge": "set_outcome quality_report",
    "count": "costs_report crypto_price crypto_candles crypto_derivatives stock_quote exchange_rate market_summary sec_financials "
             "sec_filings defi fear_greed site_stats youtube_stats mail_census country_economy",
    "bell": "price_alert news_watch page_watch list_alerts news_watches page_watches schedule",
    "plus": "make_image make_video sheet_create goal_add site_link_create work_plan",
    "minus": "mail_trash forget forget_heard remove_rule remove_alert unschedule mail_unsubscribe drop_change code_undo news_unwatch "
             "page_unwatch sheet_clear",
    "tap": "click type_text press_key chrome_tabs",
    "play": "run_command apply_change work_start mcp_call",
    "upload": "send_file sheet_write",
    "tune": "set_role set_style set_telegram_notify add_rule mail_filter mail_labels mail_modify patch_skill work_control google_signin "
            "telegram_signin telegram_code open_tools luma luma_tools",
    "signal": "check_website service_status system_check vuln_check",
    "media": "music_search youtube_video youtube_videos youtube_channel tv_show media_key",
    "cloud": "weather surf_forecast drive_search drive_download",
    "stop": "stop_all confirm_cancel",
    "spinner": "wait",
}
THOUGHT_OF = {tool: thought for thought, tools in THOUGHTS.items() for tool in tools.split()}
OWN_THOUGHT = {"ag_code": "code", "ag_judge": "judge", "ag_night": "memory"}   # agents that work without the brain's tools: what they do anyway
CARD_MOOD = {"done": ("happy", "check"), "partial": ("sad", "exclaim"), "failed": ("sad", "cross"), "need_owner": ("surprise", "question"),
             "canceled": ("calm", "stop")}
RATED = {5: "wow", 4: "love", 2: "sad", 1: "sad"}   # Tema's rating of a task, on the faces of the agents that did it
FRESH = 60        # s a finished card or an agent's error keeps its face before the agent calms down
PRAISED = 180     # s Tema's rating shows
NEW = 4           # s an agent looks surprised at a task it has just been given
LONG_STEP = 20    # s one tool runs before the agent looks tense
ANGRY = 3         # its failed tool calls in one task that make the agent angry
DOZE = 600        # s free before the agent dozes off


def _who(step):
    return step["who"].split("/")[0]


def _moods(active, alive, waiting, voice_now=None):
    """{agent: (emotion, thought)}. Off — asleep with a stop; free — calm with a pause, asleep after ten minutes; a fresh result
    for a minute (happy with a check, sad with a cross or an exclamation); its error — a knockout; Tema's rating for a few
    minutes; at work — the running tool's thought or thinking dots, and a face for how it goes: surprised at a new task, tense
    at a long step, angry after failures, dizzy when the model is overloaded or it has two tasks at once. Sena listens and
    speaks in a voice conversation, and waits for Tema's answer with a question."""
    codes = [code for code, _, _ in AGENTS]
    if not alive:
        return {code: ("sleepy", "stop") for code in codes}
    now, last = time.time(), {}
    for e in task_log.items[-50:]:
        for s in e["steps"]:
            last[_who(s)] = max(last.get(_who(s), 0), s.get("time") or e.get("end", 0))
        for c in e.get("cards", []):
            last[c["agent"]] = max(last.get(c["agent"], 0), e.get("end", 0))
        last["ag_sena"] = max(last.get("ag_sena", 0), e.get("end", 0))
    moods = {code: ("sleepy" if now - last.get(code, 0) > DOZE else "calm", "pause") for code in codes}
    for e in task_log.items[-20:]:   # oldest first: the newest wins
        rated = e.get("rating") or {}
        if now - (rated.get("time") or 0) < PRAISED and rated.get("value") in RATED:
            face = (RATED[rated["value"]], "check" if rated["value"] >= 4 else "cross")
            for code in {_who(s) for s in e["steps"]} | {c["agent"] for c in e.get("cards", [])} | {"ag_sena"}:
                moods[code] = face
        elif now - e.get("end", 0) < FRESH and not e.get("service"):
            for c in e.get("cards", []):
                moods[c["agent"]] = CARD_MOOD.get(c.get("status"), ("calm", "check"))
            failed = (e.get("outcome") or {}).get("verdict") == "failed"
            moods["ag_sena"] = ("sad", "cross") if failed else ("happy", "check")
    for t in list(status.tasks.values()):
        for c in t.get("cards", []):
            if now - c.get("time", 0) < FRESH:
                moods[c["agent"]] = CARD_MOOD.get(c.get("status"), ("calm", "check"))
    for r in _probe(lambda: events.search("today", kind="error", since=now - FRESH, limit=20), []):
        if r.get("who"):
            moods[r["who"].split("/")[0]] = ("ko", "exclaim")
    tasks_of = {}
    for t in active:
        using, steps = t.get("using") or {}, t["steps"]
        working = {_who(s) for s in steps[-3:] if s["text"] != "Готово"} | {w.split("/")[0] for w in using}
        for code in working:
            tasks_of[code] = tasks_of.get(code, 0) + 1
            mine = [s for s in steps if _who(s) == code]
            tool = next((tl for w, tl in using.items() if w.split("/")[0] == code), None)
            thought = THOUGHT_OF.get(tool, "dots") if tool else OWN_THOUGHT.get(code, "dots")
            if thought == "spinner" or tasks_of[code] > 1:
                face = "dizzy"
            elif sum(n for w, n in (t.get("failed_by") or {}).items() if w.split("/")[0] == code) >= ANGRY:
                face = "angry"
            elif mine and now - mine[0]["time"] < NEW:
                face = "surprise"
            elif tool and mine and now - mine[-1]["time"] > LONG_STEP:
                face = "tense"
            else:
                face = "calm"
            moods[code] = (face, thought)
    if voice_now == "speaking":
        moods["ag_sena"] = ("happy", "horn")
    elif voice_now == "listening" and "ag_sena" not in tasks_of:
        moods["ag_sena"] = ("surprise", "listen")
    if waiting:
        moods["ag_sena"] = ("surprise", "question")
    return moods


def _agents(active, alive=True, waiting=False, voice_now=None):
    busy = {}
    for t in active:
        for s in t["steps"][-3:]:
            busy[_who(s)] = s["text"]
    moods = _moods(active, alive, waiting, voice_now)
    return [{"code": code, "name": name, "role": role, "busy": busy.get(code), "emotion": moods[code][0], "thought": moods[code][1]}
            for code, name, role in AGENTS]


def overview(q=None):
    """What both apps poll every couple of seconds: only the fields a screen of theirs reads (tests/test_mac.py, test_android.py)."""
    from sena.agents.ag_web import browser
    from sena.google import auth
    from sena.telegram import account
    active = _active()
    today = _probe(lambda: costs.summary("today"), {"total": 0})
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    state, question, sends = core(), ask_owner.pending(), sena_tools.pending_sends()
    return {
        "now": time.time(), "core": state, "process": process(),
        "accounts": {"telegram_bot": bool(config.TELEGRAM_TOKEN), "telegram_account": _probe(lambda: account.client is not None, False),
                     "google": _probe(auth.state, "?"), "youtube": _probe(youtube.signed_in, False), "browser": _probe(lambda: browser._context is not None, False),
                     "keys": {k: config.has(k) for k in KEYS}},
        "today": {"tasks": sum(e["start"] >= midnight and not e.get("service") for e in task_log.items),
                  "errors": _probe(lambda: events.count("today", "error"), 0), "usd": today["total"]},
        "settings": settings.load(), "question": question, "pending_sends": sends,
        "budget": {"limit": settings.get("budget_usd_day"), "spent": today["total"]},
        "scheduler": {"state": store.load_json(config.JOBS_STATE_FILE, {}), "busy": scheduler.busy(), "jobs_total": len(scheduler.jobs())},
        "active": active, "agents": _agents(active, state["alive"], bool(question or sends), _probe(voice.state)),
        "index": {"items": _probe(index.count, 0), "pending": _probe(index.pending, 0)},
    }


def _moment(q, key):
    return number(q, key, None, 0, 4e9, float)


def _same(q, reply):
    """A polled list with its signature (sig, of everything but "now"): the app sends back the one it holds (same=…) and, while
    nothing changed, gets {"same": true} instead of the whole list again — the Mac's task list was 1.8 MB every 3 seconds."""
    sig = hashlib.blake2b(json.dumps({k: v for k, v in reply.items() if k != "now"}, sort_keys=True, ensure_ascii=False, default=str).encode(),
                          digest_size=8).hexdigest()
    if q.get("same") == sig:
        return {"same": True, "sig": sig, **({"now": reply["now"]} if "now" in reply else {})}
    return {**reply, "sig": sig}


SLIM_DROP = ("steps", "cards")   # what a finished row in a list leaves out (slim=1): the task's page (task) has them, and they are half the bytes


def tasks(q):
    finished = data.finished_tasks(number(q, "limit", 100, 1, task_log.MAX_TASKS))
    if q.get("slim") == "1":
        finished = [{k: v for k, v in t.items() if k not in SLIM_DROP} for t in finished]
    return _same(q, {"now": time.time(), "active": _active(), "finished": finished})


def task(q):
    t = data.task(q.get("id", ""))
    if "error" in t:
        return t
    end = (t.get("end") or time.time()) + 600   # analysis and the verdict arrive after the task ends
    t["events"] = events.search(tid=t["id"], since=t["start"] - 1, until=end, limit=1000) if t.get("id") else []
    return t


def events_rows(q):
    # the actor (who) is filtered in the query before limit: otherwise filtering the most recent entries would leave almost nothing
    return events.search(valid_period(q), q.get("q", ""), q.get("kind", ""), q.get("tid", ""), limit=number(q, "limit", 300, 1, 3000),
                         since=_moment(q, "since"), until=_moment(q, "until"), who=q.get("who", ""))


def kinds(q):
    return {"kinds": list(events.KINDS), "counts": events.counts(valid_period(q), _moment(q, "since"), _moment(q, "until"),
                                                                 q.get("q", ""), q.get("tid", ""), q.get("who", ""))}


def log(q):
    """The process log's tail: without offset — the last LOG_TAIL bytes, with offset — only what's been appended since last time."""
    offset = number(q, "offset", -1, -1, 2 ** 62)
    try:
        size = LOG_FILE.stat().st_size
    except OSError:
        return {"offset": 0, "size": 0, "text": "", "path": str(LOG_FILE)}
    fresh = offset < 0 or offset > size or size - offset > LOG_TAIL   # first time, the log was truncated, or we fell so far behind there's nothing to catch up on
    start = max(0, size - LOG_TAIL) if fresh else offset
    with open(LOG_FILE, "rb") as f:
        f.seek(start)
        text = f.read(size - start).decode("utf-8", "replace")
    if fresh and start > 0:
        text = text.split("\n", 1)[-1]   # don't start in the middle of a line
    return {"offset": size, "size": size, "text": text, "path": str(LOG_FILE), "fresh": fresh}


def costs_view(q):
    period = valid_period(q, "month")
    return {"summary": costs.summary(period), "rows": costs.recent(period, number(q, "limit", 300, 1, 5000)), "usage": limits.usage(),
            "prices_as_of": costs.PRICES_AS_OF}


def _index_view():
    return {"items": _probe(index.count, 0), "pending": _probe(index.pending, 0), "by_kind": _probe(index.counts, {}),
            "size": _probe(lambda: config.INDEX_DB.stat().st_size, 0), "stats": _probe(index.stats, {}),
            "embed": roles.model_of("embed"), "embed_place": roles.where("embed"),   # who counts the fingerprints: at home free, in the cloud paid
            "ann_threshold": {"vectors": index.ANN_VECTORS, "search_ms": index.ANN_SEARCH_MS}}   # when the seam in _vector_hits should open


MEMORY_PARTS = {"text": facts.read, "archive": lambda: store.read_text(config.ARCHIVE_FILE), "personality": personality.read,
                "sections": facts.sections, "updated": lambda: _probe(lambda: config.MEMORY_FILE.stat().st_mtime), "index": _index_view}


def memory_view(q=None):
    """Memory as the screens read it; fields=sections,index — only those parts are read (the phone polls the sections and the
    registry's numbers, not the archive that only grows and the index counts taken under its lock)."""
    wanted = {f for f in str((q or {}).get("fields") or "").split(",") if f in MEMORY_PARTS} or set(MEMORY_PARTS)
    return {name: part() for name, part in MEMORY_PARTS.items() if name in wanted}


def people_view(q=None):
    """The people both apps show: the phone's rows (current facts as plain text — a retired fact stays only in the card's
    history), plus what the Mac shows besides: how many voice prints, when the card was made."""
    rows = data.people_rows()
    cards = {p["id"]: p for p in people.all_people()}
    return [{**r, "voices": len(cards.get(r["id"], {}).get("voices") or []), "created": cards.get(r["id"], {}).get("created")} for r in rows]


def skills_view(q):
    name = q.get("name")
    if name:
        path, code = skills._path(name), skills._code_path(name)
        if not path.exists():
            return {"error": f"Навыка «{name}» нет"}
        text = path.read_text(encoding="utf-8")
        when, _, recipe = text.partition("\n")
        return {"name": name, "when": when.strip(), "recipe": recipe.strip(), "code": code.read_text(encoding="utf-8") if code.exists() else "",
                "updated": path.stat().st_mtime, "size": path.stat().st_size}
    return [{**s, "updated": _probe(lambda s=s: skills._path(s["name"]).stat().st_mtime), "size": _probe(lambda s=s: skills._path(s["name"]).stat().st_size, 0)}
            for s in skills.catalog()]


def notes_view(q):
    return {"folders": vault.FOLDERS, "root": str(vault.root()), "notes": vault.find(q["q"], q.get("folder", "")) if q.get("q") else vault.notes(q.get("folder", ""))}


def rules_view(q=None):
    """A single list of everything Sena does on her own, regularly or conditionally: the owner's standing and one-off rules
    together with their scheduled jobs, jobs without a rule (if any remain), and price alerts — all in one list,
    with no split into subsections (the owner asked for it this way on 2026-10-18)."""
    linked = {j for r in rules.all_rules() for j in r["jobs"]}
    loose = [j for j in scheduler.jobs() if j["id"] not in linked]   # jobs scheduled not through a rule
    return {"rules": rules.rows(), "jobs": loose, "alerts": alerts.active()}


def changes_view(q=None):
    # each change with what is said about it wherever it is shown (ag_code.verdict): why it can't be applied, what to look at first
    return {"changes": [{**c, **ag_code.verdict(c)} for c in reversed(ag_code.changes())], "pending": store.read_text(ag_code.PENDING_FILE).strip() or None,
            "good": store.read_text(ag_code.GOOD_FILE).strip() or None, "branches": [b.strip("* ") for b in _git("branch", "--list", "sena/auto-*").splitlines()],
            "head": _git("rev-parse", "--short", "HEAD"), "dirty": _dirty()}


def config_view(q=None):
    return {
        "models": {"main": config.MODEL_MAIN, "fast": config.MODEL_FAST, "voice": config.MODEL_VOICE, "transcribe": config.MODEL_TRANSCRIBE,
                   "tts": f"{config.MODEL_TTS} · {config.VOICE}", "embed": roles.model_of("embed"), "embed_place": roles.where("embed"),   # the one counting now
                   "embed_dims": config.EMBED_DIMS},
        "paths": {"root": str(config.ROOT), "data": str(config.DATA), "vault": str(config.VAULT_DIR), "desktop": str(config.DESKTOP),
                  "models": str(config.MODELS_DIR), "log": str(LOG_FILE), "events_db": str(config.EVENTS_DB), "index_db": str(config.INDEX_DB)},
        "keys": {k: config.has(k) for k in KEYS}, "required": list(config.REQUIRED_KEYS),
        "app_port": config.APP_PORT, "owner_id": bool(config.OWNER_ID), "headless": config.BROWSER_HEADLESS,
        "scheduler": {"max_attempts": scheduler.MAX_ATTEMPTS},
        "memory": {"history_recent": history.RECENT, "history_max": history.MAX_MESSAGES, "tasks_max": task_log.MAX_TASKS,
                   "reflect_every": personality.EVERY, "backup_days": ag_night.KEEP_BACKUPS_DAYS},
        "settings": settings.load(), "options": settings.OPTIONS, "ranges": settings.RANGES,
        "prices_as_of": costs.PRICES_AS_OF, "process": process(), "core": core(),
    }


# Integrations for the app's section: the settings flag (if it can be turned off), which events count as usage
# (kind, field, allowed field values), which cost providers count as money, which words in errors count as failures
# (unless: words that make an error another integration's — «Claude Code …» is the coding agent's, not the brain's)
# which models count for whom: the "model" event stores the model name from the request
CLAUDE_MODELS = {m for m in (config.MODEL_MAIN, config.MODEL_FAST, config.MODEL_VOICE) if m.startswith("claude")}
OPENAI_MODELS = {m for m in (config.MODEL_MAIN, config.MODEL_FAST, config.MODEL_VOICE) if not m.startswith("claude")}
TELEGRAM_ACCOUNT_WORDS = ("аккаунт Телеграма", "Вход в Телеграм", "Telethon")
INTEGRATIONS = {
    "telegram_bot": {"events": [("message_in", "source", {"telegram"})], "words": ("Телеграм",), "unless": TELEGRAM_ACCOUNT_WORDS},
    "telegram_account": {"flag": "use_telegram_account", "events": [("tool", "name", {"list_chats", "read_chat", "send_message"})],
                         "words": TELEGRAM_ACCOUNT_WORDS, "restart": True},
    "app": {"events": [("message_in", "source", {"app", "desk"})], "words": ("Сервер приложения",)},
    # the phone's own channel is its live voice (its chat and screens go through the same /ask and desk API as this Mac's)
    "phone": {"events": [("voice", "device", {"phone"}), ("app", "source", {"phone"})], "words": ("телефон",)},
    # ready(installed): whether what it needs is on this Mac (files and programs only — nothing is woken up to check)
    "hearing": {"flag": "use_hearing", "events": [("heard", None, None)], "words": ("Распознавание речи", "слух", "whisper"),
                "ready": lambda have: have.get("hearing", False)},
    "scheduler": {"events": [("job", None, None), ("heartbeat", None, None)], "words": ("Расписание", "по расписанию", "Фоновая проверка")},
    "memory": {"events": [("sync", None, None), ("embeddings", None, None)], "words": ("память", "Указатель", "указател")},
    "obsidian": {"events": [("sync", "source", {"vault"})], "words": ("заметк", "Obsidian", "хранилищ")},
    "sources": {"events": [("fetch", "source", {"Open-Meteo", "Hacker News", "источник данных"})], "words": ("Open-Meteo", "новост", "курс")},
    "claude_api": {"events": [("model", "model", CLAUDE_MODELS)], "providers": ("anthropic",), "words": ("Claude",), "unless": ("Claude Code",)},
    # needed: whether anything of Sena's uses it now; an unneeded one without a key is "not used", not "not connected"
    "openai": {"events": [("model", "model", OPENAI_MODELS), ("embeddings", "model", {config.MODEL_EMBED})], "providers": ("openai",), "words": ("OpenAI",),
               "needed": lambda: bool(OPENAI_MODELS) or roles.where("embed") == config.CLOUD},
    "home": {"events": [("model", "model", {config.LOCAL_CHAT}), ("embeddings", "model", {config.LOCAL_EMBED}), ("read", None, None),
                        ("transcribe", "model", {config.WHISPER_MODEL.name, config.WHISPER_SMALL.name}), ("speech", "model", {config.LOCAL_VOICE.removesuffix(".onnx"), config.LOCAL_VOICE_FALLBACK})],
             "words": ("домашн", "Дома не вышло", "Распознавание дома"), "ready": lambda have: have.get("embed", False)},
    "browser": {"flag": "use_browser", "events": [("browser", None, None), ("tool", "name", {"browser_task"})], "words": ("браузер", "Chrome", "Playwright")},
    "mac": {"events": [("tool", "name", {"computer_task"})], "words": ("компьютер",)},
    "claude": {"flag": "use_claude", "events": [("tool", "name", {"claude_task"})], "providers": ("claude-code",), "words": ("Claude Code",),
               "ready": lambda have: shutil.which("claude", path=_PATH) is not None},
    "google": {"flag": "use_google", "events": [("google", None, None), ("fetch", "source", {"Google"})], "words": ("Google", "почт", "Таблиц"),
               "unless": ("Google Maps",)},
    "gemini": {"flag": "use_gemini", "events": [("media", None, None), ("fetch", "source", {"Gemini"}), ("speech", "model", {config.MODEL_TTS}),
                                                ("transcribe", "model", {config.MODEL_TRANSCRIBE})], "providers": ("gemini",), "words": ("Gemini", "картинк", "озвучк")},
    "maps": {"flag": "use_maps", "events": [("fetch", "source", {"TomTom", "Google Maps", "OpenStreetMap"})], "providers": ("tomtom", "google-maps", "google_maps", "maps"), "words": ("TomTom", "Google Maps", "маршрут")},
    "travel": {"flag": "use_travel", "events": [("fetch", "source", {"Авиасейлс", "Travelpayouts"})], "providers": ("travelpayouts", "aviasales"), "words": ("Авиасейлс", "Travelpayouts", "билет")},
    "luma": {"flag": "use_luma", "events": [("tool", "name", {"luma", "luma_tools"})], "words": ("Luma",), "ready": lambda have: config.LUMA_APP.exists()},
    "mcp": {"flag": "use_mcp", "events": [("tool", "name", {"mcp_tools", "mcp_call"})], "words": ("MCP",)},   # the owner's MCP servers, all of them at once
    "site": {"flag": "use_site", "events": [("fetch", "source", {"Сайт"})], "words": ("Сайт", "портфолио")},
    "github": {"flag": "use_github", "events": [("fetch", "source", {"GitHub"})], "words": ("GitHub",)},
    "hireflow": {"flag": "use_hireflow", "events": [("fetch", "source", {"HireFlow", "hh.ru"})], "providers": ("hireflow",), "words": ("Hire-flow", "HireFlow", "вакансии")},
    "youtube": {"flag": "use_youtube", "events": [("tool", "name", {"youtube_channel", "youtube_videos", "youtube_video", "youtube_comments", "youtube_stats", "youtube_change"})], "words": ("YouTube",)},
}


TALLY_FRESH = 30   # s: the use counters of the integrations are counted again at most this often (the apps ask every few seconds)
_tallies = {}      # (kind, field, values) -> (when counted, since what moment, (uses since then, last use))


def _names(meta, text):
    """An error's text (lower case) is this integration's: one of its words, none of the words that make it another's."""
    return any(w.lower() in text for w in meta.get("words", ())) and not any(u.lower() in text for u in meta.get("unless", ()))


def integrations_view(q=None):
    """Integration state: whether it's enabled, how many times it's been used today and when last, today's errors and spend.
    The app asks for this every five seconds, so the log is read via counters, without event details; the counters themselves
    (a "last use" walks the journal back to the last such event) are reused for TALLY_FRESH seconds."""
    current = settings.load()
    since = costs.span("today")[0]
    errors = events.brief("today", "error", 500)
    by_provider = dict(_probe(lambda: costs.summary("today")["by_provider"], []))
    have = _probe(local.installed, {})   # the home engines on this Mac: checked once per call
    now = time.time()

    def tally(kind, column, want):
        key = (kind, column, tuple(sorted(want or ())))   # identical queries (fetch, tool…) serve several integrations at once
        hit = _tallies.get(key)
        if not hit or hit[1] != since or now - hit[0] >= TALLY_FRESH:
            hit = _tallies[key] = (now, since, events.tally(kind, column, want or (), since))
        return hit[2]

    out = {}
    for name, meta in INTEGRATIONS.items():
        uses, last = 0, None
        for kind, column, want in meta["events"]:
            n, moment = tally(kind, column, want)
            uses += n
            if moment is not None:
                last = max(last or 0, moment)
        errs = [e for e in errors if _names(meta, (e.get("text") or "").lower())]
        usd = sum(v["usd"] for p, v in by_provider.items() if any(k in p for k in meta.get("providers", ())))
        flag = meta.get("flag")
        out[name] = {"flag": flag, "enabled": current.get(flag, True) if flag else None, "uses_today": uses, "last_used": last,
                     "errors_today": len(errs), "last_error": errs[-1]["text"] if errs else None, "usd_today": usd, "restart": meta.get("restart", False),
                     "needed": meta.get("needed", lambda: True)(), "ready": _probe(lambda: meta["ready"](have), True) if "ready" in meta else True}
    return out


def _integration(b):
    """Turn an integration on or off from the remote: the settings flag changes, and the model stops seeing its tools from the next turn on."""
    name, on = field(b, "id"), field(b, "on", bool)
    meta = INTEGRATIONS.get(name)
    if not meta or not meta.get("flag"):
        return {"error": "эту интеграцию нельзя выключить: она часть самой Сены"}
    settings.update({meta["flag"]: on})
    note = " — аккаунт подключится при следующем запуске Сены" if meta.get("restart") and on else ""
    return {"result": f"{name}: {'включена' if on else 'выключена'}{note}", "enabled": on}


def history_view(q):
    rows = history.stored(config.OWNER_ID)
    return _same(q, {"messages": rows[-number(q, "limit", 200, 1, 10000):], "total": len(rows), "summary": history.summary(config.OWNER_ID)})


def allowed_path(path, must_exist=True):
    """A path within Sena's file folders (the remote never hands out, opens, or attaches to a task anything else)."""
    if not isinstance(path, str) or not path.strip():
        raise ApiError(400, "Не указан путь к файлу")
    try:
        p = os.path.realpath(path)
    except ValueError:
        raise ApiError(400, "Путь к файлу записан неверно")
    if not any(p.startswith(os.path.realpath(str(d)) + os.sep) for d in FILE_DIRS.values()):
        raise ApiError(403, "Файл вне папок Сены")
    if must_exist and not os.path.isfile(p):
        raise ApiError(404, "Такого файла нет")
    return p


def files_view(q):
    """Files the owner sent, Sena downloaded, or Sena made: newest on top."""
    rows = []
    for folder, d in FILE_DIRS.items():
        if not d.exists():
            continue
        for p in d.iterdir():
            try:
                if p.is_file() and not p.name.startswith("."):
                    st = p.stat()
                    rows.append({"name": attachments.shown_name(p), "file": p.name, "path": str(p), "size": st.st_size,
                                 "mtime": st.st_mtime, "kind": attachments.kind(p), "folder": folder})
            except OSError:
                continue   # the file was deleted while we were reading the list
    rows.sort(key=lambda r: -r["mtime"])
    return rows[:number(q, "limit", 300, 1, 1000)]


def file_raw(q):
    """The file itself (an image to view, text, PDF): returned as bytes."""
    p = allowed_path(q.get("path"))
    if os.path.getsize(p) > FILE_LIMIT:
        raise ApiError(413, "Файл слишком большой для просмотра в приложении")
    kind = attachments.kind(p)
    if kind == "image":
        return attachments.image_data(p)
    if kind in ("pdf", "text") or p.lower().endswith((".mp4", ".m4v", ".mov")):
        with open(p, "rb") as f:
            blob = f.read()
        return blob, MIME.get(kind, "video/mp4")
    return b"", "application/octet-stream"


def home_view(q=None):
    """The home and the cloud: who does what and why, what is installed on this Mac and awake, how many calls stayed home today
    and how many went out. Read every few seconds by the apps: no model is woken up by it."""
    today = _probe(lambda: costs.summary("today"), {})
    home = today.get("home") or {"calls": 0, "by_service": {}}
    return {"roles": roles.table(), "status": local.status(), "learning": {"min_examples": roles.MIN_EXAMPLES},
            "today": {"home": home["calls"], "home_by_service": home["by_service"], "out": today.get("calls", 0), "usd": today.get("total", 0.0)}}


def question_image(q=None):
    picture = ask_owner.image((q or {}).get("id") if isinstance(q, dict) else None)
    return (picture or b""), "image/jpeg"


GET = {"overview": overview, "tasks": tasks, "task": task, "events": events_rows, "kinds": kinds, "log": log,
       "costs": costs_view, "memory": memory_view, "people": people_view, "skills": skills_view, "notes": notes_view, "rules": rules_view,
       "changes": changes_view, "config": config_view, "history": history_view, "state": lambda q: data.state(),
       "files": files_view, "file": file_raw, "question_image": question_image, "integrations": integrations_view,
       "secrets": lambda q: {"items": secrets.names(), "agents": ["ag_mac", "ag_web"]},   # names and notes only: reading them isn't an action worth a journal line
       "home": home_view,
       "panels": lambda q: panels.listing(), "panel": lambda q: panels.panel(q.get("id", ""))}   # screens the server describes (phone/panels.py)


def get(name, q):
    func = GET.get(name)
    if not func:
        raise ApiError(404, "Не найдено")
    return func(q)


# ---------- actions ----------

def _background(what, func):
    """A manual run of a background job — the same path as a scheduled one: marked "running now". A double click won't trigger a paid run twice."""
    if not scheduler.run_now(what, func):
        return {"error": f"«{what}» уже идёт: дождись конца"}
    return {"result": f"{what}: запущено в фоне, ход — в журнале и логе"}


_embed_lock = threading.Lock()


def _embed(b):
    # fingerprints for what has none (at home, or a paid call if the role was moved to the cloud): a second run while the first is still going does nothing
    if not _embed_lock.acquire(blocking=False):
        return {"error": "Индексация уже идёт: дождись конца"}
    try:
        return {"result": f"Отпечатков досчитано: {index.embed_pending()[0]}"}
    finally:
        _embed_lock.release()


STOP_HARD = 30   # a normal stop finishes or writes down the tasks (up to 8 s) and stops her programs; past this it is cut off


STARTING_FOR = 60   # s: a "the core is starting" marker younger than this means a core is on its way up (the app waits as long)


def _mark_starting():
    # "the core is starting": the app won't bring up a second screen while the port is being handed to the core (app.main clears the marker)
    store.write_text(config.STARTING_FILE, str(time.time()))


def _starting():
    """A core is on its way up: started from an app, restarted after an edit or by run.sh after a fall (it writes the same marker)."""
    t = _probe(lambda: float(config.STARTING_FILE.read_text().strip()), 0)
    return 0 <= time.time() - t < STARTING_FOR


def _missing_keys(env):
    """The keys a core started with this environment would lack: .env read now (the owner may have just added one) under the
    environment as run.sh hands it on — what its config.check reads before stopping with exit 78."""
    from dotenv import dotenv_values
    fresh = {**{k: v for k, v in dotenv_values(config.ENV_FILE).items() if v}, **env}
    return [k for k in config.REQUIRED_KEYS if not fresh.get(k)]


def _core_start(b):
    """Turn Sena on from the remote. This process is screen-only: we launch run.sh and free up the port for it; the app will reconnect.
    Only from a screen whose core is really off: a second press while one is coming up would start a second run.sh. Not without
    the keys the core can't start without: it would stop at once, and nothing would bring this screen back — the phone away from
    the Mac would lose it for good, the Mac app would say only «выключена»."""
    if core()["alive"]:
        return {"result": "Сена уже работает"}
    if not power.is_off() or _starting():   # this process is the core itself, still coming up, or a core is about to take the port
        return {"error": "Сена уже запускается: подожди несколько секунд"}
    env = {**os.environ, "PATH": _PATH}
    missing = _missing_keys(env)
    if missing:
        return {"error": "Сена не запустится: в .env не хватает " + ", ".join(missing) + ". Допиши их (образец — .env.example) и нажми «Запустить» снова"}
    _mark_starting()
    with open(config.LOGS_DIR / "app_launch.log", "ab") as log:
        log.write(f"\n=== запуск из пульта {datetime.now():%Y-%m-%d %H:%M:%S} ===\n".encode())
        # the owner's own file mask, not this process's private one: run.sh and the core it starts pass it on to his tools
        subprocess.Popen(["/bin/zsh", str(config.ROOT / "run.sh")], cwd=config.ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True, env=env, umask=config.OWNER_UMASK)
    threading.Timer(0.7, lambda: os._exit(0)).start()   # hand the port over to the core
    return {"result": "Запускаю Сену: несколько секунд, и приложение вернётся уже с ядром", "handover": True}


def _core_stop(b):
    """Turn Sena off from the remote: the core exits normally, and run.sh doesn't restart it but brings the screen up without the core."""
    c = core()
    if not c["alive"]:
        if c["pid"]:   # a crashed run's pid file, its number now someone else's or nobody's: never signalled, just cleared
            config.PID_FILE.unlink(missing_ok=True)
        return {"error": "Ядро и так не запущено"}
    store.write_text(config.STOP_FILE, "1")
    events.log("stop", "остановка из приложения")
    pid = c["pid"]
    threading.Timer(0.7, lambda: os.kill(pid, signal.SIGTERM)).start()
    hard = threading.Timer(STOP_HARD, lambda: _kill_if_still_sena(pid))   # didn't exit normally: otherwise the port stays taken while the app already says "off"
    hard.daemon = True
    hard.start()
    return {"result": "Останавливаю Сену: приложение вернётся без ядра через несколько секунд", "handover": True}


def _kill_if_still_sena(pid):
    try:
        if _is_core(pid, cached=False):   # the number may already be another program's: asked now, not from a minute ago
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass   # already exited


def _restart(b):
    if not core()["is_core"]:
        return {"error": "Ядро не запущено — перезапускать нечего. Сначала «Запустить Сену»"}
    events.log("stop", "перезапуск из приложения")
    _mark_starting()
    me = os.getpid()
    threading.Timer(1.5, lambda: os.kill(me, signal.SIGTERM)).start()   # a normal exit: the databases close; run.sh will bring it back up
    fallback = threading.Timer(STOP_HARD, lambda: os._exit(3))   # if it didn't exit normally — force it
    fallback.daemon = True
    fallback.start()
    return {"result": "Перезапускаюсь: секунд через десять приложение снова ответит", "handover": True}


def _settings(b):
    before = settings.load()
    after, rejected = settings.apply(b)
    changed = {k: after[k] for k in after if before.get(k) != after[k]}
    return {"settings": after, "changed": changed, "rejected": rejected, "_log": {"changed": changed, "rejected": rejected or None}}


def _memory(b):
    # the whole memory text at once (an older Mac build; the apps now change one line at a time — fact_add/fact_edit/fact_delete)
    text, snapshot = field(b, "text"), field(b, "snapshot", str, None)
    before = {line.strip() for line in (snapshot if snapshot is not None else facts.read()).splitlines()}
    secret = next((line.strip() for line in text.splitlines() if line.strip() not in before and facts.secret_like(line)), None)
    if secret:   # memory is in every prompt in plain text: a password typed into it is refused like remember refuses it
        return {"error": f"Не сохранено: «{secret[:60]}» похоже на пароль, код или ключ — им место в разделе «Секреты» "
                         "(там они зашифрованы и выдаются по правилам), не в памяти"}
    facts.replace(text, snapshot)   # snapshot — the text the owner saw: Sena's edits made in the meantime aren't lost
    return {"result": "Память сохранена", "text": facts.read(), "_log": {"size": len(text)}}


_FACT_DONE = ("Запомнила", "Обновила", "Заменила", "Удалила")   # how facts.remember / forget say they did it


def _fact(out):
    """One memory line changed from an app (instead of rewriting the whole memory): done — {"result"}; anything else (a refusal,
    several lines fit, a password, a relative date) — {"error"} with the reason, nothing changed."""
    return {"result": out} if not isinstance(out, Failure) and str(out).startswith(_FACT_DONE) else {"error": str(out)}


def _open(b, reveal=False):
    p = allowed_path(field(b, "path"))
    name = os.path.basename(p)
    if not reveal and (os.path.splitext(p)[1].lower() not in OPENABLE or os.access(p, os.X_OK)):
        return {"error": f"«{name}» такого типа (или с правом запуска) я из приложения не открываю: он может запустить программу. Покажи его в Finder и открой сам, если уверен"}
    opened = subprocess.run(["open", "-R", p] if reveal else ["open", p], capture_output=True, timeout=10)
    if opened.returncode != 0:
        return {"error": f"Не получилось открыть «{name}»"}
    return {"result": ("Показываю в Finder: " if reveal else "Открываю: ") + name}


def _answer(b):
    text = field(b, "text").strip()
    if not text:
        return {"error": "пустой ответ"}
    shown = ask_owner.pending()   # the app shows the oldest question: its answer box answers that one, unless it names another
    if not ask_owner.deliver(text, field(b, "id", str, "") or (shown or {}).get("id")):
        return {"error": "Сена сейчас ничего не спрашивает"}
    events.log("message_in", text, source="desk", answer=True)
    return {"result": "Ответ передан Сене"}


def _checkpoint(b):
    events.checkpoint()
    index.checkpoint()
    return {"result": "Хвосты WAL записаны в базы"}


def _strings(b, key):
    values = field(b, key, list, [])
    if not all(isinstance(v, str) for v in values):
        raise ApiError(400, f"В поле «{key}» должны быть строки")
    return values


# this is how actions write a substantive rejection (no such record, not saved): for the app it's an error, not a green "done" toast
_REFUSED = ("Не сохранено", "Не найдено", "Не знаю", "Назови", "Человек не найден", "Задача не найдена", "Такого правила нет", "Такой отправки уже нет", "Навыка «")


def _text(out):
    """The response of an action that returns a string: a substantive rejection — {"error"}, everything else — {"result"}.
    A Failure is a refusal by its own word; the prefixes are for the producers that still refuse in plain text."""
    refused = isinstance(out, Failure) or (isinstance(out, str) and (out.startswith(_REFUSED[:-1]) or (out.startswith(_REFUSED[-1]) and out.endswith("» нет"))))
    return {"error": str(out)} if refused else {"result": out}


def _person(b):
    # a field that's absent from the request is left untouched; an empty one means clear it (the owner erased the text).
    # aliases, when sent, are the whole list as the owner left it; retire — current facts he removed (they stay in the card's history)
    given = {k: field(b, k, str, None) for k in ("name", "relationship", "opinion")}
    fields = {k: v.strip() for k, v in given.items() if v is not None and v.strip()}
    clear = [k for k, v in given.items() if v is not None and not v.strip()] + (["aliases"] if "aliases" in b else [])
    aliases = [a.strip() for a in _strings(b, "aliases") if a.strip()]
    return _text(people.update(field(b, "id"), clear=clear, aliases=aliases, facts=_strings(b, "facts"), retire=_strings(b, "retire"), **fields))


def _note_write(b):
    return data.write_note_result(field(b, "path"), field(b, "text"), field(b, "mode", str, "replace"))


def _note_delete(b):
    return data.delete_note_result(field(b, "path"))


def _person_add(b):
    # a new card from the app: the name and every other name up front (aliases — the list the owner typed; alias — one, as older
    # builds send it), the rest (relationship, opinion, facts) folded in right away if given
    names = list(dict.fromkeys(a.strip() for a in [field(b, "alias", str, None) or "", *_strings(b, "aliases")] if a.strip()))
    p = people.new_person(name=field(b, "name", str, None), alias=names[0] if names else None)
    fields = {k: v.strip() for k, v in {"relationship": field(b, "relationship", str, None), "opinion": field(b, "opinion", str, None)}.items() if v and v.strip()}
    facts_list = _strings(b, "facts")
    if fields or facts_list or names[1:]:
        people.update(p["id"], aliases=names[1:], facts=facts_list, **fields)
    return {"result": "Добавлено", "id": p["id"]}


def stop_task(b):
    # the task's own id (tid, what both apps show) or the number of the thread it runs on (older builds, a task still starting)
    ident = str(field(b, "id", (str, int))).strip()
    if not re.fullmatch(r"[0-9A-Za-z-]{1,64}", ident):
        raise ApiError(400, "Поле «id» должно быть номером задачи или потока")
    return {"result": cancel.stop_task(ident)}


def _or_error(made):
    # a rule, job, or alert: a dict means success, a string means why it failed
    return made if isinstance(made, dict) else {"error": made}


def _change(func, b):
    # the edit can't be applied, or there's no such edit — that's a rejection, not a success
    ok, text = func(field(b, "id"))
    if ok and func is ag_code.try_apply_change:
        _mark_starting()   # Sena restarts after an edit is applied: the app shouldn't bring up a second screen
    return {"result": text} if ok else {"error": text}


def _history_clear(b):
    history.clear(config.OWNER_ID)
    return {"result": "Переписка очищена"}


def _secret_reveal(b):
    # the owner reads his own secret in the app (behind its lock): the value goes to him and nowhere else; the journal keeps the fact
    name = field(b, "name")
    value = secrets.reveal(name, "пульт")
    if value is None:
        return {"error": f"Секрета «{name}» нет или он помечен «никогда не показывать»"}
    return {"value": value}


ACTIONS = {
    "stop_all": lambda b: {"result": sena_tools.stop_all()},   # as his «стоп» in chat: a long work's running turn stops too, and the work pauses
    "stop_task": stop_task,
    "restart": _restart,
    "core_start": _core_start,
    "core_stop": _core_stop,
    "heartbeat": lambda b: _background("Фоновая проверка", ag_sena.heartbeat),
    "tidy": lambda b: _background("Ночная уборка", ag_night.tidy),
    "weekly": lambda b: _background("Недельный разбор", ag_judge.weekly),
    "backup": lambda b: _background("Копия памяти", lambda: ag_night.backup_memory(force=True)),   # asked for now: made even if the night's copy exists
    "sync_index": lambda b: _background("Сверка указателя", recall.sync_all),
    "sync_vault": lambda b: _background("Сверка хранилища заметок", vault_sync.sync_all),
    "embed": _embed,
    "checkpoint": _checkpoint,
    "recost": lambda b: {"result": f"Пересчитано записей: {costs.recost()}"},
    "settings": _settings,
    "memory": _memory,
    # one line: old and text are lines as memory shows them (with their date); an edited line replaces the old one, which keeps its history in the archive
    "fact_add": lambda b: _fact(facts.remember(field(b, "section"), field(b, "text"))),
    "fact_edit": lambda b: _fact(facts.remember(field(b, "section"), field(b, "text"), instead_of=field(b, "old"))),
    "fact_delete": lambda b: _fact(facts.forget(field(b, "text"))),
    # the owner's own rewrite: the guards that keep what the judge and the night learned from the model's rewrites don't stop him
    "skill_write": lambda b: _text(skills.write_skill(field(b, "name"), field(b, "when", str, ""), field(b, "recipe", str, ""), field(b, "code", str, None), by="owner")),
    "skill_delete": lambda b: _text(skills.delete_skill(field(b, "name"))),
    "person": _person,
    "person_add": _person_add,
    "person_delete": lambda b: _text(people.delete(field(b, "id"))),
    "person_merge": lambda b: _text(people.merge(field(b, "keep"), field(b, "drop"))),
    "heard_forget": lambda b: _text(heard.forget(field(b, "person", str, ""), field(b, "since", (int, float), None), field(b, "until", (int, float), None))),   # the owner's own click: no second confirmation
    # the keeper of secrets: values come in and go out only here, behind the app's own lock; the model is never in the loop
    # note, allow and sites: absent from the request — kept as they are; a value (even an empty one) replaces them
    "secret_put": lambda b: _text(secrets.put(field(b, "name"), field(b, "value", str, ""), field(b, "note", str, None), _strings(b, "allow") if "allow" in b else None,
                                              field(b, "reveal", str, "confirm"), _strings(b, "sites") if "sites" in b else None)),
    "secret_remove": lambda b: _text(secrets.remove(field(b, "name"))),
    "secret_reveal": _secret_reveal,
    "role_move": lambda b: _text(roles.move(field(b, "role"), field(b, "place"), "так решил владелец")),   # the owner moves a job home or to the cloud
    "note_write": _note_write,
    "note_delete": _note_delete,
    "job_remove": lambda b: _text(scheduler.unschedule(field(b, "id"))),
    "rule_add": lambda b: _or_error(rules.add(field(b, "text"))),
    "rule_remove": lambda b: _text(rules.remove(field(b, "id"))),
    "rule_toggle": lambda b: _or_error(rules.toggle(field(b, "id"), field(b, "enabled", bool, True))),
    "job_toggle": lambda b: _or_error(scheduler.toggle(field(b, "id"), field(b, "enabled", bool, True))),
    "alert_toggle": lambda b: _or_error(alerts.toggle(field(b, "id"), field(b, "enabled", bool, True))),
    "alert_remove": lambda b: _text(alerts.remove(field(b, "id"))),
    "change_apply": lambda b: _change(ag_code.try_apply_change, b),
    "change_drop": lambda b: _change(ag_code.try_drop_change, b),
    "comment": lambda b: _text(ag_judge.comment(field(b, "id"), field(b, "text"))),
    "outcome": lambda b: _text(task_log.set_outcome(field(b, "id"), field(b, "verdict"), field(b, "reason", str, "оценка из приложения"), by="owner")),
    "rate": lambda b: _text(task_log.set_rating(field(b, "id"), field(b, "rating", int))),
    "history_clear": _history_clear,
    "answer": _answer,
    "integration": _integration,
    "confirm_send": lambda b: _text(panels.press(field(b, "id"), True)),   # beside the request: a long errand isn't cut by the app's timeout
    "cancel_send": lambda b: _text(panels.press(field(b, "id"), False)),
    "reveal": lambda b: _open(b, reveal=True),
    "open_file": _open,
    "panel_act": lambda b: panels.act(b),   # an action from a server-described panel: its answer and the panel as it is now
}


CORE_ACTIONS = {"heartbeat": "фоновую проверку", "tidy": "ночную уборку", "weekly": "недельный разбор", "embed": "отпечатки памяти",
                "sync_index": "сверку указателя", "sync_vault": "сверку заметок", "comment": "разбор задачи", "answer": "ответ помощнику",
                "confirm_send": "отправку сообщений", "change_apply": "правку своего кода"}   # they spend or need the core: refused while she is off


def act(name, body):
    """Carry out an action and log it as a single event (no separate event is logged inside the action;
    _log — extra details for this entry, never sent to the client)."""
    func = ACTIONS.get(name)
    if not func:
        raise ApiError(404, "Нет такого действия")
    if name in CORE_ACTIONS and power.is_off():
        events.log("app", f"приложение: {name}", source="desk", failed=True, error="Сена выключена")
        return {"error": power.refusal(CORE_ACTIONS[name])}
    if not isinstance(body, dict):
        raise ApiError(400, "Тело запроса должно быть объектом JSON")
    try:
        out = func(body)
    finally:
        _git_cache.clear()   # the action may have changed code, branches, or git state
    extra = out.pop("_log", {})
    if "error" in out:
        events.log("app", f"приложение: {name}", source="desk", failed=True, error=str(out["error"])[:300])
    else:
        events.log("app", f"приложение: {name}", source="desk", result=str(out.get("result", ""))[:300], **extra)
    return out
