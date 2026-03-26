"""
defproc.gov.in eProcurement Portal Scraper
Ministry of Defence - NIC GePNIC platform

Strategy:
  - Navigate directly to the Advanced Search Results page (no CAPTCHA needed)
  - Parse 20 tenders per page using Playwright
  - Click the Next (>) button to paginate
  - Save output to CSV and JSON

Usage:
  python defproc_scraper.py [--pages N] [--org "MES"] [--output-dir output/]
"""
from __future__ import annotations
import asyncio
import csv
import json
import logging
import random
import re
import time
import argparse
from datetime import datetime
from pathlib import Path

from fake_useragent import UserAgent
from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError
from playwright_stealth import Stealth

# ─── Paths ─────────────────────────────────────────────────────────────────────
OUTPUT_DIR   = Path("output")
LOG_DIR      = Path("logs")
SCREENSHOTS  = Path("screenshots")
OUTPUT_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
SCREENSHOTS.mkdir(exist_ok=True)

# ─── Constants ──────────────────────────────────────────────────────────────────
BASE_URL    = "https://defproc.gov.in/nicgep/app"
RESULTS_URL = f"{BASE_URL}?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct"

DELAY_MIN  = 2.5   # seconds
DELAY_MAX  = 5.0
MAX_RETRY  = 3

CSV_FIELDS = [
    "tender_id", "ref_number", "title", "organisation",
    "published_date", "closing_date", "opening_date",
    "status", "detail_url", "scraped_at", "page_num",
]

# ─── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "scraper_log.txt", encoding="utf-8"),
    ],
)
log = logging.getLogger("defproc")


# ─── Helpers ────────────────────────────────────────────────────────────────────
def parse_title_cell(raw: str) -> dict:
    """
    Parse the combined Title/RefNo/TenderID cell.
    Format: [Title text][Ref number][YEAR_ORG_SEQNO_REV]

    Returns dict with 'title', 'ref_number', 'tender_id'
    """
    parts = re.findall(r"\[([^\[\]]+)\]", raw)
    tender_id  = ""
    ref_number = ""
    title      = ""

    if parts:
        # Last bracket that matches the NIC tender ID format: YEAR_ORG_NUM_REV
        for i in range(len(parts) - 1, -1, -1):
            if re.match(r"\d{4}_[A-Z_]+_\d+_\d+", parts[i]):
                tender_id  = parts[i]
                ref_number = parts[i - 1] if i > 0 else ""
                title      = parts[i - 2] if i > 1 else (parts[0] if parts else "")
                break
        if not tender_id:
            title = parts[0] if parts else raw
    else:
        title = raw.strip()

    return {"title": title.strip(), "ref_number": ref_number.strip(), "tender_id": tender_id.strip()}


async def random_delay():
    delay = random.uniform(DELAY_MIN, DELAY_MAX)
    await asyncio.sleep(delay)


async def get_page_with_retry(page, url: str, retries: int = MAX_RETRY):
    """Navigate to a URL with exponential backoff on failure."""
    for attempt in range(1, retries + 1):
        try:
            resp = await page.goto(url, wait_until="networkidle", timeout=60_000)
            return resp
        except (PWTimeoutError, Exception) as e:
            if attempt == retries:
                raise
            wait = 2 ** attempt + random.uniform(0, 1)
            log.warning(f"Attempt {attempt} failed ({e}), retrying in {wait:.1f}s...")
            await asyncio.sleep(wait)


async def click_with_retry(page, selector: str, retries: int = MAX_RETRY):
    """Click a selector with retry and navigation wait."""
    for attempt in range(1, retries + 1):
        try:
            async with page.expect_navigation(wait_until="networkidle", timeout=45_000):
                await page.click(selector)
            return True
        except Exception as e:
            if attempt == retries:
                log.error(f"Failed to click {selector!r} after {retries} attempts: {e}")
                return False
            await asyncio.sleep(2 ** attempt)
    return False


