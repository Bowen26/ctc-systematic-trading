"""Exploration: round-open fits, connections on the market clock, seq continuity per connection.

    python3 -I explore1.py <recording.jsonl>
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import feedlib as FL  # noqa: E402

frames = FL.load_frames(sys.argv[1])
fits = FL.fit_round_opens(frames)
for d, r in sorted(fits.items(), key=lambda kv: kv[1]["open_min"]):
    print(d, FL.SEASON_DATES.get(d), r["n"], FL.hms(r["open_min"]), "med-min %.3f sd %.3f p95 %.3f max %.3f" % (
        r["open_med"] - r["open_min"], r["jit_sd"], r["jit_p95"], r["jit_max"]))
fits_all = FL.fit_round_opens(frames, types=("storm", "track", "advisory", "impact", "damage_report", "storm_formed",
                                             "storm_dissipated", "snapshot"))
for d, r in sorted(fits_all.items(), key=lambda kv: kv[1]["open_min"]):
    print("ALL", d, r["n"], FL.hms(r["open_min"]), "med-min %.3f sd %.3f max %.3f" % (
        r["open_med"] - r["open_min"], r["jit_sd"], r["jit_max"]))
clock = FL.Clock({d: r["open_med"] for d, r in fits.items()})
for c in FL.connections(frames):
    sub = frames[c["i0"]:c["i1"]]
    cnt = collections.Counter(f["type"] for f in sub)
    ns = [f["message"] for f in sub if f["type"] == "news"]
    seqs = [m.get("seq") for m in ns if m.get("seq") is not None]
    print("conn", c["k"], FL.hms(c["start"]), "->", FL.hms(c["last"]), "dur %.1f" % (c["last"] - c["start"]),
          dict(cnt), "seq", seqs[:1], seqs[-1:], "n", len(seqs))
    for lab, w in (("start", c["start"]), ("end", c["last"])):
        d, s, ph = clock.locate(w)
        print("    ", lab, d, FL.SEASON_DATES.get(d), "s=%.1f" % s, FL.mkt_label(s), ph)
    # first/last news
    nw = [(f["received_at"], f["message"]) for f in sub if f["type"] == "news"]
    for w, m in nw[:3] + nw[-3:]:
        print("       news", FL.hms(w), m.get("type"), m.get("seq"), m.get("last_seq"), m.get("time"))
