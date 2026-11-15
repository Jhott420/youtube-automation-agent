# Coloring Book Generator (Amazon KDP)

Give it a theme and it makes a print-ready coloring book for Amazon KDP:

1. Writes different page ideas for the theme
2. Generates line art for each page and cleans it to pure black and white
3. Builds `interior.pdf`: 8.5 x 11 in, 300 DPI, a title page, a "This book belongs to" page, then one picture per sheet with a blank back so markers don't bleed through
4. Builds `cover.pdf`: back + spine + front with 0.125 in bleed, spine width worked out from the page count, and the barcode area on the back left empty
5. Writes `listing.txt`: title, subtitle, description, 7 keywords and the KDP print settings to choose

## AI keys and cost

- Real artwork needs **an OpenAI key** or **a Gemini key with billing turned on**. Free-tier Gemini keys can't make images.
- Each picture is one image request, plus one for the cover. Check your provider's current image price before a big run.
- If both keys are set, OpenAI is used.
- Re-running the same theme skips pages that already exist, so a failed run doesn't charge you twice.

## Run it from your phone (GitHub Actions)

1. Repo **Settings > Secrets and variables > Actions**: add `OPENAI_API_KEY` or `GEMINI_API_KEY` (the storytelling channel may already use one)
2. **Actions** tab > **Make Coloring Book** > **Run workflow**
3. Enter a theme, the number of pictures and your author name. Tick **Demo** first to try it for free with placeholder shapes
4. When it finishes, open the run and download **coloring-book** under **Artifacts**

## Run it on a computer

```bash
cd coloring-book
pip install -r requirements.txt
export OPENAI_API_KEY=...          # or GEMINI_API_KEY with billing on
python make_book.py "cute axolotls" --pages 30 --ages "4-8" --author "Your Name"
python make_book.py "cute axolotls" --demo   # free layout test, no AI
```

The files are saved in `output/<theme>/`.

## Before you publish

- **Look at every page** in `pages/`. AI line art often has broken lines, extra limbs or areas filled black. Delete a bad page and run the same command again, and only that page is redone.
- Pages over 35% black are listed as warnings in `listing.txt`.
- **KDP asks whether content is AI-generated. Answer yes.** Hiding it can get your account closed.
- On KDP choose: paperback, 8.5 x 11 in, black & white interior, white paper, **no bleed**, then upload `interior.pdf` and `cover.pdf` and check KDP's previewer.
- KDP needs at least 24 pages and allows spine text only from 80 pages. The generator handles both.
- Order one author copy before promoting the book.
