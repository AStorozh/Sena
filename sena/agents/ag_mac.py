"""ag_mac — computer agent: macOS terminal and screenshots. Every command runs inside the macOS sandbox through core/seatbelt.run:
Sena's data, keys and the Keychain tool stay closed whatever the command says; output goes to a file, stdin is closed, a stop or
the time limit ends the whole process group.
The screen: clicks, typing and keys are tools of their own, each coming back with the screenshot after it — one step per action
instead of two (SWE-agent's browser, Agent S). The screenshot is sized so no Claude vision tier shrinks it again (at most 1568 px
and 1.15 megapixels): a point the model reads off it is a point on it, and the tools turn it into screen points themselves (Agent S
measured raw pixels from a re-shrunk picture coming back at about half their true value). Text in any language goes through the
clipboard, which gets its old text back (Agent S types non-Latin text that way)."""
import ctypes
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlparse

from sena import config
from sena.agents import common
from sena.core import brain, cancel, seatbelt
from sena.core import secrets, status
from sena.core.safety import clean_env, external, forbidden_command, spill
from sena.core.tools import CHECK, LOOK_STATUS, NEXT, Failure, doing, image, string, tool
from sena.memory import lessons, skills

EFFORT = "medium"  # how deeply it thinks: low / medium / high
ROLE = """Ты ag_mac — агент компьютера в системе Сены (ag_sena), работаешь для Темы. У тебя полный доступ к его MacBook через терминал (zsh, macOS).
Выполняй задачу командами терминала. Рабочий стол: ~/Desktop. Установлены brew и blueutil (Bluetooth).
Каждая команда — новый zsh в домашней папке: cd и export в следующую не переходят, пиши cd ~/Desktop/X && … в той же команде.
Утилиты macOS — BSD: sed -i '' 's/a/b/' файл; строки файла — nl -ba файл | sed -n '40,80p'; новый файл — cat <<'EOF' > файл.
Ничего интерактивного: флаги -y, --yes, --no-input; не запускай vim, nano, less и то, что ждёт ввода с клавиатуры.
ps и top в песочнице не запускаются: процессы — pgrep -fl имя, кто занял порт — lsof -nP -iTCP:3000 -sTCP:LISTEN.
Результат на экране (окна, программы, настройки) проверяй глазами: инструмент look — снимок экрана.
Действия на экране — инструменты click (x, y с последнего снимка как есть: пересчёт в точки экрана сделан за тебя), type_text (любой язык)
и press_key (return, esc, cmd+s): каждый возвращает снимок после действия, отдельный look нужен, только если окно изменится позже.
cliclick в терминале по координатам со снимка не используй — они в другом масштабе. Сначала клавиши и меню (press_key cmd+…), потом клики;
Музыку и видео (YouTube, Музыка, Spotify) ставь на паузу, включай и листай только media_key: она отвечает, кто играет звук, — это и есть проверка.
Играет не тот плеер — нажми ещё раз (вернёшь как было) и выведи нужную вкладку chrome_tabs show, потом снова media_key. По странице YouTube
не кликай ради паузы или фокуса: клик мимо кнопки открывает другое видео.
программу открывай open -a "Имя"; страницы и вкладки Chrome — только chrome_tabs (open -a "Google Chrome" адрес плодит вкладки,
AppleScript к Chrome из терминала Chrome не принимает); поменял файл командой — открытая программа может этого не видеть: закрой и открой её заново.
Необратимое — не твоё решение: удаляй только в Корзину, не делай rm -rf, sudo, выключение и перезагрузку, не ставь и не сноси программы,
не трогай чужие проекты, если в задаче нет явного «можно». Нужно такое — остановись и напиши, что требуется подтверждение Темы.
Файлы в проектах Темы (папки ~/Desktop/<проект> с кодом) ты читаешь и запускаешь, но не правишь: правка проекта — дело ag_code
(claude_task, со снимком и проверкой). Задача требует правки там — закончи с need_owner и напиши, что это задача для claude_task.
Команда дольше 5 минут будет убита: серверы и долгое запускай в фоне с выводом в файл: nohup команда > /tmp/имя.log 2>&1 & — и проверь через sleep 2; tail -n 20 /tmp/имя.log
Команды идут в песочнице macOS: данные и ключи Сены, пароли, Связка ключей, sudo закрыты — такой отказ не обходи, скажи, что нужен Тема.
Закончи вызовом finish: outcome (done — сделано и проверено, partial, failed, need_owner), итог и факт, который это подтверждает (вывод команды, путь, значение).
Если в задаче сказано «Готово, когда …» — в evidence дай именно этот факт, проверенный тобой."""

