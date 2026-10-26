# engine:build

# Fair-value engine for Systematic Equities (Hurricane Season)

## 0. Bottom line

- **What was built.** `fairvalue.py` is a stdlib-only engine. It returns `{symbol: (mean, sd)}` for the 10 stocks and both ETFs at any point in the season.
  - It replays the raw feed (deduplicated by seq, snapshots included) into a `SeasonState`.
  - It values the season by Monte Carlo over the current storm's remaining path and the remaining storms.
  - All constants come from the verified fits.
- **Holdout replay result (seasons 401-600, every raw `feed.jsonl` message, library and analog pool from seasons 1-400 only).**
  - On RMSE vs settlement the engine matches or beats the house price at every checkpoint of every round.
  - **The edge is largest mid-storm.** Round-3 PORT RMSE is 4.47 vs 5.68 for the house at real 240 s, and 2.82 vs 4.32 at 360 s. HAVEN is 4.60 vs 6.23 and 2.68 vs 4.56.
  - **It is exact after the final report.** RMSE is 0.01 or less, against house staleness of 2.2 at real 740 s.
  - **It equals the house** just before storm 3 forms (real 0 s of round 3) and once the house has absorbed the preliminary report (600 s).
  - **The sd is calibrated:** z-score sd is 0.93-0.98 and coverage of the 90% interval is 92-95%.
- **I replaced the predict-area model with my own current-storm predictor.** The verifier found the fitter's version badly misspecified (`major_problems`).
  - Mine is the physics hazard computed along analog tracks, with analogs weighted by the sources pattern posterior.
  - It beats both the fitter's model and the verifier's corrected one at every age (§5.2).
- **Speed.** The median call takes 32 ms (p95 127 ms, max 194 ms). The fast parameter file takes 11 ms median with the same accuracy. Processing one message takes about 2 µs.

## 1. API

All files are in `/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/engine/`.

```python
import sys; sys.path.insert(0, ".../work/se/engine")
from fairvalue import SeasonState, fair_values
st = SeasonState()            # engine_params.json; live library = seasons 1-600 (validation passes library_seasons=(1,400))
#   optional: case=<dict from GET /cases/hurricane.json>, persist_path="state.jsonl", adjust_file="adjust.json"
st.on_message(msg)            # raw news message or {"type":"news","message":{...}}; dedup by seq; snapshot ingested
fv = fair_values(st)          # {"PORT": (mean, sd), ..., "ARCH": (..), "REBLD": (..)}; cached until the next new message
st.set_adjustment("PORT", mult=1.0, add=-2.0)       # announcement hook: per-scenario v -> mult*v + add; ETFs rebuilt
st.override("settle.island.PORT.K", 0.03)           # live parameter patch (layer bounds, V0/a/K, k, thresholds, ...)
# adjust_file JSON, re-read whenever it changes: {"GRID": {"add": 1.5}, "_overrides": {"settle.insurance.HAVEN.top": 1400}}
st.cumulative_damage(); st.season_loss()            # completed rounds (final reports)
st.incurred(); st.closure_so_far()                  # current storm: locked-in E[damage], closure seconds per site and
                                                    #   weighted per company, sites closed now
st.pattern_posterior()                              # sources model (patched, with the 20 s recent-turn floor)
st.diag["current_storm"]                            # E[total damage], incurred damage, E[remaining] damage, closure so far
st.diag                                             # E[final cumulative damage], E[closure], E[season loss], P(L>400),
                                                    #   P(L>1300), phase, analog count, compute time
st.to_state() / SeasonState.from_state(d) / SeasonState.restore("state.jsonl")   # restart-safe
```

- **Phases handled per round:** unformed (treated as a library storm), storm in progress, storm over (before the preliminary report), after the preliminary report, after the final report.
- **Late start without saved state.** If a completed round has a final report but no track, the engine imputes its closure and site split from the 15 library storms with the nearest damage vectors.

## 2. How each fit is used, and what I decided where fitter and verifier disagreed

