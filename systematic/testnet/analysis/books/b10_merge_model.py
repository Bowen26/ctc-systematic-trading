"""Merge the follow-on-clip penalty (b06b) and replenishment summary (b06) into cost_model.json, and print the
summary table used in the report.
usage: python3 -I b10_merge_model.py cost_model.json reclip.json replenish.json"""
import sys, json
cm_path, rc_path, rp_path = sys.argv[1:4]
cm = json.load(open(cm_path)); rc = json.load(open(rc_path)); rp = json.load(open(rp_path))
SY = [k for k in cm if not k.startswith("_")]
for s in SY:
    r = rc.get(s, {})
    pre = r.get("-0.25", {}).get("mean"); p2 = r.get("2", {}).get("mean"); p8 = r.get("8", {}).get("mean")
    cm[s]["followon_clip"] = {"n_events": r.get("-0.25", {}).get("n"), "cost_Lq_before_bps": pre, "cost_Lq_plus2s_bps": p2,
                              "cost_Lq_plus8s_bps": p8, "penalty_2s_bps": (p2 - pre) if (p2 is not None and pre is not None) else None,
                              "source": "b06b_reclip.py: L/4 walk cost vs pre-trade mid after another taker's same-side order >= 0.2 L (120-770 s)"}
    st = rp.get(s, {}).get("storm", {})
    cm[s]["replenish_storm"] = {k: st.get(k) for k in ("n", "touch_100", "same_px_100", "depth50_100", "touch_refill_px_bps")}
pool = rc.get("_pooled", {})
cm["_pooled_followon_penalty_bps"] = {"before": pool.get("-0.25", {}).get("mean"), "plus2s": pool.get("2", {}).get("mean"),
                                      "plus8s": pool.get("8", {}).get("mean"), "plus2s_net_of_consensus": pool.get("2", {}).get("net_mean")}
cm["_recommended_fill_model"] = {
    "per_share_cost_bps": "interpolate cm[sym][phase]['table'] at x = clip/L (vs the book mid; book mid != consensus, see report)",
    "phase_choice": "open 0-3 s: do not trade; h1_entry 50-58; h1_exit 72-80; s3_start 80-120; storm 120-540; after 540-780",
    "followon": "add cm[sym]['followon_clip']['penalty_2s_bps'] to each further same-side clip within ~8 s of the previous one",
    "permanent_impact": "shift the reference mid by cm[sym]['permanent_impact_bps_per_L']['storm_10s'] * (net signed shares / L)",
}
json.dump(cm, open(cm_path, "w"), indent=1)
print(f"{'sym':6s} {'L':>4s} | storm: h10sh  L/8   L/4   L/2  lin-gamma  quad05(h,g1,g2)        | after: h10sh  L/4 | h1_exit L/4 | follow-on +2s | impact/L 10s")
for s in SY:
    m = cm[s]; st = m["storm"]; af = m["after"]; ex = m["h1_exit"]
    c = st["clips"]; ca = af["clips"]
    fo = m["followon_clip"]; im = m.get("permanent_impact_bps_per_L", {})
    print(f"{s:6s} {m['L']:4d} | {c['10sh']['mean']:10.1f} {c['L/8']['mean']:5.1f} {c['L/4']['mean']:5.1f} {c['L/2']['mean']:5.1f} {st['lin05']['gamma_bps_per_x']:8.0f}"
          f"   ({st['quad05']['h']:5.1f},{st['quad05']['g1']:5.0f},{st['quad05']['g2']:5.0f}) | {ca['10sh']['mean']:10.1f} {ca['L/4']['mean']:5.1f} |"
          f" {ex['clips']['L/4']['mean']:9.1f}   | {fo['penalty_2s_bps'] if fo['penalty_2s_bps'] is not None else float('nan'):+8.1f} (n={fo['n_events']}) |"
          f" {im.get('storm_10s', float('nan')):+6.1f}")
