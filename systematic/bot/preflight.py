"""Read-only competition-morning preflight for ctcbot (Systematic Equities, CTC 2026).

usage (from BOT; the folder name contains a space, so quote absolute paths):

    ../.venv/bin/python -I preflight.py --host HOST --key-file PATH [--listen-s 20] [--tries 5]
        [--profile FILE ...] [--set a.b=VALUE ...]

Pass the same ``--profile`` / ``--set`` flags as the bot run. They change the config hash (printed with the live
phrase a ``--live`` run with those flags will ask for), and ``feed.connections.N.local_addr`` binds the matching
test connection to that local address, exactly as the bot would.

Checks, in order:

1. python: the interpreter version (the bot is tested on 3.14).
2. websockets: the import the bot's feed uses (``websockets.asyncio.client.connect``) and its version.
3. key file: exists, a regular file owned by you, mode 600 (WARN otherwise), non-empty, one line, the bot's key
   format. The key is never printed: no character, length or hash of it appears in the output.
4. config and the bot's offline self-test (in process, through ``ctcbot.app.cli.main``).
5. GET /api/v1/competitions/time ``--tries`` times: state, competition id, fictional time and REST latency.
6. GET /cases/hurricane.json, diffed against the research case.json the bot uses; a timing difference is printed
   in a banner.
7. GET /api/v1/positions and /api/v1/orders/open: WARN if anything is held or open on this key.
8. One market-data WebSocket per ``feed.connections`` entry (two by default), all at once, each sending exactly
   the bot's one subscription message, counting frames by type for ``--listen-s`` seconds.

Safety: the only HTTP method this script can send is GET, to four allow-listed URLs on ``--host`` over https,
with no redirect followed and no proxy used; the key goes only in the ``X-API-Key`` header (https and wss). It
never places, cancels or modifies an order. Exit status: 0 when no check failed (WARNs allowed), 1 when one did,
2 for a usage error, 130 when interrupted.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True          # before any import: keeps engine/ and laws/ free of __pycache__

import argparse  # noqa: E402
import asyncio  # noqa: E402
import collections  # noqa: E402
import email.utils  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import ssl  # noqa: E402
import stat  # noqa: E402
import statistics  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Any, Sequence, TextIO  # noqa: E402

BOT = os.path.dirname(os.path.abspath(__file__))
if BOT not in sys.path:
    sys.path.insert(0, BOT)             # python -I does not add the script directory

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_INTERRUPTED = 0, 1, 2, 130
TESTED_PYTHON = (3, 14)
TESTED_WEBSOCKETS = "17."
MAX_KEY_FILE = 4096                     # the bot refuses a larger key file (cli._MAX_KEY_FILE)
REST_PATHS = ("/competitions/time", "/positions", "/orders/open")     # below exchange.rest_base
SLOW_MEDIAN_MS = 1000.0                 # REST latency above this (median, fresh TLS connection each) is a WARN
HTTP_TIMEOUT_S = 10.0


# ----------------------------------------------------------------------------------------------- report
@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


class Report:
    """Prints each check as it completes and keeps them for the summary. Every line is redacted."""

    def __init__(self, out: TextIO) -> None:
        self.out = out
        self.secrets: list[str] = []
        self.checks: list[Check] = []

    def add_secret(self, secret: str) -> None:
        if secret and len(secret) >= 8 and secret not in self.secrets:
            self.secrets.append(secret)

    def redact(self, text: Any) -> str:
        text = text if isinstance(text, str) else str(text)
        for s in self.secrets:
            text = text.replace(s, "***")
        return text

    def say(self, text: str = "") -> None:
        try:
            self.out.write(self.redact(text) + "\n")
            self.out.flush()
        except Exception:  # noqa: BLE001 - a closed pipe must not hide the exit status
            pass

    def add(self, name: str, status: str, detail: str = "", lines: Sequence[str] = ()) -> Check:
        c = Check(name, status, self.redact(detail))
        self.checks.append(c)
        self.say(f"[{status}] {name}{(': ' + detail) if detail else ''}")
        for line in lines:
            self.say(f"       {line}")
        return c

    def summary(self, host: str) -> int:
        n_fail = sum(1 for c in self.checks if c.status == FAIL)
        n_warn = sum(1 for c in self.checks if c.status == WARN)
        self.say("")
        self.say("=" * 78)
        self.say(f"PREFLIGHT SUMMARY  host {host}  {time.strftime('%Y-%m-%d %H:%M:%S')}")
        for c in self.checks:
            self.say(f"  {c.status:4}  {c.name}")
        if n_fail:
            self.say(f"RESULT: FAIL ({n_fail} FAIL, {n_warn} WARN). Fix every FAIL before the dry run; "
                     "do not go live.")
        elif n_warn:
            self.say(f"RESULT: WARN (0 FAIL, {n_warn} WARN). Read each WARN above with the team before the "
                     "dry run.")
        else:
            self.say("RESULT: PASS. Next: the 2-minute dry run on this host.")
        self.say("=" * 78)
        return EXIT_FAIL if n_fail else EXIT_OK


# ----------------------------------------------------------------------------------------------- local checks
def check_python(rep: Report) -> None:
    v = sys.version_info
    detail = f"{v.major}.{v.minor}.{v.micro} at {sys.executable} (prefix {sys.prefix})"
    if not sys.flags.isolated:
        detail += "; not run with -I"
    if (v.major, v.minor) == TESTED_PYTHON:
        rep.add("python", PASS, detail)
    else:
        rep.add("python", WARN, detail + f"; the bot is tested on {TESTED_PYTHON[0]}.{TESTED_PYTHON[1]} "
                                         "(use SYS/.venv/bin/python)")


def check_websockets(rep: Report) -> bool:
    try:
        import websockets
        from websockets.asyncio.client import connect  # noqa: F401 - the bot's feed uses exactly this
    except Exception as e:  # noqa: BLE001
        rep.add("websockets", FAIL, f"import failed ({type(e).__name__}: {e}): the bot's feed cannot run; use "
                                    "SYS/.venv/bin/python")
        return False
    ver = str(getattr(websockets, "__version__", "?"))
    if ver.startswith(TESTED_WEBSOCKETS):
        rep.add("websockets", PASS, f"{ver} (websockets.asyncio.client.connect)")
    else:
        rep.add("websockets", WARN, f"{ver}: the bot is tested on websockets 17.3")
    return True


def check_key_file(path: str, rep: Report) -> str | None:
    """Validate the key file the way the bot reads it. Returns the key (never printed) or None if unusable."""
    from ctcbot.app.cli import KEY_RE
    p = os.path.abspath(os.path.expanduser(path))
    try:
        lst = os.lstat(p)
        st = os.stat(p)
    except OSError as e:
        rep.add("key file", FAIL, f"{p}: {e.strerror or type(e).__name__}")
        return None
    if not stat.S_ISREG(st.st_mode):
        rep.add("key file", FAIL, f"{p} is not a regular file")
        return None
    warns: list[str] = []
    notes: list[str] = []
    mode = stat.S_IMODE(st.st_mode)
    if mode & 0o077:
        warns.append(f"mode {mode:04o} lets group or others read it: run  chmod 600 '{p}'")
    elif mode != 0o600:
        notes.append(f"mode {mode:04o} (owner only)")
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        warns.append(f"owned by uid {st.st_uid}, not by you (uid {os.getuid()})")
    if stat.S_ISLNK(lst.st_mode):
        notes.append("a symbolic link")
    try:
        with open(p, "rb") as f:
            raw = f.read(MAX_KEY_FILE + 1)
    except OSError as e:
        rep.add("key file", FAIL, f"{p}: cannot read it: {e.strerror or type(e).__name__}")
        return None
    if len(raw) > MAX_KEY_FILE:
        rep.add("key file", FAIL, f"{p} is larger than {MAX_KEY_FILE} bytes: not a key file (the bot refuses it)")
        return None
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        rep.add("key file", FAIL, f"{p} is not plain ASCII text (the bot refuses it); re-create it with pbpaste")
        return None
    key = text.strip()
    rep.add_secret(key)
    if not key:
        rep.add("key file", FAIL, f"{p} is empty")
        return None
    n_lines = sum(1 for line in text.splitlines() if line.strip())
    if n_lines != 1:
        rep.add("key file", FAIL, f"{p} holds {n_lines} non-empty lines: it must hold only the key, on one line")
        return None
    if not KEY_RE.match(key):
        rep.add("key file", FAIL, f"{p}: the content does not have the bot key format (16-256 characters from "
                                  "A-Z a-z 0-9 . _ ~ + / = -), so the bot would refuse it; was the whole key "
                                  "copied, and nothing else?")
        return None
    if text not in (key, key + "\n", key + "\r\n"):
        notes.append("spaces around the key (the bot strips them)")
    detail = f"{p}: one line, bot key format ok (the key is not shown)"
    if notes:
        detail += "; " + "; ".join(notes)
    if warns:
        rep.add("key file", WARN, detail + "; " + "; ".join(warns))
    else:
        rep.add("key file", PASS, f"{detail}; mode {mode:04o}")
    return key


def bot_args(host: str, profiles: Sequence[str], sets: Sequence[str]) -> list[str]:
    """The run_bot.py flags that give the same effective config as the bot run."""
    out = ["--host", host]
    for p in profiles:
        out += ["--profile", p]
    for s in sets:
        out += ["--set", s]
    return out


def load_bot_config(args: Sequence[str]) -> Any:
    """The effective config (and hash) ``run_bot.py`` builds from the same flags. Raises ``cli.CliExit``."""
    from ctcbot.app import cli
    ns = cli.build_parser().parse_args(list(args))
    return cli._load_cfg(ns)


def run_self_test(args: Sequence[str]) -> tuple[int, list[str]]:
    """The bot's own offline self-test, in process: ``run_bot.py --self-test`` with the same flags."""
    from ctcbot.app import cli
    buf = io.StringIO()
    code = cli.main(["--self-test", *args], stdin=io.StringIO(), isatty=lambda: False, environ={}, out=buf,
                    err=buf)
    return int(code), buf.getvalue().splitlines()


