"""Project settings: paths, keys from .env, model names. The rest of the code takes everything from here.
Keys are read into this module only: .env never goes into the process environment, so no program Sena starts and no code an
agent runs can find them there (core/safety.clean_env is the second line for keys that came from the shell)."""
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.environ.get("SENA_ENV") or ROOT / ".env")   # tests point this at an empty file: they never see the real keys
_SETTINGS = {**{k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None}, **os.environ}   # the shell wins, as before


def _env(name, default=""):
    return _SETTINGS.get(name) or default


def has(name):
    """Whether a key or setting is filled in (the control panel shows this, never the value)."""
    return bool(_SETTINGS.get(name))


# direct internet access, no proxy tunnels (agents get this environment too)
for _var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_var, None)

# ---------- keys (.env file) ----------
TELEGRAM_TOKEN = _env("TELEGRAM_TOKEN")      # Sena's Telegram bot
OWNER_ID = int(_env("TG_OWNER_ID") or 0)     # the bot only answers Tema
TG_API_ID = int(_env("TG_API_ID") or 0)      # access to Tema's account (Telethon)
TG_API_HASH = _env("TG_API_HASH")
OPENAI_API_KEY = _env("OPENAI_API_KEY")      # OpenAI: no longer needed by default (everything moved to Claude and Gemini); used only for an OpenAI model put in MODEL_* or cloud fingerprints (optional)
APP_TOKEN = _env("APP_TOKEN")                # the app server's password: it controls the computer
GEMINI_API_KEY = _env("GEMINI_API_KEY")      # Google AI Studio: Sena's voice, the backup hearing, images and video (without it she speaks with the home voice)
TOMTOM_KEY = _env("TOMTOM_KEY")              # TomTom: places and routes, free and without a card (optional)
TRAVELPAYOUTS_TOKEN = _env("TRAVELPAYOUTS_TOKEN")   # Aviasales Data API (Travelpayouts): tickets, free (optional)
GOOGLE_MAPS_KEY = _env("GOOGLE_MAPS_KEY")    # Google Maps Platform: unavailable for billing accounts in Vietnam (optional)
REQUIRED_KEYS = ("TELEGRAM_TOKEN", "SENA_CLAUDE_BRAIN_KEY", "TG_API_ID", "TG_API_HASH", "TG_OWNER_ID", "APP_TOKEN")
SECRET_KEYS = ("TELEGRAM_TOKEN", "OPENAI_API_KEY", "TG_API_HASH", "APP_TOKEN", "GEMINI_API_KEY", "TOMTOM_KEY",
               "TRAVELPAYOUTS_TOKEN", "GOOGLE_MAPS_KEY", "LUMA_KEY", "SENA_SITE_KEY", "SENA_GITHUB_TOKEN", "OPENALEX_KEY", "HIREFLOW_KEY")   # secrets: never end up in the terminal's or Claude Code's environment (core/safety.clean_env)
for _key in [k for k in os.environ if k in SECRET_KEYS or k.startswith(("SENA_CLAUDE_", "ANTHROPIC_"))]:
    os.environ.pop(_key)   # a key exported in the shell doesn't stay in the environment either: every program started later inherits it


def secret_values():
    """Sena's own keys as (value, name): masked out of every journal line and tool output like the owner's secrets."""
    names = [*SECRET_KEYS, "SENA_CLAUDE_BRAIN_KEY", "SENA_CLAUDE_AGENTS_KEY"]
    return [(_SETTINGS[k], k) for k in names if len(_SETTINGS.get(k) or "") >= 8]

