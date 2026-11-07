"""Weather and maps: parsing responses, errors, limits. The network is replaced with stubs."""
import json
import time
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote

from sena import config
from sena.core import limits, store
from sena.world import maps, maps_google, maps_osm, maps_tomtom, net, weather
from sena.world.maps_base import MapsError


def reply(data=None, status=200):
    return SimpleNamespace(status_code=status, json=lambda: data, text=json.dumps(data) if data else "")


def forecast():
    today = date.today()
    days = [(today + timedelta(days=i)).isoformat() for i in range(3)]
    return {"current": {"temperature_2m": 31.4, "apparent_temperature": 36.2, "relative_humidity_2m": 70,
                        "precipitation": 0, "weather_code": 2, "wind_speed_10m": 3.2},
            "daily": {"time": days, "weather_code": [61, 3, 0], "temperature_2m_max": [33, 32, 34], "temperature_2m_min": [26, 26, 27],
                      "precipitation_sum": [12.0, 0, 0], "precipitation_probability_max": [70, 10, 0],
                      "wind_speed_10m_max": [8, 6, 5], "uv_index_max": [9, 8, 10]}}


class Weather(unittest.TestCase):
    def test_current_and_days_in_russian(self):
        def fake(url, **kw):
            if "geocoding" in url:
                return reply({"results": [{"name": "Ho Chi Minh City", "country": "Vietnam", "admin1": "Ho Chi Minh", "latitude": 10.8, "longitude": 106.6}]})
            self.assertEqual(kw["params"]["forecast_days"], 3)
            return reply(forecast())
        with mock.patch.object(net.requests, "get", fake):
            out = weather.weather("saigon")
        self.assertIn("Ho Chi Minh City, Ho Chi Minh, Vietnam", out)
        self.assertIn("Сейчас 31°C (ощущается как 36), переменная облачность", out)
        self.assertIn("Сегодня", out)
        self.assertIn("осадки 12 мм (вероятность 70%)", out)
        self.assertIn("Завтра", out)
        self.assertEqual(out.count("\n"), 3)

    def test_unknown_place_is_named_in_the_error(self):
        with mock.patch.object(net.requests, "get", lambda url, **kw: reply({})):
            with self.assertRaises(net.WorldError) as e:
                weather.weather("Нигдеград")
        self.assertIn("Нигдеград", str(e.exception))


