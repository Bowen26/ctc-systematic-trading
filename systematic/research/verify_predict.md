# verify:predict

## verdict

major_problems

## issues

## Verification of the 'predict' fit (adversarial review)

I wrote all of my own code in `SP/work/se/verify-predict/` (v_parse, v_damage, v_damage2, v_damage3, v_settle, v_feat, v_model, v_alpha, v_edge, v_resid, v_stack, v_house, v_check_blend, v_final). I parsed the raw data myself into `v_cache.json`. Fits use seasons 1-400 and every metric below is on the holdout (seasons 401-600). Everything ran with Python stdlib only, as `python3 -I`.

### What I confirmed
- **Timing.** Observations are contiguous (ages 0..480 every 2 s) in all 1800 storms, so obs index t//2 is age t. House row (60+t)//2 has the same timestamp as the obs at age t. Advisories arrive at ages 30, 90, ..., every cone point is later than its advisory, and the final report matches the manifest exactly. I found no look-ahead in features.py, m_knn, m_lr, m_at, m_eval or alpha*.py: train predictions are out-of-fold or leave-one-out, holdout references are train-only, and prior rounds use only their final reports.
- **The params file exists, parses and matches the report.** I rebuilt blendB from the saved per-method predictions and the `blend.weights` in predict.json, scored against my own truth. The holdout RMSE table comes out identical (age 30 PORT 18.55, LOSS 216.32; age 480 10.80 / 143.66). The fitter's round-3 alpha numbers also come out exactly with my own house and settlement parse (age 30 PORT info-only +3.04, t 4.7, IC 0.35; MAE house/ours/combo 6.61/7.12/6.37).
- **Structural identities**, refit independently on 1-400 and scored on holdout:
  - loss = 9.0/6.0/2.5/7.0/1.2 · damage (RMSE 0.044).
  - PORT = 99.999 - 0.5499·D - 0.02002·H (MAE 0.013).
  - ISLE = 60 - 0.38·D (0.003); AQUA = 35 - 0.20·D (0.003).
  - GRID 0.019, FERRY 0.020, MAT 0.002, GEN 0.038, RECON 0.131.
  - HAVEN = 75 - 0.06·clip(L-400, 0, 900) (MAE 0.002); REIN (0.001).
  - ETFs equal their baskets exactly.
  - The 0.237 closure threshold matches impact messages 99.93% of the time.
  - Intensity fade holds (RMSE 0.04 on I ≈ 1.2).
- **The house-bias round trip is confirmed and model-free.** At age 4 the house jumps (PORT +1.88, HAVEN +3.47, RECON -0.69), and ARCH/REBLD follow at age 10 (+9.17 / -2.77). The fixed-sign portfolio (signs `-----+++---+`) from 30 to 420 s on the holdout:
  - Round 3: $3,631 mean, sd $8,615, t 6.0, P(loss) 0.35.
  - Rounds 1 and 2: $4,205 and $4,173 (t ≈ 7).
  - Every 100-season block is positive (t 2.3-5.4).
  - Entering 2 s late gives $3,622.
  - Break-even cost is only about $0.39 per share per side (limit-weighted).
- **Lag numbers are confirmed.** Fitter combo PORT at age 30, lag 0/2/4/20 s: 3.06 / 3.10 / 1.81 / 0.93. Lag 2 is about the same as lag 0, so there is no material off-by-one.

### Problems
1. **CRITICAL: the damage model is misspecified because it ignores remaining value (prior damage).**
   - A storm damages only what is still standing. Added damage = Σ_s w_s·cond_s·cap·(1-e^(-kE_s)), where cond_s is the site's remaining fraction after earlier storms. That fraction is known in real time from the earlier rounds' final reports.
   - Evidence of bias in the fitter's full-track additive model (holdout, pred - true): PORT round 1 -4.70, round 3 +4.03; AQUA -3.60 / +3.10; GRID -3.12 / +3.44.
   - The regression on prior damage is strongly negative (PORT: true ≈ 1.24·pred - 1.23·pred·prior/100).
   - The remaining-value (RV) model cuts full-track holdout RMSE about in half:

     | | PORT | ISLE | AQUA | GRID | FERRY | LOSS |
     |---|---|---|---|---|---|---|
     | All rounds, fitter | 10.80 | 4.81 | 8.29 | 6.03 | 5.52 | 143.8 |
     | All rounds, RV | 5.47 | 2.56 | 2.77 | 3.16 | 3.28 | 57.7 |
     | Round 3, fitter | 13.09 | 5.72 | 10.34 | 7.01 | 6.32 | 177.2 |
     | Round 3, RV | 4.49 | 2.01 | 1.77 | 2.35 | 2.61 | 47.9 |

   - Kernel shape is secondary under RV: Gaussian PORT 5.39, (1-u/2)^1.5 5.46, quadratic 5.47, cubic 6.19.
