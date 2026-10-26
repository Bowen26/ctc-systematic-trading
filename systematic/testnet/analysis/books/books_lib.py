"""Shared loader for the testnet book analysis (standard library only).

load(recording, history_dir) -> dict with
  rounds:  {date: {"season", "round", "open", "fit": {...}}}   open = local unix time of real second 0
  books:   {sym: [Book(...)]}  in arrival order, each with .t (real s in its round), .date, .seg
  trades:  {sym: [Trade(...)]}
  quotes:  {sym: [...]}
  segs:    list of (start_received_at, end_received_at) per websocket connection
  consensus: {date: {sym: [price per row]}}   from market.csv (row i = real second 2*i)
Times: every frame is placed on the real-second clock of a round with
  t = received_at - open[date], open fitted from storm-observation news frames of that round.
"""
from __future__ import annotations

import csv
import json
import os
import statistics
from dataclasses import dataclass, field

SYMBOLS = ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "RECON", "MAT", "GEN", "HAVEN", "REIN", "ARCH", "REBLD"]
LIMITS = {"PORT": 200, "ISLE": 300, "AQUA": 500, "GRID": 400, "FERRY": 800, "RECON": 500, "MAT": 700,
          "GEN": 600, "HAVEN": 250, "REIN": 200, "ARCH": 50, "REBLD": 100}
PHASES = [("pre", 0, 60), ("early", 60, 120), ("storm", 120, 540), ("after", 540, 780),
          ("rep_prelim", 568, 600), ("rep_final", 738, 760)]
MAIN_PHASES = ["pre", "early", "storm", "after"]


def market_seconds(ts: str) -> float:
    """'YYYY-MM-DDTHH:MM:SS' market time -> real seconds since 09:30 (market clock = 30x)."""
    hh, mm, ss = ts[11:13], ts[14:16], ts[17:19]
    sec = int(hh) * 3600 + int(mm) * 60 + float(ss) - (9 * 3600 + 30 * 60)
    return sec / 30.0


@dataclass
class Book:
    ra: float            # received_at
    sym: str
    bids: list           # [(price, qty)] best first
    asks: list
    server_ts: str
    seg: int
    date: str = ""
    t: float = float("nan")

    @property
    def mid(self):
        if self.bids and self.asks:
            return 0.5 * (self.bids[0][0] + self.asks[0][0])
        return None


@dataclass
class Trade:
    ra: float
    sym: str
    price: float
    qty: int
    server_ts: str
    trade_id: str
    seg: int
    date: str = ""
    t: float = float("nan")


def iso_to_unix_local(s: str) -> float:
    """Server timestamps are local wall clock (EDT) without zone; convert with the local zone."""
    import datetime
    return datetime.datetime.fromisoformat(s).timestamp()