# ---------- models in the cloud (core/llm.py) ----------
# Chosen by the model bench of 25.10.2026 (tools/model_bench): quality first, then speed and price.
MODEL_MAIN = "claude-opus-5-5"            # the main brain (key SENA_CLAUDE_BRAIN_KEY): coordinator, memory cleanup, personality
MODEL_FAST = "claude-haiku-5-5"           # fast and cheap: the browser and computer agents, the post-task review, narrow jobs not yet moved home
MODEL_VOICE = "claude-sonnet-5-5"         # voice commands ("Sena, ..."): an instant reply that must be right (15 of 16 at 3.7 s)
MODEL_TTS = "gemini-3.8-flash-tts"        # Sena's one voice everywhere (Telegram, the phone, the Mac): Gemini (google/media.py)
VOICE = "Kore"                            # the owner picked it by ear; the home voice speaks only when the cloud can't
VOICE_STYLE = "спокойно, тепло и естественно, как молодая помощница; носитель русского языка"   # how the voice speaks (the manner of the sample he chose)
MODEL_LIVE = "gemini-3.8-live"            # the live voice (phone/voice.py): answers in about a second in the voice VOICE; it only talks, the thinking is MODEL_VOICE's
MODEL_TRANSCRIBE = "gemini-3.5-transcribe"   # speech to text in the cloud: only when the home hearing fails (3.8% word errors, the best measured)
MODEL_EMBED = "text-embedding-3-small"    # semantic fingerprints in the cloud (OpenAI, needs its key): only if the "embed" role is moved there
EMBED_DIMS = 512                          # fingerprint width for both: a shortened fingerprint keeps nearly all the quality at a third of the space

# ---------- models at home, on this Mac (core/local.py) ----------
# One llama-server on 127.0.0.1 holds them: a model loads on the first request and sleeps after a while without work.
LOCAL_PORT = 8766
LOCAL_IDLE_SEC = 600                      # a model unused this long is unloaded: the Mac's memory goes back to the owner
LOCAL_EMBED = "embeddinggemma-300M-Q8_0.gguf"   # memory fingerprints: on the Russian paraphrase set (sena/home/bench.py) the answer was
                                                # in the top five for 98% of questions, ahead of bge-m3 (94%) and Qwen3-Embedding (96%); 4 ms a query
LOCAL_EMBED_PROMPTS = ("task: search result | query: ", "title: none | text: ")   # the model's own prefixes for a question and for a record
LOCAL_CHAT = "Qwen3.5-4B-Q4_K_M.gguf"     # narrow judgments at home in strict JSON: overheard speech, other people's messages and mail
LOCAL_VISION = "mmproj-Qwen3.5-4B-F16.gguf"   # the same model's eyes: what is in a photo (a description for the file registry)
LOCAL_VOICE = "ru_RU-irina-medium.onnx"   # Piper's Russian voice at home; without it macOS speaks with Milena
LOCAL_VOICE_FALLBACK = "Milena"           # the system voice: always there, plainer

# ---------- who does what ----------
# A role is a job that can work at home or in the cloud. Perception, memory and judgments about other people's private data
# stay at home; the cloud gets what needs a big mind. The owner (and the nightly check, see LEARNING) can move a role:
# settings "roles". Jobs that are not here are fixed: the brain, agents, the judge, nightly cleanup, personality and the weekly
# review think in the cloud (MODEL_MAIN, MODEL_FAST); the wake word, voice prints, reading documents and photos are always at home.
HOME, CLOUD = "home", "cloud"
ROLES = {
    "embed": HOME,       # fingerprints of everything Sena remembers, other people's speech included
    "hearing": HOME,     # the owner's voice to text (Telegram, the app); background hearing never leaves home
    "ears": HOME,        # overheard conversations -> people's cards
    "heartbeat": HOME,   # the hourly check reads other people's messages and the mail
    "compact": CLOUD,    # the summary of the older conversation (the conversation itself is already in the cloud brain's context)
    "harvest": CLOUD,    # long facts the owner mentioned in passing, from the conversation leaving the window (memory/harvest.py)
    "router": CLOUD,     # which ready recipe solves a browser task (the task text is written by the cloud brain)
    "rules": CLOUD,      # the time in a rule that the code could not read
    "feedback": CLOUD,   # the owner's next message read as a remark on the last answer (agents/ag_sena/agent.py)
    "plain": CLOUD,      # an agent's question to the owner retold in plain words (core/ask_owner.py)
}
PRIVATE = ("embed", "ears", "heartbeat")   # never fall back to the cloud when the home engine fails: the job waits instead
LEARNING = ("compact", "harvest", "router", "rules", "feedback")  # cloud answers are kept as examples (data/training); once the home model matches them, the role moves home

