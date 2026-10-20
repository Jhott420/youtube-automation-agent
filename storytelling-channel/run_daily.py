import os
import sys

from dotenv import load_dotenv

load_dotenv()

REQUIRED_ENV = [
    "OPENAI_API_KEY",
    "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "YOUTUBE_REFRESH_TOKEN",
]

def check_env():
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        print("Missing required secrets: " + ", ".join(missing))
        print("Add them under GitHub repo Settings > Secrets and variables > Actions.")
        sys.exit(1)

def clean_title(title, series_name, episode_number):
    # YouTube titles are limited to 100 characters and may not contain < or >.
    title = title.strip().strip('"').strip("'").replace("<", "").replace(">", "")
    full = f"{title} | {series_name} Ep. {episode_number}"
    return full if len(full) <= 100 else title[:100]

def build_description(series_name, episode_number, script):
    teaser = script[:600].rsplit(" ", 1)[0] + "..."
    return (
        f"{series_name} - Episode {episode_number}\n\n"
        f"{teaser}\n\n"
        "A new chapter of the story every day. Subscribe so you don't miss the next signal.\n\n"
        "This story is fiction, written with AI assistance and narrated with text-to-speech.\n\n"
        "#storytelling #mystery #thelastsignal"
    )[:5000]

def main():
    check_env()

    # Imported after the env check so the OpenAI client is created with the key present.
    from story_engine import next_episode, load_state
    from generate_video import create_episode_video
    from upload_video import upload_video

    title, script, series_name = next_episode()
    episode_number = load_state()["episode_number"]
    print(f"Generated episode {episode_number}: {title}")

    video_path = create_episode_video(title, script)

    video_title = clean_title(title, series_name, episode_number)
    description = build_description(series_name, episode_number, script)
    video_id = upload_video(video_path, video_title, description)

    print(f"Done. https://youtu.be/{video_id}")

if __name__ == "__main__":
    main()
