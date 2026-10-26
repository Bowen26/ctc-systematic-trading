# fit:rebuild

## headline

- RECON = 40 + 24·(1 − e^(−D/1000)), where D = season-cumulative Σ(9.0·PORT% + 7.0·GRID% + 1.2·FERRY%) in $M (ISLE and AQUA do not count). Holdout (seasons 401-600) RMSE 0.0029, max error 0.006, which is the floor set by settlement rounding.
- MAT = 28 + 14·(1 − e^(−L/1200)), where L = season insured loss in $M = Σ(9P + 6I + 2.5A + 7G + 1.2F)%, i.e. the running sum of industry_loss_musd. Holdout RMSE 0.0027, max 0.005.
- GEN = 32 + 18·(1 − e^(−O/120)), where O = Σ over storms and substations of weight × real seconds the substation is closed. Closed means wind I·exp(−(d/r)²) > 0.35, fitted from the impact messages; 120 real s = 1 market hour. Holdout RMSE 0.025, max 0.12. O can be computed live from the 2-s storm feed and is final when the storm ends, 200 s before the final damage report.
- Capacity is shared over the whole season, not reset each storm. Per-storm forms fit far worse (holdout RMSE 0.62 / 0.28 / 0.31). Each storm's damage % is of the original value and the season total is a plain sum.
- Marginal value: RECON 0.024·e^(−D/1000) per $M, which is 0.216 per PORT point at D = 0 and 0.099 at the season median D = 779. MAT 0.0117·e^(−L/1200) per $M. GEN 0.15·e^(−O/120) per weighted second. A 90th-percentile storm (665 $M) lifts RECON 11.7 from zero but only 4.3 if 1000 $M was already done.
- The house opens rebuilders too high versus settlement. Across all 600 seasons: RECON +0.57 / +0.47 / +0.30, MAT +0.22 / +0.17 / +0.13, GEN +0.22 / +0.13 / +0.09 at the round 1 / 2 / 3 opens (t-stats about 1.5 to 3.2).
- In the storm, the GEN nowcast beats the house from storm age about 200 s (holdout round 3 RMSE 0.30 vs 0.55 at age 300, 0.05 vs 0.15 at age 400). For RECON/MAT, plugging in the preliminary report is worse than the house (0.70 vs 0.42), so a track-based damage nowcast is needed to beat it before the final report.

## params_file

/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/fits/rebuild.json

## scripts

[
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/common.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/extract.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/explore1.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/explore2.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/fit_recon_mat.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/free_weights.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/explore_impact.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/fit_wind.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/fit_wind2.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/features.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/explore_gen.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/fit_gen.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/gen_refine.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/gen_final.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/gen_resid_other.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/expectation.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/fresh_frac.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/conditional.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/trend.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/house_bias.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/realtime.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/gen_nowcast.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/alt_measures.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/marginal.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/make_params.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/rebuild/export_fresh.py"
]

## report

# Rebuilders (RECON, MAT, GEN): fair-value laws

Every number below comes from scripts in `SP/work/se/rebuild/`, using Python 3.14 standard library only, run with `python3 -I`.
- **Fit set:** seasons 1-400. **Holdout:** seasons 401-600.
- **Parameter file:** `SP/work/se/fits/rebuild.json`.
- **Bot helper file:** `SP/work/se/fits/rebuild_fresh_fractions.json`.

## 1. Summary of the laws

