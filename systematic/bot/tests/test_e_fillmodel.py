"""Group E: ``sim.fillmodel`` equals inline copies of r_sim's formulas (DESIGN.md section 9.1 E "fillmodel").

The r_sim pieces below (``_rs_prep``, ``_rs_interp``, ``_rs_walk_cost``, ``_rs_imp_kernel`` and the cost block of
``sim()``) are copied from ``testnet/analysis/rebacktest/r_sim.py`` (read only, never imported here), and the
comparisons run on synthetic fill histories.
"""
from __future__ import annotations

import bisect
import json
import math
import os
import random
import sys
import unittest

sys.dont_write_bytecode = True
BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BOT not in sys.path:
    sys.path.insert(0, BOT)

import testpaths  # noqa: E402
from ctcbot.types import ApiKind, Side  # noqa: E402

from sim import fillmodel as F  # noqa: E402

SYMS = ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "RECON", "MAT", "GEN", "HAVEN", "REIN", "ARCH", "REBLD"]
ETFS = ("ARCH", "REBLD")
PH_LIST = ["open", "pre", "h1_entry", "early", "h1_exit", "s3_start", "storm", "after", "rep_prelim", "rep_final"]
IMP_T = [0.0, 2.0, 4.0, 10.0, 20.0, 30.0]
IMP_V = [27.6, 29.7, 31.4, 41.0, 35.8, 26.8]
IMP_TAU = 47.0


# ----------------------------------------------------------------------------------------- inline r_sim copies
def _rs_prep(raw: dict) -> dict:
    out = {}
    for s in SYMS:
        L = raw[s]["L"]
        d = {"L": L, "ph": {}, "rho": raw[s]["replenish_storm"]["depth50_100"]["within_2s"]}
        for ph in PH_LIST:
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


def _rs_interp(xs, ys, x):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    k = bisect.bisect_right(xs, x)
    x0, x1 = xs[k - 1], xs[k]
    return ys[k - 1] + (ys[k] - ys[k - 1]) * (x - x0) / (x1 - x0)


def _rs_walk_cost(CM, sym, ph, x, side, side_specific=True):
    if x <= 0:
        return 0.0
    p = CM[sym]["ph"][ph]
    c = _rs_interp(p["xs"], p["ys"], x)
    if side_specific and p["rx"]:
        r = _rs_interp(p["rx"], p["rb"] if side > 0 else p["rs"], x)
        c *= r
    return c


def _rs_imp_kernel(dt):
    if dt <= IMP_T[-1]:
        return _rs_interp(IMP_T, IMP_V, dt)
    if dt > 150:
        return 0.0
    return IMP_V[-1] * math.exp(-(dt - IMP_T[-1]) / IMP_TAU)


def _rs_fo_im(hist, tf, side, beta, fo_win=8.0, use_imp=True, credit=True):
    """The kernel-mode loop of r_sim ``sim()``."""
    fo = 0.0
    im = 0.0
    for (tk, sk, xk) in hist:
        dt = tf - tk
        if dt <= 0 or dt > 150:
            continue
        if sk == side:
            if dt <= fo_win:
                fo += beta * xk
            elif use_imp:
                im += _rs_imp_kernel(dt) * xk
        elif use_imp and credit:
            im -= _rs_imp_kernel(dt) * xk
    return fo, im


def _rs_room2(L, depth, rho, xprev, filled_now):
    xmax = max(0.0, depth - (1.0 - rho) * xprev)
    return int(xmax * L) - filled_now


def _rs_cw(CM, sym, ph, X1, q, L, side):
    X2 = X1 + q / L
    c2 = _rs_walk_cost(CM, sym, ph, X2, side)
    c1 = _rs_walk_cost(CM, sym, ph, X1, side) if X1 > 0 else 0.0
    return (X2 * c2 - X1 * c1) / (X2 - X1)


class RsimCost:
    """CostModel stand-in whose ``walk_bp`` is the inline r_sim ``walk_cost`` on the measured cost model."""

    def __init__(self, CM: dict) -> None:
        self.CM = CM

    def walk_bp(self, symbol, phase, x, side=None):
        if side is None:
            return _rs_walk_cost(self.CM, symbol, phase, x, 1, side_specific=False)
        return _rs_walk_cost(self.CM, symbol, phase, x, F.side_int(side))

    def half_spread_bp(self, symbol, phase):
        return self.CM[symbol]["ph"][phase]["hs"]

    def depth(self, symbol, phase):
        return self.CM[symbol]["ph"][phase]["depth"]

    def rho(self, symbol):
        return self.CM[symbol]["rho"]


