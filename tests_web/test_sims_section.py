"""NFL SIMS on the V5 site: validated read-only exports, rejection rules, freshness inputs, and isolation from the book."""

import ast
import gzip
import json
import pathlib
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from sims_v5 import importer, schema
from web_v4 import sims_section

FIX = pathlib.Path(__file__).parent / "fixtures" / "sims"
NOW = datetime(2026, 10, 30, 21, 30, tzinfo=timezone.utc)
ROOT = pathlib.Path(__file__).resolve().parents[1]


def _doc(kind):
    return json.load(gzip.open(next(FIX.glob(f"sims_nfl_{kind}_*.json.gz")), "rt"))


def _write(tmp, name, doc, rehash=True):
    if rehash:
        doc["payload_sha256"] = schema.payload_hash(doc["body"])
    with gzip.open(tmp / name, "wt") as f:
        json.dump(doc, f)


def test_sims_produced_fixtures_validate_only_when_fixtures_are_allowed():
    for kind in schema.KINDS:
        assert schema.validate(_doc(kind), NOW, allow_fixture=True) == []
        assert any("TEST FIXTURE" in p for p in schema.validate(_doc(kind), NOW))
    got = importer.load(str(FIX), NOW)
    assert got["status"] == "REJECTED" and len(got["rejected"]) == 3 and got["edges"] is None


@pytest.mark.parametrize("mutate,reason", [
    (lambda d: d["body"]["rows"][0].__setitem__("roi_pct", 99.0), "payload hash"),           # edited without rehash
    (lambda d: d.__setitem__("schema_version", 2), "schema version"),
    (lambda d: d.__setitem__("league", "CFB"), "only NFL"),
    (lambda d: d.__setitem__("exported_at", "2027-01-01T00:00:00+00:00"), "future"),
    (lambda d: d.__setitem__("format", "something-else"), "format"),
])
def test_header_and_hash_problems_reject_the_file(mutate, reason):
    d = _doc("edges")
    mutate(d)
    assert any(reason in p for p in schema.validate(d, NOW, allow_fixture=True))


@pytest.mark.parametrize("mutate,reason", [
    (lambda b: b["rows"][0].__setitem__("slate_game", "2026_09_Georgia_Florida"), "not an NFL game id"),
    (lambda b: b["rows"][0].__setitem__("p_win", 1.3), "probability outside 0-1"),
    (lambda b: b["rows"][0].__setitem__("price", 50), "impossible American price"),
    (lambda b: b["rows"][0].__setitem__("rank", 9), "rank"),
    (lambda b: b.__setitem__("top_row_ids", b["row_ids"][:6]), "Top 5 row ids"),
])
def test_content_problems_reject_the_file_even_with_a_correct_hash(mutate, reason):
    d = _doc("edges")
    mutate(d["body"])
    d["payload_sha256"] = schema.payload_hash(d["body"])
    assert any(reason in p for p in schema.validate(d, NOW, allow_fixture=True))


def test_projection_content_rules():
    d = _doc("projections")
    g = next(x for x in d["body"]["games"] if x["forecast_status"] == "official")
    g["players"][0]["play_prob"] = -0.1
    d["payload_sha256"] = schema.payload_hash(d["body"])
    assert any("probability outside 0-1" in p for p in schema.validate(d, NOW, allow_fixture=True))


def test_newest_valid_export_wins_and_a_newer_invalid_one_is_reported(tmp_path):
    for p in FIX.glob("*.json.gz"):
        shutil.copy(p, tmp_path)
    bad = _doc("edges")
    bad["exported_at"] = "2026-10-30T21:20:00+00:00"
    bad["body"]["rows"][0]["p_win"] = 2.0
    _write(tmp_path, "sims_nfl_edges_2026-11-08_revision1_20261030T212000Z.json.gz", bad)
    (tmp_path / "sims_nfl_edges_corrupt_20261030T212500Z.json.gz").write_bytes(b"not gzip")
    got = importer.load(str(tmp_path), NOW, allow_fixture=True)
    assert got["status"] == "OK" and got["edges"]["_file"].endswith("205713Z.json.gz")
    assert {r["file"] for r in got["rejected"]} == {"sims_nfl_edges_2026-11-08_revision1_20261030T212000Z.json.gz",
                                                     "sims_nfl_edges_corrupt_20261030T212500Z.json.gz"}


