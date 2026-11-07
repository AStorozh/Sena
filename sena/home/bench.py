"""Measuring the home models on what Sena really does — the numbers the choice of a model rests on (python -m sena.home bench|check).
- golden: a small registry "as after a year" and the owner's real questions; the answer must be in the top five of recall.find_rows.
  Also runs in tests/test_search_quality.py on word-based test fingerprints (the pipeline), here on the real model (the meaning).
- paraphrase: Russian questions that share almost no words with their answers (slang, synonyms, another script) — only a
  fingerprint model that understands meaning finds them; recall@1, recall@5, MRR.
- jobs: overheard conversations and the hourly check — the home model with the production prompts, judged by fixed checks.
Everything is written to the registry of whatever data folder is active: the command line points it at a temporary one."""
import time
from datetime import datetime

from sena.agents import ag_ears
from sena.agents.ag_sena import prompt
from sena.core import local
from sena.memory import index, people

DAY = 86400
TOP = 5


# ---------- golden: the pipeline on the owner's questions ----------
def golden_fill():
    """Everything a year of use leaves behind, in miniature: facts, people, messages, tasks, notes, skills, files, overheard lines, rules, entities."""
    now = time.time()
    at = lambda days: now - days * DAY
    put = index.put
    put("факт", "f1", "наушники Marshall Major IV, синие", at(200), meta={"tier": "core", "facet": "Обо мне"})
    put("факт", "f2", "любит кофе без сахара и чай с лимоном", at(150), meta={"tier": "core", "facet": "Предпочтения"})
    put("факт", "f3", "в ноябре съездить в Далат", at(20), meta={"tier": "core", "facet": "Планы"})
    put("факт", "f4", "паспорт действует до марта 2031", at(300), meta={"tier": "archive", "facet": "Обо мне"})
    put("факт", "f5", "номер машины A123BC, Toyota Corolla серая", at(250), meta={"tier": "archive", "facet": "Обо мне"})
    put("факт", "f6", "KSclay — керамическая мастерская, сайт на Tilda", at(100), meta={"tier": "core", "facet": "Проекты"})
    people._people = []
    olga = people.new_person(name="Ольга")
    people.update(olga["id"], facts=["сестра", "присылает документы по пятницам"])
    igor = people.new_person(name="Игорь")
    people.update(igor["id"], facts=["бухгалтер", "считает налоги по УСН"])
    serkan = people.new_person(name="Серкан")
    people.update(serkan["id"], facts=["партнёр по KSclay", "живёт в Стамбуле"])
    put("проект", "Проекты/KSclay.md", "KSclay (кс клэй): керамическая мастерская, сайт на Tilda", at(90), meta={"aliases": ["кс клэй"]})
    put("место", "Места/Порту.md", "Порту: город на севере Португалии, река Дору", at(60))
    index.register_names("проект", {"KSclay": "Проекты/KSclay.md", "кс клэй": "Проекты/KSclay.md"})
    index.register_names("место", {"Порту": "Места/Порту.md"})
    chat = [("Тема", "сколько стоит доллар сегодня"), ("Сена", "25 890 донгов за доллар"),
            ("Тема", "закажи столик в ресторане Хачапури на пятницу"), ("Сена", "Столик на двоих в 19:00 забронирован"),
            ("Тема", "как называлась гостиница в Порту"), ("Сена", "Вы жили в Porto Riverside, у реки Дору")]
    for i, (author, text) in enumerate(chat, 1):
        put("сообщение", f"c:{i}", text, at(30 - i), meta={"author": author, "facet": "telegram"})
    put("задача", "t1", "найти комод Серкана на WB — нашла, артикул 123. Замечание: не тот комод", at(40), meta={"facet": "владелец"})
    put("задача", "t2", "включить музыку на YouTube — играет плейлист lo-fi", at(12), meta={"facet": "владелец"})
    put("задача", "t3", "каждое утро в 8 присылать погоду — отправлено", at(1), meta={"facet": "расписание"})
    put("задача", "t4", "ночная уборка: бэкап памяти — готово", at(1), meta={"facet": "служебная"})
    put("задача", "t5", "оплатить счёт от Игоря за октябрь — счёт на 12 000 рублей оплачен", at(15), meta={"facet": "владелец"})
    put("заметка", "Знания/Комиссия Wildberries.md#0", "Комиссия Wildberries: на Wildberries комиссия считается от цены со скидкой", at(80),
        meta={"author": "owner", "facet": "Знания"})
    put("заметка", "Знания/Хачапури.md#0", "Рецепт хачапури: тесто на мацони, сыр сулугуни и имеретинский", at(70), meta={"author": "owner", "facet": "Знания"})
    put("заметка", "Проекты/KSclay.md#0", "KSclay: план на ноябрь — запустить каталог, обновить фото изделий", at(5), meta={"author": "owner", "facet": "Проекты"})
    put("навык", "wb-поиск", "wb-поиск — когда ищем товар на WB: открыть wildberries.ru, вбить артикул", at(50))
    put("навык", "mac-громкость", "mac-громкость — менять громкость на Mac: osascript set volume", at(55))
    put("урок", "l1", "hh.ru: вход на hh.ru во вложенном окне, кнопка «Войти» внутри него", at(20), meta={"agent": "ag_web", "scope": "hh.ru"})
    put("цель", "g1", "Английский до B2 к маю. Для работы за границей. Срок: 01.05.2027. Шаги: ✓ записаться на курс; разговорный клуб по средам", at(25),
        meta={"status": "active"})
    put("файл", "generated/kscl.png", "kscl.png: логотип для сайта KSclay, керамическая чашка", at(9), meta={"facet": "image"})
    put("файл", "inbox/schet_oktyabr.pdf", "schet_oktyabr.pdf: счёт от Игоря за бухгалтерские услуги, октябрь", at(16), meta={"facet": "pdf"})
    put("файл", "inbox/bilety_lissabon.pdf", "bilety_lissabon.pdf: билеты Москва — Лиссабон на 12 марта", at(3), meta={"facet": "pdf"})
    put("услышано", "h1", "Игорь: сайт нужно переделать к пятнице", at(7), meta={"pid": igor["id"], "who": "Игорь"})
    index.link("услышано", "h1", "человек", igor["id"], "said_by")
    put("услышано", "h2", "Ольга: документы отправлю в пятницу утром", at(6), meta={"pid": olga["id"], "who": "Ольга"})
    index.link("услышано", "h2", "человек", olga["id"], "said_by")
    put("правило", "r1", "не звонить после 23:00, писать текстом", at(120))
    put("правило", "r1", "не звонить после 22:00, писать текстом", at(2))   # changed: the old wording stays in history
    index.relink_mentions()
    index.embed_pending()
    return {"olga": olga["id"], "igor": igor["id"], "serkan": serkan["id"], "t1_month": f"{datetime.fromtimestamp(at(40)):%Y-%m}",
            "empty_month": f"{datetime.fromtimestamp(at(700)):%Y-%m}"}


