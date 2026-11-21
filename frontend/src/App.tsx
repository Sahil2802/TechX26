import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as api from './api'
import * as sfx from './sfx'
import { Login, type Session } from './Login'
import { ApprovalGate, RunPanel } from './Gate'
import { CurveballShowcase, Findings, MetricCards, ReservoirChart, ScheduleSection, SensorsPanel } from './Panels'
import { ApprovalBanner } from './Popup'
import { Hero, HeroVideo, Rail, TopBar, type Tab } from './ShellParts'
import type { DecideResult, State, Verb } from './types'

interface Toast { id: number; kind: DecideResult['kind']; text: string }

export default function App() {
  const [state, setState] = useState<State | null>(null)
  const [scenario, setScenario] = useState('')
  const [scripted, setScripted] = useState(false)
  const [fireVol, setFireVol] = useState('0')
  const [fireSector, setFireSector] = useState('S6')
  const [busy, setBusy] = useState(false)
  const [session, setSession] = useState<Session | null>(null) // the token itself lives in api.ts, in memory only
  const [plant, setPlant] = useState('')
  const [tab, setTab] = useState<Tab>('overview')
  const [toasts, setToasts] = useState<Toast[]>([])
  const toastId = useRef(0)
  const missionFrame = useRef<HTMLIFrameElement>(null)
  const playhead = useRef<number | null>(null) // minute the Mission Control replay has reached, reported by the iframe
  const epoch = useRef(0) // bumped on every plant switch and logout; late responses from an older epoch are dropped
  const plantRef = useRef('')
  const [loadError, setLoadError] = useState('')

  // Forget everything tied to the previous plant/session so nothing leaks into the next one.
  const resetView = useCallback((nextPlant: string) => {
    epoch.current += 1
    plantRef.current = nextPlant
    playhead.current = null
    setPlant(nextPlant); setState(null); setScenario(''); setFireVol('0'); setFireSector('S6'); setLoadError(''); setBusy(false)
  }, [])
  const logout = useCallback(() => {
    api.setSession('', 'A')
    resetView('')
    setSession(null)
    setToasts([])
  }, [resetView])

  const toast = useCallback((kind: Toast['kind'], text: string) => {
    const id = ++toastId.current
    sfx.play(kind)
    setToasts((t) => [...t, { id, kind, text }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 7000)
  }, [])

  const refresh = useCallback(async () => {
    const mine = epoch.current
    const s = await api.getState()
    if (mine !== epoch.current || s.plant !== plantRef.current) return // stale: plant switched or logged out meanwhile
    setState(s)
    setScenario((cur) => (cur in s.scenarios ? cur : s.loaded ?? Object.keys(s.scenarios)[0]))
    setFireVol(String(s.fire.volume_m3))
    setFireSector(s.fire.sector)
  }, [])

  useEffect(() => {
    api.setUnauthorizedHandler(logout)
  }, [logout])

  useEffect(() => {
    if (!session) return
    const mine = epoch.current
    refresh().catch((e: Error) => {
      if (e instanceof api.UnauthorizedError || mine !== epoch.current) return
      toast('error', e.message)
      setLoadError(e.message)
    })
  }, [refresh, toast, session, plant])

  const onReady = (s: Session) => { sfx.play('boot'); api.setSession(s.token, s.plant); resetView(s.plant); setSession(s) }
  const switchPlant = (id: string) => { if (!session) return; sfx.play('relay'); api.setSession(session.token, id); resetView(id) }

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.source !== missionFrame.current?.contentWindow) return
      const data = e.data as { aquagridPlayhead?: unknown; aquagridSfx?: unknown } | null
      const at = data?.aquagridPlayhead
      if (typeof at === 'number' && Number.isFinite(at)) playhead.current = at
      if (sfx.isSfx(data?.aquagridSfx)) sfx.play(data.aquagridSfx) // Mission Control banner changed severity
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])

  // A new approval request is the one event that needs a human: sound the attention chime when the count rises.
  const pendingCount = state?.actions.filter((a) => a.status === 'PENDING_APPROVAL').length ?? 0
  const prevPending = useRef<number | null>(null)
  useEffect(() => {
    if (!state) { prevPending.current = null; return } // plant switch / logout: don't chime for the initial load
    if (prevPending.current !== null && pendingCount > prevPending.current) sfx.play('attention')
    prevPending.current = pendingCount
  }, [state, pendingCount])

  // Recomputed only when the data version (or scenario) changes, never on unrelated re-renders, so typing in a form
  // can't reload the replay; when it does reload (new run, new data) it resumes at the viewer's minute.
  const missionKey = state?.mission_key
  const missionSrc = useMemo(
    () => (missionKey === undefined ? '' : api.missionUrl(scenario, missionKey, plant, session?.token ?? '', playhead.current)),
    [scenario, missionKey, plant, session?.token],
  )

  // Every write goes through one place: lock the buttons, call the API, redraw from server truth.
  const act = async (fn: () => Promise<DecideResult | void>) => {
    if (busy) return
    setBusy(true)
    const startedIn = epoch.current
    try {
      const r = await fn()
      if (r && epoch.current === startedIn) toast(r.kind, r.message)
      await refresh()
    } catch (e) {
      if (epoch.current === startedIn) toast('error', (e as Error).message)
    } finally {
      if (epoch.current === startedIn) setBusy(false) // resetView already cleared it for a newer plant
    }
  }

  if (!session) return <Login onReady={onReady} />
  if (!state) {
    return (
      <main>
        <p className="mut">{loadError || 'Loading console…'}</p>
        {loadError && <button onClick={logout}>Back to login</button>}
        <Toasts toasts={toasts} />
      </main>
    )
  }

  const fireActive = state.fire.volume_m3 > 0
  const firePct = (state.fire.volume_m3 / state.metrics.capacity_m3) * 100
  const pending = state.actions.filter((a) => a.status === 'PENDING_APPROVAL')

  return (
    <div className="app">
      <HeroVideo plant={state.plant} />
      <TopBar clock={state.demo_now} tab={tab} onTab={setTab} pending={pending.length}
        plant={state.plant} plants={state.plants} admin={session.role === 'admin'} onPlant={switchPlant} />
      <Hero levelPct={state.metrics.level_pct} floorPct={state.metrics.floor_pct} band={state.metrics.band} pending={pending.length} />
      <ApprovalBanner ids={pending.map((a) => a.id)} onOpen={() => setTab('approvals')} />
      {state.curveballs?.scada?.stale && (
        <div className="alert a-error" role="alert">
          ⚠️ <b>SCADA TELEMETRY LOSS ACTIVE (&gt;10 min quiet):</b> All setpoint and valve proposals are BLOCKED by Python guards. Tariff optimization is paused, and the network is held at safe baseline production.
        </div>
      )}
      <div className="shell">
        <Rail
          state={state} scenario={scenario} busy={busy} scripted={scripted}
          fireVol={fireVol} fireSector={fireSector}
          onScenario={(k) => { setScenario(k); act(async () => { await api.loadScenario(k) }) }}
          onRun={() => act(async () => { await api.runAgent(scenario, scripted) })}
          onScripted={setScripted} onFireVol={setFireVol} onFireSector={setFireSector}
          onApplyFire={() => act(async () => { await api.applyFire(Number(fireVol) || 0, fireSector) })}
        />

        {/* All four tabs stay mounted and are only hidden, so the Mission Control replay and the
            gate's form state survive switching tabs. */}
        <main className="stage">
          <div hidden={tab !== 'overview'}>
            <div className="mission-wrap">
              <span className="mut mission-loading">Loading Mission Control…</span>
              <iframe ref={missionFrame} className="mission" title="AquaGrid Mission Control" sandbox="allow-scripts"
                src={missionSrc} />
            </div>
            <p className="mut">Read-only replay of the selected scenario, 12:00-14:00. Every figure is computed by the guards; the agent feed
              narrates it. Pick a scenario and Run agent on the left, then decide in Approvals.</p>
            <MetricCards m={state.metrics} fireM3={state.fire.volume_m3} cb={state.curveballs} />
            <div className="cols">
              <section><h2>Sensors</h2><SensorsPanel sensors={state.sensors} valves={state.valves} /></section>
              <section><h2>Reservoir</h2><ReservoirChart s={state} /></section>
            </div>
          </div>

          <div hidden={tab !== 'telemetry'}>
            {fireActive
              ? <div className="alert a-warning">🚒 <b>Fire reserve ACTIVE</b>: {state.fire.volume_m3.toLocaleString()} m³ ({firePct.toFixed(1)}%) locked for Civil Defence (Sector {state.fire.sector}). Effective floor raised to <b>{state.metrics.floor_pct.toFixed(0)}%</b>.</div>
              : <div className="alert a-info">No active fire request. Set a volume in the left rail to simulate the Civil Defence curveball (U-5.2); it locks that volume on top of the 35 % strategic reserve.</div>}
            <h2>Leak vs sensor fault</h2>
            <Findings findings={state.findings} />
            <h2>Tariff-aware schedule and the reserve floor</h2>
            <ScheduleSection s={state} />
          </div>

          <div hidden={tab !== 'approvals'}>
            <h2>Human approval gate</h2>
            <ApprovalGate actions={state.actions} disabled={busy}
              demoLeak={state.demo_leak} onStageDemo={() => act(api.stageDemo)}
              onStageIsolationFlush={() => act(api.stageIsolationFlush)}
              onDecide={(id, verb: Verb, operator, note, tok) => act(() => api.decide(id, verb, operator, note, tok))} />
          </div>

          <div hidden={tab !== 'curveballs'}>
            <CurveballShowcase state={state} disabled={busy} onRefresh={refresh} toast={toast} />
          </div>

          <div hidden={tab !== 'log'}>
            <h2>Agent briefing and decision trace</h2>
            <RunPanel key={state.run?.run_id ?? 'none'} run={state.run} />
            <p className="mut">The LLM reads through two read-only tools and can only queue proposals. Code validates every proposal; only a human can approve, reject or revert.</p>
          </div>
        </main>
      </div>

      <Toasts toasts={toasts} />
    </div>
  )
}

function Toasts({ toasts }: { toasts: Toast[] }) {
  return <div id="toast" role="status">{toasts.map((t) => <div key={t.id} className={`alert a-${t.kind}`}>{t.text}</div>)}</div>
}
