# fit:island

## headline

- I found the settlement formula and it holds on the holdout, to within cents: value = V0 - a*D - K*sum_s(w_s*T_s). D is the sum of the season's final damage_pct (pp); T_s is the real seconds site s was closed; w_s is the site weight from case.json.
- Fitted constants: PORT 100 / 0.55 / 0.02, ISLE 60 / 0.38 / 0, AQUA 35 / 0.20 / 0, GRID 50 / 0.24 / 0.05, FERRY 25 / 0.05 / 0.06 (V0 / $ per pp / $ per weight-second). The free fit on seasons 1-400 lands within 0.001 of each of these.
- A site is closed while I*exp(-(d/r)^2) > 0.35 (scale 1.0, threshold 0.35 from a scan). The impact messages confirm this independently: 2628 of 2631 match the first crossing at any company site on the island, 97-99% within 2 s.
- Holdout 401-600 settlement error, RMSE / max ($): PORT 0.0139/0.069, ISLE 0.0034/0.008, AQUA 0.0031/0.006, GRID 0.0138/0.046, FERRY 0.0187/0.094. ISLE and AQUA are exact up to price rounding. For the other three the leftover is about 0.45 s of timing error per crossing, because intensity is only seen every 2 s.
- Value lost per 1 pp of damage: PORT $0.55, ISLE $0.38, AQUA $0.20, GRID $0.24, FERRY $0.05. A full closure costs $2.40 (PORT), $6.00 (GRID, all substations) and $7.20 (FERRY, all terminals) per market hour. ISLE and AQUA have no closure cost.
- Storm order does not matter. The model is additive in reported damage: the three per-storm coefficients agree to 4 decimals, and a D^2 term adds nothing. A compounding model (1-d1)(1-d2)(1-d3) fails: RMSE 0.08 for FERRY up to 2.3 for PORT.
- Closure loss can be computed live from the 2 s storm feed. For FERRY it is the larger part of the loss (season mean $4.94 vs $1.88 from damage), and it is $3.71 for GRID. The damage part is only known when reports arrive. The preliminary report is noisy (prelim/final ratio 1.05, sd 0.33), so its PORT value error is $5.2 sd per storm. The final report is exact.

## params_file

/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/fits/island.json

## scripts

[
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/extract.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/fit_island.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/island_value.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/augment.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/common.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/sitefeat.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/noclose.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/fit_port.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/fit_port2.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/fit_gen.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/jointfit.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/knotfit.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/durlib.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/closure.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/evalfix.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/tune.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/checks.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/resid.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/resid2.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/schemes.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/noise.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/prelim.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/impact_check.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/explore1.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/explore2.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/explore3.py",
 "/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad/work/se/island/explore4.py"
]

## report

# Island-business valuation (PORT, ISLE, AQUA, GRID, FERRY)

Work dir: `SP/work/se/island/`. Parameters: `SP/work/se/fits/island.json`. I fit on seasons 1-400 and checked on the holdout, seasons 401-600. Every number below comes from scripts I ran; the main log is `island/fit_island.log`.

## 1. The answer

For each company and each season:

    settlement = V0 - a * D_total - K * sum over sites s of ( w_s * T_s )

- **D_total** is the sum, over the season's storms, of the company's `damage_pct` from the final reports (in pp; the manifest values are identical).
- **T_s** is the number of real seconds (storm age) during which site s was closed, summed over the 3 storms.
- **w_s** is the site weight from case.json.

A site is closed while

    wind = I * exp( -(d / r)^2 ) > 0.35

where d is the distance from the site to the storm centre, r is the storm radius and I is the storm intensity. All three come from the 2 s `storm` messages.

| co | V0 | a ($ per pp of damage) | a as % of V0 | K ($ per weight-second closed) | closure term? |
|---|---|---|---|---|---|
| PORT | 100 | 0.55 | 0.55% | 0.02 | yes (1 site, w = 1) |
| ISLE | 60 | 0.38 | 0.633% | 0 | **no** |
| AQUA | 35 | 0.20 | 0.571% | 0 | **no** |
| GRID | 50 | 0.24 | 0.48% | 0.05 | yes (5 substations) |
| FERRY | 25 | 0.05 | 0.20% | 0.06 | yes (4 terminals) |

The rounded values are not assumed. They are where the free fit on seasons 1-400 lands:

| co | V0 | a | K | per-site K / w_s |
|---|---|---|---|---|
| PORT | 99.9990 | 0.54995 | 0.02001 | 0.0200 |
| ISLE | 60.0004 | 0.38002 | -0.00000 | 0, 0, 0 |
| AQUA | 35.0001 | 0.20000 | 0.00000 | 0, 0, 0, 0 |
| GRID | 49.9993 | 0.23992 | 0.05002 | 0.0501, 0.0500, 0.0500, 0.0501, 0.0500 |
| FERRY | 25.0010 | 0.05008 | 0.05998 | 0.0601, 0.0600, 0.0598, 0.0599 |

