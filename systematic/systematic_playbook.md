# CTC 2026 Systematic Equities (Hurricane Season): data-driven playbook

> **Testnet host check (23 Oct).** Use **`cornelltradingcompetition.org`** (REST `https://…/api/v1`, WebSocket `wss://…/ws/v1/market-data`). That is the host the team bot key works on, and its `/cases/hurricane.json` is identical to the research `case.json`, timing included (60 s pre-storm, 480 s storm, 240 s aftermath), so every time in this playbook applies. The starter kit's `hurricane.cornelltradingcompetition.org` rejects the key and serves a different case file (20 s pre-storm, 180 s aftermath), so don't point the bot there. Still derive times from the served `case.json` and feed events rather than hardcoding them, and re-diff `case.json` at startup on competition day.

> **Files in this folder:** `engine/` is `work/se/engine/` (fair-value engine; run from any directory), `fits/` is `work/se/fits/`, `laws/` holds `se_laws.py` and `strategy_addons.py` (from `work/final/`), `bot_skeleton/` is `work/se/bot/skeleton/`, `data/case.json` is the research `case.json`, and `research/` holds every fit, verification, engine, backtest and red-team report plus the main backtest scripts. The research data itself is in `~/Downloads/hurricane-season-research/`; scripts that replay seasons take its `history/` folder as an argument.

## Testnet findings (23 Oct recording) [testnet]

The full write-up, with every number traced to a script, is in **`testnet/analysis/TESTNET_FINDINGS.md`**. The evidence is one season of real books: season 224 round 3 plus season 225 rounds 1-3.

1. **The testnet book is not the consensus plus a spread.** It opened at the packet prices. In 225 r1, HAVEN sat about 13% above `market.csv` for most of the storm. Price every edge off the book.
2. **Costs.** Storm half-spread is 12-23 bp. An L/4 clip costs a median 34-51 bp for stocks and 16 bp for ETFs. In RT form that is h = 11-16 bp and γ = 0.8-1.3% per full limit (stocks). Costs at 72-120 s are 2-7× the storm, and a full-limit stock clip hits a 3-L wall (304-328 bp). **S3 clips are now L/12** and the threshold is 2× the measured cost (§4.1).
3. **Formation.** The jump shows on the books (sign 29/33), but the book is half done only at a median 72 s. H1 with the 72-78 s exit made **+$65 on books** against +$7.4k on the consensus. **H1 no longer exits before 80 s; S3 takes over the H1 book** (§4.2).
4. **S1-r3 is confirmed.** The book stays stale 6-8 s after the final report, worth at least $0.9k per round 3 from +1 s. Keep the taker, and move the sanity check to **≥ 756 s** (§4.3).
5. **ETF lead-lag.** The ETF books lag their baskets by 7 s, but the rule pays on books only with a capped entry: +2.7k to +3.7k over 4 rounds, against −6.1k to −9.7k when it walks the book. **Still TEST-FIRST, default OFF** (§4.5).
6. **S3 is unproven on books.** In season 225 it made −$3.8k on books against +$3.6k on the consensus; round 3 was positive. Score it on fills in the next testnet season at 25% size.
7. **Re-backtest with measured costs** (still priced on the consensus). The Plan as written earns 10.7k. The new configuration earns 19.8k [20.1k] with H1 at full size (16.7k at half size), and 14.2k at costs ×1.5 with +2 s of latency. **Plan on 10-15k per season, not 20k.**
8. **Feed.** The feed dropped 5 times without a close frame, once for 31.7 s, and season 225 lost 22 messages. The engine's snapshot handler resets the pattern posterior: 16 of 24 recovered advisories move a fair value past the S3 threshold. **Fix this and run two connections before any live run** (§6, §9.1).
9. **Advisories.** Testnet official advisories are fresh ensemble draws, but the engine impact is negligible: S3 P&L is $11,164 against $11,122.
10. **Other teams already trade the clock pattern.** They net-bought 1-2 L of the formation names in 0-50 s, and someone paid $8.5k sending market orders into the ±8% walls at the season open. **Send no marketable orders in the first 3 s of a round.**

**Pending:** a proposed 1-lot order test (fills, latency, price protection; findings §7). The team must approve it before anyone runs it.

This replaces `work/final/systematic_playbook.md`. It keeps everything from that document that survived verification and the red-team review, and corrects what they overturned (section 10 lists the changes).

**How to read the numbers**
- Dollar figures are **per season, at full position limits, on the house consensus** (`market.csv`).
- They are measured on the **holdout seasons 401-600**. Train figures (seasons 1-400) are in brackets.
- Labels:
  - **[P]** the packet or case.json states it;
  - **[D]** measured, with the script named in the appendix;
  - **[RT]** from the red-team report, which has the last word on disputed numbers;
  - **[I]** inference or an untested rule;
  - **[Ask]** post the question on Slack;
  - **[testnet]** changed by the 23 Oct testnet recording (`testnet/analysis/TESTNET_FINDINGS.md`). Text that was superseded is kept next to it.

**The "RT fill model"** is behind every "realistic" number in this document:
- An order fills at the next 2-s row after the decision, at house consensus ± h. The half-spread h is 0.25% unless stated.
- Each share pays an extra depth cost γ·|q|/L, with γ = 0.5%, q the clip size and L the limit.
- Each symbol fills at most ¼ of its limit per 2-s row ("worked clips").
- At most 180 actions in any 60 s.
- Orders in the report windows fill one row late, i.e. we lose the report race to faster teams.
- **Not modeled:** other teams crowding the same trades, house inventory skew beyond γ, and announcements.
- **[testnet] Measured on the recorded books, this fill model is superseded.**
  - **h:** measured storm intercept 11-16 bp for stocks and 14 bp for ETFs, against 0.25%. The 10-share cost after 540 s is 10-13 bp.
  - **γ:** 0.8-1.3% per full limit for stocks and 0.07-0.11% for ETFs, for clips up to ½ L, against 0.5%. Costs at 72-120 s are 2-7× the storm. Beyond about 0.7 L a stock clip reaches the 3-L wall.
  - **Clips:** S3 now works L/12 clips, not ¼.
  - **Replacement:** the re-backtest's fill model uses the phase tables in `testnet/analysis/books/cost_model.json`, plus a follow-on charge for our own same-side flow and an impact term. The Plan's numbers under that model are in the testnet findings section above (`TESTNET_FINDINGS.md` §3, §5).

---

## 1. How this case is won

**What the house does well, and where it is beatable.**
- The house consensus is a slow but mostly efficient Bayesian. At the round opens, at storm end, and once it has absorbed the preliminary report, it is as accurate as our full Monte Carlo posterior (§3.3).
- **It is beatable during the storm**, in two ways:
  1. It over-reacts to storm formation in a way you can predict from the clock. At 64 s HAVEN jumps +9.7%, the islands +1.4% to +3.0%, and the rebuilders −1.1% to −1.4%. It then drifts back: HAVEN falls 7.9% by storm end.
  2. It lags a physics engine that integrates the 2-s storm feed. In round 3 at real 240 s, PORT RMSE is 4.47 for the engine against 5.68 for the house.
- In the last 40 s of round 3 settlement is known exactly. The house stays stale for another 4 s on stocks and 10 s on ETFs.

**The plan has four parts:**
- **S3:** trade the engine against the house in worked ¼-limit clips from 80 s to 540 s. **[testnet] Use L/12 clips, with the threshold at 2× the measured cost (§4.1).**
- **H1:** pre-position for the formation jump at 50-56 s and exit at 72-78 s. **[testnet] No exit before 80 s: S3 inherits the H1 book at 80 s, because the book has barely repriced by 74 s (§4.2).**
- **Flatten:** close the book before the preliminary report in rounds 1-2. **[testnet] Over 540-568 s in L/12 clips.**
- **S1-r3:** take exact settlement at 740 s in round 3. **[testnet] Confirmed on the books; keep the taker.**

**Expected value.**
- Under the RT fill model the plan earns **$20.3k per season [19.5k]**, with sd 11.7k, p5 +2.3k, and 96% of seasons positive.
- Across half-spreads of 0.1%, 0.25%, 0.5% and 1% it earns $22.8k, $20.3k, $16.1k and $9.3k.
- If the round-1 formation fills show no crowding, H1 at full size raises it to $24.7k [23.9k].
- **[testnet] With measured book costs** (re-backtest, still priced on the consensus):
  - the Plan as written earns **$10.7k [9.8k]**, with p5 −7.8k;
  - the new configuration (L/12 S3 clips, H1 at full size with no forced exit) earns **$19.8k [20.1k]**, $15.0k at costs ×1.5 and $14.2k at costs ×1.5 with +2 s of latency;
  - with H1 at half size it earns $16.7k [16.6k].
  - The only book evidence for S3 (season 225) is negative. **Plan on $10-15k per season.**

**What did not survive the red team.**
- The backtest's $35.5k was an artefact of filling the full limit in one order with infinite depth. With a 0.5% depth cost, single-shot fills fall to $14.7k.
- Report snipes and ETF lead-lag are latency races worth at most $1-4k on top of the plan, so they wait for testnet measurements.
  - **[testnet] Measured on the books:**
    - The ETF books do lag, by 7 s. Lead-lag pays only with capped entries.
    - In season 225, rounds 1-2 reports left $0-12 of stale stock value per report.
    - Both stay default OFF.
- Every figure is before other teams crowd the formation and drift trades. They are visible to anyone who looks at `market.csv`.

**Plan numbers** [D] `dp_bt3.py`, h = 0.25%, γ = 0.5%, worked clips, report race lost:

| Configuration | Holdout mean [train] | sd | p5 | Seasons > 0 |
|---|---|---|---|---|
| **Plan**: H1 at ½ size, S3, flatten at 562-568 s in rounds 1-2, S1-r3; no S2, no rounds 1-2 S1, no REIN | **20,346 [19,504]** | 11,745 | 2,313 | 96% |
| Plan with H1 at full size | 24,715 [23,917] | 13,706 | 2,547 | 97% |
| Plan, +2 s latency on every order | 18,592 [17,621] | 11,688 | 230 | 95% |
| Plan, harsh: +2 s everywhere, storm half-spread max(h, 0.5·σ₂ₛ) | 18,006 [16,986] | 11,637 | −536 | 94% |
| Plan, depth cost γ = 0 / 0.25% / 1% | 22,964 / 21,623 / 17,790 | | 3,841 / 2,923 / 758 | |
| Plan at h = 0.1% / 0.5% / 1% | 22,846 / 16,050 / 9,287 | | 4,008 / −1,452 / −6,347 | |
| Reference: backtest S6R, single shot, infinite depth [RT repro] | 35,483 [35,410] | 16,029 | 9,982 | 99% |
| Reference: S6R single shot with γ = 0.5% [RT] | 14,661 [14,249] | 15,183 | −8,472 | 83% |

**Not included in the plan number:**
- S1-r3 placed as limit orders at S ∓ ε: +0.15k if we are 2 s late, +1.0k if we are first [RT oracle at 25 bp]. The simulator's market-order version nets about 0.
- The REIN ask at ≥ 90.02: +$50-130.
- Swapping the ETF legs for lead-lag, if the testnet shows the ETF book lags: an estimated +1.3k to +4.3k at 25 bp [I, additive].

---

## 2. The fitted model

Every constant was fitted on seasons 1-400 and checked on 401-600 by an independent verifier. The engine (`work/se/engine/`) implements all of it. `engine_params.json` holds the constants.

### 2.1 Settlement laws (exact)

**Definitions:**
- **D:** a company's cumulative **final** damage_pct, summed over the season's storms, in percentage points.
- **L:** season insured loss in $M, the sum of the final `industry_loss_musd` values.
- **T_w:** for each company, Σ over its sites of weight × real seconds the site was closed during the season.

