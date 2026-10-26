"""Writer's independent checks of claims the verifier did not re-test (standard library only).

    python3 -I w01_checks.py <recording.jsonl> <history dir> <verify dir>

Uses only the loader/clock helpers in verify/vlib.py (round opens = median of received_at - real_s over storm frames).
Sections:
  A  connection gaps, drop position vs the nearest track / official-advisory tick, trades during the long outage
  B  snapshot recovery after each drop (delay, last_seq)
  C  feed hygiene: max inter-frame gap by phase, bursts, duplicate trade_ids, empty books
  D  house ladder shape (pre-storm 5-55 s): level size / L and distance from mid
  E  ETF vs basket NAV on the books: lag correlation, RMS by lag, stale episodes, formation window 68-80 s
  F  ETF lead-lag rule replayed on the books (playbook 4.5 rule), latency 0.3 / 1 / 2 s
  G  follow-on cost: L/4 same-side clip after another taker order >= 0.2 L (storm 80-540 s)
"""
import sys, os, math, statistics as st
sys.path.insert(0, sys.argv[3])
import vlib as V

REC, HIST = sys.argv[1], sys.argv[2]
frames = V.load_rec(REC)
opens = V.round_opens(frames)
keys = sorted(opens, key=lambda k: opens[k]["open"])
O = {k: opens[k]["open"] for k in keys}
# season 226 r1 open: not fitted from storm frames (none); not used below.
conns = V.connections(frames)
gaps = [(conns[i][1], conns[i + 1][0]) for i in range(len(conns) - 1)]  # wall (last frame, next ack)


def round_of(w):
    for k in keys:
        if O[k] - 10 <= w < O[k] + 790:
            return k, w - O[k]
    return None, None


def phase(real):
    if real < 0 or real >= 780:
        return "pause"
    if real < 60:
        return "pre"
    if real < 540:
        return "storm"
    return "after"


print("== A. drops ==")
for i, (a, b) in enumerate(gaps, 1):
    k, r = round_of(a)
    if k is None:
        print(f"drop {i}: last frame outside fitted rounds")
        continue
    # nearest track tick (real 60 + 20j) and official advisory tick (real 90 + 60j) during the storm
    ticks = [("track", 60 + 20 * j) for j in range(25)] + [("advisory", 90 + 60 * j) for j in range(8)]
    near = min(ticks, key=lambda t: abs(r - t[1]))
    print(f"drop {i}: {k} real {r:.2f} phase {phase(r)} gap {b - a:.2f} s; nearest tick {near[0]} {near[1]} "
          f"offset of last frame {r - near[1]:+.2f} s; next ack at real {r + (b - a):.2f}")
# trades whose server timestamp falls inside the long outage
long_gap = max(gaps, key=lambda g: g[1] - g[0])
# server ts is local EDT wall; convert via received_at-timestamp offset of the same frame type (median)
from datetime import datetime
def ts_unix(fr):
    # server timestamp (naive local) -> unix using the median (received_at - parsed) of orderbook frames
    return datetime.fromisoformat(fr["timestamp"]).timestamp()
offs = [fr["received_at"] - ts_unix(fr) for fr in frames[:4000] if fr["type"] == "orderbook"]
lat = st.median(offs)
inside = [fr for fr in frames if fr["type"] == "trade" and long_gap[0] < ts_unix(fr) + lat < long_gap[1]
          and fr["received_at"] > long_gap[1]]
k, r0 = round_of(long_gap[0])
print(f"long outage {long_gap[1] - long_gap[0]:.2f} s ({k} real {r0:.1f}-{r0 + long_gap[1] - long_gap[0]:.1f}); "
      f"median book latency {lat * 1000:.1f} ms; trades received after reconnect but stamped inside the outage: {len(inside)}"
      + (f", first stamped {inside[0]['timestamp'][11:22]}" if inside else ""))

print("\n== B. snapshot recovery ==")
snaps = [(fr["received_at"], fr["message"]) for fr in frames
         if fr["type"] == "news" and fr["message"].get("type") == "snapshot"]
seqs_seen = []
maxseq = {}
for fr in frames:
    if fr["type"] == "news" and "seq" in fr["message"]:
        seqs_seen.append((fr["received_at"], fr["message"]["seq"]))
