"""Everything available to the agent's code in the browser: both browser_code code and ready-made recipes.
There is one shared list: whatever ROLE (agent.py) describes is exactly what's in the environment."""
from sena.agents.ag_web import actions, browser, captcha, human, view, youtube
from sena.core import sandbox


def helpers(page):
    return {
        "page": page, "context": browser.context(), "time": sandbox.module("time"),   # its sleep is short and a stop gets through it (sandbox.nap)
        "elements": view.elements, "read_page": view.read_page, "ready": view.ready, "locate": view.locate,
        "click": actions.click, "fill": actions.fill, "use_tab": actions.use_tab,
        "upload": actions.upload, "download_file": actions.download_file, "allow_dialog": actions.allow_dialog,
        "youtube_play": youtube.youtube_play, "youtube_search_play": youtube.youtube_search_play,
        "youtube_pause_others": youtube.youtube_pause_others,
        "human_click": human.human_click, "human_click_at": human.human_click_at, "human_type": human.human_type,
        "human_scroll": human.human_scroll, "human_move": human.human_move,
        "captcha": captcha.captcha, "captcha_checkbox": captcha.captcha_checkbox, "solve_captcha": captcha.solve_captcha,
    }
