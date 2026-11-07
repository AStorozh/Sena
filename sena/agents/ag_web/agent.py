"""ag_web — the browser agent: Tema's Chrome via Playwright. Sees the page as a list of elements and a screenshot,
clicks like a human, solves captchas, runs site recipes from skills."""
import uuid

from sena import config
from sena.agents import common
from sena.agents.ag_web import browser, guard, recipes, toolkit, view
from sena.core import ask_owner, brain, cancel, events, safety, sandbox, status
from sena.core.tools import CHECK, LOOK_STATUS, NEXT, Failure, doing, image, string, tool
from sena.memory import lessons, skills

# a long print can't be paged here (no view_file): the agent prints less instead (SWE-agent's advice for long outputs)
PRINT_LESS = "Весь вывод сохранён, но тебе его не открыть: печатай меньше — только нужные поля, срез [:20], поиск по тексту страницы"
RUN_LIMIT = 1800   # seconds one task of the browser agent may take, all its steps together (an owner's question can wait up to 3 min each)
EFFORT = "medium"  # how deeply it thinks: low / medium / high. Speed comes from doing several steps in one call, but it still needs to think properly
ROLE = """Ты ag_web — агент браузера в системе Сены (ag_sena), работаешь для Темы. Управляешь его браузером Chrome через Playwright (Python, синхронный режим).
В коде доступны: page — текущая вкладка, context — весь браузер (context.pages, context.new_page()).
Смотреть на страницу:
- кнопки, ссылки, поля: print(elements(page)) — пронумерованный список видимых элементов «[5]<button> Войти».
  Показывает и вложенные окна страницы (группа «вложенное окно», номера вида f1.7: оплата, чаты, виджеты), и открытые теневые блоки. Сам ждёт загрузки и закрывает баннеры cookies.
  Кликать и вводить по номеру: click(page, 5), fill(page, 7, "текст"), click(page, "f1.7"). Это работает и во вложенных окнах, а page.click('[data-sena-id="5"]') видит только главное. Звёздочка * — элемент появился после прошлого вызова;
- текст: print(read_page(page)) — заголовок, адрес и читаемый текст страницы вместе со вложенными окнами; длинная страница — по частям: read_page(page, start=…), как подскажет её начало;
- глазами: инструмент look — скриншот вкладки в CSS-пикселях (координаты на картинке = координаты для клика), на нём подписаны номера из elements.
  marks=false — чистый кадр, когда надо оценить само изображение: видео, реклама, цвета. Когда по тексту непонятно: видео, реклама, ошибки, капча.
Скорость: если для сайта есть навык (read_skill) — выполни его шаги одним вызовом browser_code подряд и проверь результат один раз в конце.
Несколько уверенных действий тоже делай одним вызовом, не трать ход на каждый клик. Проверяй по фактам: elements, текст, адрес, заголовок.
Как человек: на сайтах с защитой, входом, оплатой, капчей действуй через human_click(page, 5), human_type(page, 7, "текст"),
human_scroll(page, 600), human_click_at(page, x, y) — мышь идёт по кривой, печать с паузами. На простых сайтах — обычные page.click / page.fill, они быстрее.
Капча: print(captcha(page)) скажет, есть ли она. print(solve_captcha(page)) решает её сама: галочка, картинки reCAPTCHA (до 8 раундов), клики как человек.
Не решилась — ask_tema: Тема решит в окне браузера и ответит, после этого продолжай.
Если сайт просит войти: в задаче есть secret() — введи его сам (fill(page, n, secret())); нет — ask_tema с просьбой войти в открытом окне браузера, затем продолжай.
У каждой задачи своя вкладка (page); другие задачи работают в своих вкладках, их действия идут вперемежку с твоими.
Если задача про то, что уже открыто («пропусти рекламу», «поставь на паузу», «следующий трек») — найди вкладку среди context.pages по адресу и заголовку.
Перед тем как включить музыку или видео, проверь context.pages: если где-то уже играет другое видео — поставь его на паузу.
События браузера приходят строкой «[событие]» в списке элементов или отдельным блоком после вывода: диалоги, скачанные файлы, новые вкладки.
Диалог alert принимается сам; confirm и prompt по умолчанию отклоняются, чтобы принять — allow_dialog(page) ДО нажатия (allow_dialog(page, True, "текст") — для prompt).
Скачать файл по клику: print(download_file(page, 5)) — вернёт путь к файлу. Загрузить файл на сайт: upload(page, 3, "/полный/путь") — подходит и для поля файла, и для кнопки «Выбрать файл».
Клик открыл новую вкладку — переходи в неё: use_tab(context.pages[-1]), следующий вызов кода начнётся уже там.
Заметки по сайту лежат в навыках site-<домен через дефис> (например site-hh-ru): если такой есть для твоего сайта — прочитай до начала работы.
Один вызов кода — до 2 минут, иначе его оборвут; ожидание элемента ограничено 5 секундами; time.sleep — не дольше 10 секунд за раз. Долгих ожиданий не ставь: лучше несколько коротких вызовов.
Впереди необратимое — оплата, отправка сообщения, публикация, удаление, отклик на вакансию, запись или бронь — без «да» Темы (ask_tema) не делай.
YouTube: print(youtube_search_play(page, "запрос")) — одним вызовом ставит на паузу другие вкладки YouTube, ищет, открывает первый ролик, запускает, пропускает рекламу
и возвращает название. Для уже открытого ролика — youtube_play(page). Не трать отдельные ходы на проверку вкладок и поиск.
Закончи вызовом finish: outcome (done — сделано и проверено, partial, failed, need_owner), итог и факт, который это подтверждает (название, адрес, текст на странице).
Если в задаче сказано «Готово, когда …» — в evidence дай именно этот факт, увиденный тобой."""

