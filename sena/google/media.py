"""Sena's voice, the backup hearing, images and video via the Gemini API (key from Google AI Studio, GEMINI_API_KEY in .env).
Requests go directly, no SDK. Voice: Gemini TTS with the voice the owner picked (config.VOICE) — the same everywhere she speaks;
the live voice: Gemini Live (Live below), a conversation by sound both ways in the same voice, answering in about a second;
hearing: Gemini Transcribe, only when the home whisper fails (core/roles.py decides, these are handed to it).
Images: the Nano Banana family (gemini-nano-banana-2.1 by default, lite is cheaper, pro is the strongest).
Video: Gemini Omni Flash. Everything is paid: Google has no free tier for images or video.
A daily limit is supported (LIMITS), currently turned off; how much was made today is counted in core/limits.py."""
import base64
import io
import json
import os
import re
import ssl
import subprocess
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import certifi
import requests
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException
from websockets.sync.client import connect

from sena import config
from sena.core import attachments, cancel, costs, events, limits, safety, store
from sena.core.tools import string, strings, tool

API = "https://generativelanguage.googleapis.com/v1beta"
IMAGE_MODELS = {"standard": "gemini-nano-banana-2.1", "lite": "gemini-3.1-flash-lite-image", "pro": "gemini-3-pro-image"}
VIDEO_MODEL = "gemini-omni-1.1-flash"
ASPECTS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
SIZES = ("1K", "2K", "4K")
LIMITS = {"image": None, "video": None}   # per day; None — no limit (owner's decision), the expense count is still kept
VIDEO_WAIT = 600                      # seconds to wait for a finished video
POLL_MISSES = 3                       # failed asks in a row about a rendering video before it counts as lost
REFERENCE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".heif"}   # files that can be a sample picture


class MediaError(Exception):
    pass


def _key():
    if not config.GEMINI_API_KEY:
        raise MediaError("Ключ Gemini не задан. Создай его на aistudio.google.com/apikey и впиши в .env строкой GEMINI_API_KEY=..., затем перезапусти Сену.")
    return config.GEMINI_API_KEY


def _check_limit(kind):
    try:
        limits.check(kind, LIMITS[kind], "картинок" if kind == "image" else "видео")
    except limits.LimitReached as e:
        raise MediaError(str(e))


def _explain(response):
    # a clear explanation of the Google error
    try:
        message = response.json()["error"]["message"]
    except Exception:
        message = response.text[:300]
    low = message.lower()
    if response.status_code in (402, 429) or "billing" in low or "quota" in low or "free tier" in low or "paid" in low:
        return ("Google не принял запрос: нужен платный тариф Gemini API "
                f"(AI Studio → Billing) или кончился лимит. Ответ Google: {message[:200]}")
    if response.status_code in (400, 403) and "api key" in low:
        return f"Ключ Gemini не подошёл: {message[:200]}"
    return f"Google {response.status_code}: {message[:300]}"


def _request(method, url, **kwargs):
    headers = {"x-goog-api-key": _key(), **kwargs.pop("headers", {})}
    try:
        response = requests.request(method, url, headers=headers, timeout=kwargs.pop("timeout", 600), **kwargs)
    except requests.RequestException as e:
        if method != "GET":   # a request that makes something: Google may have made it and charged for it — record "no price"
            costs.record("gemini", "request", "", {}, purpose="сбой связи с Google", unknown=True)   # (a GET only reads: free)
        raise MediaError(f"Нет связи с Google ({type(e).__name__}); если картинка или видео уже делались, деньги могли списаться")
    events.log("fetch", url, source="Gemini", method=method, status=response.status_code, failed=response.status_code >= 400 or None)
    if response.status_code >= 400:
        raise MediaError(_explain(response))
    return response


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value)


def _output(result, kind):
    # the model's output of the requested kind (image or video): the REST response format is parsed by meaning, not by position in the JSON
    for node in _walk(result):
        if node.get("type") == kind and (node.get("data") or node.get("uri")):
            return node
    return None


def _filename(prompt, ext):
    slug = re.sub(r"[^\w]+", "-", prompt.lower()).strip("-")[:40] or "media"
    return store.stamped(config.GENERATED_DIR, f"{slug}.{ext}")


