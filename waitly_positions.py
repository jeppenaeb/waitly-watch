import os
import re
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


async def dump_text(page: Page, label: str) -> str:
    os.makedirs("state", exist_ok=True)
    safe = _safe(label)
    path = f"state/{safe}.txt"
    try:
        text = await page.locator("body").inner_text()
    except Exception:
        text = ""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        log(f"Body text dumped: {path} (chars={len(text)})")
    except Exception as e:
        log(f"Body text dump failed: {e}")
    return text


async def accept_cookies_if_present(page: Page) -> None:
    # best-effort cookie accept
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


def _walk_json(obj: Any, max_nodes: int = 20000):
    """
    Generator that yields (path, value) for dict/list JSON, bounded.
    """
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


def _guess_queues_from_json(data: Any) -> List[Dict]:
    """
    Try to infer queue records from arbitrary JSON responses.
    We look for lists of dicts that resemble queue items.
    """
    candidates: List[Dict] = []

    # Heuristics: an item is "queue-like" if it contains at least a name + position/number fields
    name_keys = {"name", "title", "queueName", "waitlistName"}
    pos_keys = {"position", "rank", "place", "number"}
    total_keys = {"total", "size", "members", "count", "totalMembers"}

    for path, cur in _walk_json(data):
        if isinstance(cur, list) and cur and all(isinstance(x, dict) for x in cur[:5]):
            # examine first few dicts
            for item in cur[:50]:
                keys = set(item.keys())
                has_name = any(k in keys for k in name_keys) or any("name" in k.lower() for k in keys)
                has_pos = any(k in keys for k in pos_keys) or any(k.lower() in pos_keys for k in keys)
                has_total = any(k in keys for k in total_keys) or any(k.lower() in total_keys for k in keys)
                # Often you might only have name+position or name+rank
                if has_name and (has_pos or has_total):
                    # normalize
                    name = None
                    for k in item.keys():
                        if k in ("name", "title"):
                            name = item.get(k)
                            break
                    if not name:
                        # fallback
                        for k in item.keys():
                            if "name" in k.lower() or "title" in k.lower():
                                name = item.get(k)
                                break

                    def pick(keys_set):
                        for k in item.keys():
                            if k in keys_set or k.lower() in keys_set:
                                return item.get(k)
                        return None

                    position = pick(pos_keys)
                    total = pick(total_keys)

                    rec = {
                        "name": str(name).strip() if name is not None else "UNKNOWN",
                    }
                    if position is not None:
                        rec["position"] = position
                    if total is not None:
                        rec["total"] = total
                    candidates.append(rec)

            if len(candidates) >= 5:
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


async def scrape_queues_from_dom(page: Page) -> List[Dict]:
    """
    Generic DOM scraping: collect links + text blocks and try to infer queues.
    This is a fallback when API parsing yields nothing.
    """
    # Collect links (href + text)
    links = await page.evaluate(
        """() => Array.from(document.querySelectorAll('a'))
          .map(a => ({ href: a.getAttribute('href') || '', text: (a.innerText||'').trim() }))
          .filter(x => x.href || x.text)"""
    )

    os.makedirs("state", exist_ok=True)
    with open("state/dom_links.json", "w", encoding="utf-8") as f:
        json.dump(links[:2000], f, ensure_ascii=False, indent=2)
    log(f"DOM links dumped: state/dom_links.json (count={len(links)})")

    # Heuristic: queue links often contain "queue", "venteliste", "waitlist"
    queue_like = []
    for l in links:
        href = (l.get("href") or "").lower()
        txt = (l.get("text") or "").lower()
        if any(k in href for k in ["queue", "queues", "waitlist", "venteliste"]) or any(
            k in txt for k in ["venteliste", "waitlist", "kø", "queue"]
        ):
            queue_like.append(l)

    with open("state/dom_queue_like_links.json", "w", encoding="utf-8") as f:
        json.dump(queue_like[:500], f, ensure_ascii=False, indent=2)
    log(f"Queue-like links dumped: state/dom_queue_like_links.json (count={len(queue_like)})")

    # Convert queue_like to queue records if possible
    out = []
    for l in queue_like:
        name = (l.get("text") or "").strip()
        href = l.get("href")
        if name:
            out.append({"name": name, "url": href})
    # de-dupe
    dedup = []
    seen = set()
    for r in out:
        key = (r.get("name") or "") + "|" + (r.get("url") or "")
        if key not in seen:
            dedup.append(r)
            seen.add(key)
    return dedup


