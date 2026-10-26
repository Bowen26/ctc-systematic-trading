"""Group D unit tests: run ids, atomic state files and the restart rule (DESIGN.md §9.1 D persist)."""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from unittest import mock

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402

from ctcbot.types import ServerTime  # noqa: E402
from ctcbot.app import persist  # noqa: E402
from ctcbot.app.persist import (RestartPlan, engine_log_name, ledger_from_wall, ledger_to_wall, load_state,  # noqa: E402
                                new_run_id, read_engine_log, restart_plan, save_state)


class TestFiles(unittest.TestCase):
    def test_round_trip(self):
        d = testpaths.tmpdir("persist")
        p = os.path.join(d, "state.json")
        st = {"saved_wall": 1.8e9, "comp_id": "c1", "orders": {"recs": [1, 2]}, "set": {3, 1}, "dry_run": True}
        save_state(p, st)
        got = load_state(p)
        self.assertEqual(got["comp_id"], "c1")
        self.assertEqual(got["set"], [1, 3])
        self.assertEqual([f for f in os.listdir(d)], ["state.json"])       # no temp files left behind

    def test_missing_corrupt_and_non_object(self):
        d = testpaths.tmpdir("persist2")
        self.assertIsNone(load_state(os.path.join(d, "nope.json")))
        p = os.path.join(d, "bad.json")
        with open(p, "w") as f:
            f.write('{"half": ')
        self.assertIsNone(load_state(p))
        with open(p, "w") as f:
            f.write("[1, 2]")
        self.assertIsNone(load_state(p))

    def test_atomic_write_keeps_old_state_on_failure(self):
        d = testpaths.tmpdir("persist3")
        p = os.path.join(d, "state.json")
        save_state(p, {"v": 1})
        with mock.patch("ctcbot.app.persist.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                save_state(p, {"v": 2})
        self.assertEqual(load_state(p), {"v": 1})
        self.assertEqual(os.listdir(d), ["state.json"])

    def test_concurrent_writers_never_tear(self):
        d = testpaths.tmpdir("persist4")
        p = os.path.join(d, "state.json")
        big = {"x": "y" * 50_000}
        errors: list[Exception] = []

        def writer() -> None:
            for i in range(20):
                save_state(p, dict(big, i=i))

        def reader() -> None:
            for _ in range(200):
                v = load_state(p)
                if v is not None and v.get("x") != big["x"]:
                    errors.append(AssertionError("torn read"))

        save_state(p, big)
        ts = [threading.Thread(target=writer) for _ in range(3)] + [threading.Thread(target=reader)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errors, [])

    def test_run_ids_unique_and_engine_log_names(self):
        ids = {new_run_id() for _ in range(200)}
        self.assertGreater(len(ids), 150)                         # 4 random hex digits per second
        rid = new_run_id()
        self.assertRegex(rid, r"^\d{8}T\d{6}[0-9a-f]{4}$")
        a, b = new_run_id(), new_run_id()
        fmt = "engine_state_{run_id}_e{epoch}.jsonl"
        if a != b:
            self.assertNotEqual(engine_log_name(fmt, a, 0), engine_log_name(fmt, b, 0))
        self.assertEqual(engine_log_name(fmt, "R", 2), "engine_state_R_e2.jsonl")
        self.assertEqual(engine_log_name("{bad", "R", 1), "engine_state_R_e1.jsonl")
        self.assertEqual(engine_log_name("../x/{run_id}.jsonl", "R", 1), "R.jsonl")   # never a path

    def test_read_engine_log(self):
        d = testpaths.tmpdir("persist5")
        p = os.path.join(d, "engine.jsonl")
        with open(p, "w") as f:
            f.write('{"type":"storm","seq":1}\n\nnot json\n[1]\n{"type":"track","seq":2}\n')
        self.assertEqual([m["seq"] for m in read_engine_log(p)], [1, 2])
        self.assertEqual(read_engine_log(os.path.join(d, "missing")), [])
        self.assertEqual(read_engine_log(None), [])

    def test_ledger_conversion(self):
        wall = ledger_to_wall([95.0, 99.5], now_mono=100.0, now_wall=1000.0)
        self.assertEqual(wall, [995.0, 999.5])
        back = ledger_from_wall(wall + [900.0, "x", 2000.0], now_mono=50.0, now_wall=1010.0)
        self.assertEqual(back, [35.0, 39.5])                       # 900 is too old, 2000 is in the future


class TestRestartPlan(unittest.TestCase):
    ST = ServerTime("comp-7", "running", "1802-05-10T11:00:00", 100.0)

    def saved(self, **kw):
        d = {"saved_wall": 1000.0, "comp_id": "comp-7", "dry_run": True, "engine_log": "engine_state_A_e0.jsonl"}
        d.update(kw)
        return d

    def test_cases(self):
        p = restart_plan(None, self.ST, 1010.0, True, "host")
        self.assertEqual((p.restore, p.promotion), (False, False))
        p = restart_plan(self.saved(), self.ST, 1010.0, True, "sim")
        self.assertFalse(p.restore)                                # never in sim
        p = restart_plan(self.saved(), self.ST, 1010.0, True, "host")
        self.assertEqual((p.restore, p.promotion, p.engine_log), (True, False, "engine_state_A_e0.jsonl"))
        p = restart_plan(self.saved(), self.ST, 1010.0, True, "replay")
        self.assertTrue(p.restore)
        p = restart_plan(self.saved(comp_id="comp-6"), self.ST, 1010.0, True, "host")
        self.assertFalse(p.restore)
        self.assertIn("comp-6 -> comp-7", p.reason)
        p = restart_plan(self.saved(), None, 1010.0, True, "host")
        self.assertFalse(p.restore)                                # cannot verify: fail closed
        p = restart_plan(self.saved(comp_id=None), self.ST, 1010.0, True, "host")
        self.assertFalse(p.restore)

    def test_promotion_both_ways(self):
        p = restart_plan(self.saved(dry_run=True), self.ST, 1010.0, False, "host")
        self.assertEqual((p.restore, p.promotion), (True, True))
        self.assertIn("dry-run -> live", p.reason)
        p = restart_plan(self.saved(dry_run=False), self.ST, 1010.0, True, "host")
        self.assertEqual((p.restore, p.promotion), (True, True))
        self.assertIn("live -> dry-run", p.reason)
        p = restart_plan(self.saved(dry_run=False), self.ST, 1010.0, False, "host")
        self.assertEqual((p.restore, p.promotion), (True, False))

    def test_server_time_shapes(self):
        for st in (self.ST, "comp-7", {"competition_id": "comp-7"}, {"comp_id": "comp-7"}):
            self.assertTrue(restart_plan(self.saved(), st, 1010.0, True, "host").restore, st)
        self.assertIsInstance(restart_plan(self.saved(), self.ST, 1010.0, True, "host"), RestartPlan)

    def test_engine_log_is_a_basename(self):
        p = restart_plan(self.saved(engine_log="/etc/../tmp/engine_state_A_e0.jsonl"), self.ST, 1010.0, True, "host")
        self.assertEqual(p.engine_log, "engine_state_A_e0.jsonl")


if __name__ == "__main__":
    unittest.main()
