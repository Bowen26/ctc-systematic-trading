# fit:insurance

## headline

- Settlement is deterministic in the season loss L ($M) and fits within rounding: HAVEN = 75 - 0.06*clip(L-400, 0, 900), so retention R = 400, layer top 1300 and floor 21. REIN = 90 - 0.035*max(L-1300, 0), with no top seen up to the largest loss of 1766.2 (REIN 73.68). Holdout RMSE is 0.0029 for HAVEN and 0.0016 for REIN, max error 0.0065. ARCH moves -0.06 per $M through its 1 HAVEN while 400 < L < 1300.
- The insured loss of each storm follows an exact formula: L = 9*PORT% + 6*ISLE% + 2.5*AQUA% + 7*GRID% + 1.2*FERRY% (total exposure $2570M). Storms damage only the value that is left, so the mean storm loss falls from 408 to 331 to 258 across rounds 1-3 (train). Losses in different rounds correlate at -0.2 to -0.28, and this falls to about 0 once that depletion is removed, so the storms themselves are independent.
- Preliminary damage per company = final x exp(0.30 z), with no bias, independent across companies and rounded to 0.1. The preliminary industry loss is about 20% off (sd of final/prelim is 0.19-0.21). It overstates big storms: when prelim > $800M the bias is +$183M. A posterior estimate cuts the holdout error from $105M to $69M, but the house's own nowcast is better ($44M). Do not trade against the house in the window between the preliminary and final reports using the preliminary report alone.
- Fair value at the round-1 open: E[HAVEN] = 40.50 with the training storms (39.88 with all 600 seasons; bootstrap sd 0.85). E[REIN] = 88.94 (88.89; sd 0.09). P(L < 400) = 5.5% and P(L > 1300) = 20%. Tables against cumulative loss after round 1 (C1), after round 2 (C2) and the round-3 storm pattern are in the report.
- The house prices REIN too high at the opens of rounds 1 and 2. Settlement minus house open averages -0.38 +- 0.11 and -0.33 +- 0.11 over all 600 seasons (t 3.4 and 3.1), with the same sign in train and holdout. Selling REIN when the model is below the house made +0.38 and +0.45 per share (t 3.4 and 4.3).
- The house prices HAVEN too low at the opens of rounds 1, 2 and 3. Settlement minus house open averages +1.81, +1.57 and +1.05 (t 2.6, 2.6, 2.3 pooled). The holdout alone has the same sign but is not significant (+0.54 +- 1.18 at the round-1 open).
- Live season: REIN at 89.36 is worth about 0.37-0.47 per share less than its price (sell). HAVEN at 39.55 is worth about 0.3-1.0 per share more (buy), which is within one bootstrap sd. The live HAVEN open sits +1.41 above the research average open. That offset is pure house noise, but it carries over (correlation 0.63 at the round-2 open).
- Round 3 settlement is known exactly at the final report (real 740 s). The house reacts about 4 s after each report, with quote noise sd of 0.06 for HAVEN and 0.13 for REIN.

## params_file

/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/fits/insurance.json

## scripts

[
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/extract.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/fit_settle.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/loss_dist.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/indep.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/prelim.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/prelim_noise.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/build_library.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/insmodel.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/validate.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/prelim_eval.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/house.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/house_stats.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/house2.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/tables.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/bootstrap.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/state_example.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/render_tables.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/make_params.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/explore1.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/explore2.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/explore3.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/explore4.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/insurance/explore5.py"
]

## report

The insurer payouts are an exact function of the season's total insured loss, and I fitted that function from code I ran. Fitting used seasons 1-400; seasons 401-600 are the holdout. Results:

- **HAVEN (layers):** HAVEN pays the layer from $400M to $1300M of season loss.
- **REIN (layers):** REIN pays everything above $1300M. No top shows up in the data.
- **Loss formula:** each storm's insured loss is a fixed formula of the five island-business damage percentages.
- **Fair values and mispricing:** the fair-value tables account for storms only damaging the value that is left. Against them, the house prices REIN too high at the opens of rounds 1 and 2, which holds on both the train and holdout seasons. It prices HAVEN too low, but that result is weaker and not significant on the holdout alone.

I used only the Python standard library, ran everything as `python3 -I`, and wrote nothing into the data folder. The work directory is `SP/work/se/insurance`, where SP is the scratchpad path in the briefing. The parameters are in `SP/work/se/fits/insurance.json`. The bot can import the model from `insmodel.py` (stdlib only).

## 1. Data and methods

- **Feature cache.** `extract.py` reads all 600 seasons once and writes `features.json` (18 MB, 2.7 s). For each season it stores:
  - the storms in the manifest;
  - the preliminary and final `damage_report` messages from the feed;
  - the settlement of all 12 symbols;
  - the full 390-row HAVEN, REIN and ARCH house-price series of each round;
  - the first and last price row of every symbol in each round.

  Every other script reads this cache.
