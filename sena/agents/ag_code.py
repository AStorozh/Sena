"""ag_code — code agent: Claude Code (subscription) works inside the project folder on the desktop.
Its own code (the Sena folder) Sena edits only through a safe loop: a separate branch and copy (git worktree), Claude works there,
all tests run, and Tema gets a report listing the changes. The fix is applied only on his word (apply_change):
the branch merges into the working code, Sena restarts; if it doesn't come back up within two minutes, run.sh rolls it back to the previous
commit itself and writes to Tema. This way one typo doesn't turn the bot into a process that keeps crashing in a loop.

A run streams what Claude Code does (stream-json): the owner sees "правит agent.py", "запускает тесты", its plan; a stop reaches
it at once; a run that went silent is stopped. It must be on the subscription (its first frame says how it logged in — anything
else is stopped), sees no outside services (the owner's connected mail and the like are not its tools: --strict-mcp-config), and
can't push or commit. Every run has a session id: failing tests get a repair round in the same session, with its memory of
what it changed. How deep a job goes (fast, normal, deep) picks the model and its effort. All of it is Claude Code's own
command line, studied in the Claude Agent SDK and OpenClaw's Claude Code backend (both MIT): no SDK, no new dependency."""
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from sena import config
from sena.core import ask_owner, cancel, confirm, costs, events, power, safety, seatbelt, settings, status, store
from sena.core.tools import Failure, string, tool
from sena.memory import lessons
from sena.world import luma

CHANGES_FILE = config.STATE_DIR / "self_changes.json"   # proposed fixes to its own code: branch, tests, what was changed
PENDING_FILE = config.STATE_DIR / "update_pending"      # "applied, waiting to see if it sticks": previous commit and id (run.sh reads it)
GOOD_FILE = config.STATE_DIR / "update_good"            # the last commit Sena definitely worked on
WORK_DIR = config.SELF_COPIES                           # copies for fixes (outside the repository, see config)
GRACE = 120                                             # seconds after startup to consider an update stuck
STARTED = time.time()                                   # when this run began (app.py imports the agents first thing)
APPLY_HOLD = 36 * 3600                                  # an apply prepared for his "да" waits this long (he may read the report in an hour)
CHILDREN_FILE = config.STATE_DIR / "claude_runs.json"   # the Claude Code runs Sena started: stopped with her, even after a crash
LOCK_AGE = 600                                          # a git lock this old, with no git running anywhere, was left by a killed command
_lock = threading.Lock()
_children = {}                                          # pid -> when that Claude Code run started
_busy = {}                                              # project folder -> the task Claude Code is doing in it right now
# what makes a desktop folder a code project (photos, chat exports and empty "Новая папка" are not)
MARKERS = (".git", "package.json", "pyproject.toml", "requirements.txt", "setup.py", "Package.swift", "build.gradle.kts", "build.gradle",
           "settings.gradle.kts", "Cargo.toml", "go.mod", "Makefile", "CMakeLists.txt", "index.html", "CLAUDE.md", "composer.json", "Gemfile")


def _code_folder(d):
    return d.is_dir() and not d.name.startswith(".") and any((d / m).exists() for m in MARKERS) and not _is_vault(d)


def _is_vault(path):
    # Sena's own notes: Claude Code with every permission there would rewrite lines vault_sync takes into her memory
    try:
        return config.VAULT_DIR.exists() and Path(path).resolve() == config.VAULT_DIR.resolve()
    except OSError:
        return False


def list_projects():
    """The code folders on the desktop Claude Code can work in. The owner's projects themselves, with their status, are in Luma."""
    return "\n".join(sorted(d.name for d in config.DESKTOP.iterdir() if _code_folder(d)))


def folder(project):
    """The project's folder directly on the desktop in the folder's own spelling (the disk ignores case; the snapshots and the
    allowed settings are kept by the name), or None."""
    name = (project or "").strip()
    if not name or "/" in name or name in (".", ".."):
        return None
    if (config.DESKTOP / name).is_dir():
        return next((d for d in config.DESKTOP.iterdir() if d.name.lower() == name.lower() and d.is_dir()), config.DESKTOP / name)
    return None


# how deep a coding job goes: (model, effort); None — the owner's own Claude Code default. "deep" takes Opus 5.5 once Claude Code
# knows it (version 2.1.280 and later), until then the owner's default model at the highest effort
LEVELS = {"fast": ("claude-sonnet-5-5", "medium"), "normal": (None, None), "deep": (None, "xhigh")}
DEEP_MODEL, DEEP_SINCE = "claude-opus-5-5", (2, 1, 280)
FALLBACK = "claude-sonnet-5-5"   # the main model overloaded: Claude Code switches to this one by itself
NEVER = ("Bash(git push:*)", "Bash(git commit:*)", "Bash(git reset --hard:*)")   # denied inside Claude Code whatever the task says
READ_ONLY = ("Edit", "Write", "MultiEdit", "NotebookEdit", "Bash")                 # a reviewer's run: it reads, it doesn't touch
# what a run may use, named: with the scrubbed environment (CLAUDE_CODE_SUBPROCESS_ENV_SCRUB) Claude Code forces bypassPermissions back
# to the default mode unless the tools are listed — then every Write and Edit waited for a button nobody would press, and
# self-development stood for hours (its stderr said so in every run; checked 02.11.2026: without the list nothing is written,
# with it both files are, and NEVER still refuses a commit)
ALLOWED = ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "Glob", "Grep", "LS", "TodoWrite")
# Sena's fixed rules for every run, in Claude Code's system prompt: they outlive the compaction of a long run, which summarises the
# task text, and don't depend on the coordinator repeating them in each task (Claude Code's context docs; the ECC study)
RULES = """Ты работаешь по поручению Сены, ассистента Темы; человека рядом нет.
- У тебя уже полное разрешение писать и править файлы в этой папке (--permission-mode bypassPermissions), без диалогов и кнопок:
  никто не смотрит на экран и не нажмёт «Разрешить». Если кажется, что Write/Edit отказывает, — это настоящая ошибка, опиши её
  в «ВОПРОС ТЕМЕ» как техническую, а не проси подтвердить доступ в интерфейсе: такого интерфейса здесь нет и никогда не будет.
- Работай только в этой папке. Не коммить, не пушь, не делай stash, не создавай и не удаляй ветки, не меняй настройки и хуки git.
- Пакеты и программы ставь, только если задача прямо об этом. Не удаляй ничего сверх задачи.
- Значения ключей, паролей и токенов (.env и подобное) не печатай и не переписывай в другие файлы.
- Не трогай .claude/, .mcp.json, .vscode/tasks.json, .envrc и хуки git: это исполняемые настройки.
- Проверка или тест не проходят — чини код, а не тест.
- Правильный результат зависит от факта, которого нет ни в задаче, ни в проекте (выбор Темы, его данные), — не угадывай: до правок
  остановись и закончи ответ строкой «ВОПРОС ТЕМЕ: …» (один короткий вопрос). Сена спросит его и продолжит эту же сессию с ответом.
- Чего-то не сделал или не проверил — так и напиши."""
QUESTION = re.compile(r"ВОПРОС ТЕМЕ:\s*(.+)", re.S)
QUESTIONS = 2         # questions one job may ask the owner
QUESTION_WAIT = 600   # seconds Sena waits for his answer; then the run goes on with its best guess, named
# no telemetry or update checks, no background commands that keep -p from ending, the shell it starts gets a scrubbed environment
# (variables from Anthropic's own docs; an older CLI ignores the ones it doesn't know)
QUIET_CLAUDE = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
                "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",
                # the owner's own Claude Code keeps notes per repository, shared by its worktrees: they are his assistant's, not
                # Sena's runs' — one said "restart without asking" against CLAUDE.md (the awesome-claude-code study)
                "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
STALL = 900                      # seconds without a single line from Claude Code: it hangs, the run is stopped
LINGER = 60                      # seconds a run may stay after its result (a background command it left keeps -p alive: 2.1.163)
LIMIT = 5400                     # the longest one run may last
QUOTA_FILE = config.STATE_DIR / "claude_quota.json"   # the subscription's window as Claude Code last reported it
CLAUDE_LOGS = config.LOGS_DIR / "claude"
_version = None
_run_local = threading.local()   # .bare — this run loads none of the project's own Claude Code settings (Tema chose so)


def version():
    """Claude Code's version as numbers, (0,) if unknown; asked once per process."""
    global _version
    if _version is None:
        try:
            out = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=10).stdout
            _version = tuple(int(x) for x in re.match(r"\s*(\d+)\.(\d+)\.(\d+)", out).groups())
        except (OSError, subprocess.SubprocessError, AttributeError, ValueError):
            _version = (0,)
    return _version


def _level(level):
    model, effort = LEVELS.get(level, LEVELS["normal"])
    if level == "deep" and version() >= DEEP_SINCE:
        model = DEEP_MODEL
    return model, effort


def _plain_task(task):
    # Claude Code reads "@/path" as "insert that file here" and a leading "/" as its own command: a task Sena composes (maybe
    # from someone else's text) is taken as words only
    task = re.sub(r"(^|\s)@(?=[/~.])", r"\1", str(task))
    return ("Задача: " + task) if task.lstrip().startswith("/") else task


def quota():
    """The subscription window as Claude Code last reported it: {status, resetsAt, rateLimitType, ...} or {}."""
    return store.load_json(QUOTA_FILE, {}) or {}


def _quota_closed():
    q = quota()
    if q.get("status") == "rejected" and (q.get("resetsAt") or 0) > time.time():
        return f"лимит подписки Claude Code исчерпан до {datetime.fromtimestamp(q['resetsAt']):%d.%m %H:%M}"
    return None


