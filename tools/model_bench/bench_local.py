"""The narrow jobs on the home model instead of a cloud one: python bench_local.py <out.json> [job ...].
The same cases as bench_narrow.py; the answer comes from core/local.chat (llama-server on this Mac), as when a role moves home."""
import sys

import harness

sys.argv = [sys.argv[0], "local", sys.argv[1], *sys.argv[2:]]
setup = harness.setup


def home(model, slot):
    setup(model, slot)
    from sena.core import local, roles
    roles._cloud = lambda instructions, text, schema, purpose, effort="low": local.chat(instructions, text, schema, max_tokens=800, purpose=purpose)


harness.setup = home
import bench_narrow  # noqa: E402,F401  (runs the cases with the answers from home)
