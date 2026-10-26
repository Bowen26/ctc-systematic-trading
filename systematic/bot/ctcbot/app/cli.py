"""Command line, modes, the live gate and the startup sequence (DESIGN.md section 4.D ``app/cli.py``).

Safety model (DESIGN.md §0):

* The default is **dry-run**: without a ``LiveToken`` the ``RestClient`` logs order actions and sends nothing.
* ``--live`` needs host mode (an explicit ``--host``, or both ``--rest-base`` and ``--ws-url``), a TTY, a
  live-allowed config, a fetched ``case.json`` (and ``--accept-timing-diff`` for a timing difference), a passing
  engine probe, no KILL file, and the phrase ``LIVE <host> <cfg_hash[:6]>`` typed at the prompt (3 attempts).
  Only that confirmed path creates the ``LiveToken``.
* The key comes from ``--key-file`` or the env var named by ``exchange.key_env``; there is no default path. It is
  stripped and validated and never printed, logged or put in an error text.
* Only host mode sets ``rest.NETWORK_ENABLED`` and ``sources.NETWORK_ENABLED``, and only when no transport or
  WebSocket connect was injected. They are reset to False when :func:`main` returns.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import itertools
import json
import os
import re
import stat
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence, TextIO
from urllib.parse import urlparse

from ctcbot.types import SYMBOLS, ApiKind, ApiRequest, ApiResult, Command, ServerTime

from ctcbot.app.log import JsonlLog
from ctcbot.app.persist import (STATE_FILE, engine_log_name, load_state, new_run_id, read_engine_log,
                                restart_plan)

KEY_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{16,256}$")
HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::[0-9]{1,5})?$")
EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2
_MAX_KEY_FILE = 4096


class CliExit(Exception):
    """Ends :func:`main` with ``code`` after printing ``msg`` to the error stream."""

    def __init__(self, code: int, msg: str = "") -> None:
        super().__init__(msg)
        self.code = code
        self.msg = msg


class _Parser(argparse.ArgumentParser):
    """argparse that raises :class:`CliExit` instead of calling ``sys.exit``."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise CliExit(EXIT_USAGE, f"{self.prog}: error: {message}\n{self.format_usage()}")

    def exit(self, status: int = 0, message: str | None = None) -> None:  # type: ignore[override]
        raise CliExit(status, message or "")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="run_bot.py", description="ctcbot: Systematic Equities (Hurricane Season) bot. "
                                               "Dry-run unless --live is given and confirmed.")
    p.add_argument("--config", help="base config (default config/default.json)")
    p.add_argument("--profile", action="append", default=[], metavar="PATH", help="overlay, applied in order")
    p.add_argument("--set", action="append", default=[], dest="sets", metavar="a.b=VALUE",
                   help="override one config leaf (VALUE parsed as JSON, else a string)")
    p.add_argument("--host", help="exchange host; sets rest_base, ws_url and case_url")
    p.add_argument("--rest-base", help="https://HOST/api/v1")
    p.add_argument("--ws-url", help="wss://HOST/ws/v1/market-data")
    p.add_argument("--case-url", help="https://HOST/cases/hurricane.json")
    p.add_argument("--key-file", metavar="PATH", help="file holding only the bot key (else $CTC_BOT_KEY)")
    p.add_argument("--live", action="store_true", help="send real orders (asks for a typed confirmation)")
    p.add_argument("--accept-timing-diff", action="store_true",
                   help="allow --live when the served case timing differs from the research case")
    p.add_argument("--run-dir", metavar="DIR", help="run directory (default runs/<run_id>)")
    p.add_argument("--fresh", action="store_true", help="ignore a saved state.json in the run dir")
    p.add_argument("--no-trade", action="store_true", help="disable every strategy")
    p.add_argument("--replay", metavar="RECORDING.jsonl", help="replay a recording offline")
    p.add_argument("--replay-speed", type=float, default=1.0, metavar="X")
    p.add_argument("--replay-from", type=float, default=None, metavar="UNIX")
    p.add_argument("--replay-to", type=float, default=None, metavar="UNIX")
    p.add_argument("--sim", metavar="research:NNN|recording:PATH", help="simulate offline in scaled real time")
    p.add_argument("--sim-speed", type=float, default=1.0, metavar="X")
    p.add_argument("--history", metavar="DIR", help="research history dir (sim)")
    p.add_argument("--fv-cache", metavar="DIR", help="engine fair-value caches (sim research)")
    p.add_argument("--self-test", action="store_true", help="offline checks; exit 0 or 1")
    p.add_argument("--print-config", action="store_true", help="print the effective config and exit")
    return p


# ----------------------------------------------------------------------------- small helpers
class _Out:
    """Writes lines to a stream, swallowing broken-pipe errors."""

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream

    def __call__(self, text: str = "") -> None:
        try:
            self.stream.write(text + "\n")
            self.stream.flush()
        except Exception:  # noqa: BLE001
            pass


def _host_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


def _host_sets(args: argparse.Namespace) -> list[str]:
    sets: list[str] = []
    if args.host is not None:
        if not HOST_RE.match(args.host):
            raise CliExit(EXIT_USAGE, "--host must be a bare host name (optionally :port), without a scheme or path")
        sets.append(f"exchange.host={args.host}")
    rest_base = args.rest_base or (f"https://{args.host}/api/v1" if args.host else None)
    ws_url = args.ws_url or (f"wss://{args.host}/ws/v1/market-data" if args.host else None)
    case_url = args.case_url or (f"https://{args.host}/cases/hurricane.json" if args.host else None)
    for k, v in (("rest_base", rest_base), ("ws_url", ws_url), ("case_url", case_url)):
        if v is not None:
            sets.append(f"exchange.{k}={v}")
    return sets


