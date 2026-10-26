# fit:sources

## headline

- Official outlook labels are right 69.9% of the time in train (69.1% in holdout). Every wrong outlook is equally likely, about 6.0% each, and labels are independent from one advisory to the next given the truth. Accuracy does not improve with advisory number (68-72% for advisories 1-8), and the first advisory is right 72%. Half of all advisories change the label. A change goes toward the truth 42% of the time and away from it 43%, so it is a coin flip. Trusting the latest advisory scores 67.2% accuracy and log-loss 1.14 on the holdout.
- Wire chatter is right 39.6% in train (39.9% holdout), with each wrong outlook at about 12.1%. It is independent of the official advisory: it neither leads nor lags. Each wire message carries 1.19 nats of evidence for the named outlook, against 2.45 for an official label. An 'erratic' rumour is right only 14-18% of the time, because erratic is rare and the wire's errors are spread evenly.
- The cone and the hurricane_conditions probabilities follow the true pattern even when the label is wrong (from advisory 2-3 on). Cone plus label alone identifies the pattern 92.8% of the time at advisory 2, 96.1% at advisory 3 and 99.5% from advisory 4. The hurricane_conditions probabilities are well calibrated: holdout Brier 0.030 against 0.163 for climatology, AUC 0.990. Use them as published.
- Cone median error vs the realised position is 12, 21, 33 and 52 map units at leads of 30, 60, 120 and 240 s. The published spread is about the RMS radial error (RMS of error/spread is 0.96-1.04). For storms that are erratic it is 30, 76, 203 and 499.
- The combined classifier is a filter over official and wire labels with an erratic-turn hazard, plus a track model and a cone model. On the holdout it is right 81.7% at age 30 s, 86.2% at 60 s, 94.2% at 90 s, 98.0% at 150 s and at least 99.5% from 210 s. Mean log-loss is 0.150 against 1.142 for the latest-advisory rule, and the probabilities are calibrated.
- P(true pattern) reaches 0.99 by a median age of 90 s, against 210 s using labels only. The live engine posterior.py, replaying the raw holdout feeds message by message, reproduces these numbers exactly. With 30% of storm messages dropped, log-loss rises only to 0.166.
- Turning erratic is a constant-rate event at 1.35e-4 per second, uniform over age: 6.3% of storms turn mid-storm, and 1.7% are erratic from formation. P(turns later | not yet) = 1 - exp(-1.35e-4 x (480 - age)), or 5.5% at 60 s, 3.2% at 240 s and 0.8% at 420 s. The model detects a turn a median 33 s after it happens (75th percentile 50 s), with false alarms on 0.13% of checkpoints.

## params_file

/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/fits/sources.json

## scripts

[
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/extract.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/srclib.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/official_wire.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/hc_calib.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/cone.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/hazard.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/track_table.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/classify3.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/posterior.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/timing.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/assemble.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/demo.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/cone_variants.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/cone_blend.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/hc_by_pair.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/headlines.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/inspect_storm.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/explore1.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/explore2.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/explore3.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/explore4.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/explore5.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/explore6.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/classify.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/classify2.py"
]

## report

# Forecast sources and pattern classification: Systematic Equities (Hurricane Season)

**Scope.** This covers how accurate the official advisories (label, `hurricane_conditions`, cone) and the wire chatter are. It also covers the track signature of each pattern, and a classifier that combines all sources into a posterior over steering patterns for the bot.

**Data split.** Parameters are fitted on seasons 1-400 (1,200 storms). All holdout numbers come from seasons 401-600 (600 storms).

**Truth.** Truth at age *a* follows the packet's reference code: it is `erratic` if `turned_erratic_at` is set and *a* is at least `turned_erratic_at`. Otherwise it is the manifest pattern.

**How the numbers were produced.** Every number below comes from the scripts in `work/se/sources/`, run with `python3 -I` using only the standard library. The data folder was only read.

---

## 0. Bottom line for the bot

1. **Do not trust any single label.**
   - The official outlook is a noisy draw: P(label = truth) = 0.699, and each of the 5 wrong outlooks gets 0.060.
   - Labels are independent from one advisory to the next given the truth, and accuracy is the same for advisories 1 to 8.
   - The label changes on 50% of advisories, and a change is a coin flip between the old and the new label.
   - A rule that follows the latest official advisory is right only 67% of the time at any age. If the house prices off the latest label, it will be wrong about a third of the time and will flip-flop.
2. **The cone and `hurricane_conditions` carry the real forecast.**
   - They follow the true pattern even when the label is wrong. The exception is advisory 1, where they partly follow the label.
   - The cone alone, from advisory 4 on, identifies the pattern 99.5% of the time.
   - The `hurricane_conditions` probabilities are calibrated whether or not the label is right.
3. **The storm track separates patterns early.**
   - direct goes north, chain and recurve go north-north-west, eyewall goes north-north-east, and stall goes north-east.
   - Chain and recurve separate only at their turn, around 110-150 s.
   - Eyewall shows a radius jump to about 150-170, stall slows down near Coral Key, and erratic speeds up to about 3 units/s.
4. **The combined posterior is right 82% at 30 s, 94% at 90 s, 98% at 150 s and at least 99.5% after 210 s on the holdout, and it is calibrated.** Use `posterior.py` (class `PatternPosterior`) with `fits/sources.json`.

---

## 1. Data and setup

- **Cache.** `extract.py` turns the 600 `feed.jsonl` files into `sources/storms.json`, with one record per storm. Each record holds the 241 storm observations (ages 0 to 480 every 2 s), 8 official advisories, the wire messages and the first impact age per island.
- **Sanity checks.**
  - Each track message matches the storm observations exactly (maximum difference 0.0).
  - There are no duplicate impacts and no cross-round messages.
  - Official advisories arrive at exactly ages 30, 90, ..., 450.
