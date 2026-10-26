"""Structured JSONL logging with secret redaction (DESIGN.md section 4.D ``app/log.py``).

* :class:`JsonlLog` implements the ``EventLog`` protocol. Every line is one JSON object
  ``{"t_wall", "t_mono", "kind", ...fields}``. Secrets are replaced with ``***`` **after** serialisation, so no
  field, nested value or exception text can carry the key into the file.
* :class:`FrameRecorder` writes the raw feed in the ``record_feed.py`` format (``{**frame, "received_at"}``).
* :func:`install_redaction` routes uncaught exceptions (main thread, worker threads, the asyncio loop) through
  the same redaction and sets the ``websockets`` loggers to WARNING, because they log handshake headers
  (including ``X-API-Key``) at DEBUG.

Nothing here ever raises into the bot: a failed write is counted and reported once on the echo stream.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import math
import os
import sys
import threading
import time
import traceback
from collections import Counter
from typing import Any, Callable, Iterable, Sequence

from ctcbot.types import SourceFrame, TimeSource

REDACTED = "***"
WEBSOCKETS_LOGGERS: tuple[str, ...] = ("websockets", "websockets.client", "websockets.server",
                                       "websockets.protocol", "websockets.asyncio", "websockets.asyncio.client",
                                       "websockets.asyncio.connection")
_MIN_SECRET_LEN = 4          # never "redact" tiny strings: replacing "a" would destroy every line


# ----------------------------------------------------------------------------- redaction
class Redactor:
    """Replaces every registered secret (and its JSON-escaped spelling) with ``***``. Thread-safe."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._lock = threading.Lock()
        self._needles: tuple[str, ...] = ()
        for s in secrets:
            self.add(s)

    def add(self, secret: str | None) -> None:
        """Register one more secret. Empty or very short strings are ignored."""
        if not isinstance(secret, str):
            return
        variants = {secret, secret.strip()}
        try:
            variants.add(json.dumps(secret)[1:-1])
            variants.add(json.dumps(secret, ensure_ascii=False)[1:-1])
        except (TypeError, ValueError):
            pass
        with self._lock:
            cur = set(self._needles)
            cur.update(v for v in variants if len(v) >= _MIN_SECRET_LEN)
            # longest first, so a secret that contains another is replaced whole
            self._needles = tuple(sorted(cur, key=len, reverse=True))

    @property
    def active(self) -> bool:
        return bool(self._needles)

    def __call__(self, text: str) -> str:
        if not isinstance(text, str):
            text = str(text)
        for n in self._needles:
            if n in text:
                text = text.replace(n, REDACTED)
        return text


def redact_text(text: str, secrets: Iterable[str]) -> str:
    """One-shot redaction of ``text`` against ``secrets``."""
    return Redactor(secrets)(text)


# ----------------------------------------------------------------------------- serialisation
def _default(o: Any) -> Any:
    """``json.dumps`` fallback: sets as sorted lists, dataclasses as dicts, bytes as text, else ``str``."""
    if isinstance(o, (set, frozenset)):
        try:
            return sorted(o, key=str)
        except Exception:  # noqa: BLE001 - mixed types: keep any order
            return list(o)
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        try:
            return {f.name: getattr(o, f.name) for f in dataclasses.fields(o)}
        except Exception:  # noqa: BLE001
            return str(o)
    if isinstance(o, (bytes, bytearray)):
        return bytes(o).decode("utf-8", "replace")
    return str(o)


def _finite(o: Any) -> Any:
    """Recursively replace non-finite floats with strings so that every line is strict JSON."""
    if isinstance(o, float):
        return o if math.isfinite(o) else str(o)
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


def dumps_line(obj: Any, compact: bool = True) -> str:
    """Serialise ``obj`` to one strict-JSON line (``default`` as above, NaN and inf as strings)."""
    seps = (",", ":") if compact else None
    try:
        return json.dumps(obj, default=_default, allow_nan=False, separators=seps)
    except ValueError:
        return json.dumps(_finite(json.loads(json.dumps(obj, default=_default, separators=seps))),
                          default=_default, allow_nan=False, separators=seps)


