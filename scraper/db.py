"""SQLite storage: reviews (deduplicated), property snapshots and a scrape-run log."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH
from .parser import Review

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    property_id         TEXT NOT NULL,
    review_id           TEXT NOT NULL,
    score               REAL NOT NULL,
    reviewed_at         TEXT NOT NULL,
    reviewed_date_local TEXT NOT NULL,
    title               TEXT,
    liked_text          TEXT,
    disliked_text       TEXT,
    language            TEXT,
    guest_name          TEXT,
    guest_country       TEXT,
    traveller_type      TEXT,
    room_type           TEXT,
    nights              INTEGER,
    checkin_date        TEXT,
    checkout_date       TEXT,
    hotel_reply         TEXT,
    helpful_votes       INTEGER NOT NULL DEFAULT 0,
    content_hash        TEXT NOT NULL,
    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    PRIMARY KEY (property_id, review_id)
);
CREATE INDEX IF NOT EXISTS idx_reviews_date ON reviews (reviewed_date_local);

CREATE TABLE IF NOT EXISTS property_snapshots (
    property_id              TEXT NOT NULL,
    captured_at              TEXT NOT NULL,
    booking_hotel_id         INTEGER,
    overall_score            REAL,
    total_reviews_on_booking INTEGER,
    category_scores_json     TEXT,
    PRIMARY KEY (property_id, captured_at)
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id  TEXT NOT NULL,
    mode         TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL,          -- running | success | partial | failed
    pages        INTEGER DEFAULT 0,
    fetched      INTEGER DEFAULT 0,
    inserted     INTEGER DEFAULT 0,
    updated      INTEGER DEFAULT 0,
    warnings     TEXT,
    error        TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def known_ids(conn: sqlite3.Connection, property_id: str) -> set[str]:
    rows = conn.execute("SELECT review_id FROM reviews WHERE property_id = ?", (property_id,))
    return {r[0] for r in rows}


def upsert_reviews(conn: sqlite3.Connection, reviews: list[Review]) -> tuple[int, int]:
    """Insert new reviews, update changed ones (edit / new hotel reply).

    Returns (inserted, updated). Dedup key is (property_id, review_id); the same
    review appearing twice in one batch or across runs is stored once.
    """
    now = utcnow()
    inserted = updated = 0
    seen_in_batch: set[tuple[str, str]] = set()
    for r in reviews:
        key = (r.property_id, r.review_id)
        if key in seen_in_batch:
            continue
        seen_in_batch.add(key)
        existing = conn.execute(
            "SELECT content_hash FROM reviews WHERE property_id = ? AND review_id = ?", key
        ).fetchone()
        row = r.to_row()
        if existing is None:
            cols = list(row) + ["first_seen_at", "last_seen_at", "updated_at"]
            vals = list(row.values()) + [now, now, now]
            conn.execute(
                f"INSERT INTO reviews ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                vals,
            )
            inserted += 1
        elif existing["content_hash"] != r.content_hash:
            sets = ", ".join(f"{c} = ?" for c in row)
            conn.execute(
                f"UPDATE reviews SET {sets}, last_seen_at = ?, updated_at = ? "
                "WHERE property_id = ? AND review_id = ?",
                list(row.values()) + [now, now, *key],
            )
            updated += 1
        else:
            conn.execute(
                "UPDATE reviews SET last_seen_at = ? WHERE property_id = ? AND review_id = ?",
                (now, *key),
            )
    return inserted, updated


def save_snapshot(conn, property_id: str, hotel_id: int | None, overall: float | None, summary: dict):
    conn.execute(
        "INSERT OR REPLACE INTO property_snapshots VALUES (?, ?, ?, ?, ?, ?)",
        (
            property_id,
            utcnow(),
            hotel_id,
            overall,
            summary.get("total_reviews_on_booking"),
            json.dumps(summary.get("category_scores") or []),
        ),
    )


def start_run(conn, property_id: str, mode: str) -> int:
    cur = conn.execute(
        "INSERT INTO scrape_runs (property_id, mode, started_at, status) VALUES (?, ?, ?, 'running')",
        (property_id, mode, utcnow()),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id: int, status: str, *, pages=0, fetched=0, inserted=0,
               updated=0, warnings: list[str] | None = None, error: str | None = None):
    conn.execute(
        "UPDATE scrape_runs SET finished_at = ?, status = ?, pages = ?, fetched = ?, inserted = ?, "
        "updated = ?, warnings = ?, error = ? WHERE id = ?",
        (utcnow(), status, pages, fetched, inserted, updated,
         json.dumps(warnings[:50]) if warnings else None, error, run_id),
    )
    conn.commit()
