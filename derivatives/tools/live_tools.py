"""Live helper (calculator only, places no orders).
Usage:
  python3 -I live_tools.py weights LOG.csv OIL a1 a2 a3
      -> per-analyst bias / MAE from LOG.csv rows for that underlying, then a combined IV for new estimates a1..a3
  python3 -I live_tools.py decode S T_days sigma0_pct sigma1_pct x1 x2 x3
      -> ranks candidate 'IV-linked quantity' transforms by fit to analyst numbers x1..x3
  python3 -I live_tools.py invert NAME S T_days sigma0_pct x
      -> converts analyst number x back to IV% under transform NAME
  python3 -I live_tools.py selftest
LOG.csv columns: event,underlying,a1,a2,a3,true_iv   (IV in vol points, e.g. 112.5)
"""
import csv
import math
import sys
import statistics


def N(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(S, K, sig, T, kind):
    sT = sig * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sig * sig * T) / sT
    d2 = d1 - sT
    if kind == 'C':
        return S * N(d1) - K * N(d2)
    return K * N(-d2) - S * N(-d1)


def transforms(S, T, s0):
    """name -> f(sigma_decimal) giving the analyst quantity."""
    K = S  # straddle/call struck at spot (approx; use nearest strike if needed)
    return {
        'iv_pct': lambda s: 100 * s,
        'iv_decimal': lambda s: s,
        'variance_dec': lambda s: s * s,
        'variance_pct2': lambda s: (100 * s) ** 2,
        'daily_sd_pct': lambda s: 100 * s / math.sqrt(252),
        'weekly_sd_pct': lambda s: 100 * s * math.sqrt(5 / 252),
        'monthly_sd_pct': lambda s: 100 * s * math.sqrt(21 / 252),
        'sd_to_expiry_pct': lambda s: 100 * s * math.sqrt(T),
        'sd_to_expiry_$': lambda s: S * s * math.sqrt(T),
        'atm_straddle_$': lambda s: bs(S, K, s, T, 'C') + bs(S, K, s, T, 'P'),
        'atm_call_$': lambda s: bs(S, K, s, T, 'C'),
        'iv_change_pts': lambda s: 100 * (s - s0),
        'iv_ratio': lambda s: s / s0,
        'iv_pct_change': lambda s: 100 * (s / s0 - 1),
        'ln_iv': lambda s: math.log(s),
        'variance_change_dec': lambda s: s * s - s0 * s0,
    }


def invert(f, x, lo=0.01, hi=6.0):
    flo, fhi = f(lo), f(hi)
    increasing = fhi > flo
    for _ in range(200):
        m = 0.5 * (lo + hi)
        fm = f(m)
        if (fm < x) == increasing:
            lo = m
        else:
            hi = m
    return 0.5 * (lo + hi)


def cmd_decode(S, Tdays, s0pct, s1pct, xs):
    T = Tdays / 252.0
    s0, s1 = s0pct / 100.0, s1pct / 100.0
    rows = []
    for name, f in transforms(S, T, s0).items():
        target = f(s1)
        scale = max(abs(target), 1e-9)
        rel = [abs(x - target) / scale for x in xs]
        # also: what IV does each analyst imply under this transform?
        try:
            implied = [100 * invert(f, x) for x in xs]
        except Exception:
            implied = []
        rows.append((statistics.median(rel), name, target, implied))
    rows.sort()
    print("true IV now %.2f%% (pre-event %.2f%%), S=%.2f, T=%.1fd" % (s1pct, s0pct, S, Tdays))
    print("%-20s %12s %10s   implied IV%% per analyst" % ("transform", "f(true IV)", "med.relerr"))
    for rel, name, target, implied in rows:
        print("%-20s %12.4f %10.3f   %s" % (name, target, rel, " ".join("%.1f" % v for v in implied)))
    print("ratios x/IV%%: %s   x/IV%%^2: %s" % (
        " ".join("%.4f" % (x / s1pct) for x in xs), " ".join("%.6f" % (x / s1pct ** 2) for x in xs)))


def cmd_invert(name, S, Tdays, s0pct, x):
    f = transforms(S, Tdays / 252.0, s0pct / 100.0)[name]
    print("%s = %g  ->  IV = %.2f%%" % (name, x, 100 * invert(f, x)))


def load_log(path, und):
    out = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            if r['underlying'].strip().upper() != und.upper() or not r.get('true_iv'):
                continue
            out.append(([float(r['a1']), float(r['a2']), float(r['a3'])], float(r['true_iv'])))
    return out


def cmd_weights(path, und, new):
    hist = load_log(path, und)
    print("%d logged events for %s" % (len(hist), und))
    biases, maes = [], []
    for i in range(3):
        errs = [h[0][i] - h[1] for h in hist]
        b = statistics.mean(errs) if errs else 0.0
        mae = statistics.mean(abs(e) for e in errs) if errs else 10.0
        biases.append(b)
        maes.append(mae)
        print("  Analyst %d: errors %s | bias %+.2f | MAE %.2f" % (i + 1, " ".join("%+.1f" % e for e in errs), b, mae))
    w = [1.0 / (m + 1.0) ** 2 for m in maes]
    corr = [new[i] - (biases[i] if len(hist) >= 2 else 0.0) for i in range(3)]
    wmean = sum(wi * c for wi, c in zip(w, corr)) / sum(w)
    best = min(range(3), key=lambda i: maes[i])
    print("New estimates %s -> median %.2f | bias-corrected inverse-MAE^2 mean %.2f | best single (A%d) %.2f" % (
        new, statistics.median(new), wmean, best + 1, corr[best]))
    print("Spread of estimates (max-min) %.2f pts: %s" % (max(new) - min(new), "LOW confidence" if max(new) - min(new) > 15 else "ok"))


def selftest():
    # Synthetic: true IV 118%, pre-event 100%, analysts report daily sd% with noise
    S, Td, s0, s1 = 100.0, 18, 100.0, 118.0
    xs = [118 / math.sqrt(252) + d for d in (0.05, -0.2, 0.6)]
    print("--- selftest: analysts report daily sd%% (true %.3f) ---" % (118 / math.sqrt(252)))
    cmd_decode(S, Td, s0, s1, xs)
    print("--- selftest: analysts report change in IV (true +18) ---")
    cmd_decode(S, Td, s0, s1, [17.0, 19.5, 30.0])
    print("--- selftest: invert ---")
    cmd_invert('atm_straddle_$', 100, 18, 100, 25.0)
    cmd_invert('variance_dec', 100, 18, 100, 1.30)


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a or a[0] == 'selftest':
        selftest()
    elif a[0] == 'decode':
        cmd_decode(float(a[1]), float(a[2]), float(a[3]), float(a[4]), [float(x) for x in a[5:]])
    elif a[0] == 'invert':
        cmd_invert(a[1], float(a[2]), float(a[3]), float(a[4]), float(a[5]))
    elif a[0] == 'weights':
        cmd_weights(a[1], a[2], [float(x) for x in a[3:6]])
