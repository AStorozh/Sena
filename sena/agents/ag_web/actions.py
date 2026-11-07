"""Simple actions the agent performs on the page: an element number from elements works inside nested frames too."""
import os

from sena.agents.ag_web import browser, view
from sena.core import cancel


def click(page, target):
    view.locate(page, target).click()


def fill(page, target, text):
    view.locate(page, target).fill(text)


def upload(page, target, path):
    """Upload a file to the site. target — a file field or a "Choose file" button (a number from elements, or a selector)."""
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Файла нет: {path}")
    element = view.locate(page, target)
    if element.evaluate("e => e.tagName === 'INPUT' && e.type === 'file'"):
        element.set_input_files(path)
    else:
        with page.expect_file_chooser(timeout=5000) as chooser:
            element.click()
        chooser.value.set_files(path)
    return f"Файл выбран: {os.path.basename(path)}"


def download_file(page, target, timeout=30):
    """Click a link or download button and wait for the file. Returns the path to the saved file."""
    before = len(browser.downloads)
    view.locate(page, target).click()
    for _ in range(int(timeout * 5)):
        if len(browser.downloads) > before:
            return browser.downloads[-1]
        cancel.check_stop()
        page.wait_for_timeout(200)   # browser events are only processed inside Playwright calls
    raise TimeoutError(f"файл не скачался за {timeout} с")


def allow_dialog(page, accept=True, text=None):
    """How to answer the page's next dialog (confirm, prompt). Without a call: alert is accepted, the others are dismissed."""
    browser.allow_dialog(page, accept, text)


def use_tab(page):
    """Keep working in this tab from now on (e.g. one that opened from a click): the next calls will start from it."""
    browser.use_tab(page)
