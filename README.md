# CTC 2026: strategy pack

Strategy for the three cases of the 2026 Cornell Trading Competition. Each case has a full playbook. This page summarises them and shows where everything is.

Overall rank = 0.45 × Systematic Equities rank + 0.35 × Derivatives rank + 0.20 × Mario Markets rank. Spend prep time in that order. Systematic Equities also needs the most engineering.

| Case | Playbook | Tools |
|---|---|---|
| Systematic Equities (hurricane season, bots) | `systematic/systematic_playbook.md` | `systematic/engine/`, `laws/`, `bot_skeleton/`, `fits/`, `research/` |
| Derivatives (OIL and PLANE options, manual) | `derivatives/derivatives_playbook.md` | `derivatives/tools/` |
| Mario Markets (prediction contracts, manual) | `mario/mario_playbook.md` | `mario/tools/` |

All scripts need only the Python standard library. Run them with `python3 -I`.

---

## Systematic Equities: 45%

**How it is won.** Every symbol's settlement value is a closed-form function of public data, accurate to about a cent on 600 research seasons.

- **Island businesses** = undamaged value − (loss per damage point × cumulative damage) − (cost per second × site-weighted seconds closed).

  | Company | Formula |
  |---|---|
  | PORT | 100 − 0.55·D − 0.02·T |
  | ISLE | 60 − 0.38·D |
  | AQUA | 35 − 0.20·D |
  | GRID | 50 − 0.24·D − 0.05·T |
  | FERRY | 25 − 0.05·D − 0.06·T |

  D is cumulative damage in percentage points; T is weight × seconds closed, summed over sites.

- **Insurers.**
  - HAVEN = 75 − 0.06 × clip(L − 400, 0, 900).
  - REIN = 90 − 0.035 × max(L − 1300, 0).
  - L, the season insured loss in $M, = 9·PORT% + 6·ISLE% + 2.5·AQUA% + 7·GRID% + 1.2·FERRY%, summed over storms.
- **Rebuilders** saturate exponentially:
  - RECON in port, grid and ferry damage;
  - MAT in L;
  - GEN in grid outage time.

**Where the house can be beaten.** The house is about as good as a full model at round opens and at storm end. **During the storm it is beatable:**

- **Formation over-reaction.** At every formation, the house marks prices the wrong way, then drifts back by storm end. This happened in 83–97% of the 1,800 research rounds.

  | Symbol | Move 4 s after the storm forms |
  |---|---|
  | HAVEN | +9.5% |
  | Island stocks | +1.4% to +3.0% |
  | Rebuilders | about −1.3% |

- **Lag behind the engine.** The house trails our physics engine, which integrates the 2-s storm feed: in round 3 at real 240 s, PORT error is 4.47 for the engine against 5.68 for the house.
- **Report delays.** It reacts to damage reports 4 s late (ETFs 10 s late).

**Plan.**

| Strategy | Window | Verdict |
|---|---|---|
| Engine taker in limit/12 clips, never more than 1.5% from mid [testnet] | real 80–540 s | GO on the consensus; unproven on books, so test first |
| Formation leg: in at 50–56 s; no forced exit, the engine taker inherits it at 80 s [testnet] | half size in round 1 | test first |
| Flatten before the reports | rounds 1–2, 540–568 s [testnet] | GO |
| Take exact settlement at the final report; sweep every 0.5–1 s until 750 s [testnet] | round 3, 740 s | GO |
| ETF lead-lag, entries capped at mid × (1 + gap/2) [testnet] | – | test first, default off |
| Damage-report snipes | – | test first, default off |
| Wire-rumour fades, insurer-open bets, passive quoting | – | no-go |

**Expected value: plan on $10–15k per season.** This uses fill costs measured on the testnet's real order books, still priced on the research consensus. The earlier estimates fell as each test added realism:

| Estimate | Per season | What changed |
|---|---|---|
| Naive backtest | $35k | Full limit filled at one price, infinite depth |
| Red-team fill model | about $20k | Depth cost, worked clips, losing the report race |
| **Measured testnet costs** | **$10–15k** | Real book ladder; costs ×1.5 and +2 s for crowding and latency |

**Testnet findings (23 Oct recording; full write-up in `systematic/testnet/analysis/TESTNET_FINDINGS.md`):**
- **Costs.**
  - Small clips are cheap: about 12–23 bp half-spread in the storm.
  - Big clips are expensive: a wall sits about 8% from mid beyond roughly 0.7 × the limit.
  - S3 now trades in limit/12 clips, never more than 1.5% from mid.
- **Formation jump.** It appears on the books but arrives about 8 s later than in the consensus. So H1 no longer exits at 72–78 s; the engine taker inherits its position at 80 s.
- **S3, the core engine taker.** It is not yet proven on real books: −$3.8k in the one recorded season, against +$3.6k on the consensus. It needs a full testnet season trading at 25% size.
- **Round-3 settlement trade.** Confirmed: about $920 was available in each of two rounds, and the stale window is longer on the books (6–8 s).
- **Feed.** The connection dropped 5 times in an hour, and the engine has a snapshot bug that mis-prices after a drop. Fix both before trading.
- **Other teams.** They already trade the clock: some bought 1–2 × the limit before the first storm formed.

