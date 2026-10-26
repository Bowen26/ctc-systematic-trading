# verify:insurance

## verdict

confirmed_with_corrections

## issues

Everything below comes from my own scripts in `SP/work/se/verify-insurance/` (`v_*.py`, outputs in `*.out`), run as `python3 -I`, stdlib only, and none of them writes to the data folder. Fits use seasons 1-400 and holdout metrics use 401-600. In a few places I also ran a reverse fold (library 401-600 predicting 1-400) as extra evidence about the model's structure; no delivered parameter is fitted on it.

**What holds (no issue):**
- **Settlement formulas.** HAVEN = 75 - 0.06*clip(L-400, 0, 900) and REIN = 90 - 0.035*max(L-1300, 0) reproduce exactly. I used a different method (OLS on the interior of the layer plus bracketing of the flat regions):
  - HAVEN slope -0.0600011 (se 8e-7), R = 400.01, T = 1299.99.
  - REIN slope -0.0350073 (se 3e-6), attachment 1300.03.
  - Holdout: HAVEN RMSE 0.00288 (max 0.006), REIN RMSE 0.00159 (max 0.0065).
  - Quadratic, cubic and mid-layer-kink forms are not significant (t = -0.43 / 0.81 for the quadratic term) and do not improve the holdout.
  - All 29 seasons with L < 400 settle at 75 / 90, and all 118 with L > 1300 have HAVEN at 21. The REIN top must be at least about 1760: a top at 1750 already gives a max error of 0.57.
- **Loss formula.** K = 9.0001, 6.00035, 2.49992, 7.00008, 1.1997 (se of 2e-4 or less; a free intercept comes out at -0.002). Holdout RMSE is 0.0436.
- **Preliminary noise.** prelim = final * exp(0.30 z) holds: the log sd per company is 0.294-0.305 in train and 0.307-0.324 in holdout.
  - Direction test: corr(log p/f, log f) is between -0.09 and +0.07, while corr(log p/f, log p) is +0.25 to +0.40. So the noise sits on the preliminary, as the fitter assumed.
  - My own posterior estimator (E1) gives holdout bias -1.54 and RMSE 68.97, exactly the fitter's number.
- **Timing.** Every one of the 1800 rounds has market row i = real 2i s. The preliminary report is at real 570 s and the final at 740 s. The house jumps at rows 287 and 372, so 4 s late. Each round opens at the previous close (sd 0.033 HAVEN, 0.074 REIN).
- **No leakage in the library or the fits.** `library.json` equals my train-only raw fractions exactly (1200 storms, max difference 0). ARCH settles at its basket to within 6e-14.
- **Model-free house edges** reproduce to the cent (table in metrics).
- **Params file.** It exists, parses, and matches the report and `insmodel.py`.

**Problems found:**

1. **MODERATE: the pooled library B is biased at depleted states.**
   - For single-site PORT, depletion is exactly multiplicative (corr of raw fraction with prior damage +0.009). For the multi-site companies it is not: the pattern-demeaned corr(raw, prior D) is ISLE -0.20, AQUA -0.22, GRID -0.13, FERRY -0.09. Once part of a company is damaged, later storms take a smaller share of what is left.
   - B pools raw fractions from all rounds, so it under-predicts damage on a fresh map and over-predicts it on a depleted one. At the round-1 open, B's E[L] by round is 393.6 / 330.4 / 276.9 against actual 407.6 / 330.7 / 257.9. The season total comes out right only because these errors cancel.
   - At the round-3 open, out of sample (both folds, 600 seasons), settlement minus B's prediction is:
     - REIN +0.97 ± 0.42 for C2 of 1100-1300 (n = 73);
     - REIN +2.09 ± 0.62 for C2 > 1300 (n = 19);
     - HAVEN +2.36 ± 0.88 for C2 of 600-900 (n = 175).
   - Within C2 > 1200, B's E[L3] is 177 against an actual 136. A bot using B would sell REIN at the round-3 open in heavy seasons when REIN is in fact fair.
   - This also explains the fitter's round-3 REIN result (model - house -0.26, sign rule t 2.4). Model-free, settlement minus house is only -0.09 ± 0.09, and with model G model - house is -0.11.

