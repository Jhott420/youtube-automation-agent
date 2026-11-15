"""Make a print-ready Amazon KDP coloring book from a theme.

    python make_book.py "cute axolotls" --pages 30 --ages "4-8"
    python make_book.py "cute axolotls" --demo        # no API key: layout test only

Output (in output/<theme>/):
    interior.pdf   8.5x11 in, 300 DPI, one picture per sheet with a blank back
    cover.pdf      full wrap cover (back + spine + front) with 0.125 in bleed
    listing.txt    title, subtitle, description and keywords for the KDP form
    pages/         the cleaned black-and-white pages, to check one by one
"""
import argparse
import json
import random
import re
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

import ai

DPI = 300
TRIM_W, TRIM_H = 8.5, 11.0          # inches, the most common coloring book size
BLEED = 0.125                       # inches, cover only (interior art has no bleed)
MARGIN = 0.6                        # inches of white space around each picture
SPINE_PER_PAGE = 0.002252           # inches per page, KDP white paper
MIN_PAGES_FOR_SPINE_TEXT = 80       # KDP does not allow spine text on 79 pages or fewer
KDP_MIN_PAGES = 24

HERE = Path(__file__).resolve().parent


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "book"


def inch(value):
    return int(round(value * DPI))


# ---------- Step 1: page ideas ----------

def page_ideas(theme, count, ages, demo):
    if demo or not ai.has_text_provider():
        scenes = ["at the beach", "in a garden", "eating ice cream", "reading a book", "in space",
                  "having a picnic", "flying a kite", "at a birthday party", "in the snow", "camping"]
        return [f"{theme} {scenes[i % len(scenes)]}" for i in range(count)]

    prompt = f"""List {count} different coloring book page ideas for a coloring book about "{theme}" for ages {ages}.
Each idea is one short scene (under 15 words) with a clear main subject and a simple background.
Vary the activities, settings and poses so no two pages feel the same. Avoid text, letters and numbers in the scene.
Return only a JSON array of strings."""
    raw = ai.generate_text(prompt)
    match = re.search(r"\[.*\]", raw, re.S)
    ideas = json.loads(match.group(0)) if match else [line.strip("-*• ").strip() for line in raw.splitlines() if line.strip()]
    ideas = [str(i).strip() for i in ideas if str(i).strip()]
    if len(ideas) < count:
        raise RuntimeError(f"Only got {len(ideas)} page ideas, wanted {count}")
    return ideas[:count]


# ---------- Step 2: artwork ----------

def line_art_prompt(idea, ages):
    return (
        f"A black and white coloring book page for ages {ages}: {idea}. "
        "Clean, thick, smooth black outlines on a pure white background. "
        "No shading, no gray, no color, no filled black areas, no texture, no text, no letters, no border. "
        "Large simple shapes that are easy to color inside. Cute cartoon style. "
        "Portrait composition filling the page."
    )


def cover_prompt(theme, ages):
    return (
        f"A bright, cheerful, full-color illustration for the front cover of a coloring book about {theme} "
        f"for ages {ages}. Cute cartoon style, bold colors, one happy main character in the center, "
        "simple fun background. No text, no letters, no words, no title."
    )


