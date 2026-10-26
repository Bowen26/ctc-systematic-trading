# fit:house

## headline

- The house is systematic and can be learned. Stocks reprice 3-4 s after every feed message, in one step. ETFs reprice 9-10 s after (ETF = basket NAV from 6 s earlier). Quote noise is AR(1): sd 0.15% of price, phi 0.85 per 2 s, sd of 2-s changes 0.083%.
- Storm formation (real 60 s) is the biggest edge, and it runs on the clock alone. The house jumps the same way in 83-97% of storms: HAVEN +9.5%, FERRY +3.0%, GRID +2.6%, PORT +2.5%, ARCH +3.3% (at 70 s); RECON/MAT/GEN -1.1 to -1.4%. Being positioned before the jump earns $4,351 per storm at full limits on holdout (t 32, 93% of storms positive).
- After the jump the house is too optimistic. It then drifts back toward damage until about 480-540 s. Short island names/HAVEN/REIN/ARCH and buy rebuilders/REBLD from 100 s to 540 s: $4,118 per storm on holdout (t 12.7). Per share: HAVEN +8.1%, ARCH +3.1%, FERRY +3.1%, PORT +2.8%, GRID +2.6%. The jump plus the drift make $25.2k per season gross, positive in 96% of holdout seasons.
- The house under-reacts to storm news; it does not over-react. After a first 'direct strike' advisory, PORT falls another 8.7% and HAVEN another 13.6% from the price 6 s after the advisory to settlement (holdout, t -7.6 and -7.3). A linear house-error model (storm age bucket, move since open, latest advisory probabilities) beats the house out of sample: holdout R2 0.17-0.36, and the dynamic version breaks even at a 60 bp half-spread.
- Round-3 final report snipe: the house is unbiased but stale before 744 s (ETFs 750 s). Mean gap PORT 1.7% ($1.06), HAVEN 3.1% ($1.09), ARCH 0.95% ($2.64): $1,386 per season at full limits on holdout. It needs the valuation model to supply settlement. Rounds 1-2 jump by similar amounts at the same timing.
- At the prelim report the house closes only 25-45% of the gap to settlement, and its leftover error cannot be predicted from the prelim contents (holdout R2 <= 0). A simple reaction model predicts the house's own 4-s move (rounds 1-2 OOS R2 up to 0.76), which is enough for a prelim snipe of about 1.5% per share on PORT and 2.3% on HAVEN.
- Do not fade wire rumours. Wire is right 40% of the time, but 19% when it disagrees with the latest official advisory. The house moves only about 0.2-0.25% RMS on a wire, and fading that move makes about 0% on holdout. Round 2/3 opens price known damage efficiently (slope about 1), and the overnight gap is 0.
- Every edge is measured against the 2-s house consensus, gross of spreads and inventory skew, with no other teams in the data. Check on the testnet whether house quotes at 60-63 s and 740-743 s still sit at the stale consensus before sizing up.

## params_file

/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/fits/house.json

## scripts

[
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/build_cache.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/hlib.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/explore1.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/explore2.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/peek.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/noise.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/lag.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/endgame.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/mid.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/errmodel.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/advisory_es.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/impact_mom.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/wire_explore.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/wire.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/opens.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/open_offset.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/live_open.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/formation.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/snipes.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/combo.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/strategies.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/reaction_model.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/house/build_params.py"
]

## report

## House consensus behaviour: Systematic Equities (Hurricane Season), CTC 2026

**Scope.** This report covers the house consensus price in `market.csv` (one row every 2 s, row i = real second 2i), measured against the feed and the settlement. Fit set: seasons 1-400. Holdout: seasons 401-600. Where two numbers appear as "fit / hold", the first is the fit set and the second the holdout.

**Target C.** In round 3, C is the settlement S. In rounds 1-2, C is the post-final house price (mean of rows 376-389). I checked this choice in round 3, where the post-final house price equals S to within quote noise (mean gap at most 0.004 $, row sd 0.15%).

**t-statistics** are computed across storms, which clusters overlapping trades within a storm. The exceptions are labelled "per trade".

