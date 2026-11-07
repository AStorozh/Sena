"""The agent's browser: Chrome with a persistent profile (that's where the site logins live), each task has its own tab,
everything runs on the browser's single thread. How the agent sees the page — view.py; what it does — actions.py.
A page load has its own limit, not the 5 s an element gets; goto waits for the page itself, not for every ad on it; a link that
is a file becomes a download; a file picker never opens on the owner's screen (playwright-mcp's navigate and file chooser)."""
import functools
import os
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

try:
    from patchright import sync_api as playwright_api   # the same Playwright, but without the automation fingerprints that Cloudflare can see
except ImportError:
    from playwright import sync_api as playwright_api

from sena import config
from sena.agents.ag_web import guard
from sena.core import attachments, cancel, events, power, status, store

_playwright = None
_context = None   # the browser opens on the first task and stays open
_thread = ThreadPoolExecutor(max_workers=1)   # the browser lives on its own thread, otherwise it gets in Telegram's way (Telethon)
_tabs = {}   # a run of the agent -> its tab
_owners = {}         # tab -> the task that works in it: a request the tab makes is judged by that task (_private)
_notes = {}          # tab -> events the agent needs to be told about (dialogs, downloads, new tabs)
_dialog_rule = {}    # tab -> how to answer the next dialog: (accept, text)
_document = {}       # tab -> (address, HTTP status) of the last page document it loaded: a 404 or a 500 is told, not guessed
downloads = []       # paths of downloaded files, in order
CALL_TIMEOUT = 180   # we never wait longer than this for a single browser call once it runs: otherwise a hung page would stall every task
QUEUE_WAIT = 300     # seconds a call may wait for its turn while other tasks' calls run: past it, it isn't run at all
FRAME_WAIT = 15      # seconds a finished run waits for its tab's last frame: the judge does without it rather than keep the answer
KEEP_TABS = 6        # tabs left open when a task ends: the newest stay for a look, older ones no task works in and nothing plays in close
NAVIGATION = 30_000  # ms a page load may take: the element timeout (5 s) used to apply to every goto too
LOAD_REST = 5_000    # ms goto still waits for the whole page after its document is there, without failing
FILE_PICKER = ("страница открыла выбор файла (на экране Темы окна нет): загрузи файл кодом — "
               "page.set_input_files('input[type=file]', путь), путь только из загрузок или присланного Темой")


class Busy(RuntimeError):
    """The browser's thread stayed busy with other tasks' calls for longer than the call would wait: it never ran."""


def in_browser(func, *args, wait=None):
    """Run func on the browser's thread for this task: its status, tab and run state go along (status.carried). The time limit
    counts from when the call starts there, not from when it was asked — a quick click waiting behind another task's long call
    is not "Chrome hung" — and a call that hasn't started when its task is stopped or tired of waiting (wait seconds, QUEUE_WAIT
    by default) never runs later (it used to run after its task was told it failed, so a retry did it twice)."""
    wait = QUEUE_WAIT if wait is None else wait
    started = threading.Event()
    job = status.carried(func)

    def call():
        started.set()
        return job(*args)
    future, asked = _thread.submit(call), time.monotonic()
    while not started.wait(1):
        if cancel.is_cancelled() or time.monotonic() - asked > wait:
            if future.cancel():   # not started: it never will
                cancel.check_stop()
                raise Busy(f"Браузер занят другой задачей дольше {max(1, round(wait / 60))} мин: действие не выполнено. Попробуй позже.")
            break   # it has just started
    try:
        return future.result(timeout=CALL_TIMEOUT)
    except FutureTimeout:
        raise RuntimeError(f"Браузер не ответил за {CALL_TIMEOUT} секунд: страница или Chrome завис. "
                           "Скажи Теме закрыть окно Chrome агента, после этого браузер откроется заново.")


def _tab():
    # the tab key of this call: the agent's run pins its own (ag_web.run); anything else works in its task's tab
    return getattr(status.local, "tab", None) or cancel.current_task()


def note(page, text):
    _notes.setdefault(id(page), []).append(text)


def take_notes(page):
    return _notes.pop(id(page), [])


def allow_dialog(page, accept=True, text=None):
    # how to answer the page's NEXT dialog; by default alert is accepted, confirm and prompt are dismissed
    _dialog_rule[id(page)] = (accept, text)


