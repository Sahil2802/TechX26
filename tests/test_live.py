"""Mission Control timeline: the replay must agree with the guards and with the locked demo numbers."""
import pytest

from aquagrid import live, tools


@pytest.fixture
def tl(db):
    return live.build_timeline


def test_replay_at_final_minute_matches_live_analysis(db, tl):
    """Replaying to 14:00 must give the same verdicts the agent sees (single source of truth)."""
    t = tl("peak_2pm")
    live_view = {f.sector: f.verdict for f in tools.pre_analysis()["findings"]}
    assert live.summarize(t)["verdicts"] == live_view


def test_peak_leak_appears_only_after_it_starts(db, tl):
    t = tl("peak_2pm")
    by_min = {f["m"]: f["sectors"]["S4"]["v"] for f in t["frames"]}
    assert by_min[100] == "NONE"            # 13:40, before the leak
    assert by_min[live.LAST_MINUTE] == "GENUINE_LEAK"


def test_bloom_raises_floor_and_vetoes_throttle(db, tl):
    t = tl("peak_2pm")
    end = t["frames"][-1]
    assert end["floor"] == 40.0 and end["roi"]["vetoed"] and end["roi"]["blocked_kwh"] > 0
    assert end["roi"]["throttle_min_pct"] == 29.0           # locked demo number
    assert any(e["tag"] == "VETO" for e in t["events"])


def test_baseline_banks_savings_without_veto(db, tl):
    end = tl("baseline")["frames"][-1]
    assert end["floor"] == 35.0 and not end["roi"]["vetoed"] and end["roi"]["banked_kwh"] > 0


def test_drift_is_a_sensor_fault_not_a_leak(db, tl):
    end = tl("s4_drift")["frames"][-1]
    assert end["sectors"]["S4"]["v"] == "SENSOR_FAULT" and "P-J17" in end["bad"]


def test_attack_injection_is_flagged_and_redacted(db, tl):
    t = tl("attack")
    assert t["alerts"][0]["flags"] and "IGNORE" not in t["alerts"][0]["clean"]
    assert any(e["tag"] == "INJECTION" for e in t["events"])


def test_embedded_json_cannot_close_script_tag(db, tl):
    assert "</" not in live.to_json(tl("attack"))
