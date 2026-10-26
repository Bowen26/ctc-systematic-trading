"""Wire-rumour headline templates -> outlook, from the research feeds (to recover a lost wire from snapshot.headlines).

    python3 -I wire_templates.py <research history dir> [n_seasons]
"""
import collections
import json
import os
import re
import sys

HIST = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 100
man = json.load(open(os.path.join(HIST, "manifest.json")))
tpl = collections.defaultdict(collections.Counter)
off = collections.Counter()
for s in man["seasons"][:N]:
    for line in open(os.path.join(HIST, s["dir"], "feed.jsonl")):
        m = json.loads(line)
        if m["type"] == "advisory":
            h = m["headline"].replace(m["storm"], "<S>")
            h = re.sub(r"Cascadia Prime|Coral Key|Azure Isle|Palm Atoll", "<ISLAND>", h)
            if m["source"] == "wire":
                tpl[h][m["outlook"]] += 1
            else:
                off[re.sub(r"Hurricane conditions:.*", "Hurricane conditions: ...", re.sub(r"Advisory \d+", "Advisory N", h))] += 1
print(f"wire templates in seasons 1-{N}:")
for h, c in sorted(tpl.items(), key=lambda kv: -sum(kv[1].values())):
    print(f"  {sum(c.values()):5d}  {dict(c)}  {h}")
print("official headline templates:", len(off))
for h, c in off.most_common(8):
    print(f"  {c:5d}  {h}")