| Area | Used as | Decision |
|---|---|---|
| island | Settlement V0 − a·D − K·Σ w·T, constants exactly as fitted (fitter and verifier agree). Closure seconds use the verifier's **tickprob_impact** estimator, ported into `fvcore.StormRecord` with incremental per-interval caching and track points filling gaps. | Verifier adopted. My port reproduces it: with exact inputs, holdout RMSE is PORT 0.0118, GRID 0.0122, FERRY 0.0149. I did **not** use the preliminary-report "shrink" factor, because the physics posterior given the preliminary report is far better (§4.4). |
| physics | Hazard H, the f = 1 − exp(−k·m·H) damage on *remaining* value, the lognormal multiplier m, and the k and σ values from the train fit. The preliminary-report noise sd is the train-only 0.3003. Per-site remaining value is solved exactly from each final report. | Verifier corrections adopted (wording only, plus prelim sd 0.3003). |
| rebuild | RECON 40/24/1000, MAT 28/14/1200, GEN 32/18/120, where GEN's driver O = GRID's weighted closure seconds. | The verifier recommends continuous-time integration (GEN RMSE 0.0220). tickprob_impact does better: 0.0196 (`chk_settle.py`), so I use it. House-bias claims are not used in the engine. |
| insurance | HAVEN = 75 − 0.06·clip(L−400, 0, 900); REIN = 90 − 0.035·max(L−1300, 0) with no top. L for completed rounds is the sum of the final `industry_loss_musd` values from the feed. | Both depletion models (B and G) are **replaced by site-level physics depletion**: each library storm's per-site destroyed fractions are applied to the scenario's per-site remaining value. Holdout settlement RMSE at round opens (§4): round-3 open HAVEN **10.95** (B 11.04, G 10.97, house 11.05), REIN **2.00** (B 2.02, G 1.99, house 2.05). Round-2 open HAVEN **14.29** (B 14.46, G 14.40). |
| sources | `pattern.PatchedPosterior`: the sources `posterior.py` (vendored unchanged) plus the verifier's track-interpolation fix and 20 s recent-turn floor, with `sources.verified.json`. | Verifier adopted. My replay reproduces holdout log-loss 0.1489 and accuracy 0.9475. |
| predict | Fitter's model rejected (verifier: `major_problems`). Its role, the remaining-damage distribution of the live storm, is filled by my analog-track predictor (`analog.py`). | Mine is better than the verifier's RV3+hc at every age (§5.2) and gives a joint scenario distribution, which convex payoffs need. |
| house | Not used in the fair value; it is the benchmark. | — |

## 3. Engine internals

- **Exact prefix.** Rounds with a final report contribute:
  - D, the closure seconds T (tickprob_impact), and L (sum of the reported final losses);
  - per-site remaining value, from solving m exactly so the model reproduces each company's report.
- **Current storm in progress, at age a.**
  - The locked-in hazard and closure so far come from the feed.
  - The rest of the path comes from **analogs**:
    - For every steering class with posterior probability above 0.002, take the K = 30 library storms whose *true* class at age a matches.
    - Pick the nearest by standardized position, 30 s mean velocity and 0.5 × radius.
    - Translate each analog's future path onto the live storm. Radius is scaled by r_now / r_analog(a) and intensity by I_now / I_analog(a).
    - Each analog gives future site hazards and closure seconds (linear crossing on the 2 s grid).
    - Class weight is the posterior probability split equally over the class's analogs.
    - Storms that turn erratic later are included at their historical rate.
  - Company multipliers m are drawn by Latin hypercube from N(0, σ_c).
  - Tuned on seasons 1-400 only (`tune_at.py`). These variants all did **not** help:
    - a decaying translation offset;
    - re-weighting by the cone;
    - re-weighting by the `hurricane_conditions` probabilities;
    - changing the velocity window or adding intensity to the distance;
    - K = 10 to 40 changes less than 1%.
- **After the storm, or after the preliminary report.**
  - The hazard is complete.
  - m comes from the prior, or from the posterior grid given the preliminary report. That posterior uses the interval likelihood for a report rounded to 0.1, with lognormal sd 0.30.
- **Unformed and future rounds.**
  - Each is one of the library storms (1200 in validation, 1800 live).
  - Each library storm carries its realized per-site destroyed fractions and closure seconds. They are assigned to scenarios by stratified permutation.