def _on_dialog(page, dialog):
    accept, text = _dialog_rule.pop(id(page), (dialog.type in ("alert", "beforeunload"), None))
    note(page, f"диалог {dialog.type}: «{dialog.message[:200]}» — {'принят' if accept else 'отклонён'}")
    try:
        dialog.accept(text) if accept and text is not None else dialog.accept() if accept else dialog.dismiss()
    except Exception as e:
        note(page, f"диалог не удалось закрыть: {e}")
        events.error("Диалог в браузере", e)


def _on_download(page, download):
    # any file the page downloads is saved to data/downloads
    target = store.stamped(config.DOWNLOADS_DIR, download.suggested_filename)
    try:
        download.save_as(str(target))
        downloads.append(str(target))
        note(page, f"скачан файл: {target}")
        events.log("download", str(target), name=download.suggested_filename)
        attachments.register(target, f"{download.suggested_filename} со страницы {page.url}"[:500], "browser")
    except Exception as e:
        note(page, f"файл не скачался: {e}")
        events.error("Скачивание в браузере", e)


def _watch(page):
    page.on("dialog", lambda dialog: _on_dialog(page, dialog))
    page.on("filechooser", lambda chooser: note(page, FILE_PICKER))   # a listener keeps the Mac's own picker from opening
    page.on("download", lambda download: _on_download(page, download))
    page.on("popup", lambda popup: note(page, f"открылась новая вкладка {popup.url or '(грузится)'}: "
                                              "работать в ней — use_tab(context.pages[-1])"))
    page.on("response", lambda reply: _on_response(page, reply))
    page.on("close", lambda _: _forget(page))


def _forget(page):
    # a closed tab leaves nothing behind: its number may come back for another tab, and with it a stale dialog rule or note
    for table in (_notes, _dialog_rule, _document, _owners):
        table.pop(id(page), None)
    for key in [k for k, p in list(_tabs.items()) if p is page]:
        _tabs.pop(key, None)


def _on_response(page, reply):
    try:
        if reply.request.resource_type == "document" and reply.frame == page.main_frame:
            _document[id(page)] = (reply.url, reply.status)
    except Exception:   # a reply of a page that is closing: nothing to tell
        pass


def header(page):
    """(address, title, HTTP status or None) of the tab now: what the agent is told after its code when it changed."""
    url, status = page.url, None
    seen = _document.get(id(page))
    if seen and seen[0] == url:
        status = seen[1]
    try:
        title = page.title()
    except Exception:
        title = ""
    return url, title, status


def _patient(api):
    """goto without wait_until waits for the page's document, then up to LOAD_REST for the rest of it, never failing on the slow
    rest (third-party scripts, ads); a link that turns out to be a file comes back as None with the download noted."""
    for cls in (api.Page, api.Frame):
        original = getattr(cls, "goto", None)
        if original is None or getattr(original, "patient", False):
            continue

        @functools.wraps(original)
        def goto(self, *args, _original=original, **kwargs):
            whole = "wait_until" not in kwargs and len(args) < 3
            if whole:
                kwargs["wait_until"] = "domcontentloaded"
            try:
                reply = _original(self, *args, **kwargs)
            except Exception as e:
                if "Download is starting" in str(e):   # the address was a file: the download handler keeps it
                    return None
                raise
            if whole:
                try:
                    self.wait_for_load_state("load", timeout=LOAD_REST)
                except Exception:
                    pass
            return reply
        goto.patient = True
        cls.goto = goto


def _closed(_):
    global _context
    _context = None   # the window was closed — we'll open a new one next time
    events.log("browser", "окно Chrome закрыто")


