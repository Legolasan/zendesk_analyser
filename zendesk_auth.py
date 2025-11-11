import os
from dotenv import load_dotenv

load_dotenv()

class ZendeskAuth:
    def __init__(self):
        self._auth = os.environ.get("ZENDESK_AUTH")

    def get_auth_header(self):
        if not self._auth:
            raise ValueError("ZENDESK_AUTH is not set in environment or .env. See README for setup details.")
        return { "Authorization": f"Basic {self._auth}" }

zendesk_auth = ZendeskAuth()