def golden_questions(ids):
    """(question, find arguments, what counts as the answer: (kind, ref prefix)) — any of the listed answers in the top five is a hit."""
    person = lambda pid: [("человек", pid)]
    return [
        ("какие у меня наушники", {}, [("факт", "f1")]),
        ("какой я люблю кофе", {}, [("факт", "f2")]),
        ("чай с лимоном", {}, [("факт", "f2")]),
        ("куда я собирался в ноябре", {}, [("факт", "f3")]),
        ("что я говорил про Далат", {}, [("факт", "f3")]),
        ("до какого года паспорт", {}, [("факт", "f4")]),
        ("номер моей машины", {}, [("факт", "f5")]),
        ("Toyota Corolla", {}, [("факт", "f5")]),
        ("керамическая мастерская", {}, [("факт", "f6"), ("проект", "Проекты/KSclay.md")]),
        ("кто такая Ольга", {}, person(ids["olga"])),
        ("налоги по УСН", {}, person(ids["igor"])),
        ("что говорил Игорь про сайт", {"person": "Игорь"}, [("услышано", "h1")]),
        ("когда Ольга пришлёт документы", {"person": "Ольге"}, [("услышано", "h2"), ("человек", ids["olga"])]),
        ("", {"person": "Серкан"}, [("задача", "t1")]),   # everything about a person, no question: the task that mentions him
        ("курс доллара", {}, [("сообщение", "c:1"), ("сообщение", "c:2")]),
        ("где мы бронировали столик", {}, [("сообщение", "c:3"), ("сообщение", "c:4")]),
        ("как называлась гостиница в Порту", {}, [("сообщение", "c:5"), ("сообщение", "c:6")]),
        ("что ты знаешь о Порту", {}, [("место", "Места/Порту.md"), ("сообщение", "c:5")]),
        ("что было с комодом Серкана", {}, [("задача", "t1")]),
        ("комод", {"period": ids["t1_month"]}, [("задача", "t1")]),
        ("какой плейлист играл", {}, [("задача", "t2")]),
        ("что ты делаешь каждое утро", {}, [("задача", "t3")]),
        ("бэкап памяти", {}, [("задача", "t4")]),
        ("сколько я заплатил Игорю за октябрь", {}, [("задача", "t5"), ("файл", "inbox/schet_oktyabr.pdf")]),
        ("", {"kinds": ["задача"], "period": "month"}, [("задача", "t3")]),   # browsing by kind and period
        ("как считается комиссия Wildberries", {}, [("заметка", "Знания/Комиссия Wildberries.md")]),
        ("рецепт хачапури", {}, [("заметка", "Знания/Хачапури.md")]),
        ("сулугуни", {}, [("заметка", "Знания/Хачапури.md")]),
        ("планы на ноябрь", {"project": "KSclay"}, [("заметка", "Проекты/KSclay.md")]),
        ("фото изделий", {"project": "кс клэй"}, [("заметка", "Проекты/KSclay.md")]),
        ("как искать товар на WB", {}, [("навык", "wb-поиск")]),
        ("как поменять громкость на маке", {}, [("навык", "mac-громкость")]),
        ("как войти на hh.ru", {}, [("урок", "l1")]),   # an agent's own lesson is found by the coordinator too
        ("как там мой английский", {}, [("цель", "g1")]),   # a long goal comes up by meaning
        ("где логотип для сайта KSclay", {}, [("файл", "generated/kscl.png")]),
        ("счёт от Игоря", {"kinds": ["файл"]}, [("файл", "inbox/schet_oktyabr.pdf")]),
        ("билеты в Лиссабон", {}, [("файл", "inbox/bilety_lissabon.pdf")]),
        ("можно ли звонить ночью", {}, [("правило", "r1")]),
        ("после скольки нельзя звонить — как это правило звучало раньше", {"history": True}, [("правило", "r1", "23:00")]),   # the previous wording, from versions
    ]


