# Documentation Scraper

## Overview

The documentation scraper (`scrape_docs.py`) crawls docs.hevodata.com, extracts content, generates embeddings, and stores them in Pinecone for semantic search.

## Prerequisites

1. **Pinecone Index**: Create an index named "quickstart" in your Pinecone account
   - Dimension: 1536 (for text-embedding-3-small)
   - Metric: cosine

2. **Environment Variables**: Set in `.env` file:
   ```env
   PINECONE_API_KEY=your_pinecone_api_key
   PINECONE_INDEX_NAME=quickstart
   OPENAI_API_KEY=your_openai_api_key
   ```

## Usage

```bash
cd /Users/arunsunderraj/Documents/myScripts/zendesk_ticket_summarizer
source venv_new/bin/activate
python scripts/scrape_docs.py
```

## What It Does

1. Starts from https://docs.hevodata.com
2. Crawls documentation pages (up to 500 pages)
3. Extracts text content from HTML
4. Chunks content into ~500 token pieces
5. Generates embeddings using OpenAI text-embedding-3-small
6. Stores in Pinecone with metadata (URL, title, chunk_index)

## Configuration

Edit `scripts/scrape_docs.py` to adjust:
- `MAX_PAGES`: Maximum pages to scrape (default: 500)
- `CHUNK_SIZE`: Approximate tokens per chunk (default: 500)
- `DOCS_BASE_URL`: Starting URL (default: https://docs.hevodata.com)

## Notes

- The scraper respects rate limits (1 second delay between requests)
- Only scrapes docs.hevodata.com domain
- Skips non-HTML files (PDFs, images, etc.)
- Removes navigation, headers, footers from content

