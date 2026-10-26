#!/bin/sh
# Writer checks for TESTNET_FINDINGS.md. usage: sh run_all.sh <recording.jsonl> <SP>   (SP = scratchpad with data/ and work/)
REC="$1"; SP="$2"; H="$SP/data/hurricane-season-research/history"; A="$(cd "$(dirname "$0")/.." && pwd)"; E="$A/../../engine"
cd "$A/writer" || exit 1
python3 -I ../feed/engine_sens.py "$REC" "$H" "$E" engine_params_fast.json 226 600 w00_engine_sens_fast.json > w00_engine_sens_fast.out
python3 -I ../feed/snapshot_resync_check.py "$REC" "$H" "$E" > w00_snapshot_resync.out
python3 -I ../feed/doublecount_check.py "$REC" "$H" "$E" > w00_doublecount.out
python3 -I w01_checks.py "$REC" "$H" ../verify > w01_checks.out
python3 -I w02_sens.py "$SP" ../rebacktest ../books/cost_model.json holdout 401 600 > w02_sens_holdout.out
python3 -I w02_sens.py "$SP" ../rebacktest ../books/cost_model.json loso 1 400 > w02_sens_train.out
python3 -I w03_open.py "$REC" ../verify > w03_open.out
python3 -I w04_crowding.py "$REC" ../verify > w04_crowding.out
python3 -I w05_params.py ../books/cost_model.json > w05_params.out
python3 -I w06_t50.py ../verify/v04_h1.out > w06_t50.out
