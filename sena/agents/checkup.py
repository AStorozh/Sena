"""Checking the whole system on demand. Every part — Sena herself, her channels, the AI in the cloud and at home, her agents,
the data, the outside services — is tried with a real action that costs nothing, or less than a hundredth of a cent where only
a real call can tell (an empty balance shows on nothing else), and answers: works, works with a caveat, doesn't work — why and
what to do. Run by a button in both apps (the panel «Проверка системы», phone/panels.py), by Sena's tool system_check
(«запусти проверку», «работает ли Google?»), and every night: a part that broke since the last check is told to the owner once.
Switched off (the screen without the core, core/power.py) only what is free and starts no program is checked; the rest waits
for her. Every check has its own time limit and its own failure: one that hangs or breaks doesn't spoil the others."""
import ctypes
import importlib.util
import os
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

from sena import config
from sena.agents import ag_night
from sena.agents.ag_web import browser
from sena.core import costs, events, keychain, llm, llm_claude, local, offsite, power, roles, secrets, settings, status, store
from sena.core.tools import string, tool
from sena.google import auth, media, youtube
from sena.memory import history
from sena.telegram import account, api
from sena.world import github, hireflow, luma, maps_osm, maps_tomtom, net, news, portfolio, travel, uptime, weather

OK, WARN, FAIL, OFF, WAIT = "ok", "warn", "fail", "off", "wait"
WORDS = {OK: "работает", WARN: "с оговоркой", FAIL: "не работает", OFF: "выключено или не подключено", WAIT: "ждёт включения Сены"}
GROUPS = ("Сена", "Связь", "ИИ в облаке", "Дома на Mac", "Помощники", "Сервисы")
TIMEOUT = 25            # seconds one check may take; the home models, loading from cold, get HOME_TIMEOUT
HOME_TIMEOUT = 90
RECHECK_PAUSE = 30      # seconds before a night failure is tried once more: a blip of the network isn't worth a message
REPORT = config.STATE_DIR / "checkup.json"   # the last result of every check, and which failures were already told
_lock = threading.Lock()
_running = {"since": None, "done": 0, "total": 0}


class Check:
    def __init__(self, id, group, title, func, timeout=TIMEOUT):
        self.id, self.group, self.title, self.func, self.timeout = id, group, title, func, timeout


def _since(t):
    return datetime.fromtimestamp(t).strftime("%d.%m %H:%M") if t else ""


def _flag(name):
    return bool(settings.get(name))


# ---------- Sena herself ----------
def _core(spend):
    if power.is_off():
        return OFF, "выключена: проверено то, что можно без неё и бесплатно; остальное — после «Запустить Сену»", "Включи Сену в приложении"
    started = config.PID_FILE.stat().st_mtime if config.PID_FILE.exists() else None
    return OK, "работает" + (f" с {_since(started)}" if started else ""), ""


def _disk(spend):
    free = shutil.disk_usage(config.DATA).free / 1024 ** 3
    if free < 1:
        return FAIL, f"свободно {free:.1f} ГБ: записи и журналы могут перестать сохраняться", "Освободи место на диске"
    if free < 5:
        return WARN, f"свободно {free:.1f} ГБ", "Скоро понадобится место: освободи хотя бы 5 ГБ"
    return OK, f"свободно {free:.0f} ГБ", ""


def _databases(spend):
    report = ag_night.check_integrity()
    if "error" in report:
        return FAIL, f"проверка баз упала: {report['error']}", "Подробности — в журнале событий"
    bad = {name: verdict for name, verdict in report.items() if name.endswith(".db") and verdict != "ok"}
    if bad:
        return FAIL, "повреждены: " + "; ".join(f"{n} — {v[:80]}" for n, v in bad.items()), "Восстановить из ночной копии: «Сена ведёт» → «Копии»"
    return OK, "записи, журнал событий и указатель целы", ""


def _backups(spend):
    copies = sorted(config.BACKUPS_DIR.glob("*_sena.db")) if config.BACKUPS_DIR.is_dir() else []
    away = offsite.status()
    if not copies:
        return FAIL, "ночных копий памяти нет", "«Сена ведёт» → «Копии» → «Сделать копию сейчас»"
    days = (time.time() - copies[-1].stat().st_mtime) / 86400
    notes, status, fix = [f"копия на Mac {_since(copies[-1].stat().st_mtime)}"], OK, ""
    if days > 2:
        status, fix = WARN, "Ночная уборка не делала копию больше двух суток: «Сена ведёт» → «Копии» → «Сделать копию сейчас»"
    if not away["available"]:
        notes.append("копия вне Mac не делается: iCloud Drive выключен")
        status, fix = WARN, fix or "Включи iCloud Drive в настройках системы"
    else:
        notes.append(f"вне Mac — {away['count']}")
    return status, ", ".join(notes), fix


