#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Waitly Watch (sitemap + openings)

Funktioner:
1) NYE SIDER:
   - Henter https://waitly.eu/da/sitemap (HTML)
   - Finder nye links der matcher områder (Kbh K/V/N/Ø/S + Frederiksberg (+ -c))
   - Notifier via mail med 🆕 [Waitly] NY SIDE

2) ÅBNINGER:
   - Læser watch_urls.txt (én Waitly-URL pr linje)
   - Checker om siden er åben ved at finde et "Tilmeld"-link til app.waitly.*
   - Notifier KUN ved transition lukket -> åben (ingen spam på første run)
   - Notifier via mail med 🚨 [Waitly] ÅBNING

State:
- Gemmer baseline + sidestatus i waitly_watch_state.json

Afhængigheder:
  pip install requests beautifulsoup4

SMTP (via env vars):
  WAITLY_SMTP_HOST=smtp.gmail.com
  WAITLY_SMTP_PORT=587
  WAITLY_SMTP_USER=din_gmail@gmail.com
  WAITLY_SMTP_PASS=din_app_password
  WAITLY_MAIL_FROM=din_gmail@gmail.com  (optional; ellers SMTP_USER)

Kør:
  python waitly_watch_all.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
import smtplib
from email.message import EmailMessage


# ----------------------------
# Config
# ----------------------------

SITEMAP_URL = "https://waitly.eu/da/sitemap"

WATCHLIST_FILE = Path("watch_urls.txt")            # known pages to watch for opening
STATE_FILE = Path("waitly_watch_state.json")       # persisted state for sitemap+pages

NOTIFY_TO = "jeppe.kurland@gmail.com"

REQUEST_TIMEOUT = 25
SLEEP_BETWEEN_PAGE_FETCHES_SEC = 1.2

# SMTP via env vars
ENV_SMTP_HOST = "WAITLY_SMTP_HOST"
ENV_SMTP_PORT = "WAITLY_SMTP_PORT"
ENV_SMTP_USER = "WAITLY_SMTP_USER"
ENV_SMTP_PASS = "WAITLY_SMTP_PASS"
ENV_MAIL_FROM = "WAITLY_MAIL_FROM"

# Area matching for NEW sitemap URLs
AREA_TOKENS = (
    # København (inkl. alternative stavninger)
    "koebenhavn-k", "kobenhavn-k",
    "koebenhavn-v", "kobenhavn-v",
    "koebenhavn-o", "kobenhavn-o",   # Østerbro (ø→o)
    "koebenhavn-n", "kobenhavn-n",
    "koebenhavn-s", "kobenhavn-s",

    # Frederiksberg
    "frederiksberg",
    "frederiksberg-c",
)

# Accept:
#   /<token>/...
#   /####-<token>/...
# where #### is exactly 4 digits
AREA_SEGMENT_RE = re.compile(
    r"/(?:\d{4}-)?(" + "|".join(re.escape(t) for t in AREA_TOKENS) + r")(/|$)",
    re.IGNORECASE
)


# ----------------------------
# Helpers
# ----------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    p = urlparse(url)
    if not p.scheme or not p.netloc:
        return ""
    # drop fragment
    p2 = p._replace(fragment="")
    return urlunparse(p2)


def fetch_text(url: str) -> str:
    r = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "waitly-watch/3.0"})
    r.raise_for_status()
    return r.text


def smtp_config_present() -> bool:
    return all(os.getenv(k) for k in (ENV_SMTP_HOST, ENV_SMTP_PORT, ENV_SMTP_USER, ENV_SMTP_PASS))


def send_email(subject: str, body: str, to_addr: str) -> None:
    if not smtp_config_present():
        print("SMTP env vars mangler -> sender ikke email. (Detection kører stadig.)")
        return

    host = os.getenv(ENV_SMTP_HOST)
    port = int(os.getenv(ENV_SMTP_PORT, "587"))
    user = os.getenv(ENV_SMTP_USER)
    password = os.getenv(ENV_SMTP_PASS)
    mail_from = os.getenv(ENV_MAIL_FROM) or user

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = to_addr
    msg.set_content(body)

    with smtplib.SMTP(host, port) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg)


