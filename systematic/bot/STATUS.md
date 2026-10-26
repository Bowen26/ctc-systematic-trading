# ctcbot status (2026-10-24: competition-day readiness; hardening pass 2026-10-23)

The bot is built, integrated and hardened. It has never sent an order: every run so far was offline or a
read-only dry run, and the dry-run default and the `--live` + typed-phrase `LiveToken` gate are unchanged (the gate
now also checks the live bounds of the safety settings). It is **not cleared for live trading**: the team must
first approve and run the 1-lot test plan (TESTNET_FINDINGS §7), or decide on competition day to go live without it.

- **Competition day: `COMPETITION_DAY.md`** (printable: `COMPETITION_DAY.pdf`, 3 pages).
- How to operate it: `RUNBOOK.md`.
- The design: `DESIGN.md` revision 2, plus §12 (the integration changes), §13 (the hardening changes) and §14
  (the 23-24 Oct strategy updates).
- The integration evidence: `INTEGRATION_REPORT.md`.

## 0. What changed on 24 Oct

- **S3 start: tried 120 s overnight, reverted to the original 80 s** (`strategies.s3.start = ["storm_start", 20]`).
  Over 10 recorded testnet seasons the 120-s start was a wash (+$16.0k on the first five, −$13.0k on the five
  overnight seasons 242-246), and on 200 research seasons it was −$292 a season (se 199). DESIGN §14 item 4.
- **H1 exits by itself when S3 is off for the round** (fallback exit at 84-98 s; DESIGN §14 item 3), so `s3 off`
  or `s3 rounds 3` no longer leaves the H1 book open until the flatten or settlement.
- **`ov PARAM X|off` console command** for announcement overrides (DESIGN §14 item 2), with
  **`ANNOUNCEMENT_CARD.md`** (+ PDF): the headline-to-override table, rehearsed offline (`sim/out/announce_rehearsal`).
- **`preflight.py`**, the read-only competition-morning check: Python and websockets, the key file (format, mode
  600, never printed), the bot's self-test in process, `GET /competitions/time` ×5 with latency, the served
  `case.json` diffed against the research case (a timing difference prints a banner), positions and open orders,
  and two market-data WebSockets with the bot's exact subscription for 20 s. It can only send GETs to four
  allow-listed URLs (no redirects, no proxy). Tests: `tests/test_p_preflight.py` (28, all fakes, no socket).
- **`COMPETITION_DAY.md` / `.pdf`:** the operator's quickstart: setup, key replacement, self-test, preflight,
  2-minute dry run, the live command and phrase, the rounds, emergencies, after each round.
- **`sim/run_recording.py`:** the checks written for `testnet_s224_s225.jsonl` (2 season resets, 5 reconnect
  pairs, the relevant snapshots, the season-225 book checks) run only when that whole recording is replayed, and
  the per-season S1-r3 checks only for seasons whose round-3 final report is inside `--from`/`--to`; each skip
  prints one `[SKIP]` line. Exit status on the original recording is unchanged (0 in both modes); the 238-239
  window of `testnet_dryrun_20261023.jsonl` now exits 0 instead of 4 misleading FAILs.
- **Tests (24 Oct, final code, S3 at 80 s):** default suite `PY -m unittest discover -s tests`: **675 tests OK**
  (8 skipped; 647 before plus the 28 preflight tests). Review suite `-p "review_*.py"`: **42 tests OK** (4 slow
  skipped). Config hash on `cornelltradingcompetition.org`: `70d9aac5c403`.
- **Overnight dry-run soak (00:35-05:07, seasons 242-246):** no exceptions, no restarts, no feed drops on any
  connection. `law_check_failed` fired in several rounds; with it the round-3 settlement trade (S1-r3) made
  $81-250 a season, and with the check off $81-1,074 (+$958 over the 5 seasons, mostly season 246). Kept on
  as protection against the live settlement formulas differing; the analyst clears it with `lawok`.
- **Not verified offline:** the preflight against a real host, the second-network bind
  (`feed.connections.1.local_addr`) on macOS, and `--live` (never run anywhere).

## 1. What is done

