"""The only place where we talk to the cloud language models: dialogue with a Claude model (a name starting with "claude") goes
to llm_claude, any other name to OpenAI; OpenAI embeddings for the cloud fingerprints. Sena's voice and the backup hearing are
Gemini's (google/media.py). Which job goes to the cloud at all is decided by core/roles.py: the models on this Mac are core/local.py."""
import re
import threading
import time
from email.utils import parsedate_to_datetime
from types import SimpleNamespace

from sena import config
from sena.core import costs, events, limits, llm_claude, status

_instance = None
_lock = threading.Lock()

def _api():
    # the key is handed over explicitly (config reads .env; the process environment never holds it); the client is created on first use.
    # Deliberately kept private: all paid calls must go through the functions below, where spending is tracked.
    global _instance
    with _lock:
        if _instance is None:
            if not config.OPENAI_API_KEY:
                raise RuntimeError("нет ключа OpenAI: впиши OPENAI_API_KEY в .env")
            from openai import OpenAI   # here, not at the top: a third of a second of every start for a client that is off by default
            _instance = OpenAI(api_key=config.OPENAI_API_KEY, max_retries=2, timeout=300)   # a hung connection won't hold up a task for more than five minutes
    return _instance


OWN_WORDS = ("LimitReached", "ClaudeError", "Unavailable")   # failures whose text is already in words for Tema (the budget, a key, the home models, the Keychain)
SERVICES = (("telegram.org", "Телеграмом"), ("googleapis.com", "Google"), ("google.com", "Google"), ("anthropic.com", "Claude"), ("openai.com", "OpenAI"))
DROPPED = ("APIConnectionError", "APITimeoutError", "ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout", "TimeoutError")


def explain(e):
    """The reason for the failure, in words for Tema, without exception names. A service is named only when it is the one that
    failed: "нет связи с OpenAI" was said for every timeout and refused connection — a Telegram download, Gemini, the home model."""
    name, text = type(e).__name__, str(e).lower()
    if name in OWN_WORDS:
        return str(e)[:300]
    module = type(e).__module__
    if module.startswith("anthropic"):   # Claude API errors share names with OpenAI's (RateLimitError), but the cause differs
        if "credit balance" in text or "billing" in text:
            return "на счёте Claude кончились деньги"
        if name in ("RateLimitError", "OverloadedError") or "overloaded" in text:
            return "Claude перегружен, просит подождать"
        if name in ("AuthenticationError", "PermissionDeniedError"):
            return "ключ Claude не подошёл"
        if name in ("APIConnectionError", "APITimeoutError"):
            return "нет связи с Claude"
        return _claude_said(e)
    if module.startswith("openai"):
        if "insufficient_quota" in text or "billing" in text:
            return "на счёте OpenAI кончились деньги"
        if name == "RateLimitError" or "429" in text:
            return "OpenAI перегружен, просит подождать"
        if name in DROPPED or "timed out" in text or "connection" in text:
            return "нет связи с OpenAI"
        if name == "AuthenticationError" or "api key" in text:
            return "ключ OpenAI не подошёл"
        return "внутренняя ошибка OpenAI"
    if name == "RateLimitError" or "429" in text or "too many requests" in text:
        return "сервис перегружен, просит подождать"
    if name in DROPPED or "timed out" in text or "connection" in text:
        return f"нет связи с {_whom(e)}" if _whom(e) else "нет связи"
    return "внутренняя ошибка"


def _whom(e):
    # who a dropped connection was to, by the address its error names (requests and urllib3 name the host)
    found = re.search(r"host='([^']+)'", str(e))
    host = (found.group(1) if found else "").lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        return "программой на этом Mac"
    return next((who for part, who in SERVICES if host == part or host.endswith("." + part)), host)


def _claude_said(e):
    """Any other Claude error, as Claude named it: a request it didn't take (400 "prompt is too long", 404 an unknown model, 413
    too big) or its own failure (5xx). "An internal error of Claude" for all of them hid which one it was."""
    code, body = getattr(e, "status_code", None), getattr(e, "body", None)
    error = body.get("error") if isinstance(body, dict) else None
    said = " ".join(str((error.get("message") if isinstance(error, dict) else None) or getattr(e, "message", None) or e).split())[:200]
    if isinstance(code, int) and code >= 500:
        return f"сбой на стороне Claude ({code})"
    return f"Claude не принял запрос ({code}: {said})" if code else f"ошибка Claude: {said}"


PASSING = (408, 409, 429, 500, 502, 503, 504, 529)   # HTTP answers that mean "not now", not "never"


def transient(e):
    """Whether a failed model call is worth repeating in a while: overloaded, rate-limited, a server error, no connection, a
    timeout. Money, a key, a bad request or the daily budget are not: repeating changes nothing."""
    name, text = type(e).__name__, str(e).lower()
    if name in ("LimitReached", "ClaudeError") or any(w in text for w in ("credit balance", "billing", "insufficient_quota")):
        return False
    code = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
    if isinstance(code, int):
        return code in PASSING or code >= 500   # any server error is passing (a proxy's 520–524 too)
    return (name in ("APIConnectionError", "APITimeoutError", "ConnectionError", "Timeout", "OverloadedError", "InternalServerError", "RateLimitError")
            or "overloaded" in text or "timed out" in text)


