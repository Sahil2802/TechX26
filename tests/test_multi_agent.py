"""Tests for the AquaGrid multi-agent system.

Tests are designed to run entirely offline (no LLM calls) using scripted fake
LLMs and the existing conftest.py seeded-DB fixture.

The fixture from conftest.py is called `db` (not `seeded_db`). It builds a
fresh seeded database in a tmp dir and monkeypatches config.DB_PATH.
Scenarios must be explicitly set via seed_data.set_scenario() inside each test.
"""
import pytest


def _set_scenario(scenario_id: str) -> None:
    """Helper: set the active scenario in the DB (required before running any agent)."""
    from aquagrid import db as _db, seed_data
    conn = _db.connect()
    try:
        seed_data.set_scenario(conn, scenario_id)
    finally:
        conn.close()


# ── helpers ───────────────────────────────────────────────────────────────────

class _AlwaysDoneLLM:
    """Fake LLM that immediately returns a final answer (no tool calls)."""

    def __init__(self, content: str = "Agent analysis complete."):
        self._content = content
        self.provider = "fake/done"

    def __call__(self, messages, tools=None):
        from types import SimpleNamespace
        return SimpleNamespace(content=self._content, tool_calls=None), self.provider


class _OneToolCallLLM:
    """Fake LLM that makes exactly one specified tool call, then answers."""

    def __init__(self, tool_name: str, args: dict, final: str = "Done."):
        import json
        self._calls_left = 1
        self._tool_name  = tool_name
        self._args       = json.dumps(args)
        self._final      = final
        self.provider    = "fake/one-call"

    def __call__(self, messages, tools=None):
        import uuid
        from types import SimpleNamespace
        if self._calls_left > 0:
            self._calls_left -= 1
            call = SimpleNamespace(
                id=uuid.uuid4().hex[:6],
                function=SimpleNamespace(name=self._tool_name, arguments=self._args),
            )
            return SimpleNamespace(content=None, tool_calls=[call]), self.provider
        return SimpleNamespace(content=self._final, tool_calls=None), self.provider


# ── SensorGuard ───────────────────────────────────────────────────────────────

class TestSensorGuard:

    def test_completes_with_no_tool_calls(self, db):
        _set_scenario("baseline")
        from aquagrid.agents.sensor_guard import run
        result = run("baseline", llm=_AlwaysDoneLLM())
        assert result.status == "COMPLETED"
        assert result.run_id

    def test_read_telemetry_tool_works(self, db):
        _set_scenario("peak_2pm")
        from aquagrid.agents.sensor_guard import run
        llm = _OneToolCallLLM("read_network_telemetry", {"sector": "S4"})
        result = run("peak_2pm", llm=llm)
        assert result.status == "COMPLETED"

    def test_cannot_propose_plant_setpoint(self, db):
        """SensorGuard must never propose a plant_setpoint — blocked by kind allowlist."""
        _set_scenario("peak_2pm")
        from aquagrid.agents.sensor_guard import run
        llm = _OneToolCallLLM(
            "propose_action",
            {"kind": "plant_setpoint", "target_id": "PLANT-1",
             "target_value": 1900, "rationale": "test"},
        )
        result = run("peak_2pm", llm=llm)
        # The base loop blocks it → blocked_attempts >= 1, nothing staged
        assert result.blocked_attempts >= 1
        pending = [a for a in result.actions if a.status == "PENDING_APPROVAL"]
        assert len(pending) == 0

    def test_unknown_tool_is_blocked(self, db):
        _set_scenario("peak_2pm")
        from aquagrid.agents.sensor_guard import run
        llm = _OneToolCallLLM("execute_valve_command", {"valve": "V-S4-IN", "position": 0})
        result = run("peak_2pm", llm=llm)
        assert result.blocked_attempts >= 1


# ── GridOptimizer ─────────────────────────────────────────────────────────────

class TestGridOptimizer:

    def test_completes_with_no_tool_calls(self, db):
        _set_scenario("baseline")
        from aquagrid.agents.grid_optimizer import run
        result = run("baseline", llm=_AlwaysDoneLLM())
        assert result.status == "COMPLETED"

    def test_read_tariff_tool_works(self, db):
        _set_scenario("peak_2pm")
        from aquagrid.agents.grid_optimizer import run
        llm = _OneToolCallLLM("read_tariff_and_quality_feeds", {})
        result = run("peak_2pm", llm=llm)
        assert result.status == "COMPLETED"

    def test_cannot_propose_valve_position(self, db):
        """GridOptimizer must never propose a valve_position — blocked by allowlist."""
        _set_scenario("peak_2pm")
        from aquagrid.agents.grid_optimizer import run
        llm = _OneToolCallLLM(
            "propose_action",
            {"kind": "valve_position", "target_id": "V-S4-17-18",
             "target_value": 0, "rationale": "test"},
        )
        result = run("peak_2pm", llm=llm)
        assert result.blocked_attempts >= 1
        pending = [a for a in result.actions if a.status == "PENDING_APPROVAL"]
        assert len(pending) == 0


