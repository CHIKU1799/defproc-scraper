"""
Phase 4: Daily run + diff
Runs the scraper, compares against previous run, reports new tenders.

Usage:
  python run_daily.py [--pages N] [--detail]
"""
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

OUTPUT_DIR = Path("output")
LOG_DIR    = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "daily_log.txt", encoding="utf-8"),
    ],
)
log = logging.getLogger("daily")

SNAPSHOT_PATH = OUTPUT_DIR / "previous_run.json"
CURRENT_PATH  = OUTPUT_DIR / "defproc_tenders.json"


def load_previous_ids() -> set[str]:
    if not SNAPSHOT_PATH.exists():
        return set()
    with open(SNAPSHOT_PATH) as f:
        data = json.load(f)
    return {t.get("tender_id", "") or t.get("detail_url", "") for t in data if t}


def save_snapshot(tenders: list[dict]):
    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(tenders, f, indent=2, ensure_ascii=False)


def find_new_tenders(tenders: list[dict], prev_ids: set[str]) -> list[dict]:
    new = []
    for t in tenders:
        uid = t.get("tender_id", "") or t.get("detail_url", "")
        if uid and uid not in prev_ids:
            new.append(t)
    return new


def print_new_summary(new_tenders: list[dict]):
    if not new_tenders:
        log.info("No new tenders found since last run.")
        return

    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  NEW TENDERS FOUND: {len(new_tenders)}")
    print(sep)
    for t in new_tenders:
        print(f"\n  Tender ID : {t.get('tender_id', 'N/A')}")
        print(f"  Ref No    : {t.get('ref_number', 'N/A')}")
        print(f"  Title     : {t.get('title', 'N/A')[:100]}")
        print(f"  Org       : {t.get('organisation', 'N/A')[:80]}")
        print(f"  Published : {t.get('published_date', 'N/A')}")
        print(f"  Closes    : {t.get('closing_date', 'N/A')}")
        print(f"  URL       : {t.get('detail_url', 'N/A')}")
    print(f"\n{sep}\n")


async def run(max_pages=None, run_detail=False):
    from defproc_scraper import scrape
    from detail_scraper import scrape_all_details

    run_ts = datetime.utcnow()
    log.info(f"{'='*50}")
    log.info(f"Daily run started at {run_ts.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    log.info(f"{'='*50}")

    # Load previous snapshot
    prev_ids = load_previous_ids()
    log.info(f"Previous snapshot: {len(prev_ids)} known tender IDs")

    # Run main scraper
    tenders = await scrape(max_pages=max_pages)
    log.info(f"Scraped {len(tenders)} tenders total")

    # Diff
    new_tenders = find_new_tenders(tenders, prev_ids)
    print_new_summary(new_tenders)

    # Save new snapshot
    save_snapshot(tenders)
    log.info(f"Snapshot updated: {len(tenders)} tenders saved to {SNAPSHOT_PATH}")

    # Optionally run detail scraper on new tenders only
    if run_detail and new_tenders:
        log.info(f"Running detail scraper on {len(new_tenders)} new tenders...")
        await scrape_all_details(new_tenders, output_dir=OUTPUT_DIR)

    # Write run log entry
    log_entry = (
        f"[{run_ts.strftime('%Y-%m-%d %H:%M:%S')}] "
        f"Total={len(tenders)} | New={len(new_tenders)} | "
        f"PreviousKnown={len(prev_ids)}\n"
    )
    with open(LOG_DIR / "daily_log.txt", "a", encoding="utf-8") as f:
        f.write(log_entry)

    log.info("Daily run complete.")
    return tenders, new_tenders


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Daily tender scrape + diff")
    parser.add_argument("--pages",  type=int, default=None, help="Max pages to scrape")
    parser.add_argument("--detail", action="store_true",    help="Also scrape detail pages for NEW tenders")
    args = parser.parse_args()

    asyncio.run(run(max_pages=args.pages, run_detail=args.detail))
