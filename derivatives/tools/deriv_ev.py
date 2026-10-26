"""Rank the edges by expected $ per event per underlying (ILLUSTRATIVE shock model; stdlib only).
Assumptions: S0=100, v0=100%, T=19 d, bot quotes fair +-1.005, ladder step 1.00, 200 lots/level.
dS%: 50% N(0,4%) + 50% N(0,12%);  dIV: N(0,15) pts; independent.  We assume WE get the fills."""
import math, random
from statistics import mean

def N(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def bs(k, S, K, d, v):
    T = d / 252; w = v * math.sqrt(T); d1 = (math.log(S / K) + 0.5 * w * w) / w
    return S * N(d1) - K * N(d1 - w) if k == 'C' else K * N(w - d1) - S * N(-d1)

H, LOT, D = 1.005, 200, 19.0
CON = [('C', 60), ('C', 100), ('C', 140), ('P', 60), ('P', 100), ('P', 140)]
random.seed(7)

def capture(edge, levels):
    return sum(LOT * (abs(edge) - (H + k)) for k in range(levels) if abs(edge) - (H + k) > 0)

res = {k: [] for k in ['A_all_L1', 'A_all_L3', 'A_deltaonly_L1', 'A_deltaonly_L3', 'B_vol_L1', 'B_vol_L3',
                       'R2_vol_L1_err4', 'gamma_straddle_S1unknown', 'A_60C_140P_only_L1']}
for _ in range(20000):
    sd = 0.04 if random.random() < 0.5 else 0.12
    dS = 100 * random.gauss(0, sd); dS = max(-45, min(60, dS))
    dv = random.gauss(0, 15); v1 = max(0.2, 1 + dv / 100)
    S1 = 100 + dS
    eA = {c: bs(c[0], S1, c[1], D, v1) - bs(c[0], 100, c[1], D, 1.0) for c in CON}
    eAd = {c: bs(c[0], S1, c[1], D, 1.0) - bs(c[0], 100, c[1], D, 1.0) for c in CON}
    res['A_all_L1'].append(sum(capture(e, 1) for e in eA.values()))
    res['A_all_L3'].append(sum(capture(e, 3) for e in eA.values()))
    # delta-only: trade direction implied by vol-free edge, realised at true fair; only if robust to +-15 pt
    tot1 = tot3 = 0
    for c in CON:
        lo = bs(c[0], S1, c[1], D, max(0.2, 1 + (-15) / 100)) - bs(c[0], 100, c[1], D, 1.0)
        hi = bs(c[0], S1, c[1], D, 1.15) - bs(c[0], 100, c[1], D, 1.0)
        worst = min(abs(lo), abs(hi)) if lo * hi > 0 else 0
        sgn = 1 if eAd[c] > 0 else -1
        for k in range(3):
            if worst - (H + k) > 0:
                pnl = LOT * (sgn * eA[c] - (H + k))
                tot3 += pnl
                if k == 0: tot1 += pnl
    res['A_deltaonly_L1'].append(tot1); res['A_deltaonly_L3'].append(tot3)
    res['A_60C_140P_only_L1'].append(capture(eA[('C', 60)], 1) + capture(eA[('P', 140)], 1))
    # Window B: nearest strike straddle
    Kn = min((60, 100, 140), key=lambda K: abs(K - S1))
    eb = bs('C', S1, Kn, D, v1) - bs('C', S1, Kn, D, 1.0)
    res['B_vol_L1'].append(2 * capture(eb, 1)); res['B_vol_L3'].append(2 * capture(eb, 3))
    # R2: estimate = truth + N(0,4); trade straddle at ATM in Window B if |est edge| > H + 0.5*vega*4
    est = dv + random.gauss(0, 4)
    ee = bs('C', S1, Kn, D, max(0.2, 1 + est / 100)) - bs('C', S1, Kn, D, 1.0)
    vg = bs('C', S1, Kn, D, 1.01) - bs('C', S1, Kn, D, 1.0)
    if abs(ee) - H > 0.5 * vg * 4:
        res['R2_vol_L1_err4'].append(2 * LOT * ((1 if ee > 0 else -1) * eb - H))
    else:
        res['R2_vol_L1_err4'].append(0)
    # S1 unknown, vol flat assumed: delta-neutral ratio straddle bought at stale (ratio 0.80 C : 1 P), 200 P
    eg = 0.80 * (bs('C', S1, 100, D, v1) - bs('C', 100, 100, D, 1)) + (bs('P', S1, 100, D, v1) - bs('P', 100, 100, D, 1))
    res['gamma_straddle_S1unknown'].append(LOT * (eg - 1.8 * H))

def pct(v, q):
    s = sorted(v); return s[int(q * (len(s) - 1))]
print('%-26s %9s %9s %9s %9s' % ('edge (per event, 1 underlying)', 'mean $', 'median', 'p10', 'p90'))
for k, v in res.items():
    print('%-26s %9.0f %9.0f %9.0f %9.0f' % (k, mean(v), pct(v, .5), pct(v, .1), pct(v, .9)))
