"""Market-data WebSocket client, feed normalizer and recorder.

*** UNTESTED SKELETON. The WebSocket adapters below have NEVER been run against any server. ***

Exchange rules (packet appendix):
  * /ws/v1/market-data, bot key in the X-API-Key header.
  * Send ONE subscription message within 10 s of connecting; every later message is ignored, so
    it must name all symbols and all channels we will ever need.
  * More than 10 frames sent in one second -> the exchange closes the connection. We send one
    frame per connection (plus protocol pongs) and guard sends at WS_MAX_SEND_PER_S.
  * News frames are {"type": "news", "message": {...}}. Quote frames (starter kit) are
    {"type": "quote", "symbol": ..., "bid": ...}; trades/orderbook formats: RECORD AND INSPECT.
  * Delivery: in seq order; after an exchange problem missed messages arrive together and in
    order, but for storm/track only the latest of the group; after a long gap a snapshot follows;
    rarely a duplicate. Snapshots have no seq; they carry last_seq.
  * Our recorder writes {**frame, "received_at": unix_s} per line, exactly like the starter kit's
    record_feed.py, so check_recording.py can verify the BOT's own log.

WEBSOCKET ADAPTER: the stdlib has no WebSocket client. The starter kit uses the third-party
`websockets>=14` package (pip install on the team laptops). WebsocketsLibAdapter wraps it.
MinimalStdlibWS is a fallback RFC 6455 client (text frames, ping/pong, close, no extensions);
it is UNTESTED -- prefer the websockets package and keep this only as an emergency fallback.

ROBUST FEED (testnet/analysis/TESTNET_FINDINGS.md section 1.4). The 23 Oct testnet recording had 5 abrupt drops in an
hour, one 31.7-s outage and 24 lost messages. RobustFeed keeps two connections (ideally on two network paths) and
FeedMerger turns their frames into one deduplicated Event stream:
  * news merged by (season, seq): first copy forwarded, repeats dropped; the seen-set is reset when the season
    changes (seq restarts, a snapshot shows last_seq -1, the round goes back, or a round's market date changes);
  * snapshots held 1 s, then forwarded only if they cover a seq not yet forwarded (last_seq above the contiguous
    high-water mark), so a lagging connection's live copy wins the race and a snapshot is never fed twice;
  * books and quotes: per symbol the newest server timestamp wins; trades deduplicated by trade_id;
  * reconnect after 0.25 s, then 0.5 s doubling with +-25% jitter, capped at 2 s (not the 1-2-4-8-16 s ladder);
  * per-connection watchdogs: no frame for 3 s (storm), 8 s (pre-storm, aftermath) or 35 s (pause) -> reconnect;
  * a storm-phase gap of more than 10 s that no connection filled emits feed_outage (S1-r3 then uses the wide
    epsilon, playbook section 4.3) and feed_restored when frames return.
FeedMerger is pure logic (offline test: test_feed_merge.py replays the recording as two connections). It keeps the
FeedNormalizer attributes main.py reads (max_seq, duplicates, gaps, reset). To use it in main.py, build
RobustFeed(..., phase_fn=lambda: self.state.phase) instead of FeedClient, read stats from feed.merger, and do not
call merger.reset() on the season_reset event the merger itself emits (it has already reset).
"""
import asyncio
import base64
import collections
import datetime as _dt
import hashlib
import json
import os
import random
import ssl
import struct
import time
import urllib.parse

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class SendGuard:
    """Never send more than `per_s` frames in any rolling second."""

    def __init__(self, per_s):
        self.per_s = per_s
        self._t = []

    def allow(self):
        now = time.monotonic()
        self._t = [t for t in self._t if now - t < 1.0]
        if len(self._t) >= self.per_s:
            return False
        self._t.append(now)
        return True


class WSAdapter:
    """Interface: connect(url, headers), send(text), recv() -> str | None (None = closed), close()."""

    async def connect(self, url, headers):
        raise NotImplementedError

    async def send(self, text):
        raise NotImplementedError

    async def recv(self):
        raise NotImplementedError

    async def close(self):
        pass


