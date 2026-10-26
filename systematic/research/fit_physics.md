# fit:physics

## headline

- Wind field: w = I * exp(-(d/r)^2), zero beyond d = 2r. PORT is damaged if and only if the closest 2-s observation has d/r < 2 (931 damaged storms, no exceptions). A free shape exponent fits to 1.993. Exponential, Rankine, linear and peak-wind models all fit clearly worse.
- Damage: H_s = sum of 2*w over the 2-s observations. Each storm destroys f = 1 - exp(-k_c * m * H_s) of a site's REMAINING value, and log m ~ N(0, sigma_c) is drawn once per company per storm. k per wind-second: PORT 0.010997, ISLE 0.008684, AQUA 0.015515, GRID 0.011654, FERRY 0.008476 (bootstrap SE 1-1.6%). sigma is 0.342-0.367. One k per company is enough; per-site k gives no holdout gain.
- Holdout (seasons 401-600, 600 storms), expected damage at storm end vs the final report: PORT R2 0.948 / RMSE 5.33 pts, ISLE 0.953 / 2.48, AQUA 0.968 / 2.71, GRID 0.896 / 3.15, FERRY 0.910 / 3.16. These RMSEs equal the noise floor the model itself predicts (5.67, 2.43, 2.72, 3.38, 3.41), so no feed variable can do better before a damage report. The 80% intervals cover 82-89% of outcomes.
- Insured loss is exact: loss_musd = 9.0*PORT + 6.0*ISLE + 2.5*AQUA + 7.0*GRID + 1.2*FERRY (R2 0.99999997). From the track alone, storm loss on holdout has R2 0.954, RMSE $55.9M. Season loss is the sum of the three storm losses.
- Closure: a site is closed while its wind is at least 0.350 (+/-0.001), measured at the company sites, not the island polygon or centroid. Predicted island-impact messages match yes/no 99.92% of the time on holdout; timing is exact in 81% of cases and within 1 s in 99%.
- Damage locks in late: 51% of the storm's damage is in by age 300 (real 360 s), 79% by 360, 93% by 400, 97% by 420. Median age at which 50% is locked in: PORT 360, ISLE/AQUA/FERRY 260, GRID 320.
- Preliminary report = final x lognormal noise with sd 0.30, independent of m. Alone it is worse than the model (PORT RMSE 9.8). Combined with the model it cuts holdout RMSE to PORT 4.39, ISLE 1.95, AQUA 2.30, GRID 2.78, FERRY 2.57, and loss RMSE to $46.5M.

## params_file

/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/fits/physics.json

## scripts

[
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/damage_model.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/extract.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/features.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/fitlib.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/fit_mle.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/mle_generic.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/eval_model.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/write_params.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/bootstrap.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/export_features.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/field_compare.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/noise_floor.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/timing_stats.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/gap_test.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/demo_fay.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/mult_check.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/prelim_struct.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/aqua_shelter.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/intensity_dyn.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/loss1.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/impact1.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/impact2.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/port_mle.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/edge_port.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/interp_test.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/noise_struct.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/noisecorr.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/closure_feat.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/fit_r1.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/fit_port1.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/resid_port.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/logratio_port.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/logratio_reg.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/inten.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/explore1.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/explore2.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/explore3.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/explore4.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/explore5.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/chk_ll.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/chk_solve.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/physics/find_bad.py"
]

## report

# Storm wind, damage and closure model (Systematic Equities, Hurricane Season)

## Summary

- **The model:** the observed 2-s storm track (age, x, y, radius, intensity) gives each company's expected damage in real time. That damage is "locked in" as the storm passes each site. On holdout seasons 401-600, the expected damage at the end of the storm has these R² / RMSE against the final damage report: PORT 0.948 / 5.33, ISLE 0.953 / 2.48, AQUA 0.968 / 2.71, GRID 0.896 / 3.15, FERRY 0.910 / 3.16 (RMSE in damage points).
- **Why it can't get better from the feed:** the remaining error is a random per-company, per-storm multiplier (lognormal, sd 0.34-0.37 in log hazard). Nothing in the feed predicts it. The realised holdout RMSE equals the model's own predicted noise floor, so only the damage reports can reduce it.
- **Insured loss** is an exact linear function of the five company damages.
- **Closures and impacts** follow a crisp wind threshold of 0.35, measured at the company sites.
- **Deliverables:**
  - Model code: `work/se/physics/damage_model.py`
  - Parameters and metrics: `work/se/fits/physics.json`
  - Per storm × site features (all 1800 storms), for the valuation agents: `work/se/physics/site_features.csv` and `company_features.csv`

