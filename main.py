"""
OKX Scanner v6.0 - Simple & Working
Два типа сигналов: Trend + Pullback
"""

import requests
import time
import logging
import json
import threading
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN   = "8279259149:AAFyqvMHBnpRtMyaEMmPVJdSmffWGymWHYw"
TELEGRAM_CHAT_ID = "6724936490"

DEPOSIT  = 509
LEVERAGE = 5
VOLUME   = DEPOSIT * LEVERAGE
TP_PCT   = 1.0
FEE_PCT  = 0.1
COOLDOWN = 1800
INTERVAL = 60

KYIV_TZ = timezone(timedelta(hours=2))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("bot")

trades  = {}
counter = 0
offset  = 0

def now_kyiv():
    return datetime.now(KYIV_TZ)

def get_session():
    h = now_kyiv().hour
    if 2  <= h < 8:  return "Asia"
    if 8  <= h < 12: return "London"
    if 14 <= h < 19: return "NewYork"
    return "OffHours"

def ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    v = sum(prices[:period]) / period
    for p in prices[period:]:
        v = p * k + v * (1 - k)
    return v

def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    g, l = [], []
    for i in range(-period, 0):
        d = closes[i] - closes[i-1]
        g.append(max(d, 0))
        l.append(max(-d, 0))
    ag = sum(g) / period
    al = sum(l) / period
    if al == 0: return 100.0
    return 100 - (100 / (1 + ag / al))

def fetch_symbols():
    r = requests.get(
        "https://www.okx.com/api/v5/public/instruments?instType=SWAP&ctType=linear",
        timeout=10
    ).json()
    return [i["instId"] for i in r.get("data", []) if i["instId"].endswith("-USDT-SWAP")]

def fetch_candles(symbol, bar="5m", limit=80):
    r = requests.get(
        f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={bar}&limit={limit}",
        timeout=10
    ).json()
    data = r.get("data", [])
    return list(reversed(data)) if data else []

def get_price(symbol):
    r = requests.get(
        f"https://www.okx.com/api/v5/market/ticker?instId={symbol}",
        timeout=10
    ).json()
    d = r.get("data", [])
    return float(d[0]["last"]) if d else None

def analyze(symbol):
    candles = fetch_candles(symbol)
    if len(candles) < 40:
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
    if not all([e9, e21, e50]):
        return None

    rsi_v    = rsi(c, 14)
    rsi_prev = rsi(c[:-1], 14)
    if rsi_v is None or rsi_prev is None:
        return None

    avg_v = sum(v[-25:-2]) / 23

    # ── ТИП 1: Trend Momentum ──────────────────────────────────────────
    # EMA выстроены, RSI растёт, объём есть
    type1 = (
        e9 > e21 > e50 and
        40 <= rsi_v <= 68 and
        rsi_v > rsi_prev and
        c[-1] > o[-1] and
        c[-2] > o[-2] and
        v[-1] > avg_v * 1.1 and
        price > e21
    )

    # ── ТИП 2: EMA Bounce ──────────────────────────────────────────────
    # Цена отскочила от EMA21, RSI выходит из перепроданности
    near_ema = abs(price - e21) / e21 * 100 < 0.3
    type2 = (
        e21 > e50 and
        near_ema and
        35 <= rsi_v <= 60 and
        rsi_v > rsi_prev and
        c[-1] > o[-1] and
        v[-1] > avg_v * 1.1
    )

    if not type1 and not type2:
        return None

    sig_type = "TREND" if type1 else "EMA BOUNCE"

    sl_price = min(l[-6:]) * 0.9998
    sl_pct   = abs(price - sl_price) / price * 100

    if sl_pct < 0.08 or sl_pct > 0.5:
        return None

    rr = TP_PCT / sl_pct
    if rr < 2.0:
        return None

    fee    = VOLUME * FEE_PCT / 100
    profit = round(VOLUME * TP_PCT / 100 - fee, 2)
    loss   = round(VOLUME * sl_pct / 100 + fee, 2)

    return {
        "symbol":  symbol,
        "type":    sig_type,
        "entry":   round(price, 8),
        "tp":      round(price * (1 + TP_PCT / 100), 8),
        "sl":      round(sl_price, 8),
        "sl_pct":  round(sl_pct, 3),
        "rr":      round(rr, 1),
        "rsi":     round(rsi_v, 1),
        "vol":     round(v[-1] / avg_v, 2),
        "profit":  profit,
        "loss":    loss,
        "session": get_session(),
        "e9":      round(e9, 6),
        "e21":     round(e21, 6),
    }

