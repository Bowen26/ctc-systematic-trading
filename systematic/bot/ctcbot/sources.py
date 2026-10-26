"""Market-data sources: WebSocket connections and recording replay (DESIGN.md section 4.A ``sources.py``).

:class:`WsSingleSource` is one market-data connection. It connects to ``exchange.ws_url`` (wss only, and only
to that host), sends exactly one frame per connection (the subscription, immediately after connecting, so the
packet's 10-s and 10-frames-per-second rules cannot be broken), then emits every received frame as a
:class:`SourceFrame`. A per-connection watchdog closes a connection that has been silent for ``watchdog_s()``
seconds (the core supplies the value per phase) and the source reconnects after 0.25 s, then every
0.5 +- 0.1 s, capped at 2 s; never the 1-2-4-8-16 s ladder that cost 31.7 s on the testnet.

:func:`build_sources` makes one source per ``feed.connections`` entry; all of them feed the one normalizer.

:class:`RecordingSource` replays a ``record_feed.py`` recording (one JSON frame per line plus ``received_at``),
paced on a :class:`TimeSource`, and turns each ``subscription_ack`` after the first into a down/up pair.

``websockets`` is imported lazily and only by the real connect path, which is gated by ``NETWORK_ENABLED``.
The bot key is never logged: logs carry the URL and the connection id only.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import math
import random
import time
import urllib.parse
from typing import Any, Awaitable, Callable, Iterator, Sequence

from ctcbot.config import Config
from ctcbot.types import EventLog, SourceFrame, SourceStats, TimeSource

NETWORK_ENABLED = False

_WATCHDOG_TICK_S = 0.5
_CLOSE_TIMEOUT_S = 1.0


def _redact(text: str, key: str | None) -> str:
    if key:
        text = text.replace(key, "***")
    return text


def _safe_emit(emit: Callable[[SourceFrame], None], sf: SourceFrame, log: EventLog, where: str) -> None:
    """Call the consumer; a consumer bug is logged and never tears the connection down."""
    try:
        emit(sf)
    except Exception as e:  # noqa: BLE001
        try:
            log.event("exception", where=where, error=f"{type(e).__name__}: {e}"[:300])
        except Exception:  # noqa: BLE001
            pass


def _handshake_status(e: BaseException) -> int | None:
    """The HTTP status of a rejected WebSocket handshake (websockets ``InvalidStatus``), else None."""
    for obj in (getattr(e, "response", None), e):
        v = getattr(obj, "status_code", None) if obj is not None else None
        if isinstance(v, int) and not isinstance(v, bool):
            return v
        v = getattr(obj, "status", None) if obj is not None else None
        if isinstance(v, int) and not isinstance(v, bool) and 100 <= v <= 599:
            return v
    return None


def _check_ws_url(url: Any, cfg_ws_url: Any) -> str:
    """Return ``url`` if it is wss:// and its host equals the host of ``exchange.ws_url``; else ValueError."""
    if not isinstance(cfg_ws_url, str) or not cfg_ws_url:
        raise ValueError("exchange.ws_url is not set")
    if not isinstance(url, str):
        raise ValueError("market-data URL must be a string")
    u, ref = urllib.parse.urlsplit(url), urllib.parse.urlsplit(cfg_ws_url)
    if u.scheme != "wss" or not u.hostname:
        raise ValueError("market-data URL must be wss://")
    if ref.scheme != "wss" or not ref.hostname or u.hostname.lower() != ref.hostname.lower():
        raise ValueError("market-data URL host differs from exchange.ws_url")
    return url


async def _real_connect(url: str, **kwargs: Any) -> Any:
    if not NETWORK_ENABLED:
        raise RuntimeError("network disabled")
    from websockets.asyncio.client import connect  # lazy: the rest of the bot is stdlib-only
    return await connect(url, **kwargs)


