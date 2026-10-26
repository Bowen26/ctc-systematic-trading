"""Group D unit tests: JsonlLog, FrameRecorder and the redacting exception hooks (DESIGN.md §9.1 D log)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402

from ctcbot.types import SourceFrame, Side  # noqa: E402
from ctcbot.app.log import (REDACTED, WEBSOCKETS_LOGGERS, FrameRecorder, JsonlLog, Redactor,  # noqa: E402
                            install_redaction, redact_text)

KEY = "Kx9-TEST.key_abc/DEF+123=zz"   # a fake key in the bot-key alphabet


def lines(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        raw = [ln for ln in f.read().splitlines() if ln.strip()]
    return [json.loads(ln, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c))) for ln in raw]


class TestRedactor(unittest.TestCase):
    def test_variants_and_short_strings(self):
        r = Redactor([KEY, "", "ab", None])  # type: ignore[list-item]
        self.assertEqual(r(f"x {KEY} y {KEY}"), "x *** y ***")
        self.assertEqual(r("ab ab"), "ab ab")                     # short strings are never "secrets"
        self.assertEqual(redact_text(f"k={KEY}\n", [KEY]), "k=***\n")
        r2 = Redactor(['se"cret\\val'])
        self.assertNotIn("cret", r2(json.dumps({"v": 'se"cret\\val'})))


class TestJsonlLog(unittest.TestCase):
    def test_lines_are_json_and_redacted(self):
        d = testpaths.tmpdir("log")
        path = os.path.join(d, "events.jsonl")
        echoed: list[str] = []
        log = JsonlLog(path, [KEY], echo=echoed.append)
        log.event("plain", a=1, side=Side.BUY, s={3, 1, 2}, nested={"k": [KEY, {"deep": f"pre{KEY}post"}]})
        log.event("weird", x=float("nan"), y=float("inf"), b=b"bytes", obj=object())
        log.event("clash", t_wall="w", t_mono="m")
        log.alert("auth_failed", f"401 with key {KEY}", status=401)
        log.event(f"kind_{KEY}")
        log.close()
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn(KEY, text)
        recs = lines(path)                                       # strict JSON: no NaN/Infinity literals
        self.assertEqual([r["kind"] for r in recs], ["plain", "weird", "clash", "alert", "kind_***"])
        for r in recs:
            self.assertIn("t_wall", r)
            self.assertIn("t_mono", r)
        self.assertEqual(recs[0]["side"], "buy")
        self.assertEqual(recs[0]["s"], [1, 2, 3])
        self.assertEqual(recs[0]["nested"]["k"][0], REDACTED)
        self.assertEqual(recs[1]["x"], "nan")
        self.assertEqual((recs[2]["f_t_wall"], recs[2]["f_t_mono"]), ("w", "m"))
        self.assertEqual(recs[3]["alert"], "auth_failed")
        self.assertEqual(recs[3]["msg"], "401 with key ***")
        self.assertEqual(len(echoed), 1)
        self.assertTrue(echoed[0].startswith("\a[ALERT] auth_failed"))
        self.assertNotIn(KEY, echoed[0])
        self.assertEqual(log.redact(f"a{KEY}b"), "a***b")
        self.assertEqual(log.alert_counts["auth_failed"], 1)

    def test_thread_safety(self):
        d = testpaths.tmpdir("logthr")
        path = os.path.join(d, "events.jsonl")
        log = JsonlLog(path, [KEY])

        def work(i: int) -> None:
            for j in range(200):
                log.event("w", i=i, j=j, payload="x" * 50)

        ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        log.close()
        self.assertEqual(len(lines(path)), 1600)

    def test_no_file_and_write_errors_never_raise(self):
        log = JsonlLog(None, [KEY])
        log.event("x", a=1)
        log.alert("y", "z")
        d = testpaths.tmpdir("logerr")
        echoed: list[str] = []
        log2 = JsonlLog(os.path.join(d, "e.jsonl"), [KEY], echo=echoed.append)
        log2._fh.close()                                        # simulate a broken file handle
        log2.event("after_close", a=1)
        log2.event("again", a=2)
        self.assertEqual(log2.write_errors, 2)
        self.assertEqual(len(echoed), 1)


class TestFrameRecorder(unittest.TestCase):
    def test_record_feed_format(self):
        d = testpaths.tmpdir("rec")
        path = os.path.join(d, "feed_raw.jsonl")
        rec = FrameRecorder(path, [KEY])
        fr = {"type": "news", "message": {"type": "storm", "seq": 1, "headline": f"leak {KEY}"},
              "received_at": 123.0}
        rec.record(SourceFrame(frame=fr, recv_wall=1792766846.8, recv_mono=5.0, conn="ws0#1"))
        rec.record(SourceFrame(frame={}, recv_wall=1.0, recv_mono=5.0, conn="ws0#1", control="up"))
        rec.close()
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn(KEY, text)
        recs = lines(path)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["received_at"], 1792766846.8)
        self.assertEqual(recs[0]["message"]["seq"], 1)
        self.assertIn('"type": "news"', text)                     # record_feed.py spacing
        # readable by group A's recording reader
        try:
            from ctcbot.sources import iter_recording
        except Exception:  # noqa: BLE001 - A not delivered: format checked above
            return
        got = list(iter_recording(path))
        self.assertEqual(got[0][0], 1792766846.8)
        self.assertNotIn("received_at", got[0][1])


class TestHooks(unittest.TestCase):
    def setUp(self) -> None:
        self.d = testpaths.tmpdir("hooks")
        self.path = os.path.join(self.d, "events.jsonl")
        self.log = JsonlLog(self.path, [KEY])

    def tearDown(self) -> None:
        self.log.close()

    def _text(self) -> str:
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def test_thread_exception_redacted(self):
        hooks = self.log.install_redaction()
        try:
            def boom() -> None:
                raise RuntimeError(f"connect failed with header X-API-Key: {KEY}")

            t = threading.Thread(target=boom, name="worker-x")
            t.start()
            t.join()
        finally:
            hooks.uninstall()
        text = self._text()
        self.assertNotIn(KEY, text)
        recs = [r for r in lines(self.path) if r["kind"] == "exception"]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["where"], "thread:worker-x")
        self.assertIn("X-API-Key: ***", recs[0]["error"])
        self.assertIn("RuntimeError", recs[0]["traceback"])

    def test_sys_and_loop_hooks(self):
        loop = asyncio.new_event_loop()
        prev_sys, prev_thr = sys.excepthook, threading.excepthook
        try:
            hooks = install_redaction([KEY], loop, log=self.log)
            try:
                raise ValueError(f"bad {KEY}")
            except ValueError:
                sys.excepthook(*sys.exc_info())

            async def fail() -> None:
                raise KeyError(f"loop {KEY}")

            async def main() -> None:
                t = loop.create_task(fail())
                await asyncio.sleep(0.01)
                del t
            loop.run_until_complete(main())
            loop.call_exception_handler({"message": f"plain context {KEY}"})
            hooks.uninstall()
        finally:
            loop.close()
        self.assertIs(sys.excepthook, prev_sys)
        self.assertIs(threading.excepthook, prev_thr)
        text = self._text()
        self.assertNotIn(KEY, text)
        wheres = [r["where"] for r in lines(self.path) if r["kind"] == "exception"]
        self.assertIn("main", wheres)
        self.assertIn("loop", wheres)

    def test_websockets_loggers_warning_and_filtered(self):
        hooks = install_redaction([KEY], None, log=self.log)
        try:
            for name in WEBSOCKETS_LOGGERS:
                self.assertEqual(logging.getLogger(name).level, logging.WARNING)
            lg = logging.getLogger("websockets.client")
            self.assertFalse(lg.isEnabledFor(logging.DEBUG))
            seen: list[str] = []

            class H(logging.Handler):
                def emit(self, record: logging.LogRecord) -> None:
                    seen.append(record.getMessage())

            h = H()
            lg.addHandler(h)
            lg.propagate = False
            try:
                lg.debug("> X-API-Key: %s", KEY)
                lg.warning("handshake failed: X-API-Key: %s", KEY)
            finally:
                lg.removeHandler(h)
                lg.propagate = True
            self.assertEqual(seen, ["handshake failed: X-API-Key: ***"])
        finally:
            hooks.uninstall()


if __name__ == "__main__":
    unittest.main()
