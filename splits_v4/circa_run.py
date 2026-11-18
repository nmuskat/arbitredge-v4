"""
splits_v4/circa_run.py -- process the owner's uploaded Circa splits sheet images (folder circa_uploads/, run by
.github/workflows/circa.yml; no secrets, no API calls).

  python -m splits_v4.circa_run --uploads circa_uploads --existing names.txt --out circa_new

For every image without a record yet: read it (circa_ocr), match it to ESPN's games and DraftKings lines at upload
time (espn), apply the 99% rule (circa_engine), and write circa_new/circa_<image name>.json -- what was read, every problem, every signal. The upload
time is the time the image was committed (git), so a sheet uploaded after kickoff is flagged LIVE. Written once.
"""
import argparse
import json
import os
import re
import subprocess
import time

from splits_v4 import circa_engine as E
from splits_v4 import circa_ocr as C

IMG = re.compile(r"\.(png|jpe?g|webp)$", re.I)


def record_name(image):
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.path.basename(image))
    return f"circa_{stem}.json"


def uploaded_at(path):
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct", "--", path], capture_output=True, text=True,
                             timeout=30).stdout.strip()
        return float(out) if out else time.time()
    except (OSError, ValueError, subprocess.SubprocessError):
        return time.time()


def events_for(games, at, get=None):
    """ESPN games (all leagues) for every date the sheet covers (header date if within 3 days, else upload day)."""
    from splits_v4 import espn
    days = sorted({E.game_day(g.get("sport_label"), at).strftime("%Y%m%d") for g in games} or
                  {E.game_day("", at).strftime("%Y%m%d")})
    evs, errs = [], {}
    for d in days:
        got, err = espn.games(d, get=get) if get else espn.games(d)
        evs += got
        errs.update({f"{k} {d}": v for k, v in err.items()})
    return evs, errs, days


def process(image, at, get=None, extra_rows=None):
    """extra_rows: rows of the other screenshots uploaded together, so one game is never claimed by two rows."""
    games = C.read(image)
    evs, errs, days = events_for(games + list(extra_rows or []), at, get)
    sigs = E.signals(games, evs, at, others=extra_rows)
    read = [{"label": g.get("sport_label"), "away": g.get("away"), "home": g.get("home"), "status": g["status"],
             "problems": g["problems"],
             "values": {mk: g.get(mk) for mk in ("spread", "total", "money")}} for g in games]
    return {"version": "circa_record.v2", "rule": E.RULE, "image": os.path.basename(image), "uploaded_at": at,
            "espn_dates": days, "espn_games": len(evs), "espn_errors": errs,
            "uploaded_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at)),
            "rows_read": len(games), "games_ok": sum(g["status"] == "OK" for g in games), "read": read, "signals": sigs}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--uploads", required=True)
    ap.add_argument("--existing", default=None)
    ap.add_argument("--failed", default=None, help="names of saved records that hold an error (retried)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    have = set(open(a.existing).read().split()) if a.existing and os.path.exists(a.existing) else set()
    failed = set(open(a.failed).read().split()) if a.failed and os.path.exists(a.failed) else set()
    # an image is done once it has a record without an error; a failed image is retried under a new name (records
    # are never overwritten), still dated by its original upload time
    todo = [os.path.join(a.uploads, f) for f in sorted(os.listdir(a.uploads)) if IMG.search(f)
            and (record_name(f) not in have or record_name(f) in failed)
            and not any(n.startswith(record_name(f)[:-5] + "__retry") and n not in failed for n in have)] \
        if os.path.isdir(a.uploads) else []
    taken = False
    if todo:
        os.makedirs(a.out, exist_ok=True)
        rows = {}
        for img in todo:                        # read every screenshot first: matching is done across all of them
            try:
                rows[img] = C.read(img)
            except Exception:                   # noqa: BLE001 -- reported below when that image is processed
                rows[img] = []
        for img in todo:
            try:
                others = [r for o, rs in rows.items() if o != img for r in rs]
                doc = process(img, uploaded_at(img), extra_rows=others)
            except Exception as e:                              # noqa: BLE001 -- an unreadable image is reported
                doc = {"version": "circa_record.v1", "rule": E.RULE, "image": os.path.basename(img),
                       "uploaded_at": uploaded_at(img), "error": f"{type(e).__name__}: {e}"[:300], "read": [], "signals": []}
            doc["uploaded_at_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(doc["uploaded_at"]))
            name = record_name(img)
            n = 1
            while name in have:                                   # never overwrite a saved record
                name = f"{record_name(img)[:-5]}__retry{n}.json"
                n += 1
            with open(os.path.join(a.out, name), "w") as fh:
                json.dump(doc, fh, separators=(",", ":"), sort_keys=True, default=str)
            n = sum(s["status"] == "SIGNAL" for s in doc["signals"])
            print(f"{os.path.basename(img)}: {doc.get('rows_read', 0)} games read, {doc.get('games_ok', 0)} fully readable, "
                  f"{n} signal(s){' -- ERROR ' + doc['error'] if doc.get('error') else ''}")
            taken = True
    else:
        print("no new images")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write(f"taken={'true' if taken else 'false'}\n")


if __name__ == "__main__":
    main()