When each site gets its own closure coefficient, they come out proportional to the case.json weights. So the cost of a closure is K times the site weight times the seconds closed.

The scouting puzzle is explained. GRID and FERRY have sites on every island, so every season has some damage and some closure. That is why their maximum settlements (48.57, 24.84) sit below V0 (50, 25).

### Value of a closure

Closure cost per real second, and per market hour (1 market hour = 120 real seconds):

| site | $ per real second | $ per market hour |
|---|---|---|
| PORT Deepwater Port | 0.020 | 2.40 |
| GRID Cascadia A / Cascadia B / Coral / Azure / Palm | 0.015 / 0.010 / 0.009 / 0.0085 / 0.0075 | 1.80 / 1.20 / 1.08 / 1.02 / 0.90 (all five: 6.00) |
| FERRY Cascadia / Coral / Azure / Palm | 0.018 / 0.015 / 0.012 / 0.015 | 2.16 / 1.80 / 1.44 / 1.80 (all four: 7.20) |

### Worked example: season 1

| co | D_total | weighted seconds closed | predicted | settlement |
|---|---|---|---|---|
| PORT | 84.55 | 113.6 | 100 - 46.50 - 2.27 = 51.226 | 51.24 |
| GRID | 26.87 | 63.5 | 50 - 6.45 - 3.18 = 40.375 | 40.38 |
| FERRY | 37.71 | 36.7 | 25 - 1.89 - 2.20 = 20.911 | 20.91 |
| ISLE | 22.34 | n/a | 60 - 8.49 = 51.511 | 51.51 |
| AQUA | 33.46 | n/a | 35 - 6.69 = 28.308 | 28.31 |

## 2. Holdout accuracy

Fixed rounded coefficients, seasons 401-600 (n = 200):

| co | train RMSE | train max | **test RMSE** | **test max** | test p95 abs error | test mean error | share within 5 cents |
|---|---|---|---|---|---|---|---|
| PORT | 0.0122 | 0.049 | **0.0139** | **0.069** | 0.029 | +0.001 | 0.99 |
| ISLE | 0.0031 | 0.009 | **0.0034** | **0.008** | 0.006 | 0.000 | 1.00 |
| AQUA | 0.0030 | 0.006 | **0.0031** | **0.006** | 0.006 | 0.000 | 1.00 |
| GRID | 0.0139 | 0.049 | **0.0138** | **0.046** | 0.028 | +0.001 | 1.00 |
| FERRY | 0.0191 | 0.093 | **0.0187** | **0.094** | 0.035 | -0.000 | 0.99 |

I also fed the raw holdout `feed.jsonl` files message by message through the live engine in `island_value.py`. It gives the same numbers.

**Why the residual is where it is:**
- **ISLE and AQUA** are exact up to settlement rounding. Rounding to 2 decimals alone gives an RMSE of about 0.0029.
- **PORT, GRID and FERRY:** residual RMSE grows with the number of times wind crosses the threshold. It works out to about 0.40-0.57 s of timing error per crossing (`resid2.py`). Seasons with no crossing show only rounding noise (PORT 0.0028).
- **The cause is intensity sampling.** Intensity is a random walk: its increments are uncorrelated, with sd 0.029 per 2 s, and the feed only shows it every 2 s. The simulator itself ticks every 1 s: impact ages are integers and half of them are odd.
- **This residual cannot be removed.** Hermite interpolation of position and sub-sampling at 0.25 s or 1 s (`schemes.py`) all land at the same level. Holding intensity constant between observations is worse (RMSE 0.020-0.029).

## 3. How it was found

1. **Damage alone.** I took seasons where no site came within 1.15 radii of a storm (`noclose.py`). Settlement there is exactly linear in D_total: PORT 100 - 0.55*D (190 seasons, D up to 55.7), ISLE 60 - 0.38*D (RMSE 0.0029), AQUA 35 - 0.20*D (RMSE 0.0030). Adding D^2 or D^3 terms changes nothing.
2. **Extra loss.** The rest of PORT's loss is zero unless the storm passes within about one radius of the port, and then it grows with time spent close (`explore3.py`).
3. **Closure boundary.**
   - A first fit with "closed when d/r < c * I^p", counting 2 s observations, gave c = 1.02, p = 0.45 and a PORT test RMSE of 0.042.
   - I then fit a boundary u < B(I) with no assumed shape: piecewise linear, knots every 0.2 in I, fit jointly on PORT, GRID and FERRY (`knotfit.py`). B(I) came out as sqrt(ln(I/0.35)):

     | I | 0.4 | 0.6 | 0.8 | 1.0 | 1.2 | 1.4 | 1.6 | 1.8 |
     |---|---|---|---|---|---|---|---|---|
     | fitted B(I) | 0.395 | 0.731 | 0.923 | 1.020 | 1.107 | 1.187 | 1.220 | 1.289 |
     | sqrt(ln(I/0.35)) | 0.365 | 0.734 | 0.909 | 1.025 | 1.110 | 1.177 | 1.233 | 1.280 |

   - That is exactly the closure rule I * exp(-(d/r)^2) > 0.35.
