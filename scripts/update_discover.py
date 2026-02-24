#!/usr/bin/env python3
"""
Fetches current Perplexity Discover stories and writes perplexity_cache.json.
Runs via GitHub Actions on a schedule. Uses Playwright to render the SPA.
"""

import json
import sys
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright


def fetch_discover_stories():
    stories = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )

        for topic in ["top", "tech", "finance"]:
            url = f"https://www.perplexity.ai/discover/{topic}"
            print(f"Fetching {url}...")

            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
                # Wait for story cards to appear
                page.wait_for_selector("a[href*='/discover/']", timeout=15000)

                # Extract story links and titles from the page
                items = page.evaluate("""() => {
                    const links = document.querySelectorAll('a[href*="/discover/"]');
                    const seen = new Set();
                    const results = [];
                    for (const link of links) {
                        const href = link.getAttribute('href');
                        // Skip navigation links, only get story links
                        if (!href || href === '/discover' || href.match(/^\\/discover\\/(top|tech|finance|arts|sports)$/)) continue;
                        const fullUrl = href.startsWith('http') ? href : 'https://www.perplexity.ai' + href;
                        if (seen.has(fullUrl)) continue;
                        seen.add(fullUrl);
                        // Get the visible text as the title
                        const title = link.textContent.trim();
                        if (title && title.length > 10) {
                            results.push({ title, url: fullUrl });
                        }
                    }
                    return results;
                }""")

                for item in items:
                    stories.append({
                        "title": item["title"],
                        "url": item["url"],
                        "description": "",
                        "source": "Perplexity",
                        "topic": topic,
                    })

                print(f"  Found {len(items)} stories for {topic}")

            except Exception as e:
                print(f"  Error fetching {topic}: {e}", file=sys.stderr)

        browser.close()

    return stories


def main():
    stories = fetch_discover_stories()

    if not stories:
        print("No stories fetched. Keeping existing cache.", file=sys.stderr)
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