# ---------- folders ----------
def _test_outside_its_sandbox():
    # a test started past tests/__init__ (python tests/test_x.py, unittest discover -s tests) would write into the real data;
    # so would any module unittest's discovery imports before it reaches tests/ (it imports every package it walks through:
    # a package whose __init__ loaded this file once ran a whole suite on the real data, the Keychain and the vault)
    if _env("SENA_DATA"):
        return False
    if "unittest" in os.path.basename(sys.argv[0] or ""):
        return True
    tests = ROOT / "tests"
    return any(Path(getattr(m, "__file__", None) or "/").resolve().parent == tests for m in list(sys.modules.values()))


if _test_outside_its_sandbox():
    raise SystemExit("Тесты запускаются так: python -m unittest (из папки проекта) — иначе они пишут в настоящие данные")
DATA = Path(_env("SENA_DATA") or ROOT / "data")   # everything accumulated; never goes into git
# the Keychain, iCloud and the notes vault are the real Sena's: a throwaway data folder (a test, an experiment, the exam's world)
# keeps its keys as files, makes no copy and writes its own notes unless asked — an experiment once wrote a key into the Keychain
# the real core would have trusted
REAL_DATA = DATA.resolve() == (ROOT / "data").resolve()
MEMORY_DIR = DATA / "memory"        # what Sena knows
JOURNAL_DIR = DATA / "journal"      # what happened
STATE_DIR = DATA / "state"          # settings and technical state
BACKUPS_DIR = DATA / "backups"
LOGS_DIR = DATA / "logs"
BROWSER_PROFILE = DATA / "browser_profile"   # the agent's Chrome profile: website logins are stored here
DOWNLOADS_DIR = DATA / "downloads"           # files the agent downloaded in the browser and from email
INBOX_DIR = DATA / "inbox"                   # files and photos Tema sent in Telegram
GENERATED_DIR = DATA / "generated"           # images and video that Sena made
SPILL_DIR = DATA / "spill"                   # whole outputs too long for one look (a command, a mail, a table): Sena pages them via view_file
MCP_DIR = DATA / "mcp"                       # the working folders of the owner's MCP servers and the files their tools returned
SKILLS_DIR = MEMORY_DIR / "skills"
MODELS_DIR = ROOT / "models"        # heavy models: whisper, VAD, the name detector
DESKTOP = Path.home() / "Desktop"   # Tema's projects (ag_code) and the notes vault live here
# copies of her own code for self-changes, never inside the repository: Claude Code reads every CLAUDE.md up the tree, and a copy
# inside the live repo saw the live rules beside its own (the awesome-claude-code study)
SELF_COPIES = Path.home() / "Library" / "Caches" / "Sena" / "self" if ROOT in DATA.resolve().parents else DATA / "self"
VAULT_DIR = Path(_env("SENA_VAULT") or (DESKTOP / "Obsidian_sena" if REAL_DATA else DATA / "vault"))   # Sena's notes vault (Obsidian)
APK_FILE = ROOT / "android" / "sena.apk"

