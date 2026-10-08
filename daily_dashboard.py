#!/usr/bin/env python3
"""
Дневной дэшборд: открытые позиции (с нереализованным P&L), сделки, закрытые
сегодня, общий P&L (реализованный + нереализованный), альфа против S&P 500.

Read-only: не пишет в БД, не открывает и не закрывает сделки. Отправляется
в Discord раз в день, около 22:00 МСК (в этот момент рынок США ещё открыт
и в EDT, и в EST - можно получить актуальные внутридневные котировки).

Требует: trading_bot.py в той же папке (использует его Database и константы).

Cron (гейт по времени внутри скрипта, cron может дёргать часто):
    */10 * * * * cd ~/trading_bot && venv/bin/python daily_dashboard.py >> daily_dashboard.log 2>&1
"""
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def get_last_price(ticker):
    """Последняя доступная внутридневная цена закрытия."""
    import yfinance as yf
    try:
        data = yf.download(ticker, period="1d", interval="15m", progress=False)
        if data is None or len(data) == 0:
            return None
        close = data['Close'].iloc[-1]
        if hasattr(close, 'iloc'):
            close = close.iloc[0]
        return float(close)
    except Exception as e:
        print(f"[get_last_price] {ticker}: {e}", file=sys.stderr)
        return None


def get_spy_return(start_date):
    """Доходность SPY в % с start_date по сегодня. None, если не удалось посчитать."""
    import yfinance as yf
    try:
        spy_data = yf.download('SPY', start=start_date, end=datetime.now().strftime('%Y-%m-%d'), progress=False)
        if len(spy_data) == 0:
            return None
        spy_close = spy_data['Close']
        if hasattr(spy_close, 'columns'):
            spy_close = spy_close.iloc[:, 0]
        spy_close = spy_close.dropna()
        if len(spy_close) < 2:
            return None
        spy_start = float(spy_close.iloc[0])
        spy_end = float(spy_close.iloc[-1])
        return (spy_end - spy_start) / spy_start * 100
    except Exception:
        return None


def fmt_list(rows, limit=1000):
    text = "\n".join(rows) if rows else "—"
    if len(text) > limit:
        text = text[:limit] + "\n… (обрезано)"
    return text


