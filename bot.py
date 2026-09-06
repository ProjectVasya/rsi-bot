import requests
import time
from datetime import datetime, timedelta
import os
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

RSI_THRESHOLD = 60
HOURS_BACK = 5
CANDLES_BACK = 4
TIMEFRAME_1H = "60"
TIMEFRAME_15M = "15"

def send_telegram(text):
    if not TOKEN or not CHAT_ID:
        print("❌ Нет TOKEN или CHAT_ID")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text}
        )
    except Exception as e:
        print(f"Ошибка отправки: {e}")

def get_bybit_klines(symbol, interval, limit=30):
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        if data['retCode'] != 0:
            return None
        return data['result']['list']
    except Exception as e:
        print(f"Ошибка получения {symbol}: {e}")
        return None

def calculate_rsi(closes):
    if len(closes) < 14:
        return 50
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
    if body == 0:
        return False
    if upper_shadow > body * 1.5 and lower_shadow < body * 0.8:
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
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        if data['retCode'] != 0:
            return []
        return [x['symbol'] for x in data['result']['list'] if x['symbol'].endswith('USDT')]
    except Exception as e:
        print(f"Ошибка получения списка монет: {e}")
        return []

def scan_and_alert():
    symbols = get_all_usdt_symbols()
    if not symbols:
        send_telegram("⚠️ Не удалось получить список монет")
        print("⚠️ Нет списка монет")
        return

    send_telegram(f"🔍 Сканирую {len(symbols)} монет...")
    print(f"🔍 Сканирую {len(symbols)} монет")

    now = datetime.now()
    checked = 0
    for sym in symbols[:50]:  # ограничим 50 монетами для теста
        try:
            checked += 1
            if checked % 10 == 0:
                print(f"⏳ Проверено {checked} монет")

            # === 15M данные ===
            data_15m = get_bybit_klines(sym, TIMEFRAME_15M, limit=20)
            if not data_15m or len(data_15m) < 15:
                continue

            closes_15m = [float(x[4]) for x in data_15m]
            volumes_15m = [float(x[5]) for x in data_15m]
            rsi_15m = calculate_rsi(closes_15m)

            # === Ищем паттерн ===
            pinbar_found = False
            engulfing_found = False
            for i in range(1, CANDLES_BACK + 1):
                if check_pin_bar(data_15m[-i]):
                    pinbar_found = True
                    break
                if check_engulfing(data_15m[-i-1:-i+1] if i > 1 else data_15m[-2:]):
                    engulfing_found = True
                    break

            if not (pinbar_found or engulfing_found):
                continue

            # === Проверка RSI за 5 часов ===
            data_1h = get_bybit_klines(sym, TIMEFRAME_1H, limit=15 + HOURS_BACK)
            if not data_1h or len(data_1h) < 14:
                continue

            rsi_over_threshold = False
            for i in range(1, HOURS_BACK + 1):
                if len(data_1h) < i:
                    continue
                closes_1h = [float(x[4]) for x in data_1h[:-i]]
                if len(closes_1h) < 14:
                    continue
                rsi_1h = calculate_rsi(closes_1h)
                if rsi_1h > RSI_THRESHOLD:
                    rsi_over_threshold = True
                    break

            if not rsi_over_threshold:
                continue

            # === Отправка сигнала ===
            pattern = []
            if pinbar_found:
                pattern.append("🟢 Пин-бар")
            if engulfing_found:
                pattern.append("🟢 Поглощение")
            pattern_text = " + ".join(pattern) if pattern else "—"

            msg = (
                f"🔔 **СИГНАЛ** 🔔\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🪙 **{sym}**\n"
                f"💰 Цена: `{float(data_15m[-1][4]):.4f}`\n"
                f"📊 1H RSI (пик за 5ч): {max([calculate_rsi([float(x[4]) for x in data_1h[:-i]]) for i in range(1, HOURS_BACK+1) if len([float(x[4]) for x in data_1h[:-i]]) >= 14]):.1f}\n"
                f"📉 15M RSI: **{rsi_15m:.1f}**\n"
                f"📈 Паттерн: {pattern_text}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"✅ Тестовый режим (RSI > {RSI_THRESHOLD})"
            )
            send_telegram(msg)
            print(f"✅ Сигнал: {sym}")
            time.sleep(1)

        except Exception as e:
            print(f"❌ {sym}: {e}")
            continue

    send_telegram(f"✅ Сканирование завершено. Проверено {checked} монет.")
    print(f"✅ Проверено {checked} монет")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_webserver():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

if __name__ == "__main__":
    Thread(target=run_webserver, daemon=True).start()
    send_telegram("🔧 Диагностический режим запущен. Сканирую 50 монет...")
    print("🔧 Диагностический режим")
    scan_and_alert()
    while True:
        time.sleep(300)
        scan_and_alert()