"""Group A tests: ctcbot.rest (live gate, host allow-list, retries, executor, parsers, redaction) — §9.1 A rest.

Everything runs against fake transports and fake connections; no test opens a network socket
(``socket.create_connection`` is patched to fail in the gate test, and the connection tests use socketpairs).
"""
import copy
import json
import os
import pickle
import socket
import sys
import threading
import time
import unittest
from unittest import mock

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402,F401
from ctcbot import rest  # noqa: E402
from ctcbot.config import Config, config_hash, load_config  # noqa: E402
from ctcbot.rest import (HttpsTransport, LiveToken, NotSentError, RestClient, RestExecutor, parse_book,  # noqa: E402
                         parse_open_orders, parse_order_ack, parse_positions, parse_time, round_price)
from ctcbot.timesource import RealTime  # noqa: E402
from ctcbot.types import (ApiKind, ApiRequest, ApiResult, BookLevel, ListLog, OpenOrderRow, OrderAck,  # noqa: E402
                          PositionRow, Priority, ServerTime, Side)

KEY = "k3y-SECRET-0123456789abcdef"
ISSUE = getattr(rest, "_" + "ISSUE")
SETS = ["exchange.rest_base=https://ex.test/api/v1", "exchange.ws_url=wss://ex.test/ws/v1/market-data",
        "exchange.case_url=https://cdn.test/cases/hurricane.json"]


def cfg_with(*extra):
    return load_config(sets=SETS + list(extra))


def raw_cfg(**exchange):
    """A Config that bypasses validation (to test the client's own URL checks)."""
    d = copy.deepcopy(cfg_with().d)
    d["exchange"].update(exchange)
    return Config(d=d, hash=config_hash(d), root=BOT, sources=(), live_allowed=True)


class FakeTransport:
    """Records calls; answers from a script of (status, body) tuples, exceptions or callables."""

    def __init__(self, *script, default=(200, b"{}")):
        self.calls = []
        self.resets = 0
        self.script = list(script)
        self.default = default
        self.lock = threading.Lock()

    def __call__(self, method, url, headers, body, timeout):
        with self.lock:
            self.calls.append((method, url, dict(headers), body, timeout))
            item = self.script.pop(0) if self.script else self.default
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item(method, url, headers, body, timeout)
        return item

    def reset(self):
        self.resets += 1


def req(kind, n=1, **kw):
    return ApiRequest(req_id=n, kind=kind, **kw)


def place(n=1, side=Side.BUY, qty=5, price=45.0, symbol="PORT", **kw):
    return ApiRequest(req_id=n, kind=ApiKind.PLACE, symbol=symbol, side=side, qty=qty, price=price, **kw)


def client(transport, live=True, cfg=None, key=KEY, log=None):
    cfg = cfg or cfg_with()
    tok = LiveToken("ex.test", cfg.hash, ISSUE) if live else None
    return RestClient(cfg, key, RealTime(), log or ListLog(), live=tok, transport=transport)


