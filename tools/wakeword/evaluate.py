import os, sys, subprocess
import numpy as np
from openwakeword.model import Model

# Tests the "Sena" detector on macOS synthesized speech (a different engine than Piper: a fair test on unseen voices)
MODEL = sys.argv[1]
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval")
os.makedirs(D, exist_ok=True)
m = Model(wakeword_models=[MODEL], inference_framework="onnx")

PHR = ["сколько времени", "включи музыку", "какая погода завтра", "напомни позвонить папе", "открой проект", "что у меня по планам",
       "прочитай последние сообщения", "поставь громкость на пятьдесят", "найди билеты в Стамбул", "ты меня слышишь"]
NAMES = ["Сена", "Эй, Сена", "Сенка", "Сеночка", "Сенюш", "Слушай, Сена", "Знаешь, Сена", "Сенааа", "Сенок", "Сена, стоп"]
COND = {"обычно": ([], None), "быстро": (["-r", "230"], None), "медленно": (["-r", "140"], None),
        "низкий": ([], "asetrate=22050*0.8,aresample=16000"), "высокий": ([], "asetrate=22050*1.2,aresample=16000"), "шум": ([], "noise")}
EN = ["Hey Sena, what time is it", "Sena, play some music", "Sena, remind me to call dad", "Listen Sena, find flights", "Sena, stop",
      "Sena, what are my plans", "Hey Sena", "Sena"]
NEG_RU = ["Сенат собрался вчера", "Сейчас пойдём гулять", "Цена какая у телефона", "Сено лежит в поле", "Он сел на поезд", "Знаешь, сегодня холодно",
          "Эй, Серёга, ты где", "Сенокос начался", "Лена, ты где", "Гена пришёл", "Сцена была огромная", "Селена поёт", "Давай сегодня в семь",
          "Стена белая", "Света звонила", "Таня, привет", "Тень от дома", "Семья собралась", "Сделай потише", "Весна пришла", "Привет, как дела",
          "Включи музыку погромче", "Какая сегодня погода", "Напомни позвонить маме", "Поставь будильник на семь", "Открой новый проект",
          "Сколько стоит билет", "Что у нас на ужин", "Ты меня слышишь вообще", "Прочитай сообщение от Ули"]
NEG_EN = ["Send me the report", "Senna was a great driver", "The scene was beautiful", "Say no to that", "Tina, come here", "See you tomorrow",
          "Set an alarm for seven", "Play some music please", "What is the weather today", "Remind me to call mom", "Hey, what's up", "Listen to me",
          "The cinema is closed", "Seven senators", "Dana is here", "Selena sings", "Sienna is a color", "Stop it now"]


def make(voice, text, rate, filt, out):
    subprocess.run(["say", "-v", voice, *rate, "-o", f"{D}/t.aiff", text], check=True)
    if filt == "noise":
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", f"{D}/t.aiff", "-f", "lavfi", "-i", "anoisesrc=c=pink:a=0.06",
                        "-filter_complex", "[0]aresample=16000[a];[1]aresample=16000[b];[a][b]amix=inputs=2:duration=first", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", out], check=True)
    else:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", f"{D}/t.aiff", *(["-af", filt] if filt else []), "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", out], check=True)


def score(path):
    m.reset()
    return max(p[list(p)[0]] for p in m.predict_clip(path))


pos, neg = [], []
for cname, (rate, filt) in COND.items():
    sc = []
    for i, p in enumerate(PHR):
        make("Milena", f"{NAMES[i]}, {p}", rate, filt, f"{D}/x.wav"); sc.append(score(f"{D}/x.wav"))
    pos += sc
    print(f"{cname:9} >=0.5: {sum(s >= 0.5 for s in sc)}/10  min={min(sc):.2f} med={np.median(sc):.2f}", flush=True)
sc = []
for p in EN:
    make("Samantha", p, [], None, f"{D}/x.wav")
    sc.append(score(f"{D}/x.wav"))
pos += sc
print(f"english   >=0.5: {sum(s >= 0.5 for s in sc)}/{len(sc)}  min={min(sc):.2f} med={np.median(sc):.2f}", flush=True)
for voice, lst in (("Milena", NEG_RU), ("Samantha", NEG_EN)):
    for p in lst:
        make(voice, p, [], None, f"{D}/x.wav"); s = score(f"{D}/x.wav"); neg.append((s, p))
worst = sorted(neg, reverse=True)[:5]
print("негативы: max=%.2f, >=0.5: %d/%d" % (worst[0][0], sum(s >= 0.5 for s, _ in neg), len(neg)), [f"{p} {s:.2f}" for s, p in worst])
for t in (0.5, 0.8, 0.9, 0.95, 0.98):
    print(f"порог {t}: ловит {sum(s >= t for s in pos)}/{len(pos)}, ложных {sum(s >= t for s, _ in neg)}/{len(neg)}")
