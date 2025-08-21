"""
monitor.py
Main script: reads credentials from environment variables and runs the Quotex monitor.
Sends a Telegram message when 4 consecutive 1-minute candles of the same color occur
on any of the top-3 monitored assets.

Notes:
- Provide credentials via environment variables:
    QUOTEX_EMAIL, QUOTEX_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
- If your repo already contains a Quotex client implementation (stable_api.py or api.py),
  monitor.py will try to import `Quotex` from them.
- Render workers are non-interactive. If your account requires interactive 2FA on each login,
  you will need to implement session reuse (I can help add that).
"""

import asyncio
import time
from collections import deque, defaultdict
import requests
import os
import traceback

# Read credentials from environment variables (do NOT put real tokens in the repo)
QUOTEX_EMAIL = os.getenv("QUOTEX_EMAIL")
QUOTEX_PASSWORD = os.getenv("QUOTEX_PASSWORD")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not all([QUOTEX_EMAIL, QUOTEX_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
    print("WARNING: One or more required environment variables are missing.")
    print("Set QUOTEX_EMAIL, QUOTEX_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID in your host.")

# Config (can be overridden via env)
TIMEFRAME = int(os.getenv("TIMEFRAME", 60))            # 1-minute candles
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", 300))  # 5 minutes default cooldown per asset
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", 1.0))      # loop sleep seconds

last_alert_time = defaultdict(lambda: 0)
recent_colors = defaultdict(lambda: deque(maxlen=4))  # keep last 4 closed candles


def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials not set. Skipping telegram send. Message would be:", text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print("Telegram send error:", e)


def candle_color(candle):
    """
    Uniformly determine candle color from common candle dict keys.
    Returns: 'green', 'red', or 'doji'
    """
    try:
        o = float(candle.get("open") or candle.get("o") or candle.get("O"))
        c = float(candle.get("close") or candle.get("c") or candle.get("C"))
        if c > o:
            return "green"
        elif c < o:
            return "red"
        else:
            return "doji"
    except Exception:
        return "doji"


async def pick_top_3_assets(qx):
    """
    Try to pick top 3 assets by payout if available; otherwise fallback to first 3 codes.
    This is best-effort because the internal structure of the client may vary.
    """
    try:
        instruments = getattr(qx.api, "instruments", None)
        if instruments:
            pairs = []
            for i in instruments:
                try:
                    code = i[1]
                    payment = float(i[5]) if i[5] not in (None, "") else 0.0
                    pairs.append((code, payment))
                except Exception:
                    continue
            if pairs:
                pairs.sort(key=lambda x: x[1], reverse=True)
                return [p for p, _ in pairs[:3]]
    except Exception:
        print("pick_top_3_assets: error checking instruments:", traceback.format_exc())

    try:
        assets = list(qx.codes_asset.keys())
        return assets[:3]
    except Exception:
        return []


async def monitor():
    # Import any available Quotex client implementation
    try:
        from stable_api import Quotex  # prefer local stable_api if present
    except Exception:
        try:
            from api import Quotex
        except Exception:
            print("ERROR: No Quotex client found. Please add stable_api.py or api.py to the repo.")
            return

    print("Connecting to Quotex... (using env credentials)")
    qx = Quotex(email=QUOTEX_EMAIL, password=QUOTEX_PASSWORD, lang="en", user_data_dir="browser")

    # connect/login (client should handle 2FA/session; may prompt if interactive)
    try:
        await qx.connect()
    except Exception as e:
        print("Quotex connect error:", e)
        print("If this hangs or requests 2FA, implement session reuse or run locally where you can provide 2FA.")
        return

    # load assets
    try:
        await qx.get_all_assets()
    except Exception:
        print("Warning: get_all_assets() failed or is unavailable; continuing anyway.")

    top_assets = await pick_top_3_assets(qx)
    if not top_assets:
        print("No assets found to monitor. Exiting.")
        return

    print("Monitoring assets:", top_assets)

    # Start candle streams if client exposes that method
    for asset in top_assets:
        try:
            qx.start_candles_stream(asset, TIMEFRAME)
        except Exception:
            # not fatal; maybe client doesn't need explicit start
            pass

    last_seen_ts = {}

    while True:
        try:
            for asset in top_assets:
                candles = None
                try:
                    candles = await qx.get_realtime_candles(asset)
                except Exception as e:
                    # transient failure; print and continue
                    print(f"get_realtime_candles error for {asset}: {e}")

                if not candles:
                    continue

                # candles expected as dict mapping timestamp -> candle dict
                try:
                    sorted_candles = sorted(candles.items(), key=lambda x: int(x[0]))
                except Exception:
                    # if format differs, skip
                    continue

                if not sorted_candles:
                    continue

                ts, candle = sorted_candles[-1]
                if last_seen_ts.get(asset) == ts:
                    continue
                last_seen_ts[asset] = ts

                color = candle_color(candle)
                if color == "doji":
                    continue

                seq = recent_colors[asset]
                seq.append(color)

                if len(seq) == 4 and all(c == seq[0] for c in seq):
                    now = time.time()
                    if now - last_alert_time[asset] >= COOLDOWN_SECONDS:
                        msg = f"🔔 {asset}: 4 closed candles in a row — {seq[0].upper()}\nLatest ts: {ts}"
                        print(msg)
                        send_telegram(msg)
                        last_alert_time[asset] = now
        except Exception:
            print("Main loop error:", traceback.format_exc())

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(monitor())
    except KeyboardInterrupt:
        print("Exiting monitor")