def _seconds(path):
    # file duration: needed if Google didn't send video tokens and the price is calculated per second
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return float(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


# ---------- voice and hearing ----------
SPEECH_PART = 160   # characters: a longer reply is spoken in parts at once — ready in the time of its longest part, not of the whole
SPEECH_WORKERS = 3


def _speech_parts(text):
    """The reply split at sentence ends into at most SPEECH_WORKERS parts of similar length (a short one stays whole)."""
    text = " ".join(text.split())   # already as said aloud (roles.speak)
    if len(text) <= SPEECH_PART:
        return [text]
    sentences = re.findall(r"[^.!?…]+[.!?…]*\s*", text)
    if len(sentences) < 2:
        return [text]
    count = min(SPEECH_WORKERS, len(sentences))
    size = len(text) / count
    parts, current = [], ""
    for sentence in sentences:
        if current and len(current) >= size and len(parts) < count - 1:
            parts.append(current.strip())
            current = ""
        current += sentence
    return parts + [current.strip()] if current.strip() else parts


def _pcm(audio):
    # Gemini answers with a WAV (24 kHz, mono, 16-bit): its frames, so the parts can be joined
    if audio[:4] != b"RIFF":
        return audio
    with wave.open(io.BytesIO(audio)) as w:
        return w.readframes(w.getnframes())


def _speak_part(text, voice, purpose):
    body = {"model": config.MODEL_TTS, "response_format": {"type": "audio"}, "generation_config": {"speech_config": [{"voice": voice}]},
            "input": [{"type": "user_input", "content": [{"type": "text", "text": text,
                                                          "annotations": [{"type": "speech_metadata", "style": config.VOICE_STYLE}]}]}]}
    for attempt in (1, 2):
        try:
            result = _request("POST", f"{API}/interactions", json=body, timeout=60).json()
            break
        except MediaError as e:
            if attempt == 2 or "лимит" not in str(e):   # a moment over the per-minute limit: one more try
                raise
            time.sleep(2)
    costs.gemini_audio(config.MODEL_TTS, result, "speech", purpose)
    node = _output(result, "audio")
    if not node or not node.get("data"):
        raise MediaError("Gemini не вернул звук")
    return _pcm(base64.b64decode(node["data"]))


def speech(text, voice=None, purpose="озвучка"):
    """Text -> mp3 in Sena's voice (config.VOICE, the manner config.VOICE_STYLE)."""
    limits.budget_check()
    voice = voice or config.VOICE
    parts = _speech_parts(text)
    with ThreadPoolExecutor(max_workers=len(parts)) as pool:
        pcm = b"".join(pool.map(lambda part: _speak_part(part, voice, purpose), parts))
    done = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", "pipe:0", "-codec:a", "libmp3lame",
                           "-b:a", "64k", "-f", "mp3", "pipe:1"], input=pcm, capture_output=True, timeout=120)
    if done.returncode or not done.stdout:
        raise MediaError(f"озвучка не собралась (ffmpeg): {done.stderr.decode(errors='replace')[-200:]}")
    events.log("speech", text, voice=voice, purpose=purpose, model=config.MODEL_TTS, bytes=len(done.stdout), parts=len(parts))
    return done.stdout


def transcribe(audio, filename="voice.ogg", purpose=None):
    """The owner's voice (any format: Telegram's OGG, the phone's m4a) -> text in the cloud, when the home hearing failed."""
    limits.budget_check()
    done = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"],
                          input=audio, capture_output=True, timeout=120)
    if done.returncode or not done.stdout:
        raise MediaError(f"запись не читается ({filename})")
    body = {"model": config.MODEL_TRANSCRIBE, "input": [
        {"type": "text", "text": "Сделай точную расшифровку речи без пояснений. Речь русская, бывают английские слова и названия."},
        {"type": "audio", "data": base64.b64encode(done.stdout).decode(), "mime_type": "audio/wav"}]}
    result = _request("POST", f"{API}/interactions", json=body, timeout=120).json()
    costs.gemini_audio(config.MODEL_TRANSCRIBE, result, "transcribe", purpose or "голос в текст")
    text = " ".join(node["text"] for step in result.get("steps", []) if step.get("type") != "user_input"
                    for node in _walk(step) if node.get("type") == "text" and isinstance(node.get("text"), str)).strip()
    events.log("transcribe", text, purpose=purpose, model=config.MODEL_TRANSCRIBE, bytes=len(audio))
    return text


# ---------- the live voice ----------
LIVE_URL = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
LIVE_QUIET_MS = 500   # this much quiet after a phrase and the model answers (Live's own speech detector)


def _declaration(spec, waiting):
    # our tool description (core/tools.tool) in Gemini's words; a tool that takes time doesn't stop the conversation (NON_BLOCKING)
    out = {"name": spec["name"], "description": spec["description"], "parameters": spec["parameters"]}
    return {**out, "behavior": "NON_BLOCKING"} if spec["name"] in waiting else out


