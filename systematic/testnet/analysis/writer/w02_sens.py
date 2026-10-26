"""Writer's rerun of the re-backtest sensitivities for the configuration the findings recommend
(REOPT with H1 in L/4 clips at 50-56 s), using the team's r_sim read-only. Standard library only.
usage: python3 -I w02_sens.py <SP> <rebacktest_dir> <cost_model.json> <holdout|loso> <s0> <s1>
"""
import sys
SP, RDIR, CMP, lib, s0, s1 = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), int(sys.argv[6])
sys.path.insert(0, RDIR)
import r_sim as R  # noqa: E402

B, CM = R.init(SP, CMP)
S3 = dict(s3_t0=80, k12=0.1, k3=0.2, clip_s3=1 / 12, flat12="auto")
H1 = dict(h1_size=1.0, h1_in=(50, 56), h1_out=None, clip_h1=0.25)
me = dict(cost="meas", lat_rep=1, excl=R.NOREIN, rate=150, cap=1 / 12, thr_m=2.0)
W = R.plan_windows(**H1, **S3)
H1_NOREB = [s for s in R.SYMS if s not in ("REIN", "RECON", "MAT", "GEN", "REBLD")]
cfgs = [
    dict(me, name="REC (REOPT, H1 L/4 50-56 s)", windows=W),
    dict(me, name="REC costs x1.5", windows=W, mult=1.5),
    dict(me, name="REC costs x2", windows=W, mult=2.0),
    dict(me, name="REC +2 s latency", windows=W, lat=1),
    dict(me, name="REC +2 s latency, costs x1.5", windows=W, lat=1, mult=1.5),
    dict(me, name="REC, H1 without RECON/MAT/GEN/REBLD", windows=R.plan_windows(**dict(H1, h1_syms=H1_NOREB), **S3)),
    dict(me, name="REC, H1 half size", windows=R.plan_windows(**dict(H1, h1_size=0.5), **S3)),
    dict(me, name="REC without H1 (S3 + S1-r3)", windows=R.plan_windows(h1_size=0, **S3)),
    dict(me, name="REC without ETFs", windows=W, excl={w: ("REIN", "ARCH", "REBLD") for w in ("S1", "S3", "H1")}),
    dict(me, name="REC without S1-r3", windows=R.plan_windows(**H1, **S3, s1r3=False)),
    dict(me, name="REC under the RT fill model", windows=W, cost="rt", h=0.0025, gamma=0.005, cap=1 / 12),
    dict(me, name="REC, 60-120 s phases x3", windows=W, phase_mult={"early": 3.0, "h1_exit": 3.0, "s3_start": 3.0}),
]
out = R.run(SP, lib, list(range(s0, s1 + 1)), cfgs)
print(f"### {lib} {s0}-{s1}")
print(R.HDR)
for c, res in zip(cfgs, out):
    print(R.row(c["name"], R.summarize(res)))