def hit(rows, answers):
    for r in rows[:TOP]:
        for a in answers:
            if r["kind"] == a[0] and r["ref"].startswith(a[1]) and (len(a) < 3 or a[2] in r["text"]):
                return True
    return False


# ---------- paraphrase: meaning without shared words ----------
RECORDS = {
    "f1": "Наушники Marshall Major IV, синие",
    "f2": "Любит кофе без сахара, утром американо",
    "f3": "В ноябре хочет съездить в Далат",
    "f4": "Паспорт действует до марта 2031 года",
    "f5": "Машина: Toyota Corolla, серая, номер A123BC",
    "f6": "KSclay — керамическая мастерская, сайт на Tilda",
    "f7": "Аллергия на арахис",
    "f8": "Размер обуви 43",
    "f9": "Живёт в Нячанге, район у моря",
    "f10": "Работает по утрам, с 7 до 11 не отвлекать",
    "f11": "Зарплата приходит на счёт в Тинькофф",
    "f12": "Учит вьетнамский, занятия по вторникам",
    "f13": "Любимый фильм — «Интерстеллар»",
    "f14": "Тренировки в зале три раза в неделю",
    "f15": "Не ест мясо по понедельникам",
    "f16": "День рождения — 14 июля",
    "f17": "Ноутбук — MacBook Air M3, 16 ГБ",
    "f18": "Хочет научиться серфингу",
    "f19": "Подписка на Spotify семейная",
    "f20": "Предпочитает переписку голосовым сообщениям",
    "p_olga": "Ольга: сестра, присылает документы по пятницам",
    "p_igor": "Игорь: бухгалтер, считает налоги по УСН",
    "p_serkan": "Серкан: партнёр по KSclay, живёт в Стамбуле",
    "p_anna": "Анна Петрова: стоматолог, клиника на Чан Фу",
    "p_mark": "Марк: тренер по серфингу, пляж Бай Зай",
    "p_lena": "Лена: соседка, присматривает за котом",
    "m1": "Сколько стоит доллар сегодня? — 25 890 донгов",
    "m2": "Закажи столик в грузинском ресторане на пятницу",
    "m3": "Столик на двоих в 19:00 забронирован в «Хачапури и вино»",
    "m4": "Как называлась гостиница в Порту? — Porto Riverside у реки Дору",
    "m5": "Напомни купить корм коту",
    "m6": "Переведи на вьетнамский: где ближайшая аптека",
    "m7": "Какая завтра погода? — Ясно, +31, ветер слабый",
    "m8": "Отправь Игорю выписку за сентябрь",
    "m9": "Найди дешёвые билеты в Бангкок на выходные",
    "m10": "Сколько калорий в фо бо?",
    "t1": "Найти комод на Wildberries для Серкана — нашла, артикул 123, но не тот цвет",
    "t2": "Включить музыку на YouTube — играет плейлист lo-fi",
    "t3": "Каждое утро в 8 присылать погоду",
    "t4": "Бэкап памяти ночью — готово",
    "t5": "Оплатить счёт Игоря за октябрь — 12 000 рублей оплачено",
    "t6": "Записаться к стоматологу Анне на четверг — записано на 15:00",
    "t7": "Сравнить цены на велосипеды Giant — лучший вариант 14 млн донгов",
    "t8": "Продлить домен asto-portfolio.ru — продлён до 2027 года",
    "t9": "Собрать вакансии продакт-менеджера на hh.ru — найдено 12",
    "t10": "Отменить подписку на Netflix — отменена",
    "n1": "Комиссия Wildberries: считается от цены со скидкой, для керамики 17%",
    "n2": "Рецепт хачапури по-аджарски: тесто на мацони, сулугуни, яйцо",
    "n3": "KSclay, план на ноябрь: каталог, новые фото изделий, реклама в Instagram",
    "n4": "Как настроить роутер: адрес 192.168.1.1, сменить канал Wi-Fi на 6",
    "n5": "Визы во Вьетнам: электронная виза на 90 дней, продление через агентство",
    "n6": "Идеи подарков Ольге: шарф, книга по керамике, сертификат в спа",
    "n7": "Налоги ИП на УСН 6%: платить до 28 числа после квартала",
    "n8": "Серфинг: лучшие волны в Муйне с ноября по март",
    "n9": "Список покупок для дома: лампочки E27, батарейки AA, губки",
    "n10": "Конспект книги «Атомные привычки»: маленькие шаги, система важнее цели",
    "d1": "schet_oktyabr.pdf: счёт от Игоря за бухгалтерские услуги, октябрь",
    "d2": "bilety_lissabon.pdf: билеты Москва — Лиссабон на 12 марта",
    "d3": "kscl_logo.png: логотип для сайта KSclay, керамическая чашка",
    "d4": "dogovor_arendy.pdf: договор аренды квартиры в Нячанге на год",
    "d5": "foto_kot.jpg: кот спит на подоконнике",
    "d6": "spravka_2ndfl.pdf: справка о доходах за 2025 год",
    "d7": "skrin_oshibki.png: ошибка 502 на сайте asto-portfolio.ru",
    "h1": "Игорь: сайт нужно переделать к пятнице",
    "h2": "Ольга: документы отправлю в пятницу утром",
    "h3": "Марк: завтра волны будут до двух метров, приходи к шести",
    "h4": "Лена: кот опять не ел, может, корм не тот",
    "r1": "Не звонить после 22:00, писать текстом",
    "r2": "Когда прошу сайт — сначала собери требования",
    "r3": "Отвечай кратко, без воды",
    "s1": "wb-поиск: искать товар на Wildberries по артикулу",
    "s2": "mac-громкость: менять громкость на Mac",
    "s3": "youtube-плеер: включить видео или музыку на YouTube",
}

