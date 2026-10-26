"""Check on the RECORDED testnet books (season 225 rounds 1-3; s224 r3 is skipped because the recording starts
at 218 s): the Plan and REOPT replayed with decisions on the book touch mid (engine fair from the season-225
cache vs the book, as the live bot will trade) and fills by walking the recorded book `lat` s after the decision,
plus our own follow-on/impact from the r_sim kernel (the recording cannot contain our own orders).
The same configs are also run on the consensus (r_sim, measured costs) for comparison.  3 rounds: indicative only.
usage: python3 -I r11_book_replay.py <SP> <cost_model.json> <books_dir> <recording.jsonl> <history_dir>"""
import bisect, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import r_sim as R  # noqa: E402

SP, CMP, BOOKS, REC, HIST = sys.argv[1:6]
B, CM = R.init(SP, CMP)
sys.path.insert(0, BOOKS)
import books_lib as bl  # noqa: E402

D = bl.load(REC, HIST)
DATES = {d: v for d, v in D["rounds"].items() if v.get("season") == 225 and "open" in v}
BYR = {v["round"]: d for d, v in DATES.items()}
print("rounds:", {r: (d, round(DATES[d]["open"], 3)) for r, d in sorted(BYR.items())})


class Book:
    def __init__(self, lat=0.3, decide=True, fill=True):
        self.lat, self.decide, self.fill = lat, decide, fill
        self.fr = {}
        for i, s in enumerate(R.SYMS):
            for r, d in BYR.items():
                fs = [f for f in D["books"][s] if f.date == d]
                self.fr[(r, i)] = (fs, [f.t for f in fs])

    def has(self, r):
        return r in BYR

    def _frame(self, r, i, t):
        fs, ts = self.fr[(r, i)]
        j = bisect.bisect_right(ts, t) - 1
        if j < 0:
            return None
        f = fs[j]
        x = DATES[BYR[r]]["open"] + t
        sg = bl.seg_at(D["segs"], x)
        if sg is None or sg != f.seg or not f.bids or not f.asks:
            return None
        return f

    def mid(self, r, i, t):
        f = self._frame(r, i, t)
        return f.mid if f else None

    def walk(self, r, i, t, side, q):
        f = self._frame(r, i, t)
        if f is None:
            return None
        n, v, _ = bl.walk(f.asks if side > 0 else f.bids, q)
        return (n, v) if n else None


se = B.Season(SP, 225, "loso")
S3 = dict(s3_t0=80, k12=0.1, k3=0.2, clip_s3=1 / 12, flat12="auto")
H1 = dict(h1_size=1.0, h1_in=(42, 56), h1_out=None, clip_h1=0.125)
me = dict(cost="meas", excl=R.NOREIN, rate=150, cap=1 / 12, thr_m=2.0)
plan = dict(cost="meas", cap=0.25, excl=R.NOREIN)
cfgs = [("Plan", dict(plan, windows=R.plan_windows())),
        ("REOPT", dict(me, windows=R.plan_windows(**H1, **S3))),
        ("REOPT without H1", dict(me, windows=R.plan_windows(h1_size=0, **S3))),
        ("REOPT, H1 forced exit 72-86", dict(me, windows=R.plan_windows(**dict(H1, h1_out=(72, 86)), **dict(S3, s3_t0=88)))),
        ("H1 only (full, L/8, exit 80-94)", dict(me, windows=[w for w in R.plan_windows(**dict(H1, h1_out=(80, 94)), **S3) if w["name"] != "S3"]))]
print("| config | fills on | total | r1 | r2 | r3 | cost paid | acts | H1 entry trades | S3+S3x | per symbol |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
for name, c in cfgs:
    for label, extra in (("consensus (lat_rep 1)", dict(lat_rep=1)), ("recorded book, 0.3 s", dict(book=Book(0.3))),
                         ("recorded book, +2 s", dict(book=Book(0.3), lat=1)),
                         ("decide on consensus, fill on book", dict(book=Book(0.3, decide=False))),
                         ("decide on book, fill consensus + cost model", dict(book=Book(0.3, fill=False)))):
        x = R.sim(se, dict(c, **extra))
        comp = {}
        for k, v in x["by"].items():
            w = k.split("|")[0]
            comp[w] = comp.get(w, 0.0) + v[0]
        cp = sum(v[1] for v in x["by"].values())
        h1 = comp.get("H1", 0) + comp.get("H1x", 0)
        s3 = comp.get("S3", 0) + comp.get("S3x", 0)
        ps = ", ".join(f"{s} {v:,.0f}" for s, v in zip(R.SYMS, x["sym"]) if s != "REIN")
        print(f"| {name} | {label} | {x['pnl']:,.0f} | {x['round'][0]:,.0f} | {x['round'][1]:,.0f} | {x['round'][2]:,.0f} | "
              f"{cp:,.0f} | {x['act']} | {h1:,.0f} | {s3:,.0f} | {ps} |")
