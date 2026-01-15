import os
import json
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any

from playwright.async_api import async_playwright, Page


DEBUG = os.getenv("WAITLY_DEBUG", "1") == "1"


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[waitly_positions {ts}] {msg}", flush=True)


def _safe(label: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in label)[:80]


async def debug_dump(page: Page, label: str) -> None:
    if not DEBUG:
        return
    os.makedirs("state", exist_ok=True)
    safe = _safe(label)
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
    for name in ["Tillad alle", "Accepter", "Accept", "Allow all", "OK"]:
        try:
            btn = page.get_by_role("button", name=name)
            if await btn.count() > 0:
                await btn.first.click(timeout=3000)
                log(f"Cookie banner accepted via button: {name}")
                await page.wait_for_timeout(400)
                return
        except Exception:
            pass

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
                await page.wait_for_timeout(400)
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
                await page.wait_for_timeout(600)
                return
        except Exception:
            pass

    for name in ["Login", "Log ind", "Sign in"]:
        try:
            link = page.get_by_role("link", name=name)
            if await link.count() > 0:
                await link.first.click(timeout=5000)
                log(f"Clicked login link: {name}")
                await page.wait_for_timeout(600)
                return
        except Exception:
            pass


async def choose_private_account_if_prompted(page: Page) -> None:
    """
    User confirmed the button text is: "Gå til privatkonto".
    Best-effort; no failure if prompt not present.
    """
    candidates = [
        "Gå til privatkonto",
        "Privatkonto",
        "Lejer",
        "Jeg er lejer",
        "Som lejer",
        "Tenant",
        "I am a tenant",
    ]

    for name in candidates:
        try:
            btn = page.get_by_role("button", name=name)
            if await btn.count() > 0:
                await btn.first.click(timeout=5000)
                log(f"Chose account type via button: {name}")
                await page.wait_for_timeout(900)
                return
        except Exception:
            pass

    for name in candidates:
        try:
            link = page.get_by_role("link", name=name)
            if await link.count() > 0:
                await link.first.click(timeout=5000)
                log(f"Chose account type via link: {name}")
                await page.wait_for_timeout(900)
                return
        except Exception:
            pass

    for name in candidates:
        try:
            loc = page.locator(f"text={name}").first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click(timeout=5000)
                log(f"Chose account type via text click: {name}")
                await page.wait_for_timeout(900)
                return
        except Exception:
            pass

    log("Account-type prompt not detected (or could not click it) – continuing.")


async def find_visible_selector(page: Page, selectors: List[str]) -> Optional[str]:
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.first.is_visible():
                return sel
        except Exception:
            pass
    return None


async def try_navigate_to_waitlists(page: Page) -> None:
    """
    After login, try to click into the section where waitlists are shown.
    This often triggers the API calls we actually need.
    Best-effort.
    """
    candidates = ["Ventelister", "Mine ventelister", "Waitlists", "Queues", "Mine køer", "Mine lister"]

    for name in candidates:
        try:
            link = page.get_by_role("link", name=name)
            if await link.count() > 0:
                await link.first.click(timeout=5000)
                log(f"Navigated via link: {name}")
                await page.wait_for_timeout(1200)
                return
        except Exception:
            pass

        try:
            btn = page.get_by_role("button", name=name)
            if await btn.count() > 0:
                await btn.first.click(timeout=5000)
                log(f"Navigated via button: {name}")
                await page.wait_for_timeout(1200)
                return
        except Exception:
            pass

    log("Did not find a waitlists/queues navigation item (best-effort).")


def summarize_json_samples(samples: List[Dict[str, Any]]) -> None:
    log(f"JSON samples summary (showing up to 20): {len(samples)} captured")
    for s in samples[:20]:
        url = s.get("url", "")
        data = s.get("json")
        if isinstance(data, dict):
            keys = list(data.keys())[:15]
            log(f"  - {url} | dict keys: {keys}")
        elif isinstance(data, list):
            log(f"  - {url} | list len: {len(data)}")
        else:
            log(f"  - {url} | type: {type(data).__name__}")


def _walk_json(obj: Any, max_nodes: int = 20000):
    stack = [([], obj)]
    seen = 0
    while stack:
        path, cur = stack.pop()
        seen += 1
        if seen > max_nodes:
            return
        yield path, cur
        if isinstance(cur, dict):
            for k, v in cur.items():
                stack.append((path + [str(k)], v))
        elif isinstance(cur, list):
            for i, v in enumerate(cur):
                stack.append((path + [str(i)], v))


def guess_queues_from_json(data: Any) -> List[Dict]:
    """
    Loose heuristic: look for list[dict] where dicts include name/title-ish fields
    and numeric-ish fields (position/rank/total/etc).
    Returns normalized dicts, best-effort.
    """
    candidates: List[Dict] = []
    name_like = ("name", "title", "queue", "waitlist", "venteliste")
    num_like = ("position", "rank", "place", "number", "total", "count", "size", "members")

    for _, cur in _walk_json(data):
        if isinstance(cur, list) and cur and all(isinstance(x, dict) for x in cur[:5]):
            for item in cur[:150]:
                lowkeys = [str(k).lower() for k in item.keys()]
                has_name = any(any(n in k for n in name_like) for k in lowkeys)
                has_num = any(any(n in k for n in num_like) for k in lowkeys)
                if not (has_name and has_num):
                    continue

                def pick(preds):
                    for k in item.keys():
                        kl = str(k).lower()
                        if any(p in kl for p in preds):
                            return item.get(k)
                    return None

                name = pick(("name", "title", "queue", "waitlist", "venteliste"))
                pos = pick(("position", "rank", "place", "number"))
                tot = pick(("total", "count", "size", "members"))

                rec = {"name": str(name).strip() if name is not None else "UNKNOWN"}
                if pos is not None:
                    rec["position"] = pos
                if tot is not None:
                    rec["total"] = tot
                candidates.append(rec)

            if len(candidates) >= 3:
                break

    # de-dupe by name
    out = []
    seen = set()
    for c in candidates:
        n = c.get("name")
        if n and n not in seen:
            out.append(c)
            seen.add(n)
    return out


