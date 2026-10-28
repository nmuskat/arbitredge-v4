"""
sportsbook_v4/persist.py -- the synthetic sportsbook ledger as immutable files.

Every advance() of a run becomes ONE file, written once and never modified
(published as release assets of `v4-book`):

    book_<sport>_<week YYYYMMDD>_<run_id[:12]>_<seq 00000>.json.gz
    {"format", "population": {...header...}, "run": {v4_run row}, "interval": {v4_interval row},
     "wagers": [v4_wager rows in insertion order], "rows_sha256": hash of everything above}

load_dir() rebuilds a book database from those files and refuses (LedgerIntegrityError)
on any gap, reordering, altered row, or interval whose wagers no longer hash to the
recorded wagers_hash. verify_replay() then re-simulates the run from scratch and
requires the identical wager set -- proof that the stored ledger was neither altered
nor produced by different engine code or data.
"""

import glob
import gzip
import json
import os

from sportsbook_v4.population import ensure_population
from sportsbook_v4.util import json_hash

FORMAT = "v4-book-1"


class LedgerIntegrityError(RuntimeError):
    pass


def _rows(conn, sql, args):
    cur = conn.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _engine_wagers_hash(wagers):
    # the exact formula advance() records in v4_interval.wagers_hash
    return json_hash([[w["wager_id"], w["market"], w["side"], w["accepted_line"], w["accepted_price"],
                       w["stake"], w["accepted_at"]] for w in wagers])


def _body_hash(d):
    return json_hash({k: d[k] for k in ("format", "population", "run", "interval", "wagers")})


def file_name(run, seq):
    week = run["window_start"][:10].replace("-", "")
    return f"book_{run['sport']}_{week}_{run['run_id'][:12]}_{seq:05d}.json.gz"


def export_interval(conn, run_id, seq, out_dir):
    """Write one advance (run_id, seq) to out_dir. Returns the path."""
    run = _rows(conn, "SELECT * FROM v4_run WHERE run_id=?", (run_id,))[0]
    interval = _rows(conn, "SELECT * FROM v4_interval WHERE run_id=? AND seq=?", (run_id, seq))[0]
    wagers = _rows(conn, "SELECT * FROM v4_wager WHERE run_id=? AND interval_seq=? ORDER BY rowid", (run_id, seq))
    pop = _rows(conn, "SELECT population_id, sport, params_version, params_hash, bettor_count, team_universe, "
                      "population_hash FROM v4_population WHERE population_id=?", (run["population_id"],))[0]
    d = {"format": FORMAT, "population": pop, "run": run, "interval": interval, "wagers": wagers}
    d["rows_sha256"] = _body_hash(d)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, file_name(run, seq))
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as fh:
        fh.write(json.dumps(d, sort_keys=True, separators=(",", ":")).encode())
    return path


def _insert(conn, table, row):
    cols = list(row)
    conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 [row[c] for c in cols])


def load_dir(conn, book_dir):
    """Load every book_*.json.gz in book_dir (recursively). Returns {run_id: summary}."""
    files = sorted(glob.glob(os.path.join(book_dir, "**", "book_*.json.gz"), recursive=True)) if book_dir else []
    docs = []
    for p in files:
        try:
            with gzip.open(p, "rt") as fh:
                d = json.load(fh)
        except (OSError, ValueError) as e:
            raise LedgerIntegrityError(f"{os.path.basename(p)}: unreadable ({e})")
        if d.get("format") != FORMAT or d.get("rows_sha256") != _body_hash(d):
            raise LedgerIntegrityError(f"{os.path.basename(p)}: content does not match its recorded hash")
        if os.path.basename(p) != file_name(d["run"], d["interval"]["seq"]):
            raise LedgerIntegrityError(f"{os.path.basename(p)}: file name does not match its content")
        docs.append(d)
    docs.sort(key=lambda d: (d["run"]["run_id"], d["interval"]["seq"]))
    out = {}
    for d in docs:
        run, iv, wagers, pop = d["run"], d["interval"], d["wagers"], d["population"]
        rid = run["run_id"]
        s = out.get(rid)
        if s is None:
            if iv["seq"] != 0:
                raise LedgerIntegrityError(f"run {rid[:12]}: first stored interval is seq {iv['seq']}, not 0")
            if iv["interval_start"] != run["window_start"]:
                raise LedgerIntegrityError(f"run {rid[:12]}: ledger does not start at the window start")
            pid = ensure_population(conn, pop["sport"], json.loads(pop["team_universe"]), pop["bettor_count"])
            got = conn.execute("SELECT population_hash FROM v4_population WHERE population_id=?", (pid,)).fetchone()
            if pid != run["population_id"] or got[0] != pop["population_hash"]:
                raise LedgerIntegrityError(f"run {rid[:12]}: bettor population does not regenerate identically")
            _insert(conn, "v4_run", run)
            s = out[rid] = {"run": run, "files": 0, "wagers": 0, "last_seq": -1, "watermark": run["window_start"]}
        else:
            keep = {k: v for k, v in run.items() if k != "created_at"}
            if keep != {k: v for k, v in s["run"].items() if k != "created_at"}:
                raise LedgerIntegrityError(f"run {rid[:12]}: run definition differs between files")
        if iv["seq"] != s["last_seq"] + 1 or iv["interval_start"] != s["watermark"]:
            raise LedgerIntegrityError(f"run {rid[:12]}: interval seq {iv['seq']} does not continue the ledger "
                                       f"(expected seq {s['last_seq'] + 1} starting {s['watermark']})")
        if (len(wagers) != iv["accepted"] or _engine_wagers_hash(wagers) != iv["wagers_hash"]
                or any(w["run_id"] != rid or w["interval_seq"] != iv["seq"] for w in wagers)):
            raise LedgerIntegrityError(f"run {rid[:12]} interval {iv['seq']}: wagers do not match the interval record")
        for w in wagers:
            _insert(conn, "v4_wager", w)
        _insert(conn, "v4_interval", iv)
        s.update(files=s["files"] + 1, wagers=s["wagers"] + len(wagers), last_seq=iv["seq"],
                 watermark=iv["interval_end"])
    conn.commit()
    return out


KEY = ("wager_id", "bettor_index", "canonical_game_id", "market", "side", "accepted_line", "accepted_price",
       "stake", "accepted_at", "offer_as_of")


def wager_set(conn, run_id):
    return {tuple(r) for r in conn.execute(f"SELECT {', '.join(KEY)} FROM v4_wager WHERE run_id=?", (run_id,))}