- **Wire timing.** There are about 10.2 wire messages per storm, at ages 10 to 470. The gap between messages has mean 40 s and median 29 s, and the first wire message arrives at a median age of 40 s.
- **Wire text.** There is one fixed headline template per outlook, so the headline text carries no information.
- **Pattern mix and erratic turns.**
  - Train patterns: direct 23.75%, chain 22.33%, recurve 22.75%, stall 14.92%, eyewall 14.58%, erratic from formation 1.67%.
  - 74 of 1,180 train storms that were not erratic from formation turned erratic mid-storm.

## 2. Official advisories

### 2.1 Accuracy by advisory number (age = 30 + 60·(n-1))

| Advisory | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | All |
|---|---|---|---|---|---|---|---|---|---|
| Train (n = 1,200 each) | 72.3% | 69.2% | 69.8% | 71.1% | 70.7% | 68.3% | 68.2% | 69.8% | **69.91% ± 0.47** |
| Holdout (n = 600 each) | 71.5% | 70.7% | 66.8% | 66.7% | 71.5% | 68.8% | 67.8% | 69.0% | **69.10% ± 0.67** |

- Accuracy does not trend with age; the standard error per cell is about 1.3 points in train and 1.9 in holdout.
- It is the same for every true pattern: the diagonal runs 0.69 to 0.71.
- It is stable across six blocks of 100 seasons (0.681 to 0.714).

### 2.2 Confusion matrices (rows = truth, columns = stated outlook)

**Train:**

| Truth \ said | direct | chain | recurve | stall | eyewall | erratic | n |
|---|---|---|---|---|---|---|---|
| direct | **0.692** | 0.063 | 0.064 | 0.056 | 0.061 | 0.064 | 2185 |
| chain | 0.072 | **0.711** | 0.056 | 0.050 | 0.062 | 0.050 | 2073 |
| recurve | 0.064 | 0.061 | **0.691** | 0.050 | 0.063 | 0.072 | 2109 |
| stall | 0.064 | 0.056 | 0.059 | **0.710** | 0.053 | 0.059 | 1415 |
| eyewall | 0.060 | 0.060 | 0.064 | 0.066 | **0.696** | 0.053 | 1357 |
| erratic | 0.056 | 0.050 | 0.052 | 0.085 | 0.067 | **0.690** | 461 |

**Holdout:**

| Truth \ said | direct | chain | recurve | stall | eyewall | erratic | n |
|---|---|---|---|---|---|---|---|
| direct | **0.678** | 0.071 | 0.057 | 0.057 | 0.073 | 0.064 | 1272 |
| chain | 0.062 | **0.686** | 0.062 | 0.063 | 0.066 | 0.061 | 1094 |
| recurve | 0.056 | 0.053 | **0.708** | 0.071 | 0.049 | 0.062 | 771 |
| stall | 0.066 | 0.054 | 0.059 | **0.712** | 0.051 | 0.058 | 725 |
| eyewall | 0.055 | 0.070 | 0.055 | 0.070 | **0.690** | 0.060 | 670 |
| erratic | 0.067 | 0.060 | 0.075 | 0.071 | 0.052 | **0.675** | 268 |

**Errors are uniform.** The wrong cells are statistically uniform: χ² = 32.8 with 24 degrees of freedom, which is not significant. This is not the structured confusion you would get from tracks that look alike (for example, direct mistaken for chain); it is pure label noise.

**Fitted symmetric model.**
- P(said = truth) = 0.6991, and P(said = a given wrong outlook) = 0.0602.
- So each official label carries LLR = ln(0.6991/0.0602) = **2.4525 nats** for the named outlook.
- Equivalently, with probability q = 0.639 the label is the truth, and otherwise it is drawn uniformly from all 6 outlooks.

**Precision P(truth | said).** In train: direct 0.759, chain 0.767, recurve 0.765, stall 0.686, eyewall 0.654, **erratic 0.366**. An official "Erratic" is wrong about 63% of the time, because erratic is rare.

### 2.3 First advisory, changes and persistence (train / holdout)

**First advisory right:** 72.3% / 71.5%.

**Independence of consecutive advisories:**
- P(next correct | previous correct) = 0.692 / 0.690.
- P(next correct | previous wrong) = 0.704 / 0.683.
- So there is no persistence of errors.

**Number of correct labels out of 8 (storms that keep one pattern):** this matches a Binomial(8, 0.697). Over k = 2..8, χ² = 3.45 in train and 1.7 in holdout.

**Changes:**
- The label changes on **50.0% / 50.4%** of consecutive advisory pairs.
- Of the changes, 42.3% / 41.7% go wrong→right (toward the truth), 43.0% / 42.5% go right→wrong (away from it), and 14.0% / 15.1% go wrong→wrong.
- So **changes are not toward the truth**: after a flip, the old and the new label are each right about 43% of the time.

**Plurality vote of the first k labels (ties broken by the latest):**

| k | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| Train | 72.3% | 69.2% | 83.0% | 90.5% | 92.1% | 93.8% | 95.0% | 95.4% |
| Holdout | 71.5% | 70.7% | 80.5% | 87.7% | 91.0% | 92.3% | 93.7% | 94.0% |

**Erratic switch.**
- The first official advisory after a mid-storm turn says "erratic" 70% / 72% of the time, the same as the normal accuracy.
- The median time from the turn to the first "erratic" label is 47 s / 24 s, which simply reflects the 60-s advisory schedule.
- There is no anticipation: before the turn, P(label = erratic) stays at about 0.06.

## 3. Wire chatter

### 3.1 Accuracy

- Overall: **39.57% ± 0.44 in train and 39.85% ± 0.63 in holdout**.
- It is flat by minute of age: 38.2% to 40.6% in train and 35.7% to 42.7% in holdout.
- It is flat by true pattern (0.377 to 0.408) and by round.

**Confusion matrix (train; holdout in the params file):**