All numbers below come from scripts in `work/se/physics/`. They were fit on seasons 1-400; holdout is seasons 401-600 (600 storms). Python standard library only, run as `python3 -I`.

## Data used

- **`storms.json`** (cache, from `extract.py`): per storm, 241 storm observations at ages 0, 2, ..., 480, the last track, impacts, the preliminary and final reports, and manifest fields. The research feed has no gaps. Track points equal the storm observations at the same age.
- **`site_series.json`** (from `features.py`): per storm and site, the observations with d/r < 2.2.
- **Storm-state facts:**
  - Every storm starts with intensity 1.000 and radius 80.0.
  - 2-s intensity increments are uncorrelated (lag-1 autocorrelation of differences -0.005). Intensity is a mean-reverting random walk (step sd 0.0293), not a smooth signal plus measurement noise, so the observations are the true state rounded to the published precision.
  - Impact ages can be odd seconds, so the simulator ticks at least once per second.

## 1. Wind field

The shape was settled on PORT, the only single-site company.

- **Hard cut-off at 2 radii.** PORT damage is above 0 if and only if the minimum over the 2-s observations of d/r is below 2. This holds for all 931 damaged storms. Examples: 1.991 gives damage 0.04, 2.001 gives 0, and every storm at 2.001 or beyond has 0. Intensity does not shift this edge. The only exception in 1800 storms is one ISLE storm with damage 0.01 at q_min = 2.0016, a crossing between two 2-s observations.
- **Gaussian shape.** Define Z = log(-log(1 - frac)), where frac = damage / remaining value. Exposure models then become Z = log k + log H + noise, so a lower residual sd means a better shape.

PORT comparison (`field_compare.py`):

| field / functional | sd(Z) train | sd(Z) holdout | holdout RMSE (pts) |
|---|---|---|---|
| **Gaussian exp(-(d/r)^2), cut at 2r** | **0.3417** | **0.3365** | **5.33** |
| exp(-(d/r)^b), b free (fitted b = 1.993), cut 2r | 0.3417 | 0.3371 | 5.34 |
| Gaussian, no cut | 0.4152 | 0.3988 | 5.50 |
| modified Rankine (1 inside r, (d/r)^-4.61 outside), cut 2r | 0.3778 | 0.3589 | 5.85 |
| linear (1 - q/2)^1.12 | 0.4439 | 0.4700 | 6.34 |
| peak wind only, Z = a + b log(w_max) | 0.5200 | 0.4681 | 6.61 |
| exponential exp(-d/r), cut 2r | 0.6806 | 0.6958 | 8.99 |
| Rankine (q inside, 1/q outside), cut 2r | 0.9461 | 0.9487 | 12.77 |

- **Edge test (`edge_port.py`).** With the Gaussian, the mean of log(implied H / H) stays flat across q_min bins all the way to the edge: -3.79 for q_min below 0.5, -3.91 for q_min in [1.95, 2). With the linear field it drifts from -3.40 to +1.92.
- **Hazard is linear in intensity.** A free intensity exponent p fits 0.92-1.16 by company, with no holdout gain, so p = 1.
- **Integrated exposure beats every alternative.** On round-1 storms it beats closure-time-only, excess-over-0.35 and closure-time-added models; the closure-time weight fits to about 0 (`closure_feat.py`).

**Wind formula:** `w(d) = I * exp(-(d/r)^2)` for d < 2r, else 0.

## 2. Site damage function

**Key structural finding: damage is applied to the remaining value.** The final-report damage_pct is the damage this storm added, as a percent of the original value. A site that is already damaged loses a fraction of what is left. Example, PORT in season 357: earlier rounds did 35.2 + 41.67, and the round-3 report of 15.87 is 68.6% of the remaining 23.13. Using remaining value raised the rank correlation of PORT damage with exposure from 0.82 to 0.94.

**Fitted model (`fit_mle.py`, maximum likelihood):**

