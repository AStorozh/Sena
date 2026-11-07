"""The shared "brain" of all agents: the model reasons, calls tools, sees the result, and reasons further.
Uses the Responses API: it's the only one where models can think and use tools at the same time.
The guards around the loop come from projects that run agents for hours (Hermes Agent, OpenClaw, both MIT): a model call
that failed for a passing reason is repeated after a pause instead of ending the task; a reply cut off at the length limit
is never executed; an agent that ends with words instead of its card is asked once more; a tool failing again and again,
and the last steps before the limit, are named to the model."""
import hashlib
import json
import random
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from sena import config
from sena.core import cancel, costs, events, llm, secrets, settings, status
from sena.core.cancel import Stopped
from sena.core.tools import Ended, Failure
from sena.core.tools import problems as _problems


SERVICE = ("check", "status", "next")   # the fields every call may carry beside its own: a self-check, the caption, the next step


def tool_problems(args, schema):
    # the service fields every call may carry are taken out before the call, so they never count
    if not schema:
        return []
    props = {k: v for k, v in (schema.get("properties") or {}).items() if k not in SERVICE}
    return _problems(args, {**schema, "properties": props, "required": [r for r in schema.get("required", []) if r in props]}, "вызов")

PATIENCE = (15, 30, 60)   # seconds before repeating a model call that failed for a passing reason (overload, network)
MOST_WAIT = 120           # a service asking to wait longer than this isn't waited for here
# a model overloaded or its server failing: the same question goes at once to its neighbour, and the rest of the run stays there
# (LangChain's model fallback) — before the waits and before the home model, which answers without tools
OVERFLOW = {config.MODEL_MAIN: config.MODEL_VOICE, config.MODEL_FAST: config.MODEL_VOICE, config.MODEL_VOICE: config.MODEL_MAIN}
# a refusal by a model with no fallback of its own on Anthropic's side (llm_claude.FALLBACK_MODELS): the same question once more
# on the next one up — a safety classifier sometimes refuses harmless work, and the owner chose (25.10.2026) the rare extra cost
# over a task that stops
REFUSED = {config.MODEL_FAST: config.MODEL_VOICE}
_switched = threading.local()   # .to — {model: the one this run moved to}
# calls one task may make of a tool that runs for long or costs much: past it the call isn't run, the rest of the turn is
# (LangChain's tool call limit) — a loop of coding jobs or videos ends with a sentence, not with the quota or the day's money
CAPS = {"claude_task": 4, "browser_task": 8, "computer_task": 8, "make_video": 3}
CAPPED = "Лимит «{name}» в этой задаче исчерпан ({most}). Не вызывай «{name}» снова; закончи остальным и скажи Теме, что осталось."
NUDGES = 2                # how many times an agent that answered in words instead of finish is asked to close its card
FAIL_WARN, FAIL_STOP = 3, 8   # one tool failing this many times in a row: a warning to change approach / the end of the run
LAST_STEPS = 3            # this many steps before the limit the model is told to bring what is nearly done to a checked result
MOST_CUTS = 2             # replies in a row cut at the length limit that are asked again; past it the run sums up

CUT_OFF = ("Твой прошлый ответ оборвался на пределе длины, вызовы инструментов в нём неполные и не выполнены. "
           "Повтори нужный шаг короче: меньше текста за один вызов, длинное — частями.")
EMPTY = "Ты закончила ход без ответа. Ответь Теме словами: что сделано и что получилось."
GO_ON = "Твой ответ оборвался на пределе длины. Продолжи ровно с места обрыва: без повторов, без вступления, без инструментов."
NARRATED = ("Ты ответила словами, но не закрыла задачу через finish. Если работа не закончена — сделай следующий шаг прямо сейчас, "
            "не описывай намерение. Если закончена или дальше нельзя — вызови finish: статус, итог и факт.")
LAST = ("[Служебно: осталось {left} шага из {total}. Доведи до проверяемого результата то, что почти готово, и закончи; "
        "нового не начинай. Не успеваешь всё — заверши проверенную часть и скажи, что осталось.]")