- **Expectation.**
  - 2000 scenarios while a storm is in progress, 12000 otherwise.
  - The random design is fixed, so values move smoothly from call to call.
  - Each scenario is run through the full settlement map, and mean and sd are taken across scenarios.
  - Convex payoffs (HAVEN and REIN layers, the concave rebuilders) are therefore valued by expectation, not by plugging in a point estimate.
  - ETF = basket of each scenario's values.
  - The sd includes a settlement-formula floor: PORT 0.012, GRID 0.012, FERRY 0.015, GEN 0.02, other stocks 0.003 or less.

## 4. Holdout validation (`replay_validate.py`, 200 seasons × 3 rounds × 9 checkpoints)

At checkpoint T, every message with real time ≤ T has been processed. So T = 570 includes the preliminary report and T = 740 includes the final report. "House" is the `market.csv` row T/2. Each cell is engine / house RMSE vs settlement.

### 4.1 Round 3

| T | PORT | ISLE | AQUA | GRID | FERRY | RECON | MAT | GEN | HAVEN | REIN | ARCH | REBLD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 11.05/11.15 | 3.66/3.67 | 2.37/2.38 | 2.50/2.52 | 1.91/1.93 | 2.58/2.63 | 1.13/1.14 | 1.39/1.40 | 10.95/11.05 | 2.00/2.05 | 24.97/25.21 | 8.29/8.43 |
| 120 | **6.91**/8.03 | 2.17/2.32 | 1.46/1.58 | 1.62/1.96 | 1.25/1.40 | 1.55/1.91 | 0.68/0.86 | 0.89/1.07 | **7.09**/8.54 | 1.33/1.48 | 15.94/19.38 | 4.99/6.24 |
| 240 | **4.47**/5.68 | 1.18/1.39 | 0.73/0.91 | 1.05/1.40 | 0.57/0.76 | 0.93/1.35 | 0.44/0.66 | 0.59/0.81 | **4.60**/6.23 | 1.14/1.12 | 10.23/14.36 | 3.09/4.66 |
| 360 | **2.82**/4.32 | 0.74/0.88 | 0.37/0.42 | 0.66/0.95 | 0.25/0.42 | 0.55/0.96 | 0.26/0.46 | 0.28/0.55 | **2.68**/4.56 | 0.92/0.95 | 5.77/10.07 | 1.71/3.32 |
| 480 | 2.46/2.63 | 0.71/0.74 | 0.34/0.34 | 0.52/0.54 | 0.13/0.15 | 0.46/0.51 | 0.22/0.24 | 0.03/0.11 | 2.20/2.38 | 0.89/0.95 | 4.78/5.28 | 1.34/1.51 |
| 570 | 2.18/2.47 | 0.58/0.72 | 0.34/0.34 | 0.51/0.52 | 0.11/0.13 | 0.42/0.47 | 0.20/0.22 | 0.020/0.061 | 1.99/2.22 | 0.77/0.87 | 4.33/4.84 | 1.22/1.38 |
| 600 | 2.18/2.18 | 0.58/0.57 | 0.34/0.34 | 0.51/0.51 | 0.11/0.11 | 0.42/0.44 | 0.20/0.20 | 0.020/0.056 | 1.99/1.98 | 0.77/0.80 | 4.33/4.34 | 1.22/1.25 |
| 740 | **0.01**/2.21 | 0.00/0.58 | 0.00/0.34 | 0.01/0.51 | 0.01/0.11 | 0.00/0.43 | 0.00/0.20 | 0.020/0.065 | **0.00**/1.97 | 0.00/0.78 | 0.03/4.32 | 0.02/1.25 |
| 770 | 0.01/0.11 | 0.003/0.075 | 0.003/0.042 | 0.012/0.057 | 0.015/0.027 | 0.003/0.082 | 0.003/0.049 | 0.020/0.059 | 0.004/0.066 | 0.00/0.13 | 0.03/0.43 | 0.02/0.34 |

**Directional test, round 3.** Skill = 1 − MSE_engine / MSE_house. The second number is mean $/share of sign(engine − house) × (settle − house), with its t-stat in brackets.

