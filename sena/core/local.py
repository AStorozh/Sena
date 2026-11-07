"""The home: models that run on this Mac, so what they see never leaves it. One place for every local model, as llm.py is for the cloud:
- llama-server (one process on 127.0.0.1, router mode): memory fingerprints and the home model — narrow judgments in strict JSON
  and descriptions of photos; a model loads on the first request and sleeps after config.LOCAL_IDLE_SEC without work;
- whisper.cpp: speech to text — large-v3-turbo for the owner's voice and commands, small to catch the name in a second;
- resemblyzer: voice fingerprints — who is speaking, and whether a spoken command is the owner's;
- macOS Vision and PDFKit: the text of documents and photos, labels of what is in a photo;
- Piper (the Russian voice Irina) or the system voice Milena: speaking.
Every call goes to the event journal and to the cost journal at zero cost (provider "local"), so the reports show what stayed home.
A missing model or program raises Unavailable with the reason in words; whether to fall back to the cloud is decided by core/roles.py."""
import atexit
import base64
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import warnings
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from sena import config
from sena.core import costs, events, power, safety

WHISPER = "whisper-cli"
SERVER = "llama-server"
PRESETS_FILE = config.STATE_DIR / "local_models.ini"   # generated from config on every start: which models the server may load
PID_FILE = config.STATE_DIR / "local_server.json"      # the server this Sena started: pid and the presets it was started with
EMBED, CHAT = "embed", "chat"                          # the server's names for the two models
IMAGE_SIDE = 1024   # a photo is shrunk to this side before the home model looks at it: enough to read a screenshot, fast to encode
PDF_PAGES = 60      # pages of a scan read by their picture (a second or two a page)
PDF_MOST = 2000     # pages of a PDF read at all: a text layer reads in a blink, a book or a long contract is read whole

# typical Whisper hallucinations on silence and noise — discarded (VAD does the main filtering, this is a safety net)
JUNK = re.compile(r"субтитр|редактор субтитр|коррект[ои]р а\.|продолжение следует|"
                  r"спасибо за просмотр|подписывайтесь|dimator|игорь негода", re.I | re.U)

_proc = None
_lock = threading.Lock()


class _Turns:
    """One recognition at a time — the GPU is shared, a queue is faster than a crowd — and the urgent ones first: the owner's own
    words and a quick check for the name go before overheard speech read accurately in the background (phrases of a busy room
    came faster than they were read, and his command waited behind them; a plain lock keeps no order at all). The background
    queue has a limit: a room that talks faster than it is read (a TV, two people) grew it by a thread and a sound file a phrase,
    for good; past BEHIND a phrase keeps its quick text (the caller's choice when this raises Unavailable)."""
    BEHIND = 20

    def __init__(self):
        self.free, self.urgent, self.behind = True, 0, 0
        self.changed = threading.Condition()

    @contextmanager
    def turn(self, urgent):
        with self.changed:
            full = not urgent and self.behind >= self.BEHIND
            if not full:
                self.urgent += int(urgent)
                self.behind += int(not urgent)
                try:
                    self.changed.wait_for(lambda: self.free and (urgent or not self.urgent))
                finally:
                    self.urgent -= int(urgent)
                    self.behind -= int(not urgent)
                self.free = False
        if full:
            events.log("transcribe", "точное распознавание не успевает: фраза осталась в быстром тексте", local=True, dropped=True)
            raise Unavailable("распознавание не успевает за разговором: фраза остаётся в быстром тексте")
        try:
            yield
        finally:
            with self.changed:
                self.free = True
                self.changed.notify_all()


_whisper = _Turns()


class Unavailable(RuntimeError):
    """The home engine is missing or doesn't answer; the text says why, in words for the owner."""


def _on(what):
    # Sena switched off (the screen without the core) starts none of her programs: no model server, no recognition, no voice.
    # Nor does a core that is stopping: a server started now would outlive the stop that has just put her programs away
    if power.is_off() or power.is_stopping():
        raise Unavailable(power.refusal(what))


# ---------- what is installed ----------
def path_of(name):
    return config.MODELS_DIR / name