def _load_cfg(args: argparse.Namespace, extra: Sequence[str] = ()) -> Any:
    from ctcbot.config import ConfigError, load_config
    sets = list(args.sets or []) + _host_sets(args) + list(extra)
    try:
        cfg = load_config(args.config, list(args.profile or []), sets)
        if args.no_trade:
            names = sorted(dict(cfg.get("strategies", {}) or {}))
            sets += [f"strategies.{n}.enabled=false" for n in names]
            cfg = load_config(args.config, list(args.profile or []), sets)
    except ConfigError as e:
        raise CliExit(EXIT_USAGE, f"config error: {e}") from None
    except (OSError, ValueError) as e:
        raise CliExit(EXIT_USAGE, f"config error: {type(e).__name__}: {e}") from None
    return cfg


def read_key(args: argparse.Namespace, cfg: Any, environ: Mapping[str, str]) -> tuple[str, bool]:
    """The validated bot key and whether its file is readable by group or others. Raises :class:`CliExit`."""
    group_readable = False
    if args.key_file:
        path = os.path.expanduser(args.key_file)
        try:
            st = os.stat(path)
            with open(path, "rb") as f:
                raw = f.read(_MAX_KEY_FILE + 1)
        except OSError as e:
            raise CliExit(EXIT_USAGE, f"cannot read the key file {path}: {e.strerror or type(e).__name__}") from None
        group_readable = bool(st.st_mode & (stat.S_IRGRP | stat.S_IROTH))
        if len(raw) > _MAX_KEY_FILE:
            raise CliExit(EXIT_USAGE, "bot key has an invalid format")
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            raise CliExit(EXIT_USAGE, "bot key has an invalid format") from None
    else:
        env_name = str(cfg.get("exchange.key_env", "CTC_BOT_KEY"))
        text = environ.get(env_name)
        if not text:
            raise CliExit(EXIT_USAGE, f"no bot key: pass --key-file PATH or set {env_name}")
    key = text.strip()
    if not KEY_RE.match(key):
        raise CliExit(EXIT_USAGE, "bot key has an invalid format")
    return key, group_readable


_STARTUP_IDS = itertools.count(10_000_001)      # startup GETs never collide with the core's request ids


def _req(kind: ApiKind, **kw: Any) -> ApiRequest:
    return ApiRequest(req_id=next(_STARTUP_IDS), kind=kind, created_mono=time.monotonic(), **kw)


def _get(api: Any, kind: ApiKind) -> ApiResult | None:
    try:
        return api.execute(_req(kind))
    except Exception:  # noqa: BLE001 - startup GETs are best effort
        return None


def _server_time(res: ApiResult | None) -> ServerTime | None:
    if res is None or not res.ok:
        return None
    if isinstance(res.parsed, ServerTime):
        return res.parsed
    try:
        from ctcbot.rest import parse_time
        st = parse_time(res.data)
        return st if isinstance(st, ServerTime) else None
    except Exception:  # noqa: BLE001
        return None


def _open_rows(res: ApiResult | None) -> list | None:
    if res is None or not res.ok:
        return None
    if isinstance(res.parsed, list):
        return res.parsed
    try:
        from ctcbot.rest import parse_open_orders
        return list(parse_open_orders(res.data))
    except Exception:  # noqa: BLE001
        return None


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict):
        raise ValueError(f"{path} is not a JSON object")
    return d


def _enabled_strategies(cfg: Any) -> list[str]:
    out = []
    for name, sec in sorted(dict(cfg.get("strategies", {}) or {}).items()):
        if isinstance(sec, Mapping) and sec.get("enabled"):
            out.append(name)
    return out


# ----------------------------------------------------------------------------- startup checks
@dataclass
class Startup:
    """What the startup sequence learned; the input of the live gate and of the core."""
    cfg: Any
    case_raw: dict
    case: Any
    case_diff: Any
    case_fetched: bool
    cost: Any
    probe_ok: bool
    probe_error: str | None
    laws_module: Any
    open_rows: list | None = None
    server_time: ServerTime | None = None
    notes: list[str] = field(default_factory=list)


def probe_engine(cfg: Any, case_raw: dict, log: Any) -> tuple[bool, str | None]:
    """Import the engine and run the snapshot-fix probe (DESIGN.md §4.C). Returns (ok, error text)."""
    sys.dont_write_bytecode = True
    try:
        from ctcbot.models.engine_adapter import load_engine_module, snapshot_fix_present
        edir = cfg.path(cfg.get("engine.dir"))
        mod = load_engine_module(edir)
        params = os.path.join(edir, str(cfg.get("engine.params")))
        ok = bool(snapshot_fix_present(mod, cfg.path(cfg.get("engine.probe_file")), case_raw, params))
        return ok, None if ok else "the engine snapshot-fix probe failed"
    except Exception as e:  # noqa: BLE001
        return False, f"engine probe error: {type(e).__name__}: {e}"


