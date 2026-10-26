"""
edge_v4/ledger.py -- strategy registry and paper-betting ledger (append-only).

* v4e_strategy: pre-registration. A strategy (id, version, description,
  parameters) is registered BEFORE it is evaluated; the registry count is
  the multiple-testing denominator.
* v4e_decision: every evaluated candidate (BET / WATCH / NO BET) with its
  inputs and reasons -- the audit trail.
* v4e_bet: paper bets (stake > 0) and their mode ('backtest' | 'forward').
* v4e_grade: one settlement row per bet, written once.
Nothing is ever updated or deleted (triggers).
"""

import hashlib
import json
import sqlite3

DDL = """
CREATE TABLE IF NOT EXISTS v4e_strategy (
  strategy_id TEXT NOT NULL, version TEXT NOT NULL, description TEXT NOT NULL,
  params_json TEXT NOT NULL, registered_at TEXT NOT NULL, validated INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (strategy_id, version));
CREATE TABLE IF NOT EXISTS v4e_decision (
  decision_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, version TEXT NOT NULL, mode TEXT NOT NULL,
  game_id TEXT NOT NULL, market TEXT NOT NULL, selection TEXT NOT NULL, book TEXT, line REAL, price INTEGER,
  decided_at TEXT NOT NULL, kickoff TEXT NOT NULL, decision TEXT NOT NULL CHECK (decision IN ('BET','WATCH','NO BET')),
  payload_json TEXT NOT NULL, CHECK (decided_at < kickoff),
  FOREIGN KEY (strategy_id, version) REFERENCES v4e_strategy(strategy_id, version));
CREATE TABLE IF NOT EXISTS v4e_bet (
  bet_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL UNIQUE REFERENCES v4e_decision(decision_id),
  strategy_id TEXT NOT NULL, version TEXT NOT NULL, mode TEXT NOT NULL CHECK (mode IN ('backtest','forward')),
  game_id TEXT NOT NULL, market TEXT NOT NULL, side TEXT NOT NULL, line REAL, price INTEGER NOT NULL,
  stake REAL NOT NULL CHECK (stake > 0), placed_at TEXT NOT NULL, kickoff TEXT NOT NULL,
  p_model REAL, p_push REAL, ev REAL, close_fair_prob REAL, price_source TEXT NOT NULL,
  CHECK (placed_at < kickoff));
CREATE TABLE IF NOT EXISTS v4e_grade (
  bet_id TEXT PRIMARY KEY REFERENCES v4e_bet(bet_id), result TEXT NOT NULL CHECK (result IN ('W','L','P')),
  pnl REAL NOT NULL, home_score INTEGER NOT NULL, away_score INTEGER NOT NULL, graded_at TEXT NOT NULL,
  result_source TEXT NOT NULL);
"""
for _t in ("v4e_strategy", "v4e_decision", "v4e_bet", "v4e_grade"):
    DDL += (f"CREATE TRIGGER IF NOT EXISTS {_t}_nu BEFORE UPDATE ON {_t} BEGIN SELECT RAISE(ABORT,'{_t} is append-only'); END;\n"
            f"CREATE TRIGGER IF NOT EXISTS {_t}_nd BEFORE DELETE ON {_t} BEGIN SELECT RAISE(ABORT,'{_t} is append-only'); END;\n")


def connect(path=":memory:"):
    c = sqlite3.connect(path)
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(DDL)
    return c


def _id(*parts):
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:32]


def register(conn, strategy_id, version, description, params, registered_at):
    with conn:
        conn.execute("INSERT INTO v4e_strategy (strategy_id, version, description, params_json, registered_at)"
                     " VALUES (?,?,?,?,?) ON CONFLICT (strategy_id, version) DO NOTHING", (strategy_id, version, description, json.dumps(params, sort_keys=True),
                                             registered_at))
    row = conn.execute("SELECT params_json FROM v4e_strategy WHERE strategy_id=? AND version=?",
                       (strategy_id, version)).fetchone()
    if json.loads(row[0]) != json.loads(json.dumps(params, sort_keys=True)):
        raise ValueError("strategy version already registered with different parameters: bump the version")