MEDIA = {"play_pause": 16, "next": 17, "previous": 18}   # NX_KEYTYPE_PLAY, _NEXT, _PREVIOUS: the keys on the Mac keyboard

TOOLS = [
    tool("chrome_tabs", "Вкладки обычного Chrome Темы: list — список; open — открыть адрес (в уже открытой вкладке того же сайта, новую не "
                        "плодит); show — показать вкладку; close — закрыть вкладку. Вкладка — номер «окно.вкладка» из списка. Ответ — вкладки после действия.",
         {"check": CHECK, "status": doing("«Закрываю лишние вкладки YouTube»"), "action": {**string("действие"), "enum": ["list", "open", "show", "close"]},
          "url": string("адрес для open"), "tab": string("номер вкладки «окно.вкладка» для show и close")}, ["check", "status", "action"]),
    tool("media_key", "Системная клавиша музыки и видео Mac, как на клавиатуре: play_pause, next, previous. Действует на плеер, который "
                      "играл последним (YouTube в Chrome, Музыка, Spotify, другой браузер), без кликов по странице. Ответ — кто сейчас играет звук.",
         {"check": CHECK, "status": doing("«Ставлю ролик на паузу»"), "action": {**string("клавиша"), "enum": list(MEDIA)}},
         ["check", "status", "action"]),
    tool("run_command", "Выполнить команду в терминале и получить вывод",
         {"check": CHECK, "status": doing("«Подключаю наушники», «Не получилось, пробую иначе»"), "next": NEXT, "command": string("Команда для zsh")},
         ["check", "status", "command"]),
    tool("look", "Посмотреть на экран Mac: снимок экрана", {"check": CHECK, "status": LOOK_STATUS}, ["check", "status"]),
    tool("click", "Клик мышью по точке с последнего снимка экрана (координаты как на снимке). Вернёт снимок после клика, курсор на нём виден.",
         {"check": CHECK, "status": doing("«Жму «Сохранить» в окне Pages»"), "x": {"type": "integer"}, "y": {"type": "integer"},
          "double": {"type": "boolean", "description": "двойной клик"}, "right": {"type": "boolean", "description": "правой кнопкой"}},
         ["check", "status", "x", "y"]),
    tool("type_text", "Напечатать текст туда, где стоит курсор ввода: любой язык, эмодзи не нужны. Вернёт снимок после ввода.",
         {"check": CHECK, "status": doing("«Пишу название файла»"), "text": string("что напечатать"),
          "enter": {"type": "boolean", "description": "нажать Enter после текста"}}, ["check", "status", "text"]),
    tool("press_key", "Нажать клавишу или сочетание: return, esc, tab, space, delete, arrow-up, cmd+s, cmd+shift+n, alt+tab. Вернёт снимок.",
         {"check": CHECK, "status": doing("«Сохраняю: cmd+s»"), "keys": string("клавиша или сочетание через +")}, ["check", "status", "keys"]),
]


TIMEOUT = 300               # seconds a command may run
SHOWN = 10_000              # characters of a command's output the model sees (its start and its end), as mini-swe-agent
RUN_LIMIT = 1800            # seconds one task of the computer agent may take, all its steps together
NETWORK = re.compile(r"\b(curl|wget|http|https|ftp)\b|https?://", re.I)
TIMED_OUT = ("команда не завершилась за 5 минут, вся её группа процессов остановлена. Обычно так бывает, когда программа ждёт ввода "
             "(нужен флаг вроде -y или --no-input) или долгое запущено не в фоне (сервер, watch, tail -f)")
LESS = ("Весь вывод сохранён в {path} — команду не повторяй, читай файл частями: sed -n '1,200p' {path}, grep -n 'слово' {path}, "
        "tail -n 100 {path}")   # rerunning to see the rest would repeat an install or a test run (opencode keeps the whole output to read)
OUTPUTS = os.path.join(tempfile.gettempdir(), "sena-output")   # outside Sena's data: the sandboxed terminal can read it
NO_SCREEN = "Не удалось снять экран: нужно разрешение «Запись экрана» для программы, из которой запущена Сена"