2. **MODERATE: the tables' stated uncertainty is too small.** The bootstrap sd (0.05-0.85) leaves out the choice of depletion model. G minus B reaches:
   - HAVEN +1.17 at C2 = 600 and -1.24 at C1 = 0;
   - REIN +0.5 at C2 = 1200 and +0.85 to +0.94 at C2 of 1400-1800;
   - at the round-1 open, 39.88 (G) against 40.49 (B), a 0.6 gap.

3. **MODERATE: the erratic pattern column fails on the holdout.** For erratic-at-formation storms, the holdout mean loss is 35.1 ± 9.0 (n = 15), against 158.8 predicted by B and 174.8 by G from 20 training storms. Five of those 20 training storms exceed 300. So, for example, "storm-1 erratic: E[HAVEN] 47.46" is not reliable. For storms that turn erratic later, the loss depends strongly on when they turn: 71 if before age 120, 312 if after age 360.

4. **LOW-MODERATE: some house-edge claims do not survive out of sample.**
   - The HAVEN mispricing is not significant on the holdout: +0.54 ± 1.18 at the round-1 open, +0.58 ± 1.02 at round 2, +0.65 ± 0.78 at round 3.
   - The reported HAVEN round-2 sign-rule profit of +1.30 (t 2.2) scores the training seasons with a library built from those same seasons. On the holdout alone, B gives +0.31 ± 1.02 and G gives +2.05 ± 1.01.
   - REIN at the round-1 and round-2 opens does hold: model-free -0.54 ± 0.21 and -0.47 ± 0.19 on the holdout. The holdout-only sign rule with B gives +0.61 ± 0.19 (t 3.3).

5. **LOW: arithmetic error in the report.** With no top, REIN at L = 2570 is 90 - 0.035*1270 = **45.55**, not 61.85. 61.85 corresponds to L = 2104. The params file does not contain this number.

6. **LOW: look-ahead in some labelled results.** The pattern columns and the E1p estimator (RMSE 65.6) condition on the manifest's true formation pattern, which a live bot does not have. They are labelled, but they are not achievable in real time.

7. **LOW: what the live L should be.** The bot cannot see `season_loss_musd`. Computing L from the sum of the three final feed reports gives a HAVEN max error of 0.012 (RMSE 0.0036), not 0.006. This is negligible.

8. **LOW: "REIN is non-monotone in C1 between 550 and 700, and this is real."** Under B the dip is at most 0.1 and within the noise of how the table is built. Under G the curve is monotone (88.94 / 88.79 / 88.69 at C1 = 500 / 600 / 700).

9. **INFO: the retention is pinned by extrapolation.** No season has an L between 391.8 and 433.5, so R = 400 comes from extending the line fitted inside the layer to the value 75. Because the slope is identified to 1e-6, this is solid unless the contract has some non-linear feature near the retention.

## corrections

Corrected parameters are in `SP/work/se/fits/insurance.verified.json`. A stdlib module the bot can use is `SP/work/se/verify-insurance/insmodel_g.py` (`build_lib(library.json)`, `ev_one`, `ev_two`, `ev_three`).

**1. Keep unchanged.**
- HAVEN = 75 - 0.06*clip(L-400, 0, 900); REIN = 90 - 0.035*max(L-1300, 0), with no top (top of at least about 1760).
- L_storm = 9*PORT + 6*ISLE + 2.5*AQUA + 7*GRID + 1.2*FERRY.
- prelim = final * exp(0.30 z).
- Live L: the sum of the FINAL feed `industry_loss_musd` values.

**2. Replace the depletion step with model G for values conditional on the state.**
- Damage added: d_c = (100 - D_c) * min(1, rawF_c * (1 - D_c/100)^g_c).
- Library: rawF_c = raw_c / (1 - Dprev_c/100)^g_c for each training storm.
- Exponents (seasons 1-400, bootstrap sd in brackets): g_PORT 0.0 (0.04), g_ISLE 1.45 (0.14), g_AQUA 0.85 (0.13), g_GRID 0.50 (0.08), g_FERRY 0.55 (0.12).
- That is 5 parameters fitted on 1200 storms, and g = 0 gives back the fitter's model B.
- Quote |G - B| as model uncertainty on top of the bootstrap sd.

**3. Corrected tables (train library, G with B in brackets).**
- **Round-1 open:** E[HAVEN] 39.88 (40.49), E[REIN] 88.90 (88.94), E[L] 1013 (1000), P(L < 400) 0.044 (0.055), P(L > 1300) 0.210 (0.202).
- **Round-3 open by C2,** E[HAVEN] / E[REIN]:

