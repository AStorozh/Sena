"""Who does what: the home (core/local.py, models on this Mac) or the cloud (core/llm.py).
config.ROLES says where each switchable job works by default; the owner moves a role in settings ("roles"), and the nightly
check moves a learning job home once the home model gives the same answers as the cloud one (graduate). This module is also
the one door for those jobs: ask() — a short judgment in strict JSON or a short text, hear() — the owner's voice to text,
embed() — memory fingerprints, speak() — a voice. A cloud answer of a learning job is kept as an example in
data/training/<role>.jsonl: the home model is tested on these examples and can later be taught on them (dataset())."""
import json
import random
import re
import time

import numpy as np

from sena import config
from sena.core import cancel, events, llm, local, moment, settings, store, tools
from sena.core.tools import string, tool

HOME, CLOUD = config.HOME, config.CLOUD
MIN_EXAMPLES = 40   # a learning job is tested only with this many cloud examples on hand
EVAL_SIZE = 40      # the newest examples it is tested on
GRADUATE = 0.9      # how close to the cloud the home answers must be on average (score) to take the job over
CLOUD_PAUSE = 300   # after the cloud failed a narrow job, the next ones go straight home this long: no waiting on the cloud's retries each time
_cloud_down = 0.0   # until when the cloud is left alone

TITLES = {
    "embed": "Отпечатки памяти", "hearing": "Голос владельца в текст", "ears": "Разбор подслушанного",
    "heartbeat": "Проверка «есть ли важное»", "compact": "Сводка старой переписки", "harvest": "Факты из переписки", "router": "Выбор готового рецепта",
    "rules": "Время в правиле (что не разобрал код)", "feedback": "Несогласие Темы с ответом",
    "plain": "Простые слова: вопросы агентов, названия задач",
}
WHY = {
    "embed": "всё, что Сена помнит, включая чужую речь, — отпечатки считаются дома",
    "hearing": "голос владельца не покидает дом; если домашний слух сломался — облако подстрахует",
    "ears": "речь людей вокруг — чужая; разбирается только дома",
    "heartbeat": "чужие сообщения и почта читаются только дома",
    "compact": "переписка и так в контексте облачного мозга; переедет домой, когда домашняя модель догонит",
    "harvest": "те же слова Темы, что уже видел облачный мозг; переедет домой, когда домашняя модель догонит",
    "router": "текст задачи пишет облачный мозг; переедет домой, когда домашняя модель догонит",
    "rules": "код разбирает почти всё время сам; остаток — облаку, пока домашняя модель не догонит",
    "feedback": "те же слова Темы и ответ Сены, что уже видел облачный мозг; переедет домой, когда домашняя модель догонит",
    "plain": "слова Темы и агентов, что уже видело облако; дома — когда домашняя модель будет писать так же понятно",
}
FIXED = [   # jobs that don't move: (title, place, model, why)
    ("Мозг: понимает, решает, поручает, проверяет", CLOUD, config.MODEL_MAIN, "нужен самый большой ум; получает только то, что нужно для ответа"),
    ("Голосовые команды", CLOUD, config.MODEL_VOICE, "ответ почти сразу и без ошибок"),
    ("Агенты: браузер и компьютер", CLOUD, config.MODEL_FAST, "скриншоты, длинные страницы, много шагов"),
    ("Разбор задачи после работы (обучение)", CLOUD, config.MODEL_FAST, "учится на сделанном: качество важнее всего"),
    ("Ночная уборка памяти, характер, недельный разбор", CLOUD, config.MODEL_MAIN, "редко и требует ума"),
    ("Голос Сены", CLOUD, f"{config.MODEL_TTS} · {config.VOICE}", "один голос везде; без облака говорит домашний"),
    ("Живой разговор голосом", CLOUD, f"{config.MODEL_LIVE} · {config.VOICE}", "отвечает за секунду тем же голосом; думает за него мозг голосовых команд"),
    ("Картинки и видео", CLOUD, "Gemini", "дома такого качества на 16 ГБ не сделать"),
    ("Чтение документов и фото", HOME, "macOS Vision, PDFKit", "текст PDF, сканов и снимков читается на Mac"),
    ("Имя «Сена», голоса людей", HOME, "openWakeWord, resemblyzer", "всегда дома"),
]


# ---------- where ----------
def where(role):
    """HOME or CLOUD for the role right now: the owner's (or the nightly check's) choice, otherwise config.ROLES."""
    if role not in config.ROLES:
        raise ValueError(f"Нет такой роли: {role}")
    return (settings.get("roles") or {}).get(role) or config.ROLES[role]


