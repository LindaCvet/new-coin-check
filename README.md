# TA Quick Checker (GitHub Actions)

Ieraksti monētu `COIN.txt` (formāts: `SYMBOL [EXCHANGE] [QUOTE]`, piem. `BTC COINBASE USD`) un GitHub Actions automātiski ģenerēs **OUTPUT.md** ar:
- trendu (↑/↓), RSI(1h), MACD(1h),
- **Verdiktu** (*Vērts pirkt*, *Vērts pirkt uz atvilkuma*, *Pagaidīt*, *Nav ieteicams*),
- Setup, Entry, **SL/TP1/TP2/TP3** (ATR bāzēti),
- riska birku (*zems/vidējs/augsts*) un **Score** (0–100).

## Lietošana
1. Rediģē `COIN.txt`, piem.:
