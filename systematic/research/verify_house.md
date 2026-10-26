# verify:house

## verdict

confirmed_with_corrections

## issues

## Verification of the `house` fit (house consensus behaviour)

**How I checked it.** I rebuilt the cache myself from the raw `market.csv`, `feed.jsonl`, `settlement.json` and the manifest, and did not use the fitter's `cache.pkl`. I re-implemented every headline computation with the standard library only. Everything was fitted on seasons 1-400 and tested on 401-600. Scripts are in `work/se/verify-house/` and outputs in `out_*.txt` / `res_*.json`.

**Overall.** Every headline number reproduces exactly. I found no look-ahead and no timing errors. The issues are about which trade schedule to use, how much the error-model overlay is worth, an improvable jump model, and some wording.

### Issues, most severe first

1. **[MEDIUM] The drift-trade entry at 100 s adds risk without adding return.**
   - The average house path is flat from 100 s to about 220 s (PORT sign-adjusted path +2.23% at 100 s vs +2.21% at 175 s, holdout).
   - Entering at 100 s and holding to 540 s, holdout: $4,118/storm, sd $7,946, 68% of storms positive, p10 -$5,159.
   - Entering at 220 s (row 110): $4,005/storm, sd $5,285, 77% positive, p10 -$1,921.
   - The fit set alone points to the same choice: $3,880 (t 17.6) at 100 s vs $3,828 (t 24.0) at 220 s.
   - Full playbook per season, holdout: $25.2k, sd $13.2k, p10 $8.3k, 96% of seasons positive becomes $24.9k, sd $9.3k, p10 $14.3k, 100% positive.

2. **[MEDIUM] The F2 error model does not work as a sizing overlay on the drift trade (report section 9, item 3).**
   - Entry at 220 s, holdout: F2-gated (sign when |pred| > 1%) $3,633/storm vs clock-only $4,005 when exiting at 540 s; $3,687 vs $4,099 when holding to C.
   - Entry at 280 s: $3,186 vs $3,516.
   - The dynamic version re-implemented gives $2,401/storm, t 8.4, break-even 60.5 bp (exact match). The clock-only playbook breaks even at about 98 bp.
   - Most of F2's out-of-sample R2 is the age-bucket intercept (F0), holdout:

     | Symbol | F0 | F2 |
     |---|---|---|
     | PORT | 0.090 | 0.202 |
     | HAVEN | 0.212 | 0.304 |
     | GEN | 0.296 | 0.360 |

   - F2 is still a valid fair-value cross-check, but it should not drive trading on top of the drift.

3. **[LOW-MED] The formation-jump model (linear in x0) can be improved.**
   - The storm's x position at age 0 (x0, uniform 380-620) is the only formation parameter that varies across storms. Radius 80, intensity 1, vx 0 and vy -1.8 are the same in all 1,800 storms.
   - Adding x0² and the season loss already known from earlier rounds' final reports raises holdout R2 (rows 31->34; ETFs 34->37):

     | Symbol | Linear x0 | Quadratic x0 + known loss |
     |---|---|---|
     | PORT | 0.377 | 0.469 |
     | RECON | 0.293 | 0.395 |
     | MAT | 0.208 | 0.293 |
     | REIN | 0.021 | 0.174 |
     | REBLD | 0.262 | 0.346 |
     | HAVEN | 0.260 | 0.287 |
     | ISLE | 0.001 | 0.028 |
     | AQUA | 0.002 | 0.050 |

   - On rows 31->33, PORT goes from 0.477 to 0.591.
   - Splitting x0 into 6 bins gives similar results, so the quadratic is not an artefact. The realised jump does not predict the later drift (holdout R2 about 0 for every symbol), so the drift is a constant bias, not a reversal of each storm's jump.

4. **[LOW-MED] The drift trade is a bet on damage and loses in quiet storms.**
   - These splits use manifest outcomes, so they are diagnostics only, not live features.
   - The formation snipe is positive in every subgroup. Its lowest is +$2,080/storm, in round 3 of seasons whose total loss exceeds $1.4bn. Stall storms are 85% positive.

     | Group | n | Drift from 100 s: mean, % positive | Drift from 220 s: mean, % positive |
     |---|---|---|---|
     | Storm loss < $25M | 84 | -$5,317, 2% | -$1,823, 24% |
     | Storm loss $25-150M | 496 | -$2,230, 36% | +$293, 60% |
     | Storm loss >= $400M | 714 | +$10,330, 97% | +$7,915, 96% |
     | Recurve | 373 | -$548, 50% | +$1,276, 70% |
     | Turned erratic | 114 | -$1,486, 42% | -$120, 46% |

   - F2 gating does not avoid the losing storms (see issue 2).

