"""Record everything the testnet publishes: the storm feed plus quotes, trades and order books of all 12 symbols.

Read-only: this script never places, cancels or creates anything.

    python3 -I record_all.py --out recordings/season.jsonl [--key-file ~/.ctc_bot_key] [--url wss://cornelltradingcompetition.org]

The bot key is read from --key-file (default ~/.ctc_bot_key) or the CTC_BOT_KEY environment variable. It is never printed.
Each output line is the frame as received plus "received_at" (unix seconds). Reconnects with backoff; the exchange does
not resend old news, so gaps show up as missing seq numbers in check_recording.py.

Needs the websockets library (the starter kit asks for websockets>=14).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time

import websockets

SYMBOLS = ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "RECON", "MAT", "GEN", "HAVEN", "REIN", "ARCH", "REBLD"]
CHANNELS = ["news", "quotes", "trades", "orderbook"]
USER_AGENT = "ctc2026-recorder/1.0"


def read_key(path: str) -> str:
    key = os.environ.get("CTC_BOT_KEY", "").strip()
    if key:
        return key
    path = os.path.expanduser(path)
    try:
        with open(path) as f:
            key = f.read().strip()
    except OSError:
        sys.exit(f"no bot key: set CTC_BOT_KEY or put the key in {path}")
    if not key:
        sys.exit(f"{path} is empty")
    return key


async def record(url: str, key: str, out: str) -> None:
    subscribe = {"action": "subscribe", "symbols": SYMBOLS, "channels": CHANNELS}
    headers = {"X-API-Key": key}  # User-Agent goes through user_agent_header below
    backoff, frames, started = 1.0, 0, time.time()
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "a") as f:
        while True:
            try:
                async with websockets.connect(f"{url.rstrip('/')}/ws/v1/market-data", additional_headers=headers,
                                              user_agent_header=USER_AGENT, max_size=None) as ws:
                    await ws.send(json.dumps(subscribe))  # the one subscription message; later ones are ignored
                    print(f"connected; subscribed to {len(SYMBOLS)} symbols x {CHANNELS}", flush=True)
                    backoff = 1.0
                    last_flush = time.time()
                    async for frame in ws:
                        now = time.time()
                        f.write(json.dumps({**json.loads(frame), "received_at": now}) + "\n")
                        frames += 1
                        if now - last_flush >= 2.0:  # keep the file current even when the feed is quiet
                            f.flush()
                            last_flush = now
                        if frames % 2000 == 0:
                            print(f"{frames} frames in {now - started:.0f} s", flush=True)
            except (OSError, websockets.WebSocketException) as exc:
                print(f"disconnected ({type(exc).__name__}: {exc}); retry in {backoff:.0f} s", flush=True)
            f.flush()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="wss://cornelltradingcompetition.org")
    p.add_argument("--key-file", default="~/.ctc_bot_key")
    p.add_argument("--out", default="recordings/season.jsonl")
    a = p.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(record(a.url, read_key(a.key_file), a.out))


if __name__ == "__main__":
    main()
