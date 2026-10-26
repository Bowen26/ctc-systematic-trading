"""Group E: ``sim.recorded_market.RecordedMarket``, recorded-book fills and the section 9.2 acceptance (SLOW).

Unit tests use a synthetic recording and manifest written under ``tests/_tmp`` (fakes from ``test_e_exchange``).
``TestRealRecording`` reads the testnet recording: 5 down/up control pairs, the comp_id changing at the two season
boundaries, and the fitted round opens. ``TestAcceptance92`` runs ``sim/run_recording.py`` in both modes with every
group's real modules (``CTCBOT_SLOW=1``).
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from typing import Any

sys.dont_write_bytecode = True
BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BOT not in sys.path:
    sys.path.insert(0, BOT)

import testpaths  # noqa: E402
from ctcbot.types import ApiKind, ListLog  # noqa: E402
from test_e_exchange import CASE, FakeConfig, FakeSchedule, Req, at  # noqa: E402

from sim.exchange import SimExchange  # noqa: E402
from sim.recorded_market import RecordedMarket  # noqa: E402

T0 = 1.8e9
D1, D2 = "1650-06-01", "1640-02-02"


def mt(date: str, tau: float) -> str:
    return (dt.datetime.fromisoformat(f"{date}T09:30:00") + dt.timedelta(seconds=30 * tau)).isoformat()


def book(sym: str, bids: list, asks: list, ts: str = "x") -> dict:
    return {"type": "orderbook", "symbol": sym, "bids": [{"price": p, "quantity": q} for p, q in bids],
            "asks": [{"price": p, "quantity": q} for p, q in asks], "timestamp": ts}


def write_recording(root: str) -> tuple[str, str]:
    """A two-season synthetic recording plus a history dir with a manifest. Returns (recording, history)."""
    hist = os.path.join(root, "history")
    os.makedirs(hist, exist_ok=True)
    with open(os.path.join(hist, "manifest.json"), "w") as f:
        json.dump({"seasons": [{"season": 501, "storms": [{"round": 1, "open": f"{D1}T09:30:00"}]},
                               {"season": 502, "storms": [{"round": 1, "open": f"{D2}T09:30:00"}]}]}, f)
    rows: list[tuple[float, Any]] = [
        (0.0, {"type": "subscription_ack", "symbols": [], "channels": []}),
        (0.1, book("PORT", [(49.9, 10), (49.8, 20)], [(50.1, 10), (50.2, 20), (50.5, 100)], "a")),
        (10.05, {"type": "news", "message": {"type": "storm", "round": 1, "storm": "Ann", "time": mt(D1, 110.0),
                                             "age": 50, "seq": 30}}),
        (12.02, {"type": "news", "message": {"type": "storm", "round": 1, "storm": "Ann", "time": mt(D1, 112.0),
                                             "age": 52, "seq": 31}}),
        (20.0, book("PORT", [(49.9, 10), (49.8, 20)], [(50.1, 10), (50.2, 20), (50.5, 100)], "b")),
        (21.0, book("PORT", [(50.0, 8)], [(50.15, 3), (50.3, 50)], "c")),
        (22.0, book("PORT", [(49.0, 8)], [(50.25, 50)], "d")),
        (500.0, {"type": "subscription_ack", "symbols": [], "channels": []}),
        (600.0, {"type": "news", "message": {"type": "damage_report", "round": 1, "storm": "Ann",
                                             "time": mt(D1, 740.0), "stage": "final", "seq": 99}}),
        (700.0, {"type": "news", "message": {"type": "snapshot", "round": 1, "storm": "Ann", "time": f"{D1}T16:00:00",
                                             "phase": "closed", "last_seq": 99}}),
        (990.0, {"type": "news", "message": {"type": "snapshot", "round": 1, "storm": "Bo", "time": f"{D2}T09:30:00",
                                             "phase": "pre_storm", "last_seq": -1}}),
        (1020.0, {"type": "news", "message": {"type": "snapshot", "round": 1, "storm": "Bo", "time": f"{D2}T09:30:00",
                                              "phase": "pre_storm", "last_seq": -1}}),
        (1030.4, {"type": "news", "message": {"type": "snapshot", "round": 1, "storm": "Bo", "time": f"{D2}T09:45:00",
                                              "phase": "pre_storm", "last_seq": -1}}),
        (1040.0, book("PORT", [(60.0, 5)], [(60.2, 5)], "e")),
    ]
    path = os.path.join(root, "rec.jsonl")
    with open(path, "w") as f:
        for t, fr in rows:
            f.write(json.dumps(dict(fr, received_at=T0 + t)) + "\n")
        f.write("not json\n")
        f.write(json.dumps([1, 2]) + "\n")
    return path, hist


def make(root: str | None = None, **kw: Any) -> RecordedMarket:
    root = root or testpaths.tmpdir("e_rec")
    path, hist = write_recording(root)
    return RecordedMarket(path, FakeConfig(), hist, case=CASE, schedule=FakeSchedule(), **kw)


class TestRecordedMarket(unittest.TestCase):
    def setUp(self) -> None:
        self.m = make()

    def test_stats_and_bad_lines(self) -> None:
        self.assertEqual(self.m.stats["frames"], 14)
        self.assertEqual(self.m.stats["bad_lines"], 2)
        self.assertEqual(self.m.stats["acks"], 2)

    def test_frames_and_control_pairs(self) -> None:
        fr = list(self.m.frames())
        ctl = [(f.t, f.control, f.conn) for f in fr if f.control]
        self.assertEqual(ctl, [(0.0, "up", "rec#1"), (500.0, "down", "rec#1"), (500.0, "up", "rec#2")])
        self.assertEqual(len([f for f in fr if not f.control]), 14)
        ack2 = [f for f in fr if f.frame.get("type") == "subscription_ack"][1]
        self.assertEqual((ack2.conn, ack2.t), ("rec#2", 500.0))
        self.assertEqual(fr[2].recv_wall, T0 + 0.1)
        self.assertNotIn("received_at", fr[2].frame)
        self.assertEqual(self.m.default_t_end(), 1042.0)

    def test_window(self) -> None:
        m = make(t_from=T0 + 400.0, t_to=T0 + 995.0)
        fr = list(m.frames())
        self.assertEqual(fr[0].control, "up")
        self.assertEqual(fr[0].conn, "rec#1")
        self.assertEqual(min(f.t for f in fr), 500.0)
        self.assertEqual(max(f.t for f in fr), 990.0)
        self.assertEqual(m.default_t_end(), 992.0)

    def test_round_fit_and_seasons(self) -> None:
        r1, r2 = self.m.rounds
        self.assertAlmostEqual(r1.open_t, 12.02 - 112.0)            # the minimum storm offset
        self.assertAlmostEqual(r1.close_t, r1.open_t + 780.0)
        self.assertEqual((r1.season, r1.comp_id, r1.round, r1.date, r1.storm), ("501", "rec-501", 1, D1, "Ann"))
        self.assertAlmostEqual(r2.open_t, 1030.4 - 30.0, places=5)          # paused 09:30 snapshots are not used
        self.assertEqual(r2.season, "502")
        self.assertEqual(self.m.comp_id_at(0.0), "rec-501")
        self.assertEqual(self.m.comp_id_at(989.9), "rec-501")
        self.assertEqual(self.m.comp_id_at(990.0), "rec-502")
        self.assertEqual(self.m.season_switches(), [(990.0, "502")])
        self.assertEqual(self.m.reports, [(600.0, "501", 1, "final", 99)])
        self.assertIsNone(self.m.settlement("501"))                 # round 3 never closes in the recording

    def test_server_time(self) -> None:
        s = self.m.server_time(10.0)
        self.assertEqual(s["state"], "running")
        self.assertEqual(s["competition_id"], "rec-501")
        _, tau = FakeSchedule.market_to_t(s["fictional_time"], 30.0)
        self.assertAlmostEqual(tau, 10.0 + 99.98, places=4)
        self.assertEqual(self.m.server_time(800.0)["state"], "paused")
        p = self.m.server_time(995.0)
        self.assertEqual((p["state"], p["competition_id"], p["fictional_time"]), ("paused", "rec-502", f"{D2}T09:30:00"))

    def test_books(self) -> None:
        b = self.m.book_at("PORT", 20.5)
        self.assertEqual((b.t, b.asks[0], b.timestamp), (20.0, (50.1, 10), "b"))
        self.assertIsNone(self.m.book_at("PORT", 0.05))
        self.assertAlmostEqual(self.m.mark("PORT", 21.5), (50.0 + 50.15) / 2)
        self.assertIsNone(self.m.mark("GRID", 21.5))
        self.assertEqual([x[0] for x in self.m.books_between(0.1, 21.0)], [20.0, 21.0])
        self.assertEqual(self.m.book("PORT", 21.0)["asks"][0], {"price": 50.15, "quantity": 3})


class TestRecordedFills(unittest.TestCase):
    def setUp(self) -> None:
        self.m = make()
        self.ex = SimExchange(self.m, FakeConfig(), None, ListLog(), fills=True)
        self.req = Req()

    def test_walk_depletion_follow_on_and_resting_fill(self) -> None:
        at(self.ex, 20.0).execute(self.req.buy("PORT", 25, 50.2))
        at(self.ex, 20.1).execute(self.req.buy("PORT", 10, 50.2))
        at(self.ex, 20.29)
        self.assertEqual(self.ex.results()["fills"], [])              # matched only at t + 0.3
        at(self.ex, 20.5)
        f = self.ex.results()["fills"]
        self.assertEqual([(x["qty"], x["kind"]) for x in f], [(25, "taker"), (5, "taker")])
        self.assertAlmostEqual(f[0]["price"], (10 * 50.1 + 15 * 50.2) / 25)
        self.assertAlmostEqual(f[0]["t"], 20.3)
        self.assertEqual(f[1]["price"], 50.2)                       # VWAP 50.2 + 12.5 bp follow-on, capped at the limit
        at(self.ex, 21.0)                                           # next frame: ask 50.15 x 3 reaches the 50.2 rest
        f = self.ex.results()["fills"]
        self.assertEqual((f[-1]["qty"], f[-1]["price"], f[-1]["kind"]), (3, 50.2, "passive"))
        at(self.ex, 22.0)                                           # 50.25 is above the limit: nothing
        self.assertEqual(len(self.ex.results()["fills"]), 3)
        self.assertEqual(self.ex.positions()["PORT"], 33)
        rows = self.ex.execute(self.req(ApiKind.GET_OPEN)).parsed
        self.assertEqual((rows[0].remaining, rows[0].status), (2, "partially_filled"))

    def test_follow_on_raises_the_price_without_a_binding_limit(self) -> None:
        at(self.ex, 20.0).execute(self.req.buy("PORT", 10, 51.0))
        at(self.ex, 20.1).execute(self.req.buy("PORT", 10, 51.0))
        at(self.ex, 20.5)
        p1, p2 = (x["price"] for x in self.ex.results()["fills"])
        self.assertAlmostEqual(p1, 50.1)
        self.assertAlmostEqual(p2, 50.2 + 50.0 * 100.0 * (10 / 200) * 1e-4)

    def test_market_closed_and_close_cancel(self) -> None:
        at(self.ex, 600.0).execute(self.req.buy("PORT", 5, 40.0))
        self.assertEqual(at(self.ex, 700.0).execute(self.req.buy("PORT", 5, 40.0)).status, 409)
        self.assertEqual(self.ex.results()["live_orders"], 0)       # cancelled at the fitted close (680.02)
        self.assertEqual(at(self.ex, 1010.0).execute(self.req.buy("PORT", 5, 40.0)).status, 200)

    def test_season_switch_resets_positions(self) -> None:
        at(self.ex, 20.0).execute(self.req.buy("PORT", 10, 51.0))
        at(self.ex, 500.0)
        self.assertEqual(self.ex.positions()["PORT"], 10)
        at(self.ex, 995.0)
        self.assertEqual(self.ex.positions()["PORT"], 0)
        res = self.ex.results()["seasons"]
        self.assertEqual(sorted(res), ["501", "502"])
        self.assertEqual(res["501"]["marks"], "last_mid")
        self.assertAlmostEqual(res["501"]["pnl"], 10 * ((49.0 + 50.25) / 2 - 50.1))
        self.assertEqual(self.ex.execute(self.req(ApiKind.GET_TIME)).parsed.comp_id, "rec-502")

    def test_fills_disabled(self) -> None:
        ex = SimExchange(self.m, FakeConfig(), None, ListLog(), fills=False)
        at(ex, 20.0).execute(self.req.buy("PORT", 25, 50.2))
        at(ex, 30.0)
        self.assertEqual(ex.results()["fills"], [])
        self.assertEqual(len(ex.execute(self.req(ApiKind.GET_OPEN)).parsed), 1)


class TestRealRecording(unittest.TestCase):
    FIT = {("224", 3): 1792766628.455, ("225", 1): 1792767650.037, ("225", 2): 1792768610.536,
           ("225", 3): 1792769571.126}

    @classmethod
    def setUpClass(cls) -> None:
        testpaths.require(testpaths.RECORDING)
        testpaths.require(os.path.join(testpaths.HISTORY, "manifest.json"))
        cls.m = RecordedMarket(testpaths.RECORDING, FakeConfig(), testpaths.HISTORY, case=CASE,
                               schedule=FakeSchedule())

    def test_five_reconnect_pairs(self) -> None:
        downs = ups = n = 0
        conns = set()
        for f in self.m.frames():
            if f.control == "down":
                downs += 1
            elif f.control == "up":
                ups += 1
            else:
                n += 1
            conns.add(f.conn)
        self.assertEqual((downs, ups, n), (5, 6, 44296))
        self.assertEqual(conns, {f"rec#{k}" for k in range(1, 7)})
        self.assertEqual(self.m.stats["bad_lines"], 0)

    def test_comp_id_changes_at_the_two_season_boundaries(self) -> None:
        sw = self.m.season_switches()
        self.assertEqual([s for _, s in sw], ["225", "226"])
        self.assertAlmostEqual(sw[0][0], 683.14, delta=0.1)
        self.assertAlmostEqual(sw[1][0], 3627.77, delta=0.1)
        changes = []
        prev = None
        for k in range(0, 3800 * 4):
            c = self.m.comp_id_at(k * 0.25)
            if c != prev:
                changes.append(c)
                prev = c
        self.assertEqual(changes, ["rec-224", "rec-225", "rec-226"])

    def test_round_opens_match_the_findings(self) -> None:
        got = {(rs.season, rs.round): self.m.unix_opens[rs.date] for rs in self.m.rounds}
        self.assertEqual(sorted(got), [("224", 3), ("225", 1), ("225", 2), ("225", 3), ("226", 1)])
        for key, want in self.FIT.items():
            self.assertAlmostEqual(got[key], want, delta=0.5, msg=str(key))

    def test_settlement_only_for_complete_seasons(self) -> None:
        self.assertIsNotNone(self.m.settlement("224"))
        self.assertIsNotNone(self.m.settlement("225"))
        self.assertIsNone(self.m.settlement("226"))
        finals = [(s, r) for _, s, r, st, _ in self.m.reports if st == "final"]
        self.assertEqual(finals, [("224", 3), ("225", 1), ("225", 2), ("225", 3)])


class TestAcceptance92(unittest.TestCase):
    """``sim/run_recording.py --mode dryrun`` and ``--mode book`` with a scripted ``lawok all`` (CTCBOT_SLOW=1)."""

    def _run(self, mode: str) -> dict:
        testpaths.slow()
        testpaths.require(testpaths.RECORDING)
        testpaths.require(testpaths.HISTORY)
        testpaths.integration("ctcbot.app.core", "ctcbot.exec.order_manager", "ctcbot.models.engine_adapter",
                              "ctcbot.strategies.runner", "ctcbot.models.laws")
        from sim import run_recording
        out_dir = os.path.join(BOT, "sim", "out", os.path.basename(testpaths.tmpdir(f"e_rec_{mode}")))
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            code = run_recording.main(["--recording", testpaths.RECORDING, "--history", testpaths.HISTORY,
                                       "--mode", mode, "--command", "1:lawok all", "--out", out_dir])
        with open(os.path.join(out_dir, "report.json")) as f:
            rep = json.load(f)
        rep["_code"] = code
        rep["_stdout"] = buf.getvalue()
        return rep

    def test_dryrun(self) -> None:
        rep = self._run("dryrun")
        failed = [c for c in rep["checks"] if not c["ok"] and c.get("blocking", True)]
        self.assertEqual(failed, [], rep["_stdout"][-4000:])

    def test_book(self) -> None:
        rep = self._run("book")
        failed = [c for c in rep["checks"] if not c["ok"] and c.get("blocking", True)]
        self.assertEqual(failed, [], rep["_stdout"][-4000:])


if __name__ == "__main__":
    unittest.main()
