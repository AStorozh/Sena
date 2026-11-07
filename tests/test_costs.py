"""Cost accounting: price tables, recording every paid call, reports, recosting. The network is replaced with stubs."""
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sena import config
from sena.core import costs, llm, roles, status
from sena.google import media
from sena.core.tools import tool


def usage(**kw):
    return SimpleNamespace(**kw)


def reply(model="gpt-5.4", input=0, cached=0, output=0, tier="default", web=0):
    out = [SimpleNamespace(type="web_search_call") for _ in range(web)] + [SimpleNamespace(type="message")]
    return SimpleNamespace(model=model, service_tier=tier, output=out, output_text="ok", id="r",
                           usage=usage(input_tokens=input, output_tokens=output, input_tokens_details=usage(cached_tokens=cached)))


class Isolated(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patch = mock.patch.object(config, "JOURNAL_DIR", Path(self.dir.name))
        patch.start()
        self.addCleanup(patch.stop)
        costs._reset()
        self.addCleanup(costs._reset)

    def rows(self):
        return costs._all_rows()


class Pricing(Isolated):
    def test_cached_input_is_cheaper_and_snapshot_name_finds_the_model(self):
        usd = costs.openai_response("gpt-5.4", reply(model="gpt-5.4-2026-03-05", input=1000, cached=400, output=200))
        self.assertAlmostEqual(usd, (600 * 2.5 + 400 * 0.25 + 200 * 15) / 1e6)
        mini = costs.openai_response("gpt-5.4-mini", reply(model="gpt-5.4-mini-2026-03-17", input=1000, output=1000))
        self.assertAlmostEqual(mini, (1000 * 0.75 + 1000 * 4.5) / 1e6)   # mini, not gpt-5.4: the longest matching name wins

    def test_long_context_uses_long_prices_for_the_whole_request(self):
        usd = costs.openai_response("gpt-5.4", reply(input=300_000, output=1000))
        self.assertAlmostEqual(usd, (300_000 * 5.0 + 1000 * 22.5) / 1e6)

    def test_haiku_above_100k_prompt_tokens_is_billed_whole_at_the_higher_card(self):
        """The Claude docs: Haiku 5.5 is $0.10/$0.50 up to 100K prompt tokens, $0.50/$2.50 above — the prompt counts cache too."""
        usage = lambda inp, read: SimpleNamespace(input_tokens=inp, cache_read_input_tokens=read, cache_creation_input_tokens=0, output_tokens=1000,  # noqa: E731
                                                  server_tool_use=None)
        small = costs.claude_response(SimpleNamespace(model="claude-haiku-5-5", raw_usage=usage(50_000, 40_000)))
        self.assertAlmostEqual(small, (50_000 * 0.10 + 40_000 * 0.01 + 1000 * 0.50) / 1e6)
        big = costs.claude_response(SimpleNamespace(model="claude-haiku-5-5", raw_usage=usage(60_000, 50_000)))
        self.assertAlmostEqual(big, (60_000 * 0.50 + 50_000 * 0.05 + 1000 * 2.50) / 1e6)

    def test_every_request_of_a_paused_turn_is_billed(self):
        """LiteLLM's study: a turn paused by the built-in search is several requests; only the last one used to be priced."""
        first = SimpleNamespace(input_tokens=1000, cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=100, server_tool_use=SimpleNamespace(web_search_requests=1))
        last = SimpleNamespace(input_tokens=2000, cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=200, server_tool_use=None)
        usd = costs.claude_response(SimpleNamespace(model="claude-opus-5-5", calls=[("claude-opus-5-5", first), ("claude-opus-5-5", last)]))
        self.assertAlmostEqual(usd, (3000 * 4.0 + 300 * 20.0) / 1e6 + costs.WEB_SEARCH_PER_CALL)
        self.assertEqual(len(self.rows()), 2)

    def test_web_search_is_ten_dollars_per_thousand_calls_and_priority_doubles_tokens_only(self):
        usd = costs.openai_response("gpt-5.4-mini", reply(model="gpt-5.4-mini", input=1_000_000, web=3, tier="priority"))
        self.assertAlmostEqual(usd, 0.75 * 2 + 3 * 0.01)

    def test_unknown_model_is_unpriced_not_zero(self):
        self.assertIsNone(costs.openai_response("gpt-9-mystery", reply(model="gpt-9-mystery", input=500, output=500)))
        data = costs.summary("today")
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["unpriced"], {"openai/gpt-9-mystery": 1})
        self.assertIn("Без цены", costs.render(data))

    def test_embeddings_and_old_openai_voice_rows(self):
        e = costs.openai_embeddings("text-embedding-3-small", SimpleNamespace(model="text-embedding-3-small", usage=usage(prompt_tokens=7, total_tokens=7)))
        self.assertAlmostEqual(e, 7 * 0.02 / 1e6)
        # Sena no longer calls OpenAI's voice and hearing, but their old journal rows are still priced (recost)
        s, estimated = costs.price("openai", "speech", "gpt-4o-mini-tts", {"seconds": 3.36, "chars": 37})
        self.assertAlmostEqual(s, 3.36 / 60 * 0.015 + 37 / 4 * 0.6 / 1e6, places=7)
        self.assertTrue(estimated)
        t, _ = costs.price("openai", "transcribe", "gpt-4o-transcribe", {"audio": 33, "output": 11})
        self.assertAlmostEqual(t, (33 * 6.0 + 11 * 10.0) / 1e6)   # about $0.0055 per minute of audio
        d, _ = costs.price("openai", "transcribe", "gpt-4o-transcribe", {"seconds": 120})
        self.assertAlmostEqual(d, 2 * 0.006)

    def test_gemini_image_splits_image_tokens_from_text(self):
        # a real nano banana lite (1K) response: 11 input tokens, 1421 output, of which 1120 is the image
        response = {"usage": {"total_input_tokens": 11, "total_output_tokens": 1421, "total_thought_tokens": 0,
                              "output_tokens_by_modality": [{"modality": "image", "tokens": 1120}]}, "service_tier": "standard"}
        usd = costs.gemini("gemini-3.1-flash-lite-image", response, "image")
        self.assertAlmostEqual(usd, (11 * 0.25 + 301 * 1.5 + 1120 * 30.0) / 1e6)
        self.assertGreater(usd, 0.0336)   # a bit more than the bare image price, for the text

    def test_gemini_video_by_tokens_and_by_seconds_when_tokens_are_missing(self):
        per_second = costs.GEMINI_VIDEO_TOKENS_PER_SECOND
        tokens = costs.gemini("gemini-omni-1.1-flash", {"usage": {"total_output_tokens": per_second * 3,
                              "output_tokens_by_modality": [{"modality": "video", "tokens": per_second * 3}]}}, "video")
        self.assertAlmostEqual(tokens, per_second * 3 * 17.5 / 1e6)   # about $0.30 for 3 seconds
        by_seconds = costs.gemini("gemini-omni-1.1-flash", {"usage": {}}, "video", video_seconds=3)
        self.assertAlmostEqual(by_seconds, per_second * 3 * 17.5 / 1e6)
        self.assertTrue(costs.summary("today")["estimated"])