- **Groups A-E built to DESIGN.md.**
  - Infrastructure: config, clock, the two-connection feed normalizer, the REST client, the executor and the rate
    limiter.
  - State and execution: books, positions, orders, the order manager, reconcile and risk.
  - Models and strategies: the engine adapter, cost model, coverage, laws, H1, S3, flatten, S1-r3, the REIN ask,
    lead-lag (off) and announcements.
  - Application: the core, shell, console, logs, persistence and cli.
  - Simulation: the exchange, research and recorded markets, the harness and reports.
- **Integration:** six fixes, in DESIGN §12.
- **Hardening** (this pass): all 33 findings of the spec, safety and robustness reviews were handled.
  - 30 are fixed as proposed.
  - 3 are fixed in a different form, with the reasons in the table below: safety 6 (one sub-item kept by design),
    safety 9 (the stale-book rule became a feed-stale rule) and spec 7 (a strategy re-emit instead of either
    proposed fix).
  - 27 changes are listed in DESIGN §13. `config/default.json` gained 11 keys, and `TargetIntent` gained 3 optional
    fields; DESIGN §3 and §8 were re-synced verbatim.
- **Documents:** `RUNBOOK.md` (install, config, replay, dry run, live gate, console, logs, restart, competition-day
  checklist) and this file.

## 2. What is tested, and how

Everything below ran offline on this laptop:
- no socket was opened and no order left the machine;
- `~/.ctc_bot_key` was never read;
- every deliverable is under `bot/`, with the probes and backups in the session scratchpad.

Run outputs:
- `runs/harden_replay_x10`;
- `sim/out/harden_default`, `harden_parity`, `harden_rec_dryrun` and `harden_rec_book`.

| Check | Command | Result |
|---|---|---|
| Default unit suite | `PY -m unittest discover -s tests -v` | **638 tests OK** (8 skipped: slow or data-dependent). It includes the new `tests/test_h_hardening.py` (33 tests) |
| Review and fault-injection tests | `PY -m unittest discover -s tests -p "review_*.py" -v` | **42 tests OK** (4 slow skipped). With `CTCBOT_SLOW=1`, the 4 slow `review_robust_sim` tests also pass |
| Slow acceptance suite | `CTCBOT_SLOW=1 CTCBOT_REQUIRE_DATA=1 PY -m unittest discover -s tests -v` | See §2.1 |
| Self-test | `PY -I run_bot.py --self-test` | exit 0, all 6 checks ok |
| End-to-end dry run through the real shell | `run_bot.py --replay <recording> --replay-speed 10` (§3 of the runbook) | All 44,296 frames, **0 exceptions**, 3,068 order actions, at most 146 in any 61 s, **0 exchange violations, 0 live orders at the end**, 5 reconnects, 2 season resets (each merged with its comp_id), 1 real `feed_down`. It ended through `sources_done` → kill → `kill_complete` 0.8 s later → exit 0 (6.4 min wall) |
| §9.2 recording acceptance | `sim/run_recording.py --mode dryrun\|book` | **Every blocking check passes in both modes.** Relevant snapshots 695, 764, 250, 416, 828; S1-r3 fired in both round 3s; 0 exceptions, violations and 429s; at most 146/141 actions per 61 s. Book mode, season 225: **15,741** (11,739 before), rounds 3,972 / 7,021 / 4,748. Round 2 is the gain: the H1 rebuilders are no longer dropped at base cost. The S3 ±15k flag (non-blocking) still trips at −29.0k |
| §9.3 research seasons 401-440, default config | `sim/run_research.py --fv cache --ablations` | Mean **16,337** (16,350 before), p5 5,342, hit 1.00, 0 violations, max 146 per 61 s; `abl_edge` 16,582. **All default checks pass** |
| §9.3 parity profile against REC | `sim/run_research.py --profile config/rec_parity.json --h1-off`, then `compare_rec.py` | Mean **19,673** against REC 19,660, r 0.996, median \|d\| 835, H1+S3+flatten 19,503, H1-off diagnostic 14,299: all pass. The action count still fails (1,769 against 1,227 ± 20%, as before: integration gap G1) |
| H1 cost-ratio calibration | Scratchpad probe, seasons 401-440 | The new first-clip ratio has a median of 0.94 at base cost (p90 1.13), and **drops the rebuilders in 0/40 seasons** (6/40 before). At costs ×1.5 its median is 1.28, and it drops them in 5/40 seasons |

