"""Generate ``ctcbot/models/data/snapshot_probe.jsonl``, the engine snapshot-fix probe (DESIGN.md section 4.C).

usage (from anywhere; offline, standard library only):

    SYS/.venv/bin/python -I tools/make_snapshot_probe.py --history DIR --out PATH [--season 401] [--round 1]
        [--age 100] [--verify ENGINE_DIR]

The probe is research season ``--season`` round ``--round`` replayed up to storm age ``--age`` (every message of
that round in file order until the first one older than the cut), followed by one snapshot built from that state
in the testnet schema: ``obs`` is the latest storm observation without type, round, storm or seq; ``track`` is the
path thinned to every 8 s and ending at the obs; ``advisory`` is the latest official advisory **without storm,
seq, round or type**; then ``impacts``, ``damage`` (all reports so far) and the last 8 ``headlines``.

``snapshot_fix_present`` (``ctcbot.models.engine_adapter``) replays this file through fresh ``SeasonState``s and
checks that the snapshot is a no-op after the full stream, and that recovering the snapshot's advisory from the
snapshot (with and without the late live copy) reproduces the clean stream exactly. The unpatched engine resets
its pattern posterior on that recovery (TESTNET_FINDINGS section 1.3), so ``--verify`` must print True for
``fairvalue.py`` and False for ``fairvalue_orig.py``. Input files are data, never executed.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(BOT, "ctcbot", "models", "data", "snapshot_probe.jsonl")
THIN_S = 8


def _round(v):
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    if isinstance(v, str) and v.isdigit():
        return int(v)
    return None


def read_round(feed_path: str, rnd: int, max_age: float) -> list[dict]:
    """Messages of round ``rnd`` in file order, up to (and including) storm age ``max_age``."""
    out: list[dict] = []
    with open(feed_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if not isinstance(m, dict):
                continue
            r = _round(m.get("round"))
            if r is None:
                continue
            if r < rnd:
                continue
            if r > rnd:
                break
            age = m.get("age")
            if isinstance(age, (int, float)) and age > max_age:
                break
            out.append(m)
    if not out:
        raise SystemExit(f"no messages for round {rnd} in {feed_path}")
    return out


def build_snapshot(msgs: list[dict]) -> dict:
    """One testnet-schema snapshot describing the state after ``msgs``."""
    storms = [m for m in msgs if m.get("type") == "storm"]
    if not storms:
        raise SystemExit("the cut contains no storm observation")
    last = storms[-1]
    obs = {k: v for k, v in last.items() if k not in ("type", "round", "storm", "seq")}
    track_pts: dict[int, list] = {}
    for m in msgs:
        if m.get("type") == "track":
            for p in m.get("points") or []:
                track_pts[int(p[0])] = list(p)
    for m in storms:  # positions the track has not covered yet come from the observations themselves
        a = int(m["age"])
        track_pts.setdefault(a, [a, m["x"], m["y"], m["radius"]])
    last_age = int(last["age"])
    thinned = [track_pts[a] for a in sorted(track_pts) if a <= last_age and a % THIN_S == 0]
    end_pt = [last_age, last["x"], last["y"], last["radius"]]
    if not thinned or thinned[-1][0] != last_age:
        thinned.append(end_pt)
    officials = [m for m in msgs if m.get("type") == "advisory" and m.get("source") == "official"]
    if not officials:
        raise SystemExit("the cut contains no official advisory; choose a later --age")
    adv_src = max(officials, key=lambda m: int(m.get("number", 0)))
    advisory = {k: v for k, v in adv_src.items() if k not in ("type", "round", "storm", "seq")}
    impacts = [{"island": m["island"], "age": m["age"], "time": m["time"]} for m in msgs if m.get("type") == "impact"]
    damage = [{k: v for k, v in m.items() if k not in ("type", "seq", "headline")}
              for m in msgs if m.get("type") == "damage_report"]
    heads = []
    for m in msgs:
        if m.get("headline") and m.get("type") not in ("storm", "track"):
            h = {"seq": m.get("seq"), "type": m.get("type"), "time": m.get("time"), "round": m.get("round"),
                 "headline": m["headline"]}
            if m.get("source"):
                h["source"] = m["source"]
            heads.append(h)
    seqs = [m["seq"] for m in msgs if isinstance(m.get("seq"), int)]
    return {"type": "snapshot", "round": _round(last.get("round")), "storm": last.get("storm"),
            "time": last.get("time"), "phase": "storm", "last_seq": max(seqs) if seqs else -1, "obs": obs,
            "track": thinned, "advisory": advisory, "impacts": impacts, "damage": damage, "headlines": heads[-8:]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--history", required=True, help="research history directory (season-NNN/feed.jsonl)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="output JSONL (default: the committed probe file)")
    ap.add_argument("--season", type=int, default=401)
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--age", type=float, default=100.0)
    ap.add_argument("--verify", metavar="ENGINE_DIR", default=None,
                    help="after writing, run the probe on ENGINE_DIR/fairvalue.py and fairvalue_orig.py")
    a = ap.parse_args(argv)
    feed = os.path.join(a.history, f"season-{a.season:03d}", "feed.jsonl")
    if not os.path.isfile(feed):
        print(f"missing research feed: {feed}", file=sys.stderr)
        return 2
    msgs = read_round(feed, a.round, a.age)
    snap = build_snapshot(msgs)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    tmp = a.out + ".tmp"
    with open(tmp, "w") as f:
        for m in msgs + [snap]:
            f.write(json.dumps(m, separators=(",", ":")) + "\n")
    os.replace(tmp, a.out)
    print(f"wrote {a.out}: {len(msgs)} messages (season {a.season} round {a.round} to age {a.age:g}, "
          f"last_seq {snap['last_seq']}) + 1 snapshot (advisory #{snap['advisory'].get('number')})")
    if a.verify:
        if BOT not in sys.path:
            sys.path.insert(0, BOT)
        from ctcbot.models.engine_adapter import load_engine_module, snapshot_fix_present
        case_path = os.path.join(os.path.dirname(BOT), "data", "case.json")
        with open(case_path) as f:
            case = json.load(f)
        params = os.path.join(a.verify, "engine_params_fast.json")
        for fn in ("fairvalue.py", "fairvalue_orig.py"):
            if not os.path.isfile(os.path.join(a.verify, fn)):
                print(f"{fn}: not found")
                continue
            mod = load_engine_module(a.verify, fn)
            print(f"{fn}: snapshot fix present = {snapshot_fix_present(mod, a.out, case, params)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
