# test_zendesk_api.py
import requests
import json
import os

# Load environment variables manually
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # If dotenv not available, assume env vars are set

class ZendeskAuth:
    def __init__(self):
        self._auth = os.environ.get("ZENDESK_AUTH")

    def get_auth_header(self):
        if not self._auth:
            raise ValueError("ZENDESK_AUTH is not set in environment or .env. See README for setup details.")
        return { "Authorization": f"Basic {self._auth}" }

zendesk_auth = ZendeskAuth()

TICKET_ID = 64258
ZENDESK_COMMENTS_URL = f"https://hevodata.zendesk.com/api/v2/tickets/{TICKET_ID}/comments"

print(f"Testing Zendesk API for ticket {TICKET_ID}...")
print(f"URL: {ZENDESK_COMMENTS_URL}")
print()

headers = zendesk_auth.get_auth_header()
print(f"Auth header present: {'Yes' if headers else 'No'}")
print()

try:
    response = requests.get(ZENDESK_COMMENTS_URL, headers=headers, timeout=30)
    print(f"Status Code: {response.status_code}")
    print()
    
    if response.status_code == 200:
        data = response.json()
        comments = data.get('comments', [])
        
        print(f"Total comments returned: {len(comments)}")
        print()
        
        # Analyze each comment
        public_comments = []
        internal_comments = []
        customer_comments = []
        
        for i, comment in enumerate(comments):
            is_public = comment.get('public', True)
            author_id = comment.get('author_id')
            body_preview = comment.get('body', '')[:100].replace('\n', ' ')
            
            comment_info = {
                'index': i,
                'id': comment.get('id'),
                'author_id': author_id,
                'public': is_public,
                'body_preview': body_preview,
                'created_at': comment.get('created_at')
            }
            
            if is_public:
                public_comments.append(comment_info)
            else:
                internal_comments.append(comment_info)
        
        print("=" * 80)
        print(f"PUBLIC COMMENTS: {len(public_comments)}")
        print("=" * 80)
        for comm in public_comments:
            print(f"  [{comm['index']}] ID: {comm['id']}, Author: {comm['author_id']}, Public: {comm['public']}")
            print(f"      Preview: {comm['body_preview']}...")
            print()
        
        print("=" * 80)
        print(f"INTERNAL COMMENTS (public=false): {len(internal_comments)}")
        print("=" * 80)
        if internal_comments:
            for comm in internal_comments:
                print(f"  [{comm['index']}] ID: {comm['id']}, Author: {comm['author_id']}, Public: {comm['public']}")
                print(f"      Preview: {comm['body_preview']}...")
                print()
        else:
            print("  ⚠️  NO INTERNAL COMMENTS FOUND!")
            print("  This means the API is not returning internal notes (public=false)")
            print()
        
        print("=" * 80)
        print("FULL JSON RESPONSE (first 2000 chars):")
        print("=" * 80)
        print(json.dumps(data, indent=2)[:2000])
        print()
        
        # Check if we need to use a different endpoint
        print("=" * 80)
        print("RECOMMENDATION:")
        print("=" * 80)
        if len(internal_comments) == 0:
            print("❌ Internal comments are NOT being returned by the API.")
            print("   Possible reasons:")
            print("   1. API token doesn't have permission to view internal notes")
            print("   2. Need to use a different endpoint or parameter")
            print("   3. Internal notes don't exist for this ticket")
        else:
            print("✅ Internal comments ARE being returned by the API.")
            print("   The issue might be in how the conversation is being formatted.")
        
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        
except Exception as e:
    print(f"Error: {str(e)}")
    import traceback
    traceback.print_exc()
