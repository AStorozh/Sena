"""Fences on Playwright itself, for every page and tab the agent's code touches: the browser opens only sites, a file goes up
to a site only if it may leave Sena (core/safety.forbidden_path), the browser writes files only into its own folders, and the
logins of sites (cookies, saved state) never come back as text; a password bound to its sites is typed only there. After
someone else's text the router, this Mac and the home devices are reached only at an address the owner named — checked on the
agent's own goto and on every request to such an address (a link clicked, a script's navigation, a page's own fetch).
While a recipe replays without the model (replaying()), a press of an irreversible button — send, pay, buy, delete, publish,
reply to a job, book — is refused, and in the agent's own run a press that can't be taken back (pay, buy, send, reply to a job,
book, publish) needs the owner's "yes" to exactly it, once: no one would ask the owner first (Skyvern guards its cached scripts
the same way: the agent takes over where judgement is needed). A press is checked where it lands: a click on an element, the
mouse at a point (the element under it), Enter in a form (its submit button), a dispatched click.
The agent's instructions ask for the same. What these fences can't see is a page's own script run through page.evaluate: it
can press anything, which is why the instructions, the owner's "yes" for the agents that change the Mac, and the address
fence stay the other lines."""
import contextlib
import functools
import importlib
import os
import re
import tempfile
import threading
import time
import weakref
from urllib.parse import urlsplit

from sena import config
from sena.core import cancel, events, safety, secrets, status
from sena.world import net

SCHEMES = {"http", "https", "about", "data"}


def _refuse(text):
    events.log("browser", text, blocked=True)
    raise PermissionError(text)


def inside(url, task=None):
    """Why the browser must not reach this address now, or None. After someone else's text a page or a mail may be steering:
    the router, this Mac and the home devices are reached only at an address the owner named himself, or one the agent opened
    before any foreign text came in (its page's own requests go on) — E2B: private addresses are always walled off from the
    code's side. task — whose request it is (the tab's own task, browser._private); by default the task whose call runs now."""
    parts = urlsplit(str(url or ""))
    marked = task.get("outside") if task is not None else status.outside()
    if parts.scheme.lower() not in ("http", "https", "ws", "wss") or not parts.hostname or not marked:
        return None
    task = task if task is not None else (status.current() or {})
    if _named(parts.hostname, task.get("text", "")) or parts.hostname in task.get("inside_ok", ()):
        return None
    where = net.private_host(str(url))
    return f"После чужого текста браузер не открывает {where}: такой адрес Тема называет сам" if where else None


def _named(host, text):
    # the owner wrote this very address, not a longer one that contains it (10.0.0.1 inside 10.0.0.15)
    return bool(re.search(rf"(?<![\w.-]){re.escape(host)}(?![\w-]|\.\w)", text or "", re.I))


def _url(url):
    parts = urlsplit(str(url or ""))
    scheme = parts.scheme.lower()
    if scheme not in SCHEMES:
        _refuse(f"Адрес «{scheme}:» браузер агента не открывает: только сайты (http, https). Файлы на Mac — через агента компьютера")
    why = inside(url)
    if why:
        _refuse(why)
    task = status.current()
    if scheme in ("http", "https") and parts.hostname and task is not None:
        if net.private_host(str(url)) and parts.hostname not in task.setdefault("inside_ok", []):
            task["inside_ok"].append(parts.hostname)   # opened while allowed: the page's own requests to it aren't refused later
        # a site is someone else's text from its first byte: marked before it loads, so a page that sends the browser on to
        # the router while it loads meets the address fence too
        status.touched(f"сайт {parts.hostname}")


# requests to an address that looks like one inside the Mac or the home network: only these pass through Sena's code — a route
# for every request would make each one wait for the browser's single thread — and only these may be refused
PRIVATE = re.compile(r"^(?:https?|wss?)://(?:[^/@?#]*@)?(?:localhost|0\.0\.0\.0|127(?:\.\d+){3}|10(?:\.\d+){3}|192\.168(?:\.\d+){2}|"
                     r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d+){2}|169\.254(?:\.\d+){2}|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])(?:\.\d+){2}|"
                     r"\[[0-9a-f:.]*\]|[^/:?#@]+\.(?:local|lan|home|internal))(?::\d+)?(?:[/?#]|$)", re.I)


