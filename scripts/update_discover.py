#!/usr/bin/env python3
"""
Fetches current Perplexity Discover stories and writes perplexity_cache.json.
Runs via GitHub Actions on a schedule. Uses Playwright to render the SPA.
"""

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright


def parse_relative_time(text):
    """Convert 'X minutes/hours/days ago' to an ISO timestamp."""
    if not text:
        return None
    text = text.strip().lower()
    now = datetime.now(timezone.utc)

    m = re.search(r'(\d+)\s*(minute|min|hour|hr|day|week|month)', text)
    if not m:
        return None

    val = int(m.group(1))
    unit = m.group(2)

    if unit in ('minute', 'min'):
        dt = now - timedelta(minutes=val)
    elif unit in ('hour', 'hr'):
        dt = now - timedelta(hours=val)
    elif unit == 'day':
        dt = now - timedelta(days=val)
    elif unit == 'week':
        dt = now - timedelta(weeks=val)
    elif unit == 'month':
        dt = now - timedelta(days=val * 30)
    else:
        return None

    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# JavaScript extraction code — kept as a raw string to avoid escaping issues.
# Accepts both topic-specific selectors and a broad fallback.
JS_EXTRACT = r"""(topic) => {
    // Primary selector: links containing /discover/<topic>/
    let selector = `a[href*="/discover/${topic}/"]`;
    let links = document.querySelectorAll(selector);

    // Fallback: if no topic-specific links found, grab ALL discover story links
    // This handles cases where /discover/top renders links as /discover/<slug>
    if (links.length === 0) {
        links = document.querySelectorAll('a[href*="/discover/"]');
    }

    const seen = new Set();
    const results = [];

    for (const link of links) {
        const href = link.getAttribute('href');
        if (!href) continue;

        // Deduplicate by href
        if (seen.has(href)) continue;
        seen.add(href);

        // Walk up to find a card-like container
        let card = link;
        for (let i = 0; i < 6; i++) {
            if (!card.parentElement) break;
            card = card.parentElement;
            if (card.querySelectorAll('a[href]').length >= 1) break;
        }

        // Title: prefer <h1-h3> inside the card, else link text
        const heading = card.querySelector('h1, h2, h3, h4');
        let title = heading ? heading.innerText.trim() : link.innerText.trim();
        if (!title) continue;

        // Source / byline
        const sourceEl = card.querySelector('[class*="source"], [class*="byline"], [class*="publisher"]');
        const source = sourceEl ? sourceEl.innerText.trim() : '';

        // Relative timestamp text
        const timeEl = card.querySelector('time, [class*="time"], [class*="ago"], [class*="date"]');
        const timeText = timeEl ? (timeEl.getAttribute('datetime') || timeEl.innerText.trim()) : '';

        // Source count
        const countEl = card.querySelector('[class*="source-count"], [class*="sourceCount"], [class*="count"]');
        const sourceCount = countEl ? countEl.innerText.trim() : '';

        results.push({ title, href, source, timeText, sourceCount });
    }
    return results;
}"""


def scroll_and_wait(page, scrolls=4, delay=800):
    """Scroll down to trigger lazy-loaded content."""
    for _ in range(scrolls):
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        page.wait_for_timeout(delay)
    # Scroll back to top
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(300)


def fetch_topic(page, topic: str, max_retries: int = 3) -> list[dict]:
    """
    Navigate to /discover/<topic> and extract stories.
    Retries up to max_retries times with increasing wait.
    """
    url = f"https://www.perplexity.ai/discover/{topic}"

    for attempt in range(1, max_retries + 1):
        try:
            print(f"  [{topic}] attempt {attempt}: loading {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2500)

            # Scroll to trigger lazy loading
            scroll_and_wait(page, scrolls=3, delay=700)

            # Try primary extraction
            raw = page.evaluate(JS_EXTRACT, topic)

            if not raw:
                print(f"  [{topic}] attempt {attempt}: no results from primary selector, trying broader…")
                # Broader fallback: grab all discover links regardless of topic
                raw = page.evaluate(
                    """() => {
                        const links = document.querySelectorAll('a[href*="/discover/"]');
                        const seen = new Set();
                        const results = [];
                        for (const link of links) {
                            const href = link.getAttribute('href');
                            if (!href || seen.has(href)) continue;
                            seen.add(href);
                            let card = link;
                            for (let i = 0; i < 6; i++) {
                                if (!card.parentElement) break;
                                card = card.parentElement;
                                if (card.querySelectorAll('a[href]').length >= 1) break;
                            }
                            const heading = card.querySelector('h1,h2,h3,h4');
                            const title = heading ? heading.innerText.trim() : link.innerText.trim();
                            if (!title) continue;
                            const timeEl = card.querySelector('time,[class*="time"],[class*="ago"],[class*="date"]');
                            const timeText = timeEl ? (timeEl.getAttribute('datetime') || timeEl.innerText.trim()) : '';
                            const countEl = card.querySelector('[class*="source-count"],[class*="sourceCount"],[class*="count"]');
                            const sourceCount = countEl ? countEl.innerText.trim() : '';
                            results.push({ title, href, source: '', timeText, sourceCount });
                        }
                        return results;
                    }"""
                )

            if raw:
                print(f"  [{topic}] attempt {attempt}: got {len(raw)} raw items")
                return raw

            wait_ms = attempt * 3000
            print(f"  [{topic}] attempt {attempt}: still empty, waiting {wait_ms}ms before retry…")
            page.wait_for_timeout(wait_ms)

        except Exception as exc:
            print(f"  [{topic}] attempt {attempt} error: {exc}")
            if attempt < max_retries:
                page.wait_for_timeout(attempt * 3000)

    print(f"  [{topic}] all retries exhausted, returning empty list")
    return []


def build_stories(raw_items: list[dict], topic: str, limit: int = 20) -> list[dict]:
    """
    Convert raw JS extraction results into clean story dicts.
    Stagger fallback timestamps so they're not all identical.
    """
    stories = []
    now = datetime.now(timezone.utc)

    for idx, item in enumerate(raw_items[:limit]):
        title = item.get("title", "").strip()
        href = item.get("href", "").strip()
        if not title or not href:
            continue

        # Build full URL
        if href.startswith("http"):
            url = href
        else:
            url = f"https://www.perplexity.ai{href}"

        # Parse timestamp — stagger fallback by 5-min increments to avoid duplicates
        time_text = item.get("timeText", "")
        pub_date = parse_relative_time(time_text)
        if not pub_date:
            staggered = now - timedelta(minutes=idx * 5)
            pub_date = staggered.strftime("%Y-%m-%dT%H:%M:%SZ")

        source_count = item.get("sourceCount", "").strip()
        source = item.get("source", "").strip()

        stories.append({
            "title": title,
            "url": url,
            "source": source,
            "pubDate": pub_date,
            "sourceCount": source_count,
        })

    return stories


def main():
    topics = ["top", "tech", "finance"]
    all_stories = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        for topic in topics:
            print(f"\nFetching /discover/{topic} …")
            raw = fetch_topic(page, topic, max_retries=3)
            stories = build_stories(raw, topic, limit=20)
            print(f"  -> {len(stories)} stories for {topic}")
            all_stories.extend(stories)

        browser.close()

    # Write output
    output = {"stories": all_stories}
    out_path = Path("perplexity_cache.json")
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"Wrote {len(stories)} stories to perplexity_cache.json")


if __name__ == "__main__":
    main()
