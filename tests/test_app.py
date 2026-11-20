"""The operator console, driven headlessly the way an operator would use it."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from aquagrid import config, db as dbm, llm

APP = str(Path(__file__).resolve().parent.parent / "app.py")
THROTTLE = json.dumps({"kind": "plant_setpoint", "target_id": "PLANT-1", "target_value": 500, "rationale": "peak tariff"})
ISOLATE = json.dumps({"kind": "valve_position", "target_id": "V-S4-17-18", "target_value": 0, "rationale": "isolate leak"})


@pytest.fixture(autouse=True)
def no_token(monkeypatch):
    monkeypatch.setattr(config, "OPERATOR_TOKEN", None)


def scripted_llm(monkeypatch, *proposals):
    """Replace the real LLM chain with a model that reads once, makes `proposals`, then answers."""
    steps = iter([("read_tariff_and_quality_feeds", "{}"), *[("propose_action", p) for p in proposals]])

    def fake(messages, tools_):
        step = next(steps, None)
        if step is None:
            return SimpleNamespace(content="## Situation\nScripted brief.", tool_calls=None), "fake/model"
        return SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
            id="c", function=SimpleNamespace(name=step[0], arguments=step[1]))]), "fake/model"
    monkeypatch.setattr(llm, "chat", fake)


def open_app():
    at = AppTest.from_file(APP, default_timeout=90)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def run_agent(at):
    at.button(key="run").click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def keyed(at, prefix):
    return [b for b in at.button if b.key and b.key.startswith(prefix)]


def valve(name="V-S4-17-18"):
    return dbm.connect(ro=True).execute("SELECT position_pct FROM valves WHERE id=?", (name,)).fetchone()[0]


def texts(elements):
    return " ".join(str(e.value) for e in elements)


def test_peak_scenario_shows_the_veto_the_quarantine_and_the_leak(db):
    at = open_app()
    floor = next(m for m in at.metric if m.label == "Effective floor")
    assert floor.value == "40%" and any(m.label == "Intake sensor" and m.value == "UNTRUSTED" for m in at.metric)
    assert "REJECTED BY GUARD" in texts(at.error) and "29.0% < 40.0% floor" in texts(at.error)
    assert "GENUINE LEAK at J17 on P-J17-J18" in texts(at.error)
    sensors = at.dataframe[0].value
    assert list(sensors["Trust"][:2]) == ["🔴 QUARANTINED"] * 2 and list(sensors["Sensor"][:2]) == ["P-J07", "T-INTAKE"]
    code = " ".join(c.value for c in at.code)
    assert "11,600 m3 = 29.0%" in code and "VETO" in code and "17,200 m3 = 43.0%" in code and "PASS" in code
    assert "deterministic code" in texts(at.caption)


@pytest.mark.parametrize("scenario", ["baseline", "s4_drift", "attack"])
def test_every_scenario_loads_and_renders(db, scenario):
    at = open_app()
    at.selectbox(key="scenario").set_value(scenario)
    at.button(key="load").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(m.label == "Effective floor" for m in at.metric)


def test_baseline_has_no_quarantine_and_a_35_percent_floor(db):
    at = open_app()
    at.selectbox(key="scenario").set_value("baseline")
    at.button(key="load").click().run()
    assert next(m for m in at.metric if m.label == "Effective floor").value == "35%"
    assert set(at.dataframe[0].value["Trust"]) == {"🟢 trusted"} and "No anomaly" in texts(at.success)


def test_nothing_moves_until_a_human_approves_then_approve_executes(db, monkeypatch):
    scripted_llm(monkeypatch, THROTTLE, ISOLATE)
    at = run_agent(open_app())
    assert valve() == 100 and len(keyed(at, "approve_")) == 1  # the throttle was vetoed, the isolation is waiting
    assert "fake/model" in texts(at.success)
    at.text_input(key="operator_id").set_value("alice")
    keyed(at, "approve_")[0].click().run()
    assert not at.exception
    assert valve() == 0 and "EXECUTED by alice" in texts(at.success)
    assert not keyed(at, "approve_") and "No actions are waiting" in texts(at.info)


def test_reject_leaves_the_valve_alone(db, monkeypatch):
    scripted_llm(monkeypatch, ISOLATE)
    at = run_agent(open_app())
    keyed(at, "reject_")[0].click().run()
    assert valve() == 100 and "not executed" in texts(at.warning)
    assert not keyed(at, "approve_")


def test_an_executed_action_can_be_reverted(db, monkeypatch):
    scripted_llm(monkeypatch, ISOLATE)
    at = run_agent(open_app())
    keyed(at, "approve_")[0].click().run()
    assert valve() == 0
    keyed(at, "revert_")[0].click().run()
    assert valve() == 100 and "REVERTED" in texts(at.success)


def test_a_missing_or_wrong_operator_token_changes_nothing(db, monkeypatch):
    monkeypatch.setattr(config, "OPERATOR_TOKEN", "s3cret")
    scripted_llm(monkeypatch, ISOLATE)
    at = run_agent(open_app())
    keyed(at, "approve_")[0].click().run()
    assert valve() == 100 and "Invalid operator token" in texts(at.error)
    at.text_input(key="operator_token").set_value("wrong")
    keyed(at, "approve_")[0].click().run()
    assert valve() == 100
    at.text_input(key="operator_token").set_value("s3cret")
    keyed(at, "approve_")[0].click().run()
    assert valve() == 0 and "EXECUTED" in texts(at.success)


def test_an_empty_operator_id_cannot_approve(db, monkeypatch):
    scripted_llm(monkeypatch, ISOLATE)
    at = run_agent(open_app())
    at.text_input(key="operator_id").set_value("  ")
    keyed(at, "approve_")[0].click().run()
    assert valve() == 100 and "operator ID" in texts(at.error)


def test_the_scripted_attacker_cannot_move_anything_and_shows_in_the_trace(db):
    at = open_app()
    at.selectbox(key="scenario").set_value("attack")
    at.radio(key="mode").set_value("Scripted attacker")
    run_agent(at)
    assert valve("V-S4-IN") == 100 and valve() == 100 and not keyed(at, "approve_")
    assert "2 blocked attempt(s)" in texts(at.success)
    trace = at.dataframe[-1].value
    assert "🟥 ATTACKER" in set(trace["Who"]) and "🟦 CODE" in set(trace["Who"])
    assert any("unknown tool" in d for d in trace["Detail"]) and any("approved" in d for d in trace["Detail"])
    decided = at.dataframe[-2].value
    assert set(decided["Status"]) == {"REJECTED_BY_GUARD"}


def test_all_providers_down_still_gives_a_brief_and_stages_nothing(db, monkeypatch):
    def down(messages, tools_):
        raise llm.AllProvidersFailed("everything unreachable")
    monkeypatch.setattr(llm, "chat", down)
    at = run_agent(open_app())
    assert "NEEDS_HUMAN_REVIEW" in texts(at.warning) and not keyed(at, "approve_")
    assert "GENUINE_LEAK" in texts(at.markdown) and "Generated by deterministic code only" in texts(at.markdown)
