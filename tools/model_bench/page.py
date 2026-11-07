"""The results page: python page.py -> report.html (tables per task from results/summary.json, the voice samples embedded)."""
import base64
import html
import json
import subprocess
import tempfile
from pathlib import Path

from report import MODELS, NAMES, PER_DAY

HERE = Path(__file__).parent
S = json.load(open(HERE / "results" / "summary.json", encoding="utf-8"))
VERDICT = json.load(open(HERE / "verdict.json", encoding="utf-8"))   # the conclusions, written after reading the numbers
CLAUDE = {m for m in MODELS if m.startswith("claude")}


def esc(x):
    return html.escape(str(x))


def money(x):
    if x is None:
        return "—"
    if x >= 0.1:
        return f"{x:.2f} $"
    if x >= 0.001:
        return f"{x:.4f} $".rstrip("0").rstrip(",")
    return f"{x:.5f} $"


def sec(x):
    return "—" if x is None else f"{x:.1f} с".replace(".", ",")


def pct(x):
    return "—" if x is None else f"{x}%"


def best(rows, key, high=True):
    values = [r[key] for r in rows.values() if r and r.get(key) is not None]
    return (max(values) if high else min(values)) if values else None


def table(title, rows, columns, note=""):
    head = "".join(f"<th>{esc(c[0])}</th>" for c in columns)
    body = []
    for m in MODELS:
        r = rows.get(m)
        if not r:
            continue
        cells = []
        for label, key, fmt, high in columns:
            v = r.get(key)
            top = high is not None and v is not None and v == best(rows, key, high)
            cells.append(f"<td class='{'top' if top else ''}'>{fmt(v)}</td>")
        body.append(f"<tr><th scope=row><span class='who {'c' if m in CLAUDE else 'o'}'></span>{esc(NAMES[m])}</th>{''.join(cells)}</tr>")
    return (f"<h3>{esc(title)}</h3>" + (f"<p class=note>{note}</p>" if note else "") +
            f"<div class=scroll><table><thead><tr><th>Модель</th>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>")


def mp3(path):
    path = Path(path)
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "a.mp3"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-ac", "1", "-b:a", "48k", str(target)], check=True)
        return "data:audio/mpeg;base64," + base64.b64encode(target.read_bytes()).decode()


QUALITY = ("Качество", "rate", pct, True)
PRICE = ("Цена за раз", "usd_each", money, False)
MEDIAN = ("Время, медиана", "sec_median", sec, False)
P90 = ("Время, 9 из 10 быстрее", "sec_p90", sec, False)


def section_tasks():
    out = []
    for key, title, what, metric in (
            ("brain", "Мозг Сены", VERDICT["req"]["brain"], "51 вопрос экзамена ума на копии памяти; инструменты подменены, оценка по вызовам и ответу."),
            ("voice_commands", "Голосовые команды", VERDICT["req"]["voice"], "16 коротких команд по быстрому пути (без размышлений)."),
            ("web", "Агент браузера", VERDICT["req"]["web"], "9 задач на тестовом магазине на этом Mac, два прогона; оценка по тому, что дошло до сайта."),
            ("mac", "Агент компьютера", VERDICT["req"]["mac"], "8 задач в песочнице (запись только в папку задачи, домашняя папка и сеть закрыты), два прогона; оценка по файлам."),
            ("hardweb", "Браузер: трудные задачи", VERDICT["req"]["web"], "4 трудные задачи (свой выпадающий список и календарь, бесконечная прокрутка, три шага с ловушкой оплаты, новая вкладка); у Fable и Astra один прогон, у остальных два."),
            ("hardmac", "Компьютер: трудные задачи", VERDICT["req"]["mac"], "3 трудные задачи (починить код по тестам, свести таблицы, архив); у Fable и Astra один прогон, у остальных два."),
            ("judge", "Судья: разбор задач", VERDICT["req"]["judge"], "8 законченных задач с известной правдой, два прогона.")):
        cols = [QUALITY, PRICE, MEDIAN, P90]
        if key == "judge":
            cols = [("Верная оценка", "verdict_rate", pct, True), ("Нужный урок", "lesson_rate", pct, True), ("Безопасность", "safe_rate", pct, True), PRICE, MEDIAN]
        if key == "web":
            cols = cols + [("Вопросов Теме на задачу", "asked_each", lambda v: "—" if v is None else f"{v:.2f}".replace(".", ","), False)]
        pick = f"<p class=pick>{VERDICT['picks'][key]}</p>" if key in VERDICT["picks"] else ""
        out.append(f"<section id={key.replace('_', '-')}><h2>{esc(title)}</h2>" + (f"<p class=req><b>Что важно:</b> {esc(what)}</p>" if not key.startswith("hard") else "")
                   + table("Результаты", S[key], cols, esc(metric)) + pick + "</section>")
    narrow = {m: {**{f"{j}_{k}": v for j, r in (S["narrow"].get(m) or {}).items() if r for k, v in r.items()}} for m in MODELS}
    cols = [("Время в правиле", "rules_rate", pct, True), ("Выбор рецепта", "router_rate", pct, True), ("Сводка: факты", "compact_facts_kept", pct, True),
            ("Цена правила", "rules_usd_each", money, False), ("Время правила", "rules_sec_median", sec, False)]
    out.append(f"<section id=narrow><h2>Мелкие дела</h2><p class=req><b>Что важно:</b> {esc(VERDICT['req']['narrow'])}</p>"
               + table("Результаты", narrow, cols, "12 правил со временем, 9 выборов рецепта, 3 сводки переписки; два прогона.")
               + f"<p class=pick>{VERDICT['picks']['narrow']}</p></section>")
    return "".join(out)