- **Checks.** These all held:
  - `season_loss_musd` equals the sum of the three storm losses (max difference 0.1, which is rounding).
  - The feed's final reports equal the manifest in every storm (0 mismatches).
  - Every season has exactly 6 damage reports. Preliminary reports come at 14:15 (real 570 s) and final reports at 15:40 (real 740 s).
  - ARCH and REBLD settle exactly at their baskets (max difference 6e-14).
- **Library of storms.** `build_library.py` turns every training storm into a vector of "raw fractions": raw_c = damage_c / (100 - prior cumulative damage_c), for each of the 5 companies. This is the share of the company's remaining value that the storm destroyed. `library.json` holds the 1200 training storms; `library_all.json` holds all 1800 for live use.
- **Expected values.** E[f(L)] is computed by exact enumeration over the library (1200 storms, or 1200 x 1200 pairs when two storms remain). The round-1 open uses a 200k-300k-path Monte Carlo over three storms. A fast columnar implementation (`ev_one_fast`, `ev_two_fast`) does a two-storm expectation in 0.19 s.
- **A correction made during the work.** An early run used a random 300-400 storm subsample of the library. That alone added about 0.5-1.0 of noise to the round-2 values, shared by every season. All reported numbers use the full library.

## 2. Settlement as a function of season loss L (`fit_settle.py`)

Model: value = v0 + slope * clip(L - attach, 0, top - attach).

- The breakpoints were grid-searched: a 10 $M grid over a wide range, then 1 $M, then 0.1 $M.
- For each pair of breakpoints, the intercept and slope come from least squares.
- The fit used seasons 1-400 only.

| | attach ($M) | top ($M) | value at L below attach | slope per $M | value at top |
|---|---|---|---|---|---|
| HAVEN fit | 400.0 | 1300.0 | 75.0003 | -0.060001 | 20.9999 |
| REIN fit | 1300.0 | not identified (best fit at 1766.3, the largest loss seen) | 90.0001 | -0.035003 | (73.68 at L = 1766.2) |

The clean formulas:

- **HAVEN** = 75 - 0.06 * clip(L - 400, 0, 900). This is a $900M layer at 6.0 cents per $M, worth 54 points in total. HAVEN is 75 when nothing is paid and has a floor of 21 at L >= 1300.
- **REIN** = 90 - 0.035 * max(L - 1300, 0). It attaches exactly where the HAVEN layer ends.

**REIN layer top.** The data cannot locate a top. The fit is linear all the way to the largest season loss of 1766.2, where REIN = 73.68. Moving the top above 1766 does not change the error at all (the error profile is flat there). So the top is at least 1766. The total insured value on the map is 2570 (section 3), so a season loss of 2570 would give REIN 61.85 if there is no top.

**How sharply the breakpoints are pinned down.** Moving the HAVEN retention by 0.5 $M raises the error 5-6 times; moving its top by 0.5 $M raises it 12 times. The REIN attachment is pinned similarly.

Errors of the clean formulas:

| | train MAE | train RMSE | train max | holdout MAE | holdout RMSE | holdout max |
|---|---|---|---|---|---|---|
| HAVEN | 0.0020 | 0.0028 | 0.0060 | 0.0021 | 0.0029 | 0.0060 |
| REIN | 0.0004 | 0.0012 | 0.0060 | 0.0007 | 0.0016 | 0.0065 |

The settlement is deterministic. The errors are fully explained by rounding: prices are rounded to 0.01 (error up to 0.005) and L to 0.1 (worth up to 0.003 on HAVEN).

Where the 600 seasons land:

| Where L falls | Seasons |
|---|---|
| Below the HAVEN retention (L <= 400) | 29 |
| Inside the HAVEN layer | 453 |
| In the REIN layer (L > 1300) | 118 |

**Price impact per $M:**

| L range | HAVEN | REIN |
|---|---|---|
| 0-400 | 0 | 0 |
| 400-1300 | -0.06 | 0 |
| Above 1300 | 0 | -0.035 |

**ARCH** holds 1 HAVEN, so on top of its island-business exposure ARCH loses 0.06 per $M while 400 < L < 1300.

## 3. The insured-loss formula and depletion (`loss_dist.py`, `explore3.py`)

I regressed the final industry loss on the final damage % of the five companies (no intercept). The fit is exact:

**L_storm = 9.0 * PORT + 6.0 * ISLE + 2.5 * AQUA + 7.0 * GRID + 1.2 * FERRY** (damage % points added by this storm)

- Fitted coefficients: 9.0001, 6.0004, 2.4999, 7.0001, 1.1997.
- The error has sd 0.044, which is just the rounding of damage % to 0.01. The holdout RMSE is 0.043.
- So the insured values are PORT $900M, ISLE $600M, AQUA $250M, GRID $700M and FERRY $120M: **$2570M in total**.
- The cumulative season loss is the same formula applied to the cumulative damage D: C = sum of K_c * D_c, where K_c are the coefficients above.
- The preliminary industry loss is the same formula applied to the preliminary damage % (error sd 0.32, from rounding the preliminary % to 0.1).

