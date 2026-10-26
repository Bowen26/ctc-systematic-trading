# Derivatives cheat sheet (OIL / PLANE options)

**The game:** after each headline, the bots keep quoting the old prices for a while. Window A, **T+0 to 60 s**: old
underlying and old vol. Window B, **T+60 to 120 s**: new underlying, old vol. At **T+120 s** they snap to fair.
Whatever sits between a stale quote and the new fair value is free money, minus about **$1.005** of half-spread.
**About 90% of it is the price (delta) edge in the first 60 s.** Be fast, be right, and take every level that clears.

## One event (2.5 real minutes)

| Real time | Who | Do |
|---|---|---|
| Before T+0 | Traders | **Open orders = 0.** Ticket staged on the likely first contract (60C or 140P), Limit selected, quantity 300 typed. Next event's level-1 prices written on the card |
| T+0 to 10 s | Caller | Read the headline aloud plus the new S (and IV, analyst numbers or quantities). Format: "OIL UP 8, VOL UP. PLANE DOWN 5, VOL UP." |
| T+5 to 15 s | Quant | Run `deriv_live.py event ...` and shout the top 3 lines per underlying |
| **T+10 to 55 s** | Traders | **Window A:** work the click list. **One limit order per line, up to 300**, priced at the deepest level that clears; it fills each level at that level's resting price |
| T+55 s | Caller | "CANCEL DIRECTIONAL": pull unfilled Window A orders |
| T+60 s | Quant | Did S move to the announced value? Did option quotes reprice to the new S? If not, Window A continues |
| **T+60 to 115 s** | Traders | **Window B (vol only):** straddle at the strike nearest the new S, only if \|dσ\| ≥ 9 vol points at ATM |
| T+120 s | Quant | **Snap read:** true IV per underlying (R2: grade the analysts; R3: decode the quantity) |
| T+120 to 145 s | Traders | Hit stale *team* orders (odd prices or sizes). Trim net \|Δ\| above ±400 using the deepest-ITM line |
| **T+145 s** | Caller | **"CANCEL ALL."** Write the next event's level-1 prices |

**Exchange clock (UI header).**
- Rounds 1-2: news at 09:30, 11:40, 13:50, snap 1 h 44 min later.
- Round 3: news at 09:30, 11:07:30, 12:45, 14:22:30, snap 1 h 18 min later.
- Event 1 of each round fires at the 09:30 open.

## Quotes and numbers to know

- **Bot ladder:** bid = floor(fair − 1.00), ask = ceil(fair + 1.00), 3 levels × 200 lots, $1.00 apart. No bid when fair < 1.00, so a deep-OTM long can't be sold back.
- **Confirmed on Slack:**
  - Fills happen at the resting order's price.
  - Each option has a **position limit of 300**, and the limit also caps order size.
  - So a line is at most level 1 (200) plus 100 of level 2: one order of 300 at the level-2 price.
  - Room to buy = 300 − position; room to sell = 300 + position.
- **Net edge at level k** = \|edge\| − (1.005 + (k − 1)). Take it if positive, plus a buffer of 0.10 (60C/140P) or 0.30 (ATM and 140C).
- **Multiplier 1:** $1 of PnL per 1.00 of price per contract.
- **OIL greeks**, S = 100, IV = 100%, T = 20 trading days. Puts: Δ = call Δ − 1, same vega.

  | | 60C | 100C | 140C |
  |---|---|---|---|
  | Price | 40.30 | 11.20 | 1.88 |
  | Δ | 0.975 | 0.556 | 0.146 |
  | Vega per vol point | 0.017 | 0.111 | 0.065 |

  ATM vega is about 0.11 at any vol, so **10 vol points ≈ $1.1 ≈ one half-spread.**

- **Edge in Window A**, with dS in $ and dσ in vol points:

  | Contract | Edge |
  |---|---|
  | 60C | 0.975·dS + 0.017·dσ |
  | 100C | 0.556·dS + 0.007·dS² + 0.111·dσ |
  | 140C | 0.146·dS + 0.004·dS² + 0.065·dσ |
  | Any put | call edge − dS (same strike) |

  A synthetic forward (long C − short P) earns exactly dS whatever the vol does, but crosses two spreads.
