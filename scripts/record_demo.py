"""Record a captioned walkthrough of one run as an MP4 (Playwright video + ffmpeg).

Usage:
  uv run python scripts/record_demo.py RUN_ID OUT.mp4 [--base http://127.0.0.1:7860] [--code CODE] [--theme dark]

It drives the real app like a person would: the one-click sign-in link (when a code
is given), the tension map, the slate, the phone animatic with its 3-second hook ring,
a trim that breaks a hard rule, and the quality page. Captions are drawn on the page,
so the video explains itself without a voice-over. Nothing is written to the run
(no decisions are saved).

Confidentiality: a video of a client run shows script lines. Keep those files out of
git (save them under runs/ or another git-ignored folder); only record the invented
sample show for anything public.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

from playwright.async_api import Page, async_playwright

CAPTION_JS = """(t) => {
  let el = document.getElementById('__cap');
  if (!el) {
    el = document.createElement('div');
    el.id = '__cap';
    Object.assign(el.style, {position: 'fixed', left: '50%', bottom: '28px', transform: 'translateX(-50%)',
      maxWidth: '82%', padding: '10px 18px', background: 'rgba(12,20,26,0.92)', color: '#ECE6D8',
      font: '600 20px/1.4 "IBM Plex Sans", system-ui, sans-serif', borderRadius: '8px', zIndex: 99999,
      textAlign: 'center', pointerEvents: 'none', border: '1px solid rgba(242,163,58,0.7)'});
    document.body.appendChild(el);
  }
  el.textContent = t;
  el.style.display = t ? 'block' : 'none';
}"""


async def cap(page: Page, text: str, seconds: float) -> None:
    await page.evaluate(CAPTION_JS, text)
    await page.wait_for_timeout(int(seconds * 1000))


async def go(page: Page, path: str) -> None:
    """Client-side navigation, as a person clicking through (keeps the session)."""
    await page.evaluate("p => { history.pushState({}, '', p); dispatchEvent(new PopStateEvent('popstate')); }", path)
    await page.wait_for_load_state("networkidle")


async def scroll(page: Page, total: int, steps: int = 8, pause: int = 220) -> None:
    for _ in range(steps):
        await page.mouse.wheel(0, total / steps)
        await page.wait_for_timeout(pause)


def _tracked_by_git(path: Path) -> bool:
    """True when git would commit this path (inside the repo and not ignored)."""
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    if inside.returncode != 0:
        return False
    return subprocess.run(["git", "check-ignore", "-q", str(path)]).returncode != 0


async def record(run: str, out: Path, base: str, code: str | None, theme: str) -> None:
    if code and _tracked_by_git(out):
        # Signed in, the runs list shows the client's show titles: such a video must stay private.
        raise SystemExit(f"Refusing: {out} would be committed. Save signed-in recordings under runs/ "
                         "(git-ignored), or record without --code for a public video.")
    tmp = Path(tempfile.mkdtemp(prefix="coldopen-video-"))
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1280, "height": 720}, device_scale_factor=1,
                                        color_scheme=theme, record_video_dir=str(tmp),
                                        record_video_size={"width": 1280, "height": 720})
        page = await ctx.new_page()
        await page.goto(base + "/" + (f"#code={code}" if code else ""))
        await page.wait_for_load_state("networkidle")
        await cap(page, "Cold Open reads a season of microdrama scripts and picks the promos to launch with.", 4)
        if code:
            await cap(page, "Reviewers sign in with one click: the link carries the access code.", 3.5)

        await go(page, f"/run/{run}/map")
        await page.wait_for_timeout(1800)  # the map "develops" in
        await cap(page, "The tension map: every second of every episode. The brackets are the promos it chose.", 3)
        box = await page.locator("canvas").first.bounding_box()
        if box:
            for i in range(10):
                await page.mouse.move(box["x"] + box["width"] * (0.15 + 0.07 * i), box["y"] + box["height"] * (0.1 + 0.08 * i))
                await page.wait_for_timeout(260)
        await cap(page, "Hover any row to read the line at that moment.", 2.5)

        await go(page, f"/run/{run}/slate")
        await cap(page, "The launch slate: a hero and four challengers that differ in story engine and footage.", 4.5)
        await scroll(page, 900)
        await cap(page, "Every score is explained. The bench holds the next ten.", 3.5)
        await page.mouse.wheel(0, -3000)
        await page.wait_for_timeout(400)

        await go(page, f"/run/{run}/promo/")
        await page.wait_for_timeout(800)
        await cap(page, "The cut room plays the promo the way a phone would.", 3)
        play = page.get_by_role("button", name="Play")
        if await play.count():
            await play.first.click()
            await cap(page, "The ring fills during the first 3 seconds: the hook has to land inside it.", 6)
            await cap(page, "Lines appear at their real timecodes, with faces described rather than named.", 6)
            pause = page.get_by_role("button", name="Pause")
            if await pause.count():
                await pause.first.click()

        handle = page.get_by_role("slider", name="Segment 1 start")
        if await handle.count():
            await handle.first.scroll_into_view_if_needed()
            await handle.first.focus()
            await cap(page, "Trims snap to line and shot boundaries.", 2)
            for _ in range(6):
                await page.keyboard.press("ArrowRight")
                await page.wait_for_timeout(350)
            await cap(page, "Push the hook out of the first 3 seconds and approval is blocked, here and on the server.", 4.5)
            reset = page.get_by_role("button", name="Reset trims")
            if await reset.count():
                await reset.first.click()
                await page.wait_for_timeout(500)
        await cap(page, "Then approve, mark organic-only or reject, and export an EDL for the editor.", 4)

        await go(page, f"/run/{run}/quality")
        await page.wait_for_timeout(600)
        await cap(page, "Quality: why candidates were rejected, ablations against simpler baselines, real failures.", 4)
        await scroll(page, 1400, steps=10)
        await page.mouse.wheel(0, -4000)
        await cap(page, "AI judges meaning. Code owns facts. The editor makes the final call.", 4)

        video = page.video
        await ctx.close()
        await browser.close()
        src = Path(await video.path()) if video else None

    if not src or not src.exists():
        raise SystemExit("no video was recorded")
    out.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-crf", "24", "-preset", "medium", "-movflags", "+faststart", str(out)], check=True)
    else:
        out = out.with_suffix(".webm")
        shutil.copy(src, out)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("out", type=Path)
    ap.add_argument("--base", default="http://127.0.0.1:7860")
    ap.add_argument("--code")
    ap.add_argument("--theme", default="dark", choices=["dark", "light"])
    a = ap.parse_args()
    asyncio.run(record(a.run, a.out, a.base, a.code, a.theme))
