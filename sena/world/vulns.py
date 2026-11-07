"""Known vulnerabilities in libraries: the OSV.dev database (Google; aggregates GitHub Advisory, PyPA, npm and others), no key needed. Checks packages named manually
(requests==2.19.0, npm:lodash@4.17.20), or a project's dependencies on the desktop via requirements.txt and package-lock.json. A version is required: without it, an answer covering all versions at once would be misleading."""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sena import config
from sena.core.safety import external
from sena.core.tools import string, strings, tool
from sena.world import net

BATCH = "https://api.osv.dev/v1/querybatch"
VULN = "https://api.osv.dev/v1/vulns/{}"
HEADERS = {"User-Agent": "Sena/1.0 (https://github.com/AStorozh/Sena)"}
CHUNK = 500       # requests per batch (OSV's limit is 1000)
DETAILED = 30     # how many vulnerabilities we read details for
SHOWN = 25        # how many lines are shown to Tema
RANK = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "MEDIUM": 2, "LOW": 3}
PYPI = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)(?:\[[^\]]*\])?\s*(?:==|@|\s)\s*(\d[\w.\-+!]*)$")


class VulnError(net.WorldError):
    pass


def _spec(text):
    """"requests==2.19.0" or "npm:lodash@4.17.20" -> (ecosystem, name, version)."""
    text = str(text).strip()
    if text.lower().startswith("npm:"):
        name, _, version = text[4:].strip().rpartition("@")
        if name and version:
            return "npm", name, version
    else:
        m = PYPI.match(text[5:].strip() if text.lower().startswith("pypi:") else text)
        if m:
            return "PyPI", m.group(1), m.group(2)
    raise VulnError(f"«{text}»: пиши пакет с точной версией, например requests==2.19.0 или npm:lodash@4.17.20")


def _project(name):
    """The project's folder on the desktop: by name (Luma, Sena) or full path within the desktop. Only dependency files are read."""
    desktop = Path(config.DESKTOP).resolve()
    raw = Path(str(name).strip()).expanduser()
    path = (raw if raw.is_absolute() else desktop / raw).resolve()
    if desktop not in path.parents or not path.is_dir():
        raise VulnError("Проект должен лежать на рабочем столе: назови папку (Luma, Sena) или полный путь внутри него")
    return path


def _from_project(path):
    found = {}
    reqs = path / "requirements.txt"
    if reqs.is_file():
        for line in reqs.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.split("#")[0].split(";")[0].strip()
            m = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)(?:\[[^\]]*\])?==(\d[\w.\-+!]*)$", line)
            if m:
                found[("PyPI", m.group(1), m.group(2))] = True
    lock = path / "package-lock.json"
    if lock.is_file():
        try:
            for key, info in (json.loads(lock.read_text(encoding="utf-8")).get("packages") or {}).items():
                name = key.rsplit("node_modules/", 1)[-1] if "node_modules/" in key else ""
                if name and isinstance(info, dict) and info.get("version") and not info.get("link"):
                    found[("npm", name, info["version"])] = True
        except (ValueError, OSError):
            raise VulnError("package-lock.json не читается")
    if not found:
        raise VulnError(f"В папке {path.name} нет requirements.txt с закреплёнными версиями (==) или package-lock.json")
    return list(found)


def _numbers(version):
    return tuple(int(n) for n in re.findall(r"\d+", str(version))[:4])


def _fixed(vuln, ecosystem, name, version=""):
    """The version with the fix. A package with multiple branches (15.x and 16.x) has different fixes: take the closest one above the current version in the same branch, otherwise the closest one above."""
    fixes = []
    for a in vuln.get("affected") or []:
        pkg = a.get("package") or {}
        if pkg.get("name", "").lower() == name.lower() and pkg.get("ecosystem") == ecosystem:
            fixes += [e["fixed"] for r in a.get("ranges") or [] for e in r.get("events") or [] if e.get("fixed")]
    if not fixes:
        return ""
    now = _numbers(version)
    above = sorted((f for f in set(fixes) if _numbers(f) > now), key=_numbers)
    same = [f for f in above if _numbers(f)[:1] == now[:1]]
    return (same or above or fixes)[0]


