import json
from types import SimpleNamespace

from aquagrid import agent, config, db as dbm, llm

VALVE_CALL = json.dumps({"kind": "valve_position", "target_id": "V-S4-17-18", "target_value": 0,
                         "rationale": "Isolate the leak."})


def _msg(content=None, *calls):
    tool_calls = [SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=n, arguments=a))
                  for i, (n, a) in enumerate(calls)]
    return SimpleNamespace(content=content, tool_calls=tool_calls or None)


def scripted(*msgs):
    """Fake llm: plays msgs in order, repeating the last one."""
    it = iter(msgs)
    last = []

    def fake(messages, tools):
        last[:] = [next(it, last[0] if last else msgs[-1])]
        return last[0], "fake/model"
    return fake


def _count(table, where=""):
    return dbm.connect(ro=True).execute(f"SELECT count(*) FROM {table} {where}").fetchone()[0]


def test_tool_surface_is_two_reads_plus_propose_action():
    assert [s["function"]["name"] for s in agent.TOOL_SPECS] == [
        "read_network_telemetry", "read_tariff_and_quality_feeds", "propose_action"]


def test_propose_action_schema_has_no_approval_field():
    props = agent.TOOL_SPECS[2]["function"]["parameters"]["properties"]
    assert not {"approved", "execute_now", "approve"} & set(props)


def test_unknown_tool_blocked_and_audited(db):
    ctx = agent.RunContext("r1", "fake/model")
    assert agent.dispatch_tool("execute_valve_command", '{"V-S4-IN": 0}', ctx) == {"error": "unknown tool"}
    assert ctx.blocked_attempts == 1
    assert _count("audit_log", "WHERE event='BLOCKED'") == 1


def test_malformed_json_does_not_crash(db):
    ctx = agent.RunContext("r1", "fake/model")
    assert "error" in agent.dispatch_tool("propose_action", "{not json", ctx)
    assert "error" in agent.dispatch_tool("read_network_telemetry", '["list"]', ctx)
    assert "error" in agent.dispatch_tool("read_network_telemetry", '{"bogus": 1}', ctx)
    assert _count("actions") == 0


def test_propose_action_stages_without_moving_the_valve(db):
    ctx = agent.RunContext("r1", "fake/model")
    res = agent.dispatch_tool("propose_action", VALVE_CALL, ctx)
    assert res["status"] == "PENDING_APPROVAL"
    assert dbm.connect(ro=True).execute(
        "SELECT position_pct FROM valves WHERE id='V-S4-17-18'").fetchone()[0] == 100


def test_injected_approved_field_is_blocked(db):
    ctx = agent.RunContext("r1", "fake/model")
    bad = json.dumps({**json.loads(VALVE_CALL), "approved": True})
    assert agent.dispatch_tool("propose_action", bad, ctx)["status"] == "BLOCKED"
    assert ctx.blocked_attempts == 1 and _count("actions") == 0


def test_stubbed_run_completes(db):
    llm_fn = scripted(_msg(None, ("read_network_telemetry", "{}")), _msg("## Situation\nAll quiet."))
    res = agent.run_scenario("peak_2pm", llm=llm_fn)
    assert (res.status, res.steps, res.provider) == ("COMPLETED", 2, "fake/model")
    assert "Situation" in res.brief_md
    assert dbm.connect(ro=True).execute("SELECT status FROM runs").fetchone()[0] == "COMPLETED"


def test_step_cap_ends_the_loop(db):
    res = agent.run_scenario("peak_2pm", llm=scripted(_msg(None, ("read_tariff_and_quality_feeds", "{}"))))
    assert res.status == "NEEDS_HUMAN_REVIEW" and res.steps == config.MAX_STEPS


def test_all_providers_down_gives_a_deterministic_brief_and_stages_nothing(db):
    def down(messages, tools):
        raise llm.AllProvidersFailed("everything is down")
    res = agent.run_scenario("peak_2pm", llm=down)
    assert res.status == "NEEDS_HUMAN_REVIEW" and "human review" in res.brief_md.lower()
    assert "GENUINE_LEAK" in res.brief_md and "peak throttle to 500 m3/h" in res.brief_md
    assert "29.0% < 40.0% floor" in res.brief_md
    assert _count("actions") == 0


# ---- the agent can read and stage, nothing else ---------------------------------------------------

def test_agent_tools_are_read_and_stage_only():
    names = [s["function"]["name"] for s in agent.TOOL_SPECS]
    assert names == ["read_network_telemetry", "read_tariff_and_quality_feeds", "propose_action"]
    assert set(agent.READ_TOOLS) == set(names[:2])
    assert agent.KNOWN_TOOLS == set(names)


