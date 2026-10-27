# Storytelling YouTube Automation

A daily AI storytelling YouTube automation for the serialized mystery **The Last Signal**.

Every day GitHub Actions:

1. Reads `series_bible.json` (the world and rules) and `series_state.json` (where the story left off)
2. Writes the next episode's title and script (`story_engine.py`)
3. Generates scene images, narrates the story and renders a 1280x720 video with a thumbnail (`generate_video.py`)
4. Uploads it to YouTube as **private** and sets the thumbnail (`upload_video.py`)
5. Commits the updated `series_state.json` back to the repo so tomorrow's episode continues the story

Nothing runs on your phone. You manage everything from the GitHub app or Safari.

## AI providers

A free-tier **`GEMINI_API_KEY`** is enough to run the whole channel. Get one at https://aistudio.google.com/apikey.

| Job | Provider order (first one configured wins) | Gemini model |
| --- | --- | --- |
| Story + title | OpenAI → Gemini | `gemini-3.7-flash` |
| Narration | ElevenLabs → OpenAI → Gemini → gTTS (free, no key) | `gemini-3.1-flash-tts-preview`, voice `Kore` (24 kHz, encoded to MP3 with the FFmpeg bundled with moviepy) |
| Scene images + thumbnail | OpenAI → Gemini → styled title cards | `gemini-3.1-flash-image` |

- **Images are not free.** Free-tier Gemini keys get 0 image requests, so episodes use title-card visuals and a plain thumbnail until you enable billing (or add an OpenAI key). To skip the attempt entirely, set the `SCENE_IMAGE_COUNT` variable to `0`.
- Models and voice can be changed without editing code: add repo **Variables** (Settings > Secrets and variables > Actions > **Variables** tab) named `GEMINI_TEXT_MODEL`, `GEMINI_IMAGE_MODEL`, `GEMINI_TTS_MODEL`, `GEMINI_TTS_VOICE`, `ELEVENLABS_TTS_MODEL` or `SCENE_IMAGE_COUNT` (default 6; `0` turns images off). See `.env.example`.
- **Voice limits:** free-tier Gemini allows 10 narration requests per day *per model*. An episode is cleaned of any stage directions, split into ~3,000-character parts (usually 4–5) and joined. If `gemini-3.1-flash-tts-preview` runs out, the whole episode is narrated with `gemini-2.5-flash-preview-tts` instead (its own 10/day), then gTTS as a last resort, so each episode keeps one voice.
- **Reliability:** busy Gemini servers (503) are retried with backoff, and text falls back from `gemini-3.7-flash` to `gemini-3.5-flash-lite` and `gemini-3.1-pro-preview`. If Gemini's safety filter rejects one passage, it is retried, split, and only that passage falls back to gTTS.

## Files

| File | Purpose |
| --- | --- |
| `run_daily.py` | Entry point the workflow runs |
| `story_engine.py` | Title + script generation, series state |
| `generate_video.py` | Scene images, voiceover, thumbnail, video rendering |
| `upload_video.py` | YouTube upload + thumbnail |
| `providers.py` | Gemini / OpenAI / ElevenLabs / gTTS calls and provider order |
| `auth.py` | One-time sign-in on a computer: opens Google in your browser and saves the refresh token to `.env` |
| `series_bible.json` | Series world, tone and rules (edit to steer the story) |
| `series_state.json` | Current episode number and story so far (updated automatically) |
| `assets/stock/` | Optional licensed video clips used as visuals |
| `../.github/workflows/daily_story_upload.yml` | The daily schedule (must live at the repo root) |

## Setup

1. Create a GitHub repo
2. Add all files
3. Create Google Cloud project
4. Enable YouTube Data API v3
5. Create OAuth 2.0 Client ID
6. Download the JSON and save it as `client_secret.json`
7. Keep it private. **Do not commit `client_secret.json`** (it is in `.gitignore`). You only need it to read the client ID and secret, or to run `auth.py`.
8. Add these repo secrets:
   - GEMINI_API_KEY (or OPENAI_API_KEY; either one is enough)
   - YOUTUBE_CLIENT_ID
   - YOUTUBE_CLIENT_SECRET
   - YOUTUBE_REFRESH_TOKEN
   - Optional: ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID for premium narration
9. Run the workflow manually once
10. If it works, switch to public

### Getting the YouTube refresh token from an iPhone