NEAR = 0.75   # of the task's money or the run's time: past it the model is told once to wrap up what is nearly done
NEARLY = ("[Служебно: потрачено больше трёх четвертей бюджета задачи или времени на неё. Доведи до проверяемого результата то, "
          "что почти готово, и закончи; нового не начинай.]")
FAILING = ("\n[Этот инструмент ошибся {n} раза подряд. Перечитай просьбу, назови себе свои допущения и смени способ или инструмент; "
           "не выходит — честно заверши с объяснением.]")
# tools that act on one thing (the browser's tab, the screen, the terminal): several of their calls in a turn run in order, not at once
SERIAL = {"browser_code", "look", "run_command", "click", "type_text", "press_key"}
# the owner's words that arrived while the task ran: named so the model doesn't take them for a tool's output or someone's text
# (Hermes Agent: with a bare "guidance" line models refused such messages as injected)
TOLD = "[Сообщение Темы, пришло сейчас, пока ты работаешь: это его собственные слова, не вывод инструмента. Учти их и продолжай.]\n"
UNOFFERED = "Инструмента «{name}» в этом ходе нет (выключен в интеграциях или его группа не открыта): открой группу через open_tools или сделай иначе."
FINISH_ALONE = "finish не выполнен: он был вместе с другими действиями. Посмотри их результаты и закончи отдельным вызовом finish."


def run(model, effort, instructions, input, tools, functions, max_steps=30, describe=None, name="ag_sena", verbosity="low", max_seconds=None):
    # name — who is working: the "Tasks" section shows the performer of each step.
    # max_seconds — the run's clock, all steps together: past it the run sums up like at the step limit (mini-swe-agent's wall time).
    # A tool keeps what it notes for the rest of its run in status.local only when it runs on the run's own thread (one call, or
    # calls in order); calls at once run on pool threads that get a copy (status.carried): state for the whole run lives in a
    # dict it owns, shared by reference
    previous = tuple(getattr(status.local, k, None) for k in ("who", "run", "told"))
    moved = getattr(_switched, "to", None)
    status.local.who, status.local.run = name, uuid.uuid4().hex[:6]   # every event of this run carries its id (Microsoft Agent Framework's run ids)
    _switched.to = {}
    try:
        try:
            result = think(model, effort, instructions, input, tools, functions, max_steps, describe, verbosity, max_seconds)
        except Stopped as e:   # stopped while waiting to repeat a model call
            events.log("stop", "остановлено по просьбе Темы")
            return Ended(str(e), "stopped")
        if name != "ag_sena":
            status.show("Готово")  # for the diagram in "Tasks": this agent has finished
        return result
    finally:
        (status.local.who, status.local.run, status.local.told), _switched.to = previous, moved


def _pause(seconds):
    # a wait that a stop interrupts at once
    end = time.time() + seconds
    while time.time() < end:
        cancel.check_stop()
        time.sleep(min(1.0, max(0.0, end - time.time())))


def respond(effort, **kwargs):
    """One model call that outlives a passing failure: overloaded, rate-limited, a server error, no network — the same call is
    repeated after 15, 30 and 60 s (about ±20 %, or as long as the service asked, up to two minutes) while the owner sees
    why it waits. A model call changes nothing in the world, so repeating it is always safe. An instant reply (effort
    "none": voice, the browser's quick path) doesn't wait. Hermes Agent's recovery ladder (turn_recovery_autorecover.py, MIT).
    A task the owner stopped makes no further call: a tool that saw the stop comes back as words, and the next paid turn
    (a whole one on Opus) would only answer them."""
    cancel.check_stop()
    waits = () if effort == "none" else PATIENCE
    moved = getattr(_switched, "to", None)
    if moved and kwargs.get("model") in moved:   # this run already moved off an overloaded model: no going back and forth
        kwargs["model"] = moved[kwargs["model"]]
    for attempt, wait in enumerate((*waits, None), 1):
        try:
            response = llm.respond(**kwargs)
        except Stopped:   # the owner's stop reached the call in flight (core/llm_claude.py): no neighbour, no repeat
            raise
        except Exception as e:
            instead = _overflow(kwargs, e) if attempt == 1 else None
            if instead is not None:
                return _read_pages(instead)
            asked = llm.retry_after(e) or 0
            if wait is None or not llm.transient(e) or type(e).__name__ == "APITimeoutError" or asked > MOST_WAIT:
                raise   # a timeout the client has already repeated, or a wait longer than ours: more tries multiply the wait and the bill (LiteLLM)
            wait = min(max(wait, asked), MOST_WAIT) * random.uniform(0.8, 1.2)
            why = llm.explain(e)
            events.log("retry", f"модель не ответила ({why}), повторю через {round(wait)} с", attempt=attempt, error=type(e).__name__)
            status.show(f"Модель не ответила ({why}), повторю через {round(wait)} с", tool="wait")
            _pause(wait)
            status.tool_done()
        else:
            return _read_pages(_unrefused(kwargs, response))


