import requests
import time
from datetime import datetime, timedelta
import os
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# ===== НАСТРОЙКИ (ТЕСТОВЫЙ РЕЖИМ) =====
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

RSI_THRESHOLD = 60          # тестовый порог
HOURS_BACK = 5              # проверяем за последние 5 часов

TIMEFRAME_1H = "60"
TIMEFRAME_15M = "15"
# =======================================

def send_telegram(text):
    if not TOKEN or not CHAT_ID:
        print("❌ Ошибка: TOKEN или CHAT_ID не заданы")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": text})
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
    except:
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
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        if data['retCode'] != 0:
            return []
        return [x['symbol'] for x in data['result']['list'] if x['symbol'].endswith('USDT')]
    except:
        return []

def scan_and_alert():
    symbols = get_all_usdt_symbols()
    if not symbols:
        print("⚠️ Не удалось получить список монет. Попробую позже...")
        return

    now = datetime.now()
    for sym in symbols:
        try:
            # === 15M — ищем пин-бар или поглощение ===
            data_15m = get_bybit_klines(sym, TIMEFRAME_15M, limit=20)
            if not data_15m or len(data_15m) < 15:
                continue

            closes_15m = [float(x[4]) for x in data_15m]
            volumes_15m = [float(x[5]) for x in data_15m]
            rsi_15m = calculate_rsi(closes_15m)

            pinbar = check_pin_bar(data_15m[-1])
            engulfing = check_engulfing(data_15m)

            if not (pinbar or engulfing):
                continue

            # === Объём на 15M ===
            avg_volume = sum(volumes_15m[-6:-1]) / 5 if len(volumes_15m) >= 6 else volumes_15m[-1]
            last_volume = volumes_15m[-1]
            volume_ratio = last_volume / avg_volume if avg_volume > 0 else 0
            volume_status = "🔻 Падает" if volume_ratio < 0.8 else "🟡 Высокий"

            # === Проверяем перегрев на 1H за последние HOURS_BACK часов ===
            data_1h = get_bybit_klines(sym, TIMEFRAME_1H, limit=15 + HOURS_BACK)
            if not data_1h or len(data_1h) < 14:
                continue

            rsi_over_threshold = False
            max_rsi_1h = 0
            for i in range(1, HOURS_BACK + 1):
                if len(data_1h) < i:
                    continue
                closes_1h = [float(x[4]) for x in data_1h[:-i]] if i > 0 else [float(x[4]) for x in data_1h]
                if len(closes_1h) < 14:
                    continue
                rsi_1h = calculate_rsi(closes_1h)
                if rsi_1h > max_rsi_1h:
                    max_rsi_1h = rsi_1h
                if rsi_1h > RSI_THRESHOLD:
                    rsi_over_threshold = True

            if not rsi_over_threshold:
                continue

            # === Цена ===
            price = float(data_15m[-1][4])

            # === Формируем уведомление ===
            pattern = []
            if pinbar:
                pattern.append("🟢 Пин-бар")
            if engulfing:
                pattern.append("🟢 Поглощение")
            pattern_text = " + ".join(pattern) if pattern else "—"

            msg = (
                f"🔔 **НОВЫЙ СИГНАЛ** 🔔\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🪙 **{sym}**\n"
                f"💰 Цена: `{price:.4f}`\n"
                f"📊 1H RSI (пик за {HOURS_BACK}ч): **{max_rsi_1h:.1f}**\n"
                f"📉 15M RSI: **{rsi_15m:.1f}**\n"
                f"📈 Паттерн: {pattern_text}\n"
                f"🔊 Объём: {volume_status}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"✅ Тестовый режим (RSI > {RSI_THRESHOLD})"
            )

            print(msg)
            send_telegram(msg)
            time.sleep(1)

        except Exception as e:
            print(f"❌ {sym}: {e}")
            continue

# === ВЕБ-СЕРВЕР ===
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

# === ЗАПУСК ===
if __name__ == "__main__":
    Thread(target=run_webserver, daemon=True).start()
    print("🌐 Веб-сервер запущен")

    send_telegram(f"👋 **Тестовый режим**\n🔍 Ищу паттерны + RSI > {RSI_THRESHOLD} за последние {HOURS_BACK} часов\n📊 Ожидаю много сигналов для проверки")
    print(f"🤖 Бот запущен. Тестовый режим: RSI > {RSI_THRESHOLD}, окно {HOURS_BACK} часов")
    while True:
        scan_and_alert()
        print("⏳ Пауза 5 минут...")
        time.sleep(300)