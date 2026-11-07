"""Browser agent on a real headless Chrome and local pages: nested windows, shadow DOM blocks,
numbers overlaid on the frame, dialogs, downloads, tabs. Skipped if Chrome is not installed."""
import os
import re
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from sena import config

HAS_CHROME = os.path.exists("/Applications/Google Chrome.app")

MAIN = """<!doctype html><html><head><meta charset="utf-8"><title>Главная</title></head><body>
<button id="b1" onclick="out('b1 нажата')">Главная кнопка</button>
<input id="name" placeholder="Имя">
<div id="out"></div>
<x-box></x-box>
<iframe src="/frame" width="300" height="80"></iframe>
<iframe src="http://127.0.0.1:{other}/frame" width="300" height="80"></iframe>
<a href="/second" target="_blank">Открыть вторую</a>
<a href="/file.txt" id="dl">Скачать файл</a>
<input type="file" id="up" onchange="out('файл:' + this.files[0].name)">
<button id="pick" onclick="document.getElementById('up').click()">Выбрать файл</button>
<button onclick="out('confirm:' + confirm('Удалить?'))">Удалить</button>
<button onclick="alert('Привет'); out('alert прошёл')">Предупредить</button>
<script>
function out(t) {{ document.getElementById('out').textContent = t; }}
class XBox extends HTMLElement {{ connectedCallback() {{
  const r = this.attachShadow({{mode: 'open'}});
  r.innerHTML = '<button>Кнопка в тени</button>';
  r.querySelector('button').onclick = () => out('тень нажата');
}} }}
customElements.define('x-box', XBox);
</script></body></html>"""

FRAME = """<!doctype html><meta charset="utf-8"><body>
<button onclick="this.textContent='Нажато в окне'; document.body.dataset.clicked='1'">{label}</button>
<p>Текст внутри окна {label}</p></body>"""

BUSY = """<!doctype html><meta charset="utf-8"><title>Занята</title><body>Живой сайт
<script>(function loop() { fetch('/slow').then(loop); })();</script></body>"""


def make_server(routes):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            route = routes.get(self.path.split("?")[0])
            if not route:   # like a real site: an error page with a body (an empty one makes Chrome fail the navigation itself)
                self.send_response(404)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write("<!doctype html><meta charset='utf-8'><title>Нет такой страницы</title><p>404</p>".encode())
                return
            body, ctype, headers = route() if callable(route) else route
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body.encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def slow():
    time.sleep(3)
    return "{}", "application/json", {}


def never():
    time.sleep(8)   # a picture that never comes in time: the page's "load" waits for it
    return "", "image/png", {}


