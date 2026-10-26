"""Event study 4: does the ETF book lag its basket book?

A. Correlation of 2-s log changes of the ETF book mid with 2-s log changes of the basket NAV built from the
   component book mids, NAV lagged by 0..8 s (1-s grid), per round and pooled; the same on the consensus (2-s rows).
B. RMS of log(ETF_t / NAV_{t-k}) on books (bp) for k = 0..10 s (the red team's consensus test: exactly 6 s).
C. The playbook lead-lag rule on the recorded books: every 1 s, gap = (NAV - ETF mid)/ETF mid with book mids;
   enter toward +/- limit when |gap| > max(0.5%, 3 x ETF half-spread) (marketable limit, keeps at least half the
   gap: cap = mid x (1 + gap/2)); exit (walk the book) at |gap| < 0.1%, 8 s elapsed, or a gap sign change.
   Latency 0 / 1 / 2 s between decision and the book we trade on. The consensus version of the same rule
   (decision row i, fill row i+1 at ETF consensus +/- h, h = 0.25%) is run on the same rounds for comparison.

usage: python3 -I etf_lag.py recording.jsonl season224_dir season225_dir
"""
import sys, os, math, bisect, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec

R = rec.Recording(sys.argv[1], sys.argv[2:4])
KEYS = [(224, 3), (225, 1), (225, 2), (225, 3)]
W = {"ARCH": rec.ARCH_W, "REBLD": rec.REBLD_W}


def bm(sym, t):
    i = bisect.bisect_right(R.book_rx[sym], t) - 1
    return rec.mid(R.books[sym][i]) if i >= 0 else None


def bk(sym, t):
    i = bisect.bisect_right(R.book_rx[sym], t) - 1
    return R.books[sym][i] if i >= 0 else None


def navb(etf, t):
    v = 0.0
    for s, k in W[etf].items():
        m = bm(s, t)
        if m is None:
            return None
        v += k * m
    return v


def s_range(key):
    return (222 if key == (224, 3) else 2, 778)


def ok(key, s):
    return not R.in_gap(R.wall(key, s))


print("A. corr(2-s change of ETF mid at t, 2-s change of NAV at t - lag); books 1-s grid, consensus 2-s rows")
for etf in ("ARCH", "REBLD"):
    for phase, (p0, p1) in (("storm 80-540", (80, 540)), ("all 0-780", (0, 780))):
        pooled = {lag: ([], []) for lag in range(0, 9)}
        pooled_c = {lag: ([], []) for lag in range(0, 9, 2)}
        for key in KEYS:
            a, z = s_range(key); a = max(a, p0); z = min(z, p1)
            ri = R.rounds[key]
            for s in range(a + 10, z):
                for lag in range(0, 9):
                    t = [R.wall(key, x) for x in (s, s - 2, s - lag, s - lag - 2)]
                    if not all(ok(key, x) for x in (s, s - 2, s - lag, s - lag - 2)):
                        continue
                    e1, e0 = bm(etf, t[0]), bm(etf, t[1])
                    n1, n0 = navb(etf, t[2]), navb(etf, t[3])
                    if None in (e1, e0, n1, n0):
                        continue
                    pooled[lag][0].append(math.log(e1 / e0)); pooled[lag][1].append(math.log(n1 / n0))
            for s in range(a + 10, z, 2):
                for lag in range(0, 9, 2):
                    e1, e0 = ri.cons(etf, s), ri.cons(etf, s - 2)
                    p1_ = {x: ri.cons(x, s - lag) for x in W[etf]}; p0_ = {x: ri.cons(x, s - lag - 2) for x in W[etf]}
                    n1, n0 = rec.nav(p1_, etf), rec.nav(p0_, etf)
                    pooled_c[lag][0].append(math.log(e1 / e0)); pooled_c[lag][1].append(math.log(n1 / n0))
        rb = " ".join(f"{lag}:{st.correlation(*pooled[lag]):+.2f}" for lag in range(0, 9))
        rc = " ".join(f"{lag}:{st.correlation(*pooled_c[lag]):+.2f}" for lag in range(0, 9, 2))
        print(f"   {etf:5s} {phase:13s} books  {rb}   (n={len(pooled[0][0])})")
        print(f"   {etf:5s} {phase:13s} consensus  {rc}")

