"""Russian time expressions in the owner's rules -> scheduler jobs, by code. "каждый день в 8", "по будням в 7:30",
"по понедельникам и четвергам вечером", "по выходным в 10 утра", "завтра в 9", "25 октября в 18:00", "через 2 часа".
parse() answers one of three ways: None — there is no time in the text at all (a rule about how to do things: no schedule,
no model); a list of jobs — the time is clear; an empty list — there is time in the text but the code isn't sure what it means
("утром присылай погоду", "если до 10 не отвечу — звони"): then the planner's model decides (memory/rules.py, role "rules").
Vague parts of the day follow the planner's own convention: morning 9:00, day 13:00, evening 20:00; vague night is never planned.
Times are local wall-clock times, as the scheduler keeps them (scheduler.FORMAT)."""
import re
from datetime import datetime, timedelta

FORMAT = "%d.%m.%Y %H:%M"
MAX_JOBS = 6

DAYS = {"понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3, "пятниц": 4, "суббот": 5, "воскресен": 6}
MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7, "август": 8, "сентябр": 9,
          "октябр": 10, "ноябр": 11, "декабр": 12}
WORD_HOURS = {"час": 1, "один": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8,
              "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12}
COUNTS = {**{k: v for k, v in WORD_HOURS.items() if k != "час"}, "одну": 1, "одна": 1, "пару": 2}   # "через две недели", "через пару часов"
# vague parts of the day in their own forms (утро, утром, по утрам, утрами, утречком, наутро, утренний; вечер, вечером,
# вечерами, вечерком, вечерний): a stem alone was found inside other words — «внутри» read as morning, «вечеринку» as evening —
# and a form left out made a rule "без расписания" for good. Night is left to the model
MORNING = r"\b(?:на|по)?утр(?:о|ом|а|у|ам|ами|енн\w*|ечк\w*)\b"
EVENING = r"\b(?:под)?вечер(?:ом|а|у|ам|ами|ок|к\w*|н\w*)?\b"
PARTS = ((MORNING, 9), (r"\bдн[её]м\b", 13), (EVENING, 20))
MONTH_WORD = r"\b(?:январ|феврал|март|апрел|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*|\bма[йяею]\b"   # not «смартфон», «майка»
UNIT = r"(минут\w*|мин\b|час\w*|ч\b|сут\w*|дн\w*|день|недел\w*)"   # what "через N …" counts in, by code; a month or a year — the model

_DAY = r"(понедельник|вторник|сред|четверг|пятниц|суббот|воскресен)\w*"
WEEKDAY_WORD = r"\b(?:понедельник|вторник|четверг|пятниц|суббот|воскресен)\w*|\bсред(?:а|у|ы|е|ам|ами|ах)\b"   # not «среди», «средство»
# None from parse() means "no time at all": no schedule and no model, for good. So the test errs towards "there is time": a word
# wrongly taken for time costs one narrow call of the planner, a time it misses loses the owner's reminder silently
TIMEISH = re.compile(r"\d{1,2}[:.]\d{2}|\b[вк]\s*\d{1,2}\b(?!\s*(минут|мин\b|раз|числ|%|процент))|\d{1,2}\s*(?:час|ч\b)|" + MORNING + "|" + EVENING
                     + r"|\bдн[её]м\b|\bноч(?:ь\w*|и|ам|ами|н\w*)\b|полдень|полночь|\bобед\w*|\b(?:после)?завтра(?:шн\w*)?\b|сегодня|кажд"
                     + r"|\bеже(?:дневн|недельн|месячн|годн)|будн|выходн|\bнедел\w*|\bмесяц\w*|" + WEEKDAY_WORD
                     + r"|\bчерез\s+(?:[\w-]+\s+)?(?:\d|минут|мин\b|секунд|час|сут|день|дня|дней|недел|месяц|год|лет|пол)"
                     + r"|\d{1,2}\.\d{1,2}\b|\d\s*(?:-?го\s+)?числ|половин|четверт|\bв\s+(" + "|".join(WORD_HOURS) + r")\b|" + MONTH_WORD + r"|раз в", re.I)
CONDITIONAL = re.compile(r"\b(если|когда|как только|после того|пока|в случае)\b", re.I)


def _clock(text):
    """Explicit clock times in the text: [(hour, minute)], in the order they appear."""
    found = []
    # "7:30" is always a time; "7.30" only after "в"/"к" — without it "1.11" is the first of November
    for m in re.finditer(r"(?:\b(в|к)\s*)?(\d{1,2})(:|\.)(\d{2})\b\s*(утра|дня|вечера|ночи)?", text):
        if m.group(3) == "." and not m.group(1) and not m.group(5):
            continue
        found.append((m.start(), _shift(int(m.group(2)), int(m.group(4)), m.group(5))))
    taken = [m.span() for m in re.finditer(r"\d{1,2}[:.]\d{2}", text)]
    for m in re.finditer(r"\b(?:в|к)\s*(\d{1,2})\b(?![:.]\d)\s*(часов|часа|час|ч\b)?\s*(утра|дня|вечера|ночи)?", text):
        if any(a <= m.start(1) < b for a, b in taken):
            continue
        after = text[m.end():m.end() + 12]
        if re.match(r"\s*(минут|мин\b|числ|-?го\b|январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр|раз)", after):
            continue   # "в 5 минут", "в 25 числа", "к 3 марта": not an hour
        found.append((m.start(), _shift(int(m.group(1)), 0, m.group(3))))
    for m in re.finditer(r"\b(?:в|к)\s+(" + "|".join(WORD_HOURS) + r")\b\s*(часов|часа|час)?\s*(утра|дня|вечера|ночи)?", text):
        found.append((m.start(), _shift(WORD_HOURS[m.group(1)], 0, m.group(3))))
    if re.search(r"\bв\s+полдень\b", text):
        found.append((text.index("полдень"), (12, 0)))
    if re.search(r"\bв\s+полночь\b", text):
        found.append((text.index("полночь"), (0, 0)))
    out = []
    for _, t in sorted(found):
        if t and t not in out:
            out.append(t)
    return out


def _shift(hour, minute, part):
    # "в 8 вечера" — 20:00, "в 3 дня" — 15:00, "в 12 ночи" — 0:00; an impossible time is no time
    if part in ("вечера", "дня") and hour < 12:
        hour += 12
    if part == "ночи" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _vague(text):
    # a part of the day said in words: "каждое утро", "по вечерам", "днём"
    for pattern, hour in PARTS:
        if re.search(pattern, text):
            return [(hour, 0)]
    return []


def _weekdays(text):
    # "каждый понедельник", "по средам и пятницам", "каждую субботу" -> the days, Monday = 0
    days = []
    if re.search(r"(кажд\w*|по)\s+" + _DAY, text) or re.search(r"по\s+\w+\s+и\s+" + _DAY, text):
        for stem, n in DAYS.items():
            if re.search(r"\b" + stem, text) and n not in days:
                days.append(n)
    return days


def _date(text, now):
    # an absolute day: today, tomorrow, the day after, "25 октября", "25.10", "25.10.2027", "в пятницу" (the next one)
    if re.search(r"\bпослезавтра\b", text):
        return (now + timedelta(days=2)).date()
    if re.search(r"\bзавтра\b", text):
        return (now + timedelta(days=1)).date()
    if re.search(r"\bсегодня\b", text):
        return now.date()
    m = re.search(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")\w*(?:\s+(\d{4}))?", text)
    if m:
        return _day(int(m.group(3) or now.year), MONTHS[m.group(2)], int(m.group(1)), now, bool(m.group(3)))
    m = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b(?![:.]\d)", text)
    if m and int(m.group(2)) <= 12:
        return _day(int(m.group(3) or now.year), int(m.group(2)), int(m.group(1)), now, bool(m.group(3)))
    m = re.search(r"\bв(?:о)?\s+" + _DAY, text)
    if m and not re.search(r"кажд\w*\s+" + _DAY, text):
        n = next(v for k, v in DAYS.items() if m.group(1).startswith(k))
        ahead = (n - now.weekday()) % 7 or 7
        return (now + timedelta(days=ahead)).date()
    return None


def _day(year, month, day, now, year_given):
    try:
        d = datetime(year, month, day).date()
    except ValueError:
        return None
    if not year_given and d < now.date():   # "25 октября" in November means next year's
        try:
            d = datetime(year + 1, month, day).date()
        except ValueError:
            return None
    return d


def _relative(text, now):
    """"через 2 часа", "через неделю", "через пару дней" -> (the moment, whether it counts whole days); None when there is no
    such phrase or its size isn't sure: "через день" also means "every other day", and a month has no fixed length."""
    m = re.search(r"через\s+(?:(\d+|" + "|".join(sorted(COUNTS, key=len, reverse=True)) + r")\s*)?" + UNIT, text)
    if not m:
        return None
    count, unit = m.group(1), m.group(2)
    if count is None and unit.startswith(("дн", "день")):
        return None
    n = int(count) if count and count.isdigit() else COUNTS.get(count, 1)
    if unit.startswith("мин"):
        return now + timedelta(minutes=n), False
    if unit.startswith(("час", "ч")):
        return now + timedelta(hours=n), False
    return now + timedelta(days=n * (7 if unit.startswith("недел") else 1)), True


_SAID = [   # the time phrases parse() understands, to be cut out of the job's own text
    r"\b(кажд\w*|по)\s+(будн\w*|выходн\w*|рабочим\s+дням|утрам|вечерам|день|утро|вечер|сутки|" + _DAY + r")(\s+и\s+" + _DAY + r")*",
    r"\bежедневн\w*|\bв\s+(будни|выходные|рабочие\s+дни)\b",
    r"\b(в|к)\s*\d{1,2}([:.]\d{2})?\s*(часов|часа|час)?\s*(утра|дня|вечера|ночи)?(?=\W|$)",
    r"\b(в|к)\s+(" + "|".join(WORD_HOURS) + r")\s*(часов|часа|час)?\s*(утра|дня|вечера|ночи)?(?=\W|$)",
    r"\bв\s+(полдень|полночь)\b", r"\b(завтра|послезавтра|сегодня)\b",
    r"\bчерез\s+(?:(?:\d+|" + "|".join(sorted(COUNTS, key=len, reverse=True)) + r")\s*)?" + UNIT,
    r"\b\d{1,2}\s+(" + "|".join(MONTHS) + r")\w*(\s+\d{4})?", r"\b\d{1,2}\.\d{1,2}(\.\d{4})?\b",
    r"\bв(о)?\s+" + _DAY, r"\b(утром|днём|днем|вечером|утрами|вечерами|утречком|вечерком|наутро|поутру)\b",
]


def rest(text):
    """The rule without its time: "завтра в 9 напомни позвонить Игорю" -> "напомни позвонить Игорю" (the job's own text:
    a reminder then goes out without the model when it fires)."""
    out = text
    for pattern in _SAID:
        out = re.sub(pattern, " ", out, flags=re.I)
    out = re.sub(r"(^|\s)и(\s+и)*(?=\s|$)", " ", out)   # "в 8 и в 20" leaves a lonely "и"
    out = " ".join(out.split()).strip(" ,.;—-")
    return out[:1].upper() + out[1:] if len(out) >= 3 else text


def parse(text, now=None):
    """None — no time in the text; [] — time the code can't read for sure (the model decides); otherwise
    [{"at": "ДД.ММ.ГГГГ ЧЧ:ММ", "repeat": "once" | "daily" | "weekdays" | "weekly"}]."""
    now = now or datetime.now()
    t = " ".join(text.lower().replace("ё", "е").split())
    if not TIMEISH.search(t):
        return None
    if CONDITIONAL.search(t):
        return []   # "если до 10 не отвечу — звони": a condition, not a timetable
    if re.search(r"половин|без\s+(четверти|пяти|десяти|двадцати|пятнадцати)|четверт|полчаса|ноч", t) and not re.search(r"в\s+\d{1,2}\s*(часов|часа|час)?\s*ночи", t):
        return []   # "в половине девятого", "без четверти", vague night: the model decides
    later = _relative(t, now)
    if later:
        at, whole = later
        hours = (_clock(t) or _vague(t)) if whole else []   # "через 2 дня в 10" is that day at 10, not at this minute
        jobs = [at.replace(hour=h, minute=m, second=0, microsecond=0) for h, m in hours] or [at]
        if any(j <= now for j in jobs) or len(jobs) > MAX_JOBS:
            return []
        return [{"at": j.strftime(FORMAT), "repeat": "once"} for j in sorted(jobs)]
    times = _clock(t) or _vague(t)
    days = _weekdays(t)
    if re.search(r"по\s+выходн|в\s+выходн|кажд\w*\s+выходн", t):
        days = [5, 6]
    if re.search(r"по\s+будн|в\s+будн|кажд\w*\s+будн|в\s+рабочие\s+дни|по\s+рабочим", t):
        repeat, days = "weekdays", []
    elif days:
        repeat = "weekly"
    elif re.search(r"кажд\w*\s+(день|утро|вечер|сутки)|ежедневн|по\s+(утрам|вечерам)|\b(утрами|вечерами)\b|раз\s+в\s+день|каждый\s+божий", t):
        repeat = "daily"
    else:
        repeat = "once"
    if not times:
        return []   # a day without an hour ("каждый понедельник присылай отчёт"): the model picks a sensible hour
    if repeat == "once":
        day = _date(t, now)
        if day is None:
            return []   # an hour without a day and without "every": a reminder or a habit — the model decides
        jobs = [datetime(day.year, day.month, day.day, h, m) for h, m in times]
        if any(j <= now for j in jobs):
            return []   # already past ("сегодня в 8" said at 10): don't guess
    elif repeat == "weekly":
        jobs = []
        for d in days:
            ahead = (d - now.weekday()) % 7
            for h, m in times:
                when = (now + timedelta(days=ahead)).replace(hour=h, minute=m, second=0, microsecond=0)
                jobs.append(when if when > now else when + timedelta(days=7))
    else:
        jobs = [now.replace(hour=h, minute=m, second=0, microsecond=0) for h, m in times]
        jobs = [j if j > now else j + timedelta(days=1) for j in jobs]
        if repeat == "weekdays":
            jobs = [j + timedelta(days=(7 - j.weekday()) if j.weekday() >= 5 else 0) for j in jobs]
    if not jobs or len(jobs) > MAX_JOBS:
        return []
    return [{"at": j.strftime(FORMAT), "repeat": repeat} for j in sorted(jobs)]