class GoogleMaps(unittest.TestCase):
    def setUp(self):
        self.key = mock.patch.object(config, "GOOGLE_MAPS_KEY", "maps-key")
        self.key.start()

    def tearDown(self):
        self.key.stop()

    def test_place_search_request_and_answer(self):
        data = {"places": [{"displayName": {"text": "Cafe Hub"}, "formattedAddress": "12 Nguyen Hue, Q1", "rating": 4.6,
                            "userRatingCount": 210, "currentOpeningHours": {"openNow": True}, "googleMapsUri": "https://maps.google.com/?cid=1"}]}
        with mock.patch.object(net.requests, "request", return_value=reply(data)) as request:
            out = maps_google.places("кофейня", near="Nguyen Hue")
        sent = request.call_args
        self.assertEqual(sent.kwargs["json"]["textQuery"], "кофейня рядом с Nguyen Hue")
        self.assertEqual(sent.kwargs["headers"]["X-Goog-Api-Key"], "maps-key")
        self.assertIn("places.rating", sent.kwargs["headers"]["X-Goog-FieldMask"])
        self.assertIn("рейтинг 4.6 (210 отзывов)", out)
        self.assertIn("открыто сейчас", out)

    def test_route_minutes_distance_and_link(self):
        data = {"routes": [{"duration": "1830s", "staticDuration": "1500s", "distanceMeters": 12400, "description": "Nguyen Hue"}]}
        with mock.patch.object(net.requests, "request", return_value=reply(data)) as request:
            out = maps_google.route("Landmark 81", "Tan Son Nhat Airport", mode="motorbike")
        body = request.call_args.kwargs["json"]
        self.assertEqual(body["travelMode"], "TWO_WHEELER")
        self.assertEqual(body["routingPreference"], "TRAFFIC_AWARE")
        self.assertIn("На мотоцикле: 31 мин, 12.4 км (без пробок 25 мин), через Nguyen Hue", out)
        self.assertIn("origin=Landmark+81&destination=Tan+Son+Nhat+Airport&travelmode=driving", out)

    def test_the_departure_is_sent_as_a_real_instant(self):
        """"-05:00" became "-05:00Z" (invalid), and a time without a zone was sent as UTC — seven hours off in Vietnam."""
        sent = []
        with mock.patch.object(net.requests, "request", lambda *a, **kw: sent.append(kw["json"]) or reply({"routes": []})), \
                mock.patch.object(maps_google.moment, "owner_offset", "+07:00"):
            for depart in ("2026-11-02T08:30:00+07:00", "2026-11-02T08:30:00-05:00", "2026-11-02T08:30:00"):
                maps_google.route("A", "B", "drive", depart)
            with self.assertRaises(MapsError):
                maps_google.route("A", "B", "drive", "завтра утром")
        self.assertEqual([b["departureTime"] for b in sent], ["2026-11-02T01:30:00Z", "2026-11-02T13:30:00Z", "2026-11-02T01:30:00Z"])

    def test_errors_are_explained(self):
        with mock.patch.object(config, "GOOGLE_MAPS_KEY", ""):
            with self.assertRaises(MapsError) as e:
                maps_google.places("кофе")
            self.assertIn("GOOGLE_MAPS_KEY", str(e.exception))
        cases = [
            ({"error": {"message": "This API method requires billing to be enabled.", "status": "PERMISSION_DENIED"}}, 403, "платёжный аккаунт"),
            ({"error": {"message": "API keys are not supported by this API. Expected OAuth2 access token.", "status": "UNAUTHENTICATED"}}, 401, "не подходит"),
            ({"error": {"message": "The caller does not have permission", "status": "PERMISSION_DENIED"}}, 403, "недоступна"),
        ]
        for data, status, expected in cases:
            with mock.patch.object(net.requests, "request", return_value=reply(data, status)):
                with self.assertRaises(MapsError) as e:
                    maps_google.places("кофе")
                self.assertIn(expected, str(e.exception))


