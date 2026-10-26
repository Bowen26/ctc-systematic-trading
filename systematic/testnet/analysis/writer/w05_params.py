"""Writer's recomputation of bot-unit parameters from cost_model.json (standard library only).
usage: python3 -I w05_params.py <cost_model.json>
Clip sizes (floor of L/12, L/8, L/4) and the S3 entry cost term 2.0 x (walk cost of an L/12 clip + follow-on allowance)
with the allowance 0.36 x 100 bp (stocks) or 0.36 x 48 bp (ETFs); walk cost = linear interpolation of the phase 'table'.
"""
import sys, json
CM = json.load(open(sys.argv[1]))
SYMS = ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "RECON", "MAT", "GEN", "HAVEN", "REIN", "ARCH", "REBLD"]


def interp(tab, x):
    xs = sorted(float(k) for k in tab)
    ys = [tab[f"{v:.4f}"] for v in xs]
    if x <= xs[0]:
        return ys[0]
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return ys[-1]


print("sym    L    L/12 L/8 L/4 | walk(L/12) bp storm / 80-120 s | entry cost term bp storm / 80-120 s / after | mean half-spread bp storm / 80-120 s")
for s in SYMS:
    L = CM[s]["L"]
    al = 0.36 * (48.0 if s in ("ARCH", "REBLD") else 100.0)
    w = {ph: interp(CM[s][ph]["table"], 1 / 12) for ph in ("storm", "s3_start", "after")}
    print(f"{s:6s} {L:4d} {L // 12:5d} {L // 8:3d} {L // 4:3d} | {w['storm']:6.1f} / {w['s3_start']:6.1f} | "
          f"{2 * (w['storm'] + al):5.0f} / {2 * (w['s3_start'] + al):5.0f} / {2 * (w['after'] + al):5.0f} | "
          f"{CM[s]['storm']['half_spread_bps_mean']:5.1f} / {CM[s]['s3_start']['half_spread_bps_mean']:5.1f}")
