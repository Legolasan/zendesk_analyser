"""
Bulk Processor Module for CSV Ticket Analysis.
Handles background processing of multiple tickets from CSV uploads.
"""
import threading
import time
from datetime import datetime
from typing import List, Dict, Optional


class BulkJobManager:
    """Manages concurrent bulk processing jobs."""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._active_jobs = {}
                    cls._instance._job_threads = {}
        return cls._instance
    
    def is_job_running(self, job_id: str) -> bool:
        """Check if a job is currently running."""
        return job_id in self._active_jobs and self._active_jobs[job_id]
    
    def start_job(self, job_id: str, ticket_ids: List[str]):
        """Start a background job to process tickets."""
        if self.is_job_running(job_id):
            print(f"Job {job_id} is already running")
            return False
        
        self._active_jobs[job_id] = True
        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, ticket_ids),
            daemon=True
        )
        self._job_threads[job_id] = thread
        thread.start()
        return True
    
    def stop_job(self, job_id: str):
        """Signal a job to stop (it will stop after current ticket)."""
        if job_id in self._active_jobs:
            self._active_jobs[job_id] = False
    
    def _run_job(self, job_id: str, ticket_ids: List[str]):
        """Internal method to run the job processing."""
        # Import here to avoid circular imports
        from app import (
            update_bulk_job, 
            fetch_zendesk_ticket_details,
            fetch_zendesk_ticket_comments,
            format_structured_conversation,
            get_ticket_analysis,
            save_ticket_summary,
            save_ticket_priority,
            get_field_mapping,
            map_ticket_fields
        )
        from services.priority_service import PriorityAnalyzerService, extract_deal_value
        import os
        
        # Initialize priority service for this thread
        api_key = os.environ.get('OPENAI_API_KEY')
        priority_service = PriorityAnalyzerService(api_key=api_key, model="gpt-4o") if api_key else None
        
        print(f"Starting bulk job {job_id} with {len(ticket_ids)} tickets")
        
        # Update job status to running
        update_bulk_job(job_id, status='running')
        
        ticket_results = {}
        processed_count = 0
        success_count = 0
        failed_count = 0
        
        for ticket_id in ticket_ids:
            # Check if job was cancelled
            if not self._active_jobs.get(job_id, False):
                print(f"Job {job_id} was cancelled, stopping after {processed_count} tickets")
                update_bulk_job(
                    job_id,
                    status='cancelled',
                    processed_count=processed_count,
                    success_count=success_count,
                    failed_count=failed_count,
                    ticket_results=ticket_results
                )
                break
            
            print(f"Job {job_id}: Processing ticket {ticket_id} ({processed_count + 1}/{len(ticket_ids)})")
            
            try:
                # Process single ticket
                result = process_single_ticket(
                    ticket_id, 
                    priority_service,
                    fetch_zendesk_ticket_details,
                    fetch_zendesk_ticket_comments,
                    format_structured_conversation,
                    get_ticket_analysis,
                    save_ticket_summary,
                    save_ticket_priority,
                    get_field_mapping,
                    map_ticket_fields,
                    extract_deal_value
                )
                
                if result['success']:
                    success_count += 1
                    ticket_results[ticket_id] = {'status': 'success'}
                else:
                    failed_count += 1
                    ticket_results[ticket_id] = {'status': 'failed', 'error': result.get('error', 'Unknown error')}
                    
            except Exception as e:
                failed_count += 1
                error_msg = str(e)[:200]
                ticket_results[ticket_id] = {'status': 'failed', 'error': error_msg}
                print(f"Job {job_id}: Error processing ticket {ticket_id}: {error_msg}")
            
            processed_count += 1
            
            # Update progress every ticket
            update_bulk_job(
                job_id,
                processed_count=processed_count,
                success_count=success_count,
                failed_count=failed_count,
                ticket_results=ticket_results
            )
            
            # Small delay between tickets to avoid rate limits
            time.sleep(0.5)
        
        # Final status update
        final_status = 'completed' if processed_count == len(ticket_ids) else 'cancelled'
        update_bulk_job(
            job_id,
            status=final_status,
            processed_count=processed_count,
            success_count=success_count,
            failed_count=failed_count,
            ticket_results=ticket_results
        )
        
        # Clean up
        self._active_jobs.pop(job_id, None)
        self._job_threads.pop(job_id, None)
        
        print(f"Job {job_id} completed: {success_count} succeeded, {failed_count} failed out of {processed_count} processed")


