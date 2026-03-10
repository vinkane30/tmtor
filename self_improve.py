"""
modules/self_improve.py — Weekly Learning Engine
Evaluates past signals against actual price outcomes.
Generates Sunday reports and adjusts catalyst scoring weights.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import yfinance as yf

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from modules.database import (
    get_open_signals, update_signal_outcome,
    save_weekly_report, get_latest_weekly_reports, get_signals_for_week,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# SIGNAL EVALUATOR
# ─────────────────────────────────────────────

def evaluate_open_signals() -> List[Dict]:
    """
    For each open signal, fetch current price and determine outcome.
    Returns list of evaluated signal dicts.
    """
    open_signals = get_open_signals(max_age_days=config.LOOKBACK_DAYS_EVAL)
    evaluated = []

    for sig in open_signals:
        ticker    = sig["ticker"]
        yf_ticker = f"{ticker}.JK"
        entry     = sig.get("entry_high") or sig.get("entry_low") or 0
        stop      = sig.get("stop_loss") or 0
        t1        = sig.get("t1") or 0
        sent_at   = sig.get("sent_at")

        if not entry:
            continue

        try:
            hist = yf.download(yf_ticker, period="5d", interval="1d",
                                progress=False, auto_adjust=True)
            if hist.empty:
                continue

            close_col = hist["Close"].squeeze()
            current   = float(close_col.iloc[-1])
            max_high  = float(hist["High"].squeeze().max())

            max_gain_pct = (max_high - entry) / entry * 100

            # Determine outcome
            if stop and current <= stop:
                outcome = "loss"
                notes   = f"Price {current:,.0f} hit stop {stop:,.0f}"
            elif t1 and max_high >= t1:
                outcome = "win"
                notes   = f"T1 hit. Max gain {max_gain_pct:.1f}%"
            elif sig.get("sent_at"):
                # Check if signal expired (> 30 days old)
                try:
                    sent_dt = datetime.fromisoformat(str(sent_at))
                    age_days = (datetime.utcnow() - sent_dt).days
                    if age_days > 30:
                        outcome = "expired"
                        notes   = f"No move after {age_days} days"
                    else:
                        outcome = "open"
                        notes   = f"Still open. Max gain so far {max_gain_pct:.1f}%"
                except Exception:
                    outcome = "open"
                    notes   = ""
            else:
                outcome = "open"
                notes   = ""

            update_signal_outcome(sig["id"], outcome, max_gain_pct, notes)
            evaluated.append({**sig, "outcome": outcome,
                               "max_gain_pct": max_gain_pct, "notes": notes})

        except Exception as e:
            logger.warning("Could not evaluate %s: %s", ticker, e)

    return evaluated


# ─────────────────────────────────────────────
# WEEKLY REPORT BUILDER
# ─────────────────────────────────────────────

def build_weekly_report() -> Dict:
    """Aggregate this week's signal performance into a learning report."""
    # Evaluate open signals first
    evaluate_open_signals()

    # Pull signals from the past 7 days
    week_start = datetime.utcnow() - timedelta(days=7)
    signals    = get_signals_for_week(week_start)

    total   = len(signals)
    wins    = [s for s in signals if s.get("outcome") == "win"]
    losses  = [s for s in signals if s.get("outcome") == "loss"]
    open_s  = [s for s in signals if s.get("outcome") in ("open", None)]
    expired = [s for s in signals if s.get("outcome") == "expired"]

    win_rate = len(wins) / total * 100 if total else 0

    # Best and worst catalyst types by win rate
    catalyst_wins   = defaultdict(int)
    catalyst_totals = defaultdict(int)

    for s in signals:
        ct = s.get("catalyst_type", "unknown")
        catalyst_totals[ct] += 1
        if s.get("outcome") == "win":
            catalyst_wins[ct] += 1

    def wr(ct): return catalyst_wins[ct] / catalyst_totals[ct] if catalyst_totals[ct] else 0

    sorted_cats = sorted(catalyst_totals.keys(), key=wr, reverse=True)
    best_catalyst  = sorted_cats[0]  if sorted_cats else "N/A"
    worst_catalyst = sorted_cats[-1] if sorted_cats else "N/A"

    avg_gain_wins   = (sum(s.get("max_gain_pct", 0) or 0 for s in wins)   / len(wins))   if wins   else 0
    avg_loss_losses = (sum(s.get("max_gain_pct", 0) or 0 for s in losses) / len(losses)) if losses else 0

    # Build narrative notes
    notes_parts = []
    if best_catalyst != "N/A":
        notes_parts.append(f"Best catalyst: {best_catalyst} ({wr(best_catalyst)*100:.0f}% WR)")
    if total == 0:
        notes_parts.append("No signals sent this week.")
    elif win_rate >= 60:
        notes_parts.append("Strong week — signal quality high.")
    elif win_rate < 35 and total >= 3:
        notes_parts.append("Below-average week — review catalyst thresholds.")

    report = {
        "week_start":      week_start.date(),
        "total_signals":   total,
        "wins":            len(wins),
        "losses":          len(losses),
        "open_signals":    len(open_s),
        "win_rate":        round(win_rate, 1),
        "best_catalyst":   best_catalyst,
        "worst_catalyst":  worst_catalyst,
        "avg_gain_wins":   round(avg_gain_wins, 1),
        "avg_loss_losses": round(avg_loss_losses, 1),
        "notes":           " | ".join(notes_parts),
    }

    save_weekly_report(report)
    return report