class RealSamples(Isolated):
    """Numbers from real responses (15.10.2026), so the format and the math don't drift from reality."""

    def test_real_video_response_is_priced_close_to_the_published_ten_cents_a_second(self):
        # a 3-second 720p clip: 17,376 video tokens = 5792 * 3, plus 422 unlabeled and 145 for thinking
        response = {"usage": {"total_tokens": 17961, "total_input_tokens": 18, "total_output_tokens": 17798, "total_thought_tokens": 145,
                              "output_tokens_by_modality": [{"modality": "video", "tokens": 17376}]}, "service_tier": "standard"}
        usd = costs.gemini("gemini-omni-1.1-flash", response, "video")
        official = 3 * 0.10   # "about 10 cents a second"
        self.assertLess(abs(usd - official) / official, 0.05)
        self.assertGreaterEqual(usd, 17376 * 17.5 / 1e6)   # at least the price of the video tokens alone
        self.assertTrue(self.rows()[0]["estimate"])

    def test_real_image_response(self):
        response = {"usage": {"total_tokens": 1432, "total_input_tokens": 11, "total_output_tokens": 1421,
                              "output_tokens_by_modality": [{"modality": "image", "tokens": 1120}]}, "service_tier": "standard"}
        usd = costs.gemini("gemini-3.1-flash-lite-image", response, "image")
        self.assertAlmostEqual(usd, 0.0336 + 301 * 1.5 / 1e6 + 11 * 0.25 / 1e6, places=7)
        self.assertFalse(self.rows()[0]["estimate"])   # image verified live: the figure is exact

    def test_real_openai_usage_shapes(self):
        # Responses API: details also has cache_write_tokens (it's zero and unbilled for gpt-5.4)
        r = SimpleNamespace(model="gpt-5.4-mini-2026-03-17", service_tier="default", output=[SimpleNamespace(type="message")], usage=SimpleNamespace(
            input_tokens=13, output_tokens=5, input_tokens_details=SimpleNamespace(cache_write_tokens=0, cached_tokens=0),
            output_tokens_details=SimpleNamespace(reasoning_tokens=0), total_tokens=18))
        self.assertAlmostEqual(costs.openai_response("gpt-5.4-mini", r), (13 * 0.75 + 5 * 4.5) / 1e6)

    def test_cache_writes_of_newer_models_are_paid_at_their_own_rate(self):
        r = SimpleNamespace(model="gpt-5.6-sol", service_tier="default", output=[], usage=SimpleNamespace(
            input_tokens=1000, output_tokens=100, input_tokens_details=SimpleNamespace(cached_tokens=200, cache_write_tokens=400)))
        usd = costs.openai_response("gpt-5.6-sol", r)
        self.assertAlmostEqual(usd, (400 * 4.0 + 200 * 0.4 + 400 * 5.0 + 100 * 20.0) / 1e6)


