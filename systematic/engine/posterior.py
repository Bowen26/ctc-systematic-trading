"""Live steering-pattern posterior for the Hurricane Season bot (stdlib only, self-contained).

Usage in a bot:
    from posterior import PatternPosterior
    pp = PatternPosterior("fits/sources.json")       # reads params["classifier"]
    for msg in feed:                                   # each news message dict, any type, in seq order
        pp.on_message(msg)
        if msg["type"] in ("storm", "advisory"):
            out = pp.posterior()                       # dict: p (6 classes), p_turned, p_erratic_by_end, ...

Replay check against the research data (read-only):
    python3 -I posterior.py fits/sources.json <history_dir> 401 600
"""
import json, math, sys
from pathlib import Path

CLASSES = ["direct", "chain", "recurve", "stall", "eyewall", "erratic"]
CI = {c: i for i, c in enumerate(CLASSES)}
NC = 6; NS = 11
STATE_CLASS = [0, 1, 2, 3, 4, 5, 5, 5, 5, 5, 5]   # 0-4 base not turned, 5-9 base turned erratic, 10 erratic from formation
DUR = 480


def _lse(v):
    m = max(v)
    if m == -math.inf: return m
    return m + math.log(sum(math.exp(x - m) for x in v))


def _chol(A):
    n = len(A); Lm = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = A[i][j] - sum(Lm[i][k] * Lm[j][k] for k in range(j))
            Lm[i][j] = math.sqrt(s) if i == j else s / Lm[j][j]
    return Lm


class _T:
    """multivariate Student-t, parameterised by covariance (scale = cov*(nu-2)/nu)"""
    def __init__(self, d, nu):
        self.mu = d["mu"]; k = len(self.mu); self.k = k; self.nu = nu
        self.L = _chol([[v * (nu - 2) / nu for v in row] for row in d["cov"]])
        logdet = 2 * sum(math.log(self.L[i][i]) for i in range(k))
        self.c = math.lgamma((nu + k) / 2) - math.lgamma(nu / 2) - k / 2 * math.log(nu * math.pi) - 0.5 * logdet

    def logpdf(self, x):
        z = [0.0] * self.k
        for i in range(self.k):
            z[i] = (x[i] - self.mu[i] - sum(self.L[i][j] * z[j] for j in range(i))) / self.L[i][i]
        return self.c - (self.nu + self.k) / 2 * math.log1p(sum(v * v for v in z) / self.nu)


