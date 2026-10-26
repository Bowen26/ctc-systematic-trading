"""Feed normalizer: one deduplicated event stream from every market-data connection (DESIGN.md §4.A).

Ported from ``bot_skeleton/feed.py`` ``FeedMerger`` (``ingest``, ``poll``, ``_news``, ``_snapshot``,
``_season_change``, ``_note``, ``_advance``, ``_market_day``, ``_server_ts``) with the design's changes:

- Every frame of every connection (``SourceFrame.conn`` "ws0#3", "ws1#1", "rec#2") goes through one
  normalizer in arrival order, so the first copy is forwarded and later copies are dropped.
- News dedup by key **and** fingerprint. The key is ``("seq", seq)``, except announcements and news, which
  use ``("ann", seq, type, headline)`` because the packet's announcement shape carries ``"seq": 0``. The
  fingerprint is (type, round, storm, time). Same key and fingerprint is a duplicate (dropped, never a
  trigger); same key with another fingerprint is a conflict, which only a new season produces.
- High-water mark ``wm``: every seq <= wm was forwarded or is covered by a forwarded snapshot. A snapshot is
  held ``feed.snapshot_hold_s`` and released as ``engine_relevant`` iff ``last_seq > wm``, so a lagging
  connection's live copy wins the race and a snapshot recovers exactly what was lost.
- Season triggers, checked before a message updates any state, in the order (b), (a), (c), (d):
  (a) ``storm_formed_r1``, (b) ``snapshot_last_seq``, (c) ``round_decrease``, (d) ``date_change``; plus
  (e) :meth:`force_season_reset` from the core. (b) and (e) are confirmed at once. (a), (c) and (d) are
  provisional when ``feed.provisional_feed_resets`` is true: the epoch state is saved and cleared, and the
  reset waits for confirmation (a -1 snapshot, a consistent snapshot or the core) or rejection (the saved
  state is merged back).

Hardening (DESIGN.md §13): only an explicit integer ``last_seq`` of -1 is a season start; a snapshot whose
``last_seq`` is missing, null or not an integer is a bad frame (counted, neither held nor a trigger). For
``feed.reset_merge_s`` after a confirmed reset, a message dated with the previous season's calendar (its
(round, date) is the old season's and is at least 30 days off the new season's calendar) is dropped as a late
old-season copy instead of seeding the new epoch.

Pure logic: no I/O, no clock reads. The only side effects are log events.
"""
from __future__ import annotations

import collections
import datetime as _dt
import json
import math

_PREV_SEASON_DAYS = 30          # a date this far off the new season's calendar belongs to another season
from dataclasses import dataclass, field
from typing import Any

from ctcbot.config import Config
from ctcbot.types import (BookEvent, BookLevel, ConnEvent, EventLog, HeartbeatEvent, MarketEvent, NewsEvent,
                          QuoteEvent, SeasonResetEvent, SnapshotEvent, SourceFrame, TradeEvent)

TRADING_PHASES: tuple[str, ...] = ("pre_storm", "storm", "aftermath")
STORM_TYPES: frozenset[str] = frozenset({"storm_formed", "storm", "track", "advisory", "impact"})
AFTER_TYPES: frozenset[str] = frozenset({"storm_dissipated", "damage_report"})
ANN_TYPES: frozenset[str] = frozenset({"announcement", "news"})


# ----------------------------------------------------------------------------------------------- helpers
def _int(x: Any) -> int | None:
    """int for ints and integer strings ("3" -> 3); None otherwise (bools included)."""
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float) and math.isfinite(x) and x == int(x):
        return int(x)
    if isinstance(x, str):
        s = x.strip()
        if s.lstrip("-").isdigit():
            return int(s)
    return None


