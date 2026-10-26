"""H1 formation leg replayed on the recorded testnet books (season 225 rounds 1-3), our own impact ignored except
for an optional follow-on penalty per extra same-side clip.
usage: python3 -I b11_h1_book.py recording.jsonl history_dir [followon_bps=31]

Variants (per symbol, size = full limit L, clips of L/4 walking the visible book at each time):
  H1     : playbook direction (long PORT ISLE AQUA GRID FERRY HAVEN ARCH, short RECON MAT GEN REBLD), enter 50/52/54/56 s,
           exit 72/74/76/78 s.
  H1late : same entry, exit 84/86/88/90 s (the book reprices later than the consensus).
  SNIPE  : direction = sign of the book's own 56->84 s move (ORACLE upper bound; a live bot would use the engine at 60 s),
           enter 60.5/61.0/61.5/62.0 s, exit 84/86/88/90 s.
  CONS-RT: H1 on the research consensus with the RT fill model (fill at row floor(t/2)+1 +- (25 bp + 50 bp/4)).
PnL $ per round = sum over clips of dir * (exit px - entry px) * L/4, minus follow-on penalty on clips 2-4 of each leg.
"""
import sys, os, bisect, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books_lib as bl

rec, hist = sys.argv[1:3]
FO = float(sys.argv[3]) if len(sys.argv) > 3 else 31.0
D = bl.load(rec, hist)
rounds = {d: v for d, v in D["rounds"].items() if v.get("season") == 225 and "open" in v}
order = sorted(rounds, key=lambda d: rounds[d]["open"])
DIR = {"PORT": 1, "ISLE": 1, "AQUA": 1, "GRID": 1, "FERRY": 1, "HAVEN": 1, "ARCH": 1, "RECON": -1, "MAT": -1, "GEN": -1, "REBLD": -1}


def frame_at(d, s, t):
    fr = [f for f in D["books"][s] if f.date == d]
    ras = [f.ra for f in fr]
    x = rounds[d]["open"] + t
    j = bisect.bisect_right(ras, x) - 1
    if j < 0 or bl.seg_at(D["segs"], x) != fr[j].seg:
        return None
    return fr[j]


def leg(d, s, times, side, q):
    """side +1 buy (walk asks), -1 sell (walk bids); returns list of vwaps (None if no book)."""
    out = []
    for k, t in enumerate(times):
        f = frame_at(d, s, t)
        if f is None or not f.bids or not f.asks:
            out.append(None); continue
        filled, vwap, worst = bl.walk(f.asks if side > 0 else f.bids, q)
        if filled < q:
            vwap = (vwap * filled + worst * (q - filled)) / q
        pen = FO * 1e-4 * vwap * (1 if k > 0 else 0)
        out.append(vwap + side * pen)
    return out


def level(d, s, t):
    xs = [frame_at(d, s, t + 0.25 * k) for k in range(-4, 5)]
    xs = [f.mid for f in xs if f and f.mid]
    return statistics.median(xs) if xs else None


tot = {"H1": {}, "H1late": {}, "SNIPE": {}, "CONS-RT": {}}
print(f"follow-on penalty {FO:.0f} bp on clips 2-4 of each leg; $ at full limit per symbol-round")
for d in order:
    print(f"\n{d}:  sym    H1 $   (entry/exit cost bp)   H1late $   SNIPE(oracle) $   CONS-RT $")
    for s, dr in DIR.items():
        L = bl.LIMITS[s]; q = max(1, round(L / 4))
        row = {}
        for name, ent, ex, direction in (("H1", [50, 52, 54, 56], [72, 74, 76, 78], dr),
                                         ("H1late", [50, 52, 54, 56], [84, 86, 88, 90], dr)):
            e = leg(d, s, ent, direction, q); x = leg(d, s, ex, -direction, q)
            if None in e or None in x:
                row[name] = None; continue
            row[name] = sum(direction * (b - a) * q for a, b in zip(e, x))
            if name == "H1":
                m0 = level(d, s, 48); m1 = level(d, s, 70)
                ec = statistics.fmean(direction * (a - m0) / m0 * 1e4 for a in e)
                xc = statistics.fmean(-direction * (b - m1) / m1 * 1e4 for b in x)
                row["costs"] = (ec, xc)
        a0, a1 = level(d, s, 56), level(d, s, 84)
        sd = 1 if a1 > a0 else -1
        e = leg(d, s, [60.5, 61.0, 61.5, 62.0], sd, q); x = leg(d, s, [84, 86, 88, 90], -sd, q)
        row["SNIPE"] = None if (None in e or None in x) else sum(sd * (b - a) * q for a, b in zip(e, x))
        cons = D["consensus"][d][s]
        h = 0.0025 + 0.005 / 4
        ce = [cons[int(t // 2) + 1] * (1 + dr * h) for t in (50, 52, 54, 56)]
        cx = [cons[int(t // 2) + 1] * (1 - dr * h) for t in (72, 74, 76, 78)]
        row["CONS-RT"] = sum(dr * (b - a) * q for a, b in zip(ce, cx))
        for k in tot:
            if row.get(k) is not None:
                tot[k][d] = tot[k].get(d, 0) + row[k]
        c = row.get("costs", (float("nan"), float("nan")))
        f = lambda v: f"{v:9.0f}" if v is not None else "      n/a"
        print(f"            {s:6s} {f(row['H1'])}   ({c[0]:5.0f}/{c[1]:5.0f})        {f(row['H1late'])}   {f(row['SNIPE'])}        {f(row['CONS-RT'])}")
print("\ntotals per round ($, 11 symbols, full size):")
for k, v in tot.items():
    print(f"  {k:8s} " + "  ".join(f"{d}: {x:+8.0f}" for d, x in v.items()) + f"   sum {sum(v.values()):+8.0f}")
