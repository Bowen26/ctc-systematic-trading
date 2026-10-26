# Mario Markets: team playbook (CTC 2026)

> **Files in this folder:** `tools/fv_calc.py` (fair-value calculator; needs `mario_core.py` and `learning.py` beside it) and `tools/cheatsheet_tables.md` (full matrices to print). Paths below that start with `work/` refer to the original analysis workspace; the tools you need are in `tools/`.

Tags: **[P]** the packet states it · **[I]** our inference or model output · **[?]** ambiguous; ask in #questions (questions are collected in §8).

---

## 1. How this case is won

Most of the PnL is in the **1000-lot score contracts**: #1, #2 and Top-2, in both series. One point of mispricing there is worth 1,000. On a 200-lot series-winner contract, 1,000 takes 5 points of mispricing; on a 100-lot race-winner contract it takes 10.

The largest unknown is how good the four humans are against the CPUs. That single factor moves the fair value of Top-2 anywhere from about 66 to 97. **Race 1 is the first real evidence on it.** The team that reprices the Series-2 score contracts in the first minute after Race 1 has the biggest, least crowded edge. After that, the job is to:

- price #1 and Top-2 with the **order-statistic premium** (+8 and +10 at the start of a series), which naive traders leave out;
- price series-winner contracts from the **lead tables**, not from intuition;
- take every **arbitrage and stale quote** (baskets, identities, hard bounds, finished outcomes);
- **never leave quotes resting while a race is running.**

Do not bet the human level at full size before Race 1, and cap total exposure to the level, because a full-limit level bet carries about 13k–27k of 1-sd risk. Once one race has given real evidence, scale in.

---

## 2. Rules that matter

### 2.1 What the packet says [P]

| Item | Rule |
|---|---|
| Field | 12 racers per race: 4 humans and 8 CPUs. Two series of 4 races (8 races). Tracks are chosen at random. |
| Characters | The 4 humans use **visually distinct** characters from Daisy, Peach, Birdo and Yoshi, with **identical stats** and an identical kart (Teddy Buggy, Roller Tires, Cloud Glider). Any premium for a favourite character is noise. |
| Points (by overall place among all 12) | 15, 12, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1. **Mnemonic: 1st = 15, 2nd = 12, otherwise 13 − place.** |
| Race winner (binary, **limit 100**) | Four contracts per race. The highest-placed *human* settles to 100 even if a CPU wins the race overall. The others settle to 0. Settles after that race. |
| Series winner (binary, **limit 200**) | Four contracts per series. The highest 4-race total settles to 100. **Every human tied for the lead settles to 50**, so a 3-way tie pays 150 in total and a 4-way tie pays 200. Settles after Race 4 / Race 8. |
| #1, #2, Top-2 (non-binary, **limit 1000**) | Settle to the highest human series total, the second highest, and their sum. Tie example from the packet: 45 and 45 give #1 = 45, #2 = 45, Top-2 = 90. |
| Trading availability | "You may trade any market that has not yet settled", including upcoming races' race-winner contracts and unresolved series and score contracts. A race's race-winner contracts settle once the race is complete. |
| Bots | Not allowed. Trading is manual through the UI ("You should not make bots for the other two cases"). |
| Scoring | Ranked **only by total PnL**. Prizes: 1st, a Switch for each member (Jump Trading); 2nd, $500; 3rd, $300. |
| Overall weight | Overall rank = 0.2·Mario + 0.35·Derivatives + 0.45·Systematic Equities. **But** "events hosted by our gold sponsors will not contribute to the overall competition scoring", and Mario is "presented by Jump Trading", a gold sponsor. |
| UI (sample screenshot) | Order types Limit and Market. Quantity quick buttons 1/5/10/25/50 (type anything larger). The book has a "YOU" column. Open-orders (Current/All) and Positions panels. **No cancel-all button is visible.** |

### 2.2 Ambiguities that change the plan [?]

