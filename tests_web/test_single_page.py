"""Single-page staging export: self-contained, hash-routed, labelled, nothing private embedded."""
import json
import os
import re

import pytest

from web_v4 import render

SNAP = os.environ.get("V4_TEST_SNAPSHOT", "data/site/snapshot.json")
pytestmark = pytest.mark.skipif(not os.path.exists(SNAP), reason="site snapshot not built")


@pytest.fixture(scope="module")
def html(tmp_path_factory):
    out = tmp_path_factory.mktemp("sp") / "page.html"
    render.export_single(SNAP, str(out))
    return open(out).read()


def test_all_sections_present_and_only_dashboard_visible_at_rest(html):
    ids = re.findall(r'<section class="view" id="v-([a-z]+)"[^>]*>', html)
    assert ids == ["dashboard", "need", "sharp", "sims", "scanner", "plays", "performance", "health"]
    assert re.search(r'id="v-dashboard" aria-label="[^"]+">', html)          # not hidden
    assert len(re.findall(r'id="v-[a-z]+" aria-label="[^"]+" hidden>', html)) == 7


def test_tabs_route_by_hash_and_avoid_reserved_names(html):
    hrefs = re.findall(r'<nav class="tabs"[^>]*>(.*?)</nav>', html, re.S)[0]
    names = re.findall(r'href="#([a-z]+)"', hrefs)
    assert names == ["dashboard", "need", "sharp", "sims", "scanner", "plays", "performance", "health"]
    assert not {"settings", "sidebar", "customize", "upgrade", "downloads"} & set(names)


def test_self_contained_no_external_scripts_or_document_skeleton(html):
    assert not re.search(r"<script[^>]+src=", html)
    assert not re.search(r"<!doctype|<html|<head>|<body", html, re.I)
    hosts = set(re.findall(r"https?://([a-z0-9.-]+)", html))
    assert hosts <= {"fonts.googleapis.com"}
    assert html.lstrip().startswith("<title>") or "<title>" in html[:8000]
    assert 'name="claude:viewport-fit-sides"' in html and "max(16px, env(safe-area-inset-left" in html


def test_nothing_private_embedded(html):
    for needle in ("/home/", "/tmp/", "apiKey", "ODDS_API_KEY", "143.244.160.20", "ledger.jsonl", "@yahoo"):
        assert needle not in html


def test_provenance_labels_present(html):
    assert "STAGING SNAPSHOT" in html and ("b-observed" in html or "b-demo" in html)
    assert "NOT VALIDATED" in html and "No official plays" in html
