"""The narrow cloud jobs on answers known in advance: python bench_narrow.py <model> <out.json> [job ...].
rules — the time in an owner's rule (the planner's real prompt and schema, a fixed «now»); router — which ready browser recipe
solves a task, if any; compact — the summary of an older conversation (are its key facts kept, is it short)."""
import json
import re
import sys
import time

import harness

model, out, only = sys.argv[1], sys.argv[2], set(sys.argv[3:])
harness.setup(model, "fast")

from sena.agents.ag_web import recipes  # noqa: E402
from sena.core import roles  # noqa: E402
from sena.memory import history, rules  # noqa: E402

NOW = "Сейчас 25.10.2026 14:00, воскресенье."
RULES = [
    ("по будням в половине восьмого утра присылай сводку новостей", {("26.10.2026 07:30", "weekdays")}),
    ("каждое воскресенье вечером напоминай полить цветы", {("25.10.2026 20:00", "weekly")}),
    ("если курс доллара вырастет выше 100 рублей, сразу скажи мне", set()),
    ("послезавтра в обед напомни забрать посылку", {("27.10.2026 13:00", "once")}),
    ("каждый вечер в десять спрашивай, как прошёл день", {("25.10.2026 22:00", "daily")}),
    ("по понедельникам и четвергам в 9 утра присылай план тренировок", {("26.10.2026 09:00", "weekly"), ("29.10.2026 09:00", "weekly")}),
    ("через неделю утром напомни продлить страховку", {("01.11.2026 09:00", "once")}),
    ("всегда отвечай мне коротко и без смайликов", set()),
    ("в эту пятницу в 18:30 напомни про встречу с Ольгой", {("30.10.2026 18:30", "once")}),
    ("по субботам и воскресеньям в 10 утра присылай прогноз погоды", {("31.10.2026 10:00", "weekly"), ("01.11.2026 10:00", "weekly")}),
    ("завтра разбуди меня в 7:15", {("26.10.2026 07:15", "once")}),
    ("каждый день днём напоминай выпить воды", {("26.10.2026 13:00", "daily")}),
]
RECIPES = [{"name": "youtube-play", "when": "включить видео или музыку на YouTube по запросу", "signature": "run(page, query)",
            "doc": "Ищет на YouTube и запускает первый ролик по запросу"},
           {"name": "hh-vacancies", "when": "найти вакансии на hh.ru по запросу и городу", "signature": "run(page, query, city)",
            "doc": "Открывает поиск hh.ru с фильтрами и возвращает первые вакансии"},
           {"name": "avito-search", "when": "найти объявления на avito.ru по запросу с ценой до", "signature": "run(page, query, max_price)",
            "doc": "Ищет на Авито с ограничением цены и возвращает первые объявления"},
           {"name": "sdek-track", "when": "узнать статус посылки СДЭК по номеру", "signature": "run(page, number)",
            "doc": "Открывает отслеживание СДЭК и возвращает статус"}]
ROUTES = [
    ("включи на ютубе лоу-фай для работы", "youtube-play", {"query": ("лоу-фай", "lo-fi", "lofi")}),
    ("найди вакансии python-разработчика в Москве на hh", "hh-vacancies", {"query": ("python",), "city": ("москв",)}),
    ("посмотри на авито велосипед до 20 тысяч", "avito-search", {"query": ("велосипед",), "max_price": ("20000", "20 000")}),
    ("где моя посылка СДЭК 1234567890", "sdek-track", {"number": ("1234567890",)}),
    ("где моя посылка СДЭК?", None, {}),
    ("найди вакансии python в Москве на hh и откликнись на первую", None, {}),
    ("поставь видео на ютубе на паузу", None, {}),
    ("найди на ютубе ролик про сёрфинг и скачай его на мак", None, {}),
    ("открой hh.ru и выйди из аккаунта", None, {}),
]
TALKS = [
    ("trip", [("user", "Слушай, давай планировать Порту."), ("assistant", "Давай! Когда хочешь лететь?"), ("user", "С 14 по 21 ноября."),
              ("assistant", "Записала даты. А бюджет?"), ("user", "Около 1500 евро на всё, без перелёта."), ("user", "Кстати, ты видела новый трейлер Дюны?"),
              ("assistant", "Видела, выглядит эффектно."), ("user", "Ага. Ладно, жить хочу у реки, Ольга советовала район Рибейра."),
              ("assistant", "Хорошо, ищу жильё в Рибейре."), ("user", "Какая там погода в ноябре?"), ("assistant", "Около 15 градусов, часто дожди."),
              ("user", "Понял, возьму куртку."), ("user", "И напомни мне купить страховку до 10 ноября."), ("assistant", "Поставила напоминание на 9 ноября."),
              ("user", "Машину брать не будем, ходим пешком и на метро."), ("assistant", "Принято, без аренды машины."), ("user", "Спасибо, ты лучшая."),
              ("assistant", "Обращайся!")],
     [r"\b14\b", r"\b21\b", r"1\s?500", r"рибейр", r"страхов", r"машин|аренд|авто", r"ольг"]),
    ("work", [("user", "По Hire-flow: релизим в пятницу вечером, 31 октября."), ("assistant", "Записала: релиз Hire-flow 31.10 вечером."),
              ("user", "Миграцию базы делает Игорь, не я."), ("assistant", "Поняла, миграция за Игорем."), ("user", "Баг со входом починили вчера."),
              ("user", "Что там с погодой завтра?"), ("assistant", "Завтра +27, без дождя."), ("user", "Окей. Описание релиза напиши до четверга, сам не успеваю."),
              ("assistant", "Подготовлю описание релиза к четвергу."), ("user", "И не присылай мне отчёты по выходным, бесит."),
              ("assistant", "Поняла, по выходным отчётов не будет."), ("user", "Перед релизом все тесты должны быть зелёными, это обязательно."),
              ("assistant", "Хорошо, проверю тесты перед релизом."), ("user", "Кстати, кот опять уронил кружку, ха."), ("assistant", "Бедная кружка!")],
     [r"пятниц|\b31\b", r"игор", r"миграц", r"четверг", r"выходн", r"тест"]),
    ("health", [("user", "С понедельника хожу в зал: понедельник, среда, пятница."), ("assistant", "Отлично! Записала расписание зала."),
                ("user", "Напоминаю: у меня аллергия на арахис, учитывай в рецептах."), ("assistant", "Помню, без арахиса."),
                ("user", "Записался к врачу на 3 ноября, в 10 утра."), ("assistant", "Поставила напоминание на 3 ноября, 10:00."),
                ("user", "Утренние новости больше не присылай, они меня грузят."), ("assistant", "Хорошо, утренние новости отключила."),
                ("user", "Сегодня такой красивый закат был."), ("assistant", "Здорово! Сфотографировал?"), ("user", "Да, скину потом."),
                ("user", "И я бросил кофе, перешёл на чай."), ("assistant", "Поняла, кофе больше не предлагаю."), ("user", "Пока!")],
     [r"понедельник|\bпн\b", r"сред|\bср\b", r"арахис", r"\b3 ноября|\b0?3\.11", r"новост", r"кофе"]),
]


