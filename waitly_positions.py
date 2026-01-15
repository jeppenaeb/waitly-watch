# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional, Dict

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def _iso_date_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()

def _slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")

def _walk_json(obj: Any) -> Iterable[Any]:
    stack = [obj]
    while stack:
        cur = stack.pop()
        yield cur
        if isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)

def _extract_queue_objects(obj: Any) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for node in _walk_json(obj):
        if not isinstance(node, dict):
            continue

        keys = set(node.keys())
        has_pos = any(k in keys for k in ("position", "spot", "rank", "place"))
        has_total = any(k in keys for k in ("total", "size", "capacity", "spots", "waitlistSize"))
        has_name = any(k in keys for k in ("name", "title", "waitlistName", "listName"))
        if has_pos and has_total and has_name:
            candidates.append(node)
    return candidates

def _best_int(d: Dict[str, Any], keys: List[str]) -> Optional[int]:
    for k in keys:
        v = d.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
    return None

def _best_str(d: Dict[str, Any], keys: List[str]) -> Optional[str]:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None

def fetch_positions_via_login(email: str, password: str, headless: bool = True) -> Dict[str, Any]:
    json_payloads: List[Any] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        def on_response(resp):
            try:
                ct = (resp.headers.get("content-type") or "").lower()
                if "application/json" in ct:
                    json_payloads.append(resp.json())
            except Exception:
                pass

        page.on("response", on_response)

        page.goto("https://my.waitly.dk/login", wait_until="domcontentloaded")

        page.get_by_label("Email address").fill(email)
        page.get_by_label("Password").fill(password)
        page.get_by_role("button", name=re.compile(r"login", re.I)).click()

        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except PlaywrightTimeoutError:
            pass

        page.wait_for_timeout(3_000)

        for url in ("https://my.waitly.dk/", "https://my.waitly.dk/profile", "https://my.waitly.dk/offers"):
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(2_000)
            except Exception:
                pass

        browser.close()

    queues: List[Dict[str, Any]] = []
    seen = set()

    for payload in json_payloads:
        for obj in _extract_queue_objects(payload):
            name = _best_str(obj, ["name", "title", "waitlistName", "listName"]) or "Unknown list"
            position = _best_int(obj, ["position", "spot", "rank", "place"])
            total = _best_int(obj, ["total", "size", "capacity", "spots", "waitlistSize"])
            if position is None or total is None:
                continue

            qid = _slugify(name)
            key = (qid, int(position), int(total))
            if key in seen:
                continue
            seen.add(key)

            queues.append({"id": qid, "name": name, "position": int(position), "total": int(total)})

    queues.sort(key=lambda x: x["name"].lower())

    return {"updated_at": _iso_date_utc(), "queues": queues}

def write_current_json(data: Dict[str, Any], out_path: str) -> None:
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
