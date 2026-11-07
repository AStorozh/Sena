"""Tickets: Aviasales price search and price alerts for flights. The network is replaced with stubs."""
import unittest
from types import SimpleNamespace
from unittest import mock

from sena import config
from sena.core import store
from sena.world import alerts, finance, net, travel


def reply(data, status=200):
    return SimpleNamespace(status_code=status, json=lambda: data, text="")


CITIES = {
    "Хошимин": [{"type": "city", "code": "SGN", "name": "Хошимин"}, {"type": "airport", "code": "SGN", "name": "Тан Сон Нят"}],
    "Бали": [{"type": "airport", "code": "DPS", "name": "Нгура-Раи"}, {"type": "city", "code": "DPS", "name": "Денпасар (Бали)"}],
    "DAD": [{"type": "city", "code": "DAD", "name": "Дананг"}],
    "SGN": [{"type": "city", "code": "SGN", "name": "Хошимин"}],
    "DPS": [{"type": "city", "code": "DPS", "name": "Денпасар (Бали)"}],
}
AIRLINES = [{"code": "TR", "name_translations": {"en": "Scoot"}}, {"code": "VJ", "name_translations": {"en": "Vietjet"}, "name": None}]


def ticket(date, price, airline="VJ", transfers=0, back=None, duration=240):
    return {"departure_at": f"{date}T08:15:00+07:00", "return_at": f"{back}T22:20:00+08:00" if back else None, "price": price,
            "airline": airline, "transfers": transfers, "duration_to": duration, "origin": "SGN", "destination": "DPS"}


class Fake:
    """Stand-in for requests.get: remembers requests, answers by URL."""
    def __init__(self, tickets=(), status=200, error=None):
        self.tickets, self.status, self.error, self.calls = list(tickets), status, error, []

    def __call__(self, url, params=None, headers=None, timeout=None, **kw):
        self.calls.append((url, params or {}, headers or {}))
        if url == travel.PLACES:
            return reply(CITIES.get(params["term"], []))
        if url == travel.AIRLINES:
            return reply(AIRLINES)
        if url == travel.PRICES:
            if self.status != 200:
                return reply({"error": self.error}, self.status)
            return reply({"success": True, "currency": "usd", "data": self.tickets})
        return reply({}, 404)


class Base(unittest.TestCase):
    def setUp(self):
        travel._names.clear()
        travel._airlines.clear()
        travel._airlines_tried[0] = 0.0
        self.key = mock.patch.object(config, "TRAVELPAYOUTS_TOKEN", "secret-token")
        self.key.start()
        self.addCleanup(self.key.stop)

    def run_with(self, fake):
        patch = mock.patch.object(net.requests, "get", fake)
        patch.start()
        self.addCleanup(patch.stop)
        return fake


class Search(Base):
    def test_month_shows_cheapest_per_day_with_link(self):
        fake = self.run_with(Fake([ticket("2026-11-14", 100, "TR", 1), ticket("2026-11-14", 120), ticket("2026-11-02", 88, "TR", 1, duration=300),
                                   ticket("2026-11-08", 101)]))
        out = travel.flight_prices("Хошимин", "Бали", "2026-11")
        self.assertIn("Хошимин → Денпасар (Бали), ноябрь 2026, в одну сторону", out)
        lines = out.splitlines()
        self.assertIn("2 ноября, 08:15 — 88 USD, Scoot, 1 пересадка, 5 ч 00 мин в пути", lines[1])
        self.assertEqual(sum("14 ноября" in line for line in lines), 1)   # one ticket per date, the cheapest one
        self.assertIn("https://www.aviasales.com/search/SGN0211DPS1", lines[-1])
        params = [c for c in fake.calls if c[0] == travel.PRICES][0]
        self.assertEqual((params[1]["origin"], params[1]["destination"], params[1]["departure_at"], params[1]["one_way"]),
                         ("SGN", "DPS", "2026-11", "true"))
        self.assertEqual(params[2]["X-Access-Token"], "secret-token")   # the key goes in a header, not in the URL
        self.assertNotIn("token", params[1])

    def test_round_trip_and_direct_flags(self):
        fake = self.run_with(Fake([ticket("2026-11-03", 174, back="2026-11-13")]))
        out = travel.flight_prices("SGN", "DPS", "2026-11", "2026-11", direct=True, currency="eur")
        self.assertIn("туда и обратно, только прямые", out)
        self.assertIn("174 EUR", out)
        self.assertIn("обратно пт 13 ноября, 22:20", out)
        self.assertIn("search/SGN0311DPS13111", out)
        params = [c for c in fake.calls if c[0] == travel.PRICES][0][1]
        self.assertEqual((params["one_way"], params["direct"], params["return_at"], params["currency"]), ("false", "true", "2026-11", "eur"))

    def test_exact_day_keeps_different_flights_of_that_day(self):
        self.run_with(Fake([ticket("2026-11-14", 100, "TR"), ticket("2026-11-14", 120, "VJ"), ticket("2026-11-14", 120, "VJ")]))
        out = travel.flight_prices("SGN", "DPS", "2026-11-14")
        self.assertEqual(sum("— " in line for line in out.splitlines()), 2)   # an identical flight is not repeated

    def test_nothing_found_is_a_normal_answer_with_site_link(self):
        self.run_with(Fake([]))
        out = travel.flight_prices("SGN", "DPS", "2026-11-14")
        self.assertIn("билетов в базе Авиасейлс нет", out)
        self.assertIn("search/SGN1411DPS1", out)

    def test_bad_input_and_errors_are_explained(self):
        self.run_with(Fake(status=401))
        with self.assertRaises(travel.TravelError) as e:
            travel.flight_prices("SGN", "DPS", "2026-11")
        self.assertIn("TRAVELPAYOUTS_TOKEN", str(e.exception))
        with self.assertRaises(travel.TravelError):
            travel.flight_prices("SGN", "DPS", "ноябрь")
        with mock.patch.object(config, "TRAVELPAYOUTS_TOKEN", ""):
            with self.assertRaises(travel.TravelError) as e:
                travel.flight_prices("SGN", "DPS", "2026-11")
        self.assertIn("travelpayouts.com", str(e.exception))
        with self.assertRaises(travel.TravelError) as e:
            travel.flight_prices("Атлантида", "DPS")
        self.assertIn("Атлантида", str(e.exception))

    def test_a_missing_airline_list_is_asked_once_not_for_every_line(self):
        fake = Fake([ticket(f"2026-11-{10 + i:02d}", 100 + i) for i in range(6)])

        def without_airlines(url, params=None, headers=None, timeout=None, **kw):
            if url == travel.AIRLINES:
                fake.calls.append((url, {}, {}))
                raise net.requests.Timeout("read timed out")   # each such wait is up to 20 s
            return fake(url, params, headers, timeout)
        self.run_with(without_airlines)
        out = travel.flight_prices("SGN", "DPS", "2026-11")
        self.assertIn("VJ", out)   # the code stands in for the name
        self.assertEqual(sum(c[0] == travel.AIRLINES for c in fake.calls), 1)

    def test_ideas_name_destinations(self):
        t = ticket("2026-11-09", 23, duration=80)
        t["destination"] = "DAD"
        self.run_with(Fake([t]))
        out = travel.flight_ideas("Хошимин", "2026-11")
        self.assertIn("Откуда: Хошимин, ноябрь 2026", out)
        self.assertIn("Дананг — 23 USD, пн 9 ноября, 08:15, Vietjet, прямой, 1 ч 20 мин в пути", out)