class WsSingleSource:
    """One market-data WebSocket connection (a :class:`ctcbot.types.MarketDataSource`)."""

    def __init__(self, cfg: Config, key: str, symbols: Sequence[str], timesrc: TimeSource, log: EventLog,
                 watchdog_s: Callable[[], float], name: str = "ws0", local_addr: tuple[str, int] | None = None,
                 connect: Callable[..., Any] | None = None, *, url: str | None = None,
                 sleep: Callable[[float], Awaitable[Any]] | None = None, rng: random.Random | None = None) -> None:
        self.name = name
        self.url = _check_ws_url(url if url is not None else cfg.get("exchange.ws_url", None),
                                 cfg.get("exchange.ws_url", None))
        if not isinstance(key, str) or not key:
            raise ValueError("market-data connection needs the bot key")
        self.cfg = cfg
        self.__key = key
        self.symbols = list(symbols)
        self.timesrc = timesrc
        self.log = log
        self.watchdog_s = watchdog_s
        self.local_addr = tuple(local_addr) if local_addr else None
        self._connect = connect
        self._sleep = sleep or asyncio.sleep
        self._rng = rng or random.Random()
        self.channels = list(cfg.get("feed.channels"))
        self.ua = str(cfg.get("exchange.user_agent"))
        self.open_timeout = float(cfg.get("feed.ws_open_timeout_s", 5.0))
        self.ping_interval = float(cfg.get("feed.ws_ping_interval_s", 20.0))
        self.ping_timeout = float(cfg.get("feed.ws_ping_timeout_s", 20.0))
        self.max_size = int(cfg.get("feed.ws_max_size", 8388608))
        self.first_s = float(cfg.get("feed.reconnect_first_s", 0.25))
        self.every_s = float(cfg.get("feed.reconnect_every_s", 0.5))
        self.jitter_s = float(cfg.get("feed.reconnect_jitter_s", 0.1))
        self.max_s = float(cfg.get("feed.reconnect_max_s", 2.0))
        # a rejected handshake is not a dropped connection: 401/403 retry slowly, 429 backs off (hardening)
        self.auth_s = float(cfg.get("feed.reconnect_auth_s", 5.0))
        self.throttle_max_s = float(cfg.get("feed.reconnect_throttle_max_s", 10.0))
        self._reject_status: int | None = None
        self._rejects = 0
        self.rejected: dict[int, int] = {}
        self.epoch = 0
        self.up = False
        self.frames = 0
        self.reconnects = 0
        self.errors = 0
        self.watchdog_trips = 0
        self.sends: list[str] = []          # frames sent on the current connection (for diagnostics and tests)
        self.sent_total = 0
        self.delays: list[float] = []
        self.last_frame_mono: float | None = None
        self._ws: Any = None
        self._stop = False
        self._attempt = 0

    # -------------------------------------------------------------------------- control
    def stop(self) -> None:
        """Stop after the current receive; the open connection is closed."""
        self._stop = True
        ws = self._ws
        if ws is not None:
            try:
                asyncio.get_running_loop().create_task(self._close(ws))
            except RuntimeError:
                pass

    def stats(self) -> SourceStats:
        return SourceStats(name=self.name, connections_up=1 if self.up else 0, frames=self.frames,
                           reconnects=self.reconnects, last_frame_mono=self.last_frame_mono, errors=self.errors)

    def reconnect_delay(self) -> float:
        """The delay before the next attempt: 0.25 s first, then 0.5 +- 0.1 s, never above the cap."""
        if self._attempt <= 0:
            d = self.first_s
        else:
            d = self.every_s + self._rng.uniform(-self.jitter_s, self.jitter_s)
        return max(0.0, min(self.max_s, d))

    # -------------------------------------------------------------------------- run
    def _kwargs(self) -> dict:
        kw: dict[str, Any] = dict(additional_headers={"X-API-Key": self.__key}, user_agent_header=self.ua,
                                  open_timeout=self.open_timeout, ping_interval=self.ping_interval,
                                  ping_timeout=self.ping_timeout, max_size=self.max_size, max_queue=None, proxy=None)
        if self.local_addr is not None:
            kw["local_addr"] = self.local_addr
        return kw

    async def _close(self, ws: Any) -> None:
        try:
            await asyncio.wait_for(ws.close(), _CLOSE_TIMEOUT_S)
        except BaseException:  # noqa: BLE001 - a dead peer never answers the close handshake
            tr = getattr(ws, "transport", None)
            if tr is not None:
                try:
                    tr.abort()
                except Exception:  # noqa: BLE001
                    pass

    async def run(self, emit: Callable[[SourceFrame], None]) -> None:
        """Connect, subscribe once, emit frames, reconnect; returns after :meth:`stop`.

        Raises RuntimeError("network disabled") when no connect is injected and ``NETWORK_ENABLED`` is False;
        otherwise only asyncio.CancelledError escapes.
        """
        connect = self._connect
        if connect is None:
            if not NETWORK_ENABLED:
                raise RuntimeError("network disabled")
            connect = _real_connect
        while not self._stop:
            got_frame = await self._session(connect, emit)
            if self._stop:
                break
            if got_frame:
                self._attempt = 0
            delay = self.reconnect_delay()
            st, self._reject_status = self._reject_status, None
            if st in (401, 403, 429):
                self._rejects += 1
                self.rejected[st] = self.rejected.get(st, 0) + 1
                if st == 429:
                    delay = max(delay, min(self.throttle_max_s, 0.5 * 2.0 ** self._rejects))
                else:
                    delay = max(delay, self.auth_s)
                if self._rejects == 1 or self._rejects % 12 == 0:
                    try:
                        self.log.alert("ws_rejected", f"market-data handshake rejected with HTTP {st} on {self.name}"
                                       f" ({self._rejects} in a row): retrying every {delay:.1f} s"
                                       + ("; check the bot key" if st in (401, 403) else ""),
                                       conn=self.name, status=st, n=self._rejects)
                    except Exception:  # noqa: BLE001
                        pass
            elif got_frame or st is None:
                self._rejects = 0
            self._attempt += 1
            self.delays.append(delay)
            self.reconnects += 1
            await self._sleep(delay)

    async def _session(self, connect: Callable[..., Any], emit: Callable[[SourceFrame], None]) -> bool:
        """One connection: returns True if at least one frame was received."""
        self.epoch += 1
        conn = f"{self.name}#{self.epoch}"
        ws: Any = None
        reader: asyncio.Future | None = None
        got = False
        self.sends = []
        try:
            res = connect(self.url, **self._kwargs())
            ws = await res if inspect.isawaitable(res) else res
            self._ws = ws
            if self._stop:
                return got
            sub = json.dumps({"action": "subscribe", "symbols": self.symbols, "channels": self.channels})
            await ws.send(sub)
            self.sends.append(sub)
            self.sent_total += 1
            self.up = True
            self.last_frame_mono = self.timesrc.mono()
            last_rx = self.last_frame_mono
            self.log.event("ws_up", conn=conn, url=self.url, local_addr=self.local_addr)
            _safe_emit(emit, SourceFrame(frame={}, recv_wall=time.time(), recv_mono=self.timesrc.mono(), conn=conn,
                                         control="up"), self.log, "ws_emit")
            while not self._stop:
                if reader is None:
                    reader = asyncio.ensure_future(ws.recv())
                done, _ = await asyncio.wait({reader}, timeout=_WATCHDOG_TICK_S)
                if not done:
                    limit = self._watchdog()
                    if self.timesrc.mono() - last_rx > limit:
                        self.watchdog_trips += 1
                        self.log.event("ws_watchdog", conn=conn, silent_s=round(self.timesrc.mono() - last_rx, 3),
                                       limit_s=limit)
                        break
                    continue
                raw, reader = reader.result(), None
                recv_wall, recv_mono = time.time(), self.timesrc.mono()
                last_rx = recv_mono
                self.last_frame_mono = recv_mono
                frame = self._decode(raw, conn)
                if frame is None:
                    continue
                got = True
                self.frames += 1
                _safe_emit(emit, SourceFrame(frame=frame, recv_wall=recv_wall, recv_mono=recv_mono, conn=conn),
                           self.log, "ws_emit")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - closed, refused, timed out, protocol errors: reconnect
            self._reject_status = _handshake_status(e) if ws is None else None
            if not self._stop:
                self.errors += 1
                self.log.event("ws_error", conn=conn, url=self.url,
                               error=_redact(f"{type(e).__name__}: {e}", self.__key)[:300])
        finally:
            if reader is not None and not reader.done():
                reader.cancel()
            if ws is not None:
                await self._close(ws)
            self._ws = None
            if self.up:
                self.up = False
                self.log.event("ws_down", conn=conn)
                _safe_emit(emit, SourceFrame(frame={}, recv_wall=time.time(), recv_mono=self.timesrc.mono(),
                                             conn=conn, control="down"), self.log, "ws_emit")
        return got

    def _watchdog(self) -> float:
        try:
            v = float(self.watchdog_s())
            return v if math.isfinite(v) and v > 0 else 35.0
        except Exception:  # noqa: BLE001 - a broken callback must not stop the feed
            return 35.0

    def _decode(self, raw: Any, conn: str) -> dict | None:
        try:
            if isinstance(raw, (bytes, bytearray, memoryview)):
                raw = bytes(raw).decode("utf-8")
            frame = json.loads(raw)
        except (ValueError, UnicodeDecodeError, TypeError):
            self.errors += 1
            self.log.event("ws_bad_frame", conn=conn, size=len(raw) if hasattr(raw, "__len__") else None)
            return None
        if not isinstance(frame, dict):
            self.errors += 1
            return None
        return frame

    def __repr__(self) -> str:
        return f"WsSingleSource(name={self.name!r}, url={self.url!r}, epoch={self.epoch})"


