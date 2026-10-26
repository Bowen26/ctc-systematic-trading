"""Diagnostics for the H1 'hand-off' (no forced exit; S3 inherits the H1 book at 80 s): how the H1 book is
unwound over time, and the H1 value by round and by symbol, train or holdout.
usage: python3 -I r04_h1_paths.py <SP> <cost_model.json> <holdout|loso> <s0> <s1>"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import r_sim as R  # noqa: E402

SP, CMP, lib, s0, s1 = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
B, CM = R.init(SP, CMP)
S3 = dict(s3_t0=80, k12=0.1, k3=0.2, clip_s3=1 / 12, flat12="auto")
me = dict(cost="meas", lat_rep=1, excl=R.NOREIN, rate=150, cap=1 / 12, thr_m=2.0)
CP = [57, 74, 80, 90, 100, 120, 160, 240, 360, 539, 569, 779]
cfgs = [dict(me, name="S3 only", windows=R.plan_windows(h1_size=0, **S3), path_t=CP),
        dict(me, name="H1 full, hand-off", windows=R.plan_windows(h1_size=1.0, h1_in=(42, 56), h1_out=None, clip_h1=0.125, **S3), path_t=CP),
        dict(me, name="H1 full, exit 72-86", windows=R.plan_windows(h1_size=1.0, h1_in=(42, 56), h1_out=(72, 86), clip_h1=0.125, **dict(S3, s3_t0=88)), path_t=CP)]
res = R.run(SP, lib, list(range(s0, s1 + 1)), cfgs)
d = {s: 1 for s in R.H_LONG}; d.update({s: -1 for s in R.H_SHORT})
for c, rr in zip(cfgs, res):
    s = R.summarize(rr)
    print(f"## {c['name']}: mean {s['mean']:,.0f}  R1 {s['round'][0]:,.0f} R2 {s['round'][1]:,.0f} R3 {s['round'][2]:,.0f}  p5 {s['p5']:,.0f}")
    print("mean position in the H1 direction (fraction of L, 11 symbols) at checkpoint t, by round:")
    for r in (1, 2, 3):
        line = []
        for cp in CP:
            v = []
            for x in rr:
                p = x["path"].get(r, {}).get(cp) if x["path"].get(r) else None
                if p is None:
                    p = x["path"].get(str(r), {}).get(str(cp))
                if p is None:
                    continue
                v.append(sum(d[sy] * p[i] / B.LIM[i] for i, sy in enumerate(R.SYMS) if sy != "REIN") / 11)
            line.append(f"{cp}:{(sum(v) / len(v)) if v else float('nan'):+.2f}")
        print(f"  r{r} " + " ".join(line))
    print("  HAVEN position / L by checkpoint (round 1): " + " ".join(
        f"{cp}:{sum(x['path'][1][cp][R.SYMS.index('HAVEN')] for x in rr) / len(rr) / 250:+.2f}" for cp in CP if all(cp in x['path'].get(1, {}) for x in rr)))
    comp = s["comp"]
    print("  components (PnL to settle): " + ", ".join(f"{k} {v[0]:,.0f} (cost {v[1]:,.0f})" for k, v in sorted(comp.items())))
