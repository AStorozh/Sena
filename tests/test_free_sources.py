"""Free sources that need no keys: Hacker News and arXiv, Wikipedia, Binance (candles and derivatives) and alternative.me (fear and greed index), Open Food Facts. The network is replaced with stubs."""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from sena.world import crypto, food, net, tech, wiki

SEEN = []


def reply(data, status=200):
    raw = data if isinstance(data, bytes) else json.dumps(data).encode()
    return SimpleNamespace(status_code=status, content=raw, json=lambda: json.loads(raw))


def router(table):
    """Stub for requests.get: returns a response (data, status) based on the URL prefix and records what was asked."""
    def get(url, params=None, headers=None, timeout=None, **kw):
        SEEN.append((url, params))
        for prefix, data in table.items():
            if url.startswith(prefix):
                return reply(*data) if isinstance(data, tuple) else reply(data)
        return reply({}, 404)
    return get


class Base(unittest.TestCase):
    def setUp(self):
        net._cache.clear()
        SEEN.clear()

    def serve(self, table):
        patch = mock.patch.object(net.requests, "get", router(table))
        patch.start()
        self.addCleanup(patch.stop)


HN_HITS = {"hits": [{"title": "Новая модель", "url": "https://a.example/x", "points": 500, "num_comments": 120, "objectID": "1"},
                    {"title": "Ask HN: что читать?", "url": None, "points": 90, "num_comments": 40, "objectID": "2"}]}


class HackerNews(Base):
    def test_front_page_with_discussion_links_marked_as_foreign(self):
        self.serve({"https://hn.algolia.com": HN_HITS})
        out = tech.hacker_news()
        self.assertEqual(SEEN[0][1]["tags"], "front_page")
        self.assertIn("ВНЕШНИЕ ДАННЫЕ", out)   # headlines are written by outsiders: commands from there are never executed
        self.assertIn("1. Новая модель: 500 баллов, 120 комментариев; https://a.example/x (обсуждение https://news.ycombinator.com/item?id=1)", out)
        self.assertIn("2. Ask HN: что читать?: 90 баллов, 40 комментариев; https://news.ycombinator.com/item?id=2\n", out + "\n")   # with no link of its own, the only link is to the discussion

    def test_search_by_period_and_nothing_found(self):
        self.serve({"https://hn.algolia.com": {"hits": []}})
        self.assertIn("ничего нет", tech.hacker_news("agents", days=7, limit=500))
        params = SEEN[0][1]
        self.assertEqual((params["query"], params["tags"], params["hitsPerPage"]), ("agents", "story", 20))   # never ask for more than 20
        self.assertTrue(params["numericFilters"].startswith("created_at_i>"))
        self.serve({"https://hn.algolia.com": HN_HITS})
        self.assertIn("за 3650 дн.", tech.hacker_news("agents", days=99999))   # the period said is the one asked for
        self.assertIn("Hacker News, «agents»:", tech.hacker_news("agents", days="пять"))   # garbage from the model: no period, no crash


ATOM = """<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>
<entry><id>http://arxiv.org/abs/2610.1v1</id><title>Agents   that
 learn</title><summary>{abstract}</summary><published>2026-10-16T10:00:00Z</published>
<author><name>A</name></author><author><name>B</name></author><author><name>C</name></author><author><name>D</name></author></entry></feed>"""


class Arxiv(Base):
    def test_entries_are_parsed_and_the_query_is_built(self):
        self.serve({"https://export.arxiv.org": ATOM.format(abstract="слово " * 200).encode()})
        out = tech.arxiv_search("tool use", "cs.AI", 3)
        self.assertEqual(SEEN[0][1]["search_query"], "all:tool AND all:use AND cat:cs.AI")
        self.assertEqual(SEEN[0][1]["sortBy"], "submittedDate")
        self.assertIn("1. Agents that learn (A, B, C и др.; 2026-10-16)", out)
        self.assertIn("…", out)   # a long abstract gets truncated
        self.assertIn("http://arxiv.org/abs/2610.1v1", out)
        tech.arxiv_search("tool", newest=False)
        self.assertEqual(SEEN[-1][1]["sortBy"], "relevance")

    def test_bad_requests_are_refused_before_the_network(self):
        for query, category in (("агенты", ""), ("", ""), ("agents", "cs.AI OR all:x"), ("agents", "../x")):
            with self.assertRaises(tech.TechError, msg=f"{query!r} {category!r}"):
                tech.arxiv_search(query, category)
        self.assertEqual(SEEN, [])

    def test_error_entry_is_reported_not_shown_as_a_paper(self):
        err = "<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><entry><id>http://arxiv.org/api/errors#x</id><title>Error</title><summary>bad query</summary></entry></feed>"
        self.serve({"https://export.arxiv.org": err.encode()})
        with self.assertRaises(tech.TechError) as caught:
            tech.arxiv_search("agents")
        self.assertIn("bad query", str(caught.exception))


