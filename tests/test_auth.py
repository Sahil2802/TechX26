"""Token login and per-request plant routing."""
import pytest
from fastapi.testclient import TestClient

from aquagrid import config, db as dbm, gateway, web


@pytest.fixture(autouse=True)
def tokens(monkeypatch):
    monkeypatch.setattr(config, "OPERATOR_TOKEN", None)
    monkeypatch.setattr(config, "ADMIN_TOKEN", "adm")
    monkeypatch.setattr(config, "PLANT_A_TOKEN", "ta")
    monkeypatch.setattr(config, "PLANT_B_TOKEN", "tb")
    monkeypatch.setattr(web, "_last_run", {})


@pytest.fixture
def client(db):
    return TestClient(web.app)


def h(token, plant=None):
    return {"X-Operator-Token": token, **({"X-Plant": plant} if plant is not None else {})}


@pytest.mark.parametrize("token,role,ids", [("adm", "admin", ["A", "B"]), ("ta", "plant", ["A"]), ("tb", "plant", ["B"])])
def test_login_returns_role_and_plants(client, token, role, ids):
    r = client.post("/api/login", json={"token": token})
    assert r.status_code == 200 and r.json()["role"] == role and [p["id"] for p in r.json()["plants"]] == ids


def test_login_bad_token_is_401(client):
    assert client.post("/api/login", json={"token": "nope"}).status_code == 401


def test_state_without_token_is_401(client):
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", headers=h("nope")).status_code == 401


def test_plant_token_cannot_use_other_plant(client):
    assert client.get("/api/state", headers=h("ta", "B")).status_code == 403
    assert client.get("/api/state", headers=h("tb", "A")).status_code == 403


@pytest.mark.parametrize("plant", ["C", "../x", ""])
def test_unknown_plant_is_422(client, plant):
    assert client.get("/api/state", headers=h("adm", plant)).status_code == 422


def test_endpoint_sees_the_plant_the_dependency_set(client):
    s = client.get("/api/state", headers=h("adm", "B")).json()
    assert s["plant"] == "B" and s["role"] == "admin" and [p["id"] for p in s["plants"]] == ["A", "B"]
    assert client.get("/api/state", headers=h("ta")).json()["plant"] == "A"


def test_plants_are_isolated(client):
    client.get("/api/state", headers=h("adm", "B"))
    dbm.set_plant("B")
    try:
        res = gateway.stage_gated_valve_or_setpoint_action(
            {"kind": "valve_position", "target_id": "V-S2-07-08", "target_value": 0, "rationale": "t"},
            run_id="r", proposed_by="test")
    finally:
        dbm.set_plant("A")
    assert res.status == "PENDING_APPROVAL"
    assert len(client.get("/api/state", headers=h("adm", "B")).json()["actions"]) == 1
    assert client.get("/api/state", headers=h("adm", "A")).json()["actions"] == []


def test_write_accepts_plant_token_only_for_its_plant(client):
    body = {"scenario": "baseline"}
    assert client.post("/api/scenario", json=body, headers=h("tb", "B")).status_code == 200
    assert client.post("/api/scenario", json=body, headers=h("tb", "A")).status_code == 403
    assert client.post("/api/scenario", json=body).status_code == 401


def test_mission_accepts_query_token_and_plant(client):
    assert client.get("/api/mission?token=adm&plant=B&scenario=baseline").status_code == 200
    assert client.get("/api/mission?scenario=baseline").status_code == 401


def test_open_demo_mode(client, monkeypatch):
    for k in ("ADMIN_TOKEN", "PLANT_A_TOKEN", "PLANT_B_TOKEN"):
        monkeypatch.setattr(config, k, None)
    assert client.get("/api/state").status_code == 200
    r = client.post("/api/login", json={"token": ""}).json()
    assert r["role"] == "admin" and len(r["plants"]) == 2
