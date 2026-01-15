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
    Your exact button text: "Gå til privatkonto"
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


async def capture_nuxt_public_config(page: Page) -> None:
    """
    Best-effort: may be null/empty on my.waitly.dk. Still useful when present.
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


def parse_subscriptions_payload(payload: Any) -> List[Dict]:
    """
    Input: JSON from https://app.waitly.dk/api/v2/consumer/users/<id>/subscriptions
    Output: list of {id, name, position, total}

    Mapping:
      - position = item["placement"]
      - total:
          prefer list.template.lists match on list.id -> active_subscribers if present
          else list.subscribers
      - name:
          prefer list.company.name + " - " + list.name
          fallback list.full_name
    """
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []

    out: List[Dict] = []

    for item in data:
        if not isinstance(item, dict):
            continue

        placement = item.get("placement", None)
        list_obj = item.get("list") or {}
        if not isinstance(list_obj, dict):
            list_obj = {}

        list_id = list_obj.get("id")
        list_name = list_obj.get("name")
        full_name = list_obj.get("full_name")

        company_name = None
        company = list_obj.get("company")
        if isinstance(company, dict):
            company_name = company.get("name")

        # total calculation
        total = None

        # Try to find matching list stats inside template.lists
        template = list_obj.get("template")
        if isinstance(template, dict):
            lists = template.get("lists")
            if isinstance(lists, list) and list_id is not None:
                for l in lists:
                    if isinstance(l, dict) and l.get("id") == list_id:
                        # Prefer active_subscribers (often equals "people ahead" universe)
                        if "active_subscribers" in l and l.get("active_subscribers") is not None:
                            total = l.get("active_subscribers")
                        elif "subscribers" in l and l.get("subscribers") is not None:
                            total = l.get("subscribers")
                        break

        # Fallback: list.subscribers
        if total is None:
            total = list_obj.get("subscribers")

        # name
        name = None
        if company_name and list_name:
            name = f"{company_name} - {list_name}"
        elif full_name:
            name = full_name
        elif list_name:
            name = list_name
        else:
            name = "Unknown waitlist"

        rec = {
            "id": str(list_id) if list_id is not None else str(item.get("id", "")),
            "name": name,
        }
        if placement is not None:
            rec["position"] = placement
        if total is not None:
            rec["total"] = total

        out.append(rec)

    # Remove obvious duplicates by id
    dedup = []
    seen = set()
    for r in out:
        key = r.get("id") or r.get("name")
        if key and key not in seen:
            dedup.append(r)
            seen.add(key)

    return dedup


def find_subscriptions_json(api_json_samples: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Find the subscriptions payload among captured JSON responses.
    """
    for s in api_json_samples:
        url = s.get("url", "")
        if "/api/v2/consumer/users/" in url and url.endswith("/subscriptions"):
            j = s.get("json")
            if isinstance(j, dict) and "data" in j:
                return j
    return None


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
                if len(dumped) > 600_000:
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

        # Login flow
        await click_login(page)
        await accept_cookies_if_present(page)
        await debug_dump(page, "02_after_login_click")

        await choose_private_account_if_prompted(page)
        await accept_cookies_if_present(page)
        await debug_dump(page, "03_after_private_account_choice")

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
                "Could not find login form fields. Inspect state/debug_04_login_form_not_found.html/png"
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

        # Let SPA load
        try:
            await page.wait_for_load_state("networkidle", timeout=25000)
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

        # Optional: Nuxt config snapshot
        await capture_nuxt_public_config(page)

        # Give a bit more time for API calls
        await page.wait_for_timeout(2000)
        try:
            await page.wait_for_load_state("networkidle", timeout=25000)
        except Exception:
            pass

        await debug_dump(page, "08_dashboard_loaded")

        # Save captured JSON responses for debugging
        os.makedirs("state", exist_ok=True)
        with open("state/api_json_samples.json", "w", encoding="utf-8") as f:
            json.dump(api_json_samples[:80], f, ensure_ascii=False, indent=2)

        log(f"Captured JSON responses: {len(api_json_samples)} (saved state/api_json_samples.json)")
        summarize_json_samples(api_json_samples)

        # --- NEW: parse subscriptions deterministically ---
        subs_json = find_subscriptions_json(api_json_samples)
        if subs_json:
            queues = parse_subscriptions_payload(subs_json)
            log(f"Parsed subscriptions -> {len(queues)} queues")
            if DEBUG:
                log("Queues sample: " + json.dumps(queues[:3], ensure_ascii=False))
            await browser.close()
            return queues

        # If we didn't see subscriptions in captured responses, fail loudly with debug
        await debug_dump(page, "09_subscriptions_not_found")
        await browser.close()
        raise RuntimeError(
            "Did not capture subscriptions endpoint in this run. "
            "See state/api_json_samples.json and state/debug_09_subscriptions_not_found.html/png"
        )
