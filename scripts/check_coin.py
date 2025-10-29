import os, sys
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# CCXT + indikatoru bibliotēka
import ccxt
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange

ANTI_FOMO_PCT = 15.0  # ja 24h >= 15% -> tikai pullback
MAX_COINS = 15        # palielināts līdz 15, kā lūgts

# Preset 15 monētu saraksts (droši pieejamas CEX, īpaši COINBASE)
PRESET_15 = [
    "BTC", "ETH", "SOL", "AVAX", "LINK",
    "NEAR", "ADA", "MATIC", "ARB", "OP",
    "XRP", "DOGE", "DOT", "LTC", "ATOM"
]

# ----------------------------
# Palīgfunkcijas
# ----------------------------

def parse_coin_lines(path="COIN.txt"):
    """
    Nolasa līdz MAX_COINS rindām. Katra rinda: <SYMBOL> [EXCHANGE] [QUOTE]
    Noklusējumi: COINBASE/USD (jo Binance bieži bloķē GitHub Actions IP).

    Īpašais režīms:
    - Ja failā vienīgā (vai pirmā) rinda ir 'PRESET' vai 'DEFAULT15',
      tiks izmantots PRESET_15 saraksts uz COINBASE/USD (max 15).
    """
    if not os.path.exists(path):
        raise FileNotFoundError("COIN.txt nav atrodams.")

    with open(path, "r", encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]

    if not lines:
        raise ValueError("COIN.txt ir tukšs. Piemērs rindai: BTC COINBASE USD")

    # Preset 15
    first = lines[0].upper()
    if first in ("PRESET", "DEFAULT15"):
        parsed = []
        for sym in PRESET_15[:MAX_COINS]:
            parsed.append((f"{sym} COINBASE USD", sym, "COINBASE", "USD"))
        return parsed

    if len(lines) > MAX_COINS:
        lines = lines[:MAX_COINS]

    parsed = []
    for line in lines:
        parts = line.split()
        symbol = parts[0].upper()
        exchange = (parts[1].upper() if len(parts) > 1 else "COINBASE")
        quote = (parts[2].upper() if len(parts) > 2 else ("USD" if exchange == "COINBASE" else "USDT"))
        if exchange == "COINBASE" and quote == "USDT":
            quote = "USD"
        parsed.append((line, symbol, exchange, quote))
    return parsed

def make_exchange(name: str):
    """
    Noklusējums: COINBASE (GitHub Actions IP bieži bloķē Binance).
    Paplašināts biržu atbalsts fallbackam.
    """
    name = (name or "COINBASE").upper()
    if name == "BINANCE":
        ex = ccxt.binance()
    elif name == "OKX":
        ex = ccxt.okx()
    elif name == "BYBIT":
        ex = ccxt.bybit()
    elif name == "KRAKEN":
        ex = ccxt.kraken()
    elif name == "KUCOIN":
        ex = ccxt.kucoin()
    else:
        ex = ccxt.coinbase()
    ex.load_markets()
    return ex

def pick_pair(ex, base: str, quote: str):
    base, quote = base.upper(), quote.upper()
    # Coinbase → USD, nevis USDT
    if ex.id.lower() == "coinbase" and quote == "USDT":
        quote = "USD"
    sym = f"{base}/{quote}"
    if sym in ex.symbols:
        return sym
    # Fallback uz USD (daudzas biržas)
    if f"{base}/USD" in ex.symbols:
        return f"{base}/USD"
    raise ValueError(f"Pāris nav atrodams biržā: {base}/{quote}")

def normalize_tf(exchange_id: str, tf: str) -> str:
    # Coinbase neatbalsta '4h' → lietojam '6h'
    if exchange_id.lower() == "coinbase" and tf == "4h":
        return "6h"
    return tf

