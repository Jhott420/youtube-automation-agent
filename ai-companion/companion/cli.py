"""Operator admin CLI.

    admin persona show [--prompt]      print persona.yaml (or the compiled system prompt)
    admin persona edit                 edit persona.yaml in $EDITOR, validated before saving
    admin persona validate             check persona.yaml without changing anything
    admin users                        list known users
    admin memory list <user_id>
    admin memory add <user_id> <text>
    admin memory edit <id> <text>
    admin memory delete <id>
    admin memory wipe <user_id>
    admin conversations <user_id> [--limit N]
    admin pause <user_id>              kill switch: the bot stops replying to this user
    admin resume <user_id>

Talks to SQLite and the YAML file directly (no API key needed). Changes take
effect immediately in a running bot: the DB is shared and the persona hot-reloads.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import typer

from .config import Settings
from .db import Database
from .embeddings import make_embedder
from .memory import MemoryStore
from .persona import PersonaError, load_persona_file
from .prompt_builder import build_prompt

app = typer.Typer(help="Admin controls for the AI companion.", no_args_is_help=True)
persona_app = typer.Typer(help="View and edit the persona.", no_args_is_help=True)
memory_app = typer.Typer(help="Manage long-term memories.", no_args_is_help=True)
app.add_typer(persona_app, name="persona")
app.add_typer(memory_app, name="memory")


def _settings() -> Settings:
    return Settings.from_env()


def _db() -> Database:
    return Database(_settings().db_path)


def _memory() -> MemoryStore:
    s = _settings()
    return MemoryStore(Database(s.db_path), make_embedder(s.embedder, s.embedding_model))


def _fail(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


# ---------------------------------------------------------------- persona ----


@persona_app.command("show")
def persona_show(
    prompt: bool = typer.Option(False, "--prompt", help="Show the compiled system prompt instead of the YAML."),
):
    """Print the current persona."""
    path = _settings().persona_path
    if not prompt:
        typer.echo(path.read_text(encoding="utf-8"))
        return
    try:
        persona = load_persona_file(path)
    except PersonaError as e:
        _fail(str(e))
    bundle = build_prompt(persona, memories=[], history=[], user_text="(example)")
    typer.echo(bundle.system_text)


@persona_app.command("validate")
def persona_validate():
    """Check persona.yaml for errors."""
    path = _settings().persona_path
    try:
        p = load_persona_file(path)
    except PersonaError as e:
        _fail(str(e))
    typer.secho(f"OK: {path} defines persona {p.name!r}", fg=typer.colors.GREEN)


@persona_app.command("edit")
def persona_edit():
    """Open persona.yaml in $EDITOR. The file is only replaced if it validates."""
    path = _settings().persona_path
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or ("nano" if shutil.which("nano") else "vi")

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as tmp:
        tmp.write(path.read_text(encoding="utf-8"))
        tmp_path = Path(tmp.name)
    try:
        while True:
            subprocess.call([*shlex.split(editor), str(tmp_path)])
            try:
                persona = load_persona_file(tmp_path)
            except PersonaError as e:
                typer.secho(str(e), fg=typer.colors.RED, err=True)
                if typer.confirm("Edit again? (No discards your changes)", default=True):
                    continue
                typer.echo("Discarded; persona unchanged.")
                raise typer.Exit(1)
            break

        if tmp_path.read_text(encoding="utf-8") == path.read_text(encoding="utf-8"):
            typer.echo("No changes.")
            return
        shutil.copy2(path, path.with_suffix(".yaml.bak"))
        # Write next to the target then rename: a running bot never sees a half-written file.
        staging = path.with_suffix(".yaml.tmp")
        shutil.copyfile(tmp_path, staging)
        os.replace(staging, path)
        typer.secho(
            f"Saved persona {persona.name!r}. Backup at {path.with_suffix('.yaml.bak')}. "
            "A running bot picks this up on the next message.",
            fg=typer.colors.GREEN,
        )
    finally:
        tmp_path.unlink(missing_ok=True)


# ----------------------------------------------------------------- memory ----


@memory_app.command("list")
def memory_list(user_id: str):
    """List a user's long-term memories."""
    rows = _memory().list(user_id)
    if not rows:
        typer.echo(f"No memories for {user_id}.")
        return
    for m in rows:
        typer.echo(f"[{m.id:>5}] ({m.source}, {m.updated_at}) {m.content}")


@memory_app.command("add")
def memory_add(user_id: str, text: str):
    """Add a memory by hand."""
    typer.echo(f"Added memory {_memory().add(user_id, text)}.")


@memory_app.command("edit")
def memory_edit(memory_id: int, text: str):
    """Replace a memory's text (re-embeds it)."""
    if not _memory().edit(memory_id, text):
        _fail(f"Memory {memory_id} not found.")
    typer.echo(f"Updated memory {memory_id}.")


@memory_app.command("delete")
def memory_delete(memory_id: int):
    """Delete one memory."""
    if not _memory().delete(memory_id):
        _fail(f"Memory {memory_id} not found.")
    typer.echo(f"Deleted memory {memory_id}.")


@memory_app.command("wipe")
def memory_wipe(
    user_id: str,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    history: bool = typer.Option(False, "--history", help="Also delete the conversation history."),
):
    """Delete ALL memories for a user (optionally their chat history too)."""
    what = "all memories" + (" and chat history" if history else "")
    if not yes and not typer.confirm(f"Delete {what} for {user_id}?"):
        raise typer.Abort()
    mem = _memory()
    n = mem.wipe(user_id)
    msg = f"Deleted {n} memories"
    if history:
        msg += f" and {mem.db.wipe_conversation(user_id)} messages"
    typer.echo(f"{msg} for {user_id}.")


# --------------------------------------------------------- users / control ----


@app.command("users")
def users():
    """List known users and their status."""
    rows = _db().list_users()
    if not rows:
        typer.echo("No users yet.")
    for u in rows:
        status = typer.style("PAUSED", fg=typer.colors.RED) if u.paused else "active"
        typer.echo(f"{u.user_id:<24} {u.display_name or '-':<20} {status:<8} since {u.created_at}")


@app.command("conversations")
def conversations(user_id: str, limit: int = typer.Option(50, "--limit", "-n")):
    """Show a user's recent conversation."""
    msgs = _db().recent_messages(user_id, limit)
    if not msgs:
        typer.echo(f"No messages for {user_id}.")
    for m in msgs:
        who = typer.style("USER", fg=typer.colors.CYAN) if m.role == "user" else typer.style("BOT ", fg=typer.colors.MAGENTA)
        flag = typer.style(f" [{m.flag}]", fg=typer.colors.RED) if m.flag else ""
        typer.echo(f"{m.created_at} {who}{flag}: {m.content}")


@app.command("pause")
def pause(user_id: str):
    """Kill switch: the bot stops replying to this user immediately."""
    db = _db()
    db.get_or_create_user(user_id)
    db.set_paused(user_id, True)
    typer.secho(f"{user_id} paused. The bot will not reply to them.", fg=typer.colors.YELLOW)


@app.command("resume")
def resume(user_id: str):
    """Undo `pause`."""
    if not _db().set_paused(user_id, False):
        _fail(f"Unknown user {user_id}.")
    typer.secho(f"{user_id} resumed.", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