| Truth \ said | direct | chain | recurve | stall | eyewall | erratic | n |
|---|---|---|---|---|---|---|---|
| direct | **0.386** | 0.129 | 0.115 | 0.121 | 0.118 | 0.131 | 2781 |
| chain | 0.130 | **0.408** | 0.111 | 0.118 | 0.116 | 0.118 | 2753 |
| recurve | 0.119 | 0.120 | **0.399** | 0.129 | 0.116 | 0.116 | 2624 |
| stall | 0.114 | 0.125 | 0.139 | **0.390** | 0.117 | 0.115 | 1763 |
| eyewall | 0.125 | 0.121 | 0.111 | 0.123 | **0.400** | 0.120 | 1680 |
| erratic | 0.127 | 0.134 | 0.127 | 0.108 | 0.127 | **0.377** | 599 |

- The wrong cells are uniform (χ² = 21.1 with 24 degrees of freedom).
- Fitted model: P(correct) = 0.3957 and P(each wrong outlook) = 0.1209, so each message carries LLR = **1.186 nats**. Equivalently, with probability 0.275 the message is the truth, and otherwise it is uniform over 6.
- Consecutive wire messages are independent: P(next correct | previous correct) = 0.391, and | previous wrong = 0.400.

### 3.2 Is the wire informative?

Yes. Adding wire messages to the official-label filter improves holdout log-loss from 0.463 to 0.391 and accuracy from 84.0% to 86.7% (mean over 48 checkpoints). A wire message is worth about half an official label, measured in log-likelihood ratio.

### 3.3 Lead or lag against the official advisory

There is none; the wire is conditionally independent of the official label.
- P(wire = previous official) = 0.312 and P(wire = next official) = 0.313 (holdout: 0.318 / 0.310). Independence predicts 0.3957·0.6991 + 5·0.1209·0.0602 = **0.313**.
- When the previous official label was wrong, the wire repeats it 12.2% of the time (holdout 12.9%), against 12.1% by chance. So the wire does not copy the forecasters.
- Around a mid-storm turn, P(wire says erratic) is:

| Time relative to the turn | Train | Holdout |
|---|---|---|
| ≤ -60 s | 0.124 | 0.141 |
| [-30, 0) s | 0.143 (n = 49) | 0.227 (n = 22) |
| [0, 30) s | 0.34 | 0.46 |
| [30, 60) s | 0.43 | 0.44 |

The wire switches immediately at the turn. The small rise just before the turn is not significant.

### 3.4 Are some rumours systematically wrong?

**Not in the likelihood**: given the truth, every wrong rumour is equally likely. But because the noise is spread evenly over 6 outlooks, rare outlooks are over-reported:

| Outlook | Share of rumours (train) | Truth share (train) | P(right) train / holdout |
|---|---|---|---|
| erratic | 13.3% | 4.9% | **13.9% / 17.9%** |
| stall | 16.1% | 14.5% | 35.1% / 36.8% |
| eyewall | 15.6% | 13.8% | 35.3% / 35.4% |
| direct | 18.3% | 22.8% | 48.1% / 52.7% |
| chain | 18.9% | 22.6% | 48.7% / 49.9% |
| recurve | 17.9% | 21.5% | 48.1% / 40.5% |

**The "will turn erratic" rumour is the systematically wrong one: about 85% false.** If house flow trades on wire rumours, these are the trades to fade.

## 4. `hurricane_conditions` calibration

**Target.** For each (advisory, island) pair where the island had no impact message yet, the outcome is whether an impact message for that island arrives later in the storm. n = 30,539 in train and 15,248 in holdout.

**Already-impacted islands** (7,861 train rows) cannot be scored, because only the first impact is published. For these islands the advisory gives a mean p of 0.456, and 45% of them have p ≥ 0.95, consistent with hurricane conditions still going on.

### 4.1 Reliability table

| Bucket | Train n | Train mean p | Train observed | Holdout n | Holdout mean p | Holdout observed |
|---|---|---|---|---|---|---|
| 0 | 19747 | 0.000 | 0.001 | 9724 | 0.000 | 0.001 |
| (0, .05] | 2212 | 0.024 | 0.018 | 1122 | 0.023 | 0.019 |
| (.05, .15] | 1074 | 0.096 | 0.095 | 522 | 0.097 | 0.090 |
| (.15, .25] | 544 | 0.206 | 0.191 | 259 | 0.206 | 0.201 |
| (.25, .35] | 413 | 0.303 | 0.317 | 218 | 0.305 | 0.294 |
| (.35, .45] | 358 | 0.405 | 0.419 | 180 | 0.406 | 0.389 |
| (.45, .55] | 350 | 0.502 | 0.506 | 186 | 0.503 | 0.527 |
| (.55, .65] | 358 | 0.605 | 0.648 | 218 | 0.606 | 0.638 |
| (.65, .75] | 401 | 0.709 | 0.726 | 214 | 0.707 | 0.710 |
| (.75, .85] | 504 | 0.811 | 0.839 | 267 | 0.809 | 0.753 |
| (.85, .95] | 975 | 0.913 | 0.904 | 487 | 0.913 | 0.910 |
| (.95, 1) | 1242 | 0.975 | 0.975 | 614 | 0.976 | 0.972 |
| 1 | 2361 | 1.000 | 0.995 | 1237 | 1.000 | 0.998 |

### 4.2 Scores

- **Brier:** 0.0278 in train (climatology 0.1600) and **0.0296 in holdout** (climatology 0.1631). Holdout AUC is 0.990.
- **Recalibration does not help:** binning the probabilities on train gives a holdout Brier of 0.02986 against 0.02962 raw. Use the published probabilities as they are.
- **By island (holdout):**

| Island | n | Base rate | Mean p | Brier |
|---|---|---|---|---|
| Cascadia Prime | 3700 | 0.423 | 0.421 | 0.044 |
| Coral Key | 3875 | 0.191 | 0.191 | 0.028 |
| Azure Isle | 4167 | 0.079 | 0.075 | 0.021 |
| Palm Atoll | 3506 | 0.142 | 0.152 | 0.026 |

- **By advisory number (holdout Brier vs climatology):**

| Advisory | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| Brier | 0.088 | 0.053 | 0.030 | 0.017 | 0.0074 | 0.0015 | ~0 | 0 |
| Climatology | 0.233 | 0.231 | 0.212 | 0.179 | 0.111 | 0.021 | 0.0007 | 0 |