def _keys(spend):
    missing = [k for k in config.REQUIRED_KEYS if not config.has(k)]
    if missing:
        return FAIL, "нет обязательных ключей: " + ", ".join(missing), "Впиши их в .env (образец — .env.example)"
    return OK, "все обязательные ключи на месте", ""


def _budget(spend):
    limit, spent = settings.get("budget_usd_day"), costs.spent_today()
    if not limit:
        return OK, f"предела нет; сегодня потрачено ${spent:.2f}", ""
    if spent >= limit:
        return FAIL, f"предел исчерпан: ${spent:.2f} из ${limit:.2f} — платные вызовы стоят до завтра", "Подними предел: Настройки → Поведение"
    if spent >= limit * 0.8:
        return WARN, f"потрачено ${spent:.2f} из ${limit:.2f} — предел близко", "Если нужно больше — Настройки → Поведение"
    return OK, f"сегодня ${spent:.2f} из ${limit:.2f}", ""


def _errors(spend):
    rows = [e for e in events.search(kind="error", since=time.time() - 86400, limit=500)
            if not str(e.get("place", "")).startswith("Проверка системы")]   # a failed check is already its own line
    if not rows:
        return OK, "ошибок не было", ""
    places = {}
    for e in rows:
        place = str(e.get("place") or e["text"]).split(":")[0][:60]
        places[place] = places.get(place, 0) + 1
    top = sorted(places.items(), key=lambda kv: -kv[1])[:3]
    return WARN, f"ошибок {len(rows)}; чаще всего — " + ", ".join(f"{p} ({n})" for p, n in top), "Подробности — «Журнал» → «События» в приложении Mac"


def _secrets(spend):
    try:
        names = secrets.names()   # opens the store with its key: proves the key and the file agree
    except Exception as e:   # the key doesn't open the file: another Mac's key, or the Keychain item was replaced
        return FAIL, f"хранилище не открывается своим ключом ({type(e).__name__})", "Верни прежний ключ (Связка ключей или копия вне Mac): без него секреты не прочесть"
    if not names:
        return OK, "секретов пока нет", ""
    where = "в файле рядом с данными" if config.SECRETS_KEY.exists() or not keychain.enabled() else "в Связке ключей"
    return OK, f"хранилище открывается, секретов {len(names)}; ключ {where}", ""


def _exam(spend):
    runs = store.load_json(config.EXAM_FILE, {"runs": []}).get("runs") or []
    if not runs:
        return WARN, "экзамена ума ещё не было: неизвестно, верно ли мозг выбирает действия", "«Сена ведёт» → «Экзамен ума» → «Запустить экзамен» (около 1 $)"
    last = runs[-1]
    status = OK if last["passed"] == last["total"] else WARN
    return status, f"последний экзамен {_since(last['t'])}: {last['passed']} из {last['total']}", "" if status == OK else "Что не сдано — «Сена ведёт» → «Экзамен ума»"


# ---------- channels ----------
def _internet(spend):
    reply, seconds, why = net.probe("https://www.apple.com/library/test/success.html", who="проверка системы")
    if reply is None:
        return FAIL, f"интернета нет: {why}", "Проверь Wi-Fi или кабель"
    if seconds > 3:
        return WARN, f"медленный: ответ за {seconds:.1f} с", "Если так всё время — проверь Wi-Fi или роутер"
    return OK, f"есть, ответ за {seconds:.1f} с", ""


def _telegram_bot(spend):
    if not config.TELEGRAM_TOKEN:
        return FAIL, "нет ключа бота", "Впиши TELEGRAM_TOKEN в .env (его даёт @BotFather)"
    me = api.post("getMe", {}, retries=1, timeout=15) or {}
    if not me.get("ok"):
        return FAIL, f"Телеграм не принял ключ бота: {me.get('description') or 'нет ответа'}", "Проверь TELEGRAM_TOKEN у @BotFather"
    name = me["result"].get("username", "")
    hook = (api.post("getWebhookInfo", {}, retries=1, timeout=15) or {}).get("result") or {}
    if hook.get("url"):
        return FAIL, f"у бота @{name} задан вебхук: сообщения уходят туда, а не к Сене", "Убрать вебхук: открыть api.telegram.org/bot<ключ>/deleteWebhook"
    return OK, f"бот @{name} на связи", ""


