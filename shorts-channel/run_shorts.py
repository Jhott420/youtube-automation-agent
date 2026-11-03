"""Daily entry point: write and render 4 YouTube Shorts.

By default nothing is uploaded: each Short is saved to output/ with a matching
.txt file holding its title, description and tags, ready to upload by hand.

Env:
  OPENAI_API_KEY   required
  SHORTS_UPLOAD    1 = also upload to YouTube (needs the YOUTUBE_* secrets). Default: off
  SHORTS_PRIVACY   scheduled (default) | public | unlisted | private  (only with SHORTS_UPLOAD=1)
  SHORTS_DRY_RUN   1 = don't record topics in shorts_state.json (for testing)
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


def check_env(upload):
    required = ["OPENAI_API_KEY"] + ([] if not upload else
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
    upload = os.getenv("SHORTS_UPLOAD") == "1"
    privacy = os.getenv("SHORTS_PRIVACY", "scheduled")
    check_env(upload)

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
    if upload:
        from upload_short import get_youtube_service, upload_short
        youtube = get_youtube_service()

    characters = config.get("characters") if config.get("style") == "duo" else None
    previous_topics = [h["topic"] for h in state["history"]]
    made, failures = 0, []

    for i, fmt in enumerate(plan):
        label = f"[{i + 1}/{count}] {fmt['id']}"
        try:
            short = None
            for attempt in range(3):  # retry if the script runs too long for a Short
                short = write_short(fmt, previous_topics, config["target_words"], characters)
                path = OUTPUT / f"short_{datetime.now(timezone.utc):%Y%m%d_%H%M}_{i + 1}_{fmt['id']}.mp4"
                try:
                    video_path, duration = render_short(short, path, config["voice"], config["voice_speed"], characters)
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

            description = build_description(short, fmt)
            tags = fmt["tags"] + ["shorts"]
            Path(video_path).with_suffix(".txt").write_text(
                f"TITLE:\n{title}\n\nDESCRIPTION:\n{description}\n\nTAGS:\n{', '.join(tags)}\n\n"
                f"SCRIPT:\n" + "\n".join(f"{l['speaker']}: {l['text']}" for l in short.get("lines", []))
                + ("" if short.get("lines") else short["script"]) + "\n",
                encoding="utf-8",
            )

            if not upload:
                record["video_id"] = None
                print(f"{label} saved {video_path} (not uploaded)")
            else:
                video_id = upload_short(
                    youtube, video_path, title, description,
                    tags, publish_at=slots[i], privacy=privacy,
                )
                record["video_id"] = video_id
                record["publish_at"] = slots[i] if privacy == "scheduled" else None
                print(f"{label} uploaded https://youtube.com/shorts/{video_id} -> {record['publish_at'] or privacy}")

            previous_topics.append(short["topic"])
            made += 1
            if not dry_run:
                state["history"].append(record)
                state["total_published"] += 1
                save(STATE, state)  # save after each short so a later failure keeps earlier progress
        except Exception as exc:  # noqa: BLE001 - one bad short should not stop the others
            failures.append(f"{label}: {exc}")
            print(f"{label} FAILED: {exc}")

    if not dry_run:
        state["history"] = state["history"][-400:]
        save(STATE, state)
    print(f"\nMade {made}/{count} shorts.")
    if failures:
        print("Failures:\n  " + "\n  ".join(failures))
        sys.exit(1)


if __name__ == "__main__":
    main()
