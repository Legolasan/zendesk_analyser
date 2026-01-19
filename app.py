from flask import Flask, request, render_template, redirect, url_for, session, jsonify
import requests
import os
import sqlite3
import time
import json
import uuid
import csv
import io
from datetime import datetime
from requests.exceptions import Timeout, RequestException, ConnectionError as RequestsConnectionError
from zendesk_auth import zendesk_auth
from services.openai_service import EnhancedOpenAIService
from services.priority_service import PriorityAnalyzerService, extract_deal_value
from utils.field_mapper import map_ticket_fields, get_field_mapping
import errno

# PostgreSQL support for Railway deployment (using psycopg v3)
try:
    import psycopg
    from psycopg.rows import dict_row
    POSTGRES_AVAILABLE = True
    print("[DB Config] psycopg (v3) imported successfully")
except ImportError as e:
    POSTGRES_AVAILABLE = False
    print(f"[DB Config] psycopg import failed: {e}")

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
ZENDESK_TICKET_URL_TEMPLATE = "https://hevodata.zendesk.com/api/v2/tickets/{}"
ZENDESK_COMMENTS_URL_TEMPLATE = "https://hevodata.zendesk.com/api/v2/tickets/{}/comments"

# Database configuration - uses PostgreSQL if DATABASE_URL is set, otherwise SQLite
DATABASE_URL = os.environ.get('DATABASE_URL')

# Log database configuration for debugging
print(f"[DB Config] DATABASE_URL is set: {DATABASE_URL is not None}")
if DATABASE_URL:
    # Mask password for logging
    import re
    masked = re.sub(r'(://[^:]+:)[^@]+(@)', r'\1****\2', DATABASE_URL)
    print(f"[DB Config] DATABASE_URL (masked): {masked}")
print(f"[DB Config] POSTGRES_AVAILABLE: {POSTGRES_AVAILABLE}")

USE_POSTGRES = DATABASE_URL is not None and POSTGRES_AVAILABLE
print(f"[DB Config] USE_POSTGRES: {USE_POSTGRES}")

DB_PATH = os.path.join(os.path.dirname(__file__), 'ticket_summaries.db')

def get_db_connection():
    """Get a database connection - PostgreSQL for Railway, SQLite for local."""
    if USE_POSTGRES:
        # Railway provides DATABASE_URL in format: postgresql://user:pass@host:port/db
        conn = psycopg.connect(DATABASE_URL)
        return conn
    else:
        return sqlite3.connect(DB_PATH)

openai_service = EnhancedOpenAIService(api_key=OPENAI_API_KEY, model="gpt-4o") if OPENAI_API_KEY else None
priority_service = PriorityAnalyzerService(api_key=OPENAI_API_KEY, model="gpt-4o") if OPENAI_API_KEY else None

def init_db():
    """Initialize the database and create tables if they don't exist.
    Supports both PostgreSQL (Railway) and SQLite (local development).
    """
    if USE_POSTGRES:
        _init_postgres_db()
    else:
        _init_sqlite_db()

def _init_postgres_db():
    """Initialize PostgreSQL database for Railway deployment with retry logic."""
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            print(f"Attempting PostgreSQL connection (attempt {attempt + 1}/{max_retries})...")
            conn = get_db_connection()
            print("PostgreSQL connection successful!")
            
            # Use autocommit for DDL statements to avoid transaction issues
            conn.autocommit = True
            cursor = conn.cursor()
            
            # Create table with PostgreSQL syntax
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ticket_summaries (
                    id SERIAL PRIMARY KEY,
                    ticket_id TEXT NOT NULL UNIQUE,
                    issue_description TEXT,
                    root_cause TEXT,
                    test_case_needed INTEGER,
                    test_case_needed_reason TEXT,
                    regression_test_needed INTEGER,
                    regression_test_needed_reason TEXT,
                    test_case_description TEXT,
                    test_case_steps TEXT,
                    recommended_solution TEXT,
                    search_queries_used TEXT,
                    search_results_summary TEXT,
                    additional_test_scenarios TEXT,
                    test_cases TEXT,
                    num_test_cases INTEGER,
                    documentation_references TEXT,
                    is_documented_limitation INTEGER,
                    is_documented_prerequisite INTEGER,
                    documentation_check_summary TEXT,
                    issue_theme TEXT,
                    root_cause_theme TEXT,
                    ai_provider TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            print("  Created ticket_summaries table")
            
            # Add columns if they don't exist (for existing databases)
            # Using DO block to handle column existence check
            columns_to_add = [
                ('recommended_solution', 'TEXT'),
                ('search_queries_used', 'TEXT'),
                ('search_results_summary', 'TEXT'),
                ('additional_test_scenarios', 'TEXT'),
                ('test_cases', 'TEXT'),
                ('num_test_cases', 'INTEGER'),
                ('documentation_references', 'TEXT'),
                ('is_documented_limitation', 'INTEGER'),
                ('is_documented_prerequisite', 'INTEGER'),
                ('documentation_check_summary', 'TEXT'),
                ('issue_theme', 'TEXT'),
                ('root_cause_theme', 'TEXT'),
                ('ai_provider', 'TEXT')
            ]
            
            for col_name, col_type in columns_to_add:
                try:
                    cursor.execute(f'''
                        DO $$ 
                        BEGIN 
                            ALTER TABLE ticket_summaries ADD COLUMN {col_name} {col_type};
                        EXCEPTION 
                            WHEN duplicate_column THEN NULL;
                        END $$;
                    ''')
                except Exception as e:
                    print(f"  Warning: Could not add column {col_name}: {e}")
            
            # Create index on ticket_id for faster lookups
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_ticket_id ON ticket_summaries(ticket_id)
            ''')
            
            # Create ticket_priorities table for Q1 planning
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ticket_priorities (
                    id SERIAL PRIMARY KEY,
                    ticket_id TEXT NOT NULL UNIQUE,
                    clear_description TEXT,
                    ai_theme TEXT,
                    product_area TEXT,
                    is_blocker INTEGER,
                    is_churn_risk INTEGER,
                    is_escalation INTEGER,
                    is_revenue_impact INTEGER,
                    is_lost_deal INTEGER,
                    deal_value TEXT,
                    signal_details TEXT,
                    priority_score TEXT,
                    ticket_fields TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            print("  Created ticket_priorities table")
            
            # Add columns using DO block to handle existence
            priority_columns = [
                ('ticket_fields', 'TEXT'),
                ('is_lost_deal', 'INTEGER'),
                ('deal_value', 'TEXT')
            ]
            
            for col_name, col_type in priority_columns:
                try:
                    cursor.execute(f'''
                        DO $$ 
                        BEGIN 
                            ALTER TABLE ticket_priorities ADD COLUMN {col_name} {col_type};
                        EXCEPTION 
                            WHEN duplicate_column THEN NULL;
                        END $$;
                    ''')
                except Exception as e:
                    print(f"  Warning: Could not add column {col_name}: {e}")
            
            # Create index on ticket_priorities
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_priority_ticket_id ON ticket_priorities(ticket_id)
            ''')
            
            # Create bulk_jobs table for CSV bulk processing
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bulk_jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT,
                    total_tickets INTEGER,
                    processed_count INTEGER,
                    success_count INTEGER,
                    failed_count INTEGER,
                    ticket_results TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            print("  Created bulk_jobs table")
            
            # Create index on bulk_jobs
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_bulk_jobs_status ON bulk_jobs(status)
            ''')
            
            # Verify tables exist and count existing records (for safety check)
            cursor.execute("SELECT COUNT(*) FROM ticket_summaries")
            summaries_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM ticket_priorities")
            priorities_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM bulk_jobs")
            bulk_jobs_count = cursor.fetchone()[0]
            print(f"PostgreSQL database initialized successfully")
            print(f"  Existing records: {summaries_count} ticket summaries, {priorities_count} priorities, {bulk_jobs_count} bulk jobs")
            
            cursor.close()
            conn.close()
            return  # Success, exit the retry loop
            
        except Exception as e:
            print(f"Error initializing PostgreSQL database (attempt {attempt + 1}): {str(e)}")
            if attempt < max_retries - 1:
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                print("All PostgreSQL connection attempts failed")
                raise

def _init_sqlite_db():
    """Initialize SQLite database for local development."""
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
                    recommended_solution TEXT,
                    search_queries_used TEXT,
                    search_results_summary TEXT,
                    additional_test_scenarios TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # Add new columns if they don't exist (for existing databases)
            try:
                conn.execute('ALTER TABLE ticket_summaries ADD COLUMN recommended_solution TEXT')
            except sqlite3.OperationalError:
                pass  # Column already exists
            try:
                conn.execute('ALTER TABLE ticket_summaries ADD COLUMN search_queries_used TEXT')
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute('ALTER TABLE ticket_summaries ADD COLUMN search_results_summary TEXT')
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute('ALTER TABLE ticket_summaries ADD COLUMN additional_test_scenarios TEXT')
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute('ALTER TABLE ticket_summaries ADD COLUMN test_cases TEXT')
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute('ALTER TABLE ticket_summaries ADD COLUMN num_test_cases INTEGER')
            except sqlite3.OperationalError:
                pass
            # Add documentation columns
            try:
                conn.execute('ALTER TABLE ticket_summaries ADD COLUMN documentation_references TEXT')
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute('ALTER TABLE ticket_summaries ADD COLUMN is_documented_limitation INTEGER')
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute('ALTER TABLE ticket_summaries ADD COLUMN is_documented_prerequisite INTEGER')
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute('ALTER TABLE ticket_summaries ADD COLUMN documentation_check_summary TEXT')
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute('ALTER TABLE ticket_summaries ADD COLUMN issue_theme TEXT')
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute('ALTER TABLE ticket_summaries ADD COLUMN root_cause_theme TEXT')
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute('ALTER TABLE ticket_summaries ADD COLUMN ai_provider TEXT')
            except sqlite3.OperationalError:
                pass
            # Create index on ticket_id for faster lookups
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_ticket_id ON ticket_summaries(ticket_id)
            ''')
            
            # Create ticket_priorities table for Q1 planning
            conn.execute('''
                CREATE TABLE IF NOT EXISTS ticket_priorities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT NOT NULL UNIQUE,
                    clear_description TEXT,
                    ai_theme TEXT,
                    product_area TEXT,
                    is_blocker INTEGER,
                    is_churn_risk INTEGER,
                    is_escalation INTEGER,
                    is_revenue_impact INTEGER,
                    is_lost_deal INTEGER,
                    deal_value TEXT,
                    signal_details TEXT,
                    priority_score TEXT,
                    ticket_fields TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Add ticket_fields column if it doesn't exist (for existing databases)
            try:
                conn.execute('ALTER TABLE ticket_priorities ADD COLUMN ticket_fields TEXT')
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            # Add deal_value column if it doesn't exist (for existing databases)
            try:
                conn.execute('ALTER TABLE ticket_priorities ADD COLUMN deal_value TEXT')
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            # Add is_lost_deal column if it doesn't exist (for existing databases)
            try:
                conn.execute('ALTER TABLE ticket_priorities ADD COLUMN is_lost_deal INTEGER')
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            # Create index on ticket_priorities
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_priority_ticket_id ON ticket_priorities(ticket_id)
            ''')
            
            # Create bulk_jobs table for CSV bulk processing
            conn.execute('''
                CREATE TABLE IF NOT EXISTS bulk_jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT,
                    total_tickets INTEGER,
                    processed_count INTEGER,
                    success_count INTEGER,
                    failed_count INTEGER,
                    ticket_results TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create index on bulk_jobs
            conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_bulk_jobs_status ON bulk_jobs(status)
            ''')
            
            # Enable WAL mode for better concurrent performance
            conn.execute('PRAGMA journal_mode=WAL')
            
            # Verify tables exist and count existing records (for safety check)
            cursor = conn.execute("SELECT COUNT(*) FROM ticket_summaries")
            summaries_count = cursor.fetchone()[0]
            cursor = conn.execute("SELECT COUNT(*) FROM ticket_priorities")
            priorities_count = cursor.fetchone()[0]
            cursor = conn.execute("SELECT COUNT(*) FROM bulk_jobs")
            bulk_jobs_count = cursor.fetchone()[0]
            print(f"SQLite database initialized successfully")
            print(f"  Existing records: {summaries_count} ticket summaries, {priorities_count} priorities, {bulk_jobs_count} bulk jobs")
            
            conn.commit()
    except sqlite3.Error as e:
        print(f"Error initializing SQLite database: {str(e)}")
        raise

def save_ticket_summary(ticket_id, fields):
    """
    Save ticket summary to database.
    Supports both PostgreSQL (Railway) and SQLite (local development).
    Uses INSERT OR REPLACE (SQLite) / ON CONFLICT (PostgreSQL) to handle updates.
    Args:
        ticket_id: Zendesk ticket ID
        fields: dict containing all summary fields
    """
    # Ensure fields is a dictionary
    if fields is None:
        print(f"ERROR: fields is None for ticket {ticket_id}, cannot save to database")
        return
    
    if not isinstance(fields, dict):
        print(f"ERROR: fields is not a dict for ticket {ticket_id}, type: {type(fields)}")
        return
    
    # Prepare common data
    regression_value = None
    if fields.get('regression_test_needed') is not None:
        regression_value = 1 if fields.get('regression_test_needed') else 0
    
    # Convert search_queries_used to JSON string if it's a list
    search_queries_json = fields.get('search_queries_used', '')
    if isinstance(search_queries_json, list):
        search_queries_json = json.dumps(search_queries_json)
    
    # Convert test_cases to JSON string if it's a list
    test_cases_json = ''
    test_cases_list = fields.get('test_cases', [])
    if isinstance(test_cases_list, list) and test_cases_list:
        test_cases_json = json.dumps(test_cases_list)
    num_test_cases = fields.get('num_test_cases', len(test_cases_list) if test_cases_list else 0)
    
    # Convert documentation_references to JSON if it's a list
    doc_refs_json = fields.get('documentation_references', '')
    if isinstance(doc_refs_json, list):
        doc_refs_json = json.dumps(doc_refs_json)
    
    # Get themes and log them
    issue_theme = fields.get('issue_theme', 'Unknown Theme')
    root_cause_theme = fields.get('root_cause_theme', 'Unknown Root Cause Theme')
    if issue_theme:
        log_message = f"issue theme is {issue_theme} - {ticket_id}"
        print(log_message)
        try:
            with open('app.log', 'a') as log_file:
                log_file.write(f"{datetime.now().isoformat()} - {log_message}\n")
        except Exception:
            pass
    if root_cause_theme:
        log_message = f"root cause theme is {root_cause_theme} - {ticket_id}"
        print(log_message)
        try:
            with open('app.log', 'a') as log_file:
                log_file.write(f"{datetime.now().isoformat()} - {log_message}\n")
        except Exception:
            pass
    
    # Prepare values tuple
    values = (
        ticket_id,
        fields.get('issue_description', ''),
        fields.get('root_cause', ''),
        issue_theme,
        root_cause_theme,
        1 if fields.get('test_case_needed') else 0,
        fields.get('test_case_needed_reason', ''),
        regression_value,
        fields.get('regression_test_needed_reason', ''),
        fields.get('test_case_description', ''),
        fields.get('test_case_steps', ''),
        fields.get('recommended_solution', ''),
        search_queries_json,
        fields.get('search_results_summary', ''),
        fields.get('additional_test_scenarios', ''),
        test_cases_json,
        num_test_cases,
        doc_refs_json,
        1 if fields.get('is_documented_limitation') else 0,
        1 if fields.get('is_documented_prerequisite') else 0,
        fields.get('documentation_check_summary', ''),
        'OpenAI',
        datetime.now().isoformat()
    )
    
    if USE_POSTGRES:
        _save_ticket_summary_postgres(values)
    else:
        _save_ticket_summary_sqlite(values)

