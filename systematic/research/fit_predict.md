# fit:predict

## headline

- **Yes, there is a tradable mid-storm edge, but most of it is a house bias, not our forecast.** In every round (all 600 seasons, t up to 20), about 4 s after a storm forms the house reprices too optimistically: PORT up ~$1.9, HAVEN up ~$3.5, GRID up ~$1.0, rebuilders down ~$0.4-0.7. ARCH/REBLD follow about 6 s later. It bleeds back by age ~420. A fixed-sign round trip (enter at age 30, exit at 420, full limits, at mid, no costs) makes **~$3.4-4.2k per storm**: holdout t 6-7, sd ~$8.6k, loses 31-35% of the time.
- **Our own information edge peaks at the first advisory (age 30) and is latency-sensitive.** Beyond the bias it is worth PORT +$3.04/share (t 4.7), REBLD +2.25, HAVEN +1.96, ISLE +0.82, FERRY +0.56. PORT's lag-0 edge of $3.06 falls to $1.81 after 4 s and $0.93 after 20 s. Closure-driven names keep the edge all storm: FERRY IC 0.4-0.5, GEN IC 0.4-0.8.
- **Late in the storm, do not fight the house on PORT, ISLE or AQUA.** The house sees damage the feed hides: inverting its price at age 480 gives round-3 damage RMSE of 2.0 / 1.7 / 4.7 points, versus 5.7 / 10.3 / 13.1 for our full-track model. Our standalone settlement MAE loses to the house on these from age 45 on.
- **Prediction accuracy (holdout 401-600, RMSE).** PORT damage goes 23.4 (unconditional) → 18.6 (age 30, R² 0.37) → 13.2 (240) → 10.8 (360+). The 10.8 floor is unobservable per-island assessment noise. LOSS goes 261 → 216 → 167 → 144, ISLE 11.4 → 7.9 → 5.1 → 4.8. Closure time is predictable to near zero error by age 360-420.
- **Structural identities (seasons 1-400; error from true inputs on the holdout):** loss = 9.0 PORT + 6.0 ISLE + 2.5 AQUA + 7.0 GRID + 1.2 FERRY (RMSE 0.04). HAVEN = 75 - 0.06·clip(L-400, 0, 900); REIN = 90 - 0.035·clip(L-1300, 0, ≥467); ISLE = 60 - 0.38·cumD; AQUA = 35 - 0.20·cumD; MAT = 28 + 14·(1 - e^(-L/1200)); all of these are near-exact (MAE ≤ $0.003). PORT, GRID, FERRY and GEN need closure seconds (e.g. PORT = 100 - 0.55·cumD - 0.020·H). Closure is deterministic from the track (wind proxy I·(1-d/2R)² > 0.237).
- **The predictive spread is usable.** The blendB sigma covers 90-95% of outcomes at the 90% level. Errors are leptokurtic (median |z| 0.2-0.7), so the params include empirical z-quantiles for convex HAVEN/REIN payoffs. For REIN, plug-in beat a Gaussian expectation.
- **Combining the house price with our signal beats the house price alone** for GRID, FERRY, RECON, MAT, GEN, HAVEN, ARCH and REBLD mid-storm (e.g. GRID MAE $1.04 vs $1.25 at age 120, GEN $0.21 vs $0.42 at 300). It does not beat the house on PORT/ISLE/AQUA after age 30, or on REIN.

## params_file

/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/fits/predict.json

## scripts

[
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/pm_lib.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/build_cache.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/damage_model.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/features.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/m_knn.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/m_lr.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/m_at.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/m_eval.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/alpha.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/alpha2.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/alpha3.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/alpha4.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/alpha5.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/make_params.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/report_tables.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/explore/e1.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/explore/e2.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/explore/e3.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/explore/e4.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/explore/e5.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/explore/e6.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/explore/e7.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/explore/e8.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/explore/e9.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/explore/e10.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/explore/e11.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/explore/e12.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/explore/e13.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/explore/e14.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/explore/e15.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/predict/explore/e16.py"
]

## report

# Predicting a storm's final damage while it is in progress, and the round-3 alpha test

Fits use seasons 1-400; every number labelled "holdout" is on seasons 401-600. Every number below comes from the scripts listed at the end (Python stdlib only, run with `python3 -I`).

## 0. Bottom line: is there a mid-storm edge, and when does it peak?

