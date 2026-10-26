"""Task 1, literal form: the Plan with its own decision rule unchanged (S3 enters beyond k*sd + 0.75%*P and exits
beyond 0.25%*P, as in dp_bt3 with h 0.25% / gamma 0.5%), only the FILLS re-priced with the measured cost model.
(r02_plan.py instead lets the Plan's threshold use the measured phase cost, thr_m = 1.)
usage: python3 -I r02b_plan_literal.py <SP> <cost_model.json> <holdout|loso> <s0> <s1> <out.json>"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import r_sim as R  # noqa: E402

SP, CMP, lib, s0, s1, OUT = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), sys.argv[6]
B, CM = R.init(SP, CMP)
me = dict(cost="meas", cap=0.25, lat_rep=1, excl=R.NOREIN, thr_fixed=0.0075, x_fixed=0.0025)
P = R.plan_windows()
cfgs = [
    dict(me, name="Plan literal, measured fills (base)", windows=P),
    dict(me, name="Plan literal, no replenishment cap", windows=P, repl=False),
    dict(me, name="Plan literal, full H1", windows=R.plan_windows(h1_size=1.0)),
    dict(me, name="Plan literal, no H1", windows=R.plan_windows(h1_size=0)),
    dict(me, name="Plan literal, no S1-r3", windows=R.plan_windows(s1r3=False)),
    dict(me, name="Plan literal, no ETFs", windows=P, excl={w: ("REIN", "ARCH", "REBLD") for w in ("S1", "S3", "H1")}),
    dict(me, name="Plan literal, H1 only (no S3)", windows=[w for w in P if w["name"] != "S3"]),
    dict(me, name="Plan literal, costs x1.5", windows=P, mult=1.5),
    dict(me, name="Plan literal, costs x2", windows=P, mult=2.0),
    dict(me, name="Plan literal, +2 s latency", windows=P, lat=1),
    dict(me, name="Plan literal, full H1, costs x2", windows=R.plan_windows(h1_size=1.0), mult=2.0),
    dict(me, name="Plan literal, full H1, +2 s", windows=R.plan_windows(h1_size=1.0), lat=1),
]
out = R.run(SP, lib, list(range(s0, s1 + 1)), cfgs)
print(f"### {lib} {s0}-{s1}: Plan with its literal thresholds, fills at the measured cost model")
print(R.HDR)
summ = {}
for c, res in zip(cfgs, out):
    s = R.summarize(res)
    summ[c["name"]] = s
    print(R.row(c["name"], s))
s = summ["Plan literal, measured fills (base)"]
print("\n| sym | total | H1 (H1+H1x) | S3 (S3+S3x) | S1 | cost paid | cost / gross |")
print("|---|---|---|---|---|---|---|")
for sym in R.SYMS:
    g = lambda ws, z=0: sum(s["by"].get(f"{w}|{sym}", [0, 0, 0])[z] for w in ws)
    tot, cp = s["sym"][sym], g(("H1", "H1x", "S3", "S3x", "S1"), 1)
    print(f"| {sym} | {tot:,.0f} | {g(('H1', 'H1x')):,.0f} | {g(('S3', 'S3x')):,.0f} | {g(('S1',)):,.0f} | {cp:,.0f} | "
          f"{(cp / (tot + cp)) if tot + cp > 0 else float('nan'):.0%} |")
json.dump(summ, open(OUT, "w"))
