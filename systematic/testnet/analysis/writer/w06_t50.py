"""Summarise the formation half-move times printed in verify/v04_h1.out (standard library only).
usage: python3 -I w06_t50.py <v04_h1.out>"""
import sys, statistics as st
rows = []
for line in open(sys.argv[1]):
    p = line.split()
    if len(p) >= 12 and p[0] in ("r1", "r2", "r3") and "|" in p and "mid48" not in p:
        try:
            rows.append((p[0], p[1], float(p[-2]), float(p[-1])))
        except ValueError:
            pass
for grp, syms in (("stocks", None), ("ETFs", ("ARCH", "REBLD"))):
    sel = [r for r in rows if (r[1] in syms) == (syms is not None)] if syms else [r for r in rows if r[1] not in ("ARCH", "REBLD")]
    b = [r[2] for r in sel]
    c = [r[3] for r in sel]
    lag = [r[2] - r[3] for r in sel]
    print(f"{grp}: n={len(sel)} book t50 median {st.median(b):.1f} s (range {min(b):.1f}-{max(b):.1f}); consensus t50 median "
          f"{st.median(c):.1f} s; book minus consensus median {st.median(lag):+.1f} s")
