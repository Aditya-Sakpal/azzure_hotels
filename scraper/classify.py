"""Rule-based topic + sentiment classification.

Method
------
Booking reviews arrive already split into "liked" and "disliked" text. That gives
us polarity for free and far more reliably than a sentiment model would on short
hotel-review fragments. So:

  * A topic found in the **disliked** text  -> a *complaint* about that topic.
  * A topic found in the **liked** text     -> *praise* for that topic.
  * Title-only reviews are attributed by score (<= 5.9 complaint, >= 8 praise).
  * Review-level sentiment comes from the 1-10 score
    (>= 8 positive, <= 5.9 negative, otherwise neutral).

Topic detection is a curated keyword/regex lexicon (below), matched on word
boundaries, case-insensitive, multi-label (one review can hit several topics).

Why rules rather than an LLM? Transparent (staff can see *why* a review was
tagged), deterministic, free, offline and easily tuned by editing this file.
The lexicon is English-only; non-English reviews get no topics (flagged in the UI).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import NEGATIVE_MAX_SCORE, POSITIVE_MIN_SCORE
from .parser import is_trivial


@dataclass(frozen=True)
class Topic:
    key: str
    label: str
    patterns: tuple[str, ...]


TOPICS: tuple[Topic, ...] = (
    Topic("cleanliness", "Cleanliness", (
        r"clean\w*", r"unclean", r"dirt\w*", r"dust\w*", r"filth\w*", r"grim[ey]\w*", r"stain\w*",
        r"smell\w*", r"stink\w*", r"odou?r\w*", r"musty", r"mou?ld\w*", r"hairs? (in|on|all over|everywhere)", r"(found|pubic|long) hairs?", r"hygien\w*",
        r"cockroach\w*", r"roach(es)?", r"bed ?bugs?", r"bugs?", r"insects?", r"ants", r"pests?",
        r"spotless", r"tidy", r"untidy", r"rubbish", r"garbage", r"trash", r"gross", r"disgusting",
        r"sticky", r"vomit", r"blood",
    )),
    Topic("checkin", "Check-in & access", (
        r"check[- ]?in\w*", r"check[- ]?out\w*", r"arriv\w*", r"keys?", r"key ?card", r"codes?",
        r"pin", r"lock ?box", r"self[- ]check\w*", r"reception hours", r"after hours",
        r"instructions?", r"waited", r"waiting", r"queue", r"booking (was )?cancel\w*",
        r"reservation", r"overbook\w*", r"locked out", r"luggage", r"bags? storage", r"access codes?",
    )),
    Topic("staff", "Staff & service", (
        r"staff", r"receptionists?", r"reception", r"front desk", r"manager", r"management",
        r"owner", r"hosts?", r"employees?", r"team", r"rude\w*", r"unhelpful", r"helpful",
        r"friendly", r"unfriendly", r"polite", r"impolite", r"welcoming", r"attitude", r"service",
        r"customer service", r"accommodating", r"responsive", r"unprofessional", r"professional",
    )),
    Topic("noise", "Noise", (
        r"nois\w*", r"loud\w*", r"quiet\w*", r"sound ?proof\w*", r"thin walls?",
        r"hear (everything|people|the)", r"could hear", r"traffic", r"music", r"part(y|ies)",
        r"construction", r"drilling", r"ear ?plugs?", r"shouting", r"yelling", r"snor\w*",
        r"woke (me|us) up", r"kept (me|us) awake",
    )),
    Topic("facilities", "Facilities & amenities", (
        r"facilit\w*", r"kitchen\w*", r"lifts?", r"elevators?", r"stairs?", r"steps",
        r"laundry", r"washing machine", r"dryer", r"wi-?fi", r"internet", r"parking", r"car ?park",
        r"pool", r"gym", r"common (area|room)", r"lounge", r"balcony", r"rooftop", r"bbq",
        r"breakfast", r"vending", r"microwave", r"kettle", r"amenit\w*",
    )),
    Topic("location", "Location", (
        r"locat\w*", r"walking distance", r"close to", r"near(by)?", r"stations?", r"transport",
        r"bus(es)?", r"trains?", r"light rail", r"cbd", r"city", r"central", r"neighbou?rhood",
        r"suburb", r"(the|great|nice|safe|good|lovely|central) area", r"shops", r"restaurants", r"cafes?", r"stadium", r"beach",
        r"convenien\w*", r"walkable",
    )),
    Topic("room", "Room condition & comfort", (
        r"rooms?", r"beds?", r"mattress\w*", r"pillows?", r"sheets?", r"linen", r"blankets?",
        r"duvet", r"towels?", r"air ?con\w*", r"aircon", r"a/?c", r"heat(er|ing)", r"fan",
        r"windows?", r"curtains?", r"blinds", r"tv", r"fridge", r"furniture", r"carpet",
        r"small", r"tiny", r"cramped", r"spacious", r"comfort\w*", r"uncomfortable", r"broken",
        r"worn", r"old", r"outdated", r"dated", r"run[- ]down", r"renovat\w*", r"maintenance",
        r"repair\w*", r"lights?", r"lamp", r"socket", r"power point",
    )),
    Topic("bathroom", "Bathroom & shower", (
        r"bathrooms?", r"showers?", r"toilets?", r"ensuite", r"en-suite", r"water pressure",
        r"hot water", r"cold water", r"drain\w*", r"sink", r"basin", r"shared bathroom",
        r"bath", r"tap", r"plumbing",
    )),
    Topic("value", "Value for money", (
        r"price\w*", r"pric(ey|y)", r"value", r"money", r"expensive", r"cheap\w*", r"overpriced",
        r"worth\w*", r"costs?", r"paid", r"pay(ing)?", r"fees?", r"charged", r"charges?", r"surcharge", r"refund\w*",
        r"deposit", r"affordable", r"budget", r"rip[- ]?off", r"bargain", r"\$\d+",
    )),
)

_COMPILED = {t.key: re.compile(r"\b(?:" + "|".join(t.patterns) + r")\b", re.IGNORECASE)
             for t in TOPICS}
TOPIC_LABELS = {t.key: t.label for t in TOPICS}


def topics_in(text: str | None) -> list[str]:
    if not text or is_trivial(text):
        return []
    return [key for key, rx in _COMPILED.items() if rx.search(text)]


_EN_STOPWORDS = re.compile(r"\b(the|and|was|were|is|it|to|of|very|but|not|room|staff|we|i|good|great)\b", re.I)


def looks_english(text: str) -> bool:
    """Booking's `lang` is the guest's language, not always the text's. Many
    non-English guests write in English, so fall back to a stopword check."""
    words = re.findall(r"[^\W\d_]+", text)
    if len(words) < 3:
        return False
    return len(_EN_STOPWORDS.findall(text)) / len(words) >= 0.15


def sentiment_for(score: float) -> str:
    if score >= POSITIVE_MIN_SCORE:
        return "positive"
    if score <= NEGATIVE_MAX_SCORE:
        return "negative"
    return "neutral"


def classify(review: dict) -> dict:
    """Return {sentiment, complaints: [topic], praise: [topic], classifiable: bool}."""
    score = float(review["score"])
    sentiment = sentiment_for(score)
    lang = (review.get("language") or "en").lower()
    classifiable = lang.startswith("en") or looks_english(
        " ".join(filter(None, [review.get("title"), review.get("liked_text"), review.get("disliked_text")])))

    complaints: list[str] = []
    praise: list[str] = []
    if classifiable:
        complaints = topics_in(review.get("disliked_text"))
        praise = topics_in(review.get("liked_text"))
        title_topics = topics_in(review.get("title"))
        if sentiment == "negative":
            complaints = sorted(set(complaints) | set(title_topics), key=list(_COMPILED).index)
        elif sentiment == "positive":
            praise = sorted(set(praise) | set(title_topics), key=list(_COMPILED).index)

    return {
        "sentiment": sentiment,
        "complaints": complaints,
        "praise": praise,
        "classifiable": classifiable,
        "has_complaint_text": not is_trivial(review.get("disliked_text")),
    }
