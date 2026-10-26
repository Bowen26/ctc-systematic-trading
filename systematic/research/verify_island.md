# verify:island

## verdict

confirmed_with_corrections

## issues

## Verification of the island fit (PORT, ISLE, AQUA, GRID, FERRY)

**The fit holds.** I re-implemented the whole fit independently: my own feed parser, my own closure engine and my own least-squares solver (`verify-island/vx_*.py`). Given the fitter's estimator, I reproduce every headline number to the printed digit:
- free-fit coefficients;
- per-site K/w, which comes out at 0.050 or 0.060 for every site;
- holdout RMSE and max;
- the season-1 worked example;
- the loss decomposition, per-storm closure quantiles and preliminary-report statistics;
- the 4 s and 10 s sparse-observation tables.

The problems are in how the closure seconds are estimated and in the live engine, not in the formula.

### What I checked and found clean

- **Leakage: none in the formula.**
  - The final report's damage_pct in the feed equals the manifest damage in all 9000 values (max difference 0). Using the manifest in `extract.py` therefore makes no difference.
  - The boundary scan and the rounded coefficients were chosen on seasons 1-400 only.
  - The "lin" scheme was preferred over "count" on train too (train 0.0122 vs 0.0151 for PORT), so the choice did not use the holdout.
- **Timing: no off-by-one.**
  - In all 1800 storms: formed 10:00 (real 60), dissipated 14:00 (real 540), preliminary report 14:15 (570), final report 15:40 (740).
  - Every storm has exactly 241 observations (ages 0..480, every 2 s), no gaps and no duplicate seqs.
  - T is in real seconds, and 1 market hour = 120 real seconds.
  - Counting ticks 0..479 or 1..480 gives identical results.
- **Overfitting: none.** Each company has 3 coefficients plus 2 shared wind parameters, fitted on 400 seasons. Train and test errors are equal. Per-round K comes out at 0.02 / 0.05 / 0.06 in every round, and per-storm damage coefficients agree to 4 decimals.
- **Edge cases.**
  - Seasons with zero damage (83 PORT, 10 ISLE, 6 AQUA) have exactly 0.0000 error.
  - The top 5% of damage, the top 5% of closure, and seasons with an erratic storm (136) all look like the rest: RMSE 0.012-0.021, no bias above 0.004.
  - The worst residuals are all grazing events, where wind stays within about 0.01 of 0.35 for 10-20 s (season 461 PORT, seasons 40, 512 and 222 FERRY).
- **Impact rule confirmed.** 2628 of 2631 impact messages match the first model crossing at any company site on the island, with 0 false crossings. Leaving out the GRID, AQUA or FERRY sites leaves 308, 114 or 563 impacts with no model crossing, so all company sites matter.
- **Params file.** `fits/island.json` exists and parses. Its site lists match case.json, each per-site cost equals K x w, the per-hour costs are 2.40 / 6.00 / 7.20, and its numbers match the report.

### Problems found

1. **MEDIUM: the claim that the residual "cannot be removed" is wrong.** About 15-20% of the RMSE (25-37% of the MSE) can be removed, with no new fitted parameters. The method (`tickprob_impact`):
   - Treat the simulator's closure as a count of 1-s ticks.
   - For each unobserved odd tick, use the probability that the site is closed, given that intensity is a random walk. I measured sd 0.0294 per 2 s; the 2 s / 4 s ratio is 1.41 and the lag-1 correlation is -0.006.
   - Condition on impact messages. Before an island's impact (or while there is none yet), no company site on that island has crossed.

   Holdout RMSE:

   | | PORT | GRID | FERRY |
   |---|---|---|---|
   | fitter's scheme | 0.0139 | 0.0138 | 0.0187 |
   | tickprob_impact | 0.0118 | 0.0122 | 0.0149 |
   | paired t (per-season squared error) | 2.59 | 3.05 | 3.76 |

   Train RMSE also improves: 0.0122 to 0.0101 (PORT), 0.0139 to 0.0115 (GRID), 0.0191 to 0.0135 (FERRY). The worst error drops from 0.094 to 0.077. The gain is stable for sd between 0.02 and 0.04, and it disappears as sd goes to 0; the deterministic 1-s tick version gives the same RMSE as the fitter's scheme. A continuous-time bridge version is not significantly better (t between -0.2 and 1.3). That points to discrete 1-s closure ticks. In dollars the gain is small (0.2-0.4 cents RMSE).

