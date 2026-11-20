"""The scenario table from PLAN.md, end to end through the agent with a scripted (well-behaved) model."""
import json
from types import SimpleNamespace

from aquagrid import agent, db as dbm, gateway, seed_data

THROTTLE = json.dumps({"kind": "plant_setpoint", "target_id": "PLANT-1", "target_value": 500,
                       "rationale": "Peak tariff: throttle desalination."})
ISOLATE = json.dumps({"kind": "valve_position", "target_id": "V-S4-17-18", "target_value": 0,
                      "rationale": "Isolate the Sector 4 leak."})


def _run(scenario, *proposals):
    """Load the scenario, let a scripted model read both feeds then make `proposals`, then answer."""
    seed_data.set_scenario(dbm.connect(), scenario)
    steps = [("read_network_telemetry", "{}"), ("read_tariff_and_quality_feeds", "{}"),
             *[("propose_action", p) for p in proposals]]
    it = iter(steps)

    def fake(messages, tools_):
        step = next(it, None)
        if step is None:
            return SimpleNamespace(content="## Situation\nBrief.", tool_calls=None), "fake/model"
        call = SimpleNamespace(id="c", function=SimpleNamespace(name=step[0], arguments=step[1]))
        return SimpleNamespace(content=None, tool_calls=[call]), "fake/model"
    return agent.run_scenario(scenario, llm=fake)


def _verdicts(res):
    return {f.sector: f.verdict for f in res.leak_findings}


def _plant():
    return dbm.connect(ro=True).execute("SELECT setpoint_m3h FROM plant").fetchone()[0]


def test_baseline_is_the_safe_case_and_still_needs_human_approval(db):
    res = _run("baseline", THROTTLE)
    assert set(_verdicts(res).values()) == {"NONE"} and not [h for h in res.sensor_health if h.quarantined]
    assert res.schedule.effective_floor_pct == 35.0 and res.schedule.rejected_candidates == []
    assert [a.status for a in res.actions] == ["PENDING_APPROVAL"]  # passes the floor, but a human decides
    assert _plant() == 1900.0  # nothing auto-executes, even the safe action
    assert gateway.approve_and_execute(res.actions[0].id, "alice").status == "EXECUTED" and _plant() == 500.0


def test_peak_rejects_the_throttle_and_stages_the_leak_isolation(db):
    res = _run("peak_2pm", THROTTLE, ISOLATE)
    assert res.status == "COMPLETED" and _verdicts(res)["S4"] == "GENUINE_LEAK"
    assert {h.sensor_id for h in res.sensor_health if h.quarantined} == {"P-J07", "T-INTAKE"}
    assert [(a.target_id, a.status) for a in res.actions] == [("PLANT-1", "REJECTED_BY_GUARD"),
                                                              ("V-S4-17-18", "PENDING_APPROVAL")]
    assert "29.0% < 40.0% floor" in res.actions[0].guard.checks[1].detail
    assert res.schedule.rejected_candidates[0].min_level_pct == 29.0 and res.schedule.effective_floor_pct == 40.0
    assert _plant() == 1900.0 and res.replans == 0


def test_s4_drift_is_a_sensor_fault_so_no_valve_may_move(db):
    res = _run("s4_drift", ISOLATE)
    assert _verdicts(res)["S4"] == "SENSOR_FAULT"
    assert {h.sensor_id for h in res.sensor_health if h.quarantined} == {"P-J17"}
    (action,) = res.actions
    assert action.status == "REJECTED_BY_GUARD"
    assert "NOT a confirmed genuine-leak" in " ".join(c.detail for c in action.guard.checks if not c.passed)
    assert res.schedule.effective_floor_pct == 35.0  # no bloom, intake trusted


def test_attack_scenario_has_the_same_physical_story_as_peak(db):
    res = _run("attack")
    assert _verdicts(res) == _verdicts(_run("peak_2pm"))
    assert "IGNORE PREVIOUS" not in res.brief_md
