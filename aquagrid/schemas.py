"""Pydantic contracts between modules. ActionRequest is the ONLY thing the LLM may send."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SensorKind = Literal["pressure", "flow", "turbidity", "chlorine", "level"]
HealthStatus = Literal["OK", "DRIFT", "FLATLINE", "IMPOSSIBLE", "STALE"]
ActionStatus = Literal[
    "REJECTED_BY_GUARD", "BLOCKED", "PENDING_APPROVAL", "EXECUTED",
    "DENIED", "EXECUTION_FAILED", "REVERTED",
]


class SensorSpec(BaseModel):
    id: str
    kind: SensorKind
    location: str
    sector: str | None = None
    neighbours: list[str] = []


class SensorHealth(BaseModel):
    sensor_id: str
    status: HealthStatus
    quarantined: bool
    evidence: str
    metrics: dict[str, float] = {}


class LeakFinding(BaseModel):
    sector: str
    verdict: Literal["GENUINE_LEAK", "SENSOR_FAULT", "NONE"]
    suspected_pipe: str | None = None
    suspected_valve: str | None = None  # valve that isolates suspected_pipe (filled from topology by tools)
    suspected_node: str | None = None
    confidence: float = 0.0
    evidence: list[str] = []
    mass_balance_residual_lps: float = 0.0


class Check(BaseModel):
    name: str
    passed: bool
    detail: str
    value: float | None = None
    limit: float | None = None


class GuardReport(BaseModel):
    passed: bool
    checks: list[Check]


class ScheduleSlot(BaseModel):
    hour: int
    tariff_aed_kwh: float
    production_m3h: float
    projected_level_pct: float


class RejectedCandidate(BaseModel):
    name: str
    reason: str
    min_level_pct: float


class Schedule(BaseModel):
    slots: list[ScheduleSlot]
    effective_floor_pct: float
    cost_aed: float
    baseline_cost_aed: float
    kwh_saved: float
    rejected_candidates: list[RejectedCandidate]


class ActionRequest(BaseModel):
    """What the LLM may send. NOTHING else: no approved/execute_now field exists."""
    model_config = ConfigDict(extra="forbid")

    kind: Literal["valve_position", "plant_setpoint", "isolation_and_flush"]
    target_id: str = Field(pattern=r"^[A-Z0-9-]{2,20}$")
    target_value: float = 0.0  # valve: 0-100 % open; plant: m3/h; isolation_and_flush: 0
    rationale: str = Field(max_length=600)
    evidence: list[str] = Field(default=[], max_length=10)


class ApprovalTokenRecord(BaseModel):
    """U-5.4: Single-use, parameter-bound, expiring approval token."""
    token: str
    action_id: int
    action_type: str
    target_id: str
    target_value: float
    created_ts: str
    expires_ts: str
    used_ts: str | None = None
    used_by: str | None = None
    status: Literal["ACTIVE", "USED", "EXPIRED", "REVOKED"] = "ACTIVE"


class WorkTicket(BaseModel):
    """U-5.3: Field crew work ticket returned by read tools (untrusted text)."""
    ticket_id: str
    ts: str
    assigned_to: str
    details: str
    flags: list[str] = []


class SimReport(BaseModel):
    method: Literal["wntr", "joukowsky_fallback"]
    min_pressure_m: float
    min_pressure_node: str
    surge_head_m: float
    closure_s: int
    critical_nodes_ok: bool
    notes: str


class ActionRecord(BaseModel):
    id: int
    run_id: str
    kind: str
    target_id: str
    from_value: float
    to_value: float
    rationale: str
    proposed_by: str
    status: ActionStatus
    guard: GuardReport
    sim: SimReport | None = None
    approver: str | None = None
    note: str | None = None
    created_ts: str
    decided_ts: str | None = None
    provenance: str = "agent"  # U-5.3: provenance tracking ('agent' | 'untrusted_text')
    from_untrusted_text: bool = False
    approval_token: str | None = None  # U-5.4: token consumed for execution


class StageResult(BaseModel):
    status: ActionStatus
    action_id: int | None = None
    reasons: list[str] = []


class NetworkTelemetry(BaseModel):
    as_of: str
    sensors: list[dict]  # id, kind, latest, min, max, health, quarantined
    leak_findings: list[LeakFinding]
    valves: dict[str, float]


class TariffQualityFeed(BaseModel):
    as_of: str
    current_band: str
    tariff: list[dict]
    reservoir_level_pct: float
    bloom_active: bool
    effective_floor_pct: float
    fire_reserve_m3: float = 0.0        # U-5.2: Civil Defence emergency volume
    fire_reserve_sector: str = "S6"     # which sector the fire is in
    alerts: list[dict]  # sanitized text + flags
    intake_quality: dict  # turbidity/chlorine health + trusted flag
    schedule: Schedule
    scada_stale: bool = False           # U-5.5: SCADA quiet flag
    telemetry_loss: bool = False        # U-5.5: telemetry drop alert
    non_compliant_sectors: list[str] = [] # U-5.1: sectors failing water quality floor
    sector_chlorine: dict[str, float] = {} # U-5.1: chlorine residual readings per sector
    work_tickets: list[dict] = []       # U-5.3: sanitized field crew tickets


class AuditEvent(BaseModel):
    id: int
    ts: str
    run_id: str | None = None
    stage: str
    actor: str
    event: str
    payload: dict


class RunResult(BaseModel):
    run_id: str
    scenario: str
    status: Literal["COMPLETED", "NEEDS_HUMAN_REVIEW", "FAILED"]
    provider: str | None = None
    steps: int = 0
    replans: int = 0
    sensor_health: list[SensorHealth] = []
    leak_findings: list[LeakFinding] = []
    schedule: Schedule | None = None
    actions: list[ActionRecord] = []
    blocked_attempts: int = 0
    brief_md: str = ""
