"""The coordinator's tools: what Sena can do herself, and which agents she hands work off to.
Anything that goes out to people (Telegram messages, emails) is irreversible, so it's a two-step process: send_message / mail_send
only prepare it, and confirm_send sends it, only on the following turn (after Tema's "yes"), never on the same one."""
import functools
import re
import subprocess
from email.utils import getaddresses

from sena import config, scheduler
from sena.agents import ag_code, ag_judge, ag_mac, checkup, common, reader, sources
from sena.agents.ag_sena import toolbox, work
from sena.agents.ag_web import agent as ag_web
from sena.core import ask_owner, attachments, cancel, confirm, costs, events, roles, safety, secrets, settings, status
from sena.core.tools import Failure, clamp, string, tool
from sena.google import auth as google_auth
from sena.google import gmail, media, youtube
from sena.google import tools as google
from sena.memory import facts, goals, heard, history, index, people, quality, recall, rules, skills, vault
from sena.telegram import account, api
from sena.world import (advisories, alerts, books, clock, countries, crypto, dictionary, finance, food, github, hireflow, luma, maps, mcp, music, net, news, pages, portfolio, pubmed, recipes, research, sec, surf, tech, travel, tv, uptime, vulns, weather, wiki)

pending = confirm.pending   # every prepared action lives in core/confirm; the tools here only prepare and name them
_hold = confirm.hold
TO_CHAT = re.compile(r"^сообщение в «(.+)»$")   # how a prepared message is named: the chat it goes to


def send_message(name, text):
    # the chat is looked up right now: Tema confirms the real name, not whatever the model called it, and the message goes to
    # exactly that chat on his "yes" — not to whatever the name matches by then (a newer group, «Мама Лена» beside «Мама»)
    try:
        found = account.chat(name)   # a kept sign-in that didn't connect when Sena started connects here (account._need)
    except RuntimeError as e:   # no sign-in, or it doesn't connect now: nothing is prepared that can't be sent
        return Failure(str(e))
    if not found:
        return Failure(f"Чат «{name}» не найден: уточни название у Темы или посмотри list_chats")
    shown, peer = found
    sid = _hold(f"сообщение в «{shown}»", text, lambda: account.send_message(shown, text, peer))
    return (f"Ещё не отправлено [{sid}]. Нашла чат «{shown}». Спроси Тему коротко: отправить в «{shown}»: «{text}»? "
            f"Он имел в виду другой чат — подготовь заново с точным названием. Отправишь через confirm_send({sid}) только после его «да».")


def _google_ready():
    # mail goes out through his Google sign-in: without one a «да» would be spent on a failure (none since the sign-in was lost)
    return None if google_auth.state() == "ok" else Failure(google_auth.HOW_TO_SIGN_IN)


ADDRESS = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;.]{2,}$")


def _not_addresses(field):
    # what in a "to" or "cc" isn't a mail address ("Игорь", "olga@", "x@y"): checked before anything is held for a "yes"
    pairs = getaddresses([str(field or "")])
    return [name or addr for name, addr in pairs if not ADDRESS.match(addr or "")] if field else []


def mail_send(to, subject, body, cc=None, reply_to_id=None):
    wrong = _not_addresses(to) + _not_addresses(", ".join(cc) if isinstance(cc, list) else cc)
    if wrong or not str(to or "").strip():   # Mastra validates a tool's input before a human is asked to approve it
        return Failure(f"Не адрес почты: {', '.join(wrong) or 'пусто'}. Найди адрес (карточка человека, find, прошлые письма — mail_search) "
                       "и подготовь письмо заново.")
    signed_out = _google_ready()
    if signed_out:
        return signed_out
    odd = [f"{addr}: {why}" for _, addr in getaddresses([str(to or ""), ", ".join(cc) if isinstance(cc, list) else str(cc or "")])
           if (why := safety.look_alike(addr))]
    warn = f" Внимание: {'; '.join(odd)} — так подделывают адреса; скажи Теме это прямо." if odd else ""
    sid = _hold(f"письмо {to}" + (" (адрес похож на подделку)" if odd else ""), f"{subject}: {body}", lambda: gmail.send(to, subject, body, cc, reply_to_id))
    return (f"Письмо ещё не отправлено [{sid}]. Покажи Теме коротко: кому ({to}), тема, суть текста — и спроси, отправлять ли.{warn} "
            f"Отправишь через confirm_send({sid}) только после его «да».")


def mail_bulk(query, action, label=""):
    """Everything a Gmail search finds, at once: prepared with the count and examples, done after the owner's "yes"."""
    signed_out = _google_ready()
    if signed_out:
        return signed_out
    found, said = gmail.bulk_plan(query, action, label)
    if not found:
        return said
    sid = _hold(f"почта: {gmail.ACTIONS[action][0]}", said, lambda: gmail.bulk_apply(found, action, label))
    return (f"Ещё не сделано [{sid}]. {said}. Скажи Теме коротко: что, сколько писем и пару примеров — и спроси, делать ли. "
            f"Сделаешь через confirm_send({sid}) только после его «да».")


def mail_filter(sender="", query="", action="archive", label=""):
    """A Gmail filter so that new mail sorts itself: prepared, created after the owner's "yes"."""
    signed_out = _google_ready()
    if signed_out:
        return signed_out
    body, said = gmail.filter_plan(sender, query, action, label)
    sid = _hold("фильтр почты", said, lambda: gmail.filter_apply(body))
    return f"Фильтр ещё не создан [{sid}]: {said}. Спроси Тему, создавать ли. Создашь через confirm_send({sid}) после его «да»."


def mail_unsubscribe(sender):
    """Unsubscribe the standard way the sender offers: the one-click link or a mail to their address — after the owner's "yes"."""
    signed_out = _google_ready()
    if signed_out:
        return signed_out
    way, said = gmail.unsubscribe_plan(sender)
    if not way:
        return said

    def run():
        if way[0] == "mail":
            return gmail.send(way[1], way[2], "unsubscribe")
        try:   # the link came in someone's mail: only a public address is dialled (world/net.py)
            reply = net.send("POST", way[1], form="List-Unsubscribe=One-Click", headers={"Content-Type": "application/x-www-form-urlencoded"},
                             who="отписка", redirects=False, public=True)
        except net.WorldError as e:
            return Failure(f"Не отписалась: {e}")
        return f"Отписалась ({reply.status_code})"
    sid = _hold("отписка от рассылки", said, run)
    return f"Ещё не отписалась [{sid}]: {said}. Спроси Тему; отпишешься через confirm_send({sid}) после его «да»."


