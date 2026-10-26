"""Shared helpers for the independent verification (standard library only).

Clock convention used everywhere in verify/:
  real_s(market time) = (seconds since 09:30 of that market day) / 30
  round open (wall) = median over storm frames of (received_at - real_s)
  a book/trade frame at wall time w belongs to the round with open <= w < open + 790, real = w - open.
market.csv row i  <->  real second 2*i of its round.
"""
import json, csv, os, statistics as st
from datetime import datetime

DATE_ROUND = {"1803-05-11": (224, 3), "1802-05-10": (225, 1), "1802-05-11": (225, 2),
              "1802-05-12": (225, 3), "1801-05-11": (226, 1)}
SYMS = ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "RECON", "MAT", "GEN", "HAVEN", "REIN", "ARCH", "REBLD"]
LIM = dict(PORT=200, ISLE=300, AQUA=500, GRID=400, FERRY=800, RECON=500, MAT=700, GEN=600,
           HAVEN=250, REIN=200, ARCH=50, REBLD=100)
SIGN = dict(PORT=1, ISLE=1, AQUA=1, GRID=1, FERRY=1, HAVEN=1, ARCH=1, RECON=-1, MAT=-1, GEN=-1, REBLD=-1)


def real_of(tstr):
    hh, mm, ss = int(tstr[11:13]), int(tstr[14:16]), int(tstr[17:19])
    return (hh * 3600 + mm * 60 + ss - 9.5 * 3600) / 30.0


def load_rec(path):
    out = []
    with open(path) as f:
        for line in f:
            out.append(json.loads(line))
    return out


def connections(frames):
    """list of (start_wall, end_wall) per connection, end = last frame before next ack."""
    acks = [i for i, fr in enumerate(frames) if fr["type"] == "subscription_ack"]
    segs = []
    for k, i in enumerate(acks):
        j = acks[k + 1] - 1 if k + 1 < len(acks) else len(frames) - 1
        segs.append((frames[i]["received_at"], frames[j]["received_at"]))
    return segs


def round_opens(frames):
    """{(season,round): dict(open=median, n, sd, mn, mx, slope, lin_sd)} from storm news frames."""
    by = {}
    for fr in frames:
        if fr["type"] != "news":
            continue
        m = fr["message"]
        if m.get("type") != "storm":
            continue
        key = DATE_ROUND.get(m["time"][:10])
        if key is None:
            continue
        by.setdefault(key, []).append((real_of(m["time"]), fr["received_at"]))
    res = {}
    for key, pts in by.items():
        offs = [w - r for r, w in pts]
        med = st.median(offs)
        resid = [o - med for o in offs]
        # linear fit w = a + b*r
        n = len(pts)
        mr = sum(r for r, _ in pts) / n
        mw = sum(w for _, w in pts) / n
        sxx = sum((r - mr) ** 2 for r, _ in pts)
        sxy = sum((r - mr) * (w - mw) for r, w in pts)
        b = sxy / sxx
        a = mw - b * mr
        lres = [w - (a + b * r) for r, w in pts]
        res[key] = dict(open=med, n=n, sd=st.pstdev(resid), mn=min(resid), mx=max(resid),
                        slope=b, a=a, lin_sd=st.pstdev(lres))
    return res


def news_msgs(frames):
    out = []
    for fr in frames:
        if fr["type"] == "news":
            m = fr["message"]
            out.append((fr["received_at"], m))
    return out


def load_market(hist, season):
    """{round: [(row, {sym: price})]}"""
    out = {}
    with open(os.path.join(hist, "season-%d" % season, "market.csv")) as f:
        rd = csv.DictReader(f)
        cnt = {}
        for row in rd:
            r = int(row["round"])
            i = cnt.get(r, 0)
            cnt[r] = i + 1
            out.setdefault(r, []).append({s: float(row[s]) for s in SYMS})
    return out


def load_feed(hist, season):
    with open(os.path.join(hist, "season-%d" % season, "feed.jsonl")) as f:
        return [json.loads(l) for l in f if l.strip()]


def load_settle(hist, season):
    with open(os.path.join(hist, "season-%d" % season, "settlement.json")) as f:
        return json.load(f)


def books_by_round(frames, opens, pad=10.0):
    """{(season,round): {sym: [(real, bids, asks)]}} bids/asks as lists of (price, qty)."""
    keys = sorted(opens, key=lambda k: opens[k]["open"])
    out = {k: {s: [] for s in SYMS} for k in keys}
    for fr in frames:
        if fr["type"] != "orderbook":
            continue
        w = fr["received_at"]
        for k in keys:
            o = opens[k]["open"]
            if o - pad <= w < o + 780 + pad:
                bids = [(l["price"], l["quantity"]) for l in fr["bids"]]
                asks = [(l["price"], l["quantity"]) for l in fr["asks"]]
                out[k][fr["symbol"]].append((w - o, bids, asks))
                break
    return out


def trades_by_round(frames, opens, pad=10.0):
    keys = sorted(opens, key=lambda k: opens[k]["open"])
    out = {k: {s: [] for s in SYMS} for k in keys}
    seen = set()
    for fr in frames:
        if fr["type"] != "trade":
            continue
        if fr["trade_id"] in seen:
            continue
        seen.add(fr["trade_id"])
        # use server timestamp (local EDT wall) -> unix via received_at offset approx: use received_at
        w = fr["received_at"]
        for k in keys:
            o = opens[k]["open"]
            if o - pad <= w < o + 780 + pad:
                out[k][fr["symbol"]].append((w - o, fr["price"], fr["quantity"]))
                break
    return out


def book_at(series, t):
    """last book with real <= t (binary search). returns (real, bids, asks) or None"""
    lo, hi = 0, len(series)
    while lo < hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= t:
            lo = mid + 1
        else:
            hi = mid
    return series[lo - 1] if lo > 0 else None


def mid(b):
    if not b or not b[1] or not b[2]:
        return None
    return 0.5 * (b[1][0][0] + b[2][0][0])


def walk(levels, q):
    """average price for q shares walking levels [(p,qty)], None if insufficient"""
    rem, cost = q, 0.0
    for p, n in levels:
        take = min(rem, n)
        cost += take * p
        rem -= take
        if rem <= 0:
            break
    if rem > 0:
        return None
    return cost / q


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * p
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)
