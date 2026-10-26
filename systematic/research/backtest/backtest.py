"""Event-driven backtest of the Systematic Equities (Hurricane Season) strategies against the house consensus.

Standard library only; run as python3 -I. Never writes into the data folder.

Inputs per season (research data, read-only): feed.jsonl (seq order), market.csv (house consensus every 2 real s,
row i of a round = real second 2i, 390 rows), settlement.json; plus the engine fair-value cache built by
fvcache.py (one engine evaluation after every distinct message time, messages with seq <= current only).

Timeline (per round): decision times = every market row time 2i (i = 0..389) UNION every message time (real s).
At decision time t the strategy sees
  * the engine fair (mu, sd) after all messages with time <= t (cache entry with the largest t_e <= t),
  * the last house price P = row floor(t/2) (the row stamped at or before t).
Fill model: an order decided at t fills at the house price of the NEXT row, j = floor(t/2) + 1, at
  P_j * (1 + h) for buys and P_j * (1 - h) for sells (h = half-spread, fraction of price). No fill if j > 389.
  (The house reacts to a message at t at row floor(t/2)+2 or +3 (verify-house), so j is the last stale row.)
Constraints: |position| <= limit at all times; team rate limit 180 order actions in any rolling 60 real s
  (one action per order; orders over budget are dropped and re-evaluated at the next decision, highest
  priority / largest $ edge first); optional market-impact variant caps each symbol's fills at cap*limit per row.
Scoring: PnL = cash + position x settlement (round 3). Round attribution: mark-to-market at the last house row of
  rounds 1-2 (row 389) and at settlement for round 3; by symbol: cash_s + pos_s x settle_s.

Strategies are lists of windows (see make_* below). Window modes:
  taker : threshold rule vs the engine (below)      flat  : target 0      fixed : clock rule with fixed direction
Taker rule for symbol s (limit L, edge e = mu - P, threshold th = k*sd + h*P):
  e >  th  -> target +size*L (only trades if it increases the long)
  e < -th  -> target -size*L
  else if pos > 0 and e < -(xk*sd + h*P) -> target 0      (fair below the bid: exit)
  else if pos < 0 and e >  (xk*sd + h*P) -> target 0
  else hold.     size = 1 (full limit) or min(1, |e|/(zfull*sd)) when zfull is set.

Usage:
  python3 -I backtest.py <SP> <experiment> [nproc]      experiments: see EXPERIMENTS at the bottom
"""
import bisect
from collections import deque
import csv
import json
import math
import multiprocessing as mp
import os
import sys
import time
from datetime import datetime

SYMBOLS = ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "RECON", "MAT", "GEN", "HAVEN", "REIN", "ARCH", "REBLD"]
SI = {s: i for i, s in enumerate(SYMBOLS)}
NS = len(SYMBOLS)
LIMITS = {"PORT": 200, "ISLE": 300, "AQUA": 500, "GRID": 400, "FERRY": 800, "RECON": 500, "MAT": 700,
          "GEN": 600, "HAVEN": 250, "REIN": 200, "ARCH": 50, "REBLD": 100}
LIM = [LIMITS[s] for s in SYMBOLS]
ISLAND = ["PORT", "ISLE", "AQUA", "GRID", "FERRY"]
REBUILD = ["RECON", "MAT", "GEN"]
NROWS = 390
RATE = 180
WINDOW_S = 60.0

SP = None


def paths(sp):
    return (os.path.join(sp, "data/hurricane-season-research/history"), os.path.join(sp, "work/se/backtest/cache"))


def real_second(tm):
    t = datetime.fromisoformat(tm)
    return round((t - t.replace(hour=9, minute=30, second=0, microsecond=0)).total_seconds() / 30.0, 3)