def private_request(route, request, task=None):
    """The route for PRIVATE addresses: a link, a script or the page itself reaching inside the Mac or the home network after
    someone else's text, at an address the owner didn't name, is refused like the agent's own goto. task — the task of the tab
    that made the request: a request is handled during whatever call the browser's thread runs, often another task's."""
    why = inside(request.url, task)
    if why:
        events.log("browser", why, blocked=True, url=request.url[:200])
        route.abort("blockedbyclient")
    else:
        route.continue_()


def _paths(files):
    items = files if isinstance(files, (list, tuple)) else [files]
    return [str(f) for f in items if isinstance(f, (str, os.PathLike))]   # a file given as bytes (a dict) isn't a path on the Mac


def _upload(files):
    for path in _paths(files):
        why = safety.forbidden_path(path)
        if why:
            _refuse(f"Этот файл нельзя отправлять на сайт: {why}")


def _writable(path):
    if path is None:
        return
    real = os.path.realpath(os.path.expanduser(str(path)))
    folders = [os.path.realpath(d) for d in (config.DOWNLOADS_DIR, config.GENERATED_DIR, tempfile.gettempdir())]
    if not any(real == d or real.startswith(d + os.sep) for d in folders):
        _refuse(f"Сохранять файлы браузер может только в {config.DOWNLOADS_DIR}")


def _no_file_source(kwargs):
    if kwargs.get("path"):
        _refuse("Подключать к странице файлы с Mac нельзя: передай текст скрипта или стиля прямо в коде")


def _never(what, why="это входы Темы на сайтах"):
    def check(*args, **kwargs):
        _refuse(f"{what} агенту недоступно: {why}")
    return check


def _wrap(cls, name, check, with_self=False):
    # check(*args, **kwargs) before the call — or check(self, *args, **kwargs) when the target is the object itself
    original = getattr(cls, name, None)
    if original is None or getattr(original, "guarded", False):
        return

    @functools.wraps(original)
    def guarded(self, *args, **kwargs):
        check(self, *args, **kwargs) if with_self else check(*args, **kwargs)
        return original(self, *args, **kwargs)
    guarded.guarded = True
    setattr(cls, name, guarded)


_replay = threading.local()
# a reply to a job goes out in one click on hh.ru (career-ops guards «Откликнуться» for that), a booking binds a slot or money;
# a bare "apply" stays out: on a shop it applies a filter or a coupon
IRREVERSIBLE = re.compile(r"\b(отправить|отправка|оплатить|оплата|купить|заказать|оформить заказ|подтвердить|удалить|опубликовать|"
                          r"перевести|откликнуться|отклик\w*|подать заявку|записаться|забронировать|"
                          r"send|pay|buy|order|checkout|purchase|delete|remove|publish|submit|confirm|transfer|apply now|easy apply|"
                          r"book now|reserve)\b", re.I)


@contextlib.contextmanager
def replaying():
    """The block where a code recipe runs without the model (agents/ag_web/recipes.py)."""
    _replay.on = True
    try:
        yield
    finally:
        _replay.on = False


READS = (("inner_text", ()), ("get_attribute", ("value",)), ("get_attribute", ("aria-label",)), ("get_attribute", ("title",)))


def _words_of(target):
    # what the button says: its text, its value, its label, its title — read quickly, never failing the click itself
    said = []
    for name, args in READS:
        get = getattr(target, name, None)
        if get is None:
            continue
        try:
            try:
                said.append(get(*args, timeout=500) or "")
            except TypeError:   # an element handle reads without a timeout of its own
                said.append(get(*args) or "")
        except Exception:
            continue
    return " ".join(said)