def site_change(method, path, body=None):
    # a site edit is visible to every visitor: the site first shows "before → after", applied only after Tema's "yes"
    data = portfolio.preview(method, path, body)
    sid = _hold("правка сайта", portfolio.summary(method, path, data), lambda: portfolio.apply(method, path, body))
    return (f"Ещё не применено [{sid}]. Пробный прогон сайта (что изменится):\n{portfolio.shown(data)}\n"
            f"Покажи Теме коротко, что поменяется (было → стало), и спроси, применять ли. Применишь через confirm_send({sid}) только после его «да».")


def github_change(method, path, body=None):
    # a write to GitHub is visible to other people: first the request and the "before" state, applied only after Tema's "yes"
    plan = github.preview(method, path, body)
    sid = _hold("правка на GitHub", github.summary(method, path, plan), lambda: github.apply(method, path, body))
    return (f"Ещё не применено [{sid}]. Что будет сделано:\n{github.shown(plan)}\n"
            f"Покажи Теме коротко (что и где поменяется) и спроси, применять ли. Применишь через confirm_send({sid}) только после его «да».")


def hireflow_change(method, path, body=None):
    # personal data in Hire-flow feeds into job matching and cover letters: first "before → after", applied only after Tema's "yes"
    plan = hireflow.preview(method, path, body)
    sid = _hold("правка данных в Hire-flow", hireflow.summary(method, path, plan), lambda: hireflow.apply(method, path, body))
    return (f"Ещё не применено [{sid}]. Что будет записано:\n{hireflow.shown(plan)}\n"
            f"Покажи Теме коротко (что запишется, было → стало) и спроси, применять ли. Применишь через confirm_send({sid}) только после его «да».")


def youtube_change(action, video_id=None, title=None, description=None, tags=None, privacy=None, publish_at=None, category_id=None, path=None,
                   playlist_id=None, parent_id=None, comment_id=None, text=None, moderation=None):
    # visible to the channel's viewers: first "before → after", applied only after Tema's "yes"
    args = {k: v for k, v in dict(video_id=video_id, title=title, description=description, tags=tags, privacy=privacy, publish_at=publish_at, category_id=category_id,
                                   path=path, playlist_id=playlist_id, parent_id=parent_id, comment_id=comment_id, text=text, moderation=moderation).items() if v is not None}
    plan = youtube.preview(action, **args)
    sid = _hold("действие на YouTube", youtube.summary(action, plan), lambda: youtube.apply(action, **args))
    return (f"Ещё не применено [{sid}]. Что будет сделано:\n{youtube.shown(plan)}\n"
            f"Покажи Теме коротко и спроси, применять ли. Применишь через confirm_send({sid}) только после его «да».")


def confirm_send(id):
    done, result, item = confirm.confirm(id)
    if done:
        _note_recipient(item["name"])
        _tell_work(item, "да", result)
    return result


def confirm_cancel(id):
    """His «нет» to a prepared action: dropped with its buttons and cards (core/confirm.decline checks it is his own word), so no
    stray press of an old «Да» does it later; the long work that prepared it hears the answer."""
    item = dict(confirm.pending.get(str(id)) or {})
    result = confirm.decline(str(id))
    if item and not isinstance(result, Failure):
        _tell_work(item, "нет", result)
    return result


def _note_recipient(name):
    # the task now relates to the person the message went to (a chat name that matches a card); mail addresses don't match anyone
    chat = TO_CHAT.match(name)
    task, pid = status.current(), index.lookup("человек", chat.group(1)) if chat else None
    if task and pid:
        index.link("задача", task["id"], "человек", pid, "about")


def _tell_work(item, said, result):
    """His answer to what a long work's turn prepared — a press or his «да» in the chat — reaches that work (work.answered: a
    waiting one goes on with it). What the action answered comes fenced: it may retell a page or a mail, and the work's notes
    are read as his words."""
    outcome = safety.external(str(result)[:500], "итог подтверждённого действия", own=True)
    work.answered(item.get("tid"), f"Тема ответил «{said}» на «{item['name']}»; итог: {outcome}")


def forget_heard(person="", period=""):
    """Prepare to forget overheard lines: the only deletion of them, and only on the owner's "yes" (confirm_send on the next turn)."""
    since, until, title = None, None, "всё время"
    if period:
        try:
            since, until, title = costs.span(period)
        except ValueError as e:
            return str(e)
    if not person and not period:
        return "Назови человека или период: всё подряд не забываю"
    if person and not people.by_id(person):   # whose words, settled before his «да» is asked for (heard.forget refuses a guess)
        found = index.matches("человек", person)
        if len(found) > 1:
            return Failure(f"Под «{person}» подходят: {', '.join(people.title(r) for r in found[:8])}. Спроси Тему, кого именно")
        if not found:
            return Failure(f"Не знаю человека «{person}»: уточни у Темы, о ком речь")
    what = f"всё, что {'говорил(а) ' + person if person else 'слышала'} за {title}"
    sid = _hold(f"забыть подслушанное: {what}", "", lambda: heard.forget(person, since, until))
    return f"Ещё не забыто [{sid}]. Спроси Тему: точно забыть {what}? Это не вернуть. Подтвердишь через confirm_send({sid}) только после его «да»."


def make_image(prompt, aspect_ratio="1:1", size="1K", tier="standard", reference_images=None):
    # the image goes straight to Tema in Telegram: it's his own private chat, no confirmation needed
    path = media.generate_image(prompt, aspect_ratio, size, tier, reference_images)
    return f"Картинка готова: {path}. {api.send_media(path, prompt[:200])}."