class TestGateAndHeaders(unittest.TestCase):
    def test_headers_and_key_scope(self):
        tr = FakeTransport()
        c = client(tr)
        self.assertTrue(c.live)
        c.execute(req(ApiKind.GET_TIME))
        c.execute(req(ApiKind.GET_CASE))
        c.execute(place())
        (m1, u1, h1, b1, t1), (m2, u2, h2, b2, _), (m3, u3, h3, b3, t3) = tr.calls
        ua = c.cfg.get("exchange.user_agent")
        self.assertEqual((m1, u1), ("GET", "https://ex.test/api/v1/competitions/time"))
        self.assertEqual((h1["User-Agent"], h1["Accept"], h1["X-API-Key"]), (ua, "application/json", KEY))
        self.assertNotIn("Content-Type", h1)
        self.assertEqual(t1, 3.0)
        self.assertEqual(u2, "https://cdn.test/cases/hurricane.json")
        self.assertNotIn("X-API-Key", h2)
        self.assertEqual(h2["User-Agent"], ua)
        self.assertEqual((m3, u3, h3["Content-Type"], h3["X-API-Key"], t3),
                         ("POST", "https://ex.test/api/v1/orders", "application/json", KEY, 2.0))
        self.assertEqual(json.loads(b3), {"symbol": "PORT", "side": "buy", "order_type": "limit", "quantity": 5,
                                          "price": 45.0})
        tr2 = FakeTransport()
        client(tr2, cfg=cfg_with("exchange.case_url=https://ex.test/cases/hurricane.json")).execute(
            req(ApiKind.GET_CASE))
        self.assertEqual(tr2.calls[0][2]["X-API-Key"], KEY)

    def test_paths(self):
        tr = FakeTransport()
        c = client(tr)
        c.execute(req(ApiKind.CANCEL, order_id="a/b c"))
        c.execute(req(ApiKind.CANCEL_ALL))
        c.execute(req(ApiKind.CANCEL_ALL, symbol="PORT"))
        c.execute(req(ApiKind.ETF_CREATE, symbol="ARCH", qty=2))
        c.execute(req(ApiKind.ETF_REDEEM, symbol="REBLD", qty=1))
        c.execute(req(ApiKind.GET_OPEN))
        c.execute(req(ApiKind.GET_POSITIONS))
        c.execute(req(ApiKind.GET_BOOK, symbol="HAVEN"))
        got = [(m, u.replace("https://ex.test/api/v1", "")) for m, u, *_ in tr.calls]
        self.assertEqual(got, [("DELETE", "/orders/a%2Fb%20c"), ("DELETE", "/orders/all"),
                               ("DELETE", "/orders/all?symbol=PORT"), ("POST", "/etf/ARCH/create"),
                               ("POST", "/etf/REBLD/redeem"), ("GET", "/orders/open"), ("GET", "/positions"),
                               ("GET", "/orderbook/HAVEN")])
        self.assertEqual(json.loads(tr.calls[3][3]), {"quantity": 2})

    def test_non_https_raises_before_io(self):
        with self.assertRaises(ValueError):
            RestClient(raw_cfg(rest_base="http://ex.test/api/v1"), KEY, RealTime(), ListLog(),
                       transport=FakeTransport())
        tr = FakeTransport()
        c = RestClient(raw_cfg(case_url="http://cdn.test/cases/hurricane.json"), KEY, RealTime(), ListLog(),
                       transport=tr)
        with self.assertRaises(ValueError):
            c.execute(req(ApiKind.GET_CASE))
        self.assertEqual(tr.calls, [])
        with self.assertRaises(ValueError):
            HttpsTransport(conn_factory=lambda *a: None)("GET", "http://ex.test/x", {}, None, 1.0)

    def test_dry_run_without_token(self):
        tr = FakeTransport(default=(200, b"[]"))
        log = ListLog()
        c = client(tr, live=False, log=log)
        self.assertFalse(c.live)
        r1 = c.execute(place(qty=7, price=45.129))
        r2 = c.execute(req(ApiKind.CANCEL, n=2, order_id="x1"))
        r3 = c.execute(req(ApiKind.CANCEL_ALL, n=3, priority=Priority.KILL))
        r4 = c.execute(req(ApiKind.ETF_CREATE, n=4, symbol="ARCH", qty=1))
        self.assertEqual(tr.calls, [])
        self.assertEqual((r1.status, r1.dry_run, r1.sent, r1.ok, r1.outcome_unknown), (200, True, False, True, False))
        self.assertEqual(r1.data, {"order_id": "dry-1", "status": "dry_run"})
        self.assertEqual(r1.parsed, OrderAck("dry-1", "dry_run", 0, 7, None))
        self.assertEqual([r.data["order_id"] for r in (r2, r3, r4)], ["dry-2", "dry-3", "dry-4"])
        dry = log.kinds("dry_order")
        self.assertEqual(len(dry), 4)
        self.assertEqual(dry[0]["body"], {"symbol": "PORT", "side": "buy", "order_type": "limit", "quantity": 7,
                                          "price": 45.12})
        self.assertEqual(dry[2]["path"], "/orders/all")
        g = c.execute(req(ApiKind.GET_OPEN, n=5))
        self.assertEqual(len(tr.calls), 1)
        self.assertEqual((g.status, g.dry_run, g.sent, g.parsed), (200, False, True, []))

    def test_token_for_other_host_or_config(self):
        cfg = cfg_with()
        for tok in (LiveToken("other.test", cfg.hash, ISSUE), LiveToken("ex.test", "ffffffffffff", ISSUE),
                    LiveToken("ex.test", cfg.hash[:3], ISSUE)):
            tr = FakeTransport()
            log = ListLog()
            c = RestClient(cfg, KEY, RealTime(), log, live=tok, transport=tr)
            self.assertFalse(c.live, tok)
            self.assertTrue(c.execute(place()).dry_run)
            self.assertEqual(tr.calls, [])
            self.assertTrue(any(f.get("alert") == "live_token_rejected" for _, f in log.events))
        for tok in (LiveToken("EX.test", cfg.hash, ISSUE), LiveToken("ex.test:443", cfg.hash[:6], ISSUE)):
            tr = FakeTransport()
            c = RestClient(cfg, KEY, RealTime(), ListLog(), live=tok, transport=tr)
            self.assertTrue(c.live)
            self.assertFalse(c.execute(place()).dry_run)
            self.assertEqual(len(tr.calls), 1)

    def test_live_token_needs_issuer(self):
        with self.assertRaises(TypeError):
            LiveToken("ex.test", "abc123", object())
        with self.assertRaises(TypeError):
            LiveToken("ex.test", "abc123", None)
        tok = LiveToken("ex.test", "abc123def456", ISSUE)
        with self.assertRaises(AttributeError):
            tok.host = "evil.test"
        with self.assertRaises(TypeError):
            copy.copy(tok)
        with self.assertRaises(TypeError):
            pickle.dumps(tok)
        self.assertEqual(repr(tok), "LiveToken(host='ex.test', cfg_hash='abc123')")

    def test_issue_referenced_only_in_rest_and_cli(self):
        hits = []
        for root, dirs, files in os.walk(BOT):
            rel_root = os.path.relpath(root, BOT)
            if rel_root.split(os.sep)[0] in ("tests", "runs", "__pycache__") or "__pycache__" in root:
                continue
            for name in files:
                if name.endswith(".py"):
                    path = os.path.join(root, name)
                    with open(path, encoding="utf-8") as f:
                        if "_ISSUE" in f.read():
                            hits.append(os.path.relpath(path, BOT))
        self.assertIn(os.path.join("ctcbot", "rest.py"), hits)
        self.assertLessEqual(set(hits), {os.path.join("ctcbot", "rest.py"), os.path.join("ctcbot", "app", "cli.py")},
                             hits)

    def test_market_orders_and_invalid_requests(self):
        tr = FakeTransport()
        c = client(tr)
        r = c.execute(place(order_type="market"))
        self.assertEqual((r.status, r.sent, r.dry_run), (None, False, False))
        self.assertIn("market orders are disabled", r.error)
        for bad in (place(qty=0), place(qty=1.5), place(qty=True), place(price=None), place(price=float("nan")),
                    place(price=-1.0), place(side=None), place(symbol=""), req(ApiKind.CANCEL),
                    req(ApiKind.GET_BOOK), req(ApiKind.ETF_CREATE, symbol="ARCH", qty=0)):
            res = c.execute(bad)
            self.assertEqual((res.status, res.sent), (None, False), bad)
            self.assertTrue(res.error.startswith("invalid request"), res.error)
        self.assertEqual(tr.calls, [])
        tr2 = FakeTransport()
        c2 = client(tr2, cfg=cfg_with("exchange.allow_market_orders=true"))
        c2.execute(place(order_type="market"))
        self.assertEqual(json.loads(tr2.calls[0][3]), {"symbol": "PORT", "side": "buy", "order_type": "market",
                                                       "quantity": 5})

    def test_network_gate(self):
        self.assertFalse(rest.NETWORK_ENABLED)
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("socket opened")):
            c = RestClient(cfg_with(), KEY, RealTime(), ListLog())     # the default transport
            with self.assertRaises(RuntimeError) as cm:
                c.execute(req(ApiKind.GET_TIME))
            self.assertEqual(str(cm.exception), "network disabled")
            self.assertTrue(c.execute(place()).dry_run)              # no token: order actions stay local
            with self.assertRaises(RuntimeError):
                HttpsTransport()("GET", "https://ex.test/api/v1/competitions/time", {}, None, 1.0)


