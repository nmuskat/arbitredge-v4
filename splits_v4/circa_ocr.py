"""
splits_v4/circa_ocr.py -- read the Circa Sports splits sheet image (as posted by @BettingSplits on X, uploaded by the
owner) into numbers. Free, local text recognition (Tesseract); no paid service, no AI model.

The sheet is a fixed grid: per sport a dark header row ("CFB - TUESDAY, NOV 17 | SPREAD HANDLE BETS | TOTAL HANDLE
BETS | MONEY HANDLE BETS"), then two rows per game (away team first, home team second), every number in its own
small box. Each box is read on its own (digits, '.', '%', '+', '-', ',' only).

Checks (a game that fails any of them is reported as UNREADABLE and never guessed):
  * both teams' handle % and bets % for a market must add to 100 (+-1 for rounding);
  * spreads must be opposite (+x / -x); totals equal on both rows;
  * every value must parse.
"""
import os
import re
import subprocess
import tempfile

try:                                    # Pillow is only needed to read images (installed by circa.yml)
    from PIL import Image, ImageOps
except ImportError:                     # pragma: no cover
    Image = ImageOps = None

NUM_CHARS = "0123456789.%+-,"
# column order after the team name (left to right)
COLS = ("spread", "spread_handle", "spread_bets", "total", "total_handle", "total_bets",
        "money", "money_handle", "money_bets")


