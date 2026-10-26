"""Fair-value engine: pluggable models + precomputed lookup tables.

*** UNTESTED SKELETON. The value formulas are PLACEHOLDERS with the right shape; the research
    team replaces `value_fn` and the tables with fitted versions (fits/*.json). ***

Interface every model implements (strategies only ever see this):
    evaluate(state, now_real) -> {symbol: FairEstimate(mu, sigma, src)}
    settlement(state)         -> {symbol: value} once settlement is (near-)deterministic, else None

Speed budget: evaluate() runs after every feed batch (storm obs every 2 s, bursts at events) and
must take < 20 ms. Everything expensive is precomputed offline into JSON tables:
  * scenario library: one row per research storm (pattern, damage-to-date by 4-s age bucket,
    final damage, insured loss, outage hours) -> analog forecast of the current storm
  * layer tables: E[insurer payout(L_so_far + future losses)] on a grid of L for k = 0,1,2
    storms still to come -> exact-in-expectation insurer fair values with a 1-D lookup
  * advisory/wire confusion matrices by age bucket -> pattern posterior
ETF fair = basket of component fairs (both settle that way: packet section 3).
"""
import bisect
import json
import math

import config

INF = float("inf")


class FairEstimate:
    __slots__ = ("mu", "sigma", "src")

    def __init__(self, mu, sigma, src=""):
        self.mu, self.sigma, self.src = mu, sigma, src

    def __repr__(self):
        return f"Fair({self.mu:.3f}+-{self.sigma:.3f} {self.src})"


class Interp1D:
    """Piecewise-linear lookup, clamped at the ends (bisect, stdlib)."""

    def __init__(self, xs, ys):
        self.xs, self.ys = list(xs), list(ys)

    def __call__(self, x):
        xs, ys = self.xs, self.ys
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        i = bisect.bisect_right(xs, x)
        x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
        return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def layer_payout(loss, attach, exhaust):
    """What a layer [attach, exhaust] pays for a season total `loss` ($M)."""
    return min(max(loss - attach, 0.0), exhaust - attach)


def build_layer_tables(storm_losses, attach, exhaust, grid_max=4000.0, step=5.0, k_max=2, draws=4000, seed=7):
    """E[payout(L + X_k)] for k future storms, X_k = sum of k iid draws from the empirical
    per-storm loss list. Offline helper; result is saved to JSON and loaded at start-up."""
    import random
    rng = random.Random(seed)
    xs = [i * step for i in range(int(grid_max / step) + 1)]
    out = {}
    for k in range(k_max + 1):
        sums = [sum(rng.choice(storm_losses) for _ in range(k)) for _ in range(draws if k else 1)]
        ys = [sum(layer_payout(L + x, attach, exhaust) for x in sums) / len(sums) for L in xs]
        out[k] = {"xs": xs, "ys": ys}
    return out