### 2.1 Slow acceptance suite

`CTCBOT_SLOW=1 CTCBOT_REQUIRE_DATA=1`, on the final code: **638 tests in 999 s, 1 failure, 0 skipped.**
- **The one failure** is `test_e_research.TestAcceptance93.test_parity_run`, on the action count only (1,769 against
  1,227 ± 20%). This is the same known design gap as at integration (G1); every other parity criterion passes.
- **Kill drill:** cancel-all after 0.4 ms, 8 kill CANCEL_ALLs, 0 orders open 1 s after the last POST.
- **S1-r3 hand-off latency:** p95 32.3 ms, against the 300-ms limit.
- **Slow reviews:** `CTCBOT_SLOW=1 CTCBOT_REQUIRE_DATA=1 PY -m unittest discover -s tests -p "review_*.py"` passes all
  42 tests, the 4 recorded-market fault-injection runs included.

### 2.2 Evidence behind the design choices

- **Book age is not a staleness signal.** On the recording, H1's entry books are p50 5.2 s and p90 19 s old at
  order time. A 3-s/8-s book-age rule would have refused 33 approved H1 entries and 24 S3 clips. The feed-based rule
  refused none of the approved orders.
- **Slow GETs.** With every REST answer 2.1 s late, the bot now completes reconcile cycles. Mid-storm its positions
  equal the exchange's of 6-7 s earlier, and at the quiet points (after 225 r1 and r2) they are exact. Before, no
  cycle ever completed.
- **REIN top-up.** Both proposed fixes made reconcile book foreign fills in the B property test:
  - an OM top-up: two same-price orders sent at once (the default test);
  - letting passive rests use the full limit: one seed of the slow test.

  The REIN strategy now re-emits its ask every cycle while the ask is short of its room. Risk and the OM are
  unchanged, and the property tests pass.

## 3. Review findings and their outcomes

Code locations are the reviewed ones. The tests that guard each fix are in `tests/review_*.py` and
`tests/test_h_hardening.py`.

