"""Curveball U-5.1: Chlorine drops in Sector 2.

Brief: Chlorine residual in Sector 2 has dropped below 0.2 mg/L. Water quality outranks tariff savings,
so the tariff plan must change and an isolation-and-flush action must be staged.
Build: A water-quality floor in Python that marks the sector as non-compliant, removes it from tariff-driven
scheduling, and stages isolation and flushing through the gated write path for operator approval.
Live test: Inject the low chlorine reading during the 2:00 PM peak-tariff window.
"""
import pytest

from aquagrid import config, db as dbm, gateway, guards, scheduler, seed_data, tools
from aquagrid.schemas import ActionRequest


def test_chlorine_floor_marks_sector_non_compliant(db):
    """When chlorine in Sector 2 drops below 0.2 mg/L, analyze() marks it non-compliant."""
    conn = dbm.connect()
    seed_data.set_scenario(conn, "peak_2pm")
    # Inject low chlorine (0.08 mg/L < 0.20 mg/L)
    seed_data.inject_chlorine(conn, sector="S2", value=0.08)
    conn.close()

    pre = tools.pre_analysis()
    assert "S2" in pre["non_compliant_sectors"]
    assert pre["sector_chlorine"]["S2"] == 0.08
    alert = next(a for a in pre["alerts"] if a["kind"] == "chlorine_residual_breach")
    assert "0.08 mg/L" in alert["message"]
    assert "< 0.2" in alert["message"]


def test_water_quality_outranks_tariff_savings_in_schedule(db):
    """Tariff-driven optimization is rejected when a sector has a water-quality breach."""
    conn = dbm.connect()
    seed_data.set_scenario(conn, "peak_2pm")
    seed_data.inject_chlorine(conn, sector="S2", value=0.08)
    conn.close()

    feeds = tools.read_tariff_and_quality_feeds()
    sched = feeds.schedule
    # The throttle candidate must be rejected for water quality floor
    rejected = [c for c in sched.rejected_candidates if "quality" in c.reason.lower() or "chlorine" in c.reason.lower()]
    assert len(rejected) > 0
    assert "Water quality in Sector S2 (<0.2 mg/L chlorine) outranks tariff savings" in rejected[0].reason
    assert sched.kwh_saved == 0.0  # no tariff savings allowed when quality floor breached


def test_guard_blocks_tariff_throttle_during_chlorine_breach(db):
    """The setpoint guard blocks production throttle if Sector 2 is non-compliant."""
    conn = dbm.connect()
    seed_data.set_scenario(conn, "peak_2pm")
    seed_data.inject_chlorine(conn, sector="S2", value=0.08)
    state = tools.load_state(conn)
    conn.close()

    req = ActionRequest(kind="plant_setpoint", target_id="PLANT-1", target_value=500.0,
                        rationale="Peak tariff throttle")
    report = guards.check_setpoint(req, state)
    assert not report.passed
    wq_check = next(c for c in report.checks if c.name == "water_quality_floor")
    assert not wq_check.passed
    assert "Water quality floor violation" in wq_check.detail
    assert "outranks tariff savings" in wq_check.detail


def test_staging_isolation_and_flush_through_gated_write_path(db):
    """Stages isolation and flush through gated write path as PENDING_APPROVAL (does not auto-execute)."""
    conn = dbm.connect()
    seed_data.set_scenario(conn, "peak_2pm")
    seed_data.inject_chlorine(conn, sector="S2", value=0.08)
    conn.close()

    # Stage isolation & flush via tools helper
    res = tools.stage_sector_isolation_and_flush(sector="S2", run_id="u51-test")
    assert res.status == "PENDING_APPROVAL"
    assert res.action_id is not None

    # Check that valve did NOT move yet
    conn = dbm.connect(ro=True)
    v_s2_pos = conn.execute("SELECT position_pct FROM valves WHERE id='V-S2-IN'").fetchone()[0]
    conn.close()
    assert v_s2_pos == 100.0  # untouched until human approves

    # Human operator approves in control-room console
    rec = gateway.approve_and_execute(res.action_id, operator_id="chief_operator", note="Approved Sector 2 isolation & flush")
    assert rec.status == "EXECUTED"
    assert rec.approver == "chief_operator"

    # Valve is now closed (isolated)
    conn = dbm.connect(ro=True)
    v_s2_pos = conn.execute("SELECT position_pct FROM valves WHERE id='V-S2-IN'").fetchone()[0]
    conn.close()
    assert v_s2_pos == 0.0


def test_isolation_and_flush_rejected_if_no_breach(db):
    """If there is no water quality breach or leak, an isolation and flush is rejected by guards."""
    conn = dbm.connect()
    seed_data.set_scenario(conn, "baseline")  # baseline is healthy
    state = tools.load_state(conn)
    conn.close()

    req = ActionRequest(kind="isolation_and_flush", target_id="S2", target_value=0.0,
                        rationale="Unwarranted isolation")
    report = guards.check_isolation_and_flush(req, state)
    assert not report.passed
    q_check = next(c for c in report.checks if c.name == "quality_floor")
    assert not q_check.passed
    assert "no active water-quality breach" in q_check.detail


def test_live_injection_scenario_u51(db):
    """Live test scenario u51_chlorine drops chlorine at 13:45 / 14:00 peak window."""
    conn = dbm.connect()
    seed_data.set_scenario(conn, "u51_chlorine")
    conn.close()

    pre = tools.pre_analysis()
    assert "S2" in pre["non_compliant_sectors"]
    assert pre["cl_s2"] < config.CHLORINE_MIN_MGL
    assert abs(pre["cl_s2"] - 0.08) < 0.02

    feeds = tools.read_tariff_and_quality_feeds()
    assert "S2" in feeds.non_compliant_sectors
    assert len(feeds.schedule.rejected_candidates) > 0
