"""REOPT with a deadline on the inherited H1 book: in [T_d, T_d+24] s the S3 exit rule is strict (a position is
kept only while the engine edge in its favour exceeds xstrict x the entry threshold), then S3 resumes as normal.
Lower-variance alternative to the plain hand-off.  usage: python3 -I r10_deadline.py <SP> <cm> <lib> <s0> <s1>"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import r_sim as R  # noqa: E402

SP, CMP, lib, s0, s1 = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
B, CM = R.init(SP, CMP)
S3 = dict(s3_t0=80, k12=0.1, k3=0.2, clip_s3=1 / 12, flat12="auto")
H1 = dict(h1_size=1.0, h1_in=(42, 56), h1_out=None, clip_h1=0.125)
me = dict(cost="meas", lat_rep=1, excl=R.NOREIN, rate=150, cap=1 / 12, thr_m=2.0)
W = R.plan_windows(**H1, **S3)


def deadline(ws, td, xs):
    out = []
    for w in ws:
        if w["name"] == "S3":
            out.append(dict(w, t0=float(td), t1=float(td + 24), xstrict=xs))
        out.append(w)
    return out


cfgs = [dict(me, name="REOPT (hand-off)", windows=W)]
for td in (100, 120, 160):
    for xs in (0.0, 0.5, 1.0):
        cfgs.append(dict(me, name=f"REOPT + strict exit {td}-{td + 24} s, xstrict {xs}", windows=deadline(W, td, xs)))
cfgs.append(dict(me, name="REOPT forced exit 72-86 (reference)", windows=R.plan_windows(**dict(H1, h1_out=(72, 86)), **dict(S3, s3_t0=88))))
out = R.run(SP, lib, list(range(s0, s1 + 1)), cfgs)
print(f"### {lib} {s0}-{s1}")
print(R.HDR)
for c, res in zip(cfgs, out):
    print(R.row(c["name"], R.summarize(res)))