# what can't be taken back once pressed: money, a message or a reply going out, a booking, a publication. Asked of the owner
# even in the agent's own run (the prompt alone stood between a page that talks the model into it and the click); generic
# words — confirm, submit, delete — would ask about every cookie banner, so they stay for model-free recipes only
FINAL = re.compile(r"\b(оплатить|купить|заказать|оформить заказ|перевести деньги|отправить|опубликовать|откликнуться|отклик\w*|"
                   r"подать заявку|забронировать|pay|pay now|buy|buy now|place order|checkout|purchase|send|publish|apply now|easy apply|book now)\b", re.I)
YES = re.compile(r"^\s*(да|ага|ок|окей|хорошо|давай|можно|жми|нажимай|подтверждаю|согласен|согласна|yes|ok|go)\b", re.I)
NOT_YES = re.compile(r"\b(не|нет|стоп|подожди|погоди|отмена|но|no|don't|stop|but)\b", re.I)
LABEL_MOST = 60  # characters a button's label has at most
APPROVAL_TTL = 600   # seconds a "yes" to a press waits to be used
_approved = {}   # task id -> [(words of a button the owner said "yes" to, when)]: each is good for one press (A2A: an approval names its action)
_approved_lock = threading.Lock()


def _task_key(task=None):
    # approvals belong to the task (its id), not to a thread: a thread's number goes to the next task, and with it an unused "yes"
    task = cancel.current_task() if task is None else task
    found = status.tasks.get(task) if isinstance(task, int) else None
    return found["id"] if found else task


def approve(task, words, answer):
    """The owner's answer to the agent's question about pressing «words»: a plain "yes" grants exactly one such press for this
    task, for APPROVAL_TTL; anything else grants nothing. Returns whether it was granted."""
    words = " ".join(str(words or "").lower().split())
    if not words or not YES.match(str(answer or "")) or NOT_YES.search(str(answer or "")):
        return False
    now = time.time()
    with _approved_lock:
        for key in [k for k, items in _approved.items() if all(now - when > APPROVAL_TTL for _, when in items)]:
            del _approved[key]   # tasks long over, approvals never used
        _approved.setdefault(_task_key(task), []).append((words, now))
        del _approved[_task_key(task)][:-5]
    events.log("browser", f"Тема разрешил нажать «{words[:60]}» один раз", approved=True)
    return True


def _same_press(words, said):
    """Whether a press of the button labelled said is the press the owner approved as words: the same label, or one that begins
    with the other where a word ends — and then the shorter one names the irreversible act itself («Оплатить» for «Оплатить
    2 300 ₽»). A «да» to a dialog's «Да» used to pass «Подать заявку» («да» is inside «подать»), and «Да, оплатить» too."""
    if not words or not said:
        return False
    if words == said:
        return True
    short, longer = sorted((words, said), key=len)
    return bool(re.match(rf"{re.escape(short)}(?!\w)", longer)) and bool(FINAL.search(short))


def _use_approval(task, said):
    said, now = " ".join(said.lower().split()), time.time()
    with _approved_lock:
        for i, (words, when) in enumerate(_approved.get(task, [])):
            if now - when <= APPROVAL_TTL and _same_press(words, said):
                del _approved[task][i]
                return True
    return False


def final_press(target, task=None):
    """Refuse a press that can't be taken back unless the owner approved exactly it (raises PermissionError with what to do)."""
    said = _words_of(target).strip()
    # a button's label is short; a long text is a card or a block that happens to contain «Купить» (a product tile opens a page)
    if len(said) <= LABEL_MOST and FINAL.search(said) and not _use_approval(_task_key(task), said):
        _refuse(f"«{said[:60]}» — необратимое нажатие: сначала спроси Тему через ask_tema(вопрос, approve=«{said[:40]}»); "
                "после его «да» нажми ровно один раз")