1. **The biggest, most robust edge is a systematic house bias, not superior forecasting.** About 4 s after each storm forms (age 4), the house consensus jumps toward an *optimistic* valuation, then drifts back as damage accrues. By age about 420 it is back near fair value. This holds in every round, and it is model-free: it uses only market.csv and settlement.json across all 600 seasons.
   - The jump: PORT +$1.88, ISLE +0.72, AQUA +0.52, GRID +0.97, FERRY +0.54, HAVEN +3.47, REIN +0.19, RECON -0.70, MAT -0.40, GEN -0.56.
   - ARCH and REBLD jump about 6 s later (age 10), so for a few seconds the ETFs are off their basket NAV (ARCH +$9.15 at age 10).
   - The round trip, house[420] - house[t1], is negative for island businesses, HAVEN, REIN and ARCH and positive for RECON, MAT, GEN and REBLD, with t-stats 3-12 per symbol per round.
   - Portfolio at full limits (fixed signs `-----+++---+` in symbol order PORT ... REBLD; enter at age 30, exit at 420; at mid, no costs): mean **$3.6k per storm on the holdout (t 6.0, sd $8.6k, P(loss) 35%)** in round 3, and $4.2k in rounds 1 and 2.
2. **Our own information edge peaks at the first official advisory (age 30) and decays with latency.** The bias-removed ("info-only") PnL at age 30 is:

   | Symbol | $/share | t-stat | IC |
   |---|---|---|---|
   | PORT | +3.04 | 4.7 | 0.35 |
   | ISLE | +0.82 | 4.0 | |
   | AQUA | +0.58 | 4.2 | |
   | FERRY | +0.56 | 5.3 | 0.46 |
   | RECON | +0.70 | 4.8 | 0.47 |
   | GEN | +0.36 | 5.1 | 0.43 |
   | HAVEN | +1.96 | 3.1 | |
   | REBLD | +2.25 | 4.8 | 0.48 |
   | ARCH | +4.24 | 3.0 | |

   - The PORT edge at age 30 falls from $3.06 at lag 0 to $1.81 if execution is 4 s late, and to $0.93 at 20 s. The house closes 20% of the gap to our signal within 10 s and 31% within 60 s.
   - After the first advisory, the information edge survives mainly where value depends on **closure time**, which our model gets right and the house does not:
     - FERRY IC 0.4-0.5 for the whole storm.
     - GEN IC 0.3-0.8, still +$0.06/share (t 11) at age 420.
     - PORT around landfall (ages 240-300: +$1.37-1.38, t 4-5).
3. **Do not bet against the house on PORT, ISLE or AQUA late in the storm.** The house knows the per-island damage factor that the feed never reveals. Inverting the house price at age 480 gives the round-3 damage with RMSE 2.0 (ISLE), 1.7 (AQUA) and 4.7 (PORT) points, versus 5.7 / 10.3 / 13.1 for our full-track physics model. From age 45 on, our standalone settlement MAE is worse than the house's on these three names.
4. **Combining house and ours** (settle = a + b·house + c·ours, fit on 1-400) beats the house MAE mid-storm for GRID, FERRY, RECON, MAT, GEN, HAVEN, ARCH and REBLD. It does not beat the house for PORT, ISLE and AQUA after age 30, or for REIN. Tables are in section 7.

## 1. Data, timing, conventions

- **Timing.** Storm age t is in real seconds. The house price available at age t is market.csv round-3 row floor((60+t)/2). The obs at age t is index t//2.
- **What counts as available at age t.** A message is used if its `age` is ≤ t; for impacts, the impact age is ≤ t.
- **Cache.** One pass over all feeds (4 s) produces `predict/cache.json`, with per-storm obs, advisories, wire messages, impacts, prelim/final reports and market rows. The manifest's final damage equals the feed's final report exactly.
- **Distributions over 1800 storms.**
  - PORT damage: mean 15.8, sd 22.9, zero for 48% of storms, max 97.5.
  - ISLE 8.6 ± 11.5; AQUA 13.1 ± 14.9; GRID 13.5 ± 9.8; FERRY 12.5 ± 10.3.
  - Industry loss: 336 ± 256 $M.
- **Source accuracy.** The official outlook names the true pattern 69-72% of the time at every advisory number; it does not improve over the storm. Wire rumors are right 40% of the time. The cone `spread` is about the RMS miss distance: the ratio of RMS error to spread is 1.04, 1.05, 1.08 and 1.31 at leads of 30, 60, 120 and 240 s.
- **Intensity fade.** Over the storm's last minute, I(a) = I(420)·(480-a)/60.

## 2. Structural facts: what can and cannot be known in real time

