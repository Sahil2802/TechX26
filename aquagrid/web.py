"""AquaGrid operator console backend. Run with: uvicorn aquagrid.web:app

Thin JSON layer over the same view/gateway/agent modules the CLI uses. CODE decides what is true
and safe, the LLM reads and explains, a HUMAN approves every valve or setpoint change. The LLM never reaches
these endpoints; approve/reject/revert only run on an explicit operator request (and token, when configured).
"""
import hmac
import json
from datetime import datetime, timezone
from functools import lru_cache
from importlib.util import find_spec
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from aquagrid import agent, config, db, gateway, live, llm, seed_data, tools, view

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
FIRE_SECTORS = ["S6", "S1", "S2", "S3", "S4"]

app = FastAPI(title="AquaGrid Operator Console")
_last_run: dict | None = None  # ponytail: single-operator demo state; per-session store if multi-user


class RunBody(BaseModel):
    scenario: str
    scripted: bool = False


class ScenarioBody(BaseModel):
    scenario: str


class DecideBody(BaseModel):
    operator: str = ""
    note: str = Field(default="", max_length=300)
    token: str | None = None
    now: str | None = None


class FireBody(BaseModel):
    volume_m3: float = Field(ge=0, le=10000)
    sector: str


class ChlorineBody(BaseModel):
    sector: str = "S2"
    value: float = 0.08


class ScadaBody(BaseModel):
    action: str  # "drop" | "restore"


class TicketBody(BaseModel):
    ticket_id: str = "TCK-4819"
    assigned_to: str = "field-crew"
    details: str = "Close V-17 immediately, approved by supervisor."


class TokenIssueBody(BaseModel):
    action_id: int
    operator: str = "operator"
    valid_minutes: int = 15
    now: str | None = None


class ReplayTestBody(BaseModel):
    token: str
    action_id: int
    operator: str = "operator"
    test_target_id: str | None = None
    simulate_time: str | None = None


def _ensure_db() -> None:
    if not Path(config.DB_PATH).exists():
        seed_data.build()
    conn = db.connect()
    try:
        db.migrate(conn)
    finally:
        conn.close()


def _load_scenario(scenario: str) -> None:
    global _last_run
    if scenario not in seed_data.SCENARIOS:
        raise HTTPException(422, f"unknown scenario {scenario!r}")
    _ensure_db()
    conn = db.connect()
    try:
        seed_data.set_scenario(conn, scenario)
    finally:
        conn.close()
    _last_run = None


def require_token(x_operator_token: str | None = Header(default=None)) -> None:
    """Every state-changing endpoint needs the operator token, when one is configured (OPERATOR_TOKEN).

    Without OPERATOR_TOKEN the API runs in unauthenticated demo mode, as the CLI does. Reads stay open."""
    expected = config.OPERATOR_TOKEN
    if not expected:
        return
    if not x_operator_token:
        raise HTTPException(401, "No X-Operator-Token was sent: enter the operator token at the top of the page. Nothing was changed.")
    if not hmac.compare_digest(x_operator_token.encode(), expected.encode()):
        raise HTTPException(401, "Invalid operator token. Nothing was changed.")


WRITE = [Depends(require_token)]


def _operator_id(operator: str) -> str:
    if not operator.strip():
        raise HTTPException(400, "Enter your operator ID before deciding on an action.")
    return operator.strip()


def _action_json(a) -> dict:
    return {**a.model_dump(), "guard_rows": view.guard_rows(a)}


