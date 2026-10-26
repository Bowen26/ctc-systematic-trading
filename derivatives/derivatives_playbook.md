# CTC 2026 Derivatives Case: Team Playbook (OIL / PLANE options)

> **Files in this folder:** everything listed in Appendix B is in `tools/` (`deriv_live.py`, `analyst.py`, `r3_invert.py`, `bs.py`, `live_tools.py`, `event_log_template.csv`, plus the verification scripts). Paths below that start with `work/` refer to the original analysis workspace.

Tags: **[P]** the packet or a screenshot states it. **[I]** our inference or computation. **[Q]** ambiguous; ask in #questions or test live.

All numbers below were recomputed with a standard-library Black-Scholes model (r = 0, T on a 252-day basis, vega per vol point, theta per trading day). It matches the exchange calculator to 4 decimals. Live tools are in `work/final/`. Run every script with `python3 -I`.

---

## 1. How this case is won

This is a **stale-quote game**. At each news release (T+0), the headline and, we believe, the new underlying prices are made public. The bots keep quoting options off the old underlying until T+60 and off the old vol until T+120. Whatever sits between the stale quote and BS(new S, new IV) is free money, minus about $1.005 of half-spread per contract.

**About 90% of that money is a price (delta) edge in the first 60 seconds**, and it barely depends on vol. It is captured first through the deep in-the-money (ITM) **60C** (Δ ≈ 0.98) and **140P**, then through the at-the-money (ATM) option in the same direction. A pure vol trade needs at least 9 vol points just to cover the half-spread at ATM.

The case is won in four steps:
- **Precompute:** the stale bot prices you will hit are known before the news, so write them down in advance.
- **Be fast and correct:** one trader per underlying; limit orders priced **exactly at each bot level**.
- **Take every level whose net edge is positive,** up to the limits, and never take unhedged directional guesses.
- **Use every snap as a measurement:** read the true IV to grade the analysts (R2) and decode the "quantity linked to IV" (R3).

Scoring is Σ(round rank)^1.5, which punishes a bad round more than it rewards a great one. So **three solid top-5 rounds beat two wins and a disaster.**

---

## 2. Rules that matter

### 2.1 Facts

| Fact | Source |
|---|---|
| Two underlyings, OIL and PLANE. 3 calls and 3 puts each, **12 contracts**. European, one expiry Jun 26 16:00, 20 trading days after the start. **The underlying cannot be traded.** | [P] |
| 3 rounds (one "trading day" each), 10 events (3 / 3 / 4). Each event lasts **2.5 real minutes**. | [P] |
| Bots "adjust the underlying price **1 minute** after event release", and "the correct volatility is priced in for options **2 minutes** after". | [P] |
| "Shortly after each event, the news and the new prices of both underlyings are announced publicly." | [P]; timing [Q] |
| R1: new IV of both underlyings announced with each event. R2: 3 analysts each publish an IV estimate per underlying; "reliability varies across analysts and across events". R3: analysts publish "a quantity linked to IV", explained live. | [P] |
| The packet suggests using the IV calculator after the snap "to see which analyst(s) were correct". | [P] (hints that some analysts may be exactly right; see §4.4) |
| Teams can post their own markets and trade with other teams and bots. **Manual UI only; no bots.** | [P] |
| Options are "traded via bid/ask spreads and executed at the midpoint". | [P]; meaning [Q], critical |
| PnL is "calculated at the end of each round once the underlying value is revealed". | [P]; meaning [Q], critical |
| Within each round teams are ranked by PnL. Case score = Σ rank^1.5 (lower is better). The case is **35%** of the overall rank. | [P] |
| The built-in calculator takes SPOT and VOL as inputs. The packet says "Other inputs (time to expiry and interest rate) are fixed". Rate = 0%. | [P] |
| OIL strikes are 60 / 100 / 140. Bot books have **3 levels × 200 lots** per side, **$1.00 apart**, and each quote is 2.01 wide. | screenshot [P] |
| 50 lots @ 10.2 shows a notional of $510, so **the multiplier is 1** ($1 of PnL per 1.00 of price per contract). | screenshot [P] |
| The UI labels OIL "Newtonian Crude Oil". | screenshot [P] |

### 2.2 What the screenshot tells us [I, verified]

- **The bots quote around a reference spot of exactly 100.00 at a flat 99.02% IV, not around the displayed live tick of 99.89.**
  - Put-call parity on the mids gives S = 100.000 at K = 100 and at K = 140. These are the two independent strikes, because the 60P has no bid.
  - The quoting rule **bid = floor(fair − 1.00), ask = ceil(fair + 1.00)**, applied to fairs at 99.02%, reproduces **all six quotes to the cent**, including "–" / 1.29 on the 60P.
  - So each mid is within ±0.005 of fair, and the effective half-spread is 1.00–1.01. **Use 1.005.**
- **The bots post no bid when fair < 1.00.** Deep out-of-the-money (OTM) longs (for example the 60P) cannot be sold back to the bots.
- **The live tick and the bots' reference spot differ.** Two consequences:
  - The IV tab may compute IV with the wrong spot. See the readout fix in §3.8.
  - The end-of-round "underlying value" may differ from the bots' reference [Q].

### 2.3 Clock card (exchange time shown in the UI header) [P + I]

R1 and R2 run at 52 exchange-minutes per real minute; R3 at 39. Events are back-to-back with no gap, and each round ends 30 real seconds after its last snap. **A whole round lasts 7.5 real minutes (R1/R2) or 10 (R3).**

| Ev | T+0 news | T+15s | T+55s: cancel unfilled directional orders | T+60s: underlying moves | T+115s: last vol order | T+120s: snap | T+145s: CANCEL ALL | Next |
|---|---|---|---|---|---|---|---|---|
| 1 / 4 | 09:30:00 | 09:43:00 | 10:17:40 | 10:22:00 | 11:09:40 | 11:14:00 | 11:35:40 | 11:40 |
| 2 / 5 | 11:40:00 | 11:53:00 | 12:27:40 | 12:32:00 | 13:19:40 | 13:24:00 | 13:45:40 | 13:50 |
| 3 / 6 | 13:50:00 | 14:03:00 | 14:37:40 | 14:42:00 | 15:29:40 | 15:34:00 | 15:55:40 | 16:00 close |
| 7 | 09:30:00 | 09:39:45 | 10:05:45 | 10:09:00 | 10:44:45 | 10:48:00 | 11:04:15 | 11:07:30 |
| 8 | 11:07:30 | 11:17:15 | 11:43:15 | 11:46:30 | 12:22:15 | 12:25:30 | 12:41:45 | 12:45 |
| 9 | 12:45:00 | 12:54:45 | 13:20:45 | 13:24:00 | 13:59:45 | 14:03:00 | 14:19:15 | 14:22:30 |
| 10 | 14:22:30 | 14:32:15 | 14:58:15 | 15:01:30 | 15:37:15 | 15:40:30 | 15:56:45 | 16:00 close |

Event 1 of every round fires at the 09:30 open, so the setup has to happen before the bell.

### 2.4 The unknowns that change the plan

Set these switches from Slack answers, or from the Event 1 tests in §4.3. Defaults are shown in **bold**.

