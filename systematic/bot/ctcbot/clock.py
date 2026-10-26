"""Market clock: round, phase, real seconds since the round's open, and pauses (DESIGN.md §4.A ``clock.py``).

The exchange publishes no millisecond clock, so the model anchors ``t`` (real seconds since this round's
09:30 open) to observations:

- **Samples.** Every trading-phase observation gives ``offset = recv_mono - t(market_time)``; a server sample
  uses ``(sent + recv) / 2``. Delivery only adds delay, so the feed offset is the *minimum* over recent samples.
- **Precedence.** (1) server (fresh < ``clock.server_fresh_s``; median of the last 5), unless fresh storm
  observations prove it stale: a feed message is never received before its market time, so a server time more
  than ``_SERVER_BEHIND_S`` (2 s) behind the storm observations is a frozen or cached answer and the storm offset
  is used instead, with a ``clock_server_stale`` alert (hardening); (2) storm observations
  (fresh < ``feed_fresh_s``; minimum over the last 60 s); (3) snapshots and other news (fresh <
  ``snapshot_fresh_s``; minimum over the last 60 s); (4) extrapolate the last anchor for at most
  ``max_extrapolate_s``; (5) unknown. Anchoring to recent storm frames absorbs the server tick running about
  0.05% slow.
- **Trading-phase rule.** A snapshot whose phase is not pre_storm, storm or aftermath never sets the round, the
  date or an anchor sample; it only feeds the pause rule. Within an epoch the round only increases.
- **Running.** A fresh server sample decides (``state == "running"``). Otherwise the clock runs once two
  trading-phase observations of this round show advancing market time, and stops when two snapshots show the
  same market time at least ``pause_same_time_s`` apart (``t`` then freezes). After a season reset it is not
  running until there is evidence.

The model is pure logic driven by the core: every method takes the time it needs as an argument.
"""
from __future__ import annotations

import collections
import math
import statistics
from typing import Any

from ctcbot.case import Schedule
from ctcbot.config import Config
from ctcbot.types import ClockReading, EventLog, Phase, ServerTime

TRADING_PHASES: tuple[str, ...] = ("pre_storm", "storm", "aftermath")
_FEED_WINDOW_S = 60.0
_SERVER_BEHIND_S = 2.0


