"""Turn Booking.com `ReviewList` GraphQL payloads into validated, normalised rows.

Everything Booking-specific about the *shape* of the data lives here, so a schema
change on their side means touching one file. Unknown/missing optional fields
degrade to None; missing *required* fields raise SchemaError so a silent layout
change can never write half-empty rows into the database.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .config import LOCAL_TZ


class SchemaError(Exception):
    """Booking returned something we don't recognise (layout/API change)."""


class BookingError(Exception):
    """Booking returned an explicit error object (often bot-protection or bad input)."""


@dataclass
class Review:
    property_id: str
    review_id: str            # Booking's own stable review hash (`reviewUrl`)
    score: float              # 1-10
    reviewed_at: str          # ISO-8601 UTC
    reviewed_date_local: str  # YYYY-MM-DD in Australia/Sydney
    title: str | None
    liked_text: str | None
    disliked_text: str | None
    language: str | None
    guest_name: str | None
    guest_country: str | None
    traveller_type: str | None
    room_type: str | None
    nights: int | None
    checkin_date: str | None
    checkout_date: str | None
    hotel_reply: str | None
    helpful_votes: int
    content_hash: str

    def to_row(self) -> dict:
        return asdict(self)


# Booking guests often type these into the "disliked" box meaning "no complaints".
_TRIVIAL = re.compile(
    r"^\s*(n/?a|na|none|nothing|nil|no|non|-+|\.+|nope|nothing really|nothing at all|"
    r"nothing to (complain|dislike|report|say)( about)?|no complaints?|all good|"
    r"everything was (good|great|fine|perfect))\s*[.!]*\s*$",
    re.IGNORECASE,
)


def clean_text(value) -> str | None:
    if value is None:
        return None
    text = html.unescape(str(value)).replace("\r", "").strip()
    text = re.sub(r"[ \t]+", " ", text)
    return text or None


def is_trivial(text: str | None) -> bool:
    return text is None or bool(_TRIVIAL.match(text))


def _content_hash(*parts) -> str:
    joined = "␟".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def extract_review_list(payload: dict) -> dict:
    """Return the `reviewListFrontend` object or raise a descriptive error."""
    if not isinstance(payload, dict):
        raise SchemaError(f"Expected JSON object, got {type(payload).__name__}")
    if payload.get("errors") and not payload.get("data"):
        raise BookingError(f"GraphQL errors: {json.dumps(payload['errors'])[:300]}")
    node = (payload.get("data") or {}).get("reviewListFrontend")
    if node is None:
        raise SchemaError("Response has no data.reviewListFrontend")
    typename = node.get("__typename")
    if typename and typename != "ReviewListFrontendResult":
        raise BookingError(f"{typename}: {node.get('message')} (status {node.get('statusCode')})")
    if not isinstance(node.get("reviewCard"), list):
        raise SchemaError("reviewListFrontend.reviewCard missing or not a list")
    return node


def parse_review(card: dict, property_id: str) -> Review:
    try:
        review_id = card["reviewUrl"]
        score = float(card["reviewScore"])
        ts = int(card["reviewedDate"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SchemaError(f"Review card missing required field: {exc!r}") from exc
    if not review_id:
        raise SchemaError("Review card has empty reviewUrl (id)")
    if not 0 < score <= 10:
        raise SchemaError(f"Score out of range: {score}")

    text = card.get("textDetails") or {}
    guest = card.get("guestDetails") or {}
    booking = card.get("bookingDetails") or {}
    room = booking.get("roomType") or {}
    reply = card.get("partnerReply") or {}

    title = clean_text(text.get("title"))
    liked = clean_text(text.get("positiveText"))
    disliked = clean_text(text.get("negativeText"))
    hotel_reply = clean_text(reply.get("reply"))

    reviewed = datetime.fromtimestamp(ts, tz=timezone.utc)
    nights = booking.get("numNights")

    return Review(
        property_id=property_id,
        review_id=str(review_id),
        score=score,
        reviewed_at=reviewed.isoformat(),
        reviewed_date_local=reviewed.astimezone(LOCAL_TZ).date().isoformat(),
        title=title,
        liked_text=liked,
        disliked_text=disliked,
        language=text.get("lang"),
        guest_name=None if guest.get("anonymous") else clean_text(guest.get("username")),
        guest_country=guest.get("countryName"),
        traveller_type=guest.get("guestTypeTranslation"),
        room_type=clean_text(room.get("name")),
        nights=int(nights) if isinstance(nights, (int, float)) else None,
        checkin_date=booking.get("checkinDate"),
        checkout_date=booking.get("checkoutDate"),
        hotel_reply=hotel_reply,
        helpful_votes=int(card.get("helpfulVotesCount") or 0),
        # Hash of the mutable parts: lets us detect edited reviews / new hotel replies.
        content_hash=_content_hash(score, title, liked, disliked, hotel_reply),
    )


def parse_page(payload: dict, property_id: str) -> tuple[list[Review], int, list[str]]:
    """Parse one GraphQL page -> (reviews, total_count, per-card warnings).

    A single malformed card is skipped with a warning rather than failing the
    whole page; a malformed *page* raises.
    """
    node = extract_review_list(payload)
    reviews, warnings = [], []
    for i, card in enumerate(node["reviewCard"]):
        try:
            reviews.append(parse_review(card, property_id))
        except SchemaError as exc:
            warnings.append(f"card {i}: {exc}")
    total = node.get("reviewsCount")
    return reviews, int(total) if isinstance(total, (int, float)) else -1, warnings


def parse_property_summary(payload: dict) -> dict:
    """Booking's own category sub-scores (Staff, Cleanliness, ...) for context."""
    node = extract_review_list(payload)
    cats = []
    for s in node.get("ratingScores") or []:
        if isinstance(s, dict) and s.get("value") is not None:
            cats.append({
                "key": s.get("name"),
                "label": s.get("translation") or s.get("name"),
                "score": round(float(s["value"]), 2),
            })
    total = None
    for f in node.get("languageFilter") or []:
        if f.get("value") == "":
            total = f.get("count")
    return {"category_scores": cats, "total_reviews_on_booking": total}