def check_self_test(args: Sequence[str], rep: Report) -> None:
    try:
        code, lines = run_self_test(args)
    except Exception as e:  # noqa: BLE001
        rep.add("offline self-test", FAIL, f"{type(e).__name__}: {e}")
        return
    oks = [ln for ln in lines if ln.startswith("[ok]")]
    bad = [ln for ln in lines if not ln.startswith("[ok]")]
    if code == 0:
        rep.add("offline self-test", PASS, f"exit 0, {len(oks)} [ok] lines (config, research case, engine "
                                           "snapshot-fix probe, cost model, settlement laws, case diff)",
                lines=bad[:20])
    else:
        rep.add("offline self-test", FAIL, f"exit {code}: the bot refuses --live if the engine probe fails",
                lines=lines[:30])


# ----------------------------------------------------------------------------------------------- HTTP (GET only)
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect, so the key cannot travel to another URL; a 3xx surfaces as an HTTP error."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def make_opener() -> Any:
    """An opener that verifies TLS certificates, uses no proxy and follows no redirect."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(),
                                       urllib.request.HTTPSHandler(context=ssl.create_default_context()))


@dataclass
class HttpResult:
    url: str
    status: int | None
    data: Any = None
    text: str = ""
    ms: float = 0.0
    error: str | None = None
    date: str | None = None
    wall: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300


class ReadOnlyHttp:
    """GET-only HTTPS client for the allow-listed URLs of one host. It has no other request method."""

    def __init__(self, rest_base: str, case_url: str, key: str, user_agent: str, rep: Report,
                 timeout: float = HTTP_TIMEOUT_S) -> None:
        base = urllib.parse.urlsplit(rest_base)
        if base.scheme != "https" or not base.hostname:
            raise ValueError("rest_base must be an https URL")
        self.rest_base = rest_base.rstrip("/")
        self.case_url = case_url
        urls = [self.rest_base + p for p in REST_PATHS] + [case_url]
        for u in urls:
            s = urllib.parse.urlsplit(u)
            if s.scheme != "https" or s.hostname != base.hostname:
                raise ValueError(f"refusing {u}: only https URLs on {base.hostname}")
        self.allowed = frozenset(urls)
        self._key = key
        self.ua = user_agent
        self.rep = rep
        self.timeout = float(timeout)
        self._opener = make_opener()
        self.requests: list[tuple[str, str]] = []           # (method, url) of every request made

    def url(self, rest_path: str) -> str:
        return self.rest_base + rest_path

    def get(self, url: str) -> HttpResult:
        if url not in self.allowed:
            raise ValueError(f"preflight only reads {sorted(self.allowed)}")
        req = urllib.request.Request(url, method="GET",
                                     headers={"User-Agent": self.ua, "Accept": "application/json"})
        req.add_unredirected_header("X-API-Key", self._key)
        if req.get_method() != "GET":                        # belt and braces: GET is the only method
            raise AssertionError("preflight sends GET requests only")
        self.requests.append((req.get_method(), url))
        status: int | None = None
        body = b""
        date = None
        err = None
        t0 = time.perf_counter()
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                status = int(getattr(r, "status", None) or r.getcode())
                date = r.headers.get("Date") if getattr(r, "headers", None) is not None else None
                body = r.read(4_000_000)
        except urllib.error.HTTPError as e:
            status = int(e.code)
            date = e.headers.get("Date") if getattr(e, "headers", None) is not None else None
            try:
                body = e.read(4096) or b""
            except Exception:  # noqa: BLE001
                body = b""
        except Exception as e:  # noqa: BLE001 - DNS, refused, TLS, timeout, reset: reported, never raised
            err = f"{type(e).__name__}: {getattr(e, 'reason', None) or e}"
        ms = (time.perf_counter() - t0) * 1000.0
        text = self.rep.redact(body.decode("utf-8", "replace")) if body else ""
        data = None
        if text.strip():
            try:
                data = json.loads(text)
            except ValueError:
                data = None
        if err is None and status is not None and not 200 <= status < 300:
            err = f"HTTP {status}: {' '.join(text.split())[:200]}"
        return HttpResult(url, status, data, text, ms, None if err is None else self.rep.redact(err), date,
                          time.time())


def _auth_hint(status: int | None) -> str:
    if status in (401, 403):
        return " (the key was rejected: check the key file and that this is the right host)"
    if status == 429:
        return " (rate limited)"
    return ""


def _clock_offset(res: HttpResult) -> float | None:
    """Laptop clock minus the server's Date header, in seconds (1-s resolution), or None."""
    if not res.date:
        return None
    try:
        return res.wall - email.utils.parsedate_to_datetime(res.date).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def check_time(http: ReadOnlyHttp, rep: Report, tries: int) -> Any:
    """GET /competitions/time ``tries`` times. Returns the last parsed ServerTime or None."""
    from ctcbot.rest import parse_time
    url = http.url("/competitions/time")
    results = [http.get(url) for _ in range(max(1, tries))]
    oks = [r for r in results if r.ok]
    lines = [f"try {i + 1}: {'HTTP ' + str(r.status) if r.status is not None else 'no answer'} "
             f"{r.ms:.0f} ms{'' if r.ok else ' - ' + str(r.error)}" for i, r in enumerate(results)]
    auth = [r for r in results if r.status in (401, 403)]
    if auth:
        rep.add("competition time", FAIL, f"GET {url}: {auth[0].error}{_auth_hint(auth[0].status)}", lines=lines)
        return None
    if not oks:
        first = results[0]
        rep.add("competition time", FAIL, f"GET {url}: no successful answer ({first.error}){_auth_hint(first.status)}",
                lines=lines)
        return None
    times = [parse_time(r.data) for r in oks]
    st = times[-1]
    lat = sorted(r.ms for r in oks)
    med = statistics.median(lat)
    elapsed = "-" if st.elapsed_active_s is None else f"{st.elapsed_active_s:g} s"
    detail = (f"state {st.state}, competition {st.comp_id}, fictional time {st.fictional_time}, elapsed active "
              f"{elapsed}; REST latency min {lat[0]:.0f} / median {med:.0f} / max {lat[-1]:.0f} ms "
              f"over {len(oks)}/{len(results)} tries (each a fresh TLS connection; the bot's keep-alive requests "
              "are faster)")
    off = _clock_offset(oks[-1])
    if off is not None:
        lines.append(f"laptop clock minus the server's Date header: {off:+.1f} s (information only: the bot "
                     "times the market from the exchange, not from the laptop clock)")
    warns = []
    if len(oks) < len(results):
        warns.append(f"{len(results) - len(oks)} of {len(results)} tries failed")
    if any(r.status == 429 for r in results):
        warns.append("HTTP 429 seen")
    if med > SLOW_MEDIAN_MS:
        warns.append(f"slow: median {med:.0f} ms (check the network; the bot's order timeout is 2 s)")
    comp_ids = {t.comp_id for t in times}
    if len(comp_ids) > 1:
        warns.append(f"the competition id changed between tries: {sorted(map(str, comp_ids))}")
    if st.comp_id is None and st.state is None and st.fictional_time is None:
        warns.append(f"the answer has no competition_id/state/fictional_time: {oks[-1].text[:200]}")
    rep.add("competition time", WARN if warns else PASS, detail + ("; " + "; ".join(warns) if warns else ""),
            lines=lines)
    return st


