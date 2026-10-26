"""Holdout validation of the fair-value engine by replaying the raw research feeds message by message.

For each holdout season (default 401-600; analog pool / future-storm library = seasons 1-400 only) the feed.jsonl
messages are fed in seq order to a fresh SeasonState. At real seconds T in CHECKPOINTS of each round (all messages
with real time <= T processed; 570 is just after the preliminary report, 740 just after the final report) the
engine fair values are computed and compared with the season's settlement, next to the house consensus price at
the same market.csv row (row T/2 of the round).

Usage: python3 -I replay_validate.py <history_dir> [s0 s1] [nproc] [out_rows.json] [--stress] [--params=engine_params.json]
  --stress: also replay with simulated feed outages (bursts where only the last storm/track message survives) and a
            restart from the persisted message log at the open of round 2.
"""
import csv
import json
import math
import multiprocessing as mp
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fairvalue import SeasonState, fair_values, SYMBOLS  # noqa: E402

CHECKPOINTS = [0, 120, 240, 360, 480, 570, 600, 740, 770]
HIST = None
STRESS = False
ST = None


def real_second(tm: str) -> float:
    t = datetime.fromisoformat(tm)
    return (t - t.replace(hour=9, minute=30, second=0, microsecond=0)).total_seconds() / 30.0


def load_season(n):
    d = os.path.join(HIST, f"season-{n:03d}")
    with open(os.path.join(d, "feed.jsonl")) as f:
        feed = [json.loads(line) for line in f]
    house = {1: [], 2: [], 3: []}
    with open(os.path.join(d, "market.csv")) as f:
        for row in csv.DictReader(f):
            house[int(row["round"])].append({k: float(row[k]) for k in SYMBOLS})
    with open(os.path.join(d, "settlement.json")) as f:
        settle = json.load(f)
    return feed, house, settle


def outage(feed, seed):
    """Simulated exchange problems: 6 bursts of 5-40 storm observations per round; inside a burst only the last
    storm and the last track message survive (everything else in the burst is delivered in order). Plus 2% of
    messages duplicated (network retries)."""
    import random
    rng = random.Random(seed)
    out = []
    by_round = {}
    for m in feed:
        by_round.setdefault(m["round"], []).append(m)
    for r in sorted(by_round):
        msgs = by_round[r]
        ages = sorted({m["age"] for m in msgs if m["type"] == "storm"})
        drop_ages = set()
        for _ in range(6):
            L = rng.randint(5, 40)
            a0 = rng.choice(ages[:-L]) if len(ages) > L else ages[0]
            drop_ages.update(a for a in ages if a0 <= a < a0 + 2 * L)
        last_storm_in_burst = set()
        srt = sorted(drop_ages)
        for i, a in enumerate(srt):
            if i + 1 == len(srt) or srt[i + 1] != a + 2:
                last_storm_in_burst.add(a)
        for m in msgs:
            if m["type"] in ("storm", "track") and m.get("age") in drop_ages and m["age"] not in last_storm_in_burst:
                continue
            out.append(m)
            if rng.random() < 0.02:
                out.append(dict(m))
    return out


