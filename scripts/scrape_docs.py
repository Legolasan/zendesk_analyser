"""
Documentation Scraper for docs.hevodata.com
Scrapes documentation pages, chunks content, generates embeddings, and stores in Pinecone.
"""
import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
import json
from typing import List, Dict
from openai import OpenAI
from pinecone import Pinecone, ServerlessSpec
from dotenv import load_dotenv

load_dotenv()

# Configuration
DOCS_BASE_URL = "https://docs.hevodata.com"
PINECONE_API_KEY = os.environ.get('PINECONE_API_KEY', 'pcsk_339VMc_3fF2iGeefNdKNSionNQC3dmNvzsAJTAft3ZdrZ94UmspP1SaTqNyaQPeYyDj7ui')
PINECONE_INDEX_NAME = os.environ.get('PINECONE_INDEX_NAME', 'quickstart')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

# Initialize clients
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
pc = None
index = None

try:
    pc = Pinecone(api_key=PINECONE_API_KEY)
    index = pc.Index(PINECONE_INDEX_NAME)
except Exception as e:
    print(f"Warning: Could not initialize Pinecone: {str(e)}")
    print("Please ensure:")
    print(f"  1. PINECONE_API_KEY is set in environment")
    print(f"  2. Index '{PINECONE_INDEX_NAME}' exists")
    print(f"  3. Index has dimension 1536 (for text-embedding-3-small)")

# Track visited URLs
visited_urls = set()
# Configurable limit - set MAX_PAGES environment variable to change (default: 500)
# Set to 0 or very high number to scrape all available pages
MAX_PAGES = int(os.environ.get('MAX_PAGES', '500'))  # Limit to prevent infinite crawling
CHUNK_SIZE = 500  # Approximate tokens per chunk


def is_valid_url(url: str) -> bool:
    """Check if URL is valid for scraping."""
    parsed = urlparse(url)
    # Only scrape docs.hevodata.com pages
    if parsed.netloc not in ['docs.hevodata.com', 'www.docs.hevodata.com']:
        return False
    # Skip non-HTML files
    if any(url.endswith(ext) for ext in ['.pdf', '.zip', '.jpg', '.png', '.gif', '.css', '.js']):
        return False
    return True