- **When the label is wrong** the probabilities are still calibrated: holdout mean p 0.204 against observed 0.201. They are less sharp, though: Brier 0.040 against 0.025 when the label is right.
- **Mechanism (advisory 1).** The probabilities blend the label and the truth. For example, when truth is recurve but the label is chain, P(Cascadia Prime) is 0.63, close to what a chain label gives when correct (0.73) rather than recurve (0.11). From advisory 2 on they mostly follow the truth (`hc_by_pair.py`).

## 5. Cone accuracy

**Leads.** Cone points are at leads of +30, +60, +120 and +240 s, truncated at age 480. So advisory 8 has only the +30 point.

**Error** is the distance from each cone point to the realised position at that age.

### 5.1 Holdout error by lead (train is within 0.5 units)

| Lead | n | Median | Mean | RMSE | p90 | Mean spread | Median err/spread | Within 1 spread | Within 2 spreads | Persistence median |
|---|---|---|---|---|---|---|---|---|---|---|
| 30 s | 4800 | 11.8 | 13.4 | 16.3 | 22.9 | 14.9 | 0.83 | 62% | 97.5% | 16.8 |
| 60 s | 4200 | 20.7 | 25.4 | 33.4 | 43.5 | 28.4 | 0.83 | 64% | 97.3% | 37.1 |
| 120 s | 3600 | 32.6 | 46.1 | 69.5 | 81.5 | 53.3 | 0.76 | 68% | 96.4% | 81.3 |
| 240 s | 2400 | 52.1 | 88.6 | 149.4 | 185.4 | 113.8 | 0.61 | 77% | 96.3% | 192.4 |

- **Spread calibration.** In train, excluding storms that turn erratic within the lead, the RMS of err/spread is 1.04, 1.02, 1.00 and 0.96 by lead. So **spread ≈ RMS radial error**: use a per-axis σ ≈ spread/√2. A 2-D Gaussian with that σ predicts a median err/spread of 0.83, which matches the shorter leads.
- **Persistence baseline.** The cone beats extrapolating the current velocity by 30-73%.

### 5.2 Holdout median error by true pattern and by stated outlook (each cell: true pattern / stated outlook)

| Lead | direct | chain | recurve | stall | eyewall | erratic |
|---|---|---|---|---|---|---|
| 30 s | 10.9 / 11.3 | 11.8 / 11.5 | 12.1 / 12.0 | 10.6 / 11.2 | 12.1 / 11.9 | **30.3** / 15.4 |
| 60 s | 19.8 / 19.8 | 21.4 / 21.2 | 21.4 / 21.0 | 16.5 / 18.0 | 21.1 / 21.7 | **76.2** / 27.6 |
| 120 s | 31.4 / 32.1 | 36.7 / 35.4 | 36.8 / 36.3 | 20.3 / 23.0 | 32.3 / 32.9 | **202.9** / 52.1 |
| 240 s | 51.2 / 51.2 | 60.2 / 55.0 | 69.1 / 63.7 | 20.4 / 25.3 | 52.1 / 54.4 | **499.5** / 107.9 |

**Label right vs label wrong (holdout median error):** 11.6 vs 12.3 at 30 s, 20.5 vs 21.1 at 60 s, 31.9 vs 34.3 at 120 s, and 49.4 vs 56.9 at 240 s. A wrong label barely hurts the cone. Storms that turn erratic during the lead have median errors of 40 at 60 s, 84 at 120 s and 193 at 240 s.

### 5.3 The cone encodes the true pattern

**Closer to truth or to the label?** For wrong-label advisories (excluding erratic), the cone displacement is closer to the true pattern's mean path than to the label's mean path in this many cases (all 600 seasons, exploratory, `explore5.py`):

| Advisory | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| Closer to truth / closer to label | 249/151 | 362/45 | 420/16 | 416/0 | 391/2 | 426/0 | 430/3 | 396/7 |

**Cone-plus-label classifier** (2-fold cross-fitted within train, `cone_variants.py`), log-loss / accuracy:

| Advisory | Label only | Label + cone | Label + `hurricane_conditions` |
|---|---|---|---|
| 1 | 0.960 / 72.3% | 1.132 / 79.9% (overconfident) | 0.753 / 78.2% |
| 2 | 1.018 / 69.2% | 0.454 / 92.8% | |
| 3 | 1.033 / 69.8% | 0.311 / 96.1% | |
| 4 | 1.022 / 71.1% | **0.057 / 99.5%** | |
| 6 | 1.116 / 68.3% | **0.021 / 99.8%** | |

## 6. Track signatures (train, storms that never turn erratic)

Columns are mean (sd).
- **dx₀**: x(t) − x(0).
- **hdg₀**: heading from the start.
- **hdg₂₀**: heading over the last 20 s.
- Headings are in degrees, with 0 = north and + = east. The start is always (500±69, 740) with velocity (0, −1.8), radius 80 and intensity 1.0.