print()
print("B. RMS of log(ETF_t / NAV_(t-k)) in bp, books (1-s grid) vs consensus (rows), storm 80-540 s pooled over rounds")
for etf in ("ARCH", "REBLD"):
    rb, rc = [], []
    for k in range(0, 11):
        xs, cs = [], []
        for key in KEYS:
            a, z = s_range(key); a = max(a, 80); z = min(z, 540)
            ri = R.rounds[key]
            for s in range(a + 12, z):
                if not (ok(key, s) and ok(key, s - k)):
                    continue
                e, n = bm(etf, R.wall(key, s)), navb(etf, R.wall(key, s - k))
                if e and n:
                    xs.append(1e4 * math.log(e / n))
                if s % 2 == 0 and k % 2 == 0:
                    pc = {x: ri.cons(x, s - k) for x in W[etf]}
                    cs.append(1e4 * math.log(ri.cons(etf, s) / rec.nav(pc, etf)))
        rb.append(f"{k}:{math.sqrt(st.mean(x*x for x in xs)):.0f}")
        if cs:
            rc.append(f"{k}:{math.sqrt(st.mean(x*x for x in cs)):.0f}")
    print(f"   {etf:5s} books: " + " ".join(rb))
    print(f"   {etf:5s} cons : " + " ".join(rc))
    # mean premium (ETF vs same-time NAV) on books, by round
    for key in KEYS:
        a, z = s_range(key)
        xs = [1e4 * math.log(bm(etf, R.wall(key, s)) / navb(etf, R.wall(key, s))) for s in range(a, z) if ok(key, s)]
        print(f"      {key} ETF book premium over same-time book NAV: mean {st.mean(xs):+.0f} bp, sd {st.pstdev(xs):.0f} bp")

print()
print("C. Lead-lag rule PnL on the books vs on the consensus (same rounds)")
H = 0.0025


def run_book(etf, key, lat):
    L = rec.LIMITS[etf]
    T = rec.Taker(R)
    a, z = s_range(key)
    pos_open = None   # (sign, t_entry)
    trades = 0
    for s in range(a + 2, min(z, 776)):
        t = R.wall(key, s)
        if not ok(key, s) or not ok(key, s + lat):
            continue
        b = bk(etf, t); n = navb(etf, t)
        if b is None or n is None or not b[2] or not b[3]:
            continue
        m = 0.5 * (b[2][0][0] + b[3][0][0]); hs = (b[3][0][0] - b[2][0][0]) / 2 / m
        gap = (n - m) / m
        pos = T.pos.get(etf, 0)
        if pos != 0:
            sg = 1 if pos > 0 else -1
            if abs(gap) < 0.001 or s - pos_open[1] >= 8 or gap * sg < 0:
                T.to_target(etf, 0, R.wall(key, s + lat), None)
                if T.pos.get(etf, 0) == 0:
                    pos_open = None
                continue
        if pos == 0 and abs(gap) > max(0.005, 3 * hs):
            sg = 1 if gap > 0 else -1
            cap = m * (1 + gap / 2)
            got, _ = T.to_target(etf, sg * L, R.wall(key, s + lat), cap)
            if got:
                pos_open = (sg, s); trades += 1
    if T.pos.get(etf, 0):
        T.to_target(etf, 0, R.wall(key, min(z, 778)), None)
    return T.pnl_sym(etf), trades, T


