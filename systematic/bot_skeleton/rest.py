"""REST client (stdlib http.client, keep-alive, thread pool).

*** UNTESTED SKELETON. Has never sent a request. Response field names are GUESSES until the
    testnet shows them; every parser below is tolerant and logs unknown shapes. ***

Rules (packet section 7/8):
  * X-API-Key header with the bot key; a custom User-Agent on EVERY request (the network in front
    of the exchange refuses urllib's default).
  * Endpoints (prefix /api/v1): POST /orders, DELETE /orders/{id}, DELETE /orders/all[?symbol=],
    GET /orders/open, GET /positions, GET /orderbook/{symbol}, GET /competitions/time,
    POST /etf/{symbol}/create, POST /etf/{symbol}/redeem (body {"quantity": n}).
    Case data: GET /cases/hurricane.json (no /api/v1 prefix).
  * Order actions are NEVER retried automatically: a timeout leaves the order state UNKNOWN and
    the order manager reconciles with GET /orders/open. GETs may retry.
  * Every order-action method requires a ratelimit.Ticket, so nothing can bypass the budget.
"""
import asyncio
import http.client
import json
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import ratelimit

NETWORK_ALLOWED = False   # main.py flips this only with --network and the review acknowledgement


class RestResult:
    __slots__ = ("status", "data", "latency", "error")

    def __init__(self, status, data, latency, error=None):
        self.status, self.data, self.latency, self.error = status, data, latency, error

    @property
    def ok(self):
        return self.status is not None and 200 <= self.status < 300

    def __repr__(self):
        return f"RestResult({self.status}, {str(self.data)[:120]}, {self.latency*1000:.0f}ms, {self.error})"


def g(d, *keys, default=None):
    """Tolerant getter: first present key wins."""
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


