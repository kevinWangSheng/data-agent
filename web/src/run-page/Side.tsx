// The right pane: the outcome, the numbers, the answer and its table, the verdicts, the task and
// the config. The answer comes before the task, so it is on the first screen.

import { useState, type ReactNode } from 'react'

import { Md } from '../code/markdown'
import { fmtCost, fmtDur, fmtTokens } from '../format'
import type { RunNotes, RunView } from '../run-view/model'
import { isIssue, isSql, narrationId, verifyId } from './outline'
import { DataTable, StatusChip, VerdictCell, VerdictChip } from './ui'

function Block({ title, right, children }: { title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <div className="sec">
      <div className="flex items-center mb-1.5">
        <h2 className="eyebrow">{title}</h2>
        <div className="flex-1" />
        {right}
      </div>
      {children}
    </div>
  )
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <div className="text-[10.5px] text-ink-3">{label}</div>
      <div className={`mono text-[14px] font-medium ${tone ?? ''}`}>{value}</div>
    </div>
  )
}

function Clipped({ children, height, label }: { children: ReactNode; height: number; label: string }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <div className="relative overflow-hidden" style={open ? undefined : { maxHeight: height }}>
        {children}
        {!open && <div className="absolute bottom-0 inset-x-0 h-10 bg-gradient-to-t from-panel to-transparent" />}
      </div>
      <button type="button" className="link mt-1" onClick={() => setOpen(!open)}>
        {open ? 'show less' : label}
      </button>
    </>
  )
}

const INFERRED_TITLE = 'The agent did not name the query; this is its last successful query before the answer.'

const OUTCOME_TONE = { success: 'text-ok', failure: 'text-fail', running: 'text-ink-2', cancelled: 'text-ink-2' } as const
const OUTCOME_BG = { success: 'bg-[var(--ok-bg)]', failure: 'bg-[var(--fail-bg)]', running: '', cancelled: '' } as const
const OUTCOME_TEXT = { success: 'Success', failure: 'Failure', running: 'Running', cancelled: 'Cancelled' } as const