# ----------------------------------------------------------------------------- the event log
class JsonlLog:
    """``EventLog`` writing one redacted JSON object per line to ``path`` (``None``: no file, echo only).

    ``echo`` receives every alert (prefixed with a terminal bell) and the first write error. ``timesrc``
    supplies ``t_mono``/``t_wall`` (default: the real clocks). Thread-safe: the engine worker, the laws
    worker and the REST workers log through the same instance.
    """

    _RESERVED = ("t_wall", "t_mono", "kind")

    def __init__(self, path: str | None, secrets: Sequence[str] = (), echo: Callable[[str], None] | None = None,
                 *, timesrc: TimeSource | None = None, bell: bool = True) -> None:
        self.path = path
        self._redactor = Redactor(secrets)
        self._echo = echo
        self._timesrc = timesrc
        self._bell = bell
        self._lock = threading.Lock()
        self._fh = None
        self.write_errors = 0
        self.counts: Counter[str] = Counter()
        self.alert_counts: Counter[str] = Counter()
        if path:
            d = os.path.dirname(os.path.abspath(path))
            os.makedirs(d, exist_ok=True)
            self._fh = open(path, "a", encoding="utf-8", buffering=1)

    # -- EventLog
    def event(self, kind: str, **fields: Any) -> None:
        self._write(kind, fields)

    def alert(self, kind: str, msg: str, **fields: Any) -> None:
        f = dict(fields)
        f["alert"] = kind
        f["msg"] = msg
        with self._lock:
            self.alert_counts[kind] += 1
        self._write("alert", f)
        if self._echo is not None:
            text = self.redact(f"{chr(7) if self._bell else ''}[ALERT] {kind}: {msg}")
            try:
                self._echo(text)
            except Exception:  # noqa: BLE001 - a broken terminal must not stop the bot
                pass

    # -- helpers
    def redact(self, text: str) -> str:
        """Apply the secret replacement used for every line."""
        return self._redactor(text)

    def add_secret(self, secret: str) -> None:
        """Register a secret learned after construction."""
        self._redactor.add(secret)

    @property
    def redactor(self) -> Redactor:
        return self._redactor

    def install_redaction(self, secrets: Sequence[str] | None = None, loop: Any = None) -> "RedactionHooks":
        """Install the redacting exception hooks, logging exceptions to this log (see :func:`install_redaction`)."""
        for s in secrets or ():
            self.add_secret(s)
        return install_redaction((), loop, log=self, redactor=self._redactor)

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None

    # -- internals
    def _now(self) -> tuple[float, float]:
        ts = self._timesrc
        if ts is not None:
            try:
                return float(ts.wall()), float(ts.mono())
            except Exception:  # noqa: BLE001
                pass
        return time.time(), time.monotonic()

    def _write(self, kind: str, fields: dict) -> None:
        t_wall, t_mono = self._now()
        rec: dict[str, Any] = {"t_wall": round(t_wall, 6), "t_mono": round(t_mono, 6), "kind": str(kind)}
        for k, v in fields.items():
            rec[("f_" + k) if k in self._RESERVED else k] = v
        try:
            line = dumps_line(rec)
        except Exception as e:  # noqa: BLE001 - never let logging raise
            line = dumps_line({"t_wall": rec["t_wall"], "t_mono": rec["t_mono"], "kind": "log_error",
                               "of": str(kind), "error": f"{type(e).__name__}: {e}"})
        line = self._redactor(line)
        with self._lock:
            self.counts[str(kind)] += 1
            if self._fh is None:
                return
            try:
                self._fh.write(line + "\n")
            except Exception as e:  # noqa: BLE001
                self.write_errors += 1
                if self.write_errors == 1 and self._echo is not None:
                    try:
                        self._echo(self._redactor(f"[ALERT] log_write_failed: {type(e).__name__}: {e}"))
                    except Exception:  # noqa: BLE001
                        pass