| Age | Pattern | dx₀ | hdg₀ | hdg₂₀ | Speed (last 20 s) | Radius | Intensity |
|---|---|---|---|---|---|---|---|
| 30 s | direct | −1.7 (8.3) | −1.8 (9.2) | −2.3 (11.6) | 1.80 | 81.7 (2.2) | 1.08 |
| | chain | −14.9 (9.5) | −17.0 (10.8) | −21.0 (13.8) | 1.75 | 80.0 (2.1) | 1.04 |
| | recurve | −15.6 (9.9) | −17.3 (11.1) | −21.2 (13.8) | 1.82 | 79.0 (2.3) | 1.02 |
| | stall | 19.4 (9.7) | 21.4 (10.6) | 26.6 (12.7) | 1.85 | 81.5 (2.1) | 1.11 |
| | eyewall | 10.7 (10.0) | 11.2 (10.8) | 13.2 (13.0) | 1.87 | 81.2 (2.3) | 1.05 |
| | erratic | −8.6 (22.8) | −0.6 (37.6) | −5.7 (51.8) | 1.99 (0.99) | 79.9 | 1.09 |
| 60 s | direct | −4.4 (14.6) | −2.2 (8.0) | −2.5 | 1.89 | 83.1 (3.2) | 1.14 |
| | chain | −38.5 (16.3) | −21.8 (9.4) | −26.7 | 1.84 | 79.8 (3.0) | 1.06 |
| | recurve | −38.4 (16.5) | −21.4 (9.2) | −25.2 | 1.83 | 78.1 (3.0) | 1.01 |
| | stall | 48.3 (17.2) | 26.6 (9.7) | 31.4 | 1.90 | 83.0 (2.9) | 1.19 |
| | eyewall | 27.3 (16.5) | 14.5 (8.8) | 18.0 | 1.91 | 82.0 (3.2) | 1.09 |
| | erratic | −19.5 (62.4) | −2.6 (48.9) | 8.3 (74.7) | 2.42 (1.56) | 79.3 | 1.14 |
| 120 s | direct | −9.6 (24.9) | −2.4 (6.8) | −3.6 | 1.89 | 85.9 (4.5) | 1.21 |
| | chain | −83.2 (27.4) | −23.4 (7.4) | −20.1 (19.7) | 1.79 | 79.6 (4.4) | 1.10 |
| | recurve | −88.5 (25.8) | −25.1 (7.5) | −36.0 (22.5) | 1.81 | 76.1 (4.3) | 1.00 |
| | stall | 108.1 (27.6) | 29.2 (7.6) | 30.9 | 1.98 | 85.6 (4.1) | 1.27 |
| | eyewall | 64.0 (26.0) | 16.6 (6.6) | 19.5 | 1.96 | 83.6 (4.1) | 1.12 |
| 180 s | chain | −85.1 (54.4) | −16.1 | **+8.6** (26.9) | 1.83 | 79.7 | 1.13 |
| | recurve | −168.7 (41.8) | −37.6 | **−76.8** (36.0) | 1.86 | 74.4 | 0.99 |
| | eyewall | 100.3 (34.2) | 17.3 | 19.0 | 1.92 | **110.7 (34.3)** | 1.07 |
| 240 s | direct | −21.5 (39.3) | −2.9 | −2.4 | 1.86 | 91.5 | 1.28 |
| | chain | −39.2 (66.5) | −5.7 | **+28.6** (12.7) | 1.90 | 79.7 | 1.13 |
| | recurve | −276.8 (50.8) | −53.3 | **−104.2** (12.2) | 2.01 | 72.4 | 1.00 |
| | stall | 227.2 (50.4) | 30.9 | 33.0 | 1.69 | 92.5 | 1.39 |
| | eyewall | 138.7 (35.4) | 18.0 | 19.9 | 1.92 | **140.5 (28.9)** | 1.04 |
| 300 s | stall | 263.0 (64.6) | 31.1 | 26.3 (47.0) | **0.91 (0.47)** | 95.5 | 1.39 |
| | eyewall | 176.4 | 18.2 | 20.5 | 1.96 | **157.1 (9.9)** | 1.07 |
| | erratic | −7.6 (386.6) | −5.8 (80.1) | 5.1 (95.9) | **3.51 (0.81)** | 78.2 | 1.16 |

**Rules of thumb:**
- **direct**: heading about −3° throughout. Radius grows to about 100 and intensity to about 1.3.
- **chain**: north-north-west at about −25° until about 120 s, then turns north-east to about +29° from about 180-210 s. Radius stays at about 80.
- **recurve**: same start as chain, then turns west to about −105° from about 120-210 s. Radius shrinks to about 67 and intensity stays at about 1.0.
- **stall**: north-east at about +31° toward Coral Key. It converges to (779±14, 286±13) and slows to about 0.5-0.9 units/s after about 270 s. Intensity reaches about 1.4.
- **eyewall**: north-north-east at about +18°. The radius jumps from about 85 to 150-170 at some point between about 120 and 250 s.
- **erratic**: speed rises to about 3.0-3.5 units/s with random heading. After a mid-storm turn the speed goes from 1.9 to about 3.0 within about 30-40 s.

**Best single-feature d′ between confusable pairs:**

| Pair | 30 s | 60 s | 90 s | 120 s | 150 s | 180 s |
|---|---|---|---|---|---|---|
| direct vs eyewall | 1.35 | 2.04 | 2.47 | 2.89 | | |
| direct vs chain | 1.51 | 2.23 | | | | |
| stall vs eyewall | 1.04 | 1.32 | 1.68 | | | |
| chain vs recurve | 0.46 (radius) | 0.58 | 0.73 | 0.81 | 1.60 (hdg₂₀) | 2.68 |

## 7. Pattern classifier

### 7.1 Model

The target is the current truth, one of 6 classes. All inputs are available in real time, and only messages with age ≤ t are used.

**(a) Source filter: an 11-state hidden Markov chain.**

- **States:**
  - (b, not turned) for the 5 base patterns.
  - (b, turned erratic) for the same 5.
  - erratic from formation.
- **Prior at age 0:** π_b = {direct 0.2375, chain 0.2233, recurve 0.2275, stall 0.1492, eyewall 0.1458, erratic 0.0167}.
- **Transition:** between messages, mass moves from (b, 0) to (b, 1) with probability 1 − exp(−λΔt), where **λ = 1.3494e-4 per second**.
- **Emissions:**
  - Each official label o multiplies the state by P(o | c) = 0.6991 if o = c, else 0.0602.
  - Each wire message multiplies it by 0.3957 if o = c, else 0.1209.
  - Here c is the current class of the state: erratic for every turned state.
- This part alone is the "official + wire" naive Bayes. It is exact under the independent-noise model verified in §2-3.

**(b) Track model.**

- **Checkpoints:** every 10 s from 10 to 480.
- **Features (12):**
  - early displacement per 30 s over [0, min(t, 90)];
  - displacement over the last 20 s;
  - displacement over [t−60, t−20];
  - x and y now;
  - radius − 80 and maximum radius so far − 80;
  - intensity;
  - mean speed over the last 20 s.
