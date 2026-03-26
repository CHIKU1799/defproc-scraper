"""
Phase 1b: Explore alternative listing pages on defproc.gov.in
- Active Tenders listing (no captcha likely)
- Tenders by Closing Date
- Advanced Search with CAPTCHA OCR fallback
Goal: Find a captcha-free paginated endpoint
"""
import asyncio
import json
import re
import base64
import os
from pathlib import Path
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

BASE_URL = "https://defproc.gov.in/nicgep/app"
SCREENSHOTS_DIR = Path("screenshots")
SCREENSHOTS_DIR.mkdir(exist_ok=True)

PAGES_TO_TRY = [
    ("Active Tenders", f"{BASE_URL}?page=FrontEndLatestActiveTenders&service=page"),
    ("Tenders by Closing Date", f"{BASE_URL}?page=FrontEndListTendersbyDate&service=page"),
    ("Tenders by Location", f"{BASE_URL}?page=FrontEndTendersByLocation&service=page"),
]


async def inspect_page(page, name: str, url: str):
    print(f"\n{'='*60}")
    print(f"  INSPECTING: {name}")
    print(f"  URL: {url}")
    print(f"{'='*60}")

    response = await page.goto(url, wait_until="networkidle", timeout=60_000)
    print(f"  Status: {response.status}  Final URL: {page.url}")
    await page.wait_for_timeout(2000)

    slug = name.lower().replace(" ", "_")
    await page.screenshot(path=str(SCREENSHOTS_DIR / f"{slug}.png"), full_page=True)
    print(f"  Screenshot: screenshots/{slug}.png")

    # Save full HTML
    html = await page.content()
    html_path = SCREENSHOTS_DIR / f"{slug}.html"
    html_path.write_text(html, encoding="utf-8")

    # Extract page title
    title = await page.title()
    print(f"  Title: {title}")

    # Check for captcha
    has_captcha = "captchaImage" in html or "captchaText" in html
    print(f"  Has CAPTCHA: {has_captcha}")

    # Count table rows
    row_count = await page.evaluate("() => document.querySelectorAll('table tr').length")
    print(f"  Table rows: {row_count}")

    # Look for pagination
    page_info = await page.evaluate("""() => {
        const text = document.body.innerText;
        const m = text.match(/\\d+\\s*[-–]\\s*\\d+\\s*(of|out of)\\s*(\\d+)/i)
              || text.match(/Page\\s+(\\d+)\\s+of\\s+(\\d+)/i)
              || text.match(/Total[:\\s]+(\\d+)/i);
        const nextLink = document.querySelector('a[title="Next"], a[class*="next"]');
        const pageLinks = Array.from(document.querySelectorAll('a')).filter(
            a => /^\\d+$/.test(a.innerText.trim()) && a.href
        ).map(a => ({text: a.innerText.trim(), href: a.href})).slice(0, 10);
        return {
            paginationText: m ? m[0] : null,
            hasNextLink: !!nextLink,
            nextLinkHref: nextLink ? nextLink.href : null,
            pageLinks: pageLinks,
        };
    }""")
    print(f"  Pagination: {json.dumps(page_info, indent=4)}")

    # Inspect the results table structure (first 3 rows)
    table_info = await page.evaluate("""() => {
        const tables = Array.from(document.querySelectorAll('table'));
        // Find the biggest table (likely the results)
        let biggest = null, maxRows = 0;
        for (const t of tables) {
            const rows = t.querySelectorAll('tr');
            if (rows.length > maxRows) { maxRows = rows.length; biggest = t; }
        }
        if (!biggest) return null;
        const headers = Array.from(biggest.querySelectorAll('tr:first-child th, tr:first-child td'))
            .map(c => c.innerText.trim());
        const firstRow = Array.from(biggest.querySelectorAll('tr:nth-child(2) td'))
            .map(c => c.innerText.trim().substring(0, 80));
        const secondRow = Array.from(biggest.querySelectorAll('tr:nth-child(3) td'))
            .map(c => c.innerText.trim().substring(0, 80));
        return {rowCount: maxRows, headers, firstRow, secondRow};
    }""")
    if table_info:
        print(f"\n  Biggest table ({table_info['rowCount']} rows):")
        print(f"    Headers:  {table_info['headers']}")
        print(f"    Row 1:    {table_info['firstRow']}")
        print(f"    Row 2:    {table_info['secondRow']}")

    # Check for onclick/href patterns in links
    link_patterns = await page.evaluate("""() => {
        const links = Array.from(document.querySelectorAll('a[onclick], a[href*="DirectLink"], a[href*="tender"]'));
        return links.slice(0, 5).map(a => ({
            text: a.innerText.trim().substring(0, 50),
            href: a.href.substring(0, 150),
            onclick: (a.getAttribute('onclick') || '').substring(0, 150),
        }));
    }""")
    if link_patterns:
        print(f"\n  Link patterns (DirectLink/tender):")
        for lp in link_patterns:
            print(f"    text={lp['text']!r}  href={lp['href']!r}  onclick={lp['onclick']!r}")

    return {"name": name, "url": url, "has_captcha": has_captcha, "table_rows": row_count, "pagination": page_info}