class RestClient:
    def __init__(self, host, api_prefix, key, user_agent, timeout, get_timeout, req_limiter, dry_run=True, log=print):
        self.host, self.prefix, self.key, self.ua = host, api_prefix, key, user_agent
        self.timeout, self.get_timeout = timeout, get_timeout
        self.req_limiter, self.dry_run, self.log = req_limiter, dry_run, log
        self._local = threading.local()
        self._rl_lock = threading.Lock()
        self._dry_id = 0

    # -- transport -------------------------------------------------------------------------
    def _conn(self, timeout):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = http.client.HTTPSConnection(self.host, timeout=timeout)
            self._local.conn = c
        c.timeout = timeout
        return c

    def _request(self, method, path, body=None, api=True, timeout=None, retries=0):
        if not NETWORK_ALLOWED:
            raise RuntimeError("network disabled (skeleton safety gate)")
        url = (self.prefix if api else "") + path
        hdrs = {"User-Agent": self.ua, "X-API-Key": self.key, "Accept": "application/json",
                "Connection": "keep-alive"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        timeout = timeout or self.timeout
        for attempt in range(retries + 1):
            while True:
                with self._rl_lock:
                    wait = self.req_limiter.wait_needed()
                if wait <= 0:
                    break
                time.sleep(wait)
            t0 = time.monotonic()
            try:
                c = self._conn(timeout)
                c.request(method, url, body=data, headers=hdrs)
                r = c.getresponse()
                raw = r.read()
                lat = time.monotonic() - t0
                try:
                    parsed = json.loads(raw) if raw else None
                except ValueError:
                    parsed = raw.decode("utf-8", "replace")[:500]
                return RestResult(r.status, parsed, lat)
            except (OSError, http.client.HTTPException) as exc:
                self._local.conn = None          # drop the broken keep-alive connection
                if attempt >= retries:
                    return RestResult(None, None, time.monotonic() - t0, repr(exc))
        return RestResult(None, None, 0.0, "unreachable")

    # -- reads (not order actions) --------------------------------------------------------------
    def get_case(self):
        return self._request("GET", "/cases/hurricane.json", api=False, timeout=self.get_timeout, retries=2)

    def get_time(self):
        return self._request("GET", "/competitions/time", timeout=self.get_timeout, retries=1)

    def get_positions(self):
        return self._request("GET", "/positions", timeout=self.get_timeout, retries=1)

    def get_open_orders(self):
        return self._request("GET", "/orders/open", timeout=self.get_timeout, retries=1)

    def get_orderbook(self, symbol):
        return self._request("GET", "/orderbook/" + urllib.parse.quote(symbol), timeout=self.get_timeout)

    # -- order actions (each consumes one Ticket) ---------------------------------------------
    @staticmethod
    def _check(ticket):
        if not isinstance(ticket, ratelimit.Ticket):
            raise RuntimeError("order action without a rate-limit ticket")
        ticket.consume()

    def _dry(self, what, body=None):
        self._dry_id += 1
        self.log(f"DRY-RUN {what} {body or ''}")
        return RestResult(200, {"order_id": f"dry-{self._dry_id}", "status": "dry_run"}, 0.0)

    def place_order(self, ticket, symbol, side, quantity, order_type="limit", price=None):
        self._check(ticket)
        body = {"symbol": symbol, "side": side, "order_type": order_type, "quantity": int(quantity)}
        if order_type == "limit":
            body["price"] = round(float(price), 6)
        if self.dry_run:
            return self._dry("POST /orders", body)
        return self._request("POST", "/orders", body)

    def cancel_order(self, ticket, order_id):
        self._check(ticket)
        if self.dry_run:
            return self._dry(f"DELETE /orders/{order_id}")
        return self._request("DELETE", "/orders/" + urllib.parse.quote(str(order_id)))

    def cancel_all(self, ticket, symbol=None):
        self._check(ticket)
        path = "/orders/all" + (("?symbol=" + urllib.parse.quote(symbol)) if symbol else "")
        if self.dry_run:
            return self._dry("DELETE " + path)
        return self._request("DELETE", path)

    def etf_create(self, ticket, symbol, quantity):
        self._check(ticket)
        if self.dry_run:
            return self._dry(f"POST /etf/{symbol}/create", {"quantity": quantity})
        return self._request("POST", f"/etf/{symbol}/create", {"quantity": int(quantity)})

    def etf_redeem(self, ticket, symbol, quantity):
        self._check(ticket)
        if self.dry_run:
            return self._dry(f"POST /etf/{symbol}/redeem", {"quantity": quantity})
        return self._request("POST", f"/etf/{symbol}/redeem", {"quantity": int(quantity)})


class AsyncRest:
    """Runs RestClient calls on a thread pool so the asyncio loop never blocks."""

    def __init__(self, client, workers):
        self.client = client
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rest")

    async def call(self, fn_name, *args, **kw):
        loop = asyncio.get_running_loop()
        fn = getattr(self.client, fn_name)
        return await loop.run_in_executor(self.pool, lambda: fn(*args, **kw))

    async def warm(self, n):
        """Open n keep-alive connections before a scheduled snipe (GET /competitions/time)."""
        await asyncio.gather(*(self.call("get_time") for _ in range(n)), return_exceptions=True)


# -- tolerant parsers for response shapes we have not seen yet ----------------------------------
def parse_positions(data):
    """-> {symbol: {"qty": int, "mark": float|None, "avg": float|None, "upnl": float|None}}"""
    rows = data
    if isinstance(data, dict):
        rows = g(data, "positions", "data", default=data)
    out = {}
    if isinstance(rows, dict):
        rows = [dict(v, symbol=k) if isinstance(v, dict) else {"symbol": k, "quantity": v} for k, v in rows.items()]
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        s = g(r, "symbol", "ticker")
        if s is None:
            continue
        out[s] = {"qty": int(g(r, "quantity", "position", "qty", "net", default=0)),
                  "mark": g(r, "mark", "mark_price", "price"),
                  "avg": g(r, "avg_price", "average_price", "cost_basis"),
                  "upnl": g(r, "unrealized_pnl", "unrealized", "upnl"),
                  "rpnl": g(r, "realized_pnl", "realized")}
    return out


def parse_open_orders(data):
    """-> list of {"id", "symbol", "side", "price", "remaining"}"""
    rows = g(data, "orders", "data", default=data) if isinstance(data, dict) else data
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        qty = g(r, "remaining_quantity", "remaining", "leaves_quantity")
        if qty is None:
            qty = int(g(r, "quantity", "qty", default=0)) - int(g(r, "filled_quantity", "filled", default=0))
        out.append({"id": str(g(r, "order_id", "id")), "symbol": g(r, "symbol"), "side": g(r, "side"),
                    "price": g(r, "price"), "remaining": int(qty)})
    return out


def parse_order_ack(data):
    """-> (order_id|None, status|None, filled_qty|None)"""
    oid = g(data, "order_id", "id")
    return (str(oid) if oid is not None else None, g(data, "status"), g(data, "filled_quantity", "filled"))