def context():
    global _playwright, _context
    if power.is_off():
        raise RuntimeError(power.refusal("браузер помощника"))
    if _context is None and power.is_stopping():   # a Chrome started now would outlive the stop that closes its programs
        raise RuntimeError("Сена останавливается: браузер помощника заново не открываю")
    if _playwright is None:
        guard.install(playwright_api)   # before any page exists: the fences hold for every tab the agent's code touches
        _patient(playwright_api)
        _playwright = playwright_api.sync_playwright().start()
    if _context is None:
        # no automation flags: otherwise sites (YouTube) detect the robot and break the video after ~45 seconds
        # extensions (uBlock Origin Lite against ads) are installed into this profile by hand from the Chrome store: Chrome 137+ no longer accepts the flags for that
        _context = _playwright.chromium.launch_persistent_context(
            str(config.BROWSER_PROFILE), channel="chrome", headless=config.BROWSER_HEADLESS, viewport=None,
            ignore_default_args=["--enable-automation"], args=["--disable-blink-features=AutomationControlled"],
        )
        _context.on("close", _closed)
        _context.on("page", _watch)   # and tabs that open later
        _context.route(guard.PRIVATE, _private)   # the home network after someone else's text: every request, not only goto
        events.log("browser", "Chrome запущен", profile=str(config.BROWSER_PROFILE))
        for page in _context.pages:
            _watch(page)
        _context.set_default_timeout(5000)  # wait at most 5 seconds for an element, so the agent doesn't hang
        _context.set_default_navigation_timeout(NAVIGATION)
    return _context


def _private(route, request):
    # a request is handled during whatever call the browser's thread runs now — often another task's: it is judged by the task
    # of the tab that made it (a page of a task that read a mail fetching the router while a clean task's click runs)
    try:
        task = _owners.get(id(request.frame.page))
    except Exception:   # a service worker's request belongs to no frame: the call running now decides, as before
        task = None
    guard.private_request(route, request, task)


def stop():
    """Close the agent's Chrome: this run's and one a killed run left behind. Found by the agent's own profile folder, so the
    owner's own Chrome is never touched. Returns whether it was running."""
    global _context
    _context = None
    found = subprocess.run(["pgrep", "-f", str(config.BROWSER_PROFILE)], capture_output=True, text=True).stdout.split()
    for pid in found:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (OSError, ValueError):
            pass   # already gone
    if found:
        events.log("browser", "Chrome помощника закрыт: Сена остановлена", processes=len(found))
    return bool(found)


def final_frame():
    # this task's tab frame (the judge looks at the result by eye); None if there's no tab
    page = _tabs.get(_tab())
    if page is None or page.is_closed():
        return None
    return page.screenshot(type="jpeg", quality=50, scale="css")


def _own(page):
    # the tab works for the task whose call holds it now: its requests are that task's (_private)
    task = status.current()
    if task is not None:
        _owners[id(page)] = task
    return page


def use_tab(page):
    # the task keeps working in this tab from now on
    _tabs[_tab()] = _own(page)


def get_page():
    # each task has its own tab, so parallel tasks don't interfere with each other
    browser = context()
    tab = _tab()
    if tab not in _tabs or _tabs[tab].is_closed():
        blank = [p for p in browser.pages if p.url == "about:blank" and p not in _tabs.values()]
        _tabs[tab] = blank[0] if blank else browser.new_page()
    return _own(_tabs[tab])


def _playing(page):
    # something sounds or moves in the tab (music, a video): it isn't closed under the owner
    try:
        return bool(page.evaluate("() => [...document.querySelectorAll('video, audio')].some(m => !m.paused && !m.ended)"))
    except Exception:
        return True   # can't tell: left open


def release():
    """The run that owned this tab is over: the tab is no longer its — the next task opens its own instead of taking it over
    (a new task used to navigate away the music the last one left playing). Past KEEP_TABS, the oldest tabs no task works in
    and nothing plays in are closed, so the tabs of finished tasks don't pile up. Called on the run's own thread, which doesn't
    wait for the tidying: behind other tasks' calls it held the agent's card for minutes."""
    _tabs.pop(_tab(), None)
    if _context is not None:
        _thread.submit(_close_old_tabs)


def _close_old_tabs():
    try:
        _close_tabs()
    except Exception as e:   # Chrome closed meanwhile: nothing to tidy, and no one waits for the answer
        events.error("Закрытие старых вкладок браузера", e)


def _close_tabs():
    if _context is None:
        return
    held = {id(p) for p in _tabs.values()}
    pages = [p for p in _context.pages if not p.is_closed()]
    for page in pages[:max(0, len(pages) - KEEP_TABS)]:
        if id(page) not in held and not _playing(page):
            try:
                page.close()
            except Exception as e:
                events.error("Закрытие старой вкладки браузера", e)
