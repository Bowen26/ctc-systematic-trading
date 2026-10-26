"""(a) Does a snapshot-recovered official advisory reset the pattern posterior? Runs the team engine
(read-only import) over the recorded feed in arrival order and logs the posterior key / content around
every snapshot that carries an advisory the engine had not seen live.
usage: python3 -I v02_snapshot_bug.py <recording> <engine_dir>
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
from vlib import *
rec, edir = sys.argv[1], sys.argv[2]
sys.path.insert(0, edir)
import importlib.util
spec = importlib.util.spec_from_file_location("se_fair", os.path.join(edir, "fairvalue.py"))
E = importlib.util.module_from_spec(spec); spec.loader.exec_module(E)

F = load_rec(rec)
st_ = E.SeasonState(params=os.path.join(edir, "engine_params_fast.json"))
season_date = None
events = 0
watch = 0
for fr in F:
    if fr["type"] != "news":
        continue
    m = fr["message"]
    d = m.get("time", "")[:10]
    key = DATE_ROUND.get(d)
    if key and key[0] != season_date and m.get("type") != "snapshot":
        st_.reset(); season_date = key[0]
    if m.get("type") == "snapshot":
        adv = m.get("advisory") or {}
        r = m.get("round")
        recd = st_.rounds.get(r)
        num = int(adv.get("number", 0)) if adv else 0
        new = adv and num and recd is not None and num not in recd.official
        if new:
            pre = (st_.post.key, len(st_.post.obs), len(getattr(st_.post, "labels", []) or []))
            st_.on_message(m)
            post = (st_.post.key, len(st_.post.obs))
            print("snapshot at %s (round %s) recovers official #%d: posterior key/obs before %s, after %s" % (
                m["time"], r, num, pre[:2], post))
            watch = 3
            continue
    st_.on_message(m)
    if watch and m.get("type") == "storm":
        print("   next live storm obs age %s: posterior key %s, obs held %d" % (m["age"], st_.post.key, len(st_.post.obs)))
        watch -= 1