class FlightAlerts(Base):
    def setUp(self):
        super().setUp()
        store.clear(config.ALERTS_FILE)
        alerts._health.clear()
        self.sent = []

    def test_alert_fires_when_fare_drops_and_includes_dates(self):
        fake = self.run_with(Fake([ticket("2026-11-02", 130, "TR", 1)]))
        self.assertIn("Слежу", alerts.add("flight", "SGN-DPS:2026-11", "below", 100))
        with mock.patch.object(alerts.time, "time", return_value=10_000.0):
            self.assertEqual(alerts.check(self.sent.append), 0)   # too expensive: stay quiet
        fake.tickets = [ticket("2026-11-02", 88, "TR", 1)]
        with mock.patch.object(alerts.time, "time", return_value=10_000.0 + 3600 + 1):
            self.assertEqual(alerts.check(self.sent.append), 1)
        self.assertIn("SGN-DPS:2026-11 ниже 100, сейчас 88", self.sent[0])
        self.assertIn("2 ноября", self.sent[0])
        self.assertIn("search/SGN0211DPS1", self.sent[0])

    def test_flights_are_checked_at_most_once_an_hour(self):
        fake = self.run_with(Fake([ticket("2026-11-02", 130)]))
        alerts.add("flight", "SGN-DPS:2026-11", "below", 100)
        with mock.patch.object(alerts.time, "time", return_value=50_000.0):
            alerts.check(self.sent.append)
            before = len([c for c in fake.calls if c[0] == travel.PRICES])
            alerts.check(self.sent.append)
        self.assertEqual(len([c for c in fake.calls if c[0] == travel.PRICES]), before)

    def test_expired_dates_are_detected(self):
        self.assertTrue(travel.expired("SGN-DPS:2020-05"))
        self.assertTrue(travel.expired("SGN-DPS:2020-05-14:2020-05-20"))
        self.assertFalse(travel.expired("SGN-DPS:2999-05"))
        self.assertFalse(travel.expired("SGN-DPS"))      # without a departure date, "expired" doesn't apply
        self.assertFalse(travel.expired("SGN-DPS:2026-13"))   # a malformed date is not treated as expired

    def test_alert_for_a_past_month_is_removed_with_a_notice(self):
        self.run_with(Fake([ticket("2026-11-02", 130)]))
        store.save_json(config.ALERTS_FILE, [{"id": "old", "kind": "flight", "symbol": "SGN-DPS:2020-05", "condition": "below", "value": 100,
                                              "once": False, "fired": False}])
        self.assertEqual(alerts.check(self.sent.append), 0)
        self.assertIn("снят: дата вылета прошла", self.sent[0])
        self.assertEqual(alerts.listing(), "Сигналов нет")

    def test_unknown_route_is_rejected_and_money_alerts_still_work(self):
        self.run_with(Fake([]))
        with self.assertRaises(travel.TravelError):
            alerts.add("flight", "SGN-DPS:2026-11", "below", 100)
        with self.assertRaises(travel.TravelError):
            alerts.add("flight", "SGN", "below", 100)
        with mock.patch.object(finance, "price_of", lambda kind, symbol: 80000.0):
            self.assertIn("Слежу", alerts.add("crypto", "BTC", "above", 90000))


if __name__ == "__main__":
    unittest.main()
