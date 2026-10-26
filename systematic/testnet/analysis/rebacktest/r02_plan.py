"""Task 1: the playbook Plan (dp_bt3 REC-F: H1 1/2 size worked 50-56 / exit 72-78, S3 80-540 worked in 1/4-L
clips, k 0.1 rounds 1-2 / 0.2 round 3, rounds 1-2 flattened 562-568, S1-r3 740-778, no S2, no REIN, report
race lost) re-priced with the measured testnet cost model (r_sim cost mode "meas"), plus fill-model variants.
usage: python3 -I r02_plan.py <SP> <cost_model.json> <holdout|loso> <s0> <s1> <out.json>"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import r_sim as R  # noqa: E402

SP, CMP, lib, s0, s1, OUT = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), sys.argv[6]
B, CM = R.init(SP, CMP)
rt = dict(cost="rt", h=0.0025, cap=0.25, gamma=0.005, lat_rep=1, excl=R.NOREIN)
me = dict(cost="meas", cap=0.25, lat_rep=1, excl=R.NOREIN)
P = R.plan_windows()
cfgs = [
    dict(rt, name="RT model (playbook 20.3k)", windows=P),
    dict(me, name="MEAS full model (base)", windows=P),
    dict(me, name="MEAS base, rate 150", windows=P, rate=150),
    dict(me, name="MEAS no replenishment cap", windows=P, repl=False),
    dict(me, name="MEAS table only (no follow-on, no impact)", windows=P, beta_stock=0.0, beta_etf=0.0, imp=False),
    dict(me, name="MEAS table + follow-on (no impact)", windows=P, imp=False),
    dict(me, name="MEAS impact without reversal credit", windows=P, imp_credit=False),
    dict(me, name="MEAS books-report form (+31 bp/clip, 41 bp/L permanent)", windows=P, fo_mode="fixed"),
    dict(me, name="MEAS symmetric (buy/sell mean) tables", windows=P, side_specific=False),
    dict(me, name="MEAS Plan with full H1", windows=R.plan_windows(h1_size=1.0)),
    dict(me, name="MEAS Plan without H1", windows=R.plan_windows(h1_size=0)),
    dict(me, name="MEAS Plan without S1-r3", windows=R.plan_windows(s1r3=False)),
    dict(me, name="MEAS Plan without ETFs (and REIN)", windows=P, excl={w: ("REIN", "ARCH", "REBLD") for w in ("S1", "S3", "H1")}),
    dict(me, name="MEAS costs x1.5", windows=P, mult=1.5),
    dict(me, name="MEAS costs x2", windows=P, mult=2.0),
    dict(me, name="MEAS +2 s latency on every order", windows=P, lat=1),
]
seasons = list(range(s0, s1 + 1))
out = R.run(SP, lib, seasons, cfgs)
print(f"### {lib} {s0}-{s1}: Plan under the measured cost model")
print(R.HDR)
summ = {}
for c, res in zip(cfgs, out):
    s = R.summarize(res)
    summ[c["name"]] = s
    print(R.row(c["name"], s))
print()
print("per-symbol PnL ($/season) and per-symbol x strategy (H1 = H1+H1x round trip, S3 = S3+S3x, S1):")
for name in ("RT model (playbook 20.3k)", "MEAS full model (base)"):
    s = summ[name]
    print(f"[{name}]")
    print("| sym | total | H1 | S3 | S1 | cost paid | shares |")
    print("|---|---|---|---|---|---|---|")
    for sym in R.SYMS:
        g = lambda ws, z=0: sum(s["by"].get(f"{w}|{sym}", [0, 0, 0])[z] for w in ws)
        print(f"| {sym} | {s['sym'][sym]:,.0f} | {g(('H1', 'H1x')):,.0f} | {g(('S3', 'S3x')):,.0f} | {g(('S1',)):,.0f} | "
              f"{g(('H1', 'H1x', 'S3', 'S3x', 'S1'), 1):,.0f} | {g(('H1', 'H1x', 'S3', 'S3x', 'S1'), 2):,.0f} |")
json.dump(summ, open(OUT, "w"), indent=1)
