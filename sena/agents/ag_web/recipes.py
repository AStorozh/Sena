"""The fast path: ready-made, tested code instead of the model reasoning step by step.
If a skill contains a code recipe (run(page, ...)) that fully solves the task, ag_web skips step-by-step thinking:
one short call picks the recipe and its parameters (core/roles.py, role "router": in the cloud until the home model gives the
same answers, its answers kept as examples), then the code just runs. If it fails, the regular agent solves the task
and learns that the recipe is broken (the post-task review will rewrite it)."""
import json

from sena.agents.ag_web import browser, guard, toolkit
from sena.core import cancel, events, roles, sandbox, status
from sena.core.cancel import Stopped
from sena.memory import rules, skills

ROUTER = """Ты быстрый маршрутизатор браузерного агента Сены. Дана задача Темы, его стоящие правила (если есть) и список готовых
проверенных рецептов: код, который выполнится без рассуждений и без проверки правил на лету.
Выбери рецепт, только если он целиком решает именно эту задачу: тот же сайт и то же действие, ничего лишнего, и ни одно правило
Темы ему не мешает — если правило может быть важно именно здесь, лучше null: задачу решит обычный агент, который правила видит
на каждом шаге.
В сомнении — null: тогда задачу решит агент.
Параметры бери из текста задачи как есть (запрос, адрес), ничего не выдумывай. Нужного параметра в задаче нет — recipe null: задачу решит агент.
Ответь только JSON: {"recipe": "имя" или null, "args": {"параметр": "значение"}}"""
SCHEMA = {"type": "object", "properties": {"recipe": {"type": ["string", "null"]}, "args": {"type": "object"}}, "required": ["recipe", "args"]}


def _route(task, recipes):
    listing = [{k: r[k] for k in ("name", "when", "signature", "doc")} for r in recipes]
    # only the names offered, or null: a made-up name can't be generated at all (Ollama's advice: tight schemas for small models)
    schema = {**SCHEMA, "properties": {**SCHEMA["properties"], "recipe": {"type": ["string", "null"], "enum": [r["name"] for r in recipes] + [None]}}}
    standing = rules.listing()
    text = f"Задача: {task}\n\nРецепты:\n{json.dumps(listing, ensure_ascii=False, indent=1)}" + (f"\n\nПравила Темы:\n{standing}" if standing else "")
    try:
        choice = roles.ask("router", ROUTER, text, schema, purpose="быстрый путь браузера", max_tokens=300, effort="none")
    except ValueError:   # not JSON: let the agent think it through
        return None
    if not isinstance(choice, dict):
        return None
    recipe = next((r for r in recipes if r["name"] == choice.get("recipe")), None)
    args = choice.get("args") or {}
    if not recipe or not isinstance(args, dict):
        return None
    # a parameter the task doesn't have comes as null or empty: it is missing, not a value — run(page, query=None) played «None»
    args = {k: v for k, v in args.items() if v is not None and v != ""}
    if set(args) - set(recipe["params"]) or set(recipe["required"]) - set(args):
        return None   # a made-up or missing parameter: let the agent think it through
    return recipe, args


def _execute(source, args):
    # on the browser thread: run the recipe's code on the task's tab; (succeeded, result or reason)
    env = toolkit.helpers(browser.get_page())
    try:
        scope = sandbox.load(source, env)   # the same fences as the agent's own code: a recipe is code a model once wrote
        with guard.replaying():   # no one asks the owner on this path: an irreversible click is refused, the agent takes over
            result = scope["run"](scope["page"], **args)
        if result is None or not str(result).strip():   # nothing came back: a broken page reads like "no data" (Firecrawl's rule)
            return False, "рецепт ничего не вернул: проверь, сделано ли, — не считаю это успехом"
        return True, str(result)
    except Stopped:
        raise
    except BaseException as e:   # BaseException, so that an exit() inside a recipe doesn't shut down the bot
        events.error("Рецепт в браузере", e)
        return False, f"{type(e).__name__}: {e}"


def try_fast(task):
    """(answer, None) — the recipe handled it; (None, note) — it didn't, a note for the agent; (None, None) — there are no recipes."""
    recipes = skills.code_recipes()
    if not recipes:
        return None, None
    try:
        choice = _route(task, recipes)
    except Exception as e:
        events.error("Маршрутизатор быстрого пути", e)
        return None, None
    if not choice:
        return None, None
    recipe, args = choice
    cancel.check_stop()
    status.show(f"Рецепт «{recipe['name']}»")
    ok, text = browser.in_browser(_execute, recipe["source"], args)
    status.trace(f"ag_web: рецепт {recipe['name']} {args} -> {'сработал' if ok else 'ошибка'}: {text[:300]}")
    events.log("recipe", text, name=recipe["name"], args=args, failed=not ok or None)
    skills.used(recipe["name"], "ran" if ok else "failed")   # a recipe that works is alive; one that keeps failing leaves the fast path
    if ok:
        return f"Готово по рецепту «{recipe['name']}»: {text}", None
    return None, (f"Быстрый путь по рецепту «{recipe['name']}» не сработал: {text[:300]}. "
                  "Реши задачу сам; найдёшь способ надёжнее — разбор после задачи обновит рецепт.")
