"""SQLite DDL, connections, transactions, audit logging."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from aquagrid import config

SCHEMA_SQL = """
CREATE TABLE junctions(id TEXT PRIMARY KEY, sector TEXT, elevation_m REAL, base_demand_lps REAL, critical INTEGER);
CREATE TABLE pipes(id TEXT PRIMARY KEY, from_node TEXT, to_node TEXT, length_m REAL, diameter_mm REAL, valve_id TEXT);
CREATE TABLE sensors(id TEXT PRIMARY KEY, kind TEXT, location TEXT, sector TEXT, neighbours_json TEXT);
CREATE TABLE telemetry(scenario TEXT, ts TEXT, sensor_id TEXT, value REAL);
CREATE INDEX idx_telemetry ON telemetry(scenario, sensor_id, ts);
CREATE TABLE tariff(hour INTEGER PRIMARY KEY, aed_per_kwh REAL, band TEXT);
CREATE TABLE demand_forecast(scenario TEXT, hour INTEGER, demand_m3h REAL);
CREATE TABLE reservoir(id TEXT PRIMARY KEY, capacity_m3 REAL, level_pct REAL);
CREATE TABLE plant(id TEXT PRIMARY KEY, setpoint_m3h REAL, min_m3h REAL, max_m3h REAL, kwh_per_m3 REAL);
CREATE TABLE valves(id TEXT PRIMARY KEY, pipe_id TEXT, position_pct REAL);
CREATE TABLE quality_alerts(scenario TEXT, ts TEXT, source TEXT, kind TEXT, severity TEXT, message TEXT);
CREATE TABLE actions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, kind TEXT, target_id TEXT,
  from_value REAL, to_value REAL, rationale TEXT, proposed_by TEXT, status TEXT,
  guard_json TEXT, sim_json TEXT, approver TEXT, note TEXT, created_ts TEXT, decided_ts TEXT);
CREATE TABLE runs(run_id TEXT PRIMARY KEY, scenario TEXT, started_ts TEXT, status TEXT,
  provider TEXT, steps INTEGER, replans INTEGER);
CREATE TABLE audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT, stage TEXT,
  actor TEXT, event TEXT, payload_json TEXT);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
"""


def connect(ro: bool = False) -> sqlite3.Connection:
    """ro=True opens the file with mode=ro: any write raises OperationalError.
    Autocommit mode; use transaction() for atomic writes."""
    if ro:
        uri = Path(config.DB_PATH).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    else:
        Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE ... COMMIT, or ROLLBACK on any exception (which is re-raised)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def init_db() -> None:
    """Create a fresh database file with the full schema (drops any existing one)."""
    path = Path(config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA_SQL)
    finally:
        conn.close()


def log_event(conn: sqlite3.Connection, run_id: str | None, stage: str,
              actor: str, event: str, payload: dict) -> None:
    """The ONLY audit writer. actor: code | llm:<provider>/<model> | human:<id> | attacker.
    Does not commit: call inside transaction() or in autocommit mode."""
    conn.execute(
        "INSERT INTO audit_log(ts, run_id, stage, actor, event, payload_json) VALUES (?,?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), run_id, stage, actor,
         event, json.dumps(payload, default=str)),
    )