def _save_ticket_summary_postgres(values):
    """Save ticket summary to PostgreSQL database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO ticket_summaries (
                ticket_id, issue_description, root_cause, issue_theme, root_cause_theme,
                test_case_needed, test_case_needed_reason,
                regression_test_needed, regression_test_needed_reason,
                test_case_description, test_case_steps,
                recommended_solution, search_queries_used,
                search_results_summary, additional_test_scenarios,
                test_cases, num_test_cases,
                documentation_references, is_documented_limitation,
                is_documented_prerequisite, documentation_check_summary,
                ai_provider, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticket_id) DO UPDATE SET
                issue_description = EXCLUDED.issue_description,
                root_cause = EXCLUDED.root_cause,
                issue_theme = EXCLUDED.issue_theme,
                root_cause_theme = EXCLUDED.root_cause_theme,
                test_case_needed = EXCLUDED.test_case_needed,
                test_case_needed_reason = EXCLUDED.test_case_needed_reason,
                regression_test_needed = EXCLUDED.regression_test_needed,
                regression_test_needed_reason = EXCLUDED.regression_test_needed_reason,
                test_case_description = EXCLUDED.test_case_description,
                test_case_steps = EXCLUDED.test_case_steps,
                recommended_solution = EXCLUDED.recommended_solution,
                search_queries_used = EXCLUDED.search_queries_used,
                search_results_summary = EXCLUDED.search_results_summary,
                additional_test_scenarios = EXCLUDED.additional_test_scenarios,
                test_cases = EXCLUDED.test_cases,
                num_test_cases = EXCLUDED.num_test_cases,
                documentation_references = EXCLUDED.documentation_references,
                is_documented_limitation = EXCLUDED.is_documented_limitation,
                is_documented_prerequisite = EXCLUDED.is_documented_prerequisite,
                documentation_check_summary = EXCLUDED.documentation_check_summary,
                ai_provider = EXCLUDED.ai_provider,
                updated_at = EXCLUDED.updated_at
        ''', values)
        
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error saving ticket summary to PostgreSQL: {str(e)}")

def _save_ticket_summary_sqlite(values):
    """Save ticket summary to SQLite database."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO ticket_summaries (
                    ticket_id, issue_description, root_cause, issue_theme, root_cause_theme,
                    test_case_needed, test_case_needed_reason,
                    regression_test_needed, regression_test_needed_reason,
                    test_case_description, test_case_steps,
                    recommended_solution, search_queries_used,
                    search_results_summary, additional_test_scenarios,
                    test_cases, num_test_cases,
                    documentation_references, is_documented_limitation,
                    is_documented_prerequisite, documentation_check_summary,
                    ai_provider, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', values)
            conn.commit()
    except sqlite3.Error as e:
        print(f"Error saving ticket summary to SQLite: {str(e)}")
    except Exception as e:
        print(f"Unexpected error saving ticket summary to SQLite: {str(e)}")

def get_ticket_summary(ticket_id):
    """
    Retrieve a ticket summary from the database by ticket_id.
    Supports both PostgreSQL (Railway) and SQLite (local development).
    Returns dict with ticket data or None if not found.
    """
    if USE_POSTGRES:
        return _get_ticket_summary_postgres(ticket_id)
    else:
        return _get_ticket_summary_sqlite(ticket_id)

def _get_ticket_summary_postgres(ticket_id):
    """Retrieve ticket summary from PostgreSQL."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(row_factory=dict_row)
        cursor.execute('SELECT * FROM ticket_summaries WHERE ticket_id = %s', (ticket_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if row:
            return _convert_datetime_fields(dict(row))
        return None
    except Exception as e:
        print(f"Error retrieving ticket summary from PostgreSQL: {str(e)}")
        return None

def _get_ticket_summary_sqlite(ticket_id):
    """Retrieve ticket summary from SQLite."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT * FROM ticket_summaries WHERE ticket_id = ?', (ticket_id,))
            row = cursor.fetchone()
            
            if row:
                return dict(row)
            return None
    except sqlite3.Error as e:
        print(f"Error retrieving ticket summary from SQLite: {str(e)}")
        return None
    except Exception as e:
        print(f"Unexpected error retrieving ticket summary from SQLite: {str(e)}")
        return None

def get_recent_tickets(limit=10):
    """
    Get recent ticket summaries from the database.
    Supports both PostgreSQL (Railway) and SQLite (local development).
    Returns list of dicts with ticket data.
    """
    if USE_POSTGRES:
        return _get_recent_tickets_postgres(limit)
    else:
        return _get_recent_tickets_sqlite(limit)

def _convert_datetime_fields(row_dict):
    """Convert datetime fields to ISO format strings for template compatibility."""
    for key in ['created_at', 'updated_at']:
        if key in row_dict and row_dict[key] is not None:
            if hasattr(row_dict[key], 'isoformat'):
                row_dict[key] = row_dict[key].isoformat()
    return row_dict


def _get_recent_tickets_postgres(limit):
    """Get recent tickets from PostgreSQL."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(row_factory=dict_row)
        cursor.execute('''
            SELECT ticket_id, issue_description, root_cause, issue_theme,
                   test_case_needed, regression_test_needed,
                   created_at, updated_at
            FROM ticket_summaries 
            ORDER BY updated_at DESC 
            LIMIT %s
        ''', (limit,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [_convert_datetime_fields(dict(row)) for row in rows]
    except Exception as e:
        print(f"Error retrieving recent tickets from PostgreSQL: {str(e)}")
        return []

def _get_recent_tickets_sqlite(limit):
    """Get recent tickets from SQLite."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT ticket_id, issue_description, root_cause, issue_theme,
                       test_case_needed, regression_test_needed,
                       created_at, updated_at
                FROM ticket_summaries 
                ORDER BY updated_at DESC 
                LIMIT ?
            ''', (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error as e:
        print(f"Error retrieving recent tickets from SQLite: {str(e)}")
        return []
    except Exception as e:
        print(f"Unexpected error retrieving recent tickets from SQLite: {str(e)}")
        return []

def search_tickets(query):
    """
    Search tickets by ticket_id or issue description.
    Supports both PostgreSQL (Railway) and SQLite (local development).
    Returns list of dicts with matching ticket data.
    """
    if USE_POSTGRES:
        return _search_tickets_postgres(query)
    else:
        return _search_tickets_sqlite(query)

def _search_tickets_postgres(query):
    """Search tickets in PostgreSQL."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(row_factory=dict_row)
        search_pattern = f'%{query}%'
        cursor.execute('''
            SELECT ticket_id, issue_description, root_cause, issue_theme,
                   test_case_needed, regression_test_needed,
                   created_at, updated_at
            FROM ticket_summaries 
            WHERE ticket_id LIKE %s OR issue_description LIKE %s OR root_cause LIKE %s OR issue_theme LIKE %s
            ORDER BY updated_at DESC 
            LIMIT 20
        ''', (search_pattern, search_pattern, search_pattern, search_pattern))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [_convert_datetime_fields(dict(row)) for row in rows]
    except Exception as e:
        print(f"Error searching tickets in PostgreSQL: {str(e)}")
        return []

def _search_tickets_sqlite(query):
    """Search tickets in SQLite."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT ticket_id, issue_description, root_cause, issue_theme,
                       test_case_needed, regression_test_needed,
                       created_at, updated_at
                FROM ticket_summaries 
                WHERE ticket_id LIKE ? OR issue_description LIKE ? OR root_cause LIKE ? OR issue_theme LIKE ?
                ORDER BY updated_at DESC 
                LIMIT 20
            ''', (f'%{query}%', f'%{query}%', f'%{query}%', f'%{query}%'))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error as e:
        print(f"Error searching tickets in SQLite: {str(e)}")
        return []
    except Exception as e:
        print(f"Unexpected error searching tickets in SQLite: {str(e)}")
        return []

# ============================================================
# TICKET PRIORITIES CRUD FUNCTIONS (Q1 Planning Module)
# ============================================================

def save_ticket_priority(ticket_id, fields):
    """
    Save ticket priority analysis to database.
    Supports both PostgreSQL (Railway) and SQLite (local development).
    """
    if fields is None or not isinstance(fields, dict):
        print(f"ERROR: Invalid fields for ticket priority {ticket_id}")
        return
    
    # Convert ticket_fields dict to JSON string for storage
    ticket_fields_dict = fields.get('ticket_fields', {})
    ticket_fields_json = json.dumps(ticket_fields_dict) if ticket_fields_dict else ''
    
    values = (
        ticket_id,
        fields.get('clear_description', ''),
        fields.get('ai_theme', ''),
        fields.get('product_area', 'Other'),
        1 if fields.get('is_blocker') else 0,
        1 if fields.get('is_churn_risk') else 0,
        1 if fields.get('is_escalation') else 0,
        1 if fields.get('is_revenue_impact') else 0,
        1 if fields.get('is_lost_deal') else 0,
        fields.get('deal_value', ''),
        fields.get('signal_details', ''),
        fields.get('priority_score', 'Medium'),
        ticket_fields_json,
        datetime.now().isoformat()
    )
    
    if USE_POSTGRES:
        _save_ticket_priority_postgres(values)
    else:
        _save_ticket_priority_sqlite(values)

def _save_ticket_priority_postgres(values):
    """Save ticket priority to PostgreSQL database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO ticket_priorities (
                ticket_id, clear_description, ai_theme, product_area,
                is_blocker, is_churn_risk, is_escalation, is_revenue_impact,
                is_lost_deal, deal_value, signal_details, priority_score, ticket_fields, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticket_id) DO UPDATE SET
                clear_description = EXCLUDED.clear_description,
                ai_theme = EXCLUDED.ai_theme,
                product_area = EXCLUDED.product_area,
                is_blocker = EXCLUDED.is_blocker,
                is_churn_risk = EXCLUDED.is_churn_risk,
                is_escalation = EXCLUDED.is_escalation,
                is_revenue_impact = EXCLUDED.is_revenue_impact,
                is_lost_deal = EXCLUDED.is_lost_deal,
                deal_value = EXCLUDED.deal_value,
                signal_details = EXCLUDED.signal_details,
                priority_score = EXCLUDED.priority_score,
                ticket_fields = EXCLUDED.ticket_fields,
                updated_at = EXCLUDED.updated_at
        ''', values)
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error saving ticket priority to PostgreSQL: {str(e)}")

def _save_ticket_priority_sqlite(values):
    """Save ticket priority to SQLite database."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO ticket_priorities (
                    ticket_id, clear_description, ai_theme, product_area,
                    is_blocker, is_churn_risk, is_escalation, is_revenue_impact,
                    is_lost_deal, deal_value, signal_details, priority_score, ticket_fields, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', values)
            conn.commit()
    except sqlite3.Error as e:
        print(f"Error saving ticket priority to SQLite: {str(e)}")

def get_ticket_priority(ticket_id):
    """
    Retrieve a ticket priority from the database by ticket_id.
    Returns dict with priority data or None if not found.
    """
    if USE_POSTGRES:
        return _get_ticket_priority_postgres(ticket_id)
    else:
        return _get_ticket_priority_sqlite(ticket_id)

def _get_ticket_priority_postgres(ticket_id):
    """Retrieve ticket priority from PostgreSQL."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(row_factory=dict_row)
        cursor.execute('SELECT * FROM ticket_priorities WHERE ticket_id = %s', (ticket_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if row:
            return _convert_datetime_fields(dict(row))
        return None
    except Exception as e:
        print(f"Error retrieving ticket priority from PostgreSQL: {str(e)}")
        return None

def _get_ticket_priority_sqlite(ticket_id):
    """Retrieve ticket priority from SQLite."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT * FROM ticket_priorities WHERE ticket_id = ?', (ticket_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
    except sqlite3.Error as e:
        print(f"Error retrieving ticket priority from SQLite: {str(e)}")
        return None

def get_recent_priorities(limit=3):
    """
    Get recent ticket priorities from the database.
    Returns list of dicts with priority data.
    """
    if USE_POSTGRES:
        return _get_recent_priorities_postgres(limit)
    else:
        return _get_recent_priorities_sqlite(limit)

