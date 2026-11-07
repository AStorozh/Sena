"""Overheard speech: lines are kept for good, registered for search-when-asked, linked to who said them; forgetting is the owner's explicit command."""
import json
import time
import unittest
from unittest import mock

from sena import config
from sena.core import events, store
from sena.memory import heard, index, people, recall


class Heard(unittest.TestCase):
    def setUp(self):
        index._reset()
        index.drop(heard.KIND)
        people._people = []
        store.clear(config.EARS_LOG)
        self.olga = people.new_person(name="Ольга")

    def line(self, text, pid="g1", who="неизвестный", when=None, id=None):
        return {"id": id or f"id{abs(hash(text)) % 10 ** 6}", "time": when or time.time(), "tz": "+01:00", "pid": pid, "who": who, "text": text}

    def test_a_line_is_logged_registered_and_linked_to_a_known_voice(self):
        heard.append(self.line("документы будут в пятницу", pid=self.olga["id"], who="Ольга", id="a1"))
        heard.append(self.line("какой-то шум", id="a2"))
        logged = store.lines(config.EARS_LOG)
        self.assertEqual([e["id"] for e in logged], ["a1", "a2"])
        rows = index.search("документы", kinds=[heard.KIND], text_only=True, scope="all")
        self.assertEqual(rows[0]["meta"], {"pid": self.olga["id"], "who": "Ольга"})
        self.assertEqual([(l["kind"], l["ref"], l["rel"]) for l in index.links_of(heard.KIND, "a1")], [("человек", self.olga["id"], "said_by")])
        self.assertEqual(index.links_of(heard.KIND, "a2"), [])   # an unknown voice points at no one
        self.assertNotIn(heard.KIND, {r["kind"] for r in index.search("документы", text_only=True)})   # never in a general search
        self.assertIn("документы будут", recall.find("документы", person="Ольга"))   # but there when asked about her

    def test_old_lines_without_ids_and_corrections_are_understood(self):
        for entry in ({"time": "2026-01-05T10:00:00", "pid": "g1", "who": "неизвестный", "text": "старая реплика"},
                      self.line("новая реплика", id="n1"), {"fix": "n1", "pid": self.olga["id"], "who": "Ольга"}):
            store.append_jsonl(config.EARS_LOG, entry)
        rows = heard.entries()
        self.assertEqual([e["id"] for e in rows][1:], ["n1"])
        self.assertRegex(rows[0]["id"], r"^l[0-9a-f]{10}$")   # named by what it holds
        self.assertEqual(rows[1]["pid"], self.olga["id"])
        report = heard.sync_index()
        self.assertEqual(report["added"], 2)
        self.assertEqual([l["ref"] for l in index.links_of(heard.KIND, "n1")], [self.olga["id"]])
        self.assertGreater(index.search("старая", kinds=[heard.KIND], text_only=True, scope="all")[0]["time"], 0)

    def test_naming_a_voice_moves_its_lines_to_the_person(self):
        entries = [self.line("я Ольга, привезу документы", id="v1"), self.line("а это шум", pid="g2", id="v2")]
        for e in entries:
            heard.append(e)
        heard.assign(entries, "g1", self.olga["id"])
        self.assertEqual([l["ref"] for l in index.links_of(heard.KIND, "v1", rel="said_by")], [self.olga["id"]])
        self.assertEqual(index.links_of(heard.KIND, "v2"), [])
        self.assertEqual(heard.entries()[0]["pid"], self.olga["id"])   # the correction line is applied on reading

    def test_forget_follows_the_corrections_the_review_made(self):
        """The raw log is append-only and a correction is a line of its own: judged on the raw lines, «забудь, что говорила
        Ольга» kept the line the review had given her and deleted the one it had taken from her for Ivan."""
        ivan = people.new_person(name="Иван")
        heard.append(self.line("я переезжаю в Лиссабон в марте", id="a1"))   # an unknown voice the review names as Olga
        heard.append(self.line("Иван говорит о своей машине", pid=self.olga["id"], who="Ольга", id="a2"))   # hers at first, Ivan's in truth
        heard.assign([e for e in heard.entries() if e["id"] == "a1"], "g1", self.olga["id"])
        heard.assign([e for e in heard.entries() if e["id"] == "a2"], self.olga["id"], ivan["id"])
        self.assertEqual(heard.forget("Ольга"), "Забыто реплик: 1")
        self.assertEqual([(e["id"], e["pid"]) for e in heard.entries()], [("a2", ivan["id"])])
        raw = store.read_text(config.EARS_LOG)
        self.assertNotIn("Лиссабон", raw)
        self.assertNotIn('"fix": "a1"', raw)   # its correction went with it
        heard.sync_index()   # startup, night: nothing comes back
        self.assertEqual([r["ref"] for r in index.browse(kinds=[heard.KIND])], ["a2"])

    def test_a_line_from_before_ids_keeps_its_name_when_an_earlier_one_is_forgotten(self):
        """Such lines were named by their place: forgetting the first gave the second its name, and its correction pointed nowhere."""
        store.append_jsonl(config.EARS_LOG, {"time": 1000.0, "pid": "g1", "who": "?", "text": "первая старая реплика"})
        store.append_jsonl(config.EARS_LOG, {"time": 2000.0, "pid": "g1", "who": "?", "text": "вторая старая реплика"})
        second = heard.entries()[1]
        heard.assign([second], "g1", self.olga["id"])
        self.assertEqual(heard.forget(since=0, until=1500), "Забыто реплик: 1")
        self.assertEqual([(e["id"], e["pid"]) for e in heard.entries()], [(second["id"], self.olga["id"])])   # the correction still holds

    def test_old_corrections_by_place_move_to_their_lines_and_survive_a_forget_before_them(self):
        """Corrections and facts from before ids named a line by its place in the log («l1»): a forget of a line before it moved
        them onto someone else's words — Ольга's line lost her, a stranger's became hers for the next «забудь, что говорила Ольга»."""
        from sena.core import moment

        def t(day):
            return f"2026-10-{day:02d} 12:00:00"
        for day, text in ((1, "реплика A"), (2, "реплика B"), (3, "реплика C")):
            store.append_jsonl(config.EARS_LOG, {"time": t(day), "text": text, "pid": "g1", "who": "?"})
        store.append_jsonl(config.EARS_LOG, {"fix": "l1", "pid": self.olga["id"], "who": "Ольга"})   # the review named B by its place
        people.update(self.olga["id"], facts=["едет в Дананг"], source="heard", sources=["l1"])   # and learned this from B
        heard._migrated = False   # the first read after the upgrade
        self.assertEqual({e["text"]: e["pid"] for e in heard.entries()}, {"реплика A": "g1", "реплика B": self.olga["id"], "реплика C": "g1"})
        self.assertNotIn('"fix": "l1"', store.read_text(config.EARS_LOG))   # moved to B's own name, once
        b = next(e for e in heard.entries() if e["text"] == "реплика B")
        self.assertEqual(next(f for f in people.by_id(self.olga["id"])["facts"] if f["text"] == "едет в Дананг")["from"], [b["id"]])
        since = moment.parse(t(1))
        self.assertEqual(heard.forget(since=since - 60, until=since + 60), "Забыто реплик: 1")   # line A goes: the places shift
        self.assertEqual({e["text"]: e["pid"] for e in heard.entries()}, {"реплика B": self.olga["id"], "реплика C": "g1"})
        self.assertIn("фактов о людях, выведенных только из них: 1", heard.forget("Ольга"))   # her line, and what came from it
        self.assertEqual([e["text"] for e in heard.entries()], ["реплика C"])
        self.assertNotIn("едет в Дананг", people.facts(people.by_id(self.olga["id"]), all=True))

    def test_two_people_of_one_name_are_asked_about_not_forgotten_at_random(self):
        other = people.new_person(name="Ольга Иванова")
        people.update(self.olga["id"], name="Ольга Петрова")
        heard.append(self.line("я в Порту до пятницы", pid=self.olga["id"], who="Ольга Петрова", id="o1"))
        heard.append(self.line("я в Лиссабоне", pid=other["id"], who="Ольга Иванова", id="o2"))
        said = heard.forget("Ольга")
        self.assertIn("Ольга Петрова", said)
        self.assertIn("Ольга Иванова", said)
        self.assertIn("Ничего не забыла", said)
        self.assertEqual([e["id"] for e in heard.entries()], ["o1", "o2"])
        self.assertEqual(heard.forget("Ольга Иванова"), "Забыто реплик: 1")
        self.assertEqual([e["id"] for e in heard.entries()], ["o1"])

    def test_merging_two_cards_keeps_the_dropped_ones_lines_and_tags(self):
        """The voice card «Вася (голос)» merged into «Вася» lost what was said under it: «забудь, что говорил Вася» reached
        nothing, and the owner's tags pointed at a card that no longer existed."""
        vasya, voice = people.new_person(name="Вася"), people.new_person(name="Вася (голос)")
        heard.append(self.line("я куплю новый ноутбук", pid=voice["id"], who="Вася (голос)", id="h1"))
        index.put("файл", "inbox/plan.pdf", "plan.pdf: план ремонта")
        index.link("файл", "inbox/plan.pdf", "человек", voice["id"], "about")   # the owner's tag
        people.merge(vasya["id"], voice["id"])
        self.assertEqual([l["ref"] for l in index.links_of(heard.KIND, "h1", rel="said_by")], [vasya["id"]])
        self.assertIn(("файл", "inbox/plan.pdf"), {(l["kind"], l["ref"]) for l in index.links_of("человек", vasya["id"], rel="about")})
        self.assertIn("ноутбук", recall.find("ноутбук", person="Вася", kinds=[heard.KIND]))
        self.assertEqual(heard.forget("Вася"), "Забыто реплик: 1")
        self.assertNotIn("ноутбук", store.read_text(config.EARS_LOG))

    def test_forget_is_explicit_and_leaves_a_trace_in_the_journal(self):
        day = 86400
        heard.append(self.line("раз", pid=self.olga["id"], who="Ольга", when=1_000 * day, id="f1"))
        heard.append(self.line("два", pid=self.olga["id"], who="Ольга", when=1_010 * day, id="f2"))
        heard.append(self.line("три", id="f3", when=1_010 * day))
        self.assertIn("Назови человека или период", heard.forget())
        self.assertIn("Не знаю человека", heard.forget("Виктор"))
        self.assertEqual(heard.forget("Ольга", since=1_005 * day), "Забыто реплик: 1")
        self.assertEqual([e["id"] for e in heard.entries()], ["f1", "f3"])
        self.assertEqual(index.count(heard.KIND), 2)
        self.assertEqual(heard.forget(since=0, until=2_000 * day), "Забыто реплик: 2")
        self.assertEqual(heard.entries(), [])
        self.assertTrue(events.search("all", "забыто реплик", kind="cleanup"))