# ---------------------------------------------------------------------------------------------- data
class Season:
    __slots__ = ("n", "rows", "settle", "fv", "fvt", "msgs", "dec")

    def __init__(self, sp, n, lib):
        hist, cache = paths(sp)
        d = os.path.join(hist, f"season-{n:03d}")
        self.n = n
        self.rows = {1: [], 2: [], 3: []}
        with open(os.path.join(d, "market.csv")) as f:
            for row in csv.DictReader(f):
                self.rows[int(row["round"])].append([float(row[s]) for s in SYMBOLS])
        for r in (1, 2, 3):
            assert len(self.rows[r]) == NROWS, (n, r, len(self.rows[r]))
        with open(os.path.join(d, "settlement.json")) as f:
            st = json.load(f)
        self.settle = [float(st[s]) for s in SYMBOLS]
        self.msgs = {1: [], 2: [], 3: []}
        with open(os.path.join(d, "feed.jsonl")) as f:
            for line in f:
                m = json.loads(line)
                if m.get("type") in ("storm", "track"):
                    self.msgs[int(m["round"])].append((real_second(m["time"]), m["type"], None))
                else:
                    self.msgs[int(m["round"])].append((real_second(m["time"]), m["type"], m))
        with open(os.path.join(cache, f"fv_{lib}_{n:03d}.json")) as f:
            ent = json.load(f)["entries"]
        # per round: list of (t, mu, sd); carry-in entry at t=-1 = last value of the previous round
        self.fv = {1: [], 2: [], 3: []}
        last = None
        for e in ent:
            r, t = int(e[0]), float(e[1])
            if r > 1 and not self.fv[r] and last is not None:
                self.fv[r].append((-1.0, last[0], last[1]))
            item = (t, e[2:2 + NS], e[2 + NS:2 + 2 * NS])
            self.fv[r].append(item)
            last = (item[1], item[2])
        for r in (2, 3):
            if not self.fv[r]:
                self.fv[r].append((-1.0, last[0], last[1]))
        self.fvt = {r: [x[0] for x in self.fv[r]] for r in (1, 2, 3)}
        # decision times
        self.dec = {}
        for r in (1, 2, 3):
            ts = {2.0 * i for i in range(NROWS)} | {m[0] for m in self.msgs[r]}
            self.dec[r] = sorted(ts)

    def fair_at(self, r, t):
        i = bisect.bisect_right(self.fvt[r], t + 1e-9) - 1
        x = self.fv[r][max(i, 0)]
        return x[1], x[2]

    def event_time(self, r, typ, stage=None):
        for t, ty, m in self.msgs[r]:
            if ty == typ and (stage is None or (m and m.get("stage") == stage)):
                return t
        return None


