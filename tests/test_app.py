"""The operator console backend, driven through its HTTP API the way the web page uses it."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aquagrid import config, db as dbm, llm, web

THROTTLE = json.dumps({"kind": "plant_setpoint", "target_id": "PLANT-1", "target_value": 500, "rationale": "peak tariff"})
ISOLATE = json.dumps({"kind": "valve_position", "target_id": "V-S4-17-18", "target_value": 0, "rationale": "isolate leak"})


@pytest.fixture(autouse=True)
def no_token(monkeypatch):
    monkeypatch.setattr(config, "OPERATOR_TOKEN", None)
    monkeypatch.setattr(web, "_last_run", None)


@pytest.fixture
def client(db):
    return TestClient(web.app)


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


def run_agent(client, scenario="peak_2pm", scripted=False):
    r = client.post("/api/run", json={"scenario": scenario, "scripted": scripted})
    assert r.status_code == 200, r.text
    return client.get("/api/state").json()


def valve(name="V-S4-17-18"):
    return dbm.connect(ro=True).execute("SELECT position_pct FROM valves WHERE id=?", (name,)).fetchone()[0]


def pending(state):
    return [a for a in state["actions"] if a["status"] == "PENDING_APPROVAL"]


def decide(client, action_id, verb, operator="alice", token=None, note=""):
    headers = {} if token is None else {"X-Operator-Token": token}
    return client.post(f"/api/actions/{action_id}/{verb}", json={"operator": operator, "note": note}, headers=headers)


@pytest.mark.skipif(not web.DIST.is_dir(), reason="frontend not built (npm --prefix frontend run build)")
def test_react_bundle_is_served_and_api_routes_still_win(client):
    page = client.get("/")
    assert page.status_code == 200 and '<div id="root">' in page.text and "AquaGrid Operator Console" in page.text
    assert client.get("/api/state").headers["content-type"].startswith("application/json")


def test_peak_scenario_shows_the_veto_the_quarantine_and_the_leak(client):
    s = client.get("/api/state").json()
    assert s["metrics"]["floor_pct"] == 40 and s["metrics"]["intake_trusted"] is False
    assert any("29.0% < 40.0% floor" in c["reason"] for c in s["schedule"]["rejected"])
    assert any("GENUINE LEAK at J17 on P-J17-J18" in f["headline"] for f in s["findings"])
    assert [r["Sensor"] for r in s["sensors"][:2]] == ["P-J07", "T-INTAKE"]
    assert all(r["Trust"] == "QUARANTINED" for r in s["sensors"][:2])
    veto, hold = s["arithmetic"]["throttle"]["lines"], s["arithmetic"]["hold"]["lines"]
    assert "11,600 m3 = 29.0%" in " ".join(veto) and "VETO" in " ".join(veto)
    assert "17,200 m3 = 43.0%" in " ".join(hold) and "PASS" in " ".join(hold)
    assert all("deterministic code" in f["decided_by"] for f in s["findings"])


@pytest.mark.parametrize("scenario", ["baseline", "s4_drift", "attack"])
def test_every_scenario_loads_and_renders(client, scenario):
    assert client.post("/api/scenario", json={"scenario": scenario}).status_code == 200
    s = client.get("/api/state").json()
    assert s["loaded"] == scenario and "floor_pct" in s["metrics"]


def test_unknown_scenario_is_rejected(client):
    assert client.post("/api/scenario", json={"scenario": "nope"}).status_code == 422


def test_baseline_has_no_quarantine_and_a_35_percent_floor(client):
    client.post("/api/scenario", json={"scenario": "baseline"})
    s = client.get("/api/state").json()
    assert s["metrics"]["floor_pct"] == 35 and {r["Trust"] for r in s["sensors"]} == {"trusted"}
    assert all(f["tone"] == "ok" for f in s["findings"])


def test_nothing_moves_until_a_human_approves_then_approve_executes(client, monkeypatch):
    scripted_llm(monkeypatch, THROTTLE, ISOLATE)
    s = run_agent(client)
    assert valve() == 100 and len(pending(s)) == 1  # the throttle was vetoed, the isolation is waiting
    assert s["run"]["provider"] == "fake/model"
    r = decide(client, pending(s)[0]["id"], "approve")
    assert r.status_code == 200 and "EXECUTED by alice" in r.json()["message"]
    assert valve() == 0 and not pending(client.get("/api/state").json())


def test_reject_leaves_the_valve_alone(client, monkeypatch):
    scripted_llm(monkeypatch, ISOLATE)
    s = run_agent(client)
    r = decide(client, pending(s)[0]["id"], "reject")
    assert valve() == 100 and r.json()["kind"] == "warning" and not pending(client.get("/api/state").json())


def test_an_executed_action_can_be_reverted(client, monkeypatch):
    scripted_llm(monkeypatch, ISOLATE)
    aid = pending(run_agent(client))[0]["id"]
    decide(client, aid, "approve")
    assert valve() == 0
    assert "REVERTED" in decide(client, aid, "revert").json()["message"] and valve() == 100


def test_a_missing_or_wrong_operator_token_changes_nothing(client, monkeypatch):
    scripted_llm(monkeypatch, ISOLATE)
    aid = pending(run_agent(client))[0]["id"]  # staged before the token is switched on
    monkeypatch.setattr(config, "OPERATOR_TOKEN", "s3cret")
    assert decide(client, aid, "approve").status_code == 401 and valve() == 100
    assert decide(client, aid, "approve", token="wrong").status_code == 401 and valve() == 100
    assert decide(client, aid, "approve", token="").status_code == 401 and valve() == 100
    assert decide(client, aid, "approve", token="s3cret").status_code == 200 and valve() == 0


def test_an_empty_operator_id_cannot_approve(client, monkeypatch):
    scripted_llm(monkeypatch, ISOLATE)
    aid = pending(run_agent(client))[0]["id"]
    r = decide(client, aid, "approve", operator="  ")
    assert r.status_code == 400 and "operator ID" in r.json()["detail"] and valve() == 100


def test_deciding_an_unknown_or_already_decided_action_is_a_clean_error(client, monkeypatch):
    scripted_llm(monkeypatch, ISOLATE)
    aid = pending(run_agent(client))[0]["id"]
    decide(client, aid, "reject")
    assert decide(client, aid, "approve").status_code == 409
    assert decide(client, 9999, "approve").status_code == 409
    assert decide(client, aid, "delete").status_code == 404


def test_the_scripted_attacker_cannot_move_anything_and_shows_in_the_trace(client):
    s = run_agent(client, "attack", scripted=True)
    assert valve("V-S4-IN") == 100 and valve() == 100 and not pending(s)
    assert s["run"]["blocked_attempts"] == 2
    whos = {r["Who"] for r in s["run"]["trace"]}
    assert {"ATTACKER", "CODE"} <= whos
    assert any("unknown tool" in r["Detail"] for r in s["run"]["trace"])
    assert {a["status"] for a in s["actions"]} == {"REJECTED_BY_GUARD"}


def test_all_providers_down_still_gives_a_brief_and_stages_nothing(client, monkeypatch):
    def down(messages, tools_):
        raise llm.AllProvidersFailed("everything unreachable")
    monkeypatch.setattr(llm, "chat", down)
    s = run_agent(client)
    assert s["run"]["status"] == "NEEDS_HUMAN_REVIEW" and not pending(s)
    assert "GENUINE_LEAK" in s["run"]["brief_md"] and "Generated by deterministic code only" in s["run"]["brief_md"]


def test_fire_reserve_raises_the_floor_and_validates_input(client):
    base = client.get("/api/state").json()["metrics"]["floor_pct"]
    assert client.post("/api/fire", json={"volume_m3": 2000, "sector": "S6"}).status_code == 200
    s = client.get("/api/state").json()
    assert s["fire"] == {"volume_m3": 2000, "sector": "S6"} and s["metrics"]["floor_pct"] > base
    assert client.post("/api/fire", json={"volume_m3": -1, "sector": "S6"}).status_code == 422
    assert client.post("/api/fire", json={"volume_m3": 10, "sector": "Z9"}).status_code == 422


def test_mission_control_page_is_a_self_contained_replay(client):
    r = client.get("/api/mission", params={"scenario": "peak_2pm"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "MISSION CONTROL" in r.text and 'id="data"' in r.text and "/*DATA*/" not in r.text
    assert client.get("/api/mission", params={"scenario": "nope"}).status_code == 422


def test_mission_control_reloads_after_a_run_and_carries_its_trace(client):
    before = client.get("/api/state").json()["mission_key"]
    s = run_agent(client, "attack", scripted=True)
    assert s["mission_key"] != before
    page = client.get("/api/mission", params={"scenario": "attack"}).text
    assert s["run"]["run_id"] in page or "ATTACKER" in page


def test_stage_demo_queues_a_pending_action_and_moves_nothing(client):
    s = client.get("/api/state").json()
    assert s["demo_leak"]["valve"] == "V-S4-17-18"
    r = client.post("/api/stage-demo")
    assert r.status_code == 200 and r.json()["status"] == "PENDING_APPROVAL"
    after = client.get("/api/state").json()
    assert valve() == 100 and [a["proposed_by"] for a in pending(after)] == ["console:demo"]
    assert decide(client, pending(after)[0]["id"], "approve").status_code == 200 and valve() == 0


def test_stage_demo_needs_a_confirmed_leak(client):
    client.post("/api/scenario", json={"scenario": "baseline"})
    assert client.get("/api/state").json()["demo_leak"] is None
    assert client.post("/api/stage-demo").status_code == 409


WRITES = [("/api/scenario", {"scenario": "baseline"}), ("/api/run", {"scenario": "baseline", "scripted": True}),
          ("/api/fire", {"volume_m3": 1000, "sector": "S6"}), ("/api/stage-demo", None),
          ("/api/actions/1/approve", {"operator": "alice"})]


def snapshot(client):
    s = client.get("/api/state").json()
    return {k: s[k] for k in ("loaded", "fire", "valves", "actions", "run")}


@pytest.mark.parametrize("path,body", WRITES)
def test_every_write_endpoint_needs_the_operator_token(client, monkeypatch, path, body):
    monkeypatch.setattr(config, "OPERATOR_TOKEN", "s3cret")
    before = snapshot(client)
    assert client.post(path, json=body).status_code == 401
    assert client.post(path, json=body, headers={"X-Operator-Token": "wrong"}).status_code == 401
    assert snapshot(client) == before  # nothing changed


def test_the_token_in_the_body_or_query_is_not_accepted(client, monkeypatch):
    monkeypatch.setattr(config, "OPERATOR_TOKEN", "s3cret")
    assert client.post("/api/scenario?token=s3cret", json={"scenario": "baseline", "token": "s3cret"}).status_code == 401


def test_write_endpoints_accept_the_header_token_and_reads_stay_open(client, monkeypatch):
    monkeypatch.setattr(config, "OPERATOR_TOKEN", "s3cret")
    assert client.get("/api/state").status_code == 200 and client.get("/api/health").json()["token_required"] is True
    ok = client.post("/api/scenario", json={"scenario": "baseline"}, headers={"X-Operator-Token": "s3cret"})
    assert ok.status_code == 200 and client.get("/api/state").json()["loaded"] == "baseline"


def test_health_reports_db_and_providers(client):
    h = client.get("/api/health").json()
    assert h["db"] is True and isinstance(h["providers"], list) and h["token_required"] is False and "wntr" in h


def test_actions_endpoint_is_the_approval_queue(client, monkeypatch):
    scripted_llm(monkeypatch, ISOLATE)
    s = run_agent(client)
    run_id = s["run"]["run_id"]
    queue = client.get("/api/actions", params={"status": "PENDING_APPROVAL"}).json()
    assert [a["target_id"] for a in queue] == ["V-S4-17-18"] and queue[0]["guard_rows"]
    assert len(client.get("/api/actions", params={"run_id": run_id}).json()) == 1
    assert client.get("/api/actions", params={"run_id": "nope"}).json() == []


def test_run_and_trace_endpoints(client, monkeypatch):
    scripted_llm(monkeypatch, ISOLATE)
    run_id = run_agent(client)["run"]["run_id"]
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["run_id"] == run_id and run["scenario"] == "peak_2pm" and len(run["actions"]) == 1
    trace = client.get(f"/api/runs/{run_id}/trace").json()
    assert trace and {"#", "Who", "Step", "Detail"} <= set(trace[0])
    assert client.get("/api/runs/nope").status_code == 404 and client.get("/api/runs/nope/trace").status_code == 404


def test_audit_endpoint_is_ordered_filtered_and_bounded(client):
    run_id = run_agent(client, "attack", scripted=True)["run"]["run_id"]
    events = client.get("/api/audit", params={"run_id": run_id}).json()
    assert events and {e["run_id"] for e in events} == {run_id}
    assert [e["id"] for e in events] == sorted(e["id"] for e in events) and isinstance(events[0]["payload"], dict)
    assert len(client.get("/api/audit", params={"limit": 2}).json()) == 2
    assert client.get("/api/audit", params={"limit": 0}).status_code == 422
    assert client.get("/api/audit", params={"limit": 5000}).status_code == 422


def test_401_says_whether_the_token_was_missing_or_wrong(client, monkeypatch):
    monkeypatch.setattr(config, "OPERATOR_TOKEN", "s3cret")
    missing = client.post("/api/scenario", json={"scenario": "baseline"})
    wrong = client.post("/api/scenario", json={"scenario": "baseline"}, headers={"X-Operator-Token": "nope"})
    assert missing.status_code == wrong.status_code == 401
    assert "No X-Operator-Token" in missing.json()["detail"] and "Invalid operator token" in wrong.json()["detail"]
    assert "s3cret" not in missing.text + wrong.text
