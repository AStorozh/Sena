"""Travel: flight tickets. Source — the Aviasales Data API (a free Travelpayouts affiliate key, TRAVELPAYOUTS_TOKEN).
These are the cheapest fares Aviasales users found over the last two days: a guide to dates and prices,
not a live quote. The browser agent opens the exact price for a date via the link in the response. No hotels in this API (Hotellook is closed)."""
import re
import time
from datetime import date, datetime, timedelta

from sena import config
from sena.core.tools import string, tool
from sena.world import net
from sena.world.finance import fmt

PRICES = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
PLACES = "https://autocomplete.travelpayouts.com/places2"
AIRLINES = "https://api.travelpayouts.com/data/en/airlines.json"
SEARCH = "https://www.aviasales.com/search/"
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
MONTHS_NOM = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
SHOWN = 6

_names = {}        # city code → name (from Aviasales autocomplete)
_airlines = {}     # airline code → name


class TravelError(net.WorldError):
    pass


def _explain(reply):
    if reply.status_code == 401:
        return "Ключ Travelpayouts не принят: проверь TRAVELPAYOUTS_TOKEN в .env"
    try:
        detail = reply.json().get("error") or ""
    except (ValueError, AttributeError):
        detail = ""
    return f"Авиасейлс ответил {reply.status_code}" + (f": {detail}" if detail else "")


def _get(url, params=None, auth=False):
    headers = {}
    if auth:
        if not config.TRAVELPAYOUTS_TOKEN:
            raise TravelError("Ключа для билетов нет. Зарегистрируйся на travelpayouts.com, скопируй ключ из Профиля (API token) "
                              "и впиши в .env строкой TRAVELPAYOUTS_TOKEN=..., затем перезапусти Сену.")
        headers["X-Access-Token"] = config.TRAVELPAYOUTS_TOKEN
    return net.as_json(net.get(url, params, headers, who="Авиасейлс", error=TravelError, explain=_explain), "Авиасейлс", TravelError)


# ---------- cities ----------
def _suggest(term):
    data = _get(PLACES, {"term": term, "locale": "ru", "types[]": ["city", "airport"]})
    return data if isinstance(data, list) else []


def place(term):
    """City by name (Bali, Ho Chi Minh City, Tokyo) or IATA code (SGN): (code, name)."""
    term = term.strip()
    if not term:
        raise TravelError("Не указан город")
    code = term.upper() if re.fullmatch(r"[A-Za-z]{3}", term) else ""
    if code in _names:
        return code, _names[code]
    found = _suggest(term)
    if code:
        found = [p for p in found if p.get("code") == code] or found
    cities = [p for p in found if p.get("type") == "city"] or found
    if not cities:
        raise TravelError(f"Не нашла город «{term}»: назови иначе или кодом IATA (SGN, DPS)")
    best = cities[0]
    _names[best["code"]] = best.get("name") or best["code"]
    return best["code"], _names[best["code"]]


def _name(code):
    if not code:
        return "куда угодно"
    try:
        return _names.get(code) or place(code)[1]
    except TravelError:
        return code


_airlines_tried = [0.0]   # when the list last failed: it isn't asked again for every ticket line (each waits up to 20 s)
AIRLINES_RETRY = 600


def _airline(code):
    if not _airlines and time.time() - _airlines_tried[0] > AIRLINES_RETRY:
        try:
            for a in _get(AIRLINES):
                _airlines[a["code"]] = (a.get("name_translations") or {}).get("en") or a.get("name") or a["code"]
        except (TravelError, ValueError, KeyError, TypeError):
            _airlines_tried[0] = time.time()   # the codes stand in for the names meanwhile
    return _airlines.get(code, code or "авиакомпания не указана")


# ---------- tickets ----------
def _parse_date(value, what):
    value = (value or "").strip()
    if value and not re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", value):
        raise TravelError(f"{what}: месяц вида 2026-11 или день вида 2026-11-14")
    return value


def _when(iso):
    d = datetime.fromisoformat(iso[:19])
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}, {d:%H:%M}"


