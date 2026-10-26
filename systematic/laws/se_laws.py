"""CTC 2026 Systematic Equities: settlement laws, live closure integrator, riskless bounds and the round-3
final-report order list. Standard library only. Drop-in for the bot skeleton's ScenarioModel.value_fn.

All constants were fitted on research seasons 1-400 and reproduce settlement.json for all 600 seasons
(rmse <= 0.022 per stock; see v_settle.py). They are only valid if no announcement changed them.

Self-test:  python3 -I se_laws.py --selftest <path to hurricane-season-research>
"""
import json, math, sys

# ---- island businesses: value = V0 - A*D - KC*sum_s(w_s * closed_real_seconds_s) --------------------------
V0 = {"PORT": 100.0, "ISLE": 60.0, "AQUA": 35.0, "GRID": 50.0, "FERRY": 25.0}
A = {"PORT": 0.55, "ISLE": 0.38, "AQUA": 0.20, "GRID": 0.24, "FERRY": 0.05}       # $/share per damage pp
KC = {"PORT": 0.02, "ISLE": 0.0, "AQUA": 0.0, "GRID": 0.05, "FERRY": 0.06}        # $/share per weight-real-second
LOSS_PER_PP = {"PORT": 9.0, "ISLE": 6.0, "AQUA": 2.5, "GRID": 7.0, "FERRY": 1.2}  # $M insured loss per damage pp
SITES = {  # (x, y, weight) from case.json; only closure-sensitive companies are needed here
    "PORT": [(470, 80, 1.0)],
    "GRID": [(420, 110, 0.30), (560, 140, 0.20), (770, 285, 0.18), (825, 475, 0.17), (245, 510, 0.15)],
    "FERRY": [(380, 185, 0.30), (720, 300, 0.25), (760, 470, 0.20), (330, 480, 0.25)],
}
CLOSE_THR = 0.35  # site closed while intensity * exp(-(d/radius)^2) > 0.35
ARCH = {"PORT": 1, "ISLE": 1, "AQUA": 2, "GRID": 1, "FERRY": 2, "HAVEN": 1}
REBLD = {"RECON": 2, "MAT": 2, "GEN": 1}
LIMITS = {"PORT": 200, "ISLE": 300, "AQUA": 500, "GRID": 400, "FERRY": 800, "RECON": 500, "MAT": 700,
          "GEN": 600, "HAVEN": 250, "REIN": 200, "ARCH": 50, "REBLD": 100}


def values(D, L, T):
    """D: cumulative FINAL damage pp per island company (sum over storms); L: season insured loss $M
    (sum of final industry_loss_musd); T: season weighted closure real-seconds for PORT, GRID, FERRY."""
    v = {c: V0[c] - A[c] * D[c] - KC[c] * T.get(c, 0.0) for c in V0}
    v["RECON"] = 40 + 24 * (1 - math.exp(-(9 * D["PORT"] + 7 * D["GRID"] + 1.2 * D["FERRY"]) / 1000))
    v["MAT"] = 28 + 14 * (1 - math.exp(-L / 1200))
    v["GEN"] = 32 + 18 * (1 - math.exp(-T.get("GRID", 0.0) / 120))
    v["HAVEN"] = 75 - 0.06 * min(max(L - 400, 0.0), 900.0)
    v["REIN"] = 90 - 0.035 * max(L - 1300, 0.0)   # layer top unobserved (no research season above L = 1766)
    v["ARCH"] = sum(q * v[s] for s, q in ARCH.items())
    v["REBLD"] = sum(q * v[s] for s, q in REBLD.items())
    return v


def bounds(D, L, T):
    """Riskless bounds from what is already locked in: damage, loss and closure only accumulate.
    Returns {sym: ("max", value)} for symbols that can only fall and ("min", value) for those that can only rise."""
    v = values(D, L, T)
    out = {s: ("max", v[s]) for s in ("PORT", "ISLE", "AQUA", "GRID", "FERRY", "HAVEN", "REIN", "ARCH")}
    out.update({s: ("min", v[s]) for s in ("RECON", "MAT", "GEN", "REBLD")})
    out["HAVEN_floor"] = ("min", 21.0)
    return out