@unittest.skipUnless(HAS_CHROME, "нужен Google Chrome")
class BrowserAgent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sena.agents.ag_web import browser
        cls.browser = browser
        cls.other = make_server({"/frame": (FRAME.format(label="Кнопка из чужого окна"), "text/html; charset=utf-8", {})})
        routes = {
            "/frame": (FRAME.format(label="Кнопка в окне"), "text/html; charset=utf-8", {}),
            "/second": ("<!doctype html><meta charset='utf-8'><title>Вторая</title><p>вторая страница</p>", "text/html; charset=utf-8", {}),
            "/file.txt": ("содержимое файла", "text/plain; charset=utf-8", {"Content-Disposition": 'attachment; filename="report.txt"'}),
            "/busy": (BUSY, "text/html; charset=utf-8", {}),
            "/slow": slow,
            "/never": never,
            "/hanging": ("<!doctype html><meta charset='utf-8'><title>Висит</title><p>текст есть</p><img src='/never'>", "text/html; charset=utf-8", {}),
        }
        cls.site = make_server(routes)
        routes["/"] = (MAIN.format(other=cls.other.server_address[1]), "text/html; charset=utf-8", {})
        cls.url = f"http://127.0.0.1:{cls.site.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.browser.in_browser(lambda: cls.browser.context().close())
        cls.site.shutdown()
        cls.other.shutdown()

    def run_in_browser(self, func):
        return self.browser.in_browser(func)

    def open_main(self):
        def go():
            page = self.browser.get_page()
            page.goto(self.url + "/")
            page.wait_for_load_state("load")
            time.sleep(0.5)   # windows still loading
            return page
        return self.run_in_browser(go)

    def id_of(self, listing, text):
        m = re.search(r"\[(f?\d+(?:\.\d+)?)\]<\w+[^>]*> " + re.escape(text), listing)
        self.assertIsNotNone(m, f"нет «{text}» в списке:\n{listing}")
        return m.group(1)

    def out_text(self):
        return self.run_in_browser(lambda: self.browser.get_page().evaluate("document.getElementById('out').textContent"))

    # ---------- vision ----------
    def test_elements_include_frames_and_shadow(self):
        from sena.agents.ag_web import view
        self.open_main()
        listing = self.run_in_browser(lambda: view.elements(self.browser.get_page()))
        for expected in ("Главная кнопка", "Кнопка в тени", "Кнопка в окне", "Кнопка из чужого окна", "вложенное окно"):
            self.assertIn(expected, listing)

    def test_the_agent_is_told_where_it_landed_and_a_stale_number_fails_at_once(self):
        """Playwright MCP: after the code, the page's address, title and an error status when they changed; a number from an
        old listing is an error saying what to do, not a five-second wait."""
        from sena.agents.ag_web import agent, view
        self.open_main()
        out = agent.browser_code(f"page.goto('{self.url}/second')")
        self.assertIn("/second — «Вторая»", out)
        out = agent.browser_code(f"page.goto('{self.url}/нет-такой')")
        self.assertIn("«Нет такой страницы», HTTP 404", out)
        self.assertNotIn("[страница]", agent.browser_code("print(1)"))   # nothing changed: nothing said
        self.open_main()

        def stale():
            page = self.browser.get_page()
            number = self.id_of(view.elements(page), "Главная кнопка")
            page.evaluate("document.querySelectorAll('[data-sena-id]').forEach(e => e.removeAttribute('data-sena-id'))")
            started = time.time()
            try:
                view.locate(page, number)
            except LookupError as e:
                return str(e), time.time() - started
        said, took = self.run_in_browser(stale)
        self.assertIn("вызови elements(page) снова", said)
        self.assertLess(took, 4)   # well below the 5 s an element waits: the stale number is said at once

    def test_a_typed_password_or_card_number_never_reaches_the_model(self):
        from sena.agents.ag_web import view
        self.open_main()

        def listing():
            page = self.browser.get_page()
            page.evaluate("""document.body.insertAdjacentHTML('beforeend',
                '<input type=password value="Sekret-123"><input name=cardnumber value="4111111111111111"><input name=city value="Дананг">')""")
            return view.elements(page)
        shown = self.run_in_browser(listing)
        self.assertNotIn("Sekret-123", shown)
        self.assertNotIn("4111111111111111", shown)
        self.assertIn("[заполнено]", shown)
        self.assertIn('value="Дананг"', shown)   # an ordinary field still shows what is in it

    def test_click_by_number_works_in_frame_shadow_and_cross_origin_frame(self):
        from sena.agents.ag_web import actions, view
        self.open_main()
        listing = self.run_in_browser(lambda: view.elements(self.browser.get_page()))
        shadow, frame, foreign = (self.id_of(listing, t) for t in ("Кнопка в тени", "Кнопка в окне", "Кнопка из чужого окна"))
        self.assertTrue(frame.startswith("f") and foreign.startswith("f") and frame != foreign)
        self.run_in_browser(lambda: actions.click(self.browser.get_page(), shadow))
        self.assertEqual(self.out_text(), "тень нажата")
        self.run_in_browser(lambda: actions.click(self.browser.get_page(), frame))
        self.run_in_browser(lambda: actions.click(self.browser.get_page(), foreign))
        after = self.run_in_browser(lambda: view.read_page(self.browser.get_page()))
        self.assertEqual(after.count("Нажато в окне"), 2)   # buttons in both windows changed their label
        clicked = self.run_in_browser(lambda: [f.evaluate("document.body.dataset.clicked || ''") for f in self.browser.get_page().frames[1:]])
        self.assertEqual(sorted(clicked), ["1", "1"])

    def test_human_click_and_type_accept_numbers(self):
        from sena.agents.ag_web import human, view
        self.open_main()
        listing = self.run_in_browser(lambda: view.elements(self.browser.get_page()))
        button, field = self.id_of(listing, "Главная кнопка"), self.id_of(listing, "Имя")
        self.run_in_browser(lambda: human.human_click(self.browser.get_page(), button))
        self.assertEqual(self.out_text(), "b1 нажата")
        self.run_in_browser(lambda: human.human_type(self.browser.get_page(), field, "Тема"))
        value = self.run_in_browser(lambda: self.browser.get_page().evaluate("document.getElementById('name').value"))
        self.assertEqual(value, "Тема")

    def test_read_page_has_text_of_frames(self):
        from sena.agents.ag_web import view
        self.open_main()
        text = self.run_in_browser(lambda: view.read_page(self.browser.get_page()))
        self.assertIn("Главная", text)
        self.assertIn("Текст внутри окна Кнопка в окне", text)
        self.assertIn("Текст внутри окна Кнопка из чужого окна", text)

    def test_marked_screenshot_draws_numbers_and_cleans_up(self):
        from sena.agents.ag_web import view
        self.open_main()
        jpeg, width, height, url, drawn = self.run_in_browser(lambda: view.screenshot(self.browser.get_page(), marks=True))
        self.assertTrue(jpeg.startswith(b"\xff\xd8"))
        self.assertGreaterEqual(drawn, 5)
        left = self.run_in_browser(lambda: self.browser.get_page().evaluate("!!document.getElementById('__sena_marks')"))
        self.assertFalse(left)
        _, _, _, _, none = self.run_in_browser(lambda: view.screenshot(self.browser.get_page(), marks=False))
        self.assertEqual(none, 0)

    def test_busy_site_does_not_make_us_wait_for_network_silence(self):
        from sena.agents.ag_web import view

        def go():
            page = self.browser.get_page()
            page.goto(self.url + "/busy")
            start = time.time()
            view.ready(page)
            return time.time() - start
        self.assertLess(self.run_in_browser(go), 4.5)

    def test_a_page_load_has_its_own_limit_a_file_link_downloads_and_no_picker_opens(self):
        """playwright-mcp's navigate: goto waits for the document, then a little for the rest, never failing on a picture that
        hangs; a link that is a file comes back as a download; a file picker is noted instead of opening on the owner's screen."""
        from unittest import mock

        def go():
            page = self.browser.get_page()
            start = time.time()
            with mock.patch.object(self.browser, "LOAD_REST", 500):
                page.goto(self.url + "/hanging")
            took = time.time() - start
            title = page.title()
            gone = page.goto(self.url + "/file.txt")
            page.wait_for_timeout(500)
            page.goto(self.url + "/")
            page.click("#pick")
            page.wait_for_timeout(300)
            return took, title, gone, self.browser.take_notes(page)
        took, title, gone, notes = self.run_in_browser(go)
        self.assertLess(took, 4)
        self.assertEqual(title, "Висит")
        self.assertIsNone(gone)
        self.assertTrue(any("выбор файла" in n for n in notes), notes)

    # ---------- robustness: dialogs, files, tabs ----------
    def click_text(self, text):
        from sena.agents.ag_web import actions, view

        def go():
            page = self.browser.get_page()
            listing = view.elements(page)
            actions.click(page, self.id_of(listing, text))
            page.wait_for_timeout(300)
            return self.browser.take_notes(page)
        return self.run_in_browser(go)

    def test_alert_is_accepted_and_reported(self):
        self.open_main()
        notes = self.click_text("Предупредить")
        self.assertEqual(self.out_text(), "alert прошёл")
        self.assertTrue(any("alert" in n and "Привет" in n and "принят" in n for n in notes), notes)

    def test_confirm_is_dismissed_unless_allowed(self):
        from sena.agents.ag_web import actions
        self.open_main()
        notes = self.click_text("Удалить")
        self.assertEqual(self.out_text(), "confirm:false")
        self.assertTrue(any("confirm" in n and "отклонён" in n for n in notes), notes)
        self.run_in_browser(lambda: actions.allow_dialog(self.browser.get_page()))
        notes = self.click_text("Удалить")
        self.assertEqual(self.out_text(), "confirm:true")
        self.assertTrue(any("confirm" in n and "принят" in n for n in notes), notes)

    def test_download_file_is_saved_in_data(self):
        from sena.agents.ag_web import actions, view
        self.open_main()

        def go():
            page = self.browser.get_page()
            listing = view.elements(page)
            return actions.download_file(page, self.id_of(listing, "Скачать файл"))
        path = self.run_in_browser(go)
        self.assertTrue(path.startswith(str(config.DOWNLOADS_DIR)))
        self.assertTrue(path.endswith("report.txt"))
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "содержимое файла")

    def test_upload_into_file_input_and_through_button(self):
        import tempfile
        from sena.agents.ag_web import actions, view
        self.open_main()
        with tempfile.NamedTemporaryFile("w", suffix=".txt", prefix="отчёт-", delete=False, encoding="utf-8") as f:
            f.write("данные")
        name = os.path.basename(f.name)
        try:
            # directly into the file field
            self.assertIn("Файл выбран", self.run_in_browser(lambda: actions.upload(self.browser.get_page(), "#up", f.name)))
            self.assertEqual(self.out_text(), "файл:" + name)
            # via the "Choose file" button, by number from the listing
            self.run_in_browser(lambda: self.browser.get_page().evaluate("document.getElementById('out').textContent = ''; document.getElementById('up').value = ''"))

            def via_button():
                page = self.browser.get_page()
                return actions.upload(page, self.id_of(view.elements(page), "Выбрать файл"), f.name)
            self.assertIn("Файл выбран", self.run_in_browser(via_button))
            self.assertEqual(self.out_text(), "файл:" + name)
            with self.assertRaises(FileNotFoundError):
                self.run_in_browser(lambda: actions.upload(self.browser.get_page(), "#up", "/нет/такого/файла.txt"))
        finally:
            os.unlink(f.name)

    def test_fences_hold_whatever_the_code_asks(self):
        from sena.agents.ag_web import actions, agent
        self.open_main()
        private = config.STATE_DIR / "fence-check.json"
        private.write_text("{}", encoding="utf-8")
        attempts = {"файл Mac": lambda: self.browser.get_page().goto("file:///etc/hosts"),
                    "данные Сены на сайт": lambda: actions.upload(self.browser.get_page(), "#up", str(private)),
                    "куки": lambda: self.browser.context().cookies(),
                    "запись вне папок": lambda: self.browser.get_page().screenshot(path=os.path.expanduser("~/fence-check.png"))}
        for what, attempt in attempts.items():
            with self.assertRaises(PermissionError, msg=what):
                self.run_in_browser(attempt)
        self.assertFalse(os.path.exists(os.path.expanduser("~/fence-check.png")))
        self.assertIn("Главная", agent.browser_code("print(page.title())"))   # ordinary code still runs
        self.assertIn("Код не запущен", agent.browser_code("import os\nprint(os.environ)"))

    def test_a_recipe_replaying_without_the_model_never_presses_an_irreversible_button(self):
        """Skyvern's guard on cached scripts: no one asks the owner on the fast path, so "Удалить" is refused there, while an
        ordinary button works — and the agent itself may still press it (its rules ask Tema first)."""
        from sena.agents.ag_web import guard
        self.open_main()

        def go():
            page = self.browser.get_page()
            with guard.replaying():
                page.get_by_role("button", name="Главная кнопка").click()
                try:
                    page.get_by_role("button", name="Удалить").click()
                    refused = False
                except PermissionError:
                    refused = True
            return refused
        self.assertTrue(self.run_in_browser(go))
        self.assertEqual(self.out_text(), "b1 нажата")   # the ordinary click went through, the irreversible one never ran

    def test_the_owners_password_reaches_the_login_field_on_the_browsers_thread(self):
        """secret() ran on the browser's thread and looked for the run's ticket there, where it never was: an empty password."""
        from sena.agents.ag_web import agent
        from sena.core import secrets, status
        secrets.put("hh-test", "parol-hh-2026", allow=["ag_web"])
        self.open_main()
        status.local.web_run = {"ticket": ("hh-test", secrets.use("hh-test", "ag_web", "вход")), "unanswered": 0}
        try:
            out = agent.browser_code("fill(page, '#name', secret())\nprint(len(page.input_value('#name')))")
        finally:
            del status.local.web_run
        self.assertIn("13", out)   # the value went in; the model saw only its length
        self.assertNotIn("parol-hh-2026", out)

    def test_a_press_is_checked_where_it_lands(self):
        """The mouse by coordinates, Enter in a filled form and a dispatched click pressed «Оплатить» without his "yes"."""
        from sena.agents.ag_web import guard, human
        self.open_main()

        def page_with_payment():
            page = self.browser.get_page()
            page.evaluate("""document.body.insertAdjacentHTML('beforeend', '<form id=pay onsubmit="out(\\'оплачено\\'); return false">' +
                '<input id=card value="4111"><button id=paybtn style="position:fixed;left:20px;top:300px;width:200px;height:40px">' +
                '<span>Оплатить 2 300 ₽</span></button></form>'); document.getElementById('out').textContent = '';""")
            return page
        attempts = {"мышь по точке": lambda p: human.human_click_at(p, 120, 320),
                    "page.mouse.click": lambda p: p.mouse.click(120, 320),
                    "Enter в поле формы": lambda p: (p.focus("#card"), p.keyboard.press("Enter")),
                    "Enter на поле": lambda p: p.press("#card", "Enter"),
                    "событие click": lambda p: p.dispatch_event("#paybtn", "click")}
        for what, attempt in attempts.items():
            def go(attempt=attempt):
                page = page_with_payment()
                try:
                    attempt(page)
                    return "нажато"
                except PermissionError as e:
                    return str(e)
            said = self.run_in_browser(go)
            self.assertIn("необратимое нажатие", said, what)
            self.assertEqual(self.out_text(), "", what)

        def approved():
            page = page_with_payment()
            guard.approve(None, "Оплатить 2 300 ₽", "да")
            human.human_click_at(page, 120, 320)
            page.wait_for_timeout(200)
        self.run_in_browser(approved)
        self.assertEqual(self.out_text(), "оплачено")   # his "yes" lets exactly that press through, once

    def test_after_someone_elses_text_a_script_cant_take_the_browser_inside(self):
        from sena.agents.ag_web import guard
        self.open_main()
        task = {"id": "t-inside", "text": "найди цену на 127.0.0.1", "outside": "страница сайта"}

        def go():
            page = self.browser.get_page()
            fetched = page.evaluate("fetch('http://10.255.255.1/apply.cgi').then(() => 'дошло', () => 'не дошло')")
            page.evaluate("location.href = 'http://10.255.255.1/admin'")
            page.wait_for_timeout(500)
            return fetched, page.url
        with mock.patch.object(guard.status, "outside", return_value="страница сайта"), mock.patch.object(guard.status, "current", return_value=task):
            fetched, url = self.run_in_browser(go)
        self.assertEqual(fetched, "не дошло")
        self.assertFalse(url.startswith("http://10."), url)   # the navigation was refused: the router never opened (Chrome shows its error page)

    def test_only_a_cookie_banner_is_clicked_unasked(self):
        """«Разрешить» on a sign-in page gives a site the owner's account; only a consent banner's «Принять» is closed by itself."""
        from sena.agents.ag_web import view

        def show(html):
            page = self.browser.get_page()
            page.set_content(f"<!doctype html><meta charset='utf-8'><div id=out></div>{html}")
            return view.dismiss_banners(page), page.evaluate("document.getElementById('out').textContent")
        self.open_main()
        said, out = self.run_in_browser(lambda: show("<div class='app-dialog'><p>Дать приложению доступ к почте?</p><button onclick=\""
                                                     "document.getElementById('out').textContent='доступ дан'\">Разрешить</button></div>"))
        self.assertEqual((said, out), ("", ""))
        said, out = self.run_in_browser(lambda: show("<div id='cookie-notice'><p>Мы используем файлы cookie.</p><button onclick=\""
                                                     "document.getElementById('out').textContent='закрыт'\">Принять</button></div>"))
        self.assertEqual((said, out), ("закрыт баннер «Принять»", "закрыт"))
        self.open_main()

    def test_a_finished_task_leaves_its_tab_and_old_idle_tabs_close(self):
        from sena.agents.ag_web import agent

        def tabs_now():
            return len(self.browser.context().pages)
        before = self.run_in_browser(tabs_now)
        keys = []

        def fake_fast(task):
            keys.append(agent.status.local.tab)
            self.browser.in_browser(lambda: self.browser.get_page().goto(self.url + "/second"))
            return "готово", None
        with mock.patch.object(agent.recipes, "try_fast", fake_fast), mock.patch.object(agent, "_show_result_to_judge", lambda: None):
            for _ in range(self.browser.KEEP_TABS + 2):
                agent.run("открой вторую страницу")
        self.assertEqual(len(set(keys)), self.browser.KEEP_TABS + 2)   # a tab of its own for every run, even on one thread
        self.assertFalse(set(keys) & set(self.browser._tabs))   # none of them is held after its run
        self.assertLessEqual(self.run_in_browser(tabs_now), max(before, self.browser.KEEP_TABS))

    def test_popup_is_reported_and_tab_can_be_switched(self):
        from sena.agents.ag_web import actions, view
        self.open_main()
        notes = self.click_text("Открыть вторую")
        self.assertTrue(any("новая вкладка" in n for n in notes), notes)

        def go():
            page = self.browser.get_page()
            actions.use_tab(page.context.pages[-1])
            second = self.browser.get_page()
            second.wait_for_load_state("load")
            return view.read_page(second)
        self.assertIn("вторая страница", self.run_in_browser(go))
        self.run_in_browser(lambda: actions.use_tab(self.browser.get_page().context.pages[0]))   # go back to the main tab

    # ---------- intelligence: judge frame and a full agent run ----------
    def test_final_frame_and_task_evidence(self):
        from unittest import mock
        from sena.agents.ag_web import agent as ag_web
        from sena.core import llm, status
        from tests.test_core import FakeModel, call, response
        self.open_main()   # this thread's (test's) tab is on the page: the run doesn't take it over, it opens its own
        status.begin("проверка вкладки")
        tabs, pages = [], []
        model = FakeModel(
            response(call("browser_code", {"check": "старт", "status": "смотрю",
                                           "code": f"page.goto('{self.url}/')\nprint(elements(page))\nprint(read_page(page)[:40])"}, "c1")),
            response(call("browser_code", {"check": "список получен", "status": "жму", "code": "click(page, 1)\nprint(page.evaluate(\"document.getElementById('out').textContent\"))"}, "c2")),
            response(text="Нажала главную кнопку."),   # words instead of the card: the agent is asked to close it
            response(call("finish", {"outcome": "done", "result": "Нажала главную кнопку.", "evidence": "b1 нажата"}, "c3")),
        )
        original, saved = llm.respond, ag_web.browser.get_page

        def spy():
            page = saved()
            tabs.append(id(page))
            pages.append(page)
            return page

        llm.respond = model
        try:
            with mock.patch.object(ag_web.recipes, "try_fast", lambda task: (None, None)), \
                 mock.patch.object(ag_web.browser, "get_page", spy):
                answer = ag_web.run("нажми главную кнопку")
            (task,) = [t for t in status.tasks.values() if t["text"] == "проверка вкладки"]
        finally:
            llm.respond = original
            status.end("")
        self.assertIn("Статус: сделано. Нажала главную кнопку.\nФакт: b1 нажата", answer)
        self.assertIn("ВНЕШНИЕ ДАННЫЕ", answer)   # written after reading a page: it reaches the coordinator as data, not as its own words
        self.assertIn("не закрыла задачу через finish", model.inputs[3][0]["content"])
        self.assertEqual(len(set(tabs)), 1, "все шаги агента должны идти в одной вкладке")
        self.assertEqual(len(task["code"]), 2)
        self.assertIn("click(page, 1)", task["code"][1]["code"])
        self.assertFalse(task["code"][1]["failed"])
        self.assertTrue(task["image"].startswith(b"\xff\xd8"), "итоговый кадр для судьи")
        self.assertEqual(self.run_in_browser(lambda: pages[-1].evaluate("document.getElementById('out').textContent")), "b1 нажата")
        self.assertEqual(self.out_text(), "")   # the test's own tab was left alone

    def test_final_frame_is_none_without_tab(self):
        from sena.core import status
        def probe():
            status.local.tab = "нет-такой-вкладки"
            return self.browser.final_frame()
        self.assertIsNone(self.run_in_browser(probe))

