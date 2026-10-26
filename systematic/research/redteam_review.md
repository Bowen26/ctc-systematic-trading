# Red-team review: Systematic Equities (Hurricane Season) engine, backtest and playbook

All numbers below come from scripts I ran. Unless a line says otherwise, they are on holdout seasons 401-600, $ per season, at full limits, measured on the house consensus in `market.csv`. My scripts and outputs are in `/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/redteam/`.

## 0. Bottom line

- **No look-ahead and no off-by-one bug.**
  - I reproduced the backtest exactly: S6R at h = 0.25% gives 35,483, and my rewritten simulator gives the same figure.
  - The timing is right: the decision uses row ⌊t/2⌋, which still shows the pre-news price, and the fill is at row ⌊t/2⌋+1.
- **The $35.5k comes from the fill model, not from any code bug.**
  - The backtest fills the full limit in one order at consensus ± h, with no depth limit, no inventory skew and no competition.
  - With a depth cost γ per full-limit clip, S6R falls to 23.6k at γ = 0.25%, 14.7k at 0.5% and 0.8k at 1%.
  - If orders are worked in ¼-limit clips, it holds at 28.3k, 25.3k and 22.1k.
- **Realistic planning figure, still on consensus and before crowding: about $22-25k.**
  - Setup: worked clips, γ = 0.5%, and report snipes lost to faster teams. That gives 24.5k (p5 2.9k).
  - Adding 2 s of latency on every order gives 22.6k (train 21.9k).
- **The two analyses mostly measured different things.**
  - The playbook is right that the house equals a full posterior at the round opens, at storm end and after the preliminary report.
  - It is wrong to skip the storm phase. The house has a clock-predictable formation over-reaction and in-storm drift worth **$19.2k with no engine at all** (train 18.1k), and the engine adds more on top.
  - The playbook's ETF lead-lag reproduces on consensus. But S6R already earns $6.1k from ETFs, so the lead-lag adds only about $1.8k at 25 bp (about $5.0k at 10 bp).
- **Report snipes in both documents are a pure race.** The oracle bound at 10 bp is $5.6k if we fill 2 s after the report, and $1.0k if we fill 2 s later than that.

## 1. Bugs and model defects found

| # | Severity | Defect | Evidence (my runs) | Fix / corrected number |
|---|---|---|---|---|
| B1 | **Major (fill model)** | Full limit fills in one order at consensus ± constant h, with infinite depth and no impact. The README says `market.csv` "is not a traded price"; the packet says the house skews against inventory. | S6R single-shot with linear depth cost γ·\|q\|/L: γ 0 → 35,483; 0.25% → 23,557; 0.5% → 14,661; 1% → 798. H1 alone is about −0.1k at γ 0.5% (−6,655 + 6,511). | Work orders in ¼-limit clips per 2 s row, with H1 entered over 50-56 s and exited over 72-78 s: 28,273 / 26,870 / 25,349 / 22,102 for γ 0 / 0.25 / 0.5 / 1% (train 27,621 / 26,208 / 24,717 / 21,530). |
| B2 | **Major (competition)** | S1/S2 assume we beat both the house's 4 s reaction and every other team. | Losing the report race by one row costs 4.7k: 35,483 → 30,769, with S1 −179 and S2 −88. | Plan with report lat+1. Worked + γ 0.5% + report lat+1 = **24,458**; adding lat+1 on all orders = 22,605. |
| B3 | Moderate (test flaw) | The backtest's "¼-limit cap → 24.8k (−30%)" robustness test is not an impact test. H1 is a single decision at 56 s, so the cap enters only ¼ of it, and the rest of the H1 position is handed to S3. | I reproduce 24,849: H1 −399 + H1x 1,180, against H1 7,053 uncapped. | Properly worked H1: 28,273 at γ 0. |
| B4 | Moderate (attribution) | The "components" are trade-level PnL to settlement, not stand-alone values, so they overstate what you lose by dropping a part. | Dropping H1: 30,440, so the marginal is **5.0k**, not the 7.1k attributed. Dropping S1+S2: 31,569, marginal **3.9k**, not 4.4k. Dropping all ETF trading: 29,359, marginal **6.1k**. | Use the marginal values when choosing what to switch off. |
| B5 | Moderate (double count across the two documents) | Playbook ETF lead-lag (~$9k) plus snipes (~$4.8k) cannot be added to S6R. They use the same ARCH/REBLD limits and the same report events. | S6R without ETFs (29,359) + lead-lag at 25 bp (ARCH 5,927 + REBLD 1,956) = 37,242, only +1,759 over S6R. At 10 bp: 35,192 + 12,340 = 47,532, which is +5,005. | Run one ETF engine, not both. |
| B6 | Minor (leak) | Engine `sd_floor` (0.003-0.033) was taken from holdout settlement RMSE (`chk_settle.py`). The train (loso) engine uses physics and source fits from 1-400, so it is in-sample for train; the report already says so. | Floors are ≤ 3 cents against k·sd thresholds of dollars; train and holdout agree within 1%. | Negligible. Refit the floors on train for hygiene. |
| B7 | Minor | Spread is constant across phases, although the house "widens spreads when risk rises". | Storm half-spread = max(h, 0.5·σ₂ₛ) → 34,364; 3× spread at reports → 33,791. | S6R is robust to wider spreads; depth (B1) is what matters. |
| B8 | Minor (rate limit) | "Rate limit never binds" is true only for single-shot fills. | Worked clips reach 180 in a 60 s window (650-800 actions per season). Capped at 150/min: 24,406; at 120/min: 23,922. | Keep a 120-150/min bot budget. |