def load_rsim_cost() -> RsimCost:
    with open(testpaths.require(testpaths.COST_MODEL), encoding="utf-8") as f:
        return RsimCost(_rs_prep(json.load(f)))


class _Cfg:
    def __init__(self, d: dict) -> None:
        self.d = d

    def get(self, dotted, default=None):
        cur = self.d
        for k in dotted.split("."):
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur


# ----------------------------------------------------------------------------------------- tests
class TestFormulas(unittest.TestCase):
    def test_interp_matches(self) -> None:
        rng = random.Random(1)
        xs = sorted(rng.uniform(0, 1) for _ in range(12))
        ys = [rng.uniform(5, 300) for _ in xs]
        for _ in range(500):
            x = rng.uniform(-0.2, 1.3)
            self.assertAlmostEqual(F.interp(xs, ys, x), _rs_interp(xs, ys, x), places=12)

    def test_impact_kernel_and_decay(self) -> None:
        p = F.FillParams()
        for k in range(0, 1700):
            dt = k * 0.1
            self.assertAlmostEqual(F.impact_kernel(dt, p), _rs_imp_kernel(dt), places=12, msg=f"dt={dt}")
        self.assertEqual(F.impact_kernel(150.01, p), 0.0)
        self.assertAlmostEqual(F.impact_kernel(30.0, p), 26.8)
        self.assertAlmostEqual(F.impact_kernel(77.0, p), 26.8 * math.exp(-1.0))

    def test_follow_on_and_impact_match_on_synthetic_histories(self) -> None:
        rng = random.Random(7)
        for case in range(300):
            credit = case % 3 != 0
            use_imp = case % 5 != 0
            p = F.FillParams(impact=use_imp, impact_credit=credit)
            tf = 2.0 * rng.randint(40, 380)
            hist = [(2.0 * rng.randint(0, int(tf / 2) + 2), rng.choice((1, -1)), rng.uniform(0.01, 0.4))
                    for _ in range(rng.randint(0, 25))]
            side = rng.choice((1, -1))
            beta = rng.choice((100.0, 48.0))
            got = F.follow_on_and_impact(hist, tf, side, beta, p)
            want = _rs_fo_im(hist, tf, side, beta, use_imp=use_imp, credit=credit)
            self.assertAlmostEqual(got[0], want[0], places=10)
            self.assertAlmostEqual(got[1], want[1], places=10)

    def test_follow_on_off(self) -> None:
        p = F.FillParams(follow_on=False)
        fo, im = F.follow_on_and_impact([(10.0, 1, 0.2)], 14.0, 1, 100.0, p)
        self.assertEqual(fo, 0.0)
        self.assertEqual(im, 0.0)

    def test_replenish_room_matches(self) -> None:
        rng = random.Random(3)
        for _ in range(500):
            L = rng.choice((50, 100, 200, 800))
            depth, rho = rng.uniform(0.1, 1.2), rng.uniform(0, 1)
            xprev, now = rng.uniform(0, 1.5), rng.randint(0, L)
            self.assertEqual(F.replenish_room(L, depth, rho, xprev, now), _rs_room2(L, depth, rho, xprev, now))

    def test_walk_increment_matches_rsim(self) -> None:
        cost = load_rsim_cost()
        rng = random.Random(11)
        for _ in range(200):
            sym = rng.choice(SYMS)
            ph = rng.choice(PH_LIST)
            L = cost.CM[sym]["L"]
            side = rng.choice((1, -1))
            X1 = rng.choice((0.0, rng.uniform(0, 0.6)))
            q = rng.randint(1, L)
            c = (lambda z, s=sym, h=ph, sd=side: cost.walk_bp(s, h, z, F.side_enum(sd)))
            self.assertAlmostEqual(F.walk_increment(c, X1, X1 + q / L), _rs_cw(cost.CM, sym, ph, X1, q, L, side),
                                   places=9)

    def test_walk_bp_side_equals_rsim_walk_cost(self) -> None:
        cost = load_rsim_cost()
        rng = random.Random(5)
        for _ in range(200):
            sym, ph = rng.choice(SYMS), rng.choice(PH_LIST)
            x = rng.uniform(0.001, 1.2)
            for side in (Side.BUY, Side.SELL):
                self.assertAlmostEqual(cost.walk_bp(sym, ph, x, side),
                                       _rs_walk_cost(cost.CM, sym, ph, x, 1 if side is Side.BUY else -1), places=12)

    def test_fill_price_is_rsim_price(self) -> None:
        for side in (1, -1):
            px = F.fill_price(50.0, side, 1.5, 20.0, 10.0, -3.0)
            self.assertAlmostEqual(px, 50.0 * (1.0 + 1.5 * (20.0 + 10.0 - 3.0) * 1e-4 * side))

    def test_match_row(self) -> None:
        p = F.FillParams()
        row = lambda tau, pp=p: F.match_row(tau, 570.0, 740.0, 780.0, pp)  # noqa: E731
        self.assertEqual(row(80.0), 41)
        self.assertEqual(row(79.99), 40)
        self.assertEqual(row(81.0), 41)
        self.assertEqual(row(569.9), 285)
        self.assertEqual(row(570.0), 287)       # report window: +lat_rep_rows
        self.assertEqual(row(599.9), 301)
        self.assertEqual(row(600.0), 301)
        self.assertEqual(row(739.0), 370)
        self.assertEqual(row(740.0), 372)
        self.assertEqual(row(779.0), 391)
        self.assertEqual(F.match_row(80.0, 570.0, 740.0, 780.0, F.FillParams(lat_rows=1)), 42)

    def test_marginal_cost_of_linear_walk(self) -> None:
        c = lambda x: 10.0 + 100.0 * x  # noqa: E731 - x*c(x) = 10x + 100x^2, so m(x) = 10 + 200x
        for x in (0.05, 0.2, 0.5, 0.9):
            self.assertAlmostEqual(F.marginal_cost(c, x), 10.0 + 200.0 * x, places=6)
        self.assertAlmostEqual(F.marginal_cost(c, 0.0), 10.0, delta=0.05)   # forward difference at 0

    def test_limit_fill_qty_is_largest_quantity_within_limit(self) -> None:
        c = lambda x: 10.0 + 100.0 * x  # noqa: E731
        L, row_px = 200, 50.0
        for side in (1, -1):
            for limit_bp in (12.0, 30.0, 55.0, 90.0, 400.0):
                limit = row_px * (1 + side * limit_bp * 1e-4)
                for x1 in (0.0, 0.1):
                    q = 150
                    got = F.limit_fill_qty(q, x1, L, c, side, row_px, 2.0, 1.0, 1.0, limit)

                    def ok(n: int) -> bool:
                        mp = row_px * (1 + side * (F.marginal_cost(c, x1 + n / L) + 3.0) * 1e-4)
                        return mp <= limit + 1e-12 if side > 0 else mp >= limit - 1e-12

                    best = max([n for n in range(1, q + 1) if ok(n)], default=0)
                    self.assertEqual(got, best, msg=f"side {side} limit {limit_bp} x1 {x1}")
        self.assertEqual(F.limit_fill_qty(37, 0.0, L, c, 1, row_px, 0.0, 0.0, 1.0, None), 37)
        self.assertEqual(F.limit_fill_qty(0, 0.0, L, c, 1, row_px, 0.0, 0.0, 1.0, 60.0), 0)

    def test_params_from_config(self) -> None:
        with open(os.path.join(BOT, "config", "default.json"), encoding="utf-8") as f:
            d = json.load(f)
        p = F.FillParams.from_config(_Cfg(d))
        self.assertEqual(p.kernel_t, tuple(IMP_T))
        self.assertEqual(p.kernel_bp, tuple(IMP_V))
        self.assertEqual((p.beta_stock, p.beta_etf, p.fo_window_s, p.tau_s, p.cut_s), (100.0, 48.0, 8.0, 47.0, 150.0))
        self.assertEqual((p.lat_rows, p.lat_rep_rows, p.recorded_latency_s), (0, 1, 0.3))
        self.assertEqual(p.beta("ARCH"), 48.0)
        self.assertEqual(p.beta("PORT"), 100.0)
        d["sim"]["impact_kernel_t"] = [0.0, 2.0]
        with self.assertRaises(ValueError):
            F.FillParams.from_config(_Cfg(d))

    def test_fill_history(self) -> None:
        h = F.FillHistory()
        h.add("PORT", 41, 82.0, 1, 50, 200)
        h.add("PORT", 41, 82.0, 1, 10, 200)
        h.add("PORT", 42, 84.0, -1, 20, 200)
        self.assertEqual(h.filled_side("PORT", 41, 1), 60)
        self.assertEqual(h.filled_side("PORT", 41, -1), 0)
        self.assertEqual(h.fills("PORT")[0], (82.0, 1, 0.25))
        h.reset()
        self.assertEqual(h.fills("PORT"), [])

    def test_side_helpers(self) -> None:
        self.assertEqual(F.side_int(Side.BUY), 1)
        self.assertEqual(F.side_int("sell"), -1)
        self.assertEqual(F.side_int(-1), -1)
        self.assertIs(F.side_enum(1), Side.BUY)
        with self.assertRaises(ValueError):
            F.side_int("hold")


