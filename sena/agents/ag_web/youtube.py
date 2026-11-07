"""YouTube in one call: pause other tabs, search, open the first video, play it, skip ads."""
import time
from urllib.parse import quote_plus

from sena.agents.ag_web import browser
from sena.core import cancel, status
from sena.core.tools import Failure


def youtube_play(page, max_wait=25):
    # starts the video on the open YouTube page, skips ads, waits until the actual video is playing.
    # for ads: click "skip" if it's there; otherwise mute, speed up 16x, and jump to the end
    start, s = time.time(), None
    while time.time() - start < max_wait:
        cancel.check_stop()
        s = page.evaluate("""() => { const v = document.querySelector('video'); if (!v) return null;
            const ad = !!document.querySelector('.ad-showing, .ad-interrupting');
            const skip = document.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button-modern, .ytp-ad-skip-button, .ytp-ad-skip-button-slot button');
            if (skip && skip.offsetParent) skip.click();
            if (ad) { v.muted = true; try { v.playbackRate = 16; } catch (e) {}
                      if (isFinite(v.duration) && v.duration > 0) v.currentTime = v.duration; }
            else if (window.__senaMuted) { v.muted = false; v.playbackRate = 1; window.__senaMuted = false; }
            if (ad) window.__senaMuted = true;
            if (v.paused) v.play();
            return {ad, paused: v.paused, t: v.currentTime} }""")
        if s and not s["ad"] and not s["paused"] and s["t"] > 2:
            return f"Играет: {page.title()}"
        status.show("Пропускаю рекламу" if s and s["ad"] else "Запускаю видео")
        time.sleep(0.5)
    return Failure(f"Не удалось запустить за {max_wait} сек: {s}")


def youtube_pause_others(page):
    # pause videos in other YouTube tabs, so two don't play at once
    n = 0
    for p in browser.context().pages:
        if p is not page and "youtube.com" in p.url:
            try:
                n += int(p.evaluate("() => { const v = document.querySelector('video'); if (v && !v.paused) { v.pause(); return 1 } return 0 }"))
            except Exception:
                pass
    return n


def youtube_search_play(page, query):
    # the whole flow in one call: pause other tabs -> search -> first video -> playback
    paused = youtube_pause_others(page)
    page.goto("https://www.youtube.com/results?search_query=" + quote_plus(query), wait_until="domcontentloaded")
    first = page.locator("ytd-video-renderer a#video-title").first
    first.wait_for(timeout=8000)
    title, href = first.get_attribute("title") or first.inner_text(), first.get_attribute("href")
    if not href:   # a result without a link (a live block, a shelf): nothing to open
        return Failure(f"У первого результата «{title}» нет ссылки на ролик: открой его кликом")
    page.goto("https://www.youtube.com" + href, wait_until="domcontentloaded")
    result = youtube_play(page)
    return (f"Поставила на паузу другие вкладки: {paused}. " if paused else "") + f"Выбрала «{title}». {result}"
