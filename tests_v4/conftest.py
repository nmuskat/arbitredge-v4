import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sportsbook_v4.engine import advance, create_run  # noqa: E402
from sportsbook_v4.population import ensure_population  # noqa: E402
from sportsbook_v4.schema import ensure_schema  # noqa: E402
from tests_v4 import fixtures as fx  # noqa: E402

MID = "2026-10-29T12:00:00Z"


@pytest.fixture(scope="session")
def full_week(tmp_path_factory):
    """One full 10,000-bettor fixture week, advanced in two steps, on a temp DB file."""
    path = tmp_path_factory.mktemp("v4") / "v4_week.db"
    conn = sqlite3.connect(path)
    ensure_schema(conn)
    pid = ensure_population(conn, "nfl", fx.NFL_TEAMS)
    market, slate, facts = fx.build_quotes(), fx.build_slate(), fx.build_facts()
    run_id = create_run(conn, pid, slate, market)
    first = advance(conn, run_id, MID, market, facts, data_complete_through=MID)
    second = advance(conn, run_id, fx.WINDOW_END, market, facts, data_complete_through=fx.WINDOW_END)
    yield {"path": path, "conn": conn, "pid": pid, "run_id": run_id, "market": market,
           "slate": slate, "facts": facts, "first": first, "second": second}
    conn.close()
