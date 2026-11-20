"""LOCKED demo numbers (frozen at HC1). If this fails, someone retuned the story by accident."""
from aquagrid import config, db as dbm, seed_data

PEAK_HOURS = range(config.PEAK_START_H, config.PEAK_END_H)


def _level_after_peak(start_pct: float, production_m3h: float, demand: dict[int, float]) -> float:
    m3 = start_pct / 100 * config.RESERVOIR_CAPACITY_M3
    for h in PEAK_HOURS:
        m3 += production_m3h - demand[h]
    return round(m3 / config.RESERVOIR_CAPACITY_M3 * 100, 1)


def _peak_inputs(db):
    seed_data.set_scenario(dbm.connect(), "peak_2pm")
    conn = dbm.connect(ro=True)
    r1 = conn.execute("SELECT level_pct FROM reservoir WHERE id='R1'").fetchone()[0]
    plant = conn.execute("SELECT setpoint_m3h FROM plant").fetchone()[0]
    demand = {r["hour"]: r["demand_m3h"] for r in conn.execute(
        "SELECT hour, demand_m3h FROM demand_forecast WHERE scenario='peak_2pm'")}
    return r1, plant, demand


def test_locked_reservoir_start_is_44(db):
    assert _peak_inputs(db)[0] == 44.0


def test_locked_hold_projects_to_43_0(db):
    r1, plant, demand = _peak_inputs(db)
    assert plant == config.PLANT_HOLD_M3H == 1900.0
    assert _level_after_peak(r1, plant, demand) == 43.0


def test_locked_throttle_projects_to_29_0(db):
    r1, _, demand = _peak_inputs(db)
    assert config.THROTTLE_CANDIDATE_M3H == 500.0
    assert _level_after_peak(r1, config.THROTTLE_CANDIDATE_M3H, demand) == 29.0


def test_locked_effective_floor_is_40():
    assert config.RESERVE_FLOOR_PCT + config.BLOOM_RESERVE_MARGIN_PCT == 40.0
