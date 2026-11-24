import pytest

from scraper import db
from scraper.parser import BookingError, SchemaError, parse_page

CARD = {
    "reviewUrl": "abc123", "reviewScore": 9.0, "reviewedDate": 1791666297,
    "textDetails": {"lang": "en", "positiveText": "Great &amp; clean", "negativeText": "Nothing", "title": None},
    "guestDetails": {"username": "Sam", "countryName": "Australia", "guestTypeTranslation": "Solo traveller", "anonymous": False},
    "bookingDetails": {"roomType": {"name": "Double"}, "numNights": 2, "checkinDate": "2026-10-08", "checkoutDate": "2026-10-10"},
    "partnerReply": None, "helpfulVotesCount": 0,
}


def page(*cards, total=None):
    return {"data": {"reviewListFrontend": {"__typename": "ReviewListFrontendResult",
                                            "reviewCard": list(cards), "reviewsCount": total or len(cards)}}}


def test_parse_valid_card():
    reviews, total, warnings = parse_page(page(CARD), "p1")
    assert total == 1 and not warnings
    rv = reviews[0]
    assert rv.review_id == "abc123" and rv.liked_text == "Great & clean"
    assert rv.reviewed_date_local == "2026-10-11"  # Sydney time, not UTC


def test_bad_card_is_skipped_not_fatal():
    bad = {**CARD, "reviewScore": None}
    reviews, _, warnings = parse_page(page(CARD, bad), "p1")
    assert len(reviews) == 1 and len(warnings) == 1


def test_booking_error_object_raises():
    payload = {"data": {"reviewListFrontend": {"__typename": "ReviewsFrontendError", "message": "Request not supported", "statusCode": 400}}}
    with pytest.raises(BookingError):
        parse_page(payload, "p1")


def test_schema_change_raises():
    with pytest.raises(SchemaError):
        parse_page({"data": {"somethingElse": {}}}, "p1")


def test_upsert_dedupes_and_detects_edits(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    reviews, _, _ = parse_page(page(CARD, CARD), "p1")
    assert db.upsert_reviews(conn, reviews) == (1, 0)          # duplicate in same batch
    assert db.upsert_reviews(conn, reviews) == (0, 0)          # re-run: nothing new
    edited = {**CARD, "partnerReply": {"reply": "Thanks!"}}
    reviews2, _, _ = parse_page(page(edited), "p1")
    assert db.upsert_reviews(conn, reviews2) == (0, 1)         # hotel reply -> update
    assert conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 1
