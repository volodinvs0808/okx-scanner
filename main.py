"""
OKX Smart Scanner v5.1
+ Avtozapis signalov dlya analiza V6
"""

import requests
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

# ── Nastroyki ─────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN   = "8279259149:AAFyqvMHBnpRtMyaEMmPVJdSmffWGymWHYw"
TELEGRAM_CHAT_ID = "6724936490"

DEPOSIT          = 509
LEVERAGE         = 5
VOLUME           = DEPOSIT * LEVERAGE
TP_PCT           = 1.5
FEE_PCT          = 0.1
SIGNAL_COOLDOWN  = 1800
SCAN_INTERVAL    = 60

MIN_RR           = 3.0
WICK_MIN         = 0.40
VOL_MULT         = 1.4
RSI_LOW          = 25
RSI_HIGH         = 72
SL_MIN_PCT       = 0.08
SL_MAX_PCT       = 0.35

KYIV_TZ = timezone(timedelta(hours=2))

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("scanner")

# ── Journal ───────────────────────────────────────────────────────────────────

signal_journal = []
signal_counter = 0

# ── Utils ─────────────────────────────────────────────────────────────────────

def now_kyiv():
    return datetime.now(KYIV_TZ)

def get_session():
    h = now_kyiv().hour
    if   2 <= h <  8: return "Asia"
    elif 8 <= h < 12: return "London"
    elif 14<= h < 19: return "NewYork"
    return "OffHours"

def ema(prices, period):
    if len(prices) < period:
        return None
    k   = 2 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return val

def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains) / period
    al = sum(losses) / period
    if al == 0:
        return 100.0
    return 100 - (100 / (1 + ag / al))

