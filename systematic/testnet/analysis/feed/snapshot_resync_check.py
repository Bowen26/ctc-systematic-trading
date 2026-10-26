"""Check: an official advisory lost in a disconnect and recovered from the next snapshot (as in drop 1, season 224
advisory #5).  What does the engine do with it?

    python3 -I snapshot_resync_check.py <recording.jsonl> <research history dir> <engine dir> [engine file, default fairvalue.py]

snapshot.advisory has no 'storm' (nor 'seq', 'round', 'type').  SeasonState._on_snapshot feeds it to the pattern
classifier as dict(adv, type="advisory", round=r) - without 'storm' - and PatternPosterior.on_message resets itself
whenever (round, storm) changes, so the classifier is wiped (all earlier official and wire labels, obs, track) and then
wiped again by the next live message that carries the storm name.

For each of the 24 official advisories of season 225:
  base  C stream: research feed with the testnet advisories (what a clean feed would give)
  V     C minus that advisory, plus the first recorded snapshot carrying it (at its received_at)   [engine as is]
  F     same as V with a patched engine: snapshot advisory gets the snapshot's storm name, and a live official
        advisory whose number is already known is not fed to the classifier again                  [proposed fix]
Evaluated every 10 s from the advisory to s = 540.
"""
import importlib.util
import os
import statistics as stx
import sys
import csv

sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import feedlib as FL  # noqa: E402

REC, HIST, ENG = sys.argv[1:4]
EFILE = sys.argv[4] if len(sys.argv) > 4 else "fairvalue.py"
spec = importlib.util.spec_from_file_location("se_engine", os.path.join(ENG, EFILE))
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)
PARAMS = os.path.join(ENG, "engine_params_fast.json")
H = 0.0025
KR = {1: 0.1, 2: 0.1, 3: 0.2}


class Fixed(E.SeasonState):
    def _on_snapshot(self, m):
        adv = m.get("advisory")
        if adv and "storm" not in adv:
            m = dict(m, advisory=dict(adv, storm=m.get("storm")))
        return super()._on_snapshot(m)

    def on_message(self, m):
        if isinstance(m, dict) and m.get("type") == "advisory" and m.get("source") == "official" and isinstance(m.get("round"), int):
            rec = self.rounds.get(m["round"])
            if rec is not None and int(m.get("number", 0)) in rec.official and m.get("seq") is not None:
                self.seen.add(m["seq"])
                return False
        return super().on_message(m)


frames = FL.load_frames(REC)
opens = {d: r["open_med"] for d, r in FL.fit_round_opens(frames).items()}
research = FL.research_feed(HIST, 225)
rec_news = [(f["received_at"], f["message"]) for f in frames if f["type"] == "news" and FL.season_of_msg(f["message"]) == 225]
tn_off = {m["seq"]: m for _, m in rec_news if m.get("type") == "advisory" and m.get("source") == "official"}
snaps = [(w, m) for w, m in rec_news if m.get("type") == "snapshot"]
rows = list(csv.DictReader(open(os.path.join(HIST, "season-225", "market.csv"))))
px = {r: [] for r in (1, 2, 3)}
for row in rows:
    px[int(row["round"])].append({s: float(row[s]) for s in E.SYMBOLS})
DATES = {1: "1802-05-10", 2: "1802-05-11", 3: "1802-05-12"}


def arr(m):
    return opens[m["time"][:10]] + FL.mkt_seconds(m["time"])


base_stream = sorted(((arr(m), tn_off.get(m["seq"], m)) for m in research), key=lambda x: (x[0], x[1]["seq"]))


def replay(cls, stream, evals):
    st = cls(params=PARAMS, library_seasons=(226, 600))
    i = 0
    out = {}
    for r, s in evals:
        thr = opens[DATES[r]] + s + 1.5
        while i < len(stream) and stream[i][0] <= thr:
            st.on_message(stream[i][1])
            i += 1
        fv = E.fair_values(st)
        pp = st.pattern_posterior()
        out[(r, s)] = ({k: v for k, v in fv.items()}, (pp or {}).get("p"), st.post.n_off, st.post.n_wire)
    return out


