"""Re-backtest simulator with the MEASURED testnet cost model (stdlib only).

Library module; the runner scripts (r01-r05) import it after inserting this folder into sys.path.
It reuses the team backtest's data layer (work/se/backtest/backtest.py: Season = market.csv consensus rows,
engine fair-value cache, decision times) and reproduces red-team rt_bt.sim2 exactly in cost mode "rt".

Timing (unchanged from rt_bt.sim2): a decision at real second t sees consensus row floor(t/2) and the engine
value after all messages <= t; the order fills on consensus row j = floor(t/2) + 1 + lat (+ lat_rep inside the
report windows 570-600 / 740-780).

Cost mode "meas" (fill price = consensus_j * (1 + side * cost_bp / 1e4)):
  1. Book walk: cm[sym][phase]['table'] (mean of market buy/sell walking the visible testnet book, bp of the
     touch mid, at x = shares / L), made side-specific by the buy/sell ratio at the measured clip sizes
     (10 sh, L/8, L/4, L/2, L), piecewise-linear in x.  Several of our orders that land on the same row walk
     the same book: the increment pays the marginal walk cost (X2*c(X2) - X1*c(X1)) / (X2 - X1).
     Phase = phase of the order's arrival time t + 2*lat: open 0-3, pre 3-50, h1_entry 50-58, pre 58-60,
     early 60-72, h1_exit 72-80, s3_start 80-120, storm 120-540, after 540-568, rep_prelim 568-600,
     after 600-738, rep_final 738-760, after 760-780.
  2. Follow-on (r00_followon.out): every earlier same-side fill of ours in this symbol within fo_win (8 s)
     adds beta * (its shares / L) bp; beta = 100 bp per L for stocks, 48 for ETFs.
  3. Impact / skew (books b07 pooled, per L of net taker flow, net of consensus): I(dt) = 27.6, 29.7, 31.4,
     41.0, 35.8, 26.8 bp at dt = 0, 2, 4, 10, 20, 30 s, then exp decay with tau 47 s (cut at 150 s).
     Same-side fills older than fo_win add I(dt) * x; opposite-side fills of any age subtract I(dt) * x
     (the skewed book helps the reversing order) when imp_credit is on.
  4. Depth replenishment (repl): shares per symbol per row <= L * max(0, D(phase) - (1 - rho) * x_prev),
     D = median inner depth within 3% of mid / L for that phase, rho = share of depth-within-50bp refilled
     to 100% within 2 s (storm), x_prev = our same-side shares on the previous row / L.
  mult scales 1-3 (crowding sensitivity); phase_mult scales single phases.
Decision rule in "meas" mode: S3 enters toward +-L when |mu - P| > k*sd + thr_m*mult*(walk(clip) + 0.36*beta)*P
  (thr_fixed/x_fixed instead reproduce the playbook's literal 0.75% / 0.25%), and exits when the engine is beyond
  the phase half-spread on the other side (xstrict: stricter exit inside a window).
Other options: fo_mode "fixed" (books report: +31 bp per further same-side clip within 8 s and a 41 bp/L
  permanent shift on our position), imp_credit, side_specific, repl, rate, lat, lat_rep, path_t (position
  checkpoints), book (r11: decide on and/or fill against the recorded testnet book instead of the consensus).
plan_windows(flat12="auto") flattens rounds 1-2 over 540-568 s in clips of max(clip_s3, L/12).
"""
import bisect
import json
import math
import os
import sys
from collections import deque

B = None  # backtest module, set by init()
CM = None  # preprocessed cost model
SYMS = ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "RECON", "MAT", "GEN", "HAVEN", "REIN", "ARCH", "REBLD"]
ETFS = ("ARCH", "REBLD")
PH_LIST = ["open", "pre", "h1_entry", "early", "h1_exit", "s3_start", "storm", "after", "rep_prelim", "rep_final"]
IMP_T = [0.0, 2.0, 4.0, 10.0, 20.0, 30.0]
IMP_V = [27.6, 29.7, 31.4, 41.0, 35.8, 26.8]
IMP_TAU = 47.0