# ----------------------------
# State
# ----------------------------

def load_state(path: Path) -> dict:
    if not path.exists():
        return {
            "last_run": None,
            "sitemap": {"count": 0, "urls": []},
            "pages": {},  # url -> {"open": bool, "last_seen": iso, "reason_text": str, "reason_href": str}
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("last_run", None)
            data.setdefault("sitemap", {"count": 0, "urls": []})
            data.setdefault("pages", {})
            if not isinstance(data["pages"], dict):
                data["pages"] = {}
            return data
    except Exception:
        pass
    return {
        "last_run": None,
        "sitemap": {"count": 0, "urls": []},
        "pages": {},
    }


def save_state(path: Path, state: dict) -> None:
    state["last_run"] = now_iso()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------------------------
# Part 1: Discover NEW relevant URLs from sitemap
# ----------------------------

def extract_links_from_html(html: str, base_url: str) -> Set[str]:
    soup = BeautifulSoup(html, "html.parser")
    out: Set[str] = set()
    for a in soup.find_all("a", href=True):
        abs_url = urljoin(base_url, a.get("href", ""))
        norm = normalize_url(abs_url)
        if norm:
            out.add(norm)
    return out


def matches_area_rule(url: str) -> bool:
    p = urlparse(url)
    if p.netloc.lower() != "waitly.eu":
        return False
    return bool(AREA_SEGMENT_RE.search(p.path))


def sitemap_check(state: dict) -> List[str]:
    """
    Returns list of NEW relevant URLs compared to stored baseline.
    Updates sitemap baseline in state if changed.
    """
    prev_urls = set(normalize_url(u) for u in state["sitemap"].get("urls", []) if isinstance(u, str))
    prev_count = int(state["sitemap"].get("count", 0) or 0)

    html = fetch_text(SITEMAP_URL)
    all_links = extract_links_from_html(html, SITEMAP_URL)

    relevant = {u for u in all_links if matches_area_rule(u)}
    current_count = len(relevant)

    # init baseline if empty (no email)
    if prev_count == 0 and not prev_urls:
        state["sitemap"] = {"count": current_count, "urls": sorted(relevant)}
        return []

    if current_count == prev_count:
        return []

    new_urls = sorted(relevant - prev_urls)
    state["sitemap"] = {"count": current_count, "urls": sorted(relevant)}
    return new_urls


# ----------------------------
# Part 2: Watch known pages for OPEN transitions (Tilmeld-link)
# ----------------------------

def load_watch_urls(path: Path) -> List[str]:
    if not path.exists():
        return []
    urls: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        u = normalize_url(line)
        if u:
            urls.append(u)
    # de-dup preserve order
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def detect_open_signal(html: str) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Returns: (is_open, reason_text, reason_href)

    Strong signal:
      <a href="...app.waitly.dk|app.waitly.eu..."> ... Tilmeld ... </a>
    Fallback:
      page text contains "tilmeld dig listen"
    """
    soup = BeautifulSoup(html, "html.parser")

    # Strong signal: "tilmeld" + app.waitly.*
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        text = (a.get_text(" ", strip=True) or "").strip()
        if not href or not text:
            continue
        href_l = href.lower()
        text_l = text.lower()
        if "tilmeld" in text_l and ("app.waitly.dk" in href_l or "app.waitly.eu" in href_l):
            return True, text, href

    # Fallback: plain text
    page_text = soup.get_text(" ", strip=True).lower()
    if "tilmeld dig listen" in page_text:
        return True, "Tilmeld dig listen (fallback tekstmatch)", None

    return False, None, None


def watch_pages_for_openings(state: dict, urls: List[str]) -> List[dict]:
    """
    Returns list of events for URLs that transitioned closed -> open.
    Does NOT alert the first time a URL is seen (baseline learning).
    """
    opened_events: List[dict] = []

    pages_state: Dict[str, dict] = state.get("pages", {})
    if not isinstance(pages_state, dict):
        pages_state = {}
        state["pages"] = pages_state

    for i, url in enumerate(urls, start=1):
        try:
            html = fetch_text(url)
            is_open, reason_text, reason_href = detect_open_signal(html)

            prev = pages_state.get(url) if isinstance(pages_state.get(url), dict) else None
            seen_before = prev is not None
            prev_open = bool(prev.get("open")) if prev else False

            # Notify only if we have a previous observation AND it flips closed -> open
            if seen_before and (not prev_open) and is_open:
                opened_events.append({
                    "url": url,
                    "reason_text": reason_text,
                    "reason_href": reason_href,
                })

            pages_state[url] = {
                "open": is_open,
                "last_seen": now_iso(),
                "reason_text": reason_text,
                "reason_href": reason_href,
            }

        except Exception as e:
            pages_state.setdefault(url, {"open": False, "last_seen": None})
            print(f"Fejl ved hentning: {url} -> {e}")

        if i < len(urls):
            time.sleep(SLEEP_BETWEEN_PAGE_FETCHES_SEC)

    state["pages"] = pages_state
    return opened_events


# ----------------------------
# Main
# ----------------------------

def build_email(opened_events: List[dict], new_urls: List[str]) -> tuple[str, str]:
    # Subject severity: ÅBNING trumfer NY SIDE
    if opened_events:
        subject = f"🚨 [Waitly] ÅBNING: {len(opened_events)} liste(r) åbnede"
    else:
        subject = f"🆕 [Waitly] NY SIDE: {len(new_urls)} nye relevante sider"

    sections: List[str] = [
        f"Tidspunkt (UTC): {now_iso()}",
        f"Sitemap: {SITEMAP_URL}",
    ]

    if opened_events:
        lines = ["", "🚨 ÅBNINGER (lukket → åben):"]
        for ev in opened_events:
            lines.append(f"- {ev['url']}")
            if ev.get("reason_text"):
                lines.append(f"  tekst: {ev['reason_text']}")
            if ev.get("reason_href"):
                lines.append(f"  link:  {ev['reason_href']}")
        sections.append("\n".join(lines))

    if new_urls:
        lines = ["", "🆕 NYE SIDER (sitemap):"]
        for u in new_urls:
            lines.append(f"- {u}")
        sections.append("\n".join(lines))

    sections.append("")
    sections.append(f"State: {STATE_FILE.resolve()}")

    body = "\n".join(sections)
    return subject, body


def main() -> int:
    state = load_state(STATE_FILE)

    # 1) Discover new relevant URLs from sitemap
    new_urls = sitemap_check(state)

    # 2) Watch known pages for openings
    watch_urls = load_watch_urls(WATCHLIST_FILE)
    opened_events = watch_pages_for_openings(state, watch_urls) if watch_urls else []

    # Save state BEFORE email so we don't spam if email fails
    save_state(STATE_FILE, state)

    if opened_events or new_urls:
        subject, body = build_email(opened_events, new_urls)
        print(body)
        send_email(subject, body, NOTIFY_TO)
    else:
        print("Ingen nye relevante sitemap-links og ingen åbninger i watchlisten.")

    if not watch_urls:
        print(f"Tip: Opret {WATCHLIST_FILE} med én Waitly-URL pr. linje for åbningsovervågning.")

    return 0


# ----------------------------
# Debug-wrapper (din præference)
# ----------------------------
if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("\nCRASH:\n" + traceback.format_exc())
        input("Tryk Enter for at afslutte...")
        raise

    # --- Export waitlist positions for dashboard (requires login) ---
    try:
        import os
        waitly_email = os.environ.get("WAITLY_LOGIN_EMAIL", "").strip()
        waitly_pass = os.environ.get("WAITLY_LOGIN_PASSWORD", "").strip()

        if waitly_email and waitly_pass:
            from waitly_positions import fetch_positions_via_login, write_current_json
            snapshot = fetch_positions_via_login(waitly_email, waitly_pass, headless=True)
            write_current_json(snapshot, "current.json")
            print(f"[dashboard] Wrote current.json with {len(snapshot.get('queues', []))} queues.")
        else:
            print("[dashboard] WAITLY_LOGIN_EMAIL/PASSWORD not set; skipping position export.")
    except Exception as e:
        print(f"[dashboard] Failed to export positions: {e}")