def make_video(prompt, aspect_ratio="16:9", resolution="720p", reference_image=None):
    path = media.generate_video(prompt, aspect_ratio, resolution, reference_image)
    return f"Видео готово: {path}. {api.send_media(path, prompt[:200])}."


def send_file(path):
    # the file goes to Telegram's servers too: Sena's own data, keys and logins never do, wherever they lie (core/safety);
    # what the owner sent, what was downloaded and what she made, and his files on the Mac, do — and, as it goes to him
    # himself, her own long outputs (data/spill: a collected list, a long table) and her notes
    refused = safety.forbidden_path(path, to_owner=True)
    if refused:
        return Failure(f"Этот файл не отправляю: {refused}")
    return api.send_file(path)


def send_telegram(text):
    """Tema asked for something in his Telegram bot (a link, an address, a list): it goes there. His own request — the ban on
    writing there on her own (set_telegram_notify) is about what she sends unasked, not this; she used to refuse him, search
    his account for a chat with the bot and ask which chat to use."""
    if not text.strip():
        return Failure("Нечего отправлять")
    sent = api.send_text(text)
    return "Отправила в Телеграм-бот" if (sent or {}).get("ok") else Failure("Телеграм не принял сообщение: нет связи")


def find_files(query, limit=15):
    """Find files on the Mac by name via Spotlight — without going through the computer agent."""
    done = subprocess.run(["mdfind", "-name", query], capture_output=True, text=True, timeout=20)
    rows = [l for l in done.stdout.splitlines() if l and "/Library/" not in l and "/.Trash/" not in l][:clamp(limit, 15, 1, 50)]
    # a downloaded file is named by whoever made it: the names are fenced as data (the owner's own Mac, so the task isn't marked)
    return safety.external("\n".join(rows), "имена файлов на Mac", own=True) if rows else f"Файлов с «{query}» в имени не нашла"


pending_sends = confirm.waiting          # for the control panel
def pressed(sid, yes, chat_id=None):
    """The owner's own press of «Да» or «Нет» on a prepared action (Telegram, the apps): done or dropped, and the conversation
    keeps a line of it — the next turn knows the mail went out instead of preparing it again (the n8n study: a press never
    reached the history) — and so does the long work that prepared it. What it confirms runs as a task of its own
    (core/confirm.confirm_now). A failure comes back as words, not as an exception: Telegram's handler had nothing to say then."""
    item = dict(confirm.pending.get(sid) or {})
    said = "Да" if yes else "Нет"
    try:
        result = confirm.confirm_now(sid) if yes else confirm.cancel(sid)
    except Exception as e:
        events.error(f"Нажатие «{said}»: {item.get('name') or sid}", e)
        result = Failure(f"Не получилось: {str(e)[:300]}")
    if item:   # the whole result: a report of Claude Code cut at 300 characters lost what was done
        history.reply(chat_id or config.OWNER_ID, f"(Тема нажал «{said}» — {item['name']}: {result if yes else 'не делаю'})",
                      read="итог по кнопке" if "<<<ВНЕШНИЕ ДАННЫЕ" in str(result) else None)
        _tell_work(item, said.lower(), result)
    return result


def waiting_confirmation():
    # what waits for his «да», for the model's context: each by its id and name (a message, a mail, a reading, an agent's task)
    return [f"- [{w['id']}] {w['name']}: «{w['text'][:300]}»" for w in confirm.waiting()]


# ---------- the keeper of secrets: the model handles names and tickets, never values ----------
def secret_names():
    """What secrets the owner keeps and which agents may use them (names and notes only)."""
    rows = secrets.names()
    if not rows:
        return "Секретов пока нет: Тема заводит их в приложении, раздел «Секреты»"
    return "\n".join(f"- {r['name']}" + (f" — {r['note']}" if r["note"] else "") + f" (можно: {', '.join(r['allow']) or 'никому'}"
                     + (f"; вводится только на {', '.join(r['sites'])}" if r.get("sites") else "") + ")" for r in rows)


def secret_reveal(name):
    """Prepare to show a secret to the owner himself: on his "yes" it goes to him in Telegram, the model never sees it."""
    if name not in {r["name"] for r in secrets.names()}:
        return Failure(f"Секрета «{name}» нет. Есть: {', '.join(r['name'] for r in secrets.names()) or 'ничего'}")

    def send():
        value = secrets.reveal(name, "чат")
        if value is None:
            return f"Секрет «{name}» помечен как «никогда не показывать»"
        api.send_text(f"{name}: {value}")
        return "Отправила Теме в Телеграм, значение тебе не показываю"
    sid = _hold(f"показать секрет {name}", "", send)
    return f"Ещё не показано [{sid}]. Спроси Тему: показать «{name}»? Подтвердишь через confirm_send({sid}) только после его «да»."


def _with_secret(agent, task, secret):
    # an agent needs a secret: allowed — it gets a ticket inside its run; not yet — the owner is asked first
    if secret and secret not in {r["name"] for r in secrets.names()}:
        return Failure(f"Секрета «{secret}» нет. Есть: {', '.join(r['name'] for r in secrets.names()) or 'ничего'}")
    if secret and not secrets.allowed(secret, agent):   # the card and the button name the agent by what it is, never its code
        sid = _hold(f"разрешить {common.agent_word(agent, 1)} пароль «{secret}»", task[:120], lambda: secrets.grant(secret, agent))
        return (f"Агенту {agent} пока нельзя использовать «{secret}» [{sid}]. Спроси Тему, разрешить ли; "
                f"подтвердишь через confirm_send({sid}) после его «да» — и тогда поручи задачу заново.")
    return None


def _after_outside(agent, task, run):
    # someone else's text could have written this task: the agents that change the Mac wait for the owner's own "yes"
    source = status.outside()
    if not source:
        return None
    sid = _hold(f"поручение {common.agent_word(agent, 1)}", task[:300], run)
    return (f"Ещё не поручено [{sid}]: в этой задаче был чужой текст ({source}), а {agent} меняет файлы и запускает программы. "
            f"Спроси Тему коротко, поручать ли: «{task[:200]}». Поручишь через confirm_send({sid}) только после его «да».")


