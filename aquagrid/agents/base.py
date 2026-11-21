"""Shared agent loop used by all three AquaGrid agents.

This is the same capped plan/act loop that was in agent.py, extracted so each
specialist agent can configure its own system prompt, tool specs, and tool set
without duplicating the loop logic.
"""
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace

from aquagrid import config, db
from aquagrid import llm as llm_client
from aquagrid.gateway import stage_gated_valve_or_setpoint_action
from aquagrid.schemas import RunResult


# ── run context ──────────────────────────────────────────────────────────────

@dataclass
class AgentContext:
    run_id: str
    agent_name: str
    provider: str | None = None
    blocked_attempts: int = 0
    rejections: int = 0
    escalated: bool = False
    extra: dict = field(default_factory=dict)   # agent-specific state

    @property
    def actor(self) -> str:
        if self.provider and self.provider.startswith("attacker"):
            return "attacker"
        label = f"llm:{self.provider}" if self.provider else "llm:unknown"
        return f"{self.agent_name}:{label}"


# ── audit helper ─────────────────────────────────────────────────────────────

def _audit(ctx: AgentContext, stage: str, event: str, payload: dict,
           actor: str | None = None) -> None:
    conn = db.connect()
    try:
        db.log_event(conn, ctx.run_id, stage, actor or ctx.actor, event, payload)
    finally:
        conn.close()


# ── tool call helpers ─────────────────────────────────────────────────────────

def _tool_call_dict(call) -> dict:
    out = {"id": call.id, "type": "function",
           "function": {"name": call.function.name, "arguments": call.function.arguments}}
    extra = getattr(call, "extra_content", None)
    if extra:
        out["extra_content"] = extra
    return out


def _assistant_dict(msg) -> dict:
    out = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        out["tool_calls"] = [_tool_call_dict(c) for c in msg.tool_calls]
    return out


# ── propose action helper ────────────────────────────────────────────────────

def _propose(args: dict, ctx: AgentContext, allowed_kinds: set[str]) -> dict:
    """Stage an action through the gateway, enforcing kind allowlist per agent."""
    if ctx.escalated:
        return {"error": "replan limit reached; escalated to human review. No further proposals."}
    # Per-agent kind restriction (e.g. SensorGuard can only propose valve_position)
    kind = args.get("kind", "")
    if allowed_kinds and kind not in allowed_kinds:
        ctx.blocked_attempts += 1
        _audit(ctx, "tool_call", "BLOCKED",
               {"reason": f"agent {ctx.agent_name!r} may not propose kind {kind!r}",
                "allowed": list(allowed_kinds)})
        return {"error": f"This agent may only propose {allowed_kinds}. Kind {kind!r} is not allowed."}

    result = stage_gated_valve_or_setpoint_action(args, ctx.run_id, ctx.actor)
    out = result.model_dump()
    if result.status == "BLOCKED":
        ctx.blocked_attempts += 1
    elif result.status == "REJECTED_BY_GUARD":
        ctx.rejections += 1
        if ctx.rejections > config.MAX_REPLANS:
            ctx.escalated = True
            out["escalation"] = "replan limit reached; escalated to human review"
    return out


# ── main loop ─────────────────────────────────────────────────────────────────

def run_agent_loop(
    *,
    agent_name: str,
    scenario_id: str,
    system_prompt: str,
    tool_specs: list[dict],
    read_tools: dict,         # {name: callable}
    allowed_propose_kinds: set[str],   # which action kinds this agent may propose
    fallback_brief_fn,        # fn(ctx) -> str  — called when LLM unavailable
    llm=None,
    max_steps: int | None = None,
) -> RunResult:
    """
    Capped plan/act loop shared by all agents.

    Parameters
    ----------
    agent_name          : short label stored in audit_log actor field
    scenario_id         : must already be loaded in DB
    system_prompt       : agent-specific instruction
    tool_specs          : list of OpenAI function-call specs
    read_tools          : mapping of tool name -> callable (read-only)
    allowed_propose_kinds : set of ActionRequest.kind values this agent may use
    fallback_brief_fn   : produces a plain-text brief if every LLM provider fails
    llm                 : override for testing (default: llm_client.chat)
    max_steps           : cap on LLM turns (default: config.MAX_STEPS)
    """
    if llm is None:
        llm = llm_client.chat
    max_steps = max_steps or config.MAX_STEPS

    known_tools = {s["function"]["name"] for s in tool_specs}
    ctx = AgentContext(run_id=uuid.uuid4().hex[:8], agent_name=agent_name)

    # Register this agent run in the runs table
    conn = db.connect()
    conn.execute(
        "INSERT INTO runs VALUES(?,?,?,?,?,?,?)",
        (ctx.run_id, scenario_id,
         datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "RUNNING", None, 0, 0),
    )
    conn.close()

    _audit(ctx, "agent_start", "STARTED",
           {"agent": agent_name, "scenario": scenario_id}, actor="code")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": f"Scenario {scenario_id}. Analyse and advise the operator."},
    ]

    status, brief, steps = "NEEDS_HUMAN_REVIEW", "", 0

    try:
        while steps < max_steps and not ctx.escalated:
            steps += 1
            msg, ctx.provider = llm(messages, tool_specs)
            messages.append(_assistant_dict(msg))

            if not msg.tool_calls:
                status, brief = "COMPLETED", msg.content or ""
                break

            for call in msg.tool_calls:
                name = call.function.name
                raw  = call.function.arguments
                _audit(ctx, "tool_call", "call", {"tool": name, "args": raw})

                if name not in known_tools:
                    ctx.blocked_attempts += 1
                    _audit(ctx, "tool_call", "BLOCKED",
                           {"tool": name, "reason": "unknown tool"})
                    result = {"error": "unknown tool"}
                else:
                    try:
                        args = json.loads(raw) if raw else {}
                        if not isinstance(args, dict):
                            raise ValueError("arguments must be a JSON object")
                        if name == "propose_action":
                            result = _propose(args, ctx, allowed_propose_kinds)
                        else:
                            result = read_tools[name](**args).model_dump()
                    except (ValueError, TypeError) as e:
                        _audit(ctx, "tool_call", "error", {"tool": name, "error": str(e)})
                        result = {"error": f"bad arguments: {e}"}

                _audit(ctx, "tool_result", "result",
                       {"tool": name, "status": result.get("status", "ok")})
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, default=str),
                })
        else:
            why = ("Replan limit reached" if ctx.escalated
                   else f"Step cap ({max_steps}) reached")
            brief = (f"**{why}: human review required.**\n\n"
                     + fallback_brief_fn(ctx))

    except llm_client.AllProvidersFailed as e:
        brief = (f"**No LLM provider available ({e}): human review required.**\n\n"
                 + fallback_brief_fn(ctx))
        _audit(ctx, "llm", "ALL_PROVIDERS_FAILED", {"error": str(e)}, actor="code")

    finally:
        replans = max(ctx.rejections - 1, 0)
        conn = db.connect()
        conn.execute(
            "UPDATE runs SET status=?, provider=?, steps=?, replans=? WHERE run_id=?",
            (status, ctx.provider, steps, replans, ctx.run_id),
        )
        conn.close()

    # Import gateway here (not at module top) to match agent.py import discipline
    from aquagrid.gateway import list_actions
    return RunResult(
        run_id=ctx.run_id,
        scenario=scenario_id,
        status=status,
        provider=ctx.provider,
        steps=steps,
        replans=replans,
        blocked_attempts=ctx.blocked_attempts,
        brief_md=brief,
        actions=list_actions(run_id=ctx.run_id),
    )
