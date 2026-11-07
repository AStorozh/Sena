"""News: parsing feeds (and broken ones), the noise filter, one story across outlets and languages, weight by independent owners,
the stories as Sena gets them (the opening of the article, doubtful ones marked with a longer passage), local news, search in
any language, the background watch (gathering ahead, telling about a watched topic), the full text of an article, the catalog.
The network is replaced with stubs; no model is asked."""
import time
import threading
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest import mock

import numpy as np

from sena import config
from sena.core import store
from sena.world import articles, net, news


def rss(items):
    body = "".join(f"<item><title>{t}</title><link>{link}</link><pubDate>{format_datetime(when)}</pubDate>"
                   f"<description>&lt;p&gt;{d}&lt;/p&gt;</description></item>" for t, when, d, link in items)
    return f"<?xml version='1.0'?><rss version='2.0'><channel>{body}</channel></rss>".encode()


def google(items):
    body = "".join(f"<item><title>{t} - {outlet}</title><link>https://news.google.com/rss/articles/CBM{n}?oc=5</link>"
                   f"<pubDate>{format_datetime(when)}</pubDate><description>{t}</description><source url='{site}'>{outlet}</source></item>"
                   for n, (t, when, outlet, site) in enumerate(items))
    return f"<?xml version='1.0'?><rss version='2.0'><channel>{body}</channel></rss>".encode()


def atom(title, when):
    return (f"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><entry><title>{title}</title>"
            f"<link href='https://a/1'/><updated>{when.isoformat()}</updated><summary>кратко</summary></entry></feed>").encode()


def rdf(title, when):
    return (f"<?xml version='1.0'?><rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#' xmlns='http://purl.org/rss/1.0/' "
            f"xmlns:dc='http://purl.org/dc/elements/1.1/'><item><title>{title}</title><link>https://r/1</link>"
            f"<dc:date>{when.strftime('%Y-%m-%dT%H:%M:%SZ')}</dc:date></item></rdf:RDF>").encode()


NOW = datetime.now(timezone.utc)
SOURCES = [
    {"name": "BBC", "url": "u-bbc", "topic": "world", "lang": "en", "country": "GB", "tier": 1, "group": "bbc"},
    {"name": "DW", "url": "u-dw", "topic": "world", "lang": "en", "country": "DE", "tier": 1, "group": "dw"},
    {"name": "NYT", "url": "u-nyt", "topic": "world", "lang": "en", "country": "US", "tier": 1, "group": "nyt", "text": False},
    {"name": "Broken", "url": "u-broken", "topic": "world", "lang": "en", "country": "US", "tier": 2, "group": "broken"},
    {"name": "ТАСС", "url": "u-tass", "topic": "russia", "lang": "ru", "country": "RU", "tier": 2, "group": "tass", "kind": "state"},
    {"name": "Snopes", "url": "u-snopes", "topic": "factcheck", "lang": "en", "country": "US", "tier": 2, "group": "snopes", "kind": "factcheck"},
    {"name": "VnExpress", "url": "u-vne", "topic": "local", "lang": "en", "country": "VN", "tier": 2, "group": "vnexpress"},
    {"name": "Straits Times", "url": "u-st", "topic": "world", "lang": "en", "country": "SG", "tier": 2, "group": "sph"},
]
TEXT = "Officials said the strikes on refineries will resume. " * 40   # a long open article: its opening is cut for Sena


def item(title, source="BBC", group=None, tier=1, kind="news", hours=1, position=0, summary="", link=None, text=True):
    return {"title": title, "link": link or f"https://{group or source.lower()}.example/{abs(hash(title)) % 10**6}", "time": NOW - timedelta(hours=hours),
            "summary": summary, "source": source, "group": group or source.lower(), "tier": tier, "kind": kind, "position": position,
            "text": text, "topic": "world"}