2. **MEDIUM (operations): the live engine handles feed outages badly.** During an exchange problem only the latest storm and track message of each burst arrives. The engine then interpolates (wind - threshold) linearly across the whole gap and ignores track messages.

   I simulated this on the holdout (rows: outage pattern; values: RMSE / max in $):

   | engine | outages | PORT | GRID | FERRY |
   |---|---|---|---|---|
   | fitter's | 6 x (5-40 obs) | 0.12 / 1.07 | 0.17 / 1.10 | 0.26 / 1.09 |
   | fitter's | 3 x (30-90 obs) | 0.63 / 2.84 | 0.70 / 2.37 | 0.81 / 3.14 |
   | v2 (track points fill position) | 6 x (5-40 obs) | 0.034 / 0.20 | 0.084 / 0.52 | 0.118 / 0.72 |
   | v2 (track points fill position) | 3 x (30-90 obs) | 0.27 / 1.65 | 0.34 / 1.65 | 0.32 / 1.53 |

   The fitter's sparse test (every 4th or 10th observation) understates this, because evenly thinned observations are not how outages look. What remains after filling position is unobserved intensity, which cannot be recovered.

3. **MEDIUM (operations): a restart loses all earlier state.** The engine keeps state only in memory and ignores `snapshot` messages. A restart at the open of round 2 gives holdout errors (RMSE / max) of PORT 19.1 / 51.9, GRID 6.1 / 14.9 and FERRY 3.3 / 7.0. Snapshots carry damage reports and the current round's impacts, but not earlier rounds' closure seconds, so the state must be saved to disk.

4. **LOW: the preliminary report is used as-is.**
   - Its noise is multiplicative, with relative sd about 0.33 at every damage level.
   - It is uncorrelated across companies (|corr| < 0.1), so it partly diversifies in ARCH.
   - Fit on train, final ≈ 0.85-0.89 x preliminary. Shrinking by that factor cuts holdout error by about 10%; for PORT, 9.84 to 8.79 pp per storm.
   - A preliminary value of 0 always means a final value of 0 (869 of 869 PORT cases). A final value of 0 always comes with a preliminary of 0.

5. **LOW / informational.**
   - ISLE and AQUA residuals sit exactly at the rounding floor: settlement rounding (sd 0.0029) plus damage_pct rounding (a x 0.0029 x sqrt(3)) gives 0.0034 for ISLE and 0.0031 for AQUA, as observed.
   - Every alternative form I tried is clearly worse or collapses back to the base model (table under the metrics).

### What can be known in real time
- **Closure loss** is fully known from the feed by storm end (real 540), to about 1-1.5 cents. Impact conditioning uses only past messages.
- **Damage** before the preliminary report at real 570 needs a damage nowcast, which is the physics agent's job.
- **Between 570 and 740**, use the shrunk preliminary value; the error per storm is still large, about 8.8 pp for PORT.
- **From the final report at 740**, settlement is known to about 1-1.5 cents for PORT, GRID and FERRY and 0.3 cents for ISLE and AQUA.
- **Live announcements** may change V0, a, K, the threshold or the sites. Keep these as parameters that can be patched.

## corrections

The settlement formula and constants need no changes:
- value = V0 - a x D_total - K x sum_s(w_s x T_s), with site s closed while I x exp(-(d/r)^2) > 0.35.
- Constants (V0 / a / K): PORT 100 / 0.55 / 0.02, ISLE 60 / 0.38 / 0, AQUA 35 / 0.20 / 0, GRID 50 / 0.24 / 0.05, FERRY 25 / 0.05 / 0.06.

**Corrections:**

