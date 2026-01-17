"""
Waitly sitemap discovery (København + Frederiksberg).

Purpose
-------
Detect *new* Waitly association pages ("foreninger") by diffing the public sitemap.

Scope
-----
Only these areas are included:

- København K (1000–1499)
- København V (1500–1799)
- København Ø (2100)
- København N (2200)
- København S (2300–2450)
- Frederiksberg (1800–2000)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup


SITEMAP_URL = "https://waitly.eu/da/sitemap"

# State files (committed by GitHub Actions)
KNOWN_URLS_KBH_PATH = "state/known_sitemap_urls_kbh.json"
DISCOVERED_LOG_KBH_PATH = "state/discovered_forenings_log_kbh.json"


FORENING_RE = re.compile(r"^/da/foreninger/(\d{4})-[^/]+/([^/?#]+)")


@dataclass(frozen=True)
class ForeningUrl:
    url: str
    postcode: int
    area: str
    slug: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _area_from_postcode(postcode: int) -> Optional[str]:
    # Singles first
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


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch_sitemap_html(timeout_s: int = 30) -> str:
    r = requests.get(SITEMAP_URL, timeout=timeout_s)
    r.raise_for_status()
    return r.text


def extract_scoped_forening_urls(sitemap_html: str) -> List[ForeningUrl]:
    """Parse HTML sitemap and return forenings-URLs limited to scope."""
    soup = BeautifulSoup(sitemap_html, "html.parser")

    hrefs: Set[str] = set()
    for a in soup.find_all("a", href=True):