| Unknown | Answer → what changes |
|---|---|
| **Fill rule**: what "executed at the midpoint" means [Q] | **I. Fill at the resting order's price** (a normal limit order book; the UI's limit ticket suggests this): take level k only if \|edge\| > 1.005 + (k−1). II. Every fill prints at the book mid: every edge above model error is profitable, closing is free (so close after every snap), and position limits become the binding constraint. III. A crossing order fills at the midpoint of your limit and the resting price: you must price exactly at the resting level, because a limit priced deep through the book gives away half the distance. **Pricing exactly at each level is optimal under all three readings**, so it is the rule. |
| **Round-end mark** [Q] | **BS fair at the final S and IV, or bot mid**: carrying inventory is fine. Intrinsic value at a revealed price: every long option loses its time value (ATM 100C at 19 days ≈ 10.92, 140C 1.72, 60C 0.26), so close all longs before 16:00; selling time value then becomes a large edge (§5, contingent). A new random "revealed" value: flatten delta and gamma before 16:00. **Until it is answered: never hold net-long time value or large \|Δ\| into 16:00.** |
| **When S1 is public** [Q] | **At T+0 with the headline**: the main plan. Only at T+60: use the fallback in §4.7. |
| **Option bots in Window B** (T+60 to T+120) [Q] | **They reprice to BS(new S, old IV)**: Window B is vol-only. They stay fully stale: Window A effectively lasts until T+120. |
| **Bot liquidity** [Q] | **Shared by all teams and not refilled**: speed decides; split contracts between traders; slower fills come from levels 2–3 and second-tier contracts. Refilled: limits bind, so size each line to the limit. |
| **T convention** [Q] | **T decays continuously within the round** (20 → 19 in R1, 19 → 18 in R2, 18 → 17 in R3): use the per-event T in §3.7. Fixed at 20: ATM prices are about 0.26 higher by the end of R1, and R3 inversions shift by up to 5 vol points. **Always read T off the Fair tab.** |

---

## 3. Core model and cheat sheets (print these)

### 3.1 Calculator match [P, verified]
For the 100C at S = 100, vol = 100%, T = 20/252:

| Fair | Δ | Γ | Vega/pt | Θ/day |
|---|---|---|---|---|
| 11.2019 | 0.5560 | 0.0140 | 0.1113 | −0.2782 |

The IV of 11.2 is 99.98%. Both match the UI exactly.

### 3.2 Greeks at S = 100, T = 20 days (calls)
**For puts:** same Γ, vega and θ; put Δ = call Δ − 1; put price = call − S + K.

| Vol | 60C px / Δ / vega | 100C px / Δ / vega / θ | 140C px / Δ / vega |
|---|---|---|---|
| 60% | 40.005 / 0.999 / 0.001 | 6.735 / 0.534 / 0.112 / −0.168 | 0.174 / 0.028 / 0.018 |
| 80% | 40.070 / 0.991 / 0.007 | 8.972 / 0.545 / 0.112 / −0.223 | 0.791 / 0.084 / 0.043 |
| 100% | 40.299 / 0.975 / 0.017 | 11.202 / 0.556 / 0.111 / −0.278 | 1.879 / 0.146 / 0.065 |
| 120% | 40.741 / 0.954 / 0.027 | 13.423 / 0.567 / 0.111 / −0.332 | 3.332 / 0.204 / 0.080 |
| 150% | 41.776 / 0.922 / 0.041 | 16.734 / 0.584 / 0.110 / −0.412 | 5.971 / 0.279 / 0.095 |

**ATM vega is about 0.11 per vol point at every vol level.** A 10-point IV move is worth only about $1.1 per contract, roughly one half-spread.

### 3.3 Call price sheet, T = 19 days (backup when the laptop tool is unavailable; put = call − S + K)
For other T values, use the Fair tab or `deriv_live.py sheet --days <T>`. An ATM price changes by about 2.6% per trading day of T.

| S | 60C @60% | @80% | @100% | @120% | @140% | 100C @60% | @80% | @100% | @120% | @140% | 140C @60% | @80% | @100% | @120% | @140% |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 60 | 3.94 | 5.25 | 6.55 | 7.85 | 9.15 | 0.00 | 0.06 | 0.26 | 0.66 | 1.26 | 0.00 | 0.00 | 0.01 | 0.05 | 0.17 |
| 65 | 7.08 | 8.32 | 9.61 | 10.92 | 12.23 | 0.02 | 0.17 | 0.55 | 1.18 | 2.02 | 0.00 | 0.00 | 0.02 | 0.10 | 0.31 |
| 70 | 11.00 | 12.02 | 13.18 | 14.41 | 15.68 | 0.07 | 0.40 | 1.04 | 1.94 | 3.03 | 0.00 | 0.00 | 0.05 | 0.21 | 0.53 |
| 75 | 15.45 | 16.19 | 17.15 | 18.26 | 19.45 | 0.23 | 0.85 | 1.80 | 2.99 | 4.33 | 0.00 | 0.01 | 0.11 | 0.37 | 0.85 |
| 80 | 20.19 | 20.68 | 21.44 | 22.39 | 23.47 | 0.60 | 1.58 | 2.87 | 4.34 | 5.93 | 0.00 | 0.04 | 0.22 | 0.63 | 1.30 |
| 85 | 25.07 | 25.38 | 25.95 | 26.74 | 27.70 | 1.30 | 2.69 | 4.30 | 6.03 | 7.82 | 0.01 | 0.09 | 0.41 | 1.01 | 1.89 |
| 90 | 30.03 | 30.20 | 30.62 | 31.26 | 32.09 | 2.47 | 4.24 | 6.12 | 8.06 | 10.01 | 0.02 | 0.20 | 0.70 | 1.53 | 2.65 |
| 95 | 35.01 | 35.11 | 35.40 | 35.91 | 36.62 | 4.21 | 6.26 | 8.33 | 10.41 | 12.49 | 0.06 | 0.39 | 1.12 | 2.22 | 3.59 |
| 100 | 40.00 | 40.06 | 40.26 | 40.66 | 41.26 | 6.57 | 8.75 | 10.92 | 13.09 | 15.24 | 0.15 | 0.71 | 1.72 | 3.09 | 4.73 |
| 105 | 45.00 | 45.03 | 45.17 | 45.48 | 45.98 | 9.52 | 11.68 | 13.87 | 16.06 | 18.25 | 0.33 | 1.18 | 2.52 | 4.18 | 6.07 |
| 110 | 50.00 | 50.02 | 50.11 | 50.34 | 50.76 | 13.01 | 15.03 | 17.14 | 19.31 | 21.49 | 0.65 | 1.87 | 3.54 | 5.49 | 7.62 |
| 115 | 55.00 | 55.01 | 55.07 | 55.25 | 55.59 | 16.94 | 18.73 | 20.72 | 22.80 | 24.94 | 1.18 | 2.81 | 4.82 | 7.04 | 9.38 |
| 120 | 60.00 | 60.00 | 60.04 | 60.18 | 60.46 | 21.22 | 22.73 | 24.54 | 26.52 | 28.59 | 2.00 | 4.04 | 6.36 | 8.82 | 11.36 |
| 125 | 65.00 | 65.00 | 65.03 | 65.13 | 65.35 | 25.74 | 26.98 | 28.59 | 30.43 | 32.41 | 3.17 | 5.59 | 8.17 | 10.84 | 13.56 |
| 130 | 70.00 | 70.00 | 70.02 | 70.09 | 70.28 | 30.44 | 31.42 | 32.82 | 34.51 | 36.38 | 4.74 | 7.47 | 10.27 | 13.11 | 15.95 |
| 135 | 75.00 | 75.00 | 75.01 | 75.07 | 75.21 | 35.26 | 36.00 | 37.21 | 38.74 | 40.49 | 6.75 | 9.69 | 12.65 | 15.60 | 18.55 |
| 140 | 80.00 | 80.00 | 80.01 | 80.05 | 80.17 | 40.15 | 40.71 | 41.72 | 43.09 | 44.73 | 9.19 | 12.24 | 15.29 | 18.32 | 21.34 |