class PatternPosterior:
    """P(pattern | advisories, wire). conf[source][age_bucket][true][said] from research data."""

    PATTERNS = ["direct", "chain", "recurve", "stall", "eyewall", "erratic"]

    def __init__(self, prior=None, conf=None, bucket_s=60):
        self.prior = prior or {p: 1.0 / len(self.PATTERNS) for p in self.PATTERNS}
        self.conf = conf or {}
        self.bucket_s = bucket_s
        self.reset()

    def reset(self):
        self.logp = {p: math.log(max(1e-9, self.prior[p])) for p in self.PATTERNS}
        self.used = set()

    def update(self, adv):
        key = (adv.get("source"), adv.get("seq"))
        if key in self.used:                # idempotent under duplicates / snapshot replays
            return
        self.used.add(key)
        src, said = adv.get("source"), adv.get("outlook")
        table = self.conf.get(src)
        if not table or said not in self.PATTERNS:
            return
        b = str(min(int((adv.get("age") or 0) // self.bucket_s), max(int(k) for k in table)))
        for p in self.PATTERNS:
            self.logp[p] += math.log(max(1e-6, table.get(b, {}).get(p, {}).get(said, 1.0 / 6)))

    def probs(self):
        m = max(self.logp.values())
        w = {p: math.exp(v - m) for p, v in self.logp.items()}
        z = sum(w.values())
        return {p: v / z for p, v in w.items()}


class FairValueModel:
    name = "base"

    def evaluate(self, state, now_real):
        raise NotImplementedError

    def settlement(self, state):
        return None


class HouseFollowModel(FairValueModel):
    """Safe default: fair = house mid with infinite uncertainty -> strategies never trade."""
    name = "house_follow"

    def evaluate(self, state, now_real):
        return {s: FairEstimate(b.mid(), INF, "house") for s, b in state.books.items() if b.mid() is not None}


def etf_fairs(fairs, baskets):
    out = {}
    for e, b in baskets.items():
        if all(c in fairs for c in b):
            mu = sum(w * fairs[c].mu for c, w in b.items())
            # conservative: perfectly correlated legs (sum of sigmas); refine with scenario covariances
            sd = sum(w * fairs[c].sigma for c, w in b.items())
            out[e] = FairEstimate(mu, sd, "basket")
    return out


def apply_overrides(fairs, overrides):
    for s, ov in (overrides or {}).items():
        f = fairs.get(s)
        if f is None:
            continue
        if ov.get("disabled"):
            f.sigma = INF
        if ov.get("fair_abs") is not None:
            f.mu, f.src = float(ov["fair_abs"]), f.src + "+abs"
        if ov.get("fair_mult") is not None:
            f.mu, f.src = f.mu * float(ov["fair_mult"]), f.src + "+mult"
        if ov.get("sigma_mult") is not None:
            f.sigma *= float(ov["sigma_mult"])
    return fairs


class ScenarioModel(FairValueModel):
    """Analog-scenario model. params JSON (from research) holds:
         value:     per-symbol coefficients for value_fn (PLACEHOLDER form below)
         layers:    {"HAVEN": {"attach":..,"exhaust":..,"per_musd":..,"base":..}, "REIN": {...}}
         layer_tables: {"HAVEN": {"0": {"xs":[..],"ys":[..]}, "1": .., "2": ..}, "REIN": ..}
         future:    mean damage (fraction) per island business for one unconditional storm
         prelim_shrink: E[final | prelim] = a + b*prelim per company (prelim is an estimate)
         sigma:     residual model sd per symbol, by phase
       scenarios: list of research-storm rows for the analog forecast of the live storm.
    """
    name = "scenario"

    def __init__(self, params, scenarios=None, posterior=None):
        self.p = params
        self.scen = scenarios or []
        self.post = posterior or PatternPosterior()
        self.tables = {}
        for ins, tab in (params.get("layer_tables") or {}).items():
            self.tables[ins] = {int(k): Interp1D(v["xs"], v["ys"]) for k, v in tab.items()}

    @classmethod
    def from_files(cls, params_path, scenarios_path=None):
        with open(params_path) as f:
            params = json.load(f)
        scen = None
        if scenarios_path:
            with open(scenarios_path) as f:
                scen = json.load(f)
        return cls(params, scen)

    # ---- the deterministic map (must reproduce settlement.json exactly; research owns it) ----
    def value_fn(self, cum_damage, season_loss, outage_h, layer_expect=None):
        """PLACEHOLDER SHAPES. cum_damage: fraction per island business; season_loss in $M;
        outage_h: dict of site-weighted closure hours (incl. 'GRID_hours' for GEN)."""
        v, p = {}, self.p.get("value", {})
        for s in ("PORT", "ISLE", "AQUA", "GRID", "FERRY"):
            c = p.get(s, {"v0": config.START_PRICES[s], "k": 1.0, "o": 0.0})
            v[s] = c["v0"] * (1.0 - c["k"] * cum_damage.get(s, 0.0)) - c.get("o", 0.0) * outage_h.get(s, 0.0)
        for s in ("RECON", "MAT", "GEN"):
            c = p.get(s, {"base": config.START_PRICES[s], "amp": 0.0, "cap": 1.0, "w": {}})
            work = sum(wt * cum_damage.get(src, 0.0) for src, wt in c.get("w", {}).items())
            if s == "GEN":
                work += c.get("w_out", 0.0) * outage_h.get("GRID_hours", 0.0)
            v[s] = c["base"] + c["amp"] * (1.0 - math.exp(-work / max(1e-9, c["cap"])))
        for s in ("HAVEN", "REIN"):
            c = (self.p.get("layers") or {}).get(s)
            if c is None:
                v[s] = config.START_PRICES[s]
                continue
            pay = layer_expect[s] if layer_expect and s in layer_expect else layer_payout(season_loss, c["attach"], c["exhaust"])
            v[s] = c["base"] - c["per_musd"] * pay
        for e, b in config.BASKETS.items():
            v[e] = sum(w * v[c] for c, w in b.items())
        return v

    def outages_from_track(self, storm):
        """TODO(research): closure hours per company from the observed wind field at each site."""
        return {}

    # ---- live evaluation ------------------------------------------------------------------
    def _expected_current_storm(self, state):
        """Remaining damage of the live storm: weighted analogs. TODO(research): weights from
        pattern posterior x track similarity at the current age; here: posterior-weighted means."""
        if not self.scen or state.storm.obs is None or state.storm.dissipated:
            return {}, 0.0
        probs = self.post.probs()
        age = state.storm.obs.get("age", 0)
        acc, wsum, loss = {}, 0.0, 0.0
        for row in self.scen:
            w = probs.get(row["pattern"], 0.0)
            if w <= 0:
                continue
            b = str(min(int(age // 4) * 4, 480))
            done = (row.get("dmg_by_age") or {}).get(b, {})
            for s, fin in row["final"].items():
                acc[s] = acc.get(s, 0.0) + w * max(0.0, fin - done.get(s, 0.0)) / 100.0
            loss += w * max(0.0, row["loss"] - (row.get("loss_by_age") or {}).get(b, 0.0))
            wsum += w
        if wsum <= 0:
            return {}, 0.0
        return {s: v / wsum for s, v in acc.items()}, loss / wsum

    def evaluate(self, state, now_real):
        cum = state.ledger.cumulative_damage(provisional=True)
        loss = state.ledger.season_loss(provisional=True)
        rnd = state.round or 1
        in_storm = state.storm.obs is not None and not any(r == rnd for r, _ in state.ledger.reports)
        cur, cur_loss = self._expected_current_storm(state) if in_storm else ({}, 0.0)
        storms_left = 3 - rnd
        fut = self.p.get("future", {})
        exp_dmg = {s: cum.get(s, 0.0) + cur.get(s, 0.0) + storms_left * fut.get(s, 0.0) for s in cum}
        layer_exp = {}
        for ins, tabs in self.tables.items():
            if storms_left in tabs:
                layer_exp[ins] = tabs[storms_left](loss + cur_loss)
        vals = self.value_fn(exp_dmg, loss + cur_loss, self.outages_from_track(state.storm), layer_exp)
        sig = (self.p.get("sigma") or {}).get(state.phase, {})
        out = {s: FairEstimate(vals[s], float(sig.get(s, 0.05 * vals[s])), self.name) for s in config.STOCKS if s in vals}
        out.update(etf_fairs(out, config.BASKETS))
        return apply_overrides(out, state.overrides)

    def settlement(self, state):
        """Round 3 after the FINAL report: deterministic settlement (if value_fn is exact)."""
        if state.round != 3 or (3, "final") not in state.ledger.reports:
            return None
        cum = state.ledger.cumulative_damage(provisional=False)
        loss = state.ledger.season_loss(provisional=False)
        vals = self.value_fn(cum, loss, self.outages_from_track(state.storm))
        off = {s for s, ov in (state.overrides or {}).items() if ov.get("disabled") or ov.get("needs_operator")}
        for e, bk in config.BASKETS.items():
            if off & set(bk):
                off.add(e)                      # an ETF is only as known as its components
        return {s: v for s, v in vals.items() if s not in off}
