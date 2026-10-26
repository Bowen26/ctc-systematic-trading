"""1-lot order tests on the practice testnet (TESTNET_FINDINGS §7, T1-T3). INTERACTIVE: each batch shows its exact
orders and sends nothing until you type SEND.

    ../.venv/bin/python -I order_test.py [--host cornelltradingcompetition.org] [--key-file ~/.ctc_bot_key]
                                         [--batch T1 T2 T3]

Batches (1 share per order, at most 30 order actions per run, symbols PORT, HAVEN, ARCH only):
  T1 order lifecycle (PORT): rest a 1-share buy 2% below the bid; list open orders; cancel it by id; rest another;
     cancel-all by symbol; list again.                                                              4 actions
  T2 round trips (PORT, HAVEN, ARCH): buy 1 at the ask, then sell 1 at the bid; time until /positions shows it.
                                                                                                    6 actions
  T3 price protection (PORT): buy 1 at 1%, 3% and 10% through the ask (each sold back at the bid), then a
     1-share market buy and market sell.                                                         up to 8 actions
Stops at once on HTTP 429, on two 5xx answers, on an unexpected status, or if the estimated cost passes $100.
Refuses to start if this key holds positions or open orders in the test symbols, or if no round is running.

Writes order_tests/<time>/log.jsonl (every request and answer, no key) and summary.md, then checks that the bot's own
parsers (ctcbot.rest) understand every answer the exchange gave. That last check is the point of the exercise.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BOT = os.path.join(os.path.dirname(HERE), "bot")
UA = "ctc2026-order-test/1.0"
SYMBOLS = ("PORT", "HAVEN", "ARCH")
MAX_ACTIONS = 30
MAX_COST_USD = 100.0
OK_STATUSES = {200, 201, 202, 204, 400, 404, 409, 422}


class Stop(RuntimeError):
    pass


def read_key(path: str) -> str:
    key = open(os.path.expanduser(path)).read().strip()
    if not key or any(c.isspace() for c in key):
        raise SystemExit(f"{path}: expected one token")
    return key


class Exchange:
    """Thin REST client. Every call is logged without the key; order actions are counted against MAX_ACTIONS."""

    def __init__(self, host: str, key: str, log_path: str, opener=None) -> None:
        self.base = f"https://{host}"
        self.key = key
        self.log_path = log_path
        self.records: list[dict] = []
        self.actions = 0
        self.n5xx = 0
        self.cost_usd = 0.0
        self._open = opener or urllib.request.build_opener(urllib.request.ProxyHandler({})).open

    def call(self, method: str, path: str, body: dict | None = None, step: str = "", action: bool = False) -> dict:
        if action:
            if self.actions >= MAX_ACTIONS:
                raise Stop(f"order-action budget of {MAX_ACTIONS} reached")
            self.actions += 1
        url = self.base + ("" if path.startswith("/cases/") else "/api/v1") + path
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"User-Agent": UA, "Accept": "application/json",
                                              **({"Content-Type": "application/json"} if data else {})})
        req.add_unredirected_header("X-API-Key", self.key)
        t0 = time.perf_counter()
        status, text = None, ""
        try:
            with self._open(req, timeout=10) as r:
                status = int(getattr(r, "status", None) or r.getcode())
                text = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status = int(e.code)
            text = (e.read() or b"").decode("utf-8", "replace")
        except OSError as e:
            text = f"{type(e).__name__}: {e}"
        ms = (time.perf_counter() - t0) * 1000.0
        try:
            js = json.loads(text) if text else None
        except ValueError:
            js = None
        rec = {"t_wall": time.time(), "step": step, "method": method, "path": path, "body": body, "status": status,
               "latency_ms": round(ms, 1), "json": js, "text": None if js is not None else text[:500]}
        self.records.append(rec)
        with open(self.log_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        print(f"    {method:6s} {path:32s} -> {status} in {ms:6.0f} ms  {json.dumps(js)[:160] if js is not None else text[:160]}")
        if status == 429:
            raise Stop("HTTP 429: rate limited")
        if status is not None and status >= 500:
            self.n5xx += 1
            if self.n5xx >= 2:
                raise Stop("two 5xx answers")
        elif status is None or status not in OK_STATUSES:
            raise Stop(f"unexpected answer {status}")
        return rec

    # convenience
    def get(self, path: str, step: str = "") -> dict:
        return self.call("GET", path, step=step)

    def place(self, sym: str, side: str, price: float | None, step: str) -> dict:
        body = {"symbol": sym, "side": side, "order_type": "limit" if price is not None else "market", "quantity": 1}
        if price is not None:
            body["price"] = price
        return self.call("POST", "/orders", body, step=step, action=True)


def book(ex: Exchange, sym: str) -> tuple[float, float]:
    js = ex.get(f"/orderbook/{sym}", step="book")["json"] or {}
    bids, asks = js.get("bids") or [], js.get("asks") or []
    if not bids or not asks:
        raise Stop(f"{sym}: empty book")
    return float(bids[0]["price"]), float(asks[0]["price"])


def position(ex: Exchange, sym: str, step: str) -> int:
    js = ex.get("/positions", step=step)["json"]
    rows = js.get("positions", js) if isinstance(js, dict) else js
    for r in rows or []:
        if isinstance(r, dict) and r.get("symbol") == sym:
            for k in ("quantity", "qty", "position", "net"):
                if isinstance(r.get(k), (int, float)):
                    return int(r[k])
    return 0


def wait_position(ex: Exchange, sym: str, want: int, step: str, limit_s: float = 3.0) -> float | None:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < limit_s:
        if position(ex, sym, step) == want:
            return round((time.perf_counter() - t0) * 1000.0, 1)
        time.sleep(0.25)
    return None


def order_id(rec: dict) -> str | None:
    js = rec.get("json")
    if isinstance(js, dict):
        d = js.get("order") if isinstance(js.get("order"), dict) else js
        oid = d.get("order_id", d.get("id"))
        return None if oid is None else str(oid)
    return None


def confirm(title: str, plan: list[str]) -> bool:
    print(f"\n=== {title}")
    for line in plan:
        print("   ", line)
    ans = input("Type SEND to send this batch (anything else skips it): ").strip()
    return ans == "SEND"


def round_clock(ex: Exchange, step: str) -> float:
    """Real seconds into the running round; raises Stop when no round is running."""
    t = ex.get("/competitions/time", step=step)["json"] or {}
    if t.get("state") != "running":
        raise Stop(f"competition state is {t.get('state')!r}, not running: rerun when a round is running")
    ft = str(t.get("fictional_time", ""))
    try:
        hh, mm = int(ft[11:13]), int(ft[14:16])
        return ((hh * 60 + mm) - (9 * 60 + 30)) * 60 / 30.0
    except ValueError:
        return -1.0


def fresh_window(ex: Exchange) -> None:
    """Called after SEND, before the first order: the round must still be running and between 5 and 700 s."""
    real_s = round_clock(ex, "fresh time")
    if not (5 <= real_s <= 700):
        raise Stop(f"{real_s:.0f} s into the round now: too close to the open or the close. Nothing was sent for "
                   "this batch; rerun when a round is between 5 and 700 s")


def round_px(px: float, up: bool) -> float:
    return (math.ceil if up else math.floor)(px * 100 - 1e-9) / 100.0


def t1(ex: Exchange) -> None:
    bid, ask = book(ex, "PORT")
    px = round_px(bid * 0.98, up=False)
    plan = [f"PORT BUY 1 limit {px} (2% below the bid {bid}: rests, should not fill)", "GET /orders/open",
            "DELETE that order by id", "GET /orders/open",
            f"PORT BUY 1 limit {px} again", "DELETE /orders/all?symbol=PORT", "GET /orders/open"]
    if not confirm("T1 order lifecycle (4 order actions)", plan):
        return
    fresh_window(ex)
    bid2, _ = book(ex, "PORT")                       # prices may have moved while the prompt waited
    px2 = round_px(bid2 * 0.98, up=False)
    if px2 != px:
        print(f"    fresh bid {bid2}: resting price now {px2} (was {px})")
    px = px2
    r = ex.place("PORT", "buy", px, "T1 rest 1")
    oid = order_id(r)
    ex.get("/orders/open", step="T1 open after rest")
    if oid:
        ex.call("DELETE", f"/orders/{oid}", step="T1 cancel by id", action=True)
    else:
        print("    no order id in the answer: cancelling by symbol instead")
    ex.get("/orders/open", step="T1 open after cancel")
    ex.place("PORT", "buy", px, "T1 rest 2")
    ex.call("DELETE", "/orders/all?symbol=PORT", step="T1 cancel-all symbol", action=True)
    ex.get("/orders/open", step="T1 open after cancel-all")


def t2(ex: Exchange) -> None:
    quotes = {s: book(ex, s) for s in SYMBOLS}
    plan = []
    for s, (bid, ask) in quotes.items():
        plan += [f"{s} BUY 1 limit {ask} (the ask), wait for /positions = +1", f"{s} SELL 1 limit at the bid then, wait for 0"]
    est = sum(a - b for b, a in quotes.values())
    plan.append(f"estimated cost: about ${est:.2f} (the spreads)")
    if not confirm("T2 round trips (6 order actions)", plan):
        return
    fresh_window(ex)
    for s in SYMBOLS:
        bid, ask = book(ex, s)
        ex.place(s, "buy", ask, f"T2 {s} buy")
        t_in = wait_position(ex, s, 1, f"T2 {s} pos")
        print(f"    {s}: long after {t_in} ms" if t_in is not None else f"    {s}: buy not filled within 3 s")
        bid2, _ = book(ex, s)
        ex.place(s, "sell", bid2, f"T2 {s} sell")
        t_out = wait_position(ex, s, 0, f"T2 {s} pos")
        print(f"    {s}: flat after {t_out} ms" if t_out is not None else f"    {s}: NOT flat within 3 s")
        ex.cost_usd += max(0.0, ask - bid2)
        if ex.cost_usd > MAX_COST_USD:
            raise Stop("cost limit")
        if t_out is None:
            raise Stop(f"{s} not flat: check the browser and close it by hand")


def t3(ex: Exchange) -> None:
    bid, ask = book(ex, "PORT")
    steps = [(1.01, "1%"), (1.03, "3%"), (1.10, "10%")]
    plan = [f"PORT BUY 1 limit {round_px(ask * m, up=True)} ({lab} through the ask {ask}); sell 1 back at the bid"
            for m, lab in steps]
    plan += ["PORT BUY 1 market; then PORT SELL 1 market", "(records whether each is filled, capped or rejected)"]
    if not confirm("T3 price protection (up to 8 order actions)", plan):
        return
    fresh_window(ex)
    for m, lab in steps:
        bid, ask = book(ex, "PORT")
        r = ex.place("PORT", "buy", round_px(ask * m, up=True), f"T3 buy {lab} through")
        got = wait_position(ex, "PORT", 1, f"T3 pos {lab}")
        print(f"    {lab} through: answer {r['status']}, long: {got is not None}")
        if got is not None:
            bid2, _ = book(ex, "PORT")
            ex.place("PORT", "sell", bid2, f"T3 sell back {lab}")
            if wait_position(ex, "PORT", 0, f"T3 flat {lab}") is None:
                raise Stop("PORT not flat after T3: check the browser and close it by hand")
            ex.cost_usd += max(0.0, ask - bid2)
        else:
            ex.call("DELETE", "/orders/all?symbol=PORT", step=f"T3 cleanup {lab}", action=True)
    ex.place("PORT", "buy", None, "T3 market buy")
    if wait_position(ex, "PORT", 1, "T3 market pos") is not None:
        ex.place("PORT", "sell", None, "T3 market sell")
        if wait_position(ex, "PORT", 0, "T3 market flat") is None:
            raise Stop("PORT not flat after the market round trip: check the browser")


def preconditions(ex: Exchange) -> None:
    real_s = round_clock(ex, "pre time")
    print(f"    round clock: {real_s:.0f} real s into the round")
    if not (5 <= real_s <= 700):
        raise Stop("too close to the open or the close of a round: run it between 5 s and 700 s into a round")
    js = ex.get("/positions", step="pre positions")["json"]
    rows = js.get("positions", js) if isinstance(js, dict) else js
    held = [r for r in rows or [] if isinstance(r, dict) and r.get("symbol") in SYMBOLS
            and any(r.get(k) for k in ("quantity", "qty", "position", "net"))]
    if held:
        raise Stop(f"this key already holds {held}: is someone (or the bot) trading on it? Stop them first")
    oo = ex.get("/orders/open", step="pre open orders")["json"]
    rows = oo.get("orders", oo) if isinstance(oo, dict) else oo
    if any(isinstance(r, dict) and r.get("symbol") in SYMBOLS for r in rows or []):
        raise Stop("open orders exist in the test symbols on this key: stop whoever placed them first")


def parser_check(records: list[dict]) -> list[str]:
    """Run the bot's own parsers on every answer the exchange gave."""
    sys.path.insert(0, BOT)
    from ctcbot import rest as R  # noqa: E402
    out = []
    for rec in records:
        js, path, m = rec.get("json"), rec["path"], rec["method"]
        if js is None:
            continue
        try:
            if m == "POST" and path == "/orders":
                ack = R.parse_order_ack(js)
                ok = ack.order_id is not None
                out.append(f"{'PASS' if ok else 'FAIL'} order ack ({rec['step']}): id={ack.order_id} status={ack.status} "
                           f"filled={ack.filled_qty} remaining={ack.remaining} avg={ack.avg_fill_price}")
            elif path == "/orders/open":
                rows = R.parse_open_orders(js)
                out.append(f"PASS open orders ({rec['step']}): {len(rows)} parsed")
            elif path == "/positions":
                pos = R.parse_positions(js)
                out.append(f"PASS positions ({rec['step']}): {{{', '.join(f'{k}:{v.qty}' for k, v in pos.items())}}}")
            elif path == "/competitions/time":
                st = R.parse_time(js)
                out.append(f"{'PASS' if st.state else 'FAIL'} time: state={st.state} comp={st.comp_id}")
            elif path.startswith("/orderbook/"):
                bids, asks = R.parse_book(js)
                out.append(f"{'PASS' if bids or asks else 'FAIL'} book {path}: {len(bids)} bids, {len(asks)} asks")
        except Exception as e:  # noqa: BLE001 - a parser crash is exactly what we are looking for
            out.append(f"FAIL {m} {path} ({rec['step']}): {type(e).__name__}: {e}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="cornelltradingcompetition.org")
    ap.add_argument("--key-file", default="~/.ctc_bot_key")
    ap.add_argument("--batch", nargs="+", default=["T1", "T2", "T3"], choices=["T1", "T2", "T3"])
    a = ap.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit("run this in a terminal: every batch needs a typed SEND")
    out = os.path.join(HERE, "order_tests", dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out, exist_ok=True)
    ex = Exchange(a.host, read_key(a.key_file), os.path.join(out, "log.jsonl"))
    print(f"1-lot order tests on {a.host}; log in {out}")
    stopped = None
    try:
        preconditions(ex)
        for b in a.batch:
            {"T1": t1, "T2": t2, "T3": t3}[b](ex)
    except Stop as e:
        stopped = str(e)
        print(f"\nSTOPPED: {stopped}")
    except KeyboardInterrupt:
        stopped = "interrupted"
    finally:
        try:
            ex.call("GET", "/orders/open", step="final open orders")
            ex.call("GET", "/positions", step="final positions")
        except Stop:
            pass
    checks = parser_check(ex.records)
    lat = sorted(r["latency_ms"] for r in ex.records if r["method"] in ("POST", "DELETE") and r["status"])
    lines = [f"# 1-lot order test, {a.host}, {dt.datetime.now():%Y-%m-%d %H:%M}", "",
             f"- order actions: {ex.actions}; estimated cost ${ex.cost_usd:.2f}; stopped: {stopped or 'no'}",
             f"- order-action latency (ms): " + (f"median {lat[len(lat) // 2]}, max {lat[-1]}" if lat else "n/a"),
             "", "## Bot parser check", ""] + [f"- {c}" for c in checks]
    open(os.path.join(out, "summary.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(["", *lines]))


if __name__ == "__main__":
    main()