# ---------- data files ----------
MEMORY_FILE = MEMORY_DIR / "memory.md"
ARCHIVE_FILE = MEMORY_DIR / "archive.md"
PERSONALITY_FILE = MEMORY_DIR / "personality.md"
PEOPLE_FILE = MEMORY_DIR / "people.json"
RULES_FILE = MEMORY_DIR / "rules.json"      # Tema's rules: standing instructions and the schedule derived from them
GOALS_FILE = MEMORY_DIR / "goals.json"      # Tema's long goals: why, deadline, steps, how it goes, the next check (memory/goals.py)
WORKS_FILE = MEMORY_DIR / "works.json"      # long works Sena pursues turn after turn: goal, done-when, plan, checkpoint, budget (agents/ag_sena/work.py)
HISTORY_FILE = JOURNAL_DIR / "history.json"
SUMMARY_FILE = JOURNAL_DIR / "history_summary.json"
TASKS_FILE = JOURNAL_DIR / "tasks.json"
HISTORY_ARCHIVE = JOURNAL_DIR / "history.archive.jsonl"   # messages that no longer fit in history.json: moved here, never deleted
TASKS_ARCHIVE = JOURNAL_DIR / "tasks.archive.jsonl"       # the same for tasks that no longer fit in tasks.json
LINKS_FILE = JOURNAL_DIR / "links.jsonl"                   # links between records that can't be recomputed (the owner's tags); the registry's table is rebuilt from it
FILES_FILE = JOURNAL_DIR / "files.jsonl"                   # every file Sena received, downloaded or made: what it is, where from, for which task
VAULT_SYNC_STATE = STATE_DIR / "vault_sync.json"           # the owner's own lines in the vault cards as last seen: new ones are absorbed, vanished ones retired
SECRETS_FILE = STATE_DIR / "secrets.enc"                   # the owner's passwords and codes, encrypted (core/secrets.py); copied by backups
SECRETS_KEY = STATE_DIR / "secrets.key"                    # the key to them: only on this Mac, never in a backup
EARS_LOG = JOURNAL_DIR / "convo_log.jsonl"     # raw lines from overheard conversations
OWNER_VOICE_FILE = STATE_DIR / "owner_voice.json"          # the owner's voice fingerprints, learned from the voice messages he sends himself
VOICES_FILE = STATE_DIR / "voices.json"                     # people's voice fingerprints by person id: unversioned, a voice heard again is no change of the card
EXAM_FILE = STATE_DIR / "exam.json"                        # the exam of her mind (sena/exam): every run, its score, what failed, what it cost
EXAM_SNAPSHOT = STATE_DIR / "exam_snapshot"                # personality and skills as they were at the last exam that held: what a failed one rolls back to
SETTINGS_FILE = STATE_DIR / "settings.json"
JOBS_FILE = STATE_DIR / "jobs.json"
JOBS_STATE_FILE = STATE_DIR / "jobs_state.json"
PERSONALITY_STATE_FILE = STATE_DIR / "personality_state.json"
INDEX_DB = STATE_DIR / "index.db"                          # the memory index: entries, fingerprints, a full-text index (SQLite)
EVENTS_DB = JOURNAL_DIR / "events.db"                      # the event journal (SQLite, never cleaned up)
ALERTS_FILE = STATE_DIR / "alerts.json"                    # price alerts (crypto, stocks, currencies)
NEWS_WATCH_FILE = STATE_DIR / "news_watch.json"            # the news watch: watched topics, stories told, the last place asked about
PAGE_WATCH_FILE = STATE_DIR / "page_watch.json"            # pages watched for the owner's goal: the last text read of each (world/pages.py)
SITE_WATCH_FILE = STATE_DIR / "site_watch.json"            # the owner's site watch: what it saw last (world/uptime.py)
USAGE_FILE = STATE_DIR / "usage.json"                      # how many paid calls were made today (daily limits)
PID_FILE = STATE_DIR / "sena.pid"                          # the pid of the running core (written by app.main; the console uses it to see whether Sena is alive)
STOP_FILE = STATE_DIR / "stop_requested"                   # "stop and don't restart": written by the console, read by run.sh
STARTING_FILE = STATE_DIR / "core_starting"                # "the core is starting, the port has been handed off": written by the console, removed by app.main
TELEGRAM_SESSION = STATE_DIR / "tema"          # Telethon appends .session
GOOGLE_CLIENT_FILE = STATE_DIR / "google_client.json"   # the OAuth client file from Google Cloud (downloaded by hand)
GOOGLE_TOKEN_FILE = STATE_DIR / "google_token.json"     # the login token; created by python -m sena.google

