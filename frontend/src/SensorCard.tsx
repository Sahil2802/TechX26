import './panels.css'
import type { SensorRow } from './types'

// Sensor fields are untrusted: rendered as React text only.
export function SensorCard({ r, onClose }: { r: SensorRow; onClose: () => void }) {
  const bad = r.Trust === 'QUARANTINED'
  const rows: [string, string | number][] = [['Kind', r.Kind], ['At', r.At], ['Latest', r.Latest], ['Status', r.Status]]
  return (
    <aside className="scard" role="dialog" aria-label={`Sensor ${r.Sensor} details`}>
      <header>
        <b>{r.Sensor}</b>
        <button type="button" className="scard-x" onClick={onClose} aria-label="Close sensor details">×</button>
      </header>
      <dl>
        {rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
      </dl>
      <div className={`scard-trust ${bad ? 'is-bad' : 'is-ok'}`}>
        {bad ? 'QUARANTINED' : 'trusted'}
        {bad && <span> · excluded from every calculation</span>}
      </div>
      <p className="scard-why"><small>Why</small>{r.Why}</p>
    </aside>
  )
}