| Quantity | Finding | Knowable when |
|---|---|---|
| Industry loss | **loss = 9.0·PORT + 6.0·ISLE + 2.5·AQUA + 7.0·GRID + 1.2·FERRY** (damage %), RMSE $0.04M | As soon as damage is known |
| Site exposure | Zero damage if closest approach > 2R. Best kernel: E_s = Σ_obs 2s · I · (1 - d/2R)² | Exactly, from the track |
| Damage model | dmg_c = Σ_s w_s · cap_c · (1 - e^(-k_c·E_s)). cap = PORT 70, ISLE 74, AQUA 70, GRID 78, FERRY 71. k = 0.0204, 0.0176, 0.0339, 0.0194, 0.0176 | Model value, exactly from the track |
| Irreducible damage noise | Full-track holdout RMSE: PORT 10.8, ISLE 4.8, AQUA 8.3, GRID 6.0, FERRY 5.5, LOSS 144. Residuals are correlated across companies on the same island (0.5-0.77) but not across islands (PORT-ISLE 0.04). No kernel shape, threshold, left/right asymmetry, farm-vs-fleet split or flexible ridge on 35 track summaries reduces it (best PORT 10.78) | Only through the prelim report (age 510) or the house price |
| Preliminary report | Errors are independent across companies; RMSE PORT 9.8, LOSS 105. Blend: final ≈ 0.55·prelim + 0.40·track model (PORT RMSE 6.95, LOSS 81). Post-storm window, outside this area | Age 510 |
| Closure (hurricane conditions) | Site closed while I·(1-d/2R)² > 0.237. Reproduces impact messages with 99.9% accuracy. Deterministic | Exactly, from the track |
| Impacts | Carry no information beyond the track | |
| House price | Knows the hidden per-island damage factor: implied-damage RMSE 2-5 points at age 480 (section 0, item 3) | Continuously |

So in real time, closure-so-far and the model damage-so-far are exact. Final damage has a floor error of about 0.47 × its unconditional SD even with the full track. The remaining uncertainty is (a) the future path, resolved by about age 360, and (b) assessment noise, which the feed resolves only at the prelim report but which leaks into house prices continuously.

## 3. Features (point-in-time, `features.py`)

For each storm at ages t ∈ {0, 15, 30, 45, 60, 90, 120, 150, 180, 240, 300, 360, 420, 480}:

- **State:** x, y, vx, vy, R, I; 30-s mean velocity (v30x, v30y); northernmost y so far.
- **So far:** site exposures `eso[17]`, model damage so far `dso[5]`, closure seconds so far `hso[5]` (site-weight weighted).
- **Latest official advisory:** outlook one-hot, hurricane_conditions[4], cone points, lag since the advisory.
- **Wire and impacts:** rumor counts by outlook plus their fractions; impact flags[4].
- **Physics forecasts of final damage and closure:**
  - `dpers` / `hpers`: track so far plus a constant-velocity path.
  - `dcone` / `hcone`: track so far plus a path linearly interpolated through the cone points, extrapolated past the last point, with R held and I faded.
  - `dconeb` / `hconeb`: the cone path averaged over 5 shifted copies (centre plus ±1.118·spread in x and y, which matches an isotropic Gaussian with per-axis sd = spread/√2).
- **Targets:** final damage_pct[5]; LOSS (via the identity); closure seconds H_PORT, H_GRID, H_FERRY (these drive PORT/GRID/FERRY/GEN value).

## 4. Methods

1. **kNN analogs (`m_knn.py`).** Training storms are compared at the same age.
   - Distance² = dx² + dy² + wv·dv30² + wd·Σ d(dso)² + wc·Σ d(dconeb)² + wo·[outlook differs], all standardized at that age.
   - Prediction: "raw" (mean of analog finals) or "incr" (dso_now + mean(analog final - analog dso)). Closure is always incr.
   - Tuned per age by coordinate descent on a 300-query subsample, self excluded, minimizing Σ MSE/var over 6 targets.
   - Examples: age 30 {wv 1, wo 2, wd 0.25, wc 0.25, K 20, raw}; age 120 {1, 1, 0.25, 0.25, K 40, raw}; age 240 {1, 1, 1, 4, K 20, incr}; age 360 {0, 0, 0.25, 0.25, K 10, incr}.
2. **Linear (ridge) regression (`m_lr.py`), per age.**
   - *LR_basic* (the requested hc + position version): 1, x, y, v30x, v30y, R, I, hc[4], outlook[6].
   - *LR_full*: basic + dso, dpers, dcone, dconeb, wire fractions, nwire, impacts, adv lag, and the closure features. Lambda is chosen by 5-fold season-block CV (0.03-0.3 × n).
   - Coefficients are stored in original units per target.
3. **Analog-track ensemble (AT, `m_at.py`; extra method).**
   - Take 30 analogs (weights on position, velocity and outlook).
   - Translate each analog's *future* path to the current position, and scale R and I by the ratio current/analog at age t.
   - Run the damage and closure models on (so far + translated future). This gives 30 joint physical scenarios.
