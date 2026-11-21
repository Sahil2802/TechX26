import { useState } from 'react'

export const GATE_ANCHOR_ID = 'human-gate-anchor'

// Floating "human approval needed" notice. Clicking it opens the Action required tab and scrolls to the gate.
// Dismissal is remembered per set of pending ids, so a new proposal brings it back.
export function ApprovalPopup({ ids, onOpen }: { ids: number[]; onOpen: () => void }) {
  const key = ids.join(',')
  const [dismissed, setDismissed] = useState<string | null>(null)
  if (ids.length === 0 || dismissed === key) return null

  const open = () => {
    onOpen()
    setDismissed(key)
    setTimeout(() => document.getElementById(GATE_ANCHOR_ID)?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 150)
  }
  const label = `Human approval needed: ${ids.length} action${ids.length === 1 ? '' : 's'} waiting (#${ids.join(', #')})`
  return (
    <div className="popup" role="alert" onClick={open}>
      <span>🔔 {label} — click to review</span>
      <button aria-label="Dismiss" onClick={(e) => { e.stopPropagation(); setDismissed(key) }}>×</button>
    </div>
  )
}
