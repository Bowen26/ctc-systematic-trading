# backtest:run

# Backtest of the Hurricane Season strategies (holdout seasons 401-600)

## 0. Bottom line

- **What was built.** `backtest.py` is an event-driven replay of the holdout seasons. It reads every `feed.jsonl` message in seq order, uses the `market.csv` row every 2 s as the house price, and marks the book to `settlement.json` at the end. The fair values come from `fairvalue.py`, cached once per distinct message time. Strategy parameters were fitted on seasons 1-400. For those seasons the engine ran with a **leave-one-season-out** analog pool, so a storm is never its own analog. All numbers below are holdout results on 401-600 (library 1-400), and every one comes from code I ran.
- **Look-ahead audit.** I compared the cache against fresh engines fed only messages with (round, time) ≤ (r, t), at 10 points in both caches. They match to 5e-5, which is the 4-decimal rounding. There is no state leak and no future message reaches the engine.
- **The recommended strategy is the combined S6R.** At half-spread 0.25% it makes **$35.5k per season on holdout** (sd 16.0k, 5th percentile +10.0k, 99% of seasons positive). The train mean is $35.4k.
  - Its parts are the S3 mid-storm taker (about $24.0k), a pre-positioned formation leg (about $7.1k), the S1 final-report trade (about $2.7k) and the S2 preliminary-report trade (about $1.7k).
  - It uses at most 84 of the 180 allowed actions in any 60 s, so the rate limit never binds.
- **S3 (mid-storm fair-value taker) is the engine's real edge.**
  - Holdout: $27.6k per season at h = 0.25% with k = 0.1, and $21.5k at h = 0.5%.
  - About three-quarters of it comes from trades opened in the first minute after the storm forms, when the house has jumped up and then drifts back.
  - It **survives extra latency**: about 65% remains with one extra 2-s row and about half with five extra rows (10 s).
- **S1 and S2 are pure latency races.** With one extra row of latency (2 s) both go to zero or negative, because the house reprices at row +2.
  - The round-3 final-report trade earns about $1.0k per season with a 92% hit rate and almost no downside, but only if we fill before the house's 3-4 s reaction.
- **S4, insurer-layer positioning at the opens, is small on its own.** HAVEN+REIN held earns about $0.6-0.7k per season (t ≈ 2). Its larger variants earn money from the house's formation jump, not from layer pricing.
- **S5, fading wire rumors, loses at every threshold and every spread** ($-0.8k to $-27.8k per season). The house research agrees. Leave it off.
- **The formation trade must be entered at 56 s**, before the storm forms (fill row 29). Entered on the `storm_formed` message at 60 s, it becomes a latency race and is worthless with one extra row. Entered at 56 s it is immune to latency.
- **Risk limits.** Drawdown stops and caps on σ-weighted gross exposure lower the mean and do not raise the 5th percentile. Keep only a $20k per-round drawdown stop as a circuit breaker. The skeleton's current 6000 costs about $3k per season.
- **ETF create/redeem arbitrage cannot be tested**, because there is no ETF book in the data. It was skipped. ARCH and REBLD are traded here only as single symbols against their house consensus.

## 1. Simulation model

**Data**
- **Prices.** The house price is `market.csv` row i of each round, at real second 2i (390 rows).
- **News.** Feed messages are grouped by (round, real second). Real second = (market time − 09:30) / 30.
- **Fair value.** After each message group, `fair_values()` gives (mean, sd) for all 12 symbols.
- **Cache.** One entry per message time is stored in `cache/fv_{holdout|loso}_NNN.json`, 600 files, 83 MB. The holdout cache took 526 s and the loso cache 1110 s, both on 15 processes.

**Decision times**
- A decision is taken at every row time (2i) and at every message time.
- At decision time t the strategy sees:
  - the engine value after all messages with time ≤ t;
  - the last house price P = row ⌊t/2⌋.

