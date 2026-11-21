"""AquaGrid Multi-Agent Orchestrator.

Runs three specialized agents for one scenario and merges their outputs into a
unified operator report.

Execution model
---------------
Phase 1 (parallel):  SensorGuard + GridOptimizer run simultaneously via threads.
                     Each writes its proposals to the shared actions table.
Phase 2 (sequential): SecuritySentinel reads the full audit trail from Phase 1
                       (including any BLOCKED / REJECTED events) and produces an
                       incident report.
Phase 3:              Orchestrator merges all three briefs into one final report
                       and stores it in the runs table as "multi_agent" run.

SQLite concurrency: reads are always opened with mode=ro. Writes (action staging)
use BEGIN IMMEDIATE which serialises the sub-millisecond lock window. No data race
is possible because each agent has its own run_id.
"""
import contextvars
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aquagrid import config, db
from aquagrid.agents import grid_optimizer, security_sentinel, sensor_guard
from aquagrid.schemas import RunResult


# ── result container ──────────────────────────────────────────────────────────

@dataclass
class MultiAgentResult:
    orchestration_id: str
    scenario: str
    started_ts: str
    finished_ts: str | None = None

    # Per-agent RunResult objects
    sensor_guard: RunResult | None = None
    grid_optimizer: RunResult | None = None
    security_sentinel: RunResult | None = None

    # Errors (agent name → message)
    errors: dict[str, str] = field(default_factory=dict)

    # Merged operator brief
    merged_brief_md: str = ""

    # Overall status
    status: str = "PENDING"

    @property
    def all_actions(self) -> list:
        """Combined action queue from all agents."""
        out = []
        for r in [self.sensor_guard, self.grid_optimizer, self.security_sentinel]:
            if r is not None:
                out.extend(r.actions)
        return out

    @property
    def any_blocked(self) -> bool:
        for r in [self.sensor_guard, self.grid_optimizer, self.security_sentinel]:
            if r is not None and r.blocked_attempts > 0:
                return True
        return False


# ── orchestrator ──────────────────────────────────────────────────────────────

def run(scenario_id: str, llm=None) -> MultiAgentResult:
    """
    Run all three agents for *scenario_id* and return a merged MultiAgentResult.

    Parameters
    ----------
    scenario_id : must already be loaded in the DB (call seed_data.set_scenario first)
    llm         : optional LLM callable override for testing (passed to all agents)
    """
    orch_id = uuid.uuid4().hex[:8]
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result  = MultiAgentResult(
        orchestration_id=orch_id,
        scenario=scenario_id,
        started_ts=started,
    )

    # ── Phase 1: SensorGuard + GridOptimizer in parallel ─────────────────────
    _log(orch_id, scenario_id,
         "PHASE_1_START", {"agents": ["SensorGuard", "GridOptimizer"]})

    sg_result:   RunResult | None = None
    go_result:   RunResult | None = None
    sg_error:    str | None = None
    go_error:    str | None = None

    def _run_sg():
        nonlocal sg_result, sg_error
        try:
            sg_result = sensor_guard.run(scenario_id, llm=llm)
        except Exception as e:
            sg_error = str(e)

    def _run_go():
        nonlocal go_result, go_error
        try:
            go_result = grid_optimizer.run(scenario_id, llm=llm)
        except Exception as e:
            go_error = str(e)

    t_sg = threading.Thread(target=contextvars.copy_context().run, args=(_run_sg,), name="SensorGuard", daemon=True)
    t_go = threading.Thread(target=contextvars.copy_context().run, args=(_run_go,), name="GridOptimizer", daemon=True)
    t_sg.start()
    t_go.start()
    t_sg.join(timeout=config.LLM_TIMEOUT_S.get("groq", 30) * config.MAX_STEPS + 30)
    t_go.join(timeout=config.LLM_TIMEOUT_S.get("groq", 30) * config.MAX_STEPS + 30)

    result.sensor_guard  = sg_result
    result.grid_optimizer = go_result
    if sg_error:
        result.errors["SensorGuard"] = sg_error
    if go_error:
        result.errors["GridOptimizer"] = go_error

    _log(orch_id, scenario_id, "PHASE_1_DONE", {
        "sensor_guard_status":  sg_result.status if sg_result else "ERROR",
        "grid_optimizer_status": go_result.status if go_result else "ERROR",
    })

    # ── Phase 2: SecuritySentinel reads the full audit trail ──────────────────
    _log(orch_id, scenario_id, "PHASE_2_START", {"agent": "SecuritySentinel"})
    try:
        ss_result = security_sentinel.run(scenario_id, llm=llm)
        result.security_sentinel = ss_result
    except Exception as e:
        result.errors["SecuritySentinel"] = str(e)
        ss_result = None
    _log(orch_id, scenario_id, "PHASE_2_DONE", {
        "security_sentinel_status": ss_result.status if ss_result else "ERROR",
    })

    # ── Phase 3: merge briefs ─────────────────────────────────────────────────
    result.merged_brief_md = _merge_briefs(
        scenario_id, orch_id, sg_result, go_result, ss_result, result.errors
    )
    result.finished_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result.status = _overall_status(result)

    _log(orch_id, scenario_id, "ORCHESTRATION_DONE", {
        "status": result.status,
        "agents_ok": [k for k in ["SensorGuard", "GridOptimizer", "SecuritySentinel"]
                      if k not in result.errors],
        "errors": result.errors,
    })
    return result


