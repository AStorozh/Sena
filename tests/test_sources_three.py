"""Nine more sources: MusicBrainz, Wiktionary, TVMaze, country safety, OpenAlex and Crossref, DeFiLlama, OSV.dev, SEC EDGAR, OpenStreetMap. The network is replaced with stubs."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sena import config
from sena.world import advisories, countries, crypto, dictionary, maps_osm, music, net, research, sec, tv, vulns
from sena.world.maps_base import MapsError
from tests.test_free_sources import SEEN, Base, reply


class Music(Base):
    def test_artist_with_studio_albums_newest_first_without_live_records(self):
        groups = {"release-groups": [{"title": "Kid A", "first-release-date": "2000-10-02", "primary-type": "Album", "secondary-types": []},
                                     {"title": "Live at Home", "first-release-date": "2012-01-01", "primary-type": "Album", "secondary-types": ["Live"]},
                                     {"title": "OK Computer", "first-release-date": "1997-05-21", "primary-type": "Album", "secondary-types": []},
                                     {"title": "Без даты", "primary-type": "EP", "secondary-types": []}]}
        artist = {"artists": [{"id": "a1", "name": "Radiohead", "type": "Group", "country": "GB", "life-span": {"begin": "1991", "ended": None}, "tags": [{"name": "rock", "count": 5}]},
                              {"id": "a2", "name": "radiohead 3"}]}
        self.serve({"https://musicbrainz.org/ws/2/artist/": artist, "https://musicbrainz.org/ws/2/release-group": groups})
        with mock.patch.object(music.time, "sleep") as pause:
            out = music.music_search("Radiohead")
        pause.assert_called_once()   # we wait one second between requests: that's what MusicBrainz asks for
        self.assertIn("Radiohead (Group; GB; 1991–по сей день; rock)", out)
        self.assertIn("Студийных альбомов и EP: 2. Последние:\n  2000-10-02: Kid A (Album)\n  1997-05-21: OK Computer (Album)", out)
        self.assertNotIn("Live at Home", out)
        self.assertIn("Тёзки и похожие имена: radiohead 3", out)
        self.assertEqual(SEEN[1][1]["artist"], "a1")

    def test_albums_tracks_and_dangerous_query_characters(self):
        self.serve({"https://musicbrainz.org/ws/2/release-group/": {"release-groups": [{"title": "OK Computer", "artist-credit": [{"name": "Radiohead"}], "first-release-date": "1997-05-21", "primary-type": "Album", "secondary-types": []}]},
                    "https://musicbrainz.org/ws/2/recording/": {"recordings": [{"title": "Karma Police", "artist-credit": [{"name": "Radiohead"}], "first-release-date": "1997", "length": 261000, "releases": [{"title": "OK Computer"}]}]}})
        self.assertIn("1. OK Computer — Radiohead; 1997-05-21; Album", music.music_search("OK Computer", "album"))
        self.assertIn("1. Karma Police — Radiohead; 1997; в «OK Computer»; 4:21", music.music_search('Karma "Police" AND (x)', "track"))
        self.assertNotIn('"', SEEN[-1][1]["query"])   # search control characters are stripped out
        with self.assertRaises(music.MusicError):
            music.music_search("  ")

    def test_nothing_found_is_said_the_same_way_for_every_kind(self):
        self.serve({"https://musicbrainz.org/ws/2/artist/": {"artists": []}, "https://musicbrainz.org/ws/2/release-group/": {"release-groups": []},
                    "https://musicbrainz.org/ws/2/recording/": {"recordings": []}})
        for kind in ("artist", "album", "track", "что-то"):
            self.assertEqual(music.music_search("Нетакой", kind), "В MusicBrainz по «Нетакой» ничего нет.", kind)   # her own words, not someone's text


RU_PAGE = "\n= Русский =\n\n=== Морфологические свойства ===\nсо-ба-ка\n\n==== Значение ====\nдомашнее животное ◆ За дверью заскулила собака. И. Автор [НКРЯ]\nсленг: символ «@» ◆ Пример\n\n==== Этимология ====\nОт др.-русск. собака.\n\n= Английский =\n\n==== Значение ====\nчужой язык\n"
EN_PAGE = "\n== English ==\n\n=== Etymology ===\nFrom Serendip + -ity.\n\n=== Noun ===\nserendipity (uncountable)\nA fortunate discovery by chance.\nA quote nobody needs.\nAnother quote.\nThird quote.\n\n== German ==\n\n=== Noun ===\nfremd\n"


class Dictionary(Base):
    def test_russian_meanings_without_book_quotes_and_only_the_native_language_block(self):
        self.serve({"https://ru.wiktionary.org/w/api.php": {"query": {"pages": [{"title": "собака", "extract": RU_PAGE}]}}})
        out = dictionary.word_info("собака")
        self.assertIn("Значение: 1) домашнее животное 2) сленг: символ «@»", out)
        self.assertIn("Этимология: От др.-русск. собака.", out)
        self.assertNotIn("заскулила", out)   # book quote examples after ◆ are removed
        self.assertNotIn("чужой язык", out)

    def test_english_entry_and_missing_words(self):
        self.serve({"https://en.wiktionary.org/w/api.php": {"query": {"pages": [{"title": "serendipity", "extract": EN_PAGE}]}}})
        out = dictionary.word_info("serendipity", "en")
        self.assertIn("Etymology: From Serendip + -ity.", out)
        self.assertIn("Noun: serendipity (uncountable) A fortunate discovery by chance. A quote nobody needs.", out)
        self.assertNotIn("Third quote", out)
        self.assertNotIn("fremd", out)   # the German section of the same page is not used
        net._cache.clear()
        self.serve({"https://ru.wiktionary.org/w/api.php": {"query": {"pages": [{"title": "ыыы", "missing": True}]}}})
        self.assertIn("слова «Ыыы» нет", dictionary.word_info("Ыыы"))
        for lang in ("ru.evil.com/", "../"):
            with self.assertRaises(dictionary.DictionaryError):
                dictionary.word_info("слово", lang)


SHOW = {"name": "Severance", "status": "Running", "genres": ["Drama"], "premiered": "2022-02-18", "rating": {"average": 7.7}, "webChannel": {"name": "Apple TV"}, "url": "https://tvmaze/x",
        "summary": "<p>Про <b>работу</b>.</p>", "_embedded": {"nextepisode": {"season": 3, "number": 1, "name": "Новая", "airstamp": "2026-10-24T01:00:00+00:00"},
                                                              "previousepisode": {"season": 2, "number": 10, "name": "Cold Harbor", "airdate": "2025-03-21"}}}


class TVShows(Base):
    def test_next_episode_previous_one_and_summary_without_html(self):
        self.serve({"https://api.tvmaze.com/singlesearch/shows": SHOW})
        out = tv.tv_show("severance")
        self.assertEqual(SEEN[0][1]["embed[]"], ["nextepisode", "previousepisode"])
        self.assertIn("Severance (Drama): идёт; на Apple TV; с 2022-02-18; рейтинг 7.7", out)
        self.assertRegex(out, r"Следующая серия: сезон 3, серия 1 «Новая», 2\d\.10\.2026 \d\d:\d\d \(у тебя\)")
        self.assertIn("Последняя вышла: сезон 2, серия 10 «Cold Harbor», 2025-03-21", out)
        self.assertIn("О чём: Про работу.", out)

    def test_ended_show_special_without_number_and_unknown_show(self):
        ended = {**SHOW, "status": "Ended", "_embedded": {"previousepisode": {"season": 5, "number": None, "name": "El Camino"}}}
        self.serve({"https://api.tvmaze.com/singlesearch/shows": ended})
        out = tv.tv_show("Breaking Bad")
        self.assertIn("Следующей серии нет: сериал закончился.", out)
        self.assertIn("спецвыпуск «El Camino»", out)
        net._cache.clear()
        self.serve({"https://api.tvmaze.com/singlesearch/shows": ({}, 404)})
        with self.assertRaises(tv.TVError) as caught:
            tv.tv_show("Нетакогосериала")
        self.assertIn("в TVMaze нет", str(caught.exception))


STATE = [{"Title": "Burma - Level 4: Do Not Travel", "Link": "https://state/mmr", "Summary": "<p>Do not travel</p><p>to Burma.</p>", "Updated": "2026-05-07T00:00:00"},
         {"Title": "Vietnam - Level 1: Exercise Normal Precautions", "Link": "https://state/vnm", "Summary": "Exercise normal precautions.", "Published": "2026-06-23T00:00:00"},
         {"Title": "Korea, South - Level 1: Exercise Normal Precautions", "Link": "https://state/kor", "Summary": "ok"}]
FCDO_LIST = {"links": {"children": [{"title": "Myanmar travel advice", "base_path": "/foreign-travel-advice/myanmar"}, {"title": "Vietnam travel advice", "base_path": "/foreign-travel-advice/vietnam"},
                                    {"title": "USA travel advice", "base_path": "/foreign-travel-advice/usa"}]}}


def fcdo_page(alerts=()):
    return {"public_updated_at": "2026-09-21T14:25:10+01:00", "details": {"alert_status": list(alerts), "change_description": "Новое про шторма", "parts": [
        {"title": "Warnings and insurance", "body": "<h2>Storms</h2><p>Season runs May to November.</p>"}, {"title": "Safety and security", "body": "<p>Petty crime is common.</p>"}]}}


class TravelAdvice(Base):
    def card(self, **over):
        return mock.patch.object(countries, "_country", return_value={"name": "Вьетнам", "en": "Vietnam", **over})

    def test_both_governments_are_shown_with_levels_and_sections(self):
        self.serve({"https://cadataapi.state.gov": STATE, "https://www.gov.uk/api/content/foreign-travel-advice/vietnam": fcdo_page(), "https://www.gov.uk/api/content/foreign-travel-advice": FCDO_LIST})
        with self.card():
            out = advisories.travel_advice("Вьетнам")
        self.assertIn("Госдеп США: уровень 1 из 4 (обычные меры предосторожности); обновлено 2026-06-23", out)
        self.assertIn("МИД Великобритании: запретов на поездки нет; обновлено 2026-09-21; что изменилось: Новое про шторма", out)
        self.assertIn("Warnings and insurance: Storms Season runs May to November.", out)   # adjacent words don't run together
        self.assertIn("Safety and security: Petty crime is common.", out)
        self.assertIn("осторожность у каждого своя", out)   # disclaimer: this is not Sena's forecast, but the two governments' assessments

    def test_other_names_of_the_same_country_are_matched_and_alerts_are_translated(self):
        self.serve({"https://cadataapi.state.gov": STATE, "https://www.gov.uk/api/content/foreign-travel-advice/myanmar": fcdo_page(["avoid_all_travel_to_parts"]),
                    "https://www.gov.uk/api/content/foreign-travel-advice": FCDO_LIST})
        with self.card(name="Мьянма", en="Myanmar"):
            out = advisories.travel_advice("Мьянма")
        self.assertIn("уровень 4 из 4 (не ездить)", out)   # the State Department calls the country Burma
        self.assertIn("МИД Великобритании: не ездить в отдельные районы", out)
        self.assertEqual(advisories._pick(STATE, "South Korea", lambda r: r["Title"].split(" - Level")[0])["Link"], "https://state/kor")   # "Korea, South"

    def test_a_missing_country_or_a_silent_source_does_not_break_the_other(self):
        self.serve({"https://cadataapi.state.gov": ({}, 503), "https://www.gov.uk/api/content/foreign-travel-advice": FCDO_LIST})
        with self.card(name="Нигдестан", en="Nowhereland"):
            out = advisories.travel_advice("Нигдестан")
        self.assertIn("Госдеп США: не ответил", out)
        self.assertIn("МИД Великобритании: такой страны в списке рекомендаций нет", out)


def alex(results):
    return {"results": results}


WORK = {"id": "https://openalex.org/W1", "display_name": "Attention", "publication_year": 2017, "cited_by_count": 78007, "doi": "https://doi.org/10.1234/x",
        "authorships": [{"author": {"display_name": n}} for n in ("A", "B", "C", "D")], "primary_location": {"source": {"display_name": "NeurIPS"}}, "open_access": {"oa_url": "https://free/x.pdf"}}


class Research(Base):
    def test_works_authors_citations_and_doi(self):
        self.serve({"https://api.openalex.org/works/doi:10.1234/x": WORK, "https://api.openalex.org/works": alex([WORK]),
                    "https://api.openalex.org/authors": alex([{"id": "https://openalex.org/A1", "display_name": "Geoffrey Hinton", "works_count": 562, "cited_by_count": 529524,
                                                               "summary_stats": {"h_index": 150}, "last_known_institutions": [{"display_name": "University of Toronto"}]}])})
        out = research.research_search("transformers", sort="cited", limit=3)
        self.assertEqual(SEEN[0][1]["filter"], "title_and_abstract.search:transformers")   # when sorting by citations we search title and abstract
        self.assertEqual(SEEN[0][1]["sort"], "cited_by_count:desc")
        self.assertIn("1. Attention (2017, NeurIPS; цитирований 78 007)\n   A, B, C и др.\n   https://doi.org/10.1234/x; читать бесплатно: https://free/x.pdf", out)
        self.assertIn("сверяй название с DOI", out)
        self.assertIn("Geoffrey Hinton (University of Toronto; работ 562, цитирований 529 524, h-индекс 150)", research.research_search("hinton", "authors"))
        self.assertIn("Attention (2017", research.research_search("https://doi.org/10.1234/x.", "works"))
        self.assertEqual(SEEN[-1][0], "https://api.openalex.org/works/doi:10.1234/x")   # a trailing period is not part of the DOI

    def test_citations_use_the_id_of_the_best_work(self):
        self.serve({"https://api.openalex.org/works": alex([WORK])})
        out = research.research_search("attention", "citations", limit=2)
        self.assertEqual(SEEN[1][1]["filter"], "cites:W1")
        self.assertIn("Её цитируют, самые заметные:", out)

    def test_an_empty_openalex_budget_falls_back_to_crossref_for_works_only(self):
        crossref = {"message": {"items": [{"DOI": "10.5678/y", "title": ["Работа"], "author": [{"given": "Ann", "family": "Lee"}], "issued": {"date-parts": [[2020]]},
                                           "container-title": ["Nature"], "is-referenced-by-count": 12}]}}
        self.serve({"https://api.openalex.org": ({}, 429), "https://api.crossref.org/works": crossref})
        out = research.research_search("topic", limit=1)
        self.assertIn("OpenAlex сегодня не отвечает, данные Crossref", out)
        self.assertIn("Работа (2020, Nature; цитирований 12)\n   Ann Lee\n   https://doi.org/10.5678/y", out)
        with self.assertRaises(research.ResearchError) as caught:
            research.research_search("hinton", "authors")
        self.assertIn("OPENALEX_KEY", str(caught.exception))
        net._cache.clear()
        self.serve({"https://api.openalex.org/works/doi:10.5678/y": ({}, 404), "https://api.crossref.org/works/10.5678/y": {"message": crossref["message"]["items"][0]}})
        self.assertIn("Работа (2020", research.research_search("10.5678/y"))   # a DOI missing from OpenAlex is fetched from Crossref
        net._cache.clear()
        self.serve({"https://api.openalex.org": ({}, 429), "https://api.crossref.org/works": {"message": {"items": []}}})
        self.assertIn("Работ по «редкость» нет", research.research_search("редкость"))   # not a bare header with nothing under it

    def test_the_key_is_sent_when_set(self):
        self.serve({"https://api.openalex.org/works": alex([])})
        with mock.patch.object(config, "OPENALEX_KEY", "k"):
            research.research_search("x")
        self.assertEqual(SEEN[0][1]["api_key"], "k")


class DeFi(Base):
    def test_chains_protocols_stablecoins_and_yields(self):
        self.serve({"https://api.llama.fi/v2/chains": [{"name": "Ethereum", "tvl": 49.5e9, "tokenSymbol": "ETH"}, {"name": "Dead", "tvl": 0}, {"name": "Base", "tvl": 6.2e9}],
                    "https://api.llama.fi/protocols": [{"name": "Binance CEX", "category": "CEX", "tvl": 100e9}, {"name": "Aave V3", "category": "Lending", "tvl": 16.9e9, "change_1d": -0.5, "change_7d": None, "chains": ["a", "b"]},
                                                       {"name": "Aave V2", "category": "Lending", "tvl": 1e8, "change_1d": 1, "change_7d": 2, "chains": []}],
                    "https://stablecoins.llama.fi/stablecoins": {"peggedAssets": [{"name": "Tether", "symbol": "USDT", "circulating": {"peggedUSD": 183e9}, "circulatingPrevWeek": {"peggedUSD": 184e9}, "price": 0.9993, "pegMechanism": "fiat-backed"},
                                                                                  {"name": "Shaky", "symbol": "SH", "circulating": {"peggedUSD": 1e9}, "price": 0.93, "pegMechanism": "algorithmic"}]},
                    "https://yields.llama.fi/pools": {"data": [{"project": "a", "chain": "Ethereum", "symbol": "USDC", "stablecoin": True, "tvlUsd": 50e6, "apy": 8.5, "apyBase": 3, "apyReward": 5.5, "ilRisk": "no", "exposure": "single"},
                                                                {"project": "b", "chain": "X", "symbol": "USDT", "stablecoin": True, "tvlUsd": 1e6, "apy": 90, "ilRisk": "no", "exposure": "single"},
                                                                {"project": "c", "chain": "Y", "symbol": "ETH-USDC", "stablecoin": False, "tvlUsd": 90e6, "apy": 40, "ilRisk": "yes", "exposure": "multi"}]}})
        chains = crypto.defi("chains", limit=5)
        self.assertIn("всего 55,7 млрд $", chains)
        self.assertIn("1. Ethereum: 49,5 млрд $ (ETH)", chains)
        self.assertNotIn("Dead", chains)
        top = crypto.defi("protocols")
        self.assertNotIn("Binance", top)   # custodial exchanges don't count as protocols
        self.assertIn("1. Aave V3 (Lending): 16,9 млрд $; за сутки −0,50%, за неделю —; сетей 2", top)
        filtered = crypto.defi("protocols", "v2")
        self.assertIn("Aave V2", filtered)
        self.assertEqual(filtered.count("\n"), 0)   # a name filter finds only one protocol — a single line with no line break
        stables = crypto.defi("stablecoins")
        self.assertIn("Tether (USDT): 183 млрд $; за неделю −0,54%; цена 0,9993 $; fiat-backed", stables)
        self.assertIn("Shaky (SH): 1,0 млрд $; цена 0,9300 $ (ОТКЛОНЕНИЕ от доллара)", stables)
        yields = crypto.defi("yields")
        self.assertIn("1. a / Ethereum / USDC: 8,50% годовых (база 3,00%, награды токенами 5,50%); пул 50,0 млн $", yields)
        self.assertNotIn("b / X", yields)   # small pools are dropped
        self.assertNotIn("ETH-USDC", yields)   # not a stablecoin and subject to impermanent loss
        self.assertIn("высокий риск", yields)
        self.assertIn("Деньги, запертые", crypto.defi("что-то странное"))


class Vulnerabilities(Base):
    def post(self, batch, details):
        def request(method, url, json=None, headers=None, timeout=None, allow_redirects=None, **kw):
            SEEN.append((url, json))
            return reply(batch)
        self.serve({**{f"https://api.osv.dev/v1/vulns/{k}": v for k, v in details.items()}, "https://api.osv.dev/v1/vulns/": ({}, 404)})   # specific before general
        patch = mock.patch.object(net.requests, "request", request)
        patch.start()
        self.addCleanup(patch.stop)

    def test_packages_are_parsed_strictly_and_results_are_sorted_by_severity(self):
        details = {"GHSA-low": {"id": "GHSA-low", "summary": "Мелочь", "database_specific": {"severity": "LOW"}, "aliases": ["CVE-1"], "affected": [{"package": {"name": "requests", "ecosystem": "PyPI"}, "ranges": [{"events": [{"introduced": "0"}, {"fixed": "2.20.0"}]}]}]},
                   "GHSA-high": {"id": "GHSA-high", "summary": "Утечка паролей", "database_specific": {"severity": "HIGH"}, "aliases": ["CVE-2"], "affected": [{"package": {"name": "requests", "ecosystem": "PyPI"}, "ranges": [{"events": [{"fixed": "2.32.4"}]}]}]}}
        self.post({"results": [{"vulns": [{"id": "GHSA-low"}, {"id": "GHSA-high"}]}, {}]}, details)
        out = vulns.vuln_check(["requests==2.19.0", "npm:@scope/pkg@1.2.3"])
        body = SEEN[0][1]["queries"]
        self.assertEqual(body[0], {"package": {"name": "requests", "ecosystem": "PyPI"}, "version": "2.19.0"})
        self.assertEqual(body[1], {"package": {"name": "@scope/pkg", "ecosystem": "npm"}, "version": "1.2.3"})   # a name with @ is parsed correctly
        self.assertIn("Проверено пакетов: 2; с известными уязвимостями: 1 (записей 2)", out)
        self.assertLess(out.index("GHSA-high"), out.index("GHSA-low"))   # the more severe one comes first
        self.assertIn("requests 2.19.0: GHSA-high (CVE-2); high; Утечка паролей; исправлено в 2.32.4", out)
        for bad in ("requests", "requests==", "npm:lodash", "что-то"):
            with self.assertRaises(vulns.VulnError, msg=bad):
                vulns.vuln_check([bad])

    def test_the_fix_is_taken_from_the_branch_the_project_is_on(self):
        vuln = {"affected": [{"package": {"name": "next", "ecosystem": "npm"}, "ranges": [{"events": [{"introduced": "0"}, {"fixed": "15.5.24"}]}]},
                             {"package": {"name": "next", "ecosystem": "npm"}, "ranges": [{"events": [{"introduced": "16.0.0"}, {"fixed": "16.2.6"}, {"fixed": "16.3.1"}]}]}]}
        self.assertEqual(vulns._fixed(vuln, "npm", "next", "16.2.3"), "16.2.6")   # not 15.5.24: that would be a downgrade for the 16.x branch
        self.assertEqual(vulns._fixed(vuln, "npm", "next", "15.1.0"), "15.5.24")
        self.assertEqual(vulns._fixed(vuln, "npm", "next", "17.0.0"), "15.5.24")   # above every fix: show something at least
        self.assertEqual(vulns._fixed({}, "npm", "next", "1.0.0"), "")

    def test_details_that_did_not_load_never_read_as_no_fix(self):
        """A timeout on the details said "исправления нет; серьёзность не указана; без описания" — a false security statement."""
        self.post({"results": [{"vulns": [{"id": "GHSA-xxxx-yyyy-zzzz"}]}]}, {"GHSA-xxxx-yyyy-zzzz": ({}, 500)})
        out = vulns.vuln_check(["requests==2.19.0"])
        self.assertIn("GHSA-xxxx-yyyy-zzzz (подробности не прочитались: https://osv.dev/vulnerability/GHSA-xxxx-yyyy-zzzz)", out)
        self.assertNotIn("исправления нет", out)

    def test_clean_packages_say_so_with_a_caveat(self):
        self.post({"results": [{}]}, {})
        out = vulns.vuln_check("requests==2.99.0")
        self.assertIn("Проверено пакетов: 1. Известных уязвимостей в базе OSV нет. Это не гарантия", out)

    def test_project_is_read_from_lock_files_and_only_from_the_desktop(self):
        desk = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        project = desk / "Demo"
        project.mkdir()
        (project / "requirements.txt").write_text("requests==2.19.0  # старый\nflask>=2.0\n-e .\nurllib3[socks]==1.26.5 ; python_version>'3'\n")
        (project / "package-lock.json").write_text(json.dumps({"packages": {"": {"version": "1.0.0"}, "node_modules/lodash": {"version": "4.17.20"}, "node_modules/a/node_modules/lodash": {"version": "4.17.21"},
                                                                          "node_modules/linked": {"version": "1.0.0", "link": True}}}))
        self.post({"results": [{}, {}, {}, {}]}, {})
        with mock.patch.object(config, "DESKTOP", desk):   # the desktop is config's, as every path (CLAUDE.md)
            out = vulns.vuln_check(project="Demo")
            names = [(q["package"]["ecosystem"], q["package"]["name"], q["version"]) for q in SEEN[0][1]["queries"]]
            self.assertEqual(names, [("PyPI", "requests", "2.19.0"), ("PyPI", "urllib3", "1.26.5"), ("npm", "lodash", "4.17.20"), ("npm", "lodash", "4.17.21")])   # no >=, no links, no root entry
            self.assertIn("проекта Demo: 4", out)
            for outside in ("/etc", "../..", str(Path.home())):
                with self.assertRaises(vulns.VulnError, msg=outside):
                    vulns.vuln_check(project=outside)
            (desk / "Empty").mkdir()
            with self.assertRaises(vulns.VulnError):
                vulns.vuln_check(project="Empty")


class Sec(Base):
    def setUp(self):
        super().setUp()
        for patch in (mock.patch.object(config, "SEC_CONTACT", "Artemii Test test@example.com"),):
            patch.start()
            self.addCleanup(patch.stop)

    def test_it_refuses_to_call_without_a_contact_because_sec_blocks_anonymous_tools(self):
        with mock.patch.object(config, "SEC_CONTACT", ""), self.assertRaises(sec.SecError) as caught:
            sec.sec_filings("AAPL")
        self.assertIn("SEC_CONTACT", str(caught.exception))
        self.assertEqual(SEEN, [])

    def table(self):
        return {"https://www.sec.gov/files/company_tickers.json": {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, "1": {"cik_str": 1, "ticker": "APLE", "title": "Apple Hospitality REIT, Inc."}}}

    def test_filings_with_links_and_the_form_filter(self):
        recent = {"form": ["8-K", "10-Q", "10-K"], "filingDate": ["2026-10-01", "2026-08-01", "2025-11-01"], "reportDate": ["", "2026-06-27", "2025-09-27"],
                  "accessionNumber": ["0000320193-26-000001", "0000320193-26-000002", "0000320193-25-000003"], "primaryDocument": ["a.htm", "b.htm", "c.htm"]}
        self.serve({**self.table(), "https://data.sec.gov/submissions/CIK0000320193.json": {"sicDescription": "Electronic Computers", "fiscalYearEnd": "0927", "filings": {"recent": recent}}})
        out = sec.sec_filings("aapl", "10-K")
        self.assertEqual(SEEN[1][0], "https://data.sec.gov/submissions/CIK0000320193.json")   # the ticker is matched exactly, not by the name "Apple Hospitality"
        self.assertIn("Apple Inc. (AAPL), Electronic Computers; финансовый год заканчивается 09.27", out)
        self.assertIn("1. 2025-11-01: 10-K (годовой отчёт), за период по 2025-09-27\n   https://www.sec.gov/Archives/edgar/data/320193/000032019325000003/c.htm", out)
        self.assertNotIn("10-Q", out)
        self.assertEqual(sec._company("Apple Hospitality")[2], "APLE")   # by name: every word must be present
        with self.assertRaises(sec.SecError):
            sec._company("Совсем неизвестная")

    def test_financials_take_year_and_quarter_with_the_change_to_last_year(self):
        def entries(rows):
            return {"units": {"USD": [{"start": s, "end": e, "val": v, "filed": f} for s, e, v, f in rows]}}
        revenue = entries([("2024-09-29", "2025-09-27", 416e9, "2025-10-31"), ("2023-10-01", "2024-09-28", 391e9, "2024-11-01"), ("2026-03-29", "2026-06-27", 94e9, "2026-08-01"),
                           ("2025-03-30", "2025-06-28", 85.8e9, "2025-08-01"), ("2025-09-29", "2026-06-27", 280e9, "2026-08-01")])   # nine months is neither a quarter nor a year
        self.serve({**self.table(), "https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/RevenueFromContractWithCustomerExcludingAssessedTax.json": revenue,
                    "https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/EarningsPerShareDiluted.json": {"units": {"USD/shares": [{"start": "2024-09-29", "end": "2025-09-27", "val": 7.46, "filed": "2025-10-31"}]}}})
        out = sec.sec_financials("AAPL")
        self.assertIn("Выручка: за год по 2025-09-27: 416 млрд $ (год назад 391 млрд $, +6,39%); последний квартал по 2026-06-27: 94,0 млрд $ (год назад 85,8 млрд $, +9,56%)", out)
        self.assertIn("Прибыль на акцию (разводнённая): за год по 2025-09-27: 7,46 $", out)
        self.assertIn("Операционная прибыль: нет данных в отчётах", out)   # this metric isn't present: say so plainly
        self.assertIn("не прогноз и не совет", out)

    def test_sec_down_is_not_no_data_in_the_filings(self):
        concept = "https://data.sec.gov/api/xbrl/companyconcept/"
        self.serve({**self.table(), concept: ({}, 500)})
        with self.assertRaises(sec.SecError) as caught:
            sec.sec_financials("AAPL")
        self.assertIn("не отвечает", str(caught.exception))
        net._cache.clear()
        self.serve({**self.table(), f"{concept}CIK0000320193/us-gaap/EarningsPerShareDiluted.json": {"units": {"USD/shares": [
            {"start": "2024-09-29", "end": "2025-09-27", "val": 7.46, "filed": "2025-10-31"}]}}, f"{concept}CIK0000320193/us-gaap/NetIncomeLoss.json": ({}, 500)})
        out = sec.sec_financials("AAPL")
        self.assertIn("Чистая прибыль: не прочиталось", out)
        self.assertIn("Выручка: нет данных в отчётах", out)   # 404 for every revenue tag: really not in the filings

    def test_a_blocked_request_explains_what_to_check(self):
        self.serve({"https://www.sec.gov/files/company_tickers.json": ({}, 403)})
        with self.assertRaises(sec.SecError) as caught:
            sec.sec_filings("AAPL")
        self.assertIn("SEC_CONTACT", str(caught.exception))


class NominatimPace(unittest.TestCase):
    def test_parallel_calls_still_keep_their_distance(self):
        """Tool calls run in parallel: two place lookups asked Nominatim in the same instant, against its one-a-second rule."""
        import threading
        import time
        times = []
        with mock.patch.object(maps_osm, "PAUSE", 0.2), mock.patch.object(maps_osm, "_last", 0.0):
            threads = [threading.Thread(target=lambda: (maps_osm._pause(), times.append(time.time()))) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(2)
        self.assertGreaterEqual(abs(times[1] - times[0]), 0.18)


class OpenStreetMap(Base):
    def setUp(self):
        super().setUp()
        for patch in (mock.patch.object(maps_osm.time, "sleep"),):
            patch.start()
            self.addCleanup(patch.stop)

    NOMINATIM = [{"lat": "10.95", "lon": "108.24", "display_name": "Муйне, Вьетнам"}]

    def test_places_are_searched_by_tag_sorted_by_distance_and_unnamed_are_skipped(self):
        elements = [{"type": "node", "lat": 10.96, "lon": 108.25, "tags": {"name": "Далёкое", "addr:street": "Main", "addr:housenumber": "5", "opening_hours": "Mo-Su 08:00-20:00", "phone": "+84 1"}},
                    {"type": "node", "lat": 10.951, "lon": 108.241, "tags": {"name": "Ближнее"}}, {"type": "node", "lat": 10.95, "lon": 108.24, "tags": {"amenity": "cafe"}},
                    {"type": "way", "center": {"lat": 10.953, "lon": 108.243}, "tags": {"name": "Здание"}}]
        self.serve({"https://nominatim.openstreetmap.org/search": self.NOMINATIM, "https://overpass-api.de/api/interpreter": {"elements": elements}})
        out = maps_osm.places("кофейня", "Mui Ne", 3)
        self.assertIn('amenity~"^(cafe)$"', SEEN[1][1]["data"])   # "кофейня" (coffee shop) is recognized and searched by tag, not by name
        lines = out.splitlines()
        self.assertTrue(lines[1].startswith("1. Ближнее"))
        self.assertIn("3. Далёкое — Main 5;", out)
        self.assertIn("Mo-Su 08:00-20:00; тел. +84 1", out)
        self.assertNotIn("4.", out)   # places without a name are not shown

    def test_unknown_words_are_searched_in_names_and_a_busy_server_is_replaced(self):
        self.serve({"https://nominatim.openstreetmap.org/search": self.NOMINATIM, "https://overpass-api.de": ({}, 502), "https://overpass.kumi.systems/api/interpreter": {"elements": [{"type": "node", "lat": 10.95, "lon": 108.24, "tags": {"name": "Surf Cover"}}]}})
        out = maps_osm.places("Surf shop", "Mui Ne")
        self.assertIn('["name"~"surf",i]', SEEN[1][1]["data"])   # "shop" is a generic word, so we search by "surf"
        self.assertIn("1. Surf Cover", out)
        with self.assertRaises(MapsError):
            maps_osm.places("кофе", None)

    def test_routes_use_the_right_profile_and_say_there_are_no_traffic_data(self):
        route = {"code": "Ok", "routes": [{"duration": 2100, "distance": 27200}]}
        self.serve({"https://nominatim.openstreetmap.org/search": self.NOMINATIM, "https://routing.openstreetmap.de/routed-car/": route, "https://routing.openstreetmap.de/routed-bike/": route})
        out = maps_osm.route("A", "B", "motorbike")
        self.assertTrue(SEEN[-1][0].startswith("https://routing.openstreetmap.de/routed-car/route/v1/driving/108.24,10.95;108.24,10.95"))
        self.assertIn("На мотоцикле: 35 мин, 27.2 км (без пробок: у OpenStreetMap их нет; для мотоцикла посчитано как для машины)", out)
        maps_osm.route("A", "B", "bicycle")
        self.assertIn("routed-bike", SEEN[-1][0])
        self.assertIn("Google Картах", maps_osm.route("A", "B", "transit"))
        net._cache.clear()
        self.serve({"https://nominatim.openstreetmap.org/search": []})
        with self.assertRaises(MapsError):
            maps_osm.route("Нигде", "Никуда", "drive")


class Registered(unittest.TestCase):
    def test_every_new_tool_is_known_to_sena(self):
        from sena.agents.ag_sena import tools
        names = {t["name"] for t in tools.available()}
        self.assertTrue({"music_search", "word_info", "sec_filings", "sec_financials", "tv_show", "travel_advice", "research_search", "defi", "vuln_check"} <= names)


if __name__ == "__main__":
    unittest.main()
