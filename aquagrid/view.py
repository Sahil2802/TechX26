"""Presentation helpers for the operator console (aquagrid/web.py).

Presentation-neutral: everything is plain data in, plain data out, so it is unit-tested.
"""
import json
import statistics

from aquagrid import config, db, guards, scheduler, tools
from aquagrid.schemas import ActionRecord, LeakFinding

_BADGES = (("code", "CODE"), ("llm:", "LLM"), ("human:", "HUMAN"), ("attacker", "ATTACKER"))
DETAIL_WIDTH = 140


def actor_badge(actor: str) -> str:
    """Who did it: deterministic CODE, the LLM, a HUMAN operator, or the scripted ATTACKER."""
    return next((badge for prefix, badge in _BADGES if actor.startswith(prefix)), actor.upper())


def loaded_scenario() -> str | None:
    """Which scenario's state is currently loaded in the database."""
    conn = db.connect(ro=True)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='scenario'").fetchone()
    finally:
        conn.close()
    return row["value"] if row else None


def load_snapshot() -> dict:
    """Everything the console shows, read fresh from the database (read-only) plus the code analysis."""
    conn = db.connect(ro=True)
    try:
        valves = {r["id"]: r["position_pct"] for r in conn.execute("SELECT id, position_pct FROM valves ORDER BY id")}
        plant = dict(conn.execute("SELECT * FROM plant").fetchone())
        reservoir = dict(conn.execute("SELECT * FROM reservoir").fetchone())
    finally:
        conn.close()
    pre = tools.pre_analysis()
    return {"scenario": pre["scenario"], "pre": pre, "feeds": tools.read_tariff_and_quality_feeds(),
            "valves": valves, "plant": plant, "reservoir": reservoir}


def sensor_rows(pre: dict) -> list[dict]:
    """One row per sensor, quarantined ones first."""
    rows = []
    for spec in pre["specs"]:
        health = pre["health"][spec.id]
        rows.append({"Sensor": spec.id, "Kind": spec.kind, "At": spec.location,
                     "Latest": round(pre["series"][spec.id][-1], 2), "Status": health.status,
                     "Trust": "QUARANTINED" if health.quarantined else "trusted",
                     "Why": health.evidence if health.quarantined else ""})
    return sorted(rows, key=lambda r: (r["Trust"] != "QUARANTINED", r["Sensor"]))


def finding_card(f: LeakFinding) -> dict:
    """Headline + tone + evidence for one sector finding. Always attributes the decision to code."""
    where = f"{f.suspected_node} on {f.suspected_pipe}" if f.suspected_pipe else f.suspected_node
    if f.verdict == "GENUINE_LEAK":
        headline = f"{f.sector}: GENUINE LEAK at {where}. Isolating valve: {f.suspected_valve or 'n/a'}"
        tone = "error"
    elif f.verdict == "SENSOR_FAULT":
        headline = f"{f.sector}: SENSOR FAULT at {where or 'sensor'}. No valve action is allowed on this evidence"
        tone = "warning"
    else:
        headline, tone = f"{f.sector}: no anomaly", "ok"
    return {"headline": headline, "tone": tone, "evidence": list(f.evidence),
            "decided_by": "Decided by deterministic code (guards.classify_sector), not by the LLM."}


def _per_hour(values: list[float]) -> str:
    return f"{values[0]:,.0f}" if len(set(values)) == 1 else f"avg {statistics.fmean(values):,.0f}"


def reservoir_arithmetic(level_pct: float, capacity_m3: float, production_m3h: float,
                         demand_by_hour: dict[int, float], floor_pct: float,
                         fire_reserve_m3: float = 0.0) -> dict:
    """The floor check as visible arithmetic, using the same projection as the guards."""
    hours = list(range(config.PEAK_START_H, config.PEAK_END_H))
    demand = [demand_by_hour[h] for h in hours]
    levels = guards.project_reservoir(level_pct, [production_m3h] * len(hours), demand, capacity_m3)
    low = min(levels)
    start_m3, end_m3 = level_pct * capacity_m3 / 100, levels[-1] * capacity_m3 / 100
    net_per_hour = production_m3h - statistics.fmean(demand)
    ok = low >= floor_pct
    fire_pct = (fire_reserve_m3 / capacity_m3 * 100) if capacity_m3 > 0 else 0.0
    lines = [
        f"Start        {level_pct:.1f}% of {capacity_m3:,.0f} m3 = {start_m3:,.0f} m3",
    ]
    if fire_reserve_m3 > 0:
        lines.append(
            f"Fire reserve {fire_reserve_m3:,.0f} m3 ({fire_pct:.1f}%) committed to Civil Defence (Sector 6)")
    lines += [
        f"Peak window  {config.PEAK_START_H:02d}:00-{config.PEAK_END_H:02d}:00 ({len(hours)} h): production "
        f"{production_m3h:,.0f} - demand {_per_hour(demand)} = {net_per_hour:+,.0f} m3/h",
        f"Net          {net_per_hour:+,.0f} x {len(hours)} h = {end_m3 - start_m3:+,.0f} m3",
        f"End          {end_m3:,.0f} m3 = {levels[-1]:.1f}%   {'>=' if ok else '<'} {floor_pct:.1f}% floor"
        f"  ->  {'PASS' if ok else 'VETO'}",
    ]
    return {"lines": lines, "end_pct": levels[-1], "min_pct": low, "passes": ok}


