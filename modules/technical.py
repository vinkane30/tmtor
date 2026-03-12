"""
modules/technical.py — IDX High-Momentum Quality Analyzer
Full trade setup: entry, exit, thesis, risk, fundamentals, macro, news
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

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────

@dataclass
class TechnicalResult:
    ticker:            str
    company_name:      str = ""
    current_price:     float = 0.0
    
    # Technical
    ema8:              float = 0.0
    ema21:             float = 0.0
    ema50:             float = 0.0
    ema200:            float = 0.0
    rsi:               float = 0.0
    adx:               float = 0.0
    
    # Volume
    today_volume:      float = 0.0
    avg_volume_20d:    float = 0.0
    volume_ratio:      float = 0.0
    volume_signal:     str = ""       # "accumulation" | "distribution" | "neutral"
    
    # Price levels
    support_1:         float = 0.0
    support_2:         float = 0.0
    resistance_1:      float = 0.0
    resistance_2:      float = 0.0
    high_20d:          float = 0.0
    low_20d:           float = 0.0
    
    # Trade setup
    entry_low:         float = 0.0
    entry_high:        float = 0.0
    stop_loss:         float = 0.0
    t1:                float = 0.0
    t2:                float = 0.0
    t3:                float = 0.0
    rr_ratio:          float = 0.0
    
    # Fundamental
    roe:               Optional[float] = None
    der:               Optional[float] = None
    pbv:               Optional[float] = None
    revenue_growth:    Optional[float] = None
    market_cap:        Optional[float] = None
    pe_ratio:          Optional[float] = None
    
    # Scoring
    tech_score:        int = 0
    fund_score:        int = 0
    conditions_met:    List[str] = field(default_factory=list)
    conditions_failed: List[str] = field(default_factory=list)
    passed:            bool = False
    is_rejected:       bool = False
    rejection_reason:  str = ""
    
    # Macro & sentiment
    ihsg_trend:        str = ""       # "bullish" | "neutral" | "bearish"
    
    # News
    recent_news:       List[str] = field(default_factory=list)
    
    # Full thesis
    buy_thesis:        str = ""
    bear_case:         str = ""
    invalidation:      str = ""
    entry_trigger:     str = ""
    verdict:           str = ""       # "BUY" | "WATCH" | "AVOID"
    verdict_reason:    str = ""


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _rp(v: float) -> str:
    return f"Rp {v:,.0f}"

def _pct(v: float) -> str:
    return f"{v:+.1f}%"

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
        dx   = (100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan))
        return float(dx.rolling(period).mean().iloc[-1])
    except Exception:
        return 0.0

def _support_resistance(df: pd.DataFrame) -> Tuple[float, float, float, float]:
    """Pivot-based S/R using last 20 candles."""
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

def _fetch_news(ticker_clean: str) -> List[str]:
    """Fetch recent news from Kontan and Bisnis RSS."""
    headlines = []
    feeds = [
        f"https://rss.kontan.co.id/search/{ticker_clean}",
        "https://rss.kontan.co.id/category/bursa",
        "https://rss.bisnis.com/feed/rss2/market/emiten.rss",
    ]
    for url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                title = entry.get("title", "")
                if ticker_clean.upper() in title.upper() or len(headlines) < 3:
                    headlines.append(title)
                if len(headlines) >= 5:
                    break
        except Exception:
            pass
        if len(headlines) >= 5:
            break
    return headlines[:5]

def _ihsg_trend() -> str:
    """Check IHSG vs EMA50 and EMA200."""
    try:
        ihsg = yf.Ticker("^JKSE")
        df   = ihsg.history(period="1y", interval="1d")
        if df.empty:
            return "unknown"
        close  = df["Close"]
        ema50  = float(close.ewm(span=50).mean().iloc[-1])
        ema200 = float(close.ewm(span=200).mean().iloc[-1])
        price  = float(close.iloc[-1])
        if price > ema50 and price > ema200:
            return "bullish"
        elif price > ema200:
            return "neutral"
        else:
            return "bearish"
    except Exception:
        return "unknown"


# ─────────────────────────────────────────────
# UNIVERSE FILTER
# ─────────────────────────────────────────────

NOTASI_KHUSUS_BLACKLIST = set()  # populate from config or daily IDX file

def _passes_universe_filter(ticker: str, price: float,
                             avg_vol: float, avg_val: float,
                             market_cap: float) -> Tuple[bool, str]:
    if ticker in NOTASI_KHUSUS_BLACKLIST:
        return False, "Notasi Khusus — legal/financial risk"
    if price < 200:
        return False, f"Price {_rp(price)} below Rp 200 safety floor (gocap risk)"
    if avg_vol < 500_000:
        return False, f"Avg volume {avg_vol/1e6:.2f}M below 500K minimum (illiquid)"
    if avg_val < 500_000_000:
        return False, f"Avg daily value Rp {avg_val/1e9:.1f}B below Rp 500M minimum"
    return True, ""


# ─────────────────────────────────────────────
# CORE ANALYSIS
# ─────────────────────────────────────────────

def analyse_ticker(ticker: str, company_name: str = "") -> Optional[TechnicalResult]:
    symbol = f"{ticker}.JK" if not ticker.endswith(".JK") else ticker
    ticker_clean = ticker.replace(".JK", "")
    result = TechnicalResult(ticker=ticker_clean, company_name=company_name or ticker_clean)

    try:
        tk   = yf.Ticker(symbol)
        df   = tk.history(period="1y", interval="1d")
        info = {}
        try:
            info = tk.info or {}
        except Exception:
            pass

        if df is None or len(df) < 30:
            result.is_rejected   = True
            result.rejection_reason = "Insufficient price history (<30 days)"
            return result

        close  = df["Close"]
        volume = df["Volume"]
        price  = float(close.iloc[-1])

        # ── Volume ──────────────────────────────
        avg_vol = float(volume.rolling(20).mean().iloc[-1])
        avg_val = avg_vol * price
        today_vol = float(volume.iloc[-1])
        vol_ratio = today_vol / avg_vol if avg_vol > 0 else 0

        # ── Universe filter ──────────────────────
        mcap = float(info.get("marketCap", 0) or 0)
        ok, reason = _passes_universe_filter(ticker_clean, price, avg_vol, avg_val, mcap)
        if not ok:
            result.is_rejected = True
            result.rejection_reason = reason
            return result

        # ── Technicals ───────────────────────────
        ema8   = float(close.ewm(span=8).mean().iloc[-1])
        ema21  = float(close.ewm(span=21).mean().iloc[-1])
        ema50  = float(close.ewm(span=50).mean().iloc[-1])
        ema200 = float(close.ewm(span=200).mean().iloc[-1])
        rsi    = _calc_rsi(close)
        adx    = _calc_adx(df)
        high20 = float(close.rolling(20).max().iloc[-1])
        low20  = float(close.rolling(20).min().iloc[-1])
        s1, s2, r1, r2 = _support_resistance(df)

        # ── Volume signal ─────────────────────────
        day_range = float(df["High"].iloc[-1]) - float(df["Low"].iloc[-1])
        close_pos = ((price - float(df["Low"].iloc[-1])) / day_range) if day_range > 0 else 0.5
        if vol_ratio >= 2.0:
            vol_signal = "accumulation" if close_pos >= 0.75 else \
                         "distribution" if close_pos <= 0.25 else "neutral_high_vol"
        else:
            vol_signal = "neutral"

        # ── Fundamentals ─────────────────────────
        roe  = info.get("returnOnEquity")
        der_raw = info.get("debtToEquity")
        der  = der_raw / 100 if der_raw else None   # yfinance returns %, convert to ratio
        pbv  = info.get("priceToBook")
        pe   = info.get("trailingPE")
        rev_g = info.get("revenueGrowth")

        # ── Scoring ──────────────────────────────
        cmet, cfail = [], []
        tech_score = 0

        def chk(cond, yes_msg, no_msg):
            nonlocal tech_score
            if cond:
                tech_score += 1
                cmet.append(yes_msg)
            else:
                cfail.append(no_msg)

        chk(ema8 > ema21,              f"EMA8 {_rp(ema8)} > EMA21 {_rp(ema21)} ✓",
                                        f"EMA8 < EMA21 (no uptrend)")
        chk(price > ema50,             f"Price above EMA50 {_rp(ema50)} ✓",
                                        f"Price below EMA50 {_rp(ema50)}")
        chk(price > ema200,            f"Price above EMA200 {_rp(ema200)} ✓",
                                        f"Price below EMA200 {_rp(ema200)}")
        chk(50 <= rsi <= 70,           f"RSI {rsi:.1f} in buy zone (50–70) ✓",
                                        f"RSI {rsi:.1f} outside 50–70 zone")
        chk(adx > 25,                  f"ADX {adx:.1f} trending (>25) ✓",
                                        f"ADX {adx:.1f} weak trend (<25)")
        chk(vol_ratio >= 1.5,          f"Volume {vol_ratio:.1f}x avg ✓",
                                        f"Volume {vol_ratio:.1f}x avg (need >1.5x)")
        chk(price >= high20 * 0.97,    f"Near/at 20-day high {_rp(high20)} ✓",
                                        f"Not near 20-day high {_rp(high20)}")

        fund_score = 0
        if roe and roe > 0.15:
            fund_score += 1; cmet.append(f"ROE {roe*100:.1f}% > 15% ✓")
        elif roe:
            cfail.append(f"ROE {roe*100:.1f}% < 15%")

        if der and der < 1.5:
            fund_score += 1; cmet.append(f"DER {der:.2f}x < 1.5x ✓")
        elif der:
            cfail.append(f"DER {der:.2f}x > 1.5x (leveraged)")

        if pbv and 0.8 <= pbv <= 3.0:
            fund_score += 1; cmet.append(f"PBV {pbv:.2f}x in fair range ✓")
        elif pbv:
            cfail.append(f"PBV {pbv:.2f}x outside fair range (0.8–3x)")

        if rev_g and rev_g > 0.10:
            fund_score += 1; cmet.append(f"Revenue growth {rev_g*100:.1f}% > 10% ✓")
        elif rev_g:
            cfail.append(f"Revenue growth {rev_g*100:.1f}% < 10%")

        # ── Trade levels ─────────────────────────
        entry_low  = price
        entry_high = price * 1.02
        stop_loss  = s1 * 0.98 if s1 > 0 else price * 0.94
        risk       = price - stop_loss
        t1         = price + risk * 1.5
        t2         = price + risk * 2.5
        t3         = price + risk * 4.0
        rr         = (t2 - price) / risk if risk > 0 else 0

        # ── Entry trigger text ────────────────────
        trigger = (
            f"Price > {_rp(high20)} (20d high breakout) "
            f"AND Volume > 2x avg "
            f"AND RSI 50–70 "
            f"AND IHSG > EMA50"
        )

        passed = tech_score >= config.MIN_TECHNICAL_COUNT

        # ── IHSG macro ────────────────────────────
        ihsg = _ihsg_trend()

        # ── News ──────────────────────────────────
        news = _fetch_news(ticker_clean)

        # ── Thesis generation ─────────────────────
        verdict, verdict_reason, buy_thesis, bear_case, invalidation = _generate_thesis(
            ticker_clean, price, tech_score, fund_score,
            roe, der, pbv, rev_g, pe,
            vol_signal, vol_ratio, rsi, adx,
            ema8, ema21, ema50, ema200,
            s1, r1, high20, ihsg, passed, news
        )

        result.current_price   = price
        result.ema8, result.ema21 = ema8, ema21
        result.ema50, result.ema200 = ema50, ema200
        result.rsi, result.adx = rsi, adx
        result.today_volume    = today_vol
        result.avg_volume_20d  = avg_vol
        result.volume_ratio    = vol_ratio
        result.volume_signal   = vol_signal
        result.support_1       = s1
        result.support_2       = s2
        result.resistance_1    = r1
        result.resistance_2    = r2
        result.high_20d        = high20
        result.low_20d         = low20
        result.entry_low       = entry_low
        result.entry_high      = entry_high
        result.stop_loss       = stop_loss
        result.t1, result.t2, result.t3 = t1, t2, t3
        result.rr_ratio        = rr
        result.roe, result.der = roe, der
        result.pbv, result.pe_ratio = pbv, pe
        result.revenue_growth  = rev_g
        result.market_cap      = mcap
        result.tech_score      = tech_score
        result.fund_score      = fund_score
        result.conditions_met  = cmet
        result.conditions_failed = cfail
        result.passed          = passed
        result.ihsg_trend      = ihsg
        result.recent_news     = news
        result.buy_thesis      = buy_thesis
        result.bear_case       = bear_case
        result.invalidation    = invalidation
        result.entry_trigger   = trigger
        result.verdict         = verdict
        result.verdict_reason  = verdict_reason

        return result

    except Exception as e:
        logger.error("analyse_ticker %s error: %s", ticker, e, exc_info=True)
        result.is_rejected     = True
        result.rejection_reason = f"Data error: {e}"
        return result


# ─────────────────────────────────────────────
# THESIS GENERATOR
# ─────────────────────────────────────────────

def _generate_thesis(
    ticker, price, tech_score, fund_score,
    roe, der, pbv, rev_g, pe,
    vol_signal, vol_ratio, rsi, adx,
    ema8, ema21, ema50, ema200,
    s1, r1, high20, ihsg, passed, news
) -> Tuple[str, str, str, str, str]:

    # Verdict
    total = tech_score + fund_score
    if passed and ihsg in ("bullish", "neutral") and vol_signal == "accumulation":
        verdict = "BUY"
        verdict_reason = f"Strong setup: {tech_score}/7 technical + {fund_score}/4 fundamental + big money accumulation"
    elif passed and ihsg == "bearish":
        verdict = "WATCH"
        verdict_reason = f"Good stock but IHSG bearish — wait for index to recover EMA200 before entry"
    elif tech_score >= 4:
        verdict = "WATCH"
        verdict_reason = f"{tech_score}/7 technical conditions met — not enough confluence yet"
    else:
        verdict = "AVOID"
        verdict_reason = f"Only {tech_score}/7 conditions met — no edge"

    # Buy thesis
    bull_points = []
    if ema8 > ema21:
        bull_points.append("EMA8 crossed above EMA21 — short-term momentum positive")
    if price > ema50:
        bull_points.append(f"Price holding above EMA50 {_rp(ema50)} — medium trend intact")
    if vol_signal == "accumulation":
        bull_points.append(f"Volume {vol_ratio:.1f}x avg with close near high — bandar accumulating")
    if roe and roe > 0.15:
        bull_points.append(f"ROE {roe*100:.1f}% — management generating real returns above cost of capital")
    if der and der < 1.0:
        bull_points.append(f"Low DER {der:.2f}x — balance sheet strong, minimal currency mismatch risk")
    if news:
        bull_points.append(f"Recent catalyst: {news[0][:80]}")

    buy_thesis = "\n".join(f"• {p}" for p in bull_points) if bull_points else "• Insufficient bullish signals"

    # Bear case
    bear_points = []
    if ihsg == "bearish":
        bear_points.append("IHSG below EMA200 — rising tide not lifting boats, index risk is real")
    if rsi > 70:
        bear_points.append(f"RSI {rsi:.1f} overbought — late entry, pullback likely before next leg")
    if der and der > 1.5:
        bear_points.append(f"DER {der:.2f}x — heavy debt, vulnerable if BI raises rates or IDR weakens")
    if vol_signal == "distribution":
        bear_points.append(f"High volume with close near low — possible bandar distributing (selling into strength)")
    if pbv and pbv > 3:
        bear_points.append(f"PBV {pbv:.2f}x — expensive, requires flawless execution to justify valuation")
    bear_points.append("Liquidity risk: IDX mid-caps can gap down 5–10% on bad news with no buyers")
    bear_points.append("Rupiah weakness vs USD can trigger foreign fund outflows across the board")

    bear_case = "\n".join(f"• {p}" for p in bear_points)

    # Invalidation
    invalidation = (
        f"• Price closes below support S1 {_rp(s1)} on high volume\n"
        f"• IHSG breaks below EMA200\n"
        f"• RSI drops below 40 (momentum failed)\n"
        f"• Volume dries up to <0.5x avg for 3+ days (bandar exited)\n"
        f"• Negative corporate disclosure or KPPU/OJK action"
    )

    return verdict, verdict_reason, buy_thesis, bear_case, invalidation


# ─────────────────────────────────────────────
# FORMATTER — Full Trade Setup Card
# ─────────────────────────────────────────────

def format_full_analysis(t: TechnicalResult) -> str:
    rp  = _rp
    pct = _pct

    verdict_emoji = {"BUY": "🟢", "WATCH": "🟡", "AVOID": "🔴"}.get(t.verdict, "⚪")
    ihsg_emoji    = {"bullish": "📈", "neutral": "➡️", "bearish": "📉", "unknown": "❓"}.get(t.ihsg_trend, "❓")
    vol_emoji     = {"accumulation": "🐋", "distribution": "🚨", "neutral": "➖", "neutral_high_vol": "⚠️"}.get(t.volume_signal, "➖")

    lines = [
        f"{'='*34}",
        f"{verdict_emoji} *${t.ticker}* — {t.verdict}",
        f"_{t.verdict_reason}_",
        f"{'='*34}",
        "",
        f"💰 *Price:* {rp(t.current_price)}",
        f"🏦 *Market Cap:* Rp {t.market_cap/1e12:.2f}T" if t.market_cap else "",
        f"{ihsg_emoji} *IHSG Macro:* {t.ihsg_trend.upper()}",
        "",
        "📊 *TECHNICAL SETUP*",
        f"Score: {t.tech_score}/7 conditions",
        f"EMA8/21/50/200: {rp(t.ema8)} / {rp(t.ema21)} / {rp(t.ema50)} / {rp(t.ema200)}",
        f"RSI: {t.rsi:.1f}  ADX: {t.adx:.1f}",
        "",
        "📐 *SUPPORT & RESISTANCE*",
        f"R2: {rp(t.resistance_2)}  R1: {rp(t.resistance_1)}",
        f"Price: ➤ {rp(t.current_price)}",
        f"S1: {rp(t.support_1)}  S2: {rp(t.support_2)}",
        f"20d High: {rp(t.high_20d)}  20d Low: {rp(t.low_20d)}",
        "",
        f"{vol_emoji} *VOLUME / FLOW*",
        f"Today: {t.today_volume/1e6:.1f}M  |  20d avg: {t.avg_volume_20d/1e6:.1f}M",
        f"Ratio: {t.volume_ratio:.1f}x  →  {t.volume_signal.upper().replace('_',' ')}",
        "",
        "📈 *FUNDAMENTALS*",
        f"ROE: {f'{t.roe*100:.1f}%' if t.roe else 'N/A'}  "
        f"DER: {f'{t.der:.2f}x' if t.der else 'N/A'}  "
        f"PBV: {f'{t.pbv:.2f}x' if t.pbv else 'N/A'}  "
        f"PE: {f'{t.pe_ratio:.1f}x' if t.pe_ratio else 'N/A'}",
        f"Rev Growth: {f'{t.revenue_growth*100:.1f}%' if t.revenue_growth else 'N/A'}",
        f"Fund Score: {t.fund_score}/4",
        "",
        "🎯 *TRADE PLAN*",
        f"Entry Zone: {rp(t.entry_low)} – {rp(t.entry_high)}",
        f"Stop Loss: {rp(t.stop_loss)} (–{abs((t.stop_loss-t.current_price)/t.current_price*100):.1f}%)",
        f"T1: {rp(t.t1)} (+{(t.t1/t.current_price-1)*100:.1f}%)",
        f"T2: {rp(t.t2)} (+{(t.t2/t.current_price-1)*100:.1f}%)",
        f"T3: {rp(t.t3)} (+{(t.t3/t.current_price-1)*100:.1f}%)",
        f"R:R Ratio: 1:{t.rr_ratio:.1f}",
        f"Time Stop: Exit if <+5% in 10 trading days",
        "",
        "⚡ *ENTRY TRIGGER*",
        f"_{t.entry_trigger}_",
        "",
        "🐂 *BULL CASE (Why I win)*",
        t.buy_thesis,
        "",
        "🐻 *BEAR CASE (How I lose money)*",
        t.bear_case,
        "",
        "❌ *INVALIDATION POINTS*",
        t.invalidation,
    ]

    if t.recent_news:
        lines += ["", "📰 *RECENT NEWS / RUMORS*"]
        for n in t.recent_news[:4]:
            lines.append(f"• _{n[:100]}_")

    lines += [
        "",
        "✅ *CONDITIONS MET*",
        *[f"  ✓ {c}" for c in t.conditions_met],
        "",
        "✗ *CONDITIONS FAILED*",
        *[f"  ✗ {c}" for c in t.conditions_failed[:4]],
    ]

    return "\n".join(l for l in lines if l is not None)


# ─────────────────────────────────────────────
# BATCH ANALYSIS (for /scan)
# ─────────────────────────────────────────────

def analyse_tickers_batch(pairs: List[Tuple[str, str]]) -> List[TechnicalResult]:
    results = []
    for ticker, name in pairs:
        try:
            r = analyse_ticker(ticker, name)
            if r:
                results.append(r)
        except Exception as e:
            logger.warning("Batch analyse error %s: %s", ticker, e)
    return results


def is_market_healthy() -> Tuple[bool, str]:
    trend = _ihsg_trend()
    if trend == "bullish":
        return True,  "IHSG bullish — above EMA50 & EMA200. Full scan active."
    elif trend == "neutral":
        return True,  "IHSG neutral — above EMA200 only. Selective longs only."
    elif trend == "bearish":
        return False, "IHSG bearish — below EMA200. No new longs. Capital preservation mode."
    return True, "IHSG status unknown — proceeding with caution."