**Fill model (as specified)**
- An order decided at t fills at row j = ⌊t/2⌋+1: buys at P_j(1+h), sells at P_j(1−h). Nothing fills when j > 389.
- The verified house research says the house reacts to a message at even t at row ⌊t/2⌋+2. That makes row j the last stale row.
- **Robustness variants:**
  - `lat+1` and `lat+2`: fill one or two rows later;
  - `cap.25`: each symbol fills at most ¼ of its limit per row (the market-impact proxy);
  - `rate60`: 60 actions per minute instead of 180.

**Constraints**
- Position limits PORT 200, ISLE 300, AQUA 500, GRID 400, FERRY 800, RECON 500, MAT 700, GEN 600, HAVEN 250, REIN 200, ARCH 50, REBLD 100.
- At most 180 order actions in any rolling 60 real seconds, one action per order, with the window restarting each round.
  - When over budget, orders go in priority order: flats, then S1, S2/H1, S3, S4, and the larger $ edge first within a group.
  - Dropped orders are re-evaluated at the next decision.

**Accounting**
- PnL = cash + position × settlement.
- By round: mark-to-market at row 389 of rounds 1-2, and at settlement for round 3.
- By symbol: cash_s + pos_s × settle_s.
- By component: the trade-level PnL to settlement, Σ q·(settle − fill), which sums exactly to the total.

**Fitting protocol**
- For every strategy and half-spread, parameters were chosen by **train mean PnL per season** on seasons 1-400 (loso engine). Holdout results are reported for that choice.
- The full grids appear below only as sensitivity; nothing was chosen on them.
- Train and holdout agree closely everywhere. For example, S3 at k = 0.1 and h = 0.25% gives 26,991 on train and 27,555 on holdout.

## 2. Strategy rules

**Shared taker rule** (symbol s, limit L, e = μ − P, θ = k·sd + h·P):
- If e > θ, move to +L. This only ever adds to a long.
- If e < −θ, move to −L.
- Otherwise, if long and e < −h·P (fair below the bid), go flat. If short and e > h·P, go flat.
- Otherwise, hold.

**Windows.** Rules apply only inside each strategy's window and positions are held between windows. A "flat at T" means target 0 at real second T. Every time below is in real seconds after the round opens.

| Strategy | Window and symbols | Exit variants tested |
|---|---|---|
| **S1** final report | Opens at the final report (740). Rounds 1-2: 740-768; round 3: 740-778. All 12 symbols. | flat: rounds 1-2 flattened at 770, round 3 held to settlement. hold: everything held to settlement. |
| **S2** preliminary report | 570-598, all 12 symbols | flat at 600, flat at 736, or hold |
| **S3** mid-storm taker | 60 (formation) to 540 (dissipation), all 12 symbols | flat at 568 (just before the preliminary) or 770 per round group, or hold. Also tested: no exit except reversal; size min(1, abs(e)/(z·sd)) for z = 1, 2; window to 568. |
| **S4** round open | 0-58 of each round, before the storm forms. HAVEN+REIN, or all 12 symbols. | hold (re-evaluated at the next open), flat at 72 (after the formation reaction), or flat at 770 |
| **S5** wire fade | A wire whose outlook disagrees with the latest official advisory, at time t_w | House reaction row a = ⌊t_w/2⌋+2 (even) or +3 (odd). If abs(P_a/P_⌊t_w/2⌋ − 1) > thr, trade the 10 stocks against the move at full limit, fill row a+1, flatten 60 s later. |
| **H1** formation (house-behaviour reference) | Enter at 56 (fill row 29) or on the storm_formed message at 60 (fill row 31). Long PORT ISLE AQUA GRID FERRY HAVEN REIN ARCH, short RECON MAT GEN REBLD, full limits. | flat at 66, 72, 80 or 90 |
| **H2** drift (house-behaviour reference) | At 220: short the islands, HAVEN and ARCH; long RECON MAT GEN REBLD | flat at 538 |
| **S6R** combined | One book. H1 enter 56 / flat 72; S3 74-540 with k3 for rounds 1-2 and round 3; S2 570-598 with k2; S1 740-778 with k1; everything held. At h = 1%, H1 is replaced by S4 on all symbols (k = 0.05, held). | Risk variants: drawdown stop 6k-30k per round (no new risk after it triggers); σ cap Σ abs(pos)·sd ≤ 8k or 15k |

