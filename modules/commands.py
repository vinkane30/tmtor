"""
modules/commands.py — 4 commands only
/scan    full pipeline (market health + story + technical)
/ticker  on-demand single stock
/news    corporate action feed (story + insider + upcoming events merged)
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
from modules.story       import run_story_detection
from modules.technical   import analyse_ticker, analyse_tickers_batch, is_market_healthy
from modules.signals     import build_and_save_signals, format_scan_summary
from modules.self_improve import build_weekly_report, format_weekly_report_message, evaluate_open_signals
from modules.database    import get_recent_catalysts

logger = logging.getLogger(__name__)


async def _send(update: Update, text: str):
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN,
                                        disable_web_page_preview=True)

def _catalyst_label(ctype: str) -> str:
    return {
        "asset_injection":       "Injeksi Aset",
        "strategic_acquisition": "Akuisisi",
        "rights_issue_strategic":"Rights Issue + Investor Strategis",
        "government_contract":   "Kontrak Pemerintah",
        "insider_buying":        "Pembelian Insider",
        "buyback":               "Buyback",
        "special_agm":           "RUPSLB",
    }.get(ctype, ctype.replace("_", " ").title())


# ── /scan ─────────────────────────────────────

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Scanning…")

    healthy, market_msg = is_market_healthy()
    header = f"{'✅' if healthy else '⛔'} _{market_msg}_\n{'─'*32}\n\n"

    if not healthy:
        await msg.edit_text(
            header + "Scan paused — tunggu IHSG pulih di atas EMA50.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    await msg.edit_text(header + "📰 Scraping IDX, KPPU, news…", parse_mode=ParseMode.MARKDOWN)
    stories = await run_story_detection()

    if not stories:
        await msg.edit_text(header + "❌ Tidak ada corporate action signifikan saat ini.",
                            parse_mode=ParseMode.MARKDOWN)
        return

    await msg.edit_text(header + f"📊 Technical: {len(stories)} ticker…", parse_mode=ParseMode.MARKDOWN)
    technicals     = analyse_tickers_batch([(s.ticker, s.company_name) for s in stories])
    signal_triples = build_and_save_signals(stories, technicals)

    await msg.edit_text(header + format_scan_summary([(s, t) for s, t, _ in signal_triples]),
                        parse_mode=ParseMode.MARKDOWN)

    for _, _, signal_msg in signal_triples:
        await asyncio.sleep(0.5)
        await update.message.reply_text(signal_msg, parse_mode=ParseMode.MARKDOWN,
                                        disable_web_page_preview=True)


# ── /ticker XXXX ─────────────────────────────

async def cmd_ticker(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: `/ticker BBRI`", parse_mode=ParseMode.MARKDOWN)
        return

    ticker = ctx.args[0].upper().strip()
    msg    = await update.message.reply_text(f"🔍 *${ticker}*…", parse_mode=ParseMode.MARKDOWN)
    tech   = analyse_ticker(ticker)

    if tech is None:
        await msg.edit_text(f"❌ No data for `{ticker}`.")
        return
    if tech.is_rejected:
        await msg.edit_text(f"⚠️ *${ticker}* — _{tech.rejection_reason}_",
                            parse_mode=ParseMode.MARKDOWN)
        return

    rp   = lambda v: f"Rp {v:,.0f}"
    text = (
        f"📊 *${ticker}*  {tech.tech_score}/7 conditions\n"
        f"Price {rp(tech.current_price)}  RSI {tech.rsi:.0f}  "
        f"Vol {tech.today_volume/1e6:.1f}M vs {tech.avg_volume_20d/1e6:.1f}M avg\n\n"
        + "\n".join(f"  ✅ {c}" for c in tech.conditions_met)
        + ("\n" + "\n".join(f"  ✗ {c}" for c in tech.conditions_failed[:3]))
    )

    if tech.passed:
        stop_pct = (tech.current_price - tech.stop_loss) / tech.current_price * 100
        text += (
            f"\n\n💼 Entry {rp(tech.entry_low)}–{rp(tech.entry_high)}"
            f"  Stop {rp(tech.stop_loss)} (–{stop_pct:.1f}%)\n"
            f"  T1 {rp(tech.t1)}  T2 {rp(tech.t2)}  T3 {rp(tech.t3)}  R:R 1:{tech.rr_ratio:.1f}"
        )
    else:
        text += f"\n\n_Need {config.MIN_TECHNICAL_COUNT} conditions — only {tech.tech_score} met._"

    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)


# ── /news ─────────────────────────────────────

async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = get_recent_catalysts(hours=24)

    if not rows:
        await update.message.reply_text("📭 Tidak ada corporate action dalam 24 jam terakhir.")
        return

    rows.sort(key=lambda r: r.get("score", 0), reverse=True)
    lines = ["📰 *Corporate Actions — 24h*\n"]
    for r in rows[:12]:
        s = r.get("score", 0)
        dot = "🔴" if s >= 9 else ("🟡" if s >= 7 else "🟢")
        lines.append(
            f"{dot} *${r['ticker']}*  {_catalyst_label(r.get('catalyst_type',''))}  {s}/10\n"
            f"   _{r['headline'][:90]}_\n"
            f"   {r.get('source_url','')}\n"
        )
    await _send(update, "\n".join(lines))


# ── /report ───────────────────────────────────

async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("📊 Generating report…")
    evaluate_open_signals()
    report = build_weekly_report()
    await msg.edit_text(format_weekly_report_message(report), parse_mode=ParseMode.MARKDOWN)
