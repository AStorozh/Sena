"""Price alerts: "tell me when BTC is above 90 thousand". Checked every 5 minutes with a plain request, no model involved (no tokens spent).
A triggered alert goes to Tema in Telegram. A one-off alert is removed once it fires; a recurring one fires again
only after the price has moved back (otherwise it would spam)."""
import threading
import time
import uuid

from sena import config
from sena.core import events, store
from sena.core.tools import Failure, string, tool
from sena.world import finance, net, travel

_lock = threading.Lock()
KINDS = ("crypto", "stock", "fx", "flight")
WORDS = {"above": "выше", "below": "ниже"}
SLOW = {"flight": 3600}   # the fares database updates slowly: no point asking more often than once an hour


def _show(kind, x):
    return finance.fmt(x, 0) if kind == "flight" else finance.fmt(x)   # fares in whole dollars


def _price(kind, symbol):
    return travel.fare_of(symbol) if kind == "flight" else finance.price_of(kind, symbol)


def _load():
    return store.load_json(config.ALERTS_FILE, [])


def add(kind, symbol, condition, value, once=True):
    if kind not in KINDS:
        raise finance.FinanceError(f"kind — одно из: {', '.join(KINDS)}")
    if condition not in WORDS:
        raise finance.FinanceError("condition — above (выше) или below (ниже)")
    now = _price(kind, symbol)   # this also checks that the ticker or route actually exists
    with _lock:
        items = _load()
        item = {"id": uuid.uuid4().hex[:6], "kind": kind, "symbol": symbol, "condition": condition, "value": float(value),
                "once": bool(once), "fired": False}
        items.append(item)
        store.save_versioned(config.ALERTS_FILE, items, indent=1)
    return (f"Слежу [{item['id']}]: {symbol} {WORDS[condition]} {_show(kind, float(value))} "
            f"({'один раз' if once else 'каждый раз, когда пересечёт'}). Сейчас {_show(kind, now)}.")


def listing():
    items = _load()
    return "\n".join(f"[{a['id']}] {a['symbol']} {WORDS[a['condition']]} {_show(a['kind'], a['value'])}"
                     f" ({'один раз' if a['once'] else 'повторяется'})" + ("" if a.get("enabled", True) else " (ВЫКЛЮЧЕН, не сработает)") for a in items) or "Сигналов нет"


def remove(id):
    with _lock:
        items = _load()
        kept = [a for a in items if a.get("id") != id]
        if len(kept) == len(items):
            return Failure("Нет такого сигнала")
        store.save_versioned(config.ALERTS_FILE, kept, indent=1)
    return "Удалён"


def active():
    """All alerts as data (for the screen)."""
    return _load()


def toggle(id, enabled):
    """Disable an alert without deleting it, or re-enable it: a disabled one isn't checked."""
    with _lock:
        items = _load()
        alert = next((a for a in items if a["id"] == id), None)
        if alert is None:
            return Failure("Нет такого сигнала")
        alert["enabled"] = bool(enabled)
        store.save_versioned(config.ALERTS_FILE, items, indent=1)
        return alert


# a price source that keeps failing: asked less and less often (up to an hour apart), and the owner hears once, after an hour,
# that his alert is blind — and once more when it sees again (n8n's poll back-off and its "tell once" on a broken trigger)
_health = {}          # (kind, symbol) -> {"fails", "since", "until", "told"}
PAUSE_MOST = 3600     # s between tries of a source that keeps failing, at most
TELL_AFTER = 3600     # s a source is down before the owner hears of it


def _failed(key, error, notify):
    h = _health.setdefault(key, {"fails": 0, "since": time.time(), "until": 0, "told": False})
    h["fails"] += 1
    h["until"] = time.time() + min(60 * 2 ** h["fails"], PAUSE_MOST)
    if not h["told"] and time.time() - h["since"] >= TELL_AFTER:
        h["told"] = True
        notify(f"Сигнал по {key[1]}: цену не получаю больше часа ({str(error)[:120]}). Проверяю реже, скажу, когда заработает.")


def _recovered(key, notify):
    h = _health.pop(key, None)
    if h and h["told"]:
        notify(f"Сигнал по {key[1]}: цена снова приходит, слежу как обычно.")


def _quote(key):
    """The price for a key, or None while its source is down — a failure of any kind (no answer, or an answer of an odd shape: a
    quote without a price) counts against the source, never stops the round: the watches started after it still start."""
    if time.time() < _health.get(key, {}).get("until", 0):   # its source is down: asked again after a pause, not every check
        return None, None
    try:
        return _price(*key), None
    except Exception as e:
        events.error(f"Сигнал {key[1]}", e)
        return None, e if isinstance(e, net.WorldError) else net.WorldError(f"источник ответил непонятно ({type(e).__name__})")