def move(role, place, why=""):
    """Move a role home or to the cloud; recorded in settings and in the journal."""
    if role not in config.ROLES or place not in (HOME, CLOUD):
        return f"Роли: {', '.join(config.ROLES)}; место — {HOME} или {CLOUD}"
    if where(role) == place:
        return f"«{TITLES[role]}» и так {'дома' if place == HOME else 'в облаке'}"
    settings.update({"roles": {**(settings.get("roles") or {}), role: place}})
    events.log("update", f"роль «{TITLES[role]}» теперь {'дома' if place == HOME else 'в облаке'}" + (f": {why}" if why else ""), role=role, place=place)
    return f"«{TITLES[role]}» теперь {'дома' if place == HOME else 'в облаке'}"


def model_of(role, place=None):
    place = place or where(role)
    if role == "embed":
        return config.LOCAL_EMBED if place == HOME else config.MODEL_EMBED
    if role == "hearing":
        return (config.WHISPER_MODEL if config.WHISPER_MODEL.exists() else config.WHISPER_SMALL).name if place == HOME else config.MODEL_TRANSCRIBE
    return config.LOCAL_CHAT if place == HOME else config.MODEL_FAST


# ---------- narrow jobs ----------
def _json(text):
    """The JSON object in a reply: the whole text, or the first object that parses from a "{" — a model may wrap it in words or
    a fence, and a greedy pattern took two objects for one (DSPy parses the same way, by balanced decoding)."""
    text = text or ""
    try:
        return json.loads(text)
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            return decoder.raw_decode(text, m.start())[0]
        except ValueError:
            continue
    raise ValueError("ответ не JSON")


def _cloud(instructions, text, schema, purpose, effort="low"):
    kwargs = dict(purpose=purpose, model=config.MODEL_FAST, reasoning={"effort": effort}, instructions=instructions, input=text)
    if schema:
        kwargs["text"] = {"format": {"type": "json_schema", "name": "answer", "schema": schema, "strict": False}}
    answer = llm.respond(**kwargs).output_text
    if not schema:
        return answer.strip()
    try:
        data = _json(answer)
        wrong = tools.problems(data, schema)
    except ValueError:
        data, wrong = None, ["ответ не JSON"]
    if wrong:   # one more try with its own answer and what was wrong in it, as the system's note (pydantic-ai's retry feedback)
        kwargs["input"] = (f"{text}\n\n[Служебная проверка, не слова Темы: твой прошлый ответ — {answer[:1500]}\n"
                           f"Этот прошлый ответ не по схеме — {'; '.join(wrong[:6])}. Ответь заново, только JSON по схеме.]")
        try:
            data = _json(llm.respond(**kwargs).output_text)
            wrong = tools.problems(data, schema)
        except ValueError:
            wrong = ["ответ не JSON"]
        if wrong:   # a dict missing what the job needs breaks it further on, and would be kept as an example to learn from
            raise ValueError(f"ответ не по схеме и после повтора: {'; '.join(wrong[:4])}")
    return data


def ask(role, instructions, text, schema=None, image=None, purpose=None, max_tokens=800, effort="low"):
    """A narrow job's answer from wherever its role works: a dict when there is a schema, otherwise text.
    At home and failed: a private role raises (the job waits — other people's data never goes out as a fallback),
    any other role asks the cloud instead. In the cloud and failed (no internet, no money, the day's budget spent): the home
    model answers if it is on this Mac, so the job doesn't stop. A cloud answer of a learning role is kept as an example."""
    private = role in config.PRIVATE
    if where(role) == HOME:
        try:
            return local.chat(instructions, text, schema, image, max_tokens, purpose, private)
        except local.Unavailable as e:
            if private:
                raise
            events.error(f"Дома не вышло («{TITLES.get(role, role)}»), спрашиваю облако", e)
            return _keep(role, instructions, text, schema, _cloud(instructions, text, schema, purpose, effort), max_tokens)
    global _cloud_down
    home = local.installed()["chat"]
    if home and cloud_paused():
        return local.chat(instructions, text, schema, image, max_tokens, purpose, private)
    try:
        answer = _cloud(instructions, text, schema, purpose, effort)
    except cancel.Stopped:   # the owner stopped the task while Claude answered: not a broken cloud, and no home model for it
        raise
    except Exception as e:
        if not home:
            raise
        if llm.outage(e):   # the cloud is down for a while: the next jobs go home without waiting on it
            _cloud_down = time.time() + CLOUD_PAUSE
            events.error(f"Облако не ответило («{TITLES.get(role, role)}»): {llm.explain(e)}; {CLOUD_PAUSE // 60} мин отвечает домашняя модель", e)
        else:   # one broken answer or request: this one goes home, the cloud stays in use
            events.error(f"Облако не справилось с «{TITLES.get(role, role)}»: {llm.explain(e)}; этот раз отвечает домашняя модель", e)
        return local.chat(instructions, text, schema, image, max_tokens, purpose, private)
    return _keep(role, instructions, text, schema, answer, max_tokens)


