"""Voice and hearing (no Claude here: OpenAI, Gemini and the home models): python bench_voice.py <out.json> <folder for audio>.
Hearing: the same Russian phrases spoken by two home voices (Irina, Milena) and Irina under noise, recognized by each
system; the metric is word errors (WER) after plain normalization. Voice: Sena's typical replies spoken by each voice —
files to listen to, the time it took, the price of a minute, and intelligibility (words the accurate home whisper gets wrong).
Gemini is called the way Sena calls it (google/media.py, the model swapped in config): its price lands in the run's cost journal."""
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

import harness

out, folder = sys.argv[1], Path(sys.argv[2])
folder.mkdir(parents=True, exist_ok=True)
harness.setup("", "fast")

import numpy as np  # noqa: E402
from sena import config  # noqa: E402
from sena.core import llm, local  # noqa: E402
from sena.google import media  # noqa: E402
PHRASES = [
    "напомни завтра утром позвонить Игорю насчёт договора",
    "открой ютуб и включи что-нибудь спокойное для работы",
    "сколько я потратил на такси за последнюю неделю",
    "напиши маме что я задержусь на час и буду к ужину",
    "найди в почте письмо от бухгалтера про акты за октябрь",
    "поставь будильник на семь тридцать и выключи свет в спальне",
    "какая погода в Дананге на выходных будет дождь или нет",
    "запиши идею приложение для учёта сёрф сессий",
    "проверь работает ли сайт портфолио и пришли отчёт",
    "что нового в мире искусственного интеллекта за сегодня",
    "отправь Ольге презентацию по проекту Hire-flow",
    "сделай громкость на маке потише и поставь паузу",
]
SAME = {"ютуб": "youtube", "youtube": "youtube", "ютубе": "youtube", "hireflow": "hireflow", "хайрфлоу": "hireflow", "хайр": "hireflow",
        "7": "семь", "30": "тридцать", "семь тридцать": "семь тридцать", "сёрф": "серф", "серфсессий": "серф сессий"}
REPLIES = ["Готово: напомню завтра в девять утра позвонить Игорю.",
           "Сегодня в Дананге плюс двадцать девять, к вечеру возможен короткий дождь. Зонт лучше взять.",
           "Нашла письмо от Ольги: она просит прислать акты за октябрь до пятницы. Подготовить ответ?"]


def words(text):
    text = text.lower().replace("ё", "е").replace("hire-flow", "hireflow").replace("хайр-флоу", "hireflow")
    text = re.sub(r"(\d+):(\d+)", r"\1 \2", text)
    tokens = re.findall(r"[a-zа-я0-9]+", text)
    return [SAME.get(t, t) for t in tokens]


def wer(reference, hypothesis):
    r, h = words(reference), words(hypothesis)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(1, len(r))


def to_wav16(path):
    target = str(path) + ".16k.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-ar", "16000", "-ac", "1", target], check=True)
    return target


def seconds_of(path):
    done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(done.stdout.strip() or 0)


def noisy(wav_path, target, snr_db=8):
    with wave.open(wav_path) as w:
        frames, rate = w.readframes(w.getnframes()), w.getframerate()
    x = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
    rng = np.random.default_rng(5)
    noise = rng.normal(0, 1, len(x)).astype(np.float32)
    noise = np.convolve(noise, np.ones(8) / 8, mode="same")   # a duller hiss, like a room, not pure white
    scale = np.sqrt((x ** 2).mean() / (noise ** 2).mean() / (10 ** (snr_db / 10)))
    y = np.clip(x + noise * scale, -32768, 32767).astype(np.int16)
    with wave.open(target, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate), w.writeframes(y.tobytes())
    return target


# ---------- the clips: two home voices and one under noise ----------
clips = []
for n, phrase in enumerate(PHRASES):
    irina = folder / f"in-irina-{n}.mp3"
    irina.write_bytes(local.speak(phrase, purpose="замер: голос для слуха"))
    milena_aiff = folder / f"in-milena-{n}.aiff"
    subprocess.run(["say", "-v", "Milena", "-o", str(milena_aiff), phrase], check=True)
    clips.append({"voice": "Ирина", "text": phrase, "path": to_wav16(irina)})
    clips.append({"voice": "Милена", "text": phrase, "path": to_wav16(milena_aiff)})
    clips.append({"voice": "Ирина с шумом", "text": phrase, "path": noisy(to_wav16(irina), str(folder / f"in-noisy-{n}.wav"))})


