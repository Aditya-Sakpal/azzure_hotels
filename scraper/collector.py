"""Orchestrates a collection run across properties.

Modes
  incremental (default): page newest-first and stop as soon as a whole page
                         consists of reviews we already have.
  full:                  walk every page (use for the first run, or periodically
                         to pick up edited reviews / late hotel replies).

Each property is isolated: one property failing (blocked, layout change) is
logged in `scrape_runs` and the others still run.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import db
from .booking_client import BookingClient, CollectionError
from .config import MAX_PAGES_PER_PROPERTY, PAGE_SIZE, Property
from .parser import SchemaError, parse_page, parse_property_summary

log = logging.getLogger(__name__)


@dataclass
class PropertyResult:
    property_id: str
    status: str = "failed"
    pages: int = 0
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    total_on_booking: int | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


async def collect_property(client: BookingClient, conn, prop: Property, full: bool) -> PropertyResult:
    result = PropertyResult(prop.id)
    run_id = db.start_run(conn, prop.id, "full" if full else "incremental")
    session = None
    try:
        known_before = db.known_ids(conn, prop.id)
        if not known_before and not full:
            log.info("[%s] no reviews stored yet — doing a full first collection", prop.id)
            full = True

        session = await client.open_property(prop)
        skip, total, last_date = 0, None, None
        sorted_ok = True

        while result.pages < MAX_PAGES_PER_PROPERTY:
            payload = await session.fetch_page(skip)
            reviews, page_total, warnings = parse_page(payload, prop.id)
            result.pages += 1
            result.warnings += [f"skip={skip} {w}" for w in warnings]

            if result.pages == 1:
                total = page_total
                result.total_on_booking = total
                with db.transaction(conn):
                    db.save_snapshot(conn, prop.id, session.template.hotel_id,
                                     session.template.hotel_score, parse_property_summary(payload))

            # Data-quality check: incremental early-stop is only safe if Booking
            # honoured the newest-first sort.
            for r in reviews:
                if last_date and r.reviewed_at > last_date:
                    sorted_ok = False
                last_date = r.reviewed_at
            if not sorted_ok and "sort-order" not in " ".join(result.warnings):
                result.warnings.append("sort-order: results not newest-first; disabling early stop")

            with db.transaction(conn):
                ins, upd = db.upsert_reviews(conn, reviews)
            result.fetched += len(reviews)
            result.inserted += ins
            result.updated += upd
            log.info("[%s] page %d (skip=%d): %d reviews, +%d new, %d updated (total %s)",
                     prop.id, result.pages, skip, len(reviews), ins, upd, total)

            if not reviews:
                break
            skip += PAGE_SIZE
            if total is not None and total >= 0 and skip >= total:
                break
            if not full and sorted_ok and all(r.review_id in known_before for r in reviews):
                log.info("[%s] caught up with stored reviews — stopping early", prop.id)
                break
            await session.polite_pause()

        result.status = "partial" if result.warnings else "success"
    except (CollectionError, SchemaError) as exc:
        result.error = str(exc)
        result.status = "partial" if result.inserted or result.updated else "failed"
        log.error("[%s] %s", prop.id, exc)
    except Exception as exc:  # never let one property kill the whole run
        result.error = f"{type(exc).__name__}: {exc}"
        result.status = "failed"
        log.exception("[%s] unexpected error", prop.id)
    finally:
        if session:
            await session.close()
        db.finish_run(conn, run_id, result.status, pages=result.pages, fetched=result.fetched,
                      inserted=result.inserted, updated=result.updated,
                      warnings=result.warnings, error=result.error)
    return result


async def collect(properties: list[Property], *, full: bool = False, headless: bool = False,
                  channel: str | None = "chrome") -> list[PropertyResult]:
    conn = db.connect()
    results = []
    try:
        async with BookingClient(headless=headless, channel=channel) as client:
            for prop in properties:
                results.append(await collect_property(client, conn, prop, full))
    finally:
        conn.close()
    return results
