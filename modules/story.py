"""
modules/story.py — Story Detection Engine
Scrapes Kontan RSS, Bisnis.com RSS, and news RSS for corporate action catalysts.
Returns a list of CatalystResult objects ranked by story score.
"""

import re
import logging
import asyncio
import hashlib
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple

import feedparser
import httpx
from bs4 import BeautifulSoup

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from modules.database import save_catalyst, catalyst_already_seen

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────

@dataclass
class CatalystResult:
    ticker:        str
    company_name:  str
    score:         int
    catalyst_type: str
    headline:      str
    source_url:    str
    published_at:  datetime
    raw_text:      str = ""
    is_rejection:  bool = False

    def to_dict(self) -> dict:
        return {
            "ticker":          self.ticker,
            "company_name":    self.company_name,
            "story_score":     self.score,
            "catalyst_type":   self.catalyst_type,
            "headline":        self.headline,
            "catalyst_source": self.source_url,
            "published_at":    self.published_at,
        }


# ─────────────────────────────────────────────
# KEYWORD MATCHING
# ─────────────────────────────────────────────

CATALYST_KEYWORD_MAP: List[Tuple[str, str, int]] = [
    (r"injeksi\s+aset|inbreng",                                        "asset_injection",        10),
    (r"akuisisi|pengambilalihan|takeover|merger",                       "strategic_acquisition",   9),
    (r"(HMETD|PUT|rights\s+issue).{0,80}(strategis|strategic)",        "rights_issue_strategic",  8),
    (r"kontrak.{0,60}pemerintah|proyek\s+strategis\s+nasional",        "government_contract",      7),
    (r"tender\s+offer",                                                 "strategic_acquisition",   9),
    (r"RUPSLB",                                                         "special_agm",             6),
    (r"buyback|pembelian\s+kembali\s+saham",                           "buyback",                 5),
    (r"(HMETD|PUT|rights\s+issue)",                                    "rights_issue",            5),
]

REJECTION_PATTERNS = [
    r"pelunasan\s+utang",
    r"refinanc",
    r"restrukturisasi\s+(hutang|utang)",
    r"membayar\s+(hutang|kewajiban)",
]


def classify_text(text: str) -> Tuple[str, int, bool]:
    text_lower = text.lower()
    is_rejection = any(re.search(p, text_lower) for p in REJECTION_PATTERNS)
    best_type, best_score = "vague_rumor", 2
    for pattern, ctype, score in CATALYST_KEYWORD_MAP:
        if re.search(pattern, text_lower):
            if score > best_score:
                best_type, best_score = ctype, score
    return best_type, best_score, is_rejection


def extract_ticker_from_text(text: str) -> Optional[str]:
    matches = re.findall(r'\b([A-Z]{4})\b', text)
    FALSE_POSITIVES = {
        "YANG", "PADA", "DARI", "AKAN", "OLEH", "ATAU",
        "IHSG", "RUPS", "HMETD", "KPPU", "BUMN", "RUPSLB",
        "TBKK", "PERSEROAN", "DIREKSI",
    }
    for m in matches:
        if m not in FALSE_POSITIVES:
            return m
    return None


# ─────────────────────────────────────────────
# SOURCE 1: KONTAN RSS (replaces IDX direct API)
# ─────────────────────────────────────────────

KONTAN_RSS_FEEDS = [
    "https://rss.kontan.co.id/category/bursa",
    "https://rss.kontan.co.id/category/investasi",
]

BISNIS_RSS_FEEDS = [
    "https://rss.bisnis.com/feed/rss2/market/bursa.rss",
    "https://rss.bisnis.com/feed/rss2/market/emiten.rss",
]

