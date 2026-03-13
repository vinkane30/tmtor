"""
modules/macro.py — Macro & Sector Context Brain
"""

import logging
import feedparser
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional

import yfinance as yf
import pandas as pd

logger = logging.getLogger(__name__)

SECTOR_TICKERS = {
    "Energy":    ["ADRO", "ITMG", "PTBA", "BUMI", "HRUM", "MEDC", "PGAS"],
    "Banking":   ["BBCA", "BBRI", "BBNI", "BMRI", "BRIS", "BDMN"],
    "Commodity": ["ANTM", "INCO", "TINS", "MDKA", "AMMN"],
    "Consumer":  ["UNVR", "ICBP", "INDF", "MAPI", "ACES"],
    "Telco":     ["TLKM", "EXCL", "ISAT", "TOWR"],
    "Property":  ["BSDE", "CTRA", "SMRA", "PWON"],
    "Industrial":["ASII", "SMGR", "INTP"],
    "Healthcare":["MIKA", "HEAL", "KLBF", "SIDO"],
}

GEO_HEDGE_SECTORS = {"Energy", "Commodity"}

MACRO_RSS_FEEDS = [
    "https://rss.kontan.co.id/category/makro-ekonomi",
    "https://rss.bisnis.com/feed/rss2/ekonomi-bisnis.rss",
]

MACRO_KEYWORDS = {
    "oil":         ["oil", "crude", "brent", "wti", "opec", "minyak"],
    "fed_rate":    ["fed", "federal reserve", "rate hike", "suku bunga", "hawkish"],
    "rupiah":      ["rupiah", "idr", "currency", "kurs"],
    "geopolitical":["hormuz", "ukraine", "russia", "iran", "israel", "perang", "konflik"],
    "commodity":   ["coal", "nickel", "copper", "gold", "nikel", "batu bara", "emas"],
}


@dataclass
class MacroContext:
    brent_price:      float = 0.0
    brent_change_pct: float = 0.0
    usd_idr:          float = 0.0
    gold_price:       float = 0.0
    sector_returns:   Dict[str, float] = field(default_factory=dict)
    hot_sectors:      List[str] = field(default_factory=list)
    weak_sectors:     List[str] = field(default_factory=list)
    themes:           List[str] = field(default_factory=list)
    geo_risk:         bool = False
    oil_driven:       bool = False
    rate_driven:      bool = False
    rupiah_pressure:  bool = False
    macro_headlines:  List[str] = field(default_factory=list)
    narrative:        str = ""
    sector_advice:    Dict[str, str] = field(default_factory=dict)


def fetch_macro_context() -> MacroContext:
    ctx = MacroContext()

    # Global prices
    try:
        bd = yf.Ticker("BZ=F").history(period="5d", interval="1d")
        if len(bd) >= 2:
            ctx.brent_price      = float(bd["Close"].iloc[-1])
            ctx.brent_change_pct = float((bd["Close"].iloc[-1] / bd["Close"].iloc[-2] - 1) * 100)
    except Exception:
        pass

    try:
        gd = yf.Ticker("GC=F").history(period="2d", interval="1d")
        if not gd.empty:
            ctx.gold_price = float(gd["Close"].iloc[-1])
    except Exception:
        pass

    try:
        ud = yf.Ticker("USDIDR=X").history(period="2d", interval="1d")
        if not ud.empty:
            ctx.usd_idr = float(ud["Close"].iloc[-1])
    except Exception:
        pass

    # Sector rotation
    for sector, tickers in SECTOR_TICKERS.items():
        returns = []
        for t in tickers[:3]:
            try:
                df = yf.Ticker(t + ".JK").history(period="2d", interval="1d")
                if len(df) >= 2:
                    r = float(df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1) * 100
                    returns.append(r)
            except Exception:
                pass
        if returns:
            avg = sum(returns) / len(returns)
            ctx.sector_returns[sector] = round(avg, 2)
            if avg > 0.3:
                ctx.hot_sectors.append(sector)
            elif avg < -0.5:
                ctx.weak_sectors.append(sector)

    # News
    headlines = []
    for feed_url in MACRO_RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                title = entry.get("title", "")
                if title:
                    headlines.append(title)
        except Exception:
            pass
    ctx.macro_headlines = headlines[:8]

    # Theme detection
    all_text = " ".join(headlines).lower()
    for theme, keywords in MACRO_KEYWORDS.items():
        if any(kw in all_text for kw in keywords):
            ctx.themes.append(theme)

    ctx.geo_risk        = "geopolitical" in ctx.themes
    ctx.oil_driven      = "oil" in ctx.themes or ctx.brent_change_pct > 2.0
    ctx.rate_driven     = "fed_rate" in ctx.themes
    ctx.rupiah_pressure = "rupiah" in ctx.themes or ctx.usd_idr > 16500

    ctx.narrative    = _build_narrative(ctx)
    ctx.sector_advice = _sector_advice(ctx)

    return ctx