class TestRetries(unittest.TestCase):
    def test_place_not_sent_twice(self):
        tr = FakeTransport(NotSentError("connect refused"), NotSentError("connect refused"))
        r = client(tr).execute(place())
        self.assertEqual(len(tr.calls), 2)
        self.assertEqual(tr.resets, 1)
        self.assertEqual((r.status, r.sent, r.outcome_unknown), (None, False, False))
        self.assertIn("not sent", r.error)

    def test_place_not_sent_then_ok(self):
        tr = FakeTransport(NotSentError("tls"), (200, b'{"order_id": "o1", "status": "open"}'))
        r = client(tr).execute(place())
        self.assertEqual(len(tr.calls), 2)
        self.assertTrue(r.ok)
        self.assertEqual(r.parsed.order_id, "o1")

    def test_written_order_action_never_retried(self):
        for exc in (TimeoutError("read timed out"), ConnectionResetError("reset"), OSError("broken pipe")):
            tr = FakeTransport(exc)
            r = client(tr).execute(place())
            self.assertEqual(len(tr.calls), 1, exc)
            self.assertEqual((r.status, r.sent, r.outcome_unknown), (None, True, True))
        tr = FakeTransport(OSError("x"))
        self.assertTrue(client(tr).execute(req(ApiKind.CANCEL, order_id="o1")).outcome_unknown)
        self.assertEqual(len(tr.calls), 1)
        tr = FakeTransport(OSError("x"))
        client(tr).execute(req(ApiKind.CANCEL_ALL, priority=Priority.SNIPE))
        self.assertEqual(len(tr.calls), 1)
        tr = FakeTransport(NotSentError("x"), OSError("y"))
        r = client(tr).execute(place())
        self.assertEqual((len(tr.calls), r.sent), (2, True))

    def test_kill_cancel_all_retried_once(self):
        tr = FakeTransport(OSError("stale socket"), (200, b'{"cancelled": 3}'))
        r = client(tr).execute(req(ApiKind.CANCEL_ALL, priority=Priority.KILL))
        self.assertEqual(len(tr.calls), 2)
        self.assertEqual(tr.resets, 1)
        self.assertTrue(r.ok)
        tr = FakeTransport(OSError("a"), OSError("b"), (200, b"{}"))
        r = client(tr).execute(req(ApiKind.CANCEL_ALL, priority=Priority.KILL))
        self.assertEqual((len(tr.calls), r.ok, r.sent), (2, False, True))

    def test_get_retries(self):
        tr = FakeTransport(OSError("x"), (200, b"[]"))
        r = client(tr).execute(req(ApiKind.GET_OPEN))
        self.assertEqual((len(tr.calls), r.ok, r.parsed), (2, True, []))
        tr = FakeTransport(OSError("x"), OSError("y"), (200, b"[]"))
        r = client(tr).execute(req(ApiKind.GET_POSITIONS))
        self.assertEqual((len(tr.calls), r.ok), (2, False))
        tr = FakeTransport((503, b"busy"))
        r = client(tr).execute(req(ApiKind.GET_TIME))
        self.assertEqual((len(tr.calls), r.status, r.parsed), (1, 503, None))

    def test_http_errors(self):
        tr = FakeTransport((503, b"upstream"), (400, b'{"detail": "limit"}'), (429, b"slow down"))
        c = client(tr)
        r5 = c.execute(place())
        self.assertEqual((r5.status, r5.outcome_unknown, r5.error), (503, True, "http 503: upstream"))
        r4 = c.execute(place(n=2))
        self.assertEqual((r4.status, r4.outcome_unknown, r4.data, r4.parsed), (400, False, {"detail": "limit"}, None))
        self.assertTrue(c.execute(place(n=3)).throttled)
        self.assertEqual(len(tr.calls), 3)