def process_single_ticket(
    ticket_id: str,
    priority_service,
    fetch_zendesk_ticket_details,
    fetch_zendesk_ticket_comments,
    format_structured_conversation,
    get_ticket_analysis,
    save_ticket_summary,
    save_ticket_priority,
    get_field_mapping,
    map_ticket_fields,
    extract_deal_value
) -> Dict:
    """
    Process a single ticket: fetch from Zendesk, run both analyses, save to database.
    
    Args:
        ticket_id: Zendesk ticket ID
        priority_service: PriorityAnalyzerService instance
        ... other function references to avoid circular imports
        
    Returns:
        Dict with 'success' bool and optional 'error' message
    """
    try:
        # Step 1: Fetch ticket details
        ticket_response = fetch_zendesk_ticket_details(ticket_id, max_retries=3, base_timeout=30)
        
        if ticket_response.status_code != 200:
            return {'success': False, 'error': f"Zendesk API error (ticket): {ticket_response.status_code}"}
        
        ticket_data = ticket_response.json().get('ticket', {})
        requester_id = ticket_data.get('requester_id')
        
        # Extract custom fields
        custom_fields = ticket_data.get('custom_fields', [])
        field_mapping = get_field_mapping()
        mapped_ticket_fields = map_ticket_fields(custom_fields, field_mapping)
        
        # Step 2: Fetch comments
        comments_response = fetch_zendesk_ticket_comments(ticket_id, max_retries=3, base_timeout=30)
        
        if comments_response.status_code != 200:
            return {'success': False, 'error': f"Zendesk API error (comments): {comments_response.status_code}"}
        
        comments_data = comments_response.json()
        all_comments = comments_data.get('comments', [])
        
        # Step 3: Format conversation
        conversation = format_structured_conversation(ticket_data, all_comments)
        
        if not conversation:
            return {'success': False, 'error': "No conversation found"}
        
        # Step 4: Run test case analysis
        try:
            test_case_fields = get_ticket_analysis(conversation, ticket_id=ticket_id, timeout=120)
            if test_case_fields and isinstance(test_case_fields, dict):
                save_ticket_summary(ticket_id, test_case_fields)
                print(f"  Ticket {ticket_id}: Test case analysis saved")
            else:
                print(f"  Ticket {ticket_id}: Test case analysis returned invalid result")
        except Exception as e:
            print(f"  Ticket {ticket_id}: Test case analysis failed: {str(e)[:100]}")
            # Continue with priority analysis even if test case fails
        
        # Step 5: Run priority analysis
        if priority_service:
            try:
                priority_fields = priority_service.analyze_ticket_priority(
                    conversation,
                    ticket_fields=mapped_ticket_fields if mapped_ticket_fields else None,
                    timeout=60
                )
                
                # Add ticket fields to priority data
                priority_fields['ticket_fields'] = mapped_ticket_fields
                
                # Extract deal value
                deal_value = extract_deal_value(
                    ticket_fields=mapped_ticket_fields,
                    signal_details=priority_fields.get('signal_details', '')
                )
                if deal_value:
                    priority_fields['deal_value'] = deal_value
                
                save_ticket_priority(ticket_id, priority_fields)
                print(f"  Ticket {ticket_id}: Priority analysis saved")
            except Exception as e:
                print(f"  Ticket {ticket_id}: Priority analysis failed: {str(e)[:100]}")
                # Don't fail the whole ticket if priority analysis fails
        
        return {'success': True}
        
    except Exception as e:
        return {'success': False, 'error': str(e)[:200]}


def start_bulk_job(job_id: str, ticket_ids: List[str]) -> bool:
    """
    Start a bulk processing job.
    
    Args:
        job_id: UUID for the job
        ticket_ids: List of Zendesk ticket IDs to process
        
    Returns:
        True if job started successfully, False otherwise
    """
    manager = BulkJobManager()
    return manager.start_job(job_id, ticket_ids)


def get_job_status(job_id: str) -> Optional[Dict]:
    """
    Get the current status of a bulk job.
    
    Args:
        job_id: UUID for the job
        
    Returns:
        Dict with job status or None if not found
    """
    # Import here to avoid circular imports
    from app import get_bulk_job
    return get_bulk_job(job_id)


def cancel_job(job_id: str) -> bool:
    """
    Cancel a running bulk job.
    
    Args:
        job_id: UUID for the job
        
    Returns:
        True if cancellation was signaled, False if job not found
    """
    manager = BulkJobManager()
    if manager.is_job_running(job_id):
        manager.stop_job(job_id)
        return True
    return False