def section_voice():
    v = S.get("voice")
    if not v:
        return ""
    rows = "".join(f"<tr><th scope=row>{esc(k)}</th><td>{pct(x['wer'])}</td><td>{pct(x.get('wer_Ирина'))}</td><td>{pct(x.get('wer_Милена'))}</td>"
                   f"<td>{pct(x.get('wer_Ирина с шумом'))}</td><td>{sec(x['sec_median'])}</td><td>{esc(VERDICT['stt_price'].get(k, '—'))}</td></tr>"
                   for k, x in sorted(v["hearing"].items(), key=lambda kv: kv[1]["wer"]))
    hearing = ("<h3>Распознавание речи</h3><p class=note>36 фраз: голос Ирины, голос Милены и Ирина с шумом. Ниже — доля слов с ошибкой: чем меньше, тем лучше.</p>"
               "<div class=scroll><table><thead><tr><th>Система</th><th>Ошибки, всего</th><th>Ирина</th><th>Милена</th><th>С шумом</th><th>Время</th><th>Цена минуты</th></tr></thead>"
               f"<tbody>{rows}</tbody></table></div>")
    cards = []
    for name, x in v["speaking"].items():
        players = "".join(f"<audio controls preload=none src='{mp3(f)}'></audio>" for f in x["files"] if f)
        cards.append(f"<div class=voice><h4>{esc(name)}</h4><p class=note>Разборчивость: ошибок {pct(x['wer'])} · {sec(x['sec_median'])} на фразу · "
                     f"{esc(VERDICT['tts_price'].get(name, '—'))}</p>{players}</div>")
    speaking = "<h3>Озвучка: послушай сам</h3><p class=note>Три обычных ответа Сены каждым голосом. Разборчивость — сколько слов точный домашний слух услышал неверно.</p>" \
               f"<div class=voices>{''.join(cards)}</div>"
    return f"<section id=voice><h2>Голос и слух</h2><p class=req><b>Что важно:</b> {esc(VERDICT['req']['speech'])}</p>{hearing}{speaking}<p class=pick>{VERDICT['picks']['speech']}</p></section>"


def section_summary():
    rows = "".join(f"<tr><th scope=row>{esc(r['task'])}</th><td><b>{esc(r['pick'])}</b></td><td>{esc(r['why'])}</td><td>{esc(r['now'])}</td></tr>" for r in VERDICT["summary"])
    month = "".join(f"<tr><th scope=row><span class='who {'c' if m in CLAUDE else 'o'}'></span>{esc(NAMES[m])}</th><td>{money(S['monthly'].get(m))}</td></tr>" for m in MODELS)
    return (f"<section id=summary><h2>Что выбрать</h2><p class=lead>{esc(VERDICT['lead'])}</p><div class=scroll><table class=picks><thead><tr><th>Дело</th><th>Лучший выбор</th>"
            f"<th>Почему</th><th>Сейчас</th></tr></thead><tbody>{rows}</tbody></table></div>"
            f"<h3>Если бы всё делала одна модель</h3><p class=note>Оценка месяца по замеренным ценам: в день {PER_DAY['brain']} сообщений мозгу, "
            f"{PER_DAY['voice']} голосовых команд, {PER_DAY['web']} задачи браузера, {PER_DAY['mac']} — компьютера, {PER_DAY['judge']} разборов и мелкие дела.</p>"
            f"<div class=scroll><table class=narrow><thead><tr><th>Модель</th><th>Месяц</th></tr></thead><tbody>{month}</tbody></table></div></section>")