- **Subsets:** features that are exactly collinear at early ages are dropped. Intensity is dropped at age 480, where it is identically 0.
- **Density:** per class, a multivariate Student-t with **ν = 30**. The mean and covariance are the class moments, with the covariance shrunk toward its own diagonal: (n·S + 10·diag S)/(n + 10).
- **Rows used to fit:** base classes use rows before any turn. The erratic class uses rows from erratic-from-formation storms, or rows at least **20 s** after the turn.

**(c) Cone model.**

- **Features:** the latest official advisory's cone points relative to the storm position at the advisory age (dx, dy for each lead), plus ln(spread of the +30 point).
- **Density:** Student-t (ν = 30), fitted per advisory number and per (class, label correct?). That makes P(label, cone | c) = P(label | c) · P(cone | c, label = c or not).

**(d) Combination (stacking).**

- **Formula:** log π_t(s) = log F_t(s) + w_tr(t)·S_tr(s) + w_cone(t)·S_cone(s).
- **Component scores:**
  - S(b, 0) = log f_b.
  - S(E) = log f_err.
  - **S(b, 1) = log[(1 − ρ)·f_err + ρ·f_b]**, with ρ = 0.1. This means "turned but still looks like the base pattern" and keeps the model from being overconfident right after a turn.
- **Output:** the 11 states are collapsed to the 6 classes.
- **Fitting the weights:**
  - The weights were fitted per checkpoint by minimising log-loss on 2-fold cross-fitted train predictions (seasons 1-200 against 201-400), using coordinate search on {0, 0.05, ..., 1.2}.
  - They were then smoothed with a running median over 5 checkpoints.
  - ν was chosen from {8, 30}, ρ from {0.1, 0.3}, and the floor δ from {0, 0.0005, 0.001, 0.003}; δ = 0 was selected.
- **Smoothed weights (track / cone):**

| Age | Track weight | Cone weight |
|---|---|---|
| 10-20 s | 0.6 | 0.2 |
| 30-40 s | 0.6 | 0.1 |
| 50-80 s | 0.6 | 0.05 |
| 90-100 s | 0.5 | 0.05 |
| 110-180 s | 0.4 | 0.05 |
| 190-200 s | 0.4 | 0.1 |
| 210-260 s | 0.5-0.6 | 0.2 |
| 270-330 s | 0.6 | 0.4 |
| 340-360 s | 0.8 | 0.4 |
| 370-400 s | 1.2 | 0.3-0.4 |
| 410-430 s | 1.0 | 0.2-0.3 |
| 440-480 s | 0.8 | 0.2-0.3 |

- **Meaning of weights below 1.** They correct for double counting (the track and the cone see the same motion) and for overconfidence of the Gaussian-like densities.
- **`hurricane_conditions` as a third component** (logit features) added nothing: holdout mean log-loss 0.151 with it against 0.150 without. It is left out of the live model (`USE_HC=0`).

### 7.2 Holdout results vs age (log-loss in nats / accuracy; target = current truth; n = 600 storms at each age)

| Age (s) | Latest official | Official filter | Official + wire filter | Track + prior only | Labels + wire + cone | **Full model** |
|---|---|---|---|---|---|---|
| 10 | 1.667 / 27.5% | 1.667 / 27.5% | 1.664 / 27.7% | 1.413 / 37.8% | 1.664 / 27.7% | **1.411 / 38.0%** |
| 20 | 1.667 / 27.5% | 1.667 / 27.5% | 1.628 / 30.8% | 1.217 / 46.8% | 1.628 / 30.8% | **1.192 / 49.0%** |
| 30 | 1.057 / 71.5% | 0.966 / 71.5% | 0.926 / 72.2% | 1.081 / 51.8% | 0.624 / 79.5% | **0.542 / 81.7%** |
| 40 | 1.057 / 71.5% | 0.967 / 71.5% | 0.907 / 72.3% | 0.948 / 54.8% | 0.610 / 80.5% | **0.483 / 84.0%** |
| 60 | 1.061 / 71.3% | 0.976 / 71.3% | 0.901 / 72.0% | 0.750 / 66.8% | 0.620 / 80.7% | **0.392 / 86.2%** |
| 90 | 1.077 / 70.7% | 0.610 / 76.8% | 0.549 / 81.7% | 0.592 / 72.3% | 0.227 / 93.3% | **0.168 / 94.2%** |
| 120 | 1.077 / 70.7% | 0.624 / 76.5% | 0.540 / 82.7% | 0.483 / 79.7% | 0.240 / 93.5% | **0.157 / 95.3%** |
| 150 | 1.171 / 66.8% | 0.433 / 84.7% | 0.357 / 87.0% | 0.395 / 83.5% | 0.131 / 95.7% | **0.057 / 98.0%** |
| 180 | 1.171 / 66.8% | 0.436 / 84.7% | 0.349 / 87.7% | 0.261 / 91.3% | 0.132 / 96.0% | **0.053 / 98.2%** |
| 210 | 1.175 / 66.7% | 0.316 / 89.2% | 0.236 / 92.3% | 0.154 / 94.7% | 0.017 / 99.7% | **0.010 / 99.7%** |
| 240 | 1.179 / 66.5% | 0.328 / 89.0% | 0.235 / 92.5% | 0.105 / 96.0% | 0.026 / 99.5% | **0.015 / 99.5%** |
| 300 | 1.057 / 71.5% | 0.245 / 92.3% | 0.145 / 95.0% | 0.028 / 99.3% | 0.016 / 99.8% | **0.013 / 99.8%** |
| 360 | 1.126 / 68.7% | 0.219 / 92.7% | 0.146 / 95.0% | 0.030 / 99.5% | 0.047 / 99.3% | **0.026 / 99.7%** |
| 420 | 1.159 / 67.3% | 0.205 / 93.2% | 0.154 / 95.2% | 0.059 / 98.7% | 0.067 / 98.8% | **0.065 / 99.2%** |
| 470 | 1.118 / 69.0% | 0.169 / 94.7% | 0.129 / 96.3% | 0.066 / 98.3% | 0.032 / 99.3% | **0.023 / 99.3%** |
| **Mean of 48 checkpoints** | 1.142 / 67.2% | 0.463 / 84.0% | 0.391 / 86.7% | 0.294 / 87.7% | 0.214 / 92.9% | **0.150 / 94.8%** |