You can do this in Safari with Google's OAuth Playground, so no Python is needed:

1. In Google Cloud Console > **APIs & Services > OAuth consent screen**, set it up (External), and add your channel's Google account under **Test users**.
2. In **Credentials > Create credentials > OAuth client ID**, choose **Web application**.
   Under **Authorized redirect URIs** add `https://developers.google.com/oauthplayground`.
   Copy the **Client ID** and **Client secret**.
3. Open https://developers.google.com/oauthplayground in Safari.
4. Tap the gear icon (top right), tick **Use your own OAuth credentials**, and paste the client ID and secret.
5. In **Step 1**, paste these scopes into the "Input your own scopes" box and tap **Authorize APIs**:
   `https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube`
6. Sign in with the account that owns the YouTube channel and allow access.
7. In **Step 2**, tap **Exchange authorization code for tokens** and copy the **Refresh token**.

Note: while the consent screen is in "Testing" mode, Google expires refresh tokens after 7 days. To keep the daily upload working, publish the app (OAuth consent screen > **Publish app**). An unverified app is fine for your own channel; you will just see a warning when you sign in.

### Alternative: on a computer with `auth.py`

1. In Google Cloud **Credentials**, create an OAuth client ID of type **Desktop app** and download it into this folder as `client_secret.json`.
2. Run:
   ```bash
   pip install -r requirements.txt
   python auth.py
   ```
3. Your browser opens. Sign in with the account that owns the channel and allow access.
4. `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET` and `YOUTUBE_REFRESH_TOKEN` are written to `.env` (other values in it are kept). `.env` is git-ignored, so also copy these three into the GitHub repo secrets for the daily workflow.

### Adding repo secrets from the iPhone

The GitHub mobile app cannot edit secrets, so use Safari:
`github.com/<you>/<repo>` > **Settings** > **Secrets and variables** > **Actions** > **New repository secret**.
(Request the desktop site from the "aA" menu if Settings is hidden.)

## Managing it from your iPhone

- **Run now:** GitHub app > repo > **Actions** > *Daily Storytelling Upload* > **Run workflow**.
- **Check a run:** open the run in the Actions tab to see logs. The rendered video is attached to each run as the `story-episode` artifact for 7 days.
- **Steer the story:** edit `series_bible.json` (tone, rules, elements) or `series_state.json` (add facts to `completed_story_points`, change `last_theme`) in the GitHub app and commit.
- **Change the time:** edit the `cron` line in `.github/workflows/daily_story_upload.yml`. It is UTC; `0 9 * * *` is 09:00 UTC daily.
- **Pause:** Actions > *Daily Storytelling Upload* > "..." > **Disable workflow**.
- **Go public:** change `"privacyStatus": "private"` to `"public"` in `upload_video.py` once you are happy with a few episodes.
- **Visuals:** AI scene images are used when an image provider works. Otherwise, `.mp4`/`.mov` clips you have the rights to in `assets/stock/` are used (up to four, cycled under the narration). Without either, styled title cards are used.
- **Thumbnails:** YouTube only accepts custom thumbnails from phone-verified channels (youtube.com/verify). Until then the run logs a warning and YouTube picks a frame.

## Running locally (optional)

```bash
cd storytelling-channel
cp .env.example .env   # fill in values
pip install -r requirements.txt
python run_daily.py
```

Requires ImageMagick (with the `@*` path policy removed from `policy.xml`), ffmpeg and DejaVu fonts. The workflow installs these automatically on GitHub.

## Notes
- Start with private uploads
- Only use royalty-safe or licensed visual assets
- Keep the story world coherent
- Let the series build over time
- YouTube API projects get 10,000 quota units/day by default; one upload costs about 1,600, so one video a day is well within limits
- New, unverified API projects may have uploads locked to private until Google audits the app. If videos stay private after you switch to public, request an audit in the YouTube API Services form
- Free-tier Gemini has daily request limits; one episode a day stays within them for text and speech. Avoid manual test runs on the same day as the scheduled one, since they share the 10/day voice allowance. Check current limits at https://ai.google.dev/gemini-api/docs/rate-limits
- A typical episode is 12–17 minutes and takes roughly 20–40 minutes to render on GitHub's runner
- YouTube requires disclosing realistic altered or synthetic content; for this fictional, clearly-narrated series the description already states it is AI-assisted fiction