def section_method():
    items = "".join(f"<li>{esc(x)}</li>" for x in VERDICT["method"])
    spent = ", ".join(f"{k}: {money(v)}" for k, v in S["spent"].items())
    return f"<section id=method><h2>Как мерили</h2><ul>{items}</ul><p class=note>Потрачено на замер: {esc(spent)}.</p></section>"


STYLE = """<style>
/* a quiet report: a sheet of paper with two inks — Claude in clay, OpenAI in teal — and one accent for the winner of each column */
:root{--bg:#f7f7f4;--sheet:#ffffff;--fg:#1f2421;--muted:#5d655f;--line:#dfe2dc;--win:#e7f3e4;--win-fg:#1f5a2b;--claude:#b4572e;--openai:#1e7a72;
--display:"Literata",Georgia,serif;--body:"Golos Text",-apple-system,"Segoe UI",sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#151816;--sheet:#1c201d;--fg:#e6e9e4;--muted:#a3aba5;--line:#2f3530;--win:#203626;--win-fg:#a6dcae;--claude:#e08a5f;--openai:#5cc3b8;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#151816;--sheet:#1c201d;--fg:#e6e9e4;--muted:#a3aba5;--line:#2f3530;--win:#203626;--win-fg:#a6dcae;--claude:#e08a5f;--openai:#5cc3b8;color-scheme:dark}
body{background:var(--bg);color:var(--fg);font:15px/1.55 var(--body);padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:24px 48px}
main{max-width:1060px;margin:0 auto;display:grid;gap:28px}
h1,h2,h3{font-family:var(--display);text-wrap:balance;line-height:1.2;margin:0}
h1{font-size:32px}h2{font-size:24px;margin-bottom:6px}h3{font-size:17px;margin:18px 0 6px}h4{margin:0 0 4px;font-size:15px}
section{background:var(--sheet);border:1px solid var(--line);border-radius:12px;padding:20px 22px;min-width:0}
.lead{font-size:17px;max-width:70ch}.note{color:var(--muted);font-size:13px;margin:4px 0 8px;max-width:80ch}.req{max-width:80ch;margin:6px 0}
.pick{background:var(--win);color:var(--win-fg);padding:10px 14px;border-radius:8px;margin:12px 0 0;max-width:90ch}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:14px}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}thead th{color:var(--muted);font-weight:600;font-size:12px;letter-spacing:.02em}
tbody th{font-weight:600;white-space:nowrap}td.top{background:var(--win);color:var(--win-fg);font-weight:700}
.who{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:8px}.who.c{background:var(--claude)}.who.o{background:var(--openai)}
.legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--muted);font-size:13px}.voices{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.voice{border:1px solid var(--line);border-radius:10px;padding:10px 12px;display:grid;gap:6px;min-width:0}audio{width:100%;height:32px}
table.narrow{max-width:420px}ul{margin:6px 0;padding-left:20px;max-width:90ch}li{margin:4px 0}nav{display:flex;gap:14px;flex-wrap:wrap;font-size:14px}nav a{color:var(--openai)}
@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
</style>"""


def build():
    head = ("<title>Выбор моделей Сены</title><meta name='claude:viewport-fit-sides' content='cover'>"
            "<link rel=stylesheet href='https://fonts.googleapis.com/css2?family=Golos+Text:wght@400;600;700&family=Literata:opsz,wght@7..72,600&display=swap'>" + STYLE)
    nav = "".join(f"<a href='#{a}'>{t}</a>" for a, t in (("summary", "Что выбрать"), ("brain", "Мозг"), ("voice-commands", "Голосовые команды"), ("web", "Браузер"),
                                                          ("mac", "Компьютер"), ("judge", "Судья"), ("narrow", "Мелкие дела"), ("voice", "Голос и слух"), ("method", "Как мерили")))
    legend = "<p class=legend><span><span class='who c'></span>Claude</span><span><span class='who o'></span>OpenAI</span><span>зелёным — лучшее в столбце</span></p>"
    page = (head + f"<main><header><h1>Выбор моделей Сены</h1><p class=note>{esc(VERDICT['date'])} · 8 моделей, 7 видов дел, всё на настоящих вызовах</p>{legend}<nav>{nav}</nav></header>"
            + section_summary() + section_tasks() + section_voice() + section_method() + "</main>")
    (HERE / "report.html").write_text(page, encoding="utf-8")
    print("report.html", round(len(page) / 1e6, 2), "MB")


if __name__ == "__main__":
    build()
