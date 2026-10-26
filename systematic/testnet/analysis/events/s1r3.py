"""Event study 3b: S1-r3 settlement positioning after the round-3 final report, priced on the recorded books.

Settlement S from settlement.json; epsilon per symbol from playbook 4.3. For each symbol, from the final-report
frame te (its received_at) to the close:
  * the book's mispriced liquidity: ask size below S - eps and bid size above S + eps, its value sum q x |S - p|,
    for a team at its limit (one side, starting flat) and in total;
  * a sweeping taker who arrives at te + d (d = 0.0, 0.3, 1, 2, 4, 6, 10 s) and from then on takes every mispriced
    level (up to its limit room) on every frame until 778 s; PnL is marked to S (our fills deplete a frame until the
    next frame; later frames as recorded);
  * what others took: trades at prices beyond S -/+ eps in [te, 780] (size, value);
  * passive opportunity: seller-initiated trades at < S - eps (they would have hit our resting bid at S - eps first)
    and buyer-initiated trades at > S + eps.

usage: python3 -I s1r3.py recording.jsonl season224_dir season225_dir
"""
import sys, os, bisect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec

R = rec.Recording(sys.argv[1], sys.argv[2:4])
EPS = {"PORT": 0.07, "ISLE": 0.02, "AQUA": 0.02, "GRID": 0.06, "FERRY": 0.09, "RECON": 0.02, "MAT": 0.02,
       "GEN": 0.11, "HAVEN": 0.02, "REIN": 0.02, "ARCH": 0.17, "REBLD": 0.12}
DELAYS = [0.0, 0.3, 1.0, 2.0, 4.0, 6.0, 10.0]

for key in [(224, 3), (225, 3)]:
    ri = R.rounds[key]
    S = ri.settlement
    te = [rx for rx, m in R.news if m.get("type") == "damage_report" and m.get("stage") == "final"
          and R.date2round.get(m["time"][:10]) == key][0]
    print("=" * 110)
    print(f"season {key[0]} round 3: final report received at real {R.real(key, te):.2f} s; close 780 s")
    print("  sym     S      pre-report mid  |  max mispriced size (side)  value at limit | sweeper PnL arriving at te + "
          + " / ".join(f"{d:g}" for d in DELAYS) + " s | others took qty (value) | passive opp qty")
    tot = {d: 0.0 for d in DELAYS}; tot_lim = 0.0; tot_oth = 0.0
    for sym in rec.SYMS:
        s_, e = S[sym], EPS[sym]; L = rec.LIMITS[sym]
        i0 = bisect.bisect_right(R.book_rx[sym], te) - 1
        pre = rec.mid(R.books[sym][i0])
        fl = R.books_between(sym, te, R.wall(key, 780))
        if i0 >= 0:
            fl = [R.books[sym][i0]] + fl
        best_q = 0; best_side = ""; best_v = 0.0
        for b in fl:
            qa = sum(q for p, q in b[3] if p < s_ - e); qb = sum(q for p, q in b[2] if p > s_ + e)
            va = buy = 0
            for p, q in sorted([x for x in b[3] if x[0] < s_ - e]):
                x = min(q, L - buy); buy += x; va += x * (s_ - p)
            vb = sell = 0
            for p, q in sorted([x for x in b[2] if x[0] > s_ + e], reverse=True):
                x = min(q, L - sell); sell += x; vb += x * (p - s_)
            if max(qa, qb) > best_q:
                best_q = max(qa, qb); best_side = "ask<S" if qa >= qb else "bid>S"
            best_v = max(best_v, va, vb)
        tot_lim += best_v
        res = []
        for d in DELAYS:
            T = rec.Taker(R)
            t0 = te + d
            times = [t0] + [b[0] + 1e-4 for b in fl if b[0] > t0 and R.real(key, b[0]) < 778]
            for t in times:
                pos = T.pos.get(sym, 0)
                T.take(sym, "buy", L - pos, t, cap=s_ - e)
                pos = T.pos.get(sym, 0)
                T.take(sym, "sell", L + pos, t, cap=s_ + e)
            pnl = T.cash + T.pos.get(sym, 0) * s_
            tot[d] += pnl; res.append(pnl)
        # others
        oth_q = 0; oth_v = 0.0; pas = 0
        for (rx, ts, p, qn, tid) in R.trades_between(sym, te, R.wall(key, 781)):
            j = bisect.bisect_left(R.book_rx[sym], rx - 0.0005) - 1
            m = rec.mid(R.books[sym][j]) if j >= 0 else None
            buyer = m is not None and p > m
            if p < s_ - e and buyer:
                oth_q += qn; oth_v += qn * (s_ - p)
            elif p > s_ + e and not buyer:
                oth_q += qn; oth_v += qn * (p - s_)
            elif p < s_ - e and not buyer:
                pas += qn
            elif p > s_ + e and buyer:
                pas += qn
        tot_oth += oth_v
        print(f"  {sym:6s} {s_:8.2f} {pre:8.2f} ({100*(s_/pre-1):+5.2f}%) | {best_q:6d} {best_side:6s} {best_v:8.0f} | "
              + " / ".join(f"{x:6.0f}" for x in res) + f" | {oth_q:5d} ({oth_v:6.0f}) | {pas:5d}")
    print(f"  TOTAL value at limit (best single frame) {tot_lim:.0f}; sweeper " + " / ".join(f"{tot[d]:.0f}" for d in DELAYS)
          + f"; others captured {tot_oth:.0f}")

print("=" * 110)
print("Playbook 746-s sanity check on the books: |touch-mid - S| vs max(3 eps, 0.5% of S); 'X' = the check would cancel")
for key in [(224, 3), (225, 3)]:
    S = R.rounds[key].settlement
    for t in (744, 746, 750, 760, 775):
        row = []
        for sym in rec.SYMS:
            i = bisect.bisect_right(R.book_rx[sym], R.wall(key, t)) - 1
            m = rec.mid(R.books[sym][i])
            dev = abs(m - S[sym]); lim = max(3 * EPS[sym], 0.005 * S[sym])
            row.append(f"{sym} {100*(m/S[sym]-1):+.2f}%{'X' if dev > lim else ''}")
        print(f"   {key} t={t}: " + ", ".join(row))
