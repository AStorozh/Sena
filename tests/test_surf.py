"""Surf forecast: wind and swell geometry, scoring, filtering by level, hourly breakdown for a spot. Open-Meteo is replaced with a stub."""
import unittest
from datetime import date, datetime
from unittest import mock

from sena.world import net, surf


def marine(swell_h=1.5, period=11, direction=200):
    day = date.today().isoformat()
    times = [f"{day}T{h:02d}:00" for h in range(24)] + [f"{(date.fromordinal(date.today().toordinal() + 1)).isoformat()}T{h:02d}:00" for h in range(24)]
    n = len(times)
    return {"hourly": {"time": times, "wave_height": [swell_h] * n, "swell_wave_height": [swell_h] * n, "swell_wave_period": [period] * n,
                       "swell_wave_direction": [direction] * n, "wind_wave_height": [0.1] * n,
                       "sea_level_height_msl": [h / 10 for h in range(n)], "sea_surface_temperature": [27.0] * n}}


def air(speed=10, direction=110):
    day = date.today().isoformat()
    times = [f"{day}T{h:02d}:00" for h in range(24)] + [f"{(date.fromordinal(date.today().toordinal() + 1)).isoformat()}T{h:02d}:00" for h in range(24)]
    n = len(times)
    return {"hourly": {"time": times, "wind_speed_10m": [speed] * n, "wind_direction_10m": [direction] * n, "precipitation": [0.0] * n,
                       "temperature_2m": [28.0] * n, "uv_index": [3.0] * n}}


class Geometry(unittest.TestCase):
    def test_angles_wrap_around(self):
        self.assertEqual(surf.angle(350, 10), 20)
        self.assertEqual(surf.angle(90, 270), 180)
        self.assertEqual(surf.compass(200), "ЮЮЗ")
        self.assertEqual(surf.compass(359), "С")

    def test_swell_from_behind_the_beach_does_not_count(self):
        self.assertAlmostEqual(surf.exposure(270, 270), 1.0)
        self.assertGreater(surf.exposure(270, 240), surf.exposure(270, 200))
        self.assertEqual(surf.exposure(270, 90), 0.0)   # swell coming from land
        self.assertEqual(surf.exposure(270, 180), 0.0)   # strictly side-on: the beach is shadowed

    def test_offshore_beats_onshore_and_strong_wind_spoils(self):
        off, onw = surf.wind_kind(270, 90, 12)[0], surf.wind_kind(270, 270, 12)[0]
        self.assertEqual(surf.wind_kind(270, 90, 12)[1], "оффшор")
        self.assertEqual(surf.wind_kind(270, 270, 12)[1], "оншор")
        self.assertGreater(off, onw * 3)
        self.assertLess(surf.wind_kind(270, 90, 35)[0], off)
        self.assertEqual(surf.wind_kind(270, 270, 3)[1], "штиль")   # with no wind, direction doesn't matter

    def test_size_by_level(self):
        self.assertGreater(surf.size_score(0.7, "beginner"), surf.size_score(2.5, "beginner"))
        self.assertGreater(surf.size_score(2.0, "advanced"), surf.size_score(0.5, "advanced"))
        self.assertLess(surf.size_score(0.1, "intermediate"), 0.1)
        self.assertGreater(surf.period_score(13), surf.period_score(6))


SPOTS = [
    {"name": "Offshore Reef", "area": "Тест", "lat": 1, "lon": 1, "facing": 250, "kind": "риф", "level": "intermediate", "note": "n"},
    {"name": "Onshore Beach", "area": "Тест", "lat": 2, "lon": 2, "facing": 110, "kind": "пляж", "level": "beginner", "note": "n"},
    {"name": "Expert Slab", "area": "Тест", "lat": 3, "lon": 3, "facing": 250, "kind": "риф", "level": "advanced", "note": "n"},
]


class Forecast(unittest.TestCase):
    def setUp(self):
        net._cache.clear()
        self.requests = []
        self.hour = 5   # the spot's local "now" hour: early morning, the whole day is still ahead
        self.patches = [mock.patch.object(surf, "spots", lambda: SPOTS), mock.patch.object(surf, "_fetch", self.fake_fetch),
                        mock.patch.object(surf, "_local_now", lambda offset: datetime(2026, 10, 16, self.hour, 30))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def fake_fetch(self, url, params):
        self.requests.append(url)
        n = len(params["latitude"].split(","))
        return [marine() if url == surf.MARINE else air() for _ in range(n)]

    def test_ranking_prefers_open_beach_with_offshore_wind(self):
        out = surf.forecast(day=0, part="morning", level="intermediate")
        lines = [l for l in out.split("\n") if l[:2] in ("1.", "2.", "3.")]
        self.assertTrue(lines[0].startswith("1. Offshore Reef"), out)
        self.assertIn("оффшор", lines[0])
        self.assertIn("Onshore Beach", lines[1])
        self.assertIn("оншор", lines[1])
        self.assertIn("ориентир, а не замена взгляду на воду", out)

    def test_spots_harder_than_level_are_not_advised(self):
        self.assertNotIn("Expert Slab", surf.forecast(level="intermediate"))
        self.assertNotIn("Offshore Reef", surf.forecast(level="beginner"))
        self.assertIn("Expert Slab", surf.forecast(level="advanced"))

    def test_one_spot_hour_by_hour_and_its_level_is_not_filtered(self):
        out = surf.forecast(day=0, part="morning", level="beginner", spot="expert")
        self.assertIn("Expert Slab", out)
        self.assertEqual(out.count(":00 — оценка"), 4)   # 6, 7, 8, 9
        self.assertIn("уровень моря", out)

    def test_two_calls_total_for_all_spots_and_tomorrow_works(self):
        out = surf.forecast(day=1, part="midday")
        self.assertEqual(sorted(self.requests), sorted([surf.MARINE, surf.FORECAST]))
        self.assertIn("завтра", out)

    def test_morning_that_has_already_passed_moves_to_tomorrow(self):
        self.hour = 14
        out = surf.forecast(day=0, part="morning")
        self.assertIn("Сегодня утро (6–10) уже прошло, показываю завтра", out)
        self.assertIn("завтра", out)

    def test_part_already_started_shows_only_remaining_hours(self):
        self.hour = 8
        out = surf.forecast(day=0, part="morning", spot="expert")
        self.assertIn("Сейчас 8:00, показываю оставшиеся часы", out)
        self.assertEqual(out.count(":00 — оценка"), 2)   # 8 and 9; six and seven have already passed

    def test_single_spot_is_not_listed_as_the_worst(self):
        with mock.patch.object(surf, "spots", lambda: SPOTS[:1]):
            self.assertNotIn("Хуже всего", surf.forecast(level="intermediate"))

    def test_bad_input(self):
        with self.assertRaises(net.WorldError):
            surf.forecast(part="night")
        with self.assertRaises(net.WorldError):
            surf.forecast(level="pro")
        with self.assertRaises(net.WorldError) as e:
            surf.forecast(spot="Нигде")
        self.assertIn("Offshore Reef", str(e.exception))


class Spots(unittest.TestCase):
    def test_file_is_valid(self):
        data = surf.spots()
        self.assertGreaterEqual(len(data), 20)
        names = [s["name"] for s in data]
        self.assertEqual(len(names), len(set(names)))
        for s in data:
            self.assertTrue(-9.2 < s["lat"] < -8.0 and 114.0 < s["lon"] < 116.0, s["name"])   # Bali
            self.assertTrue(0 <= s["facing"] < 360)
            self.assertIn(s["level"], surf.LEVEL_ORDER)


if __name__ == "__main__":
    unittest.main()