async def scrape_kontan_rss() -> List[CatalystResult]:
    """Scrape Kontan & Bisnis.com RSS for corporate action news."""
    results = []
    cutoff  = datetime.utcnow() - timedelta(hours=48)
    feeds   = KONTAN_RSS_FEEDS + BISNIS_RSS_FEEDS

    for feed_url in feeds:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:40]:
                title   = entry.get("title", "")
                summary = entry.get("summary", "")
                link    = entry.get("link", feed_url)
                combined = f"{title} {summary}"

                ctype, score, rejected = classify_text(combined)
                if score < config.MIN_STORY_SCORE:
                    continue
                if catalyst_already_seen(title[:120]):
                    continue

                pub_struct = entry.get("published_parsed")
                pub_dt = datetime(*pub_struct[:6]) if pub_struct else datetime.utcnow()
                if pub_dt < cutoff:
                    continue

                ticker = extract_ticker_from_text(combined) or "IDX"

                result = CatalystResult(
                    ticker        = ticker,
                    company_name  = title[:60],
                    score         = score,
                    catalyst_type = ctype,
                    headline      = title,
                    source_url    = link,
                    published_at  = pub_dt,
                    raw_text      = combined,
                    is_rejection  = rejected,
                )
                results.append(result)
                save_catalyst({
                    "ticker": result.ticker, "catalyst_type": result.catalyst_type,
                    "headline": result.headline, "source_url": result.source_url,
                    "published_at": result.published_at, "score": result.score,
                })

        except Exception as e:
            logger.warning("Kontan/Bisnis RSS error (%s): %s", feed_url, e)

    return results


# ─────────────────────────────────────────────
# SOURCE 2: KPPU with better headers + fallback
# ─────────────────────────────────────────────

KPPU_URLS = [
    "https://kppu.go.id/notifikasi-merger/",
    "https://kppu.go.id/merger/",
]

KPPU_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
    "Referer": "https://www.google.com/",
}

async def scrape_kppu_mergers(client: httpx.AsyncClient) -> List[CatalystResult]:
    """Scrape KPPU merger filings with fallback to Kontan M&A news."""
    results = []

    # Try KPPU directly first
    for kppu_url in KPPU_URLS:
        try:
            resp = await client.get(kppu_url, headers=KPPU_HEADERS, timeout=20)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                rows = (
                    soup.select("table tbody tr")
                    or soup.select(".entry-content li")
                    or soup.select("article")
                    or soup.select(".post-title")
                )
                for row in rows[:20]:
                    text     = row.get_text(separator=" ", strip=True)
                    link_tag = row.find("a")
                    url      = link_tag["href"] if link_tag and link_tag.get("href") else kppu_url

                    if not text or len(text) < 10:
                        continue
                    if catalyst_already_seen(text[:120]):
                        continue

                    ticker = extract_ticker_from_text(text) or "KPPU"
                    ctype, score, rejected = classify_text(text)
                    if score < 5:
                        score = 7
                        ctype = "strategic_acquisition"

                    result = CatalystResult(
                        ticker        = ticker,
                        company_name  = text[:60],
                        score         = score,
                        catalyst_type = ctype,
                        headline      = f"[KPPU] {text[:120]}",
                        source_url    = url,
                        published_at  = datetime.utcnow(),
                        raw_text      = text,
                    )
                    results.append(result)
                    save_catalyst({
                        "ticker": result.ticker, "catalyst_type": result.catalyst_type,
                        "headline": result.headline, "source_url": result.source_url,
                        "published_at": result.published_at, "score": result.score,
                    })

                if results:
                    logger.info("KPPU scrape: %d results from %s", len(results), kppu_url)
                    return results

        except Exception as e:
            logger.warning("KPPU scrape error (%s): %s", kppu_url, e)

    # Fallback: Kontan merger/akuisisi search RSS
    logger.info("KPPU blocked — falling back to Kontan akuisisi RSS")
    try:
        feed = feedparser.parse("https://rss.kontan.co.id/search/akuisisi+merger")
        for entry in feed.entries[:15]:
            title    = entry.get("title", "")
            summary  = entry.get("summary", "")
            link     = entry.get("link", "")
            combined = f"{title} {summary}"

            ctype, score, rejected = classify_text(combined)
            if score < 5:
                score = 6
                ctype = "strategic_acquisition"

            if catalyst_already_seen(title[:120]):
                continue

            ticker = extract_ticker_from_text(combined) or "M&A"
            result = CatalystResult(
                ticker        = ticker,
                company_name  = title[:60],
                score         = score,
                catalyst_type = ctype,
                headline      = f"[M&A] {title}",
                source_url    = link,
                published_at  = datetime.utcnow(),
                raw_text      = combined,
                is_rejection  = rejected,
            )
            results.append(result)
            save_catalyst({
                "ticker": result.ticker, "catalyst_type": result.catalyst_type,
                "headline": result.headline, "source_url": result.source_url,
                "published_at": result.published_at, "score": result.score,
            })

    except Exception as e:
        logger.warning("Kontan M&A fallback error: %s", e)

    return results


