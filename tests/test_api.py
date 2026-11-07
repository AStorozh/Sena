"""Request parameter parsing (sena/phone/api.py): numbers are clamped to bounds, garbage and omissions become a client error with a clear message."""
import unittest

from sena.phone.api import ApiError, field, number


class Numbers(unittest.TestCase):
    def test_missing_gives_default_and_out_of_range_is_clamped(self):
        self.assertEqual(number({}, "limit", 50), 50)
        self.assertEqual(number({"limit": ""}, "limit", 50), 50)
        self.assertEqual(number({"limit": "0"}, "limit", 50, 1, 100), 1)       # limit=0 must not mean "no limit"
        self.assertEqual(number({"limit": "-7"}, "limit", 50, 1, 100), 1)
        self.assertEqual(number({"limit": "99999999"}, "limit", 50, 1, 100), 100)
        self.assertEqual(number({"offset": "-1"}, "offset", -1, -1, 10), -1)
        self.assertEqual(number({"since": "12.5"}, "since", None, 0, 100, float), 12.5)

    def test_garbage_is_a_client_error(self):
        for raw in ("abc", "1e999999", "nan", "inf", "5; drop"):
            with self.assertRaises(ApiError) as got:
                number({"limit": raw}, "limit", 1, 1, 100, float if raw in ("nan", "inf", "1e999999") else int)
            self.assertEqual(got.exception.code, 400)
            self.assertIn("limit", got.exception.text)


class Fields(unittest.TestCase):
    def test_required_and_optional(self):
        self.assertEqual(field({"id": "a"}, "id"), "a")
        self.assertEqual(field({}, "when", str, ""), "")
        self.assertIsNone(field({"x": None}, "x", str, None))
        with self.assertRaises(ApiError) as got:
            field({}, "id")
        self.assertEqual((got.exception.code, got.exception.text), (400, "Не хватает поля «id»"))

    def test_types(self):
        self.assertEqual(field({"on": True}, "on", bool), True)
        self.assertEqual(field({"n": 5}, "n", float), 5)             # an integer fits a float field
        self.assertEqual(field({"id": 7}, "id", (str, int)), 7)
        for body, key, kind in (({"on": "yes"}, "on", bool), ({"n": True}, "n", int), ({"n": True}, "n", float), ({"id": 1}, "id", str),
                                ({"a": "x"}, "a", list), ({"a": [1]}, "a", dict)):
            with self.assertRaises(ApiError, msg=f"{body} {kind}") as got:
                field(body, key, kind)
            self.assertEqual(got.exception.code, 400)
            self.assertIn(f"«{key}»", got.exception.text)


if __name__ == "__main__":
    unittest.main()