**(1) Estimate T_s with `tickprob_impact` instead of linear interpolation of (wind - threshold).**
- For consecutive observations at ages a0 < a1, take each integer tick t from a0 to a1-1.
- Tick a0 is observed: count 1 if wind > 0.35, else 0.
- Every other tick counts P = Phi((m x g - 0.35) / (sd x g)), where:
  - g = exp(-(d/r)^2), using track points for position and radius inside a gap, else linear interpolation;
  - m = intensity interpolated linearly between the two observations;
  - sd = sqrt(var1 x (t-a0)(a1-t)/(a1-a0)), with var1 = 0.0294^2/2 per second. For the single odd tick between 2-s observations, sd = 0.0147.
- Impact conditioning, per island:
  - Before that island's impact age, or for all ticks so far if no impact message has arrived yet, set every company site on that island to 0. This must use all 17 company sites, including ISLE and AQUA.
  - At the impact tick, set the most exposed site to 1.

Holdout RMSE improves to PORT 0.0118, GRID 0.0122 and FERRY 0.0149.

**(2) Live engine changes:**
- Use track points to fill gaps.
- Read impacts, damage and the latest observation from `snapshot` messages.
- Save state to disk (to_state / from_state) after every message, so a restart keeps earlier rounds' closure.

**(3) Before the final report, use shrink x preliminary damage**, with shrink = PORT 0.8684, ISLE 0.8450, AQUA 0.8907, GRID 0.8750, FERRY 0.8533.

**Files:**
- Reference implementation, validated by streaming the raw holdout feeds: `/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/verify-island/island_value_v2.py`
- Verified parameters: `/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/fits/island.verified.json`

## corrected_params_file

/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/fits/island.verified.json

## independent_metrics

All numbers come from my own scripts in `work/se/verify-island/`: `vx_extract.py`, `vx_lib.py`, `vx_closure.py`, `vx_fit.py`, `vx_scan.py`, `vx_forms.py`, `vx_edge.py`, `vx_impact.py`, `vx_prob.py`, `vx_tpimp.py`, `vx_paired.py`, `vx_gapfill.py`, `vx_engine2_test.py`, `vx_sparse_prelim.py`. Fit on seasons 1-400; test on 401-600 (n = 200); errors in $.

**A. Fitter's estimator ("lin"), re-implemented independently.** Free fit on train (V0 / a / K):
- PORT 99.9990 / 0.54995 / 0.02001
- ISLE 60.0004 / 0.38002 / 0
- AQUA 35.0001 / 0.20000 / 0
- GRID 49.9993 / 0.23992 / 0.05002
- FERRY 25.0010 / 0.05008 / 0.05998

With the rounded coefficients fixed:

| co | test RMSE / max | p95 | mean error | share within 5 cents |
|---|---|---|---|---|
| PORT | 0.0139 / 0.069 | 0.029 | +0.0010 | 0.995 |
| ISLE | 0.0034 / 0.008 | | | |
| AQUA | 0.0031 / 0.006 | | | |
| GRID | 0.0138 / 0.046 | 0.028 | | 1.00 |
| FERRY | 0.0187 / 0.094 | 0.035 | | 0.99 |

All of these match the fitter.

**B. Time-integration schemes** (wind form I x exp(-(d/r)^2), threshold 0.35; RMSE train / test):

| scheme | PORT | GRID | FERRY |
|---|---|---|---|
| lin | 0.0122 / 0.0139 | 0.0139 / 0.0138 | 0.0191 / 0.0187 |
| count observations x 2 s | 0.0152 / 0.0157 | 0.0169 / 0.0173 | 0.0226 / 0.0252 |
| 0.2 s subsample | 0.0124 / 0.0139 | 0.0140 / 0.0138 | 0.0194 / 0.0190 |
| deterministic 1 s tick | 0.0111 / 0.0134 | 0.0137 / 0.0149 | 0.0184 / 0.0187 |
| continuous bridge | 0.0122 / 0.0134 | 0.0131 / 0.0133 | 0.0177 / 0.0188 |
| tickprob | 0.0101 / 0.0118 | 0.0120 / 0.0129 | 0.0159 / 0.0174 |
| **tickprob_impact** | **0.0101 / 0.0118** | **0.0115 / 0.0122** | **0.0135 / 0.0149** |

