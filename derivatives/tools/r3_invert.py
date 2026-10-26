"""Round-3: convert an analyst's 'quantity linked to IV' back to IV (and print the reference table).

usage examples (S = spot, days = trading days to expiry, default 18 = R3 open):
  python3 -I r3_invert.py table [--S 100] [--days 18]
  python3 -I r3_invert.py var 0.81              # variance (decimal^2)  -> 90%
  python3 -I r3_invert.py dailyvol 5.67         # daily vol in %        -> sigma = d*sqrt(252)
  python3 -I r3_invert.py dailymove 5.67 --S 100  # 1-SD daily $ move
  python3 -I r3_invert.py mad 4.52 --S 100      # mean absolute daily $ move
  python3 -I r3_invert.py move 26.7 --S 100     # 1-SD $ move to expiry
  python3 -I r3_invert.py straddle 21.26 --S 100  # ATM (K=S) straddle price, exact BS inversion
  python3 -I r3_invert.py upper 130.6 --S 100   # upper end of 1-SD lognormal range S*exp(sigma*sqrt(T))
  python3 -I r3_invert.py prob 0.71 --X 10      # P(|move to expiry| > X%)
  python3 -I r3_invert.py pct 15 --prev 80      # +15% change in IV from 80
  python3 -I r3_invert.py rank 0.6 --lo 50 --hi 130  # IV rank within [lo, hi]
  python3 -I r3_invert.py sdterm 27.6 --S 100   # SD of terminal price (lognormal)
  python3 -I r3_invert.py itm 0.25 --S 100 --K 140   # P(call ITM)=N(d2) for strike K
  python3 -I r3_invert.py rv 0 --S0 100 --S1 93 --eventdays 0.25  # realised vol of one event move
"""
import os, sys, math, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bs import ncdf, ninv, price

SQ252 = math.sqrt(252.0)
C_MAD = math.sqrt(2.0 / math.pi)   # 0.797885


def p_beyond(sig, T, X):
    w = sig * math.sqrt(T)
    up = ncdf(-(math.log(1 + X) + 0.5 * w * w) / w)
    dn = ncdf((math.log(1 - X) + 0.5 * w * w) / w) if X < 1 else 0.0
    return up + dn


def solve(f, target, lo=0.01, hi=6.0):
    flo = f(lo) - target
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        fm = f(mid) - target
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return 0.5 * (lo + hi)


def invert(kind, q, S=100.0, T=18 / 252, **kw):
    sT = math.sqrt(T)
    if kind == 'var':
        return math.sqrt(q)
    if kind == 'dailyvol':
        return q / 100 * SQ252
    if kind == 'dailymove':
        return q / S * SQ252
    if kind == 'mad':
        return q / (S * C_MAD) * SQ252
    if kind == 'move':
        return q / (S * sT)
    if kind == 'mad_expiry':
        return q / (S * C_MAD * sT)
    if kind == 'straddle':
        return 2.0 / sT * ninv(0.5 + q / (4.0 * S))
    if kind == 'straddle_approx':
        return q / (C_MAD * S * sT)
    if kind == 'upper':
        return math.log(q / S) / sT
    if kind == 'lower':
        return -math.log(q / S) / sT
    if kind == 'prob':
        return solve(lambda s: p_beyond(s, T, kw['X'] / 100), q)
    if kind == 'pct':
        return kw['prev'] / 100 * (1 + q / 100)
    if kind == 'rank':
        return (kw['lo'] + q * (kw['hi'] - kw['lo'])) / 100
    if kind == 'sdterm':
        return math.sqrt(math.log(1 + (q / S) ** 2) / T)
    if kind == 'itm':
        K = kw['K']
        # N(d2) is not monotone in sigma for ITM strikes; search the decreasing branch from small vol
        return solve(lambda s: ncdf((math.log(S / K) - 0.5 * s * s * T) / (s * sT)), q, 0.05, 4.0)
    if kind == 'rv':
        dt = kw['eventdays'] / 252.0
        return abs(math.log(kw['S1'] / kw['S0'])) / math.sqrt(dt)
    raise ValueError(kind)


