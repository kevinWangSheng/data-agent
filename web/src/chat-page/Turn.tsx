// One turn of the chat (spec decision 5, layout A): the user's message, the steps grouped by phase
// as cards (the live phase open, the rest folded), steers where they reached the agent, then the
// question the turn ended with or its answer. Cards reuse the run page's pieces.
//
// Test ids are the browser test's contract (web/e2e/chat.spec.ts): `turn`, `turn-message`,
// `step-card`, `clarification` (its buttons are the options only), `answer`.

import { Check, ChevronDown, ChevronRight, Clock, MessageSquareReply, X } from 'lucide-react'
import { useState } from 'react'

import type { Clarification, Steer, TurnView } from '../chat-view/model'
import { ChatCode } from '../code/chat-code'
import { Md } from '../code/markdown'
import { fmtCost, fmtDur } from '../format'
import type { Narration, RunStatus, RunView, Step, VerifierDispatch } from '../run-view/model'
import { shortTool } from '../run-page/tools'
import { Caret, DataTable, errorLine, inputLine, phaseLabel, Pre, StatusChip, StepChip, StepTag, VerdictChip } from '../run-page/ui'
import { Link } from '../site/Link'
import { steerState, turnLayout, type Group } from './layout'

export type TurnProps = {
  turn: TurnView
  /** Whether its clarification options can be clicked now. */
  answerable: boolean
  onPick: (option: string) => void
  /** The three-pane run page of this turn. */
  runHref: string
  onGo: (href: string) => void
}

export function Turn({ turn, answerable, onPick, runHref, onGo }: TurnProps) {
  const layout = turnLayout(turn)
  const running = turn.status === 'running'
  const databases = new Map(turn.queries.map((q) => [q.step, q.database]))
  return (
    <section data-testid="turn" className="flex flex-col gap-2.5">
      <div className="flex flex-col items-end gap-0.5">
        {turn.answersClarification && <span className="text-[10.5px] text-ink-3">Your choice</span>}
        <UserMessage text={turn.message} />
      </div>
      <TurnMeta turn={turn} runHref={runHref} onGo={onGo} />
      {layout.before.map((n) => (
        <Note key={n.seq} note={n} />
      ))}
      {layout.earlySteers.map((s) => (
        <SteerNote key={s.id} steer={s} status={turn.status} />
      ))}
      {layout.groups.map((g, i) => (
        <GroupBlock key={`${g.phase.kind}-${g.phase.dispatch}-${i}`} group={g} live={running && i === layout.groups.length - 1} databases={databases} status={turn.status} />
      ))}
      {layout.pending.map((s) => (
        <SteerNote key={s.id} steer={s} status={turn.status} />
      ))}
      {running && <Working view={turn.run} />}
      {turn.clarification && <ClarificationCard clarification={turn.clarification} active={answerable} onPick={onPick} />}
      {layout.showAnswer && turn.run.answer && (
        <div data-testid="answer" className="flex flex-col gap-2">
          <Md>{turn.run.answer.text}</Md>
          {turn.run.answer.table && <DataTable table={turn.run.answer.table} max={50} />}
        </div>
      )}
      {turn.status === 'cancelled' && <div className="text-[12.5px] text-ink-3">Cancelled. The next message starts a new turn.</div>}
      {turn.status === 'failure' && (
        <div className="banner banner-fail text-[12px]">
          The run ended with an error.{' '}
          <Link href={runHref} onGo={onGo} className="link">
            See the full run view
          </Link>
        </div>
      )}
    </section>
  )
}

function UserMessage({ text }: { text: string | null }) {
  return (
    <div data-testid="turn-message" className="max-w-[85%] rounded-lg border border-line bg-panel-2 px-3 py-2 text-[13.5px] whitespace-pre-wrap">
      {text ?? ''}
    </div>
  )
}

function TurnMeta({ turn, runHref, onGo }: { turn: TurnView; runHref: string; onGo: (href: string) => void }) {
  const { summary, steps, dispatches } = turn.run
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-3">
      {turn.clarification && turn.status === 'success' ? <span className="chip chip-deny">ASKED YOU</span> : <StatusChip status={turn.status} />}
      <span>
        {steps.length} step{steps.length === 1 ? '' : 's'}
      </span>
      {dispatches.length > 0 && (
        <span>
          {dispatches.length} verification{dispatches.length === 1 ? '' : 's'}
        </span>
      )}
      {summary.durationMs > 0 && <span>{fmtDur(summary.durationMs)}</span>}
      {summary.costUsd !== null && <span className="mono">{fmtCost(summary.costUsd)}</span>}
      <Link href={runHref} onGo={onGo} className="link">
        Full run view →
      </Link>
    </div>
  )
}

