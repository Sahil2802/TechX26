"""Terminal demo and operator console.

Human-side commands (approve / reject / revert) live here and in web.py, never in the agent.
Usage: python -m aquagrid.cli seed | reset S | inspect S | run S [--attack] | queue
       | approve ID --operator NAME | reject ID --operator NAME | revert ID --operator NAME
"""
import argparse
import hmac
import json
import sys
from getpass import getpass
from pathlib import Path

from aquagrid import agent, config, db, gateway, llm, seed_data, tools

PAYLOAD_WIDTH = 110


def _badge(actor: str) -> str:
    for prefix, badge in (("code", "CODE"), ("llm:", "LLM"), ("human:", "HUMAN"), ("attacker", "ATTACKER")):
        if actor.startswith(prefix):
            return badge
    return actor.upper()


def _need_db() -> None:
    if not Path(config.DB_PATH).exists():
        raise SystemExit(f"no database at {config.DB_PATH}: run `python -m aquagrid.cli seed` first")


def _load_scenario(scenario: str) -> None:
    _need_db()
    conn = db.connect()
    try:
        seed_data.set_scenario(conn, scenario)
    finally:
        conn.close()


def _authorise() -> None:
    """Operator token gate for human actions. The agent process never holds this token."""
    expected = config.ADMIN_TOKEN or config.OPERATOR_TOKEN or config.PLANT_A_TOKEN  # the CLI is plant A only
    if not expected:
        print("WARNING: none of ADMIN_TOKEN, OPERATOR_TOKEN, PLANT_A_TOKEN is set; approvals are unauthenticated (demo mode).")
        return
    if not hmac.compare_digest(getpass("Operator token: "), expected):
        raise SystemExit("invalid operator token")


def _print_trace(run_id: str) -> None:
    conn = db.connect(ro=True)
    try:
        rows = conn.execute("SELECT * FROM audit_log WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
    finally:
        conn.close()
    for r in rows:
        payload = json.dumps(json.loads(r["payload_json"]), default=str)
        print(f"  [{_badge(r['actor']):8}] {r['stage']}/{r['event']}  {payload[:PAYLOAD_WIDTH]}")


def _print_action(a) -> None:
    print(f"  #{a.id} {a.status:17} {a.kind} {a.target_id}: {a.from_value:g} -> {a.to_value:g}  (by {a.proposed_by})")
    for c in a.guard.checks:
        print(f"      {'PASS' if c.passed else 'FAIL'}  {c.name}: {c.detail}")
    if a.approver or a.note:
        print(f"      approver={a.approver} note={a.note}")


def cmd_seed(_: argparse.Namespace) -> None:
    seed_data.build()
    conn = db.connect(ro=True)
    print("seeded", config.DB_PATH, {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                                     for t in ("telemetry", "sensors", "valves")})


def cmd_reset(args: argparse.Namespace) -> None:
    _load_scenario(args.scenario)
    print(f"scenario {args.scenario} loaded: valves 100%, plant at hold, actions cleared")


def cmd_inspect(args: argparse.Namespace) -> None:
    _load_scenario(args.scenario)
    pre = tools.pre_analysis()
    print(f"{'sensor':10}{'kind':10}{'latest':>9}  health")
    for s in pre["specs"]:
        h = pre["health"][s.id]
        flag = f"{h.status} QUARANTINED: {h.evidence}" if h.quarantined else h.status
        print(f"{s.id:10}{s.kind:10}{pre['series'][s.id][-1]:9.2f}  {flag}")
    for f in pre["findings"]:
        print(f"{f.sector}: {f.verdict} {f.suspected_node or ''} {f.suspected_pipe or ''}")


def cmd_run(args: argparse.Namespace) -> None:
    _load_scenario(args.scenario)
    result = agent.run_scenario(args.scenario, llm=agent.ScriptedAttackLLM() if args.attack else llm.chat)
    print(f"run {result.run_id} {result.status} provider={result.provider} steps={result.steps} "
          f"blocked={result.blocked_attempts}\n\ntrace:")
    _print_trace(result.run_id)
    print("\nactions:")
    for a in result.actions:
        _print_action(a)
    print(f"\n{result.brief_md}")


def cmd_llm_check(args: argparse.Namespace) -> None:
    """Does a provider answer, and does it really call a tool (not just describe one)? Keys come from .env."""
    saved, config.LLM_CHAIN = config.LLM_CHAIN, [args.provider] if args.provider else config.LLM_CHAIN
    try:
        messages = [{"role": "system", "content": agent.SYSTEM_PROMPT},
                    {"role": "user", "content": "Scenario peak_2pm. Analyse the network and advise the operator."}]
        msg, who = llm.chat(messages, agent.tool_specs())
    except llm.AllProvidersFailed as e:
        raise SystemExit(f"FAILED: {e}")
    finally:
        config.LLM_CHAIN = saved
    calls = [(c.function.name, c.function.arguments) for c in (msg.tool_calls or [])]
    print(f"{who} answered. tool calls: {calls or 'NONE'}")
    if not calls:
        print(f"text instead of a tool call: {(msg.content or '')[:200]!r}")


def cmd_queue(_: argparse.Namespace) -> None:
    _need_db()
    pending = gateway.list_actions(status="PENDING_APPROVAL")
    print(f"{len(pending)} pending" if pending else "approval queue is empty")
    for a in pending:
        _print_action(a)


def _human_action(args: argparse.Namespace) -> None:
    _need_db()
    _authorise()
    fn = {"approve": gateway.approve_and_execute, "reject": gateway.reject, "revert": gateway.revert}[args.command]
    try:
        _print_action(fn(args.id, args.operator, args.note))
    except ValueError as e:
        raise SystemExit(str(e))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aquagrid")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed").set_defaults(func=cmd_seed)
    sub.add_parser("queue").set_defaults(func=cmd_queue)
    check = sub.add_parser("llm-check", help="verify an LLM provider answers and calls tools")
    check.add_argument("--provider", choices=list(llm.PROVIDERS))
    check.set_defaults(func=cmd_llm_check)
    for name, func in (("reset", cmd_reset), ("inspect", cmd_inspect), ("run", cmd_run)):
        p = sub.add_parser(name)
        p.add_argument("scenario", choices=list(seed_data.SCENARIOS))
        p.set_defaults(func=func)
        if name == "run":
            p.add_argument("--attack", action="store_true", help="replay the scripted malicious model")
    for name in ("approve", "reject", "revert"):
        p = sub.add_parser(name)
        p.add_argument("id", type=int)
        p.add_argument("--operator", required=True)
        p.add_argument("--note", default="")
        p.set_defaults(func=_human_action)
    return parser


def _tolerate_unprintable_text() -> None:
    """Model output can contain characters a Windows console codepage cannot encode (e.g. U+2011).
    Replace them instead of crashing after the run already finished."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    _tolerate_unprintable_text()
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