class Recording(Isolated):
    def test_failure_of_accounting_never_breaks_the_caller(self):
        with mock.patch.object(costs, "_append", side_effect=OSError("диск полон")):
            self.assertIsNone(costs.request("tomtom", "search"))
        self.assertTrue(costs.errors)
        costs._reset()
        self.assertIsNone(costs.openai_response("gpt-5.4", SimpleNamespace()))   # a response with no usage: "unpriced", not zero
        self.assertEqual(costs.summary("today")["unpriced"], {"openai/gpt-5.4": 1})

    def test_who_and_task_are_recorded_and_label_attaches_to_the_same_task(self):
        status.begin("найти кофейню рядом")
        status.local.who = "ag_web"
        try:
            task_id = status.current()["id"]
            costs.openai_response("gpt-5.4", reply(input=100, output=10))
            with costs.label("разбор: найти кофейню", task_id):
                costs.openai_response("gpt-5.4-mini", reply(model="gpt-5.4-mini", input=100, output=10))
        finally:
            status.local.who = None
            status.end("")
        first, second = self.rows()
        self.assertEqual((first["who"], first["tid"], first["task"]), ("ag_web", task_id, "найти кофейню рядом"))
        self.assertEqual((second["tid"], second["task"]), (task_id, "разбор: найти кофейню"))
        self.assertIn(task_id, costs.by_task([task_id]))
        self.assertAlmostEqual(costs.by_task([task_id])[task_id], first["usd"] + second["usd"])

    def test_outside_a_task_the_call_is_still_recorded_with_its_purpose(self):
        costs.openai_embeddings("text-embedding-3-small", SimpleNamespace(usage=usage(total_tokens=10)), purpose="отпечатки текста")
        row = self.rows()[0]
        self.assertEqual((row["who"], row["tid"], row["purpose"]), (None, None, "отпечатки текста"))
        self.assertEqual(costs.summary("today")["by_source"][0][0], "отпечатки текста")

    def test_ledger_survives_restart_and_a_torn_line(self):
        costs.openai_response("gpt-5.4", reply(input=1000, output=100))
        path = next(Path(self.dir.name, "costs").glob("*.jsonl"))
        with open(path, "a", encoding="utf-8") as f:
            f.write('{"t": 1, "provider": "оборвано')   # shut down mid-write
        costs._reset()
        self.assertEqual(len(self.rows()), 1)
        costs.request("tomtom", "search")   # the record after the torn line is not lost
        costs._reset()
        self.assertEqual(len(self.rows()), 2)

    def test_what_another_process_spends_counts_for_the_day_without_a_restart(self):
        """The audit of 25.10: each process read a month once and then saw only its own calls — the weekly exam's few dollars
        never reached the running core's daily budget."""
        costs.record("openai", "responses", "gpt-5.4", {"input": 100_000})   # 0.25 here
        self.assertAlmostEqual(costs.spent_today(), 0.25)
        path = next(Path(self.dir.name, "costs").glob("*.jsonl"))
        other = {"t": datetime.now().timestamp(), "provider": "anthropic", "service": "messages", "model": "claude-opus-5-5",
                 "units": {"input": 1_000_000}, "tier": "default", "usd": 4.0, "estimate": False, "who": None, "tid": None, "task": ""}
        line = (json.dumps(other, ensure_ascii=False) + "\n").encode()
        with open(path, "ab") as f:   # another process appends; its line is half written at first
            f.write(line[:20])
            f.flush()
            self.assertAlmostEqual(costs.spent_today(), 0.25)   # a line being written isn't read in pieces
            f.write(line[20:])
        self.assertAlmostEqual(costs.spent_today(), 4.25)
        costs.record("openai", "responses", "gpt-5.4", {"input": 100_000})
        self.assertAlmostEqual(costs.spent_today(), 4.5)
        self.assertEqual(len(self.rows()), 3)   # each line once, the half-read one included

    def test_the_days_budget_counts_an_unpriced_call_at_the_dearest_rate(self):
        """K1 of the audit: the task brake counted a call with no known price at the dearest rate, the day's budget at zero."""
        self.assertIsNone(costs.record("anthropic", "messages", "claude-unknown-9", {"input": 1_000_000}))
        self.assertAlmostEqual(costs.spent_today(), max(r[0] for r in costs.CLAUDE_TEXT.values()))
        self.assertEqual(costs.summary("today")["unpriced"], {"anthropic/claude-unknown-9": 1})   # the journal still says "unpriced"

    def test_parallel_threads_do_not_lose_records(self):
        def work():
            for _ in range(25):
                costs.request("tomtom", "search")
        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        costs._reset()
        self.assertEqual(len(self.rows()), 200)


