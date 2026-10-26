"""Group E: research market, fair-value cache, reports, and the section 9.3 research acceptance (SLOW).

Unit tests use synthetic seasons and caches written under ``tests/_tmp`` with the fakes of ``test_e_exchange``.
``TestRealData`` reads the research history and the holdout cache (skipped when absent). ``TestAcceptance93``
runs ``sim/run_research.py`` with every group's real modules; it needs ``CTCBOT_SLOW=1``.
"""
from __future__ import annotations

import bisect
import json
import math
import os
import random
import sys
import unittest
from typing import Any

sys.dont_write_bytecode = True
BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BOT not in sys.path:
    sys.path.insert(0, BOT)

import testpaths  # noqa: E402
from ctcbot.types import SYMBOLS, FairValueSnapshot  # noqa: E402
from test_e_exchange import (BASE, CASE, FakeConfig, FakeSchedule, LinearCost, make_market,  # noqa: E402
                             market_time)

from sim import report as R  # noqa: E402
from sim.fv_cache import CachedFairValues, cache_file  # noqa: E402
from sim.research_market import ResearchMarket, SnapshotBuilder  # noqa: E402


def storm_msg(r: int, tau: float, seq: int, age: float, **kw: Any) -> dict:
    m = {"type": "storm", "round": r, "storm": f"S{r}", "time": market_time(r, tau), "age": age, "x": 500.0 - age,
         "y": 700.0 - age, "vx": -1.0, "vy": -1.0, "radius": 80.0, "intensity": 1.0 + age / 1000, "seq": seq}
    m.update(kw)
    return m


def synthetic_feed() -> list[dict]:
    feed: list[dict] = []
    seq = 0
    for r in (1, 2, 3):
        feed.append({"type": "storm_formed", "round": r, "storm": f"S{r}", "time": market_time(r, 60.0),
                     "headline": f"S{r} has formed.", "seq": seq})
        seq += 1
        for k in range(0, 21):                       # obs every 2 s of age 0..40 at real 60..100
            feed.append(storm_msg(r, 60.0 + 2 * k, seq, 2.0 * k))
            seq += 1
        feed.append({"type": "advisory", "round": r, "storm": f"S{r}", "time": market_time(r, 90.0),
                     "source": "official", "number": 1, "age": 30, "outlook": "direct", "headline": "Adv 1",
                     "hurricane_conditions": {"cascadia_prime": 0.5}, "cone": [], "seq": seq})
        seq += 1
        feed.append({"type": "impact", "round": r, "storm": f"S{r}", "time": market_time(r, 95.0),
                     "island": "palm_atoll", "age": 35, "headline": "impact", "seq": seq})
        seq += 1
        for tau, stage in ((570.0, "preliminary"), (740.0, "final")):
            feed.append({"type": "damage_report", "round": r, "storm": f"S{r}", "time": market_time(r, tau),
                         "stage": stage, "damage_pct": {"PORT": 1.0}, "industry_loss_musd": 10.0 * r,
                         "headline": f"{stage} report", "seq": seq})
            seq += 1
    return feed


def collect(m: ResearchMarket) -> list:
    return list(m.frames())


