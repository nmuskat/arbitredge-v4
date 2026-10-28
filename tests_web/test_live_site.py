"""web_v4.live_site: the automatically rebuilt page, from collected data only."""
import json
import os
import re
import shutil
import subprocess

import pytest

from tests_v4.test_live_book import NFL, NOW, T0, build_store, new_book, payload, poll
from market_v4 import normalize, store
from web_v4 import live_site as LS


@pytest.fixture(scope="module")
def snaps():
    live = build_store()
    no_rank = LS.build_snapshot(live, [], [], NOW, new_book(), bettors=1500)
    ranked = LS.build_snapshot(live, [], [poll(T0 - 86400)], NOW, new_book(), bettors=1500)
    return no_rank, ranked


def test_labels_and_defaults(snaps):
    s, _ = snaps
    assert s["schema"] == LS.SCHEMA and s["plays"] == [] and "NO BET" in s["plays_note"]
    assert s["splits_real"]["status"] == "UNAVAILABLE"
    assert s["sims"]["status"] == "NOT CONNECTED"
    assert s["freshness_policy"] == {"live_max_min": 45, "delayed_max_min": 360}
    assert s["sports"]["nfl"]["book"]["label"] == "MODELED"


def test_observed_time_is_last_collection_not_build_time(snaps):
    s, _ = snaps
    assert s["sports"]["nfl"]["observed_at"] == LS._iso(NOW)         # last fetch in the fixture is at NOW
    assert s["generated_at"] == LS._iso(NOW)


def test_page_built_as_of_an_earlier_time_never_uses_later_odds():
    live = build_store()
    s = LS.build_snapshot(live, [], [], NOW - 7 * 3600, None)
    obs = LS._epoch(s["sports"]["nfl"]["observed_at"])
    assert obs <= NOW - 7 * 3600 and obs == NOW - 9 * 3600            # fixture collects every 3 h


def test_cfb_without_rankings_is_unavailable_never_guessed(snaps):
    s, _ = snaps
    cfb = s["sports"]["cfb"]
    assert cfb["rankings"]["status"] == "UNAVAILABLE"
    assert cfb["book"]["status"] == "UNAVAILABLE"
    assert all(g["top25"] is None for g in cfb["games"])           # unknown, not False
    assert all(r.get("top25") is None for r in cfb["scanner"] + cfb["sharp"])


def test_cfb_with_rankings_tags_every_game(snaps):
    _, s = snaps
    cfb = s["sports"]["cfb"]
    assert cfb["rankings"]["status"] == "OK" and cfb["rankings"]["source"] == "CollegeFootballData API"
    tag = {g["home"]: g["top25"] for g in cfb["games"]}
    assert tag == {"Alabama Crimson Tide": True, "Ohio State Buckeyes": True, "Kansas Jayhawks": False,
                   "Texas Longhorns": True}
    assert {g["event_id"] for g in cfb["book"]["games"]} == {g["event_id"] for g in cfb["games"] if g["top25"]}


def test_board_has_every_book_quote(snaps):
    s, _ = snaps
    g = s["sports"]["nfl"]["games"][0]
    books = g["markets"]["spreads"]["books"]
    assert len(books) == 7 and all("home" in v and "away" in v and "age_s" in v for v in books.values())


def test_html_is_self_contained_and_round_trips(snaps):
    s, _ = snaps
    html = LS.render_html(s)
    assert not re.search(r"""(src|href)\s*=\s*["']https?://""", html)   # no external scripts, styles or images
    assert "<link" not in html and "@import" not in html and "fetch(" not in html
    data = re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S).group(1)
    assert "<" not in data and json.loads(data) == json.loads(json.dumps(s))
    for sec in ("overview", "odds", "splits", "need", "sharp", "ev", "sims", "performance", "health"):
        assert f'["{sec}",' in html


def test_provider_text_cannot_break_out_of_the_data_block():
    live = store.connect()
    evil = [("n1", "</script><script>alert(1)</script>", "Away <!-- x", -3.0, 44.5, (-150, 130))]
    q, an = normalize.normalize(payload(T0, evil, "americanfootball_nfl", 0), LS._iso(T0))
    store.ingest(live, q, an, LS._iso(T0), "americanfootball_nfl", "odds", "fp")
    html = LS.render_html(LS.build_snapshot(live, [], [], T0, None))
    assert html.count("</script>") == 2 and "<script>alert(1)" not in html and "<!-- x" not in html
    assert "textContent" in html and "innerHTML" not in html               # page renders provider text as text


