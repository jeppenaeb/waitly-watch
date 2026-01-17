from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup


SITEMAP_URL = "https://waitly.eu/da/sitemap"

KNOWN_URLS_KBH_PATH = "state/known_sitemap_urls_kbh.json"
DISCOVERED_LOG_KBH_PATH = "state/discovered_forenings_log_kbh.json"

FORENING_RE = re.compile(r"^/da/foreninger/(\d{4})-[^/]+/([^/?#]+)")


@dataclass(frozen=True)
class ForeningUrl:
    url: str
    postcode: int
    area: str
    slug: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def area_from_postcode(postcode: int) -> Optional[str]:
    # Singles
    if postcode == 2100:
        return "København Ø"
    if postcode == 2200:
        return "København N"

    # Ranges
    ranges: List[Tuple[int, int, str]] = [
        (1000, 1499, "København K"),
        (1500, 1799, "København V"),
        (1800, 2000, "Frederiksberg"),
        (2300, 2450, "København S"),
    ]
    for lo, hi, label in ranges:
        if lo <= postcode <= hi:
            return label
    return None


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch_sitemap_html(timeout: int = 30) -> str:
    r = requests.get(SITEMAP_URL, timeout=timeout)
    r.raise_for_status()
    return r.text


def extract_scoped_forening_urls(html: str) -> List[ForeningUrl]:
    soup = BeautifulSoup(html, "html.parser")

    hrefs: Set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if isinstance(href, str) and href.startswith("/da/foreninger/"):
            hrefs.add(href)

    results: List[ForeningUrl] = []
    for href in hrefs:
        m = FORENING_RE.match(href)
        if not m:
            continue

        postcode = int(m.group(1))
        slug = m.group(2)
        area = area_from_postcode(postcode)

        if not area:
            continue

        results.append(
            ForeningUrl(
                url=href,
                postcode=postcode,
                area=area,
                slug=slug,
            )
        )

    results.sort(key=lambda x: (x.postcode, x.area, x.slug))
    return results


def diff_against_known(current: List[ForeningUrl]):
    known = load_json(KNOWN_URLS_KBH_PATH, None)
    current_set = {x.url for x in current}

    # First run: initialize baseline, do not alert
    if not isinstance(known, dict) or "urls" not in known:
        save_json(
            KNOWN_URLS_KBH_PATH,
            {
                "initialized_at": utc_now_iso(),
                "source": SITEMAP_URL,
                "urls": sorted(current_set),
            },
        )
        return True, []

    known_set = set(known.get("urls", []))
    new_urls = current_set - known_set
    new_items = [x for x in current if x.url in new_urls]

    # Update baseline
    known["updated_at"] = utc_now_iso()
    known["urls"] = sorted(current_set)
    save_json(KNOWN_URLS_KBH_PATH, known)

    return False, new_items


def append_discovered(new_items: List[ForeningUrl]) -> None:
    if not new_items:
        return

    log = load_json(DISCOVERED_LOG_KBH_PATH, [])
    if not isinstance(log, list):
        log = []

    ts = utc_now_iso()
    for x in new_items:
        log.append(
            {
                "discovered_at": ts,
                "url": x.url,
                "postcode": f"{x.postcode:04d}",
                "area": x.area,
                "slug": x.slug,
                "source": "sitemap",
                "action_taken": False,
            }
        )

    save_json(DISCOVERED_LOG_KBH_PATH, log)


def run_kbh_sitemap_discovery():
    html = fetch_sitemap_html()
    current = extract_scoped_forening_urls(html)

    initialized, new_items = diff_against_known(current)
    append_discovered(new_items)

    return {
        "updated_at": utc_now_iso(),
        "source": SITEMAP_URL,
        "initialized": initialized,
        "total_urls": len(current),
        "new_count": len(new_items),
        "new": [
            {
                "url": x.url,
                "postcode": f"{x.postcode:04d}",
                "area": x.area,
                "slug": x.slug,
                "full_url": "https://waitly.eu" + x.url,
            }
            for x in new_items
        ],
    }
