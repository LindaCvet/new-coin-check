import os, sys, subprocess
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange
import ccxt
from datetime import datetime, timezone

ANTI_FOMO_PCT = 15.0  # ja 24h >= 15% → tikai pullback

def parse_coin_file(path="COIN.txt"):
    if not os.path.exists(path):
        raise FileNotFoundError("COIN.txt nav atrodams.")
    with open(path, "r", encoding="utf-8") as f:
        line = f.read().strip()
    if not line:
        raise ValueError("COIN.txt ir tukšs. Ieraksti, piem.: BTC BINANCE USDT")
    parts = line.split()
    symbol = parts[0].upper()
    exchange = (parts[1].upper() if len(parts) > 1 else "BINANCE")
    quote = (parts[2].upper() if len(parts) > 2 else "USDT")
    return symbol, exchange, quote

# ===== REPLACE make_exchange + analyze START =====
def make_exchange(name: str):
    """
    Izveido CCXT biržas instanci.
    Noklusējums: COINBASE (jo Binance bieži blokē GitHub IP ar 451).
    """
    name = (name or "COINBASE").upper()
    if name == "BINANCE":
        ex = ccxt.binance()
    elif name == "BYBIT":
        ex = ccxt.bybit()
    elif name == "OKX":
        ex = ccxt.okx()
    else:
        ex = ccxt.coinbase()
    ex.load_markets()
    return ex

def analyze(base: str, exchange: str, quote: str):
    """
    Galvenā analīze ar drošu fall-back:
    - Ja pieprasīta BINANCE, bet tiek saņemts 451/ExchangeNotAvailable,
      automātiski pārslēdzamies uz COINBASE un QUOTE -> USD.
    """
    try:
        ex = make_exchange(exchange)
    except Exception:
        # ļoti reti, bet ja pat load_markets neizdodas
        exchange = "COINBASE"
        quote = "USD"
        ex = make_exchange(exchange)

    # papildus: ja izvēlēta COINBASE, pielāgo quote uz USD (tur parasti nav USDT)
    if exchange.upper() == "COINBASE" and quote.upper() == "USDT":
        quote = "USD"

    try:
        pair = pick_pair(ex, base, quote)
    except Exception as e:
        # Ja BINANCE pair nav (vai 451 ietekmē symbol sarakstu),
        # mēģinam automātiski uz COINBASE/USD
        if exchange.upper() == "BINANCE":
            exchange = "COINBASE"
            quote = "USD"
            ex = make_exchange(exchange)
            pair = pick_pair(ex, base, quote)
        else:
            raise e

    # ===== tālāk viss kā iepriekš =====
    df1h = add_ind(fetch_df(ex, pair, "1h"))
    df4h = add_ind(fetch_df(ex, pair, "4h"))
    df1d = add_ind(fetch_df(ex, pair, "1d"))
    df15 = add_ind(fetch_df(ex, pair, "15m"))

    t = ex.fetch_ticker(pair)
    last = float(t.get("last", t.get("close", 0)) or 0)
    open_ = float(t.get("open", last) or 0)
    pct24h = ((last - open_) / open_ * 100) if open_ else 0.0
    vol24h = t.get("baseVolume") or t.get("quoteVolume") or "—"

    c1, c4, cd = df1h.iloc[-1], df4h.iloc[-1], df1d.iloc[-1]
    trend_up = (c1.close > c1.ema50) and (c4.close > c4.ema50) and (cd.close > cd.ema50)
    macd1h = macd_state(c1.macd, c1.macd_signal)
    s = score(trend_up, macd1h=="bullish", float(c1.rsi14 or 50), pct24h >= ANTI_FOMO_PCT)

    c15 = df15.iloc[-1]
    atr = float(c15.atr14 or 0)
    price = float(c15.close or last)
    sl = price - 1.5 * atr
    tp1, tp2, tp3 = price + 1.0*atr, price + 2.0*atr, price + 3.0*atr

    anti = (pct24h >= ANTI_FOMO_PCT)
    setup = "Buy pullback" if anti else "Speculative breakout"
    entry = "Pullback uz 20 EMA / virs 15m mini-range" if anti else "Breakout virs pēdējā 15m high ar apjomu"
    risk = "vidējs"
    if anti or (atr/price if price else 0) > 0.02:
        risk = "augsts"
    if (not anti) and price and atr/price < 0.005 and trend_up:
        risk = "zems"

    return {
        "pair": pair,
        "exchange": exchange,
        "quote": quote,
        "price": round(price, 8),
        "pct24h": pct24h,
        "vol24h": vol24h,
        "trend": "↑" if trend_up else "↓",
        "rsi1h": float(c1.rsi14 or np.nan),
        "macd1h": macd1h,
        "setup": setup,
        "entry": entry,
        "SL": round(sl, 6),
        "TP1": round(tp1, 6),
        "TP2": round(tp2, 6),
        "TP3": round(tp3, 6),
        "risk": risk,
        "score": s,
    }
# ===== REPLACE make_exchange + analyze END =====


def pick_pair(ex, base: str, quote: str):
    sym = f"{base}/{quote}"
    if sym in ex.symbols:
        return sym
    if f"{base}/USD" in ex.symbols:
        return f"{base}/USD"
    raise ValueError(f"Pāris nav atrodams biržā: {base}/{quote}")
def normalize_tf(exchange_id: str, tf: str) -> str:
    """
    Coinbase neatbalsta '4h'. Aizvietojam ar '6h'.
    Pārējie TF paliek kā ir.
    """
    if exchange_id.lower() == "coinbase" and tf == "4h":
        return "6h"
    return tf

