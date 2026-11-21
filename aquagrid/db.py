"""SQLite DDL, connections, transactions, audit logging."""
import json
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
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
  guard_json TEXT, sim_json TEXT, approver TEXT, note TEXT, created_ts TEXT, decided_ts TEXT,
  provenance TEXT DEFAULT 'agent', from_untrusted_text INTEGER DEFAULT 0, approval_token TEXT);
CREATE TABLE runs(run_id TEXT PRIMARY KEY, scenario TEXT, started_ts TEXT, status TEXT,
  provider TEXT, steps INTEGER, replans INTEGER);
CREATE TABLE audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT, stage TEXT,
  actor TEXT, event TEXT, payload_json TEXT);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS fire_reserves(
  id INTEGER PRIMARY KEY CHECK(id=1),
  volume_m3 REAL NOT NULL DEFAULT 0,
  sector TEXT NOT NULL DEFAULT 'S6',
  requester TEXT NOT NULL DEFAULT 'Civil Defence',
  requested_ts TEXT,
  note TEXT
);
INSERT OR IGNORE INTO fire_reserves(id, volume_m3, sector, requester) VALUES(1, 0, 'S6', 'Civil Defence');
CREATE TABLE IF NOT EXISTS approval_tokens(
  token TEXT PRIMARY KEY,
  action_id INTEGER NOT NULL,
  action_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  target_value REAL NOT NULL,
  created_ts TEXT NOT NULL,
  expires_ts TEXT NOT NULL,
  used_ts TEXT,
  used_by TEXT,
  status TEXT DEFAULT 'ACTIVE'
);
CREATE TABLE IF NOT EXISTS work_tickets(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scenario TEXT,
  ts TEXT,
  ticket_id TEXT,
  assigned_to TEXT,
  details TEXT
);
"""


_plant: ContextVar[str] = ContextVar("plant", default="A")


def set_plant(plant: str) -> None:
    if plant not in config.PLANTS:
        raise ValueError(f"unknown plant {plant!r}")
    _plant.set(plant)


def current_plant() -> str:
    return _plant.get()


def db_path(plant: str | None = None) -> str:
    """Plant A -> config.DB_PATH; plant B -> plant_b.sqlite beside it. Unknown plants raise."""
    plant = _plant.get() if plant is None else plant
    if plant not in config.PLANTS:
        raise ValueError(f"unknown plant {plant!r}")
    paths = {"A": config.DB_PATH, "B": str(Path(config.DB_PATH).with_name("plant_b.sqlite"))}
    return paths[plant]


def connect(ro: bool = False) -> sqlite3.Connection:
    """ro=True opens the file with mode=ro: any write raises OperationalError.
    Autocommit mode; use transaction() for atomic writes."""
    if ro:
        uri = Path(db_path()).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    else:
        Path(db_path()).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path(), isolation_level=None)
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


def init_db(plant: str | None = None) -> None:
    """Create a fresh database file with the full schema (drops any existing one)."""
    path = Path(db_path(plant))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.executescript(SCHEMA_SQL)
    finally:
        conn.close()


def migrate(conn: sqlite3.Connection) -> None:
    """Apply schema additions to an existing database without destroying data (idempotent)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS fire_reserves(
          id INTEGER PRIMARY KEY CHECK(id=1),
          volume_m3 REAL NOT NULL DEFAULT 0,
          sector TEXT NOT NULL DEFAULT 'S6',
          requester TEXT NOT NULL DEFAULT 'Civil Defence',
          requested_ts TEXT,
          note TEXT
        );
        INSERT OR IGNORE INTO fire_reserves(id, volume_m3, sector, requester)
          VALUES(1, 0, 'S6', 'Civil Defence');
        CREATE TABLE IF NOT EXISTS approval_tokens(
          token TEXT PRIMARY KEY,
          action_id INTEGER NOT NULL,
          action_type TEXT NOT NULL,
          target_id TEXT NOT NULL,
          target_value REAL NOT NULL,
          created_ts TEXT NOT NULL,
          expires_ts TEXT NOT NULL,
          used_ts TEXT,
          used_by TEXT,
          status TEXT DEFAULT 'ACTIVE'
        );
        CREATE TABLE IF NOT EXISTS work_tickets(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          scenario TEXT,
          ts TEXT,
          ticket_id TEXT,
          assigned_to TEXT,
          details TEXT
        );
    """)
    for col, ctype in (("provenance", "TEXT DEFAULT 'agent'"),
                       ("from_untrusted_text", "INTEGER DEFAULT 0"),
                       ("approval_token", "TEXT")):
        try:
            conn.execute(f"ALTER TABLE actions ADD COLUMN {col} {ctype}")
        except sqlite3.OperationalError:
            pass  # column already exists


def create_approval_token(conn: sqlite3.Connection, token: str, action_id: int, action_type: str,
                          target_id: str, target_value: float, created_ts: str, expires_ts: str) -> None:
    conn.execute(
        "INSERT INTO approval_tokens(token, action_id, action_type, target_id, target_value, created_ts, expires_ts) "
        "VALUES (?,?,?,?,?,?,?)",
        (token, action_id, action_type, target_id, target_value, created_ts, expires_ts))


def get_approval_token(conn: sqlite3.Connection, token: str) -> dict | None:
    row = conn.execute("SELECT * FROM approval_tokens WHERE token=?", (token,)).fetchone()
    return dict(row) if row else None


def use_approval_token(conn: sqlite3.Connection, token: str, used_by: str, used_ts: str) -> None:
    conn.execute("UPDATE approval_tokens SET used_ts=?, used_by=?, status='USED' WHERE token=?",
                 (used_ts, used_by, token))


def list_approval_tokens(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM approval_tokens ORDER BY created_ts DESC").fetchall()]


def add_work_ticket(conn: sqlite3.Connection, scenario: str, ts: str, ticket_id: str,
                    assigned_to: str, details: str) -> None:
    conn.execute(
        "INSERT INTO work_tickets(scenario, ts, ticket_id, assigned_to, details) VALUES (?,?,?,?,?)",
        (scenario, ts, ticket_id, assigned_to, details))


def get_work_tickets(conn: sqlite3.Connection, scenario: str, until_ts: str | None = None) -> list[dict]:
    sql = "SELECT * FROM work_tickets WHERE scenario=?"
    args = [scenario]
    if until_ts:
        sql += " AND ts<=?"
        args.append(until_ts)
    return [dict(r) for r in conn.execute(sql + " ORDER BY ts", args).fetchall()]


def get_fire_reserve(conn: sqlite3.Connection) -> dict:
    """Return the current Civil Defence fire reserve row (always a single row)."""
    return dict(conn.execute("SELECT * FROM fire_reserves WHERE id=1").fetchone())


def set_fire_reserve(conn: sqlite3.Connection, volume_m3: float, sector: str,
                     requester: str, note: str = "") -> None:
    """Update the fire reserve request. Logs timestamp automatically."""
    from datetime import datetime, timezone
    conn.execute(
        "UPDATE fire_reserves SET volume_m3=?, sector=?, requester=?, requested_ts=?, note=? WHERE id=1",
        (volume_m3, sector, requester,
         datetime.now(timezone.utc).isoformat(timespec="seconds"), note))


def log_event(conn: sqlite3.Connection, run_id: str | None, stage: str,
              actor: str, event: str, payload: dict) -> None:
    """The ONLY audit writer. actor: code | llm:<provider>/<model> | human:<id> | attacker.
    Does not commit: call inside transaction() or in autocommit mode."""
    conn.execute(
        "INSERT INTO audit_log(ts, run_id, stage, actor, event, payload_json) VALUES (?,?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), run_id, stage, actor,
         event, json.dumps(payload, default=str)),
    )