| C2 | G | B |
|---|---|---|
| 0 | 67.04 / 90.00 | 67.98 / 90.00 |
| 300 | 59.54 / 89.98 | 59.40 / 89.98 |
| 600 | 46.21 / 89.70 | 45.04 / 89.66 |
| 900 | 31.89 / 89.53 | 31.51 / 89.47 |
| 1000 | 28.04 / 88.97 | 27.60 / 88.77 |
| 1100 | 24.54 / 88.19 | 24.26 / 87.85 |
| 1200 | 21.96 / 86.89 | 21.81 / 86.39 |
| 1300 | REIN 84.70 | REIN 83.95 |
| 1400 | REIN 82.07 | REIN 81.22 |
| 1500 | REIN 79.67 | REIN 78.77 |
| 1600 | REIN 77.40 | REIN 76.47 |
| 1800 | REIN 73.34 | REIN 72.40 |

- **Round-2 open by C1,** E[HAVEN] / E[REIN]:

| C1 | G | B |
|---|---|---|
| 0 | 52.98 / 89.87 | 54.22 / 89.88 |
| 300 | 44.10 / 89.59 | 43.78 / 89.49 |
| 400 | 41.01 / 89.33 | 39.92 / 89.08 |
| 500 | 37.69 / 88.94 | 36.39 / 88.56 |
| 600 | 33.70 / 88.79 | 33.07 / 88.46 |
| 800 | 26.85 / 88.07 | 27.00 / 87.84 |
| 1000 | 22.80 / 85.88 | 22.79 / 85.53 |
| 1200 | 21.15 / 82.58 | 21.12 / 82.10 |

The B values reproduce the fitter's tables, for example 45.04 / 89.66 at C2 = 600 and 81.22 at C2 = 1400. The round-2 values carry about 0.2 of Monte Carlo noise.

**4. Erratic storms.** Do not use the 20-storm train erratic column as it stands. Use at least the 35-storm pooled erratic library (mean loss 104), and treat the column as highly uncertain.

**5. Wording of the house edges.**
- **REIN, round-1 and round-2 opens:** keep the sell claim (model-free, same sign in train and holdout).
- **HAVEN, all three opens:** "too low" should read "suggestive, not significant on the holdout".
- **REIN, round-3 open:** drop the model-based edge (it comes from the bias in model B).

**6. Live season.**
- REIN at 89.36: fair value 88.90-88.99, so selling is worth +0.37 to +0.46 per share. This is robust. The payoff is skewed: 80% of seasons settle at 90, a loss of 0.64 per share to the seller.
- HAVEN at 39.55: fair value 39.88-40.49, worth +0.33 to +0.94 per share. This is not significant (empirical se 0.68), and the choice of model alone moves it by 0.6.

**7. Fix the arithmetic:** REIN at L = 2570 with no top is 45.55.

**What a live bot can and cannot know.**
- Settlement is known exactly at the round-3 final report (real 740 s). Values at each open are deterministic given D and the library, but uncertain by up to 1-2 points depending on the depletion model chosen.
- After a preliminary report, the house (HAVEN RMSE 1.97, REIN 0.79) beats a model that uses only the report (3.15 / 0.91), even when the house is measured before the report.
- The true storm pattern is not known live.
- Announcements in the live season can change the layer bounds.

## corrected_params_file

/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/fits/insurance.verified.json

## independent_metrics

All numbers are my own. Holdout means seasons 401-600 scored with parameters or libraries from 1-400 only.

**Settlement formula vs actual settlement**

| | HAVEN train | HAVEN holdout | REIN train | REIN holdout |
|---|---|---|---|---|
| RMSE, manifest L | 0.00284 | 0.00288 | 0.00121 | 0.00159 |
| Max error, manifest L | 0.006 | 0.006 | 0.006 | 0.0065 |
| RMSE, L from summed feed finals | 0.00365 | 0.0036 | 0.00128 | 0.00162 |
| Max error, L from summed feed finals | 0.012 | 0.012 | 0.007 | 0.0065 |

**Other formulas**
- Loss formula: holdout RMSE 0.0436 over 600 storms.
- Settlement minus house after the round-3 final report (row 375): HAVEN -0.002 ± 0.003, REIN -0.005 ± 0.005.

**Preliminary report (holdout, 600 storms), final-loss error**

