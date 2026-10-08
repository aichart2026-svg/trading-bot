#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys as _sys
from datetime import datetime as _datetime
from zoneinfo import ZoneInfo as _ZoneInfo


def _should_run_now():
    """Гейт по времени - реальная работа только по будням (кроме субботы),
    рядом с 18:00 Europe/Moscow. Проверяется здесь, ДО тяжёлых импортов
    (pandas/numpy/requests), чтобы холостые тики cron (файл запускается
    каждые 10 минут) не платили за их загрузку. Использует отдельные,
    подчёркнутые имена (_datetime/_ZoneInfo), чтобы не пересекаться с
    обычными datetime/ZoneInfo, которые импортируются чуть ниже и
    используются во всём остальном файле."""
    now_msk = _datetime.now(_ZoneInfo("Europe/Moscow"))
    return now_msk.weekday() != 5 and now_msk.hour == 18 and now_msk.minute < 10


if __name__ == "__main__" and not _should_run_now():
    _sys.exit(0)

import sqlite3
import json
import os
import sys
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np
import requests

INITIAL_BALANCE = 10000.00
SHEET_CSV_URL = os.environ.get("SHEET_CSV_URL", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
MANUAL_TICKERS = ["AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "GOOGL", "META", "PLTR", "AMD", "INTC"]
USE_SHEET = bool(SHEET_CSV_URL)
DB_PATH = os.path.expanduser("~/trading_bot/trading.db")
US_HOLIDAYS_2026 = {(1, 1), (1, 19), (2, 16), (4, 3), (5, 25), (6, 19), (7, 4), (9, 7), (11, 26), (12, 25)}

def is_market_open(date=None):
    if date is None:
        date = datetime.now()
    if date.weekday() >= 5:
        return False
    if (date.month, date.day) in US_HOLIDAYS_2026:
        return False
    # Только в часы торгов ET (примерно 13:30-20:00 UTC)
    if date.hour < 13 or date.hour >= 21:
        return False
    return True

def next_trading_day(date=None):
    if date is None:
        date = datetime.now()
    next_day = date + timedelta(days=1)
    next_day = next_day.replace(hour=14, minute=0, second=0, microsecond=0)
    while not is_market_open(next_day):
        next_day += timedelta(days=1)
        next_day = next_day.replace(hour=14, minute=0, second=0, microsecond=0)
    return next_day.strftime('%Y-%m-%d')

class Database:
    def __init__(self, path=DB_PATH):
        self.path = path
        self._init()

    def _init(self):
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT, signal TEXT, strategy TEXT,
            entry_price REAL, stop_price REAL, target_price REAL,
            size INTEGER, status TEXT, open_date TEXT, close_date TEXT,
            exit_price REAL, pnl REAL, pnl_pct REAL,
            market_regime TEXT, close_reason TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS performance (
            strategy TEXT PRIMARY KEY,
            total_trades INTEGER, wins INTEGER, losses INTEGER,
            win_rate REAL, avg_win REAL, avg_loss REAL,
            expectancy REAL, total_pnl REAL, last_updated TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY, value TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS equity_log (
            date TEXT PRIMARY KEY, balance REAL)""")
        conn.commit()
        conn.close()

    def add_trade(self, trade):
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute("""INSERT INTO trades 
            (ticker, signal, strategy, entry_price, stop_price, target_price,
             size, status, open_date, market_regime)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (trade['ticker'], trade['signal'], trade['strategy'],
             trade['entry'], trade['stop'], trade['target'],
             trade['size'], 'OPEN', trade['date'], trade.get('regime', 'unknown')))
        conn.commit()
        conn.close()

    def close_trade(self, trade_id, exit_price, pnl, pnl_pct, reason):
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute("""UPDATE trades SET status='CLOSED', exit_price=?, pnl=?, 
            pnl_pct=?, close_date=?, close_reason=? WHERE id=?""",
            (exit_price, pnl, pnl_pct, datetime.now().strftime('%Y-%m-%d'), reason, trade_id))
        conn.commit()
        conn.close()

    def get_open_trades(self):
        conn = sqlite3.connect(self.path)
        df = pd.read_sql("SELECT * FROM trades WHERE status='OPEN'", conn)
        conn.close()
        return df

    def get_closed_trades(self, strategy=None):
        conn = sqlite3.connect(self.path)
        if strategy:
            df = pd.read_sql(f"SELECT * FROM trades WHERE strategy='{strategy}' AND status='CLOSED'", conn)
        else:
            df = pd.read_sql("SELECT * FROM trades WHERE status='CLOSED' ORDER BY close_date", conn)
        conn.close()
        return df

    def update_performance(self, strategy):
        df = self.get_closed_trades(strategy)
        if len(df) == 0:
            return
        total = len(df)
        wins = len(df[df['pnl'] > 0])
        losses = total - wins
        win_rate = wins / total if total > 0 else 0
        avg_win = df[df['pnl'] > 0]['pnl_pct'].mean() if wins > 0 else 0
        avg_loss = df[df['pnl'] <= 0]['pnl_pct'].mean() if losses > 0 else 0
        expectancy = (win_rate * avg_win + (1-win_rate) * avg_loss) if total > 0 else 0
        total_pnl = df['pnl'].sum()
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute("""INSERT OR REPLACE INTO performance 
            (strategy, total_trades, wins, losses, win_rate, avg_win, avg_loss,
             expectancy, total_pnl, last_updated)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (strategy, total, wins, losses, win_rate, avg_win, avg_loss,
             expectancy, total_pnl, datetime.now().strftime('%Y-%m-%d')))
        conn.commit()
        conn.close()

    def get_config(self, key, default=None):
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute("SELECT value FROM config WHERE key=?", (key,))
        row = c.fetchone()
        conn.close()
        if row:
            return json.loads(row[0])
        return default

    def set_config(self, key, value):
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?,?)",
                  (key, json.dumps(value)))
        conn.commit()
        conn.close()

    def log_equity(self, balance):
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO equity_log (date, balance) VALUES (?,?)",
                  (datetime.now().strftime('%Y-%m-%d'), balance))
        conn.commit()
        conn.close()

    def get_equity_history(self):
        conn = sqlite3.connect(self.path)
        df = pd.read_sql("SELECT * FROM equity_log ORDER BY date", conn)
        conn.close()
        return df

class SelfLearning:
    def __init__(self, db):
        self.db = db

    def adapt(self):
        print("\n🧠 Self-Learning: анализирую производительность...")
        conn = sqlite3.connect(self.db.path)
        strategies = pd.read_sql("SELECT DISTINCT strategy FROM trades WHERE status='CLOSED'", conn)
        conn.close()
        config = {
            'disabled_strategies': [],
            'risk_per_trade': 0.01,
            'max_position_pct': 0.25,
            'min_win_rate': 0.35,
            'atr_stop_mult': 1.5,
            'atr_target_mult': 2.5
        }
        for _, row in strategies.iterrows():
            strat = row['strategy']
            self.db.update_performance(strat)
            conn = sqlite3.connect(self.db.path)
            perf = pd.read_sql(f"SELECT * FROM performance WHERE strategy='{strat}'", conn)
            conn.close()
            if len(perf) > 0 and perf.iloc[0]['total_trades'] >= 5:
                wr = perf.iloc[0]['win_rate']
                print(f"   📊 {strat}: {perf.iloc[0]['total_trades']} сделок, WR={wr:.1%}, P&L=${perf.iloc[0]['total_pnl']:.2f}")
                if wr < 0.35 and perf.iloc[0]['total_trades'] >= 10:
                    config['disabled_strategies'].append(strat)
                    print(f"   🚫 {strat} отключена (низкий win rate)")
        equity = self._get_equity_series()
        if len(equity) >= 10:
            current = equity[-1]
            peak = max(equity)
            drawdown = (peak - current) / peak if peak > 0 else 0
            recent = self.db.get_closed_trades().tail(20)
            if len(recent) > 0:
                recent_wr = len(recent[recent['pnl'] > 0]) / len(recent)
                if drawdown > 0.15:
                    config['risk_per_trade'] = 0.005
                    print(f"   📉 Просадка {drawdown:.1%} → риск снижен до 0.5%")
                elif recent_wr > 0.60 and drawdown < 0.10:
                    config['risk_per_trade'] = 0.015
                    print(f"   📈 Win rate {recent_wr:.1%} → риск повышен до 1.5%")
                else:
                    print(f"   ⚖️ Риск стандартный: 1.0%")
        self.db.set_config('adaptive', config)
        print("✅ Параметры обновлены")
        return config

    def _get_equity_series(self):
        df = self.db.get_equity_history()
        if len(df) > 0:
            return df['balance'].tolist()
        trades = self.db.get_closed_trades()
        if len(trades) == 0:
            return [INITIAL_BALANCE]
        equity = [INITIAL_BALANCE]
        for _, t in trades.iterrows():
            equity.append(equity[-1] + t['pnl'])
        return equity

class PaperTrading:
    def __init__(self, db):
        self.db = db

    def get_balance(self):
        trades = self.db.get_closed_trades()
        if len(trades) == 0:
            return INITIAL_BALANCE
        pnl = trades['pnl'].sum()
        return INITIAL_BALANCE + pnl

    def get_equity(self):
        trades = self.db.get_closed_trades()
        equity = [INITIAL_BALANCE]
        for _, t in trades.iterrows():
            equity.append(equity[-1] + t['pnl'])
        return equity

    def calc_position_size(self, entry, stop, risk_pct):
        balance = self.get_balance()
        risk_amount = balance * risk_pct
        risk_per_share = abs(entry - stop)
        if risk_per_share <= 0:
            return 0
        shares = int(risk_amount / risk_per_share)
        max_shares = int(balance * 0.25 / entry)
        return max(1, min(shares, max_shares))

    def open(self, signal, config):
        if not self.db.get_open_trades().empty and signal['ticker'] in self.db.get_open_trades()['ticker'].values:
            print(f"   ⏭️ {signal['ticker']} пропущен (уже есть открытая позиция)")
            return None

        if signal['strategy'] in config.get('disabled_strategies', []):
            print(f"   ⏭️ {signal['ticker']} пропущен (стратегия {signal['strategy']} отключена)")
            return None
        size = self.calc_position_size(
            signal['entry'], signal['stop'],
            config.get('risk_per_trade', 0.01)
        )
        if size <= 0:
            return None
        trade = {
            'ticker': signal['ticker'],
            'signal': signal['signal'],
            'strategy': signal['strategy'],
            'entry': signal['entry'],
            'stop': signal['stop'],
            'target': signal['target'],
            'size': size,
            'date': datetime.now().strftime('%Y-%m-%d'),
            'regime': signal.get('regime', 'unknown')
        }
        self.db.add_trade(trade)
        return trade

    def check_close(self, market_data):
        open_trades = self.db.get_open_trades()
        closed = []
        for _, trade in open_trades.iterrows():
            ticker = trade['ticker']
            if ticker not in market_data:
                continue
            d = market_data[ticker]
            high, low = d['high'], d['low']
            entry = trade['entry_price']
            stop = trade['stop_price']
            target = trade['target_price']
            size = trade['size']
            if trade['signal'] == 'BUY':
                if low <= stop:
                    pnl = (stop - entry) * size
                    pnl_pct = (stop - entry) / entry * 100
                    self.db.close_trade(trade['id'], stop, pnl, pnl_pct, 'STOP')
                    closed.append({'ticker': ticker, 'pnl': pnl, 'reason': 'STOP', 'price': stop})
                elif high >= target:
                    pnl = (target - entry) * size
                    pnl_pct = (target - entry) / entry * 100
                    self.db.close_trade(trade['id'], target, pnl, pnl_pct, 'TARGET')
                    closed.append({'ticker': ticker, 'pnl': pnl, 'reason': 'TARGET', 'price': target})
            else:
                if high >= stop:
                    pnl = (entry - stop) * size
                    pnl_pct = (entry - stop) / entry * 100
                    self.db.close_trade(trade['id'], stop, pnl, pnl_pct, 'STOP')
                    closed.append({'ticker': ticker, 'pnl': pnl, 'reason': 'STOP', 'price': stop})
                elif low <= target:
                    pnl = (entry - target) * size
                    pnl_pct = (entry - target) / entry * 100
                    self.db.close_trade(trade['id'], target, pnl, pnl_pct, 'TARGET')
                    closed.append({'ticker': ticker, 'pnl': pnl, 'reason': 'TARGET', 'price': target})
        return closed

class SignalScanner:
    def __init__(self, tickers):
        self.tickers = tickers

    def scan(self):
        print(f"\n🔍 Сканирую {len(self.tickers)} тикеров...")
        try:
            import yfinance as yf
        except ImportError:
            print("❌ Установите: pip install yfinance pandas numpy requests")
            sys.exit(1)
        end = datetime.now()
        start = end - timedelta(days=120)
        data = yf.download(self.tickers, start=start, end=end, progress=False, auto_adjust=True)
        signals = []
        for ticker in self.tickers:
            try:
                close = data['Close'][ticker].dropna()
                high = data['High'][ticker].dropna()
                low = data['Low'][ticker].dropna()
                volume = data['Volume'][ticker].dropna()
                if len(close) < 35:
                    continue
                ema9 = close.ewm(span=9).mean()
                ema21 = close.ewm(span=21).mean()
                sma20 = close.rolling(20).mean()
                std20 = close.rolling(20).std()
                bb_upper = sma20 + 2*std20
                delta = close.diff()
                gain = delta.where(delta > 0, 0).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rsi = 100 - (100 / (1 + gain/loss))
                tr1 = high - low
                tr2 = abs(high - close.shift())
                tr3 = abs(low - close.shift())
                tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                atr = tr.rolling(14).mean()
                vol_avg = volume.rolling(20).mean()
                c_now = close.iloc[-1]
                c_prev = close.iloc[-2]
                ema9_now, ema9_prev = ema9.iloc[-1], ema9.iloc[-2]
                ema21_now, ema21_prev = ema21.iloc[-1], ema21.iloc[-2]
                rsi_now, rsi_prev = rsi.iloc[-1], rsi.iloc[-2]
                bb_up_now, bb_up_prev = bb_upper.iloc[-1], bb_upper.iloc[-2]
                atr_now = atr.iloc[-1]
                if atr_now > c_now * 0.15:
                    continue

                vol_now = vol_avg.iloc[-1]
                high_20 = high.rolling(20).max().iloc[-2]
                low_20 = low.rolling(20).min().iloc[-2]
                if c_now > ema21_now and ema9_now > ema21_now:
                    regime = "uptrend"
                elif c_now < ema21_now and ema9_now < ema21_now:
                    regime = "downtrend"
                else:
                    regime = "sideways"
                sig = None
                if c_prev <= bb_up_prev and c_now > bb_up_now and volume.iloc[-1] > 1.5*vol_now:
                    sig = {'strategy': 'BB_Breakout', 'signal': 'BUY',
                           'stop': c_now - 1.5*atr_now, 'target': c_now + 3.0*atr_now}
                if sig:
                    sig.update({
                        'ticker': ticker, 'entry': round(c_now, 2),
                        'regime': regime, 'rsi': round(rsi_now, 1),
                        'atr': round(atr_now, 2), 'volume_ratio': round(volume.iloc[-1]/vol_now, 2)
                    })
                    signals.append(sig)
            except Exception as e:
                continue
        print(f"✅ Найдено {len(signals)} сигналов")
        return signals, data

class DiscordNotifier:
    def __init__(self, webhook_url):
        self.url = webhook_url

    def _calc_levels(self, entry, stop, target, signal_type, size):
        entry = float(entry)
        stop = float(stop)
        target = float(target)
        size = int(size)
        if signal_type == 'BUY':
            stop_pct = (stop - entry) / entry * 100
            target_pct = (target - entry) / entry * 100
        else:
            stop_pct = (entry - stop) / entry * 100
            target_pct = (entry - target) / entry * 100
        risk = abs(entry - stop) * size
        rr = abs(target - entry) / abs(entry - stop) if abs(entry - stop) > 0 else 0
        return {
            'stop_str': f"${stop:.2f} ({stop_pct:+.1f}%)",
            'target_str': f"${target:.2f} ({target_pct:+.1f}%)",
            'rr': f"1:{rr:.1f}",
            'risk': f"${risk:.0f}"
        }

    def _format_signal(self, s, lv):
        return (f"**{s['ticker']}**\n"
                f"💵 Вход по цене: ${float(s['entry']):.2f}\n"
                f"📉 Стоп на уровне: {lv['stop_str']}\n"
                f"📈 Тейк профит на уровне: {lv['target_str']}\n"
                f"⚖️ Соотношение риска к прибыли: {lv['rr']}\n"
                f"🧠 Стратегия: {s['strategy']}\n"
                f"🌊 Режим рынка: {s.get('regime', '-')}")

    def send_trade(self, trade, action="OPEN"):
        if not self.url:
            return
        color = 0x00ff00 if trade['signal'] == 'BUY' else 0xff0000
        emoji = "🟢" if trade['signal'] == 'BUY' else "🔴"
        lv = self._calc_levels(trade['entry'], trade['stop'], trade['target'], trade['signal'], trade['size'])
        desc = self._format_signal(trade, lv)
        desc += f"\n📊 Размер позиции: {trade['size']} шт (риск {lv['risk']})"
        payload = {
            "embeds": [{
                "title": "Новая сделка",
                "description": desc,
                "timestamp": datetime.utcnow().isoformat()
            }]
        }
        try:
            requests.post(self.url, json=payload, timeout=10)
        except Exception as e:
            print(f"   ⚠️ Discord error: {e}")

    def send_close(self, close_info):
        if not self.url:
            return
        pnl = close_info['pnl']
        color = 0x00ff00 if pnl > 0 else 0xff0000
        emoji = "✅" if pnl > 0 else "❌"
        payload = {
            "embeds": [{
                "title": f"Закрытие сделки: {close_info['ticker']} @ ${close_info['price']:.2f}",
                
                "fields": [
                    {"name": "💰 P&L", "value": f"${pnl:.2f}", "inline": True},
                    {"name": "🔒 Причина", "value": close_info['reason'], "inline": True}
                ],
                "timestamp": datetime.utcnow().isoformat()
            }]
        }
        try:
            requests.post(self.url, json=payload, timeout=10)
        except:
            pass

    def send_market_closed(self, signals, next_day):
        if not self.url:
            return
        blocks = []
        for s in signals[:10]:
            lv = self._calc_levels(s['entry'], s['stop'], s['target'], s['signal'], 0)
            blocks.append("• " + self._format_signal(s, lv))
        sig_list = "\n\n".join(blocks)
        payload = {
            "embeds": [{
                "title": f"⏸️ Рынок закрыт | Сигналы на {next_day}",
                "description": f"Найдено **{len(signals)}** сигналов. Вход отложен на следующий торговый день.\n\n{sig_list}",
                "color": 0xf1c40f,
                "timestamp": datetime.utcnow().isoformat()
            }]
        }
        try:
            requests.post(self.url, json=payload, timeout=10)
        except:
            pass

    def send_weekly_report(self, db):
        if not self.url:
            return
        trades = db.get_closed_trades()
        perf = pd.read_sql("SELECT * FROM performance", sqlite3.connect(db.path))
        equity_df = db.get_equity_history()
        if len(trades) == 0:
            return
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        week_trades = trades[trades['close_date'] >= week_ago]
        total_pnl = trades['pnl'].sum()
        week_pnl = week_trades['pnl'].sum() if len(week_trades) > 0 else 0
        balance = INITIAL_BALANCE + total_pnl
        wins = len(trades[trades['pnl'] > 0])
        total = len(trades)
        wr = wins/total if total > 0 else 0

        # Макс просадка за неделю
        week_equity = equity_df[equity_df['date'] >= week_ago]['balance'].tolist()
        max_dd_week = 0
        if len(week_equity) > 1:
            peak = week_equity[0]
            for bal in week_equity:
                if bal > peak:
                    peak = bal
                dd = (peak - bal) / peak * 100
                if dd > max_dd_week:
                    max_dd_week = dd

        # Средний R:R
        rr_values = []
        for _, t in trades.iterrows():
            entry = t['entry_price']
            stop = t['stop_price']
            target = t['target_price']
            if abs(entry - stop) > 0:
                rr = abs(target - entry) / abs(entry - stop)
                rr_values.append(rr)
        avg_rr = sum(rr_values) / len(rr_values) if rr_values else 0

        # Сравнение с S&P 500
        spy_return = None
        try:
            import yfinance as yf
            first_trade = trades['open_date'].min()
            spy_data = yf.download('SPY', start=first_trade, end=datetime.now().strftime('%Y-%m-%d'), progress=False)
            if len(spy_data) > 0:
                spy_close = spy_data['Close']
                if hasattr(spy_close, 'columns'):
                    spy_close = spy_close.iloc[:, 0]
                spy_close = spy_close.dropna()
                if len(spy_close) >= 2:
                    spy_start = float(spy_close.iloc[0])
                    spy_end = float(spy_close.iloc[-1])
                    spy_return = (spy_end - spy_start) / spy_start * 100
        except:
            pass

        fields = [
            {"name": "💰 Баланс", "value": f"${balance:,.2f}", "inline": True},
            {"name": "📈 Общий P&L", "value": f"${total_pnl:,.2f} ({total_pnl/INITIAL_BALANCE*100:.1f}%)", "inline": True},
            {"name": "📅 За неделю", "value": f"${week_pnl:,.2f}", "inline": True},
            {"name": "🎯 Win Rate", "value": f"{wr:.1%} ({wins}/{total})", "inline": True},
            {"name": "📉 Макс просадка (нед)", "value": f"{max_dd_week:.1f}%", "inline": True},
            {"name": "⚖️ Средний R:R", "value": f"1:{avg_rr:.1f}", "inline": True},
            {"name": "📊 Всего сделок", "value": str(total), "inline": True},
        ]

        if spy_return is not None:
            bot_return = total_pnl / INITIAL_BALANCE * 100
            diff = bot_return - spy_return
            emoji_spy = "🟢" if diff > 0 else "🔴"
            fields.append({"name": "📊 vs S&P 500 (SPY)", "value": f"Бот: {bot_return:+.1f}% | SPY: {spy_return:+.1f}% | {emoji_spy} {diff:+.1f}%", "inline": False})

        if len(perf) > 0:
            top = perf.nlargest(3, 'total_pnl')
            strat_text = "\n".join([f"{r['strategy']}: ${r['total_pnl']:.0f} (WR {r['win_rate']:.0%})"
                                   for _, r in top.iterrows()])
            fields.append({"name": "🏆 Топ стратегии", "value": strat_text, "inline": False})

        # График equity curve
        chart_path = '/tmp/equity_curve.png'
        has_chart = False
        if len(equity_df) > 1:
            try:
                import matplotlib
                matplotlib.use('Agg')
                import matplotlib.pyplot as plt
                fig, ax = plt.subplots(figsize=(10, 5))
                ax.plot(equity_df['date'], equity_df['balance'], label='Bot Equity', color='#00d26a', linewidth=2)
                ax.axhline(y=INITIAL_BALANCE, color='gray', linestyle='--', alpha=0.7, label='Start $10,000')
                ax.fill_between(equity_df['date'], equity_df['balance'], INITIAL_BALANCE,
                                where=(equity_df['balance'] >= INITIAL_BALANCE), alpha=0.2, color='green')
                ax.fill_between(equity_df['date'], equity_df['balance'], INITIAL_BALANCE,
                                where=(equity_df['balance'] < INITIAL_BALANCE), alpha=0.2, color='red')
                ax.set_title('Equity Curve', fontsize=14, fontweight='bold')
                ax.set_xlabel('Date')
                ax.set_ylabel('Balance ($)')
                ax.legend()
                ax.grid(True, alpha=0.3)
                plt.xticks(rotation=30)
                plt.tight_layout()
                plt.savefig(chart_path, dpi=150, bbox_inches='tight')
                plt.close()
                has_chart = True
            except Exception as e:
                print(f"   ⚠️ Ошибка графика: {e}")

        embed = {
            "title": f"📊 Еженедельный отчёт | {datetime.now().strftime('%d.%m.%Y')}",
            "color": 0x3498db,
            "fields": fields,
            "timestamp": datetime.utcnow().isoformat()
        }

        if has_chart:
            embed["image"] = {"url": "attachment://equity_curve.png"}
            try:
                with open(chart_path, 'rb') as f:
                    files = {'file': ('equity_curve.png', f)}
                    payload = {'payload_json': json.dumps({"embeds": [embed]})}
                    requests.post(self.url, data=payload, files=files, timeout=30)
                os.remove(chart_path)
                print("📨 Еженедельный отчёт с графиком отправлен в Discord")
            except Exception as e:
                print(f"   ⚠️ Discord upload error: {e}")
                requests.post(self.url, json={"embeds": [embed]}, timeout=10)
        else:
            try:
                requests.post(self.url, json={"embeds": [embed]}, timeout=10)
                print("📨 Еженедельный отчёт отправлен в Discord")
            except Exception as e:
                print(f"   ⚠️ Discord error: {e}")

def load_tickers():
    if USE_SHEET:
        try:
            df = pd.read_csv(SHEET_CSV_URL)
            tickers = df.iloc[:, 0].dropna().astype(str).str.strip().str.upper().tolist()
            print(f"✅ Загружено {len(tickers)} тикеров из Google Sheets")
            return tickers
        except Exception as e:
            print(f"⚠️ Ошибка загрузки таблицы: {e}")
    print(f"📋 Использую ручной список: {MANUAL_TICKERS}")
    return MANUAL_TICKERS

def main():
    now_msk = datetime.now(ZoneInfo("Europe/Moscow"))
    if not (now_msk.weekday() != 5 and now_msk.hour == 18 and now_msk.minute < 10):
        return
    print("="*60)
    print(f"🤖 TRADING BOT | {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    print("="*60)
    db = Database()
    learner = SelfLearning(db)
    trader = PaperTrading(db)
    discord = DiscordNotifier(DISCORD_WEBHOOK_URL)
    config = learner.adapt()
    tickers = load_tickers()
    scanner = SignalScanner(tickers)
    signals, market_data = scanner.scan()
    def _rr(s):
        risk = abs(s['entry'] - s['stop'])
        return abs(s['target'] - s['entry']) / risk if risk > 0 else -1
    signals = sorted(signals, key=_rr, reverse=True)
    print("\n🔒 Проверяю открытые сделки...")
    if len(market_data) > 0:
        current = {}
        for t in tickers:
            try:
                current[t] = {
                    'high': market_data['High'][t].iloc[-1],
                    'low': market_data['Low'][t].iloc[-1],
                    'close': market_data['Close'][t].iloc[-1]
                }
            except:
                pass
        closed = trader.check_close(current)
        for c in closed:
            print(f"   {'✅' if c['pnl'] > 0 else '❌'} {c['ticker']} закрыта @ ${c['price']} | P&L: ${c['pnl']:.2f} ({c['reason']})")
            discord.send_close(c)
    if not is_market_open():
        next_day = next_trading_day()
        print(f"\n⏸️ Рынок закрыт. Сигналы отложены на {next_day}.")
        if signals:
            if now_msk.weekday() != 6:
                discord.send_market_closed(signals, next_day)
            for sig in signals:
                print(f"   📋 {sig['ticker']} {sig['signal']} @ ${sig['entry']} — {sig['strategy']} (отложено)")
        opened = []
    else:
        print(f"\n🚀 Открываю новые сделки (баланс: ${trader.get_balance():,.2f})...")
        opened = []
        for sig in signals:
            trade = trader.open(sig, config)
            if trade:
                lv = discord._calc_levels(trade['entry'], trade['stop'], trade['target'], trade['signal'], trade['size'])
                print(f"   {'🟢' if trade['signal']=='BUY' else '🔴'} {trade['ticker']} {trade['signal']} | "
                      f"Вход: ${trade['entry']} | Стоп: {lv['stop_str']} | Цель: {lv['target_str']} | "
                      f"R:R {lv['rr']} | Размер: {trade['size']} шт")
                discord.send_trade(trade, "OPEN")
                opened.append(trade)
    balance = trader.get_balance()
    db.log_equity(balance)
    if datetime.now().weekday() == 6:
        print("\n📊 Генерирую еженедельный отчёт...")
        discord.send_weekly_report(db)
    print(f"\n💰 Текущий баланс: ${balance:,.2f}")
    print(f"📈 P&L: ${balance - INITIAL_BALANCE:,.2f} ({(balance/INITIAL_BALANCE-1)*100:.1f}%)")
    if now_msk.weekday() != 6:
        try:
            n_signals = len(signals)
            n_opened = len(opened)
            n_closed = len(closed) if "closed" in dir() else 0
        except Exception:
            n_signals = n_opened = n_closed = "?"
        try:
            requests.post(DISCORD_WEBHOOK_URL, json={
                "embeds": [{
                    "title": f"Прогон завершён | {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    "description": (
                        f"Проверено тикеров: {len(tickers)}\n"
                        f"Сигналов найдено: {n_signals}\n"
                        f"Сделок открыто: {n_opened}\n"
                        f"Сделок закрыто: {n_closed}\n"
                        f"Текущий баланс: ${balance:,.2f}"
                    )
                }]
            }, timeout=10)
        except Exception as e:
            print(f"   ⚠️ Не удалось отправить summary в Discord: {e}")

    print("="*60)

if __name__ == "__main__":
    main()