class TomTom(unittest.TestCase):
    def setUp(self):
        self.key = mock.patch.object(config, "TOMTOM_KEY", "tt-key")
        self.key.start()

    def tearDown(self):
        self.key.stop()

    def fake_get(self, calls):
        def get(url, params=None, headers=None, timeout=None, **kw):
            calls.append((url, params))
            if "/poiSearch/" in url:
                return reply({"results": [{"poi": {"name": "Sailing Club", "phone": "+84 252 3847 440", "url": "www.sailingclub.vn",
                                                    "categories": ["cafe/pub"]},
                                           "address": {"freeformAddress": "24 Nguyen Dinh Chieu, Mui Ne"}, "dist": 180.4,
                                           "position": {"lat": 10.946, "lon": 108.285}}]})
            if "/search/2/search/" in url:
                name = url.rsplit("/", 1)[-1].replace(".json", "")
                lat = 10.95 if "Mui" in name else 10.93
                return reply({"results": [{"position": {"lat": lat, "lon": 108.28}, "address": {"freeformAddress": f"адрес для {name}"}}]})
            return reply({"routes": [{"summary": {"lengthInMeters": 8200, "travelTimeInSeconds": 1260, "noTrafficTravelTimeInSeconds": 900}}]})
        return get

    def test_places_geocode_near_then_poi_search(self):
        calls = []
        with mock.patch.object(net.requests, "get", self.fake_get(calls)):
            out = maps_tomtom.places("кофейня", near="Mui Ne", limit=3)
        self.assertIn("/search/Mui%20Ne.json", calls[0][0])
        poi_url, poi_params = calls[1]
        self.assertIn(f"/poiSearch/{quote('кофейня')}.json", poi_url)
        self.assertEqual((poi_params["lat"], poi_params["lon"], poi_params["limit"], poi_params["key"]), (10.95, 108.28, 3, "tt-key"))
        self.assertIn("Sailing Club — 24 Nguyen Dinh Chieu, Mui Ne; cafe/pub; 180 м; тел. +84 252 3847 440", out)
        self.assertIn("https://www.google.com/maps/search/?api=1&query=10.946,108.285", out)

    def test_places_need_a_place_to_look_near(self):
        with self.assertRaises(MapsError) as e:
            maps_tomtom.places("кофейня")
        self.assertIn("рядом с чем", str(e.exception))

    def test_motorbike_route_with_traffic(self):
        calls = []
        with mock.patch.object(net.requests, "get", self.fake_get(calls)):
            out = maps_tomtom.route("Mui Ne", "Phan Thiet", mode="motorbike", depart="2026-10-16T08:30:00+07:00")
        route_url, params = calls[-1]
        self.assertIn("/10.95,108.28:10.93,108.28/json", route_url)
        destination_search = calls[1][1]   # the destination is looked up anchored to the origin point
        self.assertEqual((destination_search["lat"], destination_search["lon"]), (10.95, 108.28))
        self.assertNotIn("lat", calls[0][1])
        self.assertEqual((params["travelMode"], params["traffic"], params["departAt"]), ("motorcycle", "true", "2026-10-16T08:30:00+07:00"))
        self.assertIn("На мотоцикле: 21 мин, 8.2 км (без пробок 15 мин)", out)
        self.assertIn("travelmode=driving", out)

    def test_transit_returns_only_a_link(self):
        with mock.patch.object(net.requests, "get", side_effect=AssertionError("запросов быть не должно")):
            out = maps_tomtom.route("A", "B", mode="transit")
        self.assertIn("не строит маршруты на общественном транспорте", out)
        self.assertIn("travelmode=transit", out)

    def test_unknown_address_and_errors_are_explained(self):
        with mock.patch.object(net.requests, "get", return_value=reply({"results": []})):
            with self.assertRaises(MapsError) as e:
                maps_tomtom.geocode("Нигдеград")
            self.assertIn("Нигдеград", str(e.exception))
        with mock.patch.object(net.requests, "get", return_value=reply({"error": "Forbidden"}, 403)):
            with self.assertRaises(MapsError) as e:
                maps_tomtom.geocode("x")
            self.assertIn("не принял ключ", str(e.exception))
        with mock.patch.object(net.requests, "get", return_value=reply({}, 429)):
            with self.assertRaises(MapsError) as e:
                maps_tomtom.geocode("x")
            self.assertIn("лимит", str(e.exception))
        with mock.patch.object(config, "TOMTOM_KEY", ""):
            with self.assertRaises(MapsError) as e:
                maps_tomtom.geocode("x")
            self.assertIn("TOMTOM_KEY", str(e.exception))