```
H_s   = sum over 2-s observations of 2 * w_s(t)            (wind-seconds)
f_s   = 1 - exp(-k_c * m * H_s)                            (fraction of REMAINING site value destroyed)
log m ~ N(0, sigma_c^2): one draw per company per storm, shared by all of that company's sites
D_c   = 100 * sum_s weight_s * rem_s * f_s                 (reported damage_pct)
rem_s = product over earlier storms of (1 - f_s)
```

Given a reported D_c, m is solved exactly because D_c increases with m. The likelihood is φ(log m / σ) / σ divided by |dD/d log m|. Fully destroyed sites are treated as censored observations. The m solved from earlier rounds' final reports gives the exact per-site remaining value.

**Fitted parameters** (seasons 1-400; SE from a 40-replicate season bootstrap, `bootstrap.py`):

| company | k (per wind-second) | relative SE of k | k / k_PORT | sigma (sd of log m) | SE of sigma |
|---|---|---|---|---|---|
| PORT | 0.010997 | 1.3% | 1.000 | 0.3417 | 0.009 |
| ISLE | 0.008684 | 1.0% | 0.790 | 0.3507 | 0.007 |
| AQUA | 0.015515 | 1.6% | 1.411 | 0.3568 | 0.009 |
| GRID | 0.011654 | 1.3% | 1.060 | 0.3674 | 0.006 |
| FERRY | 0.008476 | 0.9% | 0.771 | 0.3554 | 0.006 |

**Vulnerability multiplier.** One k per company is enough. Per-site k lowers training NLL by only 0.7-5.2 for 2-4 extra parameters and does not improve holdout NLL. The company descriptions in case.json do not map onto damage the way their wording suggests:
- ISLE ("light construction") and FERRY ("breaks only a little") have the lowest k, and there is no cap.
- AQUA has the highest k.
- I tested boat-shelter mechanisms for the AQUA fleets: fleet hazard counted only before the site closes, only before the island impact, only below the threshold, or only above it. All are far worse (training NLL 2782-10509 against 1646 for the base model).
- These descriptions probably describe value or revenue effects (bookings, outage, fares), which is a different area's job.

**The noise is real, lognormal and unpredictable:**
- **Lognormal.** Standardised quantiles match the normal: 1% at -2.41 vs -2.33, 99% at 2.31 vs 2.33; kurtosis 2.91.
- **Constant across exposure.** sd(Z) is 0.29-0.37 in every H bin.
- **Not explained by storm features.** Regressing PORT's Z on 13 storm features (intensity, radius, speed, q_min, timing, round, prior damage, season, ...) gives R² 0.02.
- **Independent across companies in the same storm:** |corr| below 0.08 for all 10 pairs.
- **Independent across rounds** for the same company: corr between -0.08 and +0.04.
- **Unbiased by pattern** in damage space: every pattern-by-company mean residual is within about 2 SE.
- **Shared within a company.** The company-level implied sd does not shrink when several sites contribute; it is still 0.35-0.40 at effective site count 1.6-2.2. So m is one draw per company per storm, not per site.
- **At the floor.** The model's predicted RMSE matches the realised holdout RMSE: PORT 5.67 vs 5.33, ISLE 2.43 vs 2.48, AQUA 2.72 vs 2.71, GRID 3.38 vs 3.15, FERRY 3.41 vs 3.16 (`noise_floor.py`).
- **Time step does not matter.** Integrating at 1-s or 0.5-s interpolated steps instead of 2-s observations leaves sd(Z) unchanged (0.3417 / 0.3420 / 0.3421).

## 3. Closure / hurricane conditions

Rule: a **site is closed while its wind is at least 0.35**. The impact message for an island is the first second at which any company site on that island reaches w ≥ 0.35.

Where the threshold comes from (`impact2.py`, all 1800 storms):

| quantity | value |
|---|---|
| largest site wind at any observation before the impact age | 0.3504 |
| smallest site wind at an even-second impact age | 0.3493 |
| largest site wind ever on islands with no impact message | 0.3493 |
| estimated threshold | 0.350 ± 0.001 (spread is rounding in the published observations) |

**Ruled out:** the nearest point of the island polygon (wind at impact has a median of 0.744, and non-impacted islands reach up to 1.35) and the island centroid.

**Holdout accuracy** (linear interpolation to 1 s, `closure()` in the model):

| | island-storms | yes/no agreement | false alarms | missed | timing exact | within 1 s | within 2 s | mean abs error |
|---|---|---|---|---|---|---|---|---|
| train | 4800 | 99.98% | 0 | 1 | 83.0% | 98.7% | 99.3% | 0.25 s |
| holdout | 2400 | 99.92% | 0 | 2 | 81.4% | 99.0% | 99.7% | 0.21 s |