QUESTIONS = [
    ("в чём я слушаю музыку", ["f1"]),
    ("что я пью по утрам", ["f2"]),
    ("куда я хотел поехать осенью", ["f3"]),
    ("когда истекает загранник", ["f4"]),
    ("какая у меня тачка", ["f5"]),
    ("чем занимается моя мастерская", ["f6"]),
    ("что мне нельзя есть", ["f7", "f15"]),
    ("какой у меня размер ноги", ["f8"]),
    ("в каком городе я живу", ["f9"]),
    ("когда меня лучше не беспокоить", ["f10", "r1"]),
    ("какой язык я изучаю", ["f12"]),
    ("мой любимый фильм", ["f13"]),
    ("как часто я хожу в спортзал", ["f14"]),
    ("когда у меня день рождения", ["f16"]),
    ("на каком компьютере я работаю", ["f17"]),
    ("каким спортом я хочу заняться на море", ["f18", "n8"]),
    ("кто моя сестра", ["p_olga"]),
    ("кто считает мне налоги", ["p_igor"]),
    ("с кем я веду бизнес в Турции", ["p_serkan"]),
    ("к какому врачу я хожу лечить зубы", ["p_anna", "t6"]),
    ("кто учит меня кататься на доске", ["p_mark"]),
    ("кто кормит кота, когда меня нет", ["p_lena"]),
    ("почём сегодня бакс", ["m1"]),
    ("где мы бронировали ужин", ["m3", "m2"]),
    ("в каком отеле я жил в Португалии", ["m4"]),
    ("что нужно купить для питомца", ["m5"]),
    ("как по-вьетнамски спросить про лекарства", ["m6"]),
    ("будет ли завтра жарко", ["m7"]),
    ("что я просил переслать бухгалтеру", ["m8"]),
    ("куда я искал перелёт в Таиланд", ["m9"]),
    ("сколько энергии во вьетнамском супе", ["m10"]),
    ("что было с мебелью для партнёра", ["t1"]),
    ("что играло фоном", ["t2"]),
    ("что ты присылаешь мне каждый день", ["t3"]),
    ("делала ли ты резервную копию", ["t4"]),
    ("сколько я заплатил бухгалтеру", ["t5", "d1"]),
    ("когда мне к зубному", ["t6", "p_anna"]),
    ("какой велик мы выбрали", ["t7"]),
    ("до какого года оплачен адрес сайта", ["t8"]),
    ("сколько нашлось работы по профессии", ["t9"]),
    ("от какого стримингового сервиса я отказался", ["t10"]),
    ("как маркетплейс берёт процент", ["n1"]),
    ("как готовить грузинскую лодочку с сыром", ["n2"]),
    ("что запланировано для мастерской на следующий месяц", ["n3"]),
    ("как зайти в настройки домашнего интернета", ["n4"]),
    ("сколько можно жить во Вьетнаме без продления", ["n5"]),
    ("что подарить сестре", ["n6"]),
    ("до какого числа платить налог за квартал", ["n7"]),
    ("когда лучший сезон для волн", ["n8"]),
    ("что купить в хозяйственном", ["n9"]),
    ("о чём книга про привычки", ["n10"]),
    ("квитанция за бухгалтерию", ["d1"]),
    ("документ на перелёт в Португалию", ["d2"]),
    ("картинка с эмблемой магазина керамики", ["d3"]),
    ("бумаги на жильё", ["d4"]),
    ("фотография питомца", ["d5"]),
    ("документ о заработке для налоговой", ["d6"]),
    ("скриншот, когда сайт лежал", ["d7"]),
    ("что говорил бухгалтер про сайт", ["h1"]),
    ("когда сестра пришлёт бумаги", ["h2", "p_olga"]),
    ("какие будут волны завтра", ["h3"]),
    ("почему кот не ест", ["h4"]),
    ("можно ли звонить поздно вечером", ["r1"]),
    ("что делать, если прошу сделать сайт", ["r2"]),
    ("как длинно отвечать", ["r3"]),
    ("как найти вещь по номеру на маркетплейсе", ["s1"]),
    ("как сделать звук тише на ноутбуке", ["s2"]),
    ("как поставить песню", ["s3", "t2"]),
]