class WebsocketsLibAdapter(WSAdapter):
    """ADAPTER for the starter kit's approach (`websockets>=14`). Third-party: not stdlib."""

    def __init__(self, guard, user_agent, close_timeout=1.0, **connect_kwargs):
        """connect_kwargs go to the event loop's create_connection, e.g. local_addr=("192.168.1.20", 0) to pin a
        connection to one network interface (Wi-Fi vs hotspot) for RobustFeed's second path."""
        self.guard, self.user_agent, self._ws = guard, user_agent, None
        self.close_timeout, self.connect_kwargs = close_timeout, connect_kwargs

    async def connect(self, url, headers):
        import websockets  # noqa: imported lazily so the rest of the bot stays stdlib-only
        hdrs = {k: v for k, v in headers.items() if k.lower() != "user-agent"}
        try:
            self._ws = await websockets.connect(url, additional_headers=hdrs, user_agent_header=self.user_agent,
                                                max_size=2 ** 23, open_timeout=5, **self.connect_kwargs)
        except TypeError:   # older/newer signature: fall back to the starter-kit call
            self._ws = await websockets.connect(url, additional_headers=hdrs, **self.connect_kwargs)

    async def send(self, text):
        if not self.guard.allow():
            raise RuntimeError("send guard: would exceed frames/s")
        await self._ws.send(text)

    async def recv(self):
        try:
            return await self._ws.recv()
        except Exception:        # websockets.ConnectionClosed and friends
            return None

    async def close(self):
        if self._ws is not None:
            try:   # a dead peer never answers the close handshake: give up after close_timeout and abort
                await asyncio.wait_for(self._ws.close(), self.close_timeout)
            except Exception:
                tr = getattr(self._ws, "transport", None)
                if tr is not None:
                    tr.abort()