| Symbol | Law | Sensitivity per share | Settlement error with exact inputs, all 600 seasons: RMSE / max ($) |
|---|---|---|---|
| PORT | 100 − 0.55·D − 0.02·T_w | −0.55/pp (−$110/pp at full limit); −0.02 per weighted closed second (−2.40 per market hour) | 0.011 / 0.059 |
| ISLE | 60 − 0.38·D (no closure cost) | −0.38/pp | 0.003 / 0.009 |
| AQUA | 35 − 0.20·D (no closure cost) | −0.20/pp | 0.003 / 0.006 |
| GRID | 50 − 0.24·D − 0.05·T_w | −0.24/pp; −0.05 per weighted second | 0.012 / 0.052 |
| FERRY | 25 − 0.05·D − 0.06·T_w | −0.05/pp; −0.06 per weighted second | 0.014 / 0.077 |
| RECON | 40 + 24·(1 − e^(−X/1000)), with X = Σ_storms (9·D_PORT + 7·D_GRID + 1.2·D_FERRY) in $M | +0.024·e^(−X/1000) per $M | 0.003 / 0.006 |
| MAT | 28 + 14·(1 − e^(−L/1200)) | +0.0117·e^(−L/1200) per $M | 0.003 / 0.005 |
| GEN | 32 + 18·(1 − e^(−O/120)), with O = GRID's T_w (substation closure) | +0.15·e^(−O/120) per weighted second | 0.019 / 0.099 |
| HAVEN | 75 − 0.06·clip(L − 400, 0, 900); floor 21 once L ≥ 1300 | −0.06 per $M while 400 < L < 1300 | 0.004 / 0.012 |
| REIN | 90 − 0.035·max(L − 1300, 0); top of the layer unidentified, at least ~1760 | −0.035 per $M above 1300 | 0.001 / 0.007 |
| ARCH | basket: 1 PORT + 1 ISLE + 2 AQUA + 1 GRID + 2 FERRY + 1 HAVEN | – | 0.032 / 0.156 |
| REBLD | basket: 2 RECON + 2 MAT + 1 GEN | – | 0.020 / 0.106 |

**Notes on the laws:**
- **Source of the errors.** [D] `dp_checks/chk_settle_all.py`, which uses the engine's `tickprob_impact` closure estimator. The verifiers obtained the same constants independently (`verify_island`, `verify_rebuild`, `verify_insurance`).
- **The closure terms matter.** Without them the RMSE is $2.27 for PORT, $3.97 for GRID, $5.61 for FERRY and $8.36 for GEN (earlier playbook, `v_settle.py`).
- **What the laws rule out.**
  - Value is not V0·(1 − damage).
  - There is no insurance recovery for the companies.
  - Damage adds in reported percentage points.
  - The undamaged values (100 / 60 / 35 / 50 / 25) sit **above** the starting prices. The house opens at the unconditional expected settlement.
- **Live L.** Use the sum of the three **final** feed reports. The HAVEN error is then at most 0.012.

### 2.2 Wind and closure

| Item | Law and parameters | Accuracy |
|---|---|---|
| Wind at a site | w = I·exp(−(d/r)²) if d < 2r, else 0. The cut-off is crisp: the largest d/r with damage is 1.991, the smallest without is 2.001 | Free exponent 1.993; Gaussian beats every alternative shape |
| Closure | Site closed while w > 0.35. An island's `impact` message is the first second any **company** site on the island crosses (all 17 sites) | Threshold 0.350 best on train. 2,628 of 2,631 impacts match with 0 false crossings; 99.3% within 2 s |
| Closure seconds T (`tickprob_impact`) | The simulator ticks at 1 s. Tick a0 (observed) counts 1 if w > 0.35. Every other tick counts P = Φ((m·g − 0.35)/(sd·g)), where g = exp(−(d/r)²) uses track points inside gaps, m is intensity interpolated linearly, sd = sqrt(var1·(t − a0)(a1 − t)/(a1 − a0)) and var1 = 0.0294²/2. Before an island's impact, every site on it is set to 0; at the impact tick, the most exposed site is set to 1 | Holdout RMSE: PORT 0.0118, GRID 0.0122, FERRY 0.0149 (linear interpolation: 0.0139 / 0.0138 / 0.0187) |
| Feed outages | Only the last storm and track message of a burst arrives. Fill position from track points; unobserved intensity cannot be recovered | 6 gaps of 5-40 observations cost PORT 0.03, GRID 0.08 and FERRY 0.12 RMSE; the engine stress test costs about 0.10 for PORT/GRID/FERRY and 0.17 for GEN |

**Sites** [P] case.json, as (x, y, weight); map 1000 × 700, y points south:

| Company | Sites |
|---|---|
| PORT | Cascadia (470, 80, 1.00) |
| ISLE | Coral (780, 285, 0.34), Azure (830, 480, 0.34), Palm (230, 525, 0.32) |
| AQUA | Palm (240, 605, 0.30), Palm (130, 540, 0.25), Coral (760, 335, 0.25), Coral (705, 290, 0.20) |
| GRID | Cascadia (420, 110, 0.30), Cascadia (560, 140, 0.20), Coral (770, 285, 0.18), Azure (825, 475, 0.17), Palm (245, 510, 0.15) |
| FERRY | Cascadia (380, 185, 0.30), Coral (720, 300, 0.25), Azure (760, 470, 0.20), Palm (330, 480, 0.25) |

**How often sites close** (earlier playbook, all 600 seasons):

| Company | Storms with any closure | Weighted closed seconds per storm: mean / p90 / max |
|---|---|---|
| PORT | 32% | 27 / 104 / 180 |
| GRID | 88% | 25 / 47 / 86 |
| FERRY | 76% | 28 / 70 / 92 |

### 2.3 Damage physics

**Model:**
- Hazard: H_s = Σ over 2-s observations of 2·w_s (wind-seconds).
- Fraction of the site's **remaining** value destroyed: f_s = 1 − exp(−k_c·m·H_s).
- Company damage: D_c = 100·Σ_s weight_s·rem_s·f_s.
- Noise: log m ~ N(0, σ_c²), one draw per company per storm, shared by that company's sites, independent across companies and storms.

**Parameters** (train, censored MLE):

| | PORT | ISLE | AQUA | GRID | FERRY |
|---|---|---|---|---|---|
| k_c | 0.010997 | 0.008684 | 0.015515 | 0.011654 | 0.008476 |
| σ_c | 0.3417 | 0.3507 | 0.3568 | 0.3674 | 0.3554 |
| Holdout RMSE of E[D \| full track] at storm end (pp) | 5.33 | 2.48 | 2.71 | 3.15 | 3.16 |
| Same, plus the preliminary report (pp) | 4.39 | 1.95 | 2.30 | 2.79 | 2.58 |
| 80% interval coverage, storms with hazard > 0 | 0.79 | 0.82 | 0.84 | 0.84 | 0.82 |

**Checks and consequences:**
- The pooled σ is 0.3497. Holdout point accuracy does not depend on the σ choice.
- **Depletion is real and site-level.** A storm destroys a share of what is left, so the same direct hit adds less to PORT the more it is already damaged.
  - Storms are otherwise independent: "fresh-equivalent" losses are flat across rounds at 407.6 / 414.8 / 403.9.
  - Reported losses fall by round: train means 407.6 / 330.7 / 257.9.
- **Damage locks in late.** Share of final damage already done, by storm age in seconds:

  | Age (real s) | 240 (300) | 300 (360) | 340 (400) | 360 (420) | 380 (440) | 400 (460) | 420 (480) | 440 (500) |
  |---|---|---|---|---|---|---|---|---|
  | PORT | 0.01 | 0.10 | 0.35 | 0.53 | 0.69 | 0.83 | 0.93 | 0.98 |
  | Weighted by insured loss | 0.16 | 0.37 | 0.59 | 0.71 | 0.81 | 0.90 | 0.96 | 0.99 |

- **The multiplier m is not revealed before the preliminary report.** The house's 532→568 s move loads at most 0.005 $/pt on (D − E[D]), with R² ≤ 0.002.

### 2.4 Insured loss and the loss distribution

**Per-storm loss.** L_storm = 9.0·PORT + 6.0·ISLE + 2.5·AQUA + 7.0·GRID + 1.2·FERRY, using that storm's percentage points.
- Exact to report rounding: RMSE $0.044M.
- Total exposure is $2,570M.

**Season L ($M)** (briefing, 600 seasons):

| min | p10 | p25 | p50 | p75 | p90 | p99 | max |
|---|---|---|---|---|---|---|---|
| 128.7 | 595 | 756 | 1,035 | 1,252 | 1,422 | 1,650 | 1,766 |

