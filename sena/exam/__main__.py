"""python -m sena.exam [command] — the exam of Sena's mind: real requests, the real cloud brain, tools as stand-ins.
  (nothing)                  the last runs: the score by group and what failed
  run [id|group ...]         run the exam (all cases, or the named ones); --budget 3 caps the spend, --why says what for,
                             --notify tells the owner in Telegram (the weekly run does)
The run works on a throwaway copy of the memory, the registry and the settings: nothing real is sent, run or written. Its
cost goes into the real expense log; a full run also decides the probation of new personality and skills (probation.py).
One run at a time (agents/programs.py keeps the running one); Sena starts it there too — tracked, stopped with her."""
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

COMMAND = sys.argv[1] if len(sys.argv) > 1 else "show"

if COMMAND == "child":   # inside the throwaway data folder the parent made: run and write the result
    from sena.exam import runner
    from sena.telegram import api

    def _refuse(*args, **kwargs):   # the exam's world never reaches the owner: whatever it does, nothing is sent to him for real
        raise RuntimeError("экзамен: в Телеграм ничего не уходит")
    api.post = api._upload = _refuse
    out, budget, only = sys.argv[2], float(sys.argv[3]), sys.argv[4:]
    result = runner.run(set(only) or None, budget)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    sys.exit(0)

from sena import config  # noqa: E402
from sena.agents import programs  # noqa: E402
from sena.core import costs, events, local, settings, store  # noqa: E402
from sena.exam import probation  # noqa: E402
from sena.exam.cases import CASES  # noqa: E402

KEEP = 52   # a year of weekly runs


def runs():
    return store.load_json(config.EXAM_FILE, {"runs": []})["runs"]


def _copy_index(target):
    # the registry as it is: the exam's brain recalls the archive and finds skills as Sena does (rebuilding it would mean
    # computing every fingerprint again). A read-only copy: the core may be writing it meanwhile
    if not config.INDEX_DB.exists():
        return
    src, dst = sqlite3.connect(f"file:{config.INDEX_DB}?mode=ro", uri=True), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _copy_world(tmp):
    """The memory, the records, the registry and the settings as they are now: the exam asks the brain Sena really has.
    Returns the texts of what is on probation as they were given to the exam (probation.measure)."""
    measured = probation.measure()
    if config.MEMORY_DIR.is_dir():
        shutil.copytree(config.MEMORY_DIR, os.path.join(tmp, "memory"), ignore=shutil.ignore_patterns("*.history.jsonl", "*.moved-*"))
    state = os.path.join(tmp, "state")
    os.makedirs(state, exist_ok=True)
    store.snapshot(os.path.join(state, os.path.basename(config.RECORDS_DB)))   # people, rules, lessons: one consistent copy
    _copy_index(os.path.join(state, os.path.basename(config.INDEX_DB)))
    # the settings; and the home model server's record, so the exam uses the core's server, never starts a second one on its port
    for f in (config.SETTINGS_FILE, local.PID_FILE):
        if f.exists():
            shutil.copy2(f, os.path.join(state, f.name))
    return measured


def _spent_in(tmp):
    """The expense rows the run wrote in its throwaway folder — read even when it crashed or timed out: the money was real."""
    rows = []
    folder = os.path.join(tmp, "journal", "costs")
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if name.endswith(".jsonl"):
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                rows += [json.loads(line) for line in f if line.strip().startswith("{")]
    return rows


def _child(only, budget, measured=None):
    """One run in a throwaway copy of the world: (result or None, the expense rows it wrote, what was measured). Stopped
    half way (Sena's stop, a timeout) it still books what it spent: the money was real."""
    tmp = tempfile.mkdtemp(prefix="sena-exam-")
    out = os.path.join(tmp, "result.json")
    result, rows = None, []
    try:
        given = _copy_world(tmp)
        measured = given if measured is None else measured
        # the throwaway world never reaches the real Keychain or the iCloud copy, whatever the parent's environment says: the
        # secrets key there has one fixed name, and a world without the store would mint a new one over the owner's (round 2)
        env = {**os.environ, "SENA_DATA": tmp, "SENA_VAULT": os.path.join(tmp, "vault"), "SENA_KEYCHAIN": "off", "SENA_OFFSITE": "off"}
        child = subprocess.Popen([sys.executable, "-m", "sena.exam", "child", out, str(budget), *only], cwd=config.ROOT, env=env)
        try:
            code = child.wait(timeout=3600)
        except subprocess.TimeoutExpired:
            code = "время вышло"
        finally:
            if child.poll() is None:   # a timeout or a stop of this run: the child doesn't go on spending on its own
                child.kill()
                child.wait()
        if code or not os.path.exists(out):
            events.error("Экзамен", RuntimeError(f"прогон не завершился (код {code})"))
        else:
            with open(out, encoding="utf-8") as f:
                result = json.load(f)
    finally:
        rows = _spent_in(tmp)
        shutil.rmtree(tmp, ignore_errors=True)
        for row in rows:   # into the real expense log, under "экзамен" — a failed or stopped run's spend too
            costs.record(row["provider"], row["service"], row.get("model", ""), row.get("units"), row.get("tier", "default"),
                         purpose="экзамен", moment=row["t"], unknown=bool(row.get("unknown")))
    if result:
        result.pop("rows", None)
    return result, rows, measured