2. **CRITICAL: the "10.8 floor is unobservable per-island noise" and "no kernel or ridge reduces it" claims are false.**
   - Under RV, cross-company residual correlations fall from 0.38-0.75 (for example ISLE-AQUA 0.75, PORT-GRID 0.54) to |r| < 0.07.
   - So there is no hidden per-island factor. It was an artifact of the misspecification, because prior damage hits all companies on an island together.
3. **CRITICAL: headline 3 ("the house sees damage the feed hides; don't fight the house on PORT/ISLE/AQUA") is wrong.**
   - House-implied round-3 damage at age 480 (my inversion): PORT 4.66, ISLE 1.96, AQUA 1.72, GRID 2.25, FERRY 2.61.
   - RV full track: 4.49, 2.01, 1.77, 2.35, 2.61. So the house simply uses the correct model.
   - With RV, our standalone settlement MAE beats the house:
     - age 30: PORT 5.37 vs 6.61, ISLE 1.80 vs 2.15, AQUA 1.16 vs 1.44, HAVEN 5.58 vs 6.60, REBLD 3.92 vs 5.12;
     - age 240: PORT 2.46 vs 2.63, HAVEN 2.49 vs 3.41;
     - age 300: PORT 1.60 vs 2.15.
   - The house is about equal or slightly better only from age 420 on (PORT 1.17 vs 1.27).
   - Info-only PnL and IC (RV vs fitter): age 30 PORT +3.91 (t 6.3) IC 0.53 vs +3.04 / 0.35; age 240 PORT +1.86 (t 6.0) IC 0.48 vs 0.16; AQUA +0.29 (t 7.1) vs -0.03; age 300 PORT +2.08 (t 8.8) IC 0.65 vs 0.20.
4. **HIGH: every mid-storm prediction is biased by round.**
   - Fitter blendB bias, PORT round 1 / round 3: -5.7 / +3.8 at age 30, -5.2 / +4.9 at 120, -4.2 / +4.9 at 240.
   - LOSS bias in round 3 is +79 to +93 $M at every age.
   - Round 3 is the round the bot trades. In storms with prior PORT damage > 30% (116 of 200), the PORT bias is +8 to +10 points at ages 30-480.
   - The spread model inherits this: round-3 mean z is -0.2 to -0.5.
5. **HIGH: holdout accuracy is overstated as achievable error.** Holdout RMSE, all rounds (round 3 in brackets):

   | Age | PORT blendB | PORT RV | LOSS blendB | LOSS RV |
   |---|---|---|---|---|
   | 30 | 18.55 | 16.90 (13.64) | 216 | 185 (151) |
   | 120 | 16.02 | 13.70 (10.72) | 200 | 156 (124) |
   | 240 | 13.19 | 9.71 (7.10) | 167 | 109 (80) |
   | 360 | 10.88 | 5.83 (4.87) | 145 | 62 (51) |

   - My same pipeline with additive damage (ADD3) reproduces the fitter's numbers (age 240 PORT 13.34, LOSS 169). That shows the gap comes from RV, not from my method.
   - Stacking blendB on top of RV gives weight ≈ 0 and no RMSE gain at any age. The fitter's ML stack adds nothing once RV is in.
6. **MEDIUM: the preliminary-report blend is understated.** final ≈ 0.35 + 0.26·prelim + 0.72·RV track gives PORT RMSE 4.71 (the fitter quoted 6.95 with the additive track). This matters for the 170 s of trading between the preliminary and final reports.
7. **LOW: "info-only" PnL (PORT +$3.04) is not a realizable PnL.** It needs a two-unit position. The realizable gain over bias-only at a single limit is combo-sign minus bias-only: at age 30, fitter +1.19, RV +1.98.
8. **LOW: the closure predictor (unaffected by RV) is fine.** Fitter blendB round-3 holdout H RMSE at age 120 is 29.9 / 10.9 / 12.6, slightly better than my 32.6 / 11.5 / 13.6. Keep it.
9. **LOW: documentation and reproducibility gaps in params.**
   - The `spread.form` string omits the a3·pred² term and the floor, although 4 coefficients are stored.
   - Feature construction (4-s future step, ±1.118·spread blur, intensity fade) is only a pointer to features.py.
   - The kNN/AT blend weights were fit with 1200 training references, but the params say to use all 1800 storms live (a mild inconsistency).