def _read_pages(response):
    """The reply as it is; one whose model read pages it found itself (the built-in web search) marks the task: foreign text like
    any other. Marked here, on every reply respond() gives — a run that ends on time, a summed-up end or the rest of a cut answer
    read pages too, and the mark used to wait for the next step of the loop, which they never reached."""
    if getattr(response, "searched", False) or any(getattr(o, "type", None) == "web_search_call" for o in getattr(response, "output", None) or []):
        status.touched("поиск в интернете")
    return response


def _unrefused(kwargs, response):
    """The answer itself, or — when a model without a fallback of its own refused — its neighbour's answer to the same question;
    the rest of the run stays on the neighbour, so the same page doesn't meet the same refusal at every step."""
    model = kwargs.get("model")
    partner = REFUSED.get(model)
    if not partner or getattr(response, "stop_reason", None) != "refusal":
        return response
    try:
        again = llm.respond(**{**kwargs, "model": partner})
    except Stopped:
        raise
    except Exception as e:   # the neighbour failed: the refusal stands, as before
        events.error(f"Повтор отказа на {partner}", e)
        return response
    if isinstance(getattr(_switched, "to", None), dict):
        _switched.to[model] = partner
    events.log("retry", f"{model} отказалась отвечать: повторила на {partner}", fallback=partner, refused=True)
    return again


def _crowded(e):
    # the model itself is overloaded or its server failed — not the network, the money or the request
    code = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
    return (code == 529 or isinstance(code, int) and code >= 500 or type(e).__name__ in ("OverloadedError", "InternalServerError")
            or "overloaded" in str(e).lower())


def _overflow(kwargs, e):
    """The answer of the overloaded model's neighbour, asked at once, or None. When it answers, the rest of this run stays on it."""
    model = kwargs.get("model")
    partner = OVERFLOW.get(model)
    if not partner or not _crowded(e) or not llm.transient(e):
        return None
    try:
        response = llm.respond(**{**kwargs, "model": partner})
    except Stopped:
        raise
    except Exception as again:
        events.error(f"Запасная модель {partner} тоже не ответила", again)
        return None
    if isinstance(getattr(_switched, "to", None), dict):
        _switched.to[model] = partner
    events.log("retry", f"{model} перегружена ({llm.explain(e)}): продолжаю на {partner}", fallback=partner, error=type(e).__name__)
    status.show(f"Основная модель перегружена — продолжаю на запасной ({partner})", tool="wait")
    return response


def _cut_off(response):
    # the reply stopped at the length limit: whatever tool calls it holds are unfinished (Claude: stop_reason, OpenAI: incomplete)
    return (getattr(response, "stop_reason", None) in ("max_tokens", "model_context_window_exceeded")   # the window filled up: cut the same way
            or getattr(getattr(response, "incomplete_details", None), "reason", None) == "max_output_tokens")


VOLATILE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?|\b\d{1,2}:\d{2}(:\d{2})?\b|\b\d+(\.\d+)? ?(с|сек|мс|s|ms)\b")


def _same(output):
    """A tool's result for the loop guard, whole: times and durations differ from call to call and would hide that nothing
    changed; a picture counts by all of it (two screens alike at the top are two screens, not a repeat)."""
    if isinstance(output, list):
        output = [{**p, "text": VOLATILE.sub("#", str(p.get("text") or ""))} if isinstance(p, dict) and p.get("type") == "input_text" else p
                  for p in output]
        text = json.dumps(output, ensure_ascii=False, sort_keys=True, default=str)
    else:
        text = VOLATILE.sub("#", str(output))
    return hashlib.sha1(text.encode()).hexdigest()


