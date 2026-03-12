"""
modules/utils.py — Safe Telegram formatting utilities
"""
import re

def safe_md(text: str) -> str:
    """
    Escape characters that break Telegram MarkdownV1 parser.
    Use this on any dynamic value before inserting into message strings.
    """
    if not text:
        return ""
    # Escape underscores and asterisks inside values (not our formatting)
    # For MarkdownV1, main culprits are: _ * ` [
    # We only escape inside dynamic values, not the whole string
    text = str(text)
    # Remove any stray markdown that could break parsing
    text = text.replace("*", "").replace("`", "").replace("[", "(").replace("]", ")")
    return text

def rp(v: float) -> str:
    """Format as Rupiah string, safe for Telegram."""
    try:
        return f"Rp {v:,.0f}"
    except Exception:
        return "Rp 0"

def pct(v: float, decimals: int = 1) -> str:
    """Format as percentage string."""
    try:
        return f"{v:+.{decimals}f}%"
    except Exception:
        return "0%"

def stars(score: int) -> str:
    """Convert 0-100 score to star rating."""
    if score >= 80: return "⭐⭐⭐⭐⭐"
    if score >= 65: return "⭐⭐⭐⭐"
    if score >= 50: return "⭐⭐⭐"
    if score >= 35: return "⭐⭐"
    return "⭐"

def chunk_message(text: str, size: int = 4000):
    """Split long message into Telegram-safe chunks."""
    return [text[i:i+size] for i in range(0, len(text), size)]
