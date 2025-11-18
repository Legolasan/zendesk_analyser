from flask import Flask, request, render_template, redirect, url_for, session, jsonify
import requests
import openai
import os
import sqlite3
import time
from datetime import datetime
from requests.exceptions import Timeout, RequestException, ConnectionError as RequestsConnectionError
from openai import OpenAIError
from zendesk_auth import zendesk_auth
import errno

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
ZENDESK_URL_TEMPLATE = "https://hevodata.zendesk.com/api/v2/tickets/{}/comments"
DB_PATH = os.path.join(os.path.dirname(__file__), 'ticket_summaries.db')

openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)

def init_db():
    """Initialize the SQLite database and create tables if they don't exist."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS ticket_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT NOT NULL UNIQUE,
                    issue_description TEXT,
                    root_cause TEXT,
                    test_case_needed INTEGER,
                    test_case_needed_reason TEXT,
                    regression_test_needed INTEGER,
                    regression_test_needed_reason TEXT,
                    test_case_description TEXT,
                    test_case_steps TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # Create index on ticket_id for faster lookups
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_ticket_id ON ticket_summaries(ticket_id)
            ''')
            # Enable WAL mode for better concurrent performance
            conn.execute('PRAGMA journal_mode=WAL')
            conn.commit()
    except sqlite3.Error as e:
        print(f"Error initializing database: {str(e)}")
        raise

def save_ticket_summary(ticket_id, fields):
    """
    Save ticket summary to SQLite database.
    Uses INSERT OR REPLACE to handle updates gracefully.
    Args:
        ticket_id: Zendesk ticket ID
        fields: dict containing all summary fields
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            # Use INSERT OR REPLACE to update if ticket already exists
            # Convert boolean to integer for database storage
            regression_value = None
            if fields.get('regression_test_needed') is not None:
                regression_value = 1 if fields.get('regression_test_needed') else 0
            
            conn.execute('''
                INSERT OR REPLACE INTO ticket_summaries (
                    ticket_id, issue_description, root_cause,
                    test_case_needed, test_case_needed_reason,
                    regression_test_needed, regression_test_needed_reason,
                    test_case_description, test_case_steps, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                ticket_id,
                fields.get('issue_description', ''),
                fields.get('root_cause', ''),
                1 if fields.get('test_case_needed') else 0,
                fields.get('test_case_needed_reason', ''),
                regression_value,
                fields.get('regression_test_needed_reason', ''),
                fields.get('test_case_description', ''),
                fields.get('test_case_steps', ''),
                datetime.now().isoformat()
            ))
            conn.commit()
    except sqlite3.Error as e:
        # Log error but don't fail the request
        print(f"Error saving ticket summary to database: {str(e)}")
    except Exception as e:
        # Log error but don't fail the request
        print(f"Unexpected error saving ticket summary to database: {str(e)}")

def get_ticket_summary(ticket_id):
    """
    Retrieve a ticket summary from the database by ticket_id.
    Returns dict with ticket data or None if not found.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row  # Enable column access by name
            cursor = conn.execute('''
                SELECT * FROM ticket_summaries WHERE ticket_id = ?
            ''', (ticket_id,))
            row = cursor.fetchone()
            
            if row:
                return dict(row)
            return None
    except sqlite3.Error as e:
        print(f"Error retrieving ticket summary from database: {str(e)}")
        return None
    except Exception as e:
        print(f"Unexpected error retrieving ticket summary from database: {str(e)}")
        return None

def get_recent_tickets(limit=10):
    """
    Get recent ticket summaries from the database.
    Returns list of dicts with ticket data.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT ticket_id, issue_description, root_cause, 
                       test_case_needed, regression_test_needed,
                       created_at, updated_at
                FROM ticket_summaries 
                ORDER BY updated_at DESC 
                LIMIT ?
            ''', (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error as e:
        print(f"Error retrieving recent tickets from database: {str(e)}")
        return []
    except Exception as e:
        print(f"Unexpected error retrieving recent tickets from database: {str(e)}")
        return []

def search_tickets(query):
    """
    Search tickets by ticket_id or issue description.
    Returns list of dicts with matching ticket data.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT ticket_id, issue_description, root_cause,
                       test_case_needed, regression_test_needed,
                       created_at, updated_at
                FROM ticket_summaries 
                WHERE ticket_id LIKE ? OR issue_description LIKE ? OR root_cause LIKE ?
                ORDER BY updated_at DESC 
                LIMIT 20
            ''', (f'%{query}%', f'%{query}%', f'%{query}%'))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error as e:
        print(f"Error searching tickets in database: {str(e)}")
        return []
    except Exception as e:
        print(f"Unexpected error searching tickets in database: {str(e)}")
        return []

def format_ticket_for_display(row):
    """Convert database row to display format."""
    return {
        'ticket_id': row['ticket_id'],
        'issue_description': row.get('issue_description', ''),
        'root_cause': row.get('root_cause', ''),
        'test_case_needed': bool(row.get('test_case_needed', 0)),
        'test_case_needed_reason': row.get('test_case_needed_reason', ''),
        'regression_test_needed': bool(row.get('regression_test_needed', 0)) if row.get('regression_test_needed') is not None else None,
        'regression_test_needed_reason': row.get('regression_test_needed_reason', ''),
        'test_case_description': row.get('test_case_description', ''),
        'test_case_steps': row.get('test_case_steps', ''),
        'created_at': row.get('created_at', ''),
        'updated_at': row.get('updated_at', '')
    }

# Initialize database on app startup
init_db()

def fetch_zendesk_ticket_with_retry(ticket_id, max_retries=3, base_timeout=30):
    """
    Fetch Zendesk ticket with retry logic and exponential backoff.
    Args:
        ticket_id: Zendesk ticket ID
        max_retries: Maximum number of retry attempts
        base_timeout: Base timeout in seconds
    Returns:
        requests.Response object
    Raises:
        RequestException: If all retries fail
    """
    url = ZENDESK_URL_TEMPLATE.format(ticket_id)
    headers = zendesk_auth.get_auth_header()
    
    for attempt in range(max_retries):
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=base_timeout
            )
            return response
        except (Timeout, RequestsConnectionError) as e:
            if attempt < max_retries - 1:
                # Exponential backoff: wait 1s, 2s, 4s...
                wait_time = 2 ** attempt
                print(f"Request failed (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                # Last attempt failed, raise the exception
                raise RequestException(f"Failed to fetch ticket after {max_retries} attempts: {str(e)}")
        except Exception as e:
            # For other exceptions, don't retry
            raise RequestException(f"Unexpected error fetching ticket: {str(e)}")
    
    raise RequestException(f"Failed to fetch ticket after {max_retries} attempts")

def get_openai_summary_and_testcase(conversation, timeout=60):
    """
    Generate summary and test case from conversation.
    Args:
        conversation: The ticket conversation text
        timeout: Timeout in seconds for OpenAI API call (default: 60)
    Returns:
        dict with issue_description, root_cause, test_case_needed, regression_test_needed, test_case_description, test_case_steps
    Raises:
        TimeoutError: If OpenAI API call exceeds timeout
        Exception: For other OpenAI API errors
    """
    prompt = f"""
You are a QA or software development engineer. Below is a full transcript of a Zendesk ticket (customer-agent conversation):
---
{conversation}
---

IMPORTANT: Carefully analyze the issue description and root cause to determine if a functional test case is needed.

Test cases SHOULD be created for issues that represent functional bugs or defects, including:
- Data processing errors (incorrect calculations, transformations, validations)
- Logic errors in code that cause incorrect behavior
- Edge cases or boundary conditions that weren't handled properly
- API or integration issues that cause incorrect data flow
- Business rule violations or incorrect implementation of business logic
- Data type mismatches, schema validation issues, or data corruption
- Missing error handling that causes unexpected behavior
- Performance issues that affect functionality (not just speed)
- Security vulnerabilities that allow unauthorized access or data leaks
- Issues where the system behaves differently than expected based on specifications
- Rate limiting and throttling issues (even if handled automatically, the retry/backoff logic needs testing)
- Data ingestion failures or intermittent issues (these indicate gaps in test coverage)
- API integration issues including rate limit handling, retry mechanisms, error recovery
- Issues that occur "occasionally" or "intermittently" (these are often edge cases that need test coverage)

Test cases should be created when:
- The issue reveals a gap in existing test coverage
- The bug could recur if similar code changes are made
- The issue represents a class of problems (not just a one-off data issue)
- The root cause involves code logic, algorithms, or system behavior
- The fix requires code changes that should be validated with tests

Do NOT create test cases for:
- Pure configuration errors where someone set the wrong value (e.g., wrong environment variable value set by mistake)
- One-time data corruption that requires manual data fix (not a code bug, just corrupted data)
- Customer education or documentation issues (user doesn't know how to use the feature correctly)
- Issues resolved by granting permissions or account-level changes (not code changes, just access control)
- Infrastructure/deployment issues (server down, network issues) that don't indicate code bugs
- Issues where the system is working as designed but customer expectations differ
- API version limitations or missing features (e.g., "v1 API doesn't have this data, need to use v3" - this is a feature gap, not a bug)
- Feature gaps or missing functionality (system working correctly but lacks features available in newer versions/APIs)
- Pipeline setup or infrastructure changes (e.g., "need to set up new pipeline", "need to migrate to new API version")
- Enhancement requests or new feature requirements (system working as designed, but needs additional features)

IMPORTANT DISTINCTIONS:
- If the issue is about HOW the system handles rate limits, retries, or errors → Test case IS needed
- If the issue is about WHAT rate limit value is configured → Test case NOT needed (configuration only)
- If the issue involves data ingestion, processing, or transformation logic errors → Test case IS needed
- If the issue involves data ingestion, processing, or transformation logic working correctly but missing features → Test case NOT needed (feature gap)
- If the issue is intermittent or occasional → Test case IS needed (indicates edge case)
- If the issue is about API version limitations or missing fields in an API version → Test case NOT needed (feature gap, not a bug)
- If the issue requires setting up a new pipeline or infrastructure → Test case NOT needed (infrastructure/setup, not a bug)
- If the issue is about "system not providing data" due to API limitations → Test case NOT needed (feature gap)
- If the issue is about "system providing wrong/incorrect data" → Test case IS needed (functional bug)

CRITICAL EVALUATION STEP:
1. First, identify the Issue Description and Root Cause from the conversation
2. Then, evaluate using this decision tree (check ALL that apply):
   
   IMMEDIATELY YES if ANY of these apply:
   a. Does the issue involve INCORRECT functionality, WRONG calculations, WRONG data, or ERRORS in processing?
      → YES (Test Case Needed - functional bug)
   b. Does the issue involve unexpected behavior, intermittent failures, or system behaving incorrectly?
      → YES (Test Case Needed - functional bug)
   c. Does the issue involve API rate limiting, retry logic, error handling, or backoff mechanisms behaving incorrectly?
      → YES (Test Case Needed - functional behavior bug)
   d. Is the issue described as "occasionally", "intermittently", "sometimes" failing?
      → YES (Test Case Needed - indicates edge case bug)
   e. Does the root cause indicate a logic error, algorithm bug, or incorrect code implementation?
      → YES (Test Case Needed - code bug)
   
   IMMEDIATELY NO if ANY of these apply:
   f. Is the issue about API version limitations or missing features in an API version (e.g., "v1 doesn't have this, need v3")?
      → NO (Test Case Needed = NO - feature gap, not a bug)
   g. Is the issue about the system working correctly but missing data/fields available in a newer API version?
      → NO (Test Case Needed = NO - feature gap, not a bug)
   h. Does the issue require setting up a new pipeline, infrastructure changes, or migration to a new API version?
      → NO (Test Case Needed = NO - infrastructure/setup, not a bug)
   i. Is the issue about "system not providing data" because the API version doesn't support it (not because it's broken)?
      → NO (Test Case Needed = NO - feature gap)
   j. Is this purely a configuration mistake (wrong env variable value set by human error)?
      → NO (Test Case Needed = NO)
   k. Is this one-time data corruption that requires manual data fix (not a code bug)?
      → NO (Test Case Needed = NO)
   l. Is this customer education (user doesn't know how to use the feature correctly)?
      → NO (Test Case Needed = NO)
   
3. CRITICAL: Distinguish between BUGS vs FEATURE GAPS:
   - BUG: System is broken, not working correctly, providing wrong data, or behaving incorrectly → Test case NEEDED
   - FEATURE GAP: System working correctly but missing features available in newer versions/APIs → Test case NOT needed
   - INFRASTRUCTURE: Need to set up new pipeline, migrate to new API version → Test case NOT needed
   
4. When in doubt:
   - If it's a functional bug (system broken/incorrect) → YES
   - If it's a feature gap (system working, just missing features) → NO
   - If it's infrastructure/setup (need new pipeline, migration) → NO
   
5. Be precise: Test cases are for bugs, not for feature requests or infrastructure changes

IMPORTANT FOR TEST CASE GENERATION: When writing test case descriptions and steps, you MUST ensure they are designed to validate the root cause you identified. The test should:
- Reproduce the conditions that led to the root cause
- Verify the system handles those conditions correctly after the fix
- Fail if the root cause issue still exists
- Pass when the root cause is properly addressed
- Be generic enough to catch similar issues, but specific enough to validate the root cause scenario

If a test case is needed, then evaluate if it should be added to the regression test suite. Regression tests should be added when:
- The issue represents a bug that could recur in similar scenarios
- The fix involves code changes that could be affected by future code modifications
- The issue type is common enough that it should be part of ongoing test coverage
- The test would catch similar issues before they reach production

Regression tests are NOT needed when:
- The issue is a one-time fix that won't recur
- The test is too specific to a single customer scenario and not generalizable
- The fix is a temporary workaround that will be replaced later
- The issue is already well-covered by existing regression tests

Produce the following:
1. Issue Description (technical, as reported/observed)
2. Root Cause (precise, technical details if known/applicable)
3. Test Case Needed: Answer "Yes" if a functional test case is needed, "No" if not needed. 
   IMPORTANT: Be conservative - if the issue involves any code behavior, logic, data processing, or system functionality that could be tested, answer "Yes". 
   Only answer "No" if it's clearly a configuration-only issue, one-time data fix, or user education issue.
   Provide a detailed reason explaining your decision, especially if answering "No".
4. Regression Test Needed: (REQUIRED - MUST be included) If Test Case Needed is "Yes", then answer "Yes" if this should be added to the regression test suite to prevent similar issues in the future, or "No" if it's a one-time test or doesn't need ongoing regression coverage. If Test Case Needed is "No", write "N/A - Not applicable". ALWAYS provide a brief reason for your decision.
5. Test Case Description: (ONLY provide this if Test Case Needed is "Yes") Not specific to this ticket's field names or values, but a GENERIC regression test intent—describe the class of issue, e.g. type-mismatches, missing columns, etc, as an engineer would. CRITICAL: The test case description MUST be designed to validate that the root cause identified above has been addressed and won't recur. The test should catch the same type of problem that the root cause represents. If Test Case Needed is "No", write "N/A - Test case not needed"
6. Test Case Steps: (ONLY provide this if Test Case Needed is "Yes") Detailed, explicit, but do NOT mention the ticket's specific data/column names—use generic terms like 'a boolean column', 'expected data types', etc. Cover setup, inputs, expected outcomes, and edge cases for this class of problem. CRITICAL: The test steps MUST include validation steps that specifically verify the root cause scenario is handled correctly. The test should reproduce conditions similar to what caused the root cause, then verify the system behaves correctly. Ensure the test would fail if the root cause issue still exists, and pass when the root cause is properly fixed. If Test Case Needed is "No", write "N/A - Test case not needed"

Format output EXACTLY as follows (all sections must be included):
Issue Description:
<your issue description>
Root Cause:
<your root cause>
Test Case Needed:
<Yes or No>
<brief reason>
Regression Test Needed:
<Yes or No or "N/A - Not applicable">
<brief reason>
Test Case Description:
<your generalized test case description OR "N/A - Test case not needed">
Test Case Steps:
<step 1>
<step 2>
... OR "N/A - Test case not needed"

CRITICAL: You MUST always include the "Regression Test Needed:" section in your output, even if Test Case Needed is "No" (in which case write "N/A - Not applicable"). Do not skip this section.

CRITICAL FOR TEST CASES: When writing Test Case Description and Test Case Steps, ensure they directly address and validate the Root Cause you identified. The test must be designed to catch the specific root cause scenario, not just the symptom. The test should verify that the underlying root cause has been fixed.

(important: do not refer to the ticket's exact columns, but use generalized language that would work for any similar case)
"""
    try:
        resp = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=1200,
            temperature=0.2,
            timeout=timeout,
        )
        output = resp.choices[0].message.content
        
        # Debug logging: Log full AI response for troubleshooting
        print(f"\n=== AI Response for ticket ===\n{output}\n=== End AI Response ===\n")
        
    except TimeoutError:
        raise TimeoutError(f"OpenAI API request timed out after {timeout} seconds")
    except OpenAIError as e:
        raise Exception(f"OpenAI API error: {str(e)}")
    except Exception as e:
        # Handle connection errors, broken pipes, etc.
        error_msg = str(e)
        if 'BrokenPipeError' in error_msg or 'broken pipe' in error_msg.lower():
            raise Exception(f"Connection interrupted: The request was interrupted. Please try again.")
        raise Exception(f"OpenAI API error: {error_msg}")
    
    def section(key, text):
        part = text.split(f'{key}:')
        if len(part) > 1:
            # Include all possible section name variations
            nxt = ['Issue Description', 'Root Cause', 'Test Case Needed', 'Regression Test Needed', 'Regression Needed', 'Regression Test', 'Test Case Description', 'Test Case Steps']
            start = part[1].strip()
            for label in nxt:
                if label != key and f'{label}:' in start:
                    start = start.split(f'{label}:')[0].strip()
            return start
        return ''
    
    test_case_needed_text = section('Test Case Needed', output).strip()
    test_case_needed = test_case_needed_text.upper().startswith('YES')
    
    # Try multiple variations of the section name
    regression_test_needed_text = section('Regression Test Needed', output).strip()
    if not regression_test_needed_text:
        # Try alternative formats
        regression_test_needed_text = section('Regression Needed', output).strip()
    if not regression_test_needed_text:
        regression_test_needed_text = section('Regression Test', output).strip()
    
    regression_test_needed = None
    if test_case_needed:
        # Only evaluate regression if test case is needed
        if regression_test_needed_text:
            if 'N/A' in regression_test_needed_text.upper() or 'NOT APPLICABLE' in regression_test_needed_text.upper():
                regression_test_needed = None  # Explicitly None when N/A
            else:
                regression_test_needed = regression_test_needed_text.upper().startswith('YES')
        else:
            # If regression section is missing but test case is Yes, log warning
            print(f"WARNING: Regression Test Needed section not found in output. Output preview: {output[:500]}")
            regression_test_needed = None
    
    return {
        'issue_description': section('Issue Description', output),
        'root_cause': section('Root Cause', output),
        'test_case_needed': test_case_needed,
        'test_case_needed_reason': test_case_needed_text,
        'regression_test_needed': regression_test_needed,
        'regression_test_needed_reason': regression_test_needed_text,
        'test_case_description': section('Test Case Description', output),
        'test_case_steps': section('Test Case Steps', output),
    }

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        ticket_id = request.form.get('ticket_id')
        print('DEBUG Zendesk header:', zendesk_auth.get_auth_header())  # Debug print
        
        # Store ticket_id in session for the redirect
        session['ticket_id'] = ticket_id or ''
        session.pop('error', None)
        session.pop('issue_description', None)
        session.pop('root_cause', None)
        session.pop('test_case_needed', None)
        session.pop('test_case_needed_reason', None)
        session.pop('regression_test_needed', None)
        session.pop('regression_test_needed_reason', None)
        session.pop('test_case_description', None)
        session.pop('test_case_steps', None)
        
        if not ticket_id:
            session['error'] = "Please enter a ticket ID."
        else:
            try:
                # Fetch Zendesk ticket with retry logic
                response = fetch_zendesk_ticket_with_retry(ticket_id, max_retries=3, base_timeout=30)
                
                if response.status_code != 200:
                    session['error'] = f"Zendesk API error: {response.status_code}"
                else:
                    data = response.json()
                    public_comments = [c for c in data.get('comments', []) if c.get('public')]
                    conversation = "\n---\n".join(f"{c['body']}" for c in public_comments)
                    if conversation:
                        # Generate summary with 60 second timeout
                        fields = get_openai_summary_and_testcase(conversation, timeout=60)
                        session['issue_description'] = fields['issue_description']
                        session['root_cause'] = fields['root_cause']
                        session['test_case_needed'] = fields['test_case_needed']
                        session['test_case_needed_reason'] = fields['test_case_needed_reason']
                        session['regression_test_needed'] = fields['regression_test_needed']
                        session['regression_test_needed_reason'] = fields['regression_test_needed_reason']
                        session['test_case_description'] = fields['test_case_description']
                        session['test_case_steps'] = fields['test_case_steps']
                        
                        # Save to database (non-blocking, fast operation)
                        save_ticket_summary(ticket_id, fields)
                    else:
                        session['error'] = "No public conversation found for this ticket."
            except Timeout as e:
                session['error'] = f"Request timed out: The operation took too long. Please try again."
            except TimeoutError as e:
                session['error'] = f"OpenAI API timed out: {str(e)}"
            except RequestException as e:
                error_msg = str(e)
                if 'BrokenPipeError' in error_msg or 'broken pipe' in error_msg.lower():
                    session['error'] = "Connection interrupted. Please try again."
                else:
                    session['error'] = f"Network error: {error_msg}"
            except BrokenPipeError:
                # Client disconnected, silently handle
                session['error'] = "Connection interrupted. Please try again."
            except OSError as e:
                # Handle broken pipe and other OS-level errors
                if e.errno == errno.EPIPE:
                    session['error'] = "Connection interrupted. Please try again."
                else:
                    session['error'] = f"System error: {str(e)}"
            except Exception as e:
                error_msg = str(e)
                if 'BrokenPipeError' in error_msg or 'broken pipe' in error_msg.lower() or 'EPIPE' in error_msg:
                    session['error'] = "Connection interrupted. Please try again."
                else:
                    session['error'] = f"Error: {error_msg}"
        
        # Redirect to GET to prevent form resubmission on refresh
        try:
            return redirect(url_for('index'))
        except BrokenPipeError:
            # Client disconnected during redirect, handle gracefully
            return '', 204
        except OSError as e:
            # Handle OS-level errors including broken pipe
            if hasattr(e, 'errno') and e.errno == errno.EPIPE:
                return '', 204
            raise
    
    # GET request - retrieve data from session and clear it
    ticket_id = session.pop('ticket_id', '')
    error = session.pop('error', '')
    fields = {
        'issue_description': session.pop('issue_description', ''),
        'root_cause': session.pop('root_cause', ''),
        'test_case_needed': session.pop('test_case_needed', None),
        'test_case_needed_reason': session.pop('test_case_needed_reason', ''),
        'regression_test_needed': session.pop('regression_test_needed', None),
        'regression_test_needed_reason': session.pop('regression_test_needed_reason', ''),
        'test_case_description': session.pop('test_case_description', ''),
        'test_case_steps': session.pop('test_case_steps', '')
    }
    
    # Get recent tickets for display (limit to 3 initially to prevent UI from growing)
    recent_tickets = get_recent_tickets(limit=3)
    
    try:
        return render_template('index.html', ticket_id=ticket_id, error=error, recent_tickets=recent_tickets, **fields)
    except (BrokenPipeError, OSError) as e:
        # Client disconnected while rendering, handle gracefully
        if hasattr(e, 'errno') and e.errno == errno.EPIPE:
            # Silently ignore broken pipe during response
            return '', 204
        raise

@app.route('/api/ticket/<ticket_id>')
def get_ticket_api(ticket_id):
    """API endpoint to get a ticket summary by ID."""
    try:
        ticket = get_ticket_summary(ticket_id)
        if ticket:
            return jsonify(format_ticket_for_display(ticket))
        return jsonify({'error': 'Ticket not found'}), 404
    except (BrokenPipeError, OSError) as e:
        if hasattr(e, 'errno') and e.errno == errno.EPIPE:
            return '', 204
        raise

@app.route('/api/tickets/recent')
def get_recent_tickets_api():
    """API endpoint to get recent tickets."""
    try:
        limit = request.args.get('limit', 10, type=int)
        tickets = get_recent_tickets(limit=limit)
        return jsonify(tickets)
    except (BrokenPipeError, OSError) as e:
        if hasattr(e, 'errno') and e.errno == errno.EPIPE:
            return '', 204
        raise

@app.route('/api/tickets/search')
def search_tickets_api():
    """API endpoint to search tickets."""
    try:
        query = request.args.get('q', '')
        if not query:
            return jsonify([])
        tickets = search_tickets(query)
        return jsonify(tickets)
    except (BrokenPipeError, OSError) as e:
        if hasattr(e, 'errno') and e.errno == errno.EPIPE:
            return '', 204
        raise

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=True, port=port)
