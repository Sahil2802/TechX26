"""The two LLM-facing read tools, run against the real seeded scenarios."""
import pytest

from aquagrid import db as dbm, seed_data, tools

MAX_RESULT_BYTES = 6000


def _load(scenario):
    seed_data.set_scenario(dbm.connect(), scenario)
    telemetry, feeds = tools.read_network_telemetry(), tools.read_tariff_and_quality_feeds()
    return telemetry, feeds, {f.sector: f for f in telemetry.leak_findings}, {s["id"]: s for s in telemetry.sensors}


def test_baseline_has_zero_false_positives(db):
    _, feeds, findings, sensors = _load("baseline")
    assert all(f.verdict == "NONE" for f in findings.values())
    assert all(s["health"] == "OK" and not s["quarantined"] for s in sensors.values())
    assert feeds.effective_floor_pct == 35.0 and feeds.intake_quality["trusted"] and not feeds.bloom_active


def test_peak_leak_is_localised_by_code(db):
    _, _, findings, _ = _load("peak_2pm")
    s4 = findings["S4"]
    assert s4.verdict == "GENUINE_LEAK" and (s4.suspected_node, s4.suspected_pipe) == ("J17", "P-J17-J18")
    assert s4.mass_balance_residual_lps > 30
    assert s4.suspected_valve == "V-S4-17-18"  # the model must be able to act on the finding using tool output alone
    assert all(f.suspected_valve is None for f in findings.values() if f.sector != "S4")


def test_peak_quarantines_the_faulty_sensors(db):
    _, feeds, findings, sensors = _load("peak_2pm")
    assert sensors["P-J07"]["health"] == "IMPOSSIBLE" and sensors["P-J07"]["quarantined"]
    assert sensors["T-INTAKE"]["health"] == "FLATLINE" and sensors["T-INTAKE"]["quarantined"]
    assert findings["S2"].verdict == "SENSOR_FAULT" and findings["S2"].suspected_node == "J07"  # J07 is in S2
    assert not feeds.intake_quality["trusted"] and feeds.bloom_active and feeds.effective_floor_pct == 40.0
    assert all(not sensors[f"P-J{n}"]["quarantined"] for n in (16, 17, 18, 19, 20))  # the leak is real


def test_peak_schedule_carries_the_rejected_throttle_with_its_floor_number(db):
    _, feeds, _, _ = _load("peak_2pm")
    cand = feeds.schedule.rejected_candidates[0]
    assert cand.min_level_pct == 29.0 and "29.0% < 40.0% floor" in cand.reason
    assert feeds.reservoir_level_pct == 44.0 and feeds.current_band == "peak"


def test_s4_drift_is_a_sensor_fault_not_a_leak(db):
    _, feeds, findings, sensors = _load("s4_drift")
    assert findings["S4"].verdict == "SENSOR_FAULT" and findings["S4"].suspected_node == "J17"
    assert sensors["P-J17"]["quarantined"] and sensors["P-J17"]["health"] in ("DRIFT", "FLATLINE")
    assert all(f.verdict != "GENUINE_LEAK" for f in findings.values())
    assert not feeds.bloom_active and feeds.effective_floor_pct == 35.0


def test_attack_alert_is_redacted_and_wrapped_as_untrusted(db):
    _, feeds, findings, _ = _load("attack")
    alert = feeds.alerts[0]
    assert alert["flags"] and "[REDACTED: instruction-like text]" in alert["message"]
    assert alert["message"].startswith("<untrusted_data>") and alert["message"].endswith("</untrusted_data>")
    assert "IGNORE PREVIOUS" not in feeds.model_dump_json() and "approved=true" not in feeds.model_dump_json()
    assert findings["S4"].verdict == "GENUINE_LEAK"  # same physical story as peak_2pm


@pytest.mark.parametrize("scenario", ["baseline", "peak_2pm", "s4_drift", "attack"])
def test_tool_results_stay_under_6kb(db, scenario):
    telemetry, feeds, _, _ = _load(scenario)
    assert len(telemetry.model_dump_json()) < MAX_RESULT_BYTES
    assert len(feeds.model_dump_json()) < MAX_RESULT_BYTES


def test_sector_filter_returns_only_that_sector(db):
    seed_data.set_scenario(dbm.connect(), "peak_2pm")
    t = tools.read_network_telemetry("S4", 15)
    assert {s["id"] for s in t.sensors} >= {"P-J17", "F-S4"} and all(not s["id"].startswith("P-J07") for s in t.sensors)
    assert [f.sector for f in t.leak_findings] == ["S4"]


@pytest.mark.parametrize("kwargs", [{"sector": "S9"}, {"window_min": 3}, {"window_min": 500}])
def test_bad_inputs_are_rejected(db, kwargs):
    seed_data.set_scenario(dbm.connect(), "baseline")
    with pytest.raises(ValueError):
        tools.read_network_telemetry(**kwargs)


def test_read_tools_only_ever_open_read_only_connections(db, monkeypatch):
    seed_data.set_scenario(dbm.connect(), "baseline")
    opened = []
    real = dbm.connect
    monkeypatch.setattr(tools.db, "connect", lambda ro=False: opened.append(ro) or real(ro=ro))
    tools.read_network_telemetry()
    tools.read_tariff_and_quality_feeds()
    assert opened and all(opened)
