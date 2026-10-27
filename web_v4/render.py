"""web_v4/render.py -- Jinja rendering shared by the live app and the static export."""

import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from web_v4 import svg

HERE = os.path.dirname(os.path.abspath(__file__))
ET = ZoneInfo("America/New_York")
PAGES = [("index", "Dashboard"), ("need", "ArbitrEdge Need"), ("sharp", "Sharp Money"), ("sims", "SIMS"),
         ("scanner", "EV Scanner"), ("plays", "Official Plays"), ("performance", "Performance"), ("health", "System Health")]


def odds(a):
    if a is None:
        return "—"
    a = int(a)
    return f"+{a}" if a > 0 else str(a)


def line(x, signed=True):
    if x is None:
        return "—"
    x = float(x)
    if x == 0 and signed:
        return "PK"
    s = f"{x:+.1f}" if signed else f"{x:.1f}"
    return s.replace(".0", "") if s.endswith(".0") else s


def pct(x, d=1):
    return "—" if x is None else f"{100 * float(x):.{d}f}%"


def money(x):
    if x is None:
        return "—"
    x = float(x)
    return f"{'-' if x < 0 else ''}${abs(x):,.0f}"


def et(ts, fmt="%a %b %-d · %-I:%M %p ET"):
    if ts is None:
        return "—"
    if isinstance(ts, (int, float)):
        d = datetime.fromtimestamp(ts, timezone.utc)
    else:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return d.astimezone(ET).strftime(fmt)


def signed(x, d=2):
    return "—" if x is None else f"{float(x):+.{d}f}"


def env():
    e = Environment(loader=FileSystemLoader(os.path.join(HERE, "templates")),
                    autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
    e.filters.update(odds=odds, line=line, pct=pct, money=money, et=et, signed=signed)
    e.globals.update(svg=svg, PAGES=PAGES)
    return e


def load_snapshot(path="data/site/snapshot.json"):
    with open(path) as fh:
        return json.load(fh)


def render_page(e, page, snap, link=lambda p: "/" if p == "index" else f"/{p}"):
    tpl = e.get_template(f"{page}.html")
    return tpl.render(s=snap, page=page, link=link, games={g["game_id"]: g for g in snap["games"]})


def export(snapshot_path="data/site/snapshot.json", out_dir="data/site/export"):
    """Static copy of every page (relative links) -- for review without a server."""
    snap = load_snapshot(snapshot_path)
    e = env()
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(HERE, "static", "site.css")) as fh:
        css = fh.read()
    with open(os.path.join(out_dir, "site.css"), "w") as fh:
        fh.write(css)
    for page, _ in PAGES:
        html = render_page(e, page, snap, link=lambda p: f"{p}.html")
        html = html.replace('href="/static/site.css"', 'href="site.css"')
        with open(os.path.join(out_dir, f"{page}.html"), "w") as fh:
            fh.write(html)
    return out_dir


SINGLE_PAGE_CSS = """
/* single-page staging export: phone safe areas, side gutters, one dark world */
:root { color-scheme: dark; }
html, body { background: var(--bg); }
body::before { content: ""; position: fixed; inset: 0 0 auto; height: env(safe-area-inset-top, 0px); z-index: 20;
               background: rgba(6, 11, 22, .97); }
.top { top: env(safe-area-inset-top, 0px); }
.top-in, nav.tabs, main { padding-left: max(16px, env(safe-area-inset-left, 0px));
                          padding-right: max(16px, env(safe-area-inset-right, 0px)); }
.top-in { padding-block: 10px; }
nav.tabs { padding-block: 0; }
main { padding-block: 18px 48px; }
.staging { font: 700 10px/1 var(--mono); letter-spacing: .08em; color: var(--orange-2); border: 1px solid rgba(255,122,26,.5);
           border-radius: 4px; padding: 4px 6px; white-space: nowrap; }
nav.tabs a:focus-visible, a:focus-visible { outline: 2px solid var(--orange); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
"""

ROUTER_JS = """
(function () {
  var views = document.querySelectorAll('section.view');
  var tabs = document.querySelectorAll('nav.tabs a');
  function show() {
    var id = (location.hash || '#dashboard').slice(1);
    if (!document.getElementById('v-' + id)) id = 'dashboard';
    views.forEach(function (v) { v.hidden = v.id !== 'v-' + id; });
    tabs.forEach(function (a) { a.classList.toggle('on', a.getAttribute('href') === '#' + id); });
    window.scrollTo(0, 0);
  }
  window.addEventListener('hashchange', show);
  show();
})();
"""


def export_single(snapshot_path, out_path, title="ArbitrEdge V4 Staging"):
    """All 8 sections in ONE self-contained HTML document (no doctype/head: the host wraps it),
    navigated by #hash, for a private hosted preview on a phone."""
    snap = load_snapshot(snapshot_path)
    e = env()
    with open(os.path.join(HERE, "static", "site.css")) as fh:
        css = fh.read()
    names = {"index": "dashboard"}
    link = lambda p: "#" + names.get(p, p)
    sections = []
    for page, label in PAGES:
        body = e.get_template(f"{page}.html").render(s=snap, page=page, link=link, base_tpl="_section.html",
                                                     games={g["game_id"]: g for g in snap["games"]})
        vid = names.get(page, page)
        sections.append(f'<section class="view" id="v-{vid}" aria-label="{label}"{"" if vid == "dashboard" else " hidden"}>'
                        f"{body}</section>")
    tabs = "".join(f'<a href="{link(p)}" class="{"on" if p == "index" else ""}">{label}</a>' for p, label in PAGES)
    shell = e.from_string(SINGLE_PAGE_SHELL)
    html = shell.render(s=snap, title=title, css=css + SINGLE_PAGE_CSS, tabs=tabs, sections="".join(sections),
                        router=ROUTER_JS)
    with open(out_path, "w") as fh:
        fh.write(html)
    return out_path


SINGLE_PAGE_SHELL = """{% import "_macros.html" as mx %}<title>{{ title }}</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600;700&display=swap">
<style>{{ css | safe }}</style>
<header class="top">
  <div class="top-in">
    <div class="logo"><span class="a">Arbitr</span><span class="e">Edge</span><sup>V4</sup></div>
    <span class="staging">STAGING SNAPSHOT</span>
    <div class="asof">as of <b>{{ s.as_of | et }}</b></div>
  </div>
  <nav class="tabs" aria-label="Sections">{{ tabs | safe }}</nav>
</header>
<main>
{{ sections | safe }}
<footer>
  <div class="legend">
    {{ mx.badge("observed") }} real data &nbsp; {{ mx.badge("model") }} Engine C &nbsp; {{ mx.badge("synthetic") }} simulated customers
    &nbsp; {{ mx.badge("experimental") }} uncalibrated &nbsp; {{ mx.badge("demo") }} simulated quotes &nbsp; {{ mx.badge("validated") }} evidence gate passed
  </div>
  <p class="note">ArbitrEdge V4 staging · separate from the V3 website · research and paper tracking only, no wager is recommended.
  This page is a snapshot built {{ s.generated_at | et }}; it changes when a new snapshot is published.</p>
</footer>
</main>
<script>{{ router | safe }}</script>
"""
