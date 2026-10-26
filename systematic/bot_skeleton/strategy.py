"""Strategy modules. They only emit intents; the order manager budgets, risk-checks and sends.

*** UNTESTED SKELETON. Parameters in config.STRAT are starting points for replay sweeps. ***

Modules (priority in the action budget in brackets)
  EventSniper          [SNIPE] damage reports, official advisories, impacts, announcements
  SettlementPositioner [SNIPE] round 3: quotes off before 15:40, then trade to settlement
  EtfArb               [TAKE first leg, HEDGE other legs] ETF book vs component books
  FundamentalTaker     [TAKE]  cross when |fair - house| clears spread + k*sigma + margin
  PassiveQuoter        [QUOTE] optional; only quiet phases; requotes only on fair moves
Shared conventions
  * Trade toward a TARGET position = limit * clamp(z / z_full), z = (fair - mid) / sigma, with a
    deadband (only trade a side whose edge clears the threshold) -> no flip-flopping, no wasted
    actions. Positions settle at fundamental value, so there is no need to "exit".
  * Taking orders are marketable LIMIT orders priced at fair -/+ threshold: they fill at the
    house price and any residual rests at a price we still like (no market orders; ASK what the
    exchange's "price protection" does to market orders).
"""
import time

import config
from clock import parse_mt, real_s_since_open
from ratelimit import Priority


class Intent:
    __slots__ = ("kind", "symbol", "side", "qty", "price", "order_type", "priority", "strategy",
                 "use_exchange_room", "fair_ref", "bid", "ask")

    def __init__(self, kind, symbol=None, side=None, qty=0, price=None, order_type="limit",
                 priority=Priority.TAKE, strategy="", use_exchange_room=False, fair_ref=None, bid=None, ask=None):
        self.kind, self.symbol, self.side, self.qty, self.price = kind, symbol, side, qty, price
        self.order_type, self.priority, self.strategy = order_type, priority, strategy
        self.use_exchange_room, self.fair_ref, self.bid, self.ask = use_exchange_room, fair_ref, bid, ask

    def __repr__(self):
        return f"{self.kind}:{self.strategy}:{self.symbol}:{self.side}:{self.qty}@{self.price}:P{int(self.priority)}"


class Ctx:
    """What a strategy sees for one decision."""

    def __init__(self, state, fairs, now_real, settlement, limiter_available, mono=None):
        self.state, self.fairs, self.now_real = state, fairs, now_real
        self.settlement, self.avail = settlement, limiter_available
        self.mono = time.monotonic() if mono is None else mono     # replay passes simulated time


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def take_toward(ctx, s, mu, sigma, k_sigma, min_edge_bp, z_full, max_slip_bp, priority, strategy,
                min_clip, use_exchange_room=False):
    """Marketable limit toward the target position, or None."""
    b = ctx.state.books[s]
    if b.bid is None or b.ask is None or sigma == float("inf"):
        return None
    mid = 0.5 * (b.bid + b.ask)
    thr = k_sigma * sigma + min_edge_bp * 1e-4 * mid
    lim = config.LIMITS[s]
    z = (mu - mid) / max(sigma, 1e-9)
    target = round(lim * clamp(z / z_full, -1.0, 1.0))
    pos = ctx.state.pos[s]
    if mu - b.ask > thr and target > pos:
        qty = target - pos
        px = min(mu - thr, b.ask * (1 + max_slip_bp * 1e-4))
        side = "buy"
    elif b.bid - mu > thr and target < pos:
        qty = pos - target
        px = max(mu + thr, b.bid * (1 - max_slip_bp * 1e-4))
        side = "sell"
    else:
        return None
    if qty < min_clip:
        return None
    return Intent("order", s, side, qty, round(px, config.PRICE_DECIMALS), priority=priority, strategy=strategy,
                  use_exchange_room=use_exchange_room, fair_ref=mu)


class Strategy:
    name = "base"

    def __init__(self, params):
        self.p = params
        self.enabled = params.get("enabled", True)

    def on_batch(self, events, ctx):
        return []

    def on_tick(self, ctx):
        return []


