# Testnet findings: the 23 Oct 2026 recording (season 224 round 3 to season 225)

**What was recorded.** `testnet/recordings/testnet_s224_s225.jsonl` has 44,296 frames, recorded from 10:47 to 11:50 EDT on `cornelltradingcompetition.org`. It covers:
- season 224 round 3 from real 218.3 s (storm age 160; the brief's "real 160 s" is the storm age);
- all of season 225 (Pia, Rex and Sia);
- about 50 s of season 226.

**Where the numbers come from.**
- Four analysis reports (`feed/`, `books/`, `events/`, `rebacktest/`) were checked by an independent verifier (`verify/`). This document keeps only claims the verifier confirmed or corrected.
- Where the verifier did not re-test a claim, the writer re-ran or re-measured it (`writer/`). Anything that rests on neither is labelled, and **[I]** marks an inference or an untested rule.
- Every number cites its output file:
  - `vNN` is `verify/vNN_*.out`;
  - `wNN` is `writer/wNN_*.out`, with a section letter where the file has sections;
  - `w00 engine`, `w00 snapshot` and `w00 doublecount` are the writer's re-runs of the feed report's `engine_sens.py`, `snapshot_resync_check.py` and `doublecount_check.py`. The outputs are identical to the published ones apart from timing lines.
- Re-run everything with `sh writer/run_all.sh <recording> <SP>`.

**How much to trust it.** This is one season of book evidence: 3 full rounds plus one partial, 3 formations and 8 damage reports. The recorder never traded, so our own fills and impact are not observed. Single-round results are anecdotes.

---

## Key findings

1. **The testnet book is not the research consensus plus a spread.**
   - Season 225 opened at the packet's starting prices: 8 of 12 symbols were within 25 bp of the packet, against 6 of 12 within 25 bp of `market.csv` (`v09`).
   - In 225 r1, HAVEN's book sat about 13% above the consensus from 300 to 540 s (56.0-56.3 against 49.4-49.7). It snapped to 52.75 at 552 s (`v10`).
   - Other participants' net flow ran *against* that gap (correlation −0.69), so the gap is the house's own quote level, not skew from flow (`v09`).
   - In 225 r3 and 224 r3 the book tracked the consensus: the RMS gap was 27-165 bp and 22-112 bp, against 173-1,160 bp in 225 r1 (`v10`).
   - **Action:** price every edge off the book. P&L measured on the consensus cannot be checked against fills.
2. **Costs: small clips are cheaper than the RT model, large clips much dearer, and 72-120 s is the expensive window.**
   - Storm half-spread is a median 12-23 bp. An L/4 clip costs a median 34-51 bp for stocks and 16 bp for ETFs (`v03`).
   - Fitting the RT form gives h = 11-16 bp and γ = 0.8-1.3% per full limit for stocks, against the model's 0.25% and 0.5% (`v03`).
   - Every book uses the same ladder: about 0.10 / 0.07 / 0.18-0.30 / 0.24 L, then a 3 L wall about 8% from mid (`w01 D`). A full-limit stock clip therefore costs 304-328 bp (`v03`).
   - Clips cost 2-7× the storm level in 72-120 s (`v03`).
3. **The formation jump shows on the books, but late, so H1 with the playbook's 72-78 s exit made about $0.**
   - The book move has the consensus sign in 29 of 33 symbol-rounds, at a median 1.20× its size (`v04`).
   - The book is half done at a median 72.0 s for stocks and 77.3 s for ETFs (`w06`). The consensus reprices at 64 s and 70 s.
   - Full-size H1 made **+$65 on the books**, against +$7,389 for the same capped trades on the consensus with the RT model (`v04`).
4. **S1-r3 (the settlement trade after the round-3 final report) is confirmed, and the window is longer on the books.**
   - For stock moves of at least 1%, the book has done 20% of its move at +4 s and 91% at +8 s. The consensus has done 99% by +4 s (`v05`).
   - A sweeper starting 1 s after the report finds at least $924 (224 r3) and $927 (225 r3) (`v05`).
   - The 746-s sanity check would cancel PORT, HAVEN and ARCH, the profitable names, in both rounds (`v05`).
5. **S3 (the core engine taker) is not yet proven on the books.**
   - S3 alone in season 225 made −$3,790 (`r11`, reproduced in `v07`) and −$2,175 (`v12`) on the books, against +$3,569 and +$3,000 on the consensus.
   - Round 2 drives the loss (books −9,407): the engine was also wrong there on the consensus. Round 3 made +7,738 on the books (`v12`).
   - The S3 signal is not reversed on the books (`v08`).
6. **Re-backtest with measured costs, still priced on the consensus.**
   - The Plan as written earns **10,713 [9,811]**, with p5 −7,821 (`v06`).
   - The re-backtest's recommended configuration, with H1 at full size, earns **19,772 [20,061]**, with p5 −804 (`w02`). With costs ×1.5 and +2 s of latency it earns 14,227 [14,562]. With H1 at half size it earns 16,702 [16,591], with p5 +1,584.
   - **Plan on 10-15k per season.** Nothing here is validated on books.
7. **The ETF books lag their baskets by 7 s, but lead-lag pays only with a price cap.**
   - Correlation peaks at a 7 s lag: ARCH +0.49, REBLD +0.39 (`w01 E`).
   - With the entry capped at mid × (1 + gap/2), the lead-lag rule makes +2.7k to +3.7k over 4 rounds. Walking the book uncapped loses 6.1k to 9.7k (`w01 F`, `F2`).
   - It stays TEST-FIRST and default OFF.
8. **Feed: 5 abrupt drops and one 31.7-s outage.**
   - 22 messages were lost in season 225 and 2 in season 224, one of them official advisory #5 (`v01`).
   - Snapshots every 30.11 s carry the latest obs, a thinned track, the latest advisory, impacts and all damage reports (`v01`). They do not carry the lost intensity path.
   - The engine's snapshot handler resets the pattern posterior. Replaying each official advisory as lost and then recovered from a snapshot, 16 of 24 move a fair value past the S3 threshold. The worst is GRID +4.99, 11× the threshold (`w00 snapshot`).
   - With the two-part fix, 22 of 24 are identical to the clean feed and one more is within 0.02 of the threshold. The exception had its next snapshot 30.15 s later, a genuine gap.
9. **Testnet official advisories are fresh draws of the forecast ensemble. It does not matter to the engine.**
   - Outlook, number, age and time match in 28 of 28 advisories. `hurricane_conditions` differs with sd 0.040 (`v01`).
   - With testnet advisories, the S3 target changes in 11 of 1,551 symbol-steps, and P&L is $11,164 against $11,122 (`w00 engine`).
10. **Other teams already trade the clock.**
    - In 0-50 s of 225 r1, others net-bought 1.1-2.0 L of PORT, HAVEN, FERRY and ARCH (`v09`). The playbook's price-based crowding check never fires (`w04`).
    - Seven large market orders (GEN 558 shares, the other six a full limit) in the first 2 s of season 225 hit the ±8% walls and paid $8,522 against the 20-s mid (`w03`).
11. **Report snipes stay OFF, and the drift cannot be tested here.**
    - Season 225's rounds 1-2 reports moved prices less than 1%, so the stale value per report was $0-12 in stocks and $0-53 in ETFs (`w01 I`).
    - Season 225 ran against the research-mean drift. The clock-only drift trade lost 39.5k on the books and 22.7k on the consensus (`w01 H`).

---

## 1. Feed integrity and the robust-feed design

### 1.1 Disconnects and losses (`v01`, `w01 A`, `w01 B`)

| Drop | Round, real s | Phase | Connection lifetime | Gap to reconnect | Nearest tick (last frame vs tick) | Messages lost | First snapshot after the drop (`last_seq`) |
|---|---|---|---|---|---|---|---|
| 1 | 224 r3, 329.4 | storm | 111.1 s | 1.62 s | official advisory 330 (−0.56 s) | 746 (official #5), 747 (storm) | 31.6 s (764) |
| 2 | 225 r1, 52.7 | pre-storm | 743.2 | 1.19 | – | none | 7.8 s |
| 3 | 225 r1, 480.2 | storm | 426.3 | 1.89 | track 480 (+0.19 s) | 250 (storm) | 2.1 s (250) |
| 4 | 225 r2, 221.1 | storm | 699.5 | **31.72** | track 220 (+1.07 s) | 386-405: 16 storm, 3 wire, 1 track | 49.8 s (416) |
| 5 | 225 r3, 450.7 | storm | 1,158.5 | 1.32 | official advisory 450 (+0.75 s) | 812 (storm) | 30.8 s (828) |

- **The drops were abrupt** (no close frame). Every lost seq falls inside a gap (`v01`).
- **The exchange kept running during drop 4.** Twelve trades stamped inside the outage arrived after the reconnect; the first is stamped 11:20:59.80 (`w01 A`). So only the WebSocket endpoint was unreachable.
- **Drop 4's 31.7 s matches the recorder's backoff ladder** of 1, 2, 4, 8 and 16 s (`v01`).
- **All 4 storm-phase drops fell between 0.56 s before and 1.07 s after a track or advisory tick** (`w01 A`). This suggests a server-side trigger.
  - The verifier rated the feed report's claim that two connections would have saved 21.77 of 22 messages **unsupported**: it assumes the two connections drop independently.
- **Clock** (`v01`):
  - round opens 10:43:48.455, 11:00:50.037, 11:16:50.536 and 11:32:51.126;
  - residual sd 72-106 ms, worst +0.398 s;
  - the server tick runs slow, with slope 1.000395-1.000576.
  - Anchor the market clock to each storm frame rather than to a fixed 30×.

### 1.2 Data hygiene (`w01 C`, `v01`)

| Fact | Value | Bot consequence |
|---|---|---|
| Largest gap between frames while connected | storm 2.12 s; pre-storm 2.54 s; aftermath 5.21 s; pause 29.57 s | Watchdogs at 3 / 8 / 8 / 35 s |
| Bursts | 116 frames in 1 s at 225 r3 741.2 s (the final report); at most 4 news frames in 1 s | Parse off the hot path; the S1-r3 orders go out in this burst |
| Duplicate `trade_id` | 37, every one re-sent right after a reconnect | Deduplicate by `trade_id` |
| Empty books | 72 frames, at every close (779-781 s) and at season boundaries; 74 quotes with bid/ask `None` | Handle `None`; do not treat as a crash |
| Crossed or locked books | 0 | – |
| Sequence numbers | season 225 starts again at seq 0 | Reset the engine and the dedup sets on a seq restart |
| Snapshots | 123, median spacing 30.11 s; `snapshot.advisory` has no `storm`, `seq`, `round` or `type` field | See §1.3 |

### 1.3 Engine snapshot bug (`v02`, `w00 snapshot`, `w00 doublecount`)

| Case | Result |
|---|---|
| Engine as is, recording replayed | The snapshot resets the pattern posterior to key (3, None) with 0 obs. The next live obs resets it again to (3, 'Omar') with 1 obs, so the recovered advisory and the snapshot track are discarded. It fired twice in 224 r3: on the join and on the recovery of advisory #5 (`v02`) |
| Each of the 24 season-225 official advisories lost, then recovered from the next snapshot, engine as is | 16 of 24 move some fair value past k·sd + h·P. The pattern posterior moves by up to 0.986. Worst: GRID 4.99, 11.2× the threshold (r2 #2) |
| Fix part 1 only (add the snapshot's `storm` to its advisory, obs and track), snapshot arriving before the live message | The official label is counted twice; ARCH moves +1.30 |
| Fix parts 1 and 2 (part 2: never feed an official advisory whose number is already known) | The double-count case is identical to the clean feed (0.000). Of the 24 recovery cases, 22 match exactly and r1 #6 is off by 0.007 (0.02 of the threshold). r3 #1 is 4.6× the threshold because its next snapshot came 30.15 s later (a genuine gap) |

### 1.4 Robust-feed design (to build)

1. **Two market-data connections** on two network paths, such as Wi-Fi plus a hotspot, or the standby laptop. Ask for the connection limit.
   - The benefit cannot be measured from one connection: if the drops are server-side, both connections drop together.
   - Recording two connections in parallel for one season costs nothing and settles it.
2. **Merge news by (season, seq).** Forward the first copy and drop repeats. Reset the seen-set when seq restarts at 0, or when a snapshot shows `last_seq` −1 or a new date.
3. **Hold each snapshot for 1 s**, then forward it only if `last_seq` is above the highest seq already forwarded. Resync obs, track, advisory (with the §1.3 fix), impacts and damage from it. Read wire outlooks from the snapshot headlines; the feed report found each wire template maps to one outlook (`feed/wire_templates.out`, not re-verified).
4. **Market data:** for each symbol the newest server timestamp wins. Deduplicate trades by `trade_id`.
5. **Reconnect** after 0.25 s, then every 0.5 s with jitter, capped at 2 s. Do not use the 1-2-4-8-16 s ladder.
6. **Watchdogs** at 3 s in the storm, 8 s pre-storm and in the aftermath, and 35 s in pauses.
   - S3 keeps the 6-s engine-staleness rule.
   - A storm-phase gap of more than 10 s that neither connection filled switches S1-r3 to the wide ε (playbook §4.3). Drop 4 would have done so.

---

## 2. Advisory differences and engine impact

| Message type | Testnet vs research feed | Source |
|---|---|---|
| Storm obs, tracks, wires, impacts, damage reports, formed, dissipated | Identical. Only official advisories differ | `v01` |
| Official advisories (28 live: 24 in s225, 4 in s224) | Outlook, number, age and time are identical in 28 of 28. In s225 the cone differs in 24, `hurricane_conditions` in 13 and the headline in 13 | `v01` |
| `hurricane_conditions`, s225 | Differences: n = 96, mean +0.0010, sd 0.0395, max 0.160. Non-zero in 4 of 58 cases where research is 0, and in 0 of 4 where research is 1. z-sd is 1.04 for an independent 47-member ensemble draw | `v01` |
| Cone position, s225 | Mean difference 3.8 / 7.5 / 17.8 / 40.4 units at leads of 30 / 60 / 120 / 240 s. Spreads are unbiased: 22.0 vs 21.7 … 269.6 vs 272.7 | `v01` |

**Engine impact** (`w00 engine`; bot configuration: fast params, analog library limited to seasons 226-600, fair values every 10 s at 60-540 s, 147 points):

| Feed compared with research | max \|Δμ\| / (k·sd + h·P) | mean | Pattern max \|dp\| | S3 targets that differ | S3 P&L (research $11,122) |
|---|---|---|---|---|---|
| Research with the testnet advisories swapped in | ≤ 0.30 (HAVEN) | ≤ 0.005 | 0.027 | 11 of 1,551 | $11,164 |
| Recorded testnet feed (drops included) | up to 1.14 (AQUA, r2 s=250, inside the 31.7-s outage) | ≤ 0.021 | 0.027 | 13 of 1,551 | $11,065 |
| Recorded feed, excluding the outage points | ≤ 0.30 | ≤ 0.006 | 0.027 | – | – |
| After the round-3 final report | 0 for every feed | – | – | – | – |

`check_recording.py` will therefore always flag official advisories. Compare them on outlook, number, age and time only.

---

## 3. Books: spreads, depth and cost curves

### 3.1 The house ladder (`w01 D`; pre-storm 5-55 s, medians)

| | Level 1 | Level 2 | Level 3 | Level 4 | Level 5 (wall) |
|---|---|---|---|---|---|
| Stocks: size | 0.08-0.16 L | 0.07-0.09 L | 0.18-0.30 L | 0.24 L | 3.00 L |
| Stocks: distance from mid | 8-12 bp | 16-22 bp | 38-49 bp | 70-89 bp | 797-804 bp |
| ETFs: size | 0.39 L | 0.28 L | 1.20 L | 0.96 L | 3.00 L |
| ETFs: distance from mid | 10 bp | 20 bp | 45-64 bp | 91-127 bp | 791-800 bp |

**After each open, only the wall is quoted at first.** The touch first came inside 1% at a median 0.64-1.01 s after the open, and at the latest 2.50 s after (`w03`). In season 225, seven large market orders sent in that window filled 773-802 bp from the 20-s mid, at a cost of $8,522 (`w03`). They were PORT, ISLE, MAT, HAVEN, ARCH and REBLD at a full limit, and GEN at 558 shares.

### 3.2 Spreads and storm costs per symbol (`v03`; 0.25-s grid; cost = mean of a market buy and sell walking the book, bp of touch mid; medians unless stated)

| Sym | L | Touch (sh) | Storm half-spread median / p90 | Storm cost 10 sh / L/8 / L/4 / L/2 / L | Storm L/4 mean by round: 224r3 / 225r1 / r2 / r3 | RT-form fit h / γ (bp, x ≤ ½) |
|---|---|---|---|---|---|---|
| PORT | 200 | 20 | 16.8 / 56.6 | 18 / 23 / 41 / 67 / 328 | 42 / 39 / 43 / **89** | 11.2 / 113 |
| ISLE | 300 | 29 | 14.3 / 25.0 | 15 / 19 / 35 / 55 / 310 | 28 / 38 / 40 / 37 | 10.7 / 90 |
| AQUA | 500 | 48 | 15.0 / 32.4 | 15 / 20 / 38 / 59 / 310 | 35 / 49 / 51 / 30 | 11.4 / 96 |
| GRID | 400 | 40 | 15.7 / 27.8 | 16 / 19 / 36 / 58 / 307 | 38 / 32 / 40 / 42 | 11.2 / 94 |
| FERRY | 800 | 80 | 16.8 / 38.0 | 17 / 21 / 40 / 64 / 304 | 51 / 36 / 41 / 53 | 12.7 / 102 |
| RECON | 500 | 50 | 15.9 / 31.9 | 16 / 19 / 35 / 60 / 307 | 31 / 34 / 42 / 53 | 11.0 / 96 |
| MAT | 700 | 70 | 16.1 / 30.5 | 16 / 19 / 36 / 60 / 306 | 34 / 29 / 41 / 48 | 11.6 / 96 |
| GEN | 600 | 60 | 16.1 / 26.5 | 16 / 20 / 34 / 53 / 312 | 33 / 31 / 37 / 43 | 12.7 / 80 |
| HAVEN | 250 | 25 | 23.2 / 67.8 | 23 / 28 / 51 / 80 / 326 | 60 / 42 / 59 / **94** | 16.0 / 129 |
| REIN | 200 | 19 | 12.2 / 15.6 | 12 / 16 / 29 / 47 / 316 | 32 / 28 / 27 / 29 | 7.8 / 78 |
| ARCH | 50 | 20 | 15.8 / 35.6 | 16 / 16 / 16 / 20 / 35 | 15 / 15 / 19 / 27 | 14.0 / 11 |
| REBLD | 100 | 40 | 15.5 / 29.6 | 16 / 16 / 16 / 19 / 32 | 14 / 14 / 17 / 24 | 14.5 / 7 |

### 3.3 Costs by phase (`v03`; L/4 clip, median bp)

| Sym | pre 3-60 s | H1 entry 50-58 s | 72-80 s | 80-120 s | storm 120-540 s | after 540-780 s |
|---|---|---|---|---|---|---|
| PORT | 22 | 22 | 64 | 169 | 41 | 23 |
| ISLE | 22 | 24 | 86 | 76 | 35 | 25 |
| AQUA | 24 | 24 | 85 | 76 | 38 | 28 |
| GRID | 22 | 22 | 79 | 115 | 36 | 31 |
| FERRY | 20 | 21 | 76 | 66 | 40 | 31 |
| RECON | 22 | 21 | 57 | 108 | 35 | 32 |
| MAT | 25 | 24 | 56 | 86 | 36 | 31 |
| GEN | 26 | 26 | 91 | 86 | 34 | 27 |
| HAVEN | 21 | 21 | 141 | 254 | 51 | 28 |
| REIN | 25 | 26 | 30 | 27 | 29 | 29 |
| ARCH | 10 | 10 | 0.2 | 46 | 16 | 10 |
| REBLD | 10 | 10 | 28 | 32 | 16 | 10 |

- **Phase effects.** The 72-120 s window costs 2-7× the storm. Pre-storm and aftermath clips are cheap.
  - ARCH's 0.2 bp at 72-80 s is not a real cost. At that point the ARCH book is clamped one tick inside its stale levels (see §4.5).
- **The table means are skewed by one round.** PORT's storm L/4 mean is 54 and HAVEN's 64, because of 225 r3 (89 and 94). The medians are 41 and 51 (`v03`).
- **Follow-on** (`w01 G`). After another taker's order of at least 0.2 L, an L/4 clip on the same side costs more than it did just before:
  - stocks: median 27 bp just before, 52 bp at +2 s (n = 100);
  - ETFs: 16 bp just before, 45 bp at +2 s (n = 210).
  - These are gross figures: they include whatever the price did after the order.
  - The re-backtest's follow-on term of 100 bp per L of our own same-side flow in 8 s for stocks (about 25 bp per L/4 clip) is in line with this. Its 48 bp per L for ETFs looks low against this check.
- **What the bot should use:** piecewise-linear interpolation of the phase `table` in `books/cost_model.json`, plus the follow-on term, plus the impact kernel. The impact kernel is fitted on other participants' flow (`rebacktest/r00_followon.out`) and was not re-measured independently. Treat clips above about 0.7 L as off-limits for stocks.

---

## 4. Which edges exist on real books

| Edge | On the books? | Size on books vs consensus (same rounds) | Verdict |
|---|---|---|---|
| **H1 formation leg** | Yes, late. Sign agrees in 29 of 33; size ratio 1.20 (`v04`). Book half-move at a median 72.0 s for stocks (range 66.0-89.6 s) and 77.3 s for ETFs (`w06`) | Full size, exit 72-78 s: **+65** vs +7,389. Half size: −1,658 vs +4,401 (`v04`) | TEST-FIRST, then GO with **no exit before 80 s** (S3 inherits the H1 book) |
| **In-storm drift** (clock-only) | Sign agrees in 30 of 33 (`w01 H`), but season 225 ran against the research mean | Drift trade **−39,476** vs −22,707 consensus RT (`w01 H`) | Fallback only. No evidence either way |
| **Report windows, rounds 1-2** (S2, S1) | The book is stale for 6-8 s (`v05`) | Stale value per report in 225 r1-r2: $0-12 in stocks, $0-53 in ETFs. 225 r3 preliminary: $263-352 in stocks at +1-2 s (`w01 I`) | OFF until the latency test (§7) |
| **S1-r3** | Yes. The book is stale 6-8 s after the final report | A sweeper from +1 s finds at least **$924 / $927** per round 3. The consensus oracle is $1,335 / $1,407 at 742 s and $196 / $225 at 744 s (`v05`) | **GO; keep the taker**; sanity check at ≥ 756 s |
| **ETF lead-lag** | Yes, a 7-s lag (`w01 E`) | **+2,673 to +3,684** over 4 rounds with a capped entry; **−6,145 to −9,675** walking the book (`w01 F`, `F2`) | TEST-FIRST, default OFF; capped entries only |
| **S3 engine taker** | The signal holds against the 600-s level (`v08`) | S3 alone in season 225: **−3,790 / −2,175** on books vs +3,569 / +3,000 on consensus. Round 3: +6,763 / +7,738 on books (`v07`, `v12`) | GO (core), but score it on fills next testnet season |

### 4.1 Formation and H1 (`v04`, `v09`, `w04`, `w06`)

| H1 exit window, full size, entry cap kept | Books r1 / r2 / r3 = total | Consensus RT, capped |
|---|---|---|
| 72-78 s (playbook) | +2,947 / +872 / −3,754 = **+65** | +7,389 |
| 80-86 s | +808 | +8,168 |
| 84-90 s | +2,785 | +12,971 |
| 86-92 s | +4,551 | +17,026 |
| 120-126 s | +24,479 | +29,803 |

- **The 72-78 s exit sells into a stale book.** In r1 at 74 s, GRID, FERRY and ARCH had moved +0.15%, +0.16% and +0.26% on the book, against +5.0%, +10.0% and +4.0% on the consensus. They caught up by 80 s (`v04`).
- **Later exits look better only because of the drift.** Season 225 drifted the H1 way, so later exits are flattered by drift, not by the formation jump (`v04`).
- **The entry cap (48-s mid ± 0.5%) binds, and it protects.** In r2, AQUA filled 173 of 500, GRID 188 of 400 and GEN 261 of 600. In r3, PORT filled 86 of 200 and RECON 85 of 500 (`v04`).
  - The books report replayed H1 without the cap and got −2.7k. The verifier attributes the difference to the missing cap.
- **Crowding is real.**
  - In 0-50 s of r1, others net-bought PORT +1.52 L, HAVEN +1.96 L, FERRY +1.60 L and ARCH +1.06 L, and sold REBLD −0.99 L. In r3 they bought HAVEN +1.94 L and ARCH +2.06 L (`v09`).
  - In 60-80 s of r3, others sold ARCH −4.62 L into the stale book (`v09`).
  - The playbook's price-based crowding skip never fires: the 30→48 s book move was at most 0.21% (`w04`).
  - r1, a crowded round, was H1's best round on the books. So there is no evidence the skip helps.
- **This season's formation-jump direction varied.** The book moved HAVEN +21.8%, +6.6% and −10.8% from 58 to 90 s in r1, r2 and r3 (`w04`).

### 4.2 Reports and S1-r3 (`v05`, `w01 I`)

- **The book reprices 3-4 s after the consensus.** For the 7 stock moves of at least 1%, the median share of the move done is:

  | +2 s | +4 s | +6 s | +8 s | +10 s |
  |---|---|---|---|---|
  | 0.07 | 0.20 | 0.36 | 0.91 | 1.00 |

  The consensus has done 0.99 by row +2 (+4 s). The books report's "+1.0 s / 0.0 s" lag was corrected by the verifier.

| S1-r3 | 224 r3 | 225 r3 |
|---|---|---|
| Final report received | 740.26 s | 740.23 s |
| Sweeper from te + 0.3 / 1 / 2 / 4 / 6 s (lower bound; ignores re-posts) | $937 / 924 / 679 / 619 / 362 | $966 / 927 / 834 / 545 / 229 |
| Value range including re-posted stale quotes (verifier's range) | $937-1,250 | $966-1,386 |
| Value other participants took beyond S ± ε | $934 | $2,307 |
| Consensus oracle at 742 / 744 s, 25 bp | $1,335 / $196 | $1,407 / $225 |
| Sanity check \|mid − S\| > max(3ε, 0.5%) would cancel at 746 / 750 / 752 / 756 s | PORT, HAVEN, ARCH / ARCH / ARCH / none | PORT, HAVEN, ARCH / HAVEN / none / none |

- The re-backtest priced the S1-r3 taker at −$15 because it fills on the consensus at 744 s, after the consensus has repriced. Its `REC without S1-r3` is 19,786 against 19,772 with it (`w02`). That fill model does not match the books, so **its advice to drop the taker is unsupported**.

### 4.3 S3 on the books (`v07`, `v08`, `v12`)

| Season 225, S3 only | r1 | r2 | r3 | Total |
|---|---|---|---|---|
| Independent replay, consensus (`v12`) | +1,949 | −4,448 | +5,499 | +3,000 |
| Independent replay, books (`v12`) | −505 | −9,407 | +7,738 | **−2,175** |
| `r11` (REOPT without H1), consensus | +2,182 | −4,680 | +6,068 | +3,569 |
| `r11`, recorded books, 0.3 s | −779 | −9,773 | +6,763 | **−3,790** |

- **The S3 signal holds against the post-storm book level.** Correlation of (engine − book) with (book at 600 s − book) is 0.94, 0.74, 0.13 and 0.50 in 224 r3 and 225 r1 / r2 / r3.
  - The engine's RMSE against the 600-s book level is 137, 181, 228 and 330 bp; the book's own is 344, 241, 148 and 306 bp. So the engine beats the book in 224 r3 and 225 r1 and loses in 225 r2 (`v08`).
- **In round 3, the sign rule earns about the same on books as on the consensus.** The rule (|engine − price| > 75 bp, scored against settlement) earns +304 bp per signal on book prices (n = 419), against +338 bp on the consensus (`v08`).
- **Flattening after the storm-end snap barely helps.** In r1 the HAVEN book snapped from 56.2 to 52.75 at 546-552 s (`v10`). Flattening rounds 1-2 over 552-568 s instead gives books r1 +63 and r2 −9,453 (`v12`).

### 4.4 Drift (`w01 H`)

- Season 225's in-storm move from 74 to 538 s ran against the research mean. HAVEN's book rose +26.3%, +30.1% and +7.7% by round; its consensus rose +7.1%, +22.1% and +6.2%.
- The clock-only drift trade (playbook §4.7, worked L/4 clips) lost:

  | | r1 | r2 | r3 | Total |
  |---|---|---|---|---|
  | Books | −13,633 | −18,275 | −7,568 | −39,476 |
  | Consensus RT | −4,370 | −14,986 | −3,351 | −22,707 |

- This is one season against a mean measured over 600, so it neither confirms nor refutes the drift.

### 4.5 ETF lag (`w01 E`, `w01 F`, `w01 F2`, `v03`)

| | ARCH | REBLD |
|---|---|---|
| Correlation of 2-s changes, ETF(t) vs basket NAV(t − k) | +0.08 at 0 s, peak **+0.49 at 7 s** | +0.10 at 0 s, peak **+0.39 at 7 s** |
| RMS of log ETF(t)/NAV(t − k), demeaned per round | 60 bp at 0 s, **32 bp at 7 s** | 42 bp at 0 s, **28 bp at 7 s** |
| Touch through NAV by more than 50 bp | 7.9% of 60-776 s; 65 episodes, median 1.5 s | 5.3%; 46 episodes, median 1.5 s |
| 68-80 s after formation: NAV − ETF, 225 r1 / r2 / r3 | +0.7 to +6.5% / −0.6 to +2.7% / −2.4 to −0.4% | −2.3 to −0.2% / −0.8 to +0.9% / −0.7 to +1.3% |
| Size priced better than NAV ∓ 0.3% at 68-80 s, median, % of limit | 216 / 216 / 158 | 216 / 28 / 0 |
| Storm half-spread, median / p90 (`v03`) | 15.8 / 35.6 bp | 15.5 / 29.6 bp |

**The playbook §4.5 lead-lag rule replayed on the books, both ETFs, full limit, 4 rounds** (224 r3 from 222 s). Our fills do not deplete the book, which is optimistic:

| Entry order | Decision grid | 0.3 s latency | 1 s | 2 s |
|---|---|---|---|---|
| Walk the book (no cap) | 0.5 s | −6,145 | −7,824 | −9,675 |
| Marketable limit capped at mid × (1 + gap/2) | 1 s | +2,673 | +3,016 | +3,230 |
| Same, capped | 0.5 s | +3,684 | +3,483 | +2,382 |

- **The book lag is real, but the cap does the work.** Without it the rule pays the spread on entries the book has already corrected.
- **Do not swap the ETF legs for lead-lag.** On the consensus, the ETF legs inside S3 and H1 are worth 19,772 with ETFs against 15,902 without [20,061 vs 16,256] (`w02`). Lead-lag's book value on one season is no larger than that.
- **A formation-only ETF rule would conflict with H1's ARCH leg.** ARCH is stale at 68-80 s, but H1 already holds the ARCH limit.

---

## 5. Re-backtested expected value with measured costs

**On the consensus.** Holdout 401-600, train 1-400 in [brackets], $ per season at full limits. Fills use the `cost_model.json` phase tables, follow-on and impact; the report race is lost.

"REC" is the re-backtest's recommended configuration: REOPT with H1 at full size, entered in L/4 clips at 50-56 s. This document keeps H1 at ½ size in round 1 until the round-1 check passes (§6), so the "H1 at half size" row is the floor for round 1.

| Configuration | Mean | p5 | Seasons > 0 | Source |
|---|---|---|---|---|
| Plan as written, RT fill model (playbook) | 20,346 [19,504] | 2,313 | 96% | playbook, `r01` |
| Plan as written, measured fills | **10,713 [9,811]** | −7,821 | 83% [81%] | `v06` |
| REOPT (H1 in L/8 clips, 42-56 s) | 19,778 [20,145] | −663 [1,411] | 94% | `v06` |
| **REC** | **19,772 [20,061]** | −804 [1,323] | 94% [96%] | `v06`, `w02` |
| REC under the RT fill model | 24,338 [24,504] | 3,728 | 96% | `w02` |
| REC, costs ×1.5 | 14,993 [15,358] | −4,588 | 89% | `w02` |
| REC, costs ×2 | 10,653 [11,119] | −8,344 | 78% | `w02` |
| REC, +2 s latency | 18,991 [19,231] | −1,526 | 94% | `w02` |
| **REC, +2 s and costs ×1.5** | **14,227 [14,562]** | −5,939 | 87% | `w02` |
| REC, 60-120 s costs ×3 | 15,910 [16,550] | −4,341 | 86% | `w02` |
| REC, H1 at half size | 16,702 [16,591] | 1,584 [155] | 96% | `w02` |
| REC, H1 without RECON, MAT, GEN, REBLD | 18,660 [18,755] | 1,015 [2,526] | 95% | `w02` |
| REC without H1 (S3 + S1-r3) | 12,453 [12,024] | −2,972 | 91% | `w02` |
| REC without ETFs | 15,902 [16,256] | −1,556 | 94% | `w02` |
| REC without S1-r3 | 19,786 [20,071] | −768 | 94% | `w02` |

REC uses 1,229 actions per season and peaks at the 150-per-minute cap (`w02`).

**On the recorded books** (season 225 only; `r11`, reproduced exactly in `v07`):

| Configuration | Consensus | Recorded books, 0.3 s | Books, +2 s |
|---|---|---|---|
| REOPT | 34,872 | 23,210 | 23,569 |
| REOPT, forced H1 exit 72-86 s | 24,638 | 11,360 | 11,540 |
| Plan | −4,991 | −16,323 | −16,062 |
| S3 only | 3,569 | −3,790 | −3,556 |

- **REOPT's +23.2k on the books is not a validation.** H1 inventory held through a season that drifted the H1 way made +48.4k, and S3 lost 25.4k (`v07`).
- **Planning number: 10-15k per season.**
  - The top of the range is REC at costs ×1.5 with +2 s of latency (14.2k). The bottom is REC at costs ×2 (10.7k).
  - Both are consensus-priced. The verifier rates the planning number unsupported on books.

---

## 6. Changes to the playbook strategies and bot parameters

| Item | Playbook before | Now [testnet] | Evidence |
|---|---|---|---|
| Fill model h | 0.25% | Use the phase tables (`cost_model.json`). Storm intercept 11-16 bp for stocks, 14 bp for ETFs | `v03` |
| Fill model γ | 0.5% per full-limit clip | Storm 0.8-1.3% for stocks (x ≤ ½), 0.07-0.11% for ETFs. 80-120 s costs 2-7× the storm. A 3 L wall beyond about 0.7 L | `v03`, `w01 D` |
| S3 clip | ¼ L per 2 s | **L/12 per 2 s** (bot units below) | `v06`, `w05` |
| S3 entry threshold | k·sd + γ·mid (75 bp) | k·sd + 2.0 × (walk cost of one L/12 clip + 36 bp; 17 bp for ETFs). That is 107-138 bp for stocks in the storm and 69-73 bp for ETFs; 138-342 bp at 80-120 s. k stays 0.1 in rounds 1-2 and 0.2 in round 3 | `w05`, `v06` |
| S3 order cap | min(μ − thr, ask × 1.005) | Same, and **never beyond mid ± 1.5%** (the wall sits about 8% out) | `w01 D` [I] |
| Flatten, rounds 1-2 | 562-568 s, ¼ clips | **540-568 s, L/12 clips** | `v06` |
| H1 entry | 50-56 s, L/4 clips, capped at the 48-s mid ± 0.5% | Unchanged. The cap binds and protects | `v04` |
| H1 exit | 72-78 s | **No forced exit; S3 inherits the H1 book at 80 s.** If the engine is not live by 76 s, exit over 84-98 s in L/8 clips [I] | `v04`, `w06`, `v06` |
| H1 size | Round 1 ½; rounds 2-3 full if the round-1 check passes | Unchanged. On the consensus, full size from round 1 earns 19,772 against 16,702 at half size, but its p5 is −804 against +1,584. On the books H1 is unproven | `w02`, `v04` |
| H1 crowding skip | 30→48 s price move > ⅓ of the jump | **Drop it.** It never fires on the books | `w04`, `v09` |
| H1 round-1 check | Cost ≤ ½ of the 58→74 s jump | **Use the 58→90 s book move** of HAVEN, PORT and ARCH against our own entry cost per share (48-s mid to fill); pass if cost ≤ ⅓ of the move [I] | `v04` (the book has barely moved by 74 s) |
| H1 rebuilder legs | In | Keep at base cost. Drop RECON, MAT, GEN and REBLD from H1 if round-1 costs are ≥ 1.5× the tables (p5 then 1,015) | `w02` |
| S1-r3 | Take at 740 s, top up every 2 s; sanity check at 746 s | **Keep the taker.** Sweep every 0.5-1 s until 750 s. **Sanity check at ≥ 756 s** | `v05` |
| ETF lead-lag | TEST-FIRST | **TEST-FIRST, default OFF.** If enabled: entries only as marketable limits capped at mid × (1 + gap/2), never a book walk. Keep the ETF legs in S3 and H1 | `w01 F`, `F2`, `w02` |
| Report snipes (S2; S1 in rounds 1-2) | OFF | OFF. The book is stale 6-8 s, but latency is unmeasured | `v05`, `w01 I` |
| Round opens | – | **No marketable orders in the first 3 s of a round** | `w03` |
| Feed | One connection; 6-s and 10-s rules | Two connections, merge and dedup, fast reconnect, phase watchdogs, the engine snapshot fix (§1.3-1.4) | `v01`, `v02`, `w00` |
| Fill sanity | 3 clips with slippage > 3 × (h + γ/4) | 3 clips with slippage > 3 × the phase-table cost | `v03` |
| Action budget | 150 per minute | Unchanged. REC uses 1,229 actions per season and reaches 150 in a minute | `w02` |
| Planning EV | 20.3k | 10-15k, consensus-priced | §5 |

**Bot units** (`w05`; exit band = phase mean half-spread):

| Sym | L | S3 clip L/12 (sh) | H1 clip L/4 | L/8 (H1 fallback exit) | Entry cost term, storm / 80-120 s / after (bp) | Exit band, storm / 80-120 s (bp) |
|---|---|---|---|---|---|---|
| PORT | 200 | 16 | 50 | 25 | 123 / 315 / 101 | 24 / 114 |
| ISLE | 300 | 25 | 75 | 37 | 107 / 165 / 99 | 16 / 43 |
| AQUA | 500 | 41 | 125 | 62 | 112 / 170 / 98 | 18 / 47 |
| GRID | 400 | 33 | 100 | 50 | 107 / 205 / 99 | 17 / 63 |
| FERRY | 800 | 66 | 200 | 100 | 117 / 138 / 102 | 21 / 32 |
| RECON | 500 | 41 | 125 | 62 | 110 / 195 / 99 | 18 / 57 |
| MAT | 700 | 58 | 175 | 87 | 110 / 164 / 100 | 18 / 43 |
| GEN | 600 | 50 | 150 | 75 | 109 / 161 / 100 | 18 / 42 |
| HAVEN | 250 | 20 | 62 | 31 | 138 / 342 / 104 | 31 / 123 |
| ARCH | 50 | 4 | 12 | 6 | 73 / 129 / 58 | 19 / 46 |
| REBLD | 100 | 8 | 25 | 12 | 69 / 101 / 55 | 17 / 33 |

REIN stays out of H1 and S3.

---

## 7. What still needs testing

| # | Open question | Why it matters | How |
|---|---|---|---|
| 1 | Our own fills and impact | Every cost above comes from other participants' orders | The 1-lot plan below, then a full testnet season at 25% size |
| 2 | S3 on books | The only book evidence (one season) is negative in rounds 1-2 | Full testnet season at 25% size, with S3 P&L scored on fills. If S3 is negative on fills in rounds 1-2 again, restrict it to round 3 [I] |
| 3 | Latency: message → order → ack → visible fill | The S2/S1 gate (p95 < 1.5 s); S1-r3 sweep timing; the +2 s assumption | Test T4-T5 |
| 4 | Price protection and order semantics | Sets the mid ± cap; whether remainders rest; IOC | Tests T2-T3 and Slack |
| 5 | Whether the drops are server-side | Decides whether a second connection helps | Record two connections in parallel for one season (zero risk) |
| 6 | Engine fix and merged feed | The §1.3 bug is material | Replay this recording through the patched engine and the merge layer; expect 22 of 24 recovery cases exact and r3 #1 off because of its 30-s gap |
| 7 | Sample size | 3 formations and 8 reports | Record at least 2 more seasons (books plus news) and re-run `verify/` and `writer/` |
| 8 | Testnet house = live house? | The book levels differ from `market.csv`; depth and walls | Slack |

### Proposed 1-lot order test plan (needs team approval before it runs; nothing has been sent)

**Scope and limits.**
- Testnet host `cornelltradingcompetition.org` only, with the team bot key. Never on the live exchange.
- 1 share per order (1 unit for create/redeem).
- At most 60 order actions per round and 10 per 10 s.
- Kill switch armed. Every test position is closed with a 1-lot order in the same round.
- **Stop rules:**
  - an order state unknown for more than 3 s;
  - any reject code not already listed;
  - cumulative realised cost above $100.
- Operator: A. Watcher: D. The team approves in writing before T1.

| Test | When | Orders | Measures | Decides |
|---|---|---|---|---|
| T1. Order lifecycle: 1-lot buy at touch −2% (rests), `GET /orders/open`, `DELETE /orders/{id}`, cancel-all by symbol | pre-storm 10-40 s | 6 | Ack and cancel latency; response schemas | Parsers |
| T2. Marketable round trip: 1-lot buy at the ask, 1-lot sell at the bid, on PORT, HAVEN and ARCH | aftermath 600-700 s | 12 | Fill price vs touch; time to `/positions`; whether our trade prints on the trade channel | Fill model at 1 lot |
| T3. Price protection: 1-lot limits 1%, 3% and 10% through the touch, plus one 1-lot market order, on PORT | aftermath 600-720 s | 8 | Reject, cap, or fill price | The mid ± cap |
| T4. Latency ladder: one 1-lot marketable order every 30 s in 90-540 s, alternating sides | storm | 32 | News → send → ack → `/positions`, p50 / p95 | The +2 s assumption |
| T5. Burst latency: 1-lot PORT at te + 0.3 s after the 570-s and 740-s reports, closed 20 s later | reports | 8 | Ack p95 under a 116-frames-per-second burst | S2/S1 gate; S1-r3 sweep |
| T6. Formation fills: 1-lot PORT buy at 52 s (capped at 48-s mid + 0.5%), sold at 84 s. In another round, 1-lot ARCH when NAV − ETF > 0.5% at 68-80 s, capped at mid × (1 + gap/2), sold at 84 s | 50-90 s | 4 per round | Whether stale levels fill at our price | H1 exit timing; the ETF cap |
| T7. Create/redeem 1 unit (if offered); self-trade (rest a 1-lot buy, send a 1-lot marketable sell at it); limit-room cancel | aftermath | ≤ 6 | P&L at marks; rejects; cancels | Slack Q4 in the playbook |

---

## 8. New Slack questions

These say nothing about the house's reaction timing or its formation behaviour.

1. Our market-data WebSocket dropped five times in an hour with no close frame (connection lifetimes 111-1,159 s). Is there a server-side idle or heartbeat timeout, or a recommended ping interval? Will the live venue use the same gateway?
2. How many concurrent market-data connections may a team or key hold? Can we keep two open for redundancy?
3. Is there a REST endpoint for the current snapshot, or for news since seq N, so a client can resync after a drop?
4. Is the snapshot schema final? Its `advisory` has no `storm`, `seq`, `round` or `type` field. Will live snapshots also come about every 30 s?
5. On the testnet, official advisories differ from the published research feed in `hurricane_conditions`, cone and headline, while storm observations, tracks, wires and reports are identical. Are official advisories regenerated on each run, and will live advisories come from the same generator?
6. Is `trade_id` globally unique and safe to deduplicate on? The last trade per symbol is re-sent after a reconnect. Is there an aggressor-side field or a private fills channel?
7. Market-data timestamps are local wall clock without a time zone, and news `time` is the market clock. Will live use the same conventions? Is there a server-time endpoint with millisecond precision?
8. At each close every book goes empty and quotes carry a null bid and ask. Are resting orders cancelled at the close?
9. Is the testnet house the same pricing model and configuration as the live house: starting prices, spreads, depth and order sizes?
10. Is there an opening auction or restricted period in the first seconds of a round? What happens to a limit order priced far through the touch: is it filled at the touch, capped, or rejected?
11. Is the tick size 0.01 for every symbol, ETFs included?

---

## Appendix: reproduction

| Output | Script | Content |
|---|---|---|
| `verify/v01_feed.out` | `v01_feed.py` | Clock fits, connections, lost and different messages, advisory statistics, snapshot keys |
| `verify/v02_snapshot_bug.out` | `v02_snapshot_bug.py` | Engine reset on snapshot advisories |
| `verify/v03_costs.out` | `v03_costs.py` | Half-spreads, costs by phase and round, RT-form fits |
| `verify/v04_h1.out` | `v04_h1.py` | Formation jump, H1 replay on books vs consensus |
| `verify/v05_reports.out` | `v05_reports.py` | Report reprice timing, S1-r3 sweeper, sanity-check timing |
| `verify/v06_rerun_*.out` | `v06_rerun.py` | Re-backtest reruns |
| `verify/v07_r11_repro.out` | `rebacktest/r11_book_replay.py` | Season-225 book replay |
| `verify/v08_book_vs_truth.out` | `v08_book_vs_truth.py` | Engine, book and consensus accuracy; S3 signal |
| `verify/v09_flow_open.out` | `v09_flow_open.py` | Others' net flow; season-225 open vs packet |
| `verify/v10_gap_path.out` | `v10_gap_path.py` | Book vs consensus by time shift; storm-end paths |
| `verify/v12_s3_book.out` | `v12_s3_book.py` | Independent S3 replay on books |
| `writer/w00_engine_sens_fast.out` | `feed/engine_sens.py` (re-run) | Engine sensitivity to testnet advisories and gaps |
| `writer/w00_snapshot_resync.out` | `feed/snapshot_resync_check.py` (re-run) | Snapshot recovery, engine as is vs patched |
| `writer/w00_doublecount.out` | `feed/doublecount_check.py` (re-run) | Fix part 1 vs parts 1+2 |
| `writer/w01_checks.out` | `writer/w01_checks.py` | A drops; B snapshot recovery; C hygiene; D ladder; E ETF lag; F/F2 ETF lead-lag replay; G follow-on; H drift; I report stale value |
| `writer/w02_sens_holdout.out`, `w02_sens_train.out` | `writer/w02_sens.py` | REC and its sensitivities and marginals |
| `writer/w03_open.out` | `writer/w03_open.py` | Book and large trades right after each open |
| `writer/w04_crowding.out` | `writer/w04_crowding.py` | Playbook crowding check on the books |
| `writer/w05_params.out` | `writer/w05_params.py` | Bot-unit clips, thresholds and exit bands from `cost_model.json` |
| `writer/w06_t50.out` | `writer/w06_t50.py` | Formation half-move times, summarised from `v04` |

---

## Fix verification (added 23 Oct 2026)

All of this ran offline. Nothing touched the network and no order was sent.

**What changed**
- **Engine** (`engine/fairvalue.py`; the original is kept as `engine/fairvalue_orig.py`). Both parts of the §1.3 fix are in:
  - **Part 1:** whatever `_on_snapshot` rebuilds from a snapshot (advisory, obs, track, impacts) carries the snapshot's `storm`, round and type.
  - **Part 2:** `on_message` never ingests an official advisory whose number the round's storm already has. Its seq is still marked as seen.
- **Feed layer** (`bot_skeleton/feed.py`): new `FeedMerger` (the merge logic, no I/O) and `RobustFeed` (N connections, reconnect ladder, phase watchdogs) implement §1.4. The existing classes still work as before. Small additions:
  - `Event` has an optional `conn`, plus read-only `category` and `received_at`;
  - `WebsocketsLibAdapter` accepts `local_addr`, to pin each connection to one network path;
  - `WebsocketsLibAdapter` closes with a 1-s timeout.
- **Check scripts** (`feed/snapshot_resync_check.py`, `doublecount_check.py`, `engine_sens.py`): one optional last argument, the engine file (default `fairvalue.py`). Nothing else changed in them.
- **New test files:**
  - the merge test, `bot_skeleton/test_feed_merge.py`;
  - in `fixcheck/`: `clean_feed_identity.py`, `run_fixcheck.sh` and every output cited below.

**Engine.** The original-engine reruns are byte-identical to the published `w00` outputs, apart from timing lines.

| Check | Original engine | Patched engine | Output (`fixcheck/`) |
|---|---|---|---|
| Each of the 24 s225 official advisories lost, then recovered from the next snapshot | 16 of 24 move a fair value past k·sd + h·P. Worst: GRID 4.99, 11.2× the threshold. Max \|dp\| 0.986 | **22 of 24 match the clean feed exactly** (\|Δμ\| 0.000).<br>**r1 #6: 0.007 (0.02× the threshold).**<br>**r3 #1: AQUA 1.082 (4.63×) in 3 of 46 evaluations.** Its next snapshot came 30.15 s later.<br>Row for row, this is the published fix column | `snapshot_resync_{orig,patched}.out` |
| Snapshot reaches the engine before the live advisory (225 r3 #1) | The classifier is wiped (obs 15 → 1). With part 1 alone, ARCH moves +1.295 | **Identical to the clean order:** \|dp\| 0.0000, every fair value +0.000 | `doublecount_{orig,patched}.out` |
| Season-225 sensitivity (`engine_sens.py`, fast params) | As in `w00 engine` | **T, the recorded feed: unchanged.** No s225 official advisory was lost.<br>**D, gaps plus snapshots:** the r3 s=90 double count goes away (D's snapshot sorts 27 ms before the research advisory):<br>- max \|dp\| 0.014 → 0.002;<br>- RECON 0.135 → 0.067;<br>- S3 cells that differ from R: 9 → 8 | `engine_sens_fast_{orig,patched}.out` |
| **Clean feed: research seasons 401-405, through both engines** | – | **Identical: 0 of 2,340 evaluations × 12 symbols differ.**<br>- Mean, sd and pattern posterior are compared with `==`, every 10 s from 0 to 770 s of each round.<br>- Both parameter files were run: `engine_params.json` with library 1-400, and the fast params with library 226-600.<br>- Classifier state and return value are identical after every message | `clean_feed_identity.out` |
| Clean s225 feed plus all 96 recorded snapshots, each placed after its `last_seq` | – | **Identical** (2 × 234 evaluations) | same |
| Negative control: the stream above minus official r1 #1, so it arrives only in the next snapshot | – | The engines differ in 75 of 234 evaluations.<br>- The original engine differs from the full stream in 9 evaluations. Worst: ARCH 281.24 against 308.54 at r1 s=90.<br>- **The patched engine equals the full stream in all 234** | same |

**Feed** (`test_feed_merge.py`).

How the test simulates two connections:
- The recording is the server stream. Connections A and B each lose every frame inside their own drop windows: one drop per 6 min on average (twice the 23 Oct rate); 85% last 0.3-0.85 s and 15% last 3-35 s.
- After each drop, the server's resend burst is added: books, quotes, and the last trade again.
- Both copies get latency jitter, and 4 stalls of 0.2-1.5 s each, so they overtake each other.
- Two scenarios, 20 seeds each:
  - **Independent:** A and B drop at independent times.
  - **Half shared:** half of B's drops are copies of A's, the server-side case.

| Check | Result |
|---|---|
| **No gaps where either copy had the message** | **40 of 40 runs pass.**<br>- Every news (season, seq) that A or B received is forwarded exactly once, with the recorded content.<br>- Seq'd news lost per run, one connection against merged: 33.7 → **24.0** (independent). The 24.0 is exactly the 24 lost on 23 Oct before the split.<br>- Half shared: 33.6 → 28.2. The extra 4.2 were dropped by both copies |
| Season change | 2 resets per run (224 → 225 → 226), both triggered by the `last_seq` −1 snapshot. Without them, s225's seqs would collide with s224's |
| Snapshots | 5 of about 243 copies are forwarded per run, each covering a hole:<br>- `last_seq` 695: the start of the recording;<br>- 764: s224 advisory #5;<br>- 250, 416 and 828: s225 drops 3, 4 and 5.<br>Every lost seq is covered whenever a later snapshot exists. None is forwarded twice |
| The 19-ms race (225 r3 #1) | A snapshot that arrives before the other connection's live advisory is held 1 s, then dropped. With one connection it is forwarded |
| Trades, books and quotes | About 10,000 duplicate trades and 32,000 stale book and quote copies are dropped per run. Every `trade_id` is forwarded once. Per symbol, forwarded timestamps strictly increase, and the final book is the newest |
| Outage alarm | Every merged storm silence over 10 s raises `feed_outage`, then `feed_restored`. Nothing fires outside the storm. The recorded 31.7-s outage fires in every run |
| `RobustFeed` with fake sockets | After a drop, the connection is back in 0.25 s. A stall trips the 3-s storm watchdog. Each session sends exactly one subscription. The merged news equals the 35 seqs streamed.<br>Consumer API: `events()` and an `on_event` callback see the same events in the same order, and the iterator ends after `stop()`. Every event carries its category, raw dict, `received_at` and (news only) seq |
| Ladder; mutations | Delays: 0.25 s, then 0.375-0.625 s, never above 2.0 s.<br>Mutations the test catches:<br>- no seen-set reset;<br>- the literal `last_seq` rule (below);<br>- no 1-s hold |

`replay.py --chaos` (seasons 401-420) still passes 20 of 20 with the new `feed.py`.

**For consumers: what to call**

*Engine* (`engine/fairvalue.py`):
- **Public API unchanged.** `SeasonState`, `fair_values`, `set_adjustment` and the rest keep their names and signatures. The only addition is the private `_official_known`.
- **One return value changed:** `on_message` now returns `False` for an official advisory whose number is already known.

| Call | Use |
|---|---|
| `spec = importlib.util.spec_from_file_location("se_engine", "<systematic>/engine/fairvalue.py"); E = importlib.util.module_from_spec(spec); spec.loader.exec_module(E)` | Import by file path. `bot_skeleton/` has its own, different `fairvalue.py`, so a bare `from fairvalue import ...` picks whichever directory comes first on `sys.path` |
| `st = E.SeasonState(params="engine_params_fast.json", library_seasons=(226, 600))` | One per season; this is the §2 bot configuration. The defaults are `engine_params.json` and its library. **A relative `params` path is resolved against `engine/`, not the working directory**, so pass a bare file name or an absolute path |
| `st.on_message(ev.msg)` | Every **news** and **snapshot** event. The snapshot fix lives in here. Returns `True` if the message was new |
| `E.fair_values(st)` | Returns `{symbol: (mean, sd)}` for the 10 stocks and 2 ETFs |
| `st.set_adjustment(symbol, mult=1.0, add=0.0)` | Manual adjustments |

Checked end to end: the recording, run through `FeedMerger`, fed news and snapshot events to `E.SeasonState`. A `season_reset` created a new `SeasonState`, and the engine returned 12 fair values in 225 r2.

*Feed* (`bot_skeleton/feed.py`; needs `websockets` from `systematic/.venv` unless you pass your own adapter):
- **Construct:**
  ```
  feed.RobustFeed(url, headers, symbols, channels, queue=None, recorder=None, adapter_factory=None,
                  n_conn=2, urls=None, merger=None, phase_fn=None, connect_timeout=6.0, poll_s=0.1,
                  rng=None, log=print, on_event=None, user_agent=...)
  ```
  The class docstring explains every argument. For example, `adapter_factory=[f_wifi, f_hotspot]` with `feed.WebsocketsLibAdapter(feed.SendGuard(5), ua, local_addr=(ip, 0))` puts each connection on its own network path.
- **Receive, in one of three ways:**
  - `async for ev in rf.events():` starts the feed, and ends after `rf.stop()` once the queue is drained;
  - `on_event=callback(ev)`, called synchronously and in order;
  - `await rf.run()` while reading `rf.queue`.
- **Event fields:**
  - `ev.category`: `"news"`, `"snapshot"`, `"orderbook"`, `"quote"`, `"trade"` or `"status"`;
  - `ev.kind`: the fine type (the news `type`, `"md_orderbook"`, or a status kind);
  - `ev.msg`: the raw dict. That is the news message itself for news and snapshots, and the whole frame for market data;
  - `ev.received_at` (unix seconds), `ev.seq` (news only) and `ev.conn`.
- **Status kinds to handle:**
  - `season_reset`: create a new `SeasonState`. The merger has already reset itself, so do not call `rf.merger.reset()`;
  - `ws_connected` and `ws_disconnected`;
  - `feed_outage` and `feed_restored`: a storm gap over 10 s, after which S1-r3 uses the wide ε.
- **Control:** `rf.stop()`, `await rf.force_reconnect(k=None)`, `rf.connected`, `rf.up`, `rf.connects`, `rf.watchdog_trips`, `rf.merger.outages`, `rf.merger.gaps`, `rf.merger.stats`.
- **Offline replay without sockets:** `m = feed.FeedMerger()`, then `m.ingest(conn, frame, recv_ts)` and `m.poll(now, phase=None)`. Both return lists of `Event`.
- **Constants:** `feed.reconnect_delay(attempt)`, `feed.watchdog_s(phase)`, `feed.SNAPSHOT_HOLD_S`, `feed.OUTAGE_ALERT_S`.

**Choices and limits**
- **"Forward a snapshot only if `last_seq` is new"** is implemented as `last_seq` above the contiguous high-water mark: the highest seq such that every seq up to it was forwarded, or covered by an earlier snapshot.
  - The literal rule, "above the highest seq forwarded", never recovers anything on one connection, because seqs up to `last_seq` always arrive before the snapshot.
  - Example: in drop 1, seqs 748-764 came before the snapshot with `last_seq` 764, so advisory #5 would stay lost.
- **Not built:** wires lost in a drop stay lost. Drop 4 lost 3 of them, and the snapshot carries them only as headlines (§1.4 step 3).
- **Not tested:**
  - `RobustFeed` has run only against fake sockets.
  - `main.py` still uses `FeedClient`. To switch, pass `phase_fn=lambda: self.state.phase`, and do not call `merger.reset()` on the merger's own `season_reset`.
  - Whether drops are server-side (§7 #5) is still open.

**Reproduce** (about 4 minutes, from `systematic/`):

```
python3 -I -m py_compile engine/fairvalue.py engine/fairvalue_orig.py bot_skeleton/feed.py bot_skeleton/test_feed_merge.py
sh testnet/analysis/fixcheck/run_fixcheck.sh "$PWD/testnet/recordings/testnet_s224_s225.jsonl" ~/Downloads/hurricane-season-research/history
```

Individually, from `testnet/analysis/fixcheck/`. Here `E` is the absolute path of `engine/`, `REC` is the recording, `HIST` is the history directory, and the last argument picks the engine:

```
python3 -I ../feed/snapshot_resync_check.py REC HIST E fairvalue_orig.py > snapshot_resync_orig.out   # fairvalue.py -> _patched
python3 -I ../feed/doublecount_check.py REC HIST E fairvalue.py > doublecount_patched.out
python3 -I ../feed/engine_sens.py REC HIST E engine_params_fast.json 226 600 engine_sens_fast_patched.json fairvalue.py > engine_sens_fast_patched.out
python3 -I clean_feed_identity.py HIST E REC 401 405 > clean_feed_identity.out
python3 -I ../../../bot_skeleton/test_feed_merge.py REC > feed_merge_test.out
```
