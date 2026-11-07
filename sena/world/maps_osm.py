"""OpenStreetMap provider: places (Nominatim and Overpass) and routes (FOSSGIS's public OSRM). No keys needed and no cost, which makes it the fallback for when TomTom
or Google don't respond or have no key. Limitations: no traffic or ratings, shared public services that ask for no more than one request per second. Data from mappers, sometimes incomplete."""
import math
import re
import threading
import time

from sena.core.safety import external
from sena.core.tools import clamp
from sena.world import net
from sena.world.maps_base import MODE_RU, MapsError, directions_link, minutes, place_link

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")   # the shared servers are often overloaded: two to choose from
ROUTING = "https://routing.openstreetmap.de/{}/route/v1/driving/{},{};{},{}"
MODES = {"drive": "routed-car", "motorbike": "routed-car", "bicycle": "routed-bike", "walk": "routed-foot"}   # travel mode -> OSRM profile
HEADERS = {"User-Agent": "Sena/1.0 (https://github.com/AStorozh/Sena)"}
PAUSE = 1.1   # Nominatim's rule: no more than one request per second
CACHE_SECONDS = 3600
# request words -> OpenStreetMap tags; anything unrecognized is searched by part of the name
TAGS = [(("кофе", "кафе", "coffee", "cafe"), 'amenity~"^(cafe)$"'), (("ресторан", "поесть", "поужин", "restaurant", "food"), 'amenity~"^(restaurant|fast_food)$"'),
        (("аптек", "pharmacy", "drugstore"), 'amenity~"^(pharmacy)$"'), (("банкомат", "atm"), 'amenity~"^(atm)$"'), (("банк", "bank"), 'amenity~"^(bank)$"'),
        (("супермаркет", "продукт", "магазин", "grocery", "supermarket", "minimart", "convenience"), 'shop~"^(supermarket|convenience)$"'),
        (("больниц", "клиник", "врач", "hospital", "clinic", "doctor"), 'amenity~"^(hospital|clinic|doctors)$"'), (("заправк", "азс", "бензин", "fuel", "gas station"), 'amenity~"^(fuel)$"'),
        (("отел", "гостиниц", "хостел", "hotel", "hostel", "guest"), 'tourism~"^(hotel|hostel|guest_house)$"'), (("спортзал", "фитнес", "gym", "fitness"), 'leisure~"^(fitness_centre)$"'),
        (("прачечн", "стирк", "laundry"), 'shop~"^(laundry|dry_cleaning)$"'), (("бар", "паб", "pub"), 'amenity~"^(bar|pub)$"'), (("пляж", "beach"), 'natural~"^(beach)$"'),
        (("парковк", "parking"), 'amenity~"^(parking)$"'), (("прокат мото", "байк", "скутер", "motorbike", "scooter", "rental"), 'amenity~"^(motorcycle_rental|bicycle_rental|car_rental)$"'),
(("туалет", "toilet"), 'amenity~"^(toilets)$"'), (("метро", "станци", "station"), 'railway~"^(station|subway_entrance)$"'),
        (("коворкинг", "cowork"), 'office~"^(coworking)$"')]
GENERIC = {"shop", "store", "магазин", "near", "рядом", "the", "and", "для"}
_last = 0.0
_pace = threading.Lock()   # tool calls run in parallel: two of them mustn't ask Nominatim in the same second


def _pause():
    global _last
    with _pace:
        wait = _last + PAUSE - time.time()
        if wait > 0:
            time.sleep(wait)
        _last = time.time()


def _explain(reply):
    if reply.status_code in (429, 503, 504):
        return "OpenStreetMap сейчас перегружен: подожди минуту"
    return None


def geocode(text, bias=None):
    """Address or place name -> (lat, lon, Nominatim's own name for it).
    bias — (lat, lon): same-named places near this point come first (a soft bounding box around the point, not a hard one)."""
    _pause()
    params = {"q": text, "format": "jsonv2", "limit": 1, "accept-language": "ru"}
    if bias:
        params.update(viewbox=f"{bias[1] - 1},{bias[0] + 1},{bias[1] + 1},{bias[0] - 1}", bounded=0)
    found = net.as_json(net.get(NOMINATIM, params, HEADERS, timeout=30, ttl=CACHE_SECONDS, who="OpenStreetMap", error=MapsError, explain=_explain), "OpenStreetMap", MapsError)
    if not found:
        raise MapsError(f"Не нашла на карте: «{text}». Уточни адрес или название.")
    return float(found[0]["lat"]), float(found[0]["lon"]), found[0].get("display_name", text)