**Checked and clean:**
- **Event times.** Identical in all 1,800 storms: formed 60, dissipated 540, preliminary 570, final 740 (`rt_timing.out`).
- **Row timing.** Row i is real second 2i (asserted for every row). The report row still shows the pre-news price: absorbed fraction 0.00 at +0 s, 0.02-0.03 at +2 s, 0.87-0.90 at +4 s.
- **Cache.** It has a t = −1 pre-message entry, so decisions in round 1 at 0-58 s do not see formation. The library is filtered by season (`fairvalue.py` line 97). Strategy k values were chosen on loso train.
- **Quote noise.** Fills at the next row do not select on noise. The noise is stationary AR(1): 8.3 bp per 2 s, saturating near 20 bp by 40 s (σ ≈ 14 bp, half-life ≈ 7 s).

## 2. Corrected results, holdout, h = 0.25% (train in brackets)

| Variant | Mean | sd | p5 | Hit |
|---|---|---|---|---|
| S6R as reported (repro) | 35,483 [35,410] | 16,029 | 9,982 | 0.99 |
| Report race lost (+1 row at 570/740) | 30,769 [31,080] | 15,567 | 7,057 | 0.98 |
| +1 row on all orders | 25,779 [25,290] | 15,171 | 3,541 | 0.96 |
| Single-shot, γ 0.5% | 14,661 [14,249] | 15,183 | −8,472 | 0.83 |
| Single-shot harsh (0.5σ storm spread, γ 0.5%, report race lost) | 9,786 [9,607] | 14,647 | −12,194 | 0.76 |
| **Worked ¼ clips, γ 0.5%, report race lost** | **24,458 [23,865]** | 13,638 | 2,904 | 0.97 |
| Same + 1 row latency everywhere | 22,605 [21,906] | 13,719 | 604 | 0.95 |
| Same at 120 actions per minute | 23,922 | 13,711 | 2,367 | 0.95 |
| Worked, γ 0.5%, no ETFs or REIN (ETFs left to a separate lead-lag) | 20,192 | 11,373 | 1,851 | 0.97 |
| Clock-only, no engine (long at 56 s, flip short islands/HAVEN/ARCH at 74 s, flat at 538 s) | 19,183 [18,106] | 14,066 | −9,693 | 0.90 |
| Clock-only, harsh single-shot | 627 [−477] | 14,153 | −28,170 | 0.56 |

