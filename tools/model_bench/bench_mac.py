"""The computer agent (ag_mac) on terminal tasks in a sandbox: python bench_mac.py <model> <out.json> [task ids...].
The real agent and prompt; its commands run under sandbox-exec — writing only inside the task's folder, the home folder
unreadable, no network — and "the screen" is a prepared picture, so the owner's real screen never reaches a model.
A task is graded by the files left behind and by the facts in the answer."""
import configparser
import io
import os
import subprocess
import sys
import time
from pathlib import Path

import harness

model, out, only = sys.argv[1], sys.argv[2], set(sys.argv[3:])
harness.setup(model, "fast")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from sena.agents import ag_mac  # noqa: E402
from sena.core import cancel, status  # noqa: E402
from sena.core.safety import clean_env, cut, forbidden_command  # noqa: E402
from sena.core.tools import image  # noqa: E402

BOX = Path(os.environ["SENA_DATA"]) / "box"
BOX.mkdir(exist_ok=True)
PROFILE = ("(version 1)(allow default)"
           "(deny file-write* (subpath \"/Users\") (subpath \"/Applications\") (subpath \"/Library\") (subpath \"/opt\") (subpath \"/usr/local\")"
           " (subpath \"/private/tmp\") (subpath \"/private/var\"))"
           f"(deny file-read* (subpath \"{Path.home()}\"))(deny network-outbound (remote ip \"*:*\"))"
           f"(allow file-write* (subpath \"{BOX}\") (literal \"/dev/null\") (subpath \"/private/var/folders\"))(allow file-read* (subpath \"{BOX}\"))")
blocked = []


def sandboxed(command):
    cancel.check_stop()
    print("Команда:", command, flush=True)
    refusal = forbidden_command(command)
    if refusal:
        blocked.append(command)
        return refusal
    env = {**clean_env(), "HOME": str(BOX)}
    try:
        done = subprocess.run(["sandbox-exec", "-p", PROFILE, "/bin/zsh", "-f", "-c", command], cwd=BOX, env=env,
                              capture_output=True, text=True, timeout=120)
        output = (done.stdout + done.stderr).strip() or "(пустой вывод)"
        output += f"\n[код возврата {done.returncode}]"
    except subprocess.TimeoutExpired:
        output = "Команда не завершилась за 2 минуты"
    return cut(output)


def screen():
    shot = Image.new("RGB", (1440, 900), (236, 236, 240))
    d = ImageDraw.Draw(shot)
    try:
        font, small = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 22), ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 17)
    except OSError:
        font = small = ImageFont.load_default()
    d.rectangle((0, 0, 1440, 26), fill=(250, 250, 250))
    d.text((14, 4), "Finder   Файл   Правка   Вид   Окно   Справка", fill=(20, 20, 20), font=small)
    d.rounded_rectangle((420, 260, 1020, 560), 14, fill=(255, 255, 255), outline=(190, 190, 190))
    d.text((450, 290), "Установщик обновлений", fill=(20, 20, 20), font=font)
    d.text((450, 345), "Не удалось установить обновление macOS 27.1.", fill=(40, 40, 40), font=small)
    d.text((450, 375), "Код ошибки: 0x8F21. Попробуйте позже.", fill=(40, 40, 40), font=small)
    d.rounded_rectangle((870, 500, 990, 536), 8, fill=(30, 110, 230))
    d.text((902, 507), "Закрыть", fill=(255, 255, 255), font=small)
    buffer = io.BytesIO()
    shot.save(buffer, "JPEG", quality=85)
    return [image(buffer.getvalue())]


ag_mac.run_command, ag_mac.look = sandboxed, screen


def prepare_sort():
    inbox = BOX / "inbox"
    inbox.mkdir()
    for name in ("отпуск.jpg", "скан.png", "договор.pdf", "резюме.docx", "заметки.txt", "песня.mp3", "IMG_0412.JPG", "счёт.pdf", "архив.zip", "схема.png"):
        (inbox / name).write_bytes(b"x")


def grade_sort(answer):
    want = {"images": {"отпуск.jpg", "скан.png", "IMG_0412.JPG", "схема.png"}, "docs": {"договор.pdf", "резюме.docx", "счёт.pdf"},
            "other": {"заметки.txt", "песня.mp3", "архив.zip"}}
    got = {k: {p.name for p in (BOX / "sorted" / k).glob("*")} for k in want}
    left = [p.name for p in (BOX / "inbox").glob("*")]
    return got == want and not left, f"разложено {got}, осталось {left}"


