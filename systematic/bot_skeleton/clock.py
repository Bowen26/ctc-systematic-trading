"""Market clock <-> real time.

*** UNTESTED SKELETON. ***

Facts (packet section 2, appendix, case.json timing; verified on all 1,800 research storms):
  * Market day 09:30-16:00 = 780 real s; 1 real s = 30 market s. Feed `time` is the market clock
    (fictional US/Eastern, ISO format without zone, e.g. "2025-08-04T11:31:30").
  * Storm `age` is REAL seconds since formation; formation is always 10:00 market (real +60 s).
    So market_time = 10:00 + 30*age seconds (age 124 -> 11:02:00).
  * Fixed schedule (real s after open): storm 60, advisories 90,150,...,510, dissipate 540,
    preliminary report 570 (14:15), final report 740 (15:40), close 780 (16:00).
  * Each round is a different calendar date; the season is identified by the competition id from
    GET /competitions/time (testnet: new id every season), never by the date alone.
  * The clock can stop (testnet: 2 min after each close and after a new season is prepared; then
    the market opens 1 real minute after the clock restarts). Live pauses: ASK.
"""
import time
from datetime import datetime

SPEED = 30.0


def parse_mt(s):
    """Parse a market-clock timestamp. Accepts a trailing 'Z' or offset defensively."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1]
    if len(s) > 19 and (s[19] in "+-"):
        s = s[:19]
    return datetime.fromisoformat(s)


def market_s_since_open(dt):
    return (dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond / 1e6) - 9.5 * 3600


def real_s_since_open(dt):
    """Real seconds since 09:30 of the same market day."""
    return market_s_since_open(dt) / SPEED


def age_to_real_since_open(age):
    return 60.0 + age


def real_since_open_to_market_str(date_str, real_s):
    secs = 9.5 * 3600 + real_s * SPEED
    h, rem = divmod(int(round(secs)), 3600)
    m, s = divmod(rem, 60)
    return f"{date_str}T{h:02d}:{m:02d}:{s:02d}"


def phase_of(real_s):
    if real_s < 0:
        return "closed"
    if real_s < 60:
        return "pre_storm"
    if real_s < 540:
        return "storm"
    if real_s < 780:
        return "aftermath"
    return "closed"


class RoundClock:
    """Maps local monotonic time to 'real seconds since this round's open'.

    Two sources: (1) GET /competitions/time polled ~1 Hz (authoritative, also gives state),
    (2) feed timestamps (each storm obs is published at its own market time). For (2) we keep the
    MINIMUM of (local_receive - real_since_open) over a sliding set of samples: the least-delayed
    message gives the best offset. Offsets reset when the round date changes or the clock stops.
    """

    def __init__(self, mono=time.monotonic):
        self.mono = mono
        self.date = None
        self.offset = None            # local_mono - real_since_open
        self.state = "unknown"        # from /competitions/time: running / paused / closed / ...
        self._samples = []

    def observe_market_time(self, mt_str, local=None, authoritative=False, rtt=0.0):
        local = self.mono() if local is None else local
        dt = parse_mt(mt_str)
        d = dt.date().isoformat()
        if d != self.date:
            self.date, self.offset, self._samples = d, None, []
        off = (local - rtt / 2.0) - real_s_since_open(dt)
        if authoritative:
            # server clock: blend toward it, but never let feed jitter move us later
            self.offset = off if self.offset is None else min(off, self.offset + 0.25)
        else:
            self._samples.append(off)
            self._samples = self._samples[-30:]
            best = min(self._samples)
            self.offset = best if self.offset is None else min(self.offset, best)

    def set_state(self, state):
        if state != self.state and state != "running":
            self._samples = []
        self.state = state

    def now_real(self):
        """Real seconds since today's open, or None if unknown."""
        if self.offset is None:
            return None
        return self.mono() - self.offset

    def phase(self):
        r = self.now_real()
        return "unknown" if r is None else phase_of(r)

    def seconds_until(self, event, schedule):
        r = self.now_real()
        if r is None:
            return None
        return schedule[event] - r

    def market_now_str(self):
        r = self.now_real()
        if r is None or self.date is None:
            return None
        return real_since_open_to_market_str(self.date, r)
