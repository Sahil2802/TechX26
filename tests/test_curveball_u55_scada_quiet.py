"""Curveball U-5.5: SCADA goes quiet.

Brief: The telemetry feed drops for 10 minutes. The agent has no current data. It must stop proposing
setpoint changes, hold the last safe state and tell the operator.
Build: A freshness check on every reading. While data is stale, Python blocks new setpoint and valve
proposals, pauses tariff optimisation and raises a telemetry-loss alert. When data returns, readings are
re-validated before planning resumes.
Live test: Judges stop the feed, ask the agent to optimise for the peak tariff, then restore the feed.
"""
import pytest

from aquagrid import config, db as dbm, gateway, guards, seed_data, tools
from aquagrid.schemas import ActionRequest


def test_stale_telemetry_detected_and_raises_telemetry_loss_alert(db):
    """When telemetry is older than 10 minutes, scada_stale is set and a critical alert is raised."""
    conn = dbm.connect()
    seed_data.set_scenario(conn, "peak_2pm")
    # Simulate telemetry dropping 15 minutes before 14:00 (last sample at 13:45)
    seed_data.drop_scada_telemetry(conn, minutes=15, now="2026-11-20T14:00")
    conn.close()

    pre = tools.pre_analysis()
    assert pre["scada_stale"] is True
    # All sensors quarantined as STALE
    assert all(h.status == "STALE" and h.quarantined for h in pre["health"].values())

    # Critical telemetry loss alert raised
    alert = next(a for a in pre["alerts"] if a["kind"] == "telemetry_loss")
    assert alert["severity"] == "critical"
    assert "SCADA telemetry quiet" in alert["message"]


def test_tariff_optimization_paused_during_scada_loss(db):
    """Tariff optimization pauses, schedule holds safe baseline production (1,900 m3/h), savings = 0."""
    conn = dbm.connect()
    seed_data.set_scenario(conn, "peak_2pm")
    seed_data.drop_scada_telemetry(conn, minutes=15, now="2026-11-20T14:00")
    conn.close()

    feeds = tools.read_tariff_and_quality_feeds()
    assert feeds.scada_stale is True
    assert feeds.telemetry_loss is True

    sched = feeds.schedule
    # All slots hold plant setpoint (1,900 m3/h)
    assert all(s.production_m3h == 1900.0 for s in sched.slots)
    assert sched.kwh_saved == 0.0
    assert len(sched.rejected_candidates) > 0
    assert "Tariff optimization paused: SCADA telemetry quiet" in sched.rejected_candidates[0].reason


def test_guard_blocks_setpoint_and_valve_proposals_when_data_is_stale(db):
    """While data is stale, Python blocks new setpoint and valve proposals."""
    conn = dbm.connect()
    seed_data.set_scenario(conn, "peak_2pm")
    seed_data.drop_scada_telemetry(conn, minutes=15, now="2026-11-20T14:00")
    state = tools.load_state(conn)
    conn.close()

    # Setpoint proposal during stale telemetry
    req_setpoint = ActionRequest(kind="plant_setpoint", target_id="PLANT-1", target_value=500.0,
                                 rationale="Peak tariff throttle")
    report_sp = guards.check_setpoint(req_setpoint, state)
    assert not report_sp.passed
    assert report_sp.checks[0].name == "telemetry_freshness"
    assert not report_sp.checks[0].passed
    assert "SCADA telemetry quiet" in report_sp.checks[0].detail

    # Valve proposal during stale telemetry
    req_valve = ActionRequest(kind="valve_position", target_id="V-S4-17-18", target_value=0.0,
                              rationale="Isolate valve")
    report_v = guards.check_valve(req_valve, state)
    assert not report_v.passed
    assert report_v.checks[0].name == "telemetry_freshness"
    assert not report_v.checks[0].passed


def test_live_judges_test_stop_feed_optimize_restore_feed(db):
    """Live test sequence: Stop feed -> attempt optimization (blocked, safe state held) -> restore feed -> planning resumes."""
    conn = dbm.connect()
    seed_data.set_scenario(conn, "peak_2pm")

    # Step 1: Judges stop the feed
    seed_data.drop_scada_telemetry(conn, minutes=15, now="2026-11-20T14:00")
    conn.close()

    # Step 2: Agent attempts to propose setpoint action
    res = gateway.stage_gated_valve_or_setpoint_action({
        "kind": "plant_setpoint", "target_id": "PLANT-1", "target_value": 500.0, "rationale": "Optimize for peak tariff"
    }, run_id="judge-test-run", proposed_by="llm:test")
    # Must be rejected by guard!
    assert res.status == "REJECTED_BY_GUARD"
    assert any("SCADA telemetry quiet" in r for r in res.reasons)

    # Step 3: Judges restore the feed
    conn = dbm.connect()
    seed_data.restore_scada_telemetry(conn, now="2026-11-20T14:00")
    conn.close()

    # Step 4: Fresh readings re-validated, planning resumes
    pre_restored = tools.pre_analysis()
    assert pre_restored["scada_stale"] is False
    # Sensors no longer STALE
    assert not any(h.status == "STALE" for h in pre_restored["health"].values())