class ForgetCompletely(Heard):
    def test_forgetting_lines_forgets_what_was_learned_only_from_them(self):
        from sena.agents import ag_ears
        lines = [self.line("Ольга переехала в Дананг", pid=self.olga["id"], who="Ольга", id="f1"),
                 self.line("у Ольги собака Буся", pid=self.olga["id"], who="Ольга", id="f2")]
        for e in lines:
            heard.append(e)
        ag_ears.apply({"about": "о переезде", "people": [{"id": self.olga["id"], "name": "", "facts": ["живёт в Дананге"], "relationship": "", "retire": []}],
                       "merge": []}, lines[:1], {}, lambda pid: pid)
        people.update(self.olga["id"], facts=["сестра Темы"], source="chat")   # known from chat, not from the lines
        card = people.by_id(self.olga["id"])
        self.assertEqual(next(f for f in card["facts"] if f["text"] == "живёт в Дананге")["from"], ["f1"])
        self.assertIn("живёт в Дананге", recall.find("Дананг", person="Ольга"))
        said = heard.forget(person="Ольга")
        self.assertIn("и фактов о людях, выведенных только из них: 1", said)
        card = people.by_id(self.olga["id"])
        self.assertEqual(people.facts(card, all=True), ["сестра Темы"])   # gone for good, not just retired
        past = [card for version in store.versions(config.PEOPLE_FILE) for card in version.get("data") or [] if card.get("id") == self.olga["id"]]
        self.assertNotIn("Дананг", json.dumps(past, ensure_ascii=False))   # nor in her card's history (other tests' people may live there)
        self.assertNotIn("Дананг", recall.find("Дананг", person="Ольга", history=True))   # nor in the registry's past

    def test_a_failure_while_forgetting_leaves_the_words_out_of_the_registrys_history(self):
        """The cards were saved before the registry was purged: a crash between left the forgotten facts for the next
        reconciliation, which dropped them into versions — kept against the owner's word."""
        people.update(self.olga["id"], facts=["лечится у психиатра"], source="heard", sources=["z1"])
        with mock.patch.object(people, "_save", side_effect=OSError("диск полон")), self.assertRaises(OSError):
            people.forget_sources({"z1"})
        self.assertNotIn("психиатр", " ".join(r["text"] for r in index.search("психиатра", text_only=True, history=True, scope="all")))
        people._people = None   # what the cards on disk still say
        people.forget_sources({"z1"})

    def test_the_review_keeps_only_clean_facts_and_retires_only_what_the_card_has(self):
        from sena.agents import ag_ears
        people.update(self.olga["id"], facts=["живёт в Москве"], source="chat")
        entries = [self.line("Ольга: я теперь живу в Стамбуле", pid=self.olga["id"], who="Ольга", id="r1")]
        ops = ag_ears.accept({"people": [{"id": self.olga["id"], "name": "", "relationship": "",
                                          "facts": ["живёт в Стамбуле", "возможно, работает в банке", "x" * 250],
                                          "retire": ["живёт в Москве", "любит кошек"]}], "merge": []},
                             entries, {}, people.by_id)
        self.assertEqual(ops, [("note", self.olga["id"], ["живёт в Стамбуле"], "", ["живёт в Москве"])])
        ag_ears.apply({"people": [{"id": self.olga["id"], "name": "", "relationship": "", "facts": ["живёт в Стамбуле"], "retire": ["живёт в Москве"]}],
                       "merge": []}, entries, {}, lambda pid: pid)
        card = people.by_id(self.olga["id"])
        self.assertEqual(people.facts(card), ["живёт в Стамбуле"])
        self.assertIn("живёт в Москве", people.facts(card, all=True))   # history, not deleted

    def test_the_review_sees_the_conversations_date_and_whole_cards(self):
        from sena.agents import ag_ears
        for i in range(6):
            people.update(self.olga["id"], facts=[f"факт номер {i}"], source="chat")
        when = time.mktime((2026, 10, 24, 18, 30, 0, 0, 0, -1))
        context = ag_ears._people([self.line("привет", pid=self.olga["id"], who="Ольга", when=when)])
        self.assertIn("факт номер 0", context)   # all of hers, not the last four
        self.assertIn("24.10.2026 18:30, суббота", ag_ears._when([self.line("привет", when=when)]))


if __name__ == "__main__":
    unittest.main()