TOOLS = [
    tool("browser_code", "Выполнить Python-код с доступом к браузеру: переменные page и context. Результат выводи через print.",
         {"check": CHECK, "status": doing("«Открываю YouTube», «Пропускаю рекламу», «Не получилось, пробую иначе»"), "next": NEXT, "code": string("Python-код")},
         ["check", "status", "code"]),
    tool("look", "Посмотреть на текущую вкладку: скриншот с подписанными номерами элементов",
         {"check": CHECK, "status": LOOK_STATUS,
          "marks": {"type": "boolean", "description": "Подписать номера элементов на картинке (по умолчанию да); false — чистый кадр"}},
         ["check", "status"]),
    tool("ask_tema", "Прислать Теме скриншот и вопрос и дождаться ответа (до 3 минут): капча не поддаётся, нужен вход или код из СМС, "
                     "либо впереди необратимое — оплата, отправка сообщения, публикация, удаление, отклик на вакансию, бронь. Без его «да» такое не делай: "
                     "кнопка оплаты, отправки, отклика, брони не нажмётся, пока он не разрешит именно её (approve).",
         {"check": CHECK, "question": string("Что нужно от Темы, одной фразой"),
          "approve": string("для необратимого: надпись кнопки, которую нажмёшь после его «да» (например «Оплатить 2 300 ₽»); разрешение — на одно нажатие")},
         ["check", "question"]),
]


def _run_code(code):
    cancel.check_stop()
    page = browser.get_page()
    try:
        # we touch the window only if the agent's tab isn't active: in a hidden tab YouTube won't let you skip the ad.
        # If it's on screen, we don't disturb Tema.
        if page.evaluate("document.visibilityState") == "hidden":
            page.bring_to_front()
    except Exception as e:
        events.error("Переключение вкладки на передний план", e)
    from sena.core import secrets
    env = toolkit.helpers(page)
    ticket = _run().get("ticket")   # on the browser's thread: the run's own state came with the call (status.carried)

    def secret():
        # the allowed secret for a login field: used in code, wiped from any output, and typed only on its own sites
        if not ticket:
            return ""
        why = secrets.off_site(ticket[0], page.url)
        if why:
            raise PermissionError(why)
        return secrets.resolve(ticket[1]) or ""
    env["secret"] = secret
    before = browser.header(page)
    output = sandbox.run(code, env, "браузер: страницы сайтов", how=PRINT_LESS)
    failed = isinstance(output, Failure)
    status.record_code(code, failed=failed)   # in full: the judge will save a recipe from it
    notes = browser.take_notes(page)
    now = browser.get_page()
    after = browser.header(now)
    if after != before:   # the page, its title or its status changed: said, not left to guesswork (Playwright MCP, only on change)
        notes.append(f"[страница] {after[0][:200]} — «{after[1][:120]}»" + (f", HTTP {after[2]}" if after[2] else ""))
    if notes:   # dialogs, downloaded files, new tabs; the dialog's text comes from the page, so it's "external data" too
        output += "\n" + safety.external("\n".join(notes), "события браузера: диалоги, загрузки, вкладки")
    return Failure(output) if failed else output


def browser_code(code):
    return browser.in_browser(_run_code, code)


def look(marks=True):
    jpeg, width, height, url, drawn = browser.in_browser(lambda: view.screenshot(browser.get_page(), marks))
    caption = f"Скриншот {width}x{height}, адрес {url[:120]}" + (f"; на картинке подписано номеров: {drawn}" if drawn else "")
    return [{"type": "input_text", "text": caption}, image(jpeg)]


