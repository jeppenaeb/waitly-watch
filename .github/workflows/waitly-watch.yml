from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Set, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


SITEMAP_URL = "https://waitly.eu/da/sitemap"

KNOWN_URLS_KBH_PATH = "state/known_sitemap_urls_kbh.json"
DISCOVERED_LOG_KBH_PATH = "state/discovered_forenings_log_kbh.json"

# Match on PATH only (handles absolute and relative URLs)
FORENING_PATH_RE = re.compile(r"^/da/foreninger/(\d{4})-[^/]+/([^/?#]+)")


@dataclass(frozen=True)
class ForeningUrl:
    url: str          # path only, e.g. /da/foreninger/2100-.../slug
    postcode: int
    area: str
    slug: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def area_from_postcode(postcode: int) -> Optional[str]:
    if postcode == 2100:
        return "København Ø"
    if postcode == 2200:
        return "København N"

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


def _normalize_href_to_path(href: str) -> Optional[str]:
    if not href:
        return None

    if href.startswith("/da/"):
        return href

    if href.startswith("http://") or href.startswith("https://"):
        try:
            p = urlparse(href)
            if p.path.startswith("/da/"):
                return p.path
        except Exception:
            return None

    return None


def extract_scoped_forening_urls(html: str) -> List[ForeningUrl]:
    soup = BeautifulSoup(html, "html.parser")

    paths: Set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if not isinstance(href, str):
            continue

        path = _normalize_href_to_path(href)
        if path and path.startswith("/da/foreninger/"):
            paths.add(path)

    results: List[ForeningUrl] = []
    for path in paths:
        m = FORENING_PATH_RE.match(path)
        if not m:
            continue

        postcode = int(m.group(1))
        slug = m.group(2)
        area = area_from_postcode(postcode)
        if not area:
            continue

        results.append(
            ForeningUrl(
                url=path,
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

    # First run OR empty/broken baseline → init silently
    if (
        not isinstance(known, dict)
        or "urls" not in known
        or not isinstance(known.get("urls"), list)
        or len(known.get("urls")) == 0
    ):
        save_json(
            KNOWN_URLS_KBH_PATH,
            {
                "initialized_at": utc_now_iso(),
                "source": SITEMAP_URL,
                "urls": sorted(current_set),
                "note": "Baseline initialized (or re-initialized) without alert.",
            },
        )
        return True, []

    known_set = set(known.get("urls", []))
    new_urls = current_set - known_set
    new_items = [x for x in current if x.url in new_urls]

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