def run(only=(), budget=None, why="вручную", notify=False):
    if not programs.exam_begin():
        print("Экзамен уже идёт: второй не запускаю", flush=True)
        events.log("exam", "экзамен не запущен: уже идёт другой", why=why)
        return None
    signal.signal(signal.SIGTERM, signal.default_int_handler)   # a stop by SIGTERM unwinds like Ctrl+C: the spend is still booked
    try:
        return _run(only, budget or settings.get("exam_budget_usd"), why, notify)
    finally:
        programs.exam_end()


def _run(only, budget, why, notify):
    started = time.time()
    result, _, measured = _child(only, budget)
    if result is None:
        return None
    checked = [c for c in result["cases"] if c["passed"] is not None]
    record = {**result, "t": started, "why": why, "full": not only, "passed": sum(1 for c in checked if c["passed"]), "total": len(checked),
              "version": config.version()}   # which code this exam measured
    verdict, rolled = "", []
    if record["full"]:
        accepted = next((r for r in reversed(runs()) if r.get("full") and r.get("accepted")), None)
        again = None
        lost = probation.flips(record, accepted)[0] if accepted else []
        if lost and result["usd"] < budget:   # a lost case is asked once more before it counts: one run flips by chance
            rerun, _, _ = _child(lost, budget - result["usd"], measured)
            again = {c["id"]: c["passed"] for c in (rerun or {}).get("cases", [])}
            record["rechecked"] = again
        verdict, rolled = probation.judge(record, accepted, again, measured)
        record.update(verdict=verdict, rolled_back=rolled, accepted=not rolled)
    history = runs() + [record]
    store.save_json(config.EXAM_FILE, {"runs": history[-KEEP:]}, indent=1)
    events.log("exam", f"экзамен: {record['passed']} из {record['total']}" + (f"; {verdict}" if verdict else ""),
               passed=record["passed"], total=record["total"], usd=record["usd"], why=why, rolled_back=rolled or None)
    if notify:
        from sena.telegram import api
        api.notify(summary(record))
    return record


def summary(record):
    """A few lines for the owner: the score, the weak groups, what it cost, the probation verdict."""
    groups = {}
    for c in record["cases"]:
        if c["passed"] is not None:
            done, total = groups.get(c["group"], (0, 0))
            groups[c["group"]] = (done + bool(c["passed"]), total + 1)
    weak = [f"{g} {d}/{t}" for g, (d, t) in groups.items() if d < t]
    lines = [f"Экзамен ума: {record['passed']} из {record['total']} ({record['usd']:.2f} $, {record['seconds'] / 60:.0f} мин)."]
    if weak:
        lines.append("Слабее всего: " + ", ".join(weak) + ".")
    if record.get("verdict"):
        lines.append(record["verdict"][0].upper() + record["verdict"][1:] + ".")
    return "\n".join(lines)


def show():
    history = runs()
    if not history:
        print(f"Экзамена ещё не было. Вопросов: {len(CASES)}. Запуск: python -m sena.exam run")
        return
    for r in history[-5:]:
        print(time.strftime("%d.%m.%Y %H:%M", time.localtime(r["t"])), "—", summary(r).replace("\n", " "))
    last = history[-1]
    for c in last["cases"]:
        if not c["passed"]:
            print(f"  - {c['id']} ({c['group']}): {'; '.join(c['problems'])}")


if __name__ == "__main__":
    args = sys.argv[2:]
    if COMMAND == "run":
        budget = float(args[args.index("--budget") + 1]) if "--budget" in args else None
        why = args[args.index("--why") + 1] if "--why" in args else "вручную"
        flags = {"--budget", "--why"}
        only = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] not in flags)]
        record = run(only, budget, why, notify="--notify" in args)
        print(summary(record) if record else "Экзамен не прошёл до конца: подробности в журнале событий")
    else:
        show()