for i, (a, b) in enumerate(gaps, 1):
    nxt = [(w, m) for w, m in snaps if w > b]
    if not nxt:
        continue
    w, m = nxt[0]
    prev = [s for t, s in seqs_seen if t <= a]
    print(f"drop {i}: first snapshot {w - b:.1f} s after reconnect ({w - a:.1f} s after the drop), last_seq {m.get('last_seq')}, "
          f"highest seq before drop {prev[-1] if prev else None}")
sp = [snaps[i + 1][0] - snaps[i][0] for i in range(len(snaps) - 1)]
print(f"snapshots {len(snaps)}; median spacing {st.median(sp):.2f} s")

print("\n== C. hygiene ==")
mx = {}
for ci, (a, b) in enumerate(conns):
    ws = [fr["received_at"] for fr in frames if a <= fr["received_at"] <= b]
    for x, y in zip(ws, ws[1:]):
        k, r = round_of(x)
        ph = phase(r) if k else "pause"
        if y - x > mx.get(ph, (0,))[0]:
            mx[ph] = (y - x, k, None if r is None else round(r, 1))
for ph in ("pre", "storm", "after", "pause"):
    print(f"max inter-frame gap while connected, {ph}: {mx[ph][0]:.2f} s at {mx[ph][1]} real {mx[ph][2]}")
# bursts
ws = sorted(fr["received_at"] for fr in frames)
best, j = (0, None), 0
for i in range(len(ws)):
    while ws[i] - ws[j] > 1.0:
        j += 1
    if i - j + 1 > best[0]:
        best = (i - j + 1, ws[i])
k, r = round_of(best[1])
print(f"max frames in any 1 s: {best[0]} at {k} real {r:.1f}")
nb, j = (0, None), 0
nws = sorted(fr["received_at"] for fr in frames if fr["type"] == "news")
for i in range(len(nws)):
    while nws[i] - nws[j] > 1.0:
        j += 1
    nb = max(nb, (i - j + 1, nws[i]))
print(f"max news frames in any 1 s: {nb[0]}")
tid = {}
for idx, fr in enumerate(frames):
    if fr["type"] == "trade":
        tid.setdefault(fr["trade_id"], []).append(idx)
dups = [v for v in tid.values() if len(v) > 1]
ackidx = [i for i, fr in enumerate(frames) if fr["type"] == "subscription_ack"]
near_ack = sum(1 for v in dups if any(0 < v[-1] - a <= 40 for a in ackidx))
print(f"duplicate trade_ids: {len(dups)}; of which the repeat arrives within 40 frames after a reconnect ack: {near_ack}")
empty = [(fr["received_at"]) for fr in frames if fr["type"] == "orderbook" and not fr["bids"] and not fr["asks"]]
emp_r = sorted({(round_of(w)[0], round(round_of(w)[1])) for w in empty if round_of(w)[0]})
print(f"empty orderbook frames: {len(empty)}; (round, real s) of those inside rounds: {emp_r}")
nonequote = sum(1 for fr in frames if fr["type"] == "quote" and (fr.get("bid") is None or fr.get("ask") is None))
print(f"quotes with bid or ask None: {nonequote}")
crossed = sum(1 for fr in frames if fr["type"] == "orderbook" and fr["bids"] and fr["asks"]
              and fr["bids"][0]["price"] >= fr["asks"][0]["price"])
print(f"crossed or locked books: {crossed}")

books = V.books_by_round(frames, opens)
trades = V.trades_by_round(frames, opens)

print("\n== D. ladder shape, pre-storm 5-55 s (median over frames; size/L and distance from mid in bp) ==")
for s in V.SYMS:
    L = V.LIM[s]
    lev = {i: ([], []) for i in range(5)}
    for k in keys:
        for (t, bids, asks) in books[k][s]:
            if not (5 <= t <= 55) or len(bids) < 5 or len(asks) < 5:
                continue
            m = 0.5 * (bids[0][0] + asks[0][0])
            for i in range(5):
                lev[i][0].append(0.5 * (bids[i][1] + asks[i][1]) / L)
                lev[i][1].append(1e4 * 0.5 * ((m - bids[i][0]) + (asks[i][0] - m)) / m)
    if not lev[0][0]:
        continue
    print(f"{s:6s} " + "  ".join(f"L{i + 1} {st.median(lev[i][0]):.2f}L @{st.median(lev[i][1]):5.0f}" for i in range(5)))

