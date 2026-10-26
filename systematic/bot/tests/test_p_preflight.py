"""Tests for ``preflight.py``, the read-only competition-morning check.

No socket is ever opened: ``socket.create_connection`` and ``socket.socket.connect`` raise in every test,
``urllib.request.build_opener`` returns a fake opener that answers by path and records every request, and
``preflight._ws_connect`` is replaced by a fake WebSocket factory. The bot's self-test is faked except in one
integration test that runs it for real (offline). The key file lives in ``tests/_tmp``.
"""
from __future__ import annotations

import asyncio
import email.message
import io
import json
import os
import socket
import sys
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stderr
from typing import Any
from unittest import mock

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402

import preflight  # noqa: E402

KEY = "PreflightKey-0123456789abcdef.ghij~klm"
HOST = "test.invalid"
REST = f"https://{HOST}/api/v1"
CASE_URL = f"https://{HOST}/cases/hurricane.json"
SYMS = ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "RECON", "MAT", "GEN", "HAVEN", "REIN", "ARCH", "REBLD"]
CHANNELS = ["news", "quotes", "trades", "orderbook"]


def _no_network(*a: Any, **k: Any) -> Any:
    raise AssertionError("network access attempted in a test")


def _research_case() -> dict:
    with open(testpaths.RESEARCH_CASE, encoding="utf-8") as f:
        return json.load(f)


TIME_BODY = {"competition_id": "comp-1", "state": "running", "fictional_time": "1803-05-11T11:20:00",
             "elapsed_active_seconds": 123.5}


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body
        self.headers = email.message.Message()
        self.headers["Date"] = "Sat, 24 Oct 2026 08:00:00 GMT"

    def getcode(self) -> int:
        return self.status

    def read(self, n: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class FakeOpener:
    """The exchange's REST side: answers by URL path and records (method, url, headers) of every request."""

    def __init__(self, routes: dict[str, tuple[int, Any]]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, dict]] = []

    def open(self, req: urllib.request.Request, timeout: float | None = None) -> FakeResponse:
        headers = {k.lower(): v for k, v in req.header_items()}
        self.calls.append((req.get_method(), req.full_url, headers))
        path = urllib.parse.urlsplit(req.full_url).path
        status, body = self.routes.get(path, (404, {"detail": "not found"}))
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        if status >= 400:
            hdrs = email.message.Message()
            raise urllib.error.HTTPError(req.full_url, status, "error", hdrs, io.BytesIO(raw))
        return FakeResponse(status, raw)


def default_routes(case: dict | None = None) -> dict[str, tuple[int, Any]]:
    return {"/api/v1/competitions/time": (200, TIME_BODY),
            "/cases/hurricane.json": (200, case if case is not None else _research_case()),
            "/api/v1/positions": (200, []),
            "/api/v1/orders/open": (200, [])}


def market_frames(symbols: list[str] = SYMS, channels: list[str] = CHANNELS) -> list[str]:
    fr = [{"type": "subscription_ack", "symbols": symbols, "channels": channels, "timestamp": "x"},
          {"type": "orderbook", "symbol": "PORT", "bids": [{"price": 44.85, "quantity": 18}],
           "asks": [{"price": 45.12, "quantity": 10}], "timestamp": "x"},
          {"type": "quote", "symbol": "PORT", "bid": 44.85, "ask": 45.12, "bid_size": 18, "ask_size": 10},
          {"type": "trade", "symbol": "PORT", "price": 45.12, "quantity": 5, "trade_id": "t1"},
          {"type": "news", "message": {"type": "storm", "round": 1, "seq": 3}},
          {"type": "heartbeat", "timestamp": "x"}]
    return [json.dumps(f) for f in fr]


