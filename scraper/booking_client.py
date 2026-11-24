"""Browser-backed access to Booking.com's review data.

Why a real browser?
  * Plain HTTP requests get an AWS-WAF JavaScript challenge (HTTP 202) and the old
    `reviewlist.html` endpoint now returns 404.
  * Headless Chromium passes the page challenge but Booking's GraphQL answers the
    review query with "Request not supported" — so we drive a real (headed) Chrome.

How it works
  1. Open the public property page and let the browser clear the challenge.
  2. Open the "Read all reviews" drawer. The page itself fires a `ReviewList`
     GraphQL request; we *capture* that request (URL, headers incl. CSRF token,
     persisted-query hash, hotel id). Nothing is hard-coded, so when Booking
     rotates the query hash or headers we automatically pick up the new ones.
  3. Re-issue that same request from inside the page (same cookies/session) with
     `sorter=NEWEST_FIRST` and increasing `skip`, 25 reviews per page.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import random
from dataclasses import dataclass

from playwright.async_api import Browser, BrowserContext, Page, Request, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from .config import (BACKOFF_BASE_SECONDS, DEBUG_DIR, MAX_RETRIES, PAGE_SIZE,
                     REQUEST_DELAY_SECONDS, Property)
from .parser import BookingError, SchemaError, extract_review_list

log = logging.getLogger(__name__)

# Headers the browser sets itself / that fetch() refuses to set.
_DROP_HEADERS = {"cookie", "content-length", "host", "user-agent", "referer", "origin",
                 "accept-encoding", "connection"}

# Ordered list of ways to open the full review list. If Booking renames one,
# the others still get a chance before we give up.
_OPEN_REVIEWS_SELECTORS = [
    '[data-testid="fr-read-all-reviews"]',
    '[data-testid="review-score-read-all"]',
    'button:has-text("Read all reviews")',
    'a:has-text("Read all reviews")',
    '#reviews-tab-trigger',
    'a[href="#tab-reviews"]',
]
_PAGE_READY_SELECTORS = ", ".join([
    '[data-testid="fr-read-all-reviews"]',
    '[data-testid="review-score-component"]',
    '#hp_hotel_name',
    'h2.pp-header__title',
])


class CollectionError(Exception):
    """Could not collect reviews for a property (after retries)."""


@dataclass
class QueryTemplate:
    url: str
    headers: dict
    body: dict

    @property
    def hotel_id(self) -> int | None:
        return self.body.get("variables", {}).get("input", {}).get("hotelId")

    @property
    def hotel_score(self) -> float | None:
        return self.body.get("variables", {}).get("input", {}).get("hotelScore")

    def for_page(self, skip: int, limit: int = PAGE_SIZE, sorter: str = "NEWEST_FIRST") -> dict:
        body = copy.deepcopy(self.body)
        inp = body["variables"]["input"]
        inp.update({"skip": skip, "limit": limit, "sorter": sorter})
        inp["filters"] = {"text": ""}          # no topic/language filter: we want everything
        inp.pop("upsortReviewUrl", None)
        return body


class BookingClient:
    def __init__(self, headless: bool = False, channel: str | None = "chrome", debug: bool = False):
        self.headless = headless
        self.channel = channel
        self.debug = debug
        self._pw = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None

    async def __aenter__(self) -> "BookingClient":
        self._pw = await async_playwright().start()
        args = ["--disable-blink-features=AutomationControlled"]
        if not self.headless:
            args.append("--window-position=-2400,0")   # keep the window out of the way
        try:
            self.browser = await self._pw.chromium.launch(
                channel=self.channel, headless=self.headless, args=args)
        except PlaywrightError as exc:
            log.warning("Could not launch channel=%s (%s); falling back to bundled Chromium",
                        self.channel, str(exc).splitlines()[0])
            self.browser = await self._pw.chromium.launch(headless=self.headless, args=args)
        self.context = await self.browser.new_context(
            locale="en-GB", timezone_id="Australia/Sydney", viewport={"width": 1366, "height": 900})
        return self

    async def __aexit__(self, *exc):
        if self.browser:
            await self.browser.close()
        if self._pw:
            await self._pw.stop()

    # ------------------------------------------------------------------ session
    async def open_property(self, prop: Property) -> "PropertySession":
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            page = await self.context.new_page()
            try:
                template = await self._capture_template(page, prop)
                return PropertySession(self, prop, page, template)
            except (PlaywrightTimeout, PlaywrightError, CollectionError) as exc:
                last_exc = exc
                await self._dump_debug(page, f"{prop.id}-open-{attempt}")
                await page.close()
                wait = BACKOFF_BASE_SECONDS * 2 ** (attempt - 1) + random.uniform(0, 2)
                log.warning("[%s] open attempt %d/%d failed: %s — retrying in %.0fs",
                            prop.id, attempt, MAX_RETRIES, str(exc).splitlines()[0], wait)
                await asyncio.sleep(wait)
        raise CollectionError(f"Could not open review list for {prop.id}: {last_exc}")

    async def _capture_template(self, page: Page, prop: Property) -> QueryTemplate:
        captured: list[Request] = []

        def on_request(req: Request):
            if "/dml/graphql" in req.url and '"ReviewList"' in (req.post_data or ""):
                captured.append(req)

        page.on("request", on_request)
        log.info("[%s] loading %s", prop.id, prop.english_url)
        await page.goto(prop.english_url, wait_until="domcontentloaded", timeout=60_000)
        # The WAF challenge page reloads itself; wait for real property content.
        await page.wait_for_selector(_PAGE_READY_SELECTORS, timeout=45_000)
        await page.wait_for_timeout(2_000)

        for selector in _OPEN_REVIEWS_SELECTORS:
            if captured:
                break
            loc = page.locator(selector)
            try:
                if not await loc.count():
                    continue
                target = loc.last if "has-text" in selector else loc.first
                await target.scroll_into_view_if_needed(timeout=5_000)
                await target.click(timeout=5_000)
            except (PlaywrightTimeout, PlaywrightError):
                continue
            for _ in range(10):           # up to ~5 s for the XHR to fire
                if captured:
                    break
                await page.wait_for_timeout(500)

        if not captured:
            raise CollectionError("Review list request never fired — page layout may have changed")

        req = captured[-1]
        headers = {k: v for k, v in (await req.all_headers()).items()
                   if not k.startswith(":") and k.lower() not in _DROP_HEADERS}
        try:
            body = json.loads(req.post_data)
            assert "input" in body["variables"]
        except (TypeError, ValueError, KeyError, AssertionError) as exc:
            raise CollectionError(f"Unexpected ReviewList request shape: {exc!r}") from exc
        t = QueryTemplate(url=req.url, headers=headers, body=body)
        log.info("[%s] captured ReviewList query (hotelId=%s, score=%s)", prop.id, t.hotel_id, t.hotel_score)
        return t

    async def _dump_debug(self, page: Page, name: str):
        """Save a screenshot + HTML of failures for diagnosis (git-ignored)."""
        try:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(DEBUG_DIR / f"{name}.png"), full_page=False)
            (DEBUG_DIR / f"{name}.html").write_text(await page.content(), encoding="utf-8")
        except Exception:  # debugging must never mask the real error
            pass


class PropertySession:
    def __init__(self, client: BookingClient, prop: Property, page: Page, template: QueryTemplate):
        self.client = client
        self.prop = prop
        self.page = page
        self.template = template

    async def fetch_page(self, skip: int) -> dict:
        """Fetch one page of newest-first reviews, with retry + exponential backoff."""
        body = self.template.for_page(skip)
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                status, text = await self.page.evaluate(
                    """async ([url, body, headers]) => {
                        const r = await fetch(url, {method: 'POST', headers, body: JSON.stringify(body),
                                                    credentials: 'include'});
                        return [r.status, await r.text()];
                    }""",
                    [self.template.url, body, self.template.headers],
                )
                if status in (429, 500, 502, 503, 504) or status >= 400:
                    raise BookingError(f"HTTP {status}")
                payload = json.loads(text)
                extract_review_list(payload)        # validates shape / raises on API error
                return payload
            except (BookingError, ValueError, PlaywrightError) as exc:
                last_exc = exc
                wait = BACKOFF_BASE_SECONDS * 2 ** (attempt - 1) + random.uniform(0, 2)
                log.warning("[%s] page skip=%d attempt %d/%d failed: %s — retrying in %.0fs",
                            self.prop.id, skip, attempt, MAX_RETRIES, exc, wait)
                await asyncio.sleep(wait)
            except SchemaError:
                await self.client._dump_debug(self.page, f"{self.prop.id}-schema-skip{skip}")
                raise
        await self.client._dump_debug(self.page, f"{self.prop.id}-fetch-skip{skip}")
        raise CollectionError(f"Giving up on {self.prop.id} skip={skip}: {last_exc}")

    async def polite_pause(self):
        await asyncio.sleep(random.uniform(*REQUEST_DELAY_SECONDS))

    async def close(self):
        await self.page.close()
