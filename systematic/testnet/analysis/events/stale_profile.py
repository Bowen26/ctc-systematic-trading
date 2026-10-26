"""Stale-window profile after each scheduled event, as recorded (after other participants' takes).

For each event (formation 60 s, prelim 570 s, final 740 s; te = the news frame's received_at) and each symbol whose
book moved by more than max(0.5%, 3 x pre-event half-spread) (pre = touch-mid at te - 1 s,
post = median touch-mid over [te+20, te+30]):
at te + k, k = 0..20 s, the resting size on the wrong side of the post level (asks < post x (1 - 0.2%) for an up
move; bids > post x (1 + 0.2%) for a down move) as % of the limit, and its value up to one limit, sum q |post - p|.
The value is before the exit cost of unwinding at post (about one half-spread).
Printed: per event type, the sum over symbols and rounds of the value available at each k, and the share of
event-symbols with >= 10% of the limit still stale at k; per round detail for the largest movers.

usage: python3 -I stale_profile.py recording.jsonl season224_dir season225_dir
"""
import sys, os, bisect, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec

R = rec.Recording(sys.argv[1], sys.argv[2:4])
KS = [0, 1, 2, 3, 4, 5, 6, 8, 10, 12, 14, 16, 18, 20]
EVENTS = {"formation": ("storm_formed", None), "prelim": ("damage_report", "preliminary"), "final": ("damage_report", "final")}


def bk(sym, t):
    i = bisect.bisect_right(R.book_rx[sym], t) - 1
    return R.books[sym][i] if i >= 0 else None


for name, (mt, stage) in EVENTS.items():
    evs = []
    for rx, m in R.news:
        if m.get("type") == mt and (stage is None or m.get("stage") == stage):
            key = R.date2round.get(m["time"][:10])
            if key:
                evs.append((key, rx))
    agg_v = {k: 0.0 for k in KS}; agg_n = {k: 0 for k in KS}; n_ev = 0
    agg_v_stock = {k: 0.0 for k in KS}; agg_v_etf = {k: 0.0 for k in KS}
    detail = []
    for key, te in evs:
        for sym in rec.SYMS:
            b0 = bk(sym, te - 1.0)
            pre = rec.mid(b0)
            posts = [rec.mid(bk(sym, te + x)) for x in range(20, 31)]
            posts = [p for p in posts if p]
            if not pre or not posts or not b0[2] or not b0[3]:
                continue
            post = st.median(posts)
            hs = (b0[3][0][0] - b0[2][0][0]) / 2 / pre
            mv = post / pre - 1
            if abs(mv) < max(0.005, 3 * hs):
                continue
            n_ev += 1
            d = 1 if mv > 0 else -1
            thr = post * (1 - 0.002 * d)
            L = rec.LIMITS[sym]
            row = []
            for k in KS:
                b = bk(sym, te + k)
                lv = b[3] if d > 0 else b[2]
                stale = sorted([(p, q) for p, q in lv if (p < thr if d > 0 else p > thr)], key=lambda x: d * x[0])
                got = 0; v = 0.0
                for p, q in stale:
                    x = min(q, L - got); got += x; v += x * abs(post - p)
                    if got >= L: break
                qq = sum(q for p, q in stale)
                agg_v[k] += v
                (agg_v_etf if sym in ("ARCH", "REBLD") else agg_v_stock)[k] += v
                if qq >= 0.1 * L:
                    agg_n[k] += 1
                row.append((qq / L, v))
            detail.append((abs(mv) * 100, key, sym, mv, row))
    print("=" * 110)
    print(f"{name}: {len(evs)} events, {n_ev} event-symbols with a move above threshold")
    print("   k (s after te)        " + " ".join(f"{k:6d}" for k in KS))
    print("   value $ all symbols   " + " ".join(f"{agg_v[k]:6.0f}" for k in KS))
    print("   value $ stocks        " + " ".join(f"{agg_v_stock[k]:6.0f}" for k in KS))
    print("   value $ ETFs          " + " ".join(f"{agg_v_etf[k]:6.0f}" for k in KS))
    print("   share with >=10% L    " + " ".join(f"{agg_n[k]/max(1,n_ev):6.2f}" for k in KS))
    print("   largest movers (stale size %L / value $ at k):")
    for mvp, key, sym, mv, row in sorted(detail, reverse=True)[:8]:
        print(f"     {key} {sym:6s} move {100*mv:+6.2f}%: " + " ".join(f"{100*r[0]:.0f}%/{r[1]:.0f}" for r in row))