**Distribution facts:**
- **Insurance regime at season end:**
  - L < 400 (HAVEN 75): 29 seasons;
  - 400-1300 (HAVEN's layer): 453 seasons;
  - L > 1300 (REIN pays): 118 seasons.
- **Mean storm loss by true pattern** (earlier playbook): direct 502, eyewall 485, stall 380, chain 241, recurve 127, erratic 104.
- **Erratic storms are thin and unreliable.**
  - Only 20 train storms are erratic from formation.
  - In the holdout, storms erratic from formation averaged 35 ± 9 (n = 15), against 159-175 predicted.
  - Treat erratic as highly uncertain.

### 2.5 Insurer layers

**Laws.**
- HAVEN = 75 − 0.06·clip(L − 400, 0, 900).
  - Retention 400, top 1300, both identified to about 1e-6 in slope.
  - The retention is pinned by extrapolation: no season has L between 392 and 434.
- REIN = 90 − 0.035·max(L − 1300, 0).
  - The layer top is **not identifiable** (no research season has L > 1,766) [Ask].
- HAVEN and REIN can move in opposite directions on the same storm. Only where the season total lands matters.

**Round-3 open expected values**, from the insurance verification (depletion model G). Use them as a sanity check on the engine:

| L after round 2 ($M) | 0 | 300 | 600 | 900 | 1000 | 1100 | 1200 | 1300 | 1400 | 1500 |
|---|---|---|---|---|---|---|---|---|---|---|
| E[HAVEN] | 67.04 | 59.54 | 46.21 | 31.89 | 28.04 | 24.54 | 21.96 | 21 | 21 | 21 |
| E[REIN] | 90.00 | 89.98 | 89.70 | 89.53 | 88.97 | 88.19 | 86.89 | 84.70 | 82.07 | 79.67 |

**Round-1 open:**
- E[HAVEN] is 39.88-40.49 and E[REIN] 88.90-88.94, depending on the depletion model.
- The live starting prices are HAVEN 39.55 and REIN 89.36.

### 2.6 Rebuilders

| | Driver | When it is known | Note |
|---|---|---|---|
| RECON | X = repair work on PORT, GRID and FERRY ($M) | Exact after each final report (740 s) | The preliminary report does not beat the house: RMSE 0.70-0.74 vs 0.43 |
| MAT | L | Exact after each final report | Preliminary vs house: 0.35-0.36 vs 0.20 |
| GEN | O = GRID-substation closure | **Exact at storm end (540 s)** in round 3. The engine leads the house from about 160-220 s of storm age | Engine (`tickprob_impact`): RMSE 0.020, max 0.099. Continuous-time integration: 0.022 / 0.082 |

- The rebuilders are concave: each extra dollar of damage adds less value.
- The house's open bias on rebuilders is not significant out of sample:
  - holdout house − settle is RECON +0.21 / +0.17 / +0.20 by round (t ≤ 1.1);
  - MAT is under +0.09.
  - Do not lean short.

### 2.7 Preliminary report

**Noise model.**
- prelim = final·exp(0.30·z), rounded to 0.1, with z ~ N(0, 1).
  - The train log-sd is 0.3003; holdout per company it is 0.307-0.324.
- The noise sits on the preliminary report, not the final:
  - corr(log p/f, log f) is between −0.09 and +0.07;
  - |corr| < 0.1 across companies.
- final = 0 if and only if prelim = 0 (869 of 869 PORT cases).

**How the engine uses it.**
- The engine computes the posterior of m given the track hazard and the preliminary value, using the interval likelihood of a value rounded to 0.1.
- After the report it cuts PORT error from 5.33 to 4.39 pp at storm end.
- Raw prelim gives 9.84 pp per storm; shrunk prelim gives 8.79.
- **Fallback** if the engine is down: E[final | prelim] ≈ shrink × prelim, with shrink = PORT 0.868, ISLE 0.845, AQUA 0.891, GRID 0.875, FERRY 0.853.

### 2.8 Storm dynamics, patterns and sources

| Item | Value | Accuracy and notes |
|---|---|---|
| Formation | Real 60 s at x0 ~ U(380, 620), y = 740, r = 80, I = 1.0, vx = 0, vy = −1.8. Only x0 varies between storms | – |
| Intensity | Increments sd 0.0293 per 2 s, mean-reverting (inc = 0.0143 − 0.0117·I, level ~1.22). Decays linearly to 0 over ages 420-480 (real 480-540) | – |
| Patterns (1,800 storms) | direct 450, chain 409, recurve 373, stall 272, eyewall 261, erratic 35. 114 turned erratic mid-storm | Turn hazard 1.35e-4 per s (train) |
| Official advisory | Ages 30, 90, …, 450 (real 90, 150, …, 510). Outlook right **69.9% train / 69.1% holdout**, flat across advisories 1-8; wrong labels are uniform | `hurricane_conditions` is calibrated: Brier 0.030, AUC 0.990 |
| Cone | – | Median error 11.8 / 20.7 / 32.6 / 52.1 units at leads 30 / 60 / 120 / 240 s |
| Wire | About 10 per storm. Right **39.6% / 39.8%** (chance 17%). Does not copy the officials | – |
| Pattern classifier (`pattern.PatchedPosterior`) | Track + cone + labels + hazard, with a 20-s recent-turn floor (ε = 0.0027) | Holdout log-loss **0.1489**, accuracy **94.75%**. P(true) ≥ 0.99 at median age 90 s. Turns detected 37 of 40, median delay 33 s; false alarms 0.13% |

- Use the **patched** posterior.
- The unpatched one silently drops the track score after any feed gap covering age 90: log-loss 0.218 against 0.159.

### 2.9 The fair-value engine

**What it computes.**
- `work/se/engine/fairvalue.py` returns `{symbol: (mean, sd)}` for all 12 symbols.
- It is a Monte Carlo expectation of the settlement map over:
  - **the exact prefix:** finished rounds, closure so far, and per-site remaining value solved from each final report;
  - **analog paths for the rest of the live storm:** 30 library storms per steering class, weighted by the pattern posterior and translated onto the live storm;
  - **lognormal multipliers m:** from the prior, or from the posterior given the preliminary report;
  - **library storms for future rounds,** with site-level depletion.
- Convex payoffs (the layers and the concave rebuilders) are therefore valued by expectation, not by plugging in a point estimate.

**Accuracy against the house, round 3**, holdout RMSE vs settlement ($), engine / house:

| Real s | PORT | ISLE | GRID | FERRY | GEN | HAVEN | REIN | ARCH | REBLD |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 11.05/11.15 | 3.66/3.67 | 2.50/2.52 | 1.91/1.93 | 1.39/1.40 | 10.95/11.05 | 2.00/2.05 | 24.97/25.21 | 8.29/8.43 |
| 120 | **6.91**/8.03 | 2.17/2.32 | 1.62/1.96 | 1.25/1.40 | 0.89/1.07 | **7.09**/8.54 | 1.33/1.48 | 15.94/19.38 | 4.99/6.24 |
| 240 | **4.47**/5.68 | 1.18/1.39 | 1.05/1.40 | 0.57/0.76 | 0.59/0.81 | **4.60**/6.23 | 1.14/1.12 | 10.23/14.36 | 3.09/4.66 |
| 360 | **2.82**/4.32 | 0.74/0.88 | 0.66/0.95 | 0.25/0.42 | 0.28/0.55 | **2.68**/4.56 | 0.92/0.95 | 5.77/10.07 | 1.71/3.32 |
| 540 | 2.47/2.57 | 0.72/0.75 | 0.52/0.54 | 0.13/0.13 | 0.02/0.06 | 2.20/2.26 | 0.89/0.97 | 4.79/4.94 | 1.35/1.44 |
| 600 | 2.18/2.18 | 0.58/0.57 | 0.51/0.51 | 0.11/0.11 | 0.02/0.06 | 1.99/1.98 | 0.77/0.80 | 4.33/4.34 | 1.22/1.25 |
| 740 | **0.01**/2.21 | 0.00/0.58 | 0.01/0.51 | 0.01/0.11 | 0.02/0.06 | **0.00**/1.97 | 0.00/0.78 | 0.03/4.32 | 0.02/1.25 |

**Other properties:**
- **Rounds 1-2.** The same pattern holds, but future storms dominate, so the gaps are smaller. At 240 s PORT is 15.96/16.84 in round 1 and 12.18/13.01 in round 2; HAVEN is 14.36/15.12 and 11.70/12.54.
- **Live-storm damage predictor.** Holdout RMSE of E[final damage] at storm age 30 / 120 / 240 / 360 s:
  - PORT 16.6 / 12.1 / 9.3 / 5.7 pp;
  - loss $180M / $135M / $100M / $60M.
  - It reaches the physics noise floor by age 420.
- **Calibration.** z-score sd is 0.93-0.98 (REIN 0.80, conservative). Coverage of the 90% interval is 0.917-0.949.
- **Monte Carlo noise.** Standard error ≤ 0.11 (HAVEN in a round-1 storm), ≤ 0.02 in round 3.
- **Speed.** Standard parameters take 32 ms median per call (p95 127 ms). `engine_params_fast.json` takes 11 ms median (p95 46 ms) with the same accuracy to within ±0.1 RMSE. A call right after the round-3 final report takes 29 ms [D] `dp_checks/t_final.py`.
- **Known weaknesses:**
  - erratic analogs are thin;
  - round-3 storm loss is slightly over-predicted (272 vs 258, about 1.5-2 standard errors);
  - the REIN layer top is assumed absent;
  - feed gaps degrade closure (§2.2);
  - mid-storm restarts need `persist_path`.

---

## 3. What the data says about the house

### 3.1 Reaction timing

[RT] `rt_house.out` F, all 600 seasons. Share of the +16 s move already in the price, by seconds after the report:

| | +0 s | +2 s | +4 s | +6 s | +8 s | +10 s | +12 s | +14 s |
|---|---|---|---|---|---|---|---|---|
| Stocks (prelim and final, all rounds) | 0.00 | 0.02-0.03 | **0.87-0.90** | 0.89-0.92 | 0.91-0.94 | 0.93-0.95 | 0.95-0.97 | 0.97-0.98 |
| ETFs | 0.00 | 0.01-0.02 | 0.02-0.06 | 0.03-0.06 | 0.04-0.09 | **0.92-0.96** | 0.95-0.97 | 0.97-0.98 |

**Rules that follow:**
- **Reaction row.** A message at real t moves stocks at row ⌊t/2⌋+2 for even t and ⌊t/2⌋+3 for odd t. The row stamped with the message time still shows the pre-news price.
- **Formation is the same.** The storm forms at 60 s; stocks reprice at row 32 (64 s) and ETFs at about row 35 (70 s). Rows 29-31 are unchanged (`rt_timing.out`).
- **Schedule.** Event times are identical in all 1,800 storms:
  - formed 60 s (10:00);
  - dissipated 540 s (14:00);
  - preliminary report 570 s (14:15);
  - final report 740 s (15:40).
- **Announcements.** The packet says the house needs about 45 s to absorb one [P]. There is no data.
- **[testnet] The book is slower than the consensus.**
  - At reports, for stock moves of at least 1%, the book has done 20% of its move at +4 s, 36% at +6 s and 91% at +8 s. The consensus has done 99% by +4 s.
  - At formation, the book is half done at a median 72.0 s for stocks (range 66.0-89.6 s) and 77.3 s for ETFs.
  - Sources: `verify/v05_reports.out`, `writer/w06_t50.out`.

### 3.2 The formation jump and the in-storm drift

These are clock-predictable patterns that need no engine. Mean % move of the house consensus, in three 200-season blocks [RT] `rt_house.out` C:

| Symbol | Jump, rows 29→37 (58 → 74 s) | Drift, rows 37→269 (74 → 538 s) |
|---|---|---|
| HAVEN | +9.6 / +10.2 / +9.7 | −7.3 / −6.8 / −7.9 |
| ARCH | +3.2 / +3.4 / +3.3 | −2.8 / −2.5 / −3.0 |
| FERRY | +3.1 / +3.2 / +3.0 | −3.1 / −2.7 / −2.9 |
| GRID | +2.7 / +2.8 / +2.6 | −2.4 / −2.3 / −2.6 |
| PORT | +2.5 / +2.7 / +2.6 | −2.0 / −2.2 / −2.8 |
| AQUA | +1.9 / +2.1 / +1.9 | −2.3 / −1.6 / −1.5 |
| ISLE | +1.5 / +1.6 / +1.4 | −1.6 / −1.2 / −1.1 |
| GEN | −1.4 / −1.5 / −1.4 | +1.3 / +1.2 / +1.3 |
| RECON | −1.3 / −1.4 / −1.3 | +0.9 / +0.9 / +1.2 |
| REBLD | −1.2 / −1.3 / −1.3 | +0.9 / +0.9 / +1.2 |
| MAT | −1.1 / −1.2 / −1.1 | +1.0 / +0.9 / +1.0 |
| REIN | +0.19 / +0.21 / +0.13 | −0.3 / −0.3 / −0.3 |

**Reading the table:**
- Stable in all three blocks.
- The jump does not predict the later drift: holdout R² is about 0 for every symbol. The drift is a constant bias, not a reversal of each storm's own jump.
- Formation-jump size per storm depends on x0 and on the loss already known. The verifier's model J% = b1 + bu·u + bu2·u² + bLk·Lk + bLk2·Lk², with u = (x0 − 500)/100 and Lk in $bn, has holdout R² from 0.03 (ISLE) to 0.47 (PORT). Coefficients are in `fits/house.verified.json`.
- **Size from the mean jump.** x0 is only known at 60 s, after our 56 s entry, so H1 sizes off the mean jump.

### 3.3 Where the house is as good as a model, and where it is not

**Where they tie.** Engine and house are within 1-3% RMSE:
- **at round opens.** Round-3 open PORT is 11.05 vs 11.15.
- **at storm end.** At 540 s PORT is 2.47 vs 2.57.
- **once the house has absorbed the preliminary report.** At 600 s both are 2.18.
- Between 600 and 740 s there is no fundamental edge.

**Where the engine wins: mid-storm.**
- At 120-360 s of round 3, the engine-minus-house sign rule earns:

  | Symbol | $/share | t-stat |
  |---|---|---|
  | PORT | +1.9 to +2.5 | 4.7-6.9 |
  | HAVEN | +2.3 to +3.2 | 5.4-8.3 |
  | GRID | +0.6 to +0.9 | 6-11 |
  | ARCH | +5.7 to +8.2 | 6-10 |

- A clock correction fitted on train closes only part of the gap. At 240 s, round-3 PORT is 4.47 (engine), 5.68 (house) and 5.44 (clock-corrected house); HAVEN is 4.60, 6.23 and 5.62 [RT] `rt_evh.out`.
- Mid-storm edge is therefore the engine plus the clock bias, not the clock bias alone.

**After the round-3 final report.** Settlement is known. The house stays stale until 744 s (stocks) and 750 s (ETFs).
- Oracle value of the trade, holdout:
  - **filled at 742 s:** $1,387 at 0 bp, $1,206 at 10 bp, $1,025 at 25 bp;
  - **filled at 744 s:** $415, $255 and $153.
- **[testnet] On the recorded books the stale window is longer than on the consensus.**
  - A sweeper starting 1 s after the final report found at least $924 (224 r3) and $927 (225 r3). The value including re-posted stale quotes is up to $1,250 and $1,386.
  - Other participants took $934 and $2,307 (`verify/v05_reports.out`).

### 3.4 ETFs: the consensus is the basket 6 s earlier

[RT] `rt_house.out` A and `rt_etf.out`.

**The lag.**
- ETF_i − NAV_{i−3}, where NAV is the basket of house stock consensus 3 rows earlier:
  - ARCH: RMS $0.476 (about 16 bp), max $2.69;
  - REBLD: RMS $0.380 (about 18 bp), max $1.91.
- At lag 2 or 4 rows the RMS jumps to 1.27 (ARCH) and 0.54 (REBLD), with maxima of $35 and $11. The lag is exactly 3 rows.
- The residual is the ETF's own persistent quote noise: AR(1) coefficient 0.85, mean about 0, identical before, during and after the storm.

**What it means.**
- The house never reprices an ETF from the news directly. It reprices from its own stock consensus.
- On the consensus the edge is close to deterministic.
- It pays live only if the **ETF book** lags too; this is untested (§4.5).
  - **[testnet] Tested: the ETF book lags its basket book by 7 s.**
    - Correlation of 2-s changes peaks at 7 s: ARCH +0.49, REBLD +0.39.
    - The RMS gap is smallest at a 7-s lag: 32 bp for ARCH, 28 bp for REBLD.
    - The ARCH touch is through NAV by more than 50 bp in 7.9% of 60-776 s (`writer/w01_checks.out` E).
- Create and redeem are free and immediate, but both legs are booked at marks, so a conversion has zero P&L by construction [I]. It only recycles limit room.

### 3.5 Quote noise

[RT] `rt_noise.out`, `rt_house.out` B.
- **Quiet phases:** stationary AR(1) noise, 8.3 bp per 2 s, saturating near 20 bp after 40 s (σ ≈ 14 bp, half-life ≈ 7 s). The lag-1 autocorrelation of 2-s changes is −0.05 to −0.11.
- Fills at the next row do not select on the noise.
- **Storm phase:** total 2-s RMS change, information plus noise:

  | HAVEN | PORT | FERRY | ARCH | AQUA | ISLE | GRID | RECON | REBLD | GEN | MAT | REIN |
  |---|---|---|---|---|---|---|---|---|---|---|---|
  | 129 bp | 76 | 53 | 50 | 43 | 37 | 34 | 28 | 24 | 20 | 18 | 12 |

### 3.6 Biases at the open and hard bounds

**Biases.**
- **REIN is the only stable open bias.**
  - All 600 seasons: the house REIN open is too high by $0.38 in round 1 and $0.33 in round 2 (t −3.4 and −3.1). It is not significant in round 3.
  - Worth about $50-75 per season at full limit, with sd around $590. Not traded (§4.8).
- **HAVEN open bias:** +2.44 on train but +0.54 on holdout (t 0.5). Not usable.
- **PORT, MAT, ARCH:** no significant holdout bias.
- **The open carries no signal.** It is uncorrelated with settlement (HAVEN −0.009, PORT −0.004), so the live opens (HAVEN 39.55 vs research mean 38.14) say nothing.

**Hard bounds.**
- REIN ≤ 90 always.
- The house consensus prints above 90.02 at some point in 86-88% of seasons.
- Selling 200 at the best of those prints is worth at most $119-130 per season.

### 3.7 How the house moves at reports

Used for the live law check in §5. Earlier playbook, `v_snipe.py`, fit 1-400 and test 401-600; not re-run by the red team.

**The model.**
- Predicted house move = slope × (law sensitivity × x), where:
  - for the preliminary report, x = E[D | track, prelim] − E[D | track];
  - for the final report, x = final − E[D | track, prelim].

| Symbol | Prelim slope, r1 / r2 / r3 | Final slope, r1 / r2 | Test R² (prelim / final) |
|---|---|---|---|
| PORT | 0.66 / 0.82 / 1.03 | 0.65 / 0.80 | 0.96 / 0.98 |
| ISLE | 0.76 / 0.86 / 1.00 | 0.76 / 0.87 | 0.95 / 0.97 |
| AQUA | 0.71 / 0.79 / 0.97 | 0.72 / 0.80 | 0.92 / 0.92 |
| GRID | 0.63 / 0.74 / 0.93 | 0.65 / 0.80 | 0.93 / 0.96 |
| FERRY | 0.71 / 0.84 / 0.97 | 0.72 / 0.86 | 0.88 / 0.92 |
| RECON | 0.47 / 0.68 / 1.01 | 0.46 / 0.69 | 0.81 / 0.87 |
| MAT | 0.42 / 0.65 / 1.01 | 0.43 / 0.65 | 0.72 / 0.81 |
| HAVEN | 0.49 / 0.59 / 0.82 | 0.48 / 0.59 | 0.79 / 0.82 |

**Reading the slopes.**
- Round 3 slopes are about 1: the house prices the report through the laws exactly.
- Rounds 1-2 slopes are below 1 because future storms will hit what is left.

### 3.8 What does not beat the house

| Idea | Evidence | Verdict |
|---|---|---|
| Fade wire rumours | Loses at every threshold and spread: −$0.8k to −$27.8k per season (backtest S5). The house's moves after rumours continue | NO |
| Trade toward the posterior after 600 s, or at storm end | The engine equals the house at 600 s; holding to the posterior from storm end earns $139-310 (earlier playbook) | NO |
| Hold insurers at the round opens | HAVEN 155 / 302 / 223 by round (t 0.5-1.2); REIN 65 / 51 (t 1.6 / 1.4); sd $2.6-4.1k per leg [RT] | NO |
| Report snipes without a latency edge | The +2 s fill bound is $1.0k at 10 bp and $0.5k at 25 bp for all six reports [RT] | Race only (§4.6) |

---

## 4. Strategies, ranked by corrected edge

### 4.0 Ranking

Marginal values come from switching each part on or off inside the Plan (RT fill model, h = 0.25%, holdout [train]) [D] `dp_bt3.out`.

| # | Strategy | Verdict | Corrected edge | Main risk |
|---|---|---|---|---|
| 1 | **S3 mid-storm engine taker** (80-540 s) | **GO, core.** [testnet] GO with L/12 clips and the measured-cost threshold, but **unproven on books**: score it on fills in the next testnet season at 25% size | The Plan without H1 (S3 + S1-r3) is **17.2k [16.2k]**. Survives +2 s latency on every order (Plan 20.3k → 18.6k; S3 component 19.0k → 17.2k) and wider storm spreads (19.7k). [testnet] With measured costs, S3 + S1-r3 is 12.5k [12.0k]. On season-225 books S3 alone made −3.8k against +3.6k on the consensus; round 3 was +6.8k on books | Engine bug (the snapshot reset, §9.1); depth worse than γ (confirmed); crowding |
| 2 | **H1 formation leg** (50-56 s in, 72-78 s out) | **TEST-FIRST, then GO.** ½ size in round 1; full in rounds 2-3 if the round-1 check passes; off at h ≥ 1%. [testnet] Same verdict, but **no exit before 80 s** (S3 inherits the book), the crowding skip dropped, and the round-1 check redefined (§4.2) | **+3.1k [3.3k] at ½ size, +7.5k [7.7k] at full.** About 0 at h = 1%. Single-shot at 60 s: −0.7k with +2 s. [testnet] On season-225 books, the playbook timing made +65 (full size) against +7.4k on the consensus. With measured costs and no forced exit, H1 is worth 19.8k vs 12.5k without it (consensus) | The most visible edge, so the most exposed to crowding. [testnet] Others net-bought 1-2 L of HAVEN and ARCH in 0-50 s |
| 3 | ETF legs inside S3 and H1 | GO (default) | +3.5k [3.4k] (the Plan without ETFs is 16.8k). [testnet] With measured costs: 19.8k with ETFs vs 15.9k without | – |
| 3b | **ETF lead-lag** in place of the ETF legs | **TEST-FIRST** (book-lag check, §7). [testnet] Book lag confirmed (7 s), but still **TEST-FIRST, default OFF**; capped entries only (§4.5) | At 25 bp, estimated +4.3k on time and +1.3k one row (2 s) late. Two rows (4 s) late it loses 2.7-4.4k standalone [I, additive]. [testnet] On books over 4 rounds: +2.7k to +3.7k with capped entries, −6.1k to −9.7k walking the book | The book may not lag; other teams arbitrage it |
| 4 | **S1-r3 settlement positioning** (740-778 s) | **GO.** [testnet] GO, keep the taker; sanity check at ≥ 756 s | 1.0k if we fill first, 0.15k at +2 s (25 bp oracle). Downside ≈ 0 with limit orders at S ∓ ε. [testnet] On books: at least $0.92k per round 3 from +1 s (both recorded round 3s) | Announcements; wrong S |
| 5 | **REIN ask** ≥ 90.02 | **GO** (trivial) | $50-130 | A REIN or insurance announcement |
| 6 | Report snipes: S2 (prelim) and S1 in rounds 1-2 | **TEST-FIRST, default OFF.** [testnet] Unchanged | In the Plan: −0.2k [0.0k] even when we win the race, −1.2k [−0.9k] when we lose it. Oracle bound $5.6k at 10 bp only if filled within 2 s. [testnet] The book is stale 6-8 s after a report, but in season 225 rounds 1-2 there was only $0-12 of stale stock value per report | A pure latency race |
| 7 | Clock-only (no engine): H1 + drift short 74-532 s | **Fallback only** (engine down). [testnet] Unchanged | 16.9k [15.6k], p5 −8.4k (worked, full H1). 19.2k single shot; 0.6k under harsh single-shot fills. [testnet] Season 225 drifted against the research mean: the drift trade lost 39.5k on books and 22.7k on the consensus; no evidence either way | High variance |
| – | S4 insurer opens; S5 wire fade; passive quoting; aftermath holds; advisory momentum | **NO-GO** | See §3.8 | – |

### 4.1 S3: mid-storm engine taker (GO)

**Window and inputs.**
- Window: real **80-540 s** (market 10:10-14:00), every 2 s and on every news batch.
- Inputs: engine fair value (μ, sd); our book's bid, ask and mid; limit L.

**Rules:**
1. **Enter.** Set the target to +L if μ − ask > k·sd + γ·mid; set it to −L if bid − μ > k·sd + γ·mid. Take full size as soon as the threshold is crossed: proportional sizing costs about 35% (backtest).
   - **[testnet] Replace γ·mid with the measured cost term:** 2.0 × (walk cost of one L/12 clip in the current phase + 36 bp follow-on allowance; 17 bp for ETFs).
   - In the storm that is 107-138 bp for stocks (HAVEN 138, PORT 123) and 69-73 bp for ETFs. At 80-120 s it is 138-342 bp. Bot units are in `TESTNET_FINDINGS.md` §6 (`writer/w05_params.out`).
2. **Exit to flat.** Close a long when μ < bid, and a short when μ > ask. Otherwise hold.
3. **Work the order.** Move at most **¼·L per symbol per 2 s**. **[testnet] Now L/12 per symbol per 2 s:** PORT 16, ISLE 25, AQUA 41, GRID 33, FERRY 66, RECON 41, MAT 58, GEN 50, HAVEN 20, ARCH 4, REBLD 8. Use a marketable limit at:
   - min(μ − k·sd − γ·mid, ask × 1.005) for buys;
   - max(μ + k·sd + γ·mid, bid × 0.995) for sells.
   - **[testnet]** Use the cost term from rule 1 in place of γ·mid. Never price beyond mid ± 1.5%: the 3-L wall sits about 8% from mid.

   Cancel any unfilled remainder before the next clip.
4. **Parameters.** Choose k from the measured half-spread (table below). Start with γ = 0.5% and replace it with the testnet estimate.
   - **[testnet] Measured:** with the measured-cost threshold, keep k = 0.1 in rounds 1-2 and 0.2 in round 3 (re-optimised on train).
   - Re-measure the costs from round-1 fills and scale the tables if they differ.
5. **Symbols.** Trade all except REIN, and except ARCH and REBLD if lead-lag is switched on. Skip any symbol that is frozen by an announcement or whose engine value is more than 6 s old.
6. **End of round.**
   - Rounds 1-2: flatten everything over **562-568 s** in ¼ clips, so the bot is flat through both reports and overnight. **[testnet] Flatten over 540-568 s in L/12 clips instead.**
   - Round 3: hold to settlement; S1-r3 adjusts at 740 s.
7. **[testnet] Take over the H1 book at 80 s.** There is no forced H1 exit any more (§4.2): S3's targets start from the H1 position.
8. **[testnet] Scoring.** S3 is unproven on books: in season 225 it made −3.8k on books against +3.6k on the consensus. Log its P&L on our own fills from the first testnet season at 25% size. If it is negative on fills in rounds 1-2 again, restrict S3 to round 3 [I]. The book evidence there is positive: 225 r3 made +6.8k, and in the round-3 sign rule (both recorded round 3s) each signal earns +304 bp on book prices.

**Why flatten in rounds 1-2.**
- Flattening and holding give the same mean: 20.3k vs 20.2k, train 19.5k vs 19.2k [D] `dp_bt2.out`.
- Flat removes report-time spread risk, announcement risk during the pauses, and order state across pauses.

**k by measured half-spread** (train-selected; `fits/backtest.json`):

| Measured half-spread h | k, rounds 1-2 | k, round 3 | H1 | Plan holdout mean |
|---|---|---|---|---|
| ≤ 0.15% | 0.0 | 0.1 | on | 22.8k (h = 0.1%) |
| 0.15-0.35% | 0.1 | 0.2 | on | 20.3k (h = 0.25%) |
| 0.35-0.75% | 0.1 | 0.5 | on | 16.1k (h = 0.5%) |
| ≥ 0.75% | 0.2 | 0.5 | **off** | 9.3-9.6k (h = 1%) |

**[testnet] This table applies to the old RT fill model.**
- The measured storm half-spread is 12-23 bp, the first row.
- With the measured-cost threshold (rule 1), keep k = 0.1 / 0.2. Use the table only if live costs come in far from the measured tables.

### 4.2 H1: formation leg (TEST-FIRST, then GO)

| Item | Rule |
|---|---|
| Direction | Long PORT, ISLE, AQUA, GRID, FERRY, HAVEN, ARCH. Short RECON, MAT, GEN, REBLD. REIN is out (break-even only 9 bp) |
| Entry | Real 50, 52, 54, 56 s (market 09:55:00-09:58:00), keyed on the market clock of each round. Clips of ¼·L at the touch, capped at the 48-s mid × (1 ± 0.5%). **[testnet] Keep the cap.** It binds on the books (r2: AQUA filled 173 of 500; r3: PORT 86 of 200), and it protects: the books report's replay without the cap lost 2.7k |
| Size | Round 1: **½·L**. Rounds 2-3: **full L** if the round-1 check passes; otherwise ½ or off. **[testnet] Unchanged.** On the consensus with measured costs, full from round 1 earns 19.8k but has p5 −0.8k; half size earns 16.7k with p5 +1.6k. On books H1 is unproven. Drop the RECON, MAT, GEN and REBLD legs if round-1 costs are ≥ 1.5× the tables |
| Hold | Through formation (60 s) and the house reprice (stocks 64 s, ETFs about 70 s). Never enter on the `storm_formed` message itself: that is a race worth −0.7k at +2 s. **[testnet] The book reprices later:** half done at a median 72.0 s for stocks (all 27 stock symbol-rounds by 89.6 s) and 77.3 s for ETFs |
| Exit | Real 72, 74, 76, 78 s (10:06-10:09): ¼·L clips to flat. S3 takes over from 80 s. **[testnet] No forced exit: S3 inherits the H1 book at 80 s.** If the engine is not live by 76 s, exit over 84-98 s in L/8 clips [I]. The 72-78 s exit sold into stale bids: +65 on season-225 books vs +7.4k on the consensus |
| Per-symbol skip | Skip a symbol when its mean jump (§3.2) is below 2 × (measured half-spread + impact of a ¼ clip). This is the house verifier's rule. At h = 0.25% and γ = 0.5% the cost is 0.375% a side, so every symbol except REIN passes |
| Crowding skip [I] | At 48 s, if HAVEN or ARCH has already moved more than ⅓ of its mean jump in our direction since 30 s, someone is front-running. Skip H1 for that round. **[testnet] Dropped.** It never fired on the books (30→48 s moves ≤ 0.21%), although others net-bought 1-2 L of HAVEN and ARCH in 0-50 s. Round 1 was crowded and was still H1's best round on the books |
| Round-1 check [I] | Average round-trip cost per share across H1 symbols, measured against the 48-s and 80-s mids, must be ≤ ½ of the realised 58→74 s jump in HAVEN, PORT and ARCH. Pass: full size in rounds 2-3. **[testnet] Redefined:** the book has barely moved by 74 s (r1: GRID +0.15%, FERRY +0.16%, ARCH +0.26%), so use the 58→90 s book move. Pass if our measured entry cost per share (fill vs 48-s mid) is ≤ ⅓ of that move [I] |

### 4.3 S1-r3: settlement positioning in the last 40 s (GO)

1. **740.0 s (15:40, round 3).** On the final-report message, refresh the engine synchronously (about 30 ms) and take S_s = engine mean.
   - Cross-check S against `se_laws.values(D, L, T)`.
   - Exclude a symbol if the two disagree by more than 0.02 (ISLE, AQUA, RECON, MAT, HAVEN, REIN) or 0.25 (PORT, GRID, FERRY, GEN, ETFs) [I].
2. **Within about 300 ms,** for every symbol that passes the gates:
   - if ask < S − ε, **buy** (L − pos) at limit S − ε;
   - if bid > S + ε, **sell** (L + pos) at limit S + ε.

   That is at most 12 actions at SNIPE priority.
3. **Then rest the other side as well:** a buy for the remaining room at S − ε and a sell at S + ε. Neither can fill at a price worse than settlement − ε. At most 12 more actions.
4. **742-778 s:** top up partial fills every 2 s. **[testnet] Sweep every 0.5-1 s until 750 s, then every 2 s.** The book stays stale 6-8 s and the house re-posts stale quotes. A sweeper from +1 s found at least $924 and $927 in the two recorded round 3s.
   - **[testnet] Keep the taker.** The re-backtest's advice to drop it (−$15) came from filling on the consensus after the consensus had repriced.
5. **746 s sanity check [I]:** if a stock's mid differs from S by more than max(3ε, 0.5%) after the house reprice, cancel that symbol and alert. **[testnet] Move it to ≥ 756 s.**
   - At 746 s it would have cancelled PORT, HAVEN and ARCH, the profitable names, in both recorded round 3s.
   - At 752 s it still cancels ARCH in 224 r3. At 756 s it cancels nothing (`verify/v05_reports.out`).
6. **No flattening.** Leave the orders until the close.

**ε per symbol** (max error over all 600 seasons + 1 cent):

| PORT | ISLE | AQUA | GRID | FERRY | RECON | MAT | GEN | HAVEN | REIN | ARCH | REBLD |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.07 | 0.02 | 0.02 | 0.06 | 0.09 | 0.02 | 0.02 | 0.11 | 0.02 | 0.02 | 0.17 | 0.12 |

If any storm of the season had a storm-phase feed gap longer than 10 s, use ε = 0.25 for PORT, GRID, FERRY, GEN, ARCH and REBLD. Unobserved intensity costs 0.10-0.17 (§2.2). **[testnet]** The 31.7-s outage at 225 r2 221-253 s would have triggered this. Count only gaps that neither connection filled.

**Gates.** All must hold:
1. The startup diff of `/cases/hurricane.json` is clean for that company.
2. No unresolved announcement touches the symbol, its ETF, or a linked name (§5.2).
3. The rounds 1-2 live law check passed (§5.1).

Any symbol that fails is excluded. For it, trade only gaps of more than 1% against the house price at 746 s, by hand.

### 4.4 REIN riskless ask (GO)

- **Order.** Rest a sell for all REIN sell room at U + 0.02, where U = 90 − 0.035·max(L_known − 1300, 0). L only rises, so REIN can never settle above U.
- **When.** Post at the open of each round, or re-post if resting orders do not survive pauses [Ask].
- **Cancel** on any announcement naming REIN, HAVEN, or anything that moves L.
- **Cancel at 740 s of round 3.** S1-r3 then trades REIN against the exact S. The resting ask would otherwise take REIN's sell room.
- **Value:** $50-130 per season.
- It uses REIN's whole sell room, which is why REIN is excluded from H1 and S3. Its value there is −$15.

### 4.5 ETF lead-lag (TEST-FIRST)

**Gate (all four):**
- the testnet recording (§7) shows the ARCH book still stale at 66-68 s with size, at the same lag as the consensus;
- the ETF half-spread is ≤ 25 bp;
- report-to-acknowledgement latency is ≤ 2 s;
- price protection does not reject orders priced at the stale ETF level.

**[testnet] Gate results from the 23 Oct recording** (`writer/w01_checks.out` E-F2):
- **Book lag: yes, 7 s,** against 6 s on the consensus.
  - At 66 s the ETF book is not stale yet, because the component books lag too.
  - At 68-80 s ARCH is stale: NAV − ETF was +0.7% to +6.5% in 225 r1, −0.6% to +2.7% in r2 and −2.4% to −0.4% in r3, with a median 1.6-2.2 limits priced better than NAV ∓ 0.3%.
- **Half-spread: passes.** Storm median 15.8 bp (ARCH) and 15.5 bp (REBLD).
- **Latency and price protection: untested.** See the 1-lot plan in `TESTNET_FINDINGS.md` §7.
- **Verdict: TEST-FIRST, default OFF.**
  - On the books the rule below makes +2.7k to +3.7k over 4 rounds only if entries are capped. Walking the book it loses 6.1k to 9.7k.
  - The ETF legs inside S3 and H1 are worth 19.8k with ETFs vs 15.9k without, on the consensus. So do not swap them.
  - A formation-only ETF rule would collide with H1's ARCH leg, which already holds the limit.

**Rules (as backtested):**
- **Signal:** gap = (NAV − ETF mid)/ETF mid, where NAV = Σ basket weight × live stock mid.
- **Enter** toward ±limit (ARCH 50, REBLD 100) when |gap| > max(0.5%, 3 × ETF half-spread). **[testnet] Enter only with a marketable limit capped at mid × (1 + gap/2), accepting partial fills. Never walk the book.**
- **Exit** at the first of: |gap| < 0.1%; 8 s elapsed; the gap changes sign.
- When it is on, **remove ARCH and REBLD from H1 and S3.** Run one ETF engine, not two.

**Consensus results** [RT] `rt_etf.out`, threshold 0.5%:

| ETF half-spread | Fill latency | ARCH | REBLD | Total |
|---|---|---|---|---|
| 10 bp | on time (row +1) | 8,682 | 3,658 | 12.3k |
| 10 bp | +2 s | 5,694 | 2,482 | 8.2k |
| 25 bp | on time | 5,927 | 1,956 | 7.9k |
| 25 bp | +2 s | 3,720 | 1,156 | 4.9k |
| 25 bp | +4 s | −2,719 | −1,643 | −4.4k |
| 40 bp | on time | 4,250 | 1,105 | 5.4k |

Train matches within 1%. The incremental value over the Plan is ETF lead-lag minus the 3.5k the ETF legs earn inside S3 and H1.

### 4.6 Report snipes, S2 and S1 in rounds 1-2 (TEST-FIRST, default OFF)

**Switch on only if both hold:**
- measured message-to-acknowledgement latency is p95 < 1.5 s at 570 and 740;
- testnet fills show we take the pre-report price.

**Rule if switched on** (earlier playbook):
- predicted move = slope (§3.7) × law sensitivity × x;
- trade where |move| > max(0.3% of price, one spread), at the stale touch + up to 50% of the move;
- exit stocks 580-586 s (prelim) or 750-760 s (final).

**Value:**
- The oracle bound at 10 bp is $5.6k when filled 2 s after the report and $1.0k when filled 4 s after.
- Inside the Plan with worked clips and depth cost it adds nothing: −0.2k if we win the race, −1.2k if we lose it.
- **[testnet] The book is stale for 6-8 s after a report, but the value was small this season.**
  - In season 225 rounds 1-2 the reports moved prices less than 1%, leaving $0-12 of stale stock value per report.
  - Only 225 r3's preliminary report had value: $263-352 in stocks at +1-2 s (`writer/w01_checks.out` I).
  - Still OFF until the 1-lot latency test (T5).

### 4.7 Fallback: clock-only (engine down)

- **When.** Use it if the engine crashes and cannot be restored within one round.
- **Trades.**
  - H1 as in §4.2, at full size.
  - At 74-88 s, flip in ¼ clips to short PORT, ISLE, AQUA, GRID, FERRY, HAVEN and ARCH, and long RECON, MAT, GEN and REBLD.
  - Flat over 532-538 s.
- **Value:** 16.9k [15.6k], with p5 −8.4k.
- **Size:** at most ½ in round 3 if season P&L is already positive [I].
- **[testnet] No new evidence on the drift.**
  - Season 225 drifted against the research mean: HAVEN's book rose 26%, 30% and 8% by round.
  - This trade lost 39.5k on the books and 22.7k on the consensus with the RT model (`writer/w01_checks.out` H).
  - It is one season against a 600-season mean, so the verdict is unchanged.

### 4.8 NO-GO

| Strategy | Why |
|---|---|
| S4: insurer or all-symbol trades at the round opens | Holdout t ≤ 1.6, sd $2.6-4.1k per leg. The REIN open bias is real but worth only $50-75 |
| S5: wire fade | Loses everywhere |
| Passive quoting during the storm | The house moves too fast. Taker only |
| Posterior holds in the aftermath | Engine = house after 600 s |
| Advisory momentum or advisory snipes | No edge found; the S3 engine already uses the advisories |
| σ-capped or drawdown-stopped sizing below $20k per round | Lowers the mean without raising p5 (backtest) |

---

## 5. Timeline of a round

Market time = 09:30 + 30 × real seconds. Row i of `market.csv` is real second 2i.

**Roles:**
- **Op:** bot operator.
- **An:** announcement analyst.
- **Risk:** risk monitor.
- **SB:** standby with a second laptop.

### 5.0 Before the open (T−60 min)

1. **Op.**
   - Fetch `GET /cases/hurricane.json` and diff it against the research `case.json`: sites, weights, limits, baskets, timing and company text. Any difference disables S1-r3 for the affected companies until **An** signs off.
   - Start the primary bot in dry-run; check feed, quotes, positions and the clock offset.
   - Log the parameter-file hash, then switch to `--live`.
2. **SB.**
   - Start the standby bot in dry-run on a second network.
   - Count market-data connections; stay under 25.
3. **All.** Confirm the kill switches (k, the KILL file, browser cancel-all).

### 5.1 One round, second by second

| Real s | Market | Row | House / feed | Bot | Humans |
|---|---|---|---|---|---|
| 0 | 09:30 | 0 | Open. Rounds 2-3 open at the previous close. **[testnet] Only the ±8% walls are quoted for up to 2.5 s** | Post or re-post the REIN ask. The book is flat (rounds 2-3 are flat from 568 s of the previous round). The engine values the round open. **[testnet] No marketable orders in 0-3 s** | Op checks positions; Risk notes the round-start MTM |
| 0-48 | 09:30-09:54 | 0-24 | Nothing scheduled | Idle. Record books for the crowding check | – |
| 48 | 09:54 | 24 | – | Crowding check (§4.2); set this round's H1 size | Op confirms H1 on/off on the status line |
| **50-56** | **09:55-09:58** | 25-28 | – | **H1 entry:** 4 clips of ¼·L (½ size: 2 clips) | – |
| 60 | 10:00 | 30 | `storm_formed`, first storm observation | The engine starts on the live storm | – |
| 64 / 70 | 10:02 / 10:05 | 32 / 35 | House stocks jump / ETFs jump. **[testnet] The book touch is half done only at a median 72 s (stocks) and 77 s (ETFs)** | Hold H1 | – |
| **72-78** | **10:06-10:09** | 36-39 | – | **H1 exit:** ¼ clips to flat. **[testnet] No forced exit; hold into 80 s** (fallback exit 84-98 s if the engine is not live by 76 s) | Round 1: Risk computes the H1 round-trip cost (§4.2). **[testnet] Use the redefined check: the 58→90 s book move** |
| **80-540** | **10:10-14:00** | 40-270 | Storm every 2 s; track every 20 s; official advisories at 90, 150, …, 510 s; wire about every 45 s; impacts | **S3** every 2 s and on news. Every storm message feeds the engine; values refresh off-thread. **[testnet] L/12 clips; S3 inherits the H1 book at 80 s; 80-120 s costs 2-7× the storm** | Op watches action rate, rejects and slippage; Risk watches MTM vs the breaker |
| 480-540 | 13:30-14:00 | 240-270 | Intensity decays to 0 | S3 continues. At 540 s closure is final; in round 3 GEN is exact | – |
| **562-568** | **14:11-14:14** | 281-284 | – | **Rounds 1-2: flatten the S3 book** (HEDGE priority). Round 3: hold. **[testnet] Flatten over 540-568 s in L/12 clips** | – |
| 570 | 14:15 | 285 | Preliminary report. Stocks reprice at 574 s, ETFs at 580 s. **[testnet] The book reprices later: 20% done at +4 s, 91% at +8 s** | No trading (S2 off). The engine applies the prelim posterior. Live law check (below) | – |
| 600-740 | 14:30-15:40 | 300-370 | The house now equals the engine | Idle. Round 3: from 680 s block all non-SNIPE actions; pre-warm HTTPS at 737 s | Round 3: An confirms no open announcements |
| **740** | **15:40** | 370 | **Final report.** Stocks reprice at 744 s, ETFs at 750 s. **[testnet] Book stocks are 91% done only at +8 s. Up to 116 frames arrive in the next second** | Rounds 1-2: live law check only. **Round 3: S1-r3** (§4.3, §5.3) | Op watches fills; An is ready to exclude symbols |
| 780 | 16:00 | 390 | Close. Round 3 settles | – | Between rounds: see §5.4 |

**Live law check, rounds 1-2** [I]:
- At 570 s and 740 s, log each stock's realised house move at +16 s against the slope prediction (§3.7).
- If two or more symbols miss in sign, or by more than 50% in size, **An** reviews before round 3. Affected companies are excluded from S1-r3 until cleared.

### 5.2 Announcements (any time, live only)

There is no research data on announcements. The house takes about 45 s to absorb one [P].

1. **Automatic, within 100 ms.**
   - Freeze the named symbols, their ETFs, and the linked names:
     - HAVEN ↔ REIN ↔ MAT (through L);
     - GRID → GEN;
     - PORT, GRID or FERRY → RECON;
     - any island company → HAVEN, REIN and MAT (through L).
   - For frozen symbols: cancel our resting orders, set σ to ∞ (S3, H1 and S1-r3 skip them), sound the alarm, and show the headline.
2. **An, within 10-15 s.** Map the headline to an engine change and write it to `adjust.json`:
   - a price adjustment, e.g. `{"PORT": {"mult": 1, "add": -2.0}}`;
   - or a parameter override, e.g. `{"_overrides": {"settle.insurance.HAVEN.attach": 450}}`.

   The engine applies it to every scenario and rebuilds the ETFs.

   | Category | Parameter |
   |---|---|
   | Layer change | `settle.insurance.*` |
   | Aid or hardening | `settle.island.*` or `physics.k.*` |
   | Rebuilder capacity | `settle.rebuild.*.c` |
3. **Trade toward the override.**
   - Use at most **50% of the limit** while the house adjusts (about 45 s).
   - Stop once the house has covered more than 80% of the distance to our value.
   - After 45 s, unfreeze with the override in place.
4. **In round 3 after 700 s,** an announced symbol enters S1-r3 only if **An** set the override and a second person confirmed it.

### 5.3 The final 40 s of round 3

| Real s | Market | Row | Event | Bot | Humans |
|---|---|---|---|---|---|
| 680 | 15:10 | 340 | – | Block all non-SNIPE actions so the rate window is clean at 740 s. The round-3 S3 book is held | An confirms no open announcement on any symbol; Op checks the action budget and the gates (case diff, law check) |
| 737 | 15:38:30 | – | – | Pre-warm the HTTPS connections (8-12) | – |
| **740.0** | **15:40** | 370 | **Final report** | `refresh_now()` (about 30 ms), giving S for all 12 symbols. Cross-check with `se_laws`; apply the gates. Cancel the REIN ask (1 action). Send the take orders at S ∓ ε (at most 12, SNIPE) **within about 300 ms** | Op watches acks and rejects |
| 740.3-742 | 15:40-15:41 | 370-371 | House still stale | Rest the other side at S − ε (buys) and S + ε (sells) for the remaining room (at most 12) | – |
| 744 | 15:42 | 372 | **Stocks reprice** (consensus). **[testnet] The book is still partly stale until about 748 s** | Top up partial fills every 2 s. **[testnet] Sweep every 0.5-1 s until 750 s** | – |
| 746 | 15:43 | 373 | – | Sanity check: cancel a symbol if \|mid − S\| > max(3ε, 0.5%). **[testnet] Moved to ≥ 756 s: at 746 s it cancels PORT, HAVEN and ARCH on the books** | An decides on excluded symbols; manual trades only on gaps above 1% |
| 750 | 15:45 | 375 | **ETFs reprice** | The ETF stale window (10 s) is the longest; most fills after 742 s are ETFs | – |
| 742-778 | 15:41-15:59 | – | – | Top-ups within the reserved budget. No flattening, no cancels needed | – |
| 780 | 16:00 | 390 | Close; settlement | – | Op records the final positions |

Value:
- Oracle at 25 bp: $1,025 if filled at 742 s, $153 if filled at 744 s.
- **[testnet] On the recorded books:**
  - a sweeper from +1 s found at least $924 (224 r3) and $927 (225 r3);
  - other participants took $934 and $2,307 of value beyond S ± ε.
- An announcement in this window: §5.2, step 4.

### 5.4 Between rounds

- **Op:**
  - reconcile positions in the browser (rounds 1-2 should be flat);
  - check that the engine state is persisted;
  - re-post the REIN ask if it is gone.
- **Risk:** after round 1, decide H1 size for rounds 2-3 (§4.2) and log the realised h and γ against the plan.
  - **[testnet]** Log realised clip costs against the phase tables in `cost_model.json`, and S3 P&L on our own fills.
  - If round-1 costs are ≥ 1.5× the tables, drop the rebuilder H1 legs and scale the S3 cost term.
- **An:** review announcements and overrides.
- **Do not change code.** Only parameters agreed in advance may change: H1 size, γ, and k per the h table.

---

## 6. Risk limits and kill switches

| Control | Setting | Basis |
|---|---|---|
| Position limits | Exchange limits count position + same-side resting orders. Only the REIN ask, S1-r3 orders and clip remainders rest | [P] |
| Order size | ≤ ¼·L per symbol per 2 s, always limit orders, never market orders (price protection is undefined). **[testnet] S3 and flatten: L/12 per 2 s; H1 entry: L/4; never more than ½ L in one clip.** No marketable orders in the first 3 s of a round | [RT] B1; worked fills hold up against depth cost. [testnet] A full-L stock clip costs 304-328 bp (3-L wall). Market orders at the 225 open paid $8,522 |
| Price caps | S3 never pays beyond μ ∓ (k·sd + γ·mid), and never more than ±0.5% through the touch. H1 is capped at the 48-s mid ± 0.5%. Fat-finger band ±25% of mid (±60% for SNIPE). **[testnet] γ·mid becomes the measured cost term (§4.1). No TAKE order beyond mid ± 1.5%. ETF lead-lag entries are capped at mid × (1 + gap/2)** | [I]. [testnet] Wall at about ±8%; uncapped ETF entries lose on the books |
| Action budget | Bot cap **150 actions/min** (orders + cancels), leaving 30 for humans. At a 120/min cap the Plan is unchanged: 20.3k. Cancel remainders per symbol with one cancel-all. **[testnet] Unchanged; the L/12 configuration uses about 1,229 actions per season and reaches 150 in a minute at peaks** | [D], [RT] B8 |
| Priorities | KILL < SNIPE (S1-r3, announcement freeze) < HEDGE (flatten, cancels, REIN ask) < TAKE (S3, H1). Remove the TAKE floor of 100 inside the 550-576 s blackout for HEDGE, or the 562-568 s flatten is blocked | Skeleton `ratelimit.py` |
| Drawdown breaker | **$20k per round** from the round's peak MTM (house mid): NO_NEW_RISK, a human must resume. Not the skeleton's $6k, which costs about $3k per season | Backtest §6 |
| Engine sanity | Disable a symbol if μ is NaN, if \|μ − mid\| > max(6·sd, 15% of mid), or if engine values are more than 6 s old | [I] |
| Fill sanity | Disable a symbol after 3 consecutive clips with slippage more than 3× the expected h + γ/4. **[testnet] Use 3× the phase-table cost of that clip** (`cost_model.json`) | [I] |
| Feed | No storm observation for 6 s mid-storm: no new risk. WebSocket down more than 10 s: cancel all; resume after a snapshot plus fresh observations. **[testnet]** Two connections merged by (season, seq). Reconnect at 0.25 s, then every 0.5 s with jitter, capped at 2 s. Phase watchdogs: 3 s in the storm, 8 s pre-storm and aftermath, 35 s in pauses. Deduplicate trades by `trade_id`. A snapshot is forwarded only if its `last_seq` is new | Skeleton. [testnet] 5 abrupt drops in about 1 hour, one of 31.7 s; largest frame gaps 2.12 s (storm), 5.21 s (aftermath), 29.57 s (pause) |
| Order state | An order unknown for more than 3 s: NO_NEW_RISK until reconcile. Never retry order actions automatically | Skeleton |
| HTTP 429 | Freeze 5 s, pad the ledger, stop manual trading | Skeleton |
| Manual kill | `k` on the console, a `KILL` file, or Ctrl-C: cancel all (1 action) and freeze; positions are not flattened. Browser cancel-all as backup. Drill all three (no orders left 1 s after a kill) | – |
| Process death | Op cancels all in the browser. SB promotes the standby with `--live`; it restores the engine from `engine_state.jsonl` plus a snapshot | Engine restore is exact (§2.9) |
| Variance | Season sd about $11.7k, p5 about +2.3k. No variance trades (S4). The round-3 book is held to settlement by design | – |

---

## 7. Remaining work before the event

Owners:
- **A:** execution engineer.
- **B:** models.
- **C:** microstructure and replay.
- **D:** operations.

| Pri | Task | Done when | Owner | Time |
|---|---|---|---|---|
| P0 | Testnet: sign in, bot key, User-Agent. One subscribe with 12 symbols × {news, quotes, trades, orderbook}. Record a season with `record_feed.py`; run `check_recording.py` | Prints "recording matches the published feed"; quote, trade and orderbook schemas saved. **[testnet] Done 23 Oct.** `check_recording.py` always flags official advisories, which are fresh draws; compare them on outlook, number, age and time only | A | 2 h + 1 season (40 min) |
| P0 | Order mechanics: place, cancel, cancel-all by symbol, 1-lot marketable round trip. Does a remainder rest? Limit-room cancel, self-trade, create/redeem 1 lot and qty > 1 | JSON of every endpoint saved; parsers fixed | A | 2 h |
| P0 | Price protection: 1-lot limits priced 1%, 3% and 10% through the touch, plus one market order | Reject and cap behaviour tabulated | A | 0.5 h |
| P0 | Latency: p50/p95/p99 of feed message → order ack → visible in `/positions`, including the 570 and 740 bursts | p95 known (S2/S1 gate needs < 1.5 s) | A | 1 h |
| P0 | **Book recording around the formation and reports:** all 12 books at 40-90 s, 560-600 s and 730-780 s for 3 rounds | Table: spread by symbol and phase; depth at the touch vs ¼·L; whether the 14 bp noise is in the book; book reprice time at 64 s (stocks) and 70 s (ETFs); pre-formation drift. **[testnet] Done** (`TESTNET_FINDINGS.md` §3-4). The book reprices later than the consensus | C | 4 h |
| P0 | **ETF book-lag test** (zero risk first): line the ARCH/REBLD and component books up against that season's `market.csv` (the date names the season). If ARCH is stale at 66-68 s with size, do two 1-lot round trips (buy about 66 s, sell about 74 s; under $20 of risk) | GO/NO-GO for §4.5. **[testnet] Zero-risk part done:** a 7-s lag; stale from 68 s, not 66 s. The 1-lot round trips are test T6 of the proposed plan (needs team approval) | C | 1.5 h |
| P0 | Depth cost γ: work ¼·L clips in PORT, HAVEN, FERRY and ARCH in quiet phases | Fit of cost = h + γ·q/L. **[testnet] Measured from other participants' orders on the recorded books** (`cost_model.json`). Our own impact still needs live fills (P1 25% season) | C | 2 h |
| P0 | Engine in the bot (§9.1): adapter, worker thread, fast params, persistence, reset on new season, case from GET | A replay of 5 seasons gives the same μ as `replay_validate.py` within 0.01 | B | 4 h |
| P0 | **[testnet] Engine snapshot fix** (§9.1 step 3) | Replaying the 23 Oct recording through the patched engine reproduces the patched result in `testnet/analysis/writer/w00_snapshot_resync.out`: 22 of 24 recovery cases exact | B | 1 h |
| P0 | **[testnet] Robust feed:** two connections, merge by (season, seq), snapshot gating, trade dedup, fast reconnect, phase watchdogs (§6 Feed row) | A replay of the recording with drops injected gives the same engine values as the clean feed | A | 3 h |
| P0 | **[testnet] 1-lot order test plan T1-T7** (`TESTNET_FINDINGS.md` §7): fills, latency, price protection, formation fills, create/redeem | **Team approval in writing before running.** Then: latency p50/p95, price-protection behaviour and 1-lot fill prices tabulated | A + D | 1 season |
| P1 | **[testnet] Second recording with two parallel connections** (zero risk) | Shows whether drops are server-side (both connections drop together) | A | 1 season |
| P0 | Plan strategies (§9.2): H1, S3 worked, flatten, S1-r3 with ε and gates, REIN ask, announcement freeze. Config changes | Unit tests on synthetic books | A + B | 8 h |
| P0 | Replay harness that runs the **bot's own strategy code** over seasons 401-440 with the RT fill model (the skeleton's `replay.py` has a known bug; rebuild it) | Within 10% of `dp_bt3.py` Plan on the same seasons | C | 4 h |
| P0 | Kill-switch, disconnect and restart drills, including a restart mid-storm from `engine_state.jsonl` | No orders 1 s after a kill; values after restart match | A + D | 1.5 h |
| P1 | Full testnet season live at 25% size; compare fills with the consensus | h, γ and latency fixed; k row and H1 size chosen. **[testnet] Also score S3 on our own fills, by round** (restrict S3 to round 3 if rounds 1-2 are negative again; §4.1 rule 8) and compare clip costs with `cost_model.json` | All | 40 min + 2 h |
| P1 | Announcement drills: 5 fake announcements injected into a replay | Override in < 15 s; the right linked symbols freeze | B + D | 2 h |
| P1 | Live law check and status line (§5.1) | Logs predicted vs realised moves | B | 2 h |
| P1 | Standby laptop, second network, connection count | Standby goes live in < 30 s | D | 1 h |
| P2 | Refit the engine `sd_floor` on train only (minor leak, [RT] B6) | Done | B | 0.25 h |
| P2 | S2/S1 rounds 1-2 snipe module, only if latency allows | Replay positive at +0 rows and ≥ 0 at +1 row | B | 3 h |
| Day before | Freeze code and parameters (hash); two full game-day drills | – | All | 3 h |

