#!/usr/bin/env python3
"""
Лёгкая, частая проверка открытых позиций на срабатывание стопа/тейка.
Рассчитан на запуск каждые 15 минут в течение торговой сессии (cron).

Что делает:
  - если сейчас не торговая сессия - тихо выходит, ничего не делает, и НЕ
    тянет тяжёлые библиотеки (pandas/yfinance/trading_bot) вообще - см. ниже
    про _is_market_open_light();
  - иначе берёт список открытых сделок из БД, для каждого уникального тикера
    тянет ВНУТРИДНЕВНЫЕ high/low (15-минутные бары текущего дня, не дневной бар);
  - переиспользует PaperTrading.check_close() - тот же код, что уже работает
    в основном дневном прогоне, ничего не дублирует и не пишет по-другому;
  - в Discord уходит уведомление ТОЛЬКО если сделка реально закрылась
    (send_close, тот же формат, что и раньше). Никакого "проверяю..." или
    другого шума на каждый тик - только реальные события.

НЕ делает: не сканирует новые сигналы, не открывает сделки, не считает
self-learning, не шлёт summary. Это всё по-прежнему только в trading_bot.py,
в дневном прогоне.

Про _is_market_open_light(): это НАМЕРЕННО отдельная, лёгкая копия логики
is_market_open() из trading_bot.py (используются только datetime, без
pandas/numpy/yfinance) - нужна, чтобы отсеять холостые тики cron ДО того,
как импортируются тяжёлые библиотеки (иначе CPU-стоимость импорта платится
на каждом из ~90 тиков в день, а не только на реальной работе). Если в
trading_bot.py изменится список праздников (US_HOLIDAYS_2026) или часы
торгов - обновите то же самое здесь.

Требует: trading_bot.py в той же папке (импортируется ЛЕНИВО, только когда
гейт пройден).

Использование (через cron, каждые 15 минут):
    */15 * * * * cd ~/trading_bot && venv/bin/python check_positions.py >> check_positions.log 2>&1
"""
from datetime import datetime

# Синхронизировано с US_HOLIDAYS_2026 в trading_bot.py по состоянию на 2026-08-11.
_US_HOLIDAYS_2026 = {(1, 1), (1, 19), (2, 16), (4, 3), (5, 25), (6, 19), (7, 4), (9, 7), (11, 26), (12, 25)}


def _is_market_open_light(date=None):
    """Лёгкая копия is_market_open() из trading_bot.py - см. докстринг файла."""
    if date is None:
        date = datetime.now()
    if date.weekday() >= 5:
        return False
    if (date.month, date.day) in _US_HOLIDAYS_2026:
        return False
    if date.hour < 13 or date.hour >= 21:
        return False
    return True


def get_intraday_high_low(ticker):
    """Внутридневные high/low за сегодня, 15-минутные бары."""
    import yfinance as yf
    try:
        data = yf.download(ticker, period="1d", interval="15m", progress=False)
        if data is None or len(data) == 0:
            return None
        high = float(data['High'].max().iloc[0]) if hasattr(data['High'].max(), 'iloc') else float(data['High'].max())
        low = float(data['Low'].min().iloc[0]) if hasattr(data['Low'].min(), 'iloc') else float(data['Low'].min())
        return {'high': high, 'low': low}
    except Exception as e:
        print(f"   ⚠️ {ticker}: не удалось получить внутридневные котировки ({e})")
        return None


def main():
    if not _is_market_open_light():
        return

    # тяжёлые импорты - только если гейт пройден
    from trading_bot import Database, PaperTrading, DiscordNotifier, DISCORD_WEBHOOK_URL

    db = Database()
    trader = PaperTrading(db)
    discord = DiscordNotifier(DISCORD_WEBHOOK_URL)

    open_trades = db.get_open_trades()
    if len(open_trades) == 0:
        return

    tickers = open_trades['ticker'].unique().tolist()
    current = {}
    for t in tickers:
        d = get_intraday_high_low(t)
        if d:
            current[t] = d

    closed = trader.check_close(current)
    for c in closed:
        print(f"   {'✅' if c['pnl'] > 0 else '❌'} {c['ticker']} закрыта @ ${c['price']} | "
              f"P&L: ${c['pnl']:.2f} ({c['reason']}) [внутридневная проверка]")
        discord.send_close(c)


if __name__ == "__main__":
    main()
