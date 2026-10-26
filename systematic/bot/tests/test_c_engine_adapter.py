"""Group C: ``ctcbot.models.engine_adapter`` (DESIGN.md section 9.1 C, engine_adapter)."""
from __future__ import annotations

import copy
import json
import math
import os
import sys
import threading
import time
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402
from test_c_support import FakeConfig  # noqa: E402
from ctcbot.models.engine_adapter import (EngineAdapter, load_engine_module, merge_adjustments,  # noqa: E402
                                          snapshot_fix_present)
from ctcbot.types import SYMBOLS, ListLog  # noqa: E402

PROBE = os.path.join(BOT, "ctcbot", "models", "data", "snapshot_probe.jsonl")


class FakeTime:
    def __init__(self, t: float = 100.0) -> None:
        self.t = t

    def mono(self) -> float:
        return self.t

    def wall(self) -> float:
        return 1000.0 + self.t


class FakeState:
    """SeasonState stand-in with the engine's adjustment-file semantics."""

    def __init__(self, params=None, case=None, persist_path=None, adjust_file=None, library_seasons=None,
                 library=None, **kw):
        self.kw = {"params": params, "library_seasons": library_seasons, "library": library}
        self.persist_path = persist_path
        self.adjust_file = adjust_file
        self._adjust_mtime = None
        self.adjust: dict = {}
        self.overrides: dict = {}
        self.P0 = {"settle": {"x": 1.0}}
        self.P = copy.deepcopy(self.P0)
        self.announcements: list = []
        self.log: list = []
        self.fail = False
        self.gate: threading.Event | None = None
        self.compute_calls = 0
        self.reset()

    def reset(self):
        self.version = 0
        self.seen: set = set()
        self.n = 0

    def on_message(self, m):
        if m.get("type") == "boom":
            raise ValueError("bad message")
        s = m.get("seq")
        if s is not None and s in self.seen:
            return False
        if s is not None:
            self.seen.add(s)
        if m.get("type") in ("announcement", "news"):
            self.announcements.append(m)
        self.n += 1
        self.version += 1
        self.log.append(m)
        return True

    def set_adjustment(self, symbol, mult=1.0, add=0.0):
        if mult == 1.0 and add == 0.0:
            self.adjust.pop(symbol, None)
        else:
            self.adjust[symbol] = [float(mult), float(add)]

    def clear_adjustments(self):
        self.adjust = {}

    def override(self, dotted, value):
        self.overrides[dotted] = value
        self.P = copy.deepcopy(self.P0)
        self.P["settle"].update({k: v for k, v in self.overrides.items()})

    def _poll_adjust_file(self):
        if not self.adjust_file:
            return
        try:
            mt = os.stat(self.adjust_file).st_mtime_ns
        except OSError:
            return
        if mt == self._adjust_mtime:
            return
        self._adjust_mtime = mt
        with open(self.adjust_file) as f:
            d = json.load(f)
        self.adjust = {}
        for s, v in d.items():
            if s in SYMBOLS:
                self.set_adjustment(s, v.get("mult", 1.0), v.get("add", 0.0))
        self.overrides = {}
        for k, v in (d.get("_overrides") or {}).items():
            self.override(k, v)

    def compute(self):
        self._poll_adjust_file()
        self.compute_calls += 1
        if self.gate is not None:
            self.gate.wait(5.0)
        if self.fail:
            raise RuntimeError("compute failed")
        out = {}
        for i, s in enumerate(SYMBOLS):
            m, a = self.adjust.get(s, (1.0, 0.0))
            out[s] = ((10.0 + self.n + i) * m + a, 1.0)
        return out

    def cumulative_damage(self):
        return {"PORT": float(self.n)}

    def season_loss(self):
        return 0.0

    def status(self, r):
        return "final" if any(m.get("type") == "damage_report" and m.get("round") == r for m in self.log) else "unformed"

    def pattern_posterior(self):
        return None

    @classmethod
    def restore(cls, persist_path, **kw):
        obj = cls(**kw)
        with open(persist_path) as f:
            for line in f:
                if line.strip():
                    obj.on_message(json.loads(line))
        obj.persist_path = persist_path
        return obj