class HungBrowser(unittest.TestCase):
    def test_a_call_waiting_its_turn_is_not_hung_and_one_given_up_never_runs_later(self):
        """The 180 s counted the queue: a quick click behind another task's long call was "Chrome hung", and still ran afterwards,
        so a retry did it twice. Now the clock starts when the call does, and one that never started never runs."""
        from sena.agents.ag_web import browser
        from sena.core import cancel, status
        release, ran = threading.Event(), []
        first = threading.Thread(target=lambda: browser.in_browser(release.wait, 15))   # another task's long call holds the thread
        first.start()
        time.sleep(0.2)
        try:
            with mock.patch.object(browser, "CALL_TIMEOUT", 0.5), mock.patch.object(browser, "QUEUE_WAIT", 1):
                with self.assertRaises(RuntimeError) as e:
                    browser.in_browser(lambda: ran.append("клик"))
                self.assertIn("занят другой задачей", str(e.exception))
                status.begin("остановленная")
                try:
                    cancel.cancelled.add(status.current()["id"])
                    with self.assertRaises(cancel.Stopped):
                        browser.in_browser(lambda: ran.append("после стопа"))
                finally:
                    cancel.cancelled.discard(status.current()["id"])
                    status.end("")
            with mock.patch.object(browser, "CALL_TIMEOUT", 0.5), mock.patch.object(browser, "QUEUE_WAIT", 10):
                later = threading.Thread(target=lambda: ran.append(browser.in_browser(lambda: "после очереди")))
                later.start()
                time.sleep(1.2)   # it waits longer than CALL_TIMEOUT for its turn: that is no hang
                release.set()
                later.join(5)
        finally:
            release.set()
            first.join(5)
        self.assertEqual(ran, ["после очереди"])   # the two given up never ran behind their callers' backs

    def test_a_stuck_call_returns_an_error_instead_of_waiting_forever(self):
        from unittest import mock
        from sena.agents.ag_web import browser
        release = threading.Event()
        try:
            with mock.patch.object(browser, "CALL_TIMEOUT", 0.3):
                with self.assertRaises(RuntimeError) as e:
                    browser.in_browser(release.wait, 5)
            self.assertIn("не ответил", str(e.exception))
        finally:
            release.set()