EXPENSES = [("2026-09-28", "Еда", 1240.0), ("2026-10-01", "Еда", 2350.5), ("2026-10-03", "Транспорт", 600.0), ("2026-10-07", "Еда", 1899.0),
            ("2026-10-12", "Развлечения", 3200.0), ("2026-10-15", "Еда", 4120.25), ("2026-10-21", "Еда", 980.0), ("2026-10-30", "Транспорт", 450.0),
            ("2026-11-01", "Еда", 1500.0), ("2026-10-31", "Еда", 760.75)]


def prepare_expenses():
    (BOX / "expenses.csv").write_text("дата;категория;сумма\n" + "".join(f"{d};{c};{str(a).replace('.', ',')}\n" for d, c, a in EXPENSES), encoding="utf-8")


def grade_expenses(answer):
    want = sum(a for d, c, a in EXPENSES if c == "Еда" and d.startswith("2026-10"))
    text = (BOX / "answer.txt").read_text(encoding="utf-8").strip().replace(" ", "").replace(",", ".") if (BOX / "answer.txt").exists() else ""
    try:
        return abs(float(text) - want) < 0.01, f"в answer.txt «{text}», ждали {want}"
    except ValueError:
        return False, f"в answer.txt «{text}», ждали {want}"


CONFIG = "[server]\nhost = 0.0.0.0\nport = 8000\ndebug = false\nworkers = 4\n\n[database]\nurl = postgres://localhost/app\npool = 10\n\n[logging]\nlevel = info\n"


def prepare_config():
    (BOX / "app").mkdir()
    (BOX / "app" / "config.ini").write_text(CONFIG, encoding="utf-8")


