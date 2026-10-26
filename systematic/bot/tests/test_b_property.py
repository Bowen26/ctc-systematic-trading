"""Group B acceptance: random intents against a fake exchange that enforces the packet's limit rules.

20,000 steps of 50 ms (1,000 virtual seconds) drive the real OrderManager, Reconciler, RiskManager,
OrderStore, PositionBook and BookStore through a minimal core loop (decisions every 0.25 s, reconcile
every 0.5 s, ``om.step`` after every order-action result). The fake exchange processes requests in
arrival order, so GETs land before or after concurrent PLACEs and DELETEs; responses have random latency
(some arrive after the next GET), and a few are lost (timeouts), fail with 5xx (processed or not) or never
leave (``sent=False``). Resting orders fill partially at random.

Pass criteria (DESIGN.md section 9.1 B "property"): 0 limit violations, 0 self-trades, no 61-s window
over 150 bot actions, 0 foreign fills or records, every order action carries a ticket, and once the
exchange goes quiet and reconciles run, no live record remains and positions agree with the exchange.
In addition the room invariant is checked after every event: per symbol and side, the bot's position plus
resting quantity is never below the exchange's, which is what makes 0 limit violations hold in general
and not only for the seeds tried.
"""
from __future__ import annotations

import heapq
import itertools
import math
import os
import random
import sys
import unittest
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_b_support import CASE_LIMITS, BTestCase, FakeLimiter, Rig, clock, feed  # noqa: E402

from ctcbot.state.limits import resting_qty  # noqa: E402

from ctcbot.types import (ORDER_ACTIONS, ApiKind, ApiRequest, ApiResult, CancelIntent, OpenOrderRow,  # noqa: E402
                          OrderAck, PositionRow, Priority, RestIntent, Side, TargetIntent)

SYMS = ("PORT", "ARCH", "REBLD")
DT = 0.05
SLOW = os.environ.get("CTCBOT_SLOW") == "1"


@dataclass
class _Ord:
    oid: str
    symbol: str
    side: Side
    price: float
    qty: int
    remaining: int


@dataclass
class _Pos:
    qty: int = 0
    avg: float | None = None
    realized: float = 0.0


@dataclass(order=True)
class _Ev:
    t: float
    seq: int
    kind: str = field(compare=False)          # "arrive" | "deliver"
    req: ApiRequest = field(compare=False)
    payload: object = field(compare=False, default=None)