| T | PORT | HAVEN | GRID | GEN | REIN | ARCH |
|---|---|---|---|---|---|---|
| 0 | +0.02, +0.90 [1.1] | +0.02, +1.23 [1.6] | +0.02, +0.37 [2.1] | +0.01, +0.03 [0.4] | +0.05, +0.33 [2.3] | +0.02, +2.76 [1.6] |
| 120 | +0.26, +2.54 [4.7] | +0.31, +3.06 [5.4] | +0.32, +0.79 [6.2] | +0.32, +0.45 [6.5] | +0.19, +0.34 [3.3] | +0.32, +7.87 [6.3] |
| 240 | +0.38, +2.08 [5.5] | +0.45, +3.16 [8.3] | +0.44, +0.85 [10.8] | +0.46, +0.51 [11.5] | −0.03, +0.17 [2.2] | +0.49, +8.22 [9.8] |
| 360 | +0.57, +1.89 [6.9] | +0.65, +2.27 [8.1] | +0.52, +0.56 [10.1] | +0.73, +0.37 [13.2] | +0.07, +0.27 [4.1] | +0.67, +5.68 [9.6] |
| 570 | +0.22, +0.59 [3.5] | +0.20, +0.61 [4.1] | +0.01, +0.20 [5.9] | +0.90, +0.05 [17.0] | +0.22, +0.20 [3.4] | +0.20, +1.39 [4.2] |
| 600 | −0.00, −0.06 [−0.4] | −0.01, −0.09 [−0.6] | −0.01, −0.00 [−0.1] | +0.88, +0.04 [15.8] | +0.06, +0.06 [1.1] | +0.01, −0.22 [−0.7] |
| 740 | +1.00, +1.06 [7.7] | +1.00, +1.08 [9.3] | +1.00, +0.32 [11.4] | +0.91, +0.05 [17.2] | +1.00, +0.33 [6.6] | +1.00, +2.62 [10.8] |

Rounds 1-2 show the same pattern. At T = 120-360 the directional test gives PORT +2.7 to +5.1 $/share (t 2.9-4.9) and HAVEN +2.7 to +4.8 (t 2.6-4.7). At the round-1 and round-2 opens it gives REIN +0.54 (t 2.6) and +0.53 (t 2.8). These are gross figures at the 2 s consensus price, before spread.

### 4.2 Rounds 2 and 1 (engine / house RMSE vs settlement)

