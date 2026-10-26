"""Stress of the small-sample formation-window cost tables (h1_exit 72-80, s3_start 80-120, early 60-72 come from
3 recorded rounds): those phases x2 (or x3), all other phases x1, for REOPT and its H1 alternatives.
usage: python3 -I r14_early_stress.py <SP> <cm> <lib> <s0> <s1>"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import r_sim as R  # noqa: E402

SP, CMP, lib, s0, s1 = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
B, CM = R.init(SP, CMP)
S3 = dict(s3_t0=80, k12=0.1, k3=0.2, clip_s3=1 / 12, flat12="auto")
H1 = dict(h1_size=1.0, h1_in=(42, 56), h1_out=None, clip_h1=0.125)
me = dict(cost="meas", lat_rep=1, excl=R.NOREIN, rate=150, cap=1 / 12, thr_m=2.0)
cfgs = []
for k in (1.0, 2.0, 3.0):
    pm = {"early": k, "h1_exit": k, "s3_start": k}
    cfgs += [dict(me, name=f"REOPT, 60-120 s costs x{k:g}", windows=R.plan_windows(**H1, **S3), phase_mult=pm),
             dict(me, name=f"REOPT forced exit 72-86, 60-120 s costs x{k:g}", windows=R.plan_windows(**dict(H1, h1_out=(72, 86)), **dict(S3, s3_t0=88)), phase_mult=pm),
             dict(me, name=f"REOPT without H1, 60-120 s costs x{k:g}", windows=R.plan_windows(h1_size=0, **S3), phase_mult=pm),
             dict(me, name=f"REOPT H1 1/2, 60-120 s costs x{k:g}", windows=R.plan_windows(**dict(H1, h1_size=0.5, h1_in=(50, 56)), **S3), phase_mult=pm)]
out = R.run(SP, lib, list(range(s0, s1 + 1)), cfgs)
print(f"### {lib} {s0}-{s1}")
print(R.HDR)
for c, res in zip(cfgs, out):
    print(R.row(c["name"], R.summarize(res)))
