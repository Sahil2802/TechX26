"""Plant-specific story (sector, hospital, sensor, valves, ids) comes from the profile, not literals.

Plant B's real profile is still a copy of A, so these tests swap in a variant for B."""
import dataclasses

import pytest
from fastapi.testclient import TestClient

from aquagrid import agent, config, db, guards, profiles, seed_data, tools, web

VARIANT = dict(leak_sector="S3", loop_sector="S2", critical_node="J09", chlorine_sensor="CL-S2",
               fire_sectors=("S6", "S1"), intake_valve="V-S2-IN", plant_id="PLANT-B")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(config, "OPERATOR_TOKEN", None)
    monkeypatch.setattr(config, "ADMIN_TOKEN", "adm")
    monkeypatch.setattr(web, "_last_run", {})
    yield
    db.set_plant("A")


@pytest.fixture
def plant_b(db, monkeypatch):  # `db` fixture seeds A into tmp_path
    monkeypatch.setitem(profiles.PROFILES, "B", dataclasses.replace(profiles.PROFILES["B"], **VARIANT))
    seed_data.build("B")
    yield


def _rows(sql: str, plant: str) -> list[tuple]:
    db.set_plant(plant)
    conn = db.connect(ro=True)
    try:
        return [tuple(r) for r in conn.execute(sql)]
    finally:
        conn.close()


def test_variant_seed_builds_the_loop_and_critical_node_for_its_sector(plant_b):
    assert _rows("SELECT id FROM junctions WHERE critical=1", "B") == [("J09",)]
    valves = {r[0] for r in _rows("SELECT id FROM valves", "B")}
    assert {"V-S2-IN", "V-S2-08-10", "V-S2-06-10", "V-S2-07-08"} <= valves
    assert not any(v.startswith("V-S4-") and v != "V-S4-IN" for v in valves)
    pipes = {r[0] for r in _rows("SELECT id FROM pipes", "B")}
    assert {"P-J08-J10", "P-J06-J10"} <= pipes and "P-J18-J20" not in pipes
    sensors = {r[0]: r[1] for r in _rows("SELECT id, location FROM sensors", "B")}
    assert sensors["CL-S2"] == "J09" and "CL-S4" not in sensors
    assert _rows("SELECT id FROM plant", "B") == [("PLANT-B",)]
    tele = {r[0] for r in _rows("SELECT DISTINCT sensor_id FROM telemetry", "B")}
    assert "CL-S2" in tele and "CL-S4" not in tele


def test_variant_leak_and_chlorine_key_off_the_profile(plant_b):
    db.set_plant("B")
    conn = db.connect(ro=True)
    try:
        a = tools.analyze(conn, now=config.DEMO_NOW, scenario="peak_2pm")
    finally:
        conn.close()
    verdicts = {f.sector: f.verdict for f in a["findings"]}
    assert verdicts["S3"] == "GENUINE_LEAK" and verdicts["S4"] == "NONE"
    assert a["cl_s4"] is not None  # read from CL-S2


def test_variant_guard_critical_node_and_agent_surfaces(plant_b):
    db.set_plant("B")
    pipes = [{"id": "P-S2-IN", "from_node": "R1", "to_node": "J06"}, {"id": "P", "from_node": "J06", "to_node": "J09"}]
    assert guards.connectivity_ok(pipes, {"P-S2-IN"}) == (False, ["J09"])
    assert [s["function"]["parameters"]["properties"]["sector"]["enum"]
            for s in agent.tool_specs()[:1]] == [list(profiles.get().sectors)]
    attacks = agent.ScriptedAttackLLM().ATTACKS
    assert attacks[0][1]["valve"] == "V-S2-IN" and attacks[3][1]["target_id"] == "PLANT-B"
    with pytest.raises(ValueError):
        tools.read_network_telemetry(sector="S9")


def test_variant_fire_sectors_in_state_and_validation(plant_b):
    client = TestClient(web.app)
    h = {"X-Operator-Token": "adm", "X-Plant": "B"}
    assert client.get("/api/state", headers=h).json()["fire_sectors"] == ["S6", "S1"]
    r = client.post("/api/fire", headers=h, json={"sector": "S4", "volume_m3": 100})
    assert r.status_code == 422


def test_plant_a_keeps_todays_values(db):
    assert guards.connectivity_ok([{"id": "P-S4-IN", "from_node": "R1", "to_node": "J19"}], {"P-S4-IN"}) == (False, ["J19"])
    assert agent.ScriptedAttackLLM().ATTACKS[0][1]["valve"] == "V-S4-IN"
    assert profiles.get("A").fire_sectors == ("S6", "S1", "S2", "S3", "S4")
    assert _rows("SELECT id FROM plant", "A") == [("PLANT-1",)]
    assert {"V-S4-17-18", "V-S4-18-20", "V-S4-16-20"} <= {r[0] for r in _rows("SELECT id FROM valves", "A")}
    client = TestClient(web.app)
    assert client.get("/api/state", headers={"X-Operator-Token": "adm"}).json()["fire_sectors"] == [
        "S6", "S1", "S2", "S3", "S4"]
