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


async def best_effort_accept_cookies(page: Page) -> None:
    """
    Try to accept/close cookie banners if present.
    This is best-effort: no failure if not found.
    """
    candidates = [
        ("button", "Accepter"),
        ("button", "Accept"),
        ("button", "Tillad alle"),
        ("button", "Allow all"),
        ("button", "OK"),
        ("button", "Jeg accepterer"),
        ("button", "Acceptér alle"),
    ]

    for role, name in candidates:
        try:
            btn = page.get_by_role(role, name=name)
            if await btn.count() > 0:
                await btn.first.click(timeout=2000)
                log(f"Cookie banner handled via button: {name}")
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue

    # Also try generic common selectors
    for sel in [
        "button#onetrust-accept-btn-handler",
        "button[aria-label*='accept' i]",
        "button:has-text('Accept')",
        "button:has-text('Accepter')",
    ]:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.click(timeout=2000)
                log(f"Cookie banner handled via selector: {sel}")
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue


async def click_login_if_needed(page: Page) -> None:
    """
    If the login form isn't visible yet, try clicking a "Log ind" / "Login" entry.
    Best effort.
    """
    for name in ["Log ind", "Login", "Sign in", "Indtast login"]:
        try:
            btn = page.get_by_role("button", name=name)
            if await btn.count() > 0:
                await btn.first.click(timeout=4000)
                log(f"Clicked login button: {name}")
                await page.wait_for_timeout(800)
                return
        except Exception:
            pass

    # Sometimes login is a link
    for name in ["Log ind", "Login", "Sign in"]:
        try:
            link = page.get_by_role("link", name=name)
            if await link.count() > 0:
                await link.first.click(timeout=4000)
                log(f"Clicked login link: {name}")
                await page.wait_for_timeout(800)
                return
        except Exception:
            pass


async def find_first_visible(page: Page, selectors: List[str]) -> Optional[str]:
    """
    Return the first selector that matches a visible element.
    """
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                # visible() can throw if detached; keep it simple:
                if await loc.first.is_visible():
                    return sel
        except Exception:
            continue
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

        # More responsive timeouts (we still wait explicitly where needed)
        page.set_default_timeout(15000)

        log("Opening https://my.waitly.dk/")
        await page.goto("https://my.waitly.dk/", wait_until="domcontentloaded")
        await debug_dump(page, "01_landing")

        await best_effort_accept_cookies(page)

        # Email/password might not be on landing; try clicking "Log ind"
        await click_login_if_needed(page)
        await best_effort_accept_cookies(page)
        await debug_dump(page, "02_after_cookie_or_login_click")

        # Robust selectors (email)
        email_selectors = [
            "input[type='email']",
            "input[name='email']",
            "input[id*='email' i]",
            "input[placeholder*='mail' i]",
            "input[autocomplete='email']",
            "input[type='text'][name*='email' i]",
        ]

        password_selectors = [
            "input[type='password']",
            "input[name='password']",
            "input[id*='password' i]",
            "input[autocomplete='current-password']",
        ]

        email_sel = await find_first_visible(page, email_selectors)
        pwd_sel = await find_first_visible(page, password_selectors)

        if not email_sel or not pwd_sel:
            # Try a last-ditch approach via labels
            try:
                # Danish/English label guesses
                for label in ["E-mail", "Email", "Mail", "Brugernavn", "Username"]:
                    loc = page.get_by_label(label)
                    if await loc.count() > 0 and await loc.first.is_visible():
                        email_sel = f"label:{label}"
                        await loc.first.fill(email)
                        log(f"Filled email via label: {label}")
                        break
            except Exception:
                pass

            try:
                for label in ["Adgangskode", "Password", "Kodeord"]:
                    loc = page.get_by_label(label)
                    if await loc.count() > 0 and await loc.first.is_visible():
                        pwd_sel = f"label:{label}"
                        await loc.first.fill(password)
                        log(f"Filled password via label: {label}")
                        break
            except Exception:
                pass

        # If we still don't have selectors, dump and fail with clear message
        if not email_sel or not pwd_sel:
            await debug_dump(page, "03_form_not_found")
            raise RuntimeError(
                "Could not find visible email/password fields on my.waitly.dk. "
                "Open state/debug_03_form_not_found.html/png in the repo to inspect the page."
            )

        # Fill using selectors if not already filled via labels
        log(f"Filling login form using selectors: email={email_sel}, password={pwd_sel}")
        try:
            if not str(email_sel).startswith("label:"):
                await page.fill(email_sel, email)
            if not str(pwd_sel).startswith("label:"):
                await page.fill(pwd_sel, password)
        except Exception:
            await debug_dump(page, "04_fill_failed")
            raise

        # Submit
        log("Submitting login form")
        submit_candidates = [
            "button[type='submit']",
            "button:has-text('Log ind')",
            "button:has-text('Login')",
            "button:has-text('Sign in')",
        ]
        submitted = False
        for sel in submit_candidates:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0 and await loc.first.is_visible():
                    await loc.first.click()
                    submitted = True
                    break
            except Exception:
                continue

        if not submitted:
            # Try role-based
            for name in ["Log ind", "Login", "Sign in"]:
                try:
                    btn = page.get_by_role("button", name=name)
                    if await btn.count() > 0:
                        await btn.first.click()
                        submitted = True
                        break
                except Exception:
                    continue

        if not submitted:
            await debug_dump(page, "05_submit_not_found")
            raise RuntimeError("Could not find a submit/login button to click.")

        await page.wait_for_timeout(2000)
        await debug_dump(page, "06_after_submit")
        log(f"URL after submit: {page.url}")

        # --- login sanity check (make this flexible) ---
        # We don't know the exact dashboard text; try a few candidates.
        ok = False
        for text in ["Ventelister", "Mine ventelister", "Queues", "Dashboard", "Profil", "Log ud", "Logout"]:
            try:
                if await page.locator(f"text={text}").count() > 0:
                    ok = True
                    break
            except Exception:
                pass

        if not ok:
            await debug_dump(page, "07_login_check_failed")
            raise RuntimeError(
                "Login check FAILED (did not detect dashboard markers). "
                "Inspect state/debug_07_login_check_failed.html/png."
            )

        log("Login check PASSED (dashboard marker detected)")

        # --- scrape queues (this part is placeholder-ish; we'll tailor after we see HTML) ---
        log("Scraping queue links/cards (generic)")
        await debug_dump(page, "08_before_scrape")

        # Try a couple of generic patterns
        for sel in ["a[href*='/queues/']", "a[href*='queue']", "a:has-text('venteliste')"]:
            try:
                cards = await page.query_selector_all(sel)
                if cards:
                    log(f"Found {len(cards)} elements using selector: {sel}")
                    for card in cards:
                        try:
                            name = (await card.inner_text()).strip()
                            href = await card.get_attribute("href")
                            if name:
                                queues.append({"name": name, "url": href})
                        except Exception:
                            continue
                    break
            except Exception:
                continue

        log(f"Scrape complete. Queues found: {len(queues)}")
        if DEBUG:
            log("Queues sample: " + json.dumps(queues[:3], ensure_ascii=False))

        await browser.close()

    return queues
