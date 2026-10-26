# Mario Markets cheat sheet

**How it's won:** the money is in the **1000-lot score contracts** (#1, #2, Top-2). At full size, 1 point of
mispricing = 1,000 of PnL; the same point is worth 200 on a full series-winner position and 100 on a race winner. The
biggest unknown is how good the 4 humans are against the 8 CPUs: it moves fair Top-2 anywhere from about 66 to 97.
**Race 1 is the first real evidence: reprice the Series-2 score contracts in the first minute after it.** Then price
#1 and Top-2 *above* the naive average (the order-statistic premium), price series-winner contracts from the lead
tables, take every arbitrage, and **never leave quotes resting while a race runs.**

## Rules that matter

| Item | Rule |
|---|---|
| Points per race (overall place of 12) | 1st 15, 2nd 12, then **13 − place** (3rd 10 ... 12th 1) |
| Race winner (limit 100) | Best-placed **human** = 100, even if a CPU wins the race. The 4 contracts always sum to exactly 100 |
| Series winner (limit 200) | Highest 4-race total = 100. **Tied leaders each settle 50**, so a 3-way tie pays 150 in total |
| #1 / #2 / Top-2 (limit 1000) | Settle to the best human's series points, the 2nd best's, and their sum. 45 and 45 give 45 / 45 / 90 |
| Characters | Daisy, Peach, Birdo and Yoshi have identical stats and the same kart. A favourite-character premium is noise |

## Pocket card (memorise)

| Item | Value |
|---|---|
| Prior fair values | Before Race 1: race and series winner 25. **#1 ≈ 45, #2 ≈ 37, Top-2 ≈ 82** (sd 13.5). Don't bet the level yet |
| Robust prior range | #1 36-52 · #2 29-45 · **Top-2 65-97** · #1 − #2 spread 5-10. A price inside the range is a bet, not an edge |
| Selection premium, series start | **#1 = 4m + 8, Top-2 = 8m + 10**, where m = expected points per human per race (m ≈ 6.8 if humans are no better than the CPUs, 8.5 if the average human finishes 5th, 10.5 if strong). Uneven skill adds about +2 / +3 |
| Human level | m̂ = 8.5 + β(x̄ − 8.5), x̄ = average human points per race so far. **β = 0.45, 0.6, 0.7, 0.75** after races 1-4 (then 0.8-0.85) |
| One human's skill | m_i = m̂ + γ(x̄_i − x̄). **γ = 0.25, 0.4, 0.5, 0.55** after races 1-4 (then 0.6-0.7) |
| Series-2 Top-2 | During Series 1: **82 + k(x̄ − 8.5), k = 3.1, 4.4, 5.1, 5.5** after races 1-4 |
| Two-human duel | Leader ≈ 100·Φ(lead / (5.5·√races left)) |
| Locks | Only with one race left: a **lead of 15+ locks** the series (14 can only tie) |
| Finish line | Pre-loaded tickets **buy ≤ 90, sell ≥ 10**. Never market orders |

## Right after Race 1: the key repricing (T = the four humans' total Race-1 points)