def _banner(rep: Report, lines: Sequence[str]) -> None:
    rep.say("!!" + "=" * 76)
    for line in lines:
        rep.say(f"!! {line}")
    rep.say("!!" + "=" * 76)


def _anchors(timing: dict, cfg: Any) -> dict[str, float] | None:
    from ctcbot.case import Schedule
    try:
        s = Schedule(timing, cfg.get("cost.phases"))
        return {"storm_start": s.storm_start, "storm_end": s.storm_end, "prelim": s.prelim, "final": s.final,
                "close": s.close, "s3_start": s.at(cfg.get("strategies.s3.start")),
                "h1_entry": s.at(cfg.get("strategies.h1.entry_start"))}
    except Exception:  # noqa: BLE001
        return None


def _fmt_anchors(a: dict[str, float] | None) -> str:
    if not a:
        return "?"
    return ", ".join(f"{k} {v:g} s" for k, v in a.items())


def check_case(http: ReadOnlyHttp, rep: Report, cfg: Any) -> Any:
    """GET the served case.json and diff it against the research case. Returns its CaseInfo or None."""
    from ctcbot.case import CaseError, CaseInfo, diff_case
    research_path = cfg.path(cfg.get("paths.research_case"))
    try:
        with open(research_path, encoding="utf-8") as f:
            research = json.load(f)
    except (OSError, ValueError) as e:
        rep.add("case.json", FAIL, f"cannot read the research case {research_path}: {e}")
        return None
    r = http.get(http.case_url)
    if not r.ok or not isinstance(r.data, dict):
        why = r.error or "the answer is not a JSON object"
        rep.add("case.json", FAIL, f"GET {http.case_url}: {why}{_auth_hint(r.status)}. The bot would run dry on "
                                   "the research case, and --live is refused without a served case.json")
        return None
    try:
        info = CaseInfo.from_dict(r.data)
    except CaseError as e:
        rep.add("case.json", FAIL, f"the served case.json is malformed ({e}): the bot cannot start on it")
        return None
    cd = diff_case(r.data, research)
    served_a, research_a = _anchors(info.timing, cfg), _anchors(research.get("timing") or {}, cfg)
    sched = f"round schedule (served): {_fmt_anchors(served_a)}"
    if cd.clean:
        rep.add("case.json", PASS, "identical to the research case.json, timing included", lines=[sched])
        return info
    lines = list(cd.lines[:40]) + ([f"... {len(cd.lines) - 40} more differences"] if len(cd.lines) > 40 else [])
    parts = []
    if cd.symbols:
        parts.append(f"values differ for {sorted(cd.symbols)}: S1-r3 excludes them until the analyst checks and "
                     "types 'caseok SYM' (or 'caseok all')")
    if cd.timing:
        parts.append("TIMING DIFFERS (see the banner)")
    rep.add("case.json", WARN, "differs from the research case.json: " + "; ".join(parts), lines=lines + [sched])
    if cd.timing:
        _banner(rep, ["CASE TIMING DIFFERS FROM THE RESEARCH CASE"]
                + [ln for ln in cd.lines if ln.startswith("timing")][:12]
                + [f"served:   {_fmt_anchors(served_a)}", f"research: {_fmt_anchors(research_a)}",
                   "The bot anchors every window to the served timing, but the strategies and costs were",
                   "calibrated on the research timing. Ask the organizers (Slack #questions) whether this is",
                   "the competition timing. Dry run: type 'caseok timing' once the team has reviewed it.",
                   "--live refuses to start on a timing difference unless --accept-timing-diff is given",
                   "(a team decision, after review)."])
    return info