export function Side({
  view,
  notes,
  replaying,
  numbers,
  onSelect,
  selected,
}: {
  view: RunView
  notes: RunNotes
  /** A replay is part-way: the outcome is not shown yet. */
  replaying: boolean
  numbers: Map<string, number>
  onSelect: (id: string) => void
  selected: string | null
}) {
  const { summary, answer } = view
  const status = replaying ? 'running' : summary.status
  // An inferred query is not the agent's own citation: the label says how it was found.
  const queryLabel = (id: string, inferred: true | undefined, prefix: string) =>
    inferred ? `last query before the answer · #${numbers.get(id)}` : `${prefix} #${numbers.get(id)}`
  const answerTarget = answer ? (answer.sqlStep ?? (view.narration.some((n) => n.seq === answer.seq) ? narrationId(answer.seq) : null)) : null
  const issues = view.steps.filter(isIssue).length
  const checks = [...new Map(view.dispatches.flatMap((d) => d.checks.map((c) => [c.check, c.name] as const))).entries()].sort(
    ([a], [b]) => Number(a) - Number(b) || a.localeCompare(b),
  )
  return (
    <div className="flex flex-col">
      <div className={`sec ${OUTCOME_BG[status]}`}>
        <div className="flex items-center gap-2 mb-1">
          <h2 className="eyebrow">Outcome</h2>
          <div className="flex-1" />
          <span className="mono text-[10.5px] text-ink-3">{summary.taskType ? `${summary.taskType} task` : 'task type not recorded'}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-[18px] font-semibold ${OUTCOME_TONE[status]}`}>{replaying ? 'Replaying…' : OUTCOME_TEXT[status]}</span>
          {replaying ? <span className="chip chip-info">REPLAY</span> : <StatusChip status={status} />}
        </div>
        {!replaying && notes.grade && (
          <div className="text-[12px] mt-1">
            <span className="text-ink-3">Grader: </span>
            {notes.grade.passed ? 'passed' : 'failed'}
            {notes.grade.detail && <span className="text-ink-2"> · {notes.grade.detail}</span>}
          </div>
        )}
        {!replaying && notes.failure_reason && (
          <div className="text-[12px] mt-2 pt-2 border-t border-line">
            <div className="font-semibold mb-0.5">Why it failed</div>
            <Clipped height={54} label="show all">
              <div className="text-ink-2">{notes.failure_reason}</div>
            </Clipped>
          </div>
        )}
      </div>

      <div className="sec">
        <div className="grid grid-cols-3 gap-y-2 gap-x-2">
          <Metric label={replaying ? 'elapsed' : 'wall time'} value={fmtDur(summary.durationMs)} />
          <Metric label="turns" value={summary.turns === null ? '—' : String(summary.turns)} />
          <Metric label="cost" value={summary.costUsd === null ? '—' : fmtCost(summary.costUsd)} />
          <Metric label="tool calls" value={String(view.steps.length)} />
          <Metric label="SQL" value={String(view.steps.filter(isSql).length)} />
          <Metric label="rejected / failed" value={String(issues)} tone={issues ? 'text-deny' : undefined} />
        </div>
        {summary.tokens && (
          <div className="mono text-[10.5px] text-ink-3 mt-2">
            tokens: in {fmtTokens(summary.tokens.uncachedInput)} uncached + {fmtTokens(summary.tokens.cachedInput)} cached · out {fmtTokens(summary.tokens.output)}
          </div>
        )}
      </div>

      <Block
        title={summary.taskType === 'dbt' ? 'Final report' : 'Answer'}
        right={
          answerTarget !== null ? (
            <button type="button" className="link" title={answer?.sqlInferred ? INFERRED_TITLE : undefined} onClick={() => onSelect(answerTarget)}>
              {answer?.sqlStep ? queryLabel(answer.sqlStep, answer.sqlInferred, 'from query') : 'in the trace'}
            </button>
          ) : undefined
        }
      >
        {answer ? (
          <Clipped height={150} label="show all">
            <Md>{answer.text}</Md>
          </Clipped>
        ) : (
          <div className="text-[12px] text-ink-3">{status === 'running' ? 'Not written yet.' : 'No final answer in this run.'}</div>
        )}
      </Block>

      {answer?.table && answer.sqlStep && (
        <Block
          title="Result table"
          right={
            <button type="button" className="link" title={answer.sqlInferred ? INFERRED_TITLE : undefined} onClick={() => onSelect(answer.sqlStep!)}>
              {queryLabel(answer.sqlStep, answer.sqlInferred, 'query')}
            </button>
          }
        >
          <DataTable table={answer.table} max={12} />
        </Block>
      )}

      <Block title={view.dispatches.length ? `Verifier · ${view.dispatches.length} dispatch${view.dispatches.length === 1 ? '' : 'es'}` : 'Verifier'}>
        {view.dispatches.length === 0 ? (
          <div className="text-[12px] text-ink-3">{summary.taskType === 'query' ? 'Not used: query tasks have no verifier.' : 'Not dispatched.'}</div>
        ) : (
          <>
            <table className="w-full text-[11.5px]">
              <thead>
                <tr className="text-ink-3">
                  <th className="text-left font-normal text-[10px] mono w-8">#</th>
                  <th className="text-left font-normal text-[10px] mono">model</th>
                  {checks.map(([n, name]) => (
                    <th key={n} className="font-normal text-[10px] mono w-[19px]" title={name}>
                      {n}
                    </th>
                  ))}
                  <th className="w-[52px]" />
                </tr>
              </thead>
              <tbody>
                {view.dispatches.map((d) => {
                  const models = d.overall !== null && d.checks.length ? [...new Set(d.checks.map((c) => c.model))] : ['']
                  const verdict = new Map(d.checks.map((c) => [`${c.model}\u0000${c.check}`, c.verdict]))
                  const id = verifyId(d.n)
                  return models.map((m, i) => (
                    <tr key={`${d.n}-${m}`} className={'cursor-pointer hover:bg-panel-2 ' + (selected === id ? 'bg-[var(--sel-strong)]' : '') + (i === 0 ? ' border-t border-line' : '')} onClick={() => onSelect(id)}>
                      <td className="mono text-ink-2 py-[2px]">{i === 0 ? `#${d.n}` : ''}</td>
                      <td className="mono text-ink-2 truncate max-w-[110px] text-[10.5px]" title={m}>
                        {m || <span className="text-ink-3">running…</span>}
                      </td>
                      {checks.map(([n, name]) => (
                        <td key={n} className="text-center py-[1px]">
                          {m && <VerdictCell verdict={verdict.get(`${m}\u0000${n}`)} title={`#${d.n} ${m} · CHECK ${n} ${name}: ${verdict.get(`${m}\u0000${n}`) ?? 'not run'}`} />}
                        </td>
                      ))}
                      <td className="text-right">{i === 0 && <VerdictChip verdict={d.overall} />}</td>
                    </tr>
                  ))
                })}
              </tbody>
            </table>
            <details className="mt-1.5" open>
              <summary className="text-[11px] text-ink-3">check names</summary>
              <dl className="grid grid-cols-[auto_1fr] text-[11px] gap-x-1.5 mt-1">
                {checks.map(([n, name]) => (
                  <div key={n} className="contents">
                    <dt className="mono text-ink-3">{n}</dt>
                    <dd className="text-ink-2">{name === n ? 'no name recorded' : name}</dd>
                  </div>
                ))}
              </dl>
            </details>
          </>
        )}
      </Block>

      <Block title="Task">
        {summary.task === null ? (
          <div className="text-[12px] text-ink-3">Not recorded.</div>
        ) : summary.task.length > 400 ? (
          <Clipped height={88} label={`show all · ${summary.task.length.toLocaleString()} chars`}>
            <Md className="md-small">{summary.task}</Md>
          </Clipped>
        ) : (
          <Md className="md-small">{summary.task}</Md>
        )}
      </Block>

      <Block title="Config">
        <dl className="kv">
          <dt>config</dt>
          <dd>{summary.config ?? 'not recorded'}</dd>
          <dt>model</dt>
          <dd>{summary.model ?? 'not recorded'}</dd>
          <dt>data source</dt>
          <dd>{summary.dataSource ?? 'not recorded'}</dd>
          <dt>skills</dt>
          <dd>{view.skills === null ? 'not recorded' : view.skills.length ? view.skills.join(', ') : 'none (Skill layer off)'}</dd>
          <dt>context tools</dt>
          <dd>
            discover {view.contextToolUses.discover} · hydrate {view.contextToolUses.hydrate} · dictionary_search {view.contextToolUses.dictionary_search}
          </dd>
        </dl>
      </Block>
    </div>
  )
}