| Race 1 total T | Read | Series-2 action (Series-1 #1 / Top-2 move about 0.75× as much) |
|---|---|---|
| ≥ 42 | Strong humans | S2 Top-2 fair 88-91, #1 48-49. **Buy Top-2 offered ≤ 85, #1 ≤ 45.5** |
| 35-41 | Moderately strong | Top-2 fair 83-87. Trade only gaps ≥ 4 |
| 28-34 | Little information | Stay near 78-82. Check whether one human dominates |
| 22-27 | Weak-ish | Top-2 fair 73-77. **Sell Top-2 bid ≥ 82** |
| ≤ 21 | Weak, or 1 strong + 3 weak | Top-2 fair 56-73. **Sell Top-2 bid ≥ 78, #2 bid ≥ 36** |

- **Size:** immediately 30-40% of the limit on S2 Top-2, and 20-30% each on S2 #1 and #2. Add up to 60% after Race 2 if it confirms; cut back if it doesn't.
- **One dominant human** (e.g. places 1, 10, 11, 12): buy that human's S2 series contract up to fair − 5 (fair ≈ 59), ≤ 50-100 lots until Race 2 confirms. After a 1, 4, 7, 10 race their fair is ≈ 42.

## Mid-series score fair values

Notation: r = races left; s1 ≥ s2 ≥ s3 ≥ s4 = current scores; a, b, c = gaps of 2nd, 3rd and 4th behind the leader;
m_1, m_2 = the top two humans' own expected points per race.

- **E[#1] = s1 + r·m_1 + C(a) + 0.45·[C(b) + C(c)]**
- **E[Top-2] = s1 + s2 + r·(m_1 + m_2) + [C(b − a) + 0.5·C(c − a)] × (1 + 0.55·C(a)/C(0))**
- **E[#2] = E[Top-2] − E[#1]**

Selection premium versus "score + races left × m" (S2, equal skill):

| | Series start (r = 4) | All tied, r = 3 | All tied, r = 2 | All tied, r = 1 | Leader 13+ clear, r = 1 |
|---|---|---|---|---|---|
| #1 | +8.2 | +7.1 | +5.9 | +4.5 | ≈ 0 |
| Top-2 | +10.4 | +9.0 | +7.4 | +5.5 | – |

<div style="break-inside: avoid">

C(g), the expected amount a rival g points behind finishes ahead of you. Pick the level from m̂ (S1 = humans no better
than CPUs, m ≈ 6.8; S2 = average, 8.5; S3 = strong, 10.5) and interpolate between levels:

| r | Level | g = 0 | 1 | 2 | 3 | 4 | 5 | 6 | 8 | 10 | 12 | 15 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | S1 | 2.48 | 1.98 | 1.55 | 1.19 | 0.89 | 0.65 | 0.46 | 0.21 | 0.08 | 0.02 | 0 |
| 1 | **S2** | **2.33** | **1.83** | **1.40** | **1.03** | **0.75** | **0.52** | **0.35** | **0.14** | **0.04** | **0.01** | **0** |
| 1 | S3 | 1.91 | 1.41 | 0.98 | 0.64 | 0.42 | 0.23 | 0.12 | 0.02 | 0.00 | 0.00 | 0 |
| 2 | S1 | 3.38 | 2.91 | 2.48 | 2.09 | 1.75 | 1.45 | 1.18 | 0.76 | 0.47 | 0.27 | 0.10 |
| 2 | **S2** | **3.14** | **2.67** | **2.25** | **1.87** | **1.54** | **1.25** | **1.00** | **0.61** | **0.35** | **0.19** | **0.06** |
| 2 | S3 | 2.52 | 2.05 | 1.65 | 1.30 | 1.00 | 0.76 | 0.56 | 0.27 | 0.11 | 0.04 | 0.00 |
| 3 | S1 | 4.13 | 3.65 | 3.21 | 2.80 | 2.43 | 2.10 | 1.80 | 1.29 | 0.90 | 0.61 | 0.32 |
| 3 | **S2** | **3.84** | **3.36** | **2.92** | **2.52** | **2.16** | **1.84** | **1.55** | **1.08** | **0.72** | **0.46** | **0.22** |
| 3 | S3 | 3.08 | 2.60 | 2.18 | 1.80 | 1.47 | 1.19 | 0.94 | 0.57 | 0.32 | 0.17 | 0.05 |

</div>

## Series-winner value: leader against one rival (average humans, other two out of contention)

| Lead | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 12 | 14 | 16 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 race left | 50 | 53 | 60 | 68 | 74 | 80 | 85 | 89 | 93 | 95 | 97 | 99 | 99.9 | 100 |
| 2 races left | 50 | 55 | 60 | 64 | 69 | 73 | 77 | 81 | 84 | 87 | 90 | 94 | 96 | 98 |
| 3 races left | 50 | 54 | 58 | 62 | 66 | 70 | 73 | 76 | 79 | 82 | 85 | 89 | 93 | 95 |

- **The rival** is worth 100 minus the leader. **Strong humans:** leads are stickier; with 1 race left, a lead of 4 → 80, 6 → 91, 8 → 98.
- **A leader who has also looked stronger:** +3 to +6.
- **Chasers bunched together:** don't multiply duel values; use the full matrices in the playbook (§3.7 B).

## Hard bounds and arbitrage (risk-free when quotes break them)

- **Bounds:** #1 is 10-60, and **56, 58 and 59 are impossible**. #2 is 8-54. Top-2 is 20-108, and **107 is impossible**.
- **Mid-series:** #1 ∈ [s1 + r, s1 + 15r]; Top-2 ∈ [s1 + s2 + 3r, s1 + s2 + 27r].
- **Out of reach:** one race moves two humans apart by at most 14, so a human trailing by more than 14r is worth 0.

**Arbitrage.** On multi-leg trades, do the stale leg first, sized to what you'd hold if the other legs miss.

| Relationship | Violation → trade |
|---|---|
| **Race basket = 100** | Sum of bids > 100: sell all 4. Sum of asks < 100: buy all 4 |
| **Series basket ≥ 100** | Sum of asks < 100: buy all 4. Sell only at ≥ 101 (≥ 102.5 if humans are strong), ≤ 50 baskets: a 3-way tie costs 49 per set |
| **Top-2 = #1 + #2** | Top-2 bid > #1 ask + #2 ask: sell Top-2, buy both. And the reverse |
| **#1 ≥ #2** | #2 bid > #1 ask: buy #1, sell #2 |
| **Same human, future races** | Equal fair value: buy the cheapest, sell the richest |
| **Exit trick** | A long race contract plus the other three contracts of that race is a riskless 100. When the bid is thin, buy the other three instead of selling yours |

## Phase plan

#### 1. Before Race 1

- **Scan for arbitrage.** Probe the house quotes: #1 quoted near 36 (the naive 4 × 9) means a big structural edge all day.
- **Quote wide and small,** both series the same:

| | Race winner | Series winner | #1 | #2 | Top-2 |
|---|---|---|---|---|---|
| Quote | 21 / 29 | 20 / 30 | 40.5 / 49.5 | 33 / 41 | 76 / 88 |
| Size (lots) | 10-25 | 10-25 | 25-100 | 25-100 | 25-100 |

- **#1 − #2 spread:** buy at ≤ 4.5, sell only at ≥ 10.5, ≤ 300 lots.
- **Fan premia:** sell any character's race contract bid at ≥ 30.
- **60 s before the green light: cancel every quote** (nearly all depend on Race 1). Only stink quotes ≥ 8 points away on future contracts may stay.

#### 2. During a race

- The Spotter calls places every ~5 s using quadrant codes ("Y2 D5 P7 B11").
- If score contracts trade live, Gun B reprices #1 and Top-2 at the start of the final lap. Trade edges ≥ 3, ≤ 200-300 lots in Series 1.
- **Finish line:** buy ≤ 90 / sell ≥ 10. Go to 95/5 only with two-person confirmation and no busts.
- When the last human crosses, score contracts = realised points + formula. Hit quotes ≥ 2 off.

#### 3. Between races (aim for 60 s)

1. Record all four places.
2. Update m̂ and m_i.
3. Reprice current-series scores (formulas above) and hit stale quotes ≥ 2 off at 30-50% of the limit.
4. Reprice Series-2 contracts.
5. Series winner from the lead table: edges ≥ 5 at 50-100 lots.
6. Scan for arbitrage.
7. Requote, then cancel before the green light.

| Half-spread | Race winner | Series winner | #1 / #2 | Top-2 | Series 2 |
|---|---|---|---|---|---|
| After Race 1 | ±3 | ±4 | ±2 | ±3 | ±4 |
| After Race 3 | ±3 | ±2-3 | ±1 | ±1.5 | ±3 |

- **Fade the hot human:** if the last winner's next race is bid 45-50 when fair is ≈ 36, sell 25-50 lots.

#### 4. Races 3-4 into Series 2

- Pre-compute the Series-2 grid: S2 Top-2 = 82 + 5.5·(x̄_S1 − 8.5), plus each human's skill.
- **Fire it the moment Series 1 ends.** Most teams look at Series 2 only after Series 1 settles.

## In-race (only if markets trade during races; low confidence, so calibrate on Series 1)

Leading human's chance to win the race-winner market at the start of the final lap. p is their overall place, q
the next human's place; the second number assumes a chaotic last lap. Gun A buys only when the offer is ≥ 10 below
the table, 25-50 lots in Series 1.

| p \ q | 2 | 3 | 4 | 5 | 6-7 | 8-9 |
|---|---|---|---|---|---|---|
| 1 | 64/56 | 73/64 | 80/70 | 85/75 | 90/80 | 94/86 |
| 2 | | 54/48 | 64/55 | 70/60 | 77/66 | 87/74 |
| 3 | | | 50/44 | 58/50 | 68/56 | 78/64 |
| 4 | | | | 48/43 | 58/48 | 72/61 |

Expected final points from place at the start of the final lap:

| Place | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Points | 13.3 | 11.5 | 10.2 | 9.3 | 8.4 | 7.7 | 6.9 | 6.2 | 5.4 | 4.6 | 3.7 | 2.6 |

A clear series leader sitting 1st is worth about +4.8 on #1 (13.3 against a pre-race 8.5). Otherwise scale the shift
by the chance that human ends as #1 (or top two, for Top-2).

## Risk, roles, tool

- **Orders:** limit orders only, priced to fill with a cap. Type 100/200/500 (the presets stop at 50). At most ~6 resting orders per trader, logged on paper.
- **Level cap:** L = S2 Top-2 + 0.5·(S2 #1 + S2 #2), plus (races left / 4) × the same for Series 1. Keep **\|L\| ≤ 300 before Race 1, ≤ 700 after Race 1, ≤ 1,000 after Race 2.**
- **One human's skill:** future race-winner lots + 2 × series-winner lots ≤ 400 net.
- **Reserve:** keep ≥ 300 of 1000 score capacity free for the Race-1 repricing.
- **Don'ts:** bet the level before Race 1 inside the range; pay 97-99 at the finish line; sell the series basket below 101; treat one hot race as proof of skill.
- **Roles:**
  - **Spotter:** watches only the race and calls places; never touches a keyboard. Writes the quadrant → character → contract mapping before every race.
  - **Gun A:** race winners, the finish line, arbitrage, between-race quotes.
  - **Gun B:** current-series score and series-winner contracts.
  - **Brain:** standings, m̂/m_i, tables, Series-2 prices, caps, the log.
- **Ask on Slack:** Can the current race's contracts trade while it runs? Are position limits per contract? Can we
  short? Who provides liquidity? Same 4 racers in both series? Are trades busted after the outcome? Does Mario count
  toward the overall rank?

**Tool** (Brain's laptop, not the trading one). Run it from the `mario/` folder, with places and scores in contract
order H1..H4 (not sorted); add `--K 200000` once there are 6+ races of history. It has no race-to-race shocks, so it
overreacts: after Race 1, move about 85% of the way from the prior to its number (90% after Race 2).

```
python3 -I tools/fv_calc.py --hist "4,1,7,9;6,2,5,10" --scores 16 27 14 7 --left 2
```
