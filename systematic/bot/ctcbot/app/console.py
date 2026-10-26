"""Operator console parser (DESIGN.md section 4.D ``app/console.py``).

:func:`parse` turns one typed line into a :class:`Command`: tokens are split on whitespace, the command name is
lower-cased and symbol arguments are upper-cased (keywords such as ``all``, ``timing``, ``off`` stay lower-case).
It never raises. :func:`validate` checks the arguments of the commands the core handles itself and returns an
error text (or None); commands meant for the strategies are passed through and checked there.
"""
from __future__ import annotations

import math
from typing import Sequence

from ctcbot.types import SYMBOLS, Command

MAX_LINE = 256
MAX_TOKENS = 8

#: commands whose first argument is a symbol (unless it is a keyword)
SYMBOL_FIRST = frozenset({"x", "e", "f", "m", "a", "caseok", "u", "ua", "s1ok", "x1", "lawok"})
#: argument words that are never symbols
KEYWORDS = frozenset({"all", "timing", "off", "on", "keep", "new", "restart", "rounds", "costmult"})

#: commands handled by the core (DESIGN.md §4.D command table)
CORE_COMMANDS = frozenset({"k", "cx", "p", "r", "unfreeze-kill", "x", "e", "f", "m", "a", "b", "caseok", "season",
                           "eng", "st", "pos", "ord", "ann", "holds", "q", "help", "ov"})

#: engine parameters an announcement override (``ov``) may change, by block -> symbol -> parameter names
OVERRIDE_PARAMS: dict[str, dict[str, tuple[str, ...]]] = {
    "settle.island": {s: ("V0", "a", "K") for s in ("PORT", "ISLE", "AQUA", "GRID", "FERRY")},
    "settle.rebuild": {"RECON": ("a", "b", "c"), "MAT": ("a", "b", "c"), "GEN": ("a", "b", "c")},
    "settle.insurance": {"HAVEN": ("v0", "slope", "attach", "top"), "REIN": ("v0", "slope", "attach", "top")},
    "physics.k": {s: ("",) for s in ("PORT", "ISLE", "AQUA", "GRID", "FERRY")},
}


def resolve_override(token: str) -> str | None:
    """Full engine parameter path for an ``ov`` target, or None if it is not an allowed parameter.

    Accepts the full path (``settle.insurance.HAVEN.attach``) or the short form ``SYM.param`` in any case
    (``haven.attach``, ``port.a``, ``recon.c``). ``SYM.k`` is the closure cost K of an island company and
    ``SYM.dmg`` its damage rate ``physics.k.SYM``.
    """
    if not isinstance(token, str) or not token:
        return None
    parts = token.strip().split(".")
    if len(parts) == 2:                                  # short form SYM.param
        sym, prm = parts[0].upper(), parts[1]
        for block, syms in OVERRIDE_PARAMS.items():
            if block == "physics.k":
                continue
            for name in syms.get(sym, ()):
                if name.lower() == prm.lower():
                    return f"{block}.{sym}.{name}"
        if prm.lower() == "dmg" and sym in OVERRIDE_PARAMS["physics.k"]:   # damage rate, any island company
            return f"physics.k.{sym}"
        return None
    for block, syms in OVERRIDE_PARAMS.items():           # full path, exact case
        for sym, names in syms.items():
            for name in names:
                if token == (f"{block}.{sym}" if name == "" else f"{block}.{sym}.{name}"):
                    return token
    return None
#: commands passed to ``StrategyRunner.on_command``
RUNNER_COMMANDS = frozenset({"h1", "s3", "s1r3", "lag", "rein", "flat", "u", "ua", "s1ok", "x1", "lawok"})

HELP = (
    "k kill+freeze | cx cancel all | p pause | r resume (releases drawdown/restart/feed_down/auth/season_suspect/"
    "comp_id_suspect) | unfreeze-kill | x SYM / e SYM disable/enable | f SYM PX | f SYM off | m SYM X | a SYM X | "
    "b N action cap (max 150) | caseok SYM|all|timing | season keep|new | eng restart | st pos ord ann holds | "
    "q quit (kill, wait until every order is cancelled, exit) | "
    "h1 on|off|0|0.5|1 | s3 on|off|rounds 3|costmult X | s1r3 on|off | lag on|off | rein on|off | flat on|off | "
    "u SYM | ua SYM | s1ok SYM | x1 SYM | lawok SYM|all | "
    "ov PARAM X|off engine parameter override (e.g. ov haven.attach 500, ov recon.c 1500, ov port.a 0.3)"
)