def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h  = float(candles[i][2])
        l  = float(candles[i][3])
        pc = float(candles[i-1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period

# ── API ───────────────────────────────────────────────────────────────────────

def fetch_symbols():
    url = "https://www.okx.com/api/v5/public/instruments?instType=SWAP&ctType=linear"
    r   = requests.get(url, timeout=10).json()
    return [i["instId"] for i in r.get("data", []) if i["instId"].endswith("-USDT-SWAP")]

def fetch_candles(symbol, bar="5m", limit=100):
    url  = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={bar}&limit={limit}"
    r    = requests.get(url, timeout=10).json()
    data = r.get("data", [])
    return list(reversed(data)) if data else []

# ── Analyze ───────────────────────────────────────────────────────────────────

def analyze(symbol):
    candles = fetch_candles(symbol, bar="5m", limit=100)
    if len(candles) < 55:
        return None

    o = [float(c[1]) for c in candles]
    h = [float(c[2]) for c in candles]
    l = [float(c[3]) for c in candles]
    c = [float(c[4]) for c in candles]
    v = [float(c[5]) for c in candles]

    price = c[-1]

    e9  = ema(c, 9)
    e21 = ema(c, 21)
    e50 = ema(c, 50)
    if not e9 or not e21 or not e50:
        return None
    trend_ok = e9 > e21 > e50 * 0.998

    rsi_v = rsi(c, 14)
    if rsi_v is None:
        return None
    rsi_ok = RSI_LOW <= rsi_v <= RSI_HIGH

    rsi_prev   = rsi(c[:-1], 14)
    rsi_bounce = rsi_prev is not None and rsi_prev < 50 and rsi_v > rsi_prev

    local_low = min(l[-28:-4])
    s_o, s_h  = o[-3], h[-3]
    s_l, s_c  = l[-3], c[-3]
    s_v       = v[-3]

    swept     = s_l < local_low * 0.9998
    reclaimed = s_c >= local_low

    rng = s_h - s_l
    if rng < 1e-9:
        return None
    wick   = min(s_o, s_c) - s_l
    wick_r = wick / rng
    wick_ok = wick_r >= WICK_MIN

    avg_v  = sum(v[-30:-4]) / 26
    vol_ok = avg_v > 0 and s_v > avg_v * VOL_MULT

    conf_ok = c[-2] > o[-2] and c[-2] > local_low
    curr_ok = c[-1] > o[-1] and c[-1] >= o[-2] * 0.9995

    atr_v  = atr(candles, 14)
    atr_ok = atr_v is not None and (atr_v / price * 100) >= 0.03

    body_ok = abs(s_c - s_o) / rng <= 0.55

    all_ok = (trend_ok and rsi_ok and rsi_bounce and
              swept and reclaimed and wick_ok and vol_ok and
              conf_ok and curr_ok and atr_ok and body_ok)

    if not all_ok:
        return None

    entry    = price
    sl_price = s_l * 0.9997
    sl_pct   = abs(entry - sl_price) / entry * 100

    if not (SL_MIN_PCT <= sl_pct <= SL_MAX_PCT):
        return None

    rr_val = TP_PCT / sl_pct
    if rr_val < MIN_RR:
        return None

    fee    = VOLUME * FEE_PCT / 100
    profit = round(VOLUME * TP_PCT / 100 - fee, 2)
    loss   = round(VOLUME * sl_pct / 100 + fee, 2)

    return {
        "symbol":  symbol,
        "entry":   round(entry, 8),
        "tp":      round(entry * (1 + TP_PCT / 100), 8),
        "sl":      round(sl_price, 8),
        "sl_pct":  round(sl_pct, 3),
        "rr":      round(rr_val, 1),
        "rsi":     round(rsi_v, 1),
        "vol":     round(s_v / avg_v, 2),
        "wick":    round(wick_r * 100, 1),
        "profit":  profit,
        "loss":    loss,
        "level":   round(local_low, 8),
        "session": get_session(),
        "e9":      round(e9, 6),
        "e21":     round(e21, 6),
    }

# ── Telegram ──────────────────────────────────────────────────────────────────

def send(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       text,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        log.warning(f"Telegram: {e}")

def format_signal(s, num):
    stars = "*" * min(int(s["rr"]), 5)
    stars = stars + "o" * (5 - len(stars))
    return (
        f"<b>SIGNAL #{num}  {s['symbol']}</b>  [{s['session']}]\n\n"
        f"Vhod:  <b>${s['entry']}</b>\n"
        f"TP:    <b>${s['tp']}</b>  +{TP_PCT}%\n"
        f"SL:    <b>${s['sl']}</b>  -{s['sl_pct']}%\n\n"
        f"RR:    1:{s['rr']}  {stars}\n"
        f"RSI:   {s['rsi']}\n"
        f"Vol:   x{s['vol']}\n"
        f"Fitil: {s['wick']}%\n\n"
        f"Pribyl (TP): <b>+${s['profit']}</b>\n"
        f"Ubytok (SL): <b>-${s['loss']}</b>\n"
        f"Obem: ${VOLUME}  x{LEVERAGE}\n\n"
        f"{now_kyiv().strftime('%d.%m  %H:%M:%S')}"
    )

def format_journal_record(s, num):
    """Запись в журнал — одна строка для отправки статистики"""
    return (
        f"#{num}|{now_kyiv().strftime('%d.%m %H:%M')}|"
        f"{s['symbol']}|{s['session']}|"
        f"vhod:{s['entry']}|tp:{s['tp']}|sl:{s['sl']}|"
        f"sl%:{s['sl_pct']}|rr:1:{s['rr']}|"
        f"rsi:{s['rsi']}|vol:x{s['vol']}|fitil:{s['wick']}%|"
        f"REZULTAT:?"
    )

def send_stats():
    """Отправляет статистику за день"""
    if not signal_journal:
        send("Za segodnya signalov ne bylo.")
        return

    today = now_kyiv().strftime("%d.%m")
    text  = f"<b>Statistika za {today}</b>\n"
    text += f"Vsego signalov: {len(signal_journal)}\n\n"
    text += "<b>Zhurnal (zameni ? na TP ili SL):</b>\n\n"

    for record in signal_journal[-20:]:
        text += f"<code>{record}</code>\n"

    text += (
        "\n<i>Peredaj etot spisok Claude dlya analiza V6</i>"
    )
    send(text)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global signal_counter

    send(
        "<b>Smart Scanner v5.1 zapushen!</b>\n\n"
        "Novoe: avtozapis zhurnala sdelok\n"
        "Kazhdyy den v 23:55 poluchish statistiku\n\n"
        f"TP: {TP_PCT}% | RR min: 1:{MIN_RR} | x{LEVERAGE}\n"
        f"Depozit: ${DEPOSIT} | Obem: ${VOLUME}\n\n"
        "Rezhim: 24/7"
    )

    alerted      = {}
    last_stat_day = -1

    while True:
        try:
            now_ts  = time.time()
            now_dt  = now_kyiv()
            found   = 0

            # Отправляем статистику в 23:55 каждый день
            if now_dt.hour == 23 and now_dt.minute == 55 and now_dt.day != last_stat_day:
                send_stats()
                signal_journal.clear()
                last_stat_day = now_dt.day

            symbols = fetch_symbols()
            log.info(f"Scan {len(symbols)} par | {get_session()}")

            for symbol in symbols:
                try:
                    if now_ts - alerted.get(symbol, 0) < SIGNAL_COOLDOWN:
                        continue

                    sig = analyze(symbol)

                    if sig:
                        signal_counter += 1
                        num = signal_counter

                        send(format_signal(sig, num))

                        record = format_journal_record(sig, num)
                        signal_journal.append(record)

                        alerted[symbol] = now_ts
                        found += 1
                        log.info(
                            f"SIGNAL #{num} {symbol} "
                            f"RR:1:{sig['rr']} "
                            f"Vol:x{sig['vol']}"
                        )

                    time.sleep(0.15)

                except Exception as e:
                    log.debug(f"skip {symbol}: {e}")

            log.info(f"Signaly: {found} | pauza {SCAN_INTERVAL}s")

            cutoff  = now_ts - SIGNAL_COOLDOWN * 3
            alerted = {k: v for k, v in alerted.items() if v > cutoff}

        except Exception as e:
            log.error(f"Error: {e}")
            time.sleep(30)

        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