def press(target):
    """What a press of target must pass: on a recipe's path without the model nothing irreversible at all; in the agent's own run
    the owner's "yes" to a press that can't be taken back. None — nothing to check (no element there)."""
    if target is None:
        return
    if getattr(_replay, "on", False):
        said = _words_of(target)
        if IRREVERSIBLE.search(said):
            _refuse(f"Рецепт без модели не нажимает «{said.strip()[:60]}»: необратимое делает агент, спросив Тему")
        return
    final_press(target)


# the element a press at a point lands on: through open shadow roots, up to what can be pressed (a button's icon is its button)
AT_POINT = """([x, y]) => { let el = document.elementFromPoint(x, y);
  while (el && el.shadowRoot) { const inner = el.shadowRoot.elementFromPoint(x, y); if (!inner || inner === el) break; el = inner; }
  return el && (el.closest('button, a[href], summary, label, input, [role=button], [role=link], [role=menuitem], [onclick]') || el); }"""


def element_at(page, x, y):
    """The element a press at the page's point (x, y) lands on, nested frames included: an element handle, or None."""
    frame, at, found = page.main_frame, (x, y), None
    for _ in range(5):   # a frame in a frame, a few levels at most
        try:
            handle = frame.evaluate_handle(AT_POINT, list(at)).as_element()
        except Exception:
            return found
        if handle is None:
            return found
        found, inner = handle, handle.content_frame()
        box = handle.bounding_box() if inner else None   # a frame's box is counted from the page's own corner
        if not inner or not box:
            return found
        frame, at = inner, (x - box["x"], y - box["y"])
    return found


# the button a key press of Enter (or Space) presses: the focused button itself, or the default button of the form whose field
# has the focus (Enter in a filled checkout form pays as surely as a click on «Оплатить»)
ENTER_TARGET = """(el, space) => {
  while (el && el.shadowRoot && el.shadowRoot.activeElement) el = el.shadowRoot.activeElement;
  if (!el || !el.tagName) return null;
  const tag = el.tagName, type = (el.type || '').toLowerCase(), role = el.getAttribute('role') || '';
  if (tag === 'BUTTON' || tag === 'SUMMARY' || (tag === 'A' && el.href) || /^(button|link|menuitem)$/.test(role)
      || (tag === 'INPUT' && /^(submit|button|image|reset)$/.test(type))) return el;
  if (space || !el.form || tag === 'TEXTAREA') return null;
  return el.form.querySelector('button:not([type=button]):not([type=reset]), input[type=submit], input[type=image]'); }"""
FOCUSED = f"(space) => ({ENTER_TARGET})(document.hasFocus() ? document.activeElement : null, space)"
ENTER_KEYS = {"enter", "numpadenter", "\n", "\r"}
SPACE_KEYS = {" ", "space"}


def _key(key):
    # the key a press names, without its modifiers ("Shift+Enter" is Enter too)
    name = str(key or "")
    return (name if name == "+" else name.split("+")[-1]).lower()


def _enter_on(target, space=False):
    # the button Enter (Space) pressed on target would press, as an element handle, or None
    try:
        try:
            handle = target.evaluate_handle(ENTER_TARGET, space, timeout=2000)
        except TypeError:   # an element handle evaluates without a timeout of its own
            handle = target.evaluate_handle(ENTER_TARGET, space)
        return handle.as_element()
    except Exception:
        return None


def _focused_button(page, space=False):
    # the button Enter (Space) pressed now would press: the focused element of the frame that has the focus
    for frame in page.frames:
        try:
            handle = frame.evaluate_handle(FOCUSED, space).as_element()
        except Exception:
            continue
        if handle is not None:
            return handle
    return None


def _pressing(key):
    # (presses something, only buttons): Enter presses a button or sends a form, Space presses only a button
    name = _key(key)
    return name in ENTER_KEYS or name in SPACE_KEYS, name in SPACE_KEYS


def _key_on(target, key):
    presses, space = _pressing(key)
    if presses:
        press(_enter_on(target, space))