def grade_config(answer):
    got, want = configparser.ConfigParser(), configparser.ConfigParser()
    try:
        got.read_string((BOX / "app" / "config.ini").read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"файл испорчен: {e}"
    want.read_string(CONFIG)
    want["server"]["port"], want["server"]["debug"] = "8080", "true"
    same = {s: dict(got[s]) for s in got.sections()} == {s: dict(want[s]) for s in want.sections()}
    return same, f"получилось {({s: dict(got[s]) for s in got.sections()})}"


FILES = {"billing/totals.py": "def calculate_total(items, tax=0.2):\n    return sum(i['price'] * i['qty'] for i in items) * (1 + tax)\n",
         "billing/invoice.py": "from billing.totals import calculate_total\n\ndef invoice(order):\n    total = calculate_total(order['items'])\n    return {'total': total}\n",
         "shop/cart.py": "from billing.totals import calculate_total\n\nclass Cart:\n    def total(self):\n        return calculate_total(self.items)\n\n    def total_no_tax(self):\n        return calculate_total(self.items, tax=0)\n",
         "shop/report.py": "from billing import totals\n\ndef day(orders):\n    return [totals.calculate_total(o['items']) for o in orders]\n",
         "tests/test_totals.py": "from billing.totals import calculate_total\n\ndef test_empty():\n    assert calculate_total([]) == 0\n",
         "README.md": "Функция calculate_total считает сумму заказа с налогом.\n"}


def prepare_search():
    for name, text in FILES.items():
        path = BOX / "project" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def grade_search(answer):
    import re
    return "totals.py" in answer and bool(re.search(r"\b5\b|пять", answer)), "файл или число вызовов не те (ждали billing/totals.py и 5)"


def prepare_log():
    import random
    from datetime import datetime, timedelta
    rnd = random.Random(7)
    start, lines = datetime(2026, 10, 24, 9, 0, 0), []
    ips = [f"10.0.0.{n}" for n in (12, 31, 45, 77, 90)]
    for i in range(900):
        t = start + timedelta(seconds=i * 12)            # three hours of journal
        late = t >= start + timedelta(hours=2)
        weights = [1, 8, 2, 1, 1] if not late else [1, 2, 2, 6, 1]   # .31 fails most overall, .77 in the last hour
        ip = rnd.choices(ips, weights)[0]
        code = 401 if rnd.random() < 0.55 else 200
        lines.append(f"{t:%Y-%m-%d %H:%M:%S} {ip} POST /login {code}")
    (BOX / "auth.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _log_gold():
    """The right answers: the window's edge falls exactly on a record, so both «after» and «from» the edge are accepted."""
    from datetime import datetime, timedelta
    rows = [l.split() for l in (BOX / "auth.log").read_text().splitlines()]
    last = datetime.fromisoformat(rows[-1][0] + " " + rows[-1][1])
    answers = []
    for inclusive in (False, True):
        counts = {}
        for r in rows:
            t = datetime.fromisoformat(r[0] + " " + r[1])
            if r[5] == "401" and (t >= last - timedelta(hours=1) if inclusive else t > last - timedelta(hours=1)):
                counts[r[2]] = counts.get(r[2], 0) + 1
        ip = max(counts, key=counts.get)
        answers.append([ip, str(counts[ip])])
    return answers


def grade_log(answer):
    right = _log_gold()
    text = (BOX / "answer2.txt").read_text(encoding="utf-8").split() if (BOX / "answer2.txt").exists() else []
    return text[:2] in right, f"в answer2.txt {text}, ждали одно из {right}"


def _git(*args, author="Тема Сторож <tema@example.com>"):
    name, mail = author.split(" <")
    subprocess.run(["git", "-c", f"user.name={name}", "-c", f"user.email={mail.rstrip('>')}", *args], cwd=BOX / "repo", check=True, capture_output=True)


def prepare_git():
    repo = BOX / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "prices.py").write_text("def price(x):\n    return x\n\ndef legacy_discount(x):\n    return x * 0.9\n", encoding="utf-8")
    _git("add", "."), _git("commit", "-qm", "Цены и старая скидка")
    (repo / "prices.py").write_text("def price(x):\n    return round(x, 2)\n\ndef legacy_discount(x):\n    return x * 0.9\n", encoding="utf-8")
    _git("commit", "-qam", "Округление цен", author="Игорь Смирнов <igor@example.com>")
    (repo / "prices.py").write_text("def price(x):\n    return round(x, 2)\n", encoding="utf-8")
    _git("commit", "-qam", "Чистка кода", author="Ольга Петрова <olga@example.com>")
    (repo / "README.md").write_text("Сервис цен\n", encoding="utf-8")
    _git("add", "."), _git("commit", "-qm", "README", author="Игорь Смирнов <igor@example.com>")


def grade_git(answer):
    done = subprocess.run(["git", "log", "--format=%H %an", "-S", "def legacy_discount"], cwd=BOX / "repo", capture_output=True, text=True)
    removed = done.stdout.splitlines()[0].split()[0]   # newest first: the commit that took it out
    return removed[:7] in answer and "Ольг" in answer, f"ждали {removed[:7]} и Ольгу Петрову"


def prepare_dupes():
    import random
    rnd = random.Random(3)
    blobs = [bytes(rnd.getrandbits(8) for _ in range(256)) for _ in range(6)]
    layout = {"2025/море.jpg": 0, "2025/море (1).jpg": 0, "2025/горы.jpg": 1, "2026/горы-копия.jpg": 1, "2026/кот.jpg": 2, "2026/дом.jpg": 3,
              "разное/кот 2.jpg": 2, "разное/лес.jpg": 4, "разное/река.jpg": 5, "разное/море-последнее.jpg": 0}
    for name, blob in layout.items():
        path = BOX / "photos" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blobs[blob])


def grade_dupes(answer):
    import hashlib
    kept = [hashlib.md5(p.read_bytes()).hexdigest() for p in (BOX / "photos").rglob("*") if p.is_file()]
    moved = [p for p in (BOX / "duplicates").rglob("*") if p.is_file()] if (BOX / "duplicates").exists() else []
    return len(kept) == 6 and len(set(kept)) == 6 and len(moved) == 4, f"в photos {len(kept)} файлов ({len(set(kept))} разных), в duplicates {len(moved)}"


TEST_CALC = """import unittest
from calc import total


class Total(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(total([100, 50]), 150)

    def test_discount(self):
        self.assertEqual(total([100, 100], discount=0.1), 180)

    def test_coupon_and_discount(self):
        self.assertEqual(total([200], discount=0.1, coupon=20), 160)
"""
CALC = """def total(prices, discount=0.0, coupon=0):
    s = sum(prices)
    s = s - s * discount
    if coupon:
        s = s - coupon
    s = s - s * discount
    return round(s, 2)
"""


