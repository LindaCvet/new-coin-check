# TA Quick Checker (GitHub Actions)
Ieraksti monētu `COIN.txt` (formāts: `SYMBOL [EXCHANGE] [QUOTE]`, piem. `BTC BINANCE USDT`), un GitHub Actions automātiski izveidos **OUTPUT.md** ar:
- trendu, RSI/MACD,
- ieteikumu (*Buy pullback* / *Speculative breakout* / gaidīt),
- Entry/SL/TP (ATR-bāzēti),
- riska birku un score.

## Lietošana
1) Rediģē `COIN.txt` (piem., `AVAX BINANCE USDT`) un **commit**.
2) Pēc ~1–2 minūtēm repo parādīsies/atjaunosies `OUTPUT.md`.
3) Var palaist arī manuāli: **Actions → TA Quick Checker → Run workflow**.
