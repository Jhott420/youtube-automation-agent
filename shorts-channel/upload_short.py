import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


def get_youtube_service():
    # Uses the same refresh token as the storytelling channel (youtube.upload scope).
    credentials = Credentials(
        token=None,
        refresh_token=os.getenv("YOUTUBE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("YOUTUBE_CLIENT_ID"),
        client_secret=os.getenv("YOUTUBE_CLIENT_SECRET"),
    )
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def upload_short(youtube, video_path, title, description, tags, publish_at=None, privacy="scheduled"):
    """Upload a Short. With privacy 'scheduled' it goes up private and YouTube
    makes it public at publish_at (RFC 3339, UTC)."""
    status = {"selfDeclaredMadeForKids": False}
    if privacy == "scheduled" and publish_at:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at
    else:
        status["privacyStatus"] = privacy if privacy in ("public", "unlisted", "private") else "private"

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags[:15],
            "categoryId": "24",  # Entertainment
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": status,
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    response = youtube.videos().insert(part="snippet,status", body=body, media_body=media).execute()
    return response["id"]