def prepare_fix():
    (BOX / "shop").mkdir()
    (BOX / "shop" / "calc.py").write_text(CALC, encoding="utf-8")
    (BOX / "shop" / "test_calc.py").write_text(TEST_CALC, encoding="utf-8")


def grade_fix(answer):
    if (BOX / "shop" / "test_calc.py").read_text(encoding="utf-8") != TEST_CALC:
        return False, "тесты изменены"
    done = subprocess.run([sys.executable, "-m", "unittest", "-q"], cwd=BOX / "shop", capture_output=True, text=True)
    return done.returncode == 0, "тесты всё ещё падают: " + (done.stderr or done.stdout)[-200:]


def prepare_report():
    import random
    rnd = random.Random(11)
    cities = ["Москва", "Казань", "Пермь", "Самара", "Уфа"]
    customers = [(i, f"Клиент {i}", cities[i % 5]) for i in range(1, 21)]
    orders = [(n, rnd.randint(1, 20), rnd.randint(5, 300) * 10) for n in range(1, 81)]
    (BOX / "customers.csv").write_text("customer_id,name,city\n" + "".join(f"{i},{n},{c}\n" for i, n, c in customers), encoding="utf-8")
    (BOX / "orders.csv").write_text("order_id,customer_id,amount\n" + "".join(f"{o},{c},{a}\n" for o, c, a in orders), encoding="utf-8")


def grade_report(answer):
    import csv
    city = {r["customer_id"]: r["city"] for r in csv.DictReader(open(BOX / "customers.csv", encoding="utf-8"))}
    sums = {}
    for r in csv.DictReader(open(BOX / "orders.csv", encoding="utf-8")):
        sums[city[r["customer_id"]]] = sums.get(city[r["customer_id"]], 0) + int(r["amount"])
    want = sorted(sums.items(), key=lambda kv: -kv[1])
    path = BOX / "report.csv"
    if not path.exists():
        return False, "нет report.csv"
    rows = list(csv.reader(open(path, encoding="utf-8")))
    try:
        got = [(r[0].strip(), int(float(r[1]))) for r in rows[1:] if r]
    except (ValueError, IndexError):
        return False, f"report.csv не читается: {rows[:3]}"
    return [c.strip().lower() for c in rows[0][:2]] == ["city", "total"] and got == want, f"получили {rows[:7]}, ждали {want}"


NOTES = {"notes/план.md": "# План", "notes/работа/задачи.md": "- сделать", "notes/работа/отчёт.pdf": "pdf", "notes/личное/фото.jpg": "jpg",
         "notes/личное/дневник.md": "день", "notes/черновик.txt": "текст", "notes/работа/2026/итоги.md": "итоги"}


def prepare_zip():
    for name, text in NOTES.items():
        path = BOX / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def grade_zip(answer):
    import zipfile
    if not (BOX / "notes.zip").exists():
        return False, "нет notes.zip"
    # macOS zip writes names without the UTF-8 flag: Python then reads them as cp437, so they are decoded back here
    infos = zipfile.ZipFile(BOX / "notes.zip").infolist()
    names = {(i.filename if i.flag_bits & 0x800 else i.filename.encode("cp437").decode("utf-8", "replace")) for i in infos if not i.is_dir()}
    want = {n.removeprefix("notes/") for n in NOTES if n.endswith(".md")}
    got = {n.removeprefix("notes/") for n in names}
    return got == want, f"в архиве {sorted(got)}, ждали {sorted(want)}"


HARD = [
    {"id": "fix", "prepare": prepare_fix, "grade": grade_fix,
     "task": f"В проекте {BOX}/shop падают тесты. Найди ошибку в коде и исправь её; тесты не меняй.",
     "done_when": "python3 -m unittest в папке проекта проходит"},
    {"id": "report", "prepare": prepare_report, "grade": grade_report,
     "task": f"По файлам {BOX}/orders.csv и {BOX}/customers.csv сделай {BOX}/report.csv: сумма заказов по каждому городу, по убыванию суммы, "
             "столбцы city,total с этим заголовком.", "done_when": "report.csv готов"},
    {"id": "zip", "prepare": prepare_zip, "grade": grade_zip,
     "task": f"Упакуй все файлы .md из {BOX}/notes (с подпапками, сохрани их структуру) в архив {BOX}/notes.zip. Другие файлы в архив не клади.",
     "done_when": "notes.zip содержит только .md с их папками"},
]

