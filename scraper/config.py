"""Paths, property list and tunable constants in one place."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "reviews.db"
DEBUG_DIR = DATA_DIR / "debug"
EXPORT_PATH = ROOT / "web" / "public" / "data" / "dashboard.json"

# All reporting (weeks, "today") is done in the hotels' local time.
LOCAL_TZ = ZoneInfo("Australia/Sydney")

# Scraping behaviour
PAGE_SIZE = 25                 # reviews per GraphQL page (what Booking's own UI requests)
MAX_PAGES_PER_PROPERTY = 80    # hard safety cap (= 2,000 reviews)
REQUEST_DELAY_SECONDS = (1.5, 3.5)  # polite random delay between pages
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 4

# Sentiment buckets on Booking's 1-10 scale
POSITIVE_MIN_SCORE = 8.0
NEGATIVE_MAX_SCORE = 5.9


@dataclass(frozen=True)
class Property:
    id: str
    name: str
    short_name: str
    url: str

    @property
    def english_url(self) -> str:
        """Force the en-gb page so review text/labels come back in English."""
        if self.url.endswith(".en-gb.html"):
            return self.url
        return self.url.replace(".html", ".en-gb.html")


def load_properties() -> list[Property]:
    raw = json.loads((CONFIG_DIR / "properties.json").read_text(encoding="utf-8"))
    return [Property(**p) for p in raw]