SIGN_IN_TELEGRAM = "Скажи Сене «войди в Телеграм»: на Маке откроется QR-код — отсканируй его Телеграмом на телефоне, перезапуск не нужен"
SIGN_IN_GOOGLE = "Скажи Сене «войди в Google»: на Маке откроется окно входа — нажми в нём «Разрешить»"


def _telegram_account(spend):
    if not _flag("use_telegram_account"):
        return OFF, "выключен в настройках интеграций", ""
    if not Path(f"{config.TELEGRAM_SESSION}.session").exists():
        return FAIL, "входа в аккаунт нет", SIGN_IN_TELEGRAM
    if power.is_off():
        return WAIT, "вход сохранён; подключение проверю, когда Сена включена", ""
    if not account.connected():   # a kept sign-in that didn't connect when Sena started is tried again now
        return FAIL, "не подключился: вход истёк или Телеграм не ответил", SIGN_IN_TELEGRAM
    return OK, "подключён: чаты читаются, сообщения уходят", ""


# ---------- AI in the cloud ----------
def _claude(spend):
    if not config.CLAUDE_KEYS.get("brain"):
        return FAIL, "нет ключа мозга", "Впиши SENA_CLAUDE_BRAIN_KEY в .env (console.anthropic.com → API keys)"
    keys = ["brain"] + (["agents"] if config.CLAUDE_KEYS.get("agents") else [])
    models = {role: llm_claude.ping(role) for role in keys}
    wanted = [m for m in (config.MODEL_MAIN, config.MODEL_FAST, config.MODEL_VOICE) if llm.is_claude(m)]
    missing = [m for m in wanted if m not in set().union(*models.values())]
    if missing:
        return WARN, "ключ рабочий, но этих моделей среди доступных нет: " + ", ".join(missing), "Проверь имена MODEL_* в sena/config.py"
    if spend:   # the balance is the key's, not the model's: one token from the cheapest model shows it (a millionth of a dollar)
        for role in keys:
            llm.claude_probe(role, llm_claude.CHEAPEST if llm_claude.CHEAPEST in models[role] else config.MODEL_MAIN, purpose="проверка системы")
    summary = f"мозг {config.MODEL_MAIN}, помощники {config.MODEL_FAST} и {config.MODEL_VOICE}" + (", баланс есть" if spend else "; баланс проверю, когда Сена включена")
    if "agents" not in keys:
        return WARN, summary + "; помощники идут по ключу мозга — их траты не видны отдельно", \
            "Создай второй ключ sena-agents (console.anthropic.com → API keys) и впиши SENA_CLAUDE_AGENTS_KEY в .env"
    return OK, summary, ""


def _openai(spend):
    used = [m for m in (config.MODEL_MAIN, config.MODEL_FAST, config.MODEL_VOICE) if not llm.is_claude(m)] + \
           ([config.MODEL_EMBED] if roles.where("embed") == config.CLOUD else [])
    if not used:
        return OFF, "не используется: мозг и помощники на Claude, голос и запасной слух на Gemini", ""
    if not config.OPENAI_API_KEY:
        return FAIL, "нет ключа, а OpenAI нужен для: " + ", ".join(used), "Впиши OPENAI_API_KEY в .env (platform.openai.com → API keys)"
    llm.ping()
    if not spend:
        return OK, "ключ рабочий; баланс проверю, когда Сена включена", ""
    llm.embeddings(["проверка"], purpose="проверка системы")   # the cheapest paid call: an empty balance shows only on one
    return OK, "ключ рабочий, баланс есть (нужен для: " + ", ".join(used) + ")", ""


def _gemini(spend):
    if not config.GEMINI_API_KEY:
        return WARN, "нет ключа GEMINI_API_KEY: Сена говорит домашним голосом, картинок и видео нет", "Ключ — aistudio.google.com/apikey, вписать в .env"
    names = set(media.available_models())
    missing = sorted((set(media.IMAGE_MODELS.values()) | {media.VIDEO_MODEL, config.MODEL_TTS, config.MODEL_TRANSCRIBE}) - names)
    if missing:
        return WARN, "ключ рабочий, но этих моделей уже нет: " + ", ".join(missing), "Обнови имена моделей в sena/config.py и sena/google/media.py"
    return OK, f"ключ рабочий: голос {config.VOICE}, запасной слух" + (", картинки и видео" if _flag("use_gemini") else "; картинки и видео выключены в настройках") + \
        " (баланс видно только по платному вызову — его не трачу)", ""


# ---------- at home, on the Mac ----------
def _silence():
    path = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 16000)
    return path


