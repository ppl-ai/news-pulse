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


# JavaScript extraction code — kept as a raw string to avoid escaping issues
JS_EXTRACT = r"""(topic) => {
    const selector = `a[href*="/discover/${topic}/"]`;
    const links = document.querySelectorAll(selector);
    const seen = new Set();
    const results = [];

    for (const link of links) {
        const href = link.getAttribute('href');
        if (!href) continue;

        const fullUrl = href.startsWith('http')
            ? href
            : 'https://www.perplexity.ai' + href;

        if (seen.has(fullUrl)) continue;
        seen.add(fullUrl);

        // --- Extract title ---
        // Strategy 1: img alt attribute (cleanest source)
        let title = '';
        const imgs = link.querySelectorAll('img[alt]');
        for (const img of imgs) {
            const alt = (img.alt || '').trim();
            if (alt.length > 15 && !alt.includes('favicon')) {
                title = alt;
                break;
            }
        }

        // Strategy 2: first leaf div with substantial text
        if (!title) {
            const allDivs = link.querySelectorAll('div');
            for (const div of allDivs) {
                if (div.children.length === 0) {
                    const txt = div.textContent.trim();
                    if (txt.length > 20
                        && !/^\d+ sources?$/.test(txt)
                        && txt !== 'Published') {
                        title = txt;
                        break;
                    }
                }
            }
        }

        // Strategy 3: split textContent on delimiters
        if (!title) {
            const raw = link.textContent.trim();
            const cleaned = raw.split(/Published|\d+ sources/)[0].trim();
            if (cleaned.length > 15) title = cleaned;
        }

        // --- Extract timestamp ---
        let timeAgo = '';
        const divs2 = link.querySelectorAll('div');
        for (const div of divs2) {
            const txt = div.textContent.trim();
            if (/^\d+\s*(minute|min|hour|hr|day|week|month)s?\s*ago$/i.test(txt)) {
                timeAgo = txt;
                break;
            }
        }

        // --- Extract source count ---
        let sourceCount = '';
        for (const div of divs2) {
            const txt = div.textContent.trim();
            if (/^\d+ sources?$/.test(txt)) {
                sourceCount = txt;
                break;
            }
        }

        if (title && title.length > 10) {
            results.push({
                title,
                url: fullUrl,
                timeAgo: timeAgo,
                sourceCount: sourceCount
            });
        }
    }
    return results;
}"""


def fetch_discover_stories():
    stories = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36"
        )

        for topic in ["top", "tech", "finance"]:
            url = f"https://www.perplexity.ai/discover/{topic}"
            print(f"Fetching {url}...")

            try:
                page.goto(url, wait_until="networkidle", timeout=45000)
                page.wait_for_selector(
                    f"a[href*='/discover/{topic}/']", timeout=20000
                )
                page.wait_for_timeout(2000)

                items = page.evaluate(JS_EXTRACT, topic)

                for item in items:
                    pub_date = parse_relative_time(item.get("timeAgo", ""))
                    stories.append({
                        "title": item["title"],
                        "url": item["url"],
                        "description": "",
                        "source": "Perplexity",
                        "topic": topic,
                        "pubDate": pub_date or datetime.now(timezone.utc).strftime(
                            "%Y-%m-%dT%H:%M:%SZ"
                        ),
                        "sourceCount": item.get("sourceCount", ""),
                    })

                print(f"  Found {len(items)} stories for {topic}")

            except Exception as e:
                print(f"  Error fetching {topic}: {e}", file=sys.stderr)

        browser.close()

    return stories


def main():
    stories = fetch_discover_stories()

    if not stories:
        cache_path = Path("perplexity_cache.json")
        if cache_path.exists():
            print(
                "No stories fetched. Keeping existing cache unchanged.",
                file=sys.stderr,
            )
            sys.exit(0)
        else:
            print("No stories fetched and no existing cache.", file=sys.stderr)
            sys.exit(1)

    # Build the cache structure
    topics = {"top": [], "tech": [], "finance": []}
    for s in stories:
        t = s.get("topic", "top")
        if t in topics:
            topics[t].append(s)

    cache = {
        "stories": stories,
        "cached_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "topics": topics,
    }

    with open("perplexity_cache.json", "w") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(stories)} stories to perplexity_cache.json")


if __name__ == "__main__":
    main()
