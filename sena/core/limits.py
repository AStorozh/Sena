"""Daily limits on paid calls: so Sena doesn't spend money by oversight. The counter resets every day."""
import threading
from datetime import date

from sena import config
from sena.core import costs, power, settings, store

_lock = threading.Lock()


class LimitReached(Exception):
    pass


def _today():
    state = store.load_json(config.USAGE_FILE, {})
    return state if state.get("date") == date.today().isoformat() else {"date": date.today().isoformat()}


def budget_check():
    """Daily budget from settings (the apps' Settings → Behaviour): once exhausted, paid calls stop until tomorrow or until the limit
    is raised. With Sena switched off (the screen without the core) no paid call goes out at all."""
    if power.is_off():
        raise LimitReached(power.refusal("платные вызовы"))
    limit = settings.get("budget_usd_day")
    if not limit:
        return
    spent = costs.spent_today()
    if spent >= limit:   # the section as both apps name it: there is no screen called «пульт»
        raise LimitReached(f"Дневной бюджет исчерпан: потрачено ${spent:.2f} из ${limit:.2f}. Поднять предел можно в приложении: Настройки → Поведение.")


def check(kind, limit, what):
    """Raises LimitReached if kind has already been used limit times today. limit=None — no restriction."""
    budget_check()
    if limit is None:
        return
    with _lock:
        if _today().get(kind, 0) >= limit:
            raise LimitReached(f"Дневной лимит исчерпан: {limit} {what} в сутки. Он нужен, чтобы не тратить деньги по недосмотру; "
                               "поменять его можно в коде рядом с вызовом.")


def usage():
    """How much of what was done today: {kind: count} (for the status screen)."""
    return {k: v for k, v in _today().items() if k != "date"}


def spend(kind):
    with _lock:
        state = _today()
        state[kind] = state.get(kind, 0) + 1
        store.save_json(config.USAGE_FILE, state)
