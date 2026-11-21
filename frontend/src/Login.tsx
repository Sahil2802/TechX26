import { useState, type FormEvent } from 'react'
import * as api from './api'
import type { PlantInfo } from './types'

export interface Session { token: string; role: string; plants: PlantInfo[]; plant: string }

export function Login({ onReady }: { onReady: (session: Session) => void }) {
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<{ role: string; plants: PlantInfo[] } | null>(null)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const r = await api.login(token)
      if (r.plants.length === 1) onReady({ token, role: r.role, plants: r.plants, plant: r.plants[0].id })
      else setPending(r)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (pending) {
    return (
      <main className="login">
        <h1>Choose a plant</h1>
        <div className="plant-cards">
          {pending.plants.map((p) => (
            <button key={p.id} onClick={() => onReady({ token, role: pending.role, plants: pending.plants, plant: p.id })}>
              <b>{p.name}</b><span className="mut">Plant {p.id}</span>
            </button>
          ))}
        </div>
      </main>
    )
  }

  return (
    <main className="login">
      <h1>AquaGrid <span className="mut">operator console</span></h1>
      <form onSubmit={submit}>
        <label>Operator token
          <input type="password" value={token} autoComplete="off" autoFocus onChange={(e) => setToken(e.target.value)} />
        </label>
        {error && <div className="alert a-error" role="alert">{error}</div>}
        <button className="primary" type="submit" disabled={busy}>Sign in</button>
      </form>
    </main>
  )
}
