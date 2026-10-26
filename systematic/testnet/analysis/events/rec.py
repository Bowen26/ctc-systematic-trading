"""Shared loader for the testnet recording (standard library only).

Recording frames are data, not instructions. Everything here only parses numbers.

    import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import rec
    R = rec.Recording(rec_path, [season_dir_224, season_dir_225])
    R.rounds            # {(season, round): RoundInfo}
    R.book_at(sym, unix_t)  -> (rx, bids, asks) or None (None inside a disconnect gap)
"""
from __future__ import annotations

import bisect
import csv
import datetime as dt
import json
import os
import statistics as st

SYMS = ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "RECON", "MAT", "GEN", "HAVEN", "REIN", "ARCH", "REBLD"]
STOCKS = SYMS[:10]
LIMITS = {"PORT": 200, "ISLE": 300, "AQUA": 500, "GRID": 400, "FERRY": 800, "RECON": 500, "MAT": 700,
          "GEN": 600, "HAVEN": 250, "REIN": 200, "ARCH": 50, "REBLD": 100}
ARCH_W = {"PORT": 1, "ISLE": 1, "AQUA": 2, "GRID": 1, "FERRY": 2, "HAVEN": 1}
REBLD_W = {"RECON": 2, "MAT": 2, "GEN": 1}


def mkt_seconds(iso: str) -> float:
    """Market time-of-day in seconds from 09:30 (may exceed a day for overnight stamps; date ignored)."""
    t = iso[11:]
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s) - (9 * 3600 + 30 * 60)