def _from_outside(what, run):
    """A standing rule or a long work asked for by a task that has read someone else's text: made only on the owner's own "yes".
    A rule is read as his order in every prompt from then on, and a work runs turn after turn with every tool: a mail that
    talks the model into either must not outlive the turn that read it. None — nothing foreign was read: made at once."""
    source = status.outside()
    if not source:
        return None
    sid = _hold(what, "", run)
    return (f"Ещё не сделано [{sid}]: в этой задаче был чужой текст ({source}). Спроси Тему коротко: {what}? "
            f"Сделаешь через confirm_send({sid}) только после его «да».")


@functools.wraps(rules.add_rule)
def add_rule(*args, **kwargs):
    text = kwargs.get("text", args[0] if args else "")
    return _from_outside(f"записать правило «{str(text)[:100]}»", lambda: rules.add_rule(*args, **kwargs)) or rules.add_rule(*args, **kwargs)


@functools.wraps(work.start)
def work_start(*args, **kwargs):
    title = kwargs.get("title", args[0] if args else "")
    return _from_outside(f"завести долгую работу «{str(title)[:80]}»", lambda: work.start(*args, **kwargs)) or work.start(*args, **kwargs)


def computer_task(task, secret="", done_when=""):
    return (_with_secret("ag_mac", task, secret) or _after_outside("ag_mac", task, lambda: ag_mac.run(task, secret, done_when))
            or ag_mac.run(task, secret, done_when))


def claude_task(project, task, level="normal", verify="", project_settings=True):
    # verify, not check: the brain takes "check" off every call as the model's own note (core/brain.py)
    def run():
        return ag_code.run(project, task, level, check=verify, project_settings=project_settings)
    return _after_outside("ag_code", f"{project}: {task}" + (f" (проверка: {verify})" if verify else ""), run) or run()


def _plain(text):
    return " ".join(str(text or "").lower().split())


def answer_agent(id, text):
    """Pass the owner's answer to the agent that asked that question (several were waiting at once). It counts as his own words
    only when it is exactly what he said this turn, before any foreign text: a retelling — or a "да" a page talked the model
    into — can't press an irreversible button for him (ag_web/guard.approve)."""
    said = getattr(status.local, "said", None)
    own = bool(said) and not status.outside() and _plain(text) == _plain(ask_owner.NAMED.sub("", said, count=1))
    question = ask_owner.deliver(text, id, own=own)
    if question:
        return f"Передала {common.agent_word(question.get('who'), 1)}"
    return Failure(f"Вопроса {id} уже нет: агент дождался или перестал ждать. Ждут: "
                   + (", ".join(f"[{q['id']}] {q['question'][:80]}" for q in ask_owner.waiting()) or "никто"))


def code_undo(project):
    """Prepare to return a project to how it was before its last Claude Code run: on the owner's "yes" it is done."""
    text, go = ag_code.undo_plan(project)
    if go is None:
        return text
    sid = _hold(f"вернуть проект {project}", text, go)
    return f"Ещё не сделано [{sid}]. {text}. Спроси Тему; подтвердишь через confirm_send({sid}) только после его «да»."


def stop_all():
    """Stop every other task of his — and a long work's turn: Sena's own service work, but his «стоп» pauses the work."""
    me = cancel.current_task()
    for ident, t in list(status.tasks.items()):
        if ident != me and t.get("work"):
            cancel.stop_task(t["id"])
    return cancel.stop_all()


def tell_task(task_id, text):
    """The owner's correction for a task that is already running: it reads it at its next step."""
    t = status.tell(task_id, text)
    return (f"Передала задаче «{t['text'][:80]}»: учтёт на следующем шаге. Ответь Теме одной фразой." if t
            else Failure(f"Задачи {task_id} уже нет, она закончилась: сделай сказанное сама, как новую просьбу."))


def browser_task(task, secret="", done_when=""):
    return _with_secret("ag_web", task, secret) or ag_web.run(task, secret, done_when)


DONE_WHEN = string("проверяемый факт, по которому видно, что сделано: «играет ролик с названием …», «файл лежит в …», «в корзине 2 товара»")
# an agent never sees this conversation: whatever the coordinator knows must be in the task itself (crewAI's delegation tools,
# translations/en.json:58 — "they know nothing about the task, so share absolutely everything you know")
ALONE = ("Агент не видит этот разговор: напиши всё словами — адрес, имена и названия, что уже пробовали и что не вышло, что именно вернуть. "
         "Не ссылайся на «тот», «второй», «как вчера» — назови сам предмет")

