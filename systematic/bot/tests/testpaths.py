"""Shared test paths and helpers for the ctcbot unit tests (DESIGN.md section 1).

Every test file inserts BOT into ``sys.path`` itself and then imports this module by name, because
``tests/`` has no ``__init__.py`` and ``unittest discover -s tests`` puts ``tests/`` on the path.

Constants point at read-only data outside BOT. ``require(path)`` skips a test whose data is missing and
``integration(modname, ...)`` skips a test whose cross-group modules do not import yet; with
``CTCBOT_REQUIRE_DATA=1`` both raise ``AssertionError`` instead, so an acceptance run cannot pass with
nothing run. ``tmpdir()`` hands out a fresh directory under ``tests/_tmp/``, the only place tests write.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from types import ModuleType

BOT: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYS: str = os.path.dirname(BOT)
SP: str = ("/private/tmp/claude-501/-Users-bowentian--claude-spares-spare-iBWS3H/"
           "5e6e71d5-291b-48c1-afaa-35b357d20e7e/scratchpad")

RECORDING: str = os.path.join(SYS, "testnet", "recordings", "testnet_s224_s225.jsonl")
ENGINE_DIR: str = os.path.join(SYS, "engine")
LAWS: str = os.path.join(SYS, "laws", "se_laws.py")
COST_MODEL: str = os.path.join(SYS, "testnet", "analysis", "books", "cost_model.json")
RESEARCH_CASE: str = os.path.join(SYS, "data", "case.json")
SKELETON_FEED: str = os.path.join(SYS, "bot_skeleton", "feed.py")
HISTORY: str = os.environ.get("CTCBOT_HISTORY", SP + "/data/hurricane-season-research/history")
FV_CACHE: str = os.environ.get("CTCBOT_FV_CACHE", SP + "/work/se/backtest/cache")
REBACKTEST: str = os.path.join(SYS, "testnet", "analysis", "rebacktest")
SLOW: bool = os.environ.get("CTCBOT_SLOW") == "1"
REQUIRE: bool = os.environ.get("CTCBOT_REQUIRE_DATA") == "1"

TMP_ROOT: str = os.path.join(BOT, "tests", "_tmp")

if BOT not in sys.path:
    sys.path.insert(0, BOT)


def _skip_or_fail(msg: str) -> None:
    if REQUIRE:
        raise AssertionError(msg + " (CTCBOT_REQUIRE_DATA=1)")
    raise unittest.SkipTest(msg)


def require(path: str) -> str:
    """Return ``path`` if it exists; otherwise skip the test (or fail under CTCBOT_REQUIRE_DATA=1)."""
    if not path or not os.path.exists(path):
        _skip_or_fail(f"missing data: {path}")
    return path


def integration(*modnames: str) -> list[ModuleType]:
    """Import every named module and return them in order.

    Skips the test (or fails under CTCBOT_REQUIRE_DATA=1) when any import fails, so a test that needs
    another group's real modules waits for them instead of failing.
    """
    sys.dont_write_bytecode = True
    mods: list[ModuleType] = []
    for name in modnames:
        try:
            mods.append(importlib.import_module(name))
        except Exception as e:  # noqa: BLE001 - any import failure means "not ready yet"
            _skip_or_fail(f"integration module {name} unavailable: {type(e).__name__}: {e}")
    return mods


def slow() -> None:
    """Skip the calling test unless CTCBOT_SLOW=1."""
    if not SLOW:
        raise unittest.SkipTest("slow test (set CTCBOT_SLOW=1)")


def tmpdir(prefix: str = "t") -> str:
    """Create and return a fresh directory under ``BOT/tests/_tmp``."""
    os.makedirs(TMP_ROOT, exist_ok=True)
    return tempfile.mkdtemp(prefix=prefix + "_", dir=TMP_ROOT)
