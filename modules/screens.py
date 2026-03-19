"""
modules/screens.py — Systematic Screen Runner
Runs 5 day-trade screens + 5 swing screens across the IDX universe.
Uses the existing analyse_ticker() engine — no new data fetching.

Called by cmd_scan: day_trades, swings = run_all_screens()
"""

import logging
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from modules.technical import analyse_ticker, TechnicalResult
from modules.regime    import detect_regime, RegimeResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# SCREEN RESULT
# ─────────────────────────────────────────────

@dataclass
class ScreenResult:
    ticker:        str
    screen_name:   str
    price:         float
    rsi:           float
    vol_ratio:     float
    timeframe:     str
    entry_trigger: str
    entry_price:   float
    stop_loss:     float
    target_1:      float
    target_2:      float
    rr_ratio:      float
    why:           str
    risk_note:     str
    rs_score:      float = 0.0
    spring_score:  int   = 0
    total_score:   int   = 0


# ─────────────────────────────────────────────
# SCREEN UNIVERSE
# LQ45 + high-conviction mid-caps
# ─────────────────────────────────────────────

DAY_TRADE_UNIVERSE = [
    # LQ45 liquid names — tight spreads, fast execution
    "BBCA", "BBRI", "BMRI", "BBNI", "TLKM",
    "ASII", "ADRO", "PTBA", "ANTM", "INCO",
    "MDKA", "AMMN", "MEDC", "PGAS", "BRIS",
    "MIKA", "HEAL", "TOWR", "EXCL", "ISAT",
]

SWING_UNIVERSE = [
    # LQ45 blue chips + high-quality mid-caps
    "BBCA", "BBRI", "BMRI", "BBNI", "TLKM",
    "ASII", "ADRO", "ITMG", "PTBA", "BUMI",
    "ANTM", "INCO", "TINS", "MDKA", "AMMN",
    "UNVR", "ICBP", "INDF", "MAPI", "ACES",
    "BSDE", "CTRA", "SMRA", "PWON",
    "KLBF", "SIDO", "MIKA", "HEAL",
    "SMGR", "INTP",
]


# ─────────────────────────────────────────────
# DAY TRADE SCREENS
# ─────────────────────────────────────────────

def _screen_volume_breakout(t: TechnicalResult) -> Optional[ScreenResult]:
    """
    Volume Breakout: vol > 3x avg + price near / breaking 20d high.
    Classic bandar entry signal — high volume + breakout = momentum ignition.
    """
    if t.volume_ratio < 3.0:
        return None
    if t.current_price < t.high_20d * 0.97:   # not near 20d high
        return None
    if t.rsi < 45 or t.rsi > 80:
        return None
    if t.at_arb or t.at_ara:
        return None

    risk = t.current_price - t.stop_loss
    if risk <= 0:
        risk = t.atr

    return ScreenResult(
        ticker        = t.ticker,
        screen_name   = "Volume Breakout",
        price         = t.current_price,
        rsi           = t.rsi,
        vol_ratio     = t.volume_ratio,
        timeframe     = "Intraday / 1-2 day",
        entry_trigger = (
            f"Price closes above {t.high_20d:,.0f} (20d high) "
            f"on vol > {t.avg_volume_20d * 3 / 1e6:.1f}M. "
            f"Entry on retest of breakout level."
        ),
        entry_price   = t.current_price,
        stop_loss     = t.stop_loss,
        target_1      = t.current_price + risk * 1.5,
        target_2      = t.current_price + risk * 2.5,
        rr_ratio      = round((risk * 2.0) / risk, 1),
        why           = (
            f"Vol {t.volume_ratio:.1f}x avg — explosive flow. "
            f"Price {t.current_price:,.0f} pressing 20d high {t.high_20d:,.0f}. "
            f"RSI {t.rsi:.0f} — momentum intact, not overbought. "
            f"{t.volume_signal.upper()} volume pattern."
        ),
        risk_note     = (
            "Day trade — hard stop below breakout candle low. "
            "Exit 70% at T1, trail the rest. "
            "If price fails to close above 20d high, no entry."
        ),
        rs_score      = t.rs_score,
        total_score   = t.total_score,
    )