def extract_tenders_from_page(rows_data: list, page_num: int) -> list[dict]:
    """Parse raw row data into structured tender records."""
    tenders = []
    ts = datetime.utcnow().isoformat()

    for row in rows_data:
        parsed = parse_title_cell(row.get("title_raw", ""))
        tender = {
            "tender_id":      parsed["tender_id"],
            "ref_number":     parsed["ref_number"],
            "title":          parsed["title"],
            "organisation":   row.get("organisation", "").replace("||", " > "),
            "published_date": row.get("published_date", ""),
            "closing_date":   row.get("closing_date", ""),
            "opening_date":   row.get("opening_date", ""),
            "status":         "Active",   # results page only shows active tenders
            "detail_url":     row.get("detail_href", ""),
            "scraped_at":     ts,
            "page_num":       page_num,
        }
        tenders.append(tender)

    return tenders


# ─── Core scraping JS (runs inside page context) ────────────────────────────────
EXTRACT_ROWS_JS = """() => {
    const rows = document.querySelectorAll(
        'table.list_table tr.even, table.list_table tr.odd, #table tr.even, #table tr.odd'
    );
    return Array.from(rows).map(row => {
        const cells = Array.from(row.querySelectorAll('td'));
        const linkEl = row.querySelector('a[title="View Tender Information"]');
        return {
            sno:          cells[0]?.innerText.trim() || '',
            published_date: cells[1]?.innerText.trim() || '',
            closing_date:   cells[2]?.innerText.trim() || '',
            opening_date:   cells[3]?.innerText.trim() || '',
            title_raw:      cells[4]?.innerText.trim() || '',
            organisation:   cells[5]?.innerText.trim() || '',
            detail_href:    linkEl ? linkEl.href : '',
        };
    });
}"""

GET_PAGINATION_JS = """() => {
    const next = document.querySelector('#linkFwd');
    const last = document.querySelector('#linkLast');
    const pageLinks = Array.from(document.querySelectorAll('a[id^="linkPage"]'))
        .map(a => ({id: a.id, text: a.innerText.trim(), href: a.href}));
    // Current page: find the "1" or bold/active page indicator
    const bodyText = document.body.innerText;
    const m = bodyText.match(/Page\\s+(\\d+)\\s+of\\s+(\\d+)/i);
    return {
        hasNext: !!next,
        nextHref: next ? next.href : null,
        hasLast:  !!last,
        lastHref: last ? last.href : null,
        pageLinks: pageLinks,
        paginationText: m ? m[0] : null,
    };
}"""


# ─── Main scraper ───────────────────────────────────────────────────────────────
async def scrape(
    max_pages: int | None = None,
    output_dir: Path = OUTPUT_DIR,
    org_filter: str | None = None,
) -> list[dict]:
    """
    Scrape all tenders from defproc.gov.in.

    Args:
        max_pages:  stop after N pages (None = all)
        output_dir: where to write CSV/JSON
        org_filter: optional org substring filter (e.g. "MES")

    Returns:
        List of tender dicts
    """
    ua = UserAgent()
    all_tenders: list[dict] = []
    run_start = datetime.utcnow()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=ua.chrome,
            locale="en-US",
            timezone_id="Asia/Kolkata",
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        # ── Step 1: Establish session by visiting landing page ──────────────
        log.info("Establishing session...")
        await get_page_with_retry(page, f"{BASE_URL}?page=FrontEndLatestActiveTenders&service=page")
        await random_delay()

        # ── Step 2: Navigate directly to results page (no CAPTCHA!) ─────────
        log.info(f"Navigating to results: {RESULTS_URL}")
        await get_page_with_retry(page, RESULTS_URL)
        await page.wait_for_timeout(2000)

        current_page = 1
        consecutive_empty = 0

        while True:
            if max_pages and current_page > max_pages:
                log.info(f"Reached max_pages limit ({max_pages}), stopping.")
                break

            # ── Extract tenders from current page ──────────────────────────
            rows_data = await page.evaluate(EXTRACT_ROWS_JS)
            if not rows_data:
                consecutive_empty += 1
                log.warning(f"Page {current_page}: no rows found (empty={consecutive_empty})")
                if consecutive_empty >= 2:
                    break
            else:
                consecutive_empty = 0

            tenders = extract_tenders_from_page(rows_data, current_page)

            # Optional org filter
            if org_filter:
                tenders = [t for t in tenders if org_filter.lower() in t["organisation"].lower()]

            all_tenders.extend(tenders)

            # ── Pagination info ────────────────────────────────────────────
            pagination = await page.evaluate(GET_PAGINATION_JS)
            total_pages_text = pagination.get("paginationText") or "?"

            log.info(
                f"Page {current_page} {total_pages_text} — scraped {len(tenders)} tenders "
                f"(running total: {len(all_tenders)})"
            )

            # ── Rotate user agent between pages ────────────────────────────
            await context.set_extra_http_headers({"User-Agent": ua.chrome})

            # ── Check if there's a Next page ───────────────────────────────
            if not pagination["hasNext"]:
                log.info("No 'Next' button found — reached last page.")
                break

            # ── Delay before next page ─────────────────────────────────────
            await random_delay()

            # ── Click Next (>) ─────────────────────────────────────────────
            success = await click_with_retry(page, "#linkFwd")
            if not success:
                log.error("Could not click Next button, stopping.")
                break

            current_page += 1
            await page.wait_for_timeout(1500)

        # ── Save screenshot of last page ────────────────────────────────────
        await page.screenshot(path=str(SCREENSHOTS / "last_page.png"), full_page=True)
        await browser.close()

    # ── Write outputs ───────────────────────────────────────────────────────────
    run_end = datetime.utcnow()
    _write_outputs(all_tenders, output_dir, run_start, run_end, current_page)

    return all_tenders


