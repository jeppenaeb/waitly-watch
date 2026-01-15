# -*- coding: utf-8 -*-
"""
waitly_watch_all.py

What it does (high level):
1) Sitemap scan: finds new relevant Waitly pages for Copenhagen + Frederiksberg.
2) Opening watch: checks URLs in watch_urls.txt for "Tilmeld" (signup) links; emails on closed->open.
   Removes URLs from watch_urls.txt if the page is gone (HTTP 404/410) or if it opened.
3) Dashboard export: logs into my.waitly.dk via Playwright (see waitly_positions.py) and writes current.json.

State files (committed back to repo by the workflow):
- state/known_sitemap_urls.json
- state/open_state.json
- watch_urls.txt (may be edited)
"""

from __future__ import annotations

import json
import os
import re
import smtplib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Tuple

import requests
from bs4 import BeautifulSoup

from waitly_positions import fetch_positions_via_login, write_current_json


SITEMAP_URL = "https://waitly.eu/da/sitemap"

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
KNOWN_SITEMAP_PATH = STATE_DIR / "known_sitemap_urls.json"
OPEN_STATE_PATH = STATE_DIR / "open_state.json"
WATCH_URLS_PATH = ROOT / "watch_urls.txt"
CURRENT_JSON_PATH = ROOT / "current.json"


def iso_date_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v


@dataclass
class SmtpCfg:
    host: str
    port: int
    user: str
    password: str
    mail_from: str
    mail_to: str


def load_smtp_cfg() -> SmtpCfg | None:
    host = env("WAITLY_SMTP_HOST")
    port = env("WAITLY_SMTP_PORT")
    user = env("WAITLY_SMTP_USER")
    password = env("WAITLY_SMTP_PASS")
    mail_from = env("WAITLY_MAIL_FROM") or user
    # By default: send to the login mailbox (often the same). Can be overridden.
    mail_to = env("WAITLY_MAIL_TO") or user

    if not all([host, port, user, password, mail_from, mail_to]):
        print("SMTP not fully configured (missing one or more WAITLY_SMTP_* / WAITLY_MAIL_* env vars).")
        return None

    try:
        port_i = int(str(port))
    except Exception:
        print("Invalid WAITLY_SMTP_PORT:", port)
        return None

    return SmtpCfg(host=host, port=port_i, user=user, password=password, mail_from=mail_from, mail_to=mail_to)