Total: about 45 person-hours.

---

## 8. Questions for Slack

**Do not ask** about the house's 4 s and 10 s lags, the formation jump or the drift. The answers would be public, and the edge is ours to test.

1. **Price protection.** Is it a band around the house price, or a size cap? Does it apply to limit orders priced through the touch, and to market orders? How wide is it?
2. **Rate limits.**
   - Is the 180/min team limit a sliding 60-s window, a fixed minute or a token bucket?
   - Do rejected orders count?
   - Do exchange-initiated cancels (limit room, self-trade) count?
   - Do browser orders count toward the team's 180?
3. **Order types.**
   - Does the unfilled part of a marketable limit order rest?
   - Is there an IOC or FOK flag, a tick size, or a maximum order quantity?
4. **Create/redeem.**
   - Can one action carry quantity > 1, and is there a maximum?
   - Are "marks" the house consensus?
   - Are the stock legs checked against position limits, and can a create or redeem trigger the cancel-resting-for-room rule?
5. **Resting orders.** Do they survive the close and the pause between rounds? How long are the live pauses? Do snapshots arrive while the clock is stopped?
6. **Announcements.**
   - How many should we expect, and do they carry numbers or structured fields?
   - Can one arrive during a pause, or between the round-3 final report and the close?
   - Does "from that time" exclude damage already done?
