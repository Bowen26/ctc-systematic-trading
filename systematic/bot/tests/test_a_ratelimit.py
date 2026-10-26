"""Group A tests: ctcbot.ratelimit (DESIGN.md §9.1 A ratelimit)."""
import bisect
import math
import os
import random
import sys
import threading
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402,F401
from ctcbot.config import load_config  # noqa: E402
from ctcbot.ratelimit import ActionLimiter, RequestLimiter  # noqa: E402
from ctcbot.types import Priority, Ticket  # noqa: E402

FLOORS = {Priority.KILL: 0, Priority.SNIPE: 1, Priority.HEDGE: 4, Priority.TAKE: 10}


def limiter(cap=150):
    # max_cap = team - 10 - overdraft (hardening): the KILL ceiling max_cap + overdraft = 170 is absolute
    return ActionLimiter(cap, 61.0, FLOORS, 20, 150)


def max_in_window(times, window):
    """Largest number of times inside any half-open window [a, a + window)."""
    times = sorted(times)
    best = 0
    for i, t in enumerate(times):
        j = bisect.bisect_left(times, t + window)
        best = max(best, j - i)
    return best


class TestActionLimiter(unittest.TestCase):
    def test_random_load_30_minutes(self):
        rng = random.Random(11)
        lim = limiter()
        grants = {p: [] for p in Priority}
        now = 0.0
        while now < 1800.0:
            now += rng.expovariate(4.0)
            p = rng.choices(list(Priority), weights=[0.03, 0.2, 0.3, 0.47])[0]
            n = rng.choice([1, 1, 1, 2, 3, 12])
            got = lim.try_acquire(p, n, now, tag="x")
            self.assertIn(len(got), (0, n))
            grants[p] += [now] * len(got)
            if rng.random() < 0.002:
                lim.on_throttled(now, 5.0)
        non_kill = [t for p in Priority if p is not Priority.KILL for t in grants[p]]
        every = non_kill + grants[Priority.KILL]
        self.assertGreater(len(non_kill), 2000)
        self.assertGreater(len(grants[Priority.KILL]), 10)
        self.assertLessEqual(max_in_window(non_kill, 61.0), 150)
        self.assertLessEqual(max_in_window(every, 61.0), 170)
        self.assertLessEqual(max_in_window(every, 60.0), 170)

    def test_floors(self):
        lim = limiter()
        self.assertEqual(len(lim.try_acquire(Priority.TAKE, 140, 0.0)), 140)
        self.assertEqual(lim.available(0.0), 10)
        self.assertEqual(lim.try_acquire(Priority.TAKE, 1, 0.1), [])
        self.assertEqual(len(lim.try_acquire(Priority.HEDGE, 6, 0.1)), 6)
        self.assertEqual(lim.try_acquire(Priority.HEDGE, 1, 0.1), [])
        self.assertEqual(len(lim.try_acquire(Priority.SNIPE, 3, 0.1)), 3)
        self.assertEqual(lim.try_acquire(Priority.SNIPE, 1, 0.1), [])
        self.assertEqual(lim.available(0.1), 1)
        self.assertEqual(len(lim.try_acquire(Priority.KILL, 21, 0.2)), 21)     # 149 + 21 = cap + overdraft
        self.assertEqual(lim.try_acquire(Priority.KILL, 1, 0.2), [])
        self.assertEqual(lim.used(0.2), 170)
        self.assertEqual(lim.available(0.2), -20)
        self.assertEqual(lim.used(61.0), 30)            # the 140 TAKE grants at t=0 aged out
        self.assertEqual(lim.used(61.2), 0)

    def test_denial_records_nothing(self):
        lim = limiter()
        lim.try_acquire(Priority.TAKE, 135, 0.0)
        before = (lim.used(1.0), lim.ledger())
        self.assertEqual(lim.try_acquire(Priority.TAKE, 6, 1.0), [])     # 15 - 6 < 10: all or nothing
        self.assertEqual((lim.used(1.0), lim.ledger()), before)
        self.assertEqual(lim.stats(1.0)["denied"]["TAKE"], 6)
        tickets = lim.try_acquire(Priority.TAKE, 5, 1.0, tag="s3:PORT")
        self.assertEqual(len(tickets), 5)
        self.assertIsInstance(tickets[0], Ticket)
        self.assertEqual((tickets[0].priority, tickets[0].t, tickets[0].tag), (Priority.TAKE, 1.0, "s3:PORT"))
        self.assertEqual(lim.try_acquire(Priority.TAKE, 0, 1.0), [])

    def test_reservation(self):
        lim = limiter()
        lim.reserve(100.0, 200.0, {Priority.TAKE: 150, Priority.HEDGE: 150})
        self.assertFalse(lim.reserved(99.9))
        self.assertTrue(lim.reserved(100.0))
        self.assertTrue(lim.reserved(199.9))
        self.assertFalse(lim.reserved(200.0))
        self.assertEqual(len(lim.try_acquire(Priority.TAKE, 1, 99.0)), 1)
        self.assertEqual(lim.try_acquire(Priority.TAKE, 1, 150.0), [])
        self.assertEqual(lim.try_acquire(Priority.HEDGE, 1, 150.0), [])
        self.assertEqual(lim.floor(Priority.TAKE, 150.0), 150)
        self.assertEqual(lim.floor(Priority.SNIPE, 150.0), 1)
        self.assertEqual(len(lim.try_acquire(Priority.SNIPE, 24, 150.0)), 24)
        self.assertEqual(len(lim.try_acquire(Priority.TAKE, 1, 200.0)), 1)
        lim.clear_reservations()
        self.assertFalse(lim.reserved(150.0))
        self.assertTrue(lim.stats(150.0)["reserved"] is False)

    def test_throttle_freeze_and_padding(self):
        lim = limiter()
        lim.try_acquire(Priority.TAKE, 20, 0.0)
        lim.on_throttled(10.0, 5.0)
        self.assertTrue(lim.frozen(12.0))
        for p in (Priority.SNIPE, Priority.HEDGE, Priority.TAKE):
            self.assertEqual(lim.try_acquire(p, 1, 12.0), [], p)
        self.assertEqual(len(lim.try_acquire(Priority.KILL, 1, 12.0)), 1)
        self.assertFalse(lim.frozen(15.0))
        self.assertEqual(lim.available(15.0), 9)             # padded to floor(TAKE), then one KILL action
        self.assertEqual(lim.try_acquire(Priority.TAKE, 1, 15.0), [])
        self.assertEqual(len(lim.try_acquire(Priority.HEDGE, 1, 15.0)), 1)
        self.assertEqual(lim.seconds_until(Priority.TAKE, 1, 15.0), 46.0)   # the 20 real grants expire at 61
        self.assertEqual(len(lim.try_acquire(Priority.TAKE, 1, 61.0)), 1)

    def test_seconds_until(self):
        lim = limiter()
        lim.restore([float(k) for k in range(15) for _ in range(10)])   # 150 used: 10 per second for 15 s
        self.assertEqual(lim.used(20.0), 150)
        self.assertEqual(lim.seconds_until(Priority.SNIPE, 1, 20.0), 41.0)  # needs 2 free (floor 1)
        self.assertEqual(lim.seconds_until(Priority.TAKE, 1, 20.0), 42.0)   # needs 11 free: two batches
        self.assertEqual(lim.seconds_until(Priority.KILL, 20, 20.0), 0.0)
        self.assertEqual(lim.seconds_until(Priority.TAKE, 141, 20.0), math.inf)
        lim.reserve(30.0, 50.0, {Priority.TAKE: 150})
        self.assertEqual(lim.seconds_until(Priority.TAKE, 1, 20.0), 42.0)
        lim.reserve(55.0, 70.0, {Priority.TAKE: 150})
        self.assertEqual(lim.seconds_until(Priority.TAKE, 1, 20.0), 50.0)   # blocked until the reservation ends
        lim2 = limiter()
        lim2.on_throttled(0.0, 5.0)
        self.assertEqual(lim2.seconds_until(Priority.SNIPE, 1, 1.0), 4.0)

    def test_cap_setter_clamps(self):
        lim = limiter()
        lim.cap = 500
        self.assertEqual(lim.cap, 150)
        lim.cap = -3
        self.assertEqual(lim.cap, 0)
        lim.cap = 120
        self.assertEqual(lim.cap, 120)
        self.assertEqual(ActionLimiter(999, 61.0, FLOORS, 20, 170).cap, 170)

    def test_kill_ceiling_is_absolute_and_refund(self):
        lim = limiter()
        self.assertEqual(len(lim.try_acquire(Priority.TAKE, 120, 0.0)), 120)
        lim.cap = 100                                    # 'b 100' below the used count
        self.assertEqual(lim.try_acquire(Priority.SNIPE, 1, 1.0), [])
        self.assertEqual(len(lim.try_acquire(Priority.KILL, 50, 1.0)), 50)     # 170 = max_cap + overdraft
        self.assertEqual(lim.try_acquire(Priority.KILL, 1, 1.0), [])
        t = lim.try_acquire(Priority.TAKE, 1, 70.0)[0]
        used = lim.used(70.0)
        self.assertTrue(lim.refund(t, 70.1))
        self.assertEqual(lim.used(70.1), used - 1)
        self.assertFalse(lim.refund(t, 70.1))            # only once
        self.assertFalse(lim.refund(None, 70.1))

    def test_ledger_restore_and_stats(self):
        lim = limiter()
        lim.try_acquire(Priority.TAKE, 3, 5.0)
        lim.try_acquire(Priority.KILL, 2, 6.0)
        led = lim.ledger()
        self.assertEqual(led, [5.0, 5.0, 5.0, 6.0, 6.0])
        other = limiter()
        other.restore(led + [float("nan")])
        self.assertEqual(other.used(10.0), 5)
        st = lim.stats(10.0)
        self.assertEqual((st["used"], st["granted_total"], st["kill_in_window"]), (5, 5, 2))
        self.assertEqual(st["granted"]["KILL"], 2)

    def test_built_from_config(self):
        cfg = load_config()
        floors = {Priority[k]: v for k, v in cfg.get("rate.floors").items()}
        lim = ActionLimiter(cfg.get("rate.bot_cap_per_min"), cfg.get("rate.window_s"), floors,
                            cfg.get("rate.kill_overdraft"), cfg.get("exchange.team_actions_per_min") - 10)
        self.assertEqual((lim.cap, lim.max_cap, lim.floor(Priority.TAKE, 0.0)), (150, 170, 10))
        from ctcbot.app.core import default_factories
        built = default_factories()["limiter"](cfg=cfg)
        self.assertEqual((built.cap, built.max_cap, built.kill_ceiling), (150, 150, 170))
        lim2 = ActionLimiter(150, 61.0, {"TAKE": 10, "HEDGE": 4}, 20, 170)
        self.assertEqual(lim2.floor(Priority.HEDGE, 0.0), 4)


class TestRequestLimiter(unittest.TestCase):
    def test_window(self):
        now = [100.0]
        rl = RequestLimiter(3, clock=lambda: now[0])
        self.assertEqual([rl.wait_needed() for _ in range(3)], [0.0, 0.0, 0.0])
        w = rl.wait_needed()
        self.assertAlmostEqual(w, 1.0)
        now[0] = 100.6
        self.assertAlmostEqual(rl.wait_needed(), 0.4)
        now[0] = 101.0
        self.assertEqual([rl.wait_needed() for _ in range(3)], [0.0, 0.0, 0.0])
        slept = []

        def fake_sleep(s):
            slept.append(s)
            now[0] += s
        self.assertGreater(rl.acquire(sleep=fake_sleep), 0.0)
        self.assertTrue(slept)

    def test_thread_safe(self):
        rl = RequestLimiter(50)
        go = []
        lock = threading.Lock()

        def worker():
            for _ in range(20):
                if rl.wait_needed() == 0.0:
                    with lock:
                        go.append(1)
        ts = [threading.Thread(target=worker) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(len(go), 50)


if __name__ == "__main__":
    unittest.main()
