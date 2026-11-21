"""Every safety-relevant decision as pure functions: lists/dicts in, schemas out. No DB, no LLM."""
import re
import statistics
from collections import deque
from datetime import datetime

from aquagrid import config
from aquagrid.schemas import ActionRequest, Check, GuardReport, LeakFinding, SensorHealth, SensorSpec

REDACTION = "[REDACTED: instruction-like text]"
_INJECTION_PATTERNS = {
    "ignore_instructions": r"ignore\s+(all|previous|prior)",
    "system_prefix": r"\bsystem\s*(override)?\s*:",
    "approval_field": r"approved\s*=",
    "pre_approved": r"pre-?approved",
    "action_command": r"\b(execute|close|open|set)\b.{0,40}(\bvalve\b|\bV-[A-Z0-9-]+|\bPLANT-\d)",
    "close_order": r"\b(close|open|shut|isolate|operate)\s+(V-[A-Z0-9-]+|\bvalve\b)",
    "supervisor_approval": r"approved\s+by\s+supervisor|supervisor\s+approval",
    "emergency_order": r"\b(immediately|urgent|emergency\s+order)\b",
    "role_change": r"you\s+are\s+now",
    "code_fence": r"```",
    "tool_name": r"stage_gated_valve_or_setpoint_action|propose_action|execute_valve|run_sql",
}
_COMPILED = {name: re.compile(p, re.IGNORECASE) for name, p in _INJECTION_PATTERNS.items()}


def is_untrusted_text(text: str) -> tuple[bool, list[str]]:
    """Return (True, flags) if text matches any prompt-injection or unauthorized instruction pattern."""
    flags: list[str] = []
    for name, rx in _COMPILED.items():
        if rx.search(text):
            flags.append(name)
    return bool(flags), flags


def sanitize_text(text: str) -> tuple[str, list[str]]:
    """Redact instruction-like sentences from untrusted free text. Returns (clean, flags)."""
    flags: list[str] = []
    kept: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        hit = [name for name, rx in _COMPILED.items() if rx.search(sentence)]
        if not hit:
            kept.append(sentence)
            continue
        flags.extend(n for n in hit if n not in flags)
        if not kept or kept[-1] != REDACTION:  # collapse runs of redacted sentences
            kept.append(REDACTION)
    return " ".join(kept), flags