def _ocr(img, psm=7, whitelist=None):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "c.png")
        img.save(p)
        cmd = ["tesseract", p, "stdout", "--psm", str(psm), "--oem", "1"]
        if whitelist:
            cmd += ["-c", f"tessedit_char_whitelist={whitelist}"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout.strip()


def _lum(p):
    return (p[0] + p[1] + p[2]) / 3


def _runs(flags, min_len):
    out, start = [], None
    for i, f in enumerate(flags + [False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    return out


def _prep(cell, scale=6):
    """Dark text on any light background (grey or green box) -> black on white: enlarge, smooth, then threshold."""
    from PIL import ImageFilter
    g = cell.convert("L").resize((cell.width * scale, cell.height * scale), Image.BICUBIC)
    g = g.filter(ImageFilter.GaussianBlur(1.2)).point(lambda v: 0 if v < 150 else 255)
    return ImageOps.expand(g, border=30, fill=255)


def layout(im):
    """Find the table, the header rows (sport name) and the team rows, and the column centres."""
    W, H = im.size
    px = im.load()
    # dark header rows: very dark across most of the table width
    dark = [sum(_lum(px[x, y]) < 120 for x in range(0, W, 8)) > (W // 8) * 0.55 for y in range(H)]
    headers = [(a, b) for a, b in _runs(dark, 15) if b - a <= 70]     # header bars, not the black page margins
    if not headers:
        raise ValueError("no sport header row found")
    # column centres from the first header row: the bright header words (SPREAD, HANDLE, ...) sit on dark blue
    y0, y1 = headers[0]
    top = y0
    while top > 0 and sum(_lum(px[x, top - 1]) < 120 for x in range(0, W, 8)) > (W // 8) * 0.3:
        top -= 1                                                      # true top of the bar (text rows are less dark)
    band = range(top + 3, y1 - 3, 2)
    bright = [15 < x < W - 25 and any(_lum(px[x, y]) > 170 for y in band) for x in range(W)]
    words = _runs(bright, 3)
    # merge letters into words (gaps under 12 px)
    merged = []
    for a, b in words:
        if merged and a - merged[-1][1] < 12:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    label_end = merged[0][1] if merged else W // 4       # "CFB - TUESDAY, NOV 17" is the first block of words
    centres = [(a + b) // 2 for a, b in merged if a > W * 0.25]
    if len(centres) < 9:
        raise ValueError(f"expected 9 column headings, found {len(centres)}")
    centres = centres[-9:]
    # team rows: grey/green value boxes in the spread column, outside header rows
    xs = centres[0]
    box = [(not dark[y]) and 200 <= _lum(px[xs - 18, y]) <= 245 for y in range(H)]
    rows = [r for r in _runs(box, 12)]
    return {"headers": headers, "centres": centres, "rows": rows, "name_x": (int(W * 0.04), int(min(centres[0] - 45, W * 0.29)))}


def read(path):
    """[{"sport", "date_label", "away", "home", "spread": {"away": {"line","handle","bets"}, "home": {...}},
         "total": {...}, "money": {...}, "status"}]"""
    im = Image.open(path).convert("RGB")
    L = layout(im)
    W = im.size[0]
    half = max(18, int((L["centres"][1] - L["centres"][0]) * 0.42))
    # header labels
    hdr = []
    for a, b in L["headers"]:
        crop = im.crop((0, a, L["centres"][0] - half, b))
        txt = _ocr(_prep(ImageOps.invert(crop), 3), psm=7)      # white text on dark blue -> dark on light
        hdr.append((a, b, txt))
    team_rows = []
    for a, b in L["rows"]:
        vals = {}
        for col, cx in zip(COLS, L["centres"]):
            cell = im.crop((cx - half + 7, a + 4, cx + half - 7, b - 4))   # inside the box, borders excluded
            vals[col] = _ocr(_prep(cell), psm=7, whitelist=NUM_CHARS)
        name = _ocr(_prep(im.crop((L["name_x"][0], a, L["name_x"][1], b)), 3), psm=7)
        sport = next((t for (ha, hb, t) in reversed(hdr) if hb <= a), "")
        team_rows.append({"y": a, "sport_label": sport, "name": _clean_name(name), "raw": vals})
    games, i = [], 0
    while i + 1 < len(team_rows):
        r1, r2 = team_rows[i], team_rows[i + 1]
        if r1["sport_label"] != r2["sport_label"]:
            i += 1
            continue
        games.append(_pair(r1, r2))
        i += 2
    return games


def _clean_name(s):
    s = re.sub(r"^[^A-Za-z(]*", "", s)                       # icons/logos misread as symbols
    s = re.sub(r"\(\d+\)\s*", "", s)                          # (8) Houston -> Houston (AP rank prefix)
    s = re.sub(r"^[A-Z]{1,2}\s+(?=[A-Z][a-z])", "", s)       # stray letters from logos
    return s.strip(" .,'\"‘’“”")


def _num(s, pct=False):
    s = s.replace(",", "").replace(" ", "")
    if pct:
        m = re.fullmatch(r"(\d{1,3})%?", s)
        return float(m.group(1)) if m and int(m.group(1)) <= 100 else None
    m = re.fullmatch(r"([+-]?\d+(?:\.\d)?)", s)
    return float(m.group(1)) if m else None


def _pct_candidates(raw):
    """Plausible readings of one percentage box: the digits as read, without a leading '1' (box border read as 1),
    without one trailing character (stray dot read as a digit)."""
    d = re.sub(r"[^0-9]", "", raw.split("%")[0] if "%" in raw else raw)
    out = set()
    for c in (d, d[1:] if d.startswith("1") else None, d[:-1] if len(d) > 1 else None):
        if c and int(c) <= 100:
            out.add(int(c))
    return out


def _pct_pair(ra, rb):
    """(away %, home %) only when EXACTLY ONE combination of plausible readings adds to exactly 100; else (None, None)."""
    fits = {(x, y) for x in _pct_candidates(ra) for y in _pct_candidates(rb) if x + y == 100}
    return (float(next(iter(fits))[0]), float(next(iter(fits))[1])) if len(fits) == 1 else (None, None)


def _pair(r1, r2):
    g = {"sport_label": r1["sport_label"], "away": r1["name"], "home": r2["name"], "problems": []}
    lab = r1["sport_label"].upper()
    g["sport"] = "cfb" if lab.startswith("CFB") else "nfl" if lab.startswith("NFL") else lab.split(" ")[0].lower() or None
    for mk in ("spread", "total", "money"):
        a, h = r1["raw"], r2["raw"]
        m = {"away": {"line": _num(a[mk]), "handle": _num(a[mk + "_handle"], True), "bets": _num(a[mk + "_bets"], True)},
             "home": {"line": _num(h[mk]), "handle": _num(h[mk + "_handle"], True), "bets": _num(h[mk + "_bets"], True)}}
        for k in ("handle", "bets"):
            x, y = _pct_pair(a[mk + "_" + k], h[mk + "_" + k])
            m["away"][k], m["home"][k] = x, y
            if x is None:
                g["problems"].append(f"{mk} {k}: unreadable or not adding to 100% ({a[mk + '_' + k]!r} / {h[mk + '_' + k]!r})")
        la, lh = m["away"]["line"], m["home"]["line"]
        if la is None or lh is None:
            g["problems"].append(f"{mk} line unreadable ({a[mk]!r} / {h[mk]!r})")
        elif mk == "spread" and abs(la + lh) > 0.01:
            g["problems"].append(f"spread {la:+g} / {lh:+g} not opposite")
        elif mk == "total" and la != lh:
            g["problems"].append(f"total {la:g} / {lh:g} differ")
        g[mk] = m
    if "total" in g:
        g["total"] = {"over": g["total"]["away"], "under": g["total"]["home"]}
    g["status"] = "OK" if not g["problems"] else "UNREADABLE"
    return g
