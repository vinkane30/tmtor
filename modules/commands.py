"""
modules/commands.py — IDX Story Bot v4
4 commands only: /scan /ticker /news /report
"""

import asyncio
import logging
import re
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from modules.story        import run_story_detection
from modules.regime       import detect_regime
from modules.macro        import fetch_macro_context, get_macro_tag, get_sector_for_ticker
from modules.technical    import (analyse_ticker, analyse_tickers_batch,
                                   is_market_healthy, format_full_analysis)
from modules.signals      import build_and_save_signals, format_scan_summary
from modules.self_improve import (build_weekly_report, format_weekly_report_message,
                                   evaluate_open_signals)
from modules.database     import get_recent_catalysts

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# SAFE FORMATTING
# ─────────────────────────────────────────────

def _rp(v: float) -> str:
    try:
        return f"Rp {float(v):,.0f}"
    except Exception:
        return "Rp 0"

def _safe(v) -> str:
    """Strip markdown-breaking characters from dynamic values."""
    s = str(v) if v is not None else "N/A"
    for ch in ["_", "*", "`", "[", "]"]:
        s = s.replace(ch, "")
    return s

async def _send(update: Update, text: str):
    """Send in chunks with Markdown fallback."""
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        try:
            await update.message.reply_text(
                chunk, parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
        except Exception:
            clean = re.sub(r'[*_`]', '', chunk)
            await update.message.reply_text(
                clean, disable_web_page_preview=True
            )
        await asyncio.sleep(0.2)

def _catalyst_label(ctype: str) -> str:
    return {
        "asset_injection":        "Injeksi Aset",
        "strategic_acquisition":  "Akuisisi/Merger",
        "rights_issue_strategic": "Rights Issue Strategis",
        "government_contract":    "Kontrak Pemerintah",
        "insider_buying":         "Pembelian Insider",
        "buyback":                "Buyback",
        "special_agm":            "RUPSLB",
        "rights_issue":           "Rights Issue",
    }.get(ctype, ctype.replace("_", " ").title())

def _regime_bar(regime: str) -> str:
    return {
        "BULL":    "🟢 BULL    — Trend Following",
        "SIDEWAYS":"🟡 SIDEWAYS — VCP + Selective",
        "BEAR":    "🟠 BEAR    — Mean Reversion + RS",
        "PANIC":   "🔴 PANIC   — Institutional Discount",
        "UNKNOWN": "⚪ UNKNOWN  — Defensive",
    }.get(regime, "⚪ UNKNOWN")


# ─────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*IDX Story Bot v4*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "4 commands only:\n\n"
        "*/scan*\n"
        "Regime detection + catalyst scan +\n"
        "5 day trade + 5 swing setups.\n"
        "Everything in one.\n\n"
        "*/ticker BBRI*\n"
        "4-Point Alpha Report: regime, verdict,\n"
        "catalyst, full trade setup.\n\n"
        "*/news*\n"
        "Macro context (Brent, USD/IDR, sectors)\n"
        "+ corporate actions last 24h.\n\n"
        "*/report*\n"
        "Weekly win rate and signal performance.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Score: 80+ High Conviction | 60+ Watch\n"
        "40+ Speculative | below 40 Avoid\n\n"
        "Bukan rekomendasi investasi. DYOR.",
        parse_mode=ParseMode.MARKDOWN
    )