def _build_narrative(ctx: MacroContext) -> str:
    parts = []
    if ctx.geo_risk and ctx.oil_driven:
        parts.append(
            "Geopolitical tensions driving oil to $" + str(round(ctx.brent_price)) + "/bbl. "
            "Energy and commodity stocks are geopolitical hedges."
        )
    elif ctx.oil_driven and ctx.brent_price > 80:
        parts.append(
            "Brent crude at $" + str(round(ctx.brent_price)) + "/bbl. "
            "Coal and energy names benefit from elevated oil prices."
        )
    if ctx.rupiah_pressure:
        parts.append(
            "USD/IDR at " + str(round(ctx.usd_idr)) + " — rupiah under pressure. "
            "USD-earner commodities benefit."
        )
    if ctx.rate_driven:
        parts.append(
            "Rate concerns in headlines. "
            "Oversold LQ45 banks historically mean-revert 5-8% from RSI below 35."
        )
    if ctx.hot_sectors:
        parts.append("Hot sectors today: " + ", ".join(ctx.hot_sectors))
    if not parts:
        parts.append("No dominant macro theme — stock-specific catalysts drive alpha today.")
    return " | ".join(parts)


def _sector_advice(ctx: MacroContext) -> Dict[str, str]:
    advice = {}
    for sector in SECTOR_TICKERS:
        ret = ctx.sector_returns.get(sector, 0)
        if sector in GEO_HEDGE_SECTORS and ctx.geo_risk:
            advice[sector] = "OVERWEIGHT — Geopolitical hedge"
        elif sector == "Banking" and ctx.rate_driven:
            advice[sector] = "SELECTIVE — Oversold LQ45 banks = institutional discount"
        elif ret > 0.5:
            advice[sector] = "OVERWEIGHT — Up " + str(round(ret, 1)) + "% today"
        elif ret < -1.0:
            advice[sector] = "UNDERWEIGHT — Down " + str(round(abs(ret), 1)) + "% today"
        else:
            advice[sector] = "NEUTRAL — " + str(round(ret, 1)) + "% today"
    return advice


def get_sector_for_ticker(ticker: str) -> Optional[str]:
    for sector, tickers in SECTOR_TICKERS.items():
        if ticker.upper() in tickers:
            return sector
    return None


def get_macro_tag(ticker: str, macro: MacroContext, rsi: float) -> Tuple[str, str]:
    sector = get_sector_for_ticker(ticker)

    if sector in GEO_HEDGE_SECTORS and macro.geo_risk and macro.oil_driven:
        return (
            "Geopolitical Hedge",
            "Brent $" + str(round(macro.brent_price)) + "/bbl + geopolitical risk = "
            + sector + " stocks decouple from IHSG weakness"
        )
    if sector == "Banking" and rsi < 35:
        return (
            "Institutional Discount",
            "LQ45 bank at RSI below 35 = institutional buying zone. "
            "Check foreign flow stabilization before entry."
        )
    if sector == "Commodity" and macro.rupiah_pressure:
        return (
            "USD Earner Hedge",
            "USD/IDR " + str(round(macro.usd_idr)) + " benefits commodity exporters"
        )
    if sector and sector in macro.hot_sectors:
        return (
            "Sector Rotation",
            sector + " outperforming IHSG today"
        )
    return ("", "")
