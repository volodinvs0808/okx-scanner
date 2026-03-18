"""
OKX Liquidity Grab Scanner v4.0
Strategiya: Smart Money Concept - Liquidity Sweep + Order Block
Avtor: @FWSCryptobot
"""

import requests
import time
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional

# ─── Nastroyki ────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN   = "8279259149:AAFyqvMHBnpRtMyaEMmPVJdSmffWGymWHYw"
TELEGRAM_CHAT_ID = "6724936490"

DEPOSIT          = 509
LEVERAGE         = 5
VOLUME           = DEPOSIT * LEVERAGE
TP_PCT           = 1.2
FEE_PCT          = 0.1
SIGNAL_COOLDOWN  = 1800   # sekund mezhdu signalami dlya odnoj pary
SCAN_INTERVAL    = 60     # sekund mezhdu skanirovaniyami
MIN_RR           = 2.5    # minimalnyj Risk/Reward

# Filtry
WICK_MIN         = 0.45   # min fitil' (45% svechki)
VOL_MULT         = 1.5    # ob'em v N raz vyshe srednego
RSI_LOW          = 30
RSI_HIGH         = 70
ATR_MIN_PCT      = 0.03   # min volatil'nost' 0.03%
SL_MIN_PCT       = 0.08   # min stoploss
SL_MAX_PCT       = 0.45   # max stoploss

KYIV_TZ = timezone(timedelta(hours=2))

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("scanner")

# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class Signal:
    symbol:    str
    entry:     float
    tp:        float
    sl:        float
    sl_pct:    float
    rr:        float
    rsi:       float
    vol_ratio: float
    wick_pct:  float
    profit:    float
    loss:      float
    level:     float
    session:   str

# ─── Helpers ──────────────────────────────────────────────────────────────────

def now_kyiv() -> datetime:
    return datetime.now(KYIV_TZ)


def get_session() -> str:
    h = now_kyiv().hour
    if 2  <= h < 8:  return "Asia Session"
    if 8  <= h < 12: return "London Open"
    if 14 <= h < 19: return "New York Open"
    return "Off-Hours"