TOOLS = [
    toolbox.OPEN_TOOL,
    tool("list_chats", "Список последних чатов Темы в Телеграме с числом непрочитанных сообщений"),
    tool("telegram_signin", "Аккаунт Телеграма не подключён (инструменты чатов так и ответили): открыть на Маке страницу с QR-кодом — "
                            "Тема отсканирует её Телеграмом на телефоне, кода подтверждения не нужно. Сама вход не проходи и коды не вводи."),
    tool("read_chat", "Прочитать последние сообщения из чата Темы в Телеграме по названию чата",
         {"name": string("Название чата или его часть"),
          "limit": {"type": "integer", "description": "Сколько последних сообщений прочитать, по умолчанию 20"}}, ["name"]),
    tool("send_message", "Подготовить сообщение от имени Темы в чат Телеграма (только если Тема просил написать). Отправится после его подтверждения через confirm_send.",
         {"name": string("Название чата или его часть"), "text": string("Текст сообщения")}, ["name", "text"]),
    tool("mail_send", "Подготовить письмо с почты Темы (только если он просил написать). Уйдёт после его подтверждения через confirm_send. reply_to_id — id письма, на которое отвечаем.",
         {"to": string("Адрес получателя"), "subject": string("Тема (при ответе можно пустую)"), "body": string("Текст письма"),
          "cc": string("Копия, необязательно"), "reply_to_id": string("id письма из mail_search, если это ответ")}, ["to", "subject", "body"]),
    tool("mail_bulk", "Сделать сразу со всеми письмами по запросу Gmail (до 10 000): архив, прочитано, метка, снять метку, корзина. "
                      "Готовит действие с числом писем и примерами; выполнишь через confirm_send после «да» Темы.",
         {"query": string("поисковая строка Gmail: from:news@ozon.ru, category:promotions older_than:1y, label:старое"),
          "action": {**string(), "enum": list(gmail.ACTIONS)}, "label": string("метка для label / unlabel")}, ["query", "action"]),
    tool("mail_filter", "Создать фильтр Gmail, чтобы новые письма раскладывались сами: от кого или по запросу — в архив, прочитанными, в корзину, с меткой. "
                        "Через confirm_send после «да» Темы.",
         {"sender": string("адрес или часть, например ozon.ru"), "query": string("или поисковая строка Gmail"),
          "action": {**string(), "enum": list(gmail.FILTER_ACTIONS)}, "label": string("метка для label")}, ["action"]),
    tool("mail_unsubscribe", "Отписаться от рассылки стандартным способом отправителя (ссылка в один клик или письмо) — после «да» Темы.",
         {"sender": string("адрес отправителя из переписи почты")}, ["sender"]),
    tool("confirm_send", "Выполнить подготовленное действие по его id (сообщение, письмо, правка, показ секрета, поручение агенту, чтение…) — только когда "
                         "Тема в ответ подтвердил («да», «отправляй», «применяй»)", {"id": string()}, ["id"]),
    tool("confirm_cancel", "Тема отказался от подготовленного («нет», «не надо», «отмена» в ответ на твой вопрос): убрать его по id — ни сделать, "
                           "ни случайно нажать старую кнопку «Да» уже нельзя", {"id": string()}, ["id"]),
    tool("list_projects", "Папки с кодом на рабочем столе — куда может зайти claude_task. Сами проекты Темы и их статус — в Luma (project_list)"),
    tool("find_files", "Найти файлы на Mac по части имени (Spotlight): быстрее и дешевле агента компьютера. Нашла — покажи пути или отправь через send_file.",
         {"query": string("часть имени файла, например «договор»"), "limit": {"type": "integer", "description": "сколько путей, по умолчанию 15"}}, ["query"]),
    tool("claude_task", "Передать задачу по коду агенту Claude внутри папки проекта: читает и правит файлы, запускает тесты, возвращает отчёт. "
                        "Пушить и коммитить ему запрещено; остальные границы пиши в задаче явно: что не трогать, не удалять, не ставить пакеты, пока Тема не попросил именно это. "
                        "Проект Sena правится в копии и ждёт apply_change.",
         {"project": string("Точное название папки проекта из list_projects"), "task": string("Подробная задача для Claude с границами. " + ALONE),
          "level": {**string("fast — простая правка, быстро (Sonnet); normal — обычная работа; deep — сложное: архитектура, трудная ошибка, большой проект (сильнейшая модель, глубже думает)"),
                    "enum": ["fast", "normal", "deep"]},
          "verify": string("команда проверки в папке проекта, успех которой значит «сделано»: тесты, сборка (npm test, pytest, make). "
                           "После Claude она запустится сама; не прошла — её вывод уйдёт ему на починку. Не знаешь — пусто"),
          "project_settings": {"type": "boolean", "description": "false — без собственных настроек Claude Code этого проекта (хуки, env, "
                                                                 "его CLAUDE.md): когда Тема не разрешил их. По умолчанию true"}}, ["project", "task"]),
    tool("answer_agent", "Передать ответ Темы агенту, который ждёт его на свой вопрос (когда ждут несколько агентов сразу)",
         {"id": string("id вопроса из списка ждущих"), "text": string("ответ Темы его словами")}, ["id", "text"]),
    tool("code_undo", "Вернуть проект к виду до последней задачи Claude Code (снимок файлов до правки): покажет, что изменится, и сделает после «да» Темы",
         {"project": string("Точное название папки проекта")}, ["project"]),
    tool("computer_task", "Передать задачу агенту компьютера: файлы, папки, программы, громкость, запуск проектов через терминал MacBook Темы, "
                          "Chrome у Темы на экране — открыть страницу, включить видео, закрыть или переключить вкладки. "
                          "Не для сайтов (browser_task) и не для правки кода проектов (claude_task). Нужен пароль Темы (сервер, архив) — secret=имя из secret_names: "
                          "агент получит его в переменной окружения, значение никто не увидит.",
         {"task": string("Подробная задача для агента компьютера. " + ALONE), "secret": string("имя секрета, если он нужен для дела"), "done_when": DONE_WHEN}, ["task"]),
    tool("browser_task", "Передать задачу агенту браузера: сайты в его собственном браузере — читать, кликать, заполнять, скачивать, входить (hh.ru, маркетплейсы, любые сайты). "
                         "Это отдельный браузер, не тот Chrome, что у Темы на экране: открыть Теме страницу, включить ему видео или музыку, "
                         "закрыть или переключить его вкладки — computer_task. Не для Gmail, Google Таблиц и Диска (у них свои инструменты) и не для файлов на Mac (computer_task). Нужен пароль для входа — secret=имя из secret_names: "
                         "агент подставит его сам, значение никто не увидит.",
         {"task": string("Подробная задача для агента браузера. " + ALONE), "secret": string("имя секрета, если он нужен для входа"), "done_when": DONE_WHEN}, ["task"]),
    tool("secret_names", "Какие пароли и коды хранит Тема у хранителя секретов и каким агентам они разрешены (имена и заметки, без значений)."),
    tool("secret_reveal", "Тема просит показать свой пароль: после его «да» (confirm_send) значение уйдёт ему лично в Телеграм; тебе оно не показывается.",
         {"name": string("имя секрета из secret_names")}, ["name"]),
    tool("add_task_comment", "Записать комментарий или поправку Темы к выполненной задаче (id из списка задач в контексте), чтобы в следующий раз сделать лучше",
         {"task_id": string(), "comment": string("Суть замечания Темы одной-двумя фразами")}, ["task_id", "comment"]),
    tool("stop_all", "Остановить все остальные задачи, которые сейчас выполняются (агенты прекратят работу на следующем шаге)"),
    tool("tell_task", "Передать уточнение Темы задаче, которая уже идёт (id из «Параллельно уже выполняются»): «не тот ролик, второй», «сделай фон светлее». "
                      "Задача и её агенты учтут его на следующем шаге; новую задачу ради этого не начинай.",
         {"task_id": string("id задачи из списка"), "text": string("слова Темы для неё, как он сказал")}, ["task_id", "text"]),
    *people.TOOLS,
    *facts.TOOLS,
    *rules.TOOLS,
    *goals.TOOLS,
    *work.TOOLS,
    *settings.TOOLS,
    *roles.TOOLS,
    *ag_code.TOOLS,
    *quality.TOOLS,
    tool("telegram_code",
         ("Выполнить Python-код с доступом к Телеграм-аккаунту Темы только на чтение: tg.dialogs(limit) — чаты; tg.messages(chat, limit, search, before) — "
          "сообщения чата (chat — часть названия; search — слово; before — дата datetime); tg.search_all(query, limit) — поиск по всем чатам; "
          "tg.participants(chat, limit) — участники; tg.me() — сам Тема. Возвращают простые словари (id, chat, date, sender, text). "
          "Результат выводи через print; код работает не дольше двух минут. Написать людям — только send_message."),
         {"code": string("Python-код")}, ["code"]),
    tool("send_file", "Прислать Теме файл или картинку с компьютера в его Telegram-бот", {"path": string("Полный путь к файлу")}, ["path"]),
    tool("send_telegram", "Тема сам просит прислать ему что-то в Telegram-бот (ссылку, адрес, текст) — отправь сразу, без вопросов. "
                          "Запрет писать туда по своей инициативе (set_telegram_notify) на его прямую просьбу не действует. "
                          "Это бот Сены, а не его аккаунт: чат с ботом в аккаунте искать не нужно.",
         {"text": string("Что прислать, как Тема просил")}, ["text"]),
    tool("view_file", "Открыть файл с компьютера: картинку увидишь, документы Word, PDF, таблицы xlsx, книги EPUB и FB2 (по главам), презентации PPTX (по слайдам) и текстовые файлы прочитаешь. Для файлов из переписки (путь в записке «сохранён:»), почты и Диска. "
                      "Длинный текст приходит частями: в начале сказано, какая это часть из скольких; следующую читай с part. "
                      "Сюда же сохраняется длинный вывод инструментов (путь в нём указан): дочитывай его так, а не запрашивай заново.",
         {"path": string("Полный путь к файлу"), "part": {"type": "integer", "description": "какую часть длинного текста читать, с 1 (по умолчанию 1)"},
          "find": string("слово или фраза: вместо части — где это в файле (часть и строка), чтобы открыть нужную часть сразу")}, ["path"]),
    skills.READ_TOOL,
    *reader.TOOLS,
    *sources.TOOLS,
    *recall.TOOLS,
    tool("forget_heard", "Забыть подслушанное вокруг Темы — реплики человека и/или за период — только когда Тема прямо это попросил. "
                         "Подготовка: само удаление — через confirm_send после его «да». Иначе услышанное хранится всегда.",
         {"person": string("имя человека; пусто — все"), "period": string("today, yesterday, week, month, ГГГГ-ММ или ГГГГ-ММ-ДД..ГГГГ-ММ-ДД; пусто — всё время")}),
    *scheduler.TOOLS,
    *google.TOOLS,
    *vault.TOOLS,
    *media.TOOLS,
    *weather.TOOLS,
    *surf.TOOLS,
    *finance.TOOLS,
    *crypto.TOOLS,
    *alerts.TOOLS,
    *news.TOOLS,
    *pages.TOOLS,
    *checkup.TOOLS,
    *tech.TOOLS,
    *wiki.TOOLS,
    *food.TOOLS,
    *books.TOOLS,
    *pubmed.TOOLS,
    *countries.TOOLS,
    *recipes.TOOLS,
    *clock.TOOLS,
    *uptime.TOOLS,
    *music.TOOLS,
    *dictionary.TOOLS,
    *sec.TOOLS,
    *tv.TOOLS,
    *advisories.TOOLS,
    *research.TOOLS,
    *vulns.TOOLS,
    *maps.TOOLS,
    *travel.TOOLS,
    *luma.TOOLS,
    *mcp.TOOLS,
    *portfolio.TOOLS,
    *github.TOOLS,
    *hireflow.TOOLS,
    *youtube.TOOLS,
    *costs.TOOLS,
]