def cloud_paused():
    """True for a while after the cloud failed a narrow job: the next ones are answered at home without asking it."""
    return time.time() < _cloud_down



def _keep(role, instructions, text, schema, answer, max_tokens=800):
    # a cloud answer of a learning role becomes an example the home model is tested and taught on; the test replays the same
    # limit the job really had (a summary asked with 900 tokens was tested with 800 and cut short)
    if role in config.LEARNING:
        store.append_jsonl(config.TRAINING_DIR / f"{role}.jsonl", {"t": moment.now(), "role": role, "model": config.MODEL_FAST, "max_tokens": max_tokens,
                                                                    "instructions": instructions, "input": text, "schema": schema, "output": answer})
    return answer


def hear(audio, filename="voice.ogg", purpose="голос в текст", cloud=None):
    """The owner's voice (Telegram, the app) -> text. At home first; if the home hearing fails, the cloud transcribes it:
    the owner is talking to Sena and must be understood. cloud — the cloud hearing (google/media.transcribe), handed in by
    the caller: this layer doesn't reach up to Google."""
    if where("hearing") == HOME or cloud is None:
        try:
            text = local.hear(audio, purpose)
            if text.strip() or cloud is None:
                return text
            events.log("transcribe", "дома слов не разобрано, отправляю в облако", purpose=purpose, bytes=len(audio))   # a quiet or long one: not lost
        except local.Unavailable as e:
            if cloud is None:
                raise
            events.error("Распознавание дома не вышло, отправляю в облако", e)
    return cloud(audio, filename, purpose)


def embed(texts, query=False, purpose=None):
    """Fingerprints from the model the "embed" role works with (vectors.py batches the texts)."""
    if where("embed") == HOME:
        return local.embed(texts, query, purpose)
    return [np.asarray(item.embedding, dtype=np.float32) for item in llm.embeddings(texts, purpose)]


def embed_stamp():
    """The model and width new fingerprints come from: the registry never compares vectors of two different models."""
    return local.stamp() if where("embed") == HOME else f"{config.MODEL_EMBED}/{config.EMBED_DIMS}"


LINK = re.compile(r"https?://\S+|www\.\S+")
SAY_LINK = re.compile(r"(продикт|прочита|произнес|назови|скажи|диктуй|spell|read).{0,30}(ссылк|адрес|url|link)", re.I | re.S)


def spoken(text, links=False):
    """A written answer as it is said aloud: markdown marks (headings, bullets, bold, code) are not read out as symbols, and
    a link is not read letter by letter — «ссылка в чате» instead (it is right there in the text). links — he asked to hear it."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)                        # [words](link) -> words
    if not links:
        text = LINK.sub("ссылка в чате", text)
    text = re.sub(r"^\s{0,3}(?:#{1,6}|[-*+•]|\d+[.)])\s+", "", text, flags=re.M)   # heading and list marks at a line's start
    text = re.sub(r"^\s*(?:---+|\*\*\*+)\s*$", "", text, flags=re.M)             # rules
    text = re.sub(r"[*_`~]{1,3}([^*_`~\n]+)[*_`~]{1,3}", r"\1", text)               # **bold**, _italic_, `code`
    return re.sub(r"(?<=[^.!?…:;,\s])\s*\n+", ". ", text.strip())                    # a line without a stop ends a sentence when said


def speak(text, voice, cloud=None, links=False):
    """Text -> mp3. Sena has one voice everywhere — the cloud one (cloud: google/media.speech, handed in by the caller); the home
    voice takes over only when the cloud doesn't answer (no internet, no money, Sena switched off): she never goes silent."""
    text = spoken(text, links)
    if voice == settings.HOME_VOICE or cloud is None:
        return local.speak(text)
    try:
        return cloud(text, voice)
    except Exception as e:
        events.error("Облачная озвучка не вышла, говорю домашним голосом", e)
        return local.speak(text)


# ---------- learning: examples, the test, moving home ----------
def examples(role):
    path = config.TRAINING_DIR / f"{role}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _norm(value):
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    if isinstance(value, str):
        return " ".join(value.lower().split())
    return value