async def explore_active():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        results = []
        for name, url in PAGES_TO_TRY:
            try:
                info = await inspect_page(page, name, url)
                results.append(info)
            except Exception as e:
                print(f"  ERROR on {name}: {e}")

        # ── Also try the Advanced Search directly posting with empty captcha ──
        print(f"\n{'='*60}")
        print("  TRYING: Advanced Search with Pytesseract OCR captcha")
        print(f"{'='*60}")
        try:
            await try_advanced_search(page)
        except Exception as e:
            print(f"  ERROR: {e}")

        await browser.close()

        print(f"\n\n{'='*60}")
        print("  SUMMARY")
        print(f"{'='*60}")
        for r in results:
            print(f"  {r['name']}: captcha={r['has_captcha']}, rows={r['table_rows']}, next={r['pagination'].get('hasNextLink')}")


async def try_advanced_search(page):
    """Try to solve the CAPTCHA using simple image processing and submit the search."""
    try:
        import pytesseract
        from PIL import Image
        import io
        HAS_OCR = True
    except ImportError:
        print("  pytesseract/PIL not installed — skipping OCR captcha solve")
        HAS_OCR = False

    search_url = f"{BASE_URL}?page=FrontEndAdvancedSearch&service=page"
    await page.goto(search_url, wait_until="networkidle", timeout=60_000)
    await page.wait_for_timeout(1500)

    html = await page.content()
    # Extract captcha base64 image
    m = re.search(r'captchaImage[^>]*src="data:image/png;base64,([^"]+)"', html)
    if not m:
        print("  No captcha image found in page")
        return

    img_data = m.group(1).replace('\n', '').replace(' ', '')
    img_bytes = base64.b64decode(img_data)
    with open("screenshots/captcha_live.png", "wb") as f:
        f.write(img_bytes)
    print("  Captcha saved: screenshots/captcha_live.png")

    if not HAS_OCR:
        return

    # OCR the captcha
    img = Image.open(io.BytesIO(img_bytes)).convert("L")
    # Enlarge and threshold for better OCR
    img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
    captcha_text = pytesseract.image_to_string(img, config="--psm 8 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789").strip()
    print(f"  OCR captcha text: {captcha_text!r}")

    # Fill in the captcha and submit
    await page.fill("#captchaText", captcha_text)
    await page.wait_for_timeout(500)

    async with page.expect_navigation(wait_until="networkidle", timeout=30_000):
        await page.click("#submit")

    await page.wait_for_timeout(2000)
    print(f"  Result URL: {page.url}")
    html_after = await page.content()
    if "captchaText" in html_after:
        print("  Still on captcha page (wrong captcha text)")
    elif "result" in page.url.lower() or "AdvancedSearchResult" in page.url:
        print("  SUCCESS: Reached results page!")
        await page.screenshot(path="screenshots/adv_search_result.png", full_page=True)
    else:
        print("  Unknown redirect, saving HTML...")
        Path("screenshots/adv_search_after.html").write_text(html_after)


if __name__ == "__main__":
    asyncio.run(explore_active())