async def fetch_positions(email: str, password: str) -> List[Dict]:
    if not email or not password:
        raise RuntimeError("WAITLY_LOGIN_EMAIL / WAITLY_LOGIN_PASSWORD not set")

    log("Starting Playwright")
    queues: List[Dict] = []
    api_json_samples: List[Dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(15000)

        # Capture JSON responses after login to infer data
        async def on_response(resp):
            try:
                ct = (resp.headers.get("content-type") or "").lower()
                url = resp.url
                if "application/json" in ct:
                    # Only keep a limited number (avoid huge logs)
                    data = await resp.json()
                    # Keep small-ish sample; skip enormous payloads
                    dumped = json.dumps(data, ensure_ascii=False)
                    if len(dumped) > 300_000:
                        return
                    api_json_samples.append({"url": url, "json": data})
            except Exception:
                return

        page.on("response", on_response)

        log("Opening https://my.waitly.dk/")
        await page.goto("https://my.waitly.dk/", wait_until="domcontentloaded")
        await debug_dump(page, "01_landing")

        await accept_cookies_if_present(page)
        await click_login(page)
        await accept_cookies_if_present(page)
        await debug_dump(page, "02_after_login_click")

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
            await debug_dump(page, "03_form_not_found")
            await dump_text(page, "login_form_not_found_body")
            raise RuntimeError(
                "Could not find login form fields. Inspect state/debug_03_form_not_found.html/png"
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
            await debug_dump(page, "04_submit_not_found")
            await dump_text(page, "submit_not_found_body")
            raise RuntimeError("Could not find submit/login button")

        # Wait for navigation / API calls
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass

        await page.wait_for_timeout(1500)
        await debug_dump(page, "05_after_submit")
        log(f"URL after submit: {page.url}")

        if "/login" in page.url:
            await debug_dump(page, "06_login_failed")
            await dump_text(page, "06_login_failed_body")
            raise RuntimeError(
                "Login failed (still on /login). See state/debug_06_login_failed.html/png and state/06_login_failed_body.txt"
            )

        log("Login succeeded (URL changed)")

        # Give the app a moment to load & fetch data
        await page.wait_for_timeout(2500)
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass

        await debug_dump(page, "07_after_login_loaded")

        # Save captured API JSON samples
        os.makedirs("state", exist_ok=True)
        with open("state/api_json_samples.json", "w", encoding="utf-8") as f:
            json.dump(
                [{"url": s["url"], "json": s["json"]} for s in api_json_samples[:30]],
                f,
                ensure_ascii=False,
                indent=2,
            )
        log(f"Captured JSON responses: {len(api_json_samples)} (saved state/api_json_samples.json)")

        # 1) Try infer queues from API JSON
        inferred: List[Dict] = []
        for s in api_json_samples:
            try:
                guessed = _guess_queues_from_json(s["json"])
                if guessed:
                    log(f"Inferred {len(guessed)} queue-like records from API response: {s['url']}")
                    inferred = guessed
                    break
            except Exception:
                continue

        if inferred:
            queues = inferred
            # ensure fields are sane
            for q in queues:
                if "name" in q:
                    q["name"] = str(q["name"]).strip()
            await browser.close()
            return queues

        # 2) Fallback: DOM scraping
        log("API inference yielded 0 queues. Falling back to DOM scraping.")
        await debug_dump(page, "08_before_dom_scrape")
        dom_queues = await scrape_queues_from_dom(page)

        log(f"DOM scrape complete. Queues found: {len(dom_queues)}")
        if DEBUG:
            log("DOM queues sample: " + json.dumps(dom_queues[:3], ensure_ascii=False))

        await browser.close()
        return dom_queues
