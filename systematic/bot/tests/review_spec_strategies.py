"""Spec-conformance review tests (reviewer, lens = spec). Not part of the default discover pattern.

Run from BOT:
  SYS/.venv/bin/python -m unittest discover -s tests -p "review_spec_*.py" -v

Tests that encoded the spec where the reviewed code deviated were marked ``expectedFailure``. The hardening pass
fixed every one of them, so the markers are gone and each test now guards its fix (DESIGN.md §13). Every other
test checks a spec value the code already meets (windows derived from case timing, bot units, thresholds,
cadences).
"""
from __future__ import annotations

import math
import os
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402
from test_c_support import (MIDS, FakeConfig, FakeLawCheck, FakeLaws, books, fill, fv, make_ctx,  # noqa: E402
                            make_deps, news, season, targets)
from ctcbot.case import Schedule, clip_size  # noqa: E402
from ctcbot.models.coverage import CLOSURE_SYMBOLS  # noqa: E402
from ctcbot.strategies.flatten import Flatten  # noqa: E402
from ctcbot.strategies.h1 import H1  # noqa: E402
from ctcbot.strategies.s1r3 import S1R3  # noqa: E402
from ctcbot.strategies.s3 import S3Rule, table_value  # noqa: E402
from ctcbot.types import SYMBOLS, CancelIntent, ListLog, PriceQuality, RestIntent, Side  # noqa: E402

RESEARCH_TIMING = {"pre_storm": 60, "storm": 480, "aftermath": 240, "prelim_report": 30, "final_report": 40}
# the starter kit's other host (playbook header): 20 s pre-storm, 180 s aftermath
ALT_TIMING = {"pre_storm": 20, "storm": 480, "aftermath": 180, "prelim_report": 30, "final_report": 40}
T0 = 10_000.0
S = {s: round(MIDS[s] * 1.01, 4) for s in SYMBOLS}


def _rests(xs):
    return [i for i in xs if isinstance(i, RestIntent)]


# ====================================================================================== windows and units
class WindowsFromCaseTiming(unittest.TestCase):
    """Every strategy window resolves from the served case timing (DESIGN §5.2, §6 notation)."""

    SPEC = {  # (config key, research-timing real seconds from the playbook / findings / DESIGN §6)
        "strategies.h1.ref_mid_at": 48, "strategies.h1.entry_start": 50, "strategies.h1.entry_last": 56,
        "strategies.h1.check_t0": 58, "strategies.h1.check_t1": 90, "strategies.h1.fallback_deadline": 76,
        "strategies.h1.fallback_start": 84, "strategies.h1.fallback_last": 98,
        "strategies.s3.start": 80, "strategies.s3.end": 540,
        "strategies.flatten.start": 540, "strategies.flatten.end": 568, "strategies.flatten.behind_alert_at": 560,
        "strategies.s1r3.fast_until": 750, "strategies.s1r3.sweep_end": 778, "strategies.s1r3.sanity_at": 756,
        "strategies.s1r3.block_from": 680, "strategies.s1r3.prewarm_at": 737,
        "strategies.s1r3.announce_lock_from": 700, "strategies.s1r3.late_trigger_ok_until": 775,
        "strategies.rein_ask.start": 0, "strategies.rein_ask.end_r12": 780, "strategies.rein_ask.end_r3": 740,
        "strategies.etf_leadlag.start": 60, "strategies.etf_leadlag.end": 738,
        "guards.opening.until": 3, "risk.block_marketable_before": 3,
    }

    def setUp(self):
        self.cfg = FakeConfig()

    def test_research_timing_matches_spec_seconds(self):
        sch = Schedule(RESEARCH_TIMING, self.cfg.get("cost.phases"))
        self.assertEqual((sch.storm_start, sch.storm_end, sch.prelim, sch.final, sch.close, sch.speed),
                         (60.0, 540.0, 570.0, 740.0, 780.0, 30.0))
        for key, want in self.SPEC.items():
            self.assertEqual(sch.at(self.cfg.get(key)), float(want), key)
        # cost phases used by S3 (s3_start 80-120, storm 120-540) and H1 (h1_entry 50-58)
        for t, ph in ((50.0, "h1_entry"), (57.9, "h1_entry"), (79.9, "h1_exit"), (80.0, "s3_start"),
                      (119.9, "s3_start"), (120.0, "storm"), (539.9, "storm"), (540.0, "after"),
                      (568.0, "rep_prelim"), (738.0, "rep_final"), (760.0, "after")):
            self.assertEqual(sch.cost_phase(t), ph, t)

    def test_windows_move_with_served_timing(self):
        sch = Schedule(ALT_TIMING, self.cfg.get("cost.phases"))
        self.assertEqual((sch.storm_start, sch.storm_end, sch.prelim, sch.final, sch.close),
                         (20.0, 500.0, 530.0, 640.0, 680.0))
        # anchors keep their offsets: H1 entry 10-16 s, S3 40-500 s, flatten 500-528 s, S1-r3 sanity at 656 s
        self.assertEqual(sch.at(self.cfg.get("strategies.h1.entry_start")), 10.0)
        self.assertEqual(sch.at(self.cfg.get("strategies.h1.entry_last")), 16.0)
        self.assertEqual(sch.at(self.cfg.get("strategies.s3.start")), 40.0)
        self.assertEqual(sch.at(self.cfg.get("strategies.flatten.end")), 528.0)
        self.assertEqual(sch.at(self.cfg.get("strategies.s1r3.sanity_at")), 656.0)
        self.assertAlmostEqual(sch.speed, 23400.0 / 680.0)


