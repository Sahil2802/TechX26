"""AquaGrid operator console backend. Run with: uvicorn aquagrid.web:app

Thin JSON layer over the same view/gateway/agent modules the CLI uses. CODE decides what is true
and safe, the LLM reads and explains, a HUMAN approves every valve or setpoint change. The LLM never reaches
these endpoints; approve/reject/revert only run on an explicit operator request (and token, when configured).
"""
import hmac
import json
from functools import lru_cache
from importlib.util import find_spec
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from aquagrid import agent, config, db, gateway, live, llm, profiles, seed_data, view

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

app = FastAPI(title="AquaGrid Operator Console")
_last_run: dict[str, dict | None] = {}  # keyed by plant. ponytail: single-operator demo state; per-session store if multi-user


class RunBody(BaseModel):
    scenario: str
    scripted: bool = False


class ScenarioBody(BaseModel):
    scenario: str


class DecideBody(BaseModel):
    operator: str = ""
    note: str = Field(default="", max_length=300)


class FireBody(BaseModel):
    volume_m3: float = Field(ge=0, le=10000)
    sector: str


def _ensure_db() -> None:
    if not Path(db.db_path()).exists():
        seed_data.build(db.current_plant())
    conn = db.connect()
    try:
        db.migrate(conn)
    finally:
        conn.close()


def _load_scenario(scenario: str) -> None:
    if scenario not in profiles.get().scenarios:
        raise HTTPException(422, f"unknown scenario {scenario!r}")
    _ensure_db()
    conn = db.connect()
    try:
        seed_data.set_scenario(conn, scenario)
    finally:
        conn.close()
    _last_run[db.current_plant()] = None


class LoginBody(BaseModel):
    token: str = ""


def _tokens_configured() -> bool:
    return bool(config.ADMIN_TOKEN or config.OPERATOR_TOKEN or config.PLANT_A_TOKEN or config.PLANT_B_TOKEN)


def _resolve(token: str | None) -> tuple[str, list[str]] | None:
    """token -> (role, allowed plant ids), or None if it matches nothing. Open demo mode (no token configured) is admin."""
    if not _tokens_configured():
        return "admin", list(config.PLANTS)
    if not token:
        return None
    given = token.encode()
    for expected, role, plants in ((config.ADMIN_TOKEN or config.OPERATOR_TOKEN, "admin", list(config.PLANTS)),
                                   (config.PLANT_A_TOKEN, "plant", ["A"]), (config.PLANT_B_TOKEN, "plant", ["B"])):
        if expected and hmac.compare_digest(given, expected.encode()):
            return role, plants
    return None


def _plants_json(ids: list[str]) -> list[dict]:
    return [{"id": i, "name": config.PLANTS[i]} for i in ids]


async def plant_context(request: Request, x_operator_token: str | None = Header(default=None), x_plant: str | None = Header(default=None),
                        token: str | None = Query(default=None), plant: str | None = Query(default=None)) -> None:
    """Resolve role and plant for this request, then set the plant ContextVar.

    Must be `async def`: FastAPI runs sync dependencies in a threadpool with a copied context, so a ContextVar
    set there would never reach the sync endpoints. Header wins; the query params exist for the iframe (/api/mission).
    # ponytail: a token in the URL leaks into logs/history (demo-grade); use a short-lived ticket if this ships."""
    if request.url.path != "/api/mission":  # query fallback is for the iframe only; writes stay header-only
        token = plant = None
    chosen = x_plant if x_plant is not None else (plant if plant is not None else "A")
    if chosen not in config.PLANTS:
        raise HTTPException(422, "unknown plant")
    resolved = _resolve(x_operator_token or token)
    if resolved is None:
        tail = " Nothing was changed." if request.method != "GET" else ""
        if not (x_operator_token or token):
            raise HTTPException(401, "No X-Operator-Token was sent: enter the operator token at the top of the page." + tail)
        raise HTTPException(401, "Invalid operator token." + tail)
    if chosen not in resolved[1]:
        raise HTTPException(403, "This token cannot use that plant.")
    db.set_plant(chosen)


api = APIRouter(dependencies=[Depends(plant_context)])


def require_token(x_operator_token: str | None = Header(default=None)) -> None:
    """Every state-changing endpoint needs a token valid for the current plant (header only, never the URL).

    With no token configured at all the API runs in unauthenticated demo mode, as the CLI does."""
    if not _tokens_configured():
        return
    if not x_operator_token:
        raise HTTPException(401, "No X-Operator-Token was sent: enter the operator token at the top of the page. Nothing was changed.")
    resolved = _resolve(x_operator_token)
    if resolved is None:
        raise HTTPException(401, "Invalid operator token. Nothing was changed.")
    if db.current_plant() not in resolved[1]:
        raise HTTPException(403, "This token cannot use that plant. Nothing was changed.")


WRITE = [Depends(require_token)]


def _operator_id(operator: str) -> str:
    if not operator.strip():
        raise HTTPException(400, "Enter your operator ID before deciding on an action.")
    return operator.strip()


def _action_json(a) -> dict:
    return {**a.model_dump(), "guard_rows": view.guard_rows(a)}


@app.post("/api/login")
def login(body: LoginBody) -> dict:
    resolved = _resolve(body.token)
    if resolved is None:
        raise HTTPException(401, "Invalid token.")
    return {"role": resolved[0], "plants": _plants_json(resolved[1])}


