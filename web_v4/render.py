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
