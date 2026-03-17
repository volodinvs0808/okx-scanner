import requests
import time
from datetime import datetime

TELEGRAM_TOKEN = "8279259149:AAFyqvMHBnpRtMyaEMmPVJdSmffWGymWHYw"
TELEGRAM_CHAT_ID = "6724936490"
CHECK_INTERVAL = 60

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"})

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
    return list(reversed(data))

def ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return val

def analyze(symbol):
    candles = get_candles(symbol, 60)
    if len(candles) < 55:
        return None

    closes = [float(c[4]) for c in candles]
    opens = [float(c[1]) for c in candles]
    vols = [float(c[5]) for c in candles]

    ema21 = ema(closes, 21)
    ema50 = ema(closes, 50)
    price = closes[-1]
    if not ema21 or not ema50:
        return None
    trend_ok = price > ema21 > ema50

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

    green_ok = all(closes[-i] > opens[-i] for i in range(1, 6))
    gap_ok = all(
        abs(opens[-i] - closes[-i-1]) / closes[-i-1] * 100 < 0.05
        for i in range(1, 5)
    )

    avg_vol = sum(vols[-25:-5]) / 20
    last_vol = sum(vols[-5:]) / 5
    vol_ok = avg_vol > 0 and last_vol > avg_vol * 1.2

    if trend_ok and rsi_ok and green_ok and gap_ok and vol_ok:
        return {
            "symbol": symbol,
            "price": price,
            "rsi": round(rsi, 1),
            "vol_ratio": round(last_vol / avg_vol, 2)
        }
    return None

def format_signal(s):
    volume = 5000
    fee = volume * 0.001
    tp_pct = 0.5
    sl_pct = 0.25
    profit = round(volume * tp_pct / 100 - fee, 2)
    loss = round(volume * sl_pct / 100 + fee, 2)
    tp_price = round(s['price'] * (1 + tp_pct / 100), 6)
    sl_price = round(s['price'] * (1 - sl_pct / 100), 6)
    return (
        f"<b>SIGNAL {s['symbol']}</b>\n\n"
        f"Vhod: ${s['price']}\n"
        f"TP: ${tp_price}\n"
        f"SL: ${sl_price}\n\n"
        f"RSI: {s['rsi']} | Vol: x{s['vol_ratio']}\n\n"
        f"Pribyl: +${profit}\n"
        f"Ubytok: -${loss}\n"
        f"Plecho x10\n\n"
        f"{datetime.now().strftime('%H:%M:%S')}"
    )

def main():
    send_telegram("Bot zapushen! Skaniruyu USDT-SWAP futures OKX...")
    alerted = {}
    while True:
        try:
            symbols = get_symbols()
            now = datetime.now().strftime("%H:%M")
            print(f"[{now}] Skaniruyu {len(symbols)} par...")
            for symbol in symbols:
                try:
                    result = analyze(symbol)
                    if result and alerted.get(symbol) != now:
                        send_telegram(format_signal(result))
                        alerted[symbol] = now
                        print(f"SIGNAL: {symbol}")
                    time.sleep(0.15)
                except Exception as e:
                    print(f"Oshibka {symbol}: {e}")
            if len(alerted) > 500:
                alerted.clear()
        except Exception as e:
            print(f"Oshibka: {e}")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