class SmallParts(unittest.TestCase):
    def test_the_codes_sleep_is_short_and_a_stop_gets_through_it(self):
        """A plain time.sleep(600) held the browser's only thread past the code's own limit (it ticks only between lines)."""
        from sena.core import cancel, sandbox, status
        clock = sandbox.module("time")   # what the agent's code gets as time (toolkit.helpers): one capped, stop-aware sleep for all model code
        started = time.monotonic()
        with mock.patch.object(sandbox, "NAP", 0.3):
            clock.sleep(600)
        self.assertLess(time.monotonic() - started, 2)   # capped by NAP, generous for a slow runner
        status.begin("остановили")
        try:
            cancel.cancelled.add(status.current()["id"])
            with self.assertRaises(cancel.Stopped):
                clock.sleep(5)
        finally:
            cancel.cancelled.discard(status.current()["id"])
            status.end("")

    def test_a_strange_answer_about_tiles_and_a_result_without_a_link_dont_break_the_call(self):
        from types import SimpleNamespace
        from sena.agents.ag_web import captcha, youtube
        for text in ('{"tiles": [[1, 2], [3], "x", [2, "b"], [3, 3]]}', "не JSON", '{"tiles": null}', '["a"]'):
            with mock.patch.object(captcha.llm, "respond", lambda **kw: SimpleNamespace(output_text=text)):
                picks = captcha._vision_pick(b"png", "светофоры", 3, 3)
            self.assertTrue(all(isinstance(p, tuple) and len(p) == 2 for p in picks), (text, picks))
        first = SimpleNamespace(wait_for=lambda timeout: None, get_attribute=lambda name: "Лучшее за неделю" if name == "title" else None,
                                inner_text=lambda: "")
        page = SimpleNamespace(goto=lambda *a, **k: None, locator=lambda sel: SimpleNamespace(first=first))
        with mock.patch.object(youtube, "youtube_pause_others", lambda page: 0):
            self.assertIsInstance(youtube.youtube_search_play(page, "лучшее"), youtube.Failure)


