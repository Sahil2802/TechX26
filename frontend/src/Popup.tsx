export const GATE_ANCHOR_ID = 'human-gate-anchor'

// Persistent "human approval needed" banner, shown on every tab while anything is pending.
// No dismiss and no approve/reject here: the only approval path is ApprovalGate.
export function ApprovalBanner({ ids, onOpen }: { ids: number[]; onOpen: () => void }) {
  if (ids.length === 0) return null
  const open = () => {
    onOpen()
    setTimeout(() => document.getElementById(GATE_ANCHOR_ID)?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 150)
  }
  return (
    <div className="banner" role="alert">
      <span>
        <b>Human approval needed:</b> {ids.length} action{ids.length === 1 ? '' : 's'} waiting (#{ids.join(', #')}). Nothing moves until you decide.
      </span>
      <button className="banner-btn" onClick={open}>Review in Approvals</button>
    </div>
  )
}
