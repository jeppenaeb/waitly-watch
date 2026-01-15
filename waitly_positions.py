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


async def dump_body_text(page: Page, label: str) -> str:
    """
    Dump visible body text to state/ and return it. Useful when login fails but
    error selectors are unknown.
    """
    os.makedirs("state", exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in label)[:80]
    path = f"state/{safe}.txt"

    try:
        body_text = await page.locator("body").inner_text()
    except Exception:
        # fallback if inner_text fails
        body_text = ""

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(body_text)
        log(f"Body text dumped: {path} (chars={len(body_text)})")
    except Exception as e:
        log(f"Body text dump failed: {e}")

    return body_text


def extract_interesting_lines(text: str) -> List[str]:
    """
    Pull lines likely to contain the reason for login failure.
    """
    keywords = [
        "forkert", "ugyldig", "invalid", "incorrect", "fejl",
        "captcha", "robot", "turnstile", "hcaptcha", "recaptcha",
        "verification", "verificer", "bekræft", "kode", "2fa", "two-factor",
        "magic", "link", "email", "e-mail", "send", "sende", "tjek din mail",
        "for mange", "too many", "rate", "limit", "blocked", "blokeret",
    ]

    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if any(k in low for k in keywords):
            lines.append(line)

    # de-dupe while keeping order
    seen = set()
    out = []
    for l in lines:
        if l not in seen:
            out.append(l)
            seen.add(l)

    return out[:40]


async def accept_cookies_if_present(page: Page) -> None:
    # best-effort
    for name in ["Tillad alle", "Accepter", "Accept", "Allow all", "OK"]:
        try:
            btn = page.get_by_role("button", name=name)
            if await btn.count() > 0:
                await btn.first.click(timeout=3000)
                log(f"Cookie banner accepted via button: {name}")
                await page.wait_for_timeout(500)
                return
        except Exception:
            pass

    # common ids
    for sel in [
        "button#onetrust-accept-btn-handler",
        "button[aria-label*='accept' i]",
        "button:has-text('Accept')",
        "button:has-text('Accepter')",
    ]:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.click(timeout=3000)
                log(f"Cookie banner accepted via selector: {sel}")
                await page.wait_for_timeout(500)
                return
        except Exception:
            pass


async def click_login(page: Page) -> None:
    # button first
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

    # link fallback
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
        await debug_dump(page, "debug_01_landing")

        await accept_cookies_if_present(page)
        await click_login(page)
        await accept_cookies_if_present(page)
        await debug_dump(page, "debug_02_after_login_click")

        email_selectors = [
            "input[name='email']",
            "input[type='email']",
            "input[id*='email' i]",
            "input[placeholder*='mail' i]",
            "input[autocomplete='email']",
        ]
        password_selectors = [
            "input[type='password']",
            "input[name='password']",
            "input[id*='password' i]",
            "input[autocomplete='current-password']",
        ]

        email_sel = await find_visible_selector(page, email_selectors)
        pwd_sel = await find_visible_selector(page, password_selectors)

        if not email_sel or not pwd_sel:
            await debug_dump(page, "debug_03_form_not_found")
            body = await dump_body_text(page, "debug_login_form_not_found_body")
            interesting = extract_interesting_lines(body)
            if interesting:
                log("Interesting body lines:")
                for line in interesting:
                    log(f"  - {line}")
            raise RuntimeError(
                "Could not find login form fields. Inspect state/debug_03_form_not_found.html/png "
                "and state/debug_login_form_not_found_body.txt"
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
            "button:has-text('Sign in')",
        ]:
            try:
                btn = page.locator(sel)
                if await btn.count() > 0 and await btn.first.is_visible():
                    # log disabled state (if any)
                    try:
                        disabled = await btn.first.get_attribute("disabled")
                        aria_disabled = await btn.first.get_attribute("aria-disabled")
                        log(f"Submit button attrs disabled={disabled} aria-disabled={aria_disabled}")
                    except Exception:
                        pass

                    await btn.first.click()
                    submitted = True
                    break
            except Exception:
                pass

        if not submitted:
            await debug_dump(page, "debug_04_submit_not_found")
            body = await dump_body_text(page, "debug_submit_not_found_body")
            interesting = extract_interesting_lines(body)
            if interesting:
                log("Interesting body lines:")
                for line in interesting:
                    log(f"  - {line}")
            raise RuntimeError("Could not find submit/login button")

        # wait a bit for either navigation or error rendering
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        await page.wait_for_timeout(1500)
        await debug_dump(page, "debug_05_after_submit")
        log(f"URL after submit: {page.url}")

        # --- If still on /login: dump body text and try to surface WHY ---
        if "/login" in page.url:
            await debug_dump(page, "debug_06_login_failed")

            body = await dump_body_text(page, "debug_06_login_failed_body")
            interesting = extract_interesting_lines(body)

            if interesting:
                log("Login failure hints (from visible page text):")
                for line in interesting:
                    log(f"  - {line}")
            else:
                # print first chunk to help us even if no keywords matched
                preview = body.strip().replace("\r", "")[:1200]
                log("Login failure body preview (first 1200 chars):")
                for line in preview.split("\n")[:40]:
                    if line.strip():
                        log(f"  > {line.strip()}")

            raise RuntimeError(
                "Login failed (still on /login). See state/debug_06_login_failed.html/png "
                "and state/debug_06_login_failed_body.txt"
            )

        log("Login succeeded (URL changed)")

        # --- SCRAPING (generic placeholder; we’ll tailor once we have dashboard HTML) ---
        log("Scraping queues (generic)")
        await debug_dump(page, "debug_07_before_scrape")

        for sel in ["a[href*='/queues/']", "a[href*='queue']", "a:has-text('venteliste')"]:
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
