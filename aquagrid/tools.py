"""Read-side data access plus the deterministic pre-analysis (stage 2).

The two LLM-facing read tools open the database with mode=ro, so a write raises
sqlite3.OperationalError. They return short summaries and code verdicts, never raw arrays.
"""
import json
import sqlite3
from datetime import datetime

from aquagrid import config, db, guards, profiles, scheduler
from aquagrid.schemas import NetworkTelemetry, SensorSpec, TariffQualityFeed

def _scenario(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key='scenario'").fetchone()
    if row is None:
        raise RuntimeError("no scenario loaded: call seed_data.set_scenario first")
    return row["value"]


def _series(conn: sqlite3.Connection, scenario: str, sensor_ids: list[str] | None,
            since: str | None, until: str) -> dict[str, list[float]]:
    """Values per sensor, oldest first, for scenario within [since, until]."""
    sql, args = "SELECT sensor_id, value FROM telemetry WHERE scenario=? AND ts<=?", [scenario, until]
    if since:
        sql, args = sql + " AND ts>=?", args + [since]
    out: dict[str, list[float]] = {} if sensor_ids is None else {s: [] for s in sensor_ids}
    for r in conn.execute(sql + " ORDER BY ts", args):
        if sensor_ids is None or r["sensor_id"] in out:
            out.setdefault(r["sensor_id"], []).append(r["value"])
    return out


def _specs(conn: sqlite3.Connection) -> list[SensorSpec]:
    return [SensorSpec(id=r["id"], kind=r["kind"], location=r["location"], sector=r["sector"],
                       neighbours=json.loads(r["neighbours_json"])) for r in conn.execute("SELECT * FROM sensors")]


def analyze(conn: sqlite3.Connection, now: str | None = None, scenario: str | None = None) -> dict:
    """Stage-2 pre-analysis in code: sensor health, leak-vs-fault per sector, intake trust, bloom.

    now/scenario default to the live demo clock and the loaded scenario; Mission Control's replay passes
    earlier minutes to see what the same guards concluded back then."""
    scenario, now = scenario or _scenario(conn), now or config.DEMO_NOW
    specs = _specs(conn)
    series = _series(conn, scenario, [s.id for s in specs], None, now)
    last_ts = {r[0]: r[1] for r in conn.execute(
        "SELECT sensor_id, max(ts) FROM telemetry WHERE scenario=? AND ts<=? GROUP BY sensor_id", (scenario, now))}
    health = {s.id: guards.check_sensor(s, series[s.id], last_ts[s.id], now) for s in specs}
    expected = {r[0]: r[1] for r in conn.execute("SELECT sector, sum(base_demand_lps) FROM junctions GROUP BY sector")}
    valve_of = {r["id"]: r["valve_id"] for r in conn.execute("SELECT id, valve_id FROM pipes WHERE valve_id IS NOT NULL")}
    findings = []
    for sector in profiles.get().sectors:
        finding, updated = guards.classify_sector(
            sector, [s for s in specs if s.sector == sector], series, health, expected[sector])
        findings.append(finding.model_copy(update={"suspected_valve": valve_of.get(finding.suspected_pipe)}))
        health.update({h.sensor_id: h for h in updated})
    alerts = [dict(r) for r in conn.execute(
        "SELECT ts, source, kind, severity, message FROM quality_alerts WHERE scenario=? AND ts<=? ORDER BY ts",
        (scenario, now))]
    cl_id = profiles.get().chlorine_sensor
    chlorine = series[cl_id][-1] if not health[cl_id].quarantined else None
    return {
        "scenario": scenario, "specs": specs, "series": series, "health": health, "findings": findings,
        "intake_trusted": not (health["T-INTAKE"].quarantined or health["CL-INTAKE"].quarantined),
        "bloom_active": any(a["kind"] == "algal_bloom" and a["severity"] in ("high", "critical") for a in alerts),
        "alerts": alerts, "cl_s4": chlorine,
        "demand_forecast": {r["hour"]: r["demand_m3h"] for r in conn.execute(
            "SELECT hour, demand_m3h FROM demand_forecast WHERE scenario=?", (scenario,))},
    }


def pre_analysis() -> dict:
    conn = db.connect(ro=True)
    try:
        return analyze(conn)
    finally:
        conn.close()


def load_state(conn: sqlite3.Connection, exclude_action_id: int | None = None) -> dict:
    """Live state plus code analysis, as the guards need it. exclude_action_id: an action being
    re-validated must not conflict with its own pending row."""
    a = analyze(conn)
    valve_pipe = {r["id"]: r["pipe_id"] for r in conn.execute("SELECT id, pipe_id FROM valves")}
    pending = [valve_pipe[r["target_id"]] for r in conn.execute(
        "SELECT target_id FROM actions WHERE status='PENDING_APPROVAL' AND kind='valve_position' AND id IS NOT ?",
        (exclude_action_id,))]
    # U-5.2: load fire reserve
    try:
        fire_row = db.get_fire_reserve(conn)
        fire_reserve_m3 = float(fire_row["volume_m3"])
    except Exception:
        fire_reserve_m3 = 0.0
    return {
        "valves": {r["id"]: r["position_pct"] for r in conn.execute("SELECT id, position_pct FROM valves")},
        "valve_pipe": valve_pipe,
        "plant": dict(conn.execute("SELECT * FROM plant").fetchone()),
        "reservoir": dict(conn.execute("SELECT * FROM reservoir").fetchone()),
        "pipes": [dict(r) for r in conn.execute("SELECT * FROM pipes")],
        "junctions": [dict(r) for r in conn.execute("SELECT * FROM junctions")],
        "scenario": a["scenario"], "leak_findings": a["findings"], "intake_trusted": a["intake_trusted"],
        "bloom_active": a["bloom_active"], "demand_forecast": a["demand_forecast"], "cl_s4": a["cl_s4"],
        "pending_pipes": pending,
        "fire_reserve_m3": fire_reserve_m3,  # U-5.2: passed to check_setpoint guard
    }


def read_network_telemetry(sector: str | None = None, window_min: int = 30) -> NetworkTelemetry:
    sectors = profiles.get().sectors
    if sector is not None and sector not in sectors:
        raise ValueError(f"sector must be one of {sectors} or omitted")
    if not 5 <= window_min <= 120:
        raise ValueError("window_min must be between 5 and 120")
    conn = db.connect(ro=True)
    try:
        a = analyze(conn)
        valves = {r["id"]: r["position_pct"] for r in conn.execute("SELECT id, position_pct FROM valves")}
    finally:
        conn.close()
    sensors = []
    for s in a["specs"]:
        if sector is not None and s.sector != sector:
            continue
        window, h = a["series"][s.id][-window_min:], a["health"][s.id]
        row = {"id": s.id, "kind": s.kind, "loc": s.location, "latest": round(window[-1], 2),
               "min": round(min(window), 2), "max": round(max(window), 2),
               "health": h.status, "quarantined": h.quarantined}
        if h.status != "OK":
            row["evidence"] = h.evidence
        sensors.append(row)
    findings = [f for f in a["findings"] if sector is None or f.sector == sector]
    return NetworkTelemetry(as_of=config.DEMO_NOW, sensors=sensors, leak_findings=findings, valves=valves)


def _intake_quality(a: dict) -> dict:
    def part(sid: str) -> dict:
        h = a["health"][sid]
        return {"status": h.status, "latest": round(a["series"][sid][-1], 3), "trusted": not h.quarantined}
    return {"turbidity": part("T-INTAKE"), "chlorine": part("CL-INTAKE"), "trusted": a["intake_trusted"]}


def read_tariff_and_quality_feeds() -> TariffQualityFeed:
    conn = db.connect(ro=True)
    try:
        a = analyze(conn)
        tariff = [dict(r) for r in conn.execute("SELECT hour, aed_per_kwh, band FROM tariff ORDER BY hour")]
        reservoir = dict(conn.execute("SELECT * FROM reservoir").fetchone())
        plant = dict(conn.execute("SELECT * FROM plant").fetchone())
        # U-5.2: load Civil Defence fire reserve (table may not exist on legacy DBs)
        try:
            fire_row = db.get_fire_reserve(conn)
            fire_reserve_m3 = float(fire_row["volume_m3"])
            fire_reserve_sector = fire_row["sector"]
        except Exception:
            fire_reserve_m3, fire_reserve_sector = 0.0, "S6"
    finally:
        conn.close()
    now_hour = datetime.fromisoformat(config.DEMO_NOW).hour
    floor = guards.effective_floor(a["bloom_active"], a["intake_trusted"],
                                   fire_reserve_m3, reservoir["capacity_m3"])
    schedule = scheduler.build_schedule(
        reservoir["level_pct"], [a["demand_forecast"][h] for h in range(24)], [t["aed_per_kwh"] for t in tariff],
        plant, floor, reservoir["capacity_m3"], start_hour=now_hour)
    alerts = []
    for al in a["alerts"]:
        clean, flags = guards.sanitize_text(al["message"])
        alerts.append({**{k: al[k] for k in ("ts", "source", "kind", "severity")},
                       "message": f"<untrusted_data>{clean}</untrusted_data>", "flags": flags})
    return TariffQualityFeed(
        as_of=config.DEMO_NOW, current_band=tariff[now_hour]["band"], tariff=tariff[now_hour:],
        reservoir_level_pct=reservoir["level_pct"], bloom_active=a["bloom_active"], effective_floor_pct=floor,
        fire_reserve_m3=fire_reserve_m3, fire_reserve_sector=fire_reserve_sector,
        alerts=alerts, intake_quality=_intake_quality(a), schedule=schedule)