All three values follow V = a + b·(1 − exp(−D/c)). D is **cumulative over the whole season** (every storm so far). The value starts at a when D = 0 and approaches a + b as D grows. In the table, *Fitted* is the free fit on 1-400 and *Used* is the rounded law (the simulator's constants, as far as the data can tell).

| Symbol | Damage measure D | Fitted (a, b, c) | Used (a, b, c) | Holdout RMSE | Holdout max error |
|---|---|---|---|---|---|
| RECON | Σ over storms of 9.0·PORT% + 7.0·GRID% + 1.2·FERRY% ($M of port + grid + ferry damage at insured value) | 39.998, 23.996, 999.6 | **40, 24, 1000** | 0.0029 | 0.0060 |
| MAT | Σ over storms of 9.0·PORT% + 6.0·ISLE% + 2.5·AQUA% + 7.0·GRID% + 1.2·FERRY% = season insured loss in $M (running sum of `industry_loss_musd`) | 28.001, 14.001, 1200.3 | **28, 14, 1200** | 0.0027 | 0.0051 |
| GEN | O = Σ over storms and the 5 GRID substations of weightᵢ × real seconds that substation i is closed. Closed means I·exp(−(dᵢ/r)²) > 0.35. | 32.001, 18.025, 120.3 | **32, 18, 120** | 0.0251 | 0.120 |

- **Fit quality.** For RECON and MAT the train and holdout RMSE (0.0029 / 0.0027) equals the noise from rounding settlements to 0.01 (0.01/√12 = 0.0029), so these laws are effectively exact.
- **GEN error source.** GEN's 0.025 comes from interpolating when a substation opens and closes between the 2-s observations. It implies an outage error of only about ±0.7 weighted seconds (p1-p99).
- **No hidden extra terms.** GEN's leftover error is not related to GRID damage (correlation 0.000) or to closures at other companies' sites (|correlation| ≤ 0.08).
- **Substation weights confirmed.** Fitted corrections to the substation weights are all ≤ 0.0005.
- **GEN time unit.** c = 120 real seconds = 3600 market seconds, i.e. **1 market hour of weighted grid outage**. In market hours H, GEN = 32 + 18·(1 − e^(−H)).
- **REBLD.** Settlement is exactly 2·RECON + 2·MAT + GEN (max difference 3e-14). Built from the three laws, it has holdout RMSE 0.026 and max error 0.127.
- **Scouting range explained.** RECON's minimum of 41.30 is D = 56 $M and its maximum of 58.15 is D = 1411 $M. Low-damage seasons therefore settle far below the 52.57 open.

## 2. Methods

### 2.1 Data cache
`extract.py` reads every feed, the manifest, settlements and market data once into `rebuild/cache.json` (29 MB, about 3 s). It holds:
- storm observations [age, x, y, vx, vy, r, I];
- impact messages;
- preliminary and final damage reports;
- settlements;
- house prices at each round's first and last row, and every 10 s.

### 2.2 The dollar weights of the island businesses
- Regressing each storm's insured loss on its five damage percentages (1800 storms, `explore2.py`) gives an exact identity: **loss = 9.0001·PORT + 6.0003·ISLE + 2.5000·AQUA + 7.0001·GRID + 1.1996·FERRY**, RMSE 0.044 $M (rounding), with no constant.
- So the insured values are PORT $900M, ISLE $600M, AQUA $250M, GRID $700M and FERRY $120M. Season loss equals the sum of the storm losses to within 0.1.
- These are the dollar weights. The stock-value weights from the brief (PORT 100, ISLE 60, AQUA 35, GRID ≈49, FERRY ≈25) fit clearly worse (table 2.4).
- A fit with free weights per company (`free_weights.py`, Nelder-Mead) recovers the same weights:
  - RECON: implied $M per point PORT 9.008, GRID 7.000, FERRY 1.199, ISLE 0.004, AQUA 0.000 (with c = 1000).
  - MAT: PORT 8.997, ISLE 5.998, AQUA 2.500, GRID 7.003, FERRY 1.197 (with c = 1200).
- This confirms the packet: RECON repairs the port, grid and ferry terminals, and MAT supplies all repairs.

### 2.3 How damage accumulates
- Each storm's `damage_pct` is a share of the **original** value. The season total is a plain sum, which never exceeds 100% (maximum seen 99.48).
- Each storm damages only what is left. For PORT, pct / (100 − prior) has mean 19.69 / 19.76 / 19.86% in rounds 1 / 2 / 3, while the raw pct falls from 19.7 to 12.2 (`fresh_frac.py`).
- Treating the total as multiplicative (1 − Π(1 − d)) fits worse: RECON RMSE 0.45, MAT 0.24.

### 2.4 Which concave shape fits, and is capacity per season or per storm?
Holdout RMSE and max error, with c refit on 1-400 for each shape (`fit_recon_mat.py`, `fit_gen.py`). "Per storm" means V = a + b·Σ_storms g(X_storm), i.e. crews reset after each storm.

| Shape | RECON per season | MAT per season | GEN per season | RECON per storm | MAT per storm | GEN per storm |
|---|---|---|---|---|---|---|
| a + b·(1 − e^(−D/c)) | **0.0030 / 0.006** | **0.0027 / 0.005** | **0.023 / 0.080** | 0.621 / 1.64 | 0.281 / 1.20 | 0.311 / 1.44 |
| a + b·D/(D + c) | 0.035 / 0.108 | 0.018 / 0.077 | 0.030 / 0.149 | 0.621 / 1.64 | 0.281 / 1.20 | 0.313 / 1.46 |
| a + b·log(1 + D/c) | 0.072 / 0.222 | 0.035 / 0.152 | 0.046 / 0.300 | 0.621 / 1.64 | 0.281 / 1.20 | 0.314 / 1.48 |
| a + b·√D | 0.149 / 0.429 | 0.085 / 0.349 | 0.087 / 0.354 | 1.287 / 4.01 | 0.532 / 2.27 | 0.641 / 2.49 |
| a + b·min(D, cap) | 0.487 / 1.30 | 0.226 / 1.08 | 0.300 / 1.38 | 0.638 / 1.68 | 0.279 / 1.34 | 0.323 / 1.38 |
| linear | 0.648 / 1.76 | 0.283 / 1.35 | 0.359 / 1.96 | 0.648 / 1.76 | 0.283 / 1.35 | 0.359 / 1.96 |

The GEN column uses closure integrated with a 0.5-s midpoint rule. The final GEN method (integer ages) gives 0.025 / 0.12 (table 2.6).

**Conclusion:** the saturating exponential with **season-wide capacity** is the law. Crews and supplies that one storm uses stay used for the rest of the season.

Other damage measures tried, all with the exponential shape (holdout RMSE, coarse c grid; `alt_measures.py`):

| Symbol | Measure | Holdout RMSE |
|---|---|---|
| RECON | insured $ of PORT + GRID + FERRY | 0.0097 (coarse c) / 0.0029 (refined c) |
| RECON | insured $ of all five companies (= season loss) | 1.68 |
| RECON | insured $ of PORT + GRID only | 0.173 |
| RECON | stock-value weights | 0.216 |
| RECON | unweighted % | 1.05 |
| MAT | season insured loss | 0.0040 |
| MAT | stock-value weights | 0.095 |
| MAT | unweighted % | 0.868 |
| MAT | PORT + GRID + FERRY only | 0.673 |
| GEN | weighted closure seconds | 0.025 |
| GEN | unweighted closure seconds | 0.700 |
| GEN | weighted time within 1.0·r | 0.607 |
| GEN | weighted time within 1.25·r | 0.748 |
| GEN | weighted wind integral | 0.782 |
| GEN | cumulative GRID damage % | 1.52 |
| GEN | island impacts weighted by grid weight | 1.99 |

### 2.5 Wind model and the closure threshold (for GEN)
- **Calibration.** We used the impact messages (first hurricane conditions on an island, `fit_wind.py` / `fit_wind2.py`, seasons 1-200, 2400 storm-island pairs).
- **Prediction rule.** The impact is predicted at the first integer storm age where the wind at any of the island's sites crosses the threshold. Between the 2-s observations, x, y, r and I are interpolated linearly.
- **Result.** Wind = I·exp(−(d/r)²) with threshold **θ = 0.35** matches the impact age within 0.5 s for **93.9%** of pairs and within 2 s for **99.7%**, with no false alarms.
- **Alternatives are worse.**
  - Exponent 1.5: 94% within 2 s.
  - Exponent 1: 79% within 2 s.
  - θ of 0.345 or 0.355: worse.
  - Smoothing the intensity: worse. The noisy-looking intensity values in the feed are the true state.
- **Intensity at the end of the storm.** It falls linearly from its age-420 value to 0 at age 480: I(t) = I(420)·(480 − t)/60.
- **Closure seconds.** For each substation, count the seconds with wind > 0.35 and weight them by the `case.json` weights:

| Substation | x | y | weight |
|---|---|---|---|
| Cascadia A | 420 | 110 | 0.30 |
| Cascadia B | 560 | 140 | 0.20 |
| Coral | 770 | 285 | 0.18 |
| Azure | 825 | 475 | 0.17 |
| Palm | 245 | 510 | 0.15 |

### 2.6 Final GEN closure computation (`gen_final.py`)

| Variant | How closure is counted | Holdout RMSE | Max error |
|---|---|---|---|
| **int1** (recommended) | Evaluate every integer age; odd ages linearly interpolated; each counts 1 s | 0.0251 | 0.120 |
| herm1 | As int1, but positions interpolated with the velocity (Hermite) | 0.0237 | 0.102 |
| obs2 | Only the 2-s observations, each counts 2 s (simplest to run live) | 0.0272 | 0.088 |

A per-storm cache of O is in `rebuild/gen_outage.json`. Cumulative O at every 10 s of storm age is in `rebuild/gen_curves.json`.

## 3. Marginal value: how much each rebuilder moves (`marginal.py`)

dV/dD = (b/c)·e^(−D/c). The upside still left is b·e^(−D/c).

**RECON** (per $M: 0.024·e^(−D/1000))

| Cumulative D level | D | V | Per $M | Upside left | Per PORT point | Per GRID point | Per FERRY point |
|---|---|---|---|---|---|---|---|
| 0 | 0 | 40.00 | 0.0240 | 24.00 | 0.216 | 0.168 | 0.029 |
| median after round 1 | 190 | 44.15 | 0.0199 | 19.85 | 0.179 | 0.139 | 0.024 |
| median after round 2 | 517 | 49.69 | 0.0143 | 14.31 | 0.129 | 0.100 | 0.017 |
| season p25 | 419 | 48.21 | 0.0158 | 15.79 | 0.142 | 0.111 | 0.019 |
| season median | 779 | 52.98 | 0.0110 | 11.02 | 0.099 | 0.077 | 0.013 |
| season p90 | 1196 | 56.74 | 0.0073 | 7.26 | 0.065 | 0.051 | 0.009 |

**MAT** (per $M of insured loss: 0.01167·e^(−L/1200))

| Cumulative L level | L | V | Per $M | Upside left | Per PORT point | Per ISLE point | Per AQUA point | Per GRID point | Per FERRY point |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 0 | 28.00 | 0.0117 | 14.00 | 0.105 | 0.070 | 0.029 | 0.082 | 0.014 |
| median after round 1 | 417 | 32.11 | 0.0082 | 9.89 | 0.074 | 0.049 | 0.021 | 0.058 | 0.010 |
| median after round 2 | 717 | 34.30 | 0.0064 | 7.70 | 0.058 | 0.039 | 0.016 | 0.045 | 0.008 |
| season median | 1022 | 36.03 | 0.0050 | 5.97 | 0.045 | 0.030 | 0.012 | 0.035 | 0.006 |
| season p90 | 1415 | 37.69 | 0.0036 | 4.31 | 0.032 | 0.022 | 0.009 | 0.025 | 0.004 |

**GEN** (per weighted second: 0.15·e^(−O/120))
- At O = 0, one real second of closure is worth 0.045 for Cascadia A, 0.030 for Cascadia B, 0.027 for Coral, 0.026 for Azure and 0.023 for Palm.
- Typical levels: O median after round 1 = 24 (V 35.21, 0.123 per weighted second); median after round 2 = 48 (37.89, 0.101); season median = 73 (40.23, 0.081); season p90 = 106 (42.59, 0.062).

**Price move from one new storm adding X, by damage already done (D_prev).** ΔV = b·e^(−D_prev/c)·(1 − e^(−X/c)). The X columns are the per-storm 50th / 75th / 90th / 99th percentiles.

| RECON: D_prev \ X ($M) | 159 | 385 | 665 | 966 |
|---|---|---|---|---|
| 0 | 3.54 | 7.67 | 11.66 | 14.87 |
| 500 | 2.15 | 4.65 | 7.07 | 9.02 |
| 1000 | 1.30 | 2.82 | 4.29 | 5.47 |
| 1250 | 1.01 | 2.20 | 3.34 | 4.26 |

| MAT: D_prev \ X ($M) | 263 | 507 | 692 | 993 |
|---|---|---|---|---|
| 0 | 2.76 | 4.82 | 6.13 | 7.88 |
| 600 | 1.67 | 2.93 | 3.72 | 4.78 |
| 1200 | 1.01 | 1.77 | 2.26 | 2.90 |

| GEN: O_prev \ X (weighted s) | 23 | 39 | 46 | 61 |
|---|---|---|---|---|
| 0 | 3.19 | 5.02 | 5.78 | 7.16 |
| 60 | 1.93 | 3.04 | 3.51 | 4.35 |
| 120 | 1.17 | 1.85 | 2.13 | 2.64 |

**Example.** A direct hit on the port (about 85% PORT and 15% GRID, so X ≈ 870 $M) moves RECON:
- by +13.9 on an undamaged map;
- by +8.5 if 500 $M of damage is already done;
- by +5.1 if 1000 $M is already done.

## 4. Future storms: fair value before the season is over

### 4.1 Laplace factors
m_r = E[exp(−X_r/c)] over training storms in round r (`conditional.py`):

| Symbol | Round 1 | Round 2 | Round 3 |
|---|---|---|---|
| RECON | 0.7630 | 0.8070 | 0.8443 |
| MAT | 0.7296 | 0.7737 | 0.8204 |
| GEN | 0.8235 | 0.8214 | 0.8267 |

- **Why the factors rise for RECON and MAT.** They rise by round because later storms find less left to damage. Storm damages are negatively correlated across rounds (RECON between rounds 1 and 3: −0.23).
- **GEN outage does not get used up.** Cross-round correlations for GEN are +0.03, −0.10 and −0.10.

### 4.2 Two ways to compute fair value before round k
- **Method A:** FV = a + b·(1 − e^(−D/c)·Π_{r≥k} m_r). It ignores the using-up of remaining value, so it is biased low for RECON and MAT (holdout bias: settlement exceeds FV by +0.68 for RECON at the round-2 open).
- **Method B:** resample the training seasons' fresh damage fractions f = pct / (100 − prior) for the remaining rounds, apply them to each company's current remaining value, and average exp(−(D + added)/c). The data is in `fits/rebuild_fresh_fractions.json` and the algorithm is described in the JSON. Use method A for GEN.

### 4.3 Holdout results at round opens (RMSE vs settlement, with bias = settlement − prediction)

| Symbol, round | Method A | Method B | House open | House − B (mean, sd) |
|---|---|---|---|---|
| RECON r2 | 3.763 (+0.68) | **3.633 (+0.37)** | 3.659 (−0.17) | +0.54, 0.42 |
| RECON r3 | 2.661 (+0.26) | **2.592 (+0.09)** | 2.629 (−0.20) | +0.29, 0.32 |
| MAT r2 | 1.624 (+0.27) | 1.525 (+0.16) | 1.521 (−0.06) | +0.22, 0.22 |
| MAT r3 | 1.204 (+0.11) | 1.145 (+0.06) | 1.141 (−0.09) | +0.14, 0.18 |
| GEN r2 | 1.975 (+0.17) | same as A | 1.983 (−0.03) | +0.20, 0.19 |
| GEN r3 | 1.391 (+0.07) | same as A | 1.397 (−0.07) | +0.14, 0.13 |

- **Season-open fair value.** RECON 51.82 (method B = empirical mean of 1-400). The house open averages 52.57, and the mean settlement is 52.00 over all 600 seasons (52.36 on the holdout). MAT: 35.66 fair, 35.95 house, 35.73 settlement. GEN: 39.93 fair, 40.24 house, 40.02 settlement.
- **House bias, all 600 seasons** (house − settlement at the round 1 / 2 / 3 opens, `house_bias.py`):
  - RECON +0.567 / +0.467 / +0.300 (se 0.18 / 0.15 / 0.11);
  - MAT +0.216 / +0.173 / +0.127 (se 0.07 / 0.06 / 0.05);
  - GEN +0.215 / +0.125 / +0.090 (se 0.10 / 0.08 / 0.06).
- **Reading the bias.** The house consistently prices rebuilders slightly too high before storms. This is a small positive-expectation lean short, about 0.5 per share against a settlement sd of about 4 for RECON.
- **No drift by season number** (`trend.py`): RECON slope +0.11 per 100 seasons (t = 1.1), MAT t = 0.6, GEN t = 0.0.

### 4.4 Storm pattern changes the outlook
- E[exp(−X/c)] by the storm's true pattern (train):

| Symbol | direct | eyewall | stall | chain | recurve | erratic (n=20) |
|---|---|---|---|---|---|---|
| RECON | 0.628 | 0.729 | 0.873 | 0.827 | 0.963 | 0.926 |
| MAT | 0.670 | 0.680 | 0.739 | 0.835 | 0.900 | 0.885 |
| GEN | 0.767 | 0.747 | 0.703 | 0.879 | 0.949 | 0.941 |

- Stall storms are the worst for GEN because they sit over substations.
- More by-pattern detail is in the JSON (`future_storms.m_by_pattern`).

## 5. What can and cannot be known in real time

1. **RECON and MAT damage is exact only after each final report** (real 740 s of each round, 15:40). That is 40 s before the close.
   - After the round-3 final report our law is exact (0.003). The house is unbiased but noisy: sd 0.08 RECON, 0.05 MAT, 0.06 GEN.
   - The house's errors at 750 s and 778 s are only weakly correlated (0.2), so they are mostly random quote noise.
2. **The preliminary report (real 570 s) is noisy.**
   - Per storm, final minus preliminary in driver dollars has mean −12.7 and sd 93 $M for RECON, and mean −17.1 and sd 98 $M for MAT (about 20% relative). The calibration is final ≈ 26.5 + 0.85·prelim.
   - On the round-3 holdout, plugging in the preliminary report gives RMSE 0.70 (RECON) and 0.35 (MAT). The house between 600 and 738 s is 0.42-0.44 and 0.20. The best blend puts weight 0.94 on the house and gains nothing.
   - **So do not trade RECON or MAT on the preliminary report alone.** You need a track-based damage nowcast from the island-damage model, then map it through the laws above.
3. **GEN can be computed live from the storm feed and is final when the storm ends** (age 480, real 540 s). That is 200 s before the final report.
   - After the storm the house is already close (RMSE 0.065 vs our 0.025), so there is little edge left then.
   - **The edge is during the storm.** Our nowcast (`gen_nowcast.py --ext`) predicts E[exp(−O_rest/120)] with an ordinary least-squares fit for each 20-s age bucket. Its inputs are:
     - dead-reckoning projected closure;
     - closure along the official forecast cone;
     - the official probabilities of hurricane conditions by island;
     - the outlook dummies;
     - which substations are closed now.
   - Coefficients for each age bucket are in the JSON. Round-3 holdout RMSE vs settlement:

| Storm age (s) | 0 | 40 | 100 | 160 | 200 | 240 | 300 | 360 | 400 | 440 |
|---|---|---|---|---|---|---|---|---|---|---|
| No forecast | 1.39 | 1.39 | 1.39 | 1.39 | 1.40 | 1.38 | 1.18 | 0.75 | 0.41 | 0.13 |
| Our nowcast | 1.39 | 0.99 | 0.94 | 0.83 | 0.72 | 0.56 | 0.31 | 0.16 | 0.05 | 0.03 |
| House at the same moment | 1.40 | 1.12 | 1.00 | 0.84 | 0.77 | 0.72 | 0.55 | 0.29 | 0.15 | 0.07 |

   - The nowcast matches or beats the house at every age from 40 s on, except 120-140 s, where it is within 0.03.
   - Fair value during a storm:

     FV = 32 + 18·(1 − e^(−(O_prev + O_so_far)/120) · Ŷ · Π_{future rounds} m_r)

     where Ŷ is the nowcast's predicted E[exp(−O_rest/120)], clipped to [0, 1].
   - Without the nowcast, the unconditional E[e^(−O_rest/120) | age] is 0.824 up to age 120, then 0.830 at 180, 0.842 at 240, 0.874 at 300, 0.934 at 360, 0.986 at 420 and 1 at 480.

## 6. Caveats
- **Announcements in the live season may change a, b, c or which damage counts** (for example, more crews means a larger c). The research data has none. Keep the parameters configurable and re-derive them from the announcement text.
- **GEN depends on rebuilding the storm path.** After a feed gap only the latest storm/track message arrives. Fill positions from the track points (every 4 s, but they carry no intensity) and interpolate intensity. Expect the GEN error to grow above 0.025.
- **Method B is an approximation.** It uses damage fractions at the company level. For multi-site companies (ISLE, AQUA, GRID, FERRY) the fraction still falls slightly by round because individual sites get used up, which only a site-level model would capture. That is a small effect on RECON and MAT expectations.
- **Weakly supported numbers.** The erratic pattern has only 20 training storms. The house-bias estimates have t-statistics of only about 1.5-3.2.
- **The fitted constants look like round design values** (40/24/1000, 28/14/1200, 32/18/120). Fitted and rounded versions give the same holdout error. Insured values are $M per damage percentage point.

## 7. Files
- Scripts are in `SP/work/se/rebuild/`. Run order: `extract.py` → `features.py 0.35` → `gen_final.py` → `expectation.py` → `conditional.py` → `realtime.py` → `gen_nowcast.py` and `gen_nowcast.py --ext` → `alt_measures.py` → `marginal.py` → `make_params.py <fits/rebuild.json>` → `export_fresh.py <fits dir>`. `export_fresh.py` must come after `make_params.py`.
- Caches in `rebuild/`: `cache.json`, `features_0.350.json`, `gen_outage.json`, `gen_curves.json`, `gen_nowcast_rows.json`, and the `*_results.json` files.
