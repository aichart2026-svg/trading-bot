# Trading bot (paper trading)

Python paper-trading bot for US stocks. It takes tickers from a Google Sheet (CSV export) or a
manual list, looks for breakout signals, opens virtual positions, tracks stops and targets,
keeps everything in SQLite and reports to Discord. No real money is used.

- `trading_bot.py` signals, virtual positions, SQLite (trades, performance, config, equity_log), Discord notifier
- `check_positions.py` checks stop and target of open positions
- `daily_dashboard.py` daily summary embed in Discord
- `market_check.py` US market holiday check
- `get_pnl.py` unrealized PnL of open positions

Strategies in the journal: `BB_Breakout`, `High_Breakout`, `EMA_Golden_Cross`. Closed trades carry
a `close_reason` (`STOP`, `TARGET`, `ACQUIRED` for corporate events closed by hand).

## Install

```
pip install -r requirements.txt
cp .env.example .env
# edit .env
```

## Run

The scripts read settings from environment variables. Load `.env` before running:

```
set -a; source .env; set +a
python3 trading_bot.py
```

`trading_bot.py` has a time gate: it only works on weekdays around 18:00 Europe/Moscow (change
`_should_run_now` at the top of the file for another schedule). Example cron:

```
*/10 * * * * cd ~/trading_bot && set -a && . ./.env && set +a && python3 trading_bot.py
*/15 * * * * cd ~/trading_bot && set -a && . ./.env && set +a && python3 check_positions.py
*/10 * * * * cd ~/trading_bot && set -a && . ./.env && set +a && python3 daily_dashboard.py
```

## Settings

- `DISCORD_WEBHOOK_URL` required for notifications
- `SHEET_CSV_URL` optional Google Sheet CSV export with tickers; if empty, `MANUAL_TICKERS` from `trading_bot.py` is used

The database is created at `~/trading_bot/trading.db`.

Not financial advice. This is a research tool.
