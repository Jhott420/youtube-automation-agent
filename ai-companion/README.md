# AI Companion

An operator-controlled AI companion chatbot ("AI girlfriend"-style) built on
Claude, with a Telegram front-end, long-term memory, and an admin CLI.

**You, the operator, control everything:** the persona, what the bot remembers,
and a per-user kill switch. **The bot always says it is an AI.** It's built to be
honest with the people it talks to, not to pass as a human.

```
Telegram ─┐                      ┌─ config/persona.yaml (hot-reloaded)
          ├─► ChatEngine ────────┼─ SQLite: users, messages, memories
HTTP API ─┘   │                  └─ Claude (Messages API)
              ├ kill switch → AI disclosure
              └ memories + history → prompt → Claude
admin CLI ──► SQLite + persona.yaml directly (no API key needed)
```

## Features

| Area | What it does |
|---|---|
| Persona | `config/persona.yaml`: name, age, backstory, traits, speech style, emoji habits, relationship dynamic, hard limits, greeting, and a **required AI disclosure**. Hot-reloads on the next message. A bad edit is rejected and the last good persona stays live. |
| Disclosure | The disclosure is put into every system prompt and sent as the first message to every new user. `/ai` shows it again. Validation refuses a persona whose disclosure doesn't say it's an AI, or whose age is under 18. |
| Prompt builder | Combines persona + disclosure + baseline rules (a cached system block), retrieved memories (a per-turn system block), and recent history (the `messages` array). |
| Memory | Every turn is stored. Older turns are summarized by Claude into short facts. The top-k facts by embedding similarity are added to the prompt each turn. The admin can list, add, edit, delete and wipe them. |
| Admin | Typer CLI (`admin ...`) plus matching HTTP endpoints, protected by a token. |

## Setup

Requires Python 3.11+.

```bash
cd ai-companion
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .            # installs the `admin` and `companion-bot` commands

cp .env.example .env        # then fill in ANTHROPIC_API_KEY and TELEGRAM_BOT_TOKEN
admin persona validate
```

To get a Telegram bot token, message [@BotFather](https://t.me/BotFather) and send `/newbot`.
To make the bot private, put your numeric Telegram ID in `TELEGRAM_ALLOWED_USER_IDS`
(you can get your ID from @userinfobot).

> **Model note:** the default is `claude-sonnet-4-5`, as requested. That model
> is **deprecated and retires on 2026-11-30**. Set `CLAUDE_MODEL=claude-sonnet-5-5`
> in `.env` before then. No code changes are needed.

## Running

```bash
companion-bot                                   # Telegram bot (or: python -m companion.telegram_bot)
uvicorn companion.api:app --port 8000           # optional HTTP API (needs ADMIN_API_TOKEN)
```

Telegram commands for users: `/start`, `/ai` (show the disclosure), `/forget` (delete
their own memories and history), and `/help`. The bot only answers in private chats.

### Docker

```bash
docker build -t ai-companion .
docker run -d --env-file .env -v companion-data:/app/data --name companion ai-companion
# admin commands inside the container:
docker exec -it companion admin users
# to edit the persona without rebuilding, mount it:  -v $PWD/config:/app/config
```

## Admin CLI

User IDs are prefixed by platform: `tg:<telegram id>`, `api:<id>`. `admin users` lists them.

```bash
admin persona show                 # print persona.yaml
admin persona show --prompt        # print the compiled system prompt
admin persona edit                 # opens $EDITOR; only saves if valid; keeps a .bak
admin persona validate

admin users
admin conversations tg:123456789 -n 100
admin memory list   tg:123456789
admin memory add    tg:123456789 "User's dog is named Max"
admin memory edit   42 "User's dog is named Maximus"
admin memory delete 42
admin memory wipe   tg:123456789 [--history] [--yes]

admin pause  tg:123456789          # kill switch: bot goes silent for this user, instantly
admin resume tg:123456789
```

The CLI writes to the same SQLite file the bot uses, and the persona file reloads
on its own, so every change applies to a running bot right away.

## HTTP API

Every route except `/health` needs `Authorization: Bearer $ADMIN_API_TOKEN`. If that
variable isn't set, those routes return 503. An open `/chat` would let anyone spend
your Claude credits.

| Method & path | Purpose |
|---|---|
| `GET /health` | status, model, active persona, last persona reload error |
| `POST /chat` `{user_id, text, display_name?}` | chat as `api:<user_id>` |
| `GET /admin/persona` | current persona |
| `GET /admin/users` | users |
| `GET /admin/users/{id}/conversations?limit=` | history |
| `GET/POST/DELETE /admin/users/{id}/memories` | list / add / wipe |
| `PATCH/DELETE /admin/memories/{id}` | edit / delete one |
| `POST /admin/users/{id}/pause`, `/resume` | kill switch |

Bind it to `127.0.0.1` (the default) or put it behind a reverse proxy with TLS.

## How memory works

1. Every message is stored in `messages`.
2. The last `HISTORY_WINDOW` (default 20) messages go to Claude verbatim.
3. When `SUMMARIZE_BATCH` (default 20) older, unsummarized messages pile up, Claude
   condenses them into one-line facts ("User's sister Ana lives in Lisbon"). Each fact
   is embedded and stored in `memories`. This runs after the reply is sent.
4. Each turn, the `MEMORY_TOP_K` facts most similar to the new message (cosine ≥
   `MEMORY_MIN_SCORE`) go into the system prompt.

**Embeddings.** The default `hashing` embedder is pure Python with no extra
dependencies or downloads. It matches on shared words and word fragments, which is
good enough for facts like names, pets and hobbies, but it doesn't understand
synonyms. For real semantic matching, run `pip install sentence-transformers` (a
large install that includes PyTorch) and set `EMBEDDER=sentence-transformers`.
Existing memories are re-embedded automatically the next time they're retrieved.

## Baseline rules

There is no separate filtering layer. Claude's reply is sent as is. The only rules are
four lines in `companion/safety.py`, added to every system prompt after the persona:
be honest about being an AI, point users who mention self-harm toward crisis lines,
keep things platonic with anyone who says they're under 18, and don't guilt-trip or
pressure users. They live in code so persona edits can't remove them.

Persona validation still requires an AI disclosure and an age of 18+.

**Privacy:** the default disclosure tells users the operator can read chats. Users can
delete their own data with `/forget`. The SQLite file holds personal conversations,
so protect it accordingly.

## Tests

```bash
pytest -q
```

The tests cover memory retrieval and summarization, persona validation and hot reload,
prompt assembly, the engine pipeline,
and the API auth. They use a fake LLM, so no API key is needed and nothing is billed.

## Project layout

```
ai-companion/
├── companion/
│   ├── config.py          settings from env / .env
│   ├── persona.py         Persona model + hot-reloading manager
│   ├── prompt_builder.py  persona + disclosure + rules + memories + history
│   ├── memory.py          memory CRUD, retrieval, summarization
│   ├── embeddings.py      hashing embedder (default) / sentence-transformers
│   ├── safety.py          baseline system-prompt rules
│   ├── llm.py             Claude client wrapper
│   ├── engine.py          the pipeline every interface uses
│   ├── db.py              SQLite schema + queries
│   ├── api.py             FastAPI app
│   ├── telegram_bot.py    Telegram interface
│   └── cli.py             `admin` CLI
├── config/persona.yaml    sample persona ("Mira")
├── tests/
├── requirements.txt, pyproject.toml, .env.example, Dockerfile
```