class FakeSockConn:
    """http.client-like connection over a local socketpair (no network)."""

    made = []

    def __init__(self, host, port, timeout, fail_connect=False, fail_request=False):
        self.host, self.port, self.timeout = host, port, timeout
        self.sock = None
        self.peer = None
        self.connects = 0
        self.requests = []
        self.closed = False
        self.fail_connect, self.fail_request = fail_connect, fail_request
        FakeSockConn.made.append(self)

    def connect(self):
        if self.fail_connect:
            raise ConnectionRefusedError("refused")
        self.connects += 1
        self.sock, self.peer = socket.socketpair()

    def request(self, method, path, body=None, headers=None):
        if self.fail_request:
            raise TimeoutError("timed out")
        self.requests.append((method, path))

    def getresponse(self):
        class R:
            status = 200
            will_close = False

            def read(self):
                return b'{"competition_id": "c1", "state": "running"}'
        return R()

    def close(self):
        self.closed = True
        for s in (self.sock, self.peer):
            if s is not None:
                s.close()
        self.sock = None


class TestDefaultTransport(unittest.TestCase):
    def setUp(self):
        FakeSockConn.made = []
        self.now = [0.0]

    def tearDown(self):
        for c in FakeSockConn.made:
            c.close()

    def test_keepalive_idle_and_closed(self):
        tr = HttpsTransport(idle_recycle_s=10.0, conn_factory=FakeSockConn, clock=lambda: self.now[0])
        c = client(tr)
        for _ in range(3):
            self.assertTrue(c.execute(req(ApiKind.GET_TIME)).ok)
            self.now[0] += 2.0
        self.assertEqual((tr.opened, len(FakeSockConn.made[0].requests)), (1, 3))
        self.now[0] += 11.0                                       # idle > idle_recycle_s
        self.assertTrue(c.execute(req(ApiKind.GET_TIME)).ok)
        self.assertEqual((tr.opened, tr.recycled["idle"]), (2, 1))
        self.assertTrue(FakeSockConn.made[0].closed)
        FakeSockConn.made[1].peer.close()                        # the server closed the keep-alive connection
        FakeSockConn.made[1].peer = None
        self.assertTrue(c.execute(req(ApiKind.GET_TIME)).ok)
        self.assertEqual((tr.opened, tr.recycled["closed"]), (3, 1))
        FakeSockConn.made[2].peer.sendall(b"x")                  # unsolicited bytes: also unusable
        self.assertTrue(c.execute(req(ApiKind.GET_TIME)).ok)
        self.assertEqual(tr.opened, 4)

    def test_connect_failure_is_not_sent(self):
        fac = lambda h, p, t: FakeSockConn(h, p, t, fail_connect=True)  # noqa: E731
        tr = HttpsTransport(conn_factory=fac, clock=lambda: self.now[0])
        r = client(tr).execute(place())
        self.assertEqual((r.sent, r.status), (False, None))
        self.assertEqual(tr.opened, 2)                           # retried once on a fresh connection

    def test_failure_after_write_is_sent(self):
        fac = lambda h, p, t: FakeSockConn(h, p, t, fail_request=True)  # noqa: E731
        tr = HttpsTransport(conn_factory=fac, clock=lambda: self.now[0])
        r = client(tr).execute(place())
        self.assertEqual((r.sent, r.outcome_unknown), (True, True))
        self.assertEqual(tr.opened, 1)

    def test_per_thread_connections_and_prewarm(self):
        tr = HttpsTransport(conn_factory=FakeSockConn, clock=lambda: self.now[0])
        c = client(tr)
        c.prewarm()
        th = threading.Thread(target=c.prewarm)
        th.start()
        th.join()
        self.assertEqual(tr.opened, 2)
        self.assertEqual([m.requests for m in FakeSockConn.made], [[("GET", "/api/v1/competitions/time")]] * 2)