4. **Blends (`m_eval.py`).** Per age and target, OLS on training out-of-fold predictions:
   - blendA = [1, kNN, LR_full]
   - blendB = [1, kNN, LR_full, AT], clipped at ≥ 0
   - LOSS = the identity applied to the blended damages.
   - Typical blendB weights: at age 30, LR_full about 0.5-0.78, kNN 0.04-0.39, AT 0.12-0.30. At age 360, AT carries 0.7-1.5.

## 5. Holdout accuracy (RMSE, seasons 401-600, n = 600 storms)

Unconditional mean vs blendB. Skill = 1 - (RMSE/RMSE_mean)².

| age | PORT | ISLE | AQUA | GRID | FERRY | LOSS $M | H_PORT s | H_GRID s | H_FERRY s |
|---|---|---|---|---|---|---|---|---|---|
| mean | 23.35 | 11.42 | 15.21 | 9.74 | 10.55 | 260.8 | 44.9 | 16.8 | 26.0 |
| 0 | 23.14 | 11.47 | 15.27 | 9.66 | 10.17 | 258.8 | 44.6 | 16.7 | 25.6 |
| 15 | 22.79 | 10.21 | 14.07 | 9.26 | 9.55 | 250.2 | 43.8 | 14.6 | 22.9 |
| 30 | 18.55 (0.37) | 7.90 (0.52) | 11.55 | 7.91 | 8.15 | 216.3 (0.31) | 36.3 | 11.8 | 17.3 |
| 60 | 18.20 | 7.28 | 11.10 | 7.92 | 7.84 | 215.2 | 35.0 | 11.7 | 16.3 |
| 90 | 16.14 (0.52) | 5.96 | 9.74 | 7.52 | 7.18 | 200.8 | 29.6 | 10.4 | 12.6 |
| 120 | 16.02 | 5.90 | 9.63 | 7.48 | 7.09 | 200.3 | 29.1 | 10.2 | 11.7 |
| 180 | 14.65 | 5.52 | 8.91 | 6.91 | 6.22 | 182.8 | 25.4 | 8.5 | 7.9 |
| 240 | 13.19 (0.68) | 5.06 | 8.54 | 6.57 | 5.87 | 167.4 (0.59) | 21.4 | 6.8 | 5.2 |
| 300 | 11.85 | 4.77 | 8.29 | 6.24 | 5.58 | 153.3 | 14.7 | 4.6 | 3.2 |
| 360 | 10.88 (0.78) | 4.83 | 8.34 | 6.10 | 5.58 | 145.2 (0.69) | 7.5 | 2.2 | 1.4 |
| 420 | 10.83 | 4.90 | 8.30 | 6.01 | 5.53 | 144.0 | 1.5 | 0.4 | 0.5 |
| 480 | 10.80 | 4.81 | 8.28 | 6.03 | 5.54 | 143.7 | 0 | 0 | 0 |

Method comparison for PORT (LOSS in brackets):

| age | kNN | LR_basic | LR_full | AT | phys cone only | blendB |
|---|---|---|---|---|---|---|
| 30 | 18.85 (222) | 18.42 (217) | 18.73 (218) | 19.36 (223) | 21.73 (275) | 18.55 (216) |
| 120 | 16.65 (211) | 17.03 (213) | 16.07 (200) | 17.63 (211) | 17.98 (237) | 16.02 (200) |
| 240 | 13.95 (176) | 16.69 (208) | 13.18 (168) | 14.22 (178) | 13.58 (173) | 13.19 (167) |
| 360 | 11.54 (151) | 16.51 (205) | 11.02 (146) | 10.91 (145) | 11.00 (145) | 10.88 (145) |

- **The methods are close.** LR_full is the best single method. blendB adds 0-2%. AT is best late (360+) and for closure.
- **LR_basic plateaus.** It has no damage-so-far input and degrades after age 180.
- **Information arrives in two steps.** The first advisory (age 30) gives the big jump. The storm's approach (150-360) does the rest.
- **The floor is reached by about age 360.** Full tables for every target and method are in `predict/tables.md`.

## 6. Predictive spread and calibration (for convex insurer payoffs)