def send(text, markup=None):
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    if markup:
        data["reply_markup"] = json.dumps(markup)
    try:
        r = requests.post(url, data=data, timeout=10)
        return r.json().get("result", {}).get("message_id")
    except Exception as e:
        log.warning(f"send: {e}")

def edit(msg_id, text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText",
            data={"chat_id": TELEGRAM_CHAT_ID, "message_id": msg_id,
                  "text": text, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        log.warning(f"edit: {e}")

def answer_cb(cb_id, text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
            data={"callback_query_id": cb_id, "text": text},
            timeout=10
        )
    except: pass

def fmt_signal(s, num):
    icon = "🔥" if s["type"] == "TREND" else "📍"
    stars = "*" * min(int(s["rr"]), 5) + "o" * (5 - min(int(s["rr"]), 5))
    return (
        f"{icon} <b>#{num} {s['symbol']}</b> [{s['session']}]\n"
        f"<i>{s['type']}</i>\n\n"
        f"Vhod:  <b>${s['entry']}</b>\n"
        f"TP:    <b>${s['tp']}</b>  +{TP_PCT}%\n"
        f"SL:    <b>${s['sl']}</b>  -{s['sl_pct']}%\n\n"
        f"RR: 1:{s['rr']}  {stars}\n"
        f"RSI: {s['rsi']} | Vol: x{s['vol']}\n"
        f"EMA9: {s['e9']} | EMA21: {s['e21']}\n\n"
        f"Pribyl: <b>+${s['profit']}</b>\n"
        f"Ubytok: <b>-${s['loss']}</b>\n"
        f"${VOLUME} x{LEVERAGE}\n\n"
        f"{now_kyiv().strftime('%d.%m %H:%M:%S')}"
    )

def fmt_result(s, num, result, price=None):
    base = f"<b>#{num} {s['symbol']}</b> [{s['type']}]\n\n"
    base += f"Vhod: ${s['entry']} | TP: ${s['tp']} | SL: ${s['sl']}\n\n"
    if result == "tp":
        base += f"REZULTAT: TP +${s['profit']}"
    elif result == "sl":
        base += f"REZULTAT: SL -${s['loss']}"
    elif result == "hold" and price:
        pnl_pct = (price - s['entry']) / s['entry'] * 100
        pnl_usd = round(VOLUME * pnl_pct / 100, 2)
        sign = "+" if pnl_usd >= 0 else ""
        base += (
            f"POZITSIYA OTKRYTA\n"
            f"Tsena: ${price}\n"
            f"PnL: {sign}${pnl_usd} ({sign}{pnl_pct:.2f}%)\n"
            f"Do TP: {round(s['tp'] - price, 6)}"
        )
    return base

def buttons(num):
    return {"inline_keyboard": [[
        {"text": "TP",  "callback_data": f"tp_{num}"},
        {"text": "SL",  "callback_data": f"sl_{num}"},
        {"text": "Derzu", "callback_data": f"hold_{num}"}
    ]]}

def poll():
    global offset
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 25, "allowed_updates": ["callback_query"]},
                timeout=30
            ).json()
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                cb = upd.get("callback_query")
                if not cb: continue
                parts = cb.get("data", "").split("_")
                if len(parts) != 2: continue
                action, num_str = parts
                try: num = int(num_str)
                except: continue
                if num not in trades:
                    answer_cb(cb["id"], "Ne najden")
                    continue
                t   = trades[num]
                sig = t["signal"]
                mid = cb["message"]["message_id"]
                if action == "tp":
                    trades[num]["result"] = "tp"
                    answer_cb(cb["id"], f"+${sig['profit']} TP!")
                    edit(mid, fmt_result(sig, num, "tp"))
                elif action == "sl":
                    trades[num]["result"] = "sl"
                    answer_cb(cb["id"], f"-${sig['loss']} SL")
                    edit(mid, fmt_result(sig, num, "sl"))
                elif action == "hold":
                    trades[num]["result"] = "hold"
                    p = get_price(sig["symbol"])
                    answer_cb(cb["id"], "Proveryayu...")
                    edit(mid, fmt_result(sig, num, "hold", p))
        except Exception as e:
            log.debug(f"poll: {e}")
        time.sleep(1)