def run_command(command):
    cancel.check_stop()
    print("Команда:", command)
    refusal = forbidden_command(command)
    if refusal:
        return Failure(refusal)
    if not seatbelt.available():   # without the sandbox the terminal stays closed: a word list alone is not a wall
        return Failure("Команда не запущена: на этом Mac нет песочницы macOS (/usr/bin/sandbox-exec), а без неё терминал закрыт.")
    env = clean_env()
    ticket = _run().get("ticket")
    if ticket:   # the secret the owner allowed for this task: in the command's environment only, never in the model's text
        value = secrets.resolve(ticket[1])
        if value:
            env[f"SECRET_{ticket[0].upper().replace('-', '_').replace('.', '_')}"] = value
    mine = cancel.current_task()
    output, code, why = seatbelt.run(command, os.path.expanduser("~"), TIMEOUT, stop=lambda: mine in cancel.cancelled, env=env)
    output = (output or "(пустой вывод)") + {"stopped": "\n[остановлено по просьбе Темы]", "timeout": f"\n[{TIMED_OUT}]"}.get(why, f"\n[код возврата {code}]")
    denied = bool(code) and seatbelt.denied(output)
    if denied:
        output += ("\n[Это защита Сены: песочница macOS закрывает её данные и ключи, пароли, Связку ключей, sudo и её код. "
                   "Не обходи; если это правда нужно Теме — скажи, что сделать это он должен сам.]")
    shown = spill(secrets.mask(output), SHOWN, "команда", how=LESS, folder=OUTPUTS)   # masked before it lands where the terminal can read it
    if NETWORK.search(command):   # a page, a file from the network: someone else's text, and the task now carries it
        status.touched("вывод команды из сети")
        shown = external(shown, "вывод команды")
    if why == "timeout" or denied:   # a hung or refused command is a failed call: the streak guard and the review count it
        return Failure(shown)
    return shown


# ---------- the screen ----------
MOST_EDGE, MOST_PIXELS = 1568, 1_150_000   # the picture every Claude vision tier takes as it is, without shrinking it again
SCREEN = "ObjC.import('AppKit'); var s = $.NSScreen.screens.objectAtIndex(0).frame.size; s.width + ' ' + s.height"
CLICLICK = shutil.which("cliclick") or "/opt/homebrew/bin/cliclick"   # where brew put it, as the system check finds it
SCREEN_WAIT = 60   # seconds a computer task waits for the screen while another one uses it
# non-Latin text goes through the clipboard, which gets back what it held (a picture too: AppleScript keeps the data as it was)
PASTE = """on run argv
    set saved to missing value
    try
        set saved to the clipboard
    end try
    set the clipboard to (item 1 of argv)
    do shell script (quoted form of (item 2 of argv)) & " kd:cmd t:v ku:cmd"
    delay 0.3
    if saved is not missing value then set the clipboard to saved
end run"""
SPECIAL = {"return", "enter", "esc", "tab", "space", "delete", "fwd-delete", "home", "end", "page-up", "page-down",
           "arrow-up", "arrow-down", "arrow-left", "arrow-right", *[f"f{i}" for i in range(1, 17)]}
ALIASES = {"escape": "esc", "backspace": "delete", "up": "arrow-up", "down": "arrow-down", "left": "arrow-left", "right": "arrow-right",
           "pageup": "page-up", "pagedown": "page-down", "del": "fwd-delete"}
