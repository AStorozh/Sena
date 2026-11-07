"""Background hearing: "is the name Sena audible". Everything is local and free: the speech itself is recognized by core/local.py
(whisper.cpp with Silero VAD), the voice fingerprint is there too; the name is caught by its text and by openWakeWord on the raw sound
of the live stream, every 80 ms (Detector, for the live voice).
The detector looks at the sound in steps of 80 ms, and how sure it is depends on where the word falls on that step: the same
"Sena, can you hear me" scored 0.50 or 1.00 by a shift of 20 ms. So PHASES detectors listen at once, each shifted by 20 ms,
and the surest one counts (measured 25.10.2026: the name above 0.95 at any shift instead of 3 shifts in 8)."""
import re

import numpy as np

from sena import config

WAKE_SCORE = 0.95   # name detector confidence: at 0.95 testing showed zero false positives, catching ~60% on its own
PHASES = 4          # detectors shifted by a quarter of the 80 ms step: the name is caught however it falls on the step
FRAME = 1280        # samples the detector takes at a time (80 ms of 16 kHz)

# Wake name: Sena. Caught at the start of a phrase, with or without a lead-in: "hey Sena", "listen, Sena", "you know, Sena",
# "Senka", "Senyush", "Senochka", "Senok", "hey Sena". Whisper spells the name differently (tena, sinochka, sina, cina) — we account for that.
NAME = r"(?:[ст][еиэ]н(?:а+|ая|ка|юш|юша|очка|ечка|ок|уш|ушка)?|[sc]e?[ie]na+)"
WAKE = re.compile(r"^[\W\d]*(?:(?:эй|слушай|знаешь|хей|hey|hi|ok|okay|listen|yo)[\W]*)?" + NAME + r"(?=\W|$)[\W]*", re.I | re.U)

LEAD_INS = {"эй", "слушай", "знаешь", "хей", "hey", "hi", "ok", "okay", "listen", "yo"}
LATIN = str.maketrans({"s": "с", "c": "с", "e": "е", "i": "и", "y": "и", "n": "н", "a": "а", "t": "т", "m": "м", "k": "к", "o": "о"})
FORMS = {"сена": 1, "сенка": 2, "сеночка": 2, "сенечка": 2}   # the name's forms and how many letters may differ in each


def _distance(a, b):
    # how many letters to change, add or drop to turn a into b
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (x != y))
    return row[-1]


def near_name(text):
    """Whether the phrase starts with a word close to the name: whisper and the owner's own voice turn "Sena" into "Smena",
    "Siena", "Sina" — a strict pattern turned such calls away. Used where the detector has already heard something like it."""
    words = re.findall(r"[a-zа-яё]+", text.lower())
    if words and words[0] in LEAD_INS:
        words = words[1:]
    if not words:
        return False
    word = words[0].translate(LATIN)
    return any(_distance(word, form) <= most for form, most in FORMS.items())


def _model():
    from openwakeword.model import Model
    return Model(wakeword_models=[str(config.WAKE_MODEL)], inference_framework="onnx")


class Detector:
    """The name detector on a live stream: one per microphone, since it keeps the sound's state from frame to frame.
    score(frame) takes 80 ms of 16 kHz 16-bit PCM and says how sure it is, 0..1, that the name has just been said:
    the surest of PHASES detectors, detector k hearing the stream k quarters of a step later.
    No model file — it hears nothing (0), and the name is caught only by whisper's text."""

    def __init__(self):
        self.models = [_model() for _ in range(PHASES)] if config.WAKE_MODEL.exists() else []
        self.tail = bytes(FRAME * 2)   # the previous frame: the shifted detectors take their step partly from it

    def score(self, frame):
        if not self.models:
            return 0.0
        stream, size = self.tail + frame, len(frame)
        self.tail = frame
        best = 0.0
        for k, model in enumerate(self.models):
            shift = k * size // PHASES // 2 * 2   # bytes, on a sample boundary
            chunk = stream[len(stream) - size - shift:len(stream) - shift]
            best = max(best, float(model.predict(np.frombuffer(chunk, dtype=np.int16))["sena"]))
        return best
