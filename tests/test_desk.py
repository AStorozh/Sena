"""Mac app API (sena/phone/desk.py): data only with a token, no keys; actions and behavior settings
accept only valid values and control the schedule."""
import json
import os
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest import mock

from sena import config, scheduler
from sena.agents import ag_code, ag_night
from sena.core import attachments, cancel, events, settings, status, store
from sena.memory import index, task_log, vault
from sena.phone import data, desk, server
from sena.world import alerts


class Desk(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, method, path, body=None, token=None):
        conn = HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=10)
        t = config.APP_TOKEN if token is None else token
        conn.request(method, path + ("&" if "?" in path else "?") + "t=" + t, body=json.dumps(body).encode() if body is not None else None)
        answer = conn.getresponse()
        raw = answer.read()
        return answer.status, (json.loads(raw) if answer.getheader("Content-Type", "").startswith("application/json") else raw)

    def test_home_and_cloud_shows_who_does_what_and_moves_a_role(self):
        from sena.core import local, roles
        self.addCleanup(settings.update, {"roles": {}})
        local.embed(["вызов дома для счётчика"])
        _, h = self.req("GET", "/desk/api/home")
        switchable = [r for r in h["roles"] if r["switchable"]]
        self.assertEqual([r["role"] for r in switchable], list(config.ROLES))
        self.assertEqual(next(r for r in switchable if r["role"] == "ears")["place"], "home")
        self.assertGreaterEqual(h["today"]["home"], 1)
        self.assertIn("embeddings", h["today"]["home_by_service"])
        self.assertIn("installed", h["status"])
        _, r = self.req("POST", "/desk/api/role_move", {"role": "compact", "place": "home"})
        self.assertIn("теперь дома", r["result"])
        self.assertEqual(roles.where("compact"), "home")
        _, r = self.req("POST", "/desk/api/role_move", {"role": "nonsense", "place": "home"})
        self.assertIn("Роли:", r["result"])
        desk._tallies.clear()   # the counters are reused for TALLY_FRESH seconds: this one needs this moment's
        _, i = self.req("GET", "/desk/api/integrations")
        self.assertGreaterEqual(i["home"]["uses_today"], 1)   # home calls are the home's, not OpenAI's

    def test_api_needs_token(self):
        code, body = self.req("GET", "/desk/api/overview", token="wrong")
        self.assertEqual((code, body["error"]), (403, "нет доступа"))
        code, _ = self.req("GET", "/desk/../README.md", token="")
        self.assertEqual(code, 403)   # without a password there is nothing, not even "not found": a path outside the screen doesn't reveal what's behind it

    def test_overview_task_events_and_log_without_secrets(self):
        with mock.patch.dict(config._SETTINGS, {"OPENAI_API_KEY": "sk-secret-123", "TOMTOM_KEY": ""}):
            status.begin("пульт: проверочная задача")
            tid = status.current()["id"]
            status.show("смотрю", who="ag_web")
            events.log("tool", "read_chat", name="read_chat", args={"name": "Мама"})
            task_log.save(status.end("готово"))
            config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
            (config.LOGS_DIR / "bot.log").write_text("строка один\nОшибка: пример\n", encoding="utf-8")
            _, o = self.req("GET", "/desk/api/overview")
            self.assertEqual(o["core"]["alive"], False)   # no pid file: the core isn't running, the dashboard is separate
            self.assertEqual(o["accounts"]["keys"]["OPENAI_API_KEY"], True)
            self.assertEqual(o["accounts"]["keys"]["TOMTOM_KEY"], False)
            self.assertNotIn("sk-secret", json.dumps(o))
            self.assertEqual([a["code"] for a in o["agents"]][:2], ["ag_sena", "ag_web"])
            _, cfg = self.req("GET", "/desk/api/config")
            self.assertNotIn("sk-secret", json.dumps(cfg))
            self.assertEqual(cfg["models"]["main"], config.MODEL_MAIN)
            _, t = self.req("GET", "/desk/api/task?id=" + tid)
            self.assertEqual(t["text"], "пульт: проверочная задача")
            self.assertIn("tool", [e["kind"] for e in t["events"]])
            _, k = self.req("GET", "/desk/api/kinds")
            self.assertGreaterEqual(k["counts"].get("tool", 0), 1)
            _, rows = self.req("GET", "/desk/api/events?who=ag_web&kind=step")
            self.assertTrue(rows and all(r["who"] == "ag_web" for r in rows))
            _, log = self.req("GET", "/desk/api/log")
            self.assertIn("Ошибка: пример", log["text"])
            with (config.LOGS_DIR / "bot.log").open("a", encoding="utf-8") as f:
                f.write("третья\n")
            _, more = self.req("GET", f"/desk/api/log?offset={log['offset']}")
            self.assertEqual(more["text"], "третья\n")   # only what was appended

    def test_weekly_and_watch_follow_settings(self):
        before = settings.load()
        self.addCleanup(settings.update, {k: before[k] for k in settings.DEFAULTS})
        # default is Sunday 20:00: 2026-10-18 is a Sunday
        self.assertEqual(scheduler.last_weekly(datetime(2026, 10, 18, 21, 0)), datetime(2026, 10, 18, 20, 0))
        self.assertEqual(scheduler.last_weekly(datetime(2026, 10, 18, 19, 0)), datetime(2026, 10, 11, 20, 0))   # the time hasn't come yet — last week
        # moved to Wednesday 9:00 (day 2): the nearest already-past time is Wednesday 14.10
        _, r = self.req("POST", "/desk/api/settings", {"weekly_day": 2, "weekly_hour": 9, "watch_every_min": 15})
        self.assertEqual((r["settings"]["weekly_day"], r["settings"]["weekly_hour"], r["settings"]["watch_every_min"]), (2, 9, 15))
        self.assertEqual(scheduler.last_weekly(datetime(2026, 10, 18, 21, 0)), datetime(2026, 10, 14, 9, 0))
        self.assertEqual(scheduler.last_weekly(datetime(2026, 10, 14, 8, 0)), datetime(2026, 10, 7, 9, 0))
        _, r = self.req("POST", "/desk/api/settings", {"weekly_day": 9, "watch_every_min": 0})   # out of range — discarded
        self.assertEqual((r["settings"]["weekly_day"], r["settings"]["watch_every_min"]), (2, 15))

    def test_actions_settings_and_scheduler_switches(self):
        before = settings.load()
        self.addCleanup(settings.update, {k: before[k] for k in settings.DEFAULTS})
        self.addCleanup(cancel.cancelled.clear)   # stop_all below marks real tasks — don't leave that for other tests
        _, r = self.req("POST", "/desk/api/settings", {"heartbeat": False, "heartbeat_every_min": 5, "tidy_hour": 6, "verbosity": "huge", "nonsense": 1})
        self.assertEqual((r["settings"]["heartbeat"], r["settings"]["tidy_hour"]), (False, 6))
        self.assertEqual(r["settings"]["heartbeat_every_min"], before["heartbeat_every_min"])   # out of range — discarded
        self.assertEqual(r["settings"]["verbosity"], before["verbosity"])
        self.assertNotIn("nonsense", r["settings"])
        self.assertFalse(scheduler.tidy_due(datetime(2026, 10, 16, 5, 0), {}))    # cleanup hour moved to 6
        self.assertTrue(scheduler.tidy_due(datetime(2026, 10, 16, 6, 0), {}))
        _, r = self.req("POST", "/desk/api/stop_all", {})
        self.assertIn("остановлены", r["result"])
        _, r = self.req("POST", "/desk/api/restart", {})
        self.assertIn("перезапускать нечего", r["error"])   # not the core — nothing to restart
        _, r = self.req("POST", "/desk/api/nope", {})
        self.assertIn("error", r)
        self.assertTrue(any(e["text"] == "приложение: stop_all" for e in events.search("today", kind="app")))   # dashboard actions are in the journal

    def test_memory_rewrite_and_skill_from_desk(self):
        from sena.memory import facts, skills
        self.req("POST", "/desk/api/memory", {"text": "## Обо мне\n- проверка пульта\n"})
        self.assertIn("проверка пульта", facts.read())
        _, m = self.req("GET", "/desk/api/memory")
        self.assertIn("проверка пульта", m["text"])
        _, r = self.req("POST", "/desk/api/skill_write", {"name": "desk-test", "when": "когда проверяем пульт", "recipe": "шаг один"})
        self.assertIn("сохранён", r["result"])
        _, s = self.req("GET", "/desk/api/skills?name=desk-test")
        self.assertEqual((s["when"], s["recipe"], s["code"]), ("когда проверяем пульт", "шаг один", ""))
        self.req("POST", "/desk/api/skill_delete", {"name": "desk-test"})
        self.assertNotIn("desk-test", skills.names())

    def test_core_pid_detection_and_start_stop_guards(self):
        import os
        config.PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        self.addCleanup(lambda: config.PID_FILE.unlink(missing_ok=True))
        c = desk.core()
        self.assertEqual((c["alive"], c["is_core"]), (True, True))
        self.assertEqual(desk.act("core_start", {})["result"], "Сена уже работает")   # don't start a live core a second time
        config.PID_FILE.write_text("999999", encoding="utf-8")
        self.assertFalse(desk.core()["alive"])
        self.assertIn("error", desk.act("core_stop", {}))   # nothing to stop
        self.assertIn("error", desk.act("restart", {}))

    def test_budget_question_pending_and_files(self):
        from sena.core import ask_owner, costs, limits
        from sena.agents.ag_sena import tools as sena_tools
        # daily budget: exhausted — no paid call is made, with a clear explanation
        before = settings.load()
        self.addCleanup(settings.update, {"budget_usd_day": before["budget_usd_day"]})
        costs._reset()
        costs.record("openai", "responses", "gpt-5.4", {"input": 1_000_000, "output": 0})
        settings.update({"budget_usd_day": 0.5})
        with self.assertRaises(limits.LimitReached) as got:
            limits.budget_check()
        self.assertIn("бюджет", str(got.exception))
        from sena.core import llm
        self.assertIn("бюджет", llm.explain(got.exception))
        settings.update({"budget_usd_day": 0})
        limits.budget_check()
        # Tema's question is visible in the overview and can be answered from the dashboard
        ask_owner.set_sender(lambda q, img: None)
        answers = []
        threading.Thread(target=lambda: answers.append(ask_owner.ask("Какой код из СМС?", image=b"jpg", timeout=5)), daemon=True).start()
        for _ in range(50):
            if ask_owner.pending():
                break
            time.sleep(0.05)
        _, o = self.req("GET", "/desk/api/overview")
        self.assertEqual((o["question"]["question"], o["question"]["has_image"]), ("Какой код из СМС?", True))
        conn = HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=10)
        conn.request("GET", "/desk/api/question_image?t=" + config.APP_TOKEN)
        self.assertEqual(conn.getresponse().read(), b"jpg")
        _, r = self.req("POST", "/desk/api/answer", {"text": "1234"})
        self.assertIn("передан", r["result"])
        for _ in range(50):
            if answers:
                break
            time.sleep(0.05)
        self.assertEqual(answers, ["Тема ответил: 1234"])
        self.assertIn("error", self.req("POST", "/desk/api/answer", {"text": "ещё"})[1])
        # a prepared send: visible, confirmed with a button without the turn rule, cancellable
        sent = []
        sid = sena_tools._hold("Мама", "привет", lambda: sent.append("ушло") or "Отправлено")
        _, o = self.req("GET", "/desk/api/overview")
        self.assertIn(sid, [p["id"] for p in o["pending_sends"]])   # other tests' prepared sends may be there too
        self.assertEqual(self.req("POST", "/desk/api/confirm_send", {"id": sid})[1]["result"], "Отправлено")
        self.assertEqual(sent, ["ушло"])
        sid2 = sena_tools._hold("Папа", "пока", lambda: "нет")
        self.assertIn("Отменено", self.req("POST", "/desk/api/cancel_send", {"id": sid2})[1]["result"])
        # files: upload from the dashboard, listing, the file itself, foreign paths aren't served
        conn = HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=10)
        from urllib.parse import quote
        conn.request("POST", "/desk/upload?name=" + quote("заметка.txt") + "&t=" + config.APP_TOKEN, body="привет из пульта".encode())
        up = json.loads(conn.getresponse().read())
        self.assertEqual((up["kind"], up["name"]), ("text", "заметка.txt"))
        _, files = self.req("GET", "/desk/api/files")
        self.assertTrue(any(f["path"] == up["path"] and f["folder"] == "присланное" for f in files))
        conn = HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=10)
        conn.request("GET", "/desk/api/file?path=" + quote(up["path"]) + "&t=" + config.APP_TOKEN)
        self.assertEqual(conn.getresponse().read().decode(), "привет из пульта")
        code, bad = self.req("GET", "/desk/api/file?path=" + quote("/etc/hosts", safe=""))
        self.assertEqual(code, 403)   # a foreign path — a refusal to the client, not a server crash
        self.assertIn("вне папок", bad["error"])
        self.assertNotIn("/etc", bad["error"])
        code, bad = self.req("POST", "/ask", {"text": "x", "files": ["/etc/hosts"]})
        self.assertEqual((code, "вне папок" in bad["error"]), (403, True))

    def test_rules_view_merges_everything_and_toggle_hides_without_deleting(self):
        from sena.memory import rules
        for r in rules.all_rules():
            rules.remove(r["id"])
        for j in scheduler.jobs():
            scheduler.unschedule(j["id"])
        made = rules.add("стоять постоянным указанием")   # in tests the model refuses — background scheduling stays silent, the record remains
        at = (datetime.now() + timedelta(days=1)).strftime("%d.%m.%Y 08:00")
        loose, _ = scheduler.add("Напомнить про созвон", at, "once")   # a job without a rule (via phone or the scheduling tool)

        view = desk.rules_view()
        self.assertEqual([r["id"] for r in view["rules"]], [made["id"]])
        self.assertEqual([j["id"] for j in view["jobs"]], [loose["id"]])   # a job from a rule doesn't land here a second time

        self.assertIn(made["text"], rules.listing())
        off = desk.act("rule_toggle", {"id": made["id"], "enabled": False})
        self.assertEqual(off["enabled"], False)
        self.assertNotIn(made["text"], rules.listing())   # gone from the agents' memory
        self.assertEqual(rules.by_id(made["id"])["text"], made["text"])   # the text remains
        on = desk.act("rule_toggle", {"id": made["id"], "enabled": True})
        self.assertEqual(on["enabled"], True)
        self.assertIn(made["text"], rules.listing())
        self.assertIn("error", desk.act("rule_toggle", {"id": "нет такого", "enabled": True}))

    def test_disabled_job_and_alert_do_not_fire(self):
        for j in scheduler.jobs():
            scheduler.unschedule(j["id"])
        past = (datetime.now() - timedelta(days=1)).strftime(scheduler.FORMAT)
        store.save_json(config.JOBS_FILE, [{"id": "d1", "text": "каждый день", "at": past, "repeat": "daily"},
                                           {"id": "o1", "text": "один раз", "at": past, "repeat": "once"}])
        self.addCleanup(store.save_json, config.JOBS_FILE, [])
        self.assertEqual(desk.act("job_toggle", {"id": "o1", "enabled": False})["enabled"], False)
        self.assertEqual(desk.act("job_toggle", {"id": "d1", "enabled": False})["enabled"], False)
        self.assertEqual(scheduler._due(), [])                                  # disabled ones sit there and don't fire
        self.assertEqual({j["id"] for j in scheduler.jobs()}, {"d1", "o1"})     # and weren't deleted
        self.assertEqual(scheduler.due_within(48), [])
        back = desk.act("job_toggle", {"id": "d1", "enabled": True})            # a repeat whose time has passed — moved to next time
        self.assertGreater(datetime.strptime(back["at"], scheduler.FORMAT), datetime.now())
        self.assertEqual([j["id"] for j in scheduler._due()], [])               # the one-off is still disabled
        desk.act("job_toggle", {"id": "o1", "enabled": True})
        self.assertEqual([j["id"] for j in scheduler._due()], ["o1"])
        self.assertIn("error", desk.act("job_toggle", {"id": "нет такого", "enabled": True}))

        store.save_json(config.ALERTS_FILE, [{"id": "a1", "kind": "crypto", "symbol": "BTC", "condition": "above", "value": 50.0, "once": False, "fired": False}])
        self.addCleanup(store.save_json, config.ALERTS_FILE, [])
        said = []
        with mock.patch.object(alerts, "_price", return_value=100.0):
            self.assertEqual(desk.act("alert_toggle", {"id": "a1", "enabled": False})["enabled"], False)
            self.assertEqual(alerts.check(said.append), 0)                      # a disabled one isn't checked
            self.assertEqual(said, [])
            desk.act("alert_toggle", {"id": "a1", "enabled": True})
            self.assertEqual(alerts.check(said.append), 1)
        self.assertIn("error", desk.act("alert_toggle", {"id": "нет такого", "enabled": True}))


    # ---------- dashboard fixes: job order, journal filters, refusals, background jobs ----------

    def test_overview_counts_the_jobs(self):
        rows = [{"id": "a", "at": "05.11.2026 09:00"}, {"id": "b", "at": "20.10.2026 09:00", "enabled": False}, {"id": "e", "at": "мусор"}]
        store.save_json(config.JOBS_FILE, rows)
        self.addCleanup(store.save_json, config.JOBS_FILE, [])
        _, o = self.req("GET", "/desk/api/overview")
        self.assertEqual(o["scheduler"]["jobs_total"], 3)

    def test_events_filter_by_who_before_limit_and_kinds_follow_filters(self):
        from urllib.parse import quote
        tid = "whotest1"
        for i in range(4):
            events.log("tool", f"web {i}", who="ag_web", tid=tid, name="x")
        events.log("tool", "вкладка", who="ag_web/page", tid=tid, name="x")
        for i in range(10):
            events.log("tool", f"mac {i}", who="ag_mac", tid=tid, name="x")
        events.log("tool", "редкийсловарь", who="ag_web", tid=tid, name="x")
        _, rows = self.req("GET", f"/desk/api/events?tid={tid}&who=ag_web&limit=3")
        self.assertEqual(len(rows), 3)   # the last three events of exactly this worker, not "the last three, filtered down to its own"
        self.assertTrue(all(r["who"].split("/")[0] == "ag_web" for r in rows))
        self.assertEqual(rows[-1]["text"], "редкийсловарь")
        _, rows = self.req("GET", f"/desk/api/events?tid={tid}&who=ag_web&limit=100")
        self.assertEqual(len(rows), 6)
        self.assertEqual(self.req("GET", f"/desk/api/events?tid={tid}&who=ag_w")[1], [])   # a prefix of the code isn't a worker
        self.assertEqual(self.req("GET", f"/desk/api/kinds?tid={tid}&who=ag_web")[1]["counts"], {"tool": 6})
        self.assertEqual(self.req("GET", f"/desk/api/kinds?tid={tid}&q=" + quote("редкийсловарь"))[1]["counts"], {"tool": 1})
        self.assertEqual(self.req("GET", f"/desk/api/kinds?tid={tid}")[1]["counts"], {"tool": 16})

    def test_log_tail_is_fresh_when_client_fell_far_behind(self):
        config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        path = config.LOGS_DIR / "bot.log"
        self.addCleanup(path.unlink, missing_ok=True)
        path.write_text("".join(f"строка номер {i}\n" for i in range(8000)), encoding="utf-8")
        size = path.stat().st_size
        self.assertGreater(size, 2 * desk.LOG_TAIL)
        _, r = self.req("GET", "/desk/api/log?offset=10")
        self.assertTrue(r["fresh"])   # no way to catch up on almost the whole log piece by piece: show the tail
        self.assertLessEqual(len(r["text"].encode()), desk.LOG_TAIL)
        _, r = self.req("GET", f"/desk/api/log?offset={size - 8}")
        self.assertEqual((r["fresh"], len(r["text"].encode())), (False, 8))

    def test_memory_save_keeps_what_sena_added_meanwhile(self):
        from sena.memory import facts
        facts.replace("## Обо мне\n- старый факт\n")
        seen = facts.read()
        facts.remember("Обо мне", "Сена добавила, пока владелец правил")
        _, r = self.req("POST", "/desk/api/memory", {"text": seen.replace("старый факт", "правка владельца"), "snapshot": seen})
        self.assertIn("правка владельца", r["text"])
        self.assertIn("Сена добавила", r["text"])
        _, r = self.req("POST", "/desk/api/memory", {"text": "## Обо мне\n- только это\n"})   # without a snapshot — as is
        self.assertNotIn("Сена добавила", r["text"])

    def test_person_card_clears_emptied_fields_and_keeps_missing_ones(self):
        from sena.memory import people
        p = people.new_person(name="Тестовый Человек")
        self.req("POST", "/desk/api/person", {"id": p["id"], "relationship": "друг", "opinion": "добрый", "facts": ["любит чай"]})
        self.assertEqual((p["relationship"], p["opinion"]), ("друг", "добрый"))
        self.req("POST", "/desk/api/person", {"id": p["id"], "opinion": ""})   # cleared the opinion; relationship isn't in the request — stays
        self.assertEqual((p["relationship"], p["opinion"], p["name"]), ("друг", "", "Тестовый Человек"))
        self.req("POST", "/desk/api/person", {"id": p["id"], "name": "", "relationship": "  "})
        self.assertEqual((p["name"], p["relationship"], people.facts(p)), ("", "", ["любит чай"]))
        self.assertEqual(self.req("POST", "/desk/api/person", {"id": p["id"], "facts": "не список"})[0], 400)

    def test_people_show_current_facts_as_text_in_both_apps(self):
        from sena.memory import people
        p = people.new_person(name="Оля")
        self.addCleanup(people.delete, p["id"])   # a named card would become a note in the vault other tests count
        people.update(p["id"], facts=["любит чай"])
        people.update(p["id"], facts=["любит чай с мятой"])   # the barer fact retires, it stays only in the card's history
        _, rows = self.req("GET", "/desk/api/people")
        mine = next(r for r in rows if r["id"] == p["id"])
        self.assertEqual(mine["facts"], ["любит чай с мятой"])   # the Mac used to get the fact records and draw empty cards
        self.assertEqual((mine["voices"], mine["created"]), (0, p["created"]))
        self.assertEqual(mine["facts"], next(r for r in data.people_rows() if r["id"] == p["id"])["facts"])   # one source for the phone and the Mac

    def test_person_editor_sends_the_whole_alias_list_and_retires_facts(self):
        from sena.memory import people
        p = people.new_person(name="Вася", alias="Василий")
        self.addCleanup(people.delete, p["id"])
        people.update(p["id"], aliases=["Васёк"], facts=["работает в банке", "живёт в Казани"])
        self.req("POST", "/desk/api/person", {"id": p["id"], "aliases": ["Василий", "Вася-сосед"], "retire": ["живёт в Казани"]})
        self.assertEqual(p["aliases"], ["Василий", "Вася-сосед"])   # the list as the owner left it: "Васёк" removed, a new one added
        self.assertEqual(people.facts(p), ["работает в банке"])
        self.assertIn("живёт в Казани", people.facts(p, all=True))   # a retired fact isn't deleted
        self.req("POST", "/desk/api/person", {"id": p["id"], "facts": ["любит футбол"]})   # no aliases in the request: they stay
        self.assertEqual(p["aliases"], ["Василий", "Вася-сосед"])

    def test_one_memory_line_is_added_edited_and_deleted_without_rewriting_memory(self):
        from sena.memory import facts
        facts.replace("## Обо мне\n- 01.10.2026 10:00: живу в Москве\n- 01.10.2026 10:00: люблю кофе\n")
        out = desk.act("fact_add", {"section": "Обо мне", "text": "бегаю по утрам"})
        self.assertIn("Запомнила", out["result"])
        out = desk.act("fact_edit", {"section": "Обо мне", "old": "01.10.2026 10:00: живу в Москве", "text": "живу в Лиссабоне"})
        self.assertIn("Заменила", out["result"])
        self.assertNotIn("Москве", facts.read())
        self.assertIn("живу в Москве", store.read_text(config.ARCHIVE_FILE))   # the old line keeps its history in the archive
        out = desk.act("fact_delete", {"section": "Обо мне", "text": "01.10.2026 10:00: люблю кофе"})
        self.assertIn("Удалила", out["result"])
        for name, body in (("fact_delete", {"section": "Обо мне", "text": "нет такой строки вовсе"}),
                           ("fact_add", {"section": "Обо мне", "text": "мой пароль от почты qwerty123!"}),
                           ("fact_edit", {"section": "Обо мне", "old": "нет такой строки", "text": "что-то"})):
            self.assertIn("error", desk.act(name, body), name)   # a refusal, and memory isn't touched
        self.assertIn("бегаю по утрам", facts.read())

    def test_secret_refusals_are_errors_and_absent_agents_are_kept(self):
        from sena.core import secrets
        self.assertIn("error", desk.act("secret_put", {"name": "почта", "value": "ab"}))   # too short: a refusal, not "saved"
        self.assertIn("error", desk.act("secret_put", {"name": "плохое имя!", "value": "длинный-пароль-1"}))
        self.assertIn("result", desk.act("secret_put", {"name": "pochta", "value": "длинный-пароль-1", "allow": ["ag_web"]}))
        desk.act("secret_put", {"name": "pochta", "note": "почта на работе"})   # the agents aren't in the request: they stay
        self.assertEqual(next(s for s in secrets.names() if s["name"] == "pochta")["allow"], ["ag_web"])
        desk.act("secret_put", {"name": "pochta", "allow": []})   # an empty list is a choice: no agent
        self.assertEqual(next(s for s in secrets.names() if s["name"] == "pochta")["allow"], [])
        self.assertIn("error", desk.act("secret_remove", {"name": "нет-такого"}))
        secrets.remove("pochta")

    def test_core_is_the_process_running_sena_not_whatever_took_its_number(self):
        config.PID_FILE.write_text(str(os.getppid()), encoding="utf-8")   # a live process that isn't this one
        self.addCleanup(lambda: config.PID_FILE.unlink(missing_ok=True))
        for command, alive in (("/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 -u -m sena", True),
                               ("/Users/x/Desktop/Sena/mac/Sena.app/Contents/MacOS/Sena", False),
                               ("/usr/bin/python3 -u -m sena.phone", False), ("/bin/zsh /Users/x/Desktop/Sena/run.sh", False)):
            desk._seen_core.clear()
            with mock.patch.object(desk.subprocess, "run", return_value=mock.Mock(stdout=command + "\n")):
                self.assertEqual(desk.core()["alive"], alive, command)
        desk._seen_core.clear()
        with mock.patch.object(desk.subprocess, "run", return_value=mock.Mock(stdout="/Applications/Other.app/Contents/MacOS/Other\n")):
            self.assertIn("error", desk.act("core_stop", {}))   # nothing to stop, and the stale pid file is cleared, its number never signalled
        self.assertFalse(config.PID_FILE.exists())
        desk._seen_core.clear()

    def test_start_is_refused_while_a_core_is_coming_up(self):
        from sena.core import power
        self.addCleanup(config.STARTING_FILE.unlink, missing_ok=True)
        config.PID_FILE.unlink(missing_ok=True)
        self.assertIn("уже запускается", desk.act("core_start", {})["error"])   # this process isn't a screen: it is a core on its way up
        with mock.patch.object(power, "is_off", return_value=True), mock.patch.object(desk.subprocess, "Popen") as popen, \
                mock.patch.object(desk.threading, "Timer"), mock.patch.object(desk, "_missing_keys", return_value=[]):
            store.write_text(config.STARTING_FILE, str(time.time() - 5))   # Start was pressed a moment ago
            self.assertIn("уже запускается", desk.act("core_start", {})["error"])
            popen.assert_not_called()   # no second run.sh
            store.write_text(config.STARTING_FILE, str(time.time() - desk.STARTING_FOR - 1))   # a marker left from long ago
            self.assertTrue(desk.act("core_start", {})["handover"])
            popen.assert_called_once()
            self.assertEqual(popen.call_args.kwargs["umask"], config.OWNER_UMASK)   # run.sh and the core get the owner's mask, not the screen's 077

    def test_openai_is_not_used_unless_a_role_needs_it_and_counters_are_reused(self):
        from sena.core import roles
        self.addCleanup(settings.update, {"roles": {}})
        desk._tallies.clear()
        view = desk.integrations_view()
        self.assertFalse(view["openai"]["needed"])   # Claude thinks, fingerprints are counted at home: no key is not a fault
        self.assertTrue(view["claude_api"]["needed"])
        self.assertEqual(desk.config_view()["models"]["embed"], config.LOCAL_EMBED)   # the model counting fingerprints now
        roles.move("embed", "cloud")
        self.assertTrue(desk.integrations_view()["openai"]["needed"])
        self.assertEqual((desk.config_view()["models"]["embed"], desk.config_view()["models"]["embed_place"]), (config.MODEL_EMBED, "cloud"))
        with mock.patch.object(desk.events, "tally", return_value=(0, None)) as tally:
            desk.integrations_view()
        tally.assert_not_called()   # the counters from a moment ago are reused: a "last use" walks the journal back
        desk._tallies.clear()

    def test_the_phone_counts_its_live_voice(self):
        desk._tallies.clear()
        before = desk.integrations_view()["phone"]["uses_today"]   # other tests' phone voices are in today's journal too
        desk._tallies.clear()
        events.log("voice", "разговор начат", device="phone")
        events.log("voice", "разговор начат", device="mac")
        events.log("app", "/voice", source="app")   # the Mac's voice message: not the phone's
        self.assertEqual(desk.integrations_view()["phone"]["uses_today"] - before, 1)
        desk._tallies.clear()

    def test_a_long_confirmed_action_goes_on_behind_the_press(self):
        from sena.agents.ag_sena import tools as sena_tools
        from sena.memory import history
        from sena.phone import panels
        gate = threading.Event()
        self.addCleanup(gate.set)
        sid = sena_tools._hold("поручение ag_web", "найти билеты", lambda: gate.wait(5) and "Билеты найдены")
        with mock.patch.object(panels, "PRESS_WAIT", 0.05):
            out = desk.act("confirm_send", {"id": sid})
        self.assertIn("результат придёт в переписку", out["result"])   # answered at once, not cut by the app's request timeout
        gate.set()
        for _ in range(100):
            if any("Билеты найдены" in m["content"] for m in history.stored(config.OWNER_ID)[-3:]):
                break
            time.sleep(0.02)
        self.assertTrue(any("Билеты найдены" in m["content"] for m in history.stored(config.OWNER_ID)[-3:]))   # the result reaches the chat
        self.assertIn("error", desk.act("confirm_send", {"id": sid}))   # pressed already: nothing waits any more

    def _change(self, **extra):
        change = {"id": "chg1", "branch": "sena/auto-chg1", "base": "abc", "task": "проверка", "created": time.time(), "tests": True, "diff": "", "report": "", **extra}
        ag_code._record(change)
        self.addCleanup(lambda: ag_code.CHANGES_FILE.unlink(missing_ok=True))
        return change

    def test_change_refusals_are_errors_and_success_is_a_result(self):
        self._change(tests=False)
        out = desk.act("change_apply", {"id": "chg1"})
        self.assertIn("не прошла тесты", out["error"])   # a refusal is {"error"}, not a success with the refusal text
        self.assertNotIn("result", out)
        self.assertIn("error", desk.act("change_apply", {"id": "нет такой"}))
        self.assertIn("error", desk.act("change_drop", {"id": "нет такой"}))
        ag_code._record({**ag_code.changes()[-1], "tests": True})
        with mock.patch.object(ag_code, "_git", return_value="M файл.py"):   # the working tree has someone else's changes
            self.assertIn("незакоммиченные", desk.act("change_apply", {"id": "chg1"})["error"])

        def fake_git(args, check=True):
            return "" if args[0] == "status" else "deadbeef0000"
        self.addCleanup(ag_code.PENDING_FILE.unlink, missing_ok=True)
        self.addCleanup(config.STARTING_FILE.unlink, missing_ok=True)
        with mock.patch.object(ag_code, "_git", side_effect=fake_git), mock.patch.object(ag_code, "_restart"):
            ok = desk.act("change_apply", {"id": "chg1"})
        self.assertIn("перезапускаюсь", ok["result"])
        self.assertTrue(config.STARTING_FILE.exists())   # the app knows the core is about to restart
        self._change(id="chg2", branch="sena/auto-chg2")
        with mock.patch.object(ag_code, "_git", return_value=""):
            self.assertIn("отклонена", desk.act("change_drop", {"id": "chg2"})["result"])
        self.assertEqual([c["id"] for c in ag_code.changes()], ["chg1"])

    def test_changes_say_why_they_cannot_be_applied_and_what_to_look_at(self):
        self._change(tests=True, git_touched=True, tests_changed=["tests/test_x.py"])
        self._change(id="chg2", branch="sena/auto-chg2", tests=True, loosened=["assertTrue убран"])
        with mock.patch.object(desk, "_git", return_value=""):
            rows = {c["id"]: c for c in desk.changes_view()["changes"]}
        self.assertIn("не принята", rows["chg1"]["refused"])   # the Mac used to call it "ready" and offer "apply"
        self.assertTrue(any("изменил существующие тесты" in n for n in rows["chg1"]["notes"]))
        self.assertIsNone(rows["chg2"]["refused"])
        self.assertTrue(any("ослабление проверок" in n for n in rows["chg2"]["notes"]))   # the Telegram report's warnings, now in the app too

    def test_manual_backup_is_made_even_when_tonight_s_copy_exists(self):
        made = []
        with mock.patch.object(ag_night, "backup_memory", lambda force=False: made.append(force)):
            self.assertIn("запущено", desk.act("backup", {})["result"])
            for _ in range(100):
                if made and "Копия памяти" not in scheduler.busy():
                    break
                time.sleep(0.02)
        self.assertEqual(made, [True])

    def test_git_banner_ignores_untracked_files_and_cache_is_reset_by_actions(self):
        self.addCleanup(cancel.cancelled.clear)   # stop_all below marks real tasks — don't leave that for other tests
        asked = []
        with mock.patch.object(desk, "_git", side_effect=lambda *a: asked.append(a) or ""):
            desk._dirty()
        self.assertEqual(asked, [("status", "--porcelain", "--untracked-files=no")])
        desk._git_cache["x"] = (time.time(), "старый ответ")
        desk.act("stop_all", {})
        self.assertEqual(desk._git_cache, {})   # the action could have changed code or branches

    def test_a_running_task_is_stopped_by_its_own_id(self):
        self.addCleanup(cancel.cancelled.clear)
        status.begin("задача, которую остановят по её номеру")
        try:
            tid = status.current()["id"]
            self.assertEqual(desk.act("stop_task", {"id": tid})["result"], "Задача остановлена")   # the tid both apps show, not only the thread
            self.assertIn(tid, cancel.cancelled)
        finally:
            status.end("")
        self.assertIn("уже закончилась", desk.act("stop_task", {"id": "ab12cd34"})["result"])
        for bad in ("", "../x", "a b"):
            with self.assertRaises(desk.ApiError):
                desk.act("stop_task", {"id": bad})

    def test_active_tasks_carry_service_mark(self):
        status.begin("служебная уборка", service=True)
        try:
            _, d = self.req("GET", "/desk/api/tasks")
            mine = next(t for t in d["active"] if t["text"] == "служебная уборка")
            self.assertIs(mine["service"], True)
        finally:
            status.end("")

    def test_active_task_carries_the_agent_s_own_next_step_guess(self):
        status.begin("задача с планом")
        try:
            status.show("шаг один", upcoming="шаг два")
            _, d = self.req("GET", "/desk/api/tasks")
            mine = next(t for t in d["active"] if t["text"] == "задача с планом")
            self.assertEqual(mine["next"], "шаг два")
            status.show("шаг два")   # no further guess: the hint clears instead of going stale
            _, d = self.req("GET", "/desk/api/tasks")
            mine = next(t for t in d["active"] if t["text"] == "задача с планом")
            self.assertIsNone(mine["next"])
        finally:
            status.end("")

    def test_manual_background_run_is_single_and_marks_busy(self):
        gate, started = threading.Event(), []
        self.addCleanup(gate.set)

        def tidy():
            started.append(1)
            gate.wait(5)
        with mock.patch.object(ag_night, "tidy", tidy):
            first = desk.act("tidy", {})
            second = desk.act("tidy", {})   # double click
            self.assertIn("запущено", first["result"])
            self.assertIn("уже идёт", second["error"])
            self.assertIn("Ночная уборка", self.req("GET", "/desk/api/overview")[1]["scheduler"]["busy"])   # the "running now" indicator lights up
            gate.set()
            for _ in range(100):
                if "Ночная уборка" not in scheduler.busy():
                    break
                time.sleep(0.02)
        self.assertEqual(len(started), 1)
        self.assertNotIn("Ночная уборка", scheduler.busy())

    def test_embed_does_not_run_twice_at_once(self):
        gate, calls = threading.Event(), []
        self.addCleanup(gate.set)

        def slow(*a, **k):
            calls.append(1)
            gate.wait(5)
            return 3, None
        with mock.patch.object(index, "embed_pending", slow):
            first = []
            t = threading.Thread(target=lambda: first.append(desk.act("embed", {})))
            t.start()
            for _ in range(100):
                if calls:
                    break
                time.sleep(0.02)
            self.assertIn("уже идёт", desk.act("embed", {})["error"])
            gate.set()
            t.join(5)
        self.assertEqual(first[0]["result"], "Отпечатков досчитано: 3")
        self.assertEqual(len(calls), 1)

    def test_settings_report_what_was_rejected(self):
        before = settings.load()
        self.addCleanup(settings.update, {k: before[k] for k in settings.DEFAULTS})
        out = desk.act("settings", {"active_from": 20, "active_to": 10, "weekly_hour": 99, "nonsense": 1, "voice": ["x"], "tidy_hour": 5})
        self.assertEqual(sorted(out["rejected"]), ["active_from", "active_to", "nonsense", "voice", "weekly_hour"])
        self.assertEqual(out["changed"], {"tidy_hour": 5} if before["tidy_hour"] != 5 else {})
        self.assertEqual((out["settings"]["active_from"], out["settings"]["active_to"]), (before["active_from"], before["active_to"]))
        ok = desk.act("settings", {"active_from": 8, "active_to": 22})
        self.assertEqual((ok["rejected"], ok["settings"]["active_from"], ok["settings"]["active_to"]), ([], 8, 22))
        late = desk.act("settings", {"active_from": 23})   # "from" is not less than the current "to"
        self.assertEqual((late["rejected"], late["settings"]["active_from"]), (["active_from"], 8))

    def test_restart_marks_core_as_starting_and_core_survives_vanishing_pid_file(self):
        self.addCleanup(config.STARTING_FILE.unlink, missing_ok=True)
        with mock.patch.object(desk, "core", return_value={"is_core": True}), mock.patch.object(desk.threading, "Timer"):
            out = desk.act("restart", {})
        self.assertTrue(out["handover"])
        self.assertTrue(config.STARTING_FILE.exists())   # otherwise the app would raise a second screen after 4 s
        fake = mock.Mock()
        fake.read_text.return_value = str(os.getpid())
        fake.stat.side_effect = FileNotFoundError
        with mock.patch.object(config, "PID_FILE", fake):
            c = desk.core()   # the file vanished between the check and reading its time
        self.assertEqual((c["alive"], c["started"]), (True, None))

    def test_open_refuses_programs_but_reveals_them(self):
        script = attachments.save(b"echo hi", "run.command")
        page = attachments.save(b"<script>1</script>", "page.html")   # html isn't a preview, it's a page with code
        text = attachments.save(b"hi", "note.txt")
        ran = attachments.save(b"hi", "exec.txt")
        os.chmod(ran, 0o755)
        for f in (script, page, ran):
            out = desk.act("open_file", {"path": str(f)})
            self.assertIn("я из приложения не открываю", out["error"])
        with mock.patch.object(desk.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            self.assertIn("Открываю", desk.act("open_file", {"path": str(text)})["result"])
            self.assertEqual(run.call_args[0][0][0], "open")
            self.assertIn("Показываю", desk.act("reveal", {"path": str(script)})["result"])   # any file can be revealed in Finder
        self.assertEqual(self.req("POST", "/desk/api/open_file", {"path": "/bin/sh"})[0], 403)

    def test_one_event_per_action_and_failures_are_logged(self):
        def mine(text):
            return [e for e in events.search("today", kind="app") if e["text"] == text]
        before = len(mine("приложение: memory"))
        desk.act("memory", {"text": "## Обо мне\n- одна запись\n"})
        rows = mine("приложение: memory")
        self.assertEqual(len(rows) - before, 1)   # previously the action wrote two identical events
        self.assertEqual(rows[-1]["size"], len("## Обо мне\n- одна запись\n"))
        self.assertNotIn("_log", rows[-1])
        out = desk.act("integration", {"id": "openai", "on": False})
        self.assertIn("error", out)
        failed = mine("приложение: integration")
        self.assertTrue(failed and failed[-1]["failed"] is True)
        self.assertNotIn("_log", json.dumps(desk.act("settings", {"tidy_hour": 3})))
        self.addCleanup(settings.update, {"tidy_hour": settings.DEFAULTS["tidy_hour"]})

    def test_settings_integer_values_are_integers_and_stamped_names_do_not_collide(self):
        out = desk.act("settings", {"weekly_hour": 20.5, "tidy_hour": 3.0})
        self.assertIn("weekly_hour", out["rejected"])                  # the hour is an integer: the scheduler won't digest 20.5
        self.assertEqual(out["settings"]["tidy_hour"], 3)
        self.assertIsInstance(out["settings"]["tidy_hour"], int)       # 3.0 from the phone is stored as 3
        self.addCleanup(settings.update, {"tidy_hour": settings.DEFAULTS["tidy_hour"]})
        first = attachments.save(b"1", "same.png")
        second = attachments.save(b"2", "same.png")
        self.assertNotEqual(first, second)                              # the same name within one second
        long_one = attachments.save(b"3", "я" * 300 + ".png")
        self.assertTrue(long_one.exists() and long_one.suffix == ".png")  # a long name doesn't break the write

    def test_refusals_come_back_as_errors_not_as_green_results(self):
        for name, body in (("rule_remove", {"id": "нет-такого"}), ("person", {"id": "нет-такого", "name": "x"}), ("skill_delete", {"name": "нет-такого-навыка"}),
                           ("outcome", {"id": "нет-такой", "verdict": "done"}), ("rate", {"id": "нет-такой", "rating": 5}), ("confirm_send", {"id": "нет-такой"})):
            out = desk.act(name, body)
            self.assertIn("error", out, name)   # previously "Task not found" was shown as a success
            self.assertNotIn("result", out, name)

    def test_rate_action_stores_tema_s_own_score_separately_from_the_judge_s_verdict(self):
        entry = task_log.save({"text": "оценка: проверка", "start": time.time(), "steps": [], "answer": "готово"})
        out = desk.act("rate", {"id": entry["id"], "rating": 9})   # out of range: clamped, not rejected
        self.assertIn("result", out)
        stored = next(e for e in task_log.items if e["id"] == entry["id"])
        self.assertEqual(stored["rating"]["value"], 5)
        self.assertNotIn("outcome", stored)   # the judge's own verdict is untouched by Tema's manual score

    def test_note_that_is_not_utf8_does_not_break_the_notes_list(self):
        folder = vault.root() / "Проверка"
        folder.mkdir(parents=True, exist_ok=True)
        bad = folder / "cp1251.md"
        bad.write_bytes("заметка".encode("cp1251"))
        self.addCleanup(bad.unlink, missing_ok=True)
        out = desk.get("notes", {})
        self.assertTrue(isinstance(out, (list, dict)))                  # previously a UnicodeDecodeError gave a 500 for the whole section
        self.assertIn("text", data.note("Проверка/cp1251.md"))

    def test_notes_folder_cannot_leave_the_vault(self):
        import tempfile
        from sena.memory import vault
        vault.ensure()
        vault.write_note("Идеи/Внутри", "# Внутри\n\nтекст", "create")
        with tempfile.TemporaryDirectory() as outside:
            (Path(outside) / "чужая.md").write_text("# чужая", encoding="utf-8")
            for folder in (outside, "..", "../..", "Идеи/../..", ".obsidian"):
                code, view = self.req("GET", "/desk/api/notes?folder=" + quote(folder, safe=""))
                self.assertEqual((code, view["notes"]), (200, []), folder)   # previously an absolute path gave a 500 with a foreign file's name
        code, view = self.req("GET", "/desk/api/notes?folder=" + quote("Идеи", safe=""))
        self.assertIn("Идеи/Внутри.md", [r["path"] for r in view["notes"]])
        self.assertTrue(all(r["path"].startswith("Идеи/") for r in view["notes"]))
        self.assertEqual(self.req("POST", "/desk/api/note_write", {"path": "../x", "text": "a"})[0], 400)
        out = self.req("POST", "/desk/api/note_write", {"path": "Идеи/Внутри", "text": "ещё", "mode": "create"})[1]
        self.assertIn("уже есть", out["error"])   # a write refusal is an error, not a "result"


class WhatTheAppsAskFor(unittest.TestCase):
    """Round two of the apps' audit: refusals that read as success, a start that lost the phone its server, replies far bigger
    than the screens read."""

    def test_a_start_without_the_keys_it_needs_is_refused_and_the_screen_stays(self):
        from sena.core import power
        config.PID_FILE.unlink(missing_ok=True)
        self.addCleanup(config.STARTING_FILE.unlink, missing_ok=True)
        config.STARTING_FILE.unlink(missing_ok=True)
        with mock.patch.object(power, "is_off", return_value=True), mock.patch.object(desk.subprocess, "Popen") as popen, \
                mock.patch.object(desk.threading, "Timer") as timer:
            out = desk.act("core_start", {})   # the tests' .env is empty: the core would stop at once with exit 78
        self.assertIn("не хватает", out["error"])
        self.assertIn("TELEGRAM_TOKEN", out["error"])
        popen.assert_not_called()
        timer.assert_not_called()   # this screen doesn't exit: the phone keeps its server and reads why
        self.assertFalse(config.STARTING_FILE.exists())   # and the app doesn't wait a minute for a core that never comes

    def test_missing_keys_are_read_from_the_env_file_as_it_is_now(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            env = Path(folder) / ".env"
            env.write_text("".join(f"{k}=x{i}\n" for i, k in enumerate(config.REQUIRED_KEYS[1:])) + f"{config.REQUIRED_KEYS[0]}=\n", encoding="utf-8")
            with mock.patch.object(config, "ENV_FILE", env):
                self.assertEqual(desk._missing_keys({}), [config.REQUIRED_KEYS[0]])   # an empty value is a missing key
                self.assertEqual(desk._missing_keys({config.REQUIRED_KEYS[0]: "из окружения"}), [])   # what run.sh hands on counts too

    def test_removing_a_job_or_an_alert_that_is_gone_is_an_error(self):
        for name in ("job_remove", "alert_remove"):
            out = desk.act(name, {"id": "нет-такого"})
            self.assertIn("error", out, name)   # was a green toast «Нет такого задания»
            self.assertNotIn("result", out, name)

    def test_an_error_counts_for_the_integration_it_names(self):
        def last(name):
            return desk.integrations_view()[name]["last_error"] or ""
        events.error("Claude Code не закончил", RuntimeError("метка-кода"))
        self.assertIn("метка-кода", last("claude"))
        self.assertNotIn("метка-кода", last("claude_api"))   # «Claude Code …» was marked on the brain's row too
        events.error("Claude: рассуждение привязано к другой версии переписки", RuntimeError("метка-мозга"))
        self.assertIn("метка-мозга", last("claude_api"))
        self.assertNotIn("метка-мозга", last("claude"))
        events.error("Вход в аккаунт Телеграма", RuntimeError("метка-аккаунта"))
        self.assertIn("метка-аккаунта", last("telegram_account"))
        self.assertNotIn("метка-аккаунта", last("telegram_bot"))
        events.error("Маршрут Google Maps", RuntimeError("метка-карт"))
        self.assertNotIn("метка-карт", last("google"))

    def test_a_secret_note_is_kept_when_absent_and_cleared_when_emptied(self):
        from sena.core import secrets
        self.addCleanup(secrets.remove, "note-test")
        self.assertIn("result", desk.act("secret_put", {"name": "note-test", "value": "длинный-пароль-2", "note": "почта на работе"}))
        desk.act("secret_put", {"name": "note-test", "allow": []})   # no note in the request: it stays
        self.assertEqual(next(s for s in secrets.names() if s["name"] == "note-test")["note"], "почта на работе")
        desk.act("secret_put", {"name": "note-test", "note": ""})   # the owner emptied the field: cleared, not «обновлён» over the old one
        self.assertEqual(next(s for s in secrets.names() if s["name"] == "note-test")["note"], "")

    def test_the_owner_rewrites_a_skill_from_the_app(self):
        from sena.memory import skills
        self.addCleanup(skills.delete_skill, "owner-rewrite")
        desk.act("skill_write", {"name": "owner-rewrite", "when": "когда завариваю чай", "recipe": "вскипятить воду, насыпать чай, ждать пять минут, процедить"})
        out = desk.act("skill_write", {"name": "owner-rewrite", "when": "когда завариваю чай", "recipe": "кофе вместо чая: смолоть зёрна, турка на огонь"})
        self.assertIn("сохранён", out.get("result", ""))   # was «Не сохранено … Поправь точечно (patch_skill)» — a tool he doesn't have
        self.assertIn("турка", skills.text("owner-rewrite"))

    def test_a_new_person_keeps_every_other_name(self):
        from sena.memory import people
        out = desk.act("person_add", {"name": "Ольга Проверкина", "alias": "Оля", "aliases": ["Оля", "Олечка"], "facts": ["любит чай"]})
        self.addCleanup(people.delete, out["id"])
        p = people.by_id(out["id"])
        self.assertEqual(p["aliases"], ["Оля", "Олечка"])   # «через запятую» kept only the first one
        self.assertEqual(people.facts(p), ["любит чай"])

    def test_task_lists_can_be_slim_and_say_when_nothing_changed(self):
        rows = [{"id": "t1", "text": "задача", "start": 1.0, "end": 2.0, "steps": [{"who": "ag_sena", "text": "шаг", "time": 1.0}],
                 "cards": [{"asked": "найти"}], "answer": "ответ", "comments": [], "service": False}]
        with mock.patch.object(desk.data, "finished_tasks", return_value=rows), mock.patch.object(desk, "_active", return_value=[]):
            full = desk.tasks({"limit": "5"})
            slim = desk.tasks({"limit": "5", "slim": "1"})
            again = desk.tasks({"limit": "5", "slim": "1", "same": slim["sig"]})
            rows.append({**rows[0], "id": "t2", "text": "ещё задача"})
            changed = desk.tasks({"limit": "5", "slim": "1", "same": slim["sig"]})
        self.assertIn("steps", full["finished"][0])   # the diagram draws a finished task's stages
        self.assertFalse({"steps", "cards"} & set(slim["finished"][0]))   # the lists read neither: the task's page brings them
        self.assertEqual(slim["finished"][0]["answer"], "ответ")   # the Mac's search looks in answers
        self.assertEqual((again["same"], again["sig"]), (True, slim["sig"]))
        self.assertNotIn("finished", again)   # a few bytes instead of the whole list
        self.assertNotEqual(changed["sig"], slim["sig"])
        self.assertEqual([t["id"] for t in changed["finished"]], ["t1", "t2"])

    def test_the_conversation_says_when_nothing_changed(self):
        from sena.memory import history
        rows = [{"id": "m1", "role": "user", "content": "привет", "time": "2026-10-26T05:00:00"}]
        with mock.patch.object(history, "stored", return_value=rows), mock.patch.object(history, "summary", return_value=""):
            first = desk.get("history", {"limit": "300"})
            again = desk.get("history", {"limit": "300", "same": first["sig"]})
        self.assertEqual(first["messages"], rows)
        self.assertEqual(again, {"same": True, "sig": first["sig"]})

    def test_memory_reads_only_the_parts_asked_for(self):
        with mock.patch.object(desk.index, "stats") as stats, mock.patch.object(desk.store, "read_text", wraps=store.read_text) as read:
            view = desk.memory_view({"fields": "sections"})
        self.assertEqual(set(view), {"sections"})
        stats.assert_not_called()   # the registry's counts are taken under its lock: the facts tab doesn't need them every 15 s
        self.assertNotIn(config.ARCHIVE_FILE, [c.args[0] for c in read.call_args_list])   # nor the archive, which only grows
        self.assertEqual(set(desk.memory_view({"fields": "index"})), {"index"})
        self.assertEqual(set(desk.memory_view({"fields": "нет-такого"})), set(desk.MEMORY_PARTS))   # nothing known asked: the whole view, as before
        self.assertEqual(set(desk.memory_view()), set(desk.MEMORY_PARTS))

    def test_files_show_their_own_names(self):
        for folder, name, shown in ((config.INBOX_DIR, "20261025-отчёт.pdf", "20261025-отчёт.pdf"), (config.DOWNLOADS_DIR, "mail-1a2b-счёт.pdf", "счёт.pdf")):
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / name
            path.write_bytes(b"%PDF")
            self.addCleanup(path.unlink, missing_ok=True)
            row = next(r for r in desk.files_view({}) if r["path"] == str(path))
            self.assertEqual(row["name"], shown)   # a dated name of his own stays whole, a mail's prefix goes (attachments.shown_name)

    def test_stop_all_from_the_apps_stops_a_long_work_s_turn_too(self):
        self.addCleanup(cancel.cancelled.clear)
        started, release, turn = threading.Event(), threading.Event(), {}

        def work_turn():
            status.begin("Работа «Проверка стопа»: ход 1", service=True)   # as agent.work_turn begins one
            status.current()["work"] = "w-stop-test"
            turn["tid"] = status.current()["id"]
            started.set()
            release.wait(5)
            status.end("")
        thread = threading.Thread(target=work_turn)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(release.set)
        self.assertTrue(started.wait(5))
        out = desk.act("stop_all", {})
        self.assertIn("остановлены", out["result"])
        self.assertIn(turn["tid"], cancel.cancelled)   # the apps' «Стоп» is his «стоп» in chat: the work's turn stops, the work pauses

    def test_the_views_carry_no_field_no_app_reads(self):
        self.assertNotIn("labels", desk.kinds({}))
        self.assertNotIn("screen_started", desk.core())
        self.assertNotIn("flags", desk.config_view())
        home = desk.home_view()
        self.assertNotIn("graduate", home["learning"])
        self.assertNotIn("home_seconds", home["today"])


if __name__ == "__main__":
    unittest.main()