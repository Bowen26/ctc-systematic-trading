"""Independent re-check of numbers disputed between the two derivatives reports and the critique.
Stdlib only. r = 0, T on a 252-day basis, vega per vol point."""
import math

def N(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def n(x): return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

def bs(kind, S, K, days, vol):
    T = days / 252.0
    w = vol * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * w * w) / w
    d2 = d1 - w
    if kind == 'C':
        return S * N(d1) - K * N(d2)
    return K * N(-d2) - S * N(-d1)

def greeks(kind, S, K, days, vol):
    T = days / 252.0
    w = vol * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * w * w) / w
    delta = N(d1) if kind == 'C' else N(d1) - 1
    gamma = n(d1) / (S * w)
    vega = S * n(d1) * math.sqrt(T) / 100
    theta = -S * n(d1) * vol / (2 * math.sqrt(T)) / 252
    return delta, gamma, vega, theta

def iv(kind, price, S, K, days, lo=0.01, hi=5.0):
    for _ in range(200):
        m = 0.5 * (lo + hi)
        if bs(kind, S, K, days, m) < price: lo = m
        else: hi = m
    return 0.5 * (lo + hi)

H = 1.005
print("== 1. calculator match ==")
print("C100 S100 v100 T20: %.4f" % bs('C', 100, 100, 20, 1.0), "greeks %.4f %.4f %.4f %.4f" % greeks('C', 100, 100, 20, 1.0))
print("IV of 11.2: %.3f%%" % (100 * iv('C', 11.2, 100, 100, 20)))

print("\n== 2. screenshot quotes: fair+-1.00 rounded outward vs fair+-1.005 ==")
quotes = {('C', 60): (39.28, 41.29), ('C', 100): (10.09, 12.10), ('C', 140): (0.81, 2.82),
          ('P', 100): (10.09, 12.10), ('P', 140): (40.81, 42.82), ('P', 60): (None, 1.29)}
for (k, K), (b, a) in quotes.items():
    f = bs(k, 100, K, 20, 0.9902)
    b1 = math.floor((f - 1.0) * 100 + 1e-9) / 100; a1 = math.ceil((f + 1.0) * 100 - 1e-9) / 100
    print("%s%d fair@99.02%%=%.4f  model(+-1.00 out)= %s/%.2f   screen= %s/%.2f" %
          (k, K, f, "%.2f" % b1 if b1 > 0 else "-", a1, b, a))
for K in (100, 140):
    print("parity S_impl at K=%d: %.3f" % (K, K + sum(quotes[('C', K)]) / 2 - sum(quotes[('P', K)]) / 2))

print("\n== 3. key Window A edges (S0=100, v0=100, T=20) ==")
for dS, dv in [(10, 0), (-10, 0), (10, 20), (-10, 20), (5, -20), (2, 10)]:
    row = []
    for k, K in [('C', 60), ('C', 100), ('C', 140), ('P', 60), ('P', 100), ('P', 140)]:
        row.append("%s%d %+.2f" % (k, K, bs(k, 100 + dS, K, 20, 1 + dv / 100) - bs(k, 100, K, 20, 1.0)))
    print("dS %+d%% dv %+d: " % (dS, dv) + "  ".join(row))

print("\n== 4. execution readings, 60C on +10% flat vol, 3 levels x 200 ==")
e = bs('C', 110, 60, 20, 1.0) - bs('C', 100, 60, 20, 1.0)
lv = [H, H + 1, H + 2]
I = sum(200 * (e - x) for x in lv)
def III(limit): return sum(200 * (e - 0.5 * (limit + x)) for x in lv)
print("edge %.3f; reading I %.0f; III with limit fair-0.25 (%.3f): %.0f; III with ask+2 (%.3f): %.0f; III one order per level: %.0f" %
      (e, I, e - 0.25, III(e - 0.25), H + 2, III(H + 2), I))
print("III, two orders (200 @ L1, then 400 @ L3): %.0f" %
      (200 * (e - H) + 200 * (e - 0.5 * (H + 2 + H + 1)) + 200 * (e - (H + 2))))

print("\n== 5. S1 unknown: delta-neutral straddle at stale (vol flat) ==")
dc = greeks('C', 100, 100, 20, 1.0)[0]; dp = greeks('P', 100, 100, 20, 1.0)[0]
r = -dp / dc
for m in (5, 8, 10, 12, 15):
    out = []
    for s in (+1, -1):
        S1 = 100 * (1 + s * m / 100)
        ed = r * (bs('C', S1, 100, 20, 1) - bs('C', 100, 100, 20, 1)) + (bs('P', S1, 100, 20, 1) - bs('P', 100, 100, 20, 1))
        out.append(ed)
    print("|dS| %2d%%: up %.2f  down %.2f   cost %.3f  (ratio %.3f C per P)" % (m, out[0], out[1], (1 + r) * H, r))