def _period(value):
    """2026-11 → "November 2026", 2026-11-14 → "14 November"."""
    if not value:
        return "ближайшие даты"
    year, month, *day = value.split("-")
    return f"{int(day[0])} {MONTHS[int(month) - 1]}" if day else f"{MONTHS_NOM[int(month) - 1]} {year}"


def _stops(n):
    n = n or 0
    return "прямой" if n == 0 else "1 пересадка" if n == 1 else f"{n} пересадки"


def _search_link(origin, destination, depart, back=None):
    """Aviasales search page for exact dates: FROM DDMM TO [DDMM return] 1 passenger."""
    def ddmm(iso):
        return iso[8:10] + iso[5:7]
    return f"{SEARCH}{origin}{ddmm(depart)}{destination}{ddmm(back) if back else ''}1"


def _tickets(origin, destination, date, return_date, direct, currency, limit, unique=False):
    params = {"origin": origin, "currency": currency.lower(), "sorting": "price", "limit": limit,
              "unique": "true" if unique else "false", "one_way": "false" if return_date else "true"}
    if destination:
        params["destination"] = destination
    if date:
        params["departure_at"] = date
    if return_date:
        params["return_at"] = return_date
    if direct:
        params["direct"] = "true"
    data = _get(PRICES, params, auth=True)
    if not data.get("success", True):
        raise TravelError(str(data.get("error") or "Авиасейлс не вернул билеты"))
    tickets = [t for t in data.get("data") or [] if t.get("price") and t.get("departure_at")]
    return sorted(tickets, key=lambda t: t["price"])


def _details(t):
    parts = [_airline(t.get("airline")), _stops(t.get("transfers"))]
    duration = t.get("duration_to") or t.get("duration")
    if duration:
        hours, minutes = divmod(duration, 60)
        parts.append((f"{hours} ч " if hours else "") + (f"{minutes:02d} мин" if hours else f"{minutes} мин") + " в пути")
    return ", ".join(parts)


def _line(t, currency):
    line = f"{_when(t['departure_at'])} — {fmt(t['price'], 0)} {currency}, {_details(t)}"
    if t.get("return_at"):
        line += f"; обратно {_when(t['return_at'])}"
    return line


def flight_prices(origin, destination, date="", return_date="", direct=False, currency="USD"):
    """The cheapest fares for the route. date and return_date: a month (2026-11) or a day (2026-11-14), empty — any."""
    date, return_date = _parse_date(date, "date"), _parse_date(return_date, "return_date")
    currency = (currency or "USD").upper()
    (o, o_name), (d, d_name) = place(origin), place(destination)
    day = len(date) == 10
    tickets = _tickets(o, d, date, return_date, direct, currency, limit=30 if day else 100)
    shown, seen = [], set()
    for t in tickets:   # a day: different options on that day; a month: one fare per departure date
        key = (t.get("airline"), t["departure_at"]) if day else t["departure_at"][:10]
        if key not in seen:
            seen.add(key)
            shown.append(t)
        if len(shown) == SHOWN:
            break
    trip = "туда и обратно" if return_date else "в одну сторону"
    head = f"{o_name} → {d_name}, {_period(date)}, {trip}{', только прямые' if direct else ''}"
    if not shown:
        back = return_date if len(return_date) == 10 else None
        link = f" Проверить на сайте: {_search_link(o, d, date, back)}" if day and (back or not return_date) else ""
        return f"{head}: билетов в базе Авиасейлс нет (за последние дни маршрут не искали). Попробуй другие даты или соседний город.{link}"
    best = shown[0]
    back = best.get("return_at") if return_date else None
    lines = [f"{head}. Дешевле всего:"] + [_line(t, currency) for t in shown]
    lines.append(f"Это цены из поисков за последние двое суток, ориентир. Точная цена на {_period(best['departure_at'][:10])}: "
                 f"{_search_link(o, d, best['departure_at'], back)}")
    return "\n".join(lines)