**A storm damages only the remaining value: damage_c = (100 - D_c) * raw_c.** PORT has a single site, so it shows this cleanly. In direct-strike storms the PORT raw fraction averages 0.49, 0.50 and 0.52 in rounds 1, 2 and 3. The additive damage halves after round 1 (49.2 vs 24.0 and 22.8 points). The same holds for eyewall (0.23 / 0.23 / 0.25) and chain (0.22 / 0.21 / 0.18).

What happens with the other patterns:

| Pattern | PORT damage |
|---|---|
| Stall | Never touches PORT |
| Recurve | Never touches PORT |
| Direct | Hits PORT |
| Eyewall | Hits PORT |
| Chain | Hits PORT |

Cumulative damage can reach 99.48% for PORT.

**Example of why the damage mix matters (`state_example.py`).** Two states have nearly the same C2 but different damage. If the round-3 storm is a direct strike:

| State before round 3 | C2 | E[L3] if storm 3 is direct | E[HAVEN] | E[REIN] |
|---|---|---|---|---|
| PORT 80% damaged | 887 | 247 | 31.04 | 89.97 |
| PORT untouched | 858 | 534 | 24.60 | 84.67 |

## 4. Per-storm insured loss distribution (`loss_dist.py`, `indep.py`)

**By round ($M):**

| split | round | n | mean | sd | p10 | p25 | p50 | p75 | p90 | max |
|---|---|---|---|---|---|---|---|---|---|---|
| train | 1 | 400 | 407.6 | 269.7 | 80.7 | 159.9 | 417.4 | 585.8 | 800.8 | 1183.1 |
| train | 2 | 400 | 330.7 | 240.2 | 59.4 | 121.5 | 280.6 | 498.4 | 642.4 | 1201.2 |
| train | 3 | 400 | 257.9 | 229.3 | 35.4 | 85.0 | 171.7 | 420.8 | 600.5 | 1048.7 |
| holdout | 1 | 200 | 422.8 | 272.3 | 58.6 | 164.3 | 430.2 | 629.3 | 812.8 | 1165.0 |
| holdout | 2 | 200 | 352.3 | 261.7 | 57.2 | 120.8 | 310.6 | 521.7 | 734.9 | 1080.7 |
| holdout | 3 | 200 | 260.4 | 220.5 | 34.5 | 83.8 | 203.2 | 394.7 | 550.8 | 985.2 |

The season total (train) has mean 996 and sd 327, with p10 599, p50 1020, p90 1412 and max 1766. The holdout seasons ran heavier: mean 1035, sd 328.

The rounds have clearly different distributions (KS test p < 1e-4), but the cause is depletion, not the round itself. A regression of storm loss on pattern, round and prior cumulative loss gives:

- round-2 effect: -7.9
- round-3 effect: +1.0
- prior cumulative loss: **-21.1 $M per $100M** already lost.

**By true pattern ($M):** pattern is the pattern at formation. "erratic_turned" is any storm that later turned erratic.

| pattern | n train | mean | sd | p10 | p50 | p90 | max | n holdout | holdout mean | holdout sd |
|---|---|---|---|---|---|---|---|---|---|---|
| direct | 262 | 529.5 | 258.2 | 200.5 | 519.3 | 889.5 | 1183.1 | 152 | 511.6 | 276.9 |
| chain | 250 | 245.2 | 237.8 | 19.7 | 144.1 | 635.0 | 997.8 | 132 | 255.4 | 240.4 |
| recurve | 256 | 128.0 | 60.4 | 49.4 | 123.4 | 210.4 | 280.8 | 91 | 132.0 | 71.9 |
| stall | 175 | 379.2 | 174.0 | 87.5 | 452.9 | 533.1 | 626.3 | 89 | 391.8 | 149.6 |
| eyewall | 163 | 489.5 | 207.9 | 235.3 | 467.1 | 779.4 | 1201.2 | 81 | 496.8 | 211.5 |
| erratic | 20 | 155.4 | 151.4 | 0.0 | 102.6 | 341.8 | 492.4 | 15 | 35.1 | 35.0 |
| erratic_turned | 74 | 222.0 | 224.2 | 9.2 | 160.4 | 595.2 | 928.5 | 40 | 199.0 | 213.0 |

**Mean loss by pattern and round (train)** shows the depletion:

| pattern | round 1 | round 2 | round 3 |
|---|---|---|---|
| direct | 626 | 479 | 424 |
| chain | 330 | 249 | 136 |
| recurve | 140 | 123 | 118 |
| stall | 490 | 358 | 286 |
| eyewall | 595 | 472 | 388 |