**Testnet host.** Point the bot at `cornelltradingcompetition.org`: that's where the team key works, and its case file matches the research data exactly, timing included. The starter kit's `hurricane.` subdomain rejects the key and serves a different case file.

**Before the event (P0, about 45 person-hours in the playbook's §7).**
1. On the testnet, record all order books around formation (40–90 s) and both reports. Measure:
   - spread and depth per symbol;
   - whether the books really jump at 64 s;
   - latency;
   - price protection;
   - ETF book lag.
2. Wire `engine/fairvalue.py` into the bot (playbook §9).
3. Rebuild the replay harness so it runs the bot's own strategy code.
4. Drill the kill switch and restarts.
5. Do **not** ask Slack about the formation jump or the house lags.

---

## Derivatives: 35%

**How it is won.** It is a stale-quote game.

| Time after the news (T+0) | What has moved | What to trade |
|---|---|---|
| T+0 to T+60 | Bots still quote options off the old underlying price | Price (delta) edge: deep ITM 60C or 140P first, then ATM in the same direction |
| T+60 to T+120 | Bots quote off the new price but the old vol | Vol edge: straddle at the strike nearest the new price |
| T+120 | Bots snap to the correct vol | Read the true IV |

- About 90% of the value is the price edge in the first minute.
- Bots quote Black-Scholes fair ± 1.00, so the half-spread is about 1.005. A vol-only trade needs a move of at least 9 vol points at ATM to pay.

**By round.**
- **Round 1 (IV announced).** Take every level with positive edge, up to the limits. Use Event 1 to test:
  - the fill rule;
  - how PnL is marked;
  - whether T decays.
- **Round 2 (three analysts).** Take the price part at full size; it is robust to IV error. Size the vol part by the analyst model. First check whether any analyst is ever exactly right (pick one) or none is (weighted average).
- **Round 3 (a quantity linked to IV).** Identify it from the fingerprint table, and confirm at the first snap.

**Risk.** Scoring is Σ(round rank)^1.5, so three solid rounds beat two wins and a disaster. Until round-end marking is confirmed, never hold net-long time value or large delta into a round close.

**Live tool:**
```
python3 -I deriv_live.py event --S0 100 --v0 100 --S1 105 --v1 120 --days 20
```
It prints the exact order list, with the price at each bot level and EV per lot.

---

## Mario Markets: 20%

This may not count toward the overall rank. The packet says gold-sponsor events don't count, and Mario is "presented by" gold sponsor Jump Trading, yet the overall formula gives it 20%. Ask on Slack.

**Where the money is.** Most PnL is in the 1000-lot score contracts (#1, #2, Top-2), where 1 point = $1,000.

**Prior fair values.** These are reasonable values; how good the humans are against the CPUs is the main unknown, so don't bet the level before Race 1.

| Contract | Fair value |
|---|---|
| Race winner, series winner | 25 |
| #1 | ≈ 45 |
| #2 | ≈ 37 |
| Top-2 | ≈ 82 (sd 13.5) |

**The biggest edge.** Reprice the Series-2 score contracts in the first minute after Race 1: Series-2 Top-2 ≈ 82 + 3.1 × (average human points per race − 8.5). Then:
- **Price the order-statistic premium:** #1 = 4m + 8 and Top-2 = 8m + 10 at series start, where m is expected points per human per race.
- **Use the lead tables** for series-winner contracts.
- **Take every arbitrage:**
  - the 4 race-winner contracts sum to exactly 100;
  - Top-2 = #1 + #2;
  - the series-winner basket is worth at least 100, because tied leaders each settle at 50.
- **Pull all quotes before each race starts.**
- **At the finish line,** pre-load limit orders: buy at ≤ 90, sell at ≥ 10.

**Live tool** (Race-1 places and current scores, in contract order H1–H4):
```
python3 -I fv_calc.py --hist "4,1,7,9" --scores 9 15 6 4 --left 3
```

---

## Questions to post in #questions now

The full ranked lists are in each playbook's §8.

**Derivatives:**
- How is PnL marked at round end?
- What does "executed at the midpoint" mean for fills?
- Are the new underlying prices published at T+0 or at T+60?
- Are position limits per contract?

**Mario:**
- Does Mario count toward the overall rank?
- Can the current race's contracts be traded while it runs?
- Are position limits per contract or per market type?

**Systematic Equities:**
- Which host will the live competition use: `cornelltradingcompetition.org`, or `hurricane.cornelltradingcompetition.org` (the starter kit's README), whose case file has different timing?
- What is price protection?
- Is the rate limit a sliding window?
- Do resting orders survive between rounds?
