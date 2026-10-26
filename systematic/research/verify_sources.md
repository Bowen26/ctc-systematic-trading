# verify:sources

## verdict

confirmed_with_corrections

## issues

## Verification of the 'sources' fit (forecast-source accuracy and pattern classifier)

### What I did (Python stdlib only, run with python3 -I; scripts in work/se/verify-sources/)
- **Own extractor, `vx_extract.py`.** It builds its own cache straight from the raw `feed.jsonl` files and the manifest. It does not use the fitter's `storms.json`. It also runs data checks:
  - seq increases throughout and there are no duplicate observations or impacts;
  - all 241 storm observations are present for every storm;
  - every message's `time` equals open + 30 x (60 + age) s, with 0 mismatches.
- **Own library, `vlib.py` and `vmod.py`.** These re-implement the 12 track features, the cone features, the 11-state filter, the Student-t density and the scoring from the report's description.
- **Re-fits.** I re-fitted the source model, the hazard and the full classifier on seasons 1-400. Alternatives were tuned only by 2-fold cross-fitting inside train. Every model was scored on seasons 401-600.
- **Live engine.** I replayed `posterior.py` on the raw holdout feeds, including feed-gap and late-start scenarios that the fitter did not test.

### Confirmed (my numbers match the report)
- **Official labels.**
  - Accuracy is 0.69906 in train (n = 9,600) and 0.69104 in holdout (n = 4,800).
  - The wrong cells are uniform (χ² = 32.8 with 24 df). LLR = 2.4525 nats.
  - P(correct | previous correct) = 0.692 and P(correct | previous wrong) = 0.703 in train. The label changes on 49.7% / 50.1% of consecutive advisories.
- **Wire.**
  - Accuracy is 0.39566 in train (n = 12,200) and 0.39846 in holdout (n = 6,116). The wrong cells are uniform (χ² = 21.1 with 24 df).
  - The wire repeats a previous official label that was wrong 12.2% of the time, against 12.1% by chance, so it does not copy the forecasters.
- **Stability.** Official and wire accuracy are stable over 100-season blocks: official 0.681-0.714, wire 0.390-0.407.
- **Hazard.**
  - Train: λ = 74 turns over the exposure time = 1.3494e-4 per second. Holdout: 1.469e-4 (40 turns).
  - Erratic from formation: 20 of 1,200 storms in train and 15 of 600 in holdout.
- **`hurricane_conditions`.**
  - Holdout Brier is 0.0296 (climatology 0.1631) and AUC is 0.9898. The probabilities are calibrated whether the label is right or wrong: mean p 0.204 against observed 0.201 when wrong.
- **Cone.**
  - Holdout median error is 11.8, 20.7, 32.6 and 52.1 at leads of 30, 60, 120 and 240 s. For storms that are erratic at the advisory it is 30.3, 76.2, 202.9 and 499.5.
  - RMS of error/spread in train is 1.036, 1.024, 0.999 and 0.962.
- **Classifier, from my own features and code using the params file.**
  - Holdout mean over 48 checkpoints: log-loss **0.1501**, accuracy **0.9475**, Brier 0.0730. In-sample on seasons 1-400: 0.1472 / 0.9487.
  - Replaying the raw feeds through `posterior.py` gives 0.1501 / 0.9475 as well.
  - My independent re-fit of the fitter's form produced **identical** per-age weights and identical holdout numbers.
- **Timing and detection.**
  - P(true) ≥ 0.99 at a median age of 90 s (p25 90, p75 150). MAP lock-in: median 30 s, p90 90 s, 99.8% of storms lock in.
  - Turn detection: 37 of 40 turns are detected within the storm, median delay 33 s, p75 50 s. False alarms: 35 of 27,203 non-erratic checkpoints = 0.13%.
  - With 30% of storm messages dropped at random: 0.1660.

