"""
Phase 1: Explore defproc.gov.in eProcurement portal
- Visit the advanced search page
- Screenshot the form
- Extract all form fields
- Submit the form and capture results
- Screenshot results page
- Print HTML of the results table
"""
import asyncio
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

BASE_URL = "https://defproc.gov.in/nicgep/app"
SEARCH_PAGE = f"{BASE_URL}?page=FrontEndAdvancedSearch&service=page"
SCREENSHOTS_DIR = Path("screenshots")
SCREENSHOTS_DIR.mkdir(exist_ok=True)


async def explore():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ])
        context = await browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        # ── 1. Visit Advanced Search form ──────────────────────────────────
        print(f"[*] Navigating to: {SEARCH_PAGE}")
        response = await page.goto(SEARCH_PAGE, wait_until="networkidle", timeout=60_000)
        print(f"    Status: {response.status}")
        await page.wait_for_timeout(2000)

        await page.screenshot(path=str(SCREENSHOTS_DIR / "search_form.png"), full_page=True)
        print("[✓] Screenshot saved: screenshots/search_form.png")

        # ── 2. Extract all form fields ──────────────────────────────────────
        print("\n[*] Extracting form fields...")
        fields = await page.evaluate("""() => {
            const result = [];
            document.querySelectorAll('input, select, textarea').forEach(el => {
                result.push({
                    tag:   el.tagName,
                    type:  el.type || '',
                    name:  el.name || '',
                    id:    el.id   || '',
                    value: el.value || '',
                    options: el.tagName === 'SELECT'
                        ? Array.from(el.options).map(o => ({value: o.value, text: o.text.trim()}))
                        : null
                });
            });
            return result;
        }""")

        print(f"\n{'='*60}")
        print(f"  FORM FIELDS ({len(fields)} total)")
        print(f"{'='*60}")
        for f in fields:
            if f["tag"] == "SELECT" and f["options"]:
                opts = ", ".join(f"{o['value']}={o['text']}" for o in f["options"][:5])
                print(f"  [{f['tag']:8}] name={f['name']!r:30} id={f['id']!r:25} options=[{opts}...]")
            else:
                display_val = (f["value"][:40] + "...") if len(f["value"]) > 40 else f["value"]
                print(f"  [{f['tag']:8}] name={f['name']!r:30} id={f['id']!r:25} type={f['type']!r:12} value={display_val!r}")

        # Save full field list to JSON for reference
        with open(SCREENSHOTS_DIR / "form_fields.json", "w") as fh:
            json.dump(fields, fh, indent=2)
        print("\n[✓] Full field list saved: screenshots/form_fields.json")

        # ── 3. Extract page title and any visible search filters ────────────
        title = await page.title()
        print(f"\n[*] Page title: {title!r}")

        # ── 4. Submit the search form (blank = all tenders) ─────────────────
        print("\n[*] Submitting search form (blank = all tenders)...")

        # Look for the search/submit button
        submit_btn = await page.query_selector(
            'input[type="submit"], button[type="submit"], '
            'input[value*="Search"], button:has-text("Search"), '
            'a:has-text("Search")'
        )
        if submit_btn:
            btn_text = await submit_btn.get_attribute("value") or await submit_btn.inner_text()
            print(f"    Found submit button: {btn_text!r}")
        else:
            print("    WARNING: No obvious submit button found — will try clicking Search link")

        # Capture network requests to understand the POST
        requests_log = []
        page.on("request", lambda req: requests_log.append({
            "method": req.method,
            "url": req.url,
            "post_data": req.post_data,
        }) if req.resource_type in ("document", "xhr", "fetch") else None)

        await page.wait_for_timeout(1000)

        if submit_btn:
            async with page.expect_navigation(wait_until="networkidle", timeout=60_000):
                await submit_btn.click()
        else:
            # Try finding and clicking a search link
            search_link = await page.query_selector('a[href*="Search"], a[href*="search"]')
            if search_link:
                async with page.expect_navigation(wait_until="networkidle", timeout=60_000):
                    await search_link.click()
            else:
                print("    ERROR: Cannot find how to submit — printing page HTML for inspection")
                html = await page.content()
                print(html[:3000])

        await page.wait_for_timeout(2000)
        result_url = page.url
        print(f"\n[✓] Result URL: {result_url}")

        # Cookies
        cookies = await context.cookies()
        jsession = next((c for c in cookies if c["name"] == "JSESSIONID"), None)
        print(f"[✓] JSESSIONID: {jsession['value'][:20]}..." if jsession else "[!] No JSESSIONID found")
        print(f"[✓] All cookies: {[c['name'] for c in cookies]}")

        # ── 5. Screenshot results page ──────────────────────────────────────
        await page.screenshot(path=str(SCREENSHOTS_DIR / "results_page.png"), full_page=True)
        print("[✓] Screenshot saved: screenshots/results_page.png")

        # ── 6. Print results table HTML ──────────────────────────────────────
        print("\n[*] Inspecting results table structure...")
        results_html = await page.evaluate("""() => {
            // Try common table selectors
            const selectors = [
                'table.list_table', 'table#table', '.tablesorter',
                'table[class*="result"]', 'table[class*="list"]',
                'table[class*="tender"]', 'form table', 'table'
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el) return {selector: sel, html: el.outerHTML.substring(0, 8000)};
            }
            return {selector: null, html: document.body.innerHTML.substring(0, 8000)};
        }""")

        print(f"\n  Using selector: {results_html['selector']}")
        print(f"\n{'='*60}")
        print("  RESULTS TABLE HTML (first 8000 chars)")
        print(f"{'='*60}")
        print(results_html["html"])

        # Also save full results page HTML
        full_html = await page.content()
        with open(SCREENSHOTS_DIR / "results_page.html", "w", encoding="utf-8") as fh:
            fh.write(full_html)
        print("\n[✓] Full results HTML saved: screenshots/results_page.html")

        # ── 7. Extract pagination info ───────────────────────────────────────
        print("\n[*] Pagination controls...")
        pagination = await page.evaluate("""() => {
            const info = {};
            // Common patterns: "Page X of Y", "Showing X-Y of Z"
            const bodyText = document.body.innerText;
            const pageMatch = bodyText.match(/Page\\s+(\\d+)\\s+of\\s+(\\d+)/i)
                           || bodyText.match(/(\\d+)\\s*-\\s*(\\d+)\\s+of\\s+(\\d+)/i);
            if (pageMatch) info.pageText = pageMatch[0];

            // Next button
            const nextBtn = document.querySelector(
                'a[title*="Next"], a:has-text("Next"), input[value="Next"],'
                ' a[href*="next"], a[class*="next"]'
            );
            if (nextBtn) {
                info.nextBtn = {
                    tag: nextBtn.tagName,
                    href: nextBtn.href || '',
                    text: nextBtn.innerText.trim(),
                    onclick: nextBtn.getAttribute('onclick') || ''
                };
            }

            // Page numbers
            const pageLinks = Array.from(document.querySelectorAll('a[href*="page"], a[href*="Page"]'))
                .slice(0, 10).map(a => ({text: a.innerText.trim(), href: a.href}));
            info.pageLinks = pageLinks;

            // Count rows in results table
            const rows = document.querySelectorAll('table tr');
            info.tableRowCount = rows.length;

            return info;
        }""")
        print(json.dumps(pagination, indent=2))

        # ── 8. Log captured network requests ────────────────────────────────
        print(f"\n[*] Captured {len(requests_log)} navigation requests:")
        for r in requests_log[-5:]:
            print(f"    {r['method']} {r['url'][:100]}")
            if r["post_data"]:
                print(f"         POST data: {r['post_data'][:300]}")

        await browser.close()
        print("\n[✓] Phase 1 complete.")


if __name__ == "__main__":
    asyncio.run(explore())
