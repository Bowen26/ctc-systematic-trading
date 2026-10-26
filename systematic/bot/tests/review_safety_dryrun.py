"""Safety review (adversarial): dry-run never sends an order action.

Not part of the default suite (``discover`` only picks up ``test*.py``). Run from BOT with:

    SYS/.venv/bin/python -m unittest discover -s tests -p "review_safety_dryrun.py" -v

* ``test_default_transport_with_network_on`` - the host-mode dry-run condition: ``rest.NETWORK_ENABLED`` is True and
  the real ``HttpsTransport`` is used. A Python audit hook records (and blocks) every socket connect and DNS
  lookup. Every order-action kind is answered as a dry run with zero connection attempts, while a GET does try
  to connect (so the hook works).
* ``test_host_dry_run_end_to_end`` - ``cli.main`` in host mode without ``--live``: real startup, real core,
  strategies and engine, a fake WebSocket replaying the first seconds of the testnet recording, ``k`` and
  ``q`` on the console. The bot attempts order actions (``dry_order`` events) and the transport never sees a
  POST or DELETE; the key appears in no output.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import socket
import sys
import threading
import time
import unittest
from typing import Any
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
BOT = os.path.dirname(HERE)
sys.path.insert(0, BOT)
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402

from ctcbot.types import ORDER_ACTIONS, ApiKind, ApiRequest, ListLog, Priority, Side  # noqa: E402

_AUDIT = {"on": False, "events": []}


def _hook(event: str, args: Any) -> None:
    if not _AUDIT["on"]:
        return
    if event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname"):
        _AUDIT["events"].append((event, repr(args)[:120]))
        raise ConnectionRefusedError(f"review audit hook blocked {event}")


sys.addaudithook(_hook)

KEY = "ReviewKey-0123456789abcdef.XYZ~q"


class TestDryRunGate(unittest.TestCase):
    def test_default_transport_with_network_on(self):
        from ctcbot import rest
        from ctcbot.config import load_config
        cfg = load_config(sets=["exchange.rest_base=https://exchange.invalid/api/v1"])
        log = ListLog()
        old = rest.NETWORK_ENABLED
        rest.NETWORK_ENABLED = True                             # as cli._run_host sets it in host mode
        _AUDIT["events"].clear()
        _AUDIT["on"] = True
        try:
            from ctcbot.timesource import RealTime
            api = rest.RestClient(cfg, KEY, RealTime(), log, live=None)    # default HttpsTransport
            reqs = [ApiRequest(1, ApiKind.PLACE, Priority.TAKE, "PORT", Side.BUY, 1, 50.0),
                    ApiRequest(2, ApiKind.CANCEL, Priority.HEDGE, order_id="abc"),
                    ApiRequest(3, ApiKind.CANCEL_ALL, Priority.KILL),
                    ApiRequest(4, ApiKind.CANCEL_ALL, Priority.SNIPE, symbol="PORT"),
                    ApiRequest(5, ApiKind.ETF_CREATE, Priority.TAKE, "ARCH", qty=1),
                    ApiRequest(6, ApiKind.ETF_REDEEM, Priority.TAKE, "ARCH", qty=1),
                    ApiRequest(7, ApiKind.PLACE, Priority.TAKE, "PORT", Side.SELL, 1, None, order_type="market")]
            res = [api.execute(r) for r in reqs]
            attempts_actions = list(_AUDIT["events"])
            ex = rest.RestExecutor(api, 2, lambda r: None, log, kill_workers=1)
            try:                                                # the executor path, kill worker included
                for r in reqs[:4]:
                    ex.submit(r)
                time.sleep(0.3)
            finally:
                ex.close(1.0)
            attempts_exec = list(_AUDIT["events"])
            g = api.execute(ApiRequest(8, ApiKind.GET_TIME, Priority.HEDGE))
        finally:
            _AUDIT["on"] = False
            rest.NETWORK_ENABLED = old
        self.assertEqual(attempts_actions, [])
        self.assertEqual(attempts_exec, [])
        for r, x in zip(reqs[:6], res[:6]):
            self.assertTrue(x.dry_run and not x.sent, (r.kind, x))
        self.assertFalse(res[6].sent)                           # a market order is refused before the gate
        self.assertTrue(_AUDIT["events"], "the audit hook saw nothing for a GET: the test proves nothing")
        self.assertFalse(g.ok)
        self.assertNotIn(KEY, json.dumps([e for e in log.events], default=str))


class _FakeWS:
    """A WebSocket that replays recording frames at their recorded pace (from the first frame)."""

    def __init__(self, frames: list[tuple[float, dict]]) -> None:
        self.frames = frames
        self.i = 0
        self.t0: float | None = None
        self.sent: list[str] = []

    async def send(self, s: str) -> None:
        self.sent.append(s)

    async def recv(self) -> str:
        if self.t0 is None:
            self.t0 = time.monotonic()
        if self.i >= len(self.frames):
            await asyncio.sleep(3600)
        ra, fr = self.frames[self.i]
        wait = self.t0 + (ra - self.frames[0][0]) - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self.i += 1
        return json.dumps(fr)

    async def close(self) -> None:
        pass


class TestHostDryRun(unittest.TestCase):
    def test_host_dry_run_end_to_end(self):
        testpaths.integration("ctcbot.config", "ctcbot.case", "ctcbot.rest", "ctcbot.sources",
                              "ctcbot.exec.order_manager", "ctcbot.models.engine_adapter")
        from ctcbot.app import cli
        from test_d_cli import FakeTransport
        from test_d_kill import _Pipe
        from test_d_shell import recording_head
        with open(testpaths.require(testpaths.RESEARCH_CASE)) as f:
            case = json.load(f)

        class Transport(FakeTransport):
            def __call__(self, method, url, headers, body, timeout):
                if url.endswith("/competitions/time"):
                    with self.lock:
                        self.calls.append((method, url))
                    return 404, b'{"detail": "not here"}'           # the clock runs on the feed
                return super().__call__(method, url, headers, body, timeout)

        frames = recording_head(25.0)
        conns: list[_FakeWS] = []

        def ws_connect(url: str, **kw: Any) -> _FakeWS:
            ws = _FakeWS(frames)
            conns.append(ws)
            return ws

        d = testpaths.tmpdir("review_dry")
        kd = testpaths.tmpdir("review_key")
        self.addCleanup(lambda: __import__("shutil").rmtree(kd, ignore_errors=True))
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        kp = os.path.join(kd, "key.txt")
        with open(kp, "w") as f:
            f.write(KEY + "\n")
        os.chmod(kp, 0o600)
        t = Transport(case)
        pipe = _Pipe()
        out, err = io.StringIO(), io.StringIO()

        def operator() -> None:
            time.sleep(20.0)
            pipe.send("k")
            time.sleep(1.0)
            pipe.send("q")

        threading.Thread(target=operator, daemon=True).start()
        with mock.patch("socket.create_connection", side_effect=AssertionError("network")), \
                mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network")):
            try:
                rc = cli.main(["--host", "exchange.invalid", "--run-dir", d, "--key-file", kp], transport=t,
                              ws_connect=ws_connect, stdin=pipe.r, isatty=lambda: False, environ={}, out=out,
                              err=err)
            finally:
                pipe.close()
        self.assertEqual(rc, 0, err.getvalue()[-3000:])
        methods = {m for m, _ in t.calls}
        with open(os.path.join(d, "events.jsonl"), encoding="utf-8") as f:
            recs = [json.loads(x) for x in f]
        kinds = [r["kind"] for r in recs]
        dry = [r for r in recs if r["kind"] == "dry_order"]
        sys.stderr.write(f"\n[dry-run] {len(dry)} dry_order events ({sorted({r['api'] for r in dry})}), "
                         f"transport methods {sorted(methods)}, {len(conns)} ws connections, "
                         f"{len(t.calls)} REST calls\n")
        self.assertNotIn("POST", methods)
        self.assertNotIn("DELETE", methods)
        self.assertTrue(dry, "no order action was attempted: the test exercised nothing")
        self.assertIn("kill", [r.get("alert") for r in recs if r["kind"] == "alert"])
        self.assertTrue(all(len(c.sent) == 1 for c in conns))        # only the subscribe frame
        blob = out.getvalue() + err.getvalue()
        for root, _, files in os.walk(d):
            for fn in files:
                with open(os.path.join(root, fn), encoding="utf-8", errors="replace") as f:
                    blob += f.read()
        self.assertNotIn(KEY, blob)
        self.assertIn("shell_exit", kinds)


if __name__ == "__main__":
    unittest.main()