DETAIL = re.compile(r"https?://\S+|/[\w./-]{3,}|\b[0-9a-f]{6,}\b|\d[\d.,:/-]*", re.I)   # what a summary must keep word for word
NEAR = 0.85   # two lines mean the same when their home fingerprints are this close


def _pairs(want, got):
    """Lines matched one to one by meaning (home fingerprints, the closest pairs first): (matched, missed, extra)."""
    if not want or not got:
        return 0, list(want), list(got)
    try:
        vecs = local.embed(list(want) + list(got), purpose="проверка домашней модели")
        a, b = vecs[:len(want)], vecs[len(want):]
        close = [(float(x @ y), i, j) for i, x in enumerate(a) for j, y in enumerate(b)]
    except local.Unavailable:   # no home fingerprints: two lines are the same only when they read the same
        close = [(float(_norm(x) == _norm(y)), i, j) for i, x in enumerate(want) for j, y in enumerate(got)]
    left, right, matched = set(range(len(want))), set(range(len(got))), 0
    for near, i, j in sorted(close, reverse=True):
        if near < NEAR:
            break
        if i in left and j in right:
            left.discard(i)
            right.discard(j)
            matched += 1
    return matched, [want[i] for i in sorted(left)], [got[j] for j in sorted(right)]


def _f1(want, got):
    """(0..1, what differs) for two sets of lines; both empty is full agreement: holding back is an answer too."""
    if not want and not got:
        return 1.0, "верно: записывать нечего"
    matched, missed, extra = _pairs(want, got)
    p, r = (matched / len(got) if got else 0.0), (matched / len(want) if want else 0.0)
    notes = ([f"пропущено: {'; '.join(missed)[:300]}"] if missed else []) + ([f"лишнее: {'; '.join(extra)[:300]}"] if extra else [])
    return (round(2 * p * r / (p + r), 3) if p + r else 0.0), "; ".join(notes) or "верно"


def _router(cloud, home):
    # the recipe exactly, then each parameter
    want, got = (cloud or {}).get("recipe"), (home or {}).get("recipe")
    if _norm(want) != _norm(got):   # the same recipe written in another case is the same recipe
        return 0.0, f"рецепт: нужен {want or 'null'}, выбран {got or 'null'}"
    a, b = (cloud or {}).get("args") or {}, (home or {}).get("args") or {}
    wrong = sorted(k for k in a.keys() | b.keys() if _norm(a.get(k)) != _norm(b.get(k)))
    return (1.0, "верно") if not wrong else (0.5, "параметры не те: " + ", ".join(wrong))


def _fact_line(f):
    return f"{f.get('section') or f.get('name') or ''}: {f.get('text') or f.get('fact') or ''}"


def _harvest(cloud, home):
    # the facts and the people as lines matched by meaning
    parts = [_f1([_fact_line(f) for f in (cloud or {}).get(k) or [] if isinstance(f, dict)],
                 [_fact_line(f) for f in (home or {}).get(k) or [] if isinstance(f, dict)]) for k in ("facts", "people")]
    return round(sum(s for s, _ in parts) / 2, 3), "; ".join(f"{k}: {fb}" for k, (_, fb) in zip(("факты", "люди"), parts))


def _summary_lines(text):
    return [line.strip(" -•") for line in str(text).splitlines() if len(line.split()) >= 3]


def _compact(cloud, home):
    # the summary's lines by meaning, and the details it must keep word for word
    meaning, feedback = _f1(_summary_lines(cloud), _summary_lines(home))
    need = set(DETAIL.findall(str(cloud)))
    lost = sorted(need - set(DETAIL.findall(str(home))))
    kept = 1 - len(lost) / len(need) if need else 1.0
    return round((meaning + kept) / 2, 3), feedback + (f"; потеряны детали: {', '.join(lost[:10])}" if lost else "")


def _feedback(cloud, home):
    # the verdict alone: the reason is free words and never reads the same twice
    want, got = bool((cloud or {}).get("disagrees")), bool((home or {}).get("disagrees"))
    return (1.0, "верно") if want == got else (0.0, "несогласие пропущено" if want else "несогласие придумано")


def _exact(cloud, home):
    return (1.0, "верно") if _norm(cloud) == _norm(home) else (0.0, "не совпало")


def _rules(cloud, home):
    # the same jobs at the same times, whatever the wording of their texts
    if not (isinstance(cloud, dict) and isinstance(home, dict)):
        return _exact(cloud, home)
    pick = lambda a: sorted((j.get("at"), j.get("repeat")) for j in a.get("jobs", []) if isinstance(j, dict))   # noqa: E731
    return (1.0, "верно") if pick(cloud) == pick(home) else (0.0, "не совпало")