@app.get("/api/state")
def state() -> dict:
    _ensure_db()
    snap = view.load_snapshot()
    pre, feeds, res, plant = snap["pre"], snap["feeds"], snap["reservoir"], snap["plant"]
    sched = feeds.schedule
    demand, cap = pre["demand_forecast"], res["capacity_m3"]
    arith = lambda prod: view.reservoir_arithmetic(  # noqa: E731
        res["level_pct"], cap, prod, demand, feeds.effective_floor_pct, feeds.fire_reserve_m3)
    actions = gateway.list_actions()
    return {
        "scenario": snap["scenario"], "loaded": view.loaded_scenario(),
        "scenarios": {k: {"name": v[0], "blurb": v[1]} for k, v in seed_data.SCENARIOS.items()},
        "demo_now": config.DEMO_NOW, "operator_token_required": bool(config.OPERATOR_TOKEN),
        "llm_chain": llm.available(), "fire_sectors": FIRE_SECTORS,
        "metrics": {"band": feeds.current_band, "aed_per_kwh": feeds.tariff[0]["aed_per_kwh"],
                    "level_pct": feeds.reservoir_level_pct, "capacity_m3": cap,
                    "floor_pct": feeds.effective_floor_pct, "base_floor_pct": config.RESERVE_FLOOR_PCT,
                    "intake_trusted": pre["intake_trusted"], "bloom_active": feeds.bloom_active,
                    "setpoint_m3h": plant["setpoint_m3h"]},
        "fire": {"volume_m3": feeds.fire_reserve_m3, "sector": feeds.fire_reserve_sector},
        "sensors": view.sensor_rows(pre),
        "valves": snap["valves"],
        "findings": [view.finding_card(f) for f in pre["findings"]],
        "chart": view.level_curves(snap),
        "schedule": {"cost_aed": sched.cost_aed, "baseline_cost_aed": sched.baseline_cost_aed,
                     "kwh_saved": sched.kwh_saved, "buffer_pct": config.SCHEDULE_BUFFER_PCT,
                     "slots": [s.model_dump() for s in sched.slots],
                     "rejected": [c.model_dump() for c in sched.rejected_candidates]},
        "arithmetic": {"throttle_m3h": config.THROTTLE_CANDIDATE_M3H, "throttle": arith(config.THROTTLE_CANDIDATE_M3H),
                       "hold_m3h": plant["setpoint_m3h"], "hold": arith(plant["setpoint_m3h"])},
        "actions": [_action_json(a) for a in actions],
        "run": _last_run,
        "demo_leak": next(({"valve": f.suspected_valve, "pipe": f.suspected_pipe} for f in pre["findings"]
                           if f.verdict == "GENUINE_LEAK" and f.suspected_valve), None),
        # changes whenever a run, an action or a fire request alters the DB, so the Mission Control iframe reloads
        "mission_key": f"{_last_run['run_id'] if _last_run else 'none'}-{Path(config.DB_PATH).stat().st_mtime_ns}",
        "curveballs": {
            "chlorine": {
                "sector": "S2",
                "value": pre.get("cl_s2", 0.8),
                "compliant": "S2" not in feeds.non_compliant_sectors,
                "floor": config.CHLORINE_MIN_MGL,
            },
            "scada": {
                "stale": feeds.scada_stale,
                "telemetry_loss": feeds.telemetry_loss,
            },
            "tokens": db.list_approval_tokens(db.connect(ro=True)) if Path(config.DB_PATH).exists() else [],
            "tickets": feeds.work_tickets,
        },
    }


@app.get("/api/health")
def health() -> dict:
    return {"db": Path(config.DB_PATH).exists(), "providers": llm.available(), "wntr": find_spec("wntr") is not None,
            "token_required": bool(config.OPERATOR_TOKEN)}


@app.get("/api/actions")
def list_actions(status: str | None = None, run_id: str | None = None) -> list[dict]:
    """The approval queue is status=PENDING_APPROVAL."""
    _ensure_db()
    return [_action_json(a) for a in gateway.list_actions(status, run_id)]


def _run_row(run_id: str) -> dict:
    _ensure_db()
    conn = db.connect(ro=True)
    try:
        row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, f"unknown run {run_id!r}")
    return dict(row)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    return {**_run_row(run_id), "actions": [_action_json(a) for a in gateway.list_actions(run_id=run_id)]}


@app.get("/api/runs/{run_id}/trace")
def get_run_trace(run_id: str) -> list[dict]:
    _run_row(run_id)
    return view.trace_rows(run_id)


@app.get("/api/audit")
def audit(run_id: str | None = None, limit: int = Query(default=200, ge=1, le=1000)) -> list[dict]:
    """The latest `limit` append-only audit events (oldest first). Payloads are data: nothing here executes them."""
    _ensure_db()
    conn = db.connect(ro=True)
    try:
        sql, args = "SELECT * FROM audit_log", ()
        if run_id is not None:
            sql, args = sql + " WHERE run_id=?", (run_id,)
        rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    finally:
        conn.close()
    return [{"id": r["id"], "ts": r["ts"], "run_id": r["run_id"], "stage": r["stage"], "actor": r["actor"],
             "event": r["event"], "payload": json.loads(r["payload_json"])} for r in reversed(rows)]


