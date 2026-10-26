# verify:physics

## verdict

confirmed_with_corrections

## issues

# Adversarial verification of the 'physics' fit (wind, damage, closure, insured loss)

## Summary
I rebuilt the whole pipeline independently in `work/se/verify-physics/`:
- my own feed extractor (`vx_extract.py`) and per-site distance cache (`vx_sites.py`)
- my own hazard, a bisection solver for L = log(k*m), and normal quadrature (`vlib.py`)
- my own fit (`vx_fit.py`) and censored MLE (`vx_cens.py`)
- my own holdout evaluation (`vx_holdout.py`, `vx_post.py`, `vx_time.py`)

Every headline number reproduces to 3-4 decimals:
- k and sigma: identical for PORT, ISLE, GRID and FERRY. AQUA is 0.015513 / 0.3561 from my censored MLE, against the fitter's 0.015515 / 0.3568.
- All holdout R2, RMSE, MAE and bias.
- Loss coefficients, closure threshold, model+prelim posterior, and the time-profile R2.

I found no look-ahead leakage that matters, no timing off-by-one and no overfitting. The fair-value formula and parameters stand. The corrections are about how some claims are stated, plus a few minor items.

## Issues, most severe first

1. **MEDIUM: the "damage locks in late" shares are in unweighted damage points.**
   - "51% by age 300, 79% by 360, 93% by 400, 97% by 420" is the sum of E[D] in points over all five companies. I reproduce it exactly: 0.508 / 0.790 / 0.927 / 0.968.
   - Weighted by insured dollars (what HAVEN and REIN, and the season loss, depend on), the share is much lower:

     | age (real s) | 240 (300) | 300 (360) | 340 (400) | 360 (420) | 380 (440) | 400 (460) | 420 (480) | 440 (500) |
     |---|---|---|---|---|---|---|---|---|
     | $-weighted share | 0.164 | 0.370 | 0.588 | 0.707 | 0.814 | 0.898 | 0.956 | 0.986 |
     | PORT alone | 0.01 | 0.10 | 0.35 | 0.53 | 0.69 | 0.83 | 0.93 | 0.98 |

   - At age 300, PORT ($9M per point) is 10% locked in, while AQUA is 80%.
   - The bot's insurer and PORT logic must use the per-company curves (in `physics.verified.json`), not the pooled 51%.

2. **LOW-MEDIUM: 80% interval coverage of "82-89%" is inflated.**
   - Company-storms with zero hazard (PORT is untouched in about 45% of storms) are always trivially "covered".
   - Restricted to hazard > 0 (holdout), coverage is PORT 0.790 (n 328), ISLE 0.819 (421), AQUA 0.835 (486), GRID 0.843 (591), FERRY 0.818 (588).
   - So the intervals are calibrated: slightly wide for GRID and AQUA, not over-covering.

3. **LOW: minor holdout leakage in three auxiliary estimates.** None of them changes any metric.
   - `prelim_log_sd` = 0.302 was estimated over seasons 1-600 (`prelim_struct.py` loops over 1..600). Train-only gives 0.3003, and the holdout posterior RMSE is unchanged (PORT 4.393, loss $46.53M).
   - The 0.35 threshold and the 2r cut-off test used all 1800 storms. A train-only threshold scan (0.345-0.355) is still best at 0.350.
   - The cut-off is crisp everywhere: the largest q_min among the 931 damaged PORT storms is 1.99061, and the smallest q_min among undamaged storms is 2.00136.

4. **LOW: sigma is less stable than the bootstrap SE suggests.**
   - Holdout sigma is below train sigma for all five companies (pooled 0.3373 vs 0.3557, about 3 SE). The gap comes from seasons 401-500 (company sigmas 0.317-0.329); seasons 501-600 are back at about 0.35.
   - The company sigmas are statistically indistinguishable from each other. The pooled value over all 600 seasons is 0.3497.
   - This matters only for interval widths. With a pooled sigma of 0.3557, holdout point RMSE is identical (PORT 5.333, loss $55.92M).

5. **INFO: AQUA needs censoring, and the fitter handles it correctly.**
   - Excluding the 8 right-censored train cases (sites already near zero remaining value, or one extreme multiplier: season 35 round 2, D = 45.00 = cap, z of about 2.6) gives k = 0.015466.
   - Censored MLE gives 0.015513, matching the fitter.

6. **INFO: the "18 mismatches in 7200 island-storms" wording.**
   - I count 3 yes/no mismatches: 1 train miss and 2 holdout misses, with 0 false alarms.
   - The rest of the 18 would be timing errors of more than 1-2 s. I did not check those individually.

