"""Event study 2: the in-storm drift (real 74 -> 538 s) in the books vs the consensus, and the clock-only
drift trade priced on the books.

Drift trade (playbook 4.7 fallback, stand-alone): short PORT ISLE AQUA GRID FERRY HAVEN ARCH, long RECON MAT GEN
REBLD; enter in four L/4 clips at 80, 82, 84, 86 s (marketable limit capped 0.5% through the touch at that time),
exit in four clips at 532, 534, 536, 538 s walking the book (no cap). Book fills deplete a frame until the next
frame of that symbol. Consensus estimate: playbook RT model (next row, cons x (1 +/- (h + gamma*q/L)), h 0.25%, gamma 0.5%).

usage: python3 -I drift.py recording.jsonl season224_dir season225_dir
"""
import sys, os, math, bisect, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec

R = rec.Recording(sys.argv[1], sys.argv[2:4])
SHORT = ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "HAVEN", "ARCH"]
LONG = ["RECON", "MAT", "GEN", "REBLD"]
SY = SHORT + LONG
DIR = {s: (-1 if s in SHORT else 1) for s in SY}
H, G = 0.0025, 0.005
RESEARCH_DRIFT = {"HAVEN": -7.3, "ARCH": -2.8, "FERRY": -2.9, "GRID": -2.4, "PORT": -2.3, "AQUA": -1.8, "ISLE": -1.3,
                  "GEN": +1.3, "RECON": +1.0, "REBLD": +1.0, "MAT": +1.0, "REIN": -0.3}   # playbook 3.2, 3-block means


def bm(key, sym, s):
    i = bisect.bisect_right(R.book_rx[sym], R.wall(key, s)) - 1
    return rec.mid(R.books[sym][i]) if i >= 0 else None


def touch(key, sym, s):
    i = bisect.bisect_right(R.book_rx[sym], R.wall(key, s)) - 1
    b = R.books[sym][i]
    return (b[2][0][0] if b[2] else None), (b[3][0][0] if b[3] else None)


print("A. Drift 74 -> 538 s: % change of book touch-mid and of consensus; research mean (playbook 3.2) for reference")
print("   sym     research |" + " | ".join(f"  s{k[0]}r{k[1]} cons    book " for k in [(225, 1), (225, 2), (225, 3)]) + " | s224r3 (222->538) cons book")
for sym in rec.SYMS:
    row = f"   {sym:6s} {RESEARCH_DRIFT[sym]:+7.1f}% |"
    for key in [(225, 1), (225, 2), (225, 3)]:
        ri = R.rounds[key]
        row += f" {100*(ri.cons(sym, 538)/ri.cons(sym, 74)-1):+7.2f}% {100*(bm(key, sym, 538)/bm(key, sym, 74)-1):+7.2f}% |"
    ri = R.rounds[(224, 3)]
    row += f" {100*(ri.cons(sym, 538)/ri.cons(sym, 222)-1):+7.2f}% {100*(bm((224, 3), sym, 538)/bm((224, 3), sym, 222)-1):+7.2f}%"
    print(row)

print()
print("B. Book-mid path vs consensus during the storm (correlation of 10-s log changes, 80-530 s; RMS gap bp)")
for key in [(225, 1), (225, 2), (225, 3), (224, 3)]:
    ri = R.rounds[key]
    s0 = 80 if key != (224, 3) else 230
    out = []
    for sym in rec.SYMS:
        xs, ys, gap = [], [], []
        for s in range(s0, 530, 10):
            if R.in_gap(R.wall(key, s)) or R.in_gap(R.wall(key, s + 10)):
                continue
            b0, b1 = bm(key, sym, s), bm(key, sym, s + 10)
            c0, c1 = ri.cons(sym, s), ri.cons(sym, s + 10)
            xs.append(math.log(b1 / b0)); ys.append(math.log(c1 / c0)); gap.append(1e4 * math.log(b0 / c0))
        try:
            r = st.correlation(xs, ys)
        except Exception:
            r = float("nan")
        out.append(f"{sym} {r:+.2f}/{math.sqrt(st.mean(g*g for g in gap)):.0f}")
    print(f"   {key}: " + "  ".join(out))