class BotUnits(unittest.TestCase):
    """DESIGN §6 bot-unit table / TESTNET_FINDINGS §6 (w05_params.out) against clip_size and the config."""

    TABLE = {  # sym: (L, S3 L/12, H1 L/4, H1 exit L/8, entry s3_start, storm, after)
        "PORT": (200, 16, 50, 25, 315, 123, 101), "ISLE": (300, 25, 75, 37, 165, 107, 99),
        "AQUA": (500, 41, 125, 62, 170, 112, 98), "GRID": (400, 33, 100, 50, 205, 107, 99),
        "FERRY": (800, 66, 200, 100, 138, 117, 102), "RECON": (500, 41, 125, 62, 195, 110, 99),
        "MAT": (700, 58, 175, 87, 164, 110, 100), "GEN": (600, 50, 150, 75, 161, 109, 100),
        "HAVEN": (250, 20, 62, 31, 342, 138, 104), "REIN": (200, 16, 50, 25, 100, 100, 98),
        "ARCH": (50, 4, 12, 6, 129, 73, 58), "REBLD": (100, 8, 25, 12, 101, 69, 55),
    }

    def test_clips_and_entry_costs(self):
        cfg = FakeConfig()
        div = cfg.get("sizing.clip_div")
        for sym, (L, c12, c4, c8, e0, e1, e2) in self.TABLE.items():
            self.assertEqual(clip_size(L, div["s3"]), c12, sym)
            self.assertEqual(clip_size(L, div["h1"]), c4, sym)
            self.assertEqual(clip_size(L, div["h1_exit"]), c8, sym)
            self.assertEqual(clip_size(L, div["flatten"]), c12, sym)
            ec = cfg.sym(sym)["entry_cost_bp"]
            self.assertEqual((ec["s3_start"], ec["storm"], ec["after"]), (e0, e1, e2), sym)

    def test_s3_threshold_per_round_and_phase(self):
        cfg = FakeConfig()
        rule = S3Rule(make_deps(cfg), cfg.get("strategies.s3"))
        self.assertEqual((rule.k(1), rule.k(2), rule.k(3)), (0.1, 0.1, 0.2))
        self.assertAlmostEqual(rule.cost_frac("PORT", "s3_start", 16), 0.0315)
        self.assertAlmostEqual(rule.cost_frac("PORT", "storm", 16), 0.0123)
        self.assertAlmostEqual(rule.cost_frac("ARCH", "storm", 4), 0.0073)
        self.assertEqual(table_value(cfg.sym("HAVEN")["exit_band_bp"], "storm"), 31.2)

    def test_quarter_scale_units(self):
        cfg = FakeConfig()
        cfg.set("sizing.scale", 0.25)
        lim = {s: int(math.floor(v[0] * 0.25 + 1e-9)) for s, v in self.TABLE.items()}
        self.assertEqual(lim["PORT"], 50)
        self.assertEqual(clip_size(lim["PORT"], 12), 4)
        self.assertEqual(clip_size(lim["ARCH"], 12), 1)          # 12 / 12
        self.assertEqual(clip_size(lim["ARCH"], 4), 3)


