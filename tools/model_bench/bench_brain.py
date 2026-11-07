"""The brain and the voice commands on Sena's own exam: python bench_brain.py <model> <out.json> <brain|voice> <budget> [case ids...].
brain — the coordinator's place, the owner's effort; voice — the fast path (fast=True: no reasoning, an instant reply)."""
import sys

import harness

model, out, mode, budget, only = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]), set(sys.argv[5:])
harness.setup(model, "main" if mode == "brain" else "fast")

from sena.agents.ag_sena import agent  # noqa: E402
from sena.exam import runner  # noqa: E402

agent.without_cloud = lambda *a, **k: None   # a failure of the model under test must show, not be answered by the home model
if mode == "voice":
    real_ask = agent.ask
    agent.ask = lambda chat, text, **kw: real_ask(chat, text, fast=True)

result = runner.run(only or None, budget)
result["model"], result["mode"] = model, mode
result.pop("rows", None)
result["rows"] = harness.rows()
harness.save(out, result)