### 3.4 Identities and mental math [I, exact]

**Definitions:**
- **Edge** = BS(S1, σ1) − stale mid. Positive means buy; negative means sell.
- **Net edge at bot level k** = |edge| − (1.005 + (k−1)).

**Window A identities** (quotes still at S0, σ0):
- **Put edge = call edge − dS** at the same strike.
- **A synthetic forward** (long C, short P, same K) earns **exactly dS** for any vol change. It is the vol-free delta instrument, but it crosses two spreads (2.01).

**Window B identity** (quotes at S1, σ0): call edge = put edge at the same strike ≈ vega × dσ. Trade the straddle at the strike nearest S1.

**Taylor approximation** around σ0 = 100%, T ≈ 20 days (dS in $, dσ in vol points):

| Contract | Edge ≈ |
|---|---|
| 60C | 0.975·dS + 0.017·dσ |
| 100C | 0.556·dS + 0.007·dS² + 0.111·dσ |
| 140C | 0.146·dS + 0.004·dS² + 0.065·dσ |

For puts, subtract dS from the call at the same strike. Use the tool once |dS| > 5% or |dσ| > 20.

### 3.5 Exact break-evens at S = 100, σ = 100%, T = 20 (one shock at a time)

| Contract | \|dS\| to clear 1.005 (up / down) | \|dS\| to clear 2.01 | dσ to clear 1.005 (up / down) |
|---|---|---|---|
| 60C | $1.03 / $1.03 | ≈ $2.07 | +37.8 / never |
| 140P | $1.18 / $1.17 | ≈ $2.37 | +14.3 / −18.1 |
| 100C | $1.77 / $1.85 | $3.5 / $3.8 | **+9.0 / −9.0** (18 to cover a round trip) |
| 100P | $2.35 / $2.19 | $4.9 / $4.3 | +9.0 / −9.0 |
| 140C | $5.9 / $8.9 | $10.5 / never | +14.3 / −18.1 (−2.01 cannot be reached) |
| 60P | never / $17.8 | – | +37.8 / never |

### 3.6 Window A decision card (largest edge first; illustrative shock ±5% and ±20 vol points)

| | **Vol DOWN** | **Vol FLAT** | **Vol UP** |
|---|---|---|---|
| **S UP** | Sell puts, buy the deep call: S 140P 5.58, B 60C 4.74, S 100P 4.29 | B 60C 4.90, S 140P 4.16, B 100C 2.95, S 100P 2.05 | Buy calls: B 60C 5.24, B 100C 5.20, B 140C 2.58, S 140P 2.42 |
| **S FLAT** | Sell the 100 straddle: 2.23 per leg (−20); 140s only if beyond −18 | Nothing | Buy the 100 straddle 2.22 per leg, plus the 140s 1.45 per leg (+20) |
| **S DOWN** | Sell calls, buy the deep put: S 60C 5.17, S 100C 4.73, B 140P 3.57, S 140C 1.43 | S 60C 4.84, B 140P 4.37, S 100C 2.60, B 100P 2.40 | Buy puts: B 140P 5.53, B 100P 4.53, S 60C 4.29 |

**Deep ITM goes first** unless the vol move is large relative to the price move. ATM overtakes deep ITM when:

| dS | S up, vol up (buy 100C) | S up, vol down (sell 100P) | S down, vol up (buy 100P) | S down, vol down (sell 100C) |
|---|---|---|---|---|
| ±2% | \|dσ\| > 9 | > 17 | > 19 | > 10 |
| ±5% | > 21 | > 40 | > 53 | > 25 |

Rule of thumb: ATM first only if |dσ| > about 4.5–5 × |dS%| (when trading calls) or about 9 × |dS%| (when trading puts).

**Full click lists** at S0 = 100, σ0 = 100%, T = 20, for contracts with |edge| > 1.105. "L1 $" assumes 200 lots of each listed contract at level 1; the residual is measured at the new fair.

| dS | dVol | Click order (gross edge per contract) | L1 $ | L1–L3 $ | L1 residual Δ / vega |
|---|---|---|---|---|---|
| −10% | −20 | S 60C 10.06, B 140P 8.36, S 100C 6.77, B 100P 3.23, S 140C 1.64 | 5,008 | 12,368 | −594 / −3.3 |
| −10% | 0 | S 60C 9.61, B 140P 8.91, B 100P 5.17, S 100C 4.83 | 4,899 | 12,297 | −573 / +1.5 |
| −10% | +20 | B 140P 9.81, S 60C 8.92, B 100P 7.16, S 100C 2.84 | 4,941 | 12,454 | −557 / +2.7 |
| −5% | −20 | S 60C 5.17, S 100C 4.73, B 140P 3.57, S 140C 1.43 | 2,176 | 4,558 | −488 / −23.3 |
| −5% | 0 | S 60C 4.84, B 140P 4.37, S 100C 2.60, B 100P 2.40 | 2,038 | 3,916 | −571 / +5.5 |
| −5% | +20 | B 140P 5.53, B 100P 4.53, S 60C 4.29 | 2,268 | 5,003 | −453 / +27.9 |
| −2% | −20 | S 100C 3.28, S 60C 2.21, S 100P 1.28, S 140C 1.24 | 800 | 1,152 | −216 / −53.1 |
| −2% | 0 | S 60C 1.94, B 140P 1.72 | 332 | 332 | −368 / +8.0 |
| −2% | +20 | B 100P 3.11, B 140P 3.06, S 60C 1.46, B 100C 1.11 | 944 | 1,409 | −335 / +52.7 |
| 0 | −20 | S 100P 2.23, S 100C 2.23 | 490 | 580 | −18 / −44.7 |
| 0 | +20 | B 100P 2.22, B 100C 2.22, B 140P 1.45, B 140C 1.45 | 666 | 752 | −91 / +76.3 |
| +2% | −20 | S 100P 3.11, S 140P 2.91, B 60C 1.76, S 100C 1.11 | 971 | 1,391 | +347 / −53.8 |
| +2% | 0 | B 60C 1.95, S 140P 1.69, B 100C 1.14 | 354 | 354 | +480 / +11.2 |
| +2% | +20 | B 100C 3.38, B 60C 2.35, B 140C 1.88, B 100P 1.38 | 994 | 1,413 | +272 / +66.8 |
| +5% | −20 | S 140P 5.58, B 60C 4.74, S 100P 4.29 | 2,319 | 5,156 | +449 / −33.5 |
| +5% | 0 | B 60C 4.90, S 140P 4.16, B 100C 2.95, S 100P 2.05 | 2,008 | 3,826 | +559 / −13.6 |
| +5% | +20 | B 60C 5.24, B 100C 5.20, B 140C 2.58, S 140P 2.42 | 2,284 | 4,654 | +518 / +26.9 |
| +10% | −20 | S 140P 9.85, B 60C 9.72, S 100P 5.96, B 100C 4.04 | 5,111 | 12,932 | +566 / −15.1 |
| +10% | 0 | B 60C 9.83, S 140P 8.10, B 100C 6.22, S 100P 3.78, B 140C 1.90 | 4,961 | 12,123 | +598 / +1.8 |
| +10% | +20 | B 60C 10.10, B 100C 8.44, S 140P 6.07, B 140C 3.93, S 100P 1.56 | 5,014 | 12,422 | +595 / +3.6 |