**Code.** All numbers come from the scripts in `work/se/house/`. Cached features are in `work/se/house/cache.pkl`, raw outputs in `out_*.txt` and `res_*.json`, and the parameters in `work/se/fits/house.json`.

### 0. Summary
The house updates its price in one step 3-4 s after every message. It is accurate between storms and exact after the final report. During a storm it is systematically wrong:

- **Formation jump.** When the storm forms (real 60 s), it reprices island businesses and HAVEN upward, and rebuilders downward. The direction is the same in 83-97% of storms.
- **Optimism.** From then on it under-prices storm damage.
- **Under-reaction.** Its moves continue rather than reverse: it under-reacts to advisories, impacts and its own price moves.
- **Late correction.** It catches up only as the damage happens (about 480-540 s), and resolves the rest at the dissipation, prelim and final reports.

These biases are worth about $8.4k per storm gross at full position limits. That is about 6x the round-3 final-report snipe, and the biggest part needs no storm model.

### 1. Mechanics

**Lag (`lag.py`).**

- **Activity at each lag.** For each message type I computed the sum over the 10 stocks of squared 2-s price changes, scaled by noise. With noise only, this sum is 10.
  - For messages at an even second t, the activity spike sits entirely in the row covering (t+2, t+4].
  - For messages at an odd second, it sits in (t+3, t+5].
  - So the stock lag is 3-4 s.
- **Storm-observation cross-correlation.** The correlation between house activity and changes in storm velocity peaks at exactly 4 s: 0.106, against 0.008 or less at every other lag. Holdout gives 0.101.
- **Step shape.** At the final report the fraction of the jump completed is 0.00 at rows 369-371 and 1.00 at row 372 for every stock.
- **ETFs.** ETFs move 3 rows (6 s) after the stocks. ETF minus basket NAV built from house stock prices has RMSE $2.06 at lag 0 and $0.48 at lag 3 rows (ARCH); for REBLD it is $0.75 and $0.38. In short, ETF(t) = NAV(t-6 s) plus its own noise.

RMS reaction per stock in the reaction row, above baseline (fit):

| Event | RMS reaction per stock |
|---|---|
| storm_formed | 3.9% |
| final report | 2.3% |
| prelim report | 1.6% |
| dissipated | 1.5% |
| official advisory | 0.63% |
| impact | 0.60-0.63% |
| wire | 0.19-0.25% |

**Quote noise (`noise.py`).** The noise is multiplicative: P = C·(1+n), with n following an AR(1) process.

- Stationary sd is 0.146-0.156% of price for every symbol. I measured it against the known settlement in round-3 post-final rows.
- phi is 0.84-0.86 per 2 s.
- The sd of 2-s changes in quiet windows is 0.082-0.086%. The quiet windows are before formation and between the prelim and final reports, and both match the holdout.
- In dollars, sd of 2-s changes / sd of level:

| Symbol | sd 2-s change | sd level |
|---|---|---|
| PORT | $0.061 | $0.11 |
| ISLE | $0.042 | $0.075 |
| AQUA | $0.023 | $0.041 |
| GRID | $0.030 | $0.056 |
| FERRY | $0.016 | $0.029 |
| RECON | $0.043 | $0.077 |
| MAT | $0.030 | $0.052 |
| GEN | $0.033 | $0.063 |
| HAVEN | $0.035 | $0.064 |
| REIN | $0.073 | $0.13 |
| ARCH | $0.24 | $0.44 |
| REBLD | $0.18 | $0.32 |

- Between the prelim and final reports the house is flat apart from noise.
- The gap from one round's close to the next round's open is 0 (sd 0.13%).

### 2. Formation jump and in-storm drift (`formation.py`, `snipes.py`, `combo.py`)

**The jump.** At real 60 s the storm forms. Stocks reprice at 64 s (row 32); ETFs at 70 s (row 35). Jump J from row 31 to row 33, with the share of storms where the jump has the stated sign:

