"""The ONE write path for actions, valves and plant.

Staging (what the agent may call) only ever inserts REJECTED_BY_GUARD / PENDING_APPROVAL rows.
It never touches valves or plant.
"""
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from aquagrid import config, db, hydraulics, tools
from aquagrid.guards import evaluate_action, is_untrusted_text
from aquagrid.schemas import ActionRecord, ActionRequest, ApprovalTokenRecord, GuardReport, SimReport, StageResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso_dt(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _block(conn: sqlite3.Connection, run_id: str, actor: str, reasons: list[str], raw: object) -> StageResult:
    db.log_event(conn, run_id, "stage", actor, "BLOCKED", {"reasons": reasons, "raw_args": raw})
    return StageResult(status="BLOCKED", reasons=reasons)


def _evaluate(req: ActionRequest, state: dict) -> tuple[GuardReport, SimReport | None]:
    """The action guards, plus the hydraulics check for a valve closure. Used at staging and again at approval."""
    report = evaluate_action(req, state)
    if req.kind == "isolation_and_flush":
        return report, None
    sim = hydraulics.assess_valve_change(req, state)
    if sim is None:
        return report, None
    checks = [*report.checks, hydraulics.sim_check(sim)]
    return GuardReport(passed=all(c.passed for c in checks), checks=checks), sim


def _insert_action(conn: sqlite3.Connection, run_id: str, req: ActionRequest, from_value: float,
                   status: str, report: GuardReport, proposed_by: str, sim: SimReport | None = None,
                   provenance: str = "agent", from_untrusted_text: bool = False) -> int:
    cur = conn.execute(
        "INSERT INTO actions(run_id, kind, target_id, from_value, to_value, rationale, proposed_by,"
        " status, guard_json, sim_json, created_ts, provenance, from_untrusted_text) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, req.kind, req.target_id, from_value, req.target_value, req.rationale, proposed_by,
         status, report.model_dump_json(), sim.model_dump_json() if sim else None, _now(),
         provenance, int(from_untrusted_text)))
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
    elif req.kind == "isolation_and_flush":
        known = req.target_id in ("S2", "SECTOR-2", "V-S2-IN") or req.target_id in state["valves"]
        from_value = state["valves"].get("V-S2-IN", 100.0)
    else:
        known, from_value = req.target_id == state["plant"]["id"], state["plant"]["setpoint_m3h"]
    if not known:
        return _block(conn, run_id, proposed_by, [f"unknown {req.kind} target {req.target_id!r}"], req.model_dump())

    pending = conn.execute("SELECT count(*) FROM actions WHERE run_id=? AND status='PENDING_APPROVAL'",
                           (run_id,)).fetchone()[0]
    if pending >= config.MAX_PENDING_PER_RUN:
        return _block(conn, run_id, proposed_by,
                      [f"pending cap reached ({config.MAX_PENDING_PER_RUN} per run)"], req.model_dump())

    # U-5.3: Provenance tracking for untrusted text
    is_untrusted, _ = is_untrusted_text(req.rationale + " " + " ".join(req.evidence))
    provenance = "untrusted_text" if is_untrusted else "agent"

    report, sim = _evaluate(req, state)
    status = "PENDING_APPROVAL" if report.passed else "REJECTED_BY_GUARD"
    try:
        with db.transaction(conn):
            action_id = _insert_action(conn, run_id, req, from_value, status, report, proposed_by, sim,
                                       provenance=provenance, from_untrusted_text=is_untrusted)
            db.log_event(conn, run_id, "stage", proposed_by, status,
                         {"action_id": action_id, "request": req.model_dump(), "provenance": provenance,
                          "guard": json.loads(report.model_dump_json())})
    except Exception as e:  # transaction already rolled back
        return StageResult(status="EXECUTION_FAILED", reasons=[f"staging failed, rolled back: {e}"])
    reasons = [c.detail for c in report.checks if not c.passed]
    return StageResult(status=status, action_id=action_id, reasons=reasons)


