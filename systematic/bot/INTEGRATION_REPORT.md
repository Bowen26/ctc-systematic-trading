# ctcbot integration report

Integrator pass over groups A-E, 23 Oct 2026. Everything ran offline. Nothing opened a socket, `~/.ctc_bot_key` was never read, and no order reached a real exchange. Writes went only under `bot/`, apart from analysis scripts in the session scratchpad. The dry-run default and the `--live` + `LiveToken` gate are unchanged.

`SYS` = `/Users/bowentian/Documents/Fun Stuff/CTC2026/systematic`; `BOT` = `SYS/bot`; `PY` = `SYS/.venv/bin/python` (3.14.8); `SP` = the session scratchpad named in DESIGN.md §9.0.

## Summary

- **Builds and tests.** All 99 Python files compile. The default unit suite passes: 604 tests, 0 failures, 8 slow tests skipped. The slow acceptance run (`CTCBOT_SLOW=1 CTCBOT_REQUIRE_DATA=1`) has 1 failure in 604 tests: the §9.3 parity action count (§7).
- **Engine probe.** It passes on the patched `engine/fairvalue.py` and fails on `fairvalue_orig.py`, with both parameter files.
- **Feed.** On 21 streams, the bot's `FeedNormalizer` and the fix session's `FeedMerger` forward exactly the same news, relevant snapshots, trades and book/quote frames, and reset the season at the same frames. The streams are the recording plus 20 simulated two-connection runs. The remaining differences are deliberate design choices (§4).
- **End-to-end dry run** (`run_bot.py --replay`, real shell, thread-mode engine, offline SimExchange). It processed all 44,296 frames with 0 exceptions. It made 3,087 order actions with at most 147 in any 61 s, broke no exchange rule (0 limit, rate or resting violations) and left 0 live orders. It handled 5 reconnects and 2 season changes, each counted once.
- **Six integration fixes**, recorded in DESIGN.md §12. Two of them changed results:
  - **Fill-sanity false positives.** Before the fix, the breaker disabled 8-11 of 12 symbols and so blocked S1-r3.
  - **Wasted remainder cancels in the harness.** In season 401 these were 227 of 504 cancels.

  With them the default-config research mean is **16,350**; E reported 10,896 before them. In season 401 alone, the fixes take it from 12,172 to 31,061.
- **REC parity.** On 401-440 the parity run reproduces REC per season: mean **19,668** against 19,660, r 0.996, median |diff| 744. The per-strategy split agrees within 200 per strategy. **One §9.3 criterion still fails: actions per season** (1,771 against 1,227 ± 20%). The cause is in the design, not the code: the bot's clips are limit orders, and it cancels their unfilled remainders, while r_sim fills everything and cancels nothing (§6.1).

## 1. Build and unit tests

| Command (from `BOT`) | Result |
|---|---|
| `PY -I SP/integ/compile_all.py BOT` (`compile()` on every `.py`, no bytecode written) | 99 files, 0 failures |
| `PY -m unittest discover -s tests -v` | **604 tests, OK, 8 skipped (all SLOW)**, 47.6 s. Before my changes: 602, OK |
| `PY -I run_bot.py --self-test` | exit 0. All of these pass: config, research case, engine snapshot-fix probe, cost model ("matches the config tables"), laws module, `diff_case` clean |
| `PY -I run_bot.py` (no host) | exit 2, "host mode needs --host" |

**Interfaces.** All of these calls match:
- every cross-group call in DESIGN.md §4.F;
- the undocumented extras the groups reported, such as `EngineAdapter(snapshot_fix=)`, `BotCore.set_engine_listener`, D's `ClockedExchange` and `SimExchange.clock`;
- the cli's signature-matched construction of E's market classes.

No call was broken. The problems were behavioural, between groups, and are fixed below.

## 2. Integration changes (all recorded in DESIGN.md §12)

