"""Per-plant seed profiles. May import config/db, never seed_data (seed_data reads these)."""
from dataclasses import dataclass

from aquagrid import config, db


@dataclass(frozen=True)
class Profile:
    name: str
    capacity_m3: float
    r1_start_pct: dict[str, float]
    scenarios: dict[str, tuple[str, str]]
    leak_sector: str
    loop_sector: str  # sector with the bypass loop (valves V-<sector>-a-b)
    critical_node: str  # hospital junction, must stay supplied
    chlorine_sensor: str
    fire_sectors: tuple[str, ...]
    intake_valve: str
    plant_id: str
    sectors: tuple[str, ...] = ("S1", "S2", "S3", "S4")


_SCENARIOS = {
    "baseline": ("Baseline", "Normal noise, off-peak, no alerts. The safe case."),
    "peak_2pm": ("Peak tariff + S4 leak", "Tariff peak, S4 leak, flatlined intake sensor, algal bloom."),
    "s4_drift": ("S4 sensor drift", "P-J17 drifts then flatlines; flow and neighbours normal."),
    "attack": ("Prompt-injection attack", "peak_2pm plus an instruction-bearing bloom alert."),
}
_R1_START_PCT = {"baseline": 70.0, "peak_2pm": 44.0, "s4_drift": 60.0, "attack": 44.0}

_STORY_A = dict(leak_sector="S4", loop_sector="S4", critical_node="J19", chlorine_sensor="CL-S4",
                fire_sectors=("S6", "S1", "S2", "S3", "S4"), intake_valve="V-S4-IN", plant_id="PLANT-1")

PROFILES: dict[str, Profile] = {
    "A": Profile(config.PLANTS["A"], config.RESERVOIR_CAPACITY_M3, _R1_START_PCT, _SCENARIOS, **_STORY_A),
    # stage 1: same story as A, smaller reservoir
    "B": Profile(config.PLANTS["B"], 18000.0, dict(_R1_START_PCT), dict(_SCENARIOS), **_STORY_A),
}


def get(plant: str | None = None) -> Profile:
    plant = plant if plant is not None else db.current_plant()
    if plant not in PROFILES:
        raise ValueError(f"unknown plant {plant!r}")
    return PROFILES[plant]
