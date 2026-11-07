"""What the benchmark really cost, into Sena's real cost journal (data/journal/costs): every paid row of every run, once,
marked «замер моделей», through core/costs.py — the one place a paid call is booked (its prices, its file format, its lock).
Gemini's voice and hearing go through google/media.py in the runs, so they are rows like the others. python journal.py [--write]"""
import glob
import json
import sys
from pathlib import Path

import harness  # noqa: F401  (puts Sena on the path)
from sena.core import costs  # noqa: E402

HERE = Path(__file__).parent

rows, seen = [], set()
for f in glob.glob(str(HERE / "results" / "*" / "*.json")):
    for r in json.load(open(f, encoding="utf-8")).get("rows", []):
        if r["provider"] in ("local", "claude-code", "tomtom") or not r.get("usd"):
            continue
        key = (r["t"], r["provider"], r.get("model"), r.get("usd"))
        if key not in seen:
            seen.add(key)
            rows.append(r)
by = {}
for r in rows:
    by[r["provider"]] = round(by.get(r["provider"], 0) + r["usd"], 4)
print(len(rows), "строк;", by, "всего", round(sum(by.values()), 2), "$")
if "--write" in sys.argv:
    have = {(r["t"], r["provider"]) for r in costs._all_rows()}   # a second --write adds nothing twice
    added = 0
    for r in rows:
        if (r["t"], r["provider"]) not in have:
            costs.record(r["provider"], r["service"], r.get("model", ""), r.get("units"), r.get("tier", "default"), purpose="замер моделей",
                         note="замер моделей на временной папке", moment=r["t"], unknown=bool(r.get("unknown")))
            added += 1
    print("дописано строк:", added)
