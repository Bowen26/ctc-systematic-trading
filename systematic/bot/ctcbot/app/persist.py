"""Run ids, atomic state files and the restart rule (DESIGN.md section 4.D ``app/persist.py``).

``state.json`` is written every ``app.persist_s`` by the core's persist job (schema in DESIGN.md §4.D). On a
start in an existing run dir, :func:`restart_plan` decides whether the saved state may be restored (only when its
``comp_id`` equals the server's current ``competition_id``) and whether the start is a **promotion** (the saved
state was written in the other mode, dry-run versus live).
"""
from __future__ import annotations

import json
import os
import secrets as _secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ctcbot.types import ServerTime

STATE_FILE = "state.json"


def new_run_id() -> str:
    """``YYYYmmddTHHMMSS`` plus 4 random hex digits; every start gets a new one."""
    return time.strftime("%Y%m%dT%H%M%S") + _secrets.token_hex(2)


def _json_default(o: Any) -> Any:
    if isinstance(o, (set, frozenset)):
        try:
            return sorted(o, key=str)
        except Exception:  # noqa: BLE001
            return list(o)
    if isinstance(o, (bytes, bytearray)):
        return bytes(o).decode("utf-8", "replace")
    return str(o)


def save_state(path: str, d: Mapping[str, Any]) -> None:
    """Write ``d`` as JSON to ``path`` atomically (temporary file in the same directory, fsync, ``os.replace``).

    A reader never sees a partial file: it sees either the previous state or the new one.
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, default=_json_default, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def load_state(path: str) -> dict | None:
    """The saved state, or None when the file is missing, unreadable, not JSON or not an object."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def write_json_atomic(path: str, obj: Any) -> None:
    """Alias of :func:`save_state` for any JSON value (used for the adjustment files and tests)."""
    save_state(path, obj)


@dataclass(frozen=True)
class RestartPlan:
    """Outcome of :func:`restart_plan`."""
    restore: bool              # restore the saved state (engine log, laws, coverage, orders, risk, runner)
    promotion: bool            # the saved state was written in the other mode (dry-run <-> live)
    engine_log: str | None     # the saved engine-state file name (relative to the run dir), when restoring
    reason: str


def _server_comp_id(server_time: Any) -> str | None:
    if server_time is None:
        return None
    if isinstance(server_time, ServerTime):
        return server_time.comp_id
    if isinstance(server_time, str):
        return server_time or None
    if isinstance(server_time, Mapping):
        v = server_time.get("competition_id", server_time.get("comp_id"))
        return None if v is None else str(v)
    v = getattr(server_time, "comp_id", None)
    return None if v is None else str(v)


def restart_plan(saved: Mapping[str, Any] | None, server_time: Any, now_wall: float, dry_run_now: bool,
                 mode: str) -> RestartPlan:
    """Decide how a start in a run dir with a saved state proceeds.

    * Never restore in ``sim`` mode, without a saved state, or when the server's ``competition_id`` is unknown.
    * Restore only when the saved ``comp_id`` equals the server's (in replay, the comp_id the replay's
      SimExchange serves).
    * ``promotion`` is True when the saved ``dry_run`` differs from ``dry_run_now`` (the standby's path from
      dry-run to ``--live``, or the reverse).
    """
    if mode == "sim":
        return RestartPlan(False, False, None, "sim mode never restores")
    if not saved or not isinstance(saved, Mapping):
        return RestartPlan(False, False, None, "no saved state")
    saved_cid = saved.get("comp_id")
    server_cid = _server_comp_id(server_time)
    age = None
    try:
        age = float(now_wall) - float(saved.get("saved_wall"))
    except (TypeError, ValueError):
        pass
    age_txt = "" if age is None else f", saved {age:.0f} s ago"
    if saved_cid is None:
        return RestartPlan(False, False, None, f"saved state has no comp_id{age_txt}")
    if server_cid is None:
        return RestartPlan(False, False, None, f"server competition_id unknown; not restoring{age_txt}")
    if str(saved_cid) != str(server_cid):
        return RestartPlan(False, False, None, f"comp_id changed ({saved_cid} -> {server_cid}){age_txt}")
    engine_log = saved.get("engine_log")
    engine_log = os.path.basename(str(engine_log)) if engine_log else None
    saved_dry = bool(saved.get("dry_run", True))
    if saved_dry != bool(dry_run_now):
        direction = "dry-run -> live" if saved_dry else "live -> dry-run"
        return RestartPlan(True, True, engine_log, f"promotion {direction} (comp_id {server_cid}){age_txt}")
    return RestartPlan(True, False, engine_log, f"restore (same comp_id {server_cid}){age_txt}")


def engine_log_name(fmt: str, run_id: str, epoch: int) -> str:
    """The engine-state file name for one run and season epoch (``engine.persist_file`` formatted)."""
    try:
        name = str(fmt).format(run_id=run_id, epoch=epoch)
    except (KeyError, IndexError, ValueError):
        name = f"engine_state_{run_id}_e{epoch}.jsonl"
    return os.path.basename(name)


def read_engine_log(path: str | None, limit: int = 200_000) -> list[dict]:
    """The messages of an engine-state JSONL file, in order; unreadable lines are skipped."""
    out: list[dict] = []
    if not path:
        return out
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if isinstance(m, dict):
                    out.append(m)
                    if len(out) >= limit:
                        break
    except OSError:
        return []
    return out


def ledger_to_wall(ledger: Iterable[float], now_mono: float, now_wall: float) -> list[float]:
    """Convert limiter ledger entries from mono to wall time."""
    return [round(now_wall + (float(t) - now_mono), 6) for t in ledger]


def ledger_from_wall(ledger_wall: Sequence[Any] | None, now_mono: float, now_wall: float,
                     max_age_s: float = 61.0) -> list[float]:
    """Convert saved wall-time ledger entries back to mono time, keeping those younger than ``max_age_s``."""
    out: list[float] = []
    for t in ledger_wall or ():
        try:
            tw = float(t)
        except (TypeError, ValueError):
            continue
        age = now_wall - tw
        if 0.0 <= age < max_age_s:
            out.append(now_mono - age)
    out.sort()
    return out
