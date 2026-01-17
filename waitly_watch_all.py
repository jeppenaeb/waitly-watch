import os
import json
import asyncio
import sys
import traceback
from datetime import datetime, timezone
from typing import List, Dict

from waitly_positions import fetch_positions
from waitly_sitemap_kbh import run_kbh_sitemap_discovery
from waitly_mail import send_mail


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    ts = utc_now_iso()
    print(f"[waitly_watch_all {ts}] {msg}", flush=True)


def _format_new_forenings_email(sitemap_kbh: Dict) -> str:
    new_items = sitemap_kbh.get("new", []) or []
    lines = []
    lines.append("Der er opdaget nye forenings-sider på Waitly (København/Frederiksberg).")
    lines.append("")
    for item in new_items:
        area = item.get("area", "Ukendt område")
        postcode = item.get("postcode", "????")
        full_url = item.get("full_url", "")
        slug = item.get("slug", "")
        lines.append(f"- {area} ({postcode}): {slug}")
        if full_url:
            lines.append(f"  {full_url}")
    lines.append("")
    lines.append("Tip: Tjek hurtigst muligt om ventelisten er åben for opskrivning.")
    return "\n".join(lines)


async def main() -> None:
    log("Waitly Watch started")

    # 1) Sitemap discovery (Kbh/Fb)
    sitemap_kbh = None
    try:
        sitemap_kbh = run_kbh_sitemap_discovery()
        new_count = int(sitemap_kbh.get("new_count", 0) or 0)
        initialized = bool(sitemap_kbh.get("initialized", False))

        if initialized:
            log("Sitemap (KBH) baseline initialized (no alerts on first run).")
        elif new_count > 0:
            log(f"Sitemap (KBH) discovered {new_count} new forening(s). Sending mail...")
            subject = f"Waitly: {new_count} ny(e) forening(er) i Kbh/Fb"
            body = _format_new_forenings_email(sitemap_kbh)
            send_mail(subject, body)
            log("Mail sent.")
        else:
            log("Sitemap (KBH) no new forening(s).")

    except Exception as e:
        # Never crash the whole run because of sitemap/mail — positions still matter.
        log(f"WARNING: Sitemap (KBH) discovery/mail failed: {e}")
        sitemap_kbh = {
            "updated_at": utc_now_iso(),
            "source": "https://waitly.eu/da/sitemap",
            "error": str(e),
        }

    # 2) Positions
    email = os.getenv("WAITLY_LOGIN_EMAIL", "")
    password = os.getenv("WAITLY_LOGIN_PASSWORD", "")

    if not email or not password:
        raise RuntimeError("WAITLY_LOGIN_EMAIL / WAITLY_LOGIN_PASSWORD missing")

    queues: List[Dict] = await fetch_positions(email, password)

    # HARD FAIL if login creds exist but result is empty
    if isinstance(queues, list) and len(queues) == 0:
        raise RuntimeError("Login credentials are set, but positions scrape returned 0 queues.")

    data = {
        "updated_at": utc_now_iso(),
        "queues": queues,
        "sitemap_kbh": sitemap_kbh,
    }

    with open("current.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    log(f"current.json written with {len(queues)} queues")


if __name__ == "__main__":
    # Debug-wrapper (your preference): print traceback + wait for Enter on crash (only if TTY)
    try:
        asyncio.run(main())
    except Exception:
        log("FATAL ERROR")
        traceback.print_exc()
        if sys.stdin.isatty():
            try:
                input("\n[Crash] Press Enter to exit...")
            except Exception:
                pass
        raise
