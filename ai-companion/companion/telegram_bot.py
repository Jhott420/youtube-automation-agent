"""Telegram interface (python-telegram-bot v21+, long polling).

Run:  python -m companion.telegram_bot

User IDs are stored as "tg:<telegram user id>" -- use that form with the
admin CLI (e.g. `admin pause tg:123456789`). `admin users` lists them.

Only private chats are handled; the bot ignores groups.
"""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Settings
from .engine import ChatEngine, Reply

log = logging.getLogger(__name__)

TELEGRAM_MAX_CHARS = 4096

HELP_TEXT = (
    "Commands:\n"
    "/ai - what am I? (I'm an AI)\n"
    "/forget - delete everything I remember about you and our chat history\n"
    "/help - this message"
)


def split_message(text: str, limit: int = TELEGRAM_MAX_CHARS) -> list[str]:
    """Split on paragraph/line boundaries so no chunk exceeds Telegram's limit."""
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


class CompanionBot:
    def __init__(self, engine: ChatEngine):
        self.engine = engine
        self.allowed = engine.settings.telegram_allowed_user_ids

    # ---- helpers ---------------------------------------------------------------

    @staticmethod
    def _uid(update: Update) -> str:
        return f"tg:{update.effective_user.id}"

    @staticmethod
    def _name(update: Update) -> str | None:
        u = update.effective_user
        return u.first_name or u.username

    def _is_allowed(self, update: Update) -> bool:
        return not self.allowed or str(update.effective_user.id) in self.allowed

    async def _send(self, update: Update, reply: Reply) -> None:
        if reply.disclosure:
            await update.effective_chat.send_message(reply.disclosure)
        if reply.text:
            for chunk in split_message(reply.text):
                await update.effective_chat.send_message(chunk)

    async def _reject(self, update: Update) -> None:
        await update.effective_chat.send_message("Sorry, this bot is private.")

    # ---- handlers --------------------------------------------------------------

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update):
            return await self._reject(update)
        reply = await asyncio.to_thread(self.engine.greet, self._uid(update), self._name(update), "telegram")
        await self._send(update, reply)

    async def ai(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_chat.send_message(self.engine.disclosure_text())

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_chat.send_message(HELP_TEXT)

    async def forget(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update):
            return await self._reject(update)
        uid = self._uid(update)

        def _wipe() -> tuple[int, int]:
            return self.engine.memory.wipe(uid), self.engine.db.wipe_conversation(uid)

        mems, msgs = await asyncio.to_thread(_wipe)
        await update.effective_chat.send_message(
            f"Done. I deleted {mems} memories and {msgs} messages about you."
        )

    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_allowed(update):
            return await self._reject(update)
        uid = self._uid(update)
        await update.effective_chat.send_action(ChatAction.TYPING)
        reply = await asyncio.to_thread(
            self.engine.handle_message, uid, update.message.text, self._name(update), "telegram"
        )
        await self._send(update, reply)
        if reply.kind == "normal":
            # After the reply is delivered, fold old turns into long-term memory.
            await asyncio.to_thread(self.engine.summarize_if_needed, uid)

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.exception("Telegram handler error", exc_info=context.error)

    # ---- wiring ------------------------------------------------------------------

    def build_application(self, token: str) -> Application:
        app = Application.builder().token(token).build()
        private = filters.ChatType.PRIVATE
        app.add_handler(CommandHandler("start", self.start, filters=private))
        app.add_handler(CommandHandler("ai", self.ai, filters=private))
        app.add_handler(CommandHandler("help", self.help, filters=private))
        app.add_handler(CommandHandler("forget", self.forget, filters=private))
        app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, self.on_message))
        app.add_error_handler(self.on_error)
        return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # don't log the bot token in URLs
    settings = Settings.from_env()
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set (see .env.example)")
    engine = ChatEngine.from_settings(settings)
    log.info("Starting bot with persona %r on model %s", engine.personas.get().name, settings.model)
    CompanionBot(engine).build_application(settings.telegram_bot_token).run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
