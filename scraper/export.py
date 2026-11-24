"""Classify stored reviews and write the JSON file the dashboard reads.

The dashboard is a static site, so all filtering/aggregation happens in the
browser on this one file. At this scale (hundreds to low thousands of reviews)
that is instant and means the hosted demo needs no server or database.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .classify import TOPICS, classify
from .config import (EXPORT_PATH, LOCAL_TZ, NEGATIVE_MAX_SCORE, POSITIVE_MIN_SCORE,
                     load_properties)


def build_dashboard_data(conn) -> dict:
    props = load_properties()
    reviews = []
    for row in conn.execute("SELECT * FROM reviews ORDER BY reviewed_at DESC"):
        r = dict(row)
        c = classify(r)
        reviews.append({
            "id": r["review_id"],
            "property": r["property_id"],
            "score": r["score"],
            "date": r["reviewed_date_local"],
            "reviewedAt": r["reviewed_at"],
            "title": r["title"],
            "liked": r["liked_text"],
            "disliked": r["disliked_text"],
            "lang": r["language"],
            "guest": r["guest_name"],
            "country": r["guest_country"],
            "travellerType": r["traveller_type"],
            "room": r["room_type"],
            "nights": r["nights"],
            "checkin": r["checkin_date"],
            "reply": r["hotel_reply"],
            "sentiment": c["sentiment"],
            "complaints": c["complaints"],
            "praise": c["praise"],
            "classifiable": c["classifiable"],
            "firstSeen": r["first_seen_at"],
        })

    properties = []
    for p in props:
        snap = conn.execute(
            "SELECT * FROM property_snapshots WHERE property_id = ? ORDER BY captured_at DESC LIMIT 1",
            (p.id,)).fetchone()
        run = conn.execute(
            "SELECT * FROM scrape_runs WHERE property_id = ? AND status != 'running' "
            "ORDER BY id DESC LIMIT 1", (p.id,)).fetchone()
        last_ok = conn.execute(
            "SELECT finished_at FROM scrape_runs WHERE property_id = ? AND status IN ('success','partial') "
            "ORDER BY id DESC LIMIT 1", (p.id,)).fetchone()
        stored = conn.execute("SELECT COUNT(*) FROM reviews WHERE property_id = ?", (p.id,)).fetchone()[0]
        properties.append({
            "id": p.id,
            "name": p.name,
            "shortName": p.short_name,
            "url": p.url,
            "bookingScore": snap["overall_score"] if snap else None,
            "bookingReviewCount": snap["total_reviews_on_booking"] if snap else None,
            "categoryScores": json.loads(snap["category_scores_json"]) if snap and snap["category_scores_json"] else [],
            "storedReviews": stored,
            "lastRun": {
                "status": run["status"], "finishedAt": run["finished_at"], "inserted": run["inserted"],
                "error": run["error"],
            } if run else None,
            "lastSuccessAt": last_ok["finished_at"] if last_ok else None,
        })

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "timezone": str(LOCAL_TZ),
        "thresholds": {"positiveMin": POSITIVE_MIN_SCORE, "negativeMax": NEGATIVE_MAX_SCORE},
        "topics": [{"key": t.key, "label": t.label} for t in TOPICS],
        "properties": properties,
        "reviews": reviews,
    }


def export_dashboard(path: Path = EXPORT_PATH) -> tuple[Path, int]:
    conn = db.connect()
    try:
        data = build_dashboard_data(conn)
    finally:
        conn.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)   # atomic: the dashboard never sees a half-written file
    return path, len(data["reviews"])
