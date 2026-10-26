"""Group D tests: the command line, the live gate and the startup sequence (DESIGN.md §9.1 D cli).

``socket.create_connection`` (and ``socket.socket.connect``) are patched to raise in every test, the REST
transport and the WebSocket connect are injected, and stdin / isatty / environ are passed to ``cli.main``.
Tests that run the whole startup (engine probe, cost model, laws) are integration tests.
"""
from __future__ import annotations

import io
import json
import os
import socket
import sys
import threading
import unittest
from typing import Any
from unittest import mock

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402

from ctcbot.app import cli  # noqa: E402

KEY = "TestKey-0123456789abcdef.ghij~klm"
HOST = "test.invalid"


def _no_network(*a: Any, **k: Any) -> Any:
    raise AssertionError("network access attempted in a test")


class FakeTransport:
    """The exchange behind RestClient: routes by path, records every call (never the headers' key)."""

    def __init__(self, case: dict | None, open_orders: list | None = None, case_status: int = 200,
                 timing_change: bool = False) -> None:
        self.case = case
        self.open_orders = open_orders or []
        self.case_status = case_status
        self.calls: list[tuple[str, str]] = []
        self.key_seen_for: list[str] = []
        self.lock = threading.Lock()
        if timing_change and case is not None:
            self.case = json.loads(json.dumps(case))
            self.case["timing"]["storm"] = 420

    def __call__(self, method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> tuple[int, bytes]:
        with self.lock:
            self.calls.append((method, url))
            if "X-API-Key" in headers:
                self.key_seen_for.append(url)
        if url.endswith("/cases/hurricane.json"):
            if self.case_status != 200 or self.case is None:
                return self.case_status, b'{"detail": "nope"}'
            return 200, json.dumps(self.case).encode()
        if url.endswith("/orders/open"):
            return 200, json.dumps(self.open_orders).encode()
        if url.endswith("/positions"):
            return 200, b"[]"
        if url.endswith("/competitions/time"):
            return 200, json.dumps({"competition_id": "c-test", "state": "paused",
                                    "fictional_time": "1802-05-10T09:30:00", "elapsed_active_seconds": 0}).encode()
        return 404, b'{"detail": "not found"}'

    def methods(self) -> set[str]:
        return {m for m, _ in self.calls}


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def refused_connect(url: str, **kw: Any) -> Any:
    raise ConnectionRefusedError("refused (test)")


class CliBase(unittest.TestCase):
    def setUp(self) -> None:
        testpaths.integration("ctcbot.config", "ctcbot.case", "ctcbot.rest", "ctcbot.sources")
        self._patches = [mock.patch("socket.create_connection", _no_network),
                         mock.patch.object(socket.socket, "connect", _no_network)]
        for p in self._patches:
            p.start()
        self.dir = testpaths.tmpdir("cli")
        self.out = io.StringIO()
        self.err = io.StringIO()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()

    def main(self, *argv: str, transport: Any = None, stdin: str = "", tty: bool = False,
             env: dict | None = None, ws: Any = refused_connect) -> int:
        return cli.main(list(argv), transport=transport, ws_connect=ws, stdin=io.StringIO(stdin),
                        isatty=lambda: tty, environ=env if env is not None else {}, out=self.out, err=self.err)

    def key_file(self, text: str = KEY + "\n", mode: int = 0o600) -> str:
        p = os.path.join(self.dir, "key.txt")
        with open(p, "w") as f:
            f.write(text)
        os.chmod(p, mode)
        return p

    def all_text(self) -> str:
        parts = [self.out.getvalue(), self.err.getvalue()]
        for root, _, files in os.walk(self.dir):
            for fn in files:
                if fn.endswith(".jsonl") or fn.endswith(".json"):
                    with open(os.path.join(root, fn), encoding="utf-8", errors="replace") as f:
                        parts.append(f.read())
        return "\n".join(parts)

    def research_case(self) -> dict:
        with open(testpaths.require(testpaths.RESEARCH_CASE)) as f:
            return json.load(f)


class TestCliEarlyExits(CliBase):
    def test_default_is_host_mode_and_needs_host(self):
        rc = self.main("--run-dir", self.dir)
        self.assertEqual(rc, 2)
        self.assertIn("host mode needs --host", self.err.getvalue())

    def test_live_with_offline_modes_refused(self):
        for extra in (["--replay", "x.jsonl"], ["--sim", "research:401"], ["--self-test"]):
            self.err = io.StringIO()
            self.assertEqual(self.main("--live", *extra), 2, extra)
            self.assertIn("--live cannot be combined", self.err.getvalue())
        self.assertEqual(self.main("--replay", "a", "--sim", "research:1"), 2)

    def test_bad_arguments_exit_2(self):
        self.assertEqual(self.main("--bogus"), 2)
        self.assertEqual(self.main("--host", "https://x/y"), 2)
        self.assertEqual(self.main("--set", "live_allowed=true", "--host", HOST), 2)

    def test_kill_file_present(self):
        open(os.path.join(self.dir, "KILL"), "w").close()
        rc = self.main("--host", HOST, "--run-dir", self.dir, "--key-file", self.key_file())
        self.assertEqual(rc, 2)
        self.assertIn("remove the KILL file first", self.err.getvalue())

    def test_missing_and_invalid_keys(self):
        self.assertEqual(self.main("--host", HOST, "--run-dir", self.dir), 2)
        self.assertIn("no bot key", self.err.getvalue())
        bad = "short key with spaces " + KEY
        self.err = io.StringIO()
        self.assertEqual(self.main("--host", HOST, "--run-dir", self.dir, env={"CTC_BOT_KEY": bad}), 2)
        self.assertIn("bot key has an invalid format", self.err.getvalue())
        self.assertNotIn(bad, self.all_text())
        self.assertNotIn(KEY, self.all_text())
        self.err = io.StringIO()
        p = self.key_file("x" * 10)
        self.assertEqual(self.main("--host", HOST, "--run-dir", self.dir, "--key-file", p), 2)
        self.assertIn("invalid format", self.err.getvalue())
        self.err = io.StringIO()
        self.assertEqual(self.main("--host", HOST, "--run-dir", self.dir, "--key-file", self.dir + "/nope"), 2)

    def test_read_key_strips_and_validates(self):
        args = cli.build_parser().parse_args(["--key-file", self.key_file(KEY + "\r\n\n")])
        key, gr = cli.read_key(args, mock.Mock(get=lambda k, d=None: "CTC_BOT_KEY"), {})
        self.assertEqual(key, KEY)
        self.assertFalse(gr)
        args = cli.build_parser().parse_args([])
        key, _ = cli.read_key(args, mock.Mock(get=lambda k, d=None: "MY_KEY"), {"MY_KEY": f"  {KEY}\n"})
        self.assertEqual(key, KEY)
        with self.assertRaises(cli.CliExit) as cm:
            cli.read_key(args, mock.Mock(get=lambda k, d=None: "MY_KEY"), {"MY_KEY": KEY + "\x00"})
        self.assertNotIn(KEY, cm.exception.msg)

    def test_live_without_tty_refused_early_and_group_readable_warns(self):
        p = self.key_file(mode=0o644)
        t = FakeTransport(self.research_case())
        rc = self.main("--host", HOST, "--run-dir", self.dir, "--key-file", p, "--live", transport=t, tty=False)
        self.assertEqual(rc, 2)
        self.assertIn("stdin is not a terminal", self.err.getvalue())
        self.assertIn("readable by group or others", self.err.getvalue())
        self.assertEqual(t.calls, [])                          # refused before any request
        self.assertNotIn(KEY, self.all_text())
        with open(os.path.join(self.dir, "events.jsonl")) as f:
            kinds = [json.loads(x)["kind"] for x in f]
        self.assertIn("key_file_mode", kinds)
        self.assertIn("live_refused", kinds)

    def test_live_allowed_false_profile_refused(self):
        prof = os.path.join(self.dir, "simonly.json")
        with open(prof, "w") as f:
            json.dump({"live_allowed": False, "sizing": {"scale": 0.5}}, f)
        rc = self.main("--host", HOST, "--run-dir", self.dir, "--key-file", self.key_file(), "--profile", prof,
                       "--live", transport=FakeTransport(self.research_case()), tty=True)
        self.assertEqual(rc, 2)
        self.assertIn("live_allowed: false", self.err.getvalue())

    def test_print_config_and_network_flags_reset(self):
        rc = self.main("--print-config", "--set", "sizing.scale=0.5")
        self.assertEqual(rc, 0)
        text = self.out.getvalue()
        self.assertIn('"scale": 0.5', text)
        self.assertIn("# sizing.scale: 1.0 -> 0.5", text)
        from ctcbot import rest, sources
        self.assertFalse(rest.NETWORK_ENABLED)
        self.assertFalse(sources.NETWORK_ENABLED)

    def test_issue_token_only_in_cli_and_rest(self):
        hits = []
        for root, _, files in os.walk(os.path.join(BOT, "ctcbot")):
            for fn in files:
                if fn.endswith(".py"):
                    with open(os.path.join(root, fn), encoding="utf-8") as f:
                        if "_ISS" + "UE" in f.read():
                            hits.append(os.path.relpath(os.path.join(root, fn), BOT))
        self.assertEqual(sorted(hits), ["ctcbot/app/cli.py", "ctcbot/rest.py"])


class TestGateHelpers(unittest.TestCase):
    def test_live_refusals(self):
        ok = dict(isatty=True, live_allowed=True, case_fetched=True, timing_diff=False, accept_timing_diff=False,
                  probe_ok=True, kill_file_present=False)
        self.assertEqual(cli.live_refusals(**ok), [])
        for k, v, frag in (("isatty", False, "terminal"), ("live_allowed", False, "live_allowed"),
                           ("case_fetched", False, "case.json"), ("timing_diff", True, "timing"),
                           ("probe_ok", False, "probe"), ("kill_file_present", True, "KILL")):
            r = cli.live_refusals(**dict(ok, **{k: v}))
            self.assertEqual(len(r), 1, k)
            self.assertIn(frag, r[0])
        self.assertEqual(cli.live_refusals(**dict(ok, timing_diff=True, accept_timing_diff=True)), [])

    def test_ask_phrase(self):
        said: list[str] = []
        self.assertFalse(cli.ask_phrase("LIVE h abc123", io.StringIO("no\nLIVE h abc12\nlive h abc123\n"), said.append))
        self.assertEqual(sum("does not match" in s for s in said), 3)
        self.assertTrue(cli.ask_phrase("LIVE h abc123", io.StringIO("x\n  LIVE h abc123  \n"), said.append))
        self.assertFalse(cli.ask_phrase("LIVE h abc123", io.StringIO(""), said.append))      # EOF
        self.assertFalse(cli.ask_phrase("LIVE h abc123", io.StringIO("x\ny\nz\nLIVE h abc123\n"), said.append))


class TestCliIntegration(CliBase):
    """Full host-mode startups (engine probe, cost model, laws, engine adapter): integration."""

    def setUp(self) -> None:
        super().setUp()
        testpaths.integration("ctcbot.models.engine_adapter", "ctcbot.models.cost_model", "ctcbot.models.laws",
                              "ctcbot.app.core", "ctcbot.exec.order_manager", "ctcbot.strategies.runner")

    def host_args(self, *extra: str) -> list[str]:
        return ["--host", HOST, "--run-dir", self.dir, "--key-file", self.key_file(), *extra]

    def test_default_dry_run_no_order_actions_and_no_key_leak(self):
        t = FakeTransport(self.research_case())
        rc = self.main(*self.host_args(), transport=t, stdin="st\nq\n")
        self.assertEqual(rc, 0, self.err.getvalue()[-2000:])
        self.assertNotIn("POST", t.methods())
        self.assertNotIn("DELETE", t.methods())
        self.assertTrue(any(u.endswith("/cases/hurricane.json") for _, u in t.calls))
        self.assertTrue(t.key_seen_for)
        self.assertTrue(all(u.startswith(f"https://{HOST}/") for u in t.key_seen_for))
        self.assertNotIn(KEY, self.all_text())
        self.assertIn("DRY-RUN", self.out.getvalue())
        from ctcbot import rest, sources
        self.assertFalse(rest.NETWORK_ENABLED)
        self.assertFalse(sources.NETWORK_ENABLED)
        with open(os.path.join(self.dir, "events.jsonl")) as f:
            recs = [json.loads(x) for x in f]
        self.assertIn("shell_exit", [r["kind"] for r in recs])
        self.assertTrue(os.path.exists(os.path.join(self.dir, "state.json")))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "feed_raw.jsonl")))
        st = _load(os.path.join(self.dir, "state.json"))
        self.assertTrue(st["dry_run"])

    def test_live_three_wrong_phrases(self):
        t = FakeTransport(self.research_case())
        rc = self.main(*self.host_args("--live"), transport=t, tty=True, stdin="LIVE x\nlive\nnope\n")
        self.assertEqual(rc, 2)
        self.assertIn("confirmation phrase was not typed", self.err.getvalue())
        self.assertNotIn("POST", t.methods())
        self.assertNotIn(KEY, self.all_text())

    def test_live_correct_phrase_one_token_prompt_and_foreign_warning(self):
        from ctcbot import rest
        made: list[Any] = []
        real = rest.LiveToken

        class Counting(real):  # type: ignore[misc, valid-type]
            def __init__(self, *a: Any, **k: Any) -> None:
                super().__init__(*a, **k)
                made.append(self)

        t = FakeTransport(self.research_case(), open_orders=[{"order_id": "o1", "symbol": "PORT", "side": "buy",
                                                              "price": 40.0, "quantity": 5,
                                                              "remaining_quantity": 5, "status": "open"}])
        prof = os.path.join(self.dir, "quarter.json")
        with open(prof, "w") as f:
            json.dump({"sizing": {"scale": 0.25}}, f)
        # the phrase needs the effective config hash
        from ctcbot.config import load_config
        cfg = load_config(None, [prof], [f"exchange.host={HOST}", f"exchange.rest_base=https://{HOST}/api/v1",
                                          f"exchange.ws_url=wss://{HOST}/ws/v1/market-data",
                                          f"exchange.case_url=https://{HOST}/cases/hurricane.json"])
        phrase = f"LIVE {HOST} {cfg.hash[:6]}"
        with mock.patch.object(rest, "LiveToken", Counting):
            rc = self.main(*self.host_args("--live", "--profile", prof), transport=t, tty=True,
                           stdin=f"wrong\n{phrase}\nq\n")
        self.assertEqual(rc, 0, self.err.getvalue()[-2000:])
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0].host, HOST)
        out = self.out.getvalue()
        self.assertIn("sizing.scale: 1.0 -> 0.25", out)
        self.assertIn("open orders already exist on this key", out)
        self.assertIn(f"Type exactly:  {phrase}", out)
        self.assertNotIn(KEY, self.all_text())
        st = _load(os.path.join(self.dir, "state.json"))
        self.assertFalse(st["dry_run"])

    def test_live_refused_after_failed_case_fetch(self):
        t = FakeTransport(self.research_case(), case_status=500)
        rc = self.main(*self.host_args("--live"), transport=t, tty=True, stdin="")
        self.assertEqual(rc, 2)
        self.assertIn("case.json could not be fetched", self.err.getvalue())

    def test_live_refused_on_timing_diff_unless_accepted(self):
        t = FakeTransport(self.research_case(), timing_change=True)
        rc = self.main(*self.host_args("--live"), transport=t, tty=True, stdin="")
        self.assertEqual(rc, 2)
        self.assertIn("timing differs", self.err.getvalue())
        self.err = io.StringIO()
        self.out = io.StringIO()
        rc = self.main(*self.host_args("--live", "--accept-timing-diff"), transport=t, tty=True, stdin="wrong\n")
        self.assertEqual(rc, 2)
        self.assertIn("confirmation phrase was not typed", self.err.getvalue())   # got past the timing check

    def test_live_refused_after_failed_probe(self):
        t = FakeTransport(self.research_case())
        rc = self.main(*self.host_args("--live", "--set", "engine.probe_file=/nonexistent/probe.jsonl"), transport=t,
                       tty=True, stdin="")
        self.assertEqual(rc, 2)
        self.assertIn("probe", self.err.getvalue())

    def test_self_test_offline(self):
        rc = self.main("--self-test")
        self.assertEqual(rc, 0, self.out.getvalue())
        self.assertIn("[ok] engine snapshot-fix probe", self.out.getvalue())
        self.assertNotIn("FAIL", self.out.getvalue())


if __name__ == "__main__":
    unittest.main()