class ClockModel:
    """Market clock for the current (epoch, round)."""

    def __init__(self, schedule: Schedule, cfg: Config, log: EventLog) -> None:
        self.s = schedule
        self.log = log
        g = lambda k, d: float(cfg.get("clock." + k, d))  # noqa: E731
        self.server_fresh_s = g("server_fresh_s", 3.0)
        self.feed_fresh_s = g("feed_fresh_s", 10.0)
        self.snapshot_fresh_s = g("snapshot_fresh_s", 35.0)
        self.pause_same_time_s = g("pause_same_time_s", 20.0)
        self.max_extrapolate_s = g("max_extrapolate_s", 40.0)
        self.window = max(5, int(cfg.get("clock.offset_window", 30)))
        self.epoch: int = 0
        self.comp_id: str | None = None
        self.round: int | None = None
        self.date: str | None = None
        # samples: (recv_mono, offset, t_m, date)
        self._samples: dict[str, collections.deque] = {k: collections.deque(maxlen=self.window)
                                                       for k in ("server", "storm", "snapshot", "news")}
        self._server_last: tuple[float, str, str | None, float | None] | None = None
        self._prog: tuple[float, float] | None = None
        self._feed_running = False
        self._stopped = False
        self._frozen_t: float | None = None
        self._paused_tm: float | None = None
        self._last_t_m: float | None = None
        self._last_anchor: tuple[float, float, str] | None = None
        self._snap_same: tuple[str, float] | None = None
        self._events: list[tuple[int, int]] = []
        self._reported: set[tuple[int, int]] = set()
        self._logged: tuple[str | None, str | None, float | None] = (None, None, None)
        self._ratelog_at: dict[str, float] = {}
        self._server_stale = False

    # -------------------------------------------------------------------------- helpers
    def _ratelog(self, kind: str, now: float, **fields: Any) -> None:
        last = self._ratelog_at.get(kind)
        if last is not None and now - last < 10.0:
            return
        self._ratelog_at[kind] = now
        self.log.event(kind, **fields)

    def _parse(self, market_time: Any, now: float) -> tuple[str, float] | None:
        try:
            return Schedule.market_to_t(market_time, self.s.speed)
        except (ValueError, TypeError):
            self._ratelog("clock_parse_warn", now, value=str(market_time)[:40])
            return None

    def _clear_feed(self) -> None:
        for k in ("storm", "snapshot", "news"):
            self._samples[k].clear()
        self._prog = None
        self._feed_running = False
        self._stopped = False
        self._frozen_t = None
        self._paused_tm = None
        self._last_t_m = None
        self._last_anchor = None

    def _new_round(self, r: int, date: str, now: float) -> None:
        prev = (self.round, self.date)
        self.round, self.date = r, date
        self._clear_feed()
        key = (self.epoch, r)
        if key not in self._reported:
            self._reported.add(key)
            self._events.append(key)
        self.log.event("clock_round", epoch=self.epoch, round=r, date=date, prev_round=prev[0], prev_date=prev[1])

    # -------------------------------------------------------------------------- inputs
    def observe_feed(self, market_time: str, round_: int | None, recv_mono: float, kind: str, season_epoch: int,
                     phase: str | None = None) -> None:
        """One feed observation. ``kind``: "storm" (storm observation), "news" (other news) or "snapshot"
        (``phase`` = the snapshot's phase)."""
        if season_epoch is not None and season_epoch != self.epoch:
            if season_epoch < self.epoch:
                return
            self.on_season_reset(season_epoch, None)
        parsed = self._parse(market_time, recv_mono)
        if parsed is None:
            return
        date, t_m = parsed
        if kind == "snapshot":
            self._pause_rule(market_time, date, t_m, recv_mono)
            if phase not in TRADING_PHASES:
                if self.date is not None and date == self.date:
                    self._phase_check(phase, recv_mono)
                return
        r = round_ if isinstance(round_, int) and not isinstance(round_, bool) else None
        if r is None:
            if self.round is None or date != self.date:
                return
            r = self.round
        if self.round is None or r > self.round:
            self._new_round(r, date, recv_mono)
        elif r < self.round:
            self._ratelog("clock_round_back", recv_mono, round=r, current=self.round, epoch=self.epoch)
            return
        elif date != self.date:
            self._ratelog("clock_date_mismatch", recv_mono, date=date, current=self.date, round=r)
            return
        if self._prog is None or t_m > self._prog[0] + 1e-9:
            if self._prog is not None:
                if not self._feed_running or self._stopped:
                    self.log.event("clock_running", epoch=self.epoch, round=self.round, t=round(t_m, 3),
                                   source="feed")
                self._feed_running = True
                self._stopped = False
                self._frozen_t = None
                self._paused_tm = None
            self._prog = (t_m, recv_mono)
        if self._last_t_m is None or t_m > self._last_t_m:
            self._last_t_m = t_m
        if self._paused_tm is not None and abs(t_m - self._paused_tm) < 1e-9:
            return                                   # a repeated pause time is not an anchor sample
        k = "storm" if kind == "storm" else ("snapshot" if kind == "snapshot" else "news")
        self._samples[k].append((recv_mono, recv_mono - t_m, t_m, None))
        if kind == "snapshot":
            self._phase_check(phase, recv_mono)

    def _pause_rule(self, time_str: str, date: str, t_m: float, recv: float) -> None:
        ss = self._snap_same
        if ss is None or ss[0] != time_str:
            self._snap_same = (time_str, recv)
            return
        if recv - ss[1] < self.pause_same_time_s:
            return
        if not self._stopped:
            self.log.event("clock_stopped", epoch=self.epoch, round=self.round, market_time=time_str,
                           same_for_s=round(recv - ss[1], 3))
        self._stopped = True
        self._feed_running = False
        if self.date is not None and date == self.date:
            self._frozen_t = t_m
            self._paused_tm = t_m
            for k in ("storm", "snapshot", "news"):
                dq = self._samples[k]
                keep = [s for s in dq if abs(s[2] - t_m) >= 1e-9]
                if len(keep) != len(dq):
                    dq.clear()
                    dq.extend(keep)
            if self._last_anchor is not None and self._last_anchor[2] != "server":
                self._last_anchor = None
        elif self.date is not None:
            cur = self._t_from_anchor(recv)
            self._frozen_t = max(self.s.close, cur if cur is not None else self.s.close)

    def _phase_check(self, phase: str | None, now: float) -> None:
        t = self._t_from_anchor(now)
        if t is None or phase is None:
            return
        ph = self.s.phase(t)
        if ph.value != phase:
            self._ratelog("clock_phase_mismatch", now, snapshot_phase=phase, clock_phase=ph.value, t=round(t, 3),
                          round=self.round)

    def observe_server(self, st: ServerTime, sent_mono: float, recv_mono: float) -> None:
        """A ``GET /competitions/time`` result; ``(sent + recv) / 2`` is taken as the sampling instant."""
        if st is None:
            return
        mid = (float(sent_mono) + float(recv_mono)) / 2.0
        if st.comp_id:
            self.comp_id = st.comp_id
        state = (st.state or "").strip().lower()
        date: str | None = None
        t_m: float | None = None
        if st.fictional_time:
            parsed = self._parse(st.fictional_time, recv_mono)
            if parsed is not None:
                date, t_m = parsed
        prev = self._server_last
        self._server_last = (float(recv_mono), state, date, t_m)
        if prev is None or prev[1] != state:
            self.log.event("clock_server_state", state=state, fictional_time=st.fictional_time, comp_id=st.comp_id)
        if state == "running" and t_m is not None:
            self._samples["server"].append((mid, mid - t_m, t_m, date))

    def on_season_reset(self, epoch: int, comp_id: str | None) -> None:
        """A confirmed season reset: forget the round, the date, every sample and the running evidence."""
        self.epoch = int(epoch)
        if comp_id is not None:
            self.comp_id = comp_id
        self.round = None
        self.date = None
        self._clear_feed()
        self._samples["server"].clear()
        self._server_last = None
        self._snap_same = None
        self.log.event("clock_season_reset", epoch=self.epoch, comp_id=self.comp_id)

    # -------------------------------------------------------------------------- reading
    def _server_fresh(self, now: float) -> tuple[float, str, str | None, float | None] | None:
        sl = self._server_last
        if sl is None or now - sl[0] >= self.server_fresh_s:
            return None
        if sl[2] is not None and self.date is not None and sl[2] != self.date:
            return None
        return sl

    def _anchor(self, now: float) -> tuple[float | None, str | None, float | None]:
        """(offset, source, newest sample mono) from the freshest source, or (None, None, None)."""
        sv = [s for s in self._samples["server"] if self.date is None or s[3] is None or s[3] == self.date]
        st = self._samples["storm"]
        storm_off = None
        if st and now - st[-1][0] < self.feed_fresh_s:
            storm_off = min([s[1] for s in st if now - s[0] <= _FEED_WINDOW_S] or [st[-1][1]])
        if sv and now - sv[-1][0] < self.server_fresh_s:
            srv_off = statistics.median(s[1] for s in sv[-5:])
            if storm_off is None or srv_off - storm_off <= _SERVER_BEHIND_S:
                if self._server_stale:
                    self._server_stale = False
                    self.log.event("clock_server_ok", epoch=self.epoch, round=self.round)
                return srv_off, "server", sv[-1][0]
            if not self._server_stale:          # the server says an earlier time than the feed already showed
                self._server_stale = True
                try:
                    self.log.alert("clock_server_stale", f"server time is {srv_off - storm_off:.1f} s behind the storm "
                                   "observations: using the feed clock", behind_s=round(srv_off - storm_off, 3),
                                   epoch=self.epoch, round=self.round)
                except Exception:  # noqa: BLE001
                    pass
        if storm_off is not None:
            return storm_off, "storm", st[-1][0]
        other = list(self._samples["snapshot"]) + list(self._samples["news"])
        if other:
            newest = max(s[0] for s in other)
            if now - newest < self.snapshot_fresh_s:
                offs = [s[1] for s in other if now - s[0] <= _FEED_WINDOW_S] or [max(other)[1]]
                return min(offs), "snapshot", newest
        return None, None, None

    def _t_from_anchor(self, now: float) -> float | None:
        off, _, _ = self._anchor(now)
        if off is None and self._last_anchor is not None and now - self._last_anchor[1] <= self.max_extrapolate_s:
            off = self._last_anchor[0]
        return None if off is None else now - off

    def _running(self, now: float) -> tuple[bool, tuple | None]:
        sl = self._server_fresh(now)
        if sl is not None:
            return sl[1] == "running", sl
        return self._feed_running and not self._stopped, None

    def read(self, now: float) -> ClockReading:
        """The clock at ``now``."""
        running, sl = self._running(now)
        offset, source, newest = self._anchor(now)
        if offset is not None:
            self._last_anchor = (offset, newest, source)
            quality = "server" if source == "server" else "feed"
        elif self._last_anchor is not None and now - self._last_anchor[1] <= self.max_extrapolate_s:
            offset, newest, source = self._last_anchor[0], self._last_anchor[1], "extrapolated"
            quality = "extrapolated"
        else:
            quality = "unknown"
            source = None
        t: float | None
        if running:
            t = None if offset is None else now - offset
        elif sl is not None and sl[3] is not None and (self.date is None or sl[2] == self.date):
            t = sl[3]
        elif self._frozen_t is not None:
            t = self._frozen_t
        else:
            t = self._last_t_m
        if t is not None and self.round is None and self.date is None and quality != "server":
            t = None
        phase = self.s.phase(t)
        anchor_age = None if newest is None else max(0.0, now - newest)
        lq, ls, lo = self._logged
        if quality != lq or source != ls or (offset is not None and lo is not None and abs(offset - lo) > 0.25):
            self._logged = (quality, source, offset)
            self.log.event("clock", quality=quality, source=source, offset=offset, t=None if t is None else round(t, 3),
                           round=self.round, epoch=self.epoch, running=running, phase=phase.value)
        return ClockReading(known=t is not None, season_epoch=self.epoch, comp_id=self.comp_id, round=self.round,
                            date=self.date, t=t, phase=phase, running=running, quality=quality,
                            anchor_age_s=anchor_age)

    def t_to_mono(self, t: float, now: float) -> float | None:
        """The mono time at which this round reaches real second ``t``; None while stopped or unanchored."""
        running, _ = self._running(now)
        if not running:
            return None
        offset, _, _ = self._anchor(now)
        if offset is None and self._last_anchor is not None and now - self._last_anchor[1] <= self.max_extrapolate_s:
            offset = self._last_anchor[0]
        return None if offset is None else offset + float(t)

    def round_events(self) -> list[tuple[int, int]]:
        """(epoch, round) pairs that became current since the last call, each reported once."""
        out, self._events = self._events, []
        return out

    def state(self) -> dict:
        """Diagnostics for the status line and logs."""
        return {"epoch": self.epoch, "round": self.round, "date": self.date, "comp_id": self.comp_id,
                "feed_running": self._feed_running, "stopped": self._stopped, "frozen_t": self._frozen_t,
                "samples": {k: len(v) for k, v in self._samples.items()},
                "server_state": None if self._server_last is None else self._server_last[1]}
