"""
modules/signals.py — Signal Generation + Telegram Formatting
Combines story scores + technical results into high-conviction signals.
"""

import logging
from datetime import datetime
from typing import List, Optional, Tuple

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from modules.story     import CatalystResult
from modules.technical import TechnicalResult
from modules.database  import save_signal, was_ticker_signalled_recently

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# MATCHING
# ─────────────────────────────────────────────

def match_story_to_technical(
    stories:    List[CatalystResult],
    technicals: List[TechnicalResult],
) -> List[Tuple[CatalystResult, TechnicalResult]]:
    """
    Pair each passing technical result with its best story catalyst.
    Returns sorted list of (story, technical) pairs.
    """
    tech_map = {t.ticker.upper(): t for t in technicals}
    paired   = []

    for story in stories:
        ticker = story.ticker.upper()
        if ticker in tech_map:
            tech = tech_map[ticker]
            if tech.passed and story.score >= config.MIN_STORY_SCORE:
                paired.append((story, tech))

    # Sort: story_score * 2 + tech_score (story is primary weight)
    paired.sort(key=lambda x: x[0].score * 2 + x[1].tech_score, reverse=True)
    return paired[:config.MAX_SIGNALS_PER_SCAN]


# ─────────────────────────────────────────────
# RISK LABEL
# ─────────────────────────────────────────────

def _risk_label(story: CatalystResult, tech: TechnicalResult) -> str:
    risks = []
    if tech.avg_volume_20d < 1_000_000:
        risks.append("⚠️ Low liquidity — size position carefully")
    if story.score < 8:
        risks.append("📰 Story not yet confirmed — watch for follow-up disclosure")
    if tech.rsi > 65:
        risks.append("🔥 RSI elevated — avoid chasing, wait for pullback entry")
    if not risks:
        risks.append("✅ Risk profile acceptable for standard position")
    return "\n    ".join(risks)


def _catalyst_label(ctype: str) -> str:
    labels = {
        "asset_injection":       "Injeksi Aset",
        "strategic_acquisition": "Akuisisi / Pengambilalihan",
        "rights_issue_strategic":"Rights Issue + Investor Strategis",
        "government_contract":   "Kontrak Pemerintah",
        "insider_buying":        "Pembelian Insider",
        "buyback":               "Buyback Saham",
        "vague_rumor":           "Rumor (belum konfirmasi)",
        "special_agm":           "RUPSLB",
    }
    return labels.get(ctype, ctype.replace("_", " ").title())


def _score_stars(score: int) -> str:
    filled = min(score, 10) // 2
    return "⭐" * filled + "☆" * (5 - filled) + f" {score}/10"


# ─────────────────────────────────────────────
# TELEGRAM MESSAGE FORMATTER
# ─────────────────────────────────────────────

def format_signal_message(story: CatalystResult, tech: TechnicalResult) -> str:
    """Format the full Telegram signal message."""

    rp = lambda v: f"Rp {v:,.0f}"

    # Position size guidance based on conviction
    if story.score >= 9 and tech.tech_score >= 5:
        pos_size = "3–5%"
        conviction = "🔥 HIGH CONVICTION"
    elif story.score >= 7 and tech.tech_score >= 4:
        pos_size = "2–3%"
        conviction = "💡 MEDIUM CONVICTION"
    else:
        pos_size = "1–2%"
        conviction = "🔍 SPECULATIVE"

    stop_pct = ((tech.current_price - tech.stop_loss) / tech.current_price * 100) if tech.current_price else 0
    t1_pct   = ((tech.t1 - tech.current_price) / tech.current_price * 100) if tech.current_price else 0
    t2_pct   = ((tech.t2 - tech.current_price) / tech.current_price * 100) if tech.current_price else 0
    t3_pct   = ((tech.t3 - tech.current_price) / tech.current_price * 100) if tech.current_price else 0

    tech_bullets = "\n    ".join([f"• {c}" for c in tech.conditions_met[:5]])
    risk_text = _risk_label(story, tech)

    msg = (
        f"🔥 *${story.ticker}* — {story.company_name}\n"
        f"*{conviction}* | Story {_score_stars(story.score)}\n"
        f"{'─' * 32}\n\n"

        f"📰 *STORY:*\n"
        f"{story.headline}\n"
        f"_{_catalyst_label(story.catalyst_type)}_\n"
        f"🔗 {story.source_url}\n\n"

        f"📊 *PATTERN MATCH:* Resembles {tech.pattern_match}\n\n"

        f"{'─' * 32}\n"
        f"💼 *TRADE PLAN*\n"
        f"  Entry  : {rp(tech.entry_low)} – {rp(tech.entry_high)}\n"
        f"  Stop   : {rp(tech.stop_loss)} (–{stop_pct:.1f}%)\n"
        f"           _Invalidated if price closes below stop_\n"
        f"  T1     : {rp(tech.t1)} (+{t1_pct:.0f}%) → take 40%\n"
        f"  T2     : {rp(tech.t2)} (+{t2_pct:.0f}%) → take 35%\n"
        f"  T3     : {rp(tech.t3)} (+{t3_pct:.0f}%) → trail 25%\n"
        f"  R:R    : 1:{tech.rr_ratio:.1f} | Size: max {pos_size} portfolio\n\n"

        f"{'─' * 32}\n"
        f"🧠 *WHY NOW*\n"
        f"  Story catalyst : {_catalyst_label(story.catalyst_type)}\n"
        f"  Technical      :\n    {tech_bullets}\n"
        f"  Bandar signal  : Vol {tech.today_volume/tech.avg_volume_20d:.1f}x avg | "
        f"RSI {tech.rsi:.0f}\n"
        f"  Risk:\n    {risk_text}\n\n"

        f"⏰ _Signal generated {datetime.now().strftime('%Y-%m-%d %H:%M')} WIB_\n"
        f"⚠️ _DYOR — Ini bukan rekomendasi investasi_"
    )
    return msg