class FakeAPI:
    """ExchangeAPI fake for the executor: optional gates per kind, records executions and prewarms."""

    def __init__(self, gates=None):
        self.gates = gates or {}
        self.executed = []
        self.prewarmed = []
        self.lock = threading.Lock()
        self.timesrc = RealTime()

    def execute(self, r):
        g = self.gates.get(r.kind)
        if g is not None:
            g.wait(10.0)
        with self.lock:
            self.executed.append(r)
        now = time.monotonic()
        return ApiResult(req=r, status=200, data={}, parsed=None, error=None, sent_mono=now, done_mono=now)

    def prewarm(self):
        with self.lock:
            self.prewarmed.append(threading.current_thread().name)

    def redact(self, s):
        return str(s).replace(KEY, "***")


class Collector:
    def __init__(self):
        self.results = []
        self.cv = threading.Condition()

    def __call__(self, res):
        with self.cv:
            self.results.append(res)
            self.cv.notify_all()

    def wait(self, pred, timeout=5.0):
        end = time.monotonic() + timeout
        with self.cv:
            while not pred(self.results):
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self.cv.wait(left)
            return True


class TestExecutor(unittest.TestCase):
    def test_prewarm_all_touches_every_worker(self):
        api = FakeAPI()
        ex = RestExecutor(api, 12, Collector(), ListLog(), kill_workers=1)
        try:
            ex.prewarm_all()
            end = time.monotonic() + 5.0
            while len(api.prewarmed) < 12 and time.monotonic() < end:
                time.sleep(0.01)
            time.sleep(0.1)
            # every normal worker; never the kill worker (hardening: a kill must not wait behind a prewarm)
            self.assertEqual(sorted(api.prewarmed), sorted([f"rest-{i}" for i in range(12)]))
        finally:
            ex.close()

    def test_freeze_purge_and_unaffected_kinds(self):
        gate = threading.Event()
        api = FakeAPI({ApiKind.GET_TIME: gate})
        col = Collector()
        ex = RestExecutor(api, 1, col, ListLog(), kill_workers=1)
        try:
            ex.submit(req(ApiKind.GET_TIME, n=1))
            time.sleep(0.1)                                      # the one worker is now blocked
            for n in (2, 3, 4):
                ex.submit(place(n=n))
            ex.submit(req(ApiKind.ETF_CREATE, n=5, symbol="ARCH", qty=1))
            ex.submit(req(ApiKind.CANCEL, n=6, order_id="o1", priority=Priority.HEDGE))
            ex.submit(req(ApiKind.GET_OPEN, n=7))
            ex.freeze()
            purged = [r for r in col.results if r.error == "purged"]
            self.assertEqual(sorted(r.req.req_id for r in purged), [2, 3, 4, 5])
            self.assertTrue(all(r.sent is False and r.status is None for r in purged))
            ex.submit(place(n=8))
            self.assertEqual(col.results[-1].error, "frozen")
            self.assertEqual(col.results[-1].req.req_id, 8)
            gate.set()
            self.assertTrue(col.wait(lambda rs: {1, 6, 7} <= {r.req.req_id for r in rs if r.error is None}))
            self.assertEqual({r.req_id for r in api.executed}, {1, 6, 7})
            ex.unfreeze()
            ex.submit(place(n=9))
            self.assertTrue(col.wait(lambda rs: any(r.req.req_id == 9 and r.ok for r in rs)))
            ex.submit(place(n=10))
            ex.purge()
        finally:
            ex.close()

    def test_priority_order(self):
        gate = threading.Event()
        api = FakeAPI({ApiKind.GET_TIME: gate})
        ex = RestExecutor(api, 1, Collector(), ListLog())
        try:
            ex.submit(req(ApiKind.GET_TIME, n=1))
            time.sleep(0.1)
            ex.submit(place(n=5, priority=Priority.TAKE))
            ex.submit(place(n=4, priority=Priority.TAKE))
            ex.submit(req(ApiKind.CANCEL, n=6, order_id="x", priority=Priority.HEDGE))
            ex.submit(place(n=7, priority=Priority.SNIPE))
            gate.set()
            end = time.monotonic() + 5
            while len(api.executed) < 5 and time.monotonic() < end:
                time.sleep(0.01)
            self.assertEqual([r.req_id for r in api.executed], [1, 7, 6, 4, 5])
        finally:
            ex.close()

    def test_kill_served_while_workers_blocked(self):
        gate = threading.Event()
        api = FakeAPI({ApiKind.PLACE: gate})
        col = Collector()
        ex = RestExecutor(api, 12, col, ListLog(), kill_workers=1)
        try:
            for n in range(12):
                ex.submit(place(n=n + 1))
            end = time.monotonic() + 5
            while ex.inflight < 12 and time.monotonic() < end:
                time.sleep(0.01)
            self.assertEqual(ex.inflight, 12)
            t0 = time.monotonic()
            ex.submit(req(ApiKind.CANCEL_ALL, n=100, priority=Priority.KILL))
            self.assertTrue(col.wait(lambda rs: any(r.req.req_id == 100 for r in rs), timeout=1.0))
            self.assertLess(time.monotonic() - t0, 0.5)
            self.assertEqual([r.req.req_id for r in col.results], [100])   # the POSTs are still blocked
        finally:
            gate.set()
            ex.close()

    def test_execute_exception_and_close(self):
        class Boom(FakeAPI):
            def execute(self, r):
                raise RuntimeError(f"boom with {KEY}")
        log = ListLog()
        col = Collector()
        ex = RestExecutor(Boom(), 2, col, log)
        try:
            ex.submit(place(n=1))
            ex.submit(req(ApiKind.GET_OPEN, n=2))
            self.assertTrue(col.wait(lambda rs: len(rs) == 2))
            by = {r.req.req_id: r for r in col.results}
            self.assertEqual((by[1].status, by[1].sent, by[1].outcome_unknown), (None, True, True))
            self.assertFalse(by[2].sent)
            self.assertNotIn(KEY, json.dumps(log.events, default=str))
            self.assertIn("***", by[1].error)
        finally:
            ex.close()
        ex.submit(place(n=3))
        self.assertEqual(col.results[-1].error, "closed")

    def test_on_result_exception_is_contained(self):
        log = ListLog()
        calls = []

        def bad(res):
            calls.append(res)
            raise ValueError("consumer bug")
        ex = RestExecutor(FakeAPI(), 1, bad, log)
        try:
            ex.submit(req(ApiKind.GET_OPEN, n=1))
            ex.submit(req(ApiKind.GET_OPEN, n=2))
            end = time.monotonic() + 5
            while len(calls) < 2 and time.monotonic() < end:
                time.sleep(0.01)
            self.assertEqual(len(calls), 2)
            self.assertTrue(log.kinds("exception"))
        finally:
            ex.close()

    def test_with_rest_client_dry_run(self):
        col = Collector()
        c = client(FakeTransport(), live=False)
        ex = RestExecutor(c, 2, col, ListLog())
        try:
            ex.submit(place(n=1))
            self.assertTrue(col.wait(lambda rs: len(rs) == 1))
            self.assertTrue(col.results[0].dry_run)
        finally:
            ex.close()