# ── SecuritySentinel ──────────────────────────────────────────────────────────

class TestSecuritySentinel:

    def test_completes_with_no_tool_calls(self, db):
        _set_scenario("attack")
        from aquagrid.agents.security_sentinel import run
        result = run("attack", llm=_AlwaysDoneLLM())
        assert result.status == "COMPLETED"

    def test_read_audit_trail_tool_works(self, db):
        _set_scenario("attack")
        from aquagrid.agents.security_sentinel import run
        llm = _OneToolCallLLM(
            "read_audit_trail",
            {"event_filter": "BLOCKED,REJECTED_BY_GUARD", "limit": 50},
        )
        result = run("attack", llm=llm)
        assert result.status == "COMPLETED"

    def test_cannot_propose_any_action(self, db):
        """SecuritySentinel has an empty allowed_propose_kinds — all proposals blocked."""
        _set_scenario("attack")
        from aquagrid.agents.security_sentinel import run
        llm = _OneToolCallLLM(
            "propose_action",
            {"kind": "valve_position", "target_id": "V-S4-IN",
             "target_value": 0, "rationale": "test"},
        )
        result = run("attack", llm=llm)
        assert result.blocked_attempts >= 1

    def test_audit_trail_reader_returns_data(self, db):
        """read_audit_trail() should run without errors even on an empty log."""
        _set_scenario("attack")
        from aquagrid.agents.security_sentinel import read_audit_trail
        report = read_audit_trail(event_filter="BLOCKED,REJECTED_BY_GUARD")
        assert hasattr(report, "events")
        assert hasattr(report, "threat_counts")
        d = report.model_dump()
        assert "event_count" in d
        assert "threat_counts" in d


# ── Orchestrator ──────────────────────────────────────────────────────────────

class TestOrchestrator:

    def test_all_three_agents_complete(self, db):
        _set_scenario("baseline")
        from aquagrid import orchestrator
        result = orchestrator.run("baseline", llm=_AlwaysDoneLLM())
        assert result.sensor_guard is not None
        assert result.grid_optimizer is not None
        assert result.security_sentinel is not None
        assert result.status in {"COMPLETED", "NEEDS_HUMAN_REVIEW", "PARTIAL"}

    def test_merged_brief_contains_all_agents(self, db):
        _set_scenario("baseline")
        from aquagrid import orchestrator
        result = orchestrator.run("baseline", llm=_AlwaysDoneLLM())
        assert "SensorGuard" in result.merged_brief_md
        assert "GridOptimizer" in result.merged_brief_md
        assert "SecuritySentinel" in result.merged_brief_md

    def test_orchestration_id_is_unique(self, db):
        _set_scenario("baseline")
        from aquagrid import orchestrator
        r1 = orchestrator.run("baseline", llm=_AlwaysDoneLLM())
        r2 = orchestrator.run("baseline", llm=_AlwaysDoneLLM())
        assert r1.orchestration_id != r2.orchestration_id

    def test_errors_dict_empty_on_success(self, db):
        _set_scenario("baseline")
        from aquagrid import orchestrator
        result = orchestrator.run("baseline", llm=_AlwaysDoneLLM())
        assert result.errors == {}

    def test_attack_scenario_blocked_attempts(self, db):
        """Running the scripted attacker through the orchestrator produces blocked events."""
        _set_scenario("attack")
        from aquagrid import agent as _agent, orchestrator
        result = orchestrator.run("attack", llm=_agent.ScriptedAttackLLM())
        total_blocked = sum(
            (r.blocked_attempts if r else 0)
            for r in [result.sensor_guard, result.grid_optimizer, result.security_sentinel]
        )
        # Scripted attacker has 4 attack steps; at least some should block
        assert total_blocked >= 1

    def test_all_actions_property(self, db):
        _set_scenario("baseline")
        from aquagrid import orchestrator
        result = orchestrator.run("baseline", llm=_AlwaysDoneLLM())
        assert isinstance(result.all_actions, list)