summary = []
print(f"{'adv':8s} {'snap lag':>8s} | {'V: max|dmu|':>12s} {'sym':5s} {'max/thr':>7s} {'evals>thr':>9s} {'max|dp|':>7s} {'n_off/n_wire after':>18s} | "
      f"{'F: max|dmu|':>11s} {'max/thr':>7s} {'max|dp|':>7s}")
for seq, adv in sorted(tn_off.items()):
    r = adv["round"]
    s0 = int(FL.mkt_seconds(adv["time"]))
    sn = next(((w, m) for w, m in snaps if (m.get("advisory") or {}).get("number") == adv["number"] and m.get("round") == r
               and w >= arr(adv)), None)
    if sn is None:
        continue
    evals = [(r, s) for r in (1, 2, 3) for s in range(60, 541, 10)]
    evals = [(rr, s) for rr, s in evals if (rr, s) >= (r, s0 - 10)]
    var_stream = sorted([x for x in base_stream if x[1].get("seq") != seq] + [sn], key=lambda x: x[0])
    # base evaluated on the same prefix (replay from the season start, evals from the advisory on)
    full = [(rr, s) for rr in (1, 2, 3) for s in range(60, 541, 10)]
    B = replay(E.SeasonState, base_stream, full)
    V = replay(E.SeasonState, var_stream, full)
    Fx = replay(Fixed, var_stream, full)
    win = [(r, s) for s in range(s0, 541, 10)]  # rest of this storm

    def stats(X):
        worst = (0.0, "", 0.0)
        n_over = 0
        dp = 0.0
        for k in win:
            over = False
            for sym in E.SYMBOLS:
                mu_b, sd_b = B[k][0][sym]
                d = abs(X[k][0][sym][0] - mu_b)
                thr = KR[r] * sd_b + H * px[r][k[1] // 2][sym]
                if d / thr > worst[2]:
                    worst = (d, sym, d / thr)
                over |= d > thr
            n_over += over
            if X[k][1] and B[k][1]:
                dp = max(dp, max(abs(X[k][1][c] - B[k][1][c]) for c in B[k][1]))
        return worst, n_over, dp

    (wv, nv, dpv), (wf, nf, dpf) = stats(V), stats(Fx)
    k_end = (r, 540)
    lag = sn[0] - arr(adv)
    tag = f"r{r}#{adv['number']}"
    print(f"{tag:8s} {lag:8.2f} | {wv[0]:12.3f} {wv[1]:5s} {wv[2]:7.2f} {nv:4d}/{len(win):<4d} {dpv:7.3f} "
          f"{str(V[k_end][2]) + '/' + str(V[k_end][3]) + ' vs ' + str(B[k_end][2]) + '/' + str(B[k_end][3]):>18s} | "
          f"{wf[0]:11.3f} {wf[2]:7.2f} {dpf:7.3f}", flush=True)
    summary.append((tag, wv, nv, len(win), dpv, wf, nf, dpf))
print()
print(f"engine as is: advisories where some symbol moved by more than k*sd + h*P: {sum(1 for x in summary if x[2] > 0)} of {len(summary)}; "
      f"evaluations over threshold {sum(x[2] for x in summary)} of {sum(x[3] for x in summary)}; max |dp| {max(x[4] for x in summary):.3f}; "
      f"median over advisories of max |dp| {stx.median([x[4] for x in summary]):.3f}")
print(f"patched:      advisories with any evaluation over threshold {sum(1 for x in summary if x[6] > 0)}; "
      f"evaluations over threshold {sum(x[6] for x in summary)}; max |dp| {max(x[7] for x in summary):.3f}")
