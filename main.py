"""
main.py — IDX Story Bot (Webhook mode)

Free Render plan: Web Service, wakes on Telegram message, sleeps when idle.
No scheduler — all commands are on-demand via Telegram.

How it works:
  1. Render gives you a URL: https://your-app.onrender.com
  2. You register that URL as a Telegram webhook once (run /setwebhook)
  3. Every time you send a command, Telegram POSTs it to your URL
  4. Render wakes up, processes it, returns 200, goes back to sleep
"""

import asyncio
import logging
import os
import sys

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import Application, CommandHandler

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import config
from modules.database  import init_db
from modules.commands  import cmd_start, cmd_scan, cmd_ticker, cmd_news, cmd_report

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    handlers= [logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ── Validation ─────────────────────────────────────────────────────────────

def _validate():
    missing = []
    if not config.TELEGRAM_TOKEN or config.TELEGRAM_TOKEN == "YOUR_BOT_TOKEN_HERE":
        missing.append("TELEGRAM_TOKEN")
    if missing:
        logger.error("Missing env vars: %s", missing)
        sys.exit(1)


# ── Build telegram app (shared instance) ───────────────────────────────────

def _build_app() -> Application:
    app = Application.builder().token(config.TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("scan",   cmd_scan))
    app.add_handler(CommandHandler("ticker", cmd_ticker))
    app.add_handler(CommandHandler("news",   cmd_news))
    app.add_handler(CommandHandler("report", cmd_report))
    return app


# ── FastAPI app ─────────────────────────────────────────────────────────────

_tg_app: Application = None

@asynccontextmanager
async def lifespan(web_app: FastAPI):
    # Startup
    global _tg_app
    _validate()
    init_db()
    _tg_app = _build_app()
    await _tg_app.initialize()
    logger.info("IDX Story Bot ready (webhook mode)")
    yield
    # Shutdown
    await _tg_app.shutdown()

web = FastAPI(lifespan=lifespan)


@web.get("/")
async def health():
    """Health check — Render pings this to verify the service is up."""
    return {"status": "ok", "bot": "idx-story-bot"}


@web.post("/webhook")
async def webhook(request: Request):
    """
    Telegram calls this URL every time a user sends a command.
    Render wakes up → we process → return 200 → Render may sleep again.
    """
    try:
        data   = await request.json()
        update = Update.de_json(data, _tg_app.bot)
        await _tg_app.process_update(update)
    except Exception as e:
        logger.error("Webhook error: %s", e, exc_info=True)
    return Response(status_code=200)


@web.get("/setwebhook")
async def set_webhook(request: Request):
    """
    Call this once after deploy to register the webhook with Telegram.
    Visit: https://your-app.onrender.com/setwebhook
    """
    base_url = str(request.base_url).rstrip("/")
    webhook_url = f"{base_url}/webhook"
    result = await _tg_app.bot.set_webhook(
        url              = webhook_url,
        allowed_updates  = ["message"],
        drop_pending_updates = True,
    )
    if result:
        logger.info("Webhook set to: %s", webhook_url)
        return {"status": "webhook set", "url": webhook_url}
    return {"status": "failed"}


@web.get("/deletewebhook")
async def delete_webhook():
    """Removes the webhook — useful when switching back to polling locally."""
    await _tg_app.bot.delete_webhook()
    return {"status": "webhook deleted"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:web", host="0.0.0.0", port=port, log_level="info")