4. **Fine scan.** I scanned the wind scale s from 0.98 to 1.02 and the threshold from 0.33 to 0.37 (`fit_island.py` step 1). The minimum is at s = 1.00, threshold = 0.35, with train RMSE PORT 0.0122, GRID 0.0139, FERRY 0.0191. The next best pair, (1.01, 0.36), gives 0.015 / 0.017 / 0.023.
5. **Closure time estimate.** Between consecutive observations I interpolate (wind - 0.35) linearly to find the crossing time ("lin" scheme). Simply counting observations over the threshold and multiplying by 2 s is slightly worse: test RMSE PORT 0.0157, GRID 0.0173, FERRY 0.0252.
6. **Independent check with impact messages** (`impact_check.py`). The `impact` message is sent at the first 1 s tick when wind at any company site on the island exceeds 0.35.
   - 2628 of 2631 impacts match.
   - The modelled first-crossing observation is 0 s after the impact age in 1298 cases and 1 s after in 1283; 2597 are within 2 s.
   - The model never crosses without an impact message.
   - Using the island polygon instead of the sites fits far worse (only 280 within 2 s, 525 false crossings).

## 4. Functional-form tests

Train / test RMSE in $, all with the closure term (`fit_island.py` step 3):

| form | PORT | ISLE | AQUA | GRID | FERRY |
|---|---|---|---|---|---|
| additive V0 - a*sum(d) | 0.0122/0.0139 | 0.0031/0.0034 | 0.0029/0.0032 | 0.0139/0.0138 | 0.0191/0.0186 |
| separate a1, a2, a3 per storm (order test) | 0.0122/0.0139 | 0.0031/0.0034 | 0.0029/0.0032 | 0.0138/0.0140 | 0.0191/0.0187 |
| additive + D^2 | 0.0122/0.0140 | 0.0031/0.0034 | 0.0029/0.0032 | 0.0139/0.0138 | 0.0191/0.0186 |
| compounding 1-prod(1-d) | 2.3155/2.3300 | 0.5331/0.5282 | 0.5627/0.6019 | 0.3197/0.3549 | 0.0790/0.0819 |
| additive, no closure term | 0.7296/0.8295 | 0.0031/0.0034 | 0.0029/0.0031 | 0.9607/0.9890 | 1.7220/1.6094 |

**Storm order does not matter.** The per-storm coefficients are:
- PORT -0.54993 / -0.54997 / -0.54994
- ISLE -0.38003 / -0.38002 / -0.38001
- AQUA -0.19999 / -0.19999 / -0.20001
- GRID -0.23982 / -0.23995 / -0.23997
- FERRY -0.05007 / -0.05012 / -0.05004

The reported percentages are additive, which means they are percentages of the original asset value. The largest season sum is 99.48 (PORT, season 313), so cumulative damage looks bounded at 100%, but no cap or floor is ever binding in the data.

**Outage term.** GRID's outage is just substation closure time; there is no extra outage tail after the storm. Any repair-time outage proportional to damage would be absorbed into a, and is indistinguishable from it.

**What the company texts mean:**
- ISLE "bookings change with the damage" shows up only as the larger damage coefficient (0.633% of V0 per pp).
- AQUA "boats can go to a shelter" means no closure cost.
- FERRY "breaks only a small part" means a tiny a (0.05) and a large K.

## 5. What can and cannot be known in real time

**Closure loss is observable live.** Each storm message updates the closure seconds for every site; `IslandValuer.on_storm` does this, and `closed_sites()` lists sites that are closed right now. The closure part of value is final at storm end (age 480, real second 540), accurate to about 1-2 cents.

Closure loss per storm:

| co | mean | p50 | p90 | p99 | max | share of storms with any closure |
|---|---|---|---|---|---|---|
| PORT | 0.544 | 0.000 | 2.084 | 2.850 | 3.597 | 0.32 |
| GRID | 1.235 | 1.212 | 2.325 | 3.044 | 4.292 | 0.88 |
| FERRY | 1.648 | 1.230 | 4.178 | 5.223 | 5.539 | 0.76 |

