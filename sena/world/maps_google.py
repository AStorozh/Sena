"""Google Maps Platform provider: places (Places API New) and routes (Routes API). Key GOOGLE_MAPS_KEY.
Unavailable for billing accounts in some regions (including Artemiy's, Vietnam): TomTom is used instead in that case."""
from datetime import datetime, timezone

from sena import config
from sena.core import costs, moment
from sena.core.safety import external
from sena.core.tools import clamp
from sena.world import net
from sena.world.maps_base import MODE_RU, MapsError, directions_link, minutes

PLACES = "https://places.googleapis.com/v1/places:searchText"
ROUTES = "https://routes.googleapis.com/directions/v2:computeRoutes"
MODES = {"drive": "DRIVE", "walk": "WALK", "bicycle": "BICYCLE", "transit": "TRANSIT", "motorbike": "TWO_WHEELER"}


def _key():
    if not config.GOOGLE_MAPS_KEY:
        raise MapsError("Ключ Google Карт не задан. В Google Cloud (проект Sena) включи Places API (New) и Routes API, "
                        "создай API-ключ и впиши в .env строкой GOOGLE_MAPS_KEY=..., затем перезапусти Сену. "
                        "Для карт нужен платёжный аккаунт (в бесплатных пределах списаний нет).")
    return config.GOOGLE_MAPS_KEY


def _explain(reply):
    try:
        error = reply.json()["error"]
        message, state = error.get("message", ""), error.get("status", "")
    except (ValueError, KeyError):   # response isn't JSON, or has no error field
        message, state = reply.text[:300], ""
    low = message.lower()
    if "api keys are not supported" in low:
        return ("Этот ключ для Карт не подходит (ключи вида AQ... работают только с Gemini). Создай обычный ключ: "
                "Google Cloud → APIs & Services → Credentials → Create credentials → API key, "
                "ограничь его Places API (New) и Routes API и впиши в .env как GOOGLE_MAPS_KEY.")
    if "billing" in low or state == "BILLING_DISABLED":
        return "Для Google Карт нужен платёжный аккаунт: включи Billing в проекте Sena. Ответ Google: " + message[:200]
    if state in ("PERMISSION_DENIED", "SERVICE_DISABLED") or "not been used" in low or "disabled" in low or "blocked" in low:
        return ("Google Карты отказали: проверь, что в проекте включены Places API (New) и Routes API, ключ не ограничен "
                "другими API, а платёжный аккаунт проекта находится в регионе, где Google Maps Platform доступна "
                "(во Вьетнаме она недоступна). Ответ Google: " + message[:200])
    return f"Google Карты {reply.status_code}: {message[:300]}"


def _instant(depart):
    """The departure as Google's UTC timestamp. A time without a zone is the owner's local time (his device's zone, else the
    Mac's), not UTC: a bare 08:30 sent as 08:30Z was seven hours off in Vietnam; "-05:00" used to become "-05:00Z"."""
    try:
        when = datetime.fromisoformat(str(depart).strip().replace("Z", "+00:00"))
    except ValueError:
        raise MapsError("depart — время ISO с поясом, например 2026-10-16T08:30:00+07:00")
    if when.tzinfo is None:
        owner = moment.zone(moment.owner_offset)
        when = when.replace(tzinfo=owner) if owner else when.astimezone()
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _call(url, body, fields):
    reply = net.send("POST", url, body, headers={"X-Goog-Api-Key": _key(), "X-Goog-FieldMask": fields},
                     timeout=30, who="Google Maps", error=MapsError, explain=_explain)
    costs.request("google-maps", "routes" if "routes.googleapis" in url else "places")   # the price per call depends on the field mask: reported as "no price"
    return net.as_json(reply, "Google Maps", MapsError)


def places(query, near=None, limit=5):
    text = f"{query} рядом с {near}" if near else query
    data = _call(PLACES, {"textQuery": text, "languageCode": "ru", "maxResultCount": clamp(limit, 5, 1, 10)},
                 "places.displayName,places.formattedAddress,places.rating,places.userRatingCount,"
                 "places.currentOpeningHours.openNow,places.googleMapsUri")
    found = data.get("places", [])
    if not found:
        return "Ничего не нашла по этому запросу"
    lines = []
    for i, p in enumerate(found, 1):
        opened = p.get("currentOpeningHours", {}).get("openNow")
        lines.append(f"{i}. {p['displayName']['text']} — {p.get('formattedAddress', 'адрес не указан')}"
                     + (f"; рейтинг {p['rating']} ({p.get('userRatingCount', 0)} отзывов)" if p.get("rating") else "")
                     + ("; открыто сейчас" if opened is True else "; сейчас закрыто" if opened is False else "")
                     + (f"; {p['googleMapsUri']}" if p.get("googleMapsUri") else ""))
    return external("\n".join(lines), "Google Карты: места")


def route(origin, destination, mode="drive", depart=None):
    body = {"origin": {"address": origin}, "destination": {"address": destination}, "travelMode": MODES[mode],
            "languageCode": "ru", "units": "METRIC"}
    if mode in ("drive", "motorbike"):
        body["routingPreference"] = "TRAFFIC_AWARE"
    if depart:
        body["departureTime"] = _instant(depart)
    data = _call(ROUTES, body, "routes.duration,routes.staticDuration,routes.distanceMeters,routes.description")
    routes = data.get("routes")
    if not routes:
        return f"Маршрут от «{origin}» до «{destination}» ({MODE_RU[mode].lower()}) построить не удалось"
    r = routes[0]
    line = f"{MODE_RU[mode]}: {minutes(r['duration'])}, {r.get('distanceMeters', 0) / 1000:.1f} км"
    if r.get("staticDuration") and r["staticDuration"] != r["duration"] and mode in ("drive", "motorbike"):
        line += f" (без пробок {minutes(r['staticDuration'])})"
    if r.get("description"):
        line += f", через {r['description']}"
    return external(f"{line}.\nОткрыть на карте: {directions_link(origin, destination, mode)}", "Google Карты: маршрут")
