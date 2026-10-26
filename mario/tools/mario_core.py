"""Core Mario Markets model (standard library only).

Race model: 12 racers (4 humans H1..H4, 8 CPUs). Primary model is
Plackett-Luce (PL) == latent performance = log-strength + Gumbel(0,1) noise,
sorted descending.  CPUs have log-strength 0, humans theta_i (in Gumbel noise
units).  PL lets us compute the exact distribution of the 4 human finishing
positions (11,880 ordered outcomes) and exact likelihoods for Bayesian updates.
A Gaussian (Thurstonian) variant is provided for robustness checks.
"""
import math
import random
import itertools

POINTS = [15, 12, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
NR, NH, NC = 12, 4, 8
FACT8 = math.factorial(8)
ALL_POS = list(itertools.permutations(range(1, 13), 4))  # 11880 ordered outcomes


def pl_prob_positions(pos, w):
    """Exact PL probability that humans finish at overall positions pos (1-based)
    given human weights w (CPU weight 1).  CPUs exchangeable -> factor 8!."""
    slot_w = [1.0] * 12
    for i, p in enumerate(pos):
        slot_w[p - 1] = w[i]
    # suffix sums
    prob = 1.0
    S = sum(slot_w)
    for k in range(12):
        prob *= slot_w[k] / S
        S -= slot_w[k]
    return prob * FACT8


def pl_loglik_positions(pos, theta):
    w = [math.exp(t) for t in theta]
    slot_w = [1.0] * 12
    for i, p in enumerate(pos):
        slot_w[p - 1] = w[i]
    S = sum(slot_w)
    ll = 0.0
    for k in range(11):
        ll += math.log(slot_w[k] / S)
        S -= slot_w[k]
    return ll  # (constant log 8! dropped)


def race_dist_pl(theta):
    """Exact distribution over human positions: list of (pos_tuple, pts_tuple, prob)."""
    w = [math.exp(t) for t in theta]
    out = []
    tot = 0.0
    for pos in ALL_POS:
        p = pl_prob_positions(pos, w)
        tot += p
        out.append((pos, tuple(POINTS[q - 1] for q in pos), p))
    assert abs(tot - 1.0) < 1e-9, tot
    return out


def race_dist_gauss(theta, n=300000, seed=1):
    """Simulated distribution under Gaussian noise (sd 1)."""
    rng = random.Random(seed)
    cnt = {}
    g = rng.gauss
    for _ in range(n):
        perf = [(theta[i] + g(0, 1), i) for i in range(4)]
        perf += [(g(0, 1), -1) for _ in range(8)]
        perf.sort(reverse=True)
        pos = [0] * 4
        for r, (_, i) in enumerate(perf):
            if i >= 0:
                pos[i] = r + 1
        t = tuple(pos)
        cnt[t] = cnt.get(t, 0) + 1
    return [(pos, tuple(POINTS[q - 1] for q in pos), c / n) for pos, c in cnt.items()]


def race_dist_uniform():
    p = 1.0 / len(ALL_POS)
    return [(pos, tuple(POINTS[q - 1] for q in pos), p) for pos in ALL_POS]


# ---------------------------------------------------------------- summaries

def race_summary(dist):
    """Per-human position distributions and race-level diagnostics."""
    posd = [[0.0] * 13 for _ in range(4)]
    ep = [0.0] * 4
    epts = [0.0] * 4
    epts2 = [0.0] * 4
    top_h = [0.0] * 4  # race-winner market (highest-placed human)
    n_top4 = 0.0
    cpu_wins = 0.0
    hum_pts = {}
    for pos, pts, p in dist:
        for i in range(4):
            posd[i][pos[i]] += p
            ep[i] += p * pos[i]
            epts[i] += p * pts[i]
            epts2[i] += p * pts[i] ** 2
        top_h[min(range(4), key=lambda i: pos[i])] += p
        n_top4 += p * sum(1 for q in pos if q <= 4)
        if 1 not in pos:
            cpu_wins += p
        s = sum(pts)
        hum_pts[s] = hum_pts.get(s, 0.0) + p
    sdpts = [math.sqrt(epts2[i] - epts[i] ** 2) for i in range(4)]
    return dict(posd=posd, ep=ep, epts=epts, sdpts=sdpts, top_h=top_h,
                n_top4=n_top4, cpu_wins=cpu_wins, hum_pts=hum_pts)


def sampler(dist):
    pts = [d[1] for d in dist]
    cw = []
    c = 0.0
    for d in dist:
        c += d[2]
        cw.append(c)
    return pts, cw


def sample_sums(dist, r, M, seed=7):
    """M samples of the r-race sum vector of human points."""
    rng = random.Random(seed)
    pts, cw = sampler(dist)
    draws = rng.choices(pts, cum_weights=cw, k=r * M)
    out = []
    for m in range(M):
        a = [0, 0, 0, 0]
        for j in range(r):
            x = draws[m * r + j]
            a[0] += x[0]; a[1] += x[1]; a[2] += x[2]; a[3] += x[3]
        out.append(tuple(a))
    return out


def settle(final):
    """Return (series-winner payouts list, #1, #2, n_tied_at_top)."""
    m = max(final)
    tied = [i for i in range(4) if final[i] == m]
    pay = [0.0] * 4
    if len(tied) == 1:
        pay[tied[0]] = 100.0
    else:
        for i in tied:
            pay[i] = 50.0
    s = sorted(final, reverse=True)
    return pay, s[0], s[1], len(tied)


def evaluate(scores, samples, weights=None, want_dist=False):
    """Fair values given current scores and samples of remaining points.
    samples: list of 4-tuples (equal weight) or with weights list."""
    sw = [0.0] * 4
    e1 = e2 = e1sq = e2sq = eT = eTsq = 0.0
    ptie = p3 = p4 = 0.0
    W = 0.0
    d1, d2, dT = {}, {}, {}
    s0, s1, s2, s3 = scores
    for k, x in enumerate(samples):
        wt = 1.0 if weights is None else weights[k]
        f = (s0 + x[0], s1 + x[1], s2 + x[2], s3 + x[3])
        m = max(f)
        nt = (f[0] == m) + (f[1] == m) + (f[2] == m) + (f[3] == m)
        if nt == 1:
            for i in range(4):
                if f[i] == m:
                    sw[i] += 100 * wt
        else:
            for i in range(4):
                if f[i] == m:
                    sw[i] += 50 * wt
            ptie += wt
            if nt == 3:
                p3 += wt
            elif nt == 4:
                p4 += wt
        if nt >= 2:
            a, b = m, m
        else:
            b = max(v for v in f if v != m)
            a = m
        e1 += wt * a; e1sq += wt * a * a
        e2 += wt * b; e2sq += wt * b * b
        eT += wt * (a + b); eTsq += wt * (a + b) ** 2
        W += wt
        if want_dist:
            d1[a] = d1.get(a, 0) + wt
            d2[b] = d2.get(b, 0) + wt
            dT[a + b] = dT.get(a + b, 0) + wt
    res = dict(sw=[v / W for v in sw],
               e1=e1 / W, e2=e2 / W, eT=eT / W,
               sd1=math.sqrt(max(0, e1sq / W - (e1 / W) ** 2)),
               sd2=math.sqrt(max(0, e2sq / W - (e2 / W) ** 2)),
               sdT=math.sqrt(max(0, eTsq / W - (eT / W) ** 2)),
               ptie=ptie / W, p3=p3 / W, p4=p4 / W)
    if want_dist:
        res['d1'] = {k: v / W for k, v in d1.items()}
        res['d2'] = {k: v / W for k, v in d2.items()}
        res['dT'] = {k: v / W for k, v in dT.items()}
    return res


def evaluate_exact1(scores, dist):
    """Exact r=1 evaluation by enumerating the race distribution."""
    samples = [d[1] for d in dist]
    weights = [d[2] for d in dist]
    return evaluate(scores, samples, weights)


def pct(dist_map, qs=(0.05, 0.25, 0.5, 0.75, 0.95)):
    ks = sorted(dist_map)
    out = []
    for q in qs:
        c = 0.0
        for k in ks:
            c += dist_map[k]
            if c >= q - 1e-12:
                out.append(k)
                break
    return out


# ------------------------------------------------------------- scenarios

def bisect(f, lo, hi, target, it=60):
    flo = f(lo) - target
    for _ in range(it):
        mid = 0.5 * (lo + hi)
        fm = f(mid) - target
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return 0.5 * (lo + hi)


def calibrate_pl():
    """theta for S2 (E[human pos]=5.0) and S3 (E[#humans in top4]=3.0)."""
    def epos(t):
        return race_summary(race_dist_pl([t] * 4))['ep'][0]

    def ntop4(t):
        return race_summary(race_dist_pl([t] * 4))['n_top4']
    t2 = bisect(epos, 0.0, 3.0, 5.0, it=30)
    t3 = bisect(ntop4, 0.0, 5.0, 3.0, it=30)
    return t2, t3


# Calibrated values (computed by calibrate_pl(); hard-coded for speed, see scenarios.py)
SCEN = {}


def scenarios(t2, t3):
    return {
        'S1': [0.0, 0.0, 0.0, 0.0],
        'S2': [t2] * 4,
        'S3': [t3] * 4,
        'S4': [t3, t2, t2, 0.0],  # H1 strong (S3-level), H2/H3 average (S2-level), H4 weak (CPU-level)
    }
