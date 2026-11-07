"""One home model server for every benchmark run at once: the runs find it on its port. Stopped when this process ends."""
import signal
import sys
import time

import harness  # noqa: F401  (puts Sena on the path)
from sena.core import local  # noqa: E402

local._ensure()
print("домашние модели: сервер поднят", flush=True)
signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
while True:
    time.sleep(5)