Notes on the table:
- **Latest official** means P = 0.699 on the named outlook and 0.060 on the others.
- **Prior only** scores a mean of 1.712 / 26.5%.
- **Multi-class Brier** (mean over checkpoints): 0.073 for the full model against 0.518 for the latest official.
- **Train, cross-fitted:** the full model scores 0.162 / 94.2%. The live engine run in-sample on seasons 1-400 scores 0.147 / 94.9%, against 0.150 / 94.75% on the holdout, so there is no meaningful overfit.

**Calibration (holdout, all class-probabilities pooled):**

| Bucket | 0-0.1 | 0.1-0.2 | 0.2-0.3 | 0.3-0.4 | 0.4-0.5 | 0.5-0.6 | 0.6-0.7 | 0.7-0.8 | 0.8-0.9 | 0.9-1.0 |
|---|---|---|---|---|---|---|---|---|---|---|
| Mean p | 0.0026 | 0.144 | 0.247 | 0.348 | 0.445 | 0.547 | 0.651 | 0.752 | 0.858 | 0.993 |
| Observed | 0.0029 | 0.144 | 0.250 | 0.354 | 0.426 | 0.559 | 0.651 | 0.742 | 0.869 | 0.991 |

**Holdout confusion at 30 s** (rows = truth; columns direct, chain, recurve, stall, eyewall, erratic):

| Truth | direct | chain | recurve | stall | eyewall | erratic |
|---|---|---|---|---|---|---|
| direct | 130 | 6 | 9 | 6 | 13 | 0 |
| chain | 12 | 107 | 20 | 0 | 0 | 0 |
| recurve | 4 | 6 | 90 | 0 | 0 | 0 |
| stall | 6 | 0 | 0 | 85 | 2 | 0 |
| eyewall | 7 | 3 | 2 | 8 | 66 | 0 |
| erratic | 2 | 1 | 2 | 1 | 0 | 12 |

- The main early confusions are **chain → recurve** and **direct vs eyewall**.
- At 90 s the remaining chain/recurve confusion is 9 + 6 storms. At 240 s there is 1 off-diagonal case among the non-erratic storms.

**How early the pattern is identified** (holdout storms that keep one pattern, raw-feed replay, `timing.py`):

| Method | Median age P(true) ≥ 0.9 | Median age P(true) ≥ 0.99 (p25 / p75) | MAP stays correct from: median / p90 |
|---|---|---|---|
| Full model | 30 s (p75 90) | **90 s** (90 / 150) | 30 / 90 s (99.8% of storms lock in) |
| Official + wire | 90 s (p75 170) | 210 s (150 / 270); 94.5% ever reach it | 30 / 240 s |
| Official only | 90 s | 210 s; 88% ever reach it | 30 / 270 s |
| Latest official | never (it is capped at 0.70) | never | 390 / 450 s; only 70% of storms ever lock |

**Robustness.** With 30% of storm messages dropped at random, the full model scores 0.166 / 94.5%; the engine falls back to track points and the nearest observations.

**Worst holdout misses** (P(true) about 2e-4):
- A direct storm whose early motion and advisory-2/3 cones looked like recurve (season 533, round 3).
- A chain storm labelled "erratic" twice, with an erratic-looking wide-spread cone at age 330 (season 414, round 3).
- Storms in the first seconds after a late turn.

The Student-t densities and the ρ mixture limit how bad these misses get. With the earlier Gaussian, single-likelihood version, misses reached 1e-40 and the mean log-loss was 0.158.

### 7.3 Turning erratic: P(turns erratic) by age

- **Hazard:** constant at λ = 1.349e-4 per second (train: 74 turns over the exposure time).
- **P(turns mid-storm):** 6.27% in train and 6.84% in holdout. **P(erratic from formation):** 1.67% / 2.50%.
- **Turn time is uniform over age:** across 60-s bins the counts are 11, 15, 12, 14, 18, 15, 12, 17 (χ² = 3.05 with 7 degrees of freedom).
- **By pattern** (train turn rate): direct 8.1%, chain 6.7%, recurve 6.2%, stall 2.2%, eyewall 6.9%. Over all seasons stall is 2.9% against about 7% for the others, but the pattern differences are not significant (χ² = 7.5 with 4 degrees of freedom, p ≈ 0.11), so a pooled λ is used.
- **Formula:** P(turns later | not erratic yet at age a) = 1 − exp(−λ·(480 − a)).

| Age a | 0 | 60 | 120 | 180 | 240 | 300 | 360 | 420 |
|---|---|---|---|---|---|---|---|---|
| Model | 6.3% | 5.5% | 4.7% | 4.0% | 3.2% | 2.4% | 1.6% | 0.8% |
| Empirical, train | 6.3% | 5.7% | 4.9% | 4.1% | 3.2% | 2.1% | 1.4% | 1.0% |
| Empirical, holdout | 6.8% | 6.2% | 5.4% | 5.1% | 4.6% | 3.5% | 2.3% | 0.9% |

- **P(currently erratic)** in train is 1.7% at age 0, 2.3% at 60, 3.1% at 120, 4.8% at 240, 5.8% at 300, 6.9% at 420 and 7.8% at 480.
- **Detection:** the full model's P(erratic) reaches 0.5 for 37 of 40 holdout turns within the storm. The median delay is **33 s** (p75 50 s, maximum 101 s). False alarms occur on 0.13% of non-erratic checkpoints.
- **The engine also outputs** P(erratic by the end of the storm) = P(erratic now) + (1 − P(erratic now))·(1 − exp(−λ(480 − t))). Holdout Brier is 0.072 for ages 10-60 s, 0.059 for 60-150 s, 0.045 for 150-300 s and 0.020 for 300-480 s, against climatology 0.083.