def check_account(http: ReadOnlyHttp, rep: Report) -> None:
    """GET /positions and /orders/open: anything held or open on this key is a WARN."""
    from ctcbot.rest import parse_open_orders, parse_positions
    rp = http.get(http.url("/positions"))
    ro = http.get(http.url("/orders/open"))
    failed = [(name, r) for name, r in (("positions", rp), ("orders/open", ro)) if not r.ok]
    if failed:
        rep.add("positions and open orders", FAIL,
                "; ".join(f"GET /{n}: {r.error}{_auth_hint(r.status)}" for n, r in failed)
                + ". The bot's reconcile reads both endpoints")
        return
    pos = parse_positions(rp.data)
    held = {s: row.qty for s, row in sorted(pos.items()) if row.qty}
    orders = parse_open_orders(ro.data)
    lines = [f"/positions answer: {' '.join(rp.text.split())[:240] or '(empty)'}",
             f"/orders/open answer: {' '.join(ro.text.split())[:240] or '(empty)'}"]
    warns = []
    if held:
        warns.append("positions held: " + " ".join(f"{s}{q:+d}" for s, q in held.items()))
    if orders:
        desc = ", ".join(f"{o.symbol} {o.side.value} {o.remaining}@{o.price}" for o in orders[:8])
        warns.append(f"{len(orders)} open orders on this key ({desc}{', ...' if len(orders) > 8 else ''}): "
                     "is someone trading on this key (browser, another bot)? Cancel them before going live")
    if warns:
        rep.add("positions and open orders", WARN, "; ".join(warns), lines=lines)
    else:
        rep.add("positions and open orders", PASS, f"flat ({len(pos)} position rows, all zero) and no open orders",
                lines=lines)