MODIFIERS = {"cmd": "cmd", "command": "cmd", "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "option": "alt", "opt": "alt", "shift": "shift", "fn": "fn"}
_last_points = None
_screen_lock = threading.Lock()   # one computer task at a time looks, clicks and types: two typed into each other's windows
_holder = {}                      # what the task holding the screen is, for the one that waits


def _run():
    # this run's own state ({"ticket", "frame", "screen"}): one dict the run owns, shared with every step's thread by reference
    return getattr(status.local, "mac_run", None) or {}


def _take_screen():
    # wait for the screen (a stop gets through), at most SCREEN_WAIT; whether it was taken
    end = time.monotonic() + SCREEN_WAIT
    while not _screen_lock.acquire(timeout=1):
        cancel.check_stop()
        if time.monotonic() > end:
            return False
    _holder["task"] = ((status.current() or {}).get("text") or "")[:80]
    return True


def _let_screen_go():
    _holder.clear()
    _screen_lock.release()


def _with_screen(action):
    """action() with the screen for this run: taken at the run's first look, click or key and held to its end, so its clicks,
    its typing and its clipboard (non-Latin text goes through it) aren't another task's. Terminal commands don't need it.
    A Failure when another task keeps the screen past SCREEN_WAIT."""
    run = getattr(status.local, "mac_run", None)
    if not (run and run.get("screen")):
        if not _take_screen():
            return Failure(f"Экраном сейчас пользуется другая задача («{_holder.get('task') or 'без названия'}»): дождись её конца "
                           "или сделай это командами терминала, без экрана")
        if run is not None:
            run["screen"] = True
    try:
        return action()
    finally:
        if run is None:   # a tool called outside a run (a check): the screen only for this one action
            _let_screen_go()


def _points():
    """The main screen's size in points — the space cliclick counts in — from the system, not guessed from the pixels (the old
    "wider than 2000 px is Retina" guess halved a 2560 px monitor that isn't one). The last good answer stands in for a failed one."""
    global _last_points
    try:
        w, h = subprocess.run(["osascript", "-l", "JavaScript", "-e", SCREEN], capture_output=True, text=True, timeout=10).stdout.split()
        _last_points = (round(float(w)), round(float(h)))
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return _last_points


def _fit(w, h):
    # the picture's size for the model: the screen in points, shrunk until it fits every vision tier
    k = min(1.0, MOST_EDGE / max(w, h), (MOST_PIXELS / (w * h)) ** 0.5)
    return max(1, int(w * k)), max(1, int(h * k))   # down, never past the limit


def _screen(cursor=False):
    """A screenshot sized for the model (_fit of the screen in points), or None. cursor — draw the pointer (screencapture -C)."""
    points = _points()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "screen.jpg")
        subprocess.run(["screencapture", "-x", *(["-C"] if cursor else []), "-t", "jpg", path], timeout=30)
        if not os.path.exists(path):
            return None
        if points is None:   # the system didn't say: the pixels themselves, halved when they look like Retina
            px = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path], capture_output=True, text=True, timeout=30).stdout.split()
            w, h = int(px[-3]), int(px[-1])
            points = (w // 2, h // 2) if w > 2000 else (w, h)
        w, h = _fit(*points)
        subprocess.run(["sips", "-z", str(h), str(w), path], capture_output=True, timeout=30)
        with open(path, "rb") as f:
            frame = f.read()
    _run()["frame"] = frame   # the last thing the agent saw: the post-task review looks at it too
    return frame


def look():
    def shot():
        frame = _screen()
        return [image(frame)] if frame else Failure(NO_SCREEN)
    return _with_screen(shot)


def _to_points(x, y):
    # a point read off the screenshot -> the same point on the screen
    points = _points()
    if not points:
        return None
    w, h = _fit(*points)
    return (min(max(0, round(int(x) * points[0] / w)), points[0] - 1), min(max(0, round(int(y) * points[1] / h)), points[1] - 1))


NO_INPUT = ("Клики и клавиши не доходят: у приложения «Сена» нет разрешения «Универсальный доступ». Остановись (need_owner) и передай "
            "Теме: Системные настройки → Конфиденциальность и безопасность → Универсальный доступ → включить «Сена».")


def _input_allowed():
    """Whether macOS lets this process press keys and click: without «Универсальный доступ» cliclick exits 0 and the system
    drops every event without a word — the agent clicked and pressed for minutes and nothing moved (03.11.2026)."""
    try:
        ax = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(ax.AXIsProcessTrusted())
    except OSError:
        return True   # can't tell: let it try


def _cliclick(args):
    cancel.check_stop()
    if not _input_allowed():
        return NO_INPUT
    done = subprocess.run([CLICLICK, *args], capture_output=True, text=True, timeout=30)
    return "" if done.returncode == 0 else (done.stderr or done.stdout).strip() or f"cliclick вернул {done.returncode}"


def _after(what, cursor=False):
    # an action's answer: what was done, and the screen right after it
    time.sleep(0.6)
    frame = _screen(cursor=cursor)
    return [{"type": "input_text", "text": what + "; снимок экрана после действия:"}, image(frame)] if frame else what


def click(x, y, double=False, right=False):
    return _with_screen(lambda: _click(x, y, double, right))


def _click(x, y, double, right):
    point = _to_points(x, y)
    if not point:
        return Failure("Не удалось узнать размер экрана: сделай look и попробуй ещё раз")
    kind = "rc" if right else "dc" if double else "c"
    error = _cliclick([f"{kind}:{point[0]},{point[1]}"])
    if error:
        return Failure(error if error == NO_INPUT else f"Клик не удался: {error}")
    return _after(f"{'Правый клик' if right else 'Двойной клик' if double else 'Клик'} в ({x}, {y}) на снимке", cursor=True)


# ---------- the owner's Chrome: its tabs ----------
# AppleScript to Chrome from the sandbox is refused by Chrome itself (-10004 «нарушение прав»: it takes no Apple Events from a
# sandboxed sender), and `open -a "Google Chrome" URL` opens a new tab every time — three tabs of one clip (03.11.2026). These
# fixed scripts run outside the sandbox: no code of the model's, only list, open (in the tab of that site if there is one),
# show and close; the address comes in as an argument, never as script text.
TABS = """on run argv
set act to item 1 of argv
tell application id "com.google.Chrome"   -- by its id: the name also finds the browser agent's own Chrome for Testing
  if act is "open" then
    set u to item 2 of argv
    if (count of windows) is 0 then make new window
    if item 3 of argv is "0" then
      tell front window to make new tab with properties {URL:u}
    else
      set w to window (item 3 of argv as integer)
      set t to tab (item 4 of argv as integer) of w
      set URL of t to u
      set active tab index of w to (item 4 of argv as integer)
      set index of w to 1
    end if
    activate
  else if act is "show" then
    set w to window (item 2 of argv as integer)
    set active tab index of w to (item 3 of argv as integer)
    set index of w to 1
    activate
  else if act is "close" then
    close tab (item 3 of argv as integer) of window (item 2 of argv as integer)
  end if
  set out to ""
  set wi to 0
  repeat with w in windows
    set wi to wi + 1
    set ti to 0
    repeat with t in tabs of w
      set ti to ti + 1
      set out to out & wi & "." & ti & "  " & (URL of t) & "  " & (title of t) & linefeed
    end repeat
  end repeat
  return out
end tell
end run"""


def _tabs_script(*args):
    done = subprocess.run(["osascript", "-", *map(str, args)], input=TABS, capture_output=True, text=True, timeout=20)
    if done.returncode:
        why = (done.stderr or "").strip()[-200:]
        if "-1743" in why:
            return Failure("macOS не разрешает Сене управлять Chrome: нужен Тема — Системные настройки → Конфиденциальность и безопасность → "
                           "Автоматизация → «Сена» → включить Google Chrome")
        return Failure(f"Chrome не ответил: {why}")
    return done.stdout.strip()


def playing():
    """Who plays sound now: the apps holding macOS's «Playing audio» power assertion (Chrome, Yandex, Music hold it while they play)."""
    out = subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True, timeout=10).stdout
    return sorted(set(re.findall(r"pid \d+\(([^)]+)\).*named: \"Playing audio\"", out)))


