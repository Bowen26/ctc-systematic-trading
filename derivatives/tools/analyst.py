"""Round-2 analyst aggregation: online bias/variance tracking + robust combination + sizing.

Library + CLI.  All numbers in vol points.  error = analyst estimate - IV backed out after the snap.
CLI example:
  python3 -I analyst.py --hist "A1:+1.5,-2.0;A2:+6,+4;A3:-14,+9" --est 92,99,70 --mkt 80 --vega 0.111
"""
import math, argparse

PRIOR_SD = 8.0      # vol pts, prior analyst error SD until pooled data exists
K_SD = 1.0          # pseudo-observations for the variance prior
K_BIAS = 2.0        # pseudo-observations shrinking bias to 0
CLIP_MULT = 2.5     # winsorise errors at CLIP_MULT x pooled robust scale before bias/var
PAIR_TOL = 3.0      # "two analysts agree" tolerance (raw pts)
OUTLIER_PTS = 8.0   # minimum gap for calling the third analyst an outlier
GAP_RATIO = 3.0


def _median(v):
    v = sorted(v)
    n = len(v)
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def analyst_stats(hist, robust=True):
    """hist: dict name -> list of past errors. Returns dict name -> (bias, sd)."""
    allerr = [e for v in hist.values() for e in v]
    if len(allerr) >= 3:
        if robust:
            scale = max(1.0, 1.4826 * _median([abs(e) for e in allerr]))
            clip = CLIP_MULT * scale
        else:
            clip = float('inf')
        s0sq = max(1.0, sum(min(abs(e), clip) ** 2 for e in allerr) / len(allerr))
    else:
        clip = float('inf') if not robust else 3 * PRIOR_SD
        s0sq = PRIOR_SD ** 2
    out = {}
    for name, errs in hist.items():
        n = len(errs)
        ce = [max(-clip, min(clip, e)) for e in errs]       # winsorised for the bias
        b = sum(ce) / (n + K_BIAS) if n else 0.0
        ss = sum((e - b) ** 2 for e in errs)                 # raw errors for the variance (keep liars penalised)
        var = (K_SD * s0sq + ss) / (K_SD + n)
        out[name] = (b, math.sqrt(var))
    return out


def ivw(x, stats, use):
    w = {n: 1.0 / stats[n][1] ** 2 for n in use}
    W = sum(w.values())
    mu = sum(w[n] * x[n] for n in use) / W
    chi2 = sum(w[n] * (x[n] - mu) ** 2 for n in use)
    birge = math.sqrt(max(1.0, chi2 / (len(use) - 1))) if len(use) > 1 else 1.0
    return mu, 1.0 / math.sqrt(W), birge, w


def tight_pair_drop(vals):
    """Raw-value rule: two analysts within PAIR_TOL and the third > max(OUTLIER_PTS, GAP_RATIO*pair gap) away."""
    if len(vals) != 3:
        return None
    it = sorted(vals.items(), key=lambda kv: kv[1])
    g_lo, g_hi = it[1][1] - it[0][1], it[2][1] - it[1][1]
    if g_hi <= PAIR_TOL and g_lo > max(OUTLIER_PTS, GAP_RATIO * g_hi):
        return it[0][0]
    if g_lo <= PAIR_TOL and g_hi > max(OUTLIER_PTS, GAP_RATIO * g_lo):
        return it[2][0]
    return None


def gap_drop(vals, ratio=GAP_RATIO, floor=OUTLIER_PTS):
    """Drop the isolated analyst if its gap to the nearer one is > ratio x the other gap and > floor."""
    if len(vals) != 3:
        return None
    it = sorted(vals.items(), key=lambda kv: kv[1])
    g_lo, g_hi = it[1][1] - it[0][1], it[2][1] - it[1][1]
    if g_lo > floor and g_lo > ratio * max(g_hi, 0.5):
        return it[0][0]
    if g_hi > floor and g_hi > ratio * max(g_lo, 0.5):
        return it[2][0]
    return None


def combine(est, hist, robust=True):
    stats = analyst_stats(hist, robust=robust)
    names = list(est)
    x = {n: est[n] - stats[n][0] for n in names}
    mu, se, birge, w = ivw(x, stats, names)
    med = _median(list(x.values()))
    dropped = tight_pair_drop(est) or gap_drop(x)
    keep = [n for n in names if n != dropped]
    mu_r, se_r, birge_r, _ = ivw(x, stats, keep)
    return {'stats': stats, 'weights': w, 'debiased': x, 'ivw': mu, 'ivw_se': se, 'birge': birge,
            'tau_ivw': se * birge, 'median': med, 'mean': sum(est.values()) / len(est),
            'robust': mu_r, 'robust_se': se_r, 'dropped': dropped, 'tau': se_r * birge_r}


def size_levels(edge_pts, tau, vega, half=1.005, z=1.0, nlev=3, lot=200):
    """Contracts to take: bot level k (k=0,1,2) taken iff vega*|E| - (half + k) >= z * vega * tau."""
    n = 0
    for k in range(nlev):
        if vega * abs(edge_pts) - (half + k) >= z * vega * tau:
            n += 1
    return n * lot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hist', default='', help='e.g. "A1:+1.5,-2;A2:+6,+4;A3:-14,+9"')
    ap.add_argument('--est', required=True, help='current estimates, e.g. 92,99,70')
    ap.add_argument('--mkt', type=float, required=True, help='IV in the (stale) quotes, vol pts')
    ap.add_argument('--vega', type=float, default=0.111, help='vega per pt of the contract to trade')
    ap.add_argument('--z', type=float, default=0.5)
    a = ap.parse_args()
    est = {'A%d' % (i + 1): float(v) for i, v in enumerate(a.est.split(','))}
    hist = {n: [] for n in est}
    if a.hist:
        for part in a.hist.split(';'):
            n, errs = part.split(':')
            hist[n.strip()] = [float(e) for e in errs.split(',') if e.strip()]
    r = combine(est, hist)
    for n in est:
        b, s = r['stats'][n]
        print('%s est %.1f  bias %+.2f  sd %.2f  weight %.4f  debiased %.2f' % (n, est[n], b, s, r['weights'][n], r['debiased'][n]))
    print('mean %.2f | median(debiased) %.2f | IVW %.2f (se %.2f, Birge %.2f, tau %.2f) | ROBUST %.2f (dropped=%s, tau %.2f)' % (
        r['mean'], r['median'], r['ivw'], r['ivw_se'], r['birge'], r['tau_ivw'], r['robust'], r['dropped'], r['tau']))
    E = r['robust'] - a.mkt
    print('edge vs market: %+.2f vol pts -> $%.3f per contract (vega %.3f); spread breakeven %.1f pts; need %.1f pts at z=%.1f' % (
        E, E * a.vega, a.vega, 1.005 / a.vega, 1.005 / a.vega + a.z * r['tau'], a.z))
    print('size: %d contracts per strike/side (%s)' % (size_levels(E, r['tau'], a.vega, z=a.z), 'BUY vol' if E > 0 else 'SELL vol'))


if __name__ == '__main__':
    main()