10. **LOW: settlement forms can be simpler.** GEN = 31.985 + 17.863·(1-e^(-H_G/120)) + 0.0012·D_G has holdout MAE 0.028 vs 0.038 with 3 parameters instead of 5. RECON = 40.062 + 24.323·(1-e^(-(D_P+D_G)/120)) matches the 7-parameter quadratic (0.131) with 3.
11. **Edge cases.**
    - Only 3 holdout storms have zero damage everywhere; RV handles them better (age 120 LOSS RMSE 2.7 vs 40).
    - Big storms (LOSS > 700, n = 70) are under-predicted early by both models (PORT bias -30 to -35 at age 30); late, RV is much less biased (-5.8 vs -16.4 at 360).
    - Pure erratic storms (n = 15): RV is better from age 120 on.
    - Storms that turn erratic mid-storm (n = 40): at age 30 neither model works (PORT RMSE about 19 for both); later RV is better.
    - Stall storms: RV fixes AQUA (RMSE 4.3 vs 15.7 at age 120).
    - Overfitting is not the issue (train ≈ holdout); misspecification is.

## corrections

## Corrected model (fit on seasons 1-400; written to fits/predict.verified.json)

1. **Remaining-value (RV) damage model.**
   - Exposure: E_s = Σ over 2-s obs of 2·I·K(d/R), with K(u) = (1-u/2)² for u < 2 (otherwise 0).
   - Added damage % for storm r: D_c,r = Σ_s w_s·cond_s·cap_c·(1 - e^(-k_c·E_s)).
   - cap = PORT 87.41, ISLE 96.67, AQUA 96.42, GRID 98.96, FERRY 91.78.
   - k = 0.019949, 0.013998, 0.025088, 0.018455, 0.014933.
   - cond_s starts at 1 each season. After each round's final report: part_s = w_s·cond_s·(1-e^(-k·E_s)), then cond_s -= (D_c/100)·part_s/Σpart/w_s, floored at 0.
   - For PORT this reduces exactly to cond = 1 - cumD/100.
   - The loss identity is unchanged: 9/6/2.5/7/1.2.
2. **Mid-storm predictor (RV3+hc), per grid age, per company.** damage = max(0, β·[1, at_rv, cone_rv, dso_rv, hc×4, outlook one-hot×5]).
   - at_rv: 25 training analogs at the same age, chosen on standardized position, v30 and an outlook mismatch penalty of 2. Each analog's future track is translated to the current position, with R and I scaled to the current storm. Damage is evaluated under RV with the current cond.
   - cone_rv: the latest official cone path, 3×3 blur at 0.7·spread, intensity fade from 420 s.
   - Coefficients are in `rv_calibration`. Reference implementation: verify-predict/v_feat.py.
   - For closure seconds, keep the fitter's blendB (slightly better), or use `closure_calibration`.
3. **Spread.** σ² = max(floor², a0 + a1·at_sd² + a2·pred²). Holdout 90% coverage is 0.89-0.95. Use the stored train z-quantiles, because the errors are leptokurtic (50% coverage is 0.36-0.79).
4. **Preliminary report (age 510).** final ≈ a + 0.20-0.29·prelim + 0.69-0.78·RV track, with per-company coefficients stored. Holdout RMSE: PORT 4.71, ISLE 2.11, AQUA 2.50, GRID 2.92, FERRY 2.73.
5. **Settlement formulas.** The fitter's V2 forms are confirmed and kept. Optional simpler forms:
   - GEN = 31.985 + 17.863·(1-e^(-H_G/120)) + 0.0012·D_G
   - RECON = 40.062 + 24.323·(1-e^(-(D_P+D_G)/120))
6. **Fair value.** combo = a + b·house + c·ours_RV, with coefficients per age per symbol in `combo_rv`. With RV, combo beats the house MAE at age 30 for every symbol except REIN, and for most symbols through age 300.
7. **Revised trading conclusions.**
   - Keep the model-free bias lean (confirmed).
   - The information edge is real for PORT, ISLE and AQUA mid-storm. Drop the "never fade the house on PORT/ISLE/AQUA" rule before about age 360; from 420 s on the house is roughly equal to us.
   - The age-30 edge decays less with latency than the fitter found: RV PORT combo +3.85 / +4.07 / +2.97 / +2.12 at lags 0/2/4/20 s, vs the fitter's +3.06 / +3.10 / +1.81 / +0.93.
   - Size against real spreads, which are not in the data.