def load_laws(cfg: Any, log: Any) -> Any:
    sys.dont_write_bytecode = True
    try:
        from ctcbot.models.laws import load_laws_module
        return load_laws_module(cfg.path(cfg.get("laws.module")))
    except Exception as e:  # noqa: BLE001
        log.alert("laws_unavailable", f"settlement laws module failed to load: {type(e).__name__}: {e}")
        return None


def startup_checks(args: argparse.Namespace, cfg: Any, log: Any, api: Any | None, say: Callable[[str], None]
                   ) -> Startup:
    """Steps (2)-(6) of the host-mode startup; also used offline (``api=None``: the research case)."""
    from ctcbot.case import CaseInfo, diff_case
    from ctcbot.models.cost_model import CostModel
    research = _read_json(cfg.path(cfg.get("paths.research_case")))
    served = None
    if api is not None:
        res = _get(api, ApiKind.GET_CASE)
        if res is not None and res.ok and isinstance(res.data, dict):
            served = res.data
        else:
            st = None if res is None else res.status
            err = None if res is None else res.error
            log.alert("case_fetch_failed", f"GET case.json failed (status {st}, {err}): continuing in dry-run on "
                                           "the research case")
            say("WARNING: case.json fetch failed; using the research case (dry-run only)")
    case_raw = served if served is not None else research
    case_diff = diff_case(case_raw, research)
    for line in getattr(case_diff, "lines", ()):
        log.event("case_diff", line=line)
    if served is not None and not case_diff.clean:
        log.alert("case_diff", f"served case.json differs from the research case: symbols "
                               f"{sorted(case_diff.symbols)}, timing {case_diff.timing}")
    case = CaseInfo.from_dict(case_raw)
    cost = CostModel(cfg.path(cfg.get("cost.model")), cfg)
    for line in cost.check_against_config() or ():
        log.event("cost_check_warn", line=line)
    probe_ok, probe_err = probe_engine(cfg, case_raw, log)
    if not probe_ok:
        log.alert("engine_snapshot_bug", f"{probe_err}: forwarding no snapshots to the engine; --live refused")
    laws_module = load_laws(cfg, log)
    st = Startup(cfg=cfg, case_raw=case_raw, case=case, case_diff=case_diff, case_fetched=served is not None,
                 cost=cost, probe_ok=probe_ok, probe_error=probe_err, laws_module=laws_module)
    if api is not None:
        st.open_rows = _open_rows(_get(api, ApiKind.GET_OPEN))
        pos = _get(api, ApiKind.GET_POSITIONS)
        log.event("startup_positions", status=None if pos is None else pos.status)
        st.server_time = _server_time(_get(api, ApiKind.GET_TIME))
    return st


# ----------------------------------------------------------------------------- the live gate
def live_refusals(*, isatty: bool, live_allowed: bool, case_fetched: bool, timing_diff: bool,
                  accept_timing_diff: bool, probe_ok: bool, kill_file_present: bool,
                  bound_problems: Sequence[str] = ()) -> list[str]:
    """Every reason ``--live`` must be refused (empty: the gate may ask for the phrase).

    ``bound_problems`` are ``config.live_bound_problems(cfg)``: safety settings loosened beyond the live bounds.
    """
    reasons = [f"config outside the live bounds: {b}" for b in bound_problems]
    if not isatty:
        reasons.append("stdin is not a terminal")
    if not live_allowed:
        reasons.append("a loaded config file says live_allowed: false (sim-only profile)")
    if not case_fetched:
        reasons.append("case.json could not be fetched from the exchange")
    if timing_diff and not accept_timing_diff:
        reasons.append("the served case timing differs from the research case (pass --accept-timing-diff after "
                       "review)")
    if not probe_ok:
        reasons.append("the engine snapshot-fix probe failed")
    if kill_file_present:
        reasons.append("a KILL file exists in the run dir")
    return reasons


def live_prompt_lines(host: str, cfg: Any, profiles: Sequence[str], case_diff: Any, open_rows: list | None,
                      laws_ok: bool) -> list[str]:
    """The text shown before the confirmation phrase is asked for."""
    from ctcbot.config import diff_from_default
    lines = ["", "=" * 72, "LIVE TRADING: real orders will be sent", "=" * 72,
             f"host:          {host}", f"config hash:   {cfg.hash}",
             f"profiles:      {', '.join(profiles) if profiles else '-'}",
             "config differences from default.json:"]
    diffs = list(diff_from_default(cfg))
    lines += [f"  {d}" for d in diffs] or ["  (none)"]
    lines.append("case.json differences:")
    lines += [f"  {d}" for d in getattr(case_diff, "lines", ())] or ["  (none)"]
    lines += [f"enabled strategies: {', '.join(_enabled_strategies(cfg)) or '-'}",
              f"sizing.scale:  {cfg.get('sizing.scale')}",
              f"action cap:    {cfg.get('rate.bot_cap_per_min')} per {cfg.get('rate.window_s')} s"]
    if open_rows:
        lines.append(f"WARNING: {len(open_rows)} open orders already exist on this key (another live instance?)")
    if not laws_ok:
        lines.append("WARNING: the settlement laws module is unavailable: S1-r3 excludes the closure symbols")
    lines.append(f"Type exactly:  LIVE {host} {cfg.hash[:6]}")
    return lines


