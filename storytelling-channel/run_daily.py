import os
import sys

from dotenv import load_dotenv

load_dotenv()

REQUIRED_ENV = [
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
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")):
        print("Set GEMINI_API_KEY (free tier works) or OPENAI_API_KEY to write the story.")
        print("Add it under GitHub repo Settings > Secrets and variables > Actions.")
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

    from story_engine import next_episode, load_state, generate_scene_prompts
    from generate_video import create_episode_video, create_thumbnail, generate_scene_images
    from upload_video import upload_video, set_thumbnail

    title, script, series_name = next_episode()
    episode_number = load_state()["episode_number"]
    print(f"Generated episode {episode_number}: {title}")

    scene_count = int(os.getenv("SCENE_IMAGE_COUNT") or 6)
    scene_images = []
    if scene_count > 0:
        try:
            scene_images = generate_scene_images(generate_scene_prompts(title, script, scene_count))
        except Exception as error:
            print(f"Skipping AI scene images: {error}")

    video_path = create_episode_video(title, script, scene_images)
    thumbnail_path = create_thumbnail(title, scene_images[0] if scene_images else None)

    video_title = clean_title(title, series_name, episode_number)
    description = build_description(series_name, episode_number, script)
    video_id = upload_video(video_path, video_title, description)
    set_thumbnail(video_id, thumbnail_path)

    print(f"Done. https://youtu.be/{video_id}")

if __name__ == "__main__":
    main()