## 3. Results per strategy (configuration chosen on train)

T1 is holdout, $ per season at full limits. "Actions" is per season and "max/60s" is the worst 60-s window across the 200 seasons.

| Strategy | h | Chosen on train | Train mean | **Holdout mean** | sd | t | p5 | Hit | R1 | R2 | R3 | Actions | max/60s | Notional |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 hold | 0.1% | k=0, hold | 4,852 | **5,456** | 12,177 | 6.3 | -15,723 | 0.68 | 696 | 1,906 | 2,854 | 40 | 33 | 1.19M |
| S1 hold | 0.25% | k=0, hold | 4,082 | **4,401** | 12,178 | 5.1 | -16,700 | 0.61 | 371 | 1,745 | 2,285 | 25 | 20 | 0.62M |
| S1 hold | 0.5% | k=0, hold | 3,414 | **3,733** | 11,620 | 4.5 | -15,111 | 0.59 | -2 | 1,681 | 2,055 | 16 | 19 | 0.35M |
| S1 hold | 1% | k=0, hold | 2,187 | **2,842** | 9,888 | 4.1 | -12,331 | 0.60 | -269 | 1,254 | 1,858 | 11 | 12 | 0.19M |
| S1 flat | 0.1% | k=0.1 | 2,640 | **2,702** | 1,872 | 20.4 | 320 | 0.99 | 608 | 794 | 1,299 | 54 | 36 | 1.14M |
| S1 flat | 0.25% | k=0.25 | 1,889 | **1,882** | 1,684 | 15.8 | 39 | 0.95 | 330 | 546 | 1,006 | 18 | 20 | 0.32M |
| S1 flat | 0.5% | k=0.25 | 1,504 | **1,486** | 1,521 | 13.8 | 0 | 0.93 | 248 | 408 | 831 | 9 | 17 | 0.13M |
| S1 flat | 1% | k=0.25 | 1,051 | **1,029** | 1,280 | 11.4 | -1 | 0.85 | 149 | 258 | 622 | 6 | 14 | 0.07M |
| S2 hold | 0.25% | k=0, hold | 3,389 | **3,250** | 12,287 | 3.7 | -17,150 | 0.59 | 143 | 1,319 | 1,788 | 22 | 19 | 0.54M |
| S2 flat at 600 | 0.1% | k=0.1 | 1,241 | **1,346** | 1,356 | 14.0 | -378 | 0.90 | 327 | 475 | 544 | 59 | 41 | 1.13M |
| S2 flat at 600 | 0.25% | k=0.25 | 701 | **747** | 1,093 | 9.7 | -262 | 0.76 | 157 | 307 | 283 | 17 | 25 | 0.29M |
| S2 flat at 600 | 0.5% | k=0.25 | 560 | **609** | 838 | 10.3 | -103 | 0.79 | 112 | 233 | 263 | 8 | 20 | 0.11M |
| S2 flat at 600 | 1% | k=0.25 | 295 | **355** | 618 | 8.1 | -161 | 0.60 | 68 | 144 | 144 | 4 | 14 | 0.05M |
| **S3** | 0.1% | k=0.1, hold | 33,229 | **33,230** | 17,732 | 26.5 | 7,115 | 0.95 | 11,498 | 11,081 | 10,652 | 273 | 86 | 6.13M |
| **S3** | 0.25% | k=0.1, hold | 26,991 | **27,555** | 17,239 | 22.6 | 993 | 0.95 | 9,734 | 9,307 | 8,513 | 186 | 78 | 3.91M |
| **S3** | 0.5% | k=0.1, hold | 20,564 | **21,488** | 16,359 | 18.6 | -3,866 | 0.93 | 7,598 | 7,299 | 6,591 | 132 | 63 | 2.58M |
| **S3** | 1% | k=0.2, hold | 13,251 | **14,433** | 13,353 | 15.3 | -7,079 | 0.89 | 5,009 | 5,042 | 4,382 | 63 | 51 | 1.07M |
| S4 HAVEN+REIN, held | 0.25% | k=0 | 668 | **666** | | 2.1 | | | | | | | | |
| S4 HAVEN+REIN, flat at 72 | 0.25% | k=0 | 1,522 | **1,576** | 2,105 | 10.6 | -1,948 | 0.81 | 564 | 525 | 487 | 12 | | |
| S4 all, flat at 72 | 0.1% | k=0 | 6,843 | **6,884** | 8,044 | 12.1 | -4,571 | 0.81 | 2,337 | 2,191 | 2,356 | 80 | 26 | 1.56M |
| S4 all, flat at 72 | 0.25% | k=0.05 | 5,159 | **5,101** | 6,788 | 10.6 | -4,366 | 0.79 | 1,690 | 1,635 | 1,776 | 48 | 15 | 0.86M |
| S4 all, flat at 72 | 0.5% | k=0.05 | 3,234 | **3,096** | 5,947 | 7.4 | -3,795 | 0.70 | 981 | 1,000 | 1,115 | 33 | 14 | 0.58M |
| S4 all, held | 1% | k=0.05 | 1,616 | **1,230** | 11,015 | 1.6 | -14,151 | 0.46 | -255 | 560 | 925 | 5 | 11 | 0.08M |
| H1, enter 56, flat 72 | 0.25% | | 10,112 | **9,683** | 7,730 | 17.7 | -1,384 | 0.91 | | | | 72 | 24 | 1.27M |
| H1, enter 56, flat 72 | 0.5% | | 6,934 | **6,509** | 7,698 | 12.0 | -4,523 | 0.81 | | | | 72 | 24 | 1.27M |
| H1, entered at 60 | 1% | | 582 | **162** | 7,639 | 0.3 | -10,502 | 0.45 | | | | 72 | 24 | |
| H2 drift | 0.25% | | 8,378 | **8,867** | 8,194 | 15.3 | -5,116 | 0.87 | 3,104 | 3,038 | 2,724 | 66 | 11 | 1.16M |
| H2 drift | 1% | | -352 | **149** | 8,289 | 0.3 | -14,073 | 0.53 | | | | 66 | 11 | |

