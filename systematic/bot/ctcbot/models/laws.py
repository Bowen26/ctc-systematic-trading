"""Settlement-law cross-check and the live law check (DESIGN.md section 4.C, ``models/laws.py``).

* ``load_laws_module`` imports ``laws/se_laws.py`` read-only by file path (no bytecode is written next to it).
* ``LawsCrossCheck`` rebuilds the settlement laws' inputs from the forwarded feed: D and L are the sums of the
  final ``damage_pct`` and ``industry_loss_musd`` per round (from news or snapshot ``damage`` lists); T per round
  comes from ``se_laws.ClosureIntegrator`` over the merged storm path (observations, plus track points in
  observation gaps with linearly interpolated intensity). T is integrated once per round when its storm ends,
  in a background worker (mode ``"thread"``) or synchronously (mode ``"inline"``); ``values()`` only reads
  cached results, so nothing heavy runs on the event loop at 740 s. S1-r3 compares the engine's S with it.
* ``LawCheck`` is the rounds 1-2 live law check: at each report it records the stock mids and the engine means
  before, takes the means after from the first fair value that ingested the report, and at +``horizon_s``
  compares the realised mid move with ``slope * (mu_after - mu_before)``. ``passed(sym)`` gates S1-r3 (g).
"""
from __future__ import annotations

import bisect
import importlib.util
import math
import os
import queue
import sys
import threading
from types import ModuleType
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from ctcbot.models.coverage import CLOSURE_SYMBOLS, StormCoverage, as_round, unwrap
from ctcbot.types import SYMBOLS

if TYPE_CHECKING:  # pragma: no cover - type hints only
    from ctcbot.config import Config
    from ctcbot.types import BookView, EventLog, FairValueSnapshot

ISLAND_COMPANIES: tuple[str, ...] = ("PORT", "ISLE", "AQUA", "GRID", "FERRY")
CLOSURE_COMPANIES: tuple[str, ...] = ("PORT", "GRID", "FERRY")
DL_SYMBOLS: tuple[str, ...] = tuple(s for s in SYMBOLS if s not in CLOSURE_SYMBOLS)
ROUNDS: tuple[int, ...] = (1, 2, 3)