# ----------------------------------------------------------------------------------------------- market data
@dataclass
class WsResult:
    name: str
    local_addr: tuple[str, int] | None = None
    connected: bool = False
    connect_ms: float | None = None
    status: int | None = None               # HTTP status of a rejected handshake
    error: str | None = None
    sent: list[str] = field(default_factory=list)
    counts: collections.Counter = field(default_factory=collections.Counter)
    news: collections.Counter = field(default_factory=collections.Counter)
    ack: dict | None = None
    frames: int = 0
    bad_frames: int = 0
    first_data_s: float | None = None
    max_gap_s: float = 0.0
    listened_s: float = 0.0


async def _ws_connect(url: str, **kwargs: Any) -> Any:
    """The real connect: the same call as the bot's ``sources._real_connect``."""
    from websockets.asyncio.client import connect
    return await connect(url, **kwargs)


def ws_kwargs(cfg: Any, key: str, local_addr: tuple[str, int] | None) -> dict:
    """The keyword arguments the bot's ``WsSingleSource`` passes to ``connect``."""
    kw: dict[str, Any] = dict(additional_headers={"X-API-Key": key},
                              user_agent_header=str(cfg.get("exchange.user_agent")),
                              open_timeout=float(cfg.get("feed.ws_open_timeout_s", 5.0)),
                              ping_interval=float(cfg.get("feed.ws_ping_interval_s", 20.0)),
                              ping_timeout=float(cfg.get("feed.ws_ping_timeout_s", 20.0)),
                              max_size=int(cfg.get("feed.ws_max_size", 8388608)), max_queue=None, proxy=None)
    if local_addr is not None:
        kw["local_addr"] = local_addr
    return kw