### Issues found (most severe first)
1. **MAJOR (real-time robustness bug in `posterior.py`).**
   - **Cause.** `_pt(a)` falls back only to storm observations within ±8 s or to an exact track point. Track points are every 4 s, so an age that is 2 mod 4 cannot be recovered from the track. That includes **age 90**, which the early-displacement feature needs at every checkpoint ≥ 90.
   - **Effect.** After a feed gap covering about ages 82-98, the track score is silently switched off for the rest of the storm. The packet says that after a gap only the latest storm/track message of the group arrives. A bot that (re)starts mid-storm also loses the track score, because the engine ignores snapshots. The cone score of any advisory issued inside the gap is lost the same way.
   - **Evidence (holdout replay).**

     | Scenario | Unpatched | Patched | No-gap reference |
     |---|---|---|---|
     | 20-s gap at ages 80-100, log-loss over all 48 checkpoints | 0.2184 (track score missing on 24,000 of 28,800 checkpoint-rows) | 0.1587 | 0.1501 |
     | Bot started at age 200, log-loss over ages 200-480 | 0.1044 (track missing on 17,400 of 17,400) | 0.0414 | 0.0323 |

     The fitter's random 30% drop test cannot expose this, because it almost never removes every observation within ±8 s of age 90.
   - **Fix.** Interpolate linearly between the two track points either side of the missing age (`verify-sources/posterior_patched.py`). It leaves the no-gap result unchanged at 0.1501.
2. **MODERATE (overconfident at the extremes).** A turn to erratic cannot be seen in the track for about 20-40 s. Meanwhile the ρ = 0.1 mixture gives a recently turned storm only 10% of a base-pattern likelihood.
   - Holdout rows where the top class gets p ≥ 0.999: mean p 0.99965, realised 0.99743, so about 7 times more errors than stated.
   - Rows with p in [0.99, 0.999): stated 0.99683, realised 0.99519.
   - 72 rows had top class p ≥ 0.99 and were wrong; 63 of these fall within 60 s after a turn.
   - The 0-20 s window after a turn is 0.27% of rows but 10% of the total log-loss (log-loss 5.50, accuracy 7.6%).
   - **Fix.** Hold back ε = 1 − exp(−λ·D) for "turned in the last D seconds but not yet visible", with D = 20 s, so ε = 0.0027.
     - D was chosen on train, where 10-20 s was best.
     - Holdout log-loss improves from 0.1501 to **0.1489**, and accuracy is unchanged.
     - The [0.99, 0.999) bucket becomes 0.99611 stated against 0.99690 realised.
     - The remaining ≥ 0.999 rows are erratic-class calls, with 4 wrong out of 1,273.
3. **MINOR (timing).**
   - In all 14,400 cases the official advisory has a lower seq than the storm observation of the same age, so it arrives just before it. Wire messages at even ages come after the observation.
   - If the bot calls `posterior()` right on the advisory message, the cone is measured from the age−2 observation, about 3.6 units off, until the next message. Checkpoint metrics are not affected.
   - There is no other off-by-one. The evaluation includes messages with age ≤ t, which is right, because those are published by real second 60 + t.
4. **MINOR (design choices made with all 600 seasons).**
   - The exploratory scripts looked at all 600 seasons to choose the features and the cone conditioning.
   - The numbers themselves are fitted on train only: my train-only class means reproduce the params to within 5e-8, while all-600 means differ by up to 103.
   - Every alternative design lands within 0.150-0.152 on the holdout, so the effect of those choices is negligible.
5. **MINOR (parameter count).**
   - The model has 27,970 parameters: 24,084 for the track densities, 3,780 for the cone densities and 96 weights.
   - The gap between in-sample and holdout is only 0.003 nats.
   - Replacing the 96 per-age weights with 2 global weights costs only 0.002 nats on the holdout (0.1521).
   - So it is not overfit, but much of the complexity buys little.
6. **MINOR (overstated claims).**
   - "Cone + label identifies the pattern 99.5% from advisory 4" fails at advisory 8, which has only one cone point. On the holdout I get 94.2% at advisory 2, 98.5% at 3, 99.8% at 4-7 and **95.8% at 8**.
   - "The cone follows the truth from advisory 2-3 on": the fitted pull toward the label is β = 0.37 at advisory 1, **0.13 at advisory 2**, 0.03 at 3 and about 0 from 4. So advisory 2 is still pulled 13% toward the label.
7. **INFO.**
   - The holdout has more erratic storms than train (2.5% erratic from formation against 1.67%; λ 1.47e-4 against 1.35e-4). The predicted P(erratic by end) is 0.075-0.086 against 0.092 observed, which is within sampling noise.
   - At the 240-s lead the holdout RMS of error/spread is 0.86 (train 0.96), so the long-lead spread is slightly conservative.
   - The 1e-6 clip in the log-loss is irrelevant: the lowest holdout p_true is 1.6e-4, and unclipped log-loss is identical.

