"""One-line operator status (DESIGN.md section 4.D ``app/status.py``).

:func:`format_status` renders the dict returned by ``BotCore.status(now)``, for example::

    [DRY] NORMAL e1 r2 storm t=213.4 feed | ws 2/2 0.4s wm695 gaps0 dup0 | eng 0.3s 41ms q0 | act 87/150 rej0 unk0 |
    mtm +3,210 pk +4,020 | PORT+200 HAVEN-120 | frz - | holds -

Missing or malformed fields render as ``?``; the function never raises.
"""
from __future__ import annotations

import math
from typing import Any, Mapping


def _f(v: Any, fmt: str = "{:.1f}", none: str = "?") -> str:
    if v is None:
        return none
    try:
        x = float(v)
    except (TypeError, ValueError):
        return none
    if not math.isfinite(x):
        return none
    return fmt.format(x)


def _money(v: Any) -> str:
    if v is None:
        return "?"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "?"
    if not math.isfinite(x):
        return "?"
    return f"{x:+,.0f}"


def _names(v: Any, limit: int = 8) -> str:
    if not v:
        return "-"
    try:
        items = sorted(str(x) for x in v)
    except TypeError:
        return "?"
    more = len(items) - limit
    text = ",".join(items[:limit])
    return text + (f"+{more}" if more > 0 else "")


def _positions(v: Any, limit: int = 12) -> str:
    if not isinstance(v, Mapping):
        return "flat"
    parts = []
    for sym, q in v.items():
        try:
            qi = int(q)
        except (TypeError, ValueError):
            continue
        if qi:
            parts.append(f"{sym}{qi:+d}")
    if not parts:
        return "flat"
    return " ".join(parts[:limit]) + (" ..." if len(parts) > limit else "")


def format_status(st: Mapping[str, Any]) -> str:
    """Render a ``BotCore.status`` dict as one line."""
    try:
        return _format(st)
    except Exception as e:  # noqa: BLE001 - the status line must never take the bot down
        return f"[status unavailable: {type(e).__name__}]"


def _format(st: Mapping[str, Any]) -> str:
    g = st.get
    label = str(g("label") or "?")
    mode = str(g("mode") or "?")
    if g("killed"):
        mode = "FROZEN"
    rnd = g("round")
    head = (f"[{label}] {mode} e{g('epoch', '?')} r{rnd if rnd is not None else '?'} {g('phase') or '?'} "
            f"t={_f(g('t'))} {g('quality') or '?'}")
    if not g("running", True):
        head += " paused"
    if g("pending_reset"):
        head += f" RESET?({g('pending_reset')})"
    ws = (f"ws {g('conns_up', '?')}/{g('conns', '?')} {_f(g('frame_age'), '{:.1f}s')} wm{g('wm', '?')} "
          f"gaps{g('gaps', '?')} dup{g('dup', '?')}")
    eng = f"eng {_f(g('eng_age'), '{:.1f}s', '-')} {_f(g('eng_ms'), '{:.0f}ms')} q{g('eng_q', '?')}"
    if g("eng_alive") is False:
        eng += " DOWN"
    act = f"act {g('act_used', '?')}/{g('act_cap', '?')} rej{g('rej', 0)} unk{g('unk', 0)}"
    pnl = f"mtm {_money(g('mtm'))} pk {_money(g('peak'))}"
    pos = _positions(g("positions"))
    tail = f"frz {_names(g('frozen'))} | holds {_names(g('holds'))}"
    if g("disabled"):
        tail += f" | off {_names(g('disabled'))}"
    return " | ".join([head, ws, eng, act, pnl, pos, tail])
