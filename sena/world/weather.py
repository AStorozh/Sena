"""Weather via Open-Meteo: free, no key needed. The place is looked up by name in any language.
lookup — the forecast as data (the system check tries it too), weather — the same in words for Sena."""
from datetime import date

from sena.core.tools import clamp, string, tool
from sena.world import net

GEO = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
# WMO weather codes -> Russian
CODES = {0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "пасмурно", 45: "туман", 48: "изморозь",
         51: "слабая морось", 53: "морось", 55: "сильная морось", 56: "ледяная морось", 57: "сильная ледяная морось",
         61: "слабый дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь", 67: "сильный ледяной дождь",
         71: "слабый снег", 73: "снег", 75: "сильный снег", 77: "снежные зёрна", 80: "слабый ливень", 81: "ливень",
         82: "сильный ливень", 85: "снегопад", 86: "сильный снегопад", 95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом"}
DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


class WeatherError(net.WorldError):
    pass


def locate(place):
    found = net.as_json(net.get(GEO, {"name": place, "count": 1, "language": "ru"}, who="Open-Meteo", error=WeatherError), "Open-Meteo", WeatherError).get("results")
    if not found:
        raise WeatherError(f"Места «{place}» не нашла. Назови город точнее, например «Ho Chi Minh City» или «Санкт-Петербург».")
    return found[0]


def lookup(place, days=3):
    """Weather now and by day (1-7) as data: place, now, days."""
    where = locate(place)
    days = clamp(days, 3, 1, 7)
    data = net.as_json(net.get(FORECAST, who="Open-Meteo", error=WeatherError, params={
        "latitude": where["latitude"], "longitude": where["longitude"], "timezone": "auto", "forecast_days": days,
        "wind_speed_unit": "ms",
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max,uv_index_max",
    }), "Open-Meteo", WeatherError)
    now, daily = data["current"], data["daily"]
    title = ", ".join(dict.fromkeys(p for p in (where["name"], where.get("admin1"), where.get("country")) if p))   # no duplicates: "Moscow, Moscow"
    out = {"place": title, "now": {"temp": now["temperature_2m"], "feels": now["apparent_temperature"], "code": now["weather_code"],
                                   "text": CODES.get(now["weather_code"], "без описания"), "wind": now["wind_speed_10m"],
                                   "humidity": now["relative_humidity_2m"]}, "days": []}
    for i, day in enumerate(daily["time"]):
        d = date.fromisoformat(day)
        out["days"].append({"date": day, "label": "Сегодня" if i == 0 else "Завтра" if i == 1 else DAYS[d.weekday()].capitalize(),
                            "min": daily["temperature_2m_min"][i], "max": daily["temperature_2m_max"][i], "code": daily["weather_code"][i],
                            "text": CODES.get(daily["weather_code"][i], ""), "rain": daily["precipitation_sum"][i] or 0,
                            "rain_chance": daily["precipitation_probability_max"][i] or 0, "wind": daily["wind_speed_10m_max"][i] or 0,
                            "uv": daily["uv_index_max"][i] or 0})
    return out


def weather(place, days=3):
    """Weather now and a forecast for several days (1-7), in words."""
    w = lookup(place, days)
    now = w["now"]
    lines = [f"{w['place']}. Сейчас {now['temp']:.0f}°C (ощущается как {now['feels']:.0f}), {now['text']}, ветер {now['wind']:.0f} м/с, "
             f"влажность {now['humidity']}%."]
    for d in w["days"]:
        lines.append(f"{d['label']} ({d['date'][8:10]}.{d['date'][5:7]}): {d['min']:.0f}…{d['max']:.0f}°C, {d['text']}"
                     + (f", осадки {d['rain']:.0f} мм (вероятность {d['rain_chance']}%)" if d["rain"] or d["rain_chance"] else "")
                     + f", ветер до {d['wind']:.0f} м/с, УФ {d['uv']:.0f}")
    return "\n".join(lines)


TOOLS = [tool("weather", "Погода сейчас и прогноз на несколько дней для города или места. Бесплатно.",
              {"place": string("Город или место, например Ho Chi Minh City или Москва"),
               "days": {"type": "integer", "description": "На сколько дней прогноз, 1–7, по умолчанию 3"}}, ["place"])]
FUNCTIONS = {"weather": weather}