# ─────────────────────────────────────────────
# SOURCE 3: NEWS RSS (config feeds)
# ─────────────────────────────────────────────

async def scrape_news_rss() -> List[CatalystResult]:
    """Parse news RSS feeds from config for corporate action keywords."""
    results = []
    cutoff  = datetime.utcnow() - timedelta(hours=24)

    for feed_url in config.NEWS_RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:30]:
                title   = entry.get("title", "")
                summary = entry.get("summary", "")
                link    = entry.get("link", feed_url)
                combined = f"{title} {summary}"

                ctype, score, rejected = classify_text(combined)
                if score < config.MIN_STORY_SCORE:
                    continue
                if catalyst_already_seen(title[:120]):
                    continue

                pub_struct = entry.get("published_parsed")
                pub_dt     = datetime(*pub_struct[:6]) if pub_struct else datetime.utcnow()
                if pub_dt < cutoff:
                    continue

                ticker = extract_ticker_from_text(combined) or "NEWS"
                result = CatalystResult(
                    ticker        = ticker,
                    company_name  = title[:60],
                    score         = score,
                    catalyst_type = ctype,
                    headline      = title,
                    source_url    = link,
                    published_at  = pub_dt,
                    raw_text      = combined,
                    is_rejection  = rejected,
                )
                results.append(result)
                save_catalyst({
                    "ticker": result.ticker, "catalyst_type": result.catalyst_type,
                    "headline": result.headline, "source_url": result.source_url,
                    "published_at": result.published_at, "score": result.score,
                })

        except Exception as e:
            logger.warning("RSS feed error (%s): %s", feed_url, e)

    return results


# ─────────────────────────────────────────────
# SOCIAL VELOCITY (stub)
# ─────────────────────────────────────────────

async def check_social_velocity(tickers: List[str]) -> Dict[str, float]:
    logger.debug("Social velocity check: stub (not implemented)")
    return {}


# ─────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────

async def run_story_detection() -> List[CatalystResult]:
    """Run all scrapers, deduplicate, sort by score descending."""
    async with httpx.AsyncClient(
        headers={"User-Agent": "IDXBot/1.0 (research; contact@example.com)"},
        follow_redirects=True,
    ) as client:
        kontan_task = scrape_kontan_rss()
        kppu_task   = scrape_kppu_mergers(client)
        news_task   = scrape_news_rss()

        kontan_results, kppu_results, news_results = await asyncio.gather(
            kontan_task, kppu_task, news_task, return_exceptions=True
        )

    all_results: List[CatalystResult] = []
    for r in [kontan_results, kppu_results, news_results]:
        if isinstance(r, list):
            all_results.extend(r)
        elif isinstance(r, Exception):
            logger.error("Story detection sub-task failed: %s", r)

    seen_hashes = set()
    deduped     = []
    for r in all_results:
        h = hashlib.md5(r.headline.encode()).hexdigest()
        if h not in seen_hashes:
            seen_hashes.add(h)
            deduped.append(r)

    qualified = [r for r in deduped if not r.is_rejection and r.score >= config.MIN_STORY_SCORE]
    qualified.sort(key=lambda x: x.score, reverse=True)

    logger.info("Story detection: %d qualified catalysts found", len(qualified))
    return qualified


async def get_recent_story_summary(hours: int = 24) -> List[CatalystResult]:
    from modules.database import get_recent_catalysts
    rows = get_recent_catalysts(hours=hours)
    results = []
    for row in rows:
        try:
            results.append(CatalystResult(
                ticker        = row["ticker"],
                company_name  = row.get("company_name", row["ticker"]),
                score         = row["score"],
                catalyst_type = row["catalyst_type"],
                headline      = row["headline"],
                source_url    = row["source_url"],
                published_at  = row["published_at"] if isinstance(row["published_at"], datetime)
                                else datetime.utcnow(),
            ))
        except Exception:
            pass
    return results
