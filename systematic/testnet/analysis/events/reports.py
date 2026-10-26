"""Event study 3a: damage reports (preliminary 570 s, final 740 s) on the 2-s market-clock grid.

For every report (season 224 round 3, season 225 rounds 1-3) and every symbol whose book moved by more than
max(0.3%, 2 x pre half-spread) between the report row and +30 s (pre = touch-mid at row te/2 - 1 i.e. te - 2 s;
post = median touch-mid over te+20..te+30):
  * rows (2 s each) after the report row until the book mid first covers 50% / 90% of pre -> post;
    the same for the consensus (rows until 90% of its te-2 -> te+20 move);
  * stale touch: at te+2, +4, +6, +8 s the size (% of limit) on the wrong side of the post level at the touch and
    behind it (asks < post x (1 - 0.1%) for an up move, bids > post x (1 + 0.1%) for a down move), and its value up to
    one limit, sum q |post - p|.
Grid times are te + 2k with te the report's received_at.

usage: python3 -I reports.py recording.jsonl season224_dir season225_dir
"""
import sys, os, bisect, statistics as st, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec

R = rec.Recording(sys.argv[1], sys.argv[2:4])


def bk(sym, t):
    i = bisect.bisect_right(R.book_rx[sym], t) - 1
    return R.books[sym][i] if i >= 0 else None


agg = collections.defaultdict(list)
for rx, m in R.news:
    if m.get("type") != "damage_report":
        continue
    key = R.date2round.get(m["time"][:10])
    if key is None:
        continue
    stage = m["stage"]; te = rx; ri = R.rounds[key]; tr = 570 if stage == "preliminary" else 740
    print("=" * 112)
    print(f"{stage} report, season {key[0]} round {key[1]}: received at real {R.real(key, te):.2f} s")
    print("   sym     book move  cons move | book rows to 50% / 90% | cons rows to 90% | stale at +2/+4/+6/+8 s: size %L, value $")
    for sym in rec.SYMS:
        b0 = bk(sym, te - 2.0); pre = rec.mid(b0)
        posts = [rec.mid(bk(sym, te + x)) for x in range(20, 31)]
        posts = [p for p in posts if p]
        if not pre or not posts:
            continue
        post = st.median(posts); mv = post / pre - 1
        hs = (b0[3][0][0] - b0[2][0][0]) / 2 / pre
        c0, c1 = ri.cons(sym, tr - 2), ri.cons(sym, tr + 20)
        if abs(mv) < max(0.003, 2 * hs):
            continue
        d = 1 if mv > 0 else -1
        r50 = r90 = None
        for k in range(0, 16):
            mm = rec.mid(bk(sym, te + 2 * k))
            f = (mm - pre) / (post - pre)
            if r50 is None and f >= 0.5: r50 = k
            if r90 is None and f >= 0.9: r90 = k; break
        cr = None
        for k in range(0, 16):
            if (ri.cons(sym, tr + 2 * k) - c0) / ((c1 - c0) or 1e-9) >= 0.9:
                cr = k; break
        thr = post * (1 - 0.001 * d); L = rec.LIMITS[sym]
        st_ = []
        for k in (2, 4, 6, 8):
            b = bk(sym, te + k)
            lv = b[3] if d > 0 else b[2]
            stale = sorted([(p, q) for p, q in lv if (p < thr if d > 0 else p > thr)], key=lambda x: d * x[0])
            got = 0; v = 0.0
            for p, q in stale:
                x = min(q, L - got); got += x; v += x * abs(post - p)
                if got >= L: break
            st_.append((sum(q for p, q in stale) / L, v))
        grp = "ETF" if sym in ("ARCH", "REBLD") else "stock"
        agg[(stage, grp)].append((r50, r90, cr, st_))
        print(f"   {sym:6s} {100*mv:+8.2f}% {100*(c1/c0-1):+8.2f}% | {r50 if r50 is not None else '-':>6} / {r90 if r90 is not None else '-':>3}"
              f"          | {cr if cr is not None else '-':>6}           | " + "  ".join(f"{100*a:4.0f}% ${b:4.0f}" for a, b in st_))
print("=" * 112)
print("SUMMARY (median rows of 2 s; stale value summed over events)")
for (stage, grp), v in sorted(agg.items()):
    med = lambda i: st.median([x[i] for x in v if x[i] is not None]) if any(x[i] is not None for x in v) else float("nan")
    sv = [sum(x[3][j][1] for x in v) for j in range(4)]
    sz = [st.median([x[3][j][0] for x in v]) for j in range(4)]
    print(f"   {stage:11s} {grp:5s} n={len(v):2d}: book rows to 50% {med(0):.1f}, to 90% {med(1):.1f}; consensus rows to 90% {med(2):.1f};"
          f" stale value at +2/+4/+6/+8 s: " + " / ".join(f"${x:.0f}" for x in sv) + "; median stale size " + " / ".join(f"{100*x:.0f}%" for x in sz))
