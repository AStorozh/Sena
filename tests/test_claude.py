"""Claude API as Sena's brain (core/llm_claude.py): the request is translated from Responses API's language, the turn chain is remembered,
keys are picked by role, costs are logged with Claude's pricing. No network: the Anthropic client is replaced with a stub."""
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from sena import config
from sena.core import costs, llm, llm_claude
from sena.core.tools import image, tool

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


def usage(**kw):
    return NS(input_tokens=kw.get("input", 10), output_tokens=kw.get("output", 5), cache_read_input_tokens=kw.get("read", 0),
              cache_creation_input_tokens=kw.get("write", 0), server_tool_use=NS(web_search_requests=kw["search"]) if "search" in kw else None)


def message(content, stop="end_turn", id="msg_1", **kw):
    return NS(id=id, model="claude-opus-5-5", content=content, stop_reason=stop, usage=usage(**kw), stop_details=kw.get("details"))


def text(s):
    return NS(type="text", text=s)


def use(id, name, **args):
    return NS(type="tool_use", id=id, name=name, input=args)


class Fake:
    """Replaces the Anthropic client: records requests, answers with the prepared responses in order."""
    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []
        self.messages = self
        self.beta = NS(messages=self)   # requests to models that have a fallback model go through beta

    def create(self, **kwargs):
        self.sent.append(kwargs)
        return self.answers.pop(0)


