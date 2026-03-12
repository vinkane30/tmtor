"""
modules/regime.py — Market Regime Detection Brain
Replaces binary bull/bear with 4-regime system + RS scoring
"""

import logging
from dataclasses import dataclass
from typing import Tuple
import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

@dataclass
class RegimeResult:
    regime:        str    # "BULL" | "SIDEWAYS" | "BEAR" | "PANIC"
    strategy:      str    # "TREND_FOLLOW" | "VCP" | "MEAN_REVERT" | "RS_HUNT"
    ihsg_price:    float = 0.0
    ihsg_ema50:    float = 0.0
    ihsg_ema200:   float = 0.0
    ihsg_rsi:      float = 0.0
    ihsg_atr_pct:  float = 0.0   # volatility as % of price
    vix_equivalent: float = 0.0  # IDX volatility proxy
    description:   str = ""
    emoji:         str = ""
    scan_mode:     str = ""      # what scan to run


def detect_regime() -> RegimeResult:
    """
    4-Regime system based on IHSG position relative to EMAs + volatility.

    BULL:     Price > EMA50 > EMA200         → Trend Following + VCP
    SIDEWAYS: Price between EMA50 & EMA200   → VCP + selective momentum
    BEAR:     Price < EMA50, above EMA200    → Mean Reversion + RS hunting
    PANIC:    Price < EMA200                 → Pure RS + Institutional Discount
    """
    try:
        tk   = yf.Ticker("^JKSE")
        df   = tk.history(period="1y", interval="1d")
        if df is None or len(df) < 60:
            return _default_regime()

        close  = df["Close"]
        high   = df["High"]
        low    = df["Low"]

        price  = float(close.iloc[-1])
        ema50  = float(close.ewm(span=50).mean().iloc[-1])
        ema200 = float(close.ewm(span=200).mean().iloc[-1])

        # RSI
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi   = float((100 - 100 / (1 + rs)).iloc[-1])

        # ATR as volatility proxy
        tr    = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr14     = float(tr.rolling(14).mean().iloc[-1])
        atr_pct   = atr14 / price * 100

        # Regime classification
        if price > ema50 and ema50 > ema200:
            regime   = "BULL"
            strategy = "TREND_FOLLOW"
            desc     = "IHSG Bull — EMA50 > EMA200. Trend following aktif."
            emoji    = "🟢"
            scan     = "MOMENTUM"

        elif price > ema200 and price < ema50:
            regime   = "SIDEWAYS"
            strategy = "VCP"
            desc     = "IHSG Sideways — Price antara EMA50 & EMA200. VCP + selective."
            emoji    = "🟡"
            scan     = "VCP_MOMENTUM"

        elif price < ema50 and price > ema200 * 0.97:
            regime   = "BEAR"
            strategy = "MEAN_REVERT"
            desc     = "IHSG Bear — Di bawah EMA50. Pivot ke mean reversion & RS stocks."
            emoji    = "🟠"
            scan     = "MEAN_REVERT_RS"

        else:
            regime   = "PANIC"
            strategy = "RS_HUNT"
            desc     = "IHSG Panic — Di bawah EMA200. Pure RS hunting + institutional discount."
            emoji    = "🔴"
            scan     = "RS_INSTITUTIONAL"

        return RegimeResult(
            regime        = regime,
            strategy      = strategy,
            ihsg_price    = price,
            ihsg_ema50    = ema50,
            ihsg_ema200   = ema200,
            ihsg_rsi      = rsi,
            ihsg_atr_pct  = atr_pct,
            description   = desc,
            emoji         = emoji,
            scan_mode     = scan,
        )

    except Exception as e:
        logger.error("Regime detection error: %s", e, exc_info=True)
        return _default_regime()


def _default_regime() -> RegimeResult:
    return RegimeResult(
        regime="UNKNOWN", strategy="MEAN_REVERT",
        description="IHSG data unavailable — defensive mode.",
        emoji="⚪", scan_mode="MEAN_REVERT_RS"
    )


def calc_rs_score(ticker: str, df_stock: pd.DataFrame,
                  df_ihsg: pd.DataFrame, window: int = 10) -> float:
    """
    RS Score = %change stock (10d) / %change IHSG (10d)
    Score > 1.2 = outperforming index during dip = institutional support
    """
    try:
        s_ret  = (df_stock["Close"].iloc[-1] / df_stock["Close"].iloc[-window] - 1) * 100
        i_ret  = (df_ihsg["Close"].iloc[-1]  / df_ihsg["Close"].iloc[-window]  - 1) * 100
        if i_ret == 0:
            return 0.0
        return round(s_ret / abs(i_ret), 3)
    except Exception:
        return 0.0


def calc_ad_line(df: pd.DataFrame) -> pd.Series:
    """
    Accumulation/Distribution Line.
    Rising A/D + falling price = Smart Money accumulation (BUY signal).
    """
    clv = ((df["Close"] - df["Low"]) - (df["High"] - df["Close"])) / \
          (df["High"] - df["Low"]).replace(0, np.nan)
    ad  = (clv * df["Volume"]).cumsum()
    return ad


def detect_ad_divergence(df: pd.DataFrame, lookback: int = 20) -> Tuple[bool, str]:
    """
    Returns (is_diverging, description).
    Bullish divergence: price making lower lows, A/D making higher lows.
    This is the 'Smart Money footprint'.
    """
    try:
        ad      = calc_ad_line(df)
        price   = df["Close"]

        p_slope = float(np.polyfit(range(lookback), price.iloc[-lookback:], 1)[0])
        ad_slope= float(np.polyfit(range(lookback), ad.iloc[-lookback:],    1)[0])

        if p_slope < 0 and ad_slope > 0:
            return True, "🚨 SMART MONEY DIVERGENCE: Price falling but A/D rising — institutional accumulation detected"
        elif p_slope > 0 and ad_slope < 0:
            return False, "⚠️ Distribution: Price rising but A/D falling — institutions exiting"
        elif p_slope > 0 and ad_slope > 0:
            return False, "✅ Confirmed uptrend: Price and A/D both rising"
        else:
            return False, "➖ No divergence signal"
    except Exception:
        return False, "A/D calculation unavailable"