At h = 0.1%, S6R is 42,527 but falls to 7,638 at γ 0.5% single-shot. The lower-k settings trade more, so they are more exposed to depth.

## 3. Reconciliation table

| Claim | Source | Verdict | Corrected number |
|---|---|---|---|
| Settlement is a closed-form law of public data to ~1 cent | Playbook 3.1; engine 5.1 | **Confirmed for RMSE; the maximum error is larger** | Holdout RMSE / max (cents): PORT 1.2/5.9, GRID 1.2/5.2, FERRY 1.5/7.7, GEN 2.0/9.9, other stocks ≤ 0.4/1.2, ARCH 3.3/13.3, REBLD 2.1/10.6. The playbook's ε (PORT 0.04, GRID 0.04, FERRY 0.06, GEN 0.07) is below these maxima. The cost is cents per share, but set ε ≥ the max. |
| The house is about as accurate as a full posterior | Playbook 1, 3.7 | **True at opens, storm end and after the preliminary report; false mid-storm** | Round-3 RMSE vs settlement, engine / house / house with a train-fitted clock correction: T = 240 PORT 4.47/5.68/5.44, HAVEN 4.60/6.23/5.62; T = 360 PORT 2.82/4.32/4.00. Equal at T = 0 (11.05/11.15) and T = 600 (2.18/2.18). |
| Mid-storm edge: $24-27.6k (backtest) vs a $1.9k "crude replica, P2" (playbook) | Both | **Backtest right on consensus; the playbook missed the house's clock bias** | Formation jump, rows 29→37 (58→74 s), three 200-season blocks: HAVEN +9.6/+10.2/+9.7%, ARCH +3.2/+3.4/+3.3%, rebuilders −1.1 to −1.5%. Drift, rows 37→269: HAVEN −7.3/−6.8/−7.9%, PORT −2.0/−2.2/−2.8%. Clock-only is worth 19.2k; the engine adds about 12.4k more (S6R without snipes, 31.6k). Realistic: about 19-21k for S3 + H1. |
| Stocks react to reports 4 s late | Both | **Confirmed** | 0.00 at +0 s, about 0.02 at +2 s, 0.87-0.90 at +4 s, 0.98 at +16 s, in all 6 report × round cells. |
| ETF consensus = basket 6 s earlier; ETFs 10 s late | Playbook 3.5 | **Confirmed with a qualification** | ETF_i − NAV_{i−3}: RMS 0.476 for ARCH (16 bp, max 2.69) and 0.380 for REBLD (18 bp, max 1.91). At lag 2 or 4: 1.27 / 0.54, with maxima of 35 / 11. The residual is its own AR(1) noise (lag-1 autocorrelation 0.85) and is the same pre-storm, mid-storm and post-storm. Reports: ETFs show 0.92-0.96 of the move at +10 s. |
| ETF lead-lag worth ~$9k at 10 bp | Playbook 4.2, 5 | **Reproduced on consensus, but overlaps S6R; the book lag is unproven** | Standalone, 0.5% threshold: ARCH 8,682 + REBLD 3,658 = **12.3k at 10 bp**; 7.9k at 25 bp; 5.4k at 40 bp. +1 row latency: 8.2k at 10 bp. +2 rows: −2.7k. Train agrees within 1%. Incremental over S6R: **+1.8k at 25 bp, +5.0k at 10 bp.** |
| Report snipes ~$4.8k at 10 bp | Playbook; backtest S1+S2 flat 4.05k at 10 bp | **Consistent on consensus; a pure latency race** | Oracle bound, filling 2 s after the report: 5,646 at 10 bp (preliminary 2,286; final rounds 1-2 2,154; round 3 to settlement 1,206), 4,271 at 25 bp. Filling 4 s after: 1,010 at 10 bp and 516 at 25 bp. Backtest with +1 row: S1 −203, S2 −425. |
| Insurer open mispricing (REIN/HAVEN) | Playbook +357 HAVEN round 2 (t 2.6); backtest S4 666 (t 2.1) | **Weak; only the REIN level bias is stable** | House REIN open is too high by 0.38 in round 1 and 0.33 in round 2 (600 seasons, t −3.4/−3.1; train −0.30, holdout −0.54), worth about $50-75 per season. HAVEN open bias is +2.44 on train but +0.54 on holdout (t 0.5). Engine-guided, holdout: HAVEN 155/302/223 by round (t 0.5-1.2), REIN 65/51 (t 1.6/1.4). |
| REIN riskless ask at ≥ 90.02 | Playbook | **Confirmed, small** | The house consensus goes above 90.02 in 86-88% of seasons; the perfect-timing bound is $119-130 per season. |
| Wire fade loses; snipes die with +2 s; H1 at 60 s depends on latency | Both | **Agree** | Not re-run except the latency rows above. |