class RunState(unittest.TestCase):
    def test_one_unanswered_question_is_enough_for_a_run_whatever_thread_asks(self):
        """The flag was set in a throwaway step thread and died with it: Tema got a second 3-minute question in the same run."""
        from concurrent.futures import ThreadPoolExecutor
        from sena.agents.ag_web import agent
        from sena.core import ask_owner, status
        asked = []
        status.local.web_run = {"ticket": None, "unanswered": 0}
        try:
            with mock.patch.object(agent.browser, "in_browser", lambda func, *a: b"jpg"), \
                    mock.patch.object(ask_owner, "ask", lambda q, shot: asked.append(q) or agent.Failure("Тема не ответил за 3 мин")):
                with ThreadPoolExecutor(max_workers=1) as pool:   # each step on a thread of its own, as the brain runs them
                    pool.submit(status.carried(lambda: agent.ask_tema("Войди в аккаунт"))).result()
                second = agent.ask_tema("Ты здесь?")
        finally:
            del status.local.web_run
        self.assertEqual(len(asked), 1)
        self.assertIn("уже не ответил", second)

    def test_chrome_isnt_started_while_sena_is_stopping(self):
        from sena.agents.ag_web import browser
        with mock.patch.object(browser, "_context", None), mock.patch.object(browser.power, "is_stopping", return_value=True):
            with self.assertRaises(RuntimeError) as e:
                browser.context()
        self.assertIn("останавливается", str(e.exception))


