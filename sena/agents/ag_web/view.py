"""How the agent sees the page and finds elements on it.
elements — a numbered list of buttons and fields: the main window, nested frames (iframe), and open shadow DOM blocks;
read_page — readable text; screenshot — a frame with numbers drawn on it; locate — an element by its number.
The numbering idea comes from browser-use: the model doesn't guess selectors, it takes a number from the list."""
import itertools
import re
import weakref

from sena.agents.ag_web import browser

# The number is set as the data-sena-id attribute and persists between calls. Nested frames get a prefix on the number: f1.7.
SCAN_JS = r"""({prefix}) => {
  const sel = 'a[href], button, input, textarea, select, summary, [contenteditable="true"], [onclick], [tabindex]:not([tabindex="-1"]),' +
              '[role=button], [role=link], [role=tab], [role=menuitem], [role=checkbox], [role=radio], [role=option], [role=switch], [role=textbox], [role=combobox]';
  const first = !window.__senaNext;
  let next = window.__senaNext || 1;
  const found = [];
  const collect = (root) => {   // including open shadow roots: a plain querySelectorAll doesn't see into them
    root.querySelectorAll(sel).forEach((el) => found.push(el));
    root.querySelectorAll('*').forEach((host) => { if (host.shadowRoot) collect(host.shadowRoot); });
  };
  collect(document);
  const out = [];
  for (const el of found) {
    const r = el.getBoundingClientRect(), st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none' || el.disabled) continue;
    const inView = r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
    if (inView) {  // element is covered by something else (a popup, a banner) — don't show it
      const root = el.getRootNode();
      const at = (root.elementFromPoint ? root : document).elementFromPoint(
        Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1), Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1));
      if (at && at !== el && !el.contains(at) && !at.contains(el) && at !== root.host) continue;
    }
    let isNew = false;
    if (!el.dataset.senaId) { el.dataset.senaId = prefix + (next++); isNew = true; }
    const tag = el.tagName.toLowerCase(), type = el.type && tag === 'input' ? ' type=' + el.type : '';
    const img = el.querySelector && el.querySelector('img[alt]');
    let text = (el.getAttribute('aria-label') || el.innerText || el.placeholder || el.title || el.alt || (img && img.alt) ||
                (tag === 'a' ? decodeURI(el.getAttribute('href') || '') : '')).replace(/\s+/g, ' ').trim().slice(0, 80);
    // a password, a card number or a one-time code is never shown to the model: only that the field is filled (browser-use)
    const hidden = el.type === 'password' || /cc-|one-time-code|current-password|new-password/.test(el.autocomplete || '') ||
                   /card|cvc|cvv|парол|password|pin/i.test((el.name || '') + ' ' + (el.id || ''));
    if ((tag === 'input' || tag === 'textarea') && el.value) text += hidden ? ' value=[заполнено]' : ' value="' + String(el.value).slice(0, 40) + '"';
    out.push({id: el.dataset.senaId, tag, type, text, inView, isNew: isNew && !first, x: r.left, y: r.top, w: r.width, h: r.height});
  }
  window.__senaNext = next;
  return out;
}"""

# boxes with numbers drawn over the page for the duration of the screenshot
MARKS_JS = r"""(marks) => {
  const box = document.createElement('div');
  box.id = '__sena_marks';
  box.style.cssText = 'position:fixed;left:0;top:0;width:100vw;height:100vh;pointer-events:none;z-index:2147483647';
  for (const m of marks) {
    const r = document.createElement('div');
    r.style.cssText = `position:absolute;left:${m.x}px;top:${m.y}px;width:${m.w}px;height:${m.h}px;border:2px solid #ec6933;box-sizing:border-box`;
    const t = document.createElement('div');
    t.textContent = m.id;
    t.style.cssText = `position:absolute;left:-2px;top:${m.y < 16 ? 0 : -16}px;background:#ec6933;color:#fff;font:bold 11px/14px monospace;padding:0 3px`;
    r.appendChild(t);
    box.appendChild(r);
  }
  document.documentElement.appendChild(box);
}"""
UNMARK_JS = "() => { const m = document.getElementById('__sena_marks'); if (m) m.remove(); }"

