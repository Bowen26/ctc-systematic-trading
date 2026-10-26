"""Red-team re-run of the backtest with harsher, more realistic fill assumptions (stdlib only).
Imports the team's backtest.py for data loading and strategy windows; replaces simulate() with sim2(), which adds
  * phase/symbol-dependent half-spread: h_eff = max(h, kappa * sigma2s[sym][phase]) during the storm (60-540 s) and
    h_eff = h * rep_mult inside 570-600 and 740-770 (house widens when risk rises),
  * linear depth impact: extra cost gamma * |q| / limit (fraction of price) per share (walking the book),
  * latency in rows for all orders (lat) and separately for report-window orders only (lat_rep),
  * optional symbol exclusions per window name (e.g. no ETFs in S3).
Usage: python3 -I rt_bt.py <SP> <holdout|loso> <s0> <s1> <variant-set> [nproc]
"""
import json, math, os, sys
from collections import deque
import multiprocessing as mp
SP = sys.argv[1]
sys.path.insert(0, os.path.join(SP, "work/se/backtest"))
import backtest as B  # noqa: E402

# 2-s RMS change of the house consensus during the storm (rows 34-269), bp, from rt_house.out section B
SIG_STORM = {"PORT": 76, "ISLE": 37, "AQUA": 43, "GRID": 34, "FERRY": 53, "RECON": 28, "MAT": 18, "GEN": 20,
             "HAVEN": 129, "REIN": 12, "ARCH": 50, "REBLD": 24}
SIG = [SIG_STORM[s] * 1e-4 for s in B.SYMBOLS]


def heff(cfg, i, t):
    h = cfg["h"]
    if 60 <= t < 540 and cfg.get("kappa"):
        return max(h, cfg["kappa"] * SIG[i])
    if (570 <= t < 600 or 740 <= t < 770) and cfg.get("rep_mult"):
        return h * cfg["rep_mult"]
    return h