class InsideAddresses(unittest.TestCase):
    def test_after_someone_elses_text_the_home_network_opens_only_at_an_address_he_named(self):
        """E2B walls off private addresses: here, once a page or a mail is in the task, the router and this Mac open only when
        the owner named them; before that, and for public sites, nothing changes."""
        from sena.agents.ag_web import guard
        with mock.patch.object(guard.status, "outside", return_value="страница сайта"), \
                mock.patch.object(guard.status, "current", return_value={"id": "t-shop", "text": "найди цену на сайте магазина", "outside": "страница сайта"}):
            with self.assertRaises(PermissionError):
                guard._url("http://192.168.1.1/admin")
            with self.assertRaises(PermissionError):
                guard._url("http://localhost:8080/")
            guard._url("https://93.184.215.14/")   # a public address
        with mock.patch.object(guard.status, "outside", return_value="страница сайта"), \
                mock.patch.object(guard.status, "current", return_value={"id": "t-router", "text": "открой роутер 192.168.1.1 и проверь", "outside": "x"}):
            guard._url("http://192.168.1.1/admin")   # he named it himself
        with mock.patch.object(guard.status, "outside", return_value=None):
            guard._url("http://localhost:8080/")   # nothing foreign read yet: his own task
        with mock.patch.object(guard.status, "outside", return_value="страница сайта"), \
                mock.patch.object(guard.status, "current", return_value={"id": "t-near", "text": "открой 10.0.0.15 и проверь", "outside": "x"}):
            with self.assertRaises(PermissionError):
                guard._url("http://10.0.0.1/")   # a part of the address he named is not the address

    def test_the_fence_sees_every_request_to_such_an_address_and_a_site_marks_the_task(self):
        """Only goto was checked: a script's navigation, a link or the page's own fetch reached the router. Every request to an
        address that looks inside goes through the route now (and only those: a route for all would make each request wait for
        the browser's thread)."""
        from types import SimpleNamespace
        from sena.agents.ag_web import guard
        from sena.core import status
        for inside in ("http://192.168.1.1/apply.cgi", "http://10.0.0.7:8080/", "ws://127.0.0.1:9000/", "http://router.local/", "http://[::1]/"):
            self.assertTrue(guard.PRIVATE.match(inside), inside)
        for public in ("https://hh.ru/", "http://192.168.example.com/", "https://10.ru/"):
            self.assertFalse(guard.PRIVATE.match(public), public)
        aborted, continued = [], []
        route = SimpleNamespace(abort=lambda code: aborted.append(code), continue_=lambda: continued.append(1))
        with mock.patch.object(guard.status, "outside", return_value="страница сайта"), \
                mock.patch.object(guard.status, "current", return_value={"id": "t", "text": "найди цену", "outside": "страница сайта"}):
            guard.private_request(route, SimpleNamespace(url="http://192.168.1.1/apply.cgi"))
        guard.private_request(route, SimpleNamespace(url="http://192.168.1.1/"))   # nothing foreign read: his own task goes on
        self.assertEqual((aborted, continued), (["blockedbyclient"], [1]))
        status.begin("открой hh.ru")
        try:
            with mock.patch.object(guard.net, "private_host", return_value=None):   # no name is looked up in tests
                guard._url("https://hh.ru/vacancy/1")
            self.assertEqual(status.outside(), "сайт hh.ru")   # marked before the page loads: a redirect inward meets the fence
        finally:
            status.end("")


