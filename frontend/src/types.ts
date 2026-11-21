// Shapes returned by aquagrid/web.py (GET /api/state and friends).

export interface ScenarioInfo { name: string; blurb: string }

export interface Metrics {
  band: string
  aed_per_kwh: number
  level_pct: number
  capacity_m3: number
  floor_pct: number
  base_floor_pct: number
  intake_trusted: boolean
  bloom_active: boolean
  setpoint_m3h: number
}

export interface SensorRow {
  Sensor: string
  Kind: string
  At: string
  Latest: number
  Status: string
  Trust: 'QUARANTINED' | 'trusted'
  Why: string
}

export interface Finding {
  headline: string
  tone: 'error' | 'warning' | 'ok'
  evidence: string[]
  decided_by: string
}

export interface Chart { hour: number[]; [series: string]: number[] }

export interface Slot { hour: number; tariff_aed_kwh: number; projected_level_pct: number; [k: string]: number | string }
export interface Rejected { name: string; reason: string; min_level_pct: number }
export interface Schedule {
  cost_aed: number
  baseline_cost_aed: number
  kwh_saved: number
  buffer_pct: number
  slots: Slot[]
  rejected: Rejected[]
}

export interface Arithmetic { lines: string[]; end_pct: number; min_pct: number; passes: boolean }

export interface GuardRow { Check: string; Result: 'PASS' | 'FAIL'; Detail: string }

export interface Action {
  id: number
  kind: string
  target_id: string
  from_value: number
  to_value: number
  rationale: string
  proposed_by: string
  status: string
  approver: string | null
  note: string | null
  guard_rows: GuardRow[]
  provenance?: string
  from_untrusted_text?: boolean
  approval_token?: string | null
}

export interface TraceRow { '#': number; Who: string; Step: string; Detail: string }

export interface AgentInfo {
  run_id: string | null
  status: string
  steps: number
  blocked: number
}

export interface Run {
  run_id: string
  scenario: string
  status: 'COMPLETED' | 'NEEDS_HUMAN_REVIEW' | 'FAILED'
  provider: string | null
  steps: number
  blocked_attempts: number
  brief_md: string
  trace: TraceRow[]
  agents?: {
    sensor_guard?: AgentInfo
    grid_optimizer?: AgentInfo
    security_sentinel?: AgentInfo
  }
}

export interface ChlorineStatus {
  sector: string
  value: number | null
  compliant: boolean
  floor: number
}

export interface ScadaStatus {
  stale: boolean
  telemetry_loss: boolean
}

export interface ApprovalToken {
  token: string
  action_id: number
  action_type: string
  target_id: string
  target_value: number
  created_ts: string
  expires_ts: string
  used_ts: string | null
  used_by: string | null
  status: 'ACTIVE' | 'USED' | 'EXPIRED' | 'REVOKED'
}

export interface WorkTicket {
  ticket_id: string
  ts: string
  assigned_to: string
  details: string
  flags: string[]
}

export interface CurveballState {
  chlorine: ChlorineStatus
  scada: ScadaStatus
  tokens: ApprovalToken[]
  tickets: WorkTicket[]
}

export interface State {
  scenario: string
  loaded: string | null
  scenarios: Record<string, ScenarioInfo>
  demo_now: string
  operator_token_required: boolean
  llm_chain: string[]
  fire_sectors: string[]
  metrics: Metrics
  fire: { volume_m3: number; sector: string }
  sensors: SensorRow[]
  valves: Record<string, number>
  findings: Finding[]
  chart: Chart
  schedule: Schedule
  arithmetic: { throttle_m3h: number; throttle: Arithmetic; hold_m3h: number; hold: Arithmetic }
  actions: Action[]
  run: Run | null
  demo_leak: { valve: string; pipe: string | null } | null
  mission_key: string
  plant: string
  plants: PlantInfo[]
  role: 'admin' | 'plant'
  curveballs?: CurveballState
}

export interface PlantInfo { id: string; name: string }
export interface LoginResult { role: 'admin' | 'plant'; plants: PlantInfo[] }

export type Verb = 'approve' | 'reject' | 'revert'
export interface DecideResult { kind: 'success' | 'warning' | 'error'; message: string; status: string }
