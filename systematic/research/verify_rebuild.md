# verify:rebuild

## verdict

confirmed_with_corrections

## issues

## Verification summary
I rebuilt the pipeline from scratch with my own extraction, wind and closure code, fitting code and nowcast code (scripts in `SP/work/se/verify-rebuild/`, run with `python3 -I`, standard library only).
- **The three main laws hold exactly as stated:**
  - RECON = 40 + 24(1 − e^(−D/1000)), with D = Σ(9P + 7G + 1.2F)
  - MAT = 28 + 14(1 − e^(−L/1200))
  - GEN = 32 + 18(1 − e^(−O/120)), closed while I·e^(−(d/r)²) > 0.35
- **The holdout numbers match the fitter's to 3-4 digits.**
- **The params file `fits/rebuild.json` exists, parses, and its values match the report:**
  - a, b, c for all three laws;
  - holdout RMSE and max error for the int1, herm1 and obs2 variants;
  - Laplace factors m_r;
  - marginal values;
  - method A/B round-open results.
- **No look-ahead leakage in the main laws:**
  - The drivers use the feed's final damage reports. These are identical to the manifest (max difference 0), and the season loss equals the sum of storm losses to within 0.1.
  - The feed's 2-s storm observations and the case.json weights are both available live.
  - The final report arrives at real 740 s, row 370 of the round. The house reflects it from row 372 (744 s).
- **No overfitting.** Each law has 3 parameters, and GEN adds θ. Train and holdout RMSE are equal. The nowcast uses 14 coefficients per age bucket fit on 1200 storms, and its in-sample and holdout errors are similar.
- **Re-implementations that reproduced the fitter's tables:**
  - The extended GEN nowcast: 0.99 / 0.94 / 0.83 / 0.72 / 0.56 / 0.30 / 0.16 / 0.05 / 0.03 at ages 40-440, vs house 1.12 / 1.00 / 0.84 / 0.77 / 0.72 / 0.55 / 0.29 / 0.15 / 0.07.
  - Method A/B round-open RMSEs, the m_r values, and the preliminary-report results.

## Issues found
1. **[MODERATE] The "house opens rebuilders too high / lean short" claim is overstated.** The fitter reports pooled all-600 numbers, which mix the fit period with the holdout. Split by period (house − settlement at the round 1/2/3 opens, with t-stats):
   - **RECON:** train +0.745 / +0.616 / +0.350 (t 3.4 / 3.3 / 2.5); holdout **+0.212 / +0.170 / +0.200 (t 0.7 / 0.7 / 1.1)**.
   - **MAT:** train +0.285 / +0.229 / +0.148 (t 3.2 / 2.9 / 2.5); holdout **+0.078 / +0.062 / +0.085 (t 0.6 / 0.6 / 1.1)**.
   - **GEN:** train +0.274 / +0.174 / +0.098; holdout **+0.096 / +0.028 / +0.072 (t ≤ 0.7)**.
   - Out of sample the bias does not replicate significantly. At the round-2 open on the holdout, method B is biased low by 0.37 for RECON, while the house is only 0.17 too high.
   - A short lean of about 0.5 per share is not supported. At most about 0.2 for RECON, 0.08 for MAT and 0.05 for GEN, against a settlement sd of about 4.4 / 1.8 / 2.3.
2. **[LOW-MODERATE] The recommended GEN integration (int1) is not the most accurate.** Holdout RMSE / max error with 32/18/120, by integration method:
   - continuous-time, linear interpolation, midpoint dt = 0.1 s: **0.0220 / 0.082**
   - same at dt = 0.25 s: 0.0223 / 0.079
   - dt = 0.5 s: 0.0231 / 0.080
   - herm1: 0.0237 / 0.102
   - int1: 0.0251 / 0.120
   - obs2: 0.0272 / 0.088
   - half-integer ages: 0.0277 / 0.095
   - smoothed intensity: 0.0255 / 0.126 (smoothing confirmed worse)

   The fitter's own table 2.4 shows the 0.5-s rule at 0.023 / 0.080, yet the report recommends int1. Continuous integration is also live-computable: each 2-s segment can be integrated as soon as its end observation arrives.
