"""
One-time helper that gets a YouTube refresh token and saves it to .env.

Run this on any computer with Python (not needed on iPhone - see README for
the OAuth Playground method that works from Safari):

    pip install -r requirements.txt
    python auth.py

It reads client_secret.json (a "Desktop app" OAuth client downloaded from
Google Cloud, never committed), opens your browser so you can sign in with the
Google account that owns the YouTube channel, then writes YOUTUBE_CLIENT_ID,
YOUTUBE_CLIENT_SECRET and YOUTUBE_REFRESH_TOKEN into .env. Other values already
in .env are kept.
"""
import json
import sys
from pathlib import Path

from dotenv import set_key
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube"
]

CLIENT_SECRET_FILE = Path("client_secret.json")
ENV_FILE = Path(".env")

def load_client_config():
    if not CLIENT_SECRET_FILE.exists():
        sys.exit(
            "client_secret.json not found.\n"
            "Google Cloud Console > APIs & Services > Credentials > Create credentials > "
            "OAuth client ID > Desktop app, then download the JSON into this folder as client_secret.json."
        )
    config = json.loads(CLIENT_SECRET_FILE.read_text())
    client = config.get("installed") or config.get("web")
    if not client:
        sys.exit("client_secret.json doesn't look like an OAuth client file.")
    return client

def main():
    client = load_client_config()

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
    # access_type=offline + prompt=consent makes Google return a refresh token every time,
    # even if this account has authorized the app before.
    credentials = flow.run_local_server(
        port=0,
        open_browser=True,
        access_type="offline",
        prompt="consent",
        authorization_prompt_message="Opening your browser to sign in with Google...\nIf it doesn't open, visit: {url}",
        success_message="Signed in. You can close this tab and return to the terminal.",
    )

    if not credentials.refresh_token:
        sys.exit("Google didn't return a refresh token. Remove the app at myaccount.google.com/permissions and run again.")

    ENV_FILE.touch(exist_ok=True)
    values = {
        "YOUTUBE_CLIENT_ID": client["client_id"],
        "YOUTUBE_CLIENT_SECRET": client["client_secret"],
        "YOUTUBE_REFRESH_TOKEN": credentials.refresh_token,
    }
    for key, value in values.items():
        set_key(str(ENV_FILE), key, value, quote_mode="never")

    print(f"\nSaved {', '.join(values)} to {ENV_FILE.resolve()}")
    print("To use them in GitHub Actions, add the same three values as repo secrets:")
    print("Settings > Secrets and variables > Actions > New repository secret.")

if __name__ == "__main__":
    main()
