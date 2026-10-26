"""Hurricane Season fair-value engine (CTC 2026 Systematic Equities). Python standard library only.

    import sys; sys.path.insert(0, ".../work/se/engine")
    from fairvalue import SeasonState, fair_values
    st = SeasonState()                       # loads engine_params.json, storm library, pattern classifier
    for msg in feed: st.on_message(msg)      # raw news-channel messages (dedup by seq; snapshots ingested)
    fv = fair_values(st)                     # {symbol: (mean, sd)} for the 10 stocks and 2 ETFs
    st.set_adjustment("PORT", mult=1.0, add=-2.0)   # live announcement hook (human-set)
    st.diag                                  # expected damage / closure / loss, pattern posterior, ...

Model (all constants from work/se/fits/*.verified.json, fit on research seasons 1-400):
  * settlement map (exact to cents with exact inputs):
      island co: V0 - a*D_c - K*sum_s(w_s*T_s); D_c = season sum of final damage_pct; T_s closure seconds
      RECON = 40 + 24(1-exp(-(9P+7G+1.2F)/1000)); MAT = 28 + 14(1-exp(-L/1200)); GEN = 32 + 18(1-exp(-O/120)),
      O = sum over GRID substations of w*T; HAVEN = 75 - 0.06*clip(L-400, 0, 900); REIN = 90 - 0.035*max(L-1300, 0);
      L = season insured loss = sum_c lpp_c*D_c (lpp 9/6/2.5/7/1.2); ETF = basket.
  * physics (wind I*exp(-(d/r)^2), hazard H = sum 2*w, damage f = 1-exp(-k*m*H) of the REMAINING site value,
    log m ~ N(0, sigma_c), prelim = final*exp(N(0, 0.30^2)), closure while w > 0.35 with the tickprob_impact
    closure-second estimator).
  * current storm: locked-in hazard and closure so far (exact from the 2-s feed) + analog-track scenarios for the
    rest of the path (analogs selected per steering class, weighted by the pattern posterior of the sources model)
    + lognormal company multipliers (prior before the preliminary report, posterior after it, exact after the final).
  * future storms: the empirical per-storm library (every research storm of the library seasons, with its own
    per-site destroyed fractions and closure seconds) applied to the scenario's per-site remaining value, so
    depletion is handled at site level.
  Fair value = Monte Carlo expectation of the settlement map over these scenarios (common random numbers, so the
  value moves smoothly from call to call); sd = scenario dispersion (plus a small settlement-formula floor).
"""
from __future__ import annotations

import copy
import json
import math
import os
import random
import sys
import time
from statistics import NormalDist

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fvcore as F  # noqa: E402
from analog import AnalogLibrary  # noqa: E402
from pattern import PatchedPosterior  # noqa: E402

COMPANIES = F.COMPANIES
STOCKS = F.STOCKS
SYMBOLS = F.SYMBOLS
_ND = NormalDist()


def _resolve(path, base=HERE):
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(base, path))


def _deep_set(d, dotted, value):
    keys = dotted.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