def build_sources(cfg: Config, key: str, symbols: Sequence[str], timesrc: TimeSource, log: EventLog,
                  watchdog_s: Callable[[], float], connect: Callable[..., Any] | None = None) -> list[WsSingleSource]:
    """One :class:`WsSingleSource` per ``feed.connections`` entry, each with its own name and ``local_addr``."""
    out = []
    for entry in cfg.get("feed.connections"):
        la = entry.get("local_addr")
        out.append(WsSingleSource(cfg, key, symbols, timesrc, log, watchdog_s, name=entry["name"],
                                  local_addr=(str(la[0]), int(la[1])) if la else None, connect=connect))
    return out


# ----------------------------------------------------------------------------------------------- recordings
def iter_recording(path: str) -> Iterator[tuple[float, dict]]:
    """``(received_at, frame without received_at)`` per line, in file order; unusable lines are skipped."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                fr = json.loads(line)
            except ValueError:
                continue
            if not isinstance(fr, dict):
                continue
            ra = fr.pop("received_at", None)
            if isinstance(ra, bool) or not isinstance(ra, (int, float)) or not math.isfinite(ra):
                continue
            yield float(ra), fr


class RecordingSource:
    """Replays a recording as one connection (a :class:`ctcbot.types.MarketDataSource`).

    Emits an initial ``"up"``, then each frame whose ``received_at`` is in ``[t_from, t_to)`` with ``recv_mono =
    timesrc.mono()`` and ``recv_wall = received_at``. Each ``subscription_ack`` after the first emitted frame
    becomes a ``"down"`` then ``"up"`` pair (the connection epoch increments) before the ack itself; a final
    ``"down"`` follows the last frame. With ``paced``, frames are released so that ``received_at - first``
    maps onto ``timesrc`` time (under :class:`ctcbot.timesource.ScaledTime`, ``delta / speed`` real seconds).
    """

    def __init__(self, path: str, timesrc: TimeSource, log: EventLog, name: str = "rec", paced: bool = True,
                 t_from: float | None = None, t_to: float | None = None) -> None:
        self.path = path
        self.name = name
        self.timesrc = timesrc
        self.log = log
        self.paced = paced
        self.t_from = t_from
        self.t_to = t_to
        self.epoch = 0
        self.frames = 0
        self.reconnects = 0
        self.errors = 0
        self.last_frame_mono: float | None = None
        self.up = False
        self.done = False
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def stats(self) -> SourceStats:
        return SourceStats(name=self.name, connections_up=1 if self.up else 0, frames=self.frames,
                           reconnects=self.reconnects, last_frame_mono=self.last_frame_mono, errors=self.errors)

    def _control(self, emit: Callable[[SourceFrame], None], kind: str, recv_wall: float) -> None:
        self.up = kind == "up"
        _safe_emit(emit, SourceFrame(frame={}, recv_wall=recv_wall, recv_mono=self.timesrc.mono(),
                                     conn=f"{self.name}#{self.epoch}", control=kind), self.log, "replay_emit")

    async def run(self, emit: Callable[[SourceFrame], None]) -> None:
        """Emit the recording until its end (or :meth:`stop`), then return."""
        speed = float(getattr(self.timesrc, "speed", 1.0) or 1.0)
        first: float | None = None
        mono0 = 0.0
        n = 0
        try:
            for ra, fr in iter_recording(self.path):
                if self._stop:
                    break
                if self.t_from is not None and ra < self.t_from:
                    continue
                if self.t_to is not None and ra >= self.t_to:
                    break
                if first is None:
                    first, mono0 = ra, self.timesrc.mono()
                    self.epoch = 1
                    self.log.event("replay_start", path=self.path, first_received_at=ra, t_from=self.t_from,
                                   t_to=self.t_to)
                    self._control(emit, "up", ra)
                elif fr.get("type") == "subscription_ack":
                    self._control(emit, "down", ra)
                    self.epoch += 1
                    self.reconnects += 1
                    self._control(emit, "up", ra)
                if self.paced:
                    wait = (mono0 + (ra - first)) - self.timesrc.mono()
                    if wait > 0.0005:
                        await asyncio.sleep(wait / speed)
                elif n % 500 == 499:
                    await asyncio.sleep(0)
                n += 1
                self.frames += 1
                self.last_frame_mono = self.timesrc.mono()
                _safe_emit(emit, SourceFrame(frame=fr, recv_wall=ra, recv_mono=self.last_frame_mono,
                                             conn=f"{self.name}#{self.epoch}"), self.log, "replay_emit")
        except OSError as e:
            self.errors += 1
            self.log.event("replay_error", path=self.path, error=f"{type(e).__name__}: {e}"[:300])
        finally:
            if self.up:
                self._control(emit, "down", time.time() if first is None else (first + (self.timesrc.mono() - mono0)))
            self.done = True
            self.log.event("replay_end", path=self.path, frames=self.frames, reconnects=self.reconnects)