def _home_models(spend):
    have = local.installed()
    if not (have["embed"] and have["chat"]):
        return FAIL, "домашние модели не скачаны или нет программы их сервера", "python -m sena.home setup; brew install llama.cpp"
    if power.is_off():
        return WAIT, "файлы на месте; запуск проверю, когда Сена включена", ""
    started = time.time()
    local.embed(["проверка"], purpose="проверка системы")
    local.chat("Ответь одним словом: работаю.", max_tokens=8, purpose="проверка системы")
    return OK, f"отпечатки (EmbeddingGemma) и домашняя модель (Qwen) отвечают, {time.time() - started:.0f} с", ""


def _hearing(spend):
    have = local.installed()
    if not have["hearing"]:
        return FAIL, "распознавания речи нет", "python -m sena.home setup; brew install whisper-cpp ffmpeg"
    if power.is_off():
        return WAIT, "файлы на месте; запуск проверю, когда Сена включена", ""
    wav = _silence()
    try:
        local.transcribe(wav, best=True, purpose="проверка системы", journal=False)   # the accurate model if it's there, else the small one
    finally:
        os.unlink(wav)
    return (OK, "whisper распознаёт речь", "") if have["hearing_best"] else \
        (WARN, "распознаёт малая модель: точная не скачана", "python -m sena.home setup — докачает large-v3-turbo")


def _voice(spend):
    if not local.installed()["voice"]:
        return FAIL, "домашнего голоса нет", "python -m sena.home setup (голос Ирина)"
    if power.is_off():
        return WAIT, "голос на месте; озвучку проверю, когда Сена включена", ""
    if not local.speak("Проверка.", purpose="проверка системы"):
        return FAIL, "озвучка вернула пустой звук", "python -m sena.home setup — переустановит голос"
    irina = local.path_of(config.LOCAL_VOICE).exists() and importlib.util.find_spec("piper") is not None
    return OK, "домашний голос говорит (" + ("Ирина" if irina else "Милена, системный") + ")", ""


def _listening(spend):
    missing = [m for m in ("resemblyzer", "openwakeword") if importlib.util.find_spec(m) is None]
    if missing:
        return FAIL, "не установлено: " + ", ".join(missing), "pip install -r requirements.txt"
    if not config.WAKE_MODEL.exists():
        return FAIL, "нет детектора имени «Сена»", "Нет файла models/sena.onnx: обучи его кодом из tools/wakeword и положи в models/"
    if not store.load_json(config.OWNER_VOICE_FILE, []):   # every spoken command is then someone else's: no «да» by voice, agents wait
        return WARN, "голос Темы ещё не выучен: голосовые команды считаются чужими", "Пришли Сене в Телеграм 2–3 голосовых сообщения — она выучит твой голос"
    return OK, "детектор имени и распознавание по голосу на месте, голос Темы выучен", ""


def _reading(spend):
    return (OK, "macOS читает текст фото, сканов и PDF", "") if local.installed()["reading"] else \
        (FAIL, "чтение документов недоступно", "pip install -r requirements.txt (pyobjc Vision и Quartz)")


# ---------- the agents ----------
def _browser(spend):
    if not _flag("use_browser"):
        return OFF, "выключен в настройках интеграций", ""
    if not Path("/Applications/Google Chrome.app").exists():
        return FAIL, "Google Chrome не установлен", "Установи Chrome с google.com/chrome"
    if importlib.util.find_spec("patchright") is None and importlib.util.find_spec("playwright") is None:
        return FAIL, "нет библиотеки управления браузером", "pip install -r requirements.txt"
    if browser._context is not None:
        return OK, "Chrome помощника открыт и работает", ""
    return OK, "готов: Chrome и библиотека на месте, окно откроется с первой задачей", ""


def _accessibility():
    try:
        lib = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(lib.AXIsProcessTrusted())
    except (OSError, AttributeError):
        return None   # not knowable here: not a failure


def _computer(spend):
    if not shutil.which("cliclick") and not Path("/opt/homebrew/bin/cliclick").exists():
        return FAIL, "нет cliclick: клики по окнам невозможны", "brew install cliclick"
    from Quartz import CGPreflightScreenCaptureAccess   # asks nothing, only tells
    problems = ([] if CGPreflightScreenCaptureAccess() else ["нет разрешения «Запись экрана»: снимки окон будут пустыми"]) \
        + (["нет разрешения «Универсальный доступ»: клики и ввод не пройдут"] if _accessibility() is False else [])
    if problems:
        return WARN, "; ".join(problems), "Настройки → Конфиденциальность и безопасность: дай разрешения приложению Sena (и Терминалу, если Сена запущена из него)"
    return OK, "cliclick на месте, снимки экрана разрешены", ""