def _screen_opening_momentum(t: TechnicalResult) -> Optional[ScreenResult]:
    """
    Opening Momentum: strong EMA8/21 alignment + RSI in buy zone + above average vol.
    Catch stocks that open strong and continue — ride the institutional order flow.
    """
    if t.ema8 <= t.ema21:
        return None
    if not (52 <= t.rsi <= 70):
        return None
    if t.volume_ratio < 1.5:
        return None
    if t.current_price <= t.ema50:
        return None
    if t.at_arb or t.at_ara:
        return None

    risk = t.current_price - t.stop_loss
    if risk <= 0:
        risk = t.atr

    return ScreenResult(
        ticker        = t.ticker,
        screen_name   = "Opening Momentum",
        price         = t.current_price,
        rsi           = t.rsi,
        vol_ratio     = t.volume_ratio,
        timeframe     = "Same day / next open",
        entry_trigger = (
            f"Buy on open if price holds above EMA8 {t.ema8:,.0f}. "
            f"Vol must be > {t.avg_volume_20d * 1.5 / 1e6:.1f}M by 10:00 WIB."
        ),
        entry_price   = t.current_price,
        stop_loss     = t.stop_loss,
        target_1      = t.resistance_1 if t.resistance_1 > t.current_price else t.current_price + risk * 1.5,
        target_2      = t.resistance_2 if t.resistance_2 > t.current_price else t.current_price + risk * 2.5,
        rr_ratio      = round((risk * 1.8) / risk, 1),
        why           = (
            f"EMA8 {t.ema8:,.0f} > EMA21 {t.ema21:,.0f} — uptrend confirmed. "
            f"RSI {t.rsi:.0f} in buy zone. "
            f"Price above EMA50 = trend support. "
            f"Vol {t.volume_ratio:.1f}x avg — active participation."
        ),
        risk_note     = (
            "Momentum trade — only valid while EMA8 > EMA21. "
            "If EMA cross fails intraday, exit immediately. "
            "Avoid if IHSG opens down > 0.5%."
        ),
        rs_score      = t.rs_score,
        total_score   = t.total_score,
    )


def _screen_silent_accumulation_daytrade(t: TechnicalResult) -> Optional[ScreenResult]:
    """
    Silent Accumulation Day Trade: high vol + narrow range = bandar loading.
    Next candle often explosive. Enter breakout of the tight range.
    """
    if not t.is_silent_accum:
        return None
    if t.volume_ratio < 3.0:
        return None
    if t.at_arb:
        return None

    range_top = t.high_20d
    risk      = max(t.atr, t.current_price * 0.02)

    return ScreenResult(
        ticker        = t.ticker,
        screen_name   = "Silent Accumulation",
        price         = t.current_price,
        rsi           = t.rsi,
        vol_ratio     = t.volume_ratio,
        timeframe     = "Next 1-3 candles",
        entry_trigger = (
            f"BUY when price breaks above today's high {t.high_20d:,.0f} "
            f"on any vol spike. Tight range = coiled spring."
        ),
        entry_price   = t.current_price * 1.005,
        stop_loss     = t.current_price - risk,
        target_1      = t.current_price + risk * 2.0,
        target_2      = t.current_price + risk * 3.5,
        rr_ratio      = round(2.0, 1),
        why           = (
            f"Vol {t.volume_ratio:.1f}x avg but range NARROW — "
            f"bandar absorbing supply without moving price. "
            f"Pre-breakout signature. "
            f"{'Smart money A/D divergence confirmed. ' if t.ad_divergence else ''}"
            f"Expect explosive move within 1-3 sessions."
        ),
        risk_note     = (
            "High R:R but binary — either breaks out or fails. "
            "Hard stop below today's low. "
            "Size: half position max, add on confirmation."
        ),
        rs_score      = t.rs_score,
        total_score   = t.total_score,
    )


# ─────────────────────────────────────────────
# SWING SCREENS
# ─────────────────────────────────────────────

def _screen_spring_setup(t: TechnicalResult) -> Optional[ScreenResult]:
    """
    Wyckoff Spring: accumulation at support with institutional absorption.
    Highest conviction swing setup — Spring score >= 5.
    """
    if t.spring_score < 5:
        return None
    if t.at_arb:
        return None
    if not t.near_major_support:
        return None

    risk = t.current_price - t.stop_loss
    if risk <= 0:
        risk = t.atr * 1.5

    return ScreenResult(
        ticker        = t.ticker,
        screen_name   = f"Spring Setup ({t.spring_score}/10)",
        price         = t.current_price,
        rsi           = t.rsi,
        vol_ratio     = t.volume_ratio,
        timeframe     = "3-15 days (swing)",
        entry_trigger = t.entry_trigger[:200] if t.entry_trigger else (
            f"4H close above {t.high_20d:,.0f} on vol > 1.2x avg. "
            f"Stop: below S1 {t.support_1:,.0f}."
        ),
        entry_price   = t.entry_low,
        stop_loss     = t.stop_loss,
        target_1      = t.t1,
        target_2      = t.t2,
        rr_ratio      = t.rr_ratio,
        why           = (
            f"Spring score {t.spring_score}/10. "
            f"{'Stopping volume — institutions absorbing. ' if t.stopping_volume else ''}"
            f"{'OBV diverging — smart money accumulating. ' if t.obv_diverging else ''}"
            f"{'A/D line rising vs falling price. ' if t.ad_divergence else ''}"
            f"{'BB squeeze — energy coiling. ' if t.bb_squeeze_spring else ''}"
            f"Within {abs(t.support_proximity_pct):.1f}% of major support {t.support_1:,.0f}."
        ),
        risk_note     = (
            f"Stop below S1 {t.support_1:,.0f} — decisive close below = thesis broken. "
            "Spring fails 50% of time without story catalyst. "
            "Watch for IDX disclosure / RUPSLB to confirm."
        ),
        rs_score      = t.rs_score,
        spring_score  = t.spring_score,
        total_score   = t.total_score,
    )