def main():
    import sys
    force = "--force" in sys.argv
    now_msk = datetime.now(ZoneInfo("Europe/Moscow"))
    if not force and not (now_msk.weekday() < 5 and now_msk.hour == 22 and now_msk.minute < 10):
        return

    # тяжёлые импорты - только если гейт пройден
    import sqlite3
    import pandas as pd
    import requests
    from trading_bot import Database, DISCORD_WEBHOOK_URL, INITIAL_BALANCE

    db = Database()
    conn = sqlite3.connect(db.path)
    trades = pd.read_sql("SELECT * FROM trades", conn)
    conn.close()

    if len(trades) == 0:
        return

    closed = trades[trades['status'] == 'CLOSED']
    open_trades = trades[trades['status'] == 'OPEN']

    today = datetime.now().strftime('%Y-%m-%d')
    closed_today = closed[closed['close_date'] == today]

    realized_pnl = float(closed['pnl'].sum()) if len(closed) > 0 else 0.0

    # нереализованный P&L по открытым позициям
    unrealized_pnl = 0.0
    open_lines = []
    tickers = open_trades['ticker'].unique().tolist()
    prices = {t: get_last_price(t) for t in tickers}

    for _, t in open_trades.iterrows():
        price = prices.get(t['ticker'])
        if price is None:
            open_lines.append(f"{t['ticker']}: нет котировки")
            continue
        if t['signal'] == 'BUY':
            pnl = (price - t['entry_price']) * t['size']
        else:
            pnl = (t['entry_price'] - price) * t['size']
        unrealized_pnl += pnl
        pct = pnl / (t['entry_price'] * t['size']) * 100 if t['entry_price'] * t['size'] != 0 else 0
        open_lines.append(f"{t['ticker']} {t['signal']} {t['strategy']}: {pnl:+.0f}$ ({pct:+.1f}%)")

    closed_today_lines = [
        f"{t['ticker']} {t['strategy']}: {t['pnl']:+.0f}$ ({t['close_reason']})"
        for _, t in closed_today.iterrows()
    ]

    # Итоги недели (по пятницам) и месяца (в последний торговый день месяца)
    today_date = now_msk.date()
    week_start = (today_date - timedelta(days=today_date.weekday())).strftime('%Y-%m-%d')
    month_start = today_date.replace(day=1).strftime('%Y-%m-%d')
    is_friday = now_msk.weekday() == 4
    is_last_day_of_month = (today_date + timedelta(days=1)).month != today_date.month

    week_field = None
    if is_friday:
        closed_week = closed[closed['close_date'] >= week_start]
        if len(closed_week) > 0:
            wk_pnl = float(closed_week['pnl'].sum())
            wk_wins = len(closed_week[closed_week['pnl'] > 0])
            wk_total = len(closed_week)
            week_field = {
                "name": f"📅 Итоги недели (с {week_start})",
                "value": f"Сделок: {wk_total} | Win rate: {wk_wins/wk_total*100:.0f}% | P&L: ${wk_pnl:+.2f}",
                "inline": False
            }

    month_field = None
    if is_last_day_of_month:
        closed_month = closed[closed['close_date'] >= month_start]
        if len(closed_month) > 0:
            mo_pnl = float(closed_month['pnl'].sum())
            mo_wins = len(closed_month[closed_month['pnl'] > 0])
            mo_total = len(closed_month)
            month_field = {
                "name": f"🗓️ Итоги месяца (с {month_start})",
                "value": f"Сделок: {mo_total} | Win rate: {mo_wins/mo_total*100:.0f}% | P&L: ${mo_pnl:+.2f}",
                "inline": False
            }

    total_pnl = realized_pnl + unrealized_pnl
    balance = INITIAL_BALANCE + realized_pnl
    total_equity = INITIAL_BALANCE + total_pnl

    first_trade_date = trades['open_date'].min()
    spy_return = get_spy_return(first_trade_date)

    fields = [
        {"name": "💰 Баланс (реализованный)", "value": f"${balance:,.2f}", "inline": True},
        {"name": "🧮 Текущий капитал", "value": f"${total_equity:,.2f}", "inline": True},
        {"name": "📈 Общий P&L", "value": f"${total_pnl:,.2f} ({total_pnl/INITIAL_BALANCE*100:+.1f}%)", "inline": True},
        {"name": f"🔒 Закрыто сегодня ({len(closed_today)})", "value": fmt_list(closed_today_lines), "inline": False},
        {"name": f"📂 Открыто сейчас ({len(open_trades)}), нереал. P&L: ${unrealized_pnl:+.2f}",
         "value": fmt_list(open_lines), "inline": False},
    ]
    if week_field:
        fields.append(week_field)
    if month_field:
        fields.append(month_field)

    if spy_return is not None:
        bot_return = total_pnl / INITIAL_BALANCE * 100
        diff = bot_return - spy_return
        emoji = "🟢" if diff > 0 else "🔴"
        fields.append({
            "name": "📊 Альфа vs S&P 500",
            "value": f"Бот: {bot_return:+.1f}% | SPY: {spy_return:+.1f}% | {emoji} {diff:+.1f}%",
            "inline": False
        })

    embed = {
        "title": f"Дневной дэшборд | {datetime.now().strftime('%d.%m.%Y')}",
        "fields": fields,
        "timestamp": datetime.utcnow().isoformat()
    }

    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=15)
        print("Дневной дэшборд отправлен в Discord")
    except Exception as e:
        print(f"⚠️ Ошибка отправки дэшборда: {e}")


if __name__ == "__main__":
    main()
