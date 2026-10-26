"""Sensitivity of the playbook plan (REC-F: H1 half size worked 50-56/72-78, S3 80-540 worked, rounds 1-2 S3 book
flattened 562-568, round 3 held + S1 740-778, no S2 / no S1 rounds 1-2, no REIN) under the red-team fill model.
Usage: python3 -I dp_bt3.py <SP> <holdout|loso> <s0> <s1> <h>"""
import sys, os
import dp_bt as D  # reads sys.argv[1]
R, B = D.R, D.B
import multiprocessing as mp


def w(h, h1_scale=0.5, h1=True, s1_r12=False, s2=False, flat12=562):
    ws = D.s6w(h, h1_scale, s1_r12=s1_r12, s2=s2, h1=h1)
    if flat12:
        ws.append(B.flat("S3x", (1, 2), float(flat12), prio=0, dur=6))
    return ws


def variants(h):
    base = dict(cap=0.25, gamma=0.005, lat_rep=1, excl=D.NOREIN)
    rec = dict(windows=w(h), **base)
    V = [("REC-F", rec),
         ("REC-F g0", dict(rec, gamma=0.0)), ("REC-F g.25%", dict(rec, gamma=0.0025)), ("REC-F g1%", dict(rec, gamma=0.01)),
         ("REC-F + lat+1 all", dict(rec, lat=1)), ("REC-F 120/min", dict(rec, rate=120)),
         ("REC-F storm spread max(h,.5sig)", dict(rec, kappa=0.5)),
         ("REC-F full H1", dict(rec, windows=w(h, 1.0))), ("REC-F no H1", dict(rec, windows=w(h, h1=False))),
         ("REC-F no ETFs", dict(rec, excl=D.NOETF)),
         ("REC-F + S1r12/S2 race lost", dict(rec, windows=w(h, s1_r12=True, s2=True))),
         ("REC-F + S1r12/S2 race won", dict(rec, windows=w(h, s1_r12=True, s2=True), lat_rep=0)),
         ("REC-F harsh: g.5% + .5sig + lat+1 all", dict(rec, kappa=0.5, lat=1))]
    return [dict(name=n, h=h, **kw) for n, kw in V]


if __name__ == "__main__":
    argv = D.argv
    lib, s0, s1, h = argv[2], int(argv[3]), int(argv[4]), float(argv[5])
    seasons = list(range(s0, s1 + 1)); cfgs = variants(h)
    with mp.get_context("fork").Pool(12, initializer=D._init, initargs=(lib, cfgs)) as pool:
        out = dict(pool.imap_unordered(D._work, seasons, chunksize=2))
    print(f"### {lib} seasons {s0}-{s1}, h = {h}")
    print("| variant | mean | sd | p5 | hit | R1 | R2 | R3 | acts | max60 | components |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for k, c in enumerate(cfgs):
        res = [out[n][k] for n in seasons]
        s = B.summarize([{"pnl": x["pnl"], "round": x["round"]} for x in res])
        comp = {}
        for x in res:
            for wn, v in x["win"].items():
                comp[wn] = comp.get(wn, 0.0) + v[0] / len(res)
        acts = sum(x["act"] for x in res) / len(res); mx = max(x["max60"] for x in res)
        cs = ", ".join(f"{a} {b:,.0f}" for a, b in sorted(comp.items()))
        print(f"| {c['name']} | {s['mean']:,.0f} | {s['sd']:,.0f} | {s['p5']:,.0f} | {s['hit']:.2f} | "
              f"{s['round'][0]:,.0f} | {s['round'][1]:,.0f} | {s['round'][2]:,.0f} | {acts:.0f} | {mx} | {cs} |", flush=True)