### Alternatives tried (holdout, mean over 48 checkpoints: log-loss / accuracy / Brier)

| Model | Log-loss | Accuracy | Brier |
|---|---|---|---|
| Latest official label | 1.1410 | 0.6731 | 0.5171 |
| Official-only filter | 0.4625 | 0.8399 | |
| Official + wire filter | 0.3911 | 0.8670 | 0.1887 |
| Gaussian product, weights = 1 | 0.2651 | 0.9448 | 0.0845 |
| Student-t(30) product, weights = 1 | 0.2531 | 0.9444 | 0.0839 |
| Gaussian, global weights (track 0.5, cone 0.05) | 0.1542 | 0.9473 | 0.0737 |
| Student-t, global weights (track 0.5, cone 0.1) | 0.1521 | 0.9467 | 0.0746 |
| **Fitter's form (my re-fit)** | **0.1501** | **0.9475** | **0.0730** |
| Cone mean blended toward the label | 0.1497 | 0.9474 | 0.0729 |
| Per-checkpoint multinomial logistic regression (L2 = 0.003, chosen by CV) | 0.1642 | 0.9448 | 0.0795 |
| Fitter's model + 20-s recent-turn floor | 0.1489 | 0.9475 | 0.0730 |

**Label and hazard forms (holdout).**
- Mean log-likelihood per label:
  - official: symmetric −1.11567, full 6×6 −1.11721, per-advisory-number −1.11569;
  - wire: symmetric −1.64054, full −1.64106.
- Hazard log-likelihood: constant hazard −393.17, uniform turn time −393.02 to −392.60.

**Conclusion.** No alternative is clearly better. The fitter's symmetric noise model, constant hazard and tempered generative stacking stand.

### Edge cases (fitter's model, holdout)

| Subset | Log-loss | Accuracy |
|---|---|---|
| Zero-damage storms (only 3, all erratic; 144 rows) | 0.156 | 95.8% |
| Top-5% loss storms (≥ $849M) | 0.160 | 94.1% |
| Erratic from formation (15 storms) | 0.214 | 94.2% |
| Turned storms, before the turn | 0.186 | 93.0% |
| Turned storms, 0-20 s after the turn | 5.50 | 7.6% |
| Turned storms, 20-60 s after the turn | 1.29 | 62.9% |
| Turned storms, ≥ 60 s after the turn | 0.029 | 98.8% |
| Never-erratic chain (weakest base pattern) | 0.147 | 93.2% |
| Never-erratic stall (best base pattern) | 0.089 | 97.0% |

- Ages ≤ 80 s account for 70.6% of the total log-loss.
- The worst miss that is not near a turn is season 414 round 3, a chain storm with two "erratic" labels and an erratic-looking cone.

### What can and cannot be known in real time
- **Can be known:** labels, wire, cone, `hurricane_conditions`, observations and track, all through messages with age ≤ t.
- **Cannot be known:**
  - a turn to erratic before about 20-40 s after it happens;
  - chain vs recurve before their turn at about 110-150 s;
  - whether any single label is right.
- **For a late joiner:** the engine ignores snapshots, so it loses the label/wire history. Feeding the snapshot's latest advisory into the engine as an official message is recommended.

## corrections

The statistical fit is confirmed without change:
- official labels: P(right) = 0.6990625, P(each wrong) = 0.0601875, LLR 2.4525 nats;
- wire: P(right) = 0.395656, P(each wrong) = 0.120869, LLR 1.186 nats;
- erratic hazard: λ = 1.34940e-4 per second, so P(turns later | not yet at age a) = 1 − exp(−λ(480 − a));
- priors as in sources.json, ν = 30, ρ = 0.1, δ = 0, per-age weights as in sources.json.

