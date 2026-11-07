"""More open sources: books, PubMed, Stack Overflow, Hugging Face, countries, "on this day", recipes, world time, site and service statuses. Network is replaced by stubs."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sena import config
from sena.world import books, clock, countries, net, pubmed, recipes, tech, uptime, weather, wiki
from tests.test_free_sources import SEEN, Base


class Books(Base):
    def test_results_with_cover_and_description_of_the_first(self):
        self.serve({"https://openlibrary.org/search.json": {"docs": [{"key": "/works/OL1W", "title": "Dune", "author_name": ["Frank Herbert"], "first_publish_year": 1965, "edition_count": 155,
                                                                      "cover_i": 11, "ratings_average": 4.3, "ratings_count": 448, "number_of_pages_median": 608}]},
                    "https://openlibrary.org/works/OL1W.json": {"description": {"value": "Пустыня и пряность.\r\n\r\n----------\r\nСм. также издания"}}})
        out = books.book_search("dune herbert", 3)
        self.assertIn("ВНЕШНИЕ ДАННЫЕ", out)
        self.assertIn("1. Dune: Frank Herbert; 1965; изданий: 155; рейтинг 4.3 из 5 (448 оценок); 608 стр.", out)
        self.assertIn("https://covers.openlibrary.org/b/id/11-L.jpg", out)
        self.assertIn("О первой книге: Пустыня и пряность.", out)
        self.assertNotIn("См. также", out)   # after the dash comes links to editions

    def test_field_mixed_with_a_bare_word_is_retried_as_plain_words(self):
        calls = []

        def get(url, params=None, headers=None, timeout=None, **kw):
            calls.append(params)
            return SimpleNamespace(status_code=200, content=b"", json=lambda: {"docs": [] if "author:" in (params or {}).get("q", "") else [{"key": "/works/OL1W", "title": "Dune"}]})
        with mock.patch.object(net.requests, "get", get):
            out = books.book_search("author:herbert dune")
        self.assertEqual([c["q"] for c in calls[:2]], ["author:herbert dune", "herbert dune"])
        self.assertIn("1. Dune", out)
        with self.assertRaises(books.BooksError):
            books.book_search("  ")


ARTICLES = """<?xml version='1.0'?><PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>111</PMID><Article>
<Journal><Title>Sleep Med</Title><JournalIssue><PubDate><Year>2022</Year></PubDate></JournalIssue></Journal>
<ArticleTitle>Melatonin and <i>sleep</i>: a review</ArticleTitle>
<Abstract><AbstractText Label="RESULTS">Longer sleep.</AbstractText><AbstractText Label="CONCLUSIONS">Helps a little, in adults.</AbstractText></Abstract>
<AuthorList><Author><LastName>Ivanov</LastName></Author><Author><LastName>Smith</LastName></Author><Author><LastName>Lee</LastName></Author></AuthorList>
<PublicationTypeList><PublicationType>Journal Article</PublicationType><PublicationType>Meta-Analysis</PublicationType></PublicationTypeList>
</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""


