"""
Inspect the direct results page in browser to understand pagination
and find the page size / total records.
"""
import asyncio
import json
import re
from pathlib import Path
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

BASE_URL = "https://defproc.gov.in/nicgep/app"
RESULTS_URL = f"{BASE_URL}?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct"


async def inspect():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        # Capture POST requests
        requests_log = []
        page.on("request", lambda req: requests_log.append({
            "method": req.method,
            "url": req.url,
            "post_data": req.post_data,
        }) if req.method == "POST" else None)

        print("[*] Loading results page directly...")
        await page.goto(RESULTS_URL, wait_until="networkidle", timeout=60_000)
        await page.wait_for_timeout(2000)

        await page.screenshot(path="screenshots/results_direct.png", full_page=True)

        # Count tenders
        tender_count = await page.evaluate("""() => {
            return document.querySelectorAll('table#table tr.even, table#table tr.odd, table.list_table tr.even, table.list_table tr.odd').length;
        }""")
        print(f"[✓] Tenders on page: {tender_count}")

        # Get ALL clickable/interactive elements
        interactive = await page.evaluate("""() => {
            const items = [];
            document.querySelectorAll('a, button, input[type="submit"], input[type="button"]').forEach(el => {
                const text = (el.innerText || el.value || '').trim();
                if (text && !el.id.startsWith('DirectLink') && !el.id.startsWith('PageLink')) {
                    items.push({
                        tag: el.tagName,
                        id: el.id || '',
                        text: text.substring(0, 60),
                        href: el.href || '',
                        onclick: (el.getAttribute('onclick') || '').substring(0, 100),
                    });
                }
            });
            return items;
        }""")
        print("\n[*] Interactive elements (non-tender links):")
        for item in interactive:
            print(f"   [{item['tag']}] id={item['id']!r} text={item['text']!r} href={item['href'][:80]!r}")

        # Get page body text for any "page X of Y" or "records" text
        body_text = await page.evaluate("() => document.body.innerText")
        page_refs = re.findall(r'(?:page|records?|showing|total|found)\s*\d[^\n]{0,50}', body_text, re.IGNORECASE)
        print(f"\n[*] Page/records references in visible text:")
        for ref in page_refs[:10]:
            print(f"    {ref.strip()}")

        # Try to extract all tender rows
        tenders = await page.evaluate("""() => {
            const rows = document.querySelectorAll('table.list_table tr.even, table.list_table tr.odd, #table tr.even, #table tr.odd');
            return Array.from(rows).map(row => {
                const cells = Array.from(row.querySelectorAll('td'));
                const linkEl = row.querySelector('a[title="View Tender Information"]');
                return {
                    sno: cells[0]?.innerText.trim(),
                    published_date: cells[1]?.innerText.trim(),
                    closing_date: cells[2]?.innerText.trim(),
                    opening_date: cells[3]?.innerText.trim(),
                    title_raw: cells[4]?.innerText.trim(),
                    organisation: cells[5]?.innerText.trim(),
                    detail_href: linkEl?.href || '',
                };
            });
        }""")
        print(f"\n[*] Extracted {len(tenders)} tenders. First 3:")
        for t in tenders[:3]:
            print(json.dumps(t, indent=2))

        # Now try to find "Next" button or page navigation
        # Try clicking a "View All" or looking for the page size selector
        print("\n[*] Looking for page size selector or navigation...")

        # Check the URL when we first land - see if there's a default page size
        # Try to force a larger page or look at page count via the form state

        # The hidden div has iterPage_0 which controls paging - let's inspect it
        form_state = await page.evaluate("""() => {
            const hiddenDiv = document.querySelector('#AdvancedSearchhidden');
            if (!hiddenDiv) return null;
            const inputs = Array.from(hiddenDiv.querySelectorAll('input'));
            const result = {};
            inputs.forEach(inp => {
                if (inp.name && !['formids','seedids','component','page','service','session','submitmode','submitname'].includes(inp.name)) {
                    result[inp.name] = inp.value.substring(0, 40);
                }
            });
            return result;
        }""")
        print(f"\n[*] Form state (non-boilerplate hidden inputs):")
        if form_state:
            for k, v in form_state.items():
                print(f"    {k}: {v!r}")

        # Try fetching the Advanced Search form to understand pagination submit mechanism
        print("\n[*] Loading AdvancedSearch form to understand pagination...")
        await page.goto(f"{BASE_URL}?page=FrontEndAdvancedSearch&service=page",
                        wait_until="networkidle", timeout=60_000)
        await page.wait_for_timeout(1000)

        # Extract page size options
        page_size = await page.evaluate("""() => {
            const selects = document.querySelectorAll('select');
            return Array.from(selects).map(s => ({
                name: s.name,
                id: s.id,
                options: Array.from(s.options).map(o => ({value: o.value, text: o.text.trim()}))
            }));
        }""")
        print("\n[*] Select dropdowns on Advanced Search:")
        for sel in page_size:
            print(f"    {sel['name']}/{sel['id']}: {sel['options'][:8]}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(inspect())
