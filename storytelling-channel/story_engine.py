import json

from providers import generate_text

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def load_state():
    return load_json("series_state.json")

def load_bible():
    return load_json("series_bible.json")

def generate_title(series_name, episode_number):
    bible = load_bible()
    prompt = f"""
    Generate one dramatic suspenseful YouTube title.
    Series: {series_name}
    Episode number: {episode_number}
    Genre: {bible["genre"]}
    Location: {bible["location"]}
    Output only the title, no quotes.
    """
    title = generate_text(prompt, temperature=0.9, max_tokens=80)
    return title.splitlines()[0].strip().strip('"')

def generate_story_script(series_name, episode_number, last_theme, story_points):
    bible = load_bible()
    prompt = f"""
    Write a serialized cinematic storytelling script for a long-form YouTube episode.

    Series: {series_name}
    Episode number: {episode_number}
    Previous theme: {last_theme}
    Core theme: {bible["core_theme"]}
    Genre: {bible["genre"]}
    Location: {bible["location"]}
    Essential elements: {', '.join(bible['essential_elements'])}
    Story rules: {'; '.join(bible['story_rules'])}

    Already established storyline:
    {chr(10).join(story_points)}

    Requirements:
    - 1,800 to 2,500 words
    - strong beginning
    - clear narrative tension
    - strong emotional undertone
    - grounded character emphasis
    - atmospheric world-building
    - no filler
    - must escalate the mystery
    - finish with a haunting, unforgettable ending

    Format:
    - this text is read aloud by a single narrator voice exactly as written
    - output only the spoken narration as plain prose paragraphs
    - no title, headings, scene labels, speaker names, stage directions, sound cues or markdown
    """
    return generate_text(
        prompt,
        system="You are a master storyteller and YouTube scriptwriter.",
        temperature=0.9,
        max_tokens=2600
    )

def generate_scene_prompts(title, script, count=4):
    bible = load_bible()
    prompt = f"""
    Describe {count} distinct cinematic still images that illustrate key moments of this episode, in story order.
    Title: {title}
    Location: {bible["location"]}
    Genre: {bible["genre"]}

    Script:
    {script[:6000]}

    Rules:
    - one image per line, no numbering, no extra text
    - each line under 40 words: subject, setting, lighting, mood
    - moody historical-noir photography, no text, no logos, no recognizable real people
    """
    lines = generate_text(prompt, temperature=0.8, max_tokens=600).splitlines()
    scenes = [line.strip(" -*0123456789.\t") for line in lines if len(line.strip()) > 15]
    return scenes[:count]

def next_episode():
    state = load_state()
    bible = load_bible()

    episode_number = state["episode_number"] + 1
    series_name = state["series_name"]
    last_theme = state["last_theme"]
    story_points = state.get("completed_story_points", [])

    title = generate_title(series_name, episode_number)
    script = generate_story_script(series_name, episode_number, last_theme, story_points)

    state["episode_number"] = episode_number
    state["last_title"] = title
    state["last_theme"] = f"Episode {episode_number} reveals a deeper clue beneath {bible['location']}"
    state["completed_story_points"] = story_points + [f"Episode {episode_number} uncovers a deeper clue in the city's hidden history."]

    save_json("series_state.json", state)

    return title, script, series_name