# ----------------------------------------------------------------------------------------- market
class TestResearchMarket(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        price = (lambda r, i, s: BASE[s] * (1 + 0.001 * i) * (1 + 0.1 * (r - 1)))
        cls.m, cls.cfg, cls.cost = make_market(price=price, feed=synthetic_feed())
        cls.frames = collect(cls.m)

    def test_round_opens_and_specs(self) -> None:
        self.assertEqual([rs.open_t for rs in self.m.rounds], [0.0, 810.0, 1620.0])
        self.assertEqual([rs.close_t for rs in self.m.rounds], [780.0, 1590.0, 2400.0])
        self.assertEqual(self.m.rounds[1].date, "1700-03-02")
        self.assertIsNone(self.m.round_at(780.0))
        self.assertEqual(self.m.round_at(811.0).round, 2)

    def test_first_frame_is_connection_up(self) -> None:
        f0 = self.frames[0]
        self.assertEqual((f0.t, f0.control, f0.conn), (0.0, "up", "sim#1"))

    def test_row_frames(self) -> None:
        rows = [f for f in self.frames if f.frame.get("type") in ("quote", "orderbook")]
        self.assertEqual(len(rows), 3 * 390 * 24)
        times = sorted({f.t for f in rows})
        self.assertEqual(times[:3], [0.0, 2.0, 4.0])
        self.assertIn(810.0, times)
        self.assertNotIn(780.0, times)
        q = next(f.frame for f in rows if f.t == 100.0 and f.frame["type"] == "quote" and f.frame["symbol"] == "PORT")
        px = BASE["PORT"] * 1.05
        self.assertEqual(q["bid"], math.floor(px * (1 - 10e-4) * 100 + 1e-6) / 100)
        self.assertEqual(q["ask"], math.ceil(px * (1 + 10e-4) * 100 - 1e-6) / 100)
        b = next(f.frame for f in rows if f.t == 100.0 and f.frame["type"] == "orderbook" and f.frame["symbol"] == "PORT")
        self.assertEqual([lv["quantity"] for lv in b["asks"]], [20, 14, 56, 50, 460])
        asks = [lv["price"] for lv in b["asks"]]
        bids = [lv["price"] for lv in b["bids"]]
        self.assertEqual(asks, sorted(asks))
        self.assertEqual(bids, sorted(bids, reverse=True))
        # level 2 covers x in [0.10, 0.17]: walk increment (0.17 * 27 - 0.1 * 20) / 0.07 = 37 bp
        self.assertEqual(asks[1], math.ceil(px * (1 + 37e-4) * 100 - 1e-6) / 100)
        self.assertEqual((b["asks"][0]["price"], b["bids"][0]["price"]), (q["ask"], q["bid"]))

    def test_book_timestamps_strictly_increase(self) -> None:
        last: dict = {}
        for f in self.frames:
            fr = f.frame
            if fr.get("type") in ("quote", "orderbook"):
                key = (fr["type"], fr["symbol"])
                if key in last:
                    self.assertGreater(fr["timestamp"], last[key])
                last[key] = fr["timestamp"]

    def test_news_times(self) -> None:
        news = [(f.t, f.frame["message"]) for f in self.frames if f.frame.get("type") == "news"
                and f.frame["message"].get("type") != "snapshot"]
        self.assertEqual(len(news), len(synthetic_feed()))
        formed = [t for t, m in news if m["type"] == "storm_formed"]
        self.assertEqual(formed, [60.0, 870.0, 1680.0])
        finals = [t for t, m in news if m["type"] == "damage_report" and m["stage"] == "final"]
        self.assertEqual(finals, [740.0, 1550.0, 2360.0])
        order = [(t, m["seq"]) for t, m in news]
        self.assertEqual(order, sorted(order))          # time order, seq order within one instant

    def test_order_within_an_instant(self) -> None:
        at60 = [f.frame for f in self.frames if f.t == 60.0]
        kinds = [fr["type"] for fr in at60]
        self.assertEqual(kinds[:24], ["quote", "orderbook"] * 12)
        self.assertEqual([fr["message"]["type"] for fr in at60[24:]], ["storm_formed", "storm"])

    def test_snapshots_every_30s_with_phases(self) -> None:
        snaps = [(f.t, f.frame["message"]) for f in self.frames if f.frame.get("type") == "news"
                 and f.frame["message"].get("type") == "snapshot"]
        self.assertEqual([t for t, _ in snaps[:3]], [0.5, 30.5, 60.5])
        by_t = dict(snaps)
        self.assertEqual((by_t[0.5]["phase"], by_t[0.5]["round"], by_t[0.5]["last_seq"]), ("pre_storm", 1, -1))
        self.assertEqual(by_t[0.5]["time"], "1700-03-01T09:30:00")
        self.assertEqual(by_t[30.5]["time"], "1700-03-01T09:45:00")
        self.assertEqual((by_t[60.5]["phase"], by_t[60.5]["last_seq"]), ("storm", 1))
        self.assertEqual((by_t[780.5]["phase"], by_t[780.5]["round"]), ("closed", 1))
        self.assertEqual(by_t[780.5]["time"], "1700-03-01T16:00:00")
        s2 = by_t[810.5]
        self.assertEqual((s2["phase"], s2["round"], s2["obs"], s2["track"]), ("pre_storm", 2, None, []))
        self.assertEqual(len(s2["damage"]), 2)
        self.assertEqual(by_t[2400.5]["phase"], "closed")
        self.assertEqual(self.m.default_t_end(), 2402.5)

    def test_snapshot_schema_matches_testnet(self) -> None:
        snap = next(f.frame["message"] for f in self.frames if f.t == 120.5)
        self.assertEqual(set(snap), {"type", "round", "storm", "time", "phase", "last_seq", "obs", "track",
                                     "advisory", "impacts", "damage", "headlines"})
        self.assertEqual(set(snap["obs"]), {"time", "age", "x", "y", "vx", "vy", "radius", "intensity"})
        self.assertEqual(snap["obs"]["age"], 40.0)
        self.assertEqual([p[0] for p in snap["track"]], [0.0, 8.0, 16.0, 24.0, 32.0, 40.0])
        self.assertTrue(not ({"type", "round", "storm", "seq"} & set(snap["advisory"])))
        self.assertEqual(snap["advisory"]["number"], 1)
        self.assertEqual(snap["impacts"], [{"island": "palm_atoll", "age": 35, "time": market_time(1, 95.0)}])
        self.assertEqual(snap["storm"], "S1")
        self.assertLessEqual(len(snap["headlines"]), 8)

    def test_pause_only_and_no_snapshots(self) -> None:
        m, _, _ = make_market(feed=synthetic_feed(), snapshots="pause")
        snaps = [f.t for f in m.frames() if f.frame.get("type") == "news" and f.frame["message"]["type"] == "snapshot"]
        self.assertEqual(snaps, [780.5, 1590.5, 2400.5])
        m, _, _ = make_market(feed=synthetic_feed(), snapshots="none")
        self.assertFalse(any(f.frame.get("type") == "news" and f.frame["message"]["type"] == "snapshot"
                             for f in m.frames()))
        with self.assertRaises(ValueError):
            make_market(snapshots="sometimes")

    def test_drop_seq_removes_delivery_but_not_server_state(self) -> None:
        m, _, _ = make_market(feed=synthetic_feed(), drop_seqs=[5, 6])
        fr = list(m.frames())
        seqs = {f.frame["message"].get("seq") for f in fr if f.frame.get("type") == "news"}
        self.assertNotIn(5, seqs)
        self.assertNotIn(6, seqs)
        snap = next(f.frame["message"] for f in fr if f.t == 90.5)
        self.assertGreater(snap["last_seq"], 6)

    def test_marks_and_settlement(self) -> None:
        self.assertAlmostEqual(self.m.mark("PORT", 100.0), BASE["PORT"] * 1.05)
        self.assertAlmostEqual(self.m.mark("PORT", 795.0), BASE["PORT"] * (1 + 0.389))
        self.assertAlmostEqual(self.m.round_marks(1)["GRID"], BASE["GRID"] * 1.389)
        self.assertAlmostEqual(self.m.round_marks(3)["GRID"], BASE["GRID"] * 1.1)
        self.assertEqual(self.m.settlement("901"), self.m.settle)
        self.assertIsNone(self.m.settlement("902"))

    def test_season_argument_and_missing_season(self) -> None:
        hist = os.path.dirname(self.m.dir)
        m = ResearchMarket(hist, "research:901", FakeConfig(), LinearCost(), case=CASE, schedule=FakeSchedule())
        self.assertEqual(m.season, 901)
        with self.assertRaises(FileNotFoundError):
            ResearchMarket(hist, 902, FakeConfig(), LinearCost(), case=CASE, schedule=FakeSchedule())


class TestSnapshotBuilder(unittest.TestCase):
    def test_track_grid_plus_latest(self) -> None:
        b = SnapshotBuilder()
        for k in range(0, 12):
            b.ingest(storm_msg(1, 60 + 2 * k, k, 2.0 * k))
        s = b.snapshot(1, "storm", "x")
        self.assertEqual([p[0] for p in s["track"]], [0.0, 8.0, 16.0, 22.0])
        self.assertEqual(s["last_seq"], 11)
        self.assertIsNone(b.snapshot(2, "pre_storm", "y")["obs"])


# ----------------------------------------------------------------------------------------- fv cache
def write_cache(path: str) -> str:
    def ent(r: int, t: float, mu0: float) -> list:
        return [r, t] + [mu0 + k for k in range(12)] + [0.5 + 0.01 * k for k in range(12)]

    entries = [ent(1, -1, 10.0), ent(1, 60.0, 11.0), ent(1, 62.0, 12.0), ent(1, 100.0, 13.0),
               ent(2, 60.0, 21.0), ent(2, 740.0, 22.0)]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"season": 999, "lib": "holdout", "symbols": list(SYMBOLS), "entries": entries}, f)
    return path


