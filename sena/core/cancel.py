"""Stopping tasks. Each of Tema's tasks runs in its own thread: stop_all flags all the others,
and their agents stop working on the next step (while long actions check check_stop themselves).
A flag belongs to the task (its id from status.begin), not to its thread: a thread runs one task after another, and a flag
left on a thread's number stopped whatever task got that thread next (a stop pressed on a review stopped a later one).
A stop for a task still starting on its thread (the app knows the thread, the task has no id yet) waits for it a little."""
import threading
import time
import weakref

from sena.core import status

EARLY = 30    # seconds a stop for a task that is only starting on its thread waits for that task to begin
_early = {}   # thread number -> (the thread, when): a stop for the task that thread is still starting
_ran = weakref.WeakSet()   # threads that have finished a task: their number in an older list of the app means that task


def _early_for(key):
    # whether a stop for a starting task still stands on this thread number: the same thread, alive, and recent
    thread, when = _early.get(key, (None, 0.0))
    if thread is not None and (time.monotonic() - when > EARLY or not thread.is_alive()):
        _early.pop(key, None)
        return False
    return thread is not None


class _Flags(set):
    """The ids of the tasks the owner stopped. Asked with a thread's number — what current_task() gives and what long actions
    keep for their stop check — it answers for the task that thread runs now, so a flag ends with its task; a thread without a
    task is flagged by its own number."""

    def __contains__(self, key):
        if not isinstance(key, int):
            return set.__contains__(self, key)
        task = status.tasks.get(key)
        if key in _early and _early_for(key):
            if not task:
                return True   # still starting: it stops before it begins
            _early.pop(key, None)
            self.add(task["id"])   # the stop came while this very task was starting: it is its flag now
        return set.__contains__(self, task["id"] if task else key)


cancelled = _Flags()


class Stopped(Exception):
    pass


def current_task():
    # which of Tema's tasks this thread belongs to (agents of one task can run in parallel in different threads)
    return getattr(status.local, "task", None) or threading.get_ident()


def is_cancelled():
    return current_task() in cancelled


def check_stop():
    # call inside long actions (waiting for video, a command, code in the browser): a stop must take effect immediately
    if is_cancelled():
        raise Stopped("Остановлено по просьбе Темы")


def _tidy():
    # flags of tasks that have ended and of threads that are gone stop nothing any more
    alive = {t["id"] for t in list(status.tasks.values())} | {t.ident for t in threading.enumerate()}
    cancelled.intersection_update(alive)
    for key in list(_early):
        _early_for(key)


def new_task():
    # a task starts on this thread: a flag on the thread's own number is not its (a stop that came while it was starting is)
    cancelled.discard(threading.get_ident())


def finish_task(key):
    # the task is finished: its flags are no longer needed (key — its id, or the number of a thread without a task)
    cancelled.discard(key)
    _early.pop(threading.get_ident(), None)
    _ran.add(threading.current_thread())


def stop_all(services=False):
    # services — service tasks too (Sena is stopping); otherwise cleanup and triage are left alone
    me = current_task()
    cancelled.update(t["id"] for ident, t in list(status.tasks.items()) if ident != me and (services or not t.get("service")))
    _tidy()
    return "Все остальные задачи остановлены"


def stop_task(task):
    """Stop one task: by its id (the status task's "id", the app's tid), or by the number of the thread it runs on (older app
    builds, and a task still starting: the thread is known, the id not yet). One that has already ended says so and leaves no
    flag behind (the A2A study: "stopped" was answered for a finished task)."""
    key = str(task).strip()
    found = next((t for t in list(status.tasks.values()) if t["id"] == key), None)
    if found is None and key.isdigit():
        found = status.tasks.get(int(key))
        thread = next((t for t in threading.enumerate() if t.ident == int(key)), None)
        if found is None and thread is not None and thread not in _ran:
            _early[int(key)] = (thread, time.monotonic())   # its task is still starting: it stops as soon as it begins
            return "Задача остановлена"
    if found is None:
        return "Задача уже закончилась: останавливать нечего"
    cancelled.add(found["id"])
    _tidy()
    return "Задача остановлена"