def test_missing_or_unreadable_exports_never_break_the_block(tmp_path):
    assert sims_section.block(str(tmp_path / "nope"), NOW)["status"] == "MISSING"
    assert sims_section.block(None, NOW)["status"] == "MISSING"
    (tmp_path / "sims_nfl_edges_x.json.gz").write_bytes(b"\x1f\x8b broken")
    b = sims_section.block(str(tmp_path), NOW.timestamp())
    assert b["status"] == "REJECTED" and b["edges"] is None and "nothing from them is shown" in b["detail"]


def test_block_passes_sims_values_through_unchanged():
    b = sims_section.block(str(FIX), NOW, allow_fixture=True)
    d = _doc("edges")["body"]
    by_id = dict(zip(d["row_ids"], d["rows"]))
    src = [by_id[i] for i in d["top_row_ids"]]
    assert b["fixture"] and len(b["edges"]["top"]) == len(src) == 5
    for got, want in zip(b["edges"]["top"], sorted(src, key=lambda r: r["rank"])):
        for k in ("player_name", "line", "price", "p_win", "p_cal", "roi_pct", "cal_roi_pct", "cons_roi_pct", "odds_at", "model_version"):
            assert got[k] == want[k]
    offers = sims_section.decode_offers(b["edges"]["offers"])
    assert len(offers) == len(d["rows"])
    for got, want in zip(offers, d["rows"]):              # the compact table decodes to the exported values
        for k in sims_section.TABLE_COLS:
            w = want.get(k)
            assert got[k] == (round(w, 4) if isinstance(w, float) else w), k
    assert sum(b["edges"]["excluded_reasons"].values()) >= sum(r["quality"] != "valid" for r in d["rows"])
    p = _doc("projections")["body"]
    assert [g["game_id"] for g in b["projections"]["games"]] == [g["game_id"] for g in p["games"]]


def test_accept_copies_new_valid_files_only_and_never_overwrites(tmp_path):
    new, have, ok = tmp_path / "new", tmp_path / "have", tmp_path / "ok"
    new.mkdir(), have.mkdir()
    real = _doc("history")
    real.pop("fixture")
    _write(new, "sims_nfl_history_2026_20261030T210000Z.json.gz", real)
    for p in FIX.glob("*.json.gz"):
        shutil.copy(p, new)                                          # fixtures: refused by the production import
    acc, rej = importer.accept(str(new), str(have), str(ok), NOW)
    assert acc == ["sims_nfl_history_2026_20261030T210000Z.json.gz"] and len(rej) == 3
    shutil.copy(ok / acc[0], have)
    assert importer.accept(str(new), str(have), str(tmp_path / "ok2"), NOW)[0] == []   # already imported: skipped