def fetch_df(ex, symbol: str, tf="1h", limit=400):
    tf = normalize_tf(ex.id, tf)
    o = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df = pd.DataFrame(o, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def add_ind(df: pd.DataFrame):
    out = df.copy()
    # RSI
    out["rsi14"] = RSIIndicator(close=out["close"], window=14).rsi()
    # MACD
    macd = MACD(close=out["close"], window_slow=26, window_fast=12, window_sign=9)
    out["macd"] = macd.macd()
    out["macd_signal"] = macd.macd_signal()
    out["macd_hist"] = macd.macd_diff()
    # ATR
    atr = AverageTrueRange(high=out["high"], low=out["low"], close=out["close"], window=14)
    out["atr14"] = atr.average_true_range()
    # EMA
    out["ema20"] = EMAIndicator(close=out["close"], window=20).ema_indicator()
    out["ema50"] = EMAIndicator(close=out["close"], window=50).ema_indicator()
    out["ema200"] = EMAIndicator(close=out["close"], window=200).ema_indicator()
    return out


def macd_state(macd, sig):
    if pd.isna(macd) or pd.isna(sig): return "flat"
    if macd > sig: return "bullish"
    if macd < sig: return "bearish"
    return "flat"

def score(trend_up: bool, macd_bull: bool, rsi: float, anti_fomo: bool) -> int:
    s = 50
    if trend_up: s += 15
    if macd_bull: s += 10
    if 55 <= (rsi or 0) <= 70: s += 10
    if anti_fomo: s -= 5
    return max(0, min(s, 100))

def analyze(base: str, exchange: str, quote: str):
    ex = make_exchange(exchange)
    pair = pick_pair(ex, base, quote)

    # Dati
    df1h = add_ind(fetch_df(ex, pair, "1h"))
    df4h = add_ind(fetch_df(ex, pair, "4h"))
    df1d = add_ind(fetch_df(ex, pair, "1d"))
    df15 = add_ind(fetch_df(ex, pair, "15m"))

    # 24h statistika
    t = ex.fetch_ticker(pair)
    last = float(t.get("last", t.get("close", 0)) or 0)
    open_ = float(t.get("open", last) or 0)
    pct24h = ((last - open_) / open_ * 100) if open_ else 0.0
    vol24h = t.get("baseVolume") or t.get("quoteVolume") or "—"

    # Konteksts
    c1, c4, cd = df1h.iloc[-1], df4h.iloc[-1], df1d.iloc[-1]
    trend_up = (c1.close > c1.ema50) and (c4.close > c4.ema50) and (cd.close > cd.ema50)
    macd1h = macd_state(c1.macd, c1.macd_signal)
    s = score(trend_up, macd1h=="bullish", float(c1.rsi14 or 50), pct24h >= ANTI_FOMO_PCT)

    # Timing no 15m
    c15 = df15.iloc[-1]
    atr = float(c15.atr14 or 0)
    price = float(c15.close or last)
    sl = price - 1.5 * atr
    tp1, tp2, tp3 = price + 1.0*atr, price + 2.0*atr, price + 3.0*atr

    anti = (pct24h >= ANTI_FOMO_PCT)
    setup = "Buy pullback" if anti else "Speculative breakout"
    entry = "Pullback uz 20 EMA / virs 15m mini-range" if anti else "Breakout virs pēdējā 15m high ar apjomu"
    risk = "vidējs"
    if anti or (atr/price if price else 0) > 0.02:
        risk = "augsts"
    if (not anti) and price and atr/price < 0.005 and trend_up:
        risk = "zems"

    return {
        "pair": pair,
        "exchange": exchange,
        "quote": quote,
        "price": round(price, 8),
        "pct24h": pct24h,
        "vol24h": vol24h,
        "trend": "↑" if trend_up else "↓",
        "rsi1h": float(c1.rsi14 or np.nan),
        "macd1h": macd1h,
        "setup": setup,
        "entry": entry,
        "SL": round(sl, 6),
        "TP1": round(tp1, 6),
        "TP2": round(tp2, 6),
        "TP3": round(tp3, 6),
        "risk": risk,
        "score": s,
    }

def write_output(result: dict, coin_line: str, out_path="OUTPUT.md"):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    md = f"""# TA Quick Checker — rezultāts

**Ievade (COIN.txt):** `{coin_line}`  
**Laiks (UTC):** {now}

## {result['pair']} @ {result['exchange']}
- Cena: **{result['price']}** | 24h: **{result['pct24h']:+.2f}%** | Vol: **{result['vol24h']}**
- Konteksts: Trend **{result['trend']}** | RSI(1h) **{result['rsi1h']:.0f}** | MACD(1h) **{result['macd1h']}**

### Ieteikums
- **Setup:** {result['setup']}
- **Entry:** {result['entry']}
- **SL:** `{result['SL']}`
- **TP1/TP2/TP3:** `{result['TP1']}` / `{result['TP2']}` / `{result['TP3']}`
- **Risks:** {result['risk']} | **Score:** {result['score']}/100

*Ne finanšu padoms.*
"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)

def main():
    # Nolasām ievadi
    base, exchange, quote = parse_coin_file()
    with open("COIN.txt","r",encoding="utf-8") as f:
        coin_line = f.read().strip()

    res = analyze(base, exchange, quote)
    write_output(res, coin_line)

if __name__ == "__main__":
    main()
