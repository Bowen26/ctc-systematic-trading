"""(e) Re-run re-backtest headline configurations with the team's r_sim (imported read-only from rebacktest/),
plus the parameter set the report actually recommends (H1 in L/4 clips at 50-56 s) and two extra stresses.
usage: python3 -I v06_rerun.py <SP> <rebacktest_dir> <cost_model.json> <holdout|loso> <s0> <s1>
"""
import sys, os
SP, RDIR, CMP, lib, s0, s1 = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), int(sys.argv[6])
sys.path.insert(0, RDIR)
import r_sim as R  # noqa: E402

B, CM = R.init(SP, CMP)
S3 = dict(s3_t0=80, k12=0.1, k3=0.2, clip_s3=1 / 12, flat12="auto")
H1 = dict(h1_size=1.0, h1_in=(42, 56), h1_out=None, clip_h1=0.125)
me = dict(cost="meas", lat_rep=1, excl=R.NOREIN, rate=150, cap=1 / 12, thr_m=2.0)
W = R.plan_windows(**H1, **S3)
lit = dict(cost="meas", cap=0.25, lat_rep=1, excl=R.NOREIN, thr_fixed=0.0075, x_fixed=0.0025)
cfgs = [
    dict(lit, name="Plan literal, measured fills (r02b base)", windows=R.plan_windows()),
    dict(me, name="REOPT (r06)", windows=W),
    dict(me, name="REOPT under RT fill model", windows=W, cost="rt", h=0.0025, gamma=0.005, cap=1 / 12),
    dict(me, name="REOPT as RECOMMENDED: H1 L/4 clips 50-56 s", windows=R.plan_windows(**dict(H1, h1_in=(50, 56), clip_h1=0.25), **S3)),
    dict(me, name="REOPT without H1 (S3 + S1-r3)", windows=R.plan_windows(h1_size=0, **S3)),
    dict(me, name="REOPT, 60-120 s phases x3", windows=W, phase_mult={"early": 3.0, "h1_exit": 3.0, "s3_start": 3.0}),
]
out = R.run(SP, lib, list(range(s0, s1 + 1)), cfgs)
print(f"### {lib} {s0}-{s1}")
print(R.HDR)
for c, res in zip(cfgs, out):
    print(R.row(c["name"], R.summarize(res)))
