"""Finance: exchange rates, crypto, stocks, price alerts. The network is replaced by stubs."""
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from sena import config
from sena.core import store
from sena.world import alerts, finance, net


def reply(data, status=200):
    return SimpleNamespace(status_code=status, json=lambda: data, text="")


ER = {"result": "success", "time_last_update_utc": "Thu, 15 Oct 2026 00:02:31 +0000", "rates": {"USD": 1, "VND": 25890.2, "RUB": 83.93, "EUR": 0.8944}}
CBR = {"Date": "2026-10-15T11:30:00+03:00", "Valute": {"USD": {"Value": 83.9497, "Nominal": 1}, "JPY": {"Value": 55.0, "Nominal": 100}}}


def router(table):
    def get(url, params=None, headers=None, timeout=None, **kw):
        for prefix, data in table.items():
            if url.startswith(prefix):
                if isinstance(data, Exception):
                    raise data
                return reply(data[0], data[1]) if isinstance(data, tuple) else reply(data)
        return reply({}, 404)
    return get


class Clean(unittest.TestCase):
    def setUp(self):
        net._cache.clear()   # exchange rates are kept for an hour: each test answers with its own stub


class Rates(Clean):
    def test_rates_are_kept_for_an_hour_since_they_change_once_a_day(self):
        get = mock.Mock(side_effect=router({"https://open.er-api.com": ER}))
        with mock.patch.object(net.requests, "get", get):
            for _ in range(3):   # a price alert on a pair asks every few minutes
                finance.price_of("fx", "USD/VND")
        self.assertEqual(get.call_count, 1)

    def test_conversion_in_russian_format(self):
        with mock.patch.object(net.requests, "get", router({"https://open.er-api.com": ER})):
            out = finance.exchange_rate("usd", "vnd", 100)
        self.assertIn("100 USD = 2 589 020 VND", out)
        self.assertIn("курс 1 USD = 25 890 VND", out)

    def test_ruble_pair_adds_central_bank(self):
        with mock.patch.object(net.requests, "get", router({"https://open.er-api.com": ER, "https://www.cbr-xml-daily.ru": CBR})):
            out = finance.exchange_rate("USD", "RUB")
        self.assertIn("Официальный курс ЦБ РФ на 2026-10-15: 1 USD = 83,95 RUB", out)

    def test_unknown_currency(self):
        with mock.patch.object(net.requests, "get", router({"https://open.er-api.com": ER})):
            with self.assertRaises(finance.FinanceError) as e:
                finance.exchange_rate("USD", "ZZZ")
        self.assertIn("ZZZ", str(e.exception))

    def test_number_format(self):
        self.assertEqual(finance.fmt(1234567.8), "1 234 568")
        self.assertEqual(finance.fmt(83.9497), "83,95")
        self.assertEqual(finance.fmt(0.00123456), "0,001235")


class Crypto(Clean):
    def test_binance_then_coinbase_fallback_and_other_currency(self):
        binance = {"lastPrice": "81000.5", "priceChangePercent": "-2.77", "highPrice": "83000", "lowPrice": "80000"}
        get = router({"https://api.binance.com": binance, "https://open.er-api.com": ER})
        with mock.patch.object(net.requests, "get", get):
            out = finance.crypto_price(["btc"], vs="VND")
        self.assertIn("BTC:", out)
        self.assertIn("−2,77% за сутки", out)
        coinbase = {"data": {"amount": "2365.4", "base": "ETH", "currency": "USD"}}
        get = router({"https://api.binance.com": ({}, 400), "https://api.coinbase.com": coinbase})
        with mock.patch.object(net.requests, "get", get):
            out = finance.crypto_price("ETH")
        self.assertIn("ETH: 2 365 USD", out)
        self.assertNotIn("за сутки", out)


