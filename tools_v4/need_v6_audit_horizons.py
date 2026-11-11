"""Re-run the v6 development replay, additionally capturing each game-market's card at T-48/24/6/1 h (no engine change)."""
import json, sys
sys.path.insert(0, "/home/user/arbitredge-v4")
from need_v6 import need as NEED, replay as RP
from sportsbook_v4.util import to_epoch
S = sys.argv[1]
H = (48, 24, 6, 1)
orig = NEED.card
cap = {}
def wrap(k, t, gm, desk, exposure, wagers, game, names):
    c = orig(k, t, gm, desk, exposure, wagers, game, names)
    kick = to_epoch(game.commence_time)
    for h in H:
        if kick - t >= h * 3600:
            cap[(gm.sport, k[0], k[1], h)] = c
    return c
NEED.card = wrap
rep = RP.run(S + "/v6/market.db", S + "/build/rankings", S + "/fx/results")
rep["horizons"] = [{"sport": s, "event_id": e, "market": m, "h": h, "card": c} for (s, e, m, h), c in cap.items()]
json.dump(rep, open(S + "/audit/replay_h.json", "w"), default=str)
print(len(rep["frozen"]), len(rep["horizons"]))