**S1, round 3 alone** (the flat variant, so rounds 1-2 are flattened before round 3):
- h = 0.1%: mean 1,299, hit 99%, worst season −176.
- h = 0.25%: 1,006, hit 92%, worst −90.
- h = 0.5%: 831, hit 83%.
- Rounds 1-2 earn less: hit 40-57% at h = 0.25%.

**"Hold" variants of S1 and S2.** These have the highest train mean, but most of the extra comes from carrying the rounds 1-2 position into later storms (HAVEN in particular). That adds about $2.5k of mean and about $10k of sd. Inside the combined book the extra risk is diversified away: holdout p5 is 10.2k for S6a_H1 against 9.3k for the flat-exit version.

**S5 wire fade:**

| h | thr 0.1% | thr 0.2% | thr 0.3% | thr 0.5% | thr 1.0% (best on train) |
|---|---|---|---|---|---|
| 0.1% | -3,994 | -2,955 | -2,243 | -1,606 | -777 (t -3.3) |
| 0.25% | -7,954 | -5,313 | -3,745 | -2,463 | -1,169 (t -4.9) |
| 0.5% | -14,555 | -9,243 | -6,247 | -3,892 | -1,822 |
| 1% | -27,758 | -17,103 | -11,252 | -6,749 | -3,129 |

