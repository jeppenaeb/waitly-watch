import os
import json
import asyncio
import sys
from datetime import datetime, timezone
from typing import List, Dict

from waitly_positions import fetch_positions
from waitly_sitemap_kbh import run_kbh_sitemap_discovery


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    ts = utc_now_iso()
    print(f"[waitly_watch_all {ts}] {msg}", flush=True)


async def main() -> None:
    log("Waitly Watch started")

    # 1) Discover new Waitly "foreninger" pages in København/Frederiksberg via sitemap.
    #    This runs without login and provides early warnings when new associations appear.
    try:
        sitemap_kbh = run_kbh_sitemap_discovery()
        if sitemap_kbh.get("new_count", 0):
            log(f"Sitemap (KBH) discovered {sitemap_kbh['new_count']} new forening(s)")
        else:
            log("Sitemap (KBH) no new forening(s)")
    except Exception as e:
        # Never crash the entire run due to sitemap parsing; positions still matter.
        log(f"WARNING: Sitemap (KBH) discovery failed: {e}")
        sitemap_kbh = {
            "updated_at": utc_now_iso(),
            "source": "https://waitly.eu/da/sitemap",
            "error": str(e),
        }

    email = os.getenv("WAITLY_LOGIN_EMAIL", "")
    password = os.getenv("WAITLY_LOGIN_PASSWORD", "")

    if not email or not password:
        raise RuntimeError("WAITLY_LOGIN_EMAIL / WAITLY_LOGIN_PASSWORD missing")

    queues: List[Dict] = await fetch_positions(email, password)

    # --- HARD FAIL if login creds exist but result is empty ---
    if isinstance(queues, list) and len(queues) == 0:
        raise RuntimeError(
            "Login credentials are set, but positions scrape returned 0 queues."
        )

    data = {
        "updated_at": utc_now_iso(),
        "queues": queues,
        "sitemap_kbh": sitemap_kbh,
    }

    with open("current.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    log(f"current.json written with {len(queues)} queues")


if __name__ == "__main__":
    # Debug-wrapper (your preference): print traceback + wait for Enter on crash.
    # NOTE: In GitHub Actions stdin is not a TTY; in that case we do not block.
    try:
        asyncio.run(main())
    except Exception:
        log("FATAL ERROR")
        import traceback

        traceback.print_exc()
        if sys.stdin.isatty():
            try:
                input("\n[Crash] Press Enter to exit...")
            except Exception:
                pass
        raise