A full Window A basket leaves **net delta of about ±450 to ±600 in the direction of the move** for 200 lots per line, plus small vega. This matters for the carry rule in §6.2.

**Window B (vol only):** trade both legs of the straddle at the strike nearest S1, and only if |dσ| ≥ 9 points at ATM (+14 / −18 at the 140 strike). Example: S1 = 110, σ moves 100 → 125. Level 1 nets +1.74 per leg on the 100 straddle and +1.51 per leg on the 140 straddle; level 2 nets +0.74 and +0.51. That is $1,798 in total, with net vega of +174 per point.

### 3.7 Time to expiry per event (if T decays continuously; check the Fair tab live) [I/Q]

| Round | Event | T at news (days) | T at snap | ATM 100C @100% at news | ATM theta per event |
|---|---|---|---|---|---|
| R1 | E1 / E2 / E3 | 20.00 / 19.67 / 19.33 | 19.73 / 19.40 / 19.07 | 11.20 / 11.11 / 11.02 | −0.094 |
| R2 | E4 / E5 / E6 | 19.00 / 18.67 / 18.33 | 18.73 / 18.40 / 18.07 | 10.92 / 10.82 / 10.73 | −0.094 |
| R3 | E7 / E8 / E9 / E10 | 18.00 / 17.75 / 17.50 / 17.25 | 17.80 / 17.55 / 17.30 / 17.05 | 10.63 / 10.56 / 10.48 / 10.41 | −0.074 |

If T is actually fixed at 20, the ATM price at the E3 snap is 11.20 rather than 10.94. That 0.26 gap is a quarter of the half-spread. Until T is confirmed, **add a 0.30 buffer on ATM and 140-strike vol trades.**

### 3.8 IV readout after the snap (feeds every analyst scorecard)
- **Mid-rounding precision:** 0.01 of mid is about 0.09 vol points at ATM but 0.6 points on the 60C. Always read IV from the ATM strike.
- **Spot problem [Q]:** if the IV tab uses the live tick rather than the bots' reference spot, a $1 offset reads **94.97% from the call and 103.91% from the put against a true 100%**. A simple average still gives 99.44%.
- **Fix:** run `deriv_live.py iv --K 100 --C <mid> --P <mid> --days <T>`.
  - It solves for the parity spot S = K + C − P and inverts at that spot, giving the exact IV.
  - Without a laptop, use the delta-weighted average (|Δ_put|·IV_call + Δ_call·IV_put). It gives 100.06% in the $1-offset case.
- **Check at the E1 snap:** compare the announced R1 IV with the snapped IV, and confirm the smile is flat across strikes.

### 3.9 PLANE (spot, strikes and IV unknown until the screen opens) [I/Q]
- Black-Scholes is homogeneous in (S, K). If PLANE's strikes are 0.6 / 1.0 / 1.4 × spot, prices, $ edges and vegas scale by S_PLANE/100, while deltas are unchanged. **A fixed $2.01 spread is relatively wider on a cheap underlying.**

| PLANE spot | ATM vega/pt | Min \|dS\| for deep ITM to clear $1.005 | Min \|dσ\| for ATM to clear $1.005 |
|---|---|---|---|
| 25 | 0.028 | ≈ 4% | ≈ 36 pts |
| 50 | 0.056 | ≈ 2% | ≈ 18 pts |
| 100 | 0.112 | ≈ 1% | ≈ 9 pts |
| 200 | 0.224 | ≈ 0.5% | ≈ 4.5 pts |

- **At 09:30:** read PLANE's strikes, its parity spot and its IV, then run the tool with `--K <k1,k2,k3>`.

### 3.10 R3 fingerprints: candidate "quantities linked to IV" (S = 100, T = 18 days)

| IV | Variance σ² | Daily SD % | Mean abs daily $ | 1-SD $ to expiry (Sσ√T) | ATM straddle (exact) | P(\|move\| > 10%) | P(\|move\| > 20%) |
|---|---|---|---|---|---|---|---|
| 40% | 0.160 | 2.52 | 2.01 | 10.69 | 8.53 | 0.348 | 0.060 |
| 60% | 0.360 | 3.78 | 3.02 | 16.04 | 12.78 | 0.532 | 0.207 |
| 80% | 0.640 | 5.04 | 4.02 | 21.38 | 17.03 | 0.640 | 0.343 |
| 100% | 1.000 | 6.30 | 5.03 | 26.73 | 21.26 | 0.709 | 0.449 |
| 120% | 1.440 | 7.56 | 6.03 | 32.07 | 25.48 | 0.757 | 0.529 |
| 150% | 2.250 | 9.45 | 7.54 | 40.09 | 31.77 | 0.806 | 0.617 |

**Inverses.** m is the analyst's number; σ is a decimal; T is in years.

| Quantity | σ = |
|---|---|
| Variance | √m |
| Daily SD % | 15.8745 × m / 100 |
| 1-SD daily $ | 15.8745 × m / S |
| Mean abs daily $ | 19.8957 × m / S |
| 1-SD $ to expiry | (m/S) / √T |
| ATM straddle | (2/√T) · N⁻¹(0.5 + m/(4S)), or put m/2 into the IV tab as an ATM call |
| % change in IV | σ_prev × (1 + p) |
| Change in points | σ_prev + m |
| Ratio | σ_prev × m |
| Realised vol of the event move | \|ln(S1/S0)\| / √(event length / 252) |

Notes:
- **Realised vol has a different scale from IV.** A 7% move inside one R3 event gives 230% if down and 215% if up.
- `r3_invert.py` handles all of these.
- **Invert with both T conventions:** a straddle of 21.26 means 99.99% at 18 days but 94.86% at 20 days.

---

## 4. Game plan, phase by phase

### 4.1 Before the bell (check-in to 09:30)
1. **Read the Slack answers and set the switches in §2.4.** If the fill rule is unanswered, plan the Event 1 tests in §4.3.
2. **Prepare every laptop:**
   - Open the UI, with one trader per underlying (OIL / PLANE toggle). Multiple logins per team is a [Q].
   - Run `deriv_live.py` once to check it works.
   - Print an event card (Appendix A) for each event.
3. **At 09:30, before reading the headline:**
   - The Quant reads S0 and σ0 for both underlyings from the stale chain (parity spot plus ATM IV).
   - Write the **L1 bid and ask of all 12 contracts** on the E1 card. Those are exactly the prices you will hit in Window A; L2 and L3 are L1 ± 1.00 and ± 2.00.
   - In later events, do the same during the previous event's post-snap window.