def extract_text_from_html(html_content: str, url: str) -> Dict:
    """Extract text content from HTML."""
    from datetime import datetime
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Remove script and style elements
    for script in soup(["script", "style", "nav", "footer", "header"]):
        script.decompose()
    
    # Extract title
    title = soup.find('title')
    title_text = title.get_text().strip() if title else url
    
    # Extract section from URL or breadcrumbs
    section = ""
    try:
        # Try to get section from breadcrumbs
        breadcrumbs = soup.find_all(['nav', 'ol', 'ul'], class_=lambda x: x and ('breadcrumb' in x.lower() or 'nav' in x.lower()))
        if breadcrumbs:
            section = ' > '.join([a.get_text().strip() for a in breadcrumbs[0].find_all('a')[:3]])
        
        # Fallback: extract section from URL path
        if not section:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            path_parts = [p for p in parsed.path.split('/') if p]
            if len(path_parts) > 1:
                section = ' > '.join(path_parts[:2])  # First two path segments
    except:
        pass
    
    # Extract last_updated from meta tags or use current time
    last_updated = datetime.now().isoformat()
    try:
        # Try to find last modified date in meta tags
        meta_date = soup.find('meta', {'property': 'article:modified_time'}) or \
                    soup.find('meta', {'name': 'last-modified'}) or \
                    soup.find('time', {'datetime': True})
        if meta_date:
            if meta_date.get('content'):
                last_updated = meta_date.get('content')
            elif meta_date.get('datetime'):
                last_updated = meta_date.get('datetime')
    except:
        pass
    
    # Extract main content
    main_content = soup.find('main') or soup.find('article') or soup.find('body')
    if main_content:
        text = main_content.get_text(separator='\n', strip=True)
    else:
        text = soup.get_text(separator='\n', strip=True)
    
    # Clean up text
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    text = '\n'.join(lines)
    
    return {
        'title': title_text,
        'text': text,
        'url': url,
        'section': section,
        'last_updated': last_updated
    }


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> List[str]:
    """Split text into chunks of approximately chunk_size tokens."""
    # Estimate tokens: ~4 chars per token, but be conservative
    max_chars = chunk_size * 3  # Conservative estimate
    
    # Simple chunking by sentences and paragraphs
    paragraphs = text.split('\n\n')
    chunks = []
    current_chunk = []
    current_length = 0
    
    for para in paragraphs:
        para_length = len(para.split())  # Approximate token count
        para_chars = len(para)
        
        # If single paragraph is too large, split it further
        if para_chars > max_chars:
            # Split large paragraph by sentences
            sentences = para.split('. ')
            for sentence in sentences:
                sent_length = len(sentence.split())
                sent_chars = len(sentence)
                
                if current_length + sent_length > chunk_size and current_chunk:
                    chunks.append('\n\n'.join(current_chunk))
                    current_chunk = [sentence]
                    current_length = sent_length
                elif sent_chars > max_chars:
                    # Even sentence is too large, split by words
                    words = sentence.split()
                    word_chunk = []
                    word_length = 0
                    for word in words:
                        if word_length + 1 > chunk_size and word_chunk:
                            if current_chunk:
                                chunks.append('\n\n'.join(current_chunk))
                            current_chunk = [' '.join(word_chunk)]
                            current_length = word_length
                            word_chunk = [word]
                            word_length = 1
                        else:
                            word_chunk.append(word)
                            word_length += 1
                    if word_chunk:
                        current_chunk.append(' '.join(word_chunk))
                        current_length += word_length
                else:
                    current_chunk.append(sentence)
                    current_length += sent_length
        elif current_length + para_length > chunk_size and current_chunk:
            chunks.append('\n\n'.join(current_chunk))
            current_chunk = [para]
            current_length = para_length
        else:
            current_chunk.append(para)
            current_length += para_length
    
    if current_chunk:
        chunks.append('\n\n'.join(current_chunk))
    
    # Ensure no chunk exceeds token limit (8000 tokens = ~32000 chars max)
    final_chunks = []
    for chunk in chunks:
        if len(chunk) > 30000:  # Safety limit for embedding API
            # Split oversized chunks
            words = chunk.split()
            temp_chunk = []
            temp_length = 0
            for word in words:
                if temp_length + len(word) > 30000 and temp_chunk:
                    final_chunks.append(' '.join(temp_chunk))
                    temp_chunk = [word]
                    temp_length = len(word)
                else:
                    temp_chunk.append(word)
                    temp_length += len(word)
            if temp_chunk:
                final_chunks.append(' '.join(temp_chunk))
        else:
            final_chunks.append(chunk)
    
    return final_chunks


def generate_embedding(text: str) -> List[float]:
    """Generate embedding for text using OpenAI."""
    try:
        response = openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=text
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"Error generating embedding: {str(e)}")
        return None


def find_all_links(html_content: str, base_url: str) -> List[str]:
    """Find all links on the page."""
    soup = BeautifulSoup(html_content, 'html.parser')
    links = []
    
    for link in soup.find_all('a', href=True):
        href = link['href']
        absolute_url = urljoin(base_url, href)
        # Remove fragments
        absolute_url = absolute_url.split('#')[0]
        if is_valid_url(absolute_url) and absolute_url not in visited_urls:
            links.append(absolute_url)
    
    return links


