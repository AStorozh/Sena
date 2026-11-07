"""Run one benchmark for several models at once, each in its own process and its own copy of the world:
python launch.py <bench> <label> <model,model,...> [args for the bench...]  ->  results/<label>/<model>.json and .log"""
import os
import subprocess
import sys
import time
from pathlib import Path

import world

HERE = Path(__file__).parent
bench, label, models, extra = sys.argv[1], sys.argv[2], sys.argv[3].split(","), sys.argv[4:]
folder = HERE / "results" / label
folder.mkdir(parents=True, exist_ok=True)
world.private(HERE / "results")   # the answers and logs quote the owner's memory: his alone, like the world they came from
procs = []
for model in models:
    data = world.copy(HERE / "runs" / label / model)
    env = {**os.environ, "SENA_DATA": str(data), "SENA_VAULT": str(data / "vault"), "SENA_HEADLESS": "1", "PYTHONUNBUFFERED": "1"}
    log = open(folder / f"{model}.log", "w")
    procs.append((model, subprocess.Popen([sys.executable, str(HERE / f"bench_{bench}.py"), model, str(folder / f"{model}.json"), *extra],
                                          cwd=str(HERE), env=env, stdout=log, stderr=subprocess.STDOUT), time.time()))
for model, proc, started in procs:
    proc.wait()
    print(f"{model}: код {proc.returncode}, {time.time() - started:.0f} с", flush=True)