class FundamentalTaker(Strategy):
    name = "taker"

    def __init__(self, params):
        super().__init__(params)
        self.last = {}

    def on_tick(self, ctx):
        out, now, p = [], ctx.mono, self.p
        for s, f in ctx.fairs.items():
            if now - self.last.get(s, 0.0) < p["cooldown_s"]:
                continue
            it = take_toward(ctx, s, f.mu, f.sigma, p["k_sigma"], p["min_edge_bp"], p["z_full"], p["max_slip_bp"],
                             Priority.TAKE, self.name, max(1, int(p["min_clip_frac"] * config.LIMITS[s])))
            if it:
                self.last[s] = now
                out.append(it)
        return out


class PassiveQuoter(Strategy):
    """Desired quotes only; the order manager's QuoteBook decides whether a requote is worth the
    actions (fair moved > requote_frac_of_half_width * half width, fill, or age)."""
    name = "quoter"

    def on_tick(self, ctx):
        p, st = self.p, ctx.state
        if st.phase not in p["phases"]:
            return [Intent("quote_off", s, priority=Priority.HEDGE, strategy=self.name) for s in p["symbols"]]
        out = []
        for s in p["symbols"]:
            f, b = ctx.fairs.get(s), st.books[s]
            if f is None or f.sigma == float("inf") or b.bid is None or b.ask is None:
                continue
            hw = max(p["half_width_bp"] * 1e-4 * f.mu, f.sigma)
            size = max(1, int(p["size_frac"] * config.LIMITS[s]))
            bid = min(f.mu - hw, b.ask - 0.01)       # never cross the house (self-trade / taking)
            ask = max(f.mu + hw, b.bid + 0.01)
            out.append(Intent("quote", s, qty=size, priority=Priority.QUOTE, strategy=self.name,
                              fair_ref=f.mu, bid=round(bid, 2), ask=round(ask, 2)))
        return out


class EventSniper(Strategy):
    """React within the 2-4 s before the house reprices (research: the house absorbs ~98-100% of
    the preliminary and final report moves between +2 s and +4 s; ~40% of the first-advisory
    move by +4 s and ~90% by +44 s). main.py recomputes fairs on the batch BEFORE calling us."""
    name = "sniper"
    TRIGGERS = ("damage_report", "advisory", "impact")

    def on_batch(self, events, ctx):
        p, out = self.p, []
        fresh = []
        for e in events:
            if e.kind == "announcement":
                out += self._announcement(e, ctx)
            elif e.kind in self.TRIGGERS and isinstance(e.msg, dict) and e.msg.get("time"):
                if e.kind == "advisory" and e.msg.get("source") != "official":
                    continue
                age = ctx.now_real - real_s_since_open(parse_mt(e.msg["time"])) if ctx.now_real is not None else 0.0
                if age <= p["stale_after_s"]:          # catch-up bursts arrive late: let the taker handle
                    fresh.append(e)
        if not fresh:
            return out
        if ctx.state.round == 3 and any(e.kind == "damage_report" and e.msg.get("stage") == "final" for e in fresh):
            return out                                  # SettlementPositioner owns the final 40 s
        intents = []
        for s, f in ctx.fairs.items():
            it = take_toward(ctx, s, f.mu, f.sigma, 0.5, p["min_edge_bp"], 1.0, 400, Priority.SNIPE, self.name,
                             1, use_exchange_room=True)
            if it:
                intents.append(it)
        if intents and p.get("cancel_all_first") and ctx.state.orders:
            out.append(Intent("cancel_all", priority=Priority.SNIPE, strategy=self.name))
        return out + intents

    def _announcement(self, e, ctx):
        """Text we cannot parse reliably: pull our orders in the named symbols, flag them for the
        operator (console 'a'), widen their sigma until a human sets an override."""
        out = []
        for s in e.msg.get("symbols") or []:
            if s in config.LIMITS:
                ov = ctx.state.overrides.setdefault(s, {})
                ov["sigma_mult"] = 4.0
                ov["needs_operator"] = e.msg.get("headline")
                out.append(Intent("cancel_all", s, priority=Priority.SNIPE, strategy=self.name))
        return out


