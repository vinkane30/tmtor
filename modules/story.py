"""
modules/story.py — Story Detection Engine
Scrapes IDX keterbukaan informasi, KPPU, news RSS, and social velocity.
Returns a list of CatalystResult objects ranked by story score.
"""

import re
import logging
import asyncio
import hashlib
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from urllib.parse import urljoin

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
    ticker:       str
    company_name: str
    score:        int
    catalyst_type: str
    headline:     str
    source_url:   str
    published_at: datetime
    raw_text:     str = ""
    is_rejection: bool = False  # rights issue for debt only → rejected

    def to_dict(self) -> dict:
        return {
            "ticker":        self.ticker,
            "company_name":  self.company_name,
            "story_score":   self.score,
            "catalyst_type": self.catalyst_type,
            "headline":      self.headline,
            "catalyst_source": self.source_url,
            "published_at":  self.published_at,
        }


# ─────────────────────────────────────────────
# KEYWORD MATCHING
# ─────────────────────────────────────────────

CATALYST_KEYWORD_MAP: List[Tuple[str, str, int]] = [
    # (keyword_pattern, catalyst_type, score)
    (r"injeksi\s+aset|inbreng",               "asset_injection",        10),
    (r"akuisisi|pengambilalihan|takeover",      "strategic_acquisition",  9),
    (r"(HMETD|PUT|rights\s+issue).{0,80}(strategis|strategic)",
                                               "rights_issue_strategic",  8),
    (r"kontrak.{0,60}pemerintah|proyek\s+strategis\s+nasional",
                                               "government_contract",     7),
    (r"tender\s+offer",                        "strategic_acquisition",  9),
    (r"RUPSLB",                                "special_agm",             6),
    (r"buyback|pembelian\s+kembali\s+saham",   "buyback",                 5),
    (r"(HMETD|PUT|rights\s+issue)",            "rights_issue",            5),
]

REJECTION_PATTERNS = [
    r"pelunasan\s+utang",
    r"refinanc",
    r"restrukturisasi\s+(hutang|utang)",
    r"membayar\s+(hutang|kewajiban)",
]


def classify_text(text: str) -> Tuple[str, int, bool]:
    """Return (catalyst_type, score, is_rejection)."""
    text_lower = text.lower()

    # Check rejection first
    is_rejection = any(re.search(p, text_lower) for p in REJECTION_PATTERNS)

    best_type, best_score = "vague_rumor", 2
    for pattern, ctype, score in CATALYST_KEYWORD_MAP:
        if re.search(pattern, text_lower):
            if score > best_score:
                best_type, best_score = ctype, score

    return best_type, best_score, is_rejection


def extract_ticker_from_text(text: str) -> Optional[str]:
    """Try to extract a 4-letter IDX ticker from disclosure text."""
    # IDX tickers are 4 uppercase letters
    matches = re.findall(r'\b([A-Z]{4})\b', text)
    # Filter out common false positives
    FALSE_POSITIVES = {"YANG", "PADA", "DARI", "AKAN", "OLEH", "ATAU",
                       "IHSG", "RUPS", "HMETD", "KPPU", "BUMN", "RUPSLB"}
    for m in matches:
        if m not in FALSE_POSITIVES:
            return m
    return None


# ─────────────────────────────────────────────
# IDX KETERBUKAAN INFORMASI SCRAPER
# ─────────────────────────────────────────────

async def scrape_idx_disclosures(client: httpx.AsyncClient) -> List[CatalystResult]:
    """Scrape IDX keterbukaan informasi for corporate action keywords."""
    results = []
    try:
        # IDX provides a JSON API for disclosures
        params = {
            "start": 0,
            "length": 50,
            "columns[0][data]": "No",
            "order[0][column]": 0,
            "order[0][dir]": "desc",
        }
        url = "https://www.idx.co.id/primary/News/GetNewsByCategory"
        params_disc = {"category": "SP,KD", "start": 0, "length": 50}

        resp = await client.get(url, params=params_disc, timeout=20)
        if resp.status_code != 200:
            logger.warning("IDX disclosure API returned %s", resp.status_code)
            return results

        data = resp.json()
        items = data.get("data", [])

        for item in items:
            headline  = item.get("Title", "")
            ticker    = item.get("StockCode", "") or extract_ticker_from_text(headline) or "UNKNOWN"
            file_url  = item.get("AttachmentFile", "")
            pub_date  = item.get("Date_publish", "")

            combined_text = headline
            ctype, score, rejected = classify_text(combined_text)

            if score < 2:
                continue
            if catalyst_already_seen(headline):
                continue

            try:
                pub_dt = datetime.strptime(pub_date[:19], "%Y-%m-%dT%H:%M:%S")
            except Exception:
                pub_dt = datetime.utcnow()

            result = CatalystResult(
                ticker       = ticker.upper().strip(),
                company_name = item.get("Name", ticker),
                score        = score,
                catalyst_type= ctype,
                headline     = headline,
                source_url   = f"https://www.idx.co.id{file_url}" if file_url else config.IDX_DISCLOSURE_URL,
                published_at = pub_dt,
                raw_text     = combined_text,
                is_rejection = rejected,
            )
            results.append(result)
            save_catalyst({
                "ticker": result.ticker, "catalyst_type": result.catalyst_type,
                "headline": result.headline, "source_url": result.source_url,
                "published_at": result.published_at, "score": result.score,
            })

    except Exception as e:
        logger.error("IDX scrape error: %s", e, exc_info=True)

    return results


