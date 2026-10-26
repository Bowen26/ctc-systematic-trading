"""Recommended live parameters in bot units: clip sizes in shares, and the REOPT S3 entry threshold cost term
thr_m * (walk(L/12) + 0.36*beta) and exit band (half-spread) in bp of mid, by phase, vs the playbook's 75 bp.
usage: python3 -I r12_params.py <SP> <cost_model.json>"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
import r_sim as R  # noqa: E402

SP, CMP = sys.argv[1], sys.argv[2]
B, CM = R.init(SP, CMP)
THR_M, CLIP = 2.0, 1 / 12
print("| sym | L | S3 clip L/12 (sh) | H1 clip L/8 (sh) | H1 clip L/4 (sh) | entry cost term bp: s3_start 80-120 | storm 120-540 | after 540-780 | exit band bp: s3_start | storm |")
print("|---|---|---|---|---|---|---|---|---|---|")
for s in R.SYMS:
    L = CM[s]["L"]
    beta = 48.0 if s in R.ETFS else 100.0
    th = lambda ph: THR_M * (R.interp(CM[s]["ph"][ph]["xs"], CM[s]["ph"][ph]["ys"], CLIP) + 0.36 * beta)
    hs = lambda ph: CM[s]["ph"][ph]["hs"]
    print(f"| {s} | {L} | {int(CLIP * L)} | {int(L / 8)} | {int(L / 4)} | {th('s3_start'):.0f} | {th('storm'):.0f} | {th('after'):.0f} | "
          f"{hs('s3_start'):.0f} | {hs('storm'):.0f} |")
print("playbook S3 cost term: h + gamma = 25 + 50 = 75 bp in every phase; exit band h = 25 bp")