class TestPricesAndParsers(unittest.TestCase):
    def test_round_price(self):
        self.assertEqual(round_price(45.129, Side.BUY, 2), 45.12)
        self.assertEqual(round_price(45.121, Side.SELL, 2), 45.13)
        self.assertEqual(round_price(45.12, Side.BUY, 2), 45.12)
        self.assertEqual(round_price(45.12, Side.SELL, 2), 45.12)
        self.assertEqual(round_price(0.1 + 0.2, Side.SELL, 2), 0.3)
        self.assertEqual(round_price(1.005, Side.BUY, 2), 1.0)
        self.assertEqual(round_price(1.005, Side.SELL, 2), 1.01)
        self.assertEqual(round_price(72.5, Side.SELL, 6), 72.5)
        tr = FakeTransport()
        c = client(tr)
        c.execute(place(side=Side.BUY, price=45.129))
        c.execute(place(side=Side.SELL, price=45.121))
        self.assertEqual([json.loads(b)["price"] for *_, b, _ in tr.calls], [45.12, 45.13])

    def test_parse_positions(self):
        rows = [{"symbol": "PORT", "quantity": 10, "avg_price": 45.0, "mark": 45.5, "realized_pnl": 1.0,
                 "unrealized_pnl": 5.0}, {"symbol": "HAVEN", "position": "-3", "average_price": "50.5"}, "junk"]
        log = ListLog()
        p = parse_positions(rows, log)
        self.assertEqual(p["PORT"], PositionRow("PORT", 10, 45.0, 45.5, 1.0, 5.0))
        self.assertEqual(p["HAVEN"], PositionRow("HAVEN", -3, 50.5, None, None, None))
        self.assertTrue(log.kinds("parse_warn"))
        self.assertEqual(parse_positions({"positions": rows[:1]})["PORT"].qty, 10)
        self.assertEqual(parse_positions({"data": {"positions": rows[:1]}})["PORT"].qty, 10)
        keyed = parse_positions({"PORT": {"qty": 4, "mark_price": 44.0}, "ARCH": 2, "cash": 1000.0})
        self.assertEqual((keyed["PORT"].qty, keyed["PORT"].mark, keyed["ARCH"].qty), (4, 44.0, 2))
        self.assertNotIn("cash", keyed)
        self.assertEqual(parse_positions({"symbol": "GEN", "net": 7})["GEN"].qty, 7)
        self.assertEqual(parse_positions([]), {})
        log2 = ListLog()
        self.assertEqual(parse_positions("<html>oops</html>", log2), {})
        self.assertLessEqual(len(log2.kinds("parse_warn")[0]["body"]), 300)

    def test_parse_open_orders(self):
        rows = [{"order_id": "o1", "symbol": "PORT", "side": "buy", "price": 45.0, "quantity": 10,
                 "remaining_quantity": 4, "status": "partially_filled"},
                {"id": 7, "symbol": "ARCH", "side": "SELL", "price": "300.5", "qty": 2, "filled_quantity": 1},
                {"symbol": "X"}]
        log = ListLog()
        out = parse_open_orders({"orders": rows}, log)
        self.assertEqual(out[0], OpenOrderRow("o1", "PORT", Side.BUY, 45.0, 10, 4, "partially_filled"))
        self.assertEqual(out[1], OpenOrderRow("7", "ARCH", Side.SELL, 300.5, 2, 1, None))
        self.assertEqual(len(out), 2)
        self.assertTrue(log.kinds("parse_warn"))
        keyed = parse_open_orders({"o9": {"symbol": "GEN", "side": "buy", "remaining": 3}})
        self.assertEqual((keyed[0].order_id, keyed[0].remaining, keyed[0].qty), ("o9", 3, None))
        self.assertEqual(parse_open_orders([]), [])
        self.assertEqual(parse_open_orders(None), [])

    def test_parse_order_ack(self):
        a = parse_order_ack({"order_id": "o1", "status": "filled", "filled_quantity": 5, "remaining_quantity": 0,
                             "avg_fill_price": 45.1})
        self.assertEqual(a, OrderAck("o1", "filled", 5, 0, 45.1))
        self.assertEqual(parse_order_ack({"id": 12, "filled": 0, "remaining": 5, "fill_price": None}),
                         OrderAck("12", None, 0, 5, None))
        self.assertEqual(parse_order_ack({"order": {"order_id": "z", "average_price": 1.5}}).order_id, "z")
        log = ListLog()
        self.assertEqual(parse_order_ack({"status": "open", "remaining_quantity": 5}, log),
                         OrderAck(None, "open", None, 5, None))
        self.assertEqual(parse_order_ack("garbage", log), OrderAck(None, None, None, None, None))
        self.assertEqual(len(log.kinds("parse_warn")), 2)
        tr = FakeTransport((201, b'{"status": "open", "remaining_quantity": 5}'))
        r = client(tr).execute(place())
        self.assertTrue(r.ok)
        self.assertIsNone(r.parsed.order_id)

    def test_parse_time_and_book(self):
        self.assertEqual(parse_time({"competition_id": 42, "state": "running", "fictional_time": "1802-05-10T10:00:00",
                                     "elapsed_active_seconds": 61.5}),
                         ServerTime("42", "running", "1802-05-10T10:00:00", 61.5))
        self.assertEqual(parse_time([1, 2]), ServerTime(None, None, None, None))
        bids, asks = parse_book({"symbol": "PORT", "bids": [{"price": 44.85, "quantity": 18}, None, [44.7, 14]],
                                 "asks": [{"price": "45.12", "quantity": 10}]})
        self.assertEqual(bids, (BookLevel(44.85, 18), BookLevel(44.7, 14)))
        self.assertEqual(asks, (BookLevel(45.12, 10),))
        self.assertEqual(parse_book({"orderbook": {"bids": [], "asks": []}}), ((), ()))
        self.assertEqual(parse_book(None), ((), ()))

    def test_results_carry_parsed(self):
        tr = FakeTransport((200, b'[{"symbol": "PORT", "quantity": 3}]'),
                           (200, b'[{"order_id": "a", "symbol": "PORT", "side": "buy", "remaining_quantity": 1}]'),
                           (200, b'{"competition_id": "c", "state": "paused", "fictional_time": null}'),
                           (200, b'{"bids": [[1.0, 2]], "asks": []}'), (200, b"not json"), (204, b""))
        c = client(tr)
        self.assertEqual(c.execute(req(ApiKind.GET_POSITIONS)).parsed["PORT"].qty, 3)
        self.assertEqual(c.execute(req(ApiKind.GET_OPEN)).parsed[0].order_id, "a")
        self.assertEqual(c.execute(req(ApiKind.GET_TIME)).parsed.state, "paused")
        self.assertEqual(c.execute(req(ApiKind.GET_BOOK, symbol="PORT")).parsed, ((BookLevel(1.0, 2),), ()))
        r = c.execute(req(ApiKind.GET_CASE))
        self.assertEqual((r.data, r.parsed), ("not json", None))
        r = c.execute(req(ApiKind.CANCEL_ALL))
        self.assertEqual((r.status, r.data, r.parsed), (204, None, None))


