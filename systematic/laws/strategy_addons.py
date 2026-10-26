"""Add-on strategy modules for the bot skeleton in work/se/bot/skeleton (same Intent / Ctx / state API).

*** UNTESTED. Never run against an exchange. Copy next to strategy.py and register in build_strategies(). ***

EtfLeadLag   [SNIPE-level priority]  research: the house ETF consensus equals the basket value of the house
             stock consensus 6 s earlier (corr 0.98 ARCH, 0.87 REBLD at lag 3 rows, ~0 elsewhere). Trade the ETF
             ALONE toward the live basket value, exit when the gap closes or after hold_s. Replaces EtfArb's
             two-leg hedge (8 actions, 7 extra spreads) for this purpose.
ReportSniper [SNIPE]  research: stocks reprice to damage reports at +4 s, ETFs at +10 s; the jump is
             predictable from (final or prelim) minus our prior posterior (test R2 0.72-0.98, REIN 0.3).
             Enter within ~1 s, exit after the house has repriced (rounds 1-2 and the round-3 prelim).
             The round-3 FINAL is owned by SettlementPositioner (hold to settlement, see se_laws.final_orders).
"""
import config
from ratelimit import Priority
from strategy import Intent, Strategy


def _mid(b):
    return None if b.bid is None or b.ask is None else 0.5 * (b.bid + b.ask)


class EtfLeadLag(Strategy):
    name = "etf_lag"
    # params: enter_gap (0.005), exit_gap (0.001), hold_s (8.0), max_slip_frac (0.5), cooldown_s (2.0)

    def __init__(self, params):
        super().__init__(params)
        self.open = {}      # etf -> (entry mono time, side)
        self.last = {}

    def on_tick(self, ctx):
        st, p, out, now = ctx.state, self.p, [], ctx.mono
        for e, basket in config.BASKETS.items():
            if st.overrides.get(e, {}).get("disabled") or any(st.overrides.get(c, {}).get("needs_operator") for c in basket):
                continue
            eb = st.books[e]
            mids = {c: _mid(st.books[c]) for c in basket}
            em = _mid(eb)
            if em is None or any(m is None for m in mids.values()):
                continue
            nav = sum(w * mids[c] for c, w in basket.items())
            gap = (nav - em) / em
            pos, lim = st.pos[e], config.LIMITS[e]
            if e in self.open:                                   # exit logic
                t0, side = self.open[e]
                if abs(gap) < p["exit_gap"] or now - t0 > p["hold_s"] or (side > 0) != (gap > 0):
                    if pos > 0:
                        out.append(Intent("order", e, "sell", pos, round(eb.bid, 6), priority=Priority.SNIPE, strategy=self.name))
                    elif pos < 0:
                        out.append(Intent("order", e, "buy", -pos, round(eb.ask, 6), priority=Priority.SNIPE, strategy=self.name))
                    del self.open[e]
                    self.last[e] = now
                continue
            if now - self.last.get(e, 0.0) < p["cooldown_s"] or abs(gap) < p["enter_gap"]:
                continue
            if gap > 0 and pos < lim:                            # ETF stale-cheap: buy it
                px = min(eb.ask + p["max_slip_frac"] * (nav - eb.ask), nav)
                out.append(Intent("order", e, "buy", lim - pos, round(px, 6), priority=Priority.SNIPE,
                                  strategy=self.name, use_exchange_room=True, fair_ref=nav))
                self.open[e] = (now, +1)
            elif gap < 0 and pos > -lim:                         # ETF stale-rich: sell it
                px = max(eb.bid - p["max_slip_frac"] * (eb.bid - nav), nav)
                out.append(Intent("order", e, "sell", lim + pos, round(px, 6), priority=Priority.SNIPE,
                                  strategy=self.name, use_exchange_room=True, fair_ref=nav))
                self.open[e] = (now, -1)
        return out


class ReportSniper(Strategy):
    """jump_model(state, msg) -> {symbol: predicted house move in $/share}. Build it from the research fit:
    move = slope[stage][round][sym] * (FV(state | new report) - FV(state | previous information)).
    params: min_move_frac (0.003), max_slip_frac (0.5), exit_after_s (12.0), stale_after_s (1.5)."""
    name = "report_sniper"

    def __init__(self, params, jump_model):
        super().__init__(params)
        self.jump_model = jump_model
        self.exits = []     # (due mono time, symbol)

    def on_batch(self, events, ctx):
        st, p, out = ctx.state, self.p, []
        for e in events:
            if e.kind != "damage_report" or not isinstance(e.msg, dict):
                continue
            if st.round == 3 and e.msg.get("stage") == "final":
                continue                                          # SettlementPositioner owns it
            moves = self.jump_model(st, e.msg)
            for s, mv in moves.items():
                b = st.books.get(s)
                if b is None or b.bid is None or b.ask is None or st.overrides.get(s, {}).get("needs_operator"):
                    continue
                mid, lim, pos = 0.5 * (b.bid + b.ask), config.LIMITS[s], st.pos[s]
                if abs(mv) < p["min_move_frac"] * mid or abs(mv) < (b.ask - b.bid):
                    continue
                if mv > 0 and pos < lim:
                    out.append(Intent("order", s, "buy", lim - pos, round(b.ask + p["max_slip_frac"] * (mv - (b.ask - mid)), 6),
                                      priority=Priority.SNIPE, strategy=self.name, use_exchange_room=True, fair_ref=mid + mv))
                elif mv < 0 and pos > -lim:
                    out.append(Intent("order", s, "sell", lim + pos, round(b.bid + p["max_slip_frac"] * (mv + (mid - b.bid)), 6),
                                      priority=Priority.SNIPE, strategy=self.name, use_exchange_room=True, fair_ref=mid + mv))
                else:
                    continue
                self.exits.append((ctx.mono + p["exit_after_s"], s))
        return out

    def on_tick(self, ctx):
        st, out, keep = ctx.state, [], []
        for due, s in self.exits:
            if ctx.mono < due:
                keep.append((due, s)); continue
            b, pos = st.books[s], st.pos[s]
            if b.bid is None or b.ask is None or pos == 0:
                continue
            if pos > 0:
                out.append(Intent("order", s, "sell", pos, round(b.bid, 6), priority=Priority.HEDGE, strategy=self.name))
            else:
                out.append(Intent("order", s, "buy", -pos, round(b.ask, 6), priority=Priority.HEDGE, strategy=self.name))
        self.exits = keep
        return out
