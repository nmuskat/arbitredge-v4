import pytest

from data_v4 import rankings as R

DAY = 86400
T0 = 1_793_000_000

TEAMS = [{"school": s, "mascot": m} for s, m in [
    ("Texas", "Longhorns"), ("Texas A&M", "Aggies"), ("Miami", "Hurricanes"), ("Miami (OH)", "RedHawks"),
    ("Ohio State", "Buckeyes"), ("Ohio", "Bobcats"), ("Navy", "Midshipmen"), ("Notre Dame", "Fighting Irish"),
    ("Hawai'i", "Rainbow Warriors")]]


def payload(week, ranked, season_type="regular"):
    return [{"season": 2026, "seasonType": season_type, "week": week, "polls": [
        {"poll": "Coaches Poll", "ranks": [{"rank": 1, "school": "Ohio"}]},
        {"poll": "AP Top 25", "ranks": [{"rank": i + 1, "school": s} for i, s in enumerate(ranked)]}]}]


def snap(fetched_at, week, ranked):
    return {"source": "CollegeFootballData API", "fetched_at": fetched_at, "polls": R.parse_ap(payload(week, ranked)),
            "teams": TEAMS}


def test_parse_keeps_only_ap_poll():
    polls = R.parse_ap(payload(9, ["Texas", "Ohio State"]))
    assert len(polls) == 1 and polls[0]["ranks"][0] == {"rank": 1, "school": "Texas"}


@pytest.mark.parametrize("name,school", [
    ("Texas A&M Aggies", "Texas A&M"), ("Texas Longhorns", "Texas"), ("Miami (OH) RedHawks", "Miami (OH)"),
    ("Miami Hurricanes", "Miami"), ("Ohio Bobcats", "Ohio"), ("Ohio State Buckeyes", "Ohio State"),
    ("Hawai'i Rainbow Warriors", "Hawai'i"), ("Nowhere Tech Owls", None)])
def test_team_matching_never_confuses_prefix_schools(name, school):
    assert R.TeamMatcher(TEAMS).school_for(name) == school


def test_ranked_vs_unranked_counts_as_top25():
    s = R.top25_status("Navy Midshipmen", "Notre Dame Fighting Irish", T0 + 2 * DAY, [snap(T0, 9, ["Notre Dame"])])
    assert s["status"] == "OK" and s["top25"] and s["away_rank"] == 1 and s["home_rank"] is None


def test_unranked_matchup_excluded():
    s = R.top25_status("Navy Midshipmen", "Ohio Bobcats", T0 + DAY, [snap(T0, 9, ["Texas"])])
    assert s["status"] == "OK" and not s["top25"]


def test_poll_fetched_after_kickoff_is_not_used():
    s = R.top25_status("Texas Longhorns", "Navy Midshipmen", T0 - DAY, [snap(T0, 9, ["Texas"])])
    assert s["status"] == "UNAVAILABLE" and "before kickoff" in s["reason"]


def test_latest_applicable_poll_wins_rank_changes():
    snaps = [snap(T0, 9, ["Texas", "Miami"]), snap(T0 + 7 * DAY, 10, ["Miami"])]
    early = R.top25_status("Texas Longhorns", "Navy Midshipmen", T0 + 3 * DAY, snaps)
    late = R.top25_status("Texas Longhorns", "Navy Midshipmen", T0 + 9 * DAY, snaps)
    assert early["top25"] and early["poll_week"] == 9
    assert not late["top25"] and late["poll_week"] == 10       # dropped out of the poll


def test_stale_poll_is_unavailable_not_unranked():
    s = R.top25_status("Texas Longhorns", "Navy Midshipmen", T0 + 30 * DAY, [snap(T0, 9, ["Texas"])])
    assert s["status"] == "UNAVAILABLE"


def test_no_rankings_at_all_is_unavailable():
    assert R.top25_status("Texas Longhorns", "Navy Midshipmen", T0, [])["status"] == "UNAVAILABLE"


def test_unmatched_team_is_unavailable_unless_other_team_ranked():
    s = R.top25_status("Nowhere Tech Owls", "Navy Midshipmen", T0 + DAY, [snap(T0, 9, ["Texas"])])
    assert s["status"] == "UNAVAILABLE" and "not matched" in s["reason"]
    s2 = R.top25_status("Nowhere Tech Owls", "Texas Longhorns", T0 + DAY, [snap(T0, 9, ["Texas"])])
    assert s2["status"] == "OK" and s2["top25"] and s2["unmatched"] == ["Nowhere Tech Owls"]


def test_postseason_poll_preferred_over_regular_in_same_snapshot():
    sn = snap(T0, 15, ["Texas"])
    sn["polls"] += R.parse_ap(payload(1, ["Miami"], "postseason"))
    s = R.top25_status("Miami Hurricanes", "Navy Midshipmen", T0 + DAY, [sn])
    assert s["season_type"] == "postseason" and s["home_rank"] == 1


def test_fetch_uses_three_calls_and_records_provenance():
    calls = []

    def opener(url):
        calls.append(url)
        if "/teams/fbs" in url:
            return [{"school": "Texas", "mascot": "Longhorns"}]
        return payload(9, ["Texas"]) if "regular" in url else []
    s = R.fetch(2026, "k", now=T0, opener=opener)
    assert len(calls) == 3 and s["source"] == "CollegeFootballData API" and s["fetched_at"] == T0
    assert s["teams"][0]["school"] == "Texas" and s["polls"][0]["week"] == 9


def test_rankings_run_requires_key_and_never_prints_it(tmp_path, monkeypatch, capsys):
    from data_v4 import rankings_run
    monkeypatch.delenv("CFBD_API_KEY", raising=False)
    assert rankings_run.main(["--out", str(tmp_path)]) == 1
    secret = "cfbdsecret" + "123456"
    monkeypatch.setenv("CFBD_API_KEY", secret)

    def boom(*a, **k):
        raise RuntimeError(f"401 for key {secret}")
    monkeypatch.setattr(R, "fetch", boom)
    assert rankings_run.main(["--out", str(tmp_path)]) == 1
    assert secret not in capsys.readouterr().out


def test_season_boundary():
    from data_v4.rankings_run import season_for
    import calendar
    assert season_for(calendar.timegm((2027, 1, 10, 0, 0, 0))) == 2026
    assert season_for(calendar.timegm((2026, 10, 27, 0, 0, 0))) == 2026


def test_rankings_workflow_is_gated_pinned_and_only_place_with_cfbd_key():
    import os
    import re
    wf_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows")
    text = open(os.path.join(wf_dir, "cfb-rankings.yml")).read()
    assert "if: vars.CFBD_ENABLED == 'on'" in text and "pull_request" not in text
    assert all(re.fullmatch(r"actions/checkout@[0-9a-f]{40}", u) for u in re.findall(r"uses: (\S+)", text))
    for f in os.listdir(wf_dir):
        if f != "cfb-rankings.yml":
            assert "CFBD_API_KEY" not in open(os.path.join(wf_dir, f)).read()