class Live:
    """One live conversation with Gemini Live (WebSocket). open() connects and waits until the model is ready (about a second);
    send() passes the microphone (16 kHz 16-bit mono PCM), receive() returns what came back as (kind, value): "audio" — her voice,
    24 kHz PCM; "heard" and "said" — pieces of what was heard and said; "turn" — her turn is over; "interrupted" — she was cut off;
    "call" — a tool call {id, name, args}; "cancel" — calls no longer needed; "end" — Google closes the conversation.
    Every reply's tokens go into the cost journal the moment Google reports them; usd is the conversation's price so far.
    waiting — the tools whose answer takes time: the conversation goes on while they work, the answer is spoken when it comes."""

    def __init__(self, instructions, tools, waiting=(), purpose="живой голос"):
        self.instructions, self.tools, self.waiting, self.purpose = instructions, tools, set(waiting), purpose
        self.usd = 0.0
        self._ws = None
        self._lock = threading.Lock()   # the microphone and the tool answers are sent from different threads

    def open(self, timeout=10):
        limits.budget_check()
        setup = {"model": f"models/{config.MODEL_LIVE}",
                 "generationConfig": {"responseModalities": ["AUDIO"],
                                      "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": config.VOICE}}}},
                 "systemInstruction": {"parts": [{"text": self.instructions}]},
                 "tools": [{"functionDeclarations": [_declaration(t, self.waiting) for t in self.tools]}],
                 "realtimeInputConfig": {"automaticActivityDetection": {"silenceDurationMs": LIVE_QUIET_MS}},
                 "inputAudioTranscription": {}, "outputAudioTranscription": {}}
        secure = ssl.create_default_context(cafile=certifi.where()) if LIVE_URL.startswith("wss:") else None   # Python on Mac doesn't know its own CAs
        try:
            self._ws = connect(LIVE_URL, additional_headers={"x-goog-api-key": _key()}, ssl=secure, open_timeout=timeout,
                               close_timeout=2, max_size=None, compression=None)
            self._ws.send(json.dumps({"setup": setup}))
            ready = json.loads(self._ws.recv(timeout=timeout))
            if "setupComplete" not in ready:
                raise ValueError(f"Google ответил {str(ready)[:200]}")
        except (OSError, TimeoutError, WebSocketException, ValueError) as e:
            self.close()
            events.log("fetch", LIVE_URL, source="Gemini", method="WS", model=config.MODEL_LIVE, failed=True, reason=_live_reason(e))
            raise MediaError(f"Живой голос не открылся: {_live_reason(e)}")
        events.log("fetch", LIVE_URL, source="Gemini", method="WS", model=config.MODEL_LIVE)
        return self

    def _send(self, message):
        with self._lock:
            try:
                self._ws.send(json.dumps(message))
            except (ConnectionClosed, OSError) as e:
                raise MediaError(f"Живой голос оборвался: {_live_reason(e)}")

    def send(self, pcm):
        self._send({"realtimeInput": {"audio": {"mimeType": "audio/pcm;rate=16000", "data": base64.b64encode(pcm).decode()}}})

    def note(self, text):
        """A quiet note into the conversation (the time, a fact): she takes it in and doesn't answer it."""
        self._send({"clientContent": {"turns": [{"role": "user", "parts": [{"text": text}]}], "turnComplete": False}})

    def tell(self, text):
        """Something for her to say now (an answer of the brain that came late): she answers it at once."""
        self._send({"clientContent": {"turns": [{"role": "user", "parts": [{"text": text}]}], "turnComplete": True}})

    def pause(self):
        # the microphone went quiet for a while: Live finishes hearing the phrase now instead of waiting for more sound
        self._send({"realtimeInput": {"audioStreamEnd": True}})

    def answer(self, call_id, name, result, quiet=False):
        """A tool's answer. She speaks it at once (INTERRUPT: the first sound in 0.6 s; WHEN_IDLE waited for an idle that in a
        live conversation came 8–23 s late, measured 02.11.2026); quiet — take it without a word (SILENT)."""
        self._send({"toolResponse": {"functionResponses": [{"id": call_id, "name": name, "response": {"result": result},
                                                            "scheduling": "SILENT" if quiet else "INTERRUPT"}]}})

    def receive(self, timeout=0.5):
        """What came back from one message of Google's: a list of (kind, value); empty if nothing arrived within timeout."""
        try:
            raw = self._ws.recv(timeout=timeout)
        except TimeoutError:
            return []
        except (ConnectionClosed, OSError) as e:
            return [("end", _live_reason(e))]
        try:
            message = json.loads(raw)
        except ValueError:
            return []
        out = []
        usage = message.get("usageMetadata")
        if usage:
            self.usd += costs.gemini_live(config.MODEL_LIVE, usage, self.purpose) or 0.0
        content = message.get("serverContent") or {}
        if content.get("interrupted"):
            out.append(("interrupted", True))
        for part in (content.get("modelTurn") or {}).get("parts", []):
            data = (part.get("inlineData") or {}).get("data")
            if data:
                out.append(("audio", base64.b64decode(data)))
        for kind, key in (("heard", "inputTranscription"), ("said", "outputTranscription")):
            text = (content.get(key) or {}).get("text")
            if text:
                out.append((kind, text))
        if content.get("turnComplete"):
            out.append(("turn", True))
        for call in (message.get("toolCall") or {}).get("functionCalls", []):
            out.append(("call", {"id": call.get("id"), "name": call.get("name"), "args": call.get("args") or {}}))
        if message.get("toolCallCancellation"):
            out.append(("cancel", message["toolCallCancellation"].get("ids", [])))
        if "goAway" in message:
            out.append(("end", "Google закрывает разговор"))
        return out

    def close(self):
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass   # already closed from Google's side


