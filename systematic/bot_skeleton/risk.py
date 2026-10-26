"""Pre-trade risk checks, trading modes and the kill switch.

*** UNTESTED SKELETON. ***

Modes
  NORMAL       everything allowed
  NO_NEW_RISK  only orders that reduce |position| (and cancels); entered on stale feed, unknown
               order state, drawdown breach, operator 'p'
  FROZEN       nothing but cancels/KILL; entered by the kill switch (file KILL, hotkey 'k', SIGINT)
Checks per order intent (cheap, synchronous, run before a rate-limit ticket is spent):
  symbol enabled; qty <= MAX_ORDER_FRACTION*limit; position room incl. resting same-side orders
  (worst case, pending orders counted); limit price within PRICE_BAND (SNIPE_PRICE_BAND for
  snipes) of the house mid; stale-feed gate in the storm phase; round/market open.
Note: settlement is at fundamental value, so the kill switch CANCELS and FREEZES; it does not
flatten. Flattening crosses 12 spreads and is only right if the model itself is broken.
"""
import os
import time

import config
from ratelimit import Priority

NORMAL, NO_NEW_RISK, FROZEN = "NORMAL", "NO_NEW_RISK", "FROZEN"


class RiskManager:
    def __init__(self, state, log=print):
        self.state = state
        self.mode = NORMAL
        self.reason = ""
        self.disabled = set()
        self.market_open = False
        self.log = log

    def set_mode(self, mode, reason):
        if mode != self.mode:
            self.log(f"RISK: {self.mode} -> {mode} ({reason})")
        self.mode, self.reason = mode, reason

    def reduces(self, symbol, side, qty):
        p = self.state.pos[symbol]
        return (side == "sell" and p > 0 and qty <= p) or (side == "buy" and p < 0 and qty <= -p)

    def check(self, intent):
        """Return (approved_qty, reason). approved_qty 0 means reject."""
        s, side, qty = intent.symbol, intent.side, int(intent.qty)
        if self.mode == FROZEN:
            return 0, "frozen"
        if not self.market_open:
            return 0, "market closed"
        if s in self.disabled or (self.state.overrides.get(s) or {}).get("disabled"):
            return 0, "symbol disabled"
        if qty <= 0:
            return 0, "qty<=0"
        reducing = self.reduces(s, side, qty)
        if self.mode == NO_NEW_RISK and not reducing:
            return 0, "no new risk"
        lim = config.LIMITS[s]
        qty = min(qty, int(config.MAX_ORDER_FRACTION * lim))
        # marketable snipes may rely on the exchange cancelling our worst same-side resting orders
        room = self.state.room(s, side, count_resting=not intent.use_exchange_room)
        qty = min(qty, room)
        if qty <= 0:
            return 0, "no room under limit"
        if intent.price is not None:
            mid = self.state.books[s].mid()
            band = config.SNIPE_PRICE_BAND if intent.priority <= Priority.SNIPE else config.PRICE_BAND
            if mid is None:
                return 0, "no house mid"
            if abs(intent.price - mid) > band * mid:
                return 0, f"price {intent.price:.4f} outside band of mid {mid:.4f}"
        if not reducing and self.state.phase == "storm":
            if time.monotonic() - self.state.storm.obs_recv_mono > config.STALE_FEED_S:
                return 0, "stale storm feed"
        return qty, "ok"

    def on_pnl(self, mtm):
        st = self.state
        st.pnl_mtm = mtm
        st.pnl_peak = max(st.pnl_peak, mtm)
        if st.pnl_peak - mtm > config.MAX_DRAWDOWN_USD and self.mode == NORMAL:
            self.set_mode(NO_NEW_RISK, f"drawdown {st.pnl_peak - mtm:,.0f}")


class KillSwitch:
    """Poll for a KILL file (works even if the console is wedged). The caller cancels-all."""

    def __init__(self, path=config.KILL_FILE):
        self.path = path

    def triggered(self):
        return os.path.exists(self.path)