# ---------- hearing models ----------
WHISPER_MODEL = MODELS_DIR / "ggml-large-v3-turbo-q5_0.bin"   # large-v3-turbo: far more accurate than small for Russian with English words
WHISPER_SMALL = MODELS_DIR / "ggml-small.bin"             # used only while turbo isn't downloaded
VAD_MODEL = MODELS_DIR / "ggml-silero-v5.1.2.bin"         # tells speech apart from music and noise
WAKE_MODEL = MODELS_DIR / "sena.onnx"                     # the "Sena" wake-word detector (openWakeWord)
TRAINING_DIR = DATA / "training"                          # examples of cloud answers for narrow jobs: to test and teach the home model
READING_DIR = DATA / "reading"                            # notes of a long reading as they come: a failed one resumes, kept a day (agents/reader.py)

APP_PORT = 8765
VOICE_PORT = 8767   # the live voice (WebSocket): the apps stream the microphone here and get her voice back (phone/voice.py)

# ---------- Claude API for Sena herself (code is written only through the subscription; Claude Code and the terminal never get these keys) ----------
CLAUDE_KEYS = {"brain": _env("SENA_CLAUDE_BRAIN_KEY"),     # Console: sena-brain — the main brain (MODEL_MAIN)
               "agents": _env("SENA_CLAUDE_AGENTS_KEY")}   # Console: sena-agents — helpers (MODEL_FAST, MODEL_VOICE); without it they use the brain's key

# ---------- GitHub (the owner's fine-grained token; without it GitHub tools report there's no token) ----------
GITHUB_TOKEN = _env("SENA_GITHUB_TOKEN")
GITHUB_API = "https://api.github.com"   # replaced in tests with the address of a fake server on the same Mac

# ---------- Tema's Hire-flow (job search, letters): the private Agent API /agent/v1 ----------
HIREFLOW_URL = _env("HIREFLOW_URL")     # Hire-flow's address: http://127.0.0.1:8000 (this Mac) or https://… (optional)
HIREFLOW_KEY = _env("HIREFLOW_KEY")     # the API key from `python -m app.cli api-key create` in the Hire-flow folder (optional)

# ---------- Tema's portfolio site (the site's private API /api/sena/v1) ----------
SITE_PUBLIC = "" if _env("SITE_PUBLIC_URL") == "off" else _env("SITE_PUBLIC_URL")   # the portfolio site's public address: Sena checks that it's reachable (empty or off — don't check)
SITE_URL = _env("SITE_URL")             # the site's address, https://… (optional; without it the site's tools report there's no address)
SITE_KEY = _env("SENA_SITE_KEY")        # the site's API key: the same one set in the site's own environment (optional)

# ---------- open sources that ask to be introduced (world/research.py, world/sec.py) ----------
OPENALEX_KEY = _env("OPENALEX_KEY")   # optional: without a key OpenAlex gives a shared daily budget per address; once it's used up Sena falls back to Crossref
SEC_CONTACT = _env("SEC_CONTACT")       # "First Last email@example.com": SEC EDGAR requires identifying yourself on every request, and blocks you without it (optional)

# ---------- Luma: Tema's personal app (to-dos, calendar, habits, expenses); Sena works with it through its tool API ----------
LUMA_URL = _env("LUMA_URL") or "http://127.0.0.1:3110"   # the Luma app on this Mac; the default port matches Luma's own
LUMA_PORT_FILE = None if _env("LUMA_URL") else Path.home() / "Library/Application Support/luma-desktop/server.pid"   # Luma writes the port it took here when 3110 is busy; an address in .env wins
LUMA_KEY = _env("LUMA_KEY")                          # Luma's API key (optional): with it, Luma's journal shows that Sena acted, and her kill switches apply
LUMA_APP = Path(_env("LUMA_APP") or DESKTOP / "Luma/desktop/dist/mac-arm64/Luma.app")   # what to open Luma with if it isn't running

