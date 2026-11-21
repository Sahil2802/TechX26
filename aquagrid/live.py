"""Mission Control data: a per-minute timeline of what the guards concluded, plus the energy-water ROI.

Pure code, no Streamlit. Every number the live screen shows is computed here by the same guards and
scheduler the agent uses (tools.analyze replayed at earlier minutes). The agent feed only narrates it.
"""
import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path

from aquagrid import config, db, guards, profiles, scheduler, seed_data, tools

ASSETS = Path(__file__).parent / "mission"
FRAME_START = 15  # first replayed minute (12:15): enough samples for the flatline/drift windows
LAST_MINUTE = seed_data.MINUTES - 1


def ts(minute: int) -> str:
    return (seed_data.START + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M")


def roi(level_pct: float, demand: dict[int, float], tariff: list[float], plant: dict,
        floor_pct: float, capacity_m3: float, s4_loss_lps: float = 0.0) -> dict:
    """Peak-tariff kWh the plan saves without breaching the floor, and what the floor blocked."""
    hours = list(range(config.PEAK_START_H, config.PEAK_END_H))
    kwh_m3 = plant["kwh_per_m3"]
    daily = [demand[h] for h in range(24)]
    schedule = scheduler.build_schedule(level_pct, daily, tariff, plant, floor_pct, capacity_m3,
                                        start_hour=config.PEAK_START_H)
    opportunity = (plant["setpoint_m3h"] - config.THROTTLE_CANDIDATE_M3H) * len(hours) * kwh_m3
    vetoed = bool(schedule.rejected_candidates)
    banked = schedule.kwh_saved
    rest = list(range(config.PEAK_START_H, 24))
    throttle = scheduler.throttle_levels(level_pct, [demand[h] for h in rest], rest, plant["max_m3h"], capacity_m3)
    return {
        "opportunity_kwh": round(opportunity), "banked_kwh": round(banked),
        "blocked_kwh": round(max(opportunity - banked, 0.0)) if vetoed else 0,
        "vetoed": vetoed, "aed_saved": round(banked * tariff[config.PEAK_START_H]),
        "peak_aed_kwh": tariff[config.PEAK_START_H],
        "min_level_pct": round(min(s.projected_level_pct for s in schedule.slots), 1),
        "throttle_min_pct": round(min(throttle), 1), "floor_pct": floor_pct,
        "leak_loss_m3h": round(s4_loss_lps * 3.6), "leak_kwh_per_h": round(s4_loss_lps * 3.6 * kwh_m3),
    }


def _leak_sensors(finding, series: dict, health: dict, specs: list) -> list[str]:
    """Pressure sensors that moved with a confirmed leak (>= 30 % of the worst drop) plus the sector inlet."""
    if finding.verdict != "GENUINE_LEAK":
        return []
    sector_specs = [s for s in specs if s.sector == finding.sector]
    drops = {s.id: guards._drop(series[s.id]) for s in sector_specs
             if s.kind == "pressure" and not health[s.id].quarantined}
    worst = max(drops.values(), default=0.0)
    hit = [i for i, d in drops.items() if d >= max(config.NEIGHBOUR_DROP_FRACTION * worst, 3.0)]
    return hit + [s.id for s in sector_specs if s.kind == "flow"]


def _frame(conn, scenario: str, m: int, ctx: dict) -> dict:
    a = tools.analyze(conn, now=ts(m), scenario=scenario)
    floor = guards.effective_floor(a["bloom_active"], a["intake_trusted"], ctx["fire_m3"], ctx["capacity"])
    level = a["series"]["L-R1"][-1]
    leaky = next(f for f in a["findings"] if f.sector == profiles.get().leak_sector)
    loss = leaky.mass_balance_residual_lps if leaky.verdict == "GENUINE_LEAK" else 0.0
    leak_ids = [i for f in a["findings"] for i in _leak_sensors(f, a["series"], a["health"], a["specs"])]
    return {
        "m": m, "level": round(level, 2), "floor": floor, "bloom": a["bloom_active"], "trusted": a["intake_trusted"],
        "sectors": {f.sector: {"v": f.verdict, "pipe": f.suspected_pipe, "node": f.suspected_node,
                               "valve": f.suspected_valve, "conf": round(f.confidence, 2),
                               "resid": round(f.mass_balance_residual_lps, 1), "ev": f.evidence}
                    for f in a["findings"]},
        "bad": {i: [h.status, h.evidence] for i, h in a["health"].items() if h.quarantined},
        "leak": leak_ids,
        "roi": roi(level, a["demand_forecast"], ctx["tariff"], ctx["plant"], floor, ctx["capacity"], loss),
    }


def _minute(stamp: str) -> int:
    return int((datetime.fromisoformat(stamp) - seed_data.START).total_seconds() // 60)


def _alerts(conn, scenario: str) -> list[dict]:
    """The alert feed through the sanitizer, exactly as the LLM would see it. Flags = redacted injection."""
    out = []
    for r in conn.execute("SELECT ts, source, kind, severity, message FROM quality_alerts WHERE scenario=? ORDER BY ts",
                          (scenario,)):
        clean, flags = guards.sanitize_text(r["message"])
        out.append({"m": _minute(r["ts"]), "kind": r["kind"], "sev": r["severity"], "src": r["source"],
                    "raw": r["message"], "clean": clean, "flags": flags})
    return out


def _event(m: int, actor: str, level: str, tag: str, text: str) -> dict:
    return {"m": m, "ts": ts(m)[-5:], "actor": actor, "level": level, "tag": tag, "text": text}


def _diff_events(prev: dict | None, cur: dict) -> list[dict]:
    """What changed between two minutes, as CODE facts each followed by the AGENT's reading of them."""
    m, out = cur["m"], []
    for sid, (status, why) in cur["bad"].items():
        if prev is None or sid not in prev["bad"]:
            out.append(_event(m, "CODE", "warn", "QUARANTINE", f"{sid} {status}: {why}"))
            out.append(_event(m, "AGENT", "info", "REASON",
                              f"{sid} excluded from every calculation; its readings no longer count as evidence."))
    for sector, f in cur["sectors"].items():
        before = prev["sectors"][sector]["v"] if prev else "NONE"
        if f["v"] == before:
            continue
        if f["v"] == "GENUINE_LEAK":
            out.append(_event(m, "CODE", "alarm", "LEAK", f"{sector} genuine leak at {f['pipe']} "
                              f"(isolating valve {f['valve']}); " + "; ".join(f["ev"])))
            out.append(_event(m, "AGENT", "info", "REASON",
                              "Pressure drop, inlet-flow rise, mass-balance residual and a neighbour all agree: "
                              f"this is a leak, not a sensor. Drafting a close of {f['valve']} for the operator; "
                              f"closing the sector inlet would cut off the {profiles.get().critical_label}, "
                              "so I will not propose it."))
        elif f["v"] == "SENSOR_FAULT":
            out.append(_event(m, "CODE", "warn", "SENSOR-FAULT",
                              f"{sector} sensor fault near {f['node']}; no valve action allowed on this evidence"))
            out.append(_event(m, "AGENT", "info", "REASON",
                              f"{sector}: flow and neighbours are normal, so the pressure reading is the outlier. "
                              "Recommending a maintenance check, not a valve move."))
    if prev and cur["floor"] != prev["floor"]:
        why = "algal-bloom alert" if cur["bloom"] else "intake untrusted"
        out.append(_event(m, "CODE", "warn", "FLOOR",
                          f"effective reserve floor {prev['floor']:.0f}% -> {cur['floor']:.0f}% ({why})"))
    r, p = cur["roi"], (prev["roi"] if prev else None)
    if r["vetoed"] and not (p and p["vetoed"]):
        out.append(_event(m, "CODE", "alarm", "VETO", f"throttle to {config.THROTTLE_CANDIDATE_M3H:.0f} m3/h would "
                          f"reach {r['throttle_min_pct']:.1f}% < {r['floor_pct']:.0f}% floor: "
                          f"{r['blocked_kwh']:,} kWh of peak savings blocked"))
        out.append(_event(m, "AGENT", "info", "REASON",
                          "The reservoir floor outranks tariff savings. Holding the plant and re-planning inside "
                          f"the floor; {r['banked_kwh']:,} kWh is still achievable."))
    return out


def _alert_events(alerts: list[dict]) -> list[dict]:
    out = []
    for a in alerts:
        out.append(_event(a["m"], "FEED", "warn", "ALERT", f"{a['kind']} ({a['sev']}) from {a['src']}"))
        if a["flags"]:
            out.append(_event(a["m"], "CODE", "sec", "INJECTION",
                              f"instruction-like text in alert redacted by sanitizer, flags {a['flags']}; "
                              "treated as untrusted data, never executed"))
            out.append(_event(a["m"], "AGENT", "info", "REASON",
                              "Alert text contained commands addressed to me. Ignoring them: only the typed "
                              "gateway can stage actions and only a human can approve."))
    return out


def build_timeline(scenario: str, run_rows: list[dict] | None = None) -> dict:
    """Everything the Mission Control screen replays for one scenario (read-only DB)."""
    prof = profiles.get()
    conn = db.connect(ro=True)
    try:
        specs = tools._specs(conn)
        full = tools._series(conn, scenario, [s.id for s in specs], None, ts(LAST_MINUTE))
        try:
            fire_m3 = float(db.get_fire_reserve(conn)["volume_m3"])
        except Exception:
            fire_m3 = 0.0
        ctx = {"fire_m3": fire_m3, "capacity": conn.execute("SELECT capacity_m3 FROM reservoir").fetchone()[0],
               "plant": dict(conn.execute("SELECT * FROM plant").fetchone()),
               "tariff": [r[0] for r in conn.execute("SELECT aed_per_kwh FROM tariff ORDER BY hour")]}
        frames = [_frame(conn, scenario, m, ctx) for m in range(FRAME_START, seed_data.MINUTES)]
        pipes = [dict(r) for r in conn.execute("SELECT id, from_node, to_node, valve_id FROM pipes")]
        valves = {r["id"]: r["position_pct"] for r in conn.execute("SELECT id, position_pct FROM valves")}
        alerts = _alerts(conn, scenario)
    finally:
        conn.close()
    events = _alert_events(alerts)
    for prev, cur in zip([None] + frames, frames):
        events += _diff_events(prev, cur)
    name, desc = prof.scenarios[scenario]
    return {
        "scenario": scenario, "name": name, "desc": desc, "first": FRAME_START, "last": LAST_MINUTE,
        "times": [ts(m)[-5:] for m in range(seed_data.MINUTES)],
        "specs": [{"id": s.id, "kind": s.kind, "loc": s.location, "sector": s.sector} for s in specs],
        "series": {k: [round(v, 2) for v in vals] for k, vals in full.items()},
        "frames": frames, "events": sorted(events, key=lambda e: e["m"]),
        "alerts": alerts, "pipes": pipes, "valves": valves,
        "run": run_rows or [], "base_floor": config.RESERVE_FLOOR_PCT,
        "story": {"sectors": list(prof.sectors), "leak_sector": prof.leak_sector, "loop_sector": prof.loop_sector,
                  "critical_node": prof.critical_node, "critical_label": prof.critical_label, "intake_valve": prof.intake_valve, "plant_id": prof.plant_id},
        "peak": [config.PEAK_START_H, config.PEAK_END_H],
    }


def to_json(timeline: dict) -> str:
    """JSON safe to embed in an HTML <script> block."""
    return json.dumps(timeline, separators=(",", ":")).replace("</", "<\\/")


def render(timeline: dict) -> str:
    """The self-contained Mission Control page for one timeline (css + js + data inlined)."""
    page = (ASSETS / "template.html").read_text(encoding="utf-8")
    for marker, name in (("/*CSS*/", "mission.css"), ("/*JS*/", "mission.js")):
        page = page.replace(marker, (ASSETS / name).read_text(encoding="utf-8"))
    return page.replace("/*DATA*/", to_json(timeline))  # data last: it can never inject a marker


def summarize(timeline: dict) -> dict:
    """Small facts at the last minute, used by tests."""
    last = timeline["frames"][-1]
    return {"verdicts": {s: f["v"] for s, f in last["sectors"].items()}, "floor": last["floor"],
            "quarantined": sorted(last["bad"]), "roi": last["roi"],
            "mean_level": statistics.fmean(f["level"] for f in timeline["frames"])}
