"""CTC 2026 Derivatives live calculator (pricing aid only, sends no orders). Stdlib only.

EVENT (click list).  Orders are priced EXACTLY at each bot level (robust to every fill rule):
  python3 -I deriv_live.py event --S0 100 --v0 99 --S1 110 --v1 120 --days 19.67
  python3 -I deriv_live.py event --S1 110 --v1 120 --days 19.67 --quotes "60C=39.28/41.29,100C=10.09/12.10,140C=0.81/2.82,60P=/1.29,100P=10.09/12.10,140P=40.81/42.82"
  R2/R3 (IV uncertain): give candidate IVs (+ optional probabilities). Shows EV and worst case per level:
  python3 -I deriv_live.py event --S0 100 --v0 80 --S1 100 --cands 92,99,70 --probs 0.45,0.45,0.10 --days 18.33
  Window B (bots already at new S, old vol):  add --window B
  PLANE: add --K 30,50,70 (any strikes)
IV READOUT after the snap (parity spot, immune to the IV tab using the live tick):
  python3 -I deriv_live.py iv --K 100 --C 11.095 --P 11.095 --days 19.73 [--Stab 99.89]
CHEAT SHEET:
  python3 -I deriv_live.py sheet --days 19 [--K 60,100,140]
"""
import math, argparse

def N(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def n(x): return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

def bs(kind, S, K, days, vol):
    T = days / 252.0
    if vol <= 0 or T <= 0:
        return max(S - K, 0) if kind == 'C' else max(K - S, 0)
    w = vol * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * w * w) / w
    return S * N(d1) - K * N(d1 - w) if kind == 'C' else K * N(w - d1) - S * N(-d1)

def gk(kind, S, K, days, vol):
    T = days / 252.0
    w = vol * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * w * w) / w
    return (N(d1) if kind == 'C' else N(d1) - 1), S * n(d1) * math.sqrt(T) / 100

def iv(kind, px, S, K, days):
    lo, hi = 0.005, 6.0
    for _ in range(100):
        m = 0.5 * (lo + hi)
        if bs(kind, S, K, days, m) < px: lo = m
        else: hi = m
    return 0.5 * (lo + hi)

def bot_quote(fair):
    """Matches the screenshot exactly: bid = floor(fair-1.00), ask = ceil(fair+1.00)."""
    b = math.floor((fair - 1.0) * 100 + 1e-9) / 100
    a = math.ceil((fair + 1.0) * 100 - 1e-9) / 100
    return (b if b > 0 else None), a

def event(a):
    Ks = [float(k) for k in a.K.split(',')]
    if a.cands:
        vs = [float(x) / 100 for x in a.cands.split(',')]
        ps = [float(x) for x in a.probs.split(',')] if a.probs else [1.0 / len(vs)] * len(vs)
        ps = [p / sum(ps) for p in ps]
    else:
        vs, ps = [a.v1 / 100], [1.0]
    q = {}
    for part in filter(None, a.quotes.split(',')):
        name, ba = part.split('=')
        b, s = ba.split('/')
        q[name.strip()] = (float(b) if b.strip() else None, float(s) if s.strip() else None)
    Sq = a.S0 if a.window == 'A' else a.S1
    rows = []
    for kind in ('C', 'P'):
        for K in Ks:
            name = '%g%s' % (K, kind)
            bid, ask = q[name] if name in q else bot_quote(bs(kind, Sq, K, a.days, a.v0 / 100))
            fairs = [bs(kind, a.S1, K, a.days, v) for v in vs]
            ef = sum(p * f for p, f in zip(ps, fairs))
            buy = [(ask + k * a.step) for k in range(a.levels)] if ask else []
            sell = [(bid - k * a.step) for k in range(a.levels)] if bid else []
            bl = [(px, sum(p * (f - px) for p, f in zip(ps, fairs)), min(f - px for f in fairs)) for px in buy]
            sl = [(px, sum(p * (px - f) for p, f in zip(ps, fairs)), min(px - f for f in fairs)) for px in sell]
            side, lv = ('BUY', bl) if (bl and (not sl or bl[0][1] >= sl[0][1])) else ('SELL', sl)
            take = [(px, ev, wc) for px, ev, wc in lv if ev > a.buffer and (wc > 0 or not a.robust)]
            delta, vega = gk(kind, a.S1, K, a.days, sum(p * v for p, v in zip(ps, vs)))
            # vol-free part of the edge: fair at OLD vol and new S minus the quote mid
            mid = 0.5 * ((bid if bid else 0) + ask) if bid else ask - 1.005
            dpart = bs(kind, a.S1, K, a.days, a.v0 / 100) - mid
            rows.append((sum(ev for _, ev, _ in take) * a.lot, name, ef, bid, ask, side, lv, take, delta, vega, dpart))
    rows.sort(key=lambda r: -r[0])
    print('Window %s | quotes at S=%.2f v=%.1f%% | fair at S1=%.2f, IV %s | T=%.3f d | buffer %.2f%s' % (
        a.window, Sq, a.v0, a.S1, '/'.join('%.1f(p%.2f)' % (100 * v, p) for v, p in zip(vs, ps)), a.days,
        a.buffer, ' | ROBUST: worst-case>0 required' if a.robust else ''))
    print('%-5s %7s %13s %5s  %-44s %8s %6s %6s %7s' % ('ctr', 'E[fair]', 'bid/ask', 'side', 'levels: price  EV  worst', '$take', 'delta', 'vega', 'volfree'))
    nd = nv = tot = 0.0
    for cap, name, ef, bid, ask, side, lv, take, delta, vega, dpart in rows:
        lvs = ' | '.join('%.2f %+.2f %+.2f' % x for x in lv)
        qty = a.lot * len(take)
        sg = 1 if side == 'BUY' else -1
        nd += sg * qty * delta; nv += sg * qty * vega; tot += cap
        print('%-5s %7.2f %6s/%6.2f %5s  %-44s %8.0f %+6.3f %6.3f %+7.2f%s' % (
            name, ef, '-' if bid is None else '%.2f' % bid, ask, side, lvs, cap, delta, vega, dpart,
            '' if take else '  skip'))
        for px, ev, wc in take:
            print('        -> %s %d @ %.2f   (EV %+.2f/lot, worst %+.2f)' % (side, a.lot, px, ev, wc))
    print('BASKET: EV $%.0f | net delta %+.0f | net vega %+.1f per vol pt   (cap each line at your position limit)' % (tot, nd, nv))

