"""(c) Formation jump on the books vs consensus, and H1 replayed on the recorded books (season 225).
H1 (playbook 4.2): direction SIGN; entry clips of L/4 at real 50,52,54,56 s capped at 48-s book mid x (1 +- 0.5%);
exit clips of L/4 at 72,74,76,78 s (variants: later exits), uncapped walk (forced flat) unless EXIT_CAP set.
Our fills deplete the frame they hit until that symbol's next book frame. No follow-on penalty, no impact.
Consensus RT model: fill at the row after the decision at consensus x (1 +- 0.375%) (h 0.25% + gamma 0.5% x 1/4).
usage: python3 -I v04_h1.py <recording> <history_dir>
"""
import sys, os, bisect, statistics as st, copy
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
from vlib import *

rec, hist = sys.argv[1], sys.argv[2]
F = load_rec(rec)
op = round_opens(F)
segs = connections(F)
ack_t = [s for s, _ in segs]
B = books_by_round(F, op)
M225 = load_market(hist, 225)
H1SYMS = [s for s in SYMS if s in SIGN]

# storm_formed arrival
formed = {}
for w, m in news_msgs(F):
    if m.get("type") == "storm_formed":
        k = DATE_ROUND.get(m["time"][:10])
        if k in op:
            formed[k] = w - op[k]["open"]
print("storm_formed received at real s:", {k: round(v, 3) for k, v in formed.items()})


def frame_at(ser, t):
    i = bisect.bisect_right([x[0] for x in ser], t) - 1
    return i


def bmid(ser, t):
    b = book_at(ser, t)
    return mid(b)


print("\n== formation jump: book mid vs consensus (%) ==")
print("%-6s %-6s %8s %8s %8s %8s | %8s %8s | t50(book) t50(cons)" % ("round", "sym", "b58-74", "b58-80", "b58-90", "b58-120", "c58-74", "c58-90"))
agree = 0; tot = 0; ratios = []
for r in (1, 2, 3):
    key = (225, r)
    cons = M225[r]
    for s in H1SYMS:
        ser = B[key][s]
        m58 = bmid(ser, 58.0)
        bm = {t: bmid(ser, t) for t in (74.0, 80.0, 90.0, 120.0)}
        c58, c74, c90 = cons[29][s], cons[37][s], cons[45][s]
        jb = {t: (bm[t] / m58 - 1) * 100 for t in bm}
        jc74 = (c74 / c58 - 1) * 100
        jc90 = (c90 / c58 - 1) * 100
        # t50: first time book mid covers half of the 58->90 move
        tgt = 0.5 * (bm[90.0] - m58)
        t50 = None
        t = 58.0
        while t <= 90.0:
            x = bmid(ser, t)
            if tgt != 0 and (x - m58) / tgt >= 1.0:
                t50 = t; break
            t += 0.1
        tgc = 0.5 * (c90 - c58)
        c50 = None
        for i in range(29, 46):
            if tgc != 0 and (cons[i][s] - c58) / tgc >= 1.0:
                c50 = 2 * i; break
        tot += 1
        agree += (jb[90.0] > 0) == (jc74 > 0)
        if jc74 != 0:
            ratios.append(jb[90.0] / jc74)
        print("r%-5d %-6s %+8.2f %+8.2f %+8.2f %+8.2f | %+8.2f %+8.2f | %s %s" % (
            r, s, jb[74.0], jb[80.0], jb[90.0], jb[120.0], jc74, jc90,
            "%.1f" % t50 if t50 else "-", c50))
print("sign agreement book 58->90 vs consensus 58->74: %d/%d; median ratio %.2f" % (agree, tot, st.median(ratios)))


def take(ser, t, side, q, cap, dep):
    """walk the book at t for q shares on side (+1 buy, -1 sell), limit cap (None = no cap).
    dep: {frame_index: {price: qty_taken}} depletion. returns (filled, cash)"""
    i = frame_at(ser, t)
    if i < 0:
        return 0, 0.0
    fr = ser[i]
    w = op_open + t
    k = bisect.bisect_right(ack_t, w) - 1
    lv = fr[2] if side > 0 else fr[1]
    d = dep.setdefault(i, {})
    left, cash, got = q, 0.0, 0
    for p, n in lv:
        if cap is not None and ((side > 0 and p > cap + 1e-9) or (side < 0 and p < cap - 1e-9)):
            break
        avail = n - d.get(p, 0)
        if avail <= 0:
            continue
        x = min(left, avail)
        d[p] = d.get(p, 0) + x
        cash += x * p; got += x; left -= x
        if left == 0:
            break
    return got, cash


