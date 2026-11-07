"""Human-like movements: the mouse follows a curve with acceleration and a slight overshoot, typing has pauses, scrolling uses the wheel.
Sites with bot protection look at exactly this: an instant precise click and text that appears all at once give away a robot.
Every press goes through the page's mouse, which the guard watches (ag_web/guard.Mouse): whatever lies under the point — by
number or by coordinates — meets the same "yes" as a click on it."""
import random
import time
import weakref

from sena.agents.ag_web import view
from sena.core import secrets

_mouse = weakref.WeakKeyDictionary()  # tab -> where the mouse currently is (gone with the tab)


def _bezier(p0, p1, p2, p3, t):
    return tuple((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t ** 2 * c + t ** 3 * d for a, b, c, d in zip(p0, p1, p2, p3))


def human_move(page, x, y):
    x0, y0 = _mouse.get(page, (random.uniform(100, 600), random.uniform(100, 400)))
    dist = ((x - x0) ** 2 + (y - y0) ** 2) ** 0.5
    c1 = (x0 + (x - x0) * random.uniform(0.2, 0.5) + random.uniform(-dist, dist) * 0.2, y0 + (y - y0) * random.uniform(0.2, 0.5) + random.uniform(-dist, dist) * 0.2)
    c2 = (x0 + (x - x0) * random.uniform(0.6, 0.9) + random.uniform(-dist, dist) * 0.1, y0 + (y - y0) * random.uniform(0.6, 0.9) + random.uniform(-dist, dist) * 0.1)
    steps = max(8, min(40, int(dist / 15)))
    for i in range(1, steps + 1):
        t = i / steps
        t = t * t * (3 - 2 * t)  # ease in and out
        px, py = _bezier((x0, y0), c1, c2, (x, y), t)
        page.mouse.move(px, py)
        time.sleep(random.uniform(0.004, 0.016))
    if dist > 60:  # overshoot slightly and correct
        page.mouse.move(x + random.uniform(-3, 3), y + random.uniform(-3, 3))
        time.sleep(random.uniform(0.03, 0.08))
        page.mouse.move(x, y)
    _mouse[page] = (x, y)


def human_click_at(page, x, y):
    human_move(page, x, y)
    time.sleep(random.uniform(0.05, 0.15))
    page.mouse.down()   # the guard checks what lies under (x, y) before the press
    time.sleep(random.uniform(0.04, 0.12))
    page.mouse.up()
    time.sleep(random.uniform(0.1, 0.3))


def human_click_box(page, box):
    """Click a random point inside the rectangle {x, y, width, height} (a human never hits the exact center)."""
    human_click_at(page, box["x"] + box["width"] * random.uniform(0.3, 0.7), box["y"] + box["height"] * random.uniform(0.3, 0.7))


def human_click(page, target):
    el = view.locate(page, target)
    el.scroll_into_view_if_needed(timeout=3000)
    box = el.bounding_box()
    if not box:
        raise RuntimeError(f"Элемент {target} не виден")
    human_click_box(page, box)


def human_type(page, target, text):
    name = secrets.name_of(text) if len(text) >= secrets.MIN_LEN else None   # typed by letters, so the guard can't see it go in
    why = name and secrets.off_site(name, page.url)
    if why:
        raise PermissionError(why)
    human_click(page, target)
    for ch in text:
        page.keyboard.type(ch)
        time.sleep(random.uniform(0.04, 0.16) + (random.uniform(0.1, 0.3) if ch in " ,." else 0))


def human_scroll(page, dy):
    # scroll with the wheel in chunks, like a human
    left = dy
    while abs(left) >= 20:
        step = max(-400, min(400, left)) * random.uniform(0.6, 1.0)
        page.mouse.wheel(0, step)
        left -= step
        time.sleep(random.uniform(0.05, 0.2))
