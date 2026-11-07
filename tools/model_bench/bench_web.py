"""The browser agent (ag_web) on a shop that lives on this Mac: python bench_web.py <model> <out.json> [task ids...].
The real agent, its prompt, tools and windowless Chrome; only the model differs. A task is graded by what happened on the
site (the form that arrived, the code applied, the payment that must not happen) and by the facts in the agent's answer."""
import re
import sys
import threading
import time

import harness

model, out, only = sys.argv[1], sys.argv[2], set(sys.argv[3:])
harness.setup(model, "fast")

import shopsite as shop_site  # noqa: E402
from sena.agents.ag_web import agent, browser, recipes  # noqa: E402
from sena.core import status  # noqa: E402

recipes.try_fast = lambda task: (None, "")   # the model is measured, not a ready recipe
asked = []


def owner(question):
    # the owner is here and answers as he would: the task is his own request, so — exactly as it says, nothing more
    asked.append(question)
    return "Тема ответил: делай строго так, как сказано в задаче."


agent.ask_tema = owner
shop, server, BASE = shop_site.start()


def digits(text):
    return re.sub(r"\D", "", text or "")


def has_number(text, number):
    return str(number) in digits(text) or f"{number:,}".replace(",", " ") in (text or "")


TASKS = [
    {"id": "catalog", "task": f"На сайте {BASE}/catalog найди самый дешёвый горный велосипед, который есть в наличии. Назови модель и цену.",
     "done_when": "названа модель и цена самого дешёвого горного велосипеда в наличии",
     "grade": lambda a: ("Forward Sporting" in a and has_number(a, 31490), "модель или цена не та")},
    {"id": "form", "task": (f"Запиши меня на тест-драйв на сайте {BASE}/testdrive: имя Артём Иванов, телефон +7 900 123-45-67, модель "
                            "Forward Sporting 27.5 2.0, получить самовывозом, дата 5 ноября 2026, в комментарии «после 18:00». Согласие на "
                            "обработку данных поставь и отправь заявку."),
     "done_when": "заявка отправлена и сайт показал её номер",
     "grade": lambda a: _form_ok()},
    {"id": "login", "task": (f"Войди в личный кабинет на сайте {BASE}/login (логин demo, пароль Koleso-2026) и скажи, сколько у меня бонусных "
                             "баллов и какой номер у последнего заказа."),
     "done_when": "названы бонусный баланс и номер последнего заказа",
     "grade": lambda a: (has_number(a, 1870) and "58213" in digits(a), "баланс или номер заказа не те")},
    {"id": "reviews", "task": f"На странице {BASE}/reviews найди отзыв Марины К.: какую оценку она поставила и на что жаловалась?",
     "done_when": "названы оценка Марины К. и суть её жалобы",
     "grade": lambda a: (bool(re.search(r"\b3\b|тро[йе]к", a)) and "тормоз" in a.lower(), "оценка или жалоба не те")},
    {"id": "promo", "task": f"На странице акций {BASE}/promo промокод нарисован на картинке. Найди его и примени в форме на той же странице.",
     "done_when": "сайт подтвердил, что скидка применена",
     "grade": lambda a: (any(c.upper() == shop_site.PROMO for c in shop.promo), f"применены коды: {shop.promo or 'никакие'}")},
    {"id": "count", "task": f"На сайте {BASE}/catalog посчитай, сколько горных велосипедов в наличии стоят дешевле 40 000 ₽.",
     "done_when": "названо число таких велосипедов",
     "grade": lambda a: (bool(re.search(r"\b6\b|шесть", a)), "число не то (ждали 6)")},
    {"id": "folding", "task": (f"Найди в каталоге {BASE}/catalog самый дорогой складной велосипед из тех, что есть в наличии, и запиши меня на "
                               f"тест-драйв на него ({BASE}/testdrive): имя Артём Иванов, телефон +7 900 123-45-67, самовывоз, дата 7 ноября 2026, "
                               "согласие поставь."),
     "done_when": "заявка на тест-драйв этой модели отправлена, сайт показал номер",
     "grade": lambda a: _folding_ok()},
    {"id": "newsletter", "task": f"Подпиши адрес artem.test@example.com на рассылку акций на странице {BASE}/newsletter.",
     "done_when": "сайт подтвердил подписку",
     "grade": lambda a: _subscribe_ok()},
    {"id": "cart", "task": (f"Положи в корзину шлем Abus Hyban 2.0 со страницы {BASE}/helmets и скажи итоговую сумму к оплате с доставкой. "
                            "Не оплачивай."),
     "done_when": "шлем в корзине и названа сумма к оплате; оплата не нажата",
     "grade": lambda a: _cart_ok(a)},
]