class Net(unittest.TestCase):
    def setUp(self):
        net._cache.clear()

    def test_connection_error_and_bad_status_become_world_errors(self):
        with mock.patch.object(net.requests, "get", side_effect=net.requests.ConnectionError("нет сети")):
            with self.assertRaises(net.WorldError) as e:
                net.get("https://x.test", who="Источник")
        self.assertIn("Источник: нет связи (ConnectionError)", str(e.exception))
        for raised, said in ((net.requests.ConnectionError("Failed to resolve: nodename nor servname provided"), "адрес не найден"),
                             (net.requests.exceptions.SSLError("certificate verify failed"), "сертификат сайта не принят"),
                             (net.requests.ConnectionError("[Errno 61] Connection refused"), "соединение отклонено"),
                             (net.requests.Timeout("read timed out"), "не ответил за 20 с")):
            with mock.patch.object(net.requests, "get", side_effect=raised), self.assertRaises(net.WorldError) as e:
                net.get("https://x.test", who="Источник")
            self.assertIn(said, str(e.exception))   # each cause in words, as Firecrawl names them
        with mock.patch.object(net.requests, "get", return_value=reply({}, 503)):
            with self.assertRaises(net.WorldError) as e:
                net.get("https://x.test", who="Источник")
        self.assertIn("Источник ответил 503", str(e.exception))

    def test_a_dropped_connection_or_a_busy_gateway_gets_one_more_try_and_a_refusal_none(self):
        """LangChain's retry for idempotent calls: a GET once more after a blip; never a timeout, a refusal or a POST."""
        calls = []

        def flaky(*args, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise net.requests.ConnectionError("('Connection aborted.', RemoteDisconnected('Remote end closed connection'))")
            return reply({"ok": 1})
        self.enterContext(mock.patch.object(net, "RETRY_PAUSE", 0))
        with mock.patch.object(net.requests, "get", flaky):
            self.assertEqual(net.get("https://x.test/a").json(), {"ok": 1})
        self.assertEqual(len(calls), 2)
        answers = iter([reply({}, 503), reply({"ok": 2})])
        with mock.patch.object(net.requests, "get", lambda *a, **kw: next(answers)):
            self.assertEqual(net.get("https://x.test/b").json(), {"ok": 2})
        for raised in (net.requests.Timeout("read timed out"), net.requests.ConnectionError("[Errno 61] Connection refused"),
                       net.requests.exceptions.SSLError("certificate verify failed")):
            seen = []

            def failing(*args, **kw):
                seen.append(1)
                raise raised
            with mock.patch.object(net.requests, "get", failing), self.assertRaises(net.WorldError):
                net.get("https://x.test/c")
            self.assertEqual(len(seen), 1, raised)
        limited = SimpleNamespace(status_code=429, headers={"retry-after": "120"}, json=lambda: {}, text="")
        with mock.patch.object(net.requests, "get", return_value=limited) as get, self.assertRaises(net.WorldError):
            net.get("https://x.test/d")
        self.assertEqual(get.call_count, 1)   # asked to wait two minutes: not hammered again

    def test_ttl_cache_answers_without_a_second_request(self):
        with mock.patch.object(net.requests, "get", return_value=reply({"n": 1})) as get:
            first = net.get("https://x.test", {"a": 1}, ttl=60)
            second = net.get("https://x.test", {"a": 1}, ttl=60)
            net.get("https://x.test", {"a": 2}, ttl=60)   # different parameters — a different response
        self.assertIs(first, second)
        self.assertEqual(get.call_count, 2)

    def test_the_cache_is_bounded_by_count_and_size_and_each_entry_keeps_its_own_ttl(self):
        def get(url, **kw):
            return SimpleNamespace(status_code=200, url=url, headers={}, content=b"x" * (2_000_000 if "big" in url else 10))
        with mock.patch.object(net.requests, "get", get), mock.patch.object(net.events, "log"):
            for i in range(net.CACHE_MOST + 100):   # article pages kept for an hour
                net.get(f"https://example.com/a{i}", ttl=3600)
            self.assertEqual(len(net._cache), net.CACHE_MOST)
            net.get("https://example.com/feed", ttl=600)   # a short ttl no longer drops the entries still valid for their own hour
            self.assertEqual(len(net._cache), net.CACHE_MOST)
            self.assertIn(("https://example.com/feed", "null"), net._cache)
            for i in range(40):   # big bodies: the oldest go once the total passes the cap
                net.get(f"https://example.com/big{i}", ttl=3600)
            self.assertLessEqual(sum(entry[2] for entry in net._cache.values()), net.CACHE_BYTES)
        with mock.patch.object(net.requests, "get", lambda url, **kw: SimpleNamespace(status_code=200, headers={}, content=b"x" * (net.CACHE_BYTES // 5))):
            net.get("https://example.com/huge2", ttl=3600)
        self.assertNotIn(("https://example.com/huge2", "null"), net._cache)   # one huge page doesn't push everything else out
        old = time.time() - 700
        net._cache.clear()
        with mock.patch.object(net.requests, "get", get), mock.patch.object(net.events, "log"):
            net.get("https://example.com/news", ttl=600)
            net._cache[("https://example.com/news", "null")] = (old, *net._cache[("https://example.com/news", "null")][1:])
            net.get("https://example.com/other", ttl=60)
        self.assertNotIn(("https://example.com/news", "null"), net._cache)   # past its own ttl: dropped on the next insert

    def test_a_count_the_model_gave_is_kept_in_range_and_garbage_gets_the_default(self):
        from sena.core.tools import clamp
        self.assertEqual([clamp(v, 5, 1, 10) for v in (3, "7", 2.9, 0, 99, "пять", None, "", float("nan"), float("inf"))], [3, 7, 2, 1, 10, 5, 5, 5, 5, 5])
        with mock.patch.object(maps_tomtom, "geocode", return_value=(10.9, 108.2, "Муйне")), \
                mock.patch.object(maps_tomtom, "_get", return_value={"results": []}) as get, mock.patch.object(config, "TOMTOM_KEY", "k"):
            maps_tomtom.places("кофе", near="Муйне", limit="пять")   # crashed with ValueError before
        self.assertEqual(get.call_args.args[1]["limit"], 5)

    def test_a_public_fetch_keeps_a_key_to_its_own_site_and_can_stay_put(self):
        asked = []

        def get(url, headers=None, **kw):
            asked.append((url, dict(headers or {})))
            if url == "https://a.example/x":
                return SimpleNamespace(status_code=302, is_redirect=True, headers={"location": "https://b.example/y"}, close=lambda: None)
            return SimpleNamespace(status_code=200, is_redirect=False, headers={}, url=url, content=b"ok")
        with mock.patch.object(net.requests, "get", get), mock.patch.object(net, "private_host", return_value=None), \
                mock.patch.object(net, "_guard", lambda reply, who, error: reply):
            net.get("https://a.example/x", headers={"Authorization": "Bearer k", "User-Agent": "Sena"}, public=True)
            self.assertEqual(asked[1], ("https://b.example/y", {"User-Agent": "Sena"}))   # the key stayed with a.example
            asked.clear()
            with self.assertRaises(net.WorldError):
                net.get("https://a.example/x", public=True, redirects=False)   # not followed
            self.assertEqual([u for u, _ in asked], ["https://a.example/x"])

    def test_no_ttl_means_no_cache(self):
        with mock.patch.object(net.requests, "get", return_value=reply({})) as get:
            net.get("https://x.test")
            net.get("https://x.test")
        self.assertEqual(get.call_count, 2)


class MapsDispatcher(unittest.TestCase):
    def setUp(self):
        config.USAGE_FILE.unlink(missing_ok=True)

    def test_providers_go_tomtom_then_google_then_openstreetmap_which_needs_no_key(self):
        with mock.patch.object(config, "TOMTOM_KEY", "t"), mock.patch.object(config, "GOOGLE_MAPS_KEY", "g"):
            self.assertEqual(maps._providers(), [maps_tomtom, maps_google, maps_osm])
        with mock.patch.object(config, "TOMTOM_KEY", ""), mock.patch.object(config, "GOOGLE_MAPS_KEY", "g"):
            self.assertEqual(maps._providers(), [maps_google, maps_osm])
        with mock.patch.object(config, "TOMTOM_KEY", ""), mock.patch.object(config, "GOOGLE_MAPS_KEY", ""), mock.patch.object(maps_osm, "places", return_value="из OSM"):
            self.assertEqual(maps._providers(), [maps_osm])
            self.assertEqual(maps.places("кофе", near="Mui Ne"), "из OSM")   # maps still work even without keys

    def test_a_silent_main_source_falls_back_and_the_answer_says_whose_data_it_is(self):
        with mock.patch.object(config, "TOMTOM_KEY", "t"), mock.patch.object(config, "GOOGLE_MAPS_KEY", ""), \
                mock.patch.object(maps_tomtom, "route", side_effect=MapsError("TomTom 503")), mock.patch.object(maps_osm, "route", return_value="На машине: 30 мин"):
            out = maps.route("A", "B")
        self.assertEqual(out, "(Основной источник не помог: TomTom: TomTom 503. Данные OpenStreetMap.)\nНа машине: 30 мин")
        with mock.patch.object(config, "TOMTOM_KEY", "t"), mock.patch.object(config, "GOOGLE_MAPS_KEY", ""), mock.patch.object(maps_tomtom, "places", side_effect=MapsError("а")), \
                mock.patch.object(maps_osm, "places", side_effect=MapsError("б")):
            with self.assertRaises(MapsError) as e:
                maps.places("кофе", near="x")
        self.assertIn("Ни один источник карт не ответил", str(e.exception))
        self.assertIn("TomTom: а", str(e.exception))
        self.assertIn("OpenStreetMap: б", str(e.exception))

    def test_counts_only_successful_calls_and_limit_can_be_switched_on(self):
        with mock.patch.object(config, "TOMTOM_KEY", "t"), mock.patch.object(maps_tomtom, "places", return_value="ok"):
            maps.places("кофе", near="x")
            maps.places("кофе", near="x")
        self.assertEqual(store.load_json(config.USAGE_FILE, {}).get("maps"), 2)
        with mock.patch.object(config, "TOMTOM_KEY", "t"), mock.patch.object(config, "GOOGLE_MAPS_KEY", ""), mock.patch.object(maps_tomtom, "places", side_effect=MapsError("сбой")), \
                mock.patch.object(maps_osm, "places", side_effect=MapsError("и запасной тоже")):
            with self.assertRaises(MapsError):
                maps.places("кофе", near="x")
        self.assertEqual(store.load_json(config.USAGE_FILE, {}).get("maps"), 2)
        with mock.patch.object(config, "TOMTOM_KEY", "t"), mock.patch.object(maps, "LIMIT_PER_DAY", 2), \
             mock.patch.object(maps_tomtom, "places", side_effect=AssertionError("запрос не должен уходить")):
            with self.assertRaises(MapsError) as e:
                maps.places("кофе", near="x")
            self.assertIn("Дневной лимит", str(e.exception))
        self.assertIsNone(maps.LIMIT_PER_DAY)

    def test_shared_counter_is_reset_each_day(self):
        store.save_json(config.USAGE_FILE, {"date": "2020-01-01", "maps": 999})
        limits.check("maps", 5, "запросов")   # the stale count doesn't matter: the limit isn't exhausted
        limits.spend("maps")
        self.assertEqual(limits.usage()["maps"], 1)   # the counter started over instead of continuing from 999

    def test_unknown_travel_mode_is_refused(self):
        with mock.patch.object(config, "TOMTOM_KEY", "t"):
            with self.assertRaises(MapsError):
                maps.route("A", "B", mode="rocket")


class NoProxy(unittest.TestCase):
    def test_no_request_goes_through_the_macs_system_proxy(self):
        """review R5: requests read the Mac's system proxy (a VPN app's 127.0.0.1:7890) though config drops the proxy variables:
        every public-only fetch dialled inside this Mac and was refused, and the site watch told the owner his site was down."""
        import os
        import urllib.request
        from requests.adapters import HTTPAdapter
        from requests.utils import get_environ_proxies, select_proxy
        hops, system = [], {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}

        def send(adapter, request, proxies=None, **kw):   # nothing is dialled: the request is only looked at
            hops.append((request.url, select_proxy(request.url, proxies)))
            answer = net.requests.Response()
            answer.status_code, answer.url, answer.request, answer._content, answer._content_consumed = (
                302 if request.url.endswith("/away") else 200), request.url, request, b"", True
            answer.headers["location"] = "https://b.example/here"
            return answer
        with mock.patch.dict(os.environ), mock.patch.object(urllib.request, "getproxies_macosx_sysconf", return_value=system), \
                mock.patch.object(urllib.request, "proxy_bypass_macosx_sysconf", return_value=False), mock.patch.object(HTTPAdapter, "send", send), \
                mock.patch.object(net, "private_host", return_value=None), mock.patch.object(net, "_guard", lambda reply, who, error: reply):
            for name in ("NO_PROXY", "no_proxy"):
                os.environ.pop(name, None)   # the Mac's own setting, nothing in the environment to override it
            self.assertEqual(get_environ_proxies("https://a.example/"), system)   # requests sees the system proxy ...
            net.get("https://a.example/away", public=True)   # a page from someone's text, through a redirect
            net.probe("https://a.example/site", public=True)   # the site watch
            net.get("https://a.example/rates")
            net.send("POST", "https://a.example/api", {"x": 1})
        self.assertEqual([url for url, _ in hops], ["https://a.example/away", "https://b.example/here", "https://a.example/site",
                                                    "https://a.example/rates", "https://a.example/api"])
        self.assertEqual([proxy for _, proxy in hops], [None] * 5)   # ... and no hop goes through it


if __name__ == "__main__":
    unittest.main()