def ask_phrase(expected: str, stdin: TextIO, say: Callable[[str], None], attempts: int = 3) -> bool:
    """Read up to ``attempts`` lines from ``stdin``; True iff one equals ``expected`` exactly (after strip)."""
    for i in range(attempts):
        say(f"confirmation ({i + 1}/{attempts}): ")
        try:
            line = stdin.readline()
        except Exception:  # noqa: BLE001
            return False
        if not line:
            return False
        if line.strip() == expected:
            return True
        say("phrase does not match")
    return False


def _issue_live_token(host: str, cfg_hash: str) -> Any:
    """The only place a LiveToken is created: after the typed phrase matched."""
    from ctcbot import rest
    return rest.LiveToken(host, cfg_hash, rest._ISSUE)


# ----------------------------------------------------------------------------- self-test
def self_test(cfg: Any, say: Callable[[str], None]) -> int:
    """Offline checks: config, engine probe, cost model, laws module, research case diffs clean."""
    from ctcbot.case import diff_case
    ok_all = True

    def item(name: str, ok: bool, detail: str = "") -> None:
        nonlocal ok_all
        ok_all = ok_all and ok
        say(f"[{'ok' if ok else 'FAIL'}] {name}{(': ' + detail) if detail else ''}")

    item("config", True, f"hash {cfg.hash}, files {', '.join(os.path.basename(s) for s in cfg.sources)}")
    log = _NullLog()
    try:
        research = _read_json(cfg.path(cfg.get("paths.research_case")))
        item("research case", True, cfg.path(cfg.get("paths.research_case")))
    except Exception as e:  # noqa: BLE001
        item("research case", False, f"{type(e).__name__}: {e}")
        return EXIT_FAIL
    ok, err = probe_engine(cfg, research, log)
    item("engine snapshot-fix probe", ok, err or "")
    try:
        from ctcbot.models.cost_model import CostModel
        diffs = list(CostModel(cfg.path(cfg.get("cost.model")), cfg).check_against_config() or ())
        item("cost model", True, f"{len(diffs)} table differences" if diffs else "matches the config tables")
        for d in diffs[:20]:
            say(f"       WARN {d}")
    except Exception as e:  # noqa: BLE001
        item("cost model", False, f"{type(e).__name__}: {e}")
    laws = load_laws(cfg, log)
    item("settlement laws", laws is not None, cfg.path(cfg.get("laws.module")))
    try:
        cd = diff_case(research, research)
        item("diff_case(research, research) clean", bool(cd.clean))
    except Exception as e:  # noqa: BLE001
        item("diff_case", False, f"{type(e).__name__}: {e}")
    return EXIT_OK if ok_all else EXIT_FAIL


class _NullLog:
    """EventLog that discards everything (self-test)."""

    def event(self, kind: str, **fields: Any) -> None:
        pass

    def alert(self, kind: str, msg: str, **fields: Any) -> None:
        pass


# ----------------------------------------------------------------------------- offline exchanges
class ClockedExchange:
    """``ExchangeAPI`` over a SimExchange whose virtual time follows a TimeSource (shell replay and sim).

    Virtual time is ``timesrc.mono() + offset``. When the exchange has a ``clock`` hook (E's SimExchange), the
    exchange reads that time itself under its own lock; otherwise ``t`` is set before each call.
    :class:`AnchoredSource` re-anchors ``offset`` on the first replayed frame, so the exchange's virtual time
    equals the recording's ``received_at - first_received_at`` however long the startup took.
    """

    def __init__(self, exchange: Any, timesrc: Any, offset: float = 0.0) -> None:
        self.exchange = exchange
        self.timesrc = timesrc
        self.offset = float(offset)
        self._lock = threading.Lock()
        self._hooked = False
        if hasattr(exchange, "clock") and getattr(exchange, "clock", None) is None:
            try:
                exchange.clock = self.now
                self._hooked = True
            except AttributeError:
                self._hooked = False

    def now(self) -> float:
        return float(self.timesrc.mono()) + self.offset

    def execute(self, req: ApiRequest) -> ApiResult:
        if self._hooked:
            return self.exchange.execute(req)
        with self._lock:
            t = self.now()
            cur = getattr(self.exchange, "t", None)
            if cur is None or t > cur:
                adv = getattr(self.exchange, "advance", None)
                if callable(adv):
                    adv(t)
                try:
                    self.exchange.t = t
                except AttributeError:
                    pass
            return self.exchange.execute(req)

    def prewarm(self) -> None:
        """Nothing to warm offline."""


class RestartableTime:
    """A ``ScaledTime`` whose origin can be reset once the slow startup (engine library, probe) is done.

    Offline runs build the core, the engine and the exchange on this clock, then call :meth:`restart` right
    before the shell starts, so simulated time does not run ahead during construction.
    """

    def __init__(self, speed: float, start: float = 0.0) -> None:
        from ctcbot.timesource import ScaledTime
        self._cls = ScaledTime
        self.speed = float(speed)
        self.start = float(start)
        self._inner = ScaledTime(self.speed, start=self.start)

    def restart(self) -> None:
        self._inner = self._cls(self.speed, start=self.start)

    def mono(self) -> float:
        return self._inner.mono()

    def wall(self) -> float:
        return self._inner.wall()