| # | Problem (who saw it) | Fix | Files |
|---|---|---|---|
| 1 | **Fill sanity disabled most symbols** (B, E). The expected clip cost was the book walk alone, but our own consecutive clips add follow-on and impact, about 25 bp per L/4. Real and simulated clip costs therefore exceeded 3× the walk all the time. In research season 401, 59 misses disabled 8 symbols. On the 225 books, 11 of 12 were disabled, so S1-r3 placed only REIN in 225 r3 | `expected_cost_bp = walk + fo_allow` (36 bp stocks, 17.28 bp ETFs), the per-clip cost already inside the S3 threshold. It is applied in H1, S3 and flatten through the new `strategies/base.expected_clip_cost_bp`. **After the fix:** 0 misses and 0 disabled symbols across all 40 default seasons and on the 225 books. S1-r3 places all 12 symbols in 225 r3. The H1 round-1 cost ratio stays on the tables alone, as specified (H1 subtracts `fo_allow` again); see gap G3 | `ctcbot/strategies/base.py`, `h1.py`, `s3.py`, `flatten.py`, `tests/test_c_h1.py` |
| 2 | **Wasted remainder cancels in the harness** (E, B). Frames ran before jobs at the same instant. So the news decision at 2j saw the clip sent at 2(j−1) at its 2-s ttl before the 2j reconcile had marked it FILLED, and cancelled an order that had already filled. The cancel got a 404 and wasted an action. DESIGN §4.E's own worked example assumed the opposite order | The harness now runs due results and jobs before the frames of the same instant. **Result:** 404 cancels went from 227 to 0 in season 401 and are 0 in all 40 default seasons. The production shell has no defined order between job loops and frames, so it is unaffected | `sim/harness.py`, `tests/test_e_harness.py` |
| 3 | **Pause watchdog** (A raised it; the replay confirmed it). In a pre-season pause the clock reads pre_storm (frozen at 09:30:00) with `running` false, and frames come once every ~30 s. The 8-s pre_storm watchdog declared the feed down every 30 s: 8 false `feed_down` cancel-alls and alerts in the replay. In host mode it would also have reconnected both websockets every 8 s | While the clock is known and not running, `watchdog_s()` uses the pause value, "closed" (35 s). **Replay after the fix:** 1 `feed_down`, the real 31.7-s outage in 225 r2 | `ctcbot/app/core.py`, `tests/test_d_core.py` (new test) |
| 4 | A replay could not be checked against the exchange rules independently of the bot's own logs | `--replay` and `--sim` now write `run_dir/sim_exchange.json`: violations, rate windows, resting maxima, live orders left and P&L | `ctcbot/app/cli.py` |
| 5 | **Parity profile and fill sanity.** REC has no fill-sanity breaker. Under the parity caps (8% from mid), research fills after news land 130-310 bp from the decision mid. The breaker disabled 1.7 symbols per season and cost about 2.0k per season | `config/rec_parity.json` adds `risk.fill_sanity_n` 1000000. It is a sim-only profile (`live_allowed` false) | `config/rec_parity.json`, `tests/test_e_research.py` |
| 6 | **`--sim` never exited.** `SimSource` waited for `stop()` after the last frame, so a scripted `run_bot.py --sim research:NNN` ran on with no market until it was killed | `SimSource(tail_s=2.0)` returns 2 virtual seconds after the last frame, as `RecordingSource` does. The shell then stops through `sources_done` and the shutdown cancel | `sim/exchange.py`, `tests/test_e_exchange.py` (new test), `ctcbot/app/shell.py` (docstring) |

DESIGN.md was also edited where these touch the spec:
- §4.B: the fill-sanity row;
- §4.D: `watchdog_s()`;
- §4.E: harness step order and the `SimSource` signature;
- §6.1, §6.2: `expected_cost_bp`;
- §9.3: the parity profile list;
- new §12.

## 3. Engine probe (snapshot fix)

Run with `PY -I SP/integ/probe_check.py`, which calls C's `snapshot_fix_present` the way `cli.probe_engine` does:

| Engine | `engine_params_fast.json` | `engine_params.json` |
|---|---|---|
| `engine/fairvalue.py` (patched) | **True** (1.9 s) | True |
| `engine/fairvalue_orig.py` | **False** (1.5 s) | False |