async def dump_dom_links(page: Page) -> None:
    links = await page.evaluate(
        """() => Array.from(document.querySelectorAll('a'))
          .map(a => ({ href: a.getAttribute('href') || '', text: (a.innerText||'').trim() }))
          .filter(x => x.href || x.text)"""
    )
    os.makedirs("state", exist_ok=True)
    with open("state/dom_links.json", "w", encoding="utf-8") as f:
        json.dump(links[:3000], f, ensure_ascii=False, indent=2)
    log(f"DOM links dumped: state/dom_links.json (count={len(links)})")


async def capture_nuxt_public_config(page: Page) -> None:
    """
    If the app is Nuxt, window.__NUXT__.config.public often holds useful endpoints.
    We'll dump it for debugging.
    """
    try:
        nuxt_public = await page.evaluate("() => window.__NUXT__?.config?.public || null")
    except Exception:
        nuxt_public = None

    os.makedirs("state", exist_ok=True)
    with open("state/nuxt_public_config.json", "w", encoding="utf-8") as f:
        json.dump(nuxt_public, f, ensure_ascii=False, indent=2)

    if isinstance(nuxt_public, dict):
        subset = {k: nuxt_public.get(k) for k in ["baseApiUrl", "baseUrl", "consumerSiteUrl", "businessSiteUrl"] if k in nuxt_public}
        log("NUXT public config (subset): " + json.dumps(subset, ensure_ascii=False))
    else:
        log("NUXT public config not found (window.__NUXT__ missing?)")


async def fetch_positions(email: str, password: str) -> List[Dict]:
    if not email or not password:
        raise RuntimeError("WAITLY_LOGIN_EMAIL / WAITLY_LOGIN_PASSWORD not set")

    log("Starting Playwright")
    api_json_samples: List[Dict[str, Any]] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(15000)

        async def on_response(resp):
            try:
                ct = (resp.headers.get("content-type") or "").lower()
                if "application/json" not in ct:
                    return
                data = await resp.json()
                dumped = json.dumps(data, ensure_ascii=False)
                if len(dumped) > 500_000:
                    return
                api_json_samples.append({"url": resp.url, "json": data})
            except Exception:
                return

        page.on("response", on_response)

        # Landing
        log("Opening https://my.waitly.dk/")
        await page.goto("https://my.waitly.dk/", wait_until="domcontentloaded")
        await debug_dump(page, "01_landing")

        await accept_cookies_if_present(page)

        # Click login
        await click_login(page)
        await accept_cookies_if_present(page)
        await debug_dump(page, "02_after_login_click")

        # Choose private account if prompted
        await choose_private_account_if_prompted(page)
        await accept_cookies_if_present(page)
        await debug_dump(page, "03_after_private_account_choice")

        # Find login form fields
        email_sel = await find_visible_selector(
            page,
            [
                "input[name='email']",
                "input[type='email']",
                "input[id*='email' i]",
                "input[placeholder*='mail' i]",
                "input[autocomplete='email']",
            ],
        )
        pwd_sel = await find_visible_selector(
            page,
            [
                "input[type='password']",
                "input[name='password']",
                "input[id*='password' i]",
                "input[autocomplete='current-password']",
            ],
        )

        if not email_sel or not pwd_sel:
            await debug_dump(page, "04_login_form_not_found")
            await browser.close()
            raise RuntimeError(
                "Could not find login form fields after choosing private account. "
                "Inspect state/debug_04_login_form_not_found.html/png"
            )

        # Fill & submit
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
            await debug_dump(page, "05_submit_not_found")
            await browser.close()
            raise RuntimeError("Could not find submit/login button")

        # Wait for app to settle
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        await page.wait_for_timeout(1200)

        await debug_dump(page, "06_after_submit")
        log(f"URL after submit: {page.url}")

        if "/login" in page.url:
            await debug_dump(page, "07_login_failed")
            await browser.close()
            raise RuntimeError("Login failed (still on /login). Inspect state/debug_07_login_failed.html/png")

        log("Login succeeded")

        # Capture Nuxt config (very useful for endpoints)
        await capture_nuxt_public_config(page)

        # Attempt to navigate to waitlists section (if not already visible)
        await try_navigate_to_waitlists(page)
        await debug_dump(page, "08_after_navigation_attempt")

        # Let SPA fetch data
        await page.wait_for_timeout(2000)
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass

        await debug_dump(page, "09_dashboard_loaded")

        # Save JSON samples + summary
        os.makedirs("state", exist_ok=True)
        with open("state/api_json_samples.json", "w", encoding="utf-8") as f:
            json.dump(api_json_samples[:60], f, ensure_ascii=False, indent=2)

        log(f"Captured JSON responses: {len(api_json_samples)} (saved state/api_json_samples.json)")
        summarize_json_samples(api_json_samples)

        # Try to infer queues from JSON
        for s in api_json_samples:
            guessed = guess_queues_from_json(s["json"])
            if guessed:
                log(f"Inferred {len(guessed)} queue-like records from: {s['url']}")
                await browser.close()
                return guessed

        # Fallback: dump DOM links to help refine selectors
        await dump_dom_links(page)
        await browser.close()
        return []
