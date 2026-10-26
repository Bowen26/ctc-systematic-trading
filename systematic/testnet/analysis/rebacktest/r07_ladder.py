"""Where the re-optimisation gain comes from (one change at a time, measured cost model), plus operational
variants of REOPT: H1 in L/4 clips at 50-56 s, staged H1 (1/2 in round 1, full in rounds 2-3), H1 without the
single-name rebuilders, and the H1 marginal under crowded costs.
usage: python3 -I r07_ladder.py <SP> <cost_model.json> <holdout|loso> <s0> <s1> <out.json>"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import r_sim as R  # noqa: E402

SP, CMP, lib, s0, s1, OUT = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), sys.argv[6]
B, CM = R.init(SP, CMP)
plan = dict(cost="meas", cap=0.25, lat_rep=1, excl=R.NOREIN)
me = dict(cost="meas", lat_rep=1, excl=R.NOREIN, rate=150, cap=1 / 12, thr_m=2.0)
S3 = dict(s3_t0=80, k12=0.1, k3=0.2, clip_s3=1 / 12, flat12="auto")
H1 = dict(h1_size=1.0, h1_in=(42, 56), h1_out=None, clip_h1=0.125)
W = R.plan_windows(**H1, **S3)
H1_NOREB = ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "HAVEN", "ARCH"]


def staged(ws):
    """H1 at 1/2 size in round 1 and full size in rounds 2-3 (playbook round-1 check passed)."""
    out = []
    for w in ws:
        if w["name"] == "H1":
            w1 = dict(w, rounds=[1], dir={s: v * 0.5 for s, v in w["dir"].items()}, t0=50.0)
            out += [w1, dict(w, rounds=[2, 3])]
        else:
            out.append(w)
    return out


cfgs = [
    dict(plan, name="L0 Plan as-is", windows=R.plan_windows()),
    dict(plan, name="L1 = L0 + S3 threshold 2.0x cost (k .1/.2), rate 150", windows=R.plan_windows(), thr_m=2.0, rate=150),
    dict(me, name="L2 = L1 + S3 clips L/12 (flatten 540-568)", windows=R.plan_windows(clip_h1=0.25, **S3)),
    dict(me, name="L3 = L2 + H1 clips L/8 (1/2 size, 50-56 / exit 72-78)",
         windows=R.plan_windows(h1_size=0.5, h1_in=(50, 56), h1_out=(72, 78), clip_h1=0.125, **S3)),
    dict(me, name="L4 = L3 + H1 full size (42-56 / exit 72-86, S3 from 88)",
         windows=R.plan_windows(h1_size=1.0, h1_in=(42, 56), h1_out=(72, 86), clip_h1=0.125, **dict(S3, s3_t0=88))),
    dict(me, name="L5 = L4 + no forced exit, S3 inherits at 80 s (= REOPT)", windows=W),
    dict(me, name="REOPT, H1 in L/4 clips at 50-56", windows=R.plan_windows(**dict(H1, h1_in=(50, 56), clip_h1=0.25), **S3)),
    dict(me, name="REOPT, H1 staged (1/2 r1, full r2-3)", windows=staged(W)),
    dict(me, name="REOPT, H1 without RECON/MAT/GEN/REBLD", windows=R.plan_windows(**dict(H1, h1_syms=H1_NOREB), **S3)),
    dict(me, name="REOPT x1.5", windows=W, mult=1.5),
    dict(me, name="REOPT x1.5 no H1", windows=R.plan_windows(h1_size=0, **S3), mult=1.5),
    dict(me, name="REOPT x1.5, H1 without rebuilders", windows=R.plan_windows(**dict(H1, h1_syms=H1_NOREB), **S3), mult=1.5),
    dict(me, name="REOPT x2, H1 without rebuilders", windows=R.plan_windows(**dict(H1, h1_syms=H1_NOREB), **S3), mult=2.0),
    dict(me, name="REOPT x2, H1 1/2 size", windows=R.plan_windows(**dict(H1, h1_size=0.5, h1_in=(50, 56)), **S3), mult=2.0),
    dict(me, name="REOPT +2 s, x2", windows=W, mult=2.0, lat=1),
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
json.dump(summ, open(OUT, "w"))