class FakeWS:
    def __init__(self, frames: list[str], end_exc: BaseException | None = None) -> None:
        self.frames = list(frames)
        self.end_exc = end_exc
        self.sent: list[str] = []
        self.closed = False

    async def send(self, msg: str) -> None:
        self.sent.append(msg)

    async def recv(self) -> str:
        if self.frames:
            return self.frames.pop(0)
        if self.end_exc is not None:
            raise self.end_exc
        await asyncio.sleep(3600)        # silence until the listen window ends
        return ""

    async def close(self) -> None:
        self.closed = True


class HandshakeRejected(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"server rejected WebSocket connection: HTTP {status}")
        self.response = type("Resp", (), {"status_code": status})()


class FakeConnect:
    """Stands in for ``websockets.asyncio.client.connect``; records (url, kwargs) and the sockets it hands out."""

    def __init__(self, frames: list[str] | None = None, fail: BaseException | None = None,
                 end_exc: BaseException | None = None) -> None:
        self.frames = market_frames() if frames is None else frames
        self.fail = fail
        self.end_exc = end_exc
        self.calls: list[tuple[str, dict]] = []
        self.sockets: list[FakeWS] = []

    async def __call__(self, url: str, **kw: Any) -> FakeWS:
        self.calls.append((url, kw))
        if self.fail is not None:
            raise self.fail
        ws = FakeWS(self.frames, self.end_exc)
        self.sockets.append(ws)
        return ws


