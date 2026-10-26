# ctcbot runbook: Systematic Equities (Hurricane Season), CTC 2026

> **Competition day: start with `COMPETITION_DAY.md`** (printable `COMPETITION_DAY.pdf`): the step-by-step
> quickstart with copy-paste commands, including the read-only morning check `preflight.py`. This runbook is the
> detail behind it.

This runbook is for the team that runs the bot on competition day. The design is `DESIGN.md` (revision 2, with
the integration changes in §12 and the hardening changes in §13). The current state of the build is in
`STATUS.md`.

Paths:
- `SYS` is `/Users/bowentian/Documents/Fun Stuff/CTC2026/systematic`.
- `BOT` is `SYS/bot`.
- `PY` is `SYS/.venv/bin/python`.

Run every command from `BOT`.

> **The bot is dry-run unless you pass `--live` and type the confirmation phrase.** In dry-run it reads the
> market and the exchange (GETs and the WebSocket), logs every order it would send, and sends none.
> **Do not use `--live` until the team has approved the 1-lot test plan** (TESTNET_FINDINGS §7) in writing, and
> that plan has run on the testnet.

---

## 1. Install and verify

1. **Python.** The bot needs Python 3.14 with `websockets` 17.3, from `SYS/.venv`. It uses nothing else.
   Check the version:
   ```
   PY -c "import sys, websockets; print(sys.version, websockets.__version__)"
   ```
2. **Self-test.** This runs offline and sends nothing. It should exit 0, with six `[ok]` lines: config, research
   case, engine snapshot-fix probe, cost model, settlement laws, case diff.
   ```
   PY -I run_bot.py --self-test
   ```
   A failed engine probe means `SYS/engine/fairvalue.py` lacks the snapshot fix. The bot then forwards no snapshots
   to the engine and refuses `--live`.
3. **Unit tests.** About 60 s; they must end `OK` (8 skips are allowed: slow tests and data-dependent ones).
   ```
   PY -m unittest discover -s tests -v
   ```
   The review and fault-injection tests run separately; they must also end `OK`:
   ```
   PY -m unittest discover -s tests -p "review_*.py" -v
   ```
   Tests leave temporary files in `tests/_tmp/`. You can delete that folder at any time.
4. **The key.** Put the team bot key in a file that only you can read:
   ```
   chmod 600 /path/to/keyfile
   ```
   Pass it with `--key-file /path/to/keyfile`, or set the environment variable `CTC_BOT_KEY`. There is **no
   default key path**: the bot never reads `~/.ctc_bot_key` on its own. The key never appears in a log, an error or
   the status line.

---

## 2. The config file and what may change on the day

The base config is `config/default.json`. Do not edit it. Change settings in one of two ways:
- an overlay: `--profile FILE.json`. Overlays merge in order;
- one setting at a time: `--set a.b.c=VALUE`. VALUE is parsed as JSON. The path must already exist, so a typo
  fails loudly.

Two tools show the result:
- `PY -I run_bot.py --print-config [--profile ...] [--set ...]` prints the effective config, its hash and every
  difference from the default;
- the live prompt lists the same differences before you type the phrase.

**Profiles:**
- `config/testnet_quarter.json`: 25% size, engine library [300, 600]. It is allowed for live runs and is meant for
  the testnet scoring season.
- `config/rec_parity.json`: **sim only.** It reproduces the REC backtest's fill assumptions, and it carries
  `"live_allowed": false`, so `--live` refuses it.

