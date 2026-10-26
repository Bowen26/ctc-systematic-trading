"""Red-team test of the ETF lead-lag claim on the house consensus (stdlib only).
Usage: python3 -I rt_etf.py rt_data.json
Rule (playbook 4.2): at row i (real 2i) gap = (NAV_i - ETF_i)/ETF_i with NAV = basket of the stock consensus at row i.
Enter toward +/-limit when |gap| > thr and |gap| > 3h; fill at ETF row i+1+lat at (1 +/- h). Exit at the first row m
where |gap_m| < 0.1%, or 4 rows (8 s) after entry, or the gap flips sign; exit fill at row m+1+lat.
Also: (1) the eta noise (ETF_i - NAV_{i-3}) autocorrelation; (2) how much of the gap comes from report jumps and the
formation jump vs ordinary 2-s moves; (3) seasons 1-400 vs 401-600."""
import json, math, sys
D = json.load(open(sys.argv[1])); SY = D["syms"]; SE = D["seasons"]
I = {s: k for k, s in enumerate(SY)}
W = {"ARCH": ({"PORT": 1, "ISLE": 1, "AQUA": 2, "GRID": 1, "FERRY": 2, "HAVEN": 1}, 10, 50),
     "REBLD": ({"RECON": 2, "MAT": 2, "GEN": 1}, 11, 100)}
def nav(row, w): return sum(row[I[s]] * q for s, q in w.items())

# (1) eta structure
for name, (w, e, L) in W.items():
    x = []; y = []
    for s in SE[:200]:
        for r in ("1", "2", "3"):
            R = s["rows"][r]
            eta = [R[i][e] - nav(R[i - 3], w) for i in range(3, 390)]
            x += eta[:-1]; y += eta[1:]
    mx = sum(x) / len(x); my = sum(y) / len(y)
    c = sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
    print(f"{name}: eta = ETF_i - NAV_(i-3): mean {mx:+.4f}, lag-1 autocorr {c:+.3f}")

def run(name, thr, h, lat, seasons, excl=None):
    w, e, L = W[name]
    pnl = []; ntr = 0; parts = {"report": 0.0, "formation": 0.0, "other": 0.0}
    for s in seasons:
        tot = 0.0
        for r in ("1", "2", "3"):
            R = s["rows"][r]
            nv = [nav(R[i], w) for i in range(390)]
            gap = [(nv[i] - R[i][e]) / R[i][e] for i in range(390)]
            i = 0
            while i < 389:
                g = gap[i]
                if abs(g) > thr and abs(g) > 3 * h and i + 1 + lat < 390:
                    if excl and excl(i):
                        i += 1; continue
                    q = L if g > 0 else -L
                    jin = i + 1 + lat
                    pin = R[jin][e] * (1 + h if q > 0 else 1 - h)
                    m = i + 1
                    while m < 389 and m - i < 4 and abs(gap[m]) >= 0.001 and gap[m] * g > 0:
                        m += 1
                    jout = min(m + 1 + lat, 389)
                    pout = R[jout][e] * (1 - h if q > 0 else 1 + h)
                    v = q * (pout - pin); tot += v; ntr += 1
                    key = "report" if (285 <= i <= 292 or 370 <= i <= 377) else ("formation" if 30 <= i <= 37 else "other")
                    parts[key] += v
                    i = max(jout, i + 1)
                else:
                    i += 1
        pnl.append(tot)
    n = len(pnl); m = sum(pnl) / n; sd = math.sqrt(sum((p - m) ** 2 for p in pnl) / (n - 1))
    return m, sd, m / (sd / math.sqrt(n)), ntr / n, {k: v / n for k, v in parts.items()}

HOLD = [s for s in SE if s["season"] > 400]; TRAIN = [s for s in SE if s["season"] <= 400]
print("\nETF single-leg lead-lag on consensus, $/season (holdout 401-600; train mean in brackets)")
print("| ETF | thr | h (bp) | lat (rows) | mean | sd | t | trades | of which report / formation / other | train mean |")
print("|---|---|---|---|---|---|---|---|---|---|")
for name in ("ARCH", "REBLD"):
    for thr in (0.003, 0.005, 0.01):
        for h in (0.0, 0.001, 0.0025, 0.004):
            for lat in (0, 1, 2):
                if lat and h not in (0.001, 0.0025):
                    continue
                m, sd, t, nt, pp = run(name, thr, h, lat, HOLD)
                mt = run(name, thr, h, lat, TRAIN)[0]
                print(f"| {name} | {thr * 100:.1f}% | {h * 1e4:.0f} | {lat} | {m:,.0f} | {sd:,.0f} | {t:.1f} | {nt:.0f} | "
                      f"{pp['report']:,.0f} / {pp['formation']:,.0f} / {pp['other']:,.0f} | {mt:,.0f} |", flush=True)
