// Small display pieces shared by the run page's panes.

import type { CSSProperties, ReactNode } from 'react'

import type { Phase, ResultTable, RunStatus, Step } from '../run-view/model'
import { formatDbtCommandLine, isDbtExecute, parseDbtInput } from './tool-views'
import { KIND_NAME, KIND_TAG, toolKind } from './tools'

export function phaseLabel(phase: Phase): string {
  switch (phase.kind) {
    case 'orient':
      return 'Learn the environment'
    case 'explore':
      return 'Explore the data'
    case 'build':
      return 'Write and run dbt'
    case 'self-check':
      return 'Self-check'
    case 'answer':
      return 'Answer'
    case 'verify':
      return `Verifier #${phase.dispatch}`
    case 'fix':
      return `Fix after #${phase.dispatch}`
  }
}

type TagKind = ReturnType<typeof toolKind> | 'thinking' | 'text'

export function Tag({ kind, label, title }: { kind: TagKind; label?: string; title?: string }) {
  const text = kind === 'thinking' ? 'THK' : kind === 'text' ? 'MSG' : KIND_TAG[kind]
  const name = kind === 'thinking' ? 'Model thinking' : kind === 'text' ? 'Model text' : KIND_NAME[kind]
  return (
    <span className="tag" style={{ '--kc': `var(--k-${kind})` } as CSSProperties} title={title ?? name}>
      {label ?? text}
    </span>
  )
}

export const StepTag = ({ step }: { step: Step }) => <Tag kind={toolKind(step.tool)} />

/** Rejected, failed, running or nothing: the chip a step row shows. */
export function StepChip({ step, ok = false }: { step: Step; ok?: boolean }) {
  if (step.result === null) return <span className="chip chip-info">RUNNING</span>
  if (step.rejectedBy === 'guard')
    return (
      <span className="chip chip-deny" title="Refused by a guard hook before it ran">
        DENIED · guard
      </span>
    )
  if (step.rejectedBy === 'gateway')
    return (
      <span className="chip chip-block" title="Refused by the read-only SQL gateway">
        BLOCKED · gateway
      </span>
    )
  if (step.rejectedBy === 'unknown')
    return (
      <span className="chip chip-deny" title="Refused by a hook; the event file does not say which kind">
        REJECTED
      </span>
    )
  if (step.isError) return <span className="chip chip-fail">ERROR</span>
  return ok ? <span className="chip chip-ok">OK</span> : null
}

export function VerdictChip({ verdict }: { verdict: string | null }) {
  if (verdict === null) return <span className="chip chip-na">PENDING</span>
  const tone = verdict === 'PASS' ? 'chip-ok' : verdict === 'FAIL' ? 'chip-fail' : verdict === 'NEEDS_INFO' ? 'chip-deny' : 'chip-na'
  return <span className={`chip ${tone}`}>{verdict}</span>
}

/** One check's verdict as a lettered cell. */
export function VerdictCell({ verdict, title }: { verdict: string | undefined; title: string }) {
  const [letter, tone] =
    verdict === 'PASS'
      ? ['P', 'v-pass']
      : verdict === 'FAIL'
        ? ['F', 'v-fail']
        : verdict === 'NEEDS_INFO'
          ? ['?', 'v-info']
          : verdict === undefined
            ? ['', 'v-na']
            : ['–', 'v-na']
  return (
    <span className={`vc ${tone}`} title={title}>
      {letter}
    </span>
  )
}

export const STATUS_TEXT: Record<RunStatus, string> = {
  running: 'RUNNING',
  success: 'SUCCESS',
  failure: 'FAILURE',
  cancelled: 'CANCELLED',
}

const STATUS_TONE: Record<RunStatus, string> = {
  running: 'chip-info',
  success: 'chip-ok',
  failure: 'chip-fail',
  cancelled: 'chip-na',
}

export function StatusChip({ status }: { status: RunStatus }) {
  return <span className={`chip ${STATUS_TONE[status]}`}>{STATUS_TEXT[status]}</span>
}

