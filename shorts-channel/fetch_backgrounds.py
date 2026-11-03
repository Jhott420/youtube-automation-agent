"""Download free-to-use gaming / satisfying background clips from Pexels.

Pexels videos are free to use and modify, including commercially, without
attribution (https://www.pexels.com/license/). They are NOT gameplay from
commercial games; for that, record your own gameplay and put it in
assets/stock/ (see README).

Needs a free API key from https://www.pexels.com/api/ in PEXELS_API_KEY.
Clips are saved to assets/stock/pexels/ and reused on later runs.
"""

import os
import random
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
DEST = HERE / "assets" / "stock" / "pexels"

DEFAULT_QUERIES = [
    "video game", "gaming", "racing game", "arcade game",
    "neon tunnel", "satisfying", "kinetic sand", "marble run",
]


def _best_file(video):
    """Prefer a portrait or HD file around 1080px wide that isn't huge."""
    files = [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
    if not files:
        return None
    files.sort(key=lambda f: abs(min(f["width"], f["height"]) - 1080))
    return files[0]


def fetch(count=6, queries=None, min_seconds=20):
    key = os.getenv("PEXELS_API_KEY")
    if not key:
        print("PEXELS_API_KEY not set; skipping Pexels background download.")
        return []
    DEST.mkdir(parents=True, exist_ok=True)
    have = {p.stem for p in DEST.glob("*.mp4")}
    queries = list(queries or DEFAULT_QUERIES)
    random.shuffle(queries)
    saved = []

    with httpx.Client(headers={"Authorization": key}, timeout=60, follow_redirects=True) as client:
        for query in queries:
            if len(saved) >= count:
                break
            try:
                resp = client.get("https://api.pexels.com/videos/search",
                                  params={"query": query, "per_page": 15, "size": "medium"})
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                print(f"Pexels search '{query}' failed: {exc}")
                continue
            videos = [v for v in resp.json().get("videos", [])
                      if v.get("duration", 0) >= min_seconds and str(v["id"]) not in have]
            random.shuffle(videos)
            for video in videos[:2]:
                f = _best_file(video)
                if not f:
                    continue
                out = DEST / f"{video['id']}.mp4"
                try:
                    with client.stream("GET", f["link"]) as dl:
                        dl.raise_for_status()
                        with open(out, "wb") as fh:
                            for chunk in dl.iter_bytes():
                                fh.write(chunk)
                except httpx.HTTPError as exc:
                    print(f"Download of Pexels video {video['id']} failed: {exc}")
                    out.unlink(missing_ok=True)
                    continue
                have.add(str(video["id"]))
                saved.append(out)
                print(f"Saved Pexels '{query}' clip {video['id']} ({video.get('duration')}s) by "
                      f"{video.get('user', {}).get('name', 'unknown')}")
                if len(saved) >= count:
                    break
    return saved


if __name__ == "__main__":
    fetch()
