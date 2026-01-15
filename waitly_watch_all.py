import os
import json
import asyncio
from datetime import datetime, timezone
from typing import List, Dict

from waitly_positions import fetch_positions


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    ts = utc_now_iso()
    print(f"[waitly_watch_all {ts}] {msg}", flush=True)


async def main() -> None:
    log("Waitly Watch started")

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
    }

    with open("current.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    log(f"current.json written with {len(queues)} queues")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        log("FATAL ERROR")
        raise