def _step(block):
    """What Claude Code is doing, in words for the owner: one tool call of its."""
    name, args = block.get("name", ""), block.get("input") or {}
    path = args.get("file_path") or args.get("notebook_path") or args.get("path") or ""
    short = Path(str(path)).name if path else ""
    if name in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        return f"правит {short}"
    if name == "Read":
        return f"читает {short}"
    if name == "Bash":
        return f"запускает: {(args.get('description') or args.get('command') or '')[:70]}"
    if name in ("Grep", "Glob"):
        return f"ищет «{str(args.get('pattern') or '')[:40]}»"
    if name == "TodoWrite":
        todos = args.get("todos") or []
        doing = next((t.get("activeForm") or t.get("content") or "" for t in todos if t.get("status") == "in_progress"), "")
        return f"план: сделано {sum(t.get('status') == 'completed' for t in todos)} из {len(todos)}" + (f", сейчас: {doing[:60]}" if doing else "")
    if name in ("Task", "Agent"):
        return f"поручил помощнику: {str(args.get('description') or '')[:60]}"
    if name in ("WebFetch", "WebSearch"):
        return "смотрит в интернете"
    return name


def _names(items):
    # plugins, skills or agents as the init frame lists them: plain names or {"name", "path"}
    return [i if isinstance(i, str) else str((i or {}).get("name") or (i or {}).get("path") or i) for i in items or []]


def _failure(result, stopped):
    # why a run didn't succeed, in words: our own stop first, then what Claude Code itself reported
    if stopped:
        return stopped
    if not result:
        return "Claude Code завершился без отчёта"
    if not result.get("is_error") and result.get("subtype") == "success":
        return ""
    why = "; ".join(str(e) for e in result.get("errors") or []) or str(result.get("result") or "") or result.get("subtype") or ""
    code = result.get("api_error_status")
    return (why or "ошибка") + (f" (HTTP {code})" if code else "")


def _command(session, resume, level, readonly):
    """Claude Code's command line for one run: print mode with its stream, every permission (in a folder Sena chose), no
    outside services, the fixed rules, and what this run may never do."""
    model, effort = _level(level)
    args = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "bypassPermissions", "--strict-mcp-config"]
    args += ["--model", model] if model else []
    args += ["--effort", effort] if effort else []
    args += ["--fallback-model", FALLBACK] if model != FALLBACK else []
    args += [f"--resume={session}"] if resume else [f"--session-id={session}"]
    args += ["--allowedTools", *(t for t in ALLOWED if not (readonly and t in READ_ONLY))]
    args += ["--disallowedTools", *NEVER, *(READ_ONLY if readonly else ()), "--append-system-prompt", RULES]
    args += ["--setting-sources", "user"] if getattr(_run_local, "bare", False) else []
    return args


