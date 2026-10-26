"""Run the synthetic framework controls and save docs_v4/controls_synthetic.json."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edge_v4 import backtest as B, ledger  # noqa: E402

if __name__ == "__main__":
    conn = ledger.connect()
    out = {}
    for sc in ("positive", "negative", "noisy_consensus"):
        sid = B.synthetic_line_shop(conn, sc, n_games=3000, seed=7)
        r = B.summarize(conn, sid, n_boot=2000)
        s = r["summary"]
        out[sc] = {"bets": r["bets"], "edge_status": s["edge_status"] if s else "NO BETS",
                   "roi": s["record"]["roi"] if s else None, "roi_ci": [s["roi_ci"]["roi_lo"], s["roi_ci"]["roi_hi"]] if s else None,
                   "mean_clv": s["mean_clv"] if s else None, "mean_model_ev": s["mean_model_ev"] if s else None,
                   "max_drawdown": s["max_drawdown"] if s else None}
        print(sc, out[sc])
    json.dump(out, open("docs_v4/controls_synthetic.json", "w"), indent=2)