def format_weekly_report_message(report: Dict) -> str:
    """Format the weekly report for Telegram."""
    wr = report.get("win_rate", 0)
    emoji = "🟢" if wr >= 60 else ("🟡" if wr >= 40 else "🔴")

    # Get historical comparison
    history = get_latest_weekly_reports(4)
    trend   = ""
    if len(history) >= 2:
        prev_wr = history[1].get("win_rate", 0) if len(history) > 1 else 0
        delta = wr - prev_wr
        trend = f"(vs last week: {delta:+.1f}%)"

    return (
        f"📊 *IDX Story Bot — Weekly Learning Report*\n"
        f"Week of {report['week_start']}\n"
        f"{'─' * 32}\n\n"

        f"📈 *Performance*\n"
        f"  Total signals : {report['total_signals']}\n"
        f"  Wins          : {report['wins']} ✅\n"
        f"  Losses        : {report['losses']} ❌\n"
        f"  Open          : {report['open_signals']} ⏳\n"
        f"  {emoji} Win Rate   : *{wr:.1f}%* {trend}\n"
        f"  Avg gain (W)  : +{report['avg_gain_wins']:.1f}%\n"
        f"  Avg loss (L)  : {report['avg_loss_losses']:.1f}%\n\n"

        f"🧠 *Learnings*\n"
        f"  Best catalyst  : {report['best_catalyst'].replace('_', ' ').title()}\n"
        f"  Worst catalyst : {report['worst_catalyst'].replace('_', ' ').title()}\n"
        f"  Notes          : _{report['notes']}_\n\n"

        f"🔄 _Catalyst weights will be adjusted for next week based on this data._\n"
        f"⚠️ _Past performance ≠ future results_"
    )


def get_dynamic_catalyst_weights() -> Dict[str, int]:
    """
    Return adjusted catalyst scores based on recent win rates.
    Falls back to defaults if insufficient data.
    """
    from modules.database import get_signals_for_week
    import copy

    weights = copy.copy(config.CATALYST_SCORES)

    # Pull last 4 weeks of signal data
    four_weeks_ago = datetime.utcnow() - timedelta(weeks=4)
    from modules.database import get_open_signals
    all_signals = get_open_signals(max_age_days=28)

    if len(all_signals) < 5:
        return weights  # Not enough data yet

    catalyst_wins   = defaultdict(int)
    catalyst_totals = defaultdict(int)

    for s in all_signals:
        ct = s.get("catalyst_type")
        if not ct:
            continue
        catalyst_totals[ct] += 1
        if s.get("outcome") == "win":
            catalyst_wins[ct] += 1

    for ct, total in catalyst_totals.items():
        if total >= 3 and ct in weights:
            wr = catalyst_wins[ct] / total
            base_score = weights[ct]
            # Boost by up to +2 or reduce by up to -2 based on win rate
            adjustment = round((wr - 0.5) * 4)   # 0% WR → -2, 100% WR → +2
            weights[ct] = max(1, min(10, base_score + adjustment))
            logger.debug("Dynamic weight for %s: %d → %d (WR %.0f%%)",
                         ct, base_score, weights[ct], wr * 100)

    return weights
