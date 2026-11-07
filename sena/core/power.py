"""On or off. The core (python -m sena) is Sena at work. The screen without the core (python -m sena.phone) is Sena switched off:
it serves the apps and turns her back on, and spends nothing — no paid call, no home model, no agent's browser or Claude Code,
whatever button is pressed. One switch, checked at the doors every such call passes: limits.budget_check (every paid call),
core/local.py (the home models), the agents (their own programs), and the app server for the requests that only the core serves.
The core going down is "stopping": from then on new work isn't taken, and app.py finishes what was running."""
_off = False
_stopping = False
_restart = False


def set_off():
    """This process is the screen without the core: Sena is off in it."""
    global _off
    _off = True


def set_stopping():
    global _stopping
    _stopping = True


def is_off():
    return _off


def is_stopping():
    return _stopping


def request_restart():
    """The core is to come back right after it stops (a change to her own code was applied): it stops the usual way —
    tasks finished or written down, her programs stopped, the databases closed — and exits with exit_code() 3, which run.sh
    takes for "start again"."""
    global _restart
    _restart = True


def is_restarting():
    """A restart is asked for (an applied change to her own code): from here no new work starts — the wait for the running tasks
    can end — and the core comes back right after it stops."""
    return _restart


def exit_code():
    return 3 if _restart else 0


def refusal(what):
    """Why an action isn't done while Sena is off, in words for the owner. what — a noun or a verb («платные вызовы», «экзамен»,
    «отвечать»): one person throughout (it read «Сена выключена: отвечать без неё не делаю»)."""
    return f"Сена выключена: {what} — только когда она работает. Включи её в приложении: кнопка «Запустить Сену»."