def init(sp, cost_model_path):
    global B, CM
    sys.path.insert(0, os.path.join(sp, "work/se/backtest"))
    import backtest as _B
    B = _B
    assert B.SYMBOLS == SYMS
    with open(cost_model_path) as f:
        raw = json.load(f)
    CM = prep(raw)
    return B, CM


def prep(raw):
    """Per symbol and phase: xs (sorted), mean cost, buy/sell ratio curves, inner depth, half-spread."""
    out = {}
    for s in SYMS:
        L = raw[s]["L"]
        d = {"L": L, "ph": {}, "rho": raw[s]["replenish_storm"]["depth50_100"]["within_2s"]}
        for ph in PH_LIST:
            p = raw[s][ph]
            xs = sorted(float(k) for k in p["table"])
            ys = [p["table"][k] for k in sorted(p["table"], key=float)]
            cl = p["clips"]
            pts = []
            for name in ("10sh", "L/8", "L/4", "L/2", "L"):
                c = cl[name]
                if c["mean"] and c["buy_mean"] is not None and c["sell_mean"] is not None and c["mean"] > 0:
                    pts.append((c["q"] / L, c["buy_mean"] / c["mean"], c["sell_mean"] / c["mean"]))
            pts.sort()
            d["ph"][ph] = {"xs": xs, "ys": ys, "rx": [q[0] for q in pts], "rb": [q[1] for q in pts],
                           "rs": [q[2] for q in pts], "depth": p["inner_depth_within_3pct_over_L_median"],
                           "hs": ys[0]}
        out[s] = d
    return out


def interp(xs, ys, x):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    k = bisect.bisect_right(xs, x)
    x0, x1 = xs[k - 1], xs[k]
    return ys[k - 1] + (ys[k] - ys[k - 1]) * (x - x0) / (x1 - x0)


def walk_cost(i, ph, x, side, side_specific=True):
    """Average per-share cost (bp) of a clip of x*L shares walking a fresh book, side +1 buy / -1 sell."""
    if x <= 0:
        return 0.0
    p = CM[SYMS[i]]["ph"][ph]
    c = interp(p["xs"], p["ys"], x)
    if side_specific and p["rx"]:
        r = interp(p["rx"], p["rb"] if side > 0 else p["rs"], x)
        c *= r
    return c


def phase_of(t):
    if t < 3:
        return "open"
    if t < 50:
        return "pre"
    if t < 58:
        return "h1_entry"
    if t < 60:
        return "pre"
    if t < 72:
        return "early"
    if t < 80:
        return "h1_exit"
    if t < 120:
        return "s3_start"
    if t < 540:
        return "storm"
    if t < 568:
        return "after"
    if t < 600:
        return "rep_prelim"
    if t < 738:
        return "after"
    if t < 760:
        return "rep_final"
    return "after"


def imp_kernel(dt):
    if dt <= IMP_T[-1]:
        return interp(IMP_T, IMP_V, dt)
    if dt > 150:
        return 0.0
    return IMP_V[-1] * math.exp(-(dt - IMP_T[-1]) / IMP_TAU)


def heff_rt(cfg, i, t):
    h = cfg["h"]
    if 60 <= t < 540 and cfg.get("kappa"):
        return max(h, cfg["kappa"] * cfg["_sig"][i])
    if (570 <= t < 600 or 740 <= t < 770) and cfg.get("rep_mult"):
        return h * cfg["rep_mult"]
    return h


SIG_STORM = {"PORT": 76, "ISLE": 37, "AQUA": 43, "GRID": 34, "FERRY": 53, "RECON": 28, "MAT": 18, "GEN": 20,
             "HAVEN": 129, "REIN": 12, "ARCH": 50, "REBLD": 24}