def sim2(season, cfg):
    gamma = cfg.get("gamma", 0.0)
    lat = cfg.get("lat", 0)
    lat_rep = cfg.get("lat_rep", 0)
    excl = cfg.get("excl", {})
    windows = cfg["windows"]
    NS, LIM, NROWS = B.NS, B.LIM, B.NROWS
    pos = [0] * NS; cash = [0.0] * NS
    acts = deque(); n_act = 0; max60 = 0
    by_win = {}; round_pnl = {}; prev_val = 0.0
    for r in (1, 2, 3):
        rows = season.rows[r]
        acts.clear()
        filled = {}
        wins = [w for w in windows if r in w["rounds"]]
        extra = sorted({w["t0"] for w in wins})
        dts = sorted(set(season.dec[r]) | set(float(x) for x in extra if 0 <= x < 780))
        for t in dts:
            act_w = [w for w in wins if w["t0"] <= t <= w["t1"]]
            if not act_w:
                continue
            ref = min(int(t // 2), NROWS - 1)
            P = rows[ref]
            mu, sd = season.fair_at(r, t)
            orders = []; done = set()
            for w in act_w:
                mode = w["mode"]
                for s in w["syms"]:
                    i = B.SI[s]
                    if i in done:
                        continue
                    done.add(i)
                    if s in excl.get(w["name"], ()):
                        continue
                    L = LIM[i]; p = P[i]; cur = pos[i]
                    hh = heff(cfg, i, t)
                    if mode == "flat":
                        tgt = 0; edge = 0.0
                    elif mode == "fixed":
                        tgt = w["dir"][s] * L; edge = 0.0
                    else:
                        e = mu[i] - p
                        th = w["k"] * sd[i] + hh * p + gamma * p
                        tgt = cur
                        if e > th:
                            if L > cur:
                                tgt = L
                        elif e < -th:
                            if -L < cur:
                                tgt = -L
                        else:
                            xb = w.get("xk", 0.0) * sd[i] + hh * p
                            if cur > 0 and e < -xb:
                                tgt = 0
                            elif cur < 0 and e > xb:
                                tgt = 0
                        edge = abs(e)
                    q = tgt - cur
                    if q == 0:
                        continue
                    orders.append((w.get("prio", 5), -edge * abs(q), i, q, w["name"], hh))
            if not orders:
                continue
            orders.sort()
            while acts and acts[0] <= t - 60.0:
                acts.popleft()
            rep = 570 <= t < 600 or 740 <= t < 780
            j = int(t // 2) + 1 + lat + (lat_rep if rep else 0)
            if j >= NROWS:
                continue
            for prio, _, i, q, wname, hh in orders:
                if len(acts) >= cfg.get("rate", 180):
                    continue
                if cfg.get("cap"):
                    room = int(cfg["cap"] * LIM[i]) - filled.get((i, j), 0)
                    if room <= 0:
                        continue
                    if abs(q) > room:
                        q = room if q > 0 else -room
                filled[(i, j)] = filled.get((i, j), 0) + abs(q)
                acts.append(t); n_act += 1
                cost = hh + gamma * abs(q) / LIM[i]
                px = rows[j][i] * (1.0 + cost if q > 0 else 1.0 - cost)
                cash[i] -= q * px; pos[i] += q
                bw = by_win.setdefault(wname, [0.0, 0])
                bw[0] += q * (season.settle[i] - px); bw[1] += 1
            max60 = max(max60, len(acts))
        mark = rows[NROWS - 1] if r < 3 else season.settle
        val = sum(cash) + sum(pos[i] * mark[i] for i in range(NS))
        round_pnl[r] = val - prev_val; prev_val = val
    pnl = sum(cash) + sum(pos[i] * season.settle[i] for i in range(NS))
    return {"pnl": pnl, "round": [round_pnl[1], round_pnl[2], round_pnl[3]],
            "sym": [cash[i] + pos[i] * season.settle[i] for i in range(NS)], "act": n_act, "max60": max60, "win": by_win}


def S6R(h, drop=()):
    p = B.recommended(h)
    ws = B.S6(p)
    return [w for w in ws if w["name"] not in drop]


def clock_only(t_in=56, t_flip=74, t_out=538):
    """No engine: long islands/HAVEN/REIN/ARCH, short rebuilders/REBLD at t_in; flip to the opposite (drift) side at
    t_flip (short islands/HAVEN/ARCH, long rebuilders/REBLD; REIN flat); flat at t_out. Every round."""
    d1 = {s: 1 for s in B.H_LONG}; d1.update({s: -1 for s in B.H_SHORT})
    d2 = {s: -1 for s in B.ISLAND + ["HAVEN", "ARCH"]}; d2.update({s: 1 for s in B.REBUILD + ["REBLD"]}); d2["REIN"] = 0
    return [B.flat("Cx", (1, 2, 3), t_out, prio=0, dur=0),
            B.W("C1", (1, 2, 3), t_in, t_in, mode="fixed", dir=d1, prio=2),
            B.W("C2", (1, 2, 3), t_flip, t_flip, mode="fixed", dir=d2, prio=2)]


def variants(name, h):
    V = []
    s6 = S6R(h)
    if name == "depth":
        for cap in (None, 0.25):
            for g in (0.0, 0.0025, 0.005, 0.01):
                V.append((f"S6R cap {cap} gamma {g * 100:.2f}%", {"windows": s6, "cap": cap, "gamma": g}))
        V.append(("S6R cap 0.25 gamma .5% + report lat+1", {"windows": s6, "cap": 0.25, "gamma": 0.005, "lat_rep": 1}))
        V.append(("S6R-noREIN cap 0.25 gamma .5% + report lat+1", {"windows": s6, "cap": 0.25, "gamma": 0.005, "lat_rep": 1,
                  "excl": {"S3": ("REIN",), "H1": ("REIN",), "S1": ("REIN",), "S2": ("REIN",)}}))
    if name == "noetf":
        ex = {w: ("ARCH", "REBLD") for w in ("S1", "S2", "S3", "H1")}
        V.append(("S6R without any ETF trading", {"windows": s6, "excl": ex}))
        V.append(("S6R without ETFs, lat+1", {"windows": s6, "excl": ex, "lat": 1}))
        V.append(("S6R without ETFs, cap .25 gamma .5% rep lat+1", {"windows": s6, "excl": ex, "cap": 0.25, "gamma": 0.005, "lat_rep": 1}))
        V.append(("S6R without ETFs and REIN", {"windows": s6, "excl": {w: ("ARCH", "REBLD", "REIN") for w in ("S1", "S2", "S3", "H1")}}))
    if name == "worked":
        p = B.recommended(h)
        def s6w(t_in0=50, t_out1=78):
            ws = []
            for w in B.S6(p):
                if w["name"] == "H1":
                    w = dict(w); w["t0"] = float(t_in0); w["t1"] = 56.0
                elif w["name"] == "H1x":
                    w = dict(w); w["t1"] = float(t_out1)
                elif w["name"] == "S3":
                    w = dict(w); w["t0"] = float(t_out1 + 2)
                ws.append(w)
            return ws
        for g in (0.0, 0.0025, 0.005, 0.01):
            V.append((f"S6R, H1 worked 50-56/72-78, S3 from 80, cap .25, gamma {g * 100:.2f}%", {"windows": s6w(), "cap": 0.25, "gamma": g}))
        V.append(("same, gamma .5% + report lat+1", {"windows": s6w(), "cap": 0.25, "gamma": 0.005, "lat_rep": 1}))
        V.append(("same, gamma .5% + report lat+1 + lat+1 all", {"windows": s6w(), "cap": 0.25, "gamma": 0.005, "lat_rep": 1, "lat": 1}))
        V.append(("S6R base for reference, H1 single shot, no cap", {"windows": s6}))
        V.append(("worked, gamma .5% + report lat+1, rate 150", {"windows": s6w(), "cap": 0.25, "gamma": 0.005, "lat_rep": 1, "rate": 150}))
        V.append(("worked, gamma .5% + report lat+1, rate 120", {"windows": s6w(), "cap": 0.25, "gamma": 0.005, "lat_rep": 1, "rate": 120}))
        V.append(("worked, gamma .5% + rep lat+1, no ETFs/REIN", {"windows": s6w(), "cap": 0.25, "gamma": 0.005, "lat_rep": 1,
                  "excl": {w: ("ARCH", "REBLD", "REIN") for w in ("S1", "S2", "S3", "H1")}}))
    if name == "core":
        V += [("S6R base (repro)", {"windows": s6}),
              ("S6R lat+1 all", {"windows": s6, "lat": 1}),
              ("S6R gamma .25%", {"windows": s6, "gamma": 0.0025}),
              ("S6R gamma .5%", {"windows": s6, "gamma": 0.005}),
              ("S6R gamma 1%", {"windows": s6, "gamma": 0.01}),
              ("S6R storm h=max(h,.25 sig)", {"windows": s6, "kappa": 0.25}),
              ("S6R storm h=max(h,.5 sig)", {"windows": s6, "kappa": 0.5}),
              ("S6R report h x3", {"windows": s6, "rep_mult": 3}),
              ("S6R report lat+1 (lose race)", {"windows": s6, "lat_rep": 1}),
              ("S6R harsh: .5sig + gamma .5% + rep lat+1", {"windows": s6, "kappa": 0.5, "gamma": 0.005, "lat_rep": 1}),
              ("S6R harsh + lat+1 all", {"windows": s6, "kappa": 0.5, "gamma": 0.005, "lat_rep": 1, "lat": 1}),
              ("S6R no S1/S2 (no report races)", {"windows": S6R(h, drop=("S1", "S2"))}),
              ("S6R no ETFs in S3", {"windows": s6, "excl": {"S3": ("ARCH", "REBLD")}}),
              ("S6R no H1 formation leg", {"windows": S6R(h, drop=("H1", "H1x"))}),
              ("clock-only H1+drift (no engine)", {"windows": clock_only()}),
              ("clock-only, harsh", {"windows": clock_only(), "kappa": 0.5, "gamma": 0.005}),
              ]
    return [dict(name=n, h=h, **kw) for n, kw in V]


G = {}


def _init(lib, cfgs):
    G["lib"], G["cfgs"] = lib, cfgs


def _work(n):
    s = B.Season(SP, n, G["lib"])
    return n, [sim2(s, c) for c in G["cfgs"]]


if __name__ == "__main__":
    lib, s0, s1, vset = sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
    nproc = int(sys.argv[6]) if len(sys.argv) > 6 else 14
    hs = [float(x) for x in (sys.argv[7].split(",") if len(sys.argv) > 7 else ["0.0025"])]
    seasons = list(range(s0, s1 + 1))
    for h in hs:
        cfgs = variants(vset, h)
        with mp.get_context("fork").Pool(nproc, initializer=_init, initargs=(lib, cfgs)) as pool:
            out = dict(pool.imap_unordered(_work, seasons, chunksize=2))
        print(f"### {lib} seasons {s0}-{s1}, h = {h}")
        print("| variant | mean | sd | t | p5 | hit | R1 | R2 | R3 | acts | max60 | components |")
        print("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for k, c in enumerate(cfgs):
            res = [out[n][k] for n in seasons]
            s = B.summarize([{"pnl": x["pnl"], "round": x["round"]} for x in res])
            comp = {}
            for x in res:
                for wn, v in x["win"].items():
                    comp[wn] = comp.get(wn, 0.0) + v[0] / len(res)
            acts = sum(x["act"] for x in res) / len(res); mx = max(x["max60"] for x in res)
            cs = ", ".join(f"{a} {b:,.0f}" for a, b in sorted(comp.items()))
            print(f"| {c['name']} | {s['mean']:,.0f} | {s['sd']:,.0f} | {s['t']:.1f} | {s['p5']:,.0f} | {s['hit']:.2f} | "
                  f"{s['round'][0]:,.0f} | {s['round'][1]:,.0f} | {s['round'][2]:,.0f} | {acts:.0f} | {mx} | {cs} |", flush=True)
