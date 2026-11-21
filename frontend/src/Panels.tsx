import { LevelChart } from './LevelChart'
import type { Arithmetic, Finding, Metrics, Schedule, SensorRow, State } from './types'

const fmt = (n: number, d = 0) => n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })

function Card({ title, value, sub, tone }: { title: string; value: string; sub: string; tone?: 'good' | 'bad' }) {
  return (
    <div className="card">
      <small>{title}</small>
      <b className={tone}>{value}</b>
      <span>{sub}</span>
    </div>
  )
}

export function MetricCards({ m, fireM3 }: { m: Metrics; fireM3: number }) {
  const firePct = (fireM3 / m.capacity_m3) * 100
  const extras = [
    m.bloom_active || !m.intake_trusted ? '5% bloom/intake' : '',
    firePct > 0 ? `${firePct.toFixed(1)}% fire` : '',
  ].filter(Boolean)
  const floorNote = m.floor_pct > m.base_floor_pct
    ? `${m.base_floor_pct}% base + ${extras.join(' + ')}`
    : `${fmt(m.floor_pct)}% = ${m.base_floor_pct}% base`
  return (
    <div className="cards">
      <Card title="Tariff band" value={m.band.toUpperCase()} sub={`${m.aed_per_kwh.toFixed(2)} AED/kWh`} />
      <Card title="Reservoir R1" value={`${m.level_pct.toFixed(1)}%`} sub={`${fmt(m.capacity_m3)} m3 capacity`} />
      <Card title="Effective floor" value={`${fmt(m.floor_pct)}%`} sub={floorNote} />
      <Card title="Intake sensor" value={m.intake_trusted ? 'TRUSTED' : 'UNTRUSTED'}
        sub={m.intake_trusted ? 'quality readings believed' : 'assume worst case'} tone={m.intake_trusted ? 'good' : 'bad'} />
      <Card title="Bloom alert" value={m.bloom_active ? 'ACTIVE' : 'none'}
        sub={`plant ${fmt(m.setpoint_m3h)} m3/h`} tone={m.bloom_active ? 'bad' : undefined} />
    </div>
  )
}

export function SensorsPanel({ sensors, valves }: { sensors: SensorRow[]; valves: State['valves'] }) {
  const bad = sensors.filter((r) => r.Trust === 'QUARANTINED').length
  const moved = Object.entries(valves).filter(([, p]) => p < 100)
  return (
    <div className="box">
      <div className="mut">{sensors.length - bad} trusted · {bad} QUARANTINED (excluded from every calculation)</div>
      <div className="scroll">
        <table>
          <thead><tr>{['Sensor', 'Kind', 'At', 'Latest', 'Status', 'Trust', 'Why'].map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {sensors.map((r) => (
              <tr key={r.Sensor}>
                <td>{r.Sensor}</td><td>{r.Kind}</td><td>{r.At}</td><td>{r.Latest}</td><td>{r.Status}</td>
                <td>{r.Trust === 'QUARANTINED' ? <span className="bad">🔴 QUARANTINED</span> : <span className="good">🟢 trusted</span>}</td>
                <td>{r.Why}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mut valves">
        {moved.length
          ? `Valves NOT fully open: ${moved.map(([v, p]) => `${v}=${p}%`).join(', ')}`
          : `Valves: all ${Object.keys(valves).length} fully open`}
      </div>
    </div>
  )
}

export function Findings({ findings }: { findings: Finding[] }) {
  const quiet = findings.filter((f) => f.tone === 'ok').map((f) => f.headline.split(':')[0])
  return (
    <>
      {findings.filter((f) => f.tone !== 'ok').map((f) => (
        <div key={f.headline} className={`alert a-${f.tone}`}>
          <b>{f.headline}</b>
          <ul>{f.evidence.map((e) => <li key={e}>{e}</li>)}</ul>
          <div className="mut">{f.decided_by}</div>
        </div>
      ))}
      {quiet.length > 0 && <div className="alert a-success">No anomaly in {quiet.join(', ')}</div>}
    </>
  )
}

export function ScheduleSection({ s }: { s: State }) {
  const sc: Schedule = s.schedule
  const arith = (label: string, a: Arithmetic) => (
    <>
      <div className="mut">{label}</div>
      <pre>{a.lines.join('\n')}</pre>
    </>
  )
  return (
    <>
      <div className="cols">
        <div className="box">
          <LevelChart chart={s.chart} />
          <div className="mut valves">
            Scheduled cost {fmt(sc.cost_aed)} AED vs {fmt(sc.baseline_cost_aed)} AED at flat production · {fmt(sc.kwh_saved)} kWh
            of peak-hour energy avoided · always above floor + {sc.buffer_pct}%
          </div>
        </div>
        <div>
          {sc.rejected.map((c) => (
            <div key={c.name} className="alert a-error"><b>REJECTED BY GUARD:</b> {c.name}<br />{c.reason}</div>
          ))}
          {arith(`The arithmetic: throttle to ${fmt(s.arithmetic.throttle_m3h)} m3/h`, s.arithmetic.throttle)}
          {arith(`versus holding ${fmt(s.arithmetic.hold_m3h)} m3/h`, s.arithmetic.hold)}
        </div>
      </div>
      <details>
        <summary>Schedule detail</summary>
        <div className="scroll box">
          <table>
            <thead><tr>{Object.keys(sc.slots[0] ?? {}).map((c) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>{sc.slots.map((slot) => <tr key={slot.hour}>{Object.values(slot).map((v, i) => <td key={i}>{v}</td>)}</tr>)}</tbody>
          </table>
        </div>
      </details>
    </>
  )
}