| T | PORT | ISLE | AQUA | GRID | FERRY | RECON | MAT | GEN | HAVEN | REIN | ARCH | REBLD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R2 0 | 15.62/15.71 | 5.03/5.05 | 3.24/3.24 | 3.46/3.49 | 2.65/2.67 | 3.61/3.66 | 1.50/1.52 | 1.97/1.98 | 14.29/14.42 | 2.66/2.73 | 32.84/33.11 | 11.33/11.42 |
| R2 120 | 13.53/13.90 | 4.02/4.17 | 2.69/2.75 | 2.92/3.10 | 2.13/2.21 | 3.17/3.25 | 1.36/1.39 | 1.59/1.70 | 12.80/13.18 | 2.31/2.50 | 29.16/30.37 | 9.98/10.36 |
| R2 240 | 12.18/13.01 | 3.82/3.89 | 2.46/2.52 | 2.75/2.99 | 1.95/2.04 | 2.80/3.03 | 1.22/1.32 | 1.56/1.66 | 11.70/12.54 | 2.24/2.42 | 26.73/28.70 | 8.95/9.66 |
| R2 360 | 11.67/12.27 | 3.70/3.73 | 2.43/2.42 | 2.63/2.81 | 1.92/1.98 | 2.71/2.86 | 1.18/1.23 | 1.44/1.53 | 11.54/11.95 | 1.99/2.19 | 25.99/27.06 | 8.64/9.11 |
| R2 480 | 11.29/11.49 | 3.71/3.75 | 2.43/2.44 | 2.58/2.63 | 1.92/1.98 | 2.64/2.71 | 1.15/1.17 | 1.39/1.41 | 11.13/11.27 | 2.04/2.14 | 25.21/25.50 | 8.38/8.57 |
| R2 600 | 11.22/11.31 | 3.67/3.70 | 2.38/2.39 | 2.55/2.57 | 1.91/1.93 | 2.61/2.67 | 1.14/1.15 | 1.39/1.40 | 11.09/11.16 | 2.00/2.04 | 25.17/25.29 | 8.34/8.49 |
| R2 770 | 11.05/11.14 | 3.66/3.68 | 2.37/2.38 | 2.50/2.53 | 1.91/1.93 | 2.58/2.62 | 1.13/1.14 | 1.39/1.39 | 10.95/11.06 | 2.00/2.05 | 24.97/25.20 | 8.29/8.43 |
| R1 0 | 18.91/18.95 | 6.01/6.08 | 3.84/3.87 | 4.17/4.19 | 3.02/3.03 | 4.33/4.34 | 1.74/1.75 | 2.37/2.38 | 16.60/16.69 | 2.93/2.99 | 38.18/38.50 | 13.59/13.66 |
| R1 120 | 17.23/17.59 | 5.47/5.42 | 3.51/3.51 | 3.77/3.94 | 2.68/2.71 | 3.93/4.02 | 1.62/1.66 | 2.15/2.20 | 15.45/15.74 | 2.87/3.00 | 35.70/36.81 | 12.36/12.71 |
| R1 240 | 15.96/16.84 | 5.24/5.28 | 3.27/3.33 | 3.51/3.74 | 2.67/2.72 | 3.66/3.88 | 1.51/1.59 | 2.03/2.09 | 14.36/15.12 | 2.72/2.85 | 33.12/34.78 | 11.46/12.12 |
| R1 360 | 15.76/16.67 | 5.16/5.15 | 3.26/3.25 | 3.48/3.65 | 2.65/2.68 | 3.62/3.84 | 1.50/1.57 | 1.95/2.02 | 14.29/14.89 | 2.68/2.82 | 32.78/34.05 | 11.32/11.95 |
| R1 480 | 15.73/16.24 | 5.16/5.17 | 3.26/3.26 | 3.53/3.59 | 2.64/2.70 | 3.63/3.76 | 1.52/1.54 | 1.98/1.98 | 14.38/14.74 | 2.73/2.83 | 33.20/33.89 | 11.38/11.78 |
| R1 600 | 15.67/15.76 | 5.11/5.13 | 3.25/3.25 | 3.54/3.56 | 2.65/2.67 | 3.62/3.67 | 1.51/1.53 | 1.97/1.98 | 14.40/14.52 | 2.73/2.79 | 33.25/33.55 | 11.38/11.50 |
| R1 770 | 15.62/15.71 | 5.03/5.05 | 3.24/3.23 | 3.46/3.49 | 2.65/2.67 | 3.61/3.66 | 1.50/1.52 | 1.97/1.98 | 14.29/14.42 | 2.66/2.74 | 32.84/33.13 | 11.33/11.43 |

In rounds 1-2 the remaining storms dominate the error, so engine and house are close. The engine still wins mid-storm.

**Mean bias (settle − value).**
- **Round-1 open:** engine PORT −1.67 and HAVEN −1.16. The house is −0.82 and +0.54.
- **Why the engine looks biased:** the holdout seasons were heavier than train. Mean season loss is 1035 in holdout vs 996 in train, and the engine's round-1-open E[L] is 1013.
- **Round 3:** every engine bias is within ±0.35.

### 4.3 Current-storm damage prediction (round 3, RMSE of E[cumulative final damage], pct points)

| T | PORT | ISLE | AQUA | GRID | FERRY | E[L] ($M) |
|---|---|---|---|---|---|---|
| 0 | 18.66 | 9.63 | 11.86 | 7.68 | 8.59 | 206.1 |
| 120 | 11.61 | 5.70 | 7.29 | 5.02 | 5.71 | 129.5 |
| 240 | 7.52 | 3.10 | 3.65 | 3.24 | 3.49 | 85.9 |
| 360 | 4.93 | 1.95 | 1.85 | 2.41 | 2.62 | 53.5 |
| 480 | 4.47 | 1.88 | 1.68 | 2.15 | 2.52 | 46.9 |
| 570 | 3.97 | 1.53 | 1.69 | 2.14 | 2.13 | 42.8 |

### 4.4 Calibration and robustness

