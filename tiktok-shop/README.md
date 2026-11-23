# TikTok Shop Product Videos

Turns the scripts in `videos.json` into ready-to-post vertical TikTok videos (1080x1920, 30 fps, 10–30 seconds). Each video has:

- a big hook caption in the first second, readable with the sound off
- your product photos or clips with a slow pan, one per beat
- a voiceover (free by default), or no voice so you can add a trending sound in TikTok
- a closing "tap the orange cart" card with the product name and price
- a `.txt` file next to each video with the post caption and hashtags

It costs nothing to run. The voice uses the same providers as the storytelling channel: ElevenLabs, then OpenAI, then Gemini, then **gTTS, which is free and needs no key**.

## Step 1: Add product photos

Upload photos or short clips to the product's folder. In the GitHub app or on github.com, open the folder, then choose **Add file → Upload files**.

| Product | Folder |
|---|---|
| Electric Spin Scrubber | `tiktok-shop/media/spin-scrubber/` |
| Cat Window Perch | `tiktok-shop/media/cat-window-perch/` |
| Heatless Curling Set | `tiktok-shop/media/heatless-curler/` |

- They're used in filename order, so name them `01.jpg`, `02.jpg`, `03.mp4`, …
- 4–6 files per product is enough. They're reused if a video has more beats.
- Accepted types: `.jpg`, `.png`, `.webp`, `.mp4`, `.mov`. Clips are muted, cropped to vertical and looped if they're too short.
- **Use images you're allowed to use.** That means your own photos and clips (best, because real footage sells better) or the product images your supplier provides to its sellers. Don't use photos taken from other shops or creators.
- With no files, the video still renders, using colored text cards. That works for testing but sells much worse.

## Step 2: Make the videos from your phone

1. In the GitHub app or github.com, open the repo and go to **Actions → TikTok Product Videos → Run workflow**.
2. Pick a product (or **all**), optionally one video id, and whether you want a voiceover.
3. When the run finishes (a few minutes per video), open it and download the **tiktok-videos** zip.
4. Upload a video in the TikTok app, paste in the caption from its `.txt` file, and **link the product** from your TikTok Shop.

## Run on a computer instead

```bash
cd tiktok-shop
pip install -r requirements.txt
python make_video.py --list                                    # products and video ids
python make_video.py --product spin-scrubber                   # all 5 scrubber videos
python make_video.py --product spin-scrubber --video grout-check
python make_video.py --all --silent                            # everything, no voiceover
```

Videos are saved to `tiktok-shop/output/<product>/<video>.mp4`.

## Edit or add videos

Everything lives in `videos.json`. Each video has:

```json
{
  "id": "grout-check",
  "caption": "Text for the TikTok post",
  "beats": [
    {"text": "On-screen caption", "say": "What the voiceover says"}
  ],
  "cta": "Tap the orange cart"
}
```

- `say` is optional; if you leave it out, the voice reads `text`.
- To add a product, add an entry under `products` with `id`, `name`, `price`, `hashtags` and `videos`, then create `media/<id>/`. To run it from the Actions button, also add the id to the `product` options in `.github/workflows/tiktok_product_videos.yml`.

## Notes

- TikTok's buttons cover the right edge and its caption covers the bottom of the screen, so the captions sit in the upper-middle and leave the right side clear.
- Videos aren't posted automatically. TikTok's posting API requires an approved developer app, so you upload by hand. That also lets you add a trending sound and link the product.
- Keep the on-screen claims true for the product you actually sell. See `starter-products.md` for which claims to avoid.