7. **Live parameters.** Are they identical to the research seasons unless an announcement says otherwise: damage rates, closure threshold and costs, rebuilder capacities, layer bounds? Is `/cases/hurricane.json` identical to the research `case.json`?
8. **REIN.** What is REIN's layer top? No research season exceeds $1,766M.
9. **Feeds.**
   - Is there a private orders/fills channel?
   - What are the schemas of the quotes, trades and orderbook channels, and of `POST /orders`, `/positions` and `/orders/open`?
   - Does `quotes` show the house quote or the best of the whole book, including other teams?
10. **Live venue.** Wired or Wi-Fi? Is the live exchange on the same host as the testnet (`hurricane.cornelltradingcompetition.org`)?
11. **WebSocket.** Do ping/pong frames count toward the 10 frames per second limit?
12. **House on live day.** Will the live season open at the packet's starting prices? Do the house market makers show the same depth live as on the testnet?

**[testnet] New questions from the 23 Oct recording** (full wording in `TESTNET_FINDINGS.md` §8; none of them mentions the house's reaction timing or its formation behaviour):

13. Our market-data WebSocket dropped 5 times in an hour without a close frame. Is there a server-side idle or heartbeat timeout, or a recommended ping interval? Will live use the same gateway?
14. How many concurrent market-data connections may a team hold? Can we keep two for redundancy?
15. Is there a REST endpoint for the current snapshot, or for news since seq N?
16. Is the snapshot schema final? Its `advisory` has no `storm`, `seq`, `round` or `type` field. Will live snapshots also come about every 30 s?
17. Testnet official advisories differ from the research feed in `hurricane_conditions`, cone and headline; everything else is identical. Are they regenerated on each run, and will live advisories come from the same generator?
18. Is `trade_id` globally unique? The last trade per symbol is re-sent on reconnect. Is there an aggressor-side field or a private fills channel?
19. Market-data timestamps are local wall clock without a time zone. Will live use the same convention? Is there a millisecond server-time endpoint?
20. At the close every book goes empty and quotes show null bid and ask. Are resting orders cancelled at the close?
21. Is the testnet house the same pricing model and configuration as the live house: starting prices, spreads, depth and sizes?
22. Is there an opening auction or restricted period at the start of a round? What happens to a limit order priced far through the touch?
23. Is the tick size 0.01 for every symbol, ETFs included?

---

## 9. Wiring the engine and the settlement laws into the bot

### 9.1 Skeleton (`work/se/bot/skeleton/`)

**Step 1: copy the engine.** Copy `work/se/engine/{fairvalue.py, fvcore.py, analog.py, pattern.py, posterior.py, engine_params.json, engine_params_fast.json, library_all.json}` and `work/se/fits/sources.verified.json` to the team laptops.
- Keep the relative path `../fits/sources.verified.json`, or edit `paths.sources` in the params file.
- Pass the live case dict explicitly. The default `paths.case` points into the research data folder.

**Step 2: add `engine_model.py`.** The engine's `fairvalue.py` has the same module name as the skeleton's, so load it under another name:

```python
# bot/skeleton/engine_model.py
import importlib.util, os, threading, time, queue
import config
from fairvalue import FairValueModel, FairEstimate          # the skeleton's module
ENGINE_DIR = os.environ["SE_ENGINE_DIR"]
_spec = importlib.util.spec_from_file_location("se_engine", os.path.join(ENGINE_DIR, "fairvalue.py"))
E = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(E)   # the engine adds ENGINE_DIR to sys.path

class EngineModel(FairValueModel):
    name = "engine"
    def __init__(self, case, run_dir, fast=True):
        p = os.path.join(ENGINE_DIR, "engine_params_fast.json" if fast else "engine_params.json")
        self.run_dir, self.case, self.p = run_dir, case, p
        self.st = E.SeasonState(params=p, case=case, persist_path=os.path.join(run_dir, "engine_state.jsonl"),
                                adjust_file=os.path.join(run_dir, "adjust.json"))
        self.q, self.fv, self.fv_t = queue.Queue(), {}, 0.0
        self.lock, self.wake = threading.Lock(), threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()
    def on_message(self, m):            # every news message AND every snapshot, in arrival order
        self.q.put(m); self.wake.set()
    def _drain_and_compute(self):
        with self.lock:                 # FIFO drain keeps seq order; the engine dedups by seq
            while not self.q.empty():
                self.st.on_message(self.q.get_nowait())
            fv = E.fair_values(self.st)  # cached inside the engine until the next new message
        self.fv, self.fv_t = fv, time.monotonic()
    def _loop(self):                    # engine thread: owns SeasonState
        while True:
            self.wake.wait(); self.wake.clear()
            self._drain_and_compute()
    def refresh_now(self):              # synchronous (~30 ms): round-3 final report
        self._drain_and_compute()
    def evaluate(self, state, now_real):
        stale = time.monotonic() - self.fv_t > 6.0
        return {s: FairEstimate(mu, float("inf") if stale else sd, "engine") for s, (mu, sd) in self.fv.items()}
    def settlement(self, state):
        if state.round != 3 or self.st.status(3) != "final":
            return None
        return {s: mu for s, (mu, _) in self.fv.items()}  # gates and the se_laws cross-check are applied in the strategy
    def new_season(self):
        with self.lock:
            self.st.reset()             # testnet: seq restarts each season; rotate the persist file too
```

**Step 3: hook it into `main.py`.**
- **`Bot.__init__`:** `self.model = EngineModel(case=<dict from GET /cases/hurricane.json>, run_dir=args.log_dir)`.
- **`consume()`:**
  - In the snapshot branch, add `self.model.on_message(e.msg)`.
  - **[testnet] Fix the engine's snapshot handling first** (`fairvalue.py` `_on_snapshot`; both parts are needed):
    1. Add the snapshot's top-level `storm` to its `advisory`, `obs` and `track` before passing them to the pattern classifier. Today the classifier sees storm None, `PatternPosterior` resets on the (round, storm) key change, and the next live observation resets it again.
    2. Never pass an official advisory to the classifier again if its number is already known. With part 1 alone, a snapshot that arrives before the live message counts the label twice.
    - On the recording, as is, 16 of 24 recovered advisories moved a fair value past the S3 threshold (worst GRID +4.99). Patched, 22 of 24 match the clean feed exactly (`testnet/analysis/writer/w00_snapshot_resync.out`, `w00_doublecount.out`).
  - **[testnet] Feed the engine from the merged two-connection stream** (§6 Feed row). Forward a snapshot only if its `last_seq` is above the highest seq already forwarded.
  - In the news branch, add `self.model.on_message(e.msg)` after `self.state.on_message(e.msg)`.
  - If a batch contains a round-3 `damage_report` with `stage == "final"`, call `self.model.refresh_now()` before `decide(news)`. Otherwise decide on the cached values; the engine thread refreshes them within about 10-50 ms, and the 0.5-s ticker re-decides.
- **`_new_season`:** call `self.model.new_season()`.
- **Console overrides.** Route `f SYM PX` to `self.model.st.set_adjustment(SYM, mult=0, add=PX)` and `m SYM X` to `set_adjustment(SYM, mult=X)`. The engine then rebuilds the ETFs per scenario. The skeleton's own `apply_overrides` and `etf_fairs` sum sigmas and would not rebuild ETF means.

**Step 4: add the settlement laws as an independent cross-check** for S1-r3.
- Import `work/final/se_laws.py`.
- Feed `ClosureIntegrator.add(age, x, y, r, I)` with every storm message of every round, one integrator per round.
- At 740 s in round 3, compute `se_laws.values(D, L, T)`, where:
  - D = engine `st.cumulative_damage()`, a dict `{PORT, ISLE, AQUA, GRID, FERRY: pp}`;
  - L = `st.season_loss()`;
  - T = the sum over the three rounds of `integrator.weighted()`, a dict `{PORT, GRID, FERRY: weighted seconds}`.
- Do not use `se_laws.final_orders()`: its ε is in basis points. Use the $ ε table in §4.3.
- Gate per symbol as in §4.3.

### 9.2 Strategy layer (replace `build_strategies`)

| Module | Based on | Changes |
|---|---|---|
| `FormationLeg` (H1) | New | Clock-keyed on the market time of each round: 50-56 s target dir × size × L, 72-78 s target 0. ¼·L clips, TAKE priority. REIN excluded. Size from config (`h1_size` 0.5 / 1.0 / 0). **[testnet] No 72-78 s target 0:** hand the position to `EngineTaker` at 80 s. Fallback flat over 84-98 s in L/8 clips if the engine is not live by 76 s. No crowding skip |
| `EngineTaker` (S3) | `FundamentalTaker` | The §4.1 rule with bid/ask (not mid ± spread), k by round, γ from config, ¼·L clip, 2-s cooldown per symbol, window 80-540 s, skip REIN. **[testnet] L/12 clip; per-phase cost term instead of γ; cap at mid ± 1.5%; starts from the H1 position at 80 s** |
| `BookFlattener` | New | Rounds 1-2, 562-568 s: target 0 in ¼ clips, **HEDGE** priority. **[testnet] 540-568 s in L/12 clips** |
| `SettlementPositioner` (S1-r3) | Existing class | Per-symbol ε table (§4.3, in $) in place of `eps_bp`. Gates: case diff, announcements, law check, se_laws agreement. Fires in `on_batch` after `refresh_now()`. The 746-s sanity cancel. **[testnet] Sweep every 0.5-1 s to 750 s; the sanity cancel at ≥ 756 s** |
| `ReinAsk` | New | §4.4. HEDGE priority. Re-post per round |
| `AnnouncementFreeze` | `EventSniper._announcement` | Freezes named + linked + ETF symbols (`overrides[s]["disabled"] = True`), cancel-all per symbol, alarm. **Remove** the damage-report, advisory and impact triggers |
| `EtfLeadLag` | `work/final/strategy_addons.py` | Only after the §7 gate; when on, add ARCH and REBLD to `EngineTaker` and `FormationLeg` exclusions. **[testnet] Default OFF. If on, entries only as limits capped at mid × (1 + gap/2)** |
| `PassiveQuoter`, `EtfArb`, `ReportSniper` | – | Disabled |

**`config.py` changes before any testnet run:**
- `STRAT["taker"]`: `k_r12` 0.1, `k_r3` 0.2, `gamma` 0.005, `clip_frac` 0.25, `max_slip_bp` 50, window 80-540.
  - **[testnet] Replace:**
    - `clip_frac` 1/12 (bot units: PORT 16, ISLE 25, AQUA 41, GRID 33, FERRY 66, RECON 41, MAT 58, GEN 50, HAVEN 20, ARCH 4, REBLD 8);
    - `gamma` → the per-phase cost term 2.0 × (walk(L/12) + 36 bp; 17 bp for ETFs), read from `cost_model.json` (`writer/w05_params.out`);
    - a cap at mid ± 1.5%;
    - the flatten window 540-568 s in L/12 clips;
    - the H1 exit removed (S3 inherits the book at 80 s);
    - the S1-r3 sanity check at ≥ 756 s;
    - a 3-s no-TAKE window at every round open.
- Remove `k_sigma` 1.5 and `z_full` 3.0. They were far too conservative: S3 at k = 1.5 was $4.5k against $27.6k.
- `MAX_DRAWDOWN_USD` = 20000 per round.
- `BOT_ACTION_CAP` = 150.
- `BLACKOUTS`: keep them, but the flatten and cancel intents must be HEDGE priority.
- `SNIPE_PRICE_BAND` 0.60 is fine.
- Add `H1_SIZE` (round 1: 0.5) and the ε table.

### 9.3 Starter-kit `example_bot.py` structure (if not using the skeleton)

**Keep from the starter kit:**
- `HEADERS = {"User-Agent": ...}` on every `urllib` request; urllib's default is refused.
- The `X-API-Key` header.
- `get("/cases/hurricane.json", api=False)`.
- The quote handling: `data["type"] == "quote"` → `self.quotes[data["symbol"]]`.

**Change:**

```python
# 1) Engine in its own thread: it owns SeasonState; the WebSocket loop only enqueues.
E = importlib.util.module_from_spec(...)          # load engine fairvalue.py as "se_engine", as in 9.1
self.st = E.SeasonState(params=FAST_PARAMS, case=self.case, persist_path="engine_state.jsonl", adjust_file="adjust.json")
self.inq = queue.Queue(); threading.Thread(target=self.engine_loop, daemon=True).start()

# 2) One subscribe, all symbols and channels, within 10 s of connecting; send the User-Agent on the socket too.
subscribe = {"action": "subscribe", "symbols": list(self.case["limits"]), "channels": ["news", "quotes", "trades", "orderbook"]}
async with websockets.connect(ws_url, additional_headers={"X-API-Key": self.key, **HEADERS}) as ws:
    await ws.send(json.dumps(subscribe))
    async for frame in ws:
        data = json.loads(frame)
        if data["type"] == "quote": self.quotes[data["symbol"]] = data
        elif data["type"] == "news" and isinstance(data.get("message"), dict):
            m = data["message"]; self.inq.put(m); self.last_mt = m.get("time", self.last_mt)
            if m.get("type") == "damage_report" and m.get("stage") == "final" and m.get("round") == 3:
                await asyncio.to_thread(self.compute_now)   # ~30 ms, then S1-r3
                await self.settle_round3()
            elif m.get("type") == "announcement":
                self.freeze(m.get("symbols") or [])

# 3) Order calls never block the event loop: run urllib in a thread pool (8 workers for bursts).
def post(self, path, body):
    req = urllib.request.Request(self.url + "/api/v1" + path, data=json.dumps(body).encode(), method="POST",
                                 headers={**HEADERS, "X-API-Key": self.key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=2) as r: return json.load(r)
async def send(self, sym, side, qty, px):
    return await asyncio.get_running_loop().run_in_executor(self.pool, self.post, "/orders",
        {"symbol": sym, "side": side, "order_type": "limit", "quantity": int(qty), "price": round(px, 4)})

# 4) Decision loop every 0.5 s on the market clock (feed time or GET /competitions/time, every 1 s):
#    t = (market_time - 09:30) / 30 real seconds; run FormationLeg / EngineTaker / BookFlattener / ReinAsk
#    exactly as in 9.2. Positions come from GET /positions every 0.5 s; open orders from GET /orders/open;
#    cancel with DELETE /orders/{id} or DELETE /orders/all?symbol=SYM.
```

**Testnet specifics:**
- Each new season resets positions and changes the competition id.
- Detect it with `GET /competitions/time`, or by seq restarting. Then call `self.st.reset()` and rotate the persist file.
- Never run the starter kit's `decide()`. It sells on advisories and is not a strategy.

---

## 10. What changed from the earlier playbook

| Earlier claim | Status | Now |
|---|---|---|
| "The house ≈ a full posterior, so skip the storm phase; the mid-storm replica is worth $1.9k" | **Overturned** [RT] | True only at the opens, at storm end and after 600 s. Mid-storm the clock bias plus the engine is the core: S3 about 17k, H1 +3-7.5k |
| ETF lead-lag about $9k per season | **Overlaps S3/H1; book lag unproven** | Estimated +1.3k to +4.3k over the Plan at 25 bp; TEST-FIRST |
| Report snipes about $4.8k at 10 bp | **A pure race** | $1.0k at +2 s; about 0 inside the Plan. Default OFF |
| Round-3 settlement positioning $1.2k | Confirmed | 1.0-1.2k if first, 0.15-0.25k if 2 s late |
| ε = max(5 bp, 3 × RMSE) | **Too tight** | ε ≥ max error + 1¢ (§4.3) |
| Flat at every phase boundary | Changed | Carry the S3 book 80-562 s (rounds 1-2) and to settlement (round 3) |
| REIN open too high by $0.54 / $0.47 | Refined | $0.38 / $0.33 on 600 seasons; worth $50-75; not traded |
| Round-open HAVEN hold +$357 (t 2.6) | **Not significant** | t 0.5-1.2 on holdout; NO-GO |
| Drawdown guard −$6k | **Costly** | $20k per round |
| "< 300 actions per round" | Changed | Worked clips use about 540 orders per season and up to 151 in 60 s; cap 150/min |
| Backtest S6R $35.5k (¼-cap "robustness" variant 24.8k) | **Fill-model artefact** [RT B1-B4] | $14.7k single shot at γ 0.5%; worked 24.5k; Plan 20.3k |
| Settlement law max errors (lin closure): FERRY 0.097, GEN 0.103, ARCH 0.215 | Improved | `tickprob_impact`: FERRY 0.077, GEN 0.099, ARCH 0.156 (all 600) |
| Skeleton defaults k_sigma 1.5, z_full 3.0, $6k stop, no formation leg | **Wrong** | §9.2 |
| RT fill model h 0.25%, γ 0.5%, ¼-limit clips | **[testnet] Superseded** | Measured phase tables: storm h 11-16 bp, γ 0.8-1.3% (stocks); 80-120 s costs 2-7× the storm; S3 clips L/12 |
| Plan 20.3k per season | **[testnet] Halves with measured costs** | Plan as written 10.7k; new configuration 19.8k [20.1k] on the consensus, 14.2k at costs ×1.5 + 2 s; plan on 10-15k |
| H1 exit at 72-78 s | **[testnet] Changed** | No forced exit; S3 inherits the H1 book at 80 s. The book had barely repriced by 74 s (+65 on books vs +7.4k on the consensus) |
| H1 crowding skip at 48 s | **[testnet] Dropped** | Never fired on the books, although others pre-positioned 1-2 L |
| S1-r3 sanity check at 746 s | **[testnet] Moved to ≥ 756 s** | At 746 s it cancels PORT, HAVEN and ARCH on the books |
| ETF lead-lag: book lag unproven | **[testnet] Lag confirmed (7 s); verdict still TEST-FIRST, default OFF** | Pays on books only with capped entries |
| Engine snapshot handling | **[testnet] Bug** | A snapshot advisory resets the pattern posterior; fix in §9.1 |

---

## Appendix: reproduction

All scripts run with `python3 -I`, standard library only, and are read-only on the data folder. SP = the scratchpad.

| What | Command | Output |
|---|---|---|
| Plan and sensitivities | `python3 -I SP/work/final/dp_checks/dp_bt3.py SP holdout 401 600 0.0025` (also `loso 1 400`, and h = 0.001 / 0.005) | `dp_checks/dp_bt3.out` |
| Hold vs flatten | `dp_checks/dp_bt2.py` (same arguments) | `dp_bt2_hold.out`, `dp_bt2_train.out` |
| First Plan variants and red-team repro (24,458) | `dp_checks/dp_bt.py` | `dp_bt_hold.out`, `dp_bt_train.out`, `dp_bt_hold_h10.out`, `dp_bt_hold_h50.out` |
| Plan at h = 1% | `dp_checks/dp_bt4.py SP holdout 401 600 0.01` | `dp_bt4.out` |
| Clock-only fallback, worked | `dp_checks/dp_clock.py` | `dp_clock.out` |
| Settlement-law error, 600 seasons | `dp_checks/chk_settle_all.py SP` | stdout (§2.1) |
| Engine timing after the round-3 final | `dp_checks/t_final.py SP 450` | stdout |
| Red-team checks | `SP/work/se/redteam/rt_*.py` | `rt_*.out` |
| Engine validation | `SP/work/se/engine/replay_validate.py` | `validate_final.out` |
| Backtest | `SP/work/se/backtest/backtest.py SP holdout 401 600 0.0025 S6R` | 35,483 |
| **[testnet]** Testnet findings checks | `sh testnet/analysis/writer/run_all.sh <recording.jsonl> SP` (the verifier's scripts are in `testnet/analysis/verify/`) | `testnet/analysis/writer/*.out`; write-up `testnet/analysis/TESTNET_FINDINGS.md` |
