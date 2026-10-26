"""Shared primitives for the Hurricane Season fair-value engine (standard library only).

Geometry, wind field, site hazards, closure seconds (tickprob_impact estimator), company multiplier
solve, preliminary-report posterior and the settlement map. Used by fairvalue.py (live engine),
build_library.py (offline storm library) and the tuning / validation scripts.

All formulas come from the verified fits in work/se/fits/*.verified.json:
  wind        w = I * exp(-(d/r)^2)                     (damage: zero beyond d = 2r)
  closure     site closed while w > 0.35  (1-s simulator ticks; odd ticks get P(w > thr) under an
              intensity random-walk bridge, sd 0.0294 per 2 s; impact conditioning per island)
  hazard      H_s = sum over 2-s observations of 2 * w_s
  damage      f_s = 1 - exp(-k_c * m * H_s) of the REMAINING site value, log m ~ N(0, sigma_c^2)
              D_c = 100 * sum_s w_s * rem_s * f_s
  settlement  island: V0 - a*D_c - K*sum_s(w_s*T_s); RECON/MAT/GEN saturating exponentials;
              HAVEN/REIN layers of the season insured loss L = sum_c lpp_c * D_c.
"""
from __future__ import annotations

import bisect
import math

COMPANIES = ("PORT", "ISLE", "AQUA", "GRID", "FERRY")
STOCKS = ("PORT", "ISLE", "AQUA", "GRID", "FERRY", "RECON", "MAT", "GEN", "HAVEN", "REIN")
SYMBOLS = STOCKS + ("ARCH", "REBLD")
CLASSES = ("direct", "chain", "recurve", "stall", "eyewall", "erratic")
CLOSURE_COS = ("PORT", "GRID", "FERRY")  # companies whose value depends on closure seconds (GEN uses GRID's)
SQRT2 = math.sqrt(2.0)


def phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / SQRT2))


class Geometry:
    """Sites from case.json, in case.json company order (PORT, ISLE, AQUA, GRID, FERRY)."""

    def __init__(self, case: dict):
        self.sites = []  # (co, name, island, x, y, w)
        for co in COMPANIES:
            for s in case["companies"][co]["sites"]:
                self.sites.append((co, s["name"], s["island"], float(s["x"]), float(s["y"]), float(s["weight"])))
        self.n = len(self.sites)
        self.sx = [s[3] for s in self.sites]
        self.sy = [s[4] for s in self.sites]
        self.sw = [s[5] for s in self.sites]
        self.sco = [COMPANIES.index(s[0]) for s in self.sites]
        self.by_co = [[j for j in range(self.n) if self.sco[j] == c] for c in range(5)]
        self.islands = sorted({s[2] for s in self.sites})
        self.by_island = {isl: [j for j in range(self.n) if self.sites[j][2] == isl] for isl in self.islands}
        self.etfs = {e: dict(v["basket"]) for e, v in case["etfs"].items()}


# ----------------------------------------------------------------------------- wind / hazard
def hazard_add(geo: Geometry, H: list, x: float, y: float, r: float, I: float, dt: float = 2.0, cut: float = 2.0):
    """H_s += dt * I * exp(-(d/r)^2) for sites with d < cut*r."""
    if r <= 0 or I <= 0:
        return
    rr = r * r
    lim = (cut * r) ** 2
    sx, sy = geo.sx, geo.sy
    for j in range(geo.n):
        dx = x - sx[j]
        d2 = dx * dx
        if d2 >= lim:
            continue
        dy = y - sy[j]
        d2 += dy * dy
        if d2 < lim:
            H[j] += dt * I * math.exp(-d2 / rr)


def wind_g(x, y, r, sx, sy):
    return math.exp(-((x - sx) ** 2 + (y - sy) ** 2) / (r * r))