def installed():
    """Which home engines are ready: {engine: True/False} (for the screen and for the roles)."""
    return {"embed": path_of(config.LOCAL_EMBED).exists() and bool(shutil.which(SERVER)),
            "chat": path_of(config.LOCAL_CHAT).exists() and bool(shutil.which(SERVER)),
            "eyes": path_of(config.LOCAL_CHAT).exists() and path_of(config.LOCAL_VISION).exists() and bool(shutil.which(SERVER)),
            "hearing": (config.WHISPER_MODEL.exists() or config.WHISPER_SMALL.exists()) and bool(shutil.which(WHISPER)) and bool(shutil.which("ffmpeg")),
            "hearing_best": config.WHISPER_MODEL.exists(),
            "reading": _can_read(),
            "voice": path_of(config.LOCAL_VOICE).exists() or bool(shutil.which("say"))}


def _can_read():
    # whether macOS Vision is there, without importing it: the import through PyObjC took half a second of the screen's first look
    return all(importlib.util.find_spec(name) is not None for name in ("Quartz", "Vision"))


# ---------- the model server ----------
def _presets():
    # one section per model; a child process per section is started by the router when that model is first asked for
    out = ["version = 1", ""]
    if path_of(config.LOCAL_EMBED).exists():
        # cache-ram: llama-server keeps up to 8 GiB of past prompts in the Mac's memory by default; fingerprints never reuse one,
        # and the chat model's short, varied prompts rarely do (Ollama's issue 18264: 9.4 GB of memory, 1.6 GB once capped)
        out += [f"[{EMBED}]", f"model = {path_of(config.LOCAL_EMBED)}", "embeddings = true", "pooling = mean",
                "ctx-size = 2048", "batch-size = 2048", "ubatch-size = 2048", "cache-ram = 0", f"sleep-idle-seconds = {config.LOCAL_IDLE_SEC}", ""]
    if path_of(config.LOCAL_CHAT).exists():
        out += [f"[{CHAT}]", f"model = {path_of(config.LOCAL_CHAT)}", "ctx-size = 16384", "parallel = 1", "cache-ram = 512",
                "reasoning-budget = 0", f"sleep-idle-seconds = {config.LOCAL_IDLE_SEC}"]   # no hidden thinking: strict JSON, fast
        if path_of(config.LOCAL_VISION).exists():
            out.append(f"mmproj = {path_of(config.LOCAL_VISION)}")   # the same model sees: one copy in memory, not two
        out.append("")
    return "\n".join(out)


def _url(path):
    return f"http://127.0.0.1:{config.LOCAL_PORT}{path}"


def _get(path, timeout=3):
    with urllib.request.urlopen(_url(path), timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _alive():
    # someone answers on our port and knows our models
    try:
        names = {m.get("id") for m in _get("/v1/models").get("data", [])}
    except (OSError, ValueError):
        return False
    return bool(names & {EMBED, CHAT})


def _owner():
    """(the server's pid, the pid of the Sena process that looks after it, or None when the file is older than owners)."""
    try:
        data = json.loads(PID_FILE.read_text())
    except (OSError, ValueError):
        return 0, None
    return int(data.get("pid") or 0), data.get("owner")


SENA_PROCESS = re.compile(r"python[\w.]*\s(?:.*\s)?-m\s+sena(?:\.\w+)*(?:\s|$)", re.I)   # python … -m sena, -m sena.exam, -m sena.home …


def _lives(pid):
    """A Sena process is alive under this pid: Python running one of her modules. An editor or a terminal open in the project
    folder (its command line says "Sena" too), a pid some other program got since, or a check that got no answer doesn't
    count: a server no living Sena can be shown to look after is a leftover."""
    try:
        out = subprocess.run(["ps", "-p", str(int(pid)), "-o", "command="], capture_output=True, text=True, timeout=3).stdout
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return bool(SENA_PROCESS.search(out))


def _stop_previous():
    # a server left by an earlier Sena with other presets: stop it, a fresh one starts with the current models.
    # Only if that pid really is a llama-server: a pid file from long ago may name some other program by now. And never the server
    # of another living Sena process: an exam or a script that shared the data folder once stopped the core's at its exit
    stopped = False
    server, owner = _owner()
    if owner and owner != os.getpid() and _lives(owner):
        return False
    try:
        pid = server
        name = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True).stdout.strip() if pid else ""
        if pid and name.endswith(SERVER):
            os.killpg(pid, signal.SIGTERM)
            stopped = True
            time.sleep(1)
    except (OSError, ValueError, ProcessLookupError):
        pass
    PID_FILE.unlink(missing_ok=True)
    return stopped