No `__pycache__` was written under `engine/`; the existing one is dated 12:11-14:09, before the build.

## 4. Feed merge against the fix session's `FeedMerger`

Run with `PY -I SP/integ/feed_compare.py 10`. Both mergers get identical frames in arrival order, in the core's order: ingest a frame, then poll at its time. The simulated connections come from the skeleton test's generator, as copied into `tests/test_a_feed_merge.py`.

| Stream | Seq'd news | Relevant snapshots | Trades | Book+quote events | Season resets |
|---|---|---|---|---|---|
| Recording, one connection | 1,037 = 1,037, same order | [695, 764, 250, 416, 828], both | 9,873 = 9,873, same order | 33,098 = 33,098 | same two frames (6156, 43714) |
| 10 "independent" plus 10 "half shared" two-connection runs | identical in 20 of 20 (1,024-1,037 per run) | identical in 20 of 20 (5-7 per run, e.g. +506, +650, +90/+108 when both copies dropped) | identical sets in 20 of 20 | identical in 20 of 20 | identical in 20 of 20 |

**Deliberate differences**, all specified in DESIGN.md, none of which shows on these streams:
- **Provisional season resets.** Seq-restart, round-decrease and date-change triggers are provisional until a −1 snapshot, a comp_id change or a consistent snapshot confirms them; the skeleton resets at once. The −1-snapshot trigger that fired here is immediate in both.
- **Duplicates** are keyed by seq plus a fingerprint (type, round, storm, time), and announcements by headline (§11 item 2). The skeleton uses seq alone.
- **Book and quote high-water timestamps** are per epoch. The skeleton keeps them across a reset; server stamps are wall-clock, so this makes no difference.
- **Trade-id LRU** is 50,000 entries against 200,000.
- **Outage alarm.** The skeleton's `feed_outage` event is replaced by the core's FeedHealth, the 10-s `feed_down` hold and StormCoverage's gapped rounds.

## 5. Offline end-to-end dry run: `run_bot.py --replay`

```
printf 'lawok all\n' | PY -I run_bot.py --replay ../testnet/recordings/testnet_s224_s225.jsonl --replay-speed 10 \
    --run-dir runs/integ_replay_x10 --fresh --history SP/data/hurricane-season-research/history \
    --set 'engine.library_seasons=[226,600]'
PY -I SP/integ/analyze_replay.py runs/integ_replay_x10 10
```

**Setup**
- The real `cli` → `shell` → `BotCore` path ran with the thread-mode engine (fast params, out-of-sample library [226, 600]), the real `RestExecutor` workers and `RecordingSource` on a `ScaledTime(10)`.
- REST went to the offline `SimExchange` (recorded mode, `fills=False`), so orders rest and never fill.
- No `LiveToken` exists, so nothing could be sent. `lawok all` answers the 224 late join, as in §9.2.
- Wall time was 6.3 min; exit 0 by `sources_done`.

