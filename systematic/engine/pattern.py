"""Pattern posterior for the live storm: the sources-area classifier (posterior.py, vendored unchanged from
work/se/sources) with the two verifier corrections from fits/sources.verified.json:
  1. _pt(a): when no storm obs is within +-8 s, interpolate between the bracketing track points (fixes the
     silent loss of the track score after a feed gap around age 82-98 or a late start);
  2. recent-turn floor: reserve eps = 1 - exp(-lambda*20 s) = 0.0027 for 'turned erratic, not yet visible'.
Parameters: fits/sources.verified.json (classifier block; fit on seasons 1-400)."""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from posterior import PatternPosterior, CLASSES  # noqa: E402


class PatchedPosterior(PatternPosterior):
    def __init__(self, params, floor_s=None):
        super().__init__(params)
        m = params.get("classifier", params)
        self.floor_s = m.get("recent_turn_floor_s", 0) if floor_s is None else floor_s

    def _pt(self, a):
        if a in self.obs:
            return self.obs[a]
        best = None
        for d in (2, 4, 6, 8):
            for b in (a - d, a + d):
                if b in self.obs and b <= self.age:
                    best = self.obs[b]; break
            if best:
                break
        if a in self.trk:
            x, y, r = self.trk[a]
            if best:
                return (x, y, best[2], best[3], r, best[5])
            return (x, y, 0.0, 0.0, r, 1.0)
        if best:
            return best
        lo = max((k for k in self.trk if k < a), default=None)
        hi = min((k for k in self.trk if k > a), default=None)
        if lo is None or hi is None:
            return None
        w = (a - lo) / (hi - lo); p0, p1 = self.trk[lo], self.trk[hi]
        vx = (p1[0] - p0[0]) / (hi - lo); vy = (p1[1] - p0[1]) / (hi - lo)
        return (p0[0] + w * (p1[0] - p0[0]), p0[1] + w * (p1[1] - p0[1]), vx, vy, p0[2] + w * (p1[2] - p0[2]), 1.0)

    def posterior(self, at=None):
        out = super().posterior(at)
        if self.floor_s and self.floor_s > 0:
            e = 1 - math.exp(-self.lam * self.floor_s); p = out["p"]
            q = {c: (v * (1 - e) if c != "erratic" else v + (1 - v) * e) for c, v in p.items()}
            out["p"] = q; out["map"] = max(q, key=q.get)
            out["p_erratic_by_end"] = q["erratic"] + (1 - q["erratic"]) * out["p_turn_later_if_not_yet"]
        return out
