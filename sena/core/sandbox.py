"""Running code that a model wrote (browser steps, site recipes, reading Telegram) inside Sena's own process.
Three fences: the code is checked before it runs (no imports outside a short list, no names or attributes that start with
an underscore, no reflection helpers), it gets a short set of builtins without files, processes or the environment, and the
modules it may use are handed over as plain sets of their public functions, not as modules. Whatever still leaks out is wiped
by secrets.mask on the way out (core/safety._StdoutRouter). A Python sandbox is never airtight: keys are kept out of the
environment (config), the dangerous agents need the owner's "yes" after someone else's text (the task's mark, core/status.py),
and anything irreversible is confirmed by the owner anyway."""
import ast
import builtins
import functools
import importlib
import io
import time
import traceback
import types

from sena.core import cancel, safety
from sena.core.cancel import Stopped
from sena.core.tools import Failure

TICK = "sena_tick"   # called at the top of every loop and function the code has, and for each item a comprehension takes:
                     # a deadline and the owner's stop reach a loop
SHOW = "sena_show"   # the value of the code's last line, shown as a notebook shows it (E2B, Jupyter): `page.title()` needs no print
LIMIT = 120          # seconds model-written code may run (a loop that never ends would hold the browser's or Telegram's only thread)
NAP = 10             # seconds one time.sleep of model code may last: a longer wait is a loop of short ones, which the guard reaches

BUILTINS = ("print", "len", "str", "int", "float", "bool", "list", "dict", "set", "frozenset", "tuple", "range", "enumerate", "zip",
            "sorted", "reversed", "min", "max", "sum", "any", "all", "abs", "round", "divmod", "pow", "isinstance", "repr", "map",
            "filter", "iter", "next", "chr", "ord", "hex", "bin", "slice", "hash", "callable", "bytes", "bytearray",
            "Exception", "ValueError", "TypeError", "KeyError", "IndexError", "AttributeError", "StopIteration", "RuntimeError",
            "TimeoutError", "ZeroDivisionError", "LookupError", "ArithmeticError", "NotImplementedError", "AssertionError",
            "True", "False", "None")
MODULES = ("time", "re", "json", "math", "random", "datetime", "collections", "itertools", "functools", "string", "statistics",
           "textwrap", "html", "unicodedata", "difflib", "base64", "decimal", "fractions")
# attribute names that reach an object's machinery, not its data: frames, code, generator internals, string formatting
HIDDEN = {"format", "format_map", "mro", "gi_frame", "gi_code", "gi_yieldfrom", "cr_frame", "cr_code", "cr_await", "ag_frame",
          "ag_code", "ag_await", "f_back", "f_globals", "f_locals", "f_builtins", "f_code", "tb_frame", "tb_next", "co_code",
          "func_globals",
          "Formatter", "vformat", "get_field"}   # string.Formatter looks attributes up by name at run time: a door past this check
CALLS = {"getattr", "setattr", "delattr", "vars", "globals", "locals", "dir", "type", "object", "super", "eval", "exec",
         "compile", "open", "input", "breakpoint", "help", "memoryview", "classmethod", "staticmethod", "property", "exit", "quit"}