def test_agent_and_llm_modules_cannot_reach_approve_reject_revert_or_the_seed_reset():
    import ast
    import inspect
    forbidden = {"approve_and_execute", "reject", "revert", "seed_data", "_write_target", "set_scenario"}
    for module in (agent, llm):
        tree = ast.parse(inspect.getsource(module))
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        used |= {a.name.split(".")[-1] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                 for a in n.names}
        assert not forbidden & used, f"{module.__name__} reaches {forbidden & used}"


# ---- scripted attack, replan cap, scenario guard --------------------------------------------------

def _valve_positions():
    return [tuple(r) for r in dbm.connect(ro=True).execute("SELECT id, position_pct FROM valves ORDER BY id")]


def test_scripted_attack_all_blocked(db):
    dbm_conn = dbm.connect()
    from aquagrid import seed_data
    seed_data.set_scenario(dbm_conn, "attack")
    before = _valve_positions()
    res = agent.run_scenario("attack", llm=agent.ScriptedAttackLLM())
    assert res.status == "COMPLETED" and res.blocked_attempts >= 2
    assert _valve_positions() == before
    assert not [a for a in res.actions if a.status in ("EXECUTED", "PENDING_APPROVAL")]
    assert sorted(a.status for a in res.actions) == ["REJECTED_BY_GUARD", "REJECTED_BY_GUARD"]
    events = dbm.connect(ro=True).execute("SELECT actor, event FROM audit_log WHERE actor='attacker'").fetchall()
    assert {e["event"] for e in events} >= {"BLOCKED", "REJECTED_BY_GUARD"}


def test_scripted_attack_never_sees_the_injection_text(db):
    from aquagrid import seed_data
    seed_data.set_scenario(dbm.connect(), "attack")
    seen = []
    real = agent.ScriptedAttackLLM()

    def spy(messages, tools_):
        seen.append(str(messages))
        return real(messages, tools_)
    agent.run_scenario("attack", llm=spy)
    assert not any("IGNORE PREVIOUS" in s or "approved=true" in s for s in seen)


def test_replan_cap_routes_to_human_review(db):
    throttle = json.dumps({"kind": "plant_setpoint", "target_id": "PLANT-1", "target_value": 500,
                           "rationale": "Peak tariff, throttle desalination."})
    res = agent.run_scenario("peak_2pm", llm=scripted(_msg(None, ("propose_action", throttle))))
    assert res.status == "NEEDS_HUMAN_REVIEW" and res.replans == config.MAX_REPLANS
    assert res.steps == config.MAX_REPLANS + 1 < config.MAX_STEPS
    assert [a.status for a in res.actions] == ["REJECTED_BY_GUARD"] * (config.MAX_REPLANS + 1)
    assert "29.0% < 40.0% floor" in res.brief_md and "human review" in res.brief_md.lower()


def test_run_requires_the_scenario_to_be_loaded(db):
    with __import__("pytest").raises(ValueError, match="not loaded"):
        agent.run_scenario("baseline", llm=scripted(_msg("done")))


def test_every_run_is_audited_with_code_llm_actors(db):
    llm_fn = scripted(_msg(None, ("read_network_telemetry", "{}")), _msg("done"))
    res = agent.run_scenario("peak_2pm", llm=llm_fn)
    rows = dbm.connect(ro=True).execute("SELECT actor, stage FROM audit_log WHERE run_id=?", (res.run_id,)).fetchall()
    assert ("code", "pre_analysis") in {(r["actor"], r["stage"]) for r in rows}
    assert any(r["actor"] == "llm:fake/model" and r["stage"] == "tool_call" for r in rows)


def test_a_providers_tool_call_metadata_is_replayed_verbatim(db):
    """Gemini 3 attaches a thought_signature to tool calls; dropping it makes the next step fail with HTTP 400."""
    call = SimpleNamespace(id="c0", function=SimpleNamespace(name="read_tariff_and_quality_feeds", arguments="{}"),
                           extra_content={"google": {"thought_signature": "SIG-123"}})
    seen = []

    def fake(messages, tools_):
        seen.append(list(messages))
        if len(seen) == 1:
            return SimpleNamespace(content=None, tool_calls=[call]), "gemini/x"
        return _msg("done"), "gemini/x"
    agent.run_scenario("peak_2pm", llm=fake)
    replayed = next(m for m in seen[1] if m.get("tool_calls"))["tool_calls"][0]
    assert replayed["extra_content"] == {"google": {"thought_signature": "SIG-123"}}