class SettlementPositioner(Strategy):
    """Round 3. Before 15:40: quotes off (frees budget). After the exact final report: trade every
    symbol toward +/-limit wherever the house is through settlement by more than eps; residual
    orders rest at S -/+ eps until the close (they can only fill at prices that beat settlement)."""
    name = "settle"

    def __init__(self, params):
        super().__init__(params)
        self.sent = {}

    def on_tick(self, ctx):
        st, p = ctx.state, self.p
        if st.round != 3 or ctx.now_real is None:
            return []
        out = []
        t_final = config.SCHEDULE_REAL_S["final"]
        if t_final - p["quote_off_before_final_s"] <= ctx.now_real < t_final and not self.sent.get("quotes_off"):
            self.sent["quotes_off"] = True
            out.append(Intent("quotes_off_all", priority=Priority.HEDGE, strategy=self.name))
        S = ctx.settlement
        if not S:
            return out
        now = ctx.mono
        for s, v in S.items():
            b = st.books.get(s)
            if b is None or b.bid is None or b.ask is None:
                continue
            if now - self.sent.get(s, 0.0) < 1.0:
                continue
            eps = p["eps_bp"] * 1e-4 * v
            lim, pos = config.LIMITS[s], st.pos[s]
            if b.ask < v - eps and pos < lim:
                out.append(Intent("order", s, "buy", lim - pos, round(v - eps, 6), priority=Priority.SNIPE,
                                  strategy=self.name, use_exchange_room=True, fair_ref=v))
                self.sent[s] = now
            elif b.bid > v + eps and pos > -lim:
                out.append(Intent("order", s, "sell", lim + pos, round(v + eps, 6), priority=Priority.SNIPE,
                                  strategy=self.name, use_exchange_room=True, fair_ref=v))
                self.sent[s] = now
        return out

    def on_batch(self, events, ctx):
        return self.on_tick(ctx) if ctx.settlement else []


class EtfArb(Strategy):
    """ETF book vs component books. create/redeem are booked at MARKS on both legs, so the
    conversion itself is PnL-neutral: the only arbitrage is between EXECUTABLE prices, and the
    pair can simply be held (ETF settles at the basket value). create/redeem is used only to
    recycle position-limit room after a completed arb."""
    name = "etf_arb"

    def on_tick(self, ctx):
        st, p, out = ctx.state, self.p, []
        for e, basket in config.BASKETS.items():
            eb = st.books[e]
            comps = {c: st.books[c] for c in basket}
            if eb.bid is None or eb.ask is None or any(b.bid is None or b.ask is None for b in comps.values()):
                continue
            b_bid = sum(w * comps[c].bid for c, w in basket.items())
            b_ask = sum(w * comps[c].ask for c, w in basket.items())
            thr = p["min_edge_bp"] * 1e-4 * b_bid
            if eb.bid - b_ask > thr:          # ETF rich: sell ETF, buy basket
                q = min([st.room(e, "sell")] + [st.room(c, "buy") // w for c, w in basket.items()] + [5])
                if q > 0:
                    out.append(Intent("order", e, "sell", q, eb.bid, priority=Priority.TAKE, strategy=self.name))
                    out += [Intent("order", c, "buy", w * q, comps[c].ask, priority=Priority.HEDGE, strategy=self.name)
                            for c, w in basket.items()]
            elif b_bid - eb.ask > thr:        # ETF cheap: buy ETF, sell basket
                q = min([st.room(e, "buy")] + [st.room(c, "sell") // w for c, w in basket.items()] + [5])
                if q > 0:
                    out.append(Intent("order", e, "buy", q, eb.ask, priority=Priority.TAKE, strategy=self.name))
                    out += [Intent("order", c, "sell", w * q, comps[c].bid, priority=Priority.HEDGE, strategy=self.name)
                            for c, w in basket.items()]
            if p.get("recycle_with_create_redeem"):
                pe = st.pos[e]
                hedged = min(abs(st.pos[c]) // w for c, w in basket.items())
                if pe >= config.LIMITS[e] and all(st.pos[c] <= -w * pe for c, w in basket.items()):
                    out.append(Intent("redeem", e, qty=min(pe, hedged), priority=Priority.HEDGE, strategy=self.name))
                elif pe <= -config.LIMITS[e] and all(st.pos[c] >= -w * pe for c, w in basket.items()):
                    out.append(Intent("create", e, qty=min(-pe, hedged), priority=Priority.HEDGE, strategy=self.name))
        return out


def build_strategies(strat_cfg):
    mods = [EventSniper(strat_cfg["sniper"]), SettlementPositioner(strat_cfg["settle"]),
            EtfArb(strat_cfg["etf_arb"]), FundamentalTaker(strat_cfg["taker"]), PassiveQuoter(strat_cfg["quoter"])]
    return [m for m in mods if m.enabled]