### 4.2 The per-event loop (every event)

| Real time | Who | Action | Trigger / size |
|---|---|---|---|
| T−30s to 0 (previous post-snap) | Traders | **Open orders = 0** (check the "N open" counter). Stage the ticket on the most likely first contract (60C or 140P), with Limit selected and the quantity filled in. | Never leave a resting order across T+0 or T+60. |
| T+0 to 10s | Caller | Read the headline aloud. Read the announced S1 for both underlyings, plus σ1 (R1), the analyst numbers (R2) or the quantities (R3). Name the playbook row (§4.7). | Call format: "OIL UP 8, VOL UP. PLANE DOWN 5, VOL UP." |
| T+5 to 15s | Quant | `deriv_live.py event --S0 .. --v0 .. --S1 .. --v1 ..` (R1), or with `--cands a1,a2,a3` (R2/R3). Shout the top 3 lines per underlying. | Traders can start on the 60C/140P from the card without waiting: the delta edge ≈ 0.975·dS on the 60C. |
| T+10 to 55s | Traders | **Window A.** Work down the click list. Order 1: 200 @ the L1 price. Order 2 (if the edge clears L2/L3): 400 @ the L3 price. Once fill rule I is confirmed, one order at the deepest profitable level is fine. | Take level k if net edge > buffer. **R1 buffer:** 0.10 for 60C and 140P; 0.30 for ATM and 140C until T is confirmed. **R2/R3 buffer:** see §4.4. Cap every line at the position limit. |
| T+55s | Caller | "CANCEL DIRECTIONAL": pull any unfilled Window A orders. | |
| T+60s | Quant | Check that S has moved to the announced S1, and whether the option quotes moved to BS(S1, σ0) (compare the 60C change with Δ·dS). | If the quotes are still stale, Window A continues. |
| T+60 to 115s | Traders | **Window B:** both legs of the straddle at the strike nearest S1. | Only if \|dσ̂\| ≥ 9 points at ATM, plus the vol buffer. Level 2 only if \|dσ̂\| ≥ 19. |
| T+60 to 115s | Traders | Unwind a Window A position **only if** (a) the quotes are still stale and our call was wrong (selling back at the stale price recovers the loss), or (b) its vega has the wrong sign and \|vega·dσ̂\| > 1.005 (more than 9 points at ATM). | Otherwise exiting just pays another spread, because the loss is already in the mark. |
| T+120s | Quant | **SNAP READ:** true IV per underlying (§3.8). Log analyst errors (R2) and decode (R3). | |
| T+120 to 145s | Traders | Hit any **non-bot** resting orders that are now mispriced (off-grid prices or odd sizes are people). Apply the carry rule (§6.2): trim excess \|Δ\| starting with the high-delta lines. Optional: post quotes at fair ± 0.5 against teams that need to close. | Never quote before the snap in R2/R3. |
| T+145s | Caller | **"CANCEL ALL."** Write the next event's L1 prices on its card. | |

### 4.3 Round 1 (IV announced): maximum aggression on edge that is close to certain, plus calibration
- **Edge is near-certain** if S1 and σ1 are both public at T+0. Take **every level with positive net edge on all 12 contracts**, in click order, up to the limits.
- Expected size, if we win the fills: about $5,000 per underlying at level 1 on a ±10% move, and about $12,000 across all three levels (§3.6).
- **Event 1 tests.** Run each once and record it on the card:
  1. **Fill rule:** in E1's post-snap window, buy **1 lot at ask + 1.00**. A fill at the ask means reading I; at the mid, reading II; at ask + 0.50, reading III. It costs at most about $1.50. If there is pre-open trading, do it then.
  2. **PnL marking:** right after that fill, see whether TOTAL PNL shows about −1.005 (marked at mid/fair), −2.01 (marked at bid) or 0.
  3. **Window B behaviour:** at T+60, did the option quotes reprice to the new S?
  4. **Liquidity:** how many seconds until level 1 of the 60C disappears, and is it refilled?
  5. **T:** read T on the Fair tab at the news and again at the snap. Is it decaying?
  6. **Snap:** does the snapped IV equal the announced IV, and is it flat across strikes?
- **Log for later rounds:**
  - κ = dσ / |dS%| per underlying, with its sign.
  - β = dPLANE% / dOIL% on oil events.
  - Which headline types raised or lowered IV.
  - These give priors for R2/R3. Use them only as **tie-breakers** (6 data points; the designers may set dS and dσ independently).

### 4.4 Round 2 (three analysts)
**Split every trade into two parts:**
- **Vol-free delta part:** the `volfree` column of the tool, BS(S1, σ0) − stale mid. Take it at **full size**. The 60C and 140P survive IV errors of 40 to 1,000+ points; ATM delta trades do not for moves under about 5%.

  | dS | 60C | 100C | 140P | Synthetic forward |
  |---|---|---|---|---|
  | ±10% | 322–1,147 pts | 40–49 pts | 78–239 pts | vol-free (net 7.99) |
  | ±5% | 191–361 pts | 15–18 pts | 42–74 pts | vol-free (net 2.99) |
  | ±2% | 56–73 pts | ~1 pt | 11–13 pts | below cost |

  Each cell is the adverse IV error that wipes out the net edge.
- **Vol part:** sized by the analyst model, as below.

**The analyst model depends on the data. Test it after every snap.**
1. **After each snap**, log e_i = estimate_i − σ\* per analyst, per underlying, with the headline category.
2. **Exact-hit test:** does any analyst land within 0.3 points of σ\* (the readout precision at ATM)?
   - **Exact hits seen (selection / mixture regime).** The packet's "see which analyst(s) were correct" points this way. Each event, one or more analysts are right, so the job is to guess which one.
     - Set p_i = (hits_i + 1) / (events + 3), separately per underlying. Split by event category if a pattern appears, e.g. one analyst always right on oil-supply news.
     - Run `deriv_live.py event ... --cands a1,a2,a3 --probs p1,p2,p3`.
     - Take level k only if **EV > 0.25 per lot**.
     - At E4–E5, also require at least two of the three candidates to give positive net edge.
   - **No exact hits (noisy-estimator regime).** Use `analyst.py`:
     - It applies bias shrinkage and inverse-variance weights, inflates the uncertainty τ when the analysts disagree, and drops one outlier when two analysts agree.
     - Take level k if **|E| − (1.005 + k − 1) ≥ 0.5 × vega × τ**.
     - Pass `--vega` at the actual T and vol.
3. **E4 has no history.** Trade the vol part only if net edge is positive under **every** analyst's number (`--robust`), i.e. all three sit on the same side of the stale IV by more than the break-even. That is safe under both models.
4. **Tie-breakers, in order:** two analysts agreeing within 1 point; the sign from the headline playbook; the κ·|dS| prior from R1; the Tape (other teams aggressively buying ATM in Window B).

**Worked example: E6 OIL.**
- Inputs: stale IV 80%; analysts 92 / 99 / 70; T = 18.33 days.
- History (E4, E5): A1 +1.5, −2.0; A2 +6, +4; A3 −14, +9.

