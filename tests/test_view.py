"""Data helpers behind the operator console."""
import json
from types import SimpleNamespace

from aquagrid import agent, config, db as dbm, gateway, guards, seed_data, view
from aquagrid.schemas import ActionRequest


def _snapshot(scenario="peak_2pm"):
    seed_data.set_scenario(dbm.connect(), scenario)
    return view.load_snapshot()


def _arith(snap, production, floor=None):
    floor = snap["feeds"].effective_floor_pct if floor is None else floor
    return view.reservoir_arithmetic(snap["reservoir"]["level_pct"], snap["reservoir"]["capacity_m3"], production,
                                     snap["pre"]["demand_forecast"], floor)


def test_actor_badges():
    assert [view.actor_badge(a) for a in ("code", "llm:groq/gpt", "human:alice", "attacker", "x")] == [
        "CODE", "LLM", "HUMAN", "ATTACKER", "X"]


def test_snapshot_has_everything_the_console_needs(db):
    snap = _snapshot()
    assert snap["scenario"] == "peak_2pm" and snap["reservoir"]["level_pct"] == 44.0
    assert snap["plant"]["setpoint_m3h"] == 1900.0 and len(snap["valves"]) == 10
    assert snap["feeds"].effective_floor_pct == 40.0 and snap["pre"]["findings"]


def test_quarantined_sensors_are_listed_first_and_say_why(db):
    rows = view.sensor_rows(_snapshot()["pre"])
    assert [r["Sensor"] for r in rows[:2]] == ["P-J07", "T-INTAKE"]
    assert all(r["Trust"] == "QUARANTINED" and r["Why"] for r in rows[:2])
    assert all(r["Trust"] == "trusted" and r["Why"] == "" for r in rows[2:])


def test_finding_cards_always_credit_the_code(db):
    cards = {c["headline"].split(":")[0]: c for c in map(view.finding_card, _snapshot()["pre"]["findings"])}
    assert cards["S4"]["tone"] == "error" and "GENUINE LEAK at J17 on P-J17-J18" in cards["S4"]["headline"]
    assert "V-S4-17-18" in cards["S4"]["headline"]
    assert cards["S2"]["tone"] == "warning" and "No valve action is allowed" in cards["S2"]["headline"]
    assert cards["S1"]["tone"] == "ok"
    assert all("deterministic code" in c["decided_by"] for c in cards.values())


def test_the_throttle_veto_is_shown_as_exact_arithmetic(db):
    result = _arith(_snapshot(), config.THROTTLE_CANDIDATE_M3H)
    start, window, net, end = result["lines"]
    assert "44.0% of 40,000 m3 = 17,600 m3" in start
    assert "production 500 - demand 2,000 = -1,500 m3/h" in window and "14:00-18:00 (4 h)" in window
    assert "-1,500 x 4 h = -6,000 m3" in net
    assert "11,600 m3 = 29.0%" in end and "< 40.0% floor" in end and end.endswith("VETO")
    assert result["passes"] is False and result["end_pct"] == 29.0


def test_the_hold_passes_the_same_arithmetic(db):
    result = _arith(_snapshot(), config.PLANT_HOLD_M3H)
    assert "17,200 m3 = 43.0%" in result["lines"][3] and result["lines"][3].endswith("PASS")
    assert result["passes"] and result["min_pct"] == 43.0


def test_displayed_arithmetic_always_agrees_with_the_guard(db):
    snap = _snapshot()
    for production in (500.0, 1250.0, 1800.0, 1900.0):
        shown = _arith(snap, production)
        state = {"plant": snap["plant"], "reservoir": snap["reservoir"], "demand_forecast": snap["pre"]["demand_forecast"],
                 "bloom_active": snap["feeds"].bloom_active, "intake_trusted": snap["pre"]["intake_trusted"], "cl_s4": None}
        req = ActionRequest(kind="plant_setpoint", target_id="PLANT-1", target_value=production, rationale="t")
        floor = next(c for c in guards.check_setpoint(req, state).checks if c.name == "reserve_floor")
        assert (shown["passes"], shown["min_pct"]) == (floor.passed, floor.value)


def test_chart_curves_show_the_veto_against_the_floor(db):
    curves = view.level_curves(_snapshot())
    throttle = curves["Rejected: throttle to 500 m3/h"]
    assert min(throttle) == 29.0 and set(curves["Effective floor"]) == {40.0}
    assert min(curves["Scheduled plan"]) >= 40.0
    assert len({len(v) for v in curves.values()}) == 1 and curves["hour"][0] == 14


def test_trace_tells_the_story_of_a_run_with_who_did_what(db):
    ISOLATE = json.dumps({"kind": "valve_position", "target_id": "V-S4-17-18", "target_value": 0, "rationale": "leak"})
    THROTTLE = json.dumps({"kind": "plant_setpoint", "target_id": "PLANT-1", "target_value": 500, "rationale": "t"})
    steps = iter([("read_tariff_and_quality_feeds", "{}"), ("propose_action", THROTTLE), ("propose_action", ISOLATE)])

    def fake(messages, tools_):
        step = next(steps, None)
        if step is None:
            return SimpleNamespace(content="brief", tool_calls=None), "fake/model"
        return SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
            id="c", function=SimpleNamespace(name=step[0], arguments=step[1]))]), "fake/model"
    seed_data.set_scenario(dbm.connect(), "peak_2pm")
    res = agent.run_scenario("peak_2pm", llm=fake)
    gateway.approve_and_execute(res.actions[-1].id, "alice")
    rows = view.trace_rows(res.run_id)
    steps_seen = [(r["Who"], r["Step"]) for r in rows]
    assert steps_seen[0] == ("CODE", "pre_analysis/findings")
    assert ("LLM", "stage/REJECTED_BY_GUARD") in steps_seen and ("LLM", "stage/PENDING_APPROVAL") in steps_seen
    assert ("HUMAN", "execute/EXECUTED") in steps_seen
    veto = next(r for r in rows if r["Step"] == "stage/REJECTED_BY_GUARD")["Detail"]
    assert "29.0% < 40.0% floor" in veto
    assert [r["#"] for r in rows] == list(range(1, len(rows) + 1))
    assert all(len(r["Detail"]) <= view.DETAIL_WIDTH for r in rows)


def test_guard_rows_list_every_check(db):
    seed_data.set_scenario(dbm.connect(), "peak_2pm")
    res = gateway.stage_gated_valve_or_setpoint_action(
        {"kind": "valve_position", "target_id": "V-S4-17-18", "target_value": 0, "rationale": "leak"}, "r1", "llm:x")
    rows = view.guard_rows(gateway.list_actions()[0])
    assert res.status == "PENDING_APPROVAL"
    assert [r["Check"] for r in rows] == ["allowlist", "range", "leak_backing", "connectivity", "single_pending"]
    assert {r["Result"] for r in rows} == {"PASS"}


def test_approval_popup_lists_pending_ids_and_targets_the_gate():
    html = view.approval_popup_html([3, 7])
    assert "#3, #7" in html and "2 actions waiting" in html
    assert view.GATE_ANCHOR_ID in html and view.TAB_LABEL in html and "scrollIntoView" in html


def test_approval_popup_with_nothing_pending_only_clears_the_old_one():
    html = view.approval_popup_html([])
    assert "if (0 === 0) return;" in html and "remove()" in html
