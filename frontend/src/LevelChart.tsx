import './panels.css'
import type { Chart } from './types'

const W = 720, H = 280, PAD = { l: 42, r: 56, t: 10, b: 28 }
const COLORS = ['#14b8a6', '#f0564f', '#f2a93b', '#8b5cf6']
const FLOOR_COLOR = '#ff7a73'

// Dependency-free SVG line chart: reservoir level by hour, one line per series, the floor dashed and bold.
// peakHours (optional) shades those hours as a peak-tariff band behind the lines.
export function LevelChart({ chart, peakHours = [] }: { chart: Chart; peakHours?: number[] }) {
  const keys = Object.keys(chart).filter((k) => k !== 'hour')
  const all = keys.flatMap((k) => chart[k])
  const lo = Math.floor(Math.min(...all) - 2), hi = Math.ceil(Math.max(...all) + 2)
  const hours = chart.hour
  const x = (i: number) => PAD.l + (i / Math.max(hours.length - 1, 1)) * (W - PAD.l - PAD.r)
  const y = (v: number) => PAD.t + (1 - (v - lo) / (hi - lo)) * (H - PAD.t - PAD.b)
  const ticks = [0, 1, 2, 3, 4].map((i) => lo + ((hi - lo) * i) / 4)
  const step = hours.length > 1 ? (W - PAD.l - PAD.r) / (hours.length - 1) : 0
  const peakIdx = hours.map((h, i) => (peakHours.includes(h) ? i : -1)).filter((i) => i >= 0)
  const peakX0 = peakIdx.length ? Math.max(PAD.l, x(Math.min(...peakIdx)) - step / 2) : 0
  const peakX1 = peakIdx.length ? Math.min(W - PAD.r, x(Math.max(...peakIdx)) + step / 2) : 0
  const label = `Reservoir level by hour${peakIdx.length ? `, peak tariff hours ${Math.min(...peakHours)}:00 to ${Math.max(...peakHours)}:00 shaded` : ''}`

  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={label}>
        {peakIdx.length > 0 && (
          <g>
            <rect className="peak-band" x={peakX0} y={PAD.t} width={peakX1 - peakX0} height={H - PAD.t - PAD.b} />
            <text className="peak-label" x={(peakX0 + peakX1) / 2} y={PAD.t + 11} textAnchor="middle">peak tariff</text>
          </g>
        )}
        {ticks.map((v) => (
          <g key={v}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} stroke="#243251" />
            <text x={PAD.l - 6} y={y(v) + 4} textAnchor="end">{v.toFixed(0)}%</text>
          </g>
        ))}
        {hours.map((h, i) => i % 2 === 0 && <text key={h} x={x(i)} y={H - 8} textAnchor="middle">{h}:00</text>)}
        {keys.map((k, n) => {
          const floor = /floor/i.test(k)
          const pts = chart[k]
          return (
            <g key={k}>
              <polyline fill="none" stroke={floor ? FLOOR_COLOR : COLORS[n % COLORS.length]}
                strokeWidth={floor ? 3.5 : 2.5} strokeDasharray={floor ? '8 5' : undefined}
                points={pts.map((v, i) => `${x(i)},${y(v)}`).join(' ')} />
              {floor && pts.length > 0 && (
                <text className="floor-label" x={W - PAD.r + 4} y={y(pts[pts.length - 1]) + 4}>floor</text>
              )}
            </g>
          )
        })}
      </svg>
      <div className="legend">
        {keys.map((k, n) => <span key={k}><i style={{ background: /floor/i.test(k) ? FLOOR_COLOR : COLORS[n % COLORS.length] }} />{k}</span>)}
      </div>
    </>
  )
}
