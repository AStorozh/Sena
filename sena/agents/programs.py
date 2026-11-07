"""Sena's own programs outside Python — the home model server, the agent's Chrome, her Claude Code runs, the exam of her mind —
and what can block others: a git lock left by a command she was killed in the middle of. Stopped when Sena stops (app.py) and
swept when the screen comes up after a stop or a crash (phone/__main__.py): once she is off, nothing of hers keeps running,
spends, holds memory or locks the repository. Only her own: found by her pid files and her own profile folder, never by a
program's name — the owner's Claude Code and Chrome are left alone."""
import os
import subprocess
import sys
import threading

from sena import config
from sena.agents import ag_code
from sena.agents.ag_web import browser
from sena.core import events, local, power, store
from sena.core.tools import Failure
from sena.world import mcp

EXAM_RUN = config.STATE_DIR / "exam_run.json"   # the exam running now (its pid and start time): one at a time, stopped with Sena
EXAM_LOG = config.LOGS_DIR / "exam.log"         # what the exam's process printed, a crash included
_exam_lock = threading.Lock()


def exam_running():
    """Whether an exam runs now: its record names a living process — the same pid with the same start time."""
    run = store.load_json(EXAM_RUN, {}) or {}
    return bool(run.get("pid")) and ag_code._ours(run["pid"], run.get("started"))


def start_exam(why, notify=False):
    """The exam of the mind in its own process (python -m sena.exam run), the way Sena starts it: not while she is off — it calls
    the paid brain — and not while another run is on; its output goes to data/logs/exam.log, and it stops with her. Words for
    the owner; a refusal is a Failure (the apps show it as one, not as a green «done»)."""
    with _exam_lock:
        if power.is_off():
            return Failure(power.refusal("экзамен"))
        if exam_running():
            return Failure("Экзамен уже идёт")
        with open(EXAM_LOG, "ab") as log:
            proc = subprocess.Popen([sys.executable, "-m", "sena.exam", "run", "--why", why, *(["--notify"] if notify else [])], cwd=config.ROOT,
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        store.save_json(EXAM_RUN, {"pid": proc.pid, "started": ag_code._started(proc.pid), "by": "sena"})   # hers: it stops with her
    events.log("exam", f"экзамен запущен: {why}", pid=proc.pid)
    return "Экзамен запущен: результат через несколько минут"


def exam_begin():
    """A run of the exam writes itself down, whoever started it (Sena, the app, a terminal): False while another one runs — two
    at once would spend twice and judge the same personality and skills against each other. The run Sena started keeps her mark."""
    with _exam_lock:
        run = store.load_json(EXAM_RUN, {}) or {}
        if run.get("pid") not in (None, os.getpid()) and exam_running():
            return False
        mine = {"by": run["by"]} if run.get("pid") == os.getpid() and run.get("by") else {}
        store.save_json(EXAM_RUN, {"pid": os.getpid(), "started": ag_code._started(os.getpid()), **mine})
        return True


def exam_end():
    with _exam_lock:
        if (store.load_json(EXAM_RUN, {}) or {}).get("pid") == os.getpid():
            EXAM_RUN.unlink(missing_ok=True)


def _stop_exam():
    # the exam Sena started leads its own process group (the run and its throwaway child) and goes as a whole, like her Claude
    # Code: SIGINT first, so the run still books what it spent. One started by hand in a terminal is the owner's: left to him —
    # known by the mark start_exam leaves, not by its process group (zsh makes a typed command a group leader too)
    run = store.load_json(EXAM_RUN, {}) or {}
    pid = run.get("pid")
    if not pid or run.get("by") != "sena" or not ag_code._ours(pid, run.get("started")):
        return False
    try:
        if os.getpgid(pid) != pid:
            return False
    except OSError:
        return False
    ag_code._stop_group(pid)
    EXAM_RUN.unlink(missing_ok=True)
    return True


def stop_all():
    """Stop every program of Sena's that is still running; returns what was stopped, in words."""
    done = []
    for what, stop in (("сервер домашних моделей", local.stop), ("Chrome помощника", browser.stop), ("серверы MCP", mcp.stop_all),
                       ("Claude Code", ag_code.stop_children), ("экзамен", _stop_exam), ("зависший замок git", ag_code.clear_stale_lock)):
        try:
            if stop():
                done.append(what)
        except Exception as e:   # one program that won't stop doesn't keep the others running
            events.error(f"Остановка: {what}", e)
    if done:
        events.log("stop", "остановлено: " + ", ".join(done))
    return done
