"""Inside one benchmark run (a process with its own SENA_DATA copy): the model under test takes Sena's place, nothing goes
out to Telegram, no fallback hides the model's failure, and the run's spending is read from its own cost journal."""
import json
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))   # the project root

from sena import config  # noqa: E402
from sena.core import costs, llm_claude, settings  # noqa: E402
from sena.telegram import api  # noqa: E402

STARTED = time.time()


def _refuse(*args, **kwargs):
    raise RuntimeError("замер: в Телеграм ничего не уходит")


def setup(model, slot):
    """slot "main" — the brain's place (MODEL_MAIN); "fast" — agents, the judge, narrow jobs, voice commands (MODEL_FAST)."""
    if slot == "main":
        config.MODEL_MAIN = model
    else:
        config.MODEL_FAST = model
    llm_claude.role_of = lambda m: "brain"   # every Claude model under test on the one key there is
    api.post, api._upload = _refuse, _refuse
    settings.update({"judge": False, "reflect": False})
    _lowest_effort()


def _lowest_effort():
    """Some OpenAI models refuse reasoning effort "none" (GPT-6.1 Sol takes low..max): then the call is repeated at their lowest
    level, as Claude's bridge does, so the model is measured, not our setting. Each such repeat is counted."""
    from sena.core import llm
    real = llm._api

    def api():
        client = real()
        create = client.responses.create

        def patched(**kwargs):
            try:
                return create(**kwargs)
            except Exception as e:
                if "reasoning.effort" in str(e) and (kwargs.get("reasoning") or {}).get("effort") == "none":
                    LOWERED.append(kwargs.get("model"))
                    return create(**{**kwargs, "reasoning": {**kwargs["reasoning"], "effort": "low"}})
                raise
        return type("Client", (), {"responses": type("Responses", (), {"create": staticmethod(patched)})(), "__getattr__": lambda s, n: getattr(client, n)})()
    llm._api = api


LOWERED = []


def rows():
    return [r for r in costs._all_rows(since=STARTED)]


def usage(since, until=None):
    """Money, tokens and calls of the model calls between two moments (the cost journal of this run)."""
    picked = [r for r in costs._all_rows(since=since, until=until) if r["provider"] in ("openai", "anthropic")]
    units = {"input": 0, "cached": 0, "output": 0}
    for r in picked:
        u = r.get("units") or {}
        units["input"] += u.get("input", 0)
        units["cached"] += u.get("cached", 0) + u.get("cache_read", 0)
        units["output"] += u.get("output", 0)
    return {"usd": round(sum(r.get("usd") or 0 for r in picked), 6), "calls": len(picked), **units}


def save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=str)