class Reports(Isolated):
    def at(self, days_ago, hour=12):
        day = datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
        return day.timestamp()

    def test_periods_group_and_sort(self):
        costs.record("openai", "responses", "gpt-5.4", {"input": 100_000}, moment=self.at(0), purpose="a")        # 0,25
        costs.record("openai", "responses", "gpt-5.4-mini", {"input": 100_000}, moment=self.at(1))               # 0.075 yesterday
        costs.record("openai", "responses", "gpt-5.4", {"input": 100_000}, moment=self.at(40))                   # 0.25 a while ago
        self.assertAlmostEqual(costs.summary("today")["total"], 0.25)
        self.assertAlmostEqual(costs.summary("yesterday")["total"], 0.075)
        self.assertAlmostEqual(costs.summary("week")["total"], 0.325)
        self.assertAlmostEqual(costs.summary("all")["total"], 0.575)
        self.assertEqual(costs.summary("all")["by_model"][0][0], "gpt-5.4")
        day = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
        self.assertAlmostEqual(costs.summary(day)["total"], 0.25)
        with self.assertRaises(ValueError):
            costs.summary("позавчера")

    def test_tomtom_quota_and_subscription_are_shown_not_billed(self):
        for _ in range(3):
            costs.request("tomtom", "search")
        costs.request("tomtom", "routing")
        costs.subscription("claude-code", 90, purpose="код: Sena")
        data = costs.summary("month")
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["tomtom_month"]["search"], {"used": 3, "free": 2500})
        self.assertEqual(data["claude"]["calls"], 1)
        text = costs.render(data)
        self.assertIn("TomTom search: 3 запросов в этом месяце из 2500", text)
        self.assertIn("Claude Code по подписке: 1 вызовов, 2 мин", text)

    def test_recost_updates_old_records_when_prices_change(self):
        costs.openai_response("gpt-9-mystery", reply(model="gpt-9-mystery", input=1_000_000))
        self.assertIsNone(self.rows()[0]["usd"])
        with mock.patch.dict(costs.OPENAI_TEXT, {"gpt-9": (1.0, 0.1, 5.0, None)}):
            self.assertEqual(costs.recost(), 1)
        self.assertAlmostEqual(self.rows()[0]["usd"], 1.0)
        costs._reset()
        self.assertAlmostEqual(self.rows()[0]["usd"], 1.0)   # the recost was written to disk
        self.assertEqual(costs.summary("today")["unpriced"], {})

    def test_recost_is_idempotent_and_never_turns_unknown_into_zero(self):
        costs.openai_response("gpt-5.4", reply(input=5000, output=400, web=1))
        costs.openai_embeddings("text-embedding-3-small", SimpleNamespace(usage=usage(total_tokens=30)))
        costs.gemini("gemini-3.1-flash-lite-image", {"usage": {"total_input_tokens": 5, "total_output_tokens": 1200,
                     "output_tokens_by_modality": [{"modality": "image", "tokens": 1120}]}}, "image")
        costs.openai_response("gpt-5.4", SimpleNamespace())   # a response with no data: cost unknown
        before = [r["usd"] for r in self.rows()]
        self.assertEqual(costs.recost(), 0)   # fresh records are already at current prices
        self.assertEqual([r["usd"] for r in self.rows()], before)
        self.assertIsNone(before[-1])         # and "unknown" stayed unknown, not zero

    def test_accounting_errors_are_visible_in_the_report(self):
        with mock.patch.object(costs, "_append", side_effect=OSError("диск полон")):
            costs.request("tomtom", "search")
        text = costs.report("today")
        self.assertIn("не удалось записать вызовов в журнал: 1", text)
        self.assertIn("Итог занижен", text)

    def test_report_is_short_and_has_the_main_numbers(self):
        costs.openai_response("gpt-5.4", reply(input=2000, output=500), purpose="сводка переписки")
        text = costs.report("today")
        self.assertIn("Расходы, сегодня", text)
        self.assertIn("openai", text)
        self.assertIn("gpt-5.4", text)
        self.assertIn("На что: сводка переписки", text)
        self.assertLess(len(text.splitlines()), 12)

    def test_snapshot_names_are_one_model_in_the_report_and_tiny_sums_are_visible(self):
        costs.openai_response("gpt-5.4", reply(model="gpt-5.4-2026-03-05", input=100, output=10))
        costs.openai_response("gpt-5.4", reply(model="gpt-5.4-2026-03-05", input=100, output=10))
        self.assertEqual([m for m, _ in costs.summary("today")["by_model"]], ["gpt-5.4"])
        self.assertEqual(costs.money(0), "$0")
        self.assertEqual(costs.money(0.0000012), "$0.000001")
        self.assertEqual(costs.money(0.0043), "$0.0043")
        self.assertEqual(costs.money(1.234), "$1.23")


