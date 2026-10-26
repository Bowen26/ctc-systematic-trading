"""CTC 2026 Systematic Equities bot -- configuration.

*** UNTESTED SKELETON. NEVER RUN AGAINST ANY EXCHANGE WITHOUT A FULL REVIEW AND A TESTNET PASS. ***

No keys live in this file. The bot key is read from the environment variable named by
API_KEY_ENV, or from a file passed with --key-file. Do not commit keys.

Everything that case.json also provides (limits, baskets, sites, timing) is a FALLBACK copy of
the packet values; main.py replaces them with GET /cases/hurricane.json at start-up and logs any
difference.
"""

# ---- network ---------------------------------------------------------------------------------
# Packet host. Override with --host. (The starter-kit README uses a subdomain of this host for
# the hurricane case; confirm the live host in #questions before game day.)
HOST = "cornelltradingcompetition.org"
API_PREFIX = "/api/v1"
WS_PATH = "/ws/v1/market-data"
CASE_PATH = "/cases/hurricane.json"
# The network in front of the exchange refuses urllib's default User-Agent: always send ours.
USER_AGENT = "ctc26-team-bot/0.1 (python-stdlib)"
API_KEY_ENV = "CTC_BOT_KEY"
HTTP_TIMEOUT_S = 2.0            # order actions: short timeout, never auto-retried
HTTP_GET_TIMEOUT_S = 3.0
HTTP_WORKERS = 16               # parallel keep-alive connections (snipe fan-out)

# ---- instruments (packet section 3 / case.json) ------------------------------------------------
SYMBOLS = ["PORT", "ISLE", "AQUA", "GRID", "FERRY", "RECON", "MAT", "GEN", "HAVEN", "REIN", "ARCH", "REBLD"]
STOCKS = SYMBOLS[:10]
ETFS = ["ARCH", "REBLD"]
LIMITS = {"PORT": 200, "ISLE": 300, "AQUA": 500, "GRID": 400, "FERRY": 800, "RECON": 500, "MAT": 700,
          "GEN": 600, "HAVEN": 250, "REIN": 200, "ARCH": 50, "REBLD": 100}
START_PRICES = {"PORT": 72.48, "ISLE": 50.26, "AQUA": 27.33, "GRID": 36.41, "FERRY": 17.91, "RECON": 52.38,
                "MAT": 35.81, "GEN": 40.30, "HAVEN": 39.55, "REIN": 89.36, "ARCH": 289.20, "REBLD": 216.68}
BASKETS = {"ARCH": {"PORT": 1, "ISLE": 1, "AQUA": 2, "GRID": 1, "FERRY": 2, "HAVEN": 1},
           "REBLD": {"RECON": 2, "MAT": 2, "GEN": 1}}
PRICE_DECIMALS = 6              # exchange accepts up to 6 decimals
MAX_RESTING_PER_SYMBOL = 200    # exchange cap; we self-cap far lower
SELF_RESTING_CAP = 6            # our own cap per symbol (budget, not exchange, is the constraint)

# ---- clock (packet section 2 + case.json timing) -----------------------------------------------
CLOCK_SPEED = 30                # 1 real second = 30 market seconds
ROUND_REAL_S = 780              # 09:30-16:00 = 13 real minutes
SCHEDULE_REAL_S = {             # real seconds after the open of each round
    "open": 0, "storm_formed": 60, "advisory_first": 90, "advisory_every": 60,
    "storm_dissipated": 540, "prelim": 570, "final": 740, "close": 780,
}
# Windows around scheduled events in which quote refresh is blocked and the TAKE floor is raised,
# so the limiter is guaranteed to hold budget for the snipe: (event, seconds_before, seconds_after)
BLACKOUTS = [("prelim", 20, 6), ("final", 60, 40)]

