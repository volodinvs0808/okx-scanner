"""
OKX Smart Scanner v5.2
+ Inline knopki rezultata sdelki
+ Avtoanaliz tochki vhoda
+ Zhurnal s rezultatami dlya V6
"""

import requests
import time
import logging
import json
import threading
from datetime import datetime, timezone, timedelta

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("scanner")

# ── Zhurnal sdelok ────────────────────────────────────────────────────────────

trades    = {}
counter   = 0
offset    = 0

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

# ── API OKX ───────────────────────────────────────────────────────────────────

def fetch_symbols():
    url = "https://www.okx.com/api/v5/public/instruments?instType=SWAP&ctType=linear"
    r   = requests.get(url, timeout=10).json()
    return [i["instId"] for i in r.get("data", []) if i["instId"].endswith("-USDT-SWAP")]

def fetch_candles(symbol, bar="5m", limit=100):
    url  = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={bar}&limit={limit}"
    r    = requests.get(url, timeout=10).json()
    data = r.get("data", [])
    return list(reversed(data)) if data else []

def get_current_price(symbol):
    url = f"https://www.okx.com/api/v5/market/ticker?instId={symbol}"
    r   = requests.get(url, timeout=10).json()
    d   = r.get("data", [])
    return float(d[0]["last"]) if d else None

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
    e9    = ema(c, 9)
    e21   = ema(c, 21)
    e50   = ema(c, 50)
    if not e9 or not e21 or not e50:
        return None

    trend_ok = e9 > e21 > e50 * 0.998
    rsi_v    = rsi(c, 14)
    if rsi_v is None:
        return None
    rsi_ok     = RSI_LOW <= rsi_v <= RSI_HIGH
    rsi_prev   = rsi(c[:-1], 14)
    rsi_bounce = rsi_prev is not None and rsi_prev < 50 and rsi_v > rsi_prev

    local_low = min(l[-28:-4])
    s_o, s_h  = o[-3], h[-3]
    s_l, s_c  = l[-3], c[-3]
    s_v       = v[-3]

    swept     = s_l < local_low * 0.9998
    reclaimed = s_c >= local_low
    rng       = s_h - s_l
    if rng < 1e-9:
        return None
    wick      = min(s_o, s_c) - s_l
    wick_r    = wick / rng
    wick_ok   = wick_r >= WICK_MIN

    avg_v  = sum(v[-30:-4]) / 26
    vol_ok = avg_v > 0 and s_v > avg_v * VOL_MULT
    conf_ok = c[-2] > o[-2] and c[-2] > local_low
    curr_ok = c[-1] > o[-1] and c[-1] >= o[-2] * 0.9995
    atr_v   = atr(candles, 14)
    atr_ok  = atr_v is not None and (atr_v / price * 100) >= 0.03
    body_ok = abs(s_c - s_o) / rng <= 0.55

    if not (trend_ok and rsi_ok and rsi_bounce and swept and reclaimed
            and wick_ok and vol_ok and conf_ok and curr_ok and atr_ok and body_ok):
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
    }

# ── Telegram ──────────────────────────────────────────────────────────────────

def send(text, reply_markup=None):
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    try:
        r = requests.post(url, data=data, timeout=10)
        return r.json().get("result", {}).get("message_id")
    except Exception as e:
        log.warning(f"Telegram send: {e}")
        return None

def edit_message(message_id, text):
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText"
    data = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "message_id": message_id,
        "text":       text,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        log.warning(f"Telegram edit: {e}")