def _screen_institutional_discount(t: TechnicalResult, regime: str) -> Optional[ScreenResult]:
    """
    Institutional Discount: LQ45 oversold + RS holding = blue chip mean reversion.
    Works in BEAR/PANIC regime when big funds buy what retail throws away.
    """
    if regime not in ("BEAR", "PANIC"):
        return None
    if not t.is_lq45:
        return None
    if t.rsi > 38:
        return None
    if t.rs_score < 0.8:
        return None
    if t.at_arb:
        return None

    risk = t.current_price - t.bb_lower if t.bb_lower > 0 else t.atr * 2

    return ScreenResult(
        ticker        = t.ticker,
        screen_name   = "Institutional Discount",
        price         = t.current_price,
        rsi           = t.rsi,
        vol_ratio     = t.volume_ratio,
        timeframe     = "5-20 days",
        entry_trigger = (
            f"Limit at {t.bb_lower:,.0f} (lower BB) or below. "
            f"Confirm: RS Score > 1.0 for 3 days. "
            f"Foreign flow net buy = green light."
        ),
        entry_price   = t.bb_lower if t.bb_lower > 0 else t.current_price,
        stop_loss     = t.stop_loss,
        target_1      = t.bb_mid,
        target_2      = t.ema50 if t.ema50 > t.bb_mid else t.bb_mid * 1.05,
        rr_ratio      = t.rr_ratio,
        why           = (
            f"LQ45 blue chip RSI {t.rsi:.0f} — institutional discount zone. "
            f"RS {t.rs_score:.2f} — holding vs IHSG = funds are not dumping. "
            f"Historical: LQ45 below RSI 35 bounces 5-8% in 10 trading days. "
            f"{'Smart money A/D divergence: ' + t.ad_divergence_msg[:60] if t.ad_divergence else ''}"
        ),
        risk_note     = (
            "Mean reversion only — NOT a trend trade. "
            "Exit at EMA20 / BB midline, don't hold through resistance. "
            "IHSG break below EMA200 = stop everything, exit all positions."
        ),
        rs_score      = t.rs_score,
        total_score   = t.total_score,
    )


def _screen_rs_hunter(t: TechnicalResult, regime: str) -> Optional[ScreenResult]:
    """
    RS Hunter: stocks outperforming IHSG in a down market.
    These have institutional backing — funds rotate into them even when index falls.
    """
    if regime not in ("BEAR", "PANIC", "SIDEWAYS"):
        return None
    if t.rs_score < 1.3:
        return None
    if t.total_score < 50:
        return None
    if t.at_arb or t.at_ara:
        return None
    if t.volume_ratio < 1.0:
        return None

    risk = t.current_price - t.stop_loss
    if risk <= 0:
        risk = t.atr

    return ScreenResult(
        ticker        = t.ticker,
        screen_name   = f"RS Hunter ({t.rs_score:.2f}x IHSG)",
        price         = t.current_price,
        rsi           = t.rsi,
        vol_ratio     = t.volume_ratio,
        timeframe     = "1-3 weeks",
        entry_trigger = (
            f"Buy on pullback to EMA21 {t.ema21:,.0f}. "
            f"RS Score must stay > 1.2 — if it drops, exit. "
            f"Vol confirmation: any day vol > {t.avg_volume_20d * 1.5 / 1e6:.1f}M."
        ),
        entry_price   = min(t.current_price, t.ema21) if t.ema21 > 0 else t.current_price,
        stop_loss     = t.stop_loss,
        target_1      = t.t1,
        target_2      = t.t2,
        rr_ratio      = t.rr_ratio,
        why           = (
            f"RS Score {t.rs_score:.2f} — outperforming IHSG by {(t.rs_score - 1) * 100:.0f}% "
            f"in {regime} regime. "
            f"Institutions are accumulating this while selling the index. "
            f"Score {t.total_score}/100. "
            f"{'Conglomerate backing: ' + t.conglomerate_flag[:40] if t.conglomerate_flag else ''}"
        ),
        risk_note     = (
            "RS can reverse quickly on news. "
            "RS Score dropping below 1.0 = institutional support gone, exit immediately. "
            "Position size: standard — this is not a speculative play."
        ),
        rs_score      = t.rs_score,
        total_score   = t.total_score,
    )