class CallSites(Isolated):
    """Money must never bypass the ledger: check the actual paid-call sites themselves."""

    def test_llm_functions_record_their_calls(self):
        responses = SimpleNamespace(create=lambda **kw: reply(model="gpt-5.4-mini-2026-03-17", input=100, output=20))
        embeddings = SimpleNamespace(create=lambda **kw: SimpleNamespace(model="text-embedding-3-small", usage=usage(prompt_tokens=5, total_tokens=5),
                                                                         data=["v"]))
        fake = SimpleNamespace(responses=responses, embeddings=embeddings)
        with mock.patch.object(llm, "_api", lambda: fake):
            llm.respond(model="gpt-5.4-mini", input="x", purpose="сводка переписки")
            self.assertEqual(llm.embeddings(["a"]), ["v"])
        services = [(r["service"], r.get("purpose")) for r in self.rows()]
        self.assertEqual(services, [("responses", "сводка переписки"), ("embeddings", "отпечатки текста")])
        self.assertTrue(all(r["usd"] is not None for r in self.rows()))

    def test_senas_voice_and_backup_hearing_are_recorded_at_the_rate_of_their_day(self):
        import base64
        import io
        import wave
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000), w.writeframes(b"\0\0" * 2400)
        spoken = SimpleNamespace(json=lambda: {"usage": {"total_input_tokens": 10, "total_output_tokens": 50},
                                               "steps": [{"type": "model_output", "content": [{"type": "audio", "data": base64.b64encode(buffer.getvalue()).decode()}]}]})
        heard = SimpleNamespace(json=lambda: {"usage": {"total_input_tokens": 400, "total_output_tokens": 20},
                                              "steps": [{"type": "model_output", "content": [{"type": "text", "text": "привет"}]}]})
        with mock.patch.object(media, "_request", side_effect=[spoken, heard]) as sent:
            audio = media.speech("Привет.")
            self.assertEqual(media.transcribe(audio, "reply.mp3"), "привет")
        body = sent.call_args_list[0].kwargs["json"]
        self.assertEqual((body["model"], body["generation_config"]["speech_config"][0]["voice"]), (config.MODEL_TTS, config.VOICE))   # one voice everywhere
        rows = self.rows()
        self.assertEqual([(r["service"], r["model"]) for r in rows], [("speech", config.MODEL_TTS), ("transcribe", config.MODEL_TRANSCRIBE)])
        self.assertAlmostEqual(rows[0]["usd"], (10 * 0.5 + 50 * 9.0) / 1e6)
        january = datetime(2027, 1, 2).timestamp()   # the same speech after Google's price change costs twice as much
        self.assertAlmostEqual(costs.price("gemini", "speech", config.MODEL_TTS, {"input": 10, "output": 50}, moment=january)[0], (10 * 1.0 + 50 * 18.0) / 1e6)

    def test_live_voice_reply_is_priced_by_what_it_reread_and_said(self):
        # a real reply of the live voice (the probe of 25.10.2026): the re-read conversation, her sound, a tool call's text, thinking
        usage = {"promptTokenCount": 1519, "responseTokenCount": 55, "thoughtsTokenCount": 96,
                 "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 796}, {"modality": "AUDIO", "tokenCount": 596}],
                 "responseTokensDetails": [{"modality": "AUDIO", "tokenCount": 28}]}
        usd = costs.gemini_live(config.MODEL_LIVE, usage, "живой голос")
        self.assertAlmostEqual(usd, ((1519 - 596) * 0.75 + 596 * 3.0 + (55 - 28 + 96) * 4.5 + 28 * 12.0) / 1e6)
        self.assertEqual(self.rows()[0]["units"], {"input": 923, "input_audio": 596, "audio": 28, "text": 27, "thought": 96})
        self.assertEqual(costs.price("gemini", "live", "gemini-9-live", {"input": 1}), (None, False))   # an unknown model is unpriced, not free

    def test_a_long_reply_is_spoken_in_parts_at_once(self):
        text = "Первое предложение довольно длинное, чтобы набрать объём. " * 3 + "Второе тоже немаленькое и с подробностями. " * 3 + "Конец."
        parts = media._speech_parts(text)
        self.assertTrue(2 <= len(parts) <= media.SPEECH_WORKERS)
        self.assertEqual(" ".join(parts), " ".join(text.split()))   # nothing lost, the order kept
        self.assertEqual(media._speech_parts("Готово."), ["Готово."])

    def test_a_written_reply_is_said_without_its_markup(self):
        """Answers are written with headings and lists for the screen: said aloud, the marks were read out or ran lines together."""
        said = roles.spoken("## Итог\n- **Первое** дело\n1. второе: [ссылка](http://x)\n---\nКод `ls` тут.")
        self.assertEqual(said, "Итог. Первое дело. второе: ссылка. Код ls тут.")
        self.assertEqual(roles.spoken("Карта: https://maps.google.com/?q=Nyang+Nyang"), "Карта: ссылка в чате")   # a link isn't read out
        self.assertIn("https://maps.google.com", roles.spoken("Карта: https://maps.google.com/?q=x", links=True))   # unless he asked

    def test_image_is_recorded_even_when_the_model_refuses(self):
        response = SimpleNamespace(json=lambda: {"usage": {"total_input_tokens": 20, "total_output_tokens": 50}, "steps": []})
        with mock.patch.object(media, "_request", return_value=response):
            with self.assertRaises(media.MediaError):
                media.generate_image("что-то запрещённое")
        row = self.rows()[0]
        self.assertEqual((row["provider"], row["service"], row["purpose"]), ("gemini", "image", "картинка"))
        self.assertGreater(row["usd"], 0)

    def test_video_is_recorded_even_when_the_file_never_arrived(self):
        per_second = costs.GEMINI_VIDEO_TOKENS_PER_SECOND
        response = SimpleNamespace(json=lambda: {"usage": {"total_input_tokens": 30, "total_output_tokens": per_second * 2,
                                   "output_tokens_by_modality": [{"modality": "video", "tokens": per_second * 2}]}, "steps": []})
        with mock.patch.object(media, "_request", return_value=response):
            with self.assertRaises(media.MediaError):
                media.generate_video("море")
        self.assertAlmostEqual(self.rows()[0]["usd"], (30 * 1.5 + per_second * 2 * 17.5) / 1e6)

    def test_tomtom_requests_are_counted_by_kind(self):
        from sena.world import maps_tomtom
        ok = SimpleNamespace(status_code=200, json=lambda: {})
        with mock.patch.object(config, "TOMTOM_KEY", "k"), mock.patch.object(maps_tomtom.net, "get", return_value=ok):
            maps_tomtom._get("https://api.tomtom.com/search/2/search/x.json")
            maps_tomtom._get("https://api.tomtom.com/routing/1/calculateRoute/1,1:2,2/json")
        self.assertEqual([r["service"] for r in self.rows()], ["search", "routing"])

    def test_claude_code_run_is_counted_even_if_it_goes_silent(self):
        from sena.agents import ag_code
        from tests.test_security import FakeClaude
        silent = FakeClaude([{"type": "system", "subtype": "init", "apiKeySource": "none", "mcp_servers": []}], hang=True)
        with mock.patch.object(ag_code.subprocess, "Popen", return_value=silent), \
             mock.patch.object(ag_code, "_stop_group", side_effect=lambda pid: silent.done.set()) as stopped, \
             mock.patch.object(ag_code, "STALL", 0), mock.patch.object(ag_code, "_started", return_value="Sun Oct 25 10:00:00 2026"), \
             mock.patch.object(config, "DESKTOP", Path(self.dir.name)):
            Path(self.dir.name, "proj").mkdir()
            answer = ag_code.run("proj", "сделай")
        stopped.assert_called_with(99999999)   # the run that went silent is stopped with whatever it started
        self.assertIn("молчал", answer)
        self.assertEqual((self.rows()[0]["provider"], self.rows()[0]["usd"]), ("claude-code", 0.0))