class AnchoredSource:
    """Wraps a replay source; on its first exchange frame, aligns :class:`ClockedExchange` to the recording."""

    def __init__(self, inner: Any, api: ClockedExchange, first_received_at: float) -> None:
        self.inner = inner
        self.api = api
        self.first = float(first_received_at)
        self.name = getattr(inner, "name", "rec")
        self.anchored = False

    async def run(self, emit: Callable[[Any], None]) -> None:
        def wrapped(sf: Any) -> None:
            if not self.anchored and getattr(sf, "control", None) is None:
                self.anchored = True
                self.api.offset = (float(sf.recv_wall) - self.first) - float(sf.recv_mono)
            emit(sf)

        await self.inner.run(wrapped)

    def stop(self) -> None:
        self.inner.stop()

    def stats(self) -> Any:
        return self.inner.stats()


def _construct(cls: Any, values: Mapping[str, Any]) -> Any:
    """Build ``cls`` passing the named values its constructor accepts (E's constructors are not in DESIGN.md)."""
    try:
        sig = inspect.signature(cls)
    except (TypeError, ValueError) as e:
        raise CliExit(EXIT_FAIL, f"cannot inspect {getattr(cls, '__name__', cls)}: {e}") from None
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    for name, prm in sig.parameters.items():
        if prm.kind in (prm.VAR_POSITIONAL, prm.VAR_KEYWORD):
            continue
        if name in values:
            if prm.kind == prm.POSITIONAL_ONLY:
                args.append(values[name])
            else:
                kwargs[name] = values[name]
        elif prm.default is prm.empty:
            raise CliExit(EXIT_FAIL, f"{cls.__name__}: the cli has no value for required parameter {name!r}")
    return cls(*args, **kwargs)


def _first_received_at(path: str) -> float:
    from ctcbot.sources import iter_recording
    for ts, _ in iter_recording(path):
        return float(ts)
    raise CliExit(EXIT_USAGE, f"recording {path} has no frames")


# ----------------------------------------------------------------------------- main
def main(argv: Sequence[str] | None = None, *, transport: Any = None, ws_connect: Any = None,
         stdin: TextIO | None = None, isatty: Callable[[], bool] | None = None,
         environ: Mapping[str, str] | None = None, out: TextIO | None = None, err: TextIO | None = None) -> int:
    """Entry point of ``run_bot.py``. Returns the process exit code (0 ok, 1 failure, 2 refused or usage)."""
    out_s = out if out is not None else sys.stdout
    err_s = err if err is not None else sys.stderr
    say, warn = _Out(out_s), _Out(err_s)
    in_s = stdin if stdin is not None else sys.stdin
    env = environ if environ is not None else os.environ
    if isatty is None:
        def isatty() -> bool:
            try:
                return bool(in_s.isatty())
            except Exception:  # noqa: BLE001
                return False
    sys.dont_write_bytecode = True
    try:
        return _main(list(sys.argv[1:] if argv is None else argv), transport, ws_connect, in_s, isatty, env, say,
                     warn)
    except CliExit as e:
        if e.msg:
            warn(e.msg.rstrip("\n"))
        return e.code
    except KeyboardInterrupt:
        warn("interrupted")
        return 130
    finally:
        for name in ("ctcbot.rest", "ctcbot.sources"):
            mod = sys.modules.get(name)
            if mod is not None:
                mod.NETWORK_ENABLED = False


def _main(argv: list[str], transport: Any, ws_connect: Any, stdin: TextIO, isatty: Callable[[], bool],
          environ: Mapping[str, str], say: _Out, warn: _Out) -> int:
    args = build_parser().parse_args(argv)
    modes = [m for m, on in (("--replay", args.replay), ("--sim", args.sim), ("--self-test", args.self_test)) if on]
    if len(modes) > 1:
        raise CliExit(EXIT_USAGE, f"choose one of {', '.join(modes)}")
    if args.live and modes:
        raise CliExit(EXIT_USAGE, f"--live cannot be combined with {modes[0]}")
    cfg = _load_cfg(args)
    if args.print_config:
        from ctcbot.config import diff_from_default
        say(json.dumps(cfg.d, indent=1, sort_keys=True))
        say(f"# hash {cfg.hash}; live_allowed {cfg.live_allowed}")
        for line in diff_from_default(cfg):
            say(f"# {line}")
        return EXIT_OK
    if args.self_test:
        return self_test(cfg, say)

    run_id = new_run_id()
    run_dir = os.path.abspath(args.run_dir) if args.run_dir else os.path.join(
        cfg.path(cfg.get("app.run_root", "runs")), run_id)
    kill_path = os.path.join(run_dir, str(cfg.get("app.kill_file", "KILL")))
    if os.path.exists(kill_path):
        raise CliExit(EXIT_USAGE, f"remove the KILL file first: {kill_path}")
    if args.replay:
        return _run_replay(args, cfg, run_dir, run_id, stdin, isatty, say, warn)
    if args.sim:
        return _run_sim(args, cfg, run_dir, run_id, stdin, isatty, say, warn)
    if not (args.host or (args.rest_base and args.ws_url)):
        raise CliExit(EXIT_USAGE, "host mode needs --host (or both --rest-base and --ws-url); "
                                  "use --replay or --sim to run offline")
    return _run_host(args, cfg, run_dir, run_id, transport, ws_connect, stdin, isatty, environ, say, warn)


def _make_log(run_dir: str, secrets: Sequence[str], warn: _Out, timesrc: Any = None) -> JsonlLog:
    os.makedirs(run_dir, exist_ok=True)
    return JsonlLog(os.path.join(run_dir, "events.jsonl"), secrets, echo=warn, timesrc=timesrc)