class SeasonState:
    """Live state of one season, built from feed messages.

    Parameters
    ----------
    params: path or dict of engine_params.json (default: next to this file)
    library_seasons: (s0, s1) research seasons used as analog pool / future-storm library (default from params;
        the validation uses 1-400 so that the holdout 401-600 is out of sample; live use 1-600)
    persist_path: optional JSONL file; every accepted message is appended (restart with SeasonState.restore)
    """

    def __init__(self, params=None, library_seasons=None, persist_path=None, case=None, library=None, sources=None,
                 adjust_file=None):
        if params is None or isinstance(params, str):
            ppath = _resolve(params or "engine_params.json")
            with open(ppath) as f:
                P = json.load(f)
            base = os.path.dirname(ppath)
        else:
            P = copy.deepcopy(params)
            base = HERE
        self.P0 = P
        self.P = copy.deepcopy(P)
        if case is None:
            with open(_resolve(P["paths"]["case"], base)) as f:
                case = json.load(f)
        self.case = case
        self.geo = F.Geometry(case)
        if library is None:
            with open(_resolve(P["paths"]["library"], base)) as f:
                library = json.load(f)
        s0, s1 = library_seasons or P["library_seasons"]
        self.pool = [st for st in library["storms"] if s0 <= st["season"] <= s1]
        self.library_seasons = (s0, s1)
        if sources is None:
            with open(_resolve(P["paths"]["sources"], base)) as f:
                sources = json.load(f)
        self._sources = sources
        self.analogs = AnalogLibrary(self.geo, self.pool, dict(P["analog"], close_threshold=P["closure"]["close_threshold"],
                                                               cutoff_q=P["physics"]["cutoff_q"]))
        self._prep_future_library()
        self.persist_path = persist_path
        self.adjust_file = adjust_file   # optional JSON a human edits live: {"PORT": {"mult": 1, "add": -2}, "_overrides": {...}}
        self._adjust_mtime = None
        self.adjust = {}       # symbol -> [mult, add]
        self.overrides = {}    # dotted param path -> value
        self.announcements = []
        self.diag = {}
        self.reset()

    # ------------------------------------------------------------------ setup
    def _prep_future_library(self):
        geo = self.geo
        self.fut = []
        for st in self.pool:
            sp = [(j, geo.sco[j], st["f"][j]) for j in range(geo.n) if st["f"][j] > 0]
            tw = [0.0] * 5
            for j in range(geo.n):
                tw[geo.sco[j]] += geo.sw[j] * st["T"][j]
            self.fut.append((sp, tw))

    def reset(self):
        self.rounds = {}
        self.seen = set()
        self.cur_round = 0
        self.version = 0
        self.log = []
        self.post = PatchedPosterior(self._sources)
        self._post_key = None
        self._cache = None
        self._rcache = {}
        self._rng_designs = {}

    def _rec(self, r):
        rec = self.rounds.get(r)
        if rec is None:
            rec = F.StormRecord(self.geo, r, dict(self.P["closure"], cutoff_q=self.P["physics"]["cutoff_q"]))
            self.rounds[r] = rec
        return rec

    # ------------------------------------------------------------------ feed
    def on_message(self, m: dict) -> bool:
        """Ingest one news-channel message (the inner 'message' dict, or the full frame). Returns True if new."""
        if isinstance(m, dict) and m.get("type") == "news" and isinstance(m.get("message"), dict):
            m = m["message"]
        t = m.get("type")
        seq = m.get("seq")
        if t != "snapshot" and seq is not None:
            if seq in self.seen:
                return False
            self.seen.add(seq)
        if t == "advisory" and m.get("source") == "official" and self._official_known(m):
            return False
        if t == "snapshot":
            changed = self._on_snapshot(m)
            if changed:
                self._bump(m)
            return changed
        r = m.get("round")
        if isinstance(r, str) and r.isdigit():
            r = int(r)
        if t in ("storm", "track", "storm_formed", "storm_dissipated", "impact", "advisory", "damage_report"):
            if not isinstance(r, int):
                return False
            self.cur_round = max(self.cur_round, r)
            rec = self._rec(r)
            if m.get("storm"):
                rec.name = m["storm"]
        if t == "storm":
            rec.add_obs(m["age"], m["x"], m["y"], m["radius"], m["intensity"], m.get("vx"), m.get("vy"))
            self._feed_post(m)
        elif t == "track":
            rec.add_track(m["points"])
            self._feed_post(m)
        elif t == "storm_formed":
            rec.formed = True
            self._feed_post(m)
        elif t == "storm_dissipated":
            rec.dissipated = True
        elif t == "impact":
            rec.impacts.setdefault(m["island"], int(m["age"]))
            self._feed_post(m)
        elif t == "advisory":
            if m.get("source") == "official":
                rec.official[int(m.get("number", len(rec.official) + 1))] = m
            else:
                rec.wire.append(m)
            self._feed_post(m)
        elif t == "damage_report":
            rep = {"damage": dict(m["damage_pct"]), "loss": float(m.get("industry_loss_musd", 0.0))}
            if m.get("stage") == "final":
                rec.final = rep
            else:
                rec.prelim = rep
        elif t in ("announcement", "news"):
            self.announcements.append(m)
        else:
            return False
        self._bump(m)
        return True

    def _bump(self, m):
        self.version += 1
        self._cache = None
        self.log.append(m)
        if self.persist_path:
            try:
                with open(self.persist_path, "a") as f:
                    f.write(json.dumps(m, separators=(",", ":")) + "\n")
            except OSError:
                pass

    def _feed_post(self, m):
        try:
            self.post.on_message(m)
        except Exception:  # never let the classifier break the valuation
            pass

    def _official_known(self, m):
        """Snapshot fix part 2: an official advisory whose number this storm already has (recovered from a snapshot
        that arrived first, or a re-send) is never ingested again; the classifier would count its label twice."""
        r = m.get("round")
        if isinstance(r, str) and r.isdigit():
            r = int(r)
        rec = self.rounds.get(r) if isinstance(r, int) else None
        if rec is None:
            return False
        try:
            num = int(m.get("number"))
        except (TypeError, ValueError):
            return False
        storm = m.get("storm")
        return num in rec.official and (not storm or not rec.name or storm == rec.name)

    def _on_snapshot(self, m):
        changed = False
        adv = m.get("advisory") or {}
        for rep in m.get("damage") or []:
            rr = rep.get("round")
            if not isinstance(rr, int) or "damage_pct" not in rep:
                continue
            rec = self._rec(rr)
            d = {"damage": dict(rep["damage_pct"]), "loss": float(rep.get("industry_loss_musd", 0.0))}
            if rep.get("stage") == "final" and rec.final is None:
                rec.final = d; changed = True
            elif rep.get("stage") != "final" and rec.prelim is None:
                rec.prelim = d; changed = True
            self.cur_round = max(self.cur_round, rr)
        rnd = m.get("round") if isinstance(m.get("round"), int) else adv.get("round")
        if not isinstance(rnd, int):
            rnd = self.cur_round or 1
        o = m.get("obs")
        if o or adv or m.get("impacts"):
            self.cur_round = max(self.cur_round, rnd)
            rec = self._rec(rnd)
            # Snapshot fix part 1: snapshot.advisory has no storm, round or type. Everything rebuilt from the
            # snapshot carries the snapshot's storm and round; a missing storm made the classifier reset itself
            # (key (round, None)) and drop the recovered advisory, track and obs.
            name = m.get("storm") or adv.get("storm") or rec.name
            if name and not rec.name:
                rec.name = name
            for imp in m.get("impacts") or []:
                if imp.get("island") not in rec.impacts:
                    rec.impacts[imp["island"]] = int(imp["age"]); changed = True
                    self._feed_post({"type": "impact", "round": rnd, "storm": name, "island": imp["island"], "age": imp["age"]})
            if m.get("track"):
                rec.add_track(m["track"])
                self._feed_post({"type": "track", "round": rnd, "storm": name, "age": max(p[0] for p in m["track"]), "points": m["track"]})
                changed = True
            if o and int(o["age"]) not in rec.obs:
                rec.add_obs(o["age"], o["x"], o["y"], o["radius"], o["intensity"], o.get("vx"), o.get("vy"))
                so = dict(o, type="storm", round=rnd, storm=name)
                so.pop("seq", None)
                self._feed_post(so)
                changed = True
            if adv and adv.get("source", "official") == "official":
                num = int(adv.get("number", 0))
                if num and num not in rec.official:
                    sa = dict(adv, type="advisory", round=rnd, storm=name)
                    rec.official[num] = sa
                    if adv.get("seq") is None or adv["seq"] not in self.seen:
                        if adv.get("seq") is not None:
                            self.seen.add(adv["seq"])
                        self._feed_post(sa)
                    changed = True
        return changed

    # ------------------------------------------------------------------ persistence
    def to_state(self) -> dict:
        return {"log": self.log, "adjust": self.adjust, "overrides": self.overrides, "library_seasons": list(self.library_seasons)}

    @classmethod
    def from_state(cls, st: dict, **kw):
        kw.setdefault("library_seasons", tuple(st.get("library_seasons") or ()) or None)
        obj = cls(**kw)
        pp = obj.persist_path
        obj.persist_path = None
        for m in st.get("log", []):
            obj.on_message(m)
        obj.persist_path = pp
        for s, (mu, ad) in st.get("adjust", {}).items():
            obj.set_adjustment(s, mu, ad)
        for k, v in st.get("overrides", {}).items():
            obj.override(k, v)
        return obj

    @classmethod
    def restore(cls, persist_path, **kw):
        """Rebuild from the JSONL message log written with persist_path (then keep appending to it)."""
        log = []
        if os.path.exists(persist_path):
            with open(persist_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            log.append(json.loads(line))
                        except ValueError:
                            pass
        obj = cls.from_state({"log": log}, **kw)
        obj.persist_path = persist_path
        return obj

    # ------------------------------------------------------------------ manual hooks
    def set_adjustment(self, symbol, mult=1.0, add=0.0):
        """Per-symbol manual adjustment applied to every scenario value: v -> mult*v + add (stocks first; ETFs are
        recomputed from the adjusted stocks, then their own adjustment is applied)."""
        if mult == 1.0 and add == 0.0:
            self.adjust.pop(symbol, None)
        else:
            self.adjust[symbol] = [float(mult), float(add)]
        self._cache = None

    def _poll_adjust_file(self):
        """Reload the human-edited adjustment file when it changes (cheap stat per compute)."""
        if not self.adjust_file:
            return
        try:
            mt = os.stat(self.adjust_file).st_mtime
        except OSError:
            return
        if mt == self._adjust_mtime:
            return
        self._adjust_mtime = mt
        try:
            with open(self.adjust_file) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return
        self.adjust = {}
        for s_, v in d.items():
            if s_ in SYMBOLS and isinstance(v, dict):
                self.set_adjustment(s_, float(v.get("mult", 1.0)), float(v.get("add", 0.0)))
        ov = d.get("_overrides") or {}
        self.overrides = {}
        self.P = copy.deepcopy(self.P0)
        for k, v in ov.items():
            self.override(k, v)
        self._cache = None

    def clear_adjustments(self):
        self.adjust = {}
        self._cache = None

    def override(self, dotted, value):
        """Patch a model parameter live, e.g. override('settle.island.PORT.K', 0.03) or
        override('settle.insurance.HAVEN.top', 1400). Applies to the whole season's formula."""
        self.overrides[dotted] = value
        self.P = copy.deepcopy(self.P0)
        for k, v in self.overrides.items():
            _deep_set(self.P, k, v)
        self._cache = None

    # ------------------------------------------------------------------ per-round summaries
    def _round_summary(self, r):
        """(H per site, Tw per company) of round r from the feed so far, cached by the record's content."""
        rec = self.rounds.get(r)
        if rec is None or not rec._ages:
            return [0.0] * self.geo.n, [0.0] * 5, [0.0] * self.geo.n
        key = (len(rec.obs), rec.last_age(), len(rec.track), tuple(sorted(rec.impacts.items())))
        c = self._rcache.get(r)
        if c and c[0] == key:
            return c[1], c[2], c[3]
        H = rec.hazards()
        T = rec.closure_seconds()
        tw = [0.0] * 5
        for j in range(self.geo.n):
            tw[self.geo.sco[j]] += self.geo.sw[j] * T[j]
        self._rcache[r] = (key, H, tw, T)
        return H, tw, T

    def status(self, r):
        rec = self.rounds.get(r)
        if rec is None:
            return "unformed"
        if rec.final is not None:
            return "final"
        if rec.prelim is not None:
            return "prelim"
        a = rec.last_age()
        if a is None:
            return "unformed"
        if a >= 480 or rec.dissipated:
            return "over"
        return "progress"

    def phase(self):
        r = max(1, self.cur_round)
        s = self.status(r)
        if s == "final" and r < 3:
            return f"between rounds (next: round {r + 1})"
        return f"round {r}: {s}"

    def incurred(self):
        """Locked-in expected damage (pct) and closure (weighted seconds) of the current round's storm so far."""
        r = max(1, self.cur_round)
        rem = self._prefix(r)[2]
        H, tw, _ = self._round_summary(r)
        ph = self.P["physics"]
        ed = {co: F.expected_company_damage(self.geo, c, H, rem, ph["k"][co], ph["sigma"][co]) for c, co in enumerate(COMPANIES)}
        return {"round": r, "expected_damage_pct": ed, "closure_weighted_s": dict(zip(COMPANIES, tw)),
                "closed_now": [self.geo.sites[k][1] for k, on in enumerate(self.rounds[r].closed_now())] if r in self.rounds else []}

    def _prefix(self, upto):
        """Exact state after the rounds before `upto` that have final reports: (D, Tw, rem, L)."""
        geo = self.geo
        ph = self.P["physics"]
        D = [0.0] * 5; Tw = [0.0] * 5; L = 0.0
        rem = [1.0] * geo.n
        for r in range(1, upto):
            rec = self.rounds.get(r)
            if rec is None or rec.final is None:
                break
            fin = rec.final["damage"]
            H, tw, _ = self._round_summary(r)
            if not rec._ages:  # no storm track at all for this round (late start without saved state)
                tw, H = self._impute_round(fin, rem)
            for c, co in enumerate(COMPANIES):
                D[c] += fin.get(co, 0.0)
                Tw[c] += tw[c]
            L += rec.final["loss"] if rec.final.get("loss") else sum(self.P["settle"]["loss_per_pt"][co] * fin.get(co, 0.0) for co in COMPANIES)
            if any(H):
                rem = F.update_rem(geo, rem, H, fin, ph["k"])
        return D, Tw, rem, L

    def _impute_round(self, fin, rem, k=15):
        """Fallback when a completed round has a final report but no track (bot started late without saved state):
        closure (weighted seconds per company) and a site-hazard pattern from the k library storms whose final
        damage vectors are nearest. The site hazards only set how the company's reported damage is split across
        its sites (the multiplier solve rescales them to reproduce the report exactly)."""
        sc = [20.0, 10.0, 13.0, 9.0, 9.0]
        cand = []
        for idx, st in enumerate(self.pool):
            d = sum(((st["dmg"][co] - fin.get(co, 0.0)) / sc[c]) ** 2 for c, co in enumerate(COMPANIES))
            cand.append((d, idx))
        cand.sort()
        sel = [self.pool[i] for _, i in cand[:k]]
        tw = [sum(self.fut[i][1][c] for _, i in cand[:k]) / k for c in range(5)]
        H = [sum(st["H"][j] for st in sel) / k for j in range(self.geo.n)]
        return tw, H

    # ------------------------------------------------------------------ accessors
    def cumulative_damage(self):
        """{company: cumulative FINAL damage_pct over the completed rounds} (prefix of rounds with final reports)."""
        D, _, _, _ = self._prefix(4)
        return dict(zip(COMPANIES, D))

    def season_loss(self):
        """Cumulative insured loss ($M) of the completed rounds (sum of final industry_loss_musd)."""
        return self._prefix(4)[3]

    def closure_so_far(self, r=None):
        """{company: weighted closure seconds} of round r (default current) and per-site seconds."""
        r = r or max(1, self.cur_round)
        _, tw, T = self._round_summary(r)
        return {"weighted": dict(zip(COMPANIES, tw)), "per_site": {self.geo.sites[j][1]: T[j] for j in range(self.geo.n)}}

    def pattern_posterior(self):
        """Posterior over the current storm's steering class (sources model), or None before the storm forms."""
        try:
            if self.post.key is not None and self.post.key[0] == max(1, self.cur_round):
                return self.post.posterior()
        except Exception:
            return None
        return None

    # ------------------------------------------------------------------ scenario design
    def _design(self, S, nr):
        key = (S, nr)
        d = self._rng_designs.get(key)
        if d is not None:
            return d
        rng = random.Random(self.P["mc"].get("seed", 20260))
        zs, us, libs = [], [], []
        N = len(self.pool)
        for r in range(nr):
            zr, ur = [], []
            for c in range(5):
                perm = list(range(S)); rng.shuffle(perm)
                u = [(p + 0.5) / S for p in perm]
                ur.append(u)
                zr.append([_ND.inv_cdf(x) for x in u])
            zs.append(zr); us.append(ur)
            lst = []
            while len(lst) < S:
                blk = list(range(N)); rng.shuffle(blk); lst.extend(blk)
            libs.append(lst[:S])
        d = (zs, us, libs)
        self._rng_designs[key] = d
        return d

    # ------------------------------------------------------------------ valuation
    def compute(self, n_scen=None):
        """Run the scenario expectation; returns {symbol: (mean, sd)} and fills self.diag."""
        self._poll_adjust_file()
        if self._cache is not None and (n_scen is None or self._cache[0] == n_scen):
            return self._cache[1]
        t0 = time.perf_counter()
        geo = self.geo
        P = self.P
        ph = P["physics"]
        kk = [ph["k"][co] for co in COMPANIES]
        sg = [ph["sigma"][co] for co in COMPANIES]
        lpp = [P["settle"]["loss_per_pt"][co] for co in COMPANIES]
        cur = max(1, self.cur_round)
        stat = {r: self.status(r) for r in (1, 2, 3)}
        # exact prefix: leading rounds with final reports
        n_exact = 0
        while n_exact < 3 and stat[n_exact + 1] == "final":
            n_exact += 1
        D0, Tw0, rem0, L0 = self._prefix(n_exact + 1)
        todo = list(range(n_exact + 1, 4))
        in_progress = any(stat[r] == "progress" for r in todo)
        S = n_scen or (P["mc"]["n_scen"] if in_progress else P["mc"]["n_scen_quiet"])
        diag = {"phase": self.phase(), "round": cur, "status": dict(stat), "n_scen": S}
        zs, us, libs = self._design(S, 3)
        plans = []
        for idx, r in enumerate(todo):
            s = stat[r]
            if s in ("prelim", "over"):
                H, tw, _ = self._round_summary(r)
                hz = [(j, geo.sco[j], H[j]) for j in range(geo.n) if H[j] > 0]
                zr = zs[idx]
                if s == "prelim":
                    zr = []
                    pre = self.rounds[r].prelim["damage"]
                    for c, co in enumerate(COMPANIES):
                        g = F.prelim_posterior_grid(geo, c, H, rem0, kk[c], sg[c], float(pre.get(co, 0.0)), ph["prelim_log_sd"])
                        if g is None:
                            zr.append(zs[idx][c])
                        else:
                            zr.append([F.sample_grid(g[0], g[1], u) for u in us[idx][c]])
                plans.append(("fixed", r, hz, tw, zr))
            elif s == "progress":
                rec = self.rounds[r]
                H, tw, _ = self._round_summary(r)
                stv = rec.state_vector(P["analog"].get("vel_lag_s", 30))
                a = stv[0]
                pp = None
                try:
                    if self.post.key is not None and self.post.key[0] == r:
                        pp = self.post.posterior()["p"]
                except Exception:
                    pp = None
                if not pp:
                    pp = dict(self._sources["classifier"]["source_model"]["prior_base"])
                adv = rec.official[max(rec.official)] if rec.official else None
                sel = self.analogs.select(stv, pp, cone=adv)
                items = []
                for j, w in sel:
                    Hf, Tf = self.analogs.future(j, stv)
                    Ht = list(H)
                    for s_, h in Hf.items():
                        Ht[s_] += h
                    twt = list(tw)
                    for s_, tt in Tf.items():
                        twt[geo.sco[s_]] += geo.sw[s_] * tt
                    items.append((w, [(q, geo.sco[q], Ht[q]) for q in range(geo.n) if Ht[q] > 0], twt, j))
                # systematic resampling of analogs into S scenarios
                tot = sum(it[0] for it in items)
                assign = []
                k = 0
                cum = items[0][0] / tot
                for i in range(S):
                    u = (i + 0.5) / S
                    while u > cum and k < len(items) - 1:
                        k += 1
                        cum += items[k][0] / tot
                    assign.append(k)
                plans.append(("progress", r, items, assign, zs[idx]))
                diag["pattern_posterior"] = pp
                diag["analogs"] = len(items)
                diag["age"] = a
            else:  # unformed: a library storm
                plans.append(("library", r, libs[idx]))
        # ---------------------------------------------------------------- scenario loop
        adj = self.adjust
        isl = P["settle"]["island"]; rb = P["settle"]["rebuild"]; ins = P["settle"]["insurance"]
        V0 = [isl[co]["V0"] for co in COMPANIES]; Aa = [isl[co]["a"] for co in COMPANIES]; Kc = [isl[co]["K"] for co in COMPANIES]
        rw = [rb["RECON"]["w"].get(co, 0.0) for co in COMPANIES]
        Ra, Rb, Rc = rb["RECON"]["a"], rb["RECON"]["b"], rb["RECON"]["c"]
        Ma, Mb, Mc = rb["MAT"]["a"], rb["MAT"]["b"], rb["MAT"]["c"]
        Ga, Gb, Gc = rb["GEN"]["a"], rb["GEN"]["b"], rb["GEN"]["c"]
        Hv, Hs, Ha, Ht_ = ins["HAVEN"]["v0"], ins["HAVEN"]["slope"], ins["HAVEN"]["attach"], ins["HAVEN"]["top"]
        Rv, Rs, Ra2, Rt = ins["REIN"]["v0"], ins["REIN"]["slope"], ins["REIN"]["attach"], ins["REIN"].get("top")
        etf = geo.etfs
        sums = {s: 0.0 for s in SYMBOLS}; sq = {s: 0.0 for s in SYMBOLS}
        sD = [0.0] * 5; sTw = [0.0] * 5; sL = 0.0; sL2 = 0.0; pH = 0.0; pR = 0.0
        cur_add = [0.0] * 5
        sw = geo.sw
        exp = math.exp
        for i in range(S):
            D = list(D0); Tw = list(Tw0); L = L0
            rem = list(rem0)
            for pl in plans:
                kind = pl[0]
                if kind == "library":
                    sp, tw = self.fut[pl[2][i]]
                    add = [0.0] * 5
                    for j, c, f in sp:
                        v = rem[j]
                        if v > 0:
                            add[c] += sw[j] * v * f
                            rem[j] = v * (1.0 - f)
                    for c in range(5):
                        D[c] += 100.0 * add[c]; Tw[c] += tw[c]
                    L += 100.0 * sum(lpp[c] * add[c] for c in range(5))
                else:
                    if kind == "fixed":
                        hz, tw, zr = pl[2], pl[3], pl[4]
                    else:
                        it = pl[2][pl[3][i]]
                        hz, tw, zr = it[1], it[2], pl[4]
                    m = [exp(sg[c] * zr[c][i]) for c in range(5)]
                    add = [0.0] * 5
                    for j, c, h in hz:
                        v = rem[j]
                        if v > 0:
                            f = 1.0 - exp(-kk[c] * m[c] * h)
                            add[c] += sw[j] * v * f
                            rem[j] = v * (1.0 - f)
                    for c in range(5):
                        D[c] += 100.0 * add[c]; Tw[c] += tw[c]
                    L += 100.0 * sum(lpp[c] * add[c] for c in range(5))
                    if pl[1] == cur:
                        for c in range(5):
                            cur_add[c] += 100.0 * add[c]
            # settlement map
            v = {}
            for c, co in enumerate(COMPANIES):
                v[co] = V0[c] - Aa[c] * D[c] - Kc[c] * Tw[c]
            v["RECON"] = Ra + Rb * (1.0 - exp(-sum(rw[c] * D[c] for c in range(5)) / Rc))
            v["MAT"] = Ma + Mb * (1.0 - exp(-L / Mc))
            v["GEN"] = Ga + Gb * (1.0 - exp(-Tw[3] / Gc))
            v["HAVEN"] = Hv - Hs * min(max(L - Ha, 0.0), Ht_ - Ha)
            pay = max(L - Ra2, 0.0)
            if Rt is not None:
                pay = min(pay, Rt - Ra2)
            v["REIN"] = Rv - Rs * pay
            if adj:
                for s_, (mu, ad) in adj.items():
                    if s_ in v:
                        v[s_] = mu * v[s_] + ad
            for e, b in etf.items():
                x = 0.0
                for s_, q in b.items():
                    x += q * v[s_]
                if e in adj:
                    x = adj[e][0] * x + adj[e][1]
                v[e] = x
            for s_ in SYMBOLS:
                x = v[s_]
                sums[s_] += x; sq[s_] += x * x
            for c in range(5):
                sD[c] += D[c]; sTw[c] += Tw[c]
            sL += L; sL2 += L * L
            pH += L > Ha; pR += L > Ra2
        fl = P.get("sd_floor", {})
        out = {}
        for s_ in SYMBOLS:
            mu = sums[s_] / S
            var = max(0.0, sq[s_] / S - mu * mu)
            out[s_] = (mu, math.sqrt(var + fl.get(s_, 0.0) ** 2))
        diag.update({
            "E_damage_final": dict(zip(COMPANIES, [x / S for x in sD])),
            "E_closure_weighted_final": dict(zip(COMPANIES, [x / S for x in sTw])),
            "E_season_loss": sL / S, "sd_season_loss": math.sqrt(max(0.0, sL2 / S - (sL / S) ** 2)),
            "P_loss_above_HAVEN_retention": pH / S, "P_loss_above_REIN_attach": pR / S,
            "known_damage": dict(zip(COMPANIES, D0)), "known_closure_weighted": dict(zip(COMPANIES, Tw0)), "known_loss": L0,
            "compute_ms": 1000 * (time.perf_counter() - t0),
        })
        if stat.get(cur) != "final" and stat.get(cur) != "unformed":
            inc = self.incurred()
            diag["current_storm"] = {"E_total_damage_pct": dict(zip(COMPANIES, [x / S for x in cur_add])),
                                     "incurred_damage_pct": inc["expected_damage_pct"],
                                     "E_remaining_damage_pct": {co: max(0.0, cur_add[c] / S - inc["expected_damage_pct"][co]) for c, co in enumerate(COMPANIES)},
                                     "closure_so_far_weighted_s": inc["closure_weighted_s"]}
        self.diag = diag
        self._cache = (n_scen, out)
        return out


def fair_values(state: SeasonState, n_scen=None) -> dict:
    """{symbol: (mean, sd)} for the 10 stocks and both ETFs."""
    return state.compute(n_scen)


if __name__ == "__main__":
    # demo: python3 -I fairvalue.py <feed.jsonl> [round] [real_second]
    feed = sys.argv[1]
    want_r = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    want_t = float(sys.argv[3]) if len(sys.argv) > 3 else 780
    st = SeasonState()
    from datetime import datetime
    opens = {}
    with open(feed) as f:
        for line in f:
            m = json.loads(line)
            r = m.get("round")
            tm = datetime.fromisoformat(m["time"])
            day0 = tm.replace(hour=9, minute=30, second=0)
            real = (tm - day0).total_seconds() / 30.0
            if (r, real) > (want_r, want_t):
                break
            st.on_message(m)
    fv = fair_values(st)
    print(st.phase(), f"{st.diag['compute_ms']:.0f} ms")
    for s in SYMBOLS:
        print(f"{s:6s} {fv[s][0]:9.3f} +- {fv[s][1]:.3f}")
