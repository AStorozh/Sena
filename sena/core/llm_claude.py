"""Claude API for Sena herself: the only place where the Anthropic client is created. Reached only through llm.py.
The rest of the code (brain, agents) speaks the Responses API's language and doesn't know which model this is: here the
request is translated into Claude's language, and the reply comes back in the same shape (output with function_call,
output_text, id, usage).
Code is written only through the subscription: Sena's Claude keys (SENA_CLAUDE_*) aren't named ANTHROPIC_API_KEY, so they
never end up in a program's environment (core/safety.clean_env), and the client receives the key explicitly."""
import json
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from types import SimpleNamespace

from sena import config
from sena.core import cancel, events, status

MAX_TOKENS = 16000     # a reply with reasoning and tool calls; more isn't needed
# a whole reply of MAX_TOKENS may take the SDK's own estimate (an hour per 128K tokens: 450 s here) and a request without
# streaming may take ten minutes; a shorter cut-off killed a long but healthy reply and the retries paid for it again
TIMEOUT = max(600.0, 3600 * MAX_TOKENS / 128_000 + 60)
MAX_PAUSES = 5         # Claude's built-in search sometimes pauses the turn: we continue, but not forever
WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}   # built-in search instead of OpenAI's web_search
# Claude keeps no turn memory of its own: each step of a conversation is kept here for previous_response_id. A chain holds
# only its last two steps (the next call continues the last one; a refused or overloaded call is asked again from the one
# before), so a coordinator waiting on its agents' long chains keeps its own. A step waits for its continuation up to
# KEPT_FOR — longer than any tool call a step waits on (a coding job, 90 min) — and at most THREADS_KEPT steps are kept
KEPT_FOR = 3 * 3600
THREADS_KEPT = 400
LOW = {"none", "minimal", "low"}   # the lowest effort Claude takes is low
# effort "none" (voice commands, the browser's fast path): these models can also stop reasoning altogether — each in its own way;
# Opus and Fable can't, they keep the lowest effort
NO_THINKING = {"claude-haiku-5-5": {"type": "disabled"}, "claude-sonnet-5-5": {"type": "between_tools"}}
# Safety classifiers sometimes refuse on harmless input (reviewing someone else's code, terminal commands). For these models
# the refusal is retried on a different model on Anthropic's side ("default" picks it based on the refusal reason), and the
# task doesn't stall. The others (Haiku) don't have this option.
FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-fable-5", "claude-sonnet-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# a reasoning block is bound to the conversation that made it: if an earlier message changed, Claude refuses it as "bound to a
# different conversation", and the same request only fails again; once more with the mismatched blocks dropped goes through
BINDING_BETA = "thinking-binding-controls-2026-08-01"


def _rebound(e):
    return getattr(e, "status_code", None) == 400 and "bound to a different conversation" in str(e)

_clients = {}
_threads = OrderedDict()   # response id -> (when it was kept, the id it continued, every conversation message up to and including it)
_lock = threading.Lock()


class ClaudeError(Exception):
    """Claude can't answer because of a configuration issue (missing key). The text can be shown to Tema as is."""


def role_of(model):
    # the key depends on the role: the main brain and worker agents use different keys, so spending and key revocation stay separate
    return "brain" if model == config.MODEL_MAIN else "agents"


def _client(role):
    with _lock:
        if role not in _clients:
            key = config.CLAUDE_KEYS.get(role) or config.CLAUDE_KEYS.get("brain", "")   # helpers use the brain's key until their own is issued
            if not key:
                raise ClaudeError(f"нет ключа Claude для роли «{role}»: впиши SENA_CLAUDE_{role.upper()}_KEY в .env")
            # key passed explicitly: the environment (ANTHROPIC_*) isn't read. Two quick retries: longer outages are the brain's
            # recovery ladder (brain.respond), and retry layers multiply (OpenAI Agents SDK, pydantic-ai). Imported here: the
            # screen without the core never calls Claude, and the import is a third of a second of every start
            from anthropic import Anthropic, Timeout
            _clients[role] = Anthropic(api_key=key, max_retries=2, timeout=Timeout(TIMEOUT, connect=10.0))
        return _clients[role]


CHEAPEST = "claude-haiku-5-5"   # the balance probe asks it for one token: about a millionth of a dollar


def ping(role="brain"):
    """Is the Claude key of a role alive: the list of models, which costs nothing (a spent balance shows only on a paid call)."""
    return [m.id for m in _client(role).models.list(limit=100)]


