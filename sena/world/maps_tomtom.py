"""TomTom provider: places (Search API) and routes (Routing API) with traffic, including for motorbikes.
Free monthly limits, no card needed. Key TOMTOM_KEY from developer.tomtom.com. TomTom has no ratings or reviews."""
from urllib.parse import quote

from sena import config
from sena.core import costs
from sena.core.safety import external
from sena.core.tools import clamp
from sena.world import net
from sena.world.maps_base import MODE_RU, MapsError, directions_link, minutes, place_link

SEARCH = "https://api.tomtom.com/search/2"
ROUTING = "https://api.tomtom.com/routing/1/calculateRoute"
MODES = {"drive": "car", "walk": "pedestrian", "bicycle": "bicycle", "motorbike": "motorcycle"}   # TomTom has no transit mode
LANGUAGE = "ru-RU"


def _key():
    if not config.TOMTOM_KEY:
        raise MapsError("Ключ TomTom не задан: зарегистрируйся на developer.tomtom.com (бесплатно, карта не нужна), "
                        "скопируй ключ из раздела Keys и впиши в .env строкой TOMTOM_KEY=..., затем перезапусти Сену.")
    return config.TOMTOM_KEY


def _explain(reply):
    try:
        data = reply.json()
    except ValueError:   # response isn't JSON
        data = {}
    detail = (data.get("detailedError") or {}).get("message") or data.get("error") or reply.text[:200]
    if reply.status_code in (401, 403):
        return (f"TomTom не принял ключ или сервис не включён для него: {detail}. "
                "Проверь ключ в Dashboard на developer.tomtom.com (раздел Keys, у ключа должны быть включены Search и Routing).")
    if reply.status_code == 429:
        return "TomTom: исчерпан бесплатный лимит запросов, попробуй позже."
    return f"TomTom {reply.status_code}: {detail}"


def _get(url, params=None):
    reply = net.get(url, {"key": _key(), **(params or {})}, timeout=30, who="TomTom", error=MapsError, explain=_explain)
    costs.request("tomtom", "routing" if "/routing/" in url else "search")   # a free monthly limit, so we keep count
    return net.as_json(reply, "TomTom", MapsError)


def geocode(text, bias=None):
    """Address or place name -> (lat, lon, TomTom's own name for it). Fuzzy search: understands addresses, venues and stations alike.
    bias — (lat, lon): same-named places near this point are ranked first (otherwise "Sailing Club" could be found on the other side of the world)."""
    params = {"limit": 1, "language": LANGUAGE}
    if bias:
        params.update(lat=bias[0], lon=bias[1])
    data = _get(f"{SEARCH}/search/{quote(text)}.json", params)
    found = data.get("results") or []
    if not found:
        raise MapsError(f"Не нашла на карте: «{text}». Уточни адрес или название.")
    top = found[0]
    return top["position"]["lat"], top["position"]["lon"], top.get("address", {}).get("freeformAddress", text)


def places(query, near=None, limit=5):
    if not near:
        raise MapsError("Для поиска мест укажи, рядом с чем искать: адрес или район (например «Mũi Né, Phan Thiết»).")
    lat, lon, where = geocode(near)
    data = _get(f"{SEARCH}/poiSearch/{quote(query)}.json",
                {"lat": lat, "lon": lon, "radius": 5000, "limit": clamp(limit, 5, 1, 10), "language": LANGUAGE})
    found = data.get("results") or []
    if not found:
        return f"Рядом с «{where}» по запросу «{query}» ничего не нашла"
    lines = []
    for i, item in enumerate(found, 1):
        poi, pos = item.get("poi", {}), item.get("position", {})
        category = (poi.get("categories") or [""])[0]
        lines.append(f"{i}. {poi.get('name', 'без названия')} — {item.get('address', {}).get('freeformAddress', 'адрес не указан')}"
                     + (f"; {category}" if category else "")
                     + (f"; {item['dist']:.0f} м" if item.get("dist") is not None else "")
                     + (f"; тел. {poi['phone']}" if poi.get("phone") else "")
                     + (f"; {poi['url']}" if poi.get("url") else "")
                     + (f"; {place_link(pos['lat'], pos['lon'])}" if pos else ""))
    return external("\n".join(lines), "TomTom: места")


def route(origin, destination, mode="drive", depart=None):
    if mode not in MODES:
        link = directions_link(origin, destination, mode)
        return (f"TomTom не строит маршруты на общественном транспорте. Расписание откроется в Google Картах: {link}")
    a = geocode(origin)
    b = geocode(destination, bias=(a[0], a[1]))   # the destination is searched near the origin
    params = {"travelMode": MODES[mode], "routeType": "fastest", "traffic": "true", "computeTravelTimeFor": "all"}
    if depart:
        params["departAt"] = depart
    data = _get(f"{ROUTING}/{a[0]},{a[1]}:{b[0]},{b[1]}/json", params)
    routes = data.get("routes") or []
    if not routes:
        return f"Маршрут от «{origin}» до «{destination}» ({MODE_RU[mode].lower()}) построить не удалось"
    s = routes[0]["summary"]
    line = f"{MODE_RU[mode]}: {minutes(s['travelTimeInSeconds'])}, {s['lengthInMeters'] / 1000:.1f} км"
    free = s.get("noTrafficTravelTimeInSeconds")
    if free and mode in ("drive", "motorbike") and abs(s["travelTimeInSeconds"] - free) >= 60:
        line += f" (без пробок {minutes(free)})"
    return external(f"{line}.\nОт: {a[2]}\nДо: {b[2]}\nОткрыть на карте: {directions_link(origin, destination, mode)}", "TomTom: маршрут")
