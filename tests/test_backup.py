"""Backups and integrity: every file that can't be recomputed is copied, the event journal weekly, copies are checked by opening them,
and the registry comes back whole from its sources after being set aside as broken."""
import os
import time
import unittest
import zipfile
from datetime import datetime, timedelta
from unittest import mock

from sena import config
from sena.agents import ag_night
from sena.core import events, secrets, store
from sena.memory import index, people, recall


class Backups(unittest.TestCase):
    def setUp(self):
        for old in config.BACKUPS_DIR.iterdir():
            if old.is_file():
                old.unlink()
        store.save_versioned(config.STATE_DIR / "fixture.json", {"v": 1})
        store.save_versioned(config.STATE_DIR / "fixture.json", {"v": 2})   # a history file appears
        store.append_jsonl(config.EARS_LOG, {"id": "x1", "time": time.time(), "pid": "g1", "who": "кто-то", "text": "реплика"})
        store.append_jsonl(config.FILES_FILE, {"path": "inbox/a.txt", "t": time.time()})
        secrets._reset()
        secrets.put("тестовый", "secret-value-1", allow=[])
        (config.JOURNAL_DIR / "costs").mkdir(exist_ok=True)   # an empty expense folder still makes an archive; a fake record would poison the real reports
        events.log("start", "для копии")

    def test_everything_that_cannot_be_recomputed_is_copied_and_the_journal_weekly(self):
        copied = ag_night.backup_memory()
        names = sorted(p.name.split("_", 2)[-1] for p in config.BACKUPS_DIR.iterdir())
        for wanted in ("sena.db", "secrets.enc", "skills.zip", "costs.zip", "history.zip", "events.db"):
            self.assertIn(wanted, names, wanted)
        self.assertNotIn("secrets.key", names)   # the key stays on this Mac only
        copy = next(p for p in config.BACKUPS_DIR.iterdir() if p.name.endswith("_sena.db"))
        import sqlite3
        with sqlite3.connect(copy) as db:   # the records, overheard speech and the file manifest included, in one consistent copy
            keys = {k for (k,) in db.execute("SELECT DISTINCT key FROM lines")}
        db.close()   # a connection's with-block only commits
        self.assertTrue({"journal/convo_log.jsonl", "journal/files.jsonl"} <= keys, keys)
        self.assertGreaterEqual(copied, 2)   # the secrets file and the one records copy (memory files exist only when written)
        history = next(p for p in config.BACKUPS_DIR.iterdir() if p.name.endswith("_history.zip"))
        with zipfile.ZipFile(history) as z:
            self.assertTrue(any(n.endswith("fixture.json.history.jsonl") for n in z.namelist()))
        self.assertIsNone(ag_night.backup_memory())   # a second run the same day does nothing
        # the event journal: a fresh copy exists, so the next nightly run skips it; an old one triggers a new copy and keeps three
        copies = sorted(config.BACKUPS_DIR.glob("*_events.db"))
        self.assertEqual(len(copies), 1)
        for n in range(3):   # pretend three old copies from earlier months
            stale = config.BACKUPS_DIR / f"2026-0{n + 1}-01_0400_events.db"
            stale.write_bytes(copies[0].read_bytes())
        past = time.time() - 8 * 86400
        for p in config.BACKUPS_DIR.iterdir():
            os.utime(p, (past, past))   # everything is "eight days old": a new night may run, and the journal copy is due again
        with mock.patch.object(ag_night, "datetime", wraps=datetime) as clock:   # the next night: a different stamp than a second ago
            clock.now.return_value = datetime.now() + timedelta(days=1)
            ag_night.backup_memory()
        copies = sorted(config.BACKUPS_DIR.glob("*_events.db"))
        self.assertEqual(len(copies), ag_night.EVENTS_COPIES)
        self.assertFalse(any(p.name.startswith("2026-01") for p in copies))   # the oldest copies went, the newest stayed
        self.assertEqual(sum(1 for p in config.BACKUPS_DIR.iterdir() if p.name.endswith("_sena.db")), 2)   # yesterday's and today's records

    def test_integrity_check_opens_both_databases_and_the_newest_copy(self):
        index.count()   # the registry file exists once it has been opened
        ag_night.backup_memory()
        report = ag_night.check_integrity()
        self.assertEqual(report["events.db"], "ok")
        self.assertEqual(report["index.db"], "ok")
        self.assertIn("событий", report["copy"])
        self.assertTrue(events.search("today", "проверка целостности", kind="backup"))
        copy = sorted(config.BACKUPS_DIR.glob("*_events.db"))[-1]
        copy.write_bytes(b"not a database at all, just junk bytes" * 100)
        report = ag_night.check_integrity()
        self.assertIn("error", report)
        self.assertTrue(events.search("today", "целостность баз", kind="error"))