- **Calibration** (all rows before the round-3 final report):
  - z-score sd: PORT 0.98, ISLE 0.95, AQUA 0.93, GRID 0.95, FERRY 0.98, RECON 0.98, MAT 0.97, GEN 0.98, HAVEN 0.98, ARCH 0.97, REBLD 0.98.
  - REIN's z-score sd is 0.80, so its sd is conservative.
  - Coverage of the 90% interval is 0.917-0.949.
- **Monte Carlo noise** (two random designs, `mc_noise.py`). The standard error of one evaluation is at most 0.11 (HAVEN, round 1 mid-storm), 0.06 or less for the other stocks, and 0.02 or less in round 3.
- **Round-1-open values** (12000 scenarios vs 60000): PORT 72.43, HAVEN 39.87, REIN 88.83. The train-season mean settlements are 72.99, 40.56 ± 0.84 and 89.06 ± 0.13.
- **Feed outages** (`--stress`). I simulated 6 bursts of 5-40 observations per round in which only the last storm and track message survive, plus 2% duplicate messages.
  - Round-3 RMSE vs settlement, outage / clean: PORT 7.25/6.91 at T = 120, 5.04/4.47 at 240, 3.01/2.83 at 360. That is still better than the house at those times.
  - After the storm, unobserved intensity inside gaps costs about 0.10 on PORT, GRID and FERRY and 0.17 on GEN.
- **Restart and late start.**
  - Restarting from `to_state` gives values identical to the clean run.
  - A synthetic late join from a snapshot at round-3 age 200, with no saved state: PORT 56.20 vs 56.78 on the full replay, and FERRY 20.16 vs 20.72 (closure for rounds 1-2 is imputed).
  - `selftest.py` passes all checks.
- **Speed.** Standard parameters: median 32 ms, p95 127 ms, max 194 ms per call. `engine_params_fast.json` (K = 12, 600/4000 scenarios): median 11 ms, p95 46 ms, max 86 ms, with the same round-3 RMSE to within ±0.1. Both exceed the bot skeleton's 20 ms budget at p95, so call the engine once per storm observation, off the trading thread.

## 5. Component checks

### 5.1 Settlement map with exact inputs (holdout, `chk_settle.py`)

| Symbol | RMSE | max |
|---|---|---|
| PORT | 0.0118 | 0.059 |
| ISLE | 0.0034 | |
| AQUA | 0.0031 | |
| GRID | 0.0122 | 0.052 |
| FERRY | 0.0149 | 0.077 |
| RECON | 0.0029 | |
| MAT | 0.0028 | |
| GEN | 0.0196 | 0.099 |
| HAVEN | 0.0036 | |
| REIN | 0.0016 | |
| ARCH | 0.033 | |
| REBLD | 0.021 | |

### 5.2 Live-storm predictor: holdout RMSE of E[final damage], all rounds

Cells are mine (analog track + physics) / the verifier's RV3+hc / the fitter's blendB.

| age | PORT | ISLE | AQUA | GRID | FERRY | LOSS |
|---|---|---|---|---|---|---|
| 30 | **16.62**/16.90/18.55 | **6.53**/6.92/7.90 | **8.58**/9.03/11.55 | **6.49**/6.63/7.91 | **6.57**/6.83/8.15 | **180**/185/216 |
| 120 | **12.14**/13.70/16.02 | **4.17**/4.55/5.90 | **5.72**/6.13/9.63 | **4.94**/5.70/7.48 | **5.13**/5.64/7.09 | **135**/156/200 |
| 240 | **9.34**/9.71/13.19 | **2.81**/3.00/5.06 | **3.19**/3.56/8.54 | **3.96**/4.39/6.57 | **3.47**/3.71/5.87 | **100**/109/167 |
| 360 | **5.68**/5.83/10.88 | **2.49**/2.58/4.83 | **2.73**/2.75/8.34 | **3.26**/3.30/6.10 | **3.21**/3.31/5.58 | **60**/62/145 |

- By age 420 the error reaches the physics noise floor (PORT 5.34 vs a floor of 5.33).
- Weighted closure-second RMSE at age 120: PORT 27.5, GRID 8.9, FERRY 11.0.

### 5.3 Site-level depletion