## 4. ETF lead-lag stress test

**Is the 3-row lag an artifact of how the consensus is built? Yes.**
- The house's ETF consensus never reacts to news directly. It is the basket of the house's *own* stock consensus 6 s earlier, plus an independent, persistent quote noise (σ ≈ 16-18 bp, AR(1) 0.85).
- That is why the k = 3 residual is the same before, during and after the storm, and why ETFs reprice exactly 3 rows after the stocks at every report.
- On the consensus the edge is almost deterministic, because ETF_{i+3} is close to NAV_i. That explains the t-stats of 40-50.

**It is tradable only if all of the following hold in the live books:**
1. The house quotes the ETF around this lagged consensus, not around a live NAV. On the testnet this should be true, because the house "quotes around the prices of that season". Live uses the same house.
2. The ETF half-spread plus our impact is no more than about 25-40 bp. The edge holds at 40 bp on consensus: ARCH 4.25k, REBLD 1.1k.
3. Our order reaches the book within 2 s of the stock move. +1 row still pays; +2 rows loses.
4. No other team, and no house flow, re-aligns the ETF book first. Free create/redeem makes this the most obvious arbitrage in the packet, so we are only paid if the stale house size is still there when we arrive.
5. The house market makers do not pull or widen ETF quotes when the basket moves.
6. Price protection does not reject orders priced at the stale ETF level.

**Cheapest testnet check (zero risk):**
- Record the quotes and orderbook channels for ARCH, REBLD and their 8 components through real seconds 56-80 of one round.
- Identify the replayed season from the market-clock date and line the recording up against that season's `market.csv`.
- Formation is the best test event:
  - it happens at a fixed time (60 s) in every round;
  - the components move at about 64 s and ARCH should move at about 70 s;
  - the mean move is +3.3% for ARCH and −1.3% for REBLD.
- If the ARCH touch is still stale at 66-68 s and shows size, then do 1-lot marketable round trips (buy at about 66 s, sell at about 74 s; 4 actions, under $20 of risk).
- The same recording also gives the stock spreads, depth, whether the 14 bp noise is in the book, and how the book behaves before formation. That covers most of B1.

## 5. What could differ in the live season

| Difference | Effect | Strategy change |
|---|---|---|
| Announcements (none in the research data; the house takes about 45 s to adjust) | The engine is stale for the named symbol. S3 at full size would fade the house's correct repricing. | Freeze automatically: the named symbols, their ETF, and linked names (HAVEN↔REIN↔MAT via L; GRID→GEN; PORT/GRID/FERRY→RECON). Trade toward an analyst override at ≤ 50% of the limit within the 45 s. Gate the round-3 settlement orders. |
| Other teams | The formation jump (HAVEN +9.8% at 64 s in every storm) and the drift are visible at a glance in `market.csv`. Crowding pre-empts them through house inventory skew. Reports and the ETF arb become races. | Run H1 at ≤ ½ size, worked over 48-56 s. Compare our round-1 fills with the consensus and rescale for rounds 2-3. Assume the report race is lost (plan with the report-lat+1 numbers). Price every edge off the book, not the consensus. |
| House quoting (depth, skew, widening, price protection) | This dominates the PnL: B1 breaks even at about 1% single-shot. | Use ¼-limit clips with limit prices. Re-estimate γ from round-1 fills. Budget 120-150 actions per minute. |
| New storms | Same simulator, but only 35 erratic storms exist to learn from. The season distribution is wide (holdout worst −12k at base, p5 about 0.6-2.9k realistic). | Keep the $20k per-round circuit breaker. Do not add variance trades (S4). |
| Clock and pauses | Live pauses differ, and it is unknown whether resting orders survive them. | Key H1 on market time 09:58:00 of each round. Reconcile positions after each pause. |
| Case or parameter changes | REIN's layer top is unidentified (no season with L above 1,766). Live starting prices differ from the research means (e.g. HAVEN 39.55 vs 38.14), but the open is uncorrelated with settlement (corr −0.01), so there is no signal in it. | Diff `/cases/hurricane.json` at startup. |
| Bot skeleton defaults | `config.py` has k_sigma 1.5, z_full 3.0, a $6k drawdown stop and no formation leg; the backtest recommends otherwise. | Change these before the testnet runs. |

