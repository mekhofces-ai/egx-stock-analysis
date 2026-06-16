# EGX Telegram Signal Analyst

Production-ready MVP for reading selected Telegram channels/groups, extracting EGX stock recommendations, validating them with market-data and technical analysis, and sending the final result to a private Telegram bot chat.

Disclaimer: Not financial advice.

## Features

- FastAPI backend with SQLite by default and SQLAlchemy ORM
- PostgreSQL-ready database URL via `EGX_DATABASE_URL`
- Alembic migration scaffold with an initial schema
- Telethon listener for active Telegram sources
- Telegram bot commands for source management and manual analysis
- English and Arabic message parser
- CSV, mock, TradingView screener, and experimental TradingView WebSocket provider interfaces
- Technical indicators: SMA, EMA, RSI, MACD, Bollinger Bands, ATR, volume spike, support/resistance, breakout, trend, liquidity, volatility, technical score, risk score
- Signal validation engine with confidence, warnings, final decision, entry, stop, targets, invalidation, position sizing
- Chart image download and metadata placeholder for OCR/AI vision
- Streamlit dashboard for channels, messages, signals, screener, strategy backtests, manual analysis, performance, settings, and imports
- Smart all-stocks and stock-detail views with TradingView chart links/widgets, Telegram consensus, latest recommendation, and Smart PRO action overlay
- Telegram source import from CSV or Excel
- APScheduler jobs for Telegram fetch, pending analysis, BUY alerts, strategy-aware daily report, and channel performance
- Docker and docker-compose support

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and set at least:

```env
EGX_DATABASE_URL=sqlite:///./egx_signals.db
MARKET_DATA_PROVIDER_PRIORITY=tradingview_screener,tradingview_websocket,csv
TELEGRAM_BOT_TOKEN=
TELEGRAM_BOT_PRIVATE_CHAT_ID=
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
```

Do not commit `.env`; it contains secrets.

## Run

```bash
pip install -r requirements.txt
python -m app.main
streamlit run dashboard/streamlit_app.py --server.port 8509
```

FastAPI defaults to `http://localhost:8000`. Streamlit runs locally at `http://localhost:8509`.

## Database

The app creates SQLite tables automatically on startup for MVP use:

- `telegram_sources`
- `telegram_messages`
- `stocks`
- `market_prices`
- `extracted_signals`
- `technical_analysis`
- `final_analysis`
- `channel_performance`
- `bot_users`
- `app_settings`
- `jobs_log`

Alembic is available:

```bash
alembic upgrade head
```

For PostgreSQL later, install requirements and set:

```env
EGX_DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/egx_signals
```

## Telegram API ID And Hash

1. Open `https://my.telegram.org`.
2. Sign in with your Telegram account.
3. Open `API development tools`.
4. Create an app and copy `api_id` and `api_hash`.
5. Put them in `.env` as `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`.

The first Telethon login may require an interactive verification code. After that, the session file is reused.

## Telegram Bot

Create a bot:

1. Open Telegram and message `@BotFather`.
2. Run `/newbot`.
3. Copy the token into `TELEGRAM_BOT_TOKEN`.

Get your chat ID:

1. Message your bot once.
2. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`.
3. Copy `message.chat.id` into `TELEGRAM_BOT_PRIVATE_CHAT_ID`.

Set admin chats by setting:

```env
TELEGRAM_BOT_ALLOWED_CHAT_IDS=123456789
```

Users who message the bot are saved as pending until an admin approves them. Pending users can request access, but they cannot use analysis commands or receive BUY alerts yet.

## Bot Commands

- `/start`
- `/stock SYMBOL`
- `/brief SYMBOL`
- `/analyze SYMBOL`
- `/latest`
- `/daily_report`
- `/opportunities`
- `/night_report`
- `/depth`

Admin-only commands:

- `/pending_users`
- `/approve_user CHAT_ID`
- `/reject_user CHAT_ID`
- `/users`
- `/add_channel @channel1 @channel2`
- `/remove_channel @channel1 @channel2`
- `/list_channels`
- `/pause_channel @channel1 @channel2`
- `/activate_channel @channel1 @channel2`
- `/refresh_sources`

Users can also send a plain stock symbol, such as `COMI`, after approval to receive the full stock brief. Every alert includes: `Disclaimer: Not financial advice.`

## BUY Notifications

The app can send private Telegram notifications when a BUY signal or BUY recommendation is found. Set these in `.env`:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_BOT_PRIVATE_CHAT_ID=123456789
TELEGRAM_BOT_VERIFY_TLS=true
TELEGRAM_ALERT_ENABLED=true
TELEGRAM_ALERT_DECISIONS=BUY
TELEGRAM_ALERT_MIN_CONFIDENCE=70
TELEGRAM_ALERT_RECOMMENDATIONS_ENABLED=true
TELEGRAM_ALERT_REQUIRE_TELEGRAM_CONFIRMATION=true
TELEGRAM_ALERT_SCAN_INTERVAL_MINUTES=10
NIGHT_OPPORTUNITY_REPORT_ENABLED=true
NIGHT_OPPORTUNITY_REPORT_HOUR=21
NIGHT_OPPORTUNITY_TOP_N=7
```