def _get_recent_priorities_postgres(limit):
    """Get recent priorities from PostgreSQL."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(row_factory=dict_row)
        cursor.execute('''
            SELECT ticket_id, clear_description, ai_theme, product_area,
                   is_blocker, is_churn_risk, is_escalation, is_revenue_impact,
                   is_lost_deal, deal_value, priority_score, ticket_fields, created_at, updated_at
            FROM ticket_priorities 
            ORDER BY updated_at DESC 
            LIMIT %s
        ''', (limit,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [_convert_datetime_fields(dict(row)) for row in rows]
    except Exception as e:
        print(f"Error retrieving recent priorities from PostgreSQL: {str(e)}")
        return []

def _get_recent_priorities_sqlite(limit):
    """Get recent priorities from SQLite."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT ticket_id, clear_description, ai_theme, product_area,
                       is_blocker, is_churn_risk, is_escalation, is_revenue_impact,
                       is_lost_deal, deal_value, priority_score, ticket_fields, created_at, updated_at
                FROM ticket_priorities 
                ORDER BY updated_at DESC 
                LIMIT ?
            ''', (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error as e:
        print(f"Error retrieving recent priorities from SQLite: {str(e)}")
        return []

def format_priority_for_display(row):
    """Convert database row to display format for priorities."""
    # Parse ticket_fields from JSON string if present
    ticket_fields_str = row.get('ticket_fields', '')
    ticket_fields = {}
    if ticket_fields_str:
        try:
            ticket_fields = json.loads(ticket_fields_str) if isinstance(ticket_fields_str, str) else ticket_fields_str
        except (json.JSONDecodeError, TypeError):
            ticket_fields = {}
    
    return {
        'ticket_id': row['ticket_id'],
        'clear_description': row.get('clear_description', ''),
        'ai_theme': row.get('ai_theme', ''),
        'product_area': row.get('product_area', 'Other'),
        'is_blocker': bool(row.get('is_blocker', 0)),
        'is_churn_risk': bool(row.get('is_churn_risk', 0)),
        'is_escalation': bool(row.get('is_escalation', 0)),
        'is_revenue_impact': bool(row.get('is_revenue_impact', 0)),
        'is_lost_deal': bool(row.get('is_lost_deal', 0)),
        'deal_value': row.get('deal_value', ''),
        'signal_details': row.get('signal_details', ''),
        'priority_score': row.get('priority_score', 'Medium'),
        'ticket_fields': ticket_fields,
        'created_at': row.get('created_at', ''),
        'updated_at': row.get('updated_at', '')
    }

# ============================================================
# END TICKET PRIORITIES CRUD FUNCTIONS
# ============================================================

# ============================================================
# BULK JOBS CRUD FUNCTIONS (CSV Bulk Processing)
# ============================================================

def create_bulk_job(job_id, total_tickets):
    """
    Create a new bulk job record.
    Args:
        job_id: UUID string for the job
        total_tickets: Number of tickets to process
    """
    values = (
        job_id,
        'pending',  # status
        total_tickets,
        0,  # processed_count
        0,  # success_count
        0,  # failed_count
        '{}',  # ticket_results (empty JSON)
        datetime.now().isoformat(),
        datetime.now().isoformat()
    )
    
    if USE_POSTGRES:
        _create_bulk_job_postgres(values)
    else:
        _create_bulk_job_sqlite(values)

def _create_bulk_job_postgres(values):
    """Create bulk job in PostgreSQL."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO bulk_jobs (id, status, total_tickets, processed_count, 
                                   success_count, failed_count, ticket_results,
                                   created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', values)
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error creating bulk job in PostgreSQL: {str(e)}")

def _create_bulk_job_sqlite(values):
    """Create bulk job in SQLite."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('''
                INSERT INTO bulk_jobs (id, status, total_tickets, processed_count,
                                       success_count, failed_count, ticket_results,
                                       created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', values)
            conn.commit()
    except sqlite3.Error as e:
        print(f"Error creating bulk job in SQLite: {str(e)}")

def get_bulk_job(job_id):
    """
    Retrieve a bulk job by ID.
    Returns dict with job data or None if not found.
    """
    if USE_POSTGRES:
        return _get_bulk_job_postgres(job_id)
    else:
        return _get_bulk_job_sqlite(job_id)

def _get_bulk_job_postgres(job_id):
    """Get bulk job from PostgreSQL."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(row_factory=dict_row)
        cursor.execute('SELECT * FROM bulk_jobs WHERE id = %s', (job_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if row:
            result = _convert_datetime_fields(dict(row))
            # Parse ticket_results JSON
            if result.get('ticket_results'):
                try:
                    result['ticket_results'] = json.loads(result['ticket_results'])
                except (json.JSONDecodeError, TypeError):
                    result['ticket_results'] = {}
            return result
        return None
    except Exception as e:
        print(f"Error retrieving bulk job from PostgreSQL: {str(e)}")
        return None

def _get_bulk_job_sqlite(job_id):
    """Get bulk job from SQLite."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('SELECT * FROM bulk_jobs WHERE id = ?', (job_id,))
            row = cursor.fetchone()
            if row:
                result = dict(row)
                # Parse ticket_results JSON
                if result.get('ticket_results'):
                    try:
                        result['ticket_results'] = json.loads(result['ticket_results'])
                    except (json.JSONDecodeError, TypeError):
                        result['ticket_results'] = {}
                return result
            return None
    except sqlite3.Error as e:
        print(f"Error retrieving bulk job from SQLite: {str(e)}")
        return None

def update_bulk_job(job_id, status=None, processed_count=None, success_count=None, 
                    failed_count=None, ticket_results=None):
    """
    Update a bulk job record.
    Only updates fields that are provided (not None).
    """
    if USE_POSTGRES:
        _update_bulk_job_postgres(job_id, status, processed_count, success_count, 
                                   failed_count, ticket_results)
    else:
        _update_bulk_job_sqlite(job_id, status, processed_count, success_count,
                                 failed_count, ticket_results)

def _update_bulk_job_postgres(job_id, status, processed_count, success_count, 
                               failed_count, ticket_results):
    """Update bulk job in PostgreSQL."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Build dynamic update query
        updates = []
        values = []
        
        if status is not None:
            updates.append("status = %s")
            values.append(status)
        if processed_count is not None:
            updates.append("processed_count = %s")
            values.append(processed_count)
        if success_count is not None:
            updates.append("success_count = %s")
            values.append(success_count)
        if failed_count is not None:
            updates.append("failed_count = %s")
            values.append(failed_count)
        if ticket_results is not None:
            updates.append("ticket_results = %s")
            values.append(json.dumps(ticket_results) if isinstance(ticket_results, dict) else ticket_results)
        
        updates.append("updated_at = %s")
        values.append(datetime.now().isoformat())
        values.append(job_id)
        
        query = f"UPDATE bulk_jobs SET {', '.join(updates)} WHERE id = %s"
        cursor.execute(query, values)
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error updating bulk job in PostgreSQL: {str(e)}")

def _update_bulk_job_sqlite(job_id, status, processed_count, success_count,
                             failed_count, ticket_results):
    """Update bulk job in SQLite."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            # Build dynamic update query
            updates = []
            values = []
            
            if status is not None:
                updates.append("status = ?")
                values.append(status)
            if processed_count is not None:
                updates.append("processed_count = ?")
                values.append(processed_count)
            if success_count is not None:
                updates.append("success_count = ?")
                values.append(success_count)
            if failed_count is not None:
                updates.append("failed_count = ?")
                values.append(failed_count)
            if ticket_results is not None:
                updates.append("ticket_results = ?")
                values.append(json.dumps(ticket_results) if isinstance(ticket_results, dict) else ticket_results)
            
            updates.append("updated_at = ?")
            values.append(datetime.now().isoformat())
            values.append(job_id)
            
            query = f"UPDATE bulk_jobs SET {', '.join(updates)} WHERE id = ?"
            conn.execute(query, values)
            conn.commit()
    except sqlite3.Error as e:
        print(f"Error updating bulk job in SQLite: {str(e)}")

def get_recent_bulk_jobs(limit=10):
    """
    Get recent bulk jobs ordered by created_at desc.
    Returns list of dicts with job data.
    """
    if USE_POSTGRES:
        return _get_recent_bulk_jobs_postgres(limit)
    else:
        return _get_recent_bulk_jobs_sqlite(limit)

def _get_recent_bulk_jobs_postgres(limit):
    """Get recent bulk jobs from PostgreSQL."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(row_factory=dict_row)
        cursor.execute('''
            SELECT id, status, total_tickets, processed_count, success_count,
                   failed_count, created_at, updated_at
            FROM bulk_jobs 
            ORDER BY created_at DESC 
            LIMIT %s
        ''', (limit,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [_convert_datetime_fields(dict(row)) for row in rows]
    except Exception as e:
        print(f"Error retrieving recent bulk jobs from PostgreSQL: {str(e)}")
        return []

def _get_recent_bulk_jobs_sqlite(limit):
    """Get recent bulk jobs from SQLite."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT id, status, total_tickets, processed_count, success_count,
                       failed_count, created_at, updated_at
                FROM bulk_jobs 
                ORDER BY created_at DESC 
                LIMIT ?
            ''', (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error as e:
        print(f"Error retrieving recent bulk jobs from SQLite: {str(e)}")
        return []

# ============================================================
# END BULK JOBS CRUD FUNCTIONS
# ============================================================

def format_ticket_for_display(row):
    """Convert database row to display format."""
    # Parse search_queries_used if it's a JSON string
    search_queries = row.get('search_queries_used', '')
    if isinstance(search_queries, str) and search_queries:
        try:
            search_queries = json.loads(search_queries)
        except (json.JSONDecodeError, TypeError):
            search_queries = []
    
    # Parse test_cases if it's a JSON string
    test_cases = row.get('test_cases', '')
    if isinstance(test_cases, str) and test_cases:
        try:
            test_cases = json.loads(test_cases)
        except (json.JSONDecodeError, TypeError):
            test_cases = []
    elif not isinstance(test_cases, list):
        test_cases = []
    
    # Parse documentation_references if it's a JSON string
    doc_refs = row.get('documentation_references', '')
    if isinstance(doc_refs, str) and doc_refs:
        try:
            doc_refs = json.loads(doc_refs)
        except (json.JSONDecodeError, TypeError):
            doc_refs = []
    elif not isinstance(doc_refs, list):
        doc_refs = []
    
    # Get primary test case for backward compatibility
    primary_test_case = test_cases[0] if test_cases else {}
    
    # Convert datetime fields to strings if needed
    created_at = row.get('created_at', '')
    updated_at = row.get('updated_at', '')
    if hasattr(created_at, 'isoformat'):
        created_at = created_at.isoformat()
    if hasattr(updated_at, 'isoformat'):
        updated_at = updated_at.isoformat()
    
    return {
        'ticket_id': row['ticket_id'],
        'issue_description': row.get('issue_description', ''),
        'root_cause': row.get('root_cause', ''),
        'issue_theme': row.get('issue_theme', ''),
        'root_cause_theme': row.get('root_cause_theme', ''),
        'test_case_needed': bool(row.get('test_case_needed', 0)),
        'test_case_needed_reason': row.get('test_case_needed_reason', ''),
        'regression_test_needed': bool(row.get('regression_test_needed', 0)) if row.get('regression_test_needed') is not None else None,
        'regression_test_needed_reason': row.get('regression_test_needed_reason', ''),
        # Multiple test cases (new format)
        'test_cases': test_cases,
        'num_test_cases': row.get('num_test_cases', len(test_cases)),
        # Backward compatibility (single test case fields)
        'test_case_description': primary_test_case.get('description', '') or row.get('test_case_description', ''),
        'test_case_steps': primary_test_case.get('steps', '') or row.get('test_case_steps', ''),
        'recommended_solution': row.get('recommended_solution', ''),
        'additional_test_scenarios': row.get('additional_test_scenarios', ''),
        'search_queries_used': search_queries if isinstance(search_queries, list) else [],
        'search_results_summary': row.get('search_results_summary', ''),
        'documentation_references': doc_refs,
        'is_documented_limitation': bool(row.get('is_documented_limitation', 0)),
        'is_documented_prerequisite': bool(row.get('is_documented_prerequisite', 0)),
        'documentation_check_summary': row.get('documentation_check_summary', ''),
        'ai_provider': row.get('ai_provider', 'Unknown'),
        'created_at': created_at,
        'updated_at': updated_at
    }

# Initialize database on app startup
# This is safe - only creates tables if they don't exist, never drops data
print("Initializing database...")
try:
    init_db()
    print("Database initialization complete - all existing data is preserved")
except Exception as e:
    print(f"WARNING: Database initialization failed: {str(e)}")
    print("App will continue but database operations may fail")

def fetch_zendesk_ticket_details(ticket_id, max_retries=3, base_timeout=30):
    """
    Fetch Zendesk ticket details (to get requester_id for customer identification).
    Args:
        ticket_id: Zendesk ticket ID
        max_retries: Maximum number of retry attempts
        base_timeout: Base timeout in seconds
    Returns:
        requests.Response object containing ticket details
    Raises:
        RequestException: If all retries fail
    """
    url = ZENDESK_TICKET_URL_TEMPLATE.format(ticket_id)
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
                wait_time = 2 ** attempt
                print(f"Ticket details request failed (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise RequestException(f"Failed to fetch ticket details after {max_retries} attempts: {str(e)}")
        except Exception as e:
            raise RequestException(f"Unexpected error fetching ticket details: {str(e)}")
    
    raise RequestException(f"Failed to fetch ticket details after {max_retries} attempts")

def fetch_zendesk_ticket_comments(ticket_id, max_retries=3, base_timeout=30):
    """
    Fetch Zendesk ticket comments (including internal notes).
    Uses the ticket endpoint with include=comments to get ALL comments including internal notes.
    Args:
        ticket_id: Zendesk ticket ID
        max_retries: Maximum number of retry attempts
        base_timeout: Base timeout in seconds
    Returns:
        requests.Response object containing comments
    Raises:
        RequestException: If all retries fail
    """
    # Use ticket endpoint with include=comments to get ALL comments (public + internal)
    # This is more reliable than the comments endpoint alone
    url = f"{ZENDESK_TICKET_URL_TEMPLATE.format(ticket_id)}?include=comments"
    headers = zendesk_auth.get_auth_header()
    
    for attempt in range(max_retries):
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=base_timeout
            )
            
            # If successful, extract comments from the response
            if response.status_code == 200:
                data = response.json()
                # The ticket endpoint returns comments in a different structure
                # Check if comments are in the response
                if 'comments' in data:
                    # Comments are already in the response
                    return response
                elif 'ticket' in data and 'comments' in data:
                    # Comments might be in a nested structure
                    return response
                else:
                    # Fallback: try the comments endpoint directly
                    comments_url = ZENDESK_COMMENTS_URL_TEMPLATE.format(ticket_id)
                    comments_response = requests.get(
                        comments_url,
                        headers=headers,
                        timeout=base_timeout
                    )
                    return comments_response
            
            return response
        except (Timeout, RequestsConnectionError) as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(f"Comments request failed (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise RequestException(f"Failed to fetch ticket comments after {max_retries} attempts: {str(e)}")
        except Exception as e:
            raise RequestException(f"Unexpected error fetching ticket comments: {str(e)}")
    
    raise RequestException(f"Failed to fetch ticket comments after {max_retries} attempts")

# Keep backward compatibility alias
def fetch_zendesk_ticket_with_retry(ticket_id, max_retries=3, base_timeout=30):
    """Backward compatibility alias for fetch_zendesk_ticket_comments."""
    return fetch_zendesk_ticket_comments(ticket_id, max_retries, base_timeout)

def format_structured_conversation(ticket_data, comments):
    """
    Format comments with [CUSTOMER]/[AGENT]/[AGENT - INTERNAL] labels chronologically.
    This provides richer context for AI analysis by clearly identifying who said what.
    
    Args:
        ticket_data: Ticket details dict containing 'requester_id' (the customer)
        comments: List of comment objects from Zendesk API
    Returns:
        Structured conversation string with labeled speakers and timestamps
    """
    requester_id = ticket_data.get('requester_id')
    formatted_parts = []
    
    # Debug: Track comment types
    public_count = 0
    internal_count = 0
    customer_count = 0
    empty_count = 0
    
    for comment in comments:
        author_id = comment.get('author_id')
        is_public = comment.get('public', True)
        body = comment.get('body', '').strip()
        created_at = comment.get('created_at', '')
        
        # Skip empty comments
        if not body:
            empty_count += 1
            continue
        
        # Determine speaker label based on author and visibility
        if author_id == requester_id:
            label = "[CUSTOMER]"
            customer_count += 1
        elif is_public:
            label = "[AGENT]"
            public_count += 1
        else:
            label = "[AGENT - INTERNAL]"  # Internal notes - often contain engineering discussions
            internal_count += 1
        
        # Format with label and timestamp for context
        formatted_parts.append(f"{label} ({created_at}):\n{body}")
    
    # Debug log (only for ticket 64258 to avoid spam)
    ticket_id = ticket_data.get('id')
    if ticket_id == 64258 or str(ticket_id) == '64258':
        print(f"DEBUG format_structured_conversation for ticket {ticket_id}:")
        print(f"  Total comments: {len(comments)}")
        print(f"  Customer comments: {customer_count}")
        print(f"  Public agent comments: {public_count}")
        print(f"  Internal agent comments: {internal_count}")
        print(f"  Empty comments skipped: {empty_count}")
        print(f"  Formatted parts: {len(formatted_parts)}")
    
    return "\n\n---\n\n".join(formatted_parts)

def get_ticket_analysis(conversation, ticket_id=None, timeout=120):
    """
    Simplified function: Generate ticket analysis and test case using OpenAI only.
    No search, no Claude, no embeddings - just OpenAI analysis.
    
    Args:
        conversation: The ticket conversation text
        ticket_id: Optional ticket ID for logging
        timeout: Total timeout in seconds (default: 120)
    Returns:
        dict with analysis results
    """
    if not openai_service:
        raise Exception("OPENAI_API_KEY is not set. Please set it in your environment variables.")
    
    print("Starting analysis with OpenAI...")
    start_time = time.time()
    
    try:
        # Phase 1: Analyze ticket and extract root cause
        print("Phase 1: Analyzing ticket with OpenAI...")
        ticket_analysis = openai_service.analyze_ticket_phase1(conversation, timeout=60)
        
        issue_description = ticket_analysis.get('issue_description', '')
        root_cause = ticket_analysis.get('root_cause', '')
        
        # Apply validation logic
        root_cause_lower = root_cause.lower() if root_cause else ''
        issue_description_lower = issue_description.lower() if issue_description else ''
        combined_text = f"{root_cause_lower} {issue_description_lower}"
        
        root_cause_not_identified = any(phrase in root_cause_lower for phrase in [
            'not identified', 'unable to determine', 'unknown', 'not clear', 
            'cannot be determined', 'not specified', 'not found', 'unclear',
            'ambiguous', 'vague', 'not known'
        ])
        
        functional_gap_indicators = any(phrase in issue_description_lower for phrase in [
            'no task created', 'task not created', 'not created', 'missing task',
            'no failure reported', 'no error', 'silent failure', 'silently failed',
            'did not trigger', 'not triggered', 'failed to trigger', 'should have',
            'expected to', 'supposed to', 'should create', 'should generate',
            'missing', 'not generated', 'not executed', 'not running',
            'stuck', 'stopped', 'not working', 'not functioning'
        ])
        
        code_bug_indicators = any(phrase in root_cause_lower for phrase in [
            'exception', 'not properly handled', 'not handled', 'error handling',
            'exception handling', 'bug', 'defect', 'failure', 'crash', 'leak',
            'memory leak', 'race condition', 'deadlock', 'timeout', 'retry',
            'logic error', 'algorithm error', 'validation error', 'processing error',
            'data corruption', 'data loss', 'incorrect', 'wrong', 'invalid',
            'missing check', 'missing validation', 'missing error', 'missing exception'
        ])
        
        is_pure_user_mistake = any(phrase in combined_text for phrase in [
            'user error', 'user mistake', 'user configuration error',
            'user did not', 'user failed to', 'user misunderstood',
            'user did not follow', 'user not following'
        ]) and not code_bug_indicators
        
        is_product_limitation = any(phrase in combined_text for phrase in [
            'product limitation', 'system limitation', 'by design',
            'documented limitation', 'known limitation', 'working as designed',
            'feature does not exist', 'feature not available', 'missing feature',
            'not supported', 'not implemented', 'out of scope'
        ]) and not code_bug_indicators
        
        if root_cause_not_identified and functional_gap_indicators:
            ticket_analysis['test_case_needed'] = True
            ticket_analysis['test_case_needed_reason'] = f'Functional gap identified: {issue_description[:100]}... - test case needed to validate expected behavior even though root cause is unclear'
        elif root_cause_not_identified and not functional_gap_indicators:
            ticket_analysis['test_case_needed'] = False
            ticket_analysis['test_case_needed_reason'] = 'Root cause not identified and no clear functional gap - cannot create meaningful test cases without a specific root cause or expected behavior to validate'
        elif code_bug_indicators:
            ticket_analysis['test_case_needed'] = True
            ticket_analysis['test_case_needed_reason'] = f'Code bug identified: {root_cause[:100]} - test case needed to validate fix and prevent regression'
        elif is_pure_user_mistake or is_product_limitation:
            ticket_analysis['test_case_needed'] = False
            ticket_analysis['test_case_needed_reason'] = 'User mistake or product limitation - test cases should only be created for functional bugs requiring code/logic fixes'
        
        # If test case is not needed, return early
        if not ticket_analysis.get('test_case_needed'):
            return {
                'issue_description': issue_description,
                'root_cause': root_cause,
                'issue_theme': ticket_analysis.get('issue_theme', 'Unknown Theme'),
                'root_cause_theme': ticket_analysis.get('root_cause_theme', 'Unknown Root Cause Theme'),
                'test_case_needed': False,
                'test_case_needed_reason': ticket_analysis.get('test_case_needed_reason', ''),
                'regression_test_needed': None,
                'regression_test_needed_reason': 'N/A - Test case not needed',
                'test_cases': [],
                'num_test_cases': 0,
                'test_case_description': 'N/A - Test case not needed',
                'test_case_steps': 'N/A - Test case not needed',
                'recommended_solution': 'N/A - Test case not needed',
                'additional_test_scenarios': 'N/A - Test case not needed',
                'search_queries_used': [],
                'search_results_summary': '',
                'documentation_references': [],
                'is_documented_limitation': False,
                'is_documented_prerequisite': False,
                'documentation_check_summary': ''
            }
        
        # Phase 2: Generate test case (without search)
        print("Phase 2: Generating test case with OpenAI...")
        enhanced_results = openai_service.generate_test_case_with_solutions(
            ticket_analysis,
            search_results=None,  # No search results
            doc_check=None,
            timeout=60
        )
        
        # Ensure all required fields are present
        enhanced_results['search_queries_used'] = []
        enhanced_results['search_results_summary'] = ''
        enhanced_results['documentation_references'] = []
        enhanced_results['is_documented_limitation'] = False
        enhanced_results['is_documented_prerequisite'] = False
        enhanced_results['documentation_check_summary'] = ''
        
        # Phase 3: Validate test cases (only if test cases were generated)
        test_cases_list = enhanced_results.get('test_cases', [])
        num_test_cases = enhanced_results.get('num_test_cases', 0)
        test_case_description = enhanced_results.get('test_case_description', '')
        test_case_steps = enhanced_results.get('test_case_steps', '')
        
        # Check if test cases were actually generated
        has_test_cases = (
            (test_cases_list and len(test_cases_list) > 0) or
            (num_test_cases > 0) or
            (test_case_description and test_case_description not in ['N/A - Test case not needed', 'N/A', ''])
        )
        
        if not has_test_cases:
            # No test cases generated - treat as critical issue and regenerate
            print("Phase 3: No test cases generated, regenerating...")
            try:
                # Create validation feedback for missing test cases
                validation_feedback = "No test cases were generated in Phase 2. Please generate appropriate test cases for the issue and root cause."
                regenerated_results = openai_service._regenerate_test_cases_with_feedback(
                    ticket_analysis,
                    validation_feedback,
                    timeout=60
                )
                enhanced_results = regenerated_results
                enhanced_results['validation_passed'] = True
                enhanced_results['validation_issues'] = []
                enhanced_results['validation_summary'] = 'Test cases regenerated - no test cases were generated in Phase 2'
                enhanced_results['regeneration_attempted'] = True
            except Exception as regen_error:
                print(f"Regeneration failed: {str(regen_error)}")
                enhanced_results['validation_passed'] = False
                enhanced_results['validation_issues'] = ['No test cases generated and regeneration failed']
                enhanced_results['validation_summary'] = f'No test cases generated. Regeneration failed: {str(regen_error)[:200]}'
                enhanced_results['regeneration_attempted'] = True
        else:
            # Test cases exist - validate them
            print("Phase 3: Validating test cases...")
            try:
                validation_results = openai_service.validate_test_cases(
                    issue_description=issue_description,
                    root_cause=root_cause,
                    generated_test_cases=enhanced_results,
                    timeout=60
                )
                
                if validation_results.get('regeneration_needed', False) and validation_results.get('regenerated_test_cases'):
                    # Use regenerated test cases
                    print("Using regenerated test cases after validation feedback...")
                    enhanced_results = validation_results.get('regenerated_test_cases')
                    enhanced_results['validation_passed'] = True
                    enhanced_results['validation_issues'] = []
                    enhanced_results['validation_summary'] = 'Test cases regenerated based on validation feedback'
                    enhanced_results['regeneration_attempted'] = True
                else:
                    # Add validation flags to results
                    enhanced_results['validation_passed'] = validation_results.get('validation_passed', True)
                    enhanced_results['validation_issues'] = validation_results.get('minor_issues', [])
                    validation_summary = validation_results.get('overall_assessment', '')
                    if not validation_summary:
                        validation_summary = 'Validation completed'
                    enhanced_results['validation_summary'] = validation_summary
                    enhanced_results['regeneration_attempted'] = False
                    
                    if validation_results.get('critical_issues'):
                        enhanced_results['validation_critical_issues'] = validation_results.get('critical_issues', [])
            except Exception as validation_error:
                print(f"Validation phase failed (continuing with original test cases): {str(validation_error)}")
                enhanced_results['validation_passed'] = None
                enhanced_results['validation_issues'] = []
                enhanced_results['validation_summary'] = f'Validation failed: {str(validation_error)[:200]}'
                enhanced_results['regeneration_attempted'] = False
        
        total_elapsed = time.time() - start_time
        print(f"OpenAI analysis complete in {total_elapsed:.2f}s!")
        return enhanced_results
        
    except Exception as e:
        error_msg = str(e)
        print(f"OpenAI analysis failed: {error_msg}")
        return {
            'issue_description': 'Analysis failed due to error',
            'root_cause': f'Unable to analyze: {error_msg[:200]}',
            'issue_theme': 'Error',
            'root_cause_theme': 'Error',
            'test_case_needed': False,
            'test_case_needed_reason': f'Analysis failed: {error_msg[:200]}',
            'regression_test_needed': None,
            'regression_test_needed_reason': 'N/A - Analysis failed',
            'test_cases': [],
            'num_test_cases': 0,
            'test_case_description': 'Analysis failed',
            'test_case_steps': 'Analysis failed',
            'recommended_solution': 'Please retry the analysis',
            'additional_test_scenarios': '',
            'search_queries_used': [],
            'search_results_summary': '',
            'documentation_references': [],
            'is_documented_limitation': False,
            'is_documented_prerequisite': False,
            'documentation_check_summary': ''
        }

# Old function removed - use get_ticket_analysis instead
def _removed_get_openai_summary_and_testcase_enhanced(conversation, timeout=120):
    """
    Enhanced version: Generate summary and test case with web search for solutions.
    Uses two-phase approach: analyze ticket, then search for solutions and generate enhanced test case.
    
    Args:
        conversation: The ticket conversation text
        timeout: Total timeout in seconds (default: 120)
    Returns:
        dict with issue_description, root_cause, test_case_needed, regression_test_needed, 
        test_case_description, test_case_steps, recommended_solution, additional_test_scenarios,
        search_queries_used, search_results_summary
    Raises:
        TimeoutError: If Claude API call exceeds timeout
        Exception: For other Claude API errors
    """
    try:
        # Phase 1: Analyze ticket and extract root cause
        print("Phase 1: Analyzing ticket...")
        if not claude_service:
            raise Exception("CLAUDE_API_KEY is not set. Please set it in your environment variables.")
        ticket_analysis = claude_service.analyze_ticket_phase1(conversation, timeout=60)
        
        issue_description = ticket_analysis.get('issue_description', '')
        root_cause = ticket_analysis.get('root_cause', '')
        
        # STRICT VALIDATION: Check multiple conditions that should prevent test case creation
        root_cause_lower = root_cause.lower() if root_cause else ''
        issue_description_lower = issue_description.lower() if issue_description else ''
        combined_text = f"{root_cause_lower} {issue_description_lower}"
        
        # 1. Check if root cause is NOT CLEAR
        root_cause_not_identified = any(phrase in root_cause_lower for phrase in [
            'not identified', 'unable to determine', 'unknown', 'not clear', 
            'cannot be determined', 'not specified', 'not found', 'unclear',
            'ambiguous', 'vague', 'not known'
        ])
        
        # 1b. Check for FUNCTIONAL GAPS or MISSING BEHAVIOR that still need test cases
        # Even if root cause isn't clear, if expected behavior is missing, we should test it
        functional_gap_indicators = any(phrase in issue_description_lower for phrase in [
            'no task created', 'task not created', 'not created', 'missing task',
            'no failure reported', 'no error', 'silent failure', 'silently failed',
            'did not trigger', 'not triggered', 'failed to trigger', 'should have',
            'expected to', 'supposed to', 'should create', 'should generate',
            'missing', 'not generated', 'not executed', 'not running',
            'stuck', 'stopped', 'not working', 'not functioning'
        ])
        
        # 2. Check for CODE BUGS/ISSUES that REQUIRE test cases (these should override config issues)
        # If root cause clearly identifies a code bug, we should create a test case even if config issues are mentioned
        code_bug_indicators = any(phrase in root_cause_lower for phrase in [
            'exception', 'not properly handled', 'not handled', 'error handling',
            'exception handling', 'bug', 'defect', 'failure', 'crash', 'leak',
            'memory leak', 'race condition', 'deadlock', 'timeout', 'retry',
            'logic error', 'algorithm error', 'validation error', 'processing error',
            'data corruption', 'data loss', 'incorrect', 'wrong', 'invalid',
            'missing check', 'missing validation', 'missing error', 'missing exception'
        ])
        
        # 3. Check if issue is PURELY USER MISTAKE or PRODUCT LIMITATION (without code bugs)
        # Only reject if it's ONLY a user mistake/config issue, not if there's also a code bug
        is_pure_user_mistake = any(phrase in combined_text for phrase in [
            'user error', 'user mistake', 'user configuration error',
            'user did not', 'user failed to', 'user misunderstood',
            'user did not follow', 'user not following'
        ]) and not code_bug_indicators  # Only if no code bug indicators
        
        is_product_limitation = any(phrase in combined_text for phrase in [
            'product limitation', 'system limitation', 'by design',
            'documented limitation', 'known limitation', 'working as designed',
            'feature does not exist', 'feature not available', 'missing feature',
            'not supported', 'not implemented', 'out of scope'
        ]) and not code_bug_indicators  # Only if no code bug indicators
        
        # Apply strict validation - prioritize code bugs over config issues
        # BUT: Allow test cases for functional gaps even if root cause isn't clear
        if root_cause_not_identified and functional_gap_indicators:
            # Root cause not clear BUT there's a functional gap - create test case to validate expected behavior
            ticket_analysis['test_case_needed'] = True
            ticket_analysis['test_case_needed_reason'] = f'Functional gap identified: {issue_description[:100]}... - test case needed to validate expected behavior even though root cause is unclear'
        elif root_cause_not_identified and not functional_gap_indicators:
            # Root cause not clear AND no clear functional gap - skip test case
            ticket_analysis['test_case_needed'] = False
            ticket_analysis['test_case_needed_reason'] = 'Root cause not identified and no clear functional gap - cannot create meaningful test cases without a specific root cause or expected behavior to validate'
        elif code_bug_indicators:
            # If there's a clear code bug, create test case even if config issues are mentioned
            ticket_analysis['test_case_needed'] = True
            ticket_analysis['test_case_needed_reason'] = f'Code bug identified: {root_cause[:100]} - test case needed to validate fix and prevent regression'
        elif is_pure_user_mistake or is_product_limitation:
            ticket_analysis['test_case_needed'] = False
            ticket_analysis['test_case_needed_reason'] = 'User mistake or product limitation - test cases should only be created for functional bugs requiring code/logic fixes'
        
        # If test case is not needed, return early
        if not ticket_analysis.get('test_case_needed'):
            return {
                'issue_description': issue_description,
                'root_cause': root_cause,
                'issue_theme': ticket_analysis.get('issue_theme', 'Unknown Theme'),
                'root_cause_theme': ticket_analysis.get('root_cause_theme', 'Unknown Root Cause Theme'),
                'test_case_needed': False,
                'test_case_needed_reason': ticket_analysis.get('test_case_needed_reason', ''),
                'regression_test_needed': None,
                'regression_test_needed_reason': 'N/A - Test case not needed',
                'test_cases': [],
                'num_test_cases': 0,
                'test_case_description': 'N/A - Test case not needed',
                'test_case_steps': 'N/A - Test case not needed',
                'recommended_solution': 'N/A - Test case not needed',
                'additional_test_scenarios': 'N/A - Test case not needed',
                'search_queries_used': [],
                'search_results_summary': '',
                'documentation_references': [],
                'is_documented_limitation': False,
                'is_documented_prerequisite': False,
                'documentation_check_summary': ''
            }
        
        # Phase 2: Generate search queries and search for solutions
        # Use issue description (generic) instead of root cause (specific) for better search results
        print("Phase 2: Generating search queries from issue description...")
        search_queries = claude_service.generate_search_queries(
            issue_description=issue_description,
            root_cause=root_cause,
            timeout=30
        )
        
        # Execute searches
        print(f"Searching for solutions using {len(search_queries)} queries...")
        all_search_results = {'web': [], 'stackoverflow': []}
        
        for query in search_queries[:3]:  # Limit to 3 queries to avoid too many API calls
            print(f"  Searching: {query}")
            results = search_service.search_all(query, max_results=3)
            all_search_results['web'].extend(results.get('web', []))
            all_search_results['stackoverflow'].extend(results.get('stackoverflow', []))
        
        # Remove duplicates (by link)
        seen_links = set()
        for source in ['web', 'stackoverflow']:
            unique_results = []
            for result in all_search_results[source]:
                link = result.get('link', '')
                if link and link not in seen_links:
                    seen_links.add(link)
                    unique_results.append(result)
            all_search_results[source] = unique_results[:5]  # Limit to 5 per source
        
        # Phase 3: Generate enhanced test case with search results
        print("Phase 3: Generating enhanced test case with solution context...")
        enhanced_results = claude_service.generate_test_case_with_solutions(
            ticket_analysis,
            all_search_results,
            doc_check=None,
            timeout=90
        )
        
        # Add search queries used and ensure search_results_summary is set
        enhanced_results['search_queries_used'] = search_queries
        # Ensure search_results_summary is set (it should already be set in _parse_phase2_response, but ensure it's there)
        if 'search_results_summary' not in enhanced_results or not enhanced_results.get('search_results_summary'):
            enhanced_results['search_results_summary'] = search_service.format_search_results_for_prompt(all_search_results)
        enhanced_results['documentation_references'] = []
        enhanced_results['is_documented_limitation'] = False
        enhanced_results['is_documented_prerequisite'] = False
        enhanced_results['documentation_check_summary'] = ''
        
        # Debug: Print search status
        print(f"Search queries generated: {len(search_queries)}")
        print(f"Search results - Web: {len(all_search_results.get('web', []))}, StackOverflow: {len(all_search_results.get('stackoverflow', []))}")
        print(f"Search results summary length: {len(enhanced_results.get('search_results_summary', ''))}")
        
        print("Enhanced test case generation complete!")
        return enhanced_results
        
    except TimeoutError as e:
        raise TimeoutError(f"Request timed out: {str(e)}")
    except Exception as e:
        error_msg = str(e)
        if 'BrokenPipeError' in error_msg or 'broken pipe' in error_msg.lower():
            raise Exception("Connection interrupted: The request was interrupted. Please try again.")
        raise Exception(f"Error generating test case: {error_msg}")

# Old function removed - use get_ticket_analysis instead
def _removed_get_summary_with_fallback(conversation, ticket_id=None, timeout=120):
    """
    Wrapper function that tries Claude first, monitors total execution time,
    and falls back to OpenAI if Claude takes more than 50 seconds or times out.
    Always returns a result, never raises timeout exceptions.
    
    Args:
        conversation: The ticket conversation text
        ticket_id: Optional ticket ID for logging
        timeout: Total timeout in seconds (default: 120)
    Returns:
        dict with analysis results, including 'ai_provider' field ('Claude' or 'OpenAI')
    """
    CLAUDE_TIMEOUT_THRESHOLD = 50  # Switch to OpenAI if Claude takes >50 seconds
    start_time = time.time()
    
    # Try Claude first
    print(f"Starting analysis with Claude (timeout threshold: {CLAUDE_TIMEOUT_THRESHOLD}s)...")
    
    if not claude_service:
        print("Claude service not available, using OpenAI directly...")
        result = get_openai_fallback_analysis(conversation, ticket_id=ticket_id, timeout=timeout)
        result['ai_provider'] = 'OpenAI'
        return result
    
    # Phase 1: Analyze ticket - wrap in try-except to catch timeouts immediately
    try:
        phase1_start = time.time()
        ticket_analysis = claude_service.analyze_ticket_phase1(conversation, timeout=30)  # Reduced timeout
        phase1_elapsed = time.time() - phase1_start
        total_elapsed = time.time() - start_time
        
        print(f"Phase 1 completed in {phase1_elapsed:.2f}s (total: {total_elapsed:.2f}s)")
        
        # Check if we've exceeded the threshold
        if total_elapsed > CLAUDE_TIMEOUT_THRESHOLD:
            print(f"Claude exceeded {CLAUDE_TIMEOUT_THRESHOLD}s threshold ({total_elapsed:.2f}s). Switching to OpenAI...")
            log_claude_timeout(ticket_id or "unknown", total_elapsed, "Phase 1", fallback_to_openai=True)
            result = get_openai_fallback_analysis(conversation, ticket_id=ticket_id, timeout=timeout)
            result['ai_provider'] = 'OpenAI'
            return result
    except (TimeoutError, Exception) as e:
        total_elapsed = time.time() - start_time
        error_msg = str(e)
        if 'timeout' in error_msg.lower() or isinstance(e, TimeoutError):
            print(f"Claude Phase 1 timeout after {total_elapsed:.2f}s. Switching to OpenAI...")
            log_claude_timeout(ticket_id or "unknown", total_elapsed, "Phase 1", fallback_to_openai=True)
            result = get_openai_fallback_analysis(conversation, ticket_id=ticket_id, timeout=timeout)
            result['ai_provider'] = 'OpenAI'
            return result
        else:
            # For non-timeout errors, still try OpenAI as fallback
            print(f"Claude Phase 1 error: {error_msg}. Switching to OpenAI...")
            log_claude_timeout(ticket_id or "unknown", total_elapsed, "Phase 1", fallback_to_openai=True)
            result = get_openai_fallback_analysis(conversation, ticket_id=ticket_id, timeout=timeout)
            result['ai_provider'] = 'OpenAI'
            return result
    
    # Phase 1 succeeded, continue with validation and Phase 2/3
    print("DEBUG: Phase 1 succeeded, continuing to validation...")
    issue_description = ticket_analysis.get('issue_description', '')
    root_cause = ticket_analysis.get('root_cause', '')
    print(f"DEBUG: issue_description length: {len(issue_description)}, root_cause length: {len(root_cause)}")
    
    # Apply validation logic (same as in enhanced function)
    root_cause_lower = root_cause.lower() if root_cause else ''
    issue_description_lower = issue_description.lower() if issue_description else ''
    combined_text = f"{root_cause_lower} {issue_description_lower}"
    
    root_cause_not_identified = any(phrase in root_cause_lower for phrase in [
        'not identified', 'unable to determine', 'unknown', 'not clear', 
        'cannot be determined', 'not specified', 'not found', 'unclear',
        'ambiguous', 'vague', 'not known'
    ])
    
    functional_gap_indicators = any(phrase in issue_description_lower for phrase in [
        'no task created', 'task not created', 'not created', 'missing task',
        'no failure reported', 'no error', 'silent failure', 'silently failed',
        'did not trigger', 'not triggered', 'failed to trigger', 'should have',
        'expected to', 'supposed to', 'should create', 'should generate',
        'missing', 'not generated', 'not executed', 'not running',
        'stuck', 'stopped', 'not working', 'not functioning'
    ])
    
    code_bug_indicators = any(phrase in root_cause_lower for phrase in [
        'exception', 'not properly handled', 'not handled', 'error handling',
        'exception handling', 'bug', 'defect', 'failure', 'crash', 'leak',
        'memory leak', 'race condition', 'deadlock', 'timeout', 'retry',
        'logic error', 'algorithm error', 'validation error', 'processing error',
        'data corruption', 'data loss', 'incorrect', 'wrong', 'invalid',
        'missing check', 'missing validation', 'missing error', 'missing exception'
    ])
    
    is_pure_user_mistake = any(phrase in combined_text for phrase in [
        'user error', 'user mistake', 'user configuration error',
        'user did not', 'user failed to', 'user misunderstood',
        'user did not follow', 'user not following'
    ]) and not code_bug_indicators
    
    is_product_limitation = any(phrase in combined_text for phrase in [
        'product limitation', 'system limitation', 'by design',
        'documented limitation', 'known limitation', 'working as designed',
        'feature does not exist', 'feature not available', 'missing feature',
        'not supported', 'not implemented', 'out of scope'
    ]) and not code_bug_indicators
    
    if root_cause_not_identified and functional_gap_indicators:
        ticket_analysis['test_case_needed'] = True
        ticket_analysis['test_case_needed_reason'] = f'Functional gap identified: {issue_description[:100]}... - test case needed to validate expected behavior even though root cause is unclear'
    elif root_cause_not_identified and not functional_gap_indicators:
        ticket_analysis['test_case_needed'] = False
        ticket_analysis['test_case_needed_reason'] = 'Root cause not identified and no clear functional gap - cannot create meaningful test cases without a specific root cause or expected behavior to validate'
    elif code_bug_indicators:
        ticket_analysis['test_case_needed'] = True
        ticket_analysis['test_case_needed_reason'] = f'Code bug identified: {root_cause[:100]} - test case needed to validate fix and prevent regression'
    elif is_pure_user_mistake or is_product_limitation:
        ticket_analysis['test_case_needed'] = False
        ticket_analysis['test_case_needed_reason'] = 'User mistake or product limitation - test cases should only be created for functional bugs requiring code/logic fixes'
    
    print(f"DEBUG: test_case_needed = {ticket_analysis.get('test_case_needed')}")
    if not ticket_analysis.get('test_case_needed'):
        total_elapsed = time.time() - start_time
        print(f"Test case not needed, returning early. Total time: {total_elapsed:.2f}s")
        result = {
            'issue_description': issue_description,
            'root_cause': root_cause,
            'issue_theme': ticket_analysis.get('issue_theme', 'Unknown Theme'),
            'root_cause_theme': ticket_analysis.get('root_cause_theme', 'Unknown Root Cause Theme'),
            'test_case_needed': False,
            'test_case_needed_reason': ticket_analysis.get('test_case_needed_reason', ''),
            'regression_test_needed': None,
            'regression_test_needed_reason': 'N/A - Test case not needed',
            'test_cases': [],
            'num_test_cases': 0,
            'test_case_description': 'N/A - Test case not needed',
            'test_case_steps': 'N/A - Test case not needed',
            'recommended_solution': 'N/A - Test case not needed',
            'additional_test_scenarios': 'N/A - Test case not needed',
            'search_queries_used': [],
            'search_results_summary': '',
            'documentation_references': [],
            'is_documented_limitation': False,
            'is_documented_prerequisite': False,
            'documentation_check_summary': '',
            'ai_provider': 'Claude'
        }
        return result
    
    # Test case is needed, continue to Phase 2 and 3
    print(f"Test case needed: {ticket_analysis.get('test_case_needed_reason', '')[:100]}...")
    
    # Phase 2: Generate search queries - wrap in try-except
    try:
        phase2_start = time.time()
        print("Phase 2: Generating search queries from issue description...")
        search_queries = claude_service.generate_search_queries(
            issue_description=issue_description,
            root_cause=root_cause,
            timeout=20  # Reduced timeout
        )
        phase2_elapsed = time.time() - phase2_start
        total_elapsed = time.time() - start_time
        print(f"Phase 2 completed in {phase2_elapsed:.2f}s (total: {total_elapsed:.2f}s)")
        
        if total_elapsed > CLAUDE_TIMEOUT_THRESHOLD:
            print(f"Claude exceeded {CLAUDE_TIMEOUT_THRESHOLD}s threshold ({total_elapsed:.2f}s) after Phase 2. Switching to OpenAI...")
            log_claude_timeout(ticket_id or "unknown", total_elapsed, "Phase 2", fallback_to_openai=True)
            result = get_openai_fallback_analysis(conversation, ticket_id=ticket_id, timeout=timeout)
            result['ai_provider'] = 'OpenAI'
            return result
    except (TimeoutError, Exception) as e:
        total_elapsed = time.time() - start_time
        error_msg = str(e)
        print(f"ERROR in Phase 2: {error_msg}")
        # For any error in Phase 2, switch to OpenAI to ensure we get a result
        print(f"Claude Phase 2 error after {total_elapsed:.2f}s. Switching to OpenAI...")
        log_claude_timeout(ticket_id or "unknown", total_elapsed, "Phase 2", fallback_to_openai=True)
        result = get_openai_fallback_analysis(conversation, ticket_id=ticket_id, timeout=timeout)
        result['ai_provider'] = 'OpenAI'
        return result
    
    # Execute searches
    print(f"Searching for solutions using {len(search_queries)} queries...")
    all_search_results = {'web': [], 'stackoverflow': []}
    
    for query in search_queries[:3]:
        print(f"  Searching: {query}")
        try:
            results = search_service.search_all(query, max_results=3)
            all_search_results['web'].extend(results.get('web', []))
            all_search_results['stackoverflow'].extend(results.get('stackoverflow', []))
        except Exception as e:
            print(f"  Search failed for query '{query}': {str(e)}")
            continue  # Continue with other queries
    
    # Remove duplicates
    seen_links = set()
    for source in ['web', 'stackoverflow']:
        unique_results = []
        for result in all_search_results[source]:
            link = result.get('link', '')
            if link and link not in seen_links:
                seen_links.add(link)
                unique_results.append(result)
        all_search_results[source] = unique_results[:5]
    
    # Phase 3: Generate enhanced test case - wrap in try-except
    try:
        phase3_start = time.time()
        print("Phase 3: Generating enhanced test case with solution context...")
        enhanced_results = claude_service.generate_test_case_with_solutions(
            ticket_analysis,
            all_search_results,
            doc_check=None,
            timeout=30  # Reduced timeout
        )
        phase3_elapsed = time.time() - phase3_start
        total_elapsed = time.time() - start_time
        print(f"Phase 3 completed in {phase3_elapsed:.2f}s (total: {total_elapsed:.2f}s)")
        
        if total_elapsed > CLAUDE_TIMEOUT_THRESHOLD:
            print(f"Claude exceeded {CLAUDE_TIMEOUT_THRESHOLD}s threshold ({total_elapsed:.2f}s) after Phase 3. Switching to OpenAI...")
            log_claude_timeout(ticket_id or "unknown", total_elapsed, "Phase 3", fallback_to_openai=True)
            result = get_openai_fallback_analysis(conversation, ticket_id=ticket_id, timeout=timeout)
            result['ai_provider'] = 'OpenAI'
            return result
    except (TimeoutError, Exception) as e:
        total_elapsed = time.time() - start_time
        error_msg = str(e)
        print(f"ERROR in Phase 3: {error_msg}")
        # For any error in Phase 3, switch to OpenAI to ensure we get a result
        print(f"Claude Phase 3 error after {total_elapsed:.2f}s. Switching to OpenAI...")
        log_claude_timeout(ticket_id or "unknown", total_elapsed, "Phase 3", fallback_to_openai=True)
        result = get_openai_fallback_analysis(conversation, ticket_id=ticket_id, timeout=timeout)
        result['ai_provider'] = 'OpenAI'
        return result
    
    # Phase 3 succeeded, prepare final results
    if 'enhanced_results' not in locals():
        print("ERROR: enhanced_results not defined after Phase 3, switching to OpenAI...")
        result = get_openai_fallback_analysis(conversation, ticket_id=ticket_id, timeout=timeout)
        result['ai_provider'] = 'OpenAI'
        return result
    
    enhanced_results['search_queries_used'] = search_queries
    if 'search_results_summary' not in enhanced_results or not enhanced_results.get('search_results_summary'):
        enhanced_results['search_results_summary'] = search_service.format_search_results_for_prompt(all_search_results)
    enhanced_results['documentation_references'] = []
    enhanced_results['is_documented_limitation'] = False
    enhanced_results['is_documented_prerequisite'] = False
    enhanced_results['documentation_check_summary'] = ''
    enhanced_results['ai_provider'] = 'Claude'
    
    total_elapsed = time.time() - start_time
    print(f"Claude analysis complete in {total_elapsed:.2f}s!")
    return enhanced_results

# Old function removed - use get_ticket_analysis instead
def _removed_get_openai_fallback_analysis(conversation, ticket_id=None, timeout=120):
    """
    OpenAI fallback version: Generate summary and test case with web search for solutions.
    Used when Claude API times out (>50 seconds).
    Uses same three-phase approach as Claude version.
    Always returns a result, handles timeouts gracefully.
    
    Args:
        conversation: The ticket conversation text
        ticket_id: Optional ticket ID for logging
        timeout: Total timeout in seconds (default: 120)
    Returns:
        dict with issue_description, root_cause, test_case_needed, regression_test_needed, 
        test_case_description, test_case_steps, recommended_solution, additional_test_scenarios,
        search_queries_used, search_results_summary, ai_provider='OpenAI'
    """
    if not openai_fallback_service:
        raise Exception("OPENAI_API_KEY is not set. Cannot use OpenAI fallback.")
    
    print("Using OpenAI fallback...")
    start_time = time.time()
    
    # Phase 1: Analyze ticket and extract root cause
    try:
        print("Phase 1: Analyzing ticket with OpenAI...")
        ticket_analysis = openai_fallback_service.analyze_ticket_phase1(conversation, timeout=30)
        
        issue_description = ticket_analysis.get('issue_description', '')
        root_cause = ticket_analysis.get('root_cause', '')
        
        # STRICT VALIDATION: Check multiple conditions that should prevent test case creation
        root_cause_lower = root_cause.lower() if root_cause else ''
        issue_description_lower = issue_description.lower() if issue_description else ''
        combined_text = f"{root_cause_lower} {issue_description_lower}"
        
        # 1. Check if root cause is NOT CLEAR
        root_cause_not_identified = any(phrase in root_cause_lower for phrase in [
            'not identified', 'unable to determine', 'unknown', 'not clear', 
            'cannot be determined', 'not specified', 'not found', 'unclear',
            'ambiguous', 'vague', 'not known'
        ])
        
        # 1b. Check for FUNCTIONAL GAPS or MISSING BEHAVIOR that still need test cases
        functional_gap_indicators = any(phrase in issue_description_lower for phrase in [
            'no task created', 'task not created', 'not created', 'missing task',
            'no failure reported', 'no error', 'silent failure', 'silently failed',
            'did not trigger', 'not triggered', 'failed to trigger', 'should have',
            'expected to', 'supposed to', 'should create', 'should generate',
            'missing', 'not generated', 'not executed', 'not running',
            'stuck', 'stopped', 'not working', 'not functioning'
        ])
        
        # 2. Check for CODE BUGS/ISSUES that REQUIRE test cases
        code_bug_indicators = any(phrase in root_cause_lower for phrase in [
            'exception', 'not properly handled', 'not handled', 'error handling',
            'exception handling', 'bug', 'defect', 'failure', 'crash', 'leak',
            'memory leak', 'race condition', 'deadlock', 'timeout', 'retry',
            'logic error', 'algorithm error', 'validation error', 'processing error',
            'data corruption', 'data loss', 'incorrect', 'wrong', 'invalid',
            'missing check', 'missing validation', 'missing error', 'missing exception'
        ])
        
        # 3. Check if issue is PURELY USER MISTAKE or PRODUCT LIMITATION
        is_pure_user_mistake = any(phrase in combined_text for phrase in [
            'user error', 'user mistake', 'user configuration error',
            'user did not', 'user failed to', 'user misunderstood',
            'user did not follow', 'user not following'
        ]) and not code_bug_indicators
        
        is_product_limitation = any(phrase in combined_text for phrase in [
            'product limitation', 'system limitation', 'by design',
            'documented limitation', 'known limitation', 'working as designed',
            'feature does not exist', 'feature not available', 'missing feature',
            'not supported', 'not implemented', 'out of scope'
        ]) and not code_bug_indicators
        
        # Apply strict validation
        if root_cause_not_identified and functional_gap_indicators:
            ticket_analysis['test_case_needed'] = True
            ticket_analysis['test_case_needed_reason'] = f'Functional gap identified: {issue_description[:100]}... - test case needed to validate expected behavior even though root cause is unclear'
        elif root_cause_not_identified and not functional_gap_indicators:
            ticket_analysis['test_case_needed'] = False
            ticket_analysis['test_case_needed_reason'] = 'Root cause not identified and no clear functional gap - cannot create meaningful test cases without a specific root cause or expected behavior to validate'
        elif code_bug_indicators:
            ticket_analysis['test_case_needed'] = True
            ticket_analysis['test_case_needed_reason'] = f'Code bug identified: {root_cause[:100]} - test case needed to validate fix and prevent regression'
        elif is_pure_user_mistake or is_product_limitation:
            ticket_analysis['test_case_needed'] = False
            ticket_analysis['test_case_needed_reason'] = 'User mistake or product limitation - test cases should only be created for functional bugs requiring code/logic fixes'
        
        # If test case is not needed, return early
        if not ticket_analysis.get('test_case_needed'):
            return {
                'issue_description': issue_description,
                'root_cause': root_cause,
                'issue_theme': ticket_analysis.get('issue_theme', 'Unknown Theme'),
                'root_cause_theme': ticket_analysis.get('root_cause_theme', 'Unknown Root Cause Theme'),
                'test_case_needed': False,
                'test_case_needed_reason': ticket_analysis.get('test_case_needed_reason', ''),
                'regression_test_needed': None,
                'regression_test_needed_reason': 'N/A - Test case not needed',
                'test_cases': [],
                'num_test_cases': 0,
                'test_case_description': 'N/A - Test case not needed',
                'test_case_steps': 'N/A - Test case not needed',
                'recommended_solution': 'N/A - Test case not needed',
                'additional_test_scenarios': 'N/A - Test case not needed',
                'search_queries_used': [],
                'search_results_summary': '',
                'documentation_references': [],
                'is_documented_limitation': False,
                'is_documented_prerequisite': False,
                'documentation_check_summary': '',
                'ai_provider': 'OpenAI'
            }
        
        # Phase 2: Generate search queries and search for solutions
        try:
            print("Phase 2: Generating search queries from issue description with OpenAI...")
            search_queries = openai_fallback_service.generate_search_queries(
                issue_description=issue_description,
                root_cause=root_cause,
                timeout=20  # Reduced timeout
            )
        except (TimeoutError, Exception) as e:
            print(f"OpenAI Phase 2 failed: {str(e)}. Continuing with empty search queries...")
            search_queries = []
        
        # Execute searches
        print(f"Searching for solutions using {len(search_queries)} queries...")
        all_search_results = {'web': [], 'stackoverflow': []}
        
        for query in search_queries[:3]:  # Limit to 3 queries
            print(f"  Searching: {query}")
            try:
                results = search_service.search_all(query, max_results=3)
                all_search_results['web'].extend(results.get('web', []))
                all_search_results['stackoverflow'].extend(results.get('stackoverflow', []))
            except Exception as e:
                print(f"  Search failed for query '{query}': {str(e)}")
                continue  # Continue with other queries
        
        # Remove duplicates (by link)
        seen_links = set()
        for source in ['web', 'stackoverflow']:
            unique_results = []
            for result in all_search_results[source]:
                link = result.get('link', '')
                if link and link not in seen_links:
                    seen_links.add(link)
                    unique_results.append(result)
            all_search_results[source] = unique_results[:5]  # Limit to 5 per source
        
        # Phase 3: Generate enhanced test case with search results
        try:
            print("Phase 3: Generating enhanced test case with solution context using OpenAI...")
            enhanced_results = openai_fallback_service.generate_test_case_with_solutions(
                ticket_analysis,
                all_search_results,
                doc_check=None,
                timeout=30  # Reduced timeout
            )
        except (TimeoutError, Exception) as e:
            print(f"OpenAI Phase 3 failed: {str(e)}. Returning partial results...")
            # Return partial results if Phase 3 fails
            enhanced_results = {
                'issue_description': issue_description,
                'root_cause': root_cause,
                'issue_theme': ticket_analysis.get('issue_theme', 'Unknown Theme'),
                'root_cause_theme': ticket_analysis.get('root_cause_theme', 'Unknown Root Cause Theme'),
                'test_case_needed': True,
                'test_case_needed_reason': 'Test case generation timed out, but analysis completed',
                'regression_test_needed': None,
                'regression_test_needed_reason': 'N/A - Generation incomplete',
                'test_cases': [],
                'num_test_cases': 0,
                'test_case_description': 'Test case generation timed out. Please retry.',
                'test_case_steps': 'Test case generation timed out. Please retry.',
                'recommended_solution': 'Analysis completed but test case generation timed out.',
                'additional_test_scenarios': '',
                'search_queries_used': search_queries,
                'search_results_summary': search_service.format_search_results_for_prompt(all_search_results) if all_search_results else '',
                'documentation_references': [],
                'is_documented_limitation': False,
                'is_documented_prerequisite': False,
                'documentation_check_summary': ''
            }
        
        # Add search queries used and ensure search_results_summary is set
        enhanced_results['search_queries_used'] = search_queries
        if 'search_results_summary' not in enhanced_results or not enhanced_results.get('search_results_summary'):
            enhanced_results['search_results_summary'] = search_service.format_search_results_for_prompt(all_search_results)
        enhanced_results['documentation_references'] = []
        enhanced_results['is_documented_limitation'] = False
        enhanced_results['is_documented_prerequisite'] = False
        enhanced_results['documentation_check_summary'] = ''
        enhanced_results['ai_provider'] = 'OpenAI'
        
        total_elapsed = time.time() - start_time
        print(f"OpenAI fallback analysis complete in {total_elapsed:.2f}s!")
        return enhanced_results
    except Exception as e:
        # Last resort: return minimal result
        error_msg = str(e)
        print(f"OpenAI fallback completely failed: {error_msg}")
        return {
            'issue_description': 'Analysis failed due to timeout/error',
            'root_cause': 'Unable to analyze - both Claude and OpenAI failed',
            'issue_theme': 'Error',
            'root_cause_theme': 'Error',
            'test_case_needed': False,
            'test_case_needed_reason': f'Analysis failed: {error_msg[:100]}',
            'regression_test_needed': None,
            'regression_test_needed_reason': 'N/A - Analysis failed',
            'test_cases': [],
            'num_test_cases': 0,
            'test_case_description': 'Analysis failed',
            'test_case_steps': 'Analysis failed',
            'recommended_solution': 'Please retry the analysis',
            'additional_test_scenarios': '',
            'search_queries_used': [],
            'search_results_summary': '',
            'documentation_references': [],
            'is_documented_limitation': False,
            'is_documented_prerequisite': False,
            'documentation_check_summary': '',
            'ai_provider': 'Error'
        }

# Old function removed - use get_ticket_analysis instead
def _removed_get_openai_summary_and_testcase(conversation, timeout=60):
    """
    Legacy function - removed, use get_ticket_analysis instead.
    """
    pass
    # Removed - use get_ticket_analysis instead
    if False:  # Never executed
        if not openai_service:
            raise Exception("OPENAI_API_KEY is not set. Please set it in your environment variables.")
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

IMPORTANT FOR TEST CASE GENERATION: When writing test case descriptions and steps, you MUST ensure they are GENERIC and REUSABLE:
- Test cases MUST be GENERIC - use "any column", "all columns", "any table", "any field", etc.
- DO NOT use specific column names, table names, field names, or instance-specific details from the ticket
- Validate the ROOT CAUSE PATTERN/CLASS, not the specific instance
- The test should catch similar issues across different columns, tables, fields, or instances
- Reproduce the PATTERN of conditions that led to the root cause (not the exact instance)
- Verify the system handles those PATTERNS correctly after the fix
- Fail if the root cause PATTERN still exists anywhere
- Pass when the root cause PATTERN is properly addressed
- Example: "Validate permissions for all columns" (NOT "Validate permissions for column A")

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
5. Test Case Description: (ONLY provide this if Test Case Needed is "Yes") MUST be GENERIC and REUSABLE - NOT instance-specific. Describe the CLASS/PATTERN of issue, not the specific instance. Use generic terminology like "any column", "all columns", "any table", "any field", etc. DO NOT use specific column names, table names, field names, or instance-specific identifiers from the ticket. The test case should validate the ROOT CAUSE PATTERN so it catches similar issues across different instances. Examples: "Validate permissions for all columns in any table" (NOT "Validate permissions for column A"), "Test NULL value handling for all columns" (NOT "Test NULL values in column email"). If Test Case Needed is "No", write "N/A - Test case not needed"
6. Test Case Steps: (ONLY provide this if Test Case Needed is "Yes") MUST be GENERIC and REUSABLE. Use generic placeholders: "any column" or "all columns" (NOT specific column names), "the table" or "any table" (NOT specific table names), "any field" or "all fields" (NOT specific field names), "any user" (NOT specific user IDs), "any permission" (NOT specific permission names). DO NOT mention ticket-specific data/column/table/field names. The steps should validate the PATTERN/CLASS of issue, not the specific instance. Example: "For each column in the table, verify it has appropriate permissions" (NOT "Verify column 'customer_id' has read permission"). If Test Case Needed is "No", write "N/A - Test case not needed"

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
        resp = claude_service.client.messages.create(
            model=claude_service.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout,
        )
        output = resp.content[0].text
        
        # Debug logging: Log full AI response for troubleshooting
        print(f"\n=== AI Response for ticket ===\n{output}\n=== End AI Response ===\n")
        
    except TimeoutError:
        raise TimeoutError(f"Claude API request timed out after {timeout} seconds")
    except Exception as e:
        # Handle connection errors, broken pipes, etc.
        error_msg = str(e)
        if 'BrokenPipeError' in error_msg or 'broken pipe' in error_msg.lower():
            raise Exception(f"Connection interrupted: The request was interrupted. Please try again.")
        raise Exception(f"Claude API error: {error_msg}")
    
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
        session.pop('issue_theme', None)
        session.pop('root_cause_theme', None)
        session.pop('test_case_needed', None)
        session.pop('test_case_needed_reason', None)
        session.pop('regression_test_needed', None)
        session.pop('regression_test_needed_reason', None)
        session.pop('test_case_description', None)
        session.pop('test_case_steps', None)
        session.pop('test_cases', None)
        session.pop('num_test_cases', None)
        session.pop('recommended_solution', None)
        session.pop('documentation_references', None)
        session.pop('is_documented_limitation', None)
        session.pop('is_documented_prerequisite', None)
        session.pop('documentation_check_summary', None)
        session.pop('additional_test_scenarios', None)
        session.pop('search_queries_used', None)
        session.pop('search_results_summary', None)
        
        if not ticket_id:
            session['error'] = "Please enter a ticket ID."
        else:
            try:
                # Step 1: Fetch ticket details to get requester_id (customer)
                ticket_response = fetch_zendesk_ticket_details(ticket_id, max_retries=3, base_timeout=30)
                
                if ticket_response.status_code != 200:
                    session['error'] = f"Zendesk API error (ticket details): {ticket_response.status_code}"
                else:
                    ticket_data = ticket_response.json().get('ticket', {})
                    requester_id = ticket_data.get('requester_id')
                    print(f"Ticket {ticket_id} - Requester ID (customer): {requester_id}")
                    
                    # Step 2: Fetch all comments (including internal notes for richer context)
                    comments_response = fetch_zendesk_ticket_comments(ticket_id, max_retries=3, base_timeout=30)
                    
                    if comments_response.status_code != 200:
                        session['error'] = f"Zendesk API error (comments): {comments_response.status_code}"
                    else:
                        comments_data = comments_response.json()
                        all_comments = comments_data.get('comments', [])
                        
                        # Step 3: Format conversation with [CUSTOMER]/[AGENT]/[AGENT - INTERNAL] labels
                        conversation = format_structured_conversation(ticket_data, all_comments)
                        
                        if conversation:
                            # Log the structured conversation for debugging
                            print(f"Structured conversation for ticket {ticket_id}:")
                            print(f"  - Total comments: {len(all_comments)}")
                            print(f"  - Customer comments: {sum(1 for c in all_comments if c.get('author_id') == requester_id)}")
                            print(f"  - Agent public comments: {sum(1 for c in all_comments if c.get('author_id') != requester_id and c.get('public'))}")
                            print(f"  - Agent internal notes: {sum(1 for c in all_comments if c.get('author_id') != requester_id and not c.get('public'))}")
                            
                            # Generate summary with enhanced context
                            print(f"Starting analysis for ticket {ticket_id}...")
                            try:
                                fields = get_ticket_analysis(conversation, ticket_id=ticket_id, timeout=120)
                                print(f"Analysis complete for ticket {ticket_id}")
                                
                                # Validate fields before saving
                                if fields is None:
                                    print(f"ERROR: get_ticket_analysis returned None for ticket {ticket_id}")
                                    session['error'] = "Analysis failed: No results returned. Please try again."
                                elif not isinstance(fields, dict):
                                    print(f"ERROR: get_ticket_analysis returned non-dict for ticket {ticket_id}: {type(fields)}")
                                    session['error'] = "Analysis failed: Invalid result format. Please try again."
                                else:
                                    # Save to database FIRST (before storing in session)
                                    save_ticket_summary(ticket_id, fields)
                            except Exception as e:
                                print(f"Error during analysis for ticket {ticket_id}: {str(e)}")
                                import traceback
                                traceback.print_exc()
                                raise
                            
                            # Only store ticket_id in session to avoid cookie size limit
                            # All data will be retrieved from database on GET request
                            # This prevents "cookie too large" errors
                        else:
                            session['error'] = "No conversation found for this ticket."
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
                # Log the full error for debugging
                import traceback
                error_trace = traceback.format_exc()
                print(f"ERROR processing ticket {ticket_id}: {error_msg}")
                print(f"Full traceback:\n{error_trace}")
                
                if 'BrokenPipeError' in error_msg or 'broken pipe' in error_msg.lower() or 'EPIPE' in error_msg:
                    session['error'] = "Connection interrupted. Please try again."
                elif 'OPENAI_API_KEY' in error_msg:
                    session['error'] = "OpenAI API key is not configured. Please set OPENAI_API_KEY in your environment variables."
                elif '404' in error_msg or 'not_found' in error_msg.lower():
                    session['error'] = f"OpenAI API error: Model not found. Please check the model name. Error: {error_msg[:200]}"
                elif '401' in error_msg or 'unauthorized' in error_msg.lower():
                    session['error'] = f"OpenAI API authentication failed. Please check your OPENAI_API_KEY. Error: {error_msg[:200]}"
                elif 'timeout' in error_msg.lower():
                    session['error'] = f"Request timed out. The analysis took too long. Please try again or use a shorter timeout. Error: {error_msg[:200]}"
                else:
                    session['error'] = f"Error processing ticket: {error_msg[:500]}"
        
        # Ensure session is saved before redirect
        try:
            session.modified = True
        except Exception:
            pass
        
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
    
    # GET request - retrieve data from session and database
    ticket_id = session.pop('ticket_id', '')
    error = session.pop('error', '')
    
    # Retrieve data from database if ticket_id is present
    # This avoids cookie size limits by storing data in DB instead of session
    fields = {}
    if ticket_id:
        ticket_data = get_ticket_summary(ticket_id)
        if ticket_data:
            fields = format_ticket_for_display(ticket_data)
            # Remove ticket_id from fields since we pass it explicitly
            fields.pop('ticket_id', None)
        else:
            # If ticket not in DB yet, it might still be processing
            # Keep fields empty and show loading state
            fields = {
                'issue_description': '',
                'root_cause': '',
                'issue_theme': '',
                'root_cause_theme': '',
                'test_case_needed': None,
                'test_case_needed_reason': '',
                'regression_test_needed': None,
                'regression_test_needed_reason': '',
                'test_case_description': '',
                'test_case_steps': '',
                'test_cases': [],
                'num_test_cases': 0,
                'recommended_solution': '',
                'additional_test_scenarios': '',
                'search_queries_used': [],
                'search_results_summary': '',
                'documentation_references': [],
                'is_documented_limitation': False,
                'is_documented_prerequisite': False,
                'documentation_check_summary': ''
            }
    else:
        # No ticket_id, use empty fields
        fields = {
            'issue_description': '',
            'root_cause': '',
            'issue_theme': '',
            'root_cause_theme': '',
            'test_case_needed': None,
            'test_case_needed_reason': '',
            'regression_test_needed': None,
            'regression_test_needed_reason': '',
            'test_case_description': '',
            'test_case_steps': '',
            'test_cases': [],
            'num_test_cases': 0,
            'recommended_solution': '',
            'additional_test_scenarios': '',
            'search_queries_used': [],
            'search_results_summary': '',
            'documentation_references': [],
            'is_documented_limitation': False,
            'is_documented_prerequisite': False,
            'documentation_check_summary': ''
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

@app.route('/api/scraper/status')
def get_scraper_status_api():
    """API endpoint to get documentation scraper status."""
    try:
        import os
        status_file = os.path.join(os.path.dirname(__file__), 'scraper_status.json')
        
        if not os.path.exists(status_file):
            return jsonify({
                'status': 'not_started',
                'pages_scraped': 0,
                'total_vectors': 0,
                'total_chunks': 0,
                'current_url': '',
                'start_time': None,
                'last_update': None,
                'estimated_remaining_minutes': None,
                'progress_percentage': 0
            })
        
        with open(status_file, 'r') as f:
            status = json.load(f)
        
        # Pinecone removed - no vector embeddings
        
        return jsonify(status)
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e),
            'pages_scraped': 0,
            'total_vectors': 0,
            'total_chunks': 0
        }), 500

# ============================================================
# PRIORITY ANALYZER ROUTES (Q1 Planning Module)
# ============================================================

@app.route('/priority', methods=['GET', 'POST'])
def priority_index():
    """Priority Analyzer page for Q1 planning."""
    if request.method == 'POST':
        ticket_id = request.form.get('ticket_id')
        
        # Store ticket_id in session for the redirect
        session['priority_ticket_id'] = ticket_id or ''
        session.pop('priority_error', None)
        
        if not ticket_id:
            session['priority_error'] = "Please enter a ticket ID."
        else:
            try:
                # Step 1: Fetch ticket details
                ticket_response = fetch_zendesk_ticket_details(ticket_id, max_retries=3, base_timeout=30)
                
                if ticket_response.status_code != 200:
                    session['priority_error'] = f"Zendesk API error (ticket details): {ticket_response.status_code}"
                else:
                    ticket_data = ticket_response.json().get('ticket', {})
                    requester_id = ticket_data.get('requester_id')
                    
                    # Extract custom_fields from ticket data and map them
                    custom_fields = ticket_data.get('custom_fields', [])
                    field_mapping = get_field_mapping()
                    mapped_ticket_fields = map_ticket_fields(custom_fields, field_mapping)
                    
                    if mapped_ticket_fields:
                        print(f"Ticket {ticket_id}: Mapped {len(mapped_ticket_fields)} ticket fields: {list(mapped_ticket_fields.keys())}")
                    
                    # Step 2: Fetch all comments
                    comments_response = fetch_zendesk_ticket_comments(ticket_id, max_retries=3, base_timeout=30)
                    
                    if comments_response.status_code != 200:
                        session['priority_error'] = f"Zendesk API error (comments): {comments_response.status_code}"
                    else:
                        comments_data = comments_response.json()
                        
                        # Handle different response structures from Zendesk API
                        # The ticket endpoint with include=comments returns: {ticket: {...}, comments: [...]}
                        # The comments endpoint returns: {comments: [...]}
                        if 'comments' in comments_data:
                            all_comments = comments_data.get('comments', [])
                        elif 'ticket' in comments_data and 'comments' in comments_data:
                            all_comments = comments_data.get('comments', [])
                        else:
                            # Try to find comments in nested structure
                            all_comments = comments_data.get('comments', [])
                            if not all_comments and 'ticket' in comments_data:
                                ticket_obj = comments_data.get('ticket', {})
                                all_comments = ticket_obj.get('comments', [])
                        
                        # Debug: Log comment types for ticket 64258
                        if ticket_id == '64258' or ticket_id == 64258:
                            public_count = sum(1 for c in all_comments if c.get('public', True))
                            internal_count = sum(1 for c in all_comments if not c.get('public', True))
                            print(f"DEBUG Ticket {ticket_id}: {len(all_comments)} total comments ({public_count} public, {internal_count} internal)")
                            if internal_count == 0:
                                print(f"WARNING: No internal comments found for ticket {ticket_id}!")
                                print(f"Response structure keys: {list(comments_data.keys())}")
                                print(f"Sample comment keys: {list(all_comments[0].keys()) if all_comments else 'No comments'}")
                        
                        # Step 3: Format conversation
                        conversation = format_structured_conversation(ticket_data, all_comments)
                        
                        if conversation:
                            # Step 4: Analyze priority with AI (including ticket fields)
                            print(f"Starting priority analysis for ticket {ticket_id}...")
                            
                            if not priority_service:
                                session['priority_error'] = "OpenAI API key is not configured."
                            else:
                                try:
                                    fields = priority_service.analyze_ticket_priority(
                                        conversation, 
                                        ticket_fields=mapped_ticket_fields if mapped_ticket_fields else None,
                                        timeout=60
                                    )
                                    print(f"Priority analysis complete for ticket {ticket_id}")
                                    
                                    # Add ticket_fields to fields dict for database storage
                                    fields['ticket_fields'] = mapped_ticket_fields
                                    
                                    # Extract deal value from ticket_fields first, then from signal_details
                                    deal_value = extract_deal_value(
                                        ticket_fields=mapped_ticket_fields,
                                        signal_details=fields.get('signal_details', '')
                                    )
                                    if deal_value:
                                        fields['deal_value'] = deal_value
                                        print(f"Ticket {ticket_id}: Extracted deal value: {deal_value}")
                                    
                                    # Save to database
                                    save_ticket_priority(ticket_id, fields)
                                except Exception as e:
                                    print(f"Error during priority analysis for ticket {ticket_id}: {str(e)}")
                                    session['priority_error'] = f"Analysis error: {str(e)[:200]}"
                        else:
                            session['priority_error'] = "No conversation found for this ticket."
            except Timeout as e:
                session['priority_error'] = f"Request timed out. Please try again."
            except RequestException as e:
                session['priority_error'] = f"Network error: {str(e)[:200]}"
            except Exception as e:
                session['priority_error'] = f"Error processing ticket: {str(e)[:200]}"
        
        # Redirect to GET
        session.modified = True
        return redirect(url_for('priority_index'))
    
    # GET request - retrieve data from session and database
    ticket_id = session.pop('priority_ticket_id', '')
    error = session.pop('priority_error', '')
    
    # Retrieve data from database if ticket_id is present
    priority_data = {}
    if ticket_id:
        try:
            priority_row = get_ticket_priority(ticket_id)
            if priority_row:
                priority_data = format_priority_for_display(priority_row)
                # Remove ticket_id from priority_data to avoid duplicate keyword argument
                priority_data.pop('ticket_id', None)
        except Exception as e:
            print(f"Error retrieving priority data for ticket {ticket_id}: {str(e)}")
    
    # Get recent priorities for display
    try:
        recent_priorities = get_recent_priorities(limit=3)
    except Exception as e:
        print(f"Error retrieving recent priorities: {str(e)}")
        recent_priorities = []
    
    return render_template('priority.html', 
                           ticket_id=ticket_id, 
                           error=error, 
                           recent_priorities=recent_priorities,
                           **priority_data)

@app.route('/api/priority/<ticket_id>')
def get_priority_api(ticket_id):
    """API endpoint to get a ticket priority by ID."""
    try:
        priority = get_ticket_priority(ticket_id)
        if priority:
            return jsonify(format_priority_for_display(priority))
        return jsonify({'error': 'Priority analysis not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/priorities/recent')
def get_recent_priorities_api():
    """API endpoint to get recent priorities."""
    try:
        limit = request.args.get('limit', 10, type=int)
        priorities = get_recent_priorities(limit=limit)
        return jsonify(priorities)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================
# END PRIORITY ANALYZER ROUTES
# ============================================================

# ============================================================
# BULK CSV ANALYZER ROUTES
# ============================================================

@app.route('/bulk', methods=['GET', 'POST'])
def bulk_index():
    """Bulk CSV Ticket Analyzer page."""
    if request.method == 'POST':
        # Handle CSV file upload
        if 'csv_file' not in request.files:
            session['bulk_error'] = "No file uploaded."
            return redirect(url_for('bulk_index'))
        
        file = request.files['csv_file']
        
        if file.filename == '':
            session['bulk_error'] = "No file selected."
            return redirect(url_for('bulk_index'))
        
        if not file.filename.endswith('.csv'):
            session['bulk_error'] = "Please upload a CSV file."
            return redirect(url_for('bulk_index'))
        
        try:
            # Parse CSV file
            stream = io.StringIO(file.stream.read().decode("UTF-8"), newline=None)
            csv_reader = csv.DictReader(stream)
            
            # Find ticket_id column (case-insensitive, with variations)
            ticket_ids = []
            fieldnames = csv_reader.fieldnames or []
            
            # Normalize fieldnames to find ticket_id column
            ticket_id_column = None
            for name in fieldnames:
                normalized = name.lower().strip().replace(' ', '_')
                if normalized in ['ticket_id', 'ticketid', 'id', 'ticket']:
                    ticket_id_column = name
                    break
            
            if not ticket_id_column:
                session['bulk_error'] = f"CSV must have a 'ticket_id' column. Found columns: {', '.join(fieldnames)}"
                return redirect(url_for('bulk_index'))
            
            # Extract ticket IDs
            for row in csv_reader:
                ticket_id = row.get(ticket_id_column, '').strip()
                if ticket_id:
                    # Clean up the ticket ID (remove any non-numeric characters if needed)
                    ticket_id = ticket_id.strip()
                    if ticket_id:
                        ticket_ids.append(ticket_id)
            
            if not ticket_ids:
                session['bulk_error'] = "No valid ticket IDs found in CSV."
                return redirect(url_for('bulk_index'))
            
            # Remove duplicates while preserving order
            seen = set()
            unique_ticket_ids = []
            for tid in ticket_ids:
                if tid not in seen:
                    seen.add(tid)
                    unique_ticket_ids.append(tid)
            
            # Get analysis type options
            run_test_case = request.form.get('run_test_case') == '1'
            run_priority = request.form.get('run_priority') == '1'
            
            # Validate at least one is selected
            if not run_test_case and not run_priority:
                session['bulk_error'] = "Please select at least one analysis type."
                return redirect(url_for('bulk_index'))
            
            # Create bulk job
            job_id = str(uuid.uuid4())
            create_bulk_job(job_id, len(unique_ticket_ids))
            
            # Start background processing with analysis options
            from bulk_processor import start_bulk_job
            start_bulk_job(job_id, unique_ticket_ids, run_test_case=run_test_case, run_priority=run_priority)
            
            print(f"Bulk job {job_id} created with {len(unique_ticket_ids)} tickets")
            
            # Store job_id in session for display
            session['bulk_job_id'] = job_id
            session['bulk_success'] = f"Job started! Processing {len(unique_ticket_ids)} tickets."
            
        except Exception as e:
            session['bulk_error'] = f"Error processing CSV: {str(e)[:200]}"
            import traceback
            traceback.print_exc()
        
        return redirect(url_for('bulk_index'))
    
    # GET request
    error = session.pop('bulk_error', '')
    success = session.pop('bulk_success', '')
    active_job_id = session.pop('bulk_job_id', '')
    
    # Get recent bulk jobs for display
    try:
        recent_jobs = get_recent_bulk_jobs(limit=10)
    except Exception as e:
        print(f"Error retrieving recent bulk jobs: {str(e)}")
        recent_jobs = []
    
    return render_template('bulk.html',
                           error=error,
                           success=success,
                           active_job_id=active_job_id,
                           recent_jobs=recent_jobs)


@app.route('/api/bulk/status/<job_id>')
def get_bulk_status(job_id):
    """API endpoint to get bulk job status."""
    try:
        job = get_bulk_job(job_id)
        if job:
            return jsonify({
                'id': job['id'],
                'status': job['status'],
                'total_tickets': job['total_tickets'],
                'processed_count': job['processed_count'],
                'success_count': job['success_count'],
                'failed_count': job['failed_count'],
                'ticket_results': job.get('ticket_results', {}),
                'created_at': job.get('created_at', ''),
                'updated_at': job.get('updated_at', '')
            })
        return jsonify({'error': 'Job not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/bulk/jobs')
def get_bulk_jobs_api():
    """API endpoint to get recent bulk jobs."""
    try:
        limit = request.args.get('limit', 10, type=int)
        jobs = get_recent_bulk_jobs(limit=limit)
        return jsonify(jobs)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/bulk/cancel/<job_id>', methods=['POST'])
def cancel_bulk_job(job_id):
    """API endpoint to cancel a running bulk job."""
    try:
        from bulk_processor import cancel_job
        if cancel_job(job_id):
            return jsonify({'success': True, 'message': 'Cancellation requested'})
        return jsonify({'success': False, 'message': 'Job not found or not running'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================
# END BULK CSV ANALYZER ROUTES
# ============================================================

# ============================================================
# HEALTH CHECK ENDPOINTS
# ============================================================

@app.route('/health')
def health_check():
    """Basic health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    })


@app.route('/api/debug/ticket/<ticket_id>')
def debug_ticket(ticket_id):
    """Debug endpoint to check raw ticket data in database."""
    result = {
        'ticket_id': ticket_id,
        'database_type': 'PostgreSQL' if USE_POSTGRES else 'SQLite',
        'ticket_summary': None,
        'ticket_priority': None,
        'errors': []
    }
    
    try:
        # Check ticket_summaries
        summary = get_ticket_summary(ticket_id)
        if summary:
            result['ticket_summary'] = {
                'found': True,
                'keys': list(summary.keys()),
                'issue_description_length': len(summary.get('issue_description', '') or ''),
                'has_test_cases': bool(summary.get('test_cases')),
            }
        else:
            result['ticket_summary'] = {'found': False}
    except Exception as e:
        result['errors'].append(f"Error fetching summary: {str(e)}")
    
    try:
        # Check ticket_priorities
        priority = get_ticket_priority(ticket_id)
        if priority:
            result['ticket_priority'] = {
                'found': True,
                'keys': list(priority.keys()),
                'priority_score': priority.get('priority_score'),
            }
        else:
            result['ticket_priority'] = {'found': False}
    except Exception as e:
        result['errors'].append(f"Error fetching priority: {str(e)}")
    
    return jsonify(result)


def mask_database_url(url):
    """Mask password in database URL for safe logging."""
    if not url:
        return None
    import re
    # Match postgresql://user:password@host:port/db and mask the password
    masked = re.sub(r'(://[^:]+:)[^@]+(@)', r'\1****\2', url)
    return masked


@app.route('/health/db')
def database_health_check():
    """Database connection health check with detailed diagnostics."""
    # Log DATABASE_URL for debugging (masked)
    masked_url = mask_database_url(DATABASE_URL)
    print(f"[DB Health Check] DATABASE_URL: {masked_url}")
    print(f"[DB Health Check] POSTGRES_AVAILABLE: {POSTGRES_AVAILABLE}")
    print(f"[DB Health Check] USE_POSTGRES: {USE_POSTGRES}")
    
    result = {
        'timestamp': datetime.now().isoformat(),
        'database_type': 'PostgreSQL' if USE_POSTGRES else 'SQLite',
        'postgres_available': POSTGRES_AVAILABLE,
        'database_url_set': DATABASE_URL is not None,
        'database_url_masked': masked_url,
        'connection': 'unknown',
        'tables': {},
        'errors': []
    }
    
    try:
        conn = get_db_connection()
        result['connection'] = 'success'
        
        if USE_POSTGRES:
            # PostgreSQL diagnostics
            cursor = conn.cursor()
            
            # Check tables exist
            cursor.execute("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_schema = 'public'
            """)
            tables = [row[0] for row in cursor.fetchall()]
            
            # Check each expected table
            expected_tables = ['ticket_summaries', 'ticket_priorities', 'bulk_jobs']
            for table in expected_tables:
                if table in tables:
                    cursor.execute(f'SELECT COUNT(*) FROM {table}')
                    count = cursor.fetchone()[0]
                    result['tables'][table] = {'exists': True, 'row_count': count}
                else:
                    result['tables'][table] = {'exists': False, 'row_count': 0}
                    result['errors'].append(f"Table '{table}' does not exist")
            
            # Get PostgreSQL version
            cursor.execute('SELECT version()')
            result['db_version'] = cursor.fetchone()[0]
            
            cursor.close()
        else:
            # SQLite diagnostics
            cursor = conn.cursor()
            
            # Check tables exist
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            
            # Check each expected table
            expected_tables = ['ticket_summaries', 'ticket_priorities', 'bulk_jobs']
            for table in expected_tables:
                if table in tables:
                    cursor.execute(f'SELECT COUNT(*) FROM {table}')
                    count = cursor.fetchone()[0]
                    result['tables'][table] = {'exists': True, 'row_count': count}
                else:
                    result['tables'][table] = {'exists': False, 'row_count': 0}
                    result['errors'].append(f"Table '{table}' does not exist")
            
            # Get SQLite version
            cursor.execute('SELECT sqlite_version()')
            result['db_version'] = f"SQLite {cursor.fetchone()[0]}"
        
        conn.close()
        
        # Determine overall status
        if result['errors']:
            result['status'] = 'degraded'
        else:
            result['status'] = 'healthy'
            
    except Exception as e:
        result['connection'] = 'failed'
        result['status'] = 'unhealthy'
        result['errors'].append(str(e))
    
    status_code = 200 if result['status'] == 'healthy' else (503 if result['status'] == 'unhealthy' else 200)
    return jsonify(result), status_code


@app.route('/health/db/init', methods=['POST'])
def reinitialize_database():
    """Manually trigger database initialization (creates missing tables)."""
    try:
        init_db()
        return jsonify({
            'status': 'success',
            'message': 'Database initialized successfully',
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

# ============================================================
# END HEALTH CHECK ENDPOINTS
# ============================================================

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=os.environ.get('RAILWAY_ENVIRONMENT') != 'production', host='0.0.0.0', port=port)
