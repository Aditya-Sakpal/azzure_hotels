"""Command line entry point.

    python -m scraper collect              # incremental update, all properties
    python -m scraper collect --full       # re-walk every page
    python -m scraper collect -p potts-point
    python -m scraper export               # classify + write dashboard JSON
    python -m scraper status               # last run per property + counts
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import db
from .config import load_properties


def _cmd_collect(args) -> int:
    from .collector import collect

    props = load_properties()
    if args.property:
        props = [p for p in props if p.id in args.property]
        if not props:
            print(f"Unknown property. Choose from: {[p.id for p in load_properties()]}")
            return 2
    results = asyncio.run(collect(props, full=args.full, headless=args.headless,
                                  channel=None if args.bundled_chromium else "chrome"))
    print("\nProperty              status    pages  fetched  new  updated  on-booking")
    for r in results:
        print(f"{r.property_id:<21} {r.status:<9} {r.pages:>5} {r.fetched:>8} {r.inserted:>4} "
              f"{r.updated:>8}  {r.total_on_booking}")
        if r.error:
            print(f"    error: {r.error}")
        for w in r.warnings[:5]:
            print(f"    warning: {w}")
    if args.export:
        _cmd_export(args)
    # Non-zero exit if everything failed, so schedulers/CI can alert.
    return 0 if any(r.status != "failed" for r in results) else 1


def _cmd_export(args) -> int:
    from .export import export_dashboard

    path, n = export_dashboard()
    print(f"Exported {n} reviews -> {path}")
    return 0


def _cmd_status(args) -> int:
    conn = db.connect()
    for p in load_properties():
        n = conn.execute("SELECT COUNT(*), MAX(reviewed_date_local) FROM reviews WHERE property_id=?",
                         (p.id,)).fetchone()
        run = conn.execute("SELECT * FROM scrape_runs WHERE property_id=? ORDER BY id DESC LIMIT 1",
                           (p.id,)).fetchone()
        last = f"{run['status']} at {run['finished_at']}" if run else "never run"
        print(f"{p.id:<21} {n[0]:>5} reviews  newest {n[1]}  last run: {last}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scraper")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="collect reviews from Booking.com")
    c.add_argument("--full", action="store_true", help="walk all pages, not just new reviews")
    c.add_argument("-p", "--property", action="append", help="property id (repeatable)")
    c.add_argument("--headless", action="store_true",
                   help="run without a window (currently blocked by Booking — see README)")
    c.add_argument("--bundled-chromium", action="store_true",
                   help="use Playwright's Chromium instead of installed Google Chrome")
    c.add_argument("--export", action="store_true", help="run export afterwards")
    c.set_defaults(func=_cmd_collect)

    e = sub.add_parser("export", help="classify reviews and write dashboard JSON")
    e.set_defaults(func=_cmd_export)

    s = sub.add_parser("status", help="show collection status")
    s.set_defaults(func=_cmd_status)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
