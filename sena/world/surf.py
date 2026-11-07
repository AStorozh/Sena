"""Surf forecast: where to go ride. Data from Open-Meteo (wave, swell, period, wind, tides), free and no key needed.
Spots live in surf_spots.json (the beach-facing direction is approximate, adjust it to taste). The model is coarse: a grid of about 10 km,
and island or reef shadowing isn't accounted for, so this is a guide, not a substitute for looking at the water yourself."""
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sena.core.tools import clamp, string, tool
from sena.world import net

MARINE = "https://marine-api.open-meteo.com/v1/marine"
FORECAST = "https://api.open-meteo.com/v1/forecast"
SPOTS_FILE = Path(__file__).with_name("surf_spots.json")
PARTS = {"morning": (6, 10), "midday": (10, 14), "afternoon": (14, 18), "all": (6, 18)}
PARTS_RU = {"morning": "утро (6–10)", "midday": "день (10–14)", "afternoon": "после обеда (14–18)", "all": "весь день (6–18)"}
LEVELS = {"beginner": (0.4, 1.0, 1.4), "intermediate": (0.8, 1.7, 2.6), "advanced": (1.2, 2.5, 4.0)}   # too small, ideal up to, too big
LEVEL_RU = {"beginner": "новичок", "intermediate": "средний", "advanced": "опытный"}
LEVEL_ORDER = ["beginner", "intermediate", "advanced"]
COMPASS = ["С", "ССВ", "СВ", "ВСВ", "В", "ВЮВ", "ЮВ", "ЮЮВ", "Ю", "ЮЮЗ", "ЮЗ", "ЗЮЗ", "З", "ЗСЗ", "СЗ", "ССЗ"]
CACHE_SECONDS = 600   # the forecast changes slowly: no need to hit the free API more than necessary


def spots():
    """The spots of the file (Bali's)."""
    return json.loads(SPOTS_FILE.read_text(encoding="utf-8"))


def angle(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def compass(deg):
    return COMPASS[round(deg / 22.5) % 16]


def exposure(facing, swell_from):
    """0..1: how much swell reaches a beach facing this direction (swell doesn't arrive from the sides or behind)."""
    d = angle(facing, swell_from)
    return 0.0 if d >= 90 else math.cos(math.radians(d)) ** 0.7


def wind_kind(facing, wind_from, speed):
    """(quality 0..1, word). Offshore — the wind blows from land to ocean, which "grooms" the wave."""
    r = angle(facing, wind_from)   # 0 — wind from the ocean (onshore), 180 — from land (offshore)
    if speed < 6:
        return 0.9, "штиль"
    if r >= 135:
        quality, word = 1.0, "оффшор"
    elif r >= 90:
        quality, word = 0.8, "кросс-оффшор"
    elif r >= 45:
        quality, word = 0.5, "кроссшор"
    else:
        quality, word = 0.2, "оншор"
    if speed > 30:
        quality *= 0.3
    elif speed > 20:
        quality *= 0.6
    elif speed > 12 and word in ("оффшор", "кросс-оффшор"):
        quality *= 0.9
    return quality, word


def size_score(height, level):
    low, ideal, high = LEVELS[level]
    if height < 0.3:
        return 0.05
    if height < low:
        return 0.2 + 0.8 * (height - 0.3) / (low - 0.3)
    if height <= ideal:
        return 1.0
    if height <= high:
        return 1.0 - 0.5 * (height - ideal) / (high - ideal)
    return max(0.1, 0.5 - 0.2 * (height - high))


def period_score(period):
    return 0.5 if period < 6 else 0.75 if period < 9 else 0.9 if period < 11 else 1.0


def hour_score(spot, h, level):
    exp = exposure(spot["facing"], h["swell_dir"])
    height = h["swell_h"] * exp + 0.3 * h["wind_wave_h"]
    quality, word = wind_kind(spot["facing"], h["wind_dir"], h["wind_speed"])
    score = 100 * size_score(height, level) * period_score(h["swell_period"]) * quality * (0.35 + 0.65 * min(1.0, exp * 1.4))
    return {"score": score, "height": height, "exposure": exp, "wind_word": word}


def _fetch(url, params):
    data = net.as_json(net.get(url, params, timeout=30, who="Open-Meteo", ttl=CACHE_SECONDS), "Open-Meteo")
    return data if isinstance(data, list) else [data]


def _local_now(offset_seconds):
    # the current hour at the spot's location (Open-Meteo's response gives the time zone offset)
    return datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)


def _num(arr, i, default=0.0):
    v = arr[i] if i < len(arr) else None
    return default if v is None else v


