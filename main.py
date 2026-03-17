import requests
import time
from datetime import datetime

TELEGRAM_TOKEN = "8279259149:AAFyqvMHBnpRtMyaEMmPVJdSmffWGymWHYw"
TELEGRAM_CHAT_ID = "6724936490"
CHECK_INTERVAL = 60

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    })

def get_symbols():
    url = "https://www.okx.com/api/v5/public/instruments?instType=SWAP&ctType=linear"
    r = requests.get(url, timeout=10).json()
    return [i["instId"] for i in r.get("data", []) if i["instId"].endswith("-USDT-SWAP")]

def get_candles(symbol, limit=60):
    url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar=1m&limit={limit}"
    r = requests.get(url, timeout=10).json()
    data = r.get("data", [])
    if not data:
        return []
    # Разворачиваем: OKX даёт новые первыми
    return list(reversed(data))

def ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return val

def avg_volume(candles, period=20):
    vols = [float(c[5]) for c in candles[-period:]]
    return sum(vols) / len(vols) if vols else 0

def analyze(symbol):
    candles = get_candles(symbol, 60)
    if len(candles) < 55:
        return None

    closes = [float(c[4]) for c in candles]
    opens  = [float(c[1]) for c in candles]
    highs  = [float(c[2]) for c in candles]
    lows   = [float(c[3]) for c in candles]
    vols   = [float(c[5]) for c in candles]

    # --- EMA 21 / 50 ---
    ema21 = ema(closes, 21)
    ema50 = ema(closes, 50)
    price = closes[-1]
    if not ema21 or not ema50:
        return None
    trend_ok = price > ema21 > ema50

    # --- RSI 14 ---
    gains, losses = [], []
    for i in range(-15, -1):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / 14
    avg_loss = sum(losses) / 14
    if avg_loss == 0:
        rsi = 100
    else:
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
    rsi_ok = 50 <= rsi <= 72

    # --- 5 зелёных свечей без гэпов ---
    green_ok = all(closes[-i] > opens[-i] for i in range(1, 6))
    gap_ok = all(
        abs(opens[-i] - closes[-i-1]) / closes[-i-1] * 100 < 0.05
        for i in range(1, 5)
    )

    # --- Объём выше среднего ---
    avg_vol = avg_volume(candles[:-5], 20)
    last_vols = vols[-5:]
    vol_ok = avg_vol > 0 and (sum(last_vols) / 5) > avg_vol * 1.2

    if trend_ok and rsi_ok and green_ok and gap_ok and vol_ok:
        return {
            "symbol": symbol,
            "price": price,
            "ema21": round(ema21, 6),
            "ema50": round(ema50, 6),
            "rsi": round(rsi, 1),
            "vol_ratio": round((sum(last_vols)/5) / avg_vol, 2)
        }
    return None

def format_signal(s):
    deposit = 500
    leverage = 10
    volume = deposit * leverage
    fee = volume * 0.001
    tp_pct = 0.5
    sl_pct = 0.25
    profit = round(volume * tp_pct / 100 - fee, 2)
    loss   = round(volume * sl_pct / 100 + fee, 2)
    tp_price = round(s['price'] * (1 + tp_pct/100), 6)
    sl_price = round(s['price'] * (1 - sl_pct/100), 6)

    return (
        f"🟢 <b>СИГНАЛ — {s['symbol']}</b>\n\n"
        f"📍 Вход: <b>${s['price']}</b>\n"
        f"✅ TP: ${tp_price} (+{tp_pct}%)\n"
        f"❌ SL: ${sl_price} (-{sl_pct}%)\n\n"
        f"📊 RSI: {s['rsi']} | Объём: x{s['vol_ratio']}\n"
        f"📈 EMA21: {s['ema21']} | EMA50: {s['ema50']}\n\n"
        f"💵 Прибыль (TP): +${profit}\n"
        f"💸 Убыток (SL): -${loss}\n"
        f"⚡️ Плечо x10 | Объём $5000\n\n"
        f"⏰ {datetime.now().strftime('%H:%M:%S')}"
    )

def main():
    send_telegram("🤖 <b>Impulse Filter Bot запущен!</b>\n\nСтратегия: 5 зелёных + EMA21/50 + RSI + Объём\nПары: все USDT-SWAP фьючерсы OKX")
    alerted = {}

    while True:
        try:
            symbols = get_symbols()
            now = datetime.now().strftime("%H:%M")
            print(f"[{now}] Сканирую {len(symbols)} пар...")

            for symbol in symbols:
                try:
                    result = analyze(symbol)
                    if result:
                        last = alerted.get(symbol, "")
                        if last != now:
                            msg = format_signal(result)
                            send_telegram(msg)
                            alerted[symbol] = now
                            print(f"СИГНАЛ: {symbol} | RSI:{result['rsi']} | Vol:x{result['vol_ratio']}")
                    time.sleep(0.15)
                except Exception as e:
                    print(f"Ошибка {symbol}: {e}")

            if len(alerted) > 500:
                alerted.clear()

        except Exception as e:
            print(f"Общая ошибка: {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
```

Сигнал будет выглядеть так в Telegram:
```
🟢 СИГНАЛ — BTC-USDT-SWAP

📍 Вход: $84,250
✅ TP: $84,671 (+0.5%)
❌ SL: $84,039 (-0.25%)

📊 RSI: 61.3 | Объём: x1.8
📈 EMA21: 84,100 | EMA50: 83,750

💵 Прибыль (TP): +$15.00
💸 Убыток (SL): -$7.50
⚡️ Плечо x10 | Объём $5000