/** The agent's current phase and open call while the turn runs. */
function Working({ view }: { view: RunView }) {
  const phase = view.phases.at(-1)
  const open = view.steps.findLast((s) => s.result === null)
  return (
    <div className="flex items-center gap-2 text-[12.5px] text-ink-2" role="status">
      <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-accent" />
      {phase ? phaseLabel(phase) : 'Starting'}
      {open && <span className="mono truncate text-[11.5px] text-ink-3">· {shortTool(open.tool)}</span>}
    </div>
  )
}

function GroupBlock({ group, live, databases, status }: { group: Group; live: boolean; databases: Map<string, string | null>; status: RunStatus }) {
  const [open, setOpen] = useState<boolean | null>(null)
  const shown = open ?? live
  const duration = group.steps.reduce((ms, s) => ms + (s.durationMs ?? 0), 0)
  const issues = group.steps.filter((s) => s.isError || s.rejectedBy !== null).length
  return (
    <div className="flex flex-col gap-1.5">
      {group.dispatch ? (
        <>
          <VerifierCard dispatch={group.dispatch} steps={group.steps} databases={databases} />
          {group.notes.map((n) => (
            <Note key={n.seq} note={n} />
          ))}
        </>
      ) : (
        <>
          <button type="button" onClick={() => setOpen(!shown)} aria-expanded={shown} className="flex items-center gap-1.5 text-left text-[12.5px] text-ink-2 hover:text-ink">
            {shown ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            <span className="font-medium">{phaseLabel(group.phase)}</span>
            <span className="text-ink-3">
              · {group.steps.length} step{group.steps.length === 1 ? '' : 's'}
              {duration > 0 && ` · ${fmtDur(duration)}`}
            </span>
            {issues > 0 && <span className="chip chip-deny">{issues} rejected or failed</span>}
          </button>
          {shown && (
            <div className="ml-1.5 flex flex-col gap-1.5 border-l border-line pl-3">
              {group.notes.map((n) => (
                <Note key={n.seq} note={n} />
              ))}
              {group.steps.map((s) => (
                <StepCard key={s.id} step={s} database={databases.get(s.id)} />
              ))}
            </div>
          )}
        </>
      )}
      {group.steers.map((s) => (
        <SteerNote key={s.id} steer={s} status={status} />
      ))}
    </div>
  )
}

/** Thinking folded by default; the agent's text as it wrote it. */
function Note({ note }: { note: Narration }) {
  if (note.kind === 'text') return <Md className="md-small text-ink-2">{note.text}</Md>
  return (
    <details className="rounded border border-line">
      <summary className="flex items-center gap-2 px-2 py-1 text-[11.5px] text-ink-3">
        <Caret /> Thinking
        <span className="min-w-0 flex-1 truncate italic">{note.text.replace(/\s+/g, ' ').slice(0, 160)}</span>
      </summary>
      <div className="px-2.5 pb-2 text-[12px] whitespace-pre-wrap text-ink-2">{note.text}</div>
    </details>
  )
}

/** One tool call: a line with its kind, input and outcome; opened, its SQL and result. */
function StepCard({ step, database }: { step: Step; database: string | null | undefined }) {
  const [open, setOpen] = useState(false)
  const rejected = step.result !== null && (step.rejectedBy !== null || step.isError)
  return (
    <div data-testid="step-card" className="rounded-md border border-line bg-panel">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-panel-2">
        {open ? <ChevronDown className="h-3 w-3 shrink-0 text-ink-3" /> : <ChevronRight className="h-3 w-3 shrink-0 text-ink-3" />}
        <StepTag step={step} />
        {database !== undefined && (
          <span className="chip chip-na mono" title="The data source this query ran on">
            {database ?? 'database not recorded'}
          </span>
        )}
        <span className="mono min-w-0 flex-1 truncate text-[11.5px] text-ink-2">{inputLine(step) || shortTool(step.tool)}</span>
        <StepChip step={step} />
        {step.durationMs !== null && <span className="mono shrink-0 text-[10.5px] text-ink-3">{fmtDur(step.durationMs)}</span>}
      </button>
      {open && (
        <div className="flex flex-col gap-2 border-t border-line p-2.5">
          <div className="mono text-[11px] text-ink-3">{shortTool(step.tool)}</div>
          {rejected && <div className={`banner ${step.rejectedBy === 'gateway' ? 'banner-block' : step.rejectedBy !== null ? 'banner-deny' : 'banner-fail'} text-[12px] mono`}>{errorLine(step)}</div>}
          {step.sql ? <ChatCode code={step.sql} language="sql" maxHeight={260} /> : <Pre text={JSON.stringify(step.input, null, 2)} maxHeight={200} />}
          {step.result === null ? (
            <div className="text-[12px] text-ink-3">Waiting for the tool to return…</div>
          ) : step.table ? (
            <DataTable table={step.table} max={20} />
          ) : (
            !rejected && <Pre text={step.result.slice(0, 4000)} maxHeight={220} />
          )}
        </div>
      )}
    </div>
  )
}

function VerifierCard({ dispatch, steps, databases }: { dispatch: VerifierDispatch; steps: Step[]; databases: Map<string, string | null> }) {
  const [open, setOpen] = useState(false)
  return (
    <div data-testid="step-card" className="rounded-md border border-line" style={{ background: 'var(--verifier-bg)' }}>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left">
        {open ? <ChevronDown className="h-3 w-3 text-ink-3" /> : <ChevronRight className="h-3 w-3 text-ink-3" />}
        <span className="eyebrow" style={{ color: 'var(--verifier)' }}>
          Verifier #{dispatch.n}
        </span>
        <span className="min-w-0 flex-1 truncate text-[12px] text-ink-2">{dispatch.models.join(', ')}</span>
        <VerdictChip verdict={dispatch.overall} />
        <span className="text-[11px] text-ink-3">{dispatch.checks.length} checks</span>
      </button>
      {open && (
        <div className="flex flex-col gap-1.5 border-t border-line px-2.5 py-2">
          {dispatch.checks.map((c, i) => (
            <div key={i} className="flex items-center gap-2 text-[12px]">
              <VerdictChip verdict={c.verdict} />
              <span className="text-ink-2">{c.name}</span>
              <span className="mono text-[10.5px] text-ink-3">{c.model}</span>
            </div>
          ))}
          {dispatch.report && <Md className="md-small">{dispatch.report}</Md>}
          {steps.map((s) => (
            <StepCard key={s.id} step={s} database={databases.get(s.id)} />
          ))}
        </div>
      )}
    </div>
  )
}

function SteerNote({ steer, status }: { steer: Steer; status: RunStatus }) {
  const state = steerState(steer, status)
  return (
    <div className="flex items-start gap-2 rounded-md border border-dashed border-line-2 bg-panel px-2.5 py-1.5 text-[12.5px]">
      <MessageSquareReply className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
      <div className="min-w-0 flex-1">
        <span className="text-ink-3">You, while it ran: </span>
        {steer.text}
      </div>
      {state === 'delivered' ? (
        <span className="chip chip-ok" title="The message went into the running session; the agent reads it at its next step">
          <Check className="h-3 w-3" /> DELIVERED TO THE AGENT
        </span>
      ) : state === 'sending' ? (
        <span className="chip chip-na">
          <Clock className="h-3 w-3" /> SENDING
        </span>
      ) : (
        <span className="chip chip-deny" title="The turn ended before the agent read this message">
          <X className="h-3 w-3" /> NOT DELIVERED
        </span>
      )}
    </div>
  )
}

/** The question the turn ended with; its buttons are its options and nothing else. */
function ClarificationCard({ clarification, active, onPick }: { clarification: Clarification; active: boolean; onPick: (option: string) => void }) {
  return (
    <div data-testid="clarification" className="rounded-lg border border-line-2 bg-panel p-3" style={{ borderLeft: '3px solid var(--deny)' }}>
      <div className="eyebrow mb-1">The agent asks</div>
      <div className="mb-2 text-[13.5px]">{clarification.question}</div>
      <div className="flex flex-wrap gap-2">
        {clarification.options.map((o) => (
          <button
            key={o}
            type="button"
            disabled={!active}
            aria-pressed={clarification.chosen === o}
            onClick={() => onPick(o)}
            className={`rounded-md border px-2.5 py-1 text-[12.5px] ${
              clarification.chosen === o ? 'border-accent bg-[var(--sel)] text-accent' : 'border-line-2 bg-panel-2 enabled:hover:border-accent'
            } ${!active && clarification.chosen !== o ? 'opacity-50' : ''}`}
          >
            {o}
          </button>
        ))}
      </div>
      {active && <div className="mt-2 text-[11px] text-ink-3">Your choice goes out as your next message.</div>}
    </div>
  )
}
