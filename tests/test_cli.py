import json
from types import SimpleNamespace

import pytest

from aquagrid import agent, cli, config, db as dbm, llm

THROTTLE = json.dumps({"kind": "plant_setpoint", "target_id": "PLANT-1", "target_value": 500, "rationale": "t"})
ISOLATE = json.dumps({"kind": "valve_position", "target_id": "V-S4-17-18", "target_value": 0, "rationale": "leak"})


def _scripted(*steps):
    """Fake llm.chat: each step is a (tool, args) call; then a final answer."""
    it = iter(steps)

    def fake(messages, tools_):
        step = next(it, None)
        if step is None:
            return SimpleNamespace(content="## Situation\nDone.", tool_calls=None), "fake/model"
        call = SimpleNamespace(id="c", function=SimpleNamespace(name=step[0], arguments=step[1]))
        return SimpleNamespace(content=None, tool_calls=[call]), "fake/model"
    return fake


@pytest.fixture(autouse=True)
def no_token(monkeypatch):
    monkeypatch.setattr(config, "OPERATOR_TOKEN", None)


def _valve():
    return dbm.connect(ro=True).execute("SELECT position_pct FROM valves WHERE id='V-S4-17-18'").fetchone()[0]


def test_demo_flow_run_queue_approve_revert(db, monkeypatch, capsys):
    monkeypatch.setattr(llm, "chat", _scripted(("read_network_telemetry", "{}"), ("propose_action", THROTTLE),
                                               ("propose_action", ISOLATE)))
    assert cli.main(["run", "peak_2pm"]) == 0
    out = capsys.readouterr().out
    assert "REJECTED_BY_GUARD" in out and "PENDING_APPROVAL" in out and "[CODE" in out and "[LLM" in out
    assert _valve() == 100  # nothing moves until a human approves

    cli.main(["queue"])
    assert "V-S4-17-18" in capsys.readouterr().out

    cli.main(["approve", "2", "--operator", "alice", "--note", "confirmed"])
    assert "EXECUTED" in capsys.readouterr().out and _valve() == 0

    cli.main(["revert", "2", "--operator", "bob"])
    assert "REVERTED" in capsys.readouterr().out and _valve() == 100


def test_attack_run_blocks_everything(db, capsys):
    assert cli.main(["run", "attack", "--attack"]) == 0
    out = capsys.readouterr().out
    assert "BLOCKED" in out and "[ATTACKER" in out and "EXECUTED" not in out
    assert _valve() == 100


def test_approve_asks_for_the_operator_token_when_one_is_set(db, monkeypatch, capsys):
    monkeypatch.setattr(llm, "chat", _scripted(("propose_action", ISOLATE)))
    cli.main(["run", "peak_2pm"])
    monkeypatch.setattr(config, "OPERATOR_TOKEN", "s3cret")
    monkeypatch.setattr(cli, "getpass", lambda prompt: "wrong")
    with pytest.raises(SystemExit, match="invalid operator token"):
        cli.main(["approve", "1", "--operator", "mallory"])
    assert _valve() == 100
    monkeypatch.setattr(cli, "getpass", lambda prompt: "s3cret")
    cli.main(["approve", "1", "--operator", "alice"])
    assert _valve() == 0


def test_approving_a_missing_action_exits_cleanly(db):
    with pytest.raises(SystemExit, match="not found"):
        cli.main(["approve", "99", "--operator", "alice"])


def test_inspect_lists_sensors_and_findings(db, capsys):
    cli.main(["inspect", "peak_2pm"])
    out = capsys.readouterr().out
    assert "P-J07" in out and "QUARANTINED" in out and "S4: GENUINE_LEAK J17 P-J17-J18" in out


def test_llm_check_reports_the_provider_and_whether_it_called_a_tool(monkeypatch, capsys):
    seen = []

    def fake_chat(messages, tools_):
        seen.append(list(config.LLM_CHAIN))
        call = SimpleNamespace(function=SimpleNamespace(name="read_network_telemetry", arguments="{}"))
        return SimpleNamespace(content=None, tool_calls=[call]), "groq/llama-3.3-70b-versatile"
    monkeypatch.setattr(llm, "chat", fake_chat)
    chain_before = list(config.LLM_CHAIN)
    cli.main(["llm-check", "--provider", "groq"])
    assert "groq/llama-3.3-70b-versatile answered" in capsys.readouterr().out
    assert seen == [["groq"]] and config.LLM_CHAIN == chain_before  # only that provider; setting restored


def test_llm_check_flags_a_model_that_only_describes_a_tool_call(monkeypatch, capsys):
    monkeypatch.setattr(llm, "chat", lambda m, t: (SimpleNamespace(content="I would call the tool", tool_calls=None),
                                                   "ollama/qwen3:4b"))
    cli.main(["llm-check"])
    out = capsys.readouterr().out
    assert "tool calls: NONE" in out and "text instead of a tool call" in out


def test_llm_check_exits_when_nothing_answers(monkeypatch):
    def down(m, t):
        raise llm.AllProvidersFailed("groq: no key")
    monkeypatch.setattr(llm, "chat", down)
    with pytest.raises(SystemExit, match="FAILED"):
        cli.main(["llm-check", "--provider", "gemini"])


def test_model_text_a_console_codepage_cannot_encode_does_not_crash_the_cli(db, monkeypatch):
    import io
    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")  # a typical Windows console
    monkeypatch.setattr("sys.stdout", console)
    cli.main(["queue"])  # main() makes the streams tolerant
    print("Non‑breaking hyphen in a model brief")  # U+2011 has no cp1252 mapping
    console.flush()
    assert b"breaking hyphen" in console.buffer.getvalue()


def test_missing_database_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "nope.sqlite"))
    with pytest.raises(SystemExit, match="seed"):
        cli.main(["queue"])


def test_cli_is_the_only_place_besides_the_ui_that_wires_human_actions():
    assert agent.KNOWN_TOOLS == {"read_network_telemetry", "read_tariff_and_quality_feeds", "propose_action"}


def _approve_with(monkeypatch, token_name, entered):
    monkeypatch.setattr(config, token_name, "s3cret")
    monkeypatch.setattr(cli, "getpass", lambda prompt: entered)
    cli._authorise()


@pytest.mark.parametrize("name", ["ADMIN_TOKEN", "PLANT_A_TOKEN"])
def test_cli_gate_demands_admin_or_plant_a_token(monkeypatch, name):
    with pytest.raises(SystemExit, match="invalid operator token"):
        _approve_with(monkeypatch, name, "wrong")
    _approve_with(monkeypatch, name, "s3cret")


def test_cli_is_plant_a_only_so_plant_b_token_does_not_gate_it(monkeypatch, capsys):
    monkeypatch.setattr(config, "PLANT_B_TOKEN", "s3cret")
    monkeypatch.setattr(cli, "getpass", lambda prompt: pytest.fail("must not prompt"))
    cli._authorise()
    assert "none of ADMIN_TOKEN, OPERATOR_TOKEN, PLANT_A_TOKEN is set" in capsys.readouterr().out


def test_cli_gate_warns_when_no_token_is_set(capsys):
    cli._authorise()
    assert "none of ADMIN_TOKEN, OPERATOR_TOKEN, PLANT_A_TOKEN is set" in capsys.readouterr().out