# ---------- E/F: ETFs ----------
BASK = {"ARCH": {"PORT": 1, "ISLE": 1, "AQUA": 2, "GRID": 1, "FERRY": 2, "HAVEN": 1},
        "REBLD": {"RECON": 2, "MAT": 2, "GEN": 1}}


def gapwin(k):
    out = []
    for a, b in gaps:
        if O[k] - 10 <= a < O[k] + 790:
            out.append((a - O[k], b - O[k] + 2.0))
    return out


def in_gap(k, t):
    return any(a <= t <= b for a, b in gapwin(k))


def nav(k, e, t):
    tot = 0.0
    for c, wgt in BASK[e].items():
        b = V.book_at(books[k][c], t)
        m = V.mid(b)
        if m is None:
            return None
        tot += wgt * m
    return tot


print("\n== E. ETF book vs basket NAV of component book mids ==")
LAGS = list(range(0, 13))
for e in BASK:
    corr_by = {L_: ([], []) for L_ in LAGS}
    rms_by = {L_: [] for L_ in LAGS}
    for k in keys:
        t0 = 80 if k != (224, 3) else 222
        grid = [float(t) for t in range(t0, 541)]
        E_ = {}
        N_ = {}
        for t in grid:
            if in_gap(k, t):
                continue
            m = V.mid(V.book_at(books[k][e], t))
            n = nav(k, e, t)
            if m and n:
                E_[t], N_[t] = math.log(m), math.log(n)
        for L_ in LAGS:
            res = [E_[t] - N_[t - L_] for t in E_ if (t - L_) in N_]
            if res:
                mu = sum(res) / len(res)
                rms_by[L_] += [(x - mu) for x in res]
            for t in E_:
                if (t - 2) in E_ and (t - L_) in N_ and (t - L_ - 2) in N_:
                    corr_by[L_][0].append(E_[t] - E_[t - 2])
                    corr_by[L_][1].append(N_[t - L_] - N_[t - L_ - 2])
    cs = {L_: st.correlation(*corr_by[L_]) for L_ in LAGS}
    rs = {L_: 1e4 * math.sqrt(sum(x * x for x in rms_by[L_]) / len(rms_by[L_])) for L_ in LAGS}
    best_c = max(LAGS, key=lambda L_: cs[L_])
    best_r = min(LAGS, key=lambda L_: rs[L_])
    print(f"{e}: corr of 2-s changes ETF(t) vs NAV(t-k), k=0..12 s: " + " ".join(f"{cs[L_]:+.2f}" for L_ in LAGS)
          + f"  peak k={best_c}")
    print(f"{e}: RMS (demeaned per round) of log ETF(t)/NAV(t-k), bp: " + " ".join(f"{rs[L_]:.0f}" for L_ in LAGS)
          + f"  min k={best_r}")
    # stale episodes: touch through NAV by more than 50 bp, 60-776 s
    tot = n_through = 0
    eps, cur = [], None
    for k in keys:
        t0 = 60 if k != (224, 3) else 222
        t = float(t0)
        while t < 776:
            if not in_gap(k, t):
                b = V.book_at(books[k][e], t)
                n = nav(k, e, t)
                if b and b[1] and b[2] and n:
                    tot += 1
                    thr = b[2][0][0] < n * (1 - 0.005) or b[1][0][0] > n * (1 + 0.005)
                    if thr:
                        n_through += 1
                        if cur and cur[0] == k and abs(cur[1] - (t - 0.5)) < 1e-6:
                            cur = (k, t, cur[2])
                        else:
                            if cur:
                                eps.append(cur[1] - cur[2] + 0.5)
                            cur = (k, t, t)
                    else:
                        if cur:
                            eps.append(cur[1] - cur[2] + 0.5)
                        cur = None
            t += 0.5
    if cur:
        eps.append(cur[1] - cur[2] + 0.5)
    print(f"{e}: touch through NAV by > 50 bp in {100 * n_through / tot:.1f}% of 0.5-s samples (60-776 s); "
          f"{len(eps)} episodes, median {st.median(eps):.1f} s, p90 {V.pct(eps, 0.9):.1f} s")
    # formation window 68-80 s, season 225
    for k in [(225, 1), (225, 2), (225, 3)]:
        vals, sizes = [], []
        for t in [68 + 0.5 * i for i in range(25)]:
            b = V.book_at(books[k][e], t)
            n = nav(k, e, t)
            m = V.mid(b)
            if not (m and n):
                continue
            g = (n - m) / m
            vals.append(100 * g)
            if g > 0:
                q = sum(qq for p, qq in b[2] if p < n * (1 - 0.003))
            else:
                q = sum(qq for p, qq in b[1] if p > n * (1 + 0.003))
            sizes.append(100 * q / V.LIM[e])
        print(f"  {e} {k} 68-80 s: NAV-ETF {min(vals):+.1f}% to {max(vals):+.1f}%; size priced better than NAV-/+0.3%: "
              f"median {st.median(sizes):.0f}% of limit, max {max(sizes):.0f}%")

