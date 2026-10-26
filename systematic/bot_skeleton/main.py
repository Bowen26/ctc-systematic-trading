"""CTC 2026 Systematic Equities bot -- entry point and event loop.

*** UNTESTED SKELETON. IT HAS NEVER CONNECTED TO ANY EXCHANGE. ***
*** Opening any socket requires BOTH --network and --i-reviewed-untested-skeleton.  ***
*** Orders are DRY-RUN (logged, not sent) unless --live is also given.              ***

    export CTC_BOT_KEY=...            # never put the key in a file under version control
    python3 -I main.py --network --i-reviewed-untested-skeleton            # feed + GETs, dry-run orders
    python3 -I main.py --network --i-reviewed-untested-skeleton --live     # real orders (testnet first!)
    WebSocket: pip install "websockets>=14" (starter-kit approach) or --stdlib-ws (untested fallback)

Tasks on one asyncio loop: feed reader -> queue -> consumer (drain whole queue = coalesce catch-up
bursts -> update state -> fair values -> strategies -> order manager); 0.5 s ticker; 1 s clock sync;
0.5 s reconcile; schedule (blackouts, connection pre-warm); kill-file watch; console; persistence.

Console (type a line + Enter):
  k            KILL: cancel-all (1 action, KILL priority) and FREEZE
  cx           cancel-all now, keep running          p / r   pause new risk / resume
  x SYM        disable SYM and pull its orders        e SYM   enable SYM
  f SYM PX     fair override (absolute); f SYM off    m SYM X fair multiplier (announcements)
  s SYM X      sigma multiplier                       q on|off  passive quoting
  b N          bot action cap (humans trading in the browser? lower it)
  a            list announcements waiting for the operator         st  status
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # python3 -I does not add it

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402

import clock  # noqa: E402
import config  # noqa: E402
import fairvalue  # noqa: E402
import feed  # noqa: E402
import orders  # noqa: E402
import ratelimit  # noqa: E402
import rest  # noqa: E402
import risk  # noqa: E402
import state as state_mod  # noqa: E402
import strategy  # noqa: E402
from ratelimit import Priority  # noqa: E402


class Bot:
    def __init__(self, args, key):
        self.args = args
        self.log_f = open(os.path.join(args.log_dir, f"decisions-{int(time.time())}.jsonl"), "a", buffering=1)
        self.clock = clock.RoundClock()
        self.state = state_mod.State(config.SYMBOLS, config.LIMITS)
        self.limiter = ratelimit.ActionLimiter(config.BOT_ACTION_CAP, config.LIMITER_WINDOW_S, config.PRIORITY_FLOORS)
        self.req_limiter = ratelimit.RequestLimiter(config.REQUESTS_PER_S_CAP)
        self.client = rest.RestClient(args.host, config.API_PREFIX, key, config.USER_AGENT, config.HTTP_TIMEOUT_S,
                                      config.HTTP_GET_TIMEOUT_S, self.req_limiter, dry_run=not args.live, log=self.log)
        self.arest = rest.AsyncRest(self.client, config.HTTP_WORKERS)
        self.risk = risk.RiskManager(self.state, log=self.log)
        self.om = orders.OrderManager(self.state, self.risk, self.limiter, self.arest, log=self.log)
        self.model = (fairvalue.ScenarioModel.from_files(args.params, args.scenarios) if args.params
                      else fairvalue.HouseFollowModel())
        self.strats = strategy.build_strategies(config.STRAT)
        self.queue = asyncio.Queue()
        self.normalizer = feed.FeedNormalizer()
        self.recorder = feed.Recorder(os.path.join(args.log_dir, f"feed-{int(time.time())}.jsonl"))
        guard = feed.SendGuard(config.WS_MAX_SEND_PER_S)
        factory = ((lambda: feed.MinimalStdlibWS(guard)) if args.stdlib_ws
                   else (lambda: feed.WebsocketsLibAdapter(guard, config.USER_AGENT)))
        ws_url = f"wss://{args.host}{config.WS_PATH}"
        self.feed = feed.FeedClient(ws_url, {"X-API-Key": key, "User-Agent": config.USER_AGENT}, config.SYMBOLS,
                                    config.WS_SUBSCRIBE_CHANNELS, self.queue, self.normalizer, self.recorder, factory,
                                    idle_timeout=config.WS_IDLE_TIMEOUT_S, log=self.log)
        self.kill_switch = risk.KillSwitch(os.path.join(args.log_dir, config.KILL_FILE))
        self.reserved_for_date = None
        self.warmed = set()

    # ---- logging ----------------------------------------------------------------------------
    def log(self, msg, **kw):
        rec = {"t": time.time(), "msg": msg, **kw}
        print(time.strftime("%H:%M:%S"), msg, flush=True)
        self.log_f.write(json.dumps(rec, default=str) + "\n")

    # ---- decision cycle ---------------------------------------------------------------------
    def _ctx(self):
        now_real = self.clock.now_real()
        self.state.phase = clock.phase_of(now_real) if now_real is not None else self.state.phase
        t0 = time.perf_counter()
        fairs = self.model.evaluate(self.state, now_real)
        settle = self.model.settlement(self.state)
        dt = (time.perf_counter() - t0) * 1000
        if dt > 20:
            self.log(f"slow fair evaluation {dt:.1f} ms")
        return strategy.Ctx(self.state, fairs, now_real, settle, self.limiter.available())

    async def decide(self, events=None):
        ctx = self._ctx()
        intents = []
        for s in self.strats:
            try:
                intents += s.on_batch(events, ctx) if events is not None else s.on_tick(ctx)
            except Exception as exc:                  # one broken module must not stop the others
                self.log(f"strategy {s.name} error {exc!r}")
        if intents:
            self.log("intents", intents=[repr(i) for i in intents])
            await self.om.execute(intents)

    async def consume(self):
        while True:
            batch = [await self.queue.get()]
            while not self.queue.empty():              # coalesce catch-up bursts
                batch.append(self.queue.get_nowait())
            news = []
            for e in batch:
                if e.kind == "season_reset":
                    self.log("feed: seq restarted -> new season")
                    self._new_season(None)
                elif e.kind == "snapshot":
                    self.state.on_snapshot(e.msg)
                elif e.kind == "md_quote":
                    self.state.on_quote(e.msg)
                elif e.kind in ("ws_connected", "ws_disconnected", "md_trade", "md_orderbook", "unknown"):
                    if e.kind == "ws_disconnected" and self.state.orders:
                        self.log("feed down with resting orders")
                else:
                    self.state.on_message(e.msg)
                    if e.msg.get("time"):
                        self.clock.observe_market_time(e.msg["time"], local=time.monotonic())
                    if e.kind == "advisory" and hasattr(self.model, "post"):
                        self.model.post.update(e.msg)
                    if e.kind == "storm_formed" and hasattr(self.model, "post"):
                        self.model.post.reset()
                    news.append(e)
            if news:
                await self.decide(news)

    async def ticker(self):
        while True:
            await asyncio.sleep(0.5)
            await self.decide(None)

    # ---- clock, season, schedule ---------------------------------------------------------------
    def _new_season(self, comp_id):
        self.state.reset_season(comp_id)
        self.normalizer.reset(comp_id)
        self.limiter.clear_reservations()
        self.reserved_for_date = None
        for s in self.strats:
            if hasattr(s, "sent"):
                s.sent = {}

    async def clock_sync(self):
        while True:
            t0 = time.monotonic()
            r = await self.arest.call("get_time")
            rtt = time.monotonic() - t0
            if r.ok and isinstance(r.data, dict):
                mt = rest.g(r.data, "time", "market_time", "now")
                stt = rest.g(r.data, "state", "status", default="unknown")
                cid = rest.g(r.data, "competition_id", "id", "competition")
                self.clock.set_state(stt)
                if mt and stt == "running":
                    self.clock.observe_market_time(str(mt), local=time.monotonic(), authoritative=True, rtt=rtt)
                self.risk.market_open = stt == "running"
                if cid is not None and cid != self.state.comp_id:
                    self.log(f"competition id {self.state.comp_id} -> {cid}: new season")
                    self._new_season(cid)
                    if config.RECONNECT_ON_NEW_SEASON:
                        await self.feed.force_reconnect()
            await asyncio.sleep(config.CLOCK_POLL_EVERY_S)

    async def schedule(self):
        while True:
            await asyncio.sleep(0.25)
            now_real, off = self.clock.now_real(), self.clock.offset
            if now_real is None:
                continue
            if self.clock.date != self.reserved_for_date:
                self.reserved_for_date = self.clock.date
                for ev, before, after in config.BLACKOUTS:
                    t = off + config.SCHEDULE_REAL_S[ev]
                    self.limiter.reserve(t - before, t + after, {Priority.QUOTE: config.BOT_ACTION_CAP,
                                                                 Priority.TAKE: 100})
                self.warmed = set()
            for ev in ("prelim", "final"):
                dt = config.SCHEDULE_REAL_S[ev] - now_real
                if 0 < dt < 3 and (self.clock.date, ev) not in self.warmed:
                    self.warmed.add((self.clock.date, ev))
                    asyncio.ensure_future(self.arest.warm(12))

    async def reconcile_loop(self):
        while True:
            await asyncio.sleep(config.RECONCILE_EVERY_S)
            try:
                await self.om.reconcile()
            except Exception as exc:
                self.log(f"reconcile error {exc!r}")
            if self.om.unknown_since and time.monotonic() - self.om.unknown_since > 3:
                self.risk.set_mode(risk.NO_NEW_RISK, "order state unknown > 3 s")

    # ---- safety ---------------------------------------------------------------------------------
    async def kill(self, reason):
        self.risk.set_mode(risk.FROZEN, reason)
        await self.om.cancel_all(None, Priority.KILL)

    async def kill_watch(self):
        while True:
            await asyncio.sleep(0.25)
            if self.kill_switch.triggered() and self.risk.mode != risk.FROZEN:
                await self.kill("KILL file")

    async def console(self):
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                await asyncio.sleep(1.0)
                continue
            parts = line.split()
            if not parts:
                continue
            c, a = parts[0].lower(), parts[1:]
            st = self.state
            try:
                if c == "k":
                    await self.kill("operator")
                elif c == "cx":
                    await self.om.cancel_all(None, Priority.HEDGE)
                elif c == "p":
                    self.risk.set_mode(risk.NO_NEW_RISK, "operator pause")
                elif c == "r":
                    self.risk.set_mode(risk.NORMAL, "operator resume")
                elif c == "x":
                    self.risk.disabled.add(a[0])
                    await self.om.cancel_all(a[0], Priority.HEDGE)
                elif c == "e":
                    self.risk.disabled.discard(a[0])
                elif c == "f":
                    ov = st.overrides.setdefault(a[0], {})
                    ov["fair_abs"] = None if a[1] == "off" else float(a[1])
                elif c == "m":
                    st.overrides.setdefault(a[0], {})["fair_mult"] = float(a[1])
                    st.overrides[a[0]].pop("needs_operator", None)
                elif c == "s":
                    st.overrides.setdefault(a[0], {})["sigma_mult"] = float(a[1])
                elif c == "q":
                    for s in self.strats:
                        if s.name == "quoter":
                            s.p["phases"] = ["pre_storm", "aftermath"] if a[0] == "on" else []
                elif c == "b":
                    self.limiter.cap = min(int(a[0]), config.TEAM_ACTIONS_PER_MIN - config.SAFETY_MARGIN)
                elif c == "a":
                    for s, ov in st.overrides.items():
                        if ov.get("needs_operator"):
                            self.log(f"ANNOUNCEMENT {s}: {ov['needs_operator']}")
                elif c == "st":
                    self.status()
                else:
                    self.log(f"unknown command {line.strip()}")
            except (IndexError, ValueError) as exc:
                self.log(f"bad command {line.strip()} ({exc})")

    def status(self):
        st = self.state
        self.log(f"mode={self.risk.mode} round={st.round} phase={st.phase} t={self.clock.market_now_str()} "
                 f"feed={'up' if self.feed.connected else 'DOWN'} seq={self.normalizer.max_seq} "
                 f"dups={self.normalizer.duplicates} gaps={len(self.normalizer.gaps)} "
                 f"actions={self.limiter.used()}/{self.limiter.cap} orders={len(st.orders)} mtm={st.pnl_mtm:,.0f} "
                 f"pos={ {s: q for s, q in st.pos.items() if q} } loss={st.ledger.season_loss():.1f}")

    async def status_loop(self):
        while True:
            await asyncio.sleep(5.0)
            self.status()

    async def persist_loop(self):
        path = os.path.join(self.args.log_dir, "state.json")
        while True:
            await asyncio.sleep(config.PERSIST_EVERY_S)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                f.write(self.state.to_json())
            os.replace(tmp, path)

    async def run(self):
        r = await self.arest.call("get_case")
        if r.ok and isinstance(r.data, dict):
            for d in config.apply_case(r.data):
                self.log("case.json differs from packet: " + d)
            self.state.limits = dict(config.LIMITS)
        else:
            self.log(f"case.json not loaded ({r}); using packet values")
        saved = os.path.join(self.args.log_dir, "state.json")
        if os.path.exists(saved):
            with open(saved) as f:
                self.state.load_json(f.read())
            self.log("restored damage ledger / overrides from state.json (verify vs next snapshot)")
        tasks = [self.feed.run(), self.consume(), self.ticker(), self.clock_sync(), self.schedule(),
                 self.reconcile_loop(), self.kill_watch(), self.console(), self.status_loop(), self.persist_loop()]
        await asyncio.gather(*tasks)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--key-file", default=None, help="file containing only the bot key")
    ap.add_argument("--network", action="store_true", help="allow sockets at all")
    ap.add_argument("--i-reviewed-untested-skeleton", action="store_true", dest="reviewed")
    ap.add_argument("--live", action="store_true", help="send real orders (default: dry-run)")
    ap.add_argument("--stdlib-ws", action="store_true", help="use the untested stdlib WebSocket fallback")
    ap.add_argument("--params", default=None, help="fitted model params JSON (default: house-follow, no trading)")
    ap.add_argument("--scenarios", default=None)
    ap.add_argument("--log-dir", default="runlogs")
    args = ap.parse_args()
    if not (args.network and args.reviewed):
        print("Refusing to start: this is an UNTESTED skeleton. Pass --network and --i-reviewed-untested-skeleton "
              "after review. Use replay.py for offline work.")
        return 2
    key = os.environ.get(config.API_KEY_ENV)
    if args.key_file:
        with open(args.key_file) as f:
            key = f.read().strip()
    if not key:
        print(f"no bot key: set {config.API_KEY_ENV} or pass --key-file")
        return 2
    os.makedirs(args.log_dir, exist_ok=True)
    rest.NETWORK_ALLOWED = True
    bot = Bot(args, key)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        tk = bot.limiter.try_acquire(Priority.KILL, 1, "ctrl-c")
        res = bot.client.cancel_all(tk[0]) if tk else None      # best effort, synchronous
        print(f"interrupted; cancel-all -> {res}. VERIFY in the browser that no orders are resting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