def _imports(path):
    tree = ast.parse(path.read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
        elif isinstance(n, ast.Import):
            mods.update(a.name.split(".")[0] for a in n.names)
    return mods


def test_sims_code_and_the_sportsbook_never_import_each_other():
    sims_files = list((ROOT / "sims_v5").glob("*.py")) + [ROOT / "web_v4" / "sims_section.py"]
    for f in sims_files:
        assert not (_imports(f) & {"sportsbook_v4", "collect_v4", "analysis_v4"}), f.name
    for f in (ROOT / "sportsbook_v4").glob("*.py"):
        assert "sims_v5" not in _imports(f) and "sims_section" not in f.read_text(), f.name


def test_fixture_age_rules_are_left_to_the_viewer_clock():
    b = sims_section.block(str(FIX), NOW, allow_fixture=True)
    assert b["odds_stale_min"] == 90 and all(r["odds_at"] and r["kickoff_utc"] for r in b["edges"]["top"])
    late = NOW + timedelta(days=30)
    assert schema.validate(_doc("edges"), late, allow_fixture=True) == []       # an old file is still valid; the page ages it


FAKE_GH = r"""#!/bin/bash
echo "gh $*" >> "$LOG"
[[ "$GH_TOKEN" == "$SECRET" ]] && echo "SIMS-TOKEN-CALL $*" >> "$LOG"
case "$1 $2" in
  "release download")
    pat="${@: -3:1}"; dir="${@: -1}"; mkdir -p "$dir"
    if [[ "$3" == "web-export" ]]; then [[ "$FAIL" == download ]] && exit 1; cp "$SRC/$pat" "$dir/"; exit 0; fi
    if [[ "$3" == "sims-import" ]]; then cp "$HAVE/$pat" "$dir/"; exit 0; fi
    exit 1 ;;
  "api repos/nmuskat/arbitredge-sims/releases/tags/web-export")
    [[ "$FAIL" == auth ]] && { echo "HTTP 401: Bad credentials" >&2; exit 1; }
    [[ "$FAIL" == hang ]] && sleep 30
    ls "$SRC"; exit 0 ;;
  "api repos/nmuskat/arbitredge-v4/releases/tags/sims-import") ls "$HAVE"; exit 0 ;;
  "release view") exit 1 ;;
  "release create") exit 0 ;;
  "release upload") shift 3; for f in "$@"; do echo "UPLOADED $(basename "$f")" >> "$LOG"; done; exit 0 ;;
esac
exit 0
"""


def _run_import(tmp_path, token, fail="", have=()):
    import os
    import subprocess
    import time
    src, bin_dir, hv = tmp_path / "src", tmp_path / "bin", tmp_path / "have"
    for d in (src, bin_dir, hv):
        d.mkdir()
    real = _doc("history")
    real.pop("fixture")
    _write(src, "sims_nfl_history_2026_20261030T210000Z.json.gz", real)
    for p in FIX.glob("*.json.gz"):
        shutil.copy(p, src)
    for n in have:
        shutil.copy(src / n, hv / n)
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", LOG=str(tmp_path / "log"), SRC=str(src), HAVE=str(hv),
               SECRET="tok-123-secret", GH_TOKEN="github-token", SIMS_TOKEN=token, PYTHONPATH=str(ROOT), FAIL=fail,
               GITHUB_REPOSITORY="nmuskat/arbitredge-v4", SIMS_NET_TIMEOUT_S="2")
    t = time.time()
    r = subprocess.run(["bash", str(ROOT / ".github" / "scripts" / "sims_import.sh")], cwd=tmp_path, env=env,
                       capture_output=True, text=True)
    log = (tmp_path / "log").read_text() if (tmp_path / "log").exists() else ""
    return r, log, time.time() - t


REAL = "sims_nfl_history_2026_20261030T210000Z.json.gz"


def test_import_script_without_token_uses_imported_files_only(tmp_path):
    r, log, _ = _run_import(tmp_path, "", have=(REAL,))
    assert r.returncode == 0 and "not configured" in r.stdout
    assert "arbitredge-sims" not in log and "UPLOADED" not in log
    assert (tmp_path / "sims" / REAL).exists()                                       # last good export still shown


def test_import_script_imports_only_valid_real_exports_and_never_prints_the_token(tmp_path):
    r, log, _ = _run_import(tmp_path, "tok-123-secret")
    assert r.returncode == 0
    up = [ln.split()[1] for ln in log.splitlines() if ln.startswith("UPLOADED")]
    assert up == [REAL]                                                              # fixtures refused
    assert "tok-123-secret" not in r.stdout + r.stderr
    sims_calls = [ln for ln in log.splitlines() if ln.startswith("SIMS-TOKEN-CALL")]
    assert sims_calls and all("arbitredge-sims" in ln or "web-export" in ln for ln in sims_calls)
    assert (tmp_path / "sims" / up[0]).exists()


def test_import_script_downloads_nothing_when_no_export_is_new(tmp_path):
    names = [REAL] + [p.name for p in FIX.glob("*.json.gz")]
    r, log, _ = _run_import(tmp_path, "tok-123-secret", have=names)
    assert r.returncode == 0 and "no new SIMS exports" in r.stdout
    assert not [ln for ln in log.splitlines() if "release download web-export" in ln] and "UPLOADED" not in log
    assert sum("release download sims-import" in ln for ln in log.splitlines()) <= 3  # newest of each kind only


@pytest.mark.parametrize("fail", ["auth", "download", "hang"])
def test_expired_token_failed_download_or_hung_sims_never_break_the_build(tmp_path, fail):
    r, log, took = _run_import(tmp_path, "tok-123-secret", fail=fail, have=(REAL,))
    assert r.returncode == 0 and "UPLOADED" not in log and took < 15
    assert (tmp_path / "sims" / REAL).exists()                                       # keeps the last valid export
    b = sims_section.block(str(tmp_path / "sims"), NOW)
    assert b["status"] == "OK" and b["history"] is not None