Behavior:

- New Telegram messages are analyzed, and eligible BUY analyses are sent once.
- Final recommendations are scanned on the scheduler interval and sent once per symbol per day.
- BUY notifications are sent to admin chats and all approved active bot users.
- `TELEGRAM_ALERT_REQUIRE_TELEGRAM_CONFIRMATION=true` means screener BUY recommendations are only sent when Telegram also mentioned the stock.
- A night opportunity report is sent at `NIGHT_OPPORTUNITY_REPORT_HOUR` with next-session BUY/WATCH candidates ranked from screener, Telegram, and strategy analysis.
- Use `Final Recommendations > Send BUY alerts now`, `Settings > Send pending BUY alerts now`, or `Settings > Send night opportunities now` to test manually.
- If your local network injects a self-signed certificate and bot startup fails with `CERTIFICATE_VERIFY_FAILED`, set `TELEGRAM_BOT_VERIFY_TLS=false` locally.

## Add Channels

You can add channels in three ways:

- Streamlit: open `Telegram channels`
- `.env`: set `TELEGRAM_SOURCE_CHANNELS=@channel1,@channel2`
- Bot: send `/add_channel @channel`
- Excel/CSV import: use `Imports > Telegram Sources Excel/CSV` or `Telegram Channels > Import channels`

Only active sources are read. The listener stores only new messages, avoids duplicates with `(source_id, message_id)`, and continues from `last_message_id`. Adding channels from the dashboard, API, or bot can trigger an immediate fetch/analyze cycle.

Source import columns:

```csv
username,title,source_type,is_active,trust_score,notes
@egyptianborsa,Egyptian Borsa,channel,true,50,Seed example source
https://t.me/example_channel,Example Channel,channel,true,50,Links are accepted too
```

Sample file: `data/telegram_sources_sample.csv`.

## Listener And Scheduler

`python -m app.main` starts FastAPI. If scheduler is enabled, it runs:

- fetch Telegram messages every `TELEGRAM_FETCH_INTERVAL_MINUTES`
- analyze pending messages every `ANALYSIS_INTERVAL_MINUTES`
- update performance every `PERFORMANCE_INTERVAL_MINUTES`
- send a daily report at `DAILY_REPORT_HOUR`
- scan final recommendations for BUY alerts every `TELEGRAM_ALERT_SCAN_INTERVAL_MINUTES`

Run one listener pass manually:

```bash
python -c "from app.services.telegram_listener import fetch_active_channels_once; print(fetch_active_channels_once())"
```

## Market Data Providers

Configure priority:

```env
MARKET_DATA_PROVIDER_PRIORITY=tradingview_screener,tradingview_websocket,csv
MARKET_DATA_ALLOW_MOCK=false
TRADINGVIEW_AUTH_TOKEN=unauthorized_user_token
TRADINGVIEW_WS_URL=wss://data.tradingview.com/socket.io/websocket
```

Providers:

- `CSVProvider`: reads `data/ohlcv/{SYMBOL}.csv` or `data/ohlcv_sample.csv`; intraday frames use real timestamps or a `timeframe` column
- `MockProvider`: deterministic mock data for testing only, including 15m/1h/4h/1D frames; disabled unless `MARKET_DATA_ALLOW_MOCK=true`
- `TradingViewScreenerProvider`: attempts the TradingView screener endpoint for Egypt/EGX snapshot fields
- `TradingViewWebSocketProvider`: attempts TradingView chart-session OHLCV candles for 15m/1h/4h/1D strategy backtests

