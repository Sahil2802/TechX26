import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as api from './api'
import { ApprovalGate, RunPanel } from './Gate'
import { CurveballShowcase, Findings, MetricCards, ScheduleSection, SensorsPanel } from './Panels'
import { ApprovalPopup } from './Popup'
import type { DecideResult, State, Verb } from './types'

interface Toast { id: number; kind: DecideResult['kind']; text: string }
type Tab = 'mission' | 'classic' | 'action'

export default function App() {
  const [state, setState] = useState<State | null>(null)
  const [scenario, setScenario] = useState('')
  const [scripted, setScripted] = useState(false)
  const [fireVol, setFireVol] = useState('0')
  const [fireSector, setFireSector] = useState('S6')
  const [busy, setBusy] = useState(false)
  const [token, setToken] = useState('') // kept in memory only; sent as X-Operator-Token on every write
  const [tab, setTab] = useState<Tab>('mission')
  const [toasts, setToasts] = useState<Toast[]>([])
  const toastId = useRef(0)
  const missionFrame = useRef<HTMLIFrameElement>(null)
  const playhead = useRef<number | null>(null) // minute the Mission Control replay has reached, reported by the iframe

  const toast = useCallback((kind: Toast['kind'], text: string) => {
    const id = ++toastId.current
    setToasts((t) => [...t, { id, kind, text }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 7000)
  }, [])

  const refresh = useCallback(async () => {
    const s = await api.getState()
    setState(s)
    setScenario((cur) => (cur in s.scenarios ? cur : s.loaded ?? Object.keys(s.scenarios)[0]))
    setFireVol(String(s.fire.volume_m3))
    setFireSector(s.fire.sector)
  }, [])

  useEffect(() => { refresh().catch((e: Error) => toast('error', e.message)) }, [refresh, toast])

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      const at = (e.data as { aquagridPlayhead?: unknown } | null)?.aquagridPlayhead
      if (e.source === missionFrame.current?.contentWindow && typeof at === 'number' && Number.isFinite(at)) playhead.current = at
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])

  // Recomputed only when the data version (or scenario) changes, never on unrelated re-renders, so typing in a form
  // can't reload the replay; when it does reload (new run, new data) it resumes at the viewer's minute.
  const missionKey = state?.mission_key
  const missionSrc = useMemo(
    () => (missionKey === undefined ? '' : api.missionUrl(scenario, missionKey, playhead.current)),
    [scenario, missionKey],
  )

  // Every write goes through one place: lock the buttons, call the API, redraw from server truth.
  const act = async (fn: () => Promise<DecideResult | void>) => {
    if (busy) return
    setBusy(true)
    try {
      const r = await fn()
      if (r) toast(r.kind, r.message)
      await refresh()
    } catch (e) {
      toast('error', (e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!state) return <main><p className="mut">Loading console…</p><Toasts toasts={toasts} /></main>

  const fireActive = state.fire.volume_m3 > 0
  const firePct = (state.fire.volume_m3 / state.metrics.capacity_m3) * 100
  const pending = state.actions.filter((a) => a.status === 'PENDING_APPROVAL')
  const tabs: [Tab, string][] = [
    ['mission', 'Mission Control'],
    ['classic', 'Classic console'],
    ['action', pending.length ? `🔔 Action required (${pending.length})` : 'Action required'],
  ]

  return (
    <main>
      <h1>💧 AquaGrid operator console</h1>
      <div>
        <span className="pill p-code">CODE decides</span>
        <span className="pill p-llm">LLM explains</span>
        <span className="pill p-human">HUMAN approves</span>
        <span className="mut">synthetic demo data · simulated clock <b>{state.demo_now.replace('T', ' ')}</b></span>
      </div>

      <div className="bar">
        <label>Scenario
          <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
            {Object.entries(state.scenarios).map(([k, v]) => <option key={k} value={k}>{k}: {v.name}</option>)}
          </select>
        </label>
        <button disabled={busy} onClick={() => act(async () => { await api.loadScenario(scenario) })}>Load scenario</button>
        <button className="primary" disabled={busy} onClick={() => act(async () => { await api.runAgent(scenario, scripted) })}>Run agent</button>
        <label>Model
          <select value={scripted ? 'scripted' : 'real'} onChange={(e) => setScripted(e.target.value === 'scripted')}>
            <option value="real">Real LLM chain</option>
            <option value="scripted">Scripted attacker</option>
          </select>
        </label>
        {state.operator_token_required && (
          <label>Operator token
            <input type="password" value={token} autoComplete="off" placeholder="required for every change"
              onChange={(e) => { setToken(e.target.value); api.setOperatorToken(e.target.value) }} />
          </label>
        )}
      </div>
      <div className="mut">
        {state.scenarios[scenario]?.blurb} · LLM chain with keys: {state.llm_chain.join(' → ') || 'none'} (Ollama is local, not probed)
      </div>

      <ApprovalPopup ids={pending.map((a) => a.id)} onOpen={() => setTab('action')} />
      {state.curveballs?.scada?.stale && (
        <div className="alert a-error" role="alert">
          ⚠️ <b>SCADA TELEMETRY LOSS ACTIVE (&gt;10 min quiet):</b> All setpoint and valve proposals are BLOCKED by Python guards. Tariff optimization is paused, and the network is held at safe baseline production (1,900 m³/h).
        </div>
      )}
      {pending.length > 0 && (
        <div className="alert a-warning" role="status">
          🔔 <b>{pending.length} action(s) need your decision.</b> Open the <b>Action required</b> tab: nothing moves until you approve.
        </div>
      )}
      <div className="tabs main-tabs" role="tablist">
        {tabs.map(([id, label]) => (
          <button key={id} role="tab" className="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>

      {/* All three tabs stay mounted and are only hidden, so the Mission Control replay and the
          gate's form state survive switching tabs. */}
      <div hidden={tab !== 'mission'}>
          <div className="mission-wrap">
            <span className="mut mission-loading">Loading Mission Control…</span>
            <iframe ref={missionFrame} className="mission" title="AquaGrid Mission Control" sandbox="allow-scripts"
              src={missionSrc} />
          </div>
          <p className="mut">Read-only replay of the selected scenario, 12:00-14:00. Every figure is computed by the guards; the agent feed
            narrates it. Use Load scenario / Run agent above, then decide in the Action required tab.</p>
      </div>

      <div hidden={tab !== 'classic'}>
      <CurveballShowcase state={state} disabled={busy} onRefresh={refresh} toast={toast} />

      <details open={fireActive}>
        <summary>🚒 Civil Defence emergency fire reserve (U-5.2)</summary>
        <p className="mut">A fire request locks this volume on top of the 35 % strategic reserve. The scheduler veto and all guard arithmetic update immediately.</p>
        <div className="bar">
          <label>Requested volume (m³)
            <input type="number" min={0} max={10000} step={500} value={fireVol} onChange={(e) => setFireVol(e.target.value)} />
          </label>
          <label>Sector
            <select value={fireSector} onChange={(e) => setFireSector(e.target.value)}>
              {state.fire_sectors.map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
          <button className="primary" disabled={busy}
            onClick={() => act(async () => { await api.applyFire(Number(fireVol) || 0, fireSector) })}>Apply request</button>
        </div>
        {fireActive
          ? <div className="alert a-warning">🚒 <b>Fire reserve ACTIVE</b> — {state.fire.volume_m3.toLocaleString()} m³ ({firePct.toFixed(1)}%) locked for Civil Defence (Sector {state.fire.sector}). Effective floor raised to <b>{state.metrics.floor_pct.toFixed(0)}%</b>.</div>
          : <div className="alert a-info">No active fire request. Enter a volume and Apply to simulate the curveball.</div>}
      </details>

      <MetricCards m={state.metrics} fireM3={state.fire.volume_m3} cb={state.curveballs} />

      <div className="cols">
        <section><h2>Network &amp; sensors</h2><SensorsPanel sensors={state.sensors} valves={state.valves} /></section>
        <section><h2>Leak vs sensor fault</h2><Findings findings={state.findings} /></section>
      </div>

      <h2>Tariff-aware schedule and the reserve floor</h2>
      <ScheduleSection s={state} />
      </div>

      <div hidden={tab !== 'action'}>
      <h2>Human approval gate</h2>
      <ApprovalGate actions={state.actions} disabled={busy}
        demoLeak={state.demo_leak} onStageDemo={() => act(api.stageDemo)}
        onStageIsolationFlush={() => act(api.stageIsolationFlush)}
        onDecide={(id, verb: Verb, operator, note, tok) => act(() => api.decide(id, verb, operator, note, tok))} />
      </div>

      <h2>Agent briefing and decision trace</h2>
      <RunPanel key={state.run?.run_id ?? 'none'} run={state.run} />
      <p className="mut">The LLM reads through two read-only tools and can only queue proposals. Code validates every proposal; only a human can approve, reject or revert.</p>
      <Toasts toasts={toasts} />
    </main>
  )
}

function Toasts({ toasts }: { toasts: Toast[] }) {
  return <div id="toast" role="status">{toasts.map((t) => <div key={t.id} className={`alert a-${t.kind}`}>{t.text}</div>)}</div>
}