def subscription(symbols: Sequence[str], channels: Sequence[str]) -> str:
    """The bot's one subscription message, byte for byte (``sources.WsSingleSource._session``)."""
    return json.dumps({"action": "subscribe", "symbols": list(symbols), "channels": list(channels)})


async def _close(ws: Any) -> None:
    try:
        await asyncio.wait_for(ws.close(), 2.0)
    except BaseException:  # noqa: BLE001 - a dead peer never answers the close handshake
        tr = getattr(ws, "transport", None)
        if tr is not None:
            try:
                tr.abort()
            except Exception:  # noqa: BLE001
                pass


async def listen(name: str, url: str, kwargs: dict, sub: str, seconds: float, rep: Report) -> WsResult:
    """Connect, send the one subscription, count frames for ``seconds``, close."""
    res = WsResult(name, kwargs.get("local_addr"))
    connect = _ws_connect
    t0 = time.monotonic()
    try:
        ws = await asyncio.wait_for(connect(url, **kwargs), float(kwargs.get("open_timeout", 5.0)) + 5.0)
    except Exception as e:  # noqa: BLE001 - refused, rejected handshake, TLS, timeout, bind error
        from ctcbot.sources import _handshake_status
        res.status = _handshake_status(e)
        res.error = rep.redact(f"{type(e).__name__}: {e or 'timed out'}")[:300]
        return res
    res.connected = True
    res.connect_ms = (time.monotonic() - t0) * 1000.0
    start = time.monotonic()
    last = start
    try:
        await ws.send(sub)
        res.sent.append(sub)
        deadline = start + float(seconds)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), left)
            except (asyncio.TimeoutError, TimeoutError):
                break
            now = time.monotonic()
            res.max_gap_s = max(res.max_gap_s, now - last)
            last = now
            try:
                if isinstance(raw, (bytes, bytearray, memoryview)):
                    raw = bytes(raw).decode("utf-8")
                frame = json.loads(raw)
            except (ValueError, UnicodeDecodeError, TypeError):
                frame = None
            if not isinstance(frame, dict):
                res.bad_frames += 1
                continue
            res.frames += 1
            typ = str(frame.get("type"))
            res.counts[typ] += 1
            if typ == "news" and isinstance(frame.get("message"), dict):
                res.news[str(frame["message"].get("type"))] += 1
            if typ == "subscription_ack":
                if res.ack is None:
                    res.ack = frame
            elif typ != "heartbeat" and res.first_data_s is None:
                res.first_data_s = now - start
    except Exception as e:  # noqa: BLE001 - closed by the server, protocol error
        res.error = rep.redact(f"{type(e).__name__}: {e}")[:300]
    finally:
        res.listened_s = time.monotonic() - start
        res.max_gap_s = max(res.max_gap_s, time.monotonic() - last)
        await _close(ws)
    return res