class Stocks(Clean):
    def test_yahoo_and_moex_and_missing(self):
        yahoo = {"chart": {"result": [{"meta": {"regularMarketPrice": 342.96, "chartPreviousClose": 336.66, "currency": "USD",
                                                  "exchangeName": "NMS", "longName": "Apple Inc."}}]}}
        moex = {"marketdata": {"data": [["SBER", 283.5, -0.7, "14:27:23"]]}, "securities": {"data": [["SBER", "Сбербанк", 285.5]]}}
        get = router({"https://query1.finance.yahoo.com/v8/finance/chart/AAPL": yahoo, "https://query1.finance.yahoo.com": ({}, 404),
                      "https://iss.moex.com": moex})
        with mock.patch.object(net.requests, "get", get):
            out = finance.stock_quote(["AAPL", "MOEX:SBER", "NOPE"])
        self.assertIn("Apple Inc. (AAPL): 342,96 USD (+1,87% за день)", out)
        self.assertIn("Сбербанк (MOEX:SBER): 283,50 RUB (−0,70% за день)", out)
        self.assertIn("NOPE: не удалось получить цену", out)

    def test_a_quote_without_a_price_is_a_clear_failure(self):
        odd = {"chart": {"result": [{"meta": {"currency": "USD"}, "indicators": {"quote": [{"close": []}]}}]}}
        with mock.patch.object(net.requests, "get", router({"https://query1.finance.yahoo.com": odd})):
            self.assertIn("XYZ: не удалось получить цену", finance.stock_quote(["XYZ"]))
            with self.assertRaises(finance.FinanceError):
                finance.price_of("stock", "XYZ")

    def test_summary_survives_one_failure(self):
        get = router({"https://open.er-api.com": ER, "https://api.binance.com": ({}, 500), "https://api.coinbase.com": ({}, 500),
                      "https://query1.finance.yahoo.com": ({}, 429)})
        with mock.patch.object(net.requests, "get", get):
            out = finance.market_summary()
        self.assertIn("USD/VND: 25 890", out)
        self.assertIn("EUR/USD:", out)
        self.assertIn("BTC: нет данных", out)


class PairFormat(unittest.TestCase):
    def test_pair_without_slash_is_explained(self):
        with self.assertRaises(finance.FinanceError) as e:
            finance.price_of("fx", "USDVND")
        self.assertIn("USD/VND", str(e.exception))