def ivread(a):
    S = a.K + a.C - a.P
    vc = iv('C', a.C, S, a.K, a.days); vp = iv('P', a.P, S, a.K, a.days)
    print('parity spot S = K + C - P = %.3f | IV(call) %.2f%% IV(put) %.2f%% at that spot' % (S, 100 * vc, 100 * vp))
    if a.Stab:
        c2 = iv('C', a.C, a.Stab, a.K, a.days); p2 = iv('P', a.P, a.Stab, a.K, a.days)
        d = gk('C', a.Stab, a.K, a.days, 0.5 * (c2 + p2))[0]
        print('IV tab at spot %.2f would show call %.2f%% / put %.2f%%; delta-weighted fix %.2f%%' % (
            a.Stab, 100 * c2, 100 * p2, 100 * ((1 - d) * c2 + d * p2)))

def sheet(a):
    Ks = [float(k) for k in a.K.split(',')]
    vols = [60, 80, 100, 120, 140]
    for K in Ks:
        print('CALL K=%g, T=%g d  (put = call - S + K)' % (K, a.days))
        print('   S  ' + ''.join('%8d%%' % v for v in vols))
        for S in range(60, 145, 5):
            print('%5d ' % S + ''.join('%9.2f' % bs('C', S, K, a.days, v / 100) for v in vols))

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd')
    e = sp.add_parser('event')
    e.add_argument('--S0', type=float, default=100.0); e.add_argument('--v0', type=float, default=100.0)
    e.add_argument('--S1', type=float, required=True); e.add_argument('--v1', type=float, default=None)
    e.add_argument('--cands', default=''); e.add_argument('--probs', default='')
    e.add_argument('--days', type=float, required=True); e.add_argument('--K', default='60,100,140')
    e.add_argument('--window', default='A', choices=['A', 'B']); e.add_argument('--quotes', default='')
    e.add_argument('--step', type=float, default=1.0); e.add_argument('--levels', type=int, default=3)
    e.add_argument('--lot', type=int, default=200); e.add_argument('--buffer', type=float, default=0.10)
    e.add_argument('--robust', action='store_true', help='also require worst-case edge > 0')
    i = sp.add_parser('iv')
    i.add_argument('--K', type=float, required=True); i.add_argument('--C', type=float, required=True)
    i.add_argument('--P', type=float, required=True); i.add_argument('--days', type=float, required=True)
    i.add_argument('--Stab', type=float, default=None)
    s = sp.add_parser('sheet'); s.add_argument('--days', type=float, default=19.0); s.add_argument('--K', default='60,100,140')
    a = ap.parse_args()
    if a.cmd == 'event':
        if a.v1 is None and not a.cands: ap.error('give --v1 or --cands')
        event(a)
    elif a.cmd == 'iv': ivread(a)
    elif a.cmd == 'sheet': sheet(a)
    else: ap.print_help()