Stall and recurve losses are capped: stall at 626 and recurve at 281 in training.

**Correlation across rounds within a season:**

| | rounds 1 v 2 | 1 v 3 | 2 v 3 |
|---|---|---|---|
| Actual losses, train | -0.21 | -0.28 | -0.14 |
| Actual losses, holdout | -0.24 | -0.20 | -0.23 |
| Fresh-equivalent losses, all 600 | -0.01 | -0.04 | -0.03 |

"Fresh-equivalent" is what each storm would have cost on an undamaged map (sum of K_c * 100 * raw_c). A permutation test on rounds 1 v 2 gives p = 0.84. Patterns are also independent across rounds: chi-square p-values are 0.14, 0.25 and 0.78 in train (one holdout pair has p = 0.016 out of 9 tests).

Conclusion: **the storms are independent draws.** The negative correlation of losses comes entirely through depletion, and depletion shrinks the variance of the season total (its variance is 0.58 times the sum of the per-round variances). Ignoring this costs real money. Treating the three losses as independent draws gives E[HAVEN] at the round-1 open of 42.7-43.0 and E[REIN] of 87.5-87.7, about 2.5 and 1.3 off. A point estimate f(E[L]) gives 39.23 and 90.00.

## 5. Preliminary vs final damage reports (`prelim.py`, `prelim_noise.py`, `prelim_eval.py`)

**Noise model: preliminary_c = final_c * exp(0.30 z), with z ~ N(0,1), independent per company and per storm, rounded to 0.1.** Evidence for log(prelim/final), using final >= 2 points:

- **Bias and spread:** mean +0.002 (train), sd 0.300; holdout mean -0.001, sd 0.310.
- **Shape:** skew 0.06 and kurtosis 2.96. The quantiles match a normal: p1 -0.693 vs -0.696, p95 0.505 vs 0.496.
- **Same for every company:** sd 0.294-0.305.
- **Same at every size:** sd 0.28-0.33 from 2-5 points up to 40+ points.
- **Independent across companies:** the correlation of relative errors is -0.06 for ISLE-AQUA and 0.00 for GRID-FERRY.
- **Zeros:** a final of 0 always gives a preliminary of 0. A preliminary of 0 with a positive final happens only when the final is <= 0.08 (rounding).
- **No cap:** the preliminary is not capped at the remaining value (max 181.7%; 40 cases exceed what was left).

The error is therefore **relative, not absolute**. For PORT, the sd of the absolute error grows from 0.7 points (prelim < 5) to 15 points (prelim >= 40).

**Industry loss (sum of 5 noisy terms):**

- Preliminary minus final: mean +17.6, sd 94.8 (train); +16.0, sd 103.8 (holdout).
- Final/preliminary: mean 0.990, sd 0.193, p5 0.70, p95 1.33 (train). Holdout: mean 0.998, sd 0.207.
- Least squares: final = 36.4 + 0.846 * prelim, error sd 84.
- The error does not depend on the round or the pattern (relative error sd 0.17-0.22 in every group).
- It does depend on size, because big preliminaries tend to be inflated by noise and by values above what was left:

| Preliminary loss ($M) | mean final/prelim (train) |
|---|---|
| < 50 | 1.05 |
| 100-200 | 1.01 |
| 600-800 | 0.94 |
| 800-1000 | 0.88 |
| > 1000 | 0.77 |

**Estimating the final storm loss from the preliminary report (holdout, 600 storms):**

| estimator | bias | RMSE ($M) |
|---|---|---|
| E0: preliminary as is | +16.0 | 105.0 |
| E4: least-squares line | -3.4 | 91.2 |
| E5: preliminary x mean ratio for its size bin | -2.4 | 81.8 |
| E1: per-company posterior (library prior, lognormal 0.30) | -1.5 | 69.0 |
| E1p: same, prior limited to the storm's true pattern | -0.1 | 65.6 |
| E3: joint posterior over library vectors (only about 5.7 effective samples) | +4.0 | 76.7 |

- The E1 sd estimate is well calibrated: 66% of finals fall within 1 sd and 95% within 2 sd.
- For preliminaries above $800M, E0 has bias +183 and RMSE 275, while E1 has RMSE 117.

**Round-3 settlement given the preliminary report (holdout):**

| estimate | HAVEN RMSE | REIN RMSE |
|---|---|---|
| Naive f(C2 + prelim) | 3.12 | 2.23 |
| E1 | 3.14 | 0.91 |
| E1p | 2.83 | 0.89 |
| House, all 600 seasons | 2.03 | 0.66 |

**The house beats the report-only estimate** (section 7c).

## 6. Fair-value tables (`tables.py`; JSON in `tables.json` / `tables_all.json`)

How the tables are built:

- Each value is the exact E[f(L)] over library B: all storms of seasons 1-400, applied to the remaining value.
- The 1-D tables take each C, find the 40 nearest training states, rescale them to that C, and average.
- The "state sd" column shows how much the damage mix moves the value at a given C. The bot should use its own exact state D through `insmodel.ev_two_fast` / `ev_one_fast`.

Library B and the no-state models were compared on the holdout. For predicting settlement at the round-3 open, B has HAVEN RMSE 11.04 and REIN 2.02 (bias -0.16 / -0.19). The no-state models are worse: round-3 losses only gives 11.41 / 2.68, and pooled losses 11.20 / 3.62. At the round-2 open, B has 14.46 / 2.65 against 15.19 / 3.51. B's predicted E[L3] is calibrated across C2 bins. Example: when C2 is 900-1200, B predicts 215 and the actual average is 198; a no-state model predicts 258.

**(a) Round-1 open.**

| | train library | 600 library | bootstrap sd |
|---|---|---|---|
| E[HAVEN] | 40.50 | 39.88 | 0.85 |
| E[REIN] | 88.94 | 88.89 | 0.09 |

- The plain average settlement is 40.56 / 89.06 over the 400 training seasons and 39.95 / 88.99 over all 600.
- E[L] = 999. P(L < 400) = 0.055 and P(L > 1300) = 0.20.
- L quantiles: p10 521, p50 1026, p90 1423, p99 1683.
- Settlement sd: HAVEN 17.2, REIN 2.8.

E at the round-1 open once storm 1's pattern is known (train library):

| storm-1 pattern | E[HAVEN] | E[REIN] |
|---|---|---|
| direct | 33.15 | 88.48 |
| chain | 44.16 | 89.42 |
| recurve | 48.72 | 89.67 |
| stall | 39.02 | 88.41 |
| eyewall | 34.53 | 88.25 |
| erratic | 47.46 | 89.49 |

**(b) Round-2 open vs cumulative loss after round 1 (C1).** Since C1 = L1, this is also the value during round 1 as a function of the storm-1 loss.

| C1 | E[HAVEN] train | E[REIN] train | E[HAVEN] 600 | E[REIN] 600 | state sd H/R |
|---|---|---|---|---|---|
| 0 | 54.03 | 89.89 | 53.51 | 89.89 | 0.00/0.00 |
| 100 | 50.66 | 89.80 | 50.09 | 89.79 | 0.13/0.02 |
| 200 | 47.06 | 89.63 | 46.43 | 89.60 | 0.22/0.06 |
| 300 | 43.55 | 89.49 | 43.08 | 89.49 | 0.34/0.18 |
| 400 | 39.69 | 89.09 | 39.27 | 89.09 | 0.27/0.31 |
| 500 | 36.17 | 88.55 | 35.72 | 88.39 | 0.20/0.57 |
| 600 | 32.91 | 88.46 | 32.59 | 88.38 | 0.12/0.81 |
| 700 | 29.69 | 88.50 | 29.48 | 88.51 | 0.17/0.43 |
| 800 | 26.89 | 87.86 | 26.73 | 87.81 | 0.17/0.25 |
| 900 | 24.56 | 86.89 | 24.48 | 86.81 | 0.13/0.25 |
| 1000 | 22.74 | 85.55 | 22.72 | 85.48 | 0.08/0.36 |
| 1100 | 21.57 | 83.89 | 21.57 | 83.80 | 0.08/0.45 |
| 1200 | 21.13 | 82.10 | 21.11 | 81.96 | 0.16/0.91 |

The full grid in steps of 50 is in the JSON. Bootstrap sd of E[HAVEN]: 0.71 at C1 = 0, 0.67 at 300, 0.47 at 600. REIN is not monotone between C1 = 550 and 700. This is real: a large C1 usually means PORT is already destroyed, which limits later losses. It is also why the REIN state sd reaches 0.9.

**(c) Round-3 open vs C2.** Since C2 = C1 + L2, this is also the value during round 2 as a function of the storm-2 loss. Each pattern cell is E[HAVEN] / E[REIN] if storm 3 has that pattern.