def table(S, days):
    T = days / 252.0
    sT = math.sqrt(T)
    print('Reference table: S=%.2f, T=%.3f trading days (%.5f yr, sqrt(T)=%.4f). $ columns scale linearly with S.' % (S, days, T, sT))
    print('| IV | sigma^2 | daily vol % | 1-SD daily $ | mean abs daily $ | 1-SD $ to expiry | ATM straddle exact | straddle approx | 1-SD range (lognormal) | P(|move|>10%) | P(|move|>20%) | SD terminal $ | ATM vega/pt |')
    print('|---|---|---|---|---|---|---|---|---|---|---|---|---|')
    for v in (30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 150, 175, 200):
        s = v / 100
        strad = price('C', S, S, T, s) + price('P', S, S, T, s)
        appr = C_MAD * S * s * sT
        lo, hi = S * math.exp(-s * sT), S * math.exp(s * sT)
        sdt = S * math.sqrt(math.exp(s * s * T) - 1)
        d1 = 0.5 * s * sT
        vega = S * math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) * sT / 100
        print('| %d%% | %.4f | %.3f | %.3f | %.3f | %.2f | %.3f | %.3f | %.1f-%.1f | %.3f | %.3f | %.2f | %.4f |' % (
            v, s * s, 100 * s / SQ252, S * s / SQ252, S * s * C_MAD / SQ252, S * s * sT, strad, appr, lo, hi,
            p_beyond(s, T, 0.10), p_beyond(s, T, 0.20), sdt, vega))


def constants():
    print('\nLive inversion constants (sigma as a decimal; m = analyst number):')
    print('| quantity | formula for sigma | T-free constant | E7 (18.00d) | E8 (17.75d) | E9 (17.50d) | E10 (17.25d) |')
    print('|---|---|---|---|---|---|---|')
    Ts = [18.0, 17.75, 17.5, 17.25]
    print('| variance | sqrt(m) | - | - | - | - | - |')
    print('| daily vol (%%) | m/100 x %.4f | %.4f | | | | |' % (SQ252, SQ252))
    print('| 1-SD daily $ move | m/S x %.4f | %.4f | | | | |' % (SQ252, SQ252))
    print('| mean abs daily $ move | m/S x %.4f | %.4f | | | | |' % (SQ252 / C_MAD, SQ252 / C_MAD))
    print('| 1-SD $ move to expiry | m/S x 1/sqrt(T) | - | ' + ' | '.join('%.4f' % (1 / math.sqrt(d / 252)) for d in Ts) + ' |')
    print('| ATM straddle (approx) | m/S x 1/(0.7979 sqrt(T)) | - | ' + ' | '.join('%.4f' % (1 / (C_MAD * math.sqrt(d / 252))) for d in Ts) + ' |')
    print('| ATM straddle (exact) | 2/sqrt(T) x Ninv(0.5 + m/(4S)) | - | ' + ' | '.join('2/sqrt(T)=%.4f' % (2 / math.sqrt(d / 252)) for d in Ts) + ' |')
    print('| upper 1-SD bound U | ln(U/S)/sqrt(T) | - | same 1/sqrt(T) as above | | | |')
    print('| SD of terminal price | sqrt(ln(1+(m/S)^2)/T) | - | | | | |')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('kind')
    ap.add_argument('q', nargs='?', type=float, default=0.0)
    ap.add_argument('--S', type=float, default=100.0)
    ap.add_argument('--days', type=float, default=18.0)
    ap.add_argument('--X', type=float, default=10.0)
    ap.add_argument('--prev', type=float, default=100.0)
    ap.add_argument('--lo', type=float, default=0.0)
    ap.add_argument('--hi', type=float, default=200.0)
    ap.add_argument('--K', type=float, default=100.0)
    ap.add_argument('--S0', type=float, default=100.0)
    ap.add_argument('--S1', type=float, default=100.0)
    ap.add_argument('--eventdays', type=float, default=0.25)
    a = ap.parse_args()
    if a.kind == 'table':
        table(a.S, a.days)
        constants()
    else:
        s = invert(a.kind, a.q, S=a.S, T=a.days / 252.0, X=a.X, prev=a.prev, lo=a.lo, hi=a.hi, K=a.K,
                   S0=a.S0, S1=a.S1, eventdays=a.eventdays)
        print('IV = %.2f%%' % (100 * s))