def probe(role="brain", model=CHEAPEST):
    """The smallest paid call there is: one token on a role's key — the only way to see a spent balance.
    Returns the reply in the Responses API shape, for the cost journal (llm.claude_probe)."""
    message = _client(role).messages.create(model=model, max_tokens=1, messages=[{"role": "user", "content": "1"}])
    return _answer(message, _text(message))


# ---------- translating the request ----------
def _blocks(content):
    """Responses API message parts (input_text, input_image, input_file) -> Claude blocks. A string stays a string."""
    if isinstance(content, str):
        return content if content.strip() else "(пусто)"   # Claude rejects empty and whitespace-only text
    if isinstance(content, dict):
        content = [content]
    blocks = []
    for part in content or []:
        kind = part.get("type")
        if kind in ("input_text", "text", "output_text"):
            if part.get("text", "").strip():   # Claude rejects an empty text block
                blocks.append({"type": "text", "text": part["text"]})
        elif kind in ("input_image", "image"):
            match = re.match(r"data:([^;]+);base64,(.*)", part.get("image_url", ""), re.S)
            if match:
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": match.group(1), "data": match.group(2)}})
        elif kind == "tool_reference":   # an opened group (core/brain.py): the API expands the reference into the tool's definition
            blocks.append({"type": "tool_reference", "tool_name": part["tool_name"]})
        elif kind == "input_file":
            match = re.match(r"data:([^;]+);base64,(.*)", part.get("file_data", ""), re.S)
            if match:
                blocks.append({"type": "document", "source": {"type": "base64", "media_type": match.group(1), "data": match.group(2)}})
    return blocks or "(пусто)"


def _messages(input):
    """Responses API input (messages and tool outputs) -> Claude messages. Tool outputs go in one message, before the text."""
    if isinstance(input, str):
        return [{"role": "user", "content": input}]
    messages, results = [], []

    def flush(extra=()):
        nonlocal results
        if results or extra:
            messages.append({"role": "user", "content": [*results, *extra]})
        results = []

    for item in input:
        if item.get("type") == "function_call_output":
            out = item.get("output")
            results.append({"type": "tool_result", "tool_use_id": item["call_id"],
                            "content": (out.strip() and out) or "(пустой ответ)" if isinstance(out, str) else _blocks(out),
                            **({"is_error": True} if item.get("is_error") else {})})
        else:
            content = _blocks(item.get("content"))
            role = "assistant" if item.get("role") == "assistant" else "user"
            if item.get("role") in ("developer", "system") and isinstance(content, str):
                # context from Sena's system (memory, schedule, time, a note after tool results): a system message inside the
                # conversation is refused by some models a request may land on (Claude Sonnet 5 as a server-side fallback) and
                # can't open it, so it goes as a regular turn, tagged with whose text it is
                content = "[Контекст системы Сены, не слова Темы]\n" + content
            if role == "user" and results:   # text after tool outputs (the summary when the step limit is hit) — into the same message
                flush([{"type": "text", "text": content}] if isinstance(content, str) else content)
            else:
                flush()
                messages.append({"role": role, "content": content})
    flush()
    return messages


def _tools(tools):
    out = []
    for spec in tools or []:
        if spec.get("type") == "web_search":
            out.append(WEB_SEARCH)
        elif spec.get("type") == "function":
            out.append({"name": spec["name"], "description": spec.get("description", ""), "input_schema": spec["parameters"],
                        **({"defer_loading": True} if spec.get("defer") else {})})   # known to the API, loaded only when referenced
    return out


UNSUPPORTED = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength", "maxLength", "minItems", "maxItems", "uniqueItems")


def _strict(schema):
    """A JSON schema as Claude's structured answers take it: every object closed (additionalProperties false), no limits on
    numbers, lengths and counts (the API rejects them with a 400). None if it can't be: an object with free keys — Claude
    has no way to say "any keys" — then the schema is given in words instead (respond)."""
    if isinstance(schema, list):
        out = [_strict(s) for s in schema]
        return None if any(s is None for s in out) else out
    if not isinstance(schema, dict):
        return schema
    out = {k: v for k, v in schema.items() if k not in UNSUPPORTED}
    kind = out.get("type")
    if kind == "object" or (isinstance(kind, list) and "object" in kind):
        if "properties" not in out:
            return None
        out["additionalProperties"] = False
    for key in ("properties", "$defs", "definitions"):
        if key in out:
            parts = {name: _strict(part) for name, part in out[key].items()}
            if any(p is None for p in parts.values()):
                return None
            out[key] = parts
    for key in ("items", "anyOf", "allOf", "oneOf"):
        if key in out:
            out[key] = _strict(out[key])
            if out[key] is None:
                return None
    return out