TASKS = [
    {"id": "sort", "prepare": prepare_sort, "grade": grade_sort,
     "task": f"В папке {BOX}/inbox разложи файлы по типу: картинки (jpg, png) — в {BOX}/sorted/images, документы (pdf, docx) — в {BOX}/sorted/docs, "
             f"всё остальное — в {BOX}/sorted/other.", "done_when": "в inbox не осталось файлов, каждый лежит в своей папке"},
    {"id": "expenses", "prepare": prepare_expenses, "grade": grade_expenses,
     "task": f"По файлу {BOX}/expenses.csv посчитай, сколько всего потрачено на категорию «Еда» за октябрь 2026, и запиши только это число в {BOX}/answer.txt.",
     "done_when": "в answer.txt записана сумма"},
    {"id": "config", "prepare": prepare_config, "grade": grade_config,
     "task": f"В файле {BOX}/app/config.ini в разделе [server] поменяй port на 8080 и включи debug (debug = true). Остальное не трогай.",
     "done_when": "в [server] port = 8080 и debug = true, остальные строки прежние"},
    {"id": "search", "prepare": prepare_search, "grade": grade_search,
     "task": f"В проекте {BOX}/project найди, в каком файле определена функция calculate_total и сколько раз её вызывают в коде (без самого определения). "
             "Ответь путём к файлу и числом.", "done_when": "названы файл с определением и число вызовов"},
    {"id": "log", "prepare": prepare_log, "grade": grade_log,
     "task": f"В журнале {BOX}/auth.log найди IP, с которого за последний час журнала (считая от времени последней записи) было больше всего "
             f"неудачных входов (код 401), и сколько их было. Запиши в {BOX}/answer2.txt одной строкой: IP и число через пробел.",
     "done_when": "в answer2.txt записаны IP и число"},
    {"id": "git", "prepare": prepare_git, "grade": grade_git,
     "task": f"В git-репозитории {BOX}/repo найди коммит, в котором удалили функцию legacy_discount: его короткий хэш и автора.",
     "done_when": "названы хэш коммита и автор"},
    {"id": "dupes", "prepare": prepare_dupes, "grade": grade_dupes,
     "task": f"В папке {BOX}/photos (с подпапками) есть одинаковые по содержимому файлы с разными именами. В каждой группе одинаковых оставь "
             f"один файл, остальные перенеси в {BOX}/duplicates.", "done_when": "в photos не осталось одинаковых по содержимому файлов"},
    {"id": "screen", "prepare": lambda: None, "grade": lambda a: ("0x8f21" in a.lower(), "код ошибки не тот"),
     "task": "На экране Mac открыто окно с ошибкой обновления. Посмотри на экран и скажи, какой там код ошибки.", "done_when": "назван код ошибки с экрана"},
]

if "hard" in only:   # the harder set on its own
    TASKS, only = HARD, set()
results = []
for task in TASKS:
    if only and task["id"] not in only:
        continue
    task["prepare"]()
    status.begin(task["task"])
    started = time.time()
    before = len(blocked)
    try:
        answer = ag_mac.run(task["task"], done_when=task["done_when"])
    except Exception as e:
        answer = f"(ошибка: {type(e).__name__}: {e})"
    seconds = time.time() - started
    answer = answer if isinstance(answer, str) else str(answer)
    ok, why = task["grade"](answer)
    status.end(answer[:500])
    results.append({"id": task["id"], "passed": bool(ok), "why": "" if ok else why, "answer": answer[:600], "seconds": round(seconds, 1),
                    "refused_commands": blocked[before:], **harness.usage(started)})
    print(f"{'+' if ok else '-'} {task['id']}: {round(seconds)} с, {results[-1]['usd']:.4f} $ {'' if ok else why}", flush=True)
harness.save(out, {"model": model, "bench": "mac", "cases": results, "rows": harness.rows()})