| Estimator | Bias | RMSE |
|---|---|---|
| Raw preliminary | +16.01 | 104.97 |
| OLS line, final = 36.84 + 0.845 * prelim | -3.19 | 91.22 |
| E1 posterior | -1.54 | 68.97 |

For preliminaries above $800M (n = 51), the raw preliminary has bias +186 and RMSE 278, while E1 has RMSE 117.

**Round-3 settlement after the preliminary report (holdout RMSE)**

| | HAVEN | REIN |
|---|---|---|
| E1 | 3.15 | 0.91 |
| Naive f(C2 + prelim) | 3.12 | 2.23 |
| House, row 290 | 1.97 | 0.79 |
| House, rows 270-284 (before the report) | 2.22 | 0.88 |

**Round-1 open fair value**

| | E[HAVEN] | E[REIN] |
|---|---|---|
| Model B | 40.49 | 88.94 |
| Model G | 39.88 | 88.90 |
| Independent losses (no depletion) | 42.74 | 87.66 |
| Empirical, train | 40.56 ± 0.84 | 89.06 ± 0.13 |
| Empirical, holdout | 38.71 ± 1.17 | 88.83 ± 0.21 |
| Empirical, all 600 | 39.95 ± 0.68 | 88.99 ± 0.11 |

**Settlement predicted from the open-of-round state, holdout RMSE (bias = settle - prediction)**

| | HAVEN, round-3 open | REIN, round-3 open | HAVEN, round-2 open | REIN, round-2 open |
|---|---|---|---|---|
| B | 11.040 (+0.16) | 2.016 (+0.19) | 14.457 | 2.654 |
| G | 10.971 (-0.27) | 1.995 (+0.01) | 14.399 | 2.694 |
| Round-specific library | 11.060 | 1.992 | 14.53 | 2.657 |
| Fresh-only library A | 11.063 | 2.036 | 14.435 | 2.655 |
| A with depletion exponent | 11.006 | 1.996 | 14.458 | 2.720 |
| No state (independent losses) | 11.41 | 2.68 | 15.18 | 3.49 |
| Nearest-neighbour, model-free | 11.01 | 2.05 | 14.55 | 2.74 |

Pooled over both folds (600 out-of-sample seasons), G minus B in MSE is:
- round-3 open: HAVEN -2.71 ± 0.84, REIN -0.21 ± 0.10;
- round-2 open: HAVEN -2.72 ± 1.40, REIN -0.02 ± 0.10.

**Settlement minus house, model-free**

| Symbol, open | All 600 | Train | Holdout |
|---|---|---|---|
| HAVEN, round 1 | +1.81 ± 0.69 | +2.44 ± 0.84 | +0.54 ± 1.18 |
| HAVEN, round 2 | +1.57 ± 0.60 | +2.07 ± 0.74 | +0.58 ± 1.02 |
| HAVEN, round 3 | +1.05 ± 0.45 | +1.24 ± 0.56 | +0.65 ± 0.78 |
| REIN, round 1 | -0.38 ± 0.11 | -0.30 ± 0.13 | -0.54 ± 0.21 |
| REIN, round 2 | -0.33 ± 0.11 | -0.26 ± 0.13 | -0.47 ± 0.19 |
| REIN, round 3 | -0.09 ± 0.09 | -0.09 ± 0.11 | -0.10 ± 0.15 |

**Sign rule, holdout only:** trade the sign of (model - house), scored as profit per share against settlement.

| | B | G |
|---|---|---|
| REIN, round-2 open | +0.61 ± 0.19 | +0.53 ± 0.19 |
| HAVEN, round-2 open | +0.31 ± 1.02 | +2.05 ± 1.01 |
| REIN, round-3 open | +0.25 ± 0.14 | +0.21 ± 0.14 |

**Erratic-at-formation storms (holdout):** actual mean loss 35.1 ± 9.0, against 158.8 predicted by B and 174.8 by G.

Files are in `SP/work/se/verify-insurance/`:
- `v_extract.py`, `v_settle.py`, `v_depl.py`
- `v_ev.py`, `v_ev2.py`, `v_ev3.py`, `v_bins.py`, `v_pool.py`
- `v_house.py`, `v_prelim.py`, `v_post.py`, `v_edge.py`, `v_pattern.py`
- `v_tables.py`, `v_gboot.py`, `insmodel_g.py`, `make_verified.py`
- the matching `*.out` and `*.json` outputs