class NightCopies(unittest.TestCase):
    def setUp(self):
        for old in config.BACKUPS_DIR.iterdir():
            if old.is_file():
                old.unlink()

    def test_a_copy_asked_for_from_the_app_is_made_even_after_tonights(self):
        """«Сделать копию сейчас» did nothing once the night had made one."""
        ag_night.backup_memory()
        self.assertIsNone(ag_night.backup_memory())   # the night's retry: one copy a day
        with mock.patch.object(ag_night, "datetime", wraps=datetime) as clock:   # a later moment: a stamp of its own
            clock.now.return_value = datetime.now() + timedelta(seconds=5)
            self.assertIsNotNone(ag_night.backup_memory(force=True))
        self.assertEqual(len(list(config.BACKUPS_DIR.glob("*_sena.db"))), 2)
        self.assertTrue(events.search("today", kind="backup", query="копия"))

    def test_the_registry_with_its_versions_is_copied_and_a_damaged_one_is_not(self):
        """index.db holds the only history of what records said before: it was never copied."""
        import sqlite3
        index.put("факт", "ночь-1", "любит зелёный чай")
        ag_night.backup_memory()
        copy = sorted(config.BACKUPS_DIR.glob("*_index.db"))[-1]
        with sqlite3.connect(copy) as db:
            self.assertGreaterEqual(db.execute("SELECT COUNT(*) FROM items WHERE text LIKE '%зелёный чай%'").fetchone()[0], 1)
            db.execute("SELECT COUNT(*) FROM versions").fetchone()   # the versions are in it
        db.close()
        self.assertIn("версий", ag_night.check_integrity()["index_copy"])   # the newest copy opens and counts
        damaged = config.STATE_DIR / "damaged-index.db"
        with sqlite3.connect(damaged) as db:
            db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, text TEXT)")
            db.executemany("INSERT INTO items(text) VALUES (?)", [(f"строка {i} " * 5,) for i in range(3000)])
        db.close()
        with open(damaged, "r+b") as f:
            f.seek(4096 * 3 + 50)
            f.write(b"\xff" * 2000)
        self.addCleanup(damaged.unlink)
        with mock.patch.object(ag_night.config, "INDEX_DB", damaged):
            self.assertIsNone(ag_night._copy_index("2026-10-25_040000"))
        self.assertFalse((config.BACKUPS_DIR / "2026-10-25_040000_index.db").exists())   # the good copies stay the newest
        sent = []
        ag_night.ATTENTION.unlink(missing_ok=True)
        with mock.patch.object(ag_night, "check_integrity", return_value={"index.db": "*** in database main *** Tree 2 page 4"}), \
                mock.patch("sena.telegram.api.send_text", lambda text, **k: sent.append(text)), mock.patch.object(ag_night.index, "set_aside") as aside, \
                mock.patch.object(ag_night.recall, "sync_all", return_value="сверено") as rebuilt:
            self.assertIn("восстановлен", ag_night._databases())
            ag_night._databases()
        self.assertEqual(aside.call_args.args, ("*** in database main *** Tree 2 page 4", copy))   # the newest good copy takes its place
        self.assertEqual(rebuilt.call_count, 2)   # and is brought up to now from the records
        self.assertEqual(len(sent), 1)   # the owner hears it once
        self.assertIn("отложила", sent[0])

    def test_a_registry_that_failed_its_check_is_set_aside_and_replaced_by_its_copy(self):
        import sqlite3
        index.put("факт", "ночь-2", "любит горный велосипед")
        index.checkpoint()
        good = config.STATE_DIR / "good-index.db"
        with sqlite3.connect(config.INDEX_DB) as db, sqlite3.connect(good) as out:
            db.backup(out)
        db.close()
        out.close()
        self.addCleanup(good.unlink, missing_ok=True)
        kept = index.set_aside("Tree 2 page 4", good)
        self.addCleanup(lambda: [p.unlink() for p in config.STATE_DIR.glob("index.broken-*")])
        self.assertTrue((config.STATE_DIR / kept).exists())   # the damaged one is kept beside, never deleted
        self.assertIn("горный велосипед", " ".join(r["text"] for r in index.search("горный велосипед", text_only=True)))   # the copy answers


class NightSteps(unittest.TestCase):
    def test_one_failed_step_doesnt_cost_the_night_its_other_jobs(self):
        """A failed cloud call in the tidy skipped eight jobs, the nightly system check among them — on the night Claude was
        down, the check that would have said so. And the learning lock is held for the tidy itself, not the whole night."""
        from sena.agents import checkup, common
        from sena.memory import task_log
        ran = []
        store.save_json(ag_night.TIDY_STATE, {}, indent=1)   # memory changed since the last night: the model is asked
        ag_night.NIGHT_DONE.unlink(missing_ok=True)
        self.addCleanup(ag_night.NIGHT_DONE.unlink, missing_ok=True)

        def overloaded(text):
            self.assertTrue(common.learning_lock.locked())
            raise RuntimeError("529 overloaded")
        with mock.patch.object(ag_night, "think", overloaded), \
                mock.patch.object(ag_night.vault_sync, "sync_all", lambda: ran.append("хранилище") or "ок"), \
                mock.patch.object(ag_night.recall, "sync_all", lambda: ran.append("указатель") or "ок"), \
                mock.patch.object(ag_night.files, "read_pending", lambda n: ran.append("файлы") or 0), \
                mock.patch.object(ag_night.roles, "graduate", lambda: []), \
                mock.patch.object(ag_night.store, "outside_changes", lambda: []), \
                mock.patch.object(checkup, "night", lambda: ran.append("проверка") or ""):
            with self.assertRaises(RuntimeError):   # the night is not done: the scheduler tries again in an hour
                ag_night.tidy()
        self.assertEqual(ran, ["хранилище", "указатель", "файлы", "проверка"])
        self.assertIn("память и навыки: не удалось — 529 overloaded", task_log.items[-1]["answer"])
        self.assertFalse(common.learning_lock.locked())