# ----------------------------------------------------------------------------------------- exchange vs r_sim
def _rs_phase_of(t):
    if t < 3:
        return "open"
    if t < 50:
        return "pre"
    if t < 58:
        return "h1_entry"
    if t < 60:
        return "pre"
    if t < 72:
        return "early"
    if t < 80:
        return "h1_exit"
    if t < 120:
        return "s3_start"
    if t < 540:
        return "storm"
    if t < 568:
        return "after"
    if t < 600:
        return "rep_prelim"
    if t < 738:
        return "after"
    if t < 760:
        return "rep_final"
    return "after"


class TestExchangeMatchesRsim(unittest.TestCase):
    """A sequence of taker clips through ``SimExchange`` (research mode) is priced exactly as r_sim's ``sim()``."""

    LIM = {"PORT": 200, "ISLE": 300, "AQUA": 500, "GRID": 400, "FERRY": 800, "RECON": 500, "MAT": 700, "GEN": 600,
           "HAVEN": 250, "REIN": 200, "ARCH": 50, "REBLD": 100}

    def test_fake_schedule_cost_phase_is_rsim_phase_of(self) -> None:
        from test_e_exchange import FakeSchedule
        sch = FakeSchedule()
        for k in range(0, 3121):
            t = k * 0.25
            self.assertEqual(sch.cost_phase(t), _rs_phase_of(t), t)

    def test_clip_sequence(self) -> None:
        from test_e_exchange import BASE, Req, at, make_exchange
        cost = load_rsim_cost()
        price = (lambda r, i, s: BASE[s] * (1 + 0.0005 * i))
        ex, m, _ = make_exchange(price=price, cost=cost)
        req = Req()
        orders = [(80.0, "PORT", 1, 16), (80.0, "ARCH", 1, 4), (82.5, "PORT", 1, 16), (84.0, "PORT", 1, 30),
                  (101.0, "PORT", 1, 25), (130.0, "PORT", -1, 50), (200.0, "PORT", 1, 25), (300.0, "GRID", -1, 400),
                  (302.0, "GRID", -1, 100), (570.5, "HAVEN", 1, 20), (575.0, "HAVEN", 1, 20), (741.0, "REBLD", -1, 8)]
        want = []
        hist: dict[str, list] = {}
        filled_side: dict[tuple, int] = {}
        for t, sym, side, q in orders:
            row = int(t // 2)
            lim = round(price(1, row, sym) * (1.5 if side > 0 else 0.5), 2)
            at(ex, t).execute(req.buy(sym, q, lim) if side > 0 else req.sell(sym, q, lim))
            # inline r_sim (lat 0, lat_rep 1, mult 1)
            rep = 570 <= t < 600 or 740 <= t < 780
            j = int(t // 2) + 1 + (1 if rep else 0)
            ph = _rs_phase_of(t)
            L = self.LIM[sym]
            pc = cost.CM[sym]["ph"][ph]
            xprev = filled_side.get((sym, j - 1, side), 0) / L
            room2 = _rs_room2(L, pc["depth"], cost.CM[sym]["rho"], xprev, filled_side.get((sym, j, side), 0))
            if room2 < q:
                if room2 <= 0:
                    continue
                q = room2
            X1 = filled_side.get((sym, j, side), 0) / L
            cw = _rs_cw(cost.CM, sym, ph, X1, q, L, side)
            fo, im = _rs_fo_im(hist.get(sym, []), 2.0 * j, side, 48.0 if sym in ETFS else 100.0)
            px = price(1, j, sym) * (1.0 + (cw + fo + im) * 1e-4 * side)
            hist.setdefault(sym, []).append((2.0 * j, side, q / L))
            filled_side[(sym, j, side)] = filled_side.get((sym, j, side), 0) + q
            want.append((sym, j, side, q, px))
            at(ex, 2.0 * j)
            ex.execute(req(ApiKind.CANCEL_ALL))        # drop remainders so no passive fill interferes
        at(ex, 760.0)
        got = [(f["symbol"], f["row"], 1 if f["side"] == "buy" else -1, f["qty"], f["price"])
               for f in ex.results()["fills"] if f["kind"] == "taker"]
        self.assertEqual(len(got), len(want))
        for g, w in zip(got, want):
            self.assertEqual(g[:4], w[:4])
            self.assertAlmostEqual(g[4], w[4], places=9, msg=str(g))
        self.assertGreaterEqual(ex.results()["repl_hits"], 1)


if __name__ == "__main__":
    unittest.main()
