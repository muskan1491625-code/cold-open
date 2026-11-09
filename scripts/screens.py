"""Screenshot every screen at desktop and phone sizes (UI self-critique loop, PROMPT section 13).

Usage: uv run python scripts/screens.py RUN_ID OUT_DIR [--base http://127.0.0.1:7860] [--code CODE] [--theme light]

Screens of a run built from the private pack show script text: write those to runs/ (git-ignored),
never to docs/. Committed screenshots must come from an invented show.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

SIZES = {"desktop": (1440, 900), "phone": (390, 844)}


async def main(run: str, out: Path, base: str, code: str | None, theme: str | None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(f"{base}/api/runs/{run}/slate", headers={"X-Access-Code": code or ""})
    hero = json.loads(urllib.request.urlopen(req).read())["slate"]["hero"]
    q = f"?theme={theme}" if theme else ""
    pages = {
        "map": f"/run/{run}/map{q}",
        "slate": f"/run/{run}/slate{q}",
        "cutroom": f"/run/{run}/promo/{hero}{q}",
        "quality": f"/run/{run}/quality{q}",
        "new": f"/new{q}",
    }
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for size, (w, h) in SIZES.items():
            phone = w < 600  # a real phone has touch and no hover, which hides keyboard hints
            ctx = await browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, is_mobile=phone,
                                            has_touch=phone, color_scheme="light" if theme == "light" else "dark")
            page = await ctx.new_page()
            await page.goto(base + pages["map"])
            if code and await page.locator("#code").count():
                await page.screenshot(path=out / f"gate-{size}.png")
                await page.fill("#code", code)
                await page.click("button[type=submit]")
                await page.wait_for_selector("#code", state="detached")
            for name, path in pages.items():
                # Client-side navigation, like a person clicking through (the session token is in sessionStorage).
                await page.evaluate("p => { history.pushState({}, '', p); dispatchEvent(new PopStateEvent('popstate')); }", path)
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(1900 if name == "map" else 500)
                await page.screenshot(path=out / f"{name}-{size}{'-' + theme if theme else ''}.png", full_page=True)
            await ctx.close()
        await browser.close()
    print(f"wrote screenshots to {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("out", type=Path)
    ap.add_argument("--base", default="http://127.0.0.1:7860")
    ap.add_argument("--code")
    ap.add_argument("--theme")
    a = ap.parse_args()
    asyncio.run(main(a.run, a.out, a.base, a.code, a.theme))
