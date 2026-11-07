"""Maps for Sena: place search and routes. Providers in order: TomTom (if there's a key: free, no card needed, has traffic and motorbike), Google Maps Platform (if there's a key:
richer, ratings, reviews, but paid and unavailable for billing accounts in Vietnam) and OpenStreetMap (always, no keys needed: the fallback, no traffic or ratings).
If one doesn't respond, the next is tried, and the response says whose data it is. Daily limit: LIMIT_PER_DAY, currently unlimited."""
from sena import config
from sena.core import limits
from sena.core.tools import string, tool
from sena.world import maps_google, maps_osm, maps_tomtom
from sena.world.maps_base import MODES, MapsError

LIMIT_PER_DAY = None   # map requests per day; None — unlimited (Artemiy's decision), but still counted


NAMES = {maps_tomtom: "TomTom", maps_google: "Google Maps", maps_osm: "OpenStreetMap"}


def _providers():
    chain = [p for p, key in ((maps_tomtom, config.TOMTOM_KEY), (maps_google, config.GOOGLE_MAPS_KEY)) if key]
    return chain + [maps_osm]   # OpenStreetMap needs no keys: the final fallback, so maps always work


def _run(call):
    try:
        limits.check("maps", LIMIT_PER_DAY, "запросов к Картам")
    except limits.LimitReached as e:
        raise MapsError(str(e))
    failed = []
    for provider in _providers():
        try:
            result = call(provider)
        except MapsError as e:
            failed.append(f"{NAMES[provider]}: {e}")
            continue
        limits.spend("maps")   # "didn't help", not "didn't answer": the main one may have answered "not found"
        return (f"(Основной источник не помог: {'; '.join(failed)}. Данные {NAMES[provider]}.)\n" if failed else "") + result
    raise MapsError("Ни один источник карт не ответил. " + " | ".join(failed))


def places(query, near=None, limit=5):
    """Places by description: coffee shops, pharmacies, stores. near — what to search near (an address, a district)."""
    return _run(lambda p: p.places(query, near, limit))


def route(origin, destination, mode="drive", depart=None):
    """How long the trip takes and how long the route is. depart — ISO departure time (now, by default)."""
    if mode not in MODES:
        raise MapsError(f"mode — одно из: {', '.join(MODES)}")
    return _run(lambda p: p.route(origin, destination, mode, depart))


TOOLS = [
    tool("place_search", "Найти места на карте: кофейни, аптеки, магазины, адреса, с телефоном и ссылкой на карту. "
                         "Обязательно укажи near (адрес или район): без него искать негде. Рейтингов и отзывов нет. Нет ключей или основной источник не ответил: ищет по OpenStreetMap (часы работы бывают, данные местами неполные).",
         {"query": string("Что ищем, например «кофейня»"), "near": string("Рядом с чем: адрес или район, лучше с городом и страной"),
          "limit": {"type": "integer", "description": "Сколько мест, по умолчанию 5"}}, ["query", "near"]),
    tool("route", "Сколько ехать или идти от точки до точки (с пробками, если работает TomTom; запасной OpenStreetMap считает без пробок) и ссылка на маршрут в Google Картах. "
                  "Режимы: drive, walk, bicycle, motorbike (мотоцикл), transit (только ссылка).",
         {"origin": string("Откуда: адрес или название места, лучше с городом и страной"),
          "destination": string("Куда: адрес или название места"),
          "mode": {**string("Как: drive, walk, bicycle, transit, motorbike"), "enum": list(MODES)},
          "depart": string("Когда выезжать, ISO-время с часовым поясом (2026-10-16T08:30:00+07:00), необязательно")},
         ["origin", "destination"]),
]
FUNCTIONS = {"place_search": places, "route": route}