- **Model.** σ² = max(floor², a0 + a1·sd_AT² + a2·sd_kNN² + a3·pred²), with non-negative coefficients fit per age and target on training out-of-fold squared residuals. The floor is 0.05 × the unconditional SD. The coefficients are in `predict.json → spread.coef`.
- **Holdout calibration.** Mean z² is 0.5-1.3. **90% intervals cover 87-97%** and 80% intervals cover 78-93%. But 50% intervals cover 50-76%, and median |z| is 0.2-0.7 versus the Gaussian 0.674. The errors are leptokurtic: many storms miss every site and have near-zero error, while a few have large errors.
- **Shape.** Use the stored train z-quantiles (`spread.train_residual_quantiles`) rather than Gaussian tails. For PORT at age 120: q05 -1.34, q25 -0.54, q50 -0.11, q75 0.15, q95 1.73.
- **Raw ensembles.** The raw AT spread is roughly calibrated through age 150 (PORT 50/80/90 coverage ≈ 55/80/86). After that it is badly under-dispersed (5-30% coverage by 360), because it carries path uncertainty only, not the assessment noise. Recalibrate before use.
- **Joint scenarios.** The AT members (stored for round-3 storms) keep the cross-company correlation needed for season loss. Rescale them to the calibrated σ.
- **Insurers.** A Gaussian expectation E[f(L)] for REIN was worse than plug-in (MAE $2.1 vs $1.6 at age 0), because the true L3 distribution is skewed with a finite tail (max $1,201M). Use empirical or AT scenarios for REIN, not a Normal.

## 7. Round-3 alpha test

### 7.1 Settlement formulas (seasons 1-400; cum = additive sum over the 3 storms; H = closure seconds)

| Symbol | V1: requested first pass, damage only (MAE with true inputs, holdout) | V2: closure-augmented (MAE) |
|---|---|---|
| PORT | 100.40 - 0.593·D ($0.57) | **100.00 - 0.550·D - 0.0200·H_PORT ($0.013)** |
| ISLE | 60.00 - 0.380·D ($0.003) | same |
| AQUA | 35.00 - 0.200·D ($0.003) | same |
| GRID | 49.55 - 0.320·D ($0.76) | **50.01 - 0.241·D - 0.0495·H_GRID ($0.019)** |
| FERRY | 25.61 - 0.200·D ($1.21) | **25.00 - 0.0501·D - 0.0598·H_FERRY ($0.020)** (FERRY is mostly closure-driven) |
| RECON | linear in 5 cumD ($0.55) | 40.11 + 0.162·D_P + 0.208·D_G + 0.0106·D_F - 0.00061·D_P² - 0.0015·D_G² + 0.00004·D_F² ($0.13) |
| MAT | linear ($0.21) | **28.00 + 14.00·(1 - e^(-L/1200)) ($0.002)** |
| GEN | linear ($1.17) | 32.19 + 0.0129·D_G + 0.128·H_G - 0.00031·H_G² - 0.00013·D_G² ($0.038) |
| HAVEN | **75.00 - 0.0600·clip(L - 400, 0, 900)** ($0.003); floor 21 at L ≥ 1300 | same |
| REIN | **90.00 - 0.0350·clip(L - 1300, 0, W)**, W ≥ 467 (not identified: max season L is 1766) ($0.001) | same |

ETFs settle at their baskets.

In round 3 at age t, the prior rounds' D, H and L are known exactly; H comes from the recorded tracks. The round-3 terms come from blendB.

### 7.2 Holdout MAE ($/share): house(t) / ours(blendB, V2, plug-in) / combo

