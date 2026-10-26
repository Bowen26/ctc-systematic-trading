"""Check: the snapshot fix changes nothing on a clean feed.

    python3 -I clean_feed_identity.py <research history dir> <engine dir> <recording.jsonl> <s0> <s1>

Feeds the same message stream to the original engine (engine/fairvalue_orig.py) and the patched one
(engine/fairvalue.py), one fresh SeasonState each, and compares them:
  * after every message: on_message's return value and the classifier state (key, label counts, the 11-state log
    filter, obs and track counts) and the official advisory numbers per round;
  * every 10 real s of each round (s = 0..770, after all messages with real time <= s): every symbol's fair value
    (mean, sd), compared with ==, and the pattern posterior.
Streams:
  research seasons s0..s1   feed.jsonl in seq order (no snapshots in the research feed)
  225+snapshots             research season 225 with the 24 testnet official advisories swapped in, and each
                            recorded season-225 snapshot placed right after the message its last_seq names
                            (what one clean connection delivers: the snapshot follows the messages it covers)
Negative control (excluded from the result): the 225+snapshots stream minus official advisory r1 #1, which then
reaches the engine only through the next snapshot. The two engines must differ there, or the harness is blind; each
engine's fair values on the control stream are also compared with the patched engine on the full stream.
Both parameter files are run: engine_params.json (library 1-400, so 401-405 are out of sample) and
engine_params_fast.json (library 226-600, the bot configuration of TESTNET_FINDINGS.md section 2).
"""
import importlib.util
import json
import math
import multiprocessing as mp
import os
import sys
import time
from datetime import datetime

CHECKS = list(range(0, 771, 10))
RUNS = [("engine_params.json", (1, 400)), ("engine_params_fast.json", (226, 600))]