3. **[LOW] Holdout data leaked into the preliminary-report calibration.** `realtime.prelim_vs_final.calib` was fit on all 1800 storms: RECON [26.52, 0.852], MAT [39.28, 0.841]. Train-only it is RECON [24.48, 0.857] and MAT [36.37, 0.846]. No reported holdout metric used it, since the raw preliminary report was plugged in.
   - I confirmed that neither raw nor train-calibrated preliminary values beat the house in round 3: RECON 0.698 / 0.735 vs house 0.428; MAT 0.345 / 0.359 vs 0.199.
   - A train-fit blend gives 0.432 / 0.201, with weight about 0.93-0.95 on the house.
4. **[LOW] The wind-calibration statistic is inflated.** "93.9% within 0.5 s" counts the roughly 1530 storm-island pairs with no impact and no predicted crossing as exact matches. Over the 869 actual impacts (seasons 1-200, θ = 0.35):
   - 83.2% fall within 0.5 s and 99.3% within 2 s, with 1 miss and 0 false alarms.
   - θ is still independently confirmed: fitting θ on GEN settlements gives a sharp minimum at 0.350 with the exponent of 2. θ = 0.345 or 0.355 gives 0.048 / 0.047, and a 0.9 or 1.1 exponent scale gives at least 0.19 even when refit.
5. **[LOW] Some timing labels are off by up to 8 s.**
   - In `realtime.py` and `house_bias.py`, the px5 index floors to a 10-s grid. The house "738 s" is really 730 s, and "778 s" is really 770 s.
   - The "last-40 s" window starts at 740 s, but the house still shows the pre-report price until 744 s.
   - Conclusions are unchanged. At the true 738 s (row 369) the house RMSE over all 600 seasons is RECON 0.411, MAT 0.195, GEN 0.060. From 744 s it is 0.077 / 0.052 / 0.059.
6. **[LOW] The pattern tables are keyed on the true pattern.** `future_storms.m_by_pattern` and `gen_m_rest_by_age_and_true_pattern` use the manifest's true pattern, which a live bot never sees. Use them only mixed with a live pattern posterior.
7. **[INFO] The headline and the report disagree on when the nowcast beats the house.** The headline says "from about 200 s", the report says "from 40 s". My reproduction:
   - better at 40-100 s (0.99 vs 1.12, 0.94 vs 1.00);
   - slightly worse at 120-140 s (0.944 vs 0.941, 0.930 vs 0.903);
   - better from 160 s;
   - large edge from about 220 s (0.60 vs 0.75, then 0.30 vs 0.55 at 300 s).
8. **[INFO] Edge cases and extrapolation.**
   - **Driver ranges seen:** no season has zero RECON damage. D_RECON spans 55.8-1411 $M, L spans 128.8-1766 $M and O spans 0-162.5.
   - **Extrapolation:** V(D = 0) = 40 is extrapolated, but the lowest seasons fit exactly (season 318: D = 55.81 → 41.303 vs 41.30). The single O = 0 season (318) settles at exactly 32.00.
   - **Extremes:** the largest D, L and O seasons fit to within 0.003 / 0.003 / 0.03.
   - **Erratic storms:** seasons with erratic or turned-erratic storms show no degradation (RECON 0.0029, MAT 0.0027, GEN 0.020-0.021).
   - **GEN residual:**
     - Correlation is −0.0008 with cumulative GRID damage, 0.022 with O and 0.009 with season loss.
     - The implied outage error is p1 / p99 = −0.73 / +0.70 weighted seconds, i.e. interpolation noise.

