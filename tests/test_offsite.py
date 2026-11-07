"""The copy that leaves the Mac: one encrypted file a night in the off-site folder (iCloud Drive), only the recovery key opens it,
it never unpacks over data/, the oldest beyond KEEP go — and the secrets' key travels inside it, never in the plain nightly copy."""
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from cryptography.exceptions import InvalidTag

from sena import config
from sena.agents import ag_night
from sena.core import keychain, offsite, secrets, store


class Offsite(unittest.TestCase):
    def setUp(self):
        for old in config.BACKUPS_DIR.iterdir():
            if old.is_file():
                old.unlink()
        folder = offsite.folder()
        if folder.exists():
            for old in folder.iterdir():
                old.unlink()
        folder.parent.mkdir(parents=True, exist_ok=True)   # "iCloud Drive is on"
        store.save_versioned(config.PEOPLE_FILE, [{"id": "p1", "name": "Ольга"}])
        secrets._reset()
        secrets.put("почта", "secret-value-for-copy", allow=[])

    def test_the_night_leaves_one_encrypted_copy_that_only_the_key_opens(self):
        ag_night.backup_memory()
        [copy] = offsite.folder().glob("sena-*.copy")
        blob = copy.read_bytes()
        self.assertTrue(blob.startswith(offsite.MAGIC))
        self.assertNotIn(b"secret-value-for-copy", blob)
        self.assertNotIn("Ольга".encode(), blob)   # nothing readable without the key
        into = Path(tempfile.mkdtemp(prefix="sena-restore-"))
        self.addCleanup(shutil.rmtree, into, True)
        names = offsite.restore(copy, into, offsite.recovery_key())
        self.assertIn("secrets.key", names)   # the plain nightly copy never has it; the encrypted one must, or the secrets are lost with the Mac
        self.assertTrue(any(n.endswith("_sena.db") for n in names))
        self.assertTrue(any(n.endswith("_secrets.enc") for n in names))
        self.assertEqual((into / "secrets.key").read_bytes(), secrets.key_for_copy())
        with self.assertRaises(InvalidTag):
            offsite.restore(copy, into, "00" * 32)   # a wrong key opens nothing
        with self.assertRaises(ValueError):
            offsite.restore(copy, config.DATA / "x")   # never over the data folder

    def test_old_copies_go_and_no_icloud_means_no_copy(self):
        for _ in range(offsite.KEEP + 2):
            offsite.make([], {"a.txt": b"x"})
            time.sleep(0.001)
        self.assertLessEqual(len(list(offsite.folder().glob("sena-*.copy"))), offsite.KEEP)
        with mock.patch.object(config, "OFFSITE_DIR", config.DATA / "no-such-drive" / "Sena"):
            self.assertIsNone(offsite.make([], {"a.txt": b"x"}))

    def test_keychain_stays_off_in_tests(self):
        self.assertFalse(keychain.enabled())
        self.assertIsNone(keychain.get(keychain.SECRETS))
        self.assertFalse(keychain.put(keychain.SECRETS, "00"))

    def test_a_key_file_moves_into_the_keychain_and_leaves_the_folder(self):
        vault = {}
        secrets._reset()
        key = secrets.key_for_copy()   # the file key, made under the test settings
        secrets._reset()
        with mock.patch.object(keychain, "enabled", return_value=True), \
             mock.patch.object(keychain, "get", side_effect=lambda s: vault.get(s)), \
             mock.patch.object(keychain, "put", side_effect=lambda s, v: vault.__setitem__(s, v) or True):
            self.assertEqual(secrets.key_for_copy(), key)
        self.assertEqual(vault[keychain.SECRETS], key.hex())
        self.assertFalse(config.SECRETS_KEY.exists())   # no second copy of the key next to the data
        secrets._reset()
        with mock.patch.object(keychain, "enabled", return_value=True), mock.patch.object(keychain, "get", side_effect=lambda s: vault.get(s)):
            self.assertIn("почта", [r["name"] for r in secrets.names()])   # the store still opens with the key from the Keychain
        store.write_bytes(config.SECRETS_KEY, key, mode=0o600)   # back to the file for the other tests
        secrets._reset()

    def test_a_keychain_that_cant_be_read_is_not_taken_for_no_key(self):
        """A locked Keychain or a cancelled prompt used to read as "no key yet": a new key replaced the real one for good."""
        def run(code, out=""):
            return lambda *a, **k: subprocess.CompletedProcess(a, code, stdout=out, stderr="locked")
        with mock.patch.object(config, "KEYCHAIN", True):
            with mock.patch.object(keychain.subprocess, "run", run(44)):
                self.assertIsNone(keychain.get(keychain.SECRETS))   # no such item: the only "None"
            with mock.patch.object(keychain.subprocess, "run", run(0, "ab12\n")):
                self.assertEqual(keychain.get(keychain.SECRETS), "ab12")
            for broken in (run(51), mock.Mock(side_effect=subprocess.TimeoutExpired("security", 30))):
                with mock.patch.object(keychain.subprocess, "run", broken), self.assertRaises(keychain.Unavailable):
                    keychain.get(keychain.SECRETS)

    def test_no_new_key_while_there_is_a_store_to_open(self):
        secrets._reset()
        secrets.put("почта", "secret-value-for-copy", allow=[])
        key = config.SECRETS_KEY.read_bytes()
        self.addCleanup(secrets._reset)
        self.addCleanup(store.write_bytes, config.SECRETS_KEY, key, mode=0o600)
        put = mock.Mock(return_value=True)
        for get in (mock.Mock(side_effect=keychain.Unavailable("заперта")), mock.Mock(return_value=None)):
            secrets._reset()
            config.SECRETS_KEY.unlink()   # the key moved to the Keychain long ago
            with mock.patch.object(keychain, "enabled", return_value=True), mock.patch.object(keychain, "get", get), \
                    mock.patch.object(keychain, "put", put), self.assertRaises((keychain.Unavailable, RuntimeError)):
                secrets.names()
            store.write_bytes(config.SECRETS_KEY, key, mode=0o600)
        put.assert_not_called()   # the real key in the Keychain is never replaced
        secrets._reset()
        self.assertIn("почта", [r["name"] for r in secrets.names()])   # and the store still opens with it


class Attention(unittest.TestCase):
    def test_a_nightly_warning_reaches_the_owner_once(self):
        ag_night.ATTENTION.unlink(missing_ok=True)
        sent = []
        from sena.telegram import api
        with mock.patch.object(api, "send_text", lambda text, **k: sent.append(text)):   # quiet: the night's words wait for the morning
            ag_night._tell_once("ann", True, "пора")
            ag_night._tell_once("ann", True, "пора")
            ag_night._tell_once("ann", False, "пора")
            ag_night._tell_once("ann", True, "пора")
        self.assertEqual(sent, ["пора", "пора"])   # once when it becomes true, again only after it went away and came back


if __name__ == "__main__":
    unittest.main()