def media_key(action):
    """Play/pause, next, previous — the Mac keyboard's media keys, sent to the system: they reach the player that played last,
    whatever tab or window it is in. Clicking a YouTube page for the same took minutes and once opened another video (03.11.2026)."""
    cancel.check_stop()
    if action not in MEDIA:
        return Failure("action: " + ", ".join(MEDIA))
    if not _input_allowed():
        return NO_INPUT
    import Quartz
    from AppKit import NSEvent
    for down in (True, False):   # a system-defined event, subtype 8: the key code in the high half, down 0xa / up 0xb
        flags = 0xa00 if down else 0xb00
        event = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            14, (0, 0), flags, 0, 0, None, 8, (MEDIA[action] << 16) | (flags & 0xff00), -1)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event.CGEvent())
    time.sleep(1.5)   # the player answers and takes or drops its assertion
    now = playing()
    return f"Нажата {action}. Звук сейчас играет у: {', '.join(now)}" if now else f"Нажата {action}. Сейчас звук не играет ни у одной программы"


def _host(url):
    return urlparse(url if "//" in url else "https://" + url).netloc.lower().removeprefix("www.")


def chrome_tabs(action, url="", tab=""):
    """The owner's own Chrome: list its tabs, open an address (in the tab of the same site if one is open — never a pile of
    copies), show or close a tab by its number «окно.вкладка» from the list. Answers with the tabs as they are after it."""
    cancel.check_stop()
    if action == "list":
        listed = _tabs_script("list")
    elif action == "open":
        if not url.strip():
            return Failure("Нужен адрес")
        listed = _tabs_script("list")
        if isinstance(listed, Failure):
            return listed
        same = next((line.split()[0] for line in listed.splitlines() if len(line.split()) > 1 and _host(line.split()[1]) == _host(url)), None)
        window, number = same.split(".") if same else ("0", "0")
        listed = _tabs_script("open", url.strip(), window, number)
    elif action in ("show", "close"):
        parts = str(tab).split(".")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            return Failure("Номер вкладки — «окно.вкладка» из списка, например 1.3")
        listed = _tabs_script(action, *parts)
    else:
        return Failure("action: list, open, show или close")
    if isinstance(listed, Failure):
        return listed
    return external(f"Вкладки Chrome сейчас (окно.вкладка  адрес  заголовок):\n{listed or 'вкладок нет'}", "вкладки Chrome")