def load_laws_module(path: str) -> ModuleType:
    """Import the settlement-law module at ``path`` (``se_laws.py``) without writing bytecode next to it.

    Raises ``ImportError`` when the file is missing or lacks ``values`` and ``ClosureIntegrator``.
    """
    sys.dont_write_bytecode = True
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ImportError(f"laws module not found: {path}")
    stem = os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location("se_laws_" + stem if stem != "se_laws" else "se_laws", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load laws module {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for attr in ("values", "ClosureIntegrator"):
        if not callable(getattr(mod, attr, None)):
            raise ImportError(f"laws module {path} has no callable {attr}")
    return mod


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and math.isfinite(float(v)):
        return float(v)
    return None


def merged_path(obs: Mapping[int, tuple[float, float, float, float]],
                track: Mapping[int, tuple[float, float, float]]) -> list[tuple[int, float, float, float, float]]:
    """Observations plus the track points that fall in observation gaps, in age order.

    A track point's intensity is interpolated linearly between the bracketing observations; before the first
    observation it holds the first observed intensity and after the last one the last. Without any observation
    the intensity is unknown and the path is empty.
    """
    if not obs:
        return []
    ages = sorted(obs)
    out = [(a, *obs[a]) for a in ages]
    for a, (x, y, r) in track.items():
        if a in obs:
            continue
        k = bisect.bisect_left(ages, a)
        if k == 0:
            inten = obs[ages[0]][3]
        elif k >= len(ages):
            inten = obs[ages[-1]][3]
        else:
            a0, a1 = ages[k - 1], ages[k]
            i0, i1 = obs[a0][3], obs[a1][3]
            inten = i0 + (i1 - i0) * (a - a0) / (a1 - a0)
        out.append((a, x, y, r, inten))
    out.sort(key=lambda p: p[0])
    return out


class _Round:
    """One round's storm path and its end state."""

    __slots__ = ("obs", "track", "ended", "version")

    def __init__(self) -> None:
        self.obs: dict[int, tuple[float, float, float, float]] = {}
        self.track: dict[int, tuple[float, float, float]] = {}
        self.ended = False
        self.version = 0


class LawsCrossCheck:
    """Settlement values from the laws module, rebuilt from the forwarded stream (see the module docstring)."""

    def __init__(self, cfg: "Config", log: "EventLog", laws: ModuleType, coverage: StormCoverage,
                 mode: str = "thread") -> None:
        if mode not in ("thread", "inline"):
            raise ValueError(f"LawsCrossCheck mode must be 'thread' or 'inline', not {mode!r}")
        self.cfg = cfg
        self.log = log
        self.laws = laws
        self.coverage = coverage
        self.mode = mode
        self._tol: dict[str, float] = {}
        for s in SYMBOLS:
            try:
                self._tol[s] = float(cfg.sym(s)["laws_tol"])
            except (KeyError, TypeError, ValueError, AttributeError):
                self._tol[s] = 0.02
        self._lock = threading.Lock()
        self._q: queue.Queue | None = None
        self._thread: threading.Thread | None = None
        self._closed = False
        self._gen = 0
        self._results: dict[int, tuple[int, int, dict[str, float] | None]] = {}   # r -> (gen, version, T)
        self._computing = 0
        self.computations = 0
        if mode == "thread":
            self._q = queue.Queue()
            self._thread = threading.Thread(target=self._worker, name="laws-worker", daemon=True)
            self._thread.start()
        self.reset()

    # ------------------------------------------------------------------ lifecycle
    def reset(self) -> None:
        """Forget the season (results of computations still in flight are discarded)."""
        with self._lock:
            self._gen += 1
            self._results = {}
        self._rounds: dict[int, _Round] = {}
        self._finals: dict[int, dict] = {}
        self._cache_key: tuple | None = None
        self._cache_val: dict[str, float | None] | None = None

    def close(self, timeout: float = 1.0) -> None:
        """Stop the worker thread (thread mode); idempotent."""
        self._closed = True
        if self._q is not None and self._thread is not None and self._thread.is_alive():
            self._q.put(None)
            self._thread.join(timeout)

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until every scheduled computation finished (tests, restore); True if idle."""
        if self._q is None:
            return True
        import time as _time
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            with self._lock:
                busy = self._computing
            if busy == 0 and self._q.unfinished_tasks == 0:
                return True
            _time.sleep(0.005)
        return False

    # ------------------------------------------------------------------ ingest
    def _rec(self, r: int) -> _Round:
        rec = self._rounds.get(r)
        if rec is None:
            rec = self._rounds[r] = _Round()
        return rec

    def _add_obs(self, rec: _Round, o: Mapping[str, Any]) -> bool:
        age = _num(o.get("age"))
        vals = [_num(o.get(k)) for k in ("x", "y", "radius", "intensity")]
        if age is None or age < 0 or any(v is None for v in vals):
            return False
        a = int(round(age))
        tup = (vals[0], vals[1], vals[2], vals[3])
        if rec.obs.get(a) == tup:
            return False
        rec.obs[a] = tup  # type: ignore[assignment]
        return True

    def _add_track(self, rec: _Round, points: Any) -> bool:
        changed = False
        if not isinstance(points, list):
            return False
        for p in points:
            if not isinstance(p, (list, tuple)) or len(p) < 4:
                continue
            vals = [_num(v) for v in p[:4]]
            if any(v is None for v in vals) or vals[0] < 0:  # type: ignore[operator]
                continue
            a = int(round(vals[0]))  # type: ignore[arg-type]
            tup = (vals[1], vals[2], vals[3])
            if rec.track.get(a) != tup:
                rec.track[a] = tup  # type: ignore[assignment]
                changed = True
        return changed

    def _add_final(self, rep: Mapping[str, Any]) -> None:
        r = as_round(rep.get("round"))
        if r is None or r < 1:
            return
        rec = self._rec(r)
        newly_ended = not rec.ended
        rec.ended = True
        if rep.get("stage") == "final" and isinstance(rep.get("damage_pct"), dict) and r not in self._finals:
            dmg = {c: (_num(rep["damage_pct"].get(c)) or 0.0) for c in ISLAND_COMPANIES}
            loss = _num(rep.get("industry_loss_musd"))
            self._finals[r] = {"damage": dmg, "loss": loss if loss is not None else 0.0}
            self._cache_key = None
        if newly_ended:
            self._schedule(r)

    def on_news(self, msg: dict) -> None:
        """Ingest one forwarded news message (storm, track, storm_dissipated, damage_report)."""
        m = unwrap(msg)
        if m is None:
            return
        t = m.get("type")
        if t == "snapshot":
            self.on_snapshot(m)
            return
        r = as_round(m.get("round"))
        if r is None or r < 1:
            return
        if t == "storm":
            rec = self._rec(r)
            if self._add_obs(rec, m):
                self._changed(r, rec)
        elif t == "track":
            rec = self._rec(r)
            if self._add_track(rec, m.get("points")):
                self._changed(r, rec)
        elif t == "storm_dissipated":
            rec = self._rec(r)
            if not rec.ended:
                rec.ended = True
                self._schedule(r)
        elif t == "damage_report":
            self._add_final(m)

    def on_snapshot(self, msg: dict) -> None:
        """Ingest one released snapshot: obs, track, damage list, and an aftermath or closed phase."""
        m = unwrap(msg)
        if m is None:
            return
        for rep in m.get("damage") or []:
            if isinstance(rep, dict):
                self._add_final(rep)
        r = as_round(m.get("round"))
        if r is None or r < 1:
            return
        rec = self._rec(r)
        changed = False
        o = m.get("obs")
        if isinstance(o, dict):
            changed |= self._add_obs(rec, o)
        changed |= self._add_track(rec, m.get("track"))
        if changed:
            self._changed(r, rec)
        if m.get("phase") in ("aftermath", "closed") and not rec.ended:
            rec.ended = True
            self._schedule(r)

    def rebuild(self, messages: Iterable[dict]) -> None:
        """Reset, then replay messages (an engine log of news messages and snapshots, in order)."""
        self.reset()
        for m in messages:
            mm = unwrap(m)
            if mm is None:
                continue
            if mm.get("type") == "snapshot":
                self.on_snapshot(mm)
            else:
                self.on_news(mm)

    # ------------------------------------------------------------------ closure integration
    def _changed(self, r: int, rec: _Round) -> None:
        rec.version += 1
        self._cache_key = None
        if rec.ended:
            self._schedule(r)

    def _schedule(self, r: int) -> None:
        rec = self._rounds[r]
        self._cache_key = None
        job = (self._gen, r, rec.version, dict(rec.obs), dict(rec.track))
        if self._q is None:
            self._run_job(job)
        else:
            with self._lock:
                self._computing += 1
            self._q.put(job)

    def _integrate(self, obs: Mapping, track: Mapping) -> dict[str, float] | None:
        path = merged_path(obs, track)
        if not path:
            return None
        ci = self.laws.ClosureIntegrator()
        for a, x, y, rr, inten in path:
            ci.add(a, x, y, rr, inten)
        w = ci.weighted()
        return {c: float(w.get(c, 0.0)) for c in CLOSURE_COMPANIES}

    def _run_job(self, job: tuple) -> None:
        gen, r, version, obs, track = job
        try:
            T = self._integrate(obs, track)
            err = None
        except Exception as e:  # noqa: BLE001 - a bad path must not kill the worker
            T, err = None, f"{type(e).__name__}: {e}"
        with self._lock:
            self.computations += 1
            if gen == self._gen:
                old = self._results.get(r)
                if old is None or old[1] <= version:
                    self._results[r] = (gen, version, T)
        if err is not None:
            self._safe_log("laws_error", round=r, error=err)
        else:
            self._safe_log("laws_closure", round=r, version=version, n_obs=len(obs), n_track=len(track), T=T)

    def _worker(self) -> None:
        assert self._q is not None
        while True:
            job = self._q.get()
            try:
                if job is None:
                    return
                self._run_job(job)
            finally:
                if job is not None:
                    with self._lock:
                        self._computing -= 1
                self._q.task_done()

    def _safe_log(self, kind: str, **fields: Any) -> None:
        try:
            self.log.event(kind, **fields)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ results
    def finals_complete(self) -> bool:
        """True once rounds 1-3 all have final reports."""
        return all(r in self._finals for r in ROUNDS)

    def _closure_T(self) -> dict[str, float] | None:
        """Season T per closure company, or None while any round is missing, uncomputed or being recomputed."""
        cov = self.coverage.coverage() if self.coverage is not None else {}
        if any(cov.get(r, "missing") == "missing" for r in ROUNDS):
            return None
        tot = {c: 0.0 for c in CLOSURE_COMPANIES}
        with self._lock:
            for r in ROUNDS:
                rec = self._rounds.get(r)
                res = self._results.get(r)
                if rec is None or res is None or res[0] != self._gen or res[1] != rec.version or res[2] is None:
                    return None
                for c in CLOSURE_COMPANIES:
                    tot[c] += res[2].get(c, 0.0)
        return tot

    def values(self) -> dict[str, float | None] | None:
        """None until rounds 1-3 all have final reports; then per symbol, None where its inputs are incomplete."""
        if not self.finals_complete():
            return None
        T = self._closure_T()
        with self._lock:
            res_key = tuple(sorted((r, v[1]) for r, v in self._results.items() if v[0] == self._gen))
        key = (tuple(sorted(self._finals)), T is None, res_key,
               tuple((r, rec.version) for r, rec in sorted(self._rounds.items())))
        if key == self._cache_key and self._cache_val is not None:
            return dict(self._cache_val)
        D = {c: sum(self._finals[r]["damage"].get(c, 0.0) for r in ROUNDS) for c in ISLAND_COMPANIES}
        L = sum(self._finals[r]["loss"] for r in ROUNDS)
        v = self.laws.values(D, L, T if T is not None else {})
        out: dict[str, float | None] = {}
        for s in SYMBOLS:
            x = v.get(s) if isinstance(v, dict) else None
            x = float(x) if isinstance(x, (int, float)) and math.isfinite(float(x)) else None
            out[s] = x if (s in DL_SYMBOLS or T is not None) else None
        self._cache_key, self._cache_val = key, out
        return dict(out)

    def compare(self, S: Mapping[str, float]) -> dict[str, tuple[float | None, bool | None]]:
        """sym -> (S - law, |S - law| <= laws_tol[sym]); (None, None) where the law value (or S) is unknown."""
        v = self.values()
        out: dict[str, tuple[float | None, bool | None]] = {}
        for s in SYMBOLS:
            law = v.get(s) if v is not None else None
            sv = S.get(s) if isinstance(S, Mapping) else None
            if law is None or not isinstance(sv, (int, float)) or not math.isfinite(float(sv)):
                out[s] = (None, None)
                continue
            d = float(sv) - law
            out[s] = (d, abs(d) <= self._tol[s] + 1e-12)
        return out

    def status(self) -> dict[str, Any]:
        """Small summary for the status line and logs."""
        with self._lock:
            done = {r: v[1] for r, v in self._results.items() if v[0] == self._gen}
        return {"finals": sorted(self._finals), "computed": done,
                "versions": {r: rec.version for r, rec in self._rounds.items()},
                "ended": sorted(r for r, rec in self._rounds.items() if rec.ended)}


# ============================================================================== live law check (rounds 1-2)
def _stage_key(stage: Any) -> str | None:
    if not isinstance(stage, str):
        return None
    s = stage.lower()
    if s.startswith("prelim"):
        return "prelim"
    if s == "final":
        return "final"
    return None


class LawCheck:
    """Rounds 1-2 live law check (DESIGN.md section 4.C); fails closed until a check ran or ``lawok all``."""

    def __init__(self, cfg: "Config", log: "EventLog") -> None:
        self.cfg = cfg
        self.log = log
        p = cfg.get("law_check", {}) or {}
        self.enabled = bool(p.get("enabled", True))
        self.horizon_s = float(p.get("horizon_s", 16.0))
        self.min_move_frac = float(p.get("min_move_frac", 0.005))
        self.fail_sign_n = int(p.get("fail_sign_n", 2))
        self.fail_size_frac = float(p.get("fail_size_frac", 0.5))
        self.slopes: dict[str, dict[str, list[float]]] = {}
        for stage, table in (p.get("slopes") or {}).items():
            if isinstance(table, dict):
                self.slopes[stage] = {s: [float(x) for x in v] for s, v in table.items()
                                      if isinstance(v, list) and all(isinstance(x, (int, float)) for x in v)}
        self.reset()

    def reset(self) -> None:
        """Forget every check (a new season)."""
        self._pending: list[dict[str, Any]] = []
        self._done = 0
        self._failures: set[str] = set()
        self._lawok_all = False
        self._history: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ inputs
    def on_report(self, round_: int, stage: str, seq: int | None, fv_before: "FairValueSnapshot | None",
                  books: Mapping[str, "BookView"], now: float) -> None:
        """A rounds 1-2 damage report arrived: record each stock's mid and the engine means before it."""
        if not self.enabled:
            return
        r = as_round(round_)
        st = _stage_key(stage)
        if r is None or r not in (1, 2) or st is None or st not in self.slopes:
            self._log("law_check_skip", round=round_, stage=stage, reason="round or stage not checked")
            return
        syms = list(self.slopes[st])
        mids = {}
        for s in syms:
            b = books.get(s)
            mid = getattr(b, "mid", None) if b is not None else None
            if isinstance(mid, (int, float)) and math.isfinite(mid) and mid > 0:
                mids[s] = float(mid)
        mu_before = None
        if fv_before is not None and getattr(fv_before, "ok", False):
            mu_before = {s: float(fv_before.values[s][0]) for s in syms if s in fv_before.values}
        self._pending.append({"round": r, "stage": st, "seq": seq, "t0": now, "mids": mids,
                              "mu_before": mu_before, "mu_after": None, "fv_version": None})
        self._log("law_check_start", round=r, stage=st, seq=seq, have_fv_before=mu_before is not None)

    def on_fv(self, fv: "FairValueSnapshot", now: float) -> None:
        """The first ok fair value with ``seq_hi >= seq`` gives the means after the report."""
        if not self._pending or fv is None or not getattr(fv, "ok", False):
            return
        for c in self._pending:
            if c["mu_after"] is not None or now > c["t0"] + self.horizon_s:
                continue
            seq = c["seq"]
            if seq is not None:
                if fv.seq_hi is None or fv.seq_hi < seq:
                    continue
            elif fv.computed_mono <= c["t0"]:
                continue
            c["mu_after"] = {s: float(fv.values[s][0]) for s in self.slopes[c["stage"]] if s in fv.values}
            c["fv_version"] = fv.version

    def on_tick(self, books: Mapping[str, "BookView"], now: float) -> None:
        """Evaluate every check whose horizon has passed."""
        if not self._pending:
            return
        keep = []
        for c in self._pending:
            if now < c["t0"] + self.horizon_s:
                keep.append(c)
                continue
            self._evaluate(c, books, now)
        self._pending = keep

    def _evaluate(self, c: dict[str, Any], books: Mapping[str, "BookView"], now: float) -> None:
        if c["mu_after"] is None or c["mu_before"] is None:
            reason = "no fv_after" if c["mu_after"] is None else "no fv_before"
            self._log("law_check", round=c["round"], stage=c["stage"], seq=c["seq"], void=True, reason=reason)
            return
        rows = []
        misses = []
        for s, slopes in self.slopes[c["stage"]].items():
            k = c["round"] - 1
            if k >= len(slopes) or s not in c["mids"] or s not in c["mu_before"] or s not in c["mu_after"]:
                continue
            b = books.get(s)
            mid1 = getattr(b, "mid", None) if b is not None else None
            if not isinstance(mid1, (int, float)) or not math.isfinite(mid1):
                continue
            mid0 = c["mids"][s]
            pred = slopes[k] * (c["mu_after"][s] - c["mu_before"][s])
            if abs(pred) <= self.min_move_frac * mid0:
                continue
            real = float(mid1) - mid0
            sign_miss = (real > 0) != (pred > 0) or real == 0.0
            size_miss = abs(real - pred) > self.fail_size_frac * abs(pred)
            miss = sign_miss or size_miss
            rows.append({"sym": s, "pred": pred, "real": real, "mid0": mid0, "mid1": float(mid1),
                         "mu_before": c["mu_before"][s], "mu_after": c["mu_after"][s], "slope": slopes[k],
                         "miss": miss, "sign_miss": sign_miss})
            if miss:
                misses.append(s)
        failed = len(misses) >= self.fail_sign_n
        self._done += 1
        if failed:
            self._failures.update(misses)
        rec = {"round": c["round"], "stage": c["stage"], "seq": c["seq"], "void": False, "failed": failed,
               "misses": misses, "checked": len(rows), "rows": rows, "fv_version": c["fv_version"]}
        self._history.append({k: v for k, v in rec.items() if k != "rows"})
        self._log("law_check", **rec)
        if failed:
            try:
                self.log.alert("law_check_failed", f"law check failed: round {c['round']} {c['stage']}, "
                                                   f"misses {', '.join(misses)}", symbols=misses)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ results
    def checks_done(self) -> int:
        """Completed (non-void) checks this season."""
        return self._done

    def failures(self) -> frozenset[str]:
        """Symbols missed by a failed check and not cleared by ``lawok``."""
        return frozenset(self._failures)

    def clear(self, sym: str) -> None:
        """``lawok SYM`` removes one symbol from the failures; ``lawok all`` clears them and the no-check state."""
        if not isinstance(sym, str):
            return
        if sym.lower() == "all":
            self._failures.clear()
            self._lawok_all = True
            self._log("lawok", symbol="all")
        else:
            self._failures.discard(sym.upper())
            self._log("lawok", symbol=sym.upper())

    def passed(self, sym: str) -> bool:
        """True iff (a check ran or ``lawok all`` was given) and ``sym`` is not in the failures."""
        return (self._done >= 1 or self._lawok_all) and sym not in self._failures

    def pending(self) -> int:
        """Checks waiting for their horizon."""
        return len(self._pending)

    def state_dict(self) -> dict:
        """Persisted part: completed checks, failures and the ``lawok all`` flag (pending checks are dropped)."""
        return {"checks_done": self._done, "failures": sorted(self._failures), "lawok_all": self._lawok_all,
                "history": list(self._history[-20:])}

    def load_state(self, d: dict) -> None:
        """Restore what ``state_dict`` saved; malformed fields are ignored."""
        if not isinstance(d, dict):
            return
        try:
            self._done = max(0, int(d.get("checks_done", 0)))
        except (TypeError, ValueError):
            self._done = 0
        f = d.get("failures") or []
        self._failures = {s for s in f if isinstance(s, str)}
        self._lawok_all = bool(d.get("lawok_all", False))
        h = d.get("history")
        self._history = list(h) if isinstance(h, list) else []
        self._pending = []

    def _log(self, kind: str, **fields: Any) -> None:
        try:
            self.log.event(kind, **fields)
        except Exception:  # noqa: BLE001
            pass