def _run_host(args: argparse.Namespace, cfg: Any, run_dir: str, run_id: str, transport: Any, ws_connect: Any,
              stdin: TextIO, isatty: Callable[[], bool], environ: Mapping[str, str], say: _Out, warn: _Out) -> int:
    from ctcbot import rest, sources
    from ctcbot.timesource import RealTime
    # (1) the key
    key, group_readable = read_key(args, cfg, environ)
    log = _make_log(run_dir, [key], warn)
    try:
        if group_readable:
            log.event("key_file_mode", path=args.key_file)
            warn(f"WARNING: key file {args.key_file} is readable by group or others (chmod 600 it)")
        host = _host_of(cfg.get("exchange.rest_base")) or ""
        log.event("cli_start", mode="host", run_id=run_id, run_dir=run_dir, host=host, cfg_hash=cfg.hash,
                  sources=list(cfg.sources), live_requested=bool(args.live), argv_flags=_flags(args))
        kill_path = os.path.join(run_dir, str(cfg.get("app.kill_file", "KILL")))
        if args.live:            # the cheap refusals first, before any request is made
            from ctcbot.config import live_bound_problems
            early = live_refusals(isatty=bool(isatty()), live_allowed=bool(cfg.live_allowed), case_fetched=True,
                                  timing_diff=False, accept_timing_diff=True, probe_ok=True,
                                  kill_file_present=os.path.exists(kill_path),
                                  bound_problems=live_bound_problems(cfg))
            if early:
                log.event("live_refused", reasons=early)
                raise CliExit(EXIT_USAGE, "--live refused: " + "; ".join(early))
        say(f"ctcbot host mode: host {host}, run dir {run_dir}, config {cfg.hash}"
            f"{' (live requested)' if args.live else ' (DRY-RUN)'}")
        if transport is None and ws_connect is None:
            rest.NETWORK_ENABLED = True
            sources.NETWORK_ENABLED = True
        timesrc = RealTime()
        boot_api = rest.RestClient(cfg, key, timesrc, log, live=None, transport=transport)
        # (2)-(6) case, cost model, engine probe, laws, open orders and positions
        st = startup_checks(args, cfg, log, boot_api, warn)
        if not st.probe_ok:
            cfg = _load_cfg(args, ['engine.forward_snapshots="never"'])
        # (7) the restart plan
        saved = None if args.fresh else load_state(os.path.join(run_dir, STATE_FILE))
        plan = restart_plan(saved, st.server_time, time.time(), dry_run_now=not args.live, mode="host")
        log.event("restart_plan", restore=plan.restore, promotion=plan.promotion, reason=plan.reason,
                  engine_log=plan.engine_log)
        if saved is not None:
            say(f"saved state: {plan.reason}")
        # the live gate
        token = None
        if args.live:
            from ctcbot.config import live_bound_problems
            reasons = live_refusals(isatty=bool(isatty()), live_allowed=bool(cfg.live_allowed),
                                    case_fetched=st.case_fetched, timing_diff=bool(st.case_diff.timing),
                                    accept_timing_diff=bool(args.accept_timing_diff), probe_ok=st.probe_ok,
                                    kill_file_present=os.path.exists(kill_path),
                                    bound_problems=live_bound_problems(cfg))
            if reasons:
                log.event("live_refused", reasons=reasons)
                raise CliExit(EXIT_USAGE, "--live refused: " + "; ".join(reasons))
            for line in live_prompt_lines(host, cfg, list(args.profile or []), st.case_diff, st.open_rows,
                                          st.laws_module is not None):
                say(line)
            if not ask_phrase(f"LIVE {host} {cfg.hash[:6]}", stdin, say):
                log.event("live_refused", reasons=["confirmation phrase not typed"])
                raise CliExit(EXIT_USAGE, "--live refused: the confirmation phrase was not typed")
            token = _issue_live_token(host, cfg.hash)
            log.alert("live", f"LIVE trading confirmed on {host} (config {cfg.hash})", host=host, cfg_hash=cfg.hash)
        elif st.open_rows:
            warn(f"note: {len(st.open_rows)} open orders exist on this key")
        # (8) build the core and the shell
        api = rest.RestClient(cfg, key, timesrc, log, live=token, transport=transport)
        return _run_core(args, cfg, run_dir, run_id, st, plan, saved, api, timesrc, log, stdin, isatty, say,
                         mode="host", live=token is not None, secrets=[key], status_stream=warn.stream,
                         sources_fn=lambda core: sources.build_sources(cfg, key, st.case.symbols or SYMBOLS, timesrc,
                                                                       log, core.watchdog_s, connect=ws_connect))
    finally:
        log.close()


def _flags(args: argparse.Namespace) -> dict:
    """The parsed arguments, minus anything that could carry a secret."""
    d = dict(vars(args))
    d.pop("sets", None)
    d["sets_n"] = len(args.sets or [])
    return d