| age | PORT | ISLE | AQUA | GRID | FERRY | RECON | MAT | GEN | HAVEN | REIN | ARCH | REBLD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 8.58/9.11/8.28 | 2.85/3.20/2.86 | 1.93/2.39/1.93 | 2.12/2.25/2.06 | 1.69/1.63/1.56 | 1.92/2.02/1.84 | 0.88/1.00/0.86 | 1.18/1.14/1.16 | 8.44/8.85/8.64 | 1.02/1.63/1.12 | 20.5/22.9/20.3 | 6.28/6.55/6.16 |
| 30 | 6.61/7.12/**6.37** | 2.15/2.32/2.12 | 1.44/1.92/1.58 | 1.62/1.84/**1.51** | 1.31/**1.10/1.08** | 1.45/1.48/**1.34** | 0.69/0.81/0.67 | 0.93/**0.73/0.73** | 6.60/6.93/6.50 | 0.80/1.72/0.91 | 16.6/19.1/**15.8** | 5.12/**4.89/4.45** |
| 90 | 4.57/6.11/4.65 | 1.36/1.72/1.52 | 0.94/1.61/1.07 | 1.42/1.80/**1.26** | 1.00/**0.83/0.81** | 1.11/1.27/1.06 | 0.58/0.75/0.55 | 0.82/**0.64/0.63** | 5.64/6.07/**5.33** | 0.63/1.84/0.69 | 14.0/17.1/**12.9** | 4.09/4.40/**3.67** |
| 120 | 3.91/6.16/4.19 | 1.08/1.68/1.29 | 0.73/1.59/0.81 | 1.25/1.78/**1.04** | 0.78/0.76/**0.70** | 0.95/1.26/0.93 | 0.52/0.74/**0.47** | 0.75/**0.64/0.59** | 4.93/5.99/**4.66** | 0.61/1.92/0.62 | 12.3/16.9/**10.9** | 3.59/4.34/**3.25** |
| 180 | 3.13/5.56/3.49 | 0.78/1.33/0.98 | 0.49/1.35/0.58 | 1.01/1.53/**0.81** | 0.53/0.51/**0.45** | 0.77/1.11/0.78 | 0.43/0.71/0.39 | 0.62/**0.51/0.46** | 4.01/5.61/3.89 | 0.50/1.89/0.51 | 10.1/15.9/**8.75** | 2.97/3.88/**2.69** |
| 240 | 2.63/4.87/2.91 | 0.62/1.25/0.78 | 0.35/1.23/0.42 | 0.86/1.45/**0.75** | 0.41/0.36/**0.32** | 0.67/0.94/0.63 | 0.36/0.62/0.35 | 0.53/**0.38/0.37** | 3.41/4.83/3.34 | 0.46/1.89/0.46 | 8.35/14.4/**7.54** | 2.51/3.28/**2.24** |
| 300 | 2.15/4.11/2.39 | 0.48/1.13/0.56 | 0.24/1.19/0.25 | 0.66/1.25/0.59 | 0.27/0.27/0.22 | 0.53/0.80/0.52 | 0.28/0.55/0.26 | 0.42/**0.21/0.21** | 2.62/4.13/2.67 | 0.40/1.74/0.41 | 6.34/12.4/5.72 | 2.01/2.69/1.78 |
| 360 | 1.49/3.95/1.65 | 0.43/1.15/0.45 | 0.20/1.18/0.21 | 0.42/1.24/0.43 | 0.15/0.23/0.13 | 0.35/0.76/0.35 | 0.18/0.54/0.18 | 0.21/**0.10/0.10** | 1.62/4.01/1.73 | 0.36/1.78/0.37 | 4.17/12.3/4.09 | 1.23/2.59/1.20 |
| 480 | 1.18/4.00/1.22 | 0.40/1.14/0.41 | 0.19/1.18/0.20 | 0.34/1.21/0.34 | 0.09/0.22/0.09 | 0.28/0.74/0.28 | 0.15/0.53/0.15 | 0.05/0.04/0.04 | 1.22/3.87/1.27 | 0.35/1.80/0.35 | 2.96/12.1/3.01 | 0.87/2.48/0.87 |

**Reading the table.**
- On MAE alone, the house beats us standalone almost everywhere. The exceptions are FERRY and GEN, which are closure-driven, and REBLD at 30.
- MAE is the wrong yardstick for a mean forecast of a skewed variable. The PnL tests below decide.
- The V1 damage-only formulas make FERRY, GEN and REBLD much worse (FERRY $1.08-1.10 at 480), so closure must be in the engine.

### 7.3 Is it tradable? PnL per share of sign(signal - house) marked at settlement (holdout, n = 200)

Decomposition. **Bias-only** = fixed sign from the training mean of settle - house. **Info-only** = the sign of (ours - house - bias) applied to (settle - house - bias). IC = correlation of those two quantities. Cells are bias-only / info-only (t) / IC.

| age | PORT | ISLE | GRID | FERRY | GEN | HAVEN | REBLD | ARCH |
|---|---|---|---|---|---|---|---|---|
| 15 | +1.77 / +0.10 (0.1) / -.02 | +0.62 / +0.12 | +0.88 / +0.06 | +0.44 / +0.03 | +0.50 / +0.30 (3.7) / .20 | +2.96 / +0.15 | +2.22 / +1.07 (1.9) | +7.98 / +1.32 |
| **30** | +1.87 / **+3.04 (4.7) / .35** | +0.65 / **+0.82 (4.0) / .32** | +0.94 / +0.56 (4.1) / .26 | +0.51 / **+0.56 (5.3) / .46** | +0.53 / +0.36 (5.1) / .43 | +3.19 / **+1.96 (3.1)** | +2.25 / **+2.25 (4.8) / .48** | +8.21 / +4.24 (3.0) |
| 60 | +1.85 / +2.19 (4.1) / .15 | +0.61 / +0.32 (2.0) | +0.90 / +0.38 (3.2) | +0.51 / +0.31 (3.4) | +0.51 / +0.21 (3.2) | +3.11 / +1.94 (3.5) | +2.26 / +1.58 (4.0) | +8.00 / +2.83 (2.3) |
| 120 | +1.85 / +1.00 (2.3) / .09 | +0.65 / +0.35 (3.1) | +0.90 / +0.11 (1.2) | +0.51 / +0.22 (3.5) / .28 | +0.50 / +0.11 (2.0) | +3.15 / +0.60 (1.3) | +2.39 / +0.40 (1.2) | +8.37 / +0.61 |
| 240 | +1.94 / +1.37 (4.3) / .16 | +0.40 / +0.13 | +0.72 / +0.11 | +0.31 / +0.22 (6.7) / .53 | +0.45 / +0.22 (6.2) / .42 | +2.78 / +1.02 (3.1) | +2.13 / +0.58 (2.4) | +7.04 / +0.79 |
| 300 | +1.85 / +1.38 (5.3) / .20 | +0.20 / +0.08 | +0.55 / +0.17 (3.2) | +0.21 / +0.19 (8.9) / .49 | +0.37 / +0.21 (8.3) / .65 | +2.25 / +1.10 (4.0) | +1.78 / +0.88 (4.6) | +5.60 / +1.88 (3.2) |
| 360 | +1.01 / +0.46 (2.2) | +0.08 / +0.03 | +0.22 / +0.04 | +0.09 / +0.06 (4.2) / .37 | +0.18 / +0.14 (10.9) / .72 | +1.08 / -0.05 | +0.88 / +0.23 | +2.94 / +0.00 |
| 420 | +0.23 / +0.23 | ~0 | ~0 | +0.04 / 0.00 | +0.04 / +0.06 (11.6) / .79 | +0.30 / -0.19 | +0.13 / +0.04 | +0.67 / -0.21 |

**When the edge peaks.**
- The bias edge is flat from age 15 to about 300, then decays to zero by 420.
- The information edge spikes at the first advisory (age 30, IC 0.25-0.49 across all names).
- The PORT information edge comes back during landfall (240-300).
- GEN and FERRY carry information through 360-420.
- At age 0 (formation, before the house reacts) there is a small latency edge (RECON +0.59, t 3.3; REBLD +1.64, t 2.8).

**Execution lag.** Combo-signal PnL at lags of 0 / 4 / 20 / 60 s:

| age | PORT | GRID | HAVEN | ARCH |
|---|---|---|---|---|
| 30 | +3.06 / +1.81 / +0.93 / +0.03 | +0.93 / +0.83 / +0.71 / +0.46 | +3.26 / +3.09 / +1.53 / +0.90 | |
| 120 | +1.85 / +1.70 / +1.42 / +0.85 | +0.90 / +0.92 / +0.83 / +0.59 | +3.15 / +3.03 / +2.69 / +2.01 | +8.37 / +8.54 / +7.11 / +5.74 |

The age-30 PORT edge is a latency race ("house about 45 s to adjust"). The mid-storm edge is not.

**Gap closure.** This is the slope of house[t + lag] - house[t] regressed on ours - house, for lags of 10 s / 60 s / to settlement:
- At age 30: PORT 0.20 / 0.31 / 0.48; FERRY 0.37 / 0.51 / 0.82; GEN 0.27 / 0.42 / 0.78; REBLD 0.38 / 0.51 / 0.82.
- At age 240: PORT 0.00 / 0.01 / 0.11, so the house does not move toward us there; FERRY 0.02 / 0.18 / 0.56; GEN 0.04 / 0.12 / 0.58.

So the gap to our signal can also be traded intraround without holding to settlement. A signal-directed round trip from age 30 to 420 earns PORT +$2.68 (t 4.3) versus a $1.64 unconditional move, ISLE +1.14 vs 0.62, AQUA +0.80 vs 0.41, and REBLD +3.09 vs 2.11. From entries after age 120 the signal mostly reproduces the bias direction.

**Combination weights.** In settle = a + b·house + c·ours (blendB, V2), the weight c on ours is:
- 0.73 for PORT at age 30, then 0.04-0.13 from age 120 on.
- FERRY 0.5-1.0 at all ages, GEN 0.5-1.1, REBLD 0.2-1.0, RECON 0.2-0.97.
- ISLE and AQUA about 0 after age 45.

**House cross-asset inconsistency.** Value HAVEN, REIN and MAT from the damage implied by the house's own island-business prices, using exact closure so far plus the closure forecast:
- HAVEN implied MAE 5.00 vs house 5.64 (age 90), 4.24 vs 4.93 (120).
- REIN 0.27 vs 0.36 at 360-480.
- MAT 0.45 vs 0.52 at 120.

This trade needs no storm forecast at all.

**ETF lag.** The house ARCH and REBLD update about 6 s after their components. The mean |ARCH - basket| in house prices is $4.2 at age 15 and $3.0 at age 30, then $0.4-1.5 later. It is exploitable with create/redeem, but that belongs to the ETF area.

**House bias before and after storms** (model-free, all 600 seasons):
- Rounds 1-2, before the storm, the house prices HAVEN about $1.6-1.8 too low (t 2.6), and REIN (-0.38, t -3.4), RECON (-0.57, t -3.2) and REBLD (-1.78, t -3.2) too high against final settlement.
- After rounds 1-2 the signs flip (HAVEN +1.1-1.6). This involves future-storm risk.

## 8. How the engine should use this (all in `fits/predict.json`)

1. **Every observation:** update cumulative site exposure and closure (exact recursion), and the 30-s velocity. On each advisory, recompute the cone forecasts dcone/dconeb and hcone/hconeb.
2. **At the nearest lower model age** (models exist for the 14 ages; features can be computed at any t), evaluate:
   - kNN against the reference set (`predict_ref.json`: 1800 storms × 14 ages × [x, y, v30x, v30y, R, I, outlook, dso5, dconeb5, hso3], targets, and 4-s future tracks for AT; use all 1800 live);
   - LR_full;
   - AT, about 30 analogs × ≤120 points × 17 sites, a few tens of ms in Python;
   - then blendB, σ from the spread model, and the z-quantile shape.
3. **Map to settlement** with the V2 formulas plus prior-round values. For HAVEN and REIN use scenario expectations; for REIN prefer the empirical or AT scenarios.
4. **Fair value for trading:** combo = a + b·house + c·ours. The coefficients are in `alpha_round3_holdout.combo["blendB|v2|plug"]` and `["blendB|v2|dist"]`. The intercept and house slope absorb the bias.
5. **Trade policy implied by the evidence:**
   - After formation (age 4-30), lean short island businesses, HAVEN, REIN and ARCH, and long RECON, MAT, GEN and REBLD. Size that lean toward the combo signal at the first advisory, and be fast.
   - Unwind as the bias decays (ages 300-420).
   - Keep FERRY and GEN signal-driven all storm.
   - Never fade the house on PORT, ISLE or AQUA after about age 300 on the strength of the physics model alone.

## 9. Caveats

- **Spreads and fills.** All PnL is at the house *consensus mid*, with no spread, inventory skew or fill model. Spreads are only visible on the testnet. Position limits and the 180 actions/min rate limit apply. Size the edges against real spreads before trusting the dollar figures.
- **Sample size and multiple testing.** Round 3 has n = 200 holdout storms, so the SE for PORT PnL is about $0.4-0.7/share. With 12 symbols × 14 ages tested, trust t > 3-4. The bias results (t 5-20 on 600 seasons, same signs in all rounds) are the most robust.
- **Live differences.** The live season can have announcements that change company economics, and the research data has none. Any announcement invalidates the affected settlement formula and possibly the house bias. The bias is a property of the house model; verify it on the testnet, which replays research seasons exactly.
- **Fitting window.** Models are fit on seasons 1-400 only, so the holdout metrics are honest. For live use, re-running the same scripts on 1-600 would slightly improve LR, blends and the spread model. The kNN/AT reference already includes all 1800 storms (field `split`).
- **REIN top.** The top of the REIN layer is not identified (the max season loss is 1766). The formula is valid only up to L ≈ 1767.
- **Closure threshold.** The 0.237 threshold and the closure features rely on 2-s sampling. The closure-based formulas reach $0.01-0.04 MAE with exact inputs.
- **MAE vs mean.** MAE comparisons favour median-like quotes when outcomes are skewed (for example REIN at 90 most of the time). Use the PnL tables for decisions.
- **Age grid.** Hyperparameters were tuned on 300-query (kNN) and 100-query (AT) subsamples, so the per-age configs are somewhat noisy. Between grid ages, use the lower age's models with current features.

## Files

All paths are under `SP/work/se/`:
- **Params:** `fits/predict.json`, with sections damage_model, closure_model, loss_identity, knn (configs and scales), lr (coefficients and names), analog_track, blend, spread (coefficients, calibration, z-quantiles), holdout_rmse, settlement_formulas, alpha_* and house_bias_*.
- **Reference set:** `fits/predict_ref.json`.
- **Cached intermediates:** `predict/cache.json`, `features.json`, `preds_knn.json`, `preds_lr.json`, `preds_at.json`, `eval.json`, and `alpha*.json`.
- **Full tables:** `predict/tables.md`.
