"""Shared code for map providers: the error class, travel modes, time in minutes, a route link to Google Maps (the link works without a key)."""
from urllib.parse import quote_plus

from sena.world import net

MODES = ("drive", "walk", "bicycle", "transit", "motorbike")
MODE_RU = {"drive": "На машине", "walk": "Пешком", "bicycle": "На велосипеде", "transit": "На транспорте", "motorbike": "На мотоцикле"}
_TRAVEL = {"drive": "driving", "walk": "walking", "bicycle": "bicycling", "transit": "transit", "motorbike": "driving"}


class MapsError(net.WorldError):
    pass


def minutes(seconds):
    m = (int(float(str(seconds).rstrip("s"))) + 30) // 60   # round to the nearest minute, half rounds up
    return f"{m // 60} ч {m % 60} мин" if m >= 60 else f"{m} мин"


def directions_link(origin, destination, mode):
    return (f"https://www.google.com/maps/dir/?api=1&origin={quote_plus(origin)}"
            f"&destination={quote_plus(destination)}&travelmode={_TRAVEL[mode]}")


def place_link(lat, lon):
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
