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
    """
    Institutional Spring setup report card.
    Structured as: conviction → accumulation evidence → trade plan → how you lose.
    """
    from modules.utils import safe_md, rp, pct

    # ── Conviction badge ─────────────────────────────────────────────
    is_spring = tech.spring_score >= 5
    if tech.at_arb:
        badge     = "⛔ DO NOT ENTER"
        badge_why = tech.arb_warning
    elif tech.spring_score >= 8:
        badge     = "🔥 SPRING TRIGGERED"
        badge_why = "All accumulation signals confirmed"
    elif tech.spring_score >= 5:
        badge     = "👀 WATCH — ACCUMULATION FORMING"
        badge_why = f"Spring score {tech.spring_score}/10 — waiting for trigger"
    elif tech.total_score >= 75:
        badge     = "⚡ MOMENTUM SETUP"
        badge_why = f"Score {tech.total_score}/100 — trend continuation"
    else:
        badge     = "🔍 SPECULATIVE"
        badge_why = f"Score {tech.total_score}/100 — low conviction"

    regime_emoji = {"BULL": "🟢", "SIDEWAYS": "🟡", "BEAR": "🟠",
                    "PANIC": "🔴", "UNKNOWN": "⚪"}.get(tech.regime, "⚪")

    # ── Story section ────────────────────────────────────────────────
    story_line = f"_{safe_md(story.headline[:120])}_\n🔗 {story.source_url}" \
                 if story.source_url else f"_{safe_md(story.headline[:120])}_"

    # ── Accumulation evidence ────────────────────────────────────────
    accum_lines = []
    if tech.stopping_volume:
        accum_lines.append(f"✅ Stopping vol — {tech.stopping_volume_detail}")
    else:
        accum_lines.append(f"❌ No stopping volume — absorption unconfirmed")

    if tech.obv_diverging:
        accum_lines.append(f"✅ OBV divergence — {tech.obv_detail}")
    else:
        accum_lines.append(f"❌ OBV not diverging — no smart money signal yet")

    if tech.ad_divergence:
        accum_lines.append(f"✅ A/D line — {safe_md(tech.ad_divergence_msg)}")

    if tech.bb_squeeze_spring:
        accum_lines.append(f"✅ BB squeeze ({tech.bb_bandwidth_pct:.1f}% bandwidth) — energy coiling")
    else:
        accum_lines.append(f"➖ BB bandwidth {tech.bb_bandwidth_pct:.1f}% — no squeeze")

    if tech.near_major_support:
        dist = tech.support_proximity_pct
        accum_lines.append(f"✅ At support — {abs(dist):.1f}% {'above' if dist >= 0 else 'below'} S1 {rp(tech.support_1)}")
    else:
        accum_lines.append(f"➖ Not at major support (S1 {rp(tech.support_1)})")

    if tech.rs_score > 1.2:
        accum_lines.append(f"✅ RS {tech.rs_score:.2f} — outperforming IHSG during weakness")
    elif tech.rs_score > 0.8:
        accum_lines.append(f"➖ RS {tech.rs_score:.2f} — market-performing")
    else:
        accum_lines.append(f"❌ RS {tech.rs_score:.2f} — underperforming index (weak)")

    accum_text = "\n".join(f"  {l}" for l in accum_lines)

    # ── Trade plan ───────────────────────────────────────────────────
    stop_pct = abs((tech.stop_loss - tech.current_price) / tech.current_price * 100) if tech.current_price else 0
    t1_pct   = (tech.t1 / tech.current_price - 1) * 100 if tech.current_price else 0
    t2_pct   = (tech.t2 / tech.current_price - 1) * 100 if tech.current_price else 0
    t3_pct   = (tech.t3 / tech.current_price - 1) * 100 if tech.current_price else 0
    rr_label = f"1:{tech.rr_ratio:.1f}"

    if story.score >= 9 and tech.spring_score >= 7:
        pos_size = "3–5%"
    elif story.score >= 7 or tech.spring_score >= 5:
        pos_size = "2–3%"
    else:
        pos_size = "1–2%"

    # Position sizing math (Rp10M example)
    if tech.current_price > 0:
        risk_per_share = tech.current_price - tech.stop_loss
        shares_per_10m = int(10_000_000 / tech.current_price / 100) * 100  # round to lot
        max_loss_10m   = shares_per_10m * risk_per_share if risk_per_share > 0 else 0
    else:
        shares_per_10m = 0
        max_loss_10m   = 0

    stop_type = (
        f"Hard stop — 3% below S1 {rp(tech.support_1)}\n"
        f"           _Thesis breaks if support is decisively lost on high vol_"
    ) if is_spring and tech.support_1 > 0 else f"1.5 ATR stop = {rp(tech.stop_loss)}"

    # ── How you lose ─────────────────────────────────────────────────
    bear_bullets = tech.bear_case.split("\n")[:4] if tech.bear_case else []
    bear_text    = "\n".join(bear_bullets)

    # ── Message assembly ─────────────────────────────────────────────
    vol_str = f"{tech.today_volume/1e6:.1f}M ({tech.volume_ratio:.1f}x avg)"

    msg = (
        f"{'━'*34}\n"
        f"*${safe_md(story.ticker)}* — {safe_md(story.company_name)}\n"
        f"{badge}\n"
        f"_{badge_why}_\n"
        f"{regime_emoji} Regime: *{tech.regime}*  |  "
        f"Score: *{tech.total_score}/100*  |  Spring: *{tech.spring_score}/10*\n"
        f"{'━'*34}\n\n"

        f"📰 *STORY* ({story.score}/10)\n"
        f"{story_line}\n\n"

        f"🔬 *ACCUMULATION EVIDENCE*\n"
        f"{accum_text}\n\n"

        f"📈 *BANDAR FINGERPRINT*\n"
        f"  Vol: {vol_str}  |  RSI: {tech.rsi:.0f}\n"
        f"  {'🐋 SILENT ACCUM — high vol, narrow range' if tech.is_silent_accum else ''}"
        f"  {'🚨 SMART MONEY: ' + safe_md(tech.ad_divergence_msg[:60]) if tech.ad_divergence else ''}\n\n"

        f"{'━'*34}\n"
        f"💼 *TRADE PLAN*\n"
        f"  Entry  : {rp(tech.entry_low)} – {rp(tech.entry_high)}\n"
        f"  Stop   : {rp(tech.stop_loss)} (–{stop_pct:.1f}%)\n"
        f"           _{stop_type}_\n"
        f"  T1     : {rp(tech.t1)} (+{t1_pct:.0f}%) → exit 40%\n"
        f"  T2     : {rp(tech.t2)} (+{t2_pct:.0f}%) → exit 35%\n"
        f"  T3     : {rp(tech.t3)} (+{t3_pct:.0f}%) → trail 25%\n"
        f"  R:R    : {rr_label}  |  Max size: {pos_size} portfolio\n"
        f"  10M pos: {shares_per_10m:,} shares | Max loss: {rp(max_loss_10m)}\n"
        f"  Time stop: Exit jika <+5% dalam 10 hari trading\n\n"

        f"⚡ *TRIGGER*\n"
        f"  _{safe_md(tech.entry_trigger[:200])}_\n\n"

        f"{'━'*34}\n"
        f"🐻 *HOW I LOSE MONEY*\n"
        f"{bear_text}\n\n"

        f"❌ *INVALIDATION — exit immediately if:*\n"
        f"{tech.invalidation}\n\n"

        f"⏰ _{safe_md(datetime.now().strftime('%Y-%m-%d %H:%M'))} WIB_  "
        f"⚠️ _DYOR — Bukan rekomendasi investasi_"
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
    """Scan header — shows Spring conviction ranking."""
    if not signals:
        return (
            "🔍 *Full Scan Complete*\n\n"
            "❌ Tidak ada setup yang memenuhi kriteria saat ini.\n"
            "_Story score ≥ 6/10 AND technical threshold required._"
        )

    lines = [f"🔍 *Scan Complete* — {len(signals)} setup\n"]
    for s, t in signals:
        spring_tag = f" | 🌱 Spring {t.spring_score}/10" if t.spring_score >= 3 else ""
        regime_e   = {"BULL":"🟢","SIDEWAYS":"🟡","BEAR":"🟠","PANIC":"🔴"}.get(t.regime,"⚪")
        lines.append(
            f"{regime_e} *${s.ticker}* | Story {s.score}/10 | "
            f"Score {t.total_score}/100{spring_tag} | R:R 1:{t.rr_ratio:.1f}"
        )
    lines.append("\n_Detail signals ↓_")
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