## What can and cannot be known in real time (confirmed)
- **RECON and MAT** are exact only after each round's final report (real 740 s). The preliminary report at 570 s is too noisy to beat the house.
- **GEN** is live-computable from the 2-s storm feed and final at storm end (age 480, real 540 s). The house is already at about 0.06 RMSE after the storm, so the GEN edge is during the storm, from about 160-220 s of storm age.

## corrections

**Main laws: no change** (all confirmed):
- **RECON** = 40 + 24·(1 − e^(−D/1000)), where D = Σ_storms (9.0·PORT% + 7.0·GRID% + 1.2·FERRY%) from the final reports.
- **MAT** = 28 + 14·(1 − e^(−L/1200)), where L = Σ industry_loss_musd = Σ(9P + 6I + 2.5A + 7G + 1.2F).
- **GEN** = 32 + 18·(1 − e^(−O/120)), where O = Σ_storms Σ_substations w_i · seconds with I·exp(−(d_i/r)²) > 0.35.

**Corrections:**
1. **GEN closure integration: use continuous time instead of int1.**
   - Method: between consecutive 2-s observations, interpolate x, y, r and I linearly. Integrate with the midpoint rule at dt ≤ 0.25 s (0.1 s used).
   - Holdout RMSE / max: 0.0220 / 0.082, vs 0.0251 / 0.120 for int1.
   - Free-fit parameters: a = 32.0025, b = 18.0243, c = 120.306. Keep the rounded 32 / 18 / 120.
2. **House open bias: use the train/holdout split, not the pooled all-600 numbers.**
   - The holdout bias is not significant: RECON +0.21 / +0.17 / +0.20, MAT +0.08 / +0.06 / +0.09, GEN +0.10 / +0.03 / +0.07 (t ≤ 1.1).
   - Cap any short lean at about these sizes.
3. **Preliminary-to-final driver calibration (train only):** RECON final ≈ 24.48 + 0.857·prelim; MAT final ≈ 36.37 + 0.846·prelim. Still do not trade on it over the house.
4. **Wind-calibration statistic, restated:** 83.2% of actual impacts within 0.5 s, 99.3% within 2 s.
5. **House timing labels:** "738/778 s" are really 730/770 s. The house reflects the final report about 4 s after 740 s.
6. **Pattern-conditional tables:** flagged as true-pattern lookups. Use them only weighted by a live pattern posterior.

All of these are applied, plus a verification block, in the corrected params file.

## corrected_params_file

/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/fits/rebuild.verified.json

## independent_metrics

My own holdout numbers. Fit on seasons 1-400 and evaluated on seasons 401-600, except where a line says all 600.

**RECON** (D = 9P + 7G + 1.2F)
- **Exponential:** free fit a = 39.9984, b = 23.9959, c = 999.55. Train RMSE 0.0029. Holdout RMSE 0.0030, max 0.0059.
- **Rounded 40/24/1000:** holdout RMSE 0.00294, max 0.00603, bias +0.0002.
- **Alternative shapes (holdout RMSE / max):**
  - hyperbolic D/(D+c): 0.0350 / 0.108
  - arctan: 0.0419 / 0.177
  - log: 0.0721 / 0.222
  - tanh: 0.0799 / 0.302
  - Weibull exponent p = 0.98 / 1.02: 0.0073 / 0.0081 (the optimum is p = 1.00)
  - per-storm capacity: 0.621
  - multiplicative damage total: 0.446
- **Implied $M per damage point** (from inverting the law, train): PORT 9.001, ISLE 0.003, AQUA 0.001, GRID 6.998, FERRY 1.197.

**MAT**
- **Exponential:** free fit a = 28.0006, b = 14.0011, c = 1200.33. Holdout RMSE 0.0027, max 0.0052.
- **Rounded 28/14/1200:** holdout RMSE 0.00274, max 0.00515.
- **Alternative shapes (holdout RMSE):** hyperbolic 0.0176, arctan 0.0150, log 0.0354, tanh 0.0351, p = 0.98: 0.0038, per-storm 0.281, multiplicative 0.241.
- **Implied weights:** 8.999 / 6.001 / 2.500 / 7.003 / 1.198.
- **Insured-loss identity** (train storms): 9.0001P + 6.0004I + 2.4999A + 7.0001G + 1.1997F, RMSE 0.044.