def _num(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    v = float(x)
    return v if math.isfinite(v) else None


def _qty(x: Any) -> int | None:
    v = _num(x)
    if v is None or v != int(v):
        return None
    return int(v)


def _server_ts(s: Any) -> float | None:
    """Server ISO timestamp -> float seconds (naive stamps are read as local time, consistently), or None."""
    if not isinstance(s, str):
        return None
    try:
        return _dt.datetime.fromisoformat(s).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _market_day(m: dict) -> tuple[int, str] | None:
    """(round, market date) of a news message or a trading-phase snapshot, else None.

    A closed-phase snapshot's market clock runs past midnight, so it is not dated.
    """
    r = _int(m.get("round"))
    t = m.get("time")
    if r is None or not isinstance(t, str) or len(t) < 10:
        return None
    if m.get("type") == "snapshot" and m.get("phase") not in TRADING_PHASES:
        return None
    return r, t[:10]


def _levels(x: Any) -> tuple[BookLevel, ...]:
    if not isinstance(x, list):
        return ()
    out = []
    for lv in x:
        if isinstance(lv, dict):
            px, q = _num(lv.get("price")), _qty(lv.get("quantity", lv.get("qty")))
        elif isinstance(lv, (list, tuple)) and len(lv) >= 2:
            px, q = _num(lv[0]), _qty(lv[1])
        else:
            continue
        if px is None or q is None:
            continue
        out.append(BookLevel(px, q))
    return tuple(out)


def _conn_name(conn: str) -> str:
    return conn.split("#", 1)[0] if isinstance(conn, str) else str(conn)


@dataclass(slots=True)
class _Held:
    release_at: float
    recv_mono: float
    msg: dict
    last_seq: int
    round: int | None
    phase: str | None
    market_time: str | None


@dataclass(slots=True)
class _EpochState:
    """Everything a season reset clears (the trade-id LRU is kept across seasons)."""

    keys: dict = field(default_factory=dict)            # dedup key -> fingerprint
    seen_seqs: set = field(default_factory=set)
    max_seq: int = -1
    wm: int = -1
    held: list = field(default_factory=list)            # _Held, arrival order
    snap_keys: set = field(default_factory=set)         # (last_seq, time) of held or released snapshots
    days: dict = field(default_factory=dict)            # round -> market date
    round_hi: int | None = None
    sf_r1: dict | None = None                           # the round-1 storm_formed of this epoch
    book_ts: dict = field(default_factory=dict)
    quote_ts: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------------------------- normalizer
class FeedNormalizer:
    """Merges every connection's frames into one stream of :data:`ctcbot.types.MarketEvent`."""

    def __init__(self, cfg: Config, log: EventLog) -> None:
        self.cfg = cfg
        self.log = log
        self.hold_s: float = float(cfg.get("feed.snapshot_hold_s", 1.0))
        self.seq_drop: int = int(cfg.get("feed.season_reset_seq_drop", 50))
        self.trade_cap: int = max(1, int(cfg.get("feed.trade_id_cache", 50000)))
        self.provisional: bool = bool(cfg.get("feed.provisional_feed_resets", True))
        self.epoch: int = 0
        self.pending_reset: str | None = None
        self._st = _EpochState()
        self._saved: _EpochState | None = None
        self._pending: dict | None = None
        self._trades: collections.OrderedDict = collections.OrderedDict()
        self._phase_hint: str | None = None
        self._conflict_times: collections.deque = collections.deque()
        self._conflict_alert_at = -math.inf
        self.c: collections.Counter = collections.Counter()
        self.frames_by_type: collections.Counter = collections.Counter()
        self.frames_by_conn: collections.Counter = collections.Counter()
        self.resets: collections.Counter = collections.Counter()
        self.provisional_by_reason: collections.Counter = collections.Counter()
        self.merge_s: float = float(cfg.get("feed.reset_merge_s", 180.0))
        self._prev_days: dict = {}                      # the previous season's round -> date
        self._prev_until: float = -math.inf             # recv_mono until which old-season copies are dropped

    # -------------------------------------------------------------------------- public state
    @property
    def max_seq(self) -> int:
        """Highest forwarded seq this epoch (-1 if none)."""
        return self._st.max_seq

    @property
    def wm(self) -> int:
        """Contiguous high-water mark: every seq <= wm was forwarded or covered by a released snapshot."""
        return self._st.wm

    def phase_hint(self) -> str | None:
        """The last phase seen in the forwarded stream (snapshot phase, else inferred from the news type)."""
        return self._phase_hint

    def gaps(self) -> list[tuple[int, int]]:
        """Runs ``(first, last)`` of seqs <= max_seq never forwarded this epoch (a snapshot may cover them)."""
        st = self._st
        out, start = [], None
        for s in range(st.max_seq + 1):
            if s not in st.seen_seqs:
                if start is None:
                    start = s
            elif start is not None:
                out.append((start, s - 1))
                start = None
        if start is not None:
            out.append((start, st.max_seq))
        return out

    def stats(self) -> dict:
        c = self.c
        return {
            "epoch": self.epoch, "max_seq": self._st.max_seq, "wm": self._st.wm, "pending_reset": self.pending_reset,
            "frames": dict(self.frames_by_type), "frames_by_conn": dict(self.frames_by_conn),
            "dup_news": c["dup_news"], "dup_trades": c["dup_trades"], "seq_gaps": c["seq_gaps"],
            "seq_conflict": c["seq_conflict"], "stale_books": c["stale_books"], "stale_quotes": c["stale_quotes"],
            "snapshots_in": c["snapshots_in"], "snapshots_relevant": c["snapshots_relevant"],
            "snapshots_redundant": c["snapshots_redundant"], "snapshots_dup": c["snapshots_dup"],
            "resets": dict(self.resets), "provisional_resets": c["provisional_resets"],
            "provisional_by_reason": dict(self.provisional_by_reason),
            "provisional_ignored": c["provisional_ignored"], "confirmed_from_provisional": c["confirmed_from_provisional"],
            "rejected_resets": c["rejected_resets"], "unknown_frames": c["unknown_frames"],
            "bad_frames": c["bad_frames"], "held": len(self._st.held), "trade_ids": len(self._trades),
            "prev_season_dropped": c["prev_season_dropped"], "bad_last_seq": c["bad_last_seq"],
        }

    # -------------------------------------------------------------------------- frames
    def ingest(self, sf: SourceFrame) -> list[MarketEvent]:
        """Normalize one frame (or connection change) of any connection, in arrival order."""
        if sf.control is not None:
            up = sf.control == "up"
            self.c["conn_up" if up else "conn_down"] += 1
            return [ConnEvent(conn=sf.conn, up=up, recv_mono=sf.recv_mono)]
        f = sf.frame
        if not isinstance(f, dict):
            self.c["bad_frames"] += 1
            return []
        t = f.get("type")
        self.frames_by_type[t if isinstance(t, str) else "?"] += 1
        self.frames_by_conn[_conn_name(sf.conn)] += 1
        if t == "news":
            m = f.get("message")
            if not isinstance(m, dict):
                self.c["bad_frames"] += 1
                return []
            return self._message(m, sf)
        if t in ("orderbook", "book"):
            return self._book(f, sf)
        if t in ("quote", "quotes"):
            return self._quote(f, sf)
        if t in ("trade", "trades"):
            return self._trade(f, sf)
        if t == "heartbeat":
            return [HeartbeatEvent(conn=sf.conn, recv_mono=sf.recv_mono)]
        if t == "subscription_ack":
            ch = f.get("channels")
            self.log.event("subscription_ack", conn=sf.conn,
                           symbols=len(f["symbols"]) if isinstance(f.get("symbols"), list) else None,
                           channels=ch if isinstance(ch, list) else None)
            return []
        if "seq" in f and "round" in f:                    # bare message (research feed.jsonl lines)
            m = {k: v for k, v in f.items() if k != "received_at"} if "received_at" in f else f
            return self._message(m, sf)
        self.c["unknown_frames"] += 1
        return []

    def poll(self, now: float) -> list[MarketEvent]:
        """Release the snapshots whose hold has expired (``stage="released"``), in arrival order."""
        st = self._st
        if not st.held:
            return []
        out: list[MarketEvent] = []
        keep = []
        for h in st.held:
            if h.release_at > now:
                keep.append(h)
                continue
            rel = self._relevant(h.last_seq)
            if rel:
                st.wm = h.last_seq
                self._advance(st)
                self.c["snapshots_relevant"] += 1
            else:
                self.c["snapshots_redundant"] += 1
            out.append(SnapshotEvent(msg=h.msg, last_seq=h.last_seq, round=h.round, phase=h.phase,
                                     market_time=h.market_time, stage="released", engine_relevant=rel,
                                     season_epoch=self.epoch, recv_mono=h.recv_mono))
        st.held = keep
        return out

    def _relevant(self, last_seq: int) -> bool:
        """A released snapshot is engine-relevant iff it covers a seq above the high-water mark."""
        return last_seq > self._st.wm

    # -------------------------------------------------------------------------- market data
    def _book(self, f: dict, sf: SourceFrame) -> list[MarketEvent]:
        sym = f.get("symbol")
        if not isinstance(sym, str) or not sym:
            self.c["bad_frames"] += 1
            return []
        raw = f.get("timestamp")
        ts = _server_ts(raw)
        st = self._st
        if ts is not None:
            prev = st.book_ts.get(sym)
            if prev is not None and ts <= prev:
                self.c["stale_books"] += 1
                return []
            st.book_ts[sym] = ts
        return [BookEvent(symbol=sym, bids=_levels(f.get("bids")), asks=_levels(f.get("asks")),
                          server_ts=raw if isinstance(raw, str) else "", recv_mono=sf.recv_mono)]

    def _quote(self, f: dict, sf: SourceFrame) -> list[MarketEvent]:
        sym = f.get("symbol")
        if not isinstance(sym, str) or not sym:
            self.c["bad_frames"] += 1
            return []
        raw = f.get("timestamp")
        ts = _server_ts(raw)
        st = self._st
        if ts is not None:
            prev = st.quote_ts.get(sym)
            if prev is not None and ts <= prev:
                self.c["stale_books"] += 1
                self.c["stale_quotes"] += 1
                return []
            st.quote_ts[sym] = ts
        return [QuoteEvent(symbol=sym, bid=_num(f.get("bid")), ask=_num(f.get("ask")),
                           bid_size=_qty(f.get("bid_size")), ask_size=_qty(f.get("ask_size")),
                           server_ts=raw if isinstance(raw, str) else "", recv_mono=sf.recv_mono)]

    def _trade(self, f: dict, sf: SourceFrame) -> list[MarketEvent]:
        sym = f.get("symbol")
        price = _num(f.get("price"))
        qty = _qty(f.get("quantity", f.get("qty")))
        if not isinstance(sym, str) or not sym or price is None or qty is None:
            self.c["bad_frames"] += 1
            return []
        tid = f.get("trade_id")
        raw = f.get("timestamp")
        key = ("id", str(tid)) if tid is not None else ("k", sym, str(raw), price, qty)
        if key in self._trades:
            self._trades.move_to_end(key)
            self.c["dup_trades"] += 1
            return []
        self._trades[key] = None
        if len(self._trades) > self.trade_cap:
            self._trades.popitem(last=False)
        return [TradeEvent(symbol=sym, price=price, qty=qty, trade_id="" if tid is None else str(tid),
                           server_ts=raw if isinstance(raw, str) else "", recv_mono=sf.recv_mono)]

    # -------------------------------------------------------------------------- news and snapshots
    def _message(self, m: dict, sf: SourceFrame) -> list[MarketEvent]:
        if self._from_prev_season(m, sf.recv_mono):
            self.c["prev_season_dropped"] += 1
            self.log.event("prev_season_message", type=m.get("type"), round=m.get("round"), time=m.get("time"),
                           seq=m.get("seq", m.get("last_seq")), epoch=self.epoch)
            return []
        if m.get("type") == "snapshot":
            return self._snapshot(m, sf)
        return self._news(m, sf)

    def _from_prev_season(self, m: dict, now: float) -> bool:
        """A late copy of the previous season (hardening): within ``reset_merge_s`` of a confirmed reset, its
        (round, date) is the old season's, the new season already has a calendar, and the date is at least 30
        days off that calendar extrapolated to the message's round (rounds are consecutive market days)."""
        if now > self._prev_until or not self._prev_days:
            return False
        md = _market_day(m)
        if md is None:
            return False
        r, date = md
        if self._prev_days.get(r) != date:
            return False
        days = self._st.days
        if not days or days.get(r) == date:
            return False
        try:
            d_msg = _dt.date.fromisoformat(date)
            for k, dk in days.items():
                expect = _dt.date.fromisoformat(dk) + _dt.timedelta(days=int(r) - int(k))
                if abs((d_msg - expect).days) < _PREV_SEASON_DAYS:
                    return False
        except (ValueError, TypeError, OverflowError):
            return False
        return True

    @staticmethod
    def _key(m: dict, mtype: str | None, seq: int | None) -> tuple:
        if seq is None:
            try:
                return ("json", json.dumps(m, sort_keys=True, default=str))
            except (TypeError, ValueError):
                return ("json", repr(sorted(m.items(), key=lambda kv: str(kv[0]))))
        if mtype in ANN_TYPES:
            h = m.get("headline")
            return ("ann", seq, mtype, h if isinstance(h, str) else repr(h))
        return ("seq", seq)

    @staticmethod
    def _fingerprint(m: dict, mtype: str | None, r: int | None, mt: str | None) -> tuple:
        storm = m.get("storm")
        return (mtype, r, storm if isinstance(storm, str) else None, mt)

    def _news(self, m: dict, sf: SourceFrame) -> list[MarketEvent]:
        now = sf.recv_mono
        mt_raw = m.get("type")
        mtype = mt_raw if isinstance(mt_raw, str) else None
        seq = _int(m.get("seq"))
        r = _int(m.get("round"))
        tm = m.get("time") if isinstance(m.get("time"), str) else None
        key = self._key(m, mtype, seq)
        fp = self._fingerprint(m, mtype, r, tm)
        prev = self._st.keys.get(key)
        if prev is not None and prev == fp:
            self.c["dup_news"] += 1
            return []
        out: list[MarketEvent] = []
        if mtype not in ANN_TYPES:
            reason = self._news_trigger(m, mtype, seq, r, prev is not None)
            if reason is not None:
                out.extend(self._trigger(reason, r, tm, now))
                prev = self._st.keys.get(key)
                if prev is not None and prev == fp:
                    self.c["dup_news"] += 1
                    return out
        if prev is not None:
            self._conflict(mtype, seq, now)
            return out
        st = self._st
        st.keys[key] = fp
        if seq is not None:
            if seq > st.max_seq + 1:
                self.c["seq_gaps"] += 1
            st.seen_seqs.add(seq)
            if seq > st.max_seq:
                st.max_seq = seq
            self._advance(st)
        self._note(m, mtype, r, tm, None)
        out.append(NewsEvent(msg=m, mtype=mtype or "unknown", seq=seq, round=r, market_time=tm,
                             season_epoch=self.epoch, recv_mono=now))
        return out

    def _news_trigger(self, m: dict, mtype: str | None, seq: int | None, r: int | None,
                      conflict: bool) -> str | None:
        st = self._st
        if (mtype == "storm_formed" and r == 1 and seq is not None and st.max_seq >= 50
                and seq < st.max_seq - self.seq_drop):
            return "storm_formed_r1"
        if r == 1 and conflict and (st.round_hi or 0) >= 2:
            return "round_decrease"
        md = _market_day(m)
        if md is not None and md[0] in st.days and st.days[md[0]] != md[1]:
            return "date_change"
        return None

    def _snapshot(self, m: dict, sf: SourceFrame) -> list[MarketEvent]:
        now = sf.recv_mono
        ls = _int(m.get("last_seq"))
        if ls is None or ls < -1:
            # only an explicit integer -1 starts a season (hardening): a missing or malformed last_seq is a bad
            # frame, never a reset and never a recovery snapshot
            self.c["bad_frames"] += 1
            self.c["bad_last_seq"] += 1
            self.log.event("snapshot_bad_last_seq", value=repr(m.get("last_seq"))[:40], round=m.get("round"),
                           time=m.get("time"), epoch=self.epoch)
            return []
        last_seq = ls
        r = _int(m.get("round"))
        ph = m.get("phase") if isinstance(m.get("phase"), str) else None
        tm = m.get("time") if isinstance(m.get("time"), str) else None
        skey = (last_seq, tm)
        self.c["snapshots_in"] += 1
        out: list[MarketEvent] = []
        if skey not in self._st.snap_keys:
            if self.pending_reset is not None and self._confirms(last_seq, r, ph, tm):
                out.append(self._confirm("snapshot_last_seq" if last_seq == -1 else "snapshot_consistent", now))
            else:
                reason = self._snap_trigger(last_seq, r, ph, tm)
                if reason is not None:
                    was_pending = self.pending_reset is not None
                    out.extend(self._trigger(reason, r, tm, now))
                    if not was_pending and self.pending_reset is not None and self._confirms(last_seq, r, ph, tm):
                        out.append(self._confirm("snapshot_consistent", now))
        st = self._st
        dup = skey in st.snap_keys
        out.append(SnapshotEvent(msg=m, last_seq=last_seq, round=r, phase=ph, market_time=tm, stage="arrived",
                                 engine_relevant=False, season_epoch=self.epoch, recv_mono=now))
        if dup:
            self.c["snapshots_dup"] += 1
            return out
        st.snap_keys.add(skey)
        st.held.append(_Held(release_at=now + self.hold_s, recv_mono=now, msg=m, last_seq=last_seq, round=r,
                             phase=ph, market_time=tm))
        self._note(m, "snapshot", r, tm, ph)
        return out

    def _snap_trigger(self, last_seq: int, r: int | None, ph: str | None, tm: str | None) -> str | None:
        st = self._st
        if last_seq == -1 and (st.max_seq >= 0 or st.wm >= 0):
            return "snapshot_last_seq"
        trading = ph in TRADING_PHASES
        if r == 1 and trading and (st.round_hi or 0) >= 2:
            return "round_decrease"
        if trading and r is not None and tm is not None and len(tm) >= 10 and r in st.days and st.days[r] != tm[:10]:
            return "date_change"
        return None

    def _note(self, m: dict, mtype: str | None, r: int | None, tm: str | None, ph: str | None) -> None:
        st = self._st
        if mtype not in ANN_TYPES and r is not None and (mtype != "snapshot" or ph in TRADING_PHASES):
            st.round_hi = r if st.round_hi is None else max(st.round_hi, r)
            if tm is not None and len(tm) >= 10:
                st.days.setdefault(r, tm[:10])
        if mtype == "storm_formed" and r == 1:
            st.sf_r1 = m
        if mtype == "snapshot":
            if ph:
                self._phase_hint = ph
        elif mtype in STORM_TYPES:
            self._phase_hint = "storm"
        elif mtype in AFTER_TYPES:
            self._phase_hint = "aftermath"

    @staticmethod
    def _advance(st: _EpochState) -> None:
        while st.wm + 1 in st.seen_seqs:
            st.wm += 1

    def _conflict(self, mtype: str | None, seq: int | None, now: float) -> None:
        self.c["seq_conflict"] += 1
        self.log.event("seq_conflict", seq=seq, type=mtype, epoch=self.epoch)
        dq = self._conflict_times
        dq.append(now)
        while dq and dq[0] < now - 60.0:
            dq.popleft()
        if len(dq) >= 3 and now - self._conflict_alert_at >= 60.0:
            self._conflict_alert_at = now
            self.log.alert("seq_conflict", f"{len(dq)} news messages within 60 s reuse a seq with other content",
                           seq=seq, type=mtype, epoch=self.epoch)

    # -------------------------------------------------------------------------- season resets
    def _trigger(self, reason: str, r: int | None, tm: str | None, now: float) -> list[MarketEvent]:
        if reason == "snapshot_last_seq" or not self.provisional:
            if self.pending_reset is not None:          # cannot happen for (b): a -1 snapshot confirms instead
                return [self._confirm(reason, now)]
            return [self._confirmed_reset(reason, now)]
        if self.pending_reset is not None:
            self.c["provisional_ignored"] += 1
            self.log.event("season_reset_ignored", reason=reason, pending=self.pending_reset, epoch=self.epoch)
            return []
        saved = self._st
        self._saved = saved
        self._st = _EpochState()
        self.pending_reset = reason
        self._pending = {"reason": reason, "round": r, "date": tm[:10] if tm is not None and len(tm) >= 10 else None,
                         "saved_max_seq": saved.max_seq, "since": now}
        self.c["provisional_resets"] += 1
        self.provisional_by_reason[reason] += 1
        self.log.event("season_reset_provisional", reason=reason, epoch=self.epoch, saved_max_seq=saved.max_seq,
                       saved_wm=saved.wm, round=r, date=self._pending["date"])
        return [SeasonResetEvent(reason=reason, new_epoch=self.epoch + 1, recv_mono=now, provisional=True)]

    def _remember_prev(self, old: _EpochState, now: float) -> None:
        if old.days:
            self._prev_days = dict(old.days)
            self._prev_until = now + self.merge_s

    def _confirmed_reset(self, reason: str, now: float) -> SeasonResetEvent:
        old = self._st
        self._remember_prev(old, now)
        self._st = _EpochState()
        self._saved = None
        self._pending = None
        self.pending_reset = None
        self.epoch += 1
        self.resets[reason] += 1
        self.log.event("season_reset", reason=reason, epoch=self.epoch, provisional_first=False,
                       old_max_seq=old.max_seq, old_wm=old.wm)
        return SeasonResetEvent(reason=reason, new_epoch=self.epoch, recv_mono=now, provisional=False)

    def _confirms(self, last_seq: int, r: int | None, ph: str | None, tm: str | None) -> bool:
        info = self._pending
        if info is None:
            return False
        if last_seq == -1:
            return True
        return (ph in TRADING_PHASES and r is not None and r == info["round"] and tm is not None
                and info["date"] is not None and tm[:10] == info["date"]
                and last_seq < info["saved_max_seq"] - self.seq_drop)

    def _confirm(self, by: str, now: float, reason: str | None = None) -> SeasonResetEvent:
        reason = reason or self.pending_reset or by
        since = self._pending["since"] if self._pending else now
        if self._saved is not None:
            self._remember_prev(self._saved, now)
        self.epoch += 1
        self.pending_reset = None
        self._saved = None
        self._pending = None
        self.resets[reason] += 1
        self.c["confirmed_from_provisional"] += 1
        self.log.event("season_reset", reason=reason, by=by, epoch=self.epoch, provisional_first=True,
                       pending_s=round(now - since, 3))
        return SeasonResetEvent(reason=reason, new_epoch=self.epoch, recv_mono=now, provisional=False)

    def force_season_reset(self, reason: str, now: float) -> SeasonResetEvent:
        """Confirmed reset ordered by the core ("comp_id" or "operator").

        With a provisional reset pending, the state cleared at the trigger already belongs to the new season,
        so it is kept and the pending reset is confirmed under ``reason``.
        """
        if self.pending_reset is not None:
            return self._confirm(reason, now, reason=reason)
        return self._confirmed_reset(reason, now)

    def confirm_season_reset(self, reason: str, now: float) -> SeasonResetEvent | None:
        """Pending -> confirmed (``reason`` names the evidence, e.g. "comp_id"); None if nothing is pending.

        The returned event carries the original trigger's reason; ``reason`` is logged as ``by``.
        """
        if self.pending_reset is None:
            return None
        return self._confirm(reason, now)

    def reject_season_reset(self, now: float) -> None:
        """Pending -> merged back: the trigger was spurious, so the saved state and the state since merge."""
        if self.pending_reset is None or self._saved is None:
            return
        cur, sv = self._st, self._saved
        for k, fp in cur.keys.items():
            sv.keys.setdefault(k, fp)
        sv.seen_seqs |= cur.seen_seqs
        sv.max_seq = max(sv.max_seq, cur.max_seq)
        sv.wm = max(sv.wm, cur.wm)
        self._advance(sv)
        sv.snap_keys |= cur.snap_keys
        sv.held = sorted(sv.held + cur.held, key=lambda h: h.release_at)
        for rr, d in cur.days.items():                 # the saved dates win; rounds first seen while pending join
            sv.days.setdefault(rr, d)
        if cur.round_hi is not None:
            sv.round_hi = cur.round_hi if sv.round_hi is None else max(sv.round_hi, cur.round_hi)
        if sv.sf_r1 is None:
            sv.sf_r1 = cur.sf_r1
        for name in ("book_ts", "quote_ts"):
            a, b = getattr(sv, name), getattr(cur, name)
            for sym, ts in b.items():
                if a.get(sym) is None or ts > a[sym]:
                    a[sym] = ts
        reason = self.pending_reset
        since = self._pending["since"] if self._pending else now
        self._st = sv
        self._saved = None
        self._pending = None
        self.pending_reset = None
        self.c["rejected_resets"] += 1
        self.log.event("season_reset_rejected", reason=reason, epoch=self.epoch, max_seq=sv.max_seq, wm=sv.wm,
                       pending_s=round(now - since, 3))
