"""Quality score: how well Sena is doing — based on judge verdicts, errors and recipes over a period.
Tasks by verdict and by agent, recurring errors (the same spot failing over and over), recipes that
let her down, Tema's remarks, costs. Viewed by Tema (the dashboard, the quality_report tool) and the weekly review (ag_judge)."""
from collections import Counter, defaultdict
from datetime import date, timedelta

from sena.core import costs, events
from sena.core.tools import tool
from sena.memory import task_log

VERDICT_RU = {"done": "сделано", "done_with_remarks": "с замечаниями", "failed": "не вышло", "unverified": "не проверено", "none": "без оценки"}
AGENT_RU = {"ag_sena": "Сена", "ag_web": "Браузер", "ag_mac": "Компьютер", "ag_code": "Код", "ag_judge": "Разбор", "ag_night": "Уборка", "ag_ears": "Слух"}


def _period(days):
    # the last days as whole calendar days, today included: the tasks, the errors and the costs count the same days (the
    # costs' "month" was the calendar month — on the 3rd, «за 30 дней» counted three days of spending)
    today = date.today()
    return f"{today - timedelta(days=max(1, int(days)) - 1)}..{today}"


def _verdict(e):
    return (e.get("outcome") or {}).get("verdict", "none")


def summary(days=7):
    period = _period(days)
    since = costs.span(period)[0]
    tasks = [e for e in task_log.items if e["start"] >= since and not e.get("service")]
    ratings = [e["rating"]["value"] for e in tasks if e.get("rating")]
    by_agent = defaultdict(Counter)
    for e in tasks:
        for a in {s["who"].split("/")[0] for s in e["steps"]} or {"ag_sena"}:
            by_agent[a][_verdict(e)] += 1
    errors = events.search(kind="error", since=since)
    recipes = defaultdict(Counter)
    for e in events.search(kind="recipe", since=since):
        recipes[e.get("name") or "?"]["провал" if e.get("failed") else "успех"] += 1
    try:
        usd = costs.summary(period)["total"]
    except Exception as e:
        events.error("Счёт качества: расходы", e)
        usd = 0
    pairs = [(e["judge_outcome"]["verdict"], e["outcome"]["verdict"]) for e in tasks if e.get("judge_outcome") and (e.get("outcome") or {}).get("by") == "owner"]
    return {"days": days, "tasks": len(tasks), "verdicts": dict(Counter(_verdict(e) for e in tasks)), "agreement": agreement(pairs),
            "ratings": dict(Counter(ratings)), "rating_avg": round(sum(ratings) / len(ratings), 2) if ratings else None,
            "by_agent": {a: dict(c) for a, c in by_agent.items()}, "errors": len(errors),
            "repeats": [(place, n) for place, n in Counter(e.get("place") or e["text"][:60] for e in errors).most_common(8) if n >= 2],
            "recipes": {k: dict(v) for k, v in recipes.items()},
            "remarks": [{"task": e["text"], "comments": [c["text"] for c in e["comments"]]} for e in tasks if e["comments"]][-10:],
            "failed": [{"task": e["text"], "reason": e["outcome"].get("reason", "")} for e in tasks if _verdict(e) == "failed"][-10:],
            "usd": usd, "causes": dict(Counter(e["outcome"]["cause"] for e in tasks if (e.get("outcome") or {}).get("cause")))}


def agreement(pairs):
    """How often the judge's verdict matched the owner's, and Cohen's kappa — agreement beyond chance (Langfuse's score
    analytics): {"pairs", "same", "kappa"} or None with too few pairs to say anything."""
    if len(pairs) < 5:
        return None
    n = len(pairs)
    same = sum(a == b for a, b in pairs) / n
    judge, owner = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    chance = sum(judge[k] * owner[k] for k in judge) / (n * n)
    kappa = (same - chance) / (1 - chance) if chance < 1 else 1.0
    return {"pairs": n, "same": round(same, 2), "kappa": round(kappa, 2)}


def render(days=7):
    """Quality score in words: for Tema, for the weekly review and for Sena herself."""
    s = summary(days)
    if not s["tasks"] and not s["errors"]:
        return f"За {days} дн. задач не было."
    v = s["verdicts"]
    lines = [f"Качество за {days} дн.: задач {s['tasks']} — " + ", ".join(f"{VERDICT_RU.get(k, k)} {n}" for k, n in sorted(v.items(), key=lambda x: -x[1]))
             + f"; ошибок в журнале {s['errors']}; расходы ${s['usd']:.2f}."]
    if s["rating_avg"] is not None:
        lines.append(f"Тема оценил {sum(s['ratings'].values())} задач, средняя оценка {s['rating_avg']:.1f}/5.")
    if s["agreement"]:
        a = s["agreement"]
        lines.append(f"Разбор после задачи совпал с оценкой Темы в {a['same']:.0%} из {a['pairs']} задач (каппа {a['kappa']}: "
                     + ("согласны" if a["kappa"] >= 0.6 else "разбор часто ошибается — ему нельзя слепо верить") + ").")
    for a, c in sorted(s["by_agent"].items(), key=lambda x: -sum(x[1].values())):
        total = sum(c.values())
        lines.append(f"- {AGENT_RU.get(a, a)}: {total}, не вышло {c.get('failed', 0)}, с замечаниями {c.get('done_with_remarks', 0)}")
    if s["causes"]:
        lines.append("Почему не вышло (по разбору): " + ", ".join(f"{k} {n}" for k, n in sorted(s["causes"].items(), key=lambda x: -x[1])))
    if s["repeats"]:
        lines.append("Повторяющиеся ошибки: " + "; ".join(f"{p} ×{n}" for p, n in s["repeats"]))
    bad = [(k, c) for k, c in s["recipes"].items() if c.get("провал")]
    if bad:
        lines.append("Рецепты, которые подводят: " + "; ".join(f"{k} (провалов {c['провал']}, успехов {c.get('успех', 0)})" for k, c in bad))
    for f in s["failed"]:
        lines.append(f"Не вышло: «{f['task'][:80]}» — {f['reason'][:120]}")
    for r in s["remarks"]:
        lines.append(f"Замечание Темы к «{r['task'][:60]}»: " + "; ".join(r["comments"]))
    return "\n".join(lines)


TOOLS = [tool("quality_report", "Счёт качества работы Сены за период: задачи по оценкам и агентам, повторяющиеся ошибки, рецепты, "
                                "которые подводят, замечания Темы. Для вопросов «как ты справляешься», «где чаще ошибаешься».",
              {"days": {"type": "integer", "description": "за сколько дней: 1, 7 (по умолчанию) или 30"}})]
FUNCTIONS = {"quality_report": lambda days=7: render(int(days))}