# buttons on cookie banners that get in the way
BANNER_RE = re.compile(r"^(принять|принять вс[её]|согласен|согласна|соглас[ие]ться|хорошо|понятно|ок|okay|ok|accept|accept all|allow all|agree|i agree|got it|allow cookies|разрешить|да, я согласен)\W*$", re.I)
# such a button is clicked unasked only inside a cookie or consent banner: the same «Разрешить» on a sign-in page gives a site the
# owner's account, «Accept» accepts an invitation, «ОК» confirms a deletion
CONSENT_JS = r"""(el) => { let n = el;
  for (let i = 0; n && i < 6; i++) {
    const mark = [n.id, typeof n.className === 'string' ? n.className : '', n.getAttribute ? n.getAttribute('aria-label') : ''].join(' ').toLowerCase();
    if (/cookie|consent|gdpr|ccpa|onetrust|didomi|usercentrics/.test(mark)) return true;
    if (i > 0 && /cookie|куки|персональн\S* данн/i.test((n.innerText || '').slice(0, 1500))) return true;
    n = n.parentElement || (n.getRootNode && n.getRootNode().host) || null; }
  return false; }"""
SIGN_IN = re.compile(r"oauth|authorize|/consent|accounts\.google\.|/login|/signin", re.I)   # a page where "allow" grants an account, not cookies
MAX_MARKS = 80
_frame_tags = weakref.WeakKeyDictionary()   # nested frame -> its prefix: gone with the frame, never handed to another
_frame_numbers = itertools.count(1)


def ready(page, timeout=8, settle=2):
    # wait for the page to finish loading: DOM ready (no more than timeout seconds), then the network goes quiet (no more than settle):
    # on live sites the network never truly goes quiet, so there's no point waiting the full 8 seconds on every call
    for state, limit in (("domcontentloaded", timeout), ("networkidle", settle)):
        try:
            page.wait_for_load_state(state, timeout=limit * 1000)
        except Exception:
            pass


def dismiss_banners(page):
    # close the cookie banner if there is one: click the first visible button with typical wording inside a consent banner
    if SIGN_IN.search(page.url or ""):
        return ""
    try:
        for b in page.locator("button, [role=button], a").all()[:200]:
            text = (b.inner_text(timeout=200) or "").strip()
            if BANNER_RE.match(text) and b.is_visible() and b.evaluate(CONSENT_JS, timeout=500):
                b.click(timeout=1000)
                return f"закрыт баннер «{text}»"
    except Exception:
        pass
    return ""


def _viewport(page):
    size = page.viewport_size or page.evaluate("() => ({width: innerWidth, height: innerHeight})")
    return size["width"], size["height"]


def _frame_box(frame):
    # where the nested frame is on the page; None if it's hidden or tiny (trackers, ads)
    try:
        box = frame.frame_element().bounding_box()
    except Exception:
        return None
    return box if box and box["width"] >= 4 and box["height"] >= 4 else None


def scan(page):
    """All visible interactive elements on the page and its nested frames: [dict]. Coordinates are on the page; the main window comes first."""
    vw, vh = _viewport(page)
    items = []
    for frame in page.frames:
        if frame.parent_frame is None:
            prefix, dx, dy, where = "", 0, 0, ""
        else:
            box = _frame_box(frame)
            if box is None:
                continue
            prefix = _frame_tags.get(frame) or _frame_tags.setdefault(frame, f"f{next(_frame_numbers)}.")
            dx, dy, where = box["x"], box["y"], frame.url
        try:
            part = frame.evaluate(SCAN_JS, {"prefix": prefix})
        except Exception:
            continue   # the frame navigated to another page or was closed
        for it in part:
            it["x"] += dx
            it["y"] += dy
            it["frame"] = where
            if where:
                it["inView"] = it["inView"] and it["x"] + it["w"] > 0 and it["y"] + it["h"] > 0 and it["x"] < vw and it["y"] < vh
            items.append(it)
    return items