def openai_hear(model):
    def hear(path):
        config.MODEL_TRANSCRIBE = model
        return llm.transcribe(Path(path).read_bytes(), filename="clip.wav", purpose="замер: слух")
    return hear


def gemini_hear(path):
    config.MODEL_TRANSCRIBE = "gemini-3.5-transcribe"
    return media.transcribe(Path(path).read_bytes(), filename="clip.wav", purpose="замер: слух")


SYSTEMS = {"OpenAI gpt-transcribe": openai_hear("gpt-transcribe"), "OpenAI gpt-4o-transcribe": openai_hear("gpt-4o-transcribe"),
           "OpenAI gpt-4o-mini-transcribe": openai_hear("gpt-4o-mini-transcribe"), "Gemini 3.5 Transcribe": gemini_hear,
           "Дома: whisper large-v3-turbo": lambda p: local.transcribe(p, best=True, purpose="замер: слух"),
           "Дома: whisper small": lambda p: local.transcribe(p, best=False, purpose="замер: слух")}
hearing = []
for name, hear in SYSTEMS.items():
    for clip in clips:
        started = time.time()
        try:
            text = hear(clip["path"])
            error = None
        except Exception as e:
            text, error = "", f"{type(e).__name__}: {str(e)[:200]}"
        hearing.append({"system": name, "voice": clip["voice"], "text": clip["text"], "heard": text, "wer": round(wer(clip["text"], text), 3),
                        "seconds": round(time.time() - started, 2), "audio_seconds": round(seconds_of(clip["path"]), 2), "error": error})
    rows = [h for h in hearing if h["system"] == name]
    print(f"{name}: WER {sum(h['wer'] for h in rows) / len(rows):.3f}, ошибок вызова {sum(1 for h in rows if h['error'])}", flush=True)

# ---------- the voices ----------
VOICES = {"OpenAI gpt-4o-mini-tts · marin": ("openai", "marin"), "OpenAI gpt-4o-mini-tts · coral": ("openai", "coral"),
          "Gemini 3.8 Flash TTS · Kore": ("gemini-3.8-flash-tts", "Kore"), "Gemini 3.8 Flash TTS · Zephyr": ("gemini-3.8-flash-tts", "Zephyr"),
          "Gemini 3.8 Flash-Lite TTS · Kore": ("gemini-3.8-flash-lite-tts", "Kore"),
          "Дома: Ирина (Piper)": ("home", "irina"), "Дома: Милена (macOS)": ("say", "Milena")}
speaking = []
for name, (engine, voice) in VOICES.items():
    for n, reply in enumerate(REPLIES):
        started, error, path = time.time(), None, None
        try:
            if engine == "openai":
                path = folder / f"tts-{voice}-{n}.mp3"
                path.write_bytes(llm.speech(reply, voice, purpose="замер: голос"))
            elif engine == "home":
                path = folder / f"tts-irina-{n}.mp3"
                path.write_bytes(local.speak(reply, purpose="замер: голос"))
            elif engine == "say":
                path = folder / f"tts-milena-{n}.aiff"
                subprocess.run(["say", "-v", voice, "-o", str(path), reply], check=True)
            else:   # Sena's own voice path, in her own manner (config.VOICE_STYLE)
                config.MODEL_TTS = engine
                path = folder / f"tts-{engine}-{voice}-{n}.mp3"
                path.write_bytes(media.speech(reply, voice, purpose="замер: голос"))
        except Exception as e:
            error = f"{type(e).__name__}: {str(e)[:200]}"
        took = time.time() - started
        heard = local.transcribe(to_wav16(path), best=True, purpose="замер: разборчивость") if path and path.exists() else ""
        speaking.append({"voice": name, "text": reply, "file": str(path) if path else None, "seconds": round(took, 2),
                         "audio_seconds": round(seconds_of(path), 2) if path and path.exists() else 0, "heard": heard,
                         "wer": round(wer(reply, heard), 3) if heard else None, "error": error})
    rows = [s for s in speaking if s["voice"] == name]
    print(f"{name}: {sum(s['seconds'] for s in rows) / len(rows):.1f} с на фразу, ошибок {sum(1 for s in rows if s['error'])}", flush=True)

harness.save(out, {"bench": "voice", "hearing": hearing, "speaking": speaking, "rows": harness.rows()})
