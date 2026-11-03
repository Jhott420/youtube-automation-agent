# Daily Shorts (4 per day)

Every day, GitHub Actions writes, renders and uploads **4 YouTube Shorts**. YouTube then publishes them at 4 separate times so they don't compete with each other.

| Slot (UTC) | US Eastern (EDT) |
| --- | --- |
| 16:00 | 12 pm |
| 19:00 | 3 pm |
| 22:00 | 6 pm |
| 01:00 | 9 pm |

You can change these in `shorts_config.json` → `publish_times_utc`.

## Style: brainrot duo (default)

Every Short is a fast back-and-forth between two **original** characters, each with their own voice and drawn avatar:

| Character | Personality | Voice |
| --- | --- | --- |
| **TOAST** | A smug, deadpan toaster who thinks he's a genius. He knows the real facts. | OpenAI `onyx`, pitched down, orange captions |
| **GUS** | A loud, overconfident pigeon who gets everything wrong and panics. | OpenAI `echo`, pitched up, blue captions |

To change the characters, edit `characters` in `shorts_config.json` (personality, `voice`, `pitch`, `speed`, `color`). Set `"style": "narrator"` to go back to a single narrator.

Don't swap them for characters or voices from existing shows. Copying them gets Shorts claimed, demonetized or struck, and that kills a channel faster than any algorithm.

## What each Short is built to do

These are the patterns that drive Shorts retention and replays. The pipeline uses all of them on every Short:

- **Hook in the first second.** The script's first sentence is under 12 words and opens a question. It never starts with a greeting or "Did you know".
- **30–45 seconds, one idea, no intro.** The biggest payoff is saved for the last line.
- **Loop ending.** The last line flows back into the first, so replays count.
- **Big word-by-word captions** with the spoken word highlighted, because most people watch without sound. There's also a top banner and a progress bar.
- **Fast, natural AI voice.** OpenAI TTS is sped up about 12% and loudness-normalised. If OpenAI TTS fails, it falls back to gTTS.
- **Comment bait.** Each Short ends on a question, and the description asks it too.
- **4 rotating formats**, so you learn which one your audience wants:
  1. `sounds-fake-fact`: a true fact that sounds made up
  2. `twist-story`: a 40-second original story with a twist ending
  3. `impossible-choice`: a would-you-rather style dilemma that gets people arguing in the comments
  4. `absurd-hypothetical`: "what if..." questions answered with real science
- **No repeats.** Topics are saved in `shorts_state.json`, and the writer is told to avoid them.

## Setup

It uses the **same 4 repo secrets** as the storytelling channel. If those already work, there's nothing new to add:

- `OPENAI_API_KEY`
- `YOUTUBE_CLIENT_ID`
- `YOUTUBE_CLIENT_SECRET`
- `YOUTUBE_REFRESH_TOKEN`

Then:

1. **Test first.** Go to **Actions → Daily Shorts → Run workflow**, tick **dry_run** and run it. Download the `daily-shorts` artifact and watch the 4 videos.
2. Run it again without dry_run to upload for real.
3. After that it runs automatically every day at 08:17 UTC.

### Privacy setting

Set an Actions **variable** (not a secret) named `SHORTS_PRIVACY` to one of these:

- `scheduled` (default): uploads as private and YouTube makes it public at its time slot
- `public`: publishes immediately
- `private`: stays private, so you can review and publish each one by hand

**Important:** if your Google Cloud project hasn't passed YouTube's API audit, YouTube forces **every** API upload to stay private, whatever this setting says. If your Shorts stay private, request the audit: <https://support.google.com/youtube/contact/yt_api_form>. Until it's approved, publish them by hand from YouTube Studio.

## Making them look better (optional but recommended)

- **Background video (gameplay):** see *Gameplay backgrounds* below.
- **Music:** drop royalty-free tracks into `assets/music/`. They're mixed in quietly under the voice.

## Gameplay backgrounds

Don't use other people's gameplay videos (YouTube rips, TikTok compilations, Twitch clips). They get Content ID claims and can earn copyright strikes. There are two safe sources, and you can use both:

**1. Your own gameplay (best, looks like real brainrot).** Most big publishers, including Mojang (Minecraft), Epic (Fortnite), Roblox and Riot, allow monetized videos of gameplay *you* record. Check each game's video policy.
- Record 5–15 minutes of something visually busy: parkour, racing, building, or a satisfying grind. Skip cutscenes and in-game music, because licensed songs inside games still trigger claims.
- Put the files in `assets/stock/`. A random 30–45 second stretch is picked each time, so one 10-minute recording gives dozens of different backgrounds.
- GitHub rejects files over 100 MB. Compress them first (`ffmpeg -i in.mp4 -vf scale=-2:1080 -crf 28 -an out.mp4`) or use Git LFS.

**2. Automatic free gaming footage from Pexels.** Get a free API key at <https://www.pexels.com/api/> and add it as the repo secret `PEXELS_API_KEY`. Each run then downloads a few gaming, neon and satisfying clips, which are free to use commercially under the Pexels license, and keeps them between runs. This isn't footage of specific hit games, but it adds motion with zero copyright risk.

If neither is set up, an animated gradient is used.

## Files

| File | Purpose |
| --- | --- |
| `run_shorts.py` | Entry point: plan → write → render → upload, for all 4 Shorts |
| `shorts_writer.py` | Script prompt (hook, loop, retention rules) |
| `render_short.py` | Voice, word timings (Whisper), captions, background, MP4 |
| `fetch_backgrounds.py` | Downloads free-to-use background clips from Pexels |
| `upload_short.py` | YouTube upload with scheduled publish |
| `shorts_config.json` | Formats, publish times, voice, length |
| `shorts_state.json` | History of every Short (format, topic, video ID). Updated automatically |

## Cost

Each Short is roughly 1 text call, about 600 characters of TTS and 30 seconds of Whisper. That comes to a few cents per Short, or about **$0.10–0.20 a day** for 4.

## Local preview

```bash
pip install -r requirements.txt   # also needs ffmpeg
OPENAI_API_KEY=... SHORTS_DRY_RUN=1 SHORTS_COUNT=1 python run_shorts.py
# -> output/short_YYYYMMDD_1.mp4
```
