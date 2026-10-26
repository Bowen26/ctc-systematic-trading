"""Print the markdown tables used in the report from books_phases.json (b02), cost_model.json (b03+b10),
trades.json (b05), replenish.json (b06). usage: python3 -I b12_report_tables.py <dir with the json files>"""
import sys, os, json
d = sys.argv[1]
P = json.load(open(os.path.join(d, "books_phases.json"))); C = json.load(open(os.path.join(d, "cost_model.json")))
T = json.load(open(os.path.join(d, "trades.json"))); R = json.load(open(os.path.join(d, "replenish.json")))
SY = ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "RECON", "MAT", "GEN", "HAVEN", "REIN", "ARCH", "REBLD"]
print("| Sym | L | touch | hs pre | hs early (p90) | hs storm (p90) | hs after | hs rep 568-600 / 738-760 | storm depth 0.25/0.5/1/2% | storm upd/s |")
print("|---|---|---|---|---|---|---|---|---|---|")
for s in SY:
    p = P[s]; L = C[s]["L"]
    hs = lambda ph: p[ph]["half_spread_bps"]
    dep = "/".join(f"{p['storm']['depth_within'][k]['median']:.0f}" for k in ("0.25%", "0.5%", "1%", "2%"))
    print(f"| {s} | {L} | {p['storm']['touch_bid']['median']:.0f} | {hs('pre')['median']:.1f} | {hs('early')['median']:.0f} ({hs('early')['p90']:.0f}) | "
          f"{hs('storm')['median']:.1f} ({hs('storm')['p90']:.0f}) | {hs('after')['median']:.1f} | {hs('rep_prelim')['median']:.1f} / {hs('rep_final')['median']:.1f} | {dep} | {p['storm']['updates_per_s']:.2f} |")
print()
print("| Sym | storm 10sh | L/8 | L/4 | L/2 | L | quad05 h, g1, g2 | after L/4 | h1_entry L/4 buy/sell | h1_exit L/4 buy/sell | s3_start L/4 | follow-on +2 s | impact /L (10 s) |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for s in SY:
    m = C[s]; st = m["storm"]["clips"]; q = m["storm"]["quad05"]
    he = m["h1_entry"]["clips"]["L/4"]; hx = m["h1_exit"]["clips"]["L/4"]
    fo = m["followon_clip"]["penalty_2s_bps"]; im = m["permanent_impact_bps_per_L"]
    print(f"| {s} | {st['10sh']['mean']:.0f} | {st['L/8']['mean']:.0f} | {st['L/4']['mean']:.0f} | {st['L/2']['mean']:.0f} | {st['L']['mean']:.0f} | "
          f"{q['h']:.0f}, {q['g1']:.0f}, {q['g2']:.0f} | {m['after']['clips']['L/4']['mean']:.0f} | {he['buy_mean']:.0f}/{he['sell_mean']:.0f} | "
          f"{hx['buy_mean']:.0f}/{hx['sell_mean']:.0f} | {m['s3_start']['clips']['L/4']['mean']:.0f} | {fo:+.0f} (n={m['followon_clip']['n_events']}) | "
          f"{im['storm_10s']:+.0f} ± {im['storm_10s_se']:.0f} |")
print()
print("| Sym | storm orders/s | size p50 (%L) / p90 | modal trade size | buy share pre / early / storm / after | touch refill 100%: median s, <=2 s | refill px vs old touch (bp) | same-price refill 100%: median s |")
print("|---|---|---|---|---|---|---|---|")
for s in SY:
    t = T[s]; r = R[s]["storm"]
    bs = "/".join(f"{100*t[ph]['share_buy_vol']:.0f}" for ph in ("pre", "early", "storm", "after"))
    print(f"| {s} | {t['storm']['orders_per_s']:.2f} | {t['storm']['order_size']['p50']:.0f} ({100*t['storm']['order_size_over_L_p50']:.1f}%) / {t['storm']['order_size']['p90']:.0f} | "
          f"{t['size_modes'][0][0]} (x{t['size_modes'][0][1]}) | {bs} | {r['touch_100']['km_median_s']:.2f}, {100*r['touch_100']['within_2s']:.0f}% | "
          f"{r['touch_refill_px_bps']['median']:+.0f} | {r['same_px_100']['km_median_s'] if r['same_px_100']['km_median_s'] is not None else float('nan'):.1f} |")