# ---------- translating the response ----------
def _answer(message, text):
    calls = [SimpleNamespace(type="function_call", name=b.name, arguments=json.dumps(b.input, ensure_ascii=False), call_id=b.id)
             for b in message.content if b.type == "tool_use"]
    usage = message.usage
    searched = any(b.type == "web_search_tool_result" for b in message.content)   # the built-in search read pages (core/brain.py)
    return SimpleNamespace(id=message.id, model=message.model, output=calls, output_text=text, stop_reason=message.stop_reason, searched=searched,
                           usage=SimpleNamespace(input_tokens=usage.input_tokens + (usage.cache_read_input_tokens or 0) + (usage.cache_creation_input_tokens or 0),
                                                 output_tokens=usage.output_tokens), raw_usage=usage)


def _echo(content):
    """What to feed back to Claude from the response on the next step. If the response was partly produced by another model
    (the content contains a fallback marker), everything before the marker — reasoning, tool calls, unclosed built-in search
    calls — is discarded: Anthropic requires this, otherwise the request is rejected."""
    blocks = list(content)
    marks = [i for i, b in enumerate(blocks) if b.type == "fallback"]
    if not marks:
        return content   # the response came entirely from one model: return it as is
    before, after = blocks[:marks[-1]], blocks[marks[-1] + 1:]
    answered = {getattr(b, "tool_use_id", None) for b in before if b.type.endswith("_tool_result")}
    kept = [b for b in before if b.type == "text" or b.type.endswith("_tool_result") or (b.type == "server_tool_use" and b.id in answered)]
    return kept + after


def _text(message):
    if message.stop_reason == "refusal":
        category = getattr(getattr(message, "stop_details", None), "category", None)
        return f"Модель отказалась отвечать на это{f' ({category})' if category else ''}."
    return "".join(b.text for b in message.content if b.type == "text")


def _keep(id, parent, thread):
    """A step of a conversation, for its continuation: its parent's parent goes (no call ever starts from it again), and so do
    steps older than KEPT_FOR and the oldest past THREADS_KEPT."""
    with _lock:
        if parent in _threads:
            _threads.pop(_threads[parent][1], None)
        _threads[id] = (time.monotonic(), parent, thread)
        now = time.monotonic()
        while _threads and (len(_threads) > THREADS_KEPT or now - next(iter(_threads.values()))[0] > KEPT_FOR):
            _threads.popitem(last=False)


def _in_flight(exchange):
    """exchange() on a helper thread while this one waits in one-second slices: the owner's stop leaves the wait at once, not
    after the reply (without streaming one takes up to ten minutes, and the client repeats a timeout twice more). The request
    left behind finishes on its own and is paid for: Stopped.late(record) has record() called with what it cost when it does."""
    done = Future()

    def run():
        try:
            done.set_result(exchange())
        except BaseException as e:   # noqa: BLE001 — handed to the waiting thread as it is
            done.set_exception(e)
    threading.Thread(target=status.carried(run), daemon=True, name="claude").start()   # a daemon: a request left behind never holds Sena's exit
    while True:
        try:
            return done.result(timeout=1)
        except FutureTimeout:
            if cancel.is_cancelled():
                stopped = cancel.Stopped("Остановлено по просьбе Темы")
                stopped.late = lambda record: done.add_done_callback(lambda f: _settle(f, record))
                raise stopped from None


def _settle(done, record):
    # what a request left behind at a stop cost, once it finished: its answer, or the billed part of a turn that failed later
    error = done.exception()
    if error is None:
        record(done.result())
    elif getattr(error, "paid", None):
        record(SimpleNamespace(calls=error.paid))


MOVING = "\n\n<!-- moving -->\n\n"   # instructions above it stay the same for hours (cached an hour), below it change often


