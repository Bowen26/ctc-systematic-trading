"""Safety review (adversarial): the order-action budget.

Each test asserts the SAFE property. A failing test is the evidence for a review finding; it is not part of
the default suite (``discover`` only picks up ``test*.py``). Run from BOT with:

    SYS/.venv/bin/python -m unittest discover -s tests -p "review_safety_rate.py" -v

* ``test_lowered_cap_still_allows_kill``      - console ``b N`` below the used count denies every KILL ticket, so
                                                 ``cx`` and the kill follow-ups stop for up to 61 s.
* ``test_bot_alone_stays_under_team_cap``     - max_cap (team - 10 = 170) plus the KILL overdraft (20) is 190 > 180.
* ``test_unsent_cancel_does_not_spin``        - a cancel answered ``sent=False`` (connect/TLS failure) is re-emitted
                                                 by the very next ``om.step`` at the same instant; the loop only ends
                                                 when the budget is gone.
* ``test_repeated_k_is_budgeted``             - every ``k`` sends one more CANCEL_ALL, without a ticket once the
                                                 overdraft is spent.
* ``test_exchange_never_sees_more_than_cap``  - tickets are granted at decision time; requests that wait in the
                                                 executor queue reach the exchange later and pile onto the next
                                                 window (scaled to a 1.22-s window).
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

from test_b_support import BTestCase, Rig, ack, clock, feed, result  # noqa: E402

from ctcbot.ratelimit import ActionLimiter  # noqa: E402
from ctcbot.types import (ApiKind, ApiRequest, ApiResult, CancelIntent, ListLog, OrderAck, Priority,  # noqa: E402
                          RestIntent, Side)

FLOORS = {Priority.KILL: 0, Priority.SNIPE: 1, Priority.HEDGE: 4, Priority.TAKE: 10}


def default_limiter() -> ActionLimiter:
    """The limiter exactly as ``core.default_factories()['limiter']`` builds it from default.json.

    (Hardening: the helper now asks the factory itself; the reviewed factory built ActionLimiter(150, 61.0, FLOORS,
    20, 170), whose KILL ceiling moved with ``b N`` and reached 190.)"""
    from ctcbot.app.core import default_factories
    from ctcbot.config import load_config
    lim = default_factories()["limiter"](cfg=load_config())
    assert isinstance(lim, ActionLimiter)
    return lim


class TestBudget(BTestCase):
    def _open_rest(self, rig: Rig, now: float) -> None:
        rig.set_book("REIN", 49.9, 50.1, now)
        rig.om.set_intents([RestIntent("rein_ask", "rein_ask", "REIN", Side.SELL, 55.0, Priority.HEDGE, qty=10)],
                           now)
        reqs = rig.om.step(now, clock(100.0), feed())
        places = [r for r in reqs if r.kind == ApiKind.PLACE]
        self.assertEqual(len(places), 1, rig.log.events[-5:])
        rig.om.on_result(result(places[0], 200, parsed=ack("x1", "open", 0, 10)), now)

    def test_lowered_cap_still_allows_kill(self):
        lim = default_limiter()
        rig = Rig(self.log, limiter=lim)
        self._open_rest(rig, 0.0)
        for i in range(119):                                  # a busy minute: 120 actions in the window
            self.assertTrue(lim.try_acquire(Priority.TAKE, 1, 0.1 + i * 0.1))
        lim.cap = 100                                         # operator: "b 100" to leave room for manual trading
        now = 20.0
        cx = rig.om.cancel_all(Priority.KILL, now, reason="operator_cx")   # what the console 'cx' does
        self.assertTrue(cx, f"cx produced no request: KILL ticket denied (used {lim.used(now)}, cap {lim.cap}, "
                            f"overdraft {lim.kill_overdraft})")

    def test_bot_alone_stays_under_team_cap(self):
        lim = default_limiter()
        lim.cap = 10_000                                      # "b 10000": clamped to max_cap = 170
        n = 0
        for p in (Priority.TAKE, Priority.HEDGE, Priority.SNIPE, Priority.KILL):
            while lim.try_acquire(p, 1, 1.0):
                n += 1
        self.assertLessEqual(n, 180, f"the bot alone may send {n} actions in one window (cap {lim.cap} + "
                                     f"overdraft {lim.kill_overdraft}); the team limit is 180")

    def test_unsent_cancel_does_not_spin(self):
        lim = default_limiter()
        rig = Rig(self.log, limiter=lim)
        now = 0.0
        self._open_rest(rig, now)
        rig.om.set_intents([CancelIntent("rein_ask", Priority.HEDGE, scope="key", key="rein_ask")], now)
        pending = [r for r in rig.om.step(now, clock(100.0), feed()) if r.kind == ApiKind.CANCEL]
        self.assertEqual(len(pending), 1)
        emitted = 0
        while pending and emitted < 1000:
            req = pending.pop()
            emitted += 1
            # the network is down: connect fails before any byte is written (RestClient: sent=False)
            res = ApiResult(req=req, status=None, data=None, parsed=None, error="not sent: connect failed: "
                            "ConnectionRefusedError", sent_mono=now, done_mono=now, sent=False)
            rig.om.on_result(res, now)
            # BotCore._on_order_result calls om.step once after every order-action result
            pending += [r for r in rig.om.step(now, clock(100.0), feed()) if r.kind == ApiKind.CANCEL]
        later = lim.try_acquire(Priority.HEDGE, 1, now + 30.0)
        self.assertLessEqual(emitted, 3, f"{emitted} CANCEL attempts at one instant for one order; the budget is "
                                         f"drained ({lim.used(now)} used) and a HEDGE cancel 30 s later is "
                                         f"{'granted' if later else 'denied'}")

    def test_network_blip_does_not_block_feed_down_cancel(self):
        """Wi-Fi drops at t=0 (connect fails at once: sent=False) and returns at t=12. A clip-remainder cancel is
        pending at t=0 and the feed-down CANCEL_ALL (SNIPE) fires at t=10.5. How soon after the network is back
        does that CANCEL_ALL actually go out?"""
        lim = default_limiter()
        rig = Rig(self.log, limiter=lim)
        self._open_rest(rig, 0.0)
        rig.om.set_intents([CancelIntent("rein_ask", Priority.HEDGE, scope="key", key="rein_ask")], 0.0)
        down_until = 12.0
        queue = [r for r in rig.om.step(0.0, clock(100.0), feed()) if r.kind in (ApiKind.CANCEL, ApiKind.CANCEL_ALL)]
        sent_ok: list[tuple[float, ApiKind, Priority]] = []

        def answer(reqs: list, now: float) -> None:
            while reqs:
                req = reqs.pop(0)
                if now < down_until:
                    res = ApiResult(req=req, status=None, data=None, parsed=None, error="not sent: connect failed: "
                                    "OSError: [Errno 51] Network is unreachable", sent_mono=now, done_mono=now,
                                    sent=False)
                else:
                    sent_ok.append((now, req.kind, req.priority))
                    res = result(req, 200, t0=now)
                rig.om.on_result(res, now)
                reqs += [r for r in rig.om.step(now, clock(100.0 + now), feed())
                         if r.kind in (ApiKind.CANCEL, ApiKind.CANCEL_ALL)]

        answer(queue, 0.0)
        t = 0.25
        while t < 80.0:
            reqs = []
            if abs(t - 10.5) < 1e-9:                          # BotCore._feed_down: om.cancel_all(SNIPE)
                reqs += rig.om.cancel_all(Priority.SNIPE, t, reason="feed_down")
            reqs += [r for r in rig.om.step(t, clock(100.0 + t), feed())
                     if r.kind in (ApiKind.CANCEL, ApiKind.CANCEL_ALL)]
            answer(reqs, t)
            if any(k == ApiKind.CANCEL_ALL for _, k, _ in sent_ok):
                break
            t += 0.25
        first = next((x[0] for x in sent_ok if x[1] == ApiKind.CANCEL_ALL), None)
        self.assertIsNotNone(first)
        self.assertLessEqual(first, down_until + 1.0,
                             f"the feed-down CANCEL_ALL reached the exchange only at t={first:.2f}, "
                             f"{first - down_until:.1f} s after the network came back (ledger drained by unsent "
                             f"retries: {lim.granted})")

    def test_429_and_5xx_do_not_storm(self):
        """Positive control (passes): a 429 on a cancel and 5xx on PLACEs produce no retry storm."""
        lim = default_limiter()
        rig = Rig(self.log, limiter=lim)
        self._open_rest(rig, 0.0)
        rig.om.set_intents([CancelIntent("rein_ask", Priority.HEDGE, scope="key", key="rein_ask")], 0.0)
        c = [r for r in rig.om.step(0.0, clock(100.0), feed()) if r.kind == ApiKind.CANCEL]
        res = result(c[0], 429, error="http 429: slow down")
        rig.om.on_result(res, 0.0)
        rig.risk.on_result(res, 0.0)
        lim.on_throttled(0.0, 5.0)                            # what BotCore does on a 429
        sent: list[tuple[float, ApiKind]] = []
        t = 0.0
        while t < 20.0:                                       # ticks every 0.25 s, the runner re-sending the rest
            rig.om.set_intents([RestIntent("rein_ask", "rein_ask", "REIN", Side.SELL, 55.0, Priority.HEDGE,
                                           qty=10)], t)
            for r in rig.om.step(t, clock(100.0 + t), feed()):
                sent.append((t, r.kind))
                if r.kind == ApiKind.PLACE:                   # every PLACE comes back 503
                    out = result(r, 503, error="http 503")
                    rig.om.on_result(out, t)
                    rig.risk.on_result(out, t)
            t += 0.25
        sys.stderr.write(f"\n[429/5xx] actions after the 429: {[(t, str(k)) for t, k in sent]}\n")
        self.assertEqual([x for x in sent if x[0] < 5.0], [])            # frozen 5 s
        self.assertLessEqual(len(sent), 8, sent)                         # then a handful, never a storm

    def test_repeated_k_is_budgeted(self):
        lim = default_limiter()
        rig = Rig(self.log, limiter=lim)
        self._open_rest(rig, 0.0)
        while lim.try_acquire(Priority.KILL, 1, 1.0):         # window already at cap + overdraft
            pass
        used = lim.used(2.0)
        rig.risk.kill("console: k", 2.0)
        sent = 0
        for i in range(10):                                   # the operator presses k ten times
            sent += len([r for r in rig.om.kill(2.0 + 0.3 * i) if r.kind == ApiKind.CANCEL_ALL])
        self.assertLessEqual(used + sent, lim.cap + lim.kill_overdraft + 1,
                             f"{sent} kill CANCEL_ALLs sent on top of {used} counted actions; the ledger still "
                             f"says {lim.used(5.0)}")


class _StallAPI:
    """Twelve slow GETs occupy the workers until ``release``; order actions record their arrival time."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.arrivals: list[float] = []

    def execute(self, req: ApiRequest) -> ApiResult:
        if req.kind == ApiKind.GET_TIME:
            self.release.wait(5.0)
        else:
            with self.lock:
                self.arrivals.append(time.monotonic())
        t = time.monotonic()
        return ApiResult(req=req, status=200, data={}, parsed=OrderAck(f"o{req.req_id}", "open", 0, req.qty, None),
                         error=None, sent_mono=t, done_mono=t, sent=True)

    def prewarm(self) -> None:
        pass