# ----------------------------------------------------------------------------- storm record
class StormRecord:
    """Everything observed about one round's storm, with incremental hazard and closure estimates.

    obs: {age: (x, y, r, I)} from 'storm' messages (vx, vy kept separately), track: {age: (x, y, r)}.
    Interval caches: for consecutive observed ages a0 < a1 the hazard of grid ages a0+2..a1 and the closure
    probabilities of ticks a0..a1-1 are computed once (recomputed only if a track point lands in a gap).
    """

    def __init__(self, geo: Geometry, rnd: int, cfg: dict):
        self.geo = geo
        self.round = rnd
        self.thr = cfg["close_threshold"]
        self.var1 = cfg["intensity_sd_per_2s"] ** 2 / 2.0
        self.cut = cfg.get("cutoff_q", 2.0)
        self.name = None
        self.formed = False
        self.dissipated = False
        self.obs = {}
        self.vel = {}
        self.track = {}
        self.impacts = {}
        self.official = {}   # number -> advisory msg
        self.wire = []
        self.prelim = None   # {"damage": {...}, "loss": x}
        self.final = None
        self._ages = []
        self._iv = {}        # (a0, a1) -> (key, hazard list, {site: {tick: p}})
        self._first_h = None

    # -------------------------------------------------------------- ingest
    def add_obs(self, age, x, y, r, I, vx=None, vy=None):
        age = int(age)
        if age not in self.obs:
            bisect.insort(self._ages, age)
        self.obs[age] = (float(x), float(y), float(r), float(I))
        if vx is not None:
            self.vel[age] = (float(vx), float(vy))
        self.formed = True

    def add_track(self, points):
        for p in points:
            self.track[int(p[0])] = (float(p[1]), float(p[2]), float(p[3]))

    def last_age(self):
        return self._ages[-1] if self._ages else None

    # -------------------------------------------------------------- interval computation
    def _pos(self, t, a0, a1, p0, p1, tr_ages):
        if t in self.track:
            return self.track[t]
        lo_a, lo = a0, p0
        hi_a, hi = a1, p1
        j = bisect.bisect_left(tr_ages, t)
        if j > 0 and tr_ages[j - 1] > lo_a:
            lo_a = tr_ages[j - 1]; lo = self.track[lo_a]
        if j < len(tr_ages) and tr_ages[j] < hi_a:
            hi_a = tr_ages[j]; hi = self.track[hi_a]
        f = (t - lo_a) / (hi_a - lo_a)
        return (lo[0] + f * (hi[0] - lo[0]), lo[1] + f * (hi[1] - lo[1]), lo[2] + f * (hi[2] - lo[2]))

    def _interval(self, a0, a1, tr_ages):
        inside = tuple(a for a in tr_ages if a0 < a < a1) if a1 - a0 > 2 else ()
        key = inside
        c = self._iv.get((a0, a1))
        if c is not None and c[0] == key:
            return c
        geo = self.geo
        x0, y0, r0, I0 = self.obs[a0]
        x1, y1, r1, I1 = self.obs[a1]
        H = [0.0] * geo.n
        # hazard on the 2-s grid a0+2 .. a1 (a1 observed; interior positions from track / interpolation)
        for g in range(a0 + 2, a1 + 1, 2):
            if g == a1:
                x, y, r, I = x1, y1, r1, I1
            else:
                x, y, r = self._pos(g, a0, a1, (x0, y0, r0), (x1, y1, r1), tr_ages)
                I = I0 + (g - a0) / (a1 - a0) * (I1 - I0)
            hazard_add(geo, H, x, y, r, I, 2.0, self.cut)
        # closure ticks a0 .. a1-1
        probs = {}
        thr = self.thr
        for t in range(a0, a1):
            if t == a0:
                x, y, r, m, sd = x0, y0, r0, I0, 0.0
            else:
                x, y, r = self._pos(t, a0, a1, (x0, y0, r0), (x1, y1, r1), tr_ages)
                m = I0 + (t - a0) / (a1 - a0) * (I1 - I0)
                sd = math.sqrt(self.var1 * (t - a0) * (a1 - t) / (a1 - a0))
            if r <= 0:
                continue
            lim = (2.5 * r) ** 2
            for k in range(geo.n):
                dx = x - geo.sx[k]; dy = y - geo.sy[k]
                d2 = dx * dx + dy * dy
                if d2 > lim:
                    continue
                g = math.exp(-d2 / (r * r))
                if sd > 0:
                    pr = phi((m * g - thr) / (sd * g))
                else:
                    pr = 1.0 if m * g > thr else 0.0
                if pr > 1e-12:
                    probs.setdefault(k, {})[t] = pr
        c = (key, H, probs)
        self._iv[(a0, a1)] = c
        return c

    def _intervals(self):
        tr_ages = sorted(self.track)
        ages = self._ages
        return [(a0, a1, self._interval(a0, a1, tr_ages)) for a0, a1 in zip(ages, ages[1:])]

    # -------------------------------------------------------------- results
    def hazards(self):
        """H_s over all grid ages observed so far (first obs + every interval)."""
        geo = self.geo
        H = [0.0] * geo.n
        if not self._ages:
            return H
        a = self._ages[0]
        x, y, r, I = self.obs[a]
        hazard_add(geo, H, x, y, r, I, 2.0, self.cut)
        for _, _, (key, h, _) in self._intervals():
            for j in range(geo.n):
                if h[j]:
                    H[j] += h[j]
        return H

    def closure_seconds(self):
        """Per-site closure seconds over ticks first_obs .. last_obs-1, with impact conditioning:
        before an island's impact (or with no impact yet) its company sites were open; at the impact tick the
        most exposed site is closed."""
        geo = self.geo
        probs = [dict() for _ in range(geo.n)]
        for _, _, (key, h, pr) in self._intervals():
            for k, d in pr.items():
                probs[k].update(d)
        last = self._ages[-1] if self._ages else 0
        out = [0.0] * geo.n
        for isl, ks in geo.by_island.items():
            A = self.impacts.get(isl)
            for k in ks:
                if A is None:
                    continue
                out[k] = sum(v for t, v in probs[k].items() if t >= A)
            if A is not None and A < last:
                best = max(ks, key=lambda k: self._site_wind_at(k, A))
                out[best] += 1.0 - probs[best].get(A, 0.0)
        return out

    def _site_wind_at(self, k, t):
        ages = self._ages
        j = bisect.bisect_right(ages, t) - 1
        if j < 0:
            return -1.0
        a0 = ages[j]
        a1 = ages[j + 1] if j + 1 < len(ages) else a0
        x0, y0, r0, I0 = self.obs[a0]; x1, y1, r1, I1 = self.obs[a1]
        q = 0.0 if a1 == a0 else (t - a0) / (a1 - a0)
        x, y, r, I = x0 + q * (x1 - x0), y0 + q * (y1 - y0), r0 + q * (r1 - r0), I0 + q * (I1 - I0)
        return I * wind_g(x, y, r, self.geo.sx[k], self.geo.sy[k])

    def closed_now(self):
        a = self.last_age()
        if a is None:
            return [False] * self.geo.n
        x, y, r, I = self.obs[a]
        return [I * wind_g(x, y, r, self.geo.sx[k], self.geo.sy[k]) > self.thr for k in range(self.geo.n)]

    def state_vector(self, vel_lag_s=30):
        """(age, x, y, vx, vy, r, I) at the latest observation (analog-matching state); v = mean velocity over the
        last vel_lag_s seconds (from the start when the storm is younger)."""
        a = self.last_age()
        if a is None:
            return None
        x, y, r, I = self.obs[a]
        b = a - int(vel_lag_s)
        if a == 0:
            vx, vy = self.vel.get(0, (0.0, -1.8))
        else:
            if b < 0:
                b = self._ages[0]
            pb = self._pos_any(b)
            if pb is None or a == b:
                vx, vy = self.vel.get(a, (0.0, -1.8))
            else:
                vx, vy = (x - pb[0]) / (a - b), (y - pb[1]) / (a - b)
        return (a, x, y, vx, vy, r, I)

    def _pos_any(self, t):
        if t in self.obs:
            return self.obs[t][:3]
        if t in self.track:
            return self.track[t]
        ks = sorted(set(self.obs) | set(self.track))
        j = bisect.bisect_left(ks, t)
        if j == 0 or j >= len(ks):
            return None
        a0, a1 = ks[j - 1], ks[j]
        p0 = self.obs[a0][:3] if a0 in self.obs else self.track[a0]
        p1 = self.obs[a1][:3] if a1 in self.obs else self.track[a1]
        f = (t - a0) / (a1 - a0)
        return tuple(p0[i] + f * (p1[i] - p0[i]) for i in range(3))


