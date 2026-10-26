"""Validation: r_sim.sim in cost mode "rt" must reproduce dp_bt3.py REC-F (holdout 20,346 / train 19,504;
full H1 24,715 / 23,917) and the red-team worked number (24,458, rt_bt 'same, gamma .5% + report lat+1').
usage: python3 -I r01_validate.py <SP> <cost_model.json> <holdout|loso> <s0> <s1>"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import r_sim as R  # noqa: E402

SP, CMP, lib, s0, s1 = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
B, CM = R.init(SP, CMP)
base = dict(cost="rt", h=0.0025, cap=0.25, gamma=0.005, lat_rep=1, excl=R.NOREIN)
cfgs = [dict(base, name="REC-F (Plan) RT model", windows=R.plan_windows()),
        dict(base, name="REC-F full H1 RT model", windows=R.plan_windows(h1_size=1.0)),
        dict(base, name="REC-F RT model, rate 150", windows=R.plan_windows(), rate=150)]
seasons = list(range(s0, s1 + 1))
out = R.run(SP, lib, seasons, cfgs)
print(f"### {lib} {s0}-{s1}")
print(R.HDR)
for c, res in zip(cfgs, out):
    print(R.row(c["name"], R.summarize(res)))
