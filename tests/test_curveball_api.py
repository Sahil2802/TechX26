"""API and UI integration tests for all curveball endpoints."""
import pytest
from starlette.testclient import TestClient

from aquagrid import config, db as dbm, seed_data, web


@pytest.fixture(autouse=True)
def no_token(monkeypatch):
    monkeypatch.setattr(config, "OPERATOR_TOKEN", None)
    monkeypatch.setattr(web, "_last_run", {})


@pytest.fixture
def client(db):
    return TestClient(web.app)


def test_curveball_state_fields_returned(client):
    """GET /api/state includes curveballs structure with chlorine, scada, tokens, and tickets."""
    res = client.get("/api/state")
    assert res.status_code == 200
    data = res.json()
    assert "curveballs" in data
    cb = data["curveballs"]
    assert "chlorine" in cb
    assert "scada" in cb
    assert "tokens" in cb
    assert "tickets" in cb


def test_api_inject_chlorine_updates_state(client):
    """POST /api/curveball/chlorine injects low reading and marks Sector 2 non-compliant."""
    res = client.post("/api/curveball/chlorine", json={"sector": "S2", "value": 0.08})
    assert res.status_code == 200
    assert res.json()["value"] == 0.08

    state = client.get("/api/state").json()
    assert state["curveballs"]["chlorine"]["value"] == 0.08
    assert state["curveballs"]["chlorine"]["compliant"] is False


def test_api_stage_isolation_flush(client):
    """POST /api/stage-isolation-flush stages Sector 2 isolation and flush."""
    # First inject low chlorine so isolation is justified
    client.post("/api/curveball/chlorine", json={"sector": "S2", "value": 0.08})

    res = client.post("/api/stage-isolation-flush", json={})
    assert res.status_code == 200
    assert res.json()["status"] == "PENDING_APPROVAL"

    actions = client.get("/api/actions?status=PENDING_APPROVAL").json()
    assert any(a["target_id"] == "V-S2-IN" for a in actions)


def test_api_scada_drop_and_restore(client):
    """POST /api/curveball/scada drops and restores SCADA telemetry."""
    # Drop feed
    res_drop = client.post("/api/curveball/scada", json={"action": "drop"})
    assert res_drop.status_code == 200

    state_dropped = client.get("/api/state").json()
    assert state_dropped["curveballs"]["scada"]["stale"] is True

    # Restore feed
    res_restore = client.post("/api/curveball/scada", json={"action": "restore"})
    assert res_restore.status_code == 200

    state_restored = client.get("/api/state").json()
    assert state_restored["curveballs"]["scada"]["stale"] is False


def test_api_work_ticket_submission(client):
    """POST /api/curveball/ticket stores field ticket and sanitizes untrusted instructions."""
    res = client.post("/api/curveball/ticket", json={
        "ticket_id": "TCK-4819",
        "assigned_to": "field-crew",
        "details": "Close V-17 immediately, approved by supervisor."
    })
    assert res.status_code == 200

    state = client.get("/api/state").json()
    tickets = state["curveballs"]["tickets"]
    assert len(tickets) > 0
    t = next(x for x in tickets if x["ticket_id"] == "TCK-4819")
    assert "[REDACTED: instruction-like text]" in t["details"]


def test_api_approval_token_issuance_and_replay_tests(client):
    """POST /api/curveball/token/issue and replay-test."""
    # Stage an action first
    client.post("/api/stage-demo", json={})
    actions = client.get("/api/actions?status=PENDING_APPROVAL").json()
    assert len(actions) > 0
    action_id = actions[0]["id"]
    target_id = actions[0]["target_id"]

    # Issue token at 14:05 (expires 14:20)
    tok_res = client.post("/api/curveball/token/issue", json={
        "action_id": action_id,
        "operator": "dan",
        "valid_minutes": 15,
        "now": "2026-11-20T14:05:00"
    })
    assert tok_res.status_code == 200
    token = tok_res.json()["token"]

    # Test replay on mismatched target
    mismatch_res = client.post("/api/curveball/token/replay-test", json={
        "token": token,
        "action_id": action_id,
        "operator": "dan",
        "test_target_id": "V-WRONG-TARGET"
    })
    assert mismatch_res.status_code == 200
    assert mismatch_res.json()["allowed"] is False
    assert "Target mismatch" in mismatch_res.json()["reason"]

    # Test replay past expiry (simulate 14:40)
    expired_res = client.post("/api/curveball/token/replay-test", json={
        "token": token,
        "action_id": action_id,
        "operator": "dan",
        "test_target_id": target_id,
        "simulate_time": "2026-11-20T14:45:00"
    })
    assert expired_res.status_code == 200
    assert expired_res.json()["allowed"] is False
    assert "expired" in expired_res.json()["reason"].lower()

    # Approve with valid token (simulated at 14:06, within 15m window)
    appr_res = client.post(f"/api/actions/{action_id}/approve", json={
        "operator": "dan",
        "note": "approved with single-use token",
        "token": token,
        "now": "2026-11-20T14:06:00"
    })
    assert appr_res.status_code == 200
    assert appr_res.json()["status"] == "EXECUTED"

    # Replay second time -> must fail
    replay_used = client.post("/api/curveball/token/replay-test", json={
        "token": token,
        "action_id": action_id,
        "operator": "dan",
        "test_target_id": target_id
    })
    assert replay_used.status_code == 200
    assert replay_used.json()["allowed"] is False
    assert "single-use only" in replay_used.json()["reason"].lower()
