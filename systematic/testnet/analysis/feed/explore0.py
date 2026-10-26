"""Exploration: reconnect points and largest inter-frame gaps in the testnet recording.

    python3 -I explore0.py <recording.jsonl>
"""
import datetime as dt
import json
import sys

rec = sys.argv[1]
frames = [json.loads(l) for l in open(rec)]
print(len(frames))
for i, f in enumerate(frames):
    if f['type'] == 'subscription_ack':
        print('ACK', i, f['timestamp'], round(f['received_at'], 3))
        for g in frames[max(0, i - 8):i]:
            m = g.get('message', {})
            print('    ', g['type'], g.get('symbol'), m.get('type'), m.get('seq'), m.get('time'), g.get('timestamp'), round(g['received_at'], 3))
        for g in frames[i:i + 4]:
            m = g.get('message', {})
            print('  >>', g['type'], g.get('symbol'), m.get('type'), m.get('seq'), m.get('time'), g.get('timestamp'), round(g['received_at'], 3))
gaps = []
for a, b in zip(frames, frames[1:]):
    gaps.append((b['received_at'] - a['received_at'], a['received_at'], b['type']))
gaps.sort(reverse=True)
for g in gaps[:15]:
    print('gap %.2f at %s next %s' % (g[0], dt.datetime.fromtimestamp(g[1]).strftime('%H:%M:%S.%f'), g[2]))
