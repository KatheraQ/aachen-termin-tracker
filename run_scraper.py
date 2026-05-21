"""
Background loop: poll the Termin site, diff against seen_slots, email subscribers.

Run alongside run_server.py (separate process).
"""
from __future__ import annotations

import asyncio
import logging
import random
import sys
from typing import List

from app import db
from app.config import Anliegen, Config, load_config
from app.notifier import send_termin_alert
from app.scraper import fetch_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("scraper-loop")


async def _notify_subscribers(cfg: Config, anliegen: Anliegen, new_dates: List[str]) -> None:
    if not new_dates:
        return
    subs = db.subscribers_for(anliegen.id)
    log.info("notifying %d subscribers for %s (%d new dates)", len(subs), anliegen.id, len(new_dates))
    for sub in subs:
        unsent = [d for d in new_dates if not db.already_notified(sub["id"], anliegen.id, d)]
        if not unsent:
            continue
        try:
            await send_termin_alert(
                cfg=cfg,
                email=sub["email"],
                unsubscribe_token=sub["unsubscribe_token"],
                anliegen=anliegen,
                new_slot_dates=unsent,
            )
            for d in unsent:
                db.record_notification(sub["id"], anliegen.id, d)
        except Exception:
            log.exception("failed to email %s", sub["email"])


async def one_cycle(cfg: Config) -> None:
    by_id = {a.id: a for a in cfg.anliegen}
    results = await fetch_all(cfg.scraper, cfg.anliegen)
    for anliegen_id, dates in results.items():
        anliegen = by_id[anliegen_id]
        # Forget dates that no longer appear so they trigger again if they come back.
        db.expire_unseen_slots(anliegen_id, dates)
        new_dates = db.mark_seen_slots(anliegen_id, dates)
        if new_dates:
            log.info("NEW slots for %s: %s", anliegen_id, new_dates)
        await _notify_subscribers(cfg, anliegen, new_dates)


async def main() -> None:
    cfg = load_config()
    db.init_db()
    log.info("scraper loop starting; target=%s", cfg.scraper.target_url)
    while True:
        try:
            await one_cycle(cfg)
        except Exception:
            log.exception("cycle failed")
        delay = random.uniform(
            cfg.scraper.poll_interval_min_seconds,
            cfg.scraper.poll_interval_max_seconds,
        )
        log.info("sleeping %.1fs", delay)
        await asyncio.sleep(delay)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
