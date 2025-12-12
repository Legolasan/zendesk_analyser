"""
Utility module to track and update scraper progress status.
"""
import json
import os
from datetime import datetime
from typing import Dict, Optional

STATUS_FILE = os.path.join(os.path.dirname(__file__), '..', 'scraper_status.json')

def get_status() -> Dict:
    """Get current scraper status."""
    if not os.path.exists(STATUS_FILE):
        return {
            'status': 'not_started',
            'pages_scraped': 0,
            'total_vectors': 0,
            'total_chunks': 0,
            'current_url': '',
            'start_time': None,
            'last_update': None,
            'estimated_remaining_minutes': None,
            'progress_percentage': 0
        }
    
    try:
        with open(STATUS_FILE, 'r') as f:
            return json.load(f)
    except:
        return get_status()  # Return default on error

def update_status(
    status: str = None,
    pages_scraped: int = None,
    total_vectors: int = None,
    total_chunks: int = None,
    current_url: str = None,
    start_time: str = None,
    estimated_remaining_minutes: float = None,
    progress_percentage: float = None
):
    """Update scraper status."""
    current = get_status()
    
    if status is not None:
        current['status'] = status
    if pages_scraped is not None:
        current['pages_scraped'] = pages_scraped
    if total_vectors is not None:
        current['total_vectors'] = total_vectors
    if total_chunks is not None:
        current['total_chunks'] = total_chunks
    if current_url is not None:
        current['current_url'] = current_url
    if start_time is not None:
        current['start_time'] = start_time
    if estimated_remaining_minutes is not None:
        current['estimated_remaining_minutes'] = estimated_remaining_minutes
    if progress_percentage is not None:
        current['progress_percentage'] = progress_percentage
    
    current['last_update'] = datetime.now().isoformat()
    
    try:
        with open(STATUS_FILE, 'w') as f:
            json.dump(current, f, indent=2)
    except Exception as e:
        print(f"Error updating status: {str(e)}")

def reset_status():
    """Reset scraper status."""
    update_status(
        status='not_started',
        pages_scraped=0,
        total_vectors=0,
        total_chunks=0,
        current_url='',
        start_time=None,
        estimated_remaining_minutes=None,
        progress_percentage=0
    )

