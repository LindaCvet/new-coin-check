# Vienkāršs “izrāviena pārbaudītājs”
# Noklusējumi: BINANCE, USDT, TF = 1h/4h/1d + timing 15m
# Palaišana:
#   python quick_checker.py BTC
#   python quick_checker.py BTC ETH SOL --exchange BINANCE --quote USDT
#   python quick_checker.py --from-file coins.txt

import argparse, sys
import ccxt, pandas as pd, numpy as np
import pandas_ta as ta

ANTI_FOMO_PCT = 15.0   # ja 24h >= 15% -> tikai pullback
DEFAULT_EXCHANGE = "BINANCE"
DEFAULT_QUOTE = "USDT"

def make_exchange(name: str):
    name = (name or DEFAULT_EXCHANGE).upper()
    if name == "COINBASE":
        ex = ccxt.coinbase()
    else:
        ex = ccxt.binance()
    ex.load_markets()
    return ex

def pick_symbol(ex, base: str, quote: str):
    base, quote = base.upper(), quote.upper()
    sym = f"{base}/{quote}"
    if sym in ex.symbols:
        return sym
    # fallback: USD
    if f"{base}/USD" in ex.symbols:
        return f"{base}/USD"
    raise ValueError(f"Pāris nav atrodams: {base}/{quote} (mēģini @COINBASE vai citu quote)")

def fetch_df(ex, symbol: str, tf="1h", limit=400):
    o = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df = pd.DataFrame(o, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df

def add_ind(df: pd.DataFrame):
    out = df.copy()
    out.set_index("ts", inplace=True)
    out["rsi14"] = ta.rsi(out["close"], length=14)
    m = ta.macd(out["close"], fast=12, slow=26, signal=9)
    out["macd"], out["macd_signal"], out["macd_hist"] = m.iloc[:,0], m.iloc[:,1], m.iloc[:,2]
    out["atr14"] = ta.atr(out["high"], out["low"], out["close"], length=14)
    out["ema20"] = ta.ema(out["close"], length=20)
    out["ema50"] = ta.ema(out["close"], length=50)
    out["ema200"] = ta.ema(out["close"], length=200)
    out.reset_index(inplace=True)
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

def analyze_one(ex, base: str, quote: str):
    symbol = pick_symbol(ex, base, quote)
    # dati
    df1h = add_ind(fetch_df(ex, symbol, "1h"))
    df4h = add_ind(fetch_df(ex, symbol, "4h"))
    df1d = add_ind(fetch_df(ex, symbol, "1d"))
    df15 = add_ind(fetch_df(ex, symbol, "15m"))

    # 24h stats
    t = ex.fetch_ticker(symbol)
    last = float(t.get("last", t.get("close", 0)) or 0)
    open_ = float(t.get("open", last) or 0)
    pct24h = ((last - open_) / open_ * 100) if open_ else 0.0
    vol24h = t.get("baseVolume") or t.get("quoteVolume") or "—"

    # konteksts
    c1, c4, cd = df1h.iloc[-1], df4h.iloc[-1], df1d.iloc[-1]
    trend_up = (c1.close > c1.ema50) and (c4.close > c4.ema50) and (cd.close > cd.ema50)
    macd1h = macd_state(c1.macd, c1.macd_signal)
    s = score(trend_up, macd1h=="bullish", float(c1.rsi14 or 50), pct24h>=ANTI_FOMO_PCT)

    # timing no 15m
    c15 = df15.iloc[-1]
    atr = float(c15.atr14 or 0)
    price = float(c15.close or last)
    sl = price - 1.5*atr
    tp1, tp2, tp3 = price + 1.0*atr, price + 2.0*atr, price + 3.0*atr

    anti = (pct24h >= ANTI_FOMO_PCT)
    setup = "Buy pullback" if anti else "Speculative breakout"
    entry = "Pullback uz 20 EMA / virs 15m mini-range" if anti else "Breakout virs pēdējā 15m high ar apjomu"

    # risks (vienk.)
    risk = "vidējs"
    if anti or atr/price > 0.02:  # ļoti svārstīgs
        risk = "augsts"
    if (not anti) and atr/price < 0.005 and trend_up:
        risk = "zems"

    out = {
        "symbol": symbol,
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
        "price": round(price, 8),
    }
    return out

def print_report(r):
    print(f"\n=== {r['symbol']} ===")
    print(f"Cena: {r['price']} | 24h: {r['pct24h']:+.2f}% | Vol: {r['vol24h']}")
    print(f"Konteksts: Trend {r['trend']} | RSI(1h) {r['rsi1h']:.0f} | MACD(1h) {r['macd1h']}")
    print(f"Setup: {r['setup']}")
    print(f"Entry: {r['entry']}")
    print(f"SL: {r['SL']}")
    print(f"TP1/TP2/TP3: {r['TP1']} / {r['TP2']} / {r['TP3']}")
    print(f"Risks: {r['risk']} | Score: {r['score']}/100")
    print("(Ne finanšu padoms)")

def main():
    ap = argparse.ArgumentParser(description="Vienkāršs izrāviena pārbaudītājs")
    ap.add_argument("symbols", nargs="*", help="Simboli (piem., BTC ETH AVAX)")
    ap.add_argument("--exchange", default=DEFAULT_EXCHANGE, help="BINANCE vai COINBASE (nokl. BINANCE)")
    ap.add_argument("--quote", default=DEFAULT_QUOTE, help="USDT vai USD (nokl. USDT)")
    ap.add_argument("--from-file", help="Ceļš uz txt failu ar simboliem pa atsevišķām rindām")
    args = ap.parse_args()

    # avots: CLI vai fails
    symbols = list(args.symbols)
    if args.from_file:
        with open(args.from_file, "r", encoding="utf-8") as f:
            symbols += [ln.strip() for ln in f if ln.strip()]
    if not symbols:
        print("Nav norādīts neviens simbols. Piem.: python quick_checker.py BTC AVAX")
        sys.exit(1)

    ex = make_exchange(args.exchange)
    for s in symbols:
        try:
            r = analyze_one(ex, s, args.quote)
            print_report(r)
        except Exception as e:
            print(f"\n=== {s}/{args.quote} ===")
            print("Kļūda:", e)

if __name__ == "__main__":
    main()