def run_cons(etf, key, h=H, lat_rows=1):
    ri = R.rounds[key]; L = rec.LIMITS[etf]
    a, z = s_range(key)
    pos = 0; ent = None; cash = 0.0; n_tr = 0
    for s in range(a + (a % 2), min(z, 776), 2):
        pc = {x: ri.cons(x, s) for x in W[etf]}
        e = ri.cons(etf, s); n = rec.nav(pc, etf); gap = (n - e) / e
        fill = ri.cons(etf, s + 2 * lat_rows)
        if fill is None:
            break
        if pos:
            sg = 1 if pos > 0 else -1
            if abs(gap) < 0.001 or s - ent >= 8 or gap * sg < 0:
                cash += pos * fill * (1 - sg * h); pos = 0
            continue
        if abs(gap) > max(0.005, 3 * h):
            sg = 1 if gap > 0 else -1
            pos = sg * L; ent = s; n_tr += 1
            cash -= pos * fill * (1 + sg * h)
    if pos:
        cash += pos * ri.cons(etf, min(z, 778)) * (1 - (1 if pos > 0 else -1) * h)
    return cash, n_tr


for etf in ("ARCH", "REBLD"):
    print(f"   {etf}: round | books lat 0 s (trades) | lat 1 s | lat 2 s || consensus h=0.25% next row (trades) | +1 row | h=0.10% next row")
    tot = [0.0] * 6
    for key in KEYS:
        r0, n0, T0 = run_book(etf, key, 0)
        r1, n1, _ = run_book(etf, key, 1)
        r2, n2, _ = run_book(etf, key, 2)
        c0, m0 = run_cons(etf, key)
        c1, m1 = run_cons(etf, key, lat_rows=2)
        c2, m2 = run_cons(etf, key, h=0.001)
        for i, v in enumerate((r0, r1, r2, c0, c1, c2)):
            tot[i] += v
        print(f"      {key}: {r0:+8.0f} ({n0:3d}) | {r1:+8.0f} ({n1:3d}) | {r2:+8.0f} ({n2:3d}) || {c0:+8.0f} ({m0:3d}) | {c1:+8.0f} ({m1:3d}) | {c2:+8.0f} ({m2:3d})")
    print(f"      TOTAL: books {tot[0]:+.0f} / {tot[1]:+.0f} / {tot[2]:+.0f}   consensus {tot[3]:+.0f} / {tot[4]:+.0f} / {tot[5]:+.0f}")

print()
print("D. ETF touch half-spread (bp) on books, median and p90 by phase, pooled rounds")
for etf in ("ARCH", "REBLD"):
    for ph, (p0, p1) in (("pre 0-60", (0, 60)), ("storm 60-540", (60, 540)), ("after 540-780", (540, 780))):
        xs = []
        for key in KEYS:
            a, z = s_range(key)
            for s in range(max(a, p0), min(z, p1)):
                if not ok(key, s):
                    continue
                b = bk(etf, R.wall(key, s))
                if b and b[2] and b[3]:
                    xs.append(1e4 * (b[3][0][0] - b[2][0][0]) / (b[3][0][0] + b[2][0][0]))
        xs.sort()
        print(f"   {etf:5s} {ph:14s} median {xs[len(xs)//2]:5.1f}  p90 {xs[9*len(xs)//10]:5.1f}  (n={len(xs)})")

print()
print("E. Formation check (playbook 4.5 gate): ETF book vs its basket book NAV at 58-84 s; gap = NAV/ETF mid - 1;")
print("   'size' = ETF ask size (if gap > 0) or bid size (gap < 0) priced better than NAV x (1 -/+ 0.3%), as % of limit")
for key in [(225, 1), (225, 2), (225, 3)]:
    for etf in ("ARCH", "REBLD"):
        row = []
        for s in (58, 62, 64, 66, 68, 70, 72, 74, 76, 78, 80, 84):
            t = R.wall(key, s)
            b = bk(etf, t); n = navb(etf, t); m = rec.mid(b)
            g = n / m - 1
            if g > 0:
                sz = sum(q for p, q in b[3] if p < n * 0.997)
            else:
                sz = sum(q for p, q in b[2] if p > n * 1.003)
            row.append(f"{s}:{100*g:+.2f}%/{100*sz/rec.LIMITS[etf]:.0f}%")
        print(f"   {key} {etf:5s} " + " ".join(row))
