# Announcement card: Systematic Equities (print this; keep it next to the bot console)

**Why it matters.** After an announcement the house takes about **45 s** to reprice (packet §5). The research data
has no announcements, so most teams will be slow. Our engine is built from explicit parameters, so most headlines
become **one `ov` line**. Target: the override typed within **10 s** of the headline.

## What happens automatically

1. The bot raises the alert `ANNOUNCEMENT: <headline> -> frozen ...`.
2. It **freezes** the named symbols, their linked names, and the ETFs that hold them, and cancels their orders.

Linked names:

| Named | Also frozen |
|---|---|
| HAVEN | REIN, MAT |
| REIN | HAVEN, MAT |
| MAT | HAVEN, REIN |
| GRID | GEN, RECON, HAVEN, REIN, MAT |
| PORT, FERRY | RECON, HAVEN, REIN, MAT |
| ISLE, AQUA | HAVEN, REIN, MAT |

## What you do (in order)

1. **Read the headline. Decide: does it change a formula below?**
   - **No** (pure noise, already priced, or no economic effect): type `u SYM` for each frozen symbol. Done.
   - **Yes:** go on to step 2.
2. **Type the override:** `ov PARAM VALUE` (examples below). It applies to the whole season. `ov PARAM off` undoes
   it.
3. **Trade toward the new value:**
   - `ua SYM` for each symbol whose value changed. Include the ETF that holds it: ARCH holds 1 PORT, 1 ISLE,
     2 AQUA, 1 GRID, 2 FERRY and 1 HAVEN; REBLD holds 2 RECON, 2 MAT and 1 GEN.
   - The bot then buys or sells toward the new value for 45 s, in clips of about L/12, capped at **half the
     position limit**. It stops once the house has covered 80% of the gap.
4. **Unfreeze the linked names the headline does not affect:** `u SYM`.
5. **Wrong call?** Type `ov PARAM off`, then `x SYM` to stop trading it, or `k` for everything.

Rehearsed offline on 23 Oct. A fake "HavenBridge raises its retention to $500M" was injected into season 225
round 3, then `ov haven.attach 500` and `ua HAVEN` were typed. The bot took HAVEN from −130 to +125 (half the
limit) in 20-share clips over 26 s. Run: `sim/out/announce_rehearsal`.

## The formulas being changed

| Group | Formula | Current values |
|---|---|---|
| Islands | value = V0 − a·D − K·(closed weight-seconds); D = season damage in % points | PORT 100 / 0.55 / 0.02 · ISLE 60 / 0.38 / 0 · AQUA 35 / 0.20 / 0 · GRID 50 / 0.24 / 0.05 · FERRY 25 / 0.05 / 0.06 |
| HAVEN | 75 − slope·clip(L − attach, 0, top − attach); L = season insured loss, $M | v0 75, slope 0.06, attach 400, top 1300 (floor 21) |
| REIN | 90 − slope·max(L − attach, 0) | v0 90, slope 0.035, attach 1300 |
| RECON | a + b·(1 − e^(−X/c)); X = 9·PORT% + 7·GRID% + 1.2·FERRY% ($M, season) | a 40, b 24, c 1000 |
| MAT | a + b·(1 − e^(−L/c)) | a 28, b 14, c 1200 |
| GEN | a + b·(1 − e^(−O/c)); O = grid closure weight-seconds | a 32, b 18, c 120 |
| Damage rate (future storms) | site loses 1 − e^(−k·m·hazard) of its remaining value | k (`SYM.dmg`): PORT 0.0110, ISLE 0.0087, AQUA 0.0155, GRID 0.0117, FERRY 0.0085 |

## Headline → override

Effects are per share. "At the limit" means the full position limit; the bot trades at most half of it.

| Headline type | Type this | Effect per share | Also |
|---|---|---|---|
| HavenBridge **raises** its retention to $R M | `ov haven.attach R` | HAVEN up by 0.06 × (R − 400) once the season loss passes R; e.g. R = 500: **up to +$6** | `ua HAVEN`, `ua ARCH`; `u REIN`, `u MAT` |
| HavenBridge **lowers** its retention to $R M | `ov haven.attach R` | HAVEN down by up to 0.06 × (400 − R) | as above |
| Reinsurance now starts at $T M (was 1,300) | `ov haven.top T` and `ov rein.attach T` | HAVEN's floor rises from 21 to 75 − 0.06 × (T − 400) (T = 1,000: floor 39, **up to +$18**). REIN pays from T: **down to −$10.50** at T = 1,000, L = 1,300 | `ua HAVEN`, `ua REIN`, `ua ARCH`; `u MAT` |
| HavenBridge cedes x% of its layer (quota share) | `ov haven.slope 0.06×(1−x)` | e.g. 50%: slope 0.03, **up to +$27** at L ≥ 1,300 | `ua HAVEN`, `ua ARCH` |
| Meridian Re's limit / exposure capped at $C M | `ov rein.slope …` or `ov rein.top …` (judgment) | REIN's downside shrinks | `ua REIN` |
| Government covers x% of the port's repairs | `ov port.a 0.55×(1−x)` | PORT up by x × 0.55 × D; e.g. D = 50, x = 50%: **+$13.75** | `ua PORT`, `ua ARCH`; RECON unchanged (the work is the same): `u RECON` |
| Same for resorts / fisheries / grid / ferries | `ov isle.a …`, `ov aqua.a …`, `ov grid.a …`, `ov ferry.a …` | x × a × D | `ua SYM`, `ua ARCH` |
| Outage costs covered, or fares compensated | `ov grid.k 0` / `ov ferry.k 0` | GRID up by 0.05 × closed weight-seconds; FERRY by 0.06 × | `ua SYM`, `ua ARCH` |
| Outages will last longer (e.g. no spare transformers) | `ov grid.k …` up (judgment) | GRID down; GEN up (more outage) | `ua GRID`, `ua GEN`, `ua ARCH` |
| Port, resort or grid hardening (future storms do less) | `ov port.dmg 0.0055` (half rate), etc. | Only future storms; the engine reprices the rest of the season | `ua SYM`, `ua ARCH` |
| Tourism collapse / bookings boost for resorts | `ov isle.v0 …` | ISLE ± the change in V0 | `ua ISLE`, `ua ARCH` |
| Reconex adds crews / wins more contracts | `ov recon.b …` (a higher ceiling; judgment) | RECON up | `ua RECON`, `ua REBLD` |
| Reconex loses crews or a contract | `ov recon.b …` down | RECON down | `ua RECON`, `ua REBLD` |
| Materials price controls / supply shock | `ov mat.b …` | MAT ± | `ua MAT`, `ua REBLD` |
| GridLock buys its own generators | `ov gen.b …` down | GEN down | `ua GEN`, `ua REBLD` |

**Judgment calls.** The rebuilders' "capacity" headlines can map to the ceiling `b` or to the saturation scale `c`.
Default to `b` (proportional to the stated capacity change), and size down if unsure: `ua` for 15 s, then `u`.

## Rules

- Never `ua` a symbol whose effect you cannot write as a number.
- **Round 3 after about 700 s:** an announced symbol enters the final-report trade (S1-r3) only after `s1ok SYM`,
  confirmed by a second person.
- Log every override with the headline (the bot logs `override` events). Overrides reset at each new season.