KIND_OF = {"f": "факт", "p": "человек", "m": "сообщение", "t": "задача", "n": "заметка", "d": "файл", "h": "услышано", "r": "правило", "s": "навык"}


def paraphrase_fill():
    for ref, text in RECORDS.items():
        kind = KIND_OF[ref[0]]
        meta = {"author": "owner"} if kind == "заметка" else {"tier": "core"} if kind == "факт" else None
        index.put(kind, f"bench:{ref}", text, time.time() - DAY, meta=meta)
    index.embed_pending()


def paraphrase_score(meaning_only=False):
    """{"r1", "r5", "mrr", "misses"} on the paraphrase set: the registry's search (meaning + words), or meaning alone."""
    r1 = r5 = mrr = 0.0
    misses = []
    for question, want in QUESTIONS:
        rows = index.search(question, k=20, scope="all", min_cosine=0.0) if not meaning_only else _by_meaning(question)
        order = [r["ref"].removeprefix("bench:") for r in rows]
        ranks = [order.index(w) + 1 for w in want if w in order]
        rank = min(ranks) if ranks else 99
        r1 += rank == 1
        r5 += rank <= TOP
        mrr += 1 / rank
        if rank > TOP:
            misses.append(f"{question} -> {order[:3]}")
    n = len(QUESTIONS)
    return {"r1": round(r1 / n, 3), "r5": round(r5 / n, 3), "mrr": round(mrr / n, 3), "misses": misses}


