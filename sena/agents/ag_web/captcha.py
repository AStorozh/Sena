"""Captchas: detect whether one is present, click the "I'm not a robot" checkbox, solve image-based reCAPTCHA.
We take the tile grid and the task from the reCAPTCHA frame, ask a strong model with an image which tiles to pick,
and click like a human. This is a separate, reliable tool so the agent doesn't have to guess coordinates itself."""
import json
import random
import re
import time

from sena import config
from sena.agents.ag_web.human import human_click_at, human_click_box
from sena.core import llm
from sena.core.tools import image

CAPTCHA_RE = re.compile(r"recaptcha|hcaptcha|turnstile|captcha|arkoselabs|funcaptcha", re.I)


def captcha(page):
    # whether the page has a captcha, and which kind
    kinds = {f.url for f in page.frames if CAPTCHA_RE.search(f.url or "")}
    try:
        text = page.inner_text("body")[:5000]
    except Exception:
        text = ""
    words = re.findall(r"я не робот|i'?m not a robot|подтвердите, что вы человек|verify you are human|select all images|выберите все изображения", text, re.I)
    if not kinds and not words:
        return "капчи нет"
    kind = "recaptcha" if any("recaptcha" in k for k in kinds) else "hcaptcha" if any("hcaptcha" in k for k in kinds) else "turnstile" if any("turnstile" in k for k in kinds) else "неизвестная"
    return f"капча: {kind}; на странице: {', '.join(set(words)) or 'фреймы ' + ', '.join(k[:60] for k in list(kinds)[:3])}"


def captcha_checkbox(page):
    # click the "I'm not a robot" checkbox in a human-like way (reCAPTCHA, hCaptcha, Turnstile)
    for frame_sel, box_sel in (('iframe[src*="recaptcha"]', "#recaptcha-anchor"), ('iframe[src*="hcaptcha"]', "#checkbox"),
                               ('iframe[src*="turnstile"]', "input[type=checkbox], .ctp-checkbox-label, body")):
        try:
            box = page.frame_locator(frame_sel).first.locator(box_sel).first
            b = box.bounding_box(timeout=2000)
            if b:
                human_click_box(page, b)
                time.sleep(2)
                return "кликнула галочку, проверь look"
        except Exception:
            continue
    return "галочку не нашла"


def _vision_pick(image_png, instruction, rows, cols):
    prompt = (f"reCAPTCHA. Задание: «{instruction}». Сетка {rows}x{cols}, строки и столбцы нумеруются с 1 сверху и слева. "
              "Перечисли ВСЕ плитки, где есть хотя бы часть нужного объекта (даже маленькая, в том числе на границе). "
              'Ответь только JSON вида {"tiles": [[строка, столбец], ...]}. Если нужного объекта нет нигде — {"tiles": []}.')
    r = llm.respond(purpose="капча", model=config.MODEL_MAIN, reasoning={"effort": "low"}, input=[{"role": "user", "content": [
        {"type": "input_text", "text": prompt}, image(image_png, "image/png")]}])
    m = re.search(r"\{.*\}", r.output_text, re.S)
    try:
        tiles = json.loads(m.group(0)).get("tiles") if m else []
    except (ValueError, AttributeError):   # not JSON, or not an object: no tiles this round
        return []
    # only [row, column] pairs of numbers: anything else would break the clicks below
    return [(int(t[0]), int(t[1])) for t in tiles or [] if isinstance(t, (list, tuple)) and len(t) == 2 and all(isinstance(v, (int, float)) for v in t)]


def solve_captcha(page, rounds=8):
    if "recaptcha" not in captcha(page):
        return "reCAPTCHA не найдена (другие капчи — через ask_tema)"
    anchor = page.frame_locator('iframe[src*="recaptcha/api2/anchor"], iframe[src*="recaptcha/enterprise/anchor"]').first.locator("#recaptcha-anchor")
    if anchor.get_attribute("aria-checked", timeout=3000) != "true":
        captcha_checkbox(page)
    log = []
    for n in range(rounds):
        if anchor.get_attribute("aria-checked", timeout=2000) == "true":
            return "капча пройдена" + (": " + "; ".join(log) if log else "")
        frame_el = page.locator('iframe[src*="bframe"]').first
        if not frame_el.is_visible():
            time.sleep(1.5)
            continue
        f = page.frame_locator('iframe[src*="bframe"]').first
        cells = f.locator("table td").all()
        rows = 4 if len(cells) == 16 else 3
        instruction = " ".join(t.strip() for t in f.locator(".rc-imageselect-desc-no-canonical, .rc-imageselect-desc, .rc-imageselect-desc-wrapper").all_inner_texts() if t.strip())[:200]
        picks = _vision_pick(frame_el.screenshot(type="png"), instruction, rows, rows)
        log.append(f"раунд {n + 1}: «{instruction[:60]}» -> {picks}")
        for r_, c_ in picks:
            i = (r_ - 1) * rows + (c_ - 1)
            if 0 <= i < len(cells):
                b = cells[i].bounding_box()
                if b:
                    human_click_box(page, b)
        time.sleep(random.uniform(1.5, 2.5))
        # after selecting, new images may have appeared in the same tiles (a dynamic captcha): then another round, otherwise we confirm
        if not picks or f.locator(".rc-imageselect-dynamic-selected").count() == 0:
            btn = f.locator("#recaptcha-verify-button").first
            bb = btn.bounding_box()
            if bb:
                human_click_at(page, bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
            time.sleep(random.uniform(2, 3))
    return "капча не решилась за %d раундов: " % rounds + "; ".join(log[-3:])
