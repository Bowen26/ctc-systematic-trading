"""Event study across all scheduled events: formation (60 s), preliminary report (570 s), final report (740 s).

For each event and symbol:
  * move: post = median touch-mid over [te+20, te+30] s, pre = touch-mid at te-1 s; consensus move rows (te-2) -> (te+20)
  * timing (real s after the event, from the recorded frames):
      tA  first frame where the fast house layers (A = 10% of limit, B = 7%) quote at the new level
          (A/B mid has covered >= 50% of pre->post),
      tE  same for the backstop layer E (300% of limit),
      tCD same for the slow layers C (30%) and D (24%),
      t50/t90 touch-mid covers 50% / 90% of pre->post,
      cons90 first consensus row covering 90% of its own move.
  * stale liquidity: the maximum, over frames in [te, te+20], of resting size on the wrong side of the new level
      (asks below post x (1 - 0.2%) for an up-move, bids above post x (1 + 0.2%) for a down-move), the edge
      sum q x |post - p| of that size (in $, before exit costs), how long such size stayed (s), and how much of it
      other participants actually traded (trades at stale prices in [te, te+20]).
Only moves larger than max(0.5%, 3 x pre half-spread) are timed.

usage: python3 -I event_layers.py recording.jsonl season224_dir season225_dir
"""
import sys, os, math, bisect, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec

R = rec.Recording(sys.argv[1], sys.argv[2:4])
SZ = {}
for s, L in rec.LIMITS.items():
    SZ[s] = {round(0.10 * L): "A", round(0.07 * L): "B", round(0.30 * L): "C", round(0.24 * L): "D", 3 * L: "E"}
SZ["HAVEN"][18] = "B"
SZ["ARCH"] = {20: "A", 14: "B", 60: "C", 48: "D", 150: "E"}
SZ["REBLD"] = {40: "A", 28: "B", 120: "C", 96: "D", 300: "E"}

EVENTS = [("formation", 60, [(225, 1), (225, 2), (225, 3)]),
          ("prelim", 570, [(224, 3), (225, 1), (225, 2), (225, 3)]),
          ("final", 740, [(224, 3), (225, 1), (225, 2), (225, 3)])]


def frames(sym, t1, t2):
    i = max(0, bisect.bisect_right(R.book_rx[sym], t1) - 1)
    j = bisect.bisect_right(R.book_rx[sym], t2)
    return R.books[sym][i:j]


def mid_at(sym, t):
    i = bisect.bisect_right(R.book_rx[sym], t) - 1
    return rec.mid(R.books[sym][i]) if i >= 0 else None


def layer_mid(b, sym, lays):
    bp = [p for p, q in b[2] if SZ[sym].get(q, "x") in lays]
    ap = [p for p, q in b[3] if SZ[sym].get(q, "x") in lays]
    if bp and ap:
        return 0.5 * (max(bp) + min(ap))
    return None