- **Move needed to clear one half-spread:**

  | Contract | \|dS\| | dσ |
  |---|---|---|
  | 60C | $1.03 | – |
  | 140P | $1.18 up / $1.17 down | – |
  | 100C | $1.77 / $1.85 | ±9 |
  | 100P | $2.35 / $2.19 | ±9 |
  | 140C | $5.9 up / $8.9 down | +14 / −18 |

- **PLANE:** strikes and spot unknown until the screen opens. If strikes are 0.6/1.0/1.4 × spot, $ edges and vegas scale with S/100; deltas don't. The fixed $2.01 spread is relatively wider on a cheap underlying.

## Window A decision card

Largest edge first; ±5% move, ±20 vol points; B = buy, S = sell.

| | **Vol DOWN** | **Vol FLAT** | **Vol UP** |
|---|---|---|---|
| **S UP** | S 140P, B 60C, S 100P | B 60C, S 140P, B 100C, S 100P | B 60C, B 100C, B 140C, S 140P |
| **S FLAT** | Sell the 100 straddle (2.23/leg at −20) | Nothing | Buy the 100 straddle + the 140s (+20) |
| **S DOWN** | S 60C, S 100C, B 140P, S 140C | S 60C, B 140P, S 100C, B 100P | B 140P, B 100P, S 60C |

- **Deep ITM first** (60C Δ ≈ +0.98, 140P Δ ≈ −0.85). ATM goes first only if \|dσ\| > about 5 × \|dS%\| (calls) or 9 × \|dS%\| (puts).
- **Size with the 300 limit:** if we win the fills, a full ±10% basket is worth about $7k per underlying (it was $12k over three levels) and a ±5% one about $3k.

## Round by round

**Round 1 (new IV announced): maximum aggression.** The edge is near-certain. Take every positive level on all 12
contracts, up to 300 each. **Event 1 tests:**
1. After the first fill, does PnL mark at mid or at bid?
2. At T+60, did the options reprice to the new S?
3. How fast is level 1 refilled?
4. Does T on the Fair tab decay?
5. Does the snapped IV equal the announced IV?

Log per event: κ = dσ / \|dS%\| and β = dPLANE% / dOIL%. They're the priors for rounds 2-3.

**Round 2 (3 analysts).** Always take the **vol-free price part at full size**: the 60C and 140P survive IV errors of
40 to 1,000+ vol points. Size the vol part by the analysts:
- **After each snap:** log each analyst's error. If one lands within 0.3 points of the true IV, the round is in *pick-one* mode: weight analyst i by p_i = (hits_i + 1)/(events + 3) and trade only if EV > 0.25 per lot (`--cands a1,a2,a3 --probs p1,p2,p3`).
- **No exact hits:** use `analyst.py` (bias-corrected, inverse-variance weighted, drops one outlier). Take level k only if net edge ≥ 0.5 × vega × τ.
- **Event 4 (no history yet):** trade vol only if all three analysts sit on the same side of the stale IV, beyond break-even.

**Round 3 ("a quantity linked to IV").** At Event 7, match the six numbers to a fingerprint (S = 100, T = 18 days):

| IV | Variance | Daily SD % | Mean abs daily $ | 1-SD $ to expiry | ATM straddle |
|---|---|---|---|---|---|
| 60% | 0.36 | 3.78 | 3.02 | 16.04 | 12.78 |
| 100% | 1.00 | 6.30 | 5.03 | 26.73 | 21.26 |
| 150% | 2.25 | 9.45 | 7.54 | 40.09 | 31.77 |

