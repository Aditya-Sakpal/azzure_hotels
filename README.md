# Azzurro Hotels: Guest Review Insights

Collects public Booking.com guest reviews for four Azzurro properties and turns them into a dashboard for the operations team. The dashboard shows how this week compares with last week, which property is slipping, what guests complain about, and which negative reviews still need a reply.

**Live demo:** https://azzurro-review-insights.salesdesk-app.workers.dev
**Sample data:** [`data/reviews.db`](data/reviews.db) (SQLite) and [`web/public/data/dashboard.json`](web/public/data/dashboard.json). Together they hold 5,509 reviews collected on 25 Nov 2026.

| Property | Booking.com page |
|---|---|
| Olympic Hotel Paddington | `hotel/au/olympic-paddington` |
| Potts Point | `hotel/au/venus-potts-point-sydney` |
| Central Sydney | `hotel/au/venus-surry-hills` |
| Darling Harbour | `hotel/au/chateau-de-venus` |

---

## Quick start

Requirements: Python 3.11+, Node 18+, and **Google Chrome** installed.

```bash
# 1. Scraper
pip install -r requirements.txt
python -m playwright install chromium      # fallback browser

python -m scraper collect --export         # incremental update + regenerate dashboard data
python -m scraper collect --full           # re-walk every page (first run / weekly refresh)
python -m scraper collect -p potts-point   # one property only
python -m scraper status                   # what we have + last run per property
python -m scraper export                   # re-classify and rewrite web/public/data/dashboard.json

# 2. Dashboard
cd web
npm install
npm run dev                                # http://localhost:5173
npm run build                              # static build in web/dist

# 3. Deploy (Cloudflare Workers static assets)
npx wrangler deploy                        # from repo root; serves web/dist

# Tests
python -m pytest -q
```

A Chrome window opens during collection, which is expected (see below). Don't close it; it closes itself when the run finishes.

---

## Architecture

```
 Booking.com property page ──(real Chrome, Playwright)──► capture ReviewList GraphQL request
                                                                 │ replay newest-first, 25/page
                                                                 ▼
  scraper/parser.py   validate + normalise every review ──►  data/reviews.db (SQLite)
  scraper/db.py       upsert on (property, review_id)           ├─ reviews
                                                                ├─ property_snapshots (Booking score + category scores)
                                                                └─ scrape_runs (audit log of every run)
                                                                 │
  scraper/classify.py topics + sentiment ──► scraper/export.py ──► web/public/data/dashboard.json
                                                                 │
                                    web/ (React + Vite + Recharts, static) ──► Cloudflare Workers
```

| Path | Responsibility |
|---|---|
| `config/properties.json` | The four properties. To add a hotel, add a line here. |
| `scraper/booking_client.py` | Browser session, challenge handling, request capture, retries and backoff, debug dumps |
| `scraper/collector.py` | Per-property orchestration, incremental vs full mode, run logging, isolates failures between properties |
| `scraper/parser.py` | The only file that knows Booking's JSON shape. Validates and normalises reviews |
| `scraper/db.py` | SQLite schema, dedup and upsert, run log |
| `scraper/classify.py` | Topic lexicon and sentiment rules |
| `scraper/export.py` | Builds the dashboard JSON (written atomically) |
| `web/src/data.ts` | Pure analytics: weeks, trends, topic shares |
| `web/src/App.tsx` | Dashboard UI |

**Why this shape:** the dashboard is a static site that reads one JSON file. At this scale (thousands of reviews) all filtering runs instantly in the browser. Hosting is free, there is no server or database to keep running, and the demo can't go down because a backend is down. To update the data, run `collect --export` and then `wrangler deploy`. The next step would be to move storage to Cloudflare D1 and run the scraper on a schedule (see Next steps).

---

## How reviews are collected (and why this way)

Booking.com has no public review API. Here is what I tried, in order:

