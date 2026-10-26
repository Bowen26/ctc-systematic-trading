"""Book mids shortly after each round open vs research consensus open and the packet starting prices.
For season 226 (no research files loaded) the round open is taken from snapshot frames.
usage: python3 -I open_levels.py recording.jsonl s224 s225"""
import sys, os, math, bisect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rec
R = rec.Recording(sys.argv[1], sys.argv[2:])
PACKET = {"PORT": 72.48, "ISLE": 50.26, "AQUA": 27.33, "GRID": 36.41, "FERRY": 17.91, "RECON": 52.38, "MAT": 35.81,
          "GEN": 40.30, "HAVEN": 39.55, "REIN": 89.36, "ARCH": 289.20, "REBLD": 216.68}
def bmid(sym, t):
    b = R.book_at(sym, t); return rec.mid(b)
print("sym     | s225 r1 @20s: book  cons  packet | s225 r2 @20s: book cons prevclose(book@775 r1) | s225 r3 @20s: book cons | s226 r1 @30s: book packet")
# season 226 round 1 open: first snapshot with 1801-05-11T09:45:00 minus 30 s
t226 = None
for rx, m in R.news:
    if m.get("type") == "snapshot" and m.get("time", "").startswith("1801-05-11T09:45"):
        t226 = rx - 30; break
for sym in rec.SYMS:
    k1, k2, k3 = (225, 1), (225, 2), (225, 3)
    b1 = bmid(sym, R.wall(k1, 20)); c1 = R.rounds[k1].cons(sym, 20)
    b2 = bmid(sym, R.wall(k2, 20)); c2 = R.rounds[k2].cons(sym, 20); pc = bmid(sym, R.wall(k1, 775))
    b3 = bmid(sym, R.wall(k3, 20)); c3 = R.rounds[k3].cons(sym, 20)
    b6 = bmid(sym, t226 + 30) if t226 else None
    print(f"{sym:6s} | {b1:8.2f} {c1:8.2f} {PACKET[sym]:8.2f} ({1e4*math.log(b1/c1):+5.0f} bp vs cons) | {b2:8.2f} {c2:8.2f} {pc:8.2f} ({1e4*math.log(b2/c2):+5.0f}) | {b3:8.2f} {c3:8.2f} ({1e4*math.log(b3/c3):+5.0f}) | {b6:8.2f} {PACKET[sym]:8.2f} ({1e4*math.log(b6/PACKET[sym]):+5.0f} vs packet)")
print()
print("season 225 round 1 at 20 s: book mid vs packet start and vs consensus (bp)")
n_p = n_c = 0
for sym in rec.SYMS:
    k1 = (225, 1)
    b1 = bmid(sym, R.wall(k1, 20)); c1 = R.rounds[k1].cons(sym, 20)
    dp, dc = 1e4 * math.log(b1 / PACKET[sym]), 1e4 * math.log(b1 / c1)
    n_p += abs(dp) <= 50; n_c += abs(dc) <= 50
    print(f"   {sym:6s} vs packet {dp:+5.0f}  vs consensus {dc:+5.0f}")
print(f"   within +/-50 bp: of packet {n_p}/12, of consensus {n_c}/12")
