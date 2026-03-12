"""
main.py — IDX Story Bot v4
Run: python main.py
"""

import asyncio
import logging
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron      import CronTrigger
from telegram import Bot
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler

import config
from modules.database     import init_db
from modules.commands     import cmd_start, cmd_scan, cmd_ticker, cmd_news, cmd_report
from modules.story        import run_story_detection
from modules.technical    import analyse_tickers_batch, is_market_healthy
from modules.regime       import detect_regime
from modules.signals      import build_and_save_signals, format_scan_summary
from modules.self_improve import build_weekly_report, format_weekly_report_message, evaluate_open_signals

logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("idx_bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# SCHEDULED JOBS
# ─────────────────────────────────────────────

async def _auto_scan(bot: Bot):
    """Scheduled scan — regime-aware, sends to TELEGRAM_CHAT_ID."""
    try:
        regime = detect_regime()

        healthy, market_msg = is_market_healthy()
        regime_emoji = {
            "BULL":    "🟢",
            "SIDEWAYS":"🟡",
            "BEAR":    "🟠",
            "PANIC":   "🔴",
            "UNKNOWN": "⚪",
        }.get(regime.regime, "⚪")

        header = (
            f"{regime_emoji} *Auto Scan — {regime.regime} Regime*\n"
            f"_{market_msg}_\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        )

        stories = await run_story_detection()

        if not stories:
            if regime.regime in ("BEAR", "PANIC"):
                await bot.send_message(
                    config.TELEGRAM_CHAT_ID,
                    header +
                    "No new catalysts.\n"
                    "Regime: Mean Reversion + RS Hunt mode.\n"
                    "Check LQ45 discounts: /ticker BBCA /ticker BBRI",
                    parse_mode=ParseMode.MARKDOWN
                )
            return

        technicals     = analyse_tickers_batch(
            [(s.ticker, s.company_name) for s in stories],
            regime_result=regime
        )
        signal_triples = build_and_save_signals(stories, technicals)

        if not signal_triples:
            return

        # Send summary
        summary = format_scan_summary([(s, t) for s, t, _ in signal_triples])
        await bot.send_message(
            config.TELEGRAM_CHAT_ID,
            header + summary,
            parse_mode=ParseMode.MARKDOWN
        )

        # Send individual signal cards
        for _, _, msg in signal_triples:
            await asyncio.sleep(0.8)
            try:
                await bot.send_message(
                    config.TELEGRAM_CHAT_ID,
                    msg,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True
                )
            except Exception as e:
                # Fallback plain text if Markdown parse fails
                import re
                clean = re.sub(r'[*_`]', '', msg)
                await bot.send_message(
                    config.TELEGRAM_CHAT_ID,
                    clean,
                    disable_web_page_preview=True
                )

    except Exception as e:
        logger.error("Auto scan error: %s", e, exc_info=True)


async def _auto_weekly_report(bot: Bot):
    """Scheduled weekly performance report."""
    try:
        evaluate_open_signals()
        report = build_weekly_report()
        text   = format_weekly_report_message(report)

        for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
            await bot.send_message(
                config.TELEGRAM_CHAT_ID,
                chunk,
                parse_mode=ParseMode.MARKDOWN
            )
            await asyncio.sleep(0.3)

    except Exception as e:
        logger.error("Weekly report error: %s", e, exc_info=True)


def _build_scheduler(bot: Bot) -> AsyncIOScheduler:
    s = AsyncIOScheduler(timezone=config.TIMEZONE)

    # Daily scans at configured WIB times
    for t in config.SCAN_TIMES_WIB:
        h, m = t.split(":")
        s.add_job(
            _auto_scan,
            CronTrigger(
                hour=int(h), minute=int(m),
                timezone=config.TIMEZONE
            ),
            kwargs         = {"bot": bot},
            id             = f"scan_{t.replace(':', '')}",
            max_instances  = 1,
            coalesce       = True,
            misfire_grace_time = 300,
        )

    # Weekly report
    s.add_job(
        _auto_weekly_report,
        CronTrigger(
            day_of_week = config.WEEKLY_REPORT_DAY,
            hour        = int(config.WEEKLY_REPORT_TIME.split(":")[0]),
            minute      = int(config.WEEKLY_REPORT_TIME.split(":")[1]),
            timezone    = config.TIMEZONE,
        ),
        kwargs = {"bot": bot},
        id     = "weekly_report",
    )

    return s


# ─────────────────────────────────────────────
# POST INIT — start scheduler after bot is ready
# ─────────────────────────────────────────────

async def post_init(application: Application):
    scheduler = _build_scheduler(application.bot)
    scheduler.start()
    logger.info(
        "Scheduler started. Jobs: %s",
        [job.id for job in scheduler.get_jobs()]
    )


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    if config.TELEGRAM_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.error("TELEGRAM_TOKEN not set. Add it to environment variables.")
        sys.exit(1)

    if not hasattr(config, "TELEGRAM_CHAT_ID") or not config.TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_CHAT_ID not set. Add it to environment variables.")
        sys.exit(1)

    logger.info("Initialising database...")
    init_db()

    logger.info("Building Telegram application...")
    app = (
        Application.builder()
        .token(config.TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    # 4 commands only
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("scan",   cmd_scan))
    app.add_handler(CommandHandler("ticker", cmd_ticker))
    app.add_handler(CommandHandler("news",   cmd_news))
    app.add_handler(CommandHandler("report", cmd_report))

    logger.info(
        "IDX Story Bot v4 running. "
        "Regime-aware. Scheduled scans: %s WIB",
        config.SCAN_TIMES_WIB
    )

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
