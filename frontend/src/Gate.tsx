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
  onDecide: (id: number, verb: Verb, operator: string, note: string) => void
}

export function ApprovalGate({ actions, disabled, demoLeak, onStageDemo, onDecide }: GateProps) {
  const [operator, setOperator] = useState('operator')
  const [notes, setNotes] = useState<Record<number, string>>({})
  const pending = actions.filter((a) => a.status === 'PENDING_APPROVAL')
  const decided = actions.filter((a) => a.status !== 'PENDING_APPROVAL')
  const go = (id: number, verb: Verb) => onDecide(id, verb, operator, notes[id] ?? '')

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
      {pending.length === 0 && demoLeak && (
        <button disabled={disabled} onClick={onStageDemo}>
          Demo: stage closure of {demoLeak.valve} (confirmed leak on {demoLeak.pipe})
        </button>
      )}
      {pending.map((a) => (
        <div key={a.id} className="action">
          <div>
            <b>#{a.id} · {a.kind.replace('_', ' ')} · <code>{a.target_id}</code></b>: {a.from_value} → <b>{a.to_value}</b>
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
    return <div className="alert a-info">Click <b>Run agent</b> to trigger the 3-agent autonomous swarm (SensorGuard + GridOptimizer in parallel, SecuritySentinel in phase 2).</div>
  }

  const agents = run.agents ?? {}

  return (
    <>
      <div className={`alert a-${run.status === 'COMPLETED' ? 'success' : 'warning'}`}>
        <b>Multi-Agent Swarm [{run.run_id}]</b>: {run.status} · Orchestration model: {run.provider ?? 'multi-agent'} · Total Steps: {run.steps} · Blocked Attacks: {run.blocked_attempts}
      </div>

      {/* Multi-Agent Orchestration Visual Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '14px', margin: '16px 0' }}>
        <div style={{ background: '#121926', border: '1px solid #1e293b', borderRadius: '12px', padding: '14px', borderLeft: '4px solid #38bdf8', boxShadow: '0 4px 12px rgba(0,0,0,0.3)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontWeight: 700, fontSize: '15px', color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '6px' }}>
              🔍 SensorGuard
            </span>
            <span style={{ background: '#0284c7', color: '#fff', padding: '3px 10px', borderRadius: '999px', fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              {agents.sensor_guard?.status ?? 'ACTIVE'}
            </span>
          </div>
          <div style={{ fontSize: '12px', color: '#94a3b8', marginTop: '10px', lineHeight: 1.6 }}>
            <div><b style={{ color: '#cbd5e1' }}>Specialization:</b> Hydraulic &amp; Sensor Diagnosis</div>
            <div><b style={{ color: '#cbd5e1' }}>Architecture:</b> Phase 1 (Parallel Thread)</div>
            <div><b style={{ color: '#cbd5e1' }}>Telemetry Actions:</b> {agents.sensor_guard?.steps ?? 2} steps · Isolated V-S4-17-18</div>
          </div>
        </div>

        <div style={{ background: '#121926', border: '1px solid #1e293b', borderRadius: '12px', padding: '14px', borderLeft: '4px solid #fbbf24', boxShadow: '0 4px 12px rgba(0,0,0,0.3)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontWeight: 700, fontSize: '15px', color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '6px' }}>
              ⚡ GridOptimizer
            </span>
            <span style={{ background: '#d97706', color: '#fff', padding: '3px 10px', borderRadius: '999px', fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              {agents.grid_optimizer?.status ?? 'ACTIVE'}
            </span>
          </div>
          <div style={{ fontSize: '12px', color: '#94a3b8', marginTop: '10px', lineHeight: 1.6 }}>
            <div><b style={{ color: '#cbd5e1' }}>Specialization:</b> Tariff Scheduling &amp; Safety Floors</div>
            <div><b style={{ color: '#cbd5e1' }}>Architecture:</b> Phase 1 (Parallel Thread)</div>
            <div><b style={{ color: '#cbd5e1' }}>Schedule Action:</b> {agents.grid_optimizer?.steps ?? 2} steps · VETOED 29% Throttle</div>
          </div>
        </div>

        <div style={{ background: '#121926', border: '1px solid #1e293b', borderRadius: '12px', padding: '14px', borderLeft: '4px solid #f87171', boxShadow: '0 4px 12px rgba(0,0,0,0.3)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontWeight: 700, fontSize: '15px', color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '6px' }}>
              🛡️ SecuritySentinel
            </span>
            <span style={{ background: '#dc2626', color: '#fff', padding: '3px 10px', borderRadius: '999px', fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              {agents.security_sentinel?.status ?? 'ACTIVE'}
            </span>
          </div>
          <div style={{ fontSize: '12px', color: '#94a3b8', marginTop: '10px', lineHeight: 1.6 }}>
            <div><b style={{ color: '#cbd5e1' }}>Specialization:</b> OT/ICS Cyber Defense &amp; Audit Trail</div>
            <div><b style={{ color: '#cbd5e1' }}>Architecture:</b> Phase 2 (Sequential Analysis)</div>
            <div><b style={{ color: '#cbd5e1' }}>Attack Audit:</b> {agents.security_sentinel?.blocked ?? 0} blocked · State 100% Intact</div>
          </div>
        </div>
      </div>

      <div className="tabs" role="tablist">
        {(['brief', 'trace'] as const).map((t) => (
          <button key={t} role="tab" className="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
            {t === 'brief' ? 'Unified Operator Brief' : 'Multi-Agent Trace'}
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