def _minutes_between(earlier: str, later: str) -> float:
    return (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds() / 60


def check_sensor(spec: SensorSpec, values: list[float], last_ts: str, now: str) -> SensorHealth:
    """STALE -> IMPOSSIBLE -> FLATLINE -> OK. DRIFT needs neighbours, so classify_sector sets it."""
    def health(status, evidence, **metrics):
        return SensorHealth(sensor_id=spec.id, status=status, quarantined=status != "OK",
                            evidence=evidence, metrics=metrics)

    age = _minutes_between(last_ts, now)
    if age > config.STALE_AFTER_MIN:
        return health("STALE", f"last sample {age:.0f} min old (> {config.STALE_AFTER_MIN})", age_min=age)

    window = values[-config.FLATLINE_WINDOW:]
    lo, hi = config.VALID_RANGE[spec.kind]
    bad = [v for v in window if not lo <= v <= hi]
    if bad:
        return health("IMPOSSIBLE", f"{bad[-1]:.2f} outside physical range [{lo}, {hi}]", latest=window[-1])
    max_roc = config.MAX_ROC.get(spec.kind)
    if max_roc is not None:
        jump = max((abs(b - a) for a, b in zip(window, window[1:])), default=0.0)
        if jump > max_roc:
            return health("IMPOSSIBLE", f"jump of {jump:.1f} in one sample (> {max_roc})", max_jump=jump)

    if len(window) >= config.FLATLINE_WINDOW:
        sd = statistics.pstdev(window)
        if sd < config.FLATLINE_EPS:
            return health("FLATLINE", f"stuck at {window[-1]:.3f} for {len(window)} samples", stdev=sd)
    return health("OK", "within range, varying normally", latest=window[-1] if window else 0.0)


def _residuals(values: list[float], neighbour_values: list[list[float]]) -> list[float]:
    n = min([len(values)] + [len(nv) for nv in neighbour_values])
    return [values[i] - statistics.median(nv[i] for nv in neighbour_values) for i in range(n)]


def drift_slope(values: list[float], neighbour_values: list[list[float]]) -> float:
    """Slope per sample of (value - median(neighbours)), minus its first-sample offset.

    Common-mode trends (whole sector sagging with demand) cancel; a lone drifting sensor does not.
    """
    residual = _residuals(values, neighbour_values)
    offset = residual[0]
    return statistics.linear_regression(range(len(residual)), [r - offset for r in residual]).slope


def is_drift(values: list[float], neighbour_values: list[list[float]]) -> bool:
    """A steady ramp vs neighbours over the drift window. A sudden step (leak) is not drift."""
    if not neighbour_values:
        return False
    w = config.DRIFT_WINDOW
    window, nbrs = values[-w:], [nv[-w:] for nv in neighbour_values]
    resid = _residuals(window, nbrs)
    step = max((abs(b - a) for a, b in zip(resid, resid[1:])), default=0.0)
    return abs(drift_slope(window, nbrs)) > config.DRIFT_SLOPE_M_PER_MIN and step <= config.DRIFT_MAX_STEP_M


# ---- reservoir, floors, connectivity ---------------------------------------------------------

def mass_balance(inlet_lps: float, expected_demand_lps: float) -> float:
    """Residual: positive means more water entering the sector than demand explains."""
    return inlet_lps - expected_demand_lps


def effective_floor(bloom_active: bool, intake_trusted: bool,
                    fire_reserve_m3: float = 0.0, capacity_m3: float = 0.0) -> float:
    """35% hard minimum, +5% for bloom/untrusted intake, +N% for Civil Defence fire reserve."""
    extra = config.BLOOM_RESERVE_MARGIN_PCT if bloom_active or not intake_trusted else 0.0
    fire_pct = (fire_reserve_m3 / capacity_m3 * 100) if capacity_m3 > 0 else 0.0
    return config.RESERVE_FLOOR_PCT + extra + fire_pct


def project_reservoir(level_pct: float, production_m3h: list[float], demand_m3h: list[float],
                      capacity_m3: float) -> list[float]:
    """Level (%) after each hour, clipped to [0, 100]. Arithmetic in m3 keeps the demo numbers exact."""
    volume, levels = level_pct * capacity_m3 / 100, []
    for produced, demanded in zip(production_m3h, demand_m3h):
        volume = min(max(volume + produced - demanded, 0.0), capacity_m3)
        levels.append(volume * 100 / capacity_m3)
    return levels


def reachable_nodes(pipes: list[dict], closed_pipe_ids: set[str], source: str = "R1") -> set[str]:
    """Every node the source can still reach over open pipes (BFS)."""
    adjacency: dict[str, list[str]] = {}
    for p in pipes:
        if p["id"] not in closed_pipe_ids:
            adjacency.setdefault(p["from_node"], []).append(p["to_node"])
            adjacency.setdefault(p["to_node"], []).append(p["from_node"])
    seen, queue = {source}, deque([source])
    while queue:
        for nxt in adjacency.get(queue.popleft(), []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def connectivity_ok(pipes: list[dict], closed_pipe_ids: set[str], source: str = "R1",
                    critical: list[str] | None = None) -> tuple[bool, list[str]]:
    """Returns (all critical nodes supplied, unreachable ones)."""
    critical = config.CRITICAL_NODES if critical is None else critical
    seen = reachable_nodes(pipes, closed_pipe_ids, source)
    unreachable = sorted(n for n in critical if n not in seen)
    return not unreachable, unreachable


# ---- leak vs sensor fault --------------------------------------------------------------------

def _drop(values: list[float]) -> float:
    """Baseline mean (first samples) minus current mean (last samples)."""
    return (statistics.fmean(values[:config.BASELINE_SAMPLES])
            - statistics.fmean(values[-config.CURRENT_SAMPLES:]))


def _quarantine(h: SensorHealth, status: str, evidence: str, **metrics: float) -> SensorHealth:
    return h.model_copy(update={"status": status, "quarantined": True, "evidence": evidence,
                                "metrics": {**h.metrics, **metrics}})


def _healthy_neighbours(spec: SensorSpec, series: dict, health: dict) -> list[list[float]]:
    return [series[n] for n in spec.neighbours if n in series and not health[n].quarantined]


def _inlet_stats(flow: SensorSpec | None, series: dict, health: dict, expected_lps: float) -> tuple[float, float]:
    """(flow rise vs baseline, mass-balance residual) from a healthy inlet meter, else (0, 0)."""
    if flow is None or health[flow.id].quarantined:
        return 0.0, 0.0
    values = series[flow.id]
    now = statistics.fmean(values[-config.CURRENT_SAMPLES:])
    return now - statistics.fmean(values[:config.BASELINE_SAMPLES]), mass_balance(now, expected_lps)


def _leak_if_corroborated(sector: str, pressure: list[SensorSpec], drops: dict[str, float],
                          anomalous: dict[str, float], rise: float, residual: float) -> LeakFinding | None:
    worst_id = max(anomalous, key=anomalous.get)
    worst = anomalous[worst_id]
    near = sorted(((d, i) for i, d in drops.items()
                   if i != worst_id and d >= config.NEIGHBOUR_DROP_FRACTION * worst), reverse=True)
    signals = [rise >= config.LEAK_FLOW_RISE_LPS, residual > config.MASS_BALANCE_TOL_LPS, bool(near)]
    if not all(signals):
        return None
    loc = {s.id: s.location for s in pressure}
    a, b = loc[worst_id], loc[near[0][1]]
    return LeakFinding(
        sector=sector, verdict="GENUINE_LEAK", suspected_node=a, suspected_pipe=f"P-{min(a, b)}-{max(a, b)}",
        confidence=sum(signals) / len(signals), mass_balance_residual_lps=residual,
        evidence=[f"{worst_id} fell {worst:.1f} m vs baseline",
                  f"{near[0][1]} fell {near[0][0]:.1f} m (>= {config.NEIGHBOUR_DROP_FRACTION:.0%} of {worst:.1f})",
                  f"inlet flow +{rise:.1f} L/s vs baseline",
                  f"mass-balance residual {residual:+.1f} L/s (tolerance {config.MASS_BALANCE_TOL_LPS})"])


def classify_sector(sector: str, specs: list[SensorSpec], series: dict[str, list[float]],
                    health: dict[str, SensorHealth], expected_demand_lps: float
                    ) -> tuple[LeakFinding, list[SensorHealth]]:
    """Genuine leak vs sensor fault for one sector. Quarantined sensors never feed later maths."""
    pressure = [s for s in specs if s.kind == "pressure"]
    flow = next((s for s in specs if s.kind == "flow"), None)
    updated = {s.id: health[s.id] for s in specs}
    for s in pressure:  # steady ramp vs healthy neighbours = drifting sensor
        nbrs = _healthy_neighbours(s, series, updated)
        if not updated[s.id].quarantined and is_drift(series[s.id], nbrs):
            slope = drift_slope(series[s.id][-config.DRIFT_WINDOW:], [n[-config.DRIFT_WINDOW:] for n in nbrs])
            updated[s.id] = _quarantine(updated[s.id], "DRIFT", f"drifting {slope:+.2f} m/min vs neighbours", slope=slope)
    drops = {s.id: _drop(series[s.id]) for s in pressure if not updated[s.id].quarantined}
    anomalous = {i: d for i, d in drops.items() if d >= config.LEAK_PRESSURE_DROP_M}
    rise, residual = _inlet_stats(flow, series, updated, expected_demand_lps)
    if anomalous:
        leak = _leak_if_corroborated(sector, pressure, drops, anomalous, rise, residual)
        if leak:
            return leak, list(updated.values())
        for sid, d in anomalous.items():
            updated[sid] = _quarantine(updated[sid], "DRIFT", f"pressure fell {d:.1f} m but inlet flow, mass "
                                       "balance and neighbours do not corroborate a leak", drop_m=d)
    return _fault_or_none(sector, pressure, series, updated, residual), list(updated.values())


def _fault_or_none(sector: str, pressure: list[SensorSpec], series: dict, updated: dict,
                   residual: float) -> LeakFinding:
    bad = [s for s in pressure if updated[s.id].quarantined]
    if not bad:
        return LeakFinding(sector=sector, verdict="NONE", mass_balance_residual_lps=residual,
                           evidence=["no pressure anomaly, sensors healthy"])
    for s in bad:
        if updated[s.id].status == "FLATLINE" and is_drift(series[s.id], _healthy_neighbours(s, series, updated)):
            updated[s.id] = updated[s.id].model_copy(
                update={"evidence": updated[s.id].evidence + "; had drifted vs neighbours before sticking"})
    notes = [f"{s.id} {updated[s.id].status}: {updated[s.id].evidence}" for s in bad]
    return LeakFinding(sector=sector, verdict="SENSOR_FAULT", suspected_node=bad[0].location,
                       confidence=1.0, mass_balance_residual_lps=residual, evidence=notes)


# ---- action guards ---------------------------------------------------------------------------

def _check(name: str, passed: bool, detail: str, value: float | None = None,
           limit: float | None = None) -> Check:
    return Check(name=name, passed=passed, detail=detail, value=value, limit=limit)


def _report(checks: list[Check]) -> GuardReport:
    return GuardReport(passed=all(c.passed for c in checks), checks=checks)


def _project_peak(state: dict, production_m3h: float) -> list[float]:
    hours = range(config.PEAK_START_H, config.PEAK_END_H)
    r = state["reservoir"]
    return project_reservoir(r["level_pct"], [production_m3h] * len(hours),
                             [state["demand_forecast"][h] for h in hours], r["capacity_m3"])


def check_setpoint(req: ActionRequest, state: dict) -> GuardReport:
    plant, value = state["plant"], req.target_value
    scada_stale = state.get("scada_stale", False)
    if scada_stale:
        return _report([
            _check("telemetry_freshness", False,
                   "SCADA telemetry quiet (>10 min loss). Proposing setpoint changes is blocked while data is stale. Holding last safe state.")
        ])

    fire_reserve_m3 = state.get("fire_reserve_m3", 0.0)
    capacity_m3 = state.get("reservoir", {}).get("capacity_m3", config.RESERVOIR_CAPACITY_M3)
    floor = effective_floor(state["bloom_active"], state["intake_trusted"],
                            fire_reserve_m3, capacity_m3)
    why_parts = []
    if state["bloom_active"]:
        why_parts.append("bloom active")
    if not state["intake_trusted"]:
        why_parts.append("intake sensor untrusted")
    if fire_reserve_m3 > 0:
        why_parts.append(f"fire reserve {fire_reserve_m3:,.0f} m3")
    why = ", ".join(why_parts) if why_parts else "base reserve"
    levels = _project_peak(state, value)
    low, high = min(levels), max(levels)
    risky = value > plant["setpoint_m3h"] and (state["bloom_active"] or not state["intake_trusted"])

    # U-5.1: Water-quality floor in Python (Sector 2 chlorine residual < 0.2 mg/L)
    cl_s2 = state.get("cl_s2")
    cl_s2_ok = cl_s2 is None or cl_s2 >= config.CHLORINE_MIN_MGL
    non_compliant = state.get("non_compliant_sectors", [])
    if not cl_s2_ok and "S2" not in non_compliant:
        non_compliant = [*non_compliant, "S2"]
    quality_floor_ok = not non_compliant

    chlorine = state.get("cl_s4")
    chlorine_ok = chlorine is None or config.CHLORINE_MIN_MGL <= chlorine <= config.CHLORINE_MAX_MGL

    checks = [
        _check("bounds", plant["min_m3h"] <= value <= plant["max_m3h"],
               f"setpoint {value:.0f} m3/h vs plant limits [{plant['min_m3h']:.0f}, {plant['max_m3h']:.0f}]", value),
        _check("reserve_floor", low >= floor,
               f"projected min {low:.1f}% {'>=' if low >= floor else '<'} {floor:.1f}% floor "
               f"({config.RESERVE_FLOOR_PCT:.0f}% base + margin: {why})", low, floor),
        _check("reserve_ceiling", high <= config.RESERVOIR_MAX_PCT,
               f"projected max {high:.1f}% vs ceiling {config.RESERVOIR_MAX_PCT:.1f}%", high, config.RESERVOIR_MAX_PCT),
        _check("quality_floor", not risky,
               "intake not increased while a bloom is active or intake quality is untrusted" if not risky
               else "refusing to raise intake while a bloom is active or intake quality is untrusted"),
        _check("chlorine", chlorine_ok,
               "CL-S4 chlorine in range" if chlorine_ok else f"CL-S4 chlorine {chlorine} mg/L out of range", chlorine),
    ]
    if not quality_floor_ok:
        checks.append(_check("water_quality_floor", False,
                             f"Water quality floor violation: Sector {', '.join(non_compliant)} non-compliant "
                             f"(chlorine < {config.CHLORINE_MIN_MGL:.1f} mg/L floor). Water quality outranks tariff savings; "
                             f"tariff-driven setpoint changes are blocked.",
                             cl_s2, config.CHLORINE_MIN_MGL))
    return _report(checks)


def check_valve(req: ActionRequest, state: dict) -> GuardReport:
    scada_stale = state.get("scada_stale", False)
    if scada_stale:
        return _report([
            _check("telemetry_freshness", False,
                   "SCADA telemetry quiet (>10 min loss). Proposing valve changes is blocked while data is stale. Holding last safe state.")
        ])

    vid, value = req.target_id, req.target_value
    known = vid in state["valves"]
    checks = [_check("allowlist", known, f"{vid} is {'a known' if known else 'NOT a known'} valve"),
              _check("range", 0 <= value <= 100, f"position {value:.0f}% must be within 0-100", value)]
    if not known:
        return _report(checks)
    pipe = state["valve_pipe"][vid]
    if value < state["valves"][vid]:  # closing or throttling
        # Backed if genuine leak OR water-quality non-compliance requiring isolation
        leak_backed = any(f.verdict == "GENUINE_LEAK" and f.suspected_pipe == pipe for f in state["leak_findings"])
        quality_backed = (pipe == "P-S2-IN" and (state.get("cl_s2") is not None and state["cl_s2"] < config.CHLORINE_MIN_MGL
                                                 or "S2" in state.get("non_compliant_sectors", [])))
        backed = leak_backed or quality_backed
        checks.append(_check("leak_backing", backed,
                             f"{pipe} is {'the' if backed else 'NOT a'} confirmed genuine-leak or quality-isolation segment"))
        closed = {state["valve_pipe"][k] for k, pos in state["valves"].items() if pos <= 0}
        ok, unreachable = connectivity_ok(state["pipes"], closed | ({pipe} if value <= 0 else set()))
        checks.append(_check("connectivity", ok, "critical nodes stay supplied" if ok
                             else f"would cut supply to critical node(s) {', '.join(unreachable)}"))
    free = pipe not in state["pending_pipes"]
    checks.append(_check("single_pending", free, f"no other pending change on {pipe}" if free
                         else f"another change on {pipe} is already pending"))
    return _report(checks)


def check_isolation_and_flush(req: ActionRequest, state: dict) -> GuardReport:
    """U-5.1: Dedicated guard for isolation and flush actions staged through the gated write path."""
    scada_stale = state.get("scada_stale", False)
    if scada_stale:
        return _report([
            _check("telemetry_freshness", False,
                   "SCADA telemetry quiet (>10 min loss). Proposing isolation/flush is blocked while data is stale.")
        ])
    target = req.target_id
    valid_targets = {"S2", "SECTOR-2", "V-S2-IN"}
    is_valid = target in valid_targets or target in state.get("valves", {})
    checks = [_check("allowlist", is_valid, f"{target} is a valid sector or valve target for isolation/flush")]
    if not is_valid:
        return _report(checks)

    # Water-quality breach justification
    cl_s2 = state.get("cl_s2")
    has_breach = (cl_s2 is not None and cl_s2 < config.CHLORINE_MIN_MGL) or ("S2" in state.get("non_compliant_sectors", []))
    checks.append(_check("quality_floor", has_breach,
                         f"Sector 2 chlorine residual {cl_s2 if cl_s2 is not None else 0.0:.2f} mg/L < {config.CHLORINE_MIN_MGL:.1f} mg/L floor justifies isolation and flushing"
                         if has_breach else "no active water-quality breach justifies emergency isolation/flush"))

    # Connectivity to critical nodes
    pipe = "P-S2-IN"
    closed = {state["valve_pipe"][k] for k, pos in state["valves"].items() if pos <= 0}
    ok, unreachable = connectivity_ok(state["pipes"], closed | {pipe})
    checks.append(_check("connectivity", ok, "critical nodes (hospital J19) stay supplied" if ok
                         else f"would cut supply to critical node(s) {', '.join(unreachable)}"))
    return _report(checks)


def evaluate_action(req: ActionRequest, state: dict) -> GuardReport:
    if req.kind == "isolation_and_flush":
        return check_isolation_and_flush(req, state)
    return check_valve(req, state) if req.kind == "valve_position" else check_setpoint(req, state)