class NamedApproval(unittest.TestCase):
    """A2A: an approval names the action and is spent by it. In the agent's own run a press of «Оплатить», «Отправить»,
    «Откликнуться» waits for the owner's "yes" to exactly that button, and one "yes" is one press."""
    class Button:
        def __init__(self, label):
            self.label = label

        def inner_text(self, timeout=None):
            return self.label

        def get_attribute(self, name, timeout=None):
            return None

    def test_an_approval_belongs_to_the_task_and_runs_out(self):
        """An unused "да" stayed on the thread's number: a later task on that thread pressed «Оплатить» with no question."""
        from sena.agents.ag_web import guard
        from sena.core import status
        pay, ident, seen = self.Button("Оплатить 2 300 ₽"), {}, []

        def two_tasks():
            ident["thread"] = threading.get_ident()
            status.begin("первая")
            guard.approve(threading.get_ident(), "Оплатить 2 300 ₽", "да")   # the agent passes the thread's number, as ask_tema does
            status.end("")
            status.begin("вторая")
            try:
                guard.final_press(pay, threading.get_ident())
                seen.append("нажато")
            except PermissionError:
                seen.append("спросить")
            status.end("")
        t = threading.Thread(target=two_tasks)
        t.start()
        t.join()
        self.assertEqual(seen, ["спросить"])
        self.assertTrue(guard.approve("task-ttl", "Оплатить 2 300 ₽", "да"))
        with mock.patch.object(guard.time, "time", return_value=time.time() + guard.APPROVAL_TTL + 1):
            with self.assertRaises(PermissionError):
                guard.final_press(pay, "task-ttl")   # an old "yes" is no yes any more

    def test_a_final_press_needs_his_yes_to_exactly_it_once(self):
        from sena.agents.ag_web import guard
        pay, task = self.Button("Оплатить 2 300 ₽"), "task-approve"
        with self.assertRaisesRegex(PermissionError, "approve"):
            guard.final_press(pay, task)
        self.assertFalse(guard.approve(task, "Оплатить 2 300 ₽", "да, но не сейчас"))   # a yes with a no in it is no yes
        self.assertFalse(guard.approve(task, "Оплатить 2 300 ₽", "подожди"))
        self.assertTrue(guard.approve(task, "Оплатить 2 300 ₽", "да"))
        with self.assertRaises(PermissionError):
            guard.final_press(self.Button("Отправить сообщение"), task)   # approved was paying, not sending
        guard.final_press(pay, task)   # the approved press
        with self.assertRaises(PermissionError):
            guard.final_press(pay, task)   # spent: the next payment asks again
        guard.final_press(self.Button("Найти"), task)   # an ordinary button is never asked about
        guard.final_press(self.Button("Ноутбук Apple MacBook Air 13, 16 ГБ, 512 ГБ, серебристый — 129 990 ₽ — Купить в один клик"), task)   # a tile, not a button


