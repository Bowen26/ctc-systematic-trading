"""Writer's check of the playbook H1 crowding skip on the books (standard library only).
usage: python3 -I w04_crowding.py <recording.jsonl> <verify dir>
Playbook 4.2: at 48 s, skip H1 if HAVEN or ARCH has moved more than 1/3 of its mean jump (HAVEN 9.8%, ARCH 3.3%)
in our direction since 30 s. Prints the 30 -> 48 s book-mid move, and the 58 -> 90 s book move, per round of season 225.
"""
import sys
sys.path.insert(0, sys.argv[2])
import vlib as V

frames = V.load_rec(sys.argv[1])
opens = V.round_opens(frames)
books = V.books_by_round(frames, opens)
JUMP = {"HAVEN": 9.8, "ARCH": 3.3}
for k in [(225, 1), (225, 2), (225, 3)]:
    out = []
    for s in ("HAVEN", "ARCH", "PORT"):
        m30, m48 = V.mid(V.book_at(books[k][s], 30)), V.mid(V.book_at(books[k][s], 48))
        m58, m90 = V.mid(V.book_at(books[k][s], 58)), V.mid(V.book_at(books[k][s], 90))
        mv = 100 * (m48 / m30 - 1)
        fire = (s in JUMP) and mv > JUMP[s] / 3
        out.append(f"{s} 30->48 {mv:+.2f}%{' FIRES' if fire else ''} | 58->90 {100 * (m90 / m58 - 1):+.2f}%")
    print(f"{k}: " + "; ".join(out))