def _code(spend):
    if not _flag("use_claude"):
        return OFF, "выключен в настройках интеграций", ""
    path = shutil.which("claude") or next((p for p in ("/opt/homebrew/bin/claude", str(Path.home() / ".local/bin/claude")) if Path(p).exists()), None)
    if not path:
        return FAIL, "Claude Code не установлен", "Установи Claude Code и войди по подписке (claude в Терминале)"
    if power.is_off():
        return WAIT, "установлен; запуск проверю, когда Сена включена", ""
    done = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20)
    if done.returncode:
        return FAIL, f"не запускается: {(done.stderr or done.stdout).strip()[:150]}", "Открой claude в Терминале и войди по подписке"
    return OK, f"запускается ({done.stdout.strip()[:40]}), работает по подписке", ""


# ---------- outside services ----------
def _google(spend):
    if not _flag("use_google"):
        return OFF, "выключен в настройках интеграций", ""
    state = auth.state()
    if state == "none" and not config.GOOGLE_CLIENT_FILE.exists():   # never set up: off, not broken
        return OFF, "не подключён", f"Один раз: OAuth-клиент Google (Desktop app) — в {config.GOOGLE_CLIENT_FILE}; потом скажи Сене «войди в Google»"
    if state == "none":   # set up, and the sign-in is gone: mail is not watched — a failure, told once by the night
        return FAIL, "входа нет: почта, таблицы и диск не работают", SIGN_IN_GOOGLE
    if state == "expired":
        return FAIL, "вход истёк", SIGN_IN_GOOGLE
    profile = auth.call("GET", "https://gmail.googleapis.com/gmail/v1/users/me/profile")
    return OK, f"почта {profile.get('emailAddress', '')} доступна", ""


def _youtube(spend):
    if not _flag("use_youtube"):
        return OFF, "выключен в настройках интеграций", ""
    if auth.state() == "none":
        return OFF, "не подключён: нужен вход Google", SIGN_IN_GOOGLE
    if not youtube.signed_in():
        return FAIL, "нет входа с доступом к YouTube", SIGN_IN_GOOGLE + " и дай доступ к YouTube"
    youtube.youtube_channel()
    return OK, "канал читается", ""


def _luma(spend):
    if not _flag("use_luma"):
        return OFF, "выключена в настройках интеграций", ""
    state, said = luma.check()   # the key and the tool groups too: a bare "it answers" passed with a revoked key
    if state:
        return OK, f"Luma {said}", ""
    if state is None and config.LUMA_APP.exists():
        return WARN, "Luma сейчас не запущена; Сена откроет её сама, когда понадобится", ""
    if state is None:
        return FAIL, f"Luma не запущена, а программы нет по пути {config.LUMA_APP}", "Путь — LUMA_APP в .env"
    return FAIL, said, "Ключ — LUMA_KEY в .env (выпускается в Luma: Настройки → Панель разработчика → Ключи)"


def _github(spend):
    if not _flag("use_github"):
        return OFF, "выключен в настройках интеграций", ""
    if not config.GITHUB_TOKEN:
        return OFF, "не подключён: нет токена SENA_GITHUB_TOKEN", ""
    user, _ = github._get("/user")
    return OK, f"доступ есть: {user.get('login', '')}", ""


def _site(spend):
    if not _flag("use_site"):
        return OFF, "выключен в настройках интеграций", ""
    if not (config.SITE_URL and config.SITE_KEY):
        return OFF, "не подключён: нет SITE_URL или SENA_SITE_KEY", ""
    portfolio._get("/")
    return OK, "API сайта отвечает", ""


def _hireflow(spend):
    if not _flag("use_hireflow"):
        return OFF, "выключен в настройках интеграций", ""
    if not (config.HIREFLOW_URL and config.HIREFLOW_KEY):
        return OFF, "не подключён: нет HIREFLOW_URL или HIREFLOW_KEY", ""
    hireflow._get(hireflow.PREFIX + "/me")
    return OK, "Hire-flow отвечает, ключ принят", ""


def _maps(spend):
    if not _flag("use_maps"):
        return OFF, "выключены в настройках интеграций", ""
    if config.TOMTOM_KEY:
        maps_tomtom.geocode("Moscow")
        return OK, "TomTom отвечает (запасная — OpenStreetMap)", ""
    maps_osm.geocode("Moscow")
    return (OK, "OpenStreetMap отвечает; Google Maps не проверяю — это платный запрос", "") if config.GOOGLE_MAPS_KEY else \
        (OK, "OpenStreetMap отвечает (без пробок и рейтингов: ключа TomTom нет)", "")


