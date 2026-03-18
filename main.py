import requests
import time
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = "8279259149:AAFyqvMHBnpRtMyaEMmPVJdSmffWGymWHYw"
TELEGRAM_CHAT_ID = "6724936490"

DEPOSIT = 509
LEVERAGE = 5
VOLUME = DEPOSIT * LEVERAGE
TP_PCT = 1.2
FEE_PCT = 0.1

KYIV_TZ = timezone(timedelta(hours=2))
TRADE_HOURS = [(9, 11), (16, 18)]
CHECK_INTERVAL = 300


def now_kyiv():
    return datetime.now(KYIV_TZ)


def is_trade_time():
    hour = now_kyiv().hour
    for start, end in TRADE_HOURS:
        if start <= hour < end:
            return True
    return False


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")


def get_symbols():
    url = "https://www.okx.com/api/v5/public/instruments?instType=SWAP&ctType=linear"
    r = requests.get(url, timeout=10).json()
    return [i["instId"] for i in r.get("data", []) if i["instId"].endswith("-USDT-SWAP")]


def get_candles(symbol, bar="5m", limit=60):
    url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={bar}&limit={limit}"
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


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    ag = sum(gains) / period
    al = sum(losses) / period
    if al == 0:
        return 100
    return 100 - (100 / (1 + ag / al))


def calc_atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i][2])
        l = float(candles[i][3])
        pc = float(candles[i-1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


def check_15m_trend(symbol):
    candles = get_candles(symbol, bar="15m", limit=60)
    if len(candles) < 55:
        return False
    closes = [float(c[4]) for c in candles]
    e21 = ema(closes, 21)
    e50 = ema(closes, 50)
    if not e21 or not e50:
        return False
    return e21 > e50


def find_local_low(lows, lookback=20):
    recent = lows[-lookback:-3]
    if not recent:
        return None
    return min(recent)


def analyze(symbol):
    candles = get_candles(symbol, bar="5m", limit=60)
    if len(candles) < 40:
        return None

    closes = [float(c[4]) for c in candles]
    opens  = [float(c[1]) for c in candles]
    highs  = [float(c[2]) for c in candles]
    lows   = [float(c[3]) for c in candles]
    vols   = [float(c[5]) for c in candles]

    sig_open  = opens[-3]
    sig_close = closes[-3]
    sig_high  = highs[-3]
    sig_low   = lows[-3]
    sig_vol   = vols[-3]

    conf_open  = opens[-2]
    conf_close = closes[-2]

    curr_open  = opens[-1]
    curr_close = closes[-1]

    local_low = find_local_low(lows, lookback=20)
    if not local_low:
        return None

    grabbed = sig_low < local_low * 0.9995
    reclaimed = sig_close >= local_low

    candle_range = sig_high - sig_low
    if candle_range < 0.000001:
        return None
    lower_wick = min(sig_open, sig_close) - sig_low
    wick_ratio = lower_wick / candle_range
    strong_wick = wick_ratio >= 0.55

    avg_vol = sum(vols[-25:-4]) / 21 if len(vols) >= 25 else sum(vols[:-4]) / max(len(vols) - 4, 1)
    vol_spike = avg_vol > 0 and sig_vol > avg_vol * 2.0

    conf_green = conf_close > conf_open
    conf_above = conf_close > local_low
    curr_holding = curr_close >= conf_open * 0.9995

    rsi_val = rsi(closes, 14)
    rsi_ok = rsi_val is not None and 35 <= rsi_val <= 65

    trend_up = check_15m_trend(symbol)

    atr_val = calc_atr(candles, 14)
    price = curr_close
    atr_ok = atr_val is not None and (atr_val / price * 100) > 0.05

    if not (grabbed and reclaimed and strong_wick and vol_spike
            and conf_green and conf_above and curr_holding
            and rsi_ok and trend_up and atr_ok):
        return None

    entry = curr_close
    sl_price = sig_low * 0.9995
    sl_pct = abs(entry - sl_price) / entry * 100

    if sl_pct > 0.5 or sl_pct < 0.05:
        return None

    rr = TP_PCT / sl_pct
    if rr < 3.0:
        return None

    fee = VOLUME * FEE_PCT / 100
    profit = round(VOLUME * TP_PCT / 100 - fee, 2)
    loss   = round(VOLUME * sl_pct / 100 + fee, 2)
    tp_price = round(entry * (1 + TP_PCT / 100), 8)
    sl_price_f = round(sl_price, 8)

    return {
        "symbol":    symbol,
        "entry":     round(entry, 8),
        "tp":        tp_price,
        "sl":        sl_price_f,
        "sl_pct":    round(sl_pct, 3),
        "rr":        round(rr, 1),
        "rsi":       round(rsi_val, 1),
        "vol_ratio": round(sig_vol / avg_vol, 2),
        "wick_pct":  round(wick_ratio * 100, 1),
        "profit":    profit,
        "loss":      loss,
        "local_low": round(local_low, 8),
    }


def format_signal(s):
    return (
        f"<b>LIQUIDITY GRAB - {s['symbol']}</b>\n\n"
        f"Krupnyak sbil stopy i nabral pozitsiyu!\n\n"
        f"Vhod:  ${s['entry']}\n"
        f"TP:    ${s['tp']}  (+{TP_PCT}%)\n"
        f"SL:    ${s['sl']}  (-{s['sl_pct']}%)\n\n"
        f"Risk/Reward: 1:{s['rr']}\n"
        f"RSI: {s['rsi']} | Volume: x{s['vol_ratio']}\n"
        f"Fitil vniz: {s['wick_pct']}%\n"
        f"Uroven: ${s['local_low']}\n\n"
        f"Pribyl (TP): +${s['profit']}\n"
        f"Ubytok (SL): -${s['loss']}\n"
        f"Obem: ${VOLUME} | Plecho x{LEVERAGE}\n\n"
        f"{now_kyiv().strftime('%H:%M:%S')}"
    )


def main():
    send_telegram(
        "<b>Liquidity Grab Bot zapushen!</b>\n\n"
        "Strategiya: Ohota za krupnym igrokom\n"
        "Risk/Reward: 1:6+\n"
        "Torgovlya: 09:00-11:00 i 16:00-18:00 Kyiv\n"
        f"Plecho: x{LEVERAGE} | Depozit: ${DEPOSIT}\n\n"
        "Zhdu signalov..."
    )

    alerted = {}

    while True:
        try:
            now_str = now_kyiv().strftime("%H:%M")

            if is_trade_time():
                symbols = get_symbols()
                print(f"[{now_str}] AKTIV - {len(symbols)} par...")

                for symbol in symbols:
                    try:
                        result = analyze(symbol)
                        if result and alerted.get(symbol) != now_str:
                            send_telegram(format_signal(result))
                            alerted[symbol] = now_str
                            print(f"SIGNAL: {symbol} RR:1:{result['rr']} Vol:x{result['vol_ratio']}")
                        time.sleep(0.2)
                    except Exception as e:
                        print(f"Err {symbol}: {e}")

                if len(alerted) > 500:
                    alerted.clear()

            else:
                next_w = "09:00" if now_kyiv().hour < 9 or now_kyiv().hour >= 18 else "16:00"
                print(f"[{now_str}] Vne vremeni. Sleduyushchee okno: {next_w}")

        except Exception as e:
            print(f"Global error: {e}")
            time.sleep(30)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