class FakeExchange:
    """Limit rules of packet section 8: position plus same-side resting orders within the case limit."""

    def __init__(self, rng: random.Random, fill_p: float = 0.01) -> None:
        self.rng = rng
        self.fill_p = fill_p
        self.pos = {s: _Pos() for s in SYMS}
        self.orders: dict[str, _Ord] = {}
        self.mid = {"PORT": 100.0, "ARCH": 50.0, "REBLD": 20.0}
        self.n = itertools.count(1)
        self.violations: dict[str, int] = {"limit": 0, "self_trade": 0, "bad_qty": 0}
        self.actions: list[tuple[float, Priority]] = []

    def touch(self, s: str) -> tuple[float, float]:
        m = self.mid[s]
        return round(m * 0.999, 2), round(m * 1.001, 2)

    def _fill(self, s: str, side: Side, q: int, px: float) -> None:
        p = self.pos[s]
        sg = side.sign
        if p.qty == 0 or (p.qty > 0) == (sg > 0):
            tot = abs(p.qty) + q
            p.avg = ((p.avg or 0.0) * abs(p.qty) + px * q) / tot
            p.qty += sg * q
            return
        close = min(q, abs(p.qty))
        p.realized += close * (px - (p.avg or px)) * (1 if p.qty > 0 else -1)
        p.qty += sg * q
        if p.qty == 0:
            p.avg = None
        elif (p.qty > 0) == (sg > 0):
            p.avg = px

    def market_tick(self) -> None:
        for s in SYMS:
            self.mid[s] *= math.exp(self.rng.gauss(0, 0.0003))
        for o in list(self.orders.values()):
            if self.rng.random() < self.fill_p:
                q = self.rng.randint(1, o.remaining)
                self._fill(o.symbol, o.side, q, o.price)
                o.remaining -= q
                if o.remaining == 0:
                    del self.orders[o.oid]

    def process(self, req: ApiRequest, t: float):
        k = req.kind
        if k == ApiKind.PLACE:
            s, side, qty, px = req.symbol, req.side, int(req.qty), float(req.price)
            if qty < 1:
                self.violations["bad_qty"] += 1
                return 400, {"detail": "bad qty"}
            L = CASE_LIMITS[s]
            same = sum(o.remaining for o in self.orders.values() if o.symbol == s and o.side == side)
            usage = (self.pos[s].qty if side == Side.BUY else -self.pos[s].qty) + same + qty
            if usage > L:
                self.violations["limit"] += 1
                return 400, {"detail": "position limit"}
            for o in list(self.orders.values()):        # self-trade prevention cancels our resting order
                if o.symbol == s and o.side != side and (
                        (side == Side.BUY and px >= o.price) or (side == Side.SELL and px <= o.price)):
                    self.violations["self_trade"] += 1
                    del self.orders[o.oid]
            bid, ask = self.touch(s)
            filled = 0
            if (side == Side.BUY and px >= ask) or (side == Side.SELL and px <= bid):
                filled = qty if self.rng.random() < 0.5 else self.rng.randint(0, qty)
                if filled:
                    self._fill(s, side, filled, ask if side == Side.BUY else bid)
            oid = f"E{next(self.n)}"
            if filled < qty:
                self.orders[oid] = _Ord(oid, s, side, px, qty, qty - filled)
            avg = (ask if side == Side.BUY else bid) if filled else None
            return 200, OrderAck(oid, "filled" if filled == qty else ("partially_filled" if filled else "open"),
                                 filled, qty - filled, avg)
        if k == ApiKind.CANCEL:
            if req.order_id in self.orders:
                del self.orders[req.order_id]
                return 200, {"status": "cancelled"}
            return 404, {"detail": "not found"}
        if k == ApiKind.CANCEL_ALL:
            for oid in [o.oid for o in self.orders.values() if req.symbol in (None, o.symbol)]:
                del self.orders[oid]
            return 200, {"status": "cancelled"}
        if k == ApiKind.GET_OPEN:
            return 200, [OpenOrderRow(o.oid, o.symbol, o.side, o.price, o.qty, o.remaining, "open")
                         for o in self.orders.values()]
        if k == ApiKind.GET_POSITIONS:
            return 200, {s: PositionRow(s, p.qty, p.avg, None, p.realized, None) for s, p in self.pos.items()
                         if p.qty != 0 or p.realized}
        return 400, None


