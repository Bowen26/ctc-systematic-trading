"""Analog-track scenarios for the remainder of the live storm (standard library only).

At storm age a, for every steering class c with posterior p_c (from the pattern posterior), take the K library
storms whose TRUE class at age a is c (truth = erratic once turned) and whose state at age a (position, 30-s mean
velocity, radius) is nearest to the live storm. Each analog's FUTURE path (ages a+2..480) is moved onto the live
storm: position offset (x_now - x_j(a)) (optionally decaying with time constant tau), radius scaled by
r_now/r_j(a), intensity scaled by I_now/I_j(a). The translated path gives the future site hazards and the future
closure seconds of the live storm under that scenario. Analog weight = p_c / K (classes with p_c < p_min dropped).
Library storms that turn erratic after age a are naturally included at their historical rate.
"""
from __future__ import annotations

import math

import fvcore as F

CI = {c: i for i, c in enumerate(F.CLASSES)}


class AnalogLibrary:
    def __init__(self, geo: F.Geometry, storms: list, cfg: dict):
        self.geo = geo
        self.cfg = cfg
        self.storms = storms
        self.N = len(storms)
        thr = cfg.get("close_threshold", 0.35)
        self.thr = thr
        self.X, self.Y, self.R, self.I = [], [], [], []
        self.VX, self.VY, self.CL = [], [], []
        for st in storms:
            o = st["obs"]
            xs = [p[0] for p in o]; ys = [p[1] for p in o]
            self.X.append(xs); self.Y.append(ys)
            self.R.append([p[2] for p in o]); self.I.append([p[3] for p in o])
            vx, vy = [], []
            lag = max(1, int(round(cfg.get("vel_lag_s", 30) / 2.0)))
            for i in range(len(o)):
                if i == 0:
                    vx.append(0.0); vy.append(-1.8)
                else:
                    b = max(0, i - lag)
                    dt = 2.0 * (i - b)
                    vx.append((xs[i] - xs[b]) / dt); vy.append((ys[i] - ys[b]) / dt)
            self.VX.append(vx); self.VY.append(vy)
            te = st.get("turned")
            base = CI[st["pattern"]]
            self.CL.append([CI["erratic"] if (te is not None and 2 * i >= te) else base for i in range(len(o))])
        # per age index scales (sd over the library) for the distance
        self.scale = []
        for i in range(241):
            cols = []
            for arr in (self.X, self.Y, self.VX, self.VY, self.R, self.I):
                v = [a[i] for a in arr]
                mu = sum(v) / len(v)
                cols.append(max(1e-6, math.sqrt(sum((q - mu) ** 2 for q in v) / len(v))))
            self.scale.append(cols)
        # per class, per age index: candidate storm indices
        self.by_class = [[[] for _ in range(6)] for _ in range(241)]
        for j in range(self.N):
            for i in range(241):
                self.by_class[i][self.CL[j][i]].append(j)
        # suffix bounding boxes of the future path from index i (inclusive) for quick site rejection
        self.bbox = []
        for j in range(self.N):
            xs, ys, rs = self.X[j], self.Y[j], self.R[j]
            n = len(xs)
            bb = [None] * n
            mnx = mxx = xs[-1]; mny = mxy = ys[-1]; mxr = rs[-1]
            for i in range(n - 1, -1, -1):
                mnx = min(mnx, xs[i]); mxx = max(mxx, xs[i]); mny = min(mny, ys[i]); mxy = max(mxy, ys[i]); mxr = max(mxr, rs[i])
                bb[i] = (mnx, mxx, mny, mxy, mxr)
            self.bbox.append(bb)

    # ------------------------------------------------------------------ selection
    def select(self, state, pclass: dict, K: int = None, p_min: float = None, exclude=None, w_r: float = None, cone=None):
        """cone: latest official advisory (dict with 'cone' list of {age, x, y, spread}) or None. With
        cfg['cone_beta'] > 0, analogs are re-weighted within each class by exp(beta * loglik) of their translated
        positions at the cone ages still in the future (per-axis sd = spread / sqrt 2)."""
        cfg = self.cfg
        K = K or cfg.get("K", 20)
        beta = cfg.get("cone_beta", 0.0)
        p_min = cfg.get("p_min", 0.002) if p_min is None else p_min
        w_r = cfg.get("w_radius", 0.5) if w_r is None else w_r
        w_i = cfg.get("w_intensity", 0.0)
        a, x, y, vx, vy, r, I = state
        i = min(240, max(0, int(a) // 2))
        sc = self.scale[i]
        ps = {CI[c]: p for c, p in pclass.items() if p >= p_min}
        tot = sum(ps.values())
        out = []
        for c, p in ps.items():
            cand = []
            for j in self.by_class[i][c]:
                if exclude is not None and exclude(j):
                    continue
                d = (((self.X[j][i] - x) / sc[0]) ** 2 + ((self.Y[j][i] - y) / sc[1]) ** 2
                     + ((self.VX[j][i] - vx) / sc[2]) ** 2 + ((self.VY[j][i] - vy) / sc[3]) ** 2
                     + w_r * ((self.R[j][i] - r) / sc[4]) ** 2 + w_i * ((self.I[j][i] - I) / sc[5]) ** 2)
                cand.append((d, j))
            if not cand:
                continue
            cand.sort()
            sel = cand[:K]
            pts = [] if (not beta or cone is None) else [q for q in cone.get("cone", []) if q["age"] > a and q["age"] <= 480]
            if pts:
                ll = []
                for _, j in sel:
                    dx0 = x - self.X[j][i]; dy0 = y - self.Y[j][i]
                    v = 0.0
                    for q in pts:
                        k = min(240, int(q["age"]) // 2)
                        s2 = max(1.0, q["spread"]) ** 2 / 2.0
                        v -= 0.5 * ((self.X[j][k] + dx0 - q["x"]) ** 2 + (self.Y[j][k] + dy0 - q["y"]) ** 2) / s2
                    ll.append(beta * v)
                mx = max(ll)
                ws = [math.exp(v - mx) for v in ll]
                z = sum(ws)
                out.extend((j, p / tot * wv / z) for (_, j), wv in zip(sel, ws))
            else:
                w = p / tot / len(sel)
                out.extend((j, w) for _, j in sel)
        if not out:  # no class above p_min had candidates: fall back to nearest overall
            cand = []
            for j in range(self.N):
                if exclude is not None and exclude(j):
                    continue
                d = ((self.X[j][i] - x) / sc[0]) ** 2 + ((self.Y[j][i] - y) / sc[1]) ** 2
                cand.append((d, j))
            cand.sort()
            sel = cand[:K]
            out = [(j, 1.0 / len(sel)) for _, j in sel]
        return out

    # ------------------------------------------------------------------ future of one analog
    def future(self, j, state, tau: float = None):
        """Future site hazards (ages a+2..480, 2-s grid) and closure seconds (ticks a..479, linear crossing)
        of analog j moved onto the live state. Returns (Hf dict site->H, Tf dict site->seconds)."""
        a, x, y, vx, vy, r, I = state
        i0 = min(240, max(0, int(a) // 2))
        if i0 >= 240:
            return {}, {}
        tau = self.cfg.get("tau", 0.0) if tau is None else tau
        xs, ys, rs, Is = self.X[j], self.Y[j], self.R[j], self.I[j]
        dx0 = x - xs[i0]; dy0 = y - ys[i0]
        kr = r / rs[i0] if rs[i0] > 0 else 1.0
        kI = I / Is[i0] if Is[i0] > 1e-6 else 1.0
        bb = self.bbox[j][i0]
        geo = self.geo
        thr = self.thr
        cut = self.cfg.get("cutoff_q", 2.0)
        if tau and tau > 0:
            dec = [math.exp(-2.0 * (ii - i0) / tau) for ii in range(i0, 241)]
            mx = max(abs(dx0), abs(dy0))
        else:
            dec = None
            mx = 0.0
        rmax = bb[4] * kr
        reach = 2.5 * rmax
        Hf, Tf = {}, {}
        for s in range(geo.n):
            sx, sy = geo.sx[s], geo.sy[s]
            # bounding-box rejection (translated suffix box, widened by the decay range)
            if dec is None:
                if sx < bb[0] + dx0 - reach or sx > bb[1] + dx0 + reach or sy < bb[2] + dy0 - reach or sy > bb[3] + dy0 + reach:
                    continue
            else:
                if sx < bb[0] - abs(dx0) - reach or sx > bb[1] + abs(dx0) + reach or sy < bb[2] - abs(dy0) - reach or sy > bb[3] + abs(dy0) + reach:
                    continue
            h = 0.0
            tcl = 0.0
            prev = None
            for ii in range(i0, 241):
                if dec is None:
                    px = xs[ii] + dx0; py = ys[ii] + dy0
                else:
                    q = dec[ii - i0]
                    px = xs[ii] + dx0 * q; py = ys[ii] + dy0 * q
                pr = rs[ii] * kr
                pI = Is[ii] * kI
                if pr <= 0:
                    wv = 0.0; g = 0.0
                else:
                    d2 = (px - sx) ** 2 + (py - sy) ** 2
                    g = math.exp(-d2 / (pr * pr))
                    wv = pI * g
                    if ii > i0 and d2 < (cut * pr) ** 2:
                        h += 2.0 * wv
                e = wv - thr
                if prev is not None:
                    if prev > 0 and e > 0:
                        tcl += 2.0
                    elif prev > 0 or e > 0:
                        tcl += 2.0 * (max(prev, e) / (abs(prev) + abs(e)))
                prev = e
            if h > 0:
                Hf[s] = h
            if tcl > 0:
                Tf[s] = tcl
        return Hf, Tf
