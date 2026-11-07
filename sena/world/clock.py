"""Time around the world: what time it is for a client or friend, how many hours apart, when a meeting would land. The system itself handles time zones (zoneinfo);
the network is only needed to find the zone of a city that isn't in the zone list (Open-Meteo)."""
import re
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

from sena.core.tools import string, strings, tool
from sena.world import net, weather

DAYS = weather.DAYS


class ClockError(net.WorldError):
    pass


@lru_cache(maxsize=1)
def _names():
    return {z.lower(): z for z in available_timezones()}


@lru_cache(maxsize=200)
def _zone(place):
    """(time zone, note) by name: first from the zone list (Tokyo, America/New_York), otherwise via a place search;
    for a place found by search the note is the "country": names like "Bali" can match several places, and the country shows which one was found."""
    key = place.strip().lower().replace(" ", "_")
    names = _names()
    if key in names:
        return ZoneInfo(names[key]), ""
    ends = sorted((z for low, z in names.items() if low.endswith("/" + key)), key=len)
    if ends:
        return ZoneInfo(ends[0]), ""
    try:
        found = weather.locate(place)
    except net.WorldError as e:
        raise ClockError(str(e))
    if not found.get("timezone"):
        raise ClockError(f"Для «{place}» не нашла часовой пояс. Назови город точнее")
    return ZoneInfo(found["timezone"]), found.get("country", "")


def _gap(hours):
    minutes = round(abs(hours) * 60)
    if not minutes:
        return "время то же, что у тебя"
    text = f"{minutes // 60} ч" + (f" {minutes % 60} мин" if minutes % 60 else "") if minutes >= 60 else f"{minutes} мин"
    return f"на {text} {'позже' if hours > 0 else 'раньше'}, чем у тебя"


def _offset(moment):
    seconds = moment.utcoffset().total_seconds()
    sign = "+" if seconds >= 0 else "−"
    hours, rest = divmod(abs(int(seconds)) // 60, 60)
    return f"UTC{sign}{hours}" + (f":{rest:02d}" if rest else "")


def _parse(at, zone):
    """A time like "15:00" (today in this zone) or "2026-10-20 15:00"."""
    match = re.fullmatch(r"(?:(\d{4}-\d{2}-\d{2})[ T])?(\d{1,2}):(\d{2})", str(at).strip())
    if not match:
        raise ClockError("Время пишется как 15:00 или 2026-10-20 15:00")
    day = datetime.fromisoformat(match.group(1)).date() if match.group(1) else datetime.now(zone).date()
    try:
        return datetime(day.year, day.month, day.day, int(match.group(2)), int(match.group(3)), tzinfo=zone)
    except ValueError:
        raise ClockError("Такого времени не бывает")


def world_time(places, at="", at_place=""):
    """What time it is in the given cities and how many hours apart from you. at — some time (15:00), at_place — where that time was named (you, by default): shows what it would be elsewhere then."""
    places = [p.strip() for p in (places.split(",") if isinstance(places, str) else places or []) if str(p).strip()]
    if not places:
        raise ClockError("Назови город или страну")
    here = datetime.now().astimezone()
    if at:
        source = _zone(at_place)[0] if str(at_place).strip() else here.tzinfo
        moment, head = _parse(at, source), f"Когда {str(at).strip()} {'в ' + at_place.strip() if str(at_place).strip() else 'у тебя'}:"
    else:
        moment, head = datetime.now().astimezone(), "Сейчас:"
    lines = [head]
    for place in places[:10]:
        zone, country = _zone(place)
        local = moment.astimezone(zone)
        diff = (local.utcoffset() - here.utcoffset()).total_seconds() / 3600
        lines.append(f"{place}{f' ({country})' if country else ''}: {local:%H:%M}, {DAYS[local.weekday()]} {local:%d.%m} ({_offset(local)}; {_gap(diff)})")
    return "\n".join(lines)


TOOLS = [tool("world_time", "Время в мире: сколько сейчас в городе или стране, на сколько часов разница с тобой. Для звонков и встреч: at=\"15:00\" и at_place=\"Бали\" "
                            "покажут, сколько будет в других местах, когда на Бали 15:00 (без at_place время считается по твоим часам). У названий вроде «Бали» бывает несколько мест: в ответе указана страна, проверь её; для Бали надёжнее «Denpasar».",
              {"places": strings("Города или страны: Нью-Йорк, Токио, Лондон"), "at": string("Время 15:00 или 2026-10-20 15:00; без него — сейчас"),
               "at_place": string("Где названо это время; по умолчанию у тебя")}, ["places"])]
FUNCTIONS = {"world_time": world_time}
