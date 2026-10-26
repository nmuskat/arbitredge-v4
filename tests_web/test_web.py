"""Website: renders every page from the built snapshot; read-only; provenance everywhere."""
import copy
import json
import os
import re

import pytest

SNAP = "data/site/snapshot.json"
pytestmark = pytest.mark.skipif(not os.path.exists(SNAP), reason="site snapshot not built (python -m web_v4.snapshot)")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from web_v4.app import app
    return TestClient(app)


PAGES = ["/", "/need", "/sharp", "/sims", "/scanner", "/plays", "/performance", "/health"]


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders_with_provenance_labels(client, path):
    r = client.get(path)
    assert r.status_code == 200
    html = r.text
    labels = re.findall(r'class="b b-(\w+)"', html)
    assert labels, "every page must show at least one provenance badge"
    assert "ArbitrEdge" in html and 'name="viewport"' in html


def test_unknown_page_404_and_no_write_methods(client):
    assert client.get("/nope").status_code == 404
    for path in PAGES:
        assert client.post(path).status_code == 405
        assert client.delete(path).status_code == 405


def test_api_docs_not_exposed(client):
    for p in ("/docs", "/openapi.json", "/redoc"):
        assert client.get(p).status_code == 404


def test_demo_data_is_always_labelled(client):
    for path in ("/sharp", "/scanner", "/need"):
        assert "b-demo" in client.get(path).text


def test_no_official_plays_without_validation(client):
    html = client.get("/plays").text
    assert "No official plays" in html
    snap = json.load(open(SNAP))
    assert snap["plays"] == []
    assert all(r["decision"] != "BET" for r in snap["scanner"])     # nothing is validated yet
    assert "NOT VALIDATED" in client.get("/scanner").text


def test_need_page_says_need_is_not_a_prediction(client):
    html = client.get("/need").text
    assert "not</b> a prediction" in html


def test_static_export_matches_pages(tmp_path):
    from web_v4 import render
    out = render.export(SNAP, str(tmp_path))
    expected = sorted([f"{p}.html" for p, _ in render.PAGES] + ["site.css"])
    assert sorted(os.listdir(out)) == expected
    idx = open(os.path.join(out, "index.html")).read()
    assert 'href="site.css"' in idx and 'href="need.html"' in idx


def test_template_escapes_untrusted_text(tmp_path):
    """Team/book strings come from external data: they must be HTML-escaped."""
    from web_v4 import render
    snap = copy.deepcopy(json.load(open(SNAP)))
    snap["games"][0]["home_name"] = "<script>alert(1)</script>"
    snap["scanner"] = [dict(snap["scanner"][0], book="<img src=x onerror=alert(1)>", decision="WATCH")]
    p = tmp_path / "s.json"
    p.write_text(json.dumps(snap))
    e = render.env()
    s2 = render.load_snapshot(str(p))
    for page in ("need", "scanner"):
        html = render.render_page(e, page, s2)
        assert "<script>alert(1)</script>" not in html and "<img src=x" not in html
