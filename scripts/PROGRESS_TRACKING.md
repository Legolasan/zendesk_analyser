# Progress Tracking Guide

## How to Track Scraper Progress

The scraper now includes detailed progress tracking. Here's how to monitor it:

### 1. **Real-Time Progress During Scraping**

When you run the scraper, you'll see detailed progress output:

```bash
python scripts/scrape_docs.py
```

**Output Example:**
```
======================================================================
DOCUMENTATION SCRAPER - Progress Tracker
======================================================================
Starting URL: https://docs.hevodata.com
Pinecone Index: quickstart
Max Pages: 500
Start Time: 2025-11-28 12:00:00
======================================================================

[0/500] (0.0%) Processing: https://docs.hevodata.com
  ✓ Scraped: 15234 chars → 3 chunks
    Generating embedding 1/3...
    Generating embedding 2/3...
    Generating embedding 3/3...
    Storing batch 1/1 (3 vectors)...
  ✓ Stored 3 vectors | Total: 3 vectors
  ⏱️  Elapsed: 0.1 min | Est. remaining: 83.3 min
  🔗 Found 15 new links to crawl (queue: 15 URLs)

[1/500] (0.2%) Processing: https://docs.hevodata.com/getting-started
  ✓ Scraped: 8921 chars → 2 chunks
    Generating embedding 1/2...
    Generating embedding 2/2...
    Storing batch 1/1 (2 vectors)...
  ✓ Stored 2 vectors | Total: 5 vectors
  ⏱️  Elapsed: 0.3 min | Est. remaining: 82.1 min
```

### 2. **Progress Indicators**

The scraper shows:
- **Page Progress**: `[X/500] (X.X%)` - Current page and percentage
- **Chunks Created**: Number of text chunks from each page
- **Embedding Progress**: `Generating embedding X/Y...`
- **Storage Progress**: `Storing batch X/Y...`
- **Total Vectors**: Running total of vectors stored
- **Time Tracking**: Elapsed time and estimated remaining time
- **Link Discovery**: New links found and queue size

### 3. **Completion Summary**

When scraping completes, you'll see a summary:

```
======================================================================
SCRAPING COMPLETE!
======================================================================
Pages Scraped: 127
Total URLs Visited: 127
Total Chunks Created: 342
Total Vectors Stored: 342
Start Time: 2025-11-28 12:00:00
End Time: 2025-11-28 12:45:23
Total Duration: 45.4 minutes (2724 seconds)
Average Time per Page: 21.4 seconds
======================================================================

Verifying Pinecone index...
✓ Index Stats:
  Total Vectors: 342
  Index Fullness: 0.15%
======================================================================

✅ Scraping and embedding complete!
```

### 4. **Check Progress Anytime**

Use the progress checker script to see current status:

```bash
python scripts/check_progress.py
```

**Output:**
```
======================================================================
PINECONE INDEX STATUS
======================================================================
Index Name: quickstart

Index Statistics:
  Total Vectors: 342
  Index Fullness: 0.15%

Namespaces:
  '': 342 vectors

Index Configuration:
  Dimension: 1536
  Metric: cosine
  Pod Type: s1.x1

Sampling stored vectors...
  Found 3 sample vectors:
    1. URL: https://docs.hevodata.com/getting-started
       Title: Getting Started with Hevo Data...
       Chunk: 0
    2. URL: https://docs.hevodata.com/sources
       Title: Data Sources Documentation...
       Chunk: 0
    3. URL: https://docs.hevodata.com/destinations
       Title: Data Destinations...
       Chunk: 0

======================================================================
✅ Index is accessible and ready to use!
======================================================================
```

### 5. **Monitoring Tips**

**During Scraping:**
- Watch the progress percentage: `[X/500] (X.X%)`
- Monitor elapsed time vs estimated remaining
- Check total vectors stored (should increase with each page)
- Look for errors (marked with ⚠️)

**After Scraping:**
- Run `check_progress.py` to verify storage
- Check total vectors match expected count
- Verify sample vectors show correct URLs

### 6. **Troubleshooting**

**If scraper stops:**
- Check for error messages (marked with ❌ or ⚠️)
- Verify Pinecone API key is correct
- Ensure index exists and has correct dimension (1536)
- Check network connectivity

**If vectors not storing:**
- Check Pinecone index status
- Verify API key has write permissions
- Check for rate limiting errors
- Review error messages in output

**To resume scraping:**
- The scraper tracks visited URLs, so you can restart it
- It will skip already visited pages
- To start fresh, clear the `visited_urls` set in code

### 7. **Expected Performance**

- **Time per page**: ~20-30 seconds (includes scraping, chunking, embedding, storage)
- **Chunks per page**: 1-5 chunks (depends on page length)
- **Total time for 500 pages**: ~2-4 hours
- **Vectors created**: ~500-2000 vectors (depends on content)

### 8. **Progress Log File** (Optional)

To save progress to a file:

```bash
python scripts/scrape_docs.py 2>&1 | tee scrape_progress.log
```

This saves all output to `scrape_progress.log` while still showing it on screen.

---

## Quick Commands

**Start scraping:**
```bash
cd zendesk_ticket_summarizer
source venv_new/bin/activate
python scripts/scrape_docs.py
```

**Check current status:**
```bash
python scripts/check_progress.py
```

**Monitor in real-time:**
```bash
# In another terminal
watch -n 5 'python scripts/check_progress.py'
```

