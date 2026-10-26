"""Stdlib-only Black-Scholes (r = 0) for the CTC 2026 derivatives case.

Conventions (match the exchange UI):
  * T in years on a 252-trading-day basis (20 trading days -> 20/252 = 0.0794).
  * vol as a decimal (1.00 = 100%).
  * vega reported per 1 vol point (i.e. dPrice / dSigma / 100).
  * theta reported per trading day (dPrice / dT / 252, sign negative for long).
"""
import math

SQRT2 = math.sqrt(2.0)
DAYS = 252.0


def ncdf(x):
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def npdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def ninv(p):
    """Inverse standard normal CDF by bisection (stdlib only, plenty fast)."""
    if p <= 0.0:
        return -40.0
    if p >= 1.0:
        return 40.0
    lo, hi = -40.0, 40.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if ncdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def d1d2(S, K, T, vol):
    w = vol * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * w * w) / w
    return d1, d1 - w


def price(kind, S, K, T, vol):
    if T <= 0 or vol <= 0:
        return max(S - K, 0.0) if kind == 'C' else max(K - S, 0.0)
    d1, d2 = d1d2(S, K, T, vol)
    if kind == 'C':
        return S * ncdf(d1) - K * ncdf(d2)
    return K * ncdf(-d2) - S * ncdf(-d1)


def greeks(kind, S, K, T, vol):
    d1, d2 = d1d2(S, K, T, vol)
    sq = math.sqrt(T)
    delta = ncdf(d1) if kind == 'C' else ncdf(d1) - 1.0
    gamma = npdf(d1) / (S * vol * sq)
    vega = S * npdf(d1) * sq / 100.0            # per vol point
    theta = -S * npdf(d1) * vol / (2.0 * sq) / DAYS  # per trading day (r=0: same for C and P)
    return {'price': price(kind, S, K, T, vol), 'delta': delta, 'gamma': gamma,
            'vega': vega, 'theta': theta}


def implied_vol(kind, px, S, K, T, lo=1e-4, hi=10.0):
    intrinsic = max(S - K, 0.0) if kind == 'C' else max(K - S, 0.0)
    if px <= intrinsic + 1e-12:
        return None
    if px >= (S if kind == 'C' else K):
        return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if price(kind, S, K, T, mid) < px:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def T_of(days):
    return days / DAYS


# Remaining trading days at each event's news / underlying-move / snap, derived from the
# packet's exchange-time schedule (trading day 09:30-16:00 = 390 exchange minutes;
# expiry Jun 26 16:00; 20.00 trading days remain at Jun 1 09:30 per the UI).
def days_left(round_no, hhmmss):
    h, m, s = [int(x) for x in hhmmss.split(':')]
    minutes = (h * 60 + m + s / 60.0) - (9 * 60 + 30)
    start = {1: 20.0, 2: 19.0, 3: 18.0}[round_no]
    return start - minutes / 390.0


SCHEDULE = {
    1: [('E1', '09:30:00', '10:22:00', '11:14:00'),
        ('E2', '11:40:00', '12:32:00', '13:24:00'),
        ('E3', '13:50:00', '14:42:00', '15:34:00')],
    2: [('E4', '09:30:00', '10:22:00', '11:14:00'),
        ('E5', '11:40:00', '12:32:00', '13:24:00'),
        ('E6', '13:50:00', '14:42:00', '15:34:00')],
    3: [('E7', '09:30:00', '10:09:00', '10:48:00'),
        ('E8', '11:07:30', '11:46:30', '12:25:30'),
        ('E9', '12:45:00', '13:24:00', '14:03:00'),
        ('E10', '14:22:30', '15:01:30', '15:40:30')],
}

CONTRACTS = [('C', 60.0), ('C', 100.0), ('C', 140.0), ('P', 60.0), ('P', 100.0), ('P', 140.0)]


def cname(kind, K):
    return '%d%s' % (int(K), kind)
