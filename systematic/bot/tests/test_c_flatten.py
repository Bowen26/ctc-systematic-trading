"""Group C: the rounds 1-2 flattener (DESIGN.md sections 6.3 and 9.1 C, flatten)."""
from __future__ import annotations

import math
import os
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

from test_c_support import FakeConfig, make_ctx, make_deps, targets  # noqa: E402
from ctcbot.strategies.flatten import Flatten  # noqa: E402
from ctcbot.types import Command, Priority  # noqa: E402


class FlattenTest(unittest.TestCase):
    def setUp(self):
        self.cfg = FakeConfig()
        self.fl = Flatten(make_deps(self.cfg))

    def step(self, t, round_=1, positions=None, **kw):
        return targets(self.fl.decide(make_ctx(t, round_, cfg=self.cfg, positions=positions or {}, **kw)))

    def test_window_and_rounds(self):
        pos = {"PORT": 100, "RECON": -50}
        self.assertEqual(self.step(539.9, positions=pos), {})
        out = self.step(540.0, positions=pos)
        self.assertEqual(set(out), {"PORT", "RECON"})
        p, r = out["PORT"], out["RECON"]
        self.assertEqual((p.target, p.priority, p.clip_interval_s, p.ttl_s), (0, Priority.HEDGE, 2.0, 2.0))
        self.assertTrue(p.reduce_only and r.reduce_only)
        self.assertEqual(p.clip_qty, 16)                          # L/12 while on schedule
        self.assertEqual(p.limit_sell, 44.71)                     # max(bid * 0.995, mid * 0.985)
        self.assertIsNone(p.limit_buy)
        self.assertEqual(r.limit_buy, 52.34)                      # min(ask * 1.005, mid * 1.015)
        self.assertIn("PORT", self.step(567.9, positions=pos, round_=2))
        self.assertEqual(self.step(550.0, positions=pos, round_=3), {})
        self.assertEqual(self.step(568.0, positions=pos, round_=2), {})

    def test_rein_is_kept_and_frozen_is_flattened(self):
        out = self.step(550.0, positions={"REIN": -200, "PORT": 30}, frozen={"PORT"})
        self.assertEqual(set(out), {"PORT"})
        self.step(568.0, positions={"REIN": -200})
        self.assertEqual(self.fl.log.kinds("alert"), [])

    def test_full_limit_from_548_is_flat_by_568(self):
        L = 200
        pos = L
        clips = []
        last_send = -1e9
        t = 548.0
        while t < 568.0:
            out = self.step(t, positions={"PORT": pos})
            it = out.get("PORT")
            if pos != 0:
                self.assertIsNotNone(it, t)
            if it is not None and t - last_send >= it.clip_interval_s - 1e-9:
                q = min(it.clip_qty, abs(pos))
                clips.append(it.clip_qty)
                self.assertLessEqual(it.clip_qty, L // 4)
                pos -= q                                          # the fake fills every clip at once
                last_send = t
            t = round(t + 0.25, 2)
        self.assertEqual(pos, 0)
        self.assertGreater(clips[0], L // 12)                     # the clip grew at once (20 > 16)
        # hardening: "behind" is judged against the base clip (L/12 x slots left): 80 > 16 x 4 at 560 s, so the
        # operator is told once, although the grown clips still finish by 568 s
        alerts = self.fl.log.kinds("alert")
        self.assertEqual([(a["alert"], sorted(a["exposure"])) for a in alerts], [("flatten_behind", ["PORT"])])

    def test_clip_formula(self):
        ctx = make_ctx(548.0, 1, cfg=self.cfg)
        self.assertEqual(self.fl.clip_for(ctx, "PORT", 200, 548.0, 568.0), (20, 10))
        self.assertEqual(self.fl.clip_for(ctx, "PORT", 10, 548.0, 568.0), (16, 10))
        self.assertEqual(self.fl.clip_for(ctx, "PORT", 200, 566.5, 568.0), (50, 1))       # capped at L/4
        self.assertEqual(self.fl.clip_for(ctx, "FERRY", -700, 556.0, 568.0),
                         (min(max(66, math.ceil(700 / 6)), 200), 6))

    def test_flatten_behind_and_not_flat(self):
        self.step(559.0, positions={"PORT": 200})
        self.assertEqual(self.fl.log.kinds("alert"), [])
        self.step(560.1, positions={"PORT": 200})                 # 3 slots of 50 cannot clear 200
        alerts = [a for a in self.fl.log.kinds("alert") if a["alert"] == "flatten_behind"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["exposure"], {"PORT": 200})
        self.step(561.0, positions={"PORT": 200})
        self.assertEqual(len([a for a in self.fl.log.kinds("alert") if a["alert"] == "flatten_behind"]), 1)
        self.step(568.0, positions={"PORT": 20, "REIN": -100})
        self.step(569.0, positions={"PORT": 20})
        nf = [a for a in self.fl.log.kinds("alert") if a["alert"] == "not_flat"]
        self.assertEqual(len(nf), 1)
        self.assertEqual(nf[0]["positions"], {"PORT": 20})
        self.step(568.0, round_=2, positions={})
        self.assertEqual(len([a for a in self.fl.log.kinds("alert") if a["alert"] == "not_flat"]), 1)

    def test_scaled_limits_and_command(self):
        cfg = FakeConfig()
        cfg.set("sizing.scale", 0.25)
        fl = Flatten(make_deps(cfg))
        out = targets(fl.decide(make_ctx(548.0, 1, cfg=cfg, positions={"PORT": 50})))
        self.assertEqual(out["PORT"].clip_qty, 5)                 # L_s 50: max(4, ceil(50 / 10)) <= 12
        ctx = make_ctx(548.0, 1, cfg=cfg)
        self.assertEqual(fl.on_command(Command("flat", ("off",)), ctx), [])
        self.assertEqual(targets(fl.decide(make_ctx(548.0, 1, cfg=cfg, positions={"PORT": 50}))), {})
        self.assertIsNone(fl.on_command(Command("flatten", ("on",)), ctx))


if __name__ == "__main__":
    unittest.main()