def _meters(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def _filter(query):
    text = query.lower()
    for words, tag in TAGS:
        if any(w in text for w in words):
            return f"[{tag}]"
    words = [w for w in re.findall(r"\w{3,}", text) if w not in GENERIC]   # search anything unrecognized by parts of the name
    return f'["name"~"{"|".join(words) or "."}",i]'


def _overpass(query):
    last = None
    for server in OVERPASS:
        try:
            return net.as_json(net.get(server, {"data": query}, HEADERS, timeout=60, ttl=CACHE_SECONDS, who="OpenStreetMap", error=MapsError, explain=_explain), "OpenStreetMap", MapsError)
        except MapsError as e:
            last = e
    raise last


def places(query, near=None, limit=5):
    if not near:
        raise MapsError("Для поиска мест укажи, рядом с чем искать: адрес или район (например «Mũi Né, Phan Thiết»).")
    lat, lon, where = geocode(near)
    tag, limit = _filter(str(query)), clamp(limit, 5, 1, 10)
    found = []
    for radius in (3000, 8000):
        data = _overpass(f"[out:json][timeout:25];nwr(around:{radius},{lat},{lon}){tag};out center 80;")
        found = [e for e in data.get("elements", []) if (e.get("tags") or {}).get("name")]
        if found:
            break
    if not found:
        return f"Рядом с «{where}» по запросу «{query}» в OpenStreetMap ничего нет. Данные картографов бывают неполными: попробуй другое слово."
    rows = []
    for e in found:
        pos = (e.get("lat"), e.get("lon")) if "lat" in e else (e["center"]["lat"], e["center"]["lon"])
        rows.append((_meters((lat, lon), pos), e["tags"], pos))
    lines = []
    for i, (dist, t, pos) in enumerate(sorted(rows, key=lambda r: r[0])[:limit], 1):
        street = " ".join(x for x in (t.get("addr:street"), t.get("addr:housenumber")) if x) or t.get("addr:full") or "адрес не указан"
        lines.append(f"{i}. {t['name']} — {street}; {dist:.0f} м" + (f"; {t['opening_hours']}" if t.get("opening_hours") else "")
                     + (f"; тел. {t.get('phone') or t.get('contact:phone')}" if t.get("phone") or t.get("contact:phone") else "") + (f"; {t.get('website') or t.get('contact:website')}" if t.get("website") or t.get("contact:website") else "")
                     + f"; {place_link(*pos)}")
    return external("\n".join(lines), "OpenStreetMap: места")


def route(origin, destination, mode="drive", depart=None):
    if mode not in MODES:
        return f"Маршруты на общественном транспорте OpenStreetMap не строит. Расписание откроется в Google Картах: {directions_link(origin, destination, mode)}"
    a = geocode(origin)
    b = geocode(destination, bias=(a[0], a[1]))
    data = net.as_json(net.get(ROUTING.format(MODES[mode], a[1], a[0], b[1], b[0]), {"overview": "false"}, HEADERS, timeout=40, who="OpenStreetMap", error=MapsError, explain=_explain),
                       "OpenStreetMap", MapsError)
    routes = data.get("routes") or []
    if data.get("code") != "Ok" or not routes:
        return f"Маршрут от «{origin}» до «{destination}» ({MODE_RU[mode].lower()}) построить не удалось"
    line = f"{MODE_RU[mode]}: {minutes(routes[0]['duration'])}, {routes[0]['distance'] / 1000:.1f} км (без пробок: у OpenStreetMap их нет" + ("; для мотоцикла посчитано как для машины" if mode == "motorbike" else "") + ")"
    return external(f"{line}.\nОт: {a[2]}\nДо: {b[2]}\nОткрыть на карте: {directions_link(origin, destination, mode)}", "OpenStreetMap: маршрут")
