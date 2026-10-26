"""Mario Markets live fair-value calculator (does NOT connect to the exchange; prints numbers only).

Examples
  # symmetric skills, S2-level humans, after race 3 of Series 1 with scores 37,28,19,9:
  python3 -I fv_calc.py --scores 28 37 19 9 --left 1 --sym S2
  # Bayesian: feed every race seen so far (overall positions of H1,H2,H3,H4 per race, ';' separated).
  # The LAST --left races of the current series are simulated; scores are taken from --scores.
  python3 -I fv_calc.py --hist "4,1,7,9;6,2,5,10" --scores 16 27 14 7 --left 2
  # Series-2 markets before Series 2 starts (current-series args omitted):
  python3 -I fv_calc.py --hist "4,1,7,9;6,2,5,10;2,3,8,11;7,1,4,12"
Options: --tau auto (default: hyperprior over 0.2..1.2) or a number; --mu0 0.9 --smu 0.8 prior on human-vs-CPU level.
"""
import sys, os, math, random, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mario_core import POINTS, pl_loglik_positions, race_dist_pl, sample_sums, evaluate, evaluate_exact1, calibrate_pl, scenarios, pct
from learning import sim_race_pts

ap = argparse.ArgumentParser()
ap.add_argument('--scores', nargs=4, type=int, default=[0, 0, 0, 0])
ap.add_argument('--left', type=int, default=4)
ap.add_argument('--hist', default='')
ap.add_argument('--sym', default='')
ap.add_argument('--tau', default='auto')
ap.add_argument('--mu0', type=float, default=0.9)
ap.add_argument('--smu', type=float, default=0.8)
ap.add_argument('--K', type=int, default=80000)
ap.add_argument('--draws', type=int, default=2000)
ap.add_argument('--nper', type=int, default=50)
a = ap.parse_args()
sc = tuple(a.scores)


def show(lbl, e):
    print("  %s series-winner FV H1..H4: %s   (sum %.1f; P(tie for lead) %.1f%%)" % (lbl, " / ".join("%.1f" % v for v in e['sw']), sum(e['sw']), 100 * e['ptie']))
    print("  %s #1 Score %.2f (sd %.2f) | #2 Score %.2f (sd %.2f) | Top-2 Total %.2f (sd %.2f)" % (lbl, e['e1'], e['sd1'], e['e2'], e['sd2'], e['eT'], e['sdT']))


if a.sym:
    T2, T3 = calibrate_pl()
    dist = race_dist_pl(scenarios(T2, T3)[a.sym])
    e = evaluate_exact1(sc, dist) if a.left == 1 else evaluate(sc, sample_sums(dist, a.left, 200000))
    print("Symmetric %s, scores %s, %d race(s) left" % (a.sym, sc, a.left))
    show("", e)
    sys.exit()

rng = random.Random(1)
TAUS = [0.2, 0.4, 0.6, 0.8, 1.0, 1.2] if a.tau == 'auto' else [float(a.tau)]
th, tv = [], []
for _ in range(a.K):
    tau = rng.choice(TAUS)
    mu = rng.gauss(a.mu0, a.smu)
    th.append([mu + rng.gauss(0, tau) for _ in range(4)])
    tv.append(tau)
obs = [tuple(int(x) for x in r.split(',')) for r in a.hist.split(';') if r.strip()]
for o in obs:
    assert len(o) == 4 and len(set(o)) == 4 and all(1 <= x <= 12 for x in o), o
lw = [sum(pl_loglik_positions(o, t) for o in obs) for t in th]
mx = max(lw)
w = [math.exp(v - mx) for v in lw]
S = sum(w)
w = [v / S for v in w]
ess = 1 / sum(v * v for v in w)
mu_m = sum(w[k] * sum(th[k]) / 4 for k in range(a.K))
print("Races observed: %d   ESS %.0f   posterior mean human level mu = %.2f" % (len(obs), ess, mu_m))
if len(TAUS) > 1:
    print("  posterior over tau (human heterogeneity): " + ", ".join("%.1f:%.0f%%" % (t, 100 * sum(w[k] for k in range(a.K) if tv[k] == t)) for t in TAUS))
rw = [0.0] * 4
for k, t in enumerate(th):
    m = max(t); e = [math.exp(x - m) for x in t]; s = sum(e)
    for i in range(4):
        rw[i] += w[k] * 100 * e[i] / s
print("  NEXT/ANY future race-winner FV H1..H4: " + " / ".join("%.1f" % v for v in rw))
draws = rng.choices(th, weights=w, k=a.draws)
# expected points per race per human
ept = [0.0] * 4
for t in draws[:800]:
    for _ in range(10):
        x = sim_race_pts(t, rng)
        for i in range(4):
            ept[i] += x[i] / 8000
print("  E[points next race] H1..H4: " + " / ".join("%.2f" % v for v in ept))


def pred(scores, r):
    smp = []
    for t in draws:
        for _ in range(a.nper):
            v = [0, 0, 0, 0]
            for _ in range(r):
                x = sim_race_pts(t, rng)
                v[0] += x[0]; v[1] += x[1]; v[2] += x[2]; v[3] += x[3]
            smp.append(tuple(v))
    return evaluate(scores, smp, want_dist=True)


if a.left < 4 or any(sc):
    print("Current series: scores %s, %d race(s) left" % (sc, a.left))
    e = pred(sc, a.left)
    show("", e)
    print("  Top-2 Total 5/25/50/75/95%%: %s" % pct(e['dT']))
print("Fresh 4-race series (e.g. Series 2 before it starts):")
e = pred((0, 0, 0, 0), 4)
show("", e)
print("  #1 5/25/50/75/95%%: %s  #2: %s  Top-2: %s" % (pct(e['d1']), pct(e['d2']), pct(e['dT'])))