def test_no_secret_reaches_the_page(snaps, monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "k" * 32)
    s, _ = snaps
    html = LS.render_html(s)
    assert "k" * 32 not in html and "apiKey" not in html and "ODDS_API_KEY" not in html


def test_cli_builds_from_shards(tmp_path):
    import gzip
    live = build_store(end=T0 - 86400)
    shard = tmp_path / "shards" / "odds-data-2026-W44"
    shard.mkdir(parents=True)
    with gzip.open(shard / "quotes_1.jsonl.gz", "wt") as fh:
        for t, cols in (("mkt_fetch", "fetched_at, sport, endpoint, request_fp, source, n_quotes, n_anomalies"),
                        ("mkt_quote", "event_id, sport, commence, home, away, book, market, side, point, price, "
                                      "market_last_update, fetched_at, source, request_fp")):
            names = [c.strip() for c in cols.split(",")]
            for row in live.execute(f"SELECT {cols} FROM {t}"):
                fh.write(json.dumps({"t": t, **dict(zip(names, row))}) + "\n")
    LS.main(["--shards", str(tmp_path / "shards"), "--out", str(tmp_path / "site"), "--bettors", "300",
             "--now", LS._iso(T0 - 86400 + 600)])
    snap = json.load(open(tmp_path / "site" / "site_snapshot.json"))
    assert snap["shards"] == 1 and len(snap["sports"]["nfl"]["games"]) == len(NFL)
    assert (tmp_path / "site" / "index.html").read_text().startswith("<!doctype html>")


def _playwright():
    if not shutil.which("node"):
        return False
    return subprocess.run(["node", "-e", "require('playwright')"], capture_output=True).returncode == 0


BROWSER_JS = r"""
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 390, height: 844 } });
  const errs = []; p.on('pageerror', e => errs.push(e.message));
  p.on('request', r => { if (!r.url().startsWith('file:')) errs.push('external ' + r.url()); });
  const now = Number(process.argv[3]);
  await p.addInitScript(n => { Date.now = () => n; }, now);
  await p.goto('file://' + process.argv[2]);
  const out = { header: await p.innerText('header') };
  for (const s of ['overview','odds','splits','need','sharp','ev','sims','performance','health']) {
    await p.evaluate(h => location.hash = h, s); await p.waitForTimeout(60); out[s] = await p.innerText('main'); }
  await p.evaluate(() => location.hash = 'odds'); await p.click('tr.game'); await p.waitForTimeout(150); out.detail = await p.innerText('main');
  await p.click('#sport button[data-s=cfb]'); await p.waitForTimeout(150); out.cfb = await p.innerText('main');
  await p.evaluate(() => location.hash = 'odds'); await p.waitForTimeout(150); out.cfbRows = await p.$$eval('tr.game', r => r.length);
  out.width = await p.evaluate(() => document.documentElement.scrollWidth); out.errs = errs;
  console.log(JSON.stringify(out)); await b.close();
})();
"""


@pytest.mark.skipif(not _playwright(), reason="node + playwright not available")
@pytest.mark.parametrize("hours_later,label", [(0.25, "LIVE"), (3, "DELAYED"), (9, "STALE")])
def test_page_in_browser_freshness_downgrades_itself(tmp_path, snaps, hours_later, label):
    s, _ = snaps
    (tmp_path / "index.html").write_text(LS.render_html(s))
    (tmp_path / "b.js").write_text(BROWSER_JS)
    now_ms = int((NOW + hours_later * 3600) * 1000)
    r = subprocess.run(["node", str(tmp_path / "b.js"), str(tmp_path / "index.html"), str(now_ms)],
                       capture_output=True, text=True, timeout=120)
    out = json.loads(r.stdout)
    assert out["errs"] == [] and out["width"] <= 390
    assert label in out["header"]
    if label == "STALE":
        assert "NOT current prices" in out["odds"]
    assert "Kansas City Chiefs" in out["odds"] and "Odds board" in out["odds"]
    assert "MODELED" in out["splits"] and "not DraftKings" in out["splits"]
    assert "Back" in out["detail"] and "pinnacle" in out["detail"]
    assert "UNAVAILABLE" in out["cfb"] and "nothing is guessed" in out["cfb"]
    assert out["cfbRows"] == 0                                   # no rankings: no CFB game shown as Top-25
