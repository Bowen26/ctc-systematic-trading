"""How the house layers react to being hit, and what a worked clip costs on the books (depth cost gamma).

A. Refill: whenever a trade removes a full-size layer order (A/B/C/D/E by order size), find the next frame where an
   order of that layer's size is back on the same side; report delay (s) and price change in the direction against
   the taker (bp), by layer, pooled over symbols and rounds, quiet phases only (0-58, 120-530 excluding 60 s after
   each event, 600-736 s) and around events.
B. Cost of a marketable clip on the recorded books: for q = L/8, L/4, L/2, L (one shot, no depletion beyond the
   frame), average cost vs touch-mid in bp, by phase (pre 0-58, storm 80-530, after 600-736), median over 1-s samples.
   Compared with the playbook RT model h + gamma*q/L (h = 0.25%, gamma = 0.5%).
C. Permanent impact of others' flow: regression of the 10-s change in book touch-mid (bp) on signed trade volume
   in that 10 s (as fraction of the limit), quiet phases, pooled over stocks.

usage: python3 -I refill_impact.py recording.jsonl season224_dir season225_dir
"""
import sys, os, bisect, math, statistics as st, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec

R = rec.Recording(sys.argv[1], sys.argv[2:4])
KEYS = [(224, 3), (225, 1), (225, 2), (225, 3)]
SZ = {}
for s, L in rec.LIMITS.items():
    SZ[s] = {round(0.10 * L): "A", round(0.07 * L): "B", round(0.30 * L): "C", round(0.24 * L): "D", 3 * L: "E"}
SZ["HAVEN"][18] = "B"
SZ["ARCH"] = {20: "A", 14: "B", 60: "C", 48: "D", 150: "E"}
SZ["REBLD"] = {40: "A", 28: "B", 120: "C", 96: "D", 300: "E"}
EVT = [60, 570, 740]


def phase(s):
    if any(e <= s < e + 60 for e in EVT):
        return "event"
    if s < 58 or 120 <= s < 530 or 600 <= s < 736:
        return "quiet"
    return "other"


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else float("nan")


print("A. Layer refill after being traded through (delay s / price move against the taker, bp)")
ref = collections.defaultdict(list)
for key in KEYS:
    for sym in rec.SYMS:
        bl = R.books_between(sym, R.wall(key, 0), R.wall(key, 780))
        for i in range(1, len(bl)):
            prev, cur = bl[i - 1], bl[i]
            for side, idx in (("ask", 3), ("bid", 2)):
                had = {SZ[sym].get(qq): p for p, qq in prev[idx] if SZ[sym].get(qq)}
                now = {SZ[sym].get(qq): p for p, qq in cur[idx] if SZ[sym].get(qq)}
                for lay, p in had.items():
                    if lay in now:
                        continue
                    # was it traded? a trade at price p within 50 ms of the frame
                    tr = R.trades_between(sym, cur[0] - 0.05, cur[0] + 0.001)
                    if not any(abs(t[2] - p) < 1e-9 for t in tr):
                        continue
                    for j in range(i + 1, min(len(bl), i + 200)):
                        nb = bl[j]
                        nw = [pp for pp, qq in nb[idx] if SZ[sym].get(qq) == lay]
                        if nw:
                            dly = nb[0] - cur[0]
                            mvbp = 1e4 * (nw[0] - p) / p * (1 if side == "ask" else -1)
                            ph = phase(R.real(key, cur[0]))
                            ref[(lay, ph)].append((dly, mvbp))
                            break
print("   layer phase  n     delay p50  p90 | price move bp p10 p50 p90 | share re-posted at the same price")
for lay in "ABCDE":
    for ph in ("quiet", "event"):
        v = ref.get((lay, ph), [])
        if not v:
            continue
        d = [x[0] for x in v]; m = [x[1] for x in v]
        same = sum(1 for x in m if abs(x) < 0.5) / len(m)
        print(f"   {lay}     {ph:6s} {len(v):4d}  {q(d,.5):6.2f} {q(d,.9):6.2f} | {q(m,.1):+6.1f} {q(m,.5):+6.1f} {q(m,.9):+6.1f} | {same:.2f}")

print()
print("B. One-shot marketable cost vs touch-mid (bp), median over 1-s samples; RT model h + gamma q/L in brackets")
FR = [(0.125, "L/8"), (0.25, "L/4"), (0.5, "L/2"), (1.0, "L")]
for grp, syms in (("stocks", rec.STOCKS), ("ETFs", ["ARCH", "REBLD"])):
    for ph, (p0, p1) in (("pre 2-58", (2, 58)), ("storm 80-530", (80, 530)), ("after 600-736", (600, 736))):
        res = {f: [] for f, _ in FR}; unfilled = {f: 0 for f, _ in FR}; n = 0
        for key in KEYS:
            a = 222 if key == (224, 3) else 0
            for s in range(max(a, p0), p1):
                t = R.wall(key, s)
                if R.in_gap(t):
                    continue
                for sym in syms:
                    i = bisect.bisect_right(R.book_rx[sym], t) - 1
                    b = R.books[sym][i]
                    m = rec.mid(b)
                    if not m:
                        continue
                    n += 1
                    L = rec.LIMITS[sym]
                    for f, _ in FR:
                        qq = max(1, round(f * L))
                        for side, lv in (("buy", b[3]), ("sell", b[2])):
                            avg, got = rec.walk(lv, qq)
                            if got < qq:
                                unfilled[f] += 1
                                continue
                            res[f].append(1e4 * abs(avg - m) / m)
        print(f"   {grp:6s} {ph:14s} " + "  ".join(f"{lab}: {q(res[f],.5):5.1f} [{25 + 50*f:4.1f}] p90 {q(res[f],.9):5.0f}" for f, lab in FR)
              + f"  (n={n}; clips not fillable within 5 levels: " + ", ".join(f"{lab} {unfilled[f]}" for f, lab in FR) + ")")

print()
print("C. Impact of others' net flow: 10-s change of touch-mid (bp) vs signed volume in that window (fraction of L)")
xs, ys = [], []
for key in KEYS:
    a = 230 if key == (224, 3) else 0
    for sym in rec.STOCKS:
        bl_rx = R.book_rx[sym]
        for s in range(a, 770, 10):
            if phase(s) != "quiet" or phase(s + 10) != "quiet":
                continue
            t0, t1 = R.wall(key, s), R.wall(key, s + 10)
            if R.in_gap(t0) or R.in_gap(t1):
                continue
            i0 = bisect.bisect_right(bl_rx, t0) - 1; i1 = bisect.bisect_right(bl_rx, t1 + 2.0) - 1
            m0, m1 = rec.mid(R.books[sym][i0]), rec.mid(R.books[sym][i1])
            if not (m0 and m1):
                continue
            v = 0
            for (rx, ts, p, qn, tid) in R.trades_between(sym, t0, t1):
                j = bisect.bisect_left(bl_rx, rx - 0.0005) - 1
                mm = rec.mid(R.books[sym][j]) if j >= 0 else None
                if mm:
                    v += qn if p > mm else -qn if p < mm else 0
            xs.append(v / rec.LIMITS[sym]); ys.append(1e4 * math.log(m1 / m0))
mx, my = st.mean(xs), st.mean(ys)
b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
r = st.correlation(xs, ys)
print(f"   n={len(xs)} windows; slope {b:+.1f} bp per 1.0 L of net buying (mid 2 s after the window), corr {r:+.2f};"
      f" |flow| p90 {q([abs(x) for x in xs], .9):.2f} L")