def _by_meaning(question):
    _, q = index.embed_pending(query=question)
    with index._lock:
        index._load_matrix()
        cos, order = index._vector_hits(q, None)
        rows = index._connect().execute(f"SELECT id, ref FROM items WHERE id IN ({','.join('?' * len(order))})", order).fetchall() if order else []
    ref = dict(rows)
    return [{"ref": ref[i]} for i in order if i in ref]


# ---------- jobs: the home model on overheard speech and the hourly check ----------
CARDS = {"p1": {"id": "p1", "name": "Ольга", "aliases": [], "facts": [{"text": "сестра Темы", "valid_to": None}]},
         "p2": {"id": "p2", "name": "Игорь", "aliases": [], "facts": [{"text": "бухгалтер", "valid_to": None}]},
         "p3": {"id": "p3", "name": "Серкан", "aliases": [], "facts": [{"text": "партнёр по KSclay", "valid_to": None}]}}
PEOPLE = "- p1: Ольга — сестра Темы\n- p2: Игорь — бухгалтер\n- p3: Серкан — партнёр по KSclay"


def _lines(text):
    out = []
    for row in text.splitlines():
        head, said = row.split("] ", 1)
        who, pid = head.strip("[").split(" / ")
        out.append({"pid": pid, "who": who, "text": said})
    return out


def _named(ops, pid, stem):
    return any(o[0] == "name" and o[1] == pid and stem in o[2].lower() for o in ops)


def _noted(ops, pid, stem=""):
    return any(o[0] == "note" and o[1] == pid and o[2] and (not stem or any(stem in f.lower() for f in o[2])) for o in ops)


EARS_CASES = [
    ("[неизвестный / g1] Привет, я Марк, буду вести у тебя серфинг по утрам\n[Тема / tema] Отлично, Марк, во сколько встречаемся?\n"
     "[неизвестный / g1] В шесть на пляже Бай Зай, возьми воду и крем", lambda ops: _named(ops, "g1", "марк") and _noted(ops, "g1")),
    ("[неизвестный / g1] В эфире новости, сегодня в Москве ожидается дождь\n[неизвестный / g2] А теперь о спорте: Спартак проиграл Зениту со счётом ноль два",
     lambda ops: not ops),
    ("[Ольга / p1] Я переехала в Дананг, теперь работаю в школе английского\n[Тема / tema] Здорово, ты давно хотела к морю",
     lambda ops: _noted(ops, "p1", "дананг")),
    ("[Игорь / p2] Запиши, пароль от банка у меня Qwerty123, не забудь\n[Тема / tema] Хорошо, и декларацию до пятницы сдашь?\n[Игорь / p2] Да, в пятницу отправлю",
     lambda ops: not any("qwerty" in f.lower() for o in ops if o[0] == "note" for f in o[2])),
    ("[неизвестный / g1] Тема, это Ольга, звоню с другого номера, мой разрядился\n[Тема / tema] Привет, Оль, что случилось?\n"
     "[неизвестный / g1] Ничего, просто напоминаю про день рождения мамы в субботу",
     lambda ops: any(o[0] == "merge" and o[1] == "p1" and o[2] == "g1" for o in ops) or _named(ops, "g1", "ольг")),
    ("[неизвестный / g1] Ну что, как дела?\n[Тема / tema] Да нормально, а у тебя?\n[неизвестный / g1] Тоже ничего, пойдём поедим где-нибудь",
     lambda ops: not any(o[0] == "note" for o in ops)),
    ("[Серкан / p3] Клиент из Гамбурга заказал двести чашек, отгрузка через месяц\n[Тема / tema] Отлично, значит нужен второй гончарный круг\n"
     "[Серкан / p3] Я уже нашёл, куплю на этой неделе", lambda ops: _noted(ops, "p3")),
]