# the model's own web search: it runs on the model provider's side (core/llm_claude.py passes it on), we have no function for it
BUILTIN = [{"type": "web_search"}]

# Integrations Tema turns off from the control panel (settings.use_*): flag -> tools that disappear for the model
INTEGRATION_TOOLS = {
    # confirm_send is not Telegram's: mail, the site, GitHub and YouTube wait for it too — with the account off, a typed «да»
    # confirmed nothing (the AG2 study); send_message stays behind the switch, so nothing new becomes reachable
    "use_telegram_account": {"list_chats", "read_chat", "send_message", "telegram_signin"},
    "use_browser": {"browser_task"},
    "use_claude": {"claude_task", "list_projects", "code_undo"},
    "use_gemini": {"make_image", "make_video"},
    "use_maps": {"place_search", "route"},
    "use_travel": {"flight_prices", "flight_ideas"},
    "use_luma": {"luma", "luma_tools"},
    "use_mcp": {"mcp_tools", "mcp_call"},
    "use_site": {"site_overview", "site_read", "site_stats", "site_link_create", "site_change"},
    "use_hireflow": {"hireflow_overview", "hireflow_read", "hireflow_do", "hireflow_change", "hireflow_search"},
    "use_github": {"github_overview", "github_read", "github_change"},
    "use_youtube": {"youtube_channel", "youtube_videos", "youtube_video", "youtube_comments", "youtube_stats", "youtube_change"},
}
GOOGLE_PREFIXES = ("mail_", "sheet_", "drive_", "google_")


def integration_of(name):
    """The integration flag a tool belongs to; None — the tool is always available."""
    for flag, names in INTEGRATION_TOOLS.items():
        if name in names:
            return flag
    return "use_google" if name.startswith(GOOGLE_PREFIXES) else None