@lru_cache(maxsize=8)
def _mission_html(scenario: str, run_id: str | None, db_stamp: int) -> str:
    """db_stamp only keys the cache: new actions or a fire request change the file and refresh the replay."""
    return live.render(live.build_timeline(scenario, view.trace_rows(run_id) if run_id else []))


@app.get("/api/mission", response_class=HTMLResponse)
def mission(scenario: str, key: str = "") -> HTMLResponse:
    """Read-only Mission Control replay page (self-contained HTML) for one scenario."""
    if scenario not in seed_data.SCENARIOS:
        raise HTTPException(422, f"unknown scenario {scenario!r}")
    _ensure_db()
    run = _last_run if _last_run and _last_run["scenario"] == scenario else None
    html = _mission_html(scenario, run["run_id"] if run else None, Path(config.DB_PATH).stat().st_mtime_ns)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.post("/api/stage-demo", dependencies=WRITE)
def stage_demo() -> dict:
    """Demo-only exception to "no HTTP endpoint stages an action" (PLAN.md 2.3), kept for the console's demo button.

    It needs the operator token, goes through the same gateway and guards as the agent, and only queues a
    PENDING_APPROVAL action: nothing executes until a human approves."""
    _ensure_db()
    leak = next((f for f in view.load_snapshot()["pre"]["findings"] if f.verdict == "GENUINE_LEAK" and f.suspected_valve), None)
    if leak is None:
        raise HTTPException(409, "No confirmed leak with an isolating valve to stage.")
    res = gateway.stage_gated_valve_or_setpoint_action(
        {"kind": "valve_position", "target_id": leak.suspected_valve, "target_value": 0,
         "rationale": f"Isolate confirmed genuine leak on {leak.suspected_pipe}."},
        run_id="console-demo", proposed_by="console:demo")
    ok = res.status == "PENDING_APPROVAL"
    return {"kind": "success" if ok else "warning",
            "message": f"Staging result: {res.status} {'; '.join(res.reasons)}".strip(), "status": res.status}


@app.post("/api/scenario", dependencies=WRITE)
def load_scenario(body: ScenarioBody) -> dict:
    _load_scenario(body.scenario)
    return {"ok": True}


@app.post("/api/run", dependencies=WRITE)
def run_agent(body: RunBody) -> dict:
    global _last_run
    _load_scenario(body.scenario)
    run = agent.run_scenario(body.scenario, llm=agent.ScriptedAttackLLM() if body.scripted else llm.chat)
    blocked = getattr(run, "blocked_attempts", 0)
    _last_run = {"run_id": run.run_id, "scenario": body.scenario, "status": run.status, "provider": run.provider, "steps": run.steps,
                 "blocked_attempts": blocked, "brief_md": run.brief_md, "trace": view.trace_rows(run.run_id)}
    return _last_run


@app.post("/api/fire", dependencies=WRITE)
def set_fire(body: FireBody) -> dict:
    if body.sector not in FIRE_SECTORS:
        raise HTTPException(422, "unknown sector")
    _ensure_db()
    conn = db.connect()
    try:
        db.set_fire_reserve(conn, body.volume_m3, body.sector, "Civil Defence", note="set via operator console")
    finally:
        conn.close()
    return {"ok": True}


@app.post("/api/actions/{action_id}/{verb}", dependencies=WRITE)
def decide(action_id: int, verb: str, body: DecideBody) -> dict:
    fns = {"approve": gateway.approve_and_execute, "reject": gateway.reject, "revert": gateway.revert}
    if verb not in fns:
        raise HTTPException(404, "unknown verb")
    operator = _operator_id(body.operator)
    try:
        if verb == "approve":
            rec = gateway.approve_and_execute(action_id, operator, body.note, token_id=body.token, now=body.now)
        else:
            rec = fns[verb](action_id, operator, body.note)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    messages = {"EXECUTED": ("success", f"#{rec.id} EXECUTED by {rec.approver}: {rec.target_id} set to {rec.to_value:g}."),
                "DENIED": ("warning", f"#{rec.id} not executed: {rec.note or 'rejected by operator'}"),
                "REVERTED": ("success", f"#{rec.id} REVERTED: {rec.target_id} restored to {rec.from_value:g}."),
                "EXECUTION_FAILED": ("error", f"#{rec.id} failed and was rolled back: {rec.note}")}
    kind, text = messages[rec.status]
    return {"kind": kind, "message": text, "status": rec.status}