class PreflightTestBase(unittest.TestCase):
    def setUp(self) -> None:
        for target, attr in ((socket, "create_connection"), (socket.socket, "connect")):
            p = mock.patch.object(target, attr, _no_network)
            p.start()
            self.addCleanup(p.stop)
        self.dir = testpaths.tmpdir("preflight")
        self.key_file = self.write_key(KEY + "\n")

    def write_key(self, content: str | bytes, mode: int = 0o600, name: str = "bot_key") -> str:
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(content.encode() if isinstance(content, str) else content)
        os.chmod(path, mode)
        return path

    def run_preflight(self, extra: list[str] | None = None, routes: dict | None = None,
                      connect: FakeConnect | None = None, key_file: str | None = None,
                      self_test: tuple[int, list[str]] = (0, ["[ok] config: fake"] * 6)
                      ) -> tuple[int, str, FakeOpener, FakeConnect]:
        opener = FakeOpener(default_routes() if routes is None else routes)
        conn = FakeConnect() if connect is None else connect
        out = io.StringIO()
        argv = ["--host", HOST, "--key-file", key_file or self.key_file, "--listen-s", "0.2", "--tries", "3"]
        with mock.patch.object(urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(preflight, "_ws_connect", conn), \
                mock.patch.object(preflight, "run_self_test", return_value=self_test), \
                redirect_stderr(io.StringIO()):
            code = preflight.main(argv + list(extra or []), out=out)
        text = out.getvalue()
        self.assertNotIn(KEY, text)                                   # the key is never printed
        for method, url, headers in opener.calls:                     # read-only: GET, allow-listed, https
            self.assertEqual(method, "GET")
            self.assertIn(url, {REST + "/competitions/time", REST + "/positions", REST + "/orders/open", CASE_URL})
            self.assertEqual(headers.get("x-api-key"), KEY)
            self.assertTrue(url.startswith(f"https://{HOST}/"))
        return code, text, opener, conn

    def status_of(self, text: str, name: str) -> str:
        for line in text.splitlines():
            if line.startswith("  ") and line.strip().endswith(name) and line.split()[0] in ("PASS", "WARN", "FAIL",
                                                                                              "SKIP"):
                return line.split()[0]
        self.fail(f"no summary line for {name!r} in:\n{text}")


class TestHappyPath(PreflightTestBase):
    def test_all_pass_read_only(self) -> None:
        code, text, opener, conn = self.run_preflight()
        self.assertEqual(code, 0, text)
        self.assertIn("RESULT: PASS", text)
        # every request is a GET; nothing else is ever sent (asserted per call in run_preflight too)
        self.assertEqual({m for m, _, _ in opener.calls}, {"GET"})
        self.assertEqual(sum(1 for _, u, _ in opener.calls if u.endswith("/competitions/time")), 3)
        self.assertNotIn("post", " ".join(m.lower() for m, _, _ in opener.calls))
        # the user agent is the bot's (urllib's default is refused by the exchange)
        for _, _, headers in opener.calls:
            self.assertEqual(headers.get("user-agent"), "ctc26-se-bot/1.0 (python-stdlib)")
        # two market-data connections at once, each sending exactly the bot's one subscription
        self.assertEqual(len(conn.calls), 2)
        sub = json.dumps({"action": "subscribe", "symbols": SYMS, "channels": CHANNELS})
        for ws in conn.sockets:
            self.assertEqual(ws.sent, [sub])
            self.assertTrue(ws.closed)
        for url, kw in conn.calls:
            self.assertEqual(url, f"wss://{HOST}/ws/v1/market-data")
            self.assertEqual(kw["additional_headers"], {"X-API-Key": KEY})
            self.assertEqual(kw["user_agent_header"], "ctc26-se-bot/1.0 (python-stdlib)")
            self.assertIsNone(kw["proxy"])
            self.assertNotIn("local_addr", kw)
        for name in ("python", "websockets", "key file", "config", "offline self-test", "competition time",
                     "case.json", "positions and open orders", "market data ws0", "market data ws1"):
            self.assertEqual(self.status_of(text, name), "PASS", name)
        self.assertIn("state running, competition comp-1", text)
        self.assertIn("identical to the research case.json, timing included", text)
        self.assertIn("orderbook 1, quote 1, subscription_ack 1, trade 1", text)

    def test_live_phrase_matches_the_bot_config(self) -> None:
        from ctcbot.app import cli
        cfg = cli._load_cfg(cli.build_parser().parse_args(["--host", HOST]))
        code, text, _, _ = self.run_preflight()
        self.assertEqual(code, 0, text)
        self.assertIn(f"hash {cfg.hash}", text)
        self.assertIn(f"LIVE {HOST} {cfg.hash[:6]}", text)

    def test_local_addr_binds_the_second_connection(self) -> None:
        code, text, _, conn = self.run_preflight(["--set", 'feed.connections.1.local_addr=["10.1.2.3", 0]'])
        self.assertEqual(code, 0, text)
        kws = sorted(conn.calls, key=lambda c: "local_addr" in c[1])
        self.assertNotIn("local_addr", kws[0][1])
        self.assertEqual(kws[1][1]["local_addr"], ("10.1.2.3", 0))
        self.assertIn("market data ws1 (local_addr 10.1.2.3)", text)


class TestParityWithTheBot(unittest.TestCase):
    """The preflight's WebSocket call and subscription are the bot's own, byte for byte."""

    def test_same_connect_kwargs_and_subscription_as_ws_single_source(self) -> None:
        from ctcbot.app import cli
        from ctcbot.sources import WsSingleSource
        from ctcbot.timesource import RealTime
        from ctcbot.types import ListLog
        cfg = cli._load_cfg(cli.build_parser().parse_args(
            ["--host", HOST, "--set", 'feed.connections.1.local_addr=["10.1.2.3", 0]']))
        seen: list[tuple[str, dict, FakeWS]] = []

        async def fake_connect(url: str, **kw: Any) -> FakeWS:
            ws = FakeWS([], end_exc=ConnectionError("done"))
            seen.append((url, kw, ws))
            return ws

        src = WsSingleSource(cfg, KEY, SYMS, RealTime(), ListLog(), lambda: 30.0, name="ws1",
                             local_addr=("10.1.2.3", 0), connect=fake_connect)
        asyncio.run(src._session(fake_connect, lambda sf: None))
        url, kw, ws = seen[0]
        self.assertEqual(url, cfg.get("exchange.ws_url"))
        self.assertEqual(preflight.ws_kwargs(cfg, KEY, ("10.1.2.3", 0)), kw)
        self.assertEqual(ws.sent, [preflight.subscription(SYMS, list(cfg.get("feed.channels")))])


class TestReadOnly(unittest.TestCase):
    def setUp(self) -> None:
        for target, attr in ((socket, "create_connection"), (socket.socket, "connect")):
            p = mock.patch.object(target, attr, _no_network)
            p.start()
            self.addCleanup(p.stop)

    def test_refuses_any_url_outside_the_allow_list(self) -> None:
        rep = preflight.Report(io.StringIO())
        opener = FakeOpener(default_routes())
        with mock.patch.object(urllib.request, "build_opener", return_value=opener):
            http = preflight.ReadOnlyHttp(REST, CASE_URL, KEY, "ua", rep)
            for url in (REST + "/orders", REST + "/orders/all", REST + "/orders/abc", REST + "/etf/ARCH/create",
                        "https://evil.example/api/v1/positions", f"http://{HOST}/api/v1/positions"):
                with self.assertRaises(ValueError, msg=url):
                    http.get(url)
        self.assertEqual(opener.calls, [])
        with self.assertRaises(ValueError):
            preflight.ReadOnlyHttp(f"http://{HOST}/api/v1", CASE_URL, KEY, "ua", rep)
        with self.assertRaises(ValueError):
            preflight.ReadOnlyHttp(REST, "https://other.invalid/cases/hurricane.json", KEY, "ua", rep)

    def test_get_is_the_only_request_method(self) -> None:
        methods = [n for n in vars(preflight.ReadOnlyHttp) if not n.startswith("_") and callable(
            getattr(preflight.ReadOnlyHttp, n))]
        self.assertEqual(sorted(methods), ["get", "url"])
        with open(preflight.__file__, encoding="utf-8") as f:
            src = f.read()
        for word in ("POST", "DELETE", "PUT", "PATCH", "RestClient", "LiveToken", "ApiKind.PLACE",
                     "ApiKind.CANCEL"):
            self.assertNotIn(word, src)

    def test_opener_follows_no_redirect_and_uses_no_proxy(self) -> None:
        opener = preflight.make_opener()                 # the real urllib opener (nothing is opened)
        redirects = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
        self.assertTrue(redirects)
        self.assertTrue(all(isinstance(h, preflight._NoRedirect) for h in redirects))
        self.assertFalse([h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler) and h.proxies])
        req = urllib.request.Request(REST + "/positions", method="GET")
        self.assertIsNone(preflight._NoRedirect().redirect_request(req, None, 302, "Found", {},
                                                                   "https://elsewhere.invalid/"))

    def test_key_header_is_not_carried_over_a_redirect(self) -> None:
        rep = preflight.Report(io.StringIO())
        opener = FakeOpener(default_routes())
        with mock.patch.object(urllib.request, "build_opener", return_value=opener):
            preflight.ReadOnlyHttp(REST, CASE_URL, KEY, "ua", rep).get(REST + "/positions")
        # urllib copies req.headers to a redirected request, but never the unredirected headers
        self.assertEqual(opener.calls[0][2]["x-api-key"], KEY)
        req = urllib.request.Request(REST + "/positions", method="GET", headers={"User-Agent": "ua"})
        req.add_unredirected_header("X-API-Key", KEY)
        self.assertNotIn("X-api-key", req.headers)