def level_curves(snapshot: dict) -> dict[str, list]:
    """Hourly reservoir level for the chart: the scheduled plan, the rejected throttle, and the floor."""
    slots, feeds = snapshot["feeds"].schedule.slots, snapshot["feeds"]
    hours = [s.hour for s in slots]
    demand = [snapshot["pre"]["demand_forecast"][h] for h in hours]
    throttle = scheduler.throttle_levels(snapshot["reservoir"]["level_pct"], demand, hours,
                                         snapshot["plant"]["max_m3h"], snapshot["reservoir"]["capacity_m3"])
    return {"hour": hours,
            "Scheduled plan": [s.projected_level_pct for s in slots],
            f"Rejected: throttle to {config.THROTTLE_CANDIDATE_M3H:.0f} m3/h": throttle,
            "Effective floor": [feeds.effective_floor_pct] * len(hours)}


def guard_rows(action: ActionRecord) -> list[dict]:
    return [{"Check": c.name, "Result": "PASS" if c.passed else "FAIL", "Detail": c.detail}
            for c in action.guard.checks]


def _clip(text: str) -> str:
    return text if len(text) <= DETAIL_WIDTH else text[:DETAIL_WIDTH - 3] + "..."


def describe_event(stage: str, event: str, payload: dict) -> str:
    """One readable line for an audit event."""
    if (stage, event) == ("pre_analysis", "findings"):
        return (f"verdicts {payload.get('verdicts')}, quarantined {payload.get('quarantined')}, "
                f"floor {payload.get('effective_floor_pct')}%")
    if stage == "tool_call" and event == "call":
        return f"{payload.get('tool')}({payload.get('args')})"
    if stage == "tool_result":
        return f"{payload.get('tool')} -> {payload.get('status')}"
    if event == "BLOCKED":
        return "; ".join(payload.get("reasons") or [payload.get("reason", "blocked")])
    if event == "REJECTED_BY_GUARD":
        failed = [c["detail"] for c in payload.get("guard", {}).get("checks", []) if not c["passed"]]
        req = payload.get("request", {})
        return f"{req.get('target_id')} -> {req.get('target_value')}: " + "; ".join(failed)
    if event == "PENDING_APPROVAL":
        req = payload.get("request", {})
        return f"queued {req.get('kind')} {req.get('target_id')} -> {req.get('target_value')} for approval"
    if event in ("EXECUTED", "REVERTED"):
        return f"{payload.get('target', 'action')} {event.lower()} (action #{payload.get('action_id')})"
    if event in ("DENIED", "EXECUTION_FAILED", "ALL_PROVIDERS_FAILED"):
        return str(payload.get("reasons") or payload.get("error") or payload.get("note") or event)
    return json.dumps(payload, default=str)


def trace_rows(run_id: str) -> list[dict]:
    """The decision trace of one run, in order, with who did each step."""
    conn = db.connect(ro=True)
    try:
        rows = conn.execute("SELECT * FROM audit_log WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
    finally:
        conn.close()
    return [{"#": i, "Who": actor_badge(r["actor"]), "Step": f"{r['stage']}/{r['event']}",
             "Detail": _clip(describe_event(r["stage"], r["event"], json.loads(r["payload_json"])))}
            for i, r in enumerate(rows, 1)]


GATE_ANCHOR_ID = "human-gate-anchor"
TAB_LABEL = "Action required"  # legacy Streamlit popup helper; the React console does this in frontend/src/Popup.tsx
_POPUP_ID = "aquagrid-approval-popup"


def approval_popup_html(pending_ids: list[int]) -> str:
    """A floating 'human approval needed' notice; clicking it scrolls to the approval gate.

    One script, always rendered: with nothing pending it removes any popup left from an earlier run.
    Ids are ints from our own database, never free text, so nothing here is user-controlled markup.
    """
    ids = [int(i) for i in pending_ids]
    n = len(ids)
    label = f"Human approval needed: {n} action{'s' if n != 1 else ''} waiting (#{', #'.join(map(str, ids))})"
    return f"""<script>
(function () {{
  var old = document.getElementById("{_POPUP_ID}");
  if (old) old.remove();
  if ({n} === 0) return;
  var box = document.createElement("div");
  box.id = "{_POPUP_ID}";
  box.setAttribute("role", "alert");
  box.style.cssText = "position:fixed;top:70px;right:20px;z-index:999999;max-width:360px;padding:12px 14px;"
    + "border-radius:10px;background:#b45309;color:#fffbeb;font:600 14px sans-serif;cursor:pointer;"
    + "box-shadow:0 6px 24px rgba(0,0,0,.35);display:flex;gap:10px;align-items:center";
  var text = document.createElement("span");
  text.textContent = "\\uD83D\\uDD14 {label} \\u2014 click to review";
  var close = document.createElement("button");
  close.textContent = "\\u00D7";
  close.setAttribute("aria-label", "Dismiss");
  close.style.cssText = "background:none;border:0;color:inherit;font-size:20px;cursor:pointer;margin-left:auto";
  close.onclick = function (e) {{ e.stopPropagation(); box.remove(); }};
  box.onclick = function () {{
    var tabs = document.querySelectorAll('button[role="tab"]');
    for (var i = 0; i < tabs.length; i++) {{
      if (tabs[i].textContent.indexOf("{TAB_LABEL}") !== -1) {{ tabs[i].click(); break; }}
    }}
    setTimeout(function () {{  // let the tab panel become visible before scrolling to the gate
      var gate = document.getElementById("{GATE_ANCHOR_ID}");
      if (gate) gate.scrollIntoView({{behavior: "smooth", block: "start"}});
    }}, 150);
    box.remove();
  }};
  box.append(text, close);
  document.body.appendChild(box);
}})();
</script>"""
