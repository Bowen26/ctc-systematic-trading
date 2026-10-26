# Bayesian skill learning under hierarchical PL prior: theta_i = mu + delta_i
import sys, os, math, random, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mario_core import *

class Post:
    def __init__(self, K=60000, mu0=0.9, smu=0.8, tau=0.6, seed=1):
        rng = random.Random(seed)
        self.th = []
        for _ in range(K):
            mu = rng.gauss(mu0, smu)
            self.th.append([mu + rng.gauss(0, tau) for _ in range(4)])
        self.lw = [0.0] * K
    def observe_positions(self, pos):
        for k, t in enumerate(self.th):
            self.lw[k] += pl_loglik_positions(pos, t)
    def observe_human_winner(self, i):
        for k, t in enumerate(self.th):
            m = max(t)
            self.lw[k] += t[i] - m - math.log(sum(math.exp(x - m) for x in t))
    def w(self):
        m = max(self.lw)
        e = [math.exp(x - m) for x in self.lw]
        s = sum(e)
        return [x / s for x in e]
    def ess(self):
        w = self.w(); return 1.0 / sum(x * x for x in w)
    def race_win(self):
        w = self.w(); out = [0.0] * 4
        for k, t in enumerate(self.th):
            m = max(t); e = [math.exp(x - m) for x in t]; s = sum(e)
            for i in range(4):
                out[i] += w[k] * e[i] / s
        return out
    def mean_mu(self):
        w = self.w()
        mu = [sum(t) / 4 for t in self.th]
        m = sum(a * b for a, b in zip(w, mu)); v = sum(a * (b - m) ** 2 for a, b in zip(w, mu))
        return m, math.sqrt(v)
    def resample(self, n, seed=5):
        rng = random.Random(seed)
        return rng.choices(self.th, weights=self.w(), k=n)

def sim_race_pts(theta, rng):
    rr = rng.random; lg = math.log
    perf = [theta[i] - lg(-lg(rr())) for i in range(4)]
    cpu = [-lg(-lg(rr())) for _ in range(8)]
    pts = []
    for i in range(4):
        p = 1 + sum(1 for j in range(4) if j != i and perf[j] > perf[i]) + sum(1 for c in cpu if c > perf[i])
        pts.append(POINTS[p - 1])
    return pts

def predictive(draws, scores, r, nper=60, seed=9):
    rng = random.Random(seed)
    smp = []
    for t in draws:
        for _ in range(nper):
            a = [0, 0, 0, 0]
            for _ in range(r):
                x = sim_race_pts(t, rng)
                a[0] += x[0]; a[1] += x[1]; a[2] += x[2]; a[3] += x[3]
            smp.append(tuple(a))
    return evaluate(scores, smp)

if __name__ == '__main__':
    T2, T3 = calibrate_pl()
    print("## Prior predictive: what tau means (sd of individual human log-strength around the human mean)")
    print("| tau | E[max human race-win prob] | E[min] | P(strongest human's race-win prob > 40%) | E[Series-winner prob of strongest human] |")
    print("|---|---|---|---|---|")
    for tau in (0.3, 0.6, 1.0):
        P = Post(K=20000, tau=tau, seed=3)
        mx = []; mn = []
        for t in P.th:
            m = max(t); e = [math.exp(x - m) for x in t]; s = sum(e)
            mx.append(max(e) / s); mn.append(min(e) / s)
        # strongest-human series win prob via simulation of 2000 draws x 20 series
        rng = random.Random(4); sw = 0.0; n = 0
        for t in P.th[:3000]:
            ib = max(range(4), key=lambda i: t[i])
            for _ in range(15):
                f = [0, 0, 0, 0]
                for _ in range(4):
                    x = sim_race_pts(t, rng); f = [f[i] + x[i] for i in range(4)]
                pay, _, _, _ = settle(f); sw += pay[ib]; n += 1
        print("| %.1f | %.1f%% | %.1f%% | %.0f%% | %.1f |" % (tau, 100 * sum(mx) / len(mx), 100 * sum(mn) / len(mn), 100 * sum(1 for v in mx if v > 0.4) / len(mx), sw / n))
    print()
    # --- Win-count table (race-winner info only; PL => win counts are sufficient)
    print("## P(human wins NEXT race) given race-winner record so far (counts of races won by each human; symmetric prior)")
    recs = [(1, 0, 0, 0), (2, 0, 0, 0), (1, 1, 0, 0), (3, 0, 0, 0), (2, 1, 0, 0), (1, 1, 1, 0), (4, 0, 0, 0), (3, 1, 0, 0), (2, 2, 0, 0), (2, 1, 1, 0), (1, 1, 1, 1),
            (5, 1, 1, 1), (4, 2, 1, 0), (3, 3, 1, 0), (6, 1, 0, 0), (7, 0, 0, 0), (2, 2, 2, 1)]
    taus = (0.3, 0.6, 1.0)
    posts = {tau: Post(K=150000, tau=tau, seed=11) for tau in taus}
    # precompute log softmax per sample
    lsm = {}
    for tau, P in posts.items():
        L = []
        for t in P.th:
            m = max(t); ls = math.log(sum(math.exp(x - m) for x in t)) + m
            L.append([x - ls for x in t])
        lsm[tau] = L
    print("| record (k1,k2,k3,k4) | " + " | ".join("tau=%.1f: P(next) for each human" % t for t in taus) + " |")
    print("|---|---|---|---|")
    for rec in recs:
        cells = []
        for tau in taus:
            L = lsm[tau]
            lw = [sum(rec[i] * l[i] for i in range(4)) for l in L]
            m = max(lw); w = [math.exp(x - m) for x in lw]; s = sum(w)
            pw = [0.0] * 4
            for k, l in enumerate(L):
                for i in range(4):
                    pw[i] += w[k] * math.exp(l[i])
            cells.append("/".join("%.1f" % (100 * x / s) for x in pw))
        print("| %s | " % (rec,) + " | ".join(cells) + " |")