def ema(prices: list, period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    k   = 2 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return val


def rsi(closes: list, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains) / period
    al = sum(losses) / period
    if al == 0:
        return 100.0
    return 100 - (100 / (1 + ag / al))


def atr(candles: list, period: int = 14) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h  = float(candles[i][2])
        l  = float(candles[i][3])
        pc = float(candles[i - 1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period

# ─── API ──────────────────────────────────────────────────────────────────────

def fetch_symbols() -> list:
    url = "https://www.okx.com/api/v5/public/instruments?instType=SWAP&ctType=linear"
    r   = requests.get(url, timeout=10).json()
    return [i["instId"] for i in r.get("data", []) if i["instId"].endswith("-USDT-SWAP")]


def fetch_candles(symbol: str, bar: str = "5m", limit: int = 80) -> list:
    url  = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={bar}&limit={limit}"
    r    = requests.get(url, timeout=10).json()
    data = r.get("data", [])
    return list(reversed(data)) if data else []

# ─── Analiz ───────────────────────────────────────────────────────────────────

def analyze(symbol: str) -> Optional[Signal]:
    candles = fetch_candles(symbol, bar="5m", limit=80)
    if len(candles) < 40:
        return None

    o = [float(c[1]) for c in candles]
    h = [float(c[2]) for c in candles]
    l = [float(c[3]) for c in candles]
    c = [float(c[4]) for c in candles]
    v = [float(c[5]) for c in candles]

    # Svechi: [-3]=signal, [-2]=podtverzhdenie, [-1]=tekushchaya
    s_o, s_h, s_l, s_c, s_v = o[-3], h[-3], l[-3], c[-3], v[-3]
    p_o, p_c                 = o[-2], c[-2]
    cur_o, cur_c             = o[-1], c[-1]

    # ── 1. Lokal'nyj minimum (uroven' likvidnosti) ──
    local_low = min(l[-28:-4])

    # ── 2. Sweep: signal'naya svecha probila uroven' ──
    swept = s_l < local_low * 0.9998

    # ── 3. Reclaim: zakrylas' vyshe urovnya ──
    reclaimed = s_c >= local_low

    # ── 4. Fitil' vniz >= 45% diapazona svechki ──
    rng = s_h - s_l
    if rng < 1e-9:
        return None
    wick     = min(s_o, s_c) - s_l
    wick_r   = wick / rng
    wick_ok  = wick_r >= WICK_MIN

    # ── 5. Ob'em na signal'noj svechke vyshe srednego ──
    avg_v   = sum(v[-30:-4]) / 26
    vol_ok  = avg_v > 0 and s_v > avg_v * VOL_MULT

    # ── 6. Podtverzhdayushchaya svecha: zelenaya i vyshe urovnya ──
    conf_ok = p_c > p_o and p_c > local_low

    # ── 7. Tekushchaya svecha derzhitsya ──
    hold_ok = cur_c >= p_o * 0.9995 and cur_c > cur_o

    # ── 8. RSI ──
    rsi_v  = rsi(c, 14)
    rsi_ok = rsi_v is not None and RSI_LOW <= rsi_v <= RSI_HIGH

    # ── 9. Trend po EMA (5m) ──
    e21     = ema(c, 21)
    e50     = ema(c, 50)
    trend   = e21 is not None and e50 is not None and e21 >= e50 * 0.998

    # ── 10. ATR — dostatochnaya volatil'nost' ──
    atr_v   = atr(candles, 14)
    atr_ok  = atr_v is not None and (atr_v / cur_c * 100) >= ATR_MIN_PCT

    # ── 11. Dopolnitel'nyj fil'tr: telo signal'noj svechki ne sleduet byt' ogromnynm ──
    body_ratio = abs(s_c - s_o) / rng
    body_ok    = body_ratio <= 0.50

    all_ok = (swept and reclaimed and wick_ok and vol_ok
              and conf_ok and hold_ok and rsi_ok and trend and atr_ok and body_ok)

    if not all_ok:
        return None

    # ── Raschyot tochek vhoda ──
    entry    = cur_c
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

    return Signal(
        symbol    = symbol,
        entry     = round(entry, 8),
        tp        = round(entry * (1 + TP_PCT / 100), 8),
        sl        = round(sl_price, 8),
        sl_pct    = round(sl_pct, 3),
        rr        = round(rr_val, 1),
        rsi       = round(rsi_v, 1),
        vol_ratio = round(s_v / avg_v, 2),
        wick_pct  = round(wick_r * 100, 1),
        profit    = profit,
        loss      = loss,
        level     = round(local_low, 8),
        session   = get_session(),
    )

# ─── Telegram ─────────────────────────────────────────────────────────────────

def send(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       text,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        log.warning(f"Telegram error: {e}")


def format_signal(s: Signal) -> str:
    rr_bar = "█" * min(int(s.rr), 10)
    return (
        f"<b>LIQUIDITY GRAB  {s.symbol}</b>\n"
        f"<i>{s.session}</i>\n"
        f"{'─' * 28}\n"
        f"Vhod:   <b>${s.entry}</b>\n"
        f"TP:     <b>${s.tp}</b>  +{TP_PCT}%\n"
        f"SL:     <b>${s.sl}</b>  -{s.sl_pct}%\n"
        f"{'─' * 28}\n"
        f"RR:     1:{s.rr}  {rr_bar}\n"
        f"RSI:    {s.rsi}\n"
        f"Volume: x{s.vol_ratio}\n"
        f"Fitil:  {s.wick_pct}%\n"
        f"Uroven: ${s.level}\n"
        f"{'─' * 28}\n"
        f"Pribyl (TP):  <b>+${s.profit}</b>\n"
        f"Ubytok (SL):  <b>-${s.loss}</b>\n"
        f"Obem: ${VOLUME}  Plecho: x{LEVERAGE}\n"
        f"{'─' * 28}\n"
        f"{now_kyiv().strftime('%d.%m.%Y  %H:%M:%S')}"
    )

# ─── Main loop ────────────────────────────────────────────────────────────────

def main() -> None:
    send(
        "<b>OKX Liquidity Grab Scanner v4.0</b>\n\n"
        "Smart Money Concept\n"
        "Filtry: Sweep + Reclaim + Volume + RSI + EMA + ATR\n"
        f"RR min: 1:{MIN_RR} | TP: {TP_PCT}% | Plecho: x{LEVERAGE}\n"
        f"Depozit: ${DEPOSIT} | Obem: ${VOLUME}\n"
        "Rezhim: 24/7\n\n"
        "Sistema gotova. Zhdu signalov..."
    )

    alerted: dict = {}

    while True:
        try:
            symbols  = fetch_symbols()
            now_ts   = time.time()
            now_str  = now_kyiv().strftime("%H:%M")
            found    = 0

            log.info(f"Scan: {len(symbols)} par | session: {get_session()}")

            for symbol in symbols:
                try:
                    if now_ts - alerted.get(symbol, 0) < SIGNAL_COOLDOWN:
                        continue

                    sig = analyze(symbol)

                    if sig:
                        send(format_signal(sig))
                        alerted[symbol] = now_ts
                        found += 1
                        log.info(
                            f"SIGNAL {symbol} | "
                            f"RR 1:{sig.rr} | "
                            f"Vol x{sig.vol_ratio} | "
                            f"Fitil {sig.wick_pct}%"
                        )

                    time.sleep(0.15)

                except Exception as e:
                    log.debug(f"skip {symbol}: {e}")

            if found == 0:
                log.info(f"Signalov net. Sleduushchij skan cherez {SCAN_INTERVAL}s")

            cutoff  = now_ts - SIGNAL_COOLDOWN * 3
            alerted = {k: v for k, v in alerted.items() if v > cutoff}

        except Exception as e:
            log.error(f"Global error: {e}")
            time.sleep(30)

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
