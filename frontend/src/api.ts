import type { DecideResult, LoginResult, Run, State, Verb } from './types'

// Session lives in memory only (never localStorage); every request carries both headers.
let sessionToken = ''
let sessionPlant = 'A'
export const setSession = (token: string, plant: string) => { sessionToken = token; sessionPlant = plant }

export class UnauthorizedError extends Error {}
let onUnauthorized: () => void = () => {}
export const setUnauthorizedHandler = (fn: () => void) => { onUnauthorized = fn }

async function call<T>(path: string, body?: unknown, authed = true): Promise<T> {
  const res = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...(authed ? { 'X-Operator-Token': sessionToken, 'X-Plant': sessionPlant } : {}),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  const data: unknown = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = (data as { detail?: unknown }).detail
    const msg = typeof detail === 'string' ? detail : `Request failed (${res.status})`
    if (res.status === 401) {
      if (authed) onUnauthorized() // a bad login attempt just shows its error inline
      throw new UnauthorizedError(msg)
    }
    throw new Error(msg)
  }
  return data as T
}

export const login = (token: string) => call<LoginResult>('/api/login', { token }, false)
export const getState = () => call<State>('/api/state')
export const loadScenario = (scenario: string) => call<{ ok: true }>('/api/scenario', { scenario })
export const runAgent = (scenario: string, scripted: boolean) => call<Run>('/api/run', { scenario, scripted })
export const applyFire = (volume_m3: number, sector: string) => call<{ ok: true }>('/api/fire', { volume_m3, sector })
export const stageDemo = () => call<DecideResult>('/api/stage-demo', {})
// The iframe cannot send headers, so the session rides in the query string.
export const missionUrl = (scenario: string, key: string, plant: string, token: string, at: number | null = null) =>
  `/api/mission?scenario=${encodeURIComponent(scenario)}&key=${encodeURIComponent(key)}` +
  `&token=${encodeURIComponent(token)}&plant=${encodeURIComponent(plant)}` +
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