| Requirement | Result |
|---|---|
| Whole recording, no exceptions | 44,296 frames, all processed (`replay_end`; 44,308 including connection controls), 0 exceptions, 17,017 decision cycles |
| Intents logged | 973 intent sets logged (identical consecutive sets are logged once, then every 40th repeat), 1,571 PLACEs (table below) |
| Position limits, resting orders included | **Exchange:** 0 `limit_shrink`, 0 `limit_room_cancel`, 0 `self_trade`, at most 2 resting orders per symbol. **Bot log** (conservative: an order counts until it is resolved): position plus same-side live orders per symbol and side never exceeds L. The maximum equals L exactly, for example S1-r3's AQUA buy rests at 500/500 |
| 150/min budget | Exchange: at most 147 bot actions in any 61 s (146 in any 60 s), 0 HTTP 429. The limiter denied 9,189 TAKE and 561 HEDGE ticket requests, of which 300 were logged as `budget_denied` skips (rate-limited log); it never overdrew |
| 5 reconnect boundaries | 5 down/up pairs (rec#1 → rec#6, at 111, 856, 1,284, 1,985 and 3,175 s); `replay_end` reports 5 reconnects |
| Season change | 2 confirmed resets (−1 snapshot, 224→225 at 683 s, 225→226 at 3,628 s), 0 provisional. Each comp_id change came within 0.3-0.9 s and was merged, so each change counts once. 2 engine resets, each with its own engine log |
| Engine snapshots | Exactly the relevant snapshots 695, 764, 250, 416 and 828 |
| Rounds | `round_start` (epoch, round): (0,3), (1,1), (1,2), (1,3), (2,1) |
| End state | 0 live orders on the exchange (13 CANCEL_ACKED records were left in the bot's log after the final shutdown cancel-all, since no reconcile runs after it). Loop lag max 34 ms |

**Intent items by round and strategy** (items in logged intent sets), with orders placed in brackets:

| Round | s3 | h1 | s1r3 | rein_ask | flatten |
|---|---|---|---|---|---|
| 224 r3 (joined at t ≈ 220 s) | 846 (341) | – | 25 (12) | 65 (2) | – |
| 225 r1 | 522 (428) | 33 (44) | – | 84 (2) | 5 (0) |
| 225 r2 | 457 (215) | 33 (44) | – | 76 (4) | 3 (0) |
| 225 r3 | 1,360 (396) | 33 (44) | 49 (24) | 74 (2) | – |
| 226 r1 (ends at t ≈ 50 s) | – | 11 (11) | – | 1 (2) | – |
| **Logged intent sets containing the strategy** | 911 | 10 | 4 | 300 | 6 |

- **S1-r3** fired at t = 740.9 s in 224 r3 and 740.9 s in 225 r3, on `seq_hi = final_seq` (876 and 865).
  - **224 r3:** it placed only AQUA, HAVEN, ISLE, MAT, RECON and REIN. The six closure names were gated with `e:law_value_none`, because rounds 1-2 were missing after the late join.
  - **225 r3:** it placed all 12 symbols.
- **Flatten** placed nothing, because nothing fills in a dry run.

**Warnings (alerts and rejects)**
- **`h1_fallback` ×1.** At the 224 late join, the engine had not ingested that round's storm by the 76-s deadline. H1 held nothing, so it was harmless.
- **`h1_check_failed` ×1.** Expected in a dry run: 0 fills, so rounds 2-3 used `size_on_fail`.
- **`feed_down` ×1.** The real 31.7-s outage in 225 r2: one SNIPE cancel-all, held 37.5 s, released after a snapshot plus storm observations.
  - The first replay, before fix 3, had 9 `feed_down` alerts. 8 of them were false, in the two pre-season pauses.
- **`risk_reject`:**
  - 4 `marketable_far`: S3 sells of HAVEN and PORT more than 1.5% from mid, which the guard blocked as intended;
  - 5 `hold:feed_down`.
- **`cancel_404`:** 0. The first run had 10.

`run_bot.py --sim research:401 --sim-speed 25` (shell mode, cached fair values) also ran end to end after fix 6:
- exit 0 by `sources_done`, 0 exceptions;
- 0 violations, 0 live orders at the end, at most 148 actions in any 61 s;
- P&L 30,695, against 31,061 for the same season under the virtual-time harness.

Shell mode shows 118 `cancel_404`. That is the research fill model's 2-s row delay meeting real-time ttl timing; on a real exchange a marketable clip fills at once.

## 6. Simulation

### 6.1 Research seasons 401-440, cost-model fills, engine caches (§9.3 allows `--fv cache`)

```
PY -I sim/run_research.py --seasons 401-440 --profile config/rec_parity.json --fv cache --h1-off --out sim/out/integ_parity_nosanity --set risk.fill_sanity_n=1000000
PY -I sim/run_research.py --seasons 401-440 --profile config/rec_parity.json --fv cache --h1-off --out sim/out/integ_parity   # before fix 5
PY -I sim/run_research.py --seasons 401-440 --fv cache --ablations --out sim/out/integ_default
PY -I sim/compare_rec.py --seasons 401-440 --report sim/out/integ_parity_nosanity/report.json
```

The `--set` on the first run is identical to the current `rec_parity.json`.

`compare_rec.py` recomputes REC with `r_sim` (read-only) at **19,660**, which reproduces the DESIGN.md baseline list exactly.

| Run | Mean | p5 | Hit | R1 / R2 / R3 | Acts (places + cancels) | Paired vs REC | §9.3 |
|---|---|---|---|---|---|---|---|
| REC (r_sim) | 19,660 | 2,072 | 0.97 | 6,376 / 6,524 / 6,760 | 1,227 (fills only) | – | – |
| **Parity** (current `rec_parity.json`) | **19,668** | 3,317 | 0.95 | 6,277 / 6,352 / 7,040 | 1,771 (1,465 + 306) | **r 0.996, median \|d\| 744**, mean d +8 (se 190) | mean, r, median, H1+S3+flatten (19,496 against 19,670 ± 12%), max 150 per 60 s, 0 violations: all PASS. **Actions: FAIL** |
| Parity, H1 off (diagnostic) | 14,281 | 2,949 | 0.97 | | 1,196 | | PASS (14,017 ± 10%) |
| Parity with the breaker (before fix 5) | 17,658 | 547 | 0.95 | | 1,648 | r 0.974, median 2,148 | mean FAIL by 36, median FAIL |
| **Default config** | **16,350** | 5,402 | 1.00 | 5,290 / 5,329 / 5,731 | 1,883 (1,322 + 561) | r 0.917, median 3,349 | all default checks PASS: p5, hit, 0 violations, max 146 per 61 s, mean ≥ 11,796 |
| `abl_edge` | 16,567 | 4,713 | 1.00 | | 1,883 | | PASS, in 13,762-21,626 |

For comparison, E's runs before the integration fixes were: default 10,896, which failed the floor, and `abl_edge` 10,519, which failed its band.

**Per-strategy split** (mean P&L to settlement per season; REC's S3x is the automatic 540-568 s flatten):

| | H1 | S3 | Flatten (S3x) | S1-r3 (S1) | REIN ask | H1+S3+flatten |
|---|---|---|---|---|---|---|
| REC | −5,723 | 27,626 | −2,233 | −9 | – | 19,670 |
| Bot, parity | −5,745 | 27,652 | −2,411 | +172 | – (off) | 19,496 |
| Bot, default | −2,239 | 20,360 | −2,008 | +173 | +64 | 16,113 |

**Why parity differs from REC.** Season by season the gap stays within ±2,850, and the worst seasons are 413 (+2,838) and 403 (+2,666). The per-season table is in `SP/integ/final_tables.md`. The causes are the ones §9.3 expects:
- **S1-r3 against REC's S1 taker:** +181. It is the only systematic gap, which is why R3 is +280.
- **Clip timing.** The bot sends a clip 2 s after its last send and only when no clip is live; REC caps per 2-s row.
- **Edge ordering under the 150 cap.** max60 is 150 in both.
- **Limit orders against unlimited fills.** The bot stops filling at its limit's marginal price; r_sim fills the whole clip at any price. This also explains flatten's −178.

**Actions criterion (still failing).**
- REC's 1,227 counts fills. r_sim's clips have no limit price and leave no remainder, so r_sim never cancels.
- Every bot clip is a limit order, and the unfilled remainder is cancelled at ttl: 306 cancels per season, all S3 ttl remainders, with 0 404s.
- That remainder is then re-clipped, so places are also higher: 1,465, +19.4%.
- No wasted actions are left after fix 2. The criterion cannot pass while cancels count and fills are limit-bound. It needs an architect decision: count places only (+19.4%, just inside ±20%), or widen the band.

**Why default differs from parity (−3,318).**
- The six one-change overlays together explain only +892. The residual of +2,418 comes from interactions: parity changes all of them together, and its S3 uses `limit_cost_frac` 0.0, where `abl_edge` uses 0.5.
- Default makes 561 cancels per season. They come from `require_marketable`, the 1.5% caps and touch-side limits leaving remainders.
- `abl_caps` disables 0.95 symbols per season through fill sanity, because its caps are the parity caps (8%). That does not happen under the default caps.

### 6.2 Recorded books, season 225, against TESTNET_FINDINGS §5

```
PY -I sim/run_recording.py --mode book --command "1:lawok all" [--profile config/rec_parity.json] [--set strategies.h1.enabled=false] --out sim/out/integ_rec_book...
```

The bot runs here use the real engine inline (fast params, library [226, 600]) and recorded-book fills with 0.3 s latency. The §5 rows are r11's REOPT on the same books, also with 0.3 s latency. Each cell is the 225 total, then the split by strategy.

| Configuration | 225 total | R1 / R2 / R3 | H1 | S3 + flatten (S3+S3x) | S1-r3 |
|---|---|---|---|---|---|
| **§5 REOPT, books, 0.3 s** | **23,210** | 10,440 / 5,973 / 6,797 | 48,406 | −25,372 | – |
| Bot, parity profile | 15,143 | 8,387 / 4,081 / 2,674 | 43,815 | −29,310 | 638 |
| Bot, default config | 11,739 | 3,985 / 2,558 / 5,196 | 27,824 | −16,721 | 631 |
| **§5 REOPT without H1, books** | **−3,790** | −779 / −9,773 / 6,763 | 0 | −3,966 | – |
| Bot, parity profile, H1 off | −5,095 | −1,920 / −8,193 / 5,019 | 0 | −5,733 | 638 |
| Bot, default, H1 off | −558 | −1,350 / −5,663 / 6,455 | 0 | −1,193 | 631 |

- **Without H1 the bot matches §5.** Parity without H1 is −5.1k against REOPT's −3.8k, and both have the same shape: r2 the worst round, r3 positive.
  - The default rules (touch edge, `require_marketable`, 1.5% caps) lose much less, −0.6k, in line with §9.2's "S3 alone lost −3.8k / −2.2k".
- **With H1 the bot earns less than REOPT** (15.1k against 23.2k), but the gap is almost all H1 (43.8k against 48.4k). S3 + flatten is close (−29.3k against −25.4k).
  - r11's REOPT enters H1 in L/8 clips from 42 s. The bot (REC rule) enters in L/4 clips at 50-56 s, later and at higher prices, in a season that drifted the H1 way (§5: "not a validation").
- **The default config is lower again**, mainly because of H1 sizing:
  - H1 is at half size in round 1;
  - the walk-only cost ratio dropped the rebuilders for rounds 2-3: 225 r2 went from 6,965 to 2,558 when fix 1's ratio change was reverted, the only code difference between those runs (gap G3).
- **S1-r3 earns +631 in 225 r3** after fix 1; before it, it earned 0 (REIN only). §9.2 references the sweeper's ≥ $927.
- The S3 attribution of −30.4k in the default run trips the non-blocking ±15k flag. Most of it is S3 selling inherited H1 inventory below settlement; the H1 + S3 + flatten sum is +11.1k.
- The parity profile in book mode shows up to 160 actions per 61 s. That is expected: the profile sets `rate.window_s` 60, and the 61-s check belongs to the default config.

### 6.3 §9.2 recording acceptance (default config)

`PY -I sim/run_recording.py --mode dryrun|book --command "1:lawok all" --out sim/out/integ_rec_{dryrun,book}`: **every blocking check passes in both modes.**

| Check | Dryrun | Book |
|---|---|---|
| Exceptions, violations, HTTP 429s | 0 | 0 |
| Max bot actions per 61 s | 146 | 142 |
| Max resting orders per symbol | ≤ 2 | ≤ 4 |
| Every order terminal at the end | yes | yes |
| Confirmed season resets (provisional) | 2 (0) | 2 (0) |
| Reconnect pairs | 5 | 5 |
| Relevant snapshots to the engine | 695, 764, 250, 416, 828 | same |
| Coverage | as specified | as specified |
| S1-r3 fired | 224 r3 and 225 r3, same cycle as the first fv with `seq_hi ≥ final_seq` | same |
| S1-r3 symbols in 224 r3 | AQUA, HAVEN, ISLE, MAT, RECON, REIN | same |
| S1-r3 symbols in 225 r3 | all 12 | all 12 |
| Marketable orders in a round's first 3 s | none | none |
| Flat at 568 s in 225 r1 and r2 | – | yes, apart from a logged `not_flat` for FERRY (−98) in r1 |

Book mode P&L: 224: 2,632; 225: 11,739. S3 in 225 is −30.4k, which raises only the non-blocking ±15k flag.

## 7. Slow acceptance suite

`CTCBOT_SLOW=1 CTCBOT_REQUIRE_DATA=1 PY -m unittest discover -s tests -v`: **604 tests in 951 s, 1 failure, 0 skipped.**

**The one failure is `test_e_research.TestAcceptance93.test_parity_run`.** Every parity criterion passes except actions per season, 1,770.8 (gap G1). Its parity numbers are identical to §6.1: mean 19,668, r 0.996, median |diff| 744.

**Everything else passes, including:**
- the B property sweep (`test_more_seeds`);
- C's real-engine reproduction of `fv_holdout_401.json`;
- **D latency:** 24 S1-r3 PLACEs, p95 = max = 32.6 ms (limits 300 and 500 ms); loop-lag p99 2.4 ms; Python 3.14.8 with the GIL;
- E harness determinism;
- both §9.2 recording runs;
- the §9.3 default run with the ablations.

## 8. Known gaps and decisions for the team or architect

- **G1. §9.3 action count fails by design** (§6.1): 1,771 against 1,227 ± 20%. Places alone are +19.4%. The criterion should count places only or carry a wider band.
- **G2. The S3 edge rule (§10) still needs sign-off.** The default (touch rule) gives 16,350; `abl_edge` (mid rule) adds only +217 now. The full REC parameter set, which is sim-only because of the 8% caps, gives 19,668. Most of the gap is interactions (§6.1), not the edge rule alone.
- **G3. Calibrate `drop_rebuilders_cost_ratio`.** As specified, the ratio is realized cost over the walk tables. At base sim cost that ratio is already 1.05-1.72 (median 1.39). So the rebuilders are dropped in 6 of 40 research seasons and in 225 on the books, although the findings say to keep them at base cost.
  - Measured against walk + `fo_allow` (fix 1's expected cost), the ratio would be about 0.5 at base cost.
  - I kept the specified behaviour. The team should pick either a threshold of about 0.75 on the new basis or the current rule.
- **G4. Fill sanity under wide caps.** With the parity or `abl_caps` caps (8% from mid), the breaker still disables about 1 symbol per season on research fills that land after a consensus jump. Under the live caps (1.5%) it disabled nothing in 40 seasons or on the books. It stays fully active live.
- **G5. Remainder cancels use budget live.** Default makes 561 cancels per season in the research sim, all ttl remainders. They are real actions under the 150/min cap; `budget_denied` shows up as soon as S3 is busy. This is not a defect, but it could matter on competition day.
- **G6. Shell-mode `--sim` shows 404 cancels** (§5). This is a research-fill-model artefact (the row delay), absent on books and live.
- **G7. The pre-existing `tests/_tmp` debris** (1,138 entries from the groups' runs) and the old E/B outputs in `sim/out/` were left in place. I removed only what I created.

## 9. Files changed by the integrator

All under `BOT`:
- `DESIGN.md`: §4.B, §4.D, §4.E, §6.1, §6.2, §9.3 and the new §12;
- `ctcbot/strategies/base.py`, `h1.py`, `s3.py`, `flatten.py`;
- `ctcbot/app/core.py`, `cli.py`, `shell.py` (docstring);
- `sim/harness.py`, `sim/exchange.py`;
- `config/rec_parity.json`;
- `tests/test_c_h1.py`, `test_d_core.py`, `test_e_harness.py`, `test_e_exchange.py`, `test_e_research.py`;
- this report.

Run outputs are in `runs/integ_replay_x10`, `runs/integ_sim401` and `sim/out/integ_*`. Analysis scripts are in `SP/integ/`: `compile_all.py`, `probe_check.py`, `feed_compare.py`, `analyze_replay.py`, `diag_season.py`, `slip_decomp.py`, `compare_tables.py`.
