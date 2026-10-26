"""Event study 1: the formation jump (storm forms at real 60 s) in the recorded books vs the research consensus,
and the playbook H1 trade priced on the recorded books.

usage: python3 -I formation.py recording.jsonl season224_dir season225_dir

H1 as in the playbook: long PORT ISLE AQUA GRID FERRY HAVEN ARCH, short RECON MAT GEN REBLD.
Entry clips at real 50, 52, 54, 56 s, target k/4 of the limit, marketable limit capped at the 48-s book mid x (1 +/- 0.5%).
Exit clips at 72, 74, 76, 78 s to flat, walking the book (no cap; whatever is left at 78 s is forced at the 78-s book).
Book fills: walk the recorded levels at the decision time (+ latency); our fills deplete a frame until that symbol's next
frame. Consensus estimate (playbook RT fill model): fill at the next 2-s row at consensus x (1 +/- (h + gamma/4)),
h = 0.25%, gamma = 0.5%.
"""
import sys, os, math, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec

R = rec.Recording(sys.argv[1], sys.argv[2:4])
LONG = ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "HAVEN", "ARCH"]
SHORT = ["RECON", "MAT", "GEN", "REBLD"]
H1 = LONG + SHORT
DIR = {s: (1 if s in LONG else -1) for s in H1}
ROUNDS = [(225, 1), (225, 2), (225, 3)]
H, G = 0.0025, 0.005


def bm(key, sym, s):
    """Book touch-mid at real s (last frame; the state carries across the short disconnects)."""
    import bisect
    i = bisect.bisect_right(R.book_rx[sym], R.wall(key, s)) - 1
    return rec.mid(R.books[sym][i]) if i >= 0 else None


def touch(key, sym, s):
    import bisect
    i = bisect.bisect_right(R.book_rx[sym], R.wall(key, s)) - 1
    b = R.books[sym][i]
    return (b[2][0][0] if b[2] else None, b[3][0][0] if b[3] else None, b[2][0][1] if b[2] else 0, b[3][0][1] if b[3] else 0)


print("=" * 100)
print("A. Jump sizes: % change of book touch-mid and of consensus, 58 s -> 74 s (rows 29->37), and book 58 -> 80 / 90 s")
print("   timing: first real s (0.5-s grid) at which the book mid has covered 50% / 90% of its 58->90 s move;"
      " consensus: first 2-s row covering 90% of its 58->90 move")
for key in ROUNDS:
    ri = R.rounds[key]
    print(f"-- season {key[0]} round {key[1]} ({ri.date})")
    print("   sym     cons58->74  book58->74  book58->80  book58->90 | book t50  t90 | cons t90 | book half-spread bp at 58 / 66 / 74")
    for sym in rec.SYMS:
        c58, c74, c90 = ri.cons(sym, 58), ri.cons(sym, 74), ri.cons(sym, 90)
        b58, b74, b80, b90 = (bm(key, sym, s) for s in (58, 74, 80, 90))
        mv = b90 - b58
        t50 = t90 = None
        s = 58.0
        while s <= 100 and mv != 0:
            f = (bm(key, sym, s) - b58) / mv
            if t50 is None and f >= 0.5: t50 = s
            if t90 is None and f >= 0.9: t90 = s; break
            s += 0.5
        cm = c90 - c58; ct90 = None
        for s in range(58, 101, 2):
            if cm != 0 and (ri.cons(sym, s) - c58) / cm >= 0.9: ct90 = s; break
        hs = []
        for s in (58, 66, 74):
            b, a, _, _ = touch(key, sym, s)
            hs.append(1e4 * (a - b) / (a + b) if (a and b) else float("nan"))
        print(f"   {sym:6s} {100*(c74/c58-1):+9.2f}% {100*(b74/b58-1):+9.2f}% {100*(b80/b58-1):+9.2f}% {100*(b90/b58-1):+9.2f}% |"
              f" {t50 if t50 else '-':>6} {t90 if t90 else '-':>5} | {ct90 if ct90 else '-':>6} | " + " / ".join(f"{x:5.1f}" for x in hs))

print("=" * 100)
print("B. H1 on the recorded books vs the consensus RT estimate (full size = 4 clips of L/4; half = clips at 50, 52 s only)")