Trades per season are 77, 47, 32, 19 and 9 respectively. The house's moves after rumors are not overreactions you can fade at these spreads.

### PnL by symbol (holdout, h = 0.25%, $ per season)

| Strategy | PORT | ISLE | AQUA | GRID | FERRY | RECON | MAT | GEN | HAVEN | REIN | ARCH | REBLD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 hold | 780 | 399 | 244 | 494 | 111 | 237 | 167 | 27 | 1,156 | 149 | 444 | 194 |
| S1 flat | 423 | 165 | 143 | 209 | 31 | 123 | 61 | -6 | 459 | 50 | 168 | 56 |
| S2 flat at 600 | 236 | 75 | 59 | 82 | -1 | 34 | -3 | -41 | 248 | -1 | 68 | -8 |
| S3 | 4,336 | 1,064 | 1,404 | 1,878 | 2,674 | 2,259 | 1,135 | 1,290 | 5,956 | 144 | 3,390 | 2,025 |
| S4 all, flat at 72 | 507 | -77 | -115 | 435 | 316 | 589 | 404 | 507 | 1,723 | -263 | 641 | 433 |
| H1 | 910 | 392 | 576 | 916 | 1,066 | 613 | 460 | 641 | 2,611 | -199 | 1,204 | 495 |
| H2 | 966 | 325 | 463 | 884 | 915 | 523 | 371 | 577 | 2,358 | 0 | 1,073 | 412 |
| **S6R** | 5,316 | 1,931 | 2,344 | 2,845 | 3,359 | 2,538 | 1,504 | 1,608 | **7,929** | -15 | 4,052 | 2,072 |

- HAVEN is the largest contributor everywhere: it has the 9.8% formation jump and the most convex payoff.
- REIN is roughly zero in everything. You can drop it, which also saves actions.

**Where S3's money comes from** (trade-level PnL by entry time, h = 0.25%, holdout):
- Entered 60-118 s: $20.6k (88 actions);
- 120-238 s: $5.5k;
- 240-358 s: $0.6k;
- 360-540 s: $0.9k.

Starting S3 at 74 s instead of 60 s still gives $23.5k. S3's correlation with the clock-only drift trade H2 is only 0.34, so it is not just the house's drift.

## 4. Sensitivity to k and half-spread

Holdout mean, with the train mean in brackets.

**S3, held to settlement**

| h \ k | 0 | 0.1 | 0.2 | 0.3 | 0.5 | 1.0 | 2.0 |
|---|---|---|---|---|---|---|---|
| 0.1% | 33,263 [33,225] | 33,230 [33,229] | 29,551 | 24,772 | 17,657 | 10,003 | 3,133 |
| 0.25% | 25,684 [24,992] | **27,555 [26,991]** | 25,060 [24,424] | 21,305 | 15,611 | 8,885 | 2,535 |
| 0.5% | 18,353 | **21,488 [20,564]** | 20,377 [19,392] | 17,690 | 13,610 | 7,745 | 2,061 |
| 1% | 10,235 | 14,106 [13,032] | **14,433 [13,251]** | 13,149 | 10,945 | 6,051 | 1,517 |

Rounds 1-2 flattened at 568 gives nearly the same results: 26,275 at h = 0.25% and k = 0.1. Fitted per round group on train, the best k is 0.0/0.1/0.1/0.2 for rounds 1-2 and 0.1/0.2/0.5/0.5 for round 3, for h = 0.1/0.25/0.5/1%.

**Other S3 rules** (h = 0.25%, k = 0.1; train / holdout / holdout p5 / actions):

