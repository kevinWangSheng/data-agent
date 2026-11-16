// The left pane's outline: phases as group rows, their steps and narration as item rows. Only the
// rows in view are rendered, so a 270-call run expanded in full stays quick.

import { useVirtualizer } from '@tanstack/react-virtual'
import { useEffect, useMemo, useRef } from 'react'

import { fmtDur } from '../format'
import type { RunView } from '../run-view/model'
import { sectionStats, type Item, type Row } from './outline'
import { shortTool } from './tools'
import { Caret, StepChip, StepTag, Tag, VerdictChip, inputLine, phaseLabel } from './ui'

function ItemRow({ item, depth, selected, number, onClick }: { item: Item; depth: number; selected: boolean; number: number | undefined; onClick: () => void }) {
  const inVerifier = item.kind === 'step' && item.step.dispatch !== null
  const cls = 'row' + (selected ? ' sel' : '') + (inVerifier ? ' in-ver' : '') + (item.kind === 'narration' && item.narration.kind === 'thinking' ? ' dim' : '')
  return (
    <div className={cls} style={{ paddingLeft: 6 + depth * 14 }} onClick={onClick} role="option" aria-selected={selected} data-row={item.id}>
      <span className="mono text-[10px] text-ink-3 w-7 text-right shrink-0">{number ?? ''}</span>
      {item.kind === 'step' ? (
        <>
          <StepTag step={item.step} />
          <span className="mono text-[11.5px] text-ink-2 shrink-0 max-w-[118px] truncate">{shortTool(item.step.tool)}</span>
          <span className="lbl">{inputLine(item.step)}</span>
          <StepChip step={item.step} />
          {item.step.durationMs !== null && <span className="mono text-[10.5px] text-ink-3 w-12 text-right shrink-0">{fmtDur(item.step.durationMs)}</span>}
        </>
      ) : (
        <>
          <Tag kind={item.narration.kind} />
          <span className="mono text-[11.5px] text-ink-3 shrink-0">{item.narration.kind === 'thinking' ? 'thinking' : 'text'}</span>
          <span className="lbl">{item.narration.text.replace(/\s+/g, ' ').trim()}</span>
        </>
      )}
    </div>
  )
}

function GroupRow({ row, view, selected, onClick, onToggle }: { row: Extract<Row, { type: 'group' }>; view: RunView; selected: boolean; onClick: () => void; onToggle: () => void }) {
  const { section } = row
  const stats = sectionStats(section)
  const dispatch = section.phase.kind === 'verify' ? view.dispatches.find((d) => d.n === section.phase.dispatch) : undefined
  const failed = dispatch ? [...new Set(dispatch.checks.filter((c) => c.verdict === 'FAIL').map((c) => c.check))] : []
  return (
    <div
      className={'row group' + (dispatch ? ' ver' : '') + (selected ? ' sel' : '')}
      onClick={onClick}
      role="option"
      aria-selected={selected}
      aria-expanded={row.open}
      data-row={row.id}
    >
      <button
        type="button"
        className="w-4 h-4 flex items-center justify-center text-ink-3 shrink-0"
        onClick={(e) => {
          e.stopPropagation()
          onToggle()
        }}
        aria-label={row.open ? 'Collapse' : 'Expand'}
        style={{ transform: row.open ? 'rotate(90deg)' : undefined }}
        tabIndex={-1}
      >
        <Caret />
      </button>
      {dispatch && <Tag kind="agent" />}
      <span className="lbl">
        {phaseLabel(section.phase)}
        {dispatch && (
          <span className="text-ink-3 font-normal">
            {' '}
            · {dispatch.models.length} model{dispatch.models.length === 1 ? '' : 's'}
          </span>
        )}
      </span>
      {row.matches !== null ? (
        <span className="mono text-[10.5px] text-ink-3 shrink-0">{row.matches} shown</span>
      ) : (
        !dispatch && (
          <span className="mono text-[10.5px] text-ink-3 shrink-0">
            {stats.calls}
            {stats.sql ? ` · ${stats.sql} sql` : ''}
            {stats.changes ? ` · ${stats.changes} chg` : ''}
          </span>
        )
      )}
      {failed.length > 0 && <span className="mono text-[10.5px] text-fail shrink-0">chk {failed.join(',')}</span>}
      {dispatch && <VerdictChip verdict={dispatch.overall} />}
      {!dispatch && stats.issues > 0 && <span className="chip chip-deny">{stats.issues} REJ/ERR</span>}
      <span className="mono text-[10.5px] text-ink-3 w-12 text-right shrink-0">{stats.durationMs !== null ? fmtDur(stats.durationMs) : ''}</span>
    </div>
  )
}

export function Outline({
  rows,
  view,
  selected,
  numbers,
  onSelect,
  onToggle,
}: {
  rows: Row[]
  view: RunView
  selected: string | null
  /** Step number (1-based, in `tool_start` order) per step id. */
  numbers: Map<string, number>
  onSelect: (id: string) => void
  onToggle: (id: string) => void
}) {
  const parent = useRef<HTMLDivElement>(null)
  const virtual = useVirtualizer({ count: rows.length, getScrollElement: () => parent.current, estimateSize: () => 26, overscan: 12 })
  const at = useMemo(() => rows.findIndex((r) => r.id === selected), [rows, selected])
  useEffect(() => {
    if (at >= 0) virtual.scrollToIndex(at, { align: 'auto' })
  }, [at, virtual])
  return (
    <div ref={parent} className="scroll flex-1" role="listbox" aria-label="Steps of the run" tabIndex={-1}>
      <div style={{ height: virtual.getTotalSize(), position: 'relative' }}>
        {virtual.getVirtualItems().map((v) => {
          const row = rows[v.index]
          return (
            <div key={row.id} style={{ position: 'absolute', top: 0, left: 0, right: 0, transform: `translateY(${v.start}px)` }}>
              {row.type === 'group' ? (
                <GroupRow row={row} view={view} selected={selected === row.id} onClick={() => onSelect(row.id)} onToggle={() => onToggle(row.id)} />
              ) : (
                <ItemRow item={row.item} depth={row.depth} selected={selected === row.id} number={numbers.get(row.id)} onClick={() => onSelect(row.id)} />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
