"""The ONE write path for actions, valves and plant.

Staging (what the agent may call) only ever inserts REJECTED_BY_GUARD / PENDING_APPROVAL rows.
It never touches valves or plant.
"""
import json
import sqlite3
from datetime import datetime, timezone

from pydantic import ValidationError

from aquagrid import config, db, hydraulics, tools
from aquagrid.guards import evaluate_action
from aquagrid.schemas import ActionRecord, ActionRequest, GuardReport, SimReport, StageResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _block(conn: sqlite3.Connection, run_id: str, actor: str, reasons: list[str], raw: object) -> StageResult:
    db.log_event(conn, run_id, "stage", actor, "BLOCKED", {"reasons": reasons, "raw_args": raw})
    return StageResult(status="BLOCKED", reasons=reasons)


def _evaluate(req: ActionRequest, state: dict) -> tuple[GuardReport, SimReport | None]:
    """The action guards, plus the hydraulics check for a valve closure. Used at staging and again at approval."""
    report = evaluate_action(req, state)
    sim = hydraulics.assess_valve_change(req, state)
    if sim is None:
        return report, None
    checks = [*report.checks, hydraulics.sim_check(sim)]
    return GuardReport(passed=all(c.passed for c in checks), checks=checks), sim


def _insert_action(conn: sqlite3.Connection, run_id: str, req: ActionRequest, from_value: float,
                   status: str, report: GuardReport, proposed_by: str, sim: SimReport | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO actions(run_id, kind, target_id, from_value, to_value, rationale, proposed_by,"
        " status, guard_json, sim_json, created_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, req.kind, req.target_id, from_value, req.target_value, req.rationale, proposed_by,
         status, report.model_dump_json(), sim.model_dump_json() if sim else None, _now()))
    return cur.lastrowid


def stage_gated_valve_or_setpoint_action(raw_args: object, run_id: str, proposed_by: str) -> StageResult:
    """Validate -> allowlist -> per-run cap -> guards -> insert PENDING_APPROVAL (one transaction)."""
    conn = db.connect()
    try:
        return _stage(conn, raw_args, run_id, proposed_by)
    finally:
        conn.close()


def _stage(conn: sqlite3.Connection, raw_args: object, run_id: str, proposed_by: str) -> StageResult:
    try:
        req = ActionRequest.model_validate(raw_args)
    except ValidationError as e:
        reasons = [f"{'.'.join(map(str, err['loc'])) or 'args'}: {err['msg']}" for err in e.errors()]
        return _block(conn, run_id, proposed_by, reasons, raw_args)

    state = tools.load_state(conn)
    if req.kind == "valve_position":
        known, from_value = req.target_id in state["valves"], state["valves"].get(req.target_id)
    else:
        known, from_value = req.target_id == state["plant"]["id"], state["plant"]["setpoint_m3h"]
    if not known:
        return _block(conn, run_id, proposed_by, [f"unknown {req.kind} target {req.target_id!r}"], req.model_dump())

    pending = conn.execute("SELECT count(*) FROM actions WHERE run_id=? AND status='PENDING_APPROVAL'",
                           (run_id,)).fetchone()[0]
    if pending >= config.MAX_PENDING_PER_RUN:
        return _block(conn, run_id, proposed_by,
                      [f"pending cap reached ({config.MAX_PENDING_PER_RUN} per run)"], req.model_dump())

    report, sim = _evaluate(req, state)
    status = "PENDING_APPROVAL" if report.passed else "REJECTED_BY_GUARD"
    try:
        with db.transaction(conn):
            action_id = _insert_action(conn, run_id, req, from_value, status, report, proposed_by, sim)
            db.log_event(conn, run_id, "stage", proposed_by, status,
                         {"action_id": action_id, "request": req.model_dump(), "guard": json.loads(report.model_dump_json())})
    except Exception as e:  # transaction already rolled back
        return StageResult(status="EXECUTION_FAILED", reasons=[f"staging failed, rolled back: {e}"])
    reasons = [c.detail for c in report.checks if not c.passed]
    return StageResult(status=status, action_id=action_id, reasons=reasons)


# ---- human side: only app.py and cli.py may call these, never the agent -----------------------

def _record(row: sqlite3.Row) -> ActionRecord:
    d = dict(row)
    guard, sim = d.pop("guard_json"), d.pop("sim_json")
    return ActionRecord(**d, guard=GuardReport.model_validate_json(guard),
                        sim=SimReport.model_validate_json(sim) if sim else None)