def outage(e):
    """Whether the cloud as such is out of reach for a while — passing (overloaded, no network) or the account (no money, a key
    that doesn't fit, the day's budget): then the home model may stand in for it. A bad request or a broken answer is not an
    outage: it is Sena's own fault, and the home model answering it would hide it (LiteLLM classifies an error once and drives
    every decision from that)."""
    if transient(e):
        return True
    name, text = type(e).__name__, str(e).lower()
    return (name in ("LimitReached", "AuthenticationError", "PermissionDeniedError")
            or any(w in text for w in ("credit balance", "billing", "insufficient_quota")))


def retry_after(e):
    """The seconds the service asked to wait before the next try, or None: retry-after-ms, or Retry-After in seconds or as a
    date (the forms LiteLLM and the SDKs read)."""
    try:
        headers = e.response.headers
        if headers.get("retry-after-ms"):
            return float(headers["retry-after-ms"]) / 1000
        value = headers.get("retry-after")
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (AttributeError, TypeError, ValueError, IndexError):
        return None


def _sent(input):
    # what went to the model, for the event journal: the message text or a list of turns (role and text, tool outputs).
    # A turn made up only of tool outputs isn't repeated: they're already recorded as tool events
    if not isinstance(input, list):
        return input
    if input and all(isinstance(i, dict) and i.get("type") == "function_call_output" for i in input):
        return f"ответы инструментов: {len(input)}"
    rows = []
    for item in input:
        if not isinstance(item, dict):
            rows.append(str(item)[:200])
        elif item.get("type") == "function_call_output":
            out = item.get("output")
            rows.append({"call_id": item.get("call_id"), "output": out if isinstance(out, str) else "(картинка)"})
        else:
            content = item.get("content")
            if isinstance(content, list):
                content = " ".join(c.get("text", "(вложение)") for c in content if isinstance(c, dict))
            rows.append({"role": item.get("role"), "content": content})
    return rows


# Every function records its own paid call in the cost journal (costs.py) and the action itself in the event journal (events.py).
# purpose — the reason for the call outside of a task ("conversation summary"); it never reaches OpenAI.
def is_claude(model):
    return str(model or "").startswith("claude")


MOVING = llm_claude.MOVING   # in instructions: what is above it stays the same for hours, what is below changes often


def respond(purpose=None, **kwargs):
    # Responses API: it's the only one where models can think and use tools at the same time
    limits.budget_check()
    started = time.time()
    if is_claude(kwargs.get("model")):   # Claude API: llm_claude translates the request and returns the reply in the same shape
        try:
            response = llm_claude.respond(**kwargs)
        except Exception as e:
            _paid_anyway(e, purpose)
            raise
        costs.claude_response(response, purpose)
    else:
        if kwargs.get("instructions"):
            kwargs["instructions"] = kwargs["instructions"].replace(MOVING, "\n\n")
        response = _api().responses.create(**kwargs)
        costs.openai_response(kwargs.get("model", ""), response, purpose)
    usage = getattr(response, "usage", None)
    calls = [{"name": getattr(o, "name", None), "args": getattr(o, "arguments", "")} for o in response.output if getattr(o, "type", "") == "function_call"]
    events.log("model", getattr(response, "output_text", ""), model=kwargs.get("model"), purpose=purpose, input=_sent(kwargs.get("input")),
               instructions=(kwargs.get("instructions") or "")[:300] or None, calls=calls or None, response=getattr(response, "id", None),
               tokens={"input": usage.input_tokens, "output": usage.output_tokens} if usage else None,
               seconds=round(time.time() - started, 1),   # how long the model took, beside what it cost (Dify keeps both with each call)
               private=getattr(status.local, "who", None) == "ag_ears")   # other people's speech around Tema doesn't go into the permanent journal
    return response


def _paid_anyway(e, purpose):
    """Claude requests that were billed though no answer came back: the earlier requests of a paused turn whose later one
    failed (e.paid), and a request left running at the owner's stop, recorded when it finishes (e.late) — under the task it
    was made for, which has ended by then."""
    task = status.current() or {}

    def record(answer):
        with costs.label(task.get("text"), task.get("id")):
            costs.claude_response(answer, purpose)
    if getattr(e, "paid", None):
        record(SimpleNamespace(calls=e.paid))
    if callable(getattr(e, "late", None)):
        e.late(record)


def ping():
    """Is the OpenAI key alive: the list of models, which costs nothing (an empty balance shows only on a paid call)."""
    return [m.id for m in _api().models.list()]


def claude_probe(role="brain", model=llm_claude.CHEAPEST, purpose=None):
    """Is there money on a Claude key: one token (llm_claude.probe), recorded like every paid call."""
    limits.budget_check()
    response = llm_claude.probe(role, model)
    costs.claude_response(response, purpose)
    events.log("model", response.output_text, model=model, purpose=purpose,
               tokens={"input": response.usage.input_tokens, "output": response.usage.output_tokens})
    return response


def embeddings(texts, purpose=None):
    limits.budget_check()
    reply = _api().embeddings.create(model=config.MODEL_EMBED, input=texts, dimensions=config.EMBED_DIMS)
    costs.openai_embeddings(config.MODEL_EMBED, reply, purpose or "отпечатки текста")
    events.log("embeddings", "", purpose=purpose, model=config.MODEL_EMBED, count=len(texts))
    return reply.data
