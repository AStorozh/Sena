"""Portfolio site (world/portfolio.py): reads and links happen right away, edits only through a dry run and Tema's confirmation,
the key only ever goes over https (or to this same Mac), redirects are never followed. The site is replaced by a fake server."""
import json
import re
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from sena import config
from sena.agents.ag_sena import tools
from sena.core import settings, status
from sena.world import portfolio

KEY = "k" * 40


class FakeSite(BaseHTTPRequestHandler):
    seen = []
    code = 200
    reply = {}
    redirect = None

    def log_message(self, *args):
        pass

    def handle_any(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0))) if self.headers.get("Content-Length") else b""
        FakeSite.seen.append((self.command, self.path, self.headers.get("Authorization"), json.loads(raw) if raw else None))
        if FakeSite.redirect:
            self.send_response(302)
            self.send_header("Location", FakeSite.redirect)
            self.end_headers()
            return
        body = json.dumps(FakeSite.reply, ensure_ascii=False).encode()
        self.send_response(FakeSite.code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_any


class Portfolio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeSite)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        FakeSite.seen, FakeSite.code, FakeSite.reply, FakeSite.redirect = [], 200, {"ok": True}, None
        for patch in (mock.patch.object(config, "SITE_URL", self.url), mock.patch.object(config, "SITE_KEY", KEY)):
            patch.start()
            self.addCleanup(patch.stop)
        before = settings.load()
        self.addCleanup(settings.update, {k: before[k] for k in settings.DEFAULTS})
        tools.pending.clear()

    # ---------- reading ----------

    def test_read_sends_the_key_and_marks_the_answer_as_foreign(self):
        FakeSite.reply = {"items": [{"key": "disclaimer", "value": "Текст"}]}
        text = portfolio.site_read("/texts/home?prefix=dis")
        self.assertEqual(FakeSite.seen, [("GET", "/api/sena/v1/texts/home?prefix=dis", f"Bearer {KEY}", None)])
        self.assertIn("ВНЕШНИЕ ДАННЫЕ", text)   # analytics can contain someone else's strings (referral URLs): never execute commands from there
        self.assertIn("Текст", text)

    def test_long_answers_are_cut_with_a_hint(self):
        FakeSite.reply = {"text": "я" * (portfolio.SHOWN + 500)}
        self.assertIn("вырезано", portfolio.site_read("/works"))

    def test_overview_reads_the_root(self):
        portfolio.site_overview()
        self.assertEqual(FakeSite.seen[0][1], "/api/sena/v1")

    def test_stats_builds_the_query_and_checks_it(self):
        portfolio.site_stats("7", "hr-acme")
        self.assertIn("/analytics?range=7&link=hr-acme&parts=kpis", FakeSite.seen[0][1])
        for bad in (("abc", "all"), ("7", "../x"), ("7", "a b")):
            with self.assertRaises(portfolio.SiteError):
                portfolio.site_stats(*bad)

    def test_russian_letters_and_spaces_in_the_query_are_encoded_by_the_tool(self):
        portfolio.site_read("/texts/search?q=Скачать резюме")
        self.assertEqual(FakeSite.seen[0][1], "/api/sena/v1/texts/search?q=%D0%A1%D0%BA%D0%B0%D1%87%D0%B0%D1%82%D1%8C%20%D1%80%D0%B5%D0%B7%D1%8E%D0%BC%D0%B5")
        portfolio.site_read("/texts/ui?search=%D1%80%D0%B5%D0%B7&limit=5")   # already-encoded text isn't encoded a second time
        self.assertEqual(FakeSite.seen[1][1], "/api/sena/v1/texts/ui?search=%D1%80%D0%B5%D0%B7&limit=5")

    def test_bad_paths_are_refused_before_any_request(self):
        for path in ("/texts/../keys", "/works/<script>", "texts home"):
            with self.assertRaises(portfolio.SiteError):
                portfolio.site_read(path)
        self.assertEqual(FakeSite.seen, [])

    # ---------- links ----------

    def test_link_is_created_at_once(self):
        FakeSite.reply = {"code": "acme", "url": "https://x.example/acme"}
        portfolio.site_link_create("Acme", audience="recruiter", target="/resume-file")
        self.assertEqual(FakeSite.seen[0][0:2], ("POST", "/api/sena/v1/links"))
        self.assertEqual(FakeSite.seen[0][3], {"label": "Acme", "audience": "recruiter", "target": "/resume-file"})

    # ---------- config and key safety ----------

    def test_missing_settings_are_said_plainly(self):
        with mock.patch.object(config, "SITE_URL", ""), self.assertRaises(portfolio.SiteError) as caught:
            portfolio.site_overview()
        self.assertIn("SITE_URL", str(caught.exception))
        with mock.patch.object(config, "SITE_KEY", ""), self.assertRaises(portfolio.SiteError) as caught:
            portfolio.site_overview()
        self.assertIn("SENA_SITE_KEY", str(caught.exception))

    def test_key_goes_only_over_https_or_to_this_mac(self):
        with mock.patch.object(config, "SITE_URL", "http://portfolio.example"), self.assertRaises(portfolio.SiteError):
            portfolio.site_overview()
        with mock.patch.object(config, "SITE_URL", "https://portfolio.example/"):
            self.assertEqual(portfolio._base(), "https://portfolio.example/api/sena/v1")

    def test_redirects_are_not_followed_so_the_key_cannot_leave(self):
        FakeSite.redirect = "http://127.0.0.1:1/steal"
        with self.assertRaises(portfolio.SiteError):
            portfolio.site_overview()
        self.assertEqual(len(FakeSite.seen), 1)

    def test_errors_of_the_site_are_explained_in_its_words(self):
        FakeSite.code = 422
        FakeSite.reply = {"error": {"code": "validation", "message": "Ключа «x» в разделе «home» нет", "details": None}}
        with self.assertRaises(portfolio.SiteError) as caught:
            portfolio.site_read("/texts/home")
        self.assertIn("validation", str(caught.exception))
        self.assertIn("Ключа «x»", str(caught.exception))

    # ---------- edits ----------

    def test_preview_never_writes(self):
        FakeSite.reply = {"dry_run": True, "applied": False, "changed": 1, "results": []}
        portfolio.preview("PUT", "/texts/home", {"changes": [{"key": "disclaimer", "value": "Новый"}]})
        portfolio.preview("DELETE", "/links/acme")
        self.assertTrue(FakeSite.seen[0][3]["dry_run"])
        self.assertEqual(FakeSite.seen[1][1], "/api/sena/v1/links/acme?dry_run=1")
        self.assertTrue(portfolio.preview("POST", "/revalidate")["dry_run"])   # harmless: no request made
        self.assertEqual(len(FakeSite.seen), 2)

    def test_apply_sends_the_real_change(self):
        text = portfolio.apply("PATCH", "/works/site-a", {"title": "Новый"})
        self.assertEqual(FakeSite.seen[0][0:2], ("PATCH", "/api/sena/v1/works/site-a"))
        self.assertNotIn("dry_run", FakeSite.seen[0][3])
        self.assertIn("Применено", text)

    def test_only_known_addresses_and_methods_can_be_changed(self):
        for method, path in (("GET", "/texts/home"), ("PUT", "/keys"), ("POST", "/analytics"), ("PUT", "/texts/home?x=1"), ("PATCH", "/../admin"), ("DELETE", "/works/a/b"), ("POST", "/audit/abc/revert"), ("PUT", "/audit/5/revert")):
            with self.assertRaises(portfolio.SiteError, msg=f"{method} {path}"):
                portfolio.preview(method, path, {})
        self.assertEqual(FakeSite.seen, [])

    def test_a_journal_entry_can_be_rolled_back_with_a_preview_first(self):
        FakeSite.reply = {"dry_run": True, "can_revert": True, "conflicts": []}
        portfolio.preview("POST", "/audit/12/revert", {})
        self.assertEqual(FakeSite.seen[0][0:2], ("POST", "/api/sena/v1/audit/12/revert"))
        self.assertTrue(FakeSite.seen[0][3]["dry_run"])
        portfolio.site_read("/audit?kind=write&limit=5&full=1")   # the log is read through ordinary reading
        self.assertEqual(FakeSite.seen[1][1], "/api/sena/v1/audit?kind=write&limit=5&full=1")

    def test_summary_counts_changes(self):
        self.assertIn("изменений 2", portfolio.summary("PUT", "/texts/home", {"changed": 2}))
        self.assertIn("изменений 1", portfolio.summary("PATCH", "/works/a", {"changes": [{"field": "title"}]}))

    # ---------- Tema's confirmation ----------

    def test_site_change_waits_for_tema_and_applies_in_the_next_turn(self):
        FakeSite.reply = {"dry_run": True, "applied": False, "changed": 1, "results": [{"key": "disclaimer", "old": "Старый", "new": "Новый"}]}
        status.new_turn("да")
        answer = tools.site_change("PUT", "/texts/home", {"changes": [{"key": "disclaimer", "value": "Новый"}]})
        sid = re.search(r"\[(\w+)\]", answer).group(1)
        self.assertIn("Старый", answer)   # the model sees the "before -> after" and will show it to Tema
        self.assertEqual(len(FakeSite.seen), 1)   # only the dry run was sent to the site
        self.assertTrue(FakeSite.seen[0][3]["dry_run"])
        self.assertIn("Нельзя подтвердить в том же ходе", tools.confirm_send(sid))
        status.new_turn("да")
        FakeSite.reply = {"applied": True}
        self.assertIn("Применено на сайте", tools.confirm_send(sid))
        self.assertEqual(len(FakeSite.seen), 2)
        self.assertNotIn("dry_run", FakeSite.seen[1][3])
        self.assertIn("Такого сообщения нет", tools.confirm_send(sid))   # can't apply it a second time

    # ---------- the switch ----------

    def test_switch_hides_all_site_tools(self):
        names = {t["name"] for t in tools.available()}
        self.assertTrue({"site_overview", "site_read", "site_stats", "site_link_create", "site_change"} <= names)
        settings.update({"use_site": False})
        self.assertFalse({n for n in {t["name"] for t in tools.available()} if n.startswith("site_")})


if __name__ == "__main__":
    unittest.main()