def type_text(text, enter=False):
    return _with_screen(lambda: _type(str(text), enter))


def _type(text, enter):
    if text.isascii() and "\n" not in text:
        error = _cliclick([f"t:{text}"]) if text else ""
    else:   # Cyrillic and the rest: keystrokes depend on the keyboard layout, the clipboard doesn't
        done = subprocess.run(["osascript", "-", text, CLICLICK], input=PASTE, capture_output=True, text=True, timeout=30)
        error = "" if done.returncode == 0 else (done.stderr or "").strip()[:200] or "вставка не удалась"
    if not error and enter:
        error = _cliclick(["kp:return"])
    if error:
        return Failure(f"Ввод не удался: {error}")
    return _after(f"Напечатала {len(text)} знаков" + (" и нажала Enter" if enter else ""))


def _keys(keys):
    """cliclick arguments for "cmd+shift+s", "return", "alt+tab"; None — a key it doesn't know."""
    *mods, key = [p.strip().lower() for p in str(keys).split("+") if p.strip()] or [""]
    held = [MODIFIERS.get(m) for m in mods]
    key = ALIASES.get(key, key)
    if None in held or not (key in SPECIAL or len(key) == 1):
        return None
    press = [f"kp:{key}" if key in SPECIAL else f"t:{key}"]
    return ([f"kd:{','.join(held)}"] + press + [f"ku:{','.join(held)}"]) if held else press


def press_key(keys):
    args = _keys(keys)
    if not args:
        return Failure(f"Не знаю клавишу «{keys}»: return, esc, tab, space, delete, arrow-up, f5 или буква с cmd/ctrl/alt/shift через +")

    def press():
        error = _cliclick(args)
        return Failure(f"Клавиша не нажалась: {error}") if error else _after(f"Нажала {keys}")
    return _with_screen(press)


def run(task, secret="", done_when=""):
    # secret — the name of an owner's secret this task may use: a ticket for the run, the value appears only in the commands' environment
    ticket = None
    if secret:
        ticket = secrets.use(secret, "ag_mac", task)
        if not ticket:
            return Failure(f"Секрет «{secret}» агенту компьютера не разрешён — попроси Тему разрешить его через координатора")
        var = f"SECRET_{secret.upper().replace('-', '_').replace('.', '_')}"
        task = f"{task}\n\nПароль «{secret}» лежит в переменной окружения ${var} каждой команды: используй её, не печатай и не копируй значение."
    previous = getattr(status.local, "mac_run", None)
    state = status.local.mac_run = {"ticket": (secret, ticket) if ticket else None, "frame": None, "screen": False}
    try:
        answer = brain.run(config.MODEL_FAST, EFFORT, common.instructions(ROLE), common.with_experience(common.task_for(task, done_when), "ag_mac"),
                           TOOLS + [skills.READ_TOOL, lessons.NOTE_TOOL, common.FINISH],
                           {"run_command": run_command, "chrome_tabs": chrome_tabs, "media_key": media_key, "look": look, "click": click, "type_text": type_text, "press_key": press_key,
                            "read_skill": skills.read_skill, "note_to_self": lessons.note_function("ag_mac"), "finish": common.finisher(done_when)},
                           max_steps=20, name="ag_mac", max_seconds=RUN_LIMIT)
        return common.close_card("ag_mac", task, done_when, answer)
    except Exception as e:   # a run that fell leaves its card too: the judge sees it failed, not that nothing was asked (A2A)
        if not isinstance(e, cancel.Stopped):
            status.card("ag_mac", task, done_when, {"status": "failed", "result": f"агент упал: {type(e).__name__}: {str(e)[:300]}", "evidence": ""})
        raise
    finally:
        if state["frame"]:   # the review judges a screen task by eye too, not only by the agent's words (Agent S)
            status.attach_image(state["frame"])
        if state["screen"]:
            _let_screen_go()
        status.local.mac_run = previous