# ---------------------------------------------------------------------------------------------- simulator
def simulate(season, cfg, want_trades=False):
    """Run one strategy config over one season. Returns a summary dict."""
    h = cfg["h"]
    cap = cfg.get("cap")                 # per-row fill cap as a fraction of the limit (impact variant)
    rate = cfg.get("rate", RATE)
    lat_extra = cfg.get("lat_extra", 0)  # robustness: extra rows of latency (0 = the specified fill model)
    windows = cfg["windows"]
    risk = cfg.get("risk") or {}
    dd_max = risk.get("dd_stop")         # MTM drawdown (from the round's peak) that stops new risk for the round
    sig_cap = risk.get("sigma_cap")      # cap on sum_s |pos_s| * sd_s ($ of 1-sd engine risk)
    pos = [0] * NS
    cash = [0.0] * NS
    n_act = 0
    shares = 0
    notional = 0.0
    deferred = 0
    max60 = 0
    acts = deque()
    round_pnl = {}
    prev_val = 0.0
    trades = [] if want_trades else None
    by_win = {}
    gross_max = 0.0
    for r in (1, 2, 3):
        rows = season.rows[r]
        acts.clear()  # rounds are separated by a pause: the 60-s window restarts
        filled_row = {}
        peak = None
        stopped = False
        wins = [w for w in windows if r in w["rounds"]]
        extra = sorted({w["t0"] for w in wins} | {w.get("t_dec", w["t0"]) for w in wins})
        dts = season.dec[r]
        if extra:
            dts = sorted(set(dts) | set(float(x) for x in extra if 0 <= x < 780))
        for t in dts:
            act_w = [w for w in wins if w["t0"] <= t <= w["t1"]]
            if not act_w:
                continue
            ref = min(int(t // 2), NROWS - 1)
            j = int(t // 2) + 1 + lat_extra
            if j >= NROWS:
                continue
            P = rows[ref]
            mu, sd = season.fair_at(r, t)
            # risk bookkeeping (marks at the last seen house price)
            if dd_max is not None:
                val = sum(cash) + sum(pos[i] * P[i] for i in range(NS))
                peak = val if peak is None else max(peak, val)
                if peak - val > dd_max:
                    stopped = True
            orders = []
            done = set()
            for w in act_w:
                syms = w["syms"]
                mode = w["mode"]
                for s in syms:
                    i = SI[s]
                    if i in done:
                        continue
                    done.add(i)
                    L = LIM[i]
                    p = P[i]
                    cur = pos[i]
                    if mode == "flat":
                        tgt = 0
                        edge = 0.0
                    elif mode == "fixed":
                        tgt = w["dir"][s] * L
                        edge = 0.0
                    else:
                        e = mu[i] - p
                        th = w["k"] * sd[i] + h * p + w.get("min_edge", 0.0) * p
                        zf = w.get("zfull")
                        sz = 1.0 if not zf else min(1.0, abs(e) / max(zf * sd[i], 1e-9))
                        tgt = cur
                        if e > th:
                            t2 = int(round(L * sz * w.get("size", 1.0)))
                            if t2 > cur:
                                tgt = t2
                        elif e < -th:
                            t2 = -int(round(L * sz * w.get("size", 1.0)))
                            if t2 < cur:
                                tgt = t2
                        else:
                            xb = w.get("xk", 0.0) * sd[i] + h * p
                            if w.get("exit", True):
                                if cur > 0 and e < -xb:
                                    tgt = 0
                                elif cur < 0 and e > xb:
                                    tgt = 0
                        edge = abs(e)
                    q = tgt - cur
                    if q == 0:
                        continue
                    increases = abs(tgt) > abs(cur) or (tgt * cur < 0)
                    if stopped and increases:
                        # no new risk after the drawdown stop: only reduce toward 0
                        if cur == 0:
                            continue
                        tgt = 0 if tgt * cur <= 0 else tgt
                        q = tgt - cur
                        if q == 0:
                            continue
                    if sig_cap is not None and increases and mode == "taker":
                        used = sum(abs(pos[k2]) * sd[k2] for k2 in range(NS) if k2 != i)
                        room = max(0.0, sig_cap - used)
                        mx = int(room / max(sd[i], 1e-9))
                        if abs(tgt) > mx:
                            tgt = mx if tgt > 0 else -mx
                            if abs(tgt) < abs(cur) and tgt * cur >= 0:
                                tgt = cur
                            q = tgt - cur
                            if q == 0:
                                continue
                    if cap:
                        room = int(cap * L) - filled_row.get((i, j), 0)
                        if room <= 0:
                            continue
                        if abs(q) > room:
                            q = room if q > 0 else -room
                    minq = max(1, int(w.get("min_clip", 0.0) * L))
                    if abs(q) < minq and tgt != 0:
                        continue
                    orders.append((w.get("prio", 5), -edge * abs(q), i, q, w["name"]))
            if not orders:
                continue
            orders.sort()
            # rate limiter
            while acts and acts[0] <= t - WINDOW_S:
                acts.popleft()
            for prio, _, i, q, wname in orders:
                if len(acts) >= rate:
                    deferred += 1
                    continue
                acts.append(t)
                n_act += 1
                px = rows[j][i] * (1.0 + h if q > 0 else 1.0 - h)
                cash[i] -= q * px
                pos[i] += q
                shares += abs(q)
                notional += abs(q) * px
                filled_row[(i, j)] = filled_row.get((i, j), 0) + abs(q)
                bw = by_win.setdefault(wname, [0.0, 0, 0])
                bw[0] += q * (season.settle[i] - px)   # trade-level PnL to settlement (sums to total PnL)
                bw[1] += 1
                bw[2] += abs(q)
                if trades is not None:
                    trades.append((r, t, j, SYMBOLS[i], q, round(px, 4), wname))
            max60 = max(max60, len(acts))
            g = sum(abs(pos[k2]) * P[k2] for k2 in range(NS))
            gross_max = max(gross_max, g)
        mark = rows[NROWS - 1] if r < 3 else season.settle
        val = sum(cash) + sum(pos[i] * mark[i] for i in range(NS))
        round_pnl[r] = val - prev_val
        prev_val = val
    pnl = sum(cash) + sum(pos[i] * season.settle[i] for i in range(NS))
    by_sym = [cash[i] + pos[i] * season.settle[i] for i in range(NS)]
    out = {"pnl": pnl, "round": [round_pnl[1], round_pnl[2], round_pnl[3]], "sym": by_sym, "act": n_act,
           "shares": shares, "notional": notional, "deferred": deferred, "max60": max60, "gross_max": gross_max,
           "win": by_win}
    if trades is not None:
        out["trades"] = trades
    return out


# ---------------------------------------------------------------------------------------------- strategies
ALL = SYMBOLS


def W(name, rounds, t0, t1, mode="taker", syms=ALL, prio=5, **kw):
    d = {"name": name, "rounds": list(rounds), "t0": float(t0), "t1": float(t1), "mode": mode, "syms": list(syms),
         "prio": prio}
    d.update(kw)
    return d


def flat(name, rounds, t, syms=ALL, prio=0, dur=12):
    return W(name, rounds, t, t + dur, mode="flat", syms=syms, prio=prio)


def S1(k=0.0, exit12="flat", **kw):
    """Final-report snipe: from the final damage report (real 740) to the close; trade the engine vs house.
    exit12: 'flat' -> rounds 1-2 flattened at 770 (round 3 held to settlement); 'hold' -> held to settlement."""
    ws = []
    if exit12 == "flat":
        ws.append(flat("S1x", (1, 2), 770, prio=1))
        ws.append(W("S1", (1, 2), 740, 768, k=k, prio=1, **kw))
        ws.append(W("S1", (3,), 740, 778, k=k, prio=1, **kw))
    else:
        ws.append(W("S1", (1, 2, 3), 740, 778, k=k, prio=1, **kw))
    return ws


def S2(k=0.0, exit="600", **kw):
    """Preliminary-report trade: from the preliminary report (real 570); exit '600' -> flatten at 600 (after the
    house has absorbed it), '738' -> flatten just before the final report, 'hold' -> hold to settlement."""
    ws = []
    if exit == "600":
        ws.append(flat("S2x", (1, 2, 3), 600, prio=1))
        ws.append(W("S2", (1, 2, 3), 570, 598, k=k, prio=2, **kw))
    elif exit == "738":
        ws.append(flat("S2x", (1, 2, 3), 736, prio=1, dur=2))
        ws.append(W("S2", (1, 2, 3), 570, 598, k=k, prio=2, **kw))
    else:
        ws.append(W("S2", (1, 2, 3), 570, 598, k=k, prio=2, **kw))
    return ws


def S3(k=0.5, t1=540, exit12="568", exit3="hold", **kw):
    """Mid-storm fair-value taker: storm formation (60) to t1; exit12/exit3: forced flatten time in rounds 1-2 /
    round 3 ('568' just before the prelim, '770' at the close, 'hold' = keep to settlement)."""
    ws = []
    for rr, ex in (((1, 2), exit12), ((3,), exit3)):
        if ex != "hold":
            ws.append(flat("S3x", rr, float(ex), prio=1))
        ws.append(W("S3", rr, 60, t1, k=k, prio=3, **kw))
    return ws


def S4(k=0.0, exit="hold", syms=("HAVEN", "REIN"), **kw):
    """Insurer-layer positioning at the round opens: pre-storm window 0-58 s of each round (fair = previous rounds'
    final reports + library storms). exit 'hold' -> hold (re-evaluated at the next open), '72' -> flatten after the
    formation reaction, '770' -> flatten at the round close."""
    ws = []
    if exit != "hold":
        ws.append(flat("S4x", (1, 2, 3), float(exit), syms=syms, prio=1))
    ws.append(W("S4", (1, 2, 3), 0, 58, k=k, syms=syms, prio=4, **kw))
    return ws


H_LONG = ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "HAVEN", "REIN", "ARCH"]
H_SHORT = ["RECON", "MAT", "GEN", "REBLD"]


def H1(exit_t=72, t_in=60):
    """House-behaviour reference (verify-house): formation snipe. At storm_formed (60): long islands, HAVEN, REIN,
    ARCH; short rebuilders and REBLD at full limits (fill row 31, the house reacts at rows 32-36); flatten at exit_t."""
    d = {s: 1 for s in H_LONG}
    d.update({s: -1 for s in H_SHORT})
    return [flat("H1x", (1, 2, 3), exit_t, prio=1), W("H1", (1, 2, 3), t_in, t_in, mode="fixed", dir=d, prio=2)]


def H2(t_in=220, t_out=538, rein=False):
    """House-behaviour reference (verify-house): clock drift. At t_in short islands, HAVEN, ARCH (REIN optional), long
    rebuilders and REBLD, full limits; flatten at t_out."""
    d = {s: -1 for s in ISLAND + ["HAVEN", "ARCH"] + (["REIN"] if rein else [])}
    d.update({s: 1 for s in REBUILD + ["REBLD"]})
    return [flat("H2x", (1, 2, 3), t_out, syms=list(d), prio=1),
            W("H2", (1, 2, 3), t_in, t_in, mode="fixed", dir=d, syms=list(d), prio=3)]


def cfg(name, h, windows, **kw):
    d = {"name": name, "h": h, "windows": windows}
    d.update(kw)
    return d


# ---------------------------------------------------------------------------------------------- wire fade (S5)
def s5_fade(season, h, thr_pct, hold_s=60, require_disagree=True):
    """Fade the house's reaction to a wire rumor that disagrees with the latest official outlook.
    Wire at t_w; the house reacts at row a = floor(t_w/2)+2 (even t_w) / +3 (odd). Impulse I_s = P_a/P_b - 1 with
    b = floor(t_w/2). If |I_s| > thr: decide at real 2a (row a seen), trade against I_s at full limit (fill row a+1),
    flatten hold_s later (fill row a+1+hold_s/2). Stocks only (10). Overlapping trades in a symbol are skipped."""
    pnl = 0.0
    n_tr = 0
    rnd = [0.0, 0.0, 0.0]
    for r in (1, 2, 3):
        rows = season.rows[r]
        off = None
        busy_until = [-1] * NS
        for t, ty, m in season.msgs[r]:
            if ty != "advisory" or not m:
                continue
            if m.get("source") == "official":
                off = m.get("outlook")
                continue
            if require_disagree and (off is None or m.get("outlook") == off):
                continue
            b = int(t // 2)
            a = b + (2 if int(t) % 2 == 0 else 3)
            j_in = a + 1
            j_out = j_in + int(hold_s // 2)
            if j_out >= NROWS:
                continue
            for i in range(10):
                if busy_until[i] >= j_in:
                    continue
                imp = rows[a][i] / rows[b][i] - 1.0
                if abs(imp) * 100 <= thr_pct:
                    continue
                q = -LIM[i] if imp > 0 else LIM[i]
                p_in = rows[j_in][i] * (1 + h if q > 0 else 1 - h)
                p_out = rows[j_out][i] * (1 - h if q > 0 else 1 + h)
                v = q * (p_out - p_in)
                pnl += v
                rnd[r - 1] += v
                n_tr += 1
                busy_until[i] = j_out
    return {"pnl": pnl, "round": rnd, "n": n_tr}


# ---------------------------------------------------------------------------------------------- parallel driver
G = {}


def _init(sp, lib, cfgs):
    G["sp"], G["lib"], G["cfgs"] = sp, lib, cfgs


def _work(n):
    s = Season(G["sp"], n, G["lib"])
    res = []
    for c in G["cfgs"]:
        if c.get("kind") == "s5":
            res.append(s5_fade(s, c["h"], c["thr"], c.get("hold", 60), c.get("disagree", True)))
        else:
            res.append(simulate(s, c))
    return n, res


def run(sp, lib, seasons, cfgs, nproc=15):
    with mp.get_context("fork").Pool(nproc, initializer=_init, initargs=(sp, lib, cfgs)) as pool:
        out = dict(pool.imap_unordered(_work, seasons, chunksize=2))
    return [[out[n][k] for n in seasons] for k in range(len(cfgs))]


# ---------------------------------------------------------------------------------------------- statistics
def pct(v, q):
    v = sorted(v)
    if not v:
        return float("nan")
    x = q * (len(v) - 1)
    lo = int(math.floor(x))
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (x - lo)


def summarize(res):
    p = [x["pnl"] for x in res]
    n = len(p)
    m = sum(p) / n
    sd = math.sqrt(sum((q - m) ** 2 for q in p) / max(1, n - 1))
    out = {"n": n, "mean": m, "sd": sd, "t": m / (sd / math.sqrt(n)) if sd > 0 else 0.0, "p5": pct(p, 0.05),
           "p50": pct(p, 0.5), "hit": sum(q > 0 for q in p) / n, "zero": sum(q == 0 for q in p) / n}
    if "round" in res[0]:
        out["round"] = [sum(x["round"][k] for x in res) / n for k in range(3)]
        out["round_hit"] = [sum(x["round"][k] > 0 for x in res) / n for k in range(3)]
        out["round_sd"] = [math.sqrt(sum((x["round"][k] - out["round"][k]) ** 2 for x in res) / max(1, n - 1)) for k in range(3)]
    if "sym" in res[0]:
        out["sym"] = {SYMBOLS[i]: sum(x["sym"][i] for x in res) / n for i in range(NS)}
        out["act"] = sum(x["act"] for x in res) / n
        out["shares"] = sum(x["shares"] for x in res) / n
        out["notional"] = sum(x["notional"] for x in res) / n
        out["deferred"] = sum(x["deferred"] for x in res) / n
        out["max60"] = max(x["max60"] for x in res)
        out["max60_p95"] = pct([x["max60"] for x in res], 0.95)
        out["gross_max"] = sum(x["gross_max"] for x in res) / n
        wins = {}
        for x in res:
            for k2, v in x["win"].items():
                a = wins.setdefault(k2, [0.0, 0, 0])
                a[0] += v[0] / n
                a[1] += v[1] / n
                a[2] += v[2] / n
        out["win"] = wins
    if "n" in res[0] and "act" not in res[0]:
        out["trades"] = sum(x["n"] for x in res) / n
    return out


def fmt(s, extra=""):
    r = s.get("round", [float("nan")] * 3)
    base = (f"{s['mean']:9.0f} {s['sd']:8.0f} {s['t']:6.1f} {s['p5']:9.0f} {s['hit']:5.2f} | "
            f"{r[0]:8.0f} {r[1]:8.0f} {r[2]:8.0f}")
    if "act" in s:
        base += f" | {s['act']:6.1f} {s['max60']:4d} {s['deferred']:6.2f} {s['notional'] / 1e3:8.0f}k"
    return base + extra


HDR = (f"{'mean':>9s} {'sd':>8s} {'t':>6s} {'p5':>9s} {'hit':>5s} | {'R1':>8s} {'R2':>8s} {'R3':>8s} | "
       f"{'acts':>6s} {'mx60':>4s} {'defer':>6s} {'notional':>9s}")


def S6(p):
    """Combined strategy: one position per symbol, the taker rule switched on in the windows of S4 (open),
    S3 (storm), S2 (prelim) and S1 (final), each with its own k; optional H1 formation overlay. p keys:
      k4, s4syms, s4exit ('hold'|'72')      k3_12, k3_3, s3exit12 ('568'|'770'|'hold'), s3_t1
      k2, s2exit ('600'|'hold')             k1, s1exit12 ('flat'|'hold')      h1 (bool), r12flat ('770'|None)
    Flats close ALL symbols (one book per symbol). Window priority: flats > S1 > S2/H1 > S3 > S4."""
    ws = []
    r12 = (1, 2)
    if p.get("s1exit12", "flat") == "flat":
        ws.append(flat("S1x", r12, 770, prio=0, dur=8))
    if p.get("s2exit", "600") == "600":
        ws.append(flat("S2x", tuple(p.get("s2exit_rounds", (1, 2, 3))), 600, prio=0, dur=0))
    if p.get("s3exit12", "568") not in ("hold", None):
        ws.append(flat("S3x", r12, float(p["s3exit12"]), prio=0, dur=0))
    if p.get("s3exit3", "hold") not in ("hold", None):
        ws.append(flat("S3x", (3,), float(p["s3exit3"]), prio=0, dur=0))
    if p.get("s4exit", "hold") != "hold" and p.get("k4") is not None:
        ws.append(flat("S4x", (1, 2, 3), float(p["s4exit"]), syms=p.get("s4syms", ("HAVEN", "REIN")), prio=0, dur=0))
    for (rr, tt) in p.get("extra_flats", []):
        ws.append(flat("Xx", tuple(rr), float(tt), prio=0, dur=0))
    if p.get("h1"):
        d = {s: 1 for s in H_LONG}
        d.update({s: -1 for s in H_SHORT})
        ws.append(flat("H1x", (1, 2, 3), p.get("h1exit", 72), prio=0, dur=0))
        t_in = p.get("h1_t", 60)
        ws.append(W("H1", (1, 2, 3), t_in, t_in, mode="fixed", dir=d, prio=2))
    s3t0 = p.get("h1exit", 72) + 2 if p.get("h1") else 60
    ws.append(W("S1", r12, 740, 768, k=p["k1"], prio=1))
    ws.append(W("S1", (3,), 740, 778, k=p["k1"], prio=1))
    ws.append(W("S2", (1, 2, 3), 570, 598, k=p["k2"], prio=2))
    kw3 = {k: p[k] for k in ("xk", "zfull", "exit") if k in p}
    ws.append(W("S3", r12, s3t0, p.get("s3_t1", 540), k=p["k3_12"], prio=3, **kw3))
    ws.append(W("S3", (3,), s3t0, p.get("s3_t1", 540), k=p["k3_3"], prio=3, **kw3))
    if p.get("k4") is not None:
        ws.append(W("S4", (1, 2, 3), 0, 58, k=p["k4"], syms=p.get("s4syms", ("HAVEN", "REIN")), prio=4))
    return ws


# ---------------------------------------------------------------------------------------------- recommended configs
def recommended(h):
    """Train-selected (seasons 1-400) combined strategy S6R for half-spread h (fits/backtest.json)."""
    table = {0.001: (0.0, 0.1), 0.0025: (0.1, 0.2), 0.005: (0.1, 0.5), 0.01: (0.2, 0.5)}
    hk = min(table, key=lambda x: abs(x - h))
    k12, k3 = table[hk]
    p = {"k1": 0.0, "s1exit12": "hold", "k2": 0.0, "s2exit": "hold", "s2exit_rounds": (1, 2),
         "k3_12": k12, "k3_3": k3, "s3exit12": "hold", "s3exit3": "hold"}
    if hk < 0.01:
        p.update({"k4": None, "h1": True, "h1_t": 56})
    else:
        p.update({"k4": 0.05, "s4syms": tuple(SYMBOLS), "s4exit": "hold"})
    return p


SINGLE = {
    "S1": lambda h: S1(0.25 if h >= 0.0025 else 0.1, "flat"),
    "S1hold": lambda h: S1(0.0, "hold"),
    "S2": lambda h: S2(0.25 if h >= 0.0025 else 0.1, "600"),
    "S2hold": lambda h: S2(0.0, "hold"),
    "S3": lambda h: S3(0.2 if h >= 0.01 else 0.1, 540, "hold", "hold"),
    "S4": lambda h: S4(0.05, "72" if h < 0.01 else "hold", tuple(SYMBOLS)),
    "S4ins": lambda h: S4(0.0, "hold", ("HAVEN", "REIN")),
    "H1": lambda h: H1(72, 56),
    "H2": lambda h: H2(),
    "S6R": lambda h: S6(recommended(h)),
}

if __name__ == "__main__":
    # python3 -I backtest.py <SP> <holdout|loso> <s0> <s1> <h> <S1|S1hold|S2|S2hold|S3|S4|S4ins|H1|H2|S6R> [cap] [lat_extra] [nproc]
    a = sys.argv[1:]
    sp, lib, s0, s1, h, strat = a[0], a[1], int(a[2]), int(a[3]), float(a[4]), a[5]
    capv = float(a[6]) if len(a) > 6 and a[6] != "none" else None
    lat = int(a[7]) if len(a) > 7 else 0
    nproc = int(a[8]) if len(a) > 8 else 15
    kw = {"lat_extra": lat}
    if capv:
        kw["cap"] = capv
    c = cfg(strat, h, SINGLE[strat](h), **kw)
    res = run(sp, lib, list(range(s0, s1 + 1)), [c], nproc)[0]
    s = summarize(res)
    print(HDR)
    print(f"{strat:6s}", fmt(s))
    print("by symbol:", {k: round(v) for k, v in s["sym"].items()})
    print("by component (trade-level PnL to settlement):", {k: round(v[0]) for k, v in s["win"].items()})
