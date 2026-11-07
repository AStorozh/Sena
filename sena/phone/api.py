"""Shared by the server and the remote: an error with a response code, and request parameter parsing.
On a client error (missing field, wrong type, garbage number, foreign path) it returns a 4xx code and a clear text with no internals exposed."""
import math

from sena.core import costs

REQUIRED = object()   # the field is required: missing it is a 400
TYPE_NAMES = {str: "строкой", int: "целым числом", float: "числом", bool: "значением да/нет", list: "списком", dict: "объектом"}


class ApiError(Exception):
    """A rejection with a code: 400 — the request is unclear, 403 — not allowed, 404 — no such thing, 413 — too large."""

    def __init__(self, code, text):
        super().__init__(text)
        self.code = code
        self.text = text


def number(q, key, default, low=1, high=1000, kind=int):
    """A numeric URL parameter: missing — default; garbage — 400; too small or too large — clamped to the low..high range.
    This way limit=0, negative, or huge values can't dump the whole database."""
    raw = q.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = kind(raw)
    except (TypeError, ValueError):
        raise ApiError(400, f"Параметр «{key}» должен быть числом")
    if isinstance(value, float) and not math.isfinite(value):
        raise ApiError(400, f"Параметр «{key}» должен быть числом")
    return max(low, min(high, value))


def field(body, key, kind=str, default=REQUIRED):
    """A request body field of the given type (kind — a type or a tuple of types). Missing field or wrong type — 400.
    If default is given, the field is optional: missing it (or null) returns default."""
    value = body.get(key)
    if value is None:
        if default is REQUIRED:
            raise ApiError(400, f"Не хватает поля «{key}»")
        return default
    kinds = kind if isinstance(kind, tuple) else (kind,)
    allowed = kinds + (int,) if float in kinds else kinds   # the number 5 in JSON also fits a float field
    ok = isinstance(value, allowed) and not (isinstance(value, bool) and bool not in kinds)   # True doesn't count as a number
    if not ok:
        raise ApiError(400, f"Поле «{key}» должно быть {' или '.join(TYPE_NAMES.get(k, k.__name__) for k in kinds)}")
    return value


def valid_period(q, default="today"):
    """A period from the URL (period=...); an unclear one — 400 with the list of what the expense journal understands."""
    period = q.get("period") or default
    try:
        costs.span(period)
    except ValueError:
        raise ApiError(400, "Период указан неверно: today, yesterday, week, month, last_month, all, ГГГГ-ММ-ДД, ГГГГ-ММ или ГГГГ-ММ-ДД..ГГГГ-ММ-ДД")
    return period