# ----------------------------------------------------------------------------- damage model
def company_damage(geo, c, m, H, rem, k):
    """D_c (pct of original value) for multiplier m."""
    s = 0.0
    for j in geo.by_co[c]:
        if H[j] > 0 and rem[j] > 0:
            s += geo.sw[j] * rem[j] * (1.0 - math.exp(-k * m * H[j]))
    return 100.0 * s


def solve_logm(geo, c, y, H, rem, k):
    """log m reproducing the reported damage y (None if no hazard or y <= 0; 8 if saturated)."""
    idx = [j for j in geo.by_co[c] if H[j] > 0 and rem[j] > 0]
    if not idx or y <= 0:
        return None
    ymax = 100.0 * sum(geo.sw[j] * rem[j] for j in idx)
    if y >= ymax - 1e-9:
        return 8.0
    lo, hi = -15.0, 15.0
    for _ in range(70):
        mid = 0.5 * (lo + hi)
        if company_damage(geo, c, math.exp(mid), H, rem, k) < y:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def update_rem(geo, rem, H, final_dmg, kmap):
    """Per-site remaining value after a storm whose final report is known (exact multiplier solve)."""
    out = list(rem)
    for c, co in enumerate(COMPANIES):
        y = final_dmg.get(co, 0.0)
        u = solve_logm(geo, c, y, H, rem, kmap[co])
        m = 0.0 if u is None else math.exp(u)
        for j in geo.by_co[c]:
            if H[j] > 0:
                out[j] = rem[j] * math.exp(-kmap[co] * m * H[j])
    return out