def judge_ws(res: WsResult, symbols: Sequence[str], channels: Sequence[str], seconds: float, state: Any,
             rep: Report) -> None:
    name = f"market data {res.name}" + (f" (local_addr {res.local_addr[0]})" if res.local_addr else "")
    if not res.connected:
        hint = ""
        if res.status in (401, 403):
            hint = ": the handshake was rejected, so the key or host is wrong"
        elif res.status == 429:
            hint = ": too many connections or reconnects (25 market-data connections per team; count them)"
        elif res.local_addr:
            hint = (f": is {res.local_addr[0]} this laptop's address on the second network? (ipconfig getifaddr "
                    "<device>)")
        rep.add(name, FAIL, f"could not connect ({res.error}){hint}")
        return
    kinds = ", ".join(f"{k} {v}" for k, v in sorted(res.counts.items()))
    if res.news:
        kinds += " [news: " + ", ".join(f"{k} {v}" for k, v in sorted(res.news.items())) + "]"
    detail = (f"{res.frames} frames in {res.listened_s:.0f} s ({kinds or 'none'}); connect {res.connect_ms:.0f} ms; "
              f"first data {'-' if res.first_data_s is None else f'{res.first_data_s:.1f} s'}; "
              f"longest silence {res.max_gap_s:.1f} s; sent {len(res.sent)} message (the bot's subscription)")
    if res.bad_frames:
        detail += f"; {res.bad_frames} undecodable frames"
    data_frames = sum(v for k, v in res.counts.items() if k not in ("subscription_ack", "heartbeat"))
    if res.ack is None and res.frames == 0:
        rep.add(name, FAIL, f"no frame at all in {seconds:g} s, not even a subscription_ack"
                            + (f" ({res.error})" if res.error else ""))
        return
    warns = []
    if res.error:
        warns.append(f"closed early: {res.error}")
    if res.ack is None:
        warns.append("no subscription_ack received")
    else:
        if set(res.ack.get("symbols") or ()) != set(symbols):
            warns.append(f"the ack's symbols differ from the subscription: {res.ack.get('symbols')}")
        if set(res.ack.get("channels") or ()) != set(channels):
            warns.append(f"the ack's channels differ from the subscription: {res.ack.get('channels')}")
    if data_frames == 0:
        st = getattr(state, "state", None)
        warns.append(f"no market data in {seconds:g} s (competition state {st}): the market may be paused "
                     "between rounds; confirm 'ws 2/2' and moving frame counts in the dry run")
    rep.add(name, WARN if warns else PASS, detail + ("; " + "; ".join(warns) if warns else ""))


def check_feed(cfg: Any, key: str, symbols: Sequence[str], seconds: float, state: Any, rep: Report) -> None:
    url = str(cfg.get("exchange.ws_url"))
    channels = list(cfg.get("feed.channels"))
    sub = subscription(symbols, channels)
    conns = []
    for i, entry in enumerate(cfg.get("feed.connections")):
        la = entry.get("local_addr")
        conns.append((str(entry.get("name") or f"ws{i}"), (str(la[0]), int(la[1])) if la else None))
    rep.say(f"... listening on {len(conns)} market-data connections for {seconds:g} s ({url})")

    async def all_conns() -> list[WsResult]:
        return list(await asyncio.gather(*(listen(n, url, ws_kwargs(cfg, key, la), sub, seconds, rep)
                                           for n, la in conns)))

    for res in asyncio.run(all_conns()):
        judge_ws(res, symbols, channels, seconds, state, rep)


