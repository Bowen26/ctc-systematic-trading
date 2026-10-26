"""Group D acceptance (SLOW, wall clock): S1-r3 latency through the real shell (DESIGN.md §9.1 D latency).

The test builds a run dir whose ``state.json`` and engine log hold season 225 up to 225 r3 t = 690 s, then runs
``cli.main(["--replay", REC, "--replay-from", <690 s>, "--replay-to", <780 s>, "--replay-speed", "1", ...])`` so the
real shell, the thread-mode engine and the executor run against the offline SimExchange.

* From the receipt (``recv_mono``) of the round-3 final ``damage_report`` frame to the hand-off of each S1-r3
  PLACE to the executor: p95 <= 300 ms and max <= 500 ms.
* Loop lag (the overshoot of a 10-ms ``asyncio.sleep``, sampled every 50 ms) has p99 <= 50 ms during 740-745 s.
Both are printed with the Python version in use (the venv's 3.14 has the GIL enabled).
"""
from __future__ import annotations

import io
import json
import os
import socket
import sys
import time
import unittest
from unittest import mock

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402

R3_OPEN_225 = 1792769571.126          # TESTNET_FINDINGS fit (DESIGN.md §9.1 A clock)


def _no_network(*a, **k):
    raise AssertionError("network access attempted in a test")


def _pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


class TestS1r3Latency(unittest.TestCase):
    def test_final_report_to_place_handoff(self):
        testpaths.slow()
        testpaths.integration("ctcbot.config", "ctcbot.sources", "ctcbot.rest", "ctcbot.models.engine_adapter",
                              "ctcbot.strategies.s1r3", "sim.exchange", "sim.recorded_market")
        rec = testpaths.require(testpaths.RECORDING)
        history = testpaths.require(testpaths.HISTORY)
        from ctcbot.config import load_config
        from ctcbot.sources import iter_recording
        from sim.recorded_market import RecordedMarket
        from ctcbot.app import cli
        cfg = load_config()
        market = RecordedMarket(rec, cfg, history)
        first = market.first_received_at
        s225 = dict((s, t) for t, s in market.season_switches()).get("225")
        self.assertIsNotNone(s225)
        t_from, t_to = R3_OPEN_225 + 690.0, R3_OPEN_225 + 780.0
        comp = market.comp_id_at(market.to_virtual(t_from))
        self.assertEqual(comp, "rec-225")

        # engine log: season 225's news messages received before 225 r3 t = 690 s
        d = testpaths.tmpdir("latency")
        msgs, seen = [], set()
        for ra, fr in iter_recording(rec):
            if ra < first + s225 or ra >= t_from:
                continue
            m = fr.get("message") if fr.get("type") == "news" else None
            if not isinstance(m, dict) or m.get("type") == "snapshot":
                continue
            k = (m.get("type"), m.get("seq"), m.get("headline"))
            if k in seen:
                continue
            seen.add(k)
            msgs.append(m)
        elog = "engine_state_prev_e0.jsonl"
        with open(os.path.join(d, elog), "w") as f:
            for m in msgs:
                f.write(json.dumps(m, separators=(",", ":")) + "\n")
        state = {"saved_wall": time.time() - 5.0, "run_id": "prev", "cfg_hash": cfg.hash, "comp_id": comp,
                 "season_epoch": 0, "round": 3, "dry_run": True, "positions": {}, "cash": 0.0, "engine_log": elog,
                 "season": {}}
        with open(os.path.join(d, "state.json"), "w") as f:
            json.dump(state, f)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch("socket.create_connection", _no_network), \
                mock.patch.object(socket.socket, "connect", _no_network):
            rc = cli.main(["--replay", rec, "--replay-from", str(t_from), "--replay-to", str(t_to),
                           "--replay-speed", "1", "--run-dir", d, "--history", history,
                           "--set", "app.restart_auto_resume=true",
                           "--set", "strategies.s1r3.require_law_check=false"],
                          stdin=io.StringIO(""), isatty=lambda: False, environ={}, out=out, err=err)
        self.assertEqual(rc, 0, err.getvalue()[-3000:])
        with open(os.path.join(d, "events.jsonl"), encoding="utf-8") as f:
            ev = [json.loads(x) for x in f]
        kinds = [e["kind"] for e in ev]
        self.assertIn("restart_auto_resume", kinds, "the restored run never left the restart hold")
        finals = [e for e in ev if e["kind"] == "report_news" and e.get("round") == 3 and e.get("stage") == "final"]
        self.assertTrue(finals, "the round-3 final damage_report was not seen")
        t_recv = float(finals[0]["recv_mono"])
        places = [e for e in ev if e["kind"] == "submit" and e.get("req_kind") == "place" and e.get("priority") == 1
                  and float(e["mono"]) >= t_recv]
        self.assertTrue(places, "S1-r3 placed nothing after the final report")
        burst = [float(e["mono"]) - t_recv for e in places if float(e["mono"]) - t_recv < 2.0]
        self.assertTrue(burst)
        p95, mx = _pct(burst, 0.95), max(burst)
        # loop lag during 740-745 s of round 3 (mono is the recording's virtual time)
        v740 = (R3_OPEN_225 - first) + 740.0
        lags = [float(e["p99_ms"]) for e in ev if e["kind"] == "loop_lag" and v740 <= float(e["mono"]) <= v740 + 5.5]
        sys.stderr.write(f"\n[latency] python {sys.version.split()[0]} gil={getattr(sys, '_is_gil_enabled', lambda: True)()} "
                         f"places={len(burst)} p95={p95 * 1e3:.1f} ms max={mx * 1e3:.1f} ms "
                         f"loop-lag p99 max={max(lags) if lags else float('nan'):.1f} ms over {len(lags)} s\n")
        self.assertLessEqual(p95, 0.300)
        self.assertLessEqual(mx, 0.500)
        self.assertTrue(lags, "no loop-lag samples in 740-745 s")
        self.assertLessEqual(max(lags), 50.0)
        self.assertEqual([e for e in ev if e["kind"] == "exception"], [])


if __name__ == "__main__":
    unittest.main()
