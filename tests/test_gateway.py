"""The gateway is the only write path. Staging must never move a valve or the plant."""
import re
import sqlite3
from pathlib import Path

import pytest

from aquagrid import config, db as dbm, gateway
from aquagrid.schemas import Check, GuardReport

VALVE = {"kind": "valve_position", "target_id": "V-S4-17-18", "target_value": 0.0,
         "rationale": "Isolate genuine leak between J17 and J18."}
PLANT = {"kind": "plant_setpoint", "target_id": "PLANT-1", "target_value": 1800.0,
         "rationale": "Trim intake slightly; reservoir stays above the floor."}
THROTTLE = {**PLANT, "target_value": 500.0, "rationale": "Peak throttle."}


def _rows(table, where=""):
    conn = dbm.connect(ro=True)
    return conn.execute(f"SELECT * FROM {table} {where}").fetchall()


def _stage(raw, run_id="r1", by="llm:test/model"):
    return gateway.stage_gated_valve_or_setpoint_action(raw, run_id, by)


def test_extra_field_approved_blocked(db):
    res = _stage({**VALVE, "approved": True})
    assert res.status == "BLOCKED" and res.action_id is None
    assert _rows("actions") == []


def test_blocked_attempt_is_audited(db):
    _stage({**VALVE, "approved": True})
    events = [r["event"] for r in _rows("audit_log")]
    assert "BLOCKED" in events


def test_non_dict_args_blocked(db):
    assert _stage("close everything").status == "BLOCKED"


def test_unknown_valve_blocked(db):
    assert _stage({**VALVE, "target_id": "V-NOPE-1"}).status == "BLOCKED"


def test_unknown_plant_blocked(db):
    assert _stage({**PLANT, "target_id": "PLANT-9"}).status == "BLOCKED"


def test_stage_never_moves_valve_or_plant(db):
    before = (_rows("valves"), _rows("plant"))
    res = _stage(VALVE)
    assert res.status == "PENDING_APPROVAL" and res.action_id
    assert _stage(PLANT).status == "PENDING_APPROVAL"
    assert [tuple(r) for r in _rows("valves")] == [tuple(r) for r in before[0]]
    assert [tuple(r) for r in _rows("plant")] == [tuple(r) for r in before[1]]


def test_pending_row_records_from_and_to_and_proposer(db):
    _stage(VALVE)
    row = _rows("actions")[0]
    assert (row["from_value"], row["to_value"], row["status"]) == (100.0, 0.0, "PENDING_APPROVAL")
    assert row["proposed_by"] == "llm:test/model" and row["approver"] is None


def test_guard_failure_is_recorded_as_rejected(db, monkeypatch):
    fail = GuardReport(passed=False, checks=[Check(name="floor", passed=False, detail="29.0% < 40.0%")])
    monkeypatch.setattr(gateway, "evaluate_action", lambda req, state: fail)
    res = _stage(PLANT)
    assert res.status == "REJECTED_BY_GUARD" and "29.0% < 40.0%" in res.reasons[0]
    assert _rows("actions")[0]["status"] == "REJECTED_BY_GUARD"


def test_pending_cap_per_run(db):
    for value in (1800.0, 1850.0, 1700.0)[:config.MAX_PENDING_PER_RUN]:
        assert _stage({**PLANT, "target_value": value}).status == "PENDING_APPROVAL"
    assert _stage({**PLANT, "target_value": 1750.0}).status == "BLOCKED"
    assert _stage(PLANT, run_id="other-run").status == "PENDING_APPROVAL"


def test_throttle_is_rejected_by_the_real_guards_and_recorded(db):
    res = _stage(THROTTLE)
    assert res.status == "REJECTED_BY_GUARD" and "29.0% < 40.0% floor" in " ".join(res.reasons)
    assert _rows("actions")[0]["status"] == "REJECTED_BY_GUARD"


def test_closing_the_inlet_is_rejected_by_the_real_guards(db):
    res = _stage({**VALVE, "target_id": "V-S4-IN"})
    assert res.status == "REJECTED_BY_GUARD" and "J19" in " ".join(res.reasons)


def test_second_pending_change_on_the_same_valve_is_rejected(db):
    assert _stage(VALVE).status == "PENDING_APPROVAL"
    assert _stage(VALVE).status == "REJECTED_BY_GUARD"


# ---- approval, rejection, revert (human side) ---------------------------------------------------

def _pending(raw=VALVE):
    res = _stage(raw)
    assert res.status == "PENDING_APPROVAL", res.reasons
    return res.action_id


def _scalar(sql):
    return dbm.connect(ro=True).execute(sql).fetchone()[0]