| Rule | Train | Holdout | p5 | Actions |
|---|---|---|---|---|
| Base | 25,792 | 26,275 | 568 | 210 |
| No exit except reversal | 26,324 | 26,947 | 682 | 129 |
| Proportional size, z = 1 | 16,714 | 17,402 | 421 | 538 |
| Proportional size, z = 2 | 10,923 | 11,346 | -344 | 562 |
| Window to 568 | 25,741 | 26,036 | 477 | 216 |
| ¼-limit cap per row | 18,492 | 19,786 | -3,201 | 657 |

Take full size as soon as the threshold is crossed. Proportional sizing costs about 35%.

**S1 (rounds 1-2 flattened at 770)**

| h \ k | 0 | 0.1 | 0.25 | 0.5 | 1 | 2 |
|---|---|---|---|---|---|---|
| 0.1% | 2,481 | **2,702** | 2,406 | 1,832 | 1,467 | 1,357 |
| 0.25% | 997 | 1,772 | **1,882** | 1,447 | 1,148 | 1,039 |
| 0.5% | -370 | 1,121 | **1,486** | 1,174 | 946 | 852 |
| 1% | -1,181 | 654 | **1,029** | 864 | 680 | 642 |

S1 held to settlement at k = 0 gives 5,456 / 4,401 / 3,733 / 2,842 (t 4-6).

**S2 (flat at 600)**

| h \ k | 0 | 0.1 | 0.25 | 0.5 | 1 |
|---|---|---|---|---|---|
| 0.1% | 1,041 | **1,346** | 1,156 | 647 | 247 |
| 0.25% | -476 | 486 | **747** | 425 | 130 |
| 0.5% | -1,544 | 126 | **609** | 374 | 148 |
| 1% | -2,014 | -116 | **355** | 207 | 83 |

**S4, HAVEN+REIN held**: 716 / 666 / 543 / 387 at k = 0; t ≤ 2.9 everywhere.

**S4, all symbols, flat at 72**

| h \ k | 0 | 0.05 | 0.1 | 0.25 | 0.5 |
|---|---|---|---|---|---|
| 0.1% | **6,884** | 6,692 | 5,118 | 756 | 22 |
| 0.25% | 5,141 | **5,101** | 3,731 | 434 | 18 |
| 0.5% | 3,023 | **3,096** | 2,272 | 280 | 12 |
| 1% | 873 | 1,431 | 1,056 | 201 | 3 |

**H1 timing** (h = 0.25%, holdout mean, with one extra row of latency in brackets):

| Entry / exit | 66 | 72 | 80 | 90 |
|---|---|---|---|---|
| Enter at 56 | 7,646 [9,710] | 9,683 [9,862] | 10,186 [10,052] | 10,172 |
| Enter at 60 (on the storm_formed message) | 7,648 [**-832**] | 9,685 [**-679**] | 10,188 [-489] | 10,174 [-564] |

## 5. Robustness (holdout mean, t in brackets)

| Strategy (train choice) | h | Base | Latency +1 row | ¼-limit cap | 60 actions/min | Cap + latency |
|---|---|---|---|---|---|---|
| S1 hold, k=0 | 0.25% | 4,401 (5.1) | 1,494 (1.7) | 2,207 (2.5) | 4,401 | 1,630 |
| S1 flat, k=0.25 | 0.25% | 1,882 (15.8) | **-203 (-9.0)** | | | |
| S2 flat, k=0.25 | 0.25% | 747 (9.7) | **-425 (-24)** | | | |
| S3, k=0.1 | 0.1% | 33,230 | 22,753 | 25,350 | 33,218 | 21,971 |
| S3, k=0.1 | 0.25% | 27,555 (22.6) | 17,597 (15.5) | 21,418 | 27,547 | 18,287 |
| S3, k=0.1 | 0.5% | 21,488 | 12,240 | 17,262 | 21,487 | 14,382 |
| S3, k=0.2 | 1% | 14,433 | 8,381 | 11,912 | 14,433 | 9,845 |
| S4 all, flat at 72 | 0.25% | 5,101 | 5,048 | 5,049 | 5,101 | 5,042 |
| H1 entered at 60 | 0.25% | 9,685 | **-679** | 2,404 | 9,685 | -182 |
| H2 | 0.25% | 8,867 | 8,712 | 2,201 | 8,867 | 2,163 |

