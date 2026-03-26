"""
Phase 3: Detail page scraper
Visits each tender's detail URL and extracts full metadata.

Usage:
  python detail_scraper.py [--input output/defproc_tenders.json] [--limit N]
"""
from __future__ import annotations
import asyncio
import json
import logging
import random
from datetime import datetime
from pathlib import Path

from fake_useragent import UserAgent
from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError
from playwright_stealth import Stealth

OUTPUT_DIR = Path("output")
LOG_DIR    = Path("logs")
BASE_URL   = "https://defproc.gov.in/nicgep/app"
DELAY_MIN  = 3.0
DELAY_MAX  = 6.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_DIR / "detail_log.txt")],
)
log = logging.getLogger("detail")

DETAIL_EXTRACT_JS = """() => {
    const getText = sel => {
        const el = document.querySelector(sel);
        return el ? el.innerText.trim() : '';
    };
    const getTable = sel => {
        const table = document.querySelector(sel);
        if (!table) return [];
        const rows = Array.from(table.querySelectorAll('tr'));
        return rows.map(row => {
            const cells = Array.from(row.querySelectorAll('td, th'));
            return cells.map(c => c.innerText.trim());
        }).filter(r => r.some(c => c));
    };

    // Full page text content (cleaned)
    const fullText = document.body.innerText;

    // All tables
    const tables = Array.from(document.querySelectorAll('table')).map(t => {
        const rows = Array.from(t.querySelectorAll('tr')).slice(0, 20);
        return rows.map(r => Array.from(r.querySelectorAll('td, th')).map(c => c.innerText.trim()))
                   .filter(r => r.some(c => c));
    }).filter(t => t.length > 0);

    // Document download links
    const docLinks = Array.from(document.querySelectorAll('a[href*="document"], a[href*="Document"]')).map(a => ({
        text: a.innerText.trim(),
        href: a.href,
    }));

    // Key-value pairs (label: value pattern in tables)
    const kvPairs = {};
    document.querySelectorAll('tr').forEach(row => {
        const cells = Array.from(row.querySelectorAll('td'));
        if (cells.length >= 2) {
            const key = cells[0].innerText.trim().replace(/:$/, '');
            const val = cells[1].innerText.trim();
            if (key && val && key.length < 60) {
                kvPairs[key] = val;
            }
        }
    });

    return {
        title: getText('title'),
        tables: tables,
        docLinks: docLinks,
        kvPairs: kvPairs,
        fullText: fullText.substring(0, 5000),
    };
}"""


async def scrape_detail(page, tender: dict, ua: UserAgent) -> dict:
    """Scrape detail page for a single tender."""
    url = tender.get("detail_url", "")
    if not url:
        return {**tender, "detail_scraped": False, "error": "no detail_url"}

    # Rotate user agent
    await page.context.set_extra_http_headers({"User-Agent": ua.chrome})

    for attempt in range(1, 4):
        try:
            await page.goto(url, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(1500)

            data = await page.evaluate(DETAIL_EXTRACT_JS)

            # Extract key fields from kvPairs or tables
            kv = data.get("kvPairs", {})
            doc_links = data.get("docLinks", [])

            def kv_get(*keys):
                for key in keys:
                    if key in kv and kv[key]:
                        return kv[key]
                return ""

            # Build structured detail record
            detail = {
                **tender,
                "detail_scraped":       True,
                "detail_fetched_at":    datetime.utcnow().isoformat(),
                "tender_value":         kv_get("Tender Value in \u20b9", "ECV", "Estimated Cost (In Lakhs)", "Tender Value"),
                "tender_fee":           kv_get("Tender Fee in \u20b9", "Tender Fee"),
                "emd":                  kv_get("EMD Amount in \u20b9", "EMD Amount (in \u20b9)", "EMD"),
                "bid_submission_start": kv_get("Bid Submission Start Date"),
                "bid_submission_end":   kv_get("Bid Submission End Date", "Closing Date"),
                "document_sale_start":  kv_get("Document Download / Sale Start Date"),
                "document_sale_end":    kv_get("Document Download / Sale End Date"),
                "clarification_start":  kv_get("Clarification Start Date"),
                "clarification_end":    kv_get("Clarification End Date"),
                "pre_bid_meeting":      kv_get("Pre Bid Meeting Date"),
                "contact":              kv_get("Contact", "Contact Person"),
                "location":             kv_get("Location", "District"),
                "pincode":              kv_get("Pincode"),
                "tender_type":          kv_get("Tender Type"),
                "tender_category":      kv_get("Tender Category"),
                "form_of_contract":     kv_get("Form Of Contract"),
                "product_category":     kv_get("Product Category"),
                "payment_mode":         kv_get("Payment Mode"),
                "documents":            [d["href"] for d in doc_links],
                "kv_pairs":             kv,
            }
            return detail

        except Exception as e:
            if attempt == 3:
                log.error(f"Failed to scrape {url}: {e}")
                return {**tender, "detail_scraped": False, "error": str(e)}
            await asyncio.sleep(2 ** attempt + random.uniform(0, 1))

    return {**tender, "detail_scraped": False, "error": "max retries"}


async def scrape_all_details(
    tenders: list[dict],
    limit: int | None = None,
    output_dir: Path = OUTPUT_DIR,
) -> list[dict]:
    if limit:
        tenders = tenders[:limit]

    ua = UserAgent()
    detailed = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=ua.chrome,
            locale="en-US",
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        # Establish session first
        await page.goto(f"{BASE_URL}?page=FrontEndLatestActiveTenders&service=page",
                        wait_until="networkidle", timeout=60_000)
        await asyncio.sleep(2)

        for i, tender in enumerate(tenders, 1):
            log.info(f"[{i}/{len(tenders)}] Scraping detail: {tender.get('tender_id', '?')}")
            result = await scrape_detail(page, tender, ua)
            detailed.append(result)

            if i < len(tenders):
                delay = random.uniform(DELAY_MIN, DELAY_MAX)
                await asyncio.sleep(delay)

            # Save incrementally every 50
            if i % 50 == 0:
                _save_detailed(detailed, output_dir)
                log.info(f"Checkpoint: saved {len(detailed)} detailed tenders")

        await browser.close()

    _save_detailed(detailed, output_dir)
    log.info(f"Detail scraping complete: {len(detailed)} tenders enriched")
    return detailed


def _save_detailed(detailed: list[dict], output_dir: Path):
    output_dir.mkdir(exist_ok=True)
    path = output_dir / "defproc_tenders_detailed.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(detailed, f, indent=2, ensure_ascii=False)
    log.info(f"Saved {len(detailed)} detailed tenders → {path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scrape tender detail pages")
    parser.add_argument("--input",  default="output/defproc_tenders.json", help="Input JSON from main scraper")
    parser.add_argument("--limit",  type=int, default=None, help="Max tenders to detail-scrape")
    parser.add_argument("--output", default="output", help="Output directory")
    args = parser.parse_args()

    tenders_path = Path(args.input)
    if not tenders_path.exists():
        print(f"Error: {tenders_path} not found. Run defproc_scraper.py first.")
        exit(1)

    with open(tenders_path) as f:
        tenders = json.load(f)

    asyncio.run(scrape_all_details(tenders, limit=args.limit, output_dir=Path(args.output)))