def elements(page, limit=150):
    ready(page)
    note = dismiss_banners(page)
    lines = ([note] if note else []) + [f"[событие] {e}" for e in browser.take_notes(page)]
    group, shown = None, 0
    found = scan(page)
    # what is on the screen first, the rest after it: a long page cut at the limit keeps what the agent can act on now (browser-use)
    found = [it for it in found if it["inView"]] + [it for it in found if not it["inView"]]
    for it in found:
        if shown >= limit:
            lines.append(f"…ещё элементов: {len(found) - shown} (ниже или выше на странице: прокрути и посмотри снова)")
            break
        if it["frame"] != group:
            group = it["frame"]
            if group:
                lines.append(f"-- вложенное окно {group[:80]} --")
        where = "" if it["inView"] else " (ниже/выше, нужно прокрутить)"
        lines.append(f"{'*' if it['isNew'] else ''}[{it['id']}]<{it['tag']}{it['type']}> {it['text']}{where}")
        shown += 1
    if not shown:
        lines.append("(нет видимых элементов)")
    return "\n".join(lines)


def read_page(page, limit=8000, start=0):
    """The title, address, and readable text of the page together with its nested frames; a long page is read on from start."""
    ready(page)
    parts = [f"{page.title()} — {page.url}"]
    for frame in page.frames:
        nested = frame.parent_frame is not None
        if nested and _frame_box(frame) is None:
            continue
        try:
            text = frame.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            continue
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if text:
            parts.append(f"-- вложенное окно {frame.url[:80]} --\n{text}" if nested else text)
    whole = "\n\n".join(parts)
    if start or len(whole) > limit:   # never a silent cut: the agent sees where it is and how to read on
        piece = whole[start:start + limit]
        more = f"; дальше — read_page(page, start={start + limit})" if start + limit < len(whole) else "; это конец"
        return f"[знаки {start + 1}–{start + len(piece)} из {len(whole)}{more}]\n{piece}"
    return whole


def locate(page, target):
    """An element by its number from elements (5 or "f1.7"), or by an ordinary selector; also searches inside nested frames.
    A number that is no longer on the page fails at once with what to do (Playwright MCP: a stale ref is an error, not a five-
    second wait and a long traceback)."""
    number = re.fullmatch(r"f?\d+(\.\d+)?", str(target))
    selector = f'[data-sena-id="{target}"]' if number else str(target)
    first = None
    for frame in page.frames:
        loc = frame.locator(selector).first
        first = first or loc
        try:
            if loc.count():
                return loc
        except Exception:
            continue
    if number:
        raise LookupError(f"Элемента {target} на странице уже нет (она изменилась): вызови elements(page) снова и возьми новый номер")
    return first


def screenshot(page, marks=True):
    """A tab frame in CSS pixels: the coordinates on the image match the click coordinates.
    marks — draw the element numbers from elements onto the image. Returns (jpeg, width, height, url, number count)."""
    width, height = _viewport(page)
    drawn = 0
    if marks:
        try:
            visible = [it for it in scan(page) if it["inView"]][:MAX_MARKS]
            page.evaluate(MARKS_JS, [{k: it[k] for k in ("id", "x", "y", "w", "h")} for it in visible])
            drawn = len(visible)
        except Exception:
            drawn = 0
    try:
        image = page.screenshot(type="jpeg", quality=60, scale="css")
    finally:
        if drawn:
            try:
                page.evaluate(UNMARK_JS)
            except Exception:
                pass
    return image, width, height, page.url, drawn