def h1_book(key, size_clips=4, lat=0.0, cap_frac=0.005, syms=H1, entry=(50, 52, 54, 56), exit_=(72, 74, 76, 78)):
    T = rec.Taker(R)
    m48 = {s: bm(key, s, entry[0] - 2) for s in syms}
    entry_t = list(entry)[:size_clips]
    for k, t in enumerate(entry_t, 1):
        for s in syms:
            L = rec.LIMITS[s]
            tgt = DIR[s] * round(k * L / 4)
            cap = m48[s] * (1 + cap_frac) if DIR[s] > 0 else m48[s] * (1 - cap_frac)
            T.to_target(s, tgt, R.wall(key, t + lat), cap)
    pos_in = dict(T.pos)
    for k, t in enumerate(exit_, 1):
        for s in syms:
            p0 = pos_in.get(s, 0)
            tgt = round(p0 * (1 - k / 4))
            T.to_target(s, tgt, R.wall(key, t + lat), None)
    left = {s: T.pos.get(s, 0) for s in syms if T.pos.get(s, 0)}
    return T, pos_in, left


def h1_cons(key, size_clips=4, syms=H1, entry=(50, 52, 54, 56), exit_=(72, 74, 76, 78)):
    """Playbook RT fill model on the consensus: decision at t fills at row t/2 + 1 at cons x (1 +/- (h + gamma*q/L))."""
    ri = R.rounds[key]
    out = {}
    for s in syms:
        L = rec.LIMITS[s]; d = DIR[s]
        clip = round(L / 4)
        cash = 0.0
        for t in list(entry)[:size_clips]:
            cash -= d * clip * ri.cons(s, t + 2) * (1 + d * (H + G * clip / L))
        xq = clip * size_clips / 4
        for t in exit_:
            cash += d * xq * ri.cons(s, t + 2) * (1 - d * (H + G * xq / L))
        out[s] = cash
    return out


for size_clips, label in ((4, "full"), (2, "half")):
    print(f"-- H1 {label} size")
    tot = {"book": 0, "book+1s": 0, "cons": 0}
    print("   round   sym     entry fill qty  avg entry  48s mid  | avg exit   | book PnL  book+1s PnL | cons PnL | cons jump-only (no costs)")
    for key in ROUNDS:
        ri = R.rounds[key]
        T, pin, left = h1_book(key, size_clips)
        T1, _, _ = h1_book(key, size_clips, lat=1.0)
        C = h1_cons(key, size_clips)
        rb = rc = rb1 = 0
        for s in H1:
            ent = [f for f in T.fills if f[1] == s and R.real(key, f[0]) < 60]
            ex = [f for f in T.fills if f[1] == s and R.real(key, f[0]) > 60]
            eq = sum(abs(f[2]) for f in ent)
            ea = sum(abs(f[2]) * f[3] for f in ent) / eq if eq else float("nan")
            xq = sum(abs(f[2]) for f in ex)
            xa = sum(abs(f[2]) * f[3] for f in ex) / xq if xq else float("nan")
            pb, pb1 = T.pnl_sym(s), T1.pnl_sym(s)
            clip = round(rec.LIMITS[s] / 4)
            nojump = DIR[s] * clip * size_clips * (sum(ri.cons(s, t + 2) for t in (72, 74, 76, 78)) / 4 -
                                                  sum(ri.cons(s, t + 2) for t in (50, 52, 54, 56)[:size_clips]) / size_clips)
            rb += pb; rc += C[s]; rb1 += pb1
            if label == "full":
                print(f"   {key[0]}r{key[1]}  {s:6s} {DIR[s]*eq:+6d}/{DIR[s]*clip*size_clips:+5d} {ea:9.2f} {bm(key, s, 48):9.2f} | {xa:9.2f} | {pb:+9.0f} {pb1:+9.0f} | {C[s]:+8.0f} | {nojump:+8.0f}")
        print(f"   {key[0]}r{key[1]}  TOTAL  book {rb:+8.0f}   book+1s {rb1:+8.0f}   consensus RT {rc:+8.0f}   (left unflat: {left})")
        tot["book"] += rb; tot["cons"] += rc; tot["book+1s"] += rb1
    print(f"   3-round total {label}: book {tot['book']:+.0f}, book +1 s latency {tot['book+1s']:+.0f}, consensus RT model {tot['cons']:+.0f}")

