"""Self-test of the live API on one holdout season: dedup, frame wrapper, snapshot late-join, manual adjustments,
parameter override, adjustment file, persistence/restore, timing. Usage: python3 -I selftest.py <history_dir> [season]"""
import json, os, sys, time, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
from fairvalue import SeasonState, fair_values, SYMBOLS
import replay_validate as RV
RV.HIST = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 else 401
feed, house, settle = RV.load_season(n)
cut = next(i for i, m in enumerate(feed) if m["round"] == 3 and m["type"] == "storm" and m["age"] == 200)
cut2 = next(i for i, m in enumerate(feed) if m["round"] == 3 and m["type"] == "storm" and m["age"] == 420)
def val(st): return {s: round(v[0], 4) for s, v in fair_values(st).items()}
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name, extra)
# reference
ref = SeasonState(library_seasons=(1, 400))
t0 = time.perf_counter()
for m in feed[:cut2 + 1]: ref.on_message(m)
t_msgs = time.perf_counter() - t0
v_ref = val(ref)
print(f"on_message: {1e6 * t_msgs / (cut2 + 1):.0f} us/message over {cut2 + 1} messages; fair_values {ref.diag['compute_ms']:.0f} ms")
# 1 duplicates + frame wrapper
st = SeasonState(library_seasons=(1, 400))
for i, m in enumerate(feed[:cut2 + 1]):
    st.on_message({"type": "news", "message": m} if i % 3 == 0 else m)
    if i % 7 == 0: st.on_message(dict(m))
check("duplicates ignored + frame wrapper", val(st) == v_ref)
# 2 snapshot late join at round 3 age 200 (synthetic snapshot built per the packet appendix)
pre = feed[:cut + 1]
last_obs = [m for m in pre if m["type"] == "storm" and m["round"] == 3][-1]
trk = [m for m in pre if m["type"] == "track" and m["round"] == 3][-1]["points"][-40:]
adv = [m for m in pre if m["type"] == "advisory" and m["round"] == 3 and m.get("source") == "official"][-1]
snap = {"type": "snapshot", "time": last_obs["time"], "phase": "storm", "last_seq": last_obs["seq"],
        "obs": {k: v for k, v in last_obs.items() if k not in ("type", "round", "storm")},
        "track": trk, "advisory": adv,
        "impacts": [{"island": m["island"], "age": m["age"], "time": m["time"]} for m in pre if m["type"] == "impact" and m["round"] == 3],
        "damage": [m for m in pre if m["type"] == "damage_report"], "headlines": []}
late = SeasonState(library_seasons=(1, 400))
late.on_message(snap)
for m in feed[cut + 1:cut2 + 1]: late.on_message(m)
v_late = val(late)
print("   late join (snapshot at r3 age 200, no saved state) vs full replay at r3 age 420; settlement:")
for s in SYMBOLS: print(f"     {s:6s} late {v_late[s]:9.3f}  full {v_ref[s]:9.3f}  settle {settle[s]:9.2f}")
check("snapshot late join runs", all(abs(v_late[s]) > 0 for s in SYMBOLS))
# 3 adjustments
st2 = SeasonState.from_state(ref.to_state(), library_seasons=(1, 400))
check("to_state/from_state reproduces values", val(st2) == v_ref)
st2.set_adjustment("PORT", 1.0, -2.0)
v2 = val(st2)
check("PORT add -2 -> PORT -2, ARCH -2", abs(v2["PORT"] - v_ref["PORT"] + 2) < 1e-6 and abs(v2["ARCH"] - v_ref["ARCH"] + 2) < 1e-6)
st2.set_adjustment("HAVEN", 0.9, 0.0); v3 = val(st2)
check("HAVEN mult 0.9", abs(v3["HAVEN"] - 0.9 * v_ref["HAVEN"]) < 1e-4)
st2.clear_adjustments(); check("clear adjustments", val(st2) == v_ref)
st2.override("settle.island.PORT.K", 0.04); v4 = val(st2)
print(f"   override PORT K 0.02 -> 0.04: PORT {v_ref['PORT']:.3f} -> {v4['PORT']:.3f}")
check("override changes PORT only among stocks", v4["PORT"] < v_ref["PORT"] and v4["ISLE"] == v_ref["ISLE"])
# 4 adjustment file
fd, path = tempfile.mkstemp(suffix=".json"); os.close(fd)
json.dump({"GRID": {"add": 1.5}, "_overrides": {}}, open(path, "w"))
st3 = SeasonState.from_state(ref.to_state(), library_seasons=(1, 400), adjust_file=path)
v5 = val(st3)
check("adjust file GRID +1.5", abs(v5["GRID"] - v_ref["GRID"] - 1.5) < 1e-6)
os.remove(path)
# 5 persistence
fd, log = tempfile.mkstemp(suffix=".jsonl"); os.close(fd); os.remove(log)
st4 = SeasonState(library_seasons=(1, 400), persist_path=log)
for m in feed[:cut2 + 1]: st4.on_message(m)
st5 = SeasonState.restore(log, library_seasons=(1, 400))
check("restore from persisted log", val(st5) == v_ref)
os.remove(log)
print("diag keys:", sorted(ref.diag))
print("current storm:", json.dumps(ref.diag.get("current_storm"), default=lambda x: round(x, 3))[:600])
print("ALL PASS" if ok else "SOME FAILED")