7. **INFO: the "noise floor" claim survives a market test.**
   - I regressed the house consensus change between real 532 s and 568 s (before the prelim) on E[D] and (D - E[D]).
   - Loadings on (D - E[D]): PORT -0.0017, ISLE +0.0050, AQUA +0.0041, GRID +0.0005, FERRY +0.0007 $/pt, all with R2 at most 0.002.
   - After the prelim (568 to 600 s), the price does load on (D - E[D]): PORT -0.150, with R2 0.28-0.33.
   - So the house does not reveal m before the prelim, and neither does the feed.

## Checks that passed
- **Data integrity.**
  - 1800 storms, each with 241 observations at ages 0..480, with no gaps or duplicates.
  - All 217,800 last-track points equal the observations.
  - Final report equals the manifest in all 1800 storms.
  - Prelim is at real 570 and final at real 740 in all 1800 rounds.
- **Timing.**
  - A storm message at age t is time-stamped at real 60 + t, and impact ages map the same way.
  - 1325 of 2631 impacts are at odd ages, so the simulator ticks at 1 s.
  - A hazard from 1-s linear interpolation gives k = 0.011000 and sd 0.3420, against 0.010997 and 0.3417 from the 2-s sum. The step size does not matter.
- **No leakage in the main evaluation.**
  - `eval_model.py` evaluates each storm, then calls `update_remaining` with that storm's final.
  - Remaining value therefore uses only the earlier rounds' finals, which are public before the next round.
  - Running the fitter's `StormTracker` on raw feed messages (seasons 401-430) matches my independent E[D] to 0.0000.
- **No overfitting.**
  - 10 fitted parameters (5 k, 5 sigma) plus 3 structural constants (2r cut-off, exponent 2, threshold 0.35), against about 7,200 informative company-storms.
  - Train and holdout RMSE are equal.
  - log k shows no trend over seasons: slope per 100 seasons between -0.006 and +0.006, all within 1.2 SE.
  - No per-round or per-pattern bias.

## Alternative functional forms
Table entries are the train sd of L = log(k*m), then the holdout rms of L - log k_train. Lower is better.

| form | PORT | ISLE | AQUA | GRID | FERRY |
|---|---|---|---|---|---|
| **Gaussian, cut at 2r, linear in I (fitter)** | **.3417/.3360** | **.3507/.3445** | **.3546/.3312** | **.3674/.3349** | **.3554/.3403** |
| I^0.9 | .3416/.3352 | .3517/.3446 | .3551/.3321 | .3686/.3347 | .3554/.3413 |
| I^1.1 | .3424/.3374 | .3504/.3451 | .3547/.3308 | .3668/.3356 | .3561/.3398 |
| w^0.9 | .3562/.3592 | .3565/.3547 | .3696/.3532 | .3666/.3352 | .3635/.3464 |
| w^1.1 | .3595/.3441 | .3948/.3801 | .3710/.3422 | .3892/.3611 | .3685/.3542 |
| cut at 1.95r | .3653/.3655 | .3977/.4073 | .3988/.3818 | .3771/.3392 | .3604/.3434 |
| cut at 2.05r | .3595/.3693 | .3718/.3947 | .3936/.3729 | .3759/.3369 | .3712/.3491 |
| cut at 2.2r | .3966/.4113 | .4029/.4520 | .4462/.4420 | .3877/.3419 | .3957/.3741 |

Further PORT-only tests (train / holdout):

| form | train | holdout |
|---|---|---|
| no cut-off | .4151 | .4313 |
| Gaussian shifted to be continuous at 2r | .5427 | .5603 |
| Gaussian with (1-(q/2)^2) taper | .8191 | .8187 |
| radius scale 0.95 | .3608 | .3439 |
| radius scale 1.05 | .3538 | .3569 |
| excess over 0.05 | .8314 | .9062 |
| shape exponent b = 1.95 | .3433 | .3419 |
| shape exponent b = 2.00 | .3417 | .3360 |
| shape exponent b = 2.05 | .3446 | .3344 |
| 1-s interpolation | .3420 | .3356 |

The intensity exponent cannot be distinguished from 1. Nothing beats the fitter's form.

**Damage function.** Proper likelihood with the Jacobian, PORT, D >= 0.5, n = 593 train / 320 holdout. Lower NLL is better.

| damage function | train NLL | holdout NLL |
|---|---|---|
| 1 - exp(-kmH) on remaining value, slope 1 | **-771.4** | -410.7 |
| power law f = (kmH)^b, free b = 0.846 | -699.4 | -357.7 |
| logistic, free slope | -706.6 | -396.8 |
| generalized Pareto, lambda = +0.25, free slope | -765.3 | -412.3 |
| generalized Pareto, lambda = -0.25, free slope | -768.3 | -403.7 |

