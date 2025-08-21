import asyncio
import time
from collections import deque, defaultdict
import requests
import os, traceback

# Credentials must be provided via environment variables
QUOTEX_EMAIL = os.getenv("QUOTEX_EMAIL")
QUOTEX_PASSWORD = os.getenv("QUOTEX_PASSWORD")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not all([QUOTEX_EMAIL, QUOTEX_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
    print("WARNING: One or more required environment variables are missing. Set them in your host (Render/GitHub Secrets).")

TIMEFRAME = 60
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", 300))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", 1.0))

last_alert_time = defaultdict(lambda: 0)
recent_colors = defaultdict(lambda: deque(maxlen=4))

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram creds not set. Message:", text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print('Telegram send error:', e)

def candle_color(candle):
    try:
        o = float(candle.get('open') or candle.get('o') or candle.get('O'))
        c = float(candle.get('close') or candle.get('c') or candle.get('C'))
        if c > o:
            return 'green'
        elif c < o:
            return 'red'
        else:
            return 'doji'
    except Exception:
        return 'doji'

async def pick_top_3_assets(qx):
    try:
        instruments = getattr(qx.api, "instruments", None)
        if instruments:
            asset_payments = []
            for i in instruments:
                try:
                    code = i[1]
                    payment = float(i[5]) if i[5] not in (None, "") else 0.0
                    asset_payments.append((code, payment))
                except Exception:
                    continue
            if asset_payments:
                asset_payments.sort(key=lambda x: x[1], reverse=True)
                top = [a for a, _ in asset_payments[:3]]
                if top:
                    return top
    except Exception:
        print("Error computing top payouts:", traceback.format_exc())
    try:
        assets = list(qx.codes_asset.keys())
        return assets[:3]
    except Exception:
        return []

async def monitor():
    print("Connecting to Quotex... (env credentials)")
    try:
        from stable_api import Quotex
    except Exception:
        try:
            from api import Quotex
        except Exception:
            print("Quotex client not found (stable_api.py/api.py). Add a compatible client to the repo.")
            return

    qx = Quotex(email=QUOTEX_EMAIL, password=QUOTEX_PASSWORD, lang='en', user_data_dir='browser')
    await qx.connect()

    await qx.get_all_assets()
    top_assets = await pick_top_3_assets(qx)
    if not top_assets:
        print("No assets found to monitor. Exiting.")
        return

    print("Monitoring top assets:", top_assets)
    for asset in top_assets:
        try:
            qx.start_candles_stream(asset, TIMEFRAME)
        except Exception:
            print(f'Failed to start stream for {asset}:', traceback.format_exc())

    last_seen_ts = {}
    while True:
        try:
            for asset in top_assets:
                try:
                    candles = await qx.get_realtime_candles(asset)
                except Exception as e:
                    print(f'get_realtime_candles error for {asset}: {e}')
                    candles = None
                if not candles:
                    continue
                sorted_candles = sorted(candles.items(), key=lambda x: int(x[0]))
                if not sorted_candles:
                    continue
                ts, candle = sorted_candles[-1]
                if last_seen_ts.get(asset) == ts:
                    continue
                last_seen_ts[asset] = ts
                color = candle_color(candle)
                if color == 'doji':
                    continue
                seq = recent_colors[asset]
                seq.append(color)
                if len(seq) == 4 and all(c == seq[0] for c in seq):
                    now = time.time()
                    if now - last_alert_time[asset] >= COOLDOWN_SECONDS:
                        msg = f"🔔 {asset}: 4 closed candles in a row — {seq[0].upper()}. Latest ts: {ts}"
                        print(msg)
                        send_telegram(msg)
                        last_alert_time[asset] = now
        except Exception:
            print('Main loop error:', traceback.format_exc())
        await asyncio.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    try:
        asyncio.run(monitor())
    except KeyboardInterrupt:
        print('Exiting monitor')