5. **[LOW] REIN is not worth trading in the clock trades after spreads.**
   - Break-even half-spread: formation 9 bp, drift 12-17 bp. Its gross contribution is about $90 per storm.
   - ISLE, RECON, MAT and REBLD break even at 46-70 bp; HAVEN at 400-490 bp; PORT, GRID, FERRY and ARCH at 110-165 bp.

6. **[LOW] The round-3 final snipe value includes quote noise.**
   - Reproduced: $1,386 per season (holdout). The same sum taken on a fully informed row (row 382, where the house already equals S) is $251.
   - Counting only gaps above 0.3% gives $1,233.
   - It is an upper bound that assumes an exact valuation model.

7. **[LOW] The "direct strike" under-reaction is overstated.**
   - Holdout, 6 s after the advisory: PORT -8.70% (t -7.6), HAVEN -13.56% (t -7.3). These reproduce.
   - The unconditional in-storm bias at that moment is PORT -2.95% and HAVEN -8.33% (holdout, row 50). The part specific to "direct" is about -5.7% for PORT and -5.2% for HAVEN.
   - "To settlement" is only true in round 3. In rounds 1-2 the target is the post-final house price.

8. **[LOW] The F2 buckets have gaps.** Storm ages 30-33, 90-93, 150-153, 210-213, 270-273, 330-333, 390-393 and 480 or more have no bucket. The bot must hold its last prediction there.

9. **[LOW] The report and the params file disagree in places.**
   - Prelim gap-closed fraction is quoted as 0.25-0.45, but GEN is 0.67 (fit) and 0.68 (hold).
   - Error-model R2 is quoted as 0.17-0.36, but REIN is 0.08.
   - `formation_jump_pct_fit` uses rows 31->33, while `formation_jump_x_model_fit` uses rows 31->34.
   - The headline "direct" figures are measured 6 s after the advisory; the body table uses 10 s.
   - Wire accuracy when it disagrees with the official advisory is 17.6% among wires that follow an official advisory. The quoted 19% counts wires sent before advisory 1 as disagreeing.

10. **[INFO] The formation reprice is one step per message, but prices keep moving afterwards.**
    - Taking the full move as rows 25-29 to rows 36-40, the jump at row 32 accounts for 56-65% of it for stocks; by row 38 it reaches 1.0. The mean path is flat after row 34.
    - Exiting at row 34 is right: holding to row 40 adds $111/storm but drops the share of positive storms from 93% to 85%.

11. **[INFO] No look-ahead or off-by-one found.**
    - The market time is 09:30 + i minutes on all 702,000 rows.
    - In all 1,800 storms: storm formed at 60 s, official advisories at 90 + 60(n-1) s, dissipation 540 s, prelim 570 s, final 740 s.
    - Reaction row is floor(t/2)+2 for messages at an even second and floor(t/2)+3 for an odd second. Activity after impacts: even seconds 1,020 vs about 430 baseline; odd seconds 882.
    - The final-report step lands at row 372 for stocks and row 375 for ETFs (median fraction of the move done jumps from 0.00 to 1.00).
    - F2 only uses advisories with t_msg <= t-4. C is used only as the target being predicted.
    - The reaction-model features are all known when the report arrives. Exiting at a single row gives the same result as their 10-row average (PORT R2 0.767 vs 0.765).

12. **[INFO] No overfitting found.**
    - F2 has 576 coefficients, but each regression fits 6 parameters on about 1,200 storms. Holdout R2 is at or above in-sample (PORT 0.180 in-sample, 0.202 holdout).
    - Results are stable across 100-season blocks: snipe $4,188-4,587/storm, drift $3,554-4,249/storm, PORT jump +2.26 to +2.73%.

13. **[INFO] The params file exists, parses and matches the report.**
    - `fits/house.json` is 690 KB.
    - All 576 F2 coefficients match my independent refit (maximum difference 4e-15); holdout R2 values match to 1e-15.
    - Noise, jump, drift and final-gap constants match the report.

## corrections

Corrected parameters are in `work/se/fits/house.verified.json` (block `corrections`).

**1. Drift trade schedule.**
- Enter at row 110 (220 s) instead of row 50 (100 s). Exit at row 270 (540 s), unchanged.
- Short PORT, ISLE, AQUA, GRID, FERRY, HAVEN and ARCH; buy RECON, MAT, GEN and REBLD; full limits. REIN is optional, and I recommend dropping it.
- Expected per-share P&L, fit set (holdout in brackets): PORT 2.57% (2.91), ISLE 1.34 (1.28), AQUA 1.77 (1.72), GRID 2.42 (2.55), FERRY 2.79 (2.76), RECON 1.10 (1.18), MAT 0.98 (1.01), GEN 1.27 (1.31), HAVEN 8.20 (8.77), REIN 0.24 (0.28), ARCH 2.86 (3.02), REBLD 1.08 (1.16).
- Full playbook (formation long held stale to row 35, then this drift), holdout:
  - All 12 symbols: $8,296/storm (t 35.0, 94% positive) and $24.9k/season (sd $9.3k, p10 $14.3k, 100% of seasons positive).
  - Without REIN: $24.7k/season.
  - Lower-risk variant entering at 270 s: $23.6k/season, sd $8.2k.