def _run():
    # this run's own state ({"ticket", "unanswered"}): one dict the run owns, shared by reference with every step's thread — a
    # value a step wrote into its thread's locals died with that thread (status.carried copies them in, never back)
    return getattr(status.local, "web_run", None) or {}


def ask_tema(question, approve=""):
    # screenshot + question for Tema, wait for his answer (ask_owner.deliver will intercept Tema's next message).
    # approve — the words of a button that can't be taken back: his own plain "yes" lets the agent press exactly it, once.
    # One unanswered question is enough for a run: he isn't there, pinging again only adds messages (AG2's study)
    if _run().get("unanswered"):
        return Failure("Тема уже не ответил на вопрос в этой задаче: не спрашивай снова — закончи finish с need_owner и тем, что от него нужно")
    shot = browser.in_browser(lambda: browser.get_page().screenshot(type="jpeg", quality=70))
    answer = ask_owner.ask(f"Нужна помощь в браузере: {question}" + (f"\n(Нажать «{approve}»? Ответь «да» — нажму один раз.)" if approve else ""), shot)
    if isinstance(answer, Failure):
        _run()["unanswered"] = 1
        return answer
    said = answer.removeprefix(ask_owner.OWN) if answer.startswith(ask_owner.OWN) else ""   # his own words, not a retelling
    if approve and said and guard.approve(cancel.current_task(), approve, said):
        return answer + f"\nРазрешено нажать «{approve}» один раз."
    return answer


def _show_result_to_judge():
    # the tab's final frame: the post-task review will judge the result by eye, not just by the agent's words. Behind other
    # tasks' calls it is waited for only briefly: the card goes to the coordinator, the review does without the frame
    try:
        jpeg = browser.in_browser(browser.final_frame, wait=browser.FRAME_WAIT)
        if jpeg:
            status.attach_image(jpeg)
    except (cancel.Stopped, browser.Busy):   # a stopped task needs no frame; a busy browser isn't a failure of this one
        pass
    except Exception as e:
        events.error("Итоговый кадр браузера", e)


def run(task, secret="", done_when=""):
    # secret — the name of an owner's secret this task may use: the code gets secret() for a login field, the model never sees the value
    from sena.core import secrets
    previous = (getattr(status.local, "who", None), getattr(status.local, "tab", None), getattr(status.local, "web_run", None))
    ticket = None
    if secret:
        ticket = secrets.use(secret, "ag_web", task)
        if not ticket:
            return Failure(f"Секрет «{secret}» агенту браузера не разрешён — попроси Тему разрешить его через координатора")
        task = f"{task}\n\nПароль «{secret}» доступен в коде как secret(): подставляй вызов в поле ввода, не печатай и не копируй значение."
    status.local.who = "ag_web"
    status.local.web_run = {"ticket": (secret, ticket) if ticket else None, "unanswered": 0}
    # the tab belongs to this run for its whole duration, whatever threads its steps run on; a thread's number would hand the
    # last run's tab (its music) to the next run on that thread
    status.local.tab = uuid.uuid4().hex
    try:
        # the fast path: a ready-made code recipe; if it doesn't work, the regular agent takes over, knowing the recipe is broken
        answer, note = recipes.try_fast(task)
        if answer is not None:
            status.show("Готово")
            status.card("ag_web", task, done_when, {"status": "done", "result": "по готовому рецепту", "evidence": answer})   # the recipe checked itself
            return answer
        if note:
            task = f"{task}\n\n{note}"
        answer = brain.run(config.MODEL_FAST, EFFORT, common.instructions(ROLE), common.with_experience(common.task_for(task, done_when), "ag_web"),
                           TOOLS + [skills.READ_TOOL, lessons.NOTE_TOOL, common.FINISH],
                           {"browser_code": browser_code, "look": look, "ask_tema": ask_tema, "read_skill": skills.read_skill,
                            "note_to_self": lessons.note_function("ag_web"), "finish": common.finisher(done_when)},
                           max_steps=25, name="ag_web", max_seconds=RUN_LIMIT)
        return common.close_card("ag_web", task, done_when, answer)
    except Exception as e:   # a run that fell leaves its card too: the judge sees it failed, not that nothing was asked (A2A)
        if not isinstance(e, cancel.Stopped):
            status.card("ag_web", task, done_when, {"status": "failed", "result": f"агент упал: {type(e).__name__}: {str(e)[:300]}", "evidence": ""})
        raise
    finally:
        _show_result_to_judge()
        try:
            browser.release()
        except cancel.Stopped:   # stopped while the browser served another task: the tab is forgotten all the same
            pass
        except Exception as e:   # Chrome closed or hung: the tab is forgotten with it
            events.error("Вкладка агента браузера после задачи", e)
        status.local.who, status.local.tab, status.local.web_run = previous