class NightRetry(unittest.TestCase):
    def test_a_night_whose_cloud_call_failed_is_tried_again_and_the_retry_runs_only_what_failed(self):
        """The round-2 audit: every step caught its own failure, so the scheduler marked the night done and memory and skills
        waited a whole day after one failed cloud call."""
        ran = []
        ag_night.NIGHT_DONE.unlink(missing_ok=True)
        self.addCleanup(ag_night.NIGHT_DONE.unlink, missing_ok=True)
        outcomes = [RuntimeError("529 overloaded"), "Память и навыки и так чистые"]

        def memory():
            ran.append("память")
            out = outcomes.pop(0)
            if isinstance(out, Exception):
                raise out
            return out
        steps = (("копия", lambda: ran.append("копия") or ""), ("память и навыки", memory), ("проверка системы", lambda: ran.append("проверка") or ""))
        with mock.patch.object(ag_night, "NIGHT", steps):
            with self.assertRaises(RuntimeError) as failed:
                ag_night.tidy()
            self.assertIn("память и навыки", str(failed.exception))
            ag_night.tidy()   # an hour later: only the failed step runs, and the night is done
        self.assertEqual(ran, ["копия", "память", "проверка", "память"])

    def test_a_registry_damaged_past_its_own_check_is_not_copied(self):
        """quick_check itself raised «database disk image is malformed» on a badly damaged registry instead of a verdict."""
        import sqlite3
        broken = config.STATE_DIR / "malformed-index.db"
        broken.write_bytes(b"SQLite format 3\x00" + b"\x00" * 84 + b"\xff" * 8000)
        self.addCleanup(broken.unlink, missing_ok=True)
        with mock.patch.object(ag_night.config, "INDEX_DB", broken):
            try:
                self.assertIsNone(ag_night._copy_index("2026-10-26_040000"))
            except sqlite3.DatabaseError as e:   # the connection itself refused: still never a copy of it
                self.fail(f"повреждённый указатель уронил копию: {e}")
        self.assertFalse((config.BACKUPS_DIR / "2026-10-26_040000_index.db").exists())

    def test_the_nights_words_go_quietly_and_into_the_conversation(self):
        from sena.memory import history
        ag_night.ATTENTION.unlink(missing_ok=True)
        self.addCleanup(ag_night.ATTENTION.unlink, missing_ok=True)
        sent = []
        with mock.patch("sena.telegram.api.send_text", lambda text, **k: sent.append((text, k))):
            ag_night._tell_once("ann", True, "Память выросла до порога")
            with mock.patch.object(ag_night.store, "outside_changes", lambda: [("memory.md", "+ строка")]):
                ag_night._outside_changes()
        self.assertEqual([k for _, k in sent], [{"quiet": True}, {"quiet": True}])   # at four in the morning: no sound
        self.assertTrue(sent[1][0].startswith("Мои файлы изменил кто-то, кроме меня"))   # she speaks of herself as «я»
        said = [m["content"] for m in history.stored(config.OWNER_ID)[-2:]]
        self.assertEqual(said, [sent[0][0], sent[1][0]])


class Rebuild(unittest.TestCase):
    def test_registry_set_aside_as_broken_comes_back_from_its_sources(self):
        people._people = []
        olga = people.new_person(name="Ольга")
        people.update(olga["id"], facts=["сестра"])
        index.put("файл", "inbox/x.txt", "x.txt: договор")
        index.link("файл", "inbox/x.txt", "человек", olga["id"], "about")   # an owner's tag: kept in links.jsonl
        index.checkpoint()
        index._reset()
        config.INDEX_DB.write_bytes(b"this is not sqlite" * 64)
        for suffix in ("-wal", "-shm"):
            config.INDEX_DB.with_name(config.INDEX_DB.name + suffix).unlink(missing_ok=True)
        self.assertEqual(index.count(), 0)   # opened fresh: the broken file was set aside
        self.assertTrue(list(config.STATE_DIR.glob("index.broken-*.db")))
        recall.sync_all()
        self.assertIn("Ольга: сестра", [r["text"] for r in index.search("сестра", kinds=["человек"], text_only=True)])
        self.assertEqual([(l["kind"], l["ref"]) for l in index.links_of("файл", "inbox/x.txt", rel="about")], [("человек", olga["id"])])
        for p in config.STATE_DIR.glob("index.broken-*"):
            p.unlink()


if __name__ == "__main__":
    unittest.main()