def h1_books(key, size=1.0, entry=(50, 52, 54, 56), exits=(72, 74, 76, 78), exit_cap=None):
    global op_open
    op_open = op[key]["open"]
    out = {}
    for s in H1SYMS:
        ser = B[key][s]
        L = LIM[s]; sg = SIGN[s]
        tgt = int(round(size * L))
        clip = max(1, int(round(L / 4)))
        m48 = bmid(ser, 48.0)
        cap = m48 * (1 + 0.005 * sg)
        dep = {}
        pos, cash = 0, 0.0
        for t in entry:
            q = min(clip, tgt - abs(pos))
            if q <= 0:
                break
            got, c = take(ser, float(t), sg, q, cap, dep)
            pos += sg * got; cash -= sg * c
        held = pos
        for t in exits:
            q = min(clip, abs(pos))
            if q <= 0:
                break
            capx = None
            if exit_cap is not None:
                mm = bmid(ser, float(t)); capx = mm * (1 - exit_cap * sg)
            got, c = take(ser, float(t), -sg, q, capx, dep)
            pos -= sg * got; cash += sg * c
        # anything left: dump at the last exit time + 2 s uncapped
        t = exits[-1] + 2.0
        while pos != 0 and t < exits[-1] + 20:
            sd = -1 if pos > 0 else 1
            got, c = take(ser, t, sd, abs(pos), None, dep)
            pos += sd * got; cash -= sd * c
            t += 2.0
        out[s] = (held, cash, pos)
    return out


def h1_cons(r, size=1.0, entry=(50, 52, 54, 56), exits=(72, 74, 76, 78), cost=0.00375, cap=True):
    cons = M225[r]
    out = {}
    for s in H1SYMS:
        L = LIM[s]; sg = SIGN[s]
        tgt = int(round(size * L)); clip = max(1, int(round(L / 4)))
        c48 = cons[24][s]
        pos, cash = 0, 0.0
        for t in entry:
            q = min(clip, tgt - abs(pos))
            if q <= 0:
                break
            p = cons[t // 2 + 1][s] * (1 + cost * sg)
            if cap and sg * (p - c48 * (1 + 0.005 * sg)) > 0:
                continue
            pos += sg * q; cash -= sg * q * p
        for t in exits:
            q = min(clip, abs(pos))
            if q <= 0:
                break
            p = cons[t // 2 + 1][s] * (1 - cost * sg)
            pos -= sg * q; cash += sg * q * p
        out[s] = (cash, pos)
    return out


print("\n== H1 full size on recorded books vs consensus RT (season 225) ==")
variants = [("playbook exit 72-78", (72, 74, 76, 78)), ("exit 80-86", (80, 82, 84, 86)), ("exit 84-90", (84, 86, 88, 90)),
            ("exit 86-92", (86, 88, 90, 92)), ("exit 120-126", (120, 122, 124, 126))]
for size in (1.0, 0.5):
    print("-- size %.1f L --" % size)
    for name, ex in variants:
        rowb, rowc, rowcn = [], [], []
        for r in (1, 2, 3):
            ob = h1_books((225, r), size=size, exits=ex)
            oc = h1_cons(r, size=size, exits=ex)
            ocn = h1_cons(r, size=size, exits=ex, cap=False)
            rowb.append(sum(v[1] for v in ob.values()))
            rowc.append(sum(v[0] for v in oc.values()))
            rowcn.append(sum(v[0] for v in ocn.values()))
            left = {s: v[2] for s, v in ob.items() if v[2]}
            if left:
                print("   leftover r%d %s" % (r, left))
        print("%-20s books r1 %+7.0f r2 %+7.0f r3 %+7.0f total %+7.0f | consensus RT (cap) r1 %+7.0f r2 %+7.0f r3 %+7.0f total %+7.0f | no cap total %+7.0f" % (
            name, rowb[0], rowb[1], rowb[2], sum(rowb), rowc[0], rowc[1], rowc[2], sum(rowc), sum(rowcn)))

print("\n-- per-symbol detail, size 1.0, playbook exit 72-78 (held after entry / PnL books / PnL consensus RT) --")
for r in (1, 2, 3):
    ob = h1_books((225, r))
    oc = h1_cons(r)
    print("r%d: " % r + "  ".join("%s %d/%d %+.0f/%+.0f" % (s, abs(ob[s][0]), LIM[s], ob[s][1], oc[s][0]) for s in H1SYMS))

print("\n-- exit-cost decomposition r1-r3, playbook timing: book mid at 48 s, avg entry, mid at 74/90 s, avg exit (PORT, HAVEN, FERRY, ARCH) --")
for r in (1, 2, 3):
    key = (225, r)
    for s in ("PORT", "HAVEN", "FERRY", "ARCH"):
        ser = B[key][s]
        print("r%d %-6s mid48 %.2f mid56 %.2f mid74 %.2f mid80 %.2f mid90 %.2f | cons58 %.2f cons74 %.2f cons90 %.2f" % (
            r, s, bmid(ser, 48), bmid(ser, 56), bmid(ser, 74), bmid(ser, 80), bmid(ser, 90),
            M225[r][29][s], M225[r][37][s], M225[r][45][s]))