SCORERS = {"router": _router, "harvest": _harvest, "compact": _compact, "feedback": _feedback, "rules": _rules}


def score(role, cloud, home):
    """How close the home answer is to the cloud's (0..1) and what differs, in words a later reflection can use. Field by field
    with partial credit (DSPy's metric with feedback), not all or nothing: facts in free text almost never match word for word,
    so with an exact check a job like harvest could never move home however good the home model got. Each learning role has its
    own scorer; anything else must be the same answer."""
    if home is None:
        return 0.0, "ответа нет или он не по схеме"
    return SCORERS.get(role, _exact)(cloud, home)


def evaluate(role, size=EVAL_SIZE):
    """The home model on the newest cloud examples of a learning role: {"examples", "tested", "agree", "ready", "misses"}.
    agree — the mean closeness (score), misses — what differed where it fell short, for a later look."""
    rows = examples(role)
    report = {"role": role, "examples": len(rows), "tested": 0, "agree": None, "ready": False, "misses": []}
    if len(rows) < MIN_EXAMPLES:
        return report
    test = rows[-size:]
    total = 0.0
    for r in test:
        try:
            mine = local.chat(r["instructions"], r["input"], r.get("schema"), max_tokens=r.get("max_tokens", 800), purpose="проверка домашней модели")
        except (local.Unavailable, ValueError):
            mine = None
        value, feedback = score(role, r["output"], mine)
        total += value
        if value < 1 and len(report["misses"]) < 5:
            report["misses"].append(feedback[:300])
    report.update(tested=len(test), agree=round(total / len(test), 3), ready=total / len(test) >= GRADUATE)
    return report


def graduate():
    """Nightly: every learning role still in the cloud is tested on its examples; one that matches moves home.
    Returns the reports; quiet when there's nothing to test."""
    if not local.installed()["chat"]:
        return []
    out = []
    for role in config.LEARNING:
        if where(role) != CLOUD:
            continue
        report = evaluate(role)
        if report["tested"]:
            events.log("review", f"домашняя модель на «{TITLES[role]}»: близость к облаку {report['agree']:.0%} на {report['tested']} примерах",
                       role=role, agree=report["agree"], tested=report["tested"])
        if report["ready"]:
            report["moved"] = move(role, HOME, f"домашняя модель близка к облаку на {report['agree']:.0%}")
        out.append(report)
    return out


def dataset(role, valid_share=0.1):
    """Examples as a chat dataset for teaching the home model (mlx-lm LoRA): data/training/<role>/train.jsonl and valid.jsonl."""
    rows = examples(role)
    if not rows:
        return None
    chats = [{"messages": [{"role": "system", "content": r["instructions"]}, {"role": "user", "content": r["input"]},
                           {"role": "assistant", "content": json.dumps(r["output"], ensure_ascii=False) if isinstance(r["output"], (dict, list)) else r["output"]}]}
             for r in rows]
    random.Random(7).shuffle(chats)
    cut = max(1, int(len(chats) * valid_share)) if len(chats) > 1 else 0
    folder = config.TRAINING_DIR / role
    folder.mkdir(parents=True, exist_ok=True)
    for name, part in (("valid.jsonl", chats[:cut]), ("train.jsonl", chats[cut:])):
        store.write_text(folder / name, "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in part))
    return {"folder": str(folder), "train": len(chats) - cut, "valid": cut}


TOOLS = [tool("set_role", "Тема попросил навсегда закрепить один из переключаемых навыков Сены дома или в облаке (не только сейчас — всегда), "
                          "или наоборот отпустить его обратно к обычному выбору. Навыки: " + "; ".join(f"{k} — {TITLES[k]}" for k in config.ROLES) + ". "
                          "Разовое «сделай это сейчас дома» — не сюда.",
              {"role": {**string("навык"), "enum": list(config.ROLES)}, "place": {**string("где: home или cloud"), "enum": [HOME, CLOUD]}},
              ["role", "place"])]
FUNCTIONS = {"set_role": move}


# ---------- for the screen ----------
def table():
    """Who does what, for the console and the apps: the switchable roles first, then the fixed jobs."""
    rows = [{"role": r, "title": TITLES[r], "place": where(r), "default": config.ROLES[r], "model": model_of(r), "why": WHY[r],
             "switchable": True, "learning": r in config.LEARNING, "examples": len(examples(r)) if r in config.LEARNING else None}
            for r in config.ROLES]
    rows += [{"role": None, "title": t, "place": p, "default": p, "model": m, "why": w, "switchable": False, "learning": False, "examples": None}
             for t, p, m, w in FIXED]
    return rows
