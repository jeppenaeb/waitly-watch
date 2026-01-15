import os
import json
from datetime import datetime, timezone
from typing import List, Dict, Optional

from playwright.async_api import async_playwright, Page


DEBUG = os.getenv("WAITLY_DEBUG", "1") == "1"


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[waitly_positions {ts}] {msg}", flush=True)


async def debug_dump(page: Page, label: str) -> None:
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


async def accept_cookies_if_present(page: Page) -> None:
    buttons = [
        "Tillad alle",
        "Accepter",
        "Accept",
        "Allow all",
        "OK",
    ]

    for name in buttons:
        try:
            btn = page.get_by_role("button", name=name)
            if await btn.count() > 0:
                await btn.first.click(timeout=3000)
                log(f"Cookie banner accepted via button: {name}")
                await page.wait_for_timeout(500)
                return
        except Exception:
            pass


async def click_login(page: Page) -> None:
    for name in ["Login", "Log ind", "Sign in"]:
        try:
            btn = page.get_by_role("button", name=name)
            if await btn.count() > 0:
                await btn.first.click(timeout=5000)
                log(f"Clicked login button: {name}")
                await page.wait_for_timeout(800)
                return
        except Exception:
            pass

    for name in ["Login", "Log ind", "Sign in"]:
        try:
            link = page.get_by_role("link", name=name)
            if await link.count() > 0:
                await link.first.click(timeout=5000)
                log(f"Clicked login link: {name}")
                await page.wait_for_timeout(800)
                return
        except Exception:
            pass


async def find_visible_selector(page: Page, selectors: List[str]) -> Optional[str]:
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.first.is_visible():
                return sel
        except Exception:
            pass
    return None


async def fetch_positions(email: str, password: str) -> List[Dict]:
    if not email or not password:
        raise RuntimeError("WAITLY_LOGIN_EMAIL / WAITLY_LOGIN_PASSWORD not set")

    log("Starting Playwright")
    queues: List[Dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(15000)

        log("Opening https://my.waitly.dk/")
        await page.goto("https://my.waitly.dk/", wait_until="domcontentloaded")
        await debug_dump(page, "01_landing")

        await accept_cookies_if_present(page)
        await click_login(page)
        await accept_cookies_if_present(page)
        await debug_dump(page, "02_after_login_click")

        email_selectors = [
            "input[name='email']",
            "input[type='email']",
            "input[id*='email' i]",
            "input[placeholder*='mail' i]",
        ]
        password_selectors = [
            "input[type='password']",
            "input[name='password']",
            "input[id*='password' i]",
        ]

        email_sel = await find_visible_selector(page, email_selectors)
        pwd_sel = await find_visible_selector(page, password_selectors)

        if not email_sel or not pwd_sel:
            await debug_dump(page, "03_form_not_found")
            raise RuntimeError(
                "Could not find login form fields. "
                "Inspect state/debug_03_form_not_found.html/png"
            )

        log(f"Filling login form ({email_sel}, {pwd_sel})")
        await page.fill(email_sel, email)
        await page.fill(pwd_sel, password)

        log("Submitting login form")
        submitted = False
        for sel in [
            "button[type='submit']",
            "button:has-text('Login')",
            "button:has-text('Log ind')",
        ]:
            try:
                btn = page.locator(sel)
                if await btn.count() > 0 and await btn.first.is_visible():
                    await btn.first.click()
                    submitted = True
                    break
            except Exception:
                pass

        if not submitted:
            await debug_dump(page, "04_submit_not_found")
            raise RuntimeError("Could not find submit/login button")

        await page.wait_for_timeout(2000)
        await debug_dump(page, "05_after_submit")
        log(f"URL after submit: {page.url}")

        # --- detect explicit error messages ---
        error_texts = []
        for sel in [
            "[role='alert']",
            ".error",
            ".alert",
            "text=Forkert",
            "text=Ugyldig",
            "text=Invalid",
            "text=incorrect",
            "text=captcha",
            "text=robot",
            "text=verification",
        ]:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0:
                    txt = (await loc.first.inner_text()).strip()
                    if txt:
                        error_texts.append(txt)
            except Exception:
                pass

        if "/login" in page.url:
            if error_texts:
                log("Login errors detected:")
                for e in error_texts[:5]:
                    log(f"  - {e}")

            await debug_dump(page, "06_login_failed")
            raise RuntimeError(
                "Login failed (still on /login). "
                "See state/debug_06_login_failed.html/png"
            )

        log("Login succeeded (URL changed)")

        # --- SCRAPING (placeholder – tilpasses når vi ser dashboard-HTML) ---
        log("Scraping queues (generic)")
        await debug_dump(page, "07_before_scrape")

        for sel in ["a[href*='/queues/']", "a:has-text('venteliste')"]:
            try:
                cards = await page.query_selector_all(sel)
                if cards:
                    for card in cards:
                        try:
                            name = (await card.inner_text()).strip()
                            href = await card.get_attribute("href")
                            if name:
                                queues.append({"name": name, "url": href})
                        except Exception:
                            pass
                    break
            except Exception:
                pass

        log(f"Scrape complete. Queues found: {len(queues)}")
        if DEBUG:
            log("Queues sample: " + json.dumps(queues[:3], ensure_ascii=False))

        await browser.close()

    return queues