# ----------------------------------------------------------------------------- raw feed recorder
class FrameRecorder:
    """Writes every exchange frame as ``{**frame, "received_at": recv_wall}``, one redacted line each.

    The format is ``record_feed.py``'s, so ``check_recording.py`` and :class:`RecordingSource` read the bot's own
    feed. Control frames (connection up/down) are not exchange frames and are skipped.
    """

    def __init__(self, path: str, secrets: Sequence[str] = ()) -> None:
        self.path = path
        self._redactor = Redactor(secrets)
        self._lock = threading.Lock()
        self.frames = 0
        self.write_errors = 0
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._fh = open(path, "a", encoding="utf-8", buffering=1 << 16)

    def record(self, sf: SourceFrame) -> None:
        """Record one :class:`SourceFrame` (ignored when it is a control frame)."""
        if sf.control is not None or not isinstance(sf.frame, dict):
            return
        self.write(sf.frame, sf.recv_wall)

    def write(self, frame: dict, recv_wall: float) -> None:
        rec = {k: v for k, v in frame.items() if k != "received_at"}
        rec["received_at"] = recv_wall
        try:
            line = self._redactor(dumps_line(rec, compact=False))
        except Exception:  # noqa: BLE001
            self.write_errors += 1
            return
        with self._lock:
            if self._fh is None:
                return
            try:
                self._fh.write(line + "\n")
                self.frames += 1
            except Exception:  # noqa: BLE001
                self.write_errors += 1

    def flush(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.flush()
                except OSError:
                    pass

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None


# ----------------------------------------------------------------------------- exception hooks
class _RedactFilter(logging.Filter):
    """Redacts the formatted message of every record that passes through a ``websockets`` logger."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            msg = str(record.msg)
        record.msg = self._redactor(msg)
        record.args = None
        if record.exc_info:
            record.exc_text = self._redactor("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return True


@dataclasses.dataclass
class RedactionHooks:
    """What :func:`install_redaction` replaced, so tests (and a clean shutdown) can put it back."""
    prev_sys: Any
    prev_thread: Any
    loop: Any = None
    prev_loop_handler: Any = None
    filters: list = dataclasses.field(default_factory=list)

    def uninstall(self) -> None:
        sys.excepthook = self.prev_sys
        threading.excepthook = self.prev_thread
        if self.loop is not None:
            try:
                self.loop.set_exception_handler(self.prev_loop_handler)
            except Exception:  # noqa: BLE001
                pass
        for lg, flt in self.filters:
            try:
                lg.removeFilter(flt)
            except Exception:  # noqa: BLE001
                pass
        self.filters.clear()


def install_redaction(secrets: Sequence[str] = (), loop: Any = None, *, log: Any = None,
                      redactor: Redactor | None = None) -> RedactionHooks:
    """Install redacting handlers for uncaught exceptions and quiet the ``websockets`` loggers.

    ``sys.excepthook``, ``threading.excepthook`` and (with ``loop``) the loop's exception handler format the
    traceback, redact it and log it as ``exception`` on ``log`` (an ``EventLog``), or write it to stderr when no
    log is given. Returns a :class:`RedactionHooks` whose ``uninstall()`` restores the previous handlers.
    """
    red = redactor if redactor is not None else Redactor()
    for s in secrets:
        red.add(s)

    def report(where: str, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            text = red("".join(traceback.format_exception(exc_type, exc, tb)))
            name = getattr(exc_type, "__name__", str(exc_type))
            err = red(f"{name}: {exc}")
        except Exception:  # noqa: BLE001
            text, err = "<unformattable exception>", "<unformattable exception>"
        if log is not None:
            try:
                log.event("exception", where=where, error=err, traceback=text)
                return
            except Exception:  # noqa: BLE001
                pass
        try:
            sys.__stderr__.write(text)
        except Exception:  # noqa: BLE001
            pass

    def sys_hook(exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is not None and issubclass(exc_type, KeyboardInterrupt):
            prev_sys(exc_type, exc, tb)
            return
        report("main", exc_type, exc, tb)

    def thread_hook(args: Any) -> None:
        if args.exc_type is SystemExit:
            return
        th = getattr(args, "thread", None)
        report(f"thread:{getattr(th, 'name', '?')}", args.exc_type, args.exc_value, args.exc_traceback)

    prev_sys = sys.excepthook
    prev_thread = threading.excepthook
    sys.excepthook = sys_hook
    threading.excepthook = thread_hook
    hooks = RedactionHooks(prev_sys=prev_sys, prev_thread=prev_thread)

    if loop is not None:
        def loop_handler(lp: Any, context: dict) -> None:
            exc = context.get("exception")
            if exc is not None:
                report("loop", type(exc), exc, exc.__traceback__)
            else:
                msg = red(str(context.get("message", "loop error")))
                if log is not None:
                    try:
                        log.event("exception", where="loop", error=msg, traceback="")
                        return
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    sys.__stderr__.write(msg + "\n")
                except Exception:  # noqa: BLE001
                    pass

        hooks.loop = loop
        try:
            hooks.prev_loop_handler = loop.get_exception_handler()
        except Exception:  # noqa: BLE001
            hooks.prev_loop_handler = None
        loop.set_exception_handler(loop_handler)

    flt = _RedactFilter(red)
    for name in WEBSOCKETS_LOGGERS:
        lg = logging.getLogger(name)
        lg.setLevel(logging.WARNING)
        lg.addFilter(flt)
        hooks.filters.append((lg, flt))
    return hooks