print("\n== F. ETF lead-lag rule on the books (enter |gap| > max(0.5%, 3 hs); exit |gap| < 0.1%, 8 s, or sign flip; full limit; "
      "fills walk the ETF book at decision + latency; our fills do not deplete the book) ==")


def walk_px(b, side, q):
    lv = b[2] if side > 0 else b[1]
    return V.walk(lv, q)


for latc in (0.3, 1.0, 2.0):
    tot_all = {}
    for e in BASK:
        L = V.LIM[e]
        out = {}
        for k in keys:
            pnl, ntr, pos, ent_t, ent_px = 0.0, 0, 0, None, None
            t0 = 60.0 if k != (224, 3) else 222.0
            t = t0
            while t < 774:
                if in_gap(k, t) or in_gap(k, t + latc):
                    t += 0.5
                    continue
                b = V.book_at(books[k][e], t)
                n = nav(k, e, t)
                m = V.mid(b)
                if not (m and n):
                    t += 0.5
                    continue
                g = (n - m) / m
                hs = (b[2][0][0] - b[1][0][0]) / 2 / m
                if pos == 0:
                    if abs(g) > max(0.005, 3 * hs):
                        side = 1 if g > 0 else -1
                        bf = V.book_at(books[k][e], t + latc)
                        px = walk_px(bf, side, L) if bf else None
                        if px:
                            pos, ent_t, ent_px = side, t, px
                            ntr += 1
                else:
                    if abs(g) < 0.001 or t - ent_t >= 8 or (g > 0) != (pos > 0):
                        bf = V.book_at(books[k][e], t + latc)
                        px = walk_px(bf, -pos, L) if bf else None
                        if px:
                            pnl += pos * L * (px - ent_px)
                            pos = 0
                t += 0.5
            if pos:
                bf = V.book_at(books[k][e], 774)
                px = walk_px(bf, -pos, L)
                pnl += pos * L * (px - ent_px)
            out[k] = (pnl, ntr)
        tot_all[e] = sum(v[0] for v in out.values())
        print(f"latency {latc:.1f} s {e}: " + "  ".join(f"{k[0]}r{k[1]} {v[0]:+,.0f} ({v[1]} trades)" for k, v in out.items())
              + f"  total {tot_all[e]:+,.0f}")
    print(f"latency {latc:.1f} s both ETFs: {sum(tot_all.values()):+,.0f} over 4 rounds (224 r3 from 222 s)")

print("\n== G. follow-on: cost of an L/4 same-side clip after another taker order >= 0.2 L (80-540 s) ==")
res = {"stock": {"pre": [], "p2": [], "p8": []}, "etf": {"pre": [], "p2": [], "p8": []}}
for k in keys:
    for s in V.SYMS:
        if s == "REIN":
            continue
        L = V.LIM[s]
        tr = trades[k][s]
        # group trades within 0.1 s into orders; aggressor from the last book before the first trade of the group
        grp = []
        for (t, p, q) in tr:
            if grp and t - grp[-1][-1][0] <= 0.1:
                grp[-1].append((t, p, q))
            else:
                grp.append([(t, p, q)])
        for g in grp:
            t, p, _ = g[0]
            if not (80 <= t <= 540) or in_gap(k, t) or in_gap(k, t + 8):
                continue
            q = sum(x[2] for x in g)
            if q < 0.2 * L:
                continue
            b0 = V.book_at(books[k][s], t - 0.05)
            if not b0 or not b0[1] or not b0[2]:
                continue
            if p >= b0[2][0][0]:
                side = 1
            elif p <= b0[1][0][0]:
                side = -1
            else:
                continue
            m0 = V.mid(b0)
            clip = max(1, round(L / 4))
            cat = "etf" if s in ("ARCH", "REBLD") else "stock"
            for tag, tt in (("pre", t - 0.05), ("p2", t + 2), ("p8", t + 8)):
                bb = V.book_at(books[k][s], tt)
                px = walk_px(bb, side, clip) if bb else None
                if px:
                    res[cat][tag].append(1e4 * side * (px - m0) / m0)