- Exponential wins on train by 3 to 72 NLL units over every alternative and is effectively tied best on holdout. Its slope of Z on log H is 1.001 (train) and 1.020 (holdout).
- Applying damage to the original value instead of the remaining value is clearly wrong: sd 0.70 vs 0.34. For storms with earlier PORT damage above 5 points, its mean residual is -0.948 against +0.015.

## Edge cases (holdout unless stated)

| case | finding |
|---|---|
| zero-damage storms (9 in all, 3 in holdout) | predicted loss 0.000 for all 3 holdout storms |
| damage without hazard | one case in 1800: ISLE, season 262 round 2, 0.01 pts at q_min 2.0016 (a crossing between 2-s observations) |
| erratic (n 55) | loss z sd 0.83, company z sd 0.91 |
| turned erratic mid-storm (n 40) | loss RMSE $32.5M |
| stall (n 89) | loss bias +$5.6M, z mean -0.19 (1.7 SE) |
| sites already damaged, rem < 0.5 (n 251) | z sd 0.90, bias +$0.5M |
| all other zero, saturated or censored cases | sites with remaining value near zero, or tiny hazard; no sign of a damage cap or AQUA fleet sheltering |
| per-site k (storms with one site at 90%+ of the damage) | every site within 2 SE of its company k |

- Pattern-level mean log residuals are within ±0.04 for the main patterns. The one notable cell is ISLE erratic, +0.217 (n 22, about 3 SE), out of about 55 cells tested.
- Residual u is normal: kurtosis 3.00, 1% / 99% quantiles at -2.30 / +2.34.
- Cross-company correlation of u within a storm is at most 0.051 in absolute value.
- sd(u) does not shrink as the effective number of hit sites grows, so m is shared by a company's sites. GRID shows a slight shrink (0.358 to 0.318), which allows a small per-site part.

## What can and cannot be known in real time
**Known live from the feed:**
- the wind at each site
- closure state and island impacts (the 0.35 threshold is exact)
- the expected damage locked in by the track observed so far
- the exact insured-loss mapping
- the exact remaining value per site after each final report

**Not knowable before the prelim (real 570 s):** the company-storm multiplier m. Its log sd is about 0.35, and it is not revealed by the feed, by the house prices or by the other companies. The prelim cuts the error about 20%, and the final report (real 740 s) gives the exact damage.

**The future path is not knowable,** and the dollar loss locks in late: only 37% by age 300 and 71% by age 360.

## corrections

The fitted formulas and parameters stand. Keep them for the bot:
- wind w = I*exp(-(d/r)^2) for d < 2r
- H = sum of 2*w over the 2-s observations
- f = 1 - exp(-k*m*H) applied to the remaining value, with log m ~ N(0, sigma)
- k: PORT 0.010997, ISLE 0.008684, AQUA 0.015515, GRID 0.011654, FERRY 0.008476
- sigma: PORT 0.3417, ISLE 0.3507, AQUA 0.3568, GRID 0.3674, FERRY 0.3554
- loss = 9.0*PORT + 6.0*ISLE + 2.5*AQUA + 7.0*GRID + 1.2*FERRY
- closure while w >= 0.35 at the company sites
- prelim = final * exp(N(0, 0.30^2))

Corrections to the stated claims:
1. **Lock-in shares.** Use the insured-dollar-weighted shares, 0.164 / 0.370 / 0.588 / 0.707 / 0.814 / 0.898 / 0.956 / 0.986 at ages 240 / 300 / 340 / 360 / 380 / 400 / 420 / 440, or the per-company curves, instead of "51% by age 300". PORT is only 10% locked in at age 300 and 53% at age 360.
2. **Interval coverage.** On storms with hazard > 0, holdout coverage of the 80% interval is 0.79-0.84, not 82-89%. The intervals are calibrated.
3. **Prelim sd.** The train-only estimate is 0.3003, not 0.302. The difference is immaterial.
4. **Optional production refit on all 600 seasons** (censored MLE):
   - k: PORT 0.011022, ISLE 0.008646, AQUA 0.015499, GRID 0.011664, FERRY 0.008560
   - sigma: PORT 0.3397, ISLE 0.3487, AQUA 0.3482, GRID 0.3570, FERRY 0.3503
   - A pooled sigma of 0.35 is also acceptable.
   - Point accuracy is unchanged. All of this is written in `physics.verified.json`.

## corrected_params_file

/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/fits/physics.verified.json

## independent_metrics

## Independent holdout metrics (seasons 401-600, 600 storms)
Fit on seasons 1-400, evaluated with my own code (`vx_holdout.py`, `vx_post.py`, `vx_time.py`). Remaining value comes from the exact final reports of the earlier rounds.

**Expected damage at storm end vs the final report.** The noise floor is the RMSE the model itself predicts.