def _run_core(args: argparse.Namespace, cfg: Any, run_dir: str, run_id: str, st: Startup, plan: Any,
              saved: dict | None, api: Any, timesrc: Any, log: JsonlLog, stdin: TextIO, isatty: Callable[[], bool],
              say: _Out, *, mode: str, live: bool, secrets: Sequence[str], sources_fn: Callable[[Any], list],
              engine_override: Any = None, status_stream: TextIO | None = None) -> int:
    """Build the executor, the engine and the core, then run the shell until it stops."""
    from ctcbot.rest import RestExecutor
    from ctcbot.app.core import BotCore
    from ctcbot.app.shell import ResultBridge, run_shell
    fmt = str(cfg.get("engine.persist_file", "engine_state_{run_id}_e{epoch}.jsonl"))
    if plan.restore and plan.engine_log:
        engine_log = os.path.join(run_dir, plan.engine_log)
        if not os.path.exists(engine_log):
            log.alert("engine_log_missing", f"saved engine log {plan.engine_log} not found: the engine starts "
                                            "empty (earlier rounds count as missing)")
    else:
        engine_log = os.path.join(run_dir, engine_log_name(fmt, run_id, 0))
    restore_engine = bool(plan.restore and os.path.exists(engine_log))

    def engine_factory(*, persist_path: str | None = None, restore: bool = False, mode: str = "thread",
                       **_: Any) -> Any:
        from ctcbot.models.engine_adapter import EngineAdapter
        lib = cfg.get("engine.library_seasons", None)
        return EngineAdapter(cfg, st.case_raw, timesrc, log, mode=mode, persist_path=persist_path,
                             adjust_dir=run_dir, run_id=run_id, restore=restore,
                             library_seasons=tuple(lib) if lib else None, snapshot_fix=st.probe_ok)

    engine = engine_override if engine_override is not None else engine_factory(
        persist_path=engine_log, restore=restore_engine, mode=str(cfg.get("engine.mode", "thread")))
    restore = None
    if plan.restore and saved is not None:
        restore = dict(saved)
        restore["engine_messages"] = read_engine_log(engine_log) if restore_engine else []
        restore["engine_log"] = os.path.basename(engine_log)
    factories = {} if engine_override is not None else {"engine": engine_factory}
    core = BotCore(cfg, st.case, st.case_diff, engine, timesrc, log, mode=mode, run_dir=run_dir, run_id=run_id,
                   laws_module=st.laws_module, cost=st.cost, restore=restore, promotion=bool(plan.promotion),
                   live_label=live, factories=factories)
    if args.accept_timing_diff and getattr(st.case_diff, "timing", False):
        core.on_command(Command("caseok", ("timing",), source="cli"), timesrc.mono())
    srcs = sources_fn(core)
    bridge = ResultBridge()
    executor = RestExecutor(api, int(cfg.get("exchange.workers")), bridge, log,
                            kill_workers=int(cfg.get("exchange.kill_workers", 1)),
                            max_queue_age_s=float(cfg.get("exchange.max_queue_age_s", 0.5)))
    say(f"running ({'LIVE' if live else 'DRY-RUN'}); type 'help' for commands, 'k' to kill, 'q' to quit")
    restart = getattr(timesrc, "restart", None)
    if callable(restart):
        restart()                       # offline clocks start now, after the slow construction above
    try:
        return asyncio.run(run_shell(core, srcs, executor, api, engine, timesrc, log, run_dir,
                                     console=bool(isatty()), stdin=stdin, result_bridge=bridge, secrets=secrets,
                                     status_stream=status_stream))
    finally:
        try:
            executor.close(0.5)
        except Exception:  # noqa: BLE001
            pass


def _offline_startup(args: argparse.Namespace, cfg: Any, log: JsonlLog, warn: _Out) -> tuple[Any, Startup]:
    st = startup_checks(args, cfg, log, None, warn)
    if not st.probe_ok:
        cfg = _load_cfg(args, ['engine.forward_snapshots="never"'])
        st.cfg = cfg
    return cfg, st


def _run_replay(args: argparse.Namespace, cfg: Any, run_dir: str, run_id: str, stdin: TextIO,
                isatty: Callable[[], bool], say: _Out, warn: _Out) -> int:
    """``--replay``: RecordingSource on a ScaledTime against an offline SimExchange (recorded mode, no fills)."""
    from ctcbot.sources import RecordingSource
    path = os.path.abspath(args.replay)
    if not os.path.exists(path):
        raise CliExit(EXIT_USAGE, f"recording not found: {path}")
    speed = float(args.replay_speed or 1.0)
    if not speed > 0:
        raise CliExit(EXIT_USAGE, "--replay-speed must be > 0")
    first = _first_received_at(path)
    log = _make_log(run_dir, [], warn)
    try:
        log.event("cli_start", mode="replay", run_id=run_id, run_dir=run_dir, recording=path, speed=speed,
                  t_from=args.replay_from, t_to=args.replay_to, cfg_hash=cfg.hash)
        cfg, st = _offline_startup(args, cfg, log, warn)
        from sim.exchange import SimExchange
        from sim.recorded_market import RecordedMarket
        history = args.history or cfg.get("paths.history_dir", None)
        market = _construct(RecordedMarket, {"path": path, "recording": path, "recording_path": path, "cfg": cfg,
                                             "history": history, "history_dir": history, "log": log,
                                             "case": st.case_raw, "cost": st.cost})
        exchange = SimExchange(market, cfg, st.cost, log, fills=False)
        # mono 0 of this clock is the replay window's start in the recording's virtual time
        start = (float(args.replay_from) - first) if args.replay_from is not None else 0.0
        timesrc = RestartableTime(speed, start=max(0.0, start))
        api = ClockedExchange(exchange, timesrc)
        saved = None if args.fresh else load_state(os.path.join(run_dir, STATE_FILE))
        st.server_time = _server_time(_get(api, ApiKind.GET_TIME))
        plan = restart_plan(saved, st.server_time, time.time(), dry_run_now=True, mode="replay")
        log.event("restart_plan", restore=plan.restore, promotion=plan.promotion, reason=plan.reason,
                  server_comp_id=None if st.server_time is None else st.server_time.comp_id)
        say(f"replay {os.path.basename(path)} at x{speed:g} (run dir {run_dir}; {plan.reason})")
        src = AnchoredSource(RecordingSource(path, timesrc, log, name="rec", paced=True, t_from=args.replay_from,
                                             t_to=args.replay_to), api, first)
        rc = _run_core(args, cfg, run_dir, run_id, st, plan, saved, api, timesrc, log, stdin, isatty, say,
                       mode="replay", live=False, secrets=[], status_stream=warn.stream,
                       sources_fn=lambda core: [src])
        _dump_sim_results(exchange, run_dir, log)
        return rc
    finally:
        log.close()


