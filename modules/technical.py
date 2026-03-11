"""
modules/technical.py — Technical Analysis Engine
Pulls price data from yfinance, computes indicators, scores setups.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────

@dataclass
class TechnicalResult:
    ticker:             str
    company_name:       str = ""
    tech_score:         int = 0
    conditions_met:     List[str] = field(default_factory=list)
    conditions_failed:  List[str] = field(default_factory=list)
    is_rejected:        bool = False
    rejection_reason:   str = ""

    # Price data
    current_price:  float = 0.0
    avg_volume_20d: float = 0.0
    today_volume:   float = 0.0
    rsi:            float = 0.0
    atr:            float = 0.0

    # Levels (computed by signal generator)
    entry_low:   float = 0.0
    entry_high:  float = 0.0
    stop_loss:   float = 0.0
    t1:          float = 0.0
    t2:          float = 0.0
    t3:          float = 0.0
    rr_ratio:    float = 0.0

    # Pattern match
    pattern_match: str = ""

    @property
    def passed(self) -> bool:
        return (not self.is_rejected) and (self.tech_score >= config.MIN_TECHNICAL_COUNT)


# ─────────────────────────────────────────────
# MARKET HEALTH CHECK
# ─────────────────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def is_market_healthy() -> Tuple[bool, str]:
    """Check if IHSG is above EMA50 — if not, suppress all signals."""
    try:
        df = yf.download(config.IHSG_TICKER, period="3mo", interval="1d", progress=False, auto_adjust=True)
        if df.empty or len(df) < config.IHSG_EMA_DAYS + 5:
            return True, "IHSG data unavailable — assuming healthy"

        close = df["Close"].squeeze()
        ema50 = _ema(close, config.IHSG_EMA_DAYS)
        recent = close.iloc[-config.IHSG_CORRECTION_DAYS:]
        ema_recent = ema50.iloc[-config.IHSG_CORRECTION_DAYS:]

        below_count = (recent.values < ema_recent.values).sum()
        if below_count >= config.IHSG_CORRECTION_DAYS:
            msg = (f"⚠️ IHSG below EMA{config.IHSG_EMA_DAYS} for "
                   f"{below_count} consecutive days — market in correction")
            return False, msg

        current_ihsg = float(close.iloc[-1])
        current_ema  = float(ema50.iloc[-1])
        pct = (current_ihsg / current_ema - 1) * 100
        msg = (f"IHSG {current_ihsg:,.0f} | EMA{config.IHSG_EMA_DAYS} {current_ema:,.0f} "
               f"({pct:+.1f}%) — market healthy ✅")
        return True, msg

    except Exception as e:
        logger.warning("IHSG health check failed: %s", e)
        return True, "IHSG check error — proceeding"


# ─────────────────────────────────────────────
# INDICATOR HELPERS
# ─────────────────────────────────────────────

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / (loss + 1e-10)
    return 100 - (100 / (1 + rs))


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0))
    return (direction * volume).cumsum()


def _macd(close: pd.Series, fast=12, slow=26, signal=9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast   = _ema(close, fast)
    ema_slow   = _ema(close, slow)
    macd_line  = ema_fast - ema_slow
    signal_line= _ema(macd_line, signal)
    histogram  = macd_line - signal_line
    return macd_line, signal_line, histogram


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period=14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _is_vcp(close: pd.Series, high: pd.Series, low: pd.Series, window=20) -> bool:
    """
    Volatility Contraction Pattern: progressively tighter price swings.
    Simplified: compare last 3 weekly ranges, each smaller than previous.
    """
    if len(close) < window:
        return False
    weeks = []
    for i in range(3):
        start = -(window) + i * 5
        end   = start + 5
        if end == 0:
            wk_high = high.iloc[start:].max()
            wk_low  = low.iloc[start:].min()
        else:
            wk_high = high.iloc[start:end].max()
            wk_low  = low.iloc[start:end].min()
        weeks.append(wk_high - wk_low)
    return weeks[2] < weeks[1] < weeks[0]


def _near_resistance(close: pd.Series, high: pd.Series, threshold=0.10) -> bool:
    """Price is within threshold% below a multi-week resistance level."""
    if len(close) < 20:
        return False
    resistance = high.iloc[-60:].max() if len(high) >= 60 else high.max()
    current    = float(close.iloc[-1])
    return current >= resistance * (1 - threshold) and current < resistance


# ─────────────────────────────────────────────
# CORE ANALYSIS FUNCTION
# ─────────────────────────────────────────────

def analyse_ticker(ticker: str, company_name: str = "") -> Optional[TechnicalResult]:
    """
    Download price data, compute indicators, return TechnicalResult.
    Returns None if data fetch fails.
    """
    # IDX tickers need .JK suffix for yfinance
    yf_ticker = ticker if ticker.endswith(".JK") else f"{ticker}.JK"

    try:
        df_daily = yf.download(yf_ticker, period="6mo", interval="1d",
                               progress=False, auto_adjust=True)
        df_weekly = yf.download(yf_ticker, period="1y", interval="1wk",
                                progress=False, auto_adjust=True)
    except Exception as e:
        logger.warning("yfinance download failed for %s: %s", ticker, e)
        return None

    if df_daily.empty or len(df_daily) < 30:
        logger.debug("Insufficient data for %s", ticker)
        return None

    # Flatten MultiIndex columns if present
    if isinstance(df_daily.columns, pd.MultiIndex):
        df_daily.columns = df_daily.columns.get_level_values(0)
    if isinstance(df_weekly.columns, pd.MultiIndex):
        df_weekly.columns = df_weekly.columns.get_level_values(0)

    close_d  = df_daily["Close"].squeeze()
    high_d   = df_daily["High"].squeeze()
    low_d    = df_daily["Low"].squeeze()
    vol_d    = df_daily["Volume"].squeeze()

    result = TechnicalResult(ticker=ticker, company_name=company_name or ticker)

    # ── REJECTION CHECKS ──────────────────────────────────────────────

    avg_vol_20 = float(vol_d.iloc[-20:].mean())
    result.avg_volume_20d = avg_vol_20
    result.today_volume   = float(vol_d.iloc[-1])

    if avg_vol_20 < config.MIN_AVG_VOLUME:
        result.is_rejected      = True
        result.rejection_reason = f"Avg volume too low ({avg_vol_20:,.0f} < {config.MIN_AVG_VOLUME:,})"
        return result

    # ── INDICATORS ────────────────────────────────────────────────────

    rsi_series  = _rsi(close_d, 14)
    ema5        = _ema(close_d, 5)
    ema20       = _ema(close_d, 20)
    obv_series  = _obv(close_d, vol_d)
    atr_series  = _atr(high_d, low_d, close_d, 14)

    current_price = float(close_d.iloc[-1])
    current_rsi   = float(rsi_series.iloc[-1])
    current_atr   = float(atr_series.iloc[-1])

    result.current_price = current_price
    result.rsi           = current_rsi
    result.atr           = current_atr

    # ── TECHNICAL CONDITIONS ──────────────────────────────────────────

    conditions = []
    failed     = []

    # 1. Volume spike
    vol_ratio = result.today_volume / (avg_vol_20 + 1)
    if vol_ratio >= config.VOLUME_SPIKE_RATIO:
        conditions.append(f"🔊 Volume spike {vol_ratio:.1f}x 20d avg")
    else:
        failed.append(f"Volume {vol_ratio:.1f}x (need {config.VOLUME_SPIKE_RATIO}x)")

    # 2. OBV rising while price flat
    obv_5d_change   = float(obv_series.iloc[-1] - obv_series.iloc[-6])
    price_5d_change = (float(close_d.iloc[-1]) / float(close_d.iloc[-6]) - 1) * 100
    if obv_5d_change > 0 and abs(price_5d_change) < 3.0:
        conditions.append("📈 OBV rising while price flat (accumulation)")
    else:
        failed.append(f"OBV no accumulation pattern")

    # 3. EMA5 crossing above EMA20
    ema_cross = (float(ema5.iloc[-1]) > float(ema20.iloc[-1]) and
                 float(ema5.iloc[-2]) <= float(ema20.iloc[-2]))
    if ema_cross:
        conditions.append("✅ EMA5 crossed above EMA20")
    else:
        ema_above = float(ema5.iloc[-1]) > float(ema20.iloc[-1])
        if ema_above:
            conditions.append("✅ EMA5 above EMA20")
        else:
            failed.append("EMA5 below EMA20")

    # 4. RSI 50–70 and rising
    rsi_prev = float(rsi_series.iloc[-2]) if len(rsi_series) >= 2 else current_rsi
    if config.RSI_LOW <= current_rsi <= config.RSI_HIGH and current_rsi > rsi_prev:
        conditions.append(f"📊 RSI {current_rsi:.1f} (50–70, rising)")
    else:
        failed.append(f"RSI {current_rsi:.1f} not in 50–70 range or not rising")

    # 5. VCP / flat base
    if _is_vcp(close_d, high_d, low_d, 20):
        conditions.append("🔵 VCP / volatility contraction pattern")
    else:
        failed.append("No VCP pattern")

    # 6. Weekly MACD bullish crossover
    if len(df_weekly) >= 35:
        close_w = df_weekly["Close"].squeeze()
        macd_w, sig_w, hist_w = _macd(close_w)
        if len(macd_w) >= 2:
            macd_cross = (float(macd_w.iloc[-1]) > float(sig_w.iloc[-1]) and
                          float(macd_w.iloc[-2]) <= float(sig_w.iloc[-2]))
            if macd_cross:
                conditions.append("📉→📈 Weekly MACD bullish crossover")
            elif float(hist_w.iloc[-1]) > 0 and float(hist_w.iloc[-1]) > float(hist_w.iloc[-2]):
                conditions.append("📊 Weekly MACD histogram expanding (positive)")
            else:
                failed.append("Weekly MACD no bullish signal")
        else:
            failed.append("Weekly MACD insufficient data")
    else:
        failed.append("Weekly MACD insufficient data")

    # 7. Near multi-week resistance
    if _near_resistance(close_d, high_d, threshold=0.10):
        conditions.append("🎯 Price within 10% of multi-week resistance")
    else:
        failed.append("Price not near resistance breakout zone")

    result.conditions_met    = conditions
    result.conditions_failed = failed
    result.tech_score        = len(conditions)

    # ── TRADE LEVELS ─────────────────────────────────────────────────

    if result.tech_score >= config.MIN_TECHNICAL_COUNT:
        _compute_trade_levels(result, current_price, current_atr, high_d, low_d)
        _identify_pattern(result, close_d, vol_d, rsi_series)

    return result


# ─────────────────────────────────────────────
# TRADE LEVEL COMPUTATION
# ─────────────────────────────────────────────

def _compute_trade_levels(result: TechnicalResult, price: float,
                           atr: float, high: pd.Series, low: pd.Series):
    """Compute entry, stop, targets using ATR-based math."""
    # Entry zone: current price ± 0.5 ATR
    result.entry_low  = round(price * 0.99, 0)
    result.entry_high = round(price * 1.005, 0)

    # Stop: 1.5 ATR below entry
    result.stop_loss  = round(price - 1.5 * atr, 0)

    risk = price - result.stop_loss
    if risk <= 0:
        risk = atr

    # Targets: 2R, 3.5R, 5R
    result.t1 = round(price + 2.0 * risk, 0)
    result.t2 = round(price + 3.5 * risk, 0)
    result.t3 = round(price + 5.0 * risk, 0)

    # Blended R:R (weighted by exit proportions 40/35/25)
    gain_t1 = (result.t1 - price) / risk
    gain_t2 = (result.t2 - price) / risk
    gain_t3 = (result.t3 - price) / risk
    result.rr_ratio = round(0.40 * gain_t1 + 0.35 * gain_t2 + 0.25 * gain_t3, 2)


def _identify_pattern(result: TechnicalResult, close: pd.Series,
                       vol: pd.Series, rsi: pd.Series):
    """Match against known multibagger archetypes."""
    current = float(close.iloc[-1])
    low_52w = float(close.iloc[-252:].min()) if len(close) >= 252 else float(close.min())
    pct_from_low = (current / low_52w - 1) * 100

    current_rsi = float(rsi.iloc[-1])
    vol_trend   = float(vol.iloc[-5:].mean()) / (float(vol.iloc[-20:-5].mean()) + 1)

    if pct_from_low < 50 and any("OBV" in c for c in result.conditions_met):
        result.pattern_match = "BBHI (early accumulation, OBV divergence)"
    elif pct_from_low < 20 and any("VCP" in c for c in result.conditions_met):
        result.pattern_match = "BREN (tight base before breakout)"
    elif vol_trend > 2 and current_rsi > 60:
        result.pattern_match = "PANI (volume + momentum surge)"
    elif any("MACD" in c for c in result.conditions_met):
        result.pattern_match = "RLCO (weekly MACD-driven momentum)"
    else:
        result.pattern_match = "Generic breakout setup"


# ─────────────────────────────────────────────
# BATCH ANALYSIS
# ─────────────────────────────────────────────

def analyse_tickers_batch(tickers: List[Tuple[str, str]]) -> List[TechnicalResult]:
    """
    tickers: list of (ticker, company_name)
    Returns only passing setups (not rejected, tech_score >= threshold).
    """
    results = []
    for ticker, name in tickers:
        try:
            r = analyse_ticker(ticker, name)
            if r is not None:
                results.append(r)
        except Exception as e:
            logger.error("Technical analysis error for %s: %s", ticker, e)

    passing = [r for r in results if r.passed]
    passing.sort(key=lambda x: x.tech_score, reverse=True)
    return passing


def get_ihsg_summary() -> str:
    """Return a formatted IHSG market summary for /ihsg command."""
    healthy, msg = is_market_healthy()

    try:
        df = yf.download(config.IHSG_TICKER, period="5d", interval="1d",
                         progress=False, auto_adjust=True)
        if not df.empty:
            close = df["Close"].squeeze()
            pct_1d = (float(close.iloc[-1]) / float(close.iloc[-2]) - 1) * 100
            pct_5d = (float(close.iloc[-1]) / float(close.iloc[0]) - 1) * 100
            return (
                f"📊 *IHSG Overview*\n"
                f"Current: {float(close.iloc[-1]):,.0f}\n"
                f"1D: {pct_1d:+.2f}% | 5D: {pct_5d:+.2f}%\n"
                f"{'✅ Market HEALTHY — safe to buy' if healthy else '⛔ Market CORRECTION — reduce exposure'}\n"
                f"_{msg}_"
            )
    except Exception:
        pass

    return f"📊 *IHSG*\n{'✅ Healthy' if healthy else '⛔ Correction'}\n_{msg}_"