def _load(conn: sqlite3.Connection, action_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
    if row is None:
        raise ValueError(f"action {action_id} not found")
    return row


def _load_in_status(conn: sqlite3.Connection, action_id: int, status: str) -> sqlite3.Row:
    row = _load(conn, action_id)
    if row["status"] != status:
        raise ValueError(f"action {action_id} is {row['status']}, expected {status}")
    return row


def _write_target(conn: sqlite3.Connection, kind: str, target_id: str, value: float) -> None:
    """The ONLY place valves and plant change."""
    if kind == "valve_position":
        conn.execute("UPDATE valves SET position_pct=? WHERE id=?", (value, target_id))
    else:
        conn.execute("UPDATE plant SET setpoint_m3h=? WHERE id=?", (value, target_id))


def _decide(conn: sqlite3.Connection, action_id: int, status: str, approver: str | None, note: str,
            guard: GuardReport | None = None, sim: SimReport | None = None) -> None:
    conn.execute("UPDATE actions SET status=?, approver=?, note=?, decided_ts=?, guard_json=COALESCE(?, guard_json),"
                 " sim_json=COALESCE(?, sim_json) WHERE id=?",
                 (status, approver, note, _now(), guard.model_dump_json() if guard else None,
                  sim.model_dump_json() if sim else None, action_id))


def approve_and_execute(action_id: int, operator_id: str, note: str = "") -> ActionRecord:
    """Re-validate against fresh state, then change valve/plant + action row + audit in ONE transaction."""
    conn = db.connect()
    try:
        row = _load_in_status(conn, action_id, "PENDING_APPROVAL")
        req = ActionRequest(kind=row["kind"], target_id=row["target_id"], target_value=row["to_value"],
                            rationale=row["rationale"])
        report, sim = _evaluate(req, tools.load_state(conn, exclude_action_id=action_id))
        actor, ctx = f"human:{operator_id}", {"action_id": action_id, "target": row["target_id"]}
        if not report.passed:
            why = "; ".join(c.detail for c in report.checks if not c.passed)
            with db.transaction(conn):
                _decide(conn, action_id, "DENIED", operator_id, f"re-validation failed at execution: {why}", report, sim)
                db.log_event(conn, row["run_id"], "execute", "code", "DENIED", {**ctx, "reasons": why})
            return _record(_load(conn, action_id))
        try:
            with db.transaction(conn):
                _write_target(conn, row["kind"], row["target_id"], row["to_value"])
                _decide(conn, action_id, "EXECUTED", operator_id, note, report, sim)
                db.log_event(conn, row["run_id"], "execute", actor, "EXECUTED",
                             {**ctx, "from": row["from_value"], "to": row["to_value"]})
        except Exception as e:  # the transaction rolled back: valve/plant are unchanged
            with db.transaction(conn):
                _decide(conn, action_id, "EXECUTION_FAILED", operator_id, f"rolled back: {e}", report, sim)
                db.log_event(conn, row["run_id"], "execute", actor, "EXECUTION_FAILED", {**ctx, "error": str(e)})
        return _record(_load(conn, action_id))
    finally:
        conn.close()


def reject(action_id: int, operator_id: str, note: str = "") -> ActionRecord:
    conn = db.connect()
    try:
        row = _load_in_status(conn, action_id, "PENDING_APPROVAL")
        with db.transaction(conn):
            _decide(conn, action_id, "DENIED", operator_id, note)
            db.log_event(conn, row["run_id"], "execute", f"human:{operator_id}", "DENIED",
                         {"action_id": action_id, "note": note})
        return _record(_load(conn, action_id))
    finally:
        conn.close()


def revert(action_id: int, operator_id: str, note: str = "") -> ActionRecord:
    """Compensating write: restore the value the action replaced."""
    conn = db.connect()
    try:
        row = _load_in_status(conn, action_id, "EXECUTED")
        with db.transaction(conn):
            _write_target(conn, row["kind"], row["target_id"], row["from_value"])
            _decide(conn, action_id, "REVERTED", row["approver"], f"reverted by {operator_id}: {note}".strip())
            db.log_event(conn, row["run_id"], "execute", f"human:{operator_id}", "REVERTED",
                         {"action_id": action_id, "restored": row["from_value"], "note": note})
        return _record(_load(conn, action_id))
    finally:
        conn.close()


def list_actions(status: str | None = None, run_id: str | None = None) -> list[ActionRecord]:
    clauses, args = [], []
    for column, value in (("status", status), ("run_id", run_id)):
        if value is not None:
            clauses.append(f"{column}=?")
            args.append(value)
    conn = db.connect(ro=True)
    try:
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return [_record(r) for r in conn.execute(f"SELECT * FROM actions {where} ORDER BY id", args)]
    finally:
        conn.close()
