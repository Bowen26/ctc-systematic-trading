"""State store: storm, damage ledger, season losses, books, positions, orders, announcements.

*** UNTESTED SKELETON (the feed-driven parts are exercised offline by replay.py --chaos). ***

Design rules
  * One asyncio thread owns State; REST results are applied on the loop, never from threads.
  * Every feed update is IDEMPOTENT and keyed, so duplicates, catch-up bursts and snapshot
    rebuilds converge to the same state:
      advisories by (round, source, number or seq), impacts by (round, island),
      damage reports by (round, stage), announcements by seq, track points by age.
  * Carried across rounds (packet section 2): positions, per-company damage, season insured
    losses. They are rebuilt from snapshot["damage"] (all reports of all rounds) after a restart.
  * Reset everything on a new season (testnet: competition id changes, positions go to zero).
"""
import json
import time

ISLAND_BUSINESSES = ["PORT", "ISLE", "AQUA", "GRID", "FERRY"]


class StormState:
    def __init__(self):
        self.round = None
        self.name = None
        self.formed = False
        self.dissipated = False
        self.obs = None                  # latest storm message
        self.obs_recv_mono = 0.0
        self.track = {}                  # age -> (x, y, radius); from track msgs and obs
        self.official = {}               # number -> advisory
        self.wire = {}                   # seq -> advisory
        self.impacts = {}                # island -> {"age", "time"}

    def latest_official(self):
        return self.official[max(self.official)] if self.official else None

    def apply(self, m, mono=None):
        t = m.get("type")
        if t == "storm":
            if self.obs is None or m.get("age", -1) >= self.obs.get("age", -1):
                self.obs, self.obs_recv_mono = m, (time.monotonic() if mono is None else mono)
            self.track[m["age"]] = (m["x"], m["y"], m.get("radius"))
        elif t == "track":
            for p in m.get("points") or []:
                if isinstance(p, (list, tuple)) and len(p) >= 4:
                    self.track.setdefault(p[0], (p[1], p[2], p[3]))
        elif t == "storm_formed":
            self.formed = True
        elif t == "storm_dissipated":
            self.dissipated = True
        elif t == "advisory":
            if m.get("source") == "official":
                self.official[m.get("number", m.get("seq"))] = m
            else:
                self.wire[m.get("seq")] = m
        elif t == "impact":
            self.impacts.setdefault(m["island"], {"age": m.get("age"), "time": m.get("time")})

    def apply_snapshot(self, snap):
        obs = snap.get("obs")
        if obs:
            o = dict(obs, type="storm")
            if self.obs is None or o.get("age", -1) >= self.obs.get("age", -1):
                self.obs, self.obs_recv_mono = o, time.monotonic()
        for p in snap.get("track") or []:
            if isinstance(p, (list, tuple)) and len(p) >= 4:
                self.track.setdefault(p[0], (p[1], p[2], p[3]))
        adv = snap.get("advisory")
        if adv:
            self.official.setdefault(adv.get("number", adv.get("seq")), adv)
        for imp in snap.get("impacts") or []:
            self.impacts.setdefault(imp.get("island"), {"age": imp.get("age"), "time": imp.get("time")})

    def sorted_track(self):
        return [(a,) + self.track[a] for a in sorted(self.track)]


class DamageLedger:
    """All damage reports of the season. ACCUMULATION RULE IS AN ASSUMPTION (additive % of the
    original site-weighted value) until research confirms it against settlement.json; switch
    `mode` to 'multiplicative' if the fit says so."""

    def __init__(self, mode="additive"):
        self.mode = mode
        self.reports = {}               # (round, stage) -> report

    def apply(self, m):
        self.reports[(int(m["round"]), m["stage"])] = m

    def rebuild(self, damage_list):
        for m in damage_list or []:
            if isinstance(m, dict) and "stage" in m and "round" in m:
                self.apply(m)

    def rounds_final(self):
        return sorted(r for (r, st) in self.reports if st == "final")

    def per_round(self, provisional=True):
        """round -> (damage_pct dict, loss_musd, stage used)."""
        out = {}
        for (r, st), m in self.reports.items():
            if st == "final" or (provisional and (r, "final") not in self.reports):
                out[r] = (m.get("damage_pct") or {}, float(m.get("industry_loss_musd") or 0.0), st)
        return out

    def cumulative_damage(self, provisional=True):
        cum = {s: 0.0 for s in ISLAND_BUSINESSES}
        pr = self.per_round(provisional)
        for r in sorted(pr):
            dmg = pr[r][0]
            for s in cum:
                d = float(dmg.get(s, 0.0)) / 100.0
                if self.mode == "additive":
                    cum[s] += d
                else:
                    cum[s] = 1.0 - (1.0 - cum[s]) * (1.0 - d)
        return cum

    def season_loss(self, provisional=True):
        return sum(v[1] for v in self.per_round(provisional).values())