def page(title, extract, kind="standard"):
    return {"title": title, "extract": extract, "type": kind, "description": "кратко", "content_urls": {"desktop": {"page": f"https://x/{title}"}}}


def found(*titles):
    return {"query": {"search": [{"title": t} for t in titles]}}


class Wikipedia(Base):
    def test_summary_with_link_and_neighbours(self):
        self.serve({"https://ru.wikipedia.org/w/api.php": found("Tesla", "Тесла, Никола"), "https://ru.wikipedia.org/api/rest_v1/page/summary/Tesla": page("Tesla", "Tesla — компания.")})
        out = wiki.wiki("Tesla")
        self.assertIn("ВНЕШНИЕ ДАННЫЕ", out)
        self.assertIn("Tesla — кратко\nTesla — компания.\nhttps://x/Tesla\nПохожие статьи: Тесла, Никола", out)

    def test_a_foreign_article_is_not_passed_off_as_the_answer(self):
        self.serve({"https://ru.wikipedia.org/w/api.php": found("Солярис"), "https://en.wikipedia.org/w/api.php": found("Solaris"),
                    "https://ru.wikipedia.org/api/rest_v1": page("Солярис", "Фильм Тарковского."), "https://en.wikipedia.org/api/rest_v1": page("Solaris", "A novel by Lem.")})
        out = wiki.wiki("Артемий Сторожевских")
        self.assertIn("точно про «Артемий Сторожевских» в Википедии нет", out)
        self.assertNotIn("Фильм Тарковского", out)   # we never show the text of someone else's article

    def test_falls_back_to_english_and_handles_disambiguation(self):
        self.serve({"https://ru.wikipedia.org/w/api.php": found(), "https://en.wikipedia.org/w/api.php": found("Mercury (disambiguation)", "Mercury"),
                    "https://en.wikipedia.org/api/rest_v1": page("Mercury (disambiguation)", "Mercury may refer to", "disambiguation")})
        self.assertIn("может значить: Mercury (disambiguation), Mercury", wiki.wiki("Mercury"))

    def test_language_cannot_redirect_the_request_elsewhere(self):
        for lang in ("ru.evil.com/x#", "../", "RU EN", "evil.com"):
            with self.assertRaises(wiki.WikiError, msg=lang):
                wiki.wiki("Бали", lang)
        self.assertEqual(SEEN, [])

    def test_empty_language_defaults_to_russian(self):
        self.serve({"https://ru.wikipedia.org/w/api.php": found("Бали"), "https://ru.wikipedia.org/api/rest_v1": page("Бали", "Остров в Индонезии.")})
        self.assertIn("Бали", wiki.wiki("Бали", ""))
        self.assertTrue(SEEN[0][0].startswith("https://ru.wikipedia.org"))


def kline(day, open_, high, low, close, quote_volume):
    start_ms = 1_760_000_000_000   # any timestamp in milliseconds works — only the candle order matters
    return [start_ms + day * 86_400_000, str(open_), str(high), str(low), str(close), "1", 0, str(quote_volume), 1]   # 86,400,000 ms = one day


