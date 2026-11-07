"""All benchmark results into one summary: per task, per model — quality, price, speed. python report.py -> results/summary.json"""
import glob
import json
import statistics
from pathlib import Path

HERE = Path(__file__).parent
R = HERE / "results"
MODELS = ["claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna", "gpt-5.4-mini"]
NAMES = {"claude-fable-5-1": "Claude Fable 5.1", "claude-opus-5-5": "Claude Opus 5.5", "claude-sonnet-5-5": "Claude Sonnet 5.5",
         "claude-haiku-5-5": "Claude Haiku 5.5", "gpt-6-astra": "GPT-6 Astra", "gpt-6.1-sol": "GPT-6.1 Sol", "gpt-6-luna": "GPT-6 Luna",
         "gpt-5.4-mini": "GPT-5.4 mini (сейчас)"}
# how often each job happens on an ordinary day (an estimate for the monthly bill, not a measurement)
PER_DAY = {"brain": 40, "voice": 10, "web": 3, "mac": 2, "judge": 5, "rules": 1, "router": 3, "compact": 2}


def load(pattern):
    out = {}
    for f in sorted(glob.glob(str(R / pattern / "*.json"))):
        data = json.load(open(f, encoding="utf-8"))
        out.setdefault(data.get("model") or Path(f).stem, []).append(data)
    return out


def median(xs):
    return round(statistics.median(xs), 2) if xs else None


def p90(xs):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(0.9 * len(xs)))], 2) if xs else None


def row(cases, cost_key="usd"):
    checked = [c for c in cases if c.get("passed") is not None]
    if not checked:
        return None
    return {"n": len(checked), "passed": sum(1 for c in checked if c["passed"]), "rate": round(100 * sum(1 for c in checked if c["passed"]) / len(checked)),
            "usd_each": round(sum(c.get(cost_key) or 0 for c in checked) / len(checked), 5), "sec_median": median([c["seconds"] for c in checked if c.get("seconds")]),
            "sec_p90": p90([c["seconds"] for c in checked if c.get("seconds")]), "calls_each": round(sum(c.get("calls", 0) for c in checked) / len(checked), 1)}


def exam(label):
    table = {}
    for model, runs in load(f"{label}-*").items():
        cases = [c for r in runs for c in r["cases"]]
        table[model] = row(cases)
        if table[model]:
            table[model]["failed"] = [c["id"] for c in cases if c.get("passed") is False]
    return table


def tasks(bench):
    table = {}
    for model, runs in load(f"{bench}-*").items():
        cases = [c for r in runs for c in r["cases"]]
        table[model] = row(cases)
        if table[model]:
            table[model]["asked_each"] = round(sum(len(c.get("asked_owner", [])) for c in cases) / len(cases), 2)
            fails = {}
            for c in cases:
                if c.get("passed") is False:
                    fails[c["id"]] = fails.get(c["id"], 0) + 1
            table[model]["failed"] = fails
    return table


def judge():
    table = {}
    for model, runs in load("judge-*").items():
        cases = [c for r in runs for c in r["cases"]]
        base = row(cases) or {}
        marks = [c.get("marks", {}) for c in cases]
        for key in ("verdict", "lesson", "safe"):
            graded = [m[key] for m in marks if m.get(key) is not None]
            base[f"{key}_rate"] = round(100 * sum(graded) / len(graded)) if graded else None
        table[model] = base
    return table


def narrow():
    table = {}
    for model, runs in load("narrow-*").items():
        cases = [c for r in runs for c in r["cases"]]
        entry = {}
        for job in ("rules", "router", "compact"):
            entry[job] = row([c for c in cases if c.get("job") == job])
            if job == "compact" and entry[job]:
                picked = [c for c in cases if c.get("job") == "compact"]
                entry[job]["facts_kept"] = round(100 * sum(c.get("facts_kept", 0) for c in picked) / max(1, sum(c.get("facts", 0) for c in picked)))
                entry[job]["words_median"] = median([c.get("words", 0) for c in picked])
        table[model] = entry
    return table


UNITS = "ноль один два три четыре пять шесть семь восемь девять десять одиннадцать двенадцать тринадцать четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать".split()
TENS = {2: "двадцать", 3: "тридцать", 4: "сорок", 5: "пятьдесят", 6: "шестьдесят", 7: "семьдесят", 8: "восемьдесят", 9: "девяносто"}
SAME = {"ютуб": "youtube", "ютубе": "youtube", "хайрфлоу": "hireflow", "хайр": "hireflow", "сёрф": "серф"}