export function Caret() {
  return (
    <svg className="caret" width="8" height="8" viewBox="0 0 8 8" aria-hidden>
      <path d="M2 1 L6 4 L2 7 Z" fill="currentColor" />
    </svg>
  )
}

export function Heading({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex items-center gap-2 mb-1.5">
      <h2 className="eyebrow">{children}</h2>
      <div className="flex-1 h-px bg-line" />
      {right}
    </div>
  )
}

export function Section({ title, right, children }: { title: ReactNode; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="mb-5">
      <Heading right={right}>{title}</Heading>
      {children}
    </section>
  )
}

export function Pre({ text, maxHeight = 420 }: { text: string; maxHeight?: number }) {
  return (
    <pre className="code plain" style={{ maxHeight }}>
      {text}
    </pre>
  )
}

const NUMBER = /^-?[\d.,]+$/

export function DataTable({ table, max = 200 }: { table: ResultTable; max?: number }) {
  const shown = table.rows.slice(0, max)
  return (
    <div className="tbl-wrap">
      <table className="tbl">
        <thead>
          <tr>
            <th className="rn">#</th>
            {table.columns.map((c, i) => (
              <th key={i}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, i) => (
            <tr key={i}>
              <td className="rn">{i + 1}</td>
              {row.map((cell, j) => (
                <td key={j} className={NUMBER.test(cell.trim()) ? 'num' : cell.trim() === 'NULL' ? 'null' : undefined}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {table.rows.length > max && <div className="tbl-more">{table.rows.length - max} more rows not shown</div>}
    </div>
  )
}

/** An Edit call's change: the lines that differ, with two lines of context. */
export function Diff({ before, after, maxHeight = 320 }: { before: string; after: string; maxHeight?: number }) {
  const o = before.split('\n')
  const n = after.split('\n')
  let head = 0
  while (head < o.length && head < n.length && o[head] === n[head]) head++
  let tail = 0
  while (tail < o.length - head && tail < n.length - head && o[o.length - 1 - tail] === n[n.length - 1 - tail]) tail++
  return (
    <pre className="code diff" style={{ maxHeight }}>
      {head > 2 && <div className="d-skip">… {head - 2} unchanged lines</div>}
      {o.slice(Math.max(0, head - 2), head).map((l, i) => (
        <div key={`t${i}`} className="d-ctx">
          {' '}
          {l}
        </div>
      ))}
      {o.slice(head, o.length - tail).map((l, i) => (
        <div key={`o${i}`} className="d-del">
          -{l}
        </div>
      ))}
      {n.slice(head, n.length - tail).map((l, i) => (
        <div key={`n${i}`} className="d-add">
          +{l}
        </div>
      ))}
      {o.slice(o.length - tail, Math.min(o.length, o.length - tail + 2)).map((l, i) => (
        <div key={`b${i}`} className="d-ctx">
          {' '}
          {l}
        </div>
      ))}
      {tail > 2 && <div className="d-skip">… {tail - 2} unchanged lines</div>}
    </pre>
  )
}

/** The first line of a rejection or error, without the hook or tool prefix. */
export function errorLine(step: Step): string {
  return (step.result ?? '')
    .replace(/^PreToolUse:\S+ hook error:\s*/, '')
    .replace(/^Error executing tool \S+:\s*/, '')
    .split('\n')[0]
}

/** A one-line description of a step's input, for list rows. */
export function inputLine(step: Step): string {
  if (isDbtExecute(step.tool)) return formatDbtCommandLine(parseDbtInput(step.input))
  const input = (step.input ?? {}) as Record<string, unknown>
  const text =
    step.sql ??
    input.sql ??
    input.command ??
    input.file_path ??
    input.pattern ??
    input.description ??
    input.query ??
    input.model ??
    input.models ??
    (Object.keys(input).length ? JSON.stringify(input) : '')
  return String(text).replace(/\s+/g, ' ').trim()
}

/** Where in the run a step belongs. */
export const whoseStep = (step: Step) => (step.dispatch === null ? 'main agent' : `verifier #${step.dispatch}`)
