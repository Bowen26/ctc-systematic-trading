"""Position paths (fraction of L, by symbol) of REOPT and of REOPT without H1 in one season, to see how long the
inherited H1 book is held.  usage: python3 -I r09_season_paths.py <SP> <cost_model.json> <loso|holdout> <season>"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import r_sim as R  # noqa: E402

SP, CMP, lib, n = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
B, CM = R.init(SP, CMP)
S3 = dict(s3_t0=80, k12=0.1, k3=0.2, clip_s3=1 / 12, flat12="auto")
H1 = dict(h1_size=1.0, h1_in=(42, 56), h1_out=None, clip_h1=0.125)
me = dict(cost="meas", lat_rep=1, excl=R.NOREIN, rate=150, cap=1 / 12, thr_m=2.0)
CP = [57, 80, 100, 120, 160, 200, 240, 300, 400, 539, 569]
se = B.Season(SP, n, lib)
for name, w in (("REOPT", R.plan_windows(**H1, **S3)), ("REOPT without H1", R.plan_windows(h1_size=0, **S3))):
    x = R.sim(se, dict(me, windows=w, path_t=CP))
    print(f"## season {n} {name}: total {x['pnl']:,.0f}, rounds " + ", ".join(f"{v:,.0f}" for v in x["round"]))
    for r in (1, 2, 3):
        print(f"  round {r}: position / L at t =  " + " ".join(f"{c:>5d}" for c in CP))
        for i, s in enumerate(R.SYMS):
            if s == "REIN":
                continue
            print(f"    {s:6s}" + " " * 22 + " ".join(f"{x['path'][r][c][i] / B.LIM[i]:+5.2f}" for c in CP))
    print("  PnL by symbol: " + ", ".join(f"{s} {v:,.0f}" for s, v in zip(R.SYMS, x["sym"]) if s != "REIN"))
