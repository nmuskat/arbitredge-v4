"""Experimental public-bettor calibration: observed-split store, popularity index, experimental hooks, metrics.
None of this may change the active m4 sportsbook."""

import csv
import importlib.util
import os

import pytest

from data_v4 import observed_splits as OS
from sportsbook_v4 import engine as E
from sportsbook_v4 import params as P
from sportsbook_v4 import popularity

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tool(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, "tools_v4", f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SC = _tool("split_calibration")


def row(**k):
    base = {"sport": "nfl", "home": "Green Bay Packers", "away": "Carolina Panthers", "kickoff_et_date": "2026-10-29",
            "sportsbook": "DraftKings", "aggregation": "single_book", "market": "spread", "selection": "home",
            "line": "-3.5", "ticket_pct": "70", "handle_pct": "65", "observed_at": "2026-10-29T22:00:00Z",
            "source": "TEST FIXTURE", "source_url": "", "collection_method": "manual_entry", "entered_by": "test",
            "notes": ""}
    base.update(k)
    return base


# ------------------------------------------------------------------ observed splits store

def test_observed_record_is_labelled_and_separate_from_modeled():
    r = OS.normalize(row())
    assert r["label"] == "OBSERVED_THIRD_PARTY" and r["sportsbook"] == "draftkings"
    import sportsbook_v4
    src = "".join(open(os.path.join(os.path.dirname(sportsbook_v4.__file__), f)).read()
                  for f in os.listdir(os.path.dirname(sportsbook_v4.__file__)) if f.endswith(".py"))
    assert "observed_splits" not in src                 # the synthetic sportsbook never reads observed data


@pytest.mark.parametrize("bad,msg", [
    ({"ticket_pct": "101"}, "out of range"), ({"ticket_pct": "", "handle_pct": ""}, "at least one"),
    ({"collection_method": "scraper"}, "no scraping"), ({"market": "spread", "selection": "over"}, "valid market"),
    ({"observed_at": "yesterday"}, "observed_at"), ({"source": ""}, "missing source"),
    ({"aggregation": "consensus"}, "aggregation")])
def test_invalid_observations_are_rejected(bad, msg):
    with pytest.raises(OS.InvalidObservation, match=msg):
        OS.normalize(row(**bad))


def test_blank_percentage_stays_blank_and_pairs_must_sum_to_100():
    a = OS.normalize(row(handle_pct=""))
    assert a["handle_pct"] is None and a["ticket_pct"] == 70.0      # never inferred from the other side
    b = OS.normalize(row(selection="away", ticket_pct="30", handle_pct="40"))
    assert OS.check_pairs([a, b]) == []
    c = OS.normalize(row(selection="away", ticket_pct="45", handle_pct="35"))
    assert any("ticket_pct sides sum to 115" in p for p in OS.check_pairs([a, c]))


def test_csv_import_round_trip_and_tamper_detection(tmp_path):
    path = tmp_path / "obs.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OS.FIELDS)
        w.writeheader()
        w.writerow(row())
        w.writerow(row(selection="away", ticket_pct="30", handle_pct="35"))
    recs, errors = OS.read_csv(path)
    assert errors == [] and len(recs) == 2
    out = OS.write(recs, str(tmp_path / "store"), now=1_800_000_000)
    assert OS.load(str(tmp_path / "store")) == recs
    OS.write(recs, str(tmp_path / "store"), now=1_800_000_001)          # same observations re-entered
    assert len(OS.load(str(tmp_path / "store"))) == 2                  # count once
    import gzip
    import json
    doc = json.load(gzip.open(out, "rt"))
    doc["records"][0]["ticket_pct"] = 99.0
    json.dump(doc, gzip.open(out, "wt"))
    with pytest.raises(ValueError, match="verification"):
        OS.load(str(tmp_path / "store"))


def test_template_has_headers_only():
    lines = open(os.path.join(REPO, "docs_v4", "templates", "observed_splits_template.csv")).read().strip().splitlines()
    assert lines == [",".join(OS.FIELDS)]                              # no example numbers that could pass as data


# ------------------------------------------------------------------ popularity index

def _rows(ind, vals):
    return [{"team": t, "indicator": ind, "value": v} for t, v in vals.items()]


def test_popularity_needs_coverage_and_preserves_total_volume():
    teams = [f"t{i}" for i in range(10)]
    pop, rep = popularity.index(_rows("tv_viewers_avg", {t: 1e6 * (i + 1) for i, t in enumerate(teams[:8])}), teams)
    assert set(pop.values()) == {1.0} and rep["tv_viewers_avg"]["used"] is False        # 80% < 90%: not used
    full = _rows("tv_viewers_avg", {t: 1e6 * (i + 1) for i, t in enumerate(teams)})
    pop, rep = popularity.index(full, teams)
    import math
    assert rep["tv_viewers_avg"]["used"] and pop["t9"] > pop["t0"]
    assert abs(sum(math.log(v) for v in pop.values())) < 1e-4                          # geometric mean 1
    assert all(popularity.MIN_MULT * 0.5 <= v <= popularity.MAX_MULT * 2 for v in pop.values())


def test_popularity_inputs_must_cite_sources(tmp_path):
    p = tmp_path / "in.csv"
    p.write_text("team,indicator,value,source,url,retrieved_at\nnfl_x,tv_viewers_avg,100,,,\n")
    with pytest.raises(ValueError, match="source"):
        popularity.read_inputs(str(p))
    for sport in ("nfl", "cfb"):
        assert popularity.read_inputs(os.path.join(REPO, "data_v4", "popularity", f"inputs_{sport}_2026.csv")) == []


# ------------------------------------------------------------------ experimental hooks change nothing in m4

def test_active_model_is_untouched_by_the_experimental_module():
    h = P.params_hash()
    import sportsbook_v4.params_calib as PC
    kw = PC.m5_draft1("nfl", ["nfl_a", "nfl_b"])
    assert P.params_hash() == h and "fav_spread_curve" not in P.ARCHETYPES["REC_CASUAL"]
    with P.variant("m5_draft1", **kw):
        assert P.params_hash() != h and P.ARCHETYPES["HIGH_STAKES_REC"]["bankroll_median"] == 15000.0
    assert P.params_hash() == h and P.TEAM_POPULARITY_PRIOR == {}


def test_variant_team_popularity_is_restored():
    with P.variant("pop", team_popularity={"nfl_a": 1.5}):
        from sportsbook_v4.population import team_popularity
        assert team_popularity("nfl_a") == 1.5
    assert P.TEAM_POPULARITY_PRIOR == {}


def test_fav_spread_curve_defaults_to_one_and_interpolates():
    assert E._fav_scale({"fav_pref": 1}, 7.0) == 1.0
    arch = {"fav_spread_curve": [(0.0, 0.6), (3.5, 1.0), (10.0, 1.25)]}
    assert E._fav_scale(arch, 0.0) == 0.6 and E._fav_scale(arch, 30.0) == 1.25
    assert E._fav_scale(arch, 6.75) == pytest.approx(1.125)


# ------------------------------------------------------------------ calibration metrics

def obs(sel, t, h, sport="nfl", market="spread", date="2026-10-29", game=("nfl", "g1"), book="draftkings", mt=None, mh=None):
    return {"sport": sport, "market": market, "selection": sel, "ticket_pct": t, "handle_pct": h, "sportsbook": book,
            "kickoff_et_date": date, "observed_at": "2026-10-29T22:00:00Z", "game_key": game,
            "modeled_ticket_pct": mt, "modeled_handle_pct": mh}


def test_metrics_errors_agreement_and_calibration_bins():
    rows = [obs("home", 70, 75, mt=60, mh=55), obs("away", 30, 25, mt=40, mh=45),
            obs("home", 40, 35, game=("nfl", "g2"), mt=55, mh=60), obs("away", 60, 65, game=("nfl", "g2"), mt=45, mh=40)]
    s = SC.summarize(rows)
    assert s["ticket_pct"] == {"n": 4, "mae": 12.5, "rmse": 12.75, "bias": 0.0}
    assert s["directional_agreement"]["ticket_pct"] == {"n": 2, "agree": 1, "rate": 0.5}
    assert s["handle_calibration_by_observed_pressure"]["70-80"] == {"n": 1, "mean_observed": 75.0, "mean_modeled": 55.0}
    assert s["handle_calibration_by_observed_pressure"]["60-70"]["mean_modeled"] == 40.0


def test_train_holdout_split_is_chronological_and_claims_need_a_large_holdout():
    rows = [obs("home", 60, 60, date="2026-10-29", mt=55, mh=55), obs("home", 60, 60, date="2026-11-12", mt=55, mh=55)]
    res = SC.evaluate(rows, "2026-11-10")
    assert res["train"]["observations"] == 1 and res["holdout"]["observations"] == 1
    assert res["verdict"].startswith("INSUFFICIENT HOLDOUT")
    assert SC.evaluate([], "2026-11-10")["verdict"] == "NO OBSERVED DATA"


def test_missing_model_wagers_are_reported_not_filled():
    rows = SC.attach([dict(obs("home", 60, 60), game_key=("nfl", "nope"))], {})
    assert rows[0]["modeled_ticket_pct"] is None
    assert SC.evaluate(rows, "2026-11-10")["unmatched_or_no_model_wagers"] == 1


def test_modeled_split_uses_only_wagers_before_the_observation():
    ws = [{"market": "spread", "side": "home", "stake": 100.0, "accepted_at": "2026-10-29T20:00:00Z"},
          {"market": "spread", "side": "away", "stake": 300.0, "accepted_at": "2026-10-29T21:00:00Z"},
          {"market": "spread", "side": "away", "stake": 999.0, "accepted_at": "2026-10-29T23:00:00Z"}]
    from sportsbook_v4.util import to_epoch
    assert SC.modeled_split(ws, "spread", "home", to_epoch("2026-10-29T22:00:00Z")) == (50.0, 25.0)


def test_match_by_names_and_eastern_date():
    games = [{"sport": "nfl", "event_id": "e1", "home": "Green Bay Packers", "away": "Carolina Panthers",
              "kickoff": "2026-10-30T00:15:00Z"}]
    m = SC.match([OS.normalize(row())], games)
    assert m[0]["game_key"] == ("nfl", "e1")
    assert SC.match([OS.normalize(row(kickoff_et_date="2026-10-30"))], games)[0]["game_key"] is None


def test_end_to_end_on_a_small_built_book():
    import sqlite3
    from tests_v4 import test_live_book as T
    from sportsbook_v4 import live_book as LB
    live, book = T.build_store(), T.new_book()
    LB.build(book, live, T.NOW, [T.poll(T.T0 - 86400)], bettors=500, variants={})
    g = book.execute("SELECT canonical_game_id FROM v4_wager WHERE market='spread' LIMIT 1").fetchone()[0]
    home, away = LB._names(live, g)
    kick = book.execute("SELECT games_json FROM v4_run WHERE sport='nfl'").fetchone()
    import datetime
    import json
    from zoneinfo import ZoneInfo
    k = next(x["commence_time"] for x in json.loads(kick[0]) if x["canonical_game_id"] == g)
    from sportsbook_v4.util import to_epoch
    d = datetime.datetime.fromtimestamp(to_epoch(k), ZoneInfo("America/New_York")).date().isoformat()
    o = OS.normalize(row(home=home, away=away, kickoff_et_date=d, observed_at=k))
    res = SC.run([o], book, live, holdout_from="2099-01-01")
    assert res["train"]["observations"] == 1 and res["unmatched_or_no_model_wagers"] == 0
    res2 = SC.run([o], book, live, holdout_from="2099-01-01", candidate="m5_draft1")
    assert res2["model"] == "m5_draft1" and res2["train"]["observations"] == 1
    assert isinstance(sqlite3, object)


# ------------------------------------------------------------------ issue-form manual entry

FORM = """### Sport

nfl

### Away team (as listed)

Carolina Panthers

### Home team (as listed)

Green Bay Packers

### Kickoff date, Eastern (YYYY-MM-DD)

2026-10-29

### Sportsbook the split is for (e.g. draftkings, circa)

DraftKings

### One sportsbook or several?

single_book

### Market

spread

### Line shown (home spread or total; blank for moneyline)

-3.5

### First side: home / over — tickets %, handle % (e.g. 62, 48)

71%, 64%

### Second side: away / under — tickets %, handle %

29, 36

### When you saw it (e.g. 2026-11-01 12:05 ET)

2026-10-29 18:05 ET

### Where it was published (site / page name)

TEST FIXTURE

### Page URL (optional)

_No response_
"""


def test_issue_form_parses_into_two_validated_records(tmp_path, monkeypatch):
    from data_v4 import observed_issue as OI
    recs = OI.records(OI.parse_body(FORM), 7)
    assert [(r["selection"], r["ticket_pct"], r["handle_pct"]) for r in recs] == [("home", 71.0, 64.0), ("away", 29.0, 36.0)]
    assert recs[0]["observed_at"] == "2026-10-29T22:05:00Z" and recs[0]["source_url"] == ""
    bad = FORM.replace("29, 36", "49, 36")
    with pytest.raises(OS.InvalidObservation, match="sum to"):
        OI.records(OI.parse_body(bad), 7)
    monkeypatch.setenv("BODY", FORM)
    assert OI.main(["--out", str(tmp_path), "--issue", "7"]) == 0 and len(OS.load(str(tmp_path))) == 2


def test_issue_workflow_is_owner_only_and_has_no_secrets():
    import yaml
    text = open(os.path.join(REPO, ".github", "workflows", "observed-splits.yml")).read()
    assert "secrets." not in text and "pull_request" not in text  # comment wording avoids the word too and "${{ github.event.issue.body }}" in text
    job = yaml.safe_load(text)["jobs"]["record"]
    assert "github.event.issue.user.login == github.repository_owner" in job["if"]
    run = job["steps"][1]["run"]
    assert "github.event" not in run                                   # the body reaches the script as data only
