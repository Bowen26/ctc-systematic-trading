"""Order manager: intents -> risk check -> rate-limit ticket -> REST, plus reconciliation and
the quote book (requote policy).

*** UNTESTED SKELETON. ***

Exchange facts that drive this design (packet section 8):
  * No modify: a requote is cancel + new (2 actions per side) or cancel-all(symbol) + new
    (1 + n actions). cancel-all across ALL symbols is ONE action -> the cheapest emergency pull.
  * Position limit includes resting same-side orders. If an immediately-filling order needs
    room, the exchange cancels our same-side resting orders, worst price first (free for us).
  * Self-trade prevention: a new order that crosses our own resting order cancels the resting
    one (also free). Both kinds of exchange cancels are invisible until reconcile -> OrderRec
    .maybe_gone is set on every resting same-side/opposite-side order that a taking order could
    have displaced, and those orders stop counting as live quotes until reconcile confirms.
  * No private fill channel is documented: GET /orders/open + GET /positions every 0.5 s is the
    source of truth (2 GETs/0.5 s = 4 req/s, far below 600 req/s).
"""
import asyncio
import itertools
import time

import config
import rest
from ratelimit import Priority
from state import OrderRec
from strategy import Intent

_tag = itertools.count(1)


class QuoteSlot:
    __slots__ = ("tag", "price", "fair_ref", "placed_mono")

    def __init__(self, tag, price, fair_ref):
        self.tag, self.price, self.fair_ref, self.placed_mono = tag, price, fair_ref, time.monotonic()


