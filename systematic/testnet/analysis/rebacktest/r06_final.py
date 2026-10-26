"""Tasks 2-3: the re-optimised configuration (REOPT, chosen on train 1-400 in r03) and the original Plan under the
measured cost model: marginal value of each strategy, per-symbol PnL, sensitivity (costs x1.5 / x2, +2 s
latency, fill-model variants, rate limit), and a re-tune of the S3 threshold under crowded costs.
REOPT = H1 full size, entered in L/8 clips 42-56 s, no forced exit (S3 inherits the H1 book at 80 s);
        S3 80-540 in L/12 clips, threshold k*sd + 2.0*(walk(L/12) + 0.36*beta), k 0.1 rounds 1-2 / 0.2 round 3;
        rounds 1-2 flattened over 540-568 s in L/12 clips; S1-r3 taker 740-778; no REIN; bot cap 150 actions/min.
usage: python3 -I r06_final.py <SP> <cost_model.json> <holdout|loso> <s0> <s1> <out.json>"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import r_sim as R  # noqa: E402

SP, CMP, lib, s0, s1, OUT = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), sys.argv[6]
B, CM = R.init(SP, CMP)
S3 = dict(s3_t0=80, k12=0.1, k3=0.2, clip_s3=1 / 12, flat12="auto")
H1 = dict(h1_size=1.0, h1_in=(42, 56), h1_out=None, clip_h1=0.125)
me = dict(cost="meas", lat_rep=1, excl=R.NOREIN, rate=150, cap=1 / 12, thr_m=2.0)
W = R.plan_windows(**H1, **S3)
NOETF = {w: ("REIN", "ARCH", "REBLD") for w in ("S1", "S3", "H1")}
plan = dict(cost="meas", cap=0.25, lat_rep=1, excl=R.NOREIN)
cfgs = [
    dict(plan, name="Plan as-is (measured costs)", windows=R.plan_windows()),
    dict(me, name="REOPT", windows=W),
    # marginals
    dict(me, name="REOPT without H1", windows=R.plan_windows(h1_size=0, **S3)),
    dict(me, name="REOPT with H1 1/2 size", windows=R.plan_windows(**dict(H1, h1_size=0.5, h1_in=(50, 56)), **S3)),
    dict(me, name="REOPT with forced H1 exit 72-86", windows=R.plan_windows(**dict(H1, h1_out=(72, 86)), **dict(S3, s3_t0=88))),
    dict(me, name="REOPT H1 only (no S3; exit 80-94, flat)", windows=[w for w in R.plan_windows(**dict(H1, h1_out=(80, 94)), **S3) if w["name"] not in ("S3",)]),
    dict(me, name="REOPT without S1-r3", windows=R.plan_windows(**H1, **dict(S3), s1r3=False)),
    dict(me, name="REOPT without ETFs", windows=W, excl=NOETF),
    dict(me, name="REOPT without MAT", windows=W, excl={w: ("REIN", "MAT") for w in ("S1", "S3", "H1")}),
    dict(me, name="REOPT without rebuilders (RECON MAT GEN REBLD)", windows=W,
         excl={w: ("REIN", "RECON", "MAT", "GEN", "REBLD") for w in ("S1", "S3", "H1")}),
    # sensitivity
    dict(me, name="REOPT costs x1.5", windows=W, mult=1.5),
    dict(me, name="REOPT costs x2", windows=W, mult=2.0),
    dict(me, name="REOPT costs x1.5, thr_m 3", windows=W, mult=1.5, thr_m=3.0),
    dict(me, name="REOPT costs x2, thr_m 3", windows=W, mult=2.0, thr_m=3.0),
    dict(me, name="REOPT costs x2, thr_m 4", windows=W, mult=2.0, thr_m=4.0),
    dict(me, name="REOPT costs x2, no H1", windows=R.plan_windows(h1_size=0, **S3), mult=2.0),
    dict(me, name="REOPT +2 s latency", windows=W, lat=1),
    dict(me, name="REOPT +2 s latency, costs x1.5", windows=W, lat=1, mult=1.5),
    dict(me, name="REOPT no impact reversal credit", windows=W, imp_credit=False),
    dict(me, name="REOPT books-report form (+31 bp/clip, 41 bp/L perm.)", windows=W, fo_mode="fixed"),
    dict(me, name="REOPT symmetric tables", windows=W, side_specific=False),
    dict(me, name="REOPT no replenishment cap", windows=W, repl=False),
    dict(me, name="REOPT rate 120/min", windows=W, rate=120),
    dict(me, name="REOPT rate 180/min", windows=W, rate=180),
    dict(me, name="REOPT under the RT fill model (h .25%, g .5%)", windows=W, cost="rt", h=0.0025, gamma=0.005, cap=1 / 12),
]
seasons = list(range(s0, s1 + 1))
out = R.run(SP, lib, seasons, cfgs)
print(f"### {lib} {s0}-{s1}")
print(R.HDR)
summ = {}
for c, res in zip(cfgs, out):
    s = R.summarize(res)
    summ[c["name"]] = s
    print(R.row(c["name"], s))
print()
for name in ("REOPT", "REOPT costs x2", "REOPT +2 s latency", "REOPT no impact reversal credit"):
    s = summ[name]
    print(f"[per symbol: {name}]  (H1 = H1 entry trades, PnL to settlement; S3 = S3 + S3x)")
    print("| sym | total | H1 | S3 | S1 | cost paid | shares |")
    print("|---|---|---|---|---|---|---|")
    for sym in R.SYMS:
        g = lambda ws, z=0: sum(s["by"].get(f"{w}|{sym}", [0, 0, 0])[z] for w in ws)
        print(f"| {sym} | {s['sym'][sym]:,.0f} | {g(('H1', 'H1x')):,.0f} | {g(('S3', 'S3x')):,.0f} | {g(('S1',)):,.0f} | "
              f"{g(('H1', 'H1x', 'S3', 'S3x', 'S1'), 1):,.0f} | {g(('H1', 'H1x', 'S3', 'S3x', 'S1'), 2):,.0f} |")
    print()
print("[per-symbol marginal of H1 inside REOPT: sym PnL(REOPT) - sym PnL(REOPT without H1)]")
a, b = summ["REOPT"]["sym"], summ["REOPT without H1"]["sym"]
print(" ".join(f"{sym} {a[sym] - b[sym]:,.0f}" for sym in R.SYMS))
json.dump(summ, open(OUT, "w"))
