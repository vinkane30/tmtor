"""
config.py — Central configuration for IDX Story Bot
Edit this file to customize behavior without touching logic modules.
"""

import os
from dataclasses import dataclass, field
from typing import List


# ─────────────────────────────────────────────
# SECRETS  (set via environment variables)
# ─────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")  # channel or group
DATABASE_PATH    = os.getenv("DATABASE_PATH", "idx_bot.db")

# ─────────────────────────────────────────────
# SCANNING SCHEDULE  (WIB = UTC+7)
# ─────────────────────────────────────────────
SCAN_TIMES_WIB = ["09:00", "11:00", "13:00", "15:30"]
NEWS_REFRESH_MINUTES = 30
TIMEZONE = "Asia/Jakarta"

# ─────────────────────────────────────────────
# SIGNAL THRESHOLDS
# ─────────────────────────────────────────────
MIN_STORY_SCORE       = 6    # minimum catalyst score to proceed to technicals
MIN_TECHNICAL_COUNT   = 3    # minimum technical conditions required
MIN_AVG_VOLUME        = 500_000   # shares/day — illiquid filter
MAX_SIGNALS_PER_SCAN  = 7
VOLUME_SPIKE_RATIO    = 3.0  # today volume vs 20d avg

# ─────────────────────────────────────────────
# RSI WINDOWS
# ─────────────────────────────────────────────
RSI_LOW  = 50
RSI_HIGH = 70

# ─────────────────────────────────────────────
# CATALYST SCORING TABLE
# ─────────────────────────────────────────────
CATALYST_SCORES = {
    "asset_injection":         10,
    "strategic_acquisition":   9,
    "rights_issue_strategic":  8,
    "government_contract":     7,
    "insider_buying":          6,
    "buyback":                 5,
    "vague_rumor":             2,
}

# ─────────────────────────────────────────────
# IDX DISCLOSURE KEYWORDS  (Indonesian)
# ─────────────────────────────────────────────
IDX_KEYWORDS = [
    "akuisisi", "injeksi aset", "inbreng",
    "HMETD", "PUT", "RUPSLB",
    "investor strategis", "pengambilalihan",
    "tender offer", "buyback", "pembelian kembali",
]

REJECTION_KEYWORDS_RIGHTS = [
    "pelunasan utang", "refinancing", "restrukturisasi hutang"
]

# ─────────────────────────────────────────────
# NEWS RSS SOURCES
# ─────────────────────────────────────────────
NEWS_RSS_FEEDS = [
    "https://www.kontan.co.id/rss/investasi",
    "https://www.kontan.co.id/rss/bursa",
    "https://ekonomi.bisnis.com/feed",
    "https://www.cnbcindonesia.com/market/rss",
    "https://katadata.co.id/feed",
]

# ─────────────────────────────────────────────
# SCRAPING URLS
# ─────────────────────────────────────────────
IDX_DISCLOSURE_URL   = "https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi/"
KPPU_MERGER_URL      = "https://www.kppu.go.id/id/merger/"
IDX_SUMMARY_API      = "https://www.idx.co.id/primary/StockData/GetIndexSaham"

# ─────────────────────────────────────────────
# REFERENCE TICKERS  (pattern comparison)
# ─────────────────────────────────────────────
REFERENCE_MULTIBAGGERS = ["PANI", "BBHI", "BREN", "RLCO"]

# ─────────────────────────────────────────────
# SELF-IMPROVEMENT
# ─────────────────────────────────────────────
WEEKLY_REPORT_DAY  = "sunday"
WEEKLY_REPORT_TIME = "19:00"
LOOKBACK_DAYS_EVAL = 7    # how many days back to evaluate signals

# ─────────────────────────────────────────────
# MARKET HEALTH
# ─────────────────────────────────────────────
IHSG_TICKER          = "^JKSE"
IHSG_EMA_DAYS        = 50
IHSG_CORRECTION_DAYS = 3   # consecutive days below EMA50 = correction