| C2 | E[L3] | HAVEN | REIN | P(L>1300) | direct | chain | recurve | stall | eyewall | erratic |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 393 | 67.98 | 90.00 | 0.00 | 59.90 / 90.00 | 70.69 / 90.00 | 75.00 / 90.00 | 69.81 / 90.00 | 63.50 / 90.00 | 73.74 / 90.00 |
| 200 | 365 | 62.76 | 89.99 | 0.00 | 50.83 / 89.99 | 66.72 / 90.00 | 74.65 / 90.00 | 63.78 / 90.00 | 55.50 / 89.97 | 71.86 / 90.00 |
| 400 | 332 | 55.11 | 89.96 | 0.01 | 43.04 / 89.90 | 60.42 / 90.00 | 67.73 / 90.00 | 54.28 / 90.00 | 46.60 / 89.92 | 65.59 / 90.00 |
| 500 | 319 | 50.03 | 89.89 | 0.04 | 38.12 / 89.65 | 54.88 / 89.99 | 62.29 / 90.00 | 49.78 / 90.00 | 42.01 / 89.83 | 60.14 / 90.00 |
| 600 | 309 | 45.05 | 89.66 | 0.08 | 33.10 / 88.92 | 49.19 / 89.90 | 57.03 / 90.00 | 45.84 / 90.00 | 37.53 / 89.58 | 54.78 / 90.00 |
| 700 | 289 | 40.46 | 89.54 | 0.10 | 30.86 / 88.59 | 44.47 / 89.83 | 50.98 / 90.00 | 39.71 / 90.00 | 33.35 / 89.40 | 49.04 / 89.98 |
| 800 | 263 | 35.96 | 89.58 | 0.11 | 29.90 / 88.90 | 40.32 / 89.83 | 44.26 / 90.00 | 32.22 / 89.89 | 29.21 / 89.33 | 43.18 / 89.85 |
| 900 | 240 | 31.51 | 89.47 | 0.19 | 27.64 / 89.16 | 35.75 / 89.82 | 37.93 / 90.00 | 26.71 / 89.15 | 25.54 / 88.89 | 37.59 / 89.66 |
| 1000 | 225 | 27.60 | 88.77 | 0.32 | 24.59 / 88.21 | 30.90 / 89.51 | 32.34 / 90.00 | 24.17 / 87.76 | 23.03 / 87.60 | 32.26 / 89.54 |
| 1100 | 207 | 24.26 | 87.85 | 0.45 | 22.48 / 87.41 | 26.60 / 89.10 | 26.55 / 89.90 | 22.38 / 85.47 | 21.63 / 85.76 | 27.01 / 89.27 |
| 1200 | 190 | 21.81 | 86.39 | 0.70 | 21.22 / 85.90 | 22.87 / 88.28 | 22.15 / 89.08 | 21.22 / 82.95 | 21.09 / 83.39 | 22.91 / 88.37 |
| 1300 | 175 | 21.00 | 83.95 | 0.99 | 21.00 / 83.35 | 21.01 / 86.29 | 21.00 / 86.54 | 21.00 / 80.26 | 21.00 / 80.78 | 21.02 / 86.30 |
| 1400 | 160 | 21.00 | 81.22 | 1.00 | 21.00 / 80.95 | 21.00 / 83.45 | 21.00 / 83.49 | 21.00 / 77.52 | 21.00 / 78.27 | 21.00 / 83.31 |
| 1500 | 147 | 21.00 | 78.77 | 1.00 | 21.00 / 78.88 | 21.00 / 80.89 | 21.00 / 80.69 | 21.00 / 74.98 | 21.00 / 76.01 | 21.00 / 80.57 |
| 1600 | 134 | 21.00 | 76.47 | 1.00 | 21.00 / 76.91 | 21.00 / 78.50 | 21.00 / 78.09 | 21.00 / 72.62 | 21.00 / 73.90 | 21.00 / 78.02 |
| 1800 | 112 | 21.00 | 72.40 | 1.00 | 21.00 / 73.29 | 21.00 / 74.23 | 21.00 / 73.53 | 21.00 / 68.56 | 21.00 / 70.17 | 21.00 / 73.52 |

- E[L3 | C2, pattern] at C2 = 0 / 600 / 1200 ($M): direct 620 / 529 / 214, chain 285 / 233 / 118, recurve 150 / 99 / 107, stall 420 / 286 / 298, eyewall 565 / 437 / 287, erratic 188 / 137 / 115.
- The 600-library values are within 0.4 of the train values: for example HAVEN 44.83 at C2 = 600, and REIN 83.78 at 1300.
- Bootstrap sd of E[HAVEN]: 0.42 at C2 = 300, 0.37 at 600, 0.25 at 900, 0.05 at 1200. For REIN: 0.05 at 900, 0.12 at 1200.
- The erratic pattern is based on only 20 storms, so its column is the least certain.

**(d) During round 3 as a function of the storm-3 loss.**

- **Exactly:** HAVEN = 75 - 0.06 * clip(C2 + L3 - 400, 0, 900) and REIN = 90 - 0.035 * max(C2 + L3 - 1300, 0).
- **Thresholds:** HAVEN starts paying when L3 > 400 - C2, and it reaches its floor and REIN starts paying when L3 > 1300 - C2.
- **After the preliminary report, P3:** take E over the final/preliminary ratios seen for that preliminary size. In each cell below, the naive f(C2 + P3) is in brackets.

