"""Pre-staging hydraulics check for a valve closure: Joukowsky surge + steady-state pressure afterwards.

Pure Python does the surge and the connectivity. WNTR supplies the pressures when it loads; if it cannot
(missing, or blocked by the OS), the same check still runs with method="joukowsky_fallback" and says why.
Read-only: it never touches the database, valves or plant.
"""
import math

from aquagrid import config
from aquagrid.guards import connectivity_ok, reachable_nodes
from aquagrid.schemas import ActionRequest, Check, SimReport

GRAVITY = 9.81
ROUGHNESS_HW = 100  # Hazen-Williams C for the synthetic mains


def joukowsky_surge_m(length_m: float, delta_v_ms: float, closure_s: float) -> float:
    """Surge head (m) for slowing the flow by delta_v over closure_s seconds.

    Rapid closure (<= 2L/a, the wave's round trip): full Joukowsky a*dv/g. Slower: Michaud 2*L*dv/(g*T)."""
    if delta_v_ms <= 0:
        return 0.0
    if closure_s <= 2 * length_m / config.WAVE_SPEED_MS:
        return config.WAVE_SPEED_MS * delta_v_ms / GRAVITY
    return 2 * length_m * delta_v_ms / (GRAVITY * closure_s)


def _area_m2(pipe: dict) -> float:
    return math.pi * (pipe["diameter_mm"] / 2000) ** 2


def _build_model(wntr, state: dict, closed: set[str]):
    """WNTR model of only what the source can still reach over open pipes."""
    reach = reachable_nodes(state["pipes"], closed)
    wn = wntr.network.WaterNetworkModel()
    wn.add_reservoir("R1", base_head=config.RESERVOIR_HEAD_M)
    for j in state["junctions"]:
        if j["id"] in reach:
            wn.add_junction(j["id"], base_demand=j["base_demand_lps"] / 1000, elevation=j["elevation_m"])
    for p in state["pipes"]:
        if p["id"] not in closed and p["from_node"] in reach and p["to_node"] in reach:
            wn.add_pipe(p["id"], p["from_node"], p["to_node"], length=p["length_m"],
                        diameter=p["diameter_mm"] / 1000, roughness=ROUGHNESS_HW)
    return wn


def _run_wntr(state: dict, closed_now: set[str], closed_after: set[str], pipe_id: str) -> tuple[float, dict]:
    """(flow in the pipe before the change in m3/s, junction pressures in m after it). Raises if WNTR is unusable."""
    import wntr.sim  # may raise ImportError, including an OS application-control block

    before = wntr.sim.WNTRSimulator(_build_model(wntr, state, closed_now)).run_sim()
    flows = before.link["flowrate"]
    flow = abs(float(flows[pipe_id].iloc[-1])) if pipe_id in flows else 0.0
    after = wntr.sim.WNTRSimulator(_build_model(wntr, state, closed_after)).run_sim()
    pressures = after.node["pressure"].iloc[-1]
    junctions = {j["id"] for j in state["junctions"]}
    return flow, {n: float(p) for n, p in pressures.items() if n in junctions}


def _fallback(state: dict, pipe: dict, reach: set[str]) -> tuple[float, dict]:
    """Upper-bound flow (the whole sector's demand through this pipe) and static-head pressures."""
    sector = next((j["sector"] for j in state["junctions"] if j["id"] == pipe["to_node"]), None)
    flow = sum(j["base_demand_lps"] for j in state["junctions"] if j["sector"] == sector) / 1000
    return flow, {j["id"]: config.RESERVOIR_HEAD_M - j["elevation_m"] for j in state["junctions"] if j["id"] in reach}


def assess_valve_change(req: ActionRequest, state: dict) -> SimReport | None:
    """Simulate a valve being closed or throttled. None when the change opens a valve or is not a valve change."""
    if req.kind != "valve_position" or req.target_id not in state["valves"]:
        return None
    current = state["valves"][req.target_id]
    if req.target_value >= current:
        return None
    pipe_id = state["valve_pipe"][req.target_id]
    pipe = next(p for p in state["pipes"] if p["id"] == pipe_id)
    closed_now = {state["valve_pipe"][v] for v, pos in state["valves"].items() if pos <= 0}
    closed_after = closed_now | ({pipe_id} if req.target_value <= 0 else set())
    reach = reachable_nodes(state["pipes"], closed_after)
    critical_ok, _ = connectivity_ok(state["pipes"], closed_after)

    method, note = "wntr", ""
    try:
        flow, pressures = _run_wntr(state, closed_now, closed_after, pipe_id)
    except Exception as e:  # WNTR is optional: any load or solve failure degrades to the plain-Python check
        method, note = "joukowsky_fallback", f" WNTR unavailable ({e}); static head, friction ignored."
        flow, pressures = _fallback(state, pipe, reach)

    closure_s = config.DEFAULT_CLOSURE_S
    delta_v = flow / _area_m2(pipe) * (current - req.target_value) / 100
    surge = joukowsky_surge_m(pipe["length_m"], delta_v, closure_s)
    node, low = min(pressures.items(), key=lambda kv: kv[1]) if pressures else ("none", 0.0)
    return SimReport(
        method=method, min_pressure_m=round(low, 2), min_pressure_node=node, surge_head_m=round(surge, 2),
        closure_s=closure_s, critical_nodes_ok=critical_ok,
        notes=f"Steady-state pressure after the change; surge assumes a {closure_s} s closure, "
              f"closing faster raises it.{note}")


def sim_check(sim: SimReport) -> Check:
    """The sim as a guard check, so the gateway blocks on it and re-runs it at approval."""
    ok = (sim.min_pressure_m >= config.MIN_SERVICE_PRESSURE_M and sim.surge_head_m <= config.MAX_SURGE_M
          and sim.critical_nodes_ok)
    detail = (f"{sim.method}: min pressure {sim.min_pressure_m:.1f} m at {sim.min_pressure_node} "
              f"(>= {config.MIN_SERVICE_PRESSURE_M:g}), surge {sim.surge_head_m:.1f} m "
              f"(<= {config.MAX_SURGE_M:g}) over a {sim.closure_s} s closure")
    return Check(name="hydraulics", passed=ok, detail=detail, value=sim.surge_head_m, limit=config.MAX_SURGE_M)