class Attribution(Isolated):
    def test_calls_from_tool_threads_and_nested_agents_land_on_the_task(self):
        # Sena calls an agent as a tool, and that agent itself runs in the tool thread: the money must land on the same task
        from sena.core import brain
        seen = {}

        def agent_tool():
            def inner(**kw):
                return SimpleNamespace(output=[], output_text="готово", id="i")
            with mock.patch.object(llm, "respond", inner):
                brain.run("m", "low", "роль", "задача", [], {}, name="ag_web")
            costs.request("tomtom", "search")       # call made from inside the agent's thread
            return "ok"

        calls = [SimpleNamespace(type="function_call", name="agent", arguments="{}", call_id="c1")]
        script = [SimpleNamespace(output=calls, output_text="", id="r1"), SimpleNamespace(output=[], output_text="итог", id="r2")]
        with mock.patch.object(llm, "respond", lambda **kw: script.pop(0)):
            status.begin("общая задача")
            try:
                seen["tid"] = status.current()["id"]
                brain.run("m", "low", "роль", "задача", [tool("agent", "агент")], {"agent": agent_tool}, name="ag_sena")
            finally:
                status.end("")
        row = self.rows()[0]
        self.assertEqual((row["tid"], row["task"]), (seen["tid"], "общая задача"))
        self.assertEqual(row["who"], "ag_sena")   # the call was made in Sena's tool thread, before entering ag_web


    def test_an_unpriced_call_still_counts_for_the_task_brake_at_the_dearest_rate(self):
        """SWE-agent fails closed on an unknown price: here the journal says "unpriced", and the brake counts it at the dearest
        known rate of its kind, so a model missing from the table can't run a task past its limit for free."""
        status.begin("задача с новой моделью")
        try:
            tid = status.current()["id"]
            self.assertIsNone(costs.record("anthropic", "messages", "claude-unknown-9", {"input": 1_000_000, "output": 0}))
            dearest = max(r[0] for r in costs.CLAUDE_TEXT.values())
            self.assertAlmostEqual(costs.task_spent(tid), dearest, places=6)
        finally:
            status.end("")
        self.assertIsNone(self.rows()[-1]["usd"])   # the journal keeps it unpriced

