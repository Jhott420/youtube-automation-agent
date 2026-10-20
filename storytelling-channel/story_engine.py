import json
import os
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}],
        temperature=0.9,
        max_tokens=80
    )
    return response.choices[0].message.content.strip()

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
    """
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role":"system","content":"You are a master storyteller and YouTube scriptwriter."},
            {"role":"user","content":prompt}
        ],
        temperature=0.9,
        max_tokens=2600
    )
    return response.choices[0].message.content.strip()

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