print("=" * 100)
print("C. Pre-formation crowding signal (playbook rule: skip if HAVEN or ARCH moved > 1/3 of mean jump our way 30 -> 48 s)")
for key in ROUNDS:
    ri = R.rounds[key]
    row = []
    for s in ("HAVEN", "ARCH", "PORT", "FERRY"):
        row.append(f"{s} book {100*(bm(key, s, 48)/bm(key, s, 30)-1):+.2f}% cons {100*(ri.cons(s, 48)/ri.cons(s, 30)-1):+.2f}%")
    print(f"   {key}: " + "; ".join(row))

print("=" * 100)
print("D. H1 timing variants, full size, 3-round totals (book walk, lat 0 / +1 s) vs consensus RT")
for ent in ((50, 52, 54, 56), (40, 42, 44, 46)):
    for ex in ((72, 74, 76, 78), (80, 82, 84, 86), (86, 88, 90, 92), (96, 98, 100, 102)):
        tb = tb1 = tc = 0; per = []
        for key in ROUNDS:
            T, _, _ = h1_book(key, 4, entry=ent, exit_=ex)
            T1, _, _ = h1_book(key, 4, lat=1.0, entry=ent, exit_=ex)
            C = h1_cons(key, 4, entry=ent, exit_=ex)
            b = sum(T.pnl_sym(s) for s in H1); b1 = sum(T1.pnl_sym(s) for s in H1); c = sum(C.values())
            tb += b; tb1 += b1; tc += c; per.append(f"{b:+.0f}/{c:+.0f}")
        print(f"   entry {ent[0]}-{ent[-1]} exit {ex[0]}-{ex[-1]}: book {tb:+7.0f}  book+1s {tb1:+7.0f}  cons {tc:+7.0f}   per round book/cons: {', '.join(per)}")

print("=" * 100)
print("E. Reactive formation sweep (new idea): after 60 s, once the house backstop (E layer, 3x limit) or the fast A/B")
print("   layers have moved > 1% in the H1 direction from the 58-s mid, take every opposite-side level priced better than")
print("   the new house level E_mid x (1 -/+ 0.3%), up to the limit, at trigger + latency; exit in 4 clips at 86-92 s.")
SZE = {s: 3 * rec.LIMITS[s] for s in rec.SYMS}
SZA = {s: (round(0.1 * rec.LIMITS[s]), round(0.07 * rec.LIMITS[s])) for s in rec.SYMS}
SZA["ARCH"] = (20, 14); SZA["REBLD"] = (40, 28); SZA["HAVEN"] = (25, 18)
import bisect
for lat in (0.3, 1.0, 2.0):
    tot = 0; rows = []
    for key in ROUNDS:
        T = rec.Taker(R); rt = 0
        for s in H1:
            d = DIR[s]; pre = bm(key, s, 58); L = rec.LIMITS[s]
            fl = R.books_between(s, R.wall(key, 60), R.wall(key, 80))
            trig = None; fair = None
            for b in fl:
                em = [p for p, q in b[2] if q == SZE[s]] + [p for p, q in b[3] if q == SZE[s]]
                e_mid = 0.5 * sum(em) if len(em) == 2 else None
                if e_mid and d * (e_mid / pre - 1) > 0.01:
                    trig, fair = b[0], e_mid; break
            if trig is None:
                continue
            t = trig + lat
            cap = fair * (1 - 0.003) if d > 0 else fair * (1 + 0.003)
            T.take(s, "buy" if d > 0 else "sell", L, t, cap)
            p_in = T.pos.get(s, 0)
            for k, tx in enumerate((86, 88, 90, 92), 1):
                T.to_target(s, round(p_in * (1 - k / 4)), R.wall(key, tx), None)
            rt += T.pnl_sym(s)
        tot += rt; rows.append(f"{key}: {rt:+.0f}")
    print(f"   latency {lat} s: total {tot:+.0f}   ({'; '.join(rows)})")