class World:
    """Minimal core loop around the group-B stack and the fake exchange."""

    def __init__(self, test: BTestCase, seed: int, fill_p: float = 0.01, fault_p: float = 0.03,
                 slow_p: float = 0.15, lat: float = 0.15) -> None:
        self.rng = random.Random(seed)
        self.test = test
        self.fault_p, self.slow_p, self.lat = fault_p, slow_p, lat
        self.rig = Rig(test.log, limiter=FakeLimiter())
        self.ex = FakeExchange(self.rng, fill_p)
        self.q: list[_Ev] = []
        self.seq = itertools.count()
        self.now = 0.0
        self.req_ids = itertools.count(1_000_000)
        self.emitted: list[ApiRequest] = []
        self.foreign_fills = 0
        self.outstanding_cycle: tuple[int, float] | None = None
        self.targets: dict[str, TargetIntent] = {}
        self.quiet = False
        self.breaches: list[tuple] = []
        self._books()

    # ---- the room invariant: the bot never under-counts position + same-side resting quantity
    def check_room(self, where: str) -> None:
        """Per symbol and side, the bot's view of what counts against the limit must be at least the
        exchange's (buy: position + resting buys; sell: resting sells - position)."""
        for s in SYMS:
            p = self.rig.positions.pos.get(s, 0)
            x = self.ex.pos[s].qty
            eb = sum(o.remaining for o in self.ex.orders.values() if o.symbol == s and o.side == Side.BUY)
            es = sum(o.remaining for o in self.ex.orders.values() if o.symbol == s and o.side == Side.SELL)
            ib = p + resting_qty(self.rig.store, s, Side.BUY)
            isell = resting_qty(self.rig.store, s, Side.SELL) - p
            if ib < x + eb or isell < es - x:
                self.breaches.append((self.now, where, s, ib, x + eb, isell, es - x))

    def _books(self) -> None:
        for s in SYMS:
            b, a = self.ex.touch(s)
            self.rig.set_book(s, b, a, now=self.now)

    # ---- transport
    def send(self, reqs: list[ApiRequest]) -> None:
        for r in reqs:
            if r.is_order_action:
                self.emitted.append(r)
                self.test.assertIsNotNone(r.ticket, f"order action without a ticket: {r}")
            arrive = self.now + self.rng.uniform(0.0, self.lat)
            u = self.rng.random()
            if r.is_order_action and u < self.fault_p / 3:     # connect failure: never reaches the exchange
                self._push(self.now + 0.01, "deliver", r, ("unsent",))
                continue
            self._push(arrive, "arrive", r, None)

    def _push(self, t: float, kind: str, req: ApiRequest, payload: object) -> None:
        heapq.heappush(self.q, _Ev(t, next(self.seq), kind, req, payload))

    def _arrive(self, ev: _Ev) -> None:
        r = ev.req
        u = self.rng.random()
        processed = True
        if u < self.fault_p and not self.quiet:
            processed = self.rng.random() < 0.5
        status, body = self.ex.process(r, ev.t) if processed else (None, None)
        if r.is_order_action and processed:
            self.ex.actions.append((ev.t, r.priority))
        if u < self.fault_p / 2 and not self.quiet:
            self._push(r.created_mono + 2.0 if r.is_order_action else ev.t + 3.0, "deliver", r, ("timeout",))
        elif u < self.fault_p and not self.quiet:
            self._push(ev.t + self.rng.uniform(0.0, 0.1), "deliver", r, ("5xx",))
        else:
            slow = self.rng.random() < self.slow_p
            back = self.rng.uniform(0.5, 1.5) if slow else self.rng.uniform(0.0, self.lat)
            self._push(ev.t + back, "deliver", r, ("ok", status, body))

    def _deliver(self, ev: _Ev) -> None:
        r, p = ev.req, ev.payload
        if p[0] == "unsent":
            res = ApiResult(r, None, None, None, "connect failed", r.created_mono, ev.t, sent=False)
        elif p[0] == "timeout":
            res = ApiResult(r, None, None, None, "timeout", r.created_mono, ev.t)
        elif p[0] == "5xx":
            res = ApiResult(r, 503, {"detail": "busy"}, None, "http 503", r.created_mono, ev.t)
        else:
            _, status, body = p
            parsed = body if status == 200 else None
            data = body if not isinstance(body, OrderAck) else {"order_id": body.order_id}
            res = ApiResult(r, status, data, parsed, None if status == 200 else f"http {status}", r.created_mono, ev.t)
        self.on_result(res)

    # ---- core
    def on_result(self, res: ApiResult) -> None:
        rig, now = self.rig, self.now
        if res.req.kind in ORDER_ACTIONS:
            rig.om.on_result(res, now)
            rig.risk.on_result(res, now)
            self.send(rig.om.step(now, clock(300.0), feed()))
        elif res.req.kind in (ApiKind.GET_OPEN, ApiKind.GET_POSITIONS):
            fills = rig.rec.on_result(res, now)
            if fills is not None:
                self.outstanding_cycle = None
                self.foreign_fills += sum(1 for f in fills if f.strategy == "foreign")

    def reconcile(self) -> None:
        if self.outstanding_cycle is not None and self.now - self.outstanding_cycle[1] < 2.0:
            return
        cid = self.rig.rec.begin_cycle(self.now)
        self.outstanding_cycle = (cid, self.now)
        self.send([ApiRequest(next(self.req_ids), ApiKind.GET_OPEN, priority=Priority.HEDGE, cycle=cid,
                              created_mono=self.now),
                   ApiRequest(next(self.req_ids), ApiKind.GET_POSITIONS, priority=Priority.HEDGE, cycle=cid,
                              created_mono=self.now)])

    def decide(self) -> None:
        rng, rig = self.rng, self.rig
        intents: list = []
        for s in SYMS:
            if rng.random() < 0.05:
                self.targets.pop(s, None)
            if rng.random() < 0.06:
                L = CASE_LIMITS[s]
                b, a = self.ex.touch(s)
                mid = (a + b) / 2
                ro = rng.random() < 0.25
                self.targets[s] = TargetIntent(
                    strategy=rng.choice(("s3", "h1", "flatten")), symbol=s, target=rng.randint(-L, L),
                    clip_qty=rng.randint(1, max(1, L // 2)), clip_interval_s=rng.choice((0.5, 1.0, 2.0)),
                    limit_buy=round(mid * (1 + rng.uniform(-0.01, 0.012)), 2),
                    limit_sell=round(mid * (1 - rng.uniform(-0.01, 0.012)), 2),
                    priority=rng.choice((Priority.TAKE, Priority.HEDGE)), ttl_s=rng.choice((0.5, 1.0, 2.0)),
                    reduce_only=ro, edge_usd_per_share=rng.random(), expected_cost_bp=50.0, ref_mid=mid)
        intents += list(self.targets.values())
        if rng.random() < 0.15:
            s = rng.choice(SYMS)
            side = rng.choice((Side.BUY, Side.SELL))
            b, a = self.ex.touch(s)
            mid = (a + b) / 2
            px = round(mid * (1 + side.sign * rng.uniform(-0.02, 0.012)), 2)
            intents.append(RestIntent(strategy="s1r3", key=f"r:{s}:{side}:{rng.randint(0, 1)}", symbol=s, side=side,
                                      price=px, priority=rng.choice((Priority.SNIPE, Priority.HEDGE)),
                                      qty=rng.choice((None, rng.randint(1, CASE_LIMITS[s])))))
        if rng.random() < 0.03:
            sc = rng.choice(("key_prefix", "strategy", "symbol", "all"))
            intents.append(CancelIntent(strategy="s1r3", priority=rng.choice((Priority.SNIPE, Priority.TAKE)),
                                        scope=sc, symbol=rng.choice(SYMS) if sc in ("symbol",) else None,
                                        key="r:" if sc == "key_prefix" else None))
        rig.om.set_intents(intents, self.now)
        reqs = rig.om.step(self.now, clock(300.0), feed())
        if rng.random() < 0.003:
            reqs += rig.om.cancel_all(Priority.SNIPE, self.now, symbol=rng.choice((None,) + SYMS), reason="test")
        self.send(reqs)

    def run(self, steps: int) -> None:
        for i in range(steps):
            self.now = round(i * DT, 6)
            while self.q and self.q[0].t <= self.now:
                ev = heapq.heappop(self.q)
                if ev.kind == "arrive":
                    self._arrive(ev)
                else:
                    self._deliver(ev)
                self.check_room(f"{ev.kind} {ev.req.kind}")
            if not self.quiet:
                self.ex.market_tick()
                self.check_room("market")
                if i % 2 == 0:
                    self._books()
            if i % 10 == 0:
                self.reconcile()
            if i % 5 == 0 and not self.quiet:
                self.decide()
                self.check_room("decide")

    def max_window(self, width: float) -> int:
        ts = sorted(r.created_mono for r in self.emitted if r.priority != Priority.KILL)
        best = j = 0
        for i, t in enumerate(ts):
            while ts[j] <= t - width:
                j += 1
            best = max(best, i - j + 1)
        return best


class PropertyTest(BTestCase):
    def _run(self, seed: int, steps: int, **kw) -> World:
        w = World(self, seed, **kw)
        w.run(steps)
        # the exchange goes quiet: resting orders are cancelled (as at a close), no new intents, all
        # responses drain, and reconcile cycles keep running
        w.quiet = True
        w.ex.orders.clear()
        w.rig.om.set_intents([], w.now)
        t0 = w.now
        quiet_steps = int(8.0 / DT)
        base = steps
        for i in range(quiet_steps):
            w.now = round((base + i) * DT, 6)
            while w.q and w.q[0].t <= w.now:
                ev = heapq.heappop(w.q)
                (w._arrive if ev.kind == "arrive" else w._deliver)(ev)
            w.ex.orders.clear()     # closed: a PLACE still in flight when it went quiet does not rest either
            if i % 10 == 0:
                w.reconcile()
            if i % 5 == 0:
                # the core's tick keeps running decision cycles (now without intents): a clip that a
                # still-fresh target placed just after the exchange went quiet gets its ttl cancel here
                w.send(w.rig.om.step(w.now, clock(300.0), feed()))
        return w

    def _check(self, w: World, max_foreign: int = 0) -> None:
        self.assertEqual(w.ex.violations, {"limit": 0, "self_trade": 0, "bad_qty": 0})
        self.assertEqual(w.breaches[:3], [])
        self.assertLessEqual(w.max_window(61.0), 150)
        self.assertLessEqual(w.rig.limiter.max_in_window(), 150)
        self.assertLessEqual(w.foreign_fills, max_foreign)
        self.assertEqual([r.local_id for r in w.rig.store.all() if r.style == "foreign"], [])
        self.assertLessEqual(len(self.alerts("position_mismatch")), max_foreign)
        live = w.rig.store.live()
        self.assertEqual([(r.local_id, r.state) for r in live if w.now - r.created_mono > 10.0], [])
        self.assertEqual(live, [])
        for s in SYMS:
            self.assertEqual(w.rig.positions.pos.get(s, 0), w.ex.pos[s].qty, s)
        # the run exercised what it claims to
        n_actions = len(w.emitted)
        self.assertGreater(n_actions, 1500)
        self.assertGreater(w.rig.om.counters["cancel_denied"] + w.rig.om.counters["budget_denied"], 0)
        self.assertGreater(w.rig.rec.counters["adopted"], 0)
        self.assertGreater(w.rig.rec.counters["fills"], 0)
        self.assertGreater(w.rig.om.counters["unknown"], 0)

    def test_random_intents_against_fake_exchange(self) -> None:
        w = self._run(seed=20261023, steps=20_000)
        self._check(w)

    def test_harsher_world(self) -> None:
        """More resting fills, more faults and slower answers than the default world."""
        w = self._run(seed=7, steps=20_000, fill_p=0.03, fault_p=0.06, slow_p=0.3, lat=0.3)
        self._check(w)

    def test_more_seeds(self) -> None:
        if not SLOW:
            self.skipTest("slow test (set CTCBOT_SLOW=1)")
        # (overrides, foreign fills allowed). A very slow network (40% of answers 0.5-1.5 s late) can keep
        # a symbol's row skipped for many seconds; the catch-up then has to split one net change across
        # both sides, and a few shares may be booked as foreign. Room safety must hold in every variant.
        variants = [(dict(), 0), (dict(fill_p=0.03), 0), (dict(fault_p=0.08), 0),
                    (dict(fill_p=0.05, fault_p=0.05, slow_p=0.3), 0), (dict(slow_p=0.4, lat=0.4), 3)]
        for seed in range(1, 9):
            for kw, max_foreign in variants:
                with self.subTest(seed=seed, **kw):
                    self.log.events.clear()
                    self._check(self._run(seed=seed, steps=20_000, **kw), max_foreign)


if __name__ == "__main__":
    unittest.main()
