"""Group A tests: ctcbot.config (DESIGN.md section 9.1 A config), plus the verbatim copies of sections 3 and 8."""
import json
import os
import shutil
import sys
import unittest

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT)
sys.dont_write_bytecode = True

import testpaths  # noqa: E402
from ctcbot.config import (BOT_ROOT, Config, ConfigError, deep_merge, diff_from_default,  # noqa: E402
                           load_config)

DEFAULT = os.path.join(BOT, "config", "default.json")
_DIRS: list = []


def tearDownModule():
    for d in _DIRS:
        shutil.rmtree(d, ignore_errors=True)


def _tmp() -> str:
    d = testpaths.tmpdir("cfg")
    _DIRS.append(d)
    return d


def _write(d: dict, name: str) -> str:
    path = os.path.join(_tmp(), name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f)
    return path


def _reverse_keys(x):
    if isinstance(x, dict):
        return {k: _reverse_keys(x[k]) for k in reversed(list(x))}
    if isinstance(x, list):
        return [_reverse_keys(v) for v in x]
    return x


class TestDefault(unittest.TestCase):
    def test_default_loads_and_validates(self):
        cfg = load_config()
        self.assertIsInstance(cfg, Config)
        self.assertEqual(len(cfg.hash), 12)
        int(cfg.hash, 16)
        self.assertTrue(cfg.live_allowed)
        self.assertNotIn("live_allowed", cfg.d)
        self.assertEqual(cfg.root, BOT)
        self.assertEqual(BOT_ROOT, BOT)
        self.assertEqual(cfg.sources, (DEFAULT,))
        self.assertEqual(cfg.get("rate.bot_cap_per_min"), 150)
        self.assertEqual(cfg.get("feed.connections.1.name"), "ws1")
        self.assertEqual(cfg.sym("PORT")["eps"], 0.07)
        self.assertEqual(diff_from_default(cfg), [])

    def test_get_and_path(self):
        cfg = load_config()
        with self.assertRaises(KeyError):
            cfg.get("strategies.s3.nope")
        self.assertIsNone(cfg.get("strategies.s3.nope", None))
        self.assertEqual(cfg.get("exchange.host", "x"), None)       # present and null: not the default
        self.assertEqual(cfg.path(cfg.get("engine.dir")), os.path.normpath(os.path.join(BOT, "..", "engine")))
        self.assertEqual(cfg.path("/abs/x"), "/abs/x")
        self.assertIsNone(cfg.path(None))

    def test_default_json_is_section_8_verbatim(self):
        with open(os.path.join(BOT, "DESIGN.md"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("## 8. `config/default.json`")
        j = src.index("```json\n", i) + len("```json\n")
        k = src.index("\n```\n", j)
        with open(DEFAULT, encoding="utf-8") as f:
            self.assertEqual(f.read(), src[j:k] + "\n")

    def test_types_py_is_section_3_verbatim(self):
        with open(os.path.join(BOT, "DESIGN.md"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("## 3. Shared types")
        j = src.index("```python\n", i) + len("```python\n")
        k = src.index("\n```\n", j)
        with open(os.path.join(BOT, "ctcbot", "types.py"), encoding="utf-8") as f:
            self.assertEqual(f.read(), src[j:k] + "\n")

    def test_version_and_testnet_overlay(self):
        import ctcbot
        self.assertEqual(ctcbot.__version__, "1.1.0")
        cfg = load_config(overlays=[os.path.join(BOT, "config", "testnet_quarter.json")])
        self.assertEqual(cfg.get("sizing.scale"), 0.25)
        self.assertEqual(cfg.get("engine.library_seasons"), [300, 600])
        self.assertTrue(cfg.live_allowed)
        self.assertEqual(sorted(diff_from_default(cfg)),
                         ["engine.library_seasons: [1,600] -> [300,600]", "sizing.scale: 1.0 -> 0.25"])


class TestOverlaysAndSets(unittest.TestCase):
    def test_overlay_plus_set(self):
        ov = _write({"strategies": {"s3": {"k_r12": 0.15}}, "feed": {"channels": ["news"]}}, "ov.json")
        cfg = load_config(overlays=[ov], sets=["strategies.s3.k_r3=0.3", "strategies.h1.enabled=false",
                                               "strategies.s3.edge_ref=mid", "strategies.s3.limit_cost_frac=0.5"])
        self.assertEqual(cfg.get("strategies.s3.k_r3"), 0.3)
        self.assertEqual(cfg.get("strategies.s3.k_r12"), 0.15)
        self.assertIs(cfg.get("strategies.h1.enabled"), False)
        self.assertEqual(cfg.get("strategies.s3.edge_ref"), "mid")       # JSON parse fails -> string
        self.assertEqual(cfg.get("feed.channels"), ["news"])             # lists replace
        self.assertEqual(cfg.get("strategies.s3.k_r3", None), 0.3)
        self.assertEqual(cfg.sources[1], ov)
        self.assertIn("set:strategies.s3.k_r3=0.3", cfg.sources)
        self.assertEqual(diff_from_default(cfg), [
            "feed.channels: [\"news\",\"quotes\",\"trades\",\"orderbook\"] -> [\"news\"]",
            "strategies.h1.enabled: true -> false",
            "strategies.s3.edge_ref: \"touch\" -> \"mid\"",
            "strategies.s3.k_r12: 0.1 -> 0.15",
            "strategies.s3.k_r3: 0.2 -> 0.3",
            "strategies.s3.limit_cost_frac: 1.0 -> 0.5",
        ])

    def test_set_list_index_and_null(self):
        cfg = load_config(sets=['feed.connections.1.local_addr=["192.168.1.20", 0]', "strategies.h1.cap_bp=null"])
        self.assertEqual(cfg.get("feed.connections")[1], {"name": "ws1", "local_addr": ["192.168.1.20", 0]})
        self.assertIsNone(cfg.get("strategies.h1.cap_bp"))

    def test_deep_merge(self):
        base = {"a": {"b": 1, "c": [1, 2]}, "d": 1}
        over = {"a": {"c": [3]}, "e": {"f": 2}}
        out = deep_merge(base, over)
        self.assertEqual(out, {"a": {"b": 1, "c": [3]}, "d": 1, "e": {"f": 2}})
        self.assertEqual(base, {"a": {"b": 1, "c": [1, 2]}, "d": 1})
        out["e"]["f"] = 9
        self.assertEqual(over["e"]["f"], 2)

    def test_live_allowed_is_and_over_files(self):
        no = _write({"live_allowed": False, "sizing": {"scale": 0.5}}, "no.json")
        yes = _write({"live_allowed": True}, "yes.json")
        for order in ([no, yes], [yes, no], [no]):
            cfg = load_config(overlays=order)
            self.assertFalse(cfg.live_allowed, order)
            self.assertNotIn("live_allowed", cfg.d)
        self.assertTrue(load_config(overlays=[yes]).live_allowed)
        bad = _write({"live_allowed": "no"}, "bad.json")
        with self.assertRaises(ConfigError):
            load_config(overlays=[bad])

    def test_hash_stable_across_key_order(self):
        with open(DEFAULT, encoding="utf-8") as f:
            d = json.load(f)
        rev = _write(_reverse_keys(d), "rev.json")
        self.assertEqual(load_config(rev).hash, load_config().hash)
        self.assertNotEqual(load_config(sets=["strategies.s3.k_r3=0.3"]).hash, load_config().hash)
        self.assertNotEqual(list(_reverse_keys(d)), list(d))


class TestValidation(unittest.TestCase):
    def assertProblem(self, fragment, **kw):
        with self.assertRaises(ConfigError) as cm:
            load_config(**kw)
        self.assertTrue(any(fragment in p for p in cm.exception.problems), (fragment, cm.exception.problems))
        return cm.exception

    def test_missing_symbol(self):
        with open(DEFAULT, encoding="utf-8") as f:
            d = json.load(f)
        del d["symbols"]["GEN"]
        self.assertProblem("missing ['GEN']", path=_write(d, "nosym.json"))

    def test_missing_strategy_section(self):
        with open(DEFAULT, encoding="utf-8") as f:
            d = json.load(f)
        del d["strategies"]["rein_ask"]
        e = self.assertProblem("strategies.rein_ask: missing section", path=_write(d, "nostrat.json"))
        self.assertTrue(any("rein_ask" in p for p in e.problems))

    def test_bad_anchor(self):
        self.assertProblem("strategies.h1.entry_start", sets=['strategies.h1.entry_start=["storm_begin", -10]'])
        self.assertProblem("strategies.s1r3.sanity_at", sets=['strategies.s1r3.sanity_at=["final", "16"]'])
        self.assertProblem("cost.phases.0.1", sets=['cost.phases.0=["open", ["opening", 0], ["open", 3]]'])
        ov = _write({"strategies": {"s3": {"new_window": ["stormy", 5]}}}, "anch.json")
        self.assertProblem("strategies.s3.new_window", overlays=[ov])

    def test_cap_above_170(self):
        # hardening: bot cap + KILL overdraft <= team - 10 (150 + 20 = 170), so the bot alone stays below 180
        self.assertProblem("bot_cap_per_min 151", sets=["rate.bot_cap_per_min=151"])
        self.assertEqual(load_config(sets=["rate.bot_cap_per_min=150"]).get("rate.bot_cap_per_min"), 150)
        self.assertEqual(load_config(sets=["rate.bot_cap_per_min=160", "rate.kill_overdraft=10"])
                         .get("rate.bot_cap_per_min"), 160)
        self.assertProblem("rate.kill_overdraft", sets=["rate.kill_overdraft=21"])
        self.assertProblem("team limit 180", sets=["exchange.team_actions_per_min=1000"])
        self.assertProblem("exchange.kill_workers", sets=["exchange.kill_workers=0"])

    def test_floors(self):
        self.assertProblem("rate.floors.HEDGE", sets=['rate.floors={"KILL": 0, "SNIPE": 1, "TAKE": 10}'])

    def test_edge_ref_pairing(self):
        self.assertProblem("with edge_ref 'touch'", sets=["strategies.s3.limit_cost_frac=0.5"])
        self.assertProblem("with edge_ref 'mid'", sets=["strategies.s3.edge_ref=mid", "strategies.s3.limit_cost_frac=0.8"])
        self.assertProblem("edge_ref must be", sets=["strategies.s3.edge_ref=book"])
        load_config(sets=["strategies.s3.edge_ref=mid", "strategies.s3.limit_cost_frac=0.0"])

    def test_urls(self):
        self.assertProblem("exchange.rest_base must start with https://", sets=["exchange.rest_base=http://x.test/api/v1"])
        self.assertProblem("exchange.case_url", sets=["exchange.case_url=http://x.test/cases/hurricane.json"])
        self.assertProblem("exchange.ws_url must start with wss://", sets=["exchange.ws_url=ws://x.test/ws/v1/market-data"])
        cfg = load_config(sets=["exchange.rest_base=https://x.test/api/v1", "exchange.ws_url=wss://x.test/ws"])
        self.assertEqual(cfg.get("exchange.rest_base"), "https://x.test/api/v1")

    def test_connections(self):
        dup = '[{"name": "ws0", "local_addr": null}, {"name": "ws0", "local_addr": null}]'
        self.assertProblem("duplicate names", sets=["feed.connections=" + dup])
        self.assertProblem("1 to 4 entries", sets=["feed.connections=[]"])
        five = json.dumps([{"name": f"w{i}", "local_addr": None} for i in range(5)])
        self.assertProblem("1 to 4 entries", sets=["feed.connections=" + five])
        self.assertProblem("local_addr", sets=['feed.connections.0.local_addr="10.0.0.1"'])

    def test_sizing(self):
        self.assertProblem("sizing.scale", sets=["sizing.scale=0"])
        self.assertProblem("sizing.scale", sets=["sizing.scale=1.5"])
        self.assertProblem("sizing.clip_div.h1", sets=["sizing.clip_div.h1=0"])

    def test_set_live_allowed_refused(self):
        self.assertProblem("live_allowed cannot be changed", sets=["live_allowed=true"])

    def test_set_unknown_key_and_bad_items(self):
        self.assertProblem("unknown config key", sets=["strategies.s3.k_r33=0.3"])
        self.assertProblem("not of the form", sets=["strategies.s3.k_r3"])

    def test_every_problem_listed(self):
        e = self.assertProblem("bot_cap_per_min", sets=["rate.bot_cap_per_min=500", "sizing.scale=2",
                                                        "exchange.rest_base=http://x.test"])
        self.assertGreaterEqual(len(e.problems), 3)

    def test_unreadable_files(self):
        with self.assertRaises(ConfigError):
            load_config(os.path.join(_tmp(), "missing.json"))
        path = os.path.join(_tmp(), "broken.json")
        with open(path, "w") as f:
            f.write("{not json")
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_user_agent(self):
        self.assertProblem("user_agent", sets=["exchange.user_agent=Python-urllib/3.14"])


if __name__ == "__main__":
    unittest.main()
