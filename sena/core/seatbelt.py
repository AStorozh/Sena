"""The macOS sandbox around every command Sena's terminal agent runs: the kernel itself refuses, whatever the command's
spelling. A list of forbidden words can be talked around (split quotes, a variable, a Python wrapper, "./" in a path — the
Codex study broke Sena's list eight ways); the system's sandbox can't. Ordinary work stays allowed: files, programs, the
network, sound, apps. What is closed:
- Sena's own data (memory, journals, keys) — neither read nor written, except what may leave her (inbox, downloads, generated);
- keys wherever they are: .env files, server and cloud keys, logins, the Keychain's files, the browser's profile;
- what runs later with more power is readable but never written: Sena's code, Claude's settings, launch agents, shell profiles,
  git hooks and configs of the projects;
- the tools that reach around the sandbox are never started: the Keychain tool (it opens the key to the owner's secrets),
  admin rights, launchd and cron;
- renaming a parent folder of anything protected (that would carry it past the rules), and fcntls that mutate through a
  read-only descriptor.
Built the way Codex builds its profile (codex-rs/sandboxing/src/seatbelt.rs, Apache-2.0): fixed rules, every path passed as a
-D parameter, the absolute /usr/bin/sandbox-exec, the parents' protection generated and kept last.
run() is the one way an agent's command is started: the terminal agent's commands, a coding job's check, the tests of Sena's copy."""
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from sena import config
from sena.core import safety

SANDBOX_EXEC = "/usr/bin/sandbox-exec"   # absolute: a planted sandbox-exec on PATH is never the one used
NEVER_RUN = ("/usr/bin/security", "/usr/bin/sudo", "/usr/bin/su", "/bin/launchctl", "/usr/bin/crontab", "/usr/bin/at", "/usr/bin/batch")


def _home(*parts):
    return str(Path.home().joinpath(*parts))


def _roots():
    """(hidden, read-only) folders and files, real paths. The hidden keys and logins are safety.SECRET_PLACES — the same list
    the file tools refuse (safety.secret_path), so what a command can't read the model can't open either."""
    data = os.path.realpath(config.DATA)
    hidden = [data, *safety.secret_places()]
    readonly = [os.path.realpath(config.ROOT), _home(".claude"), _home(".codex"), _home("Library", "LaunchAgents"),
                _home(".zshrc"), _home(".zprofile"), _home(".zshenv"), _home(".gitconfig")]
    return hidden, readonly


def _ancestors(path):
    parent = os.path.dirname(path)
    while parent and parent != "/":
        yield parent
        parent = os.path.dirname(parent)


def profile(open_to=(), offline=False):
    """(policy text, parameters) for this Mac's paths. open_to — folders inside Sena's data this one command may use
    (the copy of her code a self-change is tested in). offline — no connection out at all (a converter that has no business
    on the internet: it can't fetch addresses a document names or send what it reads anywhere)."""
    hidden, readonly = _roots()
    public = [os.path.realpath(d) for d in (config.INBOX_DIR, config.DOWNLOADS_DIR, config.GENERATED_DIR, *open_to)]
    params = {"SENA_DATA": hidden[0], **{f"PUBLIC_{i}": p for i, p in enumerate(public)},
              **{f"HIDDEN_{i}": p for i, p in enumerate(hidden[1:])}, **{f"READONLY_{i}": p for i, p in enumerate(readonly)}}
    keep_public = " ".join(f'(require-not (subpath (param "PUBLIC_{i}")))' for i in range(len(public)))
    rules = ["(version 1)", "(allow default)",
             f'(deny file-read* file-write* (require-all (subpath (param "SENA_DATA")) {keep_public}))',
             '(deny file-read* file-write* (require-all (regex #"/\\.env(\\.[^/]+)?$") (require-not (regex #"/\\.env\\.example$"))))',
             "(deny file-read* file-write* " + " ".join(f'(subpath (param "HIDDEN_{i}"))' for i in range(len(hidden) - 1)) + ")",
             '(deny file-write* (require-all (subpath (param "READONLY_0")) (require-not (subpath (param "SENA_DATA"))))'
             + "".join(f' (subpath (param "READONLY_{i}"))' for i in range(1, len(readonly)))
             + ' (regex #"^/Users/[^/]+/Desktop/[^/]+/\\.git/(hooks|config)(/.*)?$"))',
             "(deny process-exec " + " ".join(f'(literal "{p}")' for p in NEVER_RUN) + ")"]
    ancestors = sorted({a for p in [*hidden, *readonly] for a in _ancestors(p)})
    for i, a in enumerate(ancestors):
        params[f"ANCESTOR_{i}"] = a
        rules.append(f'(deny file-write-unlink (require-all (vnode-type DIRECTORY) (literal (param "ANCESTOR_{i}"))))')
    rules.append("(deny system-fcntl (fcntl-command 80 110))")
    if offline:
        rules.append("(deny network-outbound (remote ip))")
    return "\n".join(rules), params