class TestRedaction(unittest.TestCase):
    def test_key_never_appears(self):
        log = ListLog()
        tr = FakeTransport(OSError(f"connect to ex.test failed (key {KEY})"),
                           (401, f'{{"detail": "unknown key {KEY}"}}'.encode()),
                           NotSentError(f"tls {KEY}"), NotSentError(f"tls {KEY}"),
                           ValueError("refusing a non-https URL"))
        c = client(tr, log=log)
        r1 = c.execute(place())
        r2 = c.execute(place(n=2))
        r3 = c.execute(place(n=3))
        for r in (r1, r2, r3):
            blob = json.dumps([r.error, r.data], default=str)
            self.assertNotIn(KEY, blob)
        self.assertIn("***", r1.error)
        self.assertIn("***", r2.error)
        self.assertEqual(r2.data, {"detail": "unknown key ***"})
        with self.assertRaises(ValueError) as cm:
            c.execute(req(ApiKind.GET_OPEN))
        self.assertNotIn(KEY, str(cm.exception))
        self.assertNotIn(KEY, json.dumps(log.events, default=str))
        self.assertNotIn(KEY, repr(c))
        self.assertEqual(c.redact(f"a{KEY}b"), "a***b")

    def test_bad_key_format(self):
        for bad in (KEY + "\n", "with space key 123456", "kéy-0123456789abcdef"):
            with self.assertRaises(ValueError) as cm:
                RestClient(cfg_with(), bad, RealTime(), ListLog(), transport=FakeTransport())
            self.assertNotIn(bad.strip(), str(cm.exception))


if __name__ == "__main__":
    unittest.main()