def available():
    """The model's tools, accounting for disabled integrations: whatever's off, it can't see or call."""
    current = settings.load()
    return [t for t in TOOLS if current.get(integration_of(t["name"]), True)]


def for_brain(at_hand=()):
    """What the coordinator gets: the switched-on tools, all but the core ones deferred to their groups, and the built-in search.
    at_hand — tools this turn has without opening their group."""
    return toolbox.specs(available(), at_hand) + BUILTIN

FUNCTIONS = {
    "open_tools": toolbox.open_tools,
    "list_chats": account.list_chats,
    "telegram_signin": account.sign_in,
    "read_chat": account.read_chat,
    "send_message": send_message,
    "mail_send": mail_send,
    "mail_bulk": mail_bulk,
    "mail_filter": mail_filter,
    "mail_unsubscribe": mail_unsubscribe,
    "confirm_send": confirm_send,
    "confirm_cancel": confirm_cancel,
    "list_projects": ag_code.list_projects,
    "find_files": find_files,
    "claude_task": claude_task,
    "code_undo": code_undo,
    "answer_agent": answer_agent,
    "computer_task": computer_task,
    "browser_task": browser_task,
    "secret_names": secret_names,
    "secret_reveal": secret_reveal,
    "add_task_comment": ag_judge.comment,
    "stop_all": stop_all,
    "tell_task": tell_task,
    "telegram_code": account.telegram_code,
    "send_file": send_file,
    "send_telegram": send_telegram,
    "view_file": attachments.view,
    "read_skill": skills.read_skill,
    **reader.FUNCTIONS,
    **sources.FUNCTIONS,
    **recall.FUNCTIONS,
    "forget_heard": forget_heard,
    **people.FUNCTIONS,
    **facts.FUNCTIONS,
    **rules.FUNCTIONS,
    "add_rule": add_rule,
    **goals.FUNCTIONS,
    **work.FUNCTIONS,
    "work_start": work_start,
    **settings.FUNCTIONS,
    **roles.FUNCTIONS,
    **ag_code.FUNCTIONS,
    **quality.FUNCTIONS,
    **scheduler.FUNCTIONS,
    **google.FUNCTIONS,
    **vault.FUNCTIONS,
    "make_image": make_image,
    "make_video": make_video,
    **weather.FUNCTIONS,
    **surf.FUNCTIONS,
    **finance.FUNCTIONS,
    **crypto.FUNCTIONS,
    **alerts.FUNCTIONS,
    **news.FUNCTIONS,
    **pages.FUNCTIONS,
    **checkup.FUNCTIONS,
    **tech.FUNCTIONS,
    **wiki.FUNCTIONS,
    **food.FUNCTIONS,
    **books.FUNCTIONS,
    **pubmed.FUNCTIONS,
    **countries.FUNCTIONS,
    **recipes.FUNCTIONS,
    **clock.FUNCTIONS,
    **uptime.FUNCTIONS,
    **music.FUNCTIONS,
    **dictionary.FUNCTIONS,
    **sec.FUNCTIONS,
    **tv.FUNCTIONS,
    **advisories.FUNCTIONS,
    **research.FUNCTIONS,
    **vulns.FUNCTIONS,
    **maps.FUNCTIONS,
    **travel.FUNCTIONS,
    **luma.FUNCTIONS,
    **mcp.FUNCTIONS,
    **portfolio.FUNCTIONS,
    "site_change": site_change,
    **github.FUNCTIONS,
    **hireflow.FUNCTIONS,
    "hireflow_change": hireflow_change,
    "github_change": github_change,
    **youtube.FUNCTIONS,
    "youtube_change": youtube_change,
    **costs.FUNCTIONS,
}


