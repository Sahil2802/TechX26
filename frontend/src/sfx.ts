// Control-room sound effects, synthesized with WebAudio (no assets). One shared context; the Mission Control
// iframe asks the parent to play its alarm tones via postMessage, so a single mute switch covers everything.

export type Sfx =
  | 'click' | 'relay' | 'boot' | 'success' | 'warning' | 'error' | 'attention'
  | 'clear' | 'advisory' | 'alarm' | 'security'

const MUTE_KEY = 'aquagrid.muted'
const MASTER_GAIN = 0.5
const THROTTLE_MS = 120

let ctx: AudioContext | null = null
let master: GainNode | null = null
let muted = (() => { try { return localStorage.getItem(MUTE_KEY) === '1' } catch { return false } })()
const lastPlayed = new Map<Sfx, number>()

function audio(): AudioContext | null {
  if (typeof AudioContext === 'undefined') return null
  if (!ctx) {
    ctx = new AudioContext()
    master = ctx.createGain()
    master.gain.value = MASTER_GAIN
    master.connect(ctx.destination)
  }
  if (ctx.state === 'suspended') void ctx.resume().catch(() => {})
  return ctx
}

interface Tone { f: number; t: number; d: number; type?: OscillatorType; g?: number; to?: number }

// Schedule one oscillator note with a fast attack and exponential release (no clicks).
function tone(c: AudioContext, { f, t, d, type = 'sine', g = 0.2, to }: Tone) {
  const o = c.createOscillator(), v = c.createGain(), at = c.currentTime + t
  o.type = type
  o.frequency.setValueAtTime(f, at)
  if (to) o.frequency.exponentialRampToValueAtTime(to, at + d)
  v.gain.setValueAtTime(0.0001, at)
  v.gain.exponentialRampToValueAtTime(g, at + 0.008)
  v.gain.exponentialRampToValueAtTime(0.0001, at + d)
  o.connect(v).connect(master!)
  o.start(at); o.stop(at + d + 0.02)
}

const repeat = (n: number, step: number, make: (i: number) => Tone[]): Tone[] =>
  Array.from({ length: n }, (_, i) => make(i).map((x) => ({ ...x, t: x.t + i * step }))).flat()

const PATTERNS: Record<Sfx, Tone[]> = {
  click: [{ f: 1900, t: 0, d: 0.025, type: 'square', g: 0.04 }],
  relay: [{ f: 110, t: 0, d: 0.07, type: 'square', g: 0.16 }, { f: 2600, t: 0.075, d: 0.02, type: 'square', g: 0.06 }],
  boot: [660, 880, 1320].map((f, i) => ({ f, t: i * 0.11, d: 0.09, type: 'square' as const, g: 0.07 })),
  success: [{ f: 880, t: 0, d: 0.1, g: 0.18 }, { f: 1320, t: 0.1, d: 0.16, g: 0.18 }],
  warning: repeat(2, 0.2, () => [{ f: 740, t: 0, d: 0.13, type: 'triangle', g: 0.22 }]),
  error: repeat(2, 0.2, () => [{ f: 200, t: 0, d: 0.16, type: 'sawtooth', g: 0.14, to: 150 }]),
  attention: [{ f: 988, t: 0, d: 0.28, g: 0.2 }, { f: 740, t: 0.3, d: 0.4, g: 0.2 }],
  clear: [{ f: 660, t: 0, d: 0.14, g: 0.14 }, { f: 520, t: 0.14, d: 0.24, g: 0.14 }],
  advisory: repeat(2, 0.3, () => [{ f: 1000, t: 0, d: 0.18, type: 'sine', g: 0.2 }]),
  alarm: repeat(4, 0.36, () => [{ f: 880, t: 0, d: 0.17, type: 'square', g: 0.1 }, { f: 660, t: 0.18, d: 0.17, type: 'square', g: 0.1 }]),
  security: repeat(3, 0.5, () => [{ f: 600, t: 0, d: 0.25, type: 'sawtooth', g: 0.1, to: 1300 }, { f: 1300, t: 0.25, d: 0.25, type: 'sawtooth', g: 0.1, to: 600 }]),
}

export const isMuted = () => muted

export function setMuted(next: boolean) {
  muted = next
  try { localStorage.setItem(MUTE_KEY, next ? '1' : '0') } catch { /* private mode: keep in memory only */ }
}

export function play(name: Sfx) {
  if (muted) return
  const now = performance.now()
  if (now - (lastPlayed.get(name) ?? -Infinity) < THROTTLE_MS) return
  lastPlayed.set(name, now)
  const c = audio()
  if (!c) return
  PATTERNS[name].forEach((n) => tone(c, n))
}

export const isSfx = (v: unknown): v is Sfx => typeof v === 'string' && v in PATTERNS