def _clean(result):
    """What a tool answered, as the model takes it — text, parts (a picture with its words) or a card — with every known secret
    masked, and the words for the trace and the journal. Anything else a tool returns (None, a number, a dict, a list of
    words) is taken as its text: a tool that forgot to say anything must not bring the run down."""
    if isinstance(result, dict) and "finish" in result:
        card = {k: secrets.mask(v) if isinstance(v, str) else v for k, v in result["finish"].items()}
        return {"finish": card}, json.dumps(card, ensure_ascii=False, default=str)
    if isinstance(result, list) and result and all(isinstance(p, dict) for p in result):
        # words next to a picture (a command with its screenshot) are masked the same way
        result = [{**p, "text": secrets.mask(str(p.get("text") or ""))} if p.get("type") == "input_text" else p for p in result]
        words = " ".join(str(p.get("text") or "") for p in result).strip()
        opened = [p["tool_name"] for p in result if p.get("type") == "tool_reference"]
        return result, words or (f"открыты инструменты: {', '.join(opened)}" if opened else "(картинка)")
    if isinstance(result, (dict, list)):
        result = json.dumps(result, ensure_ascii=False, default=str)
    elif not isinstance(result, str):
        result = "" if result is None else str(result)
    result = secrets.mask(result)
    return result, result


def _toolset(tools, opened, claude):
    """What goes to the model. A tool marked defer waits in its group until open_tools: Claude gets every tool declared and
    loads an opened one by reference (the cached prefix stays the same); other models get only what is open so far."""
    if claude:
        return tools
    return [{k: v for k, v in t.items() if k != "defer"} for t in tools if not t.get("defer") or t.get("name") in opened]


def _opened(result, declared, claude):
    """A tool that opens others (open_tools) returns {"text", "open"}: (its output, the names it may now use, rules to add after
    the outputs). Claude takes only references inside a tool result, so the group's rules follow the results as a note."""
    names = [n for n in result.get("open", []) if n in declared]
    text = result.get("text", "")
    if claude and names:
        return [{"type": "tool_reference", "tool_name": n} for n in names], names, text
    return text, names, ""


class _Run:
    """One run of the loop: what it was given, and what it keeps from step to step."""

    def __init__(self, model, effort, instructions, tools, functions, max_steps, describe, verbosity, max_seconds):
        self.effort, self.tools, self.functions, self.describe = effort, tools, functions, describe
        self.max_steps, self.max_seconds = max_steps, max_seconds
        self.claude = llm.is_claude(model)
        self.began = time.monotonic()   # the run's clock counts awake time: a Mac asleep mid-task doesn't use it up (ralph-harness)
        self.declared = {t.get("name") for t in tools if t.get("type") == "function"}
        self.specs = {t["name"]: t.get("parameters") or {} for t in tools if t.get("type") == "function"}
        self.opened, self.rules = set(), []   # the groups open_tools opened so far, and their rules for the note after the outputs
        self.lock = threading.Lock()          # for those and the caps: the calls of one step may run at once
        self.options = dict(model=model, reasoning={"effort": effort}, instructions=instructions, tools=_toolset(tools, self.opened, self.claude),
                            text={"verbosity": verbosity})
        self.repeats = {}   # (tool, its arguments) -> (its last result, how many times in a row it came back the same): against looping
        self.capped = {}    # CAPS: calls of each tool so far in this run
        self.streak = {}    # tool -> how many of its calls in a row failed
        self.task = cancel.current_task()   # the owner's task this run works for, whichever thread runs it
        # an agent a tool starts begins after the owner's words its caller has already seen: they shaped its instructions
        self.told = getattr(status.local, "told", None) or 0
        self.nudged, self.asked_empty, self.cuts, self.warned = 0, False, 0, False

    def ask(self, input, **extra):
        # the run's next model call: the same model, instructions and tools (respond repeats it after a passing failure)
        return respond(self.effort, input=input, **extra, **self.options)