for cat in res:
    r = res[cat]
    if r["pre"]:
        print(f"{cat}: n={len(r['pre'])}; L/4 clip cost vs pre-trade mid, mean (median) bp: just before "
              f"{st.mean(r['pre']):.0f} ({st.median(r['pre']):.0f}), +2 s {st.mean(r['p2']):.0f} ({st.median(r['p2']):.0f}), "
              f"+8 s {st.mean(r['p8']):.0f} ({st.median(r['p8']):.0f})")

print("\n== F2. same rule, entry as a marketable limit capped at mid x (1 + gap/2) (keeps half the gap; partial fills), "
      "decision grid 1 s and 0.5 s ==")


def capped_fill(b, side, q, cap):
    lv = b[2] if side > 0 else b[1]
    got, cost = 0, 0.0
    for p, n in lv:
        if (side > 0 and p > cap) or (side < 0 and p < cap):
            break
        take = min(q - got, n)
        got += take
        cost += take * p
        if got >= q:
            break
    return got, (cost / got if got else None)


for step in (1.0, 0.5):
    for latc in (0.3, 1.0, 2.0):
        tot_all = {}
        txt = []
        for e in BASK:
            L = V.LIM[e]
            out = {}
            for k in keys:
                pnl, ntr, pos, q, ent_t, ent_px = 0.0, 0, 0, 0, None, None
                t = 60.0 if k != (224, 3) else 222.0
                while t < 774:
                    if in_gap(k, t) or in_gap(k, t + latc):
                        t += step
                        continue
                    b = V.book_at(books[k][e], t)
                    n = nav(k, e, t)
                    m = V.mid(b)
                    if not (m and n):
                        t += step
                        continue
                    g = (n - m) / m
                    hs = (b[2][0][0] - b[1][0][0]) / 2 / m
                    if pos == 0:
                        if abs(g) > max(0.005, 3 * hs):
                            side = 1 if g > 0 else -1
                            bf = V.book_at(books[k][e], t + latc)
                            got, px = capped_fill(bf, side, L, m * (1 + g / 2)) if bf else (0, None)
                            if got:
                                pos, q, ent_t, ent_px = side, got, t, px
                                ntr += 1
                    else:
                        if abs(g) < 0.001 or t - ent_t >= 8 or (g > 0) != (pos > 0):
                            bf = V.book_at(books[k][e], t + latc)
                            px = walk_px(bf, -pos, q) if bf else None
                            if px:
                                pnl += pos * q * (px - ent_px)
                                pos = 0
                    t += step
                if pos:
                    bf = V.book_at(books[k][e], 774)
                    pnl += pos * q * (walk_px(bf, -pos, q) - ent_px)
                out[k] = (pnl, ntr)
            tot_all[e] = sum(v[0] for v in out.values())
            txt.append(f"{e} " + " ".join(f"{k[0]}r{k[1]} {v[0]:+,.0f}({v[1]})" for k, v in out.items()) + f" = {tot_all[e]:+,.0f}")
        print(f"grid {step:.1f} s, latency {latc:.1f} s: " + " | ".join(txt) + f" || both {sum(tot_all.values()):+,.0f}")