def send_mail(cfg: SmtpCfg, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.mail_from
    msg["To"] = cfg.mail_to
    msg.set_content(body)

    with smtplib.SMTP(cfg.host, cfg.port, timeout=30) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        s.login(cfg.user, cfg.password)
        s.send_message(msg)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_sitemap_urls() -> List[str]:
    r = requests.get(SITEMAP_URL, timeout=30)
    r.raise_for_status()
    text = r.text

    # The sitemap page can be HTML or XML-ish; handle both.
    urls: List[str] = []

    # XML style <loc>https://...</loc>
    locs = re.findall(r"<loc>\s*(https?://[^<\s]+)\s*</loc>", text)
    if locs:
        urls.extend(locs)
        return sorted(set(urls))

    # HTML: collect links
    soup = BeautifulSoup(text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("http"):
            urls.append(href)

    return sorted(set(urls))


def is_relevant_url(url: str) -> bool:
    u = url.lower()

    # We mainly care about association pages.
    if "/da/foreninger/" not in u:
        return False

    # Copenhagen: URLs typically include "####-koebenhavn-k/v/n/o/s"
    if re.search(r"/\d{4}-koebenhavn-(k|v|n|o|oe|s)(/|$)", u):
        return True

    # Frederiksberg: often "####-frederiksberg" and sometimes "-c"
    if re.search(r"/\d{4}-frederiksberg(-c)?(/|$)", u):
        return True

    return False


def sitemap_watch(smtp: SmtpCfg | None) -> None:
    known: List[str] = load_json(KNOWN_SITEMAP_PATH, [])
    known_set = set(known)

    urls = fetch_sitemap_urls()
    relevant = [u for u in urls if is_relevant_url(u)]

    new = [u for u in relevant if u not in known_set]

    print(f"Sitemap: total={len(urls)} relevant={len(relevant)} new={len(new)}")

    if new and smtp:
        body = "Nye relevante Waitly-sider fundet via sitemap:\n\n" + "\n".join(new) + "\n"
        send_mail(smtp, "Waitly Watch: nye relevante ventelister", body)

    # Persist full relevant set (so you don't get repeat mails)
    save_json(KNOWN_SITEMAP_PATH, sorted(set(known_set.union(relevant))))


def page_is_open(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")

    # Look for a "Tilmeld" link to app.waitly.*
    for a in soup.find_all("a", href=True):
        text = (a.get_text() or "").strip().lower()
        href = (a["href"] or "").strip().lower()
        if "tilmeld" in text and ("app.waitly.dk" in href or "app.waitly.eu" in href):
            return True

    # Fallback: some pages might not have visible "Tilmeld" text but do contain app.waitly links.
    if "app.waitly.dk" in html.lower() or "app.waitly.eu" in html.lower():
        return True

    return False


def read_watch_urls() -> List[str]:
    if not WATCH_URLS_PATH.exists():
        return []
    urls = []
    for line in WATCH_URLS_PATH.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        urls.append(s)
    return urls


def write_watch_urls(urls: List[str]) -> None:
    WATCH_URLS_PATH.write_text("\n".join(urls) + ("\n" if urls else ""), encoding="utf-8")


def opening_watch(smtp: SmtpCfg | None) -> None:
    urls = read_watch_urls()
    state: Dict[str, bool] = load_json(OPEN_STATE_PATH, {})

    removed_gone: List[str] = []
    opened_now: List[str] = []
    kept: List[str] = []

    for url in urls:
        try:
            r = requests.get(url, timeout=30, allow_redirects=True)
            status = r.status_code
        except Exception as e:
            print("Fetch failed:", url, e)
            kept.append(url)
            continue

        if status in (404, 410):
            removed_gone.append(url)
            state.pop(url, None)
            continue

        html = r.text or ""
        open_now = page_is_open(html)
        was_open = bool(state.get(url, False))

        state[url] = open_now

        if open_now and not was_open:
            opened_now.append(url)
            # Remove from watch list once opened to avoid repeated mails
            state.pop(url, None)
        else:
            kept.append(url)

    if removed_gone:
        print("Removed gone URLs:", len(removed_gone))
        if smtp:
            body = "Fjernet fra watch_urls.txt (HTTP 404/410):\n\n" + "\n".join(removed_gone) + "\n"
            send_mail(smtp, "Waitly Watch: ventelister fjernet (404/410)", body)

    if opened_now:
        print("Opened now:", len(opened_now))
        if smtp:
            body = "Ventelister er nu ÅBNE (lukket → åben):\n\n" + "\n".join(opened_now) + "\n"
            send_mail(smtp, "Waitly Watch: ventelister åbnede!", body)

    # Persist state and updated watch list
    save_json(OPEN_STATE_PATH, state)
    write_watch_urls(kept)


def dashboard_export(smtp: SmtpCfg | None) -> None:
    email = env("WAITLY_LOGIN_EMAIL")
    password = env("WAITLY_LOGIN_PASSWORD")

    if not email or not password:
        print("WAITLY_LOGIN_EMAIL/PASSWORD not set; skipping dashboard export.")
        return

    try:
        data = fetch_positions_via_login(email=email, password=password, headless=True)
        write_current_json(data, str(CURRENT_JSON_PATH))
        print("Wrote", CURRENT_JSON_PATH)
    except Exception as e:
        print("Dashboard export failed:", e)
        if smtp:
            send_mail(
                smtp,
                "Waitly Watch: dashboard export fejlede",
                f"Kunne ikke hente positionsdata via my.waitly.dk.\n\nFejl:\n{e}\n",
            )
        raise


def main() -> None:
    smtp = load_smtp_cfg()

    # 1) sitemap scan (emails only for NEW URLs; state is persisted)
    try:
        sitemap_watch(smtp)
    except Exception as e:
        print("Sitemap watch failed:", e)
        if smtp:
            send_mail(smtp, "Waitly Watch: sitemap-scan fejlede", f"Fejl:\n{e}\n")

    # 2) opening watch (emails on transitions; state persisted; watch_urls updated)
    try:
        opening_watch(smtp)
    except Exception as e:
        print("Opening watch failed:", e)
        if smtp:
            send_mail(smtp, "Waitly Watch: åbnings-overvågning fejlede", f"Fejl:\n{e}\n")

    # 3) dashboard export (critical for pipeline: re-raise on failure)
    dashboard_export(smtp)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Debug-wrapper as requested in your global preferences (prints traceback and waits)
        import traceback
        traceback.print_exc()
        try:
            input("Press Enter to exit...")
        except Exception:
            pass
        raise