| C2 | P3=200 | P3=400 | P3=600 | P3=800 | P3=1000 |
|---|---|---|---|---|---|
| 0 | 75.00 (75.0); 90.00 | 72.91 (75.0); 90.00 | 64.85 (63.0); 90.00 | 56.96 (51.0); 90.00 | 52.58 (39.0); 90.00 |
| 400 | 63.19 (63.0); 90.00 | 50.12 (51.0); 90.00 | 41.00 (39.0); 90.00 | 33.13 (27.0); 89.90 (90.0) | 29.24 (21.0); 89.62 (86.5) |
| 600 | 51.19 (51.0); 90.00 | 38.12 (39.0); 90.00 | 29.47 (27.0); 89.73 (90.0) | 24.06 (21.0); 88.19 (86.5) | 22.03 (21.0); 86.82 (79.5) |
| 800 | 39.19 (39.0); 90.00 | 26.46 (27.0); 89.80 (90.0) | 22.02 (21.0); 87.07 (86.5) | 21.02 (21.0); 82.96 (79.5) | 21.08 (21.0); 80.38 (72.5) |
| 1000 | 27.20 (27.0); 90.00 | 21.04 (21.0); 85.97 (86.5) | 21.02 (21.0); 80.66 (79.5) | 21.00 (21.0); 75.97 (72.5) | 21.00 (21.0); 73.42 (65.5) |
| 1200 | 21.00 (21.0); 86.61 (86.5) | 21.00 (21.0); 78.99 (79.5) | 21.00 (21.0); 73.67 (72.5) | 21.00 (21.0); 68.97 (65.5) | 21.00 (21.0); 66.42 (58.5) |

The full grid is in the JSON. The better per-company estimate is `insmodel.ev_given_prelim(prelim_dmg, D2, C2, lib)`, or `ev_after_prelim` for rounds 1 and 2.

## 7. House vs model (`house.py`, `house_stats.py`, `house2.py`)

**How the house prices behave:**

- Row i of market.csv is real second 2i.
- The house reacts to the preliminary report at row 287 and to the final report at row 372, about 4 s late each time.
- Each round opens at the previous round's close.
- Quote noise before a storm (iid sd): HAVEN 0.023, REIN 0.052, ARCH 0.17. The spread around the window mean is 0.048 / 0.107 / 0.35.

**Round-1 open.** The HAVEN open has mean 38.14 and sd 1.56; REIN has mean 89.36 and sd 0.12. The HAVEN open is a noise draw for each season:

- Its correlation with settlement is -0.009.
- It stays the same through the pre-storm minute (correlation 0.999 between rows 0 and 29).
- It carries into later house prices: the correlation of (house - model) with the round-1 offset is 0.63 at the round-2 open, 0.36 at the round-3 open and 0.38 after the round-1 preliminary report.

**How the house values its opens.** Comparing the house's pre-storm average with candidate valuation rules:

| | best match | intercept and fit | other rules |
|---|---|---|---|
| Round-3 open, HAVEN | my state-aware E[f] | house - model = -0.50, slope 1.005, RMSE 1.27 | no-state / point rules: RMSE 1.6-4.0 |
| Round-2 open, HAVEN | my state-aware E[f] | house - model = -1.41, slope 0.98, RMSE 2.15 | others: RMSE >= 5.3 |
| Round-3 open, REIN | my state-aware E[f] | +0.26, RMSE 0.48 | — |
| Round-2 open, REIN | my state-aware E[f] | +0.41, RMSE 0.60 | — |

So the house prices off the damage state, much as my model does, plus the persistent season offset and a bias: HAVEN too low, REIN too high.

**(a) Edge at the round opens.** The model-free evidence is settlement minus the house price at the open; t-statistics are from 600 seasons.

| symbol | open | settle - house, all 600 (+- se) | train | holdout | model - house (all) | sell/buy on the model's sign, $/share (t) |
|---|---|---|---|---|---|---|
| HAVEN | round 1 | +1.81 +- 0.69 | +2.44 +- 0.84 | +0.54 +- 1.18 | +2.31 | +1.98 (2.9) |
| HAVEN | round 2 | +1.57 +- 0.60 | +2.07 +- 0.74 | +0.58 +- 1.02 | +1.41 | +1.30 (2.2) |
| HAVEN | round 3 | +1.05 +- 0.45 | +1.24 +- 0.56 | +0.65 +- 0.78 | +0.50 | +0.35 (0.8) |
| REIN | round 1 | -0.38 +- 0.11 | -0.30 +- 0.13 | -0.54 +- 0.21 | -0.43 | +0.38 (3.4) |
| REIN | round 2 | -0.33 +- 0.11 | -0.26 +- 0.13 | -0.47 +- 0.19 | -0.42 | +0.45 (4.3) |
| REIN | round 3 | -0.09 +- 0.09 | -0.09 +- 0.11 | -0.10 +- 0.15 | -0.26 | +0.21 (2.4) |

Reading the table:

- **REIN is too high at the round-1 and round-2 opens.** The sign is the same in train and holdout. The holdout slope of (settlement - house) on (model - house) is 1.04 +- 0.44 at the round-2 open.
- **HAVEN is too low**, but the evidence is weaker:
  - The pooled t-statistics are about 2.3-2.6.
  - On the holdout alone it is not significant.
  - Its settlement sd is 11-17, so any single season is very noisy.