_available = None


def available():
    """Whether a command can be started in the sandbox from here: sandbox-exec is there, and this process isn't inside a sandbox
    already — macOS refuses a second one inside any profile that denies something (the tests of Sena's copy run that way).
    Asked once per process."""
    global _available
    if _available is None:
        try:
            _available = os.path.exists(SANDBOX_EXEC) and subprocess.run(
                [SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"], capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            _available = False
    return _available


def wrap(command, open_to=(), offline=False):
    """argv: the zsh command inside the sandbox."""
    text, params = profile(open_to, offline)
    return [SANDBOX_EXEC, "-p", text, *[f"-D{k}={v}" for k, v in params.items()], "--", "/bin/zsh", "-c", command]


# no pager waits for a key, no progress bar fills the output, git fails instead of asking for a password, brew doesn't spend
# minutes updating itself inside a five-minute window (SWE-agent and mini-swe-agent run their commands with the same set)
QUIET = {"PAGER": "cat", "GIT_PAGER": "cat", "MANPAGER": "cat", "PIP_PROGRESS_BAR": "off", "TQDM_DISABLE": "1",
         "GIT_TERMINAL_PROMPT": "0", "HOMEBREW_NO_AUTO_UPDATE": "1"}
READ_MOST = 1_000_000   # characters of output read back (its start and its end); the rest is a gigantic log nobody reads


def _stop(proc):
    # the whole process group: whatever the command started goes too
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        for _ in range(20):
            if proc.poll() is not None:
                return
            time.sleep(0.1)
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


def _read(out):
    out.flush()
    size = out.seek(0, os.SEEK_END)
    out.seek(0)
    if size <= READ_MOST:
        return out.read()
    head = out.read(READ_MOST // 2)
    out.seek(size - READ_MOST // 2)
    return head + f"\n…[пропущено {size - READ_MOST} знаков вывода]…\n" + out.read()


def run(command, cwd, seconds, stop=None, env=None, open_to=()):
    """A command inside the sandbox, as an unattended agent needs it: (output, return code — None if stopped, "" / "stopped" /
    "timeout"). Output goes to a file, not a pipe: a program left in the background keeps a pipe open and hung the call, then
    died on its next write. stdin is closed and the command gets a session of its own, so nothing waits for a password on the
    owner's terminal. A stop or the time limit ends the whole process group, and what was printed before stays. Output is
    decoded with replacement and terminal colour codes are taken out (mini-swe-agent's environments/local.py, MIT; Codex)."""
    env = {**(safety.clean_env() if env is None else env), **QUIET}
    why = ""
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as out:
        # a command works in the owner's world: what it makes there keeps his usual permissions, not those of Sena's data
        proc = subprocess.Popen(wrap(command, open_to), cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True, umask=config.OWNER_UMASK)
        deadline = time.monotonic() + seconds   # awake time: a Mac asleep for an hour doesn't make a command "too long"
        while proc.poll() is None:
            why = "stopped" if stop and stop() else "timeout" if time.monotonic() > deadline else ""
            if why:
                _stop(proc)
                break
            time.sleep(0.2)
        output = safety.visible(_read(out)).strip()
    return output, None if why else proc.returncode, why


DENIED = ("Operation not permitted", "operation not permitted", "deny(", "Permission denied")


def denied(output):
    """Whether a command's output looks like the sandbox refused it (for a plain word to the model)."""
    return any(d in output for d in DENIED)
