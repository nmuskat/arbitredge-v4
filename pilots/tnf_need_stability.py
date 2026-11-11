# Reproduces the production NFL week run for the TNF pilot (seed 0 == production bet for bet) and re-runs it with
# independent random seeds. Usage: python pilots/tnf_need_stability.py <scratch dir with tnf/shards, prod/book, prod/settle> 20261110T160049Z
# The second argument excludes settlement files written at/after that time (the production run was created from the settlements
# available before it).
import glob, gzip, json, sys, collections, sqlite3
sys.path.insert(0, "/home/user/arbitredge-v4")
from analysis_v4.actions_analyze import rebuild
from sportsbook_v4 import live_book as LB
from sportsbook_v4 import settlement as ST
from sportsbook_v4.engine import SlateSpec, GameSpec, create_run, load_run, compute_rng_key
from sportsbook_v4.population import ensure_population, load_population
from sportsbook_v4.schema import ensure_schema
from sportsbook_v4.util import to_epoch
from lab_v4.runner import run_week
from lab_v4.config import CONFIGS
S = sys.argv[1]
GID = "74d39e19225caf211ad2c8eb4110179f"
UNTIL = to_epoch("2026-11-11T13:13:10Z")
live, counts = rebuild(sorted(glob.glob(f"{S}/tnf/shards/quotes_*.jsonl.gz")), ":memory:")
# production run record
runs, wag = {}, {}
for p in glob.glob(f"{S}/prod/book/book_nfl_*.json.gz"):
    d = json.load(gzip.open(p, "rt"))
    runs[d["run"]["run_id"]] = d["run"]
    for w in d["wagers"]:
        wag[w["wager_id"]] = w
R = runs[[k for k in runs if k.startswith("14605149fa17")][0]]
games = tuple(GameSpec(g["canonical_game_id"], g["home_team_id"], g["away_team_id"], g["commence_time"],
                       g["standalone"], g["national_tv"]) for g in json.loads(R["games_json"]))
slate = SlateSpec("nfl", R["slate_id"], R["window_start"], R["window_end"], games)
book = sqlite3.connect(":memory:"); ensure_schema(book)
pid = ensure_population(book, "nfl", json.loads(json.load(gzip.open(sorted(glob.glob(f"{S}/prod/book/book_nfl_20261110_*"))[0], "rt"))["population"]["team_universe"]) if isinstance(json.load(gzip.open(sorted(glob.glob(f"{S}/prod/book/book_nfl_20261110_*"))[0], "rt"))["population"]["team_universe"], str) else json.load(gzip.open(sorted(glob.glob(f"{S}/prod/book/book_nfl_20261110_*"))[0], "rt"))["population"]["team_universe"], bettor_count=10000)
assert pid == R["population_id"], (pid, R["population_id"])
print("rng key reproduced:", compute_rng_key(pid, slate) == R["rng_key"])
# week-open bankrolls: opening + settled P&L of earlier r2 runs of this population (production settlement files)
prior = {rid for rid, r in runs.items() if r["engine_version"] == R["engine_version"] and r["population_id"] == pid
         and r["window_end"] <= R["window_start"]}
recs = {}
for p in sorted(p for p in glob.glob(f"{S}/prod/settle/*.json.gz") if p.split("settle_")[-1] < sys.argv[2]):
    for r in json.load(gzip.open(p, "rt"))["records"]:
        recs[r["settlement_id"]] = r
sup = {r["supersedes"] for r in recs.values() if r.get("supersedes")}
pnl = collections.Counter()
for r in recs.values():
    if r["settlement_id"] not in sup and r["run_id"] in prior:
        pnl[r["bettor_index"]] += r["pnl_cents"]
pop = load_population(book, pid)
snap = {b["bettor_index"]: max(0.0, round(b["bankroll"] + pnl.get(b["bettor_index"], 0) / 100.0, 2)) for b in pop["bettors"]}
print("prior r2 runs:", len(prior), "bettors with settled P&L:", len(pnl))
rid = create_run(book, pid, slate, LB._market(live, "nfl"), bankroll_snapshot=[[i, v] for i, v in sorted(snap.items())])
run = load_run(book, rid)
print("run id reproduced:", rid == R["run_id"])
prod = [w for w in wag.values() if w["run_id"] == R["run_id"]]
key = lambda w: (w["wager_id"], w["side"], float(w["accepted_line"] or 0), float(w["accepted_price"]), float(w["stake"]))
out = {}
for seed in range(0, 21):
    ws, _ = run_week(run, pop, snap, LB._market(live, "nfl"), CONFIGS["A"], seed, until=UNTIL)
    if seed == 0:
        same = sorted(map(key, ws)) == sorted(map(key, prod))
        print("seed 0 reproduces production:", same, len(ws), "vs", len(prod))
    g = [w for w in ws if w["canonical_game_id"] == GID]
    res = {}
    for m in ("spread", "total"):
        x = [w for w in g if w["market"] == m]
        e = ST.exposure(x) if m == "spread" else ST.total_exposure(x)
        a, b = ("pnl_if_home_covers_all", "pnl_if_away_covers_all") if m == "spread" else ("pnl_if_over_all", "pnl_if_under_all")
        big = {id(w) for w in sorted(x, key=lambda w: -w["stake"])[:5]}
        r5 = [w for w in x if id(w) not in big]
        e5 = ST.exposure(r5) if m == "spread" else ST.total_exposure(r5)
        res[m] = {"side": e.get("financial_need_side"), "a": e.get(a), "b": e.get(b), "handle": e.get("handle"),
                  "robust5": e5.get("financial_need_side") == e.get("financial_need_side")}
    out[seed] = res
json.dump(out, open(f"{S}/tnf/stability.json", "w"), indent=1)
for m in ("spread", "total"):
    c = collections.Counter(out[s][m]["side"] for s in out)
    print(m, "NEED side across 21 runs:", dict(c), "| seed0:", out[0][m]["side"], "| robust-5 share:", sum(out[s][m]["robust5"] for s in out) / len(out))