class MinimalStdlibWS(WSAdapter):
    """FALLBACK stdlib RFC 6455 client. UNTESTED. Client frames are masked as the RFC requires."""

    def __init__(self, guard, connect_timeout=5.0):
        self.guard, self.connect_timeout = guard, connect_timeout
        self._r = self._w = None

    async def connect(self, url, headers):
        u = urllib.parse.urlsplit(url)
        secure = u.scheme == "wss"
        port = u.port or (443 if secure else 80)
        ctx = ssl.create_default_context() if secure else None
        self._r, self._w = await asyncio.wait_for(
            asyncio.open_connection(u.hostname, port, ssl=ctx, server_hostname=u.hostname if secure else None),
            self.connect_timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
        lines = [f"GET {path} HTTP/1.1", f"Host: {u.hostname}", "Upgrade: websocket", "Connection: Upgrade",
                 f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
        lines += [f"{k}: {v}" for k, v in headers.items()]
        self._w.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        await self._w.drain()
        status = await asyncio.wait_for(self._r.readline(), self.connect_timeout)
        if b" 101 " not in status:
            raise ConnectionError(f"websocket upgrade refused: {status[:80]!r}")
        hdrs = {}
        while True:
            line = await asyncio.wait_for(self._r.readline(), self.connect_timeout)
            if line in (b"\r\n", b"\n", b""):
                break
            k, _, v = line.decode("latin-1").partition(":")
            hdrs[k.strip().lower()] = v.strip()
        expect = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
        if hdrs.get("sec-websocket-accept") != expect:
            raise ConnectionError("bad Sec-WebSocket-Accept")

    async def _send_frame(self, opcode, payload):
        n = len(payload)
        head = bytearray([0x80 | opcode])
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack("!H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack("!Q", n)
        mask = os.urandom(4)
        head += mask
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._w.write(bytes(head) + body)
        await self._w.drain()

    async def send(self, text):
        if not self.guard.allow():
            raise RuntimeError("send guard: would exceed frames/s")
        await self._send_frame(0x1, text.encode("utf-8"))

    async def recv(self):
        buf, first_op = bytearray(), None
        try:
            while True:
                b1, b2 = await self._r.readexactly(2)
                fin, op, masked, ln = b1 & 0x80, b1 & 0x0F, b2 & 0x80, b2 & 0x7F
                if ln == 126:
                    ln = struct.unpack("!H", await self._r.readexactly(2))[0]
                elif ln == 127:
                    ln = struct.unpack("!Q", await self._r.readexactly(8))[0]
                mask = await self._r.readexactly(4) if masked else None
                payload = await self._r.readexactly(ln)
                if mask:
                    payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
                if op == 0x9:                       # ping -> pong, only if the send guard allows
                    if self.guard.allow():
                        await self._send_frame(0xA, payload)
                    continue
                if op == 0xA:
                    continue
                if op == 0x8:
                    return None
                if op in (0x1, 0x2):
                    first_op, buf = op, bytearray(payload)
                elif op == 0x0:
                    buf.extend(payload)
                if fin:
                    return buf.decode("utf-8") if first_op == 0x1 else bytes(buf).decode("utf-8", "replace")
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            return None

    async def close(self):
        if self._w is not None:
            try:
                self._w.close()
            except Exception:
                pass


_CATEGORY = {"snapshot": "snapshot", "md_orderbook": "orderbook", "md_book": "orderbook", "md_quote": "quote",
             "md_trade": "trade", "season_reset": "status", "ws_connected": "status", "ws_disconnected": "status",
             "feed_outage": "status", "feed_restored": "status", "unknown": "unknown"}


class Event:
    """One feed event.
    kind      fine type: the news message type ("storm", "track", "advisory", "impact", "storm_formed",
              "storm_dissipated", "damage_report", ...), "snapshot", "md_orderbook", "md_quote", "md_trade", or a
              status kind ("season_reset", "ws_connected", "ws_disconnected", "feed_outage", "feed_restored")
    category  coarse kind: "news", "snapshot", "orderbook", "quote", "trade", "status" or "unknown"
    msg       the raw dict: the news message itself (the frame's "message") for news and snapshots, the whole
              frame for orderbook / quote / trade, a small dict for status events
    recv_ts   unix seconds when the frame arrived (alias: received_at)
    seq       the news seq (None for everything else)
    conn      index of the connection that delivered this copy first (None for some status events)
    """
    __slots__ = ("kind", "msg", "recv_ts", "seq", "conn")

    def __init__(self, kind, msg, recv_ts, seq=None, conn=None):
        self.kind, self.msg, self.recv_ts, self.seq, self.conn = kind, msg, recv_ts, seq, conn

    @property
    def category(self):
        return _CATEGORY.get(self.kind, "news")

    @property
    def received_at(self):
        return self.recv_ts

    def __repr__(self):
        return f"Event({self.kind}, seq={self.seq})"


class FeedNormalizer:
    """Turns raw frames into deduplicated Events. Pure logic: replay.py tests it with chaos feeds."""

    def __init__(self):
        self.reset()

    def reset(self, season_key=None):
        self.season_key = season_key
        self.seen = set()
        self.max_seq = -1
        self.duplicates = 0
        self.gaps = []
        self.snapshot_last_seq = -1

    def _new_season(self, m):
        seq = m["seq"]
        return self.max_seq >= 50 and seq < self.max_seq - 50 and (
            (m.get("type") == "storm_formed" and m.get("round") == 1) or seq < 5)

    def ingest(self, frame, recv_ts=None):
        recv_ts = time.time() if recv_ts is None else recv_ts
        t = frame.get("type")
        if t == "news":
            m = frame.get("message")
            if not isinstance(m, dict):
                return []
            return self.ingest_message(m, recv_ts)
        if t in ("quote", "quotes", "trade", "trades", "orderbook", "book"):
            return [Event("md_" + t.rstrip("s"), frame, recv_ts)]
        if "seq" in frame and "round" in frame:        # bare message (research feed.jsonl lines)
            return self.ingest_message(frame, recv_ts)
        return [Event("unknown", frame, recv_ts)]

    def ingest_message(self, m, recv_ts):
        mt = m.get("type")
        if mt == "snapshot":
            self.snapshot_last_seq = max(self.snapshot_last_seq, int(m.get("last_seq") or -1))
            return [Event("snapshot", m, recv_ts)]
        seq = m.get("seq")
        if seq is None:
            return [Event(mt or "unknown", m, recv_ts)]
        out = []
        if self._new_season(m):
            self.reset()
            out.append(Event("season_reset", m, recv_ts))
        if seq in self.seen:
            self.duplicates += 1
            return out
        self.seen.add(seq)
        if self.max_seq >= 0 and seq > self.max_seq + 1:
            self.gaps.append((self.max_seq, seq))
        self.max_seq = max(self.max_seq, seq)
        out.append(Event(mt, m, recv_ts, seq))
        return out


class Recorder:
    """Raw frame log in record_feed.py format (one JSON per line + received_at)."""

    def __init__(self, path):
        self.path = path
        self._f = open(path, "a", buffering=1) if path else None

    def write(self, frame, recv_ts):
        if self._f:
            self._f.write(json.dumps({**frame, "received_at": recv_ts}) + "\n")

    def close(self):
        if self._f:
            self._f.close()


class FeedClient:
    """Connect, subscribe once, read forever, reconnect with backoff. Pushes Events to a queue."""

    def __init__(self, url, headers, symbols, channels, queue, normalizer, recorder, adapter_factory,
                 idle_timeout=75.0, log=print):
        self.url, self.headers, self.queue = url, headers, queue
        self.subscribe_msg = {"action": "subscribe", "symbols": list(symbols), "channels": list(channels)}
        self.normalizer, self.recorder, self.adapter_factory = normalizer, recorder, adapter_factory
        self.idle_timeout, self.log = idle_timeout, log
        self.connected = False
        self.last_frame_mono = 0.0
        self.connects = 0
        self._ws = None
        self._stop = False

    async def force_reconnect(self):
        if self._ws is not None:
            await self._ws.close()

    def stop(self):
        self._stop = True

    async def run(self):
        backoff = 0.5
        while not self._stop:
            ws = self._ws = self.adapter_factory()
            try:
                await ws.connect(self.url, self.headers)
                await ws.send(json.dumps(self.subscribe_msg))      # the ONLY app frame we ever send
                self.connected, self.connects, backoff = True, self.connects + 1, 0.5
                self.queue.put_nowait(Event("ws_connected", {"n": self.connects}, time.time()))
                while not self._stop:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self.idle_timeout)
                    if raw is None:
                        break
                    recv_ts = time.time()
                    self.last_frame_mono = time.monotonic()
                    try:
                        frame = json.loads(raw)
                    except ValueError:
                        continue
                    self.recorder.write(frame, recv_ts)
                    for evt in self.normalizer.ingest(frame, recv_ts):
                        self.queue.put_nowait(evt)
            except Exception as exc:      # CancelledError is BaseException: shutdown still works
                self.log(f"feed: disconnected ({exc!r})")
            finally:
                self.connected = False
                await ws.close()
                self.queue.put_nowait(Event("ws_disconnected", {}, time.time()))
            if not self._stop:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 5.0)


# =============================================================================================
# Robust two-connection feed (TESTNET_FINDINGS.md section 1.4)
# =============================================================================================
RECONNECT_FIRST_S = 0.25      # first retry after a drop
RECONNECT_BASE_S = 0.5        # then 0.5, 1, 2 s, each +-RECONNECT_JITTER, never above the cap
RECONNECT_CAP_S = 2.0
RECONNECT_JITTER = 0.25
RECONNECT_RESET_UP_S = 2.0    # a connection that stayed up this long restarts the ladder at 0.25 s
# Longest gap between frames while connected on 23 Oct: storm 2.12 s, pre-storm 2.54, aftermath 5.21, pause 29.57.
WATCHDOG_S = {"storm": 3.0, "pre_storm": 8.0, "aftermath": 8.0, "closed": 35.0}
WATCHDOG_DEFAULT_S = 35.0     # phase unknown
SNAPSHOT_HOLD_S = 1.0
OUTAGE_ALERT_S = 10.0         # storm gap no connection filled -> S1-r3 wide epsilon (playbook section 4.3)
TRADING_PHASES = ("pre_storm", "storm", "aftermath")


def reconnect_delay(attempt, rng=random):
    """Seconds before reconnect attempt `attempt` (0 = first retry after a drop): 0.25, then 0.5 / 1 / 2 / 2 ...
    with +-25% jitter, capped at 2 s."""
    if attempt <= 0:
        return RECONNECT_FIRST_S
    base = min(RECONNECT_CAP_S, RECONNECT_BASE_S * 2.0 ** (attempt - 1))
    return min(RECONNECT_CAP_S, base * (1.0 - RECONNECT_JITTER + 2.0 * RECONNECT_JITTER * rng.random()))


def watchdog_s(phase):
    """Seconds without any frame after which a connection is torn down and reconnected."""
    return WATCHDOG_S.get(phase, WATCHDOG_DEFAULT_S)


def _int(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, str) and x.lstrip("-").isdigit():
        return int(x)
    return None


def _server_ts(s):
    """Server ISO timestamp -> float seconds (naive stamps are read as local time, consistently), or None."""
    if not isinstance(s, str):
        return None
    try:
        return _dt.datetime.fromisoformat(s).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _market_day(m):
    """(round, market date) of a news message or snapshot inside the trading day, else None. A closed-phase
    snapshot's market clock runs past midnight, so it is not dated."""
    r = _int(m.get("round"))
    t = m.get("time")
    if r is None or not isinstance(t, str) or len(t) < 10:
        return None
    if m.get("type") == "snapshot" and m.get("phase") not in TRADING_PHASES:
        return None
    return r, t[:10]


class FeedMerger:
    """Merges the frames of several market-data connections into one deduplicated Event stream. Pure logic.

    ingest(conn, frame, recv_ts) -> [Event]   every frame of every connection, in arrival order
    poll(now, phase=None) -> [Event]          call often (RobustFeed: every 0.1 s): releases held snapshots and
                                              raises feed_outage
    Event kinds: the news types (first copy of each seq), "snapshot", "md_orderbook", "md_quote", "md_trade",
    "season_reset", "feed_outage", "feed_restored", "unknown".
    Assumes no connection lags by more than the pause between seasons (minutes): the season detection would
    otherwise take the late frames for a new season.
    """

    def __init__(self, snapshot_hold_s=SNAPSHOT_HOLD_S, outage_alert_s=OUTAGE_ALERT_S, max_ids=200000):
        self.snapshot_hold_s, self.outage_alert_s, self.max_ids = snapshot_hold_s, outage_alert_s, max_ids
        self.trade_ids = collections.OrderedDict()   # trade ids are global: kept across seasons, bounded
        self.misc_keys = collections.OrderedDict()   # news without seq, deduplicated by content
        self.stats = collections.Counter()
        self.first_copy = collections.Counter()      # conn -> news messages it delivered first
        self.last_frame = {}                         # conn -> recv_ts of its latest frame
        self.last_any = None
        self.outage_since = None
        self.outages = []                            # (start, gap s) of storm gaps longer than outage_alert_s
        self.phase = None                            # from snapshots and news: fallback phase for the watchdogs
        self.last_reset_reason = None
        self.season_key = None
        self.book_ts, self.quote_ts = {}, {}
        self._clear_season()

    def _clear_season(self):
        self.seen = set()          # seqs forwarded this season
        self.max_seq = -1
        self.wm = -1               # every seq <= wm was forwarded or is covered by a forwarded snapshot
        self.held = []             # (release_at, recv_ts, conn, snapshot, last_seq), arrival order
        self.days = {}             # round -> market date, this season
        self.cur_round = None
        self.duplicates = 0

    def reset(self, season_key=None):
        """FeedNormalizer API: forget the season (seen-set, held snapshots) and the book/quote timestamps. The merger
        resets itself on a season change; do not call this on its own season_reset event."""
        self.season_key = season_key
        self._clear_season()
        self.book_ts, self.quote_ts = {}, {}

    @property
    def gaps(self):
        """[(first, last)] runs of seqs <= max_seq never forwarded this season (a snapshot may have covered them)."""
        out, start = [], None
        for s in range(self.max_seq + 1):
            if s not in self.seen:
                if start is None:
                    start = s
            elif start is not None:
                out.append((start, s - 1))
                start = None
        if start is not None:
            out.append((start, self.max_seq))
        return out

    # -- frames -------------------------------------------------------------------------------------
    def ingest(self, conn, frame, recv_ts):
        self.last_frame[conn] = recv_ts
        if self.last_any is None or recv_ts > self.last_any:
            self.last_any = recv_ts
        out = []
        if self.outage_since is not None:
            gap = recv_ts - self.outage_since
            self.outages.append((self.outage_since, gap))
            out.append(Event("feed_restored", {"type": "feed_restored", "since": self.outage_since, "gap_s": gap},
                             recv_ts, conn=conn))
            self.outage_since = None
        t = frame.get("type")
        if t == "news":
            m = frame.get("message")
            if isinstance(m, dict):
                out += self._news(conn, m, recv_ts)
            return out
        if t in ("orderbook", "book", "quote", "quotes"):
            kind = "md_" + t.rstrip("s")
            sym = frame.get("symbol")
            ts = _server_ts(frame.get("timestamp"))
            if sym is not None and ts is not None:
                last = self.book_ts if t in ("orderbook", "book") else self.quote_ts
                prev = last.get(sym)
                if prev is not None and ts <= prev:          # the other connection already gave us this or newer
                    self.stats["stale_" + kind] += 1
                    return out
                last[sym] = ts
            out.append(Event(kind, frame, recv_ts, conn=conn))
            return out
        if t in ("trade", "trades"):
            tid = frame.get("trade_id")
            key = tid if tid is not None else (frame.get("symbol"), frame.get("timestamp"), frame.get("price"),
                                               frame.get("quantity"))
            if self._seen_key(self.trade_ids, key):
                self.stats["dup_md_trade"] += 1
                return out
            out.append(Event("md_trade", frame, recv_ts, conn=conn))
            return out
        if t in ("heartbeat", "subscription_ack"):          # liveness only; RobustFeed reports connects itself
            return out
        if "seq" in frame and "round" in frame:            # bare message (research feed.jsonl lines)
            return out + self._news(conn, frame, recv_ts)
        out.append(Event("unknown", frame, recv_ts, conn=conn))
        return out

    def poll(self, now, phase=None):
        out = []
        if self.held:
            keep = []
            for h in self.held:
                if h[0] > now:
                    keep.append(h)
                    continue
                _, ts, conn, m, last_seq = h
                if last_seq > self.wm:     # it covers a seq no connection delivered: resync from it
                    self.wm = last_seq
                    self._advance()
                    self.stats["snapshots_forwarded"] += 1
                    out.append(Event("snapshot", m, ts, conn=conn))
                else:
                    self.stats["snapshots_redundant"] += 1
            self.held = keep
        ph = phase or self.phase
        if (self.outage_since is None and self.last_any is not None and ph == "storm"
                and now - self.last_any > self.outage_alert_s):
            self.outage_since = self.last_any
            self.stats["outages"] += 1
            out.append(Event("feed_outage", {"type": "feed_outage", "since": self.last_any,
                                             "gap_s": now - self.last_any, "phase": ph}, now))
        return out

    # -- news ---------------------------------------------------------------------------------------
    def _news(self, conn, m, recv_ts):
        if m.get("type") == "snapshot":
            return self._snapshot(conn, m, recv_ts)
        out = []
        r, seq = _int(m.get("round")), _int(m.get("seq"))
        why = self._season_change(m, r, seq)
        if why:
            out += self._new_season(m, recv_ts, why)
        self._note(m, r)
        if seq is None:
            if self._seen_key(self.misc_keys, json.dumps(m, sort_keys=True, default=str)):
                self.duplicates += 1
                return out
            out.append(Event(m.get("type") or "unknown", m, recv_ts, conn=conn))
            return out
        if seq in self.seen:
            self.duplicates += 1
            self.stats["dup_news"] += 1
            return out
        self.seen.add(seq)
        self.first_copy[conn] += 1
        if seq > self.max_seq:
            self.max_seq = seq
        self._advance()
        out.append(Event(m.get("type"), m, recv_ts, seq, conn=conn))
        return out

    def _snapshot(self, conn, m, recv_ts):
        out = []
        last_seq = _int(m.get("last_seq"))
        last_seq = -1 if last_seq is None else last_seq
        r = _int(m.get("round"))
        if last_seq == -1 and (self.max_seq >= 0 or self.wm >= 0):
            why = "snapshot last_seq -1"
        else:
            why = self._season_change(m, r, None)
        if why:
            out += self._new_season(m, recv_ts, why)
        self._note(m, r)
        self.stats["snapshots_in"] += 1
        self.held.append((recv_ts + self.snapshot_hold_s, recv_ts, conn, m, last_seq))
        return out

    def _season_change(self, m, r, seq):
        if (seq is not None and self.max_seq >= 50 and seq < self.max_seq - 50
                and (seq < 5 or (m.get("type") == "storm_formed" and r == 1))):
            return "seq restart"
        if r == 1 and self.cur_round is not None and self.cur_round >= 2:
            return f"round 1 after round {self.cur_round}"
        md = _market_day(m)
        if md is not None and self.days.get(md[0], md[1]) != md[1]:
            return f"round {md[0]} market date {self.days[md[0]]} -> {md[1]}"
        return None

    def _new_season(self, m, recv_ts, why):
        self._clear_season()
        self.last_reset_reason = why
        self.stats["season_resets"] += 1
        return [Event("season_reset", m, recv_ts)]

    def _note(self, m, r):
        if r is not None:
            self.cur_round = r if self.cur_round is None else max(self.cur_round, r)
        md = _market_day(m)
        if md is not None:
            self.days.setdefault(md[0], md[1])
        t = m.get("type")
        if t == "snapshot":
            self.phase = m.get("phase") or self.phase
        elif t in ("storm_formed", "storm", "track", "advisory", "impact"):
            self.phase = "storm"
        elif t in ("storm_dissipated", "damage_report"):
            self.phase = "aftermath"

    def _advance(self):
        while self.wm + 1 in self.seen:
            self.wm += 1

    def _seen_key(self, od, key):
        if key in od:
            return True
        od[key] = None
        if len(od) > self.max_ids:
            od.popitem(last=False)
        return False


class RobustFeed:
    """The merged multi-connection market-data feed: N WebSocket connections -> FeedMerger -> one Event stream.

    Each connection subscribes once, reconnects after 0.25 s then 0.5-2 s, and is torn down by its phase watchdog
    (3 / 8 / 8 / 35 s without a frame). The merger forwards the first copy of each news seq, holds snapshots 1 s,
    keeps the newest book / quote per symbol and deduplicates trades (module docstring).

    Constructor (keywords recommended):
      url             wss URL, e.g. "wss://cornelltradingcompetition.org/ws/v1/market-data"
      headers         dict sent on connect, e.g. {"X-API-Key": key, "User-Agent": ua}
      symbols         symbols to subscribe (the ONE subscription message names them all)
      channels        channels to subscribe, e.g. ["news", "quotes", "trades", "orderbook"]
      queue           asyncio.Queue that receives every Event (default: a new one, self.queue)
      recorder        Recorder for the raw frames, each line tagged with "conn" (default: none)
      adapter_factory zero-argument callable returning a WSAdapter, or a list with one per connection
                      (default: WebsocketsLibAdapter(SendGuard(5), user_agent) for every connection)
      n_conn          number of connections when urls is not given (default 2)
      urls            one URL per connection (overrides url and n_conn)
      on_event        optional callable(event), called synchronously for every Event, in order, before it is
                      queued; exceptions it raises are logged and swallowed
      phase_fn        optional callable returning "pre_storm" / "storm" / "aftermath" / "closed" from the bot's
                      clock; it sets the watchdog and the outage alarm (default: the merger's estimate from
                      snapshots and news)
      merger          a FeedMerger to use (default: a new one, self.merger)
      user_agent      User-Agent for the default adapter
      connect_timeout, poll_s, rng, log   connect timeout (s), merger poll period (s), jitter RNG, log function

    Receiving events, either:
      async for ev in feed.events():    # starts the connections; ends after feed.stop() once the queue is drained
          ...
    or on_event=callback, or run `await feed.run()` yourself and read feed.queue. Each Event has
    ev.category ("news", "snapshot", "orderbook", "quote", "trade", "status"), ev.kind (the fine type),
    ev.msg (the raw dict), ev.received_at (= ev.recv_ts), ev.seq (news only) and ev.conn; see Event.
    Status events: "season_reset" (start a new engine SeasonState; the merger has already reset itself),
    "ws_connected" / "ws_disconnected" (per connection), "feed_outage" / "feed_restored" (a storm gap over 10 s
    that no connection filled; S1-r3 then uses the wide epsilon). For the engine, pass the msg of every news and
    snapshot event to SeasonState.on_message.
    Control and state: stop(), await force_reconnect(k=None), .connected, .up, .connects, .watchdog_trips,
    .merger.outages, .merger.gaps, .merger.stats.
    """

    def __init__(self, url, headers, symbols, channels, queue=None, recorder=None, adapter_factory=None, n_conn=2,
                 urls=None, merger=None, phase_fn=None, connect_timeout=6.0, poll_s=0.1, rng=None, log=print,
                 on_event=None, user_agent="ctc26-team-bot/0.1 (python-stdlib)"):
        self.urls = list(urls) if urls else [url] * n_conn
        self.n = len(self.urls)
        f = adapter_factory
        if f is None:
            guard = SendGuard(5)
            f = lambda: WebsocketsLibAdapter(guard, user_agent)   # noqa: E731
        self.factories = list(f) if isinstance(f, (list, tuple)) else [f] * self.n
        if len(self.factories) != self.n:
            raise ValueError("need one adapter factory per connection")
        self.headers = headers
        self.queue = queue if queue is not None else asyncio.Queue()
        self.recorder = recorder if recorder is not None else Recorder(None)
        self.on_event = on_event
        self.subscribe_msg = {"action": "subscribe", "symbols": list(symbols), "channels": list(channels)}
        self.merger = merger if merger is not None else FeedMerger()
        self.phase_fn, self.connect_timeout, self.poll_s = phase_fn, connect_timeout, poll_s
        self.rng = rng or random.Random()
        self.log = log
        self.up = [False] * self.n
        self.connects = [0] * self.n
        self.watchdog_trips = [0] * self.n
        self.delays = [[] for _ in range(self.n)]     # reconnect delays used, per connection (diagnostics)
        self.last_mono = [0.0] * self.n
        self._ws = [None] * self.n
        self._stop = False

    @property
    def connected(self):
        return any(self.up)

    def phase(self):
        ph = None
        if self.phase_fn is not None:
            try:
                ph = self.phase_fn()
            except Exception:
                ph = None
        return ph or self.merger.phase

    def stop(self):
        self._stop = True

    async def force_reconnect(self, k=None):
        for i in (range(self.n) if k is None else [k]):
            if self._ws[i] is not None:
                await self._ws[i].close()

    def _emit(self, events):
        for e in events:
            if self.on_event is not None:
                try:
                    self.on_event(e)
                except Exception as exc:     # a consumer bug must not stop the feed
                    self.log(f"feed: on_event error {exc!r}")
            self.queue.put_nowait(e)

    async def run(self):
        """Run the connections and the merger poll until stop(); events go to on_event and self.queue."""
        await asyncio.gather(*(self._conn_loop(k) for k in range(self.n)), self._poll_loop())

    async def events(self):
        """Async iterator over the merged Events: starts run(), yields from self.queue, and ends once stop() has
        been called and the queue is drained. Leaving the loop early (break / cancel) stops the feed."""
        task = asyncio.ensure_future(self.run())
        try:
            while True:
                if not self.queue.empty():
                    yield self.queue.get_nowait()
                    continue
                if task.done():
                    task.result()            # re-raise a crash of the feed itself
                    return
                get = asyncio.ensure_future(self.queue.get())
                await asyncio.wait({get, task}, return_when=asyncio.FIRST_COMPLETED)
                if get.done():
                    yield get.result()
                else:
                    get.cancel()
        finally:
            self.stop()
            if not task.done():
                try:
                    await asyncio.wait_for(task, 2.0)
                except BaseException:
                    task.cancel()

    async def _poll_loop(self):
        while not self._stop:
            self._emit(self.merger.poll(time.time(), self.phase()))
            await asyncio.sleep(self.poll_s)

    async def _conn_loop(self, k):
        attempt = 0
        while not self._stop:
            ws = self._ws[k] = self.factories[k]()
            reader = None
            t_up = None
            try:
                await asyncio.wait_for(ws.connect(self.urls[k], self.headers), self.connect_timeout)
                await ws.send(json.dumps(self.subscribe_msg))          # the ONLY app frame on this connection
                t_up = self.last_mono[k] = time.monotonic()
                self.up[k], self.connects[k] = True, self.connects[k] + 1
                self._emit([Event("ws_connected", {"conn": k, "n": self.connects[k]}, time.time(), conn=k)])
                while not self._stop:
                    if reader is None:
                        reader = asyncio.ensure_future(ws.recv())
                    done, _ = await asyncio.wait({reader}, timeout=0.25)
                    if not done:
                        limit = watchdog_s(self.phase())
                        if time.monotonic() - self.last_mono[k] > limit:
                            self.watchdog_trips[k] += 1
                            self.log(f"feed[{k}]: no frame for {limit:.0f} s ({self.phase()}), reconnecting")
                            break
                        continue
                    raw, reader = reader.result(), None
                    if raw is None:
                        break
                    recv_ts = time.time()
                    self.last_mono[k] = time.monotonic()
                    try:
                        frame = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(frame, dict):
                        continue
                    self.recorder.write(dict(frame, conn=k), recv_ts)
                    self._emit(self.merger.ingest(k, frame, recv_ts))
            except Exception as exc:      # CancelledError is BaseException: shutdown still works
                self.log(f"feed[{k}]: disconnected ({exc!r})")
            finally:
                if reader is not None and not reader.done():
                    reader.cancel()
                was_up, self.up[k] = self.up[k], False
                await ws.close()
                if was_up:
                    self._emit([Event("ws_disconnected", {"conn": k}, time.time(), conn=k)])
            if self._stop:
                break
            if t_up is not None and time.monotonic() - t_up >= RECONNECT_RESET_UP_S:
                attempt = 0
            delay = reconnect_delay(attempt, self.rng)
            attempt += 1
            self.delays[k].append(delay)
            await asyncio.sleep(delay)
