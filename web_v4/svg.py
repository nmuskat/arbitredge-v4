"""web_v4/svg.py -- tiny server-side SVG charts (no JS, accessible titles)."""

from html import escape


def margin_histogram(hist, listed_line=None, width=560, height=170, title="Projected final margin (home - away)"):
    """hist: [[margin, p], ...]. Key numbers +-3/7 emphasised; listed line marked."""
    if not hist:
        return ""
    pad_l, pad_b, pad_t = 8, 22, 10
    n = len(hist)
    bw = (width - 2 * pad_l) / n
    pmax = max(p for _, p in hist) or 1
    bars = []
    for i, (m, p) in enumerate(hist):
        h = (height - pad_b - pad_t) * p / pmax
        x = pad_l + i * bw
        side = "home" if m > 0 else ("tie" if m == 0 else "away")
        cls = f"bar {side}" + (" key" if abs(m) in (3, 7, 10) else "")
        bars.append(f'<rect class="{cls}" x="{x:.1f}" y="{height - pad_b - h:.1f}" width="{max(bw - 1, 1):.1f}" '
                    f'height="{h:.1f}"><title>margin {m:+d}: {p * 100:.1f}%</title></rect>')
    ticks = []
    for i, (m, _) in enumerate(hist):
        if m % 7 == 0 or m in (-3, 3):
            ticks.append(f'<text class="tick" x="{pad_l + (i + 0.5) * bw:.1f}" y="{height - 6}" text-anchor="middle">{m:+d}</text>')
    mark = ""
    if listed_line is not None:
        thr = -listed_line                      # home covers the listed line if margin > thr
        pos = {m: i for i, (m, _) in enumerate(hist)}
        import math
        k = math.floor(thr)
        if k in pos:
            # integer threshold: centre of the push bar; half-point: boundary after floor(thr)
            x = pad_l + (pos[k] + (0.5 if float(thr).is_integer() else 1.0)) * bw
            mark = (f'<line class="marker" x1="{x:.1f}" x2="{x:.1f}" y1="{pad_t}" y2="{height - pad_b}"/>'
                    f'<text class="marker-label" x="{x + 4:.1f}" y="{pad_t + 10}">listed home {listed_line:+g}</text>')
    return (f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">'
            f'<title>{escape(title)}</title>{"".join(bars)}{"".join(ticks)}{mark}</svg>')


def calibration(rows, width=320, height=240, title="Calibration: predicted vs observed"):
    pad = 30
    s = width - 2 * pad

    def xy(px, py):
        return pad + px * s, height - pad - py * (height - 2 * pad)
    pts = [r for r in rows if r["n"] >= 15]
    dots = "".join(
        f'<circle class="dot" cx="{xy(r["pred"], r["obs"])[0]:.1f}" cy="{xy(r["pred"], r["obs"])[1]:.1f}" '
        f'r="{3 + min(6, r["n"] / 150):.1f}"><title>{r["bin"]}: predicted {r["pred"]:.3f}, observed {r["obs"]:.3f} (n={r["n"]})</title></circle>'
        for r in pts)
    x0, y0 = xy(0, 0)
    x1, y1 = xy(1, 1)
    axes = (f'<line class="axis" x1="{x0}" y1="{y0}" x2="{x1}" y2="{y0}"/><line class="axis" x1="{x0}" y1="{y0}" x2="{x0}" y2="{y1}"/>'
            f'<line class="diag" x1="{x0}" y1="{y0}" x2="{x1}" y2="{y1}"/>'
            f'<text class="tick" x="{(x0 + x1) / 2}" y="{height - 6}" text-anchor="middle">predicted</text>'
            f'<text class="tick" x="10" y="{(y0 + y1) / 2}" transform="rotate(-90 10 {(y0 + y1) / 2})" text-anchor="middle">observed</text>')
    return (f'<svg class="chart square" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">'
            f'<title>{escape(title)}</title>{axes}{dots}</svg>')


def split_bar(left_pct, left_label, right_label):
    """Two-sided percentage bar (tickets or handle)."""
    if left_pct is None:
        return '<div class="split empty">no wagers</div>'
    lp = max(0.0, min(100.0, float(left_pct)))
    return (f'<div class="split" role="img" aria-label="{escape(left_label)} {lp:.0f}%, {escape(right_label)} {100 - lp:.0f}%">'
            f'<span class="l" style="width:{lp:.1f}%">{escape(left_label)} {lp:.0f}%</span>'
            f'<span class="r" style="width:{100 - lp:.1f}%">{escape(right_label)} {100 - lp:.0f}%</span></div>')


def bars_signed(items, width=560, height=150, title=""):
    """items: [(label, value)] signed bars around zero (e.g. log-loss difference by season)."""
    if not items:
        return ""
    vmax = max(abs(v) for _, v in items) or 1
    n = len(items)
    bw = width / n
    mid = height / 2
    out = []
    for i, (lab, v) in enumerate(items):
        h = (height / 2 - 18) * abs(v) / vmax
        y = mid - h if v > 0 else mid
        out.append(f'<rect class="bar {"neg" if v > 0 else "pos"}" x="{i * bw + 2:.1f}" y="{y:.1f}" width="{bw - 4:.1f}" '
                   f'height="{h:.1f}"><title>{escape(str(lab))}: {v:+.4f}</title></rect>'
                   f'<text class="tick" x="{(i + 0.5) * bw:.1f}" y="{height - 3}" text-anchor="middle">{escape(str(lab))[-2:]}</text>')
    return (f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}"><title>{escape(title)}</title>'
            f'<line class="axis" x1="0" x2="{width}" y1="{mid}" y2="{mid}"/>{"".join(out)}</svg>')