def test_approve_requires_a_pending_action(db):
    with pytest.raises(ValueError):
        gateway.approve_and_execute(999, "alice")
    rejected = _stage(THROTTLE).action_id
    with pytest.raises(ValueError):
        gateway.approve_and_execute(rejected, "alice")
    action_id = _pending()
    gateway.approve_and_execute(action_id, "alice")
    with pytest.raises(ValueError):
        gateway.approve_and_execute(action_id, "alice")


def test_approve_executes_the_valve_and_audits_the_human(db):
    rec = gateway.approve_and_execute(_pending(), "alice", "confirmed on site")
    assert (rec.status, rec.approver, rec.note) == ("EXECUTED", "alice", "confirmed on site")
    assert _scalar("SELECT position_pct FROM valves WHERE id='V-S4-17-18'") == 0
    audit = _rows("audit_log", "WHERE event='EXECUTED'")
    assert audit and audit[0]["actor"] == "human:alice"


def test_approve_executes_a_plant_setpoint(db):
    rec = gateway.approve_and_execute(_pending(PLANT), "alice")
    assert rec.status == "EXECUTED" and _scalar("SELECT setpoint_m3h FROM plant") == 1800.0


def test_approve_revalidates_floor_against_fresh_state(db):
    action_id = _pending(PLANT)
    dbm.connect().execute("UPDATE reservoir SET level_pct=38")  # reservoir fell after staging
    rec = gateway.approve_and_execute(action_id, "alice")
    assert rec.status == "DENIED" and "re-validation failed" in rec.note
    assert _scalar("SELECT setpoint_m3h FROM plant") == 1900.0


def test_execute_rolls_back_on_error(db, monkeypatch):
    real = dbm.log_event

    def fail_on_execute(conn, run_id, stage, actor, event, payload):
        if event == "EXECUTED":
            raise RuntimeError("audit store unavailable")
        return real(conn, run_id, stage, actor, event, payload)
    monkeypatch.setattr(gateway.db, "log_event", fail_on_execute)
    rec = gateway.approve_and_execute(_pending(), "alice")
    assert rec.status == "EXECUTION_FAILED" and "audit store unavailable" in rec.note
    assert _scalar("SELECT position_pct FROM valves WHERE id='V-S4-17-18'") == 100


def test_reject_marks_denied_and_moves_nothing(db):
    rec = gateway.reject(_pending(), "alice", "not convinced")
    assert (rec.status, rec.approver, rec.note) == ("DENIED", "alice", "not convinced")
    assert _scalar("SELECT position_pct FROM valves WHERE id='V-S4-17-18'") == 100
    with pytest.raises(ValueError):
        gateway.reject(rec.id, "alice")


def test_revert_restores_the_previous_value(db):
    action_id = _pending()
    gateway.approve_and_execute(action_id, "alice")
    rec = gateway.revert(action_id, "bob", "leak was a false alarm")
    assert rec.status == "REVERTED" and _scalar("SELECT position_pct FROM valves WHERE id='V-S4-17-18'") == 100
    assert _rows("audit_log", "WHERE event='REVERTED'")[0]["actor"] == "human:bob"


def test_revert_only_applies_to_executed_actions(db):
    with pytest.raises(ValueError):
        gateway.revert(_pending(), "bob")


def test_list_actions_filters_by_status_and_run(db):
    a = _pending(VALVE)
    _stage(THROTTLE, run_id="r2")
    assert [r.id for r in gateway.list_actions(status="PENDING_APPROVAL")] == [a]
    assert [r.status for r in gateway.list_actions(run_id="r2")] == ["REJECTED_BY_GUARD"]
    assert len(gateway.list_actions()) == 2
    assert gateway.list_actions(status="PENDING_APPROVAL")[0].guard.passed


def test_stage_rolls_back_when_audit_write_fails(db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(gateway.db, "log_event", boom)
    res = _stage(VALVE)
    assert res.status == "EXECUTION_FAILED"
    assert _rows("actions") == []


def test_read_tools_cannot_write(db):
    with pytest.raises(sqlite3.OperationalError):
        dbm.connect(ro=True).execute("UPDATE valves SET position_pct=0")


def test_only_gateway_and_seed_reset_write_valves_and_plant(db):
    """seed_data is the operator/test reset path; nothing the agent imports may write."""
    pkg = Path(gateway.__file__).parent
    writers = {p.name for p in pkg.glob("*.py")
               if re.search(r"UPDATE\s+(valves|plant)\b", p.read_text(encoding="utf-8"), re.I)}
    assert writers <= {"gateway.py", "seed_data.py"}