TradingView warning: TradingView screener/chart-session usage may be unofficial, fragile, rate-limited, or changed without notice. Production-quality backtests should use licensed or exported OHLCV data when available. The strategy refuses stale candles, mock candles, or candles whose last close is too far from the TradingView reference quote.

## CSV Formats

Stocks CSV:

```csv
symbol,name_ar,name_en,sector,tradingview_symbol,is_active
COMI,البنك التجاري الدولي,Commercial International Bank,Banks,EGX:COMI,true
```

OHLCV CSV:

```csv
symbol,date,open,high,low,close,volume
COMI,2026-04-26,102.90,104.50,102.20,103.90,17100000
```

Samples are in `data/stocks_sample.csv` and `data/ohlcv_sample.csv`.

Market depth CSV:

```csv
timestamp,source,symbol,side,level,price,quantity,num_orders
2026-06-03 14:30:00,thndr_export,COMI,bid,1,131.70,12000,4
2026-06-03 14:30:00,thndr_export,COMI,ask,1,131.90,8500,2
```

Put depth files in `data/market_depth` or upload them from `Imports > Market Depth CSV`. The dashboard page `Market Depth`, API `GET /market-depth/screener`, and bot command `/depth` screen bid/ask pressure.

## Docker

```bash
docker compose up --build
```

API: `http://localhost:8000`

Dashboard: `http://localhost:8509`

## Tests

```bash
pytest
```

Current tests focus on parser behavior for English and Arabic recommendations.

## API Highlights

- `GET /health`
- `GET /sources`
- `POST /sources`
- `PATCH /sources/{id}`
- `POST /sources/{id}/pause`
- `POST /sources/{id}/activate`
- `GET /messages`
- `GET /signals`
- `POST /signals/analyze-pending`
- `GET /analysis/latest`
- `POST /analyze`
- `GET /analyze/{symbol}`
- `GET /stocks`
- `POST /stocks/import-csv`
- `GET /stocks/screener?filter_name=top_volume`
- `GET /stocks/{symbol}/detail`
- `POST /sources/import-file?fetch_now=true`
- `POST /sources/refresh-now`
- `GET /recommendations/screener`
- `GET /alerts/status`
- `POST /alerts/send-buy-now`
- `GET /strategy/backtest?limit=30`
- `GET /strategy/backtest/{symbol}?timeframes=15m,1h,4h,1D`
- `GET /market-depth/screener`

## Multi-Timeframe Strategy

The strategy engine tests `15m`, `1h`, `4h`, and `1D` frames with:

- EMA20/EMA50/EMA200 trend bias
- RSI stretch/confirmation
- MACD confirmation
- volume ratio and 20-candle breakout checks
- ATR/support-based stop and 2R target
- one-position-at-a-time backtest with win rate, total return, and max drawdown

Configure:

```env
STRATEGY_TIMEFRAMES=15m,1h,4h,1D
STRATEGY_SYMBOL_LIMIT=30
STRATEGY_BACKTEST_BARS=260
STRATEGY_ALLOW_MOCK_DATA=false
STRATEGY_MAX_DAILY_AGE_DAYS=14
STRATEGY_MAX_INTRADAY_AGE_DAYS=7
STRATEGY_PRICE_TOLERANCE_PERCENT=15
DAILY_REPORT_INCLUDE_STRATEGY=true
DAILY_REPORT_TOP_N=10
```

Rows with unavailable, stale, mock, or price-mismatched candles are blocked from live strategy scoring. Enable mock strategy data only for testing.

## Smart PRO Overlay

The dashboard includes a Smart PRO decision overlay inspired by the Pine strategy in this workspace. Because the Python app receives TradingView screener snapshots rather than full TradingView candle series, it estimates:

- action now
- plan
- trend
- pressure
- volume status
- buy zone
- suggested entry
- stop
- scalp/swing/long targets

Use the TradingView chart widget on `Stock Detail` to visually confirm setups. Not financial advice.

## Alert Format

```text
📊 EGX Signal Analysis
Stock: COMI
Source: @channel
Telegram Direction: BUY
Last Price: 103.9
Trend: UPTREND

Decision: BUY
Confidence: 76%
Risk: 52%

Entry: 102.50 - 104.20
Stop Loss: 98.5
Targets: 108.0, 112.0

Reasons:
1. Buy signal aligns with uptrend and volume spike.

Warnings:
* Telegram signal has no stop loss.

Disclaimer: Not financial advice.
```

Use this for analysis and education only. Not financial advice.