class IrreversibleWords(unittest.TestCase):
    def test_a_reply_to_a_job_and_a_booking_are_irreversible_a_filter_is_not(self):
        """career-ops guards «Откликнуться»: on hh.ru one click sends the reply. "Apply" alone is a shop's filter or coupon."""
        from sena.agents.ag_web import guard
        for words in ("Откликнуться", "Отклик на вакансию", "Записаться на приём", "Забронировать", "Easy Apply", "Подать заявку"):
            self.assertTrue(guard.IRREVERSIBLE.search(words), words)
        for words in ("Apply filters", "Применить", "Facebook", "Показать ещё"):
            self.assertFalse(guard.IRREVERSIBLE.search(words), words)


class RoundTwoBrowser(unittest.TestCase):
    """Round 2 of the browser agent: a «да» to a short label pressed any final button containing it; a page's request was judged
    by whatever task's call ran on the browser's thread; a finished run waited behind other tasks to give its tab back."""

    Button = NamedApproval.Button

    def test_a_yes_to_a_short_label_presses_only_what_it_names(self):
        from sena.agents.ag_web import guard
        task = "task-short-yes"
        self.assertTrue(guard.approve(task, "Да", "да"))   # a dialog's «Да»
        with self.assertRaises(PermissionError):
            guard.final_press(self.Button("Подать заявку"), task)   # «да» sits inside «подать»: not the press he approved
        with self.assertRaises(PermissionError):
            guard.final_press(self.Button("Да, оплатить"), task)   # a «Да» that doesn't name the payment doesn't pay
        self.assertTrue(guard.approve(task, "Оплатить", "да"))
        guard.final_press(self.Button("Оплатить 2 300 ₽"), task)   # the approved act with its amount on the button
        self.assertTrue(guard.approve(task, "Отправить отклик на вакансию", "да"))
        guard.final_press(self.Button("Отправить"), task)   # the button says less than the agent asked about

    def test_a_request_is_judged_by_the_task_of_the_tab_that_made_it(self):
        from types import SimpleNamespace
        from sena.agents.ag_web import browser
        page = SimpleNamespace()
        browser._owners[id(page)] = {"id": "t-b", "text": "найди цену", "outside": "страница сайта"}   # tab B read a page
        self.addCleanup(browser._owners.pop, id(page), None)
        aborted, continued = [], []
        route = SimpleNamespace(abort=lambda code: aborted.append(code), continue_=lambda: continued.append(1))
        clean = {"id": "t-a", "text": "открой роутер 192.168.1.1"}   # task A's call runs on the thread now, nothing foreign in it
        with mock.patch.object(browser.status, "outside", return_value=None), mock.patch.object(browser.status, "current", return_value=clean):
            browser._private(route, SimpleNamespace(url="http://192.168.1.1/apply.cgi", frame=SimpleNamespace(page=page)))
            browser._private(route, SimpleNamespace(url="http://192.168.1.1/", frame=None))   # no tab to ask: the call running now decides
        self.assertEqual((aborted, continued), (["blockedbyclient"], [1]))

    def test_a_finished_run_gives_its_tab_back_without_waiting_and_the_frame_waits_briefly(self):
        from sena.agents.ag_web import agent, browser
        hold = threading.Event()
        busy = browser._thread.submit(hold.wait, 5)   # another task's long call holds the browser's thread
        try:
            with mock.patch.object(browser, "_context", object()):
                started = time.monotonic()
                browser.release()
                self.assertLess(time.monotonic() - started, 1)   # the tidying waits its turn, the card doesn't
        finally:
            hold.set()
            busy.result(5)
        with mock.patch.object(browser, "in_browser", side_effect=browser.Busy("занят")) as asked, mock.patch.object(agent.events, "error") as error:
            agent._show_result_to_judge()
        self.assertEqual(asked.call_args.kwargs["wait"], browser.FRAME_WAIT)
        error.assert_not_called()   # a busy browser is no failure of this run


if __name__ == "__main__":
    unittest.main()