The 18 mismatches in 7200 island-storms are all cases where the true wind touched 0.35 at an unobserved odd second.

The model also reports **closure seconds per site**. Only the first closure per island can be checked against the feed; durations follow the packet rule ("reopens when the wind drops back below") with the fitted threshold.

## 4. Insured loss

`loss1.py`, OLS with no intercept:

**industry_loss_musd = 9.0·PORT + 6.0·ISLE + 2.5·AQUA + 7.0·GRID + 1.2·FERRY**

- Inputs are damage in percentage points.
- Fit: train R² 0.99999997, RMSE $0.044M, which is report rounding. Holdout is identical.
- Implied insured value at 100% damage: PORT $900M, ISLE $600M, AQUA $250M, GRID $700M, FERRY $120M.
- `season_loss_musd` is the sum of the three storm losses (max difference 0.1).
- The preliminary loss equals the same formula applied to the preliminary damages (noise sd 0.32).

In real time, expected loss = Σ coefficient_c × E[D_c]. Its sd comes from summing the independent company variances.

| holdout (600 storms) | R² | RMSE ($M) | MAE | bias | z-score sd |
|---|---|---|---|---|---|
| model, from the track (at storm end) | 0.954 | 55.9 | 35.7 | -2.1 | 0.95 |
| preliminary report alone | — | 105.0 | — | — | — |
| model + preliminary report (posterior) | 0.968 | 46.5 | 30.1 | -1.1 | — |

Season loss using only the model for all three storms: holdout R² 0.912, RMSE $97.0M. In the live season, rounds 1-2 are known exactly from the final reports before round 3.

## 5. Holdout accuracy per company (seasons 401-600, 600 storms)

"Model" means the expected locked-in damage at storm end, using remaining value from the earlier rounds' exact final reports (what the bot will know).

| company | R² | RMSE | MAE | bias | R², model-only remaining | RMSE, model-only remaining | RMSE, prelim alone | R², model+prelim | RMSE, model+prelim | 80% PI coverage | mean final |
|---|---|---|---|---|---|---|---|---|---|---|---|
| PORT | 0.9477 | 5.33 | 2.71 | -0.23 | 0.9405 | 5.69 | 9.84 | 0.9645 | 4.39 | 0.885 | 16.7 |
| ISLE | 0.9527 | 2.48 | 1.31 | +0.10 | 0.9497 | 2.56 | 4.56 | 0.9709 | 1.95 | 0.872 | 8.5 |
| AQUA | 0.9681 | 2.71 | 1.45 | -0.02 | 0.9641 | 2.88 | 6.63 | 0.9771 | 2.30 | 0.848 | 12.7 |
| GRID | 0.8955 | 3.15 | 2.07 | -0.04 | 0.8869 | 3.27 | 6.08 | 0.9181 | 2.78 | 0.843 | 13.8 |
| FERRY | 0.9100 | 3.16 | 2.10 | -0.30 | 0.9047 | 3.25 | 5.81 | 0.9403 | 2.57 | 0.818 | 12.9 |

- **Train (seasons 1-400) R² / RMSE:** PORT 0.947 / 5.24, ISLE 0.953 / 2.51, AQUA 0.959 / 2.99, GRID 0.892 / 3.22, FERRY 0.892 / 3.36. There is no overfitting.
- **Median calibration:** the final is below the predicted median in 48-51% of damaged storms.
- **Preliminary report model:** log(prelim/final) ~ N(0, 0.30²), constant across damage levels (0.29-0.33). It is independent of the final multiplier: cov(u_prelim, u_final) / var(u_final) = 0.93-1.06.
  - Alone, the preliminary report is much worse than the model.
  - `posterior_given_prelim()` combines the two by Bayes on a grid. This is the best estimate between real 570 s (prelim) and 740 s (final).

## 6. How early in a storm is the damage known?

The model gives the damage locked in by the track observed so far. It is not a forecast of the remaining path (that belongs to the track-forecast area).

Holdout, share of the end-of-storm expected damage already locked in at age t (real second in the round = 60 + t), and R² of the locked-in estimate against the final report:

| age (real s) | share locked in | R² PORT | R² ISLE | R² AQUA | R² GRID | R² FERRY |
|---|---|---|---|---|---|---|
| 120 (180) | 0.025 | -0.51 | -0.52 | -0.65 | -1.97 | -1.30 |
| 200 (260) | 0.145 | -0.51 | -0.25 | -0.26 | -1.67 | -0.43 |
| 240 (300) | 0.261 | -0.50 | 0.12 | 0.30 | -1.33 | 0.03 |
| 280 (340) | 0.420 | -0.39 | 0.55 | 0.75 | -0.74 | 0.48 |
| 300 (360) | 0.508 | -0.25 | 0.71 | 0.86 | -0.36 | 0.65 |
| 320 (380) | 0.602 | -0.01 | 0.82 | 0.92 | 0.04 | 0.76 |
| 340 (400) | 0.699 | 0.29 | 0.89 | 0.95 | 0.40 | 0.83 |
| 360 (420) | 0.790 | 0.57 | 0.92 | 0.96 | 0.65 | 0.87 |
| 380 (440) | 0.868 | 0.77 | 0.94 | 0.96 | 0.79 | 0.89 |
| 400 (460) | 0.927 | 0.88 | 0.95 | 0.97 | 0.86 | 0.90 |
| 420 (480) | 0.968 | 0.93 | 0.95 | 0.97 | 0.89 | 0.91 |
| 440 (500) | 0.990 | 0.95 | 0.95 | 0.97 | 0.90 | 0.91 |
| 480 (540) | 1.000 | 0.95 | 0.95 | 0.97 | 0.90 | 0.91 |

Age at which 10% / 50% / 90% of a company-storm's damage is locked in (holdout, company-storms with E[D] > 1), median with [p10, p90]:

| company | 10% | 50% | 90% |
|---|---|---|---|
| PORT | 320 [280, 360] | 360 [320, 400] | 420 [380, 440] |
| ISLE | 220 | 260 [140, 320] | 320 [180, 400] |
| AQUA | 200 | 260 [120, 320] | 320 [160, 380] |
| GRID | 260 | 320 [220, 360] | 380 [260, 420] |
| FERRY | 160 | 260 [160, 320] | 340 [200, 400] |

By pattern (all companies pooled), median age at 50% / 90%: direct 320/380, chain 320/360, recurve 220/260, stall 280/380, eyewall 280/360, erratic 160/180.

The weakening over the last 60 s means little damage is added after age 440. For a damage estimate before the storm has passed, append forecast points (for example the advisory cone) to the observations and call the same function. The result is then only as good as the forecast.

**Example, season 1 round 3, storm Fay (`demo_fay.py`).** The expected PORT damage at ages 280 / 320 / 360 / 400 / 480 is 2.1 / 14.9 / 41.6 / 60.8 / 66.0. The 80% interval at the end is [50.4, 81.4]; the actual final was 84.55. GRID was expected at 27.4 (interval [19.5, 35.5]); the actual was 15.8. The predicted Cascadia impact at age 274 matched exactly.

## 7. Storm dynamics, for forward projection (`intensity_dyn.py`)

- **Weakening:** for t in [420, 480], I(t) = I(420)·(480 - t)/60. Measured mean ratios at ages 430-470 are 0.836, 0.669, 0.503, 0.335, 0.168, against linear 0.833 ... 0.167.
- **Before age 420:** intensity is a random walk with 2-s step sd 0.0293. It is mildly mean-reverting (inc = 0.0143 - 0.0117·I per 2 s; level about 1.22, half-life about 119 s).
- **Levels and radius by pattern:**
  - Mean intensity over ages 200-400: stall 1.38, direct 1.28, chain 1.14, eyewall 1.09, recurve 1.00.
  - Mean radius at age 420: eyewall 159, stall/direct about 100, chain 80, recurve 68.

## 8. Pure-function spec (`work/se/physics/damage_model.py`, standard library only)

```python
import sys; sys.path.insert(0, ".../work/se/physics"); import damage_model as dm
r = dm.evaluate_storm(obs, remaining=None, track=None)   # obs: [(age, x, y, radius, intensity), ...]
```

Returns a dict:

| key | meaning |
|---|---|
| `hazards[17]` | H_s per site, in wind-seconds |
| `site_damage_frac[17]` | expected loss as a fraction of the ORIGINAL site value |
| `company_damage_pct{c}` | expected damage_pct, locked in so far |
| `company_damage_q{c}{0.1, 0.5, 0.9}` | exact quantiles; D increases with m, so they are D at m = exp(σ z_p) |
| `insured_loss_musd`, `insured_loss_sd` | expected storm loss and its sd |
| `closed_seconds[17]`, `closed_now[17]`, `first_closed_age[17]` | per-site closure |
| `island_first_age{island}` | predicted impact second |
| `remaining_expected[17]` | expected remaining value after the storm |
| `last_age` | latest age used |

Site order is the `SITES` constant (the case.json order); `sites_from_case(case_json)` rebuilds it.

Other functions:
- **`dm.StormTracker(remaining)`** for the live feed: `.add_storm(msg)` for every storm message (deduped by age), `.add_track(msg)` for track messages (fills gaps), `.result()` returns the dict above (about 1 ms), and `.finish(final_damage_pct)` returns the per-site remaining values for the next round.
- **`dm.update_remaining(remaining, hazards, final_damage_pct)`** solves each company's m exactly from the final report and returns the per-site remaining values.
- **`dm.posterior_given_prelim(hazards, prelim_damage_pct, remaining)`** returns E[final damage_pct | preliminary report].
- Lower level: `wind`, `fill_obs` (regularises to the 2-s grid using track points, interpolates intensity, and applies the linear weakening if extrapolating past 420), `site_hazards`, `closure`, `expected_damage`, `insured_loss`, `solve_multiplier`. Parameters are in `dm.PARAMS`.
- **Gap robustness** (`gap_test.py`, holdout round 1, filling from the track). The figure is the RMSE between the gapped and full-data estimates, in damage points:
  - 30% of observations dropped: 0.02-0.03
  - 70% dropped: 0.06-0.11
  - a 60-s outage plus 10% dropped: 0.12-0.45
  - Accuracy against the final report is unchanged in all three cases.

## 9. What can and cannot be known in real time

**Known from the feed, every 2 s:**
- the wind at every site
- which sites are closed and their accumulated closure seconds
- island impacts, predicted to the second
- each company's locked-in expected damage, with calibrated intervals
- the expected insured loss of the storm
- the season-to-date insured loss: earlier storms are known exactly from their final reports through the linear formula
- after each final report, the exact per-site remaining value, which matters for later storms hitting already-damaged sites

**Not knowable before the reports:** the company-storm multiplier m. Its sd of about 0.35 in log hazard means about ±35% on the damage of a lightly hit company, and less once a site is near saturation.
- At the storm end (real 540 s) the error is the floor in §5.
- The preliminary report (real 570 s) cuts it by about 20%.
- The final report (real 740 s) gives the exact damage.
- The future path is also unknown; damage locks in mostly after age 250-300.

## 10. Caveats

- **Live season.** Announcements could change a company's economics or vulnerability (k), and the research data cannot show that. Re-solve m from each final report anyway; a persistent shift in implied m across rounds would reveal a change.
- **Per-site remaining value.** Inferring it from company-level reports relies on the finding that m is shared by a company's sites. Under per-site noise the split across sites would be model-based.
- **Closure durations** are not directly validated (only first impacts are).
- **The 0.35 threshold** carries about ±0.001 uncertainty from observation rounding. The interpolation used for odd seconds misses about 0.25% of island impacts.
- **Expected vs most likely.** The expected damage integrates over the lognormal m, so it is above the damage at the median m. Use `company_damage_q` for the median and the tails.
- **Report rounding.** Final reports are rounded to 0.01 and preliminary reports to 0.1. Tiny damages (below 0.05) are noisy in relative terms.

## Reproduce

From `work/se/physics`, with DATA = the research data folder:

1. `python3 -I extract.py DATA/history storms.json`
2. `python3 -I features.py storms.json DATA/case.json site_series.json`
3. `python3 -I fit_mle.py site_series.json . company mle_company.json`
4. `python3 -I eval_model.py storms.json . eval_rows.json`
5. `python3 -I bootstrap.py storms.json . 40`
6. `python3 -I write_params.py . eval_rows.json bootstrap.json ../fits/physics.json`
7. `python3 -I export_features.py storms.json . site_features.csv company_features.csv`

The diagnostics are `field_compare.py`, `impact2.py`, `prelim_struct.py`, `mult_check.py`, `aqua_shelter.py`, `noise_floor.py`, `timing_stats.py` and `gap_test.py`.