def daily_stats():
    total = len(trades)
    tp_n  = sum(1 for t in trades.values() if t.get("result") == "tp")
    sl_n  = sum(1 for t in trades.values() if t.get("result") == "sl")
    wr    = round(tp_n / max(tp_n + sl_n, 1) * 100)
    fee   = VOLUME * FEE_PCT / 100
    pnl   = round(tp_n * (VOLUME * TP_PCT/100 - fee) - sl_n * (VOLUME * 0.25/100 + fee), 2)
    sign  = "+" if pnl >= 0 else ""

    text = (
        f"<b>Statistika za {now_kyiv().strftime('%d.%m.%Y')}</b>\n\n"
        f"Signalov: {total} | TP: {tp_n} | SL: {sl_n}\n"
        f"Vinreyt: {wr}%\n"
        f"PnL: {sign}${pnl}\n\n"
        f"<b>Zhurnal dlya V7:</b>\n"
    )
    for num, t in list(trades.items())[-20:]:
        s   = t["signal"]
        res = (t.get("result") or "?").upper()
        text += f"<code>#{num}|{t['time']}|{s['symbol']}|{s['type']}|rr:1:{s['rr']}|rsi:{s['rsi']}|{res}</code>\n"
    send(text)

def main():
    global counter
    threading.Thread(target=poll, daemon=True).start()

    send(
        "<b>OKX Scanner v6.0 zapushen!</b>\n\n"
        "2 tipa signalov:\n"
        "🔥 TREND - silnyj impuls vverh\n"
        "📍 EMA BOUNCE - otkат k EMA21\n\n"
        f"TP: {TP_PCT}% | x{LEVERAGE} | ${VOLUME}\n"
        "Knopki: TP / SL / Derzu\n"
        "Statistika kazhdyj den v 23:55\n\n"
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
                daily_stats()
                trades.clear()
                last_stat_day = now_dt.day

            symbols = fetch_symbols()
            log.info(f"Scan {len(symbols)} | {get_session()}")

            for symbol in symbols:
                try:
                    if now_ts - alerted.get(symbol, 0) < COOLDOWN:
                        continue
                    sig = analyze(symbol)
                    if sig:
                        counter += 1
                        num = counter
                        mid = send(fmt_signal(sig, num), markup=buttons(num))
                        trades[num] = {
                            "signal": sig,
                            "msg_id": mid,
                            "time":   now_dt.strftime("%d.%m %H:%M"),
                            "result": None
                        }
                        alerted[symbol] = now_ts
                        found += 1
                        log.info(f"SIGNAL #{num} {symbol} {sig['type']} RR:1:{sig['rr']}")
                    time.sleep(0.15)
                except Exception as e:
                    log.debug(f"skip {symbol}: {e}")

            log.info(f"Signaly: {found} | pauza {INTERVAL}s")

            cutoff  = now_ts - COOLDOWN * 3
            alerted = {k: v for k, v in alerted.items() if v > cutoff}

            if len(trades) > 300:
                old = sorted(trades.keys())[:-150]
                for k in old:
                    del trades[k]

        except Exception as e:
            log.error(f"Error: {e}")
            time.sleep(30)

        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