print("\n== 6. E6 analyst example (stale 80, T = 18.333 d) ==")
T6 = 18 + 1 / 3
base = bs('C', 100, 100, T6, 0.80)
for lab, v in [("robust 94.17", 0.9417), ("A1 92", 0.92), ("A2 99", 0.99), ("A3 70", 0.70), ("mean 87", 0.87)]:
    g = bs('C', 100, 100, T6, v) - base
    print("%-13s gross/leg %+.3f  net L1 %+.3f" % (lab, g, (g - H) if g > 0 else (-g - H)))
nets = [bs('C', 100, 100, T6, v) - base - H for v in (0.92, 0.99, 0.70)]
p = (nets[0] + nets[1]) / 2 / ((nets[0] + nets[1]) / 2 - nets[2])
print("long-vol L1 net if right: A1 %+.3f A2 %+.3f A3 %+.3f ; equal-weight EV %+.3f ; breakeven P(A3) %.3f" %
      (nets[0], nets[1], nets[2], sum(nets) / 3, p))
for v in (0.80, 0.9417):
    d = greeks('C', 100, 100, T6, v)[0]
    print("delta-neutral 200-lot split at %.2f vol: %.1f C / %.1f P" % (v, 200 * (1 - d), 200 * d))

print("\n== 7. IV readout if IV tab uses a spot $1 off the bots' reference ==")
cm = bs('C', 100, 100, 20, 1.0); pm = bs('P', 100, 100, 20, 1.0)
for off in (1.0, -1.0, 0.11):
    ivc = 100 * iv('C', cm, 100 + off, 100, 20); ivp = 100 * iv('P', pm, 100 + off, 100, 20)
    dcx = greeks('C', 100 + off, 100, 20, 1.0)[0]
    w = (1 - dcx) * ivc + dcx * ivp
    print("offset %+.2f: IV call %.2f put %.2f  simple avg %.2f  delta-weighted (|dP|*C + dC*P) %.2f" % (off, ivc, ivp, (ivc + ivp) / 2, w))

print("\n== 8. fixed vs decaying T ==")
st = bs('C', 100, 100, 18, 1.0) + bs('P', 100, 100, 18, 1.0)
print("straddle@18d 100%% = %.3f ; 21.26 inverts to %.2f%% @18d, %.2f%% @20d" %
      (st, 100 * iv('C', 21.26 / 2, 100, 100, 18), 100 * iv('C', 21.26 / 2, 100, 100, 20)))
print("ATM 100C@100%%: T=20 %.3f  T=19.067(E3 snap) %.3f  T=19 %.3f  T=18 %.3f  T=17.05 %.3f" %
      tuple(bs('C', 100, 100, d, 1.0) for d in (20, 19.0667, 19, 18, 17.05)))
print("ATM theta per event: R1/R2 (1/3 day) %.3f, R3 (1/4 day) %.3f" %
      (greeks('C', 100, 100, 19.5, 1)[3] / 3, greeks('C', 100, 100, 17.5, 1)[3] / 4))

print("\n== 9. exact break-evens (half spread) ==")
def solve(f, lo, hi):
    flo = f(lo)
    for _ in range(100):
        m = 0.5 * (lo + hi)
        if (f(m) > 0) == (flo > 0): lo = m
        else: hi = m
    return 0.5 * (lo + hi)
for k, K in [('C', 60), ('P', 140), ('C', 100), ('P', 100)]:
    up = solve(lambda d: abs(bs(k, 100 + d, K, 20, 1) - bs(k, 100, K, 20, 1)) - H, 0.0001, 30)
    dn = solve(lambda d: abs(bs(k, 100 - d, K, 20, 1) - bs(k, 100, K, 20, 1)) - H, 0.0001, 30)
    print("%s%d |dS| up %.2f down %.2f" % (k, K, up, dn))
for K in (100, 140):
    up = solve(lambda d: bs('C', 100, K, 20, 1 + d / 100) - bs('C', 100, K, 20, 1) - H, 0.001, 80)
    dn = solve(lambda d: bs('C', 100, K, 20, 1) - bs('C', 100, K, 20, 1 - d / 100) - H, 0.001, 95)
    print("K%d vol up %.2f / down %.2f pts" % (K, up, dn))

print("\n== 10. realised-vol example R3 ==")
dt = (1 / 4) / 252
print("down 7%%: %.0f%%, up 7%%: %.0f%%" % (100 * abs(math.log(0.93)) / math.sqrt(dt), 100 * math.log(1.07) / math.sqrt(dt)))

print("\n== 11. time value at risk if round-end mark were intrinsic (S=100) ==")
for d in (19, 18, 17):
    print("T=%d: " % d + "  ".join("%s%d %.2f" % (k, K, bs(k, 100, K, d, 1.0) - max(0, (100 - K) if k == 'C' else (K - 100)))
                                     for k, K in [('C', 60), ('C', 100), ('C', 140)]))