| # | Finding (severity) | Outcome | What was done |
|---|---|---|---|
| spec 1 | S1-r3 sanity check skipped for symbols first placed after 756 s (major) | **Fixed** | A stock first placed after `sanity_at` gets the step-7 check before placement. The two expected-failure tests now pass |
| spec 2 | H1 cost ratio drops the rebuilders at base cost; LIMIT_BOUND fills included (major) | **Fixed** | The ratio uses each symbol's first entry clip against the walk table and excludes LIMIT_BOUND fills. 0/40 drops at base cost. The review test's `assertIsNotNone(cost_ratio)` contradicted the reviewer's own fix (with only limit-priced fills there is no ratio), so it was adapted. A new first-clip test was added |
| spec 3 | S1-r3 drops ARCH/REBLD when lead-lag is on (major) | **Fixed** | Lead-lag blocks the ETFs only before `etf_leadlag.end` (738 s). DESIGN §13 corrects §4.C |
| spec 4 | Fallback-exit fills enter the round-1 check (minor) | **Fixed** | Only entry-direction fills count |
| spec 5 | `flatten_behind` unreachable (minor) | **Fixed** | Compared against the base clip L/12 × slots left. It now fires 35 times in 80 research rounds, against 140 `not_flat` |
| spec 6 | Windows leak up to 0.6 s; H1 clips drift with latency (minor) | **Fixed** | `TargetIntent.valid_until` and `slot_anchor`. At 0.6 s of latency the H1 clips go at 50.0, 52.6, 54.6 and 56.6 s; before, the 4th clip fell out of the window |
| spec 7 | REIN ask offers half its room for 5 s (minor) | **Fixed (different form)** | While the ask is live but short of the REIN sell room, the strategy re-emits it every cycle, so the second 0.5 L order follows within 0.25 s. Both proposed forms (an OM top-up, or exempting rests from the 0.5 L cap) made the B property test book foreign fills, so neither was used |
| spec 8 | Fill-sanity trigger 3×(walk+fo) instead of 3× table (minor) | **Fixed** | `first_clip_cost_bp`: a clip with no own same-side clip in 8 s is judged on the table (walk). Follow-on clips keep walk + fo_allow, so the §12 false disables do not return (0 disables in the research runs) |
| safety 1 | Ctrl-C exits before the kill completes (major) | **Fixed** | The shell keeps running until `kill_complete` (or 5 s) with no order action outstanding |
| safety 2 | `q` cancels once, never freezes (major) | **Fixed** | `q` and `sources_done` take the kill path and the same wait |
| safety 3 | An unsent cancel re-sent at once drains the budget (major) | **Fixed** | Tickets are refunded for `sent=False`; re-queued cancels back off 0.25 → 1 s |
| safety 4 | The KILL overdraft moves with `b N` (major) | **Fixed** | The KILL ceiling is absolute (max_cap + overdraft = 170). `cx` reports `QUEUED` truthfully; a repeated `k` reports "re-armed" |
| safety 5 | The kill worker is busy prewarming at 737 s (major) | **Fixed** | Kill workers never prewarm |
| safety 6 | Repeated `k`, repeated Ctrl-C and the kill retry bypass the budget (minor) | **Fixed, one part kept by design** | A repeated `k` only re-arms the budgeted follow-up; only the 2nd signal hard-cancels. **Kept:** the one post-write retry of the kill CANCEL_ALL (DESIGN §11 item 22). It is idempotent, and dropping it would delay a kill on a stale connection |
| safety 7 | Bot alone can reach 190 > 180 (minor) | **Fixed** | max_cap = team − 10 − overdraft = 150; validation enforces cap + overdraft ≤ team − 10. **Not done:** charging foreign order ids to the ledger (marked optional by the reviewer) |
| safety 8 | Tickets granted at decision time; queued orders go late (minor) | **Fixed** | A queued PLACE/ETF older than 0.5 s is answered `stale_queue` and refunded. Cancels are exempt (risk-reducing; they are re-queued anyway) |
| safety 9 | 1.5% cap only for orders marketable against our view; book age never checked (minor) | **Cap fixed; stale-book rule replaced** | The cap now applies on the aggressive side of mid. The book-age rule was rejected on evidence (§2.2) and replaced by `feed_stale`: no frame on any connection within the watchdog → reduce only. The review test was adapted to that rule |
| safety 10 | SIGHUP not handled (minor) | **Fixed** | Handled like SIGINT |
| safety 11 | No backoff on 401/403/429 handshakes (minor) | **Fixed** | 5 s on 401/403, 1-10 s exponential on 429, `ws_rejected` alert |
| safety 12 | Safety settings unbounded in config (minor) | **Fixed** | Always: team ≤ 180, overdraft ≤ 20, cap + overdraft ≤ team − 10, kill_workers ≥ 1. For `--live`: the price bands and other safety keys within the live bounds (the sim-only parity profile still needs 8%) |
| robust 1 | Slow GETs: no cycle completes and no hold (major) | **Fixed** | Cycle timeout 6.5 s; a dropped cycle is a failure; no completed cycle for 10 s holds `reconcile_down`. The slow test's exact-equality criterion was replaced (§2.2) |
| robust 2 | H1 re-runs the round-1 check after a restart (major) | **Fixed** | The saved result stands |
| robust 3 | `om.step` not exception-safe (major) | **Fixed** | Per-intent isolation, intent validation, atomic `_try_place`, in-flight PLACE expiry after 10 s |
| robust 4 | One odd competition_id forces a reset (major) | **Fixed** | A candidate needs 2 answers ≥ 1 s apart, or feed evidence; a flap is ignored with an alert |
| robust 5 | Snapshot without a usable `last_seq` read as −1 (major) | **Fixed** | Only an integer −1 starts a season; others are bad frames |
| robust 6 | S3 trades on values up to 5 s old (major if the engine slows) | **Fixed** | S3's own 2-s gate; `engine_slow` alert above 1 s |
| robust 7 | Strategy disabled for good; resting orders not cancelled (minor) | **Fixed** | Disabled via `set_enabled`; re-enabled after 30 s (2×/season) and at each season reset; not persisted |
| robust 8 | State write failures hold all new risk (minor) | **Fixed** | Alert only, at most once a minute |
| robust 9 | Frozen server time freezes the clock (minor) | **Fixed** | Server time more than 2 s behind fresh storm observations is ignored (`clock_server_stale`) |
| robust 10 | Failed `eng restart` leaves a closed engine (minor) | **Fixed** | Builds first, then swaps |
| robust 11 | Previous-season message seeds the new season (minor) | **Fixed** | Dropped within 180 s of a reset when its date is the old season's and ≥ 30 days off the new calendar. A same-calendar server is never affected (tested) |
| robust 12 | Up to 5 s of orders unknown after a restart (minor) | **Fixed** | `state.json` is written within 1 s of any order change |
| robust 13 | Foreign-fill noise across a season boundary (minor, log noise) | **Fixed** | A jump in ≥ 4 symbols with no live record of ours holds foreign fills 3 s and polls `/competitions/time` at once |