# ---------- this Mac: the agent's browser, the Keychain, the copy that leaves the Mac ----------
BROWSER_HEADLESS = bool(_env("SENA_HEADLESS"))   # a windowless browser: for tests
KEYCHAIN = _env("SENA_KEYCHAIN") == "on" or (_env("SENA_KEYCHAIN") != "off" and REAL_DATA)   # the secrets and copy keys (core/keychain.py)
OFFSITE_DIR = Path(_env("SENA_OFFSITE") or (Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Sena" if REAL_DATA else "off"))   # iCloud Drive: the nightly encrypted copy (core/offsite.py); "off" — none

MCP_FILE = STATE_DIR / "mcp.json"                         # the owner's MCP servers, his approvals and pinned tools (world/mcp.py)
RECORDS_DB = STATE_DIR / "sena.db"                         # the one store of records (core/store.py): the paths below live here, not as files
RECORDS = (PEOPLE_FILE, RULES_FILE, GOALS_FILE, WORKS_FILE, TASKS_FILE, TASKS_ARCHIVE, HISTORY_FILE, SUMMARY_FILE, HISTORY_ARCHIVE, EARS_LOG, FILES_FILE, LINKS_FILE,
           JOBS_FILE, JOBS_STATE_FILE, ALERTS_FILE, NEWS_WATCH_FILE, PAGE_WATCH_FILE, OWNER_VOICE_FILE, VOICES_FILE, EXAM_FILE, MEMORY_DIR / "agents")
# Records are what Sena builds up and nobody edits by hand: people, rules, goals, tasks, conversations, overheard speech, files, links,
# jobs, price alerts, the owner's voice, the exam, the agents' lessons. One database gives them one transaction, one consistent
# copy at night and one integrity check. What the owner reads and edits by hand (memory, personality, skills, notes) stays files.

# Whatever a Sena process makes (a file sent to her, a download, a picture she made, a new month's journal) is closed to other
# users of the Mac from the moment it exists: run.sh closes data/ only when it starts. A program that works in the owner's
# own folders (the terminal agent's commands, Claude Code) gets his usual mask back: OWNER_UMASK. It is the first Sena process's
# mask, carried to her own children in SENA_OWNER_UMASK (not a key): the core the screen starts inherits 077 itself and took that
# for his — files Claude Code made in his projects came out 0600
_inherited = os.umask(0o077)
try:
    OWNER_UMASK = int(os.environ.get("SENA_OWNER_UMASK") or _inherited) & 0o777
except ValueError:
    OWNER_UMASK = _inherited
os.environ["SENA_OWNER_UMASK"] = str(OWNER_UMASK)
for _dir in (MEMORY_DIR, JOURNAL_DIR, STATE_DIR, BACKUPS_DIR, LOGS_DIR, SKILLS_DIR, DOWNLOADS_DIR, INBOX_DIR, GENERATED_DIR, SPILL_DIR):
    _dir.mkdir(parents=True, exist_ok=True)


def version():
    """The code Sena runs now: the short commit id, read from .git without running git (stamped on tasks and exam runs, so
    quality can be compared before and after a change — Langfuse ties every trace to a release)."""
    try:
        head = (ROOT / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head[:8]
        ref = head[5:]
        path = ROOT / ".git" / ref
        if path.exists():
            return path.read_text(encoding="utf-8").strip()[:8]
        for line in (ROOT / ".git" / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return line.split()[0][:8]
    except OSError:
        pass
    return ""


def check_token():
    """The app server listens on the network: without a password it lets no one in, so it doesn't even start. Also needed for the screen without the core."""
    if not APP_TOKEN:
        raise SystemExit("В .env не задан APP_TOKEN: без него сервер приложения закрыт для всех (образец — .env.example)")


def check():
    """On startup: check that all keys are in place."""
    missing = [k for k in REQUIRED_KEYS if not has(k)]
    if missing:
        raise SystemExit("В .env не хватает: " + ", ".join(missing) + " (образец — .env.example)")