| Symbol | Jump, fit / hold | Same sign, fit / hold |
|---|---|---|
| PORT | +2.49% / +2.59% | 83% / 83% |
| ISLE | +1.45% / +1.37% | 84% / 82% |
| AQUA | +1.93% / +1.86% | 88% / 87% |
| GRID | +2.63% / +2.65% | 97% / 97% |
| FERRY | +3.03% / +3.04% | 86% / 86% |
| HAVEN | +9.48% / +9.56% | 95% / 95% |
| REIN | +0.18% / +0.19% | 72% / 72% |
| RECON | -1.27% / -1.29% | 94% / 94% negative |
| MAT | -1.10% / -1.10% | 98% / 96% negative |
| GEN | -1.38% / -1.41% | 97% / 96% negative |
| ARCH (at 70 s) | +3.3% | — |
| REBLD (at 70 s) | -1.3% | — |

The jump is the same in all three rounds (PORT +2.58 / +2.51 / +2.37 by round). It also depends on where the storm forms, with x0 = the storm's x position at age 0 and the jump in %:

| Symbol | Jump model (fit) | R2 |
|---|---|---|
| PORT | 2.54 + 2.63·(x0-500)/100 | 0.37 |
| HAVEN | 9.71 + 5.15·(x0-500)/100 | 0.25 |
| FERRY | 3.13 + 2.28·(x0-500)/100 | 0.23 |
| GRID | 2.70 + 1.22·(x0-500)/100 | 0.18 |
| RECON | -1.31 - 0.98·(x0-500)/100 | 0.27 |

**Snipe.** Go long the island names, HAVEN, REIN and ARCH, and short RECON, MAT, GEN and REBLD, at the stale price (stocks at row 31 / 62 s, ETFs at row 34 / 68 s). Exit just after the reaction. Holdout results:

| Symbol | $/share | % | t | Hit rate |
|---|---|---|---|---|
| HAVEN | 3.71 | 9.81 | 31 | 94% |
| ARCH | 9.47 | 3.29 | 33 | 93% |
| PORT | 1.93 | 2.62 | 21 | 81% |
| GRID | 0.97 | 2.67 | 32 | 92% |
| FERRY | 0.55 | 3.09 | 22 | 81% |
| AQUA | 0.53 | 1.92 | 22 | 83% |
| ISLE | 0.71 | 1.40 | 17 | 82% |
| REBLD | 2.72 | 1.27 | 29 | 94% |
| GEN | 0.57 | 1.42 | 31 | 90% |
| RECON | 0.67 | 1.30 | 25 | 90% |
| MAT | 0.40 | 1.12 | 30 | 93% |
| REIN | 0.16 | 0.18 | 10 | 70% |

- Per storm at full limits: **$4,351 (t 32.4), p10 +$483**. Fit: $4,357 (t 47).
- The direction is known before the round starts, so the position can be built while prices are flat at the open (0-60 s).

**The drift that follows.** Mean house path from row 33 to 540 s, all rounds, fit / hold:

| Symbol | Drift, fit / hold |
|---|---|
| PORT | -1.98% / -2.84% |
| ISLE | -1.29% / -1.13% |
| AQUA | -1.87% / -1.41% |
| GRID | -2.17% / -2.63% |
| FERRY | -2.78% / -2.94% |
| HAVEN | -6.59% / -7.73% |
| REIN | -0.28% / -0.37% |
| RECON | +0.82% / +1.15% |
| MAT | +0.86% / +1.01% |
| GEN | +1.18% / +1.33% |

The drift is monotone: about 0 until 200 s, then steep from 300 to 480 s, then flat after 510 s.

**Drift trade.** Short the island names, HAVEN, REIN and ARCH, and buy RECON, MAT, GEN and REBLD, from 100 s to 540 s. Holdout per share:

| Symbol | % | $/share | t |
|---|---|---|---|
| HAVEN | 8.14 | 3.34 | 10.4 |
| ARCH | 3.07 | 9.05 | 12.4 |
| FERRY | 3.12 | — | 10.6 |
| PORT | 2.82 | 1.98 | 5.9 |
| GRID | 2.64 | — | 13.5 |
| AQUA | 1.61 | — | 6.9 |
| GEN | 1.35 | — | 13.1 |
| ISLE | 1.28 | — | 6.9 |
| RECON | 1.17 | — | 8.0 |
| REBLD | 1.17 | — | 10.3 |
| MAT | 1.03 | — | 11.3 |
| REIN | 0.34 | — | 5.5 |