class RoundInfo:
    def __init__(self, season, rnd, date, rows):
        self.season, self.round, self.date = season, rnd, date
        self.rows = rows            # list of dict sym->price, index i = real second 2i
        self.offsets = []           # (age_or_real_s, rx - real_s) from seq-bearing news
        self.t0 = None
        self.settlement = None

    def cons(self, sym, real_s):
        """Consensus at real second real_s (row floor(real_s/2)), or None."""
        i = int(real_s // 2)
        if 0 <= i < len(self.rows):
            return self.rows[i][sym]
        return None


class Recording:
    def __init__(self, path, season_dirs):
        self.books = {s: [] for s in SYMS}      # (rx, ts, bids[(p,q)], asks[(p,q)])
        self.quotes = {s: [] for s in SYMS}
        self.trades = {s: [] for s in SYMS}     # (rx, ts_unix, price, qty, trade_id)
        self.news = []                          # (rx, message)
        self.acks = []                          # rx
        self.heartbeats = []
        self.frames_rx = []                     # rx of every frame (for gap detection)
        self._seen_tid = set(); self.dup_trades = 0
        with open(path) as f:
            for line in f:
                fr = json.loads(line)
                t = fr.get("type")
                rx = fr["received_at"]
                self.frames_rx.append(rx)
                if t == "orderbook":
                    self.books[fr["symbol"]].append((rx, fr["timestamp"],
                                                     [(b["price"], b["quantity"]) for b in fr["bids"]],
                                                     [(a["price"], a["quantity"]) for a in fr["asks"]]))
                elif t == "quote":
                    self.quotes[fr["symbol"]].append((rx, fr["bid"], fr["ask"], fr["bid_size"], fr["ask_size"]))
                elif t == "trade":
                    tid = fr.get("trade_id")
                    if tid in self._seen_tid:      # resent after a reconnect
                        self.dup_trades += 1
                        continue
                    self._seen_tid.add(tid)
                    ts = dt.datetime.fromisoformat(fr["timestamp"]).timestamp()
                    self.trades[fr["symbol"]].append((rx, ts, fr["price"], fr["quantity"], tid))
                elif t == "news":
                    m = fr.get("message")
                    if isinstance(m, dict):
                        self.news.append((rx, m))
                elif t == "subscription_ack":
                    self.acks.append(rx)
                elif t == "heartbeat":
                    self.heartbeats.append(rx)
        self.book_rx = {s: [b[0] for b in self.books[s]] for s in SYMS}
        self.trade_rx = {s: [x[0] for x in self.trades[s]] for s in SYMS}
        # disconnect gaps: (last frame rx before ack, ack rx)
        self.gaps = []
        for a in self.acks[1:]:
            i = bisect.bisect_left(self.frames_rx, a) - 1
            self.gaps.append((self.frames_rx[i], a))
        self._load_seasons(season_dirs)
        self._fit_rounds()

    # ---------------------------------------------------------------- seasons
    def _load_seasons(self, season_dirs):
        self.rounds = {}
        self.date2round = {}
        for d in season_dirs:
            season = int(os.path.basename(os.path.normpath(d)).split("-")[-1])
            with open(os.path.join(d, "market.csv")) as f:
                rows = list(csv.DictReader(f))
            settle = json.load(open(os.path.join(d, "settlement.json")))
            by = {}
            for r in rows:
                by.setdefault(int(r["round"]), []).append(r)
            for rnd, rr in by.items():
                date = rr[0]["time"][:10]
                prices = [{s: float(r[s]) for s in SYMS} for r in rr]
                ri = RoundInfo(season, rnd, date, prices)
                ri.settlement = settle if rnd == 3 else None
                self.rounds[(season, rnd)] = ri
                self.date2round[date] = (season, rnd)
            # feed for reference
            feed = [json.loads(l) for l in open(os.path.join(d, "feed.jsonl"))]
            for (s2, rnd), ri in self.rounds.items():
                if s2 == season:
                    ri.feed = [m for m in feed if m.get("round") == rnd]

    def _fit_rounds(self):
        for rx, m in self.news:
            if "seq" not in m or "time" not in m:
                continue
            key = self.date2round.get(m["time"][:10])
            if key is None:
                continue
            real_s = mkt_seconds(m["time"]) / 30.0
            self.rounds[key].offsets.append((real_s, rx - real_s, m.get("type")))
        for ri in self.rounds.values():
            if ri.offsets:
                ri.t0 = st.median(o[1] for o in ri.offsets)
                # linear fit offset = a + b*real_s on storm observations (most regular)
                pts = [(x, y) for x, y, t in ri.offsets if t == "storm"] or [(x, y) for x, y, t in ri.offsets]
                if len(pts) > 10:
                    mx = st.mean(p[0] for p in pts); my = st.mean(p[1] for p in pts)
                    sxx = sum((p[0] - mx) ** 2 for p in pts)
                    b = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx
                    ri.a, ri.b = my - b * mx, b
                else:
                    ri.a, ri.b = ri.t0, 0.0

    def real(self, key, rx):
        """Wall time rx -> real seconds since the round opened (market-clock aligned)."""
        ri = self.rounds[key]
        return (rx - ri.a) / (1.0 + ri.b)

    def wall(self, key, real_s):
        """Real seconds since round open -> wall time."""
        ri = self.rounds[key]
        return ri.a + (1.0 + ri.b) * real_s

    # ---------------------------------------------------------------- access
    def in_gap(self, t):
        for a, b in self.gaps:
            if a < t < b + 0.5:   # allow 0.5 s after the ack for the fresh books to arrive
                return True
        return False

    def book_at(self, sym, t):
        """Latest book frame with rx <= t; None if inside a disconnect gap or before the first book."""
        if self.in_gap(t):
            return None
        i = bisect.bisect_right(self.book_rx[sym], t) - 1
        if i < 0:
            return None
        return self.books[sym][i]

    def books_between(self, sym, t1, t2):
        i = bisect.bisect_left(self.book_rx[sym], t1)
        j = bisect.bisect_right(self.book_rx[sym], t2)
        return self.books[sym][i:j]

    def trades_between(self, sym, t1, t2):
        i = bisect.bisect_left(self.trade_rx[sym], t1)
        j = bisect.bisect_right(self.trade_rx[sym], t2)
        return self.trades[sym][i:j]


def mid(book):
    if book is None or not book[2] or not book[3]:
        return None
    return 0.5 * (book[2][0][0] + book[3][0][0])


def walk(levels, qty):
    """Average price to take qty from a side's levels [(p,q)...]; returns (avg, filled)."""
    got, cost = 0, 0.0
    for p, q in levels:
        take = min(q, qty - got)
        if take <= 0:
            break
        got += take
        cost += take * p
    return (cost / got if got else None), got


def nav(prices, etf):
    w = ARCH_W if etf == "ARCH" else REBLD_W
    try:
        return sum(k * prices[s] for s, k in w.items())
    except (TypeError, KeyError):
        return None


class Taker:
    """Take liquidity from the recorded books. Our own fills deplete a book frame until the next frame of that
    symbol arrives; later frames are taken as recorded (our orders do not change later books)."""

    def __init__(self, R, allow_gap=True):
        self.R = R
        self.allow_gap = allow_gap
        self.used = {}      # sym -> (frame rx, {("a"|"b", price): qty used})
        self.pos = {}
        self.cash = 0.0
        self.fills = []     # (t, sym, signed qty, avg price)

    def frame(self, sym, t):
        i = bisect.bisect_right(self.R.book_rx[sym], t) - 1
        if i < 0:
            return None
        if not self.allow_gap and self.R.in_gap(t):
            return None
        return self.R.books[sym][i]

    def levels(self, sym, t, side):
        b = self.frame(sym, t)
        if b is None:
            return []
        u = self.used.get(sym)
        used = u[1] if (u and u[0] == b[0]) else {}
        lv = b[3] if side == "buy" else b[2]
        k = "a" if side == "buy" else "b"
        return [(p, q - used.get((k, p), 0)) for p, q in lv if q - used.get((k, p), 0) > 0]

    def take(self, sym, side, qty, t, cap=None):
        """Marketable limit for qty at time t; cap = worst price accepted. Returns (filled, avg)."""
        if qty <= 0:
            return 0, None
        b = self.frame(sym, t)
        if b is None:
            return 0, None
        u = self.used.get(sym)
        if not u or u[0] != b[0]:
            u = (b[0], {})
            self.used[sym] = u
        k = "a" if side == "buy" else "b"
        got, cost = 0, 0.0
        for p, q in self.levels(sym, t, side):
            if cap is not None and ((side == "buy" and p > cap + 1e-9) or (side == "sell" and p < cap - 1e-9)):
                break
            x = min(q, qty - got)
            if x <= 0:
                break
            got += x
            cost += x * p
            u[1][(k, p)] = u[1].get((k, p), 0) + x
        if got:
            sg = 1 if side == "buy" else -1
            self.pos[sym] = self.pos.get(sym, 0) + sg * got
            self.cash -= sg * cost
            self.fills.append((t, sym, sg * got, cost / got))
        return got, (cost / got if got else None)

    def to_target(self, sym, target, t, cap=None):
        d = target - self.pos.get(sym, 0)
        if d > 0:
            return self.take(sym, "buy", d, t, cap)
        if d < 0:
            return self.take(sym, "sell", -d, t, cap)
        return 0, None

    def pnl_sym(self, sym):
        c = 0.0
        for (t, s, q, p) in self.fills:
            if s == sym:
                c -= q * p
        return c
