"""Run the team's testnet/check_recording.py under python3 -I (which does not put the script dir on sys.path).

    python3 -I run_check.py <testnet dir> <recording.jsonl> <research history dir>
"""
import sys

tdir, rec, hist = sys.argv[1:4]
sys.path.insert(0, tdir)
import check_recording  # noqa: E402

sys.argv = ["check_recording.py", rec, hist]
raise SystemExit(check_recording.main())
