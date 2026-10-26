"""Group C: ``ctcbot.models.cost_model`` (DESIGN.md section 9.1 C, cost_model)."""
from __future__ import annotations

import bisect
import json
import os
import random
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402
from test_c_support import FakeConfig  # noqa: E402
from ctcbot.models.cost_model import MODEL_PHASES, CostModel  # noqa: E402
from ctcbot.types import SYMBOLS, Side  # noqa: E402

# DESIGN.md section 6 "Bot units": entry cost s3_start / storm / after, exit band s3_start / storm (bp)
TABLE = {
    "PORT": ((315, 123, 101), (114.4, 24.2)), "ISLE": ((165, 107, 99), (43.2, 16.4)),
    "AQUA": ((170, 112, 98), (46.7, 18.5)), "GRID": ((205, 107, 99), (62.8, 17.1)),
    "FERRY": ((138, 117, 102), (31.7, 21.4)), "RECON": ((195, 110, 99), (57.2, 18.4)),
    "MAT": ((164, 110, 100), (42.8, 18.0)), "GEN": ((161, 109, 100), (42.0, 17.8)),
    "HAVEN": ((342, 138, 104), (125.4, 31.2)), "REIN": ((100, 100, 98), (13.1, 13.0)),
    "ARCH": ((129, 73, 58), (46.2, 19.1)), "REBLD": ((101, 69, 55), (33.1, 17.3)),
}


# ---------------------------------------------------------------- inline copy of r_sim's prep / interp / walk_cost
def rsim_prep(raw):
    out = {}
    for s in SYMBOLS:
        L = raw[s]["L"]
        d = {"L": L, "ph": {}, "rho": raw[s]["replenish_storm"]["depth50_100"]["within_2s"]}
        for ph in MODEL_PHASES:
            p = raw[s][ph]
            xs = sorted(float(k) for k in p["table"])
            ys = [p["table"][k] for k in sorted(p["table"], key=float)]
            cl = p["clips"]
            pts = []
            for name in ("10sh", "L/8", "L/4", "L/2", "L"):
                c = cl[name]
                if c["mean"] and c["buy_mean"] is not None and c["sell_mean"] is not None and c["mean"] > 0:
                    pts.append((c["q"] / L, c["buy_mean"] / c["mean"], c["sell_mean"] / c["mean"]))
            pts.sort()
            d["ph"][ph] = {"xs": xs, "ys": ys, "rx": [q[0] for q in pts], "rb": [q[1] for q in pts],
                           "rs": [q[2] for q in pts], "depth": p["inner_depth_within_3pct_over_L_median"],
                           "hs": ys[0]}
        out[s] = d
    return out


def rsim_interp(xs, ys, x):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    k = bisect.bisect_right(xs, x)
    x0, x1 = xs[k - 1], xs[k]
    return ys[k - 1] + (ys[k] - ys[k - 1]) * (x - x0) / (x1 - x0)


def rsim_walk_cost(CM, sym, ph, x, side, side_specific=True):
    if x <= 0:
        return 0.0
    p = CM[sym]["ph"][ph]
    c = rsim_interp(p["xs"], p["ys"], x)
    if side_specific and p["rx"]:
        r = rsim_interp(p["rx"], p["rb"] if side > 0 else p["rs"], x)
        c *= r
    return c


class CostModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        testpaths.require(testpaths.COST_MODEL)
        cls.cfg = FakeConfig()
        cls.cm = CostModel(testpaths.COST_MODEL, cls.cfg)
        with open(testpaths.COST_MODEL) as f:
            cls.raw = json.load(f)
        cls.ref = rsim_prep(cls.raw)

    def test_reproduces_section6_table(self):
        for s, ((e0, e1, e2), (b0, b1)) in TABLE.items():
            for ph, want in (("s3_start", e0), ("storm", e1), ("after", e2)):
                got = self.cm.entry_cost_bp(s, ph, 1.0 / 12)
                self.assertLessEqual(abs(got - want), 0.5, f"{s} {ph} entry {got:.2f} vs {want}")
            for ph, want in (("s3_start", b0), ("storm", b1)):
                got = self.cm.half_spread_bp(s, ph)
                self.assertLessEqual(abs(got - want), 0.1 + 1e-9, f"{s} {ph} band {got:.2f} vs {want}")

    def test_walk_matches_rsim_at_random_points(self):
        rng = random.Random(20261023)
        for _ in range(200):
            s = rng.choice(SYMBOLS)
            ph = rng.choice(MODEL_PHASES)
            x = rng.uniform(0.0, 1.2)
            sg = rng.choice((1, -1))
            want = rsim_walk_cost(self.ref, s, ph, x, sg)
            got = self.cm.walk_bp(s, ph, x, Side.BUY if sg > 0 else Side.SELL)
            self.assertAlmostEqual(got, want, places=9, msg=f"{s} {ph} x={x:.4f} side={sg}")
            self.assertAlmostEqual(self.cm.walk_bp(s, ph, x, sg), want, places=9)

    def test_mean_table_without_side(self):
        for s in ("PORT", "ARCH"):
            p = self.ref[s]["ph"]["storm"]
            self.assertAlmostEqual(self.cm.walk_bp(s, "storm", 0.25), rsim_interp(p["xs"], p["ys"], 0.25), places=9)
            self.assertAlmostEqual(self.cm.walk_bp(s, "storm", 0.25, None), self.raw[s]["storm"]["table"]["0.2500"],
                                   places=6)
        self.assertEqual(self.cm.walk_bp("PORT", "storm", 0.0, Side.BUY), 0.0)
        self.assertEqual(self.cm.walk_bp("PORT", "storm", -1.0), 0.0)

    def test_depth_rho_half_spread_and_limits(self):
        for s in SYMBOLS:
            self.assertEqual(self.cm.L(s), self.raw[s]["L"])
            self.assertAlmostEqual(self.cm.rho(s), self.raw[s]["replenish_storm"]["depth50_100"]["within_2s"])
            for ph in ("storm", "s3_start", "after"):
                self.assertAlmostEqual(self.cm.depth(s, ph), self.raw[s][ph]["inner_depth_within_3pct_over_L_median"])
                self.assertAlmostEqual(self.cm.half_spread_bp(s, ph), self.ref[s]["ph"][ph]["hs"])

    def test_etf_follow_on_allowance(self):
        storm = self.cm.walk_bp("ARCH", "storm", 1.0 / 12)
        self.assertAlmostEqual(self.cm.entry_cost_bp("ARCH", "storm", 1.0 / 12), 2.0 * (storm + 17.28))
        stock = self.cm.walk_bp("GEN", "storm", 1.0 / 12)
        self.assertAlmostEqual(self.cm.entry_cost_bp("GEN", "storm", 1.0 / 12), 2.0 * (stock + 36.0))

    def test_check_against_config(self):
        self.assertEqual(self.cm.check_against_config(), [])
        cfg = FakeConfig()
        cfg.sym("PORT")["entry_cost_bp"]["storm"] = 150
        cfg.sym("ARCH")["exit_band_bp"]["storm"] = 30.0
        lines = CostModel(testpaths.COST_MODEL, cfg).check_against_config()
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(any(ln.startswith("PORT entry_cost_bp.storm") for ln in lines))
        self.assertTrue(any(ln.startswith("ARCH exit_band_bp.storm") for ln in lines))

    def test_unknown_phase_falls_back_and_is_reported(self):
        cm = CostModel(testpaths.COST_MODEL, FakeConfig())
        self.assertAlmostEqual(cm.walk_bp("PORT", "nonsense", 0.1), cm.walk_bp("PORT", "storm", 0.1))
        self.assertTrue(any("nonsense" in ln for ln in cm.check_against_config()))
        with self.assertRaises(KeyError):
            cm.walk_bp("NOPE", "storm", 0.1)

    def test_malformed_file_raises(self):
        d = testpaths.tmpdir("cost")
        p = os.path.join(d, "bad.json")
        with open(p, "w") as f:
            json.dump({"PORT": {"L": 200}}, f)
        with self.assertRaises(ValueError):
            CostModel(p, FakeConfig())


if __name__ == "__main__":
    unittest.main()
