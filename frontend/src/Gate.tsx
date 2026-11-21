import { useState } from 'react'
import { Markdown } from './Markdown'
import { GATE_ANCHOR_ID } from './Popup'
import type { Action, Run, Verb } from './types'

const WHO_CLASS: Record<string, string> = { CODE: 'p-code', LLM: 'p-llm', HUMAN: 'p-human', ATTACKER: 'p-att' }

interface GateProps {
  actions: Action[]
  disabled: boolean
  demoLeak: { valve: string; pipe: string | null } | null
  onStageDemo: () => void
  onStageIsolationFlush?: () => void
  onDecide: (id: number, verb: Verb, operator: string, note: string, token?: string) => void
}

export function ApprovalGate({ actions, disabled, demoLeak, onStageDemo, onStageIsolationFlush, onDecide }: GateProps) {
  const [operator, setOperator] = useState('operator')
  const [notes, setNotes] = useState<Record<number, string>>({})
  const [tokens, setTokens] = useState<Record<number, string>>({})
  const pending = actions.filter((a) => a.status === 'PENDING_APPROVAL')
  const decided = actions.filter((a) => a.status !== 'PENDING_APPROVAL')
  const go = (id: number, verb: Verb) => onDecide(id, verb, operator, notes[id] ?? '', tokens[id] || undefined)

  return (
    <>
      <div id={GATE_ANCHOR_ID} />
      <div className="mut">Nothing moves until a human approves. Approval re-checks every guard against the live state first.</div>
      <div className="bar">
        <label>Operator ID
          <input value={operator} onChange={(e) => setOperator(e.target.value)} autoComplete="off" />
        </label>
      </div>

      {pending.length === 0 && <div className="alert a-info">No actions are waiting for approval.</div>}
      <div className="bar">
        {pending.length === 0 && demoLeak && (
          <button disabled={disabled} onClick={onStageDemo}>
            Demo: stage closure of {demoLeak.valve} (confirmed leak on {demoLeak.pipe})
          </button>
        )}
        {onStageIsolationFlush && (
          <button disabled={disabled} onClick={onStageIsolationFlush}>
            Demo: Stage Sector 2 Isolation &amp; Flush (U-5.1)
          </button>
        )}
      </div>
      {pending.map((a) => (
        <div key={a.id} className="action">
          <div>
            <b>#{a.id} · {a.kind.replace('_', ' ')} · <code>{a.target_id}</code></b>: {a.from_value} → <b>{a.to_value}</b>
            {a.from_untrusted_text && (
              <span className="pill p-att" style={{ marginLeft: '0.5rem' }}>⚠️ ORIGIN: UNTRUSTED TEXT</span>
            )}
            <br /><span className="mut">Proposed by <code>{a.proposed_by}</code>: {a.rationale}</span>
            <div className="scroll">
              <table>
                <thead><tr><th>Check</th><th>Result</th><th>Detail</th></tr></thead>
                <tbody>
                  {a.guard_rows.map((g) => (
                    <tr key={g.Check}>
                      <td>{g.Check}</td>
                      <td><span className={g.Result === 'PASS' ? 'good' : 'bad'}>{g.Result}</span></td>
                      <td>{g.Detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="btns">
            <input placeholder="Note (optional)" maxLength={300} value={notes[a.id] ?? ''}
              onChange={(e) => setNotes({ ...notes, [a.id]: e.target.value })} />
            <input placeholder="Approval Token (optional, U-5.4)" value={tokens[a.id] ?? ''}
              onChange={(e) => setTokens({ ...tokens, [a.id]: e.target.value })} />
            <button className="primary" disabled={disabled} onClick={() => go(a.id, 'approve')}>Approve &amp; execute</button>
            <button disabled={disabled} onClick={() => go(a.id, 'reject')}>Reject</button>
          </div>
        </div>
      ))}

      <details open={decided.length > 0}>
        <summary>Decided and blocked actions ({decided.length})</summary>
        <div className="scroll box">
          <table>
            <thead><tr>{['#', 'Status', 'Target', 'From → to', 'Proposed by', 'Approver', 'Note'].map((c) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>
              {decided.map((a) => (
                <tr key={a.id}>
                  <td>{a.id}</td><td>{a.status}</td><td>{a.target_id}</td><td>{a.from_value} → {a.to_value}</td>
                  <td>{a.proposed_by}</td><td>{a.approver ?? ''}</td><td>{a.note ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {decided.filter((a) => a.status === 'EXECUTED').map((a) => (
          <button key={a.id} className="revert" disabled={disabled} onClick={() => go(a.id, 'revert')}>
            Revert #{a.id} ({a.target_id} back to {a.from_value})
          </button>
        ))}
      </details>
    </>
  )
}

export function RunPanel({ run }: { run: Run | null }) {
  const [tab, setTab] = useState<'brief' | 'trace'>('brief')
  if (!run) {
    return <div className="alert a-info">Click <b>Run agent</b> to get the LLM briefing and the full decision trace. The panels above are already computed by code.</div>
  }
  return (
    <>
      <div className={`alert a-${run.status === 'COMPLETED' ? 'success' : 'warning'}`}>
        Run {run.run_id}: {run.status} · model {run.provider ?? 'none (code-only brief)'} · {run.steps} steps · {run.blocked_attempts} blocked attempt(s)
      </div>
      <div className="tabs" role="tablist">
        {(['brief', 'trace'] as const).map((t) => (
          <button key={t} role="tab" className="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
            {t === 'brief' ? 'Operator brief' : 'Decision trace'}
          </button>
        ))}
      </div>
      {tab === 'brief' ? (
        <div className="box"><Markdown source={run.brief_md} /></div>
      ) : (
        <div className="box scroll">
          <table>
            <thead><tr>{['#', 'Who', 'Step', 'Detail'].map((c) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>
              {run.trace.map((r) => (
                <tr key={r['#']}>
                  <td>{r['#']}</td>
                  <td><span className={`pill ${WHO_CLASS[r.Who] ?? 'p-code'}`}>{r.Who}</span></td>
                  <td>{r.Step}</td><td>{r.Detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