- I sampled random triples of train-library storms and compared them with the actual train seasons:
  - per-round mean loss: model 407.8 / 332.4 / 272.5 vs actual 407.6 / 330.7 / 257.9;
  - season mean loss: 1013 vs 996;
  - P(L > 1300): 0.213 vs 0.185.
- "Fresh-equivalent" storm losses (the same storm applied to an undamaged map) are flat across rounds (407.6 / 414.8 / 403.9), so pooling the library across rounds is justified.

## 6. What can and cannot be known in real time

- **Known exactly:**
  - final damage and loss of completed rounds, and per-site remaining value (solved from the final reports);
  - closure seconds so far, to about 1-1.5 cents (GEN about 2 cents);
  - settlement from the round-3 final report onward (real 740 s), 40 s before the close, while the house is still stale.
- **Known with model noise:**
  - **Locked-in damage:** the per-company multiplier m adds about 35% log noise until the preliminary report (real 570 s). After it, round-3 PORT error is about 4 pct points; the final report makes it exact.
  - **The remaining path:** it dominates until about age 300 (real 360 s); damage locks in late, especially for PORT.
  - **Future storms:** irreducible in rounds 1-2.
- **Not knowable:**
  - a turn to erratic before about 20-40 s after it happens;
  - chain vs recurve before about age 110-150;
  - any announcement's effect, which goes through the manual hook.

## 7. Known weaknesses and remaining model gaps

1. **Round-3 loss slightly high.** The library slightly over-predicts round-3 storm loss after two random storms (272.5 vs 257.9 in train, holdout also below). That is about 1.5-2 standard errors combined: possibly noise, possibly a weak within-season dependence the site model misses. The resulting round-1/2 bias in HAVEN and REIN is about 0.2-0.5.
2. **Few erratic analogs.** There are only 20 train storms that were erratic from formation, and the holdout erratic storms were much milder (insurance verifier). Analogs for this class are thin; keep `p_min` and the posterior floor.
3. **No edge in the window between the preliminary and final reports.** After the house absorbs the preliminary report (real 600 s), the engine equals the house for PORT, ISLE, AQUA, GRID and HAVEN. The only gain there is the house's 4 s reaction lag (T = 570).
4. **Feed gaps.** Intensity is never published inside a gap, so closure and hazard errors grow (§4.4). Long outages mid-storm also make the analog state stale.
5. **Late start without persistence.** If the bot joins mid-storm without a saved log, the early part of the current track (before the snapshot's last ~160 s) is missing and closure for earlier rounds is imputed. Always run with `persist_path`.
6. **Parameters fitted on research data.** k, σ and the classifier are train fits, and the live library uses all 600 seasons, which was not separately validated. The REIN layer top is unidentified (≥ 1766) and assumed absent. Announcements are handled only by `set_adjustment`, `override` or `adjust_file`.
7. **REIN sd is wide** (z-score sd 0.80, coverage 95%), because REIN sits at 90 in about 80% of outcomes.
8. **All edges are gross.** They are measured against the 2 s consensus price, with no spreads, inventory skew or other teams. House-behaviour trades (formation jump, drift) belong in the trading layer.

## 8. Files (in `.../scratchpad/work/se/engine/`)

- **Engine:**
  - `fairvalue.py`: the API
  - `fvcore.py`: geometry, closure, hazard, multiplier solve, preliminary-report posterior, settlement map
  - `analog.py`: analog-track predictor
  - `pattern.py`: patched classifier wrapper
  - `posterior.py`: sources classifier, vendored unchanged
- **Parameters and data:**
  - `engine_params.json`, `engine_params_fast.json`
  - `library_all.json`: 1800 storms, built by `build_library.py` from `cache.json`, which `extract.py` builds from the read-only research data
- **Validation:**
  - `replay_validate.py`; output in `validate_final.out`, `validate_stress.out`, `validate_fast.out`, rows in `validate_rows.json`
  - `selftest.py`, `chk_settle.py`, `chk_depletion.py`, `mc_noise.py`
- **Tuning:** `tune_at.py` with `post_rows.py`; results in `tune_hold.out` and `tune2.out`-`tune4.out`.
- **To rerun the validation:** `python3 -I replay_validate.py <history_dir> 401 600 14 validate_rows.json [--stress]` (about 20 s on 14 cores).