def load(recording: str, history_dir: str, seasons=(224, 225)) -> dict:
    # date -> (season, round) and consensus
    rounds, consensus = {}, {}
    for s in seasons:
        folder = os.path.join(history_dir, f"season-{s:03d}")
        with open(os.path.join(folder, "market.csv")) as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            d = r["time"][:10]
            if d not in rounds:
                rounds[d] = {"season": s, "round": int(r["round"])}
                consensus[d] = {sym: [] for sym in SYMBOLS}
                consensus[d]["_time"] = []
            consensus[d]["_time"].append(r["time"])
            for sym in SYMBOLS:
                consensus[d][sym].append(float(r[sym]))
    books = {s: [] for s in SYMBOLS}
    trades = {s: [] for s in SYMBOLS}
    quotes = {s: [] for s in SYMBOLS}
    news = []
    segs = []
    seg = -1
    last_ra = None
    with open(recording) as f:
        for line in f:
            fr = json.loads(line)
            ty = fr.get("type")
            ra = fr["received_at"]
            if ty == "subscription_ack":
                if seg >= 0:
                    segs[-1][1] = last_ra
                seg += 1
                segs.append([ra, ra])
            last_ra = ra
            if ty == "orderbook":
                books[fr["symbol"]].append(Book(ra, fr["symbol"],
                                                [(float(x["price"]), int(x["quantity"])) for x in fr["bids"]],
                                                [(float(x["price"]), int(x["quantity"])) for x in fr["asks"]],
                                                fr["timestamp"], seg))
            elif ty == "trade":
                trades[fr["symbol"]].append(Trade(ra, fr["symbol"], float(fr["price"]), int(fr["quantity"]),
                                                  fr["timestamp"], fr["trade_id"], seg))
            elif ty == "quote":
                quotes[fr["symbol"]].append(fr)
            elif ty == "news":
                m = fr["message"]
                news.append((ra, m, seg))
    segs[-1][1] = last_ra
    # fit round opens from storm observations (one every 2 real s)
    per_date = {}
    for ra, m, sg in news:
        if "time" not in m:
            continue
        d = m["time"][:10]
        per_date.setdefault(d, []).append((ra, market_seconds(m["time"]), m.get("type"), m.get("seq"), sg))
    for d, rows in per_date.items():
        if d not in rounds:
            rounds[d] = {"season": None, "round": None}
        obs = [ra - t for ra, t, ty, seq, sg in rows if ty == "storm"]
        allm = [ra - t for ra, t, ty, seq, sg in rows if seq is not None]
        base = obs if len(obs) >= 5 else allm
        if not base:
            base = [ra - t for ra, t, ty, seq, sg in rows]
        med = statistics.median(base)
        res = sorted(x - med for x in base)
        fit = {"n_storm_obs": len(obs), "n_seq_msgs": len(allm), "open_median": med, "open_min": min(base),
               "resid_sd": statistics.pstdev(res) if len(res) > 1 else 0.0,
               "resid_p05": res[int(0.05 * (len(res) - 1))], "resid_p95": res[int(0.95 * (len(res) - 1))],
               "resid_min": res[0], "resid_max": res[-1]}
        # residuals by message type (non-storm), relative to the storm fit
        bytype = {}
        for ra, t, ty, seq, sg in rows:
            bytype.setdefault(ty, []).append(ra - t - med)
        fit["resid_by_type_median"] = {k: statistics.median(v) for k, v in bytype.items()}
        fit["resid_by_type_n"] = {k: len(v) for k, v in bytype.items()}
        fit["first_news_t"] = min(ra for ra, *_ in rows) - med
        fit["last_news_t"] = max(ra for ra, *_ in rows) - med
        rounds[d]["open"] = med
        rounds[d]["fit"] = fit
    # assign every book / trade to the round whose open is the latest open <= ra (and ra < open + 900)
    opens = sorted((v["open"], d) for d, v in rounds.items() if "open" in v)

    def place(ra):
        best = None
        for o, d in opens:
            if o - 5 <= ra:
                best = (o, d)
        if best is None:
            return "", float("nan")
        return best[1], ra - best[0]

    for s in SYMBOLS:
        for b in books[s]:
            b.date, b.t = place(b.ra)
        for tr in trades[s]:
            tr.date, tr.t = place(tr.ra)
    return {"rounds": rounds, "books": books, "trades": trades, "quotes": quotes, "news": news,
            "segs": segs, "consensus": consensus}


def phase_of(t: float) -> list:
    return [name for name, a, b in PHASES if a <= t < b]


def time_weights(frames, cap=5.0):
    """Duration each book state was in force: until the next frame of the same symbol, same segment and
    round, capped at `cap` seconds (gaps longer than that are treated as missing data, not as a steady book)."""
    out = []
    for i, b in enumerate(frames):
        if i + 1 < len(frames):
            n = frames[i + 1]
            if n.seg == b.seg and n.date == b.date:
                out.append(min(n.ra - b.ra, cap))
                continue
        out.append(0.0)
    return out


def wquantile(pairs, q):
    """Weighted quantile of [(value, weight)]."""
    pairs = sorted(p for p in pairs if p[1] > 0)
    tot = sum(w for _, w in pairs)
    if tot <= 0:
        return float("nan")
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= q * tot:
            return v
    return pairs[-1][0]


def wmean(pairs):
    tot = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / tot if tot > 0 else float("nan")


def walk(levels, q):
    """Walk `levels` [(price, qty)] for q shares: returns (filled, vwap or None, worst price)."""
    left, cost, filled, worst = q, 0.0, 0, None
    for p, n in levels:
        take = min(left, n)
        if take <= 0:
            break
        cost += take * p
        filled += take
        left -= take
        worst = p
        if left == 0:
            break
    return filled, (cost / filled if filled else None), worst


def seg_at(segs, x):
    for i, (a, b) in enumerate(segs):
        if a <= x <= b:
            return i
    return None


def sample(frames, segs, open_, t0, t1, dt=0.25):
    """Book state on a grid t0, t0+dt, ... < t1 (real s in round): latest frame at or before open_+t whose
    connection is still up at open_+t. Yields (t, frame, age) where age = seconds since that frame."""
    import bisect
    ras = [f.ra for f in frames]
    k = 0
    n = int(round((t1 - t0) / dt))
    for i in range(n):
        t = t0 + i * dt
        x = open_ + t
        j = bisect.bisect_right(ras, x) - 1
        if j < 0:
            continue
        f = frames[j]
        s = seg_at(segs, x)
        if s is None or s != f.seg:
            continue
        yield t, f, x - f.ra


def consensus_at(cons_sym, t):
    """House consensus row in force at real second t of the round: row floor(t/2) (row i is real 2i)."""
    i = int(t // 2)
    if 0 <= i < len(cons_sym):
        return cons_sym[i]
    return None