def _clean(line: str) -> str:
    return "".join(ch if (ch.isprintable() or ch in " \t") else " " for ch in line[:MAX_LINE])


def parse(line: str | None, source: str = "console") -> Command | None:
    """Parse one console line. Returns None for an empty line or a ``#`` comment; never raises."""
    if not isinstance(line, str):
        try:
            line = str(line) if line is not None else ""
        except Exception:  # noqa: BLE001
            return None
    text = _clean(line).strip()
    if not text or text.startswith("#"):
        return None
    toks = text.split()[:MAX_TOKENS]
    name = toks[0].lower()
    args: list[str] = []
    if name == "ov":                                     # parameter paths are case-sensitive: keep them as typed
        return Command(name=name, args=tuple(toks[1:]), source=source)
    for i, tok in enumerate(toks[1:]):
        low = tok.lower()
        if i == 0 and name in SYMBOL_FIRST and low not in KEYWORDS:
            args.append(tok.upper())
        else:
            args.append(low)
    return Command(name=name, args=tuple(args), source=source)


def _num(s: str) -> float | None:
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _sym_ok(s: str, symbols: Sequence[str]) -> bool:
    return s in symbols


def validate(cmd: Command, symbols: Sequence[str] = SYMBOLS) -> str | None:
    """Error text for a malformed core command, None when it is well formed (or not a core command)."""
    n, a = cmd.name, cmd.args
    if n in ("k",):
        return None                                    # a kill is never refused for extra words
    if n in ("cx", "p", "r", "unfreeze-kill", "st", "pos", "ord", "ann", "holds", "q", "help"):
        return None if not a else f"{n} takes no arguments"
    if n in ("x", "e"):
        if len(a) != 1 or not _sym_ok(a[0], symbols):
            return f"usage: {n} SYM"
        return None
    if n == "f":
        if len(a) != 2 or not _sym_ok(a[0], symbols):
            return "usage: f SYM PX | f SYM off"
        if a[1] == "off":
            return None
        v = _num(a[1])
        return None if v is not None and v > 0 else "f: PX must be a positive number"
    if n == "m":
        if len(a) != 2 or not _sym_ok(a[0], symbols):
            return "usage: m SYM X"
        v = _num(a[1])
        return None if v is not None and v > 0 else "m: X must be a positive number"
    if n == "a":
        if len(a) != 2 or not _sym_ok(a[0], symbols):
            return "usage: a SYM X"
        return None if _num(a[1]) is not None else "a: X must be a number"
    if n == "b":
        if len(a) != 1:
            return "usage: b N"
        try:
            v = int(a[0])
        except ValueError:
            return "b: N must be an integer"
        return None if v >= 0 else "b: N must be >= 0"
    if n == "caseok":
        if len(a) != 1 or not (a[0] in ("all", "timing") or _sym_ok(a[0], symbols)):
            return "usage: caseok SYM|all|timing"
        return None
    if n == "season":
        return None if len(a) == 1 and a[0] in ("keep", "new") else "usage: season keep|new"
    if n == "eng":
        return None if len(a) == 1 and a[0] == "restart" else "usage: eng restart"
    if n in ("u", "ua", "s1ok", "x1"):
        return None if len(a) == 1 and _sym_ok(a[0], symbols) else f"usage: {n} SYM"
    if n == "lawok":
        return None if len(a) == 1 and (a[0] == "all" or _sym_ok(a[0], symbols)) else "usage: lawok SYM|all"
    if n in ("h1", "s3", "s1r3", "lag", "rein", "flat"):
        return None if a else f"usage: {n} on|off|..."
    if n == "ov":
        if len(a) != 2 or resolve_override(a[0]) is None:
            return "usage: ov PARAM X|off, e.g. ov haven.attach 500 (see ANNOUNCEMENT_CARD.md)"
        return None if a[1].lower() == "off" or _num(a[1]) is not None else "ov: X must be a number or off"
    return f"unknown command {n!r}"


def is_known(name: str) -> bool:
    """True for a command the core or the runner understands."""
    return name in CORE_COMMANDS or name in RUNNER_COMMANDS