class _Stream:
    """What one run's stream has said so far: its steps (shown to the owner as they come), the commands it ran, its result,
    and why it was stopped, if it was."""

    def __init__(self, proc, who):
        self.proc, self.who = proc, who
        self.last, self.done_at, self.stopped = time.monotonic(), None, ""
        self.result, self.shown, self.steps, self.commands = None, ("", 0.0), 0, []

    def line(self, line):
        self.last = time.monotonic()
        if not line.startswith("{"):
            return   # some builds print debug lines on stdout
        try:
            frame = json.loads(line)
        except ValueError:
            return
        kind = frame.get("type")
        if kind == "system" and frame.get("subtype") == "init":
            self._init(frame)
        elif kind == "assistant":
            for block in (frame.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    self._tool(block)
        elif kind == "rate_limit_event":
            store.save_json(QUOTA_FILE, {**(frame.get("rate_limit_info") or {}), "seen": time.time()}, indent=1)
        elif kind == "result":
            self.result = frame   # the last one counts: a finished helper can wake it for one more turn
            self.done_at = time.monotonic()

    def _init(self, frame):
        loaded = {k: _names(frame.get(k)) for k in ("plugins", "skills", "agents") if frame.get(k)}
        if loaded:   # what Claude Code brought into this run besides Sena's task: the owner's plugins, skills, agents
            events.log("code", "Claude Code загрузил: " + "; ".join(f"{k}: {', '.join(v[:10])}" for k, v in loaded.items()),
                       place="ag_code", **{k: v[:30] for k, v in loaded.items()})
        source = frame.get("apiKeySource")
        if source not in ("none", None) or frame.get("mcp_servers"):
            self.stopped = (f"Claude Code вошёл не по подписке ({source})" if source not in ("none", None)
                            else "Claude Code видит внешние сервисы: " + ", ".join(str(s.get("name")) for s in frame["mcp_servers"]))
            events.log("error", self.stopped, place="ag_code")
            _stop_group(self.proc.pid)

    def _tool(self, block):
        self.steps += 1
        if block.get("name") == "Bash":
            self.commands.append(str((block.get("input") or {}).get("command") or ""))
        text = _step(block)
        if text != self.shown[0] and (time.time() - self.shown[1] >= 2 or text.startswith("план")):
            status.show(text, who=self.who)
            self.shown = (text, time.time())


def _watch(stream, over, mine, awake):
    """The watchdog of one run: a run that went silent, ran too long or was stopped by the owner is stopped with whatever it
    started; one that lingers after its result (a command left in the background) is closed and its result stands. over —
    the run is over: it leaves at once, never acting on a pid that may be someone else's by now."""
    proc = stream.proc
    while not over.wait(2):
        if proc.poll() is not None:
            return
        if stream.done_at:
            if time.monotonic() - stream.done_at > LINGER:
                _stop_group(proc.pid)
                return
            continue
        why = ("остановлено по просьбе Темы" if mine in cancel.cancelled else
               f"Claude Code молчал {STALL // 60} мин" if time.monotonic() - stream.last > STALL else
               f"Claude Code работал дольше {LIMIT // 60} мин" if time.monotonic() - awake > LIMIT else "")
        if why and not stream.stopped:
            stream.stopped = why
            _stop_group(proc.pid)


def _claude(path, task, purpose, level="normal", resume=None, readonly=False):
    """One Claude Code run in the folder: {"text", "ok", "session", "turns", "seconds", "why"}. resume — a session to continue
    (a repair round keeps its memory of what it changed); readonly — it may only read files (a reviewer). Its own process group,
    remembered on disk: stopping Sena stops this run and whatever it started, and nothing else. Keys are stripped from its
    environment: subscription only."""
    if power.is_off():
        return {"text": power.refusal("Claude Code"), "ok": False, "session": None, "turns": 0, "seconds": 0, "why": "Сена выключена"}
    closed = _quota_closed()
    if closed:
        return {"text": f"Не запускаю Claude Code: {closed}. Поставлю, когда лимит откроется, если скажешь.", "ok": False, "session": None,
                "turns": 0, "seconds": 0, "why": closed}
    started, awake, session = time.time(), time.monotonic(), resume or str(uuid.uuid4())   # awake: the clock that stops while the Mac sleeps
    env = {**safety.clean_env(("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE")), **QUIET_CLAUDE}   # not a nested Claude Code: its own session
    CLAUDE_LOGS.mkdir(parents=True, exist_ok=True)
    who = status.local.who if str(getattr(status.local, "who", "") or "").startswith("ag_code") else "ag_code"
    mine = cancel.current_task()   # the owner's "stop" for this task reaches the run from the watchdog's thread
    with open(CLAUDE_LOGS / f"{session}.err", "a", encoding="utf-8") as err:
        proc = subprocess.Popen(_command(session, resume, level, readonly), cwd=path, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=err, text=True, start_new_session=True, umask=config.OWNER_UMASK)   # files it makes in his project get his usual mode
        _remember(proc)
        stream, over = _Stream(proc, who), threading.Event()
        threading.Thread(target=_watch, args=(stream, over, mine, awake), daemon=True).start()
        try:
            proc.stdin.write(_plain_task(task))
            proc.stdin.close()
            for line in proc.stdout:
                stream.line(line)
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            _stop_group(proc.pid)
        finally:
            over.set()
            _forget(proc.pid)
            costs.subscription("claude-code", time.time() - started, purpose=purpose, turns=(stream.result or {}).get("num_turns"))   # no money, the calls are counted
    result = stream.result or {}
    why = _failure(stream.result, stream.stopped)
    try:   # Claude Code dropped the permissions it was given: said in the error log, not in the stream — never silent again
        forced = "Permission mode forced" in (CLAUDE_LOGS / f"{session}.err").read_text(encoding="utf-8", errors="replace")
    except OSError:
        forced = False
    if forced:
        events.error("Claude Code без права записи", RuntimeError("Claude Code сбросил режим разрешений: правки не запишутся (см. ALLOWED в ag_code)"))
    text = str(result.get("result") or "").strip()
    if why:
        text = f"Claude Code не закончил: {why}." + (f"\nЧто успел: {text}" if text and text != why else "")
    seconds = round(time.time() - started, 1)
    events.log("model", text, model="claude-code", input=task, project=str(path), seconds=seconds, failed=bool(why) or None,
               session=session, turns=result.get("num_turns"), steps=stream.steps, level=level)
    return {"text": text or "Claude Code ничего не ответил", "ok": not why, "session": session, "turns": result.get("num_turns") or 0,
            "seconds": seconds, "why": why, "commands": stream.commands}


# what counts as Claude Code's own check of its work: a test or a build, one plain command (no chains, pipes or redirects)
CHECKS = re.compile(r"^\s*(?:\S+/)?(?:python3?(?:\.\d+)? -m (?:unittest|pytest)|pytest|npm (?:test|run (?:test|build|lint|typecheck))|"
                    r"(?:yarn|pnpm|bun) (?:test|build|run (?:test|build))|go (?:test|build|vet)|cargo (?:test|build|check)|make(?: (?:test|check|build))?|"
                    r"\./gradlew [\w:-]+|swift (?:test|build)|mvn (?:test|package|verify)|tsc(?: --noEmit)?)(?:\s[^;&|<>`$]*)?$")


def _own_check(done):
    """The last test or build command Claude Code ran itself, or "" — to be run again by Sena when no check was named."""
    return next((c.strip() for c in reversed(done.get("commands") or []) if CHECKS.match(c)), "")


_UI_CONFUSION = re.compile(r"интерфейс|кнопк|нажм|подтверд", re.I)   # it thinks a human must click something — there is no screen here


def _asked(path, done, purpose, level, where):
    """Claude Code stopped at a fact only Tema has («ВОПРОС ТЕМЕ: …»): Sena asks him, and the same session goes on with his
    answer — or, when none comes, with its best guess, named. In -p it can't ask by itself, and in ECC's measured runs no
    attempt passed without the deciding fact. A question about clicking a permission button isn't a fact Tema has — it's
    Claude Code mistaken about its own (already full) access, so it's corrected right away instead of paged to Tema."""
    for _ in range(QUESTIONS):
        found = QUESTION.search(done.get("text") or "") if done.get("ok") and done.get("session") else None
        if not found or cancel.is_cancelled():
            return done
        question = found.group(1).strip()[:500]
        if _UI_CONFUSION.search(question):
            reply = ("Здесь нет интерфейса и некого нажать кнопку — у тебя уже полное разрешение на запись и правку (bypassPermissions). "
                     "Если Write/Edit правда вернул ошибку, опиши её как техническую и продолжай; иначе просто продолжай правку.")
        else:
            status.show("Claude Code спрашивает Тему", who=getattr(status.local, "who", None) or "ag_code", tool="ask_tema")
            heard = str(ask_owner.ask(f"Claude Code ({where}) спрашивает: {question}", timeout=QUESTION_WAIT))
            reply = (f"{heard}. Продолжай задачу с этим." if heard.startswith("Тема ответил") else
                     f"Ответа нет ({heard}). Продолжай по лучшей догадке и в ответе прямо назови, что предположил.")
        done = _claude(path, reply, purpose + ", ответ Темы", level, resume=done["session"])
    return done


def _started(pid):
    # when a process started, as the system tells it: a pid can be reused, a pid with its start time can't
    try:
        return subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _remember(proc):
    started = _started(proc.pid)
    with _lock:
        _children[proc.pid] = started
        store.save_json(CHILDREN_FILE, {str(pid): when for pid, when in _children.items()})


def _forget(pid):
    with _lock:
        _children.pop(pid, None)
        store.save_json(CHILDREN_FILE, {str(p): when for p, when in _children.items()})


def _ours(pid, started):
    # still that very run of Sena's: the same pid and the same start time — the owner's own Claude Code can never match
    return bool(started) and _started(pid) == started


def _stop_group(pid):
    # SIGINT first: Claude Code closes its turn on it (its headless docs), then SIGTERM, then SIGKILL — the whole group each time
    try:
        for sig, wait in ((signal.SIGINT, 20), (signal.SIGTERM, 30)):
            os.killpg(pid, sig)
            for _ in range(wait):
                time.sleep(0.1)
                os.killpg(pid, 0)
        os.killpg(pid, signal.SIGKILL)   # didn't finish in five seconds
    except (OSError, ProcessLookupError):
        pass   # gone


def stop_children():
    """Stop Sena's Claude Code runs: this run's and the ones a killed run left behind (their pids and start times were kept on
    disk). A pid is stopped only while it still is that very run — the owner's own Claude Code in VS Code or a terminal is never
    touched. Returns how many were stopped."""
    with _lock:
        saved = store.load_json(CHILDREN_FILE, {}) or {}
        runs = {**{int(pid): when for pid, when in (saved.items() if isinstance(saved, dict) else [])}, **_children}
    stopped = 0
    for pid, started in runs.items():
        if _ours(pid, started):
            _stop_group(pid)
            stopped += 1
    with _lock:
        _children.clear()
        CHILDREN_FILE.unlink(missing_ok=True)
    return stopped


def clear_stale_lock():
    """A git lock left by a git command killed half way (Sena stopped while applying a fix) blocks every other program in the
    repository, Claude Code in VS Code included. It is removed only when it is old and no git is running anywhere on the Mac."""
    lock = config.ROOT / ".git" / "index.lock"
    try:
        if time.time() - lock.stat().st_mtime < LOCK_AGE:
            return False
    except OSError:
        return False
    if subprocess.run(["pgrep", "-x", "git"], capture_output=True).returncode == 0:   # someone's git is at work: not ours to touch
        return False
    lock.unlink(missing_ok=True)
    events.log("cleanup", "снят зависший замок git: .git/index.lock", path=str(lock))
    return True


# ---------- snapshots of the owner's projects: how each was before a coding run, and the way back (Cline's checkpoints) ----------
SNAPSHOTS = config.STATE_DIR / "snapshots"       # one shadow git repository per project, beside it — the project's own .git is never touched
SNAPSHOT_RUNS = config.STATE_DIR / "snapshots.json"   # {project: [{before, after, task, t}]}: the runs that can be undone
SNAPSHOT_FILES = 20000                            # a tree bigger than this (a dataset, a build) isn't copied: the report says so
SNAPSHOT_FILE_MB = 50                             # nor one with a file this big (a video): git would keep a second copy of it
SNAPSHOT_TOTAL_MB = 500                           # nor a tree heavier than this in all
SKIP = "\n".join((".git/", "node_modules/", ".venv/", "venv/", "__pycache__/", "*.pyc", ".gradle/", ".next/", ".DS_Store")) + "\n"
SHADOW_ID = ("-c", "user.name=Sena", "-c", "user.email=sena@localhost", "-c", "commit.gpgsign=false", "-c", "core.quotePath=false")


def _shadow(project, path, *args, check=True):
    return _git([*SHADOW_ID, "--git-dir", str(SNAPSHOTS / f"{project}.git"), "--work-tree", str(path), *args], cwd=path, check=check)


def snapshot(project, path, why):
    """The project's files as they are now, kept in its shadow repository: (commit, None), or (None, why it wasn't taken).
    Claude Code works in the owner's folder with every permission: before it starts, what was there is kept, so a run that
    broke or deleted something can be taken back whole — the project's own git, if any, knows nothing of it."""
    git_dir = SNAPSHOTS / f"{project}.git"
    try:
        if not git_dir.exists():
            git_dir.parent.mkdir(parents=True, exist_ok=True)
            _git(["init", "--quiet", "--bare", str(git_dir)])
            (git_dir / "info").mkdir(exist_ok=True)
            (git_dir / "info" / "exclude").write_text(SKIP, encoding="utf-8")
        listed = [f for f in _shadow(project, path, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0") if f]
        if len(listed) > SNAPSHOT_FILES:
            return None, f"в проекте больше {SNAPSHOT_FILES} файлов"
        sizes = {f: (path / f).stat().st_size for f in listed if (path / f).is_file()}
        big = next((f for f, size in sizes.items() if size > SNAPSHOT_FILE_MB * 2 ** 20), None)
        if big:
            return None, f"файл {big} больше {SNAPSHOT_FILE_MB} МБ"
        if sum(sizes.values()) > SNAPSHOT_TOTAL_MB * 2 ** 20:
            return None, f"файлы проекта тяжелее {SNAPSHOT_TOTAL_MB} МБ"
        _shadow(project, path, "add", "-A", "--", ".")
        _shadow(project, path, "commit", "--quiet", "--allow-empty", "--no-verify", "-m", why[:200])
        return _shadow(project, path, "rev-parse", "HEAD"), None
    except Exception as e:   # a snapshot that can't be taken never stops the work itself: the report says there is none
        events.error(f"Снимок проекта {project}", e)
        return None, str(e)[:200]


def _between(project, path, a, b):
    # {"M": [...], "A": [...], "D": [...]}: what changed from snapshot a to snapshot b
    out, parts = {"M": [], "A": [], "D": []}, _shadow(project, path, "diff", "--name-status", "--no-renames", "-z", a, b).split("\0")
    for code, name in zip(parts[::2], parts[1::2]):
        out.setdefault(code[:1], []).append(name)
    return out


def _listed(changed):
    words = (("M", "изменено"), ("A", "новых"), ("D", "удалено"))
    names = [n for code, _ in words for n in changed.get(code, [])]
    return ", ".join(f"{w} {len(changed[c])}" for c, w in words if changed.get(c)) + (f" ({', '.join(names[:8])}{'…' if len(names) > 8 else ''})" if names else "")


def undo_plan(project):
    """(what going back would do, the action) to return a project to how it was before its last coding run; (Failure, None)
    when there is nothing to go back to. The present is kept as one more snapshot first, so going back can be undone too —
    taken again at the owner's "yes": what was edited between the question and the answer is kept as well. Never while Claude
    Code works in the project: the snapshot would race its run's, and the checkout would rewrite files under it."""
    path = folder(project)
    project = path.name if path else project
    runs = store.load_json(SNAPSHOT_RUNS, {}).get(project) or []
    if not runs or path is None:
        return Failure(f"Снимков проекта «{project}» нет: Claude Code в нём ещё не работал"), None
    doing = _busy.get(str(path.resolve()))
    if doing is not None:
        return Failure(f"В проекте «{project}» сейчас работает Claude Code (задача «{doing[:80]}»): вернуть можно, когда он закончит"), None
    last = runs[-1]
    now, why = snapshot(project, path, "перед возвратом")
    if not now:
        return Failure(f"Снимок перед возвратом не снять: {why}"), None
    changed = _between(project, path, last["before"], now)
    if not any(changed.values()):
        return f"Проект «{project}» и так как до задачи «{last['task'][:80]}»", None

    def go():
        doing = _take(path, f"возврат к виду до задачи «{last['task'][:60]}»")   # no run starts while the files go back
        if doing is not None:
            return Failure(f"Не вернула: в проекте «{project}» сейчас работает Claude Code (задача «{doing[:80]}»). Когда он закончит — спроси снова")
        try:
            if (store.load_json(SNAPSHOT_RUNS, {}).get(project) or [])[-1:] != [last]:   # he was asked about another last run
                return Failure(f"Не вернула: после вопроса Claude Code ещё раз поработал в проекте «{project}» — спроси о возврате заново")
            kept, why = snapshot(project, path, "перед возвратом (на «да»)")
            if not kept:
                return Failure(f"Не вернула: снимок того, что сейчас, не снять ({why}) — без него возврат не отменить")
            gone = _between(project, path, last["before"], kept)
            _shadow(project, path, "checkout", last["before"], "--", ".")   # every file as it was; the ones made since are taken away
            for name in gone.get("A", []):
                (path / name).unlink(missing_ok=True)
        finally:
            _free(path)
        events.log("code", f"проект {project} возвращён к виду до задачи «{last['task'][:80]}»", project=project, snapshot=last["before"][:12], kept=kept[:12])
        return (f"Проект «{project}» снова как до задачи «{last['task'][:80]}»: {_listed(gone)}. То, что было перед возвратом, сохранено "
                f"в снимке {kept[:8]}: инструмента вернуть его нет, только вручную.")
    return f"Вернуть «{project}» к виду до задачи «{last['task'][:80]}»: {_listed(changed)}", go


SURFACE_FILE = config.STATE_DIR / "claude_surface.json"   # {project: digest}: the project's own Claude Code settings Tema allowed
SURFACE_KEYS = ("hooks", "env", "apiKeyHelper", "awsAuthRefresh", "awsCredentialExport", "otelHeadersHelper", "statusLine",
                "enabledPlugins", "extraKnownMarketplaces")   # what in a settings file runs code or changes where Claude Code connects
NOT_CONFIG = ("__pycache__", ".DS_Store")
SURFACE_SHOWN = 8


def surface(path):
    """What the project's own Claude Code settings would run besides Claude: (digest, names) — names of settings keys, hook
    events and hook files, never values (an env block may hold a key); (None, []) — nothing. In -p a folder counts as trusted:
    its hooks, env and helpers run unsandboxed with Tema's rights, before the first frame Sena checks (Claude Code's permission
    and headless docs; the ECC study found SessionStart and PreToolUse hooks in one of his projects)."""
    root, digest, names = Path(path) / ".claude", hashlib.sha256(), []
    for name in ("settings.json", "settings.local.json"):
        f = root / name
        if not f.is_file():
            continue
        raw = f.read_bytes()
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if not isinstance(data, dict):   # whatever Claude Code makes of a file that isn't readable settings: counted whole
            digest.update(name.encode() + b"\0" + raw)
            names.append(f".claude/{name} (не читается)")
            continue
        found = {k: data[k] for k in SURFACE_KEYS if data.get(k)}
        if found:
            digest.update(name.encode() + b"\0" + json.dumps(found, sort_keys=True, ensure_ascii=False).encode())
            names += [f"хуки {', '.join(sorted(v))}" if k == "hooks" and isinstance(v, dict) else k for k, v in found.items()]
    for f in sorted(root.rglob("*")) if root.is_dir() else []:
        rel = f.relative_to(root)
        if not f.is_file() or any(part in NOT_CONFIG for part in rel.parts) or f.suffix == ".pyc":
            continue
        if rel.parts[0] == "hooks":   # a hook's script: its text is what runs
            digest.update(str(rel).encode() + b"\0" + f.read_bytes())
            names.append(f".claude/{rel}")
        elif f.suffix == ".md":   # skills and agents may carry hooks of their own in the header
            head = f.read_text(encoding="utf-8", errors="replace")[:4000]
            front = head.split("---", 2)[1] if head.startswith("---") and head.count("---") >= 2 else ""
            if re.search(r"^hooks\s*:", front, re.M):
                digest.update(str(rel).encode() + b"\0" + front.encode())
                names.append(f".claude/{rel} (хуки)")
    return (digest.hexdigest()[:32], names) if names else (None, [])


def _gate(project, path, again):
    """None — the run may start; text — it waits: the project's own Claude Code settings are new, or changed since Tema allowed
    them (a hook planted by an earlier run would run at the next start, before any check). He is asked once per version."""
    if getattr(_run_local, "bare", False):
        return None
    digest, names = surface(path)
    if not digest or store.load_json(SURFACE_FILE, {}).get(project) == digest:
        return None
    shown = ", ".join(names[:SURFACE_SHOWN]) + ("…" if len(names) > SURFACE_SHOWN else "")

    def allowed():
        with _lock:
            store.save_json(SURFACE_FILE, {**store.load_json(SURFACE_FILE, {}), project: digest}, indent=1)
        events.log("code", f"Тема разрешил настройки Claude Code в проекте {project}: {shown}", project=project)
        return again()
    sid = confirm.hold(f"Claude Code в {project} с его настройками", shown, allowed)
    return (f"Ещё не поручено [{sid}]: в проекте {project} свои настройки Claude Code — {shown}. Они выполнятся без песочницы, "
            f"с правами Темы. Спроси Тему, запускать ли с ними; после его «да» — confirm_send({sid}): запомню и спрошу снова, только "
            "если они изменятся. Не хочет — поручи заново с project_settings=false: настройки проекта (и его CLAUDE.md) в этом "
            "запуске не загрузятся.")


def _git_view(path):
    """What a run could do to the project's own git behind the rules: HEAD, every branch and remote branch, the stash, the config
    and hooks that make git run code. None — the folder has no repository of its own."""
    git_dir = Path(path) / ".git"
    if not git_dir.is_dir():
        return None
    digest = hashlib.sha256()
    for p in sorted([git_dir / "config", *(git_dir / "hooks").glob("*")]):
        if p.is_file() and not p.name.endswith(".sample"):
            digest.update(p.name.encode() + b"\0" + p.read_bytes())
    refs = _git(["for-each-ref", "--format=%(refname) %(objectname)"], cwd=path, check=False)
    return {"head": _git(["rev-parse", "HEAD"], cwd=path, check=False), "config": digest.hexdigest(),
            "refs": dict(line.split(" ", 1) for line in refs.splitlines() if " " in line),
            "stash": len(_git(["stash", "list"], cwd=path, check=False).splitlines())}


def _git_said(before, after):
    """The project's git after the run, in words; "" — nothing moved. Commits and pushes are forbidden by rule, and a rule is no
    wall (`git -C . commit` passes a deny rule — Claude Code's permission docs, the ECC study): what happened is read afterwards."""
    if not before or not after:
        return ""
    said = []
    if after["head"] != before["head"]:
        said.append(f"сменилась текущая версия ({before['head'][:7]} → {after['head'][:7]}): похоже, был коммит")
    moved = sorted(n for n in set(before["refs"]) | set(after["refs"]) if before["refs"].get(n) != after["refs"].get(n))
    remote = [n.split("/", 2)[2] for n in moved if n.startswith("refs/remotes/")]
    if remote:
        said.append(f"сдвинулись удалённые ветки ({', '.join(remote[:4])}): похоже на push или fetch")
    branches = [n.split("/", 2)[2] for n in moved if n.startswith("refs/heads/") and after["refs"].get(n) != after["head"]]
    if branches:
        said.append(f"изменились ветки: {', '.join(branches[:4])}")
    if after["stash"] != before["stash"]:
        said.append("изменился stash")
    if after["config"] != before["config"]:
        said.append("изменились настройки или хуки git (.git/config, .git/hooks)")
    return ("\nGit проекта: " + "; ".join(said) + ". Скажи об этом Теме прямо.") if said else ""


def _memory_said():
    """Sena's own memory, personality or skills changed while Claude Code worked (it runs unsandboxed): a line for the report."""
    try:
        changed = store.outside_changes()
    except Exception as e:
        events.error("Проверка файлов Сены после Claude Code", e)
        return ""
    if not changed:
        return ""
    events.log("attention", f"файлы изменены не Сеной во время Claude Code: {', '.join(n for n, _ in changed)}", files=[n for n, _ in changed])
    return (f"\nВо время работы Claude Code изменились файлы самой Сены ({', '.join(n for n, _ in changed[:5])}): их правит только она. "
            "Скажи Теме прямо; её прежний текст сохранён в истории файла.")


def run(project, task, level="normal", check="", project_settings=True):
    """A coding job in a project folder. check — a command whose success means the job is done (tests, a build): Sena runs it
    herself and, if it fails, gives Claude Code its output in the same session up to REPAIRS times. One round of such feedback
    nearly doubles success (Aider's measurements); a report alone is Claude's word. project_settings=False — the project's own
    Claude Code settings (hooks, env, its CLAUDE.md) aren't loaded in this run. One run per project at a time: two would edit
    the same files and break each other's snapshot, and undoing one would wipe the other."""
    path = folder(project)
    if path is None:   # only a folder directly on the desktop
        return Failure(f"Папки «{project}» на рабочем столе нет: список — list_projects. Новый проект — сначала папка для него "
                       "(computer_task), потом claude_task в ней")
    if _is_vault(path):
        return Failure("Это заметки самой Сены (Obsidian), а не проект с кодом: их правят note_write и сама Сена, Claude Code туда не пускаю")
    project = path.name
    _run_local.bare = not project_settings
    try:
        if path.samefile(config.ROOT):   # its own code (regardless of name case) — only through the safe loop
            card = _card(project, task)
            result = self_change(task, level, card=card)
            if card and not any(c.get("card") == card and c.get("head") and c.get("tests") for c in changes()):
                _card_moved(card, "todo", task, result)   # nothing ready to apply: back to the backlog, with why
            elif card:
                _card_moved(card, "doing", task, "Правка готова и ждёт твоего «применять».")
            return result
        held = _gate(project, path, lambda: run(project, task, level, check, project_settings))
        if held:
            return held
        doing = _take(path, task)
        if doing is not None:
            return Failure(f"В проекте «{project}» уже работает Claude Code (задача «{doing[:80]}»): дождись её конца и поручи после")
        card = _card(project, task)
        result = "Задача прервана"
        try:
            result = _project_run(project, path, task, level, check)
            return result
        finally:
            _free(path)
            ok = not isinstance(result, Failure) and not str(result).startswith(("Claude Code не закончил", "Задача прервана")) and "НЕ прошла" not in str(result)
            _card_moved(card, "done" if ok else "todo", task, str(result))
    finally:
        _run_local.bare = False


# ---------- the job's card on the project's board in Luma ----------
def _card(project, task):
    """A coding job as a card of its project in Luma, «в работе»: Tema keeps his projects there, and what Sena did in them never
    showed up on their boards. Only a project Luma already has (by its name, any case); Luma off or closed — no card, the job
    goes on all the same. The card's id, or None."""
    if not settings.get("use_luma"):
        return None
    try:
        if not luma.is_up():
            return None
        names = {str(p.get("title", "")).lower(): p.get("id") for p in luma.call("project_list", {}).get("items", [])}
        board = names.get(project.lower())
        if not board:
            return None
        first = task.split("\n")[0].strip()
        title = first if len(first) <= 80 else first[:80].rsplit(" ", 1)[0] + "…"
        card = luma.call("task_add", {"items": [{"title": title, "project": board, "notes": task[:2000]}]})["created"][0]["id"]
        luma.call("task_update", {"items": [{"task": card, "status": "doing"}]})
    except (luma.LumaError, KeyError, IndexError, TypeError, AttributeError) as e:
        events.log("work", f"Luma: карточка для «{project}» не заведена ({str(e)[:200]})", project=project)
        return None
    events.log("work", f"Luma: «{title}» в проекте {project} — в работе", project=project, card=card)
    return card


def _card_moved(card, state, task, result):
    """The card follows the job: done, back to the backlog (todo) with why, or still in work (a change of Sena's own code
    waiting for his «применять»). The notes keep the task and how it ended. A card he deleted stays deleted."""
    if not card:
        return
    try:
        luma.call("task_update", {"items": [{"task": card, "status": state, "notes": f"{task}\n\nИтог: {result}"[:4000]}]}, per_item=True)
    except luma.LumaError as e:
        events.log("work", f"Luma: карточка не передвинута ({str(e)[:200]})", card=card)


def _take(path, what):
    """The project's folder for one job at a time — a coding run or the way back to a snapshot: None — taken now for `what`;
    otherwise what is being done there."""
    key = str(path.resolve())
    with _lock:
        doing = _busy.get(key)
        if doing is None:
            _busy[key] = what
    return doing


def _free(path):
    with _lock:
        _busy.pop(str(path.resolve()), None)


NAMED_PATH = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+\.\w{1,6})(?![\w/])")


def _grounded(path, text):
    """Lessons about a project with the paths they name checked: a line naming a file that is no longer there is marked, not
    passed on as checked (compound-engineering grounds a lesson in the files it names before trusting it)."""
    lines = []
    for line in (text or "").splitlines():
        gone = [p for p in NAMED_PATH.findall(line) if not (Path(path) / p.lstrip("./")).exists()]
        lines.append(line + (f" (в проекте уже нет {', '.join(gone[:2])} — урок мог устареть)" if gone else ""))
    return "\n".join(lines)


def _project_run(project, path, task, level, check):
    status.show(f"Работаю в проекте {project}", who=f"ag_code/{project}")
    seen = _git_view(path)
    before, unsnapped = snapshot(project, path, f"до задачи: {task[:150]}")
    own = _grounded(path, lessons.for_task("ag_code", f"{project}: {task}"))   # what reviews of earlier work here taught (test command, traps)
    previous = getattr(status.local, "who", None)
    status.local.who = f"ag_code/{project}"
    checked = ""
    try:
        done = _claude(path, task + (f"\n\nЧто известно по прошлым задачам (проверено разбором):\n{own}" if own else ""), f"код: {project}", level)
        done = _asked(path, done, f"код: {project}", level, project)
        if check.strip() and done["ok"]:
            status.show(f"Проверяю сама: {check[:60]}", who=f"ag_code/{project}")
            passed, tail = _check(path, check)
            repairs = 0
            while not passed and done["session"] and repairs < REPAIRS and not cancel.is_cancelled():
                repairs += 1
                status.show(f"Проверка не прошла: Claude Code чинит (заход {repairs})", who=f"ag_code/{project}")
                done = _claude(path, REPAIR.format(command=check, tail="\n".join(tail[-40:])), f"код: {project}, починка", level, resume=done["session"])
                passed, tail = _check(path, check)
            if passed and not cancel.is_cancelled():   # green: one look at the whole change before handing it over
                reviewed = _review(path, done, f"код: {project}", level)
                if reviewed is not done:
                    done = {**reviewed, "text": done["text"] + "\nПеред сдачей: " + reviewed["text"]}
                    passed, tail = _check(path, check)
            checked = (f"\nПроверка «{check}»: прошла" + (f" после починки ({repairs})" if repairs else "") if passed
                       else f"\nПроверка «{check}» НЕ прошла" + (f" и после {repairs} заходов починки" if repairs else "") + ":\n" + "\n".join(tail[-8:]))
        elif done["ok"] and not cancel.is_cancelled():
            reviewed = _review(path, done, f"код: {project}", level)
            if reviewed is not done:
                done = {**reviewed, "text": done["text"] + "\nПеред сдачей: " + reviewed["text"], "commands": done.get("commands", []) + reviewed.get("commands", [])}
            # no check was named: Claude Code's own "the tests pass" is run again here, not passed on as its word (MetaGPT's
            # one finding that held: running the code and feeding back errors is what improves results, review alone doesn't)
            own = _own_check(done)
            if own and not cancel.is_cancelled():
                passed, tail = _check(path, own)
                checked = (f"\nПроверила сама «{own}»: прошла" if passed else
                           f"\nClaude Code говорит, что готово, но «{own}» у меня НЕ прошла:\n" + "\n".join(tail[-8:]))
            else:
                checked = "\nНе проверено выполнением: Claude Code не запускал ни тестов, ни сборки — проверь, прежде чем говорить Теме «готово»."
    finally:
        status.local.who = previous
    status.show("Claude Code закончил", who=f"ag_code/{project}")
    told = _git_said(seen, _git_view(path)) + _memory_said()
    if told:
        events.log("code", f"проект {project}: {told.strip()}", project=project)
    bare = "\nНастройки Claude Code этого проекта в запуске не загружались." if getattr(_run_local, "bare", False) and surface(path)[0] else ""
    return done["text"] + checked + told + bare + _kept(project, path, task, before, unsnapped) + (
        f"\n[сессия Claude Code {done['session']}, ходов {done['turns']}]" if done["session"] and done["turns"] else "")


def _kept(project, path, task, before, unsnapped):
    # the line about the way back: what the run changed and how to undo it, or why there is no snapshot
    if not before:
        return f"\nСнимка до правки нет ({unsnapped}): вернуть как было можно только руками"
    after, _ = snapshot(project, path, "после задачи")
    try:
        changed = _between(project, path, before, after) if after else None
    except Exception as e:
        events.error(f"Снимок проекта {project}: что изменилось", e)
        changed = None
    if changed is not None and not any(changed.values()):
        return "\nФайлы проекта не изменились"
    try:
        loosened = _loosened(_shadow(project, path, "diff", "-U0", "--no-color", before, after)) if after else []
    except Exception as e:
        events.error(f"Снимок проекта {project}: разница", e)
        loosened = []
    with _lock:
        runs = store.load_json(SNAPSHOT_RUNS, {})
        runs[project] = (runs.get(project, []) + [{"before": before, "after": after, "task": task[:200], "t": time.time()}])[-20:]
        store.save_json(SNAPSHOT_RUNS, runs, indent=1)
    settings = sorted({n for names in (changed or {}).values() for n in names if _executable(n)})
    return (f"\n{f'Файлы: {_listed(changed)}. ' if changed else ''}Снимок до правки сохранён: вернуть как было — code_undo(«{project}») после «да» Темы"
            + (f"\nПохоже на ослабление проверок: {'; '.join(loosened)} — посмотри, прежде чем говорить «готово»." if loosened else "")
            + (f"\nClaude Code изменил исполняемые настройки проекта ({', '.join(settings[:5])}): они выполнятся сами при следующем "
               "запуске Claude Code, git или редактора — скажи Теме прямо." if settings else ""))


# ---------- edits to its own code ----------
SELF_TASK = """Ты правишь код ассистента Сены в отдельной копии (ветка {branch}); рабочий бот работает из другой папки и твои
правки его не тронут, пока Тема их не примет. Задача от Темы: {task}

Правила: следуй CLAUDE.md и README.md этого проекта; data/ и .env не трогай; новую логику покрой тестом рядом с похожими;
исправление ошибки — сначала тест, который её воспроизводит и падает, потом правка;
в конце запусти `{python} -m unittest` и добейся, чтобы все тесты прошли; допиши HISTORY.md: что сделано и зачем.
Делай только то, что просят: не улучшай, не переписывай и не чини несвязанное. Существующие тесты не меняй, если задача не про них.
Формат данных меняй только добавлением (новое поле или таблица рядом); старое не удаляй и не переименовывай в той же правке:
откат после неудачного запуска вернёт код, но не данные.
Ответь коротко по-русски: что изменил, зачем, какие файлы."""


# Sena's own git commands run no hooks and no fsmonitor a coding run could have planted (Anthropic's security plugin hardens
# its git calls the same way): Claude Code works unsandboxed in a copy that shares the repository's config and hooks
GIT_SAFE = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")


def _git(args, cwd=None, check=True):
    result = subprocess.run(["git", *GIT_SAFE, *args], cwd=cwd or config.ROOT, capture_output=True, text=True, timeout=120)
    if check and result.returncode:   # what went wrong first: the command line itself is long (ids, paths) and was all a cut message kept
        command = next((a for a in args if not a.startswith("-") and "=" not in a and "/" not in a), "")
        raise RuntimeError(f"{(result.stderr or result.stdout).strip()[:300]} (git {command})")
    return result.stdout.strip()


def _git_state():
    """A fingerprint of what makes git run code or change meaning: the repository's config, its hooks and its attributes. A
    coding run that touched them is not taken in — not committed, not merged — whatever its tests say."""
    git_dir = Path(config.ROOT) / ".git"
    digest = hashlib.sha256()
    for p in sorted([git_dir / "config", git_dir / "info" / "attributes", *(git_dir / "hooks").glob("*")]):
        if p.is_file() and not p.name.endswith(".sample"):
            digest.update(p.name.encode() + b"\0" + p.read_bytes())
    return digest.hexdigest()


def changes():
    return store.load_json(CHANGES_FILE, [])


def _save_changes(rows):
    store.save_json(CHANGES_FILE, rows[-30:], indent=1)


def _record(change):
    with _lock:
        rows = [c for c in changes() if c["id"] != change["id"]] + [change]
        _save_changes(rows)


REPAIRS = 2   # rounds in which Claude Code, in the same session, fixes what made the tests fail


def _tests(path):
    """Sena's test suite in her copy: inside the sandbox and without her keys like any check — the copy holds code Claude has
    just written, and her own tests used to run it with every key in the environment. The copy lives in her data, so this one
    run may use that folder (seatbelt open_to); all lines are kept, the failing tests' names are read from the summary."""
    return _check(path, f"{shlex.quote(sys.executable)} -m unittest", open_to=(path,), keep=None)


REPAIR = ("Проверка не прошла (`{command}`), вот конец вывода:\n{tail}\n\nПроверка и тесты правильные — не меняй их ради того, чтобы прошли. "
          "Найди причину в коде и исправь её; ничего лишнего не трогай. Снова запусти проверку и добейся, чтобы прошла. Ответь коротко, что исправил.")
REVIEW = """Перед сдачей посмотри на всю свою правку (ниже git status и сводка изменений) и проверь по списку:
- лишние файлы — временные, отладочные, для воспроизведения ошибки — удали;
- существующие тесты, если задача не про них, верни как были;
- правка делает только то, что просили, ничего несвязанного;
- всё, что просили, сделано целиком: пройдись по пунктам задачи; заглушек и TODO не осталось; чего не сделал — так и назови.
Поменял что-то — снова запусти проверку. Ответь одной строкой, что в итоге изменено; заметил вне задачи что-то неладное, но не трогал —
второй строкой «Заметил: …».

git status:
{status}

Изменения:
{stat}"""


def _review(path, done, purpose, level):
    """One more round in the same session before the work is handed over: Claude Code looks at its whole change against a short
    checklist — stray files, edited tests, unrelated edits (SWE-agent's review on submit, its default configuration). A change
    to Sena's own code is committed with everything in the copy, so a forgotten repro script would reach the working code."""
    changed = _git(["status", "--porcelain"], cwd=path, check=False)
    if not done.get("ok") or not done.get("session") or not changed:
        return done
    stat = _git(["diff", "--stat"], cwd=path, check=False)
    again = _claude(path, REVIEW.format(status=changed[-2000:], stat=stat[-2000:] or "только новые файлы"), purpose + ", проверка перед сдачей",
                    level, resume=done["session"])
    return again if again["ok"] else done


FAILED_TEST = re.compile(r"^(?:FAIL|ERROR): (\S+) \(([\w.]+)\)", re.M)
BIG_DELETE = 300   # lines removed by one change: worth the owner's look before he says "apply"


NOT_RUN = "(набор тестов не запустился)"


def _failing(passed, tail):
    """Which tests fail, by name (unittest's FAIL/ERROR lines); a suite that broke before naming any — one placeholder."""
    if passed:
        return set()
    names = {f"{m.group(2)}" for m in FAILED_TEST.finditer("\n".join(tail))}
    return names or {NOT_RUN}


def _new_failures(passed, tail, before):
    """What fails now and didn't before the change. A suite that didn't start after the change is never "nothing new":
    the placeholder in the baseline (no sandbox, say) would otherwise cancel it, and an untested change read as passed."""
    now = _failing(passed, tail)
    return now - before if NOT_RUN not in now else now


CHECK_LIMIT = 900   # seconds a check (a project's tests, a build) may run


def _check(path, command, open_to=(), keep=60):
    """The owner's (or Sena's) own check of a coding job, run here, not taken on Claude's word: (passed, last lines).
    In the same sandbox as the terminal agent (core/seatbelt.run): a project's tests can't reach Sena's data or keys, a hung
    test is stopped with everything it started, and what it printed before stays for the repair round."""
    refused = safety.forbidden_command(command)
    if refused:
        return False, [refused]
    if not seatbelt.available():
        return False, ["проверка не запущена: нет песочницы macOS"]
    mine = cancel.current_task()
    output, code, why = seatbelt.run(command, path, CHECK_LIMIT, stop=lambda: mine in cancel.cancelled, open_to=open_to)
    lines = output.splitlines()[-keep:] if keep else output.splitlines()
    if why:
        return False, lines + [f"проверка не закончилась за {CHECK_LIMIT // 60} минут и остановлена" if why == "timeout" else "проверка остановлена по просьбе Темы"]
    return code == 0, lines


# what a change does to the bar rather than to the code (addyosmani's agent-skills, its floor guard): notes, never a block
FLOOR = ((re.compile(r"@(?:unittest\.)?skip(?:If|Unless)?\b|\.skipTest\(|@pytest\.mark\.(?:skip|xfail)|@(?:unittest\.)?expectedFailure|"
                     r"\b(?:it|test|describe)\.skip\(|\bx(?:it|describe)\(|@(?:Ignore|Disabled)\b"), "пропуск теста"),
         (re.compile(r"#\s*(?:noqa|type:\s*ignore|pragma:\s*no\s*cover)|eslint-disable|@ts-(?:ignore|nocheck|expect-error)"), "выключенная проверка"),
         (re.compile(r"\b(?:TODO|FIXME)\b|NotImplementedError"), "заглушка или TODO"))
EXCEPT = re.compile(r"^\s*except\b[^:]*:\s*(?:#.*)?$")
SILENT = re.compile(r"^\s*(?:except\b[^:]*:\s*)?(?:pass|continue|\.\.\.)\s*(?:#.*)?$")
ASSERT = re.compile(r"^\s*(?:self\.assert\w*\(|assert\b|expect\(|XCTAssert\w*\(|assert[A-Z]\w*\()")
TEST_FILE = re.compile(r"(?:^|/)(?:tests?/|test_[^/]*$|[^/]*_test\.\w+$|[^/]*\.(?:test|spec)\.\w+$|[^/]*Tests?\.(?:kt|swift|java)$)")


def _loosened(diff):
    """Notes on how a change lowered the bar, from its diff (-U0): tests skipped, checks switched off, errors silenced, stubs left,
    assertions taken out of tests that stay. [] — none seen."""
    found, asserts, kept, current, last, header = {}, {}, set(), None, "", False
    for line in diff.splitlines()[:200000]:
        if line.startswith("diff --git "):   # a file's header runs until its first hunk: "--- x" there is a name, not a removed line
            header, current, last = True, None, ""
            continue
        if header:
            if line.startswith("+++ "):
                current = line[6:] if line.startswith("+++ b/") else None
                kept.add(current)
            header = not line.startswith("@@")
            continue
        if line.startswith("@@"):
            last = ""
            continue
        if current is None or line[:1] not in ("+", "-"):
            continue
        text, test = line[1:], bool(TEST_FILE.search(current))
        if ASSERT.match(text) and test:
            asserts[current] = asserts.get(current, 0) + (1 if line[0] == "-" else -1)
        if line[0] == "+":
            for pattern, what in FLOOR:
                if pattern.search(text):
                    found.setdefault(what, set()).add(current)
            if SILENT.match(text) and (EXCEPT.match(last) or text.lstrip().startswith("except")):
                found.setdefault("ошибка глушится (except … pass)", set()).add(current)
            last = text
    notes = [f"{what}: {', '.join(sorted(files)[:4])}" for what, files in found.items()]
    taken = [f"{f} (−{n})" for f, n in sorted(asserts.items()) if n > 0 and f in kept]
    return notes + ([f"из тестов убраны проверки: {', '.join(taken[:4])}"] if taken else [])


RAN = re.compile(r"^Ran (\d+) tests? in", re.M)
SKIPPED = re.compile(r"skipped=(\d+)")


def _counted(lines):
    """(tests run, tests skipped) from unittest's summary; run is None when the suite didn't say."""
    text = "\n".join(lines)
    ran, skipped = RAN.findall(text), SKIPPED.findall(text)
    return int(ran[-1]) if ran else None, int(skipped[-1]) if skipped else 0


def _shrunk(before, after):
    """A note when the suite runs fewer tests or skips more than before the change ("" — it didn't shrink)."""
    (ran0, skip0), (ran1, skip1) = before, after
    notes = ([f"тестов запускается меньше: было {ran0}, стало {ran1}"] if ran0 and ran1 is not None and ran1 < ran0 else []) + \
            ([f"пропущенных тестов больше: было {skip0}, стало {skip1}"] if skip1 > skip0 else [])
    return "; ".join(notes)


def _tests_on_old_code(path, base):
    """Whether the change's new and changed tests tell the old code from the new: they run on the base with only those test files
    put in. All pass there — they don't check what was changed (a bug fix's test must fail before the fix: addyosmani's agent-skills,
    test-driven development). A note for the owner, never a block; "" — nothing to say or nothing could be run."""
    listed = _git(["diff", "--cached", "--name-status", "--no-renames", base, "--", "tests"], cwd=path, check=False)
    files = [line.split("\t", 1)[1] for line in listed.splitlines() if line[:1] in ("A", "M") and "\t" in line]
    modules = " ".join(f[:-3].replace("/", ".") for f in files if Path(f).name.startswith("test_") and f.endswith(".py"))
    if not modules:
        return ""
    old = WORK_DIR / f"{path.name}-base"
    try:
        _git(["worktree", "add", "-q", "--detach", str(old), base])
        for f in files:
            (old / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(path / f, old / f)
        passed, lines = _check(old, f"{shlex.quote(sys.executable)} -m unittest {modules}", open_to=(old,), keep=None)
    except Exception as e:
        events.error("Новые тесты на старом коде", e)
        return ""
    finally:
        _git(["worktree", "remove", "--force", str(old)], check=False)
    text = "\n".join(lines)
    failed = len(re.findall(r"^(?:FAIL|ERROR): ", text, re.M))
    if passed and _counted(lines)[0]:
        return "Новые и изменённые тесты проходят и на старом коде: изменённое поведение они не проверяют (если это исправление ошибки — ошибку они не ловят)."
    return f"Новые тесты на старом коде падают ({failed}): правку они проверяют." if failed else ""


# settings that make Claude Code, git, an editor or a shell run code later, outside any sandbox (Anthropic's claude-code-action
# restores the same list before every run)
EXECUTABLE_DIRS = (".claude/", ".husky/", ".githooks/")
EXECUTABLE_FILES = (".mcp.json", ".claude.json", ".vscode/tasks.json", ".envrc", ".gitmodules", ".ripgreprc")


def _executable(name):
    return name.startswith(EXECUTABLE_DIRS) or name in EXECUTABLE_FILES


def _touched_tests(path, base):
    """Existing test files the change edited or deleted (git status against the base): Claude may have bent the test to pass."""
    out = _git(["diff", "--name-status", base, "--", "tests"], cwd=path, check=False)
    return sorted(line.split("\t", 1)[1] for line in out.splitlines() if line[:1] in ("M", "D") and "\t" in line)


def self_change(task, level="normal", card=None):
    """Fix to its own code in a copy: branch sena/auto-<id>, Claude, tests, report. Nothing changes in the working code.
    Failing tests get up to REPAIRS rounds in the same Claude Code session, which remembers what it changed and why."""
    task = " ".join(task.split())
    if len(task) < 10:
        return "Опиши задачу подробнее: что именно поправить в Сене и зачем"
    dirty = _dirty()
    if dirty:   # the copy is the last commit: a whole Claude Code run and test suite on code without these edits, refused at apply
        return Failure(f"Свою правку не начинаю: в рабочем коде Сены есть незакоммиченные изменения (файлов: {len(dirty)} — "
                       f"{', '.join(dirty[:5])}{'…' if len(dirty) > 5 else ''}). Копия делается от последнего коммита и их не увидит, "
                       "а применить правку поверх них нельзя. Сначала их закоммитит Тема (это делает он сам), потом поручи снова.")
    change_id = uuid.uuid4().hex[:6]
    branch, path = f"sena/auto-{change_id}", WORK_DIR / change_id
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    base = _git(["rev-parse", "HEAD"])
    _git(["worktree", "add", "-q", "-b", branch, str(path), "HEAD"])
    held = _gate("Sena", path, lambda: self_change(task, level))
    if held:
        _git(["worktree", "remove", "--force", str(path)], check=False)
        _git(["branch", "-D", branch], check=False)
        return held
    change = {"id": change_id, "branch": branch, "base": base, "task": task, "created": time.time(), "tests": None, "diff": "", "report": "",
              **({"card": card} if card else {})}   # its card in Luma: done when applied, back to the backlog when dropped
    guarded = _git_state()
    try:
        status.show("Правлю свою копию", who="ag_code/Sena")
        previous = getattr(status.local, "who", None)
        status.local.who = "ag_code/Sena"
        try:
            status.show("Проверяю тесты до правки", who="ag_code/Sena")
            passed, tail = _tests(path)
            before, counted = _failing(passed, tail), _counted(tail)   # what already fails at the base isn't this change's doing (Aider: a baseline first)
            done = _claude(path, SELF_TASK.format(branch=branch, task=task, python=sys.executable), f"свой код: {task[:60]}", level)
            done = _asked(path, done, f"свой код: {task[:60]}", level, "Sena")
            change.update(report=done["text"][:3000], session=done["session"])
            status.show("Прогоняю тесты", who="ag_code/Sena")
            passed, tail = _tests(path)
            new = _new_failures(passed, tail, before)
            change["repairs"] = 0
            while new and done["session"] and change["repairs"] < REPAIRS and not cancel.is_cancelled():
                change["repairs"] += 1
                status.show(f"Тесты не прошли: Claude Code чинит (заход {change['repairs']})", who="ag_code/Sena")
                done = _claude(path, REPAIR.format(command=f"{sys.executable} -m unittest", tail="\n".join(tail[-40:])), f"свой код, починка: {task[:50]}", level,
                               resume=done["session"])
                change["report"] = (change["report"] + f"\nПочинка {change['repairs']}: " + done["text"])[-3000:]
                passed, tail = _tests(path)
                new = _new_failures(passed, tail, before)
            if not new and not cancel.is_cancelled():   # green: one look at the whole change before it is committed
                status.show("Claude Code смотрит на правку перед сдачей", who="ag_code/Sena")
                reviewed = _review(path, done, f"свой код: {task[:50]}", level)
                if reviewed is not done:
                    change["report"] = (change["report"] + "\nПеред сдачей: " + reviewed["text"])[-3000:]
                    passed, tail = _tests(path)
                    new = _new_failures(passed, tail, before)
        finally:
            status.local.who = previous
        change["tests"] = not new
        change["git_touched"] = _git_state() != guarded
        change["tests_before"] = sorted(before)
        change["tests_tail"] = "\n".join(tail[-3:])
        change["tests_changed"] = _touched_tests(path, base)
        change["shrunk"] = _shrunk(counted, _counted(tail))
        _git(["add", "-A"], cwd=path)   # new files also count as a change
        change["diff"] = _git(["diff", "--stat", "--cached"], cwd=path)[-1500:]
        removed = re.search(r"(\d+) deletions?\(-\)", change["diff"])
        change["deleted"] = int(removed.group(1)) if removed else 0
        touched = _git(["diff", "--cached", "--name-only", "--no-renames", base], cwd=path, check=False).splitlines()
        change["config_touched"] = [f for f in touched if _executable(f)]
        change["rules_touched"] = [f for f in touched if Path(f).name in ("CLAUDE.md", "AGENTS.md")]
        change["loosened"] = _loosened(_git(["diff", "--cached", "-U0", "--no-color", base], cwd=path, check=False))
        change["old_code"] = _tests_on_old_code(path, base) if change["tests"] and change["diff"] else ""
        if change["diff"] and not change["git_touched"] and not change["config_touched"]:
            _git(["commit", "-q", "-m", f"Сена: {task[:70]}"], cwd=path)
            change["head"] = _git(["rev-parse", "HEAD"], cwd=path)   # the exact commit the report is about: only it is ever applied
            if change["tests"] and not cancel.is_cancelled():
                status.show("Независимая проверка правки", who="ag_code/Sena")
                change["review"] = _second_look(path, base, task, level)
    finally:
        _git(["worktree", "remove", "--force", str(path)], check=False)   # the copy is no longer needed, the branch stays
        status.show("Claude Code закончил", who="ag_code/Sena")
    touched_memory = _memory_said()
    change["memory_touched"] = bool(touched_memory)
    _record(change)
    events.log("update", f"правка предложена [{change_id}]: {task}", change=change_id, tests=change["tests"], diff=change["diff"])
    if change["git_touched"]:   # hooks, config or attributes of git changed during the run: code that would run later, unsandboxed
        events.log("error", f"правка [{change_id}] тронула настройки git: не принята", place="ag_code", change=change_id)
        return (f"Правка [{change_id}] НЕ принята: во время работы Claude Code изменились настройки, хуки или атрибуты git самого "
                "репозитория (.git/config, .git/hooks) — так можно подложить код, который выполнится потом без песочницы. Посмотри "
                "сам, что там изменилось, прежде чем что-то запускать.")
    if touched_memory:   # it reached outside its copy into Sena's own data: whatever its tests say, the owner hears it first
        return f"Правка [{change_id}] НЕ принята.{touched_memory}"
    if change["config_touched"]:   # settings that run code at the next start of Claude Code, an editor or a shell
        events.log("error", f"правка [{change_id}] тронула исполняемые настройки: не принята", place="ag_code", change=change_id)
        return (f"Правка [{change_id}] НЕ принята: она меняет исполняемые настройки ({', '.join(change['config_touched'][:5])}) — они "
                "выполнились бы потом без песочницы. Если это и есть задача, такие файлы Тема правит сам.")
    if not change["diff"]:
        _git(["branch", "-D", branch], check=False)
        return f"Ничего не изменилось. Отчёт Claude: {change['report'][:600]}"
    if not change["tests"]:
        return (f"Правка [{change_id}] НЕ прошла тесты, не применена; ветка {branch} сохранена для разбора.\n{change['tests_tail']}\n"
                f"Изменения:\n{change['diff']}\nОтчёт Claude: {change['report'][:800]}")
    notes = "".join(f"\n{n}" for n in verdict(change)["notes"])
    sid = _hold_apply(change)   # prepared with the report: his one "да" applies it (asking first and preparing after asked him twice)
    return (f"Правка [{change_id}] готова, тесты прошли. Изменения:\n{change['diff']}{notes}\nОтчёт Claude: {change['report'][:800]}\n"
            f"Применение подготовлено [{sid}]: спроси Тему «применять?», после его «да» — confirm_send({sid}). Подтверждение истекло — "
            f"apply_change(id=\"{change_id}\") подготовит его заново. После применения я перезапущусь.")


SECOND_LOOK = """Ты независимый рецензент. Другой исполнитель правил код ассистента Сены по задаче Темы, правка уже прошла тесты.
Задача была: {task}
Посмотри на правку свежим взглядом (ниже её diff; нужные файлы прочитай сам) и найди то, что тесты пропустили: ошибку (какой
вход или состояние её вызовет и что сломается), гонку или утечку, нарушение правил CLAUDE.md (слои, одно место для одной вещи,
платные вызовы через costs, действия в журнал событий), опасное для данных, ключей или Mac, лишнее вне задачи. Файлы не меняй.
Ответь по-русски, до 5 пунктов, важное первым: «файл:строка — что не так — почему». Существенного нет — одной строкой «Замечаний нет».

Diff:
{diff}"""


def _second_look(path, base, task, level):
    """A reviewer that didn't write the change reads it before the owner is asked: a fresh Claude Code session that can only
    read (the agents audit: the only review was Claude Code's look at its own work). Its notes go into the report, they don't
    block — the owner decides."""
    diff = _git(["diff", "-U3", "--no-color", base, "HEAD"], cwd=path, check=False)
    if not diff:
        return ""
    done = _claude(path, SECOND_LOOK.format(task=task[:500], diff=diff[:40000]), f"свой код, независимая проверка: {task[:40]}", level, readonly=True)
    if done["ok"]:
        return done["text"][:1500]
    # said, not left empty: a reviewer that never ran (the quota closed after a deep change, a stall) read as "found nothing"
    return f"не состоялась ({str(done.get('why') or 'нет ответа')[:200]}) — правку смотрел только тот, кто её писал"


def verdict(change):
    """What is said about a change wherever it is shown — the report, the apply, the apps: {"refused": why it can't be applied
    or None, "notes": what the owner should look at before "apply"}."""
    refused = ("Эта правка не прошла тесты, применять нельзя" if not change.get("tests") else
               "Эта правка меняла настройки git, исполняемые настройки или файлы самой Сены: она не принята, применять нельзя"
               if change.get("git_touched") or change.get("config_touched") or change.get("memory_touched") else None)
    notes = []
    if change.get("tests_before"):
        notes.append(f"До правки уже падали: {', '.join(change['tests_before'][:5])} — новых провалов нет.")
    if change.get("tests_changed"):
        notes.append(f"Claude изменил существующие тесты: {', '.join(change['tests_changed'][:6])} — посмотри, не подогнал ли он их под код.")
    if change.get("deleted", 0) > BIG_DELETE:
        notes.append(f"Удалено строк: {change['deleted']} — много, стоит взглянуть до «применяй».")
    if change.get("shrunk"):
        notes.append(f"Набор тестов сжался: {change['shrunk']} — Claude мог убрать проверки.")
    if change.get("loosened"):
        notes.append(f"Похоже на ослабление проверок: {'; '.join(change['loosened'])}.")
    if change.get("old_code"):
        notes.append(change["old_code"])
    if change.get("rules_touched"):
        notes.append(f"Изменены правила для Claude Code ({', '.join(change['rules_touched'])}): они действуют на каждую следующую правку — прочитай их.")
    review = (change.get("review") or "").strip()
    if review and not review.startswith("Замечаний нет"):
        notes.append(f"Независимая проверка: {review}")
    return {"refused": refused, "notes": notes}


def _restart():
    # the restart onto the new code is asked for at once: from here no long-work turn starts (work.tick), so the wait below can
    # end — new turns kept it going to its 2 minutes and were cut. We stop once the tasks are finished (including the one where
    # Tema said "apply" — its answer must get through), but no later than in 2 minutes; the stop is the usual one (tasks written
    # down, her programs stopped, databases closed) and its exit code 3 tells run.sh to start again on the new code
    power.request_restart()

    def go():
        deadline = time.time() + 120
        while status.tasks and time.time() < deadline:
            time.sleep(1)
        time.sleep(5)   # the answer has already gone out to Telegram or the app
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=go, daemon=True).start()


def _apply_name(id):
    return f"применить правку [{id}]"   # one change, one apply waiting under this name


def _waiting_apply(id):
    """The id of this change's apply already waiting for his "да", or None."""
    return next((w["id"] for w in confirm.waiting() if w["name"] == _apply_name(id)), None)


def _forget_applies(id):
    # the change is applied or dropped: an apply of it left waiting stayed in every turn's context for 36 hours and answered
    # «уже применена» or «нет такой» when pressed
    for w in confirm.waiting():
        if w["name"] == _apply_name(id):
            confirm.cancel(w["id"])


def _hold_apply(change):
    """The apply prepared for his "да" — with the report, or again by apply_change: it waits APPLY_HOLD, his own "да" on a later
    turn or his click applies it. A refusal is a Failure (the journal used to book it "done"), and one that only means "not
    now" (his tasks would be cut, the working code has uncommitted edits) is prepared again at once: his next "да" applies it,
    instead of a spent "да" and a second question."""
    def run():
        ok, text, later = _apply(change["id"])
        if ok:
            return text
        if not later:
            return Failure(text)
        return Failure(f"{text}. Применение ждёт [{_hold_apply(change)}].")
    return confirm.hold(_apply_name(change["id"]), f"{change['task'][:200]}: влить в рабочий код и перезапуститься", run, ttl=APPLY_HOLD)


def apply_change(id):
    """For the model: prepares applying the change; it is applied only on the owner's "yes" (confirm_send) or his button in the
    apps (which call try_apply_change themselves). A restart with new code is as irreversible as a sent message: a task that
    read someone else's text must not be able to do it on its own. An apply of it already waiting is named, not doubled."""
    change = next((c for c in changes() if c["id"] == id), None)
    if not change:
        return Failure("Такой правки нет")
    if change.get("applied"):
        return Failure("Эта правка уже применена")
    refused = verdict(change)["refused"]
    if refused:
        return Failure(refused)
    sid = _waiting_apply(id) or _hold_apply(change)
    return (f"Ещё не применено [{sid}]: применить правку [{id}] «{change['task'][:120]}» и перезапуститься. Спроси Тему; "
            f"подтвердишь через confirm_send({sid}) только после его «да».")


def _would_cut():
    """The owner's own tasks a restart now would cut, by their words: his requests and their agents, a scheduled job. Not the
    task his "да" came in (its answer gets out: _restart waits for it), not service work — a review, the night, a news alert:
    the review of this very change used to refuse his "да" — and not a long work's turn: _restart gives it its time, and a cut
    turn is resumed by the work itself."""
    me = status.current()
    return [t["text"] for t in list(status.tasks.values()) if t is not me and not t.get("service") and not t.get("work")]


def _dirty():
    """Tracked files of the working code with uncommitted changes: a change is made from the last commit and applied only onto
    a clean tree."""
    return [line.split(maxsplit=1)[-1] for line in _git(["status", "--porcelain", "--untracked-files=no"]).splitlines() if line.strip()]


def try_apply_change(id):
    """On Tema's word: merge the fix branch into the working code and restart (rollback happens automatically if it doesn't come back up). Returns (succeeded, text)."""
    return _apply(id)[:2]


def _apply(id):
    """try_apply_change, and whether a refusal only means "not now" (succeeded, text, later): the change itself can still go in."""
    change = next((c for c in changes() if c["id"] == id), None)
    if not change:
        return False, "Такой правки нет", False
    if change.get("applied"):
        return False, "Эта правка уже применена", False
    refused = verdict(change)["refused"]
    if refused:
        return False, refused, False
    cut = _would_cut()
    if cut:
        one = len(cut) == 1
        return False, (f"Не применяю сейчас: идёт «{cut[0][:80]}»" + ("" if one else f" и ещё {len(cut) - 1}")
                       + f" — перезапуск {'её' if one else 'их'} оборвёт. Когда {'закончится' if one else 'закончатся'} — подтверди ещё раз"), True
    if _dirty():
        return False, "В рабочем коде есть незакоммиченные изменения: сначала их нужно закоммитить (это делает Тема), иначе правка смешается с ними", True
    head = _git(["rev-parse", "--verify", "-q", change["branch"]], check=False)
    if change.get("head") and head != change["head"]:   # the branch moved after the report: what would merge isn't what was shown
        return False, f"Ветка правки [{id}] изменилась после отчёта: применять нельзя, попроси сделать правку заново", False
    try:
        _before_update(id)
    except Exception as e:
        events.error("Копия данных перед обновлением", e)
        return False, f"Не применила: не удалось сохранить копию данных перед обновлением ({e})", False
    before = _git(["rev-parse", "HEAD"])
    try:
        _git(["merge", "--no-ff", "-q", "-m", f"Сена: {change['task'][:70]} [{id}]", change.get("head") or change["branch"]])
    except RuntimeError as e:
        _git(["merge", "--abort"], check=False)
        return False, f"Не удалось влить правку: {e}", False
    after = _git(["rev-parse", "HEAD"])
    PENDING_FILE.write_text(f"{before} {id} {after}\n", encoding="utf-8")
    _record({**change, "applied": time.time(), "commit": after})
    _card_moved(change.get("card"), "done", change["task"], f"Применена [{id}].")
    events.log("update", f"правка применена [{id}], перезапуск; откат на {before[:7]}, если не поднимусь за {GRACE} с", change=id, commit=after)
    _forget_applies(id)
    _restart()
    return True, f"Применила [{id}], перезапускаюсь. Если за {GRACE // 60} минуты не поднимусь — откачусь на прежний код сама и напишу.", False


BEFORE_UPDATE = config.BACKUPS_DIR / "before-update"   # one folder per applied change: the records and memory files as they were
BEFORE_UPDATE_KEPT = 3


def _before_update(id):
    """The records and the memory files as they are before new code meets them: rolling back a failed update brings the code back,
    not data a new migration changed (addyosmani's agent-skills: expand, then contract). Nothing restores it by itself."""
    folder = BEFORE_UPDATE / id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "sena.db").unlink(missing_ok=True)   # a second try of the same apply: the copy is of now
    store.snapshot(folder / "sena.db")
    for p in (config.MEMORY_FILE, config.ARCHIVE_FILE, config.PERSONALITY_FILE, config.SETTINGS_FILE, config.SECRETS_FILE):
        if p.exists():
            shutil.copy(p, folder / p.name)
    for old in sorted((d for d in BEFORE_UPDATE.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime)[:-BEFORE_UPDATE_KEPT]:
        shutil.rmtree(old, ignore_errors=True)
    return folder


def drop_change(id):
    """For the model: the reply text (the control panel calls try_drop_change instead)."""
    return try_drop_change(id)[1]


def try_drop_change(id):
    """Reject a fix: delete the branch and the record. Returns (succeeded, text)."""
    change = next((c for c in changes() if c["id"] == id), None)
    if not change:
        return False, "Такой правки нет"
    _git(["branch", "-D", change["branch"]], check=False)
    with _lock:
        _save_changes([c for c in changes() if c["id"] != id])
    _card_moved(change.get("card"), "todo", change["task"], f"Правка [{id}] отклонена.")
    _forget_applies(id)
    events.log("update", f"правка отклонена [{id}]", change=id)
    return True, f"Правка [{id}] отклонена, ветка удалена"


def confirm_update(notify):
    """GRACE seconds after startup: if we were waiting to see whether the update would stick — it stuck. notify(text) — tell Tema.
    Only an update applied before this run began is its to confirm: one applied during it hasn't run yet (its restart may still
    wait for the tasks), and "прижилось" was said for it and the file run.sh rolls a failed update back by was deleted."""
    try:
        if PENDING_FILE.stat().st_mtime >= STARTED:
            return False
    except OSError:   # no update waiting
        return False
    before, change_id, after = (PENDING_FILE.read_text(encoding="utf-8").split() + ["", ""])[:3]
    PENDING_FILE.unlink()
    GOOD_FILE.write_text(after + "\n", encoding="utf-8")
    events.log("update", f"обновление [{change_id}] прижилось", change=change_id, commit=after)
    notify(f"Обновление [{change_id}] прижилось: работаю на новом коде ({after[:7]}).")
    return True


TOOLS = [
    tool("apply_change", "Подготовить заново применение правки своего кода (id из отчёта claude_task по проекту Sena), если подготовленное "
                         "в отчёте истекло: после «да» Темы (confirm_send) ветка вливается в рабочий код, Сена перезапускается, при сбое "
                         "откатывается сама.", {"id": string()}, ["id"]),
    tool("drop_change", "Отклонить предложенную правку своего кода и удалить её ветку", {"id": string()}, ["id"]),
]
FUNCTIONS = {"apply_change": apply_change, "drop_change": drop_change}