class Book:
    __slots__ = ("bid", "ask", "bid_size", "ask_size", "ts", "last")

    def __init__(self):
        self.bid = self.ask = self.bid_size = self.ask_size = self.last = None
        self.ts = 0.0

    def mid(self):
        if self.bid is not None and self.ask is not None:
            return 0.5 * (self.bid + self.ask)
        return self.bid if self.ask is None else self.ask

    def half_spread(self):
        if self.bid is not None and self.ask is not None:
            return 0.5 * (self.ask - self.bid)
        return None


class OrderRec:
    __slots__ = ("id", "tag", "symbol", "side", "price", "qty", "remaining", "status", "sent_mono",
                 "strategy", "priority", "fair_ref", "maybe_gone")

    def __init__(self, tag, symbol, side, price, qty, strategy, priority, fair_ref=None):
        self.id, self.tag, self.symbol, self.side, self.price = None, tag, symbol, side, price
        self.qty = self.remaining = int(qty)
        self.status = "pending"           # pending -> open -> (filled | canceled | rejected | unknown)
        self.sent_mono = time.monotonic()
        self.strategy, self.priority, self.fair_ref = strategy, priority, fair_ref
        self.maybe_gone = False           # exchange may have cancelled it (limit room / self-trade)


class State:
    def __init__(self, symbols, limits):
        self.symbols, self.limits = list(symbols), dict(limits)
        self.reset_season(None)

    def reset_season(self, comp_id):
        self.comp_id = comp_id
        self.round = None
        self.round_date = None
        self.storm = StormState()
        self.ledger = DamageLedger()
        self.books = {s: Book() for s in self.symbols}
        self.pos = {s: 0 for s in self.symbols}
        self.marks = {s: None for s in self.symbols}
        self.pnl_mtm = 0.0
        self.pnl_peak = 0.0
        self.orders = {}                   # tag -> OrderRec (live or pending)
        self.announcements = {}            # seq -> message
        self.overrides = {}                # symbol -> {"fair_mult": x, "fair_abs": y, "disabled": bool}
        self.headlines = []
        self.last_news_mono = 0.0
        self.recovered_from_snapshot = 0
        self.phase = "unknown"

    # -- feed ---------------------------------------------------------------------------------
    def on_message(self, m):
        r = m.get("round")
        if isinstance(r, str) and r.isdigit():
            r = int(r)
        if isinstance(r, int) and r != self.round and m.get("type") not in ("announcement", "news"):
            self.round = r
            self.storm = StormState()
            self.storm.round = r
        self.last_news_mono = time.monotonic()
        t = m.get("type")
        if t in ("storm", "track", "storm_formed", "storm_dissipated", "advisory", "impact"):
            if m.get("storm"):
                self.storm.name = m["storm"]
            self.storm.apply(m)
        elif t == "damage_report":
            self.ledger.apply(m)
        elif t == "announcement":
            self.announcements[m.get("seq", len(self.announcements))] = m
        if m.get("headline"):
            self.headlines = (self.headlines + [m["headline"]])[-50:]

    def on_snapshot(self, snap):
        self.ledger.rebuild(snap.get("damage"))
        r = snap.get("round")
        if isinstance(r, int) and r != self.round:     # snapshot of a round we have not seen start
            self.round, self.storm = r, StormState()
            self.storm.round, self.storm.name = r, snap.get("storm")
        self.storm.apply_snapshot(snap)
        self.phase = snap.get("phase", self.phase)
        self.recovered_from_snapshot += 1

    def on_quote(self, frame):
        s = frame.get("symbol")
        b = self.books.get(s)
        if b is None:
            return
        for k_in, k_out in (("bid", "bid"), ("ask", "ask"), ("bid_size", "bid_size"), ("ask_size", "ask_size"),
                            ("bid_qty", "bid_size"), ("ask_qty", "ask_size")):
            if frame.get(k_in) is not None:
                setattr(b, k_out, frame[k_in])
        b.ts = time.monotonic()

    # -- exposure -----------------------------------------------------------------------------
    def resting(self, symbol, side):
        return sum(o.remaining for o in self.orders.values()
                   if o.symbol == symbol and o.side == side and o.status in ("pending", "open", "unknown")
                   and not o.maybe_gone)

    def room(self, symbol, side, count_resting=True):
        """Shares we may still add on `side` without the exchange shrinking/rejecting the order.
        Limit includes position AND resting same-side orders (packet section 8)."""
        lim, p = self.limits[symbol], self.pos[symbol]
        rest = self.resting(symbol, side) if count_resting else sum(
            o.remaining for o in self.orders.values() if o.symbol == symbol and o.side == side and o.status == "pending")
        return max(0, (lim - p - rest) if side == "buy" else (lim + p - rest))

    # -- persistence --------------------------------------------------------------------------
    def to_json(self):
        return json.dumps({"comp_id": self.comp_id, "round": self.round, "pos": self.pos,
                           "damage_reports": list(self.ledger.reports.values()),
                           "announcements": list(self.announcements.values()),
                           "overrides": self.overrides, "saved_at": time.time()})

    def load_json(self, text):
        d = json.loads(text)
        self.comp_id = d.get("comp_id")
        self.ledger.rebuild(d.get("damage_reports"))
        for m in d.get("announcements") or []:
            self.announcements[m.get("seq")] = m
        self.overrides = d.get("overrides") or {}
