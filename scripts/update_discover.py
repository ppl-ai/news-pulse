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


# JavaScript extraction code
JS_EXTRACT = r"""(topic) => {
    // Primary selector: links containing /discover/<topic>/
    let selector = `a[href*="/discover/${topic}/"]`;
    let links = document.querySelectorAll(selector);

    // Fallback: if no topic-specific links found, grab ALL discover story links
    if (links.length === 0) {
        links = document.querySelectorAll('a[href*="/discover/"]');
    }

    const seen = new Set();
    const results = [];

    for (const link of links) {
        const href = link.getAttribute('href');
        if (!href) continue;

        // Skip non-story links (navigation links to topic pages)
        if (/^(\/|https:\/\/www\.perplexity\.ai)?\/discover\/(top|tech|finance|you|trending)?\/?$/.test(href)) continue;

        const fullUrl = href.startsWith('http')
            ? href
            : 'https://www.perplexity.ai' + href;

        if (seen.has(fullUrl)) continue;
        seen.add(fullUrl);

        // --- Extract title ---
        let title = '';
        const imgs = link.querySelectorAll('img[alt]');
        for (const img of imgs) {
            const alt = (img.alt || '').trim();
            if (alt.length > 15 && !alt.includes('favicon')) {
                title = alt;
                break;
            }
        }

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


def fetch_topic(page, topic, max_retries=2):
    """Fetch stories for a single topic with retries."""
    url = f"https://www.perplexity.ai/discover/{topic}"

    for attempt in range(max_retries + 1):
        try:
            print(f"  Attempt {attempt + 1} for {topic}...")
            page.goto(url, wait_until="networkidle", timeout=45000)

            try:
                page.wait_for_selector(
                    f"a[href*='/discover/{topic}/']", timeout=15000
                )
            except Exception:
                print(f"    Topic-specific selector failed, trying broad selector...")
                try:
                    page.wait_for_selector(
                        "a[href*='/discover/'] img[alt]", timeout=15000
                    )
                except Exception:
                    print(f"    Broad selector also failed, scrolling...")
                    page.mouse.wheel(0, 1000)
                    page.wait_for_timeout(3000)

            page.wait_for_timeout(2000)
            items = page.evaluate(JS_EXTRACT, topic)
            print(f"    Found {len(items)} stories")

            if items:
                return items

            if attempt < max_retries:
                print(f"    No items found, retrying...")
                page.wait_for_timeout(2000)

        except Exception as e:
            print(f"    Error: {e}", file=sys.stderr)
            if attempt < max_retries:
                page.wait_for_timeout(3000)

    return []


def fetch_discover_stories():
    stories = []
    now = datetime.now(timezone.utc)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = context.new_page()

        for topic in ["top", "tech", "finance"]:
            print(f"Fetching /discover/{topic}...")
            items = fetch_topic(page, topic)

            for i, item in enumerate(items):
                pub_date = parse_relative_time(item.get("timeAgo", ""))
                if not pub_date:
                    offset = 30 + (i * 15)
                    dt = now - timedelta(minutes=offset)
                    pub_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

                stories.append({
                    "title": item["title"],
                    "url": item["url"],
                    "description": "",
                    "source": "Perplexity",
                    "topic": topic,
                    "pubDate": pub_date,
                    "sourceCount": item.get("sourceCount", ""),
                })

            print(f"  Total for {topic}: {len([s for s in stories if s['topic'] == topic])}")

        browser.close()

    return stories


# Minimum stories required to overwrite cache.
# Prevents bad scrapes from clobbering good data.
MIN_STORIES_TO_WRITE = 30


def main():
    cache_path = Path("perplexity_cache.json")

    # Load existing cache to compare
    existing_count = 0
    if cache_path.exists():
        try:
            with open(cache_path) as f:
                existing = json.load(f)
            existing_count = len(existing.get("stories", []))
        except Exception:
            pass

    stories = fetch_discover_stories()

    from collections import Counter
    topic_counts = Counter(s["topic"] for s in stories)
    print(f"Scraped: {len(stories)} stories — {dict(topic_counts)}")
    print(f"Existing cache: {existing_count} stories")

    if not stories:
        print("No stories fetched. Keeping existing cache.", file=sys.stderr)
        sys.exit(0)

    # SAFETY: Don't overwrite a good cache with a worse scrape
    if len(stories) < MIN_STORIES_TO_WRITE:
        print(
            f"Only {len(stories)} stories (need {MIN_STORIES_TO_WRITE}+). "
            f"Keeping existing cache with {existing_count} stories.",
            file=sys.stderr,
        )
        sys.exit(0)

    if existing_count > 0 and len(stories) < existing_count * 0.5:
        print(
            f"New scrape ({len(stories)}) is less than half of existing "
            f"({existing_count}). Keeping existing cache.",
            file=sys.stderr,
        )
        sys.exit(0)

    # Build the cache
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

    with open(cache_path, "w") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(stories)} stories to perplexity_cache.json")


if __name__ == "__main__":
    main()