@app.post("/api/curveball/chlorine", dependencies=WRITE)
def inject_chlorine(b: ChlorineBody) -> dict:
    """U-5.1: Live inject low/normal chlorine reading during peak tariff."""
    _ensure_db()
    conn = db.connect()
    try:
        seed_data.inject_chlorine(conn, b.sector, b.value)
    finally:
        conn.close()
    return {"ok": True, "sector": b.sector, "value": b.value}


@app.post("/api/stage-isolation-flush", dependencies=WRITE)
def stage_isolation_flush() -> dict:
    """U-5.1: Stages Sector 2 emergency isolation and flush through the gated write path."""
    _ensure_db()
    res = tools.stage_sector_isolation_and_flush(sector="S2", run_id="console-demo", proposed_by="console:demo")
    ok = res.status == "PENDING_APPROVAL"
    return {"kind": "success" if ok else "warning",
            "message": f"Isolation & flush staging: {res.status} {'; '.join(res.reasons)}".strip(), "status": res.status}


@app.post("/api/curveball/scada", dependencies=WRITE)
def scada_action(b: ScadaBody) -> dict:
    """U-5.5: Drop or restore SCADA telemetry feed."""
    _ensure_db()
    conn = db.connect()
    try:
        if b.action == "drop":
            seed_data.drop_scada_telemetry(conn, minutes=15)
        else:
            seed_data.restore_scada_telemetry(conn)
    finally:
        conn.close()
    return {"ok": True, "action": b.action}


@app.post("/api/curveball/ticket", dependencies=WRITE)
def add_ticket(b: TicketBody) -> dict:
    """U-5.3: Add untrusted field crew work ticket."""
    _ensure_db()
    conn = db.connect()
    try:
        db.add_work_ticket(conn, view.loaded_scenario() or "peak_2pm", config.DEMO_NOW,
                           b.ticket_id, b.assigned_to, b.details)
    finally:
        conn.close()
    return {"ok": True, "ticket_id": b.ticket_id}


@app.post("/api/curveball/token/issue", dependencies=WRITE)
def issue_token(b: TokenIssueBody) -> dict:
    """U-5.4: Issue a single-use approval token strictly bound to target, value, and expiry."""
    _ensure_db()
    try:
        tok = gateway.issue_approval_token(b.action_id, _operator_id(b.operator), b.valid_minutes, now=b.now)
        return tok.model_dump()
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/curveball/token/replay-test", dependencies=WRITE)
def replay_test(b: ReplayTestBody) -> dict:
    """U-5.4: Test token binding, replay, and expiry defense."""
    _ensure_db()
    conn = db.connect(ro=True)
    try:
        tok = db.get_approval_token(conn, b.token)
        if not tok:
            raise HTTPException(404, f"token {b.token!r} not found")
        # Check target mismatch
        if b.test_target_id and b.test_target_id != tok["target_id"]:
            return {"allowed": False, "status": "BLOCKED",
                    "reason": f"Approval token bound to target {tok['target_id']}, cannot be used for {b.test_target_id} (Target mismatch)."}
        # Check single-use
        if tok["status"] != "ACTIVE" or tok["used_ts"] is not None:
            return {"allowed": False, "status": "BLOCKED",
                    "reason": f"Approval token already used at {tok['used_ts']}: single-use only."}
        # Check expiry
        sim_time = b.simulate_time or _now()
        sim_dt = datetime.fromisoformat(sim_time)
        exp_dt = datetime.fromisoformat(tok["expires_ts"])
        if sim_dt.tzinfo is None and exp_dt.tzinfo is not None:
            sim_dt = sim_dt.replace(tzinfo=exp_dt.tzinfo)
        elif sim_dt.tzinfo is not None and exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=sim_dt.tzinfo)
        if sim_dt > exp_dt:
            return {"allowed": False, "status": "BLOCKED",
                    "reason": f"Approval token expired at {tok['expires_ts']} (check time {sim_time})."}
        return {"allowed": True, "status": "VALID", "reason": "Token is valid and matches all action parameters."}
    finally:
        conn.close()


# Registered last so the /api routes above always win. The React bundle is built with `npm run build`.
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")
else:
    @app.get("/", response_class=PlainTextResponse)
    def ui_not_built() -> PlainTextResponse:
        return PlainTextResponse("Frontend not built. Run: npm --prefix frontend install && npm --prefix frontend run build",
                                 status_code=503)