class PubMed(Base):
    def test_reviews_are_searched_by_default_and_conclusions_are_shown(self):
        self.serve({"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi": {"esearchresult": {"count": "296", "idlist": ["111"]}},
                    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi": ARTICLES.encode()})
        out = pubmed.health_research("melatonin sleep")
        self.assertEqual(SEEN[0][1]["term"], "(melatonin sleep) AND (systematic review[pt] OR meta-analysis[pt])")
        self.assertIn("найдено 296", out)
        self.assertIn("Melatonin and sleep: a review (Ivanov, Smith и др.; Sleep Med, 2022; Meta-Analysis)", out)
        self.assertIn("Helps a little, in adults.", out)
        self.assertNotIn("Longer sleep.", out)   # authors' conclusions matter more than the middle of the abstract
        self.assertIn("https://pubmed.ncbi.nlm.nih.gov/111/", out)
        self.assertIn("не медицинский совет", out)

    def test_other_kinds_empty_results_and_russian_query(self):
        self.serve({"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi": {"esearchresult": {"count": "0", "idlist": []}}})
        self.assertIn("ничего нет", pubmed.health_research("zzz", kind="trials"))
        self.assertEqual(SEEN[0][1]["term"], "(zzz) AND randomized controlled trial[pt]")
        pubmed.health_research("zzz", kind="any")
        self.assertEqual(SEEN[-1][1]["term"], "(zzz)")
        with self.assertRaises(pubmed.PubMedError):
            pubmed.health_research("мелатонин сон")


class StackAndFaces(Base):
    def test_low_scored_questions_are_dropped_and_the_answer_is_readable(self):
        questions = {"items": [{"title": "Junk", "score": 0, "accepted_answer_id": 1, "link": "https://s/1", "tags": ["python"]},
                               {"title": "KeyError &amp; dicts", "score": 40, "view_count": 9000, "accepted_answer_id": 2, "link": "https://s/2", "tags": ["python", "dict"]}]}
        answers = {"items": [{"answer_id": 2, "score": 77, "body": "<p>Use <code>get</code>:</p><pre><code>d.get(&quot;k&quot;)</code></pre>"}]}
        self.serve({"https://api.stackexchange.com/2.3/search/advanced": questions, "https://api.stackexchange.com/2.3/answers/": answers})
        out = tech.stackoverflow("python KeyError", 1)
        self.assertEqual(SEEN[0][1]["pagesize"], 3)
        self.assertNotIn("Junk", out)
        self.assertIn("KeyError & dicts (голосов 40, просмотров 9000; python, dict)", out)
        self.assertIn('```\n   d.get("k")', out)
        self.assertEqual(SEEN[1][0], "https://api.stackexchange.com/2.3/answers/2")

    def test_no_questions_and_empty_query(self):
        self.serve({"https://api.stackexchange.com": {"items": []}})
        self.assertIn("нет вопросов с принятым ответом", tech.stackoverflow("zzzz"))
        with self.assertRaises(tech.TechError):
            tech.stackoverflow("")

    def test_hugging_face_models_and_papers(self):
        self.serve({"https://huggingface.co/api/models": [{"id": "a/b", "pipeline_tag": "text-generation", "likes": 5, "downloads": 9, "createdAt": "2026-10-01T00:00:00Z"}],
                    "https://huggingface.co/api/daily_papers": [{"paper": {"id": "2610.1", "title": "Новая работа", "summary": "Короткое описание", "upvotes": 7, "publishedAt": "2026-10-12T00:00:00Z"}}]})
        out = tech.huggingface("models", "text-generation", "llama", 3)
        self.assertEqual((SEEN[0][1]["pipeline_tag"], SEEN[0][1]["search"], SEEN[0][1]["limit"]), ("text-generation", "llama", 3))
        self.assertIn("1. a/b (text-generation; лайков 5, скачиваний 9; создана 2026-10-01)\n   https://huggingface.co/a/b", out)
        papers = tech.huggingface("papers")
        self.assertIn("Новая работа (голосов 7, 2026-10-12)", papers)
        self.assertIn("https://huggingface.co/papers/2610.1", papers)


def sparql(rows):
    return {"results": {"bindings": [{k: {"value": v} for k, v in row.items()} for row in rows]}}


ID_ROWS = [{"cLabel": "Индонезия", "iso": "ID", "capLabel": "Джакарта", "curLabel": "индонезийская рупия", "curCode": "IDR", "langLabel": "индонезийский язык", "tzLabel": tz, "code": "+62", "driveLabel": "слева"}
           for tz in ("Asia/Jakarta", "Asia/Makassar", "Indonesia Central Standard Time")]


class Countries(Base):
    def test_info_by_name_goes_through_search_then_the_country_card(self):
        self.serve({"https://www.wikidata.org/w/api.php": {"search": [{"id": "Q252"}, {"id": "Q1"}]}, "https://query.wikidata.org/sparql": sparql(ID_ROWS)})
        out = countries.country_info("Индонезия")
        self.assertIn("VALUES ?c { wd:Q252 wd:Q1 }", SEEN[1][1]["query"])
        for line in ("Индонезия (ID)", "Столица: Джакарта", "Валюта: индонезийская рупия (IDR)", "Телефонный код: +62", "Движение: слева", "Часовые пояса: Asia/Jakarta, Asia/Makassar"):
            self.assertIn(line, out)
        self.assertNotIn("Indonesia Central Standard Time", out)   # this is a verbal time zone name, not an IANA one: we drop those, real ones stay

    def test_two_letter_code_skips_the_search_and_non_countries_are_refused(self):
        self.serve({"https://query.wikidata.org/sparql": sparql(ID_ROWS)})
        countries.country_info("id")
        self.assertEqual(len(SEEN), 1)
        self.assertIn('?c wdt:P297 "ID".', SEEN[0][1]["query"])
        self.serve({"https://www.wikidata.org/w/api.php": {"search": [{"id": "Q5"}]}, "https://query.wikidata.org/sparql": sparql([])})
        with self.assertRaises(countries.CountryError):
            countries.country_info("человек")
        with self.assertRaises(countries.CountryError):
            countries.country_info("")

    def test_economy_shows_the_latest_year_of_each_indicator(self):
        def bank(value):
            return [{"page": 1}, [{"date": "2025", "value": None}, {"date": "2024", "value": value}, {"date": "2023", "value": value * 2}]]
        table = {"https://query.wikidata.org/sparql": sparql(ID_ROWS), "https://api.worldbank.org/v2/country/ID/indicator/NY.GDP.MKTP.CD": bank(1.4e12),
                 "https://api.worldbank.org/v2/country/ID/indicator/FP.CPI.TOTL.ZG": bank(2.1), "https://api.worldbank.org/v2/country/ID/indicator/SP.POP.TOTL": bank(2.8e8),
                 "https://api.worldbank.org/v2/country/ID/indicator/SL.UEM.TOTL.ZS": ({"message": "ошибка"}, 200)}
        self.serve({**table, "https://www.wikidata.org/w/api.php": {"search": [{"id": "Q252"}]}})
        out = countries.country_economy("Индонезия")
        self.assertIn("ВВП: 1,4 трлн $ (2024)", out)   # 2025 has no value: we take the latest year that has data
        self.assertIn("инфляция: 2,1% (2024), годом раньше 4,2", out)
        self.assertIn("население: 280 млн (2024)", out)
        self.assertIn("безработица: нет данных", out)   # a failure in one indicator doesn't bring down the rest
        self.assertIn("рост ВВП: не прочиталось", out)   # one that didn't load is not one without data
        net._cache.clear()
        self.serve({"https://query.wikidata.org/sparql": sparql(ID_ROWS), "https://www.wikidata.org/w/api.php": {"search": [{"id": "Q252"}]}})
        with self.assertRaises(countries.CountryError) as caught:   # the bank down is said so, not "нет данных" seven times
            countries.country_economy("Индонезия")
        self.assertIn("не отвечает", str(caught.exception))


class OnThisDay(Base):
    def test_dates_are_parsed_and_events_are_spread_over_history(self):
        events = [{"year": 1000 + i * 10, "text": f"событие {i}"} for i in range(40)]
        self.serve({"https://api.wikimedia.org/feed/v1/wikipedia/ru/onthisday/events/10/19": {"events": events}})
        out = wiki.on_this_day("19.10", "events", limit=4)
        self.assertEqual(SEEN[0][0], "https://api.wikimedia.org/feed/v1/wikipedia/ru/onthisday/events/10/19")
        years = [int(line.split(":")[0]) for line in out.splitlines()[2:6]]
        self.assertEqual(len(years), 4)
        self.assertGreater(years[-1] - years[0], 200)   # not just a run of ancient events
        self.assertEqual(wiki._day("10-19"), (10, 19))
        self.assertEqual(wiki._day("2024-02-29"), (2, 29))   # 2024 is a leap year, February 29 exists
        self.assertEqual((wiki._day("9.10"), wiki._day("1.5")), ((10, 9), (5, 1)))   # a one-digit day crashed the parser

    def test_bad_date_and_language_are_refused(self):
        for day in ("32.01", "abc", "13-45", "31.02"):
            with self.assertRaises(wiki.WikiError, msg=day):
                wiki.on_this_day(day)
        with self.assertRaises(wiki.WikiError):
            wiki.on_this_day("19.10", lang="ru.evil.com/")
        self.assertEqual(SEEN, [])


MEAL = {"strMeal": "Spicy Arrabiata Penne", "strCategory": "Vegetarian", "strArea": "Italian", "strInstructions": "Boil.\r\nMix.", "strIngredient1": "penne", "strMeasure1": "1 pound",
        "strIngredient2": "garlic", "strMeasure2": " ", "strIngredient3": "", "strMealThumb": "https://img/x.jpg"}


class Recipes(Base):
    def test_search_ingredient_list_random_and_russian_refusal(self):
        self.serve({"https://www.themealdb.com/api/json/v1/1/search.php": {"meals": [MEAL, {"strMeal": "Other"}]}, "https://www.themealdb.com/api/json/v1/1/filter.php": {"meals": [{"strMeal": "Curry"}]},
                    "https://www.themealdb.com/api/json/v1/1/random.php": {"meals": [MEAL]}})
        out = recipes.recipe("Arrabiata")
        self.assertIn("Spicy Arrabiata Penne (Vegetarian, кухня: Italian)", out)
        self.assertIn("Ингредиенты: penne — 1 pound; garlic — по вкусу", out)
        self.assertIn("Шаги: Boil. Mix.", out)
        self.assertIn("Похожие блюда: Other", out)
        self.assertIn("Калорий в базе нет", out)
        self.assertIn("Блюда с «chicken breast»: Curry", recipes.recipe(ingredient="chicken breast"))
        self.assertEqual(SEEN[-1][1], {"i": "chicken_breast"})
        self.assertIn("Spicy Arrabiata Penne", recipes.recipe())
        with self.assertRaises(recipes.RecipeError):
            recipes.recipe("паста")


class Clock(unittest.TestCase):
    def setUp(self):
        clock._zone.cache_clear()
        self.addCleanup(clock._zone.cache_clear)

    def test_time_is_converted_from_the_named_place(self):
        out = clock.world_time(["Tokyo", "America/New_York"], at="2026-10-20 15:00", at_place="Asia/Makassar")
        self.assertIn("Когда 2026-10-20 15:00 в Asia/Makassar:", out)
        self.assertIn("Tokyo: 16:00, вторник 20.10 (UTC+9;", out)
        self.assertIn("America/New_York: 03:00, вторник 20.10 (UTC−4;", out)   # in October New York is still on daylight saving time

    def test_unknown_zone_names_go_to_the_geocoder_and_show_the_country(self):
        with mock.patch.object(weather, "locate", return_value={"timezone": "Asia/Kolkata", "country": "Индия"}) as locate:
            out = clock.world_time("Бали", at="12:00", at_place="Asia/Makassar")
        locate.assert_called_once_with("Бали")
        self.assertIn("Бали (Индия): 09:30", out)   # both the person and Sena can tell the wrong Bali was found
        with mock.patch.object(weather, "locate", side_effect=net.WorldError("нет такого места")):
            with self.assertRaises(clock.ClockError):
                clock.world_time("Нигдестан")

    def test_gaps_and_bad_input(self):
        self.assertEqual(clock._gap(0), "время то же, что у тебя")
        self.assertEqual(clock._gap(-1.5), "на 1 ч 30 мин раньше, чем у тебя")
        self.assertEqual(clock._gap(11), "на 11 ч позже, чем у тебя")
        for kwargs in ({"places": []}, {"places": ["Tokyo"], "at": "25:99"}, {"places": ["Tokyo"], "at": "в три"}):
            with self.assertRaises(clock.ClockError, msg=str(kwargs)):
                clock.world_time(**kwargs)


def status_page(level="none", bad=(), incidents=()):
    return {"status": {"indicator": level}, "components": [{"name": n, "status": s} for n, s in bad], "incidents": [{"name": n, "status": s} for n, s in incidents]}


class Uptime(Base):
    def test_private_addresses_are_never_visited(self):
        for url in ("http://127.0.0.1:8765", "http://localhost", "192.168.1.1", "http://10.0.0.5/admin", "http://[::1]/"):
            with self.assertRaises(uptime.UptimeError, msg=url):
                uptime.check_website(url)
        self.assertEqual(SEEN, [])

    def test_redirects_are_followed_by_hand_and_each_hop_is_checked(self):
        replies = {"http://a.example": SimpleNamespace(status_code=301, headers={"Location": "https://a.example/"}), "https://a.example/": SimpleNamespace(status_code=200, headers={})}
        with mock.patch.object(uptime, "_public"), mock.patch.object(net, "probe", lambda url, headers=None, timeout=15, who="", public=False: (replies[url], 0.3, "")), \
                mock.patch.object(uptime, "_cert", return_value=(10.2, "Let's Encrypt")):
            out = uptime.check_website("http://a.example")
        self.assertIn("a.example: открывается, код 200", out)
        self.assertIn("Перенаправления: http://a.example (301) → https://a.example/ (200)", out)
        self.assertIn("Сертификат: действует ещё 10 дн. (скоро истечёт), выдан Let's Encrypt", out)

    def test_a_redirect_into_the_local_network_is_refused(self):
        def public(url):
            if "evil.internal" in url:
                raise uptime.UptimeError("Этот адрес ведёт в локальную сеть")
        reply = SimpleNamespace(status_code=302, headers={"Location": "http://evil.internal/"})
        with mock.patch.object(uptime, "_public", public), mock.patch.object(net, "probe", lambda url, headers=None, timeout=15, who="", public=False: (reply, 0.1, "")):
            with self.assertRaises(uptime.UptimeError):
                uptime.check_website("https://a.example")

    def test_down_and_error_codes_are_reported_plainly(self):
        with mock.patch.object(uptime, "_public"), mock.patch.object(uptime, "_cert", return_value=(None, "не удалось связаться по HTTPS (TimeoutError)")), \
                mock.patch.object(net, "probe", lambda url, headers=None, timeout=15, who="", public=False: (None, 15.0, "не ответил за 15 с")):
            out = uptime.check_website("a.example")
        self.assertIn("a.example: НЕ открывается: не ответил за 15 с", out)
        self.assertIn("Сертификат: не удалось связаться по HTTPS", out)
        bad = SimpleNamespace(status_code=502, headers={})
        with mock.patch.object(uptime, "_public"), mock.patch.object(uptime, "_cert", return_value=(80, "")), mock.patch.object(net, "probe", lambda url, headers=None, timeout=15, who="", public=False: (bad, 0.2, "")):
            self.assertIn("отвечает ошибкой 502", uptime.check_website("a.example"))

    def test_service_status_lists_trouble_and_refuses_unknown_names(self):
        self.serve({"https://status.claude.com": status_page("major", [("API", "partial_outage")], [("Elevated errors", "investigating")]), "https://status.openai.com": status_page(),
                    "https://www.githubstatus.com": status_page(), "https://www.cloudflarestatus.com": status_page(), "https://www.vercel-status.com": status_page()})
        out = uptime.service_status()
        self.assertIn("Anthropic (Claude): серьёзные сбои (API: частичный сбой). Происшествия: Elevated errors (investigating)", out)
        self.assertIn("OpenAI: всё работает", out)
        with self.assertRaises(uptime.UptimeError):
            uptime.service_status("netflix")

    def test_outage_note_names_the_right_provider_and_never_raises(self):
        class ClaudeError(Exception):
            pass
        self.serve({"https://status.claude.com": status_page("minor", [("API", "degraded_performance")])})
        self.assertIn("Страница статуса: Anthropic (Claude): небольшие сбои (API: тормозит)", uptime.outage_note(ClaudeError("x")))
        net._cache.clear()
        self.serve({"https://status.claude.com": status_page()})
        self.assertIn("сбоев нет: дело не в них", uptime.outage_note(ClaudeError("x")))
        self.assertEqual(uptime.outage_note(ValueError("не про модель")), "")
        net._cache.clear()
        self.serve({"https://status.claude.com": ({}, 503)})
        self.assertEqual(uptime.outage_note(ClaudeError("x")), "")   # the status page didn't respond: stay quiet instead of interfering with the answer

    def test_watch_tells_once_about_a_fall_a_return_and_an_old_certificate(self):
        said, path = [], Path(self.enterContext(tempfile.TemporaryDirectory())) / "watch.json"
        down, up = ([], 0, "не ответил за 15 с"), ([("https://a.example", 200)], 0.2, "")
        online = (SimpleNamespace(status_code=200, headers={}), 0.1, "")   # the reference page answers: the Mac itself is online
        with mock.patch.object(config, "SITE_PUBLIC", "https://a.example"), mock.patch.object(uptime, "_cert", return_value=(5.0, "")), mock.patch.object(uptime, "_normalize", lambda u: u), \
                mock.patch.object(net, "probe", return_value=online):
            with mock.patch.object(uptime, "_open", return_value=down):
                uptime.watch(said.append, path)
                self.assertEqual(len(said), 1)   # first check with no response: the certificate was mentioned, the outage not yet
                self.assertIn("истекает через 5 дн.", said[0])
                uptime.watch(said.append, path)
                self.assertIn("не открывается: не ответил за 15 с", said[1])
                uptime.watch(said.append, path)
                self.assertEqual(len(said), 2)   # stay quiet about the same thing again
            with mock.patch.object(uptime, "_open", return_value=up):
                uptime.watch(said.append, path)
            self.assertIn("снова открывается", said[2])
            self.assertEqual(len(said), 3)   # don't remind about the certificate again within the same day
        self.assertEqual(json.loads(path.read_text())["down_since"], None)
        with mock.patch.object(config, "SITE_PUBLIC", ""):
            uptime.watch(said.append, path)
        self.assertEqual(len(said), 3)   # no address set — we don't watch it

    def test_an_offline_mac_does_not_call_the_site_down_and_a_broken_watch_never_raises(self):
        said, path = [], Path(self.enterContext(tempfile.TemporaryDirectory())) / "watch.json"
        offline = (None, 15.0, "адрес не найден (такого сайта нет или нет интернета)")
        with mock.patch.object(config, "SITE_PUBLIC", "https://a.example"), mock.patch.object(uptime, "_cert", return_value=(90.0, "")), \
                mock.patch.object(uptime, "_open", return_value=([], 15.0, offline[2])), mock.patch.object(net, "probe", return_value=offline):
            for _ in range(uptime.DOWN_AFTER + 1):   # a plane, a café: the reference page doesn't answer either
                uptime.watch(said.append, path)
        self.assertEqual(said, [])
        self.assertFalse(path.exists())
        with mock.patch.object(config, "SITE_PUBLIC", "https://a.example"), mock.patch.object(uptime, "_open", side_effect=KeyError("x")), \
                mock.patch.object(uptime.events, "error") as error:
            uptime.watch(said.append, path)   # the news and page watches started after it in the same round still start
        error.assert_called_once()

    def test_the_site_check_dials_only_public_addresses_and_never_reads_the_body(self):
        """A name that resolves outside for the check and inside for the dial (DNS rebinding) gets no request; a hostile page
        serving gigabytes is never read."""
        dialled = []

        def get(url, **kw):
            dialled.append((url, kw.get("stream"), net._pinned.on))
            return SimpleNamespace(status_code=200, headers={}, close=lambda: None)
        with mock.patch.object(net.requests, "get", get), mock.patch.object(uptime, "_public"), mock.patch.object(uptime, "_cert", return_value=(90.0, "")):
            self.assertIn("открывается, код 200", uptime.check_website("https://a.example"))
        self.assertEqual(dialled, [("https://a.example", True, True)])   # streamed, under the public-only dial check
        with mock.patch.object(uptime.socket, "create_connection", side_effect=net.Inside("адрес внутри этого Mac или сети (127.0.0.1)")):
            self.assertEqual(uptime._cert("rebound.example"), (None, "адрес внутри этого Mac или сети — не проверяю"))


class Registered(unittest.TestCase):
    def test_every_new_tool_is_known_to_sena(self):
        from sena.agents.ag_sena import tools
        names = {t["name"] for t in tools.available()}
        self.assertTrue({"book_search", "health_research", "stackoverflow", "huggingface", "country_info", "country_economy", "on_this_day", "recipe", "world_time", "check_website", "service_status"} <= names)


if __name__ == "__main__":
    unittest.main()
