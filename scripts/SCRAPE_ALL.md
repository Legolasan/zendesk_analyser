# Scraping All Documentation Pages

## Current Status

The scraper found **1,558 URLs** but only scraped **500 pages** due to the `MAX_PAGES` limit.

## How to Scrape More Pages

### Option 1: Set Environment Variable (Recommended)

```bash
cd zendesk_ticket_summarizer
source venv_new/bin/activate

# Scrape all pages (or set a specific number)
export MAX_PAGES=2000  # or 0 for unlimited
python scripts/scrape_docs.py
```

### Option 2: Scrape All Available Pages

```bash
# Set to 0 to remove limit (will scrape until no more URLs found)
export MAX_PAGES=0
python scripts/scrape_docs.py
```

### Option 3: Edit the Script Directly

Edit `scripts/scrape_docs.py` line 41:
```python
MAX_PAGES = 2000  # or 0 for unlimited
```

## Estimated Time

- **Current**: 500 pages in ~54 minutes
- **All pages (1,558)**: ~3-4 hours estimated
- **Rate**: ~6.4 seconds per page

## Resume Scraping

The scraper tracks visited URLs, so you can:
1. Run with higher `MAX_PAGES` limit
2. It will skip already scraped pages
3. Continue from where it left off

## Example: Scrape Remaining Pages

```bash
cd zendesk_ticket_summarizer
source venv_new/bin/activate
export MAX_PAGES=2000  # Higher limit to get remaining pages
python scripts/scrape_docs.py
```

The scraper will skip the 500 already-scraped pages and continue with the remaining ~1,058 pages.