def scrape_page(url: str) -> Dict:
    """Scrape a single page and return content."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        content = extract_text_from_html(response.text, url)
        return content
    except Exception as e:
        return None


def store_in_pinecone(chunks: List[Dict], url: str, title: str, content: Dict = None, progress_callback=None):
    """Store chunks in Pinecone.
    
    Args:
        chunks: List of text chunks to store
        url: URL of the page
        title: Title of the page
        content: Optional content dict with section and last_updated
        progress_callback: Optional callback for progress updates
    """
    if not index:
        if progress_callback:
            progress_callback("  ⚠️  Pinecone index not available, skipping storage")
        return 0
    
    vectors = []
    total_chunks = len(chunks)
    
    # Extract section and last_updated from content if provided
    section = content.get('section', '') if content else ''
    last_updated = content.get('last_updated', '') if content else ''
    
    for i, chunk in enumerate(chunks):
        if progress_callback:
            progress_callback(f"  Generating embedding {i+1}/{total_chunks}...")
        embedding = generate_embedding(chunk['text'])
        if embedding:
            vector_id = f"{url.replace('https://', '').replace('http://', '').replace('/', '_')}_{i}"
            # Sanitize vector ID (Pinecone has restrictions)
            vector_id = vector_id.replace(':', '_').replace('?', '_').replace('&', '_').replace('=', '_')
            if len(vector_id) > 100:  # Limit ID length
                vector_id = vector_id[:100]
            
            metadata = {
                'url': url,
                'title': title[:500] if title else '',  # Limit title length
                'section': section[:200] if section else '',  # Limit section length
                'last_updated': last_updated[:50] if last_updated else '',
                'chunk_index': i,
                'text': chunk['text'][:1000]  # Store first 1000 chars for reference
            }
            vectors.append({
                'id': vector_id,
                'values': embedding,
                'metadata': metadata
            })
    
    if vectors:
        try:
            # Upsert in batches of 100
            batch_size = 100
            total_batches = (len(vectors) + batch_size - 1) // batch_size
            for i in range(0, len(vectors), batch_size):
                batch = vectors[i:i+batch_size]
                batch_num = (i // batch_size) + 1
                if progress_callback:
                    progress_callback(f"  Storing batch {batch_num}/{total_batches} ({len(batch)} vectors)...")
                index.upsert(vectors=batch)
            return len(vectors)
        except Exception as e:
            if progress_callback:
                progress_callback(f"  ❌ Error storing in Pinecone: {str(e)}")
            return 0
    return 0


def scrape_documentation(start_url: str = None):
    """Main function to scrape documentation."""
    import sys
    from datetime import datetime
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    from scraper_status import update_status, get_status
    
    if start_url is None:
        start_url = DOCS_BASE_URL
    
    to_visit = [start_url]
    visited_urls.add(start_url)
    pages_scraped = 0
    total_chunks = 0
    total_vectors_stored = 0
    start_time = datetime.now()
    
    # Initialize status
    update_status(
        status='running',
        start_time=start_time.isoformat(),
        pages_scraped=0,
        total_vectors=0,
        total_chunks=0
    )
    
    print("=" * 70)
    print("DOCUMENTATION SCRAPER - Progress Tracker")
    print("=" * 70)
    print(f"Starting URL: {start_url}")
    print(f"Pinecone Index: {PINECONE_INDEX_NAME}")
    print(f"Max Pages: {MAX_PAGES}")
    print(f"Start Time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print()
    
    while to_visit and pages_scraped < MAX_PAGES:
        current_url = to_visit.pop(0)
        
        # Progress indicator
        progress_pct = (pages_scraped / MAX_PAGES) * 100 if MAX_PAGES > 0 else 0
        print(f"[{pages_scraped}/{MAX_PAGES}] ({progress_pct:.1f}%) Processing: {current_url}")
        
        # Update status file (before scraping, so we show current URL)
        try:
            from scraper_status import update_status
            elapsed = (datetime.now() - start_time).total_seconds()
            avg_time_per_page = elapsed / pages_scraped if pages_scraped > 0 else 0
            estimated_remaining = (MAX_PAGES - pages_scraped) * avg_time_per_page / 60 if pages_scraped > 0 else None
            update_status(
                status='running',
                pages_scraped=pages_scraped,
                total_vectors=total_vectors_stored,
                total_chunks=total_chunks,
                current_url=current_url[:100],  # Limit URL length
                progress_percentage=progress_pct,
                estimated_remaining_minutes=estimated_remaining
            )
        except Exception as e:
            print(f"  Warning: Could not update status: {str(e)}")
        
        # Scrape page
        content = scrape_page(current_url)
        if not content or not content.get('text'):
            print(f"  ⚠️  Skipped (no content)")
            continue
        
        # Chunk content
        chunks_text = chunk_text(content['text'])
        chunks = [{'text': chunk} for chunk in chunks_text]
        num_chunks = len(chunks)
        total_chunks += num_chunks
        
        print(f"  ✓ Scraped: {len(content['text'])} chars → {num_chunks} chunks")
        
        # Store in Pinecone with progress callback
        def progress_msg(msg):
            print(f"    {msg}")
        
        vectors_stored = store_in_pinecone(chunks, current_url, content['title'], content, progress_callback=progress_msg)
        total_vectors_stored += vectors_stored
        
        pages_scraped += 1
        elapsed = (datetime.now() - start_time).total_seconds()
        avg_time_per_page = elapsed / pages_scraped if pages_scraped > 0 else 0
        estimated_remaining = (MAX_PAGES - pages_scraped) * avg_time_per_page if pages_scraped > 0 else 0
        
        print(f"  ✓ Stored {vectors_stored} vectors | Total: {total_vectors_stored} vectors")
        print(f"  ⏱️  Elapsed: {elapsed/60:.1f} min | Est. remaining: {estimated_remaining/60:.1f} min")
        print()
        
        # Find links for further crawling
        if pages_scraped < MAX_PAGES:
            try:
                response = requests.get(current_url, timeout=30)
                links = find_all_links(response.text, current_url)
                new_links = 0
                for link in links[:10]:  # Limit links per page
                    if link not in visited_urls and is_valid_url(link):
                        to_visit.append(link)
                        visited_urls.add(link)
                        new_links += 1
                if new_links > 0:
                    print(f"  🔗 Found {new_links} new links to crawl (queue: {len(to_visit)} URLs)")
            except Exception as e:
                print(f"  ⚠️  Error finding links: {str(e)}")
        
        # Rate limiting
        time.sleep(1)
    
    end_time = datetime.now()
    total_time = (end_time - start_time).total_seconds()
    
    # Update final status
    try:
        from scraper_status import update_status
        update_status(
            status='completed',
            pages_scraped=pages_scraped,
            total_vectors=total_vectors_stored,
            total_chunks=total_chunks,
            current_url='',
            progress_percentage=100.0,
            estimated_remaining_minutes=0
        )
    except:
        pass
    
    print()
    print("=" * 70)
    print("SCRAPING COMPLETE!")
    print("=" * 70)
    print(f"Pages Scraped: {pages_scraped}")
    print(f"Total URLs Visited: {len(visited_urls)}")
    print(f"Total Chunks Created: {total_chunks}")
    print(f"Total Vectors Stored: {total_vectors_stored}")
    print(f"Start Time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"End Time: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Total Duration: {total_time/60:.1f} minutes ({total_time:.0f} seconds)")
    print(f"Average Time per Page: {total_time/pages_scraped:.1f} seconds" if pages_scraped > 0 else "")
    print("=" * 70)
    
    # Verify storage
    if total_vectors_stored > 0:
        print("\nVerifying Pinecone index...")
        try:
            stats = index.describe_index_stats()
            print(f"✓ Index Stats:")
            print(f"  Total Vectors: {stats.get('total_vector_count', 0)}")
            print(f"  Index Fullness: {stats.get('index_fullness', 0):.2%}")
        except Exception as e:
            print(f"⚠️  Could not verify index stats: {str(e)}")
    
    print("\n✅ Scraping and embedding complete!")


if __name__ == "__main__":
    scrape_documentation()