def flight_ideas(origin, month="", direct=False, currency="USD"):
    """Where to fly cheaply from a city: the lowest prices by destination for a given month."""
    month = _parse_date(month, "month")
    currency = (currency or "USD").upper()
    o, o_name = place(origin)
    tickets = _tickets(o, "", month, "", direct, currency, limit=10, unique=True)
    if not tickets:
        return f"Из города {o_name} за этот период в базе Авиасейлс нет билетов."
    lines = [f"Откуда: {o_name}, {_period(month)}, в одну сторону. Самые низкие цены по направлениям:"]
    for t in tickets[:SHOWN + 2]:
        lines.append(f"{_name(t.get('destination'))} — {fmt(t['price'], 0)} {currency}, {_when(t['departure_at'])}, {_details(t)}")
    return "\n".join(lines)


# ---------- for price alerts ----------
def _cheapest(symbol):
    """A route like SGN-DPS:2026-11 or SGN-DPS:2026-11:2026-11 (with a return): the cheapest fare, in dollars."""
    route, _, rest = symbol.partition(":")
    dates = rest.split(":") if rest else []
    if not re.fullmatch(r"[^-\s]+-[^-\s]+", route.strip()) or len(dates) > 2:
        raise TravelError("Маршрут вида SGN-DPS:2026-11 (коды городов IATA, месяц или день вылета)")
    o, d = (place(p)[0] for p in route.split("-"))
    date = _parse_date(dates[0] if dates else "", "date")
    back = _parse_date(dates[1] if len(dates) > 1 else "", "return_date")
    tickets = _tickets(o, d, date, back, False, "USD", limit=1)
    if not tickets:
        raise TravelError(f"На {symbol} билетов в базе нет")
    return tickets[0], o, d, back


def expired(symbol):
    """The alert's departure date has already passed (a month counts through its last day): nothing left to watch."""
    value = symbol.partition(":")[2].split(":")[0]
    if not re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", value):
        return False
    year, month, *day = (int(p) for p in value.split("-"))
    try:
        last = date(year, month, day[0]) if day else date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    except ValueError:
        return False
    return last < date.today()


def fare_of(symbol):
    """A single number for alerts: the lowest price in dollars."""
    return float(_cheapest(symbol)[0]["price"])


def hint(symbol):
    """Detail for a triggered alert's message: when to fly, and a link."""
    t, o, d, back = _cheapest(symbol)
    link = _search_link(o, d, t["departure_at"], t.get("return_at") if back else None)
    return f"{_line(t, 'USD')}. {link}"


TOOLS = [
    tool("flight_prices", "Самые дешёвые билеты по маршруту из Авиасейлс: даты, цены, авиакомпании, пересадки. Цены берутся из поисков за "
                          "последние двое суток, это ориентир; точную цену на дату проверь по ссылке из ответа (она же открывается в "
                          "браузере). Город можно назвать словами или кодом IATA (SGN, DPS). Отелей нет.",
         {"origin": string("Откуда: Хошимин или SGN"), "destination": string("Куда: Бали, Токио или DPS"),
          "date": string("Месяц 2026-11 или день 2026-11-14; пусто — ближайшие даты"),
          "return_date": string("Для билета туда и обратно: месяц или день возвращения; пусто — в одну сторону"),
          "direct": {"type": "boolean", "description": "true — только прямые рейсы"},
          "currency": string("Валюта цены, по умолчанию USD; RUB, EUR, VND")}, ["origin", "destination"]),
    tool("flight_ideas", "Куда дёшево улететь из города в месяц: самые низкие цены по разным направлениям.",
         {"origin": string("Откуда: Хошимин или SGN"), "month": string("Месяц 2026-11 или день; пусто — ближайшие даты"),
          "direct": {"type": "boolean", "description": "true — только прямые рейсы"},
          "currency": string("Валюта цены, по умолчанию USD")}, ["origin"]),
]
FUNCTIONS = {"flight_prices": flight_prices, "flight_ideas": flight_ideas}