**(b) After the preliminary report (row 290):**

- **Rounds 1 and 2:** the same biases persist. HAVEN settle - house is +1.63 / +1.13 and REIN is -0.30 / -0.12.
- **Round 3:** the house is efficient. HAVEN settle - house is +0.004 +- 0.083. The house error (HAVEN RMSE 2.03, REIN 0.66) is smaller than my report-only model (3.07 / 0.98).

**(c) Round-3 window between the preliminary and final reports.** I backed out the storm-3 loss implied by the house price, using HAVEN in its linear range and otherwise REIN.

| estimate of the round-3 storm loss | holdout RMSE ($M) |
|---|---|
| House just before the preliminary report (rows 270-284), from the storm track alone | 48 |
| House after the preliminary report | 44 |
| E1 posterior | 62 |
| Raw preliminary report | 88 |

- The house's pre-report error is about 0.14-0.17 in log terms.
- On the preliminary report the house moves only about 0.26-0.31 of the way toward the reported number. That is a Bayesian-like update.
- Blending E1 with the house's pre-report estimate (fitted on train) gives a holdout RMSE of 43.4, against 44.2 for the house after the report. **No usable edge.** Beating the house here would need a track-based damage model better than about 0.15 log error, which is another area's job.
- There is a small REIN effect: holdout slope 0.35 +- 0.06 and settle - house +0.09 +- 0.06. It is not robust in train (slope -0.02).

**(d) After the round-3 final report:** the house equals settlement to within its quote noise (mean difference 0.003 for HAVEN and 0.0001 for REIN, sd 0.046 / 0.099). The apparent "edge" there is just that noise; it is only worth anything if the spread is narrower than about 0.05 for HAVEN and 0.11 for REIN.

**Live season.** Starting prices are HAVEN 39.55 and REIN 89.36.

- **REIN:** sell. The edge is 89.36 - E[REIN] = **+0.42 (train library) / +0.47 (600 library) / +0.37 (average settlement)** per share. At the 200 limit that is about $75-95 expected, with settlement sd 2.8 per share.
- **HAVEN:** buy. E[HAVEN] - 39.55 = **+0.95 / +0.33 / +0.40**, which is within one bootstrap sd of 0.85.
- **The live HAVEN offset:** the live open sits +1.41 above the research average open. Because the offset carries over, the predicted (house - model) at the round-2 open is about -0.48, compared with -1.41 on average.

## 8. What can and cannot be known in real time

**Known exactly:**

- Each storm's loss, from the final report at real 740 s of each round. The bot can rebuild D and C from those reports.
- The round-3 settlement at the round-3 final report, 40 s before the close.
- The value at the open of round k+1, under the model. It is a deterministic function of D and the storm library; no feed information arrives before the next storm forms.

**Known with noise:**

- The storm loss at the preliminary report: about 20% relative error, or 65-69 $M RMSE after the posterior.
- The storm loss during the storm, from the track. The house manages about a 15% log error before the report.
- The storm pattern, from advisories. That is another area's work; once known, use the pattern columns.

**Not known:**

- The REIN layer top. It is at least 1766; assume no top.
- Per-site damage. Only per-company damage is reported, so the model applies depletion at the company level.
- Announcements. The live season can have announcements that change insurer economics, for example the retention or layer bounds. The formulas above would then need re-fitting from the announcement.

## 9. Caveats

1. **Library choice.** The 1-D tables depend on which seasons form the library:
   - The train and 600-season libraries differ by up to about 0.6 in HAVEN (the holdout seasons had heavier losses).
   - The bootstrap sd is 0.85 at the round-1 open, falling to about 0.05 near the HAVEN floor.
   - The fresh-state-only library A overstates the season mean (1031 vs 996).
   - I used library B, which pools all rounds.
2. **Company-level depletion.** It is exact for PORT and approximate for the multi-site companies. Fresh-equivalent round-3 losses are about 8% below round 1 (378 vs 413), which shows this approximation.
3. **Statistical strength of the house edges.** The HAVEN edges are about 2-3 sigma when pooled and not significant on the holdout alone. The REIN edges are 3-4 sigma and consistent across splits.
4. **No order-book data.** Spreads are not in the data, so per-share edges must be compared with the testnet spread.
5. **1-D tables smooth over the damage mix.** The "state sd" is up to 0.9 for REIN, so the bot should use its exact damage state.

## Files

All in `SP/work/se/insurance/`:

- **Bot module:** `insmodel.py` (stdlib only)
- **Storm libraries:** `library.json` (train), `library_all.json` (all 600)
- **Cached feature file:** `features.json`
- **Rendered tables:** `report_tables.md`

Parameters: `SP/work/se/fits/insurance.json`.