summary = []
for name, te, keys in EVENTS:
    print("=" * 120)
    print(f"EVENT {name} at real {te} s")
    for key in keys:
        ri = R.rounds[key]
        print(f"-- season {key[0]} round {key[1]}")
        print("   sym    cons move  book move |   tA    tE   tCD   t50   t90 cons90 | stale: max qty (%L)  edge$ (at limit)  lasted s | others took qty")
        for sym in rec.SYMS:
            w0, w1 = R.wall(key, te), R.wall(key, te + 20)
            pre = mid_at(sym, R.wall(key, te - 1))
            posts = [mid_at(sym, R.wall(key, te + x)) for x in range(20, 31)]
            posts = [p for p in posts if p]
            if not pre or not posts:
                continue
            post = st.median(posts)
            c0, c1 = ri.cons(sym, te - 2), ri.cons(sym, te + 20)
            mv = post / pre - 1
            b0 = R.book_at(sym, R.wall(key, te - 1)) or frames(sym, w0 - 5, w0)[-1]
            hs = (b0[3][0][0] - b0[2][0][0]) / 2 / pre if (b0[2] and b0[3]) else 0.001
            line = f"   {sym:6s} {100*(c1/c0-1):+8.2f}% {100*mv:+8.2f}% |"
            if abs(mv) < max(0.005, 3 * hs):
                print(line + "   (move below threshold)")
                continue
            d = 1 if mv > 0 else -1
            fr = frames(sym, w0, R.wall(key, te + 40))
            def first(fn, need=0.5):
                for b in fr:
                    if b[0] < w0: continue
                    m = fn(b)
                    if m is not None and (m - pre) / (post - pre) >= need:
                        return R.real(key, b[0]) - te
                return None
            tA = first(lambda b: layer_mid(b, sym, "AB"))
            tE = first(lambda b: layer_mid(b, sym, "E"))
            tCD = first(lambda b: layer_mid(b, sym, "CD"))
            t50 = first(lambda b: rec.mid(b))
            t90 = first(lambda b: rec.mid(b), 0.9)
            ct = None
            for s in range(te - 2, te + 41, 2):
                if (ri.cons(sym, s) - c0) / ((c1 - c0) or 1e-9) >= 0.9:
                    ct = s - te; break
            # stale liquidity
            thr = post * (1 - 0.002 * d)
            best_q = best_e = 0; lasted = 0.0; prev_t = None
            for b in frames(sym, w0, w1):
                t = max(b[0], w0)
                lv = b[3] if d > 0 else b[2]
                stale = [(p, q) for p, q in lv if (p < thr if d > 0 else p > thr)]
                q = sum(x[1] for x in stale)
                e = sum(x[1] * abs(post - x[0]) for x in stale)
                if q > best_q: best_q = q
                if e > best_e: best_e = e
            # how long stale size >= 10% of limit was present (sum of frame durations within window)
            fl = frames(sym, w0, w1)
            for i, b in enumerate(fl):
                t_start = max(b[0], w0); t_end = fl[i + 1][0] if i + 1 < len(fl) else w1
                lv = b[3] if d > 0 else b[2]
                q = sum(qq for p, qq in lv if (p < thr if d > 0 else p > thr))
                if q >= 0.1 * rec.LIMITS[sym]:
                    lasted += max(0.0, t_end - t_start)
            # edge for one team at its limit: walk best stale prices first up to the limit
            L = rec.LIMITS[sym]
            best_lim = 0
            for b in frames(sym, w0, w1):
                lv = b[3] if d > 0 else b[2]
                stale = sorted([(p, q) for p, q in lv if (p < thr if d > 0 else p > thr)], key=lambda x: d * x[0])
                got = e = 0
                for p, q in stale:
                    x = min(q, L - got); got += x; e += x * abs(post - p)
                    if got >= L: break
                best_lim = max(best_lim, e)
            took = 0   # aggressor trades that took stale liquidity: buys below thr (up move) / sells above thr (down move)
            for (rx, ts, p, qn, tid) in R.trades_between(sym, w0, w1):
                j = bisect.bisect_left(R.book_rx[sym], rx - 0.0005) - 1
                mm = rec.mid(R.books[sym][j]) if j >= 0 else None
                if mm is None: continue
                if (d > 0 and p < thr and p > mm) or (d < 0 and p > thr and p < mm):
                    took += qn
            fmt = lambda x: f"{x:5.1f}" if x is not None else "    -"
            print(line + f" {fmt(tA)} {fmt(tE)} {fmt(tCD)} {fmt(t50)} {fmt(t90)} {ct if ct is not None else '-':>5} |"
                  f" {best_q:6d} ({100*best_q/L:4.0f}%) {best_e:8.0f} ({best_lim:6.0f}) {lasted:6.1f} | {took:5d}")
            summary.append((name, key, sym, mv, tA, tE, tCD, t50, t90, ct, best_q / L, best_lim, lasted, took / L))

print("=" * 120)
print("SUMMARY by event type (moves above threshold): median timing (s after event) and stale size")
for name, _, _ in EVENTS:
    for grp, syms in (("stocks", rec.STOCKS), ("ETFs", ["ARCH", "REBLD"])):
        rows = [r for r in summary if r[0] == name and r[2] in syms]
        if not rows: continue
        med = lambda i: st.median([r[i] for r in rows if r[i] is not None]) if any(r[i] is not None for r in rows) else float("nan")
        print(f"  {name:9s} {grp:6s} n={len(rows):2d}: tA {med(4):5.1f}  tE {med(5):5.1f}  tCD {med(6):5.1f}  t50 {med(7):5.1f}  t90 {med(8):5.1f}  cons90 {med(9):5.1f}"
              f" | stale max size med {100*med(10):4.0f}% of L, edge at limit med ${med(11):5.0f} (sum ${sum(r[11] for r in rows):6.0f}), lasted med {med(12):4.1f} s, others took med {100*med(13):3.0f}% L")
