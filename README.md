# IDX Story Bot 🔥
### Multi-bagger screener for Indonesian equities — finds the story before the price moves

> *"Price follows story. Find the story first, time the entry second."*

---

## Architecture

```
idx_bot/
├── main.py                  ← Entry point, bot + scheduler bootstrap
├── config.py                ← All thresholds, keywords, schedule (edit here)
├── requirements.txt
└── modules/
    ├── story.py             ← Story detection (IDX, KPPU, news RSS)
    ├── technical.py         ← Price data, indicators, trade levels
    ├── signals.py           ← Matching + Telegram message formatting
    ├── database.py          ← SQLite persistence (signals, catalysts, reports)
    ├── self_improve.py      ← Weekly evaluation + dynamic weight adjustment
    ├── commands.py          ← All /command handlers
    └── scheduler.py         ← APScheduler: 09:00, 11:00, 13:00, 15:30 WIB
```

---

## Quick Start

### 1. Create a Telegram Bot
1. Message `@BotFather` on Telegram → `/newbot`
2. Copy the **API token**
3. Add the bot to your channel/group → get the **chat ID** via `@userinfobot`

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Set Environment Variables
```bash
export TELEGRAM_TOKEN="7xxxxxxxxxx:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export TELEGRAM_CHAT_ID="-100xxxxxxxxxx"   # channel: negative number
```

Or create a `.env` file:
```
TELEGRAM_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```
Then add `from dotenv import load_dotenv; load_dotenv()` at the top of `main.py`.

### 4. Run
```bash
python main.py
```

---

## Bot Commands

| Command | Description |
|---------|-------------|
| `/scan` | Run full scan now — story detection + technical filter |
| `/story` | Corporate action disclosures in last 24h |
| `/ticker XXXX` | On-demand analysis of any IDX ticker |
| `/insider` | Recent director/shareholder buying activity |
| `/calendar` | Upcoming RUPSLB, rights issue dates |
| `/rumor` | High social velocity tickers (with disclaimer) |
| `/ihsg` | IHSG market health check |
| `/report` | Weekly win rate + catalyst performance |

---

## Signal Logic

### Step 1 — Story Detection (most important)
Sources scraped every 30 min:
- **IDX Keterbukaan Informasi** — corporate action filings
- **KPPU merger registry** — merger filings appear before IDX
- **News RSS** — Kontan, Bisnis, CNBC Indonesia, Katadata

Catalyst Scores:
| Catalyst | Score |
|----------|-------|
| Asset injection into small cap | 10/10 |
| Strategic acquisition | 9/10 |
| Rights issue + strategic investor | 8/10 |
| Government mega contract | 7/10 |
| Director/insider buying | 6/10 |
| Buyback program | 5/10 |
| Vague rumor only | 2/10 |

### Step 2 — Technical Timing
Only runs on tickers with story score ≥ 6/10.
Requires **≥ 3 of 7** conditions:
1. Volume spike > 3x 20-day average
2. OBV rising while price flat (accumulation)
3. EMA5 crossing above EMA20
4. RSI 14 between 50–70 and rising
5. VCP / flat base forming
6. Weekly MACD bullish crossover or positive divergence
7. Price within 10% of multi-week resistance

Auto-rejected if:
- IHSG below EMA50 for 3+ consecutive days
- Average daily volume < 500K shares
- Rights issue flagged as "for debt repayment"

### Step 3 — Signal Output
- Minimum 3–7 high conviction signals per week
- Full trade plan: entry, stop, T1/T2/T3, R:R ratio
- Position sizing guidance (1–5% portfolio)

---

## Self-Improvement System

Every Sunday at 19:00 WIB:
1. Evaluates all open signals against actual price data
2. Calculates win rate by catalyst type
3. **Dynamically adjusts catalyst scores** ±2 based on actual hit rates
4. Sends learning report to the channel

After 4+ weeks of data, the bot automatically up-weights catalyst types with high win rates and down-weights underperformers.

---

## Deployment on Railway.app

```bash
# Create railway.toml
[build]
builder = "nixpacks"

[deploy]
startCommand = "python main.py"
```

Set env vars in Railway dashboard:
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`
- `DATABASE_PATH=/data/idx_bot.db`  (for persistent volume)

---

## Extending the Bot

### Add Twitter/X Social Velocity
In `modules/story.py`, implement `check_social_velocity()`:
```python
# Requires Twitter API v2 Bearer Token
import tweepy
client = tweepy.Client(bearer_token=os.getenv("TWITTER_BEARER"))
```

### Add AHU Company Registry Scraping
```python
# In modules/story.py — scrape_ahu_registry()
# AHU uses Cloudflare; use Selenium or a proxy service
```

### Tune Thresholds
All thresholds are in `config.py`:
- `MIN_STORY_SCORE` — lower to catch more, higher for precision
- `MIN_TECHNICAL_COUNT` — lower = more signals, higher = more selective
- `VOLUME_SPIKE_RATIO` — adjust for different liquidity profiles

---

## Disclaimer

This bot is for **research and educational purposes only**.  
It is not a financial advisor. Always do your own research (DYOR).  
Past signal performance does not guarantee future results.  
Trading Indonesian equities involves substantial risk of loss.