**2. Formation-jump model.** Replace `bot_constants.formation_jump_x_model_fit` with

J% = b1 + bu·u + bu2·u² + bLk·Lk + bLk2·Lk²

- u = (x0 - 500)/100, where x0 is the storm's x at age 0 (first storm message at 60 s).
- Lk = season insured loss from earlier rounds' final reports, in $bn (0 in round 1).
- The window is the trade window: stocks rows 31->34, ETFs rows 34->37.
- Fit-set coefficients [b1, bu, bu2, bLk, bLk2] and holdout R2:

| Symbol | b1 | bu | bu2 | bLk | bLk2 | Holdout R2 |
|---|---|---|---|---|---|---|
| PORT | +1.732 | +2.607 | +2.105 | +0.446 | -1.248 | 0.469 |
| ISLE | +1.940 | -0.300 | -0.726 | -0.850 | +0.782 | 0.028 |
| AQUA | +2.586 | -0.250 | -1.052 | -0.855 | +0.711 | 0.050 |
| GRID | +2.645 | +1.211 | +0.363 | -0.585 | +0.365 | 0.195 |
| FERRY | +3.534 | +2.287 | -0.739 | -0.952 | +1.059 | 0.239 |
| RECON | -1.146 | -0.973 | -0.586 | -0.903 | +1.570 | 0.395 |
| MAT | -1.147 | -0.558 | -0.253 | -0.290 | +0.835 | 0.293 |
| GEN | -1.398 | -0.485 | -0.285 | +0.187 | +0.123 | 0.122 |
| HAVEN | +8.481 | +5.137 | +2.002 | +3.654 | -3.810 | 0.287 |
| REIN | +0.193 | +0.146 | -0.029 | -0.985 | +1.306 | 0.174 |
| ARCH | +3.192 | +1.709 | +0.441 | +0.254 | -0.702 | 0.272 |
| REBLD | -1.194 | -0.737 | -0.412 | -0.471 | +1.038 | 0.346 |

The trade signs stay fixed. Use the predicted jump only to size each symbol or to skip it when the prediction is below 2× its half-spread.