print()
print("C. Clock-only drift trade on the books vs the consensus RT estimate (full limits)")


def drift_book(key, lat=0.0):
    T = rec.Taker(R)
    for k, t in enumerate([80, 82, 84, 86], 1):
        for s in SY:
            b, a = touch(key, s, t + lat)
            cap = (a * 1.005) if DIR[s] > 0 else (b * 0.995)
            T.to_target(s, DIR[s] * round(k * rec.LIMITS[s] / 4), R.wall(key, t + lat), cap)
    pin = dict(T.pos)
    for k, t in enumerate([532, 534, 536, 538], 1):
        for s in SY:
            T.to_target(s, round(pin.get(s, 0) * (1 - k / 4)), R.wall(key, t + lat), None)
    return T, pin


def drift_cons(key):
    ri = R.rounds[key]; out = {}
    for s in SY:
        L = rec.LIMITS[s]; d = DIR[s]; clip = round(L / 4); cash = 0.0
        for t in (80, 82, 84, 86):
            cash -= d * clip * ri.cons(s, t + 2) * (1 + d * (H + G * clip / L))
        for t in (532, 534, 536, 538):
            cash += d * clip * ri.cons(s, t + 2) * (1 - d * (H + G * clip / L))
        out[s] = cash
    return out


grand = {"book": 0.0, "cons": 0.0}
for key in [(225, 1), (225, 2), (225, 3)]:
    T, pin = drift_book(key)
    C = drift_cons(key)
    print(f"-- season {key[0]} round {key[1]}:  sym fill/target  avg entry | avg exit | book PnL | cons PnL")
    tb = tc = 0
    for s in SY:
        ent = [f for f in T.fills if f[1] == s and R.real(key, f[0]) < 300]
        ex = [f for f in T.fills if f[1] == s and R.real(key, f[0]) > 300]
        eq = sum(abs(f[2]) for f in ent); xq = sum(abs(f[2]) for f in ex)
        ea = sum(abs(f[2]) * f[3] for f in ent) / eq if eq else float("nan")
        xa = sum(abs(f[2]) * f[3] for f in ex) / xq if xq else float("nan")
        pb = T.pnl_sym(s); tb += pb; tc += C[s]
        print(f"     {s:6s} {DIR[s]*eq:+6d}/{DIR[s]*rec.LIMITS[s]:+5d} {ea:9.2f} | {xa:9.2f} | {pb:+8.0f} | {C[s]:+8.0f}")
    print(f"     TOTAL book {tb:+.0f}  consensus RT {tc:+.0f}")
    grand["book"] += tb; grand["cons"] += tc
print(f"   3-round total: book {grand['book']:+.0f}, consensus RT {grand['cons']:+.0f}")

print()
print("D. Book drift vs consensus drift 74 -> 538 s, 225 r1-r3, all 12 symbols: sign agreement and slope")
xs, ys = [], []
for key in [(225, 1), (225, 2), (225, 3)]:
    ri = R.rounds[key]
    for sym in rec.SYMS:
        xs.append(100 * (ri.cons(sym, 538) / ri.cons(sym, 74) - 1)); ys.append(100 * (bm(key, sym, 538) / bm(key, sym, 74) - 1))
agree = sum(1 for x, y in zip(xs, ys) if x * y > 0)
mx, my = st.mean(xs), st.mean(ys)
b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
print(f"   same sign {agree}/{len(xs)}; slope book-on-consensus {b:.2f}; corr {st.correlation(xs, ys):.2f}; mean |book - cons| {st.mean(abs(x-y) for x, y in zip(xs, ys)):.2f} pp")
for key in [(225, 1), (225, 2), (225, 3)]:
    ri = R.rounds[key]
    d = [abs(100 * (bm(key, s, 538) / bm(key, s, 74) - 1) - 100 * (ri.cons(s, 538) / ri.cons(s, 74) - 1)) for s in rec.SYMS]
    print(f"   {key}: mean |book - cons| drift {st.mean(d):.2f} pp")