def sim(season, cfg):
    """One season, one config.  Returns pnl, per-round, per-symbol, per-(window, symbol) PnL to settlement and
    cost paid ($, vs consensus), action counts and replenishment-constraint statistics."""
    mode = cfg.get("cost", "rt")
    gamma = cfg.get("gamma", 0.0)
    lat = cfg.get("lat", 0)
    lat_rep = cfg.get("lat_rep", 0)
    excl = cfg.get("excl", {})
    windows = cfg["windows"]
    rate = cfg.get("rate", 180)
    mult = cfg.get("mult", 1.0)
    side_sp = cfg.get("side_specific", True)
    beta_s = cfg.get("beta_stock", 100.0)
    beta_e = cfg.get("beta_etf", 48.0)
    fo_win = cfg.get("fo_win", 8.0)
    use_imp = cfg.get("imp", True)
    credit = cfg.get("imp_credit", True)
    repl = cfg.get("repl", True)
    thr_m = cfg.get("thr_m", 1.0)
    fo_mode = cfg.get("fo_mode", "kernel")   # "kernel" (r00 calibration) or "fixed" (books report: +31 bp/clip)
    fo_fixed = cfg.get("fo_fixed", 31.0)
    imp_perm = cfg.get("imp_perm", 41.0)     # fixed mode: permanent shift, bp per L of our signed position
    fo_thr = cfg.get("fo_thr", 0.36)   # expected follow-on per share of a full build, in units of beta
    cfg["_sig"] = [SIG_STORM[s] * 1e-4 for s in SYMS]
    bk = cfg.get("book")   # optional recorded-book provider (r11): decide on the book mid, fill by walking it
    NS, LIM, NROWS = B.NS, B.LIM, B.NROWS
    pos = [0] * NS
    cash = [0.0] * NS
    acts = deque()
    n_act = 0
    max60 = 0
    by = {}            # (wname, i) -> [pnl_to_settle, cost_$, shares]
    round_pnl = {}
    prev_val = 0.0
    repl_hits = 0
    repl_lost = 0
    carry = 0.0   # sum over rounds 1-2 of sum_s |pos_s|/L_s left at the round close (0 = flattened)
    path = {}     # optional: positions at the checkpoint times cfg['path_t'] (before that decision)
    for r in (1, 2, 3):
        rows = season.rows[r]
        acts.clear()
        filled = {}        # (i, j) -> shares on that row (any side) for the clip cap
        filled_side = {}   # (i, j, side) -> shares
        hist = [[] for _ in range(NS)]   # our fills this round: (fill_time, side, x)
        wins = [w for w in windows if r in w["rounds"]]
        extra = sorted({w["t0"] for w in wins})
        dts = sorted(set(season.dec[r]) | set(float(x) for x in extra if 0 <= x < 780))
        cps = list(cfg.get("path_t", ()))
        for t in dts:
            while cps and cps[0] <= t:
                path.setdefault(r, {})[cps.pop(0)] = list(pos)
            act_w = [w for w in wins if w["t0"] <= t <= w["t1"]]
            if not act_w:
                continue
            ref = min(int(t // 2), NROWS - 1)
            P = rows[ref]
            if bk is not None:
                if not bk.has(r):
                    continue
                if getattr(bk, "decide", True):
                    P = [bk.mid(r, i, t) for i in range(NS)]
            mu, sd = season.fair_at(r, t)
            ph = phase_of(t + 2.0 * lat)
            orders = []
            done = set()
            for w in act_w:
                wmode = w["mode"]
                for s in w["syms"]:
                    i = B.SI[s]
                    if i in done:
                        continue
                    done.add(i)
                    if s in excl.get(w["name"], ()):
                        continue
                    L = LIM[i]
                    p = P[i]
                    if p is None:
                        continue
                    cur = pos[i]
                    if mode == "rt":
                        hh = heff_rt(cfg, i, t)
                        th_cost = hh + gamma
                        x_cost = hh
                    else:
                        clipx = w.get("clip", cfg.get("cap", 0.25))
                        beta = beta_e if s in ETFS else beta_s
                        pc = CM[s]["ph"][ph]
                        pm = mult * cfg.get("phase_mult", {}).get(ph, 1.0)
                        th_cost = thr_m * pm * (interp(pc["xs"], pc["ys"], clipx) + fo_thr * beta) * 1e-4
                        x_cost = pm * pc["hs"] * 1e-4
                        if cfg.get("thr_fixed") is not None:
                            # the playbook's literal rule: enter beyond k*sd + (h + gamma)*P, exit beyond h*P
                            th_cost, x_cost = cfg["thr_fixed"], cfg["x_fixed"]
                        hh = None
                    if wmode == "flat":
                        tgt = 0
                        edge = 0.0
                    elif wmode == "fixed":
                        tgt = w["dir"][s] * L
                        edge = 0.0
                    else:
                        e = mu[i] - p
                        th = w["k"] * sd[i] + th_cost * p
                        tgt = cur
                        if e > th:
                            if L > cur:
                                tgt = L
                        elif e < -th:
                            if -L < cur:
                                tgt = -L
                        else:
                            xb = w.get("xk", 0.0) * sd[i] + x_cost * p
                            if w.get("xstrict") is not None:
                                # strict window: keep a position only while the edge in its favour exceeds
                                # xstrict * entry threshold (0 = exit once the engine is on the other side of P)
                                xb = -w["xstrict"] * th
                            if cur > 0 and e < -xb:
                                tgt = 0
                            elif cur < 0 and e > xb:
                                tgt = 0
                        edge = abs(e)
                    q = tgt - cur
                    if q == 0:
                        continue
                    orders.append((w.get("prio", 5), -edge * abs(q), i, q, w["name"], hh, w.get("clip")))
            if not orders:
                continue
            orders.sort()
            while acts and acts[0] <= t - 60.0:
                acts.popleft()
            rep = 570 <= t < 600 or 740 <= t < 780
            j = int(t // 2) + 1 + lat + (lat_rep if rep else 0)
            if j >= NROWS:
                continue
            tf = 2.0 * j
            for prio, _, i, q, wname, hh, wclip in orders:
                if len(acts) >= rate:
                    continue
                L = LIM[i]
                capx = wclip if wclip is not None else cfg.get("cap")
                if capx:
                    room = int(capx * L) - filled.get((i, j), 0)
                    if room <= 0:
                        continue
                    if abs(q) > room:
                        q = room if q > 0 else -room
                side = 1 if q > 0 else -1
                if mode != "rt" and repl:
                    pc = CM[SYMS[i]]["ph"][ph]
                    xprev = filled_side.get((i, j - 1, side), 0) / L
                    xmax = max(0.0, pc["depth"] - (1.0 - CM[SYMS[i]]["rho"]) * xprev)
                    room2 = int(xmax * L) - filled_side.get((i, j, side), 0)
                    if room2 < abs(q):
                        repl_hits += 1
                        repl_lost += abs(q) - max(0, room2)
                        if room2 <= 0:
                            continue
                        q = room2 * side
                if bk is not None and getattr(bk, "fill", True):
                    tb = t + bk.lat + 2.0 * lat
                    n1s = filled_side.get((i, j, side), 0)
                    w2 = bk.walk(r, i, tb, side, n1s + abs(q))
                    if w2 is None:
                        continue
                    w1 = bk.walk(r, i, tb, side, n1s) if n1s > 0 else (0, 0.0)
                    vinc = (w2[0] * w2[1] - w1[0] * w1[1]) / max(1, w2[0] - w1[0])
                    mid_b = bk.mid(r, i, tb)
                acts.append(t)
                n_act += 1
                if mode == "rt":
                    cost = hh + gamma * abs(q) / L
                else:
                    X1 = filled_side.get((i, j, side), 0) / L
                    X2 = X1 + abs(q) / L
                    c2 = walk_cost(i, ph, X2, side, side_sp)
                    c1 = walk_cost(i, ph, X1, side, side_sp) if X1 > 0 else 0.0
                    cw = (X2 * c2 - X1 * c1) / (X2 - X1)
                    beta = beta_e if SYMS[i] in ETFS else beta_s
                    fo = 0.0
                    im = 0.0
                    if fo_mode == "fixed":
                        # books-report recommendation: + fo_fixed bp if any same-side fill of ours within fo_win
                        if any(sk == side and 0 < tf - tk <= fo_win for (tk, sk, xk) in hist[i]):
                            fo = fo_fixed
                        if use_imp:
                            im = imp_perm * side * pos[i] / L
                    else:
                        for (tk, sk, xk) in hist[i]:
                            dt = tf - tk
                            if dt <= 0 or dt > 150:
                                continue
                            if sk == side:
                                if dt <= fo_win:
                                    fo += beta * xk
                                elif use_imp:
                                    im += imp_kernel(dt) * xk
                            elif use_imp and credit:
                                im -= imp_kernel(dt) * xk
                    cost = mult * cfg.get("phase_mult", {}).get(ph, 1.0) * (cw + fo + im) * 1e-4
                    hist[i].append((tf, side, abs(q) / L))
                px = rows[j][i] * (1.0 + cost * side)
                if bk is not None and getattr(bk, "fill", True):
                    # recorded book: actual walk VWAP (same-row increments walk deeper) + our follow-on/impact
                    px = vinc + side * mid_b * mult * (fo + im) * 1e-4
                    cost = side * (px - mid_b) / mid_b
                filled[(i, j)] = filled.get((i, j), 0) + abs(q)
                filled_side[(i, j, side)] = filled_side.get((i, j, side), 0) + abs(q)
                cash[i] -= q * px
                pos[i] += q
                b = by.setdefault((wname, i), [0.0, 0.0, 0])
                b[0] += q * (season.settle[i] - px)
                b[1] += abs(q) * rows[j][i] * cost
                b[2] += abs(q)
            max60 = max(max60, len(acts))
        mark = rows[NROWS - 1] if r < 3 else season.settle
        val = sum(cash) + sum(pos[i] * mark[i] for i in range(NS))
        round_pnl[r] = val - prev_val
        prev_val = val
        if r < 3:
            carry += sum(abs(pos[i]) / LIM[i] for i in range(NS))
    pnl = sum(cash) + sum(pos[i] * season.settle[i] for i in range(NS))
    return {"pnl": pnl, "round": [round_pnl[1], round_pnl[2], round_pnl[3]], "carry": carry, "path": path,
            "sym": [cash[i] + pos[i] * season.settle[i] for i in range(NS)], "act": n_act, "max60": max60,
            "by": {f"{k[0]}|{SYMS[k[1]]}": v for k, v in by.items()}, "repl_hits": repl_hits, "repl_lost": repl_lost}


# ------------------------------------------------------------------------------------------- strategy builders
H_LONG = ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "HAVEN", "REIN", "ARCH"]
H_SHORT = ["RECON", "MAT", "GEN", "REBLD"]


def plan_windows(h1_size=0.5, h1_syms=None, h1_in=(50, 56), h1_out=(72, 78), s3_t0=80.0, k12=0.1, k3=0.2, k1=0.0,
                 flat12=(562, 568), s1r3=True, clip_h1=None, clip_s3=None, s3_syms=None, s3_t1=540.0):
    """The playbook Plan (dp_bt3 REC-F) with every knob exposed.  Window order matches dp_bt.s6w + dp_bt3.w:
    H1x, H1, S1(r3), S3(r1-2), S3(r3), S3x.  h1_out None = no forced H1 exit (S3 inherits the H1 book)."""
    ws = []
    d = {s: 1 for s in H_LONG}
    d.update({s: -1 for s in H_SHORT})
    if h1_size:
        hs = list(h1_syms) if h1_syms is not None else list(SYMS)
        dd = {s: d[s] * h1_size for s in hs}
        if h1_out:
            ws.append(B.W("H1x", (1, 2, 3), float(h1_out[0]), float(h1_out[1]), mode="flat", syms=hs, prio=0,
                          **({"clip": clip_h1} if clip_h1 else {})))
        ws.append(B.W("H1", (1, 2, 3), float(h1_in[0]), float(h1_in[1]), mode="fixed", dir=dd, syms=hs, prio=2,
                      **({"clip": clip_h1} if clip_h1 else {})))
    kw = {"clip": clip_s3} if clip_s3 else {}
    kwf = dict(kw)
    if flat12 == "auto":
        # flatten over 540-568 s (storm over, cheapest 'after' book) in clips of max(clip_s3, L/12): 15 rows
        # for at most 12 clips, so the book is flat before the preliminary report even when rate-limited
        flat12 = (540, 568)
        kwf = {"clip": max(clip_s3 or 0.25, 1.0 / 12)}
    if s1r3:
        ws.append(B.W("S1", (3,), 740.0, 778.0, k=k1, prio=1, **kw))
    s3s = list(s3_syms) if s3_syms is not None else list(SYMS)
    ws.append(B.W("S3", (1, 2), float(s3_t0), float(s3_t1), k=k12, prio=3, syms=s3s, **kw))
    ws.append(B.W("S3", (3,), float(s3_t0), float(s3_t1), k=k3, prio=3, syms=s3s, **kw))
    if flat12:
        ws.append(B.W("S3x", (1, 2), float(flat12[0]), float(flat12[1]), mode="flat", prio=0, **kwf))
    return ws


NOREIN = {w: ("REIN",) for w in ("S1", "S2", "S3", "H1")}


# ------------------------------------------------------------------------------------------- statistics
def pct(v, q):
    v = sorted(v)
    x = q * (len(v) - 1)
    lo = int(math.floor(x))
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (x - lo)


def summarize(res):
    p = [x["pnl"] for x in res]
    n = len(p)
    m = sum(p) / n
    sd = math.sqrt(sum((q - m) ** 2 for q in p) / max(1, n - 1))
    out = {"n": n, "mean": m, "sd": sd, "se": sd / math.sqrt(n), "p5": pct(p, 0.05), "p50": pct(p, 0.5),
           "hit": sum(q > 0 for q in p) / n,
           "round": [sum(x["round"][k] for x in res) / n for k in range(3)],
           "sym": {SYMS[i]: sum(x["sym"][i] for x in res) / n for i in range(len(SYMS))},
           "act": sum(x["act"] for x in res) / n, "max60": max(x["max60"] for x in res),
           "repl_hits": sum(x["repl_hits"] for x in res) / n, "repl_lost": sum(x["repl_lost"] for x in res) / n,
           "carry": sum(x.get("carry", 0.0) for x in res) / n}
    by = {}
    for x in res:
        for k, v in x["by"].items():
            a = by.setdefault(k, [0.0, 0.0, 0.0])
            a[0] += v[0] / n
            a[1] += v[1] / n
            a[2] += v[2] / n
    out["by"] = by
    comp = {}
    for k, v in by.items():
        w = k.split("|")[0]
        a = comp.setdefault(w, [0.0, 0.0, 0.0])
        for z in range(3):
            a[z] += v[z]
    out["comp"] = comp
    return out


# ------------------------------------------------------------------------------------------- parallel driver
G = {}


def _init(sp, lib, cfgs):
    G["sp"], G["lib"], G["cfgs"] = sp, lib, cfgs


def _work(n):
    s = B.Season(G["sp"], n, G["lib"])
    return n, [sim(s, c) for c in G["cfgs"]]


def run(sp, lib, seasons, cfgs, nproc=14):
    import multiprocessing as mp
    with mp.get_context("fork").Pool(nproc, initializer=_init, initargs=(sp, lib, cfgs)) as pool:
        out = dict(pool.imap_unordered(_work, seasons, chunksize=2))
    return [[out[n][k] for n in seasons] for k in range(len(cfgs))]


def row(name, s):
    c = s["comp"]
    cs = ", ".join(f"{a} {b[0]:,.0f}" for a, b in sorted(c.items()))
    cost = sum(b[1] for b in c.values())
    return (f"| {name} | {s['mean']:,.0f} | {s['sd']:,.0f} | {s['se']:,.0f} | {s['p5']:,.0f} | {s['hit']:.2f} | "
            f"{s['round'][0]:,.0f} | {s['round'][1]:,.0f} | {s['round'][2]:,.0f} | {cost:,.0f} | {s['act']:.0f} | "
            f"{s['max60']} | {s['repl_hits']:.1f} | {cs} |")


HDR = ("| variant | mean | sd | se | p5 | hit | R1 | R2 | R3 | cost paid | acts | max60 | repl hits | components (PnL to settle) |\n"
       "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