def _write_outputs(
    tenders: list[dict],
    output_dir: Path,
    run_start: datetime,
    run_end:   datetime,
    pages_scraped: int,
):
    output_dir.mkdir(exist_ok=True)
    csv_path  = output_dir / "defproc_tenders.csv"
    json_path = output_dir / "defproc_tenders.json"
    log_path  = LOG_DIR / "scraper_log.txt"

    # ── CSV (append mode, write header only if file is new) ────────────────────
    is_new = not csv_path.exists() or csv_path.stat().st_size == 0
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        writer.writerows(tenders)

    # ── JSON (full dump, overwritten) ──────────────────────────────────────────
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(tenders, f, indent=2, ensure_ascii=False)

    # ── Summary ────────────────────────────────────────────────────────────────
    dates = [t["published_date"] for t in tenders if t["published_date"]]
    closing = [t["closing_date"] for t in tenders if t["closing_date"]]
    orgs = sorted(set(t["organisation"].split(" > ")[0] for t in tenders if t["organisation"]))

    summary = (
        f"\n{'='*60}\n"
        f"  SCRAPE COMPLETE\n"
        f"{'='*60}\n"
        f"  Run start  : {run_start.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
        f"  Run end    : {run_end.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
        f"  Pages      : {pages_scraped}\n"
        f"  Total      : {len(tenders)} tenders\n"
        f"  Published  : {min(dates) if dates else 'N/A'} — {max(dates) if dates else 'N/A'}\n"
        f"  Closing    : {min(closing) if closing else 'N/A'} — {max(closing) if closing else 'N/A'}\n"
        f"  Orgs       : {', '.join(orgs[:5])}{'...' if len(orgs) > 5 else ''}\n"
        f"  CSV        : {csv_path}\n"
        f"  JSON       : {json_path}\n"
        f"{'='*60}\n"
    )
    log.info(summary)

    # ── Append to run log ──────────────────────────────────────────────────────
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(summary)


# ─── CLI ────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Scrape defproc.gov.in tender listings")
    parser.add_argument("--pages",   type=int, default=None, help="Max pages to scrape (default: all)")
    parser.add_argument("--org",     type=str, default=None, help="Filter by organisation substring")
    parser.add_argument("--output",  type=str, default="output", help="Output directory")
    args = parser.parse_args()

    tenders = asyncio.run(scrape(
        max_pages=args.pages,
        output_dir=Path(args.output),
        org_filter=args.org,
    ))
    print(f"\nDone. Scraped {len(tenders)} tenders total.")


if __name__ == "__main__":
    main()