Steps:
1. **No exact hits**, so this is the noisy-estimator regime.
2. `analyst.py` drops A3 (the two other debiased estimates are 4.4 apart; A3 is 20.9 away) and returns **94.17%, τ = 3.32**.
3. Edge per leg = BS(94.17) − BS(80) = **+1.514**, which nets **+0.509** at level 1.
4. That clears 0.5 × 0.107 × 3.32 = 0.18, so **buy 200 straddles at level 1**. Split them about 90C / 110P to be delta-neutral at the new vol. Level 2 nets −0.49, so skip it.
5. **Downside if A3 is right:** −2.076 per leg, about −$830 on 400 legs. That is acceptable, because A3's history makes 70 a roughly 2σ outlier.

**The same numbers under the mixture regime:** if A3 had been exactly right once in two events, then p(A3) ≈ 0.4. The trade is +EV only if p(A3) < 0.239, so **skip it**. With equal weights the EV is −0.258 per leg, also a skip.

The regime decides, which settles the disagreement between the two reports.

### 4.5 Round 3 (the "quantity linked to IV")
1. **Before E7:** record σ0 for both underlyings from the 09:30 stale chain, which still carries the R2 closing snap.
2. **At E7, T+0:** read the 6 numbers.
   - Compare their size with the fingerprints in §3.10, using σ0 ± 40 as the plausible range for σ1.
   - Watch for degenerate pairs: the straddle vs the 1-SD move to expiry (ratio 0.80); "change in points" vs "% change" when σ0 ≈ 100.
   - If the quantity looks like an option price, invert it through the IV tab.
3. **E7 sizing:**
   - Delta part at full size.
   - Vol part only if its net edge is positive under every candidate formula and every analyst, at both T conventions.
   - Otherwise level 1 at z = 1.
4. **At the E7 snap:** pick the formula and T convention that give analyst errors closest to their R2 sizes. Use `r3_invert.py` and `live_tools.py decode`. Confirm at E8.
5. **E8–E10:** run exactly as R2, carrying the R2 scorecards (reliability may persist).

### 4.6 Closing each round (E3, E6, E10; 30 real seconds after the last snap)
- **Until the round-end mark is confirmed as fair or mid:**
  - In the last event, prefer Window A lines that also shrink existing net-long time value and large |Δ|.
  - After the snap, close the **net-long ATM lines first** (largest time value), then the largest |Δ| lines.
- **You cannot close 12 lines in 30 seconds.** Plan the last event so that the ending position is already small.
- **If Slack confirms marks at fair and that positions carry,** skip all of this and keep the inventory.

### 4.7 Fallback when S1 is not public in Window A, plus priors for IV direction in R2/R3
**When S1 is unknown:**
- **Do not buy gamma blindly.** A delta-neutral straddle bought at stale prices with vol flat earns 1.19 / 1.31 (up / down) on a 10% move against a crossing cost of 1.81. It breaks even only at |dS| ≈ 12%. Our Monte Carlo gives it **−$154 per event**.
- **Trade direction from the headline only on high-confidence rows** (★ below): 60C or 140P, at half size.
- Rows marked AMB: no trade until T+60. Then read dS off the chart and trade Window B vol only.
- Worst case: a wrong-direction 60C position on a 10% move loses about 10.6 per contract (9.6 of move plus 1.0 of spread). At half the position limit L, that is about 5.3 × L. Cap the loss from forecast-based trades at roughly one good event's profit.

Headline priors. These are priors only; overwrite them with R1 data. IV changes are in vol points, starting near 100.

| Headline | OIL px | PLANE px | OIL IV | PLANE IV | Note |
|---|---|---|---|---|---|
| ★ War, invasion, strikes on oil infrastructure | +15 to +40% | −15 to −35% | +25 to +60 | +25 to +60 | Biggest event; direction is clear |
| ★ Euleria embargoes oil to Newtonia | +15 to +40% | −20 to −40% | +25 to +50 | +25 to +50 | AMB for OIL if OIL is a world price |
| ★ Second crash / serious incident | −1 to −3% | −20 to −40% | ~0 | +25 to +50 | PLANE only |
| ★ Fleet grounded | −1 to −4% | −15 to −30% | 0 to +5 | +15 to +35 | NewAir is a monopoly |
| ★ Audit finds systemic defects or a cover-up | 0 to −2% | −15 to −30% | ~0 | +15 to +30 | |
| ★ Monopoly ends (competitor allowed) | 0 | −15 to −30% | 0 | +10 to +25 | |
| Crash ruled not NewAir's fault | 0 to +1% | +8 to +20% | ~0 | −10 to −25 | Surprise against the market's belief |
| Crash confirmed as NewAir's fault | 0 to −2% | −5 to −15% | ~0 | AMB −5 to +10 | Partly priced in |
| Sabotage confirmed | +3 to +10% | AMB | +5 to +20 | +5 to +15 | |
| Grounding lifted / audit passed | 0 to +2% | +10 to +25% | ~0 | −15 to −30 | |
| Tariff raised / Newtonia retaliates | AMB (+3 to +10% if OIL is the Newtonian price, our ~65% prior) | −5 to −12% | +5 to +20 | +5 to +20 | Ask what "Newtonian Crude Oil" means |
| Tariff cut / trade deal | −3 to −12% | +5 to +15% | −5 to −30 | −5 to −30 | Largest de-escalation IV crush |
| Talks collapse / border skirmish | +4 to +12% | −4 to −12% | +10 to +25 | +10 to +20 | Escalation |
| Ceasefire / embargo or sanctions lifted | −8 to −25% | +8 to +30% | −15 to −40 | −15 to −40 | Resolution |
| Protesters attack oil factories / strike | +3 to +20% | −2 to −10% | +5 to +30 | 0 to +10 | Supply shock |
| Crackdown / factories secured / strike ends | −3 to −8% | +1 to +4% | −5 to −15 | 0 to −5 | |
| New oil field found / Newtonia gets another supplier | −5 to −20% | +3 to +15% | AMB | −5 to −15 | |
| Oiler makes a vague threat | +2 to +6% | −2 to −5% | **+10 to +25** | +5 to +15 | Vol moves more than price, so trade it in Window B |
| Oiler makes a conciliatory offer | −2 to −5% | +2 to +5% | −5 to −10 | −5 to −10 | Partial crush only |
| Rumor; then rumor denied | 30–60% of the confirmed move; the denial reverses 70–100% of it | same | up; then −5 to −15 | up; then down | Look the rumor up in the event log |
| Recession / pandemic / travel ban | −3 to −15% | −5 to −30% | +5 to +40 | +5 to +40 | **Same-direction** move (demand shock) |
| NewAir fuel hedge / bailout | ~0 | +3 to +15% | 0 | −5 to −20 | After a fuel hedge, β shrinks |

Reasoning rules for headlines not in the table:
- A supply shock moves OIL up and PLANE down.
- A demand shock moves both the same way.
- A trust shock moves PLANE alone.
- Escalation and new unknowns raise IV; resolution lowers it, even on bad news.
- Ask what is already priced in: the market already blames NewAir for the crash.

---

## 5. Edges ranked by expected value

Illustrative Monte Carlo (`deriv_ev.py`): S0 = 100, σ0 = 100%, T = 19 days; dS% drawn as 50% N(0, 4%) and 50% N(0, 12%); dσ ~ N(0, 15). It **assumes we get the fills**. Values are $ per event **per underlying**.