def _gather(day, part, level, spot):
    """Hourly data for spots on the chosen day and part of the day. A morning that has already passed is replaced by the remaining hours
    or by tomorrow (note explains why)."""
    if part not in PARTS:
        raise net.WorldError(f"part — одно из: {', '.join(PARTS)}")
    if level not in LEVELS:
        raise net.WorldError(f"level — одно из: {', '.join(LEVELS)}")
    day = clamp(day, 0, 0, 6)
    chosen = spots()
    if spot:
        chosen = [s for s in chosen if spot.lower() in s["name"].lower()]
        if not chosen:
            raise net.WorldError(f"Спота «{spot}» нет в списке. Есть: " + ", ".join(s["name"] for s in spots()))
    else:   # we don't recommend spots harder than the rider's level: advanced reefs are dangerous for intermediates and beginners
        chosen = [s for s in chosen if LEVEL_ORDER.index(s["level"]) <= LEVEL_ORDER.index(level)]
    base = {"latitude": ",".join(str(s["lat"]) for s in chosen), "longitude": ",".join(str(s["lon"]) for s in chosen),
            "timezone": "auto", "forecast_days": day + 1}
    sea = _fetch(MARINE, {**base, "hourly": "wave_height,swell_wave_height,swell_wave_period,swell_wave_direction,wind_wave_height,"
                                           "sea_level_height_msl,sea_surface_temperature"})
    air = _fetch(FORECAST, {**base, "wind_speed_unit": "kmh",
                            "hourly": "wind_speed_10m,wind_direction_10m,precipitation,temperature_2m,uv_index"})
    first, last = PARTS[part]
    note = ""
    if day == 0:   # if the morning has already passed, there's no point showing it: either the remaining part, or tomorrow
        now = _local_now(sea[0].get("utc_offset_seconds", 0)).hour
        if now >= last:
            g = _gather(1, part, level, spot)
            g["note"] = f"Сегодня {PARTS_RU[part]} уже прошло, показываю завтра.\n" + g["note"]
            return g
        if now > first:
            first, note = now, f"Сейчас {now}:00, показываю оставшиеся часы.\n"
    results, target_day = [], None
    for s, m, a in zip(chosen, sea, air):
        mh, ah = m["hourly"], a["hourly"]
        today = date.fromisoformat(mh["time"][0][:10])   # date in the spot's own time zone
        target_day = (today + timedelta(days=day)).isoformat()
        hours = []
        for i, stamp in enumerate(mh["time"]):
            if not stamp.startswith(target_day) or not first <= int(stamp[11:13]) < last:
                continue
            if mh["swell_wave_height"][i] is None:
                continue
            h = {"hour": int(stamp[11:13]), "swell_h": _num(mh["swell_wave_height"], i), "swell_period": _num(mh["swell_wave_period"], i),
                 "swell_dir": _num(mh["swell_wave_direction"], i), "wind_wave_h": _num(mh["wind_wave_height"], i),
                 "wind_speed": _num(ah["wind_speed_10m"], i), "wind_dir": _num(ah["wind_direction_10m"], i),
                 "rain": _num(ah["precipitation"], i), "temp": _num(ah["temperature_2m"], i), "uv": _num(ah["uv_index"], i),
                 "sea_level": mh["sea_level_height_msl"][i], "water": mh["sea_surface_temperature"][i]}
            h.update(hour_score(s, h, level))
            hours.append(h)
        if hours:
            results.append((s, hours))
    return {"day": day, "part": part, "level": level, "target_day": target_day, "note": note, "results": results}