class Endpoints(Isolated):
    def test_app_gets_totals_and_cost_per_task(self):
        from sena.memory import task_log
        from sena.phone import server
        status.begin("дорогая задача")
        task_id = status.current()["id"]
        costs.openai_response("gpt-5.4", reply(input=100_000))
        entry = task_log.save(status.end("готово"))
        self.assertEqual(entry["id"], task_id)   # the id in the task log and in the cost ledger is the same
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)   # cleanups run last-first: stop serving, then close the socket
        self.addCleanup(httpd.shutdown)
        conn = HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
        self.addCleanup(conn.close)
        conn.request("GET", f"/desk/api/costs?period=today&t={config.APP_TOKEN}")
        totals = json.loads(conn.getresponse().read())["summary"]
        self.assertAlmostEqual(totals["total"], 0.25)
        conn.request("GET", f"/desk/api/tasks?t={config.APP_TOKEN}")
        finished = json.loads(conn.getresponse().read())["finished"]
        self.assertAlmostEqual(next(t["cost"] for t in finished if t["id"] == task_id), 0.25)


class SpanRange(unittest.TestCase):
    """The "from..to" date range for the Mac app: both dates inclusive, order doesn't matter."""

    def test_range_inclusive(self):
        since, until, title = costs.span("2026-10-01..2026-10-18")
        self.assertEqual(datetime.fromtimestamp(since).date(), datetime(2026, 10, 1).date())
        self.assertEqual(datetime.fromtimestamp(until).date(), datetime(2026, 10, 19).date())
        self.assertEqual(title, "01.10.2026 — 18.10.2026")

    def test_single_day_and_order(self):
        since, until, title = costs.span("2026-10-18..2026-10-18")
        self.assertEqual(datetime.fromtimestamp(until) - datetime.fromtimestamp(since), timedelta(days=1))
        self.assertEqual(title, "18.10.2026")
        self.assertEqual(costs.span("2026-10-18..2026-10-01")[:2], costs.span("2026-10-01..2026-10-18")[:2])

    def test_bad_range(self):
        with self.assertRaises(ValueError):
            costs.span("2026-10-01..потом")


