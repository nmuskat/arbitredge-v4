import pytest

from data_v4 import rankings as R

DAY = 86400
T0 = 1_793_000_000


def _utc(y, mo, d, h, mi=0):
    import calendar
    return calendar.timegm((y, mo, d, h, mi, 0))

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


def test_stale_poll_is_never_unranked():
    s = R.top25_status("Texas Longhorns", "Navy Midshipmen", T0 + 30 * DAY, [snap(T0, 9, ["Texas"])])
    assert s["status"] == "PENDING" and s["top25"] is None      # a newer weekly poll is due before kickoff


def test_old_poll_in_bowl_season_beyond_limit_is_unavailable():
    fetched = _utc(2026, 12, 7, 0)
    assert R.top25_status("Texas Longhorns", "Navy Midshipmen", fetched + 60 * DAY,
                          [snap(fetched, 16, ["Texas"])])["status"] == "UNAVAILABLE"


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
    assert all(re.fullmatch(r"actions/checkout@[0-9a-f]{40}", u) or u == "./.github/workflows/site-build.yml"
               for u in re.findall(r"uses: (\S+)", text))
    assert text.count("secrets.") == 1                    # the key, in the fetch step only
    for f in os.listdir(wf_dir):
        if f != "cfb-rankings.yml":
            assert "CFBD_API_KEY" not in open(os.path.join(wf_dir, f)).read()


# ---- real cases from the first live CFBD snapshot (2026-10-28)

REAL_TEAMS = [
    {"school": "Southern Miss", "mascot": "Golden Eagles", "alternate_names": ["Southern Mississippi", "USM", "Southern Miss"]},
    {"school": "San José State", "mascot": "Spartans", "alternate_names": ["San Jose St.", "SJSU", "San José St"]},
    {"school": "App State", "mascot": "Mountaineers", "alternate_names": ["Appalachian State", "APP", "App State"]},
    {"school": "Louisiana", "mascot": "Ragin' Cajuns", "alternate_names": ["UL Lafayette", "UL", "Louisiana"]},
    {"school": "UL Monroe", "mascot": "Warhawks", "alternate_names": ["La.-Monroe", "ULM", "UL Monroe"]},
    {"school": "Louisiana Tech", "mascot": "Bulldogs", "alternate_names": ["LT", "Louisiana Tech"]},
    {"school": "Miami", "mascot": "Hurricanes", "alternate_names": ["Miami (FL)", "MIA", "Miami"]},
    {"school": "Miami (OH)", "mascot": "RedHawks", "alternate_names": ["M-OH", "Miami OH"]},
    {"school": "Hawai'i", "mascot": "Rainbow Warriors", "alternate_names": ["HAW", "Hawai'i"]},
    {"school": "Georgia Southern", "mascot": "Eagles", "alternate_names": ["GASO", "GA Southern"]},
]


@pytest.mark.parametrize("name,school", [
    ("Southern Mississippi Golden Eagles", "Southern Miss"), ("San Jose State Spartans", "San José State"),
    ("Appalachian State Mountaineers", "App State"), ("UL Monroe Warhawks", "UL Monroe"),
    ("Louisiana Ragin' Cajuns", "Louisiana"), ("Louisiana Tech Bulldogs", "Louisiana Tech"),
    ("Miami (OH) RedHawks", "Miami (OH)"), ("Miami Hurricanes", "Miami"), ("Hawaii Rainbow Warriors", "Hawai'i"),
    ("Georgia Southern Eagles", "Georgia Southern"), ("Southern Eagles", None)])
def test_real_provider_names_match_cfbd(name, school):
    assert R.TeamMatcher(REAL_TEAMS).school_for(name) == school


def test_name_fitting_two_schools_is_unmatched():
    teams = [{"school": "Alpha", "mascot": "Owls", "alternate_names": ["Central"]},
             {"school": "Beta", "mascot": "Owls", "alternate_names": ["Central"]}]
    assert R.TeamMatcher(teams).school_for("Central Owls") is None


def _wk(fetched_at, kickoff):
    return R.top25_status("Texas Longhorns", "Navy Midshipmen", kickoff, [snap(fetched_at, 9, ["Texas"])])


def test_poll_week_rule():
    tue = _utc(2026, 10, 27, 13, 41)                        # Tuesday backup fetch of the week-9 poll
    assert _wk(tue, _utc(2026, 10, 31, 19, 30))["status"] == "OK"            # this Saturday
    assert _wk(tue, _utc(2026, 11, 1, 16, 0))["status"] == "OK"              # Sunday before the release
    nxt = _wk(tue, _utc(2026, 11, 7, 19, 30))                                # next Saturday: new poll due
    assert nxt["status"] == "PENDING" and nxt["top25"] is None
    assert nxt["provisional"]["away_rank"] is None and nxt["provisional"]["home_rank"] == 1
    sun = _utc(2026, 11, 1, 21, 41)                         # Sunday fetch after the release
    assert _wk(sun, _utc(2026, 11, 7, 19, 30))["status"] == "OK"


def test_bowl_season_uses_final_poll():
    final = _utc(2026, 12, 6, 21, 41)
    assert _wk(final, _utc(2026, 12, 12, 20, 0))["status"] == "OK"           # Army-Navy week
    assert _wk(final, _utc(2026, 12, 27, 20, 0))["status"] == "OK"           # bowl, 3 weeks later
    assert _wk(final, _utc(2027, 1, 12, 0, 30))["status"] == "OK"            # title game