class TestWarnings(PreflightTestBase):
    def test_open_orders_and_positions_warn(self) -> None:
        routes = default_routes()
        routes["/api/v1/positions"] = (200, [{"symbol": "PORT", "quantity": 10}, {"symbol": "ISLE", "quantity": 0}])
        routes["/api/v1/orders/open"] = (200, [{"order_id": "o1", "symbol": "HAVEN", "side": "sell", "quantity": 5,
                                                "remaining_quantity": 5, "price": 50.0}])
        code, text, _, _ = self.run_preflight(routes=routes)
        self.assertEqual(code, 0, text)
        self.assertEqual(self.status_of(text, "positions and open orders"), "WARN")
        self.assertIn("positions held: PORT+10", text)
        self.assertIn("1 open orders on this key (HAVEN sell 5@50.0)", text)
        self.assertIn("RESULT: WARN", text)

    def test_timing_difference_is_a_banner(self) -> None:
        case = _research_case()
        case["timing"]["pre_storm"] = 20
        code, text, _, _ = self.run_preflight(routes=default_routes(case))
        self.assertEqual(code, 0, text)
        self.assertEqual(self.status_of(text, "case.json"), "WARN")
        self.assertIn("!! CASE TIMING DIFFERS FROM THE RESEARCH CASE", text)
        self.assertIn("timing.pre_storm: 60 -> 20", text)
        self.assertIn("--accept-timing-diff", text)
        self.assertIn("storm_start 20 s", text)
        self.assertIn("s3_start 80 s", text)

    def test_symbol_difference_names_the_symbols(self) -> None:
        case = _research_case()
        case["companies"]["PORT"]["about"] = str(case["companies"]["PORT"].get("about")) + " (changed)"
        code, text, _, _ = self.run_preflight(routes=default_routes(case))
        self.assertEqual(code, 0, text)
        self.assertEqual(self.status_of(text, "case.json"), "WARN")
        self.assertIn("'PORT'", text)
        self.assertIn("caseok", text)
        self.assertNotIn("CASE TIMING DIFFERS", text)

    def test_key_file_readable_by_others_warns(self) -> None:
        path = self.write_key(KEY + "\n", mode=0o644, name="loose_key")
        code, text, _, _ = self.run_preflight(key_file=path)
        self.assertEqual(code, 0, text)
        self.assertEqual(self.status_of(text, "key file"), "WARN")
        self.assertIn("chmod 600", text)

    def test_no_market_data_warns(self) -> None:
        code, text, _, _ = self.run_preflight(connect=FakeConnect(frames=market_frames()[:1]))
        self.assertEqual(code, 0, text)
        self.assertEqual(self.status_of(text, "market data ws0"), "WARN")
        self.assertIn("no market data", text)

    def test_connection_closed_early_warns(self) -> None:
        code, text, _, _ = self.run_preflight(connect=FakeConnect(end_exc=ConnectionError("closed by peer")))
        self.assertEqual(code, 0, text)
        self.assertEqual(self.status_of(text, "market data ws1"), "WARN")
        self.assertIn("closed early", text)

    def test_server_echoing_the_key_is_redacted(self) -> None:
        routes = default_routes()
        routes["/api/v1/orders/open"] = (500, f"internal error for key {KEY}".encode())
        code, text, _, _ = self.run_preflight(routes=routes,
                                              connect=FakeConnect(fail=RuntimeError(f"bad header {KEY}")))
        self.assertEqual(code, 1, text)
        self.assertIn("***", text)                       # run_preflight also asserts KEY is absent