print("=" * 100)
print("F. Reactive sweep, direction taken from the house backstop's own move (any sign, > 1% vs 58-s mid), all 12 symbols,")
print("   take stale levels better than E_mid x (1 -/+ 0.3%) up to the limit at trigger + latency; exit 4 clips")
for ex in ((80, 82, 84, 86), (86, 88, 90, 92)):
    for lat in (0.3, 1.0, 2.0):
        tot = 0; rows = []; per_sym = {}
        for key in ROUNDS:
            T = rec.Taker(R); rt = 0
            for s in rec.SYMS:
                pre = bm(key, s, 58); L = rec.LIMITS[s]
                trig = None
                for b in R.books_between(s, R.wall(key, 60), R.wall(key, 78)):
                    em = [p for p, q in b[2] if q == SZE[s]] + [p for p, q in b[3] if q == SZE[s]]
                    e_mid = 0.5 * sum(em) if len(em) == 2 else None
                    if e_mid and abs(e_mid / pre - 1) > 0.01:
                        trig, fair = b[0], e_mid; break
                if trig is None:
                    continue
                d = 1 if fair > pre else -1
                cap = fair * (1 - 0.003) if d > 0 else fair * (1 + 0.003)
                T.take(s, "buy" if d > 0 else "sell", L, trig + lat, cap)
                p_in = T.pos.get(s, 0)
                for k, tx in enumerate(ex, 1):
                    T.to_target(s, round(p_in * (1 - k / 4)), R.wall(key, tx), None)
                v = T.pnl_sym(s); rt += v; per_sym[s] = per_sym.get(s, 0) + v
            tot += rt; rows.append(f"{key}: {rt:+.0f}")
        print(f"   exit {ex[0]}-{ex[-1]} latency {lat} s: total {tot:+.0f}   ({'; '.join(rows)})")
    print("      by symbol (lat 2 s): " + ", ".join(f"{s} {v:+.0f}" for s, v in per_sym.items()))

print("=" * 100)
print("G. Playbook round-1 check on the books (4.2): H1 round-trip cost per share vs the 48-s (entry) and 80-s (exit)")
print("   book mids, against half the realised book jump 58->74 s of HAVEN, PORT, ARCH; full size, playbook timing")
for key in ROUNDS:
    T, pin, _ = h1_book(key, 4)
    costs = []
    for s in H1:
        m48, m80 = bm(key, s, 48), bm(key, s, 80)
        for (t, sym, qn, p) in T.fills:
            if sym != s: continue
            ref = m48 if R.real(key, t) < 60 else m80
            costs.append((abs(qn), 1e4 * (p - ref) / ref * (1 if qn > 0 else -1)))
    tq = sum(c[0] for c in costs)
    avg = sum(c[0] * c[1] for c in costs) / tq
    jumps = {s: 1e4 * (bm(key, s, 74) / bm(key, s, 58) - 1) for s in ("HAVEN", "PORT", "ARCH")}
    rt = 2 * avg
    print(f"   {key}: avg one-way cost {avg:+.1f} bp -> round trip {rt:.1f} bp; book jumps 58->74 HAVEN {jumps['HAVEN']:+.0f} PORT {jumps['PORT']:+.0f} ARCH {jumps['ARCH']:+.0f} bp;"
          f" pass (RT <= half of mean jump {0.5*sum(jumps.values())/3:.0f} bp): {rt <= 0.5 * sum(jumps.values()) / 3}")

print()
print("H. Others' net signed flow before formation (0-50 s), % of one team's limit, from trades vs the prevailing mid")
for key in ROUNDS:
    row = []
    for s in H1:
        v = 0
        for (rx, ts, p, qn, tid) in R.trades_between(s, R.wall(key, 0), R.wall(key, 50)):
            j = bisect.bisect_left(R.book_rx[s], rx - 0.0005) - 1
            m = rec.mid(R.books[s][j]) if j >= 0 else None
            if m: v += qn if p > m else -qn if p < m else 0
        row.append(f"{s} {100*v/rec.LIMITS[s]:+.0f}")
    print(f"   {key}: " + ", ".join(row))

print("=" * 100)
print("I. Sign agreement, book 58->90 s vs consensus 58->74 s, and size ratio (book / consensus), by round")
for key in ROUNDS:
    ri = R.rounds[key]
    agree = 0; ratios = []
    for s in rec.SYMS:
        c = ri.cons(s, 74) / ri.cons(s, 58) - 1; b = bm(key, s, 90) / bm(key, s, 58) - 1
        agree += (c * b > 0)
        if abs(c) > 0.005: ratios.append(b / c)
    print(f"   {key}: same sign {agree}/12; median size ratio over |cons move| > 0.5%: {st.median(ratios):.2f} (n={len(ratios)})")
