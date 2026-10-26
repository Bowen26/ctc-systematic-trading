"""Exploration: snapshot cadence and contents; heartbeat cadence.

    python3 -I explore2.py <recording.jsonl>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import feedlib as FL  # noqa: E402

frames = FL.load_frames(sys.argv[1])
fits = FL.fit_round_opens(frames)
clock = FL.Clock({d: r["open_med"] for d, r in fits.items()})
conns = FL.connections(frames)
prev = None
for i, w, m in FL.news_frames(frames):
    if m.get("type") != "snapshot":
        continue
    k = max(c["k"] for c in conns if c["i0"] <= i)
    d, s, ph = clock.locate(w)
    obs = m.get("obs") or {}
    adv = m.get("advisory") or {}
    hl = m.get("headlines") or []
    print("conn%d %s dt=%s %s s=%.1f %s time=%s phase=%s last_seq=%s obs_age=%s trk=%d adv#=%s imp=%d dmg=%d hl=%d hlseq=%s..%s" % (
        k, FL.hms(w), "%.2f" % (w - prev) if prev else "-", d, s, ph, m.get("time"), m.get("phase"), m.get("last_seq"),
        obs.get("age"), len(m.get("track") or []), adv.get("number"), len(m.get("impacts") or []),
        len(m.get("damage") or []), len(hl), hl[0].get("seq") if hl else None, hl[-1].get("seq") if hl else None))
    prev = w
hb = [(i, f["received_at"], f["timestamp"]) for i, f in enumerate(frames) if f["type"] == "heartbeat"]
print("heartbeats", len(hb))
for c in conns:
    hs = [h for h in hb if c["i0"] <= h[0] < c["i1"]]
    if hs:
        print("conn", c["k"], "first hb %.2f s after ack" % (FL.iso_to_unix(hs[0][2]) - FL.iso_to_unix(frames[c["i0"]]["timestamp"])),
              "intervals", sorted(set(round(FL.iso_to_unix(b[2]) - FL.iso_to_unix(a[2]), 1) for a, b in zip(hs, hs[1:]))))
