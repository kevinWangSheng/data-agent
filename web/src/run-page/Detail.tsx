// The centre pane: the key moments when nothing is selected; otherwise the selected step, thinking
// or text, phase, or verification.

import { useState, type ReactNode } from 'react'

import { ChatCode, CopyButton } from '../code/chat-code'
import { Md } from '../code/markdown'
import { fmtClock, fmtDur } from '../format'
import type { KeyMoment, Narration, RunView, Step, VerifierDispatch } from '../run-view/model'
import { fixId, isChange, narrationId, sectionStats, verifyId, type Item, type Section } from './outline'
import { DbtInputView, DbtResultView, SandboxInputView, SandboxResultView } from './ToolPanels'
import { isDbtExecute, isSandboxExec, parseDbtResult, parseSandboxResult } from './tool-views'
import { KIND_NAME, shortTool, toolKind } from './tools'
import {
  Caret,
  DataTable,
  Diff,
  Pre,
  Section as Block,
  StepChip,
  StepTag,
  Tag,
  VerdictCell,
  VerdictChip,
  errorLine,
  inputLine,
  phaseLabel,
  whoseStep,
} from './ui'

type Select = (id: string | null) => void

export type Selected =
  | { kind: 'section'; section: Section }
  | { kind: 'step'; step: Step }
  | { kind: 'narration'; narration: Narration }
  | null

const at = (ms: number | null) => (ms === null ? '' : `+${fmtClock(ms)}`)

function Header({ tag, title, right, sub }: { tag: ReactNode; title: ReactNode; right?: ReactNode; sub?: ReactNode }) {
  return (
    <div className="mb-4">
      <div className="flex items-center gap-2.5">
        {tag}
        <h1 className="text-[16px] font-semibold mono">{title}</h1>
        <div className="flex-1" />
        {right}
      </div>
      {sub && <div className="mt-1 text-[12px] text-ink-2 mono">{sub}</div>}
    </div>
  )
}

const CopyLink = () => <CopyButton text={typeof window === 'undefined' ? '' : window.location.href} label="Copy link" />

export function Detail({
  view,
  selectedId,
  selected,
  order,
  numbers,
  sections,
  onSelect,
}: {
  view: RunView
  selectedId: string | null
  selected: Selected
  /** Every item, in outline order, for previous and next. */
  order: Item[]
  numbers: Map<string, number>
  sections: Section[]
  onSelect: Select
}) {
  let body: ReactNode
  if (selectedId === null) body = <KeyMoments view={view} numbers={numbers} onSelect={onSelect} />
  else if (selected === null)
    body = (
      <div className="text-ink-3 text-[12.5px]">
        <span className="mono">{selectedId}</span> has not happened yet at this point of the replay, or is not part of this run.{' '}
        <button type="button" className="link" onClick={() => onSelect(null)}>
          Show the key moments
        </button>
      </div>
    )
  else if (selected.kind === 'section') {
    const dispatch = selected.section.phase.kind === 'verify' ? view.dispatches.find((d) => d.n === selected.section.phase.dispatch) : undefined
    body = dispatch ? (
      <DispatchDetail view={view} dispatch={dispatch} sections={sections} numbers={numbers} onSelect={onSelect} />
    ) : (
      <SectionDetail view={view} section={selected.section} numbers={numbers} onSelect={onSelect} />
    )
  } else if (selected.kind === 'step') body = <StepDetail key={selected.step.id} view={view} step={selected.step} number={numbers.get(selected.step.id)} order={order} numbers={numbers} onSelect={onSelect} />
  else body = <NarrationDetail narration={selected.narration} order={order} numbers={numbers} onSelect={onSelect} />
  return <div className="px-5 py-4 max-w-[1100px]">{body}</div>
}

// --- Key moments (nothing selected) ------------------------------------------------------------

