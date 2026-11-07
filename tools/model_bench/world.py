"""The world every benchmark run starts from: Sena's memory, records and settings as they are now, copied once (read-only
on the real data/), then copied again for each run so runs never see each other. The copies hold what data/ holds — people,
conversations, overheard speech — so they are the owner's alone, as run.sh keeps data/ (chmod go-rwx)."""
import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # the project root
HERE = Path(__file__).parent
TEMPLATE = HERE / "world"


def private(path):
    """Owner-only, the folder and everything in it: other users of the Mac read nothing of it."""
    path = Path(path)
    for p in [path, *path.rglob("*")]:
        os.chmod(p, 0o700 if p.is_dir() else 0o600)
    return path


def build():
    if TEMPLATE.exists():
        shutil.rmtree(TEMPLATE)
    (TEMPLATE / "state").mkdir(parents=True)
    shutil.copytree(ROOT / "data" / "memory", TEMPLATE / "memory", ignore=shutil.ignore_patterns("*.history.jsonl", "*.moved-*"))
    src = sqlite3.connect(f"file:{ROOT / 'data' / 'state' / 'sena.db'}?mode=ro", uri=True)
    dst = sqlite3.connect(TEMPLATE / "state" / "sena.db")
    src.backup(dst)
    dst.close()
    src.close()
    shutil.copy2(ROOT / "data" / "state" / "settings.json", TEMPLATE / "state" / "settings.json")
    private(TEMPLATE)


def copy(dst):
    dst = Path(dst)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(TEMPLATE, dst)
    os.chmod(dst.parent, 0o700)   # the runs' folder too: what a run writes later (its journal, its answers) stays behind it
    return private(dst)


if __name__ == "__main__":
    build()
    print("world:", sorted(str(p.relative_to(TEMPLATE)) for p in TEMPLATE.rglob("*") if p.is_file())[:40])
    sys.exit(0)