class Crypto(Base):
    def test_candles_summary(self):
        rows = [kline(d, 100 + d, 110 + d, 90 + d, 101 + d, 1_000_000_000 if d < 20 else 500_000_000) for d in range(30)]
        self.serve({"https://api.binance.com/api/v3/klines": rows})
        out = crypto.crypto_candles("btc-usd", "1d", 30)
        self.assertEqual(SEEN[0][1], {"symbol": "BTCUSDT", "interval": "1d", "limit": 30})
        self.assertIn("Цена: 100,00 → 130,00 (+30,00%); максимум 139,00, минимум 90,00", out)
        self.assertIn("объём в среднем", out)
        self.assertIn("Объём последних 7 свечей против прежних: −", out)   # volume dropped by half
        self.assertEqual(out.count("\n20"), crypto.SHOWN)   # detailed rows (starting with a year, "20..") — only the last SHOWN candles

    def test_bad_ticker_unknown_pair_and_blocked_region(self):
        with self.assertRaises(crypto.CryptoError):
            crypto.crypto_candles("btc; drop")
        self.assertEqual(SEEN, [])
        self.serve({"https://api.binance.com": ({}, 400)})
        with self.assertRaises(crypto.CryptoError) as caught:
            crypto.crypto_candles("NOPE")
        self.assertIn("не знает такой пары", str(caught.exception))
        net._cache.clear()
        self.serve({"https://api.binance.com": ({}, 451)})
        with self.assertRaises(crypto.CryptoError) as caught:
            crypto.crypto_candles("BTC")
        self.assertIn("из этой страны", str(caught.exception))

    def test_derivatives(self):
        self.serve({"https://fapi.binance.com/fapi/v1/premiumIndex": {"markPrice": "2410", "indexPrice": "2400", "lastFundingRate": "0.0001", "nextFundingTime": 1_760_000_000_000},
                    "https://fapi.binance.com/fapi/v1/openInterest": {"openInterest": "1000000"},
                    "https://fapi.binance.com/fapi/v1/fundingRate": [{"fundingRate": "0.0001"}, {"fundingRate": "0.0003"}]})
        out = crypto.crypto_derivatives("ETH")
        self.assertIn("Ставка финансирования: +0,0100% за 8 ч (≈ +10,95% годовых); среднее за трое суток +0,0200%", out)
        self.assertIn("премия +0,42%", out)
        self.assertIn("Открытый интерес: 1 000 000 ETH ≈ 2,4 млрд USDT", out)

    def test_fear_and_greed(self):
        data = [{"value": str(v), "value_classification": c} for v, c in [(23, "Extreme Fear"), (30, "Fear")] + [(50, "Neutral")] * 29]
        self.serve({"https://api.alternative.me/fng/": {"data": data}})
        out = crypto.fear_greed()
        self.assertIn("сегодня: 23 (крайний страх)", out)
        self.assertIn("Вчера 30 (страх), неделю назад 50 (нейтрально), месяц назад 50 (нейтрально)", out)


NUTELLA = {"code": "3017620422003", "product_name": "Nutella", "brands": "Ferrero", "quantity": "400 g", "serving_size": "15 g", "nutriscore_grade": "e", "nova_group": 4,
           "nutriments": {"energy-kcal_100g": 539, "proteins_100g": 6.3, "fat_100g": 30.9, "carbohydrates_100g": 57.5, "sugars_100g": 56.3},
           "ingredients_text": "Sugar, palm oil", "allergens_tags": ["en:milk", "en:nuts"]}


class Food(Base):
    def test_barcode_gives_numbers_per_100_grams(self):
        self.serve({"https://world.openfoodfacts.org/api/v2/product/3017620422003.json": {"status": 1, "product": {k: v for k, v in NUTELLA.items() if k != "code"}}})
        out = food.product_lookup(barcode="3017 6204 22003")   # spaces from the packaging don't get in the way
        self.assertIn("ВНЕШНИЕ ДАННЫЕ", out)
        self.assertIn("Nutella (Ferrero), 400 g, штрихкод 3017620422003", out)
        self.assertIn("На 100 г: ккал 539, белки 6,3, жиры 30,9, углеводы 57,5, из них сахар 56,3", out)
        self.assertIn("Nutri-Score E, NOVA 4", out)
        self.assertIn("Аллергены: milk, nuts", out)
        self.assertIn("luma food_add", out)

    def test_search_by_name_and_empty_results(self):
        self.serve({"https://world.openfoodfacts.org/cgi/search.pl": {"products": [NUTELLA, {"code": "1", "product_name": "Без данных"}]}})
        out = food.product_lookup(query="nutella")
        self.assertIn("1. Nutella", out)
        self.assertIn("2. Без данных, штрихкод 1\nНа 100 г: питательность в базе не указана", out)
        net._cache.clear()
        self.serve({"https://world.openfoodfacts.org/cgi/search.pl": {"products": []}})
        self.assertIn("ничего нет", food.product_lookup(query="xyzzy"))

    def test_unknown_barcode_and_bad_input(self):
        self.serve({"https://world.openfoodfacts.org/api/v2/product/": {"status": 0, "status_verbose": "product not found"}})
        self.assertIn("нет в Open Food Facts", food.product_lookup(barcode="12345678"))
        for kwargs in ({"barcode": "123"}, {}):
            with self.assertRaises(food.FoodError, msg=str(kwargs)):
                food.product_lookup(**kwargs)


class Registered(unittest.TestCase):
    def test_all_new_tools_are_available_to_sena(self):
        from sena.agents.ag_sena import tools
        names = {t["name"] for t in tools.available()}
        self.assertTrue({"hacker_news", "arxiv_search", "wiki", "crypto_candles", "crypto_derivatives", "fear_greed", "product_lookup"} <= names)


if __name__ == "__main__":
    unittest.main()