1. **Plain HTTP.** The old `reviewlist.html` endpoint now returns **404**. The property page returns **HTTP 202 with an AWS-WAF JavaScript challenge** instead of content. Dead end.
2. **Headless Playwright.** It gets past the page challenge, but Booking's GraphQL answers the review query with `ReviewsFrontendError: "Request not supported" (400)`. Headless browsers are detected.
3. **Real Chrome driven by Playwright (`channel="chrome"`, headed). This works.**

How the working method runs:

- It opens the public property page and clicks "Read all reviews", just as a guest would.
- The page then fires its own `ReviewList` GraphQL request. The scraper **captures that request**: the URL, the headers (including the CSRF token), the persisted-query hash and the hotel ID. Nothing Booking-specific is hard-coded, so if Booking changes the query hash or headers, the next run picks up the new values automatically.
- It replays the same request from inside the page (same cookies and session) with `sorter=NEWEST_FIRST`, paging 25 reviews at a time. This is far faster and more stable than parsing HTML, and each review comes back as structured JSON.

**Fields stored:** Booking's review ID, score (1–10), review date, title, liked text, disliked text, language, guest first name, country, traveller type, room type, nights, check-in and check-out dates, hotel reply, and helpful votes. Avatars are not stored. Each snapshot also records Booking's overall score and its category scores (Staff, Cleanliness and so on).

### Reliability and data quality

| Concern | How it's handled |
|---|---|
| **Duplicates** | Primary key `(property_id, review_id)` uses Booking's own stable review hash. Duplicates within a batch are skipped, and re-runs insert nothing twice (covered by tests). |
| **New reviews** | `collect` (incremental) pages newest-first and **stops at the first page made up entirely of reviews we already have**. A daily update takes a few seconds per property. |
| **Edited reviews and late hotel replies** | A content hash covers score, text and reply. If it changes, the row is updated and `updated_at` is set. `--full` re-walks everything and is meant for a weekly run. |
| **Temporary failures** | Each page fetch retries 4 times with exponential backoff and jitter. Opening a property retries with a fresh page. Requests are spaced 1.5–3.5 s apart. |
| **Layout or API changes** | Several fallback selectors are used to open the review list. The response is validated; Booking error objects and unexpected shapes raise a clear error rather than writing empty rows. One malformed review card is skipped with a warning, not the whole page. On failure, a screenshot and HTML dump go to `data/debug/` (git-ignored, since it may contain session tokens). |
| **Sort-order assumption** | Each page is checked to be newest-first. If Booking ignores the sort, early stopping is turned off and the run walks every page. |
| **One property failing** | Each property is isolated. The run continues, and the failure is recorded in `scrape_runs`. The dashboard shows a warning banner for any property whose last run failed. |
| **Runaway runs** | Hard cap of 200 pages (5,000 reviews) per property. |
| **Visibility** | Every run is logged (pages, fetched, new, updated, warnings, error). `python -m scraper status` summarises it, and `collect` exits non-zero if every property failed, so a scheduler can raise an alert. |

### Limitations of the collection method

- **It needs a real Chrome window.** Headless mode is currently blocked. On a Linux server, run it under `xvfb-run`. It should run from a normal (residential or office) IP; datacenter IPs are more likely to be challenged.
- **Booking only exposes recent reviews.** Roughly the last 36 months. For example, Paddington shows "128 reviews", but the full list endpoint returns 73, which looks like a mix of older reviews and score-only reviews that Booking doesn't list. We store what Booking publicly lists.
- **It relies on undocumented internals** (a GraphQL persisted query). Capturing the request automatically protects against hash and header changes. A redesign of the review drawer would mean updating the selectors in `booking_client.py`. Failures are loud, not silent.
- **Terms of Service.** Booking's terms restrict automated access. This collects only public data at low volume with polite delays, for internal analysis of Azzurro's own properties. For production use, I'd also look at Booking's Connectivity/Partner APIs available to the property owner, or a licensed review aggregator.

