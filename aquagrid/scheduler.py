"""Tariff-aware production schedule, computed in code (no LLM)."""
from aquagrid import config
from aquagrid.guards import project_reservoir
from aquagrid.schemas import RejectedCandidate, Schedule, ScheduleSlot

STEP_FRACTION = 0.1  # lower production in steps of 10% of plant max


def throttle_levels(level_pct: float, demand: list[float], hours: list[int], max_m3h: float,
                    capacity_m3: float) -> list[float]:
    """Reservoir level after each hour if the plant throttles during the peak window and runs flat out
    otherwise. Shared by the scheduler's veto and the operator console's chart."""
    production = [config.THROTTLE_CANDIDATE_M3H if config.PEAK_START_H <= h < config.PEAK_END_H else max_m3h
                  for h in hours]
    return project_reservoir(level_pct, production, demand, capacity_m3)


def _throttle_candidate(level_pct, demand, hours, max_m3h, floor_pct, capacity_m3) -> RejectedCandidate | None:
    """The named 'peak throttle' candidate. Floor includes bloom/intake margin + fire reserve (U-5.2)."""
    low = min(throttle_levels(level_pct, demand, hours, max_m3h, capacity_m3))
    if low >= floor_pct:
        return None
    margin = floor_pct - config.RESERVE_FLOOR_PCT
    basis = (f"{config.RESERVE_FLOOR_PCT:.0f}% base + {margin:.0f}% margin for bloom/untrusted intake"
             if margin > 0 else f"{config.RESERVE_FLOOR_PCT:.0f}% base")
    return RejectedCandidate(
        name=f"peak throttle to {config.THROTTLE_CANDIDATE_M3H:.0f} m3/h ({config.PEAK_START_H}-{config.PEAK_END_H}h)",
        reason=f"min projected {low:.1f}% < {floor_pct:.1f}% floor ({basis})", min_level_pct=low)


def build_schedule(level_pct: float, demand_forecast: list[float], tariff: list[float], plant: dict,
                   floor_pct: float, capacity_m3: float, start_hour: int = 14,
                   scada_stale: bool = False, non_compliant_sectors: list[str] | None = None) -> Schedule:
    # ponytail: greedy heuristic, swap for PuLP LP if schedules look poor
    hours = list(range(start_hour, 24))
    demand = [demand_forecast[h] for h in hours]
    price = [tariff[h] for h in hours]
    kwh = plant["kwh_per_m3"]
    baseline = sum(plant["setpoint_m3h"] * kwh * p for p in price)

    # U-5.5: While data is stale, pause tariff optimisation and hold safe baseline
    if scada_stale:
        levels = project_reservoir(level_pct, [plant["setpoint_m3h"]] * len(hours), demand, capacity_m3)
        candidate = RejectedCandidate(
            name=f"peak throttle to {config.THROTTLE_CANDIDATE_M3H:.0f} m3/h ({config.PEAK_START_H}-{config.PEAK_END_H}h)",
            reason="Tariff optimization paused: SCADA telemetry quiet (>10 min loss). Holding last safe setpoint.",
            min_level_pct=min(levels))
        return Schedule(
            slots=[ScheduleSlot(hour=h, tariff_aed_kwh=price[i], production_m3h=plant["setpoint_m3h"],
                                projected_level_pct=levels[i]) for i, h in enumerate(hours)],
            effective_floor_pct=floor_pct, cost_aed=baseline, baseline_cost_aed=baseline,
            kwh_saved=0.0, rejected_candidates=[candidate])

    # U-5.1: Water quality outranks tariff savings; remove non-compliant sector from tariff savings
    if non_compliant_sectors:
        levels = project_reservoir(level_pct, [plant["setpoint_m3h"]] * len(hours), demand, capacity_m3)
        sec_str = ", ".join(non_compliant_sectors)
        candidate = RejectedCandidate(
            name=f"peak throttle to {config.THROTTLE_CANDIDATE_M3H:.0f} m3/h ({config.PEAK_START_H}-{config.PEAK_END_H}h)",
            reason=f"Water quality in Sector {sec_str} (<0.2 mg/L chlorine) outranks tariff savings. Tariff throttle rejected.",
            min_level_pct=min(levels))
        return Schedule(
            slots=[ScheduleSlot(hour=h, tariff_aed_kwh=price[i], production_m3h=plant["setpoint_m3h"],
                                projected_level_pct=levels[i]) for i, h in enumerate(hours)],
            effective_floor_pct=floor_pct, cost_aed=baseline, baseline_cost_aed=baseline,
            kwh_saved=0.0, rejected_candidates=[candidate])

    step, floor_target = STEP_FRACTION * plant["max_m3h"], floor_pct + config.SCHEDULE_BUFFER_PCT
    production = [plant["max_m3h"]] * len(hours)

    def feasible(prod: list[float]) -> bool:
        levels = project_reservoir(level_pct, prod, demand, capacity_m3)
        return min(levels) >= floor_target and max(levels) <= config.RESERVOIR_MAX_PCT and levels[-1] >= level_pct

    order = sorted(range(len(hours)), key=lambda i: -price[i])  # most expensive hour first
    changed = feasible(production)  # if even full production fails, there is nothing to optimise
    while changed:
        changed = False
        for i in order:
            lowered = max(production[i] - step, plant["min_m3h"])
            trial = production[:i] + [lowered] + production[i + 1:]
            if lowered < production[i] and feasible(trial):
                production, changed = trial, True

    levels = project_reservoir(level_pct, production, demand, capacity_m3)
    peak = [i for i, h in enumerate(hours) if config.PEAK_START_H <= h < config.PEAK_END_H]
    cost = sum(production[i] * kwh * price[i] for i in range(len(hours)))
    saved = sum((plant["setpoint_m3h"] - production[i]) * kwh for i in peak)
    candidate = _throttle_candidate(level_pct, demand, hours, plant["max_m3h"], floor_pct, capacity_m3)
    return Schedule(
        slots=[ScheduleSlot(hour=h, tariff_aed_kwh=price[i], production_m3h=production[i],
                            projected_level_pct=levels[i]) for i, h in enumerate(hours)],
        effective_floor_pct=floor_pct, cost_aed=cost, baseline_cost_aed=baseline,
        kwh_saved=max(saved, 0.0), rejected_candidates=[candidate] if candidate else [])