# ====================================================================================== S1-r3 timing
class S1R3Timing(unittest.TestCase):
    def setUp(self):
        self.cfg = FakeConfig()
        self.laws = FakeLaws(dict(S), tol={s: self.cfg.sym(s)["laws_tol"] for s in SYMBOLS})
        self.log = ListLog()
        self.s = S1R3(make_deps(self.cfg, laws=self.laws, law_check=FakeLawCheck(True), log=self.log))

    def step(self, t, **kw):
        kw.setdefault("seasoninfo", season())
        return self.s.decide(make_ctx(t, 3, now=T0 + t, cfg=self.cfg, **kw))

    def test_sweep_fast_then_slow_and_sanity_once_at_756(self):
        fvq = fv(dict(S), round_=3, seq_hi=900, diag={"status_r3": "final"}, computed=T0 + 740.25)
        final = news("damage_report", {"stage": "final"}, seq=900, round_=3)
        self.step(740.2, news_=(final,))
        emits, sanity_t = [], None
        t = 740.25
        while t < 780.0:
            out = self.step(t, fvs=fvq)
            if _rests(out):
                emits.append(t)
            if self.log.kinds("s1r3_sanity_check") and sanity_t is None:
                sanity_t = t
            t = round(t + 0.25, 2)
        gaps = [round(b - a, 2) for a, b in zip(emits, emits[1:])]
        self.assertEqual(emits[0], 740.25)
        self.assertTrue(all(g == 0.5 for a, g in zip(emits, gaps) if a < 749.5), gaps)
        self.assertTrue(all(g == 2.0 for a, g in zip(emits, gaps) if a >= 750.0), gaps)
        self.assertLess(emits[-1], 778.0)
        self.assertEqual(sanity_t, 756.0)
        self.assertEqual(len(self.log.kinds("s1r3_sanity_check")), 1)

    def test_late_arm_after_756_still_runs_the_sanity_check(self):
        """Spec §6.4 step 7: the sanity check applies at >= 756 s. A late arm (final report lost, snapshot arm at
        760 s, which §6.4 step 1 allows until 775 s) marks the check done before anything is placed, so the
        rests go out against a book that has already repriced far from S."""
        si = season(final={3: {"damage": {}}}, final_seq={3: -1})
        repriced = books(dict(MIDS, PORT=S["PORT"] * 0.97))          # |mid - S| = 3% > max(3 eps, 0.5% S)
        self.step(760.0, seasoninfo=si, bk=repriced)                 # arm (snapshot)
        out = self.step(760.25, seasoninfo=si, bk=repriced,
                        fvs=fv(dict(S), round_=3, seq_hi=5, computed=T0 + 760.1))
        self.assertTrue(self.s.fired)
        self.assertNotIn("PORT", {i.symbol for i in _rests(out)})     # fails today: PORT is placed at S -+ eps


    def test_symbol_cleared_after_756_still_gets_the_sanity_check(self):
        """Same gap through the operator path: gate (g) fails (no rounds 1-2 law check, as in 224 r3), the
        operator types 'lawok all' at 757 s. The sanity check already ran at 756 s on an empty placed set, so
        every candidate is placed with no |mid - S| check against the repriced book."""
        lc = FakeLawCheck(False)
        self.s = S1R3(make_deps(self.cfg, laws=self.laws, law_check=lc, log=self.log))
        fvq = fv(dict(S), round_=3, seq_hi=900, diag={"status_r3": "final"}, computed=T0 + 740.25)
        final = news("damage_report", {"stage": "final"}, seq=900, round_=3)
        repriced = books(dict(MIDS, PORT=S["PORT"] * 0.97))
        self.step(740.2, news_=(final,))
        t = 740.25
        while t <= 756.0:
            self.assertEqual(_rests(self.step(t, fvs=fvq, bk=repriced)), [], t)   # everything gated by (g)
            t = round(t + 0.25, 2)
        from ctcbot.types import Command
        self.s.on_command(Command("lawok", ("all",), source="console"),
                          make_ctx(757.0, 3, now=T0 + 757.0, cfg=self.cfg, seasoninfo=season()))
        out, t = [], 757.0
        while not _rests(out) and t < 760.0:                         # next sweep emission (2-s cadence)
            out = self.step(t, fvs=fvq, bk=repriced)
            t = round(t + 0.25, 2)
        self.assertTrue(_rests(out))
        self.assertNotIn("PORT", {i.symbol for i in _rests(out)})     # fails today: PORT placed, no sanity


