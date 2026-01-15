import os
import json
import csv
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any

from playwright.async_api import async_playwright, Page


DEBUG = os.getenv("WAITLY_DEBUG", "1") == "1"

HISTORY_PATH = "state/history.json"
HISTORY_CSV_PATH = "state/history_flat.csv"

START_POSITIONS_PATH = "state/start_positions.json"
START_POSITIONS_OVERRIDE_PATH = "state/start_positions_override.json"


# -----------------------------
# Utilities
# -----------------------------
def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[waitly_positions {ts}] {msg}", flush=True)


def _safe(label: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in label)[:80]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


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


# -----------------------------
# Cookie & login flow helpers
# -----------------------------
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
    UI sometimes asks account type; user's exact button text: "Gå til privatkonto"
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


# -----------------------------
# Start positions (with OVERRIDE)
# -----------------------------
def _load_start_positions_file(path: str) -> Dict[str, int]:
    """
    Reads {"start_positions": {"1853": 431, ...}}
    Returns dict(list_id_str -> int)
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if isinstance(obj, dict) and isinstance(obj.get("start_positions"), dict):
            sp = obj["start_positions"]
            out: Dict[str, int] = {}
            for k, v in sp.items():
                try:
                    out[str(k)] = int(v)
                except Exception:
                    pass
            return out
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return {}


def load_start_positions_with_override() -> Dict[str, int]:
    """
    Priority:
      1) state/start_positions_override.json  (your canonical truth)
      2) state/start_positions.json           (fallback/template)
    """
    override = _load_start_positions_file(START_POSITIONS_OVERRIDE_PATH)
    if override:
        log(f"Using start positions OVERRIDE: {START_POSITIONS_OVERRIDE_PATH}")
        return override

    normal = _load_start_positions_file(START_POSITIONS_PATH)
    if normal:
        log(f"Using start positions: {START_POSITIONS_PATH}")
    else:
        log("No start positions file found (override or normal).")
    return normal


def write_start_positions_template_if_missing(path: str, queues: List[Dict]) -> None:
    """
    Creates a template file ONLY if missing.
    (We never overwrite user files.)
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.exists(path):
        return

    start_positions = {}
    for q in queues:
        list_id = q.get("id")
        pos = q.get("position")
        if list_id is None or pos is None:
            continue
        try:
            start_positions[str(list_id)] = int(pos)
        except Exception:
            pass

    obj = {
        "generated_at": _now_iso(),
        "note": "Template. Prefer using start_positions_override.json for the real baseline.",
        "start_positions": start_positions,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    log(f"Start positions template created: {path}")


def compute_progress_from_start(current_position: Optional[int], start_position: Optional[int]) -> Dict[str, Any]:
    if current_position is None or start_position is None:
        return {}
    try:
        cur = int(current_position)
        start = int(start_position)
        if start <= 0:
            return {}
        moved = start - cur
        moved_pct = (moved / start) * 100.0
        return {
            "start_position": start,
            "moved": moved,
            "moved_pct": round(moved_pct, 2),
        }
    except Exception:
        return {}


# -----------------------------
# Subscriptions parsing
# -----------------------------
def find_subscriptions_json(api_json_samples: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for s in api_json_samples:
        url = s.get("url", "") or ""
        if "/api/v2/consumer/users/" in url and url.endswith("/subscriptions"):
            j = s.get("json")
            if isinstance(j, dict) and isinstance(j.get("data"), list):
                return j
    return None


def _normalized_name(company_name: Optional[str], list_name: Optional[str], full_name: Optional[str]) -> str:
    if list_name and company_name:
        if company_name.strip().lower() in list_name.strip().lower():
            return list_name.strip()
        return f"{company_name.strip()} - {list_name.strip()}"

    if full_name:
        return str(full_name).strip()
    if list_name:
        return str(list_name).strip()
    if company_name:
        return str(company_name).strip()
    return "Unknown waitlist"


def parse_subscriptions_payload(payload: Any, start_positions: Dict[str, int]) -> List[Dict]:
    """
    Deterministic parser for subscriptions endpoint.
    Uses total = list.subscribers (matches UI "X of Y").
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
        active = item.get("active")
        completed = item.get("completed")
        approved = item.get("approved")

        list_obj = item.get("list") or {}
        if not isinstance(list_obj, dict):
            list_obj = {}

        list_id = list_obj.get("id")
        list_name = list_obj.get("name")
        full_name = list_obj.get("full_name")
        total = list_obj.get("subscribers")

        company_name = None
        company = list_obj.get("company")
        if isinstance(company, dict):
            company_name = company.get("name")

        name = _normalized_name(company_name, list_name, full_name)

        rec: Dict[str, Any] = {
            "id": str(list_id) if list_id is not None else str(item.get("id", "")),
            "name": name,
        }

        if placement is not None:
            try:
                rec["position"] = int(placement)
            except Exception:
                rec["position"] = placement

        if total is not None:
            try:
                rec["total"] = int(total)
            except Exception:
                rec["total"] = total

        if active is not None:
            rec["active"] = bool(active)
        if completed is not None:
            rec["completed"] = bool(completed)
        if approved is not None:
            rec["approved"] = bool(approved)

        sp = start_positions.get(rec["id"])
        rec.update(compute_progress_from_start(rec.get("position"), sp))

        out.append(rec)

    # de-dupe by id
    dedup = []
    seen = set()
    for r in out:
        key = r.get("id") or r.get("name")
        if key and key not in seen:
            dedup.append(r)
            seen.add(key)
    return dedup


# -----------------------------
# History + "this week/month/year" progress
# -----------------------------
def _load_history(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if isinstance(obj, dict) and isinstance(obj.get("lists"), dict):
            return obj
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return {"version": 1, "lists": {}}


def _save_history(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _append_history_snapshot(history: Dict[str, Any], ts: str, queues: List[Dict]) -> None:
    lists = history.setdefault("lists", {})
    if not isinstance(lists, dict):
        history["lists"] = {}
        lists = history["lists"]

    for q in queues:
        list_id = q.get("id")
        pos = q.get("position")
        tot = q.get("total")
        if not list_id or pos is None or tot is None:
            continue

        series = lists.get(str(list_id))
        if not isinstance(series, list):
            series = []
            lists[str(list_id)] = series

        # Avoid duplicates if the same timestamp is written twice
        if series and isinstance(series[-1], dict) and series[-1].get("ts") == ts:
            continue

        series.append({"ts": ts, "position": int(pos), "total": int(tot)})


def _nearest_at_or_before(series: List[Dict[str, Any]], target: datetime) -> Optional[Dict[str, Any]]:
    best = None
    best_t = None

    for p in series:
        ts = p.get("ts")
        if not isinstance(ts, str):
            continue
        dt = _parse_iso(ts)
        if not dt:
            continue
        if dt <= target:
            if best_t is None or dt > best_t:
                best = p
                best_t = dt

    return best


def compute_window_progress(history: Dict[str, Any], list_id: str, now_ts: str) -> Dict[str, Any]:
    lists = history.get("lists")
    if not isinstance(lists, dict):
        return {}

    series = lists.get(str(list_id))
    if not isinstance(series, list) or len(series) < 2:
        return {}

    now_dt = _parse_iso(now_ts)
    if not now_dt:
        return {}

    latest = series[-1] if isinstance(series[-1], dict) else None
    if not latest:
        return {}

    try:
        now_pos = int(latest.get("position"))
    except Exception:
        return {}

    def delta_for(days: int) -> Optional[int]:
        target = now_dt - timedelta(days=days)
        past = _nearest_at_or_before(series, target)
        if not past:
            return None
        try:
            past_pos = int(past.get("position"))
        except Exception:
            return None
        return past_pos - now_pos  # positive = moved forward

    week = delta_for(7)
    month = delta_for(30)
    year = delta_for(365)

    out = {}
    if week is not None:
        out["week"] = week
    if month is not None:
        out["month"] = month
    if year is not None:
        out["year"] = year

    return out


def export_history_csv(history: Dict[str, Any], queues_by_id: Dict[str, str], path: str) -> None:
    lists = history.get("lists")
    if not isinstance(lists, dict):
        return

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "list_id", "name", "position", "total"])
        for list_id, series in lists.items():
            if not isinstance(series, list):
                continue
            name = queues_by_id.get(str(list_id), "")
            for p in series:
                if not isinstance(p, dict):
                    continue
                ts = p.get("ts")
                pos = p.get("position")
                tot = p.get("total")
                if isinstance(ts, str) and pos is not None and tot is not None:
                    w.writerow([ts, list_id, name, pos, tot])


# -----------------------------
# Main entry used by waitly_watch_all.py
# -----------------------------
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
            raise RuntimeError("Could not find login form fields. Inspect state/debug_04_login_form_not_found.html/png")

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

        # Optional debug
        await capture_nuxt_public_config(page)

        # extra time for API calls to settle
        await page.wait_for_timeout(2000)
        try:
            await page.wait_for_load_state("networkidle", timeout=25000)
        except Exception:
            pass
        await debug_dump(page, "08_dashboard_loaded")

        # Save captured JSON samples for debugging
        os.makedirs("state", exist_ok=True)
        with open("state/api_json_samples.json", "w", encoding="utf-8") as f:
            json.dump(api_json_samples[:80], f, ensure_ascii=False, indent=2)
        log(f"Captured JSON responses: {len(api_json_samples)} (saved state/api_json_samples.json)")
        summarize_json_samples(api_json_samples)

        subs_json = find_subscriptions_json(api_json_samples)
        if not subs_json:
            await debug_dump(page, "09_subscriptions_not_found")
            await browser.close()
            raise RuntimeError(
                "Did not capture subscriptions endpoint in this run. "
                "See state/api_json_samples.json and state/debug_09_subscriptions_not_found.html/png"
            )

        # Start positions: OVERRIDE wins
        start_positions = load_start_positions_with_override()

        # Parse queues
        queues = parse_subscriptions_payload(subs_json, start_positions)

        # If template missing, create (harmless). But override is preferred.
        write_start_positions_template_if_missing(START_POSITIONS_PATH, queues)

        # -----------------------------
        # History + window progress
        # -----------------------------
        ts_now = _now_iso()
        history = _load_history(HISTORY_PATH)
        _append_history_snapshot(history, ts_now, queues)
        _save_history(HISTORY_PATH, history)
        log(f"History updated: {HISTORY_PATH}")

        for q in queues:
            lid = str(q.get("id", ""))
            if not lid:
                continue
            win = compute_window_progress(history, lid, ts_now)
            if win:
                q["progress"] = win

        queues_by_id = {str(q.get("id")): str(q.get("name", "")) for q in queues if q.get("id") is not None}
        export_history_csv(history, queues_by_id, HISTORY_CSV_PATH)
        log(f"History CSV written: {HISTORY_CSV_PATH}")

        log(f"Parsed subscriptions -> {len(queues)} queues")
        if DEBUG:
            log("Queues sample: " + json.dumps(queues[:3], ensure_ascii=False))

        await browser.close()
        return queues
