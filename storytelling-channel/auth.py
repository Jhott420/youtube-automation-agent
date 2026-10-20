"""
One-time helper that prints a YouTube refresh token.

Run this on any computer with Python (not needed on iPhone - see README for
the OAuth Playground method that works from Safari):

    pip install google-auth-oauthlib
    python auth.py

It reads client_secret.json (downloaded from Google Cloud, never committed),
opens a browser for you to sign in to the YouTube channel account, then prints
the values to paste into GitHub repo secrets.
"""
import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube"
]

CLIENT_SECRET_FILE = Path("client_secret.json")

def main():
    if not CLIENT_SECRET_FILE.exists():
        raise SystemExit("client_secret.json not found. Download it from Google Cloud Console > Credentials.")

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    client_info = json.loads(CLIENT_SECRET_FILE.read_text())
    client = client_info.get("installed") or client_info.get("web") or {}

    print("\nAdd these as GitHub repo secrets:\n")
    print(f"YOUTUBE_CLIENT_ID={client.get('client_id', credentials.client_id)}")
    print(f"YOUTUBE_CLIENT_SECRET={client.get('client_secret', credentials.client_secret)}")
    print(f"YOUTUBE_REFRESH_TOKEN={credentials.refresh_token}")

if __name__ == "__main__":
    main()