def _line(hours):
    n = len(hours)
    avg = {k: sum(h[k] for h in hours) / n for k in ("height", "swell_period", "swell_h", "wind_speed", "score", "rain")}
    best = max(hours, key=lambda h: h["score"])
    mid = hours[n // 2]
    return avg, best, mid


def _verdict(score):
    return "отлично" if score >= 70 else "хорошо" if score >= 50 else "так себе" if score >= 30 else "слабо"


def _rank(g):
    """Spot ranking as data, built from _gather's output: {when, date, part, level, note, rows, extra}."""
    rows = []
    for s, hours in g["results"]:
        avg, best, mid = _line(hours)
        rows.append({"name": s["name"], "area": s["area"], "kind": s["kind"], "level": s["level"], "level_ru": LEVEL_RU[s["level"]],
                     "score": avg["score"], "verdict": _verdict(avg["score"]), "height": avg["height"], "swell_h": avg["swell_h"],
                     "period": mid["swell_period"], "swell_from": compass(mid["swell_dir"]), "wind": avg["wind_speed"],
                     "wind_from": compass(mid["wind_dir"]), "wind_word": mid["wind_word"], "best_hour": best["hour"], "rain": avg["rain"],
                     "_hours": hours, "_mid": mid})
    rows.sort(key=lambda r: -r["score"])
    extra = {}
    if rows:
        best_hours, mid = rows[0]["_hours"], rows[0]["_mid"]   # water, air and tide are taken from the best spot
        sea = [h["sea_level"] for h in best_hours if h["sea_level"] is not None]
        extra = {"water": mid.get("water"), "air": mid["temp"], "uv": max(h["uv"] for h in best_hours), "rain": rows[0]["rain"] > 0.3,
                 "tide": ("прилив растёт" if sea[-1] > sea[0] + 0.05 else "отлив" if sea[-1] < sea[0] - 0.05 else "уровень стоит") if sea else None,
                 "tide_from": sea[0] if sea else None, "tide_to": sea[-1] if sea else None}
    for r in rows:
        del r["_hours"], r["_mid"]
    when = "сегодня" if g["day"] == 0 else "завтра" if g["day"] == 1 else g["target_day"]
    return {"when": when, "date": g["target_day"], "part": PARTS_RU[g["part"]], "level": LEVEL_RU[g["level"]], "note": g["note"].strip(),
            "rows": rows, "extra": extra}


def forecast(day=0, part="morning", level="intermediate", spot=None):
    """Spot ranking for the chosen day and part of the day; with spot — hourly for a single place. In words, for Sena."""
    g = _gather(day, part, level, spot)
    if not g["results"]:
        return "Модель не дала данных на это время"
    return g["note"] + (_spot_hours(g["results"][0], part, g["target_day"]) if spot else _ranking(_rank(g)))


def _ranking(data):
    rows = data["rows"]
    out = [f"Серф {data['when']}, {data['part']}, уровень: {data['level']} (споты сложнее не показываю). Лучшее:"]
    for rank_, r in enumerate(rows[:5], 1):
        out.append(f"{rank_}. {r['name']} ({r['area']}, {r['kind']}, {r['level_ru']}) — {r['verdict']}, {r['score']:.0f}/100: волна {r['height']:.1f} м, "
                   f"зыбь {r['swell_h']:.1f} м {r['period']:.0f} с с {r['swell_from']}, "
                   f"ветер {r['wind']:.0f} км/ч {r['wind_from']} ({r['wind_word']}); лучший час {r['best_hour']}:00")
    if len(rows) > 1:
        out.append(f"Хуже всего: {rows[-1]['name']} ({rows[-1]['score']:.0f}/100).")
    x = data["extra"]
    extra = []
    if x.get("water") is not None:
        extra.append(f"вода {x['water']:.0f}°C")
    extra.append(f"воздух {x['air']:.0f}°C, УФ до {x['uv']:.0f}")
    if x.get("tide"):
        extra.append(f"{x['tide']} ({x['tide_from']:+.1f} → {x['tide_to']:+.1f} м)")
    if x.get("rain"):
        extra.append("возможен дождь")
    out.append("; ".join(extra) + ".")
    out.append("Модель грубая (сетка ~10 км, соседние споты получают почти одинаковые данные, различает их направление пляжа): "
               "ориентир, а не замена взгляду на воду.")
    return "\n".join(out)


def _spot_hours(result, part, target_day):
    s, hours = result
    out = [f"{s['name']} ({s['area']}, {s['kind']}, {LEVEL_RU.get(s['level'], s['level'])}), {target_day}, {PARTS_RU[part]}. {s['note']}."]
    for h in hours:
        out.append(f"{h['hour']:02d}:00 — оценка {h['score']:.0f}: волна {h['height']:.1f} м, зыбь {h['swell_h']:.1f} м {h['swell_period']:.0f} с с "
                   f"{compass(h['swell_dir'])}, ветер {h['wind_speed']:.0f} км/ч {compass(h['wind_dir'])} ({h['wind_word']})"
                   + (f", вода {h['water']:.0f}°C" if h["water"] is not None else "")
                   + (f", уровень моря {h['sea_level']:+.1f} м" if h["sea_level"] is not None else ""))
    return "\n".join(out)


TOOLS = [tool("surf_forecast",
              "Сёрф-прогноз по спотам Бали: куда ехать кататься. Рейтинг мест на день и часть дня (волна, зыбь, период, ветер оффшор или оншор, "
              "прилив, вода). Со spot — по часам для одного места. Бесплатно.",
              {"day": {"type": "integer", "description": "0 — сегодня, 1 — завтра и так далее до 6"},
               "part": {**string("morning 6–10, midday 10–14, afternoon 14–18, all весь день; по умолчанию morning"), "enum": list(PARTS)},
               "level": {**string("Уровень катающегося; по умолчанию intermediate"), "enum": list(LEVELS)},
               "spot": string("Название спота, чтобы увидеть прогноз по часам (например Uluwatu), необязательно")})]
FUNCTIONS = {"surf_forecast": forecast}
