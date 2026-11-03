"""Daily entry point: write, render and schedule 4 YouTube Shorts.

Env:
  OPENAI_API_KEY, YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN
  SHORTS_PRIVACY   scheduled (default) | public | unlisted | private
  SHORTS_DRY_RUN   1 = write and render only, never upload
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
os.chdir(HERE)
load_dotenv()

CONFIG = HERE / "shorts_config.json"
STATE = HERE / "shorts_state.json"
OUTPUT = HERE / "output"


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def check_env(dry_run):
    required = ["OPENAI_API_KEY"] + ([] if dry_run else
                                     ["YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"])
    missing = [n for n in required if not os.getenv(n)]
    if missing:
        print("Missing required secrets: " + ", ".join(missing))
        print("Add them under GitHub repo Settings > Secrets and variables > Actions.")
        sys.exit(1)


def publish_slots(times_utc, count, now=None):
    """Next `count` publish times from the configured daily slots, at least 20 min from now."""
    now = now or datetime.now(timezone.utc)
    earliest = now + timedelta(minutes=20)
    slots = []
    day = now.date()
    while len(slots) < count:
        for hhmm in sorted(times_utc):
            h, m = map(int, hhmm.split(":"))
            slot = datetime(day.year, day.month, day.day, h, m, tzinfo=timezone.utc)
            if slot >= earliest and len(slots) < count:
                slots.append(slot)
        day += timedelta(days=1)
    return [s.strftime("%Y-%m-%dT%H:%M:%SZ") for s in slots]


def clean_title(title):
    title = title.strip().strip('"').strip("'").replace("<", "").replace(">", "")
    title = title[:90].rstrip()
    return f"{title} #shorts"


def build_description(short, fmt):
    lines = [short["description"].strip(), ""]
    if short.get("pinned_comment"):
        lines += [short["pinned_comment"].strip(), ""]
    if fmt["is_fiction"]:
        lines.append("This is an original work of fiction.")
    lines.append("Written with AI assistance and narrated with a synthetic voice.")
    tags = " ".join("#" + t.replace(" ", "") for t in fmt["tags"][:3])
    lines += ["", f"#shorts {tags}"]
    return "\n".join(lines)[:5000]


def main():
    dry_run = os.getenv("SHORTS_DRY_RUN") == "1"
    privacy = os.getenv("SHORTS_PRIVACY", "scheduled")
    check_env(dry_run)

    from render_short import render_short
    from shorts_writer import write_short

    config = load(CONFIG)
    state = load(STATE)
    OUTPUT.mkdir(exist_ok=True)

    count = int(os.getenv("SHORTS_COUNT", config["shorts_per_day"]))
    formats = config["formats"]
    # Rotate the starting format each day so every format gets every time slot.
    offset = state["total_published"] % len(formats)
    plan = [formats[(offset + i) % len(formats)] for i in range(count)]
    slots = publish_slots(config["publish_times_utc"], count)

    youtube = None
    if not dry_run:
        from upload_short import get_youtube_service, upload_short
        youtube = get_youtube_service()

    previous_topics = [h["topic"] for h in state["history"]]
    made, failures = 0, []

    for i, fmt in enumerate(plan):
        label = f"[{i + 1}/{count}] {fmt['id']}"
        try:
            short = None
            for attempt in range(3):  # retry if the script runs too long for a Short
                short = write_short(fmt, previous_topics, config["target_words"])
                path = OUTPUT / f"short_{datetime.now(timezone.utc):%Y%m%d}_{i + 1}.mp4"
                try:
                    video_path, duration = render_short(short, path, config["voice"], config["voice_speed"])
                    break
                except ValueError as exc:
                    print(f"{label} attempt {attempt + 1}: {exc}")
            else:
                raise RuntimeError("could not produce a script under 60 seconds")

            title = clean_title(short["title"])
            print(f"{label} {title} ({duration:.1f}s)")
            record = {
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "format": fmt["id"],
                "topic": short["topic"],
                "title": title,
                "duration": round(duration, 1),
            }

            if dry_run:
                record["video_id"] = None
                print(f"{label} dry run, saved {video_path}")
            else:
                video_id = upload_short(
                    youtube, video_path, title, build_description(short, fmt),
                    fmt["tags"] + ["shorts"], publish_at=slots[i], privacy=privacy,
                )
                record["video_id"] = video_id
                record["publish_at"] = slots[i] if privacy == "scheduled" else None
                print(f"{label} uploaded https://youtube.com/shorts/{video_id} -> {record['publish_at'] or privacy}")

            state["history"].append(record)
            state["total_published"] += 1
            previous_topics.append(short["topic"])
            made += 1
            save(STATE, state)  # save after each short so a later failure keeps earlier progress
        except Exception as exc:  # noqa: BLE001 - one bad short should not stop the others
            failures.append(f"{label}: {exc}")
            print(f"{label} FAILED: {exc}")

    state["history"] = state["history"][-400:]
    save(STATE, state)
    print(f"\nMade {made}/{count} shorts.")
    if failures:
        print("Failures:\n  " + "\n  ".join(failures))
        sys.exit(1)


if __name__ == "__main__":
    main()