def _text_on(target, text, element=None):
    # typed text: a secret only on its own sites (target — where it goes), and a line break in it is an Enter on the element
    _no_secret_elsewhere(target, text)
    if isinstance(text, str) and ("\n" in text or "\r" in text):
        press(_enter_on(element if element is not None else target))


_cursor = weakref.WeakKeyDictionary()   # page -> where the agent's mouse is: a press without a point lands there


class Mouse:
    """page.mouse in the agent's code: the same mouse, but a press lands on an element, and that element meets the same "yes" as
    a click on it (the coordinates read off a screenshot used to press «Оплатить» unchecked)."""

    def __init__(self, page, mouse):
        self.page, self.mouse = page, mouse

    def __getattr__(self, name):
        return getattr(self.mouse, name)

    def move(self, x, y, **kwargs):
        _cursor[self.page] = (x, y)
        return self.mouse.move(x, y, **kwargs)

    def down(self, **kwargs):
        press(element_at(self.page, *_cursor.get(self.page, (0, 0))))
        return self.mouse.down(**kwargs)

    def click(self, x, y, **kwargs):
        press(element_at(self.page, x, y))
        _cursor[self.page] = (x, y)
        return self.mouse.click(x, y, **kwargs)

    def dblclick(self, x, y, **kwargs):
        press(element_at(self.page, x, y))
        _cursor[self.page] = (x, y)
        return self.mouse.dblclick(x, y, **kwargs)


class Keyboard:
    """page.keyboard in the agent's code: Enter (and Space on a button) presses what has the focus, and is checked like a click."""

    def __init__(self, page, keyboard):
        self.page, self.keyboard = page, keyboard

    def __getattr__(self, name):
        return getattr(self.keyboard, name)

    def press(self, key, **kwargs):
        presses, space = _pressing(key)
        if presses:
            press(_focused_button(self.page, space))
        return self.keyboard.press(key, **kwargs)

    def down(self, key, **kwargs):
        presses, space = _pressing(key)
        if presses:
            press(_focused_button(self.page, space))
        return self.keyboard.down(key, **kwargs)

    def type(self, text, **kwargs):
        if isinstance(text, str) and ("\n" in text or "\r" in text):
            press(_focused_button(self.page))
        return self.keyboard.type(text, **kwargs)


def _guarded_property(cls, name, proxy):
    original = getattr(cls, name, None)
    if not isinstance(original, property) or getattr(original.fget, "guarded", False):
        return

    def get(self):
        return proxy(self, original.fget(self))
    get.guarded = True
    setattr(cls, name, property(get))


def _frame_url(target):
    # the address of the document a text goes into: an iframe's own (a login form inside another site), else its page's
    try:
        if hasattr(target, "owner_frame"):   # an element handle
            return target.owner_frame().url
        if hasattr(target, "page") and not hasattr(target, "goto"):   # a locator: ask the element's own document
            return target.evaluate("e => e.ownerDocument.location.href", timeout=2000)
        return target.url   # a page or a frame
    except Exception:
        return getattr(getattr(target, "page", None), "url", "") or ""


def _no_secret_elsewhere(target, text):
    # a secret's value is typed only on its own sites — checked where it goes, when it goes (the page may have changed since
    # secret() was called in the same code)
    if isinstance(text, str) and len(text) >= secrets.MIN_LEN:
        name = secrets.name_of(text)
        why = name and secrets.off_site(name, _frame_url(target))
        if why:
            _refuse(why)


CLICKS = ("click", "mouseup", "pointerup", "dblclick", "submit")   # dispatched events that press like a click