CAPTIONS = {
        "list_chats": "Смотрю чаты в Телеграме",
        "read_chat": "Читаю чат «{name}»",
        "send_message": "Готовлю сообщение в «{name}»",
        "confirm_send": "Делаю, что ты подтвердил",
        "confirm_cancel": "Убираю то, от чего ты отказался",
        "mail_send": "Готовлю письмо",
        "mail_search": "Смотрю почту",
        "mail_read": "Читаю письмо",
        "mail_modify": "Навожу порядок в почте",
        "mail_trash": "Убираю письма в корзину",
        "mail_census": "Переписываю почту: кто сколько пишет",
        "mail_bulk": "Готовлю массовое действие с почтой",
        "mail_filter": "Готовлю фильтр почты",
        "mail_unsubscribe": "Готовлю отписку от рассылки",
        "mail_attachment": "Беру вложение письма",
        "sheet_read": "Читаю таблицу",
        "sheet_write": "Записываю в таблицу",
        "sheet_create": "Создаю таблицу",
        "weather": "Смотрю погоду: {place}",
        "surf_forecast": "Смотрю прогноз волн",
        "exchange_rate": "Смотрю курс валют",
        "crypto_price": "Смотрю цены криптовалют",
        "stock_quote": "Смотрю котировки",
        "market_summary": "Собираю сводку рынков",
        "crypto_candles": "Смотрю историю цены и объёмов",
        "crypto_derivatives": "Смотрю фьючерсы",
        "fear_greed": "Смотрю индекс страха и жадности",
        "price_alert": "Ставлю сигнал по цене",
        "news_digest": "Смотрю главные новости",
        "news_search": "Ищу в новостях",
        "news_read": "Читаю статью целиком",
        "news_watch": "Ставлю слежку за новостями",
        "news_watches": "Смотрю, за чем слежу в новостях",
        "news_unwatch": "Снимаю слежку за новостями",
        "system_check": "Проверяю систему",
        "hacker_news": "Смотрю Hacker News",
        "arxiv_search": "Ищу научные работы",
        "wiki": "Читаю Википедию",
        "product_lookup": "Ищу состав продукта",
        "book_search": "Ищу книги",
        "health_research": "Читаю научные работы",
        "stackoverflow": "Ищу ответ на Stack Overflow",
        "huggingface": "Смотрю Hugging Face",
        "country_info": "Смотрю справку о стране",
        "country_economy": "Смотрю экономику страны",
        "on_this_day": "Смотрю, что было в этот день",
        "recipe": "Ищу рецепт",
        "world_time": "Смотрю время в мире",
        "check_website": "Проверяю сайт",
        "service_status": "Смотрю статусы сервисов",
        "defi": "Смотрю DeFi",
        "music_search": "Ищу в музыкальной базе",
        "word_info": "Смотрю слово в словаре",
        "sec_filings": "Смотрю отчёты компании",
        "sec_financials": "Смотрю цифры из отчётов",
        "tv_show": "Смотрю сериал",
        "travel_advice": "Смотрю безопасность страны",
        "research_search": "Ищу научные работы",
        "vuln_check": "Проверяю уязвимости",
        "place_search": "Ищу места на карте",
        "route": "Строю маршрут",
        "costs_report": "Считаю расходы",
        "add_rule": "Записываю правило",
        "set_style": "Меняю свои настройки",
        "set_telegram_notify": "Меняю свои настройки",
        "set_role": "Меняю свои настройки",
        "apply_change": "Готовлю применение правки своего кода",
        "drop_change": "Отклоняю правку",
        "quality_report": "Смотрю, как справляюсь",
        "remove_rule": "Убираю правило",
        "find": "Ищу в памяти",
        "tag": "Отмечаю, к чему это относится",
        "forget_heard": "Готовлю забывание подслушанного",
        "secret_names": "Смотрю, какие секреты есть",
        "secret_reveal": "Готовлю показ секрета тебе лично",
        "flight_prices": "Смотрю цены на билеты",
        "flight_ideas": "Ищу, куда дёшево улететь",
        "make_image": "Рисую картинку",
        "make_video": "Снимаю видео: это занимает минуты",
        "note_read": "Читаю заметку",
        "note_write": "Записываю в заметки",
        "note_list": "Смотрю заметки",
        "drive_search": "Ищу на Диске",
        "drive_download": "Скачиваю файл с Диска",
        "telegram_code": "Работаю с Телеграмом",
        "send_file": "Отправляю тебе файл",
        "send_telegram": "Отправляю тебе в Телеграм",
        "view_file": "Открываю файл",
        "read_long": "Читаю длинный текст целиком",
        "read_sources": "Читаю источники целиком",
        "list_projects": "Смотрю список проектов",
        "find_files": "Ищу файлы на Mac",
        "computer_task": "Компьютер взял задачу",
        "browser_task": "Браузер взял задачу",
        "luma": "Работаю с Luma",
        "luma_tools": "Смотрю, что умеет Luma",
        "site_overview": "Смотрю сайт-портфолио",
        "site_read": "Читаю сайт-портфолио",
        "site_stats": "Смотрю статистику сайта",
        "site_link_create": "Делаю ссылку для отклика",
        "site_change": "Готовлю правку сайта",
        "hireflow_overview": "Смотрю Hire-flow",
        "hireflow_read": "Читаю Hire-flow",
        "hireflow_do": "Работаю в Hire-flow",
        "hireflow_change": "Готовлю правку в Hire-flow",
        "hireflow_search": "Ищу вакансии на hh.ru",
        "github_overview": "Смотрю GitHub",
        "github_read": "Читаю GitHub",
        "github_change": "Готовлю правку на GitHub",
        "youtube_channel": "Смотрю канал YouTube",
        "youtube_videos": "Смотрю видео канала",
        "youtube_video": "Смотрю видео",
        "youtube_comments": "Читаю комментарии",
        "youtube_stats": "Смотрю статистику канала",
        "youtube_change": "Готовлю действие на YouTube",
        "forget": "Забываю",
        "read_skill": "Читаю навык",
        "schedule": "Ставлю в расписание",
        "unschedule": "Убираю из расписания",
        "list_alerts": "Смотрю сигналы",
        "remove_alert": "Снимаю сигнал",
        "mail_labels": "Смотрю метки почты",
        "sheet_info": "Смотрю таблицу",
        "sheet_clear": "Очищаю ячейки",
        "name_person": "Обновляю карточку человека",
        "note_person": "Обновляю карточку человека",
        "merge_people": "Склеиваю карточки",
        "remember": "Запоминаю",
        "work_start": "Завожу долгую работу",
        "work_plan": "Обновляю план работы",
        "work_done": "Отмечаю работу готовой",
        "work_need": "Готовлю вопрос тебе по работе",
        "work_control": "Меняю ход долгой работы",
        "work_list": "Смотрю долгие работы",
        "goal_add": "Записываю цель",
        "goal_update": "Отмечаю, как идёт цель",
        "goal_list": "Смотрю цели",
        "google_signin": "Открываю вход в Google",
        "telegram_signin": "Открываю вход в Телеграм",
        "mcp_tools": "Смотрю серверы MCP",
        "mcp_call": "Работаю с сервером MCP",
        "stop_all": "Останавливаю все задачи",
        "tell_task": "Передаю уточнение идущей задаче",
        "code_undo": "Готовлю возврат проекта к виду до правки",
        "answer_agent": "Передаю ответ агенту",
        "page_watch": "Начинаю следить за страницей",
        "page_watches": "Смотрю, за какими страницами слежу",
        "page_unwatch": "Убираю слежку за страницей",
        "add_task_comment": "Запоминаю замечание к задаче",
}


def describe(name, args):
    # what to show Tema when the coordinator calls a tool
    if name == "claude_task":
        return "Правлю свой код в копии: это займёт несколько минут" if args.get("project") == config.ROOT.name else f"Правлю код в проекте {args.get('project')}: это может занять несколько минут"
    if name == "confirm_send":   # it does whatever was prepared: a code change, a reading, a message — named by what it is
        held = (confirm.pending.get(str(args.get("id"))) or {}).get("name")
        return f"Делаю, что ты подтвердил: {held}" if held else CAPTIONS[name]
    caption = CAPTIONS.get(name)
    if caption and "{" in caption:   # captions with an argument: "Reading chat «{name}»"
        try:
            return caption.format(**{k: str(v) for k, v in args.items()})
        except (KeyError, IndexError):
            return caption.split(" «")[0].split(":")[0]
    return caption