def run_rules():
    rows = []
    for text, want in RULES:
        started = time.time()
        try:
            reply = roles._cloud(rules.PLAN + "\n" + NOW, text, rules.PLAN_SCHEMA, "замер: время в правиле", "low")
            got = {(j.get("at", "").strip(), j.get("repeat")) for j in (reply.get("jobs") or []) if isinstance(j, dict)}
            ok, why = got == want, f"получили {sorted(got)}, ждали {sorted(want)}"
        except Exception as e:
            ok, why, reply = False, f"{type(e).__name__}: {e}", None
        rows.append({"id": f"rules: {text[:40]}", "job": "rules", "passed": ok, "why": "" if ok else why, "seconds": round(time.time() - started, 1),
                     **harness.usage(started)})
    return rows


def run_router():
    rows = []
    for task, want, params in ROUTES:
        started = time.time()
        try:
            listing = [{k: r[k] for k in ("name", "when", "signature", "doc")} for r in RECIPES]
            choice = roles._cloud(recipes.ROUTER, f"Задача: {task}\n\nРецепты:\n{json.dumps(listing, ensure_ascii=False, indent=1)}", recipes.SCHEMA,
                                  "замер: быстрый путь браузера", "none")
            got, args = choice.get("recipe"), {k: str(v).lower() for k, v in (choice.get("args") or {}).items()}
            ok = got == want and all(any(w in args.get(p, "") for w in words) for p, words in params.items())
            why = f"получили {got} {args}, ждали {want}"
        except Exception as e:
            ok, why = False, f"{type(e).__name__}: {e}"
        rows.append({"id": f"router: {task[:40]}", "job": "router", "passed": ok, "why": "" if ok else why, "seconds": round(time.time() - started, 1),
                     **harness.usage(started)})
    return rows


def run_compact():
    rows = []
    for name, talk, facts in TALKS:
        started = time.time()
        old = "\n".join(f"{role}: {text}" for role, text in talk)
        try:
            summary = roles._cloud(history.COMPACT, f"# Прошлая сводка\nнет\n\n# Старые сообщения\n{old}", None, "замер: сводка переписки", "low")
            low = re.sub(r"[\u00a0\u202f\u2009]", " ", summary.lower())
            kept = [bool(re.search(pattern, low)) for pattern in facts]
            words = len(re.findall(r"\w+", summary))
            ok = all(kept) and words <= 260
            why = f"сохранено фактов {sum(kept)} из {len(facts)}, слов {words}"
        except Exception as e:
            ok, why, kept, words, summary = False, f"{type(e).__name__}: {e}", [], 0, ""
        rows.append({"id": f"compact: {name}", "job": "compact", "passed": ok, "why": why, "facts_kept": sum(kept), "facts": len(facts), "words": words,
                     "summary": summary, "missed": [f for f, k in zip(facts, kept) if not k],
                     "seconds": round(time.time() - started, 1), **harness.usage(started)})
    return rows


results = []
for job, func in (("rules", run_rules), ("router", run_router), ("compact", run_compact)):
    if not only or job in only:
        results += func()
for r in results:
    print(f"{'+' if r['passed'] else '-'} {r['id']}: {r['why']}", flush=True)
harness.save(out, {"model": model, "bench": "narrow", "cases": results, "rows": harness.rows()})