def _expired(a, notify):
    # a fare alert whose departure date has passed is taken off, with a word to the owner
    if a["kind"] != "flight" or not travel.expired(a["symbol"]):
        return False
    notify(f"Сигнал по билетам {a['symbol']} снят: дата вылета прошла.")
    events.log("alert", f"{a['symbol']}: снят, дата вылета прошла", alert=a)
    return True


def _priced(a, prices, notify):
    """The price an alert is checked against this round: asked once per kind and symbol (prices keeps it for the round), None
    while its source is down or before a slow kind's turn (fares: once an hour; the time is kept in the alert)."""
    wait = SLOW.get(a["kind"], 0)
    if wait and time.time() - a.get("checked", 0) < wait:
        return None
    key = (a["kind"], a["symbol"])
    if key not in prices:
        prices[key], error = _quote(key)
        if error:
            _failed(key, error, notify)
        elif prices[key] is not None:
            _recovered(key, notify)
    if wait:
        a["checked"] = time.time()
    return prices[key]


def _fired(a, price, notify):
    """The alert at this price: told once when the price crosses its threshold (True); a fired one fires again only after the
    price has moved half a percent back (no jitter right at the threshold)."""
    hit = price > a["value"] if a["condition"] == "above" else price < a["value"]
    if hit and not a["fired"]:
        text = f"Сигнал: {a['symbol']} {WORDS[a['condition']]} {_show(a['kind'], a['value'])}, сейчас {_show(a['kind'], price)}."
        if a["kind"] == "flight":
            try:
                text += f" {travel.hint(a['symbol'])}"
            except Exception as e:   # the hint is a courtesy: the alert itself still goes
                events.error(f"Сигнал {a['symbol']}: подсказка", e)
        notify(text)
        events.log("alert", text, alert=a)
        a["fired"] = True
        return True
    if a["fired"] and not hit and (price < a["value"] * 0.995 if a["condition"] == "above" else price > a["value"] * 1.005):
        a["fired"] = False
    return False


def _write_back(items, gone):
    # into the fresh file: alerts added or switched while the round ran stay as they are now; this round's marks (fired, the
    # fare's last check) go onto them, a fired one-off and a fare whose date passed leave. Written only when that changes it
    with _lock:
        current = store.load_json(config.ALERTS_FILE, [])
        seen = {a.get("id"): a for a in items if isinstance(a, dict)}
        kept = []
        for a in current:
            if not isinstance(a, dict) or a.get("id") in gone:
                continue
            mine = seen.get(a.get("id"))
            if mine is not None:
                a = {**a, "fired": mine.get("fired"), **({"checked": mine["checked"]} if "checked" in mine else {})}
                if a["fired"] and a.get("once"):
                    events.log("alert", f"{a['symbol']}: одноразовый сигнал отработал и снят", alert=a)
                    continue
            kept.append(a)
        if kept != current:
            store.save_versioned(config.ALERTS_FILE, kept, indent=1)


def check(notify):
    """Check all alerts; notify(text) is called for each one that fires. Returns the number that fired."""
    with _lock:
        items = _load()
    fired, prices, gone = 0, {}, set()
    for a in items:
        try:
            if not a.get("enabled", True):
                continue
            if _expired(a, notify):
                gone.add(a["id"])
                continue
            price = _priced(a, prices, notify)
            if price is not None:
                fired += _fired(a, price, notify)
        except Exception as e:   # one broken record doesn't stop the round either
            events.error("Сигнал по цене", e)
    if items:
        _write_back(items, gone)
    return fired


TOOLS = [
    tool("price_alert", "Следить за ценой и написать Теме, когда она пересечёт порог. Проверка раз в 5 минут, бесплатно. "
                        "kind: crypto (BTC), stock (AAPL, ^GSPC, GC=F, MOEX:SBER), fx (пара USD/VND), "
                        "flight (самый дешёвый билет в долларах, symbol вида SGN-DPS:2026-11, с возвратом SGN-DPS:2026-11:2026-11; проверка раз в час).",
         {"kind": {**string("Что отслеживаем"), "enum": list(KINDS)}, "symbol": string("BTC, AAPL, USD/VND или SGN-DPS:2026-11"),
          "condition": {**string("above — когда станет выше порога, below — ниже"), "enum": list(WORDS)},
          "value": {"type": "number", "description": "Порог в валюте котировки (для BTC, акций США и билетов это доллары)"},
          "once": {"type": "boolean", "description": "true (по умолчанию) — сказать один раз; false — каждый раз при новом пересечении"}},
         ["kind", "symbol", "condition", "value"]),
    tool("list_alerts", "Какие сигналы по цене сейчас включены."),
    tool("remove_alert", "Удалить сигнал по цене по id из list_alerts (выключить, не удаляя, Тема может в приложении).", {"id": string()}, ["id"]),
]
FUNCTIONS = {"price_alert": add, "list_alerts": listing, "remove_alert": remove}
