"""Shared helpers for the feed-integrity analysis of the testnet recording (standard library only).

Import with:  sys.path.insert(0, <this dir>); import feedlib as FL

Conventions
- "wall" = received_at (unix s, local clock).  Server ISO timestamps are local EDT naive strings.
- Market clock: market time = 09:30 + 30 x (real seconds since the round opened).  A round lasts 780 real s.
- Round phase by real second s in the round: pre-storm [0, 60), storm [60, 540), aftermath [540, 780], pause beyond.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import statistics as stx

ROUND_LEN = 780.0
SEASON_DATES = {  # market date -> (season, round)
    "1803-05-09": (224, 1), "1803-05-10": (224, 2), "1803-05-11": (224, 3),
    "1802-05-10": (225, 1), "1802-05-11": (225, 2), "1802-05-12": (225, 3),
    "1801-05-11": (226, 1),
}


def load_frames(path):
    with open(path) as f:
        return [json.loads(line) for line in f]


def mkt_seconds(t: str) -> float:
    """Market ISO time -> real seconds since 09:30 of that market day (30x clock)."""
    hh, mm, ss = t[11:19].split(":")
    m = int(hh) * 60 + int(mm) + float(ss) / 60.0
    return (m - 9 * 60 - 30) * 2.0  # one market minute = 2 real s


def iso_to_unix(ts: str) -> float:
    """Server wall-clock ISO (local, naive) -> unix seconds using this machine's local timezone."""
    return dt.datetime.fromisoformat(ts).timestamp()


def hms(u: float) -> str:
    return dt.datetime.fromtimestamp(u).strftime("%H:%M:%S.%f")[:-3]


def connections(frames):
    """List of dicts: index range, ack wall time, last frame wall time, per connection."""
    acks = [i for i, f in enumerate(frames) if f["type"] == "subscription_ack"]
    out = []
    for k, i0 in enumerate(acks):
        i1 = acks[k + 1] if k + 1 < len(acks) else len(frames)
        out.append({"k": k + 1, "i0": i0, "i1": i1, "start": frames[i0]["received_at"],
                    "last": frames[i1 - 1]["received_at"],
                    "next_ack": frames[i1]["received_at"] if i1 < len(frames) else None})
    return out


def news_frames(frames):
    """[(frame index, received_at, message)] for news frames whose message is a dict."""
    return [(i, f["received_at"], f["message"]) for i, f in enumerate(frames)
            if f["type"] == "news" and isinstance(f.get("message"), dict)]


def fit_round_opens(frames, types=("storm",)):
    """Per market date: fitted wall clock of the round open (09:30) from news frames of the given types.

    open_wall = received_at - mkt_seconds(time).  We use the minimum over frames (latency is one-sided, so the
    minimum is the best estimate of send time + fixed latency) and also report median and the jitter
    (received_at - fitted line) distribution."""
    by = {}
    for i, w, m in news_frames(frames):
        if m.get("type") not in types or "time" not in m:
            continue
        d = m["time"][:10]
        by.setdefault(d, []).append(w - mkt_seconds(m["time"]))
    res = {}
    for d, xs in by.items():
        xs.sort()
        med = stx.median(xs)
        res[d] = {"n": len(xs), "open_min": xs[0], "open_med": med,
                  "jit_sd": stx.pstdev(xs) if len(xs) > 1 else 0.0,
                  "jit_p95": xs[int(0.95 * (len(xs) - 1))] - xs[0], "jit_max": xs[-1] - xs[0]}
    return res


def book_resumes(frames, min_gap=60.0):
    """Wall times of the first orderbook frame after a >min_gap s silence of the books (= round opens)."""
    out, prev = [], None
    for f in frames:
        if f["type"] != "orderbook" or not (f["bids"] or f["asks"]):
            continue
        w = f["received_at"]
        if prev is not None and w - prev > min_gap:
            out.append(w)
        prev = w
    return out


def all_opens(frames):
    """Round opens: storm-frame fit (median) where a storm was recorded, else the book-resume time - 0.15 s
    (book resume lags the storm-fit open by -0.03..+0.31 s in the three season-225 rounds), dated by the next
    snapshot."""
    fits = fit_round_opens(frames)
    opens = {d: r["open_med"] for d, r in fits.items()}
    snaps = [(f["received_at"], f["message"]["time"][:10]) for f in frames
             if f["type"] == "news" and isinstance(f.get("message"), dict) and f["message"].get("type") == "snapshot"]
    for r in book_resumes(frames):
        if any(abs(r - v) < 5 for v in opens.values()):
            continue
        nxt = [d for w, d in snaps if w >= r]
        if nxt and nxt[0] not in opens:
            opens[nxt[0]] = r - 0.15
    return opens


class Clock:
    """Map wall time -> (market date, real second in round, phase) using fitted round opens."""

    def __init__(self, opens: dict):
        # opens: date -> open wall time (median fit)
        self.items = sorted((v, d) for d, v in opens.items())

    def locate(self, wall: float):
        cur = None
        for v, d in self.items:
            if wall >= v - 1e-9:
                cur = (v, d)
        if cur is None:
            v, d = self.items[0]
            return d, wall - v, "before-first-open"
        v, d = cur
        s = wall - v
        return d, s, phase_of(s)


def phase_of(s: float) -> str:
    if s < 0:
        return "pause"
    if s < 60:
        return "pre-storm"
    if s < 540:
        return "storm"
    if s <= ROUND_LEN:
        return "aftermath"
    return "pause"


def mkt_label(s: float) -> str:
    """Real second in round -> market HH:MM:SS."""
    m = 9 * 60 + 30 + s / 2.0
    h = int(m // 60)
    mm = int(m % 60)
    ss = int(round((m - math.floor(m)) * 60))
    if ss == 60:
        ss, mm = 0, mm + 1
    return f"{h:02d}:{mm:02d}:{ss:02d}"


def research_feed(sp_history: str, season: int):
    path = os.path.join(sp_history, f"season-{season:03d}", "feed.jsonl")
    with open(path) as f:
        return [json.loads(line) for line in f]


def season_of_msg(m):
    t = m.get("time")
    if not isinstance(t, str):
        return None
    sr = SEASON_DATES.get(t[:10])
    return sr[0] if sr else None
