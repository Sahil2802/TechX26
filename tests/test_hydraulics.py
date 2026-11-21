"""Pre-staging hydraulics check: Joukowsky surge + steady-state pressure after a valve closure."""
import pytest

from aquagrid import config, db as dbm, gateway, hydraulics, tools
from aquagrid.schemas import ActionRequest

VALVE = {"kind": "valve_position", "target_id": "V-S4-17-18", "target_value": 0.0,
         "rationale": "Isolate genuine leak between J17 and J18."}


def _state():
    conn = dbm.connect(ro=True)
    try:
        return tools.load_state(conn)
    finally:
        conn.close()


def _req(value=0.0, vid="V-S4-17-18"):
    return ActionRequest(kind="valve_position", target_id=vid, target_value=value, rationale="test")


def test_joukowsky_slow_closure_uses_michaud():
    # v=1 m/s, L=500 m, T=120 s: 2*L*v / (g*T)
    assert hydraulics.joukowsky_surge_m(500, 1.0, 120) == pytest.approx(0.8495, abs=1e-3)


def test_joukowsky_rapid_closure_uses_full_wave_speed():
    # T below 2L/a: a*v/g = 1000/9.81
    assert hydraulics.joukowsky_surge_m(500, 1.0, 0.5) == pytest.approx(101.94, abs=0.01)


def test_no_surge_when_nothing_closes():
    assert hydraulics.joukowsky_surge_m(500, 0.0, 120) == 0.0


def test_fallback_report_is_within_limits_for_the_leak_isolation(db, monkeypatch):
    monkeypatch.setattr(hydraulics, "_run_wntr", lambda *a, **k: (_ for _ in ()).throw(ImportError("off")))
    sim = hydraulics.assess_valve_change(_req(), _state())
    assert sim.method == "joukowsky_fallback" and sim.closure_s == config.DEFAULT_CLOSURE_S
    assert sim.surge_head_m <= config.MAX_SURGE_M and sim.min_pressure_m >= config.MIN_SERVICE_PRESSURE_M
    assert sim.critical_nodes_ok


def test_wntr_report_is_within_limits_for_the_leak_isolation(db):
    pytest.importorskip("wntr.sim")
    sim = hydraulics.assess_valve_change(_req(), _state())
    assert sim.method == "wntr"
    assert sim.min_pressure_m >= config.MIN_SERVICE_PRESSURE_M and sim.surge_head_m <= config.MAX_SURGE_M


def test_wntr_failure_degrades_to_fallback_and_says_so(db, monkeypatch):
    def boom(*a, **k):
        raise ImportError("DLL load failed")
    monkeypatch.setattr(hydraulics, "_run_wntr", boom)
    sim = hydraulics.assess_valve_change(_req(), _state())
    assert sim.method == "joukowsky_fallback" and "DLL load failed" in sim.notes


def test_fast_closure_breaches_the_surge_limit(db, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CLOSURE_S", 1)
    sim = hydraulics.assess_valve_change(_req(), _state())
    assert sim.surge_head_m > config.MAX_SURGE_M


def test_low_supply_head_breaches_the_pressure_floor(db, monkeypatch):
    monkeypatch.setattr(config, "RESERVOIR_HEAD_M", 30.0)
    sim = hydraulics.assess_valve_change(_req(), _state())
    assert sim.min_pressure_m < config.MIN_SERVICE_PRESSURE_M


def test_opening_a_valve_is_not_assessed(db):
    assert hydraulics.assess_valve_change(_req(100.0), _state()) is None


# ---- wired into the gateway ----------------------------------------------------------------------

def _rows():
    conn = dbm.connect(ro=True)
    try:
        return conn.execute("SELECT * FROM actions").fetchall()
    finally:
        conn.close()


def test_pending_valve_closure_carries_a_sim_report(db):
    res = gateway.stage_gated_valve_or_setpoint_action(VALVE, "r1", "llm:test")
    assert res.status == "PENDING_APPROVAL", res.reasons
    rec = next(a for a in gateway.list_actions() if a.id == res.action_id)
    assert rec.sim is not None and rec.sim.surge_head_m <= config.MAX_SURGE_M
    assert any(c.name == "hydraulics" and c.passed for c in rec.guard.checks)


def test_surge_over_limit_is_rejected_at_staging(db, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CLOSURE_S", 1)
    res = gateway.stage_gated_valve_or_setpoint_action(VALVE, "r1", "llm:test")
    assert res.status == "REJECTED_BY_GUARD" and any("surge" in r for r in res.reasons)
    assert _rows()[0]["sim_json"] is not None


def test_approval_rechecks_hydraulics_against_fresh_state(db, monkeypatch):
    res = gateway.stage_gated_valve_or_setpoint_action(VALVE, "r1", "llm:test")
    monkeypatch.setattr(config, "RESERVOIR_HEAD_M", 30.0)  # supply head collapses after staging
    rec = gateway.approve_and_execute(res.action_id, "alice")
    assert rec.status == "DENIED"
    conn = dbm.connect(ro=True)
    assert conn.execute("SELECT position_pct FROM valves WHERE id='V-S4-17-18'").fetchone()[0] == 100


def test_plant_setpoints_have_no_sim_report(db):
    res = gateway.stage_gated_valve_or_setpoint_action(
        {"kind": "plant_setpoint", "target_id": "PLANT-1", "target_value": 1800.0, "rationale": "trim"}, "r1", "llm:t")
    assert res.status == "PENDING_APPROVAL" and _rows()[0]["sim_json"] is None