def think(model, effort, instructions, input, tools, functions, max_steps, describe, verbosity, max_seconds=None):
    run = _Run(model, effort, instructions, tools, functions, max_steps, describe, verbosity, max_seconds)
    response = run.ask(input)
    for step in range(max_steps):
        if cancel.is_cancelled():
            events.log("stop", "остановлено по просьбе Темы")
            return Ended("Остановлено по просьбе Темы", "stopped")
        if max_seconds and time.monotonic() - run.began > max_seconds:
            return _wrap_up(response, run.options, f"Время на задачу вышло ({round(max_seconds / 60)} мин)", functions)
        calls = [o for o in response.output if o.type == "function_call"]
        run.cuts = run.cuts + 1 if calls and _cut_off(response) else 0
        if run.cuts > MOST_CUTS:   # it keeps writing too much at once: sum up instead of asking again (opencode stops after two)
            return _wrap_up(response, run.options, "Ответ раз за разом обрывался на пределе длины", functions)
        if run.cuts:   # half a call is never run: a cut message or a cut file would go out as if whole
            response = _again_shorter(run, response, calls)
            continue
        if not calls:
            done, value = _final(run, response)
            if done:
                return value
            response = value
            continue
        ran = _run_calls(run, calls)
        outputs = [out for out, _ in ran]
        card = next((o["output"] for o in outputs if isinstance(o["output"], dict) and "finish" in o["output"]), None)
        if card:   # the agent closed its card (agents/common.finish): its last word, no extra turn to restate it
            return card
        ended = _guarded(run, calls, ran)
        if ended is not None:
            return ended
        _notes(run, outputs, step)
        ended = _budget(run, response, outputs)
        if ended is not None:
            return ended
        # continue the same train of thought: the model remembers its reasoning via previous_response_id
        response = run.ask(outputs, previous_response_id=response.id)
    return _after_the_last_step(run, response)


def _again_shorter(run, response, calls):
    # a reply cut at the length limit: its half-written calls are answered as not run, and the step is asked for again, shorter
    events.log("stop", "ответ модели оборвался на пределе длины, вызовы не выполнены", calls=[c.name for c in calls])
    items = [{"type": "function_call_output", "call_id": c.call_id, "output": "Не выполнено: ответ оборвался."} for c in calls]
    return run.ask(items + [{"role": "developer", "content": CUT_OFF}], previous_response_id=response.id)


def _final(run, response):
    """A reply without calls: (True, the run's answer), or (False, the model's next reply) when it is asked once more — for
    words instead of an empty end, or for the card an agent forgot to close."""
    if getattr(response, "stop_reason", None) == "refusal":   # a refusal isn't argued with: asking again the same way can't help
        return True, response.output_text
    if not (response.output_text or "").strip() and not run.asked_empty:   # an empty final would reach the owner as a stock phrase
        run.asked_empty = True
        return False, run.ask([{"role": "developer", "content": EMPTY}], previous_response_id=response.id, tool_choice="none")
    if _cut_off(response) and "finish" not in run.functions:   # a final answer cut at the length limit: the rest once more, joined (Cline)
        return True, (response.output_text or "") + _rest(run, response)
    if "finish" in run.functions and run.nudged < NUDGES:   # an agent described its next step instead of doing it, or forgot its card
        run.nudged += 1
        events.log("step", "агент ответил словами без finish, просим закончить", who=getattr(status.local, 'who', None))
        return False, run.ask([{"role": "developer", "content": NARRATED}], previous_response_id=response.id)
    return True, response.output_text


def _rest(run, response):
    # the rest of a final answer cut at the length limit; without it the start is still an answer: better cut than none
    try:
        return run.ask([{"role": "developer", "content": GO_ON}], previous_response_id=response.id, tool_choice="none").output_text or ""
    except Stopped:
        raise
    except Exception as e:
        events.error("Продолжение оборванного ответа", e)
        return ""


def _run_calls(run, calls):
    """The calls of one step: [(output, failed)] in the model's order. One call, or the calls of one screen, browser or terminal,
    run here one after another; different tools run at once (browser and computer), each on a pool thread with this one's
    status: the task, who works, the tab, the turn."""
    offered = run.declared if run.claude else {t.get("name") for t in run.options["tools"] if t.get("type") == "function"}
    alone = len(calls) == 1

    def execute(call):
        return _execute(run, call, offered, alone)
    names = [c.name for c in calls]
    status.local.told = run.told   # an agent one of these calls starts begins after what this run has already heard
    if alone or len(set(names)) < len(names) or SERIAL & set(names):
        return [execute(c) for c in calls]
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(status.carried(execute), calls))