class TestFailures(PreflightTestBase):
    def test_key_rejected(self) -> None:
        routes = {p: (401, {"detail": "invalid api key"}) for p in default_routes()}
        code, text, _, _ = self.run_preflight(routes=routes, connect=FakeConnect(fail=HandshakeRejected(401)))
        self.assertEqual(code, 1, text)
        for name in ("competition time", "case.json", "positions and open orders", "market data ws0",
                     "market data ws1"):
            self.assertEqual(self.status_of(text, name), "FAIL", name)
        self.assertIn("key was rejected", text)
        self.assertIn("handshake was rejected", text)
        self.assertIn("RESULT: FAIL", text)

    def test_too_many_connections(self) -> None:
        code, text, _, _ = self.run_preflight(connect=FakeConnect(fail=HandshakeRejected(429)))
        self.assertEqual(code, 1, text)
        self.assertIn("25 market-data connections per team", text)

    def test_case_fetch_failure_fails_and_feed_uses_research_symbols(self) -> None:
        routes = default_routes()
        routes["/cases/hurricane.json"] = (404, {"detail": "not found"})
        code, text, _, conn = self.run_preflight(routes=routes)
        self.assertEqual(code, 1, text)
        self.assertEqual(self.status_of(text, "case.json"), "FAIL")
        self.assertIn("--live is refused", text)
        self.assertEqual(json.loads(conn.sockets[0].sent[0])["symbols"], SYMS)

    def test_malformed_case_fails(self) -> None:
        code, text, _, _ = self.run_preflight(routes=default_routes({"companies": {}}))
        self.assertEqual(code, 1, text)
        self.assertIn("malformed", text)

    def test_self_test_failure_fails(self) -> None:
        code, text, _, _ = self.run_preflight(self_test=(1, ["[ok] config: x", "[FAIL] engine snapshot-fix probe"]))
        self.assertEqual(code, 1, text)
        self.assertEqual(self.status_of(text, "offline self-test"), "FAIL")
        self.assertIn("[FAIL] engine snapshot-fix probe", text)

    def test_no_frames_at_all_fails(self) -> None:
        code, text, _, _ = self.run_preflight(connect=FakeConnect(frames=[]))
        self.assertEqual(code, 1, text)
        self.assertIn("not even a subscription_ack", text)

    def _bad_key(self, content: str | bytes, expect: str) -> None:
        path = self.write_key(content, name="bad_key")
        code, text, opener, conn = self.run_preflight(key_file=path)
        self.assertEqual(code, 1, text)
        self.assertEqual(self.status_of(text, "key file"), "FAIL")
        self.assertIn(expect, text)
        self.assertEqual(opener.calls, [])               # nothing is sent without a usable key
        self.assertEqual(conn.calls, [])
        self.assertEqual(self.status_of(text, "exchange checks"), "SKIP")

    def test_empty_key_file(self) -> None:
        self._bad_key("\n", "is empty")

    def test_two_line_key_file(self) -> None:
        self._bad_key(KEY + "\n" + KEY + "\n", "2 non-empty lines")

    def test_bad_key_format(self) -> None:
        self._bad_key("short key!\n", "bot key format")

    def test_non_ascii_key_file(self) -> None:
        self._bad_key("Schlüssel-0123456789abcdef".encode("utf-8"), "not plain ASCII")

    def test_missing_key_file(self) -> None:
        code, text, opener, conn = self.run_preflight(key_file=os.path.join(self.dir, "nope"))
        self.assertEqual(code, 1, text)
        self.assertIn("No such file", text)
        self.assertEqual((opener.calls, conn.calls), ([], []))

    def test_usage_errors(self) -> None:
        out = io.StringIO()
        with redirect_stderr(io.StringIO()):
            self.assertEqual(preflight.main(["--host", "https://x.invalid/", "--key-file", self.key_file], out=out), 2)
            self.assertEqual(preflight.main(["--host", HOST], out=out), 2)
            self.assertEqual(preflight.main(["--host", HOST, "--key-file", self.key_file, "--listen-s", "0"],
                                            out=out), 2)


class TestSelfTestInProcess(unittest.TestCase):
    def test_real_self_test_runs_offline(self) -> None:
        testpaths.require(testpaths.ENGINE_DIR)
        testpaths.require(testpaths.LAWS)
        testpaths.integration("ctcbot.models.engine_adapter", "ctcbot.models.laws", "ctcbot.models.cost_model")
        with mock.patch.object(socket, "create_connection", _no_network), \
                mock.patch.object(socket.socket, "connect", _no_network):
            code, lines = preflight.run_self_test(["--host", HOST])
        self.assertEqual(code, 0, "\n".join(lines))
        self.assertEqual(sum(1 for ln in lines if ln.startswith("[ok]")), 6, "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
