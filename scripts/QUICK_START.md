# Quick Start - Documentation Scraper

## Step 1: Create Pinecone Index

```python
from pinecone import Pinecone, ServerlessSpec

pc = Pinecone(api_key="pcsk_339VMc_3fF2iGeefNdKNSionNQC3dmNvzsAJTAft3ZdrZ94UmspP1SaTqNyaQPeYyDj7ui")
pc.create_index(
    name="quickstart",
    dimension=1536,  # Required for text-embedding-3-small
    metric="cosine",
    spec=ServerlessSpec(cloud="aws", region="us-east-1")
)
```

## Step 2: Run the Scraper

```bash
cd zendesk_ticket_summarizer
source venv_new/bin/activate
python scripts/scrape_docs.py
```

## Step 3: Monitor Progress

**During scraping**, you'll see:
- `[X/500] (X.X%)` - Page progress
- `✓ Stored X vectors | Total: X vectors` - Storage progress
- `⏱️ Elapsed: X min | Est. remaining: X min` - Time estimates

**To check status anytime:**
```bash
python scripts/check_progress.py
```

## Step 4: Verify Completion

When done, you'll see:
```
======================================================================
SCRAPING COMPLETE!
======================================================================
Pages Scraped: X
Total Vectors Stored: X
Total Duration: X minutes
======================================================================
✅ Scraping and embedding complete!
```

## Troubleshooting

**Index not found?**
- Create the index first (Step 1)
- Verify index name matches: `quickstart`

**No progress shown?**
- Check that scraper is running (look for progress messages)
- Verify Pinecone API key is correct
- Check network connectivity

**Want to see progress in a file?**
```bash
python scripts/scrape_docs.py 2>&1 | tee scrape_progress.log
```