def load_engine(eng, fname, modname):
    spec = importlib.util.spec_from_file_location(modname, os.path.join(eng, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def real_second(tm):
    t = datetime.fromisoformat(tm)
    return (t - t.replace(hour=9, minute=30, second=0, microsecond=0)).total_seconds() / 30.0


def research(hist, season):
    with open(os.path.join(hist, f"season-{season:03d}", "feed.jsonl")) as f:
        return [json.loads(line) for line in f]


def stream_225_with_snapshots(hist, rec_path):
    """[(round, real s, message)] for research 225 + testnet official advisories + recorded snapshots."""
    frames = [json.loads(line) for line in open(rec_path)]
    dates = {"1802-05-10", "1802-05-11", "1802-05-12"}
    news = [f["message"] for f in frames if f.get("type") == "news" and isinstance(f.get("message"), dict)
            and str(f["message"].get("time") or "")[:10] in dates]
    tn_off = {m["seq"]: m for m in news if m.get("type") == "advisory" and m.get("source") == "official"}
    snaps = [m for m in news if m.get("type") == "snapshot"]
    msgs = [tn_off.get(m["seq"], m) for m in sorted(research(hist, 225), key=lambda m: m["seq"])]
    after = {}
    for s in snaps:
        after.setdefault(s["last_seq"], []).append(s)
    out = [(1, -1.0, s) for s in after.pop(-1, [])]
    for m in msgs:
        out.append((m["round"], real_second(m["time"]), m))
        for s in after.pop(m["seq"], []):
            out.append((m["round"], real_second(m["time"]), s))
    assert not after, f"snapshots with unmatched last_seq: {sorted(after)}"
    return out, len(tn_off), len(snaps)


def fingerprint(st):
    p = st.post
    return (p.key, p.n_off, p.n_wire, tuple(p.lf), len(p.obs), len(p.trk), p.age, p.t_filter,
            tuple((r, tuple(sorted(rec.official)), len(rec.obs), len(rec.track), tuple(sorted(rec.impacts.items())))
                  for r, rec in sorted(st.rounds.items())),
            st.cur_round)


def compare(job):
    label, stream, eng, pname, lib = job
    E0 = load_engine(eng, "fairvalue_orig.py", "se_orig")
    E1 = load_engine(eng, "fairvalue.py", "se_patched")
    params = os.path.join(eng, pname)
    a = E0.SeasonState(params=params, library_seasons=lib)
    b = E1.SeasonState(params=params, library_seasons=lib)
    t0 = time.perf_counter()
    n_msg = n_msg_diff = n_ret_diff = 0
    n_eval = n_eval_diff = 0
    max_dmu = max_dsd = max_dp = 0.0
    first_diff = None
    i = 0
    for r in (1, 2, 3):
        for s in CHECKS:
            while i < len(stream) and (stream[i][0], stream[i][1]) <= (r, s):
                m = stream[i][2]
                ra, rb = a.on_message(m), b.on_message(m)
                n_msg += 1
                n_ret_diff += ra != rb
                if fingerprint(a) != fingerprint(b):
                    n_msg_diff += 1
                    first_diff = first_diff or ("msg", m.get("type"), m.get("seq"), m.get("last_seq"))
                i += 1
            fa, fb = E0.fair_values(a), E1.fair_values(b)
            pa, pb = a.pattern_posterior(), b.pattern_posterior()
            n_eval += 1
            same = fa == fb and pa == pb
            n_eval_diff += not same
            if not same and first_diff is None:
                first_diff = ("eval", r, s)
            for sym in fa:
                max_dmu = max(max_dmu, abs(fa[sym][0] - fb[sym][0]))
                max_dsd = max(max_dsd, abs(fa[sym][1] - fb[sym][1]))
            if pa and pb:
                max_dp = max(max_dp, max(abs(pa["p"][c] - pb["p"][c]) for c in pa["p"]))
    while i < len(stream):  # anything after 770 s (none expected)
        m = stream[i][2]
        n_ret_diff += a.on_message(m) != b.on_message(m)
        n_msg += 1
        i += 1
    return {"label": label, "params": pname, "lib": lib, "msgs": n_msg, "msg_state_diff": n_msg_diff,
            "return_diff": n_ret_diff, "evals": n_eval, "eval_diff": n_eval_diff, "max_dmu": max_dmu,
            "max_dsd": max_dsd, "max_dp": max_dp, "first_diff": first_diff, "secs": time.perf_counter() - t0}


def control_vs_clean(eng, clean, ctrl):
    """Fair values of each engine on the control stream against the patched engine on the full stream (fast params)."""
    E0 = load_engine(eng, "fairvalue_orig.py", "se_orig")
    E1 = load_engine(eng, "fairvalue.py", "se_patched")
    pname, lib = RUNS[1]

    def run(E, stream):
        st = E.SeasonState(params=os.path.join(eng, pname), library_seasons=lib)
        i, out = 0, {}
        for r in (1, 2, 3):
            for s in CHECKS:
                while i < len(stream) and (stream[i][0], stream[i][1]) <= (r, s):
                    st.on_message(stream[i][2])
                    i += 1
                out[(r, s)] = E.fair_values(st)
        return out

    ref = run(E1, clean)
    res = {}
    for name, E in (("original", E0), ("patched", E1)):
        X = run(E, ctrl)
        diff = [(abs(X[k][sym][0] - ref[k][sym][0]), k, sym, X[k][sym][0], ref[k][sym][0]) for k in ref for sym in ref[k]]
        res[name] = (sum(1 for k in ref if any(X[k][sym] != ref[k][sym] for sym in ref[k])), max(diff))
    return res, len(ref)


def main():
    hist, eng, rec_path, s0, s1 = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
    jobs = []
    s225, n_off, n_snap = stream_225_with_snapshots(hist, rec_path)
    print(f"225+snapshots stream: {len(s225)} messages ({n_off} testnet official advisories swapped in, {n_snap} snapshots)")
    for pname, lib in RUNS:
        for season in range(s0, s1 + 1):
            msgs = sorted(research(hist, season), key=lambda m: m["seq"])
            stream = [(m["round"], real_second(m["time"]), m) for m in msgs]
            jobs.append((f"season {season}", stream, eng, pname, lib))
        jobs.append(("225+snapshots", s225, eng, pname, lib))
    ctrl = [x for x in s225 if not (x[2].get("type") == "advisory" and x[2].get("source") == "official"
                                    and x[2].get("round") == 1 and x[2].get("number") == 1)]
    assert len(ctrl) == len(s225) - 1
    jobs.append(("control", ctrl, eng, RUNS[1][0], RUNS[1][1]))
    ctrl_stream = ctrl
    with mp.Pool(min(len(jobs), os.cpu_count() or 2)) as pool:
        res = pool.map(compare, jobs)
    print(f"\n{'stream':16s} {'params':24s} {'library':>9s} {'msgs':>5s} {'state!=':>7s} {'ret!=':>5s} "
          f"{'evals':>5s} {'fv!=':>5s} {'max|dmu|':>9s} {'max|dsd|':>9s} {'max|dp|':>8s} {'secs':>6s}")
    for x in res:
        print(f"{x['label']:16s} {x['params']:24s} {x['lib'][0]:>4d}-{x['lib'][1]:<4d} {x['msgs']:5d} {x['msg_state_diff']:7d} "
              f"{x['return_diff']:5d} {x['evals']:5d} {x['eval_diff']:5d} {x['max_dmu']:9.2e} {x['max_dsd']:9.2e} "
              f"{x['max_dp']:8.2e} {x['secs']:6.1f}" + (f"  first diff {x['first_diff']}" if x["first_diff"] else ""))
    ctrl = [x for x in res if x["label"] == "control"]
    res = [x for x in res if x["label"] != "control"]
    for x in ctrl:
        print(f"\nnegative control (225+snapshots minus official r1 #1, recovered from the next snapshot): {x['eval_diff']} of "
              f"{x['evals']} evaluations differ, max |dmu| {x['max_dmu']:.3f}, max |dp| {x['max_dp']:.3f} -> harness "
              + ("sees the fix" if x["eval_diff"] else "IS BLIND"))
    cvc, n_ref = control_vs_clean(eng, s225, ctrl_stream)
    for name, (n, (d, k, sym, v, ref)) in cvc.items():
        print(f"  {name} engine on the control stream vs the full stream ({RUNS[1][0]}): {n} of {n_ref} evaluations "
              f"differ; max |dmu| {d:.3f}" + (f" ({sym} at r{k[0]} s={k[1]}: {v:.2f} vs {ref:.2f})" if d else ""))
    tot_e = sum(x["evals"] for x in res)
    tot_d = sum(x["eval_diff"] for x in res)
    tot_m = sum(x["msg_state_diff"] + x["return_diff"] for x in res)
    research_rows = [x for x in res if x["label"].startswith("season")]
    print(f"\nresearch seasons {s0}-{s1}: {sum(x['evals'] for x in research_rows)} evaluations x 12 symbols, "
          f"{sum(x['eval_diff'] for x in research_rows)} differ")
    print(f"all streams: {tot_e} evaluations, {tot_d} with any fair value or posterior difference; "
          f"{tot_m} message-level state or return differences")
    ok = tot_d == 0 and tot_m == 0 and all(x["eval_diff"] for x in ctrl) and cvc["patched"][0] == 0
    print("RESULT:", "IDENTICAL" if tot_d == 0 and tot_m == 0 else "DIFFERENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
