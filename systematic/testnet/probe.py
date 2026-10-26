"""Read-only testnet probe: clock/state, the testnet case data (diffed against the research case.json), positions and one
order book. Places no orders.

    python3 -I probe.py [--key-file ~/.ctc_bot_key] [--url https://cornelltradingcompetition.org]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
USER_AGENT = "ctc2026-probe/1.0"  # the exchange refuses urllib's default user agent


def read_key(path: str) -> str:
    key = os.environ.get("CTC_BOT_KEY", "").strip()
    if not key:
        try:
            key = open(os.path.expanduser(path)).read().strip()
        except OSError:
            sys.exit(f"no bot key: set CTC_BOT_KEY or put the key in {path}")
    return key


def get(base: str, path: str, key: str):
    req = urllib.request.Request(base + path, headers={"User-Agent": USER_AGENT, "X-API-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:300]


def diff(a, b, path=""):
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: only in {'testnet' if k in a else 'research'}")
            else:
                out += diff(a[k], b[k], f"{path}/{k}")
    elif a != b:
        out.append(f"{path}: testnet={json.dumps(a)[:120]} research={json.dumps(b)[:120]}")
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="https://cornelltradingcompetition.org")
    p.add_argument("--key-file", default="~/.ctc_bot_key")
    a = p.parse_args()
    base, key = a.url.rstrip("/"), read_key(a.key_file)

    code, t = get(base, "/api/v1/competitions/time", key)
    print("clock/state:", code, json.dumps(t)[:300])

    code, case = get(base, "/cases/hurricane.json", key)
    print("case data:", code)
    if code == 200:
        research = json.load(open(os.path.join(HERE, "..", "data", "case.json")))
        d = diff(case, research)
        print("  testnet case data identical to the research case.json" if not d else f"  {len(d)} differences:")
        for line in d[:40]:
            print("   ", line)

    code, pos = get(base, "/api/v1/positions", key)
    print("positions:", code, json.dumps(pos)[:300])
    code, book = get(base, "/api/v1/orderbook/HAVEN", key)
    print("HAVEN book:", code, json.dumps(book)[:600])


if __name__ == "__main__":
    main()
