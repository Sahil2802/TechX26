from aquagrid import config
from aquagrid.scheduler import build_schedule

PLANT = {"setpoint_m3h": 1900.0, "min_m3h": 0.0, "max_m3h": 2000.0, "kwh_per_m3": 4.0}
TARIFF = [0.23] * 7 + [0.38] * 7 + [0.68] * 4 + [0.38] * 4 + [0.23] * 2
DEMAND_PEAK = [1700.0] * 14 + [2000.0] * 4 + [1700.0] * 6


def _schedule(level=44.0, floor=40.0, demand=DEMAND_PEAK):
    return build_schedule(level, demand, TARIFF, PLANT, floor, 40_000.0)


def test_slots_cover_the_rest_of_the_day():
    assert [s.hour for s in _schedule().slots] == list(range(14, 24))


def test_schedule_stays_above_floor_plus_buffer_and_ends_no_lower_than_it_started():
    s = _schedule()
    levels = [slot.projected_level_pct for slot in s.slots]
    assert min(levels) >= 40.0 + config.SCHEDULE_BUFFER_PCT
    assert levels[-1] >= 44.0


def test_peak_hours_are_throttled_below_off_peak():
    slots = {s.hour: s.production_m3h for s in _schedule().slots}
    assert max(slots[h] for h in range(14, 18)) < slots[23]


def test_named_throttle_candidate_is_rejected_with_the_exact_reason():
    cand = _schedule().rejected_candidates[0]
    assert cand.name == "peak throttle to 500 m3/h (14-18h)"
    assert cand.min_level_pct == 29.0
    assert cand.reason.startswith("min projected 29.0% < 40.0% floor")
    assert "5% margin" in cand.reason


def test_throttle_is_not_rejected_when_the_reservoir_is_full_enough():
    assert _schedule(level=70.0, floor=35.0, demand=[1700.0] * 14 + [1850.0] * 4 + [1700.0] * 6
                     ).rejected_candidates == []


def test_cost_and_peak_energy_savings_are_reported():
    s = _schedule()
    assert 0 < s.cost_aed < s.baseline_cost_aed
    assert s.kwh_saved > 0
