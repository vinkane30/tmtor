"""
modules/commands.py — IDX Story Bot v4
4 commands: /start /scan /ticker /news /report
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
from modules.technical    import analyse_ticker, analyse_tickers_batch, is_market_healthy, format_full_analysis
from modules.signals      import build_and_save_signals, format_scan_summary
from modules.self_improve import build_weekly_report, format_weekly_report_message, evaluate_open_signals
from modules.database     import get_recent_catalysts

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _rp(v):
    try:
        return "Rp " + "{:,.0f}".format(float(v))
    except Exception:
        return "Rp 0"

def _safe(v):
    s = str(v) if v is not None else "N/A"
    for ch in ["_", "*", "`", "[", "]"]:
        s = s.replace(ch, "")
    return s

async def _send(update, text):
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        try:
            await update.message.reply_text(
                chunk,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
        except Exception:
            clean = re.sub(r'[*_`]', '', chunk)
            await update.message.reply_text(
                clean,
                disable_web_page_preview=True
            )
        await asyncio.sleep(0.2)

def _catalyst_label(ctype):
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

def _regime_bar(regime):
    return {
        "BULL":    "🟢 BULL     — Trend Following",
        "SIDEWAYS":"🟡 SIDEWAYS — VCP + Selective",
        "BEAR":    "🟠 BEAR     — Mean Reversion + RS",
        "PANIC":   "🔴 PANIC    — Institutional Discount",
        "UNKNOWN": "⚪ UNKNOWN  — Defensive",
    }.get(regime, "⚪ UNKNOWN")


# ─────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*IDX Story Bot v4*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "4 commands:\n\n"
        "*/scan*\n"
        "Regime detection, catalyst scan,\n"
        "5 day trade + 5 swing setups.\n\n"
        "*/ticker BBRI*\n"
        "4-Point Alpha Report: regime, verdict,\n"
        "catalyst, full trade setup.\n\n"
        "*/news*\n"
        "Macro context (Brent, USD/IDR, sectors)\n"
        "and corporate actions last 24h.\n\n"
        "*/report*\n"
        "Weekly win rate and signal performance.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Score: 80+ High Conviction\n"
        "60+ Watch | 40+ Speculative | below 40 Avoid\n\n"
        "Bukan rekomendasi investasi. DYOR.",
        parse_mode=ParseMode.MARKDOWN
    )


# ─────────────────────────────────────────────
# /scan — regime + catalyst + 10 screens
# ─────────────────────────────────────────────

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "Initialising full scan...\nStep 1/4: Detecting regime..."
    )

    # Step 1: Regime + Macro
    regime = detect_regime()
    macro  = fetch_macro_context()

    regime_section = (
        "*MARKET REGIME*\n"
        + _regime_bar(regime.regime) + "\n"
        + "IHSG " + _rp(regime.ihsg_price)
        + "  RSI " + str(round(regime.ihsg_rsi, 1))
        + "  Vol " + str(round(regime.ihsg_atr_pct, 2)) + "%\n"
        + "EMA50: " + _rp(regime.ihsg_ema50)
        + "  EMA200: " + _rp(regime.ihsg_ema200) + "\n"
    )

    brent_sign = "+" if macro.brent_change_pct >= 0 else ""
    macro_section = (
        "\n*MACRO*\n"
        + "Brent $" + str(round(macro.brent_price))
        + " (" + brent_sign + str(round(macro.brent_change_pct, 1)) + "%)"
        + "  Gold $" + str(round(macro.gold_price))
        + "  USD/IDR " + "{:,.0f}".format(macro.usd_idr) + "\n"
    )

    if macro.hot_sectors:
        macro_section += "Hot: " + ", ".join(macro.hot_sectors) + "\n"
    if macro.weak_sectors:
        macro_section += "Weak: " + ", ".join(macro.weak_sectors) + "\n"
    if macro.narrative:
        macro_section += "_" + _safe(macro.narrative[:200]) + "_\n"

    await msg.edit_text(
        regime_section + macro_section + "\nStep 2/4: Scanning catalysts...",
        parse_mode=ParseMode.MARKDOWN
    )

    # Step 2: Catalyst scan
    stories = await run_story_detection()
    catalyst_section = ""
    signal_triples   = []

    if stories:
        technicals = analyse_tickers_batch(
            [(s.ticker, s.company_name) for s in stories],
            regime_result=regime
        )
        signal_triples = build_and_save_signals(stories, technicals)
        if signal_triples:
            catalyst_section = (
                "\n*CATALYST SIGNALS (" + str(len(signal_triples)) + " found)*\n"
                + format_scan_summary([(s, t) for s, t, _ in signal_triples])
            )
        else:
            catalyst_section = (
                "\n*CATALYSTS*\n"
                + str(len(stories)) + " found but none passed "
                + regime.strategy + " filter.\n"
            )
    else:
        if regime.regime in ("BEAR", "PANIC"):
            catalyst_section = (
                "\n*CATALYSTS*\n"
                "No new catalysts.\n"
                "Regime: Mean Reversion + RS Hunt mode.\n"
                "Check LQ45: /ticker BBCA /ticker BBRI /ticker TLKM\n"
            )
        else:
            catalyst_section = "\n*CATALYSTS*\nNo new corporate actions detected.\n"

    await msg.edit_text(
        regime_section + macro_section + catalyst_section + "\nStep 3/4: Running screens...",
        parse_mode=ParseMode.MARKDOWN
    )

    # Step 3: Screens
    day_trades, swings = [], []
    try:
        from modules.screens import run_all_screens
        day_trades, swings = run_all_screens()
    except Exception as e:
        logger.error("Screens error: %s", e)

    # Step 4: Send all
    try:
        await msg.delete()
    except Exception:
        pass

    await _send(update, regime_section + macro_section + catalyst_section)

    for _, _, signal_msg in signal_triples:
        await asyncio.sleep(0.4)
        await _send(update, signal_msg)

    if day_trades:
        dt_lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "*5 DAY TRADE SETUPS*",
            "_" + datetime.now().strftime("%d %b %Y") + "_\n",
        ]
        for i, r in enumerate(day_trades, 1):
            risk_pct = abs((r.entry_price - r.stop_loss) / r.entry_price * 100) if r.entry_price > 0 else 0
            dt_lines += [
                "*" + str(i) + ". " + r.ticker + " — " + _safe(r.screen_name) + "*",
                "Price: " + _rp(r.price) + "  RSI: " + str(round(r.rsi)) + "  Vol: " + str(round(r.vol_ratio, 1)) + "x",
                "Timeframe: " + _safe(r.timeframe),
                "Trigger: " + _safe(r.entry_trigger[:120]),
                "Entry: " + _rp(r.entry_price) + "  Stop: " + _rp(r.stop_loss) + " (-" + str(round(risk_pct, 1)) + "%)",
                "T1: " + _rp(r.target_1) + "  T2: " + _rp(r.target_2) + "  R:R 1:" + str(round(r.rr_ratio, 1)),
                "Why: " + _safe(r.why[:180]),
                "Risk: " + _safe(r.risk_note[:100]),
                "",
            ]
        await _send(update, "\n".join(dt_lines))

    if swings:
        sw_lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "*5 SWING SETUPS*",
            "_" + datetime.now().strftime("%d %b %Y") + "_\n",
        ]
        for i, r in enumerate(swings, 1):
            risk_pct = abs((r.entry_price - r.stop_loss) / r.entry_price * 100) if r.entry_price > 0 else 0
            rs_str   = "  RS: " + str(round(r.rs_score, 2)) if r.rs_score else ""
            sw_lines += [
                "*" + str(i) + ". " + r.ticker + " — " + _safe(r.screen_name) + "*",
                "Price: " + _rp(r.price) + "  RSI: " + str(round(r.rsi)) + "  Vol: " + str(round(r.vol_ratio, 1)) + "x" + rs_str,
                "Timeframe: " + _safe(r.timeframe),
                "Trigger: " + _safe(r.entry_trigger[:120]),
                "Entry: " + _rp(r.entry_price) + "  Stop: " + _rp(r.stop_loss) + " (-" + str(round(risk_pct, 1)) + "%)",
                "T1: " + _rp(r.target_1) + "  T2: " + _rp(r.target_2) + "  R:R 1:" + str(round(r.rr_ratio, 1)),
                "Why: " + _safe(r.why[:220]),
                "Risk: " + _safe(r.risk_note[:100]),
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
        "Analysing " + ticker + "...\n"
        "Regime, RS score, A/D divergence, macro, news..."
    )

    try:
        regime = detect_regime()
        macro  = fetch_macro_context()
        tech   = analyse_ticker(ticker, regime_result=regime)
    except Exception as e:
        await msg.edit_text("Error: " + str(e)[:150])
        return

    if tech is None:
        await msg.edit_text("No data for " + ticker + ". Check ticker code.")
        return

    if tech.is_rejected:
        await msg.edit_text(
            "Filtered out: " + ticker + "\n"
            "Reason: " + _safe(tech.rejection_reason)
        )
        return

    try:
        await msg.delete()
    except Exception:
        pass

    sector    = get_sector_for_ticker(ticker) or "General"
    macro_tag, macro_explain = get_macro_tag(ticker, macro, tech.rsi)
    verdict_emoji = {"BUY": "🟢", "WATCH": "🟡", "AVOID": "🔴"}.get(tech.verdict, "⚪")

    filled    = tech.total_score // 10
    score_bar = "█" * filled + "░" * (10 - filled)

    pct_from_200 = ((tech.ema200 - tech.current_price) / tech.current_price * 100
                    if tech.ema200 > 0 else 0)
    analyst_text = _build_analyst_verdict(
        ticker, tech, macro, sector, macro_tag, macro_explain, pct_from_200
    )

    risk_pct = abs((tech.stop_loss - tech.current_price) / tech.current_price * 100)
    t1_pct   = (tech.t1 / tech.current_price - 1) * 100 if tech.current_price > 0 else 0
    t2_pct   = (tech.t2 / tech.current_price - 1) * 100 if tech.current_price > 0 else 0

    rs_label = "outperforming" if tech.rs_score > 1.2 else "underperforming"

    report = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "*" + ticker + "* | Score *" + str(tech.total_score) + "/100*",
        "[" + score_bar + "]",
        verdict_emoji + " *" + tech.verdict + "* — " + _safe(tech.play_type),
        "",
        "*1. REGIME HEALTH*",
        _regime_bar(tech.regime),
        "Sector (" + sector + "): " + _safe(macro.sector_advice.get(sector, "Neutral")),
        "IHSG " + _rp(regime.ihsg_price) + "  RSI " + str(round(regime.ihsg_rsi, 1)),
        "",
        "*2. ANALYST VERDICT*",
        _safe(analyst_text),
        "",
        "*3. CATALYST CHECK*",
    ]

    if tech.rumor_flags:
        report.append("Catalyst: " + ", ".join(tech.rumor_flags))
    if tech.conglomerate_flag:
        report.append(_safe(tech.conglomerate_flag))
    if macro_tag:
        report.append("Macro: " + macro_tag)
        report.append(_safe(macro_explain[:120]))
    if not tech.rumor_flags and not macro_tag:
        report.append("No active catalyst. Monitor IDX disclosure feed.")
    if tech.recent_news:
        report.append("News: " + _safe(tech.recent_news[0][:100]))

    # ── Spring / Wyckoff block ────────────────────────────────────────
    spring_emoji = "🔥" if tech.spring_score >= 8 else ("👀" if tech.spring_score >= 5 else "➖")
    report += [
        "",
        "*3b. SPRING / ACCUMULATION RADAR*",
        spring_emoji + " *" + tech.spring_label + "* — Score " + str(tech.spring_score) + "/10",
        "  Stopping vol : " + ("✅ " + _safe(tech.stopping_volume_detail[:80]) if tech.stopping_volume else "❌ Not detected"),
        "  OBV diverge  : " + ("✅ " + _safe(tech.obv_detail[:80]) if tech.obv_diverging else "❌ Not diverging"),
        "  A/D line     : " + ("✅ " + _safe(tech.ad_divergence_msg[:60]) if tech.ad_divergence else "❌ No divergence"),
        "  BB squeeze   : " + ("✅ " + str(round(tech.bb_bandwidth_pct, 1)) + "% bandwidth" if tech.bb_squeeze_spring else "❌ No squeeze"),
        "  At support   : " + ("✅ " + str(round(abs(tech.support_proximity_pct), 1)) + "% from S1 " + _rp(tech.support_1) if tech.near_major_support else "❌ Not at major support"),
        "  RS vs IHSG   : " + ("✅ " + str(round(tech.rs_score, 2)) + " (outperforming during dip)" if tech.rs_score > 1.0 else "❌ " + str(round(tech.rs_score, 2)) + " (underperforming)"),
    ]

    report += [
        "",
        "*4. TRADE SETUP*",
        "Play: " + _safe(tech.play_type),
        "Entry: " + _rp(tech.entry_low) + " to " + _rp(tech.entry_high),
        "Trigger: " + _safe(tech.entry_trigger[:180]),
        "Stop: " + _rp(tech.stop_loss) + " (-" + str(round(risk_pct, 1)) + "%)",
        "T1: " + _rp(tech.t1) + " (+" + str(round(t1_pct, 1)) + "%)",
        "T2: " + _rp(tech.t2) + " (+" + str(round(t2_pct, 1)) + "%)",
        "R:R 1:" + str(round(tech.rr_ratio, 1)) + "  Lots: " + str(tech.lot_size),
        "Max loss (10M pos): " + _rp(max(0, (tech.current_price - tech.stop_loss) * tech.lot_size * 100)),
        "",
        "*Technicals*",
        "Price " + _rp(tech.current_price) + "  RSI " + str(round(tech.rsi, 1)) + "  ADX " + str(round(tech.adx, 1)),
        "EMA8/21: " + _rp(tech.ema8) + " / " + _rp(tech.ema21),
        "EMA50/200: " + _rp(tech.ema50) + " / " + _rp(tech.ema200),
        "BB: " + _rp(tech.bb_upper) + " / " + _rp(tech.bb_mid) + " / " + _rp(tech.bb_lower),
        "RS vs IHSG: " + str(round(tech.rs_score, 2)) + " (" + rs_label + ")",
        "Volume: " + str(round(tech.today_volume / 1e6, 1)) + "M (" + str(round(tech.volume_ratio, 1)) + "x) " + _safe(tech.volume_signal),
        "S1/S2: " + _rp(tech.support_1) + " / " + _rp(tech.support_2),
        "R1/R2: " + _rp(tech.resistance_1) + " / " + _rp(tech.resistance_2),
        "",
        "*Fundamentals*",
        "ROE: " + (str(round(tech.roe * 100, 1)) + "%" if tech.roe else "N/A")
        + "  DER: " + (str(round(tech.der, 2)) + "x" if tech.der else "N/A")
        + "  PBV: " + (str(round(tech.pbv, 2)) + "x" if tech.pbv else "N/A"),
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
        report += ["", "ARB WARNING: " + _safe(tech.arb_warning)]

    await _send(update, "\n".join(str(l) for l in report))


def _build_analyst_verdict(ticker, tech, macro, sector,
                            macro_tag, macro_explain, pct_from_200):
    rsi    = tech.rsi
    rs     = tech.rs_score
    regime = tech.regime

    if regime in ("BEAR", "PANIC") and rsi < 35 and sector == "Banking":
        pct_str = str(round(abs(pct_from_200), 1))
        return (ticker + " is a value-trap for retail but a buy-zone for big funds. "
                "Price " + pct_str + "% below EMA200. "
                "Historically 5-8% bounce in 10 trading days. "
                "Foreign flow stabilization is the confirmation signal.")

    if macro_tag == "Geopolitical Hedge":
        brent_str = str(round(macro.brent_price))
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

    rsi_str   = str(round(rsi))
    label_str = _safe(tech.score_label)
    return (ticker + ": " + label_str + ". "
            "RSI " + rsi_str + ". "
            "Wait for entry trigger before committing capital.")


# ─────────────────────────────────────────────
# /news — macro + corporate actions merged
# ─────────────────────────────────────────────

async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "Preparing institutional pre-market briefing...\n"
        "Fetching global, macro, foreign flow, sector data..."
    )

    # ── Data fetch ──────────────────────────────────────────
    regime = detect_regime()
    macro  = fetch_macro_context()
    rows   = get_recent_catalysts(hours=24)

    # Global prices via yfinance
    import yfinance as yf

    def _pct(ticker, period="2d"):
        try:
            df = yf.Ticker(ticker).history(period=period, interval="1d")
            if len(df) >= 2:
                p  = float(df["Close"].iloc[-1])
                p0 = float(df["Close"].iloc[-2])
                return p, round((p / p0 - 1) * 100, 2)
            return None, None
        except Exception:
            return None, None

    sp500_p,  sp500_c  = _pct("^GSPC")
    nasdaq_p, nasdaq_c = _pct("^IXIC")
    eido_p,   eido_c   = _pct("EIDO")
    bond_p,   _        = _pct("INDOGB10.JK")  # fallback — may return None
    coal_p,   coal_c   = _pct("MTF=F")        # Newcastle coal futures
    nickel_p, nickel_c = _pct("NI=F")

    def _arrow(c):
        if c is None: return "─"
        return "▲" if c > 0 else ("▼" if c < 0 else "─")

    def _fmt(p, c, prefix=""):
        if p is None: return "N/A"
        sign = "+" if c >= 0 else ""
        return prefix + "{:,.2f}".format(p) + " (" + sign + str(c) + "%)"

    # Bond yield flag
    bond_flag = ""
    if bond_p and bond_p > 7.0:
        bond_flag = "🚨 ABOVE 7% — BANKING LIQUIDITY RISK"
    elif bond_p and bond_p > 6.8:
        bond_flag = "⚠️ Approaching danger zone"

    # Regime label
    regime_label = {
        "BULL":    "🟢 RISK-ON  — IHSG above EMA200. Full position sizing.",
        "SIDEWAYS":"🟡 NEUTRAL  — IHSG between EMA50/200. Selective only.",
        "BEAR":    "🟠 RISK-OFF — IHSG below EMA200. 30-40% sizing only.",
        "PANIC":   "🔴 PANIC    — IHSG well below EMA200. Cash + hedges only.",
        "UNKNOWN": "⚪ UNKNOWN  — Defensive until confirmed.",
    }.get(regime.regime, "⚪ UNKNOWN")

    # EIDO sentiment
    eido_sentiment = ""
    if eido_c is not None:
        if eido_c < -1.5:
            eido_sentiment = "Foreigners pricing in more IDX downside."
        elif eido_c > 1.0:
            eido_sentiment = "Foreign institutional appetite returning."
        else:
            eido_sentiment = "Neutral foreign signal — watch open."

    # Commodity interpretation
    def _commodity_note(name, chg):
        if chg is None: return ""
        if name == "Brent" and chg > 2:
            return " — ADRO ITMG PTBA tailwind"
        if name == "Coal" and chg > 1:
            return " — Coal names accelerating"
        if name == "Nickel" and chg > 1.5:
            return " — INCO MDKA watch"
        return ""

    brent_note  = _commodity_note("Brent", macro.brent_change_pct)
    coal_note   = _commodity_note("Coal", coal_c)
    nickel_note = _commodity_note("Nickel", nickel_c)

    brent_sign = "+" if macro.brent_change_pct >= 0 else ""

    # Alpha sector
    alpha_sector = ""
    if macro.hot_sectors:
        alpha_sector = ", ".join(macro.hot_sectors)
    elif macro.sector_returns:
        best = max(macro.sector_returns, key=macro.sector_returns.get)
        alpha_sector = best + " " + str(macro.sector_returns[best]) + "%"

    # Corporate actions
    ca_lines = []
    if rows:
        rows.sort(key=lambda r: r.get("score", 0), reverse=True)
        for r in rows[:6]:
            score   = r.get("score", 0)
            dot     = "🔴" if score >= 9 else ("🟡" if score >= 7 else "🟢")
            ctype   = _catalyst_label(r.get("catalyst_type", ""))
            t       = r.get("ticker", "?")
            headline = _safe(r.get("headline", "")[:80])
            url     = r.get("source_url", "")
            ca_lines += [
                dot + " *" + t + "* — " + ctype,
                "   " + headline,
                "   " + url,
                "",
            ]
    else:
        ca_lines = [
            "No corporate actions detected in last 24h.",
            "Run /scan to trigger full detection.",
        ]

    # ── Build report ────────────────────────────────────────
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "*PRE-MARKET BRIEFING*",
        "_" + datetime.now().strftime("%A, %d %B %Y | %H:%M WIB") + "_",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",

        # ── LAYER 1: MACRO TEMPERATURE ──────────────────────
        "*LAYER 1 — MACRO TEMPERATURE*",
        "",
        "*Global Hook (Overnight)*",
        _arrow(sp500_c)  + " S&P500:  " + _fmt(sp500_p, sp500_c),
        _arrow(nasdaq_c) + " Nasdaq:  " + _fmt(nasdaq_p, nasdaq_c),
        _arrow(eido_c)   + " EIDO:    " + _fmt(eido_p, eido_c, "$"),
        "_" + _safe(eido_sentiment) + "_",
        "",
        "*Currency & Yields*",
        "USD/IDR:  " + "{:,.0f}".format(macro.usd_idr)
            + (" ⚠️ Rupiah pressure — USD earners benefit" if macro.usd_idr > 16000 else ""),
        "10Y SBN:  " + ("{:.2f}%".format(bond_p) if bond_p else "N/A")
            + (" " + bond_flag if bond_flag else ""),
        "Gold:     $" + str(round(macro.gold_price)),
        "",
        "*Commodity Check — The Heavyweights*",
        _arrow(macro.brent_change_pct) + " Brent:  $" + str(round(macro.brent_price))
            + " (" + brent_sign + str(round(macro.brent_change_pct, 1)) + "%)" + brent_note,
        _arrow(coal_c)   + " Coal:   " + _fmt(coal_p, coal_c, "$") + coal_note,
        _arrow(nickel_c) + " Nickel: " + _fmt(nickel_p, nickel_c, "$") + nickel_note,
        "",

        # ── LAYER 2: IDX PULSE ──────────────────────────────
        "*LAYER 2 — IDX PULSE*",
        "",
        "*Regime Status*",
        regime_label,
        "IHSG: " + _rp(regime.ihsg_price)
            + "  EMA50: " + _rp(regime.ihsg_ema50)
            + "  EMA200: " + _rp(regime.ihsg_ema200),
        "",
        "*Foreign Flow — Big 4 Banks*",
        "BBCA / BBRI / BMRI / BBNI",
        "_Check net foreign buy/sell on IDX or RTI before open._",
        "_If foreigners net selling Big 4 > Rp500B = do not buy banks today._",
        "",
        "*Macro Narrative*",
        "_" + _safe(macro.narrative[:350]) + "_",
        "",

        # ── LAYER 3: SECTOR & SPECIAL SITUATIONS ────────────
        "*LAYER 3 — SECTOR & SPECIAL SITUATIONS*",
        "",
        "*Alpha Sector (Relative Strength)*",
        "RS Leader: " + (_safe(alpha_sector) if alpha_sector else "No clear leader — defensive mode"),
        "",
        "*Sector Rotation*",
    ]

    for sector, advice in list(macro.sector_advice.items())[:8]:
        ret   = macro.sector_returns.get(sector, 0)
        arrow = "▲" if ret > 0 else ("▼" if ret < 0 else "─")
        lines.append(arrow + " " + sector + ": " + _safe(advice[:55]))

    lines += [
        "",
        "*Corporate Actions — Last 24H*",
        "_" + str(len(rows)) + " events_",
        "",
    ]
    lines += ca_lines

    lines += [
        # ── LAYER 4: PM TRADE DECISIONS ─────────────────────
        "*LAYER 4 — TODAY'S TRADE DECISIONS*",
        "",
    ]

    # Defensive hedge
    if macro.geo_risk and macro.brent_price > 80:
        lines += [
            "*Defensive Hedge — Geopolitical*",
            "ADRO / ITMG / PTBA — Energy names decouple from IHSG.",
            "Brent $" + str(round(macro.brent_price)) + " = structural tailwind.",
            "Buy dips to EMA50. Hard exit on ceasefire news.",
            "",
        ]
    elif macro.usd_idr > 16000:
        lines += [
            "*Defensive Hedge — Rupiah Weakness*",
            "USD/IDR " + "{:,.0f}".format(macro.usd_idr) + " — USD earner commodities are your hedge.",
            "ADRO, ITMG, INCO benefit from weak rupiah.",
            "",
        ]
    else:
        lines += [
            "*Defensive Hedge*",
            "No dominant single hedge today. Raise cash in BEAR regime.",
            "",
        ]

    # Momentum play
    if macro.hot_sectors:
        lines += [
            "*Momentum Play*",
            "Sector RS leader: " + _safe(", ".join(macro.hot_sectors[:2])),
            "Screen for 20-day breakout on volume 2x+ in these sectors.",
            "Use /scan for specific tickers.",
            "",
        ]

    # Mean reversion
    lines += [
        "*Mean Reversion — Blue Chip Watch*",
        "BBRI / BBCA / BMRI at multi-year lows.",
        "Entry only when: RSI below 30 AND foreign flow stabilizes (net buy day).",
        "Do NOT catch a falling knife. Wait for the confirmation candle.",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "/ticker KODE — Full institutional analysis",
        "/scan — Live catalyst + 10 trade setups",
        "_Bukan rekomendasi investasi. DYOR._",
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
    msg = await update.message.reply_text("Generating weekly report...")
    try:
        evaluate_open_signals()
        report = build_weekly_report()
        text   = format_weekly_report_message(report)
        try:
            await msg.delete()
        except Exception:
            pass
        await _send(update, text)
    except Exception as e:
        await msg.edit_text("Report error: " + str(e)[:200])
        logger.error("cmd_report: %s", e, exc_info=True)