**Size of each part over a whole season (all 600 seasons):**

| co | damage loss mean / sd / max | closure loss mean / sd / max | correlation between the two |
|---|---|---|---|
| PORT | 26.12 / 17.54 / 54.71 | 1.63 / 1.58 / 8.53 | 0.88 |
| GRID | 9.73 / 3.02 / 17.06 | 3.71 / 1.41 / 8.12 | 0.73 |
| FERRY | 1.88 / 0.69 / 3.85 | 4.94 / 2.65 / 13.66 | 0.77 |

ISLE (9.83 / 6.27 / 32.35) and AQUA (7.83 / 3.89 / 17.36) have no closure loss.

FERRY's value is mostly closure, so it is mostly knowable from the track while the storm is still running. GRID's is partly knowable.

**Damage loss is only known from reports**, or from a nowcast of damage from the track (the physics agent's job).
- The preliminary report arrives at real second 570 and is noisy. Preliminary minus final is +0.5 to +0.8 pp on average. The preliminary/final ratio (for storms with final damage over 1 pp) has mean 1.05 and sd 0.33.
- Using the preliminary report gives a value error per storm (sd / max) of: PORT 5.16 / 59.8, ISLE 1.79 / 18.9, AQUA 1.28 / 15.8, GRID 1.32 / 11.7, FERRY 0.28 / 2.4.
- The final report (real second 740, 40 s before the close) is exact. With it, the formula gives settlement to the cents above. Earlier rounds' final reports carry over, so in round 3 only the round-3 storm's damage is uncertain until 15:40.

**Value change per 1 pp of damage:** PORT $0.55, ISLE $0.38, AQUA $0.20, GRID $0.24, FERRY $0.05 per share. In ARCH (1 PORT, 1 ISLE, 2 AQUA, 1 GRID, 2 FERRY) the damage sensitivities add with those multiplicities.

## 6. Robustness and caveats

- **Feed gaps.** I recomputed closure from only every 4th or 10th second of observations, holdout, fixed coefficients (test RMSE / max):

  | spacing | PORT | GRID | FERRY |
  |---|---|---|---|
  | every 2 s (normal) | 0.0139 / 0.069 | 0.0138 / 0.046 | 0.0187 / 0.094 |
  | every 4 s | 0.0183 / 0.066 | 0.0207 / 0.059 | 0.0293 / 0.136 |
  | every 10 s | 0.040 / 0.21 | 0.033 / 0.13 | 0.048 / 0.29 |

  The engine interpolates across any gap. Track messages carry position and radius but **no intensity**, so they cannot fully fill a gap. After a reconnect, interpolate intensity linearly between the surrounding storm messages.
- **Live-season announcements** may change V0, a, K, the threshold or the site list. The engine keeps them as explicit parameters (`PARAMS` in `island_value.py`) so the bot can patch them.
- **Error floor.** The remaining error, about 0.45 s per threshold crossing, cannot be reduced from public data, because the 1 s intensity path between the 2 s observations is never published. The first crossing on each island could be pinned to the exact tick using the impact message's age; I did not implement this, and it is only worth about a cent.
- **Rounding.** Final damage_pct values are rounded to 2 decimals. That costs at most a * 0.005 per storm.
- **What I did not do.** This work does not forecast future damage or closure during a storm; that needs the physics agent's track and damage model. Plugging their expected closure seconds and expected damage into the formula gives a fair value.

## 7. Files

- `extract.py`: per-season cache (`cache.json`) of storm observations, impacts, preliminary/final reports and settlements, built from the data folder (read-only).
- `fit_island.py`: the full pipeline (boundary scan, free fits, form tests, fixed-coefficient holdout, sparse-observation test, streaming replay). Writes `fits/island.json` and `closure_best.json`.
- `augment.py`: adds the real-time diagnostics (preliminary error, per-storm closure distribution, season-1 breakdown, impact check) to island.json.
- `island_value.py`: standard-library live engine, `IslandValuer(case).on_message(msg)` then `.value(co, pending={co: pp})`. It removes duplicate messages by seq and uses final reports, falling back to preliminary ones.
- Exploration and diagnostics: `noclose.py`, `explore1-4.py`, `fit_port*.py`, `fit_gen.py`, `jointfit.py`, `knotfit.py`, `tune.py`, `closure.py`, `evalfix.py`, `checks.py`, `resid*.py`, `schemes.py`, `noise.py`, `prelim.py`, `impact_check.py`, plus the helpers `common.py` and `durlib.py`.
- Run with `python3 -I`, standard library only, no network.

