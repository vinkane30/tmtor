"""
modules/technical.py — IDX Quantitative Analysis Engine v2
Multi-regime scoring, RS, A/D divergence, institutional footprint,
rumor detection, ARA/ARB guard, 0-100 scoring system.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict
from datetime import datetime, timedelta

import httpx
import yfinance as yf
import pandas as pd
import numpy as np
import feedparser

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from modules.regime import (detect_regime, calc_rs_score, detect_ad_divergence,
                             RegimeResult, calc_ad_line)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

LQ45_TICKERS = {
    "BBCA", "BBRI", "BBNI", "BMRI", "TLKM", "ASII", "UNVR", "PGAS",
    "ADRO", "PTBA", "INDF", "ICBP", "KLBF", "SIDO", "GGRM", "HMSP",
    "JSMR", "SMGR", "ANTM", "INCO", "MIKA", "HEAL", "ACES", "MAPI",
    "CPIN", "JPFA", "EXCL", "ISAT", "TOWR", "BRIS",
}

CONGLOMERATE_KEYWORDS = [
    "salim", "sinarmas", "prajogo", "pangestu", "lippo", "bakrie",
    "hary tanoe", "djarum", "wings", "gudang garam", "rajawali",
    "medco", "indofood", "astra", "telkom",
]

RUMOR_KEYWORDS = [
    "rights issue", "tender offer", "backdoor listing", "akuisisi",
    "merger", "buyback", "delisting", "go private", "strategic investor",
    "injeksi aset", "RUPSLB", "divestasi", "spin off",
]

# ARA/ARB limits by price tier (IDX rules)
def _ara_arb_limit(price: float) -> float:
    if price < 200:    return 0.35
    elif price < 5000: return 0.25
    else:              return 0.20

# ─────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────

@dataclass
class TechnicalResult:
    ticker:            str
    company_name:      str = ""
    current_price:     float = 0.0

    # Regime context
    regime:            str = ""
    strategy:          str = ""

    # Score (0–100)
    total_score:       int = 0
    score_label:       str = ""      # "High Conviction" | "Moderate" | "Low Interest"
    play_type:         str = ""      # "Momentum Play" | "Mean Reversion" | "Rumor Play" | "Value Play"

    # Technical
    ema8:              float = 0.0
    ema21:             float = 0.0
    ema50:             float = 0.0
    ema200:            float = 0.0
    rsi:               float = 0.0
    adx:               float = 0.0
    atr:               float = 0.0
    bb_upper:          float = 0.0
    bb_lower:          float = 0.0
    bb_mid:            float = 0.0

    # RS & A/D
    rs_score:          float = 0.0
    ad_divergence:     bool = False
    ad_divergence_msg: str = ""

    # Volume / Flow
    today_volume:      float = 0.0
    avg_volume_20d:    float = 0.0
    volume_ratio:      float = 0.0
    volume_signal:     str = ""
    daily_turnover:    float = 0.0
    is_silent_accum:   bool = False   # high vol + narrow range

    # Foreign flow (simulated from yfinance institutional data)
    foreign_flow_signal: str = ""

    # Levels
    support_1:         float = 0.0
    support_2:         float = 0.0
    resistance_1:      float = 0.0
    resistance_2:      float = 0.0
    high_20d:          float = 0.0
    low_20d:           float = 0.0
    high_6m:           float = 0.0

    # ARA/ARB
    at_arb:            bool = False
    at_ara:            bool = False
    arb_warning:       str = ""

    # Trade levels
    entry_low:         float = 0.0
    entry_high:        float = 0.0
    stop_loss:         float = 0.0
    t1:                float = 0.0
    t2:                float = 0.0
    t3:                float = 0.0
    rr_ratio:          float = 0.0
    lot_size:          int = 0

    # Fundamental
    roe:               Optional[float] = None
    der:               Optional[float] = None
    pbv:               Optional[float] = None
    revenue_growth:    Optional[float] = None
    market_cap:        Optional[float] = None
    pe_ratio:          Optional[float] = None
    is_lq45:           bool = False

    # Conditions
    conditions_met:    List[str] = field(default_factory=list)
    conditions_failed: List[str] = field(default_factory=list)
    passed:            bool = False
    is_rejected:       bool = False
    rejection_reason:  str = ""

    # Thesis
    buy_thesis:        str = ""
    bear_case:         str = ""
    invalidation:      str = ""
    entry_trigger:     str = ""
    verdict:           str = ""
    verdict_reason:    str = ""

    # ── Spring / Wyckoff Accumulation Detection ──
    spring_score:          int   = 0    # 0-10 composite Spring score
    spring_label:          str   = ""   # "SPRING TRIGGERED" | "WATCH" | "NO SIGNAL"
    stopping_volume:       bool  = False
    stopping_volume_detail: str  = ""
    obv_diverging:         bool  = False
    obv_detail:            str   = ""
    bb_squeeze_spring:     bool  = False
    bb_bandwidth_pct:      float = 0.0  # current BB width as % of midline
    near_major_support:    bool  = False
    support_proximity_pct: float = 0.0  # % distance from current price to S1
    spring_accum_evidence: List[str] = field(default_factory=list)

    # News & rumors
    recent_news:       List[str] = field(default_factory=list)
    rumor_flags:       List[str] = field(default_factory=list)
    conglomerate_flag: str = ""


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _rp(v: float) -> str:
    return f"Rp {v:,.0f}"

def _calc_rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not rsi.empty else 50.0

def _calc_adx(df: pd.DataFrame, period: int = 14) -> float:
    try:
        high, low, close = df["High"], df["Low"], df["Close"]
        tr   = pd.concat([high - low,
                          (high - close.shift()).abs(),
                          (low  - close.shift()).abs()], axis=1).max(axis=1)
        atr  = tr.rolling(period).mean()
        up   = high.diff(); down = -low.diff()
        pdm  = up.where((up > down) & (up > 0), 0.0)
        ndm  = down.where((down > up) & (down > 0), 0.0)
        pdi  = 100 * pdm.rolling(period).mean() / atr.replace(0, np.nan)
        ndi  = 100 * ndm.rolling(period).mean() / atr.replace(0, np.nan)
        dx   = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
        return float(dx.rolling(period).mean().iloc[-1])
    except Exception:
        return 0.0

def _calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    try:
        high, low, close = df["High"], df["Low"], df["Close"]
        tr = pd.concat([high - low,
                        (high - close.shift()).abs(),
                        (low  - close.shift()).abs()], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    except Exception:
        return 0.0

def _calc_bollinger(close: pd.Series, period: int = 20) -> Tuple[float, float, float]:
    mid   = close.rolling(period).mean()
    std   = close.rolling(period).std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    return float(upper.iloc[-1]), float(mid.iloc[-1]), float(lower.iloc[-1])

def _support_resistance(df: pd.DataFrame) -> Tuple[float, float, float, float]:
    try:
        highs = df["High"].rolling(5).max()
        lows  = df["Low"].rolling(5).min()
        r1 = float(highs.iloc[-5:].max())
        r2 = float(highs.iloc[-20:-5].max())
        s1 = float(lows.iloc[-5:].min())
        s2 = float(lows.iloc[-20:-5].min())
        return s1, s2, r1, r2
    except Exception:
        return 0, 0, 0, 0

def _fetch_news_and_rumors(ticker_clean: str) -> Tuple[List[str], List[str], str]:
    """Returns (headlines, rumor_flags, conglomerate_flag)"""
    headlines   = []
    rumor_flags = []
    cong_flag   = ""

    feeds = [
        f"https://rss.kontan.co.id/search/{ticker_clean}",
        "https://rss.kontan.co.id/category/bursa",
        "https://rss.bisnis.com/feed/rss2/market/emiten.rss",
    ]
    for url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                title = entry.get("title", "")
                if not title:
                    continue
                tl = title.lower()

                if ticker_clean.upper() in title.upper():
                    headlines.append(title)

                    # Rumor detection
                    for kw in RUMOR_KEYWORDS:
                        if kw in tl and kw not in rumor_flags:
                            rumor_flags.append(kw.title())

                    # Conglomerate detection
                    for kw in CONGLOMERATE_KEYWORDS:
                        if kw in tl:
                            cong_flag = f"🏛️ {kw.title()} group involvement detected"
                            break

                if len(headlines) >= 5:
                    break
        except Exception:
            pass
        if len(headlines) >= 5:
            break

    return headlines[:5], rumor_flags[:5], cong_flag

def _check_arb_ara(price: float, prev_close: float) -> Tuple[bool, bool, str]:
    """Check if stock is at ARA (ceiling) or ARB (floor)."""
    limit   = _ara_arb_limit(prev_close)
    arb_lvl = prev_close * (1 - limit)
    ara_lvl = prev_close * (1 + limit)

    at_arb  = price <= arb_lvl * 1.005
    at_ara  = price >= ara_lvl * 0.995

    if at_arb:
        return True, False, f"⚠️ ARB WARNING: Stock near auto-reject floor {_rp(arb_lvl)} — falling knife risk, avoid entry"
    if at_ara:
        return False, True, f"⚠️ ARA: Stock near auto-reject ceiling {_rp(ara_lvl)} — do NOT chase"
    return False, False, ""

def _detect_silent_accumulation(df: pd.DataFrame, vol_ratio: float) -> bool:
    """
    Silent accumulation: volume > 3x avg BUT daily range is narrow (<1.5% of price).
    Bandar buying without moving price = pre-breakout setup.
    """
    try:
        day_range = float(df["High"].iloc[-1] - df["Low"].iloc[-1])
        price     = float(df["Close"].iloc[-1])
        range_pct = day_range / price * 100
        return vol_ratio >= 3.0 and range_pct < 1.5
    except Exception:
        return False


# ─────────────────────────────────────────────
# SPRING / WYCKOFF DETECTION
# ─────────────────────────────────────────────

def _detect_stopping_volume(df: pd.DataFrame, avg_vol: float,
                             atr: float, near_support: bool) -> Tuple[bool, str]:
    """
    Stopping volume = institutional absorption at support.
    Signature: large volume spike on a candle with NARROW spread.
    Big sellers meet big buyers → price barely moves despite huge turnover.
    This is the opposite of capitulation (which has wide spreads).
    """
    try:
        last   = df.iloc[-1]
        vol    = float(last["Volume"])
        spread = float(last["High"]) - float(last["Low"])
        close  = float(last["Close"])
        open_  = float(last["Open"])

        vol_big      = vol >= avg_vol * 2.0
        spread_ratio = spread / atr if atr > 0 else 1.0
        spread_narrow = spread_ratio < 0.8   # spread < 80% of ATR = absorption, not panic

        # Extra signal: check last 3 candles for cumulative stopping pattern
        if len(df) >= 3:
            recent_vols   = df["Volume"].iloc[-3:].mean()
            recent_spread = (df["High"].iloc[-3:] - df["Low"].iloc[-3:]).mean()
            pattern_ok    = recent_vols >= avg_vol * 1.5 and recent_spread < atr
        else:
            pattern_ok = False

        if vol_big and spread_narrow and near_support:
            direction = "red" if close < open_ else "neutral"
            detail = (
                f"Vol {vol/avg_vol:.1f}x avg | Spread {spread_ratio:.2f}x ATR "
                f"({'absorption on red candle' if direction == 'red' else 'tight range high vol'}) "
                f"= institutions absorbing supply"
            )
            return True, detail

        if pattern_ok and near_support:
            detail = f"3-candle stopping pattern: avg vol {recent_vols/avg_vol:.1f}x, narrow spread = cumulative absorption"
            return True, detail

        return False, ""
    except Exception:
        return False, ""


def _detect_obv_divergence(close: pd.Series, volume: pd.Series,
                            lookback: int = 15) -> Tuple[bool, str]:
    """
    OBV/CVD proxy divergence:
    Price making lower lows but OBV slope positive = smart money accumulating.
    This is the single most reliable Spring precursor signal.
    """
    try:
        direction = np.sign(close.diff().fillna(0))
        obv       = (direction * volume).cumsum()

        if len(obv) < lookback:
            return False, ""

        price_vals = close.iloc[-lookback:].values.astype(float)
        obv_vals   = obv.iloc[-lookback:].values.astype(float)
        x          = np.arange(lookback)

        price_slope = float(np.polyfit(x, price_vals, 1)[0])
        obv_slope   = float(np.polyfit(x, obv_vals,   1)[0])

        if price_slope < 0 and obv_slope > 0:
            # Confirm the divergence is meaningful (not noise)
            price_chg = (price_vals[-1] - price_vals[0]) / price_vals[0] * 100
            return True, (
                f"Price {price_chg:+.1f}% ({lookback}d) but OBV rising "
                f"= Smart money accumulating into weakness"
            )
        if price_slope < 0 and obv_slope < 0:
            return False, f"Price AND OBV both falling = distribution, not Spring"

        return False, ""
    except Exception:
        return False, ""


def _detect_bb_squeeze_spring(close: pd.Series, period: int = 20) -> Tuple[bool, float]:
    """
    Bollinger Band squeeze: bandwidth contracting to historically tight levels.
    Tight bands = energy coiling before explosive move.
    Spring setups often fire from a BB squeeze into support.
    """
    try:
        mid   = close.rolling(period).mean()
        std   = close.rolling(period).std()
        upper = mid + 2 * std
        lower = mid - 2 * std
        bw    = ((upper - lower) / mid).dropna()

        if len(bw) < 60:
            return False, 0.0

        current_bw = float(bw.iloc[-1])
        hist_bw    = float(bw.iloc[-120:].mean()) if len(bw) >= 120 else float(bw.mean())
        bw_pct     = round(current_bw * 100, 1)

        # Squeeze = current bandwidth < 60% of historical average
        is_squeeze = current_bw < hist_bw * 0.60
        return is_squeeze, bw_pct
    except Exception:
        return False, 0.0


def _calc_spring_score(stopping_vol: bool, obv_div: bool, bb_squeeze: bool,
                        near_support: bool, rs_score: float, ad_div: bool,
                        vol_ratio: float, rsi: float, regime: str) -> Tuple[int, str, List[str]]:
    """
    Spring Score 0-10:
    3 pts  — Stopping volume (highest weight, hardest to fake)
    2 pts  — OBV divergence (price down, smart money up)
    2 pts  — A/D line divergence (regime.py signal)
    1 pt   — BB squeeze (coiling energy)
    1 pt   — Near major support (location)
    1 pt   — RS > 1.0 in bear/panic (relative strength = institutional backing)
    """
    score = 0
    evidence = []

    if stopping_vol:
        score += 3
        evidence.append("Stopping volume detected")
    if obv_div:
        score += 2
        evidence.append("OBV/CVD bullish divergence")
    if ad_div:
        score += 2
        evidence.append("A/D line divergence (smart money)")
    if bb_squeeze:
        score += 1
        evidence.append("BB squeeze — energy coiling")
    if near_support:
        score += 1
        evidence.append("Within 3% of major support")
    if rs_score > 1.0 and regime in ("BEAR", "PANIC"):
        score += 1
        evidence.append(f"RS {rs_score:.2f} — outperforming IHSG during dip")

    if score >= 8:
        label = "🔥 SPRING TRIGGERED"
    elif score >= 5:
        label = "👀 WATCH — ACCUMULATION FORMING"
    elif score >= 3:
        label = "📍 NEAR SUPPORT — MONITORING"
    else:
        label = "➖ NO SPRING SIGNAL"

    return score, label, evidence


# ─────────────────────────────────────────────
# UNIVERSE FILTER
# ─────────────────────────────────────────────

NOTASI_KHUSUS_BLACKLIST: set = set()

def _passes_universe_filter(ticker: str, price: float,
                             avg_vol: float, daily_turnover: float) -> Tuple[bool, str]:
    if ticker in NOTASI_KHUSUS_BLACKLIST:
        return False, "Notasi Khusus — legal/financial risk"
    if price < 200:
        return False, f"Price {_rp(price)} di bawah Rp 200 (gocap risk)"
    if avg_vol < 500_000:
        return False, f"Avg volume {avg_vol/1e6:.2f}M terlalu kecil (illiquid)"
    if daily_turnover < 5_000_000_000:
        return False, f"Daily turnover Rp {daily_turnover/1e9:.1f}B < Rp 5B minimum (exit liquidity risk)"
    return True, ""


# ─────────────────────────────────────────────
# 0-100 SCORING ENGINE
# ─────────────────────────────────────────────

def _calc_score(
    regime: str,
    rsi: float, adx: float,
    ema8: float, ema21: float, ema50: float, ema200: float, price: float,
    vol_ratio: float, vol_signal: str,
    rs_score: float, ad_divergence: bool,
    bb_lower: float, bb_mid: float,
    roe: Optional[float], der: Optional[float], pbv: Optional[float],
    rev_g: Optional[float], is_lq45: bool,
    rumor_flags: List[str], silent_accum: bool,
    cmet: List[str], cfail: List[str]
) -> Tuple[int, str, str]:
    """
    Score 0-100 across 5 dimensions (20 pts each):
    1. Trend/Regime Alignment (20)
    2. Momentum Indicators   (20)
    3. Volume/Flow           (20)
    4. Fundamental Quality   (20)
    5. Catalyst/Alpha        (20)
    """
    score = 0

    # ── 1. Trend / Regime Alignment (20 pts) ──
    if regime in ("BULL", "SIDEWAYS"):
        if ema8 > ema21:              score += 7;  cmet.append("EMA8 > EMA21 (uptrend) ✓")
        else:                                       cfail.append("EMA8 < EMA21 (no uptrend)")
        if price > ema50:             score += 7;  cmet.append(f"Price > EMA50 {_rp(ema50)} ✓")
        else:                                       cfail.append(f"Price < EMA50 {_rp(ema50)}")
        if price > ema200:            score += 6;  cmet.append(f"Price > EMA200 {_rp(ema200)} ✓")
        else:                                       cfail.append(f"Price < EMA200 {_rp(ema200)}")
    else:  # BEAR / PANIC — mean reversion scoring
        if price < bb_lower:          score += 10; cmet.append(f"Price below Bollinger Lower {_rp(bb_lower)} (oversold) ✓")
        elif price < bb_mid:          score += 5;  cmet.append("Price below BB midline ✓")
        else:                                       cfail.append("Price above BB mid (not oversold enough)")
        if rs_score > 1.2:            score += 10; cmet.append(f"RS Score {rs_score:.2f} > 1.2 (outperforming IHSG) ✓")
        elif rs_score > 0.8:          score += 5;  cmet.append(f"RS Score {rs_score:.2f} moderate")
        else:                                       cfail.append(f"RS Score {rs_score:.2f} underperforming IHSG")

    # ── 2. Momentum Indicators (20 pts) ────────
    if regime in ("BULL", "SIDEWAYS"):
        if 50 <= rsi <= 70:           score += 10; cmet.append(f"RSI {rsi:.1f} in buy zone 50–70 ✓")
        elif 45 <= rsi < 50:          score += 5;  cmet.append(f"RSI {rsi:.1f} near buy zone")
        else:                                       cfail.append(f"RSI {rsi:.1f} outside 50–70")
        if adx > 25:                  score += 10; cmet.append(f"ADX {adx:.1f} trending (>25) ✓")
        else:                                       cfail.append(f"ADX {adx:.1f} weak trend")
    else:  # mean reversion
        if rsi < 35:                  score += 15; cmet.append(f"RSI {rsi:.1f} oversold <35 (mean revert setup) ✓")
        elif rsi < 45:                score += 8;  cmet.append(f"RSI {rsi:.1f} approaching oversold")
        else:                                       cfail.append(f"RSI {rsi:.1f} not oversold enough for mean reversion")
        if is_lq45 and rsi < 35:      score += 5;  cmet.append("LQ45 Blue Chip oversold = institutional discount ✓")

    # ── 3. Volume / Flow (20 pts) ──────────────
    if vol_ratio >= 5.0:              score += 20; cmet.append(f"Volume {vol_ratio:.1f}x avg — EXPLOSIVE ✓")
    elif vol_ratio >= 3.0:            score += 15; cmet.append(f"Volume {vol_ratio:.1f}x avg — Strong ✓")
    elif vol_ratio >= 1.5:            score += 8;  cmet.append(f"Volume {vol_ratio:.1f}x avg — Above avg ✓")
    else:                                           cfail.append(f"Volume {vol_ratio:.1f}x avg — Weak")

    if ad_divergence:                 score += 0   # handled separately as bonus below
    if silent_accum:                  score += 5;  cmet.append("Silent accumulation detected (high vol + narrow range) ✓")

    # ── 4. Fundamental Quality (20 pts) ────────
    fund = 0
    if roe and roe > 0.20:            fund += 6;   cmet.append(f"ROE {roe*100:.1f}% excellent (>20%) ✓")
    elif roe and roe > 0.15:          fund += 4;   cmet.append(f"ROE {roe*100:.1f}% good (>15%) ✓")
    elif roe:                                       cfail.append(f"ROE {roe*100:.1f}% below 15%")

    if der and der < 0.8:             fund += 5;   cmet.append(f"DER {der:.2f}x strong balance sheet ✓")
    elif der and der < 1.5:           fund += 3;   cmet.append(f"DER {der:.2f}x acceptable ✓")
    elif der:                                       cfail.append(f"DER {der:.2f}x highly leveraged")

    if pbv and 0.8 <= pbv <= 2.5:     fund += 5;   cmet.append(f"PBV {pbv:.2f}x fair value ✓")
    elif pbv and pbv < 0.8:           fund += 3;   cmet.append(f"PBV {pbv:.2f}x potential value ✓")
    elif pbv:                                       cfail.append(f"PBV {pbv:.2f}x expensive")

    if rev_g and rev_g > 0.15:        fund += 4;   cmet.append(f"Revenue growth {rev_g*100:.1f}% strong ✓")
    elif rev_g and rev_g > 0.08:      fund += 2;   cmet.append(f"Revenue growth {rev_g*100:.1f}% moderate ✓")
    elif rev_g:                                     cfail.append(f"Revenue growth {rev_g*100:.1f}% weak")
    score += min(fund, 20)

    # ── 5. Catalyst / Alpha (20 pts) ───────────
    if rumor_flags:                   score += 15; cmet.append(f"Catalyst detected: {', '.join(rumor_flags[:2])} ✓")
    if silent_accum and vol_ratio >= 5:score += 5; cmet.append("Explosive silent accumulation = bandar front-running ✓")

    # ── A/D Divergence Bonus (up to +10) ───────
    if ad_divergence:                 score = min(100, score + 10)

    score = max(0, min(100, score))

    # Label
    if score >= 80:
        label    = "🔥 HIGH CONVICTION — Institutional Accumulation Detected"
        play     = "Momentum Play" if regime in ("BULL", "SIDEWAYS") else "Institutional Discount"
    elif score >= 60:
        label    = "⚡ MODERATE — Worth Watching"
        play     = "VCP Setup" if adx > 20 else "Mean Reversion"
    elif score >= 40:
        label    = "🟡 LOW-MODERATE — Weak Setup"
        play     = "Speculative / Rumor Play" if rumor_flags else "Watch Only"
    else:
        label    = "❌ LOW INTEREST — Avoid"
        play     = "Avoid"

    if rumor_flags and score >= 50:
        play = "Rumor Play 🎯"
    if is_lq45 and rsi < 35 and regime in ("BEAR", "PANIC"):
        play = "Institutional Discount 🏦"

    return score, label, play


# ─────────────────────────────────────────────
# MAIN ANALYSIS
# ─────────────────────────────────────────────

_ihsg_cache: Optional[pd.DataFrame] = None
_ihsg_cache_time: Optional[datetime] = None

def _get_ihsg_df() -> pd.DataFrame:
    global _ihsg_cache, _ihsg_cache_time
    now = datetime.utcnow()
    if (_ihsg_cache is not None and _ihsg_cache_time is not None
            and (now - _ihsg_cache_time).seconds < 3600):
        return _ihsg_cache
    try:
        df = yf.Ticker("^JKSE").history(period="6mo", interval="1d")
        _ihsg_cache      = df
        _ihsg_cache_time = now
        return df
    except Exception:
        return pd.DataFrame()


def analyse_ticker(ticker: str, company_name: str = "",
                   regime_result: Optional[RegimeResult] = None) -> Optional[TechnicalResult]:
    symbol      = f"{ticker}.JK" if not ticker.endswith(".JK") else ticker
    ticker_clean = ticker.replace(".JK", "")
    result      = TechnicalResult(ticker=ticker_clean,
                                  company_name=company_name or ticker_clean)

    try:
        # ── Fetch data ───────────────────────────
        tk   = yf.Ticker(symbol)
        df   = tk.history(period="1y", interval="1d")
        info = {}
        try:
            info = tk.info or {}
        except Exception:
            pass

        if df is None or len(df) < 30:
            result.is_rejected      = True
            result.rejection_reason = "Data tidak cukup (<30 hari)"
            return result

        close  = df["Close"]
        volume = df["Volume"]
        price  = float(close.iloc[-1])
        prev   = float(close.iloc[-2]) if len(close) > 1 else price

        # ── Volume & liquidity ────────────────────
        avg_vol      = float(volume.rolling(20).mean().iloc[-1])
        today_vol    = float(volume.iloc[-1])
        vol_ratio    = today_vol / avg_vol if avg_vol > 0 else 0
        daily_turnover = today_vol * price

        # ── Universe filter ───────────────────────
        ok, reason = _passes_universe_filter(ticker_clean, price, avg_vol, daily_turnover)
        if not ok:
            result.is_rejected      = True
            result.rejection_reason = reason
            return result

        # ── ARA/ARB check ────────────────────────
        at_arb, at_ara, arb_msg = _check_arb_ara(price, prev)

        # ── Technicals ────────────────────────────
        ema8   = float(close.ewm(span=8).mean().iloc[-1])
        ema21  = float(close.ewm(span=21).mean().iloc[-1])
        ema50  = float(close.ewm(span=50).mean().iloc[-1])
        ema200 = float(close.ewm(span=200).mean().iloc[-1])
        rsi    = _calc_rsi(close)
        adx    = _calc_adx(df)
        atr    = _calc_atr(df)
        bb_upper, bb_mid, bb_lower = _calc_bollinger(close)
        high20 = float(close.rolling(20).max().iloc[-1])
        low20  = float(close.rolling(20).min().iloc[-1])
        high6m = float(close.rolling(min(126, len(close))).max().iloc[-1])
        s1, s2, r1, r2 = _support_resistance(df)

        # ── RS Score ─────────────────────────────
        ihsg_df  = _get_ihsg_df()
        rs_score = calc_rs_score(ticker_clean, df, ihsg_df) if not ihsg_df.empty else 0.0

        # ── A/D Divergence ────────────────────────
        ad_div, ad_msg = detect_ad_divergence(df, lookback=20)

        # ── Volume signal ─────────────────────────
        day_range = float(df["High"].iloc[-1]) - float(df["Low"].iloc[-1])
        close_pos = ((price - float(df["Low"].iloc[-1])) / day_range) if day_range > 0 else 0.5
        if vol_ratio >= 2.0:
            vol_signal = ("accumulation" if close_pos >= 0.75
                          else "distribution" if close_pos <= 0.25
                          else "neutral_high_vol")
        else:
            vol_signal = "neutral"

        silent_accum = _detect_silent_accumulation(df, vol_ratio)

        # ── Spring / Wyckoff Detection ────────────────────────────────────
        # Support proximity: within 3% of S1 or below lower BB
        near_major_support = False
        support_proximity_pct = 0.0
        if s1 > 0:
            support_proximity_pct = (price - s1) / s1 * 100
            near_major_support    = support_proximity_pct <= 3.0
        elif price <= bb_lower * 1.03:
            near_major_support    = True
            support_proximity_pct = (price - bb_lower) / bb_lower * 100

        stopping_vol, stopping_vol_detail = _detect_stopping_volume(df, avg_vol, atr, near_major_support)
        obv_div,      obv_detail          = _detect_obv_divergence(close, volume, lookback=15)
        bb_spr,       bb_bw               = _detect_bb_squeeze_spring(close)
        spring_score, spring_label, spring_evidence = _calc_spring_score(
            stopping_vol, obv_div, bb_spr, near_major_support,
            rs_score, ad_div, vol_ratio, rsi, regime
        )

        # ── Fundamentals ──────────────────────────
        roe   = info.get("returnOnEquity")
        der_r = info.get("debtToEquity")
        der   = der_r / 100 if der_r else None
        pbv   = info.get("priceToBook")
        pe    = info.get("trailingPE")
        rev_g = info.get("revenueGrowth")
        mcap  = float(info.get("marketCap", 0) or 0)
        is_lq45 = ticker_clean in LQ45_TICKERS

        # ── Regime ────────────────────────────────
        if regime_result is None:
            regime_result = detect_regime()
        regime   = regime_result.regime
        strategy = regime_result.strategy

        # ── News & rumors ─────────────────────────
        news, rumor_flags, cong_flag = _fetch_news_and_rumors(ticker_clean)

        # ── Scoring ───────────────────────────────
        cmet, cfail = [], []
        total_score, score_label, play_type = _calc_score(
            regime, rsi, adx, ema8, ema21, ema50, ema200, price,
            vol_ratio, vol_signal, rs_score, ad_div,
            bb_lower, bb_mid,
            roe, der, pbv, rev_g, is_lq45,
            rumor_flags, silent_accum,
            cmet, cfail
        )

        passed = total_score >= 60

        # Spring setups override passed threshold — even a score of 45+ qualifies
        # if spring_score is high, to avoid filtering out pre-breakout setups
        if spring_score >= 5 and total_score >= 40:
            passed = True

        # ── Trade levels ──────────────────────────
        if spring_score >= 5 and near_major_support:
            # Spring entry: buy near support, stop BELOW support (not just ATR-based)
            # This gives a tighter stop and better R:R than a generic ATR stop
            hard_support  = s1 if s1 > 0 else bb_lower
            entry_low     = price * 0.99          # slight limit below current
            entry_high    = price * 1.01
            stop_loss     = hard_support * 0.97   # 3% below support = below any wick
            risk          = price - stop_loss
            # Targets: next resistance levels
            t1 = r1 if r1 > price else price + risk * 2.0
            t2 = r2 if r2 > t1   else price + risk * 3.5
            t3 = price + risk * 5.5
        elif regime in ("BULL", "SIDEWAYS"):
            # Momentum entry
            entry_low  = price
            entry_high = price * 1.02
            stop_loss  = max(s1 * 0.98, price - 1.5 * atr) if s1 > 0 else price - 1.5 * atr
        else:
            # Mean reversion entry — limit at -1 SD from 20d mean
            entry_low  = bb_lower
            entry_high = bb_mid * 0.99
            stop_loss  = bb_lower - 1.5 * atr

        risk  = max(price - stop_loss, price * 0.02)
        t1    = price + risk * 1.5
        t2    = price + risk * 2.5
        t3    = price + risk * 4.0
        rr    = (t2 - price) / risk if risk > 0 else 0

        # Lot size (100 shares per lot)
        lot_size = max(1, int(10_000_000 / (price * 100))) if price > 0 else 1

        # ── Entry trigger ─────────────────────────
        if spring_score >= 8:
            trigger = (
                f"Trigger 1 (Reversal): 4H close above {_rp(high20)} (prev day high) "
                f"with vol > 1.2x avg\n"
                f"Trigger 2 (Squeeze): If BB squeeze active, first 15m candle "
                f"breaking above upper BB {_rp(bb_upper)}"
            )
        elif spring_score >= 5:
            trigger = (
                f"Set alert at {_rp(s1 * 1.03 if s1 > 0 else price)} — "
                f"wait for stopping volume (vol >{avg_vol*2/1e6:.1f}M) "
                f"+ CVD/OBV turning positive before entry. "
                f"Trigger: 4H close above {_rp(high20)} on vol > 1.2x avg."
            )
        elif regime in ("BULL", "SIDEWAYS"):
            trigger = (f"Price > {_rp(high20)} (20d high) "
                       f"AND Vol > 2x avg "
                       f"AND RSI 50–70 "
                       f"AND IHSG > EMA50")
        elif play_type == "Rumor Play 🎯":
            trigger = (f"Price breaks {_rp(high6m)} (6-month resistance) "
                       f"AND Vol > 5x avg "
                       f"AND confirmed rumor keyword")
        else:
            trigger = (f"Price ≤ {_rp(bb_lower)} (lower Bollinger) "
                       f"AND RSI < 35 "
                       f"AND RS Score > 1.2 "
                       f"AND foreign net buy 3-day trend")

        # ── Verdict ───────────────────────────────
        if at_arb:
            verdict        = "AVOID"
            verdict_reason = f"ARB risk — falling knife. {arb_msg}"
        elif spring_score >= 8:
            verdict        = "BUY"
            verdict_reason = f"Spring Triggered — {spring_label}"
        elif spring_score >= 5:
            verdict        = "WATCH"
            verdict_reason = f"Accumulation forming — {spring_label}"
        elif total_score >= 80:
            verdict        = "BUY"
            verdict_reason = score_label
        elif total_score >= 60:
            verdict        = "WATCH"
            verdict_reason = f"{score_label} — wait for entry trigger"
        else:
            verdict        = "AVOID"
            verdict_reason = score_label

        # ── Thesis ────────────────────────────────
        buy_thesis, bear_case, invalidation = _build_thesis(
            ticker_clean, price, regime, rsi, ema50, ema200,
            vol_signal, vol_ratio, roe, der, pbv,
            rs_score, ad_div, ad_msg,
            rumor_flags, cong_flag, news, is_lq45,
            s1, r1, atr
        )

        # ── Populate result ───────────────────────
        result.current_price    = price
        result.regime           = regime
        result.strategy         = strategy
        result.total_score      = total_score
        result.score_label      = score_label
        result.play_type        = play_type
        result.ema8             = ema8
        result.ema21            = ema21
        result.ema50            = ema50
        result.ema200           = ema200
        result.rsi              = rsi
        result.adx              = adx
        result.atr              = atr
        result.bb_upper         = bb_upper
        result.bb_lower         = bb_lower
        result.bb_mid           = bb_mid
        result.rs_score         = rs_score
        result.ad_divergence    = ad_div
        result.ad_divergence_msg= ad_msg
        result.today_volume     = today_vol
        result.avg_volume_20d   = avg_vol
        result.volume_ratio     = vol_ratio
        result.volume_signal    = vol_signal
        result.daily_turnover   = daily_turnover
        result.is_silent_accum  = silent_accum
        result.support_1        = s1
        result.support_2        = s2
        result.resistance_1     = r1
        result.resistance_2     = r2
        result.high_20d         = high20
        result.low_20d          = low20
        result.high_6m          = high6m
        result.at_arb           = at_arb
        result.at_ara           = at_ara
        result.arb_warning      = arb_msg
        result.entry_low        = entry_low
        result.entry_high       = entry_high
        result.stop_loss        = stop_loss
        result.t1               = t1
        result.t2               = t2
        result.t3               = t3
        result.rr_ratio         = rr
        result.lot_size         = lot_size
        result.roe              = roe
        result.der              = der
        result.pbv              = pbv
        result.pe_ratio         = pe
        result.revenue_growth   = rev_g
        result.market_cap       = mcap
        result.is_lq45          = is_lq45
        result.conditions_met   = cmet
        result.conditions_failed= cfail
        result.passed           = passed
        result.recent_news      = news
        result.rumor_flags      = rumor_flags
        result.conglomerate_flag= cong_flag
        result.buy_thesis       = buy_thesis
        result.bear_case        = bear_case
        result.invalidation     = invalidation
        result.entry_trigger    = trigger
        result.verdict          = verdict
        result.verdict_reason   = verdict_reason

        # Spring fields
        result.spring_score           = spring_score
        result.spring_label           = spring_label
        result.stopping_volume        = stopping_vol
        result.stopping_volume_detail = stopping_vol_detail
        result.obv_diverging          = obv_div
        result.obv_detail             = obv_detail
        result.bb_squeeze_spring      = bb_spr
        result.bb_bandwidth_pct       = bb_bw
        result.near_major_support     = near_major_support
        result.support_proximity_pct  = support_proximity_pct
        result.spring_accum_evidence  = spring_evidence

        return result

    except Exception as e:
        logger.error("analyse_ticker %s: %s", ticker, e, exc_info=True)
        result.is_rejected      = True
        result.rejection_reason = f"Data error: {str(e)[:100]}"
        return result


# ─────────────────────────────────────────────
# THESIS BUILDER
# ─────────────────────────────────────────────

def _build_thesis(ticker, price, regime, rsi, ema50, ema200,
                  vol_signal, vol_ratio, roe, der, pbv,
                  rs_score, ad_div, ad_msg,
                  rumor_flags, cong_flag, news, is_lq45,
                  s1, r1, atr) -> Tuple[str, str, str]:

    bull = []
    if ad_div:
        bull.append(f"🚨 SMART MONEY: {ad_msg}")
    if rs_score > 1.2:
        bull.append(f"RS Score {rs_score:.2f} — outperforming IHSG during weakness (institutional support)")
    if vol_signal == "accumulation" and vol_ratio >= 3:
        bull.append(f"Volume {vol_ratio:.1f}x avg closing near high — bandar accumulating aggressively")
    if regime in ("BEAR", "PANIC") and rsi < 35 and is_lq45:
        bull.append("LQ45 blue chip at institutional discount — historically mean-reverting asset")
    if roe and roe > 0.18:
        bull.append(f"ROE {roe*100:.1f}% — high quality business compounding above cost of capital")
    if der and der < 0.8:
        bull.append(f"DER {der:.2f}x — strong balance sheet, minimal USD debt risk")
    if rumor_flags:
        bull.append(f"Catalyst rumor: {', '.join(rumor_flags)} — 'buy rumor' setup")
    if cong_flag:
        bull.append(cong_flag)
    if news:
        bull.append(f"Recent news: {news[0][:80]}")

    buy_thesis = "\n".join(f"• {p}" for p in bull) if bull else "• Insufficient bullish signals for thesis"

    bear = []
    if regime in ("BEAR", "PANIC"):
        bear.append("IHSG dalam tren turun — market risk bisa overwhelm stock-specific story")
    if vol_signal == "distribution":
        bear.append(f"Volume {vol_ratio:.1f}x avg tapi close near low — kemungkinan bandar distributing")
    if der and der > 1.5:
        bear.append(f"DER {der:.2f}x — risiko currency mismatch jika rupiah melemah vs USD")
    if pbv and pbv > 3.5:
        bear.append(f"PBV {pbv:.2f}x — valuasi mahal, butuh execution sempurna untuk justify")
    if rsi > 75:
        bear.append(f"RSI {rsi:.1f} — overbought, risiko pullback sebelum next leg up")

    # Spring-specific bear cases (the honest "how you lose money" section)
    bear.append("Stopping volume bisa menjadi false signal — 50% of the time big volume near support adalah CAPITULATION, bukan absorption. Bedanya: capitulation = wide candle spread. Absorption = narrow spread.")
    bear.append("Spring tanpa catalyst fundamental = dead cat bounce. Harga bisa balik ke support lagi setelah 5-15% bounce.")
    bear.append("IDX mid-cap bisa gap down 5-15% tanpa warning jika bad news atau foreign outflow masif.")
    bear.append("Foreign outflow masif bisa crush saham terbaik sekalipun (lihat 2018, 2020, 2022).")

    bear_case = "\n".join(f"• {p}" for p in bear)

    # Spring-specific invalidation conditions
    hard_support = s1
    stop_price   = hard_support * 0.97 if hard_support > 0 else price - 2 * atr
    invalidation = (
        f"• Harga TUTUP di bawah S1 {_rp(hard_support)} dengan volume tinggi "
        f"(ini bukan dip, ini breakdown)\n"
        f"• Volume sepi <0.5x avg selama 3 hari berturut-turut setelah Spring "
        f"(bandar tidak follow through = signal palsu)\n"
        f"• OBV berbalik negatif setelah Spring candle "
        f"(distribusi tersembunyi)\n"
        f"• RS Score turun di bawah 0.8 — stock mulai underperform IHSG "
        f"(institutional support hilang)\n"
        f"• IHSG break di bawah EMA200 secara decisive "
        f"(regime shift ke full panic)\n"
        f"• Disclosure negatif: OJK inquiry, going concern, fraud allegation, "
        f"rights issue for debt repayment"
    )

    return buy_thesis, bear_case, invalidation


# ─────────────────────────────────────────────
# FORMATTER
# ─────────────────────────────────────────────

def format_full_analysis(t: TechnicalResult) -> str:
    rp  = _rp

    verdict_emoji = {"BUY": "🟢", "WATCH": "🟡", "AVOID": "🔴"}.get(t.verdict, "⚪")
    regime_emoji  = {"BULL": "🟢", "SIDEWAYS": "🟡", "BEAR": "🟠", "PANIC": "🔴", "UNKNOWN": "⚪"}.get(t.regime, "⚪")
    vol_emoji     = {"accumulation": "🐋 ACCUMULATION", "distribution": "🚨 DISTRIBUTION",
                     "neutral": "➖ NEUTRAL", "neutral_high_vol": "⚠️ HIGH VOL NEUTRAL"}.get(t.volume_signal, "➖")

    score_bar = "█" * (t.total_score // 10) + "░" * (10 - t.total_score // 10)

    lines = [
        f"{'━'*34}",
        f"{verdict_emoji} *${t.ticker}*  |  {t.verdict}",
        f"_{t.verdict_reason}_",
        f"{'━'*34}",
        "",
        f"🎯 *SCORE: {t.total_score}/100*",
        f"`[{score_bar}]`",
        f"_{t.score_label}_",
        f"",
        f"📌 *Play Type:* {t.play_type}",
        f"{regime_emoji} *Regime:* {t.regime} — _{t.strategy}_",
        f"💰 *Price:* {rp(t.current_price)}",
        f"🏦 *Market Cap:* {'Rp ' + f'{t.market_cap/1e12:.2f}T' if t.market_cap > 1e12 else 'Rp ' + f'{t.market_cap/1e9:.0f}B' if t.market_cap else 'N/A'}",
        f"{'🏅 LQ45 Blue Chip' if t.is_lq45 else ''}",
        "",
    ]

    if t.at_arb:
        lines += [f"⛔ *{t.arb_warning}*", ""]
    if t.at_ara:
        lines += [f"⚠️ *{t.arb_warning}*", ""]
    if t.ad_divergence:
        lines += [f"🚨 *{t.ad_divergence_msg}*", ""]

    lines += [
        "📊 *TECHNICAL*",
        f"EMA 8/21: {rp(t.ema8)} / {rp(t.ema21)}  {'✅ bullish' if t.ema8 > t.ema21 else '❌ bearish'}",
        f"EMA 50/200: {rp(t.ema50)} / {rp(t.ema200)}",
        f"BB: {rp(t.bb_upper)} / {rp(t.bb_mid)} / {rp(t.bb_lower)}",
        f"RSI: {t.rsi:.1f}  ADX: {t.adx:.1f}  ATR: {rp(t.atr)}",
        f"RS Score vs IHSG: *{t.rs_score:.2f}* {'✅ outperforming' if t.rs_score > 1.2 else '❌ underperforming'}",
        "",
        "📐 *SUPPORT & RESISTANCE*",
        f"R2: {rp(t.resistance_2)}  R1: {rp(t.resistance_1)}",
        f"Price ➤ {rp(t.current_price)}",
        f"S1: {rp(t.support_1)}  S2: {rp(t.support_2)}",
        f"20d High/Low: {rp(t.high_20d)} / {rp(t.low_20d)}",
        f"6m High (resistance): {rp(t.high_6m)}",
        "",
        f"{vol_emoji}",
        f"Volume: {t.today_volume/1e6:.1f}M  |  20d Avg: {t.avg_volume_20d/1e6:.1f}M  |  Ratio: *{t.volume_ratio:.1f}x*",
        f"Daily Turnover: Rp {t.daily_turnover/1e9:.1f}B",
        f"{'🎯 SILENT ACCUMULATION DETECTED' if t.is_silent_accum else ''}",
        "",
        "💹 *FUNDAMENTALS*",
        f"ROE: {'%s%%' % f'{t.roe*100:.1f}' if t.roe else 'N/A'}  "
        f"DER: {f'{t.der:.2f}x' if t.der else 'N/A'}  "
        f"PBV: {f'{t.pbv:.2f}x' if t.pbv else 'N/A'}  "
        f"PE: {f'{t.pe_ratio:.1f}x' if t.pe_ratio else 'N/A'}",
        f"Rev Growth: {f'{t.revenue_growth*100:.1f}%' if t.revenue_growth else 'N/A'}",
        "",
        "🎯 *TRADE PLAN*",
        f"Setup: {t.play_type}",
        f"Entry Zone: {rp(t.entry_low)} – {rp(t.entry_high)}",
        f"Stop Loss: {rp(t.stop_loss)} (–{abs((t.stop_loss-t.current_price)/t.current_price*100):.1f}%)",
        f"T1: {rp(t.t1)} (+{(t.t1/t.current_price-1)*100:.1f}%)",
        f"T2: {rp(t.t2)} (+{(t.t2/t.current_price-1)*100:.1f}%)",
        f"T3: {rp(t.t3)} (+{(t.t3/t.current_price-1)*100:.1f}%)",
        f"R:R Ratio: 1:{t.rr_ratio:.1f}",
        f"Lot Size (Rp 10M position): {t.lot_size} lots ({t.lot_size*100:,} shares)",
        f"Time Stop: Exit jika <+5% dalam 10 hari trading",
        "",
    ]

    if t.play_type == "Rumor Play 🎯":
        lines += [
            "📣 *RUMOR PLAY RULES*",
            "• Entry: Buy on breakout above 6-month resistance",
            "• Exit 70%: Saat news masuk IDNFinancials / IDX disclosure",
            "• Hold 30%: Trailing stop 7% dari high",
            "• Risk: 'Sell the news' bisa brutal — size kecil",
            "",
        ]
    elif "Institutional Discount" in t.play_type:
        lines += [
            "🏦 *INSTITUTIONAL DISCOUNT RULES*",
            f"• Entry: Limit order di {rp(t.bb_lower)} (lower BB)",
            f"• TP: EMA20 {rp(t.bb_mid)}",
            f"• Stop: 1.5x ATR = {rp(t.stop_loss)}",
            "• Foreign flow net buy 3-day = confirmation",
            "",
        ]

    lines += [
        "⚡ *ENTRY TRIGGER*",
        f"_{t.entry_trigger}_",
        "",
        "🐂 *BULL CASE*",
        t.buy_thesis,
        "",
        "🐻 *BEAR CASE (How I lose money)*",
        t.bear_case,
        "",
        "❌ *INVALIDATION*",
        t.invalidation,
    ]

    if t.rumor_flags:
        lines += ["", f"🔥 *CATALYST FLAGS:* {', '.join(t.rumor_flags)}"]
    if t.conglomerate_flag:
        lines += [t.conglomerate_flag]
    if t.recent_news:
        lines += ["", "📰 *NEWS / RUMORS*"]
        for n in t.recent_news[:4]:
            lines.append(f"• _{n[:100]}_")

    cmet_str  = "\n".join(f"  ✅ {c}" for c in t.conditions_met[:6])
    cfail_str = "\n".join(f"  ❌ {c}" for c in t.conditions_failed[:4])
    lines += [
        "",
        f"*CONDITIONS MET ({len(t.conditions_met)})*",
        cmet_str,
        f"*CONDITIONS FAILED ({len(t.conditions_failed)})*",
        cfail_str,
    ]

    return "\n".join(l for l in lines if l is not None)


# ─────────────────────────────────────────────
# BATCH + MARKET HEALTH
# ─────────────────────────────────────────────

def analyse_tickers_batch(pairs: List[Tuple[str, str]],
                          regime_result: Optional[RegimeResult] = None) -> List[TechnicalResult]:
    if regime_result is None:
        regime_result = detect_regime()
    results = []
    for ticker, name in pairs:
        try:
            r = analyse_ticker(ticker, name, regime_result)
            if r:
                results.append(r)
        except Exception as e:
            logger.warning("Batch error %s: %s", ticker, e)

    # In bear/panic, spring score is the primary sort key
    # In bull/sideways, composite score is primary
    if regime_result.regime in ("BEAR", "PANIC"):
        results.sort(key=lambda x: (x.spring_score * 10 + x.total_score), reverse=True)
    else:
        results.sort(key=lambda x: x.total_score, reverse=True)

    return results


def is_market_healthy() -> Tuple[bool, str]:
    r = detect_regime()
    # No longer binary — always return True but with regime context
    msgs = {
        "BULL":    (True,  f"IHSG Bull — Trend following mode. Full scan aktif."),
        "SIDEWAYS":(True,  f"IHSG Sideways — VCP + selective momentum mode."),
        "BEAR":    (True,  f"IHSG Bear — Switching ke Mean Reversion + RS Hunt mode."),
        "PANIC":   (True,  f"IHSG Panic — Institutional Discount + RS Hunt. Size kecil."),
        "UNKNOWN": (True,  f"IHSG unknown — Defensive mode, lanjut dengan hati-hati."),
    }
    return msgs.get(r.regime, (True, "Market status unknown"))
