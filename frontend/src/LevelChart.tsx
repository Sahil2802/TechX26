import type { Chart } from './types'

const W = 720, H = 280, PAD = { l: 42, r: 12, t: 10, b: 28 }
const COLORS = ['#14b8a6', '#f0564f', '#f2a93b', '#8b5cf6']

// Dependency-free SVG line chart: reservoir level by hour, one line per series, the floor dashed.
export function LevelChart({ chart }: { chart: Chart }) {
  const keys = Object.keys(chart).filter((k) => k !== 'hour')
  const all = keys.flatMap((k) => chart[k])
  const lo = Math.floor(Math.min(...all) - 2), hi = Math.ceil(Math.max(...all) + 2)
  const hours = chart.hour
  const x = (i: number) => PAD.l + (i / Math.max(hours.length - 1, 1)) * (W - PAD.l - PAD.r)
  const y = (v: number) => PAD.t + (1 - (v - lo) / (hi - lo)) * (H - PAD.t - PAD.b)
  const ticks = [0, 1, 2, 3, 4].map((i) => lo + ((hi - lo) * i) / 4)

  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Reservoir level by hour">
        {ticks.map((v) => (
          <g key={v}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} stroke="#243251" />
            <text x={PAD.l - 6} y={y(v) + 4} textAnchor="end">{v.toFixed(0)}%</text>
          </g>
        ))}
        {hours.map((h, i) => i % 2 === 0 && <text key={h} x={x(i)} y={H - 8} textAnchor="middle">{h}:00</text>)}
        {keys.map((k, n) => (
          <polyline key={k} fill="none" stroke={COLORS[n % COLORS.length]} strokeWidth={2.5}
            strokeDasharray={/floor/i.test(k) ? '6 4' : undefined}
            points={chart[k].map((v, i) => `${x(i)},${y(v)}`).join(' ')} />
        ))}
      </svg>
      <div className="legend">
        {keys.map((k, n) => <span key={k}><i style={{ background: COLORS[n % COLORS.length] }} />{k}</span>)}
      </div>
    </>
  )
}
