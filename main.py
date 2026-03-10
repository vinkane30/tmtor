"""
main.py — IDX Story Bot
Run: python main.py
"""

import asyncio
import logging
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron     import CronTrigger
from telegram import Bot
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler

import config
from modules.database    import init_db
from modules.commands    import cmd_scan, cmd_ticker, cmd_news, cmd_report
from modules.story       import run_story_detection
from modules.technical   import analyse_tickers_batch, is_market_healthy
from modules.signals     import build_and_save_signals, format_scan_summary
from modules.self_improve import build_weekly_report, format_weekly_report_message, evaluate_open_signals

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    handlers= [logging.StreamHandler(sys.stdout),
               logging.FileHandler("idx_bot.log", encoding="utf-8")],
)
logger = logging.getLogger(__name__)


# ── Scheduled jobs ─────────────────────────────────────────────────────────

async def _auto_scan(bot: Bot):
    healthy, market_msg = is_market_healthy()
    if not healthy:
        await bot.send_message(config.TELEGRAM_CHAT_ID,
                               f"⛔ _{market_msg}_", parse_mode=ParseMode.MARKDOWN)
        return

    stories = await run_story_detection()
    if not stories:
        return

    technicals     = analyse_tickers_batch([(s.ticker, s.company_name) for s in stories])
    signal_triples = build_and_save_signals(stories, technicals)
    if not signal_triples:
        return

    await bot.send_message(config.TELEGRAM_CHAT_ID,
                           format_scan_summary([(s, t) for s, t, _ in signal_triples]),
                           parse_mode=ParseMode.MARKDOWN)
    for _, _, msg in signal_triples:
        await asyncio.sleep(0.8)
        await bot.send_message(config.TELEGRAM_CHAT_ID, msg,
                               parse_mode=ParseMode.MARKDOWN,
                               disable_web_page_preview=True)


async def _auto_weekly_report(bot: Bot):
    evaluate_open_signals()
    report = build_weekly_report()
    await bot.send_message(config.TELEGRAM_CHAT_ID,
                           format_weekly_report_message(report),
                           parse_mode=ParseMode.MARKDOWN)


def _build_scheduler(bot: Bot) -> AsyncIOScheduler:
    s = AsyncIOScheduler(timezone=config.TIMEZONE)
    for t in config.SCAN_TIMES_WIB:
        h, m = t.split(":")
        s.add_job(_auto_scan, CronTrigger(hour=int(h), minute=int(m),
                                          timezone=config.TIMEZONE),
                  kwargs={"bot": bot}, id=f"scan_{t.replace(':','')}",
                  max_instances=1, coalesce=True, misfire_grace_time=300)
    s.add_job(_auto_weekly_report,
              CronTrigger(day_of_week=config.WEEKLY_REPORT_DAY,
                          hour=int(config.WEEKLY_REPORT_TIME.split(":")[0]),
                          minute=int(config.WEEKLY_REPORT_TIME.split(":")[1]),
                          timezone=config.TIMEZONE),
              kwargs={"bot": bot}, id="weekly_report")
    return s


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    if config.TELEGRAM_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("Set TELEGRAM_TOKEN env var before starting."); sys.exit(1)

    init_db()

    app = Application.builder().token(config.TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("scan",   cmd_scan))
    app.add_handler(CommandHandler("ticker", cmd_ticker))
    app.add_handler(CommandHandler("news",   cmd_news))
    app.add_handler(CommandHandler("report", cmd_report))

    scheduler = _build_scheduler(app.bot)
    scheduler.start()
    logger.info("IDX Story Bot running. Scans: %s WIB", config.SCAN_TIMES_WIB)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
