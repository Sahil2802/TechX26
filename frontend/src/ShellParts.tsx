import { useState } from 'react'
import * as sfx from './sfx'
import type { PlantInfo, State } from './types'

export type Tab = 'overview' | 'telemetry' | 'approvals' | 'log'

const TABS: [Tab, string][] = [
  ['overview', 'Overview'], ['telemetry', 'Telemetry'], ['approvals', 'Approvals'], ['log', 'Decision log'],
]

interface TopBarProps {
  clock: string; tab: Tab; onTab: (t: Tab) => void; pending: number
  plant: string; plants: PlantInfo[]; admin: boolean; onPlant: (id: string) => void
}

export function TopBar({ clock, tab, onTab, pending, plant, plants, admin, onPlant }: TopBarProps) {
  return (
    <header className="topbar">
      <div className="brand">AquaGrid <span className="mut">operator console</span></div>
      {admin
        ? (
          <label className="plant-switch">Plant
            <select value={plant} onChange={(e) => onPlant(e.target.value)}>
              {plants.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.id})</option>)}
            </select>
          </label>
        )
        : <span className="pill">{plants.find((p) => p.id === plant)?.name ?? plant}</span>}
      <div className="pills">
        <span className="pill p-code">CODE decides</span>
        <span className="pill p-llm">LLM explains</span>
        <span className="pill p-human">HUMAN approves</span>
      </div>
      <div className="tabs main-tabs" role="tablist">
        {TABS.map(([id, label]) => (
          <button key={id} role="tab" className="tab" aria-selected={tab === id} onClick={() => onTab(id)}>
            {label}{id === 'approvals' && pending > 0 && <span className="badge" aria-label={`${pending} pending`}>{pending}</span>}
          </button>
        ))}
      </div>
      <SoundToggle />
      <div className="clock mut">synthetic demo data · simulated clock <b>{clock.replace('T', ' ')}</b></div>
    </header>
  )
}

function SoundToggle() {
  const [off, setOff] = useState(sfx.isMuted())
  return (
    <button className="tab" aria-pressed={!off} title={off ? 'Sound off' : 'Sound on'}
      onClick={() => { sfx.setMuted(!off); setOff(!off) }}>
      {off ? '🔇 Sound off' : '🔊 Sound on'}
    </button>
  )
}

interface RailProps {
  state: State
  scenario: string
  busy: boolean
  scripted: boolean
  fireVol: string
  fireSector: string
  onScenario: (k: string) => void
  onRun: () => void
  onScripted: (v: boolean) => void
  onFireVol: (v: string) => void
  onFireSector: (v: string) => void
  onApplyFire: () => void
}

export function Rail(p: RailProps) {
  const { state } = p
  return (
    <aside className="rail">
      <h2>Scenarios</h2>
      <div className="rail-list">
        {Object.entries(state.scenarios).map(([k, v]) => (
          <button key={k} className="rail-btn" aria-pressed={p.scenario === k} disabled={p.busy} onClick={() => p.onScenario(k)}>
            <b>{k}</b> {v.name}
          </button>
        ))}
      </div>
      <p className="mut small">{state.scenarios[p.scenario]?.blurb}</p>
      <button className="primary" disabled={p.busy} onClick={p.onRun}>Run agent</button>
      <label>Model
        <select value={p.scripted ? 'scripted' : 'real'} onChange={(e) => p.onScripted(e.target.value === 'scripted')}>
          <option value="real">Real LLM chain</option>
          <option value="scripted">Scripted attacker</option>
        </select>
      </label>
      <p className="mut small">LLM chain with keys: {state.llm_chain.join(' → ') || 'none'} (Ollama is local, not probed)</p>
      <h2>Fire reserve</h2>
      <label>Requested volume (m³)
        <input type="number" min={0} max={10000} step={500} value={p.fireVol} onChange={(e) => p.onFireVol(e.target.value)} />
      </label>
      <label>Sector
        <select value={p.fireSector} onChange={(e) => p.onFireSector(e.target.value)}>
          {state.fire_sectors.map((s) => <option key={s}>{s}</option>)}
        </select>
      </label>
      <button className="primary" disabled={p.busy} onClick={p.onApplyFire}>Apply request</button>
    </aside>
  )
}

// Full-page looping backdrop for the selected plant; panels blur it. Keyed by plant so a switch swaps the clip
// while the data updates underneath. Decorative: muted, aria-hidden, held on its first frame under reduced motion.
export function HeroVideo({ plant }: { plant: string }) {
  const still = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
  return (
    <div className="hero-bg" aria-hidden="true">
      <video key={plant} src={`/hero-${plant}.mp4`} muted loop playsInline autoPlay={!still} preload="metadata" />
    </div>
  )
}

export function Hero({ levelPct, floorPct, band, pending }: { levelPct: number; floorPct: number; band: string; pending: number }) {
  return (
    <section className="hero" aria-label="Network summary">
      <h1>Coastal network, <span>under supervision</span></h1>
      <p className="hero-sub">
        Reservoir R1 at <b>{levelPct.toFixed(1)}%</b> against a <b>{floorPct.toFixed(0)}%</b> floor · {band.toUpperCase()} tariff
        · {pending > 0 ? `${pending} action${pending === 1 ? '' : 's'} awaiting a human` : 'no actions pending'}
      </p>
    </section>
  )
}