def format_story_summary(catalysts: List[CatalystResult]) -> str:
    """Format /story command output."""
    if not catalysts:
        return "📭 Tidak ada corporate action signifikan dalam 24 jam terakhir."

    lines = ["📰 *Keterbukaan Informasi Terbaru (24h)*\n"]
    for i, c in enumerate(catalysts[:10], 1):
        lines.append(
            f"{i}. *${c.ticker}* — {_score_stars(c.score)}\n"
            f"   {c.headline[:100]}\n"
            f"   _{_catalyst_label(c.catalyst_type)}_ | {c.source_url}\n"
        )
    return "\n".join(lines)


def format_scan_summary(signals: List[Tuple[CatalystResult, TechnicalResult]]) -> str:
    """Format /scan command header summary."""
    if not signals:
        return (
            "🔍 *Full Scan Complete*\n\n"
            "❌ Tidak ada setup yang memenuhi kriteria saat ini.\n"
            "_Story score ≥ 6/10 AND ≥ 3 technical conditions required._"
        )

    lines = [
        f"🔍 *Full Scan Complete* — {len(signals)} setup ditemukan\n",
        "Top setups:\n"
    ]
    for s, t in signals:
        lines.append(
            f"• *${s.ticker}* | Story {s.score}/10 | Tech {t.tech_score}/7 | "
            f"R:R 1:{t.rr_ratio:.1f}"
        )
    lines.append("\n_Detail signals dikirim di bawah ↓_")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# PIPELINE
# ─────────────────────────────────────────────

def build_and_save_signals(
    stories:    List[CatalystResult],
    technicals: List[TechnicalResult],
) -> List[Tuple[CatalystResult, TechnicalResult, str]]:
    """
    Match, filter duplicates, save to DB, return list of
    (story, technical, formatted_message).
    """
    pairs = match_story_to_technical(stories, technicals)
    output = []

    for story, tech in pairs:
        if was_ticker_signalled_recently(story.ticker, hours=48):
            logger.info("Skipping %s — already signalled within 48h", story.ticker)
            continue

        signal_id = save_signal({
            "ticker":          story.ticker,
            "company_name":    story.company_name,
            "story_score":     story.score,
            "tech_count":      tech.tech_score,
            "catalyst_type":   story.catalyst_type,
            "catalyst_source": story.source_url,
            "entry_low":       tech.entry_low,
            "entry_high":      tech.entry_high,
            "stop_loss":       tech.stop_loss,
            "t1":              tech.t1,
            "t2":              tech.t2,
            "t3":              tech.t3,
            "rr_ratio":        tech.rr_ratio,
        })
        logger.info("Saved signal ID %d for %s", signal_id, story.ticker)

        msg = format_signal_message(story, tech)
        output.append((story, tech, msg))

    return output