def _reply(call, output):
    return {"type": "function_call_output", "call_id": call.call_id, "output": output}


def _execute(run, call, offered, alone):
    """One call: (its output for the model, whether it failed). It goes to the journal below (private for ag_ears), not to the
    process's output: a print of every call's arguments put the facts of overheard speech into bot.log in clear."""
    started, args, failed = time.time(), {}, False
    if call.name not in offered or call.name not in run.functions:   # a switched-off tool is not run, whatever the model writes
        return _reply(call, UNOFFERED.format(name=call.name)), True
    if call.name == "finish" and not alone:   # the card closes after the other results are seen, not blind (smolagents)
        return _reply(call, FINISH_ALONE), False
    who = getattr(status.local, "who", None)
    try:
        cancel.check_stop()
        args = json.loads(call.arguments)
        if not isinstance(args, dict):
            raise ValueError("аргументы вызова — объект JSON с полями инструмента. Исправь вызов.")
        check = args.pop("check", None)
        if check:
            status.trace(f"{who or 'ag_sena'} проверка: {check[:300]}")
            events.log("check", check, private=who == "ag_ears")
        said, upcoming = args.pop("status", None), args.pop("next", None)
        wrong = tool_problems(args, run.specs.get(call.name))
        if wrong:   # a call that doesn't fit its description never runs: the model fixes it first
            raise ValueError("аргументы не по описанию инструмента: " + "; ".join(wrong[:5]) + ". Исправь вызов.")
        if _over_cap(run, call.name):   # not a failure: the streak guard and the review don't count a limit reached as a broken tool
            return _reply(call, CAPPED.format(name=call.name, most=CAPS[call.name])), False
        # show Tema what's happening right now, and the agent's own guess at what comes next (if it knows)
        status.show(said or (run.describe(call.name, args) if run.describe else None), upcoming=upcoming, tool=call.name)
        try:
            result = run.functions[call.name](**args)
        finally:
            status.tool_done()
        failed = isinstance(result, Failure)   # the tool says itself that the call didn't do its job (core/tools.py)
        if isinstance(result, dict) and "open" in result:
            result = _opening(run, result)
        # a secret that leaked into a tool's output never reaches the model, the trace or the journal
        result, shown = _clean(result)
    except Stopped as e:
        result = shown = str(e)
    except Exception as e:
        result = shown = secrets.mask(f"Ошибка: {e}")  # hand the error to the model, let it think and try differently
        failed = True
        events.error(f"Инструмент {call.name}", e)
    if failed:
        status.failed()
    status.trace(f"{who or 'ag_sena'}: {call.name} {call.arguments[:400]} -> {shown[:400]}")
    events.log("tool", shown, name=call.name, args=args or call.arguments, seconds=round(time.time() - started, 1), failed=failed or None,
               private=who == "ag_ears")   # facts about someone else's speech go to the same place as the conversation itself (llm.py)
    out = _reply(call, result)
    if failed and run.claude:   # Claude learns from the error channel that the call failed (pydantic-ai); OpenAI's input has no such field
        out["is_error"] = True
    return out, failed


def _over_cap(run, name):
    """Whether the task has used up its calls of a tool that runs long or costs much (CAPS). Asked only for a call that would
    run: one that doesn't fit its description doesn't spend the task's slot."""
    if name not in CAPS:
        return False
    with run.lock:
        over = run.capped.get(name, 0) >= CAPS[name]
        run.capped[name] = run.capped.get(name, 0) + (0 if over else 1)
    if over:
        events.log("tool", f"лимит {name} в задаче", name=name, capped=CAPS[name])
    return over


def _opening(run, result):
    # open_tools: the tools it opened join the run's toolset from the next step, its groups' rules the note after the outputs
    result, names, note = _opened(result, run.declared, run.claude)
    with run.lock:
        run.opened.update(names)
        run.rules.extend([note] if note else [])
    return result