| # | Edge | Mean $ (L1 only) | Mean $ (all 3 levels) | Median (L1) | How to execute |
|---|---|---|---|---|---|
| 1 | **Window A delta edge** (vol-robust: lines whose edge survives ±15 vol points) | **2,824** | **6,919** | 1,214 | Deep ITM first (60C / 140P, ≈ $1,960 alone at L1), then ATM in the same direction, then synthetic forwards if limits bind. Price at the exact bot level. Pre-written L1 prices. |
| 2 | Window A vol edge (the rest of the basket; full size in R1 only) | ≈ 285 | ≈ 630 | – | Decides the ATM legs and the click order (§3.6). R2/R3: sized by §4.4. |
| 3 | Window B vol edge | 199 | 274 | 18 | Straddle at the strike nearest S1, only if \|dσ\| ≥ 9 (ATM). |
| 4 | R2/R3 vol edge with a 4-point estimate error | 184 | – | 0 | z = 0.5 rule, or mixture EV (§4.4). |
| 5 | Carry: not paying to close | +1.005 per lot carried | – | – | Hold delta-light inventory into the next event (§6.2). |
| 6 | Picking off other teams | ? | – | – | Stale resting orders after T+0 / T+60; parity and box violations (C − P ≠ S − K; box ≠ K2 − K1). |
| 7 | Post-snap market making (fair ± 0.5) | small (≤ 0.5 per lot) | – | – | Only after the snap; cancel at T+145. |
| C | **Contingent:** round-end mark at intrinsic | ATM time value ≈ 10.9 per contract | – | – | Only if officially confirmed: sell ATM time value and close every long before 16:00. |
| ✗ | Blind gamma when S1 is unknown | **−154** | – | −191 | Don't. |
| ✗ | Directional guesses on AMB headlines | negative given the 2.01 spread | – | – | Wait for T+60. |

What the table shows:
- **About 91% of the Window A value is vol-robust.** That is why R2 and R3 lose little if the delta part is always taken at full size.
- In a competitive field our realised share will be lower: **speed, pre-staging and splitting contracts between traders decide how much of row 1 we collect.**

---

## 6. Risk management

### 6.1 Scoring convexity [P + I]

| Rank | 1 | 2 | 3 | 4 | 5 | 6 | 8 | 10 | 12 | 15 | 20 | 25 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| rank^1.5 | 1.0 | 2.8 | 5.2 | 8.0 | 11.2 | 14.7 | 22.6 | 31.6 | 41.6 | 58.1 | 89.4 | 125.0 |

| Rank path | Score |
|---|---|
| 3, 3, 3 | 15.6 |
| 2, 2, 8 | 28.3 |
| **5, 5, 5** | **33.5** |
| 6, 6, 6 | 44.1 |
| **1, 1, 15** | **60.1** |
| 1, 1, 20 | 91.4 |
| 1, 1, 25 | 127.0 (≈ 12, 12, 12) |

- **Pure variance at the same mean always hurts.** E[r^1.5] ≈ μ^1.5 + 0.375·Var/√μ.
- **Variance that comes with higher mean is fine.** Taking more positive-edge size is worth it.
- **R1 is the round where a flat book is worst,** because the edge is near-certain and competent teams will be positive.
- Posture by round:
  - **R1:** maximum breadth and size.
  - **R2/R3:** delta at full size, vol scaled by confidence.
  - **Gamble only if the case prize is already out of reach.**

### 6.2 Inventory carry (corrected)
- **Expected value.** If marks are at fair, holding inventory into the next event instead of closing it is worth **+1.005 per lot** (the spread saved) when stale liquidity or limits bind. It is worth more when they do not, because you can exit at stale prices if the news hurts. **Holding has positive EV; the problem is its variance.**
- **Variance per event** (dS ~ N(0, 5%), dσ ~ N(0, 15)):

  | Inventory | SD of PnL per event | Spread saved by holding | SD / saved | Rule |
  |---|---|---|---|---|
  | +200 60C | $975 | $201 | 4.9 | Trim |
  | −200 140P | $881 | $201 | 4.4 | Trim |
  | +200 synthetic forward | $1,001 | $402 | 2.5 | Trim if large |
  | +200 straddle | $669 | $402 | 1.7 | Hold |
  | +89C / +111P (delta-neutral) | $326 | $201 | 1.6 | Hold |

- **Rule:**
  - After each snap, if |net Δ| on an underlying exceeds about **±400**, trim the excess using the deepest-ITM line (the cheapest delta per spread). One SD at ±400 Δ is about $2,000 on a 5% move.
  - Hold delta-light and vega inventory.
  - Net the next event's trades against the inventory: dumping long 60C on bearish news is itself a stale-quote sale.
  - At round end, follow §4.6.
  - Under fill rule II, closing is free, so close everything after each snap.

### 6.3 Execution safety
- **Order type and price:**
  - Never use market orders, since the depth beyond 3 levels and the refill behaviour are unknown.
  - Every order is a limit at an exact bot level price.
  - **Read the big confirm button** ("Buy 200 OIL_0626_C60") before every click. BUY/SELL is a toggle; the wrong side doubles the loss.
- **Size:**
  - Each order must stay within the maximum order size and position limit, because an order over the limit may be rejected and waste the window [Q].
  - The ticket presets top out at 50, so type 200.
- **Resting orders:**
  - Open orders must be 0 before T+0 and T+60.
  - CANCEL ALL at T+145 and on any panic ("FLATTEN" closes everything; "NO TRADE" means wait for T+60).
- **Calls in the room:** traders do not debate inside a window. The Caller decides, and disagreements go into the post-snap slot.
- **Stale or wrong inputs:** if `deriv_live.py` and the UI's Fair tab disagree by more than 0.05 on a contract, trust the UI. The likely cause is a different T or spot.

### 6.4 Claims from the source reports that this playbook drops
- Limit orders at fair ∓ 0.25. Under fill rule III they lose 48% of the capture ($2,422 vs $4,694 on the +10% 60C example).
- "Holding is a free option" and "carry EV = 0". The truth is +c per lot, with variance.
- "Exit a wrong call at T+60": exiting only helps if the quotes are still stale or the vega is wrong by more than 9 points.
- "The second contract is the ATM in the same direction" (wrong in 4 of 6 cases), and the linear 140-strike vol break-even of 15.6 / 31.2 (the exact figures are +14.3 / −18.1).
- "IV readout ±0.05 points": that is only true if the IV tab uses the bots' reference spot.
- 50-lot sizing, which leaves 75% of level 1 unused, and single 600-lot orders, which may exceed limits.
- "Dollars on the table" as a basis for posture, since it assumes one team captures everything.
- κ as a "fourth analyst": it is a tie-breaker only.

---

## 7. Team roles and preparation

### 7.1 Roles

| Role | 4 people | 3 people |
|---|---|---|
| **Caller / Clock** | Reads the headline and the numbers, names the playbook row, calls direction / vol / size, keeps the clock (T+55, T+115, T+145), calls CANCEL ALL. | Merged with Quant; does not trade. |
| **OIL trader** | Own laptop on OIL. Clicks in order, reports fills ("bought 200 60C at 41.29"). | Same |
| **PLANE trader** | Same, on PLANE. | Same |
| **Quant / Logger** | Writes L1 prices on the next card; runs `deriv_live.py` at T+5; does the SNAP READ; keeps the analyst scorecard (R2) and the decode (R3); tracks net Δ, vega and limits. | Merged with Caller |