## corrected_params_file

/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/fits/predict.verified.json

## independent_metrics

## Holdout 401-600, computed by my own code

**Full-track damage RMSE (PORT / ISLE / AQUA / GRID / FERRY / LOSS):**
- Fitter's additive model, refit by me: 10.80 / 4.81 / 8.29 / 6.03 / 5.52 / 143.8, which reproduces the fitter.
- RV model: 5.47 / 2.56 / 2.77 / 3.16 / 3.28 / 57.7.
- Round 3 only, RV: 4.49 / 2.01 / 1.77 / 2.35 / 2.61 / 47.9 (additive: 13.09 / 5.72 / 10.34 / 7.01 / 6.32 / 177.2).

**Mid-storm RMSE, RV3+hc vs fitter blendB, all 600 holdout storms (PORT, ISLE, AQUA, LOSS):**

| Age | RV3+hc | blendB |
|---|---|---|
| 0 | 22.28, 11.32, 14.68, 245 | 23.14, 11.47, 15.27, 259 |
| 30 | 16.90, 6.92, 9.03, 185 | 18.55, 7.90, 11.55, 216 |
| 120 | 13.70, 4.55, 6.13, 156 | 16.02, 5.90, 9.63, 200 |
| 240 | 9.71, 3.00, 3.56, 109 | 13.19, 5.06, 8.54, 167 |
| 360 | 5.83, 2.58, 2.75, 62 | 10.88, 4.83, 8.34, 145 |
| 480 | 5.45, 2.59, 2.76, 57 | 10.80, 4.81, 8.28, 144 |

**Same comparison, round 3 only (PORT, LOSS):**

| Age | RV3+hc | blendB |
|---|---|---|
| 30 | 13.64, 151 | 16.97, 211 |
| 120 | 10.72, 124 | 15.81, 209 |
| 240 | 7.10, 80 | 14.35, 191 |
| 360 | 4.87, 51 | 12.96, 177 |

**Fitter blendB round-3 bias:** PORT +3.8 to +4.9 and LOSS +79 to +93 at every age. RV round-3 bias is PORT +0.7 or less and LOSS +20 or less.

**House-implied round-3 damage at age 480, RMSE:** PORT 4.66, ISLE 1.96, AQUA 1.72, GRID 2.25, FERRY 2.61. RV track: 4.49, 2.01, 1.77, 2.35, 2.61.

**Residual cross-company correlation:** 0.38-0.75 under the additive model, |r| < 0.07 under RV.

**Round-3 settlement MAE, house / ours-RV / combo:**
- Age 30: PORT 6.61 / 5.37 / 5.27; ISLE 2.15 / 1.80 / 1.79; AQUA 1.44 / 1.16 / 1.17; HAVEN 6.60 / 5.58 / 5.77; ARCH 16.57 / 13.87 / 13.54.
- Age 300: PORT 2.15 / 1.60 / 1.62; HAVEN 2.62 / 1.70 / 1.75.
- Age 480: PORT 1.18 / 1.26 / 1.21.

**Info-only PnL, RV ($/share, t, IC):**
- Age 30: PORT +3.91 (t 6.3, IC .53), ISLE +1.08 (5.4, .52), AQUA +0.84 (6.4, .58), HAVEN +3.10 (5.1, .46), REBLD +2.79 (6.1, .59).
- Age 300: PORT +2.08 (8.8, .65).

**Combo-sign PnL, PORT at age 30, lag 0/2/4/20 s:** RV +3.85 / +4.07 / +2.97 / +2.12; fitter +3.06 / +3.10 / +1.81 / +0.93.

**House-bias portfolio, 30 to 420 s, full limits, signs from 1-400:**
- Round 3: $3,631 mean, sd $8,615, t 6.0, P(loss) 0.35.
- Rounds 1 and 2: $4,205 and $4,173.
- With a 2 s entry lag: $3,622.
- Break-even cost: $0.39 per share per side.

**Structural checks:**
- Settlement formulas with true inputs, holdout MAE: PORT 0.013, ISLE 0.003, AQUA 0.003, GRID 0.019, FERRY 0.020, RECON 0.131, MAT 0.002, GEN 0.038 (alternative form 0.028), HAVEN 0.002, REIN 0.001.
- Loss identity RMSE 0.044; closure and impact agreement 0.9993.

**Other:**
- Prelim + RV blend, PORT RMSE 4.71 (preliminary report alone 9.84).
- RV spread, holdout 90% coverage 0.89-0.95.