def answer_callback(callback_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    try:
        requests.post(url, data={
            "callback_query_id": callback_id,
            "text": text
        }, timeout=10)
    except Exception as e:
        log.warning(f"Callback: {e}")

def format_signal(s, num):
    stars = "*" * min(int(s["rr"]), 5)
    stars = stars + "o" * (5 - len(stars))
    return (
        f"<b>SIGNAL #{num}  {s['symbol']}</b>  [{s['session']}]\n\n"
        f"Vhod:  <b>${s['entry']}</b>\n"
        f"TP:    <b>${s['tp']}</b>  +{TP_PCT}%\n"
        f"SL:    <b>${s['sl']}</b>  -{s['sl_pct']}%\n\n"
        f"RR:    1:{s['rr']}  {stars}\n"
        f"RSI:   {s['rsi']} | Vol: x{s['vol']} | Fitil: {s['wick']}%\n\n"
        f"Pribyl (TP): <b>+${s['profit']}</b>\n"
        f"Ubytok (SL): <b>-${s['loss']}</b>\n"
        f"Obem: ${VOLUME}  x{LEVERAGE}\n\n"
        f"{now_kyiv().strftime('%d.%m  %H:%M:%S')}\n\n"
        f"<i>Nazhmi rezultat posle zakrytiya sdelki:</i>"
    )

def make_buttons(num):
    return {
        "inline_keyboard": [[
            {"text": "TP",        "callback_data": f"tp_{num}"},
            {"text": "SL",        "callback_data": f"sl_{num}"},
            {"text": "Eshche v pozitsii", "callback_data": f"hold_{num}"}
        ]]
    }

def format_result(s, num, result, current_price=None):
    base = (
        f"<b>SIGNAL #{num}  {s['symbol']}</b>  [{s['session']}]\n\n"
        f"Vhod:  ${s['entry']}\n"
        f"TP:    ${s['tp']}  +{TP_PCT}%\n"
        f"SL:    ${s['sl']}  -{s['sl_pct']}%\n"
        f"RR:    1:{s['rr']}\n\n"
    )

    if result == "tp":
        base += f"REZULTAT: TP +${s['profit']}\n"
        base += "Otlichno! Sdelka zakryta v plus."
    elif result == "sl":
        base += f"REZULTAT: SL -${s['loss']}\n"
        base += "Stoploss. Sleduyushchiy signal budet luchshe."
    elif result == "hold":
        if current_price:
            pnl_pct = (current_price - s['entry']) / s['entry'] * 100
            pnl_usd = round(VOLUME * pnl_pct / 100, 2)
            sign    = "+" if pnl_usd >= 0 else ""
            base   += f"POZITSIYA OTKRYTA\n"
            base   += f"Tekushchaya tsena: ${current_price}\n"
            base   += f"Tekushchiy PnL: {sign}${pnl_usd} ({sign}{pnl_pct:.2f}%)\n"
            base   += f"Do TP: {round(s['tp'] - current_price, 6)}\n"
            base   += f"Do SL: {round(current_price - s['sl'], 6)}"
        else:
            base += "POZITSIYA OTKRYTA"

    return base

def send_daily_stats():
    total   = len(trades)
    tp_list = [t for t in trades.values() if t.get("result") == "tp"]
    sl_list = [t for t in trades.values() if t.get("result") == "sl"]
    open_l  = [t for t in trades.values() if t.get("result") == "hold" or not t.get("result")]

    tp_count = len(tp_list)
    sl_count = len(sl_list)
    wr       = round(tp_count / max(tp_count + sl_count, 1) * 100)

    fee      = VOLUME * FEE_PCT / 100
    profit   = VOLUME * TP_PCT / 100 - fee
    loss_avg = VOLUME * 0.22 / 100 + fee
    pnl      = round(tp_count * profit - sl_count * loss_avg, 2)

    today = now_kyiv().strftime("%d.%m.%Y")
    text  = (
        f"<b>Statistika za {today}</b>\n\n"
        f"Vsego signalov: {total}\n"
        f"TP: {tp_count}  |  SL: {sl_count}  |  Otkryty: {len(open_l)}\n"
        f"Vinreyt: {wr}%\n"
        f"PnL za den: {'+'if pnl>=0 else ''}{pnl}\n\n"
        f"<b>Zhurnal (dlya analiza V6):</b>\n\n"
    )

    for num, t in list(trades.items())[-20:]:
        s   = t["signal"]
        res = t.get("result", "?").upper()
        text += (
            f"<code>#{num}|{t['time']}|{s['symbol']}|{s['session']}|"
            f"rr:1:{s['rr']}|rsi:{s['rsi']}|vol:x{s['vol']}|"
            f"fitil:{s['wick']}%|{res}</code>\n"
        )

    text += "\n<i>Peredaj etot spisok Claude dlya V6</i>"
    send(text)

# ── Polling callbacks ─────────────────────────────────────────────────────────

def poll_callbacks():
    global offset
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            r   = requests.get(url, params={
                "offset":  offset,
                "timeout": 30,
                "allowed_updates": ["callback_query"]
            }, timeout=35).json()

            for update in r.get("result", []):
                offset = update["update_id"] + 1
                cb     = update.get("callback_query")
                if not cb:
                    continue

                data   = cb.get("data", "")
                cb_id  = cb["id"]
                msg_id = cb["message"]["message_id"]

                parts  = data.split("_")
                if len(parts) != 2:
                    continue

                action = parts[0]
                try:
                    num = int(parts[1])
                except ValueError:
                    continue

                if num not in trades:
                    answer_callback(cb_id, "Signal ne najden")
                    continue

                trade = trades[num]
                sig   = trade["signal"]

                if action == "tp":
                    trades[num]["result"] = "tp"
                    answer_callback(cb_id, f"+${sig['profit']} TP!")
                    edit_message(msg_id, format_result(sig, num, "tp"))

                elif action == "sl":
                    trades[num]["result"] = "sl"
                    answer_callback(cb_id, f"-${sig['loss']} SL")
                    edit_message(msg_id, format_result(sig, num, "sl"))

                elif action == "hold":
                    trades[num]["result"] = "hold"
                    price = get_current_price(sig["symbol"])
                    answer_callback(cb_id, "Proveryayu tsenu...")
                    edit_message(msg_id, format_result(sig, num, "hold", price))

        except Exception as e:
            log.debug(f"Polling: {e}")
        time.sleep(1)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global counter

    threading.Thread(target=poll_callbacks, daemon=True).start()

    send(
        "<b>Smart Scanner v5.2 zapushen!</b>\n\n"
        "Novoe: knopki rezultata sdelki\n"
        "- Nazhmi TP / SL / Eshche v pozitsii\n"
        "- Bот zapishet rezultat i pokazhet PnL\n"
        "- Kazhdyy den v 23:55 statistika + zhurnal\n\n"
        f"TP: {TP_PCT}% | RR min: 1:{MIN_RR} | x{LEVERAGE}\n"
        f"Depozit: ${DEPOSIT} | Obem: ${VOLUME}\n\n"
        "Rezhim: 24/7"
    )

    alerted       = {}
    last_stat_day = -1

    while True:
        try:
            now_ts = time.time()
            now_dt = now_kyiv()
            found  = 0

            if now_dt.hour == 23 and now_dt.minute == 55 and now_dt.day != last_stat_day:
                send_daily_stats()
                last_stat_day = now_dt.day

            symbols = fetch_symbols()
            log.info(f"Scan {len(symbols)} par | {get_session()}")

            for symbol in symbols:
                try:
                    if now_ts - alerted.get(symbol, 0) < SIGNAL_COOLDOWN:
                        continue

                    sig = analyze(symbol)

                    if sig:
                        counter += 1
                        num      = counter

                        msg_id = send(
                            format_signal(sig, num),
                            reply_markup=make_buttons(num)
                        )

                        trades[num] = {
                            "signal": sig,
                            "msg_id": msg_id,
                            "time":   now_kyiv().strftime("%d.%m %H:%M"),
                            "result": None
                        }

                        alerted[symbol] = now_ts
                        found += 1
                        log.info(f"SIGNAL #{num} {symbol} RR:1:{sig['rr']}")

                    time.sleep(0.15)

                except Exception as e:
                    log.debug(f"skip {symbol}: {e}")

            log.info(f"Signaly: {found} | pauza {SCAN_INTERVAL}s")

            cutoff  = now_ts - SIGNAL_COOLDOWN * 3
            alerted = {k: v for k, v in alerted.items() if v > cutoff}

            if len(trades) > 200:
                old_keys = sorted(trades.keys())[:-100]
                for k in old_keys:
                    del trades[k]

        except Exception as e:
            log.error(f"Error: {e}")
            time.sleep(30)

        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
