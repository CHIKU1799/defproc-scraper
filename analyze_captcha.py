"""
Analyze the captcha images and find network requests pattern.
Also check if there's a direct image URL for captcha (vs embedded base64).
"""
import asyncio
import base64
import re
import json
from pathlib import Path
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

BASE_URL = "https://defproc.gov.in/nicgep/app"


async def analyze():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        # Capture ALL network requests
        all_requests = []
        all_responses = []

        page.on("request", lambda req: all_requests.append({
            "method": req.method,
            "url": req.url,
            "resource_type": req.resource_type,
            "headers": dict(req.headers),
            "post_data": req.post_data,
        }))

        page.on("response", lambda resp: all_responses.append({
            "url": resp.url,
            "status": resp.status,
            "content_type": resp.headers.get("content-type", ""),
        }))

        # Visit active tenders page
        print("[*] Loading Active Tenders page...")
        await page.goto(f"{BASE_URL}?page=FrontEndLatestActiveTenders&service=page",
                        wait_until="networkidle", timeout=60_000)
        await page.wait_for_timeout(2000)

        print(f"\n[*] Network requests ({len(all_requests)}):")
        for req in all_requests:
            print(f"    [{req['method']}] {req['url'][:100]}")
            if req["post_data"]:
                print(f"           POST: {req['post_data'][:200]}")

        # Check if captcha is a separate URL
        captcha_reqs = [r for r in all_requests if "captcha" in r["url"].lower() or
                        "image" in r.get("resource_type", "")]
        print(f"\n[*] Captcha-related requests: {len(captcha_reqs)}")
        for r in captcha_reqs:
            print(f"    {r['url']}")

        # Extract captcha from HTML
        html = await page.content()
        m = re.search(r'src="data:image/png;base64,([^"]+)"', html)
        if m:
            img_b64 = m.group(1).replace('\n', '').replace(' ', '')
            print(f"\n[*] Captcha is embedded base64, length={len(img_b64)}")

            # Save multiple captcha images to understand variation
            img_bytes = base64.b64decode(img_b64)
            Path("screenshots/captcha_01.png").write_bytes(img_bytes)
            print("    Saved: screenshots/captcha_01.png")

        # Refresh to get 3 more captchas
        for i in range(2, 5):
            await page.reload(wait_until="networkidle")
            await page.wait_for_timeout(1000)
            html2 = await page.content()
            m2 = re.search(r'src="data:image/png;base64,([^"]+)"', html2)
            if m2:
                img2_b64 = m2.group(1).replace('\n', '').replace(' ', '')
                img2_bytes = base64.b64decode(img2_b64)
                Path(f"screenshots/captcha_0{i}.png").write_bytes(img2_bytes)
                print(f"    Saved: screenshots/captcha_0{i}.png")

        # Also try clicking the "Refresh" button for captcha
        refresh_btn = await page.query_selector('#captcha, button[name="captcha"]')
        if refresh_btn:
            await refresh_btn.click()
            await page.wait_for_timeout(1000)
            html3 = await page.content()
            m3 = re.search(r'src="data:image/png;base64,([^"]+)"', html3)
            if m3:
                img3_b64 = m3.group(1).replace('\n', '').replace(' ', '')
                img3_bytes = base64.b64decode(img3_b64)
                Path("screenshots/captcha_refresh.png").write_bytes(img3_bytes)
                print("    Saved: screenshots/captcha_refresh.png (after Refresh click)")

        # ── Also test POSTING directly without captcha ─────────────────────
        print("\n[*] Testing direct POST to results endpoint (no captcha)...")
        all_requests.clear()

        # Try calling results page URL directly
        resp2 = await page.goto(
            f"{BASE_URL}?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct",
            wait_until="networkidle", timeout=30_000
        )
        print(f"    Direct results URL status: {resp2.status}")
        print(f"    Final URL: {page.url}")
        html4 = await page.content()
        # Check if it shows results or redirects to captcha
        has_captcha = "captchaText" in html4
        has_results = "list_table" in html4 or "TenderTitle" in html4 or "Tender ID" in html4
        print(f"    Has captcha: {has_captcha}, Has results: {has_results}")
        Path("screenshots/direct_result.html").write_text(html4)

        # Check what the "Tenders by Closing Date" page looks like
        print("\n[*] Inspecting 'Tenders by Closing Date' page...")
        await page.goto(f"{BASE_URL}?page=FrontEndListTendersbyDate&service=page",
                        wait_until="networkidle", timeout=60_000)
        html5 = await page.content()
        has_captcha5 = "captchaText" in html5
        print(f"    Has captcha: {has_captcha5}")

        # Does the page have any AJAX calls we can intercept?
        print("\n[*] Looking for XHR/fetch patterns on the page...")
        js_patterns = re.findall(r'(fetch|XMLHttpRequest|ajax|\.get\(|\.post\(|url\s*:\s*["\'])[^;]{0,100}', html5)
        for p in js_patterns[:10]:
            print(f"    {p!r}")

        await browser.close()
        print("\n[✓] Captcha analysis complete.")


if __name__ == "__main__":
    asyncio.run(analyze())
