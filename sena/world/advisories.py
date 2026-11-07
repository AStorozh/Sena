"""Country safety before a trip: official travel advice from the UK Foreign Office (FCDO, detailed by section) and the US State Department (level 1 to 4). No keys needed.
The country can be named in any language: the English name is taken from Wikidata. These are risk assessments by other governments, each with its own level of caution."""
import re

from sena.core.safety import external
from sena.core.tools import string, tool
from sena.world import countries, net, tech

FCDO_LIST = "https://www.gov.uk/api/content/foreign-travel-advice"
STATE = "https://cadataapi.state.gov/api/TravelAdvisories"
HEADERS = {"User-Agent": "Sena/1.0 (https://github.com/AStorozh/Sena)"}
CACHE_SECONDS = 6 * 3600
LEVELS = {"1": "обычные меры предосторожности", "2": "повышенная осторожность", "3": "пересмотрите необходимость поездки", "4": "не ездить"}
ALERTS = {"avoid_all_travel_to_whole_country": "не ездить в страну", "avoid_all_but_essential_travel_to_whole_country": "ехать только по крайней необходимости",
          "avoid_all_travel_to_parts": "не ездить в отдельные районы", "avoid_all_but_essential_travel_to_parts": "в отдельные районы только по крайней необходимости"}
ALIASES = {"united states of america": "usa", "united states": "usa", "czech republic": "czechia", "russian federation": "russia", "burma": "myanmar", "korea south": "south korea",
           "republic of korea": "south korea", "türkiye": "turkey", "turkiye": "turkey", "viet nam": "vietnam"}


class AdviceError(net.WorldError):
    pass


def _norm(text):
    text = re.sub(r"\(.*?\)|travel advice", " ", str(text).lower())
    text = " ".join(re.findall(r"[a-zа-я0-9]+", text))
    return ALIASES.get(text, text)


def _pick(rows, name, key):
    """The page for the needed country: exact name match first, then via the alias list, then the single candidate where the name appears in full."""
    want = _norm(name)
    for row in rows:
        if _norm(key(row)) == want or set(_norm(key(row)).split()) == set(want.split()):
            return row
    near = [r for r in rows if want and (want in _norm(key(r)).split() or want in _norm(key(r)))]
    return near[0] if len(near) == 1 else None


def _json(url):
    return net.as_json(net.get(url, None, HEADERS, ttl=CACHE_SECONDS, who="рекомендации по безопасности", error=AdviceError), "рекомендации по безопасности", AdviceError)


def travel_advice(country):
    """Country safety advice: US State Department (level 1-4) and UK Foreign Office (warnings, safety, what's changed)."""
    c = countries._country(country)
    name = c["en"]
    lines = [f"{c['name']} ({name})"]
    try:
        row = _pick([r for r in _json(STATE) if isinstance(r, dict)], name, lambda r: r.get("Title", "").split(" - Level")[0])
    except AdviceError as e:
        row, lines = None, lines + [f"Госдеп США: не ответил ({e})"]
    else:
        if row:
            level = re.search(r"Level (\d)", row["Title"])
            summary = " ".join(tech.plain(row.get("Summary", "")).split())
            lines.append(f"Госдеп США: уровень {level.group(1)} из 4 ({LEVELS.get(level.group(1), '')}); обновлено {str(row.get('Updated') or row.get('Published') or '')[:10]}\n   {summary[:400]}\n   {row.get('Link')}" if level else f"Госдеп США: {row['Title']}")
        else:
            lines.append("Госдеп США: такой страны в списке рекомендаций нет")
    try:
        kids = (_json(FCDO_LIST).get("links") or {}).get("children") or []
        page = _pick(kids, name, lambda r: r.get("title", ""))
        if not page:
            lines.append("МИД Великобритании: такой страны в списке рекомендаций нет")
        else:
            data = _json("https://www.gov.uk/api/content" + page["base_path"])
            details = data.get("details") or {}
            alerts = [ALERTS.get(a, a.replace("_", " ")) for a in details.get("alert_status") or []]
            parts = {p["title"]: " ".join(tech.plain(p.get("body", "")).split()) for p in details.get("parts") or []}
            updated = str(data.get("public_updated_at") or "")[:10]
            lines.append("МИД Великобритании: " + (f"{'; '.join(alerts)}" if alerts else "запретов на поездки нет") + f"; обновлено {updated}"
                         + (f"; что изменилось: {details.get('change_description')}" if details.get("change_description") else ""))
            for title, limit in (("Warnings and insurance", 500), ("Safety and security", 900)):
                if parts.get(title):
                    lines.append(f"   {title}: {parts[title][:limit]}{'…' if len(parts[title]) > limit else ''}")
            lines.append(f"   https://www.gov.uk{page['base_path']}")
    except AdviceError as e:
        lines.append(f"МИД Великобритании: не ответил ({e})")
    lines.append("(Это оценки правительств США и Великобритании: осторожность у каждого своя. Перед поездкой смотри и местные новости.)")
    return external("\n".join(lines), "рекомендации по безопасности")


TOOLS = [tool("travel_advice", "Безопасность страны перед поездкой: уровень риска Госдепа США (1-4), предупреждения МИД Великобритании, раздел о безопасности (преступность, протесты, природные риски), "
                               "что изменилось в последнее время. Страна на любом языке. Только факты из рекомендаций, без своих прогнозов.",
              {"country": string("Страна: Вьетнам, Индонезия, Thailand")}, ["country"])]
FUNCTIONS = {"travel_advice": travel_advice}