def demo_art(idea, out_path, seed):
    """Simple shapes so the PDF layout can be checked without an API key. Not sellable art."""
    rng = random.Random(seed)
    w, h = 1536, 2048
    image = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(image)
    for _ in range(rng.randint(6, 10)):
        r = rng.randint(120, 320)
        x, y = rng.randint(r, w - r), rng.randint(r + 200, h - r)
        if rng.random() < 0.5:
            draw.ellipse([x - r, y - r, x + r, y + r], outline=0, width=14)
        else:
            points = [(x, y - r), (x + r, y + r // 2), (x - r, y + r // 2)]
            draw.polygon(points, outline=0, width=14)
    font = ImageFont.load_default(size=60)
    draw.text((w // 2, 90), "LAYOUT TEST - NOT REAL ART", fill=0, font=font, anchor="mm")
    draw.text((w // 2, 170), idea[:50], fill=0, font=ImageFont.load_default(size=44), anchor="mm")
    image.save(out_path)
    return out_path


def clean_line_art(src, dst):
    """Turns an AI image into a crisp pure black-and-white page sized for 8.5x11 at 300 DPI."""
    with Image.open(src) as raw:
        gray = ImageOps.grayscale(raw.convert("RGB"))
    gray = ImageOps.autocontrast(gray, cutoff=1)

    box_w, box_h = inch(TRIM_W - 2 * MARGIN), inch(TRIM_H - 2 * MARGIN)
    scale = min(box_w / gray.width, box_h / gray.height)
    gray = gray.resize((int(gray.width * scale), int(gray.height * scale)), Image.LANCZOS)
    gray = gray.filter(ImageFilter.MedianFilter(3))           # removes tiny specks
    bw = gray.point(lambda v: 255 if v > 170 else 0, mode="1")  # light grays become white

    page = Image.new("1", (inch(TRIM_W), inch(TRIM_H)), 1)
    page.paste(bw, ((page.width - bw.width) // 2, (page.height - bw.height) // 2))
    page.save(dst, dpi=(DPI, DPI))

    white_pixels = page.histogram()[255]
    return 1 - white_pixels / (page.width * page.height)  # share of the page that is black


# ---------- Step 3: interior PDF ----------

def text_page(lines, sizes):
    page = Image.new("L", (inch(TRIM_W), inch(TRIM_H)), 255)
    draw = ImageDraw.Draw(page)
    y = inch(3.2)
    for line, size in zip(lines, sizes):
        font = ImageFont.load_default(size=size)
        for part in textwrap.wrap(line, width=max(10, int(2600 / size))):
            draw.text((page.width // 2, y), part, fill=0, font=font, anchor="mm")
            y += int(size * 1.35)
        y += int(size * 0.6)
    return page.convert("1")


def belongs_to_page():
    page = text_page(["This book belongs to:"], [130])
    draw = ImageDraw.Draw(page)
    y = inch(5.4)
    draw.line([(inch(1.5), y), (inch(TRIM_W - 1.5), y)], fill=0, width=10)
    return page


def blank_page():
    return Image.new("1", (inch(TRIM_W), inch(TRIM_H)), 1)


def build_interior(title, author, art_pages, out_pdf):
    pages = [text_page([title, f"by {author}"], [120, 70]), blank_page(),
             belongs_to_page(), blank_page()]
    for path in art_pages:
        pages.append(Image.open(path).convert("1"))
        pages.append(blank_page())  # single-sided so markers don't bleed through
    while len(pages) < KDP_MIN_PAGES or len(pages) % 2:
        pages.append(blank_page())
    pages[0].save(out_pdf, save_all=True, append_images=pages[1:], resolution=DPI)
    return len(pages)


# ---------- Step 4: cover PDF ----------

def build_cover(title, subtitle, author, blurb, page_count, front_art, out_pdf):
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.units import inch as RL_INCH
    from reportlab.pdfgen import canvas

    spine = page_count * SPINE_PER_PAGE
    width = 2 * BLEED + 2 * TRIM_W + spine
    height = 2 * BLEED + TRIM_H
    back_x, spine_x = BLEED, BLEED + TRIM_W
    front_x = spine_x + spine

    c = canvas.Canvas(str(out_pdf), pagesize=(width * RL_INCH, height * RL_INCH))
    u = RL_INCH
    main, accent = HexColor("#2EC4B6"), HexColor("#FF6F91")

    c.setFillColor(main)
    c.rect(0, 0, width * u, height * u, stroke=0, fill=1)

    # Front: title band, artwork, author
    c.setFillColor(accent)
    c.roundRect((front_x + 0.5) * u, (BLEED + 8.4) * u, 7.5 * u, 2.1 * u, 0.3 * u, stroke=0, fill=1)
    c.setFillColor(white)
    size = 54 if len(title) <= 22 else 42 if len(title) <= 32 else 34
    c.setFont("Helvetica-Bold", size)
    lines = textwrap.wrap(title, width=int(560 / size)) or [title]
    y = BLEED + 9.45 + 0.3 * (len(lines) - 1)
    for line in lines[:2]:
        c.drawCentredString((front_x + TRIM_W / 2) * u, y * u, line)
        y -= size / 72 * 1.1
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString((front_x + TRIM_W / 2) * u, (BLEED + 8.0) * u, subtitle[:60])

    if front_art:
        c.drawImage(str(front_art), (front_x + 0.75) * u, (BLEED + 1.3) * u, 7.0 * u, 6.4 * u,
                    preserveAspectRatio=True, anchor="c", mask="auto")
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString((front_x + TRIM_W / 2) * u, (BLEED + 0.65) * u, author)

    # Spine (KDP only allows text from 80 pages)
    if page_count >= MIN_PAGES_FOR_SPINE_TEXT:
        c.saveState()
        c.translate((spine_x + spine / 2) * u, (BLEED + TRIM_H / 2) * u)
        c.rotate(-90)
        c.setFont("Helvetica-Bold", min(16, spine * 72 * 0.55))
        c.drawCentredString(0, -4, f"{title}  ·  {author}")
        c.restoreState()

    # Back: blurb. Bottom right 2.25 x 1.5 in is left empty for Amazon's barcode.
    c.setFont("Helvetica-Bold", 28)
    c.drawCentredString((back_x + TRIM_W / 2) * u, (BLEED + 9.6) * u, "Inside this book")
    c.setFont("Helvetica", 17)
    y = BLEED + 8.9
    for paragraph in blurb.split("\n"):
        for line in textwrap.wrap(paragraph, width=62):
            c.drawString((back_x + 0.75) * u, y * u, line)
            y -= 0.33
        y -= 0.2
        if y < BLEED + 2.4:
            break
    c.showPage()
    c.save()
    return spine, width, height


# ---------- Step 5: listing text ----------

def listing(theme, ages, count, demo):
    fallback = {
        "title": f"{theme.title()} Coloring Book",
        "subtitle": f"{count} Fun & Easy Pages for Kids Ages {ages}",
        "description": (f"A fun coloring book full of {theme}! {count} big, simple pictures with thick lines, "
                        "perfect for little hands.\nEvery picture is printed on its own page so markers "
                        "won't bleed through.\nA great gift for birthdays, holidays and rainy days."),
        "keywords": [f"{theme} coloring book", f"coloring book for kids ages {ages}", f"{theme} gifts for kids",
                     "easy coloring pages for kids", "kids activity book", "birthday gift for kids",
                     "simple coloring book"],
    }
    if demo or not ai.has_text_provider():
        return fallback
    prompt = f"""Write an Amazon KDP listing for a coloring book about "{theme}" for kids ages {ages} with {count} pictures,
each on its own page with a blank back. Return only JSON with these keys:
"title" (under 40 characters, must include "Coloring Book"),
"subtitle" (under 70 characters, mentions the ages and page count),
"description" (3 short paragraphs separated by \\n, friendly, no claims you can't back up, no ALL CAPS),
"keywords" (exactly 7 search phrases buyers would type, each under 50 characters, no brand names or trademarks)."""
    try:
        raw = ai.generate_text(prompt, temperature=0.7)
        data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        return {**fallback, **{k: v for k, v in data.items() if v}}
    except Exception as error:
        print(f"Listing generation failed, using a template: {error}")
        return fallback


# ---------- Main ----------

def main():
    parser = argparse.ArgumentParser(description="Make an Amazon KDP coloring book")
    parser.add_argument("theme", help='e.g. "cute axolotls"')
    parser.add_argument("--pages", type=int, default=30, help="number of pictures (default 30)")
    parser.add_argument("--ages", default="4-8")
    parser.add_argument("--author", default=ai.env("BOOK_AUTHOR", "Your Name"))
    parser.add_argument("--demo", action="store_true", help="no AI calls: placeholder art to check the layout")
    args = parser.parse_args()

    demo = args.demo
    if not demo and not ai.has_image_provider():
        sys.exit("No OPENAI_API_KEY or GEMINI_API_KEY set. Add one, or run with --demo to test the layout.")

    out = HERE / "output" / slugify(args.theme)
    raw_dir, page_dir = out / "raw", out / "pages"
    raw_dir.mkdir(parents=True, exist_ok=True)
    page_dir.mkdir(parents=True, exist_ok=True)

    print(f"1/5 Page ideas for '{args.theme}'")
    ideas = page_ideas(args.theme, args.pages, args.ages, demo)
    (out / "ideas.json").write_text(json.dumps(ideas, indent=2))

    print(f"2/5 Artwork ({len(ideas)} pages)")
    art_pages, warnings = [], []
    for i, idea in enumerate(ideas, 1):
        raw_path, page_path = raw_dir / f"{i:02d}.png", page_dir / f"{i:02d}.png"
        if page_path.exists():  # re-running skips pages already made (and already paid for)
            art_pages.append(page_path)
            continue
        try:
            if demo:
                demo_art(idea, raw_path, i)
            else:
                ai.generate_image(line_art_prompt(idea, args.ages), raw_path)
            black = clean_line_art(raw_path, page_path)
        except Exception as error:
            print(f"   page {i} failed, skipped: {error}")
            continue
        if black > 0.35:
            warnings.append(f"page {i:02d}: very dark ({black:.0%} black) - check it or delete it and re-run")
        art_pages.append(page_path)
        print(f"   {i:02d}/{len(ideas)} {idea}")
    if not art_pages:
        sys.exit("No pages were made.")

    print("3/5 Listing text")
    info = listing(args.theme, args.ages, len(art_pages), demo)

    print("4/5 Interior PDF")
    page_count = build_interior(info["title"], args.author, art_pages, out / "interior.pdf")

    print("5/5 Cover PDF")
    front_art = out / "cover_art.png"
    if not front_art.exists():
        try:
            if demo:
                raise RuntimeError("demo")
            ai.generate_image(cover_prompt(args.theme, args.ages), front_art)
        except Exception as error:
            if not demo:
                print(f"   Cover art failed, using page 1 instead: {error}")
            front_art = art_pages[0]
    spine, width, height = build_cover(info["title"], info["subtitle"], args.author, info["description"],
                                       page_count, front_art, out / "cover.pdf")

    (out / "listing.txt").write_text("\n".join([
        f"Title:       {info['title']}",
        f"Subtitle:    {info['subtitle']}",
        f"Author:      {args.author}",
        "",
        "Description:",
        info["description"],
        "",
        "Keywords (one per KDP box):",
        *[f"  {k}" for k in info["keywords"][:7]],
        "",
        "KDP print settings:",
        f"  Trim size: 8.5 x 11 in | Interior: black & white, white paper | Bleed: no bleed",
        f"  Pages: {page_count} | Cover finish: matte or glossy",
        f"  Cover: upload cover.pdf ({width:.3f} x {height:.3f} in, spine {spine:.3f} in)",
        "",
        "AI disclosure: KDP asks whether text and images are AI-generated.",
        "  Answer YES for images" + (" and text." if not demo else " if you used AI art."),
        "",
        "Before publishing: open every page in pages/ and delete bad ones, then re-run.",
        *(["", "Warnings:"] + [f"  {w}" for w in warnings] if warnings else []),
    ]) + "\n")

    print(f"\nDone: {out}")
    print(f"  {page_count} interior pages, spine {spine:.3f} in")
    for w in warnings:
        print(f"  warning: {w}")
    if demo:
        print("  DEMO MODE: placeholder art. Add an API key for real pages.")


if __name__ == "__main__":
    main()
