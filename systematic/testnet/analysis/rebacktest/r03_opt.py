"""Task 2: re-optimise clip size, the S3 threshold and the H1 leg under the measured cost model (train 1-400).
Stages:
  s3  : H1 off; grid clip x k x thr_m x S3 start.  S3 threshold = k*sd + thr_m*(walk(clip) + 0.36*beta) bp.
        Rounds are independent (rounds 1-2 flat at 568), so k/thr_m for rounds 1-2 and for round 3 are read off
        the same grid from R1+R2 and R3 separately.
  h1  : S3 fixed at the params given as JSON (argv 8); H1 size x clip x entry/exit timing; per-symbol H1 value.
usage: python3 -I r03_opt.py <SP> <cost_model.json> <holdout|loso> <s0> <s1> <stage> <out.json> [params.json-string]"""
import json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import r_sim as R  # noqa: E402

SP, CMP, lib, s0, s1, stage, OUT = sys.argv[1:8]
s0, s1 = int(s0), int(s1)
prm = json.loads(sys.argv[8]) if len(sys.argv) > 8 else {}
B, CM = R.init(SP, CMP)
me = dict(cost="meas", lat_rep=1, excl=R.NOREIN, rate=150)
cfgs = []
if stage == "s3":
    for clip in (0.125, 1 / 6, 0.25, 1 / 3):
        for t0 in (80, 120):
            for tm in (0.5, 1.0, 1.5, 2.0):
                for k in (0.0, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0):
                    cfgs.append(dict(me, cap=clip, thr_m=tm, key=dict(clip=clip, t0=t0, thr_m=tm, k=k),
                                     windows=R.plan_windows(h1_size=0, s3_t0=t0, k12=k, k3=k)))
elif stage == "s3c":
    # flatten window scaled to the clip (flat12="auto"): 568 - 2*(ceil(1/clip)-1) .. 568
    for clip in (1 / 16, 1 / 12, 0.125, 1 / 6, 0.25):
        for t0 in (80, 120):
            for tm in (1.0, 1.5, 2.0, 2.5, 3.0):
                for k in (0.0, 0.1, 0.2):
                    cfgs.append(dict(me, cap=clip, thr_m=tm, key=dict(clip=clip, t0=t0, thr_m=tm, k=k),
                                     windows=R.plan_windows(h1_size=0, s3_t0=t0, k12=k, k3=k, clip_s3=clip, flat12="auto")))
elif stage == "s3b":
    # extension of the s3 grid: the s3 optimum sat on the grid edge (smallest clip, largest thr_m)
    for clip in (1 / 16, 1 / 12, 0.125, 1 / 6):
        for t0 in (80, 120):
            for tm in (1.5, 2.0, 2.5, 3.0, 4.0):
                for k in (0.0, 0.1, 0.2, 0.35):
                    cfgs.append(dict(me, cap=clip, thr_m=tm, key=dict(clip=clip, t0=t0, thr_m=tm, k=k),
                                     windows=R.plan_windows(h1_size=0, s3_t0=t0, k12=k, k3=k)))
elif stage == "h1":
    clip, t0 = prm["clip"], prm["t0"]
    k12, k3, tm = prm["k12"], prm["k3"], prm["thr_m"]
    # thr_m may differ by round group: run S3 r1-2 and r3 windows with their own thr via two configs is not
    # possible inside one window list, so the s3 stage's choice must share thr_m (checked in r04).
    base_w = dict(s3_t0=t0, k12=k12, k3=k3, clip_s3=clip, flat12="auto")
    cfgs.append(dict(me, cap=clip, thr_m=tm, key=dict(h1="off"), windows=R.plan_windows(h1_size=0, **base_w)))
    for size in (0.5, 1.0):
        for ch in (0.125, 0.25, 0.5):
            n = max(1, math.ceil(size / ch - 1e-9))
            for tin1 in (56,):
                tin0 = tin1 - 2 * (n - 1)
                for tout0 in (72, 84, 100, 120, None):
                    out = None if tout0 is None else (tout0, tout0 + 2 * (n - 1))
                    t0s = max(t0, out[1] + 2) if out else t0
                    w = R.plan_windows(h1_size=size, h1_in=(tin0, tin1), h1_out=out, clip_h1=ch,
                                       **dict(base_w, s3_t0=t0s))
                    cfgs.append(dict(me, cap=clip, thr_m=tm, windows=w,
                                     key=dict(h1=size, clip_h1=ch, h1_in=[tin0, tin1], h1_out=out, s3_t0=t0s)))
seasons = list(range(s0, s1 + 1))
res = R.run(SP, lib, seasons, cfgs)
rows = []
for c, r in zip(cfgs, res):
    s = R.summarize(r)
    r12 = [x["round"][0] + x["round"][1] for x in r]
    r3 = [x["round"][2] for x in r]
    m12 = sum(r12) / len(r12); m3 = sum(r3) / len(r3)
    per = {}
    for sym in R.SYMS:
        g = lambda ws, z=0: sum(s["by"].get(f"{w}|{sym}", [0, 0, 0])[z] for w in ws)
        per[sym] = {"tot": s["sym"][sym], "H1": g(("H1", "H1x")), "S3": g(("S3", "S3x")), "S1": g(("S1",)),
                    "cost": g(("H1", "H1x", "S3", "S3x", "S1"), 1)}
    rows.append({"key": c["key"], "mean": s["mean"], "sd": s["sd"], "p5": s["p5"], "hit": s["hit"], "R12": m12, "carry": s["carry"],
                 "R3": m3, "act": s["act"], "max60": s["max60"], "comp": {k: v[0] for k, v in s["comp"].items()},
                 "per": per})
json.dump(rows, open(OUT, "w"), indent=1)
rows_sorted = sorted(rows, key=lambda x: -x["mean"])
print(f"### stage {stage}, {lib} {s0}-{s1}, {len(cfgs)} configs; top 25 by mean")
for x in rows_sorted[:25]:
    print(f"{json.dumps(x['key'])}  mean {x['mean']:8,.0f}  R12 {x['R12']:8,.0f}  R3 {x['R3']:7,.0f}  p5 {x['p5']:8,.0f}  "
          f"hit {x['hit']:.2f}  acts {x['act']:.0f}  max60 {x['max60']}  carry {x['carry']:.2f}")
