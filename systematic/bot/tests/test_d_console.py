"""Group D unit tests: console parser and status line (DESIGN.md §9.1 D console)."""
from __future__ import annotations

import os
import random
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

from ctcbot.types import Command  # noqa: E402
from ctcbot.app import console  # noqa: E402
from ctcbot.app.console import parse, validate  # noqa: E402
from ctcbot.app.status import format_status  # noqa: E402


class TestParse(unittest.TestCase):
    def test_every_command_parses(self):
        cases = {
            "k": ("k", ()), "cx": ("cx", ()), "p": ("p", ()), "r": ("r", ()),
            "unfreeze-kill": ("unfreeze-kill", ()), "x port": ("x", ("PORT",)), "E Port": ("e", ("PORT",)),
            "f port 44.5": ("f", ("PORT", "44.5")), "f PORT OFF": ("f", ("PORT", "off")),
            "m haven 1.1": ("m", ("HAVEN", "1.1")), "a haven -2": ("a", ("HAVEN", "-2")), "b 120": ("b", ("120",)),
            "caseok grid": ("caseok", ("GRID",)), "caseok ALL": ("caseok", ("all",)),
            "caseok Timing": ("caseok", ("timing",)), "season keep": ("season", ("keep",)),
            "season NEW": ("season", ("new",)), "eng restart": ("eng", ("restart",)), "st": ("st", ()),
            "pos": ("pos", ()), "ord": ("ord", ()), "ann": ("ann", ()), "holds": ("holds", ()), "q": ("q", ()),
            "h1 on": ("h1", ("on",)), "h1 0.5": ("h1", ("0.5",)), "s3 rounds 3": ("s3", ("rounds", "3")),
            "s3 costmult 1.5": ("s3", ("costmult", "1.5")), "s1r3 off": ("s1r3", ("off",)), "lag on": ("lag", ("on",)),
            "rein off": ("rein", ("off",)), "flat on": ("flat", ("on",)), "u port": ("u", ("PORT",)),
            "ua port": ("ua", ("PORT",)), "s1ok rein": ("s1ok", ("REIN",)), "x1 arch": ("x1", ("ARCH",)),
            "lawok port": ("lawok", ("PORT",)), "lawok all": ("lawok", ("all",)), "  K  ": ("k", ()),
        }
        for line, (name, args) in cases.items():
            cmd = parse(line)
            self.assertEqual(cmd, Command(name, args, "console"), line)
            self.assertIsNone(validate(cmd), line)

    def test_source_and_empty(self):
        self.assertEqual(parse("k", source="killfile").source, "killfile")
        for line in ("", "   ", "\n", "# a comment", None):
            self.assertIsNone(parse(line))

    def test_garbage_never_raises(self):
        rng = random.Random(7)
        alphabet = "abcxyzKQ019 .-_#\t\x00\x07\x1b[]{}()é☃\n"
        for _ in range(3000):
            line = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
            cmd = parse(line)
            if cmd is not None:
                self.assertIsInstance(validate(cmd), (str, type(None)))
        self.assertIsNotNone(parse("x" * 10_000))
        self.assertLessEqual(len(parse("a " * 100).args), console.MAX_TOKENS - 1)
        parse(b"\xff")                                      # not text: no exception

    def test_validate_rejects_bad_arguments(self):
        bad = ["x", "x NOPE", "e PORT GRID", "f PORT", "f PORT -1", "f PORT abc", "f PORT inf", "m PORT 0",
               "a PORT nan", "b", "b -3", "b 1.5", "caseok", "caseok nope", "season", "season maybe", "eng",
               "eng stop", "u", "x1 NOPE", "lawok", "st now", "frobnicate"]
        for line in bad:
            self.assertIsInstance(validate(parse(line)), str, line)
        self.assertIsNone(validate(parse("k now please")))     # a kill is never refused

    def test_known(self):
        self.assertTrue(console.is_known("k"))
        self.assertTrue(console.is_known("lawok"))
        self.assertFalse(console.is_known("frobnicate"))


class TestStatus(unittest.TestCase):
    def test_example_line(self):
        st = {"label": "DRY", "mode": "NORMAL", "epoch": 1, "round": 2, "phase": "storm", "t": 213.44,
              "quality": "feed", "running": True, "conns_up": 2, "conns": 2, "frame_age": 0.4, "wm": 695, "gaps": 0,
              "dup": 0, "eng_age": 0.3, "eng_ms": 41.2, "eng_q": 0, "eng_alive": True, "act_used": 87,
              "act_cap": 150, "rej": 0, "unk": 0, "mtm": 3210.4, "peak": 4020.0,
              "positions": {"PORT": 200, "ISLE": 0, "HAVEN": -120}, "frozen": [], "holds": []}
        self.assertEqual(format_status(st),
                         "[DRY] NORMAL e1 r2 storm t=213.4 feed | ws 2/2 0.4s wm695 gaps0 dup0 | eng 0.3s 41ms q0 | "
                         "act 87/150 rej0 unk0 | mtm +3,210 pk +4,020 | PORT+200 HAVEN-120 | frz - | holds -")

    def test_degraded_fields(self):
        line = format_status({"label": "LIVE", "killed": True, "running": False, "pending_reset": "date_change",
                              "eng_alive": False, "mtm": float("nan"), "positions": "bad", "frozen": ["PORT", "REIN"],
                              "holds": ["feed_down"], "disabled": ["GEN"]})
        self.assertTrue(line.startswith("[LIVE] FROZEN"))
        for part in ("paused", "RESET?(date_change)", "DOWN", "mtm ?", "flat", "frz PORT,REIN", "holds feed_down",
                     "off GEN"):
            self.assertIn(part, line)
        self.assertIsInstance(format_status({}), str)
        self.assertIn("unavailable", format_status(None))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