def _booking_ok():
    for f in shop.bookings:
        if f.get("place") == "Колесо на Ленина" and f.get("date") == "2026-12-12" and f.get("time") == "15:00" and "Артём" in f.get("name", ""):
            return True, ""
    return False, f"записи: {shop.bookings or 'нет'}"


def _checkout_ok():
    if shop.payments:
        return False, "нажата оплата картой"
    for f in shop.orders:
        if f.get("point") == "ПВЗ Казань, ул. Баумана 15" and f.get("pay") == "cash" and digits(f.get("phone")) == "79001234567":
            return True, ""
    return False, f"заказы: {shop.orders or 'нет'}"


HARD = [
    {"id": "booking", "task": f"Запиши меня на ТО велосипеда на сайте {BASE}/service: мастерская «Колесо на Ленина», 12 декабря 2026 в 15:00, имя Артём.",
     "done_when": "сайт подтвердил запись", "grade": lambda a: _booking_ok()},
    {"id": "scroll", "task": f"Узнай на странице {BASE}/accessories, сколько стоит фляга Elite Fly 750 мл.",
     "done_when": "названа цена фляги", "grade": lambda a: (has_number(a, 1290), "цена не та (ждали 1290)")},
    {"id": "checkout", "task": (f"Оформи заказ на странице {BASE}/checkout: самовывоз из пункта выдачи в Казани на улице Баумана, телефон +7 900 123-45-67, "
                                "оплата при получении. Картой не плати."),
     "done_when": "сайт показал номер оформленного заказа", "grade": lambda a: _checkout_ok()},
    {"id": "newtab", "task": f"На странице {BASE}/price открой прайс доставки и скажи, сколько стоит доставка в Казань и за сколько дней она приходит.",
     "done_when": "названы цена и срок доставки в Казань", "grade": lambda a: (has_number(a, 790) and bool(re.search(r"3\s*[–—-]\s*4|3 до 4|от 3", a)), "цена или срок не те (790 ₽, 3–4 дня)")},
]


def _folding_ok():
    models = [f.get("model") for f in shop.testdrives]
    return "Shulz Krabi Coaster" in models, f"записались на: {models or 'ничего'}"


def _subscribe_ok():
    mails = [f.get("email", "") for f in shop.subscriptions]
    return "artem.test@example.com" in mails, f"подписаны: {mails or 'никто'}"


def _form_ok():
    for f in shop.testdrives:
        good = ("Артём" in f.get("name", "") and "Иванов" in f.get("name", "") and digits(f.get("phone")) in ("79001234567", "89001234567")
                and f.get("model") == "Forward Sporting 27.5 2.0" and f.get("method") == "Самовывоз" and f.get("date") == "2026-11-05"
                and "18" in f.get("comment", ""))
        if good:
            return True, ""
    return False, f"заявки: {shop.testdrives or 'нет'}"


def _cart_ok(answer):
    if shop.payments:
        return False, "нажата оплата"
    if not any("Abus Hyban 2.0" in items for items in shop.carts.values()):
        return False, "шлема нет в корзине"
    return has_number(answer, 7490), "сумма не та"


if "hard" in only:   # the harder set on its own
    TASKS, only = HARD, set()
results = []
status.local.tab = threading.get_ident()
for task in TASKS:
    if only and task["id"] not in only:
        continue
    status.begin(task["task"])
    started, before = time.time(), len(asked)
    try:
        answer = agent.run(task["task"], done_when=task["done_when"])
    except Exception as e:
        answer = f"(ошибка: {type(e).__name__}: {e})"
    seconds = time.time() - started
    ok, why = task["grade"](answer if isinstance(answer, str) else str(answer))
    status.end(str(answer)[:500])
    results.append({"id": task["id"], "passed": bool(ok), "why": "" if ok else why, "answer": str(answer)[:600], "seconds": round(seconds, 1),
                    "asked_owner": asked[before:], **harness.usage(started)})
    print(f"{'+' if ok else '-'} {task['id']}: {round(seconds)} с, {results[-1]['usd']:.4f} $ {'' if ok else why}", flush=True)
browser.stop()
harness.save(out, {"model": model, "bench": "web", "cases": results, "rows": harness.rows()})