def _travel(spend):
    if not _flag("use_travel"):
        return OFF, "выключены в настройках интеграций", ""
    if not config.TRAVELPAYOUTS_TOKEN:
        return OFF, "не подключено: нет TRAVELPAYOUTS_TOKEN", ""
    travel.flight_ideas("MOW")
    return OK, "Авиасейлс отвечает", ""


def _feed(source):
    # a real request, past the news cache: a feed that answered an hour ago proves nothing now
    try:
        return bool(news.parse(net.get(source["url"], headers=news.HEADERS, timeout=10, who=source["name"]).content))
    except Exception:
        return False


def _sources(spend):
    weather.lookup("Moscow", days=1)
    feeds = [s for s in news.SOURCES if s["topic"] == "world" and s["url"].startswith("http")][:5]
    with ThreadPoolExecutor(max_workers=len(feeds)) as pool:
        answered = sum(pool.map(_feed, feeds))
    if answered < len(feeds) // 2 + 1:
        return WARN, f"погода есть, но ленты новостей отвечают плохо: {answered} из {len(feeds)}", "Проверь интернет; ленты иногда недоступны сами"
    return OK, f"погода и ленты новостей отвечают ({answered} из {len(feeds)})", ""


def _website(spend):
    if not config.SITE_PUBLIC:
        return OFF, "адрес сайта не задан", ""
    text = uptime.site_report(config.SITE_PUBLIC)
    first = text.split("\n")[0]
    if "НЕ открывается" in first or "ошибкой" in first:
        return FAIL, first, "Проверь хостинг сайта"
    return (WARN, text.replace("\n", "; "), "Продли сертификат сайта") if "скоро истечёт" in text else (OK, first, "")


def _vault(spend):
    folder = config.VAULT_DIR
    if not folder.is_dir():
        return FAIL, f"папки заметок нет: {folder}", "Создай её или укажи SENA_VAULT в .env"
    if not os.access(folder, os.W_OK):
        return FAIL, "в папку заметок нельзя писать", f"Проверь права на {folder}"
    return OK, f"папка заметок на месте ({sum(1 for _ in folder.rglob('*.md'))} заметок)", ""


CHECKS = [
    Check("core", "Сена", "Ядро", _core), Check("disk", "Сена", "Место на диске", _disk), Check("databases", "Сена", "Базы данных", _databases),
    Check("backups", "Сена", "Копии", _backups), Check("keys", "Сена", "Ключи", _keys), Check("budget", "Сена", "Дневной предел расходов", _budget),
    Check("errors", "Сена", "Ошибки за сутки", _errors), Check("secrets", "Сена", "Хранилище секретов", _secrets), Check("exam", "Сена", "Экзамен ума", _exam),
    Check("internet", "Связь", "Интернет", _internet), Check("telegram_bot", "Связь", "Бот в Телеграме", _telegram_bot),
    Check("telegram_account", "Связь", "Аккаунт Телеграма", _telegram_account),
    Check("claude", "ИИ в облаке", "Claude: мозг и помощники", _claude), Check("gemini", "ИИ в облаке", "Gemini: голос Сены, запасной слух, картинки", _gemini),
    Check("openai", "ИИ в облаке", "OpenAI", _openai),
    Check("home_models", "Дома на Mac", "Домашние модели", _home_models, HOME_TIMEOUT), Check("hearing", "Дома на Mac", "Распознавание речи", _hearing),
    Check("voice", "Дома на Mac", "Запасной голос дома", _voice), Check("listening", "Дома на Mac", "Имя «Сена» и голоса людей", _listening),
    Check("reading", "Дома на Mac", "Чтение фото и PDF", _reading),
    Check("browser", "Помощники", "Браузер", _browser), Check("computer", "Помощники", "Компьютер", _computer),
    Check("code", "Помощники", "Код: Claude Code", _code),
    Check("google", "Сервисы", "Google: почта, таблицы, диск", _google), Check("youtube", "Сервисы", "YouTube", _youtube),
    Check("luma", "Сервисы", "Luma", _luma), Check("github", "Сервисы", "GitHub", _github), Check("site", "Сервисы", "Сайт-портфолио: API", _site),
    Check("hireflow", "Сервисы", "Hire-flow", _hireflow), Check("maps", "Сервисы", "Карты", _maps), Check("travel", "Сервисы", "Билеты", _travel),
    Check("sources", "Сервисы", "Погода и новости", _sources), Check("website", "Сервисы", "Сайт открывается", _website),
    Check("vault", "Сервисы", "Заметки Obsidian", _vault),
]
BY_ID = {c.id: c for c in CHECKS}


