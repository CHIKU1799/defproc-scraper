# defproc-scraper

A Python web scraper for the **Ministry of Defence eProcurement Portal** ([defproc.gov.in](https://defproc.gov.in)) — built on NIC's GePNIC Java/JSP platform.

Scrapes all active tender listings, paginates through results, and optionally enriches each tender with full detail-page metadata.

---

## Project Structure

```
defproc-scraper/
├── defproc_scraper.py        # Main scraper — listing pages + pagination
├── detail_scraper.py         # Detail page scraper — per-tender enrichment
├── run_daily.py              # Daily runner — scrape + diff + new tender alerts
├── requirements.txt          # Python dependencies
│
├── explore_site.py           # Phase 1: inspect the search form + fields
├── explore_active_tenders.py # Phase 1: inspect listing pages for captcha
├── analyze_captcha.py        # Phase 1: network analysis + captcha discovery
├── inspect_results.py        # Phase 1: results table + pagination structure
│
├── output/                   # CSV and JSON output (gitignored)
├── logs/                     # Run logs (gitignored)
└── screenshots/              # Debug screenshots and HTML saves (gitignored)
```

---

## How the CAPTCHA is Bypassed

This is the most important part of the scraper's design.

### The Problem
The portal has a CAPTCHA on:
- The **Advanced Search** form (`/app?page=FrontEndAdvancedSearch`)
- The **Active Tenders** listing (`/app?page=FrontEndLatestActiveTenders`)
- The **Tenders by Closing Date** page

Every search/listing entry point requires solving a 6-character alphanumeric CAPTCHA image (embedded as a base64 PNG in the page HTML) before results are shown.

### The Discovery
During Phase 1 exploration (`analyze_captcha.py`), we tested whether the **results endpoint** itself requires a CAPTCHA — and it does not.

The portal uses NIC's Tapestry framework with a `DirectLink` component pattern. The results page URL:

```
/nicgep/app?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct
```

...is a **stateful server-side component** that renders the current session's result set. If you navigate directly to this URL after establishing any valid session cookie (`JSESSIONID`), the server returns the last search result — which defaults to **all active tenders** when no prior search has been performed.

### The Strategy
```
1. Visit any page on the portal         → server creates JSESSIONID cookie
2. Navigate directly to the results URL → server returns all active tenders
3. No search form submitted             → no CAPTCHA triggered
4. Parse 20 results per page            → click Next (>) to paginate
```

This works because:
- The session is valid as soon as any page is loaded
- The default result set (no filters) returns all tenders
- The results endpoint doesn't validate that a search was submitted
- Pagination links (`#linkFwd`, `#linkPage_N`) are plain GET requests, no CAPTCHA

### No OCR needed
We did install `tesseract` and `pytesseract` during exploration as a fallback, but the direct URL approach made them unnecessary entirely.

---

## Libraries & What Each Does

### `playwright` (async API)
**What it does:** Controls a real Chromium browser — loads pages, executes JavaScript, clicks buttons, and navigates between pages.

**Why not `requests` or `aiohttp`?**
The portal is a JSP/Tapestry app that renders content via JavaScript and maintains server-side session state. Plain HTTP requests don't execute JS and can't maintain the stateful session the way a real browser can. Playwright handles all of this transparently.

**Key usage:**
- `page.goto()` — navigate to a URL
- `page.evaluate()` — run JavaScript inside the page to extract data
- `page.click()` — click the Next button for pagination
- `context.cookies()` — access the JSESSIONID session cookie
- `page.expect_navigation()` — wait for page load after a click

---

### `playwright-stealth`
**What it does:** Patches the Playwright browser to hide automation fingerprints that websites use to detect bots.

**What it hides:**
- `navigator.webdriver = true` flag (the most common bot detector)
- Chrome runtime automation markers
- WebGL renderer strings that reveal headless environments
- Navigator plugins list (headless browsers have none)
- Hardware concurrency / platform inconsistencies

**How it's used:**
```python
from playwright_stealth import Stealth
await Stealth().apply_stealth_async(page)
```
Called once after the page object is created. Injects JavaScript patches before any page loads.

---

### `fake-useragent`
**What it does:** Generates realistic, randomised browser User-Agent strings from a live database of real browser signatures.

**Why:** If every request uses the same User-Agent (especially Playwright's default headless one), it's a strong bot signal. Rotating to real Chrome/Firefox UA strings makes traffic look like normal users.

**How it's used:**
```python
from fake_useragent import UserAgent
ua = UserAgent()
# On each page: rotate the header
await context.set_extra_http_headers({"User-Agent": ua.chrome})
```

---

### `asyncio`
**What it does:** Python's built-in async I/O framework. Playwright's async API runs inside an asyncio event loop.

**Why async:** Allows non-blocking waits (network delays, page loads, random delays between requests) without freezing the entire program. Makes it easy to add concurrent scraping in the future.

---

### `pandas` *(available, not yet used in core)*
**What it does:** Data manipulation library — useful for post-processing the scraped CSV (filtering, deduplication, analysis).

**Planned use:** Reading `defproc_tenders.csv`, deduplicating on `tender_id`, merging listing + detail data, exporting filtered views.

---

### `csv` / `json` *(standard library)*
**What they do:** Write scraped data to disk.
- `csv.DictWriter` — appends rows to `output/defproc_tenders.csv` (header written only on first run)
- `json.dump` — writes full structured data to `output/defproc_tenders.json`

---

### `logging` *(standard library)*
**What it does:** Writes timestamped log messages to both the terminal and `logs/scraper_log.txt` simultaneously.

---

## File-by-File Explanation

### `defproc_scraper.py` — Main Scraper

The core of the project. Does three things:

1. **Session establishment** — visits the Active Tenders page to get a `JSESSIONID` cookie
2. **Direct results navigation** — goes straight to the results endpoint (bypassing CAPTCHA)
3. **Pagination loop** — extracts 20 tenders per page, clicks `#linkFwd` (Next), repeats until no Next button exists

**Key functions:**
- `scrape()` — async main function, orchestrates the full scrape
- `extract_tenders_from_page()` — converts raw row data into structured dicts
- `parse_title_cell()` — parses the combined `[Title][RefNo][TenderID]` cell using regex
- `random_delay()` — sleeps 2.5–5s between pages (respectful scraping)
- `click_with_retry()` — clicks Next with 3 retries + exponential backoff
- `_write_outputs()` — saves CSV (append) and JSON (overwrite)

**CLI usage:**
```bash
python defproc_scraper.py                    # Scrape all pages
python defproc_scraper.py --pages 10         # First 10 pages only
python defproc_scraper.py --org "MES"        # Filter by organisation
python defproc_scraper.py --output my_dir/   # Custom output directory
```

---

### `detail_scraper.py` — Detail Page Enricher

Reads `output/defproc_tenders.json`, visits each tender's `detail_url`, and extracts additional fields not available in the listing view.

**What it adds:** tender value (₹), EMD amount, tender fee, bid submission dates, document download dates, location, pincode, tender type, category, payment mode, and document download URLs.

**How extraction works:**
Runs a JavaScript snippet (`DETAIL_EXTRACT_JS`) inside the page that:
- Scans all `<tr>` elements for label-value pairs (2-cell rows)
- Builds a `kvPairs` dict: `{"Tender Value in ₹": "3,06,00,000", ...}`
- Collects all document download links

Then maps known keys to structured fields using a `kv_get()` helper that tries multiple key name variants (the portal isn't consistent).

**CLI usage:**
```bash
python detail_scraper.py                          # Enrich all tenders
python detail_scraper.py --limit 50              # First 50 only
python detail_scraper.py --input output/my.json  # Custom input file
```

---

### `run_daily.py` — Daily Runner + Diff

Designed to be run on a schedule (cron). Does:

1. Loads `output/previous_run.json` to get known tender IDs
2. Runs the full scraper
3. Compares new results against previous — finds tenders not seen before
4. Prints a `NEW TENDERS FOUND` summary
5. Saves current results as the new snapshot for next run
6. Optionally runs detail scraper on new tenders only (`--detail` flag)

**CLI usage:**
```bash
python run_daily.py                  # Full run, all pages
python run_daily.py --pages 20       # Limit to 20 pages
python run_daily.py --detail         # Also scrape detail for new tenders
```

**Cron example (runs every day at 7am):**
```bash
0 7 * * * cd ~/defproc-scraper && python3 run_daily.py >> logs/cron.log 2>&1
```

---

### `explore_site.py` — Phase 1: Form Inspector

One-time exploration script. Visits the Advanced Search page, takes a screenshot, and prints every form field (inputs, selects, hidden fields) with names, IDs, and values. Used to understand the form structure before building the scraper.

---

### `explore_active_tenders.py` — Phase 1: Listing Page Analyzer

Visits all three listing pages (Active Tenders, Tenders by Closing Date, Tenders by Location), checks each for CAPTCHA presence, inspects table structure, and probes whether any page can be accessed without CAPTCHA. This is where we discovered the CAPTCHA bypass.

---

### `analyze_captcha.py` — Phase 1: Network + CAPTCHA Analysis

Captures all network requests while loading a page, extracts and saves the base64-embedded CAPTCHA images, and tests whether the direct results URL returns data without authentication. Confirmed the bypass works.

---

### `inspect_results.py` — Phase 1: Results Structure Inspector

Loads the direct results page and inspects: row count, interactive elements, pagination link IDs (`#linkFwd`, `#linkLast`, `#linkPage_N`), form hidden state fields, and the exact structure of the tender data table. This script produced the final understanding used to build the main scraper.

---

## Data Output

| File | Format | Contents |
|------|--------|----------|
| `output/defproc_tenders.csv` | CSV | All listing fields, append mode |
| `output/defproc_tenders.json` | JSON | Same data, overwritten each run |
| `output/defproc_tenders_detailed.json` | JSON | Enriched with detail-page fields |
| `output/previous_run.json` | JSON | Snapshot for daily diff |
| `logs/scraper_log.txt` | Text | Run summaries with stats |
| `logs/daily_log.txt` | Text | Daily diff logs |

---

## Installation

```bash
# Clone the repo
git clone https://github.com/CHIKU1799/defproc-scraper.git
cd defproc-scraper

# Install Python dependencies
pip install -r requirements.txt

# Install Chromium browser for Playwright
playwright install chromium
```

---

## Ethical & Legal Notes

- This scraper targets **publicly available government procurement data** on a portal maintained by NIC for the Ministry of Defence
- All scraped data is visible without login to any member of the public
- Delays of **2.5–5 seconds** between requests are enforced to avoid server load
- No authentication is bypassed — only public pages are accessed
- The CAPTCHA bypass uses a legitimate URL that the server exposes publicly — no cracking or circumvention of security controls