def _guarded(run, calls, ran):
    """The loop guard and the failure streak over a step's calls: an ending when the run goes round in circles or a tool keeps
    failing, otherwise None — a warning goes into the output past the first repeats and failures."""
    for call, (out, failed) in zip(calls, ran):
        try:   # the service fields are free words that differ from call to call: a repeat is the same action, however it's captioned (Cline)
            args = {k: v for k, v in json.loads(call.arguments or "{}").items() if k not in SERVICE}
            key = (call.name, json.dumps(args, sort_keys=True))
        except (ValueError, AttributeError):   # the model sent broken JSON or not an object: the raw string is the key
            key = (call.name, call.arguments)
        seen, times = run.repeats.get(key, (None, 0))
        result = _same(out["output"])
        times = times + 1 if result == seen else 1   # in a row: a different result in between starts the count again
        run.repeats[key] = (result, times)
        if times >= 5:
            events.log("stop", f"зациклилась: пять раз подряд {call.name} с тем же результатом")
            return Ended("Остановилась: пять раз подряд получала один и тот же результат, так не выйдет. Нужен другой подход или уточнение.", "looping")
        if times >= 3 and isinstance(out["output"], str):
            out["output"] += ("\n[Ты уже несколько раз делаешь то же самое с тем же результатом. "
                              "Это не работает: смени подход или честно заверши с объяснением.]")
        run.streak[call.name] = run.streak.get(call.name, 0) + 1 if failed else 0   # the same tool failing with different arguments
        if run.streak[call.name] >= FAIL_STOP:
            events.log("stop", f"инструмент {call.name} ошибся {FAIL_STOP} раз подряд")
            return Ended(f"Остановилась: инструмент раз за разом ошибается ({FAIL_STOP} раз подряд), так не выйдет. Последняя ошибка: {str(out['output'])[:300]}", "failing")
        if run.streak[call.name] >= FAIL_WARN and isinstance(out["output"], str):
            out["output"] += FAILING.format(n=run.streak[call.name])
    return None


def _notes(run, outputs, step):
    """What follows a step's outputs: the tools of the groups just opened and their rules, a word that the step limit is near,
    and the owner's words that came meanwhile."""
    run.options["tools"] = _toolset(run.tools, run.opened, run.claude)   # the groups opened on this step are there on the next
    if run.rules:   # the rules of the groups just opened, right after the tool results (Sena's own text, not the owner's)
        outputs.append({"role": "developer", "content": "Правила открытых групп инструментов:\n\n" + "\n\n".join(run.rules)})
        run.rules.clear()
    if run.max_steps >= 2 * LAST_STEPS and run.max_steps - step - 1 == LAST_STEPS:   # once: the limit is near, finish what's nearly done
        outputs.append({"role": "developer", "content": LAST.format(left=LAST_STEPS, total=run.max_steps)})
    told = status.heard(run.task, run.told)   # the owner wrote to this task while it worked: his words go in before the next step
    run.told += len(told)
    if told:
        outputs.append({"role": "user", "content": TOLD + "\n".join(told)})


def _budget(run, response, outputs):
    """The task's money and the run's clock after a step: past NEAR of either, once, a word to finish what is nearly done
    (AG2's alert); past the task's whole budget, an ending made from what this step's calls returned — otherwise None."""
    tid = (status.current() or {}).get("id")
    brake = settings.get("task_budget_usd") or 0
    late = (brake and tid and costs.task_spent(tid) >= NEAR * brake) or (run.max_seconds and time.monotonic() - run.began >= NEAR * run.max_seconds)
    if late and not run.warned:   # once, before the hard stop: finish what is nearly done instead of starting more
        run.warned = True
        outputs.append({"role": "developer", "content": NEARLY})
    if tid and brake and costs.task_spent(tid) >= brake:   # one task, however long, never spends past this: it sums up and stops
        events.log("stop", f"бюджет задачи исчерпан: {costs.task_spent(tid):.2f} из {brake} $")
        # the calls of this step did run: the summary is made from what they really returned (an order number, a bought ticket)
        return _wrap_up(response, run.options, f"Бюджет задачи исчерпан ({costs.task_spent(tid):.2f} из {brake} $)", run.functions, outputs)
    return None