class PatternPosterior:
    def __init__(self, params_path_or_dict):
        p = params_path_or_dict if isinstance(params_path_or_dict, dict) else json.load(open(params_path_or_dict))
        m = p.get("classifier", p)
        src = m["source_model"]
        self.prior_base = [src["prior_base"][c] for c in CLASSES]
        self.lam = src["hazard_per_s"]
        po = src["official_p_correct"]; pw = src["wire_p_correct"]
        self.loff = [[math.log(po) if i == j else math.log((1 - po) / 5) for j in range(NC)] for i in range(NC)]
        self.lwire = [[math.log(pw) if i == j else math.log((1 - pw) / 5) for j in range(NC)] for i in range(NC)]
        self.nu = m["nu"]; self.rho = m["rho"]; self.delta = m.get("delta", 0.0)
        self.weights = {int(t): (w["w_track"], w["w_cone"]) for t, w in m["weights"].items()}
        self.track = {}
        for t, d in m["track_checkpoints"].items():
            self.track[int(t)] = (d["feature_index"], [_T(d["class"][c], self.nu) for c in CLASSES])
        self.cone = {}
        for k, d in m["cone_by_number"].items():
            self.cone[int(k)] = [(_T(d[c]["label_correct"], self.nu), _T(d[c]["label_wrong"], self.nu)) for c in CLASSES]
        self.reset(None)

    # ------------------------------------------------------------------ state
    def reset(self, key):
        self.key = key
        self.obs = {}; self.trk = {}; self.seen = set()
        self.lf = [math.log(self.prior_base[b]) for b in range(5)] + [-math.inf] * 5 + [math.log(self.prior_base[5])]
        self.t_filter = 0; self.age = 0
        self.latest_adv = None; self.impacts = {}
        self.n_off = 0; self.n_wire = 0

    def _advanced(self, lf, t0, t):
        """copy of the 11-state log filter moved from age t0 to t under the erratic-turn hazard"""
        lf = lf[:]
        dt = t - t0
        if dt <= 0: return lf
        q = 1 - math.exp(-self.lam * dt); lq = math.log(q); l1q = math.log(1 - q)
        for b in range(5):
            if lf[b] == -math.inf: continue
            moved = lf[b] + lq; lf[b] += l1q
            lf[5 + b] = _lse([lf[5 + b], moved])
        return lf

    def _advance(self, t):
        if t > self.t_filter:
            self.lf = self._advanced(self.lf, self.t_filter, t); self.t_filter = t

    def on_message(self, m):
        seq = m.get("seq")
        key = (m.get("round"), m.get("storm"))
        if m.get("type") == "storm_formed" or (key != self.key and m.get("type") in ("storm", "track", "advisory")):
            if key != self.key: self.reset(key)
        if seq is not None:
            if seq in self.seen: return
            self.seen.add(seq)
        t = m.get("type")
        if t == "storm":
            a = int(m["age"]); self.obs[a] = (m["x"], m["y"], m["vx"], m["vy"], m["radius"], m["intensity"])
            self.age = max(self.age, a)
        elif t == "track":
            for pt in m["points"]:
                self.trk[int(pt[0])] = (pt[1], pt[2], pt[3])
            self.age = max(self.age, int(m["age"]))
        elif t == "advisory":
            a = int(m["age"]); o = CI.get(m["outlook"])
            if o is None: return
            self._advance(a)
            tab = self.loff if m.get("source") == "official" else self.lwire
            self.lf = [self.lf[s] + tab[STATE_CLASS[s]][o] if self.lf[s] > -math.inf else self.lf[s] for s in range(NS)]
            if m.get("source") == "official":
                self.latest_adv = m; self.n_off += 1
            else:
                self.n_wire += 1
            self.age = max(self.age, a)
        elif t == "impact":
            self.impacts.setdefault(m["island"], m["age"])

    # ------------------------------------------------------------------ features
    def _pt(self, a):
        """(x, y, vx, vy, r, i) at age a; falls back to nearest obs / track point (velocity, intensity from nearest obs)."""
        if a in self.obs: return self.obs[a]
        best = None
        for d in (2, 4, 6, 8):
            for b in (a - d, a + d):
                if b in self.obs and b <= self.age: best = self.obs[b]; break
            if best: break
        if a in self.trk:
            x, y, r = self.trk[a]
            if best: return (x, y, best[2], best[3], r, best[5])
            return (x, y, 0.0, 0.0, r, 1.0)
        return best

    def track_features(self, t):
        o0 = self._pt(0); ot = self._pt(t)
        te = min(t, 90); oe = self._pt(te)
        tr = max(0, t - 20); orr = self._pt(tr)
        tm = max(0, t - 60); om = self._pt(tm)
        if None in (o0, ot, oe, orr, om): return None
        sp = [math.hypot(self.obs[a][2], self.obs[a][3]) for a in range(tr, t + 1, 2) if a in self.obs]
        if not sp: sp = [math.hypot(ot[2], ot[3])]
        rs = [self.obs[a][4] for a in range(0, t + 1, 2) if a in self.obs] + [v[2] for a, v in self.trk.items() if a <= t]
        return [(oe[0] - o0[0]) / max(te, 1) * 30.0, (oe[1] - o0[1]) / max(te, 1) * 30.0,
                (ot[0] - orr[0]) / max(t - tr, 1) * 20.0, (ot[1] - orr[1]) / max(t - tr, 1) * 20.0,
                (orr[0] - om[0]) / max(tr - tm, 1) * 40.0 if tr > tm else 0.0,
                (orr[1] - om[1]) / max(tr - tm, 1) * 40.0 if tr > tm else 0.0,
                ot[0], ot[1], ot[4] - 80.0, max(rs) - 80.0, ot[5], sum(sp) / len(sp)]

    def _state_scores(self, v6):
        lr = math.log(self.rho); l1r = math.log(1 - self.rho)
        return v6[:5] + [_lse([l1r + v6[5], lr + v6[b]]) for b in range(5)] + [v6[5]]

    # ------------------------------------------------------------------ posterior
    def posterior(self, at=None):
        t = self.age if at is None else at
        lf = self._advanced(self.lf, self.t_filter, t)   # filter copy at age t (state itself untouched)
        v = lf[:]
        cp = (t // 10) * 10
        wt, wc = self.weights.get(cp, (0.0, 0.0)) if cp >= 10 else (0.0, 0.0)
        if cp >= 10 and wt > 0:
            f = self.track_features(cp)
            if f is not None:
                idx, g = self.track[cp]; x = [f[j] for j in idx]
                s = self._state_scores([g[c].logpdf(x) for c in range(NC)])
                v = [v[i] + wt * s[i] for i in range(NS)]
        a = self.latest_adv
        if a is not None and wc > 0 and a["age"] <= t:
            o = self._pt(int(a["age"]))
            if o is not None:
                xc = []
                for c in a["cone"]: xc += [c["x"] - o[0], c["y"] - o[1]]
                xc.append(math.log(a["cone"][0]["spread"]))
                g = self.cone[int(a["number"])]
                s = self._state_scores([g[c][0 if a["outlook"] == CLASSES[c] else 1].logpdf(xc) for c in range(NC)])
                v = [v[i] + wc * s[i] for i in range(NS)]
        z = _lse(v); ps = [math.exp(x - z) for x in v]
        p = [0.0] * NC
        for s in range(NS): p[STATE_CLASS[s]] += ps[s]
        if self.delta > 0:
            zp = _lse(lf); pp = [0.0] * NC
            for s in range(NS): pp[STATE_CLASS[s]] += math.exp(lf[s] - zp)
            p = [(1 - self.delta) * p[c] + self.delta * pp[c] for c in range(NC)]
        p_turn_later = 1 - math.exp(-self.lam * max(0, DUR - t))
        base = [ps[b] + ps[5 + b] for b in range(5)] + [ps[10]]
        return {"age": t, "p": dict(zip(CLASSES, p)), "p_turned_mid_storm": sum(ps[5:10]),
                "p_erratic_by_end": p[5] + (1 - p[5]) * p_turn_later, "p_turn_later_if_not_yet": p_turn_later,
                "p_base_pattern": dict(zip(CLASSES, base)), "map": CLASSES[max(range(NC), key=lambda c: p[c])]}


# ---------------------------------------------------------------------- replay check
def _replay(params, hist, s0, s1):
    hist = Path(hist)
    man = json.loads((hist / "manifest.json").read_text())
    pp = PatternPosterior(params)
    checks = list(range(10, 481, 10))
    stats = {t: [0.0, 0, 0] for t in checks}
    rows = []
    for s in man["seasons"]:
        if not (s0 <= s["season"] <= s1): continue
        storms = {x["round"]: x for x in s["storms"]}
        pending = {}
        def flush(rd, upto):
            st = storms[rd]
            while pending[rd] and pending[rd][0] <= upto:
                t = pending[rd].pop(0)
                out = pp.posterior(at=t)
                tru = "erratic" if st["turned_erratic_at"] is not None and t >= st["turned_erratic_at"] else st["pattern"]
                pt = max(out["p"][tru], 1e-6)
                stats[t][0] -= math.log(pt); stats[t][1] += out["map"] == tru; stats[t][2] += 1
                rows.append((s["season"], rd, t, [round(out["p"][c], 6) for c in CLASSES]))
        cur = None
        for line in (hist / s["dir"] / "feed.jsonl").open():
            m = json.loads(line)
            rd = m["round"]
            if rd != cur:
                if cur is not None: flush(cur, 10 ** 9)
                cur = rd; pending[rd] = checks[:]
            if "age" in m and m["type"] in ("storm", "track", "advisory", "impact"):
                flush(rd, m["age"] - 1)   # all messages with age < this age are in
            if m["type"] == "storm_dissipated":
                pp.on_message(m); flush(rd, 10 ** 9); continue
            pp.on_message(m)
        if cur is not None: flush(cur, 10 ** 9)
    return stats, rows


if __name__ == "__main__":
    params, hist, s0, s1 = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    stats, rows = _replay(params, hist, s0, s1)
    tot_ll = sum(v[0] for v in stats.values()); tot_n = sum(v[2] for v in stats.values())
    print("replay seasons", s0, "-", s1)
    for t in (10, 20, 30, 40, 60, 90, 120, 150, 180, 210, 240, 300, 360, 420, 470):
        v = stats[t]; print(f"  age {t:3d}: logloss {v[0] / v[2]:.4f} acc {v[1] / v[2]:.4f} n={v[2]}")
    print(f"  mean over 48 checkpoints: logloss {sum(v[0] / v[2] for v in stats.values()) / len(stats):.4f} "
          f"acc {sum(v[1] / v[2] for v in stats.values()) / len(stats):.4f}")
    if len(sys.argv) > 5:
        json.dump(rows, open(sys.argv[5], "w"))