# ── helpers ───────────────────────────────────────────────────────────────────

def _log(orch_id: str, scenario_id: str, event: str, payload: dict) -> None:
    conn = db.connect()
    try:
        db.log_event(conn, orch_id, "orchestrator", "code", event,
                     {"scenario": scenario_id, **payload})
    finally:
        conn.close()


def _overall_status(r: MultiAgentResult) -> str:
    if r.errors:
        return "PARTIAL"
    statuses = {
        getattr(r.sensor_guard, "status", None),
        getattr(r.grid_optimizer, "status", None),
        getattr(r.security_sentinel, "status", None),
    }
    if "NEEDS_HUMAN_REVIEW" in statuses or "FAILED" in statuses:
        return "NEEDS_HUMAN_REVIEW"
    return "COMPLETED"


def _agent_section(title: str, result: RunResult | None, error: str | None) -> str:
    """Format one agent's brief as a collapsible markdown section."""
    if error:
        return f"\n---\n## 🔴 {title}\n**Agent error:** {error}\n"
    if result is None:
        return f"\n---\n## ⚪ {title}\n_Agent did not run._\n"

    icon = {"COMPLETED": "✅", "NEEDS_HUMAN_REVIEW": "⚠️", "FAILED": "🔴"}.get(
        result.status, "⚪"
    )
    pending = [a for a in result.actions if a.status == "PENDING_APPROVAL"]
    blocked = result.blocked_attempts

    header = (
        f"\n---\n## {icon} {title}\n"
        f"_Status: **{result.status}** | "
        f"Provider: {result.provider or 'code-only'} | "
        f"Steps: {result.steps} | "
        f"Pending actions: {len(pending)} | "
        f"Blocked attempts: {blocked}_\n\n"
    )
    return header + (result.brief_md or "_No brief generated._")


def _merge_briefs(
    scenario_id: str,
    orch_id: str,
    sg: RunResult | None,
    go: RunResult | None,
    ss: RunResult | None,
    errors: dict[str, str],
) -> str:
    pending_total = sum(
        len([a for a in (r.actions if r else []) if a.status == "PENDING_APPROVAL"])
        for r in [sg, go, ss]
    )
    blocked_total = sum(
        (r.blocked_attempts if r else 0) for r in [sg, go, ss]
    )

    header = (
        f"# 🤖 AquaGrid Multi-Agent Operator Report\n"
        f"**Scenario:** `{scenario_id}` | "
        f"**Orchestration ID:** `{orch_id}`\n\n"
        f"| Agent | Status | Pending Actions | Blocked Attempts |\n"
        f"|---|---|---|---|\n"
        f"| 🔍 SensorGuard | {_status_icon(sg, errors.get('SensorGuard'))} | "
        f"{len([a for a in (sg.actions if sg else []) if a.status == 'PENDING_APPROVAL'])} | "
        f"{sg.blocked_attempts if sg else '—'} |\n"
        f"| ⚡ GridOptimizer | {_status_icon(go, errors.get('GridOptimizer'))} | "
        f"{len([a for a in (go.actions if go else []) if a.status == 'PENDING_APPROVAL'])} | "
        f"{go.blocked_attempts if go else '—'} |\n"
        f"| 🛡️ SecuritySentinel | {_status_icon(ss, errors.get('SecuritySentinel'))} | "
        f"0 (read-only) | "
        f"{ss.blocked_attempts if ss else '—'} |\n\n"
        f"**Total pending operator decisions:** {pending_total} | "
        f"**Total attack attempts blocked:** {blocked_total}\n"
    )

    body = (
        _agent_section("SensorGuard — Diagnostic Report", sg, errors.get("SensorGuard"))
        + _agent_section("GridOptimizer — Energy Report", go, errors.get("GridOptimizer"))
        + _agent_section("SecuritySentinel — Security Report", ss, errors.get("SecuritySentinel"))
    )

    footer = (
        "\n---\n## ⚖️ Architectural Guarantee\n"
        "_All three agents share the same deterministic safety engine (`guards.py`) "
        "and the same human-approval gateway (`gateway.py`). No agent can execute "
        "hardware changes — every proposal sits in `PENDING_APPROVAL` until a human "
        "operator supplies the cryptographic token. The LLM never touches the "
        "physical database._\n"
    )

    return header + body + footer


def _status_icon(result: RunResult | None, error: str | None) -> str:
    if error:
        return "🔴 ERROR"
    if result is None:
        return "⚪ NOT RUN"
    return {"COMPLETED": "✅ COMPLETED",
            "NEEDS_HUMAN_REVIEW": "⚠️ NEEDS REVIEW",
            "FAILED": "🔴 FAILED"}.get(result.status, result.status)