class Base(unittest.TestCase):
    """Fake feeds behind net.requests.get, articles read from a fixed text, a clean watch."""
    def setUp(self):
        net._cache.clear()
        articles._refused.clear()
        articles._resolved.clear()
        news._snapshot.clear()
        news._texts.clear()
        store.save_json(config.NEWS_WATCH_FILE, {})
        self.data = {
            "u-bbc": rss([("Ukraine resumes strikes on Russian refineries Zelensky says", NOW - timedelta(hours=2), "a", "https://bbc.example/1"),
                          ("Local election results announced", NOW - timedelta(hours=1), "b", "https://bbc.example/2"),
                          ("Opinion: why the election matters", NOW - timedelta(hours=1), "c", "https://bbc.example/opinion/3"),
                          ("Very old story about nothing", NOW - timedelta(hours=60), "d", "https://bbc.example/4")]),
            "u-dw": rss([("Ukraine resumes strikes on Russian refineries, Zelenskyy says", NOW - timedelta(hours=3), "e", "https://dw.example/1")]),
            "u-nyt": rss([("Zelensky says Ukraine will resume Russian refinery strikes", NOW - timedelta(hours=1), "f", "https://nyt.example/1")]),
            "u-tass": rss([("Россия заявила о новых переговорах", NOW - timedelta(hours=1), "g", "https://tass.example/1")]),
            "u-snopes": rss([("Fact check: viral claim about refineries is false", NOW - timedelta(hours=50), "h", "https://snopes.example/1")]),
            "u-vne": rss([("Da Nang opens new bridge over Han river", NOW - timedelta(hours=2), "i", "https://vne.example/1")]),
            "u-st": rss([("Singapore budget passes", NOW - timedelta(hours=2), "j", "https://st.example/1")]),
        }

        def get(url, params=None, headers=None, timeout=None, **kw):
            if url.startswith("https://news.google.com/rss/"):   # Google finds the festival for the place, nothing for other searches
                found = "/geo/" in url or "Da Nang" in (params or {}).get("q", "")
                return mock.Mock(content=google([("Da Nang festival draws crowds", NOW - timedelta(hours=3), "Báo Đà Nẵng", "https://baodanang.vn")] if found else []),
                                 status_code=200)
            if url not in self.data:
                raise net.requests.ConnectionError("нет связи")
            return mock.Mock(content=self.data[url], status_code=200)

        self.read = mock.Mock(side_effect=lambda link, limit=None: {"url": link, "title": "t", "site": "s", "date": "", "text": TEXT})
        self.patches = [mock.patch.object(news, "SOURCES", SOURCES), mock.patch.object(net.requests, "get", get),
                        mock.patch.object(news.articles, "read", self.read)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def numbered(out):
        return [line for line in out.split("\n") if line[:1].isdigit() and line[1:2] == "."]

    @staticmethod
    def block(out, word):
        """The lines of the numbered story whose headline has the word."""
        lines, found = out.split("\n"), []
        for n, line in enumerate(lines):
            if line[:1].isdigit() and line[1:2] == "." and word in line.lower():
                found = [line]
                for rest in lines[n + 1:]:
                    if (rest[:1].isdigit() and rest[1:2] == ".") or rest.startswith(("Не ответили", "<<<")):
                        break
                    found.append(rest)
                break
        return "\n".join(found)


# ---------- feeds ----------
class Parsing(unittest.TestCase):
    def test_three_formats(self):
        a = news.parse(rss([("Заголовок &amp; ещё", NOW, "анонс &lt;b&gt;текст&lt;/b&gt;", "https://x/0")]))[0]
        self.assertEqual(a["title"], "Заголовок & ещё")
        self.assertEqual(a["summary"], "анонс текст")
        self.assertEqual(a["link"], "https://x/0")
        self.assertAlmostEqual(a["time"].timestamp(), NOW.timestamp(), delta=2)
        b = news.parse(atom("Atom title", NOW))[0]
        self.assertEqual((b["title"], b["link"]), ("Atom title", "https://a/1"))
        c = news.parse(rdf("RDF title", NOW))[0]
        self.assertEqual((c["title"], c["link"]), ("RDF title", "https://r/1"))
        self.assertIsNotNone(c["time"])

    def test_a_feed_with_a_stray_character_is_still_read(self):
        broken = b"<?xml version='1.0'?><rss><channel><item><title>Tom & Jerry return</title><link>https://x/1</link></item></channel></rss>"
        self.assertEqual(news.parse(broken)[0]["link"], "https://x/1")

    def test_a_feed_writing_local_time_as_gmt_moves_back_whole(self):
        # a feed in UTC+7 that calls its local time GMT: the fresh item seems 6.9 h ahead, the older one 1 h ago
        fresh, older = news.parse(rss([("Свежая", NOW + timedelta(hours=6.9), "x", "https://x/1"), ("Старая", NOW - timedelta(hours=1), "y", "https://x/2")]))
        self.assertAlmostEqual((NOW - fresh["time"]).total_seconds() / 3600, 0.1, delta=0.05)
        self.assertAlmostEqual((NOW - older["time"]).total_seconds() / 3600, 8, delta=0.05)
        far = news.parse(rss([("Анонс на послезавтра", NOW + timedelta(days=2), "z", "https://x/3")]))[0]
        self.assertLessEqual(far["time"], datetime.now(timezone.utc))   # an item far ahead is not a zone: it just becomes "now"

    def test_google_names_the_outlet(self):
        got = news.parse(google([("Festival", NOW, "Báo Đà Nẵng", "https://baodanang.vn")]))[0]
        self.assertEqual((got["outlet"], got["outlet_url"]), ("Báo Đà Nẵng", "https://baodanang.vn"))


class Collecting(Base):
    def test_columns_old_news_and_a_broken_feed_are_left_out_and_named(self):
        items, ok, failed = news.collect("world", 24)
        titles = [i["title"] for i in items]
        self.assertIn("Local election results announced", titles)
        self.assertNotIn("Opinion: why the election matters", titles)   # a column, not news
        self.assertNotIn("Very old story about nothing", titles)   # older than the window
        self.assertEqual(news._names(failed), ["Broken"])
        self.assertIn("Fact check: viral claim about refineries is false", titles)   # fact checks are looked at over a week
        self.assertEqual(len(ok), 5)   # BBC, DW, NYT, Straits Times and the fact checker

    def test_local_news_are_the_countrys_outlets_and_google_for_the_place(self):
        items, _, _ = news.collect("local", 24, country="VN", place="Da Nang")
        self.assertEqual({i["source"] for i in items}, {"VnExpress", "Báo Đà Nẵng"})
        found = next(i for i in items if i["source"] == "Báo Đà Nẵng")
        self.assertEqual(found["title"], "Da Nang festival draws crowds")   # Google's " - outlet" tail is cut
        self.assertEqual((found["tier"], found["group"]), (3, "baodanang.vn"))   # an outlet off our list: tier 3, its own owner

    def test_cache_prevents_second_download(self):
        calls = []
        original = net.requests.get

        def counting(url, **kw):
            calls.append(url)
            return original(url, **kw)
        with mock.patch.object(net.requests, "get", counting):
            news.collect("world", 24)
            first = len(calls)
            news.collect("world", 24)
        self.assertEqual(len(calls), first + 1)   # only the feed that failed before is downloaded again


# ---------- one story, its weight ----------
class Clustering(unittest.TestCase):
    def test_word_forms_group_the_same_story_without_fingerprints(self):
        a = item("Zelensky Says Ukraine Will Resume Russian Refinery Strikes", "NYT")
        b = item("Ukraine resumes attacks on Russian refineries as Zelenskyy says 'we're done'", "DW")
        c = item("Stocks fall as bond yields climb on inflation worry", "CNBC")
        groups = news.cluster([a, b, c])
        self.assertEqual(len(groups), 2)
        self.assertEqual(sorted(i["source"] for i in max(groups, key=lambda g: len(g["items"]))["items"]), ["DW", "NYT"])

    def test_different_stories_with_one_common_word_stay_apart(self):
        self.assertEqual(len(news.cluster([item("Trump visits Vietnam for trade talks", "BBC"), item("Trump signs new tariff bill on steel", "NYT")])), 2)

    def test_fingerprints_merge_one_story_across_languages(self):
        ru, en, other = item("Пожар в бывшей резиденции Гитлера", "Медуза"), item("Fire at Hitler's Eagle's Nest retreat", "BBC"), item("Kenya Ebola death", "DW")
        vectors = np.array([[1.0, 0.0], [0.9, np.sqrt(1 - 0.81)], [0.0, 1.0]], dtype=np.float32)   # the first two: closeness 0.9
        groups = news.cluster([ru, en, other], vectors)
        self.assertEqual(sorted(len(g["items"]) for g in groups), [1, 2])


class Weight(unittest.TestCase):
    def test_outlets_of_one_owner_are_one_voice(self):
        one_owner = {"items": [item("A film wins", "Variety", "pmc", 2), item("A film wins", "Deadline", "pmc", 2), item("A film wins", "IndieWire", "pmc", 2)]}
        two_owners = {"items": [item("A film wins", "Guardian", "guardian", 1), item("A film wins", "NPR", "npr", 1)]}
        ranked = news.rank([one_owner, two_owners])
        self.assertIs(ranked[0], two_owners)
        self.assertIn("пока одно издание: Variety", news._status(one_owner))
        self.assertIn("2 независимых издания: Guardian, NPR", news._status(two_owners))

    def test_state_media_weigh_little_and_a_lone_fact_check_is_not_a_story(self):
        state = {"items": [item("Официальное заявление", "ТАСС", "tass", 2, "state")]}
        free = {"items": [item("Repoter sees protest", "Rappler", "rappler", 3)]}
        check = {"items": [item("Fact check: false", "Snopes", "snopes", 2, "factcheck")]}
        ranked = news.rank([state, free, check])
        self.assertEqual(ranked, [free, state])
        self.assertEqual(news._status(state), "только государственные СМИ")

    def test_a_high_place_in_its_feed_lifts_an_older_story(self):
        older_first = {"items": [item("Alpha topic", "BBC", hours=5, position=0)]}
        fresh_low = {"items": [item("Bravo topic", "BBC", hours=0.1, position=9)]}
        self.assertIs(news.rank([fresh_low, older_first])[0], older_first)

    def test_one_chatty_owner_does_not_take_the_whole_list(self):
        many = [{"items": [item(f"Story {n}", "BBC", position=n)]} for n in range(6)]
        others = [{"items": [item(f"Other {n}", "DW", "dw", position=n)]} for n in range(2)]
        picked = news._diverse(news.rank(many + others), 4)
        self.assertLessEqual(sum(news._lead(g)["group"] == "bbc" for g in picked), 2)


# ---------- the stories as Sena gets them ----------
class Digest(Base):
    def test_the_strongest_story_comes_first_with_who_carries_it_and_its_opening(self):
        out = news.news_digest("world", hours=24)
        self.assertIn("refiner", self.numbered(out)[0].lower())
        story = self.block(out, "refiner")
        self.assertIn("3 независимых издания: NYT, BBC, DW", story)
        self.assertIn("Officials said the strikes", story)   # the opening of the article that was read
        self.assertNotIn("Сомнительно", story)
        self.assertLess(len(story), news.LEAD_CHARS + 300)   # an opening, not the whole article
        self.assertIn("https://bbc.example/1", story)   # the article read: the most reliable outlet whose text is open
        self.assertNotIn("https://nyt.example/1", [c.args[0] for c in self.read.call_args_list])   # a closed site isn't even tried
        self.assertIn("ВНЕШНИЕ ДАННЫЕ", out)
        self.assertIn("Не ответили: Broken", out)

    def test_three_independent_owners_outweigh_a_phrase_of_anonymous_sourcing(self):
        rumor = {"url": "u", "title": "t", "site": "s", "date": "", "text": "Officials confirmed the strikes, sources said. " * 10}
        with mock.patch.object(news.articles, "read", lambda link, limit=None: dict(rumor, url=link)):
            story = self.block(news.news_digest("world"), "refiner")
        self.assertNotIn("Сомнительно", story)

    def test_a_doubtful_story_is_marked_and_comes_with_a_longer_passage(self):
        def read(link, limit=None):
            text = ("The election commission reportedly delayed the count, sources say. " * 30) if "bbc.example/2" in link else TEXT
            return {"url": link, "title": "t", "site": "s", "date": "", "text": text}
        with mock.patch.object(news.articles, "read", read):
            story = self.block(news.news_digest("world"), "election")
        self.assertIn("Сомнительно: пока одно издание; в тексте: «reportedly», «sources say»", story)
        self.assertGreater(len(story), news.LEAD_CHARS + 300)   # enough to check it without opening the page
        self.assertLess(len(story), news.DOUBT_CHARS + 400)

    def test_an_unreadable_story_says_it_has_only_the_summary(self):
        with mock.patch.object(news.articles, "read", side_effect=articles.Unreadable("закрыто")):
            story = self.block(news.news_digest("world"), "election")
        self.assertIn("статью прочитать не удалось, есть только анонс", story)
        self.assertIn("https://bbc.example/2", story)

    def test_a_few_more_than_asked_come_for_sena_to_choose_and_no_model_is_asked(self):
        with mock.patch.object(news.local, "chat", side_effect=AssertionError("модель не нужна")):
            out = news.news_digest("world", limit=1)
        self.assertGreaterEqual(len(self.numbered(out)), 2)
        self.assertLessEqual(len(self.numbered(out)), 5)

    def test_local_news_need_a_country_and_remember_the_place(self):
        with self.assertRaises(net.WorldError):
            news.news_digest("local", place="Da Nang")
        out = news.news_digest("local", place="Da Nang", country="vn")
        self.assertIn("Da Nang", out)
        self.assertIn("местные", out)
        state = news._state()
        self.assertEqual((state["place"], state["country"]), ("Da Nang", "VN"))   # the watch will gather this place ahead
        self.assertGreater(state["used"], time.time() - 5)

    def test_bad_topic(self):
        with self.assertRaises(net.WorldError):
            news.news_digest("gossip")


class Search(Base):
    def test_all_words_must_meet(self):
        self.assertIn("election", news.news_search("election results").lower())
        self.assertIn("ничего не нашла", news.news_search("election refineries"))

    def test_a_russian_query_finds_english_news_by_meaning(self):
        def vectors(texts):
            return np.array([[1.0, 0.0] if "refiner" in t.lower() else [0.0, 1.0] for t in texts], dtype=np.float32)
        with mock.patch.object(news, "_vectors", vectors), mock.patch.object(news.local, "embed", lambda texts, **kw: [np.array([1.0, 0.0], dtype=np.float32)]):
            out = news.news_search("удары по нефтезаводам")
        self.assertIn("refiner", out.lower())
        self.assertNotIn("election", out.lower())

    def test_rumor_words_are_the_words_of_anonymous_sourcing(self):
        found = lambda text: [m.group(0).lower() for m in news.RUMOR.finditer(text)]
        self.assertEqual(found("The minister reportedly resigned, sources say"), ["reportedly", "sources say"])
        self.assertEqual(found("По данным источников, министр якобы ушёл; слухи ходят давно"), ["по данным источников", "якобы", "слухи"])
        self.assertEqual(found("The president is expected to visit and plans to sign the deal"), [])   # ordinary plans are news, not rumor

    def test_an_opening_ends_at_a_sentence(self):
        self.assertEqual(news._opening("Первое предложение. Второе предложение длиннее. Третье.", 50), "Первое предложение. Второе предложение длиннее.")
        self.assertEqual(news._opening("Одно очень длинное предложение без точки до самого конца строки", 33), "Одно очень длинное предложение…")


# ---------- the background watch ----------
class Watch(Base):
    def test_the_watch_sleeps_while_news_are_not_in_use(self):
        with mock.patch.object(net.requests, "get", side_effect=AssertionError("ленты не нужны")):
            news.watch(mock.Mock())
        self.assertEqual(news._snapshot, {})

    def test_the_watch_gathers_ahead_and_a_digest_then_needs_no_feeds(self):
        news._remember(used=time.time())
        news.watch(mock.Mock())
        self.assertIn("all", news._snapshot)
        self.assertTrue(news._texts)   # the strongest stories were read ahead
        with mock.patch.object(net.requests, "get", side_effect=AssertionError("ленты не нужны")):
            out = news.news_digest("world")
        self.assertIn("refiner", self.numbered(out)[0].lower())
        self.assertIn("из 5 лент", out)   # the feeds that answered the watch

    def test_the_watch_runs_once_in_half_an_hour(self):
        news._remember(used=time.time())
        news.watch(mock.Mock())
        with mock.patch.object(news, "collect", side_effect=AssertionError("рано")):
            news.watch(mock.Mock())

    def _watch(self, topic, hours_ago=5):
        news.news_watch(topic)
        state = news._state()
        state["watches"][-1]["added"] = time.time() - hours_ago * 3600   # set before the stories began
        store.save_json(config.NEWS_WATCH_FILE, state)

    def test_a_strong_new_story_on_a_watched_topic_is_told_once(self):
        self._watch("refinery strikes")
        alert = mock.Mock()
        with mock.patch.object(news.settings, "awake", lambda now=None: True), mock.patch.object(news, "ALERT_OWNERS", 3):
            news.watch(alert)
            news._snapshot.clear()   # the next half hour
            news.watch(alert)
        self.assertEqual(alert.call_count, 1)
        text = alert.call_args.args[0]
        self.assertIn("«refinery strikes»", text)
        self.assertIn("refiner", text.lower())
        self.assertIn("ответь OK", text)
        self.assertIn("ВНЕШНИЕ ДАННЫЕ", text)   # the headline and passage are someone else's text

    def test_a_weak_or_old_story_is_not_told_and_nights_are_quiet(self):
        self._watch("election results")   # a single outlet carries it
        self._watch("Zelensky refineries", hours_ago=0)   # set after the story began: not new for this watch
        alert = mock.Mock()
        with mock.patch.object(news.settings, "awake", lambda now=None: True):
            news.watch(alert)
        alert.assert_not_called()
        news._snapshot.clear()
        self._watch("refinery strikes")
        with mock.patch.object(news.settings, "awake", lambda now=None: False):
            news.watch(alert)
        alert.assert_not_called()

    def test_a_day_has_a_cap(self):
        self._watch("refinery strikes")
        state = news._state()
        state.update(day=datetime.now().strftime("%Y-%m-%d"), count=news.ALERT_DAY_CAP)
        store.save_json(config.NEWS_WATCH_FILE, state)
        alert = mock.Mock()
        with mock.patch.object(news.settings, "awake", lambda now=None: True):
            news.watch(alert)
        alert.assert_not_called()

    def test_the_articles_of_a_story_to_tell_are_read_outside_the_lock(self):
        """A digest or a new watch waited behind the watch while it read someone's slow site under the shared lock."""
        self._watch("refinery strikes")
        held, real = [], news.prepare

        def prepare(g):
            held.append(news._state_lock.locked())
            return real(g)
        alert = mock.Mock()
        with mock.patch.object(news.settings, "awake", lambda now=None: True), mock.patch.object(news, "prepare", prepare):
            news.watch(alert)
        alert.assert_called_once()
        self.assertTrue(held)
        self.assertFalse(any(held))

    def test_a_gathering_made_offline_is_not_served_and_gathered_again(self):
        """The watch gathered while the network was down: for 35 minutes every digest got "nothing found" from the empty gathering."""
        news._remember(used=time.time())
        news._snapshot["all"] = {"t": time.time() - 60, "items": [], "ok": set(), "failed": [s["url"] for s in SOURCES]}
        self.assertIn("refiner", self.numbered(news.news_digest("world"))[0].lower())   # the feeds themselves were asked
        news.watch(mock.Mock())   # and the watch gathers again at its next round, not in half an hour
        self.assertTrue(news._snapshot["all"]["ok"])

    def test_a_dead_network_is_a_failure_not_nothing_found_and_silent_feeds_are_counted(self):
        net._cache.clear()
        with mock.patch.object(net.requests, "get", side_effect=net.requests.ConnectionError("нет связи")):
            out = news.news_digest("world")
        self.assertIsInstance(out, news.Failure)
        self.assertIn("похоже, нет связи", out)
        out = news._render([], {"outlets": 3, "items": 0, "failed": [f"Лента {n}" for n in range(30)]}, "Новости")
        self.assertIn("и ещё 22 ленты", out)
        self.assertLess(len(out), 200)

    def test_watch_tools(self):
        self.assertIn("Слежу", news.news_watch("выборы в США"))
        self.assertIn("уже слежу", news.news_watch("Выборы в США"))
        listed = news.news_watches()
        self.assertIn("выборы в США", listed)
        wid = listed.split("]")[0].strip("[")
        self.assertEqual(news.news_unwatch(wid), "Больше не слежу")
        self.assertEqual(news.news_watches(), "Ни за чем не слежу")
        self.assertEqual(news.news_unwatch("nope"), "Нет такой слежки")


# ---------- the article itself ----------
PAGE = ("<html><head><title>Bridge opens</title><meta property='og:site_name' content='VnExpress'></head><body><nav>Menu Home Sport</nav>"
        "<article><h1>Da Nang opens a new bridge</h1>" + "".join(f"<p>Paragraph {n}: the city opened the new bridge over the Han river today, officials said, "
                                                                 f"and traffic is expected to ease across the centre.</p>" for n in range(6))
        + "</article><footer>Advertising and cookies</footer></body></html>")


_resolve = net.socket.getaddrinfo


def public_resolver(host, *args, **kw):
    """Stands in for DNS: every name is a public address, so the public-only check looks no name up for real (security S4: the
    names of these tests left the Mac). An IP literal and localhost go to the real resolver: nothing leaves the Mac for them."""
    import ipaddress
    try:
        ipaddress.ip_address(host)
        return _resolve(host, *args, **kw)
    except (TypeError, ValueError):
        if not isinstance(host, str) or host == "localhost":
            return _resolve(host, *args, **kw)
    return [(net.socket.AF_INET, net.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


class Article(unittest.TestCase):
    def setUp(self):
        net._cache.clear()
        articles._refused.clear()
        articles._resolved.clear()
        self.enterContext(mock.patch.object(net, "_guard", lambda reply, who, error: reply))   # stand-in answers have no socket to check
        self.enterContext(mock.patch.object(net.socket, "getaddrinfo", public_resolver))

    def test_a_page_is_read_in_its_own_charset_and_files_behind_links_as_files(self):
        """Firecrawl's check on this Mac: a Russian page sent without a charset came out as mojibake; plain text and JSON gave
        nothing, a Word file behind a link was "closed by a subscription"; tables vanished from research reading."""
        body = ("<html><head><meta charset='windows-1251'><title>Курс рубля</title></head><body><article><p>"
                + "Центральный банк установил официальный курс рубля на завтра. " * 12
                + "</p><table><tr><th>Валюта</th><th>Курс</th></tr><tr><td>USD</td><td>81,25</td></tr></table></article></body></html>")
        reply = lambda content, kind: mock.Mock(content=content, text=content.decode("latin-1"), status_code=200, headers={"content-type": kind})   # noqa: E731
        with mock.patch.object(net.requests, "get", return_value=reply(body.encode("cp1251"), "text/html")):
            a = articles.read("https://cbr.example/rate", whole=True)
        self.assertIn("Центральный банк", a["text"])
        self.assertEqual(a["title"], "Курс рубля")
        self.assertIn("81,25", a["text"])   # the table kept for research
        with mock.patch.object(net.requests, "get", return_value=reply('{"rate": 81.25, "name": "доллар"}'.encode(), "application/json")):
            self.assertIn('"rate": 81.25', articles.read("https://api.example/rate.json")["text"])
        with mock.patch.object(net.requests, "get", return_value=reply("Дата;Сумма\n01.10.2026;1200\n".encode("cp1251"), "text/csv")):
            self.assertIn("01.10.2026;1200", articles.read("https://bank.example/statement.csv")["text"])
        from tests.test_attachments import word_file
        with mock.patch.object(net.requests, "get", return_value=reply(word_file("Договор поставки, пункт 4.1: оплата в течение 5 дней"), "application/octet-stream")):
            self.assertIn("оплата в течение 5 дней", articles.read("https://docs.example/dogovor.docx")["text"])

    def test_a_public_google_doc_link_reads_as_its_export(self):
        self.assertEqual(articles._export("https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit?usp=sharing"),
                         "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/export?format=txt")
        self.assertEqual(articles._export("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit#gid=777"),
                         "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/export?format=csv&gid=777")
        for tail in ("edit?usp=sharing#gid=777", "edit?gid=777&usp=sharing"):   # the sheet linked to, wherever its gid stands
            self.assertTrue(articles._export(f"https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/{tail}").endswith("&gid=777"), tail)
        self.assertEqual(articles._export("https://example.com/a"), "https://example.com/a")

    def test_the_main_text_without_menus(self):
        with mock.patch.object(net.requests, "get", return_value=mock.Mock(text=PAGE, status_code=200)):
            a = articles.read("https://e.vnexpress.net/news/bridge")
        self.assertIn("the city opened the new bridge", a["text"])
        self.assertNotIn("Menu Home Sport", a["text"])
        self.assertEqual(a["site"], "VnExpress")

    def test_a_refusing_site_is_skipped_for_hours_but_a_short_page_is_not(self):
        with mock.patch.object(net.requests, "get", return_value=mock.Mock(text="<p>Subscribe</p>", status_code=200)):
            with self.assertRaises(articles.Unreadable):
                articles.read("https://short.example/a")
        self.assertTrue(articles.readable("https://short.example/b"))
        with mock.patch.object(net.requests, "get", return_value=mock.Mock(text="", status_code=403)):
            with self.assertRaises(articles.Unreadable):
                articles.read("https://closed.example/a")
        self.assertFalse(articles.readable("https://closed.example/other"))

    def test_only_the_sites_own_refusal_closes_it_and_a_pdf_link_is_read_as_a_pdf(self):
        """crawl4ai's study: one missing page or a dead network closed a whole site for six hours; a PDF was taken for a page."""
        for code, closed in ((404, False), (500, False), (503, True)):
            with mock.patch.object(net.requests, "get", return_value=mock.Mock(text="", status_code=code)):
                with self.assertRaises(articles.Unreadable):
                    articles.read(f"https://s{code}.example/a")
            self.assertEqual(not articles.readable(f"https://s{code}.example/b"), closed, code)
        self.assertLessEqual(articles._refused["s503.example"] - time.time(), articles.BUSY_MINUTES * 60 + 1)
        pdf = mock.Mock(content=b"%PDF-1.7 ...", text="", status_code=200)
        with mock.patch.object(net.requests, "get", return_value=pdf), \
                mock.patch.object(articles.local, "pdf_text", return_value="Отчёт о сне детей. " * 40) as read:
            a = articles.read("https://who.example/reports/sleep%20report.pdf")
        read.assert_called_once()
        self.assertEqual((a["title"], a["site"]), ("sleep report", "who.example"))
        self.assertIn("Отчёт о сне детей", a["text"])

    def test_a_google_news_link_is_followed_to_the_article(self):
        page = mock.Mock(text='<c-wiz data-n-a-sg="SIG" data-n-a-ts="1700000000"></c-wiz>', status_code=200)
        answer = mock.Mock(status_code=200, text=")]}'\n\n" + '[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://baodanang.vn/a.html\\",1]",null,null,null,"generic"]]')
        with mock.patch.object(net.requests, "get", return_value=page), mock.patch.object(net.requests, "request", return_value=answer) as post:
            self.assertEqual(articles.resolve("https://news.google.com/rss/articles/CBMabc?oc=5"), "https://baodanang.vn/a.html")
        self.assertIn("SIG", post.call_args.kwargs["data"])

    def test_a_blip_in_google_news_resolving_closes_nothing(self):
        """One timeout of the address request closed Google News for half an hour: local news were unreadable after a blip."""
        page = mock.Mock(text='<c-wiz data-n-a-sg="SIG" data-n-a-ts="1700000000"></c-wiz>', status_code=200)
        self.enterContext(mock.patch.object(net, "private_host", return_value=None))   # no name is looked up for real
        with mock.patch.object(net.requests, "get", return_value=page), \
                mock.patch.object(net.requests, "request", side_effect=net.requests.Timeout("read timed out")):
            with self.assertRaises(articles.Unreadable):
                articles.resolve("https://news.google.com/rss/articles/CBMt?oc=5")
        self.assertTrue(articles.readable("https://news.google.com/rss/articles/CBMu"))
        with mock.patch.object(net.requests, "get", return_value=page), \
                mock.patch.object(net.requests, "request", return_value=mock.Mock(status_code=429, text="")):
            with self.assertRaises(articles.Unreadable):
                articles.resolve("https://news.google.com/rss/articles/CBMv?oc=5")
        self.assertFalse(articles.readable("https://news.google.com/rss/articles/CBMw"))   # its own "wait" still closes it

    def test_google_asking_to_wait_is_left_alone_for_half_an_hour(self):
        with mock.patch.object(net.requests, "get", return_value=mock.Mock(text="", status_code=429)) as get:
            for gid in ("CBMx", "CBMy"):
                with self.assertRaises(articles.Unreadable):
                    articles.resolve(f"https://news.google.com/rss/articles/{gid}?oc=5")
        self.assertEqual(get.call_count, 1)   # the second link isn't even asked
        self.assertFalse(articles.readable("https://news.google.com/rss/articles/CBMz"))
        self.assertLessEqual(articles._refused["news.google.com"] - time.time(), articles.BUSY_MINUTES * 60 + 1)

    def test_a_pages_buttons_before_the_article_are_skipped(self):
        page = ("<html><body><div>By — A. Writer Share Copy URL Email Facebook Twitter LinkedIn</div><article>"
                + "".join(f"<p>PARIS — Paragraph {n}: more than 400,000 people protested across France on Saturday, officials said.</p>" for n in range(6))
                + "</article></body></html>")
        import trafilatura   # articles loads it on the first read
        extract = trafilatura.extract

        def fake(html, **kw):   # the first pass returns the buttons glued to the text, as trafilatura did on one real page
            return extract(html, **kw) if kw.get("favor_recall") else "By — A. Writer Share Copy URL Email Facebook Twitter " + (extract(html, **kw) or "")
        with mock.patch.object(net.requests, "get", return_value=mock.Mock(text=page, status_code=200)), mock.patch.object(trafilatura, "extract", fake):
            a = articles.read("https://pbs.example/protests")
        self.assertTrue(a["text"].startswith("PARIS"))

    def test_news_read_gives_the_text_or_says_why_not(self):
        with mock.patch.object(net.requests, "get", return_value=mock.Mock(text=PAGE, status_code=200)):
            out = news.news_read("https://e.vnexpress.net/news/bridge")
        self.assertIn("ВНЕШНИЕ ДАННЫЕ", out)
        self.assertIn("the city opened the new bridge", out)
        with mock.patch.object(net.requests, "get", return_value=mock.Mock(text="", status_code=402)):
            self.assertIn("не удалось", news.news_read("https://paywall.example/x"))


# ---------- the catalog ----------
class Catalog(unittest.TestCase):
    def test_every_source_is_complete_and_unique(self):
        kinds, topics = {"news", "state", "primary", "factcheck"}, set(news.TOPICS_RU) - {"all"} | {"factcheck"}
        for s in news.SOURCES:
            self.assertTrue(s["name"] and s["group"], s)
            self.assertTrue(s["url"].startswith(("https://", "http://", "hn:")), s)
            self.assertIn(s["topic"], topics, s)
            self.assertIn(s["tier"], (1, 2, 3), s)
            self.assertIn(s.get("kind", "news"), kinds, s)
            self.assertEqual(len(s["country"]), 2, s)
            self.assertEqual(s["topic"] == "factcheck", s.get("kind") == "factcheck", s)
        urls = [s["url"] for s in news.SOURCES]
        self.assertEqual(len(urls), len(set(urls)))

    def test_every_topic_has_many_independent_owners(self):
        for topic in set(news.TOPICS_RU) - {"all", "local"}:
            owners = {s["group"] for s in news.sources(topic)}
            self.assertGreaterEqual(len(owners), 8, topic)
        self.assertGreaterEqual(len(news.SOURCES), 150)


class HackerNews(unittest.TestCase):
    def test_top_stories_through_firebase_api(self):
        net._cache.clear()
        now = int(NOW.timestamp())
        stories = {1: {"id": 1, "title": "Show HN: a tiny database", "url": "https://example.com/db", "time": now - 600, "score": 321, "descendants": 45},
                   2: {"id": 2, "title": "Ask HN: what are you building?", "time": now - 900, "score": 90, "descendants": 120},
                   3: {"id": 3, "title": "Removed story", "time": now - 100, "dead": True}}

        def get(url, params=None, headers=None, timeout=None, **kw):
            if url.endswith("topstories.json"):
                return mock.Mock(json=lambda: [1, 2, 3], status_code=200)
            return mock.Mock(json=lambda: stories[int(url.rsplit("/", 1)[-1].replace(".json", ""))], status_code=200)
        with mock.patch.object(net.requests, "get", get):
            items = news._fetch({"name": "Hacker News", "url": "hn:top", "tier": 2, "group": "hn"})
        self.assertEqual([i["title"] for i in items], ["Show HN: a tiny database", "Ask HN: what are you building?"])
        self.assertEqual(items[0]["link"], "https://example.com/db")
        self.assertIn("news.ycombinator.com/item?id=2", items[1]["link"])   # discussion-only posts have no link of their own
        self.assertEqual(items[0]["summary"], "321 очков, 45 комментариев")


class PublicOnly(unittest.TestCase):
    def test_a_link_from_someones_text_never_reaches_this_macs_own_services(self):
        from types import SimpleNamespace
        self.assertTrue(net.private_host("http://127.0.0.1:8766/v1/models"))
        self.assertTrue(net.private_host("http://192.168.1.5/admin"))
        self.assertTrue(net.private_host("http://localhost:8765/desk/api"))
        hops = iter([SimpleNamespace(is_redirect=True, headers={"location": "http://127.0.0.1:8000/agent/v1/profile"}, status_code=302, close=lambda: None)])
        with mock.patch.object(net, "private_host", lambda url: "внутри" if "127.0.0.1" in url else None), \
             mock.patch.object(net.requests, "get", lambda *a, **kw: next(hops)):
            with self.assertRaises(net.WorldError) as caught:   # a public page that redirects inside is stopped at the hop
                net.get("https://example.com/news", public=True)
        self.assertIn("Не открываю", str(caught.exception))

    def test_a_name_that_turns_inside_after_the_check_gets_no_request_through(self):
        """Flowise's check on this Mac: a name that answered "outside" to the check and "inside" to the dial got the GET through
        before its answer was refused, and the unsubscribe POST from someone's mail wasn't checked at the dial at all."""
        import http.server
        import socket
        hits = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", "6")
                self.end_headers()
                self.wfile.write(b"secret")
            do_POST = do_GET
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()   # a quick shutdown
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port, real, asked = server.server_port, socket.getaddrinfo, {"n": 0}

        def flipping(host, *args, **kw):   # the first answer (the check) is outside, every later one (the dial) inside
            if host != "rebind.example":
                return real(host, *args, **kw)
            asked["n"] += 1
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34" if asked["n"] == 1 else "127.0.0.1", port))]
        with mock.patch.object(socket, "getaddrinfo", flipping):
            with self.assertRaises(net.WorldError) as caught:
                net.get(f"http://rebind.example:{port}/admin", public=True)
            asked["n"] = 0
            with self.assertRaises(net.WorldError):
                net.send("POST", f"http://rebind.example:{port}/unsubscribe", form="x=1", public=True)
        self.assertEqual(hits, [])   # nothing reached the service inside
        self.assertIn("не открываю", str(caught.exception))
        self.assertEqual(net.get(f"http://127.0.0.1:{port}/own").status_code, 200)   # Sena's own calls to her own services still go

    def test_not_global_addresses_and_ipv4_inside_ipv6_count_as_inside(self):
        """crawl4ai's rule: "not global", with the IPv4 inside IPv6 transition forms — Tailscale and cloud metadata included."""
        for address in ("100.64.0.1", "100.100.100.100", "169.254.169.254", "::ffff:127.0.0.1", "64:ff9b::7f00:1", "2002:7f00:1::",
                        "192.0.2.1", "::1", "fe80::1", "не адрес"):
            self.assertTrue(net.inner(address), address)
        for address in ("8.8.8.8", "2606:4700::1111"):
            self.assertFalse(net.inner(address), address)
        self.assertTrue(net.private_host("http://metadata.google.internal/computeMetadata/v1/"))

    def test_the_answer_is_checked_by_the_address_it_came_from_and_by_its_size(self):
        from types import SimpleNamespace

        def reply(peer, body=b"ok"):
            sock = SimpleNamespace(getpeername=lambda: (peer, 443)) if peer else None
            raw = SimpleNamespace(_fp=SimpleNamespace(fp=SimpleNamespace(raw=SimpleNamespace(_sock=sock))), read=lambda n, decode_content=True: body[:n])
            return SimpleNamespace(url="https://x.example/", raw=raw, close=lambda: None)
        self.assertEqual(net._guard(reply("93.184.216.34"), "проверка", net.WorldError)._content, b"ok")
        for peer in ("10.0.0.5", "100.101.102.103", None):   # rebinding to inside, the owner's Tailscale, an unseen peer
            with self.assertRaises(net.WorldError):
                net._guard(reply(peer), "проверка", net.WorldError)
        with mock.patch.object(net, "MOST_BYTES", 3), self.assertRaises(net.WorldError) as caught:
            net._guard(reply("93.184.216.34", b"12345"), "проверка", net.WorldError)
        self.assertIn("больше", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
