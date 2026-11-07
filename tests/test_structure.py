"""Project layout: layers only depend downward, every module has a description. Catches structural drift."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "sena"

# layers: a lower one knows nothing about a higher one (README, "How it's built")
LAYERS = {"config": 0, "core": 0, "memory": 1, "world": 1, "google": 1, "scheduler": 1, "agents": 2, "telegram": 3, "phone": 3, "app": 4, "home": 4, "exam": 4}
# the outgoing Telegram channel (sending, account) is needed by agents: it sits at the google/world layer, not alongside the incoming bot
OUTGOING = {"telegram.api": 1, "telegram.account": 1}


def _level(dotted):
    name = dotted.removeprefix("sena.")
    for prefix, level in OUTGOING.items():
        if name == prefix or name.startswith(prefix + "."):
            return level
    return LAYERS.get(name.split(".")[0])


def _imports(path):
    # from sena.telegram import api -> sena.telegram.api; from sena import config -> sena.config
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "sena":
            for alias in node.names:
                yield f"{node.module}.{alias.name}"


class Structure(unittest.TestCase):
    def test_layers_depend_only_downward(self):
        wrong = []
        for path in sorted(ROOT.rglob("*.py")):
            module = "sena." + ".".join(path.relative_to(ROOT).with_suffix("").parts)
            me = _level(module)
            if me is None or path.name == "__main__.py":
                continue
            for imported in _imports(path):
                level = _level(imported)
                if level is not None and level > me:
                    wrong.append(f"{module} -> {imported}")
        self.assertEqual(wrong, [], "нижний слой импортирует верхний")

    def test_paid_services_are_called_only_from_their_accounting_places(self):
        # money must never bypass the expense log (core/costs.py): each paid service has exactly one place that calls it
        places = {"openai": "core/llm.py", "anthropic": "core/llm_claude.py", "generativelanguage.googleapis.com": "google/media.py",
                  "places.googleapis.com": "world/maps_google.py", "api.tomtom.com": "world/maps_tomtom.py",
                  "routes.googleapis.com": "world/maps_google.py", "claude\", \"-p": "agents/ag_code.py"}
        wrong = []
        for path in sorted(ROOT.rglob("*.py")):
            rel = str(path.relative_to(ROOT))
            text = path.read_text(encoding="utf-8")
            for needle, owner in places.items():
                uses = f"import {needle}" in text or f"from {needle} import" in text if needle in ("openai", "anthropic") else needle in text
                if uses and rel not in (owner, "core/costs.py"):
                    wrong.append(f"{rel}: {needle} (должно быть только в {owner})")
        self.assertEqual(wrong, [])

    def test_home_engines_are_run_only_from_the_home_gateway(self):
        # every model on this Mac goes through core/local.py: one place for its server, its accounting, its journal, its fallbacks
        needles = ("whisper-cli", "llama-server", "import Vision", "from piper", "VNRecognizeTextRequest", "from resemblyzer", f":{8766}")
        wrong = []
        for path in sorted(ROOT.rglob("*.py")):
            rel = str(path.relative_to(ROOT))
            text = path.read_text(encoding="utf-8")
            wrong += [f"{rel}: {n}" for n in needles if n in text and rel not in ("core/local.py", "config.py")]
        self.assertEqual(wrong, [])

    def test_every_path_the_rules_and_the_map_name_exists(self):
        """A rule naming a file that is gone sends whoever follows it in circles (the awesome-claude-code study found one in README)."""
        import re
        project = ROOT.parent
        for doc in ("CLAUDE.md", "README.md", "README.ru.md"):
            if not (project / doc).exists():   # the public version has no README.ru.md
                continue
            named = set(re.findall(r"`((?:sena|mac|android|tests|tools)/[\w./-]+?|run\.sh|scratchpad/[\w./-]+)`", (project / doc).read_text(encoding="utf-8")))
            missing = sorted(n for n in named if not (project / n.rstrip("/")).exists())
            self.assertEqual(missing, [], doc)

    def test_every_library_the_code_imports_is_in_requirements(self):
        """A fresh install must run: anthropic was imported by the brain but listed nowhere — it stood on the Mac by hand, and the
        first clean run (GitHub's CI) couldn't even import the tests. Libraries that come with another one are named with it."""
        import re
        import sys
        from importlib import metadata
        comes_with = {"pyobjc-framework-cocoa": "pyobjc-framework-quartz", "certifi": "requests", "lxml": "trafilatura",
                      "playwright": "patchright"}   # AppKit and Foundation come with Quartz; playwright is only patchright's fallback
        listed = {re.split(r"[<>=;\s\[]", line.strip())[0].lower() for line in (ROOT.parent / "requirements.txt").read_text(encoding="utf-8").splitlines()
                  if line.strip() and not line.lstrip().startswith("#")}
        distributions = {name: [d.lower() for d in dists] for name, dists in metadata.packages_distributions().items()}
        missing = set()
        for path in ROOT.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                        [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module else []
                for name in names:
                    top = name.split(".")[0]
                    if top in sys.stdlib_module_names or top == "sena":
                        continue
                    dists = distributions.get(top) or [top.lower()]
                    if not any(d in listed or comes_with.get(d) in listed for d in dists):
                        missing.add(f"{top} ({path.relative_to(ROOT.parent)})")
        self.assertEqual(sorted(missing), [])

    def test_a_throwaway_data_folder_never_reaches_the_keychain_or_icloud(self):
        """An audit's experiment set SENA_DATA to a temp folder and still wrote a key into the Mac's real Keychain."""
        import os
        import subprocess
        import sys
        import tempfile
        probe = "from sena import config; print(config.KEYCHAIN, config.OFFSITE_DIR)"
        env = {k: v for k, v in os.environ.items() if k not in ("SENA_KEYCHAIN", "SENA_OFFSITE")}
        with tempfile.TemporaryDirectory() as folder:
            env.update(SENA_DATA=folder, SENA_ENV=os.path.join(folder, ".env"))
            plain = subprocess.run([sys.executable, "-c", probe], env=env, cwd=ROOT.parent, capture_output=True, text=True).stdout.split()
            asked = subprocess.run([sys.executable, "-c", probe], env={**env, "SENA_KEYCHAIN": "on"}, cwd=ROOT.parent, capture_output=True, text=True).stdout.split()
        self.assertEqual(plain, ["False", "off"])
        self.assertEqual(asked[0], "True")   # asked for in so many words, it is used

    def test_unittest_discovery_can_never_load_the_config_before_the_test_sandbox(self):
        """python -m unittest imports every package it walks through before it reaches tests/: a package whose __init__ loaded
        sena.config once ran the whole suite on the real data, the real Keychain and the real notes vault."""
        import os
        import subprocess
        import sys
        import tempfile
        inits = [str(p.relative_to(ROOT)) for p in ROOT.rglob("__init__.py")
                 if any(not isinstance(node, ast.Expr) for node in ast.parse(p.read_text(encoding="utf-8")).body)]
        self.assertEqual(inits, [], "в пакетах sena только описание: их импортирует обход тестов раньше tests/__init__")
        env = {k: v for k, v in os.environ.items() if k not in ("SENA_DATA", "SENA_VAULT", "SENA_KEYCHAIN", "SENA_OFFSITE")}
        with tempfile.TemporaryDirectory() as folder:
            env["SENA_ENV"] = os.path.join(folder, ".env")   # no .env read either
            probe = "import sys; sys.argv[0] = 'python3 -m unittest'; from sena import config; print(config.DATA)"
            done = subprocess.run([sys.executable, "-c", probe], env=env, cwd=ROOT.parent, capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)   # a test run without its sandbox stops at once, before any path is chosen
        self.assertIn("python -m unittest", done.stdout + done.stderr)
        self.assertNotIn("/data", done.stdout)

    def test_every_module_has_a_description(self):
        missing = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*.py")
                   if p.stat().st_size and not ast.get_docstring(ast.parse(p.read_text(encoding="utf-8")))]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()


class Requirements(unittest.TestCase):
    """Round 2 (quality Q-22): only lower bounds — a fresh install and GitHub's CI took the newest of everything, and any major
    release (websockets 16, anthropic 1.0, numpy 3) would break Sena with no change of her own."""
    UNBOUNDED = {"cryptography", "regex"}   # numbered by date and by habit, not by breakage (requirements.txt says so)

    def test_every_library_is_held_below_its_next_major_and_the_installed_one_fits(self):
        from importlib import metadata
        from packaging.requirements import Requirement
        found = []
        for line in (ROOT.parent / "requirements.txt").read_text(encoding="utf-8").splitlines():
            line = line.split(" #")[0].strip()
            if not line or line.startswith("#"):
                continue
            req = Requirement(line)
            found.append(req.name)
            if req.name.lower() not in self.UNBOUNDED:
                self.assertTrue(any(s.operator == "<" for s in req.specifier), f"{req.name}: нет верхней границы")
            if req.marker is None or req.marker.evaluate():
                self.assertTrue(req.specifier.contains(metadata.version(req.name), prereleases=True), f"{req.name}: стоит версия вне границ")
        self.assertGreater(len(found), 10)   # the file was read, not skipped