# ---- human side: only web.py (operator API, token-protected) and cli.py may call these, never the agent ----

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
    elif kind == "isolation_and_flush":
        conn.execute("UPDATE valves SET position_pct=0 WHERE id='V-S2-IN'")
    else:
        conn.execute("UPDATE plant SET setpoint_m3h=? WHERE id=?", (value, target_id))


def _decide(conn: sqlite3.Connection, action_id: int, status: str, approver: str | None, note: str,
            guard: GuardReport | None = None, sim: SimReport | None = None) -> None:
    conn.execute("UPDATE actions SET status=?, approver=?, note=?, decided_ts=?, guard_json=COALESCE(?, guard_json),"
                 " sim_json=COALESCE(?, sim_json) WHERE id=?",
                 (status, approver, note, _now(), guard.model_dump_json() if guard else None,
                  sim.model_dump_json() if sim else None, action_id))


def issue_approval_token(action_id: int, operator_id: str, valid_minutes: int = config.APPROVAL_TOKEN_EXPIRY_MIN,
                         now: str | None = None) -> ApprovalTokenRecord:
    """U-5.4: Generate an approval token strictly bound to action type, target ID, target value and expiry."""
    conn = db.connect()
    try:
        row = _load_in_status(conn, action_id, "PENDING_APPROVAL")
        now_ts = now or _now()
        now_dt = datetime.fromisoformat(now_ts)
        exp_dt = now_dt + timedelta(minutes=valid_minutes)
        expires_ts = exp_dt.isoformat(timespec="seconds")
        token_str = f"tok_{uuid.uuid4().hex[:16]}"
        with db.transaction(conn):
            db.create_approval_token(conn, token_str, action_id, row["kind"], row["target_id"],
                                     row["to_value"], now_ts, expires_ts)
            db.log_event(conn, row["run_id"], "token", f"human:{operator_id}", "ISSUED",
                         {"token": token_str, "action_id": action_id, "target_id": row["target_id"],
                          "expires_ts": expires_ts})
        return ApprovalTokenRecord(token=token_str, action_id=action_id, action_type=row["kind"],
                                   target_id=row["target_id"], target_value=row["to_value"],
                                   created_ts=now_ts, expires_ts=expires_ts, status="ACTIVE")
    finally:
        conn.close()


def approve_and_execute(action_id: int, operator_id: str, note: str = "",
                        token_id: str | None = None, now: str | None = None) -> ActionRecord:
    """Re-validate against fresh state, verify single-use token binding (if provided), then change valve/plant."""
    conn = db.connect()
    try:
        row = _load(conn, action_id)
        current_ts = now or _now()

        # U-5.4: If approval token is provided, verify it strictly
        if token_id is not None:
            tok = db.get_approval_token(conn, token_id)
            if not tok:
                raise ValueError(f"approval token {token_id!r} not found")
            if tok["target_id"] != row["target_id"]:
                raise ValueError(f"approval token bound to target {tok['target_id']}, cannot be used for {row['target_id']}")
            if tok["action_type"] != row["kind"]:
                raise ValueError(f"approval token bound to action type {tok['action_type']}, cannot be used for {row['kind']}")
            if abs(tok["target_value"] - row["to_value"]) > 1e-4:
                raise ValueError(f"approval token bound to value {tok['target_value']}, cannot be used for {row['to_value']}")
            if tok["status"] != "ACTIVE" or tok["used_ts"] is not None:
                raise ValueError(f"approval token already used: single-use only (used at {tok['used_ts']})")
            curr_dt = _parse_iso_dt(current_ts)
            exp_dt = _parse_iso_dt(tok["expires_ts"])
            if curr_dt > exp_dt:
                raise ValueError(f"approval token expired at {tok['expires_ts']} (current time {current_ts})")

        if row["status"] != "PENDING_APPROVAL":
            raise ValueError(f"action {action_id} is {row['status']}, expected PENDING_APPROVAL")

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
                if token_id is not None:
                    db.use_approval_token(conn, token_id, operator_id, current_ts)
                    conn.execute("UPDATE actions SET approval_token=? WHERE id=?", (token_id, action_id))
                db.log_event(conn, row["run_id"], "execute", actor, "EXECUTED",
                             {**ctx, "from": row["from_value"], "to": row["to_value"], "token": token_id})
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
