"""Offline replay / backtest harness and feed chaos test. Never touches the network.

*** UNTESTED AGAINST THE EXCHANGE. The fill model is an ASSUMPTION until testnet spreads/depth
    are measured; every result is only as good as `--half-spread-bp` and `--latency`. ***

    python3 -I replay.py --history DIR --seasons 401-600 --model oracle_final
    python3 -I replay.py --history DIR --seasons 1-50 --chaos
    python3 -I replay.py --history DIR --seasons 401-600 --model oracle_final --sweep settle.eps_bp=2,5,20

Inputs per season: feed.jsonl (news, exact seq), market.csv (house consensus every 2 real s =
1 market minute, "a few seconds behind the news"), settlement.json. manifest.json is NEVER given
to a strategy (only to analysis), to avoid look-ahead.

Event-driven timeline: global sim time t = (round-1)*ROUND_GAP + real seconds since that round's
open. A news message at market time T is delivered at t(T); market row i at 2*i. At equal t the
news is processed first (the row still shows the pre-news house price, as on the real screen).
Fill model: an order sent at t becomes active at t + latency and trades against the first row at
or after that: buys cross at mid*(1+hs), sells at mid*(1-hs) (hs = half-spread), plus linear impact
impact_bp * qty/limit. Unfilled limit residuals rest and fill later only if the synthetic touch
trades THROUGH them (conservative). Position limits incl. resting orders, exchange cancels for
room (worst price first) and self-trade cancels are simulated as in the packet. The action
budget uses the same ratelimit.ActionLimiter on simulated time. PnL = cash + sum(pos * settlement).
Strategies and State are the live modules (shared code).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse  # noqa: E402
import csv  # noqa: E402
import json  # noqa: E402
import random  # noqa: E402
import statistics  # noqa: E402
import time  # noqa: E402

import clock  # noqa: E402
import config  # noqa: E402
import fairvalue  # noqa: E402
import feed  # noqa: E402
import ratelimit  # noqa: E402
import risk  # noqa: E402
import state as state_mod  # noqa: E402
import strategy  # noqa: E402

ROUND_GAP = 1000.0


def load_season(folder):
    with open(os.path.join(folder, "feed.jsonl")) as f:
        msgs = [json.loads(line) for line in f]
    rows = {1: [], 2: [], 3: []}
    with open(os.path.join(folder, "market.csv")) as f:
        for r in csv.DictReader(f):
            rows[int(r["round"])].append({"time": r["time"], **{s: float(r[s]) for s in config.SYMBOLS}})
    with open(os.path.join(folder, "settlement.json")) as f:
        settle = json.load(f)
    return msgs, rows, settle


def timeline(msgs, rows):
    ev = []
    for r, rr in rows.items():
        for i, row in enumerate(rr):
            ev.append(((r - 1) * ROUND_GAP + 2.0 * i, 1, "md", r, row))
    for m in msgs:
        t = (m["round"] - 1) * ROUND_GAP + clock.real_s_since_open(clock.parse_mt(m["time"]))
        ev.append((t, 0, "news", m["round"], m))
    ev.sort(key=lambda e: (e[0], e[1], e[4].get("seq", 0) if e[2] == "news" else 0))
    return ev


class SimOrder:
    __slots__ = ("tag", "symbol", "side", "price", "qty", "active_t", "strategy")

    def __init__(self, tag, symbol, side, price, qty, active_t, strat):
        self.tag, self.symbol, self.side, self.price, self.qty = tag, symbol, side, price, qty
        self.active_t, self.strategy = active_t, strat


class SimExchange:
    def __init__(self, settle, half_spread_bp, latency, impact_bp=0.0):
        self.S, self.hs, self.lat, self.imp = settle, half_spread_bp * 1e-4, latency, impact_bp * 1e-4
        self.mid = {}
        self.cash = 0.0
        self.pos = {s: 0 for s in config.SYMBOLS}
        self.pending, self.resting = [], []
        self.fills = []
        self.by_strat = {}
        self._n = 0

    def touch(self, s):
        m = self.mid[s]
        return m * (1 - self.hs), m * (1 + self.hs)

    def submit(self, it, t):
        self._n += 1
        self.pending.append(SimOrder(f"o{self._n}", it.symbol, it.side, it.price, int(it.qty), t + self.lat, it.strategy))

    def cancel_all(self, symbol=None, t=None):
        self.resting = [o for o in self.resting if symbol is not None and o.symbol != symbol]
        self.pending = [o for o in self.pending if symbol is not None and o.symbol != symbol]

    def convert(self, etf, qty, create):
        sign = 1 if create else -1           # create: give basket, get ETF
        bv = sum(w * self.mid[c] for c, w in config.BASKETS[etf].items())
        for c, w in config.BASKETS[etf].items():
            self.pos[c] -= sign * w * qty
            self.cash += sign * w * qty * self.mid[c]
        self.pos[etf] += sign * qty
        self.cash -= sign * qty * bv

    def _fill(self, o, qty, px):
        sgn = 1 if o.side == "buy" else -1
        self.pos[o.symbol] += sgn * qty
        self.cash -= sgn * qty * px
        self.fills.append((o.symbol, o.side, qty, px, o.strategy))
        self.by_strat.setdefault(o.strategy, []).append((o.symbol, sgn * qty, px))

    def _room(self, s, side):
        lim = config.LIMITS[s]
        rest_q = sum(o.qty for o in self.resting if o.symbol == s and o.side == side)
        return (lim - self.pos[s] - rest_q) if side == "buy" else (lim + self.pos[s] - rest_q)

    def on_row(self, row, t):
        for s in config.SYMBOLS:
            self.mid[s] = row[s]
        # 1) resting orders fill only if the synthetic touch trades through them
        keep = []
        for o in self.resting:
            bid, ask = self.touch(o.symbol)
            if (o.side == "buy" and ask <= o.price) or (o.side == "sell" and bid >= o.price):
                self._fill(o, o.qty, o.price)
            else:
                keep.append(o)
        self.resting = keep
        # 2) newly active orders
        still = []
        for o in self.pending:
            if o.active_t > t:
                still.append(o)
                continue
            s, lim = o.symbol, config.LIMITS[o.symbol]
            bid, ask = self.touch(s)
            px_touch = ask if o.side == "buy" else bid
            marketable = o.price is None or (o.price >= ask if o.side == "buy" else o.price <= bid)
            # self-trade prevention: our crossing order cancels our resting opposite order
            self.resting = [r for r in self.resting if not (r.symbol == s and r.side != o.side and marketable and (
                (o.side == "buy" and (o.price is None or r.price <= o.price)) or
                (o.side == "sell" and (o.price is None or r.price >= o.price))))]
            if marketable:
                # exchange cancels our worst same-side resting orders if the fill needs the room
                same = sorted((r for r in self.resting if r.symbol == s and r.side == o.side),
                              key=lambda r: r.price if o.side == "buy" else -r.price)
                for r in same:
                    if self._room(s, o.side) >= o.qty:
                        break
                    self.resting.remove(r)
                q = min(o.qty, max(0, self._room(s, o.side)))
                if q > 0:
                    sl = 1 + self.imp * q / lim
                    px = px_touch * sl if o.side == "buy" else px_touch / sl
                    if o.price is not None:          # a limit order never fills through its limit
                        px = min(px, o.price) if o.side == "buy" else max(px, o.price)
                    self._fill(o, q, px)
                left = 0 if o.price is None else o.qty - q
                if left > 0 and self._room(s, o.side) >= left:
                    o.qty = left
                    self.resting.append(o)
            else:
                q = min(o.qty, self._room(s, o.side))
                if q > 0:
                    o.qty = q
                    self.resting.append(o)
        self.pending = still

    def pnl(self):
        return self.cash + sum(q * self.S[s] for s, q in self.pos.items())

    def pnl_by_strategy(self):
        return {k: sum(q * (self.S[s] - px) for s, q, px in v) for k, v in self.by_strat.items()}


class OracleFinalModel(fairvalue.FairValueModel):
    """HARNESS VALIDATION ONLY -- LEAKS settlement.json, but only once the round-3 final report is
    in the state, i.e. it is the 'perfect valuation model' upper bound for the final-40-s snipe."""
    name = "oracle_final"

    def __init__(self, settle):
        self.S = settle

    def evaluate(self, st, now_real):
        return {}

    def settlement(self, st):
        return dict(self.S) if st.round == 3 and (3, "final") in st.ledger.reports else None


def run_season(folder, model_name, hs_bp, latency, impact_bp, strat_cfg):
    msgs, rows, settle = load_season(folder)
    ex = SimExchange(settle, hs_bp, latency, impact_bp)
    st = state_mod.State(config.SYMBOLS, config.LIMITS)
    sim_t = [0.0]
    lim = ratelimit.ActionLimiter(config.BOT_ACTION_CAP, config.LIMITER_WINDOW_S, config.PRIORITY_FLOORS,
                                  clock=lambda: sim_t[0])
    rm = risk.RiskManager(st, log=lambda *a, **k: None)
    rm.market_open = True
    model = OracleFinalModel(settle) if model_name == "oracle_final" else fairvalue.HouseFollowModel()
    strats = strategy.build_strategies(strat_cfg)
    norm = feed.FeedNormalizer()
    actions = 0

    def sync():
        st.pos = dict(ex.pos)
        st.orders = {}
        for o in ex.resting + ex.pending:
            rec = state_mod.OrderRec(o.tag, o.symbol, o.side, o.price, o.qty, o.strategy, 3)
            rec.status = "open"
            st.orders[o.tag] = rec

    def act(intents, t, now_real):
        nonlocal actions
        for it in intents:
            if it.kind == "order":
                q, _ = rm.check(it)
                if q <= 0 or not lim.try_acquire(it.priority, 1, it.strategy):
                    continue
                it.qty = q
                ex.submit(it, t)
            elif it.kind in ("cancel_all", "quotes_off_all", "quote_off"):
                if it.kind != "cancel_all" and not ex.resting:
                    continue
                if not lim.try_acquire(it.priority, 1, "cancel"):
                    continue
                ex.cancel_all(it.symbol if it.kind != "quotes_off_all" else None)
            elif it.kind in ("create", "redeem"):
                if lim.try_acquire(it.priority, 1, it.kind):
                    ex.convert(it.symbol, it.qty, it.kind == "create")
            actions += 1
            sync()

    for t, _, kind, rnd, payload in timeline(msgs, rows):
        sim_t[0] = t
        now_real = t - (rnd - 1) * ROUND_GAP
        st.phase = clock.phase_of(now_real)
        if kind == "md":
            ex.on_row(payload, t)
            for s in config.SYMBOLS:
                b = st.books[s]
                b.bid, b.ask = ex.touch(s)
            sync()
            ctx = strategy.Ctx(st, model.evaluate(st, now_real), now_real, model.settlement(st), lim.available(), mono=t)
            act([i for s in strats for i in s.on_tick(ctx)], t, now_real)
        else:
            evs = norm.ingest(payload, 0.0)
            for e in evs:
                if e.kind not in ("season_reset",):
                    st.on_message(e.msg)
            ctx = strategy.Ctx(st, model.evaluate(st, now_real), now_real, model.settlement(st), lim.available(), mono=t)
            act([i for s in strats for i in s.on_batch(evs, ctx)], t, now_real)
    return {"pnl": ex.pnl(), "by_strategy": ex.pnl_by_strategy(), "fills": len(ex.fills), "actions": actions,
            "denied": dict(lim.denied)}


# ---- chaos test of FeedNormalizer + State ----------------------------------------------------
def _snapshot_from(st, last_seq):
    s = st.storm
    trk = s.sorted_track()[-40:]
    return {"type": "snapshot", "round": st.round, "storm": s.name, "time": None, "phase": "storm", "last_seq": last_seq,
            "obs": {k: v for k, v in (s.obs or {}).items() if k not in ("type", "round", "storm")} or None,
            "track": [list(p) for p in trk], "advisory": s.latest_official(),
            "impacts": [{"island": k, **v} for k, v in s.impacts.items()],
            "damage": list(st.ledger.reports.values()), "headlines": st.headlines[-8:]}


def chaos_frames(msgs, rng, p_dup=0.02, n_bursts=6, n_gaps=2):
    """Duplicates, catch-up bursts (storm/track: only the latest of the group arrives), and
    disconnect gaps followed by a snapshot built from a clean shadow state (as the exchange would)."""
    n = len(msgs)
    drop = set()
    for _ in range(n_bursts):
        a = rng.randrange(n)
        seg = range(a, min(n, a + rng.randint(5, 25)))
        for typ in ("storm", "track"):
            idx = [k for k in seg if msgs[k]["type"] == typ]
            drop.update(idx[:-1])                    # superseded inside the burst: never delivered
    gap = set()
    for _ in range(n_gaps):
        a = rng.randrange(n)
        gap.update(range(a, min(n, a + rng.randint(5, 20))))
    shadow = state_mod.State(config.SYMBOLS, config.LIMITS)
    out, i = [], 0
    while i < n:
        m = msgs[i]
        shadow.on_message(m)
        if i in gap:
            j = i
            while j + 1 < n and (j + 1) in gap:
                j += 1
                shadow.on_message(msgs[j])
            out.append({"type": "news", "message": _snapshot_from(shadow, msgs[j]["seq"])})
            i = j + 1
            continue
        if i not in drop:
            out.append({"type": "news", "message": m})
            if rng.random() < p_dup:
                out.append({"type": "news", "message": dict(m)})
        i += 1
    return out


def chaos_check(msgs, seed):
    rng = random.Random(seed)
    clean = state_mod.State(config.SYMBOLS, config.LIMITS)
    for m in msgs:
        clean.on_message(m)
    st = state_mod.State(config.SYMBOLS, config.LIMITS)
    norm = feed.FeedNormalizer()
    for fr in chaos_frames(msgs, rng):
        for e in norm.ingest(fr, 0.0):
            if e.kind == "snapshot":
                st.on_snapshot(e.msg)
            elif e.kind not in ("season_reset",):
                st.on_message(e.msg)
    problems = []
    if st.ledger.reports != clean.ledger.reports:
        problems.append("damage ledger")
    if abs(st.ledger.season_loss() - clean.ledger.season_loss()) > 1e-9:
        problems.append("season loss")
    if (st.storm.obs or {}).get("age") != (clean.storm.obs or {}).get("age"):
        problems.append("last obs")
    if set(st.storm.impacts) != set(clean.storm.impacts):
        problems.append("impacts (last round)")
    return problems, norm.duplicates


def parse_range(s):
    a, _, b = s.partition("-")
    return list(range(int(a), int(b or a) + 1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--history", required=True)
    ap.add_argument("--seasons", default="401-600")
    ap.add_argument("--model", default="oracle_final", choices=["oracle_final", "house_follow"])
    ap.add_argument("--half-spread-bp", type=float, default=10.0)
    ap.add_argument("--latency", type=float, default=1.0, help="real seconds from decision to order active")
    ap.add_argument("--impact-bp", type=float, default=0.0, help="extra bp per full-limit order")
    ap.add_argument("--chaos", action="store_true")
    ap.add_argument("--sweep", default=None, help="module.param=v1,v2,...")
    args = ap.parse_args()
    with open(os.path.join(args.history, "manifest.json")) as f:
        dirs = {s["season"]: s["dir"] for s in json.load(f)["seasons"]}
    seasons = [n for n in parse_range(args.seasons) if n in dirs]
    if args.chaos:
        bad, dups = 0, 0
        for n in seasons:
            msgs, _, _ = load_season(os.path.join(args.history, dirs[n]))
            probs, d = chaos_check(msgs, seed=n)
            dups += d
            if probs:
                bad += 1
                print(f"season {n}: MISMATCH {probs}")
        print(f"chaos test: {len(seasons) - bad}/{len(seasons)} seasons converge to the clean state; "
              f"{dups} duplicates dropped")
        return 0
    grid = [None]
    if args.sweep:
        key, _, vals = args.sweep.partition("=")
        mod, _, par = key.partition(".")
        grid = [(mod, par, float(v)) for v in vals.split(",")]
    for g in grid:
        cfg = json.loads(json.dumps(config.STRAT))
        cfg["taker"]["enabled"] = cfg["sniper"]["enabled"] = cfg["quoter"]["enabled"] = False
        if g:
            cfg[g[0]][g[1]] = g[2]
        t0 = time.time()
        res = [run_season(os.path.join(args.history, dirs[n]), args.model, args.half_spread_bp, args.latency,
                          args.impact_bp, cfg) for n in seasons]
        p = sorted(r["pnl"] for r in res)
        label = f"{g[0]}.{g[1]}={g[2]}" if g else "base"
        print(f"{label:24s} model={args.model} hs={args.half_spread_bp}bp lat={args.latency}s n={len(p)}  "
              f"mean ${statistics.mean(p):,.0f}  median ${p[len(p)//2]:,.0f}  p10 ${p[len(p)//10]:,.0f}  "
              f"min ${p[0]:,.0f}  actions/season {statistics.mean(r['actions'] for r in res):.1f}  "
              f"({time.time() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
