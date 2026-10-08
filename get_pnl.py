#!/usr/bin/env python3
"""
Показывает нереализованный P&L по всем открытым позициям в trading.db.
Использует тот же публичный эндпоинт Yahoo Finance, что и сам бот, поэтому
цифры будут согласованы с логикой check_close() в trading_bot.py.

Использование:
    python3 get_pnl.py                    # ищет trading.db в текущей папке
    python3 get_pnl.py /path/to/trading.db
"""
import sqlite3
import sys
import requests

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "trading.db"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def get_last_price(ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    r = requests.get(url, headers=HEADERS, params={"range": "5d", "interval": "1d"}, timeout=10)
    r.raise_for_status()
    data = r.json()
    result = data["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    closes = [c for c in quote["close"] if c is not None]
    if not closes:
        raise ValueError("пустой ответ от Yahoo Finance")
    return closes[-1]


def compute_pnl(signal, entry, size, last_price):
    if signal == "BUY":
        pnl = (last_price - entry) * size
        pnl_pct = (last_price - entry) / entry * 100
    else:
        pnl = (entry - last_price) * size
        pnl_pct = (entry - last_price) / entry * 100
    return pnl, pnl_pct


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, ticker, signal, strategy, entry_price, stop_price, target_price, size, open_date "
        "FROM trades WHERE status='OPEN' ORDER BY ticker"
    ).fetchall()

    if not rows:
        print("Открытых позиций нет.")
        return

    tickers = sorted(set(r["ticker"] for r in rows))
    prices, errors = {}, {}
    for t in tickers:
        try:
            prices[t] = get_last_price(t)
        except Exception as e:
            errors[t] = str(e)

    header = f"{'TICKER':8} {'SIDE':5} {'STRATEGY':18} {'ENTRY':>9} {'LAST':>9} {'PNL $':>10} {'PNL %':>8}  {'ОТКРЫТА':10}"
    print(header)
    print("-" * len(header))

    total_pnl = 0.0
    missing = []
    for r in rows:
        t = r["ticker"]
        if t in errors:
            print(f"{t:8} {r['signal']:5} {r['strategy']:18} {r['entry_price']:>9.2f} {'н/д':>9}  -- нет цены --")
            missing.append(t)
            continue
        last = prices[t]
        pnl, pnl_pct = compute_pnl(r["signal"], r["entry_price"], r["size"], last)
        total_pnl += pnl

        flag = ""
        if r["signal"] == "BUY" and last <= r["stop_price"]:
            flag = "  <- ниже стопа (должна была закрыться)"
        elif r["signal"] == "SELL" and last >= r["stop_price"]:
            flag = "  <- выше стопа (должна была закрыться)"

        print(f"{t:8} {r['signal']:5} {r['strategy']:18} {r['entry_price']:>9.2f} "
              f"{last:>9.2f} {pnl:>10.2f} {pnl_pct:>7.1f}%  {r['open_date']:10}{flag}")

    print("-" * len(header))
    print(f"{'ИТОГО нереализованный P&L:':>62} {total_pnl:>10.2f}")
    if missing:
        print(f"\nНе удалось получить цену для: {', '.join(missing)} "
              f"(возможно, тикер снят с торгов или временно недоступен у Yahoo Finance)")


if __name__ == "__main__":
    main()