print("\n== H. in-storm drift 74 -> 538 s, book mid vs consensus, season 225 (224 r3 has no 74-s book) ==")
mk = V.load_market(HIST, 225)
agree = tot = 0
pn = {"book_mid": 0.0, "book_walk": 0.0, "cons_mid": 0.0, "cons_rt": 0.0}
per_round = {}
for rnd in (1, 2, 3):
    k = (225, rnd)
    rows = mk[rnd]
    line = []
    pr = {"book_walk": 0.0, "cons_rt": 0.0}
    for s in V.SYMS:
        if s == "REIN":
            continue
        L, d = V.LIM[s], -V.SIGN[s]  # drift trade: short the formation-long names, long the rebuilders
        b74, b538 = V.mid(V.book_at(books[k][s], 74)), V.mid(V.book_at(books[k][s], 538))
        c74, c538 = rows[37][s], rows[269][s]
        db, dc = 100 * (b538 / b74 - 1), 100 * (c538 / c74 - 1)
        tot += 1
        agree += (db > 0) == (dc > 0)
        line.append(f"{s} {db:+.1f}/{dc:+.1f}")
        # worked: 4 entry clips at 74, 78, 82, 86 s and 4 exit clips at 532..538 s, L/4 each
        q = L / 4
        ent_b = [V.walk((V.book_at(books[k][s], t)[2] if d > 0 else V.book_at(books[k][s], t)[1]), round(q)) for t in (74, 78, 82, 86)]
        ex_b = [V.walk((V.book_at(books[k][s], t)[1] if d > 0 else V.book_at(books[k][s], t)[2]), round(q)) for t in (532, 534, 536, 538)]
        cost_rt = 0.0025 + 0.005 * 0.25
        ent_c = [rows[t // 2 + 1][s] * (1 + d * cost_rt) for t in (74, 78, 82, 86)]
        ex_c = [rows[t // 2 + 1][s] * (1 - d * cost_rt) for t in (532, 534, 536, 538)]
        wb = d * round(q) * (sum(ex_b) - sum(ent_b))
        wc = d * q * (sum(ex_c) - sum(ent_c))
        pn["book_walk"] += wb
        pn["cons_rt"] += wc
        pr["book_walk"] += wb
        pr["cons_rt"] += wc
        pn["book_mid"] += d * L * (b538 - b74)
        pn["cons_mid"] += d * L * (c538 - c74)
    per_round[rnd] = pr
    print(f"r{rnd} book/cons drift %: " + "  ".join(line))
print(f"sign agreement {agree}/{tot}")
print("drift trade (fallback 4.7, full L, worked L/4 clips 74-86 s in, 532-538 s out): "
      + "  ".join(f"r{r} books {v['book_walk']:+,.0f} cons RT {v['cons_rt']:+,.0f}" for r, v in per_round.items())
      + f"  | total books {pn['book_walk']:+,.0f}, consensus RT {pn['cons_rt']:+,.0f}; at mid with no costs: books {pn['book_mid']:+,.0f}, consensus {pn['cons_mid']:+,.0f}")

print("\n== I. report windows: stale size on the book vs the book mid 20 s later, single frame, net of a 25-bp exit charge, "
      "capped at one limit per symbol (stocks / ETFs, $) ==")
dmg = []
for fr in frames:
    if fr["type"] == "news" and fr["message"].get("type") == "damage_report":
        k, r = round_of(fr["received_at"])
        if k:
            dmg.append((k, fr["message"].get("stage"), r))
for k, stage, te in dmg:
    if in_gap(k, te) or in_gap(k, te + 20):
        continue
    out = []
    for dk in (1, 2, 4, 6, 8):
        vs = ve = 0.0
        for s in V.SYMS:
            post = V.mid(V.book_at(books[k][s], te + 20))
            pre = V.mid(V.book_at(books[k][s], te - 0.2))
            b = V.book_at(books[k][s], te + dk)
            if not (post and pre and b and b[1] and b[2]):
                continue
            L = V.LIM[s]
            val, left = 0.0, L
            if post > pre:
                for p, qq in b[2]:
                    edge = post * (1 - 0.0025) - p
                    if edge <= 0 or left <= 0:
                        break
                    t_ = min(qq, left); val += edge * t_; left -= t_
            else:
                for p, qq in b[1]:
                    edge = p - post * (1 + 0.0025)
                    if edge <= 0 or left <= 0:
                        break
                    t_ = min(qq, left); val += edge * t_; left -= t_
            if s in ("ARCH", "REBLD"):
                ve += val
            else:
                vs += val
        out.append(f"+{dk}s {vs:,.0f}/{ve:,.0f}")
    print(f"{k[0]}r{k[1]} {stage:11s} te={te:.2f}: " + "  ".join(out))
