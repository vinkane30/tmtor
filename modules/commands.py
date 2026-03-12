"""
modules/commands.py — IDX Story Bot Commands
/start   welcome + command list
/scan    full pipeline (market health + story + technical)
/ticker  full trade setup card
/news    corporate action feed
/report  weekly win rate + learnings
"""

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from modules.story        import run_story_detection
from modules.technical    import (analyse_ticker, analyse_tickers_batch,
                                   is_market_healthy, format_full_analysis)
from modules.signals      import build_and_save_signals, format_scan_summary
from modules.self_improve import (build_weekly_report, format_weekly_report_message,
                                   evaluate_open_signals)
from modules.database     import get_recent_catalysts

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

async def _send(update: Update, text: str):
    """Send long messages in chunks respecting Telegram 4096 char limit."""
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        await update.message.reply_text(
            chunk,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )

def _catalyst_label(ctype: str) -> str:
    return {
        "asset_injection":        "Injeksi Aset",
        "strategic_acquisition":  "Akuisisi / Merger",
        "rights_issue_strategic": "Rights Issue + Investor Strategis",
        "government_contract":    "Kontrak Pemerintah",
        "insider_buying":         "Pembelian Insider",
        "buyback":                "Buyback",
        "special_agm":            "RUPSLB",
        "rights_issue":           "Rights Issue",
    }.get(ctype, ctype.replace("_", " ").title())


# ─────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Selamat datang di IDX Story Bot!*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🤖 _Bot ini menggabungkan Bandarmology, analisis fundamental,_\n"
        "_dan momentum teknikal untuk saham IDX._\n\n"
        "📋 *COMMANDS:*\n\n"
        "🔍 /scan\n"
        "   _Scan seluruh pasar untuk corporate action_\n"
        "   _+ analisis teknikal. Cek IHSG dulu._\n\n"
        "📊 /ticker `<kode>`\n"
        "   _Full trade setup: entry, exit, thesis,_\n"
        "   _risk, fundamental, support/resist, news._\n"
        "   _Contoh: /ticker BBRI_\n\n"
        "📰 /news\n"
        "   _Corporate actions 24 jam terakhir_\n"
        "   _(akuisisi, rights issue, buyback, dll)_\n\n"
        "📈 /report\n"
        "   _Weekly performance: win rate, P&L,_\n"
        "   _signal evaluation & learnings._\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ _Disclaimer: Bukan rekomendasi investasi._\n"
        "_DYOR. Manage your own risk._",
        parse_mode=ParseMode.MARKDOWN
    )


# ─────────────────────────────────────────────
# /scan
# ─────────────────────────────────────────────

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "🔍 *Initiating scan…*\n_Checking IHSG health…_",
        parse_mode=ParseMode.MARKDOWN
    )

    # ── Step 1: IHSG gate ────────────────────
    healthy, market_msg = is_market_healthy()
    ihsg_emoji = "✅" if healthy else "⛔"
    header = f"{ihsg_emoji} _{market_msg}_\n{'━'*32}\n\n"

    if not healthy:
        await msg.edit_text(
            header +
            "🚫 *Scan paused.*\n"
            "_IHSG below EMA200 — capital preservation mode._\n"
            "_Tunggu IHSG recovery sebelum buka posisi baru._",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ── Step 2: Story detection ───────────────
    await msg.edit_text(
        header + "📰 _Scraping Kontan, Bisnis, KPPU, RSS…_",
        parse_mode=ParseMode.MARKDOWN
    )
    stories = await run_story_detection()

    if not stories:
        await msg.edit_text(
            header +
            "📭 *Tidak ada corporate action signifikan saat ini.*\n"
            "_Coba lagi nanti atau gunakan /ticker untuk analisis manual._",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ── Step 3: Technical filter ──────────────
    await msg.edit_text(
        header + f"📊 _Running technical analysis on {len(stories)} catalyst(s)…_",
        parse_mode=ParseMode.MARKDOWN
    )
    technicals     = analyse_tickers_batch([(s.ticker, s.company_name) for s in stories])
    signal_triples = build_and_save_signals(stories, technicals)

    if not signal_triples:
        await msg.edit_text(
            header +
            f"📰 *{len(stories)} catalyst(s) found* — but none passed technical filter.\n\n"
            "_Catalysts exist but technicals not confirmed yet._\n"
            "_Use /news to see the raw catalysts._",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ── Step 4: Summary ───────────────────────
    summary = format_scan_summary([(s, t) for s, t, _ in signal_triples])
    await msg.edit_text(
        header + summary,
        parse_mode=ParseMode.MARKDOWN
    )

    # ── Step 5: Individual signal cards ───────
    for story, tech, signal_msg in signal_triples:
        await asyncio.sleep(0.6)
        await update.message.reply_text(
            signal_msg,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )


# ─────────────────────────────────────────────
# /ticker
# ─────────────────────────────────────────────

async def cmd_ticker(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text(
            "📊 *Usage:* `/ticker <kode saham>`\n\n"
            "*Contoh:*\n"
            "  `/ticker BBRI`\