Corrections:
1. **Live engine bug.** Use `verify-sources/posterior_patched.py`, loaded as `PatchedPosterior = posterior_patched.load(<sources dir>)`, which has the same API as the original. When no observation is within ±8 s, its `_pt(a)` interpolates linearly between the two neighbouring track points. Without this, a feed gap around age 82-98 or a late start silently disables the track score for the rest of the storm.
2. **Recent-turn floor.** Set `classifier.recent_turn_floor_s` = 20, so ε = 1 − exp(−λ·20) = 0.0027.
   - Then p'_erratic = p_erratic + (1 − p_erratic)·ε, and p'_c = p_c·(1 − ε) for every other class.
   - Effectively no non-erratic class can exceed 0.9973.
   - Holdout log-loss improves from 0.1501 to 0.1489, and the overconfidence above 0.99 is fixed.
3. **Statements to correct.**
   - "Cone + label 99.5% from advisory 4" holds for advisories 4-7 (99.8% on the holdout) but not for advisory 8, which has one cone point (95.8%).
   - The cone still leans 13% toward the label at advisory 2 (β = 0.37 at advisory 1, 0.13 at 2, about 0 from 3-4 on).
4. **Optional for the live season, not used in any holdout metric.** Source model refitted on all 600 seasons:
   - prior: direct 0.25, chain 0.2272, recurve 0.2072, stall 0.1511, eyewall 0.145, erratic 0.0194;
   - λ = 1.3891e-4 per second;
   - official P(right) = 0.69639, wire P(right) = 0.39659.

## corrected_params_file

/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/fits/sources.verified.json

## independent_metrics

All numbers are on the holdout (seasons 401-600), computed from my own cache of the raw feed files, unless marked as train.

**Sources:**
- Official labels: accuracy 0.69104 (n = 4,800); train 0.69906.
- Wire: accuracy 0.39846 (n = 6,116); train 0.39566.
- Erratic hazard: λ = 1.469e-4 per second on the holdout (train 1.3494e-4).
- `hurricane_conditions`: Brier 0.0296 (climatology 0.1631), AUC 0.9898.
- Cone: median error 11.8 / 20.7 / 32.6 / 52.1 at leads of 30 / 60 / 120 / 240 s. RMS of error/spread is 1.03 / 1.03 / 0.99 / 0.86.

**Fitter's classifier, my evaluation (log-loss / accuracy):**

| Age (s) | 10 | 30 | 60 | 90 | 120 | 150 | 210 | 300 | 420 |
|---|---|---|---|---|---|---|---|---|---|
| Log-loss | 1.411 | 0.542 | 0.392 | 0.168 | 0.157 | 0.057 | 0.010 | 0.013 | 0.065 |
| Accuracy | 38.0% | 81.7% | 86.2% | 94.2% | 95.3% | 98.0% | 99.7% | 99.8% | 99.2% |

- Mean over 48 checkpoints: log-loss 0.1501, accuracy 0.9475, Brier 0.0730. In-sample (train): 0.1472 / 0.9487.
- My independent re-fit of the same form gives 0.1501 / 0.9475 with identical weights. The raw-feed replay through `posterior.py` gives 0.1501 / 0.9475.

**Pattern identification and turns:**
- P(true) ≥ 0.99 at a median age of 90 s (p75 150). MAP lock-in: median 30 s, p90 90 s.
- Turn detection: median delay 33 s, p75 50 s. False alarms 0.13% of checkpoints.

**Alternatives (mean log-loss / accuracy over 48 checkpoints):**

| Model | Log-loss | Accuracy |
|---|---|---|
| Latest official label | 1.1410 | 0.6731 |
| Official + wire filter | 0.3911 | 0.8670 |
| Gaussian product, weights = 1 | 0.2651 | 0.9448 |
| Student-t product, weights = 1 | 0.2531 | 0.9444 |
| Student-t, 2 global weights | 0.1521 | 0.9467 |
| Cone blended toward the label | 0.1497 | 0.9474 |
| Multinomial logistic regression | 0.1642 | 0.9448 |
| Fitter's model + 20-s floor | 0.1489 | 0.9475 |

**Feed-gap robustness (log-loss):**

| Scenario | Unpatched | Patched |
|---|---|---|
| 20-s gap at ages 80-100 | 0.2184 | 0.1587 |
| Bot starts at age 200 (ages 200-480; no-gap 0.0323) | 0.1044 | 0.0414 |
| 30% of storm messages dropped at random | 0.1660 | 0.1649 |

The patched engine with the 20-s floor gives 0.1489 / 0.9475 on a full raw-feed replay.