Converting back to σ (m is the analyst's number, σ and T as decimals):

| Quantity | σ = |
|---|---|
| Variance | √m |
| Daily SD % | 15.87 × m / 100 |
| Daily SD $ | 15.87 × m / S |
| Mean abs daily $ | 19.90 × m / S |
| 1-SD $ to expiry | (m / S) / √T |
| ATM straddle | `r3_invert.py`, or put m/2 into the IV tab as a call |
| % change | σ_prev × (1 + p) |
| Points change | σ_prev + m |

Confirm the formula at the Event 7 snap, then run rounds 8-10 like round 2.

## Risk and rules

- **Score = Σ (round rank)^1.5.** 3, 3, 3 scores 15.6, which beats 1, 1, 15 at 60.1. So be consistent: max size in R1; in R2-R3, full size on the price part and size the vol part by confidence. Never gamble to catch up unless the prize is already lost.
- **Limit orders only,** at an exact bot level. Never market orders. **Read the big confirm button** (BUY/SELL is a toggle). Type 300 (200 for level 1 only); the presets stop at 50. Cancel any unfilled remainder at T+55.
- **Open orders must be 0** before T+0 and T+60. CANCEL ALL at T+145 and on any panic.
- **Carry:** hold delta-light and vega inventory into the next event. Trim \|net Δ\| above ±400 after each snap. A line at ±300 has no room left in that direction, so **trim the lines at the limit first**: that frees room as well as Δ.
- **Round end:** until Slack confirms how positions are marked, **don't hold net-long time value or a large \|Δ\| into 16:00.** You cannot close 12 lines in 30 s, so plan the last event's trades to finish small.
- **Ambiguous headline:** no directional trade. Wait for T+60, read dS off the chart, trade Window B vol only.

## Headline priors

These are priors only; round 1 data overrides them. Rules:
- **Supply shock:** OIL up, PLANE down.
- **Demand shock:** both move the same way.
- **Trust shock:** PLANE only.
- **Escalation or new unknowns:** IV up. **Resolution:** IV down, even on bad news.

| Headline | OIL | PLANE | IV |
|---|---|---|---|
| War / strikes on oil infrastructure | +15 to +40% | −15 to −35% | both +25 to +60 |
| Second crash / fleet grounded | ≈ 0 | −15 to −40% | PLANE +15 to +50 |
| Crash ruled not NewAir's fault | ≈ 0 | +8 to +20% | PLANE −10 to −25 |
| Tariff cut / trade deal | −3 to −12% | +5 to +15% | both −5 to −30 |
| Ceasefire / embargo lifted | −8 to −25% | +8 to +30% | both −15 to −40 |
| Protesters hit oil factories | +3 to +20% | −2 to −10% | OIL +5 to +30 |
| Oiler vague threat | +2 to +6% | −2 to −5% | OIL **+10 to +25** (trade it in Window B) |

## Roles and tools

- **Roles:**
  - **Caller/Clock:** reads, decides, keeps time, calls CANCEL ALL.
  - **OIL trader.**
  - **PLANE trader.**
  - **Quant/Logger:** tools, snap reads, analyst scorecard, net Δ.
- **Hand signals:** thumb = direction; 1-3 fingers = size; flat palm = no trade; fist = cancel all.
- **Calculator page:** the OIL & PLANE Calculator (offline copy `derivatives/tools/deriv_calc.html`). Type S1 and the new IV or the analysts' numbers, keep "Positions now" current, and read off one order per line.
- **Tools** (`derivatives/tools/`, run as `python3 -I`):
  - `deriv_live.py event --S0 100 --v0 100 --S1 105 --v1 120 --days 20` prints the click list with exact bot-level prices (`--cands`/`--probs` for analysts, `--K` for PLANE strikes);
  - `deriv_live.py iv --K 100 --C <mid> --P <mid> --days <T>` gives the parity-corrected IV;
  - `analyst.py` combines the analysts;
  - `r3_invert.py` does the round-3 conversions.
- **Still to ask on Slack:**
  - how PnL is marked at round end, and whether positions carry across rounds;
  - whether the new S is published at T+0 or T+60;
  - whether resting orders count toward the 300.