def check(code, modules=MODULES):
    """None — the code may run; otherwise the reason in words for the model."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"ошибка в коде: {e}"
    allowed = set(modules)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in allowed:
                    return f"модуль {alias.name} здесь недоступен; можно: {', '.join(sorted(allowed))}"
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in allowed:
                return f"модуль {node.module} здесь недоступен; можно: {', '.join(sorted(allowed))}"
            for alias in node.names:
                if alias.name == "*" or alias.name.startswith("_"):
                    return "импортируй функции по имени, без * и без имён с «_»"
                if alias.name in HIDDEN:
                    return f"«{alias.name}» здесь недоступен"
        elif isinstance(node, ast.Attribute) and node.attr in ("format", "format_map"):
            return "вместо .format используй f-строку"
        elif isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in HIDDEN):
            return f"обращение к «.{node.attr}» запрещено"
        elif isinstance(node, ast.Name) and ((node.id.startswith("_") and node.id != "_") or node.id in CALLS):
            return f"имя «{node.id}» здесь недоступно"
        elif isinstance(node, ast.ClassDef):
            return "классы здесь не нужны: используй функции и словари"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_"):
            return f"имя «{node.name}» начинается с «_»: так нельзя"
        elif isinstance(node, ast.arg) and node.arg.startswith("_") and node.arg != "_":
            return f"параметр «{node.arg}» начинается с «_»: так нельзя"
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and any(n.startswith("_") for n in node.names):
            return "имена с «_» здесь недоступны"
        for taken in (TICK, SHOW):
            if ((isinstance(node, ast.Name) and node.id == taken and not isinstance(node.ctx, ast.Load))
                    or (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == taken) or (isinstance(node, ast.arg) and node.arg == taken)):
                return f"имя «{taken}» занято"
    return None


class _Ticker(ast.NodeTransformer):
    # puts a call of the guard first in every loop body and function body, and between a comprehension and what it iterates
    # (a comprehension has no body of its own: `sum(1 for _ in range(10**12))` ran past every limit)
    def _guarded(self, node):
        self.generic_visit(node)
        node.body = [ast.Expr(ast.Call(ast.Name(TICK, ast.Load()), [], []))] + node.body
        return node

    def _comprehended(self, node):
        self.generic_visit(node)
        for part in node.generators:
            if not part.is_async:
                part.iter = ast.Call(ast.Name(TICK, ast.Load()), [part.iter], [])
        return node

    visit_For = visit_AsyncFor = visit_While = visit_FunctionDef = visit_AsyncFunctionDef = _guarded
    visit_ListComp = visit_SetComp = visit_DictComp = visit_GeneratorExp = _comprehended


def guarded(code, filename, show=False):
    """The code compiled with its guard calls in place (smolagents caps a model's code by operations and time; here by time and
    the owner's stop, checked inside the code itself — a timeout outside it can't stop a loop on another thread).
    show — the last line, when it is an expression, hands its value to SHOW."""
    tree = _Ticker().visit(ast.parse(code))
    if show and tree.body and isinstance(tree.body[-1], ast.Expr):
        tree.body[-1] = ast.Expr(ast.Call(ast.Name(SHOW, ast.Load()), [tree.body[-1].value], []))
    return compile(ast.fix_missing_locations(tree), filename, "exec")


def _show(value):
    if value is not None:   # a click or a fill returns None: nothing to show, as in a notebook
        print(repr(value) if isinstance(value, (bytes, bytearray)) else value)


def ticker(seconds=LIMIT, stop=None):
    """The guard the code calls: past the deadline it raises; stop() — the owner's stop for the task the code works for.
    Given what a comprehension iterates, it hands it on item by item, checking before each."""
    end = time.monotonic() + seconds   # awake time: a Mac asleep mid-run doesn't time the code out

    def tick(*items):
        if time.monotonic() > end:
            raise TimeoutError(f"код работал дольше {seconds} с и остановлен: сократи работу (меньше элементов, без бесконечных циклов)")
        if stop and stop():
            raise Stopped("Остановлено по просьбе Темы")
        if items:
            return _each(items[0], tick)
    return tick


def _each(items, tick):
    for item in items:
        tick()
        yield item


# A builtin that loops inside C never comes back to the guard: sum(range(10**12)), max(itertools.count()), a deque over
# product(...) held the browser's or Telegram's only thread for hours, past every stop. So what can be that long hands out its
# items through the guard: a long range, iter(callable, sentinel), and itertools' endless and combinatorial iterators.
LONG = 10 ** 7   # a range with more numbers than this is walked through the guard (the ticks cost a few seconds at most then)
ENDLESS = ("count", "cycle", "repeat", "product", "permutations", "combinations", "combinations_with_replacement")


class _Range:
    """range for model code: a long one gives its numbers through the guard; len, indexing, in and slices are a range's own."""

    def __init__(self, numbers, tick):
        self._numbers, self._tick = numbers, tick

    def __iter__(self):
        return _each(self._numbers, self._tick)

    def __reversed__(self):
        return _each(reversed(self._numbers), self._tick)

    def __len__(self):
        return len(self._numbers)

    def __contains__(self, value):
        return value in self._numbers

    def __getitem__(self, index):
        found = self._numbers[index]
        return _ranged(found, self._tick) if isinstance(found, range) else found

    def __repr__(self):
        return repr(self._numbers)


def _ranged(numbers, tick):
    try:
        long = len(numbers) > LONG
    except OverflowError:   # more numbers than an index can count
        long = True
    return _Range(numbers, tick) if long else numbers


def _guarding(make, tick):
    @functools.wraps(make)
    def call(*args, **kwargs):
        return _each(make(*args, **kwargs), tick)
    return call


def nap(seconds, tick=None):
    """time.sleep for model code: at most NAP seconds, in half-second pieces, each checking the owner's stop — and, imported
    inside a run, the run's deadline (tick). A plain time.sleep(600) held the browser's (or Telegram's) only thread past every
    limit: the guard ticks only between lines."""
    end = time.monotonic() + max(0.0, min(float(seconds), NAP))
    while True:
        (tick or cancel.check_stop)()
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(0.5, left))


REGEX_SECONDS = 5   # a pattern may search this long: re works inside C, where the tick guard can't reach it (Dify's check: 43 s)


class _Pattern:
    # a compiled pattern whose searches have the same time limit
    def __init__(self, compiled):
        self.compiled = compiled

    def __getattr__(self, name):
        value = getattr(self.compiled, name)
        return _timed(value) if callable(value) else value