def _live_reason(e):
    # why the live voice failed, in words: Google puts the reason in the close frame (a bad key, no money, a wrong model)
    if isinstance(e, InvalidStatus):
        return f"Google {e.response.status_code}"
    if isinstance(e, ConnectionClosed) and (e.rcvd and e.rcvd.reason):
        return f"Google: {e.rcvd.reason[:200]}"
    if isinstance(e, TimeoutError):
        return "Google не ответил вовремя"
    if isinstance(e, ConnectionClosed):
        return "связь закрылась"
    if isinstance(e, ValueError):
        return str(e)[:200]
    return f"нет связи с Google ({type(e).__name__})"


def _reference(path):
    """A picture the model named as a sample: an image only, and one that may leave the Mac — not Sena's data, not a key
    (safety.forbidden_path, the rule of every exit): otherwise any file could be sent to Google as "image/jpeg"."""
    why = safety.forbidden_path(path)
    if why:
        raise MediaError(f"Этот файл в Google не отправляю: {why}")
    real = Path(os.path.realpath(os.path.expanduser(str(path))))
    if real.suffix.lower() not in REFERENCE_TYPES or not real.is_file():
        raise MediaError(f"Образец — файл картинки ({', '.join(sorted(REFERENCE_TYPES))}): «{real.name}» не подходит или его нет")
    data, mime = attachments.image_data(real)
    return {"type": "image", "mime_type": mime, "data": base64.b64encode(data).decode()}


def generate_image(prompt, aspect_ratio="1:1", size="1K", tier="standard", reference_images=None):
    """Draw an image or rework the ones sent (reference_images — file paths). Returns the path to the PNG."""
    if aspect_ratio not in ASPECTS:
        raise MediaError(f"aspect_ratio — одно из: {', '.join(ASPECTS)}")
    if size not in SIZES:
        raise MediaError(f"size — одно из: {', '.join(SIZES)}")
    if tier not in IMAGE_MODELS:
        raise MediaError(f"tier — одно из: {', '.join(IMAGE_MODELS)}")
    references = [_reference(path) for path in (reference_images or [])[:10]]   # checked before the limit and the paid call
    _check_limit("image")
    parts = [{"type": "text", "text": prompt}, *references]
    body = {"model": IMAGE_MODELS[tier], "input": parts,
            "response_format": {"type": "image", "aspect_ratio": aspect_ratio, "image_size": size}}
    result = _request("POST", f"{API}/interactions", json=body).json()
    costs.gemini(IMAGE_MODELS[tier], result, "image", purpose="картинка")   # charged even if the model refused to draw
    image = _output(result, "image")
    if not image or not image.get("data"):
        text = " ".join(n.get("text", "") for n in _walk(result) if n.get("type") == "text")[:300]
        raise MediaError("Модель не вернула картинку" + (f": {text}" if text else " (возможно, запрос отклонён правилами безопасности)"))
    path = _filename(prompt, "png")
    path.write_bytes(base64.b64decode(image["data"]))
    limits.spend("image")
    events.log("media", prompt, model=IMAGE_MODELS[tier], media="image", file=str(path), references=len(reference_images or []) or None)
    attachments.register(path, prompt, "gemini")   # the prompt is the picture's context: that's how it is found later
    return path


