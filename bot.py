import requests
import time
from datetime import datetime, timedelta

TOKEN = "8914466964:AAHuEgd_dSrSUaYzb1Vp_UVgohe-S_-Uk9w"
CHAT_ID = "946984734"
RSI_THRESHOLD_1H = 85
RSI_THRESHOLD_15M = 80
TIMEFRAME_1H = "60"
TIMEFRAME_15M = "15"

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text})
    except Exception as e:
        print(f"Ошибка отправки: {e}")

def get_bybit_klines(symbol, interval, limit=30):
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    try:
        r = requests.get(url).json()
        if r['retCode'] != 0:
            return None
        return r['result']['list']
    except:
        return None

def calculate_rsi(closes):
    gain = 0
    loss = 0
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        if diff > 0:
            gain += diff
        else:
            loss += abs(diff)
    avg_gain = gain / 14
    avg_loss = loss / 14
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + avg_gain / avg_loss))

def check_pin_bar(candle):
    open_price = float(candle[1])
    high = float(candle[2])
    low = float(candle[3])
    close = float(candle[4])
    body = abs(close - open_price)
    upper_shadow = high - max(open_price, close)
    lower_shadow = min(open_price, close) - low
    if upper_shadow > body * 2 and lower_shadow < body * 0.5:
        return True
    return False

def check_engulfing(candles):
    if len(candles) < 2:
        return False
    prev = candles[-2]
    curr = candles[-1]
    prev_open = float(prev[1])
    prev_close = float(prev[4])
    curr_open = float(curr[1])
    curr_close = float(curr[4])
    if prev_close > prev_open and curr_close < curr_open:
        if curr_open > prev_close and curr_close < prev_open:
            return True
    return False

def get_all_usdt_symbols():
    url = "https://api.bybit.com/v5/market/tickers?category=linear"
    r = requests.get(url).json()
    return [x['symbol'] for x in r['result']['list'] if x['symbol'].endswith('USDT')]

def scan_and_alert():
    symbols = get_all_usdt_symbols()
    now = datetime.now()
    for sym in symbols:
        try:
            data_1h = get_bybit_klines(sym, TIMEFRAME_1H)
            if not data_1h or len(data_1h) < 15:
                continue
            last_candle_time = datetime.fromtimestamp(int(data_1h[-1][0]) / 1000)
            if now - last_candle_time > timedelta(hours=1):
                continue
            closes_1h = [float(x[4]) for x in data_1h]
            rsi_1h = calculate_rsi(closes_1h)
            if rsi_1h < RSI_THRESHOLD_1H:
                continue

            data_15m = get_bybit_klines(sym, TIMEFRAME_15M, limit=20)
            if not data_15m or len(data_15m) < 15:
                continue
            closes_15m = [float(x[4]) for x in data_15m]
            volumes_15m = [float(x[5]) for x in data_15m]
            rsi_15m = calculate_rsi(closes_15m)
            if rsi_15m < RSI_THRESHOLD_15M:
                continue

            avg_volume = sum(volumes_15m[-6:-1]) / 5
            last_volume = volumes_15m[-1]
            volume_ratio = last_volume / avg_volume if avg_volume > 0 else 0
            volume_status = "🔻" if volume_ratio < 0.8 else "🟡"

            pinbar = check_pin_bar(data_15m[-1])
            engulfing = check_engulfing(data_15m)

            if pinbar or engulfing:
                pin_text = "🟢 Пин-бар" if pinbar else ""
                eng_text = "🟢 Поглощение" if engulfing else ""
                msg = (f"{volume_status} {sym} — 1H RSI: {rsi_1h:.1f} | 15M RSI: {rsi_15m:.1f} "
                       f"{pin_text} {eng_text} "
                       f"(объём {'падает' if volume_ratio < 0.8 else 'высокий'}, свеча {last_candle_time.strftime('%H:%M')})")
                print(msg)
                send_telegram(msg)
                time.sleep(1)

        except Exception as e:
            print(f"❌ {sym}: {e}")
            continue

if __name__ == "__main__":
    send_telegram("✅ Бот обновлён! RSI + объём + пин-бар + поглощение на 15M")
    print("🤖 Бот запущен. Ищу монеты с RSI > 85 на 1H, RSI > 80 на 15M, пин-бар или поглощение")
    while True:
        scan_and_alert()
        print("⏳ Пауза 5 минут...")
        time.sleep(300)