def respond(model, input, instructions=None, tools=None, reasoning=None, text=None, previous_response_id=None, tool_choice=None, **_ignored):
    """One conversation step. Returns the reply in the Responses API shape; remembers the conversation itself (_keep).
    A turn that fails after some of its requests were billed carries them as error.paid; a stop raises Stopped with .late."""
    with _lock:
        kept = _threads.get(previous_response_id) if previous_response_id else None
        history = list(kept[2]) if kept else []
    if previous_response_id and not kept:
        raise ClaudeError(f"продолжение разговора потеряно (Сена перезапускалась или шаг ждал дольше {KEPT_FOR // 3600} ч): начни задачу заново")
    messages = history + _messages(input)
    if messages[0]["role"] != "user":   # Claude only starts a conversation with Tema's turn
        messages.insert(0, {"role": "user", "content": "(начало переписки)"})
    system = instructions or ""
    if (text or {}).get("verbosity") == "low":
        system += "\n\nОтвечай кратко, без лишних слов."   # Claude has no response-length parameter like OpenAI does
    fmt = (text or {}).get("format") or {}
    schema = _strict(fmt["schema"]) if fmt.get("type") == "json_schema" else None
    if fmt.get("type") == "json_schema" and schema is None:   # free keys somewhere: the schema goes in words, the caller reads JSON from the text
        system += "\n\nОтветь только JSON по этой схеме, без пояснений:\n" + json.dumps(fmt["schema"], ensure_ascii=False)
    effort = (reasoning or {}).get("effort", "medium")
    # caching points: the tools, the instructions that stay the same for hours (both kept an hour: tasks minutes apart read
    # them instead of paying for them again), the part that changes (his memory and rules — a fact noted after a talk used to
    # throw away the whole cached prefix, ~30 thousand tokens at full price on every task's first step: $0.15 of the brain's
    # $0.17, 03.11.2026), and the tail (the steps of one turn repeat its start)
    stable, _, moving = system.partition(MOVING)
    params = dict(model=model, max_tokens=MAX_TOKENS, tools=_tools(tools), cache_control={"type": "ephemeral"},
                  system=[{"type": "text", "text": part, "cache_control": {"type": "ephemeral", **({"ttl": "1h"} if i == 0 and moving.strip() else {})}}
                          for i, part in enumerate((stable, moving)) if part.strip()],
                  output_config={"effort": "low" if effort in LOW else effort})
    loaded = [i for i, t in enumerate(params["tools"]) if not t.get("defer_loading")]   # a deferred tool isn't part of the prefix
    if loaded:
        params["tools"][loaded[-1]] = {**params["tools"][loaded[-1]], "cache_control": {"type": "ephemeral", "ttl": "1h"}}
    if schema is not None:   # a structured answer: Claude's output_config.format guarantees valid JSON in the text
        params["output_config"]["format"] = {"type": "json_schema", "schema": schema}
    if effort == "none" and model in NO_THINKING:   # an instant reply: no reasoning before it
        params["thinking"] = NO_THINKING[model]
    if tool_choice == "none":
        params["tool_choice"] = {"type": "none"}
    params = {k: v for k, v in params.items() if v not in ("", [], None)}
    client = _client(role_of(model))

    def call(msgs, drop=False):
        extra, betas = dict(params), [FALLBACK_BETA] if model in FALLBACK_MODELS else []
        if drop:   # the documented recovery: the request goes on without the blocks that no longer match (LiteLLM repairs it too)
            extra["thinking"] = {**(params.get("thinking") or {"type": "adaptive"}), "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
            betas.append(BINDING_BETA)
        if model in FALLBACK_MODELS:
            extra["fallbacks"] = "default"
        if betas:
            return client.beta.messages.create(betas=betas, messages=msgs, **extra)
        return client.messages.create(messages=msgs, **extra)

    def send(msgs):
        try:
            return call(msgs)
        except Exception as e:
            if not _rebound(e) or params.get("thinking", {}).get("type") in ("disabled", "between_tools"):
                raise
            events.error("Claude: рассуждение привязано к другой версии переписки, повторяю без него", e)
            return call(msgs, drop=True)

    def exchange():
        msgs, paid = messages, []   # every request of a paused turn is billed, and any of them may have read pages
        try:
            message = send(msgs)
            paid.append(message)
            for _ in range(MAX_PAUSES):   # built-in search may have paused the turn: continue from the same place
                if message.stop_reason != "pause_turn":
                    break
                msgs = msgs + [{"role": "assistant", "content": _echo(message.content)}]
                message = send(msgs)
                paid.append(message)
        except Exception as e:
            e.paid = [(m.model, m.usage) for m in paid]   # billed though the turn failed: llm.respond records them anyway
            raise
        answer = _answer(message, _text(message))
        answer.calls = [(m.model, m.usage) for m in paid]   # costs.claude_response prices each one (LiteLLM's study: only the last one was)
        answer.searched = any(b.type == "web_search_tool_result" for m in paid for b in m.content)
        _keep(message.id, previous_response_id, msgs + [{"role": "assistant", "content": _echo(message.content)}])   # reasoning blocks go back to Claude as they are
        return answer
    return _in_flight(exchange)