# ─────────────────────────────────────────────
# KPPU MERGER REGISTRY
# ─────────────────────────────────────────────

async def scrape_kppu_mergers(client: httpx.AsyncClient) -> List[CatalystResult]:
    """Scrape KPPU merger filings — appear BEFORE IDX announcements."""
    results = []
    try:
        resp = await client.get(config.KPPU_MERGER_URL, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")

        # KPPU lists merger notifications in a table/list
        rows = soup.select("table tbody tr") or soup.select(".entry-content li")
        for row in rows[:20]:
            text = row.get_text(separator=" ", strip=True)
            link_tag = row.find("a")
            url = link_tag["href"] if link_tag and link_tag.get("href") else config.KPPU_MERGER_URL

            if catalyst_already_seen(text[:120]):
                continue

            ticker = extract_ticker_from_text(text) or "KPPU"
            ctype, score, rejected = classify_text(text)

            # KPPU filings are inherently acquisition-level
            if score < 5:
                score = 7
                ctype = "strategic_acquisition"

            result = CatalystResult(
                ticker       = ticker,
                company_name = text[:60],
                score        = score,
                catalyst_type= ctype,
                headline     = f"[KPPU] {text[:120]}",
                source_url   = url,
                published_at = datetime.utcnow(),
                raw_text     = text,
            )
            results.append(result)
            save_catalyst({
                "ticker": result.ticker, "catalyst_type": result.catalyst_type,
                "headline": result.headline, "source_url": result.source_url,
                "published_at": result.published_at, "score": result.score,
            })

    except Exception as e:
        logger.error("KPPU scrape error: %s", e, exc_info=True)

    return results


# ─────────────────────────────────────────────
# NEWS RSS
# ─────────────────────────────────────────────

async def scrape_news_rss() -> List[CatalystResult]:
    """Parse news RSS feeds for corporate action keywords."""
    results = []
    cutoff = datetime.utcnow() - timedelta(hours=24)

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

                # Try to parse publish date
                pub_struct = entry.get("published_parsed")
                if pub_struct:
                    pub_dt = datetime(*pub_struct[:6])
                else:
                    pub_dt = datetime.utcnow()

                if pub_dt < cutoff:
                    continue

                ticker = extract_ticker_from_text(combined) or "NEWS"

                result = CatalystResult(
                    ticker       = ticker,
                    company_name = title[:60],
                    score        = score,
                    catalyst_type= ctype,
                    headline     = title,
                    source_url   = link,
                    published_at = pub_dt,
                    raw_text     = combined,
                    is_rejection = rejected,
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
# SOCIAL VELOCITY (Twitter/X placeholder)
# ─────────────────────────────────────────────

async def check_social_velocity(tickers: List[str]) -> Dict[str, float]:
    """
    Returns a dict {ticker: velocity_ratio} where ratio > 5 = flag.
    Currently a stub — connect to Twitter/X API v2 or Stockbit API.
    """
    # TODO: Implement with Twitter API bearer token or Stockbit scraping
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
        idx_task  = scrape_idx_disclosures(client)
        kppu_task = scrape_kppu_mergers(client)
        news_task = scrape_news_rss()

        idx_results, kppu_results, news_results = await asyncio.gather(
            idx_task, kppu_task, news_task, return_exceptions=True
        )

    all_results: List[CatalystResult] = []
    for r in [idx_results, kppu_results, news_results]:
        if isinstance(r, list):
            all_results.extend(r)
        elif isinstance(r, Exception):
            logger.error("Story detection sub-task failed: %s", r)

    # Deduplicate by headline hash
    seen_hashes = set()
    deduped = []
    for r in all_results:
        h = hashlib.md5(r.headline.encode()).hexdigest()
        if h not in seen_hashes:
            seen_hashes.add(h)
            deduped.append(r)

    # Filter: remove rejection signals, sort descending
    qualified = [r for r in deduped if not r.is_rejection and r.score >= config.MIN_STORY_SCORE]
    qualified.sort(key=lambda x: x.score, reverse=True)

    logger.info("Story detection: %d qualified catalysts found", len(qualified))
    return qualified


async def get_recent_story_summary(hours: int = 24) -> List[CatalystResult]:
    """Return cached catalysts from the DB for the /story command."""
    from modules.database import get_recent_catalysts
    rows = get_recent_catalysts(hours=hours)
    results = []
    for row in rows:
        try:
            results.append(CatalystResult(
                ticker       = row["ticker"],
                company_name = row.get("company_name", row["ticker"]),
                score        = row["score"],
                catalyst_type= row["catalyst_type"],
                headline     = row["headline"],
                source_url   = row["source_url"],
                published_at = row["published_at"] if isinstance(row["published_at"], datetime)
                               else datetime.utcnow(),
            ))
        except Exception:
            pass
    return results