MEMORY = "## Обо мне\n- Живёт в Нячанге\n## Люди\n- Мама и папа в Москве\n- Серкан — партнёр по KSclay\n- Игорь — бухгалтер\n## Проекты\n- KSclay — керамическая мастерская"
HEARTBEAT_CASES = [
    ("Чат «Мама» (непрочитано 2): [10:02] Мама: Сынок, у папы завтра операция, позвони срочно", True),
    ("Чат «Ozon» (непрочитано 1): [09:00] Ozon: Распродажа -70% только сегодня!", False),
    ("Дела на ближайшие два часа:\n24.10.2026 15:00 — Встреча с Анной, стоматолог", True),
    ("Почта, непрочитанное:\nVietnam Airlines — «Ваш рейс VN123 перенесён на 06:00 завтра»", True),
    ("Чат «Соседи Нячанг» (непрочитано 14): [11:20] Дима: кто-нибудь знает хорошего сантехника?", False),
    ("Чат «Серкан» (непрочитано 1): [12:40] Серкан: Клиент из Германии хочет оптом 200 чашек, ответить надо сегодня", True),
    ("Почта, непрочитанное:\nSpotify — «Ваша подписка продлена»", False),
    ("Чат «Игорь» (непрочитано 1): [16:10] Игорь: завтра последний день подачи декларации, пришли выписку", True),
    ("Чат «Канал Медуза» (непрочитано 40): новости дня", False),
    ("Дела на ближайшие два часа:\n24.10.2026 20:00 — Прислать Теме сводку новостей", False),
]


def ears_check():
    """The home model on overheard conversations, through the code that accepts its answers: (passed, total, seconds each, misses)."""
    ok, spent, misses = 0, [], []
    for convo, check in EARS_CASES:
        started = time.time()
        try:
            reply = local.chat(ag_ears.ANALYZE, f"# Разговор\n{convo}\n\n# Люди\n{PEOPLE}", ag_ears.SCHEMA, max_tokens=700, purpose="замер")
            voices = {e["pid"]: [[1.0]] for e in _lines(convo) if e["pid"].startswith("g")}
            ops = ag_ears.accept(reply, _lines(convo), voices, CARDS.get)
            good = check(ops)
        except (local.Unavailable, ValueError) as e:
            good, ops = False, str(e)
        spent.append(time.time() - started)
        ok += good
        if not good:
            misses.append(f"{convo.splitlines()[0][:60]} -> {ops}")
    return ok, len(EARS_CASES), round(sum(spent) / len(spent), 1), misses


def heartbeat_check():
    """The home model on the hourly check: does it flag exactly what matters? (passed, total, seconds each, misses)."""
    ok, spent, misses = 0, [], []
    for item, want in HEARTBEAT_CASES:
        started = time.time()
        try:
            reply = local.chat(prompt.HEARTBEAT, f"# Память о Теме\n{MEMORY}\n\n# Сейчас\n{item}", prompt.HEARTBEAT_SCHEMA, max_tokens=300, purpose="замер")
            good = bool(reply.get("notify")) == want
        except (local.Unavailable, ValueError) as e:
            good, reply = False, str(e)
        spent.append(time.time() - started)
        ok += good
        if not good:
            misses.append(f"{item[:60]} -> {reply}")
    return ok, len(HEARTBEAT_CASES), round(sum(spent) / len(spent), 1), misses
