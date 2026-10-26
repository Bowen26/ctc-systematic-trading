"""Check what the engine does when a snapshot carrying an official advisory reaches it BEFORE the live advisory
message (possible with two merged connections: the recorded snapshot came only 19 ms after round-3 advisory #1).

    python3 -I doublecount_check.py <recording.jsonl> <research history dir> <engine dir> [engine file, default fairvalue.py]

Feeds research season 225 up to round 3 age 30 (s = 90), then:
  A   the live advisory #1 and the storm obs of the same tick (reference; single-connection order)
  A_snap_after  A followed by the snapshot (the order seen live on one connection)
  B   snapshot, then the live advisory                    engine as is
  B1  snapshot, then the live advisory                    snapshot advisory gets the storm name (fix 1 only)
  B2  snapshot, then the live advisory                    fix 1 + skip live official advisories already known (fix 2)
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import feedlib as FL  # noqa: E402

REC, HIST, ENG = sys.argv[1:4]
EFILE = sys.argv[4] if len(sys.argv) > 4 else "fairvalue.py"
spec = importlib.util.spec_from_file_location("se_engine", os.path.join(ENG, EFILE))
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)


class Fix1(E.SeasonState):
    def _on_snapshot(self, m):
        adv = m.get("advisory")
        if adv and "storm" not in adv:
            m = dict(m, advisory=dict(adv, storm=m.get("storm")))
        return super()._on_snapshot(m)


class Fix2(Fix1):
    def on_message(self, m):
        if isinstance(m, dict) and m.get("type") == "advisory" and m.get("source") == "official":
            rec = self.rounds.get(m.get("round"))
            if rec is not None and int(m.get("number", 0)) in rec.official and m.get("seq") is not None:
                self.seen.add(m["seq"])
                return False
        return super().on_message(m)


research = FL.research_feed(HIST, 225)
adv = next(m for m in research if m["type"] == "advisory" and m.get("source") == "official" and m["round"] == 3 and m["number"] == 1)
snap = next(f["message"] for f in FL.load_frames(REC) if f["type"] == "news" and f["message"].get("type") == "snapshot"
            and f["message"].get("last_seq") == adv["seq"] + 1)
pre = [m for m in research if m["seq"] < adv["seq"]]
live = dict(snap["advisory"], type="advisory", round=3, storm=adv["storm"], seq=adv["seq"])  # testnet content, live seq
obs30 = next(m for m in research if m["seq"] == adv["seq"] + 1)  # the storm obs of the same tick (age 30)
assert obs30["type"] == "storm" and obs30["age"] == 30


def run(cls, order):
    st = cls(params=os.path.join(ENG, "engine_params_fast.json"), library_seasons=(226, 600))
    for m in pre:
        st.on_message(m)
    n_obs_before = len(st.post.obs)
    for m in order:
        st.on_message(m)
    return st.pattern_posterior()["p"], E.fair_values(st), st.post.n_off, st.post.n_wire, n_obs_before, len(st.post.obs), len(st.post.trk)


res = {"A": run(E.SeasonState, [live, obs30]), "B": run(E.SeasonState, [snap, live, obs30]),
       "B1": run(Fix1, [snap, live, obs30]), "B2": run(Fix2, [snap, live, obs30]),
       "A_snap_after": run(E.SeasonState, [live, obs30, snap])}
for k, (p, fv, no, nw, ob0, ob1, tk) in res.items():
    print(f"{k:3s} classifier: official labels {no}, wire labels {nw}, obs {ob0} -> {ob1}, track pts {tk}; posterior "
          + ", ".join(f"{c} {v:.3f}" for c, v in p.items()))
pA, fA = res["A"][0], res["A"][1]
for k in ("B", "B1", "B2", "A_snap_after"):
    p, fv = res[k][0], res[k][1]
    print(f"{k:3s} vs A: max |dp| {max(abs(p[c] - pA[c]) for c in pA):.4f}; fair value diff: "
          + ", ".join(f"{s} {fv[s][0] - fA[s][0]:+.3f}" for s in fA))
