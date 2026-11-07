"""Screenshots of the running site (serve.py) at the sizes that matter, to check a change before showing it:
python3 site/shot.py [seconds ...]. Always both pixel densities: a bug once showed only on Retina (density 2).
Needs Playwright with Chrome; pictures go to /tmp/sena-site/."""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

SIZES = [("mac", 1512, 945, 2), ("wide", 1440, 900, 1), ("tablet", 1024, 768, 1), ("phone", 390, 844, 3)]
OUT = Path("/tmp/sena-site")


async def main(moments):
    OUT.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome")
        for name, w, h, density in SIZES:
            page = await browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=density)
            page.on("pageerror", lambda e, name=name: print(f"{name}: error: {e}"))
            await page.goto("http://127.0.0.1:8790/")
            done = 0.0
            for t in moments:
                await page.wait_for_timeout((t - done) * 1000)
                done = t
                await page.screenshot(path=OUT / f"{name}_{t:g}s.png")
            await page.close()
        await browser.close()
    print(OUT)


asyncio.run(main([float(x) for x in sys.argv[1:]] or [3.0, 10.0]))