# ---------- running ----------
FIXES = (("на счёте openai", "Пополни баланс OpenAI: platform.openai.com → Billing. До тех пор не работает то, что на нём (список — в проверке «OpenAI»)"),
         ("на счёте claude", "Пополни баланс Claude: console.anthropic.com → Billing. До тех пор мозг Сены не отвечает"),
         ("бюджет", "Подними дневной предел в приложении: Настройки → Поведение"),
         ("ключ", "Проверь ключ в .env: он отозван или вписан с ошибкой"), ("нет связи", "Проверь интернет"),
         ("не ответил", "Сервис не отвечает: повтори проверку позже"), ("перегружен", "Сервис перегружен: повтори проверку позже"))


def _failure(e):
    """A failed check in words: what went wrong and what to do."""
    text = llm.explain(e) if type(e).__module__.startswith(("openai", "anthropic")) or type(e).__name__ in ("LimitReached", "ClaudeError") else str(e) or type(e).__name__
    fix = next((f for word, f in FIXES if word in text.lower()), "Подробности — в журнале событий")
    return FAIL, text[:300], fix


def _result(check, status, summary, fix, seconds):
    return {"id": check.id, "group": check.group, "title": check.title, "status": status, "summary": summary, "fix": fix,
            "seconds": round(seconds, 1), "t": time.time()}


def _one(check, spend):
    started = time.time()
    try:
        status, summary, fix = check.func(spend)
    except Exception as e:
        status, summary, fix = _failure(e)
        events.error(f"Проверка системы: {check.title}", e)
    return _result(check, status, summary, fix, time.time() - started)


def pick(part=""):
    """The checks a request names: all; one by its id; a group; or words matched to titles and ids («google», «телеграм»)."""
    part = (part or "").strip().lower()
    if not part or part in ("все", "всё", "all", "система"):
        return list(CHECKS)
    if part in BY_ID:
        return [BY_ID[part]]
    chosen = [c for c in CHECKS if part == c.group.lower()] or [c for c in CHECKS if part in c.title.lower() or part in c.id]
    return chosen or [c for c in CHECKS if any(w in c.title.lower() or w in c.id for w in part.split() if len(w) > 2)]


def last():
    return store.load_json(REPORT, {}) or {}


def running():
    return dict(_running) if _running["since"] else None


def run(part="", spend=True):
    """Run the checks a request names, in parallel, each within its own time limit; every result is saved as it comes, so the
    panel shows the progress. spend — let the checks that need a paid call make one (less than a hundredth of a cent);
    with Sena switched off nothing is spent anyway. Returns [result] in the order of CHECKS."""
    return _run(pick(part), spend)


def _run(chosen, spend):
    spend = spend and not power.is_off()
    with _lock:
        if _running["since"]:
            raise RuntimeError("проверка уже идёт: дождись её конца")
        _running.update(since=time.time(), done=0, total=len(chosen))
    results = {}
    pool = ThreadPoolExecutor(max_workers=max(1, len(chosen)))   # all start at once: none uses up its time waiting in a queue
    started = time.time()
    one = status.carried(_one)   # a check that pays a hundredth of a cent pays it for the task that asked (the system_check tool)
    pending = {pool.submit(one, c, spend): c for c in chosen}
    try:
        while pending:
            soonest = min(started + c.timeout for c in pending.values())
            done, _ = wait(pending, timeout=max(0, soonest - time.time()), return_when=FIRST_COMPLETED)
            fresh = []
            for future in done:
                pending.pop(future)
                fresh.append(future.result())   # _one never raises
            for future, c in list(pending.items()):
                if time.time() >= started + c.timeout:   # it didn't finish in its time: the others don't wait for it
                    del pending[future]
                    fresh.append(_result(c, FAIL, f"не ответило за {c.timeout} с", "Повтори проверку; если опять — подробности в журнале событий", c.timeout))
            results.update((r["id"], r) for r in fresh)
            _save(fresh)
            _running["done"] = len(results)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        _running.update(since=None, done=0, total=0)
    ordered = [results[c.id] for c in chosen]
    bad = [r for r in ordered if r["status"] == FAIL]
    events.log("check", f"проверка системы: работает {sum(r['status'] == OK for r in ordered)}, с оговоркой {sum(r['status'] == WARN for r in ordered)}, "
                        f"не работает {len(bad)} из {len(ordered)}" + (": " + ", ".join(r["title"] for r in bad) if bad else ""),
               failed=bool(bad) or None, checks=len(ordered), spend=spend or None)
    return ordered