class ClosureIntegrator:
    """Feed it every storm observation (age, x, y, radius, intensity) of the CURRENT storm in order.
    Between consecutive observations it interpolates linearly and integrates closed time with the midpoint
    rule at dt = 0.1 s. A gap longer than 2 s is integrated the same way (fill it from the track message
    if you have one: call add() with the track points' x, y, radius and the bracketing intensities)."""

    def __init__(self, dt=0.1):
        self.dt = dt
        self.prev = None
        self.T = {c: [0.0] * len(s) for c, s in SITES.items()}

    def add(self, age, x, y, r, I):
        if self.prev is not None and age > self.prev[0]:
            a0, x0, y0, r0, i0 = self.prev
            n = max(1, int(round((age - a0) / self.dt)))
            h = (age - a0) / n
            for k in range(n):
                u = (k + 0.5) / n
                xx = x0 + u * (x - x0); yy = y0 + u * (y - y0); rr = r0 + u * (r - r0); ii = i0 + u * (I - i0)
                for c, sites in SITES.items():
                    for j, (sx, sy, _) in enumerate(sites):
                        d = math.hypot(sx - xx, sy - yy)
                        if d < 2 * rr and ii * math.exp(-(d / rr) ** 2) > CLOSE_THR:
                            self.T[c][j] += h
        if self.prev is None or age > self.prev[0]:
            self.prev = (age, x, y, r, I)

    def weighted(self):
        return {c: sum(t * w for t, (_, _, w) in zip(self.T[c], SITES[c])) for c in SITES}


def final_orders(S, pos, eps_bp=5.0, syms=None):
    """Round 3, after the final report: orders that can only fill at prices better than settlement.
    S: exact settlement values; pos: current positions. Buy up to +limit at S*(1-eps), sell down to -limit at
    S*(1+eps). Returns [(symbol, side, qty, limit_price)]. Exclude any symbol touched by an announcement."""
    out = []
    for s in (syms or LIMITS):
        lim, p = LIMITS[s], pos.get(s, 0)
        if lim - p > 0:
            out.append((s, "buy", lim - p, round(S[s] * (1 - eps_bp * 1e-4), 6)))
        if lim + p > 0:
            out.append((s, "sell", lim + p, round(S[s] * (1 + eps_bp * 1e-4), 6)))
    return out


def _selftest(root):
    man = json.load(open(root + "/history/manifest.json"))
    worst = {}
    for s in man["seasons"]:
        d = root + "/history/" + s["dir"]
        obs = {1: [], 2: [], 3: []}
        with open(d + "/feed.jsonl") as f:
            for line in f:
                if line.startswith('{"type":"storm",'):
                    m = json.loads(line)
                    obs[m["round"]].append((m["age"], m["x"], m["y"], m["radius"], m["intensity"]))
        D = {c: 0.0 for c in V0}; L = 0.0; T = {c: 0.0 for c in SITES}
        for stm in s["storms"]:
            ci = ClosureIntegrator()
            for o in obs[stm["round"]]:
                ci.add(*o)
            for c, t in ci.weighted().items():
                T[c] += t
            for c in D:
                D[c] += stm["damage_pct"][c]
            L += stm["industry_loss_musd"]
        v = values(D, L, T)
        S = json.load(open(d + "/settlement.json"))
        for k in S:
            worst[k] = max(worst.get(k, 0.0), abs(v[k] - S[k]))
    print("max |law - settlement| over", len(man["seasons"]), "seasons:", {k: round(e, 3) for k, e in worst.items()})


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--selftest":
        _selftest(sys.argv[2])
    else:
        print(__doc__)