def _after_the_last_step(run, response):
    """The reply to the last step the run had: its words, its card, or one more turn that sums up what's done and what's left."""
    last = [o for o in response.output if o.type == "function_call"]
    if not last:
        return response.output_text   # on the last step the model already answered in words: that is the answer
    if len(last) == 1 and last[0].name == "finish" and "finish" in run.functions:   # it closed its card with the last step it had
        try:
            return _card(run.functions, last[0].arguments, run.specs.get("finish"))
        except (TypeError, ValueError) as e:
            events.error("Карточка на последнем шаге", e)
    return _wrap_up(response, run.options, functions=run.functions)


def _card(functions, arguments, schema=None):
    """The card at the very end of a run: no turn is left to answer a refused card (agents/common.finisher), so a refused one
    goes as partial; its secrets are masked here as on any step. The service fields (check, status, next) and anything else
    finish doesn't take are left out, as _execute leaves them on any step: a card with the "check" an agent's other tools
    require was a TypeError here, and its checked "done" never reached the coordinator."""
    args = json.loads(arguments or "{}")
    if not isinstance(args, dict):
        raise ValueError("аргументы finish — объект JSON")
    known = (schema or {}).get("properties") or {}
    args = {k: v for k, v in args.items() if k not in SERVICE and (not known or k in known)}
    card = functions["finish"](**args)
    if not (isinstance(card, dict) and "finish" in card):
        card = functions["finish"](**{**args, "outcome": "partial"})
    return {"finish": {k: secrets.mask(v) if isinstance(v, str) else v for k, v in card["finish"].items()}} if isinstance(card, dict) and "finish" in card else card


WRAP_UP = ("Лимит шагов исчерпан. Не вызывай инструменты. Коротко и по фактам: что уже сделано и подтверждено, "
           "где всё остановилось (адрес, состояние), что осталось сделать. По этому можно будет продолжить работу.")


CLOSE_CARD = ("{why}. Сразу вызови finish: статус partial или failed (done — только если всё проверено), в итоге — что сделано, "
              "где остановилась (адрес, состояние) и что осталось. Другие инструменты не вызывай.")


def _ended(why, summary=""):
    return Ended(("Остановилась: шаги кончились." if why.startswith("Лимит шагов") else f"Остановилась: {why[0].lower() + why[1:]}.")
                 + (" " + summary if summary else ""), "limit")


def _wrap_up(response, options, why="Лимит шагов исчерпан", functions=None, outputs=None):
    """Don't give up silently: one last turn — what's done and what's left. An agent with a card closes it (finish) instead of
    leaving prose its coordinator can only take as unverified (gemini-cli's final turn that must call complete_task).
    outputs — what the calls of response returned when they did run (the money ran out after them): the summary is made from
    their real results; without them the calls are answered as not run. A stop of the owner's goes through, not summed up."""
    events.log("stop", why.lower())
    summary = ""
    try:
        items = list(outputs) if outputs is not None else [{"type": "function_call_output", "call_id": c.call_id, "output": f"{why}, инструмент не выполнен."}
                                                           for c in response.output if c.type == "function_call"]
        if functions and "finish" in functions:
            items.append({"role": "developer", "content": CLOSE_CARD.format(why=why)})
            last = respond(options["reasoning"]["effort"], input=items, previous_response_id=response.id, **options)
            card = next((c for c in last.output if c.type == "function_call" and c.name == "finish"), None)
            if card:
                try:
                    spec = next((t.get("parameters") for t in options.get("tools") or [] if t.get("name") == "finish"), None)
                    return _card(functions, card.arguments, spec)
                except (TypeError, ValueError) as e:
                    events.error("Карточка при лимите шагов", e)
            return _ended(why, (last.output_text or "").strip())
        items.append({"role": "developer", "content": WRAP_UP.replace("Лимит шагов исчерпан", why)})
        summary = respond(options["reasoning"]["effort"], input=items, previous_response_id=response.id, tool_choice="none", **options).output_text.strip()
    except Stopped:
        raise
    except Exception as e:
        events.error("Итог при лимите шагов", e)
    return _ended(why, summary)