def strategies_tested(conn):
    return conn.execute("SELECT COUNT(*) FROM v4e_strategy").fetchone()[0]


def record_decision(conn, strategy_id, version, mode, row, decided_at, kickoff):
    did = _id("dec", strategy_id, version, mode, row["game_id"], row["market"], row["selection"], row.get("book"),
              row.get("line"), row.get("american"), decided_at)
    # ON CONFLICT DO NOTHING (not INSERT OR IGNORE): only an identical, already
    # recorded decision is skipped; a CHECK violation (e.g. decided after kickoff) still raises.
    conn.execute("INSERT INTO v4e_decision (decision_id, strategy_id, version, mode, game_id, market, selection,"
                 " book, line, price, decided_at, kickoff, decision, payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                 " ON CONFLICT (decision_id) DO NOTHING",
                 (did, strategy_id, version, mode, row["game_id"], row["market"], row["selection"], row.get("book"),
                  row.get("line"), row.get("american"), decided_at, kickoff, row["decision"],
                  json.dumps(row, sort_keys=True, default=str)))
    return did


def record_bet(conn, decision_id, strategy_id, version, mode, game_id, market, side, line, price, stake, placed_at,
               kickoff, p_model, p_push, ev, price_source, close_fair_prob=None):
    bid = _id("bet", decision_id)
    conn.execute("INSERT INTO v4e_bet (bet_id, decision_id, strategy_id, version, mode, game_id, market, side, line,"
                 " price, stake, placed_at, kickoff, p_model, p_push, ev, close_fair_prob, price_source)"
                 " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (bid, decision_id, strategy_id, version, mode, game_id, market, side, line, price, stake, placed_at,
                  kickoff, p_model, p_push, ev, close_fair_prob, price_source))
    return bid


def grade(conn, bet_id, home_score, away_score, graded_at, result_source):
    from edge_v4 import pricing
    b = conn.execute("SELECT market, side, line, price, stake FROM v4e_bet WHERE bet_id=?", (bet_id,)).fetchone()
    market, side, line, price, stake = b
    margin, total = home_score - away_score, home_score + away_score
    if market == "spreads":
        x = (margin if side == "home" else -margin) + line
    elif market == "totals":
        x = (total - line) if side == "over" else (line - total)
    else:
        x = (margin if side == "home" else -margin)
    r = "W" if x > 0 else ("P" if x == 0 else "L")
    pnl = stake * pricing.net_profit_per_unit(price) if r == "W" else (-stake if r == "L" else 0.0)
    conn.execute("INSERT INTO v4e_grade (bet_id, result, pnl, home_score, away_score, graded_at, result_source)"
                 " VALUES (?,?,?,?,?,?,?)", (bet_id, r, pnl, home_score, away_score, graded_at, result_source))
    return r, pnl


def graded_bets(conn, strategy_id=None, mode=None):
    sql = ("SELECT b.bet_id, b.strategy_id, b.market, b.side, b.line, b.price, b.stake, b.placed_at, b.close_fair_prob,"
           " b.p_model, b.ev, g.result, g.pnl FROM v4e_bet b JOIN v4e_grade g USING (bet_id) WHERE 1=1")
    args = []
    if strategy_id:
        sql += " AND b.strategy_id=?"
        args.append(strategy_id)
    if mode:
        sql += " AND b.mode=?"
        args.append(mode)
    cols = ["bet_id", "strategy_id", "market", "side", "line", "american", "stake", "placed_at", "close_fair_prob",
            "p_model", "ev", "result", "pnl"]
    return [dict(zip(cols, r)) for r in conn.execute(sql + " ORDER BY b.placed_at", args)]
