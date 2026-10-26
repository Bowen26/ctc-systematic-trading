"""REST client, live gate and request executor (DESIGN.md section 4.A ``rest.py``).

Safety model:

- Order actions (place, cancel, cancel-all, ETF create/redeem) reach the network only while the
  :class:`RestClient` holds a :class:`LiveToken` for the host of ``exchange.rest_base``. A token can only be
  created with the module-private ``_ISSUE`` sentinel, which only the cli's confirmed-phrase path uses.
  Without a token every order action is answered locally as a dry run (``dry_run=True, sent=False``) and
  logged as ``dry_order`` with the body that would have been sent.
- ``NETWORK_ENABLED`` (False by default) guards the default HTTPS transport; only ``cli.main`` sets it, and
  only in host mode. An injected ``transport`` bypasses it, which is how tests run.
- The bot key is sent only over https, and only to the host of ``exchange.rest_base``. It never appears in an
  :class:`ApiResult`, a log line or an exception text: every error text and response body passes through
  :meth:`RestClient.redact` first.
- Order actions are never retried once a request byte may have been written; the one exception is the kill's
  idempotent CANCEL_ALL at KILL priority. A failure before any byte was written (connect, TLS handshake) is
  retried once on a fresh connection and then reported with ``sent=False``. GETs retry ``get_retries`` times.

The default transport keeps one keep-alive ``http.client.HTTPSConnection`` per worker thread and recycles a
connection that has been idle for more than ``exchange.idle_recycle_s`` or whose socket reads as closed, so the
first order after a quiet spell does not pay a fresh TLS handshake (100-300 ms) or fail on a dead socket.
"""
from __future__ import annotations

import collections
import heapq
import http.client
import itertools
import json
import math
import re
import select
import socket
import ssl
import threading
import time
import traceback
import urllib.parse
from typing import Any, Callable, Mapping, Sequence

from ctcbot.config import Config
from ctcbot.ratelimit import RequestLimiter
from ctcbot.timesource import RealTime
from ctcbot.types import (ORDER_ACTIONS, ApiKind, ApiRequest, ApiResult, BookLevel, EventLog, ExchangeAPI,
                          OpenOrderRow, OrderAck, PositionRow, Priority, ServerTime, Side, TimeSource)

NETWORK_ENABLED = False

_ISSUE: object = object()   # module-private: only the cli's confirmed-phrase path passes it to LiveToken

Transport = Callable[[str, str, dict, "bytes | None", float], "tuple[int, bytes]"]

_PURGEABLE = frozenset({ApiKind.PLACE, ApiKind.ETF_CREATE, ApiKind.ETF_REDEEM})
_GET_PATHS = {ApiKind.GET_OPEN: "/orders/open", ApiKind.GET_POSITIONS: "/positions",
              ApiKind.GET_TIME: "/competitions/time"}
_TICKER = re.compile(r"^[A-Z][A-Z0-9._-]{0,15}$")
_HEADER_SAFE = re.compile(r"[\x21-\x7e]+")


class NetworkDisabled(RuntimeError):
    """The default transport was used while ``NETWORK_ENABLED`` is False."""

    def __init__(self) -> None:
        super().__init__("network disabled")


class NotSentError(ConnectionError):
    """The request provably never left: the failure happened before any byte was written (connect, TLS).

    Transports raise it so the client may retry an order action on a fresh connection and report
    ``sent=False``. Any other exception from a transport means the request may have reached the exchange.
    """