def _hits(pkgs):
    """{package: [vulnerability ids]} for the packages OSV knows something about, asked in batches."""
    hit = {}
    for start in range(0, len(pkgs), CHUNK):
        chunk = pkgs[start:start + CHUNK]
        data = net.as_json(net.post(BATCH, {"queries": [{"package": {"name": n, "ecosystem": e}, "version": v} for e, n, v in chunk]}, HEADERS, timeout=90,
                                    who="OSV.dev", error=VulnError), "OSV.dev", VulnError)
        for pkg, result in zip(chunk, data.get("results") or []):
            ids = [v["id"] for v in (result or {}).get("vulns") or []]
            if ids:
                hit[pkg] = ids
    return hit


def _details(ids):
    """{id: its record, or None when OSV didn't give it this time} for the first DETAILED ids, read side by side."""
    def detail(vuln_id):
        try:
            return vuln_id, net.as_json(net.get(VULN.format(vuln_id), None, HEADERS, timeout=30, ttl=3600, who="OSV.dev", error=VulnError), "OSV.dev", VulnError)
        except VulnError:
            return vuln_id, None   # not read is not "no fix": nothing is said about what wasn't seen
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(detail, ids[:DETAILED]))


def _row(package, vid, info):
    """(rank, line) of one vulnerability of a package: the most serious first, one without details last, saying so."""
    eco, name, version = package
    v = info.get(vid)
    if not v:   # past the first DETAILED, or OSV didn't give the details this time
        return 9, f"{name} {version}: {vid} (подробности {'не прочитались' if vid in info else 'не читала'}: https://osv.dev/vulnerability/{vid})"
    severity = str((v.get("database_specific") or {}).get("severity", "")).upper()
    cves = [a for a in v.get("aliases") or [] if a.startswith("CVE-")]
    fixed = _fixed(v, eco, name, version)
    return RANK.get(severity, 5), (f"{name} {version}: {vid}{' (' + ', '.join(cves[:2]) + ')' if cves else ''}; {severity.lower() or 'серьёзность не указана'}; "
                                   f"{(v.get('summary') or 'без описания')[:140]}" + (f"; исправлено в {fixed}" if fixed else "; исправления нет"))


def vuln_check(packages=None, project=""):
    """Known vulnerabilities: packages — a list like "requests==2.19.0" or "npm:lodash@4.17.20"; project — a project folder on the desktop (checks all dependencies from its lock files)."""
    items = [packages] if isinstance(packages, str) else list(packages or [])
    pkgs = [_spec(p) for p in items]
    scope = ""
    if str(project or "").strip():
        path = _project(project)
        pkgs += _from_project(path)
        scope = f" проекта {path.name}"
    if not pkgs:
        raise VulnError("Назови пакеты (requests==2.19.0) или проект (Luma)")
    pkgs = list(dict.fromkeys(pkgs))
    hit = _hits(pkgs)
    if not hit:
        return external(f"Проверено пакетов{scope}: {len(pkgs)}. Известных уязвимостей в базе OSV нет. Это не гарантия: база знает только о том, что уже нашли и описали.", "OSV.dev")
    info = _details(list(dict.fromkeys(i for v in hit.values() for i in v)))
    rows = sorted((_row(package, vid, info) for package, vuln_ids in hit.items() for vid in vuln_ids), key=lambda r: r[0])
    total = sum(len(v) for v in hit.values())
    extra = total - len(rows[:SHOWN])
    return external(f"Проверено пакетов{scope}: {len(pkgs)}; с известными уязвимостями: {len(hit)} (записей {total}). Сначала самые серьёзные:\n"
                    + "\n".join(f"{i}. {text}" for i, (_, text) in enumerate(rows[:SHOWN], 1)) + (f"\n…и ещё {extra}" if extra > 0 else "")
                    + "\n(Не каждая запись значит, что дыра достижима в твоём коде: смотри, использует ли проект уязвимую часть. Обновляй на версию из «исправлено в».)", "OSV.dev")


TOOLS = [tool("vuln_check", "Известные уязвимости в библиотеках (OSV.dev, бесплатно): пакеты по именам с версией (requests==2.19.0, npm:lodash@4.17.20) или все зависимости проекта на рабочем столе "
                            "(project: Luma, Sena; читает requirements.txt и package-lock.json). Показывает серьёзность, CVE, описание и версию с исправлением. Правок не делает.",
              {"packages": strings("Пакеты с версией: requests==2.19.0, npm:lodash@4.17.20"), "project": string("Папка проекта на рабочем столе: Luma, ASTO_portfolio")})]
FUNCTIONS = {"vuln_check": vuln_check}