- **Call format:** "[UNDERLYING] [UP/DOWN] [size]; VOL [UP/DOWN/FLAT]".
- **Hand signals:**
  - Thumb up or down: direction.
  - 1–3 fingers: size.
  - Flat palm: no trade.
  - Hands apart: vol up. Hands together: vol down.
  - Fist: cancel all.

### 7.2 Preparation checklist
- [ ] **Post the #questions list (§8) early** and re-check Slack at check-in.
- [ ] On every laptop:
  - Test `python3 -I deriv_live.py event ...`, `iv ...` and `sheet`, plus `analyst.py` and `r3_invert.py`.
  - Save the exact command lines as shell aliases or a text file, ready to paste.
- [ ] **Print:**
  - §2.3 clock card.
  - §3.3 price sheet.
  - §3.5 break-evens.
  - §3.6 decision card and click lists.
  - §3.10 R3 fingerprints.
  - §4.7 headline priors.
  - **10 event cards** (Appendix A).
- [ ] **UI drills**, on a practice server if one exists:
  - Does clicking a chain cell load the contract, the side and the price?
  - Does a quantity preset set the quantity or add to it?
  - Where is cancel-all?
  - How fast can one person submit 6 limit orders?
- [ ] **Mock events:** one person reads "OIL +8%, IV 100 → 115; PLANE −6%, IV 90 → 110". Traders should send the first correct order within **10 seconds** and finish the Window A list within 45. Repeat until reliable.
- [ ] **Agree the switches in §2.4** and who flips them after the E1 tests.

---

## 8. Questions for #questions (ordered by impact)

1. **Round-end PnL:** are options marked to Black-Scholes at the final S and IV, to the bot mid, or settled at intrinsic on a revealed underlying value? Does "underlying value revealed" mean the live tick or the bots' reference price? Do positions and cash carry across rounds, or does each round start flat?
2. **Matching rule:** when a limit buy priced above the best ask crosses, does it fill at the resting price, the book mid, or the midpoint of the two prices? Is it the same against bots and against teams? Are there fees? How is TOTAL PNL marked live?
3. **New prices:** are the new underlying prices published at T+0 with the headline, or at T+60 when the bots move, and in all three rounds? Are they exactly where the bots move the underlying?
4. **Limits:** position limits per contract or per underlying, net or gross? Maximum order size? Is an order over the limit rejected or partly filled? Any rate limits? Are naked short options allowed?
5. **Option bots:** between T+60 and T+120, do they quote BS(new S, old IV) or stay fully stale? Do they ever reprice to the live tick? Do the Fair and IV tabs' "LIVE" spot use the live tick?
6. **Bot liquidity:** is the 3 × 200 ladder shared by all teams? Is it refilled after being hit, and how fast? Is there depth beyond 3 levels?
7. **Time to expiry:** "time to expiry ... fixed": does T fall within a round, between rounds, or never?
8. **PLANE:** starting price, strikes, starting IV. Is its spread also 2.01?
9. **Analysts:** are their numbers published at T+0, for both underlyings, at every event? Is any analyst ever exactly correct, and can more than one be?
10. **R3:** is the quantity the same for both underlyings and all analysts, and per event? Will its formula be published?
11. **Logistics:** can several team members log in at once? Team size and number of teams? Is there a practice round or pre-open trading?
12. **Tools:** are personal non-trading pricing scripts or spreadsheets allowed, given "bots are not allowed"?
13. **R1 IV:** is the announced R1 IV a single flat IV across strikes, and equal to the snap IV?
14. **Ranking:** how are ties ranked? Where does a zero-PnL team rank?
15. **OIL definition** (low priority): is OIL ("Newtonian Crude Oil") the price paid in Newtonia, including the tariff?

---

## Appendix A: Event card (print 10; one per event)

**EVENT ___  ROUND ___**  T on the Fair tab: ______ d  Fill rule: I / II / III  Mark: fair / intrinsic / ?

Clock (exchange time): News ______ | T+55 cancel directional ______ | Underlying moves ______ | T+115 last vol ______ | Snap ______ | T+145 CANCEL ALL ______

Headline (verbatim): ____________________________________________________________

Category: supply / trust / trade / demand / Oiler / other  Playbook row: ________
Prior: OIL ↑ ↓ ___% vol ↑ ↓ ___  PLANE ↑ ↓ ___% vol ↑ ↓ ___  (write before T+60)

| | **OIL** | **PLANE** |
|---|---|---|
| **PRE-NEWS** (fill in during the previous post-snap) | | |
| S0 (parity: K + C − P) | | |
| σ0 (ATM call / put / used) | / / | / / |
| L1 bid / ask: low-K C · mid-K C · high-K C | | |
| L1 bid / ask: low-K P · mid-K P · high-K P | | |
| Inventory: net Δ / net vega | / | / |
| Open orders = 0 ☐ | ☐ | ☐ |
| **AT T+0** | | |
| Announced S1 → dS % | | |
| R1 σ1 · R2 A1 / A2 / A3 · R3 q1 / q2 / q3 | | |
| R3 formula → IVs | | |
| σ̂ used · τ or p's · regime (exact-hit / noisy) | | |
| **WINDOW A** (contract, side, price, qty filled) | 1. | 1. |
| | 2. | 2. |
| | 3. | 3. |
| | 4. | 4. |
| **T+60:** S = announced? · options repriced to new S? | Y / N · Y / N | Y / N · Y / N |
| **WINDOW B:** strike, side, edge per leg, qty | | |
| **SNAP:** σ\* (call / put / parity-corrected) | / / | / / |
| Analyst errors e1 / e2 / e3 (est − σ\*) · exact hit? | / / · | / / · |
| **POST-SNAP:** inventory Δ / vega · action (hold / trim / flatten) | | |
| Stale orders from other teams hit? | | |
| TOTAL PNL at T+145 · event PnL | | |
| **LEARNED:** L1 gone after ___ s · refill Y/N · κ = dσ / \|dS%\| · β = dPLANE% / dOIL% | | |

Notes / lessons: ______________________________________________________________

---

## Appendix B: Tools

All tools are pricing aids only and send no orders. Run each with `python3 -I`.

In `work/final/`:
- `deriv_live.py`
  - `event`: the click list with orders at the exact bot-level prices; EV and worst case per level; `--cands/--probs` for analysts; `--window B`; `--K` for PLANE; `--quotes` for the actual book.
  - `iv`: parity-spot IV readout.
  - `sheet`: price tables for any T.
- `deriv_verify.py`: re-checks every disputed number.
- `deriv_ev.py`: the edge EV ranking (§5).
- `deriv_clicks.py`: the click-order table (§3.6).

In `.../work/deriv/quant/`:
- `analyst.py`: robust IVW combiner and sizing (pass `--vega` at the actual T and vol).
- `r3_invert.py`: R3 inversions and the reference table (pass `--days` for both T conventions).
- `bs.py`: Black-Scholes library.

In `.../work/deriv/playbook/`:
- `live_tools.py decode`: ranks 16 R3 transforms against the snapped IV.
- `event_log_template.csv`: the event log.