def _bt_fair_at(entries: list, r: int, t: float) -> tuple:
    """Inline copy of backtest.Season's cache handling and fair_at."""
    NS = 12
    fv: dict = {1: [], 2: [], 3: []}
    last = None
    for e in entries:
        rr, tt = int(e[0]), float(e[1])
        if rr > 1 and not fv[rr] and last is not None:
            fv[rr].append((-1.0, last[0], last[1]))
        item = (tt, e[2:2 + NS], e[2 + NS:2 + 2 * NS])
        fv[rr].append(item)
        last = (item[1], item[2])
    for k in (2, 3):
        if not fv[k]:
            fv[k].append((-1.0, last[0], last[1]))
    fvt = {k: [x[0] for x in fv[k]] for k in (1, 2, 3)}
    i = bisect.bisect_right(fvt[r], t + 1e-9) - 1
    x = fv[r][max(i, 0)]
    return tuple(x[1]), tuple(x[2])


class TestCachedFairValues(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = testpaths.tmpdir("e_fv")
        self.path = write_cache(os.path.join(self.dir, "fv_holdout_999.json"))
        self.fv = CachedFairValues(self.path, FakeSchedule(), baskets=CASE["etfs"] and
                                   {e: v["basket"] for e, v in CASE["etfs"].items()})
        self.calls = 0
        self.fv.set_listener(self._cb)

    def _cb(self) -> None:
        self.calls += 1

    def msg(self, r: int, tau: float, seq: int, typ: str = "storm") -> dict:
        return {"type": typ, "round": r, "time": market_time(r, tau), "seq": seq}

    def test_fair_at_equals_backtest(self) -> None:
        with open(self.path) as f:
            entries = json.load(f)["entries"]
        for r in (1, 2, 3):
            for t in (-5.0, -1.0, 0.0, 59.99, 60.0, 61.0, 62.0, 99.0, 100.0, 500.0, 740.0, 760.0):
                mu, sd = self.fv.fair_at(r, t)
                bmu, bsd = _bt_fair_at(entries, r, t)
                self.assertEqual(mu, bmu, (r, t))
                self.assertEqual(sd, bsd, (r, t))

    def test_nothing_before_the_first_message(self) -> None:
        self.assertIsNone(self.fv.latest())
        h = self.fv.health(5.0)
        self.assertTrue(h.alive)
        self.assertIsNone(h.oldest_pending_age_s)

    def test_publish_on_new_entry_and_on_new_seq(self) -> None:
        self.fv.submit([self.msg(1, 60.0, 0, "storm_formed"), self.msg(1, 60.0, 1)], 60.0)
        a = self.fv.latest()
        self.assertIsInstance(a, FairValueSnapshot)
        self.assertEqual(self.calls, 1)
        self.assertEqual((a.version, a.round, a.seq_hi, a.included_mono, a.ok, a.compute_ms), (1, 1, 1, 60.0,
                                                                                               True, 0.0))
        self.assertEqual(a.values["PORT"], (11.0, 0.5))
        self.assertEqual(len(a.values), 12)
        self.assertEqual(a.diag, {})
        self.fv.submit([self.msg(1, 61.0, 2)], 61.0)        # same entry, higher seq: still published
        b = self.fv.latest()
        self.assertEqual((b.version, b.seq_hi, b.values["PORT"]), (2, 2, (11.0, 0.5)))
        self.assertEqual(b.engine_version, a.engine_version)
        self.fv.submit([self.msg(1, 61.0, 2)], 61.5)        # a duplicate changes nothing
        self.assertEqual((self.fv.latest().version, self.calls), (2, 2))
        self.fv.submit([self.msg(1, 62.0, 3)], 62.0)
        c = self.fv.latest()
        self.assertEqual((c.values["PORT"][0], c.engine_version), (12.0, a.engine_version + 1))

    def test_carry_in_for_later_rounds(self) -> None:
        self.fv.submit([self.msg(1, 150.0, 10)], 150.0)
        self.fv.submit([self.msg(2, 30.0, 11)], 840.0)          # before round 2's first entry: carry-in
        self.assertEqual(self.fv.latest().values["PORT"][0], 13.0)
        self.assertEqual(self.fv.latest().round, 2)
        self.fv.submit([self.msg(3, 100.0, 12)], 1720.0)       # round 3 has no entries
        self.assertEqual(self.fv.latest().values["PORT"][0], 22.0)

    def test_snapshots_count_for_seq_hi_only_when_closed(self) -> None:
        self.fv.submit([self.msg(1, 60.0, 1)], 60.0)
        closed = {"type": "snapshot", "round": 1, "time": market_time(1, 900.0), "phase": "closed", "last_seq": 40}
        self.fv.submit([closed], 800.0)
        self.assertEqual(self.fv.latest().version, 1)          # a closed snapshot alone does not publish
        live = {"type": "snapshot", "round": 1, "time": market_time(1, 100.0), "phase": "storm", "last_seq": 41}
        self.fv.submit([live], 101.0)
        self.assertEqual((self.fv.latest().seq_hi, self.fv.latest().values["PORT"][0]), (41, 13.0))

    def test_adjustments_and_etf_propagation(self) -> None:
        self.fv.submit([self.msg(1, 60.0, 1)], 60.0)
        base = self.fv.latest()
        self.fv.set_adjustment("PORT", mult=1.0, add=-2.0)
        adj = self.fv.latest()
        self.assertEqual(adj.version, base.version + 1)
        self.assertAlmostEqual(adj.values["PORT"][0], base.values["PORT"][0] - 2.0)
        self.assertAlmostEqual(adj.values["ARCH"][0], base.values["ARCH"][0] - 2.0)   # PORT weight 1 in ARCH
        self.assertEqual(adj.values["GRID"], base.values["GRID"])
        self.fv.set_adjustment("GRID", mult=0.0, add=30.0)                            # absolute price
        self.assertEqual(self.fv.latest().values["GRID"], (30.0, 0.0))
        self.fv.set_adjustment("PORT")
        self.fv.set_adjustment("GRID")
        self.assertEqual(self.fv.latest().values, base.values)
        self.fv.override("settle.x", 1)
        self.assertEqual(self.fv.overrides, {"settle.x": 1})

    def test_reset_season(self) -> None:
        self.fv.submit([self.msg(1, 60.0, 1)], 60.0)
        v = self.fv.latest().version
        self.fv.reset_season(3, None)
        self.assertIsNone(self.fv.latest())
        self.fv.submit([self.msg(1, 62.0, 0)], 900.0)
        s = self.fv.latest()
        self.assertEqual((s.season_epoch, s.seq_hi, s.version), (3, 0, v + 1))

    def test_cache_dir_resolution(self) -> None:
        self.assertEqual(cache_file(self.dir, 999), self.path)
        fv = CachedFairValues(self.dir, FakeSchedule(), season=999)
        self.assertEqual(fv.path, self.path)
        with self.assertRaises(FileNotFoundError):
            cache_file(self.dir, 998)
        with self.assertRaises(ValueError):
            CachedFairValues(self.dir, FakeSchedule())


# ----------------------------------------------------------------------------------------- reports
def fake_run(pnl: float, fills: list[dict], orders: dict, rounds: dict | None = None, acts: int = 100) -> dict:
    return {"exchange": {"seasons": {"401": {"pnl": pnl, "marks": "settlement",
                                             "round_pnl": rounds or {"1": 1.0, "2": 2.0, "3": pnl - 3.0}}},
                         "fills": fills, "rate": {"actions": acts, "max_60s": 50, "max_61s": 51, "http_429": 0},
                         "violations": {"rate": 0, "limit_shrink": 0}, "repl_hits": 2, "live_orders": 0,
                         "max_resting": {"PORT": 2}, "early_marketable": []},
            "orders": orders, "log_counts": {"exception": 0}, "alerts": {}, "timing": {"wall_s": 1.0}}


class TestReport(unittest.TestCase):
    def test_rec_baseline_list(self) -> None:
        self.assertEqual(len(R.REC_PER_SEASON), 40)
        self.assertAlmostEqual(sum(R.REC_PER_SEASON.values()) / 40, 19660.0, delta=1.0)
        self.assertEqual(R.REC_PER_SEASON[426], -800.0)

    def test_season_report_attribution(self) -> None:
        fills = [{"order_id": "o1", "season": "401", "round": 1, "symbol": "PORT", "side": "buy", "qty": 10,
                  "price": 50.0, "ref_px": 49.9, "cost_frac": 0.002, "kind": "taker"},
                 {"order_id": "o2", "season": "401", "round": 3, "symbol": "PORT", "side": "sell", "qty": 4,
                  "price": 52.0, "ref_px": 52.1, "cost_frac": 0.0019, "kind": "taker"},
                 {"order_id": "o3", "season": "401", "round": 2, "symbol": "GRID", "side": "sell", "qty": 5,
                  "price": 40.0, "ref_px": 40.1, "cost_frac": 0.0025, "kind": "taker"}]
        orders = {"o1": {"strategy": "h1"}, "o2": {"strategy": "s3"}}
        rep = R.season_report(401, fake_run(123.0, fills, orders), {"PORT": 51.0, "GRID": 41.0})
        self.assertAlmostEqual(rep["comp"]["h1"][0], 10 * (51.0 - 50.0))
        self.assertAlmostEqual(rep["comp"]["s3"][0], -4 * (51.0 - 52.0))
        self.assertAlmostEqual(rep["comp"]["unknown"][0], -5 * (41.0 - 40.0))
        self.assertAlmostEqual(rep["group"], 10.0 + 4.0)
        self.assertAlmostEqual(rep["cost"], 10 * 49.9 * 0.002 + 4 * 52.1 * 0.0019 + 5 * 40.1 * 0.0025)
        self.assertEqual(rep["round"], [1.0, 2.0, 120.0])
        self.assertEqual((rep["act"], rep["max60"], rep["repl_hits"], rep["violations_total"]), (100, 50, 2, 0))
        self.assertEqual(rep["comp_round"]["h1"], {"1": 10.0})

    def test_summarize_row_and_paired(self) -> None:
        reps = []
        rng = random.Random(2)
        for n in range(401, 441):
            p = R.REC_PER_SEASON[n] + rng.uniform(-500, 500)
            reps.append({"season": n, "pnl": p, "round": [p / 3] * 3, "comp": {"s3": [p, 1.0, 10.0]}, "act": 1200,
                         "max60": 140, "max61": 141, "repl_hits": 0, "cost": 9000.0, "group": p,
                         "violations": {"rate": 0}, "sym": {}})
        s = R.summarize(reps)
        self.assertEqual(s["n"], 40)
        self.assertAlmostEqual(s["mean"], sum(r["pnl"] for r in reps) / 40)
        self.assertEqual(s["max60"], 140)
        line = R.row("bot", s)
        self.assertTrue(line.startswith("| bot | "))
        self.assertEqual(line.count("|"), R.HDR.split("\n")[0].count("|"))
        pr = R.paired(s["per_season"], R.REC_PER_SEASON)
        self.assertEqual(pr["n"], 40)
        self.assertGreater(pr["r"], 0.99)
        self.assertLess(pr["median_abs_diff"], 500)
        checks = {c["check"]: c["ok"] for c in R.parity_checks(s)}
        self.assertTrue(all(checks.values()), checks)
        bad = dict(s, act=2000)
        self.assertFalse(all(c["ok"] for c in R.parity_checks(bad)))
        dc = {c["check"]: c["ok"] for c in R.default_checks(s, {"mean": 12000.0})}
        self.assertFalse(dc["abl_edge within 0.70-1.10 x REC (13,762 to 21,626)"])
        self.assertTrue(dc["mean >= 0.60 x REC (11,796)"])
        self.assertIn("[PASS]", R.format_checks(R.default_checks(s)))

    def test_attribution(self) -> None:
        base = {"mean": 15000.0, "per_season": {401: 15000.0, 402: 15000.0}}
        abl = {"abl_edge": {"mean": 18000.0, "per_season": {401: 17000.0, 402: 19000.0}},
               "abl_rein": {"mean": 15500.0, "per_season": {401: 15500.0, 402: 15500.0}}}
        at = R.attribution(base, abl)
        self.assertAlmostEqual(at["gap_to_rec"], 4660.0)
        self.assertAlmostEqual(at["explained"], 3500.0)
        self.assertAlmostEqual(at["residual"], 1160.0)
        self.assertEqual([r["overlay"] for r in at["rows"]], ["abl_edge", "abl_rein"])

    def test_empty_summary(self) -> None:
        self.assertEqual(R.summarize([]), {"n": 0})
        self.assertIn("no seasons", R.row("x", {"n": 0}))
        self.assertEqual(R.paired({}, R.REC_PER_SEASON)["n"], 0)


# ----------------------------------------------------------------------------------------- real data
class TestRealData(unittest.TestCase):
    def test_real_cache_replays_backtest_fair_at(self) -> None:
        path = testpaths.require(os.path.join(testpaths.FV_CACHE, "fv_holdout_401.json"))
        hist = testpaths.require(os.path.join(testpaths.HISTORY, "season-401"))
        with open(path) as f:
            entries = json.load(f)["entries"]
        fv = CachedFairValues(path, FakeSchedule())
        rng = random.Random(4)
        for _ in range(300):
            r, t = rng.choice((1, 2, 3)), rng.uniform(-2, 780)
            self.assertEqual(tuple(fv.fair_at(r, t)[0]), _bt_fair_at(entries, r, t)[0])
        sch = FakeSchedule()
        with open(os.path.join(hist, "feed.jsonl")) as f:
            msgs = [json.loads(x) for x in f if x.strip()]
        for m in msgs:
            fv.submit([m], 0.0)
            snap = fv.latest()
            _, t = sch.market_to_t(m["time"], sch.speed)
            mu = _bt_fair_at(entries, int(m["round"]), t)[0]
            self.assertEqual(tuple(snap.values[s][0] for s in SYMBOLS), mu)
        self.assertEqual(fv.latest().seq_hi, max(m["seq"] for m in msgs))

    def test_real_season_loads(self) -> None:
        testpaths.require(os.path.join(testpaths.HISTORY, "season-401"))
        cfg = FakeConfig()
        m = ResearchMarket(testpaths.HISTORY, 401, cfg, LinearCost(), case=CASE, schedule=FakeSchedule())
        self.assertEqual(m.dates[1], "1626-12-14")
        self.assertEqual(len(m.news), 869)
        self.assertEqual(m.news[0][0], 60.0)
        self.assertAlmostEqual(m.settle["PORT"], 48.64)


# ----------------------------------------------------------------------------------------- section 9.3
class TestAcceptance93(unittest.TestCase):
    """REC parity and default-config runs on research seasons 401-440 (CTCBOT_SLOW=1)."""

    def _run(self, *extra: str) -> dict:
        testpaths.slow()
        testpaths.require(testpaths.HISTORY)
        testpaths.require(testpaths.FV_CACHE)
        testpaths.integration("ctcbot.app.core", "ctcbot.exec.order_manager", "ctcbot.exec.reconcile",
                              "ctcbot.strategies.runner", "ctcbot.models.cost_model", "ctcbot.models.laws")
        import io
        from contextlib import redirect_stderr, redirect_stdout

        from sim import run_research
        out = testpaths.tmpdir("e_rr")
        out_dir = os.path.join(BOT, "sim", "out", os.path.basename(out))
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            code = run_research.main(["--history", testpaths.HISTORY, "--fv-cache", testpaths.FV_CACHE,
                                      "--seasons", "401-440", "--out", out_dir, *extra])
        with open(os.path.join(out_dir, "report.json")) as f:
            rep = json.load(f)
        rep["_code"] = code
        rep["_stdout"] = buf.getvalue()
        return rep

    def test_parity_run(self) -> None:
        rep = self._run("--profile", os.path.join(BOT, "config", "rec_parity.json"), "--h1-off")
        failed = [c for c in rep["checks"] if not c["ok"] and c.get("blocking", True)]
        self.assertEqual(failed, [], rep["_stdout"][-3000:])

    def test_default_run_and_ablations(self) -> None:
        rep = self._run("--ablations")
        failed = [c for c in rep["checks"] if not c["ok"] and c.get("blocking", True)]
        self.assertEqual(failed, [], rep["_stdout"][-3000:])
        self.assertIn("attribution", rep)


class TestScriptHelpers(unittest.TestCase):
    def test_parse_seasons_and_commands(self) -> None:
        from sim.run_recording import parse_command
        from sim.run_research import parse_seasons
        self.assertEqual(parse_seasons("401-403,410"), [401, 402, 403, 410])
        self.assertEqual(parse_seasons("5"), [5])
        with self.assertRaises(ValueError):
            parse_seasons("9-3")
        self.assertEqual(parse_command("1:lawok all"), (1.0, "lawok all"))
        self.assertEqual(parse_command("1792766900.5:x1 PORT"), (1792766900.5, "x1 PORT"))
        with self.assertRaises(ValueError):
            parse_command("lawok")

    def test_overlays_are_sim_only(self) -> None:
        import glob
        files = [os.path.join(BOT, "config", "rec_parity.json")] + sorted(
            glob.glob(os.path.join(BOT, "sim", "ablations", "abl_*.json")))
        self.assertEqual(len(files), 7)
        for p in files:
            with open(p) as f:
                d = json.load(f)
            self.assertIs(d.get("live_allowed"), False, p)
        with open(files[0]) as f:
            par = json.load(f)
        self.assertEqual(par["rate"], {"window_s": 60.0, "floors": {"KILL": 0, "SNIPE": 0, "HEDGE": 0, "TAKE": 0}})
        self.assertEqual(par["strategies"]["s3"]["edge_ref"], "mid")
        self.assertNotIn("REIN", par["strategies"]["s1r3"]["symbols"])
        # fill_sanity_n: REC has no fill-sanity breaker (DESIGN.md §12 item 5)
        self.assertEqual(par["risk"], {"max_marketable_from_mid": 0.08, "fill_sanity_n": 1000000})


if __name__ == "__main__":
    unittest.main()