S3 at k = 0.2 and h = 0.25% keeps 15,817 with latency +1, 15,075 with +2 and 11,711 with +5 rows.

## 6. S6 combined, with risk limits

Train mean / holdout mean / sd / p5 / hit:

| Variant (h = 0.25%) | Train | **Holdout** | sd | p5 | Hit | Latency +1 | ¼ cap |
|---|---|---|---|---|---|---|---|
| S6a: S1/S2 held, S3, S4 | 29,962 | 30,588 | 16,897 | 4,009 | 0.95 | 19,276 | 25,494 |
| S6s: S1/S2 flat exits, S3, S4 | 28,079 | 28,542 | 16,723 | 3,650 | 0.95 | 18,310 | 24,416 |
| S6s without S4 | 28,991 | 29,222 | 16,674 | 986 | 0.95 | 15,561 | 20,555 |
| S6s + H1 at 60 | 33,842 | 33,868 | 15,952 | 9,269 | 0.99 | 14,880 | 21,302 |
| S6a + H1 at 60 | 35,417 | 35,480 | 16,035 | 10,197 | 0.99 | 19,069 | 24,849 |
| **S6R = S6a + H1 at 56** | **35,410** | **35,483** | 16,029 | **9,982** | 0.99 | **25,779** | 24,849 |
| S6R, drawdown stop 6k | 31,756 | 32,563 | 15,692 | 6,277 | 0.98 | | |
| S6R, drawdown stop 10k | 34,506 | 34,762 | 16,349 | 8,661 | 0.98 | | |
| S6R, drawdown stop 20k | 35,341 | 35,470 | 16,061 | 9,982 | 0.99 | | |
| S6a+H1, σ cap 8k | 28,049 | 28,077 | 13,780 | 6,030 | 0.98 | | |
| S6a+H1, σ cap 15k | 33,453 | 33,768 | 15,396 | 10,294 | 0.99 | | |

**S6R by half-spread** (train-chosen, holdout):

| h | Train | **Holdout** | sd | p5 | Hit | R1 (hit) | R2 (hit) | R3 (hit) | Latency +1 | ¼ cap | Actions | max/60s (p95) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.1% | 42,958 | **42,527** | 16,866 | 15,381 | 0.99 | 14,314 (.94) | 14,308 (.95) | 13,905 (.93) | 30,748 | 28,816 | 388 | 94 (79) |
| 0.25% | 35,410 | **35,483** | 16,029 | 9,982 | 0.99 | 11,922 (.94) | 12,137 (.91) | 11,424 (.89) | 25,779 | 24,849 | 245 | 84 (63) |
| 0.5% | 27,343 | **27,308** | 15,075 | 3,574 | 0.97 | 8,785 (.89) | 9,317 (.85) | 9,205 (.88) | 18,980 | 19,827 | 175 | 75 (55) |
| 1% | 17,651 | **18,154** | 13,456 | -3,375 | 0.93 | 5,325 (.82) | 6,368 (.77) | 6,461 (.72) | 10,652 | 14,432 | 76 | 31 (27) |

- **Season PnL quantiles** at h = 0.25%, holdout (min / p1 / p5 / p25 / median / p75 / p95 / max): -11,968 / 1,499 / 9,982 / 25,243 / 35,550 / 45,403 / 57,778 / 105,061.
- **Turnover** at h = 0.25%: 245 actions per season, about 113k shares, $5.1M notional.
- **Rate limit.** At 160 or 180 per minute, nothing is ever deferred. At 60 per minute about 1.3 orders per season are deferred, which costs almost nothing. With the ¼ cap the limit binds: 180 per minute is reached and about 5 orders per season are deferred at h = 0.1%.