def install(api):
    """Put the fences on the classes of a Playwright sync API module (patchright or playwright). Safe to call again."""
    for cls in (api.Page, api.Frame):
        _wrap(cls, "goto", lambda url=None, *a, **k: _url(url if url is not None else k.get("url")))
        _wrap(cls, "set_input_files", lambda selector=None, files=None, *a, **k: _upload(files if files is not None else k.get("files")))
        _wrap(cls, "add_script_tag", lambda *a, **k: _no_file_source(k))
        _wrap(cls, "add_style_tag", lambda *a, **k: _no_file_source(k))
    for cls in (api.Locator, api.ElementHandle):
        _wrap(cls, "set_input_files", lambda files=None, *a, **k: _upload(files if files is not None else k.get("files")))
        _wrap(cls, "screenshot", lambda *a, **k: _writable(k.get("path")))
    _wrap(api.FileChooser, "set_files", lambda files=None, *a, **k: _upload(files if files is not None else k.get("files")))
    for cls in (api.Locator, api.ElementHandle):   # the target is the element itself
        for name in ("click", "dblclick", "tap", "check", "set_checked"):
            _wrap(cls, name, lambda self, *a, **k: press(self), with_self=True)
        _wrap(cls, "dispatch_event", lambda self, type=None, *a, **k: press(self) if str(type or k.get("type", "")).lower() in CLICKS else None, with_self=True)
        _wrap(cls, "press", lambda self, key=None, *a, **k: _key_on(self, key if key is not None else k.get("key")), with_self=True)
        _wrap(cls, "fill", lambda self, value=None, *a, **k: _no_secret_elsewhere(self, value if value is not None else k.get("value")), with_self=True)
        for name in ("type", "press_sequentially"):
            _wrap(cls, name, lambda self, text=None, *a, **k: _text_on(self, text if text is not None else k.get("text")), with_self=True)
    for cls in (api.Page, api.Frame):   # the target is a selector on the page or the frame
        def first(self, selector, kwargs):
            return self.locator(selector if selector is not None else kwargs.get("selector")).first
        for name in ("click", "dblclick", "tap", "check", "set_checked"):
            _wrap(cls, name, lambda self, selector=None, *a, **k: press(first(self, selector, k)), with_self=True)
        _wrap(cls, "dispatch_event", lambda self, selector=None, type=None, *a, **k: press(first(self, selector, k)) if str(type or k.get("type", "")).lower() in CLICKS else None, with_self=True)
        _wrap(cls, "press", lambda self, selector=None, key=None, *a, **k: _key_on(first(self, selector, k), key if key is not None else k.get("key")), with_self=True)
        _wrap(cls, "fill", lambda self, selector=None, value=None, *a, **k: _no_secret_elsewhere(self, value if value is not None else k.get("value")), with_self=True)
        _wrap(cls, "type", lambda self, selector=None, text=None, *a, **k: _text_on(self, text if text is not None else k.get("text"), first(self, selector, k)), with_self=True)
    _guarded_property(api.Page, "mouse", Mouse)
    _guarded_property(api.Page, "keyboard", Keyboard)
    _wrap(api.Page, "screenshot", lambda *a, **k: _writable(k.get("path")))
    _wrap(api.Page, "pdf", lambda *a, **k: _writable(k.get("path")))
    _wrap(api.Page, "add_init_script", lambda *a, **k: _no_file_source(k))
    _wrap(api.BrowserContext, "add_init_script", lambda *a, **k: _no_file_source(k))
    _wrap(api.Download, "save_as", lambda path=None, *a, **k: _writable(path if path is not None else k.get("path")))
    _wrap(api.Video, "save_as", lambda path=None, *a, **k: _writable(path if path is not None else k.get("path")))
    _wrap(api.BrowserContext, "cookies", _never("Чтение куки"))
    _wrap(api.BrowserContext, "storage_state", _never("Сохранённое состояние входов"))
    _wrap(api.BrowserContext, "unroute_all", _never("Снятие ограды адресов", "это защита Сены"))   # the address fence is a route of the whole browser
    for cls in (api.Page, api.BrowserContext):
        _wrap(cls, "route_from_har", _never("Запись и подмена трафика"))
    tracing = getattr(api, "Tracing", None) or getattr(importlib.import_module(api.__name__ + "._generated"), "Tracing", None)
    if tracing:   # a trace holds the pages' traffic with the logins in it
        _wrap(tracing, "start", _never("Запись трассы браузера"))