class BoundedMemory(Isolated):
    """The audit of 26.10: the apps' overview asked the cost of no tasks every two seconds and walked every cost row ever written;
    every month ever read stayed parsed in memory for the life of the process (hundreds of MB at a few months)."""

    def test_no_tasks_cost_nothing_to_ask_and_a_task_is_found_without_its_months_rows(self):
        old = datetime(2026, 1, 15, 12, 0).timestamp()
        costs.record("anthropic", "messages", "claude-haiku-5-5", {"input": 50_000}, moment=old)
        with mock.patch.object(costs, "_load", wraps=costs._load) as load:
            self.assertEqual(costs.by_task([]), {})
            self.assertEqual(load.call_count, 0)   # nothing read at all
        status.begin("старая задача")
        tid = status.current()["id"]
        try:
            costs.record("anthropic", "messages", "claude-haiku-5-5", {"input": 50_000}, moment=old + 60)
        finally:
            status.end("ok")
        self.assertAlmostEqual(costs.by_task([tid, "нет-такой"])[tid], 0.005)
        self.assertEqual([p.stem for p in costs._months], [])   # January's rows left memory again
        with mock.patch.object(costs, "_load", wraps=costs._load) as load:
            self.assertAlmostEqual(costs.by_task([tid])[tid], 0.005)
            self.assertEqual(load.call_count, 0)   # its totals are kept: the file isn't read again while it stays as it was
        costs.record("anthropic", "messages", "claude-haiku-5-5", {"input": 50_000}, moment=old + 120)
        self.assertAlmostEqual(costs.by_task([tid])[tid], 0.005)   # a later line of another task is read, this one's total stays

    def test_only_this_and_last_months_rows_stay_after_a_report_of_all_time(self):
        now = datetime.now()
        for when in (datetime(2026, 1, 15), datetime(2026, 2, 15), now):
            costs.record("anthropic", "messages", "claude-haiku-5-5", {"input": 1000}, moment=when.timestamp())
        self.assertEqual(costs.summary("all")["calls"], 3)
        last = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        self.assertTrue(all(p.stem >= last for p in costs._months))
        self.assertEqual(costs.summary("2026-01")["calls"], 1)   # an older month is read again when asked for


if __name__ == "__main__":
    unittest.main()


class HourCache(unittest.TestCase):
    def test_an_hour_long_cache_write_costs_twice_the_input(self):
        five = costs._claude_text("claude-opus-5-5", {"cache_write": 1_000_000}, "default", None)[0]
        hour = costs._claude_text("claude-opus-5-5", {"cache_write": 1_000_000, "cache_write_1h": 1_000_000}, "default", None)[0]
        self.assertAlmostEqual((five, hour), (5.0, 8.0))
