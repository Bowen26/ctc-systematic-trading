#!/bin/sh
# Fix verification for TESTNET_FINDINGS.md "Fix verification". usage: sh run_fixcheck.sh <recording.jsonl> <research history dir>
# Runs the feed report's checks against the original engine (fairvalue_orig.py) and the patched one (fairvalue.py),
# the clean-feed identity check (seasons 401-405) and the two-connection feed merge test.
REC="$1"; H="$2"; A="$(cd "$(dirname "$0")/.." && pwd)"; E="$A/../../engine"; B="$A/../../bot_skeleton"
cd "$A/fixcheck" || exit 1
for v in orig patched; do
  f=fairvalue.py; [ "$v" = orig ] && f=fairvalue_orig.py
  python3 -I ../feed/snapshot_resync_check.py "$REC" "$H" "$E" "$f" > "snapshot_resync_$v.out" &
  python3 -I ../feed/doublecount_check.py "$REC" "$H" "$E" "$f" > "doublecount_$v.out" &
  python3 -I ../feed/engine_sens.py "$REC" "$H" "$E" engine_params_fast.json 226 600 "engine_sens_fast_$v.json" "$f" > "engine_sens_fast_$v.out" &
done
wait
python3 -I clean_feed_identity.py "$H" "$E" "$REC" 401 405 > clean_feed_identity.out
python3 -I "$B/test_feed_merge.py" "$REC" > feed_merge_test.out