## 8. Live use (`posterior.py`, `fits/sources.json`)

```python
import sys; sys.path.insert(0, ".../work/se/sources")
from posterior import PatternPosterior
pp = PatternPosterior(".../work/se/fits/sources.json")
pp.on_message(msg)          # every news message, in seq order (dedups by seq; resets on storm_formed or a new round/storm)
out = pp.posterior()        # {'p': {6 classes}, 'map', 'p_turned_mid_storm', 'p_erratic_by_end',
                            #  'p_turn_later_if_not_yet', 'p_base_pattern': {...}}
```

**What the engine does.**
- The label filter updates on every official and wire message.
- The track score uses the latest 10-s checkpoint at or below the current age, computed from storm observations; it falls back to track points if observations are missing.
- The cone score uses the latest official advisory.
- The cost per call is about 1 ms.

**Replay check.** `python3 -I posterior.py fits/sources.json <history> 401 600` replays the raw holdout feeds. It reproduces the batch numbers exactly: mean 0.1501 / 94.75%.

**Demo.** `demo.py` prints the posterior after each message for one storm. Season 1, round 1 (Dara, a recurve) goes like this:
- The first label "chain" moves the posterior to 0.93 chain at 30 s.
- Two recurve wire messages and the "recurve" advisory at 90 s bring it to 0.965 recurve.
- From 150 s it stays at least 0.997 recurve, even though the later advisories say "stall" and "eyewall".

**What is in the params file:**
- `likelihood_tables`: 6×6 log-likelihood tables for the official and wire labels, the priors and the hazard. A bot without the full engine can update a naive-Bayes posterior message by message from these alone.
- `classifier`: the full model, with the source model, ν, ρ, δ, weights per checkpoint, track densities per checkpoint and class, and cone densities per advisory number, class and label correctness.
- `official`, `wire`, `hurricane_conditions`, `cone`, `erratic`, `track_signatures_train` and `holdout_metrics` with all the tables above.

## 9. What can and cannot be known in real time

**Can be known in real time:**
- Storm position, velocity, radius and intensity every 2 s; the track every 20 s; official advisories at ages 30, 90, ..., 450; wire messages at random times; and impact messages.
- Every feature used here is computed only from messages with age ≤ t. The replay takes each snapshot before any message with a later age is processed.
- The cone and `hurricane_conditions` are the most informative real-time fields from advisory 2 on. The track is the most informative field before the first advisory and between advisories.

**Cannot be known:**
- **Whether or when a storm will turn erratic.** The hazard is constant (memoryless), there is no precursor in the wire or the official label, and the turn is visible only once the track speeds up, a median 33 s later.
- **Chain vs recurve before their turn** (about 110-150 s). Their tracks are the same; only the radius drifts weakly (d′ of 0.5-0.8). At advisory 1 the cone and `hurricane_conditions` partly follow the label for this pair, so before about 90 s that pair rests mostly on the label and the wire.
- **Whether the label is right.** It is pure noise around the truth, so a single label is never more than about 70-77% reliable.

**Data caveats:**
- The research data have no announcements.
- The live feed sends only the latest storm/track message of a group after a gap. The engine handles missing observations; see the 30% drop test above.

## 10. Caveats

- **Small erratic samples.** The erratic classes rest on only 35 storms erratic from formation and 114 turned storms (74 of them in train). The erratic densities and the hazard are the least certain parts of the model; the pattern-specific turn rate for stall (about 3%) may be real but is not significant.
- **Weights are fitted, not structural.** The stacking weights are fitted on cross-fitted train predictions and smoothed. They compensate for the dependence between the track and the cone; they are not structural constants.
- **Confident errors are rare but possible.** P(true) can still be as low as about 2e-4 in rare cases where the forecast product itself is wrong (season 533, round 3). For risk sizing, consider the δ = 0.001 floor; it costs less than 0.001 nats of holdout log-loss.
- **Impacts are first-only.** The `hurricane_conditions` calibration covers only islands not yet impacted, because only the first impact is published.
- **Stable but not guaranteed.** The source accuracies are stable across seasons (100-season blocks: official 0.681-0.714, wire 0.390-0.407). Nothing guarantees the live season uses the same noise rates, but the packet says it comes from the same simulator.
- **Exploratory scripts on all 600 seasons.** The early exploratory scripts (`explore1`-`explore6`, `hc_by_pair`, `headlines`) looked at all 600 seasons to find the structure. Every fitted number and every holdout metric reported above uses seasons 1-400 only for fitting.

## 11. Files

**Work directory:** `/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/sources/`

**Cache:** `storms.json`.

**Result files:** `res_official_wire.json`, `res_hc.json`, `res_cone.json`, `res_hazard.json`, `res_track_table.json`, `res_classify_final.json`, `res_timing.json`, `res_timing_drop30.json`.

**Final model:** `model_classify_final.json`; `log_classify_final.txt` is the full printout from the final classifier fit.

**Earlier versions, for reference:**
- `res_classify_v1.json` and `model_classify_v1.json` (Gaussian, mean log-loss 0.158).
- `res_classify2.json` (t-dist, 0.151).
- `res_classify3.json` (with `hurricane_conditions`, 0.151).

**Params:** `/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/fits/sources.json`

**Rebuild sequence** (from the work directory, with H = the history folder):
1. `extract.py H storms.json`
2. `official_wire.py`, `hc_calib.py`, `cone.py`, `hazard.py`, `track_table.py`, each as `storms.json out.json`
3. `classify3.py storms.json res_classify_final.json model_classify_final.json` (about 70 s)
4. `timing.py model_classify_final.json H 401 600 res_timing.json [0.3]`
5. `assemble.py <work_dir> ../fits/sources.json`
6. `posterior.py ../fits/sources.json H 401 600` (replay check)

