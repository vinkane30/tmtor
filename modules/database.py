"""
modules/database.py — SQLite persistence layer
Tables: signals, catalysts, self_improvement_log
"""

import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import DATABASE_PATH

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# CONNECTION HELPERS
# ─────────────────────────────────────────────

@contextmanager
def get_conn():
    conn = sqlite3.connect(DATABASE_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker          TEXT NOT NULL,
            company_name    TEXT,
            story_score     INTEGER,
            tech_count      INTEGER,
            catalyst_type   TEXT,
            catalyst_source TEXT,
            entry_low       REAL,
            entry_high      REAL,
            stop_loss       REAL,
            t1              REAL,
            t2              REAL,
            t3              REAL,
            rr_ratio        REAL,
            sent_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            outcome         TEXT,       -- 'win' | 'loss' | 'open' | 'expired'
            max_gain_pct    REAL,
            evaluated_at    TIMESTAMP,
            notes           TEXT
        );

        CREATE TABLE IF NOT EXISTS catalysts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker          TEXT,
            catalyst_type   TEXT,
            headline        TEXT,
            source_url      TEXT,
            published_at    TIMESTAMP,
            score           INTEGER,
            seen_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS self_improvement_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start      DATE,
            total_signals   INTEGER,
            wins            INTEGER,
            losses          INTEGER,
            open_signals    INTEGER,
            win_rate        REAL,
            best_catalyst   TEXT,
            worst_catalyst  TEXT,
            avg_gain_wins   REAL,
            avg_loss_losses REAL,
            notes           TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_signals_ticker   ON signals(ticker);
        CREATE INDEX IF NOT EXISTS idx_signals_sent     ON signals(sent_at);
        CREATE INDEX IF NOT EXISTS idx_catalysts_ticker ON catalysts(ticker);
        CREATE INDEX IF NOT EXISTS idx_catalysts_seen   ON catalysts(seen_at);
        """)
    logger.info("Database initialised at %s", DATABASE_PATH)


# ─────────────────────────────────────────────
# SIGNALS
# ─────────────────────────────────────────────

def save_signal(signal: Dict[str, Any]) -> int:
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO signals
              (ticker, company_name, story_score, tech_count, catalyst_type,
               catalyst_source, entry_low, entry_high, stop_loss,
               t1, t2, t3, rr_ratio)
            VALUES
              (:ticker, :company_name, :story_score, :tech_count, :catalyst_type,
               :catalyst_source, :entry_low, :entry_high, :stop_loss,
               :t1, :t2, :t3, :rr_ratio)
        """, signal)
        return cur.lastrowid


def get_open_signals(max_age_days: int = 30) -> List[Dict]:
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM signals
            WHERE (outcome IS NULL OR outcome = 'open')
              AND sent_at > ?
            ORDER BY sent_at DESC
        """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def update_signal_outcome(signal_id: int, outcome: str,
                          max_gain_pct: float, notes: str = ""):
    with get_conn() as conn:
        conn.execute("""
            UPDATE signals
            SET outcome = ?, max_gain_pct = ?, evaluated_at = ?, notes = ?
            WHERE id = ?
        """, (outcome, max_gain_pct, datetime.utcnow(), notes, signal_id))


def was_ticker_signalled_recently(ticker: str, hours: int = 48) -> bool:
    """Avoid re-sending the same ticker within the cooldown window."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) FROM signals
            WHERE ticker = ? AND sent_at > ?
        """, (ticker, cutoff)).fetchone()
    return row[0] > 0


def get_signals_for_week(week_start: datetime) -> List[Dict]:
    week_end = week_start + timedelta(days=7)
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM signals
            WHERE sent_at >= ? AND sent_at < ?
        """, (week_start, week_end)).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────
# CATALYSTS
# ─────────────────────────────────────────────

def save_catalyst(catalyst: Dict[str, Any]):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO catalysts
              (ticker, catalyst_type, headline, source_url, published_at, score)
            VALUES
              (:ticker, :catalyst_type, :headline, :source_url, :published_at, :score)
        """, catalyst)


def get_recent_catalysts(hours: int = 24) -> List[Dict]:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM catalysts
            WHERE seen_at > ?
            ORDER BY score DESC, seen_at DESC
        """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def catalyst_already_seen(headline: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) FROM catalysts WHERE headline = ?
        """, (headline,)).fetchone()
    return row[0] > 0


# ─────────────────────────────────────────────
# SELF-IMPROVEMENT LOG
# ─────────────────────────────────────────────

def save_weekly_report(report: Dict[str, Any]):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO self_improvement_log
              (week_start, total_signals, wins, losses, open_signals,
               win_rate, best_catalyst, worst_catalyst,
               avg_gain_wins, avg_loss_losses, notes)
            VALUES
              (:week_start, :total_signals, :wins, :losses, :open_signals,
               :win_rate, :best_catalyst, :worst_catalyst,
               :avg_gain_wins, :avg_loss_losses, :notes)
        """, report)


def get_latest_weekly_reports(n: int = 4) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM self_improvement_log
            ORDER BY week_start DESC LIMIT ?
        """, (n,)).fetchall()
    return [dict(r) for r in rows]
