from __future__ import annotations

import asyncio
import logging
import os

from aiohttp import web
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.bot.commands import (
    handle_cancel,
    handle_close,
    handle_delete,
    handle_edit,
    handle_export,
    handle_list,
    handle_merge,
    handle_pyrus,
    handle_report,
    handle_trip,
    handle_undo,
    handle_unmerge,
)
from src.bot.handlers import (
    handle_callback,
    handle_document,
    handle_help,
    handle_photo,
    handle_start,
    handle_text_message,
)
from src.config import Config
from src.extraction.claude_client import ClaudeExtractor
from src.storage.repository import ReceiptRepository
from src.storage.supabase_client import SupabaseClient

load_dotenv(override=True)
logger = logging.getLogger(__name__)


async def healthcheck(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def run_healthcheck_server() -> None:
    app = web.Application()
    app.router.add_get("/healthz", healthcheck)
    app.router.add_get("/", healthcheck)
    port = int(os.environ.get("PORT", "8080"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Healthcheck server listening on port %d", port)


def main() -> None:
    config = Config.from_env()

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    sb_client = SupabaseClient(config.supabase_url, config.supabase_service_key)
    repo = ReceiptRepository(sb_client, config.supabase_bucket)
    extractor = ClaudeExtractor(config.anthropic_api_key, config.anthropic_model_extractor)

    app = Application.builder().token(config.telegram_bot_token).build()

    app.bot_data["owner_id"] = config.telegram_owner_id
    app.bot_data["repo"] = repo
    app.bot_data["extractor"] = extractor
    app.bot_data["config"] = config
    app.bot_data["sb_client"] = sb_client

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("help", handle_help))
    app.add_handler(CommandHandler("report", handle_report))
    app.add_handler(CommandHandler("list", handle_list))
    app.add_handler(CommandHandler("edit", handle_edit))
    app.add_handler(CommandHandler("delete", handle_delete))
    app.add_handler(CommandHandler("undo", handle_undo))
    app.add_handler(CommandHandler("cancel", handle_cancel))
    app.add_handler(CommandHandler("trip", handle_trip))
    app.add_handler(CommandHandler("export", handle_export))
    app.add_handler(CommandHandler("close", handle_close))
    app.add_handler(CommandHandler("pyrus", handle_pyrus))
    app.add_handler(CommandHandler("merge", handle_merge))
    app.add_handler(CommandHandler("unmerge", handle_unmerge))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    app.add_handler(CallbackQueryHandler(handle_callback))

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Handler error: %s", context.error, exc_info=context.error)
        if isinstance(update, Update) and update.effective_chat:
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=(
                        f"⚠️ Something went wrong while processing that. "
                        f"Try resending. (Error: {type(context.error).__name__})"
                    ),
                )
            except Exception:
                pass

    app.add_error_handler(on_error)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_healthcheck_server())

    logger.info("Starting bot (long polling)…")
    # Keep pending updates so messages queued while the bot was down get processed.
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