# ─────────────────────────────────────────────
# /scan — regime + catalyst scan + 10 screens
# ─────────────────────────────────────────────

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "Initialising full scan...\n"
        "Step 1/4: Detecting regime..."
    )

    # ── Step 1: Regime + Macro ────────────────
    regime = detect_regime()
    macro  = fetch_macro_context()

    regime_section = (
        f"*MARKET REGIME*\n"
        f"{_regime_bar(regime.regime)}\n"
        f"IHSG {_rp(regime.ihsg_price)}  "
        f"RSI {regime.ihsg_rsi:.1f}  "
        f"Vol {regime.ihsg_atr_pct:.2f}%\n"
        f"EMA50: {_rp(regime.ihsg_ema50)}  "
        f"EMA200: {_rp(regime.ihsg_ema200)}\n"
    )

    macro_section = (
        f"\n*MACRO*\n"
        f"Brent ${macro.brent_price:.0f} "
        f"({'+' if macro.brent_change_pct >= 0 else ''}"
        f"{macro.brent_change_pct:.1f}%)  "
        f"Gold ${macro.gold_price:.0f}  "
        f"USD/IDR {macro.usd_idr:,.0f}\n"
    )

    if macro.hot_sectors:
        macro_section += f"Hot: {', '.join(macro.hot_sectors)}\n"
    if macro.weak_sectors:
        macro_section += f"Weak: {', '.join(macro.weak_sectors)}\n"
    if macro.narrative:
        macro_section += f"_{_safe(macro.narrative[:200])}_\n"

    await msg.edit_text(
        regime_section + macro_section + "\nStep 2/4: Scanning catalysts...",
        parse_mode=ParseMode.MARKDOWN
    )

    # ── Step 2: Catalyst scan ─────────────────
    stories = await run_story_detection()

    catalyst_section = ""
    signal_triples   = []

    if stories:
        technicals     = analyse_tickers_batch(
            [(s.ticker, s.company_name) for s in stories],
            regime_result=regime
        )
        signal_triples = build_and_save_signals(stories, technicals)

        if signal_triples:
            catalyst_section = (
                f"\n*CATALYST SIGNALS ({len(signal_triples)} found)*\n"
                + format_scan_summary([(s, t) for s, t, _ in signal_triples])
            )
        else:
            catalyst_section = (
                f"\n*CATALYSTS*\n"
                f"{len(stories)} found but none passed {regime.strategy} filter.\n"
            )
    else:
        catalyst_section = "\n*CATALYSTS*\nNo new corporate actions detected.\n"

    await msg.edit_text(
        regime_section + macro_section + catalyst_section +
        "\nStep 3/4: Running screens...",
        parse_mode=ParseMode.MARKDOWN
    )

    # ── Step 3: 10 screens ────────────────────
    try:
        from modules.screens import run_all_screens
        day_trades, swings = run_all_screens()
    except Exception as e:
        logger.error("Screens error: %s", e)
        day_trades, swings = [], []

    # ── Step 4: Assemble full output ──────────
    try:
        await msg.delete()
    except Exception:
        pass

    # Part 1 — Regime + Macro + Catalysts
    part1 = regime_section + macro_section + catalyst_section
    await _send(update, part1)

    # Individual catalyst cards
    for _, _, signal_msg in signal_triples:
        await asyncio.sleep(0.4)
        await _send(update, signal_msg)

    # Part 2 — Day Trade screens
    if day_trades:
        dt_lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"*5 DAY TRADE SETUPS*",
            f"_{datetime.now().strftime('%d %b %Y')}_\n",
        ]
        for i, r in enumerate(day_trades, 1):
            risk_pct = abs((r.entry_price - r.stop_loss) / r.entry_price * 100) if r.entry_price > 0 else 0
            dt_lines += [
                f"*{i}. {r.ticker} — {_safe(r.screen_name)}*",
                f"Price: {_rp(r.price)}  RSI: {r.rsi:.0f}  Vol: {r.vol_ratio:.1f}x",
                f"Timeframe: {_safe(r.timeframe)}",
                f"Trigger: {_safe(r.entry_trigger[:120])}",
                f"Entry: {_rp(r.entry_price)}  Stop: {_rp(r.stop_loss)} (-{risk_pct:.1f}%)",
                f"T1: {_rp(r.target_1)}  T2: {_rp(r.target_2)}  R:R 1:{r.rr_ratio:.1f}",
                f"Why: {_safe(r.why[:180])}",
                f"Risk: {_safe(r.risk_note[:100])}",
                "",
            ]
        await _send(update, "\n".join(dt_lines))

    # Part 3 — Swing screens
    if swings:
        sw_lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"*5 SWING SETUPS*",
            f"_{datetime.now().strftime('%d %b %Y')}_\n",
        ]
        for i, r in enumerate(swings, 1):
            risk_pct = abs((r.entry_price - r.stop_loss) / r.entry_price * 100) if r.entry_price > 0 else 0
            rs_str   = f"  RS: {r.rs_score:.2f}" if r.rs_score else ""
            sw_lines += [
                f"*{i}. {r.ticker} — {_safe(r.screen_name)}*",
                f"Price: {_rp(r.price)}  RSI: {r.rsi:.0f}  Vol: {r.vol_ratio:.1f}x{rs_str}",
                f"Timeframe: {_safe(r.timeframe)}",
                f"Trigger: {_safe(r.entry_trigger[:120])}",
                f"Entry: {_rp(r.entry_price)}  Stop: {_rp(r.stop_loss)} (-{risk_pct:.1f}%)",
                f"T1: {_rp(r.target_1)}  T2: {_rp(r.target_2)}  R:R 1:{r.rr_ratio:.1f}",
                f"Why: {_safe(r.why[:220])}",
                f"Risk: {_safe(r.risk_note[:100])}",
                "",
            ]
        sw_lines.append("/ticker KODE for full institutional analysis")
        await _send(update, "\n".join(sw_lines))

    if not day_trades and not swings:
        await _send(update, "No screens passed filters today. Market conditions tight.")


