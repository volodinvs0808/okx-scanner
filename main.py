import requests
import time
from datetime import datetime

TELEGRAM_TOKEN = "8279259149:AAFyqvMHBnpRtMyaEMmPVJdSmffWGymWHYw"
TELEGRAM_CHAT_ID = "6724936490"
GAP_THRESHOLD = 0.05  # %
CHECK_INTERVAL = 60   # секунд

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message})

def get_futures_symbols():
    url = "https://www.okx.com/api/v5/public/instruments?instType=FUTURES"
    r = requests.get(url).json()
    return [i["instId"] for i in r.get("data", [])]

def get_candles(symbol):
    url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar=1m&limit=6"
    r = requests.get(url).json()
    return r.get("data", [])

def check_symbol(symbol):
    candles = get_candles(symbol)
    if len(candles) < 6:
        return False
    
    # OKX формат: [time, open, high, low, close, ...]
    # candles[0] = самая новая
    green = []
    no_gap = []
    
    for i in range(5):
        o = float(candles[i][1])
        c = float(candles[i][4])
        green.append(c > o)
    
    for i in range(4):
        curr_open = float(candles[i][1])
        prev_close = float(candles[i+1][4])
        gap = abs(curr_open - prev_close) / prev_close * 100
        no_gap.append(gap < GAP_THRESHOLD)
    
    return all(green) and all(no_gap)

def main():
    send_telegram("🤖 OKX Scanner запущен! Мониторю фьючерсы...")
    alerted = set()
    
    while True:
        try:
            symbols = get_futures_symbols()
            now = datetime.now().strftime("%H:%M")
            print(f"[{now}] Проверяю {len(symbols)} пар...")
            
            for symbol in symbols:
                try:
                    if check_symbol(symbol):
                        key = f"{symbol}_{now}"
                        if key not in alerted:
                            msg = f"🟢 {symbol}\n5 зелёных свечей подряд без гэпов!\n⏰ {now}"
                            send_telegram(msg)
                            alerted.add(key)
                            print(f"СИГНАЛ: {symbol}")
                    time.sleep(0.1)
                except Exception as e:
                    pass
            
            # Очищаем старые алерты
            if len(alerted) > 1000:
                alerted.clear()
                
        except Exception as e:
            print(f"Ошибка: {e}")
        
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