class S1R3LeadLag(unittest.TestCase):
    def test_leadlag_on_keeps_etfs_in_s1r3(self):
        """Playbook §4.5 and DESIGN §6.6: with lead-lag on, ARCH and REBLD leave H1 and S3 only. The S1-r3 gate
        treats ctx.leadlag as a block (gate 'c:leadlag'), so the ETF settlement trade is lost whenever lag is on."""
        cfg = FakeConfig()
        laws = FakeLaws(dict(S), tol={s: cfg.sym(s)["laws_tol"] for s in SYMBOLS})
        s = S1R3(make_deps(cfg, laws=laws, law_check=FakeLawCheck(True), log=ListLog()))
        final = news("damage_report", {"stage": "final"}, seq=900, round_=3)
        lag = frozenset({"ARCH", "REBLD"})
        s.decide(make_ctx(740.2, 3, now=T0 + 740.2, cfg=cfg, news_=(final,), leadlag=lag, seasoninfo=season()))
        out = s.decide(make_ctx(740.25, 3, now=T0 + 740.25, cfg=cfg, leadlag=lag, seasoninfo=season(),
                                fvs=fv(dict(S), round_=3, seq_hi=900, diag={"status_r3": "final"},
                                       computed=T0 + 740.25)))
        placed = {i.symbol for i in _rests(out)}
        self.assertTrue({"ARCH", "REBLD"} <= placed, (placed, s.gates))