# ─────────────────────────────────────────────
# /ticker — 4-Point Alpha Report
# ─────────────────────────────────────────────

async def cmd_ticker(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text(
            "Usage: /ticker BBRI\n"
            "Examples: /ticker BBCA  /ticker TLKM  /ticker ADRO"
        )
        return

    ticker = ctx.args[0].upper().strip().replace(".JK", "")

    msg = await update.message.reply_text(
        f"Analysing {ticker}...\n"
        "Regime, RS score, A/D divergence, macro, news..."
    )

    try:
        regime = detect_regime()
        macro  = fetch_macro_context()
        tech   = analyse_ticker(ticker, regime_result=regime)
    except Exception as e:
        await msg.edit_text(f"Error: {str(e)[:150]}")
        return

    if tech is None:
        await msg.edit_text(f"No data for {ticker}. Check ticker code.")
        return

    if tech.is_rejected:
        await msg.edit_text(
            f"Filtered out: {ticker}\n"
            f"Reason: {_safe(tech.rejection_reason)}"
        )
        return

    try:
        await msg.delete()
    except Exception:
        pass

    sector     = get_sector_for_ticker(ticker) or "General"
    macro_tag, macro_explain = get_macro_tag(ticker, macro, tech.rsi)
    verdict_emoji = {"BUY":"🟢","WATCH":"🟡","AVOID":"🔴"}.get(tech.verdict, "⚪")

    # ── Score bar ─────────────────────────────
    filled    = tech.total_score // 10
    score_bar = "█" * filled + "░" * (10 - filled)

    # ── Analyst narrative ─────────────────────
    pct_from_200 = ((tech.ema200 - tech.current_price) / tech.current_price * 100
                    if tech.ema200 > 0 else 0)
    analyst_text = _build_analyst_verdict(
        ticker, tech, macro, sector, macro_tag, macro_explain, pct_from_200
    )

    # ── Trade levels ──────────────────────────
    risk_pct = abs((tech.stop_loss - tech.current_price) / tech.current_price * 100)
    t1_pct   = (tech.t1 / tech.current_price - 1) * 100 if tech.current_price > 0 else 0
    t2_pct   = (tech.t2 / tech.current_price - 1) * 100 if tech.current_price > 0 else 0

    report = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"*{ticker}* | Score *{tech.total_score}/100*",
        f"[{score_bar}]",
        f"{verdict_emoji} *{tech.verdict}* — {_safe(tech.play_type)}",
        "",

        # ── Point 1 ──────────────────────────
        "*1. REGIME HEALTH*",
        f"{_regime_bar(tech.regime)}",
        f"Sector ({sector}): {_safe(macro.sector_advice.get(sector, 'Neutral'))}",
        f"IHSG {_rp(regime.ihsg_price)}  RSI {regime.ihsg_rsi:.1f}",
        "",

        # ── Point 2 ──────────────────────────
        "*2. ANALYST VERDICT*",
        _safe(analyst_text),
        "",

        # ── Point 3 ──────────────────────────
        "*3. CATALYST CHECK*",
    ]

    if tech.rumor_flags:
        report.append(f"Catalyst: {', '.join(tech.rumor_flags)}")
    if tech.conglomerate_flag:
        report.append(_safe(tech.conglomerate_flag))
    if macro_tag:
        report.append(f"Macro: {macro_tag}")
        report.append(_safe(macro_explain[:120]))
    if not tech.rumor_flags and not macro_tag:
        report.append("No active catalyst. Monitor IDX disclosure feed.")
    if tech.recent_news:
        report.append(f"News: {_safe(tech.recent_news[0][:100])}")

    report += [
        "",

        # ── Point 4 ──────────────────────────
        "*4. TRADE SETUP*",
        f"Play: {_safe(tech.play_type)}",
        f"Entry: {_rp(tech.entry_low)} to {_rp(tech.entry_high)}",
        f"Trigger: {_safe(tech.entry_trigger[:120])}",
        f"Stop: {_rp(tech.stop_loss)} (-{risk_pct:.1f}%)",
        f"T1: {_rp(tech.t1)} (+{t1_pct:.1f}%)",
        f"T2: {_rp(tech.t2)} (+{t2_pct:.1f}%)",
        f"R:R 1:{tech.rr_ratio:.1f}  Lots: {tech.lot_size}",
        "",

        "*Technicals*",
        f"Price {_rp(tech.current_price)}  RSI {tech.rsi:.1f}  ADX {tech.adx:.1f}",
        f"EMA8/21: {_rp(tech.ema8)} / {_rp(tech.ema21)}",
        f"EMA50/200: {_rp(tech.ema50)} / {_rp(tech.ema200)}",
        f"BB: {_rp(tech.bb_upper)} / {_rp(tech.bb_mid)} / {_rp(tech.bb_lower)}",
        "RS vs IHSG: " + str(round(tech.rs_score, 2)) + (" (outperforming)" if tech.rs_score > 1.2 else " (underperforming)"),
        f"Volume: {tech.today_volume/1e6:.1f}M ({tech.volume_ratio:.1f}x) {_safe(tech.volume_signal)}",
        f"S1/S2: {_rp(tech.support_1)} / {_rp(tech.support_2)}",
        f"R1/R2: {_rp(tech.resistance_1)} / {_rp(tech.resistance_2)}",
        "",

        "*Fundamentals*",
        f"ROE: {f'{tech.roe*100:.1f}%' if tech.roe else 'N/A'}  "
        f"DER: {f'{tech.der:.2f}x' if tech.der else 'N/A'}  "
        f"PBV: {f'{tech.pbv:.2f}x' if tech.pbv else 'N/A'}",
        "",

        "*Bear Case*",
        _safe(tech.bear_case[:250]),
        "",

        "*Invalidation*",
        _safe(tech.invalidation[:200]),
    ]

    if tech.ad_divergence:
        report += [
            "",
            "SMART MONEY DIVERGENCE",
            _safe(tech.ad_divergence_msg),
        ]

    if tech.at_arb:
        report += ["", f"ARB WARNING: {_safe(tech.arb_warning)}"]

    await _send(update, "\n".join(str(l) for l in report))


