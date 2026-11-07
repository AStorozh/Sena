"""Hire-flow: reads and actions through the Agent API, edits only after a "yes", cost in the journal, "courier" search (Sena downloads the hh.ru pages).
Hire-flow and hh.ru are replaced by fake servers on this same Mac."""
import json
import re
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from sena import config
from sena.agents.ag_sena import tools
from sena.core import costs, safety, settings, status
from sena.core.tools import Failure
from sena.world import hireflow

KEY = "hf_" + "x" * 43


class Server:
    """Fake server: routes (method, path without parameters) -> (code, body or a function of the request). Remembers every request."""

    def __init__(self):
        self.routes, self.seen = {}, []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_any(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0))) if self.headers.get("Content-Length") else b""
                body = json.loads(raw) if raw else None
                path = self.path.split("?")[0]
                owner.seen.append({"method": self.command, "path": path, "query": self.path.partition("?")[2], "auth": self.headers.get("Authorization"),
                                   "idem": self.headers.get("Idempotency-Key"), "body": body})
                route = owner.routes.get((self.command, path))
                found = route(body) if callable(route) else route or (404, {"error": {"code": "not_found", "message": "нет такого"}})
                code, data, extra = (tuple(found) + ({},))[:3]   # a route may add headers: (code, body, {name: value})
                payload = data.encode() if isinstance(data, str) else json.dumps(data, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/html" if isinstance(data, str) else "application/json")
                for name, value in extra.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PUT = handle_any

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # a short poll: shutdown waits for it, and two servers per test at the default 0.5 s took 17 s of the suite
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def usage(cost):
    return {"llm_calls": 1, "tokens_in": 100, "tokens_out": 50, "cost_usd": cost}


class Base(unittest.TestCase):
    def setUp(self):
        self.hf, self.hh = Server(), Server()
        self.addCleanup(self.hf.stop)
        self.addCleanup(self.hh.stop)
        for patch in (mock.patch.object(config, "HIREFLOW_URL", self.hf.url), mock.patch.object(config, "HIREFLOW_KEY", KEY),
                      mock.patch.object(hireflow, "HH_HOSTS", ("127.0.0.1",)), mock.patch.object(hireflow, "PAUSE", 0), mock.patch.object(hireflow, "POLL_EVERY", 0)):
            patch.start()
            self.addCleanup(patch.stop)
        self.spent = []
        patch = mock.patch.object(costs, "record", side_effect=lambda *a, **k: self.spent.append((a, k)))
        patch.start()
        self.addCleanup(patch.stop)
        before = settings.load()
        self.addCleanup(settings.update, {k: before[k] for k in settings.DEFAULTS})
        tools.pending.clear()


class Reading(Base):
    def test_overview_joins_the_capabilities_and_the_cabinet_and_marks_it_foreign(self):
        self.hf.routes[("GET", "/agent/v1")] = (200, {"key": {"name": "sena", "scopes": ["read"]}, "capabilities": [{"do": "Что нового"}], "conventions": {"money": "usage"}})
        self.hf.routes[("GET", "/agent/v1/me")] = (200, {"counts": {"resumes": 2}, "budget": {"remaining_usd": 0.9}})
        out = hireflow.hireflow_overview()
        self.assertIn("ВНЕШНИЕ ДАННЫЕ", out)
        self.assertIn("Что нового", out)
        self.assertIn('"remaining_usd":0.9', out)
        self.assertEqual(self.hf.seen[0]["auth"], f"Bearer {KEY}")

    def test_read_accepts_short_paths_and_encodes_parameters(self):
        self.hf.routes[("GET", "/agent/v1/matches")] = (200, {"items": []})
        hireflow.hireflow_read("/matches?q=тимлид python&min_score=70")
        seen = self.hf.seen[0]
        self.assertEqual(seen["path"], "/agent/v1/matches")
        self.assertEqual(seen["query"], "q=%D1%82%D0%B8%D0%BC%D0%BB%D0%B8%D0%B4%20python&min_score=70")
        self.hf.routes[("GET", "/agent/v1/me")] = (200, {})
        hireflow.hireflow_read("/agent/v1/me")   # the full path works too
        self.assertEqual(self.hf.seen[1]["path"], "/agent/v1/me")
        for bad in ("/matches/../../admin", "/agent/v1/../me", "/ma tches"):
            with self.assertRaises(hireflow.HireFlowError, msg=bad):
                hireflow.hireflow_read(bad)

    def test_errors_are_explained_with_the_servers_hint(self):
        self.hf.routes[("GET", "/agent/v1/me")] = (403, {"error": {"code": "forbidden_scope", "message": "У ключа нет права «read».", "hint": "Выпустите ключ с правом."}})
        with self.assertRaises(hireflow.HireFlowError) as caught:
            hireflow.hireflow_read("/me")
        self.assertIn("forbidden_scope", str(caught.exception))
        self.assertIn("Выпустите ключ с правом", str(caught.exception))

    def test_a_server_that_is_not_running_is_said_plainly(self):
        with mock.patch.object(config, "HIREFLOW_URL", "http://127.0.0.1:1"), self.assertRaises(hireflow.HireFlowError) as caught:
            hireflow.hireflow_read("/me")
        self.assertIn("запущен ли он", str(caught.exception))

    def test_missing_address_key_and_insecure_address(self):
        with mock.patch.object(config, "HIREFLOW_URL", ""), self.assertRaises(hireflow.HireFlowError) as caught:
            hireflow.hireflow_read("/me")
        self.assertIn("HIREFLOW_URL", str(caught.exception))
        with mock.patch.object(config, "HIREFLOW_KEY", ""), self.assertRaises(hireflow.HireFlowError) as caught:
            hireflow.hireflow_read("/me")
        self.assertIn("api-key create", str(caught.exception))
        with mock.patch.object(config, "HIREFLOW_URL", "http://hireflow.example.com"), self.assertRaises(hireflow.HireFlowError):
            hireflow.hireflow_read("/me")
        self.assertEqual(self.hf.seen, [])   # the key never went anywhere

    def test_long_answers_are_cut_with_a_hint(self):
        self.hf.routes[("GET", "/agent/v1/history")] = (200, {"items": [{"title": "я" * 300} for _ in range(80)]})
        self.assertIn("вырезано", hireflow.hireflow_read("/history"))


class Doing(Base):
    def test_a_letter_is_written_with_a_fresh_idempotency_key_and_the_cost_is_journaled(self):
        self.hf.routes[("POST", "/agent/v1/letters")] = (201, {"id": 5, "status": "draft", "text": "Здравствуйте!", "usage": usage(0.0123)})
        out = hireflow.hireflow_do("POST", "/letters", {"vacancy_id": "101"})
        self.assertIn("Здравствуйте!", out)
        self.assertIn("Расход ИИ: $0.0123", out)
        seen = self.hf.seen[0]
        self.assertEqual(seen["body"], {"vacancy_id": "101"})
        self.assertRegex(seen["idem"], r"^[0-9a-f]{32}$")
        hireflow.hireflow_do("POST", "/letters", {"vacancy_id": "101"})
        self.assertNotEqual(self.hf.seen[0]["idem"], self.hf.seen[1]["idem"])   # every decision to write a letter is its own operation
        key, _ = hireflow._key("POST", "/agent/v1/letters", {"vacancy_id": "202"})
        self.assertEqual(key, hireflow._key("POST", "/agent/v1/letters", {"vacancy_id": "202"})[0])   # a retry before success: the same key
        self.assertEqual(self.spent[0], (("hireflow", "agent-api"), {"units": {"usd": 0.0123}, "purpose": "/agent/v1/letters"}))
        self.assertEqual(len(self.spent), 2)

    def test_a_retry_after_a_failure_carries_the_same_key_and_a_replay_is_not_paid_again(self):
        answers = iter([(500, {"error": {"code": "busy", "message": "сервер занят"}}),
                        (201, {"id": 7, "text": "Добрый день!", "usage": usage(0.02)}, {"Idempotent-Replay": "true"})])
        self.hf.routes[("POST", "/agent/v1/letters")] = lambda body: next(answers)
        with self.assertRaises(hireflow.HireFlowError):
            hireflow.hireflow_do("POST", "/letters", {"vacancy_id": "303"})
        out = hireflow.hireflow_do("POST", "/letters", {"vacancy_id": "303"})
        self.assertEqual(self.hf.seen[0]["idem"], self.hf.seen[1]["idem"])   # the retry is the same operation
        self.assertIn("Добрый день!", out)
        self.assertIn("прежний ответ", out)
        self.assertNotIn("Расход ИИ", out)
        self.assertEqual(self.spent, [])   # paid once, when it was done, and journaled by that run

    def test_a_timeout_says_the_outcome_is_unknown_and_the_same_call_is_safe(self):
        with mock.patch.object(hireflow.net, "send", side_effect=hireflow.HireFlowError("Hire-flow: не ответил за 150 с")), \
                self.assertRaisesRegex(hireflow.HireFlowError, "неизвестно.*повтори тот же вызов"):
            hireflow.hireflow_do("POST", "/letters", {"vacancy_id": "404"})

    def test_only_known_generation_addresses_are_allowed(self):
        for method, path in (("POST", "/profile"), ("PUT", "/profile"), ("POST", "/searches"), ("POST", "/knowledge"), ("GET", "/letters"), ("POST", "/jobs/1"),
                             ("POST", "/letters/5/delete"), ("DELETE", "/letters/5")):
            with self.assertRaises(hireflow.HireFlowError, msg=f"{method} {path}"):
                hireflow.hireflow_do(method, path, {})
        self.assertEqual(self.hf.seen, [])
        for ok in ("/letters/7/revise", "/texts", "/vacancies/analyze", "/vacancies", "/resumes", "/profile/advice"):
            self.hf.routes[("POST", "/agent/v1" + ok)] = (200, {"ok": True})
            self.assertIn("ok", hireflow.hireflow_do("POST", ok, {}))

    def test_the_cost_unit_is_priced_by_the_amount_hireflow_reported(self):
        self.assertEqual(costs.price("hireflow", "agent-api", "", {"usd": 0.02}), (0.02, True))


class Changing(Base):
    def test_a_profile_change_waits_for_tema_and_is_applied_only_in_the_next_turn(self):
        self.hf.routes[("GET", "/agent/v1/profile")] = (200, {"headline": "Python Dev", "location": "Москва", "full_name": "Иван"})
        self.hf.routes[("PUT", "/agent/v1/profile")] = (200, {"headline": "AI Engineer"})
        status.new_turn("да")
        answer = tools.hireflow_change("PUT", "/profile", {"headline": "AI Engineer"})
        sid = re.search(r"\[(\w+)\]", answer).group(1)
        self.assertIn("Ещё не применено", answer)
        self.assertIn("Python Dev", answer)   # "before": only for the fields that are changing
        self.assertNotIn("Москва", answer)
        self.assertEqual([r["method"] for r in self.hf.seen], ["GET"])   # no write happened
        self.assertIn("Нельзя подтвердить в том же ходе", tools.confirm_send(sid))
        status.new_turn("да")
        self.assertIn("Применено в Hire-flow", tools.confirm_send(sid))
        self.assertEqual([r["method"] for r in self.hf.seen], ["GET", "PUT"])
        self.assertEqual(self.hf.seen[1]["body"], {"headline": "AI Engineer"})

    def test_a_new_version_shows_the_current_text_and_new_knowledge_has_nothing_to_compare(self):
        self.hf.routes[("GET", "/agent/v1/knowledge/3")] = (200, {"title": "Шлюз", "version": 1, "text": "Старый текст проекта"})
        status.new_turn("да")
        shown = tools.hireflow_change("POST", "/knowledge/3/versions", {"text": "Новый текст"})
        self.assertIn("Старый текст проекта", shown)
        self.assertIn("Новый текст", shown)
        tools.hireflow_change("POST", "/knowledge", {"kind": "project", "title": "Новый", "text": "Описание"})
        self.assertEqual([r["method"] for r in self.hf.seen], ["GET"])   # new material has no "before": no extra request

    def test_everything_else_cannot_be_changed(self):
        for method, path in (("DELETE", "/knowledge/3"), ("POST", "/searches"), ("PUT", "/knowledge"), ("POST", "/profile"), ("POST", "/letters"), ("PUT", "/profile/advice"), ("POST", "/keys")):
            with self.assertRaises(hireflow.HireFlowError, msg=f"{method} {path}"):
                tools.hireflow_change(method, path, {})
        self.assertEqual(self.hf.seen, [])


def page(*ids):
    return "<html>" + "".join(f"<vacancy id={i}>" for i in ids) + "</html>"


class Searching(Base):
    def routes(self, fresh=2, found=5, job=None):
        ids = [str(100 + i) for i in range(fresh)]
        self.hf.routes[("POST", "/agent/v1/searches")] = (201, {"mode": "courier", "resume_id": 7, "usage": usage(0.004),
                                                               "fetch": {"urls": [f"{self.hh.url}/search?page=0", f"{self.hh.url}/search?page=1"]}})
        self.hf.routes[("POST", "/agent/v1/searches/7/pages")] = (200, {"found": found, "fresh": fresh, "skipped_seen": found - fresh, "skipped_words": 0, "vacancy_ids": ids,
                                                                        "detail_fetch": {"urls": [{"id": i, "url": f"{self.hh.url}/vacancy/{i}"} for i in ids]}})
        self.hf.routes[("POST", "/agent/v1/searches/7/details")] = (200, {"enriched": fresh})
        self.hf.routes[("POST", "/agent/v1/searches/7/score")] = (202, {"job": {"id": "job1", "status": "queued"}})
        self.hf.routes[("GET", "/agent/v1/jobs/job1")] = (200, job or {"status": "done", "result": {"scored": fresh, "usage": usage(0.011), "matches": [
            {"vacancy_id": "100", "title": "Python Developer", "company": "ACME", "salary": {"text": "300 000 ₽"}, "score": 88, "reasons": ["стек совпал"], "url": "https://hh.ru/vacancy/100"},
            {"vacancy_id": "101", "title": "Backend", "company": "Beta", "salary": None, "score": 41, "reasons": [], "url": "https://hh.ru/vacancy/101"}]}})
        self.hh.routes[("GET", "/search")] = (200, page("a"))
        for i in ids:
            self.hh.routes[("GET", f"/vacancy/{i}")] = (200, f"<html>вакансия {i}</html>")

    def test_the_whole_search_runs_with_pages_downloaded_by_sena(self):
        self.routes()
        out = hireflow.hireflow_search(volume=10, city="Москва", schedule="remote", queries="python, backend", min_score=60)
        posts = {r["path"]: r for r in self.hf.seen if r["method"] == "POST"}
        plan = posts["/agent/v1/searches"]["body"]
        self.assertEqual(plan, {"mode": "courier", "volume": 10, "filters": {"city": "Москва", "schedule": "remote"}, "queries": ["python", "backend"], "min_score": 60})
        self.assertEqual(posts["/agent/v1/searches/7/pages"]["body"], {"htmls": [page("a"), page("a")], "volume": 10})   # the HTML went through exactly as Sena downloaded it
        self.assertEqual(posts["/agent/v1/searches/7/details"]["body"]["items"], [{"id": "100", "html": "<html>вакансия 100</html>"}, {"id": "101", "html": "<html>вакансия 101</html>"}])
        self.assertEqual(posts["/agent/v1/searches/7/score"]["body"], {"vacancy_ids": ["100", "101"], "volume": 10})
        self.assertTrue(posts["/agent/v1/searches"]["idem"] and posts["/agent/v1/searches/7/score"]["idem"])   # expensive steps carry an idempotency key
        self.assertIn("на страницах 5, новых 2, оценено 2; подходящих (от 60): 1", out)
        self.assertIn("1. Python Developer: ACME, 300 000 ₽, балл 88 (стек совпал)", out)
        self.assertNotIn("Backend", out)   # below the threshold isn't shown
        self.assertIn("Расход ИИ $0.0150", out)
        self.assertIn("ВНЕШНИЕ ДАННЫЕ", out)
        self.assertEqual([k["units"]["usd"] for _, k in self.spent], [0.004, 0.011])   # both costs are recorded

    def test_details_can_be_skipped(self):
        self.routes()
        hireflow.hireflow_search(details=False)
        self.assertNotIn("/agent/v1/searches/7/details", [r["path"] for r in self.hf.seen])
        self.assertFalse([r for r in self.hh.seen if r["path"].startswith("/vacancy")])

    def test_nothing_new_stops_before_the_expensive_step(self):
        self.routes(fresh=0, found=4)
        out = hireflow.hireflow_search()
        self.assertIn("Новых вакансий нет: на страницах hh.ru 4, из них уже виденных 4", out)
        self.assertNotIn("/agent/v1/searches/7/score", [r["path"] for r in self.hf.seen])

    def test_pages_without_a_single_vacancy_are_not_nothing_new(self):
        """career-ops: a gate that found nothing reads exactly like one that could not look; zero on every page is a changed site."""
        self.routes(fresh=0, found=0)
        out = hireflow.hireflow_search()
        self.assertIsInstance(out, Failure)
        self.assertIn("не нашлось ни одной вакансии", out)
        self.assertNotIn("Новых вакансий нет", out)

    def test_hh_pages_come_only_from_hh_and_a_blocked_first_page_is_explained(self):
        self.routes()
        self.hf.routes[("POST", "/agent/v1/searches")] = (201, {"resume_id": 7, "usage": None, "fetch": {"urls": ["https://evil.example/x"]}})
        with self.assertRaises(hireflow.HireFlowError) as caught:
            hireflow.hireflow_search()
        self.assertIn("не с hh.ru", str(caught.exception))
        self.assertEqual(self.hh.seen, [])
        self.routes()
        self.hh.routes[("GET", "/search")] = (403, "ddos-guard")
        with self.assertRaises(hireflow.HireFlowError) as caught:
            hireflow.hireflow_search()
        self.assertIn("защита сайта", str(caught.exception))
        self.assertNotIn("/agent/v1/searches/7/pages", [r["path"] for r in self.hf.seen])   # an empty set isn't sent to Hire-flow

    def test_a_failing_second_page_does_not_lose_the_first(self):
        self.routes()
        calls = []

        def flaky(body=None):
            calls.append(1)
            return (200, page("a")) if len(calls) == 1 else (403, "блок")

        self.hh.routes[("GET", "/search")] = flaky
        out = hireflow.hireflow_search()
        self.assertIn("Часть страниц поиска не скачалась", out)
        pages = [r for r in self.hf.seen if r["path"].endswith("/pages")][0]
        self.assertEqual(pages["body"]["htmls"], [page("a")])

    def test_a_failed_or_slow_scoring_job_is_reported(self):
        self.routes(job={"status": "failed", "error": {"code": "budget_exceeded", "message": "лимит"}})
        with self.assertRaises(hireflow.HireFlowError) as caught:
            hireflow.hireflow_search()
        self.assertIn("budget_exceeded", str(caught.exception))
        self.routes(job={"status": "running"})
        with mock.patch.object(hireflow, "POLL_LIMIT", 0):
            out = hireflow.hireflow_search()
        self.assertIn("Оценка ещё идёт (задача job1)", out)
        self.assertIn("/matches?resume_id=7", out)

    def test_a_scoring_finished_after_the_wait_is_paid_once_when_read(self):
        """The cost of a slow scoring was lost: the model read /jobs/<id> later, and that read never reached the journal."""
        self.routes(job={"status": "running"})
        with mock.patch.object(hireflow, "POLL_LIMIT", 0):
            hireflow.hireflow_search()
        self.assertEqual([k["units"]["usd"] for _, k in self.spent], [0.004])   # the plan only
        self.hf.routes[("GET", "/agent/v1/jobs/job1")] = (200, {"status": "done", "result": {"scored": 2, "usage": usage(0.011), "matches": []}})
        hireflow.hireflow_read("/jobs/job1")
        hireflow.hireflow_read("/jobs/job1")   # read again: not paid again
        self.assertEqual([k["units"]["usd"] for _, k in self.spent], [0.004, 0.011])

    def test_the_owners_stop_ends_a_search_between_pages(self):
        from sena.core import cancel
        self.routes()
        calls = []

        def check():
            calls.append(1)
            if len(calls) == 2:   # the owner pressed stop while the first page was downloading
                raise cancel.Stopped("Остановлено по просьбе Темы")
        with mock.patch.object(hireflow.cancel, "check_stop", check), self.assertRaises(cancel.Stopped):
            hireflow.hireflow_search()
        self.assertEqual(len([r for r in self.hh.seen if r["path"] == "/search"]), 1)
        self.assertNotIn("/agent/v1/searches/7/score", [r["path"] for r in self.hf.seen])

    def test_volume_is_limited(self):
        self.routes()
        hireflow.hireflow_search(volume=100000)
        self.assertEqual([r for r in self.hf.seen if r["path"] == "/agent/v1/searches"][0]["body"]["volume"], 100)


class Switches(Base):
    def test_switch_hides_all_hireflow_tools_and_the_key_is_hidden_from_programs(self):
        names = {t["name"] for t in tools.available()}
        self.assertTrue({"hireflow_overview", "hireflow_read", "hireflow_do", "hireflow_change", "hireflow_search"} <= names)
        settings.update({"use_hireflow": False})
        self.assertFalse({n for n in {t["name"] for t in tools.available()} if n.startswith("hireflow_")})
        with mock.patch.dict("os.environ", {"HIREFLOW_KEY": KEY}):
            self.assertNotIn("HIREFLOW_KEY", safety.clean_env())


if __name__ == "__main__":
    unittest.main()