# ----------------------------------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="preflight.py", description="Read-only competition-morning check for ctcbot: "
                                "GET requests and market-data WebSockets only; never sends an order action.")
    p.add_argument("--host", required=True, help="exchange host, as passed to run_bot.py --host")
    p.add_argument("--key-file", required=True, metavar="PATH", help="the bot key file (never printed)")
    p.add_argument("--listen-s", type=float, default=20.0, metavar="S", help="seconds to count frames (20)")
    p.add_argument("--tries", type=int, default=5, metavar="N", help="GET /competitions/time N times (5)")
    p.add_argument("--profile", action="append", default=[], metavar="PATH", help="as for run_bot.py")
    p.add_argument("--set", action="append", default=[], dest="sets", metavar="a.b=VALUE", help="as for run_bot.py")
    return p


def main(argv: Sequence[str] | None = None, *, out: TextIO | None = None) -> int:
    out_s = out if out is not None else sys.stdout
    try:
        args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as e:
        return EXIT_USAGE if e.code not in (0, None) else EXIT_OK
    rep = Report(out_s)
    try:
        return _main(args, rep)
    except KeyboardInterrupt:
        rep.say("interrupted")
        return EXIT_INTERRUPTED


def _main(args: argparse.Namespace, rep: Report) -> int:
    from ctcbot.app import cli
    if not cli.HOST_RE.match(args.host or ""):
        rep.say("preflight.py: error: --host must be a bare host name (optionally :port), without a scheme or path")
        return EXIT_USAGE
    if not 0 < args.listen_s <= 300 or not 1 <= args.tries <= 50:
        rep.say("preflight.py: error: --listen-s must be in (0, 300] and --tries in [1, 50]")
        return EXIT_USAGE
    rep.say(f"ctcbot preflight: host {args.host}, key file {args.key_file}")
    rep.say("read-only: GET requests and market-data WebSockets only; no order is placed, cancelled or modified")
    check_python(rep)
    ws_ok = check_websockets(rep)
    key = check_key_file(args.key_file, rep)
    flags = bot_args(args.host, args.profile, args.sets)
    try:
        cfg = load_bot_config(flags)
    except cli.CliExit as e:
        rep.add("config", FAIL, (e.msg or "").strip() or f"exit {e.code}")
        return rep.summary(args.host)
    host = urllib.parse.urlsplit(str(cfg.get("exchange.rest_base"))).hostname or args.host
    files = ", ".join(os.path.basename(s) for s in cfg.sources if not s.startswith("set:"))
    rep.add("config", PASS, f"hash {cfg.hash} (files {files}; --host {args.host}; {len(args.sets)} --set item(s)); "
                            f"a --live run with exactly these flags asks for the phrase: LIVE {host} {cfg.hash[:6]}")
    check_self_test(flags, rep)
    if key is None:
        rep.add("exchange checks", SKIP, "no usable key: nothing was sent")
        return rep.summary(args.host)
    http = ReadOnlyHttp(str(cfg.get("exchange.rest_base")), str(cfg.get("exchange.case_url")), key,
                        str(cfg.get("exchange.user_agent")), rep)
    state = check_time(http, rep, args.tries)
    info = check_case(http, rep, cfg)
    check_account(http, rep)
    symbols = _symbols(info, cfg)
    if ws_ok:
        check_feed(cfg, key, symbols, float(args.listen_s), state, rep)
    else:
        rep.add("market data", SKIP, "websockets is not importable")
    return rep.summary(args.host)


def _symbols(info: Any, cfg: Any) -> list[str]:
    """The bot subscribes to the served case's symbols, else the research case's (``st.case.symbols``)."""
    if info is not None and info.symbols:
        return list(info.symbols)
    try:
        from ctcbot.case import CaseInfo
        return list(CaseInfo.from_file(cfg.path(cfg.get("paths.research_case"))).symbols)
    except Exception:  # noqa: BLE001
        from ctcbot.types import SYMBOLS
        return list(SYMBOLS)


if __name__ == "__main__":
    raise SystemExit(main())
