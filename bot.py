import requests
import time
from datetime import datetime, timedelta
import os
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ===== НАСТРОЙКИ =====
RSI_THRESHOLD = 85
HOURS_BACK = 3              # RSI за последние 3 часа
MAX_CANDLE_AGE_MINUTES = 15 # свеча не старше 15 минут
TIMEFRAME_1H = "60"
TIMEFRAME_15M = "15"
# =====================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

def send_telegram(text):
    if not TOKEN or not CHAT_ID:
        print("❌ Нет TOKEN или CHAT_ID")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text},
            headers=HEADERS,
            timeout=10
        )
    except Exception as e:
        print(f"Ошибка отправки: {e}")

def get_bybit_klines(symbol, interval, limit=30):
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
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
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        if data['retCode'] != 0:
            return []
        return [x['symbol'] for x in data['result']['list'] if x['symbol'].endswith('USDT')]
    except Exception as e:
        print(f"Ошибка получения списка монет: {e}")
        return []

def wait_for_next_15m():
    now = datetime.now()
    minutes = now.minute
    next_minute = ((minutes // 15) + 1) * 15
    if next_minute == 60:
        next_minute = 0
        now += timedelta(hours=1)
    target = now.replace(minute=next_minute, second=0, microsecond=0)
    sleep_seconds = (target - now).total_seconds()
    if sleep_seconds > 0:
        print(f"⏳ Жду до {target.strftime('%H:%M')}...")
        time.sleep(sleep_seconds)

def scan_and_alert():
    symbols = get_all_usdt_symbols()
    if not symbols:
        print("⚠️ Нет списка монет")
        return

    now = datetime.now()
    print(f"🔍 Сканирую {len(symbols)} монет...")

    for sym in symbols:
        try:
            # === 15M — паттерн на последней свече ===
            data_15m = get_bybit_klines(sym, TIMEFRAME_15M, limit=5)
            if not data_15m or len(data_15m) < 2:
                continue

            # Проверяем время последней 15M свечи
            last_15m_time = datetime.fromtimestamp(int(data_15m[-1][0]) / 1000)
            if (now - last_15m_time) > timedelta(minutes=MAX_CANDLE_AGE_MINUTES):
                continue  # свеча старая — пропускаем

            closes_15m = [float(x[4]) for x in data_15m]
            volumes_15m = [float(x[5]) for x in data_15m]
            rsi_15m = calculate_rsi(closes_15m)

            pinbar_found = check_pin_bar(data_15m[-1])
            engulfing_found = check_engulfing(data_15m)

            if not (pinbar_found or engulfing_found):
                continue

            # === Объём ===
            avg_volume = sum(volumes_15m[-6:-1]) / 5 if len(volumes_15m) >= 6 else volumes_15m[-1]
            last_volume = volumes_15m[-1]
            volume_ratio = last_volume / avg_volume if avg_volume > 0 else 0
            volume_status = "🔻 Падает" if volume_ratio < 0.8 else "🟡 Высокий"

            # === 1H — RSI за последние 3 часа ===
            data_1h = get_bybit_klines(sym, TIMEFRAME_1H, limit=10 + HOURS_BACK)
            if not data_1h or len(data_1h) < 14:
                continue

            # Проверяем время последней 1H свечи
            last_1h_time = datetime.fromtimestamp(int(data_1h[-1][0]) / 1000)
            if (now - last_1h_time) > timedelta(hours=HOURS_BACK):
                continue  # данные устарели — пропускаем

            rsi_over_threshold = False
            max_rsi = 0
            for i in range(1, HOURS_BACK + 1):
                if len(data_1h) < i:
                    continue
                closes_1h = [float(x[4]) for x in data_1h[:-i]]
                if len(closes_1h) < 14:
                    continue
                rsi = calculate_rsi(closes_1h)
                if rsi > max_rsi:
                    max_rsi = rsi
                if rsi > RSI_THRESHOLD:
                    rsi_over_threshold = True

            if not rsi_over_threshold:
                continue

            # === Отправка ===
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
                f"📊 1H RSI (пик за {HOURS_BACK}ч): **{max_rsi:.1f}**\n"
                f"📉 15M RSI: **{rsi_15m:.1f}**\n"
                f"📈 Паттерн: {pattern_text}\n"
                f"🔊 Объём: {volume_status}\n"
                f"━━━━━━━━━━━━━━━━━━"
            )

            send_telegram(msg)
            print(f"✅ {sym}")
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
    send_telegram("✅ Бот запущен! Сканирование в конце каждой 15-минутной свечи (RSI > 85, последние 3 часа)")
    print("🤖 Бот запущен. Жду 15-минутных интервалов...")
    while True:
        wait_for_next_15m()
        scan_and_alert()
        print("⏳ Ожидание следующего интервала...")