# ====================================================================================== H1 round-1 check
class H1CheckInputs(unittest.TestCase):
    def setUp(self):
        self.cfg = FakeConfig()

    def _h1(self, cost=None):
        return H1(make_deps(self.cfg, cost=cost))

    def _round1(self, h1, fills_at_70=(), fills_at_88=(), move=0.02, fallback=False):
        def step(t, mids=None, **kw):
            m = dict(MIDS)
            m.update(mids or {})
            kw.setdefault("fvs", None if fallback else fv(round_=1))
            return targets(h1.decide(make_ctx(t, 1, cfg=self.cfg, now=T0 + t, bk=books(m), **kw)))
        for t in (48.0, 50.0, 52.0, 54.0, 56.0, 58.0):
            step(t)
        step(70.0, fills=tuple(fills_at_70))
        step(76.0)
        step(84.0, positions={"PORT": 50})
        step(88.0, fills=tuple(fills_at_88), positions={"PORT": 50})
        moved = {s: MIDS[s] * (1 + move * self.cfg.sym(s)["h1_dir"]) for s in ("HAVEN", "PORT", "ARCH")}
        step(90.0, mids=moved, positions={"PORT": 50})

    def test_cost_ratio_ignores_limit_bound_fills(self):
        """§6.1 step 6 / playbook §5.4: drop the rebuilders only if *round-1 costs* are >= 1.5x the tables. A
        LIMIT_BOUND fill is priced at our limit (48-s mid + 50 bp), not at the fill, so with ack fills that lack
        avg_fill_price the ratio is about 50 bp / 22 bp >= 1.5 and the rebuilders are dropped every season.
        The fill-sanity breaker skips LIMIT_BOUND fills for the same reason; the ratio does not."""
        from ctcbot.models.cost_model import CostModel
        cost = CostModel(testpaths.require(testpaths.COST_MODEL), self.cfg)
        h1 = self._h1(cost)
        fills = []
        for i, (sym, side) in enumerate((("PORT", Side.BUY), ("ISLE", Side.BUY), ("RECON", Side.SELL),
                                          ("MAT", Side.SELL))):
            L = {"PORT": 200, "ISLE": 300, "RECON": 500, "MAT": 700}[sym]
            mid = MIDS[sym]
            lim = math.floor(mid * 1.005 * 100) / 100 if side is Side.BUY else math.ceil(mid * 0.995 * 100) / 100
            walk = cost.walk_bp(sym, "h1_entry", (L // 4) / L, side)
            fills.append(fill(sym, side, L // 4, lim, ref_mid=mid, expected=walk + 36.0,
                              quality=PriceQuality.LIMIT_BOUND, mono=T0 + 50 + i, local_id=f"lb{i}"))
        self._round1(h1, fills_at_70=fills, move=0.05)
        # hardening: LIMIT_BOUND fills are left out of the ratio (as the fill-sanity breaker does), so with only
        # limit-priced fills there is no ratio and the rebuilders stay (the reviewer's fix; the original assertion
        # that a ratio exists contradicted it)
        self.assertTrue(h1.cost_ratio is None or h1.cost_ratio < 1.5, h1.cost_ratio)
        self.assertFalse(h1.drop_rebuilders, f"cost_ratio {h1.cost_ratio} from limit prices")

    def test_cost_ratio_uses_first_clips_against_the_tables(self):
        """Hardening of review finding 2: the ratio compares each symbol's first entry clip with the walk table. Our
        own follow-on (later clips at 2.3x the table on the 225 books) no longer drops the rebuilders at base cost,
        while first clips at 1.6x the table still do."""
        from ctcbot.models.cost_model import CostModel
        cost = CostModel(testpaths.require(testpaths.COST_MODEL), self.cfg)
        for mult_first, mult_later, drop in ((0.9, 2.3, False), (1.6, 2.3, True)):
            h1 = self._h1(cost)
            fills = []
            for i, (sym, side) in enumerate((("PORT", Side.BUY), ("ISLE", Side.BUY), ("RECON", Side.SELL))):
                L = {"PORT": 200, "ISLE": 300, "RECON": 500}[sym]
                mid = MIDS[sym]
                walk = cost.walk_bp(sym, "h1_entry", (L // 4) / L, side)
                for k, mult in enumerate((mult_first, mult_later, mult_later)):
                    px = round(mid * (1 + side.sign * walk * mult / 1e4), 4)
                    fills.append(fill(sym, side, L // 4, px, ref_mid=mid, expected=walk + 36.0,
                                      mono=T0 + 50 + 2 * k + 0.1 * i, local_id=f"c{sym}{k}"))
            self._round1(h1, fills_at_70=fills, move=0.05)
            self.assertAlmostEqual(h1.cost_ratio, mult_first, delta=0.05)
            self.assertEqual(h1.drop_rebuilders, drop)

    def test_fallback_exit_fills_do_not_enter_the_round1_check(self):
        """§6.1 step 6: cost_bp is over the H1 *entry* fills (fill vs 48-s mid). The fallback exit (84-98 s) is
        also strategy 'h1', so its sells before 90 s enter the check: a sell above the 48-s mid has a negative
        'cost' and turns a failing check into a pass (rounds 2-3 then trade at size_on_pass)."""
        h1 = self._h1()
        entry = [fill("PORT", Side.BUY, 50, round(MIDS["PORT"] * 1.011, 2), ref_mid=MIDS["PORT"],
                      mono=T0 + 50, local_id="e1")]                          # 110 bp > 1/3 of a 200-bp move
        exit_ = [fill("PORT", Side.SELL, 50, round(MIDS["PORT"] * 1.04, 2), ref_mid=MIDS["PORT"] * 1.04,
                      mono=T0 + 86, local_id="x1")]
        self._round1(h1, fills_at_70=entry, fills_at_88=exit_, move=0.02, fallback=True)
        self.assertTrue(h1.fallback)
        self.assertFalse(h1.check_result, h1.check_info)


# ====================================================================================== flatten alert
class FlattenBehind(unittest.TestCase):
    def test_behind_alert_uses_the_base_clip(self):
        """Hardening of review finding 5: at 560 s the alert fires when |exposure| > (L/12) x slots_left."""
        cfg = FakeConfig()
        for e, want in ((64, False), (65, True)):           # PORT: L 200, base clip 16, 4 slots left at 560 s
            f = Flatten(make_deps(cfg))
            f.decide(make_ctx(560.0, 1, cfg=cfg, now=T0 + 560.0, positions={"PORT": -e}))
            alerts = [a for a in f.log.kinds("alert") if a.get("alert") == "flatten_behind"]
            self.assertEqual(bool(alerts), want, e)

    def test_behind_alert_fires_only_with_a_full_limit_left_at_560(self):
        """§6.3: at 560 s alert flatten_behind for |exposure| > clip * slots_left. With clip = min(max(L/12,
        ceil(|e|/slots)), L/4) and slots_left = 4 at 560 s, clip * slots_left >= |e| unless |e| > 4 * floor(L/4),
        i.e. only with ~a full limit still open (season 401 trace: rounds 1 and 2 ended not_flat with no
        flatten_behind alert). This documents the grown clip, which the alert no longer uses (see
        ``test_behind_alert_uses_the_base_clip``)."""
        cfg = FakeConfig()
        f = Flatten(make_deps(cfg))
        for sym in SYMBOLS:
            if sym == "REIN":
                continue
            L = {"PORT": 200, "ISLE": 300, "AQUA": 500, "GRID": 400, "FERRY": 800, "RECON": 500, "MAT": 700,
                 "GEN": 600, "HAVEN": 250, "ARCH": 50, "REBLD": 100}[sym]
            first = None
            for e in range(1, L + 1):
                ctx = make_ctx(560.0, 1, cfg=cfg, now=T0 + 560.0, positions={sym: -e})
                clip, slots = f.clip_for(ctx, sym, -e, 560.0, 568.0)
                self.assertEqual(slots, 4)
                if e > clip * slots and first is None:
                    first = e
            self.assertTrue(first is None or first >= 0.96 * L, (sym, first))

if __name__ == "__main__":
    unittest.main()