def _run_sim(args: argparse.Namespace, cfg: Any, run_dir: str, run_id: str, stdin: TextIO,
             isatty: Callable[[], bool], say: _Out, warn: _Out) -> int:
    """``--sim research:NNN | recording:PATH``: SimSource plus SimExchange on a ScaledTime."""
    spec = str(args.sim)
    kind, _, arg = spec.partition(":")
    if kind not in ("research", "recording") or not arg:
        raise CliExit(EXIT_USAGE, "--sim takes research:NNN or recording:PATH")
    speed = float(args.sim_speed or 1.0)
    if not speed > 0:
        raise CliExit(EXIT_USAGE, "--sim-speed must be > 0")
    timesrc = RestartableTime(speed)
    log = _make_log(run_dir, [], warn)
    try:
        log.event("cli_start", mode="sim", run_id=run_id, run_dir=run_dir, sim=spec, speed=speed, cfg_hash=cfg.hash)
        cfg, st = _offline_startup(args, cfg, log, warn)
        from sim.exchange import SimExchange, SimSource
        history = args.history or cfg.get("paths.history_dir", None)
        engine = None
        if kind == "research":
            try:
                season = int(arg)
            except ValueError:
                raise CliExit(EXIT_USAGE, "--sim research:NNN needs a season number") from None
            if not history:
                raise CliExit(EXIT_USAGE, "--sim research:NNN needs --history DIR")
            from sim.research_market import ResearchMarket
            sdir = os.path.join(history, f"season-{season:03d}")
            market = _construct(ResearchMarket, {"history": history, "history_dir": history, "season": season,
                                                 "season_dir": sdir, "path": sdir, "cfg": cfg, "cost": st.cost,
                                                 "log": log, "case": st.case_raw})
            fv_dir = args.fv_cache or cfg.get("paths.fv_cache_dir", None)
            if fv_dir:
                from ctcbot.case import Schedule
                from sim.fv_cache import CachedFairValues
                engine = CachedFairValues(os.path.join(fv_dir, f"fv_holdout_{season:03d}.json"),
                                          Schedule(st.case.timing, cfg.get("cost.phases")))
        else:
            from sim.recorded_market import RecordedMarket
            path = os.path.abspath(arg)
            market = _construct(RecordedMarket, {"path": path, "recording": path, "recording_path": path,
                                                 "cfg": cfg, "history": history, "history_dir": history, "log": log,
                                                 "case": st.case_raw, "cost": st.cost})
        exchange = SimExchange(market, cfg, st.cost, log, fills=True)
        api = ClockedExchange(exchange, timesrc)
        plan = restart_plan(None, None, time.time(), dry_run_now=True, mode="sim")
        say(f"sim {spec} at x{speed:g} (run dir {run_dir})")
        rc = _run_core(args, cfg, run_dir, run_id, st, plan, None, api, timesrc, log, stdin, isatty, say,
                       mode="sim", live=False, secrets=[], engine_override=engine, status_stream=warn.stream,
                       sources_fn=lambda core: [SimSource(market, exchange, timesrc, name="sim")])
        _dump_sim_results(exchange, run_dir, log)
        return rc
    finally:
        log.close()


def _dump_sim_results(exchange: Any, run_dir: str, log: Any) -> None:
    """Offline modes only: write the simulated exchange's own record (violations, rate windows, resting maxima,
    live orders left, P&L) to ``run_dir/sim_exchange.json``, so a replay can be checked against the exchange rules
    independently of the bot's logs. Never raises."""
    try:
        res = exchange.results()
        res = {k: v for k, v in res.items() if k not in ("fills", "orders")} | {
            "n_fills": len(res.get("fills") or ()), "n_orders": len(res.get("orders") or {})}
        path = os.path.join(run_dir, "sim_exchange.json")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(res, f, indent=1, sort_keys=True, default=str)
        os.replace(tmp, path)
        log.event("sim_exchange_results", path=path, violations=res.get("violations_total"),
                  live_orders=res.get("live_orders"), rate=res.get("rate"))
    except Exception as e:  # noqa: BLE001 - a report must not change the exit code
        log.event("sim_exchange_results_error", error=f"{type(e).__name__}: {e}")