For tickprob_impact, test max is 0.059 / 0.052 / 0.077 and the free coefficients stay at 0.02 / 0.05 / 0.06. Paired t against lin: +2.59 / +3.05 / +3.76.

**C. Wind-field shapes**, each with 2 parameters optimised on train. Objective is the sum of train MSE for PORT, GRID and FERRY; then test RMSE per company:

| shape | objective | PORT | GRID | FERRY |
|---|---|---|---|---|
| Gaussian (scale 0.9997, threshold 0.3501) | 0.000713 | 0.0139 | 0.0139 | 0.0188 |
| exp(-d/r) | 0.00380 | 0.028 | 0.039 | 0.046 |
| parabolic | 0.00714 | 0.043 | 0.048 | 0.057 |
| power-law boundary d/r < 1.028 x I^0.426 | 0.0110 | 0.042 | 0.062 | 0.078 |
| Lorentzian | 0.0204 | 0.056 | 0.082 | 0.117 |

**D. Valuation forms** (test RMSE PORT / GRID / FERRY):
- base: 0.0139 / 0.0138 / 0.0186
- closure x (1 - prior damage/100): 0.54 / 0.23 / 0.38
- wind-excess integral in place of T: 0.54 / 0.68 / 0.76
- unweighted closure (no site weights): GRID 0.40, FERRY 0.25
- multiplicative damage: 2.33 / 0.35 / 0.08
- per-round K, base + D x Tw, base + prior-damage x Tw, base + excess integral: each extra coefficient is about 0 and test RMSE is unchanged

**E. Edge cases** (lin scheme, all 600 seasons):
- zero damage: PORT, ISLE and AQUA error exactly 0
- top 5% of damage: RMSE PORT 0.018, GRID 0.018, FERRY 0.021
- seasons with an erratic storm (n = 136): 0.012 / 0.013 / 0.018
- PORT seasons with no closure: 0.0028
- RMSE grows with the number of threshold crossings

**F. Impacts.** 2631 impact messages; 3 have no model crossing; 0 false crossings. The first observation above threshold is at the impact age or 1 s after it in 2581 cases.

**G. Preliminary report.**

| co | prelim - final mean | ratio mean / sd | value error sd per storm | shrink factor | holdout RMSE raw / shrunk (pp) |
|---|---|---|---|---|---|
| PORT | +0.82 pp | 1.051 / 0.332 | $5.16 | 0.868 | 9.84 / 8.79 |
| ISLE | +0.52 pp | | $1.79 | 0.845 | 4.56 / 4.27 |
| AQUA | +0.49 pp | | $1.28 | 0.891 | 6.63 / 5.93 |
| GRID | +0.65 pp | | $1.32 | 0.875 | 6.08 / 5.44 |
| FERRY | +0.66 pp | | $0.28 | 0.853 | 5.81 / 5.16 |

**H. Live-engine stress test** (holdout; RMSE / max for PORT, GRID, FERRY):

| scenario | fitter's engine | island_value_v2.py |
|---|---|---|
| clean | matches the fitter's numbers | 0.0118 / 0.059, 0.0122 / 0.052, 0.0149 / 0.077 |
| 2% duplicated messages | unchanged | unchanged |
| 6 outages of 5-40 observations | 0.12 / 1.07, 0.17 / 1.10, 0.26 / 1.09 | 0.034 / 0.20, 0.084 / 0.52, 0.118 / 0.72 |
| 3 outages of 30-90 observations | 0.63 / 2.84, 0.70 / 2.37, 0.81 / 3.14 | 0.27 / 1.65, 0.34 / 1.65, 0.32 / 1.53 |
| restart at round 2 | 19.1 / 51.9, 6.1 / 14.9, 3.3 / 7.0 (no saved state) | identical to clean (state saved and restored) |

The v2 engine takes at most about 1 ms per call to value().