## 6. Verdict per strategy

| Strategy | Verdict | Why (holdout, 25 bp) |
|---|---|---|
| S3 mid-storm engine taker | **GO (core), worked in clips** | Survives +2 s (S3 18.8k of 24.0k), wider storm spreads and γ 0.5% when worked (about 19k). Freeze on any announcement. |
| H1 formation leg | **TEST-FIRST, then GO at ≤ ½ size, worked** | Marginal value 5.0k. About 5.1k worked at γ 0.5%, but about 0 single-shot. Most exposed to crowding. |
| Clock-only drift (no engine) | **Fallback only** | 19.2k at base fills, about 0.6k with harsh single-shot fills. |
| S1 round-3 settlement positioning | **GO, small, cheap** | 1.2k if we are first, about 0.25k if 2 s late. Downside is near zero because settlement is exact. |
| S1 rounds 1-2 final snipe and S2 preliminary snipe | **TEST-FIRST, default OFF** | Negative with one row of latency. Needs a measured report-to-ack latency well under 2 s and evidence that we win the race. |
| ETF lead-lag (playbook) | **TEST-FIRST (book-lag check)** | If the book lags and the half-spread is ≤ 25 bp, use it instead of S3's and H1's ETF legs: +1.8k. |
| REIN riskless ask ≥ 90.02 | **GO (trivial)** | $50-130. Take REIN out of H1 and S3 (worth −15 there; S6R without ETFs and REIN is 29,374 vs 29,359 with REIN). |
| S4 insurer opens | **NO-GO** | t ≤ 1.6 on holdout, sd $2.6-4.1k per leg. |
| S5 wire fade | **NO-GO** | Both analyses agree it loses. |

## 7. Files

All in `/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/redteam/`:

- **Data extract:**
  - `rt_extract.py` → `rt_data.json`. The extract came from an earlier attempt; I reviewed the script and reused its output.
- **Checks:**
  - `rt_timing.py` → `rt_timing.out`: event times and report absorption.
  - `rt_house.py` → `rt_house.out`: ETF vs lagged basket, noise, clock patterns, open bias, REIN bound, reaction lags.
  - `rt_noise.py` → `rt_noise.out`: quote-noise persistence.
  - `rt_evh.py` → `rt_evh.out`: engine vs house vs clock-corrected house, and settlement-law error.
  - `rt_snipe.py` → `rt_snipe.out`: oracle snipe bounds.
  - `rt_open.py` → `rt_open.out`: insurer trades at the round opens.
  - `rt_etf.py` → `rt_etf.out`: ETF lead-lag PnL.
- **Re-run backtest:**
  - `rt_bt.py`: the corrected simulator. It imports the team's `backtest.py` and reproduces 35,483.
  - Outputs: `rt_bt_core.out`, `rt_bt_core_h.out`, `rt_bt_core_train.out`, `rt_bt_depth.out`, `rt_bt_noetf.out`, `rt_bt_worked.out`, `rt_bt_worked2.out`, `base_s6r.out`.