def _timed(func):
    @functools.wraps(func)
    def call(*args, **kwargs):
        try:
            return func(*args, timeout=REGEX_SECONDS, **kwargs)
        except TypeError as e:
            if "timeout" not in str(e):
                raise
            return func(*args, **kwargs)   # escape, purge and the like take no time limit
    return call


def _regex():
    # model code asks for re and gets the regex module under that name: the same patterns, a backtracking one stops at
    # REGEX_SECONDS instead of holding the browser's or Telegram's only thread for good (regex is a dependency of trafilatura)
    import regex
    public = {k: v for k, v in vars(regex).items() if not k.startswith("_") and not isinstance(v, types.ModuleType)}
    for name in ("match", "fullmatch", "search", "findall", "finditer", "sub", "subn", "split", "splititer"):
        public[name] = _timed(getattr(regex, name))
    public["compile"] = lambda pattern, flags=0: _Pattern(regex.compile(pattern, flags))
    return types.SimpleNamespace(**public)


def module(name, tick=None):
    """A module as a plain set of its public functions and values: nothing that leads on to another module. time's sleep is
    nap: short, and the owner's stop (and with tick the run's deadline) gets through it."""
    if name == "re":
        return _regex()
    real = importlib.import_module(name)
    public = {k: v for k, v in vars(real).items() if not k.startswith("_") and not isinstance(v, types.ModuleType)}
    if name == "time":
        public["sleep"] = lambda seconds: nap(seconds, tick)
    if name == "itertools":
        public.update({k: _guarding(getattr(real, k), tick or cancel.check_stop) for k in ENDLESS})
    return types.SimpleNamespace(**public)


def namespace(modules=MODULES, tick=None):
    """Builtins for the code: the usual helpers, and import only from the named modules (as plain sets of functions). range and
    iter(callable, sentinel) hand a long run of items out through the guard (LONG)."""
    allowed = set(modules)
    guard = tick or cancel.check_stop

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name not in allowed:
            raise ImportError(f"модуль {name} здесь недоступен; можно: {', '.join(sorted(allowed))}")
        return module(name, tick)

    def guarded_range(*args):
        return _ranged(range(*args), guard)

    def guarded_iter(*args):
        return _each(iter(*args), guard) if len(args) == 2 else iter(*args)   # iter(f, end) calls f in C until end comes

    safe = {name: getattr(builtins, name) for name in BUILTINS if hasattr(builtins, name)}
    safe.update({"__import__": guarded_import, "range": guarded_range, "iter": guarded_iter})
    return safe


def run(code, env, source, modules=MODULES, seconds=LIMIT, stop=None, how=None):
    """Check the code, run it with its env, capture print and errors. The result is tagged as external data; code that was
    refused or raised comes back as a declared failure (core/tools.Failure) — the loop counts it, the model reads the traceback.
    seconds — how long it may run; stop() — whether the owner stopped the task (the code may run on another thread);
    how — for a reader that can't page a spilled output (core/safety.spill)."""
    refused = check(code, modules)
    if refused:
        return Failure(f"Код не запущен: {refused}. Перепиши без этого.")
    if stop is None:
        mine = cancel.current_task()
        stop = lambda: mine in cancel.cancelled   # noqa: E731
    tick = ticker(seconds, stop)
    scope = {**env, TICK: tick, SHOW: _show, "__builtins__": namespace(modules, tick)}
    output = io.StringIO()
    router = safety.router()
    router.local.buffer = output
    raised = False
    try:
        exec(guarded(code, "<код агента>", show=True), scope)
    except Stopped:
        raise
    except BaseException:  # BaseException, so that a SystemExit in the agent's code doesn't shut down the bot
        output.write(traceback.format_exc())
        raised = True
    finally:
        router.local.buffer = None
    text = output.getvalue()
    result = safety.external(safety.spill(text, 5000, "вывод кода", how=how), source) if text else "(пустой вывод, используй print)"
    return Failure(result) if raised else result


def load(source, env, modules=MODULES, seconds=LIMIT, stop=None):
    """Run a recipe's code to get its functions (skills): the same check, builtins and guard; returns the filled scope.
    The guard's clock starts here and covers the call that follows: a recipe runs on the browser's only thread, and a loop in it
    would hold that thread for good — the caller's own timeout only stops waiting (E2B's lesson: the wall is around the code)."""
    refused = check(source, modules)
    if refused:
        raise PermissionError(f"рецепт не прошёл проверку: {refused}")
    if stop is None:
        mine = cancel.current_task()
        stop = lambda: mine in cancel.cancelled   # noqa: E731
    tick = ticker(seconds, stop)
    scope = {**env, TICK: tick, "__builtins__": namespace(modules, tick)}
    exec(guarded(source, "<рецепт>"), scope)
    return scope