def _gauss_hermite(n):
    """Probabilists' Gauss-Hermite nodes/weights (sum of weights = 1) via Golub-Welsch-free Newton iteration."""
    xs, ws = [], []
    # physicists' roots by Newton on the recurrence, then rescale
    for i in range(n):
        # initial guesses (Numerical Recipes gauher)
        if i == 0:
            z = math.sqrt(2 * n + 1) - 1.85575 * (2 * n + 1) ** (-0.16667)
        elif i == 1:
            z = z - 1.14 * n ** 0.426 / z
        elif i == 2:
            z = 1.86 * z - 0.86 * xs[0]
        elif i == 3:
            z = 1.91 * z - 0.91 * xs[1]
        else:
            z = 2.0 * z - xs[i - 2]
        for _ in range(100):
            p1, p2 = math.pi ** -0.25, 0.0
            for j in range(1, n + 1):
                p3, p2 = p2, p1
                p1 = z * math.sqrt(2.0 / j) * p2 - math.sqrt((j - 1.0) / j) * p3
            pp = math.sqrt(2.0 * n) * p2
            z1, z = z, z - p1 / pp
            if abs(z - z1) < 1e-14:
                break
        xs.append(z)
        ws.append(2.0 / (pp * pp))
    return [x * SQRT2 for x in xs], [w / math.sqrt(math.pi) for w in ws]


GH_X, GH_W = _gauss_hermite(20)


def expected_company_damage(geo, c, H, rem, k, sigma):
    if not any(H[j] > 0 for j in geo.by_co[c]):
        return 0.0
    return sum(w * company_damage(geo, c, math.exp(sigma * z), H, rem, k) for z, w in zip(GH_X, GH_W))


def prelim_posterior_grid(geo, c, H, rem, k, sigma, prelim_pct, log_sd, n=121, zmax=5.0):
    """Posterior over z (log m = sigma*z) given the preliminary report (rounded to 0.1).
    Returns (zs, cdf) for inverse-CDF sampling; None if the company has no hazard."""
    if not any(H[j] > 0 for j in geo.by_co[c]):
        return None
    zs = [-zmax + 2 * zmax * i / (n - 1) for i in range(n)]
    lw = []
    P = prelim_pct
    for z in zs:
        D = company_damage(geo, c, math.exp(sigma * z), H, rem, k)
        pri = -0.5 * z * z
        if D <= 1e-9:
            lik = 1.0 if P <= 0.05 else 1e-300
        else:
            hi = math.log(P + 0.05)
            lo = math.log(P - 0.05) if P > 0.05 else -1e300
            u_hi = (hi - math.log(D)) / log_sd
            u_lo = (lo - math.log(D)) / log_sd if lo > -1e299 else -1e9
            lik = max(phi(u_hi) - phi(u_lo), 1e-300)
        lw.append(pri + math.log(lik))
    mx = max(lw)
    wts = [math.exp(v - mx) for v in lw]
    tot = sum(wts)
    cdf, acc = [], 0.0
    for v in wts:
        acc += v / tot
        cdf.append(acc)
    return zs, cdf


def sample_grid(zs, cdf, u):
    j = bisect.bisect_left(cdf, u)
    if j <= 0:
        return zs[0]
    if j >= len(zs):
        return zs[-1]
    c0, c1 = cdf[j - 1], cdf[j]
    f = 0.0 if c1 <= c0 else (u - c0) / (c1 - c0)
    return zs[j - 1] + f * (zs[j] - zs[j - 1])


# ----------------------------------------------------------------------------- settlement map
def settle_values(P, D, Tw, L):
    """Settlement of the 10 stocks given cumulative damage D[5] (pct points), weighted closure seconds
    Tw[5] (sum_s w_s T_s per company; GEN uses GRID's) and season insured loss L ($M)."""
    isl = P["island"]
    out = {}
    for c, co in enumerate(COMPANIES):
        q = isl[co]
        out[co] = q["V0"] - q["a"] * D[c] - q["K"] * Tw[c]
    rb = P["rebuild"]
    q = rb["RECON"]
    Dr = sum(q["w"][co] * D[c] for c, co in enumerate(COMPANIES))
    out["RECON"] = q["a"] + q["b"] * (1.0 - math.exp(-Dr / q["c"]))
    q = rb["MAT"]
    out["MAT"] = q["a"] + q["b"] * (1.0 - math.exp(-L / q["c"]))
    q = rb["GEN"]
    out["GEN"] = q["a"] + q["b"] * (1.0 - math.exp(-Tw[3] / q["c"]))
    ins = P["insurance"]
    q = ins["HAVEN"]
    out["HAVEN"] = q["v0"] - q["slope"] * min(max(L - q["attach"], 0.0), q["top"] - q["attach"])
    q = ins["REIN"]
    top = q.get("top")
    pay = max(L - q["attach"], 0.0)
    if top is not None:
        pay = min(pay, top - q["attach"])
    out["REIN"] = q["v0"] - q["slope"] * pay
    return out