---

## Insights methodology

**Sentiment** uses the guest's own score: **positive ≥ 8**, **neutral 6–7.9**, **negative ≤ 5.9** (configurable in `scraper/config.py`). A guest's own number is more reliable than any text model.

**Topics** come from a curated keyword and regex lexicon in `scraper/classify.py`. Nine topics are tracked: Cleanliness, Check-in & access, Staff & service, Noise, Facilities & amenities, Location, Room condition & comfort, **Bathroom & shower** (added because most of these properties have shared bathrooms and it comes up constantly), and Value for money.

The key trick is that Booking already splits every review into "liked" and "disliked" text:

- a topic in the **disliked** text counts as a **complaint**
- a topic in the **liked** text counts as **praise**
- reviews with only a title are attributed by score

This gives each topic a polarity without a sentiment model. "Nothing", "n/a", "no complaints" and similar entries in the disliked box are ignored.

**Insights shown** include, for example: *"53% of negative reviews in the last 4 weeks mentioned room condition issues (10 of 26), vs 53% in the previous period."* Others are rising issues (a share up 10+ points with at least 3 mentions), the most common complaints, the most praised topic, the lowest- and highest-rated property, and negative reviews with no hotel reply. Every percentage shows its base count, and small samples are flagged.

**Why rules and not an LLM?** Rules are transparent (staff can see why a review was tagged), deterministic, free, offline, unit-tested, and tuned by editing one file. The trade-offs:

- **Keyword limits:** misses paraphrase ("I could hear everything next door" is caught, "paper walls" is not), and context is shallow (a "small" room could be fine in a pod hostel).
- **English only.** Booking's `lang` is the guest's language, not the text's, so an English-stopword check is used as a fallback. Truly non-English reviews (about 5–10%) get no topics and are labelled so in the feed.
- **Multi-label.** One review can count towards several topics, so the shares don't add up to 100%.
- **Natural upgrade:** send only the disliked text to an LLM (such as Claude) with the same 9-topic schema, cache the result per `content_hash`, and keep the rules as a fallback and a check.

---

## Dashboard (for non-technical users)

- **This week:** average rating (Mon–Sun, Sydney time) vs last week, with a direction arrow, the 4-week average for context, and a small-sample warning. Also review count, negative count, and negative reviews still awaiting a reply.
- **Key insights:** plain-English sentences. Topic names are links that open the matching reviews.
- **Property breakdown:** average, change, number of reviews, % positive and negative, top complaint, and Booking's official score. Click a row to filter the whole dashboard to that property.
- **Trends:** average rating over time, and positive/neutral/negative counts per week (or per month for long ranges).
- **Topics:** complaints vs praise per topic, plus the share of negative reviews mentioning each, compared with the previous period.
- **Review feed:** search, filter by sentiment, topic or "no hotel reply", sort by newest, lowest or highest, with liked and disliked text, topic chips and the hotel reply.
- **Filters:** property, plus period presets (this week, last week, 4 weeks, 3 months, 12 months, all time, custom dates). "Previous period" always means the same length of time immediately before.

## Assumptions

- Weeks run Monday to Sunday in **Australia/Sydney** time. Review dates use Booking's review date, not the stay date.
- The weekly figures can rest on very few reviews (Paddington gets about 4 a week), so the 4-week average is shown alongside.

## Next steps

1. Schedule `collect --export` daily and `--full` weekly (Task Scheduler or cron on an always-on office machine), and alert when the exit code is non-zero.
2. Move storage to Cloudflare D1 so the dashboard updates without a redeploy.
3. LLM topic classification for the disliked text, with rules as a fallback and a small hand-labelled set to measure precision and recall.
4. Weekly email digest to managers: score change, top issue per property, unanswered negatives.

## Security

No credentials, cookies or API keys are stored or committed. Session tokens exist only in the browser's memory during a run. Debug dumps are git-ignored.