| company | R2 | RMSE | MAE | bias | noise floor | RMSE, median predictor |
|---|---|---|---|---|---|---|
| PORT | 0.9477 | 5.334 | 2.713 | -0.233 | 5.666 | 5.369 |
| ISLE | 0.9527 | 2.483 | 1.314 | +0.102 | 2.425 | 2.510 |
| AQUA | 0.9681 | 2.714 | 1.454 | -0.021 | 2.716 | 2.719 |
| GRID | 0.8955 | 3.146 | 2.069 | -0.036 | 3.376 | 3.148 |
| FERRY | 0.9100 | 3.161 | 2.101 | -0.297 | 3.411 | 3.197 |

My own train fit with uncensored AQUA (k 0.015466) gives the same results: AQUA RMSE 2.715.

**Other estimators, R2 / RMSE in damage points:**

| company | model-only remaining | model + prelim posterior | prelim alone (RMSE) |
|---|---|---|---|
| PORT | 0.9405 / 5.688 | 0.9645 / 4.393 | 9.844 |
| ISLE | 0.9497 / 2.562 | 0.9709 / 1.947 | 4.556 |
| AQUA | 0.9641 / 2.879 | 0.9771 / 2.302 | 6.628 |
| GRID | 0.8869 / 3.272 | 0.9180 / 2.786 | 6.075 |
| FERRY | 0.9047 / 3.252 | 0.9402 / 2.577 | 5.814 |

**80% interval coverage:**

| company | all storms | hazard > 0 only |
|---|---|---|
| PORT | 0.885 | 0.790 (n 328) |
| ISLE | 0.873 | 0.819 (n 421) |
| AQUA | 0.867 | 0.835 (n 486) |
| GRID | 0.845 | 0.843 (n 591) |
| FERRY | 0.820 | 0.818 (n 588) |

**Insured loss per storm:**
- Model from the track alone: R2 0.9539, RMSE $55.93M, MAE 35.68, bias -2.15, z-score sd 0.947.
- Model + prelim: R2 0.9681, RMSE $46.53M.
- Prelim alone: R2 0.838, RMSE $104.97M, bias +16.0.
- Season loss with the model for all 3 storms: R2 0.912, RMSE $97.04M.

**Holdout rms of log(k*m) around the train log k:** PORT 0.3360, ISLE 0.3445, AQUA 0.3312, GRID 0.3349, FERRY 0.3403. Train sigmas are 0.3417-0.3674.

**Time profile.** R2 of the locked-in damage at age t, against the final report. It matches the fitter exactly.

| age | PORT | ISLE | AQUA | GRID | FERRY |
|---|---|---|---|---|---|
| 300 | -0.25 | 0.71 | 0.86 | -0.36 | 0.64 |
| 360 | 0.57 | 0.92 | 0.96 | 0.65 | 0.87 |
| 400 | 0.88 | 0.95 | 0.97 | 0.86 | 0.90 |
| 420 | 0.93 | 0.95 | 0.97 | 0.89 | 0.91 |

**Closure.** Threshold 0.350 at the company sites, linear interpolation to 1 s.
- Holdout: yes/no agreement 0.99917 (0 false alarms, 2 missed); timing exact 0.806, within 1 s 0.988 (885 impacts).
- Train: agreement 0.99979; exact 0.828, within 1 s 0.987.
- Bounds on the threshold: the largest site wind before an impact is 0.3504; the smallest wind at an even-second impact is 0.3493; the largest wind on islands with no impact is 0.3493.

**Insured loss formula.** OLS without an intercept gives 9.0001, 6.00035, 2.49992, 7.00008, 1.1997.
- Residual rms is 0.044 (train) and 0.0436 (holdout); the largest residual is 0.144.
- Season loss equals the sum of the three storm losses (largest difference 0.1).

**Preliminary report.**
- log P = -0.001 + 1.033 log D - 0.031 log E (pooled, train).
- sd of log(P/D) is 0.294-0.304 by company and flat across damage levels (0.296-0.309).
- Cross-company correlation of the prelim noise is at most 0.094 in absolute value.

**Storm dynamics.**
- Every storm starts at I = 1.0 and r = 80.
- I/I(420) at ages 430/440/450/460/470 is 0.8359/0.6687/0.5027/0.3353/0.1677, matching the linear decay.
- The 2-s intensity increment has sd 0.0293 and lag-1 autocorrelation -0.005; inc = 0.0143 - 0.0117*I.

**The fitter's live code (`StormTracker`).** Seasons 401-430, raw feed messages:
- Matches my independent E[D] to 0.0000.
- With a 60-s outage plus 30% of storm messages dropped, the deviation is 0.05-0.27 pts RMS (1.16 at most).
- `result()` takes 1.2 ms.
