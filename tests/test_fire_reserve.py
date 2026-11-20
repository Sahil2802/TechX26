"""U-5.2: Civil Defence emergency fire reserve tests.

Verifies that a fire request raises the effective floor, that the tariff optimizer
recalculates against the higher floor, and that the guard blocks a setpoint that
would breach it.
"""
import pytest

from aquagrid import config, db as dbm, guards
from aquagrid.schemas import ActionRequest


CAPACITY = config.RESERVOIR_CAPACITY_M3  # 40_000 m3
FIRE_VOL = 2000.0                        # 2,000 m3 = 5.0% of capacity
FIRE_PCT = FIRE_VOL / CAPACITY * 100     # 5.0


# ---- effective_floor ---------------------------------------------------------

def test_fire_reserve_raises_floor_by_correct_pct():
    """2,000 m3 on a 40,000 m3 tank = exactly 5% extra floor."""
    base = guards.effective_floor(bloom_active=False, intake_trusted=True)
    with_fire = guards.effective_floor(bloom_active=False, intake_trusted=True,
                                       fire_reserve_m3=FIRE_VOL, capacity_m3=CAPACITY)
    assert abs(with_fire - (base + FIRE_PCT)) < 1e-6


def test_fire_reserve_stacks_on_top_of_bloom_margin():
    """Bloom (+5%) + fire (+5%) = 45% total floor."""
    floor = guards.effective_floor(bloom_active=True, intake_trusted=False,
                                   fire_reserve_m3=FIRE_VOL, capacity_m3=CAPACITY)
    expected = config.RESERVE_FLOOR_PCT + config.BLOOM_RESERVE_MARGIN_PCT + FIRE_PCT
    assert abs(floor - expected) < 1e-6


def test_zero_fire_reserve_does_not_change_floor():
    no_fire = guards.effective_floor(bloom_active=False, intake_trusted=True,
                                     fire_reserve_m3=0.0, capacity_m3=CAPACITY)
    base = guards.effective_floor(bloom_active=False, intake_trusted=True)
    assert abs(no_fire - base) < 1e-6


# ---- db helpers (uses real DB) -----------------------------------------------

def test_set_and_get_fire_reserve(db):
    conn = dbm.connect()
    try:
        dbm.set_fire_reserve(conn, FIRE_VOL, "S6", "Civil Defence", note="fire test")
        row = dbm.get_fire_reserve(conn)
    finally:
        conn.close()
    assert row["volume_m3"] == FIRE_VOL
    assert row["sector"] == "S6"
    assert row["requester"] == "Civil Defence"
    assert row["requested_ts"] is not None


def test_clear_fire_reserve(db):
    conn = dbm.connect()
    try:
        dbm.set_fire_reserve(conn, FIRE_VOL, "S6", "Civil Defence")
        dbm.set_fire_reserve(conn, 0.0, "S6", "Civil Defence", note="cleared")
        row = dbm.get_fire_reserve(conn)
    finally:
        conn.close()
    assert row["volume_m3"] == 0.0


# ---- guard integration -------------------------------------------------------

def _make_state(fire_m3: float = 0.0, bloom: bool = False) -> dict:
    """Minimal state dict as check_setpoint expects."""
    return {
        "plant": {"setpoint_m3h": config.PLANT_HOLD_M3H, "min_m3h": config.PLANT_MIN_M3H,
                  "max_m3h": config.PLANT_MAX_M3H, "kwh_per_m3": config.PLANT_KWH_PER_M3},
        "reservoir": {"level_pct": 44.0, "capacity_m3": CAPACITY},
        "bloom_active": bloom,
        "intake_trusted": True,
        "fire_reserve_m3": fire_m3,
        "demand_forecast": {h: 2000.0 for h in range(24)},
        "cl_s4": None,
    }


def test_guard_blocks_throttle_when_fire_reserve_raises_floor():
    """Throttling to 500 m3/h would drop to 29% — below 40% fire+bloom floor."""
    state = _make_state(fire_m3=FIRE_VOL)
    req = ActionRequest(kind="plant_setpoint", target_id="PLANT-1",
                        target_value=500.0, rationale="peak tariff")
    report = guards.check_setpoint(req, state)
    floor_check = next(c for c in report.checks if c.name == "reserve_floor")
    assert not report.passed
    assert not floor_check.passed
    assert "fire reserve" in floor_check.detail


def test_guard_passes_hold_production_with_fire_reserve():
    """Holding 1,900 m3/h stays comfortably above the raised floor."""
    state = _make_state(fire_m3=FIRE_VOL)
    req = ActionRequest(kind="plant_setpoint", target_id="PLANT-1",
                        target_value=1900.0, rationale="hold during fire reserve")
    report = guards.check_setpoint(req, state)
    floor_check = next(c for c in report.checks if c.name == "reserve_floor")
    assert floor_check.passed


def test_guard_detail_mentions_fire_reserve_volume():
    """The guard check detail text must name the fire reserve for operator visibility."""
    state = _make_state(fire_m3=FIRE_VOL)
    req = ActionRequest(kind="plant_setpoint", target_id="PLANT-1",
                        target_value=500.0, rationale="test")
    report = guards.check_setpoint(req, state)
    floor_check = next(c for c in report.checks if c.name == "reserve_floor")
    assert "fire reserve" in floor_check.detail
    assert "2,000" in floor_check.detail  # volume visible in human-readable output
