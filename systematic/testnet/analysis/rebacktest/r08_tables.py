"""Report tables derived from the run outputs (no new simulation except the testnet season check):
  A. depth-replenishment cap (max shares per symbol per 2 s) by phase, fresh and after one of our clips;
  B. measured per-share cost of the clip sizes used (walk only) vs the RT model;
  C. per-symbol cost share of gross edge, REOPT and Plan, holdout and train; drop flags;
  D. per-strategy marginal values;
  E. REOPT and Plan on the testnet seasons 224 and 225 (consensus replay; both are train seasons).
usage: python3 -I r08_tables.py <SP> <cost_model.json> <rebacktest_dir>"""
import json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import r_sim as R  # noqa: E402

SP, CMP, D = sys.argv[1], sys.argv[2], sys.argv[3]
B, CM = R.init(SP, CMP)
raw = json.load(open(CMP))
print("## A. depth-replenishment cap: shares per symbol per 2-s row = L*max(0, D - (1-rho)*x_prev)")
print("| sym | L | rho (2 s) | storm fresh | storm after L/4 | s3_start fresh | s3_start after L/4 | h1_exit fresh | L/12 clip | L/4 clip |")
print("|---|---|---|---|---|---|---|---|---|---|")
for s in R.SYMS:
    c = CM[s]; L = c["L"]; rho = c["rho"]
    f = lambda ph, xp: int(L * max(0.0, c["ph"][ph]["depth"] - (1 - rho) * xp))
    print(f"| {s} | {L} | {rho:.2f} | {f('storm', 0)} | {f('storm', .25)} | {f('s3_start', 0)} | {f('s3_start', .25)} | "
          f"{f('h1_exit', 0)} | {int(L / 12)} | {int(L / 4)} |")
print()
print("## B. walk cost per share (bp of mid, side-average) at L/12, L/8, L/4 by phase; RT model = 25 + 50x")
print("| sym | storm L/12 | storm L/8 | storm L/4 | s3_start L/12 | s3_start L/4 | h1_entry L/8 buy/sell | h1_exit L/8 buy/sell | after L/12 |")
print("|---|---|---|---|---|---|---|---|---|")
for i, s in enumerate(R.SYMS):
    w = lambda ph, x, sd=0: (R.walk_cost(i, ph, x, sd, True) if sd else R.interp(CM[s]["ph"][ph]["xs"], CM[s]["ph"][ph]["ys"], x))
    print(f"| {s} | {w('storm', 1/12):.0f} | {w('storm', .125):.0f} | {w('storm', .25):.0f} | {w('s3_start', 1/12):.0f} | "
          f"{w('s3_start', .25):.0f} | {w('h1_entry', .125, 1):.0f}/{w('h1_entry', .125, -1):.0f} | "
          f"{w('h1_exit', .125, 1):.0f}/{w('h1_exit', .125, -1):.0f} | {w('after', 1/12):.0f} |")
