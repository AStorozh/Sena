"""What's happening right now. Each of Tema's tasks runs in its own thread.
Steps are tagged with their performer (ag_sena, ag_web, ag_mac, ag_code) — visible in the "Tasks" section."""
import functools
import threading
import time
import uuid

from sena.core import events

local = threading.local()  # show — display the step in the task message, task — whose task, who — who's working, turn — turn number
tasks = {}                 # thread -> current task
inbox = {}                 # thread -> the owner's words for that running task, not yet seen by it (tell)
_turn = 0                  # Tema's message number: increases with every message of his (Telegram, app, voice)
_turn_lock = threading.Lock()   # two messages at once (Telegram and a voice command) get two numbers, never one


def new_turn(said=None):
    """A message of the owner's: sending messages to people takes two steps, confirmation is only possible on the next turn.
    said — his own words (not a voice that isn't his): only they can carry the "yes" that confirms a prepared action."""
    global _turn
    with _turn_lock:
        _turn += 1
        local.turn = _turn
    local.said = said


def join_turn():
    """A background turn (a scheduled job, a step of a long work) joins the conversation's latest turn: what it prepares for
    the owner's "yes" is answered by his next message, as if he had been asked just now (core/confirm.py counts turns). It
    says nothing itself: a "yes" can't come from it."""
    local.turn, local.said = _turn, None


def begin(text, service=False):
    # the id is needed right away: the cost journal uses it to record what the task cost (the same id later appears in the task journal).
    # service — a service task (cleanup, reviewing overheard speech): visible in "Processes" but not counted as experience
    task = {"id": uuid.uuid4().hex[:8], "text": text, "start": time.time(), "steps": [], "done": False, "answer": None, "service": service}
    tasks[threading.get_ident()] = task
    events.log("task_start", text, tid=task["id"], service=service or None)


def end(answer):
    # returns the finished task: whoever led it (ag_sena) does the journal entry and the review
    inbox.pop(threading.get_ident(), None)
    task = tasks.pop(threading.get_ident(), None)
    if task:
        task.update(done=True, answer=answer, end=time.time())
        events.log("task_end", answer, tid=task["id"], seconds=round(task["end"] - task["start"], 1))
    return task


def current():
    # the task of Tema's that this thread belongs to (or None: a background job outside any task)
    return tasks.get(getattr(local, "task", None) or threading.get_ident())


def carried(fn):
    """fn, to run on another thread for this thread's task: the worker gets this thread's status — the task, who works, the
    tab, the turn, a run's own state — while fn runs, and its own back after (a pool or the browser's thread works for one task
    after another). Its steps, foreign-text marks and costs then belong to the task: a fresh pool thread had none. On the
    calling thread itself fn runs as it is. A value set inside fn stays in the worker: a run that needs one back keeps it in a
    dict it owns (the copy is shallow)."""
    home = threading.get_ident()
    context = {**vars(local), "task": getattr(local, "task", None) or home}

    @functools.wraps(fn)
    def run(*args, **kwargs):
        if threading.get_ident() == home:
            return fn(*args, **kwargs)
        before = dict(vars(local))
        vars(local).clear()
        vars(local).update(context)
        try:
            return fn(*args, **kwargs)
        finally:
            vars(local).clear()
            vars(local).update(before)
    return run


def touched(source):
    """The task has read someone else's text: a site, a mail, a chat, a file, a web search, a voice that isn't the owner's.
    From here on its agents that can change the Mac (terminal, code) wait for the owner's "yes" (agents/ag_sena/tools.py)."""
    task = current()
    if task and not task.get("outside"):
        task["outside"] = source
        events.log("outside", source, tid=task["id"])


def outside():
    """What foreign text this task has read (the first source), or None."""
    task = current()
    return task.get("outside") if task else None


def show(text, who=None, upcoming=None, tool=None):
    # upcoming — the agent's own guess at the step after this one, if it already knows (shown as a dashed "planned next" line);
    # tool — what the step is done with (a tool's name, or "wait" for a model retry): the agent's character on the Mac shows it as a thought
    if not text:
        return
    f = getattr(local, "show", None)
    if f:
        f(text)
    task = current()
    who = who or getattr(local, "who", None) or "ag_sena"
    if task:
        task["steps"].append({"who": who, "text": text, "time": time.time(), **({"tool": tool} if tool else {})})
        if tool:   # the tool runs from now until tool_done: meanwhile the agent is doing, not thinking
            task.setdefault("using", {})[who] = tool
        task["next"] = upcoming or None
    events.log("step", text, who=who)


def tool_done():
    # the agent's tool has answered: from here on it thinks about the result (its character shows thinking, not the tool)
    task = current()
    if task:
        task.get("using", {}).pop(getattr(local, "who", None) or "ag_sena", None)


def trace(text):
    # a condensed trace of the work for the post-task review (full tool calls live in the event journal)
    task = current()
    if task:
        task.setdefault("trace", []).append(text)
        del task["trace"][:-60]


def failed():
    # a tool call that didn't do its job (it raised, or answered with a declared failure): the post-task review is worth it
    task = current()
    if task:
        task["failures"] = task.get("failures", 0) + 1
        who = getattr(local, "who", None) or "ag_sena"   # whose tool: its character gets angry after a few
        task.setdefault("failed_by", {})[who] = task.get("failed_by", {}).get(who, 0) + 1


def card(agent, asked, done_when, finished):
    """One delegation of the task: who was asked what, what was to count as done, how the agent finished (for the judge and the apps)."""
    task = current()
    if task:
        task.setdefault("cards", []).append({"agent": agent, "asked": asked[:500], "done_when": done_when[:300], **finished, "time": time.time()})


def record_code(code, failed=False):
    # the code the agent ran in the browser (in full): the judge can use it to save a recipe as code
    task = current()
    if task:
        task.setdefault("code", []).append({"code": code[:4000], "failed": failed})
        del task["code"][:-8]


def attach_image(jpeg):
    # the browser's final frame: the judge looks at the result with its own eyes, not just reads the agent's words
    task = current()
    if task:
        task["image"] = jpeg


def others():
    # the owner's other tasks running right now (so a new command understands what it relates to), with their ids for tell_task.
    # Service work (a review of a finished task, the night's cleanup, a news alert) is not his: offered as "his task", his
    # follow-up went to the review's brain, and a change to her own code was refused while its own review ran
    me = getattr(local, "task", None) or threading.get_ident()   # a tool runs in a pool thread: its own task is found via the thread's tag
    return [f"[{t['id']}] «{t['text']}» — сейчас: {t['steps'][-1]['text'] if t['steps'] else 'начало'}"
            for ident, t in list(tasks.items()) if ident != me and not t.get("service")]


def tell(task_id, text):
    """The owner's words for a task of his that is running (a correction, "not that video, the second one"): it reads them at its
    next step, whichever of its agents takes it. Returns the task, or None if it has already finished (or is service work)."""
    for ident, t in list(tasks.items()):
        if t["id"] == task_id and not t.get("done") and not t.get("service"):
            inbox.setdefault(ident, []).append(" ".join(str(text).split())[:2000])
            events.log("step", f"Тема дописал задаче: {text}", tid=t["id"])
            return t
    return None


def heard(ident, seen=0):
    """What the owner added for this task after the first `seen` of his words: every loop of the task (the coordinator and
    each agent it runs) keeps its own count, so a correction reaches all of them, not only the first to look (gemini-cli
    broadcasts mid-task corrections to every running agent)."""
    return list(inbox.get(ident, [])[seen:])
