"""
modules/commands.py — IDX Story Bot v2
Regime-aware commands with full institutional analysis
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
from modules.regime       import detect_regime
from modules.technical    import (analyse_ticker, analyse_tickers_batch,
                                   is_market_healthy, format_full_analysis)
from modules.signals      import build_and_save_signals, format_scan_summary
from modules.self_improve import (build_weekly_report, format_weekly_report_message,
                                   evaluate_open_signals)
from modules.database     import get_recent_catalysts

logger = logging.getLogger(__name__)


async def _send(update: Update, text: str):
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        await update.message.reply_text(
            chunk, parse_mode=ParseMode.MARKDOWN,
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

def _regime_header(regime_result) -> str:
    emoji_map = {
        "BULL":    "🟢", "SIDEWAYS": "🟡",
        "BEAR":    "🟠", "PANIC":    "🔴", "UNKNOWN": "⚪"
    }
    strat_map = {
        "TREND_FOLLOW":   "📈 Trend Following — EMA Crossover + VCP",
        "VCP":            "🔄 Volatility Contraction — Breakout Setup",
        "MEAN_REVERT":    "↩️ Mean Reversion — RSI Oversold + Foreign Buy",
        "RS_HUNT":        "🎯 RS Hunting — Outperformers vs IHSG",
        "RS_INSTITUTIONAL":"🏦 Institutional Discount + RS Hunt",
    }
    e = emoji_map.get(regime_result.regime, "⚪")
    s = strat_map.get(regime_result.strategy, regime_result.strategy)
    return (
        f"{e} *IHSG Regime: {regime_result.regime}*\n"
        f"_{regime_result.description}_\n"
        f"Strategy: {s}\n"
        f"IHSG: Rp {regime_result.ihsg_price:,.0f}  "
        f"RSI: {regime_result.ihsg_rsi:.1f}  "
        f"Volatility: {regime_result.ihsg_atr_pct:.2f}%\n"
        f"{'━'*32}\n"
    )


# ─────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Selamat datang di IDX Story Bot v2!*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🤖 _Institutional-grade screener untuk IDX._\n"
        "_Mendeteksi: Regime pasar, Smart Money footprint,_\n"
        "_A/D Divergence, Rumor plays, RS Score vs IHSG._\n\n"
        "📋 *COMMANDS:*\n\n"
        "🔍 /scan\n"
        "   _Full scan: regime-aware catalyst + technical._\n"
        "   _Auto-switch Bull→Momentum, Bear→Mean Revert._\n\n"
        "📊 /ticker `<kode>`\n"
        "   _Full institutional analysis:_\n"
        "   _Score 0-100, regime, RS, A/D, play type,_\n"
        "   _entry/exit/stop, thesis, bear case, rumor flags._\n"
        "   _Contoh: /ticker BBRI_\n\n"
        "📰 /news\n"
        "   _Corporate actions 24 jam (akuisisi, rights,_\n"
        "   _buyback, merger, konglomerat flags)._\n\n"
        "📈 /report\n"
        "   _Weekly: win rate, P&L, signal evaluation._\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 *Score Guide:*\n"
        "  🔥 80-100: High Conviction — Institutional Accumulation\n"
        "  ⚡ 60-79: Moderate — Worth Watching\n"
        "  🟡 40-59: Weak — Speculative Only\n"
        "  ❌ 0-39:  Low Interest — Avoid\n\n"
        "⚠️ _Bukan rekomendasi investasi. DYOR. Manage risk._",
        parse_mode=ParseMode.MARKDOWN
    )


# ─────────────────────────────────────────────
# /scan
# ─────────────────────────────────────────────

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "🔍 *Initiating regime-aware scan…*\n"
        "_Detecting IHSG market regime…_",
        parse_mode=ParseMode.MARKDOWN
    )

    # ── Regime detection ─────────────────────
    regime_result = detect_regime()
    header        = _regime_header(regime_result)

    await msg.edit_text(
        header + "📰 _Scraping Kontan, Bisnis, KPPU, RSS feeds…_",
        parse_mode=ParseMode.MARKDOWN
    )

    # ── Story detection ───────────────────────
    stories = await run_story_detection()

    if not stories:
        # In bear/panic, still scan for RS stocks even without fresh catalysts
        if regime_result.regime in ("BEAR", "PANIC"):
            await msg.edit_text(
                header +
                "📭 _Tidak ada catalyst baru — tapi IHSG bearish._\n\n"
                "🎯 *Switching ke RS Hunt mode.*\n"
                "_Gunakan /ticker pada saham LQ45 untuk cek institutional discount._\n\n"
                "*Top LQ45 untuk di-check:*\n"
                "`/ticker BBCA` `/ticker BBRI` `/ticker TLKM`\n"
                "`/ticker BMRI` `/ticker ASII` `/ticker ADRO`",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await msg.edit_text(
                header + "📭 *Tidak ada corporate action signifikan saat ini.*\n"
                "_Coba lagi nanti atau gunakan /ticker untuk analisis manual._",
                parse_mode=ParseMode.MARKDOWN
            )
        return

    # ── Technical filter (regime-aware) ──────
    await msg.edit_text(
        header + f"📊 _Technical analysis ({regime_result.strategy} mode) "
                 f"on {len(stories)} catalyst(s)…_",
        parse_mode=ParseMode.MARKDOWN
    )
    technicals     = analyse_tickers_batch(
        [(s.ticker, s.company_name) for s in stories],
        regime_result=regime_result
    )
    signal_triples = build_and_save_signals(stories, technicals)

    if not signal_triples:
        await msg.edit_text(
            header +
            f"📰 *{len(stories)} catalyst(s) found* — none passed {regime_result.strategy} filter.\n\n"
            "_Use /news to see raw catalysts, /ticker <kode> for manual check._",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ── Summary ───────────────────────────────
    await msg.edit_text(
        header + format_scan_summary([(s, t) for s, t, _ in signal_triples]),
        parse_mode=ParseMode.MARKDOWN
    )

    # ── Individual cards ──────────────────────
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
            "  `/ticker BBRI` — Bank Rakyat Indonesia\n"
            "  `/ticker TLKM` — Telkom Indonesia\n"
            "  `/ticker ADRO` — Adaro Energy\n"
            "  `/ticker BBCA` — BCA (LQ45 blue chip)\n\n"
            "_Kode IDX 4 huruf, tanpa .JK_",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    ticker = ctx.args[0].upper().strip().replace(".JK", "")

    msg = await update.message.reply_text(
        f"🔍 *Analysing ${ticker}…*\n\n"
        f"  ⏳ _Detecting IHSG regime…_\n"
        f"  ⏳ _Fetching price & volume data…_\n"
        f"  ⏳ _Loading fundamentals…_\n"
        f"  ⏳ _Calculating RS Score vs IHSG…_\n"
        f"  ⏳ _Detecting A/D divergence…_\n"
        f"  ⏳ _Scanning news & rumor flags…_\n"
        f"  ⏳ _Building trade setup…_",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        regime_result = detect_regime()
        tech          = analyse_ticker(ticker, regime_result=regime_result)
    except Exception as e:
        await msg.edit_text(
            f"❌ *Error analysing ${ticker}*\n\n`{str(e)[:200]}`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if tech is None:
        await msg.edit_text(
            f"❌ *No data for `{ticker}`*\n_Cek kode saham (4 huruf IDX, contoh: BBRI)_",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if tech.is_rejected:
        await msg.edit_text(
            f"⛔ *${ticker} — Filtered Out*\n\n"
            f"*Reason:* _{tech.rejection_reason}_\n\n"
            f"_Saham tidak memenuhi universe filter._",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        await msg.delete()
    except Exception:
        pass

    # ── Full analysis card ────────────────────
    full_text = format_full_analysis(tech)
    for chunk in [full_text[i:i+4000] for i in range(0, len(full_text), 4000)]:
        await asyncio.sleep(0.3)
        await update.message.reply_text(
            chunk,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )

    # ── Quick summary verdict ─────────────────
    verdict_emoji = {"BUY": "🟢", "WATCH": "🟡", "AVOID": "🔴"}.get(tech.verdict, "⚪")
    rp = lambda v: f"Rp {v:,.0f}"

    summary = [
        f"{'━'*32}",
        f"{verdict_emoji} *${ticker} — {tech.verdict}*  |  Score: *{tech.total_score}/100*",
        f"_{tech.score_label}_",
        f"📌 Play: *{tech.play_type}*",
        f"",
        f"💰 Price: *{rp(tech.current_price)}*",
        f"🎯 Entry: {rp(tech.entry_low)} – {rp(tech.entry_high)}",
        f"🛑 Stop: {rp(tech.stop_loss)} (–{abs((tech.stop_loss-tech.current_price)/tech.current_price*100):.1f}%)",
        f"📍 T1: {rp(tech.t1)}  T2: {rp(tech.t2)}  T3: {rp(tech.t3)}",
        f"⚖️ R:R  1:{tech.rr_ratio:.1f}",
        f"📦 Lot size: {tech.lot_size} lots",
    ]

    if tech.ad_divergence:
        summary += [f"", f"🚨 *SMART MONEY DIVERGENCE DETECTED*"]
    if tech.rumor_flags:
        summary += [f"🔥 Catalyst: {', '.join(tech.rumor_flags)}"]

    await update.message.reply_text(
        "\n".join(summary),
        parse_mode=ParseMode.MARKDOWN
    )


# ─────────────────────────────────────────────
# /news
# ─────────────────────────────────────────────

async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "📰 _Loading corporate actions…_",
        parse_mode=ParseMode.MARKDOWN
    )

    rows = get_recent_catalysts(hours=24)

    if not rows:
        await msg.edit_text(
            "📭 *Tidak ada corporate action dalam 24 jam terakhir.*\n\n"
            "_Coba /scan untuk trigger deteksi manual._",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    rows.sort(key=lambda r: r.get("score", 0), reverse=True)
    regime_result = detect_regime()

    lines = [
        "📰 *Corporate Actions — 24 Jam Terakhir*",
        f"_{len(rows)} event(s)  |  Regime: {regime_result.regime}_",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
    ]

    for r in rows[:12]:
        score    = r.get("score", 0)
        dot      = "🔴" if score >= 9 else ("🟡" if score >= 7 else "🟢")
        ctype    = _catalyst_label(r.get("catalyst_type", ""))
        ticker   = r.get("ticker", "?")
        headline = r.get("headline", "")[:90]
        url      = r.get("source_url", "")
        lines.append(
            f"{dot} *${ticker}*  {ctype}  Score: {score}/10\n"
            f"   _{headline}_\n"
            f"   {url}\n"
        )

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "_/ticker <kode> untuk full trade setup_",
    ]

    try:
        await msg.delete()
    except Exception:
        pass

    await _send(update, "\n".join(lines))


# ─────────────────────────────────────────────
# /report
# ─────────────────────────────────────────────

async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "📊 *Generating weekly report…*\n"
        "_Evaluating signals, calculating win rate…_",
        parse_mode=ParseMode.MARKDOWN
    )
    try:
        evaluate_open_signals()
        report      = build_weekly_report()
        report_text = format_weekly_report_message(report)
        try:
            await msg.delete()
        except Exception:
            pass
        await _send(update, report_text)
    except Exception as e:
        await msg.edit_text(
            f"❌ *Report failed*\n\n`{str(e)[:200]}`",
            parse_mode=ParseMode.MARKDOWN
        )
        logger.error("cmd_report: %s", e, exc_info=True)