@api.get("/api/state")
def state(x_operator_token: str | None = Header(default=None)) -> dict:
    _ensure_db()
    role, allowed = _resolve(x_operator_token) or ("admin", [])
    snap = view.load_snapshot()
    pre, feeds, res, plant = snap["pre"], snap["feeds"], snap["reservoir"], snap["plant"]
    sched = feeds.schedule
    demand, cap = pre["demand_forecast"], res["capacity_m3"]
    arith = lambda prod: view.reservoir_arithmetic(  # noqa: E731
        res["level_pct"], cap, prod, demand, feeds.effective_floor_pct, feeds.fire_reserve_m3)
    actions = gateway.list_actions()
    last = _last_run.get(db.current_plant())
    return {
        "scenario": snap["scenario"], "loaded": view.loaded_scenario(),
        "scenarios": {k: {"name": v[0], "blurb": v[1]} for k, v in profiles.get().scenarios.items()},
        "demo_now": config.DEMO_NOW, "operator_token_required": _tokens_configured(),
        "plant": db.current_plant(), "plants": _plants_json(allowed), "role": role,
        "llm_chain": llm.available(), "fire_sectors": list(profiles.get().fire_sectors),
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
        "run": last,
        "demo_leak": next(({"valve": f.suspected_valve, "pipe": f.suspected_pipe} for f in pre["findings"]
                           if f.verdict == "GENUINE_LEAK" and f.suspected_valve), None),
        # changes whenever a run, an action or a fire request alters the DB, so the Mission Control iframe reloads
        "mission_key": f"{last['run_id'] if last else 'none'}-{Path(db.db_path()).stat().st_mtime_ns}",
    }


@app.get("/api/health")
def health() -> dict:
    return {"db": Path(db.db_path()).exists(), "providers": llm.available(), "wntr": find_spec("wntr") is not None,
            "token_required": _tokens_configured()}


@api.get("/api/actions")
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


@api.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    return {**_run_row(run_id), "actions": [_action_json(a) for a in gateway.list_actions(run_id=run_id)]}


@api.get("/api/runs/{run_id}/trace")
def get_run_trace(run_id: str) -> list[dict]:
    _run_row(run_id)
    return view.trace_rows(run_id)


@api.get("/api/audit")
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
def _mission_html(plant: str, scenario: str, run_id: str | None, db_stamp: int) -> str:
    """db_stamp only keys the cache: new actions or a fire request change the file and refresh the replay."""
    return live.render(live.build_timeline(scenario, view.trace_rows(run_id) if run_id else []))


@api.get("/api/mission", response_class=HTMLResponse)
def mission(scenario: str, key: str = "") -> HTMLResponse:
    """Read-only Mission Control replay page (self-contained HTML) for one scenario."""
    if scenario not in profiles.get().scenarios:
        raise HTTPException(422, f"unknown scenario {scenario!r}")
    _ensure_db()
    last = _last_run.get(db.current_plant())
    run = last if last and last["scenario"] == scenario else None
    html = _mission_html(db.current_plant(), scenario, run["run_id"] if run else None, Path(db.db_path()).stat().st_mtime_ns)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@api.post("/api/stage-demo", dependencies=WRITE)
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


@api.post("/api/scenario", dependencies=WRITE)
def load_scenario(body: ScenarioBody) -> dict:
    _load_scenario(body.scenario)
    return {"ok": True}


@api.post("/api/run", dependencies=WRITE)
def run_agent(body: RunBody) -> dict:
    _load_scenario(body.scenario)
    run = agent.run_scenario(body.scenario, llm=agent.ScriptedAttackLLM() if body.scripted else llm.chat)
    blocked = getattr(run, "blocked_attempts", 0)
    _last_run[db.current_plant()] = {"run_id": run.run_id, "scenario": body.scenario, "status": run.status, "provider": run.provider, "steps": run.steps,
                 "blocked_attempts": blocked, "brief_md": run.brief_md, "trace": view.trace_rows(run.run_id)}
    return _last_run[db.current_plant()]


@api.post("/api/fire", dependencies=WRITE)
def set_fire(body: FireBody) -> dict:
    if body.sector not in profiles.get().fire_sectors:
        raise HTTPException(422, "unknown sector")
    _ensure_db()
    conn = db.connect()
    try:
        db.set_fire_reserve(conn, body.volume_m3, body.sector, "Civil Defence", note="set via operator console")
    finally:
        conn.close()
    return {"ok": True}


@api.post("/api/actions/{action_id}/{verb}", dependencies=WRITE)
def decide(action_id: int, verb: str, body: DecideBody) -> dict:
    fns = {"approve": gateway.approve_and_execute, "reject": gateway.reject, "revert": gateway.revert}
    if verb not in fns:
        raise HTTPException(404, "unknown verb")
    operator = _operator_id(body.operator)
    try:
        rec = fns[verb](action_id, operator, body.note)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    messages = {"EXECUTED": ("success", f"#{rec.id} EXECUTED by {rec.approver}: {rec.target_id} set to {rec.to_value:g}."),
                "DENIED": ("warning", f"#{rec.id} not executed: {rec.note or 'rejected by operator'}"),
                "REVERTED": ("success", f"#{rec.id} REVERTED: {rec.target_id} restored to {rec.from_value:g}."),
                "EXECUTION_FAILED": ("error", f"#{rec.id} failed and was rolled back: {rec.note}")}
    kind, text = messages[rec.status]
    return {"kind": kind, "message": text, "status": rec.status}


app.include_router(api)

# Registered last so the /api routes above always win. The React bundle is built with `npm run build`.
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")
else:
    @app.get("/", response_class=PlainTextResponse)
    def ui_not_built() -> PlainTextResponse:
        return PlainTextResponse("Frontend not built. Run: npm --prefix frontend install && npm --prefix frontend run build",
                                 status_code=503)