def _save(rows):
    with _lock:
        report = last()
        results = {r["id"]: r for r in report.get("results", [])}
        results.update({r["id"]: r for r in rows})
        report.update(t=time.time(), results=[results[c.id] for c in CHECKS if c.id in results])
        store.save_json(REPORT, report, indent=1)


def text(results):
    """A report in words: what doesn't work and what to do first, then what works with a caveat, then what works."""
    bad = [r for r in results if r["status"] == FAIL]
    warn = [r for r in results if r["status"] == WARN]
    wait = [r for r in results if r["status"] == WAIT]
    good = [r for r in results if r["status"] == OK]
    lines = [f"Проверено частей: {len(results)}. Работает {len(good)}, с оговоркой {len(warn)}, не работает {len(bad)}."]
    for title, rows in (("Не работает", bad), ("С оговоркой", warn)):
        if rows:
            lines.append(f"{title}:")
            lines += [f"- {r['title']}: {r['summary']}" + (f". Что сделать: {r['fix']}" if r["fix"] else "") for r in rows]
    if wait:
        lines.append("Ждёт включения Сены: " + ", ".join(r["title"] for r in wait) + ".")
    if good:
        lines.append("Работает: " + ", ".join(r["title"] for r in good) + ".")
    off = [r for r in results if r["status"] == OFF]
    if off:
        lines.append("Выключено или не подключено: " + ", ".join(f"{r['title']} ({r['summary']})" for r in off) + ".")
    return "\n".join(lines)


def night():
    """The nightly check: everything, with the paid calls of a hundredth of a cent. A failure is tried once more after a pause, so
    a blip of the network isn't told. What broke since the last time it was told is told once, quietly (the message waits for
    the morning), and so is what got fixed. Returns the text sent, or ""."""
    try:
        results = run(spend=True)
        again = [BY_ID[r["id"]] for r in results if r["status"] == FAIL]
        if again:
            time.sleep(RECHECK_PAUSE)
            fresh = {r["id"]: r for r in _run(again, spend=True)}
            results = [fresh.get(r["id"], r) for r in results]
    except RuntimeError:   # the owner is running a check right now: tonight's result is his
        return ""
    failing = {r["id"] for r in results if r["status"] == FAIL}
    with _lock:
        report = last()
        told = set(report.get("told", []))
        report["told"] = sorted(failing)
        store.save_json(REPORT, report, indent=1)
    broken, fixed = [r for r in results if r["id"] in failing - told], [r for r in results if r["id"] in told - failing]
    if not broken and not fixed:
        return ""
    lines = (["Ночная проверка: перестало работать —"] + [f"- {r['title']}: {r['summary']}. Что сделать: {r['fix']}" for r in broken] if broken else []) \
        + ([("Снова работает: " if broken else "Ночная проверка: снова работает — ") + ", ".join(r["title"] for r in fixed) + "."] if fixed else [])
    message = "\n".join(lines)
    api.notify(message, quiet=True)
    history.reply(config.OWNER_ID, message)   # into the correspondence: his «почини» is answered knowing what broke
    return message


# ---------- Sena's tool ----------
def system_check(part=""):
    """Run the system check (all of it, or the named part) and tell what doesn't work and why."""
    if not pick(part):
        return f"Такой части в проверке нет: «{part}». Есть: " + ", ".join(c.title for c in CHECKS) + "."
    try:
        return text(run(part))
    except RuntimeError as e:
        return str(e)


TOOLS = [tool("system_check", "Проверка системы: каждая часть пробуется настоящим действием (бесплатно, где нужен платный вызов — дешевле сотой цента) — "
                              "ядро, место, базы, копии, ключи, предел расходов, ошибки за сутки, связь, Claude (мозг и помощники), Gemini (голос, слух, "
                              "картинки), OpenAI, домашние модели, "
                              "слух, голос, помощники (браузер, компьютер, Claude Code), Google, YouTube, Luma, GitHub, сайт, Hire-flow, карты, билеты, "
                              "новости, заметки. "
                              "Ответ: что не работает, почему и что сделать, потом что работает. Без part — всё (до полутора минут).",
              {"part": string("Что проверить, если не всё: часть или группа словами — «Google», «Телеграм», «мозг», «браузер», «помощники», «сервисы»")})]
FUNCTIONS = {"system_check": system_check}