class Claude(unittest.TestCase):
    def setUp(self):
        llm_claude._threads.clear()

    def fake(self, *answers):
        client = Fake(*answers)
        patch = mock.patch.object(llm_claude, "_client", return_value=client)
        patch.start()
        self.addCleanup(patch.stop)
        return client

    def test_what_stays_the_same_is_cached_an_hour_and_his_memory_apart(self):
        """03.11.2026: a fact noted after a talk threw away the whole cached prefix — every task's first step paid for ~30
        thousand tokens again ($0.15 of $0.17), and the five-minute cache was gone between tasks six minutes apart."""
        client = self.fake(message([text("ок")]))
        spec = tool("add", "Сложить", {"a": {"type": "integer"}}, ["a"])
        llm_claude.respond(model="claude-opus-5-5", instructions="Правила работы." + llm_claude.MOVING + "Память: любит сёрфинг.",
                           tools=[spec], input=[{"role": "user", "content": "привет"}])
        sent = client.sent[0]
        self.assertEqual([b["text"] for b in sent["system"]], ["Правила работы.", "Память: любит сёрфинг."])
        self.assertEqual(sent["system"][0]["cache_control"], {"type": "ephemeral", "ttl": "1h"})
        self.assertEqual(sent["system"][1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(sent["tools"][-1]["cache_control"], {"type": "ephemeral", "ttl": "1h"})

    def test_request_is_translated_from_responses_api(self):
        client = self.fake(message([text("готово")]))
        spec = tool("add", "Сложить", {"a": {"type": "integer"}}, ["a"])
        answer = llm_claude.respond(
            model="claude-opus-5-5", instructions="Ты Сена.", reasoning={"effort": "none"}, text={"verbosity": "low"},
            tools=[spec, {"type": "web_search"}],
            input=[{"role": "user", "content": "привет"}, {"role": "assistant", "content": "здравствуй"},
                   {"role": "user", "content": [{"type": "input_text", "text": "смотри"}, image(PNG, "image/png")]},
                   {"role": "developer", "content": "сейчас вечер"}])
        sent = client.sent[0]
        self.assertEqual(sent["model"], "claude-opus-5-5")
        self.assertTrue(sent["system"][0]["text"].startswith("Ты Сена.") and "кратко" in sent["system"][0]["text"])
        self.assertEqual(sent["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(sent["output_config"], {"effort": "low"})   # Claude's thinking can't be turned off: "none" is the lowest setting
        self.assertEqual(sent["cache_control"], {"type": "ephemeral"})
        self.assertEqual(sent["tools"][0], {"name": "add", "description": "Сложить", "input_schema": spec["parameters"]})
        self.assertEqual(sent["tools"][1]["type"], "web_search_20260209")
        roles = [m["role"] for m in sent["messages"]]
        self.assertEqual(roles, ["user", "assistant", "user", "user"])
        self.assertEqual(sent["messages"][2]["content"][1]["source"]["media_type"], "image/png")
        self.assertIn("не слова Темы", sent["messages"][3]["content"])   # system context is flagged so it isn't mistaken for Tema's own words
        self.assertEqual((answer.output_text, answer.output), ("готово", []))

    def test_tool_call_comes_back_in_the_shape_brain_expects(self):
        self.fake(message([NS(type="thinking", thinking="", signature="s"), text("считаю"), use("tu_1", "add", a=2)], stop="tool_use"))
        answer = llm_claude.respond(model="claude-opus-5-5", input="сложи", tools=[tool("add", "Сложить")])
        call = answer.output[0]
        self.assertEqual((call.type, call.name, call.call_id, call.arguments), ("function_call", "add", "tu_1", '{"a": 2}'))

    def test_next_step_continues_the_same_conversation_with_tool_results(self):
        first = [NS(type="thinking", thinking="", signature="s"), use("tu_1", "snap")]
        client = self.fake(message(first, stop="tool_use", id="msg_1"), message([text("красный")], id="msg_2"))
        a = llm_claude.respond(model="claude-opus-5-5", input="сними", tools=[tool("snap", "Снимок")])
        llm_claude.respond(model="claude-opus-5-5", previous_response_id=a.id, tools=[tool("snap", "Снимок")],
                           input=[{"type": "function_call_output", "call_id": "tu_1", "output": [image(PNG, "image/png")]},
                                  {"role": "user", "content": "итог"}])
        second = client.sent[1]["messages"]
        self.assertEqual([m["role"] for m in second], ["user", "assistant", "user"])
        self.assertIs(second[1]["content"], first)   # thinking and the tool call are echoed back to Claude unchanged
        result, closing = second[2]["content"]
        self.assertEqual((result["type"], result["tool_use_id"], result["content"][0]["type"]), ("tool_result", "tu_1", "image"))
        self.assertEqual(closing, {"type": "text", "text": "итог"})   # text following tool results stays in the same message

    def test_empty_pieces_that_claude_would_reject_are_replaced(self):
        client = self.fake(message([text("ок")], id="msg_a"), message([text("ок")], id="msg_b"))
        a = llm_claude.respond(model="claude-opus-5-5", input=[{"role": "user", "content": "  "}, {"role": "user", "content": [{"type": "input_text", "text": ""}, {"type": "input_text", "text": "привет"}]}])
        self.assertEqual(client.sent[0]["messages"][0]["content"], "(пусто)")
        self.assertEqual(client.sent[0]["messages"][1]["content"], [{"type": "text", "text": "привет"}])
        llm_claude.respond(model="claude-opus-5-5", previous_response_id=a.id, input=[{"type": "function_call_output", "call_id": "tu_1", "output": ""}])
        self.assertEqual(client.sent[1]["messages"][-1]["content"][0]["content"], "(пустой ответ)")

    def test_refused_requests_get_a_fallback_model_except_where_it_does_not_exist(self):
        client = self.fake(message([text("ок")]), message([text("ок")]))
        llm_claude.respond(model="claude-opus-5-5", input="x")
        self.assertEqual((client.sent[0]["betas"], client.sent[0]["fallbacks"]), (["server-side-fallback-2026-07-01"], "default"))
        llm_claude.respond(model="claude-haiku-5-5", input="x")   # Haiku has no fallback model: the parameter is rejected there
        self.assertNotIn("fallbacks", client.sent[1])
        self.assertNotIn("betas", client.sent[1])

    def test_only_the_answer_of_the_model_that_took_over_is_returned_to_claude(self):
        fallback = NS(type="fallback")
        blocks = [NS(type="thinking", thinking="", signature="s"), text("начало"), use("tu_old", "add"), NS(type="server_tool_use", id="srv_open"),
                  NS(type="server_tool_use", id="srv_done"), NS(type="web_search_tool_result", tool_use_id="srv_done"), fallback, text("продолжила другая модель"),
                  NS(type="thinking", thinking="", signature="n"), use("tu_new", "add")]
        kept = llm_claude._echo(blocks)
        self.assertEqual([b.type for b in kept], ["text", "server_tool_use", "web_search_tool_result", "text", "thinking", "tool_use"])
        self.assertEqual([getattr(b, "id", None) for b in kept if b.type == "server_tool_use"], ["srv_done"])
        plain = [text("a"), use("tu_1", "add")]
        self.assertEqual(llm_claude._echo(plain), plain)   # without the marker, nothing changes

    def test_a_reasoning_block_bound_to_another_version_is_dropped_once_not_repeated_forever(self):
        """Anthropic's documented recovery: the same body only fails again; once more with "drop_block" goes through."""
        class BadRequest(Exception):
            status_code = 400
        client = self.fake(message([text("ок")]))
        answers = [BadRequest("messages.3.content.0: Invalid `signature` in `thinking` block. The block is bound to a different conversation."),
                   message([text("готово")])]

        def create(**kwargs):
            client.sent.append(kwargs)
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        client.create = create
        self.assertEqual(llm_claude.respond(model="claude-opus-5-5", input="x").output_text, "готово")
        retry = client.sent[1]
        self.assertEqual(retry["thinking"]["block_binding"], {"prefix_mismatch_behavior": "drop_block"})
        self.assertIn(llm_claude.BINDING_BETA, retry["betas"])
        self.assertEqual(retry["fallbacks"], "default")   # the model keeps its own fallback
        answers[:] = [BadRequest("credit balance is too low")]
        with self.assertRaises(BadRequest):   # any other bad request is not repeated
            llm_claude.respond(model="claude-opus-5-5", input="x")

    def test_lost_conversation_is_explained(self):
        self.fake()
        with self.assertRaises(llm_claude.ClaudeError):
            llm_claude.respond(model="claude-opus-5-5", input=[], previous_response_id="msg_unknown")

    def test_paused_search_is_resumed(self):
        client = self.fake(message([NS(type="server_tool_use")], stop="pause_turn", id="msg_1"), message([text("курс 1,12")], id="msg_2"))
        answer = llm_claude.respond(model="claude-opus-5-5", input="курс", tools=[{"type": "web_search"}])
        self.assertEqual(answer.output_text, "курс 1,12")
        self.assertEqual([m["role"] for m in client.sent[1]["messages"]], ["user", "assistant"])

    def test_refusal_is_said_plainly(self):
        self.fake(message([], stop="refusal", details=NS(category="cyber")))
        self.assertIn("отказалась", llm_claude.respond(model="claude-opus-5-5", input="x").output_text)

    def test_conversation_starting_with_sena_gets_a_first_reply_from_tema(self):
        client = self.fake(message([text("ок")]))
        llm_claude.respond(model="claude-opus-5-5", input=[{"role": "assistant", "content": "Доброе утро"}, {"role": "user", "content": "привет"}])
        self.assertEqual(client.sent[0]["messages"][0]["role"], "user")

    def test_parallel_tasks_keep_their_own_conversations(self):
        # Sena runs several tasks at once: the chains must not get mixed up, and old ones must not pile up forever
        import threading

        class Echo:
            """Replies with a tool call carrying the conversation number, and to a tool result with text carrying the same number."""
            def __init__(self):
                self.messages, self.count, self.lock = self, 0, threading.Lock()
                self.beta = NS(messages=self)

            def create(self, **kw):
                with self.lock:
                    self.count += 1
                    n = self.count
                first = kw["messages"][0]["content"]
                if len(kw["messages"]) == 1:
                    return message([use(f"tu_{first}", "echo")], stop="tool_use", id=f"msg_{n}")
                return message([text(f"итог {first}")], id=f"msg_{n}")

        patch = mock.patch.object(llm_claude, "_client", return_value=Echo())
        patch.start()
        self.addCleanup(patch.stop)
        answers, errors = {}, []

        def talk(i):
            try:
                a = llm_claude.respond(model="claude-opus-5-5", input=f"разговор {i}", tools=[tool("echo", "Эхо")])
                b = llm_claude.respond(model="claude-opus-5-5", previous_response_id=a.id,
                                       input=[{"type": "function_call_output", "call_id": a.output[0].call_id, "output": "ок"}])
                answers[i] = b.output_text
            except Exception as e:   # noqa: BLE001 — any thread error must show up in the check
                errors.append(repr(e))

        threads = [threading.Thread(target=talk, args=(i,)) for i in range(30)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(errors, [])
        self.assertEqual(answers, {i: f"итог разговор {i}" for i in range(30)})
        self.assertLessEqual(len(llm_claude._threads), llm_claude.THREADS_KEPT)

    def test_a_coordinator_keeps_its_conversation_while_its_agent_takes_many_steps(self):
        """The audit of 25.10: fifty steps of all chains together were kept, oldest first — a coordinator waiting on its browser
        agent lost its own conversation, and the owner heard "Sena restarted mid-task"."""
        import itertools
        ids = itertools.count()
        client = self.fake()
        client.create = lambda **kw: message([text("ок")], id=f"msg_{next(ids)}")
        coordinator = llm_claude.respond(model="claude-opus-5-5", input="купи билет")
        step = None
        for _ in range(60):   # the browser agent's steps meanwhile
            step = llm_claude.respond(model="claude-haiku-5-5", input="шаг", previous_response_id=step.id if step else None)
        answer = llm_claude.respond(model="claude-opus-5-5", previous_response_id=coordinator.id,
                                    input=[{"type": "function_call_output", "call_id": "tu_1", "output": "билет куплен"}])
        self.assertEqual(answer.output_text, "ок")
        self.assertEqual(len(llm_claude._threads), 4)   # each chain holds its last two steps, not every step it took

    def test_a_step_left_waiting_for_hours_is_let_go(self):
        import time
        client = self.fake(message([text("раз")], id="msg_old"), message([text("два")], id="msg_new"))
        old = llm_claude.respond(model="claude-opus-5-5", input="вопрос")
        with mock.patch.object(llm_claude.time, "monotonic", return_value=time.monotonic() + llm_claude.KEPT_FOR + 60):
            llm_claude.respond(model="claude-opus-5-5", input="другой вопрос")
        self.assertEqual(list(llm_claude._threads), ["msg_new"])
        with self.assertRaises(llm_claude.ClaudeError) as lost:
            llm_claude.respond(model="claude-opus-5-5", input="дальше", previous_response_id=old.id)
        self.assertIn("ждал дольше", str(lost.exception))
        self.assertEqual(len(client.sent), 2)

    def test_the_billed_requests_of_a_paused_turn_are_recorded_when_a_later_one_fails(self):
        class Broken(Exception):
            status_code = 500
        answers = [message([NS(type="server_tool_use")], stop="pause_turn", id="msg_1", search=1), Broken("server error")]
        client = self.fake()

        def create(**kwargs):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        client.create = create
        with mock.patch.object(costs, "record") as record, self.assertRaises(Broken):
            llm.respond(model="claude-opus-5-5", input="курс", purpose="проверка")
        self.assertEqual(record.call_count, 1)   # the first request was billed (its search too), though the turn failed
        self.assertEqual(record.call_args.args[3]["web_search"], 1)

    def test_the_owners_stop_reaches_a_call_in_flight_and_its_cost_still_lands_on_the_task(self):
        """The audit of 25.10: a stop waited for the reply (up to ten minutes, three times with the client's own retries)."""
        import threading
        import time
        from sena.core import cancel, status
        release, recorded = threading.Event(), []
        client = self.fake()
        client.create = lambda **kw: release.wait(10) and message([text("поздно")], id="msg_late", input=100, output=10)
        status.begin("долгий вопрос")
        tid = status.current()["id"]
        self.addCleanup(cancel.cancelled.discard, tid)
        with mock.patch.object(costs, "record", side_effect=lambda *a, **k: recorded.append((a[3]["input"], costs._context()["tid"]))):
            try:
                threading.Timer(0.3, cancel.stop_task, args=(tid,)).start()
                started = time.monotonic()
                with self.assertRaises(cancel.Stopped):
                    llm.respond(model="claude-opus-5-5", input="долгий вопрос")
                self.assertLess(time.monotonic() - started, 3)
                self.assertEqual(recorded, [])
            finally:
                status.end("")
            release.set()   # the request left behind finishes: what it cost is recorded, under the task it was made for
            for _ in range(100):
                if recorded:
                    break
                time.sleep(0.02)
        self.assertEqual(recorded, [(100, tid)])

    # ---------- keys ----------

    def test_key_depends_on_the_role_and_is_given_explicitly(self):
        llm_claude._clients.clear()
        self.addCleanup(llm_claude._clients.clear)
        keys = {"brain": "sk-ant-brain", "agents": ""}
        with mock.patch.object(config, "CLAUDE_KEYS", keys), mock.patch("anthropic.Anthropic") as client:   # imported by the first call
            llm_claude._client("brain")
            self.assertEqual(client.call_args.kwargs["api_key"], "sk-ant-brain")   # nothing is taken from the environment (ANTHROPIC_*)
            llm_claude._client("agents")
            self.assertEqual(client.call_args.kwargs["api_key"], "sk-ant-brain")   # the helpers' own key isn't issued yet: the brain's
            llm_claude._clients.clear()
            keys["agents"] = "sk-ant-agents"
            llm_claude._client("agents")
            self.assertEqual(client.call_args.kwargs["api_key"], "sk-ant-agents")
            llm_claude._clients.clear()
            keys.update(brain="", agents="")
            with self.assertRaises(llm_claude.ClaudeError) as caught:
                llm_claude._client("agents")
        self.assertIn("SENA_CLAUDE_AGENTS_KEY", str(caught.exception))
        self.assertEqual((llm_claude.role_of(config.MODEL_MAIN), llm_claude.role_of(config.MODEL_FAST)), ("brain", "agents"))

    def test_an_instant_reply_skips_reasoning_where_the_model_can(self):
        for model, thinking in (("claude-haiku-5-5", {"type": "disabled"}), ("claude-sonnet-5-5", {"type": "between_tools"}), ("claude-opus-5-5", None)):
            client = self.fake(message([text("готово")]))
            llm_claude.respond(model=model, input="который час", reasoning={"effort": "none"})
            self.assertEqual(client.sent[0].get("thinking"), thinking, model)
            self.assertEqual(client.sent[0]["output_config"]["effort"], "low")

    # ---------- llm.respond and costs ----------

    def test_llm_routes_claude_models_to_the_adapter_and_records_the_cost(self):
        answer = NS(id="msg_1", model="claude-opus-5-5", output=[], output_text="ок", usage=NS(input_tokens=10, output_tokens=5),
                    raw_usage=usage(input=1000, output=500, read=2000, write=100, search=2))
        with mock.patch.object(llm_claude, "respond", return_value=answer) as adapter, mock.patch.object(costs, "record") as record:
            self.assertIs(llm.respond(purpose="проверка", model="claude-opus-5-5", input="x"), answer)
        adapter.assert_called_once()
        args = record.call_args
        self.assertEqual(args.args[:3], ("anthropic", "messages", "claude-opus-5-5"))
        self.assertEqual(args.args[3], {"input": 1000, "cache_read": 2000, "cache_write": 100, "cache_write_1h": 0, "output": 500, "web_search": 2})

    def test_gpt_models_still_go_to_openai(self):
        with mock.patch.object(llm_claude, "respond") as adapter, mock.patch.object(llm, "_api") as api, mock.patch.object(costs, "openai_response"):
            api.return_value.responses.create.return_value = NS(id="r", output=[], output_text="", usage=None)
            llm.respond(model="gpt-5.4-mini", input="x")
        adapter.assert_not_called()
        api.return_value.responses.create.assert_called_once()

    def test_claude_price_counts_cache_and_search(self):
        usd, estimate = costs.price("anthropic", "messages", "claude-opus-5-5", {"input": 1_000_000, "cache_read": 1_000_000, "cache_write": 1_000_000,
                                                                                  "output": 1_000_000, "web_search": 100})
        self.assertAlmostEqual(usd, 4.0 + 0.20 + 5.0 + 20.0 + 1.0)   # input, cache read, cache write (1.25x input), output, 100 searches at 1 cent each
        self.assertFalse(estimate)
        self.assertEqual(costs.price("anthropic", "messages", "claude-неизвестная", {"input": 5}), (None, False))

    def test_claude_errors_are_explained_as_claude_not_openai(self):
        rate = type("RateLimitError", (Exception,), {"__module__": "anthropic"})("429")
        auth = type("AuthenticationError", (Exception,), {"__module__": "anthropic"})("invalid x-api-key")
        self.assertEqual(llm.explain(rate), "Claude перегружен, просит подождать")
        self.assertEqual(llm.explain(auth), "ключ Claude не подошёл")
        self.assertIn("SENA_CLAUDE", llm.explain(llm_claude.ClaudeError("нет ключа SENA_CLAUDE_BRAIN_KEY")))
        # any other error is named as Claude named it, not "an internal error of Claude"
        long = type("BadRequestError", (Exception,), {"__module__": "anthropic", "status_code": 400, "body": {
            "type": "error", "error": {"type": "invalid_request_error", "message": "prompt is too long: 250000 tokens > 200000 maximum"}}})("Error code: 400")
        self.assertEqual(llm.explain(long), "Claude не принял запрос (400: prompt is too long: 250000 tokens > 200000 maximum)")
        down = type("InternalServerError", (Exception,), {"__module__": "anthropic", "status_code": 500})("Internal server error")
        self.assertEqual(llm.explain(down), "сбой на стороне Claude (500)")


class NamedFailures(unittest.TestCase):
    """The audits of 26.10: «нет связи с OpenAI» for every timeout — a Telegram download, Gemini, the home model — while OpenAI
    isn't used; and a stop that reached Claude mid-answer was taken for a broken cloud and answered by the home model."""

    def test_only_the_service_that_failed_is_named(self):
        import requests
        from sena.core import local
        gemini = requests.ConnectionError("HTTPSConnectionPool(host='generativelanguage.googleapis.com', port=443): Max retries exceeded")
        telegram = requests.ConnectionError("HTTPSConnectionPool(host='api.telegram.org', port=443): Read timed out")
        self.assertEqual(llm.explain(gemini), "нет связи с Google")
        self.assertEqual(llm.explain(telegram), "нет связи с Телеграмом")
        self.assertEqual(llm.explain(TimeoutError("timed out")), "нет связи")
        self.assertEqual(llm.explain(local.Unavailable("домашняя модель не отвечает: Connection refused")), "домашняя модель не отвечает: Connection refused")
        dropped = type("APIConnectionError", (Exception,), {"__module__": "openai"})("Connection error.")
        self.assertEqual(llm.explain(dropped), "нет связи с OpenAI")   # OpenAI's own error still says so
        self.assertEqual(llm.explain(KeyError("oops")), "внутренняя ошибка")

    def test_a_stop_during_a_narrow_job_is_not_answered_at_home(self):
        from sena.core import cancel, local, roles
        home = mock.Mock(return_value={"x": 1})
        with mock.patch.object(roles, "_cloud", side_effect=cancel.Stopped("Остановлено по просьбе Темы")), \
                mock.patch.object(local, "installed", return_value={"chat": True}), mock.patch.object(local, "chat", home), \
                mock.patch.object(roles, "where", return_value=roles.CLOUD), self.assertRaises(cancel.Stopped):
            roles.ask("feedback", "инструкция", "текст", {"type": "object"})
        home.assert_not_called()


if __name__ == "__main__":
    unittest.main()