def wait_for(pred, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return bool(pred())


class FakeEngineTest(unittest.TestCase):
    def setUp(self):
        self.cfg = FakeConfig()
        self.cfg.set("engine.periodic_compute_s", 0.05)
        self.log = ListLog()
        self.ts = FakeTime()
        self.adapters: list[EngineAdapter] = []

    def tearDown(self):
        for a in self.adapters:
            a.close()

    def make(self, mode="thread", **kw) -> EngineAdapter:
        a = EngineAdapter(self.cfg, {"case": 1}, self.ts, self.log, mode=mode, state_factory=FakeState, **kw)
        self.adapters.append(a)
        return a

    def test_thread_mode_listener_and_values(self):
        a = self.make()
        got = threading.Event()
        a.set_listener(got.set)
        self.assertTrue(wait_for(lambda: a.latest() is not None))
        got.clear()
        a.submit([{"type": "storm", "round": 1, "seq": 5, "time": "1802-05-10T10:00:00"},
                  {"type": "news", "message": {"type": "storm", "round": 1, "seq": 6}}], 99.0)
        self.assertTrue(got.wait(5.0))
        self.assertTrue(wait_for(lambda: a.latest().seq_hi == 6))
        fv = a.latest()
        self.assertTrue(fv.ok)
        self.assertEqual(fv.round, 1)
        self.assertEqual(fv.engine_version, 2)
        self.assertEqual(fv.values["PORT"], (12.0, 1.0))
        self.assertEqual(set(fv.values), set(SYMBOLS))
        self.assertEqual(fv.included_mono, 99.0)
        self.assertEqual(fv.diag.get("status_r3"), "unformed")
        self.assertEqual(a.health(100.0).pending, 0)

    def test_stale_tracking(self):
        a = self.make()
        self.assertTrue(wait_for(lambda: a.latest() is not None))
        a.st.gate = threading.Event()
        a.submit([{"type": "storm", "round": 1, "seq": 1}], 90.0)
        self.assertTrue(wait_for(lambda: a.st.compute_calls >= 2))
        h = a.health(100.0)
        self.assertEqual(h.pending, 1)
        self.assertAlmostEqual(h.oldest_pending_age_s, 10.0)
        a.st.gate.set()
        self.assertTrue(wait_for(lambda: a.health(100.0).oldest_pending_age_s is None))
        # a duplicate message changes nothing but must not stay pending either
        v = a.latest().version
        a.submit([{"type": "storm", "round": 1, "seq": 1}], 95.0)
        self.assertTrue(wait_for(lambda: a.health(100.0).pending == 0))
        self.assertEqual(a.latest().version, v)

    def test_error_counting_and_recovery(self):
        a = self.make()
        self.assertTrue(wait_for(lambda: a.latest() is not None))
        a.st.fail = True
        for k in range(3):
            v = a.latest().version
            a.submit([{"type": "storm", "round": 1, "seq": 10 + k}], 100.0)
            self.assertTrue(wait_for(lambda: a.latest().version > v))
            self.assertFalse(a.latest().ok)
            self.assertIsNotNone(a.latest().error)
        h = a.health(100.0)
        self.assertGreaterEqual(h.consecutive_errors, 3)
        self.assertFalse(h.alive)
        self.assertGreaterEqual(h.pending, 1)
        self.assertTrue(any(f["alert"] == "engine_down" for f in self.log.kinds("alert")))
        self.assertEqual(a.latest().values["PORT"][0], 10.0)    # the previous values are kept
        a.st.fail = False
        a.submit([{"type": "storm", "round": 1, "seq": 20}], 100.0)
        self.assertTrue(wait_for(lambda: a.latest().ok))
        self.assertEqual(a.health(100.0).consecutive_errors, 0)
        self.assertTrue(a.health(100.0).alive)

    def test_bad_message_is_skipped(self):
        a = self.make(mode="inline")
        a.submit([{"type": "boom", "seq": 1}, {"type": "storm", "round": 2, "seq": 2}], 1.0)
        self.assertTrue(a.latest().ok)
        self.assertEqual(a.latest().seq_hi, 2)
        self.assertTrue(any(f.get("where") == "on_message" for f in self.log.kinds("engine_error")))

    def test_versions_monotonic_and_engine_version(self):
        d = testpaths.tmpdir("eng")
        a = self.make(mode="inline", adjust_dir=d)
        seen = [a.latest().version]
        a.submit([{"type": "storm", "round": 1, "seq": 1}], 1.0)
        seen.append(a.latest().version)
        self.assertEqual(a.latest().engine_version, 1)
        a.set_adjustment("PORT", 1.0, 3.0)                       # adjustment-only change
        seen.append(a.latest().version)
        self.assertEqual(a.latest().engine_version, 1)
        self.assertEqual(a.latest().values["PORT"][0], 14.0)
        a.reset_season(1, None)
        seen.append(a.latest().version)
        self.assertEqual(a.latest().engine_version, 0)
        self.assertEqual(a.latest().season_epoch, 1)
        self.assertIsNone(a.latest().seq_hi)
        a.submit([{"type": "storm", "round": 1, "seq": 1}], 2.0)
        seen.append(a.latest().version)
        self.assertEqual(seen, sorted(set(seen)), seen)
        b = self.make(mode="inline")
        self.ts.t += 1.0
        c = self.make(mode="inline")
        self.assertGreater(c.latest().version, b.latest().version)

    def test_adjust_merge_console_wins(self):
        d = testpaths.tmpdir("adj")
        with open(os.path.join(d, "adjust.json"), "w") as f:
            json.dump({"PORT": {"mult": 1, "add": -2.0}, "GRID": {"mult": 2.0, "add": 0.0},
                       "_overrides": {"settle.a": 1, "settle.b": 2}, "BOGUS": {"add": 1}}, f)
        a = self.make(adjust_dir=d)
        self.assertTrue(wait_for(lambda: a.latest() is not None and a.latest().values["GRID"][0] == 2 * 13.0))
        self.assertEqual(a.latest().values["PORT"][0], 8.0)
        a.set_adjustment("PORT", 1.0, 5.0)
        a.override("settle.b", 3)
        self.assertTrue(wait_for(lambda: a.latest().values["PORT"][0] == 15.0))
        with open(os.path.join(d, "adjust_engine.json")) as f:
            merged = json.load(f)
        self.assertEqual(merged["PORT"], {"mult": 1.0, "add": 5.0})
        self.assertEqual(merged["GRID"], {"mult": 2.0, "add": 0.0})
        self.assertEqual(merged["_overrides"], {"settle.a": 1, "settle.b": 3})
        self.assertNotIn("BOGUS", merged)
        with open(os.path.join(d, "adjust_console.json")) as f:
            console = json.load(f)
        self.assertEqual(console, {"PORT": {"mult": 1.0, "add": 5.0}, "_overrides": {"settle.b": 3}})
        with open(os.path.join(d, "adjust.json")) as f:
            self.assertEqual(json.load(f)["PORT"], {"mult": 1, "add": -2.0})     # the analyst's file is untouched
        self.assertTrue(any("BOGUS" in p for f in self.log.kinds("adjust_merge") for p in f["problems"]))
        a.set_adjustment("PORT", 1.0, 0.0)                     # f PORT off: the analyst's entry applies again
        self.assertTrue(wait_for(lambda: a.latest().values["PORT"][0] == 8.0))
        # the analyst edits adjust.json live
        time.sleep(0.01)
        with open(os.path.join(d, "adjust.json"), "w") as f:
            json.dump({"ISLE": {"mult": 1, "add": 1.0}}, f)
        self.assertTrue(wait_for(lambda: a.latest().values["ISLE"][0] == 12.0 and
                                 a.latest().values["GRID"][0] == 13.0))

    def test_reset_season_clears_and_rotates(self):
        d = testpaths.tmpdir("rot")
        with open(os.path.join(d, "adjust.json"), "w") as f:
            json.dump({"PORT": {"mult": 1, "add": -2.0}}, f)
        a = self.make(mode="inline", adjust_dir=d, run_id="R1")
        a.set_adjustment("HAVEN", 1.0, 1.0)
        a.submit([{"type": "announcement", "seq": 0, "headline": "x", "symbols": ["PORT"]}], 1.0)
        self.assertEqual(len(a.st.announcements), 1)
        a.st.override("settle.k", 9)
        a.reset_season(1, os.path.join(d, "engine_state_R1_e1.jsonl"))
        self.assertEqual(a.st.announcements, [])
        self.assertEqual(a.st.overrides, {})
        self.assertEqual(a.st.adjust, {})
        self.assertEqual(a.st.P, a.st.P0)
        self.assertEqual(a.st.persist_path, os.path.join(d, "engine_state_R1_e1.jsonl"))
        for name in ("adjust.json", "adjust_console.json", "adjust_engine.json"):
            with open(os.path.join(d, name)) as f:
                self.assertEqual(json.load(f), {}, name)
        with open(os.path.join(d, "adjust_R1_e0.json")) as f:
            self.assertEqual(json.load(f), {"PORT": {"mult": 1, "add": -2.0}})
        with open(os.path.join(d, "adjust_console_R1_e0.json")) as f:
            self.assertEqual(json.load(f), {"HAVEN": {"mult": 1.0, "add": 1.0}})
        self.assertEqual(a.latest().values["PORT"][0], 10.0)

    def test_direct_adjustments_without_adjust_dir(self):
        a = self.make()
        self.assertTrue(wait_for(lambda: a.latest() is not None))
        a.set_adjustment("AQUA", 0.0, 7.5)
        self.assertTrue(wait_for(lambda: a.latest().values["AQUA"][0] == 7.5))
        a.override("settle.z", 4)
        self.assertTrue(wait_for(lambda: a.st.overrides.get("settle.z") == 4))
        with self.assertRaises(ValueError):
            a.set_adjustment("NOPE", 1.0, 1.0)
        with self.assertRaises(ValueError):
            a.set_adjustment("PORT", math.nan, 1.0)

    def test_inline_listener_is_synchronous(self):
        a = self.make(mode="inline")
        calls = []
        a.set_listener(lambda: calls.append(a.latest().version))
        a.submit([{"type": "storm", "round": 1, "seq": 1}], 5.0)
        self.assertEqual(len(calls), 1)
        a.submit([{"type": "storm", "round": 1, "seq": 1}], 5.0)   # duplicate: nothing new to publish
        self.assertEqual(len(calls), 1)

    def test_snapshot_bug_forces_never(self):
        a = self.make(mode="inline", snapshot_fix=False)
        self.assertEqual(a.forward_snapshots, "never")
        self.assertTrue(any(f["alert"] == "engine_snapshot_bug" for f in self.log.kinds("alert")))
        a.submit([{"type": "snapshot", "last_seq": 5}, {"type": "storm", "round": 1, "seq": 1}], 1.0)
        self.assertEqual([m["type"] for m in a.st.log], ["storm"])
        self.assertFalse(a.health(1.0).snapshot_fix)
        b = self.make(mode="inline", snapshot_fix=True)
        self.assertEqual(b.forward_snapshots, "relevant")
        b.submit([{"type": "news", "message": {"type": "snapshot", "last_seq": 5}}], 1.0)
        self.assertEqual([m["type"] for m in b.st.log], ["snapshot"])

    def test_restore_from_log(self):
        d = testpaths.tmpdir("restore")
        p = os.path.join(d, "engine_state_R_e0.jsonl")
        with open(p, "w") as f:
            for m in ({"type": "storm", "round": 2, "seq": 40, "time": "1802-05-11T10:00:00"},
                      {"type": "storm", "round": 2, "seq": 41, "time": "1802-05-11T10:01:00"}):
                f.write(json.dumps(m) + "\n")
        a = self.make(mode="inline", persist_path=p, restore=True)
        self.assertEqual(a.st.n, 2)
        self.assertEqual(a.latest().seq_hi, 41)
        self.assertEqual(a.latest().round, 2)
        self.assertEqual(a.latest().msg_time, "1802-05-11T10:01:00")
        with self.assertRaises(ValueError):
            EngineAdapter(self.cfg, {}, self.ts, self.log, mode="inline", restore=True, state_factory=FakeState)

    def test_close_is_idempotent_and_merge_helper(self):
        a = self.make()
        a.close()
        a.close()
        self.assertFalse(a.health(0.0).alive)
        a.submit([{"type": "storm", "seq": 1}], 0.0)        # ignored after close
        merged, problems = merge_adjustments({"PORT": {"mult": "x"}, "_overrides": 3}, {"ISLE": {"add": 1}})
        self.assertEqual(merged, {"ISLE": {"mult": 1.0, "add": 1.0}})
        self.assertEqual(len(problems), 2)


def feed_until(n: int, round_: int, t_max: float) -> list[list[dict]]:
    """Season n's messages of rounds <= round_ up to real second t_max of round_, grouped by (round, time)."""
    path = testpaths.require(os.path.join(testpaths.HISTORY, f"season-{n:03d}", "feed.jsonl"))
    groups: list[list[dict]] = []
    key = None
    with open(path) as f:
        for line in f:
            m = json.loads(line)
            hh, mm, ss = (int(x) for x in m["time"].split("T")[1].split(":"))
            t = (hh * 3600 + mm * 60 + ss - 34200) / 30.0
            r = int(m["round"])
            if (r, t) > (round_, t_max):
                break
            k = (r, t)
            if k != key:
                groups.append([])
                key = k
            groups[-1].append(m)
    return groups


class RealEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        testpaths.require(os.path.join(testpaths.ENGINE_DIR, "fairvalue.py"))
        with open(testpaths.require(testpaths.RESEARCH_CASE)) as f:
            cls.case = json.load(f)

    def test_probe_detects_the_fix(self):
        testpaths.require(PROBE)
        testpaths.require(os.path.join(testpaths.ENGINE_DIR, "fairvalue_orig.py"))
        params = os.path.join(testpaths.ENGINE_DIR, "engine_params_fast.json")
        cache_dir = os.path.join(testpaths.ENGINE_DIR, "__pycache__")

        def pyc_state():
            if not os.path.isdir(cache_dir):
                return {}
            return {f: os.path.getmtime(os.path.join(cache_dir, f)) for f in os.listdir(cache_dir)}
        before = pyc_state()
        fixed = load_engine_module(testpaths.ENGINE_DIR, "fairvalue.py")
        orig = load_engine_module(testpaths.ENGINE_DIR, "fairvalue_orig.py")
        self.assertIsNot(fixed, orig)
        self.assertTrue(snapshot_fix_present(fixed, PROBE, self.case, params))
        self.assertFalse(snapshot_fix_present(orig, PROBE, self.case, params))
        self.assertEqual(pyc_state(), before, "bytecode was written into the engine folder")
        bad = os.path.join(testpaths.tmpdir("probe"), "bad.jsonl")
        with open(bad, "w") as f:
            f.write(json.dumps({"type": "storm", "round": 1, "seq": 0}) + "\n")
        with self.assertRaises(ValueError):
            snapshot_fix_present(fixed, bad, self.case, params)

    def test_inline_matches_direct_replay(self):
        cfg = FakeConfig()
        groups = feed_until(401, 1, 200.0)
        mod = load_engine_module(testpaths.ENGINE_DIR)
        direct = mod.SeasonState(params=os.path.join(testpaths.ENGINE_DIR, cfg.get("engine.params")), case=self.case,
                                 library_seasons=tuple(cfg.get("engine.library_seasons")))
        log = ListLog()
        a = EngineAdapter(cfg, self.case, FakeTime(), log, mode="inline", snapshot_fix=True)
        try:
            for k, g in enumerate(groups):
                a.submit(g, float(k))
                for m in g:
                    direct.on_message(m)
                if k % 25 == 0 or k == len(groups) - 1:
                    want = mod.fair_values(direct)
                    got = a.latest().values
                    for s in SYMBOLS:
                        self.assertEqual(got[s], (float(want[s][0]), float(want[s][1])), f"group {k} {s}")
            fv = a.latest()
            self.assertEqual(fv.seq_hi, max(m["seq"] for g in groups for m in g))
            self.assertEqual(fv.round, 1)
            self.assertEqual(fv.engine_version, direct.version)
            self.assertEqual(fv.diag["status_r3"], "unformed")
            self.assertIsNotNone(fv.diag["pattern"])
        finally:
            a.close()

    def test_adapter_runs_probe_itself(self):
        cfg = FakeConfig()
        log = ListLog()
        a = EngineAdapter(cfg, self.case, FakeTime(), log, mode="inline")
        try:
            self.assertTrue(a.snapshot_fix)
            self.assertEqual(a.forward_snapshots, "relevant")
            self.assertTrue(a.health(0.0).snapshot_fix)
        finally:
            a.close()

    def test_slow_reproduces_holdout_cache(self):
        testpaths.slow()
        cache = testpaths.require(os.path.join(testpaths.FV_CACHE, "fv_holdout_401.json"))
        with open(cache) as f:
            entries = json.load(f)["entries"]
        cfg = FakeConfig()
        cfg.set("engine.params", "engine_params.json")
        a = EngineAdapter(cfg, self.case, FakeTime(), ListLog(), mode="inline", library_seasons=(1, 400),
                          snapshot_fix=True)
        try:
            groups = feed_until(401, 3, 10_000.0)
            self.assertEqual(len(groups) + 1, len(entries))
            worst = 0.0

            def compare(entry):
                nonlocal worst
                fv = a.latest()
                for i, s in enumerate(SYMBOLS):
                    worst = max(worst, abs(fv.values[s][0] - entry[2 + i]), abs(fv.values[s][1] - entry[14 + i]))
            compare(entries[0])
            for g, e in zip(groups, entries[1:]):
                a.submit(g, 0.0)
                compare(e)
            self.assertLessEqual(worst, 5e-4)
        finally:
            a.close()


if __name__ == "__main__":
    unittest.main()