class TestExchangeWindow(unittest.TestCase):
    def test_exchange_never_sees_more_than_cap(self):
        from ctcbot.rest import RestExecutor
        cap, window = 15, 1.22                                # 150 per 61 s, scaled down 50x
        lim = ActionLimiter(cap, window, {p: 0 for p in Priority}, 0, cap)
        api = _StallAPI()
        ex = RestExecutor(api, 12, lambda r: None, ListLog(), kill_workers=1)
        ids = iter(range(1, 10_000))
        try:
            for _ in range(12):                               # the exchange is slow: 12 GETs hold every worker
                ex.submit(ApiRequest(req_id=next(ids), kind=ApiKind.GET_TIME, priority=Priority.HEDGE))
            time.sleep(0.05)
            t0 = time.monotonic()
            for _ in range(cap):                              # a burst of clips: every ticket granted at t0
                self.assertTrue(lim.try_acquire(Priority.TAKE, 1, time.monotonic()))
                ex.submit(ApiRequest(req_id=next(ids), kind=ApiKind.PLACE, priority=Priority.TAKE, symbol="PORT",
                                     side=Side.BUY, qty=1, price=50.0))
            time.sleep(1.0)
            api.release.set()                                 # the GETs return; the queued PLACEs go out now
            second = 0
            while time.monotonic() - t0 < 1.6 and second < cap:
                if lim.try_acquire(Priority.TAKE, 1, time.monotonic()):   # granted once the ledger frees
                    second += 1
                    ex.submit(ApiRequest(req_id=next(ids), kind=ApiKind.PLACE, priority=Priority.TAKE,
                                         symbol="PORT", side=Side.BUY, qty=1, price=50.0))
                else:
                    time.sleep(0.005)
            time.sleep(0.2)
        finally:
            api.release.set()
            ex.close(1.0)
        arr = sorted(api.arrivals)
        worst, j = 0, 0
        for i, t in enumerate(arr):                           # the exchange's window: 60/61 of the bot's
            while arr[j] <= t - window * 60.0 / 61.0:
                j += 1
            worst = max(worst, i - j + 1)
        self.assertLessEqual(worst, cap, f"{worst} order actions reached the exchange inside one scaled 60-s "
                                         f"window although the ledger never held more than {cap}")


if __name__ == "__main__":
    unittest.main()