**GEN** (32/18/120)
- **Holdout RMSE / max by integration method:**
  - int1: 0.0251 / 0.120 (reproduces the fitter)
  - herm1: 0.0237 / 0.102
  - obs2: 0.0272 / 0.088
  - dt = 0.5 s: 0.0231 / 0.080
  - dt = 0.25 s: 0.0223 / 0.079
  - **dt = 0.1 s: 0.0220 / 0.082** (best)
- **Continuous version (dt = 0.1 s):** free fit a = 32.0025, b = 18.0243, c = 120.31, train 0.0217, holdout 0.0220.
- **θ and wind-shape grid (int1, refit):**
  - θ = 0.345 / 0.35 / 0.355 gives 0.049 / 0.025 / 0.047.
  - Scaling the squared-distance term by 0.9 or 1.1 gives at least 0.19.
- **Alternative shapes on continuous O (holdout RMSE / max):** hyperbolic 0.0295 / 0.155, arctan 0.0345 / 0.146, log 0.0460 / 0.306. Per-storm capacity: 0.311.
- **Implied outage error:** p1 / p99 = −0.73 / +0.70 weighted seconds.

**REBLD**
- Settlement = 2·RECON + 2·MAT + GEN to within 3e-14.
- From the three laws with continuous GEN: holdout RMSE 0.0233, max 0.089.

**Impact calibration** (seasons 1-200, θ = 0.35, 869 actual impacts): 83.2% within 0.5 s, 99.3% within 2 s, 1 miss, 0 false alarms.

**Round-open fair values, holdout RMSE (bias)**

| Symbol, round | Method A | Method B | House |
|---|---|---|---|
| RECON r2 | 3.763 (+0.678) | 3.633 (+0.365) | 3.659 (−0.170) |
| RECON r3 | 2.661 | 2.592 | 2.629 |
| MAT r2 | 1.624 | 1.525 | 1.521 |
| MAT r3 | 1.204 | 1.145 | 1.141 |
| GEN r2 | 1.975 | same as A | 1.983 |
| GEN r3 | 1.391 | same as A | 1.397 |

- **Laplace factors m_r (train):** RECON .7630 / .8070 / .8443, MAT .7296 / .7737 / .8204, GEN .8235 / .8214 / .8267.

**GEN nowcast, round 3 holdout, RMSE vs settlement** (my own implementation with the cone, advisory probabilities, outlook dummies and currently closed substations)

| Storm age (s) | 40 | 100 | 160 | 200 | 240 | 300 | 360 | 400 | 440 |
|---|---|---|---|---|---|---|---|---|---|
| Nowcast | 0.992 | 0.943 | 0.833 | 0.718 | 0.563 | 0.303 | 0.157 | 0.053 | 0.026 |
| House | 1.120 | 1.003 | 0.842 | 0.766 | 0.715 | 0.548 | 0.290 | 0.153 | 0.073 |
| No forecast | 1.39 | 1.39 | 1.39 | 1.40 | 1.38 | 1.18 | 0.75 | 0.41 | 0.13 |

- A simpler nowcast (dead-reckoning, closed-now and advisory-probability features only) is worse than the house before about 300 s. The early edge comes from the cone features.
- **House GEN after the storm** (round 3 holdout): about 0.056-0.065.

**Preliminary report, round 3 holdout**
- RECON: raw 0.698, train-calibrated 0.735, house at 738 s 0.428, blend 0.432.
- MAT: raw 0.345, calibrated 0.359, house 0.199, blend 0.201.

**House open − settlement:** see issue 1. Train t-stats are about 2.5-3.4 for RECON and MAT; holdout t ≤ 1.1.