**3. Error model F2.**
- The coefficients are confirmed unchanged.
- Use it only as a fair-value cross-check, not as sizing on top of the drift.
- Hold the last prediction at storm ages with no bucket (30-33 + 60k, and 480 or more).
- An m×h interaction term (move since open × each island's hurricane probability) adds 0.00-0.03 to holdout R2. It is optional and was not adopted.

**4. Reporting values.**
- Round-3 final snipe: $1,233/season if only gaps above 0.3% are traded; the quoted $1,386 includes about $150-250 of quote noise.
- Direct-strike shortfall specific to the outlook: about -5.7% for PORT and -5.2% for HAVEN, on top of the unconditional bias of -2.95% and -8.33%.
- GEN prelim gap closed: 0.67-0.68.

Everything else in `house.json` is confirmed as is.

## corrected_params_file

/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/fits/house.verified.json

## independent_metrics

All figures are holdout (seasons 401-600) unless marked fit (seasons 1-400). Computed by the scripts in `work/se/verify-house/`: `v_timing.py`, `v_core.py`, `v_err.py`, `v_edge.py`, `v_alt.py`, `v_alt2.py`, `v_etf.py`, `v_react.py`, `v_blocks.py`, `v_misc.py`, `v_params.py`.

**Timing**
- Reaction row is floor(t/2)+2 for messages at an even second and +3 for an odd second (lag 3-4 s).
- ETFs lag the stocks by 3 rows. RMSE of ETF minus basket value, by lag in rows:

| ETF | Lag 0 | Lag 3 |
|---|---|---|
| ARCH | $2.01 | $0.48 |
| REBLD | $0.77 | $0.38 |

- The round-3 final step is at row 372 for stocks and row 375 for ETFs.

**Noise** (round-3 post-final rows against S)
- sd 0.145-0.154%, phi 0.84-0.86, sd of 2-s changes 0.081-0.086%.
- Bias within ±0.013%. Overnight gap 0.00% (sd 0.135%).

**Formation jump, rows 31->33, fit / hold**

| Symbol | Mean jump | Same-sign share |
|---|---|---|
| PORT | +2.49 / +2.59% | 83% |
| ISLE | +1.45 / +1.37% | 82-84% |
| AQUA | +1.93 / +1.86% | 87-88% |
| GRID | +2.63 / +2.65% | 97% |
| FERRY | +3.03 / +3.04% | 86% |
| RECON | -1.27 / -1.29% | 93% |
| MAT | -1.10 / -1.10% | 95-97% |
| GEN | -1.38 / -1.41% | 95-97% |
| HAVEN | +9.48 / +9.56% | 95% |
| REIN | +0.18 / +0.19% | 72% |
| ARCH (rows 34->36) | +3.22% (hold) | 96% |
| REBLD (rows 34->36) | -1.25% (hold) | 97% |

Holdout R2 of the jump model (rows 31->34), linear x0 vs quadratic x0 + known loss: PORT 0.377 vs 0.469, HAVEN 0.260 vs 0.287, RECON 0.293 vs 0.395.

**Formation snipe at full limits** (stocks rows 31->34, ETFs rows 34->37)
- $4,351/storm, t 32.4, 93% positive, p10 +$482. Fit: $4,357, t 47.0.
- Per share: HAVEN $3.71 / 9.81%, ARCH $9.47 / 3.29%, PORT $1.93 / 2.62%.

**Drift trade, exit 540 s**

| Entry | Mean $/storm | t | % positive | p10 | sd |
|---|---|---|---|---|---|
| 100 s | +4,118 | 12.7 | 68% | -5,159 | 7,946 |
| 220 s | +4,005 | 18.6 | 77% | -1,921 | 5,285 |
| 270 s | +3,591 | 19.6 | 82% | -764 | 4,492 |
| 300 s | +3,325 | 20.3 | 82% | -482 | — |

**Full playbook per season**
- Fitter's schedule (drift from 100 s): $25,229, sd $13,245, 96% of seasons positive, p10 $8,275.
- Drift from 220 s: $24,889, sd $9,282, 100% positive, p10 $14,316.
- Break-even half-spread for the whole playbook: about 98 bp.

**Error model, holdout R2 against "trust the house"**

| Symbol | F0 | F1 | F2 |
|---|---|---|---|
| PORT | 0.090 | 0.192 | 0.202 |
| ISLE | 0.084 | 0.161 | 0.171 |
| AQUA | 0.096 | 0.176 | 0.198 |
| GRID | 0.259 | 0.314 | 0.328 |
| FERRY | 0.206 | 0.319 | 0.328 |
| RECON | 0.134 | 0.187 | 0.208 |
| MAT | 0.212 | 0.245 | 0.263 |
| GEN | 0.296 | 0.344 | 0.360 |
| HAVEN | 0.212 | 0.285 | 0.304 |
| REIN | 0.036 | 0.069 | 0.076 |
| ARCH | 0.247 | 0.303 | 0.316 |
| REBLD | 0.194 | 0.240 | 0.260 |

- Single F2 entry at 220 s held to C: $3,687, t 17.5. Clock-only from the same entry: $4,099.
- Dynamic F2 version: $2,401/storm, t 8.4, break-even 60.5 bp (fit: 50.7 bp).

**Round-3 final snipe at full limits**
- Mean $1,386/season, median $938, p90 $3,033. Noise-only benchmark $251. Only gaps above 0.3%: $1,233.
- Mean |gap|: PORT 1.72%, HAVEN 3.12%, ARCH 0.95%.

**Other claims**
- **Direct-strike advisory 1**, remaining move to C at +6 s: PORT -8.70% (t -7.6), HAVEN -13.56% (t -7.3).
- **Prelim reaction model, rounds 1-2:**

| Symbol | Out-of-sample R2 | Snipe | t |
|---|---|---|---|
| PORT | 0.765 | +1.54% | 11.5 |
| HAVEN | 0.512 | +2.30% | 12.5 |
| GRID | 0.760 | +0.79% | — |

- **Prelim gap closed, round 3 (hold):** PORT 0.24, GEN 0.68, others 0.23-0.45.
- **Round-3 open slope (S on open price):** 0.93-1.04.
- **Wire accuracy:** correct 39.7%; agrees with the latest official 31.4% of the time; correct when agreeing 87.9%; correct when disagreeing 17.6%.
- **ETF lag trade, ARCH, gap above 0.3%:** 39.4 trades/round at +0.765% per trade. A placebo in the quiet pre-storm minute gives 0.9 trades/round at +0.11%, so the in-storm profit is real lag, not noise.

**Real time and caveats**
- **Known live:** the event clock, x0 at 60 s, earlier rounds' final reports, official advisories, house quotes.
- **Not known live:** C in rounds 1-2 (used only as the target), S before 740 s of round 3, and whether a storm will be low-damage. The drift trade's losses come from those storms and cannot be filtered out with F2.
- **Gross only:** every edge is measured against the 2-s consensus, with no other teams and no spreads or inventory skew. Check on the testnet before sizing up.