- Per storm: **$4,118 (t 12.7)**, 68% of storms positive. Fit: $3,880 (t 17.6).
- Entering at any time from 70 to 270 s gives $3.6-4.3k per storm.

**Combined.** Hold the formation long until 70 s, then go short from 100 s to 540 s:

- Per storm: $8,410 (t 24.6), 87% of storms positive, holdout.
- Per season: mean **$25.2k**, sd $13.2k, positive in 96% of seasons, p10 $8.3k. Fit: $24.7k, 96%.

**Interpretation (a hypothesis, not proven).** The open price carries the expected damage of the coming storm. At formation the house seems to switch to valuing the storm along a single projected path, which drops much of that expected damage. It then adds the damage back as it happens.

### 3. Mid-storm accuracy in round 3 (`mid.py`)

RMSE of (P-S)/S, and mean bias (S-P)/P (positive = house price too low), holdout:

| Time | PORT RMSE / bias | GRID | FERRY | GEN | HAVEN | ARCH |
|---|---|---|---|---|---|---|
| 0-62 s | 18.6 / -0.2 | 7.0 / +0.3 | 11.6 / +0.9 | 3.6 / -0.2 | 34.9 / +2.4 | 8.8 / +0.7 |
| 100 s | 14.8 / -2.7 | 6.1 / -2.4 | 9.5 / -2.7 | 2.8 / +1.3 | 32.2 / -7.0 | 7.7 / -2.7 |
| 210 s | 10.6 / -2.8 | 4.5 / -2.4 | 5.8 / -2.7 | 2.2 / +1.3 | 23.1 / -8.0 | 5.6 / -2.9 |
| 330 s | 8.6 / -3.0 | 3.2 / -1.8 | 3.3 / -1.6 | 1.6 / +1.1 | 18.1 / -6.8 | 4.2 / -2.2 |
| 450 s | 4.9 / -0.8 | 1.6 / -0.2 | 1.2 / -0.4 | 0.5 / +0.2 | 8.6 / -1.6 | 2.1 / -0.5 |
| 540 s | 4.4 / -0.1 | 1.5 / +0.1 | 0.8 / -0.1 | 0.16 / 0.0 | 7.2 / -0.3 | 1.8 / 0.0 |
| 580 s (post-prelim) | 3.6 / 0.0 | 1.5 / +0.1 | 0.7 / 0.0 | 0.14 | 6.2 / -0.3 | 1.6 |
| 752 s (post-final) | 0.15 | 0.15 | 0.15 | 0.16 | 0.15 | 0.15 |

- The fit set has the same shape. At 210 s the biases are PORT -2.6%, HAVEN -7.5%, GRID -2.4%, FERRY -2.8%, ARCH -2.7%, RECON/MAT/GEN +1.0/+1.0/+1.3%, with t-statistics from -6 to -13 for the island names and +8 to +16 for the rebuilders.
- **The house is most wrong from 100 to 330 s (storm age 40-270 s).** It is too optimistic about damage: island businesses, HAVEN and ARCH are too high, rebuilders too low.
- **It under-reacts to direct-strike outlooks rather than over-reacting.** Ten seconds after advisory 1 says "direct", the remaining move to settlement is:

| Symbol | Fit | Hold |
|---|---|---|
| PORT | -7.95% (t -9.3) | -8.45% (t -7.5) |
| HAVEN | -13.9% | -13.4% |
| GRID | -3.7% | -3.7% |
| ARCH | -4.7% | -4.5% |
| RECON | +2.4% | +2.7% |
| GEN | +1.9% | +1.9% |

- Other outlooks after advisory 1 (fit / hold): "stall" FERRY -7.4% / -7.9%, ISLE -4.2% / -3.6%, AQUA -4.0% / -4.3%; "recurve" AQUA -3.8% / -4.2%; "eyewall" HAVEN -10.8% / -14.7%.