class Alerts(unittest.TestCase):
    def setUp(self):
        store.clear(config.ALERTS_FILE)
        alerts._health.clear()   # a source another test failed is not "down" here
        self.price = {"BTC": 80000.0}
        self.patch = mock.patch.object(finance, "price_of", lambda kind, symbol: self.price[symbol])
        self.patch.start()
        self.sent = []

    def tearDown(self):
        self.patch.stop()

    def test_once_alert_fires_once_and_disappears(self):
        self.assertIn("Слежу", alerts.add("crypto", "BTC", "above", 90000))
        self.assertEqual(alerts.check(self.sent.append), 0)   # the price is below the threshold: silent
        self.price["BTC"] = 91000.0
        self.assertEqual(alerts.check(self.sent.append), 1)
        self.assertIn("BTC выше 90 000, сейчас 91 000", self.sent[0])
        self.assertEqual(alerts.check(self.sent.append), 0)
        self.assertEqual(alerts.listing(), "Сигналов нет")

    def test_a_blind_alert_is_asked_less_and_told_once_and_again_when_it_sees(self):
        """n8n backs off a failing poll and tells once: a price source down for hours kept being hit every check, and the owner
        never learned his alert was blind."""
        from sena.world import net
        alerts._health.clear()
        alerts.add("crypto", "BTC", "above", 90000)
        tries = []

        def down(kind, symbol):
            tries.append(symbol)
            raise net.WorldError("биржа не отвечает")
        with mock.patch.object(finance, "price_of", down):
            alerts.check(self.sent.append)
            alerts.check(self.sent.append)   # within the pause: not asked again
            self.assertEqual(len(tries), 1)
            alerts._health[("crypto", "BTC")].update(until=0, since=time.time() - alerts.TELL_AFTER - 1)
            alerts.check(self.sent.append)
        self.assertEqual(len([t for t in self.sent if "не получаю больше часа" in t]), 1)
        alerts._health[("crypto", "BTC")]["until"] = 0
        alerts.check(self.sent.append)   # the source is back
        self.assertIn("цена снова приходит", self.sent[-1])

    def test_a_quiet_round_writes_nothing_and_a_fare_keeps_its_last_check(self):
        """The round runs every five minutes: a write each time would leave a version of the file each time."""
        alerts.add("crypto", "BTC", "above", 90000)
        before = len(store.versions(config.ALERTS_FILE))
        for _ in range(3):
            self.assertEqual(alerts.check(self.sent.append), 0)   # below the threshold: nothing changed
        self.assertEqual(len(store.versions(config.ALERTS_FILE)), before)
        self.assertNotIn("checked", store.load_json(config.ALERTS_FILE, [])[0])   # only the slow kinds keep a time of their last check
        items = store.load_json(config.ALERTS_FILE, [])
        store.save_json(config.ALERTS_FILE, items + [{"id": "fl", "kind": "flight", "symbol": "SGN-DPS:2099-11", "condition": "below", "value": 50, "once": True, "fired": False}])
        with mock.patch.object(alerts.travel, "fare_of", return_value=120.0), mock.patch.object(alerts.travel, "expired", return_value=False):
            alerts.check(self.sent.append)
        fare = next(a for a in store.load_json(config.ALERTS_FILE, []) if a["id"] == "fl")
        self.assertAlmostEqual(fare["checked"], time.time(), delta=60)   # asked again in an hour, not at every round
        self.assertEqual(self.sent, [])

    def test_repeating_alert_needs_the_price_to_come_back(self):
        alerts.add("crypto", "BTC", "below", 75000, once=False)
        self.price["BTC"] = 74000.0
        self.assertEqual(alerts.check(self.sent.append), 1)
        self.assertEqual(alerts.check(self.sent.append), 0)   # still below: don't repeat
        self.price["BTC"] = 76000.0
        alerts.check(self.sent.append)
        self.price["BTC"] = 73000.0
        self.assertEqual(alerts.check(self.sent.append), 1)   # crossed again
        self.assertEqual(len(self.sent), 2)

    def test_unknown_symbol_is_rejected_and_remove_works(self):
        def boom(kind, symbol):
            raise finance.FinanceError("Тикер не найден")
        with mock.patch.object(finance, "price_of", boom):
            with self.assertRaises(finance.FinanceError):
                alerts.add("stock", "NOPE", "above", 1)
        alerts.add("crypto", "BTC", "above", 99999)
        alert_id = alerts.listing().split("]")[0].lstrip("[")
        self.assertEqual(alerts.remove(alert_id), "Удалён")
        self.assertEqual(alerts.remove(alert_id), "Нет такого сигнала")

    def test_an_odd_quote_or_a_broken_record_does_not_stop_the_round(self):
        """A Yahoo answer without a price raised KeyError out of the check: the site, news and page watches after it never started."""
        self.price["ETH"] = 900.0
        alerts.add("crypto", "BTC", "above", 90000)
        alerts.add("crypto", "ETH", "above", 1000)
        store.save_json(config.ALERTS_FILE, [{"id": "x", "kind": "crypto"}] + store.load_json(config.ALERTS_FILE, []))   # no symbol, no threshold

        def odd(kind, symbol):
            if symbol == "BTC":
                raise KeyError("regularMarketPrice")
            return 2000.0
        with mock.patch.object(finance, "price_of", odd), mock.patch.object(alerts.events, "error"):
            self.assertEqual(alerts.check(self.sent.append), 1)   # ETH still fired
        self.assertIn(("crypto", "BTC"), alerts._health)   # counted against its source: asked again after a pause
        self.assertEqual(len(store.load_json(config.ALERTS_FILE, [])), 2)   # the fired one-off left, the rest stayed

    def test_removing_what_isnt_there_is_a_failure_and_writes_nothing(self):
        with mock.patch.object(alerts.store, "save_versioned") as saved:
            self.assertIsInstance(alerts.remove("nope"), alerts.Failure)
        saved.assert_not_called()

    def test_failed_price_does_not_fire_or_crash(self):
        alerts.add("crypto", "BTC", "above", 90000)
        def boom(kind, symbol):
            raise finance.FinanceError("нет связи")
        with mock.patch.object(finance, "price_of", boom):
            self.assertEqual(alerts.check(self.sent.append), 0)
        self.assertIn("BTC", alerts.listing())


if __name__ == "__main__":
    unittest.main()