| Ambiguity | Why it matters | Default until answered |
|---|---|---|
| Can the **current** race's race-winner contracts be traded while it runs? The general rule ("any market that has not yet settled") says yes. The packet's example only mentions "later races". | Decides whether a live race-winner trader is needed at all. | Assume the current series' series and score contracts are open during races, which the packet's example implies. Treat live race-winner trading as unconfirmed. |
| Is the position limit **per contract or per market type**? Net or gross? Do resting orders count? The packet says "each [market type] with its own … position limit". | Every size in this playbook assumes a limit per contract. If the limit is per type, divide the score sizes by about 3 and the race and series sizes by about 4. | Per contract, net. Count resting orders against capacity anyway. |
| Can we go **short**? Is there a cash or margin limit? Fees? | Every "sell the basket" or "sell the rich leg" trade needs shorting. | Shorting to −limit is allowed. |
| Does Mario count toward the overall rank (the gold-sponsor note versus the 20% weight)? | Sets the risk policy (§6.6). | Assume it counts at 20%. |
| **Settlement lag**, and whether trades made after an outcome is known get **busted**. | Decides whether finish-line and end-of-series pick-offs exist. The packet says contracts settle "once a race is complete", so the window may be zero. | No window. Any pick-off is a bonus. |
| Are the **same four people on the same characters** in both series? | All learning that carries from Series 1 to Series 2 depends on this. | Yes, but confirm. |
| Are Series-2 contracts **listed from the open**? | The Series-2 repricing edge only exists during Series 1 if they are. | Yes (the packet's wording implies it). |
| Who supplies liquidity: a house bot or only teams? | If a house bot quotes a naive fair value, the structural edges are much larger and more repeatable. | Unknown. Probe it at the open (§4, Phase 1). |
| Are pricing calculators or spreadsheets allowed if order entry stays manual? | Decides whether `fv_calc.py` can be used or only printed tables. | Bring both. |

---

## 3. Core model and fair values (print this section)

### 3.0 Pocket card: memorise these

| Item | Value |
|---|---|
| Points | 1st = 15, 2nd = 12, otherwise 13 − place |
| Prior fair values before Race 1 | Race winner 25, series winner 25, **#1 ≈ 45, #2 ≈ 37, Top-2 ≈ 82 (sd 13.5)**. Do not bet the level. |
| Robust ranges before Race 1 (every plausible scenario lies inside) | #1 36–52, #2 29–45, **Top-2 65–97**, #1 − #2 spread 5–10 |
| Race-winner basket (all 4 contracts in one race) | Exactly 100, always |
| Series-winner basket | Never below 100. Typically 100.1–100.2. With one race left: up to **100.8** with average humans, **102.2** with strong humans. |
| Selection premium at series start (equal skill) | **#1 = 4m + 8, Top-2 = 8m + 10**. Uneven skill adds about +2 / +3. |
| Learning the human level | m̂ = 8.5 + β(x̄ − 8.5), where x̄ = average human points per race so far. **β = 0.45, 0.6, 0.7, 0.75** after 1–4 races; 0.8–0.85 after 5–7. |
| Learning one human's skill | m_i = m̂ + γ(x̄_i − x̄). **γ = 0.25, 0.4, 0.5, 0.55** after 1–4 races; 0.6–0.7 after 5–7. |
| Series-2 Top-2 during Series 1 | **82 + k(x̄ − 8.5), k = 3.1, 4.4, 5.1, 5.5** after Races 1–4 |
| Two-human duel for the series | Leader's value ≈ 100·Φ(lead / (5.5·√races left)) |
| Locks | Only possible with one race left (after Race 3 or 7): a **lead of 15 or more** locks the series. A lead of exactly 14 can only be tied. |
| At the finish line | Pre-loaded tickets: **buy ≤ 90, sell ≥ 10**. Never use Market orders. |

### 3.1 The model [I]

- **How a race is modelled (Plackett-Luce).** Each racer gets a performance draw: log-strength plus random noise (Gumbel), and racers finish in order of performance. CPUs have log-strength 0. Human *i* has θ_i = μ + δ_i:
  - μ is the shared human-versus-CPU level, with prior N(0.9, 0.8²);
  - δ_i is that human's own skill, drawn from N(0, τ²), with τ (the spread of skill between humans) uniform over {0.2, …, 1.2}.
- **Race-to-race shocks.** The central version also adds a shock of sd 0.5 that hits all four humans equally in a given race. It stands in for the track, warm-up and item chaos. This shock makes learning slower: one race reveals less than the shock-free model says.
- **One useful property.** Under this model, the probability that human *i* wins the race-winner market depends only on the human weights w = e^θ: P(i wins) = w_i / Σ w, whatever the CPU level.
- **Robustness.** A Gaussian-noise version of the model moves score fair values by about 1–1.5 points, so quote Top-2 1–2 points wider than the model alone suggests.
- **What does not depend on the model:** all bounds, locks, impossible values and basket identities (§3.8–3.9).

**Reference scenarios** (CPU weight 1, w = each human's weight):

| Scenario | Human weight w | Meaning | m = E[points per human per race] | P(a CPU wins the race overall) | Humans in top 4 (avg) | E[total human points in a race] (sd) |
|---|---|---|---|---|---|---|
| S1 | 1 | Humans are no better than the CPUs | 6.83 | 66.7% | 1.33 | 27.3 (6.9) |
| S2 | 2.2 | The average human finishes 5th | 8.52 | 47.6% | 1.95 | 34.1 (6.2) |
| M | 2.72 | Central case | 8.92 | 42.4% | – | ≈35.7 |
| S3 | 9.3 | 3 humans in the top 4 on average | 10.54 | 17.7% | 3.00 | 42.2 (3.9) |
| S4 | 9.3 / 2.2 / 2.2 / 1 | One strong, two average, one weak | 12.2 / 8.3 / 8.3 / 5.9 | 35.2% | 2.08 | 34.7 (5.7) |

### 3.2 Reading the scenario from Race 1 [I]

Add up the four humans' points in the race to get T. The table shows how likely each total is under each scenario, and the resulting weights if the four scenarios start equally likely.

| T (total human points in Race 1) | Likelihood S1 / S2 / S3 / S4 (%) | Posterior, equal priors (%) |
|---|---|---|
| 10–19 | 13.3 / 1.3 / 0.0 / 0.5 | 88 / 8 / 0 / 3 |
| 20–24 | 22.2 / 6.0 / 0.0 / 4.0 | 69 / 19 / 0 / 12 |
| 25–29 | 26.7 / 16.1 / 0.5 / 14.2 | 46 / 28 / 1 / 25 |
| 30–34 | 22.0 / 27.3 / 4.6 / 28.3 | 27 / 33 / 6 / 34 |
| 35–39 | 11.7 / 28.5 / 19.9 / 31.5 | 13 / 31 / 22 / 34 |
| 40–43 | 3.2 / 14.6 / 25.7 / 15.4 | 5 / 25 / 44 / 26 |
| 44–46 | 0.8 / 6.1 / 49.2 / 6.0 | 1 / 10 / 79 / 10 |

S2 and S4 have the same average level and differ only in spread. If one human keeps beating the other three, you are in S4 territory, and #1 and the leader's series contract are worth more (§3.5).

### 3.3 Prior fair values (before any race)

**Series start, by scenario** (400k simulated series per scenario):

| Scenario | #1 mean (sd) [p5–p95] | #2 mean (sd) | Top-2 mean (sd) [p5–p95] | #1 − #2 spread | Series winner |
|---|---|---|---|---|---|
| S1 | 36.2 (5.7) [27–46] | 29.7 (4.6) | 65.9 (8.8) [52–81] | 6.5 | 25 each |
| S2 | 42.3 (5.0) [34–51] | 36.3 (4.1) | 78.6 (7.7) [66–91] | 6.0 | 25 each |
| M | 43.6 | 37.8 | 81.4 | 5.8 | 25 each |
| S3 | 48.8 (3.6) [43–55] | 43.9 (2.8) | 92.6 (5.1) [84–101] | 4.9 | 25 each |
| S4 | 49.3 (4.9) | 37.3 (5.0) | 86.7 (6.9) | 12.0 | 90 / 5 / 5 / 0.5 |
| **Blended prior** (level and spread both unknown) | **44.5–45.0 (≈7.6)** | **≈37 (≈7.3)** | **81.4–82.1 (≈13.5)**, [57–101] | **≈7.7** | 25 each |

**Uneven skill adds to the premiums** (S2 level; simulated with τ = spread of skill between humans):

| τ | E[#1] | E[#2] | E[Top-2] | #1 − #2 spread (sd) |
|---|---|---|---|---|
| 0 (equal) | 42.3 | 36.3 | 78.5 | 6.0 (4.9) |
| 0.6 | 44.5 (+2.2) | 37.0 | 81.5 (+3.0) | 7.5 (5.9) |
| 1.0 | 46.8 (+4.5) | 37.5 | 84.4 (+5.9) | 9.3 (7.1) |

**Robust bands before Race 1.** These cover every scenario from S1 at τ = 0 to S3 at τ = 1.0:

| Contract | Band |
|---|---|
| #1 | 36–52 |
| #2 | 29–45 |
| Top-2 | 65–97 |
| #1 − #2 | 4.9–10.3 |

Inside a band, a price is a bet on the level, not an edge.

**Prior race-winner and series-winner values:** 25 each. The series basket is worth 100.1–100.2 because of tie payouts (P(3-way tie) 0.2–0.4%, P(4-way) about 0.01%).

### 3.4 After Race 1: Series-2 lookup table (the most valuable table) [I]

How to read it:
- The left column lists the four humans' overall places in Race 1, best first.
- "Best human" means whoever finished first among the humans.
- The main figures come from the model with race-to-race shocks. The Top-2 figure in brackets is from the shock-free model, which is what `fv_calc.py` computes.

| Race-1 human places | T | m̂ (later races) | S2 #1 | S2 #2 | **S2 Top-2** (shock-free) | Top-2 sd | Best human: Race-2 win % | Best human: S2 series value |
|---|---|---|---|---|---|---|---|---|
| prior | – | 8.4 | 44.5 | 36.9 | **81.4** (82.1) | 13.5 | 25 | 25 |
| 1,2,3,4 | 46 | 9.9 | 49.0 | 42.4 | **91.4** (92.4) | 8.9 | 31 | 34 |
| 1,2,3,5 | 45 | 9.7 | 48.5 | 41.5 | **89.9** (91.4) | 9.3 | 31 | 35 |
| 1,3,5,8 | 38 | 8.9 | 46.1 | 38.6 | **84.7** (85.6) | 11.0 | 34 | 39 |
| 2,4,6,8 | 33 | 8.6 | 44.7 | 37.5 | **82.1** (83.0) | 11.4 | 31 | 36 |
| 1,4,7,10 | 33 | 8.2 | 44.3 | 36.2 | **80.4** (80.9) | 12.4 | 36 | 42 |
| 2,5,7,10 | 29 | 8.1 | 43.4 | 35.5 | **78.9** (78.9) | 12.0 | 33 | 39 |
| 3,6,8,10 | 25 | 7.9 | 42.0 | 34.4 | **76.4** (75.8) | 12.3 | 32 | 36 |
| 4,7,9,11 | 21 | 7.4 | 40.4 | 32.7 | **73.0** (71.5) | 12.8 | 32 | 36 |
| 1,10,11,12 | 21 | 6.3 | 39.2 | 27.7 | **66.9** (64.9) | 14.4 | 48 | 59 |
| 6,9,11,12 | 14 | 6.2 | 36.1 | 27.7 | **63.8** (60.1) | 13.9 | 35 | 39 |
| 9,10,11,12 | 10 | 5.5 | 32.3 | 24.3 | **56.5** (51.1) | 13.9 | 32 | 37 |

Monte Carlo noise is about ±0.3 on scores and about ±1 on percentages.

**Lessons from this table:**
- **The spread between humans matters as well as the total.** (4,7,9,11) and (1,10,11,12) have the same T = 21. The second implies one strong human and three weak ones: S2 #2 is 5 points lower, Top-2 is 6 points lower, and the best human is worth 59 in the S2 series.
- **Even after the update, Top-2 sd stays at 9–14.** A full 1000-lot position still carries about 10k of 1-sd risk, so scale in (§4, Phase 4).
- **Correction for `fv_calc.py`.** It is shock-free, so it overreacts to a single race. After Race 1, move about **85%** of the way from the prior to its level-driven number (about 80% for extreme lows like T ≤ 15). After Race 2, move about 90%.

### 3.5 Learning rules between races [I]

**1. The human level.**
- Formula: m̂ = 8.5 + β_n · (x̄ − 8.5), where x̄ is the average human points per race over the n races seen so far.

| Races seen n | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|
| β (central, shock sd 0.5) | **0.44** | **0.61** | **0.70** | **0.76** | 0.80 | 0.82 | 0.84 |
| β range (shock sd 0.8 to no shock) | 0.35–0.52 | 0.52–0.69 | 0.62–0.76 | 0.68–0.81 | 0.73–0.84 | 0.76–0.86 | 0.79–0.88 |

- Report 1 used β = 0.45 after one race with no shock; the red-team critique proposed 0.3. Our verification puts the plausible range at 0.35–0.52, so use **0.44 to price** and size as if β were the bottom of the range.
- Treat a weak Race 1 as softer evidence than a strong one: cold start and an unfamiliar setup make Race 1 the race most likely to understate the humans.

**2. One human's own expected points.**
- Formula: m_i = m̂ + γ_n · (x̄_i − x̄), where x̄_i is that human's average.

| n | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|
| γ | 0.24 | 0.39 | 0.49 | 0.56 | 0.62 | 0.66 | 0.69 |

- Check against Report 1's example: H2 places 1st and 2nd while the field averages 8.0 over two races. Then m̂ = 8.2 and m_H2 = 8.2 + 0.39·5.5 = **10.3**; the full posterior gives 10.2.
- **Always use the leader's own m in the #1 formula and the top two humans' own m in the Top-2 formula** (red-team correction M8). The field-average m understates #1 by about 2 and Top-2 by about 3 when skill is uneven.

**3. Series-2 Top-2 during Series 1.**
- Formula: 82 + k_n · (x̄_S1 − 8.5), with k = **3.1 / 4.4 / 5.1 / 5.5** after Races 1–4. Range across shock assumptions: 2.6–3.7 after 1 race, 5.0–5.8 after 4.
- The residual sd is still 10–12 after all of Series 1.
- Adjust by spread: if one human dominates three weak ones, S2 #2 and Top-2 are lower than the formula says (see the (1,10,11,12) row of §3.4).
- S2 #1 and #2 each carry about half the level sensitivity of Top-2.

**4. Next-race win probability from the race-winner record alone.** Use τ = 0.6 as the central case; the cells show H1/H2/H3/H4 at τ = 0.3 / 0.6 / 1.0.

| Wins so far (H1, H2, H3, H4) | τ = 0.3 | **τ = 0.6** | τ = 1.0 |
|---|---|---|---|
| (1,0,0,0) | 26.7/24.5/24.4/24.4 | **31.1**/23.0/23.0/22.9 | 39.2/20.3/20.3/20.2 |
| (2,0,0,0) | 28.3/23.9/… | **36.8**/21.1/21.1/21.1 | 50.1/16.6/… |
| (1,1,0,0) | 26.1/26.1/23.9/23.9 | **28.6/28.6**/21.4/21.5 | 32.2/32.2/17.8/17.8 |
| (3,0,0,0) | 29.9/23.4/… | **41.9**/19.4/… | 57.9/14.0/… |
| (2,1,0,0) | 27.7/25.5/23.4/23.4 | **33.8/26.2**/20.0/20.0 | 42.2/26.9/15.4/15.5 |
| (4,0,0,0) | 31.5/22.8/… | **46.3**/17.9/… | 63.6/12.1/… |
| (3,1,0,0) | 29.2/24.9/22.9/22.9 | **38.6/24.1**/18.6/18.6 | 50.0/22.9/13.5/13.5 |

Full places carry more information than wins alone; use `fv_calc.py --hist` when allowed. After a dominant Race 1 (places 1, 4, 7, 10), the winner's fair chance in Race 2 is **36%, with a range of 27–44 across τ**.

**5. Series-2 series-winner values during Series 1.**
- While the Series-2 standings are level, series value ≈ normalise(p_i^1.7), where p_i are the next-race win probabilities. Use k = 2 instead of 1.7 once skill is well known.
- Example: p = 36.6 / 26.5 / 20.8 / 16.1 maps to about 45 / 26 / 17 / 11.
- After a full Series 1:
  - one dominant human (scores 52-36-25-22) → S2 series 64.5 / 21 / 8 / 7;
  - four different race winners (46-42-30-31) → 47 / 30 / 12 / 12.
- If the same human order repeats in all 4 races, the Series-1 winner's S2 series value is 37 / 60 / 77 at τ = 0.3 / 0.6 / 1.0.
- **Use this rule only when the standings are level**, i.e. for Series 2 before it starts. Mid-series, the points dominate; use §3.7. "Buy the series, sell the race" is a statistical relationship, not a hedge.

### 3.6 Score contracts mid-series [I]

Notation:
- r = races left in the series.
- s1 ≥ s2 ≥ s3 ≥ s4 = current scores of the four humans, highest first.
- a, b, c = gaps behind the leader (a = s1 − s2, b = s1 − s3, c = s1 − s4).
- m_1 and m_2 = the current top two humans' own expected points per race (§3.5).

**Formulas:**
- **E[#1] = s1 + r·m_1 + C(a) + 0.45·[C(b) + C(c)].** Maximum error 0.5 versus exact.
- **E[Top-2] = s1 + s2 + r·(m_1 + m_2) + [C(b−a) + 0.5·C(c−a)] × (1 + 0.55·C(a)/C(0)).** Maximum error 0.6.
- **E[#2] = E[Top-2] − E[#1].**

C(g) = E[(D_r − g)+] is the expected amount by which a rival g points behind finishes ahead of you (D_r is the rival's points gained relative to you over the remaining r races). Interpolate between columns using m̂:

| g | S1 r=1 | S2 r=1 | S3 r=1 | S1 r=2 | S2 r=2 | S3 r=2 | S1 r=3 | S2 r=3 | S3 r=3 |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2.48 | 2.33 | 1.91 | 3.38 | 3.14 | 2.52 | 4.13 | 3.84 | 3.08 |
| 1 | 1.98 | 1.83 | 1.41 | 2.91 | 2.67 | 2.05 | 3.65 | 3.36 | 2.60 |
| 2 | 1.55 | 1.40 | 0.98 | 2.48 | 2.25 | 1.65 | 3.21 | 2.92 | 2.18 |
| 3 | 1.19 | 1.03 | 0.64 | 2.09 | 1.87 | 1.30 | 2.80 | 2.52 | 1.80 |
| 4 | 0.89 | 0.75 | 0.42 | 1.75 | 1.54 | 1.00 | 2.43 | 2.16 | 1.47 |
| 5 | 0.65 | 0.52 | 0.23 | 1.45 | 1.25 | 0.76 | 2.10 | 1.84 | 1.19 |
| 6 | 0.46 | 0.35 | 0.12 | 1.18 | 1.00 | 0.56 | 1.80 | 1.55 | 0.94 |
| 8 | 0.21 | 0.14 | 0.02 | 0.76 | 0.61 | 0.27 | 1.29 | 1.08 | 0.57 |
| 10 | 0.08 | 0.04 | 0.00 | 0.47 | 0.35 | 0.11 | 0.90 | 0.72 | 0.32 |
| 12 | 0.02 | 0.01 | 0.00 | 0.27 | 0.19 | 0.04 | 0.61 | 0.46 | 0.17 |
| 15 | 0 | 0 | 0 | 0.10 | 0.06 | 0.00 | 0.32 | 0.22 | 0.05 |

**Size of the selection premium** (S2, equal skill), compared with "score + races left × m":

| Situation | #1 premium | Top-2 premium |
|---|---|---|
| Series start (r = 4) | +8.2 (S1 +8.9, S3 +6.6) | +10.4 (S1 +11.2, S3 +8.3) |
| All four tied, r = 3 | +7.1 | +9.0 |
| All four tied, r = 2 | +5.9 | +7.4 |
| All four tied, r = 1 | +4.5 | +5.5 |
| Leader 13 or more points clear, r = 1 | ≈ 0 | – |

#2 at series start is only about +2 above a single human's expected total.

**Worked example 1** (exact check; S2, one race left, scores 37 / 28 / 19 / 9):

| Contract | Formula | Value |
|---|---|---|
| E[#1] | 37 + 8.52 + 0.08 | **45.6** |
| E[Top-2] | 65 + 17.04 + 0.08 | **82.1** |
| E[#2] | 82.1 − 45.6 | **36.5** |
| Leader's series contract | from §3.7 | **95.1** |

**Worked example 2** (after Race 1, humans placed 1, 2, 3, 4, so scores are 15 / 12 / 10 / 9 and r = 3):

| Step | Working | Result |
|---|---|---|
| Field level | §3.4 | m̂ ≈ 9.9 |
| Leader's own m | 9.9 + 0.24·(15 − 11.5) | m_1 ≈ 10.7 |
| 2nd human's own m | 9.9 + 0.24·(12 − 11.5) | m_2 ≈ 10.0 |
| C values | columns about 70% of the way from S2 to S3, r = 3 | C(3) ≈ 2.0, C(5) ≈ 1.4, C(6) ≈ 1.1, C(2) ≈ 2.4, C(0) ≈ 3.3 |
| **E[S1 #1]** | 15 + 32.1 + 2.0 + 0.45·2.5 | **≈ 50.2** |
| **E[S1 Top-2]** | 27 + 3·20.7 + (2.4 + 1.0)·1.33 | **≈ 93.6** |
| **E[S1 #2]** | 93.6 − 50.2 | **≈ 43.4** |

### 3.7 Series-winner tables (equal skill) [I]

**A. Leader versus one rival, other humans out of contention.** The cell is the leader's value: 100·P(win outright) + 50·P(tie). The rival is worth 100 minus that.

| Lead | S1 r=1 | S2 r=1 | S3 r=1 | S1 r=2 | S2 r=2 | S3 r=2 | S1 r=3 | S2 r=3 | S3 r=3 |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 50 | 50 | 50 | 50 | 50 | 50 | 50 | 50 | 50 |
| 1 | 53 | 53 | 54 | 55 | 55 | 57 | 54 | 54 | 55 |
| 2 | 60 | 60 | 62 | 59 | 60 | 62 | 58 | 58 | 60 |
| 3 | 67 | 68 | 72 | 64 | 64 | 68 | 61 | 62 | 65 |
| 4 | 73 | 74 | 80 | 68 | 69 | 73 | 65 | 66 | 69 |
| 5 | 78 | 80 | 85 | 72 | 73 | 78 | 68 | 70 | 74 |
| 6 | 83 | 85 | 91 | 75 | 77 | 82 | 72 | 73 | 78 |
| 7 | 88 | 89 | 95 | 79 | 81 | 86 | 75 | 76 | 81 |
| 8 | 91 | 93 | 98 | 82 | 84 | 89 | 78 | 79 | 85 |
| 9 | 94 | 95 | 99 | 85 | 87 | 92 | 80 | 82 | 88 |
| 10 | 95 | 97 | 100 | 88 | 90 | 94 | 83 | 85 | 90 |
| 12 | 98 | 99 | 100 | 92 | 94 | 98 | 88 | 89 | 94 |
| 14 | 99.6 | 99.9 | 100 | 95 | 96 | 99 | 91 | 93 | 97 |
| 16 | 100 | 100 | 100 | 97 | 98 | 100 | 94 | 95 | 98 |

- Exact S2 values with one race left: lead 1 → 53.3, 2 → 60.2, 4 → 74.5, 5 → 79.9, 8 → 92.7, 10 → 96.9, 12 → 99.0, 14 → 99.9.
- Shortcut: 100·Φ(d / (σ√r)), with σ = 5.9 (S1), 5.5 (S2), 4.4 (S3). It is good to about ±3.

**B. All four humans, S2.** Each cell is the **leader's value X/Y**:
- X assumes 4th place is level with 3rd (c = b).
- Y assumes 4th place is out of contention.
- Rows are a (2nd's gap); columns are b (3rd's gap).

Do **not** multiply pairwise duel values together: that is off by 11–20 points when the chasers are bunched. The full matrices, including 2nd-place values and the S1/S3 versions, are in `cheatsheet_tables.md`.

One race left (after Race 3 / 7):

| a \ b | 0 | 2 | 4 | 6 | 8 | 10 | 13 | 16+ |
|---|---|---|---|---|---|---|---|---|
| 0 | 25/33 | 29/37 | 38/43 | 44/47 | 48/49 | 50/50 | 50/50 | 50/50 |
| 2 | | 31/42 | 42/50 | 50/55 | 56/58 | 59/60 | 60/60 | 60/60 |
| 4 | | | 48/59 | 59/66 | 68/71 | 72/73 | 74/74 | 74/74 |
| 6 | | | | 65/74 | 75/80 | 81/83 | 85/85 | 85/85 |
| 8 | | | | | 81/86 | 88/90 | 92/92 | 93/93 |
| 10 | | | | | | 91/94 | 96/97 | 97/97 |
| 13 | | | | | | | 99/99 | 100 |

Two races left (after Race 2 / 6):

| a \ b | 0 | 2 | 4 | 6 | 8 | 10 | 13 | 16 | 20+ |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 25/34 | 31/38 | 36/42 | 40/44 | 44/47 | 46/48 | 49/49 | 50/50 | 50/50 |
| 2 | | 35/44 | 41/48 | 46/52 | 51/55 | 54/57 | 58/59 | 59/60 | 60/60 |
| 4 | | | 45/54 | 51/59 | 57/62 | 61/65 | 66/67 | 68/68 | 69/69 |
| 6 | | | | 55/64 | 62/68 | 67/72 | 73/75 | 76/76 | 77/77 |
| 8 | | | | | 66/73 | 72/77 | 79/81 | 82/83 | 84/84 |
| 10 | | | | | | 76/82 | 83/86 | 87/88 | 89/89 |
| 13 | | | | | | | 88/91 | 92/94 | 95/95 |
| 16 | | | | | | | | 95/97 | 98/98 |

Three races left (after Race 1 / 5; gaps can be at most 14):

| a \ b | 0 | 2 | 4 | 6 | 8 | 10 | 13 |
|---|---|---|---|---|---|---|---|
| 0 | 25/33 | 30/36 | 34/39 | 38/42 | 41/44 | 44/46 | 47/47 |
| 2 | | 33/41 | 38/44 | 43/48 | 47/50 | 50/52 | 54/54 |
| 4 | | | 41/49 | 47/53 | 52/56 | 56/58 | 60/60 |
| 6 | | | | 50/57 | 56/60 | 61/63 | 66/66 |
| 8 | | | | | 59/64 | 64/67 | 71/71 |
| 10 | | | | | | 68/71 | 75/75 |
| 13 | | | | | | | 79/79 |

**Adjustments to the tables:**
- **S3-level humans:** leads are stickier with one race left: 4/far → 80 (S2: 74), 6/far → 91 (85), 8/far → 98 (93). S1 is about 1–2 points looser than S2.
- **Skill learning:** add **+3 to +6** to a leader who has also looked stronger. With learning, Report 1's example leader was worth 66.8 against 60.5 under equal skill after Race 1, and 92.3 against 87.9 after Race 2.
- **Dominant Race 1** (places 1, 4, 7, 10): the winner's Series-1 contract is about 59 on points alone and **63–75 with learning (central 69)**. A Race 1 won by a CPU, with the top human taking 12 points, is worth about 50.

### 3.8 Hard bounds, locks and impossible values (exact)

| Quantity | Min | Max | Note |
|---|---|---|---|
| #1 (whole series) | 10 | 60 | **56, 58 and 59 are impossible**. Above 55, only 57 and 60 can occur. |
| #2 | 8 | 54 | Two humans can earn at most 27 per race between them, so #2 ≤ 108/2. |
| Top-2 | 20 | 108 | **107 is impossible** (two humans can never score 26 between them in a race). |
| #1 − #2 | 0 | 52 | |
| Points gained in one race | – | – | Never 11, 13 or 14. Over two races never 26, 28 or 29; over three never 41, 43 or 44. |

**During a series** (r races left, current scores s1 ≥ s2):
- #1 ∈ [s1 + r, s1 + 15r]
- #2 ≥ s2 + r
- **Top-2 ∈ [s1 + s2 + 3r, s1 + s2 + 27r]**. The current top two take different places, so together they gain at least 3 per race.

**Locks:**
- The most one race can swing between two humans is 14 (15 versus 1).
- A human trailing by **more than 14r** is worth 0. Trailing by exactly 14r, they can at best tie, so the contract is worth at most 50 and in practice about 0.1.
- Locks are **impossible after Race 1 and Race 2**: the largest possible lead after two races is exactly 28, which equals 14 × 2.
- After Race 3 / 7, a lead of **15 or more locks** the series.

**Race-winner results and the series:**
- Winning all four race-winner contracts guarantees the series, with a lead of at least 4.
- Winning 3 of 4 does not guarantee it.
- A human with zero race wins can still win the series.

### 3.9 Baskets, identities and the trade for each violation

Execute every multi-leg trade starting with the **stale leg**, sized only to what you are willing to hold at fair value if the other legs fail to fill. The UI has a single order ticket, so you carry legging risk.

| Relationship | Violation → trade |
|---|---|
| Sum of the 4 race-winner contracts in a race = **100 exactly** | Sum of best bids > 100: sell all 4. Sum of best asks < 100: buy all 4. |
| Sum of the series-winner contracts **≥ 100** | Sum of best asks < 100: buy all 4 (pays at least 100). **Sell only at ≥ 101** (S1/S2-level humans) or **≥ 102.5** (S3-level), ≤ 50 baskets. |
| Top-2 = #1 + #2 | Top-2 bid > #1 ask + #2 ask: sell Top-2, buy #1 and #2. The reverse if Top-2 ask < #1 bid + #2 bid. |
| #1 ≥ #2 | #2 bid > #1 ask: buy #1, sell #2. |
| Top-2 ≤ 2·#1 and Top-2 ≥ 2·#2 | Sell 1 Top-2 and buy 2 #1; or buy 1 Top-2 and sell 2 #2. |
| Same human, different future races | Equal fair values (skill-neutral). Buy the cheapest of that human's future race contracts, sell the richest. |
| Inventory trick | A long contract plus the other three contracts of the same race is a riskless 100. To exit an unwanted long when its bid is thin, buy the other three instead of selling it. |

**Series-winner basket, exact values with one race left** (red-team correction M1: Report 2's "never above 100.5" and its "no tie risk" claims for co-leaders were wrong):

| Standings before the last race | S1 | S2 | S3 | P(3-way) / P(4-way), S2 |
|---|---|---|---|---|
| Pre-series (for reference) | 100.12 | 100.14 | 100.21 | 0.27% / 0.004% |
| **40/39/37/34**, gaps (1,3,6): the worst case | 100.51 | **100.84** | **102.22** | 1.46% / 0.11% |
| 40/38/36/33, gaps (2,4,7) | 100.47 | 100.71 | 101.31 | 1.24% / 0.09% |
| 40/38/35/32, gaps (2,5,8) | 100.43 | 100.68 | 101.39 | 1.23% / 0.07% |
| 40/39/38/37 | 100.48 | 100.49 | 100.29 | 0.84% / 0.07% |
| 40/37/37/34 | 100.25 | 100.46 | **101.62** | 0.93% / 0 |
| 40/40/37/34 | 100.16 | 100.31 | 100.59 | 0.61% / 0 |
| 40/38/36/10 | 100.27 | 100.32 | 100.45 | 0.63% / 0 |

- Of 680 possible gap patterns with one race left, none exceeds 101 at S1 or S2 level. At S3 level, 25 exceed 101 (others include (2,5,5) → 102.0 and (0,2,5) → 101.9).
- Two humans who are tied going into the final race cannot finish tied with *each other*, but a 3-way tie that excludes one of them is still possible. Only the 4-way tie is ruled out.
- A short basket loses 49 per set on a 3-way tie and 99 on a 4-way tie: at 200 lots, −9,800 or −19,800.

### 3.10 In-race information (only if the market trades during races) [I, low confidence]

These come from a lap-by-lap Gaussian model calibrated to S2. They might understate how predictive the race state is (a big visible gap with no shell incoming). They might equally overstate it (blue shells, lightning, CPU rubber-banding). **Calibrate them from the Series-1 log before sizing up.**

**Leading human's chance of winning the race-winner market at the start of the final lap.** Rows p are the leading human's overall place; columns q are the next human's overall place. Cells are equal-variance laps / chaotic final lap.

| p \ q | 2 | 3 | 4 | 5 | 6–7 | 8–9 |
|---|---|---|---|---|---|---|
| 1 | 64/56 | 73/64 | 80/70 | 85/75 | 90/80 | 94/86 |
| 2 | | 54/48 | 64/55 | 70/60 | 77/66 | 87/74 |
| 3 | | | 50/44 | 58/50 | 68/56 | 78/64 |
| 4 | | | | 48/43 | 58/48 | 72/61 |

- The leading human's fair value moves roughly 25 → 46–50 after lap 1 → 58–67 at the start of the final lap → 71–77 mid final lap → 100.
- Report 2's heuristic values are higher: 75–85 at the start of the final lap when the leader is 3 or more places clear, and 90–95 in the last third. **Size Series-1 trades off the lower numbers above**, and use Report 2's only once the log supports them.

**Expected final points from overall place at the start of the final lap** (sd 2.4–2.8). Use these to reprice #1 and Top-2 live:

| Place | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| E[final points] | 13.3 | 11.5 | 10.2 | 9.3 | 8.4 | 7.7 | 6.9 | 6.2 | 5.4 | 4.6 | 3.7 | 2.6 |

- Example: the series leader sitting 1st at the start of the final lap is worth about **+4.8 points** (13.3 against a pre-race 8.5) on #1, i.e. about 4,800 at 1000 lots.
- That full effect applies only to a leader who is clear. In general, scale the shift by the probability that this human ends the series as #1 (or in the top two, for Top-2).

**Two contenders in the final race (Race 4 / 8), live.**
- Convert live places to points (15 / 12 / 13 − place) and compare the swing with the lead L.
- Leader / trailer pre-race values with equal skill: L = 2 → 60/40, 5 → 80/20, 10 → 97/3, 13 → 99.7/0.3, 14 → 99.9/0.1 (the trailer can only tie).

### 3.11 What a point is worth, and the risk per market

**Value of one point of mispricing at the per-contract limit:**

| Market | Value per point at the limit | 1-sd settlement risk of a full position, pre-series |
|---|---|---|
| Race winner (100) | 100 | ≈ 4,300 (at p = 25) |
| Series winner (200) | 200 | ≈ 8,700 (at p = 25) |
| #1 (1000) | 1,000 | 5,000 (S2) to 7,600 (level unknown) |
| #2 (1000) | 1,000 | 4,100 (S2) to 7,300 (level unknown) |
| Top-2 (1000) | 1,000 | 7,700 (S2) to **13,500 (level unknown)** |

- #1 + #2 + Top-2 together are up to 2,000 "Top-2-equivalent" lots on the same level factor, about **27k of sd**.
- Sd of the change in Series-2 fair values (shock-free model, τ = 0.6; shrink by about 15% for the central model):

| Information | S2 Top-2 | S2 #1 | S2 #2 | S2 series winner | One future race winner |
|---|---|---|---|---|---|
| Race 1 | 8.3 | 3.9 | 4.4 | 8.5 | 5.1 |
| Races 1–4 | 10.4 | 4.9 | 5.5 | 15.2 | 9.0 |

---

## 4. Game plan, phase by phase

Sizes are percentages of the per-contract limit. If Slack says the limit is per market type, divide score-contract sizes by about 3 and race- and series-contract sizes by about 4 (§2.2).

### Phase 0: before the day
See the checklist in §7. Post the §8 questions now.

### Phase 1: market open to the Race-1 green light

1. **Arbitrage scan** across all 8 races and both series (§3.9). Take any violation at the full available size, starting with the stale leg.
2. **Probe the book.** Is there a house bot? What does it quote for #1 and Top-2? A house quote near 4 × 9 = 36 for #1, or below 40, is the naive price (fair is about 45). That reveals how much structural edge will be available all day.
3. **Quote two-sided, wide and small:**

| Market | Quote | Size |
|---|---|---|
| Race winner (all races) | 21 / 29 | 10–25 lots |
| Series winner | 20 / 30 | 10–25 lots |
| #1 | 40.5 / 49.5 | 25–100 lots |
| #2 | 33 / 41 | 25–100 lots |
| Top-2 | 76 / 88 | 25–100 lots |

   Quote both series identically.
4. **Outright score trades only outside the robust bands** (#1 36–52, #2 29–45, Top-2 65–97), at ≤ 20% of the limit. Example: sell #1 bids at 55 or more. Those come from teams that assume the top human always scores 15, which only happens in about 58% of races at level M.
5. **Level-neutral trades** (≤ 300 lots each):
   - **#1 − #2 spread:** buy it at ≤ 4.5 (positive EV in every scenario); sell only at ≥ 10.5. Fair is about 7.7, with a range of 4.9–10.3.
   - **Series 1 versus Series 2, same contract:** fair values are identical before Race 1, and warm-up argues Series 2 ≥ Series 1. Act only on gaps of ≥ 5 points (the difference has sd ≈ 11 per lot): buy the cheap one, sell the rich one.
6. **Fade fan premia:** sell any character's race contract bid at 30 or more before Race 1; the stats are identical.
7. **Sixty seconds before the green light:** cancel **every** quote whose fair value depends on Race 1. That is effectively all of them: the current race, the current series, future races, and every Series-2 contract (red-team correction M3). The only quotes that may stay are stink bids and offers at least 8 points from fair on *future* contracts.

### Phase 2: during every race

1. **Spotter** calls places of the four humans every ~5 seconds and on every change, using quadrant codes ("Y2 D5 P7 B11", "BLUE on Y"). The **Brain** logs the human order and gaps at each lap line.
2. **If the current series' score and series contracts trade live** (they should, per the packet):
   - **Gun B** reprices #1 and Top-2 at the start of the final lap from §3.10 and trades edges of **≥ 3 points**: ≤ 200–300 lots in Series 1, up to 500 in Series 2 once the log validates the tables.
   - In Race 4 / 8, trade the series contracts off the live points gap (§3.10).
3. **If the current race's race-winner contracts trade live [?]:**
   - Gun A trades separation only at the start of the final lap or later.
   - Buy when the offer is ≥ 10 points below the §3.10 table value. Cap size at **25–50 lots in Series 1** (red-team correction M6).
   - Any stink quotes in the current race must be at ≤ 1 / ≥ 99, or gone, by the start of the final lap.
4. **Finish line** (red-team correction C1):
   - The race-winner outcome is fixed the moment the first human crosses.
   - Pre-loaded tickets are **BUY Limit 90 / SELL Limit 10**.
   - Go to 95 / 5 only with two-person confirmation by screen quadrant, and only if Slack confirms that post-outcome trades are not busted.
   - Never buy at 97–99: with 2% misidentification risk, the break-even price is 98.
5. **When the last human crosses,** every human's race points are known. The current-series score contracts are now worth the realised points plus the formula for the remaining races. Hit any quote ≥ 2 points off.

### Phase 3: between races (aim to finish in 60 seconds)

1. Record **all four humans' overall places** and update the standings.
2. Update m̂ (β table) and m_i (γ table), or run `fv_calc.py --hist … --scores … --left r` and apply the §3.4 haircut.
3. **Current-series score fair values** (§3.6). Hit stale quotes ≥ 2 points off fair, up to 30–50% of the limit. These are the largest immediate mispricings after every race.
4. **Series-2 score contracts** (Series 1 only): use §3.4 or the k rule. Trade per Phase 4.
5. **Current-series winner** from the §3.7 matrices plus the skill premium. Trade edges ≥ 5 points at 50–100 lots. Check for locks and tie-prone standings.
6. **Future race-winner and Series-2 series contracts:** learned p_i, and p^1.7 for the Series-2 series. **Fade overreaction:** if the last race winner's next-race contract is bid at 45–50 when fair is about 36, sell 25–50 lots (about +1,300 EV per 100 lots at 50).
7. **Consistency scan** (§3.9).
8. **Re-quote** with these half-spreads around fair:

| Market | After Race 1 | After Race 3 |
|---|---|---|
| Race winner | ±3 | ±3 |
| Series winner | ±4 | ±2–3 (stay out of 2–98 unless locked) |
| #1 / #2 | ±2 | ±1 |
| Top-2 | ±3 | ±1.5 |
| Series-2 contracts | ±4 | ±3 |

9. Check reserved capacity and the factor caps (§6). Cancel before the next green light.

### Phase 4: right after Race 1 (the key repricing)

Compute T, the four humans' total points. Look up the row in §3.4, or use S2 Top-2 ≈ 82 + 3.1·(T/4 − 8.5).

| Race 1 total T | Read | Series-2 action (and Series-1 #1 / Top-2, about 0.75× the level move) |
|---|---|---|
| ≥ 42 | Strong humans (S3-like) | S2 Top-2 fair 88–91, #1 48–49. Buy Top-2 offered ≤ 85 and #1 ≤ 45.5 |
| 35–41 | Moderately strong | Top-2 fair 83–87. Trade only gaps ≥ 4 |
| 28–34 | Little level information | Stay near the prior (about 78–82). **Check the spread:** does one human dominate? |
| 22–27 | Weak-ish | Top-2 fair 73–77. Sell Top-2 bid ≥ 82 |
| ≤ 21 | Weak (S1-like), or one strong human and three weak | Top-2 fair 56–73. Sell Top-2 bid ≥ 78 and #2 bid ≥ 36 |

**Sizing:**
- Immediately: **30–40% of the limit** on S2 Top-2, and 20–30% each on S2 #1 and #2.
- After Race 2: add up to 60% if it confirms the direction. If it contradicts, cut back to the new fair value.
- Never exceed the level-factor cap (§6.2).

**Individual skill:** if one human looks dominant (e.g. 1, 10, 11, 12), buy that human's S2 series contract up to fair minus 5. Fair is about 59 in that example and about 42 for a 1, 4, 7, 10 race. Keep it to ≤ 50–100 lots until Race 2 confirms.

### Phase 5: Race 4 (end of Series 1) and the switch to Series 2

1. **During Races 3–4,** the Brain pre-computes a Series-2 grid for the 2–3 plausible Race-4 outcomes: x̄_S1 (the Series-1 average human points per race) gives S2 Top-2 = 82 + 5.5·(x̄_S1 − 8.5). Add the individual skill picture (S2 series via p^1.7).
2. **Live in Race 4:** trade the series contracts off the live points gap and the score contracts off expected final points. Check the tie-prone standings list in §3.9 before selling any basket.
3. **At the finish:** Series-1 contracts are exact. Take pick-offs only if a window exists [?].
4. **Fire the Series-2 grid immediately.** Most teams look at Series 2 only after Series 1 settles.

### Phase 6: Series 2, Races 5–7

- Same routine, with a tighter posterior: β about 0.8, γ about 0.6–0.7.
- Use the Series-1 lap log to recalibrate §3.10. If the hit rates hold up, raise the live caps to 50–100% for in-race edges at ≥ 90% confidence and ≥ 10 points.

### Phase 7: Race 8 (endgame)

- Exact-value pick-offs as at Race 4.
- **If a leaderboard is visible:**
  - If the team is just outside the top 3, accept more variance on Race 8 live trades.
  - If it is comfortably top 3, flatten level and skill bets that have no edge left, and harvest only the low-variance trades.

---

## 5. Edges ranked by expected value

The EV figures are **rough judgments** [I]. They assume per-contract limits and some stale liquidity.

| # | Edge | Why it exists | Rough EV | How to execute |
|---|---|---|---|---|
| 1 | **Reprice Series-2 (and remaining Series-1) score contracts after Race 1 and each Series-1 race** | Race 1 moves S2 Top-2 by sd ≈ 7–8 points. Most teams look only at the current race and series. | 3–6k over Series 1 | §3.4 table, Phase 4 sizes, scale in over Races 1–3 |
| 2 | **Structural score pricing in both series** (selection premium, the leader's own m, tight bounds) | A naive #1 price of "4 × average" misses +8; Top-2 misses +10; mid-series, the premium is +4.5 to +9. | 2–5k | §3.6 formulas. Hit quotes ≥ 2–3 points off, at 30–50% |
| 3 | **Live score and series trading during races** (if open) | Place at the start of the final lap is worth up to ±5 points on 1000-lot contracts | 0.5–1k per race, contested | Gun B at the start of the final lap, ≤ 200–300 lots in Series 1 |
| 4 | **Series-winner lead and learning pricing** | Teams price leads linearly, ignore bunching, ties and the leader's skill | 1–3k per series | §3.7 matrices plus skill premium; ≥ 5-point edges at 50–100 lots |
| 5 | **Stale quotes on outcomes already decided** (finish line, last human across, end of series) | Resting quotes go stale before settlement | 0–3k per race, **only if a window exists and trades are not busted** | Pre-loaded 90/10 tickets; score contracts at realised value ± 2 |
| 6 | **Hard arbitrage** (race basket, series basket buy, score identities, bounds, locks) | Thin, inconsistent student books | 0.3–2k each, riskless | §3.9; lead with the stale leg |
| 7 | **Fading overreaction in future race-winner contracts** | Hot-hand bias after one race | 0.5–1.5k each | Sell the hot human's future race contracts at fair + 8 or more; same-human calendar spreads |
| 8 | **Market making between races** | Two-sided flow while no information is arriving | 0.2–1k per gap | §4 widths; pull at every green light |
| 9 | **Level-neutral spreads before Race 1** (#1 − #2; Series 1 versus Series 2) | Inconsistent quotes before any information | 0.5–1.5k | Phase 1, step 5 |

**What not to do:**
- Take outright level bets before Race 1 inside the bands.
- Pay 97–99 at the finish line.
- Sell the series basket below 101.
- Treat one hot race as proof of skill.

---

## 6. Risk management

1. **Order hygiene.**
   - Use **Limit orders only**, always priced to fill immediately with a cap. Never use Market orders in a thin book.
   - The quantity buttons stop at 50, so practise typing 100, 200 and 500.
   - No cancel-all button is visible [?], so **keep few resting orders**: at most about 6 per trader, logged on paper.
2. **Factor caps.**
   - **Level factor.** Count L = Top-2 + 0.5·(#1 + #2) for Series 2, plus (races left / 4) × the same for Series 1. Cap |L| at **≤ 300 before Race 1, ≤ 700 after Race 1, ≤ 1,000 after Race 2**. Even after Race 1, each L-lot carries about 10 points of sd.
   - **One human's skill.** Future race-winner lots plus 2 × series-winner lots (both series) on any one human: **≤ 400 net**.
   - **Cross-series hedge.** Series-1 score contracts carry (races left)/4 of Series-2's level exposure. If one series' quotes are stale and the other's are fresh, trade the stale one and hedge with the fresh one in that ratio.
3. **Reserve capacity.**
   - Keep ≥ 50 of the 100 race-winner capacity free going into each race (if live trading exists).
   - Keep ≥ 100 of the 200 series capacity free going into Races 4 and 8.
   - Keep ≥ 300 of the 1000 score capacity free for the Race-1 repricing.
4. **Tail risks.**
   - Short series baskets lose 49 or 99 per set on 3- or 4-way ties: sell only at ≥ 101 / 102.5, ≤ 50 baskets.
   - Legging risk on multi-leg trades (§3.9).
   - Busted trades on post-outcome fills: assume busts are possible until Slack says otherwise.
5. **Identification risk.**
   - The characters are visually distinct [P], but split-screen quadrants are small.
   - Before **every** race, write the quadrant → character → contract ticker mapping on the sheet.
   - All calls use quadrant codes. A second person confirms before any finish-line trade above 90.
6. **Objective-aware risk policy** (red-team correction M7).
   - **If Mario counts 20% overall:** minimise expected rank. Keep the factor caps, take every low-variance edge, and do not double up after losses.
   - **If it does not count:** only Mario's top-3 prizes matter. Accept somewhat larger level and skill positions (caps × 1.5) where the edge is real, and more variance late if the team is just outside the top 3.
7. **Model risk.**
   - Warm-up or fatigue between series, track effects, and players swapping characters all break the learning [?].
   - Recalibrate in-race probabilities from the Series-1 log.
   - When `fv_calc.py` and the tables disagree by more than 2 points, trade the smaller of the two edges.

---

## 7. Team roles and preparation checklist

**Roles** (4 people; with 3, merge the Brain into Gun B):

| Role | Owns | Never does |
|---|---|---|
| **Spotter (Eyes)** | Watches only the race. Calls quadrant codes every ~5 seconds and on every change, plus "SHELL on …" and "X IN" as each human crosses. Keeps the mapping sheet. | Touch a keyboard |
| **Gun A** | Current race-winner contracts (if live), finish-line tickets, all arbitrage scans, between-race market making in race and series contracts | Hold quotes through a green light |
| **Gun B** | Current-series score and series contracts: live repricing, post-race pick-offs, Race 4/8 endgame | Trade Series 2 without the Brain's number |
| **Brain (quant / scribe)** | Standings, m̂ / m_i, `fv_calc.py` or the tables, Series-2 and future-race fair values, factor-cap tracking, lap-line log, Race-4 pre-computed grid | Chase fills |

If Slack says the current race does not trade live, Gun A becomes the second Series-2 trader and runs the after-race repricing burst with the Brain.

**Preparation checklist:**
- [ ] Post the §8 questions; update this playbook's defaults with the answers.
- [ ] Print §3.0, §3.3–3.4, §3.6 (formulas and C table), §3.7 (A and B matrices), §3.9 and §3.10. Bring the full matrices in `cheatsheet_tables.md`.
- [ ] Test `fv_calc.py` on a laptop that is **not** used for order entry. Usage: `python3 -I fv_calc.py --hist "4,1,7,9;6,2,5,10" --scores 16 27 14 7 --left 2`. Scores and places are in contract order H1..H4, not sorted. Use `--K 200000` with 6 or more races of history. Apply the §3.4 haircut. Have a spreadsheet or paper fallback in case scripts are banned.
- [ ] Drill the UI: switching contracts, typing quantities ≥ 100, Limit vs Market, cancelling one order quickly, reading the "YOU" column and the Positions panel.
- [ ] Rehearse spotting on recorded 4-player split-screen Mario Kart 8 Deluxe races with CPUs. This trains the call vocabulary and gives a first calibration of the §3.10 final-lap probabilities.
- [ ] Prepare log sheets. Per race: track, quadrant mapping, the four humans' places at each lap line, final places, and running standings.
- [ ] Agree the risk policy for both answers to the gold-sponsor question, and the factor caps.
- [ ] Agree who presses what at the finish line and the two-person confirmation rule.

---

## 8. Questions for the #questions Slack channel (priority order)

1. Is the **current race's race-winner market** open while that race runs? Are the current series' series-winner and score markets? When does each halt, how long after the finish does settlement happen, and are trades made after the outcome is visible ever **busted**?
2. Does Mario Markets **count toward the overall ranking**, given the gold-sponsor note and the 20% weight in the formula?
3. Are the **position limits** (200 / 100 / 1000) per contract or per market type? Net or gross? Do resting orders count?
4. Can teams **go short** (down to −limit)? Are there cash or margin constraints, fees or contract multipliers?
5. Who provides **liquidity**: a house market maker or bot, or only teams? What are the opening prices?
6. Are the **same four people on the same characters** for all 8 races? Who are the racers (organisers, sponsor staff, volunteers)?
7. **Game settings:** engine class (cc), CPU difficulty, items, smart steering or auto-accelerate, controllers, split-screen or separate consoles?
8. Are **Series-2 contracts listed and tradable from the open**, and through any break between series?
9. How is the race **shown** (projector, stream, any delay)? Are **full 12-place results** posted, where and how quickly?
10. Please confirm the **tie rule:** in a 3-way or 4-way tie for the series lead, does every tied racer's contract settle to 50?
11. What happens on a **restart, disconnect or DNF**?
12. **Tick size, price bounds** (0–100 for binaries?) and maximum order size?
13. Can several team members be **logged in at once**? Is there a **cancel-all**? Self-trade prevention?
14. Are **pricing calculators or spreadsheets** allowed if all order entry is manual?
15. **Track pool:** base game or Booster Course? Can tracks repeat? Is the track announced before trading on that race halts?
16. How long is the **gap between races**, and is it fixed? Is there a live leaderboard? Does Mario run at the same time as another case?

---

### Sources and verification
- **Analyst work:**
  - Monte Carlo model, tables and calculator: `…/work/mario/quant/` (`fv_calc.py`, `cheatsheet_tables.md`, `configs_full.csv`).
  - Bounds, ties and carry-over: `…/work/mario/tactics/`.
  - Red-team checks: `…/work/critic-mario/`.
- **Final verification scripts** in `…/work/final/`, all standard library only, each run with `python3 -I`:

| Script | What it checks |
|---|---|
| `verify1.py` | Exact one-race-left enumeration: basket scan over 680 gap patterns, worked example, duel values |
| `verify2.py`, `verify4.py` | Level-learning β and the k slopes, with and without race-to-race shocks; premiums and #1 − #2 spread by τ |
| `verify3.py` | Series-2 posterior table after Race 1 (§3.4) |
| `verify5.py` | Individual-skill γ |