**Efficiency regressions.** I regressed y = (C-P_t)/P_t on the house's move since the open. The slope is +0.2 to +0.5 from 100 s to 330 s in both halves (holdout t up to 16), which means the move continues: under-reaction. The slope decays to about 0 by 450 s. On 20-s moves, the slope is positive mid-storm and negative in the last 30 s of the storm (holdout -0.3 to -0.8, t -2 to -6): a small over-reaction while the storm weakens.

**House-error model (`errmodel.py`).** One model per symbol and per storm-age bucket (4-30, 34-90, 94-150, 154-210, 214-270, 274-330, 334-390, 394-480 s):

- y = b0 + b1·m + Σ g_i·h_i
- m = P_t/P_open_round - 1 (the house's move since the round opened).
- h_i = hurricane-condition probability for each island from the latest official advisory with t_msg ≤ t-4. It is 0 before the first advisory.

Example (age 94-150 s, fit):

- HAVEN: y = -0.099 + 0.219m - 0.043h_cas + 0.064h_cor - 0.088h_azu + 0.029h_pal
- PORT: y = -0.028 + 0.120m - 0.043h_cas + 0.066h_cor - 0.052h_azu + 0.027h_pal
- GEN: y = +0.008 + 0.108m + 0.011h_cas - 0.007h_cor + 0.023h_azu

Holdout R2 against "trust the house" (F0 = intercept only, F1 = + move since open, F2 = full model):

| Symbol | F0 | F1 | F2 |
|---|---|---|---|
| PORT | 0.09 | 0.19 | 0.20 (RMSE 8.07% to 7.21%) |
| ISLE | 0.08 | 0.16 | 0.17 |
| AQUA | 0.10 | 0.18 | 0.20 |
| GRID | 0.26 | 0.31 | 0.33 |
| FERRY | 0.21 | 0.32 | 0.33 |
| RECON | 0.13 | 0.19 | 0.21 |
| MAT | 0.21 | 0.25 | 0.26 |
| GEN | 0.30 | 0.34 | 0.36 |
| HAVEN | 0.21 | 0.29 | 0.30 (RMSE 14.6% to 12.2%) |
| REIN | 0.04 | 0.07 | 0.08 |
| ARCH | 0.25 | 0.30 | 0.32 |
| REBLD | 0.19 | 0.24 | 0.26 |

- Adding outlook dummies or impact flags adds less than 0.01.
- Trading the sign of the F2 prediction when it exceeds 0.5%, per holdout decision: HAVEN +6.6% ($2.53, t 13.9), PORT +3.0% ($2.02, t 8.5), ARCH +2.8% ($8.10, t 15.6), GRID +2.5% (hit 76%), GEN +1.4% (hit 82%).
- A single entry at 220 s held to C: $3,687 per storm (t 17.5).
- Dynamic version, rebalanced every 10 s to target = limit·clip(prediction/3%): $2,401 per storm (t 8.4), break-even half-spread **60 bp**. Fit: 51 bp.

### 4. Advisories and impacts (`advisory_es.py`, `impact_mom.py`, `strategies.py`)

- **After a first "direct" advisory (fit / hold):** PORT is -2.60% / -2.57% at +4 s and -4.82% / -4.89% at +58 s. The drift from +6 s to +58 s is -2.06% (t -6.5) / -2.13% (t -5.0).
- **Response to changes in hurricane probability.** I regressed price moves on x = -Σ (site weight × change in probability). The slope at +4 s versus the slope to C:

| Symbol | +4 s | To C |
|---|---|---|
| PORT | 0.68 | 3.54 |
| ISLE | 0.70 | 3.51 |
| AQUA | 0.47 | 3.14 |
| GRID | 0.22 | 3.13 |
| FERRY | 0.34 | 4.36 |

  So the house's immediate reaction is only 7-22% of the information content.
- **Follow the house's own 6-s reaction to an official advisory** (moves over 0.5%, hold 60 s, holdout): PORT +0.61% ($0.40, t 5.5), HAVEN +1.18% (t 7.2), AQUA +0.55%, FERRY +0.53%. Held to C: PORT +1.45%, HAVEN +2.47%.
- **Impacts, same rule:** FERRY +1.02% (t 6.7), HAVEN +1.36% (t 4.1), GRID +0.48%.
- These effects overlap with the drift trade and the error model.

### 5. Wire rumours (`wire.py`)

**Accuracy.**

| Case | Wire correct |
|---|---|
| All wires | 39.6% (hold 39.8%) |
| Wire agrees with latest official (29.8% of wires) | 87.9% |
| Wire disagrees with latest official | 19.0% (official correct in those cases: 60.4%) |

**House reaction.** The house reacts to wires 3-4 s later, but weakly. RMS is 0.19-0.25% per stock. The mean move in the rumoured direction is +0.03% (wrong, disagreeing wires) to +0.11% (agreeing or correct wires). The largest averages are "direct": PORT -0.18%, HAVEN -0.22%; and "recurve": PORT +0.14%, AQUA -0.12%.

**After a wrong wire that disagreed with the official advisory**, the later move against the rumour (fit) is -0.08% at 30 s, -0.12% at 60 s and -0.43% to C. Correct disagreeing wires keep going (+0.46% at the next advisory, +2.3% to C).

**Fade edge.**

- Fading by outlook gives -0.02% (fit) / +0.005% (hold) to the next advisory, and -0.13% / -0.21% to C.
- Fading the house's realised wire move (above 0.2%) gives -0.13% (fit, storm-clustered t -2.4) / -0.12% (hold, t -1.1).
- Placebo rows lose too, because moves continue in general.
- **There is no fade-the-wire edge against the consensus.**

### 6. Damage reports (`endgame.py`, `snipes.py`, `reaction_model.py`)

**Dissipation (540 s, reaction at 544 s).** The mean |move| is PORT 1.29%, HAVEN 2.4-2.5%, FERRY 0.9%. The average sign is 0. The share of the remaining gap to C that this move closes:

| Symbol | Gap closed |
|---|---|
| GEN | 0.90 |
| FERRY | 0.70 / 0.77 |
| RECON | 0.42 |
| ARCH | 0.31 |
| HAVEN | 0.24 |
| PORT | 0.19 |

Outage-driven names are nearly resolved when the storm ends.

**Prelim report (570 s, reaction at 574 s, ETFs at 580 s), round 3, fit / hold:**

| Symbol | Mean house move | Gap before | Gap left after | Right direction |
|---|---|---|---|---|
| PORT | 1.13% / 1.14% | 2.02% / 1.89% | 1.70% / 1.71% | 69% / 64% |
| HAVEN | 2.21% / 2.08% | 3.86% / 3.49% | 3.11% / 3.11% | 76% / 64% |
| GRID | 0.73% / 0.72% | 1.23% / 0.95% | 0.92% / 0.93% | 81% / 71% |

- The house closes a fraction of 0.25-0.45 of the gap (slope of its move on the gap).
- Its leftover error is not predictable from the prelim contents or its own move: holdout R2 ≤ 0 for every symbol.
- A linear model of the house's own reaction, using the prelim damage, insured loss and the house's move so far, has out-of-sample R2 of 0.2-0.64 in round 3 and up to 0.76 in rounds 1-2.
- Snipe with that model (rounds 1-2, holdout): PORT +1.54% ($0.95, t 11.5), HAVEN +2.30% (t 12.5), ARCH +0.82% ($2.27, hit 90%).

**Final report, round 3 (740 s; stocks stale at row 371 / 742 s, ETFs at row 374 / 748 s).** Snipe value = |S - P_stale|, fit / hold:

| Symbol | Mean $ | Mean % | Median % | Over 2% of storms |
|---|---|---|---|---|
| PORT | 1.06 / 1.06 | 1.72 / 1.72 | 0.22 / 0.26 | 24% / 25% |
| ISLE | 0.36 / 0.33 | 0.78 / 0.68 | — | — |
| AQUA | 0.22 / 0.19 | 0.85 / 0.71 | — | — |
| GRID | 0.32 / 0.32 | 0.91 / 0.92 | — | — |
| FERRY | 0.074 / 0.075 | 0.43 / 0.43 | — | — |
| RECON | 0.24 / 0.25 | 0.46 / 0.48 | — | — |
| MAT | 0.12 / 0.13 | 0.34 / 0.36 | — | — |
| GEN | 0.05 / 0.05 | 0.12 / 0.12 (noise) | — | — |
| HAVEN | 1.11 / 1.09 | 3.11 / 3.12 | 0.91 / 0.91 | 36% / 37% |
| REIN | 0.27 / 0.33 | 0.31 / 0.39 | — | — |
| ARCH | 2.69 / 2.64 | 0.95 / 0.95 | — | — |
| REBLD | 0.74 / 0.77 | 0.34 / 0.36 | — | — |

- The signed gap is about 0 (|t| ≤ 2.1): the house is unbiased, just stale.
- At full limits, summed over symbols: **$1,389 / $1,386 per season** (median $903 / $942, p90 $3.4k / $3.1k).
- **Per row after 740 s:** the mean |gap| is unchanged through row 371. It falls to 0.11-0.13% (noise) at row 372 for stocks and row 375 for ETFs.
- **Seconds until within 0.1% of S:** the step comes after a median of 4 s (stocks) and 10 s (ETFs). The median first time within 0.1% is 4 s for stocks and 8-10 s for ETFs. After that only about 50% of rows sit within 0.1%, because the noise sd is 0.15%.

**Final report, rounds 1-2.** The jump C - P_371 (fit / hold):

| Symbol | Mean jump |
|---|---|
| PORT | 1.56% / 1.64% ($0.95 / $1.01) |
| ISLE | 0.86% / 0.81% |
| AQUA | 0.90% / 0.83% |
| GRID | 0.98% / 1.06% |
| FERRY | 0.49% / 0.45% |
| RECON | 0.41% / 0.42% |
| MAT | 0.33% / 0.34% |
| GEN | 0.12% / 0.13% |
| HAVEN | 3.18% / 3.25% ($1.06 / $1.10) |
| REIN | 0.29% / 0.31% |
| ARCH | 0.90% / 0.92% ($2.50 / $2.57) |
| REBLD | 0.31% / 0.32% |

Timing is the same single step at row 372 (ETFs row 375). A linear model of the house's reaction to the final-minus-prelim difference gives out-of-sample R2 of 0.15-0.35. Snipe with it: PORT +1.48% (t 7.4), HAVEN +2.16% (t 8.1). In round 3 the valuation model should replace this, because settlement is fully determined there.

### 7. Round opens (`opens.py`, `open_offset.py`, `live_open.py`)

- **Rounds 2 and 3 are efficient.** Regressing settlement on the open price gives a slope b ≈ 1:
  - Round 3, fit: PORT 1.04, ISLE 1.04, AQUA 1.05, GRID 0.94, HAVEN 0.94, REIN 0.82 (t of b-1 = -3.0).
  - Round 3, hold: 1.04, 0.96, 1.00, 0.98, 0.95, 0.93.
  - Regressing (S - P_open3) on the known R1+R2 damage gives no significant slope (|t| ≤ 1.6).
- **REIN** is slightly rich at every open: -0.3% to -0.6% (holdout t -2.6 in round 1, -2.5 in round 2).
- **HAVEN** at the round-3 open is cheap when the known season loss is at least $1,000M: +5.7% (t 3.5, n 96) fit / +5.9% (t 2.8, n 58) hold. Pass this to the insurer-layer model.
- **Round-1 opening offset.** Across seasons the round-1 open varies randomly: sd 2.3% for PORT, 4.1% for HAVEN, 1.2% for ISLE. This offset does not predict settlement (slope of S - P on the offset is about -1 to -1.8). About half of it stays in the house price until the round-3 final.
- **Live starting prices vs mean research settlement:**

| Symbol | Gap | In standard errors |
|---|---|---|
| FERRY | +1.5% | +2.1 se |
| GEN | -0.7% | -2.9 se |
| REIN | -0.4% | -3.4 se |
| RECON | -0.7% | -2.1 se |
| REBLD | -0.55% | -2.1 se |

  The gap is within 1 se for the other symbols. This tilt is worth about $600 in total, with high variance.

### 8. ETF lag arbitrage (`strategies.py`)

During the storm, trade the ETF toward the current basket NAV whenever the gap exceeds a threshold, and exit 6 s later. Holdout, per trade:

| ETF | Gap threshold | Trades per round | Mean per trade | $ per round per 1-share clip |
|---|---|---|---|---|
| ARCH | >0.3% | 39 | +0.77% | $89 |
| ARCH | >0.5% | 25 | +1.10% | — |
| REBLD | >0.3% | 27 | +0.45% | $25 |

The per-trade t-statistics are above 100. The same 6-s lag applies to every event:

- ARCH moves +3.3% at 70 s, 6 s after the formation jump in the stocks.
- At the round-3 final it stays stale until 750 s.

Create/redeem lets you flatten the basket leg.

### 9. Edge summary and recommendations (holdout, gross)

| # | Effect | Window | $/share (main symbols) | % | t | $ per storm at limits | Trade? |
|---|---|---|---|---|---|---|---|
| 1 | Formation snipe | pre-60 s to 64/70 s | HAVEN 3.71, ARCH 9.47, PORT 1.93 | 9.8 / 3.3 / 2.6 | 21-33 | 4,351 | **Yes, top priority** |
| 2 | Storm drift (short damage-exposed names) | 100 to 540 s | HAVEN 3.34, ARCH 9.05, PORT 1.98 | 8.1 / 3.1 / 2.8 | 6-13 | 4,118 | **Yes** |
| 3 | F2 error-model overlay (sizing and fair value) | 70-540 s | per decision HAVEN 2.53 | 6.6 | 14 | 2,401 dynamic (break-even 60 bp) | Yes, as sizing on top of #2 |
| 4 | Advisory / impact momentum | event +6 s to +60 s | PORT 0.40, HAVEN 0.37 | 0.6-1.4 | 4-7 | overlaps #2/#3 | Through #3 |
| 5 | Round-3 final snipe | 740 to 744 s (ETFs to 750) | PORT 1.06, HAVEN 1.09, ARCH 2.64 | 1.7 / 3.1 / 0.95 | — | $1,386 per season | Yes, using the valuation model |
| 6 | Prelim / round 1-2 final / dissipation snipes | 4-s windows | PORT ~0.9-1.0 | 1.5-2.3 | 5-12 | — | Yes, with the reaction or valuation model |
| 7 | ETF lag arbitrage | continuous | ARCH 0.77% per trade | — | per-trade >100 | depends on fills | Yes, if latency and fills allow |
| 8 | Fade wire rumours | — | — | about 0 | ≤1 | — | **No** |
| 9 | Round 2/3 opens, quote noise (0.083% per 2 s) | — | — | — | — | — | No (minor tilts in REIN and HAVEN only) |

### 10. What can and cannot be known in real time; caveats

**Known in real time:**

- The clock. Formation at 60 s, advisories at 90 s + 60k, dissipation at 540 s, prelim at 570 s, final at 740 s are all deterministic.
- Every feed message, with 3-4 s (stocks) and 9-10 s (ETFs) of lead over the house.
- The house quotes, and every feature of the error model.

**Not known in real time:**

- The true storm pattern and settlement, until 740 s of round 3.
- C, the post-final house price, in rounds 1-2. It is used here only as an evaluation target. The drift trade exits at 540 s and does not depend on it.

**Not in the research data:**

- Order books, spreads, the house's inventory skew, and other teams' flow.
- Announcements. The packet says the house takes about 45 s to absorb an announcement.
- Whether the live house quotes refresh exactly like `market.csv`.

**Caveats:**

- Every edge is gross, measured against the 2-s consensus. Other teams will see the same formation and drift patterns, and the house skews its quotes against inventory. Check on the testnet that house quotes at 60-63 s and 740-743 s are still at the stale consensus, then size up.
- The h_* coefficients in the error model partly duplicate a storm-damage model. If the bot already has a full fair-value model, use this one only as a cross-check or a fallback overlay on the house price, so the same effect is not counted twice.
- The parameters file `fits/house.json` holds the fit-set coefficients and holdout metrics. Its `bot_constants` block has the timing windows, noise, the expected formation jump and its x0 model, the expected drift, and the expected gaps closed at each report.
