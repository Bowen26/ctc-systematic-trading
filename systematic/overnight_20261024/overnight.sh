#!/bin/zsh
# Overnight run, 24 Oct 2026. Read-only on the testnet: the bot runs in DRY-RUN (sends no orders).
#  1. keep the Mac awake while this script runs (caffeinate; the lid must stay open, on AC power)
#  2. record every testnet season (books, quotes, trades, news)
#  3. run the bot in dry-run on the final config as a soak test; restart it if it ever exits
#  4. every 20 min, score each finished season on the recorded books (final config vs old S3 timing)
#  5. compare S3 from 80 s vs 120 s on the 200 held-out research seasons (low priority)
# Stops by itself at END; then stops the bot and recorder cleanly and does a final scoring pass.
set -u
S="/Users/bowentian/Documents/Fun Stuff/CTC2026/systematic"
O="$S/overnight_20261024"
BOT="$S/bot"
PY="$S/.venv/bin/python"
HIST="$HOME/Downloads/hurricane-season-research/history"
CACHE="$S/research/fv_cache"
END=$(date -j -f "%Y-%m-%d %H:%M" "2026-10-24 08:30" +%s)
LOG="$O/progress.log"
mkdir -p "$O"
say() { echo "$(date +%H:%M:%S) $*" >> "$LOG"; }

caffeinate -i -s -w $$ &
say "start (pid $$), caffeinate on"

start_recorder() {
  ( cd "$S/testnet" && exec "$PY" -I -u record_all.py --out "$O/recording.jsonl" ) >> "$O/recorder.log" 2>&1 &
  REC_PID=$!; say "recorder started (pid $REC_PID)"
}
BOT_N=0
start_bot() {
  BOT_N=$((BOT_N + 1))
  ( cd "$BOT" && exec "$PY" -I -u run_bot.py --host cornelltradingcompetition.org --key-file "$HOME/.ctc_bot_key" \
      --run-dir "runs/overnight_20261024_$BOT_N" < /dev/null ) >> "$O/bot_$BOT_N.log" 2>&1 &
  BOT_PID=$!; say "dry-run bot started (pid $BOT_PID, run dir runs/overnight_20261024_$BOT_N)"
}
start_recorder
start_bot

# research comparison: S3 start 20 (80 s) vs 60 (120 s) on the 200 held-out seasons
(
  cd "$BOT"
  for SS in 60 20; do
    nice -n 19 "$PY" -I sim/run_research.py --history "$HIST" --fv-cache "$CACHE" --seasons 401-600 --workers 6 \
      --set "strategies.s3.start=[\"storm_start\",$SS]" --out "sim/out/overnight/research_s3start_$SS" \
      > "$O/research_s3start_$SS.log" 2>&1
    echo "$(date +%H:%M:%S) research S3 start $SS done" >> "$LOG"
  done
  "$PY" -I - "$BOT/sim/out/overnight" "$O/research_comparison.md" <<'EOF'
import json, os, statistics as st, sys
root, out = sys.argv[1], sys.argv[2]
def load(ss):
    r = json.load(open(os.path.join(root, f"research_s3start_{ss}", "report.json")))
    return {it["season"]: it["pnl"] for it in r["variants"]["base"]["seasons"] if it.get("season") is not None}, \
           r["variants"]["base"]["summary"]
a, sa = load(20); c, sc = load(60)
d = [c[k] - a[k] for k in a if k in c]
se = st.stdev(d) / len(d) ** 0.5
lines = ["# S3 start: 120 s (final) vs 80 s (old), 200 held-out research seasons (401-600)", "",
         f"| | mean | sd | p5 | hit |", "|---|---|---|---|---|",
         f"| 120 s (final) | {sc['mean']:,.0f} | {sc['sd']:,.0f} | {sc['p5']:,.0f} | {sc['hit']:.2f} |",
         f"| 80 s (old) | {sa['mean']:,.0f} | {sa['sd']:,.0f} | {sa['p5']:,.0f} | {sa['hit']:.2f} |", "",
         f"Paired difference (120 s minus 80 s): **{st.mean(d):+,.0f} per season** (se {se:,.0f}); "
         f"120 s better in {sum(1 for x in d if x > 0)}/{len(d)} seasons.",
         "", "Decision rule set beforehand: keep 120 s unless it loses by more than 2 se."]
open(out, "w").write("\n".join(lines) + "\n")
EOF
  echo "$(date +%H:%M:%S) research comparison written" >> "$LOG"
) &

# supervise, and score finished seasons every 20 minutes
i=0
while [ "$(date +%s)" -lt "$END" ]; do
  sleep 60
  i=$((i + 1))
  kill -0 "$BOT_PID" 2>/dev/null || { say "bot exited; restarting"; start_bot; }
  kill -0 "$REC_PID" 2>/dev/null || { say "recorder exited; restarting"; start_recorder; }
  if [ $((i % 20)) -eq 0 ]; then
    nice -n 10 "$PY" -I "$O/score_seasons.py" "$BOT" "$HIST" "$O/recording.jsonl" "$O" >> "$O/scorer.log" 2>&1
    say "scored: $(tail -1 "$O/scorer.log")"
  fi
done
say "END reached: stopping bot and recorder"
kill -INT "$BOT_PID" "$REC_PID" 2>/dev/null
sleep 15
nice -n 10 "$PY" -I "$O/score_seasons.py" "$BOT" "$HIST" "$O/recording.jsonl" "$O" >> "$O/scorer.log" 2>&1
say "final scoring: $(tail -1 "$O/scorer.log")"
wait
say "ALL_DONE"