def _number(n):
    n = int(n)
    if n < 20:
        return [UNITS[n]]
    return [TENS[n // 10]] + ([UNITS[n % 10]] if n % 10 else []) if n < 100 else [str(n)]


def words(text):
    """Plain words for counting errors: lower case, е for ё, numbers as words (a «9» said as «девять» is not an error)."""
    import re
    text = (text or "").lower().replace("ё", "е").replace("hire-flow", "hireflow").replace("хайр-флоу", "hireflow")
    out = []
    for t in re.findall(r"[a-zа-я]+|\d+", text):
        out += _number(t) if t.isdigit() else [SAME.get(t, t)]
    return out


def wer(reference, hypothesis):
    r, h = words(reference), words(hypothesis)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(1, len(r))


def voice():
    files = glob.glob(str(R / "voice" / "voice.json"))
    if not files:
        return None
    data = json.load(open(files[0], encoding="utf-8"))
    for item in data["hearing"]:
        item["wer"] = round(wer(item["text"], item["heard"]), 3) if not item["error"] else 1.0
    for item in data["speaking"]:
        item["wer"] = round(wer(item["text"], item["heard"]), 3) if item["heard"] else None
    hearing = {}
    for h in data["hearing"]:
        s = hearing.setdefault(h["system"], {"all": [], "by_voice": {}, "seconds": [], "errors": 0})
        s["all"].append(h["wer"])
        s["by_voice"].setdefault(h["voice"], []).append(h["wer"])
        s["seconds"].append(h["seconds"])
        s["errors"] += bool(h["error"])
    hearing = {k: {"wer": round(100 * sum(v["all"]) / len(v["all"]), 1), **{f"wer_{voice}": round(100 * sum(x) / len(x), 1) for voice, x in v["by_voice"].items()},
                   "sec_median": median(v["seconds"]), "errors": v["errors"]} for k, v in hearing.items()}
    speaking = {}
    for s in data["speaking"]:
        e = speaking.setdefault(s["voice"], {"wer": [], "seconds": [], "audio": 0.0, "errors": 0, "files": []})
        if s["wer"] is not None:
            e["wer"].append(s["wer"])
        e["seconds"].append(s["seconds"])
        e["audio"] += s["audio_seconds"]
        e["errors"] += bool(s["error"])
        e["files"].append(s["file"])
    speaking = {k: {"wer": round(100 * sum(v["wer"]) / len(v["wer"]), 1) if v["wer"] else None, "sec_median": median(v["seconds"]),
                    "audio_seconds": round(v["audio"], 1), "errors": v["errors"], "files": v["files"]} for k, v in speaking.items()}
    gemini = sum(r.get("usd") or 0 for r in data.get("rows", []) if r.get("provider") == "gemini")   # the cost journal of the run (google/media.py)
    return {"hearing": hearing, "speaking": speaking, "gemini_usd": data.get("gemini_usd", round(gemini, 6))}


def monthly(summary):
    """An estimate: what a month would cost if every job ran on this model, at PER_DAY jobs a day."""
    out = {}
    for model in MODELS:
        usd = 0.0
        for job, key in (("brain", summary["brain"]), ("voice", summary["voice_commands"]), ("web", summary["web"]), ("mac", summary["mac"]), ("judge", summary["judge"])):
            r = key.get(model)
            usd += (r or {}).get("usd_each", 0) * PER_DAY[job]
        for job in ("rules", "router", "compact"):
            r = (summary["narrow"].get(model) or {}).get(job)
            usd += (r or {}).get("usd_each", 0) * PER_DAY[job]
        out[model] = round(usd * 30, 2)
    return out


def spent():
    rows = []
    for f in glob.glob(str(R / "*" / "*.json")):
        data = json.load(open(f, encoding="utf-8"))
        rows += data.get("rows", [])
    by = {}
    for r in rows:
        if r["provider"] in ("openai", "anthropic", "gemini"):
            by[r["provider"]] = round(by.get(r["provider"], 0) + (r.get("usd") or 0), 4)
    return by


if __name__ == "__main__":
    summary = {"brain": exam("brain"), "voice_commands": exam("voice"), "web": tasks("web"), "mac": tasks("mac"), "hardweb": tasks("hardweb"),
               "hardmac": tasks("hardmac"), "judge": judge(), "narrow": narrow(), "voice": voice()}
    summary["monthly"] = monthly(summary)
    summary["spent"] = spent()
    json.dump(summary, open(R / "summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for part in ("brain", "voice_commands", "web", "mac", "hardweb", "hardmac", "judge"):
        print(f"\n== {part}")
        for m in MODELS:
            r = summary[part].get(m)
            if r:
                print(f"  {NAMES[m]:24} {r['passed']}/{r['n']} ({r['rate']}%)  {r['usd_each']:.4f} $  {r['sec_median']} с (p90 {r['sec_p90']})"
                      + (f"  оценка {r.get('verdict_rate')}% урок {r.get('lesson_rate')}% безоп. {r.get('safe_rate')}%" if part == "judge" else ""))
    print("\n== narrow")
    for m in MODELS:
        r = summary["narrow"].get(m)
        if r:
            print(f"  {NAMES[m]:24} " + "  ".join(f"{j} {x['passed']}/{x['n']} {x['usd_each']:.5f}$ {x['sec_median']}с" for j, x in r.items() if x))
    print("\n== monthly", summary["monthly"])
    print("== spent", summary["spent"])