**Settings that may change on the day** (each needs the team's agreement, and the reason goes in the run notes):

| Setting | Default | When to change it |
|---|---|---|
| `sizing.scale` | 1.0 | Smaller size, for example 0.25 on the testnet or for a cautious start. Applies to targets, clips, rests and risk caps |
| `strategies.<name>.enabled` | see §6 of DESIGN | Switch a strategy off for the whole run (the console's `<name> off` does it for this run only) |
| `strategies.h1.size_r1`, `size_on_pass`, `size_on_fail` | 0.5 / 1.0 / 0.5 | H1 sizing. The console's `h1 X` overrides it live |
| `strategies.h1.drop_rebuilders_cost_ratio` | 1.5 | The round-1 cost-ratio trigger for dropping RECON, MAT, GEN and REBLD (now measured on each symbol's first clip against the tables; DESIGN §13). `null` never drops them |
| `strategies.s3.edge_ref`, `limit_cost_frac` | `touch` / 1.0 | **Only after the team signs off the S3 edge rule** (DESIGN §10): `mid` with 0.5 is REC's rule |
| `strategies.s3.rounds` | [1, 2, 3] | The console's `s3 rounds 3` restricts S3 to round 3 if S3 loses on fills in rounds 1-2 |
| `strategies.etf_leadlag.enabled` | false | Test-first. Do not switch it on without a test |
| `feed.connections.1.local_addr` | null | `["<hotspot ip>", 0]` puts the second WebSocket on a second network path |
| `engine.library_seasons` | [1, 600] | Raise the lower bound above the testnet's current season for an out-of-sample engine |
| `rate.bot_cap_per_min` | 150 | Lower only. The maximum is 150, because the bot cap plus the KILL overdraft (20) must stay at or below 170 = team limit − 10. The console's `b N` lowers it live |

**Settings that must not change for a live run.** These are the safety settings. `--live` refuses a config that
loosens them beyond these bounds (`config.live_bound_problems`):

| Setting | Live bound |
|---|---|
| `risk.max_marketable_from_mid` | ≤ 0.03 (default 0.015) |
| `risk.fat_finger_frac` | ≤ 0.50 (default 0.25) |
| `risk.snipe_band_frac` | ≤ 0.80 (default 0.60) |
| `risk.max_clip_frac` | ≤ 1.0 (default 0.5) |
| `risk.max_snipe_frac` | ≤ 2.0 (default 2.0) |
| `risk.max_resting_per_symbol` | 1-16 (default 8) |
| `risk.drawdown_usd` | ≤ 40,000 (default 20,000) |
| `risk.fill_sanity_n` | 1-10 (default 3) |
| `exchange.allow_market_orders` | must be false |

Config validation also enforces these for every run:
- `exchange.team_actions_per_min` ≤ 180;
- `rate.kill_overdraft` ≤ 20;
- `rate.bot_cap_per_min` + `rate.kill_overdraft` ≤ team limit − 10;
- `exchange.kill_workers` ≥ 1;
- `exchange.max_queue_age_s` ≤ 1 s.

---

## 3. Offline replay of a recording

A replay runs the real shell, core, strategies and engine against the testnet recording. A simulated exchange
answers; it accepts orders but never fills them. Nothing leaves the machine.

```
printf 'lawok all\n' | PY -I run_bot.py --replay ../testnet/recordings/testnet_s224_s225.jsonl \
    --replay-speed 10 --run-dir runs/replay_test --fresh \
    --history <SP>/data/hurricane-season-research/history --set 'engine.library_seasons=[226,600]'
```

- `--replay-speed 10` runs the hour-long recording in about 6-7 minutes.
- `lawok all` answers the late join into season 224: round 1-2 law checks never ran there.
- The run ends by itself when the recording ends. It then cancels everything (the kill path) and exits 0.
- The results are in the run directory (§7). `sim_exchange.json` holds the simulated exchange's own record:
  violations, rate windows, resting maxima, orders left live and P&L.
- `--replay-from UNIX` and `--replay-to UNIX` replay part of the file.

**Simulations in virtual time** (faster; these are the acceptance runs):
```
PY -I sim/run_recording.py --mode dryrun --command "1:lawok all" --out sim/out/<name>
PY -I sim/run_recording.py --mode book   --command "1:lawok all" --out sim/out/<name>
PY -I sim/run_research.py --seasons 401-440 --fv cache --ablations --out sim/out/<name>
PY -I sim/run_research.py --seasons 401-440 --profile config/rec_parity.json --fv cache --h1-off --out sim/out/<name>
PY -I sim/compare_rec.py --seasons 401-440 --report sim/out/<name>/report.json
```
`PY -I run_bot.py --sim research:401 --sim-speed 25` runs one research season through the real shell.

---

## 4. Read-only dry run against the testnet

This mode connects to the real testnet (`cornelltradingcompetition.org`):
- it fetches `case.json`, `/competitions/time`, `/orders/open` and `/positions`;
- it opens two market-data WebSockets.

**It sends no order action.** Without `--live` no `LiveToken` exists. The REST client answers every place,
cancel and cancel-all itself, as `dry_run`, and logs it as `dry_order`. You need the key file.

```
PY -I run_bot.py --host cornelltradingcompetition.org --key-file /path/to/keyfile --run-dir runs/dry_<date>
```

**Check at startup:**
- the banner says `(DRY-RUN)` and the status line starts with `[DRY]`;
- `note: N open orders exist on this key` warns you if someone else is trading on the key;
- the case diff: if the served `case.json` differs from the research case, S1-r3 excludes the changed symbols until
  `caseok SYM` (after the analyst signs off). A timing difference needs `--accept-timing-diff` for live runs and
  `caseok timing` otherwise.

**What to look for:** the status line every 2 s, with these parts:
- clock: round, phase, `t`, quality;
- feed: both connections up, frame age, `wm`, gaps;
- engine: age and compute time;
- actions per minute;
- holds.

`events.jsonl` should show `intent` and `dry_order` lines in the windows of §6 of DESIGN. Stop with `q`.

---

## 5. Live mode (do not use yet)

Live sending needs **all** of these:
1. `--live` with an explicit `--host` (host mode);
2. an interactive terminal (stdin a TTY);
3. a config that is live-allowed (no sim-only profile) and inside the live bounds (§2);
4. a fetched `case.json`, and `--accept-timing-diff` if its timing differs;
5. a passing engine snapshot-fix probe;
6. no `KILL` file in the run directory;
7. the phrase `LIVE <host> <first 6 characters of the config hash>`, typed exactly at the prompt (3 attempts).
   The prompt first shows the host, the config hash, the profiles, every config and case difference, the enabled
   strategies, the size and the action cap.

Only the typed phrase creates the `LiveToken`, and the REST client sends order actions only while it holds a
token for that host and config hash. Any missing condition refuses `--live` with the reasons and exits 2.

```
PY -I run_bot.py --live --host <host> --key-file /path/to/keyfile --run-dir runs/live_<date> [--profile ...]
```

**Not before the team approves the 1-lot test plan** (TESTNET_FINDINGS §7: tests T1-T7, 1 share per order, at
most 60 actions per round, the stop rules, operator A, watcher D). After that, the next step is a full testnet
season at `--profile config/testnet_quarter.json`, with S3 P&L scored on fills.

---

## 6. Console, kill switch and announcement overrides

Type commands at the console and press Enter. Run `help` for the full list.

**Kill and stop:**

| Command | Effect |
|---|---|
| `k` | **Kill.** Freezes the bot (nothing new is placed, and queued PLACEs are purged). Sends CANCEL_ALL on the dedicated kill worker, then follow-up CANCEL_ALLs until every order known at the kill is gone and two clean reconcile cycles show nothing open. Positions are **not** flattened. A second `k` only re-arms the follow-up; it never sends an unbudgeted cancel-all |
| `touch <run dir>/KILL` | The same kill, from another terminal (checked every 0.25 s). The bot refuses to start while the file exists |
| Ctrl-C (or SIGTERM, or closing the terminal: SIGHUP) | Kill, then keep running until the kill is complete (at most `app.shutdown_kill_wait_s`, 5 s), then exit with code 130. **A second Ctrl-C** sends one immediate best-effort cancel-all and exits at once |
| `q` | Quit: the same kill path, then exit 0 once every order is cancelled |
| `cx` | One CANCEL_ALL at KILL priority without freezing. If the KILL budget is spent it prints `cancel-all QUEUED`; then use `k` or the browser |
| `unfreeze-kill` | Releases the kill. Type it in full |

Backup: **cancel all in the browser.**

**Holds and resume:**

| Command | Effect |
|---|---|
| `p` | Pause: no new risk (reducing orders still go) |
| `r` | Resume. It releases these holds: `operator_pause`, `drawdown`, `restart`, `feed_down`, `auth`, `season_suspect` and `comp_id_suspect`. `reconcile_down` releases by itself when reconcile works again |
| `holds` | Show the active holds and the disabled symbols |
| `x SYM` / `e SYM` | Disable a symbol (reducing orders only, and its orders are cancelled) or enable it |
| `b N` | Lower the bot's action cap (maximum 150). The kill keeps its own budget |
| `st`, `pos`, `ord`, `ann` | Status, positions, live orders, announcements |

**Strategies:**
- `h1 on|off|auto|X` (X is a size 0-1);
- `s3 on|off`, `s3 rounds 3`, `s3 costmult X`;
- `s1r3 on|off`;
- `rein on|off`;
- `flat on|off`;
- `lag on|off` (leave it off).

**Announcements.** An announcement freezes the named symbols and their links (and the ETFs that hold them), and
cancels their orders at once. The alert `announcement` shows the headline. Then, once the analyst decides:

| Command | Effect |
|---|---|
| `u SYM` | Unfreeze: back to normal |
| `ua SYM` | Enter the 45-s adjusting state: S3-style trades capped at L/2, toward the overridden value |
| `ov PARAM X` / `ov PARAM off` | Override one engine parameter for the rest of the season (e.g. `ov haven.attach 500`). **Use `ANNOUNCEMENT_CARD.md`** for the headline-to-override table; then `ua SYM` |
| `f SYM PX` / `f SYM off` | Fix the engine value of SYM at PX, or remove the fix |
| `m SYM X`, `a SYM X` | Value = X × engine, or engine + X |
| `run_dir/adjust.json` | The analyst's file, for example `{"PORT": {"mult": 1, "add": -2.0}, "_overrides": {...}}`. Merged into the engine |

**Round 3 settlement (S1-r3):**

| Command | Effect |
|---|---|
| `s1ok SYM` | Let S1-r3 trade a frozen or late-announced symbol |
| `x1 SYM` | Exclude a symbol from S1-r3 and cancel its rests |
| `lawok SYM\|all` | Clear the rounds 1-2 law-check gate (needed after a late join) |
| `caseok SYM\|all\|timing` | Clear the case-diff gate after the analyst checks the served `case.json` |

**Seasons and engine:**

| Command | Effect |
|---|---|
| `season keep` | Reject a suspected season change (`season_reset_suspect` alert): the state is kept |
| `season new` | Confirm it, or force one |
| `eng restart` | Rebuild the fair-value engine from its log. If the rebuild fails, the running engine is kept and `engine_restart_failed` is raised |

---

## 7. Logs and what to watch

The run directory (`--run-dir`, default `runs/<run_id>`) holds:

| File | Content |
|---|---|
| `events.jsonl` | Every structured event and alert, one JSON object per line. Alerts have `"kind": "alert"` |
| `feed_raw.jsonl` | The raw market data, in the `record_feed.py` format. It can be replayed |
| `fv.jsonl` | Every published fair-value snapshot |
| `state.json` | The saved state for restarts. Written every 5 s and within 1 s of any order change |
| `engine_state_<run>_e<epoch>.jsonl` | The engine's message log per season (used for restore) |
| `adjust*.json` | Overrides (analyst and console) |
| `KILL` | Present only if someone requested a kill |
| `sim_exchange.json` | Replay and sim only: the simulated exchange's record |

**Watch on the status line:**
- `[DRY]` or `[LIVE]`, and the mode: NORMAL, NO_NEW_RISK or FROZEN;
- round, phase and `t`;
- `ws 2/2` and frame age;
- `eng` age, ms and queue;
- `act used/cap`, rejects and unknown orders;
- MTM, positions, frozen symbols and holds.

**Alerts and what to do** (all in `events.jsonl`; the console echoes warnings):

| Alert | Meaning | Action |
|---|---|---|
| `kill`, `kill_incomplete` | Kill started, or its follow-ups ran out of KILL budget | Check `ord`; cancel all in the browser if anything stays open |
| `feed_down` | No market data for 10 s: everything was cancelled, new risk is held | It releases by itself after a snapshot (plus 2 storm obs in the storm). `r` to force |
| `ws_rejected` | The WebSocket handshake was refused (401/403: key; 429: too many connections or reconnects) | Check the key and how many connections the team has open (25 max) |
| `auth_failed` | HTTP 401/403 on REST | Check the key, then `r` |
| `api_errors`, `reconcile_down` | 5xx/transport errors, or no reconcile cycle for 10 s: the bot cannot see its fills | It releases by itself when the exchange answers. Watch it |
| `position_mismatch`, `foreign_order` | The exchange shows positions or orders the bot did not make | Another instance or the browser? Stop the other one |
| `positions_stale` | A symbol's `/positions` row was skipped for 5 s (orders in flight) | Usually harmless in bursts |
| `drawdown` | MTM fell $20k from the round's peak: new risk held | Team decision; `r` resumes |
| `symbol_disabled` | 5 rejects in 30 s or 3 bad fills in a symbol | Find the cause; `e SYM` |
| `strategy_disabled` / `strategy_reenabled` | 3 exceptions in 60 s: the strategy is off, retried automatically after 30 s (twice per season) | Report it. `<name> off` keeps it off |
| `engine_down`, `engine_slow` | Engine errors, or a compute > 1 s (S3 skips while values are older than 2 s) | `eng restart` if it does not recover |
| `h1_check_failed`, `h1_fallback` | The H1 round-1 check failed (rounds 2-3 at half size), or the engine was not live at 76 s (H1 exits) | Review; `h1 X` to override the size |
| `not_flat`, `flatten_behind` | A round ended with positions, or flatten is behind its base pace at 560 s | Flatten by hand if needed |
| `season_reset_suspect`, `season_reset_unconfirmed` | Feed evidence of a new season | Confirm with `season new` or reject with `season keep` |
| `comp_id_flap` | `/competitions/time` answered another competition once | Ignored by the bot. Report it if it repeats |
| `clock_server_stale` | The server time is behind the feed: the feed clock is in use | Report it |
| `s1r3_*` | Round-3 settlement events: laws fallback, sanity exclusions, late arm | Check `st`; use `s1ok`, `x1` or `lawok` as the analyst decides |
| `announcement` | An announcement froze symbols | Use `u`/`ua`/`f` per the analyst |
| `persist_failed` | `state.json` could not be written (disk full?) | Free space; trading continues |
| `restart`, `h1_size_needed` | Restored state: new risk is held | `h1 X` if asked, then `r` |

---

## 8. Restart after a crash

1. **If the process died while live,** cancel all in the browser first, then restart.
2. Restart with the **same** `--run-dir` and the same flags. At startup the bot reads `state.json`:
   - **Same `competition_id` as the server:** it restores positions, cash, orders, holds, strategy state (including
     the H1 round-1 check result), the action ledger, the season info and the engine (from its log). Then it holds
     `restart`. Check `pos`, `ord` and `holds`, then type `r`.
   - **Another `competition_id`:** it starts fresh.
   - **A dry-run state under `--live`** (or the reverse) is a **promotion**: dry orders, positions and the ledger are
     dropped and rebuilt from the exchange. The engine log is kept. H1 uses the fail size until `h1 X`. Then type `r`.
3. `--fresh` ignores `state.json`, for example after a corrupt file. The engine then starts empty, so earlier
   rounds count as missing for S1-r3's closure symbols; use `lawok`/`caseok` as the analyst decides.
4. A `KILL` file left in the run directory blocks the start. Remove it once you know why it is there.
5. Orders placed in the last second before a crash may be unknown to the new process. Reconcile adopts them as
   foreign orders, which are counted, alerted and cancelled by a kill.

---

## 9. Competition-day checklist

**The day before:**
- [ ] `PY -I run_bot.py --self-test` exits 0, and both unit-test commands end `OK` (§1).
- [ ] The offline replay (§3) ends with exit 0, 0 exceptions and 0 violations in `sim_exchange.json`.
- [ ] Key file in place, `chmod 600`. Know who holds the browser session for cancel-all.
- [ ] Decide and write down every `--set`/`--profile` (§2), and `--print-config` the result.
- [ ] Optional: a hotspot for `feed.connections.1.local_addr`.

**Before the session:**
- [ ] `PY -I preflight.py --host <host> --key-file /path/to/keyfile` ends `RESULT: PASS` (or with WARNs the team
  has read). It only sends GETs and opens two market-data connections (`COMPETITION_DAY.md` C2).
- [ ] Fresh run directory: `runs/<date>_<session>`. No `KILL` file in it.
- [ ] Dry run against the host (§4) for a few minutes:
  - `[DRY]` on the status line;
  - `ws 2/2`;
  - clock known;
  - `eng` fresh;
  - no `auth_failed` or `ws_rejected`;
  - `caseok`/timing reviewed with the analyst.
- [ ] Kill drill in dry-run: press `k`, then check that `ord` shows nothing live; `unfreeze-kill`. Then `touch KILL`
  and remove it. Then Ctrl-C (then restart).
- [ ] Roles: operator at the console, a watcher on `events.jsonl` alerts, the analyst for announcements and
  `adjust.json`, and someone on the browser for cancel-all.

**During the session:**
- [ ] Watch the status line: feed, engine age, action rate, holds and MTM against the $20k breaker.
- [ ] Round 1, 50-90 s: H1 entries, then the `h1_check` result.
- [ ] Rounds 1-2, 540-568 s: flatten. Watch `flatten_behind`/`not_flat`; flatten by hand if needed.
- [ ] Announcements: the analyst decides `u`/`ua`/`f`.
- [ ] Round 3, 740-778 s: S1-r3 fires on the final report. Watch the `s1r3_*` events; `s1ok`/`x1`/`lawok` per the
  analyst. REIN's riskless ask is cancelled at 740 s.
- [ ] Any doubt: `k` first, ask questions afterwards.

**After the session:**
- [ ] `q`. Wait for the exit (the kill completes first). Check in the browser that nothing is open.
- [ ] Keep the run directory: `events.jsonl` and `feed_raw.jsonl` are the record. Copy it off the laptop.
