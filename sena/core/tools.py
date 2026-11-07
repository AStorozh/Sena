"""Tool descriptions for the model (Responses API format) and fields shared by all agents."""
import base64

# self-check field: at each step the model assesses whether the previous step succeeded (as in browser-use)
CHECK = {"type": "string", "description": "Удался ли прошлый шаг и что это значит, коротко по фактам. На первом шаге — что нужно получить"}
LOOK_STATUS = {"type": "string", "description": "Что и для чего проверяешь, конкретно (куда смотришь, что ищешь), 3-8 слов по-русски"}
# the agent's own best guess at what comes after this call: shown as a dashed "planned next" line in the app, not a promise
NEXT = {"type": "string", "description": "Если уже понятно наперёд: что сделаешь следующим шагом, 2-6 слов по-русски. "
                                          "Если пока не ясно — оставь пустым, не придумывай."}


class Failure(str):
    """A tool's answer saying the call didn't do its job — nothing to act on, a refusal, code that raised, an agent's failed
    card. The model reads plain text as before; the loop counts it as a failure (the streak guard, the journal, Claude's error
    channel, the post-task review) instead of guessing from the words. crewAI's ToolFailure (tools/tool_failure.py, MIT): a
    call that "worked" can still have failed, and detection is declared by the tool, never read from a string.
    str methods (+, replace, slicing) return a plain str: wrap the final text, not a part of it."""


class Ended(str):
    """A run of the loop that ended before its work did, and why: "stopped" (the owner's stop), "looping" (the same result again
    and again), "failing" (a tool failing again and again), "limit" (steps, time, the task's money or cut answers ran out —
    whatever was finished is in the text). A delegation's card is made from the reason, not guessed from the words (A2A: every
    end is a terminal state with a name). Plain text for everyone else."""

    def __new__(cls, text, why):
        ended = super().__new__(cls, text)
        ended.why = why
        return ended


def tool(name, description, props=None, required=None):
    parameters = {"type": "object", "properties": props or {}}
    if required:
        parameters["required"] = list(required)
    return {"type": "function", "name": name, "description": description, "parameters": parameters}


def string(description=None):
    return {"type": "string", "description": description} if description else {"type": "string"}


def strings(description=None):
    spec = {"type": "array", "items": {"type": "string"}}
    return {**spec, "description": description} if description else spec


def clamp(value, default, low, high):
    """A whole number a caller gave (how many, how long), kept within low..high; empty or garbage ("пять", None) gives the default."""
    try:
        return max(low, min(int(float(value)), high))
    except (TypeError, ValueError, OverflowError):
        return default


def doing(example):
    # what the agent is doing right now — shown to Tema in the app: concretely where and what, not only the kind of action
    return {"type": "string", "description": f"Что сейчас делаешь, для Темы: конкретно куда и что именно, 3-8 слов по-русски. Например: {example}"}


_TYPES = {"string": str, "boolean": bool, "object": dict, "array": list}


def problems(value, schema, where="ответ"):
    """What in a value doesn't fit its JSON schema, in words for the model (an empty list — it fits): missing required fields,
    unknown ones where the schema lists them, wrong types, values outside an enum. A small checker for the shapes Sena's
    tools and narrow jobs use, so a wrong call is fixed by the model before it acts (pydantic-ai checks every call)."""
    out = []
    kind = schema.get("type")
    if isinstance(kind, list):   # one of several types ("string" or "null")
        return [] if any(not problems(value, {**schema, "type": k}, where) for k in kind) else [f"{where}: ждали одно из: {', '.join(kind)}"]
    if kind == "null":
        return [] if value is None else [f"{where}: ждали пусто (null)"]
    if kind == "object" or (kind is None and "properties" in schema):
        if not isinstance(value, dict):
            return [f"{where}: ждали объект"]
        props = schema.get("properties") or {}
        out += [f"{where}: нет обязательного поля «{k}»" for k in schema.get("required", []) if k not in value]
        if props and schema.get("additionalProperties") is not True:
            out += [f"{where}: лишнее поле «{k}» (есть: {', '.join(props)})" for k in value if k not in props]
        for k, v in value.items():
            if k in props and v is not None:
                out += problems(v, props[k], f"{where}.{k}" if where != "ответ" else k)
        return out
    if kind == "array":
        if not isinstance(value, list):
            return [f"{where}: ждали список"]
        for i, item in enumerate(value[:50]):
            out += problems(item, schema.get("items") or {}, f"{where}[{i}]")
        return out
    if kind in ("integer", "number"):
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and (kind == "number" or float(value).is_integer())
        if not ok:
            return [f"{where}: ждали {'целое ' if kind == 'integer' else ''}число, пришло {value!r:.40}"]
    elif kind in _TYPES and not isinstance(value, _TYPES[kind]):
        return [f"{where}: ждали {kind}, пришло {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{where}: «{str(value)[:40]}» не из списка: {', '.join(map(str, schema['enum']))}")
    return out


def image(data, mime="image/jpeg"):
    return {"type": "input_image", "image_url": f"data:{mime};base64," + base64.b64encode(data).decode()}