# ----------------------------------------------------------------------------------------------- live token
class LiveToken:
    """Permission to send order actions. Created only by cli after the typed phrase matched (§4.D)."""

    __slots__ = ("host", "cfg_hash")
    host: str
    cfg_hash: str

    def __init__(self, host: str, cfg_hash: str, issuer: object) -> None:
        if issuer is not _ISSUE:
            raise TypeError("LiveToken can only be issued by the live gate")
        if not isinstance(host, str) or not host.strip():
            raise ValueError("LiveToken needs a host")
        if not isinstance(cfg_hash, str) or not cfg_hash:
            raise ValueError("LiveToken needs the config hash")
        object.__setattr__(self, "host", host.strip().lower())
        object.__setattr__(self, "cfg_hash", cfg_hash)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("LiveToken is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("LiveToken is immutable")

    def __reduce_ex__(self, protocol: object) -> Any:
        raise TypeError("LiveToken cannot be copied or pickled")

    def __repr__(self) -> str:
        return f"LiveToken(host={self.host!r}, cfg_hash={self.cfg_hash[:6]!r})"


# ----------------------------------------------------------------------------------------------- helpers
def redact_text(text: str, secrets: Sequence[str]) -> str:
    """Replace every non-empty secret in ``text`` with ``***``."""
    if not isinstance(text, str):
        text = str(text)
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def round_price(px: float, side: Side, decimals: int) -> float:
    """Round a limit price to ``decimals``: buys down, sells up (never through our own limit)."""
    scale = 10 ** int(decimals)
    q = float(px) * scale
    r = round(q)
    if abs(q - r) < 1e-6:
        q = float(r)
    q = math.floor(q) if side is Side.BUY else math.ceil(q)
    return round(q / scale, int(decimals))


def socket_dead(sock: Any) -> bool:
    """True if an idle keep-alive socket is unusable: closed fd, EOF from the peer or unsolicited bytes.

    An idle HTTP/1.1 connection must have nothing to read; readable means the peer closed it (a plain
    non-blocking ``MSG_PEEK`` returns EOF) or sent something unexpected such as a TLS close_notify. Either way
    the connection is recycled before it is used.
    """
    try:
        fd = sock.fileno()
    except Exception:  # noqa: BLE001 - a broken socket object is dead
        return True
    if fd is None or fd < 0:
        return True
    try:
        if hasattr(select, "poll"):
            p = select.poll()
            p.register(fd, select.POLLIN | getattr(select, "POLLHUP", 0) | getattr(select, "POLLERR", 0))
            readable = bool(p.poll(0))
        else:  # pragma: no cover - Windows
            readable = bool(select.select([fd], [], [], 0)[0])
    except (OSError, ValueError):
        return True
    if not readable:
        return False
    if isinstance(sock, ssl.SSLSocket):
        return True                      # raw bytes on an idle TLS socket: close_notify or EOF
    try:
        timeout = sock.gettimeout()
        sock.setblocking(False)
        try:
            sock.recv(1, socket.MSG_PEEK)    # b"" is EOF; any byte is unsolicited data: both are unusable
        finally:
            sock.settimeout(timeout)
    except (BlockingIOError, InterruptedError):
        return False
    except OSError:
        return True
    return True


class HttpsTransport:
    """Default transport: one keep-alive HTTPS connection per calling thread.

    ``conn_factory(host, port, timeout)`` replaces ``http.client.HTTPSConnection`` in tests; with an injected
    factory the ``NETWORK_ENABLED`` gate does not apply. The connection must offer ``connect()``,
    ``request(method, path, body=, headers=)``, ``getresponse()`` (``.status``, ``.read()``), ``close()``,
    ``.sock`` and ``.timeout`` like ``http.client.HTTPConnection``.
    """

    def __init__(self, idle_recycle_s: float = 10.0,
                 conn_factory: Callable[[str, int, float], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.idle_recycle_s = float(idle_recycle_s)
        self._factory = conn_factory
        self._clock = clock
        self._local = threading.local()
        self._lock = threading.Lock()
        self._ssl_ctx: ssl.SSLContext | None = None     # one shared context: loading the CA store costs ms
        self.opened = 0
        self.recycled: collections.Counter = collections.Counter()

    @property
    def uses_network(self) -> bool:
        """True when this transport would open real sockets (no injected factory)."""
        return self._factory is None

    def _new(self, host: str, port: int, timeout: float) -> Any:
        if self._factory is None:
            if not NETWORK_ENABLED:
                raise NetworkDisabled()
            with self._lock:
                if self._ssl_ctx is None:
                    self._ssl_ctx = ssl.create_default_context()
                ctx = self._ssl_ctx
            conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
        else:
            conn = self._factory(host, port, timeout)
        with self._lock:
            self.opened += 1
        return conn

    def reset(self) -> None:
        """Drop this thread's connection; the next request opens a fresh one."""
        st = self._local
        conn = getattr(st, "conn", None)
        st.conn = None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _conn(self, host: str, port: int, timeout: float) -> Any:
        st = self._local
        conn = getattr(st, "conn", None)
        now = self._clock()
        if conn is not None:
            why = None
            if getattr(st, "key", None) != (host, port):
                why = "host"
            elif now - getattr(st, "last", now) > self.idle_recycle_s:
                why = "idle"
            elif getattr(conn, "sock", None) is not None and socket_dead(conn.sock):
                why = "closed"
            if why:
                with self._lock:
                    self.recycled[why] += 1
                self.reset()
                conn = None
        if conn is None:
            conn = self._new(host, port, timeout)
            st.conn, st.key, st.last = conn, (host, port), now
        if getattr(conn, "sock", None) is None:
            try:
                conn.timeout = timeout
                conn.connect()
            except NetworkDisabled:
                raise
            except Exception as e:  # noqa: BLE001 - nothing was written: connect or TLS failed
                self.reset()
                raise NotSentError(f"connect failed: {type(e).__name__}: {e}") from None
        conn.timeout = timeout
        sock = getattr(conn, "sock", None)
        if sock is not None:
            try:
                sock.settimeout(timeout)
            except Exception:  # noqa: BLE001
                pass
        return conn

    def __call__(self, method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> tuple[int, bytes]:
        u = urllib.parse.urlsplit(url)
        if u.scheme != "https" or not u.hostname:
            raise ValueError("refusing a non-https URL")
        host, port = u.hostname, u.port or 443
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
        conn = self._conn(host, port, timeout)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            status = int(resp.status)
        except BaseException:
            self.reset()
            raise
        self._local.last = self._clock()
        if getattr(resp, "will_close", False):
            self.reset()
        return status, data


# ----------------------------------------------------------------------------------------------- client
class _Invalid(ValueError):
    """A request that cannot be expressed (bad fields); answered locally, never sent."""


class RestClient:
    """``ExchangeAPI`` over HTTPS with the live gate, the host allow-list and tolerant response parsers.

    Thread-safe: the REST workers call :meth:`execute` concurrently.
    """

    def __init__(self, cfg: Config, key: str | None, timesrc: TimeSource, log: EventLog,
                 live: LiveToken | None = None,
                 transport: Callable[[str, str, dict, bytes | None, float], tuple[int, bytes]] | None = None) -> None:
        if key is not None and (not isinstance(key, str) or not _HEADER_SAFE.fullmatch(key)):
            raise ValueError("bot key has an invalid format")
        self.cfg = cfg
        self.timesrc = timesrc
        self.log = log
        self.__key = key
        self._secrets: tuple[str, ...] = (key,) if key else ()
        self._base: str | None = cfg.get("exchange.rest_base", None)
        self._case_url: str | None = cfg.get("exchange.case_url", None)
        self._rest_host: str | None = None
        if self._base is not None:
            u = urllib.parse.urlsplit(self._base)
            if u.scheme != "https" or not u.hostname:
                raise ValueError("exchange.rest_base must be an https URL")
            self._rest_host = u.hostname.lower()
            self._base = self._base.rstrip("/")
        self._ua = str(cfg.get("exchange.user_agent"))
        self._t_order = float(cfg.get("exchange.timeout_order_s", 2.0))
        self._t_get = float(cfg.get("exchange.timeout_get_s", 3.0))
        self._get_retries = max(0, int(cfg.get("exchange.get_retries", 1)))
        self._decimals = int(cfg.get("exchange.price_decimals", 2))
        self._allow_market = bool(cfg.get("exchange.allow_market_orders", False))
        self._reqlim = RequestLimiter(int(cfg.get("rate.requests_per_s_cap", 50)))
        if transport is None:
            transport = HttpsTransport(idle_recycle_s=float(cfg.get("exchange.idle_recycle_s", 10.0)))
        self._transport = transport
        self._default_transport = isinstance(transport, HttpsTransport) and transport.uses_network
        self._token = live
        self._live = self._check_token(live)
        self._dry_n = 0
        self._lock = threading.Lock()
        self.counts: collections.Counter = collections.Counter()

    # -------------------------------------------------------------------------- gate
    def _check_token(self, tok: LiveToken | None) -> bool:
        if tok is None:
            return False
        if not isinstance(tok, LiveToken) or self._rest_host is None:
            self.log.alert("live_token_rejected", "live token is not valid for this client; staying in dry-run")
            return False
        tok_host = tok.host.split(":")[0] if tok.host.count(":") == 1 else tok.host
        if tok_host != self._rest_host:
            self.log.alert("live_token_rejected", "live token host differs from exchange.rest_base; dry-run",
                           token_host=tok.host, rest_host=self._rest_host)
            return False
        if len(tok.cfg_hash) < 6 or not self.cfg.hash.startswith(tok.cfg_hash):
            self.log.alert("live_token_rejected", "live token was issued for another config; dry-run",
                           token_cfg=tok.cfg_hash[:12], cfg_hash=self.cfg.hash)
            return False
        self.log.event("live_enabled", host=self._rest_host, cfg_hash=self.cfg.hash)
        return True

    @property
    def live(self) -> bool:
        """True while a token for the rest_base host (and this config) is held: order actions are sent."""
        return self._live

    def redact(self, text: Any) -> str:
        """``text`` with the bot key replaced by ``***``."""
        return redact_text(text if isinstance(text, str) else str(text), self._secrets)

    # -------------------------------------------------------------------------- request building
    def _build(self, req: ApiRequest) -> tuple[str, str | None, str, dict | None]:
        """(method, url or None if the base is unknown, path for logs, body dict)."""
        k = req.kind
        base = self._base
        if k is ApiKind.PLACE:
            body = self._place_body(req)
            path = "/orders"
            return "POST", (base + path) if base else None, path, body
        if k is ApiKind.CANCEL:
            if not isinstance(req.order_id, str) or not req.order_id:
                raise _Invalid("CANCEL needs an order_id")
            path = "/orders/" + urllib.parse.quote(req.order_id, safe="")
            return "DELETE", (base + path) if base else None, path, None
        if k is ApiKind.CANCEL_ALL:
            path = "/orders/all"
            if req.symbol is not None:
                if not isinstance(req.symbol, str) or not req.symbol:
                    raise _Invalid("CANCEL_ALL symbol must be a string")
                path += "?symbol=" + urllib.parse.quote(req.symbol, safe="")
            return "DELETE", (base + path) if base else None, path, None
        if k in (ApiKind.ETF_CREATE, ApiKind.ETF_REDEEM):
            sym = self._symbol(req)
            qty = self._qty(req)
            path = f"/etf/{urllib.parse.quote(sym, safe='')}/" + ("create" if k is ApiKind.ETF_CREATE else "redeem")
            return "POST", (base + path) if base else None, path, {"quantity": qty}
        if k in _GET_PATHS:
            path = _GET_PATHS[k]
            return "GET", (base + path) if base else None, path, None
        if k is ApiKind.GET_BOOK:
            path = "/orderbook/" + urllib.parse.quote(self._symbol(req), safe="")
            return "GET", (base + path) if base else None, path, None
        if k is ApiKind.GET_CASE:
            if not self._case_url:
                raise _Invalid("exchange.case_url is not set")
            return "GET", self._case_url, self._case_url, None
        raise _Invalid(f"unknown request kind {k!r}")

    @staticmethod
    def _symbol(req: ApiRequest) -> str:
        if not isinstance(req.symbol, str) or not req.symbol:
            raise _Invalid(f"{req.kind.value} needs a symbol")
        return req.symbol

    @staticmethod
    def _qty(req: ApiRequest) -> int:
        q = req.qty
        if isinstance(q, bool) or not isinstance(q, int) or q < 1:
            raise _Invalid(f"{req.kind.value} quantity must be an int >= 1, got {q!r}")
        return q

    def _place_body(self, req: ApiRequest) -> dict:
        sym = self._symbol(req)
        side = req.side
        if isinstance(side, str) and not isinstance(side, Side):
            try:
                side = Side(side.lower())
            except ValueError:
                raise _Invalid(f"bad side {req.side!r}") from None
        if not isinstance(side, Side):
            raise _Invalid("PLACE needs a side")
        qty = self._qty(req)
        if req.order_type == "market":
            if not self._allow_market:
                raise _Invalid("market orders are disabled (exchange.allow_market_orders is false)")
            return {"symbol": sym, "side": side.value, "order_type": "market", "quantity": qty}
        if req.order_type != "limit":
            raise _Invalid(f"order_type must be 'limit', got {req.order_type!r}")
        px = req.price
        if isinstance(px, bool) or not isinstance(px, (int, float)) or not math.isfinite(px) or px <= 0:
            raise _Invalid(f"PLACE needs a finite positive price, got {px!r}")
        rpx = round_price(float(px), side, self._decimals)
        if rpx <= 0:
            raise _Invalid(f"price {px!r} rounds to {rpx}")
        return {"symbol": sym, "side": side.value, "order_type": "limit", "quantity": qty, "price": rpx}

    def _headers(self, url: str, has_body: bool) -> dict:
        u = urllib.parse.urlsplit(url)
        h = {"User-Agent": self._ua, "Accept": "application/json", "Connection": "keep-alive"}
        if has_body:
            h["Content-Type"] = "application/json"
        key = self.__key
        if key and u.scheme == "https" and u.hostname and self._rest_host and u.hostname.lower() == self._rest_host:
            h["X-API-Key"] = key
        return h

    # -------------------------------------------------------------------------- results
    def _result(self, req: ApiRequest, status: int | None, data: Any, parsed: Any, error: str | None,
                sent_mono: float, sent: bool, dry: bool = False) -> ApiResult:
        return ApiResult(req=req, status=status, data=data, parsed=parsed,
                         error=None if error is None else self.redact(error),
                         sent_mono=sent_mono, done_mono=self.timesrc.mono(), dry_run=dry, sent=sent)

    def _dry(self, req: ApiRequest, method: str, path: str, body: dict | None) -> ApiResult:
        with self._lock:
            self._dry_n += 1
            n = self._dry_n
        oid = f"dry-{n}"
        now = self.timesrc.mono()
        self.counts["dry"] += 1
        self.log.event("dry_order", api=req.kind.value, method=method, path=path, body=body, req_id=req.req_id,
                       local_id=req.local_id, priority=int(req.priority))
        return ApiResult(req=req, status=200, data={"order_id": oid, "status": "dry_run"},
                         parsed=OrderAck(oid, "dry_run", 0, req.qty, None), error=None,
                         sent_mono=now, done_mono=now, dry_run=True, sent=False)

    def _parse(self, req: ApiRequest, data: Any) -> Any:
        k = req.kind
        if k is ApiKind.PLACE:
            return parse_order_ack(data, self.log)
        if k is ApiKind.GET_OPEN:
            return parse_open_orders(data, self.log)
        if k is ApiKind.GET_POSITIONS:
            return parse_positions(data, self.log)
        if k is ApiKind.GET_TIME:
            return parse_time(data, self.log)
        if k is ApiKind.GET_BOOK:
            return parse_book(data, self.log)
        return data if isinstance(data, dict) else None

    def _decode(self, raw: bytes) -> tuple[Any, str]:
        try:
            text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        except Exception:  # noqa: BLE001
            text = ""
        text = self.redact(text)
        if not text.strip():
            return None, text
        try:
            return json.loads(text), text
        except ValueError:
            return text[:2000], text

    # -------------------------------------------------------------------------- execute
    def execute(self, req: ApiRequest) -> ApiResult:
        """Run one request (blocking, thread-safe). Order actions without a valid token are answered as dry runs.

        Raises ValueError for a non-https URL and RuntimeError("network disabled") when the default transport
        is used while ``NETWORK_ENABLED`` is False; every other failure is reported in the result.
        """
        t0 = self.timesrc.mono()
        is_action = req.kind in ORDER_ACTIONS
        try:
            method, url, path, body = self._build(req)
        except _Invalid as e:
            self.counts["invalid"] += 1
            self.log.event("rest_invalid", api=getattr(req.kind, "value", str(req.kind)), req_id=req.req_id,
                           error=self.redact(str(e)))
            return self._result(req, None, None, None, f"invalid request: {e}", t0, sent=False)
        if is_action and not self._live:
            return self._dry(req, method, path, body)
        if url is None:
            return self._result(req, None, None, None, "exchange.rest_base is not set", t0, sent=False)
        u = urllib.parse.urlsplit(url)
        if u.scheme != "https" or not u.hostname:
            raise ValueError("refusing a non-https URL")
        if self._default_transport and not NETWORK_ENABLED:
            raise NetworkDisabled()
        headers = self._headers(url, body is not None)
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8") if body is not None else None
        timeout = self._t_order if is_action else self._t_get
        max_attempts = 2 if is_action else 1 + self._get_retries
        reset = getattr(self._transport, "reset", None)
        ever_written = False
        sent_mono = t0
        err: str | None = None
        for attempt in range(1, max_attempts + 1):
            self._reqlim.acquire()
            if attempt == 1:
                sent_mono = self.timesrc.mono()
            written = True
            try:
                status, raw = self._transport(method, url, headers, payload, timeout)
            except NetworkDisabled:
                raise
            except NotSentError as e:
                written, err = False, f"not sent: {e}"
            except ValueError:
                raise
            except Exception as e:  # noqa: BLE001 - timeouts, resets, protocol errors: maybe sent
                err = f"transport error: {type(e).__name__}: {e}"
            else:
                data, text = self._decode(raw)
                ok = 200 <= status < 300
                self.counts[f"http_{status // 100}xx"] += 1
                parsed = self._parse(req, data) if ok else None
                error = None if ok else f"http {status}: {text[:300]}"
                return self._result(req, status, data, parsed, error, sent_mono, sent=True)
            ever_written = ever_written or written
            self.counts["transport_error"] += 1
            if is_action:
                retry = attempt == 1 and (not written or (req.kind is ApiKind.CANCEL_ALL
                                                          and int(req.priority) == int(Priority.KILL)))
            else:
                retry = attempt < max_attempts
            self.log.event("rest_transport_error", api=req.kind.value, req_id=req.req_id, attempt=attempt,
                           written=written, retry=retry, error=self.redact(err))
            if not retry:
                break
            if callable(reset):
                try:
                    reset()
                except Exception:  # noqa: BLE001
                    pass
        return self._result(req, None, None, None, err, sent_mono, sent=ever_written)

    def prewarm(self) -> None:
        """GET /competitions/time on this thread's connection (reopened if idle or stale). Never raises."""
        if self._base is None:
            return
        try:
            res = self.execute(ApiRequest(req_id=0, kind=ApiKind.GET_TIME, priority=Priority.KILL,
                                          created_mono=self.timesrc.mono()))
            self.log.event("rest_prewarm", status=res.status, error=res.error,
                           ms=round((res.done_mono - res.sent_mono) * 1000.0, 1))
        except Exception as e:  # noqa: BLE001
            self.log.event("rest_prewarm", status=None, error=self.redact(f"{type(e).__name__}: {e}"))

    def __repr__(self) -> str:
        return f"RestClient(base={self._base!r}, live={self._live})"


# ----------------------------------------------------------------------------------------------- executor
class RestExecutor:
    """N REST worker threads over a priority queue plus dedicated kill workers.

    ``submit`` sends KILL requests to the kill queue and everything else to a priority queue ordered by
    ``(priority, req_id)``. ``freeze`` purges queued PLACE / ETF requests and answers later ones at once with
    ``sent=False``; cancels and GETs are unaffected. Every request gets exactly one ``on_result`` call, from a
    worker thread (or from the caller for requests answered locally).

    Hardening (DESIGN.md §13): a PLACE / ETF request that waited in the queue longer than ``max_queue_age_s``
    (all workers busy on a slow exchange) is answered ``sent=False, error="stale_queue"`` instead of being sent:
    it was priced on an old book, and its ticket (refunded by the order manager) was granted when it was decided,
    so sending it late could put more than the cap into one exchange window. Kill workers never prewarm, so a
    kill is never stuck behind a slow ``GET /competitions/time``.
    """

    def __init__(self, api: ExchangeAPI, workers: int, on_result: Callable[[ApiResult], None], log: EventLog,
                 kill_workers: int = 1, timesrc: TimeSource | None = None,
                 max_queue_age_s: float | None = 0.5) -> None:
        self._api = api
        self._on_result = on_result
        self.log = log
        self._ts: TimeSource = timesrc or getattr(api, "timesrc", None) or RealTime()
        self._redact: Callable[[Any], str] = getattr(api, "redact", None) or (lambda s: str(s))
        self._cv = threading.Condition()
        self._heap: list[tuple[int, int, int, ApiRequest, float]] = []
        self._max_age = None if max_queue_age_s is None else max(0.0, float(max_queue_age_s))
        self._kill: collections.deque[ApiRequest] = collections.deque()
        self._seq = itertools.count()
        self._frozen = False
        self._closed = False
        self._gen = 0
        self.inflight = 0
        self.counts: collections.Counter = collections.Counter()
        self._n_kill = max(0, int(kill_workers))
        self._threads: list[threading.Thread] = []
        for i in range(max(1, int(workers))):
            self._threads.append(threading.Thread(target=self._worker, args=(False,), name=f"rest-{i}", daemon=True))
        for i in range(self._n_kill):
            self._threads.append(threading.Thread(target=self._worker, args=(True,), name=f"rest-kill-{i}",
                                                  daemon=True))
        for t in self._threads:
            t.start()

    # -------------------------------------------------------------------------- answers
    def _answer(self, req: ApiRequest, error: str) -> None:
        now = self._ts.mono()
        self.counts[error] += 1
        self._deliver(ApiResult(req=req, status=None, data=None, parsed=None, error=error, sent_mono=now,
                                done_mono=now, dry_run=False, sent=False))

    def _deliver(self, res: ApiResult) -> None:
        try:
            self._on_result(res)
        except Exception as e:  # noqa: BLE001 - a consumer bug must not kill a worker
            self.log.event("exception", where="rest_executor.on_result",
                           error=self._redact(f"{type(e).__name__}: {e}"),
                           tb=self._redact(traceback.format_exc()))

    # -------------------------------------------------------------------------- queue
    def submit(self, req: ApiRequest) -> None:
        """Queue one request; KILL priority goes to the kill workers."""
        answer: str | None = None
        with self._cv:
            if self._closed:
                answer = "closed"
            elif int(req.priority) == int(Priority.KILL):
                self._kill.append(req)
                self._cv.notify_all()
            elif self._frozen and req.kind in _PURGEABLE:
                answer = "frozen"
            else:
                heapq.heappush(self._heap, (int(req.priority), int(req.req_id), next(self._seq), req,
                                            self._ts.mono()))
                self._cv.notify_all()
        if answer is not None:
            self._answer(req, answer)

    def _purge_locked(self) -> list[ApiRequest]:
        keep, out = [], []
        for item in self._heap:
            (out if item[3].kind in _PURGEABLE else keep).append(item)
        if out:
            heapq.heapify(keep)
            self._heap = keep
        out.sort(key=lambda it: (it[0], it[1], it[2]))
        return [it[3] for it in out]

    def purge(self) -> list[ApiRequest]:
        """Drop queued, not-started PLACE / ETF requests (each answered ``sent=False, error="purged"``)."""
        with self._cv:
            out = self._purge_locked()
        for r in out:
            self._answer(r, "purged")
        if out:
            self.log.event("rest_purged", n=len(out))
        return out

    def freeze(self) -> None:
        """Purge queued PLACE / ETF requests and answer later ones at once, unsent, until unfreeze."""
        with self._cv:
            self._frozen = True
            out = self._purge_locked()
        for r in out:
            self._answer(r, "purged")
        self.log.event("rest_frozen", purged=len(out))

    def unfreeze(self) -> None:
        with self._cv:
            self._frozen = False
        self.log.event("rest_unfrozen")

    @property
    def frozen(self) -> bool:
        return self._frozen

    def prewarm_all(self) -> None:
        """One ``api.prewarm()`` on every normal worker thread (before its next request). Kill workers skip it: a
        kill must never wait for a prewarm GET (they reopen an idle connection on use instead)."""
        with self._cv:
            self._gen += 1
            self._cv.notify_all()

    def close(self, timeout: float = 2.0) -> None:
        """Stop the workers (in-flight requests finish) and answer queued requests with ``error="closed"``."""
        with self._cv:
            self._closed = True
            left = [it[3] for it in sorted(self._heap)] + list(self._kill)
            self._heap, self._kill = [], collections.deque()
            self._cv.notify_all()
        for r in left:
            self._answer(r, "closed")
        deadline = time.monotonic() + max(0.0, float(timeout))
        for t in self._threads:
            if t is threading.current_thread():
                continue
            t.join(max(0.0, deadline - time.monotonic()))

    def stats(self) -> dict:
        with self._cv:
            return {"queued": len(self._heap), "kill_queued": len(self._kill), "inflight": self.inflight,
                    "frozen": self._frozen, "closed": self._closed, "workers": len(self._threads),
                    "counts": dict(self.counts)}

    # -------------------------------------------------------------------------- workers
    def _worker(self, kill: bool) -> None:
        seen_gen = self._gen
        while True:
            req: ApiRequest | None = None
            prewarm = False
            purged = False
            stale = False
            with self._cv:
                while True:
                    if self._closed:
                        return
                    if self._gen != seen_gen:
                        seen_gen = self._gen
                        if kill:
                            continue           # kill workers never prewarm (hardening)
                        prewarm = True
                        break
                    if self._kill and (kill or self._n_kill == 0):
                        req = self._kill.popleft()
                        break
                    if not kill and self._heap:
                        item = heapq.heappop(self._heap)
                        req = item[3]
                        if self._frozen and req.kind in _PURGEABLE:
                            purged = True
                        elif (req.kind in _PURGEABLE and self._max_age is not None
                              and self._ts.mono() - item[4] > self._max_age):
                            stale = True
                        break
                    self._cv.wait(0.5)
                if req is not None and not purged and not stale:
                    self.inflight += 1
            if prewarm:
                fn = getattr(self._api, "prewarm", None)
                if callable(fn):
                    try:
                        fn()
                    except Exception as e:  # noqa: BLE001
                        self.log.event("rest_prewarm", error=self._redact(f"{type(e).__name__}: {e}"))
                self.counts["prewarm"] += 1
                continue
            if req is None:
                continue
            if purged:
                self._answer(req, "purged")
                continue
            if stale:
                self._answer(req, "stale_queue")
                continue
            try:
                res = self._run(req)
            except BaseException as e:  # noqa: BLE001 - never lose a request or a worker thread
                res = ApiResult(req=req, status=None, data=None, parsed=None,
                                error=self._redact(f"executor error: {type(e).__name__}: {e}"),
                                sent_mono=self._ts.mono(), done_mono=self._ts.mono(), dry_run=False,
                                sent=req.kind in ORDER_ACTIONS)
            finally:
                with self._cv:
                    self.inflight -= 1
            self._deliver(res)

    def _run(self, req: ApiRequest) -> ApiResult:
        t0 = self._ts.mono()
        try:
            res = self._api.execute(req)
            if not isinstance(res, ApiResult):
                raise TypeError(f"execute returned {type(res).__name__}")
            return res
        except NetworkDisabled:
            err, sent = "network disabled", False
        except Exception as e:  # noqa: BLE001
            err = self._redact(f"{type(e).__name__}: {e}")
            sent = req.kind in ORDER_ACTIONS        # unknown outcome: let reconcile resolve it
            self.log.event("exception", where="rest_executor.execute", api=req.kind.value, error=err,
                           tb=self._redact(traceback.format_exc()))
        return ApiResult(req=req, status=None, data=None, parsed=None, error=err, sent_mono=t0,
                         done_mono=self._ts.mono(), dry_run=False, sent=sent)


# ----------------------------------------------------------------------------------------------- parsers
def _g(d: Any, *keys: str, default: Any = None) -> Any:
    if not isinstance(d, dict):
        return default
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return default


def _f(x: Any) -> float | None:
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float)):
        v = float(x)
    elif isinstance(x, str):
        try:
            v = float(x.strip())
        except ValueError:
            return None
    else:
        return None
    return v if math.isfinite(v) else None


def _i(x: Any) -> int | None:
    v = _f(x)
    if v is None or v != int(v):
        return None
    return int(v)


def _side(x: Any) -> Side | None:
    if isinstance(x, Side):
        return x
    if isinstance(x, str):
        s = x.strip().lower()
        if s in ("buy", "b", "bid", "long"):
            return Side.BUY
        if s in ("sell", "s", "ask", "short"):
            return Side.SELL
    return None


def _warn(log: EventLog | None, what: str, data: Any) -> None:
    if log is None:
        return
    try:
        s = json.dumps(data, default=str)
    except Exception:  # noqa: BLE001
        s = repr(data)
    try:
        log.event("parse_warn", what=what, body=s[:300])
    except Exception:  # noqa: BLE001
        pass


def _unwrap(data: Any, keys: Sequence[str]) -> Any:
    for _ in range(2):
        if isinstance(data, dict):
            for k in keys:
                if isinstance(data.get(k), (list, dict)):
                    data = data[k]
                    break
            else:
                return data
    return data


def parse_positions(data: Any, log: EventLog | None = None) -> dict[str, PositionRow]:
    """``{symbol: PositionRow}`` from a list of rows, a dict keyed by symbol or a wrapped variant."""
    src = _unwrap(data, ("positions", "data", "items", "results"))
    rows: list[Any]
    if isinstance(src, dict):
        if "symbol" in src or "ticker" in src:
            rows = [src]
        else:
            rows = []
            for k, v in src.items():
                if not (isinstance(k, str) and _TICKER.match(k)):
                    continue
                if isinstance(v, dict):
                    rows.append(dict(v, symbol=v.get("symbol", k)))
                elif _f(v) is not None:
                    rows.append({"symbol": k, "quantity": v})
    elif isinstance(src, list):
        rows = src
    else:
        if data is not None:
            _warn(log, "positions", data)
        return {}
    out: dict[str, PositionRow] = {}
    bad = 0
    for r in rows:
        if not isinstance(r, dict):
            bad += 1
            continue
        sym = _g(r, "symbol", "ticker")
        qty = _i(_g(r, "quantity", "position", "qty", "net", "net_position"))
        if not isinstance(sym, str) or not sym or qty is None:
            bad += 1
            continue
        out[sym] = PositionRow(symbol=sym, qty=qty,
                               avg_price=_f(_g(r, "avg_price", "average_price", "cost_basis", "avg_cost")),
                               mark=_f(_g(r, "mark", "mark_price")),
                               realized=_f(_g(r, "realized_pnl", "realized")),
                               unrealized=_f(_g(r, "unrealized_pnl", "unrealized")))
    if bad or (rows and not out):
        _warn(log, "positions", data)
    return out


def parse_open_orders(data: Any, log: EventLog | None = None) -> list[OpenOrderRow]:
    """``[OpenOrderRow]`` from a list of orders, a wrapped list or a dict keyed by order id."""
    src = _unwrap(data, ("orders", "open_orders", "data", "items", "results"))
    rows: list[Any]
    if isinstance(src, dict):
        if "order_id" in src or "id" in src:
            rows = [src]
        else:
            rows = [dict(v, order_id=v.get("order_id", v.get("id", k))) for k, v in src.items() if isinstance(v, dict)]
    elif isinstance(src, list):
        rows = src
    else:
        if data is not None:
            _warn(log, "open_orders", data)
        return []
    out: list[OpenOrderRow] = []
    bad = 0
    for r in rows:
        if not isinstance(r, dict):
            bad += 1
            continue
        oid = _g(r, "order_id", "id")
        sym = _g(r, "symbol", "ticker")
        side = _side(_g(r, "side"))
        qty = _i(_g(r, "quantity", "qty", "original_quantity", "size"))
        rem = _i(_g(r, "remaining_quantity", "remaining", "leaves_quantity", "open_quantity"))
        if rem is None and qty is not None:
            rem = qty - (_i(_g(r, "filled_quantity", "filled")) or 0)
        if oid is None or not isinstance(sym, str) or side is None or rem is None:
            bad += 1
            continue
        status = _g(r, "status")
        out.append(OpenOrderRow(order_id=str(oid), symbol=sym, side=side, price=_f(_g(r, "price", "limit_price")),
                                qty=qty, remaining=rem, status=None if status is None else str(status)))
    if bad:
        _warn(log, "open_orders", data)
    return out


def parse_order_ack(data: Any, log: EventLog | None = None) -> OrderAck:
    """``OrderAck`` from a PLACE response; a body without an id gives ``order_id=None``."""
    d = data
    if isinstance(d, dict) and "order_id" not in d and "id" not in d and isinstance(d.get("order"), dict):
        d = d["order"]
    if not isinstance(d, dict):
        _warn(log, "order_ack", data)
        return OrderAck(None, None, None, None, None)
    oid = _g(d, "order_id", "id")
    status = _g(d, "status")
    ack = OrderAck(order_id=None if oid is None or str(oid) == "" else str(oid),
                   status=None if status is None else str(status),
                   filled_qty=_i(_g(d, "filled_quantity", "filled", "filled_qty")),
                   remaining=_i(_g(d, "remaining_quantity", "remaining", "leaves_quantity")),
                   avg_fill_price=_f(_g(d, "avg_fill_price", "average_price", "fill_price", "avg_price")))
    if ack.order_id is None:
        _warn(log, "order_ack_no_id", data)
    return ack


def parse_time(data: Any, log: EventLog | None = None) -> ServerTime:
    """``ServerTime`` from ``{competition_id, state, fictional_time, elapsed_active_seconds}``."""
    d = _unwrap(data, ("data", "time"))
    if not isinstance(d, dict):
        _warn(log, "time", data)
        return ServerTime(None, None, None, None)
    comp = _g(d, "competition_id", "comp_id", "competition")
    state = _g(d, "state", "status")
    ft = _g(d, "fictional_time", "market_time")
    st = ServerTime(comp_id=None if comp is None else str(comp), state=None if state is None else str(state),
                    fictional_time=ft if isinstance(ft, str) else None,
                    elapsed_active_s=_f(_g(d, "elapsed_active_seconds", "elapsed_active_s")))
    if st.comp_id is None and st.state is None and st.fictional_time is None:
        _warn(log, "time", data)
    return st


def _levels(x: Any) -> tuple[BookLevel, ...]:
    out = []
    if not isinstance(x, list):
        return ()
    for lv in x:
        if isinstance(lv, dict):
            px, q = _f(_g(lv, "price", "px")), _i(_g(lv, "quantity", "qty", "size"))
        elif isinstance(lv, (list, tuple)) and len(lv) >= 2:
            px, q = _f(lv[0]), _i(lv[1])
        else:
            continue
        if px is None or q is None:
            continue
        out.append(BookLevel(px, q))
    return tuple(out)


def parse_book(data: Any, log: EventLog | None = None) -> tuple[tuple[BookLevel, ...], tuple[BookLevel, ...]]:
    """``(bids, asks)`` from ``{"bids": [{price, quantity}], "asks": [...]}`` (pairs accepted too)."""
    d = _unwrap(data, ("orderbook", "book", "data"))
    if not isinstance(d, dict):
        _warn(log, "book", data)
        return (), ()
    return _levels(d.get("bids")), _levels(d.get("asks"))
