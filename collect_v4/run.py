"""
python -m collect_v4.run            # one collection pass (what the scheduler runs)
python -m collect_v4.run --status   # gateway budget/breaker status

Data directory: $ARBITREDGE_V4_DATA (default ~/ArbitrEdgeV4/data).
Gateway socket: $ARBITREDGE_V4_SOCKET (default ~/ArbitrEdgeV4/run/gateway.sock).
"""

import argparse
import json
import os

from collect_v4.collector import Collector
from edge_v4.odds_client import OddsClient

HOME = os.path.expanduser("~/ArbitrEdgeV4")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args(argv)
    client = OddsClient(os.environ.get("ARBITREDGE_V4_SOCKET", os.path.join(HOME, "run", "gateway.sock")))
    if a.status:
        print(json.dumps(client.status(), indent=2))
        return
    c = Collector(client, os.environ.get("ARBITREDGE_V4_DATA", os.path.join(HOME, "data")))
    for r in c.run_once():
        print(json.dumps(r))


if __name__ == "__main__":
    main()
