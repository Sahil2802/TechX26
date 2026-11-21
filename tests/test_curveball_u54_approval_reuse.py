"""Curveball U-5.4: Approval reuse prevention.

Brief: The operator approved closing V-17 at 2:05 PM. At 2:40 PM the agent tries to reuse that approval
to close V-18. Approvals must be single-use and specific.
Build: Approval tokens bound to action type, valve ID, parameters and expiry, marked used on execution
and checked in the gateway.
Live test: Replay the V-17 approval for V-18, then replay it for V-17 a second time.
"""
from datetime import datetime, timedelta, timezone
import pytest

from aquagrid import db as dbm, gateway, seed_data


def test_approval_token_is_bound_to_target_type_and_parameters(db):
    """Approval token records bound target_id, kind, target_value and expiration timestamp."""
    conn = dbm.connect()
    seed_data.set_scenario(conn, "peak_2pm")
    conn.close()

    res = gateway.stage_gated_valve_or_setpoint_action({
        "kind": "valve_position",
        "target_id": "V-S4-17-18",
        "target_value": 0.0,
        "rationale": "Isolate leak at J17-J18",
    }, run_id="run-u54", proposed_by="llm:test")
    action_id = res.action_id

    # Operator issues approval token at 14:05 (2:05 PM)
    tok = gateway.issue_approval_token(action_id, operator_id="operator_dan", valid_minutes=15,
                                       now="2026-11-20T14:05:00")
    assert tok.token.startswith("tok_")
    assert tok.action_id == action_id
    assert tok.action_type == "valve_position"
    assert tok.target_id == "V-S4-17-18"
    assert tok.target_value == 0.0
    assert tok.created_ts == "2026-11-20T14:05:00"
    assert tok.expires_ts == "2026-11-20T14:20:00"
    assert tok.status == "ACTIVE"


def test_replay_v17_approval_for_v18_is_rejected_target_mismatch(db):
    """Live test 1: Replay the V-17 approval for V-18 -> rejected by gateway."""
    conn = dbm.connect()
    seed_data.set_scenario(conn, "peak_2pm")
    conn.close()

    # Stage V-17 action (P-J17-J18)
    res_17 = gateway.stage_gated_valve_or_setpoint_action({
        "kind": "valve_position", "target_id": "V-S4-17-18", "target_value": 0.0, "rationale": "Close V-17"
    }, run_id="r17", proposed_by="llm:test")

    # Issue token for V-17
    tok_17 = gateway.issue_approval_token(res_17.action_id, operator_id="operator_dan", valid_minutes=15,
                                          now="2026-11-20T14:05:00")

    # Stage V-18 action (P-J18-J19 or V-S4-18-20)
    res_18 = gateway.stage_gated_valve_or_setpoint_action({
        "kind": "valve_position", "target_id": "V-S4-18-20", "target_value": 0.0, "rationale": "Close V-18"
    }, run_id="r18", proposed_by="llm:test")

    # Try to execute V-18 action using V-17 token -> must be rejected
    with pytest.raises(ValueError) as excinfo:
        gateway.approve_and_execute(res_18.action_id, operator_id="operator_dan",
                                    token_id=tok_17.token, now="2026-11-20T14:06:00")
    assert "cannot be used for" in str(excinfo.value)
    assert "bound to target" in str(excinfo.value) or "bound to" in str(excinfo.value)


def test_replay_token_a_second_time_is_rejected_single_use(db):
    """Live test 2: Execute V-17, then replay it for V-17 a second time -> rejected by gateway."""
    conn = dbm.connect()
    seed_data.set_scenario(conn, "peak_2pm")
    conn.close()

    res = gateway.stage_gated_valve_or_setpoint_action({
        "kind": "valve_position", "target_id": "V-S4-17-18", "target_value": 0.0, "rationale": "Close V-17"
    }, run_id="r1", proposed_by="llm:test")
    tok = gateway.issue_approval_token(res.action_id, operator_id="operator_dan", valid_minutes=15,
                                       now="2026-11-20T14:05:00")

    # First execution succeeds and marks token USED
    rec = gateway.approve_and_execute(res.action_id, operator_id="operator_dan",
                                      token_id=tok.token, now="2026-11-20T14:06:00")
    assert rec.status == "EXECUTED"

    # Stage another action for the same valve
    res_second = gateway.stage_gated_valve_or_setpoint_action({
        "kind": "valve_position", "target_id": "V-S4-17-18", "target_value": 0.0, "rationale": "Second attempt"
    }, run_id="r2", proposed_by="llm:test")

    # Try to replay the already-used token -> must be rejected
    with pytest.raises(ValueError) as excinfo:
        gateway.approve_and_execute(res_second.action_id, operator_id="operator_dan",
                                    token_id=tok.token, now="2026-11-20T14:10:00")
    assert "single-use only" in str(excinfo.value).lower() or "already used" in str(excinfo.value).lower()


def test_token_rejected_after_expiry(db):
    """Token approved at 2:05 PM is expired when attempted at 2:40 PM (35 min later)."""
    conn = dbm.connect()
    seed_data.set_scenario(conn, "peak_2pm")
    conn.close()

    res = gateway.stage_gated_valve_or_setpoint_action({
        "kind": "valve_position", "target_id": "V-S4-17-18", "target_value": 0.0, "rationale": "Close V-17"
    }, run_id="r1", proposed_by="llm:test")
    tok = gateway.issue_approval_token(res.action_id, operator_id="operator_dan", valid_minutes=15,
                                       now="2026-11-20T14:05:00")

    # Attempt execution at 2:40 PM (14:40) -> 35 min later, past 15 min expiry
    with pytest.raises(ValueError) as excinfo:
        gateway.approve_and_execute(res.action_id, operator_id="operator_dan",
                                    token_id=tok.token, now="2026-11-20T14:40:00")
    assert "expired" in str(excinfo.value).lower()