# ---- rate limits (packet section 8) -------------------------------------------------------------
TEAM_ACTIONS_PER_MIN = 180      # binds before the 300/min per-key limit
MANUAL_RESERVE_PER_MIN = 15     # left for humans in the browser (if browser orders count: ASK)
SAFETY_MARGIN = 5
BOT_ACTION_CAP = TEAM_ACTIONS_PER_MIN - MANUAL_RESERVE_PER_MIN - SAFETY_MARGIN   # 160
LIMITER_WINDOW_S = 61.0         # 60 s + 1 s clock-skew pad -> safe for fixed, sliding or bucket windows
# An action of priority P is allowed only if (available - n) >= FLOOR[P].
# KILL=0 SNIPE=1 HEDGE=2 TAKE=3 QUOTE=4  (see ratelimit.Priority)
PRIORITY_FLOORS = {0: 0, 1: 2, 2: 12, 3: 35, 4: 70}
REQUESTS_PER_S_CAP = 250        # team cap is 600 req/s for the whole API; we stay far below
WS_MAX_SEND_PER_S = 5           # the exchange closes a connection that sends >10 frames in 1 s
WS_IDLE_TIMEOUT_S = 75.0        # snapshot every 30 s; reconnect if nothing for this long
WS_SUBSCRIBE_CHANNELS = ["news", "quotes", "trades", "orderbook"]   # ONE message, all channels
RECONNECT_ON_NEW_SEASON = True  # testnet keeps the socket; we reconnect during the pause anyway

# ---- polling ----------------------------------------------------------------------------------
RECONCILE_EVERY_S = 0.5         # GET /orders/open + GET /positions (no private fill channel known)
CLOCK_POLL_EVERY_S = 1.0        # GET /competitions/time
PERSIST_EVERY_S = 5.0

# ---- risk -------------------------------------------------------------------------------------
MAX_ORDER_FRACTION = 1.0        # max order size as a fraction of the symbol limit
PRICE_BAND = 0.25               # reject limit prices further than this from house mid (fat finger)
SNIPE_PRICE_BAND = 0.60         # wider band for event snipes (real repricings can be ~50%)
STALE_FEED_S = 6.0              # storm phase: no storm obs for this long -> no new risk
MAX_DRAWDOWN_USD = 6000.0       # MTM drop from peak that switches to NO_NEW_RISK
KILL_FILE = "KILL"              # touch this file in the run directory to kill

# ---- strategy defaults (to be tuned by replay sweeps) ------------------------------------------
STRAT = {
    "taker": {"enabled": True, "k_sigma": 1.5, "min_edge_bp": 25, "z_full": 3.0, "min_clip_frac": 0.05,
              "cooldown_s": 2.0, "max_slip_bp": 150},
    "quoter": {"enabled": False, "symbols": [], "half_width_bp": 40, "size_frac": 0.05,
               "requote_frac_of_half_width": 0.5, "max_age_s": 45.0, "phases": ["pre_storm", "aftermath"]},
    "sniper": {"enabled": True, "min_edge_bp": 30, "stale_after_s": 3.0, "cancel_all_first": True},
    "etf_arb": {"enabled": False, "min_edge_bp": 40, "recycle_with_create_redeem": True},
    "settle": {"enabled": True, "eps_bp": 5, "quote_off_before_final_s": 60},
}

DRY_RUN_DEFAULT = True


def apply_case(case):
    """Overwrite the fallback constants with case.json values. Returns a list of differences."""
    diffs = []
    lim = case.get("limits") or {}
    for s, v in lim.items():
        if LIMITS.get(s) != v:
            diffs.append(f"limit {s}: packet {LIMITS.get(s)} -> case {v}")
            LIMITS[s] = v
    for e, spec in (case.get("etfs") or {}).items():
        b = spec.get("basket") or {}
        if BASKETS.get(e) != b:
            diffs.append(f"basket {e}: packet {BASKETS.get(e)} -> case {b}")
            BASKETS[e] = dict(b)
    return diffs
