"""Crowding sensitivity when the bot does NOT re-measure costs: fills cost x1.5 / x2 but the S3 threshold keeps
the x1 cost term (equivalent to thr_m = 2/1.5 and 2/2 under mult 1.5 / 2).  Also the Plan under the same.
usage: python3 -I r13_unadapted.py <SP> <cost_model.json> <lib> <s0> <s1>"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import r_sim as R  # noqa: E402

SP, CMP, lib, s0, s1 = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
B, CM = R.init(SP, CMP)
S3 = dict(s3_t0=80, k12=0.1, k3=0.2, clip_s3=1 / 12, flat12="auto")
H1 = dict(h1_size=1.0, h1_in=(42, 56), h1_out=None, clip_h1=0.125)
me = dict(cost="meas", lat_rep=1, excl=R.NOREIN, rate=150, cap=1 / 12, thr_m=2.0)
W = R.plan_windows(**H1, **S3)
plan = dict(cost="meas", cap=0.25, lat_rep=1, excl=R.NOREIN)
cfgs = [dict(me, name="REOPT x1.5, threshold adapted (thr_m 2.0 on x1.5 costs)", windows=W, mult=1.5),
        dict(me, name="REOPT x1.5, threshold not adapted", windows=W, mult=1.5, thr_m=2.0 / 1.5),
        dict(me, name="REOPT x2, threshold adapted", windows=W, mult=2.0),
        dict(me, name="REOPT x2, threshold not adapted", windows=W, mult=2.0, thr_m=1.0),
        dict(plan, name="Plan x1.5, threshold not adapted", windows=R.plan_windows(), mult=1.5, thr_m=1 / 1.5),
        dict(plan, name="Plan x2, threshold not adapted", windows=R.plan_windows(), mult=2.0, thr_m=0.5)]
out = R.run(SP, lib, list(range(s0, s1 + 1)), cfgs)
print(f"### {lib} {s0}-{s1}")
print(R.HDR)
for c, res in zip(cfgs, out):
    print(R.row(c["name"], R.summarize(res)))