## 7. Recommended parameters

These map onto `config.STRAT` and are also in `fits/backtest.json`.

| h | k1 (S1, 740-778, held) | k2 (S2, 570-598, held) | k3 rounds 1-2 | k3 round 3 | Formation leg | S4 |
|---|---|---|---|---|---|---|
| 0.1% | 0 | 0 | 0.0 | 0.1 | on | off |
| **0.25% (base case)** | 0 | 0 | 0.1 | 0.2 | on | off |
| 0.5% | 0 | 0 | 0.1 | 0.5 | on | off |
| 1% | 0 | 0 | 0.2 | 0.5 | off | all symbols, k=0.05, 0-58, held |

**Details for the bot:**
- **Formation leg.** Enter at real 56 s at full limits (long PORT ISLE AQUA GRID FERRY HAVEN REIN ARCH, short RECON MAT GEN REBLD). Flatten at 72. S3 resumes at 74.
- **Sizing.** Take the full limit as soon as the threshold clears. In skeleton terms, set z_full ≤ k_sigma rather than 3.0. Exit when fair crosses the far side of the quote.
- **Threshold.** The skeleton's `k_sigma` of 1.5 is far too conservative: at k = 1.5, S3 holdout is $4.5k against $27.6k.
- **Snipes.** If testnet latency cannot reliably beat the house's 3-4 s reaction, switch S1/S2 off (or make them hold-only) in rounds 1-2. S1 in round 3 costs little to keep.
- **Drop S5 and REIN.**
- **Circuit breaker.** Use a $20k per-round drawdown stop.
- **Action reserve.** Keep about 24 actions in reserve before 570 and 740.

## 8. What can and cannot be known in real time

**Exact or verified in this backtest:**
- The engine at t uses only messages with seq ≤ the current one (audit max difference 5e-5).
- Settlement is known exactly from 740 s in round 3.
- Formation always happens at 60 s, so the formation leg needs no news.

**Unknown:**
- **Spreads and depth.** Hence the h sweep. Results scale roughly linearly down to zero at h ≈ 1% for H1 and H2.
- **Whether we fill on the last stale row.** S1, S2 and an H1 entered at 60 are races against the house's 3-4 s reaction and against other teams.
- **How the house market makers skew quotes** as we take full size repeatedly. The ¼-cap variant suggests losing about 30% of S6R.
- **Announcements.** None exist in the research data.
- **Whether the house's formation jump and in-storm drift persist live.** They come from the same simulator, are stable across 100-season blocks (house verification), and train and holdout agree.

**Other caveats:**
- The engine's physics, sources and settlement constants were fitted on seasons 1-400. Train-season engine values are therefore mildly in-sample, but train and holdout PnL agree within about 3%.
- A market order is counted as one action. If marketable limit orders leave residuals, cancels add actions.
- PnL assumes every order fills completely at the house mid ± h.
- ETF create/redeem arbitrage is untested.

## 9. Files

All in `/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/backtest/`:
- `backtest.py` — simulator, strategy builders and `recommended(h)`. To reproduce a result: `python3 -I backtest.py <SP> holdout 401 600 0.0025 S6R`, which gives 35,483.
- `fvcache.py` — engine cache. Built with `python3 -I fvcache.py <SP> 401 600 holdout` and `python3 -I fvcache.py <SP> 1 400 loso`. Output is in `cache/`.
- `check_cache.py` — look-ahead and state-leak audit.
- `run_experiments.py` — 972-config sweep with grids and selection.
- `stage2.py`, `s2tables.py`, `diag.py`, `h1t.py`, `final.py`, `report_tables.py` — robustness runs, the combined strategy, diagnostics and the tables above.
- `results/` — `sweep_train.json`, `sweep_hold.json`, `stage2.json`, `final.json`, `h1_timing.json`, `diag.json`, and the markdown tables.
- Recommended parameters: `/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/fits/backtest.json`.