_ready = None   # the presets digest of the server known to be up: no health check before every request


def _ensure():
    """The model server is up: reused if it already runs with the same presets, started otherwise."""
    global _proc, _ready
    _on("домашние модели")
    presets = _presets()
    digest = hashlib.md5(presets.encode()).hexdigest()
    if _ready == digest and (_proc is None or _proc.poll() is None):
        return
    with _lock:
        try:
            same = json.loads(PID_FILE.read_text()).get("digest") == digest
        except (OSError, ValueError):
            same = False
        if same and _alive():
            server, owner = _owner()
            if owner != os.getpid() and not (owner and _lives(owner)):   # left by a Sena that is gone: this one looks after it now
                PID_FILE.write_text(json.dumps({"pid": server, "digest": digest, "owner": os.getpid()}), encoding="utf-8")
            _ready = digest
            return
        if not shutil.which(SERVER):
            raise Unavailable("на Mac нет llama-server (brew install llama.cpp)")
        if "[" not in presets:
            raise Unavailable("домашние модели не скачаны: python -m sena.home setup")
        _stop_previous()
        PRESETS_FILE.write_text(presets, encoding="utf-8")
        log = open(config.LOGS_DIR / "local.log", "ab")
        _proc = subprocess.Popen([SERVER, "--models-preset", str(PRESETS_FILE), "--host", "127.0.0.1", "--port", str(config.LOCAL_PORT),
                                  "--models-max", "2", "--no-webui", "--offline"],   # the home never goes online, by the server's own switch too
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        PID_FILE.write_text(json.dumps({"pid": _proc.pid, "digest": digest, "owner": os.getpid()}), encoding="utf-8")
        for _ in range(100):
            if _alive():
                _ready = digest
                events.log("start", "домашние модели: сервер запущен", local=True, port=config.LOCAL_PORT)
                return
            if _proc.poll() is not None:
                break
            time.sleep(0.2)
        raise Unavailable("сервер домашних моделей не поднялся (журнал: data/logs/local.log)")


def stop():
    """Stop the model server: the one this Sena started, and one an earlier run left behind when it was killed (its pid file
    names it). At exit and when Sena is switched off: the models give the Mac's memory back."""
    global _proc, _ready
    running = _proc is not None and _proc.poll() is None
    if running:
        try:
            os.killpg(_proc.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        PID_FILE.unlink(missing_ok=True)
    _proc, _ready = None, None
    if PID_FILE.exists():
        running = _stop_previous() or running
    return running


atexit.register(stop)


def _post(path, body, timeout=300):
    # the one door to the server: tests replace it (tests/__init__.py), so no test ever starts a model
    global _ready
    _ensure()
    request = urllib.request.Request(_url(path), data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise Unavailable(f"домашняя модель ответила ошибкой {e.code}: {e.read()[:200].decode(errors='replace')}") from e
    except OSError as e:
        _ready = None   # the server may have gone: the next request checks and restarts it
        raise Unavailable(f"домашняя модель не отвечает: {e}") from e


def _account(service, model, started, purpose=None, **units):
    costs.record("local", service, model, units={"calls": 1, "seconds": round(time.time() - started, 2), **units}, purpose=purpose)


# ---------- fingerprints ----------
def embed(texts, query=False, purpose=None):
    """Fingerprints of texts, normalized, config.EMBED_DIMS wide (the model is Matryoshka-trained: its first dimensions carry
    the meaning). query — a question rather than a record: the model has its own prefix for each."""
    if not texts:
        return []
    prefix = config.LOCAL_EMBED_PROMPTS[0 if query else 1]
    started = time.time()
    reply = _post("/v1/embeddings", {"model": EMBED, "input": [prefix + t for t in texts]})
    rows = sorted(reply.get("data", []), key=lambda d: d.get("index", 0))
    if len(rows) != len(texts):
        raise Unavailable("домашняя модель отпечатков вернула не столько отпечатков, сколько текстов")
    out = []
    for row in rows:
        vec = np.asarray(row["embedding"], dtype=np.float32)[:config.EMBED_DIMS]
        if not np.isfinite(vec).all():   # one NaN would make every search score with it NaN (Ollama guards its embeddings the same way)
            raise Unavailable("домашняя модель отпечатков вернула испорченный отпечаток")
        norm = float(np.linalg.norm(vec))
        out.append(vec / norm if norm else vec)
    _account("embeddings", config.LOCAL_EMBED, started, purpose, texts=len(texts), tokens=(reply.get("usage") or {}).get("prompt_tokens", 0))
    events.log("embeddings", "", purpose=purpose, model=config.LOCAL_EMBED, count=len(texts), local=True)
    return out


def stamp():
    """What every home fingerprint is made by (the registry stamps vectors with it: another model's vectors are never mixed in)."""
    return f"{config.LOCAL_EMBED}/{config.EMBED_DIMS}"


# ---------- the home model ----------
LONGEST = 4096   # tokens a home answer may grow to when the first try was cut at its limit


def _image_part(path):
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "image.jpg"
        subprocess.run(["sips", "-s", "format", "jpeg", "-Z", str(IMAGE_SIDE), str(path), "--out", str(out)], capture_output=True, timeout=60)
        if not out.exists():
            raise Unavailable(f"картинку «{Path(path).name}» не удалось открыть")
        data = base64.b64encode(out.read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + data}}


def chat(instructions, text="", schema=None, image=None, max_tokens=800, purpose=None, private=False):
    """One answer from the home model. schema — a JSON schema: the answer is generated inside it and returned as data;
    image — a picture to look at (needs the vision file). private — other people's words: the journal keeps only sizes."""
    if image is not None and not path_of(config.LOCAL_VISION).exists():
        raise Unavailable("у домашней модели нет зрения: python -m sena.home setup")
    content = [{"type": "text", "text": text}] if text else []
    if image is not None:
        content.append(_image_part(image))
    body = {"model": CHAT, "temperature": 0.1, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": content if image is not None else text}]}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}
    for limit in dict.fromkeys((max_tokens, min(max_tokens * 2, LONGEST))):   # cut at the limit: once more with room, not "the home is down" (DSPy)
        body["max_tokens"] = limit
        started = time.time()
        reply = _post("/v1/chat/completions", body)
        choice = (reply.get("choices") or [{}])[0]
        answer = (choice.get("message") or {}).get("content") or ""
        usage = reply.get("usage") or {}
        _account("chat", config.LOCAL_CHAT, started, purpose, input=usage.get("prompt_tokens", 0), output=usage.get("completion_tokens", 0))
        events.log("model", answer, model=config.LOCAL_CHAT, local=True, purpose=purpose, input=text or "(картинка)", cut=choice.get("finish_reason") == "length" or None,
                   instructions=instructions[:300], tokens={"input": usage.get("prompt_tokens"), "output": usage.get("completion_tokens")}, private=private)
        if choice.get("finish_reason") != "length":
            break
        if _looping(answer):   # cut because it went round in circles: more room only buys a longer loop (Ollama aborts a repeat)
            raise Unavailable("домашняя модель зациклилась: повторяет одно и то же")
    looped = safety.degenerate(answer, short_ok=True)   # short on purpose ({"notify": false}) is fine, a loop is not
    if looped:   # a home answer that went round in circles is no answer: the job asks the cloud or waits (Mastra checks every one)
        raise Unavailable(f"домашняя модель ответила по кругу ({looped})")
    if not schema:
        return answer.strip()
    try:
        return json.loads(answer)
    except ValueError as e:
        raise Unavailable("домашняя модель вернула не JSON") from e


def _looping(text, most=60, times=4):
    """Does the text end in one short piece said again and again (a model stuck in a loop)?"""
    for size in range(1, most + 1):
        piece = text[-size:]
        if piece.strip() and text.endswith(piece * max(times, 120 // size)):
            return True
    return False


def describe(path, purpose="что на картинке"):
    """What is in a photo, in one or two Russian sentences: for finding it later by meaning."""
    return chat("Опиши, что на картинке, для поиска по памяти: 1–2 фразы по-русски, конкретно — предметы, люди, место, "
                "заметный текст. Без вступлений и догадок.", image=path, max_tokens=160, purpose=purpose)


# ---------- hearing ----------
def to_wav16(audio):
    """Any audio (bytes or a path) -> a WAV file 16 kHz mono (whisper and the voice print expect it); the caller deletes it."""
    if isinstance(audio, (str, Path)):
        audio = Path(audio).read_bytes()
    src = tempfile.NamedTemporaryFile(suffix=".in", delete=False)
    src.write(audio)
    src.close()
    out = src.name + ".wav"
    try:
        subprocess.run(["ffmpeg", "-y", "-i", src.name, "-ar", "16000", "-ac", "1", out], capture_output=True, timeout=120)
    except FileNotFoundError as e:
        raise Unavailable("на Mac нет ffmpeg (brew install ffmpeg)") from e
    finally:
        os.unlink(src.name)
    if not os.path.exists(out):
        raise Unavailable("звук не удалось разобрать (ffmpeg)")
    return out


def clean(text):
    """Whisper's text without tags, hallucinations and one-word noise; an empty string if nothing human is left."""
    text = re.sub(r"[\[\(].*?[\]\)]", "", text).strip()   # strip out [music], (applause)
    if len(text) < 4 or text.isupper():                   # "CALM MUSIC" is a tag, not speech
        return ""
    words = text.split()
    if len(set(words)) <= 1 or len(words) < 2 or JUNK.search(text):   # repeating junk, a single short word, a known hallucination
        return ""
    return text


def transcribe(wav_path, best=True, purpose=None, private=False, journal=True):
    """Speech in a 16 kHz WAV -> text. best — large-v3-turbo (accurate, ~4 s a phrase on the M3), otherwise small (~1 s).
    --vad: only stretches where Silero hears speech are recognized, so music and noise give nothing rather than invented text;
    -l auto: Russian and English; -vp 300: a margin before speech, or VAD clips the first sound of the name.
    private — someone else's speech: the journal keeps only its size; journal=False — the caller records the line itself
    (background hearing writes one "heard" event per line, not one per recognition pass)."""
    _on("распознавание речи")
    model = config.WHISPER_MODEL if best and config.WHISPER_MODEL.exists() else config.WHISPER_SMALL
    if not model.exists() or not shutil.which(WHISPER):
        raise Unavailable("нет распознавания речи на Mac: python -m sena.home setup (и brew install whisper-cpp)")
    started = time.time()
    with _whisper.turn(urgent=not (private and best)):   # someone else's speech read accurately waits for the rest
        done = subprocess.run([WHISPER, "-m", str(model), "-l", "auto", "-nt", "-np", "--vad", "-vm", str(config.VAD_MODEL), "-vp", "300",
                               "-f", str(wav_path)], capture_output=True, text=True, timeout=600)
    if done.returncode != 0:
        raise Unavailable(f"распознавание речи упало: {done.stderr.strip()[-200:]}")
    text = clean(done.stdout.strip())
    _account("transcribe", model.name, started, purpose, audio_seconds=round(os.path.getsize(wav_path) / 32000, 1))
    if journal:
        events.log("transcribe", text, purpose=purpose, local=True, model=model.name, private=private)
    return text


def hear(audio, purpose="голос в текст"):
    """The owner's voice (bytes in any format) -> text, with the accurate model."""
    wav = to_wav16(audio)
    try:
        return transcribe(wav, best=True, purpose=purpose)
    finally:
        os.unlink(wav)


_encoder = None
_encoder_lock = threading.Lock()


def voice_print(wav_path):
    """A voice fingerprint (resemblyzer) and how many seconds of clean speech the chunk holds (pauses are cut out)."""
    global _encoder
    _on("отпечатки голоса")
    with _encoder_lock:
        if _encoder is None:
            # resemblyzer's import and its model's load warn about their own dependencies: silenced here only — a filter set for
            # good silenced every warning of the whole process
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from resemblyzer import VoiceEncoder
                _encoder = VoiceEncoder(verbose=False)
    from resemblyzer import preprocess_wav
    wav = preprocess_wav(wav_path)
    return _encoder.embed_utterance(wav).tolist(), len(wav) / 16000


def owner_print(audio):
    """The fingerprint of a voice message the owner himself recorded (bytes in any format): (vector, seconds)."""
    wav = to_wav16(audio)
    try:
        return voice_print(wav)
    finally:
        os.unlink(wav)


# ---------- reading: documents and photos (macOS Vision and PDFKit) ----------
def _vision():
    try:
        import Quartz  # noqa: F401
        import Vision
        return Vision
    except ImportError:
        return None


def _recognize(handler):
    # Russian and English, the accurate pass with language correction; lines top to bottom
    Vision = _vision()
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(["ru-RU", "en-US"])
    request.setUsesLanguageCorrection_(True)
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise Unavailable(f"распознавание текста не удалось: {error}")
    boxes = []
    for obs in request.results() or []:
        top = obs.topCandidates_(1)
        if top:
            box = obs.boundingBox()   # from 0 to 1, the origin at the bottom left
            boxes.append((box.origin.y + box.size.height, box.origin.y, box.origin.x, top[0].string()))
    return _lines(boxes)


def _lines(boxes):
    """Recognized pieces [(top, bottom, left, text)] as lines of the page: pieces side by side form one line, left to right, joined
    by " | " — the cells of a table row stay in their row (sorted by height alone, a row's cells scattered among its
    neighbours, as RAGFlow's check on a scanned invoice here showed)."""
    lines = []   # [top, bottom, [(left, text)]]: a line keeps the span of its first piece
    for top, bottom, left, text in sorted(boxes, key=lambda b: -b[0]):
        line = lines[-1] if lines else None
        if line and min(top, line[0]) - max(bottom, line[1]) > 0.5 * min(top - bottom, line[0] - line[1]):
            line[2].append((left, text))
        else:
            lines.append([top, bottom, [(left, text)]])
    return "\n".join(" | ".join(t for _, t in sorted(pieces)) for _, _, pieces in lines)


def _handler(path):
    # the picture drawn on white: dark text on a transparent PNG would otherwise read as black on black; HEIC and WebP open too
    Vision = _vision()
    if Vision is None:
        raise Unavailable("нет чтения картинок: pip install pyobjc-framework-Vision pyobjc-framework-Quartz")
    from AppKit import NSBitmapImageRep, NSColor, NSGraphicsContext, NSImage, NSRectFill
    image = NSImage.alloc().initWithContentsOfFile_(str(path))
    if image is None:
        raise Unavailable(f"картинку «{Path(path).name}» не удалось открыть")
    reps = image.representations()
    width, height = (reps[0].pixelsWide(), reps[0].pixelsHigh()) if reps and reps[0].pixelsWide() > 0 else map(int, image.size())
    scale = min(1.0, 4096 / max(width, height, 1))   # Vision needs no more than this; huge photos would only cost memory
    width, height = max(1, int(width * scale)), max(1, int(height * scale))
    canvas = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, width, height, 8, 4, True, False, "NSDeviceRGBColorSpace", 0, 0)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(canvas))
    NSColor.whiteColor().set()
    NSRectFill(((0, 0), (width, height)))
    image.drawInRect_(((0, 0), (width, height)))
    NSGraphicsContext.restoreGraphicsState()
    return Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(canvas.CGImage(), None)


def ocr(path, purpose="текст с картинки"):
    """The text in a photo or a screenshot ("" if there is none)."""
    started = time.time()
    text = _recognize(_handler(path))
    _account("read", "vision-ocr", started, purpose, chars=len(text))
    events.log("read", f"текст с картинки: {len(text)} знаков", local=True, file=Path(path).name)
    return text


def labels(path, top=8, purpose="что на картинке"):
    """What a photo shows, as English labels the system recognizes ("cat", "document", "beach")."""
    Vision = _vision()
    started = time.time()
    handler = _handler(path)
    request = Vision.VNClassifyImageRequest.alloc().init()
    ok, _ = handler.performRequests_error_([request], None)
    found = sorted(((o.confidence(), o.identifier()) for o in (request.results() or []) if o.confidence() >= 0.3), reverse=True) if ok else []
    names = [name.replace("_", " ") for _, name in found[:top]]
    _account("read", "vision-labels", started, purpose, labels=len(names))
    return names


def pdf_text(path, max_pages=PDF_MOST, purpose="текст PDF", scans=PDF_PAGES):
    """The text of a PDF, every page headed by its number as printed ([стр. 12]) so an answer can cite it: its text layer, and
    where a page has none (a scan) — recognized from the page's picture, up to scans such pages (RAGFlow keeps the page of every
    line; here a book was cut at 60 pages and no answer could name a page)."""
    if _vision() is None:
        raise Unavailable("нет чтения PDF: pip install pyobjc-framework-Vision pyobjc-framework-Quartz")
    import Quartz
    import Vision
    from Foundation import NSURL
    started = time.time()
    doc = Quartz.PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(str(path)))
    if doc is None:
        raise Unavailable(f"PDF «{Path(path).name}» не открылся")
    if doc.isLocked() and not doc.unlockWithPassword_(""):
        raise Unavailable(f"PDF «{Path(path).name}» под паролем")
    pages, scanned, unread = [], 0, 0
    count = min(doc.pageCount(), max_pages)
    for i in range(count):
        page = doc.pageAtIndex_(i)
        text = (page.string() or "").strip()
        if len(text) < 20 and scanned >= scans:   # a scan past the limit of pictures read
            unread += 1
            continue
        if len(text) < 20:   # no text layer: a scan or a photo of a page — read the picture
            box = page.boundsForBox_(Quartz.kPDFDisplayBoxMediaBox)
            scale = 2000 / max(box.size.width, box.size.height, 1)
            image = page.thumbnailOfSize_forBox_((box.size.width * scale, box.size.height * scale), Quartz.kPDFDisplayBoxMediaBox)
            cg = image.CGImageForProposedRect_context_hints_(None, None, None)[0] if image is not None else None
            if cg is not None:
                text = _recognize(Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None))
                scanned += 1
        if text:
            if re.search("[а-яё]", text, re.I):   # macOS's system font writes "к" as the Greenlandic "ĸ": word search missed "Пункт"
                text = text.replace("ĸ", "к")
            label = str(page.label() or "").strip() or str(i + 1)
            pages.append(f"[стр. {label}" + (f", в файле {i + 1}" if label != str(i + 1) else "") + f"]\n{text}")
    out = "\n\n".join(pages)
    if doc.pageCount() > count:
        out += f"\n\n[ещё страниц не прочитано: {doc.pageCount() - count}]"
    if unread:
        out += f"\n\n[страниц-сканов без текста не распознано: {unread}]"
    _account("read", "pdfkit", started, purpose, pages=count, scanned=scanned)
    events.log("read", f"PDF «{Path(path).name}»: страниц {count}, из них по картинке {scanned}", local=True)
    return out


# ---------- speaking ----------
def speak(text, purpose="озвучка"):
    """Text -> mp3 with the home voice: Piper's Irina if downloaded, otherwise macOS Milena."""
    _on("озвучка")
    started = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        raw, mp3 = Path(tmp) / "voice.wav", Path(tmp) / "voice.mp3"
        voice = path_of(config.LOCAL_VOICE)
        try:
            from piper import PiperVoice
        except ImportError:   # the voice file without the program: the system voice speaks instead
            PiperVoice = None
        if voice.exists() and PiperVoice is not None:
            import wave
            with wave.open(str(raw), "wb") as w:
                PiperVoice.load(str(voice)).synthesize_wav(text, w)
            used = voice.stem
        elif shutil.which("say"):
            raw = Path(tmp) / "voice.aiff"
            subprocess.run(["say", "-v", config.LOCAL_VOICE_FALLBACK, "-o", str(raw), text], capture_output=True, timeout=120)
            used = config.LOCAL_VOICE_FALLBACK
        else:
            raise Unavailable("нет домашнего голоса")
        subprocess.run(["ffmpeg", "-y", "-i", str(raw), "-codec:a", "libmp3lame", "-b:a", "64k", str(mp3)], capture_output=True, timeout=120)
        if not mp3.exists():
            raise Unavailable("озвучка не получилась (ffmpeg)")
        audio = mp3.read_bytes()
    _account("speech", used, started, purpose, chars=len(text))
    events.log("speech", text, voice=used, model=used, purpose=purpose, local=True, bytes=len(audio))
    return audio


def status():
    """For the screen: what is installed, whether the server runs and which models are awake."""
    loaded = {}
    try:
        loaded = {m.get("id"): (m.get("status") or {}).get("value") for m in _get("/v1/models", timeout=1).get("data", [])}
    except (OSError, ValueError):
        pass
    return {"installed": installed(), "server": bool(loaded), "models": loaded}