class OrderManager:
    def __init__(self, state, risk, limiter, arest, log=print):
        self.state, self.risk, self.limiter, self.arest, self.log = state, risk, limiter, arest, log
        self.quotes = {}          # (symbol, side) -> QuoteSlot
        self.q_cfg = config.STRAT["quoter"]
        self.unknown_since = None

    # ---- entry point --------------------------------------------------------------------
    async def execute(self, intents):
        """Snipes go out concurrently first, then everything else in order."""
        snipes = [i for i in intents if i.priority <= Priority.SNIPE]
        rest_ = [i for i in intents if i.priority > Priority.SNIPE]
        cancel_first = [i for i in snipes if i.kind.startswith("cancel")]
        for c in cancel_first:                       # cancel-all must leave before the takes
            await self._dispatch(c)
        await asyncio.gather(*(self._dispatch(i) for i in snipes if not i.kind.startswith("cancel")))
        for i in rest_:
            await self._dispatch(i)

    async def _dispatch(self, it):
        k = it.kind
        if k == "order":
            return await self.place(it)
        if k == "cancel_all":
            return await self.cancel_all(it.symbol, it.priority)
        if k == "quote":
            return await self.update_quote(it)
        if k == "quote_off":
            return await self.pull_quotes(it.symbol)
        if k == "quotes_off_all":
            return await self.pull_quotes(None)
        if k in ("create", "redeem"):
            return await self.etf(it)
        self.log(f"unknown intent {it}")

    # ---- primitives ------------------------------------------------------------------------
    async def place(self, it):
        qty, why = self.risk.check(it)
        if qty <= 0:
            return None
        tk = self.limiter.try_acquire(it.priority, 1, it.strategy)
        if not tk:
            return None
        st = self.state
        tag = f"{it.strategy}-{next(_tag)}"
        rec = OrderRec(tag, it.symbol, it.side, it.price, qty, it.strategy, int(it.priority), it.fair_ref)
        st.orders[tag] = rec
        if it.use_exchange_room or it.price is None:
            self._mark_displaced(it.symbol, it.side, it.price)
        res = await self.arest.call("place_order", tk[0], it.symbol, it.side, qty, it.order_type, it.price)
        oid, status, filled = rest.parse_order_ack(res.data) if res.data is not None else (None, None, None)
        if res.ok:
            rec.id, rec.status = oid, "open"
            if filled:
                rec.remaining = max(0, qty - int(filled))
        elif res.status is None:
            rec.status = "unknown"                    # timeout: reconcile decides; never resend
            self.unknown_since = self.unknown_since or time.monotonic()
        else:
            rec.status = "rejected"
            if res.status == 429:
                self.limiter.on_throttled()
            self.log(f"REJECT {it} -> {res}")
        return rec

    def _mark_displaced(self, symbol, side, price):
        for o in self.state.orders.values():
            if o.symbol != symbol or o.status not in ("open", "pending"):
                continue
            same = o.side == side
            crosses = price is None or (o.side != side and ((side == "buy" and o.price is not None and o.price <= price)
                                                            or (side == "sell" and o.price is not None and o.price >= price)))
            if same or crosses:
                o.maybe_gone = True

    async def cancel(self, tag, priority=Priority.HEDGE):
        rec = self.state.orders.get(tag)
        if rec is None or rec.id is None:
            return False
        tk = self.limiter.try_acquire(priority, 1, "cancel")
        if not tk:
            return False
        res = await self.arest.call("cancel_order", tk[0], rec.id)
        if res.ok:
            rec.status = "cancel_sent"
        return res.ok

    async def cancel_all(self, symbol=None, priority=Priority.HEDGE):
        tk = self.limiter.try_acquire(priority, 1, "cancel_all")
        if not tk:
            return False
        res = await self.arest.call("cancel_all", tk[0], symbol)
        for o in self.state.orders.values():
            if symbol is None or o.symbol == symbol:
                o.maybe_gone = True
        for key in list(self.quotes):
            if symbol is None or key[0] == symbol:
                del self.quotes[key]
        return res.ok

    async def etf(self, it):
        if not self.risk.market_open:
            return None
        tk = self.limiter.try_acquire(it.priority, 1, it.kind)
        if not tk:
            return None
        fn = "etf_create" if it.kind == "create" else "etf_redeem"
        return await self.arest.call(fn, tk[0], it.symbol, it.qty)

    # ---- quote book ---------------------------------------------------------------------------
    def _needs_requote(self, slot, new_px, fair, half_width):
        if slot is None:
            return True
        rec = self.state.orders.get(slot.tag)
        if rec is None or rec.status not in ("open", "pending") or rec.maybe_gone or rec.remaining <= 0:
            return True
        if abs(fair - slot.fair_ref) > self.q_cfg["requote_frac_of_half_width"] * half_width:
            return True
        return time.monotonic() - slot.placed_mono > self.q_cfg["max_age_s"] and self.limiter.available() > 120

    async def update_quote(self, it):
        s, fair = it.symbol, it.fair_ref
        hw = 0.5 * (it.ask - it.bid)
        need = {side: self._needs_requote(self.quotes.get((s, side)), px, fair, hw)
                for side, px in (("buy", it.bid), ("sell", it.ask))}
        if not any(need.values()):
            return
        # fair moved THROUGH a live quote -> pulling it is risk-reducing (HEDGE priority)
        through = {side for side in ("buy", "sell") if (s, side) in self.quotes and (
            (side == "buy" and self.quotes[(s, side)].price >= fair) or
            (side == "sell" and self.quotes[(s, side)].price <= fair))}
        pull = {side for side in ("buy", "sell") if (s, side) in self.quotes and (need[side] or side in through)}
        prio = Priority.HEDGE if through else Priority.QUOTE
        if not through and self.limiter.available() - self.limiter.floor(Priority.QUOTE) < \
                (1 if len(pull) == 2 else len(pull)) + sum(need.values()):
            return                                    # all-or-nothing: never leave half a quote
        if len(pull) == 2:                            # 1 action instead of 2; NB also pulls other
            await self.cancel_all(s, prio)            # resting orders in s (e.g. taker residuals)
        else:
            for side in pull:
                await self.cancel(self.quotes[(s, side)].tag, prio)
                self.quotes.pop((s, side), None)
        for side, px in (("buy", it.bid), ("sell", it.ask)):
            if need[side]:
                rec = await self.place(Intent("order", s, side, it.qty, px, priority=Priority.QUOTE,
                                              strategy="quoter", fair_ref=fair))
                if rec is not None and rec.status in ("open", "pending"):
                    self.quotes[(s, side)] = QuoteSlot(rec.tag, px, fair)

    async def pull_quotes(self, symbol=None):
        keys = [k for k in self.quotes if symbol is None or k[0] == symbol]
        if not keys:
            return
        await self.cancel_all(symbol, Priority.HEDGE)

    # ---- reconciliation ---------------------------------------------------------------------
    async def reconcile(self):
        r_pos, r_open = await asyncio.gather(self.arest.call("get_positions"), self.arest.call("get_open_orders"))
        st = self.state
        if r_pos.ok:
            mtm = 0.0
            for s, row in rest.parse_positions(r_pos.data).items():
                if s in st.pos:
                    st.pos[s] = row["qty"]
                    st.marks[s] = row["mark"]
                    mtm += float(row.get("upnl") or 0.0) + float(row.get("rpnl") or 0.0)
            self.risk.on_pnl(mtm)
        if r_open.ok:
            live = {o["id"]: o for o in rest.parse_open_orders(r_open.data)}
            known = set()
            for tag, o in list(st.orders.items()):
                if o.id in live:
                    o.remaining, o.status, o.maybe_gone = live[o.id]["remaining"], "open", False
                    known.add(o.id)
                elif o.status in ("open", "cancel_sent") or (o.status == "unknown" and time.monotonic() - o.sent_mono > 2):
                    o.status = "done"
                if o.status in ("done", "rejected"):
                    del st.orders[tag]
            for oid, o in live.items():               # orders we did not send (browser, other bot)
                if oid not in known:
                    rec = OrderRec(f"foreign-{oid}", o["symbol"], o["side"], o["price"], o["remaining"], "foreign", 9)
                    rec.id, rec.status = oid, "open"
                    st.orders[rec.tag] = rec
            self.unknown_since = None