def fetch_df(ex, symbol: str, tf="1h", limit=400) -> pd.DataFrame:
    tf = normalize_tf(ex.id, tf)
    o = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df = pd.DataFrame(o, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df

def add_ind(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["rsi14"] = RSIIndicator(close=out["close"], window=14).rsi()
    macd = MACD(close=out["close"], window_slow=26, window_fast=12, window_sign=9)
    out["macd"] = macd.macd()
    out["macd_signal"] = macd.macd_signal()
    out["macd_hist"] = macd.macd_diff()
    atr = AverageTrueRange(high=out["high"], low=out["low"], close=out["close"], window=14)
    out["atr14"] = atr.average_true_range()
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

def compute_verdict(pct24h: float, trend_up: bool, macd1h: str, rsi1h: float, score_val: int) -> str:
    anti = (pct24h >= ANTI_FOMO_PCT)
    rsi = rsi1h or 50.0
    macd_bull = (macd1h == "bullish")
    if anti or rsi >= 72:
        if trend_up and macd_bull and score_val >= 60:
            return "Vērts pirkt uz atvilkuma"
        return "Pagaidīt"
    if trend_up and macd_bull and 55 <= rsi <= 70 and score_val >= 70:
        return "Vērts pirkt"
    if score_val >= 55 and (trend_up or macd_bull):
        return "Pagaidīt"
    return "Nav ieteicams"

# ----------------------------
# Fallback ķēde biržām
# ----------------------------

# Fallback biržas un noklusētie quote katrai (secība ir svarīga)
FALLBACK_EXCHANGES = [
    ("COINBASE", "USD"),   # drošākais GitHub Actions vidē
    ("KRAKEN",   "USD"),
    ("KUCOIN",   "USDT"),
    ("OKX",      "USDT"),
    ("BYBIT",    "USDT"),
    # ("BINANCE",  "USDT"),  # vari ieslēgt, bet GitHub IP bieži 451
]

# ----------------------------
# Vienas monētas analīze
# ----------------------------

def analyze_one(sym: str, ex_name: str, quote: str):
    """
    Izmanto lietotāja dotu biržu/quote, bet ja pāris/ohlcv nav pieejams,
    automātiski mēģina nākamos avotus no FALLBACK_EXCHANGES.
    """
    # 1) Meklēšanas ķēde: vispirms lietotāja izvēle, tad fallbacki
    chain = []
    if ex_name:
        first_quote = quote or ("USD" if ex_name.upper() == "COINBASE" else "USDT")
        chain.append((ex_name.upper(), first_quote.upper()))
    for e, q in FALLBACK_EXCHANGES:
        if not chain or (chain[0][0] != e or chain[0][1] != q):
            chain.append((e, q))

    pair = None
    chosen_ex = None
    chosen_ex_name = None
    chosen_quote = None
    last_error = None

    # 2) Ej cauri biržām, līdz atrodi derīgu pāri + OHLCV
    for ex_id, q in chain:
        try:
            ex = make_exchange(ex_id)
            # Coinbase → USD
            if ex.id.lower() == "coinbase" and q.upper() == "USDT":
                q = "USD"
            try:
                candidate = pick_pair(ex, sym, q)
            except Exception as e:
                last_error = e
                continue

            # Ātra OHLCV validācija (piem., 1h sveces >= 50)
            _df_test = fetch_df(ex, candidate, "1h", limit=100)
            if len(_df_test) < 50:
                last_error = ValueError(f"Par maz OHLCV 1h {ex_id}")
                continue

            # veiksmīgi
            pair = candidate
            chosen_ex = ex
            chosen_ex_name = ex_id
            chosen_quote = q
            break

        except Exception as e:
            last_error = e
            continue

    if pair is None or chosen_ex is None:
        raise RuntimeError(f"Neizdevās atrast datu avotu {sym} (pēdējā kļūda: {last_error})")

    ex = chosen_ex
    exchange = chosen_ex_name
    quote = chosen_quote

    # -------- Dati & indikatori --------
    df1h = add_ind(fetch_df(ex, pair, "1h"))
    df4h = add_ind(fetch_df(ex, pair, "4h"))   # Coinbase -> 6h automātiski
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
    s = score(trend_up, macd1h == "bullish", float(c1.rsi14 or 50), pct24h >= ANTI_FOMO_PCT)
    verdict = compute_verdict(pct24h, trend_up, macd1h, float(c1.rsi14 or 50), s)

    # Timing 15m
    c15 = df15.iloc[-1]
    atr = float(c15.atr14 or 0)
    price = float(c15.close or last)
    sl = price - 1.5 * atr
    tp1, tp2, tp3 = price + 1.0*atr, price + 2.0*atr, price + 3.0*atr

    # Setup + Entry cena
    anti = (pct24h >= ANTI_FOMO_PCT)
    if anti:
        setup = "Buy pullback"
        entry_text = "Pullback uz 20 EMA vai -0.5×ATR"
        entry_price = round(price - 0.5 * atr, 6)
    else:
        setup = "Speculative breakout"
        entry_text = "Breakout virs pēdējā 15m high ar apjomu"
        entry_price = round(price + 0.5 * atr, 6)

    # Riska mērījumi
    vol_ratio = (atr/price) if price else 0.0
    risk = "vidējs"
    if anti or vol_ratio > 0.02:
        risk = "augsts"
    if (not anti) and price and vol_ratio < 0.005 and trend_up:
        risk = "zems"

    # % no entry → SL/TP1
    downside_pct = ((entry_price - sl) / entry_price * 100) if entry_price else np.nan
    upside_pct   = ((tp1 - entry_price) / entry_price * 100) if entry_price else np.nan
    rr = (upside_pct / downside_pct) if (downside_pct and downside_pct > 0) else np.nan

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
        "entry_text": entry_text,
        "entry_price": entry_price,
        "SL": round(sl, 6),
        "TP1": round(tp1, 6),
        "TP2": round(tp2, 6),
        "TP3": round(tp3, 6),
        "risk": risk,
        "score": s,
        "verdict": verdict,
        "upside_pct": float(upside_pct) if not np.isnan(upside_pct) else np.nan,
        "downside_pct": float(downside_pct) if not np.isnan(downside_pct) else np.nan,
        "rr": float(rr) if rr == rr else np.nan,
    }

def rank_score(item: dict) -> float:
    """Kombinēts rādītājs TOP izvēlei: score + R:R bonuss - riska sods."""
    risk_pen = {"zems": 0.0, "vidējs": 5.0, "augsts": 10.0}.get(item["risk"], 5.0)
    rr_bonus = min(max(item.get("rr") or 0.0, 0.0), 3.0) * 10.0  # līdz +30
    return item["score"] + rr_bonus - risk_pen

# ----------------------------
# Output ģenerēšana
# ----------------------------

def write_output(results: list, coin_lines: list, out_path="OUTPUT.md"):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Atlasām TOP 3 pēc kombinētā vērtējuma
    ranked = sorted(results, key=lambda x: rank_score(x), reverse=True)[:3]

    # Markdown
    md = [f"# TA Quick Checker — rezultāts",
          "",
          f"**Ievade (COIN.txt):**",
          "```",
          *coin_lines,
          "```",
          f"**Laiks (UTC):** {now}",
          ""]

    # TOP 3
    md.append("## TOP 3 darījumi")
    if not ranked:
        md.append("_Nav veiksmīgu analīžu._")
    else:
        for i, r in enumerate(ranked, 1):
            up = f"{r['upside_pct']:.2f}%" if r['upside_pct']==r['upside_pct'] else "—"
            dn = f"{r['downside_pct']:.2f}%" if r['downside_pct']==r['downside_pct'] else "—"
            rr = f"{r['rr']:.2f}" if r['rr']==r['rr'] else "—"
            md += [
              f"**{i}. {r['pair']} @ {r['exchange']}** — {r['verdict']} ({r['risk']}, score {r['score']})",
              f"- Entry: `{r['entry_price']}` | SL: `{r['SL']}` | TP1: `{r['TP1']}`",
              f"- Upside (TP1): **{up}**, Downside (SL): **{dn}**, R:R **{rr}**",
              ""
            ]

    # Tabula ar visiem
    md.append("## Salīdzinājuma tabula (visi)")
    md.append("| Pāris | Verdikts | Risks | Score | Entry | SL | TP1 | Upside% | Downside% | R:R | RSI(1h) | MACD | 24h% | Cena |")
    md.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|")
    for r in results:
        up = f"{r['upside_pct']:.2f}%" if r['upside_pct']==r['upside_pct'] else "—"
        dn = f"{r['downside_pct']:.2f}%" if r['downside_pct']==r['downside_pct'] else "—"
        rr = f"{r['rr']:.2f}" if r['rr']==r['rr'] else "—"
        md.append(
            f"| {r['pair']} | {r['verdict']} | {r['risk']} | {r['score']} | "
            f"{r['entry_price']} | {r['SL']} | {r['TP1']} | {up} | {dn} | {rr} | "
            f"{r['rsi1h']:.0f} | {r['macd1h']} | {r['pct24h']:+.2f}% | {r['price']} |"
        )

    # Detalizēts bloks katram (pēc izvēles — var noderēt)
    md.append("\n---\n")
    for r in results:
        md += [
            f"## {r['pair']} @ {r['exchange']}",
            f"- Cena: **{r['price']}** | 24h: **{r['pct24h']:+.2f}%** | Vol: **{r['vol24h']}**",
            f"- Konteksts: Trend **{r['trend']}** | RSI(1h) **{r['rsi1h']:.0f}** | MACD(1h) **{r['macd1h']}**",
            "",
            "### Ieteikums",
            f"- **Verdikts:** {r['verdict']}",
            f"- **Setup:** {r['setup']}",
            f"- **Entry:** {r['entry_text']}  — **Cena:** `{r['entry_price']}`",
            f"- **SL:** `{r['SL']}`",
            f"- **TP1/TP2/TP3:** `{r['TP1']}` / `{r['TP2']}` / `{r['TP3']}`",
            f"- **Risks:** {r['risk']} | **Score:** {r['score']}/100",
            ""
        ]

    md += ["*Ne finanšu padoms.*", ""]

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

# ----------------------------
# Galvenā plūsma
# ----------------------------

def main():
    parsed = parse_coin_lines()
    results = []
    errors = []
    for line, sym, ex, quo in parsed:
        try:
            res = analyze_one(sym, ex, quo)
            res["_source_line"] = line
            results.append(res)
        except Exception as e:
            errors.append((line, str(e)))

    # ja bija kļūdas, pievieno tās results beigās kā rindas ar statusu
    for line, msg in errors:
        results.append({
            "pair": line,
            "exchange": "—",
            "quote": "—",
            "price": float("nan"),
            "pct24h": float("nan"),
            "vol24h": "—",
            "trend": "—",
            "rsi1h": float("nan"),
            "macd1h": "—",
            "setup": "—",
            "entry_text": "—",
            "entry_price": float("nan"),
            "SL": float("nan"),
            "TP1": float("nan"),
            "TP2": float("nan"),
            "TP3": float("nan"),
            "risk": "—",
            "score": 0,
            "verdict": f"Kļūda: {msg}",
            "upside_pct": float("nan"),
            "downside_pct": float("nan"),
            "rr": float("nan"),
        })

    with open("COIN.txt","r",encoding="utf-8") as f:
        coin_lines = [ln.rstrip("\n") for ln in f if ln.strip()]

    write_output(results, coin_lines)

if __name__ == "__main__":
    main()
