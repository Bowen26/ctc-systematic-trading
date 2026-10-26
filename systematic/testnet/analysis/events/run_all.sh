#!/bin/sh
# Re-run every event-study script; outputs go to <script>.out next to the scripts.
# usage: sh run_all.sh <recording.jsonl> <history dir with season-224 and season-225>
E="$(cd "$(dirname "$0")" && pwd)"
REC="$1"; H="$2"
for s in clock_fit book_vs_cons layers trade_survey open_levels open_trades formation drift event_layers stale_profile reports s1r3 etf_lag refill_impact others gap_convergence; do
  python3 -I "$E/$s.py" "$REC" "$H/season-224" "$H/season-225" > "$E/$s.out" 2>&1 || echo "FAILED $s"
done
python3 -I "$E/series.py" "$REC" "$H/season-224" "$H/season-225" "$E/series" > "$E/series.out" 2>&1 || echo "FAILED series"
echo done