def generate_video(prompt, aspect_ratio="16:9", resolution="720p", reference_image=None):
    """Shoot a short video (a few seconds) from text or from an image. Returns the path to the mp4. Paid: about 10 cents per second."""
    if aspect_ratio not in ("16:9", "9:16"):
        raise MediaError("aspect_ratio для видео — 16:9 или 9:16")
    if resolution not in ("720p", "1080p"):
        raise MediaError("resolution — 720p или 1080p")
    parts = ([_reference(reference_image)] if reference_image else []) + [{"type": "text", "text": prompt}]
    _check_limit("video")
    body = {"model": VIDEO_MODEL, "input": parts,
            "response_format": {"type": "video", "aspect_ratio": aspect_ratio, "resolution": resolution, "delivery": "uri"}}
    result = _request("POST", f"{API}/interactions", json=body).json()
    path = None
    try:
        video = _output(result, "video")
        if not video:
            raise MediaError("Модель не вернула видео (возможно, запрос отклонён правилами безопасности)")
        path = _filename(prompt, "mp4")
        if video.get("data"):
            path.write_bytes(base64.b64decode(video["data"]))
        else:
            match = re.search(r"files/([\w-]+)", video["uri"])
            if not match:
                raise MediaError(f"Не понял адрес видео: {video['uri'][:120]}")
            file_id, deadline, misses = match.group(1), time.time() + VIDEO_WAIT, 0
            while True:   # the video is being rendered on Google's side
                cancel.check_stop()
                try:
                    state_name = _request("GET", f"{API}/files/{file_id}", timeout=60).json().get("state", "")
                    misses = 0
                except (MediaError, ValueError):   # one blip while it renders doesn't lose a paid video: asked again
                    misses += 1
                    if misses > POLL_MISSES:
                        raise
                    state_name = ""
                if state_name == "ACTIVE":
                    break
                if state_name == "FAILED":
                    raise MediaError("Google не смог сделать видео (запрос отклонён или сбой)")
                if time.time() > deadline:
                    raise MediaError(f"Видео не готово за {VIDEO_WAIT // 60} минут")
                time.sleep(5)
            path.write_bytes(_request("GET", f"{API}/files/{file_id}:download", params={"alt": "media"}).content)
    finally:   # the charge is made even if the video never reached us
        costs.gemini(VIDEO_MODEL, result, "video", purpose="видео",
                     video_seconds=_seconds(path) if path and path.exists() else None)
    limits.spend("video")
    events.log("media", prompt, model=VIDEO_MODEL, media="video", file=str(path))
    attachments.register(path, prompt, "gemini")
    return path


def available_models(pattern=r"image|imagen|veo|omni|banana|tts|transcribe"):
    """Which models the key can see — images, video, voice and hearing by default (checks that the key works and the names are current)."""
    names = []
    token = None
    while True:
        page = _request("GET", f"{API}/models", params={"pageSize": 200, **({"pageToken": token} if token else {})}, timeout=60).json()
        names += [m["name"].split("/")[-1] for m in page.get("models", [])]
        token = page.get("nextPageToken")
        if not token:
            break
    return sorted(n for n in names if re.search(pattern, n))


TOOLS = [
    tool("make_image",
         "Нарисовать картинку по описанию или переделать присланные фото (reference_images — пути из записок «сохранён:»). Платно, около 2–5 центов за картинку. "
         "Готовая картинка сразу уйдёт Теме в Телеграм. Делай только когда Тема просил картинку, один раз. Описание лучше на английском.",
         {"prompt": string("Что нарисовать или что изменить; подробно: сюжет, стиль, детали"),
          "aspect_ratio": {**string("Пропорции, по умолчанию 1:1"), "enum": list(ASPECTS)},
          "size": {**string("Размер: 1K обычный, 2K, 4K дороже"), "enum": list(SIZES)},
          "tier": {**string("standard — по умолчанию; lite — дешевле и быстрее; pro — самая сильная, около 13 центов"), "enum": list(IMAGE_MODELS)},
          "reference_images": strings("Пути к картинкам для правки или как образец (до 10)")}, ["prompt"]),
    tool("make_video",
         "Снять короткое видео (несколько секунд) по описанию или оживить картинку. Платно: около 10 центов за секунду, занимает минуты. "
         "Только по прямой просьбе Темы. Готовое видео сразу уйдёт ему в Телеграм.",
         {"prompt": string("Что происходит в кадре, движение камеры, стиль; подробно"),
          "aspect_ratio": {**string("16:9 горизонтальное, 9:16 вертикальное"), "enum": ["16:9", "9:16"]},
          "resolution": {**string("720p по умолчанию; 1080p дороже"), "enum": ["720p", "1080p"]},
          "reference_image": string("Путь к картинке, которую оживить (необязательно)")}, ["prompt"]),
]