def run_season(n, mode="clean"):
    feed, house, settle = load_season(n)
    D_true = {co: 0.0 for co in ("PORT", "ISLE", "AQUA", "GRID", "FERRY")}; L_true = 0.0
    for m in feed:
        if m["type"] == "damage_report" and m["stage"] == "final":
            for co in D_true:
                D_true[co] += m["damage_pct"][co]
            L_true += m["industry_loss_musd"]
    if mode == "outage":
        feed = outage(feed, n)
    ST.reset()
    rows = []
    msgs_by_round = {}
    for m in feed:
        msgs_by_round.setdefault(m["round"], []).append((real_second(m["time"]), m))
    st = ST
    for r in (1, 2, 3):
        if mode == "restart" and r == 2:
            # bot restarts at the open of round 2: rebuild from the persisted message log (to_state)
            saved = json.loads(json.dumps(st.to_state()))
            st = SeasonState.from_state(saved, params=PARAMS, library_seasons=(1, 400))
        msgs = msgs_by_round.get(r, [])
        k = 0
        for T in CHECKPOINTS:
            while k < len(msgs) and msgs[k][0] <= T + 1e-9:
                st.on_message(msgs[k][1]); k += 1
            t0 = time.perf_counter()
            fv = fair_values(st)
            ms = 1000 * (time.perf_counter() - t0)
            h = house[r][min(len(house[r]) - 1, int(T // 2))]
            d = st.diag
            rows.append(dict(season=n, round=r, T=T, ms=ms, phase=d.get("phase"),
                             eng={s: [round(fv[s][0], 4), round(fv[s][1], 4)] for s in SYMBOLS},
                             house={s: h[s] for s in SYMBOLS}, settle=settle,
                             ED={k2: round(v, 3) for k2, v in d.get("E_damage_final", {}).items()},
                             EL=round(d.get("E_season_loss", 0.0), 2), D_true=D_true, L_true=L_true))
    return rows


PARAMS = None


def init(hist, stress, params=None):
    global HIST, ST, STRESS, PARAMS
    HIST = hist
    STRESS = stress
    PARAMS = params
    ST = SeasonState(params=params, library_seasons=(1, 400))


def work(args):
    n, mode = args
    return mode, run_season(n, mode)


def rmse(v):
    return math.sqrt(sum(x * x for x in v) / len(v)) if v else float("nan")


def tables(rows, title):
    print(f"\n===== {title}: n seasons = {len({r['season'] for r in rows})}")
    for r in (3, 2, 1):
        print(f"\n--- Round {r}: RMSE vs settlement, engine / house (real second of the round)")
        print("T    | " + " | ".join(f"{s:>13s}" for s in SYMBOLS))
        for T in CHECKPOINTS:
            sel = [x for x in rows if x["round"] == r and x["T"] == T]
            cells = []
            for s in SYMBOLS:
                e = rmse([x["eng"][s][0] - x["settle"][s] for x in sel])
                h = rmse([x["house"][s] - x["settle"][s] for x in sel])
                cells.append(f"{e:6.2f}/{h:6.2f}" if e >= 0.1 or h >= 0.1 else f"{e:6.3f}/{h:6.3f}")
            print(f"{T:4d} | " + " | ".join(f"{c:>13s}" for c in cells))
    # skill and a simple directional test (round 3): trade sign(engine - house) at the house price, mark at settlement
    print("\n--- Round 3: skill = 1 - MSE_engine/MSE_house, and mean $/share of sign(engine - house)*(settle - house) [t-stat]")
    for T in CHECKPOINTS:
        sel = [x for x in rows if x["round"] == 3 and x["T"] == T]
        cells = []
        for s in SYMBOLS:
            me = sum((x["eng"][s][0] - x["settle"][s]) ** 2 for x in sel); mh = sum((x["house"][s] - x["settle"][s]) ** 2 for x in sel)
            pn = [(1 if x["eng"][s][0] > x["house"][s] else -1 if x["eng"][s][0] < x["house"][s] else 0) * (x["settle"][s] - x["house"][s]) for x in sel]
            m = sum(pn) / len(pn); sdv = math.sqrt(sum((q - m) ** 2 for q in pn) / max(1, len(pn) - 1))
            t = m / (sdv / math.sqrt(len(pn))) if sdv > 0 else 0.0
            cells.append(f"{(1 - me / mh) if mh > 0 else 0:+.2f} {m:+.2f}[{t:+.1f}]")
        print(f"{T:4d} | " + " | ".join(f"{s}:{c}" for s, c in zip(SYMBOLS, cells)))
    # current-storm damage prediction (expected cumulative final damage vs actual), round 3
    print("\n--- Round 3: RMSE of the engine's E[cumulative final damage_pct] vs the final reports (pct points)")
    for T in CHECKPOINTS:
        sel = [x for x in rows if x["round"] == 3 and x["T"] == T and x["ED"]]
        if not sel:
            continue
        print(f"{T:4d} | " + " ".join(f"{co}:{rmse([x['ED'][co] - x['D_true'][co] for x in sel]):.2f}" for co in ("PORT", "ISLE", "AQUA", "GRID", "FERRY")) + f" | E[L] vs L: {rmse([x['EL'] - x['L_true'] for x in sel]):.1f}")
    # calibration
    print("\n--- Calibration of the engine sd (all rounds/checkpoints before the final report; z = (settle - mean)/sd)")
    print("sym    | mean z | sd z  | |z|<1.645 (90%) | |z|<1 (68%) | bias (settle-mean)")
    for s in SYMBOLS:
        zs, b = [], []
        for x in rows:
            if x["T"] >= 740 and x["round"] == 3:
                continue
            mu, sd = x["eng"][s]
            zs.append((x["settle"][s] - mu) / max(sd, 1e-9)); b.append(x["settle"][s] - mu)
        m = sum(zs) / len(zs); sdz = math.sqrt(sum((z - m) ** 2 for z in zs) / len(zs))
        c90 = sum(abs(z) < 1.645 for z in zs) / len(zs); c68 = sum(abs(z) < 1 for z in zs) / len(zs)
        print(f"{s:6s} | {m:+6.3f} | {sdz:5.3f} | {c90:15.3f} | {c68:11.3f} | {sum(b)/len(b):+.3f}")
    print("\n--- Bias (mean settle - value) per round at selected checkpoints: engine / house")
    for r in (1, 2, 3):
        for T in (0, 240, 480, 600):
            sel = [x for x in rows if x["round"] == r and x["T"] == T]
            print(f"r{r} T={T:3d} | " + " ".join(f"{s}:{sum(x['settle'][s]-x['eng'][s][0] for x in sel)/len(sel):+.2f}/{sum(x['settle'][s]-x['house'][s] for x in sel)/len(sel):+.2f}" for s in SYMBOLS))
    ms = sorted(x["ms"] for x in rows)
    print(f"\ncompute time per fair_values call: median {ms[len(ms)//2]:.0f} ms, p95 {ms[int(0.95*len(ms))]:.0f} ms, max {ms[-1]:.0f} ms")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    stress = "--stress" in sys.argv
    pfile = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--params=")), None)
    if pfile:
        pfile = os.path.abspath(pfile)
    hist = args[0]
    s0, s1 = (int(args[1]), int(args[2])) if len(args) > 2 else (401, 600)
    nproc = int(args[3]) if len(args) > 3 else 14
    outp = args[4] if len(args) > 4 else os.path.join(HERE, "validate_rows.json")
    modes = ["clean"] + (["outage", "restart"] if stress else [])
    jobs = [(n, mode) for mode in modes for n in range(s0, s1 + 1)]
    t0 = time.time()
    with mp.get_context("fork").Pool(nproc, initializer=init, initargs=(hist, stress, pfile)) as pool:
        res = pool.map(work, jobs, chunksize=1)
    allrows = {m: [] for m in modes}
    for mode, rows in res:
        allrows[mode].extend(rows)
    print(f"replayed {len(jobs)} season-runs in {time.time() - t0:.0f} s")
    with open(outp, "w") as f:
        json.dump(allrows, f, separators=(",", ":"))
    tables(allrows["clean"], f"HOLDOUT seasons {s0}-{s1}, clean feed")
    for mode in modes[1:]:
        cl = {(x["season"], x["round"], x["T"]): x for x in allrows["clean"]}
        print(f"\n===== stress mode '{mode}': round 3 RMSE vs settlement, stress / clean")
        for T in CHECKPOINTS:
            sel = [x for x in allrows[mode] if x["round"] == 3 and x["T"] == T]
            print(f"T={T:3d} | " + " ".join(f"{s}:{rmse([x['eng'][s][0]-x['settle'][s] for x in sel]):.3f}/{rmse([cl[(x['season'],3,T)]['eng'][s][0]-x['settle'][s] for x in sel]):.3f}" for s in SYMBOLS))
        print(f"\n===== stress mode '{mode}': RMSE of (stress - clean) engine value, round 3")
        for T in CHECKPOINTS:
            sel = [x for x in allrows[mode] if x["round"] == 3 and x["T"] == T]
            print(f"T={T:3d} | " + " ".join(f"{s}:{rmse([x['eng'][s][0]-cl[(x['season'],3,T)]['eng'][s][0] for x in sel]):.3f}" for s in SYMBOLS))
        for r in (1, 2):
            sel = [x for x in allrows[mode] if x["round"] == r and x["T"] == 770]
            print(f"round {r} T=770 | " + " ".join(f"{s}:{rmse([x['eng'][s][0]-cl[(x['season'],r,770)]['eng'][s][0] for x in sel]):.3f}" for s in SYMBOLS))
