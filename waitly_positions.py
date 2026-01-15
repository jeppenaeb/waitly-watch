import os
import json
import traceback
from datetime import datetime, timezone
from typing import List, Dict

from playwright.async_api import async_playwright


DEBUG = os.getenv("WAITLY_DEBUG", "1") == "1"


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[waitly_positions {ts}] {msg}", flush=True)


async def debug_dump(page, label: str) -> None:
    if not DEBUG:
        return

    os.makedirs("state", exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in label)[:80]

    try:
        png = f"state/debug_{safe}.png"
        html = f"state/debug_{safe}.html"
        await page.screenshot(path=png, full_page=True)
        with open(html, "w", encoding="utf-8") as f:
            f.write(await page.content())
        log(f"DEBUG dump written: {png}, {html}")
    except Exception as e:
        log(f"DEBUG dump failed: {e}")


async def fetch_positions(email: str, password: str) -> List[Dict]:
    if not email or not password:
        raise RuntimeError("WAITLY_LOGIN_EMAIL / WAITLY_LOGIN_PASSWORD not set")

    log("Starting Playwright")
    queues: List[Dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        log("Opening https://my.waitly.dk/")
        await page.goto("https://my.waitly.dk/", wait_until="domcontentloaded")
        await debug_dump(page, "01_landing")

        log("Filling login form")
        await page.fill("input[type='email']", email)
        await page.fill("input[type='password']", password)
        await page.click("button[type='submit']")

        await page.wait_for_timeout(2000)
        await debug_dump(page, "02_after_submit")

        log(f"URL after submit: {page.url}")

        # --- login sanity check ---
        try:
            await page.wait_for_selector("text=Ventelister", timeout=15000)
            log("Login check PASSED")
        except Exception:
            await debug_dump(page, "03_login_failed")
            raise RuntimeError("Login FAILED – did not reach dashboard")

        # --- scrape queues ---
        log("Scraping queue cards")
        await debug_dump(page, "04_before_scrape")

        cards = await page.query_selector_all("a[href*='/queues/']")
        log(f"Queue links found: {len(cards)}")

        for card in cards:
            try:
                name = (await card.inner_text()).strip()
                href = await card.get_attribute("href")
                queues.append({
                    "name": name,
                    "url": href,
                })
            except Exception:
                continue

        log(f"Scrape complete. Queues found: {len(queues)}")
        if DEBUG:
            log("Queues sample: " + json.dumps(queues[:3], ensure_ascii=False))

        await browser.close()

    return queues