def _screen_vcp_breakout(t: TechnicalResult) -> Optional[ScreenResult]:
    """
    VCP / Tight Base: volatility contracting near resistance = energy storing.
    Breakout from a VCP is a high R:R swing entry — tight stop, big target.
    """
    if t.bb_bandwidth_pct > 8.0:   # BB not tight enough
        return None
    if t.total_score < 55:
        return None
    if t.rsi < 45 or t.rsi > 72:
        return None
    if t.at_arb or t.at_ara:
        return None
    if t.volume_ratio > 3.0:       # too much vol = already moving
        return None

    risk = t.current_price - t.stop_loss
    if risk <= 0:
        risk = t.atr

    return ScreenResult(
        ticker        = t.ticker,
        screen_name   = f"VCP Setup (BB {t.bb_bandwidth_pct:.1f}%)",
        price         = t.current_price,
        rsi           = t.rsi,
        vol_ratio     = t.volume_ratio,
        timeframe     = "Breakout within 1-5 days",
        entry_trigger = (
            f"BUY on first close above BB upper {t.bb_upper:,.0f} "
            f"on vol > {t.avg_volume_20d * 2 / 1e6:.1f}M. "
            f"Or: break above resistance {t.resistance_1:,.0f} with vol surge."
        ),
        entry_price   = t.bb_upper,
        stop_loss     = t.bb_lower,
        target_1      = t.t1,
        target_2      = t.t2,
        rr_ratio      = t.rr_ratio,
        why           = (
            f"BB bandwidth {t.bb_bandwidth_pct:.1f}% — historically tight, "
            f"volatility contraction = energy storing. "
            f"VCP/flat base is the Minervini setup. "
            f"RSI {t.rsi:.0f} — not overbought, has room. "
            f"Score {t.total_score}/100."
        ),
        risk_note     = (
            "Only valid while bands stay tight — if vol expands down, exit. "
            "False breakout risk: require candle CLOSE above band, not just wick. "
            "Stop: BB lower — below that the base is broken."
        ),
        rs_score      = t.rs_score,
        total_score   = t.total_score,
    )


# ─────────────────────────────────────────────
# MAIN RUNNER
# ─────────────────────────────────────────────

def run_all_screens() -> Tuple[List[ScreenResult], List[ScreenResult]]:
    """
    Run all screens. Returns (day_trades[:5], swings[:5]).
    Fetches data once per ticker, runs all applicable screens.
    """
    regime_result = detect_regime()
    regime        = regime_result.regime

    day_trades: List[ScreenResult] = []
    swings:     List[ScreenResult] = []

    # ── Day trade universe ─────────────────────────────────────────────
    logger.info("Running day trade screens on %d tickers...", len(DAY_TRADE_UNIVERSE))
    for ticker in DAY_TRADE_UNIVERSE:
        if len(day_trades) >= 5:
            break
        try:
            t = analyse_ticker(ticker, regime_result=regime_result)
            if t is None or t.is_rejected:
                continue

            result = (
                _screen_silent_accumulation_daytrade(t) or
                _screen_volume_breakout(t) or
                _screen_opening_momentum(t)
            )
            if result:
                day_trades.append(result)
                logger.info("Day trade: %s — %s", ticker, result.screen_name)

        except Exception as e:
            logger.warning("Day trade screen error %s: %s", ticker, e)

    # ── Swing universe ─────────────────────────────────────────────────
    logger.info("Running swing screens on %d tickers...", len(SWING_UNIVERSE))
    seen = set()
    for ticker in SWING_UNIVERSE:
        if len(swings) >= 5:
            break
        if ticker in seen:
            continue
        seen.add(ticker)
        try:
            t = analyse_ticker(ticker, regime_result=regime_result)
            if t is None or t.is_rejected:
                continue

            result = (
                _screen_spring_setup(t) or
                _screen_institutional_discount(t, regime) or
                _screen_rs_hunter(t, regime) or
                _screen_vcp_breakout(t)
            )
            if result:
                swings.append(result)
                logger.info("Swing: %s — %s", ticker, result.screen_name)

        except Exception as e:
            logger.warning("Swing screen error %s: %s", ticker, e)

    # Sort by quality
    day_trades.sort(key=lambda x: x.total_score, reverse=True)
    swings.sort(key=lambda x: (x.spring_score * 10 + x.total_score), reverse=True)

    logger.info(
        "Screens done: %d day trades, %d swings",
        len(day_trades), len(swings)
    )
    return day_trades[:5], swings[:5]