def _build_analyst_verdict(ticker, tech, macro, sector,
                            macro_tag, macro_explain, pct_from_200):
    rsi    = tech.rsi
    rs     = tech.rs_score
    regime = tech.regime

    if regime in ("BEAR", "PANIC") and rsi < 35 and sector == "Banking":
        pct_str = str(round(abs(pct_from_200), 1))
        return (ticker + " is a value-trap for retail but a buy-zone for big funds. "
                "Price " + pct_str + "% below EMA200 — "
                "historically 5-8% bounce in 10 trading days. "
                "Foreign flow stabilization is the confirmation signal.")

    if macro_tag == "Geopolitical Hedge":
        brent_str = str(round(macro.brent_price, 0))
        rs_str    = str(round(rs, 2))
        return (ticker + " decouples from IHSG in this environment. "
                "Brent $" + brent_str + "/bbl = sector tailwind. "
                "This plays its own macro. RS " + rs_str + " confirms institutional support.")

    if tech.ad_divergence:
        score_str = str(tech.total_score)
        return ("Smart money accumulating " + ticker + " while retail sells. "
                "A/D Line rising while price falls = institutional footprint. "
                "Highest-conviction signal available. Score: " + score_str + "/100.")

    if tech.is_silent_accum:
        vr_str = str(round(tech.volume_ratio, 1))
        return ("Pre-catalyst positioning in " + ticker + ". "
                "Volume " + vr_str + "x avg, tight range = bandar front-running. "
                "Expect disclosure or news within 1-5 days.")

    if tech.verdict == "BUY":
        score_str = str(tech.total_score)
        rs_str    = str(round(rs, 2))
        adx_str   = str(round(tech.adx, 1))
        return (ticker + " passes regime filters. Score " + score_str + "/100. "
                "RS " + rs_str + " vs IHSG. ADX " + adx_str + " trend confirmed. "
                "Entry on trigger only, do not chase.")

    rsi_str   = str(round(rsi, 0))
    label_str = _safe(tech.score_label)
    return (ticker + ": " + label_str + ". "
            "RSI " + rsi_str + ". "
            "Wait for entry trigger before committing capital.")
