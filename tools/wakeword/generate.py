import os, sys, random, logging
W = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(W, "piper-sample-generator"))
logging.disable(logging.DEBUG)
from piper_sample_generator.__main__ import generate_samples, generate_samples_onnx
RU = [os.path.join(W, "voices", f"ru_RU-{v}-medium.onnx") for v in ("irina", "denis", "dmitri", "ruslan")]
EN = os.path.join(W, "voices", "en_US-libritts_r-medium.pt")
POS_RU = ["Сена", "Сена.", "Сена!", "Сена?", "Сенка", "Сенка.", "Сеночка", "Сенюш", "Сенок", "Эй, Сена", "Эй Сена!", "Слушай, Сена",
          "Знаешь, Сена", "Сена, привет", "Сена, стоп", "Сенка, слушай", "Сеночка, привет", "Сенааа"]
POS_EN = ["Sena", "Sena.", "Senna", "Sehna", "Hey Sena", "Hey, Senna", "Listen, Sena", "Sena, stop", "Hey Sehna"]
NEG_RU = ["сенат", "сенатор", "сено", "сенокос", "сейчас", "цена", "сцена", "Селена", "Лена", "Гена", "Серёга", "стена", "Света", "Таня",
          "тень", "Тина", "сетка", "сенсация", "сегодня", "семья", "семь", "сделай", "сила", "синий", "сына", "весна", "Ксения", "Есения",
          "сеанс", "сено лежит", "цена какая", "сенат собрался", "сейчас пойдём", "он сел", "осень", "сам", "сенсор", "центр", "сера", "Сева",
          "Сеня", "Зина", "Дина", "Нина", "Ника", "тема", "Тёма", "эй", "слушай", "знаешь", "эй, Лена", "слушай, Тёма", "привет", "стоп"]
NEG_EN = ["scene", "send", "senior", "cinema", "Tina", "Dana", "Lena", "sauna", "sun", "say no", "seen", "sand", "center", "sending", "sensor",
          "sentence", "seven", "sinner", "Siena", "Selena", "Sienna", "Jenna", "Gina", "Nina", "hey", "listen", "hey Lena", "stop", "okay"]
def gen(texts, model, out, n, batch=8):
    os.makedirs(out, exist_ok=True)
    random.seed(len(out)); texts = [random.choice(texts) for _ in range(n)]
    kw = dict(text=texts, output_dir=out, max_samples=n, length_scales=[0.7, 0.85, 1.0, 1.15, 1.3], noise_scales=[0.5, 0.667, 0.9], noise_scale_ws=[0.6, 0.8, 1.0])
    if isinstance(model, list):   # Russian Piper voices (.onnx): the generator alternates the voices itself
        generate_samples_onnx(model=model, **kw)
    else:                         # the English generator (.pt) with hundreds of speakers
        generate_samples(model=model, batch_size=batch, **kw)
    print("готово:", out, len(os.listdir(out)), flush=True)
C = os.path.join(W, "clips")
gen(POS_RU, RU, f"{C}/pos_train/ru", 1800); gen(POS_RU, RU, f"{C}/pos_test/ru", 240)
gen(NEG_RU, RU, f"{C}/neg_train/ru", 1800); gen(NEG_RU, RU, f"{C}/neg_test/ru", 240)
gen(POS_EN, EN, f"{C}/pos_train/en", 1200, batch=16); gen(POS_EN, EN, f"{C}/pos_test/en", 200, batch=16)
gen(NEG_EN, EN, f"{C}/neg_train/en", 1200, batch=16); gen(NEG_EN, EN, f"{C}/neg_test/en", 200, batch=16)
print("ВСЁ ГОТОВО")
