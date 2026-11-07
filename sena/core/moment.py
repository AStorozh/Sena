"""One clock for every record. A moment is stored as a UTC epoch (float) together with the UTC offset of the place it was
written ("+01:00"), so a line can later be read back in the local time it was lived in. Only display converts to local
time, and the owner's device does that itself; the Mac's zone is only a fallback. Nothing stores a wall-clock string
without its offset anymore: such strings jump when the Mac or the owner changes time zone."""
import re
import time
from datetime import datetime, timedelta, timezone

OFFSET = re.compile(r"^[+-]\d{2}:\d{2}$")
owner_offset = ""   # the owner's current zone as "+03:00": reported by the phone or the Mac app with each request; empty — unknown
PHONE_FRESH = 6 * 3600   # seconds the phone's zone stays his after the phone last reported it
_reported = {}           # "phone" | "mac" -> (the zone it reported last, when)


def now():
    return time.time()


def offset(t=None):
    """UTC offset of the Mac's zone at moment t (now by default), as "+01:00"."""
    z = datetime.fromtimestamp(time.time() if t is None else t).astimezone().strftime("%z")   # "+0100"
    return f"{z[:3]}:{z[3:]}"


def parse(value, default=0.0):
    """Epoch from anything older code may have written: a number, an ISO string with an offset, or a naive local string
    (read in the Mac's zone — the best guess for records made before offsets were kept)."""
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return default
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.timestamp()


def zone(offset_text):
    """tzinfo for "+01:00"; None for anything else."""
    if not offset_text or not OFFSET.match(offset_text):
        return None
    sign = -1 if offset_text[0] == "-" else 1
    hours, minutes = offset_text[1:].split(":")
    return timezone(sign * timedelta(hours=int(hours), minutes=int(minutes)))


def local(t, offset_text=None):
    """datetime for display: in the zone "+01:00" when given, otherwise in the Mac's current zone."""
    tz = zone(offset_text)
    return datetime.fromtimestamp(t, tz) if tz else datetime.fromtimestamp(t).astimezone()


def set_owner_offset(offset_text, phone=False):
    """Remember where the owner is now (from a client request). The phone travels with him and the Mac app may stay at home:
    while the phone has reported within PHONE_FRESH, its zone is his, whatever the Mac says — two apps in two zones flipped it
    with every poll. Returns True only when his zone really changed."""
    global owner_offset
    if not offset_text or not OFFSET.match(offset_text):
        return False
    now = time.time()
    _reported["phone" if phone else "mac"] = (offset_text, now)
    zone, when = _reported.get("phone", ("", 0.0))
    chosen = zone if zone and now - when <= PHONE_FRESH else offset_text
    if chosen == owner_offset:
        return False
    owner_offset = chosen
    return True


DATE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4})(?![\d.]*\d)")
LINE_STAMP = re.compile(r"^\s*-?\s*\d{2}\.\d{2}\.\d{4}(?: \d{2}:\d{2})?:\s*")   # "24.10.2026 14:03:" — when a memory line was written


def annotate(text, today=None):
    """Dates in a text with how far they are from today, for the model's eyes only — never stored: «12.11.2026 (через 18 дн.)»,
    «20.10.2026 (5 дн. назад)». A line's own stamp (when it was written) is left as it is. Mastra adds relative dates at display
    time, so a plan whose date has passed reads as one, and the text changes at most once a day."""
    day = (today or datetime.now()).date()

    def mark(m):
        try:
            when = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date()
        except ValueError:
            return m.group(0)
        delta = (when - day).days
        said = "сегодня" if delta == 0 else "завтра" if delta == 1 else "вчера" if delta == -1 else (
            f"через {delta} дн." if delta > 0 else f"{-delta} дн. назад")
        return f"{m.group(0)} ({said})"
    out = []
    for line in str(text or "").splitlines():
        stamp = LINE_STAMP.match(line)
        head, body = (line[:stamp.end()], line[stamp.end():]) if stamp else ("", line)
        out.append(head + DATE.sub(mark, body))
    return "\n".join(out)