## 4. Known gaps

**Strategy and calibration decisions for the team.** These are unchanged from the integration report unless noted.
- **G1: the parity action count** fails because the bot counts its remainder cancels (1,769 against 1,227 ± 20%).
  Count places only (+19% at integration) or widen the band.
- **G2: the S3 edge rule** (touch against REC's mid) still needs sign-off.
- **G3: the H1 cost-ratio threshold.** The basis is now the first clip against the tables. 1.5 never drops at base
  cost, but at costs ×1.5 it drops in only 5/40 seasons (median 1.28). A threshold near 1.2 would separate base
  (p90 1.13) from ×1.5.
- **G4:** the fill-sanity breaker under the sim-only 8% caps (sim only).
- **G5:** remainder cancels use the action budget (about 560 per season in the default config).
- **The S3 ±15k flag on the 225 books** (−29.0k, mostly selling inherited H1 inventory). It is non-blocking, and
  H1 + S3 + flatten is positive.

**Unmeasured on a real exchange.** Latency, ack schemas, price protection, IOC, whether remainders rest, and
whether 429s count. This is the 1-lot test plan.
- The new `max_queue_age_s` (0.5 s) assumes queue waits stay short. On a slow exchange, S1-r3's 740-s burst may see
  some PLACEs answered `stale_queue`. They are refunded and re-placed by the next 0.5-s sweep. Revisit after T5
  (burst latency).

**Not exercised.**
- Host mode against the real testnet, even read-only (no network was allowed in this build).
- `--live`: never run anywhere.
- The WebSocket backoff was tested against injected handshake errors only. The status-code extraction assumes
  websockets 17's `InvalidStatus.response.status_code`.

**Kept by design.** The kill CANCEL_ALL may be retried once after a transport error (at most one extra action
per kill emission). Foreign (manual) orders are not charged to the bot's ledger, so humans must stay inside their
30 actions per minute (10 during a kill).

**Not built** (DESIGN §10). S2, S1 in rounds 1-2, the clock-only fallback, passive quoting and ETF
create/redeem.

**Housekeeping.** `tests/_tmp/` holds 1,138 old entries from earlier sessions; the test runs of this pass removed
their own. The scratchpad probes (book age, H1 ratio, slow GETs) are session files, not deliverables.

## 5. Next steps

1. The team reads `RUNBOOK.md` and agrees the competition-day settings (§2 of the runbook): H1 sizes, S3 rounds,
   the G2 edge rule and the G3 threshold.
2. A read-only dry run against `cornelltradingcompetition.org` with the key file (runbook §4):
   - check `ws 2/2`, the clock, the engine age and the case diff;
   - run the kill drill in dry-run.
3. Approve the 1-lot test plan in writing (TESTNET_FINDINGS §7, T1-T7), then run it with operator A and watcher D.
   Measure:
   - ack and cancel latency, and the burst p95 at 740 s;
   - response schemas;
   - price protection and whether 429s count.

   Then revisit `max_queue_age_s`, the 2-s S3 engine gate and the 6.5-s reconcile timeout against the measured
   latencies.
4. A full testnet season at `--profile config/testnet_quarter.json`, with S3 P&L scored on fills (restrict S3 to
   round 3 if rounds 1-2 lose again).
5. Decide G1-G5. Re-run the §9.3 parity and default runs and the §9.2 recording runs after any change.
6. On competition day, follow the runbook checklist (§9).
