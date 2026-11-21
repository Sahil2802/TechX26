import type { DecideResult, Run, State, Verb } from './types'

// Every state-changing request carries the operator token as the X-Operator-Token header (reads need none).
let operatorToken = ''
export const setOperatorToken = (token: string) => { operatorToken = token }

async function call<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, body === undefined ? undefined : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(operatorToken ? { 'X-Operator-Token': operatorToken } : {}) },
    body: JSON.stringify(body),
  })
  const data: unknown = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = (data as { detail?: unknown }).detail
    throw new Error(typeof detail === 'string' ? detail : `Request failed (${res.status})`)
  }
  return data as T
}

export const getState = () => call<State>('/api/state')
export const loadScenario = (scenario: string) => call<{ ok: true }>('/api/scenario', { scenario })
export const runAgent = (scenario: string, scripted: boolean) => call<Run>('/api/run', { scenario, scripted })
export const applyFire = (volume_m3: number, sector: string) => call<{ ok: true }>('/api/fire', { volume_m3, sector })
export const stageDemo = () => call<DecideResult>('/api/stage-demo', {})
export const missionUrl = (scenario: string, key: string, at: number | null = null) =>
  `/api/mission?scenario=${encodeURIComponent(scenario)}&key=${encodeURIComponent(key)}` +
  (at === null ? '' : `&at=${at.toFixed(2)}`)
export const decide = (id: number, verb: Verb, operator: string, note: string, token?: string) =>
  call<DecideResult>(`/api/actions/${id}/${verb}`, { operator, note, token })

export const injectChlorine = (sector: string, value: number) =>
  call<{ ok: true; sector: string; value: number }>('/api/curveball/chlorine', { sector, value })

export const stageIsolationFlush = () =>
  call<DecideResult>('/api/stage-isolation-flush', {})

export const scadaAction = (action: 'drop' | 'restore') =>
  call<{ ok: true; action: string }>('/api/curveball/scada', { action })

export const addWorkTicket = (details: string) =>
  call<{ ok: true; ticket_id: string }>('/api/curveball/ticket', { ticket_id: 'TCK-4819', assigned_to: 'field-crew', details })

export const issueApprovalToken = (action_id: number, operator: string, valid_minutes = 15) =>
  call<{ token: string; action_id: number; action_type: string; target_id: string; target_value: number; expires_ts: string }>(
    '/api/curveball/token/issue', { action_id, operator, valid_minutes }
  )

export const testTokenReplay = (token: string, action_id: number, operator: string, test_target_id?: string, simulate_time?: string) =>
  call<{ allowed: boolean; status: string; reason: string }>('/api/curveball/token/replay-test', {
    token, action_id, operator, test_target_id, simulate_time
  })