function momentTarget(view: RunView, m: KeyMoment): string | null {
  if (m.kind === 'verdict' && m.dispatch !== null) return verifyId(m.dispatch)
  if (m.step !== null) return m.step
  return view.narration.some((n) => n.seq === m.seq) ? narrationId(m.seq) : null
}

function KeyMoments({ view, numbers, onSelect }: { view: RunView; numbers: Map<string, number>; onSelect: Select }) {
  const stepById = new Map(view.steps.map((s) => [s.id, s]))
  return (
    <div>
      <div className="flex items-baseline gap-3 mb-3">
        <h1 className="text-[17px] font-semibold">Key moments</h1>
        <span className="text-ink-3 text-[12px]">
          nothing selected · pick a row on the left, or press <kbd>j</kbd>
        </span>
      </div>
      {view.keyMoments.length === 0 ? (
        <div className="text-ink-3 text-[12.5px]">No verdicts, rejections, errors or answer yet.</div>
      ) : (
        <div className="border border-line rounded overflow-hidden" role="list">
          {view.keyMoments.map((m) => {
            const target = momentTarget(view, m)
            const step = m.step !== null ? stepById.get(m.step) : undefined
            const dispatch = m.kind === 'verdict' ? view.dispatches.find((d) => d.n === m.dispatch) : undefined
            const answerAt = view.narration.find((n) => n.seq === m.seq)?.atMs ?? null
            const time = dispatch ? dispatch.endMs : m.kind === 'answer' ? answerAt : (step?.startMs ?? null)
            return (
              <button
                type="button"
                role="listitem"
                key={`${m.kind}-${m.seq}`}
                disabled={target === null}
                onClick={() => target !== null && onSelect(target)}
                className="w-full grid grid-cols-[52px_150px_1fr_auto] gap-x-3 items-center px-2.5 py-1.5 border-t border-line first:border-t-0 hover:bg-panel-2 text-left text-[12.5px]"
              >
                <span className="mono text-[11px] text-ink-3">{at(time)}</span>
                <span>
                  {m.kind === 'verdict' && dispatch ? (
                    <span className="inline-flex items-center gap-1.5">
                      <span className="chip chip-na">VERDICT</span>
                      <VerdictChip verdict={dispatch.overall} />
                    </span>
                  ) : m.kind === 'answer' ? (
                    <span className="chip chip-info">ANSWER</span>
                  ) : step ? (
                    <StepChip step={step} />
                  ) : null}
                </span>
                <span className="truncate">
                  {dispatch ? (
                    <>
                      <span className="font-medium">Verifier #{dispatch.n}</span>
                      <span className="text-ink-2">
                        {' '}
                        · {dispatch.checks.filter((c) => c.verdict === 'FAIL').length} failed checks across {dispatch.models.length} model
                        {dispatch.models.length === 1 ? '' : 's'}
                      </span>
                    </>
                  ) : m.kind === 'answer' ? (
                    <span className="text-ink-2">{(view.answer?.text ?? '').replace(/[*`#>]+/g, '').replace(/\s+/g, ' ').slice(0, 200)}</span>
                  ) : step ? (
                    <>
                      <span className="mono text-[11.5px]">
                        #{numbers.get(step.id)} {shortTool(step.tool)}
                      </span>
                      <span className="text-ink-2"> · {errorLine(step)}</span>
                    </>
                  ) : null}
                </span>
                <span className="text-[11px] text-ink-3 whitespace-nowrap">{dispatch ? `verifier #${dispatch.n}` : step ? whoseStep(step) : 'main agent'}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

// --- A step ------------------------------------------------------------------------------------

function Neighbours({ id, order, numbers, onSelect }: { id: string; order: Item[]; numbers: Map<string, number>; onSelect: Select }) {
  const i = order.findIndex((x) => x.id === id)
  const name = (item: Item) => (item.kind === 'step' ? `#${numbers.get(item.id)} ${shortTool(item.step.tool)}` : item.narration.kind)
  const prev = i > 0 ? order[i - 1] : undefined
  const next = i >= 0 ? order[i + 1] : undefined
  return (
    <div className="flex gap-2 mt-6 pt-3 border-t border-line">
      {prev && (
        <button type="button" className="btn" onClick={() => onSelect(prev.id)}>
          ‹ {name(prev)}
        </button>
      )}
      <div className="flex-1" />
      {next && (
        <button type="button" className="btn" onClick={() => onSelect(next.id)}>
          {name(next)} ›
        </button>
      )}
    </div>
  )
}

/** SQL as the agent wrote it, or formatted on request (the formatter loads on first use). */
function SqlView({ sql, maxHeight }: { sql: string; maxHeight?: number }) {
  const [pretty, setPretty] = useState<string | null>(null)
  const toggle = () => {
    if (pretty !== null) return setPretty(null)
    void import('../code/pretty-sql').then(({ prettySql }) => setPretty(prettySql(sql)))
  }
  return (
    <div>
      <div className="flex justify-end mb-1">
        <button type="button" className="link" onClick={toggle} aria-pressed={pretty !== null}>
          {pretty !== null ? 'as written' : 'format'}
        </button>
      </div>
      <ChatCode code={pretty ?? sql} language="sql" maxHeight={maxHeight} />
    </div>
  )
}

function InputView({ step }: { step: Step }) {
  const input = (step.input ?? {}) as Record<string, unknown>
  const str = (k: string) => (typeof input[k] === 'string' ? (input[k] as string) : '')
  if (step.tool === 'Edit')
    return (
      <div>
        <div className="mono text-[11.5px] text-ink-2 mb-1">{str('file_path')}</div>
        <Diff before={str('old_string')} after={str('new_string')} />
      </div>
    )
  if (step.tool === 'Write')
    return (
      <div>
        <div className="mono text-[11.5px] text-ink-2 mb-1">{str('file_path')} · new file content</div>
        {str('file_path').endsWith('.sql') ? <ChatCode code={str('content')} language="sql" maxHeight={520} /> : <Pre text={str('content')} maxHeight={520} />}
      </div>
    )
  if (isSandboxExec(step.tool)) return <SandboxInputView input={step.input} />
  if (isDbtExecute(step.tool)) return <DbtInputView input={step.input} />
  if (step.tool === 'Bash')
    return (
      <div>
        {str('description') && <div className="text-[11px] text-ink-3 mb-1">{str('description')}</div>}
        <pre className="code plain">
          <span className="text-ink-3">$ </span>
          {str('command')}
        </pre>
      </div>
    )
  if (toolKind(step.tool) === 'agent')
    return (
      <details className="border border-line rounded">
        <summary className="px-2.5 py-1.5 flex items-center gap-2 text-[12px] text-ink-2">
          <Caret /> Prompt to the {str('subagent_type') || 'subagent'} · {str('prompt').length.toLocaleString()} chars
        </summary>
        <div className="px-2 pb-2">
          <Pre text={str('prompt')} />
        </div>
      </details>
    )
  const sql = step.sql ?? (str('sql') || null)
  const rest = Object.entries(input).filter(([k]) => k !== 'sql' && k !== 'reference_sql')
  return (
    <div className="space-y-2">
      {sql && <SqlView sql={sql} />}
      {str('reference_sql') && (
        <div>
          <div className="text-[11px] text-ink-3 mb-1">reference_sql (written by the verifier)</div>
          <SqlView sql={str('reference_sql')} maxHeight={360} />
        </div>
      )}
      {rest.length > 0 && (
        <dl className="kv">
          {rest.map(([k, v]) => (
            <KeyValue key={k} k={k} v={v} />
          ))}
        </dl>
      )}
      {!sql && rest.length === 0 && !str('reference_sql') && <div className="text-ink-3 text-[12px]">no arguments</div>}
    </div>
  )
}

function KeyValue({ k, v }: { k: string; v: unknown }) {
  return (
    <>
      <dt>{k}</dt>
      <dd className="whitespace-pre-wrap">{typeof v === 'string' ? v : JSON.stringify(v)}</dd>
    </>
  )
}

function ResultView({ view, step, onSelect }: { view: RunView; step: Step; onSelect: Select }) {
  const [raw, setRaw] = useState(false)
  if (step.result === null) return <div className="text-ink-3 text-[12px]">Waiting for the tool to return…</div>
  const dispatch = view.dispatches.find((d) => d.toolUseId === step.id)
  const sandboxParsed = isSandboxExec(step.tool) ? parseSandboxResult(step.result) : null
  const dbtParsed = isDbtExecute(step.tool) ? parseDbtResult(step.result) : null
  const structured = Boolean(step.table || dispatch || sandboxParsed !== null || dbtParsed !== null)
  return (
    <div className="space-y-2">
      {dispatch && (
        <div className="text-[12px]">
          The verifier's report went back to the main agent.{' '}
          <button type="button" className="link" onClick={() => onSelect(verifyId(dispatch.n))}>
            See verifier #{dispatch.n}
          </button>
        </div>
      )}
      {!raw && sandboxParsed !== null ? (
        <SandboxResultView result={step.result} parsed={sandboxParsed} />
      ) : !raw && dbtParsed !== null ? (
        <DbtResultView result={step.result} parsed={dbtParsed} />
      ) : step.table && !raw ? (
        <DataTable table={step.table} />
      ) : !dispatch || raw ? (
        <Pre text={step.result} maxHeight={520} />
      ) : null}
      {structured && (
        <button type="button" className="link" onClick={() => setRaw(!raw)}>
          {raw ? 'hide raw result' : 'raw result'}
        </button>
      )}
    </div>
  )
}

function StepDetail({ view, step, number, order, numbers, onSelect }: { view: RunView; step: Step; number: number | undefined; order: Item[]; numbers: Map<string, number>; onSelect: Select }) {
  const rejected = step.result !== null && (step.rejectedBy !== null || step.isError)
  const tone = step.rejectedBy === 'gateway' ? 'banner-block' : step.rejectedBy !== null ? 'banner-deny' : 'banner-fail'
  const what =
    step.rejectedBy === 'guard'
      ? 'Denied by a guard hook · the call never ran'
      : step.rejectedBy === 'gateway'
        ? 'Blocked by the read-only gateway · the SQL was refused before reaching the database'
        : step.rejectedBy === 'unknown'
          ? 'Rejected by a hook · the event file does not say which kind'
          : 'The tool ran and returned an error'
  return (
    <div>
      <Header
        tag={<StepTag step={step} />}
        title={shortTool(step.tool)}
        right={
          <>
            <StepChip step={step} ok />
            <CopyLink />
          </>
        }
        sub={
          <>
            #{number} · {whoseStep(step)} · started {at(step.startMs) || 'at an unknown time'} ·{' '}
            {step.result === null ? 'running' : step.durationMs !== null ? `took ${fmtDur(step.durationMs)}` : 'no timing event'} · {KIND_NAME[toolKind(step.tool)]}
          </>
        }
      />
      {rejected && (
        <div className={`banner ${tone} mb-4`}>
          <div className="font-semibold text-[12px] mb-0.5">{what}</div>
          <div className="text-[12px] mono whitespace-pre-wrap">{errorLine(step)}</div>
        </div>
      )}
      <Block title="Input">
        <InputView step={step} />
      </Block>
      <Block title="Result">
        <ResultView view={view} step={step} onSelect={onSelect} />
      </Block>
      <Neighbours id={step.id} order={order} numbers={numbers} onSelect={onSelect} />
    </div>
  )
}

function NarrationDetail({ narration, order, numbers, onSelect }: { narration: Narration; order: Item[]; numbers: Map<string, number>; onSelect: Select }) {
  const thinking = narration.kind === 'thinking'
  return (
    <div>
      <Header
        tag={<Tag kind={narration.kind} />}
        title={thinking ? 'thinking' : 'text'}
        right={<CopyLink />}
        sub={<>main agent · {at(narration.atMs) || 'unknown time'} · {thinking ? "the model's reasoning, collapsed by default" : 'text the model wrote'}</>}
      />
      {thinking ? (
        <details className="border border-line rounded">
          <summary className="px-2.5 py-1.5 flex items-center gap-2 text-[12px] text-ink-2">
            <Caret /> Thinking · {narration.text.length.toLocaleString()} chars
            <span className="text-ink-3 truncate italic flex-1">{narration.text.replace(/\s+/g, ' ').slice(0, 160)}</span>
          </summary>
          <div className="px-3 pb-3 pt-1 text-[12.5px] whitespace-pre-wrap text-ink-2">{narration.text}</div>
        </details>
      ) : (
        <div className="border border-line rounded p-3">
          <Md>{narration.text}</Md>
        </div>
      )}
      <Neighbours id={narrationId(narration.seq)} order={order} numbers={numbers} onSelect={onSelect} />
    </div>
  )
}

// --- A phase -------------------------------------------------------------------------------------

function ItemList({ items, numbers, onSelect }: { items: Item[]; numbers: Map<string, number>; onSelect: Select }) {
  return (
    <div className="border border-line rounded overflow-hidden">
      {items.map((item) => (
        <button
          type="button"
          key={item.id}
          className="w-full flex items-center gap-2 px-2 h-[25px] border-t border-line first:border-t-0 hover:bg-panel-2 text-left"
          onClick={() => onSelect(item.id)}
        >
          <span className="mono text-[10px] text-ink-3 w-7 text-right shrink-0">{item.kind === 'step' ? numbers.get(item.id) : ''}</span>
          {item.kind === 'step' ? <StepTag step={item.step} /> : <Tag kind={item.narration.kind} />}
          <span className="mono text-[11.5px] text-ink-2 w-28 truncate shrink-0">{item.kind === 'step' ? shortTool(item.step.tool) : item.narration.kind}</span>
          <span className={'text-[12px] truncate flex-1 ' + (item.kind === 'narration' ? 'italic text-ink-3' : '')}>
            {item.kind === 'step' ? inputLine(item.step) : item.narration.text.replace(/\s+/g, ' ')}
          </span>
          {item.kind === 'step' && <StepChip step={item.step} />}
          {item.kind === 'step' && item.step.durationMs !== null && <span className="mono text-[10.5px] text-ink-3 w-12 text-right shrink-0">{fmtDur(item.step.durationMs)}</span>}
        </button>
      ))}
    </div>
  )
}

function Change({ step, numbers, onSelect }: { step: Step; numbers: Map<string, number>; onSelect: Select }) {
  const input = (step.input ?? {}) as Record<string, unknown>
  const str = (k: string) => (typeof input[k] === 'string' ? (input[k] as string) : '')
  return (
    <div>
      <button type="button" className="flex items-center gap-2 mb-1 text-left hover:underline" onClick={() => onSelect(step.id)}>
        <StepTag step={step} />
        <span className="mono text-[11.5px]">
          #{numbers.get(step.id)} {shortTool(step.tool)} {str('file_path') || inputLine(step)}
        </span>
        <StepChip step={step} />
      </button>
      {step.tool === 'Edit' ? (
        <Diff before={str('old_string')} after={str('new_string')} />
      ) : step.tool === 'Write' ? (
        <details>
          <summary className="text-[11.5px] text-ink-2 flex items-center gap-1">
            <Caret /> new file, {str('content').split('\n').length} lines
          </summary>
          <div className="mt-1">{str('file_path').endsWith('.sql') ? <ChatCode code={str('content')} language="sql" maxHeight={360} /> : <Pre text={str('content')} maxHeight={360} />}</div>
        </details>
      ) : (
        <div className="mono text-[11.5px] text-ink-2 pl-10 truncate">{(step.result ?? '').split('\n').filter(Boolean).slice(-2).join(' · ')}</div>
      )}
    </div>
  )
}

function FailedChecks({ dispatch }: { dispatch: VerifierDispatch }) {
  const failed = dispatch.checks.filter((c) => c.verdict === 'FAIL')
  return (
    <ul className="text-[12px] space-y-0.5">
      {failed.map((c, i) => (
        <li key={i}>
          <span className="mono">CHECK {c.check}</span> {c.name !== c.check ? c.name : ''} <span className="text-ink-3">· {c.model}</span>
        </li>
      ))}
    </ul>
  )
}

function SectionDetail({ view, section, numbers, onSelect }: { view: RunView; section: Section; numbers: Map<string, number>; onSelect: Select }) {
  const stats = sectionStats(section)
  const changes = section.items.flatMap((i) => (i.kind === 'step' && isChange(i.step) ? [i.step] : []))
  const after = section.phase.kind === 'fix' ? view.dispatches.find((d) => d.n === section.phase.dispatch) : undefined
  return (
    <div>
      <Header
        tag={<span className="chip chip-na">PHASE</span>}
        title={phaseLabel(section.phase)}
        right={<CopyLink />}
        sub={
          <>
            main agent · {at(stats.startMs) || 'unknown start'} · {stats.durationMs !== null ? fmtDur(stats.durationMs) : '—'} · {stats.calls} tool calls · {stats.sql} SQL · {stats.changes} changes ·{' '}
            {stats.issues} rejected or failed
          </>
        }
      />
      {after && after.overall === 'FAIL' && (
        <div className="banner banner-fail mb-4">
          <div className="text-[12px] font-semibold mb-1">Responding to verifier #{after.n}: FAIL</div>
          <FailedChecks dispatch={after} />
        </div>
      )}
      {changes.length > 0 && (
        <Block title={`Changes in this phase · ${changes.length}`}>
          <div className="space-y-3">
            {changes.map((s) => (
              <Change key={s.id} step={s} numbers={numbers} onSelect={onSelect} />
            ))}
          </div>
        </Block>
      )}
      <Block title={`Steps, thinking and text · ${section.items.length}`}>
        <ItemList items={section.items} numbers={numbers} onSelect={onSelect} />
      </Block>
    </div>
  )
}

// --- A verification ------------------------------------------------------------------------------

function CheckGrid({ dispatch }: { dispatch: VerifierDispatch }) {
  const checks = [...new Map(dispatch.checks.map((c) => [c.check, c.name])).entries()]
  const verdict = new Map(dispatch.checks.map((c) => [`${c.model}\u0000${c.check}`, c.verdict]))
  const models = [...new Set(dispatch.checks.map((c) => c.model))]
  return (
    <div className="flex flex-wrap gap-6 items-start">
      <table className="text-[11.5px]">
        <thead>
          <tr>
            <th />
            {checks.map(([n, name]) => (
              <th key={n} className="mono font-normal text-ink-3 text-[10px] px-[1px]" title={name}>
                {n}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {models.map((m) => (
            <tr key={m}>
              <td className="mono pr-2 text-ink-2 truncate max-w-[320px]" title={m}>
                {m}
              </td>
              {checks.map(([n, name]) => {
                const v = verdict.get(`${m}\u0000${n}`)
                return (
                  <td key={n} className="px-[1px] py-[1px]">
                    <VerdictCell verdict={v} title={`${m} · CHECK ${n} ${name}: ${v ?? 'not run'}`} />
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[11.5px] min-w-[220px] flex-1">
        {checks.map(([n, name]) => (
          <div key={n} className="contents">
            <dt className="mono text-ink-3 text-right">{n}</dt>
            <dd className="text-ink-2">{name === n ? <span className="text-ink-3">no name recorded</span> : name}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

function DispatchDetail({ view, dispatch, sections, numbers, onSelect }: { view: RunView; dispatch: VerifierDispatch; sections: Section[]; numbers: Map<string, number>; onSelect: Select }) {
  const stepById = new Map(view.steps.map((s) => [s.id, s]))
  const agent = dispatch.toolUseId !== null ? stepById.get(dispatch.toolUseId) : undefined
  const verifierSteps = dispatch.steps.flatMap((id) => {
    const s = stepById.get(id)
    return s ? [{ kind: 'step' as const, id, seq: s.seq, step: s }] : []
  })
  const before = dispatch.n > 1 ? sections.find((s) => s.id === fixId(dispatch.n - 1)) : undefined
  const changes = before ? before.items.flatMap((i) => (i.kind === 'step' && isChange(i.step) ? [i.step] : [])) : []
  const previous = view.dispatches.find((d) => d.n === dispatch.n - 1)
  const done = dispatch.overall !== null
  const span = dispatch.startMs !== null && dispatch.endMs !== null ? fmtDur(dispatch.endMs - dispatch.startMs) : done ? '' : 'running'
  return (
    <div>
      <Header
        tag={<Tag kind="agent" />}
        title={`Verifier #${dispatch.n}`}
        right={
          <>
            <VerdictChip verdict={dispatch.overall} />
            <CopyLink />
          </>
        }
        sub={
          <>
            read-only subagent · {at(dispatch.startMs) || 'start unknown'} {span && `· ${span}`} · {verifierSteps.length} tool calls
          </>
        }
      />
      <div className="text-[12px] text-ink-2 mb-4 flex flex-wrap gap-x-3">
        Models checked:{' '}
        {dispatch.models.map((m) => (
          <span key={m} className="mono text-ink break-all">
            {m}
          </span>
        ))}
      </div>
      {done ? (
        <Block title="Verdicts per model and check">
          <CheckGrid dispatch={dispatch} />
          {dispatch.checks.some((c) => c.verdict === 'FAIL') && (
            <div className="mt-3">
              <FailedChecks dispatch={dispatch} />
            </div>
          )}
        </Block>
      ) : (
        <div className="banner banner-info mb-4 text-[12px]">The verifier is still running.</div>
      )}
      {before && (
        <Block title={`What the main agent changed after #${dispatch.n - 1} (${previous?.overall ?? '?'}) · ${changes.length}`}>
          {changes.length === 0 ? (
            <div className="text-[12px] text-ink-3">No edits, dbt runs or shell calls between the two verifications.</div>
          ) : (
            <div className="space-y-3">
              {changes.map((s) => (
                <Change key={s.id} step={s} numbers={numbers} onSelect={onSelect} />
              ))}
            </div>
          )}
        </Block>
      )}
      {dispatch.report !== null && (
        <Block title="Full report">
          <details className="border border-line rounded">
            <summary className="px-2.5 py-1.5 text-[12px] text-ink-2 flex items-center gap-2">
              <Caret /> The verifier's report as written · {dispatch.report.length.toLocaleString()} chars
            </summary>
            <div className="px-3 pb-3">
              <Md className="md-small">{dispatch.report}</Md>
            </div>
          </details>
        </Block>
      )}
      <Block title={`The verifier's tool calls · ${verifierSteps.length}`}>
        {verifierSteps.length ? <ItemList items={verifierSteps} numbers={numbers} onSelect={onSelect} /> : <div className="text-[12px] text-ink-3">None yet.</div>}
      </Block>
      {agent && (
        <Block title="Dispatch call">
          <button type="button" className="link" onClick={() => onSelect(agent.id)}>
            #{numbers.get(agent.id)} {agent.tool} · the main agent's call that carried this verification
          </button>
        </Block>
      )}
    </div>
  )
}