print(f"RT model at L/12 / L/8 / L/4: {25 + 50 / 12:.1f} / {25 + 50 / 8:.1f} / {25 + 50 / 4:.1f} bp")
print()
for split in ("holdout", "train"):
    f6 = json.load(open(os.path.join(D, f"r06_final_{split}.json")))
    f2 = json.load(open(os.path.join(D, f"r02_plan_{split}.json")))
    print(f"## C. per-symbol PnL and cost share of gross edge ({split}); gross = PnL + cost paid")
    print("| sym | Plan PnL | Plan cost/gross | REOPT PnL | REOPT cost/gross | REOPT x1.5 PnL | REOPT x2 PnL | REOPT +2 s PnL | flag |")
    print("|---|---|---|---|---|---|---|---|---|")
    pl, ro = f2["MEAS full model (base)"], f6["REOPT"]
    r15, r2, rl = f6["REOPT costs x1.5"], f6["REOPT costs x2"], f6["REOPT +2 s latency"]

    def cost(s, sym):
        return sum(v[1] for k, v in s["by"].items() if k.endswith("|" + sym))
    for sym in R.SYMS:
        if sym == "REIN":
            continue
        a, ca = pl["sym"][sym], cost(pl, sym)
        b, cb = ro["sym"][sym], cost(ro, sym)
        flag = []
        if a <= 0:
            flag.append("drop in Plan")
        if r2["sym"][sym] <= 0:
            flag.append("loses at x2")
        if cb / (b + cb) > 0.5:
            flag.append("costs > 1/2 edge")
        print(f"| {sym} | {a:,.0f} | {ca / (a + ca):.0%} | {b:,.0f} | {cb / (b + cb):.0%} | {r15['sym'][sym]:,.0f} | "
              f"{r2['sym'][sym]:,.0f} | {rl['sym'][sym]:,.0f} | {', '.join(flag)} |")
    print()
    m = {k: v["mean"] for k, v in f6.items()}
    p = {k: v["mean"] for k, v in f2.items()}
    print(f"## D. marginal values ({split}, $/season): value = config with - config without")
    rows = [
        ("Plan: H1 (1/2 size, exit 72-78)", p["MEAS full model (base)"] - p["MEAS Plan without H1"]),
        ("Plan: H1 full instead of 1/2", p["MEAS Plan with full H1"] - p["MEAS full model (base)"]),
        ("Plan: S1-r3 taker", p["MEAS full model (base)"] - p["MEAS Plan without S1-r3"]),
        ("Plan: ETFs (ARCH, REBLD)", p["MEAS full model (base)"] - p["MEAS Plan without ETFs (and REIN)"]),
        ("REOPT: H1 (full, hand-off)", m["REOPT"] - m["REOPT without H1"]),
        ("REOPT: H1 full instead of 1/2", m["REOPT"] - m["REOPT with H1 1/2 size"]),
        ("REOPT: hand-off instead of forced exit 72-86", m["REOPT"] - m["REOPT with forced H1 exit 72-86"]),
        ("REOPT: S3 (vs H1 alone with exit 80-94)", m["REOPT"] - m["REOPT H1 only (no S3; exit 80-94, flat)"]),
        ("REOPT: S1-r3 taker", m["REOPT"] - m["REOPT without S1-r3"]),
        ("REOPT: ETFs", m["REOPT"] - m["REOPT without ETFs"]),
        ("REOPT: rebuilders RECON/MAT/GEN/REBLD", m["REOPT"] - m["REOPT without rebuilders (RECON MAT GEN REBLD)"]),
        ("REOPT: MAT", m["REOPT"] - m["REOPT without MAT"]),
        ("REOPT x2: H1", m["REOPT costs x2"] - m["REOPT costs x2, no H1"]),
    ]
    for n, v in rows:
        print(f"| {n} | {v:,.0f} |")
    print()

print("## E. testnet seasons on the consensus (both in train): REOPT and Plan, $ by round")
S3 = dict(s3_t0=80, k12=0.1, k3=0.2, clip_s3=1 / 12, flat12="auto")
H1 = dict(h1_size=1.0, h1_in=(42, 56), h1_out=None, clip_h1=0.125)
me = dict(cost="meas", lat_rep=1, excl=R.NOREIN, rate=150, cap=1 / 12, thr_m=2.0)
plan = dict(cost="meas", cap=0.25, lat_rep=1, excl=R.NOREIN)
cf = [dict(plan, name="Plan (measured)", windows=R.plan_windows()),
      dict(me, name="REOPT", windows=R.plan_windows(**H1, **S3)),
      dict(me, name="REOPT without H1", windows=R.plan_windows(h1_size=0, **S3)),
      dict(plan, name="Plan (RT model)", windows=R.plan_windows(), cost="rt", h=0.0025, gamma=0.005)]
for n in (224, 225):
    se = B.Season(SP, n, "loso")
    for c in cf:
        x = R.sim(se, c)
        print(f"season {n} {c['name']:18s}: total {x['pnl']:8,.0f}  r1 {x['round'][0]:8,.0f}  r2 {x['round'][1]:8,.0f}  r3 {x['round'][2]:8,.0f